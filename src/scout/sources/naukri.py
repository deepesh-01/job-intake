"""Naukri.com — India's largest job board.

**Status (verified 2026-04-29): WORKING via mobile-app User-Agent.**

The web `/jobapi/v3/search` endpoint is gated by Akamai Bot Manager when
called with a browser User-Agent (returns 406 reCAPTCHA). The same endpoint,
called with the Naukri Android app's `okhttp/4.12.0` User-Agent, **returns
HTTP 200 with full JSON results** — Akamai's BMP profile whitelists the
mobile-app fingerprint. **No auth, no cookies, no Playwright required.**

Empirically validated 2026-04-29: 19,562 jobs returned for the broad query
"senior software engineer" + "bengaluru" with no auth.

board_id format (semicolon-separated key=value):
    kw   = keywords (e.g. "senior backend engineer")
    loc  = location text (e.g. "bengaluru" or "bangalore" or "india")
    exp  = years of experience as `min-max` (e.g. "4-9" for senior IC)
    pages = max search pages, each = 20 results (default 2; cap 5)
    sort = "f" (freshness, default) or "r" (relevance)

Identity: `naukri:ALL:<jobId>`. Naukri's `jobId` is stable across the API
and the public `/jobs/<jobId>` URL.

Fragility note: this works because Akamai's BMP rule on this endpoint
treats `okhttp/...` as legitimate mobile traffic. If Naukri tightens that
rule, the fix is one of (a) update to whatever new UA the Android app
ships with, (b) MITM the app to capture current required headers, or
(c) switch to xvertile/akamai-bmp-generator (Go subprocess) for a
mobile sensor payload. None require Playwright.

If you set NAUKRI_COOKIE (your `nauk_at` JWT), it is sent as Bearer for
personalized results — but plain unauth works fine for general search.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "naukri"
SEARCH_URL = "https://www.naukri.com/jobapi/v3/search"
_HOST = "www.naukri.com"

# The unlock: Naukri's Android app uses OkHttp; Akamai BMP whitelists it.
# Tested chrome/chrome131/chrome136 with full cookies → all 406. okhttp/4.12.0
# with no cookies → 200. As of 2026-04-29 the deployed Naukri app pins to
# 4.x; bump if/when Naukri updates and this UA starts to fail.
_OKHTTP_UA = "okhttp/4.12.0"

# Naukri's API expects these app-identification headers; values are
# permissive — every appid 108–1109 returned 200 in the empirical sweep.
# Using web's `appid=109; systemid=Naukri` for parity with the rest of
# the boards.yaml documentation; switch to a mobile-specific id only if
# Naukri ever starts gating on header consistency.
_REQUIRED_HEADERS = {
    "appid": "109",
    "systemid": "Naukri",
    "clientid": "d3skt0p",
}

PAGE_SIZE = 20
MAX_PAGES = 5
DEFAULT_PAGES = 2
SEARCH_TIMEOUT_S = 20.0


def _parse_board_id(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in (s or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def fetch(board_id: str) -> list[Posting]:
    parsed = _parse_board_id(board_id)
    keyword = parsed.get("kw", "").strip()
    if not keyword:
        raise SourceError(SOURCE_TYPE, board_id, "missing kw= in board_id")
    location = parsed.get("loc", "").strip()
    exp = parsed.get("exp", "").strip()
    pages = min(MAX_PAGES, max(1, int(parsed.get("pages", str(DEFAULT_PAGES)))))
    sort_mode = parsed.get("sort", "f")

    headers = {
        "User-Agent": _OKHTTP_UA,
        "Accept": "application/json",
        **_REQUIRED_HEADERS,
    }
    nauk_at = os.environ.get("NAUKRI_COOKIE", "").strip()
    if nauk_at:
        # Personalized results when authenticated. Optional — unauth works fine.
        headers["Authorization"] = f"Bearer {nauk_at}"

    today = date.today()
    seen_ids: set[str] = set()
    out: list[Posting] = []

    for page in range(1, pages + 1):
        qp: dict[str, Any] = {
            "noOfResults": PAGE_SIZE,
            "urlType": "search_by_keyword",
            "searchType": "adv",
            "keyword": keyword,
            "pageNo": page,
            "sort": sort_mode,
            "k": keyword,
        }
        if location:
            qp["location"] = location
            qp["l"] = location
        if exp:
            qp["experience"] = exp

        _throttle(_HOST)
        try:
            r = httpx.get(
                SEARCH_URL,
                params=qp,
                headers=headers,
                timeout=SEARCH_TIMEOUT_S,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            raise SourceError(SOURCE_TYPE, board_id, f"http error: {e}") from e

        if r.status_code == 429:
            break
        if r.status_code == 406:
            # Akamai started gating again — UA may need bumping.
            raise SourceError(
                SOURCE_TYPE, board_id,
                "HTTP 406: Akamai BMP gate reactivated. Update _OKHTTP_UA "
                "in src/scout/sources/naukri.py to current Naukri Android "
                "app's OkHttp version (check Play Store for latest).",
            )
        if r.status_code != 200:
            raise SourceError(
                SOURCE_TYPE, board_id,
                f"HTTP {r.status_code}: {r.text[:200] if r.text else ''}",
            )

        try:
            data = r.json()
        except Exception as e:
            raise SourceError(SOURCE_TYPE, board_id, f"non-json response: {e}") from e

        listings = data.get("jobDetails") or []
        if not listings:
            break

        for j in listings:
            p = _parse_job(j, board_id, today=today)
            if p is None:
                continue
            if p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            out.append(p)

        if len(listings) < PAGE_SIZE:
            break

    return out


def _parse_job(j: dict[str, Any], board_id: str, *, today: date) -> Posting | None:
    job_id = str(j.get("jobId") or "").strip()
    title = (j.get("title") or "").strip()
    company = (j.get("companyName") or "").strip()
    if not job_id or not title or not company:
        return None

    # placeholders → location (salary moved to salaryDetail below)
    location: str | None = None
    for ph in j.get("placeholders") or []:
        ptype = (ph.get("type") or "").lower()
        label = (ph.get("label") or "").strip()
        if ptype == "location" and label:
            location = label
            break

    # salaryDetail — structured comp from the API. Naukri returns minimum
    # / maximum in INR rupees (not lakhs). hideSalary=true means the
    # employer chose not to disclose; treat as None.
    comp_string: str | None = None
    sd = j.get("salaryDetail") or {}
    if isinstance(sd, dict) and not sd.get("hideSalary"):
        min_sal = sd.get("minimumSalary")
        max_sal = sd.get("maximumSalary")
        currency = sd.get("currency") or j.get("currency") or "INR"
        if isinstance(min_sal, (int, float)) and isinstance(max_sal, (int, float)) \
                and (min_sal > 0 or max_sal > 0):
            comp_string = f"{currency} {int(min_sal):,}-{int(max_sal):,}"

    # createdDate is ms since epoch (not days-old as one might guess from
    # the field name).
    posted_at: date | None = None
    created = j.get("createdDate")
    if isinstance(created, (int, float)) and created > 0:
        try:
            posted_at = datetime.fromtimestamp(created / 1000).date()
        except (OSError, ValueError):
            posted_at = None
    elif isinstance(created, str) and created.isdigit():
        try:
            posted_at = datetime.fromtimestamp(int(created) / 1000).date()
        except (OSError, ValueError):
            posted_at = None

    jd_url = j.get("jdURL") or ""
    if jd_url and not jd_url.startswith("http"):
        jd_url = "https://www.naukri.com" + jd_url
    if not jd_url:
        jd_url = f"https://www.naukri.com/job-listings-{job_id}"

    desc_html = (j.get("jobDescription") or "").strip()
    skills = (j.get("tagsAndSkills") or "").strip()
    exp_text = (j.get("experienceText") or "").strip()
    jd_html = (
        f"<h1>{title}</h1>"
        f"<p>Company: {company}</p>"
        f"<p>Location: {location or ''}</p>"
        f"<p>Experience: {exp_text}</p>"
        f"<p>Skills: {skills}</p>"
        f"<p>{desc_html}</p>"
    )

    return Posting(
        id=f"{SOURCE_TYPE}:ALL:{job_id}",
        company=company,
        role=title,
        location=location,
        comp_string=comp_string,
        posted_at=posted_at,
        link=jd_url,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )
