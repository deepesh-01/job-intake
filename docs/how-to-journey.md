# Job Intake — Operating Guide

*Status: shipped, v2 (sheet + scout + processor + webapp + watchdog) · Updated 2026-04-27*

A daily job-intake pipeline. Scout pulls fresh postings from 30+ boards into a
Google Sheet, scores them against your resume, and drops them in a webapp where
you triage, tailor, and apply — without ever leaving the URL on your phone.

For the *vision* (current state + 10x roadmap) see [`vision.md`](./vision.md).
For the *original v1 design* (frozen for history) see
[`job-intake-design-v1-original.md`](./job-intake-design-v1-original.md).
For the *chronological build log* see [`tasks.md`](./tasks.md).

---

## What you actually have

**Three running pieces:**

1. **Scout** (Python, `python -m scout.runner`) — fetches boards, dedups,
   tags, appends to Sheet. Runs daily via launchd at 9am.
2. **API + webapp** (FastAPI on `:8090`, served via Cloudflare Tunnel at
   https://takejob.deepesh-engg.in) — read/write proxy over the Sheet plus the
   React frontend, both on the same port.
3. **Processor** (Python, `python -m processor.runner`) — invoked from the
   webapp's "Process queue" button OR directly from terminal. Sweeps
   `status=tailor` rows, calls into System A's `cli-tailor.js`, writes Drive
   URL back.

**Three persistence layers:**

1. **Google Sheet** (`SHEET_ID` in `.env`) — three tabs: `Jobs` (the data),
   `Boards` (source health), `Log` (activity). Schema version in `Jobs!A1`.
2. **Local data dir** (`~/Documents/ready-to-apply/data/`):
   - `jds/<id>.md` — every JD body ever scraped
   - `tailored/<id>.pdf` — local backup of every tailored PDF
   - `seen_ids.sqlite` — dedup cache mirror of Sheet ids
   - `locks/<id>.lock` — per-row processor mutex
3. **Google Drive folder** (`GOOGLE_DRIVE_FOLDER_ID`) — owner's Drive,
   shared with the SA. Tailored PDFs live here as link-shareable files.

**External dependencies:**

- **System A** (`~/Documents/resume-builder/`) — TypeScript Telegram bot
  exposing `dist/cli-tailor.js`. Owns your `~/bot/users/<chat_id>/base_resume.md`.
  We invoke it via subprocess.
- **Cloudflare Tunnel** (config: `~/Documents/welog/cloudflare/tunnel-config.yml`,
  service: `com.welog.cloudflared`) — routes `takejob.deepesh-engg.in` to
  `localhost:8090`.
- **Claude Code CLI** (`claude --version`) — System A spawns it for tailoring,
  critic, refinement.

---

## Quick start (you're already onboarded)

Daily flow:

1. **9am** — Scout runs automatically. New rows appear in Sheet with `status=new`.
2. **Open https://takejob.deepesh-engg.in** (owner URL with token if first time
   on a new device).
3. Pick a status filter (default: New). Browse cards. Click into a card to see
   the full JD body, tag reasoning, comp, and the company's apply link.
4. **Tap "Tailor"** on any row you want a tailored resume for. Status flips to
   `tailor`; the queue badge on the bottom-right FAB increments.
5. **Tap "Process queue · N"** when you've queued everything you want.
6. ~5 min later, status flips to `ready`, a Drive URL appears in the row
   (shows up as a clickable "Tailored resume" pill in the detail drawer).
7. Click the Drive URL → review the PDF → apply on the company's site → mark
   `applied`.

---

## Prerequisites (one-time)

### System

- macOS or Linux (`pgrep`, `lsof`, `pandoc`, `typst`, `node`, `claude` in PATH)
- Python 3.11+, `uv` for package mgmt
- Node 20+ (for System A's `cli-tailor.js`)
- `pandoc` + `typst` (for PDF rendering — owned by System A)
- Cloudflare account with a tunnel already configured (or a willingness to
  set one up)

### Google Cloud + Drive

- A GCP project with **Google Sheets API** + **Google Drive API** enabled
- A **Service Account** with JSON key downloaded → `~/secrets/job-intake-sa.json`
- An **OAuth Client ID (Desktop app type)** for Drive uploads → JSON →
  `~/secrets/job-intake-oauth.json`
- A **Google Sheet** shared with the SA email as Editor
- A **Drive folder** ("Job Intake Tailored Resumes" or whatever) shared with
  your own Google account; folder ID in `.env`

### System A (resume-builder)

- Already onboarded (you have `~/bot/users/<chat_id>/base_resume.md`)
- `cd ~/Documents/resume-builder && npm run build` — produces `dist/cli-tailor.js`

---

## Setup

### Layout

- **Code:** `~/Documents/ready-to-apply/`
- **Data:** `~/Documents/ready-to-apply/data/` (created on first scout run)
- **Secrets:** `~/secrets/` (chmod 600 for both JSONs)
- **Docs:** `~/Documents/ready-to-apply/docs/` (this file + vision + tasks +
  archived v1 design)
- **System A workspace:** `~/bot/` (read-only from our side)

### `.env`

```ini
# Sheet + service-account
GOOGLE_SHEETS_CREDS_PATH=/Users/deepeshz2/secrets/job-intake-sa.json
SHEET_ID=<your sheet id>

# Project paths
DATA_DIR=/Users/deepeshz2/Documents/ready-to-apply/data
TAG_RULES_PATH=/Users/deepeshz2/Documents/ready-to-apply/tag_rules.yaml
BOARDS_PATH=/Users/deepeshz2/Documents/ready-to-apply/boards.yaml
EXCLUDE_PATH=/Users/deepeshz2/Documents/ready-to-apply/exclude.yaml

# System A bridge
SYSTEM_A_PATH=/Users/deepeshz2/Documents/resume-builder
SYSTEM_A_BASE_RESUME=/Users/deepeshz2/bot/users/<chat_id>/base_resume.md
SYSTEM_A_USER_CHAT_ID=<your chat id>
TAILOR_DRY_RUN=false              # true = stub PDF, no Claude cost

# Drive (OAuth user delegation — fixes SA quota issue)
GOOGLE_DRIVE_FOLDER_ID=<long folder id>
GOOGLE_OAUTH_CLIENT_PATH=/Users/deepeshz2/secrets/job-intake-oauth.json
GOOGLE_OAUTH_TOKEN_PATH=/Users/deepeshz2/secrets/job-intake-oauth-token.json

# Webapp owner-write token (omit for fully open mode)
WRITE_TOKEN=<random 24-char>

LOG_LEVEL=INFO
```

### Boot

```bash
cd ~/Documents/ready-to-apply
uv sync --extra dev                 # install Python deps
uv run python scripts/bootstrap_sheet.py    # creates the three tabs
uv run python scripts/auth_drive.py         # one-time OAuth dance
uv run python scripts/verify_boards.py      # ping every board, find dead slugs
uv run python -m scout.runner               # first scout run
cd webapp && npm install && npm run build   # build static webapp
```

### Persistence

Two launchd plists run things automatically:

```bash
# Daily scout at 9am
cp com.user.jobintake.scout.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.jobintake.scout.plist

# Always-on webapp (auto-restarts on crash, runs on login)
cp com.user.jobintake.web.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.jobintake.web.plist
```

Cloudflare tunnel: hostname `takejob.deepesh-engg.in` is added as an ingress
in your existing welog tunnel (`~/Documents/welog/cloudflare/tunnel-config.yml`).
Reload after edits with `launchctl kickstart -k gui/$(id -u)/com.welog.cloudflared`.

---

## The data model

### Jobs tab — 26 columns at v2

```
id, discovered_at, posted_at, company, role, location,
comp_string, comp_currency, comp_low, comp_high, comp_low_usd, comp_high_usd,
link, jd_snippet, jd_full_path, tags, tag_reasons, status,
resume_path, last_change, tailored_at, applied_at, followup_due_at, response_at,
notes, resume_match
```

`Jobs!A1` holds `schema_version: 2`. `migrate.py` knows v1 → v2 (added
`resume_match` column).

### Status state machine

```
new → tailor       (you mark from the webapp or by editing the Sheet cell)
tailor → ready     (Processor produced the PDF + uploaded to Drive)
ready → applied    (you applied; UI auto-fills applied_at to today)
new → skip         (not interested; hides from default view)
new → rejected     (got a rejection; archived but kept)
applied → followup_due_at set  (Scout's daily sweep, after 7d no response)
applied → response_at set      (you got a recruiter reply; clears followup)
ready → error      (cli-tailor failed; check last_change for reason)
```

`rejected` works from any state — even after `ready` or `applied`.

### Tag families

| Family | Tags | Source |
|---|---|---|
| Comp | `comp_ok`, `comp_below`, `comp_unknown` | currency-aware regex on JD body |
| Stack | `stack_typescript`, `stack_python`, `stack_match` | section-anchored keyword |
| Role shape | `application_eng`, `wrong_discipline`, `seniority_match`, `seniority_junior` | title-only regex |
| Stage | `early_stage`, `growth_stage`, `late_stage` | JD body keyword |
| Culture | `ai_native`, `culture_chill_signal`, `grind_signal`, `enjoy_eligible` | JD body keyword |
| Location | `remote_ok`, `non_us_only`, `target_city` | location field |
| Other | `posted_recent`, `has_recruiter_email`, `followup_due` | computed |
| Resume | `resume_strong` | overlap count ≥3 with `base_resume.md` skills |

`tag_reasons` cell carries the *why*: `target_city=location:bengaluru;
resume_strong=overlap:5:typescript,react,aws,postgres,microservices`.

---

## Full command reference

### Scripts

| Command | What |
|---|---|
| `uv run python -m scout.runner` | Daily fetch + dedup + tag + write rows + follow-up sweep |
| `uv run python -m processor.runner` | Sweep `status=tailor` rows, invoke System A, upload PDFs to Drive |
| `uv run python -m web.server` | Boot the FastAPI webapp on `:8090` (or use the launchd plist) |
| `uv run python scripts/bootstrap_sheet.py` | Create the three tabs on a fresh Sheet |
| `uv run python scripts/verify_boards.py` | Ping every board in `boards.yaml`, report 404s |
| `uv run python scripts/reload_boards.py` | Refresh the Boards tab from `boards.yaml` (without scout sweep) |
| `uv run python scripts/add_views.py` | Refresh saved Filter Views on the Jobs tab |
| `uv run python scripts/auth_drive.py` | One-time browser OAuth for Drive uploads |

### API endpoints (port 8090)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | open | `{ok, read_only}` — UI checks `read_only` to gate writes |
| GET | `/api/jobs` | open | List with filters + sort + pagination |
| GET | `/api/jobs/{id}` | open | Single job + JD markdown body |
| PATCH | `/api/jobs/{id}` | **owner** | Mutate `status`, `applied_at`, `response_at`, `notes` |
| GET | `/api/stats` | open | Counts by status + top tags + today_added |
| GET | `/api/process/status` | open | `{is_running, current_company, current_role, queue_remaining}` |
| POST | `/api/process` | **owner** | Spawn `python -m processor.runner` (long-running) |
| GET | `/api/bot/health` | open | `{state, is_alive, is_hung, pids, heartbeat_age_seconds, ...}` |
| POST | `/api/bot/restart` | **owner** | Kill resume-builder bot PIDs + `npm start` |

**Owner auth:** if `WRITE_TOKEN` is set, all owner endpoints require either
`Authorization: Bearer <token>` header or `?token=<token>` query param.
The webapp stores the token in localStorage after first visit with `?token=…`.

### Webapp filter views

Pre-saved views (created by `add_views.py` and synced to the Sheet itself —
also accessible from Sheets UI funnel icon → Filter views):

1. Browse senior IC (sort by match)
2. Top picks (resume-strong senior IC)
3. Remote-friendly senior IC
4. Target city (Bangalore / Hyderabad / India)
5. India-relevant (remote OR target city)
6. Comp-disclosed senior IC
7. AI-native or chill (enjoy_eligible senior IC)
8. New rows only (status=new, daily triage)

---

## Daily flows

### Morning ritual (5 min)

1. Open https://takejob.deepesh-engg.in (your owner URL is bookmarked).
2. Status filter defaults to "New". Sort: best match.
3. Skim the cards. Tap one → drawer opens with full JD.
4. Decide:
   - **Tap Tailor** → status=tailor (queue badge updates instantly)
   - **Tap Reject** → status=rejected (hidden from new view next refresh)
   - **Close drawer** → leave as-is for tomorrow
5. When done queueing, tap **"Process queue · N"** at bottom-right.
6. Toast appears: *"Tailoring 5 jobs… ~5 min per row"*. Button changes
   to *"Processing 5 jobs · Now: Stripe · Senior Backend Engineer"*.
7. ~25 minutes later, all rows are `ready` with Drive PDF URLs.

### Apply ritual (per row, ~3 min)

1. In webapp, switch filter to "Ready" status.
2. Click each ready row → drawer opens.
3. Click the green **"Tailored resume"** pill → opens Drive PDF in new tab.
4. Click the **"View original"** pill → opens the company's apply page.
5. Apply on the company's site, attach the Drive PDF.
6. Back in webapp drawer: tap **Applied**. Status flips, applied_at filled.

### Follow-up nudges (automatic)

Each Scout run sweeps `status=applied` rows. If `applied_at + 7d <= today`
and `response_at` is empty, sets `followup_due_at = today`. Filter the
sheet by `followup_due_at != ""` to see the nudge list.

### Adding a new company / board

1. Edit `boards.yaml` — add `{ slug, source_type, board_id, enabled: true }`.
2. `uv run python scripts/verify_boards.py` — confirm the slug works.
3. Next 9am scout pull will pick it up. Or trigger manually:
   `uv run python -m scout.runner`.

### Tuning tag rules

`tag_rules.yaml` is reloaded each scout run. No code change needed:

- **Lower comp floor** (too few `comp_ok`) → `comp.inr_floor` or `comp.usd_floor`
- **Add a stack keyword** → `stacks.<name>.keywords`
- **Catch a grindy phrase** → `culture.grind_signals`
- **Loosen wrong_discipline** (real eng roles getting filtered) → `application_eng.exclude`

### Bot is hung (no Telegram replies)

The webapp's bot health chip in the header turns yellow/red. Click it →
popover shows heartbeat age. Tap **Restart now**. Or it'll auto-restart
within 60s anyway.

If both the webapp's watchdog AND the launchd watchdog are down (or you
don't have one running), manually:

```bash
PID=$(pgrep -f "node dist/index.js" | xargs -I{} sh -c '[ "$(lsof -a -p {} -d cwd -Fn 2>/dev/null | awk "/^n/ {print substr(\$0, 2)}" | head -1)" = "/Users/deepeshz2/Documents/resume-builder" ] && echo {}')
kill -INT $PID && sleep 5
cd ~/Documents/resume-builder && nohup npm start > ~/bot/logs/manual-start.log 2>&1 &
```

---

## Webapp (the UI in detail)

### Owner vs viewer

- **Public URL** → read-only mode. Banner at top: *"Preview mode — actions
  are disabled."* All filtering / drilling works; mutation buttons are
  visible but greyed out.
- **Owner URL** (with `?token=…`) → token saves to localStorage; banner
  doesn't appear; all action buttons live.

To revoke owner access (token leak): rotate `WRITE_TOKEN` in `.env`,
`launchctl kickstart -k gui/$(id -u)/com.user.jobintake.web`. Old tokens
become 403.

### Animations + interaction model

- **Card list:** instant render on filter change (no per-card animation
  to avoid the wave/shake — `keepPreviousData` so the old list stays
  visible during refetch).
- **Detail drawer:** vaul-style; bottom sheet on mobile, side panel on
  desktop. Spring transition.
- **Status changes:** optimistic + sonner toast confirms.
- **Process queue button:** server-side detected via lock files so all
  tabs / devices see the same running state. Polls 5s while running,
  30s when idle.
- **Bot health chip:** 30s UI poll, 60s server-side watchdog tick.

---

## Operational notes

### Logs

- Scout (when run via launchd): `~/Documents/ready-to-apply/data/launchd.{out,err}.log`
- Webapp: `~/Documents/ready-to-apply/data/web.{out,err}.log`
- Watchdog spawning resume-builder: `~/bot/logs/watchdog-jobintake.log`
- Sheet's `Log` tab — start/finish/error events for scout + processor

### Tail commands

```bash
tail -f ~/Documents/ready-to-apply/data/web.err.log     # API + webapp
tail -f ~/Documents/ready-to-apply/data/launchd.err.log # scout
launchctl list | grep jobintake                          # service status
```

### Restart commands

```bash
# webapp
launchctl kickstart -k gui/$(id -u)/com.user.jobintake.web

# scout (forces an off-schedule run)
launchctl kickstart -k gui/$(id -u)/com.user.jobintake.scout

# cloudflared tunnel (after editing config)
launchctl kickstart -k gui/$(id -u)/com.welog.cloudflared
```

### Cost expectations

- **Scout:** $0 (free APIs only)
- **Tailor:** ~$0.10–0.40 per row (Claude API: tailor + critic + optional
  refinement)
- **Daily volume:** ~50–150 new rows from scout, you tailor 0–10 of them
  → $0–$4/day if you go heavy

### Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Scout adds 0 new rows | Slug rot — boards changed ATS | `verify_boards.py`, edit `boards.yaml` |
| Tailor exits with `system_a_cli_missing` | resume-builder not built | `cd ~/Documents/resume-builder && npm run build` |
| Tailor returns "claude_auth" | claude CLI session expired | `claude login` |
| Drive upload `storageQuotaExceeded` | OAuth token missing/expired | `uv run python scripts/auth_drive.py` |
| Webapp shows `502` | API down | `launchctl kickstart -k gui/$(id -u)/com.user.jobintake.web` |
| Bot health chip says "hung" or "down" | Resume bot stuck | Auto-restart fires within 60s; manual via the chip's button |
| Sheet PATCH 403 from public URL | No write token | Open the owner URL once with `?token=…` |
| `comp_ok` rate way down | Floor changes / parser regression | Sample 10 JDs, eyeball the `comp_string` cell, tune `inr_floor` / `usd_floor` |
| Same job twice in Sheet | Fuzzy-dedup miss | Edit distance threshold is 3 in `dedup.py:fuzzy_collision` — bump to 5 |
| Schema mismatch on startup | Bumped `SCHEMA_VERSION` without migrating | `uv run python -c "from sheet.migrate import migrate; from sheet.client import SheetClient; from lib.config import load_env; e=load_env(); migrate(SheetClient(e.creds_path, e.sheet_id))"` |

---

## Don't drift this doc

When you add a feature, update:

1. **`how-to-journey.md`** (this file) — operational guide; add to relevant
   section. Endpoints in command reference. Failure modes if applicable.
2. **`tasks.md`** — append a new build step (don't edit prior steps —
   they're historical). Same shape as resume-builder's `tasks.md`.
3. **`vision.md`** — only if the change shifts the system's *direction*
   (new tier-N capability). Most changes don't.
