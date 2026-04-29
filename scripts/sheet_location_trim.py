"""One-time bulk trim: apply runner-level location filter to existing rows.

Background: `src/scout/location.py::location_filter_reason` was added
2026-04-29 to drop non-India non-global-remote rows from sources that
pull full company boards. Going forward the runner drops these at
fetch time. This script trims the historical rows that landed before
the filter existed.

Behavior:
- Reads all `Jobs` rows.
- For each row with `status=new`, derives source_type from the row id
  (prefix before first colon) and evaluates `location_filter_reason`. If
  it returns a drop reason, marks `status=skip` with marker note
  `location_filter_bulk_trim_<date>`.
- User-actioned rows (applied/tailor/ready/rejected/skip/error) untouched.
- Uses `jd_snippet` as a proxy for `jd_text` for the rare "N Locations"
  case. Snippets are ~100-200 chars and usually contain location info if
  the role mentions India. Acceptable false-negative rate.

Defaults to dry-run. Pass --apply to write.

    .venv/bin/python scripts/sheet_location_trim.py
    .venv/bin/python scripts/sheet_location_trim.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from scout import location as loc_filter  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402

_TRIM_NOTE_PREFIX = "location_filter_bulk_trim_"


def _source_type_from_id(row_id: str) -> str:
    """row id is `<source_type>:<board_id>:<posting_id>`."""
    return (row_id or "").split(":", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="actually write")
    args = parser.parse_args()

    env = load_env()
    sheet = SheetClient(env.creds_path, env.sheet_id)

    ws = sheet._ws(schema.JOBS_TAB)
    rows = ws.get_all_values()
    if len(rows) < 3:
        print("Sheet has fewer than 3 rows.")
        return 0

    header = rows[1]
    body = rows[2:]
    print(f"Sheet: {len(body)} job rows.")

    matches: list[tuple[int, dict[str, str], str]] = []
    new_total = 0

    for i, r in enumerate(body):
        sheet_row_1based = i + 3
        row = dict(zip(header, r))
        if (row.get("status") or "").strip() != schema.STATUS_NEW:
            continue
        new_total += 1
        source_type = _source_type_from_id(row.get("id", ""))
        location = row.get("location", "")
        jd_snippet = row.get("jd_snippet", "")
        reason = loc_filter.location_filter_reason(location, jd_snippet, source_type)
        if reason:
            matches.append((sheet_row_1based, row, reason))

    print(f"\nstatus=new rows scanned: {new_total}")
    print(f"location-filter matches: {len(matches)}")

    # Histogram of reasons
    reason_counts = Counter(_reason_bucket(m[2]) for m in matches)
    print("\nDrop reasons:")
    for r, n in reason_counts.most_common():
        print(f"  {n:>4}  {r}")

    # Histogram by source_type
    src_counts = Counter(_source_type_from_id(m[1].get("id", "")) for m in matches)
    print("\nDrops by source:")
    for s, n in src_counts.most_common():
        print(f"  {n:>4}  {s}")

    print("\nSample matched rows:")
    for sheet_row, row, reason in matches[:20]:
        company = (row.get("company") or "?")[:25]
        role = (row.get("role") or "?")[:45]
        loc = (row.get("location") or "?")[:30]
        print(f"  row={sheet_row:>5}  {company:<25}  {role:<45}  loc={loc:<30}  {reason}")

    if not matches:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print(f"\n[DRY RUN] Would trim {len(matches)} rows. Pass --apply to write.")
        return 0

    print(f"\nApplying — updating {len(matches)} rows...")
    note_marker = f"{_TRIM_NOTE_PREFIX}{date.today().isoformat()}"
    status_col = schema.col_letter("status")
    notes_col = schema.col_letter("notes")

    body_updates: list[dict[str, Any]] = []
    for sheet_row, _row, _reason in matches:
        body_updates.append({
            "range": f"{status_col}{sheet_row}",
            "values": [[schema.STATUS_SKIP]],
        })
        body_updates.append({
            "range": f"{notes_col}{sheet_row}",
            "values": [[note_marker]],
        })

    CHUNK = 200
    written = 0
    for start in range(0, len(body_updates), CHUNK):
        chunk = body_updates[start : start + CHUNK]
        ws.batch_update(chunk, value_input_option="USER_ENTERED")
        written += len(chunk) // 2
        print(f"  ... {written}/{len(matches)} rows updated")

    print(f"\nDone. Trimmed {len(matches)} rows. Marker: {note_marker!r}")
    return 0


def _reason_bucket(reason: str) -> str:
    """Strip the trailing location-snippet for cleaner histograms."""
    return reason.split(":", 1)[0]


if __name__ == "__main__":
    raise SystemExit(main())
