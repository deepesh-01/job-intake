import { useMemo } from "react"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { fetchJobs, fetchStats } from "@/lib/api"
import { JobCard } from "./JobCard"
import { type Filters } from "./FilterBar"

export function JobsList({
  filters,
  onOpen,
}: {
  filters: Filters
  onOpen: (id: string) => void
}) {
  const params = useMemo(() => {
    const tagsAll: string[] = []
    if (filters.resumeStrong) tagsAll.push("resume_strong")
    if (filters.targetCity) tagsAll.push("target_city")
    return {
      status: filters.status.length ? filters.status : undefined,
      tags_all: tagsAll.length ? tagsAll : undefined,
      tags_none: filters.excludeNonUsOnly ? ["non_us_only"] : undefined,
      q: filters.q || undefined,
      sort: filters.sort,
      limit: 200,
    }
  }, [filters])

  // keepPreviousData keeps the prior list visible during refetch — no
  // blank-flash, no per-card re-mount, no shake. The new data swaps in
  // atomically on success.
  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey: ["jobs", params],
    queryFn: () => fetchJobs(params),
    placeholderData: keepPreviousData,
  })

  // Compare against the unfiltered status count so we can flag rows
  // hidden by tag filters (e.g. 3 ready exist, only 2 match the
  // current resume-strong toggle).
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: fetchStats })
  const statusTotal = useMemo(() => {
    if (!stats || !filters.status.length) return null
    return filters.status.reduce(
      (sum, s) => sum + (stats.by_status[s] || 0), 0,
    )
  }, [stats, filters.status])
  const hiddenByTags =
    statusTotal !== null && data ? Math.max(0, statusTotal - data.total) : 0

  if (isLoading && !data) return <ListSkeleton />
  if (isError) {
    return (
      <div className="py-12 text-center text-sm text-destructive">
        Couldn't load jobs: {(error as Error).message}
      </div>
    )
  }
  if (!data || data.rows.length === 0) {
    return (
      <div className="py-16 text-center">
        <div className="text-2xl mb-2">🌱</div>
        <div className="text-sm text-muted-foreground">
          No jobs match those filters.
        </div>
      </div>
    )
  }

  return (
    <div className="pt-3">
      <div className="px-1 pb-2 text-xs text-muted-foreground tabular-nums flex items-center gap-2 flex-wrap min-h-5">
        <span>{data.total} {data.total === 1 ? "job" : "jobs"}</span>
        {hiddenByTags > 0 && (
          <span className="text-amber-500">
            · {hiddenByTags} hidden by tag filters
          </span>
        )}
        <span className="text-border">·</span>
        <span>cached {Math.round(data.cache_age_s)}s ago</span>
        {isFetching && (
          <span className="ml-1 size-1.5 rounded-full bg-primary animate-pulse" aria-label="Refreshing" />
        )}
      </div>
      {/* No per-card animation — pure render. Hover micro-interactions
          live as CSS transitions on the card itself. */}
      <div className="space-y-2">
        {data.rows.map((job) => (
          <JobCard key={job.id} job={job} onClick={() => onOpen(job.id)} />
        ))}
      </div>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="pt-3 space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="h-[88px] rounded-2xl bg-muted/30 animate-pulse"
          style={{ animationDelay: `${i * 60}ms` }}
        />
      ))}
    </div>
  )
}
