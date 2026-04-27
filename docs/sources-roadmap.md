# Source Roadmap — Why the Funnel Skews Tech-Brand-Heavy & How to Fix It

*Owner: Deepesh · Status: proposal · Last updated: 2026-04-27*

> Sibling docs:
> - [`vision.md`](./vision.md) — product vision
> - [`decisions.md`](./decisions.md) — ADR log
> - [`how-to-journey.md`](./how-to-journey.md) — operational guide
> - [`tasks.md`](./tasks.md) — chronological build log

---

## 1 · TL;DR

The current funnel pulls **3,273 jobs from 173 companies**, but the top 5 companies (Databricks, Nvidia, Anthropic, Stripe, Adobe) produce **46% of the total**. Only **11% of postings** are India-located — despite India being the user's primary geography. The pipeline is heavily skewed toward **public greenhouse/workday/ashby/lever boards belonging to high-profile US tech companies**, because those are the ATSes with clean public JSON APIs.

LinkedIn — which has the broadest, most diverse opening set including Indian SMBs, GCCs, and non-tech companies hiring engineers — is currently capped at **30 jobs across 5 fixed search queries**. That's the biggest unused lever.

This doc inventories what's wired up, explains the structural bias, and proposes specific moves to widen the funnel.

---

## 2 · Current source inventory

### 2.1 Source types implemented

| Source type | Class | Mechanism | What it gives us |
|---|---|---|---|
| `greenhouse` | ATS | Public JSON (`boards-api.greenhouse.io`) | High-quality data, US tech-heavy |
| `workday` | ATS | Public JSON (`/wday/cxs/<tenant>/.../jobs`) | Big enterprise (Adobe/Nvidia/Walmart/Autodesk) — large India offices |
| `ashby` | ATS | Public JSON (`api.ashbyhq.com/posting-api`) | AI-native + dev-tools |
| `lever` | ATS | Public JSON (`api.lever.co/v0/postings`) | Mixed (Cred, Meesho, Paytm + US) |
| `linkedin` | Board | Unauth guest endpoint, HTML scrape | Highest diversity, **rate-limited** (~50 reqs/run) |
| `hasjob` | Indie aggregator | Atom feed | India-focused early-stage (16-50 postings) |
| `remoteok` | Aggregator | JSON | Remote-only |
| `remotive` | Aggregator | JSON | Remote-only |
| `arbeitnow` | Aggregator | JSON | Mostly EU/remote |
| `hn_hiring` | Aggregator | HN API + LLM extraction | YC-style; only days 1–3 of month |
| `yc_waas` | Aggregator | (broken) | Endpoint 404'd; needs rewrite |

Source files: `src/scout/sources/*.py` · Boards config: `boards.yaml`

### 2.2 Actual yield (from current sheet, 3,273 rows)

```
greenhouse      1,565   (47.8%)
workday           971   (29.7%)
ashby             369   (11.3%)
lever             175   ( 5.3%)
remoteok           69   ( 2.1%)
arbeitnow          69   ( 2.1%)
linkedin           30   ( 0.9%)   ← biggest under-utilization
hasjob             13   ( 0.4%)
remotive           12   ( 0.4%)
```

86% of postings come from the four corporate ATSes (greenhouse/workday/ashby/lever). LinkedIn — the source with the richest tail of non-FAANG, non-unicorn, mid-market and Indian companies — is **<1%** of the funnel.

### 2.3 Top 25 companies (concentration view)

```
Databricks      400    Cohere          83    cursor          45
Nvidia          300    Twilio          78    replit          44
Anthropic       292    Paytm           77    meesho          40
Stripe          268    Notion          64    Webflow         27
Adobe           261    Postman         64    sentry          22
Autodesk        250    Discord         50    supabase        21
Datadog         173    Plaid           50    modal           20
Walmart         160    cursor          45
Scale AI        100    replit          44
Figma            87
```

The top 5 = 46% of the funnel. The top 25 = ~75%. The remaining 148 companies share the bottom 25%. This is a **power-law concentration** problem — the funnel optimizes for "famous tech company that publishes a Greenhouse board," not "company hiring engineers who match Deepesh's profile."

### 2.4 Geography skew

- **India-located postings: 363 (11.1%)**, despite India being the primary geography
- Top non-Indian locations: Santa Clara (110), San Francisco (84+82+74+50 = ~290 across format variants), Tokyo (50), New York (52)
- India sources by yield:
  ```
  greenhouse     121   (Razorpay/Postman/Groww/Mongo BLR — global cos with India offices)
  lever          115   (Cred, Meesho, Paytm)
  workday         78   (Adobe/Nvidia/Walmart Bangalore — same global cos)
  linkedin        30   (capped)
  hasjob          12   (indie startups)
  ```
  Most "India" rows are actually **US companies' India offices**, not Indian-founded companies. Indian-founded coverage is essentially Cred + Meesho + Paytm + Razorpay + Postman + Groww + Atlan + Hasjob's tail. That's ~10 companies.

---

## 3 · Why the funnel is biased

### 3.1 The structural reason: ATS gravity
Public JSON APIs are easy to scrape — so the cheapest sources to add are the ones with public APIs. **Greenhouse / Workday / Ashby / Lever are over-represented among well-funded US tech companies and their global subsidiaries**, because:

- US Series-B+ startups → default to Greenhouse or Ashby
- US AI-native devtools → Ashby (modern, design-led)
- US/global enterprises → Workday
- Mid-market US/EU SaaS → Lever
- **Indian tech companies → mostly custom careers pages, Naukri, Instahyre, or LinkedIn**
- **Indian non-tech → Naukri-only**
- **Smaller global companies → LinkedIn/Indeed/Wellfound**

So when we hand-curate `boards.yaml`, the easy-to-discover boards are also the ones biased toward famous US-style tech. Our verification script (`scripts/verify_boards.py`) makes the bias *explicit*: 21 of the Indian companies probed had no public ATS API and were disabled. We literally cannot reach them through this method.

### 3.2 LinkedIn under-utilization
LinkedIn is the only source we have that:
- Reaches mid-market Indian companies, GCCs (Microsoft Hyderabad, Salesforce Bangalore), consultancies, healthcare, fintech, manufacturing-tech
- Surfaces postings from **any** company, not just ones with a public ATS
- Has rich filtering (location, experience level, posted-in-last-X, remote)

But we've capped it at **5 search queries × 25 postings = 125 potential / run**, with actual yield often under 30 because the same job dedups across queries. Reasons we capped:

1. **Rate limiting**: ~50 requests before 429
2. **TOS-grey**: unauth scraping is grey-area
3. **No fallback proxy**: when blocked, nothing else fires

The cap is rational given current infra, but it's the **single highest-leverage thing to expand** if we add a proxy layer.

### 3.3 What we're missing entirely

| Surface | Why it matters | Why we don't have it |
|---|---|---|
| **Naukri.com** | India's #1 job board. ~60% of Indian engineering listings live here exclusively. | No public API, JS-heavy, requires Selenium-class scraper |
| **Instahyre** | Curated India tech, Cred-style premium quality | Requires login |
| **Wellfound (AngelList)** | Best for Indian + global early-stage | Requires login + Cloudflare-protected |
| **Indeed (India)** | Massive volume, especially mid-market | API deprecated; site has aggressive anti-scraping |
| **Foundit / Shine** | India-specific tail | Same as Naukri |
| **Y Combinator's Work-at-a-Startup** | High-quality YC company filter | `yc_waas.py` exists but the endpoint changed; needs rewrite |
| **Cutshort** | India indie tech, similar to Hasjob but bigger | No public API |
| **Twitter/X "hiring" search** | Founder-posted "we're hiring" tweets | No clean API; filtered timeline scrape |
| **Company-specific scraping** | E.g. PhonePe/Flipkart/Swiggy/Zomato (all custom careers pages) | Each one is a one-off scraper |

### 3.4 The implicit assumption that's breaking

The current architecture assumes: **"if a company is hiring engineers worth applying to, they probably have a public ATS."** That's true for ~60% of the US tech market. It's true for maybe 15% of the Indian market. So our funnel has a **structural blind spot of ~5x** for the geography the user actually cares about.

---

## 4 · How to improve — a tiered roadmap

Each tier increases coverage but adds operational cost. Pick how aggressive based on appetite.

### Tier 0 — Free wins from the current architecture (1–2 hrs)

Things we can do right now without new infrastructure.

1. **Expand the LinkedIn search set from 5 → 20 queries.**
   - Add: senior frontend, senior fullstack, principal/distinguished, engineering manager, ML engineer (×Bangalore + ×Hyderabad + ×India remote)
   - Risk: 20 queries × ~3 search reqs each = 60 reqs, well within the 50–80 threshold *if* spaced. Add per-query throttle of 4s.
   - **Expected lift: 30 → ~250 LinkedIn jobs/run.**
2. **Re-verify and re-enable disabled boards** with current slugs. Our last sweep found that companies move ATSes (Notion → Ashby, Plaid → Lever). Run `scripts/verify_boards.py` weekly via cron and auto-disable/re-enable.
3. **Add 30+ new Workday tenants for India presence**: Salesforce, Google (`google.com/about/careers`), Microsoft, Cisco, Oracle, Citrix, Intuit, Hitachi Vantara, Schneider Electric. Each one publishes a Workday board with a Bangalore/Hyderabad office.
4. **Add Indian unicorns/well-funded that are on Greenhouse/Lever but not yet in `boards.yaml`**: Glance, Khatabook, Slice, Jupiter, Open, Vedantu, BYJUs, Unacademy, Eruditus, Pesto, Crejo, FamPay, Jar, Mensa Brands. Probe each for ATS via `verify_boards.py`.
5. **Fix `yc_waas.py`** — the YC Work-at-a-Startup endpoint moved. Single-source surfaces every YC company including the India + global early-stage ones.

### Tier 1 — One new source class (4–8 hrs)

Add a *new mechanism*, not just more boards.

6. **Naukri scraper.** Selenium/Playwright-class headless browser, polite throttle, India-only. Expected daily volume: 200–500 senior engineering postings. Highest-leverage single addition. Risk: more fragile, requires periodic selector maintenance.
7. **Wellfound scraper.** Same class as Naukri, but anti-bot is stronger. Defer to Tier 2.
8. **Twitter/X "we're hiring" scrape.** Scrape `from:builder.handle hiring OR "we're hiring"` for a curated list of ~50 Indian/global founder accounts. Yields 5–20 high-signal/week. Cheap, novel signal.

### Tier 2 — Paid proxy / browser farm (one-time setup + ongoing $)

When ad-hoc scraping hits rate limits, this becomes the unblock.

9. **Browserless.io / Bright Data residential proxy** — $50–200/mo. Routes LinkedIn + Naukri + Wellfound + Indeed through residential IPs. Removes the rate-limit cap on LinkedIn entirely. Enables **LinkedIn at 200+ queries/run** rather than 5.
10. **Re-introduce Wellfound (AngelList) source** once the proxy is in place. The original v1 design deferred this; it's still the right move once we have the unblock.
11. **Indeed (India) scraper** — Indeed's anti-scraping is the most aggressive of any board. Only worth it once we have the proxy.

### Tier 3 — LLM-augmented sourcing (more speculative)

Use Claude/Sonnet to broaden the *queries* themselves.

12. **Adaptive query expansion**: every Sunday, ask Claude "given the last week's `status=applied` and `status=ready` rows, what 5 new LinkedIn search queries would have surfaced jobs Deepesh would also like?" Auto-add them to the LinkedIn board set.
13. **Direct careers-page extraction.** Maintain a list of "interesting companies that don't use a public ATS." Run a once-a-day fetch of their careers HTML, hand it to Claude with: "Extract postings that match this resume." Cost: ~$0.01 per page. 50 pages/day = $15/mo. Wildly broader coverage.

---

## 5 · Recommended next move

If we only do one thing, **do Tier 0 #1 + #3 + #4** (a single 1–2 hr session). Concrete:

1. Expand LinkedIn search set to 20 queries with throttle bump.
2. Add 30 new Workday tenants (focus on India-office globals).
3. Re-probe + add 15 more Indian unicorns/well-funded across Greenhouse/Lever/Ashby.

That alone should triple India-coverage from 363 → ~1,000+ India-located postings, and double overall company diversity from 173 → ~250 companies — without any new infra, scraper class, or paid proxy.

Then, only if appetite remains, jump to **Tier 1 #6 (Naukri)** — that's where the real long-tail of Indian SMB engineering hiring lives, and it's the single highest-leverage source we don't yet have.

---

## 6 · Open questions

- Do we want **non-tech companies hiring engineers** (banks, airlines, healthcare with India tech offices)? They show up on Naukri/LinkedIn but barely on ATSes. The current tag system has no way to distinguish "tech-co engineer at a non-tech-co" from "engineer at a tech-co" — might need a new tag class if we open the funnel here.
- Should `boards.yaml` move to per-source-type files (`boards/greenhouse.yaml`, `boards/workday.yaml`, etc.) once we cross ~80 boards? Current single-file structure is becoming hard to scan.
- LinkedIn's per-query MAX_POSTINGS_PER_BOARD is hardcoded at 25. With pagination we could pull 75–100 per query. Worth checking whether `start=25` and `start=50` still return data on the guest endpoint.
- Should the **scout configurably skip ATS sources on rate-limit risk and front-load LinkedIn** when LinkedIn is the variable-yield source? (Run order matters when you might 429 partway through.)
