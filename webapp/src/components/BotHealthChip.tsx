import { useState, useEffect } from "react"
import * as Popover from "@radix-ui/react-popover"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { AlertTriangle, Bot, Loader2, Power, RefreshCw } from "lucide-react"
import { fetchBotHealth, restartBot } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"

const UI_POLL_INTERVAL_MS = 30_000

export function BotHealthChip() {
  const qc = useQueryClient()
  const { canMutate } = useAuth()
  const [open, setOpen] = useState(false)

  const { data: health, dataUpdatedAt } = useQuery({
    queryKey: ["bot_health"],
    queryFn: fetchBotHealth,
    refetchInterval: UI_POLL_INTERVAL_MS,
    refetchIntervalInBackground: true,
  })

  // Tick once a second so the "Xs ago" label stays live (without re-fetching).
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!open) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [open])
  const sinceLastPoll = dataUpdatedAt ? Math.max(0, Math.round((now - dataUpdatedAt) / 1000)) : null

  const mut = useMutation({
    mutationFn: restartBot,
    onMutate: () =>
      toast.loading("Restarting resume bot…", { id: "bot_restart", description: "Killing old process + npm start" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["bot_health"] })
      toast.success("Bot restarted", {
        id: "bot_restart",
        description: r.new_pids.length
          ? `New PID${r.new_pids.length > 1 ? "s" : ""}: ${r.new_pids.join(", ")}`
          : "Spawned — verifying…",
      })
      setOpen(false)
    },
    onError: (err: Error) =>
      toast.error("Restart failed", { id: "bot_restart", description: err.message }),
  })

  const state = health?.state ?? "ok"
  const ageMin = health?.heartbeat_age_seconds != null
    ? (health.heartbeat_age_seconds / 60).toFixed(1)
    : null

  const tone = {
    ok:   { bg: "bg-emerald-500/10", text: "text-emerald-500", dot: "bg-emerald-500", label: "Bot OK" },
    hung: { bg: "bg-amber-500/15",  text: "text-amber-500",  dot: "bg-amber-500 animate-pulse", label: "Bot hung" },
    down: { bg: "bg-rose-500/15",   text: "text-rose-500",   dot: "bg-rose-500", label: "Bot down" },
  }[state]

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          className={cn(
            "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium",
            "transition-all hover:scale-[1.02] active:scale-95",
            tone.bg, tone.text,
          )}
          aria-label={tone.label}
        >
          <span className={cn("size-1.5 rounded-full", tone.dot)} />
          <span className="hidden sm:inline">{tone.label}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          className="z-50 w-72 rounded-2xl border border-border/60 bg-popover/95 backdrop-blur-xl shadow-2xl shadow-black/40 p-4 text-sm animate-in"
        >
          <div className="flex items-center gap-2 mb-3">
            <Bot className="size-4 text-muted-foreground" />
            <div className="font-semibold">Resume tailoring bot</div>
            <div className="grow" />
            <span className={cn("size-2 rounded-full", tone.dot)} />
          </div>

          <dl className="text-xs text-muted-foreground space-y-1.5">
            <Row k="State" v={<span className={cn("font-medium uppercase tracking-wide", tone.text)}>{state}</span>} />
            <Row k="Alive" v={health?.is_alive ? `yes (${health.pids.join(", ") || "—"})` : "no"} />
            <Row k="Heartbeat" v={ageMin != null ? `${ageMin} min ago` : "—"} />
            <Row k="Stale at" v={`${health?.stale_threshold_seconds ?? 180}s`} />
          </dl>

          {/* Polling intervals — both UI refresh and server-side watchdog */}
          <div className="mt-3 pt-3 border-t border-border/60">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">Polling</div>
            <dl className="text-xs text-muted-foreground space-y-1.5">
              <Row
                k="UI refresh"
                v={
                  <span>
                    every {Math.round(UI_POLL_INTERVAL_MS / 1000)}s
                    {sinceLastPoll != null && (
                      <span className="text-muted-foreground/60"> · {sinceLastPoll}s ago</span>
                    )}
                  </span>
                }
              />
              <Row
                k="Auto-restart"
                v={`every ${health?.auto_watchdog_interval_seconds ?? 60}s`}
              />
              <Row
                k="Cooldown"
                v={`${Math.round((health?.restart_cooldown_seconds ?? 300) / 60)} min`}
              />
            </dl>
          </div>

          {state !== "ok" && (
            <div className="mt-3 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-200/95 flex items-start gap-2">
              <AlertTriangle className="size-3.5 shrink-0 mt-0.5" />
              <div>
                {state === "hung"
                  ? "Process is alive but heartbeat is stale. Auto-restart will fire within 60s; you can force it now."
                  : "No bot process found. Auto-restart will spawn one within 60s; you can do it now."}
              </div>
            </div>
          )}

          <button
            onClick={() => canMutate && mut.mutate()}
            disabled={!canMutate || mut.isPending}
            title={!canMutate ? "Owner only" : "Kill any existing bot + npm start"}
            className={cn(
              "mt-3 w-full h-9 inline-flex items-center justify-center gap-2 rounded-xl text-xs font-medium",
              "transition-all active:scale-[0.98]",
              "disabled:opacity-40 disabled:cursor-not-allowed",
              state === "ok"
                ? "bg-muted text-foreground hover:bg-muted/80"
                : "bg-primary text-primary-foreground hover:bg-primary/90",
            )}
          >
            {mut.isPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : state === "ok" ? (
              <RefreshCw className="size-3.5" />
            ) : (
              <Power className="size-3.5" />
            )}
            {mut.isPending ? "Restarting…" : state === "ok" ? "Restart anyway" : "Restart now"}
          </button>

          {!canMutate && (
            <div className="mt-2 text-[10px] text-muted-foreground text-center">
              View-only — only the owner can restart
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="shrink-0">{k}</dt>
      <dd className="text-foreground/90 truncate text-right tabular-nums">{v}</dd>
    </div>
  )
}
