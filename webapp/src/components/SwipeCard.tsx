import {
  motion,
  type PanInfo,
  useMotionValue,
  useTransform,
} from "framer-motion"
import {
  Building2,
  Calendar,
  ExternalLink,
  MapPin,
  Sparkles,
} from "lucide-react"
import { useState } from "react"
import { type JobSummary } from "@/lib/api"
import { sourceFromId } from "@/lib/sources"
import { cn, formatComp, formatDateTime, formatDateTimeFull } from "@/lib/utils"

const SWIPE_DIST_THRESHOLD = 110
const SWIPE_VEL_THRESHOLD = 600
const SWIPE_UP_DIST_THRESHOLD = 90  // upward needs less distance — natural feel

const TAG_DISPLAY: Record<string, { label: string; tone: "good" | "neutral" | "warn" | "bad" }> = {
  resume_strong:    { label: "Resume strong",  tone: "good" },
  target_city:      { label: "Bangalore/HYD",  tone: "good" },
  comp_ok:          { label: "Comp ✓",         tone: "good" },
  ai_native:        { label: "AI-native",      tone: "good" },
  enjoy_eligible:   { label: "Chill+AI",       tone: "good" },
  early_stage:      { label: "Early",          tone: "neutral" },
  growth_stage:     { label: "Growth",         tone: "neutral" },
  late_stage:       { label: "Late",           tone: "neutral" },
  remote_ok:        { label: "Remote",         tone: "neutral" },
  stack_typescript: { label: "TS",             tone: "neutral" },
  stack_python:     { label: "Python",         tone: "neutral" },
  seniority_match:  { label: "Senior",         tone: "neutral" },
  application_eng:  { label: "App Eng",        tone: "neutral" },
  wrong_discipline: { label: "Off-discipline", tone: "warn" },
  grind_signal:     { label: "Grindy",         tone: "warn" },
  non_us_only:      { label: "US-only",        tone: "bad" },
  comp_below:       { label: "Below floor",    tone: "warn" },
  comp_unknown:     { label: "No comp",        tone: "neutral" },
}
const TONE_CLASS: Record<string, string> = {
  good:    "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
  neutral: "bg-muted/60 text-muted-foreground border-transparent",
  warn:    "bg-amber-500/10 text-amber-500 border-amber-500/20",
  bad:     "bg-rose-500/10 text-rose-500 border-rose-500/20",
}

export interface SwipeCardProps {
  job: JobSummary
  isTop: boolean
  stackIndex: number   // 0 for top, 1 for behind, 2 for further behind
  onSwipeRight: () => void
  onSwipeLeft: () => void
  onSwipeUp: () => void
  onTap: () => void
}

export function SwipeCard({
  job,
  isTop,
  stackIndex,
  onSwipeRight,
  onSwipeLeft,
  onSwipeUp,
  onTap,
}: SwipeCardProps) {
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const rotate = useTransform(x, [-300, 0, 300], [-15, 0, 15])
  const likeOpacity = useTransform(x, [40, 140], [0, 1])
  const nopeOpacity = useTransform(x, [-140, -40], [1, 0])
  const passOpacity = useTransform(y, [-140, -40], [1, 0])
  const [exiting, setExiting] = useState<"left" | "right" | "up" | null>(null)

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    if (!isTop || exiting) return
    const dx = info.offset.x
    const dy = info.offset.y
    const vx = info.velocity.x
    const vy = info.velocity.y

    // Vertical-up takes priority IF user moved more vertically than horizontally
    if ((dy < -SWIPE_UP_DIST_THRESHOLD || vy < -SWIPE_VEL_THRESHOLD) && Math.abs(dy) > Math.abs(dx)) {
      setExiting("up")
      setTimeout(onSwipeUp, 220)
      return
    }
    if (dx > SWIPE_DIST_THRESHOLD || vx > SWIPE_VEL_THRESHOLD) {
      setExiting("right")
      setTimeout(onSwipeRight, 220)
    } else if (dx < -SWIPE_DIST_THRESHOLD || vx < -SWIPE_VEL_THRESHOLD) {
      setExiting("left")
      setTimeout(onSwipeLeft, 220)
    }
    // Else: dragSnapToOrigin handles bounce-back
  }

  const compStr = job.comp_string || formatComp(job.comp_currency, job.comp_high, job.comp_high_usd)

  const visibleTags = [...job.tags]
    .filter((t) => t in TAG_DISPLAY)
    .sort((a, b) => {
      const order = ["good", "neutral", "warn", "bad"]
      return order.indexOf(TAG_DISPLAY[a].tone) - order.indexOf(TAG_DISPLAY[b].tone)
    })
    .slice(0, 6)

  const matchPct = Math.round(job.resume_match * 100)

  const stackOffset = stackIndex * 6
  const stackScale = 1 - stackIndex * 0.035

  return (
    <motion.div
      drag={isTop ? true : false}
      dragSnapToOrigin
      dragElastic={0.4}
      onDragEnd={handleDragEnd}
      onClick={(e) => {
        if (Math.abs(x.get()) < 5 && Math.abs(y.get()) < 5 && isTop) {
          e.preventDefault()
          onTap()
        }
      }}
      animate={
        exiting === "right"
          ? { x: 600, rotate: 25, opacity: 0, transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] } }
          : exiting === "left"
          ? { x: -600, rotate: -25, opacity: 0, transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] } }
          : exiting === "up"
          ? { y: -800, opacity: 0, scale: 0.85, transition: { duration: 0.25, ease: [0.16, 1, 0.3, 1] } }
          : { y: stackOffset, scale: stackScale, transition: { type: "spring", stiffness: 300, damping: 30 } }
      }
      style={{
        x: isTop ? x : 0,
        y: isTop ? y : 0,
        rotate: isTop ? rotate : 0,
        // Stacked within the parent's `isolation: isolate` boundary —
        // these only affect ordering inside the card stack, not the rest
        // of the page (so the drawer's z-50 still wins when it opens).
        zIndex: 3 - stackIndex,
      }}
      className={cn(
        "absolute inset-0 select-none",
        "rounded-[28px] bg-card border border-border/60",
        "shadow-2xl shadow-black/40",
        "p-6 flex flex-col",
        isTop ? "cursor-grab active:cursor-grabbing" : "pointer-events-none",
        "overflow-hidden",
      )}
    >
      {/* Drag-direction overlays */}
      {isTop && (
        <>
          <motion.div
            style={{ opacity: likeOpacity }}
            className="absolute top-6 right-6 z-30 px-3 py-1.5 rounded-xl border-2 border-emerald-500 text-emerald-500 text-base font-extrabold uppercase tracking-wider rotate-[12deg] pointer-events-none bg-background/30 backdrop-blur"
          >
            ✓ Tailor
          </motion.div>
          <motion.div
            style={{ opacity: nopeOpacity }}
            className="absolute top-6 left-6 z-30 px-3 py-1.5 rounded-xl border-2 border-rose-500 text-rose-500 text-base font-extrabold uppercase tracking-wider -rotate-[12deg] pointer-events-none bg-background/30 backdrop-blur"
          >
            ✗ Reject
          </motion.div>
          <motion.div
            style={{ opacity: passOpacity }}
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-30 px-4 py-2 rounded-xl border-2 border-amber-500 text-amber-500 text-base font-extrabold uppercase tracking-wider pointer-events-none bg-background/40 backdrop-blur"
          >
            ↑ Skip
          </motion.div>
        </>
      )}

      {/* Header: company + match ring */}
      <div className="flex items-start gap-3 mb-3">
        <MatchRing pct={matchPct} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Building2 className="size-3" />
            <span className="font-medium text-foreground/90 truncate">{job.company}</span>
          </div>
          <h2 className="text-lg sm:text-xl font-semibold leading-snug mt-0.5 line-clamp-3">
            {job.role}
          </h2>
        </div>
      </div>

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] mb-3">
        <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-muted/40 text-muted-foreground uppercase tracking-wide text-[10px] font-medium">
          {sourceFromId(job.id)}
        </span>
        {job.location && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-muted/40">
            <MapPin className="size-3" />
            <span className="truncate max-w-[180px]">{job.location}</span>
          </span>
        )}
        {compStr && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-foreground/5 font-medium tabular-nums">
            {compStr}
          </span>
        )}
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-muted/40 text-muted-foreground tabular-nums"
          title={`Added ${formatDateTimeFull(job.discovered_at)}`}
        >
          <Calendar className="size-3" />
          {formatDateTime(job.discovered_at)}
        </span>
      </div>

      {/* Tag chips */}
      {visibleTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
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
        </div>
      )}

      {/* JD teaser */}
      <div className="flex-1 overflow-hidden text-[13px] leading-relaxed text-muted-foreground line-clamp-[10] sm:line-clamp-[12]">
        Tap card to open the full job description.
      </div>

      {/* Footer: link out */}
      <a
        href={job.link}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="mt-3 inline-flex items-center gap-1 text-[11px] text-primary hover:underline self-start"
      >
        <ExternalLink className="size-3" />
        View original posting
      </a>
    </motion.div>
  )
}

function MatchRing({ pct }: { pct: number }) {
  const size = 48
  const stroke = 4
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
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" className="stroke-muted" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none"
          className={cn(tone, "transition-all duration-500")}
          strokeWidth={stroke}
          strokeDasharray={`${dash} ${c}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-xs font-bold tabular-nums">
        {pct}
      </div>
    </div>
  )
}
