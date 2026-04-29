"""Runner-level location filter.

Drops rows whose location is not India and not global-remote when the
source is not already India-focused. Designed and shipped 2026-04-29 per
the geography audit at
`_bmad-output/planning-artifacts/research/india-coverage-audit-2026-04-29.md`.

Decision rules (in order):
1. Source is India-focused (naukri / linkedin / linkedin_auth / hasjob /
   hn_hiring / yc_waas) → keep regardless of location.
2. Location empty → keep (Workday rows often have empty locationsText;
   downstream tags / dedup handle them).
3. Location matches a geo-locked-remote variant ("Remote - US", "USA only",
   etc.) → drop.
4. Location matches a global-remote variant ("Remote", "Anywhere",
   "Worldwide", "Remote - Global", "Remote - India", "Remote - APAC") → keep.
5. Location contains an India city / state / abbreviation token → keep.
6. Location is "N Locations" → keep iff the JD body mentions an India
   token; otherwise drop as ambiguous.
7. Anything else → drop as `loc_non_india`.

The filter applies *in addition to* the existing exclude pipeline
(company / company_pattern / role_pattern / JD-keyword). Rows dropped here
never reach the Sheet — they're counted in the per-board `excluded` total.
"""
from __future__ import annotations

import re

# Sources whose query parameters or natural scope already restrict to India.
# These bypass the filter entirely.
INDIA_FOCUSED_SOURCES: set[str] = {
    "naukri",          # India-only board
    "linkedin",        # boards.yaml entries are India/Bengaluru-targeted
    "linkedin_auth",   # same
    "hasjob",          # India indie-tech
    "hn_hiring",       # globally relevant; small volume; let through
    "yc_waas",         # YC postings; let through (small)
    "instahyre",       # India-only premium aggregator
    "hirist",          # India-only premium tech (auth-walled)
}

# Indian cities, states, common abbreviations. Word-boundary matched.
_INDIA_TOKENS = (
    "india", "bharat",
    "bengaluru", "bangalore", "blr",
    "hyderabad", "hyd",
    "mumbai", "bombay",
    "chennai", "madras",
    "pune",
    "delhi", "new delhi", "ncr",
    "gurgaon", "gurugram",
    "noida",
    "kolkata", "calcutta",
    "ahmedabad",
    "jaipur",
    "kochi", "cochin",
    "trivandrum", "thiruvananthapuram",
    "chandigarh",
    "indore",
    "bhubaneswar",
    "coimbatore",
    "nagpur",
    "vadodara", "baroda",
    "surat",
    "lucknow",
    "kanpur",
    "mysore", "mysuru",
    "thane",
    "navi mumbai",
)

_INDIA_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _INDIA_TOKENS) + r")\b",
    re.IGNORECASE,
)

# Country / region-locked remote — keep these OUT.
# Order matters: more specific patterns first.
_GEO_LOCKED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bremote\s*[-–]\s*(?:us|usa|united\s+states|uk|gb|britain|"
               r"ca|canada|emea|eu|europe|au|australia|nz|new\s+zealand|"
               r"japan|jp|sg|singapore|me|middle\s+east)\b", re.IGNORECASE),
    re.compile(r"\bremote\s*\(\s*(?:us|usa|united\s+states|uk|canada|"
               r"emea|eu|europe|australia|japan|singapore)\s*\)", re.IGNORECASE),
    re.compile(r"\b(?:us|usa|uk|canada|eu|emea)\s+(?:only|remote)\b", re.IGNORECASE),
    re.compile(r"\bunited\s+states\s+(?:only|remote)\b", re.IGNORECASE),
    re.compile(r"\bonly\s+(?:us|usa|uk)\b", re.IGNORECASE),
)

# Global / India-friendly remote — keep these.
_GLOBAL_REMOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*remote\s*$", re.IGNORECASE),
    re.compile(r"\bremote\s*[-–]\s*(?:global|world|worldwide|anywhere|"
               r"international|india|apac|asia|asia\s+pacific)\b", re.IGNORECASE),
    re.compile(r"\bremote\s*\(\s*(?:global|worldwide|anywhere|india|apac)\s*\)",
               re.IGNORECASE),
    re.compile(r"\b(?:anywhere|worldwide|fully\s+remote)\b", re.IGNORECASE),
    re.compile(r"\bremote\s*[-–]\s*friendly\b", re.IGNORECASE),
)

_MULTI_LOC_RE = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def location_filter_reason(
    location: str | None,
    jd_text: str,
    source_type: str,
) -> str | None:
    """Returns a short drop-reason string, or None to keep.

    `jd_text` is consulted only for ambiguous "N Locations" rows.
    """
    if source_type in INDIA_FOCUSED_SOURCES:
        return None

    loc = (location or "").strip()
    if not loc:
        return None  # empty → keep (let downstream judge)

    # Geo-locked remote variants — strict drop
    for pat in _GEO_LOCKED_PATTERNS:
        if pat.search(loc):
            return f"loc_geo_locked:{loc[:40]}"

    # Global / India-friendly remote → keep
    for pat in _GLOBAL_REMOTE_PATTERNS:
        if pat.search(loc):
            return None

    # India token in location string → keep
    if _INDIA_TOKEN_RE.search(loc):
        return None

    # Ambiguous "N Locations" — peek JD body
    if _MULTI_LOC_RE.match(loc):
        if _INDIA_TOKEN_RE.search(jd_text or ""):
            return None
        return "loc_multi_no_india"

    return f"loc_non_india:{loc[:40]}"
