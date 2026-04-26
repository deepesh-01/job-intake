"""YC Work at a Startup. Uses the unofficial endpoint the site itself calls.

High volume (200+ postings); caller is expected to apply application_eng
filter and exclude.yaml before insert. Auto-fires `early_stage` tag downstream.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "yc_waas"
_URL = "https://www.workatastartup.com/api/jobs"


def fetch(board_id: str = "ALL") -> list[Posting]:
    try:
        data = get_json(_URL, params={"limit": 500})
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    jobs = data.get("jobs") or data.get("results") or []
    if not isinstance(jobs, list):
        # Some shapes: {"data": {"jobs": [...]}}
        jobs = (data.get("data") or {}).get("jobs", [])
    out: list[Posting] = []
    for j in jobs:
        out.append(_parse(j, board_id))
    return out


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id") or j.get("jobId") or j.get("slug") or "")
    company = (j.get("company_name") or (j.get("company") or {}).get("name") or "").strip()
    role = (j.get("title") or j.get("role") or "").strip()
    location = (j.get("location") or j.get("locations") or "") or None
    if isinstance(location, list):
        location = ", ".join(location) or None
    posted_at = _parse_date(j.get("posted_at") or j.get("created_at"))
    link = j.get("url") or j.get("apply_url") or ""
    jd_html = j.get("description") or j.get("description_html") or ""
    comp_string = j.get("compensation") or j.get("salary") or None
    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company or board_id,
        role=role,
        location=str(location) if location else None,
        comp_string=str(comp_string) if comp_string else None,
        posted_at=posted_at,
        link=link,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )


def _parse_date(s: Any) -> date | None:
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s, tz=timezone.utc).date()
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except ValueError:
        return None
