import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { fetchStats, retryErrored } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

/** Shows when the active status filter includes "error" and there ARE errored
 *  rows. One-tap bulk-reset all errored → tailor (so the next processor
 *  sweep picks them up). Owner-only; viewer sees the count but no button. */
export function RetryErroredBanner({ statusFilter }: { statusFilter: string[] }) {
  const qc = useQueryClient()
  const { canMutate } = useAuth()
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
  })
  const erroredCount = stats?.by_status["error"] || 0

  const mut = useMutation({
    mutationFn: retryErrored,
    onMutate: () =>
      toast.loading(`Re-queuing ${erroredCount} errored ${erroredCount === 1 ? "row" : "rows"}…`, {
        id: "retry-errored",
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      qc.invalidateQueries({ queryKey: ["stats"] })
      toast.success(`Re-queued ${r.reset} ${r.reset === 1 ? "row" : "rows"}`, {
        id: "retry-errored",
        description: 'Click "Process queue" when ready to tailor.',
      })
    },
    onError: (err: Error) =>
      toast.error("Couldn't retry", { id: "retry-errored", description: err.message }),
  })

  // Only render when:
  //   1. The user has filtered TO error status (so they're already looking at it)
  //   2. There ARE errored rows to retry
  const isViewingErrors = statusFilter.length === 1 && statusFilter[0] === "error"
  if (!isViewingErrors || erroredCount === 0) return null

  return (
    <div className="mb-3 px-3 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center gap-3">
      <AlertTriangle className="size-4 text-amber-500 shrink-0" />
      <div className="text-xs text-amber-200/95 flex-1 min-w-0">
        <span className="font-medium">{erroredCount} errored</span>
        <span className="text-amber-200/70 ml-1">
          — last_change cell on each row shows the reason. One-tap retry resets all to queued.
        </span>
      </div>
      <button
        onClick={() => canMutate && !mut.isPending && mut.mutate()}
        disabled={!canMutate || mut.isPending}
        title={!canMutate ? "Owner only" : `Reset all ${erroredCount} to status=tailor`}
        className={cn(
          "shrink-0 inline-flex items-center gap-1.5 px-3 h-8 rounded-lg text-xs font-medium",
          "transition-all active:scale-95",
          "disabled:opacity-40 disabled:cursor-not-allowed",
          "bg-amber-500 text-amber-950 hover:bg-amber-400",
        )}
      >
        <RefreshCw className={cn("size-3.5", mut.isPending && "animate-spin")} />
        Retry all
      </button>
    </div>
  )
}
