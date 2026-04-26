"""Arbeitnow public JSON API. No auth. EU-heavy but lots of remote roles
that hire globally including India.

API: GET https://www.arbeitnow.com/api/job-board-api
Returns {data: [...]}. Each posting has: slug, company_name, title,
description (HTML), remote (bool), location, url, tags, created_at.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "arbeitnow"
_URL = "https://www.arbeitnow.com/api/job-board-api"


def fetch(board_id: str = "ALL") -> list[Posting]:
    try:
        data = get_json(_URL)
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    jobs = data.get("data") or []
    return [_parse(j, board_id) for j in jobs]


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("slug") or j.get("url") or "")
    company = (j.get("company_name") or "").strip() or board_id
    role = (j.get("title") or "").strip()
    is_remote = bool(j.get("remote"))
    raw_loc = (j.get("location") or "").strip()
    location = "Remote" if is_remote and not raw_loc else raw_loc or ("Remote" if is_remote else "")
    posted_at = _parse_date(j.get("created_at"))
    link = j.get("url") or ""
    desc = j.get("description") or ""

    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location or None,
        comp_string=None,  # Arbeitnow doesn't return structured comp
        posted_at=posted_at,
        link=link,
        jd_html=desc,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )


def _parse_date(s: Any) -> date | None:
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc).date()
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except ValueError:
        return None
