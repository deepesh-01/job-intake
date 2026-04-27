import { useEffect, useMemo, useState } from "react"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { CheckCircle2, ChevronUp, Sparkles, X } from "lucide-react"
import { fetchJobs, patchJob, type JobSummary } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { type Filters } from "./FilterBar"
import { SwipeCard } from "./SwipeCard"

export function CardStack({
  filters,
  onOpen,
}: {
  filters: Filters
  onOpen: (id: string) => void
}) {
  const qc = useQueryClient()
  const { canMutate } = useAuth()

  // Card view is intentionally LIMITED to status=new — the swipe actions
  // (tailor / reject) are first-time triage decisions; for already-tailored
  // or already-applied rows the list view + detail drawer is the right
  // surface. The user's status pill selection is ignored in this view.
  const params = useMemo(() => {
    const tagsAll: string[] = []
    if (filters.resumeStrong) tagsAll.push("resume_strong")
    if (filters.targetCity) tagsAll.push("target_city")
    return {
      status: ["new"],
      tags_all: tagsAll.length ? tagsAll : undefined,
      tags_none: filters.excludeNonUsOnly ? ["non_us_only"] : undefined,
      q: filters.q || undefined,
      discovered_within: filters.discoveredWithin || undefined,
      sort: filters.sort,
      limit: 200,
    }
  }, [filters])

  const { data, isLoading } = useQuery({
    queryKey: ["jobs", params],
    queryFn: () => fetchJobs(params),
    placeholderData: keepPreviousData,
  })

  // Local stack — we pop from this on swipe so the next card slides up
  // before the query refetches. Reset whenever the underlying data changes.
  const [removedIds, setRemovedIds] = useState<Set<string>>(new Set())
  const stack = useMemo(
    () => (data?.rows ?? []).filter((j) => !removedIds.has(j.id)),
    [data?.rows, removedIds],
  )

  // When filters change → fresh data → reset the removed set so we don't
  // accidentally hide rows from a different filter.
  useEffect(() => { setRemovedIds(new Set()) }, [filters])

  const mut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      patchJob(id, status === "applied" ? { status, applied_at: new Date().toISOString().slice(0, 10) } : { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      qc.invalidateQueries({ queryKey: ["stats"] })
    },
    onError: (err: Error, vars) => {
      toast.error(`Couldn't update: ${err.message}`)
      // Roll back the local hide so the card reappears
      setRemovedIds((prev) => {
        const next = new Set(prev)
        next.delete(vars.id)
        return next
      })
    },
  })

  const act = (job: JobSummary, action: "tailor" | "rejected") => {
    // Always pop the card locally so visitors get the full swipe experience.
    // The Sheet only mutates when the owner has the token — viewers see
    // identical animation but no server-side change. Their swiped cards
    // come back on a hard refresh.
    setRemovedIds((prev) => new Set(prev).add(job.id))

    if (!canMutate) {
      toast(
        action === "tailor" ? "Would queue for tailoring" : "Would mark rejected",
        {
          description: "Preview mode — only the owner can change job status.",
          icon: action === "tailor" ? "✓" : "✗",
          duration: 1500,
        },
      )
      return
    }
    toast.success(action === "tailor" ? "Queued for tailoring" : "Marked rejected", {
      duration: 1800,
    })
    mut.mutate({ id: job.id, status: action })
  }

  const skip = (job: JobSummary) => {
    // Pass — no DB change, just hide locally so the next card slides up.
    // Comes back on next refetch since the row is still status=new.
    setRemovedIds((prev) => new Set(prev).add(job.id))
    toast("Skipped — back to triage tomorrow", {
      duration: 1500,
      icon: "⏭",
    })
  }

  const top = stack[0]

  if (isLoading && !data) {
    return <StackSkeleton />
  }

  if (!top) {
    return <EmptyStack hadRows={(data?.rows.length ?? 0) > 0} />
  }

  // Render the top 3 cards stacked. Top one is interactive, others are decorative.
  const visible = stack.slice(0, 3)

  return (
    <div className="flex flex-col items-center gap-5 pt-4">
      <div className="text-xs text-muted-foreground tabular-nums">
        {stack.length} new to triage
      </div>

      {/*
        Card stack — fixed-aspect, relative-positioned.
        `isolate` creates a new stacking context so the inner cards'
        z-index values don't leak out and overlap the detail drawer
        (which renders at z-50 at the document root).
      */}
      <div className="relative isolate w-full max-w-md aspect-[3/4] sm:aspect-[4/5]">
        {visible
          .slice()
          .reverse() // back-to-front: render bottom card first so top is on top in DOM
          .map((job, i, arr) => {
            const stackIndex = arr.length - 1 - i // 0 = top, 1 = mid, 2 = back
            const isTop = stackIndex === 0
            return (
              <SwipeCard
                key={job.id}
                job={job}
                isTop={isTop}
                stackIndex={stackIndex}
                onSwipeRight={() => act(job, "tailor")}
                onSwipeLeft={() => act(job, "rejected")}
                onSwipeUp={() => skip(job)}
                onTap={() => onOpen(job.id)}
              />
            )
          })}
      </div>

      {/* Action buttons — fallback for desktop, accessibility, decision-paralysis */}
      <div className="flex items-center gap-3">
        <ActionButton
          kind="reject"
          onClick={() => top && act(top, "rejected")}
          disabled={!top}
        />
        <button
          onClick={() => top && skip(top)}
          disabled={!top}
          title="Skip — no action, back to triage on next refresh"
          className={cn(
            "size-12 grid place-items-center rounded-full transition-all active:scale-90",
            "bg-amber-500/10 text-amber-500 hover:bg-amber-500/20",
            "border-2 border-amber-500/30",
            "disabled:opacity-30 disabled:cursor-not-allowed",
          )}
          aria-label="Skip"
        >
          <ChevronUp className="size-5" strokeWidth={2.5} />
        </button>
        <button
          onClick={() => top && onOpen(top.id)}
          disabled={!top}
          className={cn(
            "h-12 px-5 rounded-full text-xs font-medium",
            "bg-muted text-foreground hover:bg-muted/80 transition-all active:scale-95",
            "border border-border/60",
            "disabled:opacity-30",
          )}
        >
          Open
        </button>
        <ActionButton
          kind="tailor"
          onClick={() => top && act(top, "tailor")}
          disabled={!top}
        />
      </div>

      <div className="text-[11px] text-muted-foreground text-center mt-1 hidden sm:block">
        ← reject · ↑ skip · open · tailor →
      </div>
    </div>
  )
}

function ActionButton({
  kind,
  onClick,
  disabled,
}: {
  kind: "reject" | "tailor"
  onClick: () => void
  disabled?: boolean
}) {
  const Icon = kind === "tailor" ? Sparkles : X
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "size-14 grid place-items-center rounded-full transition-all",
        "active:scale-90",
        "disabled:opacity-30 disabled:cursor-not-allowed",
        kind === "tailor"
          ? "bg-emerald-500/15 text-emerald-500 hover:bg-emerald-500/25 border-2 border-emerald-500/30"
          : "bg-rose-500/10 text-rose-500 hover:bg-rose-500/20 border-2 border-rose-500/30",
      )}
      aria-label={kind === "tailor" ? "Tailor" : "Reject"}
    >
      <Icon className="size-6" strokeWidth={2.5} />
    </button>
  )
}

function StackSkeleton() {
  return (
    <div className="flex flex-col items-center gap-5 pt-4">
      <div className="h-4 w-24 bg-muted/40 rounded animate-pulse" />
      <div className="relative w-full max-w-md aspect-[3/4] sm:aspect-[4/5]">
        <div className="absolute inset-0 rounded-[28px] bg-muted/30 animate-pulse" />
      </div>
    </div>
  )
}

function EmptyStack({ hadRows }: { hadRows: boolean }) {
  return (
    <div className="py-20 text-center">
      <div className="text-5xl mb-4">{hadRows ? "🎉" : "🌱"}</div>
      <div className="text-base font-medium mb-1">
        {hadRows ? "All caught up!" : "Nothing to triage"}
      </div>
      <div className="text-sm text-muted-foreground max-w-xs mx-auto">
        {hadRows
          ? "You've worked through every card matching these filters. Check back tomorrow when Scout runs."
          : "No jobs match the current filters. Try widening them or switching to list view to browse."}
      </div>
      {hadRows && (
        <CheckCircle2 className="mt-6 mx-auto size-6 text-emerald-500" />
      )}
    </div>
  )
}
