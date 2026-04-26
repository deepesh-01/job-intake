import { type JobSummary } from "@/lib/api"
import { cn, formatComp, formatRelativeDate } from "@/lib/utils"
import { CheckCircle2, MapPin, Sparkles, Wand2 } from "lucide-react"

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string; icon?: any }> = {
  new: { bg: "bg-muted", text: "text-muted-foreground", label: "New" },
  tailor: { bg: "bg-amber-500/15", text: "text-amber-500", label: "Queued", icon: Wand2 },
  ready: { bg: "bg-emerald-500/15", text: "text-emerald-500", label: "Ready", icon: CheckCircle2 },
  applied: { bg: "bg-blue-500/15", text: "text-blue-500", label: "Applied" },
  rejected: { bg: "bg-rose-500/10", text: "text-rose-500", label: "Rejected" },
  skip: { bg: "bg-muted", text: "text-muted-foreground/60", label: "Skipped" },
  error: { bg: "bg-destructive/15", text: "text-destructive", label: "Error" },
}

// Tag display priority — surface the most actionable on the card.
const TAG_PRIORITY = [
  "resume_strong",
  "target_city",
  "comp_ok",
  "ai_native",
  "enjoy_eligible",
  "early_stage",
  "growth_stage",
  "remote_ok",
  "stack_typescript",
  "stack_python",
  "seniority_match",
  "application_eng",
  "wrong_discipline",
  "grind_signal",
  "non_us_only",
]

const TAG_DISPLAY: Record<string, { label: string; tone: "good" | "neutral" | "warn" | "bad" }> = {
  resume_strong:        { label: "Resume strong",  tone: "good" },
  target_city:          { label: "Bangalore/HYD",  tone: "good" },
  comp_ok:              { label: "Comp ✓",          tone: "good" },
  ai_native:            { label: "AI-native",       tone: "good" },
  enjoy_eligible:       { label: "Chill+AI",        tone: "good" },
  early_stage:          { label: "Early",           tone: "neutral" },
  growth_stage:         { label: "Growth",          tone: "neutral" },
  late_stage:           { label: "Late",            tone: "neutral" },
  remote_ok:            { label: "Remote",          tone: "neutral" },
  stack_typescript:     { label: "TS",              tone: "neutral" },
  stack_python:         { label: "Python",          tone: "neutral" },
  seniority_match:      { label: "Senior",          tone: "neutral" },
  application_eng:      { label: "App Eng",         tone: "neutral" },
  wrong_discipline:     { label: "Off-discipline",  tone: "warn" },
  grind_signal:         { label: "Grindy",          tone: "warn" },
  non_us_only:          { label: "US-only",         tone: "bad" },
  comp_below:           { label: "Below floor",     tone: "warn" },
  comp_unknown:         { label: "No comp",         tone: "neutral" },
}

const TONE_CLASS: Record<string, string> = {
  good:    "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
  neutral: "bg-muted/60 text-muted-foreground border-transparent",
  warn:    "bg-amber-500/10 text-amber-500 border-amber-500/20",
  bad:     "bg-rose-500/10 text-rose-500 border-rose-500/20",
}

export function JobCard({ job, onClick }: { job: JobSummary; onClick: () => void }) {
  const status = STATUS_STYLES[job.status] || STATUS_STYLES.new
  const StatusIcon = status.icon
  const compStr = job.comp_string || formatComp(job.comp_currency, job.comp_high, job.comp_high_usd)

  const visibleTags = [...job.tags]
    .sort(
      (a, b) =>
        (TAG_PRIORITY.indexOf(a) === -1 ? 999 : TAG_PRIORITY.indexOf(a)) -
        (TAG_PRIORITY.indexOf(b) === -1 ? 999 : TAG_PRIORITY.indexOf(b)),
    )
    .filter((t) => t in TAG_DISPLAY)
    .slice(0, 4)

  const matchPct = Math.round(job.resume_match * 100)

  return (
    <button
      onClick={onClick}
      className={cn(
        "group relative w-full text-left",
        "rounded-2xl border border-border/60 bg-card",
        "px-4 py-3.5 transition-all",
        "hover:border-border hover:shadow-lg hover:shadow-black/5 hover:-translate-y-px",
        "active:scale-[0.99]",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <div className="flex items-start gap-3">
        {/* Match score arc */}
        <MatchRing pct={matchPct} />

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-3">
            <div className="text-[15px] font-semibold leading-tight truncate">
              {job.role}
            </div>
            <div
              className={cn(
                "shrink-0 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium",
                status.bg,
                status.text,
              )}
            >
              {StatusIcon && <StatusIcon className="size-3" strokeWidth={2.5} />}
              {status.label}
            </div>
          </div>

          <div className="mt-0.5 flex items-center gap-2 text-[13px] text-muted-foreground min-w-0">
            <span className="font-medium text-foreground/80 truncate">{job.company}</span>
            {job.location && (
              <>
                <span className="text-border">·</span>
                <span className="flex items-center gap-1 truncate">
                  <MapPin className="size-3 shrink-0" />
                  <span className="truncate">{job.location}</span>
                </span>
              </>
            )}
          </div>

          {(visibleTags.length > 0 || compStr) && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              {compStr && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-foreground/5 text-foreground/90 tabular-nums border border-transparent">
                  {compStr}
                </span>
              )}
              {visibleTags.map((tag) => {
                const meta = TAG_DISPLAY[tag]
                return (
                  <span
                    key={tag}
                    className={cn(
                      "inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md text-[11px] font-medium border",
                      TONE_CLASS[meta.tone],
                    )}
                  >
                    {tag === "resume_strong" && <Sparkles className="size-2.5" />}
                    {meta.label}
                  </span>
                )
              })}
              <div className="grow" />
              <span className="text-[10px] text-muted-foreground tabular-nums">
                {formatRelativeDate(job.discovered_at)}
              </span>
            </div>
          )}
        </div>
      </div>
    </button>
  )
}

function MatchRing({ pct }: { pct: number }) {
  // small SVG arc, no library; matches Apple Health-style ring vibe
  const size = 36
  const stroke = 3
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const dash = (Math.max(0, Math.min(100, pct)) / 100) * c
  const tone =
    pct >= 70 ? "stroke-emerald-500" :
    pct >= 40 ? "stroke-primary" :
    "stroke-muted-foreground/50"

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          className="stroke-muted"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          className={cn(tone, "transition-all duration-500")}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${c}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-[10px] font-semibold tabular-nums">
        {pct}
      </div>
    </div>
  )
}
