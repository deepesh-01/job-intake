"""Tag engine tests — verifies the rule set in tag_rules.yaml fires correctly."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml

from scout import tag
from scout.extract import Comp


_ROOT = Path(__file__).resolve().parent.parent
RULES = yaml.safe_load((_ROOT / "tag_rules.yaml").read_text())


def _run(role="Senior Engineer", location="remote", body="", comp=None, posted_at=None):
    if comp is None:
        comp = Comp(None, "USD", 200_000, 220_000, 200_000, 220_000)
    return tag.tag(
        role=role, location=location, jd_text=body, comp=comp,
        posted_at=posted_at, rules=RULES, today=date(2026, 4, 26),
    )


# ── comp tags ──


def test_comp_ok_inr():
    c = Comp(None, "INR", 3_500_000, 5_000_000, None, None)
    out = _run(comp=c)
    assert "comp_ok" in out.tags
    assert any("comp_ok=" in r for r in out.reasons)


def test_comp_below_usd():
    c = Comp(None, "USD", 30_000, 50_000, 30_000, 50_000)
    out = _run(comp=c)
    assert "comp_below" in out.tags


def test_comp_unknown_no_currency():
    c = Comp(None, None, None, None, None, None)
    out = _run(comp=c)
    assert "comp_unknown" in out.tags


# ── stack tags (need section_hint anchor) ──


def test_stack_typescript_in_requirements():
    body = "Requirements:\n- Must have TypeScript and React experience"
    out = _run(body=body)
    assert "stack_typescript" in out.tags
    assert "stack_match" in out.tags


def test_stack_does_not_fire_outside_requirements():
    body = "We use Python casually but the role does not require it."
    out = _run(body=body)
    # Section-anchored: no requirements/must-have label nearby → skip
    assert "stack_python" not in out.tags


# ── seniority ──


def test_seniority_match_senior():
    out = _run(role="Senior Backend Engineer")
    assert "seniority_match" in out.tags


def test_seniority_excludes_junior():
    out = _run(role="Junior Engineer")
    assert "seniority_match" not in out.tags
    assert "seniority_junior" in out.tags


# ── application_eng vs wrong_discipline ──


def test_application_eng_full_stack():
    out = _run(role="Full Stack Engineer")
    assert "application_eng" in out.tags
    assert "wrong_discipline" not in out.tags


def test_wrong_discipline_devops():
    out = _run(role="Senior DevOps Engineer")
    assert "wrong_discipline" in out.tags
    assert "application_eng" not in out.tags


def test_wrong_discipline_data_engineer():
    out = _run(role="Data Engineer")
    assert "wrong_discipline" in out.tags


# ── Default application_eng fallback (regression for narrow-list misses) ──


def test_default_application_eng_for_senior_engineer():
    out = _run(role="Senior Engineer")
    assert "application_eng" in out.tags
    assert "wrong_discipline" not in out.tags


def test_default_application_eng_for_staff_engineer_iii():
    out = _run(role="Staff Engineer III, Distributed Systems")
    assert "application_eng" in out.tags


def test_default_application_eng_for_mts():
    out = _run(role="Member of Technical Staff")
    assert "application_eng" in out.tags


def test_default_application_eng_for_architect():
    """Architect titles are senior IC engineering at most companies
    (Anthropic Applied AI Architect, Solutions Architect, Software Architect)."""
    out = _run(role="Applied AI Architect, Enterprise Tech")
    assert "application_eng" in out.tags
    assert "wrong_discipline" not in out.tags


def test_default_does_not_override_wrong_discipline():
    out = _run(role="Senior Embedded Engineer")
    assert "wrong_discipline" in out.tags
    assert "application_eng" not in out.tags


# ── stage ──


def test_early_stage_yc():
    out = _run(body="We're a Y Combinator backed seed stage startup")
    assert "early_stage" in out.tags


def test_growth_stage_series_b():
    out = _run(body="Recently closed our Series B round")
    assert "growth_stage" in out.tags


# ── culture ──


def test_culture_chill_signal():
    body = "Requirements:\n- Senior\nWe maintain a strict 9-to-5, no on-call culture."
    out = _run(body=body)
    assert "culture_chill_signal" in out.tags


def test_grind_signal_flags_red():
    body = "We're looking for a 10x engineer who can wear many hats."
    out = _run(body=body)
    assert "grind_signal" in out.tags


def test_enjoy_eligible_when_chill_no_grind():
    body = "We are AI-native, encouraging Cursor and Claude Code use."
    out = _run(body=body)
    assert "ai_native" in out.tags
    assert "enjoy_eligible" in out.tags


def test_enjoy_eligible_blocked_by_grind():
    body = "AI-native shop where every engineer must be a rockstar willing to hustle."
    out = _run(body=body)
    assert "ai_native" in out.tags
    assert "grind_signal" in out.tags
    assert "enjoy_eligible" not in out.tags


# ── remote ──


def test_remote_ok_india():
    out = _run(location="Remote (India)")
    assert "remote_ok" in out.tags


def test_non_us_only_blocks_remote_ok():
    out = _run(location="Remote — US only")
    assert "non_us_only" in out.tags
    assert "remote_ok" not in out.tags


# ── target_city ──


def test_target_city_bengaluru():
    out = _run(location="Bengaluru, India")
    assert "target_city" in out.tags


def test_target_city_hyderabad():
    out = _run(location="Hyderabad")
    assert "target_city" in out.tags


def test_target_city_does_not_fire_for_us():
    out = _run(location="San Francisco, CA")
    assert "target_city" not in out.tags


def test_target_city_does_not_fire_when_blocked():
    # If JD says US-only, even an India mention shouldn't fire target_city
    out = _run(location="US only")
    assert "target_city" not in out.tags
    assert "non_us_only" in out.tags


# ── other ──


def test_posted_recent():
    out = _run(posted_at=date(2026, 4, 26) - timedelta(days=3))
    assert "posted_recent" in out.tags


def test_has_recruiter_email():
    out = _run(body="Apply via jobs@example.com — see contact below")
    assert "has_recruiter_email" in out.tags


# ── follow-up ──


def test_needs_followup_after_7_days():
    assert tag.needs_followup("2026-04-15", "", today=date(2026, 4, 26))


def test_no_followup_if_response_received():
    assert not tag.needs_followup("2026-04-15", "2026-04-20", today=date(2026, 4, 26))


def test_no_followup_within_7_days():
    assert not tag.needs_followup("2026-04-25", "", today=date(2026, 4, 26))
