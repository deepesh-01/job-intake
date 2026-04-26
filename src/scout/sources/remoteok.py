"""RemoteOK public JSON API. No auth. Returns mostly remote-friendly roles
with structured comp where the company chose to disclose.

API: GET https://remoteok.com/api
First element of the array is metadata; the rest are postings. Each posting
has: id, slug, company, position, location, tags, description (HTML),
url, salary_min, salary_max, date.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "remoteok"
_URL = "https://remoteok.com/api"


def fetch(board_id: str = "ALL") -> list[Posting]:
    try:
        data = get_json(_URL)
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    if not isinstance(data, list):
        raise SourceError(SOURCE_TYPE, board_id, "unexpected response shape")
    # First element is API metadata, skip.
    postings = data[1:] if data and isinstance(data[0], dict) and "legal" in data[0] else data
    return [_parse(p, board_id) for p in postings if isinstance(p, dict)]


def _parse(j: dict[str, Any], board_id: str) -> Posting:
    posting_id = str(j.get("id") or j.get("slug") or "")
    company = (j.get("company") or "").strip() or board_id
    role = (j.get("position") or j.get("title") or "").strip()
    location = (j.get("location") or "").strip() or "Remote"
    posted_at = _parse_date(j.get("date") or j.get("epoch"))
    link = j.get("url") or j.get("apply_url") or ""

    # RemoteOK structured comp: salary_min / salary_max in USD.
    comp_string: str | None = None
    smin = j.get("salary_min")
    smax = j.get("salary_max")
    if smin and smax:
        comp_string = f"${int(smin):,} - ${int(smax):,}"
    elif smax:
        comp_string = f"Up to ${int(smax):,}"

    desc = j.get("description") or ""
    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{posting_id}",
        company=company,
        role=role,
        location=location,
        comp_string=comp_string,
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
