"""Tests for src/scout/sources/instahyre.py."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scout.sources import instahyre


# ── _parse_board_id ──────────────────────────────────────────────────


def test_parse_board_id_empty() -> None:
    assert instahyre._parse_board_id("") == {}


def test_parse_board_id_full() -> None:
    parsed = instahyre._parse_board_id(
        "pages=5;q=senior backend engineer;loc=bengaluru;exp=4-9"
    )
    assert parsed == {
        "pages": "5",
        "q": "senior backend engineer",
        "loc": "bengaluru",
        "exp": "4-9",
    }


def test_parse_board_id_strips_whitespace_and_skips_malformed() -> None:
    parsed = instahyre._parse_board_id("  pages=3 ; bogus ; q = ai engineer ")
    assert parsed == {"pages": "3", "q": "ai engineer"}


# ── _build_cookies ───────────────────────────────────────────────────


def test_build_cookies_unset_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAHYRE_COOKIE", raising=False)
    assert instahyre._build_cookies() == {}


def test_build_cookies_set_returns_sessionid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAHYRE_COOKIE", "abc123")
    assert instahyre._build_cookies() == {"sessionid": "abc123"}


# ── _build_query_params ──────────────────────────────────────────────


def test_query_params_default_only_paging() -> None:
    qp = instahyre._build_query_params({}, offset=70)
    assert qp == {"limit": instahyre.PAGE_SIZE, "offset": 70}


def test_query_params_passes_filters_through() -> None:
    parsed = {"q": "ai engineer", "loc": "bengaluru", "exp": "4-9"}
    qp = instahyre._build_query_params(parsed, offset=0)
    assert qp["q"] == "ai engineer"
    assert qp["locations"] == "bengaluru"
    assert qp["experience_min"] == 4
    assert qp["experience_max"] == 9


# ── fetch() ──────────────────────────────────────────────────────────


_SAMPLE_OBJECT: dict[str, Any] = {
    "id": 422485,
    "title": "Software Engineer 2",
    "public_url": "https://www.instahyre.com/job-422485-software-engineer-2-at-moonfrog-labs-bangalore/",
    "locations": "Bangalore",
    "keywords": ["Node.js", "React", "TypeScript"],
    "employer": {
        "company_name": "Moonfrog Labs",
        "company_tagline": "Fantastical mobile games",
        "instahyre_note": "A leader in mobile gaming.",
    },
    "resource_uri": "/api/v1/job_search/422485",
}


def _fake_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    request = httpx.Request("GET", instahyre.SEARCH_URL)
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=request,
    )


def test_fetch_parses_objects_and_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAHYRE_COOKIE", raising=False)
    page1 = {
        "objects": [_SAMPLE_OBJECT, {**_SAMPLE_OBJECT, "id": 422486, "title": "Senior SWE"}],
        "meta": {"next": None, "limit": 35, "offset": 0, "total_count": 2},
    }
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, "params": kwargs.get("params"), "cookies": kwargs.get("cookies")})
        return _fake_response(page1)

    monkeypatch.setattr(instahyre.httpx, "get", fake_get)

    postings = instahyre.fetch("pages=3")

    assert len(postings) == 2
    assert postings[0].id == "instahyre:ALL:422485"
    assert postings[0].company == "Moonfrog Labs"
    assert postings[0].role == "Software Engineer 2"
    assert postings[0].location == "Bangalore"
    assert postings[0].source_type == "instahyre"
    assert postings[0].comp_string is None
    assert postings[0].posted_at is None
    assert "Node.js" in postings[0].jd_html
    assert postings[0].link.startswith("https://www.instahyre.com/job-422485-")
    # Stops paginating once meta.next is null
    assert len(calls) == 1
    assert calls[0]["params"]["offset"] == 0
    assert calls[0]["cookies"] == {}  # no auth env


def test_fetch_skips_objects_missing_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAHYRE_COOKIE", raising=False)
    payload = {
        "objects": [
            {"id": 1, "title": "", "employer": {"company_name": "X"}},
            {"id": 2, "title": "Engineer", "employer": {}},
            {"title": "Engineer", "employer": {"company_name": "Y"}},
            _SAMPLE_OBJECT,
        ],
        "meta": {"next": None},
    }
    monkeypatch.setattr(instahyre.httpx, "get", lambda *a, **kw: _fake_response(payload))
    postings = instahyre.fetch("pages=1")
    assert len(postings) == 1
    assert postings[0].id == "instahyre:ALL:422485"


def test_fetch_raises_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAHYRE_COOKIE", "expired-cookie")
    monkeypatch.setattr(
        instahyre.httpx, "get", lambda *a, **kw: _fake_response({}, status=401)
    )
    from lib.posting import SourceError
    with pytest.raises(SourceError) as exc_info:
        instahyre.fetch("pages=1")
    assert "INSTAHYRE_COOKIE" in str(exc_info.value)


def test_fetch_dedups_within_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAHYRE_COOKIE", raising=False)
    payload = {
        "objects": [_SAMPLE_OBJECT, _SAMPLE_OBJECT, _SAMPLE_OBJECT],
        "meta": {"next": None},
    }
    monkeypatch.setattr(instahyre.httpx, "get", lambda *a, **kw: _fake_response(payload))
    postings = instahyre.fetch("pages=1")
    assert len(postings) == 1
