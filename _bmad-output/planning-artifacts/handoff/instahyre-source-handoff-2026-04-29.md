---
purpose: Hand off Instahyre source build to a fresh Claude session
written: 2026-04-29
status: ready-to-execute
prerequisite: user provides INSTAHYRE_COOKIE value at session start
---

# Handoff — Build the Instahyre source (fresh-session brief)

## Goal

Build `src/scout/sources/instahyre.py` — a new India-focused job source for the daily scout cron. **Cookie-auth pattern.** Same architecture as `linkedin_auth.py` and `naukri.py` (both already shipped). User will paste `INSTAHYRE_COOKIE` value at session start; you build the module + boards in parallel.

## Read these first (3 min)

1. `/Users/deepeshz2/Documents/ready-to-apply/CLAUDE.md` — project pointer doc
2. `/Users/deepeshz2/Documents/ready-to-apply/_bmad-output/project-context.md` — 47-rule LLM cheat sheet (footguns, conventions, exact tech versions)
3. `/Users/deepeshz2/Documents/ready-to-apply/src/scout/sources/naukri.py` — closest analog (cookie-auth, JSON API, India-focused). Read the docstring carefully for the bypass story.
4. `/Users/deepeshz2/Documents/ready-to-apply/src/scout/sources/linkedin_auth.py` — second analog (cookie-auth, HTML parsing).
5. `/Users/deepeshz2/Documents/ready-to-apply/_bmad-output/planning-artifacts/research/technical-naukri-akamai-bypass-research-2026-04-29.md` — the research that found the okhttp UA Akamai bypass. Apply the same "try mobile-app UA first" instinct to Instahyre.

## Project state at end of prior session (2026-04-29)

- **81 enabled boards** across 11 source types. Daily scout cron at 9am IST.
- Active queue: **113 status=new** (50:50 L2:L3 blend, India-focused, ₹40L+ comp floor, 7-per-company cap).
- **Tests: 92/92 pass.**
- Cookies in `.env` (gitignored): `LI_AT_COOKIE`, `NAUKRI_COOKIE`. **Add `INSTAHYRE_COOKIE` here.**
- Filter pipeline (uniform across sources):
  1. Exclude rules: company / company_pattern / role / JD-keyword (~250 patterns)
  2. Location filter: drops non-India non-remote-friendly rows (`src/scout/location.py`)
  3. Per-company intra-batch cap: top-7 by `resume_match`
  4. Auto-archive at end of run

**Important:** New sources should be added to the `INDIA_FOCUSED_SOURCES` set in `src/scout/location.py` if their query parameters guarantee India-only results. Otherwise they'll be subject to the location filter (which is fine for global aggregators).

## Recon checklist (do this FIRST in the new session)

Before writing code, probe Instahyre to find the working request shape:

```bash
# Test 1: basic auth probe with user's cookie + browser UA
curl -sS -m 15 -A "Mozilla/5.0 ... Chrome/146.0.0.0 Safari/537.36" \
  -b "lat=$INSTAHYRE_COOKIE" \
  "https://www.instahyre.com/api/v1/job/?keywords=software+engineer&location=bengaluru" | head -c 1000

# Test 2: same but with okhttp UA (Akamai bypass pattern that worked for Naukri)
curl -sS -m 15 -A "okhttp/4.12.0" \
  -b "lat=$INSTAHYRE_COOKIE" \
  "https://www.instahyre.com/api/v1/job/?keywords=software+engineer&location=bengaluru" | head -c 1000

# Test 3: HTML scrape fallback if API returns 403/406
curl -sS -m 15 -A "Mozilla/5.0 ..." -b "lat=$INSTAHYRE_COOKIE" \
  "https://www.instahyre.com/jobs/search?keywords=software+engineer&location=bengaluru" | head -c 2000
```

The **exact cookie name** to ask the user for: log in to instahyre.com → DevTools → Application → Cookies → instahyre.com → copy `lat` value. (Alternative names if `lat` doesn't exist: `sessionid`, `csrftoken`, `instahyre_session`.) Confirm the actual cookie name during recon before writing the source.

## What to build

### 1. `src/scout/sources/instahyre.py`

Mirror `naukri.py`'s structure:

- Module docstring documents the auth setup, cookie expiry behavior, board_id format
- `SOURCE_TYPE = "instahyre"`
- `_OKHTTP_UA = "okhttp/X.Y.Z"` (try this first; if it 403s with the cookie, fall back to Chrome UA)
- `_build_cookies()` — reads `INSTAHYRE_COOKIE` env var, raises `SourceError` with actionable message if missing
- `fetch(board_id)` — parses board_id (semicolon-separated key=value), GETs the search endpoint, parses response, returns `list[Posting]`
- Handle `401/403` → cookie expired (clear error message)
- Handle `429` → return None / partial yield
- Use `_throttle()` from `scout.sources._http`
- `Posting` has fields: `id`, `company`, `role`, `location`, `comp_string`, `posted_at`, `link`, `jd_html`, `source_type`, `board_id`. id format: `instahyre:ALL:<their_job_id>`.

### 2. Register in two places

- `src/scout/runner.py::_SOURCE_MODULES` — add `"instahyre": "scout.sources.instahyre"`
- `scripts/verify_boards.py::_SOURCE_MODULES` — same

### 3. Add to `INDIA_FOCUSED_SOURCES`

- `src/scout/location.py` — add `"instahyre"` to the set so the location filter doesn't try to filter India-only results.

### 4. Update `runner._enrich_one` JD-length skip exemption

- `src/scout/runner.py::_enrich_one` has a list of source types exempted from the 200-char JD minimum (because their JD bodies are short by design). Add `"instahyre"` to it. Search for the line containing `{"hn_hiring", "workday", "linkedin", "linkedin_auth", "naukri"}` and add `"instahyre"`.

### 5. `boards.yaml` entries

Add 6-8 board entries covering common search permutations. Mirror the naukri/linkedin_auth pattern. **Default `enabled: false`** until smoke test passes. Suggested initial set:

```yaml
# Instahyre — premium India tech (cookie-auth, added 2026-04-XX)
- { slug: instahyre_blr_senior_backend, source_type: instahyre, board_id: "kw=senior backend engineer;loc=bengaluru;exp=4-9", enabled: false }
- { slug: instahyre_blr_staff_swe,      source_type: instahyre, board_id: "kw=staff software engineer;loc=bengaluru;exp=5-12", enabled: false }
- { slug: instahyre_blr_full_stack,     source_type: instahyre, board_id: "kw=senior full stack engineer;loc=bengaluru;exp=4-9", enabled: false }
- { slug: instahyre_blr_eng_manager,    source_type: instahyre, board_id: "kw=engineering manager;loc=bengaluru;exp=6-15", enabled: false }
- { slug: instahyre_india_remote,       source_type: instahyre, board_id: "kw=senior software engineer;loc=remote;exp=4-9", enabled: false }
- { slug: instahyre_india_ai,           source_type: instahyre, board_id: "kw=ai engineer;loc=india;exp=3-9", enabled: false }
- { slug: instahyre_india_founding,     source_type: instahyre, board_id: "kw=founding engineer;loc=india;exp=3-9", enabled: false }
```

### 6. Tests

Add `tests/test_instahyre.py` mirroring `tests/test_location.py`'s structure. At minimum:
- Test `_parse_board_id` parses semicolon-separated keys correctly
- Test `_build_cookies` raises `SourceError` when `INSTAHYRE_COOKIE` env unset
- Mock-test the `fetch()` parser against a sample API response (use `pytest-mock`)

Run `uv run pytest tests/ --tb=short` — should be **93+/93+ passing**.

## Setup steps for user

The user will add `INSTAHYRE_COOKIE=<value>` to `.env`. The launchd plist already has `WorkingDirectory` pointing at the project root, so `python-dotenv` picks it up automatically — no plist changes needed. Same flow as `LI_AT_COOKIE` / `NAUKRI_COOKIE`.

## Smoke-test the source end-to-end

```bash
PYTHONPATH=src .venv/bin/python -c "
import sys; sys.path.insert(0, 'src')
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('.env'))
from scout.sources.instahyre import fetch
ps = fetch('kw=senior backend engineer;loc=bengaluru;exp=4-9')
print(f'count: {len(ps)}')
for p in ps[:5]: print(f'  {p.company[:25]:25}  {p.role[:55]:55}  {p.location or \"-\"}')
"
```

Expected: 10–30 senior IC software roles in Bengaluru, all India-located, no obvious non-software bloat.

## When working — the "verify, then enable" rule

1. Get the smoke test returning real data first.
2. Eyeball ~20 results to confirm they're software / senior / India-located.
3. Flip 2-3 boards to `enabled: true` (NOT all 7) — controlled rollout.
4. Trigger scout: `launchctl kickstart -k gui/$(id -u)/com.user.jobintake.scout`
5. Tail logs at `data/launchd.err.log`, look for `event='board_fetched' slug='instahyre_*' count=N`.
6. Check the queue via `curl http://127.0.0.1:8090/api/jobs?status=new&limit=20` — verify Instahyre rows look right.
7. If clean: enable the rest. If issues: tune `boards.yaml` queries first.

## Things known to bite (footguns from prior sessions)

1. **gspread `worksheet.update()` deprecation** — use `ws.update(values=..., range_name=..., value_input_option=...)` (kwargs). The pattern in `src/scout/archive.py` is correct.
2. **Comp parser raw-rupee bug** (fixed 2026-04-29) — if Instahyre returns INR comp like `"INR 1,500,000-2,500,000"` (no L/lakh suffix), the existing `_INR_RAW_RANGE_RE` in `src/scout/extract.py` handles it. No new work.
3. **Akamai-class WAFs** — if Instahyre 406s with browser UA, try okhttp UA (Naukri's bypass pattern). If both fail, document and defer.
4. **Cookie expiry** — Instahyre cookies likely expire in days/weeks. Source must raise `SourceError` with actionable message ("refresh INSTAHYRE_COOKIE — log in again") when 401/403.
5. **Per-company intra-batch cap** — already enforced at runner level (`_PER_COMPANY_INTRA_BATCH_CAP = 7`). Don't worry about over-fetching from a single company.
6. **Naukri.com is NOT a model for Instahyre's API shape** — don't assume `salaryDetail` / `placeholders` structure. Recon and adapt.

## Documentation to update at end

- `docs/sources-roadmap.md` — update the source-status table (move Instahyre from "needs cookie auth" to "shipped"). Add a paragraph in §0 documenting what was built.
- `_bmad-output/planning-artifacts/research/` — write a brief research artifact if any non-obvious technique was needed (e.g., if okhttp UA bypass repeated, or different bypass found).

## Estimated session time

- Recon (probe + identify exact cookie name + endpoint): 15–20 min
- Build source module: 30–45 min
- Tests: 15 min
- boards.yaml + register + India_focused set + JD-skip exemption: 10 min
- Smoke test + fix: 15 min
- Enable boards + trigger scout + verify: 15 min
- Update docs: 10 min

**Total: ~2 hr** including buffer.

## What good looks like

- 6–8 Instahyre boards in `boards.yaml`, mostly enabled
- ~30–80 fresh India-focused premium-tech rows surfacing in the next scout run
- Tests 93+/93+
- Source-status table in `docs/sources-roadmap.md` updated
- User can swipe through the new rows in the morning queue without seeing non-software / non-India / low-comp bloat

## If Instahyre's API turns out to be 403/captcha-walled

Apply the same diagnostic ladder as Naukri:
1. Try okhttp/Chrome/older-Chrome UAs with cookie
2. Try `curl_cffi` with TLS fingerprint impersonation (already installed in venv)
3. Inspect cookie jar for additional required cookies (CSRF, session, anti-bot)
4. If all fail, document the WAF stack in a research artifact and either build a Playwright-based fallback (separate session) or defer to next India source (Cutshort/iimjobs).

The Naukri research artifact is the template for that diagnostic write-up.
