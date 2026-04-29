---
stepsCompleted: ['init', 'parallel-research', 'empirical-validation', 'implementation', 'verification']
inputDocuments: ['docs/sources-roadmap.md', 'src/scout/sources/naukri.py']
workflowType: 'research'
lastStep: 5
research_type: 'technical'
research_topic: 'Naukri.com Akamai BMP bypass from Python without browser automation'
research_goals: 'Find a working path to access Naukri jobapi/v3/search from a daily Python cron — no Playwright, no paid proxy, no CAPTCHA service.'
user_name: 'Deepeshz2'
date: '2026-04-29'
web_research_enabled: true
source_verification: true
status: 'RESOLVED — implementation shipped and verified'
---

# Research Report: Naukri Akamai BMP Bypass

**Date:** 2026-04-29
**Author:** Deepeshz2
**Research Type:** technical / scraping infrastructure
**Status:** ✅ **RESOLVED** — `okhttp/4.12.0` User-Agent bypasses the gate. Implementation shipped to `src/scout/sources/naukri.py`, all 8 boards in `boards.yaml` enabled, 160-posting smoke test passed.

---

## TL;DR

Naukri's `/jobapi/v3/search` endpoint returns 406 reCAPTCHA when called with a browser User-Agent — Akamai Bot Manager gates it. Three independent bypass paths were investigated; **the simplest one works**: change the User-Agent to `okhttp/4.12.0` (Naukri's Android app pattern). No auth, no cookies, no `nkparam`, no TLS-fingerprint impersonation, no headless browser. **One-line config change** in the source module unlocked 19,562 jobs on a single broad query.

---

## Methodology

Four parallel research streams launched on 2026-04-29:

1. **Akamai BMP bypass research** (general-purpose agent, web search) — examined `_abck` vs `bm_sv` cookie roles, open-source sensor-data generators, current state of v2/v3 sensor reverse-engineering.
2. **Naukri alternative endpoints** (general-purpose agent, web fetch) — probed mobile site, alternate subdomains, sitemap, RSS, recruiter-side, sibling InfoEdge properties (AmbitionBox, iimjobs).
3. **Open-source Naukri scrapers** (general-purpose agent, GitHub search) — found NopeRi, JobSpy, codebasics/job-scrapper, gist-level approaches.
4. **Empirical probing from this session** — alternative subdomains, mobile UA variants, header combo sweep.

Stream 4 found the answer first: a single mobile-app User-Agent (`okhttp/4.12.0`) returns HTTP 200 where every browser UA returns 406.

---

## Empirical findings

### What was tested before the breakthrough

| Approach | Result |
|---|---|
| `httpx` with no auth, browser UA | 406 reCAPTCHA |
| `httpx` + valid `nauk_at` JWT (Bearer) + browser UA | 406 reCAPTCHA |
| `httpx` + `nauk_at` cookie + full Sec-Ch-Ua-* / Sec-Fetch-* Chrome 146 headers + HTTP/2 | 406 reCAPTCHA |
| `curl_cffi` (impersonate=chrome \| chrome120 \| chrome131 \| chrome136) + valid auth + full cookie jar (NKWAP, _t_ds, persona, **bm_sv**, nauk_rt, J, _t_r, _t_s, _t_us, is_login, MYNAUKRI[UNID], nauk_at, nauk_otl, nauk_sid, test) + warmup GET to homepage | 406 reCAPTCHA |

Conclusion before breakthrough: Akamai BMP is gating on something beyond cookies + TLS fingerprint. The user's exported jar had `bm_sv` but no `_abck`, and `_abck` is generated from sensor-data telemetry that requires JS execution.

### The breakthrough

| Approach | Result |
|---|---|
| `httpx`, **no auth, no cookies**, `User-Agent: okhttp/4.12.0`, `appid: 109`, `systemid: Naukri` | **HTTP 200, 19,562 jobs** |

Verified across 17 different `appid/systemid` combinations (108-1109, "Naukri" / "Naukri-mobile" / "Naukri-Android" / "naukri") — **all 17 returned HTTP 200**. The gate is on the User-Agent string alone; Akamai BMP whitelists `okhttp/...` traffic on this endpoint, presumably because Naukri's Android app uses OkHttp and the BMP rule was tuned to never block legitimate mobile traffic.

### End-to-end verification

8 board configurations × 1 page (20 results each) = **160 postings** parsed correctly with `companyName`, `title`, `location`, `placeholders`, `salaryDetail` (structured INR amounts), `createdDate` (ms-since-epoch), `jobDescription` (HTML-formatted body), `tagsAndSkills`, and `jdURL`.

Sample postings surfaced (Bengaluru senior backend, freshness-sorted): Incred (Backend Lead, INR 25-45 LPA), HCLTech (Golang Developer, INR 19-34 LPA), Wissen Technology (Senior Java Developer), ti Steps (Senior SWE — AI Backend Systems, INR 16-27.5 LPA), Bounteous × Accolite (Senior Java Developer, hybrid Bengaluru).

---

## Why the simpler approach beats what other repos do

The most credible existing open-source approach — [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi) (last push 2026-04-15) — uses a 12-line RSA-PKCS1_v1_5 generator to produce `Nkparam` headers, with a hardcoded 512-bit public key. That works, but is brittle: when Naukri rotates the public key, every NopeRi-pattern client breaks until reverse-engineered again. [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy/tree/main/jobspy/naukri) hardcodes a single static `Nkparam` token and is already broken (issue #301 open since Sep 2025).

The okhttp UA path bypasses the `Nkparam` requirement entirely — Akamai's BMP rule short-circuits before the application-layer signature check. **Why this is more robust than `nkparam` rotation:** Naukri would have to decide to start gating its own Android app's traffic to break this path, and the moment they do, the fix is to copy the new app version's UA string from Play Store / APKMirror — a 30-second update.

---

## Fallback paths if the okhttp UA path is closed

Documented for future reference; not currently needed.

### Fallback A — Sitemap + AmbitionBox (no auth needed, lower fidelity)

Naukri's `https://www.naukri.com/sitemap/sitemap.xml` is **NOT Akamai-gated** and lists every fresh `/job-listings-{slug}-{14digitId}` URL with `lastmod`. The single JD pages return HTML skeletons (no inline JSON) when fetched without a logged-in browser session, but [AmbitionBox](https://www.ambitionbox.com) (InfoEdge-owned, same job postings, ungated, returns HTML with `application/ld+json` JobPosting blocks) is a serviceable shadow. [iimjobs.com](https://www.iimjobs.com) (also InfoEdge, ungated, senior/management-tier focus) is a complementary feed.

### Fallback B — `nkparam` RSA generator (NopeRi pattern)

12 lines of pycryptodome RSA-PKCS1_v1_5 encryption of `f"v0|{ms_timestamp}|121_{page_type}"` with NopeRi's hardcoded public key, base64-encoded into the `Nkparam` request header. Combine with `Authorization: Bearer <nauk_at>` for personalized results. Caveats: IP-bound (residential or AWS Elastic IP only — Azure/GitHub Actions IPs are burned), key may rotate.

### Fallback C — Mobile sensor data generation (last resort)

[xvertile/akamai-bmp-generator](https://github.com/xvertile/akamai-bmp-generator) (Go, last push 2026-04-21, 336 stars) generates valid mobile-BMP sensor payloads. Run as a subprocess from Python. Heaviest option, only needed if Akamai escalates from web-UA gating to full mobile-sensor enforcement.

---

## Implementation

`src/scout/sources/naukri.py` rewritten 2026-04-29:

- **Drop** `_build_cookies()` and the `NAUKRI_COOKIE` requirement entirely.
- **Switch** User-Agent to `_OKHTTP_UA = "okhttp/4.12.0"`.
- **Keep** `NAUKRI_COOKIE` env var as an OPTIONAL Bearer header for personalized results — works fine without it.
- **Fix** parser bug: `createdDate` is ms-since-epoch (not days-old as previously assumed); use `datetime.fromtimestamp(value/1000)`.
- **Use** structured `salaryDetail` (`{minimumSalary, maximumSalary, currency, hideSalary}`) for comp parsing instead of placeholder text scraping. Yields exact INR ranges directly.
- **Pull** explicit `experienceText` / `minimumExperience` / `maximumExperience` fields into the JD body for downstream tagging.
- **Add** explicit error message on 406 telling the engineer to bump the OkHttp version (with pointer to Play Store / APKMirror).

`boards.yaml` updated: all 8 `naukri_*` entries flipped from `enabled: false` to `enabled: true`. Existing `NAUKRI_COOKIE` in `.env` is harmless (used as optional Bearer when present).

---

## Operational implications

- **Daily 9am IST cron unblocked.** No cookie expiry to manage. No refresh-token rotation. No browser session warmup.
- **Throughput:** 8 boards × 2 pages × 20 results = 320 raw postings/run. After dedup (cross-board overlap, fuzzy dedup vs 30-day window), expect ~200-250 unique India-located postings/run.
- **Per-company cap (`_PER_COMPANY_INTRA_BATCH_CAP=7`)** prevents Wissen Technology / consultancy floods.
- **Failure mode if Naukri tightens:** explicit `SourceError` with actionable message ("update _OKHTTP_UA in src/scout/sources/naukri.py to current Naukri Android app's OkHttp version"). Fix is one line.
- **Monitor:** add `naukri_*` boards to `scripts/verify_boards.py` weekly cron output to catch regressions early.

---

## Sources

### Primary (used in implementation)

- Empirical probe (this session, 2026-04-29) — first request with `okhttp/4.12.0` UA returned HTTP 200 + 19,562 jobs
- Direct API response inspection (this session) — confirmed schema: `noOfJobs`, `jobDetails[].jobId/title/companyName/placeholders/salaryDetail/createdDate/jobDescription/tagsAndSkills/jdURL`

### Secondary (cross-validation)

- [Traverser25/NopeRi](https://github.com/Traverser25/NopeRi) — Python Naukri client using `nkparam` RSA generator (still working April 2026 per its README); used as evidence that Naukri's Akamai gate is reachable from plain `requests` without TLS impersonation
- [speedyapply/JobSpy `naukri` module](https://github.com/speedyapply/JobSpy/tree/main/jobspy/naukri) — hardcoded `Nkparam` approach; broken in 2026 per open issue #301; useful header reference
- [codebasics/job-scrapper Naukri client](https://github.com/codebasics/job-scrapper/blob/main/code/src/scraper/services/naukri_api_client.py) — `httpx` + Playwright-harvested cookies pattern; documents `appid: 109; systemid: Naukri; clientid: d3skt0p` headers and the v4 detail endpoint `/jobapi/v4/job/{id}`
- [arv-anshul Naukri gist](https://gist.github.com/arv-anshul/ea66e7e9601777387ae898c96947eaa5) — pre-Akamai-tightening simple httpx approach (broken in 2026)
- [Akamai v3 sensor data deep dive — glizzykingdreko](https://medium.com/@glizzykingdreko/akamai-v3-sensor-data-deep-dive-into-encryption-decryption-and-bypass-tools-da0adad2a784) — context on why direct sensor-data generation is impractical
- [How to bypass Akamai when web scraping in 2026 — Scrapfly](https://scrapfly.io/blog/posts/how-to-bypass-akamai-anti-scraping) — context on `_abck` cookie role and TLS fingerprint requirements
- [_abck Cookie explained — Captain Compliance](https://captaincompliance.com/education/_abck/) — cookie semantics
- [Naukri robots.txt (live)](https://www.naukri.com/robots.txt) — confirmed no public bulk feed exists; sitemaps are the only documented bulk path
- [Naukri sitemap index (live)](https://www.naukri.com/sitemap/sitemap.xml) — fallback discovery oracle if API path closes
- [xvertile/akamai-bmp-generator](https://github.com/xvertile/akamai-bmp-generator) — Go-based mobile BMP sensor generator, last-resort fallback

---

## Open questions / future work

- **Detail endpoint `/jobapi/v4/job/{id}`** — codebasics/job-scrapper uses this for full JD bodies. Worth probing with `okhttp/4.12.0` UA to see if it's also bypassed; if yes, can replace the truncated `jobDescription` with full body.
- **Pagination beyond pages=5** — current cap is conservative. With auth (`NAUKRI_COOKIE` Bearer) we may get more relevant per-user pages; worth measuring after a few daily runs.
- **Other InfoEdge sites** (iimjobs, AmbitionBox) — could become diversification sources without any auth complexity.
- **Per-source precision telemetry** — Mary's earlier ask. Should land before adding more high-volume sources to verify whether Naukri's 200-250/run is signal or noise.
