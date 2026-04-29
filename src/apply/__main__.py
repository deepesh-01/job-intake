"""CLI entry: `python -m apply linkedin <job_url> [--role X] [--company Y]`."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from apply.launcher import run_linkedin_easy_apply


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="apply")
    sub = parser.add_subparsers(dest="cmd", required=True)
    li = sub.add_parser("linkedin")
    li.add_argument("url", help="LinkedIn job URL")
    li.add_argument("--role", default="", help="role title (for cover-note)")
    li.add_argument("--company", default="", help="company name (for cover-note)")
    args = parser.parse_args()

    if args.cmd == "linkedin":
        result = run_linkedin_easy_apply(args.url, args.role, args.company)
        print(json.dumps(result, indent=2))
        return 0 if result.get("submitted") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
