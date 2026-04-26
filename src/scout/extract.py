"""Extraction utilities — HTML→text, comp parsing, role/company normalization.

Comp parsing is currency-aware and regex-based per §13.4. NEVER assume USD
when currency cannot be detected — return None and let tag.py fire `comp_unknown`.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass

from markdownify import markdownify
from selectolax.parser import HTMLParser


# ────────────────────────────── HTML → text ──────────────────────────────


_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|mdash|ndash|nbsp|#\d+);")


def _decode_entities(raw: str) -> str:
    """Greenhouse and a few others double-encode HTML (the `content` arrives
    as `&lt;p&gt;`-escaped). Without unescaping, selectolax sees the tags as
    plain text and they survive into the parser output. Always unescape; if
    entities remain (double encoding), unescape again."""
    s = _html.unescape(raw)
    if _ENTITY_RE.search(s):
        s = _html.unescape(s)
    return s


def html_to_text(raw: str) -> str:
    """Best-effort HTML to plaintext. Never raises; returns '' on garbage."""
    if not raw:
        return ""
    s = _decode_entities(raw)
    try:
        tree = HTMLParser(s)
        text = tree.text(separator="\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)


def html_to_markdown(raw: str) -> str:
    if not raw:
        return ""
    s = _decode_entities(raw)
    try:
        return markdownify(s, heading_style="ATX")
    except Exception:
        return html_to_text(s)


def jd_snippet(text: str, n: int = 800) -> str:
    return text[:n].replace("\n", " ").strip()


# ────────────────────────────── normalization ──────────────────────────────

_COMPANY_SUFFIXES = re.compile(
    r"\s*(?:,?\s*(?:llc|inc|incorporated|corp|corporation|ltd|limited|pvt\.?\s*ltd\.?|"
    r"private\s+limited|gmbh|sa|ag|bv|nv|plc|co\.?))\.?\s*$",
    flags=re.IGNORECASE,
)


def normalize_company(name: str) -> str:
    s = (name or "").strip()
    s = _COMPANY_SUFFIXES.sub("", s)
    return s.lower().strip()


def normalize_role(role: str) -> str:
    s = (role or "").strip().lower()
    # Drop bracketed/parenthesized qualifiers: "(Remote)", "[US]"
    s = re.sub(r"[\[\(].+?[\]\)]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ────────────────────────────── comp parsing ──────────────────────────────


@dataclass(frozen=True)
class Comp:
    comp_string: str | None
    currency: str | None
    low: int | None
    high: int | None
    low_usd: int | None
    high_usd: int | None


# Currency detection. Priority is layered:
# 1. Foreign currency symbols/codes ($/£/€/S$/A$/C$, USD/GBP/EUR/SGD/AUD/CAD).
# 2. Explicit INR symbols/words (₹, Rs, INR, LPA, lakh, crore, cr).
# 3. INR L-shorthand (e.g. "30L") — only if no foreign currency was found,
#    since "30L" is ambiguous and could appear in non-comp text.
_FOREIGN_DETECT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Foreign primary symbols/codes — checked first so "$120,000—$200,000" wins
    # over a stray "30L" elsewhere in the body.
    ("SGD", re.compile(r"(?:S\$|\bSGD\b)")),
    ("AUD", re.compile(r"(?:A\$|\bAUD\b)")),
    ("CAD", re.compile(r"(?:C\$|\bCAD\b)")),
    ("USD", re.compile(r"(?:\$|\bUSD\b)", re.IGNORECASE)),
    ("GBP", re.compile(r"(?:£|\bGBP\b)", re.IGNORECASE)),
    ("EUR", re.compile(r"(?:€|\bEUR\b)", re.IGNORECASE)),
]
_INR_EXPLICIT_RE = re.compile(
    # Word boundaries are critical: without `\b`, "rs" matches inside
    # "stakeholders" / "engineers" / "users" and "INR" matches inside any
    # ALL-CAPS abbreviation that contains those letters in sequence.
    r"(?:₹|\bRs\b|\bINR\b|\blpa\b|\blakhs?\b|\bcrore?s?\b)",
    re.IGNORECASE,
)
# `\bcr\b` was too noisy ("cr+ users", "MCR", etc.) so require it next to a
# digit to disambiguate from generic abbreviations.
_INR_CR_RE = re.compile(r"\b\d+(?:\.\d+)?\s*cr\b", re.IGNORECASE)
_INR_SHORTHAND_RE = re.compile(r"\b\d{1,3}\s*L\b")
# Comp-context anchor — the L-shorthand and `Nx cr` must appear in salary
# context, not user-count context ("1L users", "5cr+ users") which is common
# in Indian product JDs. Window: ±80 chars around the numeric token.
_COMP_CONTEXT_RE = re.compile(
    r"\b(?:salar(?:y|ies)|compensation|ctc|tc\b|package|base|cash|fixed|"
    r"variable|stipend|annum|p\.a\.|per\s+year|annually|esop|equity)\b",
    re.IGNORECASE,
)


def _shorthand_in_comp_context(text: str, pat: re.Pattern[str]) -> bool:
    for m in pat.finditer(text):
        window = text[max(0, m.start() - 80) : m.end() + 80]
        if _COMP_CONTEXT_RE.search(window):
            return True
    return False

# Numeric token allowing "150", "150.5", "150,000", "150K", "1.5L", etc.
_NUM_RE = r"\d{1,3}(?:[,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"

# Range separators
_SEP_RE = r"\s*(?:-|–|—|to|\bto\b|—|‐)\s*"

# Patterns that capture a (low, high) tuple for each currency family.
# Order matters: try richer patterns first.

# Range separators include hyphen, en-dash (U+2013), em-dash (U+2014),
# minus sign (U+2212), the literal word "to", and the figure-dash (U+2012).
_RANGE_SEP = r"\s*(?:-|–|—|‒|−|to)\s*"

# INR-specific patterns: numbers expressed in lakh/crore/LPA.
_INR_RANGE_RE = re.compile(
    rf"(?:₹|Rs\.?|INR)?\s*({_NUM_RE})\s*(?:-|–|—|‒|−|to)\s*(?:₹|Rs\.?|INR)?\s*({_NUM_RE})\s*(?P<unit>lpa|lakhs?|cr|crores?|l)\b",
    re.IGNORECASE,
)
_INR_SINGLE_RE = re.compile(
    rf"(?:up\s*to\s+)?(?:₹|Rs\.?|INR)?\s*({_NUM_RE})\s*(?P<unit>lpa|lakhs?|cr|crores?|l)\b\+?",
    re.IGNORECASE,
)

# USD/other-currency patterns: numbers expressed with k/K or commas.
_FOREIGN_RANGE_RE = re.compile(
    rf"(?:[$£€]|S\$|A\$|C\$)\s*({_NUM_RE})\s*[kK]?\s*(?:-|–|—|‒|−|to)\s*(?:[$£€]|S\$|A\$|C\$)?\s*({_NUM_RE})\s*[kK]?",
    re.IGNORECASE,
)
_FOREIGN_SINGLE_RE = re.compile(
    rf"(?:up\s*to\s+)?(?:[$£€]|S\$|A\$|C\$)\s*({_NUM_RE})\s*[kK]?\+?",
    re.IGNORECASE,
)


def detect_currency(text: str) -> str | None:
    if not text:
        return None
    for code, pat in _FOREIGN_DETECT_PATTERNS:
        if pat.search(text):
            return code
    if _INR_EXPLICIT_RE.search(text):
        return "INR"
    # "Nx cr" only counts as INR when in comp context (avoids "5cr+ users").
    if _shorthand_in_comp_context(text, _INR_CR_RE):
        return "INR"
    # "NN L" only counts as INR when in comp context (avoids "1L users").
    if _shorthand_in_comp_context(text, _INR_SHORTHAND_RE):
        return "INR"
    return None


def _to_int(s: str) -> float:
    return float(s.replace(",", ""))


def _scale_inr(n: float, unit: str) -> int:
    u = unit.lower()
    if u in {"cr", "crore", "crores"}:
        return int(round(n * 10_000_000))
    # lakh / lakhs / lpa / l
    return int(round(n * 100_000))


def _scale_foreign_text(matched_text: str, n: float) -> int:
    """Foreign-currency: 'k'/'K' suffix multiplies by 1000; bare number is taken as-is."""
    if re.search(r"\d\s*[kK]", matched_text):
        return int(round(n * 1000))
    return int(round(n))


_EQUITY_ONLY_RE = re.compile(
    r"\b(equity\s*only|esop\s*only|equity\s+based|stock\s+only)\b", re.IGNORECASE
)


def parse_comp(
    text: str,
    *,
    fx_rates: dict[str, float],
    explicit_comp_string: str | None = None,
) -> Comp:
    """Parse compensation from a JD body. Returns a Comp dataclass.

    fx_rates maps non-USD non-INR ISO codes to USD multipliers (e.g. {"GBP": 1.20}).
    explicit_comp_string short-circuits scanning if a source pre-extracted a comp line.
    """
    body = explicit_comp_string or text or ""
    if not body:
        return Comp(None, None, None, None, None, None)

    if _EQUITY_ONLY_RE.search(body) and not detect_currency(body):
        return Comp(body[:120], None, None, None, None, None)

    currency = detect_currency(body)
    if currency is None:
        return Comp(None, None, None, None, None, None)

    matched_text: str | None = None
    low: int | None = None
    high: int | None = None

    if currency == "INR":
        m = _INR_RANGE_RE.search(body)
        if m:
            low = _scale_inr(_to_int(m.group(1)), m.group("unit"))
            high = _scale_inr(_to_int(m.group(2)), m.group("unit"))
            matched_text = m.group(0)
        else:
            m = _INR_SINGLE_RE.search(body)
            if m:
                v = _scale_inr(_to_int(m.group(1)), m.group("unit"))
                if "up to" in m.group(0).lower():
                    low, high = None, v
                else:
                    low, high = v, v
                matched_text = m.group(0)
    else:
        m = _FOREIGN_RANGE_RE.search(body)
        if m:
            low = _scale_foreign_text(m.group(0), _to_int(m.group(1)))
            high = _scale_foreign_text(m.group(0), _to_int(m.group(2)))
            matched_text = m.group(0)
        else:
            m = _FOREIGN_SINGLE_RE.search(body)
            if m:
                v = _scale_foreign_text(m.group(0), _to_int(m.group(1)))
                if "up to" in m.group(0).lower():
                    low, high = None, v
                else:
                    low, high = v, v
                matched_text = m.group(0)

    if low is None and high is None:
        # Currency was mentioned but no parseable number — degrade gracefully
        return Comp(None, currency, None, None, None, None)

    low_usd, high_usd = _to_usd(currency, low, high, fx_rates)
    return Comp(
        comp_string=(matched_text or explicit_comp_string)[:200] if (matched_text or explicit_comp_string) else None,
        currency=currency,
        low=low,
        high=high,
        low_usd=low_usd,
        high_usd=high_usd,
    )


def _to_usd(
    currency: str,
    low: int | None,
    high: int | None,
    fx_rates: dict[str, float],
) -> tuple[int | None, int | None]:
    if currency == "USD":
        return low, high
    if currency == "INR":
        # Display-only — treat 1 USD ≈ 83 INR conservatively. Used as a hint only;
        # comp_ok evaluation for INR uses inr_floor directly, not the USD-equivalent.
        rate = 1 / 83.0
    else:
        mult = fx_rates.get(currency)
        if mult is None:
            return None, None
        rate = mult
    return (
        int(round(low * rate)) if low is not None else None,
        int(round(high * rate)) if high is not None else None,
    )
