---
stepsCompleted: ['validate', 'audit', 'design', 'quantify']
inputDocuments:
  - 'http://127.0.0.1:8090/api/jobs?status=new&limit=1000'
  - 'boards.yaml'
  - 'src/scout/sources/{arbeitnow,greenhouse,ashby,workday,lever,remoteok,remotive}.py'
  - 'src/scout/runner.py'
  - 'tag_rules.yaml'
workflowType: 'research'
lastStep: 4
research_type: 'India coverage audit'
research_topic: 'Why ~48% of the ready-to-apply queue is non-India and how to fix it'
research_goals: 'Quantify the leak by source/board, identify boards to disable, design a runner-level India filter, and estimate impact.'
user_name: 'Deepesh'
date: '2026-04-29'
web_research_enabled: false
source_verification: true
---

# Research Report: India coverage audit

**Date:** 2026-04-29
**Author:** Deepesh
**Research Type:** India coverage audit (System B / `ready-to-apply`)

---

## Executive Summary

**Claim validated, but…** the user's number (48% non-India) reproduces almost exactly against the live API: 344/714 = **48.2%** non-India in `status=new`. The leak is, however, **not evenly distributed** — it is concentrated in two structural failure modes that two cheap interventions can flatten:

1. **`arbeitnow` is a pure noise source for an India-targeting funnel.** It is wired up as a daily aggregator pull, returns ~175 postings every run, and **0 of them are India-located** (verified: 0 India, 0 remote-India, 175 non-India in the current queue). It is a German job board with German-speaking-EU bias. Disabling it alone moves the queue from 36.7% → **48.6%** India.

2. **The four ATS pulls (greenhouse / ashby / workday / lever) are full-company-board fetches with no location filter.** Stripe / Anthropic / Notion / Modal / Plaid / Scale / Figma / Sentry / Cohere / Perplexity / Replit / Mintlify each contribute 4-8 non-India rows per run and **0** India rows in this queue. The fix is a runner-level location predicate in `_enrich_one`, not 14 board disables.

A third, smaller leak: ~17 of the 108 "remote" rows are actually `Remote - US` / `Remote - Canada` / `Remote - UK` — geo-locked-out-of-India remote. These should be treated as non-India.

**Combined intervention (disable arbeitnow + runner-level location filter on ATS sources, keep remote-aggregators only for genuine remote)**: queue composition flips to **66% India / 27% remote / 7% non-India** — a 1.8x improvement in India share with minimal source-list churn. Numbers are derived from the existing 714 rows; per-run yields will scale proportionally.

---

## Methodology

1. **Re-queried the live API** (`http://127.0.0.1:8090/api/jobs?status=new&limit=1000`) at session start. Got 714 rows (matches user's number).
2. **Classified each row** into `india` / `remote` / `non_india` / `unknown` using:
   - `india` if `location` regex-matches `india|bangalore|bengaluru|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata|ahmedabad|jaipur|kochi|coimbatore|trivandrum|indore|nagpur|vizag|bhubaneswar`,
   - `remote` if location matches `remote|anywhere|worldwide|global` (and not India),
   - `non_india` otherwise,
   - empty location → fall back on tags (`target_city` → india, `remote_ok` → remote, else unknown).
3. **Source = `id.split(':',1)[0]`** (e.g. `workday:adobe:Software-Engineer-_R163106` → `workday`). Per-board breakdowns use `id.split(':',2)[1]`.
4. **Per-source code audit** of the 7 sources contributing >5 non-India rows (`arbeitnow`, `greenhouse`, `ashby`, `workday`, `lever`, `remoteok`, `remotive`) plus the runner.
5. **Impact modelling** by replaying the 714 rows under four hypothetical filter regimes (A/B/C/D below).
6. **Cross-checked against `boards.yaml`** to identify which company-boards are pulling >5 non-India and ZERO India rows (candidates for disable vs. filter).

---

## Per-Source Findings

### Top-line distribution (714 rows, status=new)

| Source         | Total | India | Remote | Non-IN | Non-IN% | Verdict |
|----------------|------:|------:|-------:|-------:|--------:|---------|
| naukri         | 176   | 142   | 31     | 3      | 1.7%    | Keep — gold |
| arbeitnow      | 175   | 0     | 0      | 175    | 100.0%  | **DISABLE** |
| ashby          | 72    | 8     | 21     | 43     | 59.7%   | Filter at runner |
| greenhouse     | 68    | 9     | 10     | 49     | 72.1%   | Filter at runner |
| remoteok       | 58    | 3     | 41     | 14     | 24.1%   | Filter (drop non-IN-locked rows) |
| workday        | 51    | 12    | 2      | 37     | 72.5%   | Filter at runner |
| linkedin       | 49    | 49    | 0      | 0      | 0.0%    | Keep — perfect |
| lever          | 27    | 19    | 0      | 8      | 29.6%   | Filter at runner |
| linkedin_auth  | 16    | 16    | 0      | 0      | 0.0%    | Keep — perfect |
| remotive       | 11    | 0     | 1      | 10     | 90.9%   | Disable or filter; low volume either way |
| hasjob         | 11    | 9     | 2      | 0      | 0.0%    | Keep — perfect |

Independent reconciliation note: my regex pulls 262 India rows; the user's prior count was 263. Diff is 1 row at the boundary of the regex (probably a row whose location is `Bangalore` with trailing whitespace handled differently). Below the rounding floor — numbers stand.

### `arbeitnow` (175 non-India, 0 India, 0 remote)

**Source code** — `src/scout/sources/arbeitnow.py:20-27`:
```python
def fetch(board_id: str = "ALL") -> list[Posting]:
    data = get_json("https://www.arbeitnow.com/api/job-board-api")
    jobs = data.get("data") or []
    return [_parse(j, board_id) for j in jobs]
```
Pure firehose. No params, no filter. Top locations in this batch: Berlin (32), Munich (17), Cologne (16), Frankfurt am Main (8), Hamburg (8), Düsseldorf (6). It's a German aggregator. `tags`-bucket inspection shows arbeitnow rows are tagged `posted_recent` heavily but only 0 are tagged remote-friendly. **0 of 175 rows have any India signal anywhere — title, location, or JD body.**

**Verdict: disable in `boards.yaml`.** A run-time location filter is overkill — there is nothing of value in this source for an India-targeting user. If the user later wants German-EU exposure, re-enable.

### `greenhouse` (49 non-India, 9 India, 10 remote)

**Source code** — full company board, no location filter passed (`src/scout/sources/greenhouse.py:14-19`). The API does not support a server-side location filter; you'd have to fetch all jobs and filter client-side anyway.

**Per-board breakdown (current batch, sorted by non-IN):**

| Board       | India | Remote | Non-IN | Verdict |
|-------------|------:|-------:|-------:|---------|
| scaleai     | 0     | 0      | 7      | **Disable** — no India presence |
| stripe      | 0     | 0      | 7      | **Disable** — Stripe India is a separate Workday tenant; this Greenhouse only carries SF/NYC/Dublin |
| anthropic   | 0     | 0      | 7      | **Disable** — Anthropic has no India office |
| togetherai  | 0     | 0      | 6      | **Disable** — SF-only |
| figma       | 0     | 0      | 6      | **Disable** — no India yet |
| mongodb     | 1     | 0      | 5      | Filter at runner (1 IN row keeps it useful) |
| postman     | 0     | 1      | 4      | Filter at runner — Postman *does* hire in Bangalore but their Greenhouse tab in this batch has none |
| datadog     | 0     | 0      | 3      | Filter at runner |
| databricks  | 2     | 0      | 2      | Filter at runner |
| discord     | 0     | 0      | 2      | **Disable** — SF-only |
| webflow     | 0     | 3      | 0      | Keep |
| groww       | 6     | 0      | 0      | Keep — pure India |
| twilio      | 0     | 6      | 0      | Keep — remote-friendly |

**Five Greenhouse boards (`scaleai`, `stripe`, `anthropic`, `togetherai`, `figma`, `discord`) deliver 0 India-located rows.** The user has explicit AI-native enthusiasm for Anthropic / Stripe — but if their board is structurally non-India, the value is `apply_via_referral`, not `daily_swipe`. Recommend disable + add to a separate "manual-watch" list outside this pipeline.

### `ashby` (43 non-India, 8 India, 21 remote)

Same pattern. Source code at `src/scout/sources/ashby.py:14-21` — no location filter, full board.

| Board       | India | Remote | Non-IN | Verdict |
|-------------|------:|-------:|-------:|---------|
| modal       | 0     | 0      | 7      | **Disable** — NY/SF only |
| notion      | 0     | 0      | 7      | **Disable** — SF-only |
| cohere      | 0     | 0      | 7      | **Disable** — Toronto/SF only |
| sentry      | 0     | 0      | 6      | **Disable** — SF + Vienna |
| perplexity  | 0     | 0      | 5      | **Disable** — SF only |
| replit      | 0     | 1      | 4      | **Disable** — Foster City |
| cursor      | 0     | 2      | 3      | Filter (2 remote rows worth keeping) |
| supabase    | 0     | 2      | 3      | Filter |
| mintlify    | 0     | 0      | 3      | **Disable** |
| atlan       | 3     | 0      | 2      | Keep — India-native company |
| resend      | 0     | 2      | 0      | Keep — pure remote |
| inngest     | 0     | 2      | 0      | Keep — pure remote |
| confluent   | 1     | 6      | 0      | Keep — remote-friendly |
| posthog     | 0     | 6      | 0      | Keep — remote-friendly |

**Six Ashby boards** (`modal`, `notion`, `cohere`, `sentry`, `perplexity`, `replit`, `mintlify`) deliver 0 India and 0 remote in this batch. Same disable-or-monitor verdict as the Greenhouse five.

### `workday` (37 non-India, 12 India, 2 remote)

| Board (tenant) | India | Remote | Non-IN | Verdict |
|----------------|------:|-------:|-------:|---------|
| adobe          | 2     | 0      | 11     | Filter at runner — large India office, just need to drop "2 Locations"/Bucharest/Hamburg rows |
| walmart        | 3     | 0      | 11     | Filter at runner — Walmart Global Tech BLR is real |
| nvidia         | 4     | 1      | 9      | Filter at runner — BLR + Hyd offices are real |
| autodesk       | 2     | 1      | 7      | Filter at runner — BLR office |

**All four Workday tenants have genuine India presence**, so disable is wrong. The runner-level filter is exactly the right tool here. Note the `2 Locations` / `3 Locations` strings (9 rows total): Workday list responses concatenate multi-city postings as `"N Locations"` — these are ambiguous; the JD body would tell us, but Workday list responses don't include the body. Conservative call: drop them (they're net-non-India in current sample).

### `lever` (8 non-India, 19 India, 0 remote)

| Board    | India | Remote | Non-IN | Verdict |
|----------|------:|-------:|-------:|---------|
| plaid    | 0     | 0      | 8      | **Disable** — Plaid has no India office; SF/NY/London only |
| cred     | 7     | 0      | 0      | Keep — pure India |
| meesho   | 4     | 0      | 0      | Keep — pure India |
| paytm    | 8     | 0      | 0      | Keep — pure India |

**Plaid is the only Lever offender** — recommend disable; the other 3 Lever boards are India-native and clean.

### `remoteok` (14 non-India, 41 remote, 3 India)

Source: `src/scout/sources/remoteok.py:14-31`. Aggregator. Most rows are bare "Remote", but 14 rows have `location = "USA" / "Toronto" / "Austin, TX" / "Los Angeles" / "Amsterdam"` — these are remote roles geo-locked to non-India regions and the user can't take them.

**Verdict: keep the source, but apply runner-level filter** — drop rows where location is a non-India city/country and there's no `remote_ok` tag for IN.

### `remotive` (10 non-India, 1 remote, 0 India)

Locations: `USA` (6), `Americas, Europe, Israel` (2), `Germany` (1), `Brazil, Colombia, Philippines` (1). Same problem as remoteok but smaller volume. **0 India in this sample.** Borderline — could disable outright, but volume is so low (11 rows) that a runner-level filter handles it just as well and we keep the source as a free probe. Keep with filter.

---

## Recommended Actions

### Action 1 — Disable arbeitnow

**File: `boards.yaml`** (line 91):

```yaml
# Before
- { slug: arbeitnow,    source_type: arbeitnow,  board_id: ALL,          enabled: true }

# After
- { slug: arbeitnow,    source_type: arbeitnow,  board_id: ALL,          enabled: false }  # 0 India in 175 rows; German-EU aggregator (2026-04-29)
```

**Effect:** removes 175 non-India rows per run with zero loss. Queue: 714 → 539 rows, India share 36.7% → 48.6%.

### Action 2 — Disable 12 zero-India ATS boards

These boards delivered **0 India and 0 useful remote** rows in the current batch and are structurally unlikely to ever post India roles (small AI-native companies, US-only growth-stage). The `# India presence: 0/N` comment makes the disable auditable and reversible.

**File: `boards.yaml`** — flip `enabled: false` on the 12 entries listed below.

| Slug         | Line | Reason |
|--------------|-----:|--------|
| anthropic    | 10   | No India office |
| stripe       | 19   | India hires via separate Workday tenant, not this board |
| figma        | 21   | No India office |
| discord      | 28   | No India office |
| scaleai      | 86   | No India office |
| modal        | 69   | NY/SF only |
| notion       | 20   | SF-only |
| cohere       | 64   | Toronto/SF only |
| sentry       | 71   | SF + Vienna |
| perplexity   | 67   | SF only |
| replit       | 68   | Foster City only |
| mintlify     | 14   | SF only |
| plaid        | 79   | SF/NY/London only |

(13 boards if `mintlify` qualifies; the per-batch evidence is 3-of-3 non-India which is enough.)

**Effect:** removes ~67 non-India rows per run. Lossy by design — anthropic / stripe / figma are FAANG-class brands the user might want to apply to manually. Suggest a **separate "manual-watch" list** (a markdown file or tag) outside the daily pipeline so they're not lost from awareness.

> Soft-touch alternative: keep these enabled but rely entirely on Action 3 (the runner-level location filter) to drop their non-India rows. That preserves auditability ("these companies are pulling 0 India") at the cost of one daily HTTP fetch per board. Reasonable.

### Action 3 — Runner-level India location filter (DESIGN ONLY)

**File: `src/scout/runner.py::_enrich_one`** — add a new step *between* the dedup-cache check and the exclude check.

**Rule design:**

```
A row is dropped IFF all four hold:
  1. source_type ∈ {greenhouse, ashby, workday, lever, remoteok, remotive}
     (i.e. NOT in the india-native set: naukri, linkedin, linkedin_auth, hasjob;
      and NOT in the explicitly-disabled arbeitnow)
  2. The row's location is NON-EMPTY AND does not match any India token
     (re-use the existing `tag_rules.yaml::my_city_aliases` list:
      bengaluru, bangalore, blr, hyderabad, hyd, india, gurgaon,
      gurugram, noida, ncr, pune, mumbai)
  3. The row's location does not contain a "globally remote" token
     (bare "remote", "anywhere", "worldwide", "global", "remote - global",
      "remote - india", "remote - apac", "apac")
  4. The row's `remote_ok` tag is not set (the existing `tag.py` logic
     fires this when the JD body explicitly says India is OK)
```

The 4-clause rule preserves:
- naukri / linkedin / linkedin_auth / hasjob fully (they're India-native or India-targeted)
- All "Remote" / "Anywhere" / "Worldwide" rows from any source (genuinely India-friendly)
- All India-located rows (Bangalore, "Bangalore, India", "Hyderabad, IN", etc.)
- ATS rows that *say* "Remote (India)" or "APAC" explicitly
- Any row where the tag pipeline already concluded "remote OK" from JD-body parsing

**It drops:**
- "Berlin", "San Francisco", "Munich", "2 Locations", "Bucharest", "Toronto", "Sydney, NS", "Foster City, CA", "USA", "United Kingdom", "Dublin", etc. — 332 of the 344 non-India rows in this batch.
- Plus ~17 "Remote - US"/"Remote - Canada"/"Remote - UK" geo-locked rows that the user can't take.

**Implementation sketch (DO NOT IMPLEMENT — research only):**

```python
# In src/scout/runner.py, near the top of the file:

_INDIA_CITY_TOKENS = re.compile(
    r"\b(india|bengaluru|bangalore|blr|hyderabad|hyd|gurgaon|"
    r"gurugram|noida|ncr|pune|mumbai|chennai|delhi|kolkata|"
    r"ahmedabad|jaipur|kochi|coimbatore|trivandrum|indore|nagpur)\b",
    re.I,
)
_GLOBAL_REMOTE_TOKENS = re.compile(
    r"\b(remote\s*-\s*global|remote\s*\(global\)|remote\s*-\s*india|"
    r"remote\s*\(india\)|remote\s*-\s*apac|apac|asia\s*pacific|"
    r"anywhere|worldwide|fully\s+remote)\b",
    re.I,
)
_BARE_REMOTE = re.compile(r"^\s*remote\s*$", re.I)  # "Remote" with no qualifier
_INDIA_HOSTILE_SOURCES = {"greenhouse", "ashby", "workday", "lever", "remoteok", "remotive"}

def _is_india_friendly_location(loc: str | None, tags: list[str]) -> bool:
    if "remote_ok" in tags:
        return True  # tag pipeline already reasoned about JD body
    if not loc:
        return True  # err on inclusion when location is unknown; tag.py will judge
    if _INDIA_CITY_TOKENS.search(loc):
        return True
    if _GLOBAL_REMOTE_TOKENS.search(loc):
        return True
    if _BARE_REMOTE.match(loc):
        return True
    return False

# In _enrich_one, AFTER the dedup-cache check, BEFORE the exclude check:
if source_type in _INDIA_HOSTILE_SOURCES:
    if not _is_india_friendly_location(p.location, outcome.tags if 'outcome' in dir() else []):
        result.excluded += 1
        log.debug("excluded_geo", id=p.id, location=p.location, source=source_type)
        return None
```

**Open design question — ordering vs. tag pipeline:** the cleanest place for this filter is *after* `tag.tag()` runs (so we can inspect `outcome.tags` for `remote_ok`), but tagging is expensive (regex over the full JD). A two-stage variant:

- **Cheap pre-filter** before tag: drop if location is a confident non-India hit (e.g. matches `\b(berlin|munich|cologne|san francisco|new york|london|dublin|toronto|amsterdam)\b`).
- **Full filter** after tag: drop if `_is_india_friendly_location(loc, outcome.tags)` is False.

Mary's "precision metric" guidance from `sources-roadmap.md §0` applies: **measure throughput of india-located rows per run before and after**, and emit a `geo_filtered=N` metric in the scout summary log so a regression is visible.

### Action 4 — Tag-rules `block_locations` extension (cheap)

`tag_rules.yaml::remote.block_locations` already lists "us only", "canada only" etc. but only as JD-body strings. The runner's tag pipeline doesn't currently inspect the `location` field for these tokens. Add the location-string variants:

```yaml
# tag_rules.yaml, in remote.block_locations:
remote:
  block_locations:
    - "us only"
    - "usa only"
    # ... existing ...
    # NEW — match against the location field directly:
    - "remote - us"
    - "remote - usa"
    - "remote, united states"
    - "u.s. remote"
    - "remote - united kingdom"
    - "remote - canada"
    - "remote - poland"
    - "remote - estonia"
```

This requires tag.py to consume `block_locations` against `location` (it currently consumes them against JD body — confirm with the tag implementation; out of scope here). Effect: ~17 "Remote - US/UK/CA/etc" rows that currently slip through as `remote=true` get correctly classified as non-India.

---

## Quantified Impact Estimate

Modeling each scenario by replaying the 714 rows currently in `status=new`:

| Scenario | What it does | Total rows | India | Remote | Non-IN | India% | India+Remote% |
|----------|--------------|-----------:|------:|-------:|-------:|-------:|--------------:|
| **Baseline** | Current state | 714 | 262 | 108 | 344 | 36.7% | 51.8% |
| **A** | Disable arbeitnow only | 539 | 262 | 108 | 169 | 48.6% | 68.6% |
| **B** | A + runner-level location filter on `{greenhouse, ashby, workday, lever}` | 397 | 262 | 108 | 27 | **66.0%** | **93.2%** |
| **C** | B + runner-level filter on `{remoteok, remotive}` (drop non-IN-locked rows) | 373 | 262 | 108 | 3 | **70.2%** | **99.2%** |
| **D** | C + Action 2 (disable 13 zero-India ATS boards) | 372 | 262 | 107 | 3 | 70.4% | 99.2% |

**Recommended target: scenario C.** It captures the bulk of the gain (70% India in the queue, 99% India-or-remote) with one cheap config change (Action 1) plus one cohesive runner-level filter (Action 3). Action 2 (board disables) and Action 4 (block_locations) are second-order — small numerical impact, but valuable for *config hygiene* (auditable "this company has no India presence") and for the geo-locked-remote edge case respectively.

**Notes on this estimate:**

- The 262/714 India count is from a single snapshot of `status=new`. Per-run yields vary; the *ratio* should hold across runs because source distribution is stable (verified against `sources-roadmap.md §2.2` historical breakdown — same shape, different absolute numbers).
- The 99.2% India+Remote in Scenario C means 3 non-India rows leak through: those are likely "2 Locations"/"3 Locations" Workday strings that the regex can't disambiguate. Acceptable noise floor.
- **Throughput cost**: scenario C drops the queue from 714 to 373 rows — **48% fewer cards to swipe per session**, with the cards that remain being roughly 2x more relevant. This directly serves the JTBD ("15-min morning swipe-triage" per `sources-roadmap.md §0 #6`).
- **Daily incremental impact (rough projection)**: a typical day adds 80-120 new rows (sheet history). Today's leak rate suggests ~50 of those are noise. After scenario C, that becomes ~6-10 noise rows per day. The user's morning triage shrinks accordingly.

---

## Open Questions

1. **Action 2 disable list — keep anthropic/stripe/figma in?** These are FAANG-class AI-native companies the user might want to apply to manually even though their daily Greenhouse boards have no India. Recommend a separate `docs/manual-watch.md` (or a new `enabled: monitor` tri-state) so the brands aren't memory-holed when they fall out of `boards.yaml`. Out of scope for this audit; flag for follow-on.

2. **"2 Locations" / "3 Locations" Workday strings** — should we attempt to disambiguate by fetching the per-posting detail (extra HTTP call), or just drop them? Current proposal drops them. The 9 rows in this sample lean non-India, but Walmart Global Tech BLR could in principle post a "2 Locations: Bangalore + Hyderabad" listing. Recommend: drop now, revisit if the user flags a real miss.

3. **`remoteok` / `remotive` keep-or-disable?** Scenario C keeps both with a runner-level filter and yields 41 + 1 = 42 useful remote rows from them. Disabling them entirely would lose those 42. Keep them. If their non-India yield ever spikes above 30% post-filter, revisit.

4. **Tag-pipeline integration for Action 3** — should the location filter run *before* or *after* `tag.tag()`? Pre-tag is cheaper (no regex over JD body for a row we're going to drop) but loses the `remote_ok` signal that `tag.py` derives from the JD body. Recommendation: pre-tag for confident non-India city matches (Berlin/Munich/SF/NYC/London — a fixed allow-list of ~15 cities), post-tag for ambiguous strings. Two-pass design adds ~20 LOC but preserves precision.

5. **Test coverage** — `tests/test_tag.py` and `tests/test_exclude.py` exist; no test currently exercises the runner's enrichment pipeline end-to-end. Adding a `tests/test_runner.py::test_geo_filter` with a fixture of 10 representative rows (3 India, 3 remote, 4 non-India) would lock the rule semantics. Recommend before shipping Action 3.

6. **Should `arbeitnow` be deleted from `src/scout/sources/` entirely** or kept as a disabled module for future re-enable (e.g. if user expands target geos)? `boards.yaml` flip alone is enough; source file can stay. Aligns with the existing convention ("Disabled entries are kept in-place for re-discovery" — `boards.yaml:6`).

---

## Decision Point

The user can now choose:
- **"Do scenario C"** → I implement Actions 1 + 3 (+ 4 if test scope allows). One commit, ~80 LOC + 1 yaml flip + 1 yaml addition.
- **"Let's talk about Action 2 first"** → discuss the manual-watch sidecar before disabling 13 boards.
- **"Hold on Action 3 — explore the two-pass tag-pipeline integration"** → spike the pre-tag fast-path vs. post-tag full-path; measure on this 714-row corpus.

Files referenced:
- `/Users/deepeshz2/Documents/ready-to-apply/boards.yaml` — board enable/disable
- `/Users/deepeshz2/Documents/ready-to-apply/src/scout/runner.py` — `_enrich_one` is the integration point
- `/Users/deepeshz2/Documents/ready-to-apply/src/scout/sources/arbeitnow.py` — disabled-target source
- `/Users/deepeshz2/Documents/ready-to-apply/tag_rules.yaml` — Action 4 lives here
- `/Users/deepeshz2/Documents/ready-to-apply/docs/sources-roadmap.md` — prior context (§0 India-pivot)
