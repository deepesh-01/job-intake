"""Greenhouse public job-board API client. No auth required."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "greenhouse"
_BASE = "https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"


def fetch(board_id: str) -> list[Posting]:
    url = _BASE.format(board_id=board_id)
    try:
        data = get_json(url, params={"content": "true"})
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    jobs = data.get("jobs") or []
    out: list[Posting] = []
    for j in jobs:
        out.append(_parse(j, board_id))
    return out


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id", ""))
    company = (j.get("company_name") or board_id).strip()
    role = (j.get("title") or "").strip()
    location = None
    loc = j.get("location") or {}
    if isinstance(loc, dict):
        location = (loc.get("name") or "").strip() or None
    elif isinstance(loc, str):
        location = loc.strip() or None
    posted_at = _parse_date(j.get("updated_at") or j.get("first_published") or j.get("created_at"))
    link = j.get("absolute_url") or ""
    jd_html = j.get("content") or ""
    # Greenhouse returns content with HTML entities encoded; selectolax handles it
    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location,
        comp_string=None,  # extracted later by extract.parse_comp from JD body
        posted_at=posted_at,
        link=link,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        # Greenhouse uses ISO 8601 with timezone; strip to date
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
