"""Re-probe disabled boards in `boards.yaml` against alternate ATSes.

Background (Mary, party-mode review 2026-04-29): the 21 disabled India boards
likely moved between ATSes (greenhouse → lever → ashby → custom) rather than
disappeared. Probing the survivor list is higher-leverage than adding 45 new
tenants. This script tries each disabled board's slug against
greenhouse / lever / ashby and reports which respond.

Workday is not probed here — it requires a tenant:sub:site triple, not a
slug. Use `scripts/discover_workday.py` for those.

    .venv/bin/python scripts/reprobe_disabled.py
    .venv/bin/python scripts/reprobe_disabled.py --variants cal=cal-com,huggingface=hugging-face
    .venv/bin/python scripts/reprobe_disabled.py --json > out/reprobe.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env, load_yaml  # noqa: E402
from lib.posting import SourceError  # noqa: E402

_CROSS_ATS_PROBES = ["greenhouse", "lever", "ashby"]


def _parse_variants(s: str) -> dict[str, list[str]]:
    """`cal=cal-com,foo=foo-bar:foo-inc` → {cal:[cal-com], foo:[foo-bar,foo-inc]}."""
    out: dict[str, list[str]] = {}
    if not s:
        return out
    for pair in s.split(","):
        if "=" not in pair:
            continue
        slug, alts = pair.split("=", 1)
        out[slug.strip()] = [a.strip() for a in alts.split(":") if a.strip()]
    return out


def _try_probe(source_type: str, candidate_id: str) -> tuple[bool, int, str]:
    """Returns (ok, count, note)."""
    mod_name = f"scout.sources.{source_type}"
    try:
        mod = importlib.import_module(mod_name)
        postings = mod.fetch(candidate_id)
        return True, len(postings), ""
    except SourceError as e:
        return False, 0, e.last_error[:80]
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {str(e)[:80]}"


def _candidate_slugs(slug: str, variants: dict[str, list[str]]) -> list[str]:
    candidates = [slug]
    candidates.extend(variants.get(slug, []))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--variants",
        default="",
        help="cal=cal-com,huggingface=hugging-face",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    env = load_env()
    boards = load_yaml(env.boards_path).get("boards") or []
    variants = _parse_variants(args.variants)

    findings: list[dict[str, Any]] = []
    for b in boards:
        if b.get("enabled"):
            continue
        slug = b["slug"]
        original_st = b["source_type"]
        original_bid = str(b.get("board_id", ""))

        # Don't reprobe Workday/LinkedIn/aggregators — different mechanisms.
        if original_st not in _CROSS_ATS_PROBES:
            findings.append({
                "slug": slug,
                "skip": f"non-slug source_type={original_st}",
            })
            if not args.json:
                print(f"SKIP   {slug:>16}  non-slug source_type={original_st}", flush=True)
            continue

        result: dict[str, Any] = {
            "slug": slug,
            "originally": f"{original_st}:{original_bid}",
            "probes": {},
        }
        candidate_ids = _candidate_slugs(slug, variants)
        # Also try the original board_id if it differs (e.g. apnatime vs apna)
        if original_bid and original_bid not in candidate_ids:
            candidate_ids.append(original_bid)

        for ats in _CROSS_ATS_PROBES:
            for cid in candidate_ids:
                ok, count, note = _try_probe(ats, cid)
                key = f"{ats}:{cid}"
                result["probes"][key] = {"ok": ok, "count": count, "note": note}
        result["recommendation"] = _recommend(result, original_st, original_bid)
        findings.append(result)
        if not args.json:
            was = result["originally"]
            rec = result["recommendation"]
            print(f"{slug:>16}  was={was:<32}  -> {rec}", flush=True)
            if "RE-ENABLE" in rec or "WORKS" in rec:
                for key, probe in result["probes"].items():
                    if probe["ok"] and probe["count"] > 0:
                        print(f"                 ✓ {key} ({probe['count']} postings)", flush=True)

    if args.json:
        json.dump(findings, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    return 0


def _recommend(result: dict[str, Any], original_st: str, original_bid: str) -> str:
    hits = [
        (key, p["count"]) for key, p in result["probes"].items()
        if p["ok"] and p["count"] > 0
    ]
    if not hits:
        zero_responders = [
            key for key, p in result["probes"].items() if p["ok"] and p["count"] == 0
        ]
        if zero_responders:
            return f"NO-POSTINGS (responds but empty): {zero_responders[0]}"
        return "STILL DEAD on gh/lever/ashby — likely on custom careers, Workday, or shut down"
    # Prefer the highest count
    hits.sort(key=lambda x: -x[1])
    best_key, best_count = hits[0]
    ats, cid = best_key.split(":", 1)
    if ats == original_st and cid == original_bid:
        return f"WAS ENABLED, NOW WORKS — re-enable as-is ({best_count} postings)"
    return f"RE-ENABLE as {ats} board_id={cid} ({best_count} postings)"


def _print_human(findings: list[dict[str, Any]]) -> None:
    for f in findings:
        slug = f["slug"]
        if "skip" in f:
            print(f"SKIP   {slug:>16}  {f['skip']}")
            continue
        rec = f["recommendation"]
        was = f["originally"]
        print(f"{slug:>16}  was={was:<32}  -> {rec}")
        if "RE-ENABLE" in rec or "WORKS" in rec:
            for key, probe in f["probes"].items():
                if probe["ok"] and probe["count"] > 0:
                    print(f"                 ✓ {key} ({probe['count']} postings)")


if __name__ == "__main__":
    raise SystemExit(main())
