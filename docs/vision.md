# Job Intake — Product Vision (v2, post-MVP)

*Owner: Deepesh · Status: shipped + extending · Last updated: 2026-04-27*

> Sibling docs:
> - [`how-to-journey.md`](./how-to-journey.md) — operational guide
> - [`tasks.md`](./tasks.md) — chronological build log
> - [`decisions.md`](./decisions.md) — ADR log of non-obvious choices
> - [`job-intake-design-v1-original.md`](./job-intake-design-v1-original.md) — frozen v1 design

---

## 1. What it is now

A **job-application command center**. The Sheet is still the source of truth,
but everything around it has been hardened into three layered pieces:

```
   Scout (cron)        Processor (on-demand)        Webapp (always-on)
       │                       │                         │
       ▼                       ▼                         ▼
 ┌──────────────────────────────────────────────────────────────┐
 │     Google Sheet · 26 cols · v2 schema · live editable      │
 └──────────────────────────────────────────────────────────────┘
       ▲                       ▲                         ▲
       │ scrape 33 boards      │ subprocess → System A    │ FastAPI cache
       │ tag + match resume    │ (cli-tailor.js)          │ + React UI
       │ append rows           │ upload PDF to Drive      │ + bot watchdog
       │                       │ patch row → status=ready │
```

### What you can do today

1. Daily at 9am, Scout fetches ~1500 fresh postings from Greenhouse / Lever /
   Ashby / Workday / RemoteOK / Remotive / Arbeitnow / Hasjob / HN-Hiring,
   tags them with currency-aware comp + your resume's skills, and writes
   ~900 deduped rows into the Sheet.
2. Open https://takejob.deepesh-engg.in (or share with friends — read-only).
3. Filter / sort / drill into any role's full JD body, all metadata, all
   tag reasoning.
4. Mark interesting rows `tailor` → click **Process queue** → real Claude
   tailoring + critic + refinement + Typst PDF, uploaded to Drive,
   clickable URL appears in the Sheet within ~5 min per row.
5. Apply, mark `applied`. Follow-up sweep flags rows you haven't heard
   back from in 7 days.

### What changed from v1

| v1 design | v2 reality |
|---|---|
| 20 boards (mostly hand-curated companies) | **33 enabled boards** across 8 source types — including 4 Workday tenants (Adobe, NVIDIA, Walmart, Autodesk) and 4 free aggregators |
| `comp_unknown` for ~100% rows | Currency-aware parser handling INR (LPA/lakh/crore), USD/GBP/EUR/CAD/SGD/AUD with em-dash separators, double-encoded HTML, Greenhouse `pay-range` markup |
| No resume awareness | `resume_match` column scores 0.0–1.0 from skill overlap with `~/bot/users/<chat_id>/base_resume.md`. `resume_strong` tag fires on count ≥ 3 |
| Local PDFs only | Drive uploads via OAuth user-delegation; Sheet `resume_path` is a clickable URL |
| Sheet-only review | React webapp with filter views, virtualized list, vaul-style detail drawer, sonner toasts, framer-motion transitions, mobile-first |
| Local-only | Cloudflare Tunnel → public `takejob.deepesh-engg.in` with token-gated mutations (read-only by default) |
| Manual processor invocation | `POST /api/process` from UI; live polling of `/api/process/status` shows current job + remaining queue |
| No bot resilience | Built-in watchdog mirrors resume-builder's heartbeat-based restart; 60s poll; 5-min cooldown coexists with launchd watchdog |

---

## 2. The 10x version (where this goes next)

The current system finds and prepares jobs but stops short of three things that
would make the loop genuinely autonomous. In rough priority:

### Tier 1 — Coverage (data wins compound)

- ~~**LinkedIn via paid proxy.**~~ **Shipped free** in step 22 — used the
  unauthenticated guest endpoint (`/jobs-guest/jobs/api/seeMoreJobPostings/
  search`) with HTML parsing. 5 starter searches yield 25-50 high-quality
  India senior IC postings per scout pass. 96% target_city, 61% resume_strong.
- **More verified India boards.** Most Indian unicorns use custom careers
  pages. Build per-company HTML scrapers for the 5-10 highest-value targets
  (Swiggy, Zomato, Cars24, Cleartrip, PhonePe via Workday) — each gets
  ~30-100 senior IC openings into the funnel.
- **Add more LinkedIn search queries.** Each query is essentially free; the
  ceiling is the per-scout 429 threshold (~250 reqs total). Worth adding
  variants like "principal engineer", "tech lead", specific stacks.
- **Wellfound (free, behind anti-bot).** Headed Playwright + cookies works
  but requires care; defer to v2 still.
- **GitHub Jobs / Stack Overflow Jobs replacements** (community-run aggregators
  that filled the gap when those died — verify each is alive, add).

### Tier 2 — Smarter matching (better signal per row)

- **Embeddings-based resume↔JD similarity** as a tie-breaker on top of the
  deterministic skill overlap. Local sentence-transformers, no API cost.
  Surface as `resume_match_semantic` column; sort by combined.
- **Per-company memory.** When you mark a row `applied`, capture the JD
  language the agent reframed against and surface it the next time the same
  company opens a similar role. Reduces critic rework.
- **Negative signals.** When you mark `rejected`, derive shared tags across
  rejected rows (e.g. "all rejected rows are >80k words" or "all in pacific
  time"), surface as a "you usually skip these" dimmed-row treatment.
- **Comp inference from levels.fyi.** Many JDs say "competitive" but the
  company-level comp data exists publicly. One lookup per company pull
  improves `comp_ok` precision dramatically.

### Tier 3 — Loop closure (less manual work)

- ~~**Tinder-style triage UI.**~~ **Shipped** — card-stack view with
  swipe gestures (right=tailor, left=reject, up=skip, tap=open).
  Locked to `status=new`. Viewer mode mirrors the animation without
  mutating, so friends get the full UX.
- **Auto-apply to the highest-confidence rows.** When a row is
  `comp_ok + target_city + resume_strong + ai_native`, the user is going to
  apply. Build a per-ATS submission helper (Greenhouse + Lever have public
  apply forms; Ashby has `applyUrl`). One-click "apply with this PDF."
- **Cover letter generation** as an optional pass after tailoring. System A
  has the JD + tailored resume; cover letter is a 30-line addition.
- **Recruiter outreach drafting.** When `has_recruiter_email` fires, generate
  a 2-paragraph note tied to the JD's specific emphasis + your resume's
  matching project. User reviews + sends.
- **Telegram bridge for the webapp.** Mark `tailor` from your phone via
  `/queue` command in the existing bot; processor button maps to `/process`.
  No need to open the webapp for the daily ritual.

### Tier 4 — Operational

- **Multi-user.** Today the Sheet, Drive folder, and chat_id are all single-
  tenant. Sharding by `chat_id` is mostly schema work; the cli-tailor already
  takes `--chat-id`. Friends could run their own instance against a shared
  scout cache.
- **Deploy to Fly.io / Render** instead of laptop + cloudflared. Eliminates
  "laptop must be awake at 9am" but adds cost (~$5/mo) + the SA quota
  shenanigans become irrelevant (server has its own outbound).
- **Cloudflare Access** for proper auth instead of token-in-URL. The
  current setup is fine for personal+friends-demo; once it's mission-
  critical, swap to Access.
- **Per-company "quality budget."** If three Stripe rows in a row got
  critic score <60, the system should auto-pause Stripe board pulls until
  the user untags them — a self-tuning version of `boards.yaml` discipline.

### Tier 5 — Ecosystem

- **Public scout cache.** The deduped, tagged stream of every senior
  app-eng role across the AI-native ecosystem is genuinely valuable to
  other engineers. A read-only public mirror at `jobs.deepesh-engg.in`
  with anonymized rows could help friends without exposing the user's
  Sheet at all.
- **Open-source the scout half.** Boards.yaml + tag rules + parser is
  ~3k lines that other people would happily fork. Tailor side stays private
  (it's the user's resume + Claude bill).

---

## 3. Non-goals (hold the line)

These were non-goals in v1 and remain so. Listed here because they keep
coming up:

- **Not a job board.** No public crawl listing, no SEO, no ranking by
  popularity. The system serves one user (or a handful of friends).
- **Not a recruiting CRM.** Tracking after-application state (recruiter
  conversations, offer numbers) lives elsewhere — this system stops at
  "applied with which PDF on which date."
- **Not a generic Sheet UI.** The webapp is purpose-built for *this* schema;
  adding generic editing of arbitrary cells defeats the discipline of
  the tag/status state machine.

---

## 4. Constraints and load-bearing decisions

(Decisions that future-self might want to re-litigate but shouldn't, with
the why baked in. Mirror of resume-builder's `decisions.md` style — full
ADR list lives there for System A; key constraints captured here.)

### 4.1 Sheet-as-DB
**Why:** zero ops, free, reviewable from any device, exportable forever,
and the user's filter views become first-class application state. Trade-off:
≤60 reads/min/user from Sheet API, mitigated by 30s in-memory cache.

### 4.2 Local-first server (laptop + tunnel)
**Why:** $0 hosting, full control over the SA secrets, easy to inspect.
Trade-off: laptop must be awake. Mitigated by `caffeinate` + launchd
+ optional Fly.io migration (Tier 4).

### 4.3 OAuth user delegation for Drive
**Why:** Service accounts have 0GB Drive quota for personal Google accounts,
so SA-uploads to user-shared folders fail. OAuth user-token uploads as
the user → use the user's quota → no quota issue. One-time browser dance.

### 4.4 Read-only by default in public mode
**Why:** PATCH endpoints can mutate the Sheet and POST `/api/process`
can spend Claude API budget. With the URL public, only the bookmarked
owner-token URL unlocks writes; visitors get a clearly-labeled preview
mode with disabled action buttons.

### 4.5 No LLM for tagging / comp parsing
**Why:** Hallucinated currency or invented comp ranges are worse than
`null`. Regex misses are recoverable (rerun with broader rules); LLM
errors require manual audit per row. The current parser handles 28% of
JDs (113/395) which matches the natural disclosure rate; the rest get
`comp_unknown` and pass through.

### 4.6 System A as subprocess, not Python import
**Why:** Cross-language (TypeScript/Python) — no shared runtime. The
subprocess CLI (`cli-tailor.js`) reuses the bot's exact tailoring +
critic + refinement pipeline so output quality matches Telegram-driven
runs exactly.

### 4.7 Watchdog redundancy
**Why:** Resume-builder has a launchd-driven watchdog. We added a second
one in the FastAPI process. Redundancy beats a silent hang. Cooldowns
prevent oscillation; both watchdogs are idempotent.

---

## 5. Success criteria — v2 edition

- [x] **9am: Sheet has 5–15 reviewable senior IC roles** (currently averaging 50–80
  matching the relaxed filter view; need to tune the "morning batch" view down)
- [x] **Drill into any row from phone in <3 taps** → done via the webapp
- [x] **Tailor a JD → land a clickable Drive PDF** in <10 min → done
- [x] **System self-heals when the bot hangs** → watchdog ships
- [ ] **Apply to a marked row in <60 seconds from "tailor" → "applied"**
  → currently ~5min waiting on Claude, then manual application click
- [ ] **Cover letter and recruiter note auto-drafted on demand** → tier 3

The first four are the v2 ship gates and are met. The last two are
the v3 north star.
