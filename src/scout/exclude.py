"""exclude.yaml loader and matcher per §7.4.

Excluded rows never reach the Sheet. Caller logs the reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ExcludeRules:
    company_aliases: list[tuple[str, str]]  # (canonical_name, alias_substring)
    keyword_patterns: list[re.Pattern[str]]
    company_patterns: list[re.Pattern[str]]
    role_patterns: list[re.Pattern[str]]


def compile_rules(raw: dict) -> ExcludeRules:
    aliases: list[tuple[str, str]] = []
    for entry in raw.get("excluded_companies", []):
        canon = (entry.get("name") or "").lower().strip()
        for alias in entry.get("aliases", []):
            aliases.append((canon, alias.lower().strip()))

    keyword_patterns = [
        re.compile(re.escape(kw), re.IGNORECASE) if not _has_meta(kw) else re.compile(kw, re.IGNORECASE)
        for kw in raw.get("excluded_keywords", [])
    ]
    company_patterns = [
        re.compile(p, re.IGNORECASE) for p in raw.get("excluded_company_patterns", [])
    ]
    role_patterns = [
        re.compile(p) for p in raw.get("excluded_role_patterns", [])
    ]
    return ExcludeRules(aliases, keyword_patterns, company_patterns, role_patterns)


def _has_meta(s: str) -> bool:
    return any(c in s for c in r".*+?^$|()[]{}\\")


def excluded_reason(
    company: str, jd_text: str, rules: ExcludeRules, role: str = ""
) -> str | None:
    """Returns a short reason string if the row should be excluded, else None.

    `role` is the job title (Posting.role). Role-pattern exclusion is uniform
    across sources; useful for filtering Naukri's loose keyword matches and
    full-company-page ATSes that include sales/marketing/HR roles.
    """
    c = (company or "").lower().strip()
    for canon, alias in rules.company_aliases:
        if alias in c:
            return f"company:{canon}"
    for pat in rules.company_patterns:
        if pat.search(c):
            return f"company_pattern:{pat.pattern}"
    r = (role or "").strip()
    for pat in rules.role_patterns:
        if pat.search(r):
            return f"role:{pat.pattern}"
    body = jd_text or ""
    for pat in rules.keyword_patterns:
        if pat.search(body):
            return f"keyword:{pat.pattern}"
    return None
