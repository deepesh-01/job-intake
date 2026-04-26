import { useQuery } from "@tanstack/react-query"
import { Briefcase } from "lucide-react"
import { fetchStats } from "@/lib/api"
import { BotHealthChip } from "./BotHealthChip"

export function Header() {
  const { data } = useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: 60_000,
  })

  return (
    <header className="backdrop-blur-xl bg-background/70 border-b border-border/60 supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto h-14 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="size-8 rounded-lg bg-gradient-to-br from-primary to-primary/40 grid place-items-center shadow-sm shadow-primary/20">
            <Briefcase className="size-4 text-primary-foreground" strokeWidth={2.5} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold tracking-tight leading-tight">Job Intake</div>
            <div className="text-[11px] text-muted-foreground leading-tight tabular-nums">
              {data ? (
                <>
                  {data.total} jobs · {data.by_status["new"] || 0} new · {data.by_status["ready"] || 0} ready · {data.by_status["applied"] || 0} applied
                </>
              ) : (
                "loading…"
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data && data.today_added > 0 && (
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-primary/10 text-primary text-[11px] font-medium">
              <span className="size-1.5 rounded-full bg-primary animate-pulse" />
              {data.today_added} added today
            </div>
          )}
          <BotHealthChip />
        </div>
      </div>
    </header>
  )
}
