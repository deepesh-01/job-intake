"""Force-rewrite the Boards tab from boards.yaml without running a scout sweep."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env, load_yaml  # noqa: E402
from lib.logging import configure  # noqa: E402
from sheet.client import SheetClient  # noqa: E402


def main() -> int:
    env = load_env()
    log = configure(env.log_level)
    boards = load_yaml(env.boards_path).get("boards") or []
    sheet = SheetClient(env.creds_path, env.sheet_id)
    rows = [
        [
            b.get("slug", ""),
            b.get("source_type", ""),
            str(b.get("board_id", "")),
            bool(b.get("enabled", False)),
            "",  # last_scrape_at — set by scout
            "",  # last_scrape_status
            "",  # last_error
            0,   # rows_added_today
        ]
        for b in boards
    ]
    sheet.replace_boards_tab(rows)
    log.info("boards_reloaded", count=len(rows))
    print(f"OK — {len(rows)} board(s) written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
