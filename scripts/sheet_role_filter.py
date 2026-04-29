"""One-time bulk trim: apply current `exclude.yaml` rules to existing rows.

Background: `exclude.yaml` rules expand over time as new role/company
leaks are surfaced. Going forward the runner drops these at fetch time;
this script trims the historical rows that landed before each rule
existed.

Originally only handled `role:` matches (hence the filename). Now applies
ALL exclude prefixes (company / company_pattern / role / keyword) so a
single run catches every retroactive miss.

Behavior:
- Reads all `Jobs` rows.
- For each row with `status=new`, evaluates `excluded_reason(company,
  jd_text, rules, role)`. If ANY rule matches, marks `status=skip` with
  marker note `exclude_rules_bulk_trim_<date>`.
- User-actioned rows (applied/tailor/ready/rejected/skip/error) untouched.
- The histogram + sample output groups by reason prefix so you can see
  which new rule did what work.

Defaults to dry-run. Pass --apply to write.

    .venv/bin/python scripts/sheet_role_filter.py
    .venv/bin/python scripts/sheet_role_filter.py --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env, load_yaml  # noqa: E402
from scout import exclude as exclude_mod  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient, now_iso  # noqa: E402

_TRIM_NOTE_PREFIX = "exclude_rules_bulk_trim_"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="actually write")
    parser.add_argument("--limit", type=int, default=0, help="cap rows trimmed (0=no cap, dry-run only)")
    args = parser.parse_args()

    env = load_env()
    rules = exclude_mod.compile_rules(load_yaml(env.exclude_path))
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
        company = row.get("company", "")
        role = row.get("role", "")
        jd_snippet = row.get("jd_snippet", "")
        reason = exclude_mod.excluded_reason(company, jd_snippet, rules, role=role)
        if reason:
            matches.append((sheet_row_1based, row, reason))

    print(f"\nstatus=new rows scanned: {new_total}")
    print(f"matches (any exclude rule): {len(matches)}")

    # Histogram of which patterns hit
    from collections import Counter
    pat_hits = Counter(m[2] for m in matches)
    print("\nTop matched patterns:")
    for pat, n in pat_hits.most_common(15):
        print(f"  {n:>4}  {pat}")

    print("\nSample matched rows:")
    for sheet_row, row, reason in matches[:25]:
        company = (row.get("company") or "?")[:25]
        role = (row.get("role") or "?")[:55]
        print(f"  row={sheet_row:>5}  {company:<25}  {role}")

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
    filter_col = schema.col_letter("filter_updated_at")
    now = now_iso()

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
        body_updates.append({
            "range": f"{filter_col}{sheet_row}",
            "values": [[now]],
        })

    CHUNK = 200
    written = 0
    for start in range(0, len(body_updates), CHUNK):
        chunk = body_updates[start : start + CHUNK]
        ws.batch_update(chunk, value_input_option="USER_ENTERED")
        written += len(chunk) // 2
        print(f"  ... {written}/{len(matches)} rows updated")

    print(f"\nDone. Trimmed {len(matches)} non-engineering rows. Marker: {note_marker!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
