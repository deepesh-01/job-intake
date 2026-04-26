"""Tagging engine. All rules are deterministic regex/keyword matches per §6.

Each tag fired records a reason in tag_reasons (§6.3). The reasons string
is stored verbatim in the Sheet so the user can see *why* a tag fired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from scout.extract import Comp


@dataclass
class TagOutcome:
    tags: list[str]
    reasons: list[str]


# ────────────────────────────── helpers ──────────────────────────────


def _kw_re(kw: str) -> re.Pattern[str]:
    """Compile a keyword as a case-insensitive whole-token regex.
    If the keyword contains regex metachars (e.g. `sr\\.` or `yc s2[0-9]`),
    treat it as a regex; otherwise wrap with word boundaries."""
    has_meta = any(c in kw for c in r".*+?^$|()[]{}\\")
    if has_meta:
        return re.compile(kw, re.IGNORECASE)
    return re.compile(rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])", re.IGNORECASE)


def _first_match(text: str, kws: list[str]) -> str | None:
    for kw in kws:
        if _kw_re(kw).search(text):
            return kw
    return None


def _any_match(text: str, kws: list[str]) -> bool:
    return any(_kw_re(kw).search(text) for kw in kws)


def _section_match(text: str, kws: list[str], section_hints: list[str]) -> tuple[str | None, str | None]:
    """Return (matched_kw, section_label). Section is whichever hint
    appeared in the 400 chars before the keyword, else None."""
    for kw in kws:
        m = _kw_re(kw).search(text)
        if not m:
            continue
        window = text[max(0, m.start() - 400) : m.start()].lower()
        section = None
        for hint in section_hints:
            if hint.lower() in window:
                section = hint
                break
        return kw, section
    return None, None


# ────────────────────────────── main entry ──────────────────────────────


def tag(
    *,
    role: str,
    location: str | None,
    jd_text: str,
    comp: Comp,
    posted_at: date | None,
    rules: dict,
    today: date | None = None,
) -> TagOutcome:
    today = today or date.today()
    role_l = (role or "").lower()
    loc_l = (location or "").lower()
    body = jd_text or ""
    body_l = body.lower()

    tags: list[str] = []
    reasons: list[str] = []

    # ── Comp ──
    comp_cfg = rules.get("comp", {})
    inr_floor = int(comp_cfg.get("inr_floor", 0))
    usd_floor = int(comp_cfg.get("usd_floor", 0))

    if comp.currency is None:
        tags.append("comp_unknown")
        reasons.append("comp_unknown=no_currency_detected")
    elif comp.high is None and comp.low is None:
        tags.append("comp_unknown")
        reasons.append(f"comp_unknown=currency_detected_but_no_range:{comp.currency}")
    else:
        # Use comp_high (or comp_low if high missing) for floor check.
        anchor = comp.high if comp.high is not None else comp.low
        if comp.currency == "INR":
            if anchor >= inr_floor:
                tags.append("comp_ok")
                reasons.append(f"comp_ok=inr_high:{anchor}>={inr_floor}")
            else:
                tags.append("comp_below")
                reasons.append(f"comp_below=inr_high:{anchor}<{inr_floor}")
        elif comp.currency in {"USD", "GBP", "EUR", "SGD", "AUD", "CAD"}:
            usd_anchor = comp.high_usd if comp.high_usd is not None else comp.low_usd
            if usd_anchor is None:
                tags.append("comp_unknown")
                reasons.append(f"comp_unknown=fx_missing:{comp.currency}")
            elif usd_anchor >= usd_floor:
                tags.append("comp_ok")
                reasons.append(f"comp_ok=usd_equiv:{usd_anchor}>={usd_floor}")
            else:
                tags.append("comp_below")
                reasons.append(f"comp_below=usd_equiv:{usd_anchor}<{usd_floor}")
        else:
            tags.append("comp_unknown")
            reasons.append(f"comp_unknown=unknown_currency:{comp.currency}")

    # ── Stack (only fires when keyword found in/near a requirements section) ──
    stacks_cfg = rules.get("stacks", {})
    stack_hits: list[str] = []
    for stack_name, cfg in stacks_cfg.items():
        kws = cfg.get("keywords", [])
        hints = cfg.get("section_hint", [])
        matched, section = _section_match(body_l, kws, hints)
        if matched is None or section is None:
            # §6.1: stack tag fires only when keyword appears under/near a
            # requirements/must-have anchor. Casual mentions ("we use Python
            # for scripts") shouldn't fire it.
            continue
        tag_name = f"stack_{stack_name}"
        tags.append(tag_name)
        reasons.append(f"{tag_name}=keyword:{matched}@{section}")
        stack_hits.append(stack_name)
    if stack_hits:
        tags.append("stack_match")
        reasons.append(f"stack_match=any:{','.join(stack_hits)}")

    # ── Seniority ──
    sen_cfg = rules.get("seniority", {})
    sen_match = _first_match(role_l, sen_cfg.get("match", []))
    sen_excl = _first_match(role_l, sen_cfg.get("exclude", []))
    if sen_match and not sen_excl:
        tags.append("seniority_match")
        reasons.append(f"seniority_match=title:{sen_match}")
    if sen_excl:
        tags.append("seniority_junior")
        reasons.append(f"seniority_junior=title:{sen_excl}")

    # ── Application-eng vs wrong-discipline ──
    # Order: explicit exclude wins → `wrong_discipline`. Else explicit match
    # → `application_eng`. Else any title that *looks* like an engineering
    # role and isn't excluded gets the default `application_eng` (catches
    # "Senior Engineer", "Staff Engineer III", "Member of Technical Staff",
    # etc.) — broad but safe because exclude has already filtered infra/
    # ML/data/QA/security/embedded titles.
    ae_cfg = rules.get("application_eng", {})
    ae_match = _first_match(role_l, ae_cfg.get("match", []))
    ae_excl = _first_match(role_l, ae_cfg.get("exclude", []))
    if ae_excl:
        tags.append("wrong_discipline")
        reasons.append(f"wrong_discipline=title:{ae_excl}")
    elif ae_match:
        tags.append("application_eng")
        reasons.append(f"application_eng=title:{ae_match}")
    else:
        # Default fallback list — kept broad because exclude (wrong_discipline)
        # has already filtered infra/ML/data/QA/security/embedded titles.
        # `architect` was added after the first run showed ~30 senior IC
        # roles (e.g. Anthropic's "Applied AI Architect") fell through the
        # cracks: senior + resume-skill-overlap, but no `engineer` token.
        default_hit = _first_match(
            role_l,
            ["engineer", "developer", "swe", "mts", "smts",
             "technical staff", "architect"],
        )
        if default_hit:
            tags.append("application_eng")
            reasons.append(f"application_eng=default:{default_hit}")

    # ── Stage ──
    stage_cfg = rules.get("stage", {})
    stage_priority = ["early", "growth", "late"]
    stage_tag_map = {"early": "early_stage", "growth": "growth_stage", "late": "late_stage"}
    for stage_name in stage_priority:
        kws = (stage_cfg.get(stage_name) or {}).get("match", [])
        hit = _first_match(body_l, kws)
        if hit:
            t = stage_tag_map[stage_name]
            tags.append(t)
            reasons.append(f"{t}=keyword:{hit}")
            break  # one stage tag at a time

    # ── Culture ──
    cul_cfg = rules.get("culture", {})
    ai_hit = _first_match(body_l, cul_cfg.get("ai_native_signals", []))
    chill_hit = _first_match(body_l, cul_cfg.get("chill_signals", []))
    grind_hit = _first_match(body_l, cul_cfg.get("grind_signals", []))
    if ai_hit:
        tags.append("ai_native")
        reasons.append(f"ai_native=keyword:{ai_hit}")
    if chill_hit:
        tags.append("culture_chill_signal")
        reasons.append(f"culture_chill_signal=keyword:{chill_hit}")
    if grind_hit:
        tags.append("grind_signal")
        reasons.append(f"grind_signal=keyword:{grind_hit}")
    # Derived enjoy_eligible: (ai_native OR chill) AND NOT grind
    if (ai_hit or chill_hit) and not grind_hit:
        tags.append("enjoy_eligible")
        reasons.append(
            f"enjoy_eligible=derived(ai_native={'y' if ai_hit else 'n'},chill={'y' if chill_hit else 'n'},grind=n)"
        )

    # ── Remote / location ──
    remote_cfg = rules.get("remote", {})
    allow = remote_cfg.get("allow_locations", [])
    block = remote_cfg.get("block_locations", [])
    block_hit = _first_match(loc_l, block) or _first_match(body_l, block)
    allow_hit = _first_match(loc_l, allow)
    if block_hit:
        tags.append("non_us_only")
        reasons.append(f"non_us_only=match:{block_hit}")
    elif allow_hit:
        tags.append("remote_ok")
        reasons.append(f"remote_ok=location:{allow_hit}")

    # ── Target city: a stronger positive than `remote_ok`. Fires when the
    # location explicitly names one of the user's home cities (Bengaluru/
    # Hyderabad/India incl. NCR/Mumbai/Pune). These are the rows worth
    # triaging first. We don't auto-include `my_region` because short
    # codes like "IN" don't survive word-boundary matching against "India".
    city_aliases = rules.get("my_city_aliases", [])
    if not block_hit:
        city_hit = _first_match(loc_l, city_aliases)
        if city_hit:
            tags.append("target_city")
            reasons.append(f"target_city=location:{city_hit}")

    # ── Other ──
    if posted_at and (today - posted_at) <= timedelta(days=7):
        tags.append("posted_recent")
        reasons.append(f"posted_recent=age:{(today - posted_at).days}d")
    email_match = re.search(r"[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}", body)
    if email_match:
        tags.append("has_recruiter_email")
        reasons.append("has_recruiter_email=email_in_jd")

    return TagOutcome(tags=tags, reasons=reasons)


def reasons_to_string(reasons: list[str]) -> str:
    return "; ".join(reasons)


# ────────────────────────────── follow-up sweep ──────────────────────────────


def needs_followup(applied_at: str, response_at: str, today: date | None = None) -> bool:
    """For the §8.1 step-5 sweep. applied_at is ISO date string; response_at
    is ISO date or empty. Idempotent: caller skips rows with non-empty
    followup_due_at already."""
    if not applied_at or response_at:
        return False
    today = today or date.today()
    try:
        a = date.fromisoformat(applied_at[:10])
    except ValueError:
        return False
    return (today - a) >= timedelta(days=7)
