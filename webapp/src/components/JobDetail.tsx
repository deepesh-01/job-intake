import { Drawer } from "vaul"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  CheckCircle2,
  ExternalLink,
  FileText,
  MapPin,
  Wand2,
  X,
  XCircle,
  ArrowUpRight,
  Calendar,
  Building2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import { fetchJob, patchJob } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn, formatComp, formatDateTime, formatDateTimeFull, formatRelativeDate } from "@/lib/utils"

export function JobDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const open = id !== null

  return (
    <Drawer.Root
      open={open}
      onOpenChange={(o) => !o && onClose()}
      direction="right"
      modal
    >
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 bg-black/40 z-40 backdrop-blur-sm" />
        <Drawer.Content
          className={cn(
            "fixed bottom-0 right-0 z-50",
            "h-[85vh] w-full sm:h-full sm:w-[640px]",
            "bg-background border-t border-l border-border/60",
            "rounded-t-3xl sm:rounded-t-none sm:rounded-l-3xl",
            "shadow-2xl shadow-black/40",
            "outline-none flex flex-col",
          )}
        >
          <Drawer.Title className="sr-only">Job detail</Drawer.Title>
          <Drawer.Description className="sr-only">Full details for the selected job posting.</Drawer.Description>
          {/* mobile drag handle */}
          <div className="sm:hidden mx-auto mt-2 h-1 w-10 rounded-full bg-muted-foreground/30" />
          {id && <DetailContent id={id} onClose={onClose} />}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  )
}

function DetailContent({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient()
  const { canMutate } = useAuth()
  const { data: job, isLoading, isError } = useQuery({
    queryKey: ["job", id],
    queryFn: () => fetchJob(id),
  })

  const mut = useMutation({
    mutationFn: (body: Parameters<typeof patchJob>[1]) => patchJob(id, body),
    onSuccess: (updated) => {
      qc.setQueryData(["job", id], updated)
      qc.invalidateQueries({ queryKey: ["jobs"] })
      qc.invalidateQueries({ queryKey: ["stats"] })
    },
    onError: (err: Error) => toast.error(`Update failed: ${err.message}`),
  })

  if (isLoading) return <DetailSkeleton onClose={onClose} />
  if (isError || !job) {
    return (
      <div className="p-6">
        <button onClick={onClose} className="text-sm text-muted-foreground">Close</button>
        <div className="py-12 text-center text-sm text-destructive">Couldn't load this job.</div>
      </div>
    )
  }

  const setStatus = (next: string, toastMsg: string) => {
    const today = new Date().toISOString().slice(0, 10)
    mut.mutate(
      next === "applied"
        ? { status: next, applied_at: job.applied_at || today }
        : { status: next },
      {
        onSuccess: () => toast.success(toastMsg),
      },
    )
  }

  const compStr = job.comp_string || formatComp(job.comp_currency, job.comp_high, job.comp_high_usd)
  const tagPairs = (job.tag_reasons || "")
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean)

  return (
    <>
      {/* Sticky header */}
      <div className="px-5 sm:px-6 pt-3 pb-4 border-b border-border/60 backdrop-blur-xl bg-background/80">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
              <Building2 className="size-3" />
              <span className="font-medium text-foreground/90">{job.company}</span>
              {job.location && (
                <>
                  <span>·</span>
                  <span className="flex items-center gap-1 truncate">
                    <MapPin className="size-3" /> {job.location}
                  </span>
                </>
              )}
            </div>
            <div className="text-lg sm:text-xl font-semibold tracking-tight leading-snug">
              {job.role}
            </div>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 size-9 grid place-items-center rounded-full text-muted-foreground hover:text-foreground hover:bg-muted transition-colors -mr-2"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* metadata row */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-muted/40 tabular-nums">
            <span className="text-muted-foreground">Match</span>
            <span className="font-semibold">{Math.round(job.resume_match * 100)}%</span>
          </div>
          {compStr && (
            <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-muted/40 tabular-nums">
              <span className="text-muted-foreground">Comp</span>
              <span className="font-medium">{compStr}</span>
            </div>
          )}
          <div
            className="flex items-center gap-1 px-2 py-1 rounded-md bg-muted/40"
            title={`Added ${formatDateTimeFull(job.discovered_at)}`}
          >
            <Calendar className="size-3 text-muted-foreground" />
            <span className="tabular-nums">
              {formatDateTime(job.discovered_at)}
              <span className="text-muted-foreground"> · {formatRelativeDate(job.discovered_at)}</span>
            </span>
          </div>
          <a
            href={job.link}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 px-2 py-1 rounded-md bg-primary/10 text-primary hover:bg-primary/15 transition-colors"
          >
            <ExternalLink className="size-3" />
            View original
          </a>
        </div>

        {/* current status badge + last_change if present */}
        {(job.status !== "new" || job.last_change) && (
          <div className="mt-3 p-3 rounded-xl bg-muted/40 text-xs space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Status</span>
              <StatusBadge status={job.status} />
            </div>
            {job.tailored_at && (
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Tailored</span>
                <span>{formatRelativeDate(job.tailored_at)}</span>
              </div>
            )}
            {job.applied_at && (
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Applied</span>
                <span>{job.applied_at}</span>
              </div>
            )}
            {job.last_change && (
              <div className="pt-2 border-t border-border/60 text-foreground/90 leading-relaxed">
                <span className="text-muted-foreground">Tailoring summary: </span>
                {job.last_change}
              </div>
            )}
          </div>
        )}

        {/* Resume link if ready */}
        {job.resume_path && (
          <a
            href={job.resume_path.startsWith("http") ? job.resume_path : "#"}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => {
              if (!job.resume_path?.startsWith("http")) {
                e.preventDefault()
                navigator.clipboard?.writeText(job.resume_path || "")
                toast.success("Local path copied to clipboard")
              }
            }}
            className="mt-3 flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/15 text-emerald-500 transition-colors text-xs font-medium"
          >
            <span className="flex items-center gap-2">
              <FileText className="size-4" />
              Tailored resume
            </span>
            <ArrowUpRight className="size-4" />
          </a>
        )}
      </div>

      {/* Scrollable JD body */}
      <div className="flex-1 overflow-y-auto px-5 sm:px-6 py-5">
        {tagPairs.length > 0 && (
          <details className="mb-5 group" open={false}>
            <summary className="text-xs text-muted-foreground hover:text-foreground cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              Why these tags?
            </summary>
            <div className="mt-2 space-y-1 text-[11px] font-mono leading-relaxed text-muted-foreground bg-muted/30 rounded-lg p-3">
              {tagPairs.map((p, i) => (
                <div key={i}>{p}</div>
              ))}
            </div>
          </details>
        )}

        <div className="prose-invert max-w-none text-sm leading-relaxed">
          {job.jd_markdown ? (
            <ReactMarkdown
              components={{
                h1: ({ children }) => <h2 className="text-lg font-semibold mt-5 mb-2">{children}</h2>,
                h2: ({ children }) => <h3 className="text-base font-semibold mt-4 mb-1.5">{children}</h3>,
                h3: ({ children }) => <h4 className="text-sm font-semibold mt-3 mb-1">{children}</h4>,
                p: ({ children }) => <p className="mb-3 text-foreground/90">{children}</p>,
                ul: ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1 text-foreground/90">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1 text-foreground/90">{children}</ol>,
                li: ({ children }) => <li className="text-foreground/90">{children}</li>,
                a: ({ href, children }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">{children}</a>
                ),
                code: ({ children }) => <code className="px-1 py-0.5 rounded bg-muted text-foreground/90 text-[12px]">{children}</code>,
                strong: ({ children }) => <strong className="text-foreground font-semibold">{children}</strong>,
              }}
            >
              {job.jd_markdown}
            </ReactMarkdown>
          ) : (
            <div className="text-muted-foreground">
              {job.jd_snippet}
              <div className="mt-3 text-xs italic">
                Full JD body not on disk — use the View original link above.
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Sticky action bar — disabled in preview mode but still visible
          so visitors can see the affordance */}
      <div className="border-t border-border/60 px-5 sm:px-6 py-3 bg-background/80 backdrop-blur-xl">
        {!canMutate && (
          <div className="text-[10px] text-muted-foreground text-center mb-2 uppercase tracking-wider">
            Preview mode — owner only
          </div>
        )}
        <div className="flex items-center gap-2">
          <ActionButton
            kind="primary"
            disabled={!canMutate || mut.isPending || job.status === "tailor" || job.status === "ready"}
            onClick={() => setStatus("tailor", "Queued for tailoring")}
            icon={Wand2}
            label={job.status === "ready" ? "Re-tailor" : "Tailor"}
          />
          <ActionButton
            kind="success"
            disabled={!canMutate || mut.isPending || job.status === "applied"}
            onClick={() => setStatus("applied", "Marked as applied")}
            icon={CheckCircle2}
            label="Applied"
          />
          <ActionButton
            kind="destructive"
            disabled={!canMutate || mut.isPending || job.status === "rejected"}
            onClick={() => setStatus("rejected", "Marked as rejected")}
            icon={XCircle}
            label="Reject"
          />
        </div>
      </div>
    </>
  )
}

function ActionButton({
  kind,
  disabled,
  onClick,
  icon: Icon,
  label,
}: {
  kind: "primary" | "success" | "destructive"
  disabled?: boolean
  onClick: () => void
  icon: any
  label: string
}) {
  const styles: Record<string, string> = {
    primary: "bg-primary text-primary-foreground hover:bg-primary/90",
    success: "bg-emerald-500 text-white hover:bg-emerald-500/90",
    destructive: "bg-rose-500/10 text-rose-500 hover:bg-rose-500/15 border border-rose-500/20",
  }
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex-1 h-11 inline-flex items-center justify-center gap-2 rounded-xl text-sm font-medium",
        "transition-all active:scale-[0.98]",
        "disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100",
        styles[kind],
      )}
    >
      <Icon className="size-4" strokeWidth={2.5} />
      {label}
    </button>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    new: "bg-muted text-muted-foreground",
    tailor: "bg-amber-500/15 text-amber-500",
    ready: "bg-emerald-500/15 text-emerald-500",
    applied: "bg-blue-500/15 text-blue-500",
    rejected: "bg-rose-500/10 text-rose-500",
    skip: "bg-muted text-muted-foreground/60",
    error: "bg-destructive/15 text-destructive",
  }
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-medium uppercase tracking-wide", map[status] || map.new)}>
      {status}
    </span>
  )
}

function DetailSkeleton({ onClose }: { onClose: () => void }) {
  return (
    <div className="p-6 space-y-4">
      <div className="flex justify-end">
        <button onClick={onClose} className="size-8 grid place-items-center rounded-full hover:bg-muted">
          <X className="size-4" />
        </button>
      </div>
      <div className="h-6 bg-muted/40 rounded animate-pulse w-2/3" />
      <div className="h-4 bg-muted/40 rounded animate-pulse w-1/3" />
      <div className="h-32 bg-muted/30 rounded-xl animate-pulse" />
      <div className="h-4 bg-muted/40 rounded animate-pulse" />
      <div className="h-4 bg-muted/40 rounded animate-pulse w-5/6" />
      <div className="h-4 bg-muted/40 rounded animate-pulse w-4/6" />
    </div>
  )
}
