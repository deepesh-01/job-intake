"""Canonical Sheet schema. SCHEMA_VERSION must be bumped when columns change."""
from __future__ import annotations

SCHEMA_VERSION = 2

JOBS_TAB = "Jobs"
BOARDS_TAB = "Boards"
LOG_TAB = "Log"

# Column order from design doc §13.1. Index = column index (0-based).
JOBS_COLUMNS = [
    "id",
    "discovered_at",
    "posted_at",
    "company",
    "role",
    "location",
    "comp_string",
    "comp_currency",
    "comp_low",
    "comp_high",
    "comp_low_usd",
    "comp_high_usd",
    "link",
    "jd_snippet",
    "jd_full_path",
    "tags",
    "tag_reasons",
    "status",
    "resume_path",
    "last_change",
    "tailored_at",
    "applied_at",
    "followup_due_at",
    "response_at",
    "notes",
    # v2 — resume↔JD deterministic skill-overlap score, 0.0-1.0.
    "resume_match",
]

BOARDS_COLUMNS = [
    "slug",
    "source_type",
    "board_id",
    "enabled",
    "last_scrape_at",
    "last_scrape_status",
    "last_error",
    "rows_added_today",
]

LOG_COLUMNS = ["ts", "module", "event", "detail"]

# Status enum values per §5.1
STATUS_NEW = "new"
STATUS_TAILOR = "tailor"
STATUS_READY = "ready"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
STATUS_SKIP = "skip"
STATUS_ERROR = "error"  # used by processor on bridge failure (§10.7)

VALID_STATUSES = {
    STATUS_NEW,
    STATUS_TAILOR,
    STATUS_READY,
    STATUS_APPLIED,
    STATUS_REJECTED,
    STATUS_SKIP,
    STATUS_ERROR,
}


def col_idx(name: str) -> int:
    """0-based column index for a Jobs column."""
    return JOBS_COLUMNS.index(name)


def col_letter(name: str) -> str:
    """A1-style column letter for a Jobs column."""
    idx = col_idx(name)
    # Sheets uses 1-based letters
    n = idx + 1
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out
