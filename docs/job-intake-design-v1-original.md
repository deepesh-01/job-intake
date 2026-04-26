# Job Intake Pipeline — Product Vision & Design Doc

*System B — companion to the Resume Tailoring Bot (System A)*
*Owner: [you] · Status: pre-build · Last updated: 2026-04-26*

---

## 1. Product Vision

### What it is
A daily job intake pipeline that scrapes a curated set of job boards, deduplicates and tags new postings, writes them to a Google Sheet, and lets me trigger tailored resume generation by marking a row. The Sheet is the single source of truth. I do the filtering and the apply click. The pipeline does the boring work of finding and prepping.

### Who it's for
Just me. Single user. Runs on my laptop alongside System A.

### Why
- Manually scanning Greenhouse / Lever / Ashby every morning is the actual time sink.
- I already have a working tailoring bot (System A). System B is a *queue feeder* for it, not a parallel pipeline.
- Sheet-as-state means I can review on phone, laptop, or anywhere — and the data is exportable forever.

### Relationship to System A
System B does **not** reimplement tailoring. When a row is marked `Status=tailor`, the pipeline calls into System A's tailoring code (imported as a library, not a separate service). One source of truth for "how to tailor a resume."

### Non-goals
- Not a public service.
- Not LinkedIn scraping in v1. (Deferred to v2 with a paid proxy.)
- Not auto-applying. The apply click stays manual.
- Not generating cover letters in v1.
- Not auto-tailoring on row creation. Tailoring is triggered by me.
- Not sending follow-up reminders. Calendar handles that.
- Not hosting resumes on a public URL.
- Not computing a numerical Match Score. Tags only.

### Success criteria
- 9am: I open the Sheet, see ~5–15 deduped, tagged new rows from the past 24h.
- I filter on tags, read 2–5 interesting JDs, mark them for tailoring.
- Tailored PDFs land in the Sheet within a few minutes.
- I apply, mark `Status=applied`, move on.
- Total morning ritual: 15 minutes for what used to take 90.

---

## 2. User Stories

1. As the user, every morning I see new job postings from my curated boards in a Sheet, deduped against everything I've seen before.
2. As the user, I filter rows by tags (comp, stack, seniority, recency) to find the 2–5 I care about.
3. As the user, I mark a row `Status=tailor` and within minutes a tailored PDF link appears in that row.
4. As the user, I can add or remove company boards from a YAML config without touching code.
5. As the user, I can see why a row was tagged the way it was (which keywords matched).
6. As the user, when the Scout finds nothing new, the Sheet stays clean and I get no spam.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Scout (daily cron, on laptop wake)                       │
│  - reads boards.yaml                                     │
│  - fetches Greenhouse/Lever/Ashby/Workday                │
│  - extracts (company, role, link, comp, jd_text, date)   │
│  - dedups against Sheet                                  │
│  - applies tags                                          │
│  - appends new rows                                      │
└────────────────────────┬─────────────────────────────────┘
                         │ gspread
                         ▼
              ┌────────────────────────┐
              │ Google Sheet            │
              │  - one tab "Jobs"       │
              │  - one tab "Boards"     │ (read-only mirror of yaml)
              │  - one tab "Log"        │
              └────────────┬───────────┘
                           │
                           │ user marks Status=tailor
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│ Processor (triggered by user, polls Sheet on demand)     │
│  - finds rows where Status=tailor and tailored_at empty  │
│  - calls System A's tailoring code (library import)      │
│  - writes resume path + last_change to row               │
│  - sets Status=ready                                     │
└──────────────────────────────────────────────────────────┘
```

Key properties:
- Scout runs on a schedule. Processor runs on demand.
- Processor is **not a daemon**. It's a CLI command (or a Telegram message to System A) that triggers a single sweep.
- Both modules import from a shared `lib/` for Sheet access, dedup, tag rules.

---

## 4. Repository Layout

Separate repo from System A. Independent versioning.

```
~/code/job-intake/
├── pyproject.toml               # Python 3.11+, uv or poetry
├── .env                         # secrets, see §13.8
├── boards.yaml                  # source-of-truth config for sources
├── tag_rules.yaml               # tag definitions (keywords, comp floor)
├── src/
│   ├── scout/
│   │   ├── __init__.py
│   │   ├── runner.py            # entrypoint: `python -m scout`
│   │   ├── sources/
│   │   │   ├── greenhouse.py    # JSON API client
│   │   │   ├── lever.py
│   │   │   ├── ashby.py
│   │   │   └── workday.py       # HTML scrape via Playwright
│   │   ├── extract.py           # comp parsing, role normalization
│   │   ├── dedup.py
│   │   └── tag.py
│   ├── processor/
│   │   ├── __init__.py
│   │   └── runner.py            # entrypoint: `python -m processor`
│   ├── sheet/
│   │   ├── client.py            # gspread wrapper, batched writes
│   │   ├── schema.py            # column constants, version
│   │   └── migrate.py           # schema upgrade logic
│   └── tailor_bridge.py         # imports System A's tailoring lib
├── scripts/
│   ├── bootstrap_sheet.py       # creates a fresh Sheet from schema
│   └── reload_boards.py         # re-syncs boards.yaml → Boards tab
├── tests/
│   └── (smoke tests only for v1)
└── data/
    └── seen_ids.sqlite          # local dedup cache (mirror of Sheet)
```

System A is invoked as a subprocess via its CLI (System A is TypeScript/Node, System B is Python — no cross-language editable install possible). See §13.8 for the bridge contract and required System A CLI surface.

---

## 5. The Sheet

### 5.1 Tab: `Jobs` (the main tab)

| Column | Type | Filled by | Notes |
|---|---|---|---|
| `id` | string | Scout | `<source>:<board>:<posting_id>` — primary key |
| `discovered_at` | ISO date | Scout | When Scout first saw it |
| `posted_at` | ISO date | Scout | From source if available, else `discovered_at` |
| `company` | string | Scout | Normalized (lowercase, no LLC/Inc) |
| `role` | string | Scout | As-posted |
| `location` | string | Scout | "remote" / "SF" / "remote (US)" / null |
| `comp_string` | string | Scout | Raw matched text ("₹35-50 LPA", "$180k–$220k") |
| `comp_currency` | string | Scout | ISO code: `INR`, `USD`, `GBP`, etc. Null if not detected |
| `comp_low` / `comp_high` | int | Scout | Range in original currency units. Null if unparseable |
| `comp_low_usd` / `comp_high_usd` | int | Scout | USD-equivalent via `tag_rules.fx_rates`. For internal floor check |
| `link` | URL | Scout | Apply page or JD URL |
| `jd_snippet` | string | Scout | First 800 chars of JD body |
| `jd_full_path` | string | Scout | Local path to full JD markdown |
| `tags` | comma-separated | Scout | See §6 |
| `tag_reasons` | string | Scout | Why each tag fired (debugging) |
| `status` | enum | User → Processor | `new` \| `tailor` \| `ready` \| `applied` \| `rejected` \| `skip` |
| `resume_path` | string | Processor | Filesystem path to tailored PDF |
| `last_change` | string | Processor | One-line summary from System A |
| `tailored_at` | ISO datetime | Processor | When tailoring completed |
| `applied_at` | ISO date | User | Manual fill when I click Apply |
| `followup_due_at` | ISO date | Scout (sweep) | Set when applied_at + 7d ≤ today AND response_at is null |
| `response_at` | ISO date | User | Manual fill if recruiter responds; clears `followup_due_at` |
| `notes` | string | User | Free-form |

Default `status = new` on Scout insert. Processor only acts on `status = tailor`.

### 5.2 Tab: `Boards`

Read-only mirror of `boards.yaml`, regenerated each Scout run. Lets me see at a glance what's being scraped.

| Column | Type |
|---|---|
| `slug` | string |
| `source_type` | greenhouse \| lever \| ashby \| workday |
| `board_id` | string |
| `enabled` | bool |
| `last_scrape_at` | ISO datetime |
| `last_scrape_status` | ok \| error |
| `last_error` | string |
| `rows_added_today` | int |

### 5.3 Tab: `Log`

Append-only Scout/Processor activity log, last 30 days.

| Column | Type |
|---|---|
| `ts` | ISO datetime |
| `module` | scout \| processor |
| `event` | started \| finished \| error \| skipped |
| `detail` | string |

### 5.4 Schema versioning

`Jobs!A1` cell holds `schema_version: N`. On startup, both Scout and Processor read this. If their `SCHEMA_VERSION` constant is higher, run `migrate.py`. If lower, refuse to run with a clear error. This prevents one module from corrupting a Sheet the other module wrote.

---

## 6. Tagging

No numerical score. Tags are deterministic, transparent, and explainable.

### 6.1 Tag taxonomy (v1)

Tags are grouped by purpose. Multiple per row, comma-separated in the `tags` cell.

**Comp** (currency-aware — separate floors for INR vs USD)
| Tag | Meaning | Rule |
|---|---|---|
| `comp_ok` | Cash TC meets floor | INR ≥ floor_inr OR USD/GBP/EUR ≥ floor_usd_equivalent |
| `comp_unknown` | No comp listed | `comp_string` is null |
| `comp_below` | Below floor | parsed but under threshold |

**Stack** (only the stacks I actually want)
| Tag | Rule |
|---|---|
| `stack_typescript` | TS/Node keyword in requirements section |
| `stack_python` | Python keyword in requirements section |
| `stack_match` | derived OR of any `stack_*` |

**Role shape**
| Tag | Rule |
|---|---|
| `seniority_match` | Senior/Staff/Founding/Lead/Architect in title |
| `seniority_junior` | Junior/Entry/Intern in title — exclusion signal |
| `application_eng` | Backend/Full-stack/Product eng — NOT infra/ML/data/security |
| `wrong_discipline` | Infra/SRE/ML/data/security/QA — exclusion signal |

**Stage**
| Tag | Rule |
|---|---|
| `early_stage` | Seed/Series A — overrides `comp_below` for review |
| `growth_stage` | Series B/C |
| `late_stage` | Series D+ / pre-IPO / public |

**Culture signals** (the #7 answer — these are the ones I'll actually filter on)
| Tag | Rule |
|---|---|
| `ai_native` | JD/company explicitly uses or builds AI tooling, encourages Cursor/Copilot/Claude |
| `culture_chill_signal` | "Work-life balance", "no on-call", "9-to-5", "async", "no crunch" language |
| `grind_signal` | "Rockstar", "ninja", "wear many hats", "we work hard", rocket emoji red flags |
| `enjoy_eligible` | derived: (`ai_native` OR `culture_chill_signal`) AND NOT `grind_signal` |

**Location**
| Tag | Rule |
|---|---|
| `remote_ok` | Remote-friendly to IN, or India/Bengaluru in location |
| `non_us_only` | Posted as US-only / EU-only / specific other geo — visual deprioritize |

**Other**
| Tag | Rule |
|---|---|
| `posted_recent` | Posted within 7 days |
| `has_recruiter_email` | JD body contains an email address |
| `followup_due` | Status=applied AND applied_at ≥ 7 days ago AND no response |

The combination I'll filter on most: `comp_ok AND stack_match AND seniority_match AND application_eng AND enjoy_eligible AND remote_ok`. That's the "should genuinely apply" set.

### 6.2 Tag rules config

`tag_rules.yaml` — committed v1 values, calibrated to my actual situation:

```yaml
# ─────────────────────────────────────────────────────────────────
# COMPENSATION — currency-aware, calibrated to my real switch trigger.
# Current TC: ₹26L base + ₹4L variable + ₹50L ESOPs (4yr vest).
# Cash TC ≈ ₹30L. A meaningful upgrade is ~₹40L+ cash, OR a
# remote USD role at $60k+, OR an early-stage company where the
# equity is the real story (use `early_stage` to override comp_below).
# ─────────────────────────────────────────────────────────────────

comp:
  inr_floor: 4000000        # ₹40L total cash TC (base + variable)
  usd_floor: 60000          # $60k for remote/global roles
  # FX for non-INR/USD currencies, conservative (one-way only).
  # Convert to USD then check against usd_floor.
  fx_rates:
    GBP: 1.20
    EUR: 1.05
    SGD: 0.74
    AUD: 0.65
    CAD: 0.72

my_region: IN
my_city_aliases: [bengaluru, bangalore, blr]

# ─────────────────────────────────────────────────────────────────
# STACK — only what I actually want to be hired for.
# ─────────────────────────────────────────────────────────────────

stacks:
  typescript:
    keywords:
      - typescript
      - "type script"
      - ts
      - node.js
      - nodejs
      - "node js"
      - react
      - next.js
      - nestjs
      - deno
      - bun
      - hono
    section_hint: [required, must have, requirements, "you have", "you bring", "what we look for"]

  python:
    keywords:
      - python
      - django
      - fastapi
      - flask
      - pydantic
      - asyncio
    section_hint: [required, must have, requirements, "you have", "you bring", "what we look for"]

# ─────────────────────────────────────────────────────────────────
# ROLE SHAPE — application engineering only. Not infra, not ML, not
# data eng, not security, not QA. These tags filter aggressively.
# ─────────────────────────────────────────────────────────────────

application_eng:
  match:
    - "full stack"
    - "full-stack"
    - "fullstack"
    - "backend engineer"
    - "back-end engineer"
    - "product engineer"
    - "software engineer"
    - "applications engineer"
    - "founding engineer"
    - "early engineer"
  exclude:
    - "infrastructure"
    - "platform engineer"
    - "site reliability"
    - "sre"
    - "devops"
    - "machine learning"
    - "ml engineer"
    - "ml ops"
    - "data engineer"
    - "data scientist"
    - "security engineer"
    - "qa engineer"
    - "test engineer"
    - "ios engineer"
    - "android engineer"
    - "embedded"
    - "firmware"
    - "hardware"

seniority:
  match:
    - senior
    - staff
    - principal
    - founding
    - "founding engineer"
    - "early engineer"
    - lead
    - "sr\\."
    - "tech lead"
    - "engineering lead"
    - "head of engineering"
    - architect
    - mts            # Member of Technical Staff (FAANG-style)
    - smts
  exclude:
    - junior
    - "jr\\."
    - entry
    - "entry-level"
    - intern
    - graduate
    - "new grad"
    - associate
    - trainee
    - apprentice

# ─────────────────────────────────────────────────────────────────
# STAGE — derived from JD body language.
# `early_stage` is special: it allows `comp_below` rows to still
# be reviewable (the equity is the upside, not the cash).
# ─────────────────────────────────────────────────────────────────

stage:
  early:
    match:
      - "seed stage"
      - "series a"
      - "series-a"
      - "founding team"
      - "early stage"
      - "yc s2[0-9]"
      - "yc w2[0-9]"
      - "y combinator"
      - "first 10 engineers"
      - "first engineering hire"
      - "no.* engineer"
  growth:
    match:
      - "series b"
      - "series-b"
      - "series c"
      - "series-c"
      - "growth stage"
  late:
    match:
      - "series d"
      - "series e"
      - "pre-ipo"
      - "publicly traded"
      - "nasdaq"
      - "nyse"

# ─────────────────────────────────────────────────────────────────
# CULTURE — the most important signal for me. I want either
# (a) genuinely chill 9-5 (so I have time for personal projects), or
# (b) fast-moving but enjoyable + AI-friendly. NOT scrappy startups
# that demand long hours without the upside.
# ─────────────────────────────────────────────────────────────────

culture:
  ai_native_signals:
    # JD or company description mentions these positively
    - "cursor"
    - "github copilot"
    - "claude code"
    - "ai-native"
    - "ai-first"
    - "we encourage ai tools"
    - "ai-augmented engineering"
    - "agentic"
    - "ai agent"
    - "llm"
    - "anthropic"
    - "openai"

  chill_signals:
    - "work-life balance"
    - "no on-call"
    - "no on call"
    - "9 to 5"
    - "9-to-5"
    - "async"
    - "asynchronous"
    - "no crunch"
    - "no overtime"
    - "sustainable pace"
    - "40 hour"
    - "40-hour"
    - "fully async"
    - "result-oriented"

  grind_signals:
    # Red flags. If found, tag grind_signal regardless of other tags.
    - "rockstar"
    - "ninja"
    - "10x engineer"
    - "wear many hats"
    - "we work hard"
    - "all in"
    - "hustle"
    - "fast-paced environment"   # cliché-tier, often means crunch
    - "demanding environment"
    - "high-intensity"
    - "we don't watch the clock"
    - "996"

# ─────────────────────────────────────────────────────────────────
# REMOTE — what counts as remote-OK from Bengaluru.
# ─────────────────────────────────────────────────────────────────

remote:
  allow_locations:
    - remote
    - "fully remote"
    - "remote - global"
    - "remote (global)"
    - anywhere
    - worldwide
    - global
    - "remote - india"
    - "remote (india)"
    - "remote - apac"
    - apac
    - "asia pacific"
    - india
  block_locations:
    - "us only"
    - "usa only"
    - "united states only"
    - "us-based only"
    - "must reside in the us"
    - "canada only"
    - "uk only"
    - "emea only"
    - "europe only"
    - "eu only"
```

Re-load on every Scout run. No code changes to retune.

**Tuning playbook (post-launch):**
- After 2 weeks, eyeball rows that *should* have matched but didn't. Usually it's a stack keyword or a seniority synonym. Add to yaml.
- If `comp_ok` rate is <10% of total rows, lower `inr_floor` by ₹5L or `usd_floor` by $10k.
- If `enjoy_eligible` is firing on rows that turn out grindy, add the missed phrase to `grind_signals`.
- If `wrong_discipline` is over-firing (real app eng roles being excluded), trim its list.
- Don't add weights or scores. The point is filters, not rankings.

### 6.3 `tag_reasons` field

For every tag applied, record *why* in `tag_reasons`. Example:

```
comp_ok=range:180k-220k; stack_python=keyword:fastapi@requirements; seniority_match=title:Senior
```

When I'm confused why a row wasn't tagged the way I expected, this field tells me. Don't skip this — it's the difference between a system you trust and one you fight.

---

## 7. Sources (v1)

`boards.yaml` is the only place sources are configured.

### 7.1 Starter `boards.yaml`

I couldn't supply a hand-curated company list, so this is a starter set drawn from the criteria *application engineering, AI-native or genuinely chill, growth-or-late stage, no crypto/defense/big tech/services*. **Verify each `board_id` works on first Scout run** — ATS providers occasionally change slugs, and I'm pasting from memory.

```yaml
# Run scripts/verify_boards.py after editing — it pings each
# board's API and reports any that 404.

boards:
  # ── AI-native (high enjoy_eligible likelihood) ──
  - { slug: anthropic,    source_type: greenhouse, board_id: anthropic,    enabled: true }
  - { slug: vercel,       source_type: ashby,      board_id: vercel,       enabled: true }
  - { slug: posthog,      source_type: ashby,      board_id: posthog,      enabled: true }
  - { slug: replicate,    source_type: ashby,      board_id: replicate,    enabled: true }
  - { slug: mintlify,     source_type: ashby,      board_id: mintlify,     enabled: true }
  - { slug: resend,       source_type: ashby,      board_id: resend,       enabled: true }

  # ── Growth-stage application eng with good rep ──
  - { slug: linear,       source_type: ashby,      board_id: linear,       enabled: true }
  - { slug: stripe,       source_type: greenhouse, board_id: stripe,       enabled: true }
  - { slug: notion,       source_type: greenhouse, board_id: notion,       enabled: true }
  - { slug: figma,        source_type: greenhouse, board_id: figma,        enabled: true }
  - { slug: supabase,     source_type: ashby,      board_id: supabase,     enabled: true }
  - { slug: cal,          source_type: ashby,      board_id: cal,          enabled: true }
  - { slug: framer,       source_type: lever,      board_id: framer,       enabled: true }
  - { slug: webflow,      source_type: greenhouse, board_id: webflow,      enabled: true }
  - { slug: zapier,       source_type: greenhouse, board_id: zapier,       enabled: true }
  - { slug: github,       source_type: greenhouse, board_id: github,       enabled: true }
  - { slug: discord,      source_type: greenhouse, board_id: discord,      enabled: true }

  # ── Indian unicorns/well-funded (verify ATS — many use custom careers pages) ──
  - { slug: razorpay,     source_type: lever,      board_id: razorpay,     enabled: true }
  - { slug: postman,      source_type: greenhouse, board_id: postman,      enabled: true }
  - { slug: browserstack, source_type: greenhouse, board_id: browserstack, enabled: true }

  # ── Aggregators (different scraper class — see §7.2) ──
  - { slug: yc_waas,      source_type: yc_waas,    board_id: ALL,          enabled: true }
  - { slug: hn_hiring,    source_type: hn_hiring,  board_id: ALL,          enabled: true }

  # ── Deferred to v2 (uncomment when paid proxy in place) ──
  # - { slug: wellfound,  source_type: wellfound,  board_id: ALL,          enabled: false }
  # - { slug: linkedin,   source_type: linkedin,   board_id: ALL,          enabled: false }
```

Total: ~20 entries to start. Add to it as I discover companies I'd genuinely apply to. Drop any that consistently produce zero good matches after a month.

### 7.2 Source-type implementations

**ATS sources** (clean, JSON, designed for consumption):

- **Greenhouse** — `GET https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs?content=true`. No auth. Returns full job with body HTML.
- **Lever** — `GET https://api.lever.co/v0/postings/{board_id}?mode=json`. No auth. Body in `description` and `lists`.
- **Ashby** — public GraphQL: `https://api.ashbyhq.com/posting-api/job-board/{board_id}`. No auth.

**Aggregator sources** (heuristic parsing):

- **YC Work at a Startup (`yc_waas`)** — `https://www.workatastartup.com/api/jobs` is the unofficial endpoint that the site itself uses. Returns paginated JSON of all open roles across YC companies. Each posting has `company`, `title`, `location`, `compensation` (often null), `description`. Filter by application-eng titles before tagging. High signal density: most of these are early-stage with real equity. Auto-fires `early_stage` tag.
- **HN "Who's Hiring" (`hn_hiring`)** — Each month's thread is a top-level post (e.g. "Ask HN: Who is hiring? (April 2026)"). Use the HN Algolia API: `https://hn.algolia.com/api/v1/search?query=Ask%20HN%20Who%20is%20hiring&tags=story` to find the latest thread, then fetch comments via `https://hn.algolia.com/api/v1/items/{thread_id}`. Each top-level comment is one posting. Format is community-conventional but inconsistent — parse heuristically:
  ```
  <Company> | <Role> | <Location> | <Remote/On-site> | <Visa/Comp/etc>
  <body>
  ```
  Be tolerant: extract what you can, leave the rest in `jd_snippet`. Many HN postings list comp inline — the regex parser handles it. Auto-fires `posted_recent` (only run once per month, on the day the new thread appears).

**Deferred / fragile** (not in v1):

- **Wellfound** (formerly AngelList) — anti-bot protection, requires headed Playwright + cookies. Defer until v2.
- **LinkedIn** — needs paid proxy (Proxycurl, Coresignal). Defer until I'm willing to pay $50/mo for reach.
- **Workday** — no clean API, Playwright-only, very fragile. Add per-company only if a target uses it.
- **iCIMS, Taleo, Brassring** — same as Workday. Skip.

### 7.3 First-run / backfill

First Scout run on a new Sheet has no dedup cache. Hard cap: only insert postings where `posted_at >= today - 7 days`. Without this, the Sheet floods with hundreds of stale rows.

For YC WaaS (no `posted_at` on most postings) and HN (single monthly thread), use `discovered_at` as a proxy and accept that the first run will pull ~50-100 rows in one shot. Manually set status=skip on the irrelevant ones.

### 7.4 Hard exclusions (`exclude.yaml`)

These prevent rows from ever entering the Sheet. Applied at Scout insert time, before tagging. If a posting matches any rule, it's dropped silently and counted in the Log tab as `excluded: <reason>`.

```yaml
# Companies whose listings I never want to see, regardless of ATS.
# Exact-match (case-insensitive) on company name OR substring match
# on any company_aliases entry.

excluded_companies:
  # Big Tech (skip per #3 in PRD)
  - { name: "google",    aliases: ["google", "alphabet"] }
  - { name: "meta",      aliases: ["meta", "facebook"] }
  - { name: "amazon",    aliases: ["amazon", "aws"] }
  - { name: "microsoft", aliases: ["microsoft"] }
  - { name: "apple",     aliases: ["apple inc"] }
  - { name: "netflix",   aliases: ["netflix"] }

  # Indian services / IT consultancies (skip per #3 in PRD)
  - { name: "tcs",       aliases: ["tata consultancy", "tcs "] }
  - { name: "infosys",   aliases: ["infosys"] }
  - { name: "wipro",     aliases: ["wipro"] }
  - { name: "hcl",       aliases: ["hcl tech", "hcl technologies"] }
  - { name: "cognizant", aliases: ["cognizant"] }
  - { name: "capgemini", aliases: ["capgemini"] }
  - { name: "accenture", aliases: ["accenture"] }
  - { name: "deloitte",  aliases: ["deloitte"] }
  - { name: "ltimindtree", aliases: ["ltimindtree", "lti mindtree"] }
  - { name: "tech mahindra", aliases: ["tech mahindra"] }

# JD body or company description keyword exclusions.
# If ANY of these match, the row is dropped.

excluded_keywords:
  # Crypto / web3 (skip per #3 in PRD)
  - "web3"
  - "blockchain"
  - "crypto"
  - "defi"
  - "nft"
  - "smart contract"
  - "solidity"
  - "ethereum"
  - "bitcoin"
  - "tokenomics"
  - "dao"

  # Defense / military (skip per #3 in PRD)
  - "defense contractor"
  - "department of defense"
  - " dod "
  - "military application"
  - "weapons system"
  - "palantir"
  - "anduril"
  - "lockheed"
  - "raytheon"
  - "northrop"

# Company-name patterns suggesting Indian-services-style consultancies
# even if not in the explicit excluded_companies list.

excluded_company_patterns:
  - ".*(consulting|consultancy|services|solutions) (pvt|private) ltd.*"
  - ".*(it services|it consulting|software services).*"
```

Rationale: keeping these out of the Sheet at insert time, rather than tagging-then-filtering, means the daily review stays clean. ~5 seconds of YAML edits to whitelist a specific company if I ever change my mind.

---


## 8. Core Flows

### 8.1 Daily Scout run

```
1. Triggered by launchd/cron at 9am laptop time, with caffeinate wrapper.
2. Read boards.yaml, tag_rules.yaml, exclude.yaml.
3. For each enabled board:
     a. Fetch postings.
     b. For each posting, compute `id = "<source>:<source_id>"`.
     c. Skip if `id` exists in seen_ids.sqlite OR in Sheet.
     d. Extract company, role, location, comp_string, jd body.
     e. EXCLUSION CHECK (per §7.4):
          - If company matches excluded_companies → drop, log `excluded: big_tech` etc.
          - If JD body matches any excluded_keywords → drop, log reason.
          - If company matches excluded_company_patterns → drop, log reason.
        Excluded rows never reach the Sheet.
     f. Parse comp (currency + range per §13.4).
     g. Save full JD to data/jds/<id>.md.
     h. Apply tags per §6.
     i. Buffer row.
4. After all boards processed:
     - Apply additional dedup: fuzzy match on (company_normalized, role_normalized)
       with edit distance ≤ 3. If duplicate, keep oldest discovered, drop newer.
     - Batch-append all rows to Sheet (single API call).
     - Update Boards tab with last_scrape_at, count, exclusion count.
     - Append Log entry with summary.
5. FOLLOW-UP SWEEP (always runs, even on zero new rows):
     - Read all rows where status=applied AND response_at is null.
     - For each: if applied_at + 7 days <= today, set followup_due_at = today.
     - Single batch_update to write the followup_due_at values.
     - Rows already with followup_due_at set are not re-touched (idempotent).
6. If zero rows added AND zero followups updated: log it, do not notify.
7. If a board failed: continue with others, mark that board's
    last_scrape_status=error in Boards tab.
```

### 8.1.1 Aggregator-source quirks

**YC WaaS** runs once per day like ATS sources, but typically returns 200+ postings — apply exclusions and `application_eng` filter aggressively before insert.

**HN Hiring** runs only on the 1st-3rd of each month (when the new "Who is Hiring" thread appears). Scout checks: if today's day-of-month ≤ 3, query HN Algolia for the latest "Ask HN: Who is hiring?" thread; if its `created_at` is within the last 5 days and we haven't processed it yet (track in seen_ids.sqlite as `hn_hiring:thread:<thread_id>`), iterate its top-level comments and treat each as a posting. After that, skip HN until next month.

### 8.2 Manual review (user)

Not a code flow — this is me. But the Sheet should be set up for it:

- Default view sorted by `discovered_at` desc, filtered to `status=new`.
- Conditional formatting: red on `comp_below`, green on `stack_match AND seniority_match AND comp_ok`.
- Quick filter views saved: "Today's matches", "Comp unknown but stack matches".

### 8.3 Processor run (on demand)

Two entry points:

**A. CLI sweep:**
```
python -m processor
```
Scans Sheet for rows where `status=tailor AND resume_path is empty`. For each:
1. Acquire lock (per-row, file-based at `data/locks/<id>`).
2. Read `jd_full_path` for the row.
3. Call `tailor_resume(jd_path, output_dir)` from System A's library.
4. Move resulting PDF to `data/tailored/<id>.pdf`.
5. Update Sheet row: `resume_path`, `last_change`, `tailored_at`, `status=ready`.
6. Release lock.

**B. Telegram trigger (optional, v1.1):**
Send `/process` to System A's bot. Bot runs `python -m processor` as subprocess and reports results. Lets me trigger a sweep from my phone.

### 8.4 Status lifecycle (manual)

I move rows through states by editing the `status` cell:

```
new → tailor       (I want a tailored resume for this)
tailor → ready     (Processor has produced the PDF)
ready → applied    (I clicked Apply; I also fill applied_at)
new → skip         (not interested, hide from default view)
new → rejected     (got a rejection; archive but keep)
```

No bot watches this. State transitions are mine.

---

## 9. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Best ecosystem for scraping + Sheet APIs |
| Package mgmt | uv | Fast, lockfile-based |
| Sheet client | gspread | Mature, batched writes supported |
| Scraping | httpx (JSON APIs), playwright (Workday only) | Avoid Playwright when JSON exists |
| HTML→text | selectolax + markdownify | Fast, good-enough |
| Dedup cache | SQLite | Local mirror of seen IDs, faster than Sheet round-trip |
| Scheduling | launchd (Mac) | Wakes laptop, runs Scout, exits |
| System A bridge | Local pip install / git submodule | TBD — pick one before build |

---

## 10. Operational Considerations

### 10.1 Laptop availability
- launchd plist with `WakeFromSleep=true` if possible, else accept that Scout runs on next wake.
- `caffeinate -dims` wrapping the Scout invocation so it doesn't sleep mid-run.
- If laptop offline 24h+, Scout still works fine on next run; sources don't expire postings that fast.

### 10.2 Google Sheets API limits
- Read: 60 req/min/user. Write: same.
- Always batch via `worksheet.append_rows()` (plural) and `batch_update()`.
- Single Scout run should make ≤5 API calls regardless of row count.

### 10.3 Scraping politeness
- 1 req/sec per source domain.
- User-Agent set to identifiable string with my email.
- Respect `robots.txt` (Greenhouse/Lever/Ashby APIs are designed for consumption, but check anyway).
- On 429: exponential backoff, max 3 retries, then mark board errored and continue.

### 10.4 Comp parsing fallibility
Comp parsing is regex-based and will miss formats. That's fine — null is the correct answer when unsure, and `comp_unknown` tag still lets me filter. **Do NOT use an LLM for comp parsing in v1.** It's expensive, slow, and the failure mode (hallucinated numbers) is worse than null.

### 10.5 Privacy & secrets
- Service account JSON for Google Sheets API stored in `.env`-referenced path, mode 0600.
- No sensitive data in code or git. `.gitignore` includes `.env`, `data/`, `*.json` at root.
- Don't log full JD bodies — link to file path instead.

### 10.6 Cleanup
- `data/jds/<id>.md` for every JD ever scraped — keep forever, small.
- `data/tailored/<id>.pdf` — keep forever for re-send.
- `Log` tab — keep last 30 days, truncate older.

### 10.7 Failure modes and what happens
| Failure | Behavior |
|---|---|
| One board source returns 5xx | Skip, mark board errored, continue others |
| Sheet API rate limit | Back off + retry; if still failing, dump rows to local JSONL and Sheet next run |
| Comp parse fails | Set comp_low/high null, tag `comp_unknown` |
| JD body empty/<200 chars | Skip the row entirely |
| Duplicate detection collision (different jobs, same id) | Log warning, keep first, drop second |
| Processor: System A throws | Update row status to `error`, write error to `last_change`, continue to next row |
| Processor: lock file orphaned (>1h old) | Auto-clear and proceed |

---

## 11. Build Order

1. **Sheet bootstrap.** `bootstrap_sheet.py` creates the three tabs with correct headers and schema_version cell. Verify by hand.
2. **Sheet client lib.** `sheet/client.py` — append_rows, batch_update, read_filtered. Smoke test against the bootstrapped Sheet.
3. **Greenhouse source.** Fetch one board (e.g. Anthropic), extract fields, append to Sheet without tagging or dedup. Confirm rows look right.
4. **Lever + Ashby sources.** Mirror structure of Greenhouse client.
5. **Dedup.** seen_ids.sqlite + fuzzy company/role matching. Re-run Scout, confirm zero new rows on second run.
6. **Tagging.** Implement tag.py + tag_rules.yaml. Verify `tag_reasons` is human-readable.
7. **Comp parsing.** Regex-based. Test on 30 real JDs from past Scout runs.
8. **boards.yaml flow.** Replace hardcoded sources with config-driven. Bootstrap Boards tab.
9. **Scheduling.** launchd plist for daily 9am run.
10. **System A bridge.** `tailor_bridge.py` — install/import System A, validate function call.
11. **Processor.** CLI sweep that reads Sheet, calls bridge, writes results back.
12. **Workday source.** Only if needed. Last because it's fragile.
13. **Telegram trigger** (optional v1.1).

Target: working Scout writing to Sheet by end of weekend 1. Processor + System A integration weekend 2.

---

## 12. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Greenhouse/Lever/Ashby change API | Low | They're stable for years; pin source_type modules so one breaking doesn't break others |
| Sheet schema drifts between Scout/Processor | Medium | Schema version cell + migrate.py |
| Same job appears under different IDs across sources | Medium | Fuzzy dedup on (company, role) |
| Comp parse produces wrong number | Medium | Default to null; tag `comp_unknown` in ambiguous cases |
| Sheet gets cluttered / too many rows | High over time | `skip` and `rejected` statuses hide from default filter |
| First run floods with stale postings | Certain | 7-day cutoff on first run |
| System A's tailoring API changes | Medium | Bridge module isolates the call surface; one place to update |
| Scout silently produces zero rows for weeks | Medium | Boards tab shows last_scrape_at — eyeball weekly |
| launchd doesn't wake laptop reliably | Medium | Accept that Scout runs on next manual wake; don't depend on punctual schedule |

---

## 13. Implementation Spec

This section is binding. Use exact schemas, file paths, and config keys as specified.

### 13.1 Sheet schema (canonical)

`SCHEMA_VERSION = 1`. The Jobs tab has these columns in this order:

```
id | discovered_at | posted_at | company | role | location |
comp_string | comp_currency | comp_low | comp_high | comp_low_usd | comp_high_usd |
link | jd_snippet | jd_full_path | tags | tag_reasons |
status | resume_path | last_change | tailored_at |
applied_at | followup_due_at | response_at | notes
```

Notes on new fields vs §5.1:
- `comp_currency` — ISO code (`INR`, `USD`, `GBP`, etc.), null if `comp_string` had no currency marker.
- `comp_low_usd` / `comp_high_usd` — comp_low/high converted to USD via `tag_rules.fx_rates`. Used internally for the `comp_ok` check; the original-currency fields stay for human readability.
- `followup_due_at` — set by Scout on each run: if `status=applied` and `applied_at + 7d <= today` and `response_at` is null, set this to today's date so the row sorts to the top under a "follow up" filter view. If empty, no follow-up needed.
- `response_at` — manually filled by user when a recruiter replies. Clears `followup_due_at`.

A1 cell contains `schema_version: 1` (string, not formatted). Frozen header row.

### 13.2 ID format

`{source_type}:{board_id}:{posting_id}`

Examples:
- `greenhouse:anthropic:5544332`
- `lever:linear:abc-123-def-456`
- `ashby:vercel:0a1b2c3d-...`

This guarantees uniqueness across sources and is the dedup primary key.

### 13.3 Source client contract

Every source module exposes:

```python
def fetch(board_id: str) -> list[Posting]:
    """Return all current postings for this board.
    Raises SourceError on unrecoverable failure (with last_error string)."""

@dataclass
class Posting:
    id: str               # e.g. "greenhouse:anthropic:5544332"
    company: str
    role: str
    location: str | None
    comp_string: str | None
    posted_at: date | None
    link: str
    jd_html: str          # raw HTML, scout will convert to markdown
```

### 13.4 Comp parsing rules

Multi-currency aware. Detect currency first, then parse range, then store both original and USD-equivalent values.

**Currency detection** (in priority order — first match wins):

```
₹  / Rs / INR / lakh / lakhs / crore / cr / lpa  → INR
$  / USD / per year / annually + $              → USD
£  / GBP                                         → GBP
€  / EUR                                         → EUR
S$ / SGD                                         → SGD
A$ / AUD                                         → AUD
C$ / CAD                                         → CAD
```

**Magnitude tokens:**

```
INR:   "20 LPA" / "20L" / "20 lakh"  → 20 × 100,000   = 2,000,000
INR:   "1.5 cr" / "1.5 crore"         → 1.5 × 10,000,000 = 15,000,000
USD:   "150k" / "$150K"               → 150,000
USD:   "$150,000"                     → 150,000
```

**Range patterns** (run once per detected currency):

```
1. <CUR>XXX – <CUR>YYY           → (XXX, YYY)
2. <CUR>XXX to <CUR>YYY          → (XXX, YYY)
3. up to <CUR>YYY                → (None, YYY)
4. <CUR>XXX+                     → (XXX, XXX)
5. ESOPs / equity-only / no figure → (None, None) + tag comp_unknown
```

**Output fields:**

```
comp_string    = original matched text, e.g. "₹35-50 LPA"
comp_currency  = "INR"
comp_low       = 3500000        # in original currency units
comp_high      = 5000000
comp_low_usd   = 41666           # via fx_rates if not USD/INR
comp_high_usd  = 59523
```

**Tag firing logic:**

```python
inr_floor = config.comp.inr_floor       # 4_000_000
usd_floor = config.comp.usd_floor       # 60_000

if comp_currency == "INR":
    floor_passed = comp_high >= inr_floor
elif comp_currency in ("USD", "GBP", "EUR", "SGD", "AUD", "CAD"):
    floor_passed = comp_high_usd >= usd_floor
else:
    return "comp_unknown"

return "comp_ok" if floor_passed else "comp_below"
```

**Hard rules:**

- If currency cannot be detected, set everything null and tag `comp_unknown`. **Never assume USD.**
- If only ESOPs/equity is mentioned with no cash range, tag `comp_unknown` (cash floor can't be evaluated). The `early_stage` tag, if also present, signals "review anyway."
- Don't use an LLM for parsing in v1. Regex misses are fine — `comp_unknown` is the safe default and I can still review.

### 13.5 Dedup

```
For each new posting:
  1. Check seen_ids.sqlite → if id present, skip.
  2. Check Sheet `id` column (cached at run start) → if present, skip and add to sqlite.
  3. Fuzzy match: lowercased company + role, edit distance ≤ 3.
     If match found in last 30 days of Sheet, skip and log.
  4. Otherwise: insert; record id in sqlite.
```

### 13.6 Configuration

`.env`:
```
GOOGLE_SHEETS_CREDS_PATH=/Users/me/secrets/job-intake-sa.json
SHEET_ID=1AbCdEf...
DATA_DIR=/Users/me/code/job-intake/data
TAG_RULES_PATH=/Users/me/code/job-intake/tag_rules.yaml
BOARDS_PATH=/Users/me/code/job-intake/boards.yaml
SYSTEM_A_PATH=/Users/me/code/resume-bot
LOG_LEVEL=INFO
```

Fail fast if any path doesn't exist or any required var is missing.

### 13.7 launchd plist

`~/Library/LaunchAgents/com.user.jobintake.scout.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.user.jobintake.scout</string>
    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/caffeinate</string>
      <string>-dims</string>
      <string>/Users/me/code/job-intake/.venv/bin/python</string>
      <string>-m</string>
      <string>scout</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/me/code/job-intake</string>
    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key><integer>9</integer>
      <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/me/code/job-intake/data/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/me/code/job-intake/data/launchd.err.log</string>
  </dict>
</plist>
```

Load with `launchctl load ~/Library/LaunchAgents/com.user.jobintake.scout.plist`.

### 13.8 System A bridge

**Decision: subprocess invocation, not Python import.**

System A is a TypeScript/Node project (grammY + zx + better-sqlite3, per System A's §13.10). System B is Python. Cross-language editable install isn't possible, so the bridge is a subprocess call into System A's CLI entrypoint.

System A must expose a CLI command for headless tailoring (add to System A's build if not already there):

```bash
# from System A's repo
node dist/cli.js tailor \
  --jd-path /path/to/job_description.md \
  --base-resume /path/to/base_resume.md \
  --output-dir /path/to/output \
  --output-format json
```

Returns JSON to stdout:
```json
{
  "ok": true,
  "pdf_path": "/path/to/output/final.pdf",
  "last_change": "Reframed summary...",
  "duration_ms": 47200
}
```

System B's `tailor_bridge.py`:
```python
import json, subprocess
from pathlib import Path
from dataclasses import dataclass

SYSTEM_A_CLI = Path(os.environ["SYSTEM_A_PATH"]) / "dist/cli.js"

@dataclass
class TailorResult:
    ok: bool
    pdf_path: str | None
    last_change: str | None
    error: str | None

def run_tailor(job_id: str, jd_path: str, base_resume_path: str,
               output_dir: str) -> TailorResult:
    try:
        proc = subprocess.run(
            ["node", str(SYSTEM_A_CLI), "tailor",
             "--jd-path", jd_path,
             "--base-resume", base_resume_path,
             "--output-dir", output_dir,
             "--output-format", "json"],
            capture_output=True, text=True, timeout=360,
        )
        if proc.returncode != 0:
            return TailorResult(False, None, None, proc.stderr[:500])
        data = json.loads(proc.stdout)
        return TailorResult(
            ok=data["ok"],
            pdf_path=data.get("pdf_path"),
            last_change=data.get("last_change"),
            error=data.get("error"),
        )
    except subprocess.TimeoutExpired:
        return TailorResult(False, None, None, "system_a_timeout")
    except Exception as e:
        return TailorResult(False, None, None, f"bridge_error: {e}")
```

The bridge is the *only* place System B knows about System A. If System A's CLI surface changes, this is the one file to update.

**Action item for System A:** add a `tailor` subcommand to System A's CLI. Currently System A's design covers Telegram entrypoints but not a headless CLI. This is a small addition (~30 lines) but must be built before System B step 10.

### 13.9 Logging

Use `structlog` with key=value output. Every Scout run logs:
- `scout_started` at start
- `board_fetched board=X count=N` per board
- `row_added id=X` per insert (DEBUG level — don't spam INFO)
- `scout_finished added=N skipped=M errored=K duration_s=...` at end

Rotate logs daily, keep 30 days. Mirror critical events (start/finish/errors) to the Log tab in Sheet.

### 13.10 Build prompt for Claude Code

Use this verbatim when starting the build:

```
Read /path/to/job-intake-design.md end to end before writing any code.

Build the system per:
- Architecture in §3
- Repo layout in §4
- Sheet schema in §5 and §13.1 (note: includes comp_currency, comp_low_usd,
  comp_high_usd, followup_due_at, response_at — different from a typical
  job tracker)
- Tagging in §6 (currency-aware comp, application_eng vs wrong_discipline,
  culture signals — read §6 fully, the tag set is opinionated)
- Sources in §7, including aggregators (YC WaaS, HN Hiring) and the
  hard exclusions in §7.4
- Flows in §8 — Scout always runs the follow-up sweep, even on zero new rows
- Stack in §9
- Implementation Spec in §13 — this is binding. Use exact schemas,
  ID formats, regex rules, file paths, and config keys.

Before writing code, generate a tasks.md breaking step 1 of the §11
build order (Sheet bootstrap) into ≤30-min units. Wait for confirmation
before proceeding.

Do NOT scrape LinkedIn, Indeed, Wellfound, Workday, or any source not
listed in §7.2 v1 sources. If a use case seems to need them, ask first.

Do NOT use an LLM for comp parsing, role normalization, tagging, or
exclusion matching. All of that is regex + config rules.

Do NOT assume USD when currency cannot be detected — set comp fields
to null and tag comp_unknown. Never silently default a currency.

Do NOT add a Match Score column. Tags only.

Do NOT add a polling daemon for the Sheet. Processor is CLI-triggered.

Do NOT generate cover letters or auto-emails.

Do NOT bypass exclude.yaml — excluded companies/keywords/patterns must
filter at insert time, before tagging. Excluded rows never reach the Sheet.

Stack: Python 3.11+, uv, gspread, httpx, selectolax, structlog,
sqlite3 (stdlib), pyyaml, rapidfuzz (for dedup edit distance).

Project lives at ~/code/job-intake/, separate from System A at
~/code/resume-bot/. System A is a TypeScript/Node project; System B
calls it via subprocess CLI invocation, not Python import (see §13.8).
System A's `tailor` CLI subcommand must be built before System B step 10.

System A owns the base resume. System B passes the JD path to System A's
CLI; System A reads its own ~/.../base_resume.md per-user. Do not
duplicate base resume storage in System B.

When you finish a build step from §11, stop and let me smoke-test before
the next step.
```

---

*End of doc.*
