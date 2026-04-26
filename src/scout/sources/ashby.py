"""Ashby public job-board API. No auth required."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "ashby"
_BASE = "https://api.ashbyhq.com/posting-api/job-board/{board_id}"


def fetch(board_id: str) -> list[Posting]:
    try:
        data = get_json(_BASE.format(board_id=board_id), params={"includeCompensation": "true"})
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    jobs = data.get("jobs") or []
    return [_parse(j, board_id) for j in jobs]


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id") or j.get("jobId") or "")
    company = (j.get("company") or board_id).strip()
    role = (j.get("title") or "").strip()
    location = (j.get("locationName") or j.get("location") or "") or None
    posted_at = _parse_date(j.get("publishedAt") or j.get("updatedAt"))
    link = j.get("jobUrl") or j.get("applyUrl") or ""

    # Body may be html (`descriptionHtml`) or markdown (`descriptionPlain`)
    jd_html = j.get("descriptionHtml") or ""
    if not jd_html:
        plain = j.get("descriptionPlain") or ""
        jd_html = f"<pre>{plain}</pre>" if plain else ""

    # Ashby sometimes returns a structured compensation block — surface it raw
    comp_string = None
    comp = j.get("compensation")
    if isinstance(comp, dict) and comp.get("compensationTierSummary"):
        comp_string = comp["compensationTierSummary"]
    elif isinstance(comp, str):
        comp_string = comp

    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location,
        comp_string=comp_string,
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
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None
