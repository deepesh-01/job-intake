"""Hirist.tech — premium India tech aggregator (InfoEdge-owned).

**Status (verified 2026-04-29): WORKING via session cookies (auth required).**

Hirist (rebranded from hirist.com → hirist.tech) is fully auth-walled. The
public Next.js frontend renders empty SEO chrome; actual job content loads
via XHR to `gladiator.hirist.tech` — a LoopBack API that requires a logged-in
session. Unlike Naukri/Instahyre, no okhttp UA bypass exists — there is no
unauth path for job data.

Architecture:
- Frontend: Next.js 8.1.0 SPA (auth-gated)
- API: `https://gladiator.hirist.tech` (LoopBack, openapi spec at /explorer/)
- Working endpoint: GET /job/jobfeed (returns 50/page, JWT-cookie-gated)
- Public job URL: `https://www.hirist.tech/j/<id>`

Empirical recon 2026-04-29 (with valid cookies):
- 50 objects/page, paginates via `?page=N`
- Returns the user's *personalized* feed — server-side ranked to profile
  skills/experience. (Filter params don't change the result; they're
  ignored. The feed is what it is.)
- Field availability: id, title, introText (HTML JD body), min/max (years),
  minSal/maxSal (lakhs INR, 0 if hidden), hideSal flag, createdTimeMs,
  tags[] (skills), locations[], companyData.companyName, workFromHome flag.
  Comp visibility ~12% (most postings hide salary).

Cookie setup (REQUIRED):
    Log in to hirist.tech in browser → DevTools → Network tab → any XHR to
    gladiator.hirist.tech → copy the entire `Cookie` request-header value.
    Stash as `HIRIST_COOKIES=...` in `.env`. Required cookies:
    HIRIST_CK1 + hirist_seeker_enc (both contain the JWT) + PHPSESSID.
    JWT expires ~30 days; refresh by re-logging-in.

board_id format (semicolon-separated key=value, all keys optional):
    pages = max pages to fetch (default 3 = 150 jobs, cap 10)

Identity: `hirist:ALL:<id>`. The id maps 1:1 to `https://www.hirist.tech/j/<id>`.

Failure modes:
- 401 / 403 → cookie expired or revoked (raise SourceError with refresh hint)
- 429 → rate limited (return partial yield, scout retries next day)
- 5xx → upstream gladiator hiccup (let the runner record + skip)
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx

from lib.posting import Posting, SourceError
from scout.sources._http import _throttle

SOURCE_TYPE = "hirist"
SEARCH_URL = "https://gladiator.hirist.tech/job/jobfeed"
PUBLIC_JOB_URL = "https://www.hirist.tech/j/{id}"
_HOST = "gladiator.hirist.tech"

# A real browser UA — gladiator is fronted by Akamai BotManager (the bm_sv
# cookie is its tell). Mobile-app UA bypass that worked for Naukri did not
# unlock anything here — the auth gate is enforced at the application layer
# regardless of UA, so we just match the real frontend (browser UA + Referer).
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

PAGE_SIZE = 50  # fixed by the API
DEFAULT_PAGES = 3
MAX_PAGES = 10
SEARCH_TIMEOUT_S = 25.0


def _parse_board_id(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in (s or "").split(";"):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _cookie_header() -> str:
    val = os.environ.get("HIRIST_COOKIES", "").strip()
    if not val:
        raise SourceError(
            SOURCE_TYPE, "?",
            "HIRIST_COOKIES env var not set — see hirist.py module docstring "
            "for cookie copy instructions.",
        )
    return val


def fetch(board_id: str) -> list[Posting]:
    parsed = _parse_board_id(board_id)
    pages = min(MAX_PAGES, max(1, int(parsed.get("pages", str(DEFAULT_PAGES)))))
    cookie_header = _cookie_header()

    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json",
        "Referer": "https://www.hirist.tech/jobfeed",
        "Origin": "https://www.hirist.tech",
        "Cookie": cookie_header,
    }

    seen_ids: set[str] = set()
    out: list[Posting] = []

    for page in range(pages):
        _throttle(_HOST)
        try:
            r = httpx.get(
                SEARCH_URL,
                params={"page": page},
                headers=headers,
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
                f"HTTP {r.status_code}: cookies likely expired — refresh "
                f"HIRIST_COOKIES (log in at hirist.tech, copy Cookie header).",
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

        jobs = data.get("data") or []
        if not jobs:
            break

        for j in jobs:
            p = _parse_job(j, board_id)
            if p is None:
                continue
            if p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            out.append(p)

        if not data.get("hasMore"):
            break

    return out


def _parse_job(j: dict[str, Any], board_id: str) -> Posting | None:
    job_id = str(j.get("id") or "").strip()
    title = (j.get("title") or "").strip()
    company_data = j.get("companyData") or {}
    company = (company_data.get("companyName") or "").strip()
    if not job_id or not title or not company:
        return None

    locations = j.get("locations") or j.get("location") or []
    location_str = ", ".join(
        (loc.get("name") or "").strip()
        for loc in locations
        if isinstance(loc, dict) and loc.get("name")
    ).strip(", ") or None
    if j.get("workFromHome") and not location_str:
        location_str = "Remote"

    # Comp: minSal/maxSal are in lakhs INR. hideSal=1 means employer hid it.
    comp_string: str | None = None
    min_sal = j.get("minSal") or 0
    max_sal = j.get("maxSal") or 0
    if not j.get("hideSal") and (min_sal > 0 or max_sal > 0):
        if min_sal and max_sal:
            comp_string = f"INR {min_sal}-{max_sal} LPA"
        elif max_sal:
            comp_string = f"INR up to {max_sal} LPA"
        elif min_sal:
            comp_string = f"INR {min_sal}+ LPA"

    posted_at: date | None = None
    created_ms = j.get("createdTimeMs") or j.get("createdTime")
    if isinstance(created_ms, (int, float)) and created_ms > 0:
        try:
            posted_at = datetime.fromtimestamp(created_ms / 1000).date()
        except (OSError, ValueError):
            posted_at = None

    tags = j.get("tags") or []
    skills_text = ", ".join(
        (t.get("name") or "").strip()
        for t in tags
        if isinstance(t, dict) and t.get("name")
    )

    intro_html = (j.get("introText") or "").strip()
    exp_text = ""
    min_y, max_y = j.get("min"), j.get("max")
    if isinstance(min_y, (int, float)) and isinstance(max_y, (int, float)):
        exp_text = f"{int(min_y)}-{int(max_y)} years"

    jd_html = (
        f"<h1>{title}</h1>"
        f"<p>Company: {company}</p>"
        f"<p>Location: {location_str or ''}</p>"
        f"<p>Experience: {exp_text}</p>"
        f"<p>Skills: {skills_text}</p>"
        f"{intro_html}"
    )

    return Posting(
        id=f"{SOURCE_TYPE}:ALL:{job_id}",
        company=company,
        role=title,
        location=location_str,
        comp_string=comp_string,
        posted_at=posted_at,
        link=PUBLIC_JOB_URL.format(id=job_id),
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )
