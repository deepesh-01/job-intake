"""Hasjob — HasGeek's India-focused job board.

The legacy JSON API at /api/1/posts is dead (404). Hasjob still publishes
an Atom feed at /feed which works and includes title, location, body
content, and a URL whose path encodes the company. ~10-50 fresh postings
typically. India-only (mostly Bangalore-based startups), good signal-to-
noise for the user's target geography.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

import httpx

from lib.posting import Posting, SourceError
from scout.sources._http import USER_AGENT, _throttle  # noqa: F401

SOURCE_TYPE = "hasjob"
_URL = "https://hasjob.co/feed"
_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch(board_id: str = "ALL") -> list[Posting]:
    try:
        # Atom feed — use httpx directly since shared get_json expects JSON.
        r = httpx.get(
            _URL,
            headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
            timeout=30.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        xml = r.text
    except Exception as e:
        raise SourceError(SOURCE_TYPE, board_id, str(e)) from e

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise SourceError(SOURCE_TYPE, board_id, f"feed not parseable XML: {e}") from e

    out: list[Posting] = []
    for entry in root.findall("atom:entry", _NS):
        post = _parse_entry(entry, board_id)
        if post:
            out.append(post)
    return out


def _t(elem: ET.Element | None) -> str:
    return (elem.text or "").strip() if elem is not None else ""


_TITLE_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*\|?\s*", flags=re.IGNORECASE)


def _parse_entry(entry: ET.Element, board_id: str) -> Posting | None:
    title = _t(entry.find("atom:title", _NS))
    link_el = entry.find("atom:link", _NS)
    link = link_el.get("href", "") if link_el is not None else ""
    posting_id = _t(entry.find("atom:id", _NS)) or link
    published = _t(entry.find("atom:published", _NS))
    location = _t(entry.find("atom:location", _NS)) or None

    if not title or not link:
        return None

    role = _TITLE_PREFIX_RE.sub("", title).strip()
    # Hasjob URL shape: https://hasjob.co/<company-domain>/<slug>
    # Use the company-domain segment as a fallback company name.
    company = _company_from_link(link)

    content_el = entry.find("atom:content", _NS)
    raw_body = (content_el.text or "") if content_el is not None else ""
    # Atom <content type="html"> contains HTML-escaped HTML — runner's
    # html_to_text will unescape and parse.
    jd_html = _html.unescape(raw_body) if raw_body else ""

    posted_at: date | None = None
    if published:
        try:
            posted_at = datetime.fromisoformat(published.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    # ID stable on URL path, not the full URL (avoids re-firing if scheme changes).
    safe_id = link.replace("https://hasjob.co/", "").replace("/", "_")[:80] or posting_id
    return Posting(
        id=f"{SOURCE_TYPE}:{board_id}:{safe_id}",
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


def _company_from_link(link: str) -> str:
    """`https://hasjob.co/prayaancapital.com/fyrmb` → `prayaancapital`."""
    m = re.match(r"https?://hasjob\.co/([^/]+)/", link)
    if not m:
        return "unknown"
    seg = m.group(1)
    # Strip TLD if present (prayaancapital.com → prayaancapital)
    return re.sub(r"\.[a-z]{2,4}(?:\.[a-z]{2})?$", "", seg, flags=re.IGNORECASE)
