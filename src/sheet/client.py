from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from sheet import schema

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    # drive.file lets the same SA upload PDFs into a folder the user
    # shared with it. Per-file access — least privilege.
    "https://www.googleapis.com/auth/drive.file",
]


class SheetClient:
    def __init__(self, creds_path: Path, sheet_id: str):
        creds = Credentials.from_service_account_file(str(creds_path), scopes=_SCOPES)
        self._gc = gspread.authorize(creds)
        self._ss = self._gc.open_by_key(sheet_id)

    # ── schema version (cell A1 of Jobs holds "schema_version: N") ──

    def read_schema_version(self) -> int:
        ws = self._ws(schema.JOBS_TAB)
        cell = ws.acell("A1").value or ""
        if not cell.startswith("schema_version:"):
            raise RuntimeError(
                f"Jobs!A1 does not look like a schema marker: {cell!r}. "
                "Run scripts/bootstrap_sheet.py first."
            )
        return int(cell.split(":", 1)[1].strip())

    def write_schema_version(self, n: int) -> None:
        ws = self._ws(schema.JOBS_TAB)
        ws.update("A1", [[f"schema_version: {n}"]])

    def assert_schema_compatible(self) -> None:
        on_sheet = self.read_schema_version()
        if on_sheet > schema.SCHEMA_VERSION:
            raise RuntimeError(
                f"Sheet schema_version={on_sheet} is newer than code "
                f"({schema.SCHEMA_VERSION}). Upgrade code or run migrate.py."
            )
        if on_sheet < schema.SCHEMA_VERSION:
            raise RuntimeError(
                f"Sheet schema_version={on_sheet} is older than code "
                f"({schema.SCHEMA_VERSION}). Run migrate.py."
            )

    # ── reads ──

    def read_seen_ids(self) -> set[str]:
        ws = self._ws(schema.JOBS_TAB)
        col = schema.col_idx("id") + 1
        # Skip header row 2 (row 1 is the schema marker, row 2 is headers)
        values = ws.col_values(col)[2:]
        return {v for v in values if v}

    def read_jobs_for_dedup(self, days: int = 30) -> list[dict[str, Any]]:
        """Return recent rows with id/company/role/discovered_at for fuzzy dedup."""
        ws = self._ws(schema.JOBS_TAB)
        rows = ws.get_all_values()
        if len(rows) < 3:
            return []
        header = rows[1]
        cutoff = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
        out: list[dict[str, Any]] = []
        for r in rows[2:]:
            row = dict(zip(header, r))
            if row.get("discovered_at", "") < cutoff:
                continue
            out.append(row)
        return out

    def read_followup_candidates(self) -> list[tuple[int, str, str, str]]:
        """Return (sheet_row_index, id, applied_at, response_at) for rows
        where status=applied. sheet_row_index is 1-based for batch_update."""
        ws = self._ws(schema.JOBS_TAB)
        rows = ws.get_all_values()
        out = []
        for i, r in enumerate(rows[2:], start=3):
            row = dict(zip(rows[1], r))
            if row.get("status") == schema.STATUS_APPLIED:
                out.append(
                    (
                        i,
                        row.get("id", ""),
                        row.get("applied_at", ""),
                        row.get("response_at", ""),
                    )
                )
        return out

    def read_tailor_queue(self) -> list[tuple[int, dict[str, str]]]:
        """Rows where status=tailor and resume_path empty. Returns (1-based row, row dict)."""
        ws = self._ws(schema.JOBS_TAB)
        rows = ws.get_all_values()
        out = []
        for i, r in enumerate(rows[2:], start=3):
            row = dict(zip(rows[1], r))
            if row.get("status") == schema.STATUS_TAILOR and not row.get("resume_path"):
                out.append((i, row))
        return out

    # ── writes (batched) ──

    def append_jobs(self, rows: list[list[Any]]) -> int:
        if not rows:
            return 0
        ws = self._ws(schema.JOBS_TAB)
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)

    def update_followup_due(self, updates: list[tuple[int, str]]) -> int:
        """updates: list of (row_index, iso_date)."""
        if not updates:
            return 0
        ws = self._ws(schema.JOBS_TAB)
        col = schema.col_letter("followup_due_at")
        body = [{"range": f"{col}{r}", "values": [[v]]} for r, v in updates]
        ws.batch_update(body, value_input_option="USER_ENTERED")
        return len(updates)

    def update_row_after_tailor(
        self,
        row_index: int,
        resume_path: str,
        last_change: str,
        tailored_at: str,
        new_status: str,
    ) -> None:
        ws = self._ws(schema.JOBS_TAB)
        body = [
            {"range": f"{schema.col_letter('resume_path')}{row_index}", "values": [[resume_path]]},
            {"range": f"{schema.col_letter('last_change')}{row_index}", "values": [[last_change]]},
            {"range": f"{schema.col_letter('tailored_at')}{row_index}", "values": [[tailored_at]]},
            {"range": f"{schema.col_letter('status')}{row_index}", "values": [[new_status]]},
            {"range": f"{schema.col_letter('filter_updated_at')}{row_index}", "values": [[tailored_at]]},
        ]
        ws.batch_update(body, value_input_option="USER_ENTERED")

    def replace_boards_tab(self, rows: list[list[Any]]) -> None:
        ws = self._ws(schema.BOARDS_TAB)
        ws.clear()
        ws.update("A1", [schema.BOARDS_COLUMNS] + rows, value_input_option="USER_ENTERED")
        ws.freeze(rows=1)

    def append_log(self, ts: str, module: str, event: str, detail: str) -> None:
        ws = self._ws(schema.LOG_TAB)
        ws.append_row([ts, module, event, detail], value_input_option="USER_ENTERED")

    def truncate_log(self, keep_days: int = 30) -> int:
        """Drop log rows older than keep_days. Returns count removed."""
        ws = self._ws(schema.LOG_TAB)
        rows = ws.get_all_values()
        if len(rows) < 2:
            return 0
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat()
        kept_header = rows[0]
        kept_body = [r for r in rows[1:] if (r[0] if r else "") >= cutoff]
        removed = len(rows) - 1 - len(kept_body)
        if removed <= 0:
            return 0
        ws.clear()
        ws.update("A1", [kept_header] + kept_body, value_input_option="USER_ENTERED")
        ws.freeze(rows=1)
        return removed

    # ── internals ──

    def _ws(self, name: str) -> gspread.Worksheet:
        return self._ss.worksheet(name)

    def ensure_tabs(self) -> None:
        """Create any missing tabs with header rows. Idempotent."""
        existing = {ws.title for ws in self._ss.worksheets()}
        if schema.JOBS_TAB not in existing:
            ws = self._ss.add_worksheet(schema.JOBS_TAB, rows=1000, cols=len(schema.JOBS_COLUMNS))
            ws.update("A1", [[f"schema_version: {schema.SCHEMA_VERSION}"]])
            ws.update("A2", [schema.JOBS_COLUMNS], value_input_option="USER_ENTERED")
            ws.freeze(rows=2)
        if schema.BOARDS_TAB not in existing:
            ws = self._ss.add_worksheet(schema.BOARDS_TAB, rows=200, cols=len(schema.BOARDS_COLUMNS))
            ws.update("A1", [schema.BOARDS_COLUMNS], value_input_option="USER_ENTERED")
            ws.freeze(rows=1)
        if schema.LOG_TAB not in existing:
            ws = self._ss.add_worksheet(schema.LOG_TAB, rows=2000, cols=len(schema.LOG_COLUMNS))
            ws.update("A1", [schema.LOG_COLUMNS], value_input_option="USER_ENTERED")
            ws.freeze(rows=1)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_iso() -> str:
    return date.today().isoformat()
