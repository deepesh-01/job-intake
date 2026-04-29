// API client — talks to FastAPI at /api (proxied by Vite dev server,
// or absolute when served from anywhere else).

export interface JobSummary {
  id: string
  company: string
  role: string
  location: string | null
  status: string
  tags: string[]
  resume_match: number
  comp_string: string | null
  comp_currency: string | null
  comp_high: number | null
  comp_high_usd: number | null
  link: string
  discovered_at: string
  posted_at: string
  resume_path: string | null
  last_change: string | null
  tailored_at: string | null
  applied_at: string | null
  response_at: string | null
  followup_due_at: string | null
  notes: string | null
  filter_updated_at: string | null
}

export interface JobDetail extends JobSummary {
  jd_snippet: string
  jd_full_path: string
  jd_markdown: string | null
  tag_reasons: string | null
  local_pdf_exists: boolean | null
}

export interface JobsListResponse {
  rows: JobSummary[]
  total: number
  served_at: number
  cache_age_s: number
}

export interface StatsResponse {
  by_status: Record<string, number>
  top_tags: [string, number][]
  total: number
  today_added: number
  cache_age_s: number
}

export interface JobUpdate {
  status?: string
  applied_at?: string
  response_at?: string
  notes?: string
}

const BASE = ""  // proxied via vite dev server; absolute in prod

// Owner write-token (set once via ?token=… URL param, then stored in
// localStorage). Sent on every mutation request as a Bearer header.
const TOKEN_KEY = "job_intake_write_token"

;(function bootstrapToken() {
  if (typeof window === "undefined") return
  const sp = new URLSearchParams(window.location.search)
  const t = sp.get("token")
  if (t) {
    localStorage.setItem(TOKEN_KEY, t)
    sp.delete("token")
    const clean = window.location.pathname + (sp.toString() ? `?${sp}` : "") + window.location.hash
    window.history.replaceState({}, "", clean)
  }
})()

export function getWriteToken(): string {
  if (typeof window === "undefined") return ""
  return localStorage.getItem(TOKEN_KEY) || ""
}

export function clearWriteToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(): Record<string, string> {
  const t = getWriteToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} from ${path}`)
  return r.json() as Promise<T>
}

export async function fetchJobs(params: {
  status?: string[]
  tags_all?: string[]
  tags_none?: string[]
  q?: string
  discovered_within?: string
  sort?: string
  limit?: number
  offset?: number
}): Promise<JobsListResponse> {
  const sp = new URLSearchParams()
  if (params.status?.length) sp.set("status", params.status.join(","))
  if (params.tags_all?.length) sp.set("tags_all", params.tags_all.join(","))
  if (params.tags_none?.length) sp.set("tags_none", params.tags_none.join(","))
  if (params.q) sp.set("q", params.q)
  if (params.discovered_within) sp.set("discovered_within", params.discovered_within)
  if (params.sort) sp.set("sort", params.sort)
  if (params.limit) sp.set("limit", String(params.limit))
  if (params.offset) sp.set("offset", String(params.offset))
  return jget<JobsListResponse>("/api/jobs?" + sp.toString())
}

export async function fetchJob(id: string): Promise<JobDetail> {
  return jget<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`)
}

export async function patchJob(id: string, body: JobUpdate): Promise<JobDetail> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const text = await r.text()
    if (r.status === 403) throw new Error("Read-only — open the owner URL with ?token=…")
    throw new Error(`PATCH ${r.status}: ${text.slice(0, 200)}`)
  }
  return r.json() as Promise<JobDetail>
}

export async function fetchStats(): Promise<StatsResponse> {
  return jget<StatsResponse>("/api/stats")
}

export async function runProcessor(): Promise<{
  exit_code: number
  stdout_tail: string
  stderr_tail: string
}> {
  const r = await fetch("/api/process", {
    method: "POST",
    headers: authHeaders(),
  })
  if (!r.ok) {
    if (r.status === 403) throw new Error("Read-only — open the owner URL with ?token=…")
    throw new Error(`process ${r.status}`)
  }
  return r.json()
}

export async function fetchHealth(): Promise<{ ok: boolean; read_only: boolean }> {
  return jget("/api/health")
}

export type RetailorReason = "layout" | "shallow_detailing" | "drifting_from_jd" | "other"

export async function retailorWithFeedback(
  jobId: string,
  reason: RetailorReason,
  details?: string,
  iterate: boolean = true,
): Promise<{ ok: boolean; reason: string; iterate: boolean }> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/retailor`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ reason, details: details || null, iterate }),
  })
  if (!r.ok) {
    const text = await r.text()
    if (r.status === 403) throw new Error("Read-only — owner only")
    throw new Error(`retailor ${r.status}: ${text.slice(0, 200)}`)
  }
  return r.json()
}

export async function reuploadResume(jobId: string): Promise<{ ok: boolean; drive_url: string }> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume/reupload`, {
    method: "POST",
    headers: authHeaders(),
  })
  if (!r.ok) {
    const text = await r.text()
    if (r.status === 403) throw new Error("Read-only — owner only")
    throw new Error(`reupload ${r.status}: ${text.slice(0, 200)}`)
  }
  return r.json()
}

export async function startCopilot(jobId: string): Promise<{ ok: boolean; pid: number }> {
  const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/copilot/start`, {
    method: "POST",
    headers: authHeaders(),
  })
  if (!r.ok) {
    const text = await r.text()
    if (r.status === 403) throw new Error("Read-only — owner only")
    throw new Error(`copilot ${r.status}: ${text.slice(0, 200)}`)
  }
  return r.json()
}

export async function retryErrored(): Promise<{ reset: number; job_ids: string[] }> {
  const r = await fetch("/api/jobs/retry-errored", {
    method: "POST",
    headers: authHeaders(),
  })
  if (!r.ok) {
    if (r.status === 403) throw new Error("Read-only — owner only")
    throw new Error(`retry-errored ${r.status}`)
  }
  return r.json()
}

export interface ProcessStatus {
  is_running: boolean
  current_id: string | null
  current_company: string | null
  current_role: string | null
  queue_remaining: number
  ready_total: number
  active_locks: number
}

export async function fetchProcessStatus(): Promise<ProcessStatus> {
  return jget<ProcessStatus>("/api/process/status")
}

export interface BotHealth {
  state: "ok" | "hung" | "down"
  is_alive: boolean
  is_hung: boolean
  pids: number[]
  heartbeat_age_seconds: number | null
  last_heartbeat_iso: string | null
  stale_threshold_seconds: number
  auto_watchdog_interval_seconds: number
  restart_cooldown_seconds: number
}

export async function fetchBotHealth(): Promise<BotHealth> {
  return jget<BotHealth>("/api/bot/health")
}

export async function restartBot(): Promise<{
  ok: boolean
  killed_pids: number[]
  spawn_pid: number
  new_pids: number[]
  log_path: string
}> {
  const r = await fetch("/api/bot/restart", {
    method: "POST",
    headers: authHeaders(),
  })
  if (!r.ok) {
    if (r.status === 403) throw new Error("Read-only — owner only")
    throw new Error(`restart ${r.status}`)
  }
  return r.json()
}
