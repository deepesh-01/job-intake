"""Archive `status=skip` rows from the Jobs tab into dated archive tabs.

Used by both the daily scout (auto-archive at end of run) and the
`scripts/sheet_archive_skip.py` CLI wrapper. See that script's docstring
for design rationale.

Key invariants:
- Idempotent: calling with no skip rows is a fast no-op (returns 0).
- Failure-safe: archive tabs are written FIRST. Only after every chunk
  lands does the Jobs tab get rewritten. A mid-operation failure leaves
  data duplicated (in both Jobs and archive), never lost.
- Smart sizing: pre-calculates chunk plan; if a single bulk would exceed
  `cap` rows in one tab, splits across `Skip_<date>`, `Skip_<date>_part2`,
  etc. up front rather than mid-write.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sheet import schema
from sheet.client import SheetClient

ARCHIVE_TAB_PREFIX = "Skip_"
DEFAULT_CAP = 5000


def _archive_tab_name(today: str, part_n: int) -> str:
    if part_n == 1:
        return f"{ARCHIVE_TAB_PREFIX}{today}"
    return f"{ARCHIVE_TAB_PREFIX}{today}_part{part_n}"


def plan_chunks(
    sheet: SheetClient, skip_rows: list[list[str]], cap: int, today: str
) -> list[dict[str, Any]]:
    """Return list of {name, rows, existing, is_new, part_n}."""
    existing_titles = {ws.title for ws in sheet._ss.worksheets()}

    part_n = 1
    active_existing = 0
    while _archive_tab_name(today, part_n) in existing_titles:
        ws = sheet._ws(_archive_tab_name(today, part_n))
        existing = max(0, len(ws.get_all_values()) - 1)
        if existing < cap:
            active_existing = existing
            break
        part_n += 1
        active_existing = 0

    plans: list[dict[str, Any]] = []
    remaining = list(skip_rows)
    while remaining:
        name = _archive_tab_name(today, part_n)
        is_new = name not in existing_titles
        capacity = cap - active_existing
        if capacity <= 0:
            part_n += 1
            active_existing = 0
            continue
        chunk = remaining[:capacity]
        plans.append({
            "name": name,
            "rows": chunk,
            "existing": active_existing,
            "is_new": is_new,
            "part_n": part_n,
        })
        remaining = remaining[capacity:]
        if remaining:
            part_n += 1
            active_existing = 0
    return plans


def archive_skip_rows(
    sheet: SheetClient,
    *,
    cap: int = DEFAULT_CAP,
    log: Any = None,
    on_progress: Any = None,
) -> int:
    """Move all `status=skip` rows from Jobs to dated archive tab(s).

    Returns the number of rows archived (0 if no-op). Safe to call when
    nothing needs archiving — short-circuits with no API writes.

    `log` is an optional structlog-style logger; `on_progress(msg)` is an
    optional callback for CLI progress lines.
    """
    ws_jobs = sheet._ws(schema.JOBS_TAB)
    rows = ws_jobs.get_all_values()
    if len(rows) < 3:
        return 0

    schema_marker = rows[0]
    header = rows[1]
    body = rows[2:]

    skip_rows: list[list[str]] = []
    keep_rows: list[list[str]] = []
    status_col_idx = schema.col_idx("status")
    for r in body:
        status = (r[status_col_idx] if status_col_idx < len(r) else "").strip()
        if status == schema.STATUS_SKIP:
            skip_rows.append(r)
        else:
            keep_rows.append(r)

    if not skip_rows:
        return 0

    today = date.today().isoformat()
    plans = plan_chunks(sheet, skip_rows, cap, today)

    # ── Phase 1: write archive tabs (durable copy first) ──
    for p in plans:
        if p["is_new"]:
            ws = sheet._ss.add_worksheet(
                p["name"], rows=cap + 100, cols=len(header)
            )
            ws.update(values=[header], range_name="A1", value_input_option="USER_ENTERED")
            ws.freeze(rows=1)
            if on_progress:
                on_progress(f"created {p['name']}")
        else:
            ws = sheet._ws(p["name"])
        ws.append_rows(p["rows"], value_input_option="USER_ENTERED")
        if on_progress:
            on_progress(f"appended {len(p['rows'])} rows → {p['name']}")

    # ── Phase 2: rewrite Jobs without skip rows ──
    ws_jobs.clear()
    payload = [schema_marker, header] + keep_rows
    ws_jobs.update(values=payload, range_name="A1", value_input_option="USER_ENTERED")
    ws_jobs.freeze(rows=2)

    if log is not None:
        log.info(
            "archived_skip_rows",
            count=len(skip_rows),
            tabs=len(plans),
            jobs_remaining=len(keep_rows),
        )
    if on_progress:
        on_progress(f"Jobs tab: 2 header rows + {len(keep_rows)} job rows")

    return len(skip_rows)
