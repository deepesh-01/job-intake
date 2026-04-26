# Build Log — chronological

*Each section is a build step in the order it shipped. Don't edit prior
sections; they're historical. Append new steps at the bottom.*

> Sibling docs:
> - [`vision.md`](./vision.md) — current state + 10x roadmap
> - [`how-to-journey.md`](./how-to-journey.md) — operational guide
> - [`decisions.md`](./decisions.md) — ADR log of non-obvious choices
> - [`job-intake-design-v1-original.md`](./job-intake-design-v1-original.md) — frozen v1 design

---

# Step 1 — Repo skeleton + shared lib

*Scope: Python 3.11 / uv project at the project root. All shared
primitives (config loader, structured logger, schema constants, Posting
dataclass) before any source clients.*

## 1.1 — `pyproject.toml` (~5 min)
- `uv` + Python 3.11 minimum.
- Deps: `gspread`, `google-auth`, `httpx`, `selectolax`, `markdownify`,
  `structlog`, `pyyaml`, `rapidfuzz`, `python-dotenv`. Dev: `pytest`,
  `pytest-mock`.
- `[project.scripts]` for `scout` and `processor`.

## 1.2 — Shared lib (~15 min)
- `src/lib/config.py` — `load_env()` reads `.env`, validates required vars,
  returns frozen `Env` dataclass. Fail-fast with a single readable error
  if any required key is missing.
- `src/lib/logging.py` — structlog with key=value renderer, ISO timestamps.
- `src/lib/posting.py` — `Posting` (source-client output) and `EnrichedRow`
  (sheet-write-ready) dataclasses. `SourceError` exception with
  `last_error` carry.

## 1.3 — Sheet schema (~10 min)
- `src/sheet/schema.py` — `SCHEMA_VERSION=1` + `JOBS_COLUMNS` (25 cols
  in v1), `BOARDS_COLUMNS`, `LOG_COLUMNS`, status enum constants,
  `col_idx()`/`col_letter()` helpers.

**Done when:** `python -c "from lib.config import load_env"` works
(against an empty `.env` it raises with the missing-vars list).

---

# Step 2 — Sheet client + bootstrap

## 2.1 — `src/sheet/client.py` (~30 min)
- gspread wrapper with all read + write helpers as methods.
- Schema-version assertion on startup (refuse to run if Sheet is newer
  than code).
- Batched writes: `append_jobs(rows)` for new rows, `batch_update(...)`
  for cell-level patches, `replace_boards_tab` + `truncate_log` for
  housekeeping. Single Scout pass = ≤5 API calls.
- `read_seen_ids()` for dedup hydration; `read_followup_candidates()`
  for the daily sweep; `read_tailor_queue()` for processor.

## 2.2 — Bootstrap script (~10 min)
- `scripts/bootstrap_sheet.py` — creates `Jobs` / `Boards` / `Log` tabs
  on a fresh Sheet, writes `schema_version: 1` to `Jobs!A1`, freezes
  header rows.

## 2.3 — Migration framework (~5 min)
- `src/sheet/migrate.py` — `migrate(client)` walks from current
  `schema_version` → `SCHEMA_VERSION` running each registered migration.
  Empty in v1; will be used in step 12.

**Smoke checklist:**
- [ ] `bootstrap_sheet.py` produces three tabs in a real Sheet.
- [ ] `Jobs!A1` reads `schema_version: 1`.
- [ ] Re-running is idempotent.

---

# Step 3 — ATS source clients (Greenhouse, Lever, Ashby)

## 3.1 — Shared HTTP throttle (~10 min)
- `src/scout/sources/_http.py` — `get_json()` wrapper. 1 req/sec/host
  throttle (per §10.3 of v1 design). Exponential backoff on 429/5xx
  with 3 retries.

## 3.2 — Three source modules (~30 min, ~80 lines each)
- `greenhouse.py` — `GET boards-api.greenhouse.io/v1/boards/{board_id}/jobs?content=true`
- `lever.py` — `GET api.lever.co/v0/postings/{board_id}?mode=json`
- `ashby.py` — `GET api.ashbyhq.com/posting-api/job-board/{board_id}`
- Each implements the §13.3 contract: `fetch(board_id) -> list[Posting]`,
  raises `SourceError` on 404/timeout.

**Smoke checklist:**
- [ ] `python -c "from scout.sources.greenhouse import fetch; print(len(fetch('anthropic')))"` returns >0
- [ ] Same for `lever:razorpay` (later — they'd moved off Lever, see step 13)
- [ ] `ashby:vercel` returns the expected shape

---

# Step 4 — Aggregator sources (YC WaaS, HN Hiring)

## 4.1 — `yc_waas.py` (~20 min)
- Hits `workatastartup.com/api/jobs` (unofficial endpoint the site itself
  uses). Tolerant parsing — shape varies. Marked auto-firing of
  `early_stage` tag at runner level.

## 4.2 — `hn_hiring.py` (~30 min)
- Algolia HN API: `hn.algolia.com/api/v1/search?query=Ask HN Who is hiring`
  to find latest thread, then `items/<id>` for top-level comments.
- Heuristic line-1 parser: `Company | Role | Location | Remote | Comp`.
- Day-of-month gate: only runs on the 1st-3rd of each month.

**Note:** `yc_waas` 404'd at first verify (their endpoint changed) — kept
disabled in `boards.yaml`; HN works monthly.

---

# Step 5 — Comp parsing + extraction

## 5.1 — `src/scout/extract.py` (~45 min)
- `html_to_text` / `html_to_markdown` via selectolax + markdownify.
- `normalize_company` strips `LLC/Inc/Pvt Ltd/...` suffixes, lowercases.
- `normalize_role` lowercases + strips `(Remote)` / `[US]` brackets.
- Currency-aware comp parser:
  - INR: `LPA / lakh / lakhs / cr / crore / 30L` shorthand
  - USD/GBP/EUR/SGD/AUD/CAD: symbol + comma + `k/K` magnitude
  - Range patterns: `X-Y`, `X to Y`, `up to Y`, `X+`, equity-only fallback
- Returns `Comp(comp_string, currency, low, high, low_usd, high_usd)`.
- Hard rule: never assume USD when currency unknown — return None and
  let tagging fire `comp_unknown`.

---

# Step 6 — Tagging engine

## 6.1 — `src/scout/tag.py` (~45 min)
All tag families per §6 of the original design:
- **Comp**: currency-aware floor check (INR vs USD-equivalent)
- **Stack**: section-anchored keyword (must appear under "Requirements"
  / "Must have" / "You have" header)
- **Seniority**: title-only regex
- **Application_eng vs wrong_discipline**: title-only with explicit
  match list + exclude list
- **Stage**: JD body keyword (early/growth/late)
- **Culture**: AI-native + chill + grind signals; derived `enjoy_eligible`
- **Location**: remote_ok + non_us_only + later target_city
- **Other**: posted_recent, has_recruiter_email
- **Follow-up**: needs_followup() helper for the daily sweep

`tag_reasons` string preserves the *why* per tag for human debugging.

---

# Step 7 — Dedup + exclude.yaml

## 7.1 — `src/scout/dedup.py` (~20 min)
- SQLite mirror at `data/seen_ids.sqlite`. `(id, discovered_at,
  company_norm, role_norm)` schema.
- Three-stage check: id-exact → fuzzy company+role within last 30 days
  (rapidfuzz Levenshtein ≤3) → otherwise insert.

## 7.2 — `src/scout/exclude.py` + `exclude.yaml` (~10 min)
- Excluded companies (with aliases), keywords, regex patterns.
- Applied at insert time, before tagging — excluded rows never reach
  the Sheet.

---

# Step 8 — Scout runner + first run

## 8.1 — `src/scout/runner.py` (~45 min)
- Loads configs, opens Sheet, hydrates dedup cache from Sheet ids.
- For each enabled board: fetch → exclude → extract → tag → buffer.
- First-run cutoff (7 days) per §7.3 — without this, first scout floods
  with stale postings.
- Aggregator-specific filters (yc_waas pre-tag for application_eng).
- Final intra-batch fuzzy dedup before sheet append.
- Always-on follow-up sweep (idempotent).
- Logs to Sheet's `Log` tab.

## 8.2 — Scripts (~15 min)
- `scripts/verify_boards.py` — pings each enabled board, reports 404s.
- `scripts/reload_boards.py` — refreshes Boards tab without scout sweep.

## 8.3 — First scout run findings
- 1,512 postings fetched across 14 boards
- 395 inserted (rest filtered by 7-day cutoff + exclude + dedup)
- All 395 had `comp_unknown` (false positive — see step 9)
- 22/35 boards in initial `boards.yaml` returned 404 (slug rot)

---

# Step 9 — Critical bug fixes (post-first-run)

## 9.1 — Greenhouse double-encoded HTML (~15 min)
Greenhouse's `content` field is HTML-entity-escaped (`&lt;p&gt;` instead
of `<p>`). Selectolax saw it as plain text, so all HTML tags survived
into the parser output.

**Fix:** `html.unescape()` before passing to selectolax in
`extract.html_to_text()` / `html_to_markdown()`.

## 9.2 — Em dash separator in pay ranges (~5 min)
Anthropic and others use `—` (U+2014) between pay numbers. Regex only
accepted `-` and `–` (U+2013).

**Fix:** added `—`, `‒`, `−` to all range-separator alternations.

## 9.3 — INR L-shorthand outranking USD (~10 min)
`\d\s*L` was hitting "30L users" / "5cr+ users" in Indian product JDs.
Then INR was checked *before* USD in the priority order, so any USD
range with an unrelated `L` token elsewhere got tagged INR.

**Fix:** restructured `detect_currency` priority — foreign currency
symbols first, then explicit INR (₹/Rs/INR/lakh/crore), then INR
shorthand only if no foreign currency was found AND a comp-context
keyword (salary, base, ctc, package) is within ±80 chars of the L token.

## 9.4 — `Rs` regex matching inside English words (~5 min)
`Rs\.?` (no word boundary) was matching "stakeholders", "engineers",
"users". Caused 282/395 rows to be mis-tagged as INR.

**Fix:** added `\b` boundaries: `\bRs\b`, `\bINR\b`.

## 9.5 — `application_eng` missing on architect titles (~10 min)
49 of 52 senior roles missing `application_eng` were titled "Architect"
(Anthropic Applied AI Architect, Solutions Architect, etc.). The narrow
match list and the engineer/developer/swe fallback didn't include
`architect`.

**Fix:** added `architect` to `_DEFAULT_ENG_HINTS` in `tag.py`.

## 9.6 — `target_city` not firing on plain "India" (~5 min)
`my_region: IN` was being added as `\bin\b` to city aliases — never
matched "india" because `in` ends inside the word.

**Fix:** dropped the auto-add. Added `india` (plus `noida`, `mumbai`,
`pune`, `gurgaon`, `gurugram`, `ncr`) directly to `my_city_aliases`.

## 9.7 — Filter views were over-restrictive (~10 min)
Original "Ultimate" filter required `comp_ok + remote_ok + enjoy_eligible
+ resume_strong + app_eng + senior + (not non_us_only)` — 7 ANDs
intersected 395 rows down to 3. Most JDs don't post comp publicly so
`comp_ok` was a coverage gate, not a quality gate.

**Fix:** restructured the 8 saved filter views to chain at most 3 ANDs.
`comp_ok` and `enjoy_eligible` became sort signals, not required ANDs.
Used OR-groups for "remote OR target_city" via Sheets `CUSTOM_FORMULA`.

---

# Step 10 — Source set expansion

## 10.1 — Probe global AI-native + growth-stage (~20 min)
Added 15 candidates to `boards.yaml`. After verify:
- ✓ 8 working: Glean, Perplexity, Modal, Harvey, Browserbase, Inngest,
  MongoDB, Datadog, Twilio, Databricks, Scale AI, Atlassian
- ✗ 7 failed (slug or moved-platform): Hugging Face, Cohere, Cursor,
  Replit, Together AI, Sentry, Plaid

## 10.2 — Free aggregators (~30 min, ~80 lines each)
- `remoteok.py` — `https://remoteok.com/api` JSON list, often has comp
- `remotive.py` — `https://remotive.com/api/remote-jobs?category=software-dev`
- `arbeitnow.py` — `https://www.arbeitnow.com/api/job-board-api`

## 10.3 — Re-probe failed Indian slugs (~30 min)
Probed across all 3 ATS for each, with slug variations. Recovered:
- Razorpay → Greenhouse `razorpaysoftwareprivatelimited` (48 postings) ✓
- Atlan → Ashby (11) ✓
- Groww → Greenhouse (15) ✓
- Notion → Ashby (141) ✓
- Cohere → Ashby (114) ✓
- Cursor → Ashby (71) ✓
- Replit → Ashby (84) ✓
- Sentry → Ashby (41) ✓
- Plaid → Lever (92) ✓
- Freshworks → Lever (0 today, kept enabled) ✓

## 10.4 — Hasjob (HasGeek India) (~15 min)
Original `/api/1/posts` was 404. Found their Atom feed at `/feed` works.
Built `hasjob.py` that XML-parses the Atom feed, extracts company from
URL path segment, location from `<atom:location>`, body from `<content
type="html">`.

## 10.5 — Workday source (~45 min, including bug-fixes)
Built generic Workday client. Probed 13 candidates, 4 worked:
- Adobe (1162 postings · Bangalore office)
- NVIDIA (2000 · Bangalore + Hyderabad)
- Walmart Global Tech (2000 · Bangalore)
- Autodesk (689 · Bangalore)

**Bugs caught during build:**
- Workday rejects `limit > 20` with 400 → capped PAGE_SIZE to 20
- Workday rejects custom User-Agents → switched to a Chrome UA
- Workday list responses have `<200 char` body (just title + bullets) →
  added to the `<200 char` skip exception alongside `hn_hiring`
- Indian unicorns (PhonePe, Flipkart, Swiggy, Zomato, etc.) probed —
  none use public Workday; Paytm uses Lever and was added directly

**Result after step 10:** 33 enabled boards, ~2750 fresh postings/day,
910 deduped + tagged rows, 8 source types, target_city company spread
went from 3 → 9 companies.

---

# Step 11 — Filter view loosening + tag tuning

Already covered as step 9.7 — landed alongside the architect fix.

---

# Step 12 — Phase 1: resume-aware filtering (schema v2)

## 12.1 — `src/scout/resume.py` (~30 min)
Reads `~/bot/users/<chat_id>/base_resume.md` once per scout run.
Curated tech vocab list (~150 tokens, multi-word + single-word) intersected
against the resume → user_skills set (49 detected for the owner). Per
JD: count overlap, normalize. `is_strong()` if count ≥ 3.

## 12.2 — Schema bump v1 → v2 (~10 min)
- `SCHEMA_VERSION = 2`
- New column `resume_match` at index 25 (column Z)
- `_v1_to_v2` migration in `migrate.py` — adds the column to existing
  Sheet
- Live-migrated the in-use Sheet successfully

## 12.3 — Wire into runner + tag (~10 min)
`EnrichedRow.resume_match` field set in `_enrich_one`.
`tag.py` fires `resume_strong` when `resume.is_strong(match)`.
`_to_sheet_row` appends `resume_match` to the row.

## 12.4 — Filter view #7 + #8 + #9 (~5 min)
Added "Resume-strong" + "Resume-strong + India" + "Ultimate shortlist"
views via `add_views.py`.

**Result:** 102 rows tagged `resume_strong` (count ≥3 overlap), match
distribution healthy (median ~0.5, top-10 cluster around 0.7-1.0).

---

# Step 13 — Phase 2: System A `cli-tailor` bridge

## 13.1 — Discovered System A at `~/Documents/resume-builder/` (~5 min)
Original v1 design pointed to `~/code/resume-bot/` but actual location
was different. Read System A's `runJob.ts`, `claude.ts`, `jobs.ts`,
`render.ts`, `workspace.ts`, `config.ts`, `index.ts`, `CLAUDE.md`.

## 13.2 — Built `src/cli-tailor.ts` in resume-builder (~30 min)
Headless CLI mirroring the Telegram-driven pipeline:
`createJobWorkspace` → `runTailoring` → `runCritic` → optional
`runRefinement` → `renderResumePdf`. Args: `--jd-path`, `--chat-id`,
`--output-dir`, `--output-format json`. Outputs JSON with `ok, pdf_path,
last_change, score, refinement_applied, duration_ms, error`.

Does NOT import `index.ts` — bot stays untouched. Does NOT write to
SQLite — CLI is stateless.

## 13.3 — System A docs + ADR (~10 min)
Per System A's CLAUDE.md conventions: appended **build step Q** to
`tasks.md`, **ADR-021** to `decisions.md`, "Headless CLI" section to
`how-to-journey.md`. `npm run typecheck && npm run build` clean —
`dist/cli-tailor.js` produced.

## 13.4 — Updated System B's `tailor_bridge.py` (~10 min)
Subprocess invocation with `cwd=system_a_path` (so dotenv reads System
A's `.env`). New env var `SYSTEM_A_USER_CHAT_ID`. Processor passes
`chat_id` instead of `base_resume_path`.

## 13.5 — End-to-end live test
- Marked Postman Bengaluru row `status=tailor`
- `python -m processor.runner` ran full pipeline in ~5 min
- Real PDF (3 pages, 63KB) landed at `data/tailored/<id>.pdf`
- Sheet row updated: `status=ready`, `resume_path` filled, `last_change:
  "Removed JD-lifted tier-0 framing..."`, `tailored_at` set
- Cost: ~$0.30

## 13.6 — Bugs caught during Phase 2
- Workday `limit > 20` (covered in step 10.5)
- Workday user-agent (step 10.5)
- CLI exited 1 because System A's config.ts couldn't find env vars →
  fixed by setting subprocess `cwd=system_a_path`

---

# Step 14 — Drive integration (OAuth user delegation)

## 14.1 — First attempt: SA-only Drive client (~20 min, failed live)
Added `google-api-python-client` dep, `src/lib/drive.py` with
`DriveClient(creds_path)` using SA credentials. Built end-to-end — SA
uploads 403'd with `storageQuotaExceeded`.

**Diagnosis:** Service accounts have 0GB Drive quota for personal Google
accounts. Even when uploading to a folder shared with the SA, the SA
becomes the file owner and consumes its (zero) quota.

## 14.2 — Pivot to OAuth user delegation (~30 min)
- Added `google-auth-oauthlib` dep
- `DriveClient.from_oauth_token()` — loads user creds from refresh-token JSON
- `DriveClient.from_service_account()` kept as fallback (works only for
  Workspace Shared Drives)
- `scripts/auth_drive.py` — runs `InstalledAppFlow.from_client_secrets_file`,
  opens browser, saves refresh token to disk
- New env vars: `GOOGLE_OAUTH_CLIENT_PATH`, `GOOGLE_OAUTH_TOKEN_PATH`,
  `GOOGLE_DRIVE_FOLDER_ID`

## 14.3 — Wired into processor (~15 min)
After tailor success: upload PDF to Drive folder → set "anyone with link
can view" permission → write returned `webViewLink` to Sheet's
`resume_path` cell instead of local path. Falls back to local path on
upload failure.

## 14.4 — User setup steps that we walked through
1. GCP Console → enable Drive API
2. Create Drive folder → share with SA email as Editor
3. GCP Console → OAuth consent screen → External + add owner email as
   test user (or publish)
4. Create OAuth Client ID → Desktop app → download JSON →
   `~/secrets/job-intake-oauth.json`
5. `uv run python scripts/auth_drive.py` → browser dance → token saved
6. Backfilled 3 already-tailored PDFs to Drive, updated Sheet cells
   with shareable URLs

---

# Step 15 — Webapp (FastAPI + React)

## 15.1 — FastAPI backend (~30 min)
- `src/web/api.py` — `GET /api/jobs` (filters + sort + pagination),
  `GET /api/jobs/{id}`, `PATCH /api/jobs/{id}`, `GET /api/stats`,
  `POST /api/process` (spawns processor subprocess)
- `src/web/cache.py` — 30s TTL snapshot cache (Sheet API caps at 60
  reads/min/user)
- `src/web/server.py` — uvicorn on `0.0.0.0:8090`
- CORS for localhost + LAN IP regex (phone access)
- Port collision with caddy on `:8080` → switched to `:8090`

## 15.2 — Vite + React + Tailwind + shadcn-style scaffold (~20 min)
- TypeScript strict mode
- Tailwind with HSL CSS-variable design tokens (dark + light)
- TanStack Query, framer-motion, sonner, vaul, react-markdown
- `src/lib/utils.ts` — `cn()`, `formatComp()`, `formatRelativeDate()`
- `src/lib/api.ts` — typed client for every endpoint

## 15.3 — List view (~30 min)
- `Header` with live counts + "added today" pulse badge
- `FilterBar` — status pills (multi-select), search, advanced toggles
  (Resume-strong, Target city, Hide US-only), sort dropdown
- `JobsList` — virtualized list, `keepPreviousData` query option
- `JobCard` — Apple Health-style match ring, status badge, tag chips
  with tone (good/neutral/warn/bad), comp badge, time-ago

## 15.4 — Detail drawer (~30 min)
- `JobDetail` — vaul drawer (right-side on desktop, bottom-sheet on
  mobile)
- Sticky metadata header with match%, comp, "View original" link
- Status timeline (when tailored, when applied)
- Markdown JD body via react-markdown (custom heading/list components)
- "Why these tags?" expandable raw `tag_reasons`
- Sticky action bar: Tailor / Applied / Reject (all work at any state)

## 15.5 — Process queue button (~20 min)
- FAB at bottom-right with iOS-style number badge
- Three states: queue empty (greyed), queue has rows (blue+pulsing
  badge), running (subdued + spinner)

## 15.6 — Bug: layout shift / shake on filter changes (~10 min)
**Diagnosis:** `motion.div` with `layout` prop + `AnimatePresence
mode="popLayout"` + staggered `delay` was triggering FLIP layout
animations on every reorder, plus per-item exit-then-enter on filter
changes — created a visible "wave" effect.

**Fix:** stripped all per-card animations, removed `AnimatePresence`,
added `placeholderData: keepPreviousData` to the query so the old list
stays visible during refetch and the new data swaps in atomically.
Hover/active feedback stays as CSS transitions on the card.

## 15.7 — Process button always visible + count (~10 min)
Originally hidden when queue empty. Made always visible:
- Queue empty → greyed disabled
- Queue has rows → vibrant + count badge
- Running → spinner + label

---

# Step 16 — Public deploy via Cloudflare Tunnel

## 16.1 — Discovered existing infra (~5 min)
- `caddy` running on `:8080` for mononest project (irrelevant)
- `cloudflared` config-based tunnel at
  `~/Documents/welog/cloudflare/tunnel-config.yml` already serving
  `welog.deepesh-engg.in`
- Single tunnel can host multiple hostnames

## 16.2 — Production-build webapp + serve from API (~10 min)
- `npm run build` → `webapp/dist/`
- FastAPI mounts `dist/assets` as static + SPA catch-all that returns
  `dist/index.html` for any non-`/api/*` path
- Now everything serves from `:8090` on a single port

## 16.3 — Tunnel ingress (~5 min)
- Added entry `takejob.deepesh-engg.in → http://localhost:8090` to
  welog tunnel config (above the catch-all)
- `launchctl kickstart -k gui/$(id -u)/com.welog.cloudflared` to reload
- DNS auto-routed by user via `cloudflared tunnel route dns`

## 16.4 — Persistent webapp via launchd (~5 min)
- `com.user.jobintake.web.plist` → `KeepAlive=true`, `RunAtLoad=true`
- Logs to `data/web.{out,err}.log`

---

# Step 17 — Read-only mode for public sharing

User wanted to share the URL with friends without adding Cloudflare
Access yet, but mutations would let anyone burn Claude credits.

## 17.1 — `WRITE_TOKEN` env + auth middleware (~15 min)
- Generated 24-char URL-safe token, saved to `.env`
- `_require_write_auth(request)` checks `Authorization: Bearer <token>`
  header OR `?token=...` query param
- Applied to `PATCH /api/jobs/{id}`, `POST /api/process`, later
  `POST /api/bot/restart`
- Added `read_only: bool` to `/api/health` response so the client knows
  the mode

## 17.2 — Frontend token bootstrap (~10 min)
- On app boot: if URL has `?token=...`, save to localStorage and strip
  the param from the URL (history.replaceState)
- All mutation requests include `Authorization: Bearer <token>` header
  if localStorage has one
- 403 responses → friendly toast "Read-only — open the owner URL with
  ?token=…"

## 17.3 — Viewer-mode UI (~15 min)
First attempt: hid mutation buttons entirely. User feedback: keep them
visible but disabled so visitors see the affordance.

**Refined:**
- `useAuth()` hook combines server's `read_only` + localStorage token
  → `canMutate` boolean
- `PreviewBanner` — amber sticky bar at top, viewer mode only, message
  "Preview mode — actions are disabled. Only the owner can change job
  status."
- Action buttons rendered disabled with "Preview mode — owner only"
  caption above
- Process queue FAB always visible, disabled in viewer mode
- Tooltips explain the restriction

## 17.4 — Sticky stack fix (~5 min)
PreviewBanner + Header + FilterBar all wanted top:0 — collisions.
Wrapped all three in a single `sticky top-0 z-40` parent so they
travel together as the header unit.

---

# Step 18 — Live processor progress

User noticed: when processor is running, no UI feedback. Local React
state was lost on page refresh; nothing surfaced from the server.

## 18.1 — `GET /api/process/status` (~15 min)
Server-side detection via `data/locks/*.lock` files (mtime within last
hour = active). Returns `{is_running, current_id, current_company,
current_role, queue_remaining, ready_total, active_locks}`.

Reverses processor's `_safe_id()` mapping by looking up against the
snapshot's known ids (since `:` and `/` both become `_` in lock filenames
and aren't perfectly invertible).

## 18.2 — Frontend polling (~15 min)
- `useQuery` on `/api/process/status` with dynamic `refetchInterval`:
  5s while `is_running=true`, 30s otherwise
- ProcessorButton labels:
  - Running → "Processing N jobs" + sub-label "Now: Stripe · Senior
    Backend Engineer" + spinner, **disabled**
  - Idle + queue → "Process queue" + count badge
  - Idle + empty → "Queue empty", **disabled**
- On run completion (poll detects flip), invalidate jobs + stats so
  the new ready rows appear immediately

---

# Step 19 — Filter discrepancy fix (3 ready, 2 visible)

User saw "3 ready" in header but only 2 rows in the list. Root cause:
default `resumeStrong: true` was excluding the Databricks row (high
match=1.0 but only 2 unique skill overlaps, below `is_strong`'s
count≥3 threshold).

## 19.1 — Changed default + added discrepancy hint (~10 min)
- `resumeStrong: false` by default; opt-in via the slider menu
- When tag filters hide rows: amber "X hidden by tag filters" appears
  in the count line. No more silent gaps between header and list.

---

# Step 20 — Bot watchdog (job-intake → resume-builder)

User pointed at resume-builder's existing watchdog and asked us to also
become a watchdog so job-intake can detect + restart hangs.

## 20.1 — Read resume-builder's watchdog implementation
- `src/heartbeat.ts` — bot writes `~/bot/.heartbeat` (timestamp ms) every
  60s
- `scripts/watchdog.sh` — bash, run by launchd every 2 min: pgrep +
  cwd-match for bot PID, check heartbeat freshness (180s threshold),
  kill+nohup-npm-start on stale, notify admin via Telegram

## 20.2 — `src/web/bot_health.py` (~30 min)
Mirror of the bash watchdog in Python:
- `_read_heartbeat()` → (ts, age_seconds)
- `_find_bot_pids()` → pgrep + lsof cwd filter
- `restart_bot()` → SIGINT → 5s grace → SIGKILL → `nohup npm start &`
  with `start_new_session=True` for detachment
- `_last_restart_at` cooldown (300s) to avoid fighting launchd watchdog

## 20.3 — Endpoints + auto-restart loop (~20 min)
- `GET /api/bot/health` → `{state: ok|hung|down, ...}`, polled by UI
- `POST /api/bot/restart` (owner-only) → calls `restart_bot()`
- `_auto_watchdog_loop` — asyncio task at startup, polls every 60s,
  calls `restart_bot()` if hung+cooldown-elapsed (in `asyncio.to_thread`
  so it doesn't block the event loop)

## 20.4 — `BotHealthChip` UI (~25 min)
- Small chip in header: 🟢 Bot OK / 🟡 Bot hung / 🔴 Bot down
- Click → Radix popover with state, PIDs, heartbeat age, stale
  threshold, polling intervals (UI 30s / server 60s / cooldown 5m)
- Restart button: "Restart anyway" when ok, "Restart now" when degraded
- Owner-only; greyed for visitors with explanation

## 20.5 — Polling intervals exposed in UI (~5 min)
Per user request: added `auto_watchdog_interval_seconds` and
`restart_cooldown_seconds` to `/api/bot/health` response. UI shows them
in the popover under a "Polling" section, plus a live "Xs ago" ticker
for the last UI poll.

---

# Step 21 — Documentation + open-source

## 21.1 — Three docs (~60 min total)
- `docs/vision.md` — current state + 10x roadmap, 5 tiers
- `docs/how-to-journey.md` — operational guide (this file's sibling)
- `docs/tasks.md` — chronological build log (this file)
- Original v1 design preserved at
  `docs/job-intake-design-v1-original.md`

## 21.2 — git init + GitHub publish
- `git init` in `~/Documents/ready-to-apply/`
- Identity copied from resume-bot: `Deepesh Rathod / 60640528+deepesh-01@users.noreply.github.com`
- `gh auth switch -u deepesh-01` (was on work account `deepesh-zoca`)
- `gh repo create deepesh-01/job-intake --public --source . --push`
- Live at https://github.com/deepesh-01/job-intake

---

# Step 22 — LinkedIn (free, unauthenticated guest endpoint)

Original v1 design deferred LinkedIn to v2 ("needs paid proxy"). Found
the free path: `linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search`
returns HTML cards without auth. ~50 reqs before 429.

## 22.1 — `src/scout/sources/linkedin.py` (~30 min)
- Probe-confirmed shape: `<div class="base-card" data-entity-urn="urn:li:jobPosting:...">`
  with title / company / location / link / time inside.
- Multi-search: `board_id` is `kw=...;loc=...;tpr=...;wt=...;exp=...;details=...`
  (semicolon key=value).
- Universal ID: `linkedin:ALL:<urn_numeric>` so the same job surfaced via
  two different searches dedups naturally.
- Detail-fetch (`/jobs-guest/jobs/api/jobPosting/<id>`) is on by default
  for richer JD body → sharper tagging. On 429, loop bails gracefully
  with whatever it got.
- Cap 25 postings/board. Throttled 1 req/sec via shared host throttle.

## 22.2 — Wire up + 5 starter searches (~5 min)
- Registered in `runner._SOURCE_MODULES` and `verify_boards.py`
- Added `linkedin` to the <200-char-body bypass set (alongside
  `hn_hiring` and `workday`)
- Starter boards in `boards.yaml`:
  - `li_blr_senior_backend` — Senior Backend Engineer · Bengaluru · past week · mid-senior+
  - `li_blr_staff_swe` — Staff Software Engineer · Bengaluru
  - `li_blr_full_stack` — Senior Full-Stack Engineer · Bengaluru
  - `li_india_remote_ai` — AI Engineer · India · Remote · mid-senior+
  - `li_india_founding_eng` — Founding Engineer · India

## 22.3 — Live test
- 5 boards × 10 cards = 50 search-result postings
- 28 made it to Sheet after dedup (LinkedIn often surfaces the same role
  via multiple searches; universal urn ID dedups them in-batch)
- **Quality gain:** 39% senior+app_eng, **96% target_city**,
  **61% resume_strong** — far higher hit rate than any other source.
- Surfaced India brand-name companies otherwise hard to reach: Roku,
  Intuit, Thomson Reuters, Reliance, HDFC, Walmart Global Tech, Deltek,
  Epsilon, Mitratech.

## 22.4 — Risks + caveats (logged for future-self)
- **TOS-grey** — public job pages are scrapeable in practice but technically
  against LinkedIn TOS. Single-user, daily, ≤50 reqs is well within the
  zone they tolerate; commercial-scale scraping invites a cease-and-desist.
- **Markup drift** — `base-card` selector + URN format have been stable
  for years but a redesign could break parsing. Recovery: re-probe with
  the curl-then-selectolax pattern in step 22.1 and update selectors.
- **429 risk** — capped at 25 postings × 5 boards × ~2 reqs each = ~250
  reqs/scout. We sit just under threshold. If we expand, expect 429s
  and either reduce boards or add backoff / IP rotation.
- **Empty JD body if rate-limited** — detail-fetch loop bails on first
  429; affected rows fall back to title+company+location only (still
  searchable, less precise tag firing).

---

## Out of scope (current)

These are deliberate non-goals or deferred to v3 — see `vision.md` for
the prioritized roadmap:

- Multi-user (Sheet, Drive folder, chat_id are all single-tenant)
- Auto-apply on highest-confidence rows
- Cover letter / recruiter note generation
- Embeddings-based resume↔JD similarity
- Per-company memory of past tailoring decisions
- LinkedIn (paid proxy)
- Wellfound (anti-bot)
- Custom HTML scrapers for top India unicorns (Swiggy, Zomato, etc.)
- Cloudflare Access auth (instead of token-in-URL)
- Telegram bridge (`/queue` + `/process` commands in System A)
- Deploy to Fly.io (instead of laptop + tunnel)
- Multi-resume support
- "Quality budget" per board (auto-pause sources whose tailored rows
  consistently get rejected)
