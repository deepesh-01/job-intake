"""Workday public job-board source. No auth needed — Workday tenants
expose a public job-search REST endpoint at:

    POST https://<tenant>.<sub>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs

The list response gives title/location/postedOn/externalPath/bulletFields
per posting but NOT the full JD body. Per-posting body fetches are 1 req
each, so we cap at MAX_PER_BOARD to keep run time reasonable. Resume_match
will be lower-fidelity for Workday rows than for Greenhouse/Lever/Ashby —
the trade-off for unlocking Adobe (Bangalore), NVIDIA (Bangalore/Hyderabad),
and other Workday-only employers.

board_id is encoded as `<tenant>:<sub>:<site>` so the YAML stays simple:

    - { slug: adobe, source_type: workday, board_id: adobe:wd5:external_experienced }
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import httpx

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "workday"

# Workday rejects non-browser User-Agents with 400 Bad Request, so we
# present as a generic browser. Etiquette: 1 req/sec throttle (set on the
# host downstream) compensates for the deception.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)

# Cap per board to keep scout run time bounded — Workday tenants can have
# thousands of postings; we only need recent senior-IC ones to bubble up
# anyway, and the runner's 7-day first-run cutoff filters more.
MAX_PER_BOARD = 300
PAGE_SIZE = 20  # Workday rejects limit > 20 with 400 Bad Request.


def fetch(board_id: str) -> list[Posting]:
    """`board_id` is `<tenant>:<sub>:<site>`."""
    parts = board_id.split(":")
    if len(parts) != 3:
        raise SourceError(
            SOURCE_TYPE,
            board_id,
            f"expected 'tenant:sub:site', got {board_id!r}",
        )
    tenant, sub, site = parts
    base = f"https://{tenant}.{sub}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    url = f"{base}/jobs"
    detail_base = f"{base}/job"

    out: list[Posting] = []
    offset = 0
    try:
        while offset < MAX_PER_BOARD:
            _throttle(f"{tenant}.{sub}.myworkdayjobs.com")
            r = httpx.post(
                url,
                json={
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": "",
                    "appliedFacets": {},
                },
                timeout=20.0,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
            r.raise_for_status()
            data = r.json()
            page = data.get("jobPostings") or []
            if not page:
                break
            for j in page:
                p = _parse_listing(j, board_id, tenant, sub, site, detail_base)
                if p:
                    out.append(p)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    except httpx.HTTPError as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e
    return out


def _parse_listing(
    j: dict[str, Any],
    board_id: str,
    tenant: str,
    sub: str,
    site: str,
    detail_base: str,
) -> Posting | None:
    title = (j.get("title") or "").strip()
    external_path = j.get("externalPath") or ""
    if not title or not external_path:
        return None
    location = (j.get("locationsText") or "").strip() or None
    posted_at = _parse_workday_date(j.get("postedOn"))
    posting_id = external_path.rsplit("/", 1)[-1] or external_path

    # The Workday job page URL is the parent careers site + externalPath.
    public_url = f"https://{tenant}.{sub}.myworkdayjobs.com/{site}{external_path}"

    # bulletFields are tiny tagline strings ("Featured", "Remote-friendly", etc).
    # Concatenate with title as a coarse JD body — it's enough for tag firing
    # but resume_match will be lower for Workday rows than ATS rows.
    bullets = j.get("bulletFields") or []
    snippet = " · ".join([title] + [str(b) for b in bullets if b])
    jd_html = f"<h1>{title}</h1><p>{location or ''}</p><p>{snippet}</p>"

    return Posting(
        id=f"{SOURCE_TYPE}:{tenant}:{posting_id}",
        company=tenant,
        role=title,
        location=location,
        comp_string=None,
        posted_at=posted_at,
        link=public_url,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )


def _parse_workday_date(s: Any) -> date | None:
    """Workday returns 'Posted Today', 'Posted X Days Ago', or an ISO date."""
    if not s:
        return None
    s = str(s)
    if "today" in s.lower():
        return date.today()
    # "Posted X Days Ago" → today - X days; rough but bounded
    import re
    m = re.search(r"(\d+)\s+day", s, re.IGNORECASE)
    if m:
        from datetime import timedelta
        return date.today() - timedelta(days=int(m.group(1)))
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None
