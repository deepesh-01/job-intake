"""Unit tests for src/scout/ambitionbox.py — slug derivation + parser.

Live HTTP fetches are NOT exercised here (they'd hit AmbitionBox on every
test run). The smoke against the live site is intentional: when this
module breaks because AmbitionBox changes its DOM/JSON shape, run
`PYTHONPATH=src .venv/bin/python -c 'from scout.ambitionbox import lookup;
print(lookup("Razorpay", role="Senior Software Engineer"))'` and re-tune.
"""
from __future__ import annotations

import json

from scout.ambitionbox import (
    CompEstimate,
    _normalize_role,
    _parse_estimate,
    _pick_typical_range,
    _slugify,
    _to_int_rupees,
)


# ── slug derivation ──


def test_slugify_basic() -> None:
    assert _slugify("Razorpay") == "razorpay"


def test_slugify_strips_pvt_ltd() -> None:
    assert _slugify("Tesco Bengaluru Pvt Ltd") == "tesco-bengaluru"
    assert _slugify("ABC Private Limited") == "abc"
    assert _slugify("XYZ Inc.") == "xyz"


def test_slugify_collapses_punctuation() -> None:
    assert _slugify("WaferWire Cloud Technologies") == "waferwire-cloud-technologies"
    assert _slugify("Dell Technologies") == "dell-technologies"
    assert _slugify("Moonfrog  Labs!") == "moonfrog-labs"


def test_slugify_empty_input() -> None:
    assert _slugify("") == ""
    assert _slugify("   ") == ""
    assert _slugify(None) == ""  # type: ignore[arg-type]


# ── role normalization ──


def test_normalize_role() -> None:
    assert _normalize_role("Senior Software Engineer") == "senior software engineer"
    assert _normalize_role("Sr. Backend Engineer (Python)") == "sr backend engineer python"
    assert _normalize_role("") == ""


# ── _to_int_rupees handles AmbitionBox's mix of int + numeric-string ──


def test_to_int_rupees_int() -> None:
    assert _to_int_rupees(3181041) == 3181041


def test_to_int_rupees_numeric_string() -> None:
    # AmbitionBox returns avgCtc as a fractional string in some payloads.
    assert _to_int_rupees("3181041.0085998443") == 3181041


def test_to_int_rupees_none_or_junk() -> None:
    assert _to_int_rupees(None) == 0
    assert _to_int_rupees("not a number") == 0


# ── _pick_typical_range fuzzy match ──


_DESIGNATIONS_SAMPLE = [
    {"jobProfileName": "Senior Software Engineer", "typicalMinCtc": "3000000", "typicalMaxCtc": "3500000", "avgCtc": "3200000"},
    {"jobProfileName": "Software Developer", "typicalMinCtc": "1800000", "typicalMaxCtc": "2100000", "avgCtc": "1950000"},
    {"jobProfileName": "Software Engineer", "typicalMinCtc": "1500000", "typicalMaxCtc": "1800000", "avgCtc": "1650000"},
]


def test_pick_typical_range_exact_match() -> None:
    pick = _pick_typical_range(_DESIGNATIONS_SAMPLE, "Senior Software Engineer")
    assert pick is not None
    entry, score = pick
    assert entry["jobProfileName"] == "Senior Software Engineer"
    assert score == 100


def test_pick_typical_range_partial_match() -> None:
    pick = _pick_typical_range(_DESIGNATIONS_SAMPLE, "Backend Software Developer")
    assert pick is not None
    entry, score = pick
    # 2 of 3 tokens match ("software" + "developer") so Software Developer wins
    assert entry["jobProfileName"] == "Software Developer"
    assert 0 < score < 100


def test_pick_typical_range_empty_designations() -> None:
    assert _pick_typical_range([], "anything") is None


def test_pick_typical_range_empty_target_returns_first() -> None:
    pick = _pick_typical_range(_DESIGNATIONS_SAMPLE, "")
    assert pick is not None
    entry, score = pick
    assert entry["jobProfileName"] == "Senior Software Engineer"
    assert score == 0


# ── _parse_estimate end-to-end against a synthetic AmbitionBox payload ──


def _fake_next_data(*, with_designations: bool = True, with_overall: bool = True) -> dict:
    pp: dict = {}
    if with_designations:
        pp["filtersData"] = {"data": {"jobProfiles": _DESIGNATIONS_SAMPLE}}
    if with_overall:
        pp["salariesSummaryData"] = {"totalSalaryAverage": "20.2"}
    return {"props": {"pageProps": pp}}


def test_parse_estimate_uses_role_match_when_available() -> None:
    nd = _fake_next_data()
    est = _parse_estimate(
        nd,
        company_norm="razorpay",
        role="Senior Software Engineer",
        source_url="https://example.com",
    )
    assert isinstance(est, CompEstimate)
    assert est.comp_low == 3_000_000
    assert est.comp_high == 3_500_000
    assert est.matched_role == "Senior Software Engineer"
    assert est.matched_score == 100


def test_parse_estimate_falls_back_to_company_overall_when_no_designations() -> None:
    nd = _fake_next_data(with_designations=False, with_overall=True)
    est = _parse_estimate(
        nd,
        company_norm="razorpay",
        role="Senior Software Engineer",
        source_url="https://example.com",
    )
    assert isinstance(est, CompEstimate)
    # 20.2 LPA → 2,020,000 rupees average; ±25% range
    assert est.avg == 2_020_000
    assert est.comp_low == 1_515_000
    assert est.comp_high == 2_525_000
    assert est.matched_role is None
    assert est.matched_score == 0


def test_parse_estimate_returns_none_when_payload_empty() -> None:
    nd = _fake_next_data(with_designations=False, with_overall=False)
    est = _parse_estimate(
        nd,
        company_norm="anything",
        role="anything",
        source_url="https://example.com",
    )
    assert est is None
