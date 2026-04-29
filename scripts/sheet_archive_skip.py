"""Archive status=skip rows from Jobs tab into dated archive tabs (CLI).

Thin wrapper around `scout.archive.archive_skip_rows()`. The runner also
calls that function at the end of each daily scout, so this script is
mostly for ad-hoc / off-cycle archival.

See `src/scout/archive.py` for design rationale (failure-safe ordering,
smart 5K-row split, idempotency).

    .venv/bin/python scripts/sheet_archive_skip.py             # dry-run
    .venv/bin/python scripts/sheet_archive_skip.py --apply
    .venv/bin/python scripts/sheet_archive_skip.py --cap 10000
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from scout import archive  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="actually write")
    parser.add_argument("--cap", type=int, default=archive.DEFAULT_CAP,
                        help=f"max rows per archive tab (default {archive.DEFAULT_CAP})")
    args = parser.parse_args()

    env = load_env()
    sheet = SheetClient(env.creds_path, env.sheet_id)

    # Inspect current state for the dry-run summary.
    ws_jobs = sheet._ws(schema.JOBS_TAB)
    rows = ws_jobs.get_all_values()
    if len(rows) < 3:
        print("Jobs tab has fewer than 3 rows — nothing to do.")
        return 0
    header = rows[1]
    body = rows[2:]
    status_col_idx = schema.col_idx("status")
    skip_rows = [r for r in body
                 if (r[status_col_idx] if status_col_idx < len(r) else "").strip() == schema.STATUS_SKIP]
    keep_count = len(body) - len(skip_rows)
    print(f"Jobs tab: {len(body)} job rows.  keep: {keep_count}  skip: {len(skip_rows)}")

    if not skip_rows:
        print("\nNo skip rows to archive.")
        return 0

    today = date.today().isoformat()
    plans = archive.plan_chunks(sheet, skip_rows, args.cap, today)
    print(f"\nArchive plan ({len(plans)} chunk(s), cap={args.cap}):")
    for p in plans:
        marker = " [NEW]" if p["is_new"] else " [APPEND]"
        print(f"  {p['name']:30}  +{len(p['rows']):>5} rows  "
              f"(existing: {p['existing']}, will end at: {p['existing'] + len(p['rows'])}){marker}")

    if not args.apply:
        print("\n[DRY RUN] Pass --apply to execute.")
        return 0

    print("\nApplying...")
    archived = archive.archive_skip_rows(
        sheet,
        cap=args.cap,
        on_progress=lambda msg: print(f"  {msg}"),
    )
    print(f"\nDone. Archived {archived} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
