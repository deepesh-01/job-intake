"""One-time bulk trim: enforce per-company cap on existing sheet rows.

Background: `runner._dedup_intra_batch` caps each scout run's contribution to
top-K (default 7) per company by `resume_match`. But the existing sheet was
populated before that rule landed and has Databricks at 400 rows, Nvidia at
300, Anthropic at 292, etc. — far above the cap. This script trims the
historical bloat so the swipe queue reflects the same rule.

Behavior:
- Reads all `Jobs` rows.
- For each company (normalized via `extract.normalize_company`), considers
  only `status=new` rows — never touches user-actioned rows (`applied`,
  `tailor`, `ready`, `rejected`, `skip`, `error`). Those represent decisions
  and stay untouched.
- For companies with > _PER_COMPANY_INTRA_BATCH_CAP `status=new` rows:
  ranks by (resume_match desc, posted_at desc, discovered_at desc), keeps
  the top K, and marks the rest `status=skip` with a marker note
  `"company_cap_bulk_trim_<date>"` so the trim is auditable + reversible.

Defaults to dry-run. Pass --apply to actually write.

    .venv/bin/python scripts/sheet_company_cap.py             # dry-run
    .venv/bin/python scripts/sheet_company_cap.py --apply
    .venv/bin/python scripts/sheet_company_cap.py --cap 5     # alternate cap
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from scout import extract  # noqa: E402
from scout.runner import _PER_COMPANY_INTRA_BATCH_CAP  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402

_TRIM_NOTE_PREFIX = "company_cap_bulk_trim_"


def _safe_float(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sort_key(row: dict[str, str]) -> tuple:
    """Higher resume_match wins; ties broken by posted_at then discovered_at desc."""
    return (
        -_safe_float(row.get("resume_match", "")),
        # Reverse date-string compare: longer strings sort higher when negated;
        # use lex inversion via 'Z' padding fallback for empty.
        # Equivalent: invert ISO date by negating ordinal not feasible cleanly.
        # We just sort descending on (posted_at, discovered_at) by inverting
        # via pre-padded comparison.
    )


def _order_for_keep(row: dict[str, str]) -> tuple:
    """Sort tuple — higher score → kept first."""
    score = _safe_float(row.get("resume_match", ""))
    posted = row.get("posted_at", "") or ""
    discovered = row.get("discovered_at", "") or ""
    return (-score, _neg_str(posted), _neg_str(discovered))


def _neg_str(s: str) -> tuple:
    """Pseudo-negate a string for descending sort by character ordinals."""
    return tuple(-ord(c) for c in s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--cap", type=int, default=_PER_COMPANY_INTRA_BATCH_CAP,
        help=f"max rows per company (default {_PER_COMPANY_INTRA_BATCH_CAP})",
    )
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument(
        "--top-n-companies", type=int, default=20,
        help="how many companies to show in the dry-run summary",
    )
    args = parser.parse_args()

    env = load_env()
    sheet = SheetClient(env.creds_path, env.sheet_id)

    # Read everything once.
    ws = sheet._ws(schema.JOBS_TAB)
    rows = ws.get_all_values()
    if len(rows) < 3:
        print("Sheet has fewer than 3 rows — nothing to do.")
        return 0

    header = rows[1]
    body = rows[2:]
    print(f"Sheet: {len(body)} job rows.\n")

    # Group by normalized company → list of (sheet_row_idx_1based, row_dict).
    # Sheet row index is i + 3 (row 1 = schema marker, row 2 = header).
    by_company: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    user_actioned: dict[str, int] = defaultdict(int)
    new_count = 0
    for i, r in enumerate(body):
        sheet_row_1based = i + 3
        row = dict(zip(header, r))
        status = (row.get("status") or "").strip()
        company = (row.get("company") or "").strip()
        if not company:
            continue
        if status != schema.STATUS_NEW:
            user_actioned[status] += 1
            continue
        ck = extract.normalize_company(company)
        by_company[ck].append((sheet_row_1based, row))
        new_count += 1

    print(f"  status=new rows: {new_count}")
    print(f"  user-actioned rows (preserved): {dict(user_actioned)}")
    print(f"  unique companies (in status=new): {len(by_company)}\n")

    # Compute trim list.
    over_cap: list[tuple[str, int, int]] = []  # (company, total, will_trim)
    rows_to_trim: list[tuple[int, str]] = []   # (sheet_row, original_company)
    for ck, group in by_company.items():
        if len(group) <= args.cap:
            continue
        # Sort: highest score first → keep, rest → trim.
        group.sort(key=lambda t: _order_for_keep(t[1]))
        keep_idx = group[: args.cap]
        trim_idx = group[args.cap :]
        # Use display name from one of the rows for output.
        display_name = trim_idx[0][1].get("company") or ck
        over_cap.append((display_name, len(group), len(trim_idx)))
        for sheet_row_1based, row in trim_idx:
            rows_to_trim.append((sheet_row_1based, display_name))

    over_cap.sort(key=lambda t: -t[1])

    print(f"=== Companies over cap (cap={args.cap}) ===")
    print(f"{'company':<40} {'total':>6} {'trim':>6}")
    print(f"{'-'*40} {'-'*6} {'-'*6}")
    for name, total, trim in over_cap[: args.top_n_companies]:
        print(f"{name[:40]:<40} {total:>6} {trim:>6}")
    if len(over_cap) > args.top_n_companies:
        more = sum(t for _, _, t in over_cap[args.top_n_companies:])
        print(f"... and {len(over_cap) - args.top_n_companies} more companies "
              f"contributing {more} additional trims")

    print(f"\nTOTAL companies over cap: {len(over_cap)}")
    print(f"TOTAL rows to trim (status=new → status=skip): {len(rows_to_trim)}")

    if not args.apply:
        print("\n[DRY RUN] Pass --apply to write changes.")
        return 0

    if not rows_to_trim:
        print("\nNothing to apply.")
        return 0

    # Apply: bulk update `status` and `notes` columns.
    print(f"\nApplying — updating {len(rows_to_trim)} rows...")
    note_marker = f"{_TRIM_NOTE_PREFIX}{date.today().isoformat()}"
    status_col = schema.col_letter("status")
    notes_col = schema.col_letter("notes")

    body_updates: list[dict[str, Any]] = []
    for sheet_row_1based, _name in rows_to_trim:
        body_updates.append({
            "range": f"{status_col}{sheet_row_1based}",
            "values": [[schema.STATUS_SKIP]],
        })
        body_updates.append({
            "range": f"{notes_col}{sheet_row_1based}",
            "values": [[note_marker]],
        })

    # Chunk to stay polite; gspread tolerates large bodies but we batch
    # at 200 ranges/call (~100 rows × 2 cols) to keep each call fast.
    CHUNK = 200
    written = 0
    for start in range(0, len(body_updates), CHUNK):
        chunk = body_updates[start : start + CHUNK]
        ws.batch_update(chunk, value_input_option="USER_ENTERED")
        written += len(chunk) // 2
        print(f"  ... {written}/{len(rows_to_trim)} rows updated")

    print(f"\nDone. Trimmed {len(rows_to_trim)} rows. "
          f"Marker note: {note_marker!r}")
    print("Web cache (30s TTL) will refresh on next request.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
