"""Scout entrypoint — orchestrates the daily flow per §8.1.

Steps (matching design doc):
1. Load configs.
2. Open Sheet, verify schema, hydrate dedup cache from Sheet ids.
3. For each enabled board: fetch -> exclude -> extract -> tag -> buffer.
4. Apply fuzzy dedup, batch-append rows, refresh Boards tab.
5. Always run the follow-up sweep (idempotent).
6. Truncate log if needed.
7. Log summary.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from lib.config import load_env, load_yaml
from lib.logging import configure
from lib.posting import EnrichedRow, Posting, SourceError
from scout import dedup, exclude, extract, resume, tag
from scout.extract import Comp
from sheet import schema
from sheet.client import SheetClient, now_iso, today_iso

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
}

# §7.3 — first run hard cap: 7-day cutoff so the Sheet doesn't flood.
_FIRST_RUN_CUTOFF_DAYS = 7

# Aggregator filtering (per §8.1.1): yc_waas needs aggressive pre-tag filter.
_YC_APP_ENG_HINTS = (
    "engineer", "developer", "full stack", "fullstack", "full-stack",
    "backend", "back-end", "frontend", "front-end", "founding",
)


@dataclass
class BoardResult:
    slug: str
    source_type: str
    board_id: str
    fetched: int = 0
    inserted: int = 0
    excluded: int = 0
    deduped: int = 0
    error: str | None = None


def main() -> int:
    env = load_env()
    log = configure(env.log_level)
    log.info("scout_started")

    rules = load_yaml(env.tag_rules_path)
    boards_yaml = load_yaml(env.boards_path)
    exclude_yaml = load_yaml(env.exclude_path)
    exclude_rules = exclude.compile_rules(exclude_yaml)
    fx_rates = (rules.get("comp") or {}).get("fx_rates", {})

    # Phase 1: load the user's base resume once. Skill-overlap is computed
    # per JD downstream. Missing resume is non-fatal — match score stays 0.
    resume_profile: resume.ResumeProfile | None = None
    try:
        resume_profile = resume.load_profile(env.system_a_base_resume)
        log.info(
            "resume_profile_loaded",
            path=str(env.system_a_base_resume),
            skill_count=len(resume_profile.skills),
        )
    except FileNotFoundError as e:
        log.warning("resume_profile_missing", err=str(e))

    sheet = SheetClient(env.creds_path, env.sheet_id)
    sheet.assert_schema_compatible()

    cache_path = env.data_dir / "seen_ids.sqlite"
    cache = dedup.DedupCache.open(cache_path)

    # First-run check: if sqlite is empty AND sheet has no jobs rows, we're new.
    sheet_ids = sheet.read_seen_ids()
    cache.add_many_ids(sheet_ids)
    cache.commit()
    is_first_run = len(sheet_ids) == 0
    log.info("scout_dedup_hydrated", sheet_ids=len(sheet_ids), first_run=is_first_run)

    boards = [b for b in (boards_yaml.get("boards") or []) if b.get("enabled")]
    today = date.today()
    cutoff = today - timedelta(days=_FIRST_RUN_CUTOFF_DAYS) if is_first_run else None

    enriched_rows: list[EnrichedRow] = []
    board_results: list[BoardResult] = []

    for board in boards:
        slug = board["slug"]
        source_type = board["source_type"]
        board_id = str(board.get("board_id", ""))
        result = BoardResult(slug=slug, source_type=source_type, board_id=board_id)
        board_results.append(result)

        mod_name = _SOURCE_MODULES.get(source_type)
        if not mod_name:
            result.error = f"unknown source_type {source_type}"
            log.warning("board_skipped_unknown_source", slug=slug, source_type=source_type)
            continue
        mod = importlib.import_module(mod_name)
        try:
            if source_type == "hn_hiring":
                # Pass thread sentinel state from cache so we don't re-process
                # the same monthly thread.
                postings = mod.fetch(board_id, today=today, seen_thread_ids=set())
            else:
                postings = mod.fetch(board_id)
            result.fetched = len(postings)
            log.info("board_fetched", slug=slug, count=len(postings))
        except SourceError as e:
            result.error = e.last_error[:300]
            log.warning("board_errored", slug=slug, err=str(e))
            continue
        except Exception as e:
            result.error = str(e)[:300]
            log.warning("board_errored", slug=slug, err=str(e))
            continue

        for p in postings:
            enriched = _enrich_one(
                p,
                source_type=source_type,
                exclude_rules=exclude_rules,
                rules=rules,
                fx_rates=fx_rates,
                cache=cache,
                jds_dir=env.data_dir / "jds",
                today=today,
                cutoff=cutoff,
                result=result,
                log=log,
                resume_profile=resume_profile,
            )
            if enriched is not None:
                enriched_rows.append(enriched)

        # Aggregator-specific post-filters (§8.1.1).
        if source_type == "yc_waas":
            before = len(enriched_rows)
            enriched_rows = [
                r for r in enriched_rows
                if r.posting.source_type != "yc_waas"
                or _is_app_eng(r.posting.role)
            ]
            dropped = before - len(enriched_rows)
            if dropped:
                log.info("yc_waas_pre_filter", dropped=dropped)
                result.excluded += dropped

    # ── Final fuzzy dedup across the whole batch (already-deduped against cache) ──
    enriched_rows, intra_batch_dups = _dedup_intra_batch(enriched_rows)
    if intra_batch_dups:
        log.info("intra_batch_dedup", dropped=intra_batch_dups)

    # ── Write to Sheet ──
    rows_for_sheet = [_to_sheet_row(r) for r in enriched_rows]
    inserted = sheet.append_jobs(rows_for_sheet)

    # Update cache with what we just inserted.
    for r in enriched_rows:
        cache.add(
            r.posting.id,
            company=r.posting.company,
            role=r.posting.role,
            discovered_at=r.discovered_at,
        )
    cache.commit()
    cache.close()

    # ── Update Boards tab ──
    board_rows = [_board_row(b) for b in board_results]
    sheet.replace_boards_tab(board_rows)

    # ── Follow-up sweep (always runs, idempotent) — §8.1 step 5 ──
    followups = _run_followup_sweep(sheet, today=today)

    # ── Log summary ──
    sheet.append_log(
        now_iso(),
        "scout",
        "finished",
        f"inserted={inserted} followups={followups} boards={len(boards)} "
        f"errored={sum(1 for b in board_results if b.error)}",
    )
    truncated = sheet.truncate_log(keep_days=30)
    log.info(
        "scout_finished",
        inserted=inserted,
        followups=followups,
        log_truncated=truncated,
    )
    return 0


# ───────────────────────────── helpers ─────────────────────────────


def _enrich_one(
    p: Posting,
    *,
    source_type: str,
    exclude_rules: exclude.ExcludeRules,
    rules: dict,
    fx_rates: dict,
    cache: dedup.DedupCache,
    jds_dir: Path,
    today: date,
    cutoff: date | None,
    result: BoardResult,
    log: Any,
    resume_profile: resume.ResumeProfile | None,
) -> EnrichedRow | None:
    # Empty / too-short JD → skip (§10.7)
    if not p.jd_html and not p.role:
        return None
    jd_text = extract.html_to_text(p.jd_html)
    if len(jd_text) < 200 and source_type not in {"hn_hiring", "workday", "linkedin"}:
        # HN postings are often very short by design.
        # Workday list responses don't include the JD body — only title +
        # bullet taglines, typically ~50 chars total. Body would require
        # an N+1 detail fetch per posting (slow + rate-limit risky).
        # LinkedIn cards may also have empty bodies if detail-fetch was
        # disabled or rate-limited. We accept the lower-fidelity row and
        # let title/location drive tags.
        return None

    # First-run cutoff per §7.3
    if cutoff and p.posted_at and p.posted_at < cutoff:
        return None

    # Dedup vs cache (id-based)
    if cache.has_id(p.id):
        result.deduped += 1
        return None

    # Exclude per §7.4
    reason = exclude.excluded_reason(p.company, jd_text, exclude_rules)
    if reason:
        result.excluded += 1
        log.debug("excluded", id=p.id, reason=reason)
        return None

    # Fuzzy dedup vs prior 30 days
    collision = cache.fuzzy_collision(p.company, p.role)
    if collision:
        result.deduped += 1
        log.debug("fuzzy_dup", id=p.id, collides_with=collision)
        return None

    # Save full JD to disk
    jd_path = jds_dir / f"{_safe_id(p.id)}.md"
    jd_path.write_text(extract.html_to_markdown(p.jd_html))

    # Comp parsing — JD body is canonical; explicit_comp_string short-circuits
    comp = extract.parse_comp(
        jd_text, fx_rates=fx_rates, explicit_comp_string=p.comp_string
    )

    # Tag
    outcome = tag.tag(
        role=p.role,
        location=p.location,
        jd_text=jd_text,
        comp=comp,
        posted_at=p.posted_at,
        rules=rules,
        today=today,
    )

    # YC WaaS auto-fires early_stage per §7.2
    if source_type == "yc_waas" and "early_stage" not in outcome.tags:
        outcome.tags.append("early_stage")
        outcome.reasons.append("early_stage=yc_waas_source")

    # HN auto-fires posted_recent per §7.2
    if source_type == "hn_hiring" and "posted_recent" not in outcome.tags:
        outcome.tags.append("posted_recent")
        outcome.reasons.append("posted_recent=hn_thread_current_month")

    # Phase 1: resume↔JD skill overlap.
    match_score = 0.0
    if resume_profile is not None:
        match = resume.match(jd_text, resume_profile)
        match_score = match.score
        if resume.is_strong(match):
            outcome.tags.append("resume_strong")
            top = ",".join(match.matched[:6])
            outcome.reasons.append(f"resume_strong=overlap:{match.count}:{top}")

    result.inserted += 1
    return EnrichedRow(
        posting=p,
        discovered_at=today,
        jd_text=jd_text,
        jd_full_path=str(jd_path),
        comp_currency=comp.currency,
        comp_low=comp.low,
        comp_high=comp.high,
        comp_low_usd=comp.low_usd,
        comp_high_usd=comp.high_usd,
        tags=outcome.tags,
        tag_reasons=outcome.reasons,
        resume_match=match_score,
    )


def _is_app_eng(role: str) -> bool:
    r = (role or "").lower()
    return any(h in r for h in _YC_APP_ENG_HINTS)


def _safe_id(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def _dedup_intra_batch(rows: list[EnrichedRow]) -> tuple[list[EnrichedRow], int]:
    """Drop intra-batch fuzzy duplicates (same scout run from different sources)."""
    keep: list[EnrichedRow] = []
    seen_keys: set[tuple[str, str]] = set()
    dropped = 0
    for r in rows:
        key = (
            extract.normalize_company(r.posting.company),
            extract.normalize_role(r.posting.role),
        )
        if key in seen_keys:
            dropped += 1
            continue
        seen_keys.add(key)
        keep.append(r)
    return keep, dropped


def _to_sheet_row(r: EnrichedRow) -> list[Any]:
    p = r.posting
    snippet = extract.jd_snippet(r.jd_text)
    return [
        p.id,
        r.discovered_at.isoformat(),
        p.posted_at.isoformat() if p.posted_at else r.discovered_at.isoformat(),
        p.company,
        p.role,
        p.location or "",
        # Display compensation cell — prefer explicit, else nothing.
        p.comp_string or "",
        r.comp_currency or "",
        r.comp_low if r.comp_low is not None else "",
        r.comp_high if r.comp_high is not None else "",
        r.comp_low_usd if r.comp_low_usd is not None else "",
        r.comp_high_usd if r.comp_high_usd is not None else "",
        p.link,
        snippet,
        r.jd_full_path,
        ",".join(r.tags),
        tag.reasons_to_string(r.tag_reasons),
        schema.STATUS_NEW,
        "",  # resume_path
        "",  # last_change
        "",  # tailored_at
        "",  # applied_at
        "",  # followup_due_at
        "",  # response_at
        "",  # notes
        r.resume_match,  # 0.0 - 1.0
    ]


def _board_row(b: BoardResult) -> list[Any]:
    return [
        b.slug,
        b.source_type,
        b.board_id,
        True,
        now_iso(),
        "error" if b.error else "ok",
        b.error or "",
        b.inserted,
    ]


def _run_followup_sweep(sheet: SheetClient, *, today: date) -> int:
    """§8.1 step 5 — always runs. Idempotent: skip rows that already have followup_due_at."""
    candidates = sheet.read_followup_candidates()
    updates: list[tuple[int, str]] = []
    for row_idx, _id, applied_at, response_at in candidates:
        if not tag.needs_followup(applied_at, response_at, today=today):
            continue
        updates.append((row_idx, today.isoformat()))
    return sheet.update_followup_due(updates)


if __name__ == "__main__":
    raise SystemExit(main())
