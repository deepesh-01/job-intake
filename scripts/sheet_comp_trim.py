"""Trim status=new rows by compensation signal.

User ask: ≥₹40-50L total cash compensation, ESOPs welcome but not required.

Reality check from the audit (2026-04-29): only ~6% of queue has explicit
comp data parsed; 94% are `comp_unknown` (employer didn't disclose, or our
parser couldn't extract it from the JD body). A strict
"comp_high >= 40L OR drop" filter would reduce the queue to a single-digit
count and lose nearly all legit senior roles that simply don't post comp.

Pragmatic approach: 4 escalating filter levels. Pick based on tolerance
for false-negatives (legit senior role with no public comp gets dropped).

  LEVEL 1 (--level 1, default): drop only `comp_below` tagged rows
                                (rows where parsed comp is provably below
                                ₹40L floor). ~5% cut.
  LEVEL 2 (--level 2): also require `seniority_match` AND `application_eng`
                       for `comp_unknown` rows. ~50% cut. Recommended.
  LEVEL 3 (--level 3): also require (resume_strong OR enjoy_eligible) on
                       top of L2. ~75% cut. Aggressive.
  LEVEL 4 (--level 4): keep ONLY explicit-comp rows that meet floor.
                       ~95% cut. Hardcore.

Always defaults to dry-run.

    .venv/bin/python scripts/sheet_comp_trim.py             # dry-run, level 1
    .venv/bin/python scripts/sheet_comp_trim.py --level 2   # dry-run, level 2
    .venv/bin/python scripts/sheet_comp_trim.py --level 2 --apply
    .venv/bin/python scripts/sheet_comp_trim.py --all-levels  # show all dry-runs side-by-side
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402

_TRIM_NOTE_PREFIX = "comp_filter_bulk_trim_L"


def _safe_float(s: str) -> float | None:
    try:
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def _row_tags(row: dict[str, str]) -> set[str]:
    raw = row.get("tags") or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def _decision(row: dict[str, str], level: int) -> tuple[bool, str]:
    """Returns (keep, reason)."""
    tags = _row_tags(row)
    comp_high_usd = _safe_float(row.get("comp_high_usd", ""))
    comp_high_inr = _safe_float(row.get("comp_high", "")) if (row.get("comp_currency", "").upper() == "INR") else None

    has_explicit_pass = (
        (comp_high_usd is not None and comp_high_usd >= 60000)
        or (comp_high_inr is not None and comp_high_inr >= 4000000)
    )
    is_comp_below = "comp_below" in tags
    is_comp_unknown = "comp_unknown" in tags
    has_seniority = "seniority_match" in tags
    is_application_eng = "application_eng" in tags
    is_resume_strong = "resume_strong" in tags
    is_enjoy_eligible = "enjoy_eligible" in tags

    if has_explicit_pass:
        return True, "comp_meets_floor"
    if is_comp_below:
        return False, "comp_below"

    # Beyond here: rows are comp_unknown.

    if level == 1:
        return True, "L1_keep_unknown"

    # Loosened 2026-04-29: L2/L3 no longer require `application_eng` tag.
    # The tag's exclude list (in tag_rules.yaml) drops SRE/DevOps/Platform/
    # ML/Data engineers — but the user has explicitly enabled ML/AI/SRE
    # queries via Naukri & LinkedIn-auth boards, signaling those are wanted.
    # Senior software roles in any discipline now pass L2 as long as
    # seniority_match fires and `wrong_discipline` doesn't.
    is_wrong_discipline = "wrong_discipline" in tags
    is_ai_native = "ai_native" in tags

    if level == 2:
        if has_seniority and not is_wrong_discipline:
            return True, "L2_keep_senior"
        if not has_seniority:
            return False, "L2_drop_not_senior"
        return False, "L2_drop_wrong_discipline"

    if level == 3:
        if (has_seniority and not is_wrong_discipline
                and (is_resume_strong or is_enjoy_eligible or is_ai_native)):
            return True, "L3_keep_premium"
        if not has_seniority:
            return False, "L3_drop_not_senior"
        if is_wrong_discipline:
            return False, "L3_drop_wrong_discipline"
        return False, "L3_drop_no_strong_signal"

    if level == 4:
        return False, "L4_drop_no_explicit_comp"

    raise ValueError(f"unknown level {level}")


def _scan(rows: list[list[str]], header: list[str], level: int) -> tuple[list[tuple[int, dict[str, str], str]], list[tuple[int, dict[str, str], str]]]:
    """Return (drops, keeps) — each as (row_idx, row_dict, reason)."""
    drops: list[tuple[int, dict[str, str], str]] = []
    keeps: list[tuple[int, dict[str, str], str]] = []
    for i, r in enumerate(rows):
        sheet_row_1based = i + 3
        row = dict(zip(header, r))
        if (row.get("status") or "").strip() != schema.STATUS_NEW:
            continue
        keep, reason = _decision(row, level)
        if keep:
            keeps.append((sheet_row_1based, row, reason))
        else:
            drops.append((sheet_row_1based, row, reason))
    return drops, keeps


def _summarize(level: int, drops, keeps) -> None:
    total = len(drops) + len(keeps)
    print(f"\n=== LEVEL {level} ===")
    print(f"  scanned status=new: {total}")
    print(f"  KEEP: {len(keeps)} ({100*len(keeps)/max(1,total):.1f}%)")
    print(f"  DROP: {len(drops)} ({100*len(drops)/max(1,total):.1f}%)")
    rc = Counter(d[2] for d in drops)
    if rc:
        print(f"  drop reasons:")
        for r, n in rc.most_common():
            print(f"    {n:>4}  {r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--level", type=int, default=1, choices=[1, 2, 3, 4])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--all-levels", action="store_true",
                        help="show projection at all 4 levels (dry-run only)")
    parser.add_argument(
        "--blend",
        default=None,
        metavar="A:B",
        help=("blend L2-only and L3 sets by ratio A:B. Keeps all L2-only + "
              "top (B/A * |L2-only|) L3 by resume_match. Example: --blend 70:30"),
    )
    args = parser.parse_args()

    env = load_env()
    sheet = SheetClient(env.creds_path, env.sheet_id)
    ws = sheet._ws(schema.JOBS_TAB)
    rows = ws.get_all_values()
    if len(rows) < 3:
        print("Sheet has fewer than 3 rows.")
        return 0

    header = rows[1]
    body = rows[2:]
    print(f"Sheet: {len(body)} job rows.")

    if args.all_levels:
        for lvl in (1, 2, 3, 4):
            drops, keeps = _scan(body, header, lvl)
            _summarize(lvl, drops, keeps)
        print("\n[ALL-LEVELS dry-run] Pass --level N --apply to execute one.")
        return 0

    if args.blend:
        a_str, b_str = args.blend.split(":", 1)
        ratio_a, ratio_b = float(a_str), float(b_str)
        # L2 = broader pass-set; L3 = premium pass-set (subset of L2)
        _, l2_keeps = _scan(body, header, 2)
        _, l3_keeps = _scan(body, header, 3)
        l3_idx = {idx for idx, _, _ in l3_keeps}
        l2_only = [k for k in l2_keeps if k[0] not in l3_idx]
        # Cap L3 to maintain ratio_a:ratio_b for L2-only:L3
        target_l3 = int(round((ratio_b / ratio_a) * len(l2_only)))
        target_l3 = min(target_l3, len(l3_keeps))
        l3_sorted = sorted(
            l3_keeps,
            key=lambda x: -(float(x[1].get("resume_match", "") or 0.0)),
        )
        l3_capped = l3_sorted[:target_l3]
        keep_indices = {k[0] for k in l2_only} | {k[0] for k in l3_capped}
        # Drops = everything not in the keep set, scanning original status=new
        drops: list[tuple[int, dict[str, str], str]] = []
        keeps: list[tuple[int, dict[str, str], str]] = []
        for i, r in enumerate(body):
            sheet_row_1based = i + 3
            row = dict(zip(header, r))
            if (row.get("status") or "").strip() != schema.STATUS_NEW:
                continue
            if sheet_row_1based in keep_indices:
                tier = "L3_blend_premium" if sheet_row_1based in {k[0] for k in l3_capped} else "L2_only_blend"
                keeps.append((sheet_row_1based, row, tier))
            else:
                # Reuse a simple drop reason — could be "wasn't in L2 set" or
                # "was in L3 but capped out".
                in_l2 = sheet_row_1based in {k[0] for k in l2_keeps}
                in_l3 = sheet_row_1based in l3_idx
                if in_l3:
                    reason = f"blend_L3_capped_below_top{target_l3}"
                elif in_l2:
                    reason = "blend_L2_only_overflow"   # shouldn't fire — l2_only is fully kept
                else:
                    reason = "blend_failed_L2_filter"
                drops.append((sheet_row_1based, row, reason))
        print(f"\n=== BLEND {args.blend} ===")
        print(f"  L2-only available: {len(l2_only)} (all kept)")
        print(f"  L3 keeps available: {len(l3_keeps)}, capped at top {target_l3}")
        print(f"  Final queue: {len(keeps)} rows")
        print(f"  Ratio in result: {len(l2_only)}:{target_l3}")
        rc = Counter(d[2] for d in drops)
        print(f"  Drop reasons:")
        for r, n in rc.most_common():
            print(f"    {n:>4}  {r}")
    else:
        drops, keeps = _scan(body, header, args.level)
        _summarize(args.level, drops, keeps)

    print(f"\nSample {min(20, len(drops))} drops:")
    for idx, row, reason in drops[:20]:
        co = (row.get("company") or "?")[:22]
        role = (row.get("role") or "?")[:55]
        print(f"  row={idx:>5}  {co:<22}  {role:<55}  {reason}")

    print(f"\nSample {min(15, len(keeps))} keeps:")
    for idx, row, reason in keeps[:15]:
        co = (row.get("company") or "?")[:22]
        role = (row.get("role") or "?")[:55]
        print(f"  row={idx:>5}  {co:<22}  {role:<55}  {reason}")

    if not drops:
        print("\nNothing to drop.")
        return 0

    if not args.apply:
        print(f"\n[DRY RUN] Pass --apply to execute level {args.level}.")
        return 0

    label = f"BLEND_{args.blend.replace(':','_')}" if args.blend else f"L{args.level}"
    print(f"\nApplying {label}...")
    note_marker = f"{_TRIM_NOTE_PREFIX}{label}_{date.today().isoformat()}"
    status_col = schema.col_letter("status")
    notes_col = schema.col_letter("notes")
    body_updates: list[dict[str, Any]] = []
    for idx, _row, _reason in drops:
        body_updates.append({
            "range": f"{status_col}{idx}",
            "values": [[schema.STATUS_SKIP]],
        })
        body_updates.append({
            "range": f"{notes_col}{idx}",
            "values": [[note_marker]],
        })
    CHUNK = 200
    written = 0
    for start in range(0, len(body_updates), CHUNK):
        chunk = body_updates[start: start + CHUNK]
        ws.batch_update(chunk, value_input_option="USER_ENTERED")
        written += len(chunk) // 2
        print(f"  ... {written}/{len(drops)} rows")

    print(f"\nDone. Trimmed {len(drops)} rows. Marker: {note_marker!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
