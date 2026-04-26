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


MIGRATIONS: dict[int, callable] = {
    1: _v1_to_v2,
}
