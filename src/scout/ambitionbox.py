"""AmbitionBox salary enrichment.

For rows where neither the source nor a sibling-source posting has comp,
look the company up on AmbitionBox and pull a typical-range estimate
from their crowd-sourced salary data. SQLite-backed cache keyed by
(normalized company, normalized role) so we don't re-fetch on every
scout run.

Architecture:
    1. lookup(company, role) → CompEstimate | None
    2. Cache hit (within TTL): return cached value (incl. negative-cache
       for known 404s — don't re-scrape companies AmbitionBox doesn't have)
    3. Cache miss: derive slug → fetch /salaries/<slug>-salaries → parse
       __NEXT_DATA__ for popularDesignations + totalSalaryAverage
    4. Pick the best role match (rapidfuzz against question), fall back
       to popularDesignations[0], fall back to totalSalaryAverage company avg
    5. Store + return

Caveats:
    - Slug derivation is heuristic. Works for ~80% of real Indian product
      cos verified by spot-check (razorpay, dell, tesco, waferwire). Fails
      on aliases (moonfrog → moonfrog-labs is the real slug). The cache
      records misses too so a failed lookup doesn't burn a fetch every run.
    - AmbitionBox averages are noisy: include interns + seniors + ex-
      employees. Use as a tier-indicator, not a precise number.
    - Fetches are throttled (1 req/sec/host via _http._throttle) so a full
      scout run with 100 unique cos takes ~2 min for the AmbitionBox pass.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from scout.sources._http import _throttle

log = logging.getLogger("scout.ambitionbox")
log.setLevel(logging.INFO)

_BASE_URL = "https://www.ambitionbox.com"
_HOST = "www.ambitionbox.com"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
_FETCH_TIMEOUT_S = 15.0

_HIT_TTL_DAYS = 30   # successful estimates valid 30 days
_MISS_TTL_DAYS = 7   # negative-cache: don't re-scrape 404s for a week

# Suffixes commonly appended to Indian-tech company names that AmbitionBox
# slugs typically OMIT. Stripping them gives a better first-attempt slug.
_TRIM_SUFFIX_PATTERNS = [
    r"\s+\(india\)$",
    r"\s+pvt\.?\s*ltd\.?$",
    r"\s+private\s+limited$",
    r"\s+pte\.?\s*ltd\.?$",
    r"\s+ltd\.?$",
    r"\s+limited$",
    r"\s+llp$",
    r"\s+inc\.?$",
    r"\s+llc$",
    r"\s+corporation$",
    r"\s+corp\.?$",
    r"\s+co\.?$",
    r"\s+gmbh$",
    r"\s+sa$",
    r"\s+ag$",
    r"\s+s\.r\.l\.?$",
    r"\s+sdn\s+bhd$",
]
_TRIM_SUFFIX_RE = re.compile("|".join(_TRIM_SUFFIX_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class CompEstimate:
    """Per-company comp estimate from AmbitionBox.

    Values are in INR rupees (consistent with the rest of the pipeline's
    comp_low / comp_high columns). `source_url` points at the page the
    estimate was scraped from for audit + debugging.
    """
    company_norm: str
    role_norm: str | None
    comp_low: int   # rupees
    comp_high: int  # rupees
    avg: int        # rupees
    source_url: str
    matched_role: str | None     # the AmbitionBox role title that matched, if any
    matched_score: int           # 0-100, fuzzy match score (100 = exact, 0 = company-only fallback)


def _slugify(company: str) -> str:
    """Lowercase + suffix-trim + non-alphanumeric → hyphens. Empty if input is junk."""
    s = (company or "").strip().lower()
    if not s:
        return ""
    s = _TRIM_SUFFIX_RE.sub("", s).strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _normalize_role(role: str) -> str:
    """Lowercase + collapse non-alpha. Used as cache key + for fuzzy match."""
    return re.sub(r"[^a-z0-9]+", " ", (role or "").lower()).strip()


def _fuzzy_score(a: str, b: str) -> int:
    """Quick token-set similarity 0-100. Avoids the rapidfuzz dep just for
    this — naive Jaccard on tokens is good enough for role-title matching
    when both strings are 2-6 words."""
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0
    inter = len(ta & tb)
    union = len(ta | tb)
    return int(round(100 * inter / union))


# ── cache ──


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "ambitionbox_cache.sqlite"


def _open_cache(data_dir: Path) -> sqlite3.Connection:
    p = _cache_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ab_cache (
            company_norm TEXT NOT NULL,
            role_norm    TEXT NOT NULL,
            comp_low     INTEGER,
            comp_high    INTEGER,
            avg          INTEGER,
            matched_role TEXT,
            matched_score INTEGER,
            source_url   TEXT,
            is_miss      INTEGER NOT NULL DEFAULT 0,
            cached_at    TEXT NOT NULL,
            PRIMARY KEY (company_norm, role_norm)
        )
        """
    )
    return conn


def _cache_get(conn: sqlite3.Connection, company_norm: str, role_norm: str) -> tuple[CompEstimate | None, bool] | None:
    """Returns (estimate or None for miss, is_fresh) or None for cache-miss-the-cache."""
    row = conn.execute(
        "SELECT comp_low, comp_high, avg, matched_role, matched_score, source_url, "
        "       is_miss, cached_at FROM ab_cache WHERE company_norm=? AND role_norm=?",
        (company_norm, role_norm),
    ).fetchone()
    if row is None:
        return None
    comp_low, comp_high, avg, matched_role, matched_score, source_url, is_miss, cached_at = row
    age_days = (time.time() - time.mktime(time.strptime(cached_at, "%Y-%m-%dT%H:%M:%S"))) / 86400
    ttl = _MISS_TTL_DAYS if is_miss else _HIT_TTL_DAYS
    if age_days > ttl:
        return None  # stale
    if is_miss:
        return (None, True)  # negative-cache hit
    est = CompEstimate(
        company_norm=company_norm,
        role_norm=role_norm or None,
        comp_low=comp_low,
        comp_high=comp_high,
        avg=avg,
        source_url=source_url,
        matched_role=matched_role,
        matched_score=matched_score or 0,
    )
    return (est, True)


def _cache_set(
    conn: sqlite3.Connection,
    *,
    company_norm: str,
    role_norm: str,
    estimate: CompEstimate | None,
) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if estimate is None:
        conn.execute(
            "INSERT OR REPLACE INTO ab_cache (company_norm, role_norm, is_miss, cached_at) "
            "VALUES (?, ?, 1, ?)",
            (company_norm, role_norm, now),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO ab_cache "
            "(company_norm, role_norm, comp_low, comp_high, avg, matched_role, "
            " matched_score, source_url, is_miss, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (company_norm, role_norm,
             estimate.comp_low, estimate.comp_high, estimate.avg,
             estimate.matched_role, estimate.matched_score, estimate.source_url, now),
        )
    conn.commit()


# ── fetch + parse ──


def _fetch_salaries_html(slug: str) -> str | None:
    """Fetch /salaries/<slug>-salaries. Returns HTML on 200, None on 4xx/5xx."""
    url = f"{_BASE_URL}/salaries/{slug}-salaries"
    _throttle(_HOST)
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,application/xhtml+xml"},
            timeout=_FETCH_TIMEOUT_S,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        log.debug("ambitionbox fetch failed: %s — %s", url, e)
        return None
    if r.status_code != 200:
        log.debug("ambitionbox %s — %s", url, r.status_code)
        return None
    return r.text


def _extract_next_data(html: str) -> dict[str, Any] | None:
    """Pull Next.js `__NEXT_DATA__` JSON island from the page."""
    tree = HTMLParser(html)
    el = tree.css_first("script#__NEXT_DATA__")
    if el is None:
        return None
    try:
        return json.loads(el.text())
    except (ValueError, TypeError):
        return None


def _pick_typical_range(designations: list[dict[str, Any]], target_role: str) -> tuple[dict[str, Any], int] | None:
    """Pick the popularDesignation entry that best matches `target_role`.
    Returns (entry, fuzzy_score) or None if list is empty.
    """
    if not designations:
        return None
    target_norm = _normalize_role(target_role)
    if not target_norm:
        return designations[0], 0
    best = None
    best_score = -1
    for d in designations:
        # AmbitionBox's salary API uses `jobProfileName`; older endpoints
        # used `title` / `name` / `designation`. Cover all variants.
        name = (
            d.get("jobProfileName")
            or d.get("title")
            or d.get("name")
            or d.get("designation")
            or ""
        )
        score = _fuzzy_score(target_norm, _normalize_role(name))
        if score > best_score:
            best_score = score
            best = d
    if best is None:
        return designations[0], 0
    return best, max(0, best_score)


def _to_int_rupees(v: Any) -> int:
    """AmbitionBox returns CTC values as either int (rupees) or str ('3181041.0...')."""
    if v is None:
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _parse_estimate(
    next_data: dict[str, Any],
    *,
    company_norm: str,
    role: str | None,
    source_url: str,
) -> CompEstimate | None:
    """Pull out the best comp estimate we can from a Next.js page payload."""
    pp = next_data.get("props", {}).get("pageProps", {}) or {}

    # Source 1: per-role popularDesignations (best when target role matches)
    designations = (
        pp.get("filtersData", {}).get("data", {}).get("jobProfiles")
        or pp.get("popularDesignations")
        or pp.get("jobProfiles")
        or []
    )
    if isinstance(designations, list) and designations:
        pick = _pick_typical_range(designations, role or "")
        if pick:
            entry, score = pick
            low = _to_int_rupees(entry.get("typicalMinCtc") or entry.get("minCtc"))
            high = _to_int_rupees(entry.get("typicalMaxCtc") or entry.get("maxCtc"))
            avg = _to_int_rupees(entry.get("avgCtc"))
            if low and high:
                return CompEstimate(
                    company_norm=company_norm,
                    role_norm=_normalize_role(role) if role else None,
                    comp_low=low,
                    comp_high=high,
                    avg=avg,
                    source_url=source_url,
                    matched_role=(
                        entry.get("jobProfileName")
                        or entry.get("title")
                        or entry.get("name")
                        or entry.get("designation")
                    ),
                    matched_score=score,
                )

    # Source 2: company-overall avg (totalSalaryAverage in lakhs)
    summary = pp.get("salariesSummaryData") or pp.get("salaryData", {}).get("data") or {}
    overall_avg_lpa = summary.get("totalSalaryAverage") or pp.get("salaryData", {}).get("data", {}).get("totalSalaryAverage")
    if overall_avg_lpa:
        try:
            avg_rupees = int(float(overall_avg_lpa) * 100_000)
            # Use ±25% as a coarse range when only the company-wide average is known.
            return CompEstimate(
                company_norm=company_norm,
                role_norm=None,
                comp_low=int(avg_rupees * 0.75),
                comp_high=int(avg_rupees * 1.25),
                avg=avg_rupees,
                source_url=source_url,
                matched_role=None,
                matched_score=0,
            )
        except (TypeError, ValueError):
            pass

    return None


# ── public API ──


def lookup(
    company: str,
    *,
    role: str | None = None,
    data_dir: Path | str | None = None,
) -> CompEstimate | None:
    """Look up a comp estimate for (company, role). Returns None if no
    estimate could be derived. Uses an on-disk cache to avoid re-fetching."""
    if not (company or "").strip():
        return None
    if data_dir is None:
        data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir = Path(data_dir)

    company_norm = re.sub(r"[^a-z0-9]+", " ", company.lower()).strip()
    role_norm = _normalize_role(role) if role else ""

    conn = _open_cache(data_dir)
    cached = _cache_get(conn, company_norm, role_norm)
    if cached is not None:
        est, _fresh = cached
        return est

    # Cache miss — fetch.
    slug = _slugify(company)
    if not slug:
        _cache_set(conn, company_norm=company_norm, role_norm=role_norm, estimate=None)
        return None

    html = _fetch_salaries_html(slug)
    if html is None:
        _cache_set(conn, company_norm=company_norm, role_norm=role_norm, estimate=None)
        return None

    next_data = _extract_next_data(html)
    if next_data is None:
        log.debug("ambitionbox: no __NEXT_DATA__ for %s", slug)
        _cache_set(conn, company_norm=company_norm, role_norm=role_norm, estimate=None)
        return None

    source_url = f"{_BASE_URL}/salaries/{slug}-salaries"
    estimate = _parse_estimate(next_data, company_norm=company_norm, role=role, source_url=source_url)
    _cache_set(conn, company_norm=company_norm, role_norm=role_norm, estimate=estimate)
    return estimate
