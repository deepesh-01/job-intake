"""Schema migrations. Each entry maps from_version -> migration callable.

When SCHEMA_VERSION bumps, add a function here and an entry to MIGRATIONS.
"""
from __future__ import annotations

from sheet import schema
from sheet.client import SheetClient


def migrate(client: SheetClient) -> None:
    on_sheet = client.read_schema_version()
    target = schema.SCHEMA_VERSION
    if on_sheet == target:
        return
    if on_sheet > target:
        raise RuntimeError(
            f"Sheet schema_version={on_sheet} is newer than code ({target}); "
            "upgrade the package."
        )
    while on_sheet < target:
        if on_sheet not in MIGRATIONS:
            raise RuntimeError(f"no migration registered from version {on_sheet}")
        MIGRATIONS[on_sheet](client)
        on_sheet += 1
        client.write_schema_version(on_sheet)


def _v1_to_v2(client: SheetClient) -> None:
    """Append `resume_match` column header to the Jobs tab. Existing rows
    are left with a blank cell — the next Scout run will overwrite them."""
    ws = client._ws(schema.JOBS_TAB)
    new_col_idx = schema.col_idx("resume_match")  # 0-based
    new_col_letter = schema.col_letter("resume_match")
    needed_cols = new_col_idx + 1
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)
    ws.update(f"{new_col_letter}2", [["resume_match"]])


def _v2_to_v3(client: SheetClient) -> None:
    """Append `filter_updated_at` column header AND backfill all existing
    rows with the best-available existing timestamp:
    `applied_at || tailored_at || discovered_at`. This means the new sort
    works retroactively for rows that landed before v3.
    """
    ws = client._ws(schema.JOBS_TAB)
    new_col_idx = schema.col_idx("filter_updated_at")  # 0-based
    new_col_letter = schema.col_letter("filter_updated_at")
    needed_cols = new_col_idx + 1
    if ws.col_count < needed_cols:
        ws.add_cols(needed_cols - ws.col_count)
    ws.update(values=[["filter_updated_at"]], range_name=f"{new_col_letter}2")

    # Backfill: read existing rows and write a best-guess timestamp per row.
    all_values = ws.get_all_values()
    if len(all_values) < 3:
        return  # only headers, nothing to backfill
    header = all_values[1]
    try:
        idx_applied = header.index("applied_at")
        idx_tailored = header.index("tailored_at")
        idx_discovered = header.index("discovered_at")
    except ValueError:
        # If older sheet missing one of these columns, fall back to discovered_at only.
        idx_applied = -1
        idx_tailored = -1
        idx_discovered = header.index("discovered_at")

    body: list[dict] = []
    for i, row in enumerate(all_values[2:], start=3):  # data starts at sheet row 3
        applied = row[idx_applied] if idx_applied >= 0 and idx_applied < len(row) else ""
        tailored = row[idx_tailored] if idx_tailored >= 0 and idx_tailored < len(row) else ""
        discovered = row[idx_discovered] if idx_discovered < len(row) else ""
        value = applied or tailored or discovered or ""
        body.append({"range": f"{new_col_letter}{i}", "values": [[value]]})

    if not body:
        return
    CHUNK = 200
    for start in range(0, len(body), CHUNK):
        ws.batch_update(body[start:start + CHUNK], value_input_option="USER_ENTERED")


MIGRATIONS: dict[int, callable] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
}
