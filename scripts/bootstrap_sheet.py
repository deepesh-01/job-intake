"""Create the three tabs (Jobs/Boards/Log) on a fresh Sheet.

Idempotent — safe to re-run. Prints a one-line summary.

Usage:
    uv run python scripts/bootstrap_sheet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `src` importable when running as a script.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from lib.logging import configure  # noqa: E402
from sheet.client import SheetClient  # noqa: E402


def main() -> int:
    env = load_env()
    log = configure(env.log_level)
    client = SheetClient(env.creds_path, env.sheet_id)
    client.ensure_tabs()
    version = client.read_schema_version()
    log.info("bootstrap_finished", schema_version=version, sheet_id=env.sheet_id)
    print(f"OK — schema_version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
