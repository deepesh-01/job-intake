"""Tests for src/scout/location.py — runner-level location filter."""
from __future__ import annotations

from scout.location import location_filter_reason, INDIA_FOCUSED_SOURCES


# India-friendly cases — should KEEP (return None).
def test_india_focused_source_keeps_anything() -> None:
    # Naukri, linkedin, etc. bypass the filter entirely.
    for src in INDIA_FOCUSED_SOURCES:
        assert location_filter_reason("Berlin", "", src) is None
        assert location_filter_reason("Anywhere except India", "", src) is None


def test_empty_location_keeps() -> None:
    assert location_filter_reason("", "", "greenhouse") is None
    assert location_filter_reason(None, "", "greenhouse") is None
    assert location_filter_reason("   ", "", "greenhouse") is None


def test_india_city_keeps() -> None:
    for loc in [
        "Bengaluru, Karnataka, India",
        "Bangalore",
        "BLR",
        "Hyderabad, Telangana",
        "Hyd",
        "Mumbai",
        "Chennai, India",
        "Pune",
        "Delhi NCR",
        "Gurgaon",
        "Gurugram",
        "Noida",
        "Bangalore, India · 5 days ago",
    ]:
        assert location_filter_reason(loc, "", "greenhouse") is None, f"failed: {loc}"


def test_global_remote_keeps() -> None:
    for loc in [
        "Remote",
        "remote",
        "Remote - Global",
        "Remote (Global)",
        "Remote - India",
        "Remote (India)",
        "Remote - APAC",
        "Anywhere",
        "Worldwide",
        "Fully Remote",
        "Remote - Friendly",
    ]:
        assert location_filter_reason(loc, "", "greenhouse") is None, f"failed: {loc}"


# Non-India / blocked cases — should DROP (return reason).
def test_geo_locked_remote_drops() -> None:
    for loc in [
        "Remote - US",
        "Remote - USA",
        "Remote - UK",
        "Remote - Canada",
        "Remote - EMEA",
        "Remote - EU",
        "Remote - Europe",
        "Remote (US)",
        "Remote (Canada)",
        "US Only",
        "USA only",
        "UK only",
        "United States Remote",
        "Only US",
    ]:
        reason = location_filter_reason(loc, "", "greenhouse")
        assert reason is not None and reason.startswith("loc_geo_locked"), f"expected drop: {loc} (got {reason!r})"


def test_non_india_city_drops() -> None:
    for loc in [
        "Berlin",
        "Munich",
        "San Francisco",
        "San Francisco, California",
        "New York",
        "Cologne",
        "Hamburg",
        "Frankfurt am Main",
        "Tokyo",
        "London",
        "Paris",
        "Singapore",
        "Sydney",
    ]:
        reason = location_filter_reason(loc, "", "greenhouse")
        assert reason is not None and reason.startswith("loc_non_india"), f"expected drop: {loc} (got {reason!r})"


def test_multi_location_uses_jd() -> None:
    # "2 Locations" with India in JD → keep
    assert location_filter_reason("2 Locations", "Bengaluru, Karnataka", "workday") is None
    # "2 Locations" with no India in JD → drop
    reason = location_filter_reason("2 Locations", "San Francisco; New York", "workday")
    assert reason == "loc_multi_no_india"


def test_filter_applies_to_aggregators() -> None:
    # remoteok / remotive / arbeitnow get the filter applied
    assert location_filter_reason("Berlin", "", "remoteok") is not None
    assert location_filter_reason("Berlin", "", "remotive") is not None
    assert location_filter_reason("Berlin", "", "arbeitnow") is not None


def test_remote_friendly_with_country_in_paren_drops() -> None:
    # "Remote (USA)" should drop, not be confused with global "Remote"
    reason = location_filter_reason("Remote (USA)", "", "greenhouse")
    assert reason is not None and reason.startswith("loc_geo_locked")
