"""Instahyre — premium India tech aggregator.

**Status (verified 2026-04-29): WORKING via mobile-app User-Agent, no auth required.**

Cloudflare Bot Manager challenges browser User-Agents on `/`, `/jobs/search`,
and the public `/job-<id>-<slug>/` HTML pages (403 + JS challenge). However the
public REST endpoint `/api/v1/job_search/` is **not** behind the bot challenge
and returns clean JSON for `okhttp/4.12.0`. No cookies needed for the global
freshness feed.

Empirical recon 2026-04-29 (unauth):
- `total_count = 13627` jobs in the global pool
- Sorted descending by id (newer = larger id) — id-sorted = freshness ordering
- 35 objects per page; `meta.next` provides `?limit=35&offset=N` cursor
- All filter params (`q=`, `keywords=`, `locations=`, `experience_*`, etc.)
  are silently ignored on the unauth endpoint — every variant returns the
  same `total_count = 13627`. Filtering happens at the runner via the
  exclude pipeline + resume_match (same model as `hn_hiring` / `yc_waas`).
- Auth (`INSTAHYRE_COOKIE` -> `sessionid` cookie) **may** unlock filter
  params; build is filter-tolerant — they are passed through, harmless if
  the API ignores them, useful if/when auth honors them.

Identity: `instahyre:ALL:<id>`. The id maps 1:1 to the public job URL
(`https://www.instahyre.com/job-<id>-<slug>/`).

Field availability:
- title, company_name, locations, keywords[], public_url — always present
- comp_string, posted_at, jd_html — **NOT** in the API response. Title +
  keywords drive tags. Runner's `_enrich_one` exempts `instahyre` from the
  200-char JD-length floor for this reason.

board_id format (semicolon-separated key=value, all keys optional):
    pages = max pages to fetch (default 3, cap 10; each page = 35 results)
    q     = keyword (passed as `q=`; works only if auth honors it)
    loc   = location text (passed as `locations=`; same caveat)
    exp   = years of experience as `min-max` (passed as `experience_min`/`_max`)

Cookie setup (optional — for the personalized / filtered feed):
    Log in to instahyre.com → DevTools → Application → Cookies → instahyre.com
    → copy the `sessionid` value. Stash as `INSTAHYRE_COOKIE=...` in `.env`.
    (Django backend convention; if `sessionid` is absent, try `lat`.)
    When the cookie expires (30-day Django default), the source raises
    `SourceError` with an actionable message on the next 401/403.

Fragility note: same as Naukri — this works because Cloudflare's BM ruleset
on `/api/v1/job_search/` whitelists mobile UAs. If Instahyre tightens it, the
fallback ladder is (a) bump `_OKHTTP_UA` to current Android-app version, (b)
switch to `curl_cffi` with TLS impersonation, (c) Playwright headless. None
of (b) or (c) is implemented — defer if (a) stops working.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "instahyre"
SEARCH_URL = "https://www.instahyre.com/api/v1/job_search/"
_HOST = "www.instahyre.com"

_OKHTTP_UA = "okhttp/4.12.0"

PAGE_SIZE = 35  # fixed by the API; meta.limit confirms 35
DEFAULT_PAGES = 3
MAX_PAGES = 10
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


def _build_cookies() -> dict[str, str]:
    """Returns a cookies dict for httpx, or empty dict if no cookie configured.

    Auth is optional: the unauth feed is functional. When set, the value is
    sent as `sessionid` (Django convention).
    """
    val = os.environ.get("INSTAHYRE_COOKIE", "").strip()
    if not val:
        return {}
    return {"sessionid": val}


def _build_query_params(parsed: dict[str, str], offset: int) -> dict[str, Any]:
    qp: dict[str, Any] = {"limit": PAGE_SIZE, "offset": offset}
    if "q" in parsed and parsed["q"]:
        qp["q"] = parsed["q"]
    if "loc" in parsed and parsed["loc"]:
        qp["locations"] = parsed["loc"]
    exp = parsed.get("exp", "").strip()
    if exp and "-" in exp:
        lo, hi = exp.split("-", 1)
        if lo.strip().isdigit():
            qp["experience_min"] = int(lo.strip())
        if hi.strip().isdigit():
            qp["experience_max"] = int(hi.strip())
    return qp


def fetch(board_id: str) -> list[Posting]:
    parsed = _parse_board_id(board_id)
    pages = min(MAX_PAGES, max(1, int(parsed.get("pages", str(DEFAULT_PAGES)))))
    cookies = _build_cookies()

    headers = {
        "User-Agent": _OKHTTP_UA,
        "Accept": "application/json",
    }

    seen_ids: set[str] = set()
    out: list[Posting] = []

    for page in range(pages):
        offset = page * PAGE_SIZE
        qp = _build_query_params(parsed, offset)

        _throttle(_HOST)
        try:
            r = httpx.get(
                SEARCH_URL,
                params=qp,
                headers=headers,
                cookies=cookies,
                timeout=SEARCH_TIMEOUT_S,
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            raise SourceError(SOURCE_TYPE, board_id, f"http error: {e}") from e

        if r.status_code == 429:
            break
        if r.status_code in (401, 403):
            raise SourceError(
                SOURCE_TYPE, board_id,
                f"HTTP {r.status_code}: cookie likely expired — refresh "
                f"INSTAHYRE_COOKIE (log in at instahyre.com, copy `sessionid`).",
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

        objects = data.get("objects") or []
        if not objects:
            break

        for j in objects:
            p = _parse_job(j, board_id)
            if p is None:
                continue
            if p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            out.append(p)

        # Last page detected via meta.next or short page
        meta = data.get("meta") or {}
        if not meta.get("next") or len(objects) < PAGE_SIZE:
            break

    return out


def _parse_job(j: dict[str, Any], board_id: str) -> Posting | None:
    job_id = str(j.get("id") or "").strip()
    title = (j.get("title") or "").strip()
    employer = j.get("employer") or {}
    company = (employer.get("company_name") or "").strip()
    if not job_id or not title or not company:
        return None

    location = (j.get("locations") or "").strip() or None
    public_url = (j.get("public_url") or "").strip()
    if not public_url:
        public_url = f"https://www.instahyre.com/job-{job_id}/"

    keywords = j.get("keywords") or []
    keywords_text = ", ".join(str(k) for k in keywords if k)
    company_tagline = (employer.get("company_tagline") or "").strip()
    company_note = (employer.get("instahyre_note") or "").strip()

    jd_html = (
        f"<h1>{title}</h1>"
        f"<p>Company: {company}</p>"
        f"<p>Location: {location or ''}</p>"
        f"<p>Skills: {keywords_text}</p>"
        f"<p>Tagline: {company_tagline}</p>"
        f"<p>{company_note}</p>"
    )

    return Posting(
        id=f"{SOURCE_TYPE}:ALL:{job_id}",
        company=company,
        role=title,
        location=location,
        comp_string=None,
        posted_at=None,
        link=public_url,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )
