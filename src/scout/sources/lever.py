"""Lever public postings API. No auth required."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "lever"
_BASE = "https://api.lever.co/v0/postings/{board_id}"


def fetch(board_id: str) -> list[Posting]:
    try:
        data = get_json(_BASE.format(board_id=board_id), params={"mode": "json"})
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    if not isinstance(data, list):
        raise SourceError(SOURCE_TYPE, board_id, "unexpected response shape")
    return [_parse(j, board_id) for j in data]


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id") or j.get("lever_id") or "")
    company = board_id  # Lever doesn't return company name in posting
    role = (j.get("text") or "").strip()
    cats = j.get("categories") or {}
    loc = cats.get("location") or cats.get("allLocations")
    if isinstance(loc, list):
        location = ", ".join(loc) if loc else None
    elif isinstance(loc, str):
        location = loc or None
    else:
        location = None
    created = j.get("createdAt")
    posted_at: date | None = None
    if isinstance(created, (int, float)):
        # Lever createdAt is unix ms
        posted_at = datetime.fromtimestamp(created / 1000, tz=timezone.utc).date()
    link = j.get("hostedUrl") or j.get("applyUrl") or ""

    # Description may be HTML in `description` plus structured `lists`
    desc = j.get("description") or ""
    lists = j.get("lists") or []
    parts = [desc]
    for lst in lists:
        if isinstance(lst, dict):
            parts.append(f"<h3>{lst.get('text', '')}</h3>")
            parts.append(lst.get("content") or "")
    jd_html = "\n".join(p for p in parts if p)

    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location,
        comp_string=None,
        posted_at=posted_at,
        link=link,
        jd_html=jd_html,
        source_type=SOURCE_TYPE,
        board_id=board_id,
    )
