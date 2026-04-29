"""Ping each enabled board and report which respond / 404.

Mentioned in boards.yaml comment — run after editing the config.

    .venv/bin/python scripts/verify_boards.py
    .venv/bin/python scripts/verify_boards.py --skip linkedin   # cron-safe
    .venv/bin/python scripts/verify_boards.py --json > out/verify.json

NOTE — auto-disable consumers (e.g. weekly cron):
LinkedIn boards are flaky against this verifier (each fetch burns ~26 reqs
of the ~50/run LinkedIn rate-limit budget — see party-mode review 2026-04-29).
Cron runs SHOULD pass `--skip linkedin`.

A future auto-disable cron MUST also implement N≥3 consecutive-failure
quorum across runs before flipping `enabled: false` in YAML; a single
transient 5xx will otherwise silently kill working boards. That state lives
outside this script (e.g. `data/verify_history.json`).
"""
from __future__ import annotations

import argparse
import importlib
import json
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
    "linkedin": "scout.sources.linkedin",
    "linkedin_auth": "scout.sources.linkedin_auth",
    "naukri": "scout.sources.naukri",
    "instahyre": "scout.sources.instahyre",
    "hirist": "scout.sources.hirist",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--skip",
        default="",
        help="comma-separated source types to skip (e.g. linkedin)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    skip_types = {s.strip() for s in args.skip.split(",") if s.strip()}

    env = load_env()
    boards = load_yaml(env.boards_path).get("boards") or []
    results: list[dict] = []
    failures = 0

    for b in boards:
        if not b.get("enabled"):
            continue
        slug = b["slug"]
        st = b["source_type"]
        bid = str(b.get("board_id", ""))

        if st in skip_types:
            results.append({"slug": slug, "source_type": st, "status": "SKIP-CONFIG"})
            if not args.json:
                print(f"SKIP {slug:>20} ({st:>10}) -> skipped via --skip")
            continue

        mod_name = _SOURCE_MODULES.get(st)
        if not mod_name:
            results.append({"slug": slug, "source_type": st, "status": "UNKNOWN-TYPE"})
            failures += 1
            if not args.json:
                print(f"SKIP {slug:>20} unknown source_type {st}")
            continue
        mod = importlib.import_module(mod_name)
        try:
            postings = mod.fetch(bid)
            results.append({
                "slug": slug,
                "source_type": st,
                "status": "OK",
                "count": len(postings),
            })
            if not args.json:
                print(f"OK   {slug:>20} ({st:>10}) -> {len(postings)} postings")
        except SourceError as e:
            results.append({
                "slug": slug,
                "source_type": st,
                "status": "FAIL",
                "error": e.last_error[:200],
            })
            failures += 1
            if not args.json:
                print(f"FAIL {slug:>20} ({st:>10}) -> {e.last_error[:120]}")
        except Exception as e:
            results.append({
                "slug": slug,
                "source_type": st,
                "status": "ERR",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            })
            failures += 1
            if not args.json:
                print(f"ERR  {slug:>20} ({st:>10}) -> {type(e).__name__}: {e}")

    if args.json:
        json.dump({"failures": failures, "results": results}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"\n{failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
