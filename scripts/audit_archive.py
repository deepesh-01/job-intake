"""Re-evaluate the Skip archive tab against current filters.

After several rounds of filter expansion + a comp-parser bugfix, ask:
which archived rows would NOT be dropped if we re-ran today's pipeline
on them? Those are recovery candidates.

Buckets the audit reports:

A. **False-positive recovery**: archived rows that pass ALL current filters
   (exclude rules + location filter + comp parser). They were dropped under
   an older filter version that's since been corrected.

B. **Cap-slack recovery**: archived rows from companies that now have
   < cap status=new rows in the active Jobs tab. Top-K by resume_match,
   subject to passing current filters.

C. **Confirmed-correct drops** (no recovery): explicit-low-comp,
   non-software, blend-cap-overflow. Reported as a sanity check.

This script is READ-ONLY by default. Pass --apply to actually move
recovery candidates back from Skip_<date> to Jobs (status=new).

    .venv/bin/python scripts/audit_archive.py
    .venv/bin/python scripts/audit_archive.py --apply
    .venv/bin/python scripts/audit_archive.py --bucket A   # only false-positive recovery
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env, load_yaml  # noqa: E402
from scout import exclude as exclude_mod  # noqa: E402
from scout import extract  # noqa: E402
from scout import location as loc_filter  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402

INR_FLOOR = 4_000_000
USD_FLOOR = 60_000
COMPANY_CAP = 7
FX = {"GBP": 1.20, "EUR": 1.05, "SGD": 0.74, "AUD": 0.65, "CAD": 0.72}


def _source_type(row_id: str) -> str:
    return (row_id or "").split(":", 1)[0]


def _safe_float(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def _evaluate_filters(row: dict[str, str], rules) -> tuple[bool, str]:
    """Returns (passes, drop_reason). True = would keep under current filters."""
    src = _source_type(row.get("id", ""))
    company = row.get("company", "")
    role = row.get("role", "")
    jd_snippet = row.get("jd_snippet", "")
    location = row.get("location", "")
    comp_string = row.get("comp_string", "")

    # 1. exclude rules (company / role / company_pattern / JD-keyword)
    er = exclude_mod.excluded_reason(company, jd_snippet, rules, role=role)
    if er:
        return False, f"exclude:{er}"

    # 2. location filter
    lr = loc_filter.location_filter_reason(location, jd_snippet, src)
    if lr:
        return False, f"location:{lr}"

    # 3. comp floor (re-parsed with fixed parser)
    if comp_string:
        c = extract.parse_comp("", fx_rates=FX, explicit_comp_string=comp_string)
        if c.currency == "INR" and c.high is not None and c.high < INR_FLOOR:
            return False, f"comp_below_inr:{c.high}"
        if (c.currency in {"USD", "GBP", "EUR", "SGD", "AUD", "CAD"}
                and c.high_usd is not None and c.high_usd < USD_FLOOR):
            return False, f"comp_below_usd:{c.high_usd}"

    return True, "passes_current_filters"


def _archive_tabs(sheet: SheetClient) -> list[str]:
    return [ws.title for ws in sheet._ss.worksheets() if ws.title.startswith("Skip_")]


def _read_archive(sheet: SheetClient) -> list[tuple[str, int, dict[str, str]]]:
    """Returns [(tab_name, row_idx_1based, row_dict), ...]."""
    out = []
    for tab in _archive_tabs(sheet):
        ws = sheet._ws(tab)
        rows = ws.get_all_values()
        if len(rows) < 2:
            continue
        header = rows[0]
        for i, r in enumerate(rows[1:], start=2):
            d = dict(zip(header, r))
            if d.get("id"):
                out.append((tab, i, d))
    return out


def _read_jobs_tab_company_counts(sheet: SheetClient) -> dict[str, int]:
    """Count status=new rows per normalized company in active Jobs tab."""
    ws = sheet._ws(schema.JOBS_TAB)
    rows = ws.get_all_values()
    if len(rows) < 3:
        return {}
    header = rows[1]
    counts: dict[str, int] = defaultdict(int)
    for r in rows[2:]:
        d = dict(zip(header, r))
        if (d.get("status") or "").strip() != schema.STATUS_NEW:
            continue
        co = extract.normalize_company(d.get("company", ""))
        if co:
            counts[co] += 1
    return counts


def _marker_bucket(notes: str) -> str:
    n = notes or ""
    if "company_cap_bulk_trim" in n:
        return "company_cap"
    if "role_filter_bulk_trim" in n:
        return "role_filter_v1"
    if "location_filter_bulk_trim" in n:
        return "location_filter"
    if "exclude_rules_bulk_trim" in n:
        return "exclude_rules_v2"
    if "comp_filter_bulk_trim" in n:
        return "comp_blend"
    if "recomp_inr_below_floor" in n:
        return "recomp_below_floor"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--apply", action="store_true",
                        help="actually move recovery candidates back to Jobs")
    parser.add_argument("--bucket", default=None,
                        help="restrict recovery to one bucket: A, B, or both")
    args = parser.parse_args()

    env = load_env()
    rules = exclude_mod.compile_rules(load_yaml(env.exclude_path))
    sheet = SheetClient(env.creds_path, env.sheet_id)

    print("Reading archive tabs...")
    archived = _read_archive(sheet)
    print(f"  {len(archived)} archived rows")

    print("Reading active Jobs tab company counts...")
    co_counts = _read_jobs_tab_company_counts(sheet)
    print(f"  {len(co_counts)} unique companies in active queue")

    # Bucket by original drop marker
    marker_breakdown: Counter[str] = Counter()
    for _tab, _idx, row in archived:
        marker_breakdown[_marker_bucket(row.get("notes", ""))] += 1
    print("\nArchive composition by original drop reason:")
    for m, n in marker_breakdown.most_common():
        print(f"  {n:>5}  {m}")

    # Re-evaluate each archived row against current filters
    print("\nRe-evaluating against current filters (this hits no API)...")
    fp_passes: list[tuple[str, int, dict[str, str], str]] = []   # bucket A
    cap_eligible: dict[str, list[tuple[str, int, dict[str, str]]]] = defaultdict(list)
    confirmed_drops: list[tuple[str, int, dict[str, str], str]] = []

    for tab, idx, row in archived:
        passes, reason = _evaluate_filters(row, rules)
        bucket = _marker_bucket(row.get("notes", ""))

        if not passes:
            confirmed_drops.append((tab, idx, row, reason))
            continue

        # Row passes current filters. Was originally dropped — recovery candidate.
        if bucket == "company_cap":
            co_norm = extract.normalize_company(row.get("company", ""))
            cap_eligible[co_norm].append((tab, idx, row))
        else:
            fp_passes.append((tab, idx, row, bucket))

    # Bucket A summary — false-positive recoveries
    print(f"\n=== BUCKET A: false-positive recovery candidates: {len(fp_passes)} ===")
    by_bucket = Counter(b for _, _, _, b in fp_passes)
    for b, n in by_bucket.most_common():
        print(f"  {n:>4}  was originally dropped by: {b}")
    if fp_passes:
        print("  Sample (top 15):")
        for tab, idx, row, b in fp_passes[:15]:
            co = (row.get("company") or "?")[:25]
            role = (row.get("role") or "?")[:55]
            loc = (row.get("location") or "?")[:25]
            print(f"    [{b:<20}]  {co:<25}  {role:<55}  loc={loc}")

    # Bucket B summary — cap-slack recoveries
    print(f"\n=== BUCKET B: cap-slack recovery candidates ===")
    cap_recoveries: list[tuple[str, int, dict[str, str], str]] = []
    for co_norm, candidates in cap_eligible.items():
        active = co_counts.get(co_norm, 0)
        slots = max(0, COMPANY_CAP - active)
        if slots <= 0:
            continue
        candidates.sort(key=lambda t: -_safe_float(t[2].get("resume_match", "")))
        for tab, idx, row in candidates[:slots]:
            cap_recoveries.append((tab, idx, row, f"cap_slack:{co_norm}:slot_avail"))

    print(f"  total: {len(cap_recoveries)}")
    if cap_recoveries:
        co_grp = Counter(c[2].get("company") for c in cap_recoveries)
        for c, n in co_grp.most_common(15):
            print(f"    {n:>4}  {c}")

    # Bucket C — confirmed correct
    print(f"\n=== BUCKET C: confirmed correct drops (would still drop today): {len(confirmed_drops)} ===")
    cd_reasons = Counter(d[3].split(":", 1)[0] for d in confirmed_drops)
    for r, n in cd_reasons.most_common():
        print(f"    {n:>5}  {r}")

    print(f"\nTOTAL recovery candidates: A={len(fp_passes)} + B={len(cap_recoveries)} = {len(fp_passes) + len(cap_recoveries)}")

    if not args.apply:
        print("\n[DRY RUN] Pass --apply to move recovery candidates back to Jobs.")
        print("         Pass --bucket A to recover only false-positives,")
        print("              --bucket B to recover only cap-slack rows.")
        return 0

    # ── Apply recovery ──
    selected: list[tuple[str, int, dict[str, str], str]] = []
    if args.bucket in {None, "A", "both"}:
        selected.extend(fp_passes)
    if args.bucket in {None, "B", "both"}:
        selected.extend(cap_recoveries)
    if not selected:
        print("\nNo recovery candidates after bucket filter.")
        return 0

    # Group by archive tab to delete in batches
    by_tab: dict[str, list[int]] = defaultdict(list)
    payload_rows: list[list[Any]] = []
    ws_jobs = sheet._ws(schema.JOBS_TAB)
    jobs_rows = ws_jobs.get_all_values()
    jobs_header = jobs_rows[1]

    today_marker = f"recovered_from_archive_{date.today().isoformat()}"
    for tab, idx, row, reason in selected:
        # Build a Jobs-row preserving all columns; flip status & note
        row_values = [row.get(col, "") for col in jobs_header]
        # status=new
        status_idx = jobs_header.index("status")
        row_values[status_idx] = schema.STATUS_NEW
        # notes: original marker + recovery marker
        notes_idx = jobs_header.index("notes")
        existing_note = row_values[notes_idx] or ""
        row_values[notes_idx] = f"{existing_note}|{today_marker}|{reason}"[:500]
        payload_rows.append(row_values)
        by_tab[tab].append(idx)

    # Append to Jobs tab
    print(f"\nAppending {len(payload_rows)} rows to Jobs tab...")
    ws_jobs.append_rows(payload_rows, value_input_option="USER_ENTERED")

    # Delete from each archive tab — sort indices descending to avoid shift
    for tab, indices in by_tab.items():
        ws_arch = sheet._ws(tab)
        for idx in sorted(indices, reverse=True):
            ws_arch.delete_rows(idx)
        print(f"  removed {len(indices)} rows from {tab}")

    print(f"\nDone. Recovered {len(selected)} rows back to Jobs (status=new).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
