"""Ping each enabled board and report which respond / 404.

Mentioned in boards.yaml comment — run after editing the config.

    uv run python scripts/verify_boards.py
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env, load_yaml  # noqa: E402
from lib.posting import SourceError  # noqa: E402

_SOURCE_MODULES = {
    "greenhouse": "scout.sources.greenhouse",
    "lever": "scout.sources.lever",
    "ashby": "scout.sources.ashby",
    "yc_waas": "scout.sources.yc_waas",
    "hn_hiring": "scout.sources.hn_hiring",
    "remoteok": "scout.sources.remoteok",
    "remotive": "scout.sources.remotive",
    "arbeitnow": "scout.sources.arbeitnow",
    "hasjob": "scout.sources.hasjob",
    "workday": "scout.sources.workday",
}


def main() -> int:
    env = load_env()
    boards = load_yaml(env.boards_path).get("boards") or []
    failures = 0
    for b in boards:
        if not b.get("enabled"):
            continue
        slug = b["slug"]
        st = b["source_type"]
        bid = str(b.get("board_id", ""))
        mod_name = _SOURCE_MODULES.get(st)
        if not mod_name:
            print(f"SKIP {slug:>20} unknown source_type {st}")
            failures += 1
            continue
        mod = importlib.import_module(mod_name)
        try:
            postings = mod.fetch(bid)
            print(f"OK   {slug:>20} ({st:>10}) -> {len(postings)} postings")
        except SourceError as e:
            print(f"FAIL {slug:>20} ({st:>10}) -> {e.last_error[:120]}")
            failures += 1
        except Exception as e:
            print(f"ERR  {slug:>20} ({st:>10}) -> {type(e).__name__}: {e}")
            failures += 1
    print(f"\n{failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
