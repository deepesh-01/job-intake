"""Dedup per §13.5: SQLite mirror of seen IDs + fuzzy company/role match.

The SQLite cache is a local mirror; the Sheet's id column is authoritative.
On startup the runner fetches the Sheet ids and unions them into sqlite.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from rapidfuzz.distance import Levenshtein

from scout.extract import normalize_company, normalize_role


_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ids (
    id TEXT PRIMARY KEY,
    discovered_at TEXT NOT NULL,
    company_norm TEXT NOT NULL,
    role_norm TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_seen_company_role ON seen_ids(company_norm, role_norm);
"""


@dataclass
class DedupCache:
    """Wraps the local sqlite seen-ids store."""

    conn: sqlite3.Connection

    @classmethod
    def open(cls, path: Path) -> "DedupCache":
        conn = sqlite3.connect(str(path))
        conn.executescript(_SCHEMA)
        return cls(conn)

    def has_id(self, id_: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM seen_ids WHERE id = ?", (id_,))
        return cur.fetchone() is not None

    def add(self, id_: str, *, company: str, role: str, discovered_at: date) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_ids(id, discovered_at, company_norm, role_norm) "
            "VALUES (?, ?, ?, ?)",
            (id_, discovered_at.isoformat(), normalize_company(company), normalize_role(role)),
        )

    def add_many_ids(self, ids: Iterable[str]) -> None:
        """Cheap bulk-insert when only ids are known (e.g. union with Sheet)."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen_ids(id, discovered_at, company_norm, role_norm) "
            "VALUES (?, '', '', '')",
            ((i,) for i in ids),
        )

    def commit(self) -> None:
        self.conn.commit()

    def fuzzy_collision(self, company: str, role: str, *, max_distance: int = 3) -> str | None:
        """Look for a (company, role) within edit distance max_distance.
        Returns the matching id if found; None otherwise."""
        cn = normalize_company(company)
        rn = normalize_role(role)
        if not cn or not rn:
            return None
        cur = self.conn.execute(
            "SELECT id, company_norm, role_norm FROM seen_ids WHERE company_norm = ?",
            (cn,),
        )
        for row in cur:
            if Levenshtein.distance(rn, row[2], score_cutoff=max_distance) <= max_distance:
                return row[0]
        return None

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
