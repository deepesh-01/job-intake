"""Create / refresh saved Filter Views on the Jobs tab.

Idempotent — re-running deletes the previously-added views (matched by
title) and recreates them. Each filter view is sorted by resume_match desc
(except view 8 which sorts by discovered_at for daily triage).

Design philosophy: every view chains at most 3 AND conditions. We avoid
over-narrowing because (a) ~73% of JDs don't post comp publicly, so
`comp_ok` is coverage-bound not quality-bound, and (b) location strings
are inconsistent — a "Dublin" job may still be remote-friendly to India.
We use `comp_ok`/`enjoy_eligible` as bonus signals surfaced via the
resume_match sort, not as required gates.

Usage:
    uv run python scripts/add_views.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from lib.config import load_env  # noqa: E402
from lib.logging import configure  # noqa: E402
from sheet import schema  # noqa: E402
from sheet.client import SheetClient  # noqa: E402


# Each view: title, all_of (must contain), none_of (must not contain),
# any_of (at least one must be present), and sort key.
_VIEWS = [
    {
        "title": "1 — Browse senior IC (sort by match)",
        "all_of": ["application_eng", "seniority_match"],
        "none_of": [],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "2 — Top picks (resume-strong senior IC)",
        "all_of": ["resume_strong", "application_eng", "seniority_match"],
        "none_of": [],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "3 — Remote-friendly senior IC",
        "all_of": ["application_eng", "seniority_match", "remote_ok"],
        "none_of": ["non_us_only"],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "4 — Target city (Bangalore / Hyderabad / India)",
        "all_of": ["application_eng", "seniority_match", "target_city"],
        "none_of": [],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "5 — India-relevant (remote OR target city)",
        "all_of": ["application_eng", "seniority_match"],
        "none_of": ["non_us_only"],
        "any_of": ["remote_ok", "target_city"],
        "sort": "resume_match",
    },
    {
        "title": "6 — Comp-disclosed senior IC",
        "all_of": ["application_eng", "seniority_match", "comp_ok"],
        "none_of": [],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "7 — AI-native or chill (enjoy_eligible senior IC)",
        "all_of": ["application_eng", "seniority_match", "enjoy_eligible"],
        "none_of": [],
        "any_of": [],
        "sort": "resume_match",
    },
    {
        "title": "8 — New rows (status=new, daily triage)",
        "all_of": [],
        "none_of": [],
        "any_of": [],
        "sort": "discovered_at",
        "_status_new_only": True,
    },
]


def _has(kw: str) -> str:
    return f'ISNUMBER(SEARCH("{kw}", $P3))'


def _not_has(kw: str) -> str:
    return f'NOT(ISNUMBER(SEARCH("{kw}", $P3)))'


def _formula(all_of: list[str], none_of: list[str], any_of: list[str]) -> str | None:
    parts: list[str] = [_has(k) for k in all_of] + [_not_has(k) for k in none_of]
    if any_of:
        or_group = "OR(" + ", ".join(_has(k) for k in any_of) + ")"
        parts.append(or_group)
    if not parts:
        return None
    if len(parts) == 1:
        return f"={parts[0]}"
    return "=AND(" + ", ".join(parts) + ")"


def main() -> int:
    env = load_env()
    log = configure(env.log_level)
    client = SheetClient(env.creds_path, env.sheet_id)
    ss = client._ss
    ws = client._ws(schema.JOBS_TAB)
    sheet_id = ws.id

    tags_col = schema.col_idx("tags")
    status_col = schema.col_idx("status")
    end_col = len(schema.JOBS_COLUMNS)
    range_block = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "endRowIndex": 1000,
        "startColumnIndex": 0,
        "endColumnIndex": end_col,
    }

    # Delete existing views with our titles.
    meta = ss.fetch_sheet_metadata()
    target_titles = {v["title"] for v in _VIEWS}
    delete_reqs: list[dict] = []
    for sh in meta.get("sheets", []):
        if sh["properties"]["sheetId"] != sheet_id:
            continue
        for fv in sh.get("filterViews", []) or []:
            if fv.get("title") in target_titles:
                delete_reqs.append({"deleteFilterView": {"filterId": fv["filterViewId"]}})

    add_reqs: list[dict] = []
    for v in _VIEWS:
        body: dict = {
            "title": v["title"],
            "range": range_block,
            "sortSpecs": [
                {
                    "dimensionIndex": schema.col_idx(v["sort"]),
                    "sortOrder": "DESCENDING",
                }
            ],
        }
        if v.get("_status_new_only"):
            body["criteria"] = {
                str(status_col): {
                    "condition": {
                        "type": "TEXT_EQ",
                        "values": [{"userEnteredValue": "new"}],
                    }
                }
            }
        else:
            f = _formula(v["all_of"], v["none_of"], v["any_of"])
            if f is not None:
                body["criteria"] = {
                    str(tags_col): {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{"userEnteredValue": f}],
                        }
                    }
                }
        add_reqs.append({"addFilterView": {"filter": body}})

    requests = delete_reqs + add_reqs
    if not requests:
        print("nothing to do")
        return 0

    ss.batch_update({"requests": requests})
    log.info(
        "filter_views_updated",
        deleted=len(delete_reqs),
        added=len(add_reqs),
    )
    print(f"OK — refreshed {len(add_reqs)} filter view(s) (deleted {len(delete_reqs)} prior)")
    print("Open the Sheet → click the funnel icon at top → 'Filter views' menu → pick one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
