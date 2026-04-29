"""Discover the Workday `tenant:sub:site` triple for a company's careers URL.

Background (Amelia, party-mode review 2026-04-29): adding new Workday tenants
to `boards.yaml` is a 404 lottery without a discovery probe. This script
takes one or more careers URLs, follows redirects to the Workday host,
extracts the triple, and verifies it by hitting `/wday/cxs/.../jobs?limit=1`.

    .venv/bin/python scripts/discover_workday.py https://www.salesforce.com/company/careers/
    .venv/bin/python scripts/discover_workday.py --from-file targets.txt
    .venv/bin/python scripts/discover_workday.py --json https://...

Output (human form):
    salesforce         OK   tenant=salesforce sub=wd1 site=External_Career_Site (3 postings)
    microsoft          NOT-WORKDAY  https://careers.microsoft.com/...  (custom careers page)

Pre-canned targets for the 30-tenant ambition (uncomment in `__main__` to use):
the source-roadmap §4 list. Many of these are NOT Workday — that's the point
of this script.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.posting import SourceError  # noqa: E402
from scout.sources.workday import fetch as workday_fetch  # noqa: E402

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_WD_HOST_RE = re.compile(
    r"https?://([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com"
    r"(?:/[a-z]{2}-[A-Z]{2})?"
    r"/([a-zA-Z0-9_-]+)"
)


def _follow(url: str) -> str | None:
    """Follow redirects, return final URL string or None on error."""
    try:
        r = httpx.get(
            url,
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": _BROWSER_UA},
        )
    except httpx.HTTPError as e:
        return None
    return str(r.url)


def _extract_triple(final_url: str) -> tuple[str, str, str] | None:
    m = _WD_HOST_RE.search(final_url)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def discover(url: str) -> dict[str, Any]:
    final = _follow(url)
    if final is None:
        return {"input": url, "status": "FETCH-FAIL"}
    triple = _extract_triple(final)
    if triple is None:
        return {
            "input": url,
            "final_url": final,
            "status": "NOT-WORKDAY",
            "note": "no Workday host in redirect chain",
        }
    tenant, sub, site = triple
    board_id = f"{tenant}:{sub}:{site}"
    try:
        postings = workday_fetch(board_id)
        return {
            "input": url,
            "final_url": final,
            "status": "OK",
            "board_id": board_id,
            "tenant": tenant,
            "sub": sub,
            "site": site,
            "count": len(postings),
        }
    except SourceError as e:
        return {
            "input": url,
            "final_url": final,
            "status": "PROBE-FAIL",
            "board_id": board_id,
            "note": e.last_error[:120],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("urls", nargs="*", help="careers URL(s) to probe")
    parser.add_argument("--from-file", help="newline-separated URLs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    urls: list[str] = list(args.urls)
    if args.from_file:
        urls.extend(Path(args.from_file).read_text().strip().splitlines())
    urls = [u.strip() for u in urls if u.strip() and not u.startswith("#")]
    if not urls:
        parser.error("no URLs provided")

    results = [discover(u) for u in urls]
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    for r in results:
        status = r.get("status", "?")
        if status == "OK":
            tenant = r["tenant"]
            sub = r["sub"]
            site = r["site"]
            count = r["count"]
            print(
                f"OK            {tenant:<24} {sub:<5} site={site} ({count} postings)"
                f"   ←  {r['input']}"
            )
            print(
                f'              YAML: - {{ slug: {tenant}, source_type: workday, '
                f'board_id: "{tenant}:{sub}:{site}", enabled: true }}'
            )
        elif status == "NOT-WORKDAY":
            final = r.get("final_url", "?")
            print(f"NOT-WORKDAY                  {r['input']}  →  {final}")
        elif status == "PROBE-FAIL":
            bid = r.get("board_id", "?")
            note = r.get("note", "?")
            print(f"PROBE-FAIL    {bid}  ({note})")
        else:
            print(f"FETCH-FAIL                   {r['input']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
