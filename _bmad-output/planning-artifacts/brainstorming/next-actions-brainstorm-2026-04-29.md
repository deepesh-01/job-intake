---
date: 2026-04-29
facilitator: Deepeshz2 (via Claude Opus 4.7)
session_type: BMAD brainstorming — next strategic actions
techniques_used:
  - Five Whys (root-cause on geography skew)
  - SCAMPER (on the scout pipeline)
  - Pre-mortem / Reverse Brainstorming (90-day failure mode)
  - First Principles (strip to load-bearing core)
  - Mind Map (lever inventory across 8 axes)
  - What If (constraint inversion)
input_documents:
  - /Users/deepeshz2/Documents/ready-to-apply/CLAUDE.md
  - /Users/deepeshz2/Documents/ready-to-apply/_bmad-output/project-context.md
  - /Users/deepeshz2/Documents/ready-to-apply/docs/sources-roadmap.md
  - /Users/deepeshz2/Documents/ready-to-apply/_bmad-output/planning-artifacts/research/technical-naukri-akamai-bypass-research-2026-04-29.md
  - /Users/deepeshz2/Documents/ready-to-apply/docs/vision.md
  - /Users/deepeshz2/Documents/ready-to-apply/docs/decisions.md (ADR titles)
  - /Users/deepeshz2/Documents/ready-to-apply/boards.yaml (97 entries, 67 enabled)
brain_methods_csv: /Users/deepeshz2/Documents/ready-to-apply/.claude/skills/bmad-brainstorming/brain-methods.csv
status: complete
---

# Next-Actions Brainstorm — 2026-04-29

**Goal of session:** Surface ideas the orchestrator's linear "do X next" list missed. Not a recommendation doc — a divergence doc that ends with reconverged actionables.

**Frame the orchestrator was operating in (and what we're stress-testing):**

> "We just shipped Naukri + LinkedIn-auth + 4 reprobes. Queue is at 756 rows after archive. Next move = telemetry, sheet-aware cap, auto-archive, Naukri detail endpoint, source adjacencies."

That frame is **supply-side, infrastructure-flavored**. The brainstorm probes whether the bottleneck is supply at all.

---

## Technique 1 — Five Whys (root-cause drill)

**Surface symptom:** "37% India / 15% remote / 48% non-India" after a session whose stated goal was India-pivot. Arbeitnow alone supplies 175 of the 363 non-India rows.

| Why | Answer |
|---|---|
| Why is the queue still 48% non-India after the India-pivot session? | Because Arbeitnow + the global-Workday tenants (Adobe/NVIDIA/Walmart/Autodesk) and US-Greenhouse boards (Databricks/Stripe/Datadog/Anthropic) keep firing on every cron. We added India sources but didn't subtract or rate-limit non-India sources. |
| Why didn't we subtract / rate-limit the non-India sources? | Because the architecture treats every enabled board as equal — the runner has no concept of "geography budget". A board either runs full-yield or doesn't run at all. |
| Why does the runner have no geography budget? | Because the original v1 mental model was "more boards = better funnel"; geography was a downstream filter at the row level (`target_city` tag), not an upstream allocation decision at the source level. |
| Why has that mental model not been updated? | Because the user's primary goal flipped from "broaden funnel" → "remote/hybrid in India" in this session, and 67 boards' worth of non-India momentum has not been retuned. The boards.yaml is additive-only by social convention. |
| Why is boards.yaml additive-only? | Because every disabled board sits there as a 404 comment with a hope-it-comes-back note. The cost of *removing* a board feels higher than the cost of *adding* one — but the runtime cost of running it is paid every day. The funnel optimizes for source-coverage as a vanity metric, not for India-relevance per swipe-minute. |

**Root cause:** the funnel optimizes for breadth of coverage rather than density of relevance per cron-minute. The fix is not "add more India sources" — it's **"give every source a relevance budget and demote / disable the ones that don't pay."**

**Five Whys outputs (raw ideas surfaced):**

1. **Per-source `relevance_yield` metric** — fraction of last-30-day rows from that source where `target_city` ∈ India OR `tag` includes `remote`. Rank boards by this. The 12 lowest get `enabled: false` next run; the 5 lowest get archived from yaml entirely.
2. **Geography quota in runner** — declare `INDIA_FLOOR=0.55, REMOTE_FLOOR=0.20` per run and *truncate* postings that overflow non-India after dedup. Today the runner accepts everything.
3. **Arbeitnow audit** — it's 23% of the funnel, mostly EU. Does even one Arbeitnow row ever reach `status=tailor`? If 0/175 in 30 days → disable. (The data is in the sheet — no infra needed to answer.)
4. **Source half-life** — auto-disable any board whose last `status ∈ {tailor, ready, applied}` row is >45 days old. "Use it or lose it" rule.
5. **Re-frame `boards.yaml` as a budget allocation, not an inclusion list** — every entry has a `weight: 0.0–1.0` that controls how many postings make it through. Default 1.0; non-India aggregators get 0.2. This is one new column, ~30 lines of runner code, immediate behavioral change.

---

## Technique 2 — SCAMPER on the scout pipeline

Treating the scout pipeline (`src/scout/runner.py` + sources/*.py) as the artifact to improve.

**S — Substitute:**
- Substitute `enabled: bool` with `weight: float` (see Five Whys idea 5).
- Substitute Arbeitnow (175 rows, mostly EU) with a single LinkedIn `loc=India;wt=2` (remote) query at higher pages depth — same number of rows, 100% on-target.
- Substitute the daily 9am cron with **two crons**: 7am IST (India sources only, before Naukri load) + 9am IST (everything else). This caps Akamai exposure and lets you drop boards from the heavy slot if you 429.

**C — Combine:**
- Combine `verify_boards.py` + `reprobe_disabled.py` + `sheet_archive_skip.py` into a single **weekly health job** that emits one structured report: which boards 5xx'd, which moved ATSes, how many skip rows archived, per-source `relevance_yield`, swipe-rate per source.
- Combine the "swipe right" signal (`status=ready` + `status=tailor`) with the resume-match score to learn a per-company **"taste" multiplier**: if Razorpay is 6/8 swipe-rights but Databricks is 3/40, multiply Razorpay matches by 1.5 and Databricks by 0.5 in the next sort. Cheap, no LLM, runs in one SQL-ish pass over the sheet.
- Combine InfoEdge family — Naukri + AmbitionBox + iimjobs share posting IDs/jdURLs. One scraper, three feeds, dedup by jdURL prefix.

**A — Adapt:**
- Adapt the okhttp/4.12.0 mobile-UA bypass pattern to **Instahyre, Cutshort, Wellfound**. The Naukri research artifact's "Why this is more robust" section is generalizable: *if a site has a mobile app on okhttp, the WAF rule almost certainly whitelists that UA*. This is a cheap probe (1 hour each), not a build.
- Adapt the per-company intra-batch cap (top-7 by score) into a **per-source intra-batch cap** — Walmart Workday alone shipped 160 rows last run; cap at 30.

**M — Modify:**
- Modify the swipe UX from binary `new → tailor/skip` to **3-button**: `tailor` / `interesting-not-now` / `skip`. The middle bucket lets the user clear the queue in 15 min without losing optionality. (Currently if you want to skim it, your only "save for later" is a 2-tab manual filter.)
- Modify `target_city` tag to a binary `India: yes/no/maybe` rather than free-form city. Today's tagging produces 14 different India city spellings; collapsing them lets you actually filter.
- Modify the runner to **promote on swipe-right and demote on skip** — re-rank the next batch of `new` rows from the same source after each session.

**P — Put to other uses:**
- Use the `Skip_2026-04-29` archive (3,328 rows) as a **negative-training corpus**: companies that appear in the skip archive at >70% rate should be candidates for permanent block in `exclude.yaml`. Free signal, untouched.
- Use the resume-match scoring infrastructure to **score competitors** — feed each rejected-match's title back to System A and ask "is this rejection because of seniority gap, stack mismatch, or geography?" Run it once for the 27 rejected rows and you'll find the pattern.
- Use `verify_boards.py` not just for ATS migrations but for **company hiring-velocity** — a company that drops from 50 → 0 postings between weekly probes is signaling layoffs/hiring freeze; downweight (or even auto-disable) before they become noise.

**E — Eliminate:**
- Eliminate Arbeitnow (175 rows, near-zero `target_city` hit rate, wastes intra-batch dedup budget).
- Eliminate the 21 still-disabled "404 — slug changed?" boards in boards.yaml. They're cognitive noise. Move them to `boards.disabled-archive.yaml` so they're discoverable when needed but don't pollute the live config.
- Eliminate per-board pagination on aggregators that already saturate the freshness window (RemoteOK, Remotive). One page is plenty.
- Eliminate the `boards.yaml` single-file structure — split per-source-type once you have 100+ entries (the open question from sources-roadmap §6).

**R — Reverse:**
- Reverse the polarity of "supply optimization": instead of asking *"how do I get more rows?"*, ask *"how do I get fewer rows but with higher swipe-right rate?"*. The morning swipe-triage time-budget is the real constraint, not the funnel size.
- Reverse the order of operations: instead of `fetch → tag → score → dedup → write`, do `fetch → cheap-filter (geography/role) → tag → score → write`. Cuts the work the rest of the pipeline does on rows that will never matter.
- Reverse the LinkedIn-auth gating — instead of "only enable when cookie is fresh", make the cookie freshness *a build step* that fails the cron if `LI_AT_COOKIE` is stale, forcing the user to refresh proactively rather than discover it at 429.

**SCAMPER big surfacings:**

6. **Per-board `weight` field in boards.yaml** — replaces binary enabled.
7. **3-button swipe UX** — `tailor / save-for-later / skip`. Reduces "what do I do with this maybe?" thrash.
8. **Taste-multiplier learning** — feed `status=ready/applied` back into next run's ranking. No LLM required.
9. **Skip_archive as negative corpus** — auto-extract recurring company patterns to `exclude.yaml` candidates.
10. **Two-cron schedule** — 7am India / 9am everything-else. Lower 429 risk on Naukri+LinkedIn.
11. **Eliminate Arbeitnow + 21 zombie boards** — strict subtraction round.
12. **Mobile-UA probe of Instahyre/Cutshort/Wellfound** — generalize the okhttp finding.

---

## Technique 3 — Pre-mortem (90-day failure scenarios)

*"Imagine it's August 2026 and you've abandoned this project. What killed it?"*

Top failure modes ranked by likelihood × severity:

**F1 — Death by sheet-anxiety (high × high).** The sheet hits ~5,000 rows of unscored cruft because auto-archive never shipped. Loading the webapp takes 8 seconds. The 30s cache helps but the React virtualized list still chokes. User stops opening the swipe UI; the funnel runs but is unread. *Implication: auto-archive in runner is not "optional polish" — it's existential.*

**F2 — Naukri tightens, no fallback wired (medium × high).** The okhttp bypass closes one Tuesday morning. The error log shouts but no human is paging. By the time the user notices, 6 days of India coverage is missing and the sheet skews back to US-Workday. *Implication: cheap heartbeat probe + alert on 0-postings-from-source-X for >24h.*

**F3 — Cookie expiry on LinkedIn-auth (high × medium).** `LI_AT_COOKIE` expires silently. The 4 enabled `li_auth_*` boards return 0 postings. Runner doesn't know if it's "no India staff jobs this week" or "auth dead". The user finds out when re-checking the sheet 5 days later. *Implication: distinguish "0 rows because filter" from "0 rows because dead". Health probe must validate the auth path actually runs through.*

**F4 — User actually applies to <5 jobs/week (medium × very high).** The whole pipeline ships zero applications. Tailor button works, PDF uploads, but the *last mile* (open Drive, copy URL, paste into employer portal, write cover letter, hit submit) is still manual and friction-heavy. The bottleneck is human, not data. *Implication: measure applications-per-week as the only metric that actually matters; everything upstream is leading indicator.*

**F5 — Concentration regression (medium × medium).** Naukri + LinkedIn-auth start producing 80% of useful India rows. Then the user's `applied` rate concentrates in 5 companies (Razorpay, Cred, Postman, Atlan, Walmart-India). The 200-company funnel is theatre; the user could have skipped the ATS reprobes and still converted at the same rate. *Implication: applications-per-source/week tells you if the breadth is real.*

**F6 — System A drift breaks tailor in week 6 (low × very high).** A change in resume-builder breaks the cli-tailor.js contract; processor 500s; user can't tailor; whole funnel becomes look-only. *Implication: contract test in System B that mocks the cli signature and runs nightly.*

**F7 — User starts ignoring the morning swipe (high × very high).** The "15-min triage" becomes "5-min skim" becomes "I'll do it tomorrow" because the queue is too noisy / too large / too repetitive. The product dies of indifference, not technology. *Implication: the brutal one — ranking matters more than supply.*

**F8 — Sources-roadmap.md becomes a wishlist museum (medium × low).** Tier 1/2/3 ideas accumulate; nothing ships; doc grows but funnel doesn't. *Implication: prune the roadmap doc itself; keep only the next 3 things.*

**Pre-mortem big surfacings:**

13. **Source-health alerting**: any source returning 0 postings for >24h after previously returning >0 → push notification or Sheet-cell red flag.
14. **Distinguish auth-dead from filter-empty** — `last_scrape_status` already exists; make it surface in webapp header when ≥2 sources are dead.
15. **Application-throughput metric** — `applied`/week as the north-star. Every other metric serves it.
16. **Per-source applications-per-week panel** — kills sources that don't convert, regardless of yield.
17. **Contract test for System A bridge** — mocks `cli-tailor.js` interface; nightly cron; alerts on regression.
18. **Prune sources-roadmap.md** — collapse §4 tiers into "next 3".

---

## Technique 4 — First Principles ("strip to load-bearing")

Question: *what is this system actually for?*

**Stripped to atoms:**
- **Goal:** the user submits ≥N applications/week to roles where (geo=India-or-remote) AND (seniority=4-9yr SWE/staff/founding) AND (resume_match strong) AND (company hires their stack) AND (the user finds it interesting).
- **Constraint:** user has 15 min/morning + 1 evening session for tailoring.
- **Money:** essentially zero (no paid proxy, no LLM-per-row).
- **What's load-bearing:**
  1. The sheet (truth + auditable trail).
  2. The morning swipe-triage UX (the only human-in-loop step).
  3. The tailor pipeline (the only thing that produces a deliverable).
  4. The processor heartbeat / restart watchdog (the only thing that keeps tailor working unattended).
- **What's NOT load-bearing despite occupying mindshare:**
  1. The 67 boards. Could be 12 boards if the right 12 are picked.
  2. The 12 source types. The user doesn't care if the source is greenhouse vs ashby vs lever; only the URL and JD body matter.
  3. The `resume_strong` count==3 threshold. Arbitrary; ought to be calibrated against actual swipe-rights.
  4. The fancy comp parser. Comp is rarely the deciding factor in swipe-right vs swipe-skip; resume_match and city are.
  5. Most of `tag_rules.yaml` beyond the 6-7 rules that fire on >5% of rows.

**Implications (raw):**

19. **The 12-board minimum viable funnel** — pick the 12 sources whose `applied` count was highest in last 30 days. Run only those for one week. If apps/week stays flat, the other 55 boards are theatre. If it drops, you've measured the long-tail value.
20. **The "is this row useful?" classifier is binary, not graded.** The 0.0–1.0 `resume_match` score is a feature; what we ought to learn is the *threshold* the user actually swipes-right at. From the 13 ready + 27 rejected: compute the mean & sd of `resume_match` for ready vs rejected. If non-overlapping, set a hard cutoff and drop everything below before write.
21. **`tag_rules.yaml` is over-engineered relative to what's needed.** Audit: which tags change a swipe outcome? If `application_eng` fires on 60% of rows but doesn't predict swipe-right, it's noise.
22. **The processor's reliability is more important than the scout's coverage.** A scout outage = 1 day of stale rows. A processor outage = the user can't apply. Yet most engineering attention has gone to scout.
23. **Drive uploads are an artifact, not a feature.** If you're applying via copy-paste anyway, you only need the PDF locally. Drive uploads add OAuth complexity for ergonomic gain that's rarely measured. (Don't remove; just stop investing in.)

---

## Technique 5 — Mind Map (lever inventory across 8 axes)

A breadth pass to surface levers the linear list missed. Each axis has 5–10 candidate moves; not all are good.

```
                                     NEXT ACTIONS
                                          │
   ┌─────────┬──────────┬───────────┬─────┴─────┬────────────┬──────────┬──────────┐
SOURCES   FILTERS    RANKING       UX        TOOLING       MEASURE    GROWTH    QUALITY
```

- **Sources axis** — Naukri detail endpoint; iimjobs; AmbitionBox; mobile-UA probes (Instahyre/Cutshort/Wellfound); Twitter/X founder-hiring scrape; YC waas rewrite; per-company custom (Swiggy/Zomato/PhonePe); reverse-discovery via "company X moved ATS"; sitemap-driven discovery on Naukri's own sitemap.xml; **InfoEdge family unification** (one scraper, three feeds).
- **Filters axis** — geography quota; per-source weight; role-pattern exclude (already shipped); seniority floor (already implicit in resume_match); compensation floor (already partly there via `comp_ok`); language-of-JD detection (skip JDs that are 80% Hindi/non-English); company-size band; funding-stage tag (using a static company-DB CSV; cheap).
- **Ranking axis** — taste multiplier from swipe history; per-company multiplier; recency boost; "company hires my stack often" boost; explicit user thumbs-up/down on swipe; **decaying score** so old `new` rows automatically drop in priority; resume-version-aware scoring.
- **UX axis** — 3-button swipe; bulk-skip-by-company button; "show me only the top 20" button; keyboard shortcuts; PWA install prompt (vision says it's there); evening-mode dark theme; 15-min timer / queue countdown; **swipe streak counter** as gentle gamification; "why this match?" explanation drawer.
- **Tooling axis** — auto-archive in runner; sheet-aware per-company replacement; weekly health job (combined verify + reprobe + archive); make targets for common ops; structured logging exporter; Apple Shortcut "open today's queue"; Telegram daily summary message; CLI tool to pin a job to top.
- **Measurement axis** — apps/week north-star; per-source apps/week; per-source swipe-right rate; queue-age histogram; first-pass-yield (rows that survive from new → applied); swipe-rate per session-minute; **time-to-first-application after row landed**; tailor success rate per role-type.
- **Growth axis** — share read-only URL with one trusted reviewer for sanity-check; weekly digest to email; export pretty PDF report of week's funnel; pitch the open-source `naukri.py` finding as a blog post (free distribution); add another user (separate `LI_AT_COOKIE` per user, `chat_id`-keyed sheet rows).
- **Quality axis** — JD-body completeness check (>200 chars); duplicate-title detection within company; non-English JD filter; AI-generated job posting detection (some are sham); contact recruiter-name extraction; dedup against the user's own past applications across companies (avoid re-pitching).

**Mind map big surfacings (newly distinct):**

24. **Decaying score on `new` rows** — multiply `resume_match` by `0.95^days_since_discovered` so old un-triaged rows naturally fall to the bottom. Self-cleaning queue, no archive needed.
25. **Bulk-skip-by-company button** — one click, skip the remaining N rows of company X this batch. The current per-company cap caps *creation*; you also need a one-click cap on *consumption*.
26. **"Why this match?" drawer** — surface the 3-5 skill matches and the resume_match number. Lets the user calibrate the score, which then informs whether to tighten the threshold.
27. **Queue-age histogram in webapp header** — "23 rows < 24h, 14 rows 1-3d, 8 rows >3d". One number, instantly tells you if you're keeping up.
28. **Telegram daily morning summary** — "12 new India senior roles ready, 4 are resume_strong. Top match: Razorpay Backend Lead." Pull the user into the swipe loop with zero web-app friction.
29. **Per-stack hiring radar** — using last-30-day data, "your top stacks in current openings: Go (45 roles, ↑8), Python (33 roles, ↓12), Rust (4 roles, =)". Tells the user what to skill-bias toward.

---

## Technique 6 — What If (constraint inversion)

Quick rapid-fire thought experiments.

**WI-1: What if the user MUST apply to 5 jobs/week — no excuses?**
- The bottleneck stops being supply and becomes the *application-action loop itself*. The system would need: (a) one-click "open employer portal", (b) clipboard with cover-letter blanks, (c) calendar block enforcement, (d) end-of-week shame metric.
- Idea: **Application-action blocker** — the morning swipe UI refuses to load until you've marked your first `applied` of the week. Hostile, but enforces the contract.

**WI-2: What if we had infinite Claude budget?**
- Per-row "would-Deepesh-swipe-right?" classifier trained on 13 ready + 27 rejected → cheap to fine-tune on Sonnet/Haiku → run as a *pre-write* gate. Sheet only sees rows the classifier predicts ≥0.6 swipe-right probability.
- Adaptive query expansion (Tier 3 #12 from sources-roadmap) — every Sunday Claude proposes 5 new search queries from the previous week's `applied` rows.
- Per-row JD summarization → "this role is asking for X, you have Y, gap is Z" panel.

**WI-3: What if we had to delete 50 of the 67 enabled boards tomorrow — which 17 stay?**
- Forcing the question reveals which sources actually pay rent. From the 30-day data:
  - Keep all 8 naukri (India coverage is the goal).
  - Keep 4 li_auth + 5 li_unauth (highest-diversity India tail).
  - Keep ~~3 Indian-founded ATS~~: razorpay, postman, atlan/groww/meesho.
  - Keep ~~2 global-with-India-office~~: walmart, mongodb (highest India-job count).
  - Keep hasjob (small but high-quality indie India).
  - **Verdict: 17 selected.** Everything else (Databricks/Stripe/Anthropic/etc.) is mostly US-located and low swipe-right despite high count.

**WI-4: What if the daily cron only ran weekly?**
- Forces the question: how time-sensitive *is* a senior IC role posting? Answer: most India roles are open ≥7 days. Daily cron primarily catches Naukri/LinkedIn freshness; for Workday/Ashby it's overkill.
- Idea: **per-source cron schedule** — Naukri/LinkedIn daily; ATS sources twice/week; aggregators (Arbeitnow/RemoteOK) weekly. Cuts compute by 60%, no data loss.

**WI-5: What if we couldn't use the sheet at all — only the user's email inbox?**
- Forces the question: what is the user actually *receiving*? The sheet is a workbench, not a delivery surface. The morning interaction could be a single email digest with 5 swipe-right candidates and PDF-tailor-now buttons.
- Idea: **morning email digest** — top-5 unswipe rows by `resume_match`, with one-click links into the swipe UI deep-linked to the row. Either sheet OR email becomes a delivery channel.

**WI-6: What if the user could only see one number on the dashboard?**
- That number is **applications submitted this week**. Everything else is plumbing.

**What If big surfacings:**

30. **"Would-I-swipe-right" classifier** as a pre-write gate. Trains on existing 40 labels.
31. **Per-source cron schedules** — match cron frequency to source freshness rate. ~60% compute saving.
32. **Morning email digest with deep links** — top 5 candidates via email, not "go to webapp".
33. **Forcing function 17-board minimum** — run for 2 weeks; if apps/week stable, prove the long-tail isn't paying.

---

## Synthesis — the 7 strongest NEW ideas

Filtering the 33 raw ideas above for novelty (not in the orchestrator's original 5) and conviction.

**N1 — Per-source `relevance_yield` ranking + auto-demotion.**
Compute, for each board, the fraction of last-30-day rows where (`status ∈ {tailor, ready, applied}` OR `target_city` is India) over total rows produced. Boards <10% yield go `enabled: false`. Replaces "auto-archive" framing with "auto-prune at the source" — the cheapest filter is the one upstream.

**N2 — `weight: 0.0–1.0` per-board field replacing binary enabled.**
Migrates boards.yaml from inclusion-list to allocation-list. One YAML column, ~30 lines of runner code, immediate behavioral lever for geography balance.

**N3 — Taste multiplier from swipe history.**
Per-company multiplier learned from `applied/ready` vs `rejected/skip` over last 90 days. Multiplies `resume_match` at write-time. Zero LLM, zero new infra, learns in the background. Compounds with N1+N2.

**N4 — Decaying score on `new` rows.**
`effective_score = resume_match * 0.95^days_since_discovered`. The 756-row queue self-cleans without archive surgery. Old never-triaged rows fall to the bottom organically.

**N5 — 3-button swipe UX (`tailor` / `save-for-later` / `skip`).**
Adds a `saved` status bucket. Reduces "what do I do with this maybe?" thrash that today bottlenecks the 15-min morning session and inflates queue lifetime.

**N6 — Application-throughput metric as north-star.**
Display `applications-this-week` in the webapp header. Track per-source apps/week (not just yield). All upstream optimization decisions get judged against this number. Replaces "swipe-right rate" as the metric, since swipe-right is itself a proxy.

**N7 — Source-health alerting (auth-dead vs filter-empty distinction).**
Flag any source returning 0 postings >24h after previously returning >0. Surface in webapp header. Particularly load-bearing for Naukri (okhttp UA could close any time) and LinkedIn-auth (cookie expiry is silent today).

**Honorable mentions** (strong but second-tier):
- **N8 — Two-cron schedule** (7am India / 9am global). Lower 429 risk, faster morning queue.
- **N9 — Skip_archive negative corpus mining** for `exclude.yaml` candidates. Free signal in 3,328 rows.
- **N10 — InfoEdge family unification** (Naukri sitemap + AmbitionBox + iimjobs as one source class).
- **N11 — Mobile-UA probe of Instahyre/Cutshort/Wellfound** generalizing the okhttp finding.
- **N12 — Morning email digest** of top 5 candidates with deep-links. Pull-vs-push delivery channel.

---

## Prioritized actionables (ICE-scored)

Scoring: Impact (1–10), Confidence (1–10), Ease (1–10, higher = easier). ICE = I × C × E / 10. Effort in hours.

| ID | Idea | I | C | E | ICE | Effort | Prereq | Risk |
|---|---|---|---|---|---|---|---|---|
| N4 | Decaying score on `new` rows | 7 | 9 | 9 | 56.7 | 1h | none | none — pure scoring change |
| N3 | Taste multiplier from swipe history | 9 | 7 | 8 | 50.4 | 2h | Sheet has enough labels (40) — borderline; revisit at 100 | Cold-start: small N can mis-learn; cap multiplier ∈ [0.5, 2.0] |
| N1 | `relevance_yield` per-source + auto-demote | 9 | 8 | 7 | 50.4 | 2-3h | none | accidentally killing a low-volume but high-value board → keep `weight=0.2` floor not full disable |
| N6 | Apps/week north-star metric in header | 7 | 9 | 9 | 56.7 | 1h | none | metric drives bad behavior if user games it; mitigate by also tracking quality (response rate) |
| N7 | Source-health alerting + Sheet flag | 8 | 8 | 7 | 44.8 | 2h | structured logs already there | needs a non-spam delivery channel; webapp header is enough |
| N5 | 3-button swipe UX | 8 | 6 | 5 | 24.0 | 4h | new `saved` status enum value | adds a status the user must manage; defer until N4 proves queue-pruning by score is insufficient |
| N2 | `weight` field replacing binary enabled | 7 | 7 | 6 | 29.4 | 4h | boards.yaml schema migration | YAML breakage; mitigate with tests + 1-week soak |
| N8 | Two-cron schedule (7am/9am) | 5 | 8 | 8 | 32.0 | 1h | launchd plist edit | None — easy reversal |
| N9 | Skip_archive negative corpus → exclude.yaml | 6 | 8 | 8 | 38.4 | 2h | archive done (it is) | overfitting to 1 archive; require N≥3 instances of company |
| N10 | InfoEdge family unification | 6 | 6 | 5 | 18.0 | 4h | iimjobs/AmbitionBox probe | scope creep — defer to next session |
| N11 | Mobile-UA probe (Instahyre/Cutshort/Wellfound) | 7 | 5 | 8 | 28.0 | 1h each | Naukri pattern proven | unlikely to work first try, but cheap to find out |
| N12 | Morning email digest | 6 | 6 | 5 | 18.0 | 4h | top-5 selection logic; SMTP/SES wired | another channel to maintain; defer |

**Top 4 by ICE:**
1. **N4 Decaying score** — 1 hour, near-certain win, immediate visible effect. SHIP FIRST.
2. **N6 Apps/week header** — 1 hour, sets the metric the rest of the work answers to.
3. **N3 Taste multiplier** — 2 hours, compounds with N4. The swipe history goes from passive log to active feedback.
4. **N1 Per-source `relevance_yield`** — 2-3 hours, the *real* India-pivot move. Subtraction over addition.

These 4 together = ~6-7 hours, replace the orchestrator's "telemetry + sheet-aware cap + auto-archive + Naukri detail" stack with something that *changes ranking* rather than *changes archive thresholds*.

---

## Things to STOP doing

Surfaced by Five Whys + First Principles + Pre-mortem:

**S1 — Stop adding boards as the default lever.** The 67 enabled / 21 disabled / 6 commented-out structure is already past the point where adding board #68 changes anything. The next 5 sessions should add zero boards unless a probe shows ≥3 swipe-rights/week from the candidate. Default move is *prune*, not *add*.

**S2 — Stop investing in non-India aggregators (Arbeitnow especially).** 175 rows of EU jobs is queue pollution. Either disable or down-weight to 0.2.

**S3 — Stop framing the queue as a coverage problem.** It's a relevance-density-per-swipe-minute problem. Every "make funnel bigger" idea has near-zero priority right now; every "make ranking smarter" idea has near-max priority.

**S4 — Stop treating `boards.yaml` as a graveyard for historical 404s.** Move `enabled: false` entries with notes like "404 — try different slug" into a separate `boards.disabled-archive.yaml`. The live config should fit on one screen.

**S5 — Stop treating swipe-right rate as the north-star.** It's a leading indicator. Applications-submitted/week is the actual metric. The orchestrator's #1 (precision telemetry — swipe-right rate) is real but not load-bearing in isolation.

**S6 — Stop the Naukri detail-endpoint hunt.** It's a 30-min curiosity, not a load-bearing task. The current `jobDescription` is sufficient for the swipe decision; full body only matters at tailor-time, and the JD URL is one click away. The only reason this is on the list is because Naukri-curiosity is fresh in the orchestrator's mind, not because it pays.

---

## Honest assessment of orchestrator's original 5

| # | Original | Verdict | Why |
|---|---|---|---|
| 1 | **Precision telemetry** — per-source swipe-right rate (~1h) | **KEEP, but reframe as N6** | Same data; better metric. Apps-per-source-per-week subsumes swipe-right and ties directly to north-star. |
| 2 | **Sheet-aware per-company cap** — replace lowest-score with higher-score (~2h) | **DEMOTE** | Solves a symptom (queue size). N4 (decaying score) + N1 (relevance yield) attack root cause. Revisit only if N4+N1 prove insufficient. |
| 3 | **Auto-archive in runner** (~30min) | **KEEP, but cheap** | Pre-mortem F1 says this is existential — 5,000-row sheet kills the swipe UX. Wire it at the same time as N1. Effort is small enough not to debate. |
| 4 | **Naukri detail endpoint test** (~30min) | **DROP** | S6 above — curiosity, not value. Defer indefinitely. |
| 5 | **Cheap source adjacencies** (iimjobs, AmbitionBox, yc_waas) | **DEMOTE — hold for next session** | S1 above. The funnel is supply-saturated relative to user time-budget. Add a source only after N1+N3 prove the existing funnel is being fully utilized. |

**Recommended replacement stack (ordered):**

1. **N4 Decaying score on `new` rows** (1h) — immediate queue self-cleaning.
2. **N6 Apps-per-week header + per-source apps panel** (1h) — install the right metric before optimizing.
3. **Auto-archive in runner** (orchestrator's #3, 30min) — existential per pre-mortem.
4. **N1 Per-source `relevance_yield` + auto-demote** (2-3h) — the real India-pivot.
5. **N3 Taste multiplier from swipe history** (2h) — compounds with N4.
6. **N7 Source-health alerting** (2h) — protects the wins above.

Total ~9 hours. No new sources, no new scrapers, no new infra, no boards.yaml expansion. **The next strategic move is subtraction and learning, not addition.**

---

## Meta — what this brainstorm changed about the question

The orchestrator framed the question as **"what's next on the build queue?"**. The brainstorm reframed it as **"what is the system trying to optimize, and is the build queue serving that?"**.

Answer: the system is trying to *maximize India-relevant senior IC applications submitted per week subject to the user's 15-min morning attention budget*. Most of the recent shipping (Naukri, LinkedIn-auth, ATS reprobes) increases supply; the user's constraint is attention, not supply. The next phase compounds attention through ranking, not supply through sources.

The single most valuable surfacing: **N4 + N3 + N1 together turn the swipe history into an active feedback loop that the architecture has, until now, only used as a passive log.**
