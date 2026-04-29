import { useEffect, useRef, useState } from "react"
import { Drawer } from "vaul"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
  ExternalLink,
  FileText,
  Loader2,
  MapPin,
  Rocket,
  Wand2,
  X,
  XCircle,
  ArrowUpRight,
  Calendar,
  Building2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import * as Dialog from "@radix-ui/react-dialog"
import { fetchJob, patchJob, retailorWithFeedback, reuploadResume, startCopilot, type JobDetail as JobDetailRow, type RetailorReason } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { cn, formatComp, formatDateTime, formatDateTimeFull, formatRelativeDate } from "@/lib/utils"

export function JobDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const open = id !== null
  useBackButtonClose(open, onClose)

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

function useBackButtonClose(open: boolean, onClose: () => void) {
  // Without a synthetic history entry, the system/browser back button has
  // nowhere to go inside the SPA and exits the app. Push an entry on open
  // so back closes the drawer; pop it when the drawer closes any other way
  // (X, overlay, swipe) to avoid leaving phantom entries in history.
  const closedByPopstate = useRef(false)
  useEffect(() => {
    if (!open) return
    closedByPopstate.current = false
    window.history.pushState({ drawerOpen: true }, "")
    const onPop = () => {
      closedByPopstate.current = true
      onClose()
    }
    window.addEventListener("popstate", onPop)
    return () => {
      window.removeEventListener("popstate", onPop)
      if (!closedByPopstate.current) window.history.back()
    }
  }, [open, onClose])
}

function DetailContent({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient()
  const { canMutate } = useAuth()
  const [retailorOpen, setRetailorOpen] = useState(false)
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

        {/* Resume link — three states based on resume_path + local_pdf_exists:
            (1) http URL    → green pill, opens Drive in new tab
            (2) local + file present → amber "Re-upload to Drive" button
            (3) local + file missing → amber warning, re-tailor required
            See ADR-025 for context. */}
        <ResumeLink job={job} canMutate={canMutate} />
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
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <ActionButton
              kind="primary"
              disabled={!canMutate || mut.isPending || job.status === "tailor"}
              onClick={() => {
                if (job.status === "ready" || job.status === "applied") {
                  setRetailorOpen(true)
                } else {
                  setStatus("tailor", "Queued for tailoring")
                }
              }}
              icon={Wand2}
              label={job.status === "ready" || job.status === "applied" ? "Re-tailor" : "Tailor"}
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
          {job.status === "ready" && job.link?.includes("linkedin.com") && (
            <button
              disabled={!canMutate}
              onClick={() => {
                startCopilot(job.id)
                  .then(() => toast.success("Co-pilot launching — Chromium window opens shortly"))
                  .catch((err: Error) => toast.error(err.message))
              }}
              className={cn(
                "w-full h-10 px-4 rounded-xl text-sm font-medium",
                "bg-primary text-primary-foreground hover:bg-primary/90",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "flex items-center justify-center gap-2 transition-colors",
              )}
            >
              <Rocket className="size-4" />
              Apply with co-pilot
            </button>
          )}
        </div>
      </div>
      <RetailorDialog
        open={retailorOpen}
        onOpenChange={setRetailorOpen}
        jobId={job.id}
        onQueued={() => {
          qc.invalidateQueries({ queryKey: ["jobs"] })
          qc.invalidateQueries({ queryKey: ["job", job.id] })
        }}
      />
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


const RETAILOR_OPTIONS: { value: RetailorReason; label: string; hint: string }[] = [
  { value: "layout", label: "Layout not okay", hint: "Visual structure / formatting / hierarchy is off" },
  { value: "shallow_detailing", label: "Shallow detailing", hint: "Bullets feel generic — not concrete enough" },
  { value: "drifting_from_jd", label: "Drifting from JD", hint: "Bullets don't address what the JD actually asks for" },
  { value: "other", label: "Other", hint: "Free-text — describe what's wrong" },
]


// localStorage keys for remembering the user's preferences across sessions.
const ITERATE_PREF_KEY = "job_intake_retailor_iterate"
const ITERATE_REMEMBER_KEY = "job_intake_retailor_iterate_remember"


function RetailorDialog({
  open,
  onOpenChange,
  jobId,
  onQueued,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  jobId: string
  onQueued: () => void
}) {
  // Pull initial iterate / remember from localStorage. Default iterate=true
  // (best-quality path: edit existing resume vs. fresh-tailor from base).
  const initialRemember = typeof window !== "undefined"
    ? localStorage.getItem(ITERATE_REMEMBER_KEY) === "1"
    : false
  const initialIterate = typeof window !== "undefined" && initialRemember
    ? localStorage.getItem(ITERATE_PREF_KEY) !== "0"
    : true

  const [reason, setReason] = useState<RetailorReason>("layout")
  const [details, setDetails] = useState("")
  const [iterate, setIterate] = useState<boolean>(initialIterate)
  const [remember, setRemember] = useState<boolean>(initialRemember)
  const [submitting, setSubmitting] = useState(false)

  const reset = () => {
    setReason("layout")
    setDetails("")
    setSubmitting(false)
    // Don't reset iterate/remember — those persist intentionally.
  }

  const submit = async () => {
    if (reason === "other" && !details.trim()) {
      toast.error("Please describe what's wrong")
      return
    }
    if (remember) {
      localStorage.setItem(ITERATE_REMEMBER_KEY, "1")
      localStorage.setItem(ITERATE_PREF_KEY, iterate ? "1" : "0")
    } else {
      localStorage.removeItem(ITERATE_REMEMBER_KEY)
      localStorage.removeItem(ITERATE_PREF_KEY)
    }
    setSubmitting(true)
    try {
      await retailorWithFeedback(jobId, reason, details.trim() || undefined, iterate)
      toast.success(
        iterate
          ? "Iterating on existing resume — feedback queued"
          : "Fresh re-tailor queued — feedback will be passed to the pipeline",
      )
      onQueued()
      onOpenChange(false)
      reset()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o)
        if (!o) reset()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[60]" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[70] w-[92vw] max-w-md bg-background border border-border rounded-2xl shadow-2xl p-5 outline-none"
        >
          <Dialog.Title className="text-base font-semibold">
            Why re-tailor?
          </Dialog.Title>
          <Dialog.Description className="text-xs text-muted-foreground mt-1">
            Your feedback is passed to the resume pipeline so the next attempt addresses it specifically.
          </Dialog.Description>

          <div className="mt-4 space-y-2">
            {RETAILOR_OPTIONS.map((opt) => {
              const active = reason === opt.value
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setReason(opt.value)}
                  className={cn(
                    "w-full text-left px-3 py-2.5 rounded-xl border transition-all",
                    active
                      ? "border-primary bg-primary/10"
                      : "border-border hover:border-foreground/40 bg-transparent",
                  )}
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <span
                      className={cn(
                        "size-3.5 rounded-full border-2 grid place-items-center shrink-0",
                        active ? "border-primary" : "border-muted-foreground/40",
                      )}
                    >
                      {active && <span className="size-1.5 rounded-full bg-primary" />}
                    </span>
                    {opt.label}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5 ml-5">{opt.hint}</div>
                </button>
              )
            })}
          </div>

          <textarea
            value={details}
            onChange={(e) => setDetails(e.target.value)}
            placeholder={
              reason === "other"
                ? "Describe what's wrong (required)…"
                : "Optional: add specific feedback the LLM should address…"
            }
            rows={3}
            className="mt-3 w-full px-3 py-2 rounded-xl border border-border bg-muted/20 text-sm outline-none focus:border-primary placeholder:text-muted-foreground/60"
          />

          <div className="mt-3 space-y-2">
            <label className="flex items-start gap-2 cursor-pointer text-xs">
              <input
                type="checkbox"
                checked={iterate}
                onChange={(e) => setIterate(e.target.checked)}
                className="mt-0.5 size-4 rounded border-border bg-background text-primary focus:ring-primary"
              />
              <span>
                <span className="text-foreground font-medium">Iterate on existing tailored resume</span>
                <span className="block text-muted-foreground mt-0.5">
                  Resumes the prior Claude session and edits resume.md in place. Falls back to fresh tailor if no prior run exists.
                </span>
              </span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-xs ml-6">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="size-3.5 rounded border-border bg-background text-primary focus:ring-primary"
              />
              <span className="text-muted-foreground">Remember my choice</span>
            </label>
          </div>

          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
              className="px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={submitting || (reason === "other" && !details.trim())}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {submitting && <Loader2 className="size-3.5 animate-spin" />}
              {submitting ? "Queueing…" : "Re-tailor with feedback"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}


function ResumeLink({ job, canMutate }: { job: JobDetailRow; canMutate: boolean }) {
  const qc = useQueryClient()
  const reupload = useMutation({
    mutationFn: () => reuploadResume(job.id),
    onSuccess: () => {
      toast.success("Re-uploaded to Drive")
      qc.invalidateQueries({ queryKey: ["job", job.id] })
      qc.invalidateQueries({ queryKey: ["jobs"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  if (!job.resume_path) return null

  // State 1: Drive URL — open in new tab.
  if (job.resume_path.startsWith("http")) {
    return (
      <a
        href={job.resume_path}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-3 flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/15 text-emerald-500 transition-colors text-xs font-medium"
      >
        <span className="flex items-center gap-2">
          <FileText className="size-4" />
          Tailored resume
        </span>
        <ArrowUpRight className="size-4" />
      </a>
    )
  }

  // State 2: local path, file still on disk — offer reupload.
  if (job.local_pdf_exists) {
    return (
      <button
        disabled={!canMutate || reupload.isPending}
        onClick={() => reupload.mutate()}
        className="mt-3 w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl bg-amber-500/10 hover:bg-amber-500/15 text-amber-500 transition-colors text-xs font-medium disabled:opacity-60 disabled:cursor-not-allowed"
      >
        <span className="flex items-center gap-2">
          {reupload.isPending ? <Loader2 className="size-4 animate-spin" /> : <CloudUpload className="size-4" />}
          {reupload.isPending ? "Uploading…" : "Drive upload failed — Re-upload to Drive"}
        </span>
        <ArrowUpRight className="size-4" />
      </button>
    )
  }

  // State 3: local path, file gone — re-tailor required.
  return (
    <div className="mt-3 flex items-center gap-2 px-3 py-2.5 rounded-xl bg-amber-500/10 text-amber-500 text-xs font-medium cursor-default select-none">
      <AlertTriangle className="size-4 shrink-0" />
      <span>Resume PDF missing on disk — re-tailor required</span>
    </div>
  )
}
