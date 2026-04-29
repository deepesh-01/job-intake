---
project_name: 'ready-to-apply'
user_name: 'Deepeshz2'
date: '2026-04-29'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 47
optimized_for_llm: true
---

# Project Context for AI Agents

_Critical rules and patterns AI agents must follow when implementing code in `ready-to-apply` (a.k.a. job-intake / System B). Focus is on unobvious details that agents would otherwise miss. The `docs/` directory has the long-form rationale (`vision.md`, `decisions.md`, `how-to-journey.md`, `tasks.md`, `sources-roadmap.md`, `job-intake-design-v1-original.md`); this file is the dense rule-only version._

---

## Technology Stack & Versions

**Backend (Python)**
- Python **>=3.11** (per `pyproject.toml`); virtualenv lives at `.venv/` and is managed by **`uv`** (NOT pip)
- `gspread>=6.1.2` — Google Sheets API; sheet is the source of truth (ADR-001)
- `google-auth>=2.30.0`, `google-api-python-client>=2.140.0`, `google-auth-oauthlib>=1.2.0` — Drive uses **OAuth user delegation**, NOT service account (SA has 0 GB Drive quota → ADR workaround)
- `fastapi>=0.115.0` + `uvicorn[standard]>=0.32.0` — webapp API at `src/web/api.py`
- `httpx>=0.27.0` — all outbound HTTP (no `requests`)
- `selectolax>=0.3.21` — HTML parsing (faster than BeautifulSoup); needed for LinkedIn + Hasjob scrapers
- `markdownify>=0.12.1` — JD HTML → markdown for the detail drawer
- `structlog>=24.1.0` — structured logging; never use stdlib `logging` directly
- `pyyaml>=6.0.1` — `boards.yaml`, `exclude.yaml`, `tag_rules.yaml`
- `rapidfuzz>=3.9.0` — fuzzy company-norm dedup
- `python-dotenv>=1.0.1` — env loading; `.env` is gitignored

**Frontend (webapp/)**
- Node managed by **`fnm`** — launchd plists need explicit PATH (see ADR-022)
- `react@^18.3.1` + `react-dom@^18.3.1` (NOT 19 — TanStack Query 5.62 hasn't been re-tested)
- `vite@^6.0.7`, `typescript@^5.7.2` (strict mode + `noUnusedLocals` + `noUnusedParameters`)
- `tailwindcss@^3.4.17` (NOT 4) + `tailwindcss-animate`
- `framer-motion@^11.18.0` — swipe + drawer animations
- `vaul@^1.1.2` — bottom sheet / right drawer; do NOT replace with Radix Dialog (they handle gestures differently)
- `sonner@^1.7.4` — toasts
- `@tanstack/react-query@^5.62.0` — server state; **always pass `placeholderData: keepPreviousData`** for filtered lists (prevents wave/shake on filter change)
- `@radix-ui/react-{dialog,popover,slot,tooltip}` — primitives
- `lucide-react@^0.469.0` — icons

**Infra**
- macOS **launchd** runs both services (no Docker, no systemd):
  - `com.user.jobintake.scout.plist` — daily 9am IST cron
  - `com.user.jobintake.web.plist` — webapp on port **8090**
- **Cloudflare Tunnel** (`cloudflared`) exposes webapp at `takejob.deepesh-engg.in`
- Path alias: `@/*` → `src/*` (in `webapp/tsconfig.json`)

---

## Critical Implementation Rules

### Language-Specific Rules — Python

- **Run via `.venv/bin/python` with `PYTHONPATH=src`** — there's no `pip install -e .`. Example:
  `cd /Users/deepeshz2/Documents/ready-to-apply && PYTHONPATH=src .venv/bin/python -c "..."`
- **Never call `pip`** — install/upgrade goes through `uv`. The lockfile is `uv.lock`.
- All datetimes in the sheet/data layer are **UTC ISO 8601 with `Z` suffix** (`now_iso()` in `src/sheet/client.py`). Existing rows from before 2026-04-27 may be **date-only `YYYY-MM-DD`**; both shapes must be handled.
- `discovered_at` parsing: treat naive timestamps as UTC; date-only strings as midnight UTC of that calendar day.
- Type hints required on public function signatures. The codebase uses `str | None` (PEP 604), not `Optional[str]`.
- Use `from __future__ import annotations` in modules with forward references.
- Errors that should be retried bubble as `SourceError` (in `src/lib/posting.py`); the runner catches at the per-board boundary.

### Framework-Specific Rules — FastAPI / web

- **Sheet snapshot has a 30-second TTL cache** (`src/web/cache.py`). Any mutation **must call `_cache.invalidate()`** before returning.
- The sheet's data tab layout: row 0 is a doc/header note, **row 1 is the column header, data starts at row 2** (sheet row 3 in 1-indexed terms). Never hardcode row offsets — use `JOBS_COLUMNS` from `src/sheet/schema.py`.
- `discovered_at` lives at column index 1 (not 0 — column 0 is `id`).
- **Status enum** (`src/sheet/schema.py:VALID_STATUSES`): `new` / `tailor` / `ready` / `applied` / `rejected` / `skip` / `error`. Don't invent new values.
- **Tags** are a comma-separated string in the sheet cell, parsed into `list[str]` on read. Whitespace trimmed.
- `comp_low/high/comp_low_usd/comp_high_usd` are integers in the sheet; coerce empty cells to `None`, not `0`.
- Authentication: webapp is **read-only by default**. Mutations require `Authorization: Bearer <_WRITE_TOKEN>` (env: `WEB_WRITE_TOKEN`). The token enters via `?token=` URL param and is stashed in `localStorage`.

### Framework-Specific Rules — React / webapp

- All components are **function components with named exports** (no default exports except `App.tsx`).
- Use **`cn()` from `@/lib/utils`** to merge Tailwind classes — never raw template strings (`tailwind-merge` resolves conflicts).
- Date formatters live in `webapp/src/lib/utils.ts`:
  - `formatRelativeDate(iso)` — "today" / "3d ago"
  - `formatDateTime(iso)` — "Today, 11:32 AM" / "Apr 27, 11:32 AM"
  - `formatDateTimeFull(iso)` — full tooltip
  - **All three handle both date-only `YYYY-MM-DD` and full ISO** — never invent a time for a date-only input.
- Filter state is a single object (`Filters`); when adding a field, update **all four** of: `FilterBar`, `App.tsx` default, `JobsList` params, `CardStack` params.
- Server state goes through `@tanstack/react-query`. **No `useEffect` for data fetching.**
- Card view (`CardStack` + `SwipeCard`) is locked to `status=["new"]` regardless of the user's status pill selection — first-time triage only.

### Testing Rules

- pytest config: `pythonpath = ["src"]`, `testpaths = ["tests"]`. Run with `.venv/bin/pytest`.
- Test files are named `test_<module>.py`. Existing suites cover `dedup`, `exclude`, `extract`, `resume`, `schema`, `tag`.
- The webapp has **no automated tests** — verify via `npm run build` (must pass under `tsc -b` strict) and manual smoke against `http://127.0.0.1:8090`.
- Don't mock the Sheet in scrapers — use `pytest-mock` to stub the source HTTP layer instead, then assert against the parsed `Posting`.

### Code Quality & Style Rules

- **Conventional commits**: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`. Subject line ≤72 chars; body explains WHY.
- Co-author line on AI commits: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **No emojis** in code or docs unless the user explicitly asks.
- **Comments policy**: default to none. Add a comment only when the WHY is non-obvious (hidden constraint, workaround for a specific bug, surprising invariant). Never explain WHAT the code does.
- Don't add backwards-compatibility shims, feature flags, or `// removed` placeholders for deleted code.
- Indentation: 4-space Python, 2-space TypeScript/JSON/YAML.
- Long log lines stay structured: `log.info("event_name", key=value, ...)` — never f-strings.

### Development Workflow Rules

- Branches off `main`. Conventional names: `feat/<topic>`, `fix/<topic>`, `setup/<topic>`.
- **Never push to `main` without verifying `git status` is clean** and `git log` shows the intended commits.
- Push command in this environment: **`env -u GITHUB_TOKEN git push`** — `GITHUB_TOKEN` is/was a stale PAT in the parent shell env that overrides keychain creds; the `env -u` form bypasses it. (After 2026-04-27, secrets.sh no longer exports it; new shells are clean.)
- `gh` is logged in as `deepesh-01` (account that owns the repo) and `deepesh-zoca` (parked).
- Webapp build before deploy: `cd webapp && npm run build`. Must initialize fnm: `eval "$(/opt/homebrew/bin/fnm env --shell zsh)"`.
- After backend code changes: `launchctl kickstart -k gui/$(id -u)/com.user.jobintake.web` to reload uvicorn.
- After scout changes: `launchctl kickstart -k gui/$(id -u)/com.user.jobintake.scout` for an immediate run.
- Docs drift detection: `make docs-check` (claude-driven audit) and `make docs-sync` (apply).

### Critical Don't-Miss Rules

- **Sheet is the database.** Don't propose Postgres/SQLite "for scale" — ADR-001 is settled. The 30s cache + per-row file locks are the concurrency story.
- **Cloudflare edge caches HTML.** PWA-relevant URLs (`manifest.webmanifest`, `sw.js`, icon PNGs) need explicit `Cache-Control` headers + `?v=` cache-busters. The SPA fallback in `api.py` serves real files when they exist.
- **launchd PATH inheritance is broken** — fnm's per-shell shim doesn't reach launchd subprocesses. Both `com.user.jobintake.web.plist` AND `src/web/api.py` (defense-in-depth, ADR-022) must explicitly resolve node.
- **Drive uploads need OAuth user delegation** — service-account creds will fail with `storageQuotaExceeded`. The user OAuth credentials live in `data/oauth_token.json`.
- **Workday quirks**: `limit > 20` returns 400. Custom UAs get blocked — use the Chrome UA. List responses can be <200 chars; bypass the small-response check.
- **LinkedIn unauth endpoint is rate-limited** at ~50 reqs/run. Cap at `MAX_POSTINGS_PER_BOARD = 25`. The `_throttle()` helper in `src/scout/sources/_http.py` is mandatory.
- **Greenhouse double-encodes HTML** — `html.unescape()` before parsing with selectolax.
- **System A bridge** (`src/tailor_bridge.py`) calls `~/Documents/resume-builder/dist/cli-tailor.js` as a subprocess. **`cwd=system_a_path`** is required so dotenv can find System A's `.env`.
- **Never commit `.env`, `data/oauth_token.json`, or anything matching `*.json`** — the gitignore is broad on JSON precisely because of stray credential files.
- **Memory of session token**: a leaked PAT (`ghp_B0Wp7w...`) was committed historically and is in old `.claude/projects/.../*.jsonl` transcripts. **Rotate on https://github.com/settings/tokens** if not done.
- The webapp's `webapp/dist/` is gitignored — the FastAPI SPA fallback reads from disk; a fresh clone needs `npm run build` before the webapp serves anything.
- The `_bmad-output/` directory holds BMAD workflow artifacts. The two subdirs (`planning-artifacts`, `implementation-artifacts`) are part of the contract — don't rename.

### Sibling Project Awareness

- **System A** = `~/Documents/resume-builder/` (separate repo, separate Telegram bot, public on GitHub as `deepesh-01/resume-bot`). Do not modify it from this repo. Cross-system contract is the `cli-tailor.js` CLI signature.
- The watchdog in `src/web/bot_health.py` watches System A's heartbeat at `~/bot/.heartbeat` (NOT this repo). Restarts log to `~/bot/logs/restarts.log`.

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code change in this repo.
- Cross-reference `docs/decisions.md` (ADRs) before proposing architectural changes — many "obvious improvements" have been considered and rejected with reasoning.
- When a rule here conflicts with a request, surface the conflict and ask — don't silently override.
- Keep new rules narrow and actionable; if it's general advice, it belongs in `CLAUDE.md` or training data, not here.

**For Humans:**
- Update when the stack changes (Python/Node major bump, library swap, infra move).
- Remove rules that become obvious or stale.
- The `docs/` directory is the long-form companion — keep this file dense and scannable.

Last Updated: 2026-04-29
