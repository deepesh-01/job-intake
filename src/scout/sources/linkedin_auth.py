"""LinkedIn — authenticated (cookie-based) variant.

Companion to `linkedin.py` (unauth, ~50 req/run budget). Uses the user's
`li_at` session cookie from a logged-in LinkedIn browser session, which
unlocks a much higher rate-limit budget (empirically ~1000+ req/hour) and
returns cards that include India-only MNC postings the unauth endpoint
omits.

**TOS-grey** for personal use against your own account. Do not parallelize
or share the cookie. Cookie expires every ~365 days; refresh by logging
out + back in to LinkedIn and copying `li_at` from devtools (Application →
Cookies → linkedin.com).

Setup:
    export LI_AT_COOKIE='AQEDA...'   # the value of the `li_at` cookie
    # Optional: export LI_JSESSIONID='ajax:1234'  # for some endpoints

board_id format (semicolon-separated key=value, same shape as `linkedin`):
    kw   = keywords
    loc  = location text
    tpr  = time posted (r604800 = past week, r86400 = past 24h)
    wt   = workplace type (1=onsite, 2=remote, 3=hybrid; comma-sep allowed)
    exp  = experience level (4=mid-senior, 5=director, 6=executive)
    geo  = geoId (optional, override loc)
    details = 'false' to skip per-posting JD body fetches (cheaper)
    pages = max paginated pages (default 1; each page = 25 cards)

Identity: `linkedin_auth:ALL:<urn_numeric>`. Dedups against `linkedin:`
rows via the existing fuzzy dedup on (normalize_company, normalize_role).

Example board_ids (India-focused):
    kw=senior backend engineer;loc=Bengaluru;tpr=r604800;exp=4,5;pages=2
    kw=staff software engineer;loc=India;tpr=r604800;wt=2,3;exp=4,5
    kw=engineering manager;loc=Bengaluru;tpr=r604800;exp=5,6
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "linkedin_auth"

# Use the same `jobs-guest` endpoint as `linkedin.py` (returns parseable
# inline HTML cards) but pass `li_at` as a cookie. The auth'd `/jobs/search/`
# SPA returns a 1.4 MB shell that loads cards via XHR after JS — not useful
# without a headless browser. With li_at cookie on the guest endpoint we get
# cards directly AND a higher rate-limit budget (the unauth budget is what
# trips ~50 reqs; auth'd users see ~10x that).
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PUBLIC_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}/"

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_HOST = "www.linkedin.com"

# Authenticated rate budget is far higher; cap defensively to avoid
# tripping LinkedIn's session-anomaly detection.
MAX_POSTINGS_PER_BOARD = 100
DETAIL_TIMEOUT_S = 15.0
SEARCH_TIMEOUT_S = 25.0
_PAGE_SIZE = 25  # LinkedIn paginates in 25s


def _cookies() -> dict[str, str]:
    li_at = os.environ.get("LI_AT_COOKIE", "").strip()
    if not li_at:
        raise SourceError(
            SOURCE_TYPE, "?",
            "LI_AT_COOKIE env var not set — see linkedin_auth.py module docstring",
        )
    out = {"li_at": li_at}
    js = os.environ.get("LI_JSESSIONID", "").strip()
    if js:
        out["JSESSIONID"] = js
    return out


def _parse_board_id(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in (s or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _build_search_params(parsed: dict[str, str], start: int = 0) -> dict[str, Any]:
    qp: dict[str, Any] = {"start": start}
    if "kw" in parsed:
        qp["keywords"] = parsed["kw"]
    if "loc" in parsed:
        qp["location"] = parsed["loc"]
    if "tpr" in parsed:
        qp["f_TPR"] = parsed["tpr"]
    if "wt" in parsed:
        qp["f_WT"] = parsed["wt"]
    if "exp" in parsed:
        qp["f_E"] = parsed["exp"]
    if "geo" in parsed:
        qp["geoId"] = parsed["geo"]
    return qp


def _polite_get(
    url: str,
    *,
    cookies: dict[str, str],
    params: dict[str, Any] | None = None,
    timeout: float = SEARCH_TIMEOUT_S,
) -> str | None:
    _throttle(_HOST)
    try:
        r = httpx.get(
            url,
            params=params,
            cookies=cookies,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        raise SourceError(SOURCE_TYPE, "?", f"http error: {e}") from e
    if r.status_code == 429:
        return None
    if r.status_code in (401, 403):
        raise SourceError(
            SOURCE_TYPE, "?",
            f"HTTP {r.status_code}: cookie likely expired — refresh LI_AT_COOKIE",
        )
    if r.status_code != 200:
        raise SourceError(
            SOURCE_TYPE, "?",
            f"HTTP {r.status_code}: {r.text[:200] if r.text else ''}",
        )
    return r.text


def _parse_cards(html: str) -> list[dict[str, str]]:
    """Parse jobs-guest HTML cards. Same shape as linkedin.py::_parse_cards."""
    out: list[dict[str, str]] = []
    tree = HTMLParser(html)
    for card in tree.css("div.base-card"):
        urn = card.attributes.get("data-entity-urn", "") or ""
        urn_id = urn.rsplit(":", 1)[-1] if urn else ""
        if not urn_id.isdigit():
            continue

        title_el = card.css_first("h3.base-search-card__title")
        company_el = card.css_first("h4.base-search-card__subtitle a")
        loc_el = card.css_first("span.job-search-card__location")
        link_el = card.css_first("a.base-card__full-link")
        time_el = card.css_first("time")

        out.append({
            "urn": urn_id,
            "title": title_el.text(strip=True) if title_el else "",
            "company": company_el.text(strip=True) if company_el else "",
            "location": loc_el.text(strip=True) if loc_el else "",
            "link": (link_el.attributes.get("href") or "") if link_el else "",
            "posted_at": (time_el.attributes.get("datetime") or "") if time_el else "",
        })
    return out


def fetch(board_id: str) -> list[Posting]:
    parsed = _parse_board_id(board_id)
    fetch_details = (parsed.get("details", "true").lower() != "false")
    pages = max(1, int(parsed.get("pages", "1") or "1"))
    cookies = _cookies()

    all_cards: list[dict[str, str]] = []
    for page in range(pages):
        qp = _build_search_params(parsed, start=page * _PAGE_SIZE)
        html = _polite_get(SEARCH_URL, cookies=cookies, params=qp)
        if html is None:
            # 429 — bail. Empty result is fine, runner handles it.
            break
        cards = _parse_cards(html)
        if not cards:
            break
        all_cards.extend(cards)
        if len(cards) < _PAGE_SIZE:
            break
    if not all_cards:
        return []

    # Dedup within batch by urn (search across pages may overlap)
    seen_urns: set[str] = set()
    unique_cards: list[dict[str, str]] = []
    for c in all_cards:
        if c["urn"] in seen_urns:
            continue
        seen_urns.add(c["urn"])
        unique_cards.append(c)
    unique_cards = unique_cards[:MAX_POSTINGS_PER_BOARD]

    postings: list[Posting] = []
    for c in unique_cards:
        company = c["company"] or "(unknown)"
        role = c["title"]
        if not role:
            continue
        posted_at = _parse_date(c["posted_at"])
        link = c["link"] or PUBLIC_VIEW_URL.format(job_id=c["urn"])
        postings.append(Posting(
            id=f"{SOURCE_TYPE}:ALL:{c['urn']}",
            company=company,
            role=role,
            location=c["location"] or None,
            comp_string=None,
            posted_at=posted_at,
            link=link,
            jd_html="",
            source_type=SOURCE_TYPE,
            board_id=board_id,
        ))

    if fetch_details:
        for p in postings:
            urn = p.id.split(":")[-1]
            jd_html = _polite_get(
                DETAIL_URL.format(job_id=urn),
                cookies=cookies,
                timeout=DETAIL_TIMEOUT_S,
            )
            if jd_html is None:
                # 429 — leave remaining empty and bail; runner handles missing body.
                break
            p.jd_html = jd_html

    return postings


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
