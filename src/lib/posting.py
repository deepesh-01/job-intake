from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Posting:
    """Source-client output. id is `<source_type>:<board_id>:<posting_id>`."""

    id: str
    company: str
    role: str
    location: str | None
    comp_string: str | None
    posted_at: date | None
    link: str
    jd_html: str
    source_type: str = ""
    board_id: str = ""


@dataclass
class EnrichedRow:
    """Row ready to write to Sheet — Posting plus extracted/tagged fields."""

    posting: Posting
    discovered_at: date
    jd_text: str
    jd_full_path: str
    comp_currency: str | None
    comp_low: int | None
    comp_high: int | None
    comp_low_usd: int | None
    comp_high_usd: int | None
    tags: list[str] = field(default_factory=list)
    tag_reasons: list[str] = field(default_factory=list)
    resume_match: float = 0.0  # 0.0–1.0 deterministic skill-overlap score


class SourceError(RuntimeError):
    """Unrecoverable failure inside a source client. Carries last_error string."""

    def __init__(self, source: str, board_id: str, message: str):
        self.source = source
        self.board_id = board_id
        self.last_error = message
        super().__init__(f"{source}:{board_id} — {message}")
