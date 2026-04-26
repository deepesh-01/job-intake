"""LinkedIn — unauthenticated guest endpoint.

LinkedIn was deferred to v2 in the original design ("needs paid proxy");
this is the free path: their public guest job-search endpoint at
`/jobs-guest/jobs/api/seeMoreJobPostings/search`. Returns HTML cards.
No auth, no API key — but rate-limited (~50 reqs before 429) and TOS-grey.

board_id is a semicolon-separated key=value string encoding the search:

  kw   = keywords (e.g. "senior backend engineer")
  loc  = location text (e.g. "Bengaluru" or "India")
  tpr  = time posted (r604800 = past week, r86400 = past 24h)
  wt   = workplace type (1=onsite, 2=remote, 3=hybrid; comma-sep allowed)
  exp  = experience level (4=mid-senior, 5=director; comma-sep allowed)
  details = "true" (default) / "false" — fetch per-posting JD body

Example board_id: `kw=staff software engineer;loc=Bengaluru;tpr=r604800;exp=4,5`

Identity: `linkedin:ALL:<urn_numeric>`. The same job surfaced via two search
queries dedups naturally because LinkedIn's URN is universal.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "linkedin"
SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# LinkedIn discriminates against unusual UAs. Use a recent Chrome on macOS.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_HOST = "www.linkedin.com"

MAX_POSTINGS_PER_BOARD = 25  # cap to keep us under the 429 threshold
DETAIL_TIMEOUT_S = 15.0
SEARCH_TIMEOUT_S = 20.0


def _parse_board_id(s: str) -> dict[str, str]:
    """Parse `kw=...;loc=...;...` into a dict."""
    out: dict[str, str] = {}
    for chunk in (s or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _build_search_params(parsed: dict[str, str]) -> dict[str, Any]:
    qp: dict[str, Any] = {"start": 0}
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


def _polite_get(url: str, params: dict[str, Any] | None = None, timeout: float = SEARCH_TIMEOUT_S) -> str | None:
    """GET with throttle. Returns HTML on 200, None on 429 (rate-limited),
    raises on other errors. The runner catches SourceError; None lets the
    caller (detail-fetch loop) break gracefully."""
    _throttle(_HOST)
    try:
        r = httpx.get(
            url,
            params=params,
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
    if r.status_code != 200:
        raise SourceError(
            SOURCE_TYPE, "?",
            f"HTTP {r.status_code}: {r.text[:200] if r.text else ''}",
        )
    return r.text


def _parse_cards(html: str) -> list[dict[str, str]]:
    """Extract structured fields from the search-page HTML."""
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
    qp = _build_search_params(parsed)

    html = _polite_get(SEARCH_URL, params=qp)
    if html is None:
        raise SourceError(
            SOURCE_TYPE, board_id,
            "rate-limited on first request (429) — back off and try later",
        )

    cards = _parse_cards(html)[:MAX_POSTINGS_PER_BOARD]
    if not cards:
        return []

    postings: list[Posting] = []
    for c in cards:
        company = c["company"] or "(unknown)"
        role = c["title"]
        if not role:
            continue
        posted_at = _parse_date(c["posted_at"])
        # Strip locale-prefixed in.linkedin.com to canonical form
        link = c["link"] or f"https://www.linkedin.com/jobs/view/{c['urn']}/"
        postings.append(Posting(
            id=f"{SOURCE_TYPE}:ALL:{c['urn']}",
            company=company,
            role=role,
            location=c["location"] or None,
            comp_string=None,
            posted_at=posted_at,
            link=link,
            jd_html="",  # filled below if detail-fetch enabled
            source_type=SOURCE_TYPE,
            board_id=board_id,
        ))

    # Detail fetches give us the JD body so tagging (stack, comp, culture)
    # actually has something to chew on. Stop on the first 429 — no point
    # continuing to hammer.
    if fetch_details:
        for p in postings:
            urn = p.id.split(":")[-1]
            jd_html = _polite_get(
                DETAIL_URL.format(job_id=urn), timeout=DETAIL_TIMEOUT_S,
            )
            if jd_html is None:
                # 429 — leave remaining postings with empty body and bail.
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
