"""Tests for src/scout/sources/hirist.py."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scout.sources import hirist
from lib.posting import SourceError


# ── _parse_board_id ──────────────────────────────────────────────────


def test_parse_board_id_empty() -> None:
    assert hirist._parse_board_id("") == {}


def test_parse_board_id_pages_only() -> None:
    assert hirist._parse_board_id("pages=5") == {"pages": "5"}


# ── _cookie_header ───────────────────────────────────────────────────


def test_cookie_header_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIRIST_COOKIES", raising=False)
    with pytest.raises(SourceError) as exc:
        hirist._cookie_header()
    assert "HIRIST_COOKIES" in str(exc.value)


def test_cookie_header_set_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "PHPSESSID=abc; HIRIST_CK1=xyz")
    assert hirist._cookie_header() == "PHPSESSID=abc; HIRIST_CK1=xyz"


# ── fetch() ──────────────────────────────────────────────────────────


_SAMPLE_JOB: dict[str, Any] = {
    "id": 1631387,
    "title": "Staff Engineer - Java/Spring Boot (9-12 yrs)",
    "introText": "<p>Own one or more core applications end-to-end.</p>",
    "min": 9,
    "max": 12,
    "minSal": 60,
    "maxSal": 80,
    "hideSal": 0,
    "createdTimeMs": 1777094801056,  # 2026-04-23
    "tags": [
        {"id": 5, "name": "Java", "isMandatory": True},
        {"id": 2850, "name": "Spring Boot", "isMandatory": True},
    ],
    "locations": [{"id": 3, "name": "Bangalore"}],
    "companyData": {"companyId": 0, "companyName": "Acme Engineering"},
    "workFromHome": 0,
}


def _fake_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    request = httpx.Request("GET", hirist.SEARCH_URL)
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=request,
    )


def test_fetch_parses_full_job_with_comp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "test=cookie")
    payload = {"data": [_SAMPLE_JOB], "page": 0, "limit": 50, "count": 1, "hasMore": False}
    monkeypatch.setattr(hirist.httpx, "get", lambda *a, **kw: _fake_response(payload))
    postings = hirist.fetch("pages=1")
    assert len(postings) == 1
    p = postings[0]
    assert p.id == "hirist:ALL:1631387"
    assert p.company == "Acme Engineering"
    assert p.role.startswith("Staff Engineer")
    assert p.location == "Bangalore"
    assert p.comp_string == "INR 60-80 LPA"
    assert p.posted_at is not None
    assert p.link == "https://www.hirist.tech/j/1631387"
    assert "Java" in p.jd_html and "Spring Boot" in p.jd_html


def test_fetch_hides_comp_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "x=y")
    job = {**_SAMPLE_JOB, "hideSal": 1}
    monkeypatch.setattr(
        hirist.httpx, "get",
        lambda *a, **kw: _fake_response({"data": [job], "hasMore": False}),
    )
    postings = hirist.fetch("pages=1")
    assert postings[0].comp_string is None


def test_fetch_workFromHome_sets_remote_when_no_loc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "x=y")
    job = {**_SAMPLE_JOB, "workFromHome": 1, "locations": [], "location": []}
    monkeypatch.setattr(
        hirist.httpx, "get",
        lambda *a, **kw: _fake_response({"data": [job], "hasMore": False}),
    )
    postings = hirist.fetch("pages=1")
    assert postings[0].location == "Remote"


def test_fetch_skips_objects_missing_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "x=y")
    payload = {
        "data": [
            {"id": 1, "title": "", "companyData": {"companyName": "X"}},
            {"id": 2, "title": "Engineer", "companyData": {}},
            _SAMPLE_JOB,
        ],
        "hasMore": False,
    }
    monkeypatch.setattr(hirist.httpx, "get", lambda *a, **kw: _fake_response(payload))
    postings = hirist.fetch("pages=1")
    assert len(postings) == 1
    assert postings[0].id == "hirist:ALL:1631387"


def test_fetch_paginates_until_hasMore_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "x=y")
    pages_seen: list[int] = []
    page1 = {"data": [_SAMPLE_JOB], "hasMore": True}
    page2 = {"data": [{**_SAMPLE_JOB, "id": 1631388}], "hasMore": False}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        page = kwargs["params"]["page"]
        pages_seen.append(page)
        return _fake_response(page1 if page == 0 else page2)

    monkeypatch.setattr(hirist.httpx, "get", fake_get)
    postings = hirist.fetch("pages=5")
    assert pages_seen == [0, 1]  # stopped after page 1 said hasMore=False
    assert {p.id for p in postings} == {"hirist:ALL:1631387", "hirist:ALL:1631388"}


def test_fetch_raises_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIRIST_COOKIES", "expired")
    monkeypatch.setattr(
        hirist.httpx, "get", lambda *a, **kw: _fake_response({}, status=401)
    )
    with pytest.raises(SourceError) as exc:
        hirist.fetch("pages=1")
    assert "HIRIST_COOKIES" in str(exc.value)


def test_fetch_raises_when_cookie_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIRIST_COOKIES", raising=False)
    with pytest.raises(SourceError):
        hirist.fetch("pages=1")
