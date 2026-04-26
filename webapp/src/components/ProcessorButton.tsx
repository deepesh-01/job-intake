import { useEffect } from "react"
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { Wand2, Loader2 } from "lucide-react"
import { runProcessor, fetchProcessStatus } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

export function ProcessorButton() {
  const qc = useQueryClient()
  const { canMutate } = useAuth()

  // Single source of truth — server-side detection via lock files.
  // Polled every 5s while the processor is active, every 30s otherwise.
  // Survives page reloads, cross-device, multiple tabs — anyone watching
  // sees the same running state.
  const { data: status } = useQuery({
    queryKey: ["process_status"],
    queryFn: fetchProcessStatus,
    refetchInterval: (q) => (q.state.data?.is_running ? 5_000 : 30_000),
    refetchIntervalInBackground: true,
  })

  const isRunning = status?.is_running ?? false
  const queued = status?.queue_remaining ?? 0
  const ready = status?.ready_total ?? 0
  const currentLabel =
    status?.current_company && status?.current_role
      ? `${status.current_company} · ${status.current_role}`
      : status?.current_company || "current job"

  // Whenever a run finishes, refresh the jobs list + stats so the UI
  // shows the new ready rows immediately.
  useEffect(() => {
    if (isRunning) return
    qc.invalidateQueries({ queryKey: ["jobs"] })
    qc.invalidateQueries({ queryKey: ["stats"] })
  }, [isRunning, qc])

  const mut = useMutation({
    mutationFn: runProcessor,
    onMutate: () => {
      toast.loading(
        queued > 0 ? `Tailoring ${queued} ${queued === 1 ? "job" : "jobs"}…` : "Processor running…",
        {
          id: "proc",
          description: "~5 min per row — Claude tailor + critic + render",
        },
      )
      // Bump the status poll immediately so the button flips to "Processing"
      // without waiting for the next 30s tick.
      qc.invalidateQueries({ queryKey: ["process_status"] })
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      qc.invalidateQueries({ queryKey: ["stats"] })
      qc.invalidateQueries({ queryKey: ["process_status"] })
      if (r.exit_code === 0) {
        const summary = r.stdout_tail
          .split("\n")
          .reverse()
          .find((l) => l.includes("processor_finished")) || "Done"
        toast.success("Processor finished", {
          id: "proc",
          description: summary.replace(/^.*processor_finished\s+/, "").slice(0, 200),
        })
      } else {
        toast.error("Processor errored", {
          id: "proc",
          description: r.stderr_tail.slice(-200) || `exit=${r.exit_code}`,
        })
      }
    },
    onError: (err: Error) => {
      qc.invalidateQueries({ queryKey: ["process_status"] })
      toast.error("Couldn't run processor", { id: "proc", description: err.message })
    },
  })

  // Disable when:
  //  - viewer (no token)
  //  - processor is already running (server-side detected)
  //  - the local mutation is in flight
  //  - queue is empty
  const disabled = !canMutate || isRunning || mut.isPending || queued === 0

  const label = !canMutate
    ? "Process queue"
    : isRunning
      ? `Processing ${queued} ${queued === 1 ? "job" : "jobs"}`
      : queued === 0
        ? "Queue empty"
        : "Process queue"

  const subLabel = isRunning
    ? `Now: ${currentLabel}`
    : ready > 0 && !isRunning
      ? `${ready} ready · ${queued} queued`
      : null

  return (
    <button
      onClick={() => !disabled && mut.mutate()}
      disabled={disabled}
      title={
        !canMutate
          ? "Preview mode — only the owner can run the processor"
          : isRunning
            ? `${queued} ${queued === 1 ? "job" : "jobs"} still in queue. Now tailoring: ${currentLabel}.`
            : queued === 0
              ? `No jobs marked status=tailor. ${ready} ready, waiting on you.`
              : `Tailor ${queued} queued ${queued === 1 ? "job" : "jobs"} → status will move to "ready" with a tailored PDF.`
      }
      className={cn(
        "fixed bottom-5 right-5 z-30",
        "max-w-[calc(100vw-2.5rem)]",
        "h-14 px-5 inline-flex items-center gap-3 rounded-2xl",
        "font-semibold text-sm",
        "transition-all",
        disabled
          ? cn(
              "bg-muted/80 text-muted-foreground cursor-not-allowed",
              "border border-border/60 backdrop-blur-md",
              isRunning && "cursor-wait opacity-95 border-primary/30 bg-primary/10 text-primary",
            )
          : cn(
              "bg-primary text-primary-foreground",
              "shadow-xl shadow-primary/30",
              "hover:shadow-2xl hover:shadow-primary/40 hover:-translate-y-0.5",
              "active:scale-[0.97]",
            ),
      )}
      aria-label={label}
    >
      {isRunning ? (
        <Loader2 className="size-4 animate-spin shrink-0" />
      ) : (
        <Wand2 className="size-4 shrink-0" strokeWidth={2.5} />
      )}
      <div className="flex flex-col items-start min-w-0 leading-tight">
        <span className="truncate">{label}</span>
        {subLabel && (
          <span className="text-[10px] font-normal opacity-80 truncate max-w-[60vw] sm:max-w-[260px]">
            {subLabel}
          </span>
        )}
      </div>

      {queued > 0 && !isRunning && canMutate && (
        <span
          className={cn(
            "ml-0.5 min-w-[22px] h-[22px] px-1.5 rounded-full",
            "inline-flex items-center justify-center text-xs font-bold tabular-nums shrink-0",
            "bg-primary-foreground text-primary",
            "border border-primary-foreground/20",
          )}
        >
          {queued}
        </span>
      )}
    </button>
  )
}
