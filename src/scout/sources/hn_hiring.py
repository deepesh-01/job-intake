"""HN 'Who is Hiring' monthly thread → individual postings via Algolia API.

Only runs when day-of-month <= 3 AND the latest thread has not yet been
processed. The seen_ids cache stores `hn_hiring:thread:<thread_id>` to
guarantee single-shot processing of each month's thread. Auto-fires
`posted_recent` downstream.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from lib.posting import Posting, SourceError
from scout.sources._http import get_json

SOURCE_TYPE = "hn_hiring"
_SEARCH = "https://hn.algolia.com/api/v1/search"
_ITEM = "https://hn.algolia.com/api/v1/items/{item_id}"

_FORMAT_RE = re.compile(
    r"^(?P<company>[^|]{1,80})\|\s*(?P<role>[^|]{1,120})\|\s*(?P<location>[^|]{1,80})"
    r"(?:\|\s*(?P<remote>[^|]{1,40}))?(?:\|\s*(?P<extra>.{1,200}))?",
)


def fetch(board_id: str = "ALL", *, today: date | None = None,
          seen_thread_ids: set[str] | None = None) -> list[Posting]:
    today = today or date.today()
    if today.day > 3:
        return []  # only run during the first 3 days of the month
    seen_thread_ids = seen_thread_ids or set()

    try:
        # Find the latest "Ask HN: Who is hiring?" story
        search = get_json(
            _SEARCH,
            params={
                "query": "Ask HN Who is hiring",
                "tags": "story",
                "hitsPerPage": 5,
            },
        )
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    hits = search.get("hits") or []
    thread_id: str | None = None
    for h in hits:
        title = (h.get("title") or "").lower()
        if "who is hiring" in title and "ask hn" in title:
            thread_id = str(h.get("objectID") or h.get("story_id") or "")
            break
    if not thread_id:
        return []

    sentinel = f"hn_hiring:thread:{thread_id}"
    if sentinel in seen_thread_ids:
        return []

    try:
        thread = get_json(_ITEM.format(item_id=thread_id))
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    out: list[Posting] = []
    for child in thread.get("children") or []:
        post = _parse_comment(child, thread_id)
        if post:
            out.append(post)
    # Insert a sentinel-only Posting? No — we record the thread_id in dedup
    # cache from the runner once the batch lands.
    return out


def _parse_comment(c: dict[str, Any], thread_id: str) -> Posting | None:
    text = (c.get("text") or "").strip()
    if not text:
        return None
    # Strip HTML tags coarsely; we keep the original in jd_html.
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    first_line = plain.split("\n", 1)[0][:300]
    company = role = location = None
    m = _FORMAT_RE.match(first_line)
    if m:
        company = (m.group("company") or "").strip()
        role = (m.group("role") or "").strip()
        location = (m.group("location") or "").strip() or None
    if not company:
        return None  # nothing usable

    posting_id = str(c.get("id") or "")
    posted_at = _parse_ts(c.get("created_at_i"))
    link = f"https://news.ycombinator.com/item?id={posting_id}"

    return Posting(
        id=f"{SOURCE_TYPE}:thread{thread_id}:{posting_id}",
        company=company,
        role=role or "(see body)",
        location=location,
        comp_string=None,  # extract.parse_comp will scan body
        posted_at=posted_at,
        link=link,
        jd_html=text,
        source_type=SOURCE_TYPE,
        board_id="ALL",
    )


def _parse_ts(v: Any) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc).date()
    except (TypeError, ValueError):
        return None
