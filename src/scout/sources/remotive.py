"""Remotive public JSON API. No auth. Curated remote postings.

API: GET https://remotive.com/api/remote-jobs?category=software-dev
Returns {jobs: [...]}. Each posting has: id, title, company_name,
candidate_required_location, salary, description, url, publication_date,
tags, job_type.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "remotive"
_URL = "https://remotive.com/api/remote-jobs"


def fetch(board_id: str = "ALL") -> list[Posting]:
    # Restrict to engineering categories — Remotive returns ~1000 postings
    # across all categories otherwise.
    try:
        data = get_json(_URL, params={"category": "software-dev"})
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    jobs = data.get("jobs") or []
    return [_parse(j, board_id) for j in jobs]


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id") or j.get("url") or "")
    company = (j.get("company_name") or "").strip() or board_id
    role = (j.get("title") or "").strip()
    location = (j.get("candidate_required_location") or "").strip() or "Remote"
    posted_at = _parse_date(j.get("publication_date"))
    link = j.get("url") or ""
    desc = j.get("description") or ""
    salary = (j.get("salary") or "").strip() or None

    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location,
        comp_string=salary,
        posted_at=posted_at,
        link=link,
        jd_html=desc,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )


def _parse_date(s: Any) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except ValueError:
        return None
