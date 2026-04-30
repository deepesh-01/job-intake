"""FastAPI thin layer over the existing SheetClient.

Endpoints:
    GET  /api/jobs           — list with filters, search, pagination
    GET  /api/jobs/{id}      — single job + full JD markdown body
    PATCH /api/jobs/{id}     — update status / applied_at / response_at / notes
    GET  /api/stats          — counts per status + top tags + today added
    POST /api/process        — kick off the processor as a subprocess
    GET  /api/health         — liveness check

Cache: 30-second TTL on the rows snapshot. Any PATCH invalidates so the
next read is fresh. Processor invocation also invalidates after it ends.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from lib.config import load_env
from sheet import schema
from sheet.client import SheetClient, now_iso

from . import bot_health
from .cache import TTLCache


# ────────────────────────────── snapshot model ──────────────────────────────


@dataclass
class _Snapshot:
    """One full read of the Jobs tab. Indexed for O(1) id lookup."""

    rows: list[dict[str, Any]]          # parsed rows (header → cell)
    by_id: dict[str, int]               # id → 1-based sheet row index
    fetched_at: float


# ────────────────────────────── app + state ──────────────────────────────


_env = load_env()
_sheet = SheetClient(_env.creds_path, _env.sheet_id)
_cache: TTLCache[_Snapshot] = TTLCache(ttl_seconds=30.0)


app = FastAPI(title="job-intake", version="0.1.0", redirect_slashes=False)


# Permissive CORS for local dev — vite usually runs at :5173, browser
# loads from a different origin than uvicorn.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8090",
        "http://127.0.0.1:8090",
        "https://takejob.deepesh-engg.in",
    ],
    allow_origin_regex=r"^http://(192|10|172)\.[0-9.]+:5173$",  # phone on LAN
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────── auth ──────────────────────────────
#
# Public-internet deployment without a wall would let anyone PATCH the
# user's Sheet and burn their Claude API budget. Defense-in-depth:
#
#   - If WRITE_TOKEN env var is set, mutations require that token in
#     either the `Authorization: Bearer <token>` header or `?token=`
#     query param.
#   - If WRITE_TOKEN is unset, mutations are open (the local dev case).
#
# The owner pastes the token once via `?token=…` and the frontend stores
# it in localStorage; visitors without it get read-only.
_WRITE_TOKEN = os.environ.get("WRITE_TOKEN", "").strip()


def _require_write_auth(request: Request) -> None:
    if not _WRITE_TOKEN:
        return  # open mode for local dev
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        if header[7:].strip() == _WRITE_TOKEN:
            return
    qp = request.query_params.get("token")
    if qp and qp == _WRITE_TOKEN:
        return
    raise HTTPException(
        status_code=403,
        detail="read-only mode — owner-only mutation. Use the token-bookmarked URL.",
    )


# ────────────────────────────── snapshot loader ──────────────────────────────


def _load_snapshot() -> _Snapshot:
    cached = _cache.get()
    if cached is not None:
        return cached

    ws = _sheet._ws(schema.JOBS_TAB)
    raw = ws.get_all_values()
    if len(raw) < 2:
        snap = _Snapshot(rows=[], by_id={}, fetched_at=0.0)
        _cache.set(snap)
        return snap
    header = raw[1]
    rows: list[dict[str, Any]] = []
    by_id: dict[str, int] = {}
    for i, r in enumerate(raw[2:], start=3):
        d = dict(zip(header, r))
        # split tags into list
        d["tags"] = [t.strip() for t in (d.get("tags") or "").split(",") if t.strip()]
        # parse resume_match to float
        try:
            d["resume_match"] = float(d.get("resume_match") or 0.0)
        except (ValueError, TypeError):
            d["resume_match"] = 0.0
        # numeric comp parsing for sort
        for k in ("comp_low", "comp_high", "comp_low_usd", "comp_high_usd"):
            v = d.get(k)
            try:
                d[k] = int(v) if v not in (None, "") else None
            except (ValueError, TypeError):
                d[k] = None
        d["_row"] = i
        rows.append(d)
        if d.get("id"):
            by_id[d["id"]] = i

    snap = _Snapshot(rows=rows, by_id=by_id, fetched_at=__import__("time").time())
    _cache.set(snap)
    return snap


# ────────────────────────────── pydantic models ──────────────────────────────


class JobSummary(BaseModel):
    id: str
    company: str
    role: str
    location: str | None
    status: str
    tags: list[str]
    resume_match: float
    comp_string: str | None
    comp_currency: str | None
    comp_high: int | None
    comp_high_usd: int | None
    link: str
    discovered_at: str
    posted_at: str
    resume_path: str | None
    last_change: str | None
    tailored_at: str | None
    applied_at: str | None
    response_at: str | None
    followup_due_at: str | None
    notes: str | None
    filter_updated_at: str | None


class JobDetail(JobSummary):
    jd_snippet: str
    jd_full_path: str
    jd_markdown: str | None      # body read from disk
    tag_reasons: str | None
    # True iff resume_path is a local (non-http) path AND the file still
    # exists on disk. Lets the frontend choose between "Re-upload to Drive"
    # (file present) and "Re-tailor required" (file gone). Always None when
    # resume_path is an http URL — the column is irrelevant in that case.
    local_pdf_exists: bool | None = None


class JobUpdate(BaseModel):
    status: str | None = Field(default=None)
    applied_at: str | None = None
    response_at: str | None = None
    notes: str | None = None


class JobsListResponse(BaseModel):
    rows: list[JobSummary]
    total: int
    served_at: float
    cache_age_s: float


class StatsResponse(BaseModel):
    by_status: dict[str, int]
    top_tags: list[tuple[str, int]]
    total: int
    today_added: int
    cache_age_s: float


# ────────────────────────────── helpers ──────────────────────────────


def _to_summary(d: dict[str, Any]) -> JobSummary:
    return JobSummary(
        id=d.get("id", ""),
        company=d.get("company", ""),
        role=d.get("role", ""),
        location=d.get("location") or None,
        status=d.get("status", "new") or "new",
        tags=d.get("tags", []),
        resume_match=d.get("resume_match", 0.0),
        comp_string=d.get("comp_string") or None,
        comp_currency=d.get("comp_currency") or None,
        comp_high=d.get("comp_high"),
        comp_high_usd=d.get("comp_high_usd"),
        link=d.get("link", ""),
        discovered_at=d.get("discovered_at", ""),
        posted_at=d.get("posted_at", ""),
        resume_path=d.get("resume_path") or None,
        last_change=d.get("last_change") or None,
        tailored_at=d.get("tailored_at") or None,
        applied_at=d.get("applied_at") or None,
        response_at=d.get("response_at") or None,
        followup_due_at=d.get("followup_due_at") or None,
        notes=d.get("notes") or None,
        filter_updated_at=d.get("filter_updated_at") or None,
    )


def _to_detail(d: dict[str, Any]) -> JobDetail:
    s = _to_summary(d).model_dump()
    jd_path_str = d.get("jd_full_path", "")
    body: str | None = None
    if jd_path_str:
        p = Path(jd_path_str)
        if p.is_file():
            try:
                body = p.read_text(encoding="utf-8")
            except Exception:
                body = None

    # local_pdf_exists is only meaningful for non-http resume_paths.
    rp = (d.get("resume_path") or "").strip()
    local_pdf_exists: bool | None = None
    if rp and not rp.startswith(("http://", "https://")):
        try:
            local_pdf_exists = Path(rp).is_file()
        except (OSError, ValueError):
            local_pdf_exists = False

    return JobDetail(
        **s,
        jd_snippet=d.get("jd_snippet", "") or "",
        jd_full_path=jd_path_str,
        jd_markdown=body,
        tag_reasons=d.get("tag_reasons") or None,
        local_pdf_exists=local_pdf_exists,
    )


def _matches(r: dict[str, Any], q: str | None) -> bool:
    if not q:
        return True
    needle = q.lower()
    return any(
        needle in (r.get(k) or "").lower()
        for k in ("company", "role", "location", "jd_snippet")
    )


# ────────────────────────────── routes ──────────────────────────────


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "read_only": bool(_WRITE_TOKEN),  # client checks this to grey out write UI
    }


@app.get("/api/jobs", response_model=JobsListResponse)
def list_jobs(
    status: str | None = Query(None, description="comma-separated status values"),
    tags_all: str | None = Query(None, description="comma-separated tags ALL must match"),
    tags_none: str | None = Query(None, description="comma-separated tags NONE may match"),
    q: str | None = Query(None, description="text search across company/role/location/snippet"),
    discovered_within: str | None = Query(
        None,
        description="bucket on discovered_at: 24h | 7d | 30d | older",
    ),
    sort: str = Query(
        "resume_match_desc",
        description="resume_match_desc | discovered_desc | discovered_asc | comp_high_desc | filter_updated_desc | filter_updated_asc",
    ),
    has_comp: bool = Query(
        False,
        description="When true, only return rows where comp is known "
        "(comp_string set OR comp_high set OR comp_inherited tag). "
        "Drops the no-comp Instahyre/Hirist/Workday wall.",
    ),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> JobsListResponse:
    import time as _time
    from datetime import datetime, timedelta, timezone

    snap = _load_snapshot()
    rows = snap.rows

    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        rows = [r for r in rows if (r.get("status") or "new") in wanted]
    if tags_all:
        wanted = {t.strip() for t in tags_all.split(",") if t.strip()}
        rows = [r for r in rows if wanted.issubset(set(r.get("tags", [])))]
    if tags_none:
        unwanted = {t.strip() for t in tags_none.split(",") if t.strip()}
        rows = [r for r in rows if unwanted.isdisjoint(set(r.get("tags", [])))]
    if q:
        rows = [r for r in rows if _matches(r, q)]
    if has_comp:
        rows = [
            r for r in rows
            if r.get("comp_string")
            or r.get("comp_high")
            or "comp_inherited" in (r.get("tags") or [])
            or "comp_estimated" in (r.get("tags") or [])
        ]
    if discovered_within:
        bucket = discovered_within.strip().lower()
        delta_map = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
        now_utc = datetime.now(timezone.utc)

        def _parsed(r: dict) -> datetime | None:
            raw = r.get("discovered_at")
            if not raw:
                return None
            try:
                # Treat naive timestamps as UTC (matches scout writes).
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None

        if bucket == "older":
            cutoff = now_utc - timedelta(days=30)
            rows = [r for r in rows if (dt := _parsed(r)) and dt < cutoff]
        elif bucket in delta_map:
            cutoff = now_utc - delta_map[bucket]
            rows = [r for r in rows if (dt := _parsed(r)) and dt >= cutoff]

    # sort
    if sort == "discovered_desc":
        rows.sort(key=lambda r: r.get("discovered_at", ""), reverse=True)
    elif sort == "discovered_asc":
        # Empty timestamps go last regardless of direction.
        rows.sort(key=lambda r: r.get("discovered_at") or "9999")
    elif sort == "comp_high_desc":
        rows.sort(key=lambda r: (r.get("comp_high_usd") or r.get("comp_high") or 0), reverse=True)
    elif sort == "filter_updated_desc":
        # Backfilled-fallback: use discovered_at when filter_updated_at empty
        # (rows from before v3 schema migration that haven't been mutated yet).
        rows.sort(
            key=lambda r: r.get("filter_updated_at") or r.get("discovered_at") or "",
            reverse=True,
        )
    elif sort == "filter_updated_asc":
        rows.sort(
            key=lambda r: r.get("filter_updated_at") or r.get("discovered_at") or "9999",
        )
    else:
        rows.sort(key=lambda r: r.get("resume_match", 0.0), reverse=True)

    total = len(rows)
    page = rows[offset : offset + limit]

    return JobsListResponse(
        rows=[_to_summary(r) for r in page],
        total=total,
        served_at=_time.time(),
        cache_age_s=max(0.0, _time.time() - snap.fetched_at),
    )


@app.get("/api/jobs/{job_id:path}", response_model=JobDetail)
def get_job(job_id: str) -> JobDetail:
    snap = _load_snapshot()
    row_idx = snap.by_id.get(job_id)
    if row_idx is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    row = next((r for r in snap.rows if r.get("id") == job_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="row vanished from snapshot")
    return _to_detail(row)


_ALLOWED_FIELDS = {"status", "applied_at", "response_at", "notes"}


@app.patch("/api/jobs/{job_id:path}", response_model=JobDetail)
def patch_job(job_id: str, payload: JobUpdate, request: Request) -> JobDetail:
    _require_write_auth(request)
    snap = _load_snapshot()
    row_idx = snap.by_id.get(job_id)
    if row_idx is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")

    updates: dict[str, str] = {}
    if payload.status is not None:
        if payload.status not in schema.VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"invalid status: {payload.status} (allowed: {sorted(schema.VALID_STATUSES)})",
            )
        updates["status"] = payload.status
        # Stamp filter_updated_at on every status transition — drives the
        # per-status "Recently moved" sort in the webapp.
        updates["filter_updated_at"] = now_iso()
    if payload.applied_at is not None:
        updates["applied_at"] = payload.applied_at
    if payload.response_at is not None:
        updates["response_at"] = payload.response_at
    if payload.notes is not None:
        updates["notes"] = payload.notes

    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    ws = _sheet._ws(schema.JOBS_TAB)
    body = [
        {"range": f"{schema.col_letter(field)}{row_idx}", "values": [[value]]}
        for field, value in updates.items()
    ]
    ws.batch_update(body, value_input_option="USER_ENTERED")

    _cache.invalidate()
    fresh = _load_snapshot()
    row = next((r for r in fresh.rows if r.get("id") == job_id), None)
    if row is None:
        raise HTTPException(status_code=500, detail="row missing after update")
    return _to_detail(row)


class RetryResponse(BaseModel):
    reset: int
    job_ids: list[str]


@app.post("/api/jobs/retry-errored", response_model=RetryResponse)
def retry_errored(request: Request) -> RetryResponse:
    """Owner-only. Bulk-resets every status=error row back to status=tailor
    and clears last_change + tailored_at so they re-enter the queue cleanly.
    Returns the count + list of ids that were reset.

    The Processor on the next sweep picks them up. If the underlying root
    cause persists, they'll error again — explicitly user-driven retry,
    no auto-loop."""
    _require_write_auth(request)

    snap = _load_snapshot()
    errored: list[tuple[int, str]] = [
        (r["_row"], r.get("id", ""))
        for r in snap.rows
        if (r.get("status") or "") == schema.STATUS_ERROR
    ]
    if not errored:
        return RetryResponse(reset=0, job_ids=[])

    ws = _sheet._ws(schema.JOBS_TAB)
    body: list[dict] = []
    status_col = schema.col_letter("status")
    last_change_col = schema.col_letter("last_change")
    tailored_at_col = schema.col_letter("tailored_at")
    filter_col = schema.col_letter("filter_updated_at")
    now = now_iso()
    for row_idx, _ in errored:
        body.append({"range": f"{status_col}{row_idx}", "values": [[schema.STATUS_TAILOR]]})
        body.append({"range": f"{last_change_col}{row_idx}", "values": [[""]]})
        body.append({"range": f"{tailored_at_col}{row_idx}", "values": [[""]]})
        body.append({"range": f"{filter_col}{row_idx}", "values": [[now]]})
    ws.batch_update(body, value_input_option="USER_ENTERED")
    _cache.invalidate()
    return RetryResponse(reset=len(errored), job_ids=[i for _, i in errored])


@app.get("/api/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    import time as _time
    from datetime import date

    snap = _load_snapshot()
    by_status: Counter[str] = Counter(
        (r.get("status") or "new") for r in snap.rows
    )
    tag_counts: Counter[str] = Counter()
    for r in snap.rows:
        for t in r.get("tags", []):
            tag_counts[t] += 1
    today = date.today().isoformat()
    today_added = sum(1 for r in snap.rows if r.get("discovered_at", "").startswith(today))
    return StatsResponse(
        by_status=dict(by_status),
        top_tags=tag_counts.most_common(15),
        total=len(snap.rows),
        today_added=today_added,
        cache_age_s=max(0.0, _time.time() - snap.fetched_at),
    )


_WEBAPP_DIST = Path(__file__).resolve().parent.parent.parent / "webapp" / "dist"


def _mount_webapp() -> None:
    """Serve the production webapp bundle from the same port as the API.
    Vite outputs to webapp/dist/. API routes are registered above this so
    they take precedence; the SPA catch-all only fires for non-/api paths."""
    if not _WEBAPP_DIST.is_dir():
        return  # dev mode — Vite serves itself on :5173

    # Static assets (JS/CSS bundles in dist/assets/, plus favicon etc.)
    app.mount(
        "/assets",
        StaticFiles(directory=str(_WEBAPP_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # /api/* paths shouldn't reach here (declared above), but guard anyway.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        # If a real file exists at this path under webapp/dist/ — manifest,
        # sw.js, icons, favicon, etc. — serve THAT, not the SPA index.
        # (Vite copies /public/* into the dist root at build time.)
        if full_path:
            candidate = _WEBAPP_DIST / full_path
            try:
                resolved = candidate.resolve()
                if (
                    candidate.is_file()
                    and resolved.is_relative_to(_WEBAPP_DIST.resolve())
                ):
                    # Short cache so Cloudflare respects updates within minutes,
                    # not hours. Cloudflare's default edge cache for static
                    # responses is 4h; that bit us once when the SPA fallback
                    # cached HTML at icon URLs.
                    headers = {"Cache-Control": "public, max-age=300, must-revalidate"}
                    if full_path == "sw.js":
                        # Service workers must NOT be edge-cached or rollouts
                        # of new SW behavior get pinned for hours.
                        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    return FileResponse(str(candidate), headers=headers)
            except (OSError, ValueError):
                pass  # fall through to SPA index

        # SPA fallback — client-side router handles unknown paths.
        index = _WEBAPP_DIST / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=500, detail="webapp/dist/index.html missing")
        # Don't let Cloudflare pin index.html for hours — the JS bundle name
        # inside changes on every build, and stale index.html → 404 on assets.
        return FileResponse(
            str(index),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )


_LOCK_DIR = _env.data_dir / "locks"
_LOCK_ORPHAN_SECONDS = 60 * 60  # match processor.runner


def _active_locks() -> list[Path]:
    """Lock files whose mtime is fresh (i.e. processor is still working on
    that row). Anything older than _LOCK_ORPHAN_SECONDS is treated as
    abandoned and ignored."""
    if not _LOCK_DIR.is_dir():
        return []
    import time as _t
    now = _t.time()
    return [
        p for p in _LOCK_DIR.glob("*.lock")
        if now - p.stat().st_mtime < _LOCK_ORPHAN_SECONDS
    ]


def _id_from_lock(name: str, snap: _Snapshot) -> str | None:
    """Reverse of processor.runner._safe_id(). Not perfectly invertible
    (both ':' and '/' become '_'), so we look up against the snapshot's
    known ids for an exact match."""
    base = name.removesuffix(".lock")
    for id_ in snap.by_id.keys():
        if id_.replace(":", "_").replace("/", "_") == base:
            return id_
    return None


_RETAILOR_REASONS = {
    "layout": "Layout not okay",
    "shallow_detailing": "Shallow detailing — bullets feel generic / not concrete enough",
    "drifting_from_jd": "Drifting from JD — bullets don't address what the JD asks for",
    "other": "Other",
}


class RetailorRequest(BaseModel):
    reason: str = Field(..., description="layout | shallow_detailing | drifting_from_jd | other")
    details: str | None = Field(default=None, description="free text required when reason=other")
    iterate: bool = Field(
        default=True,
        description="True = iterate on existing tailored resume (cli-edit), "
        "False = fresh tailor with feedback in JD wrapper (cli-tailor).",
    )


@app.post("/api/jobs/{job_id:path}/retailor")
def retailor_with_feedback(job_id: str, payload: RetailorRequest, request: Request) -> dict:
    """Queue a row for re-tailor with explicit user feedback. The feedback
    is prepended to the original JD as a wrapper file at
    `data/jds_retailor/<safe_id>.md`, which the processor uses *instead*
    of the original JD on the next pass — so System A's LLM picks up the
    feedback via the existing `--jd-path` contract (no System A change).

    Body: { reason: layout|shallow_detailing|drifting_from_jd|other, details?: str }
    """
    _require_write_auth(request)
    if payload.reason not in _RETAILOR_REASONS:
        raise HTTPException(
            status_code=400,
            detail=f"reason must be one of {sorted(_RETAILOR_REASONS)}",
        )
    if payload.reason == "other" and not (payload.details or "").strip():
        raise HTTPException(
            status_code=400,
            detail="details is required when reason='other'",
        )

    snap = _load_snapshot()
    row = next((r for r in snap.rows if r.get("id") == job_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    row_idx = snap.by_id.get(job_id)
    jd_path_str = row.get("jd_full_path") or ""
    if not jd_path_str or not Path(jd_path_str).is_file():
        raise HTTPException(
            status_code=404,
            detail=f"original JD missing on disk ({jd_path_str}) — can't re-tailor without it",
        )

    # Build the wrapper. The plain-prose intro is what the LLM sees first.
    reason_label = _RETAILOR_REASONS[payload.reason]
    details_text = (payload.details or "").strip()
    feedback_block = (
        "## RE-TAILOR FEEDBACK FROM USER\n\n"
        "The previous tailoring attempt of this resume was reviewed by the "
        "user and rejected with the following feedback:\n\n"
        f"- **Issue category:** {reason_label}\n"
    )
    if details_text:
        feedback_block += f"- **Specific feedback:** {details_text}\n"
    feedback_block += (
        "\n**Apply this feedback when re-tailoring.** Make explicit, "
        "addressable changes that resolve the issue above. Do not regenerate "
        "the same content — improve specifically along the feedback dimension.\n\n"
        "---\n\n"
        "## ORIGINAL JOB DESCRIPTION\n\n"
    )
    original_jd = Path(jd_path_str).read_text(encoding="utf-8")
    wrapper_text = feedback_block + original_jd

    env = load_env()
    wrapper_dir = Path(env.data_dir) / "jds_retailor"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    safe_id = job_id.replace(":", "_").replace("/", "_")
    wrapper_path = wrapper_dir / f"{safe_id}.md"
    wrapper_path.write_text(wrapper_text, encoding="utf-8")

    # Sidecar JSON drives the processor's mode choice (iterate vs fresh).
    # Also carries a single `instruction` string ready for cli-edit.js — so
    # the processor doesn't need to re-derive it from the wrapper file.
    instruction_text = f"User feedback on previous tailored resume — {reason_label}."
    if details_text:
        instruction_text += f" Specific feedback: {details_text}"
    instruction_text += (
        " Edit resume.md in-place to address this feedback specifically. "
        "Do not regenerate the same content — improve along the feedback dimension. "
        # Pages-disambiguation: prior runs misread "make it 2 pages" as a
        # target to expand TO rather than a ceiling to compress UNDER. Pin
        # the interpretation so any page-count instruction is always a
        # hard upper bound + always implies trimming, never expansion.
        "IMPORTANT: if the user mentions a page count (e.g. '2 pages', "
        "'one page', 'fits on a page'), treat it strictly as a HARD UPPER "
        "BOUND. If the current resume is already at or below that count, "
        "leave length alone and focus on the other parts of the feedback. "
        "If it exceeds the bound, COMPRESS — cut the least-JD-aligned "
        "bullets, drop projects irrelevant to this role, tighten prose. "
        "NEVER expand a shorter resume to fill a stated page target. "
        "After editing, render to PDF mentally — if you suspect the page "
        "count still exceeds the bound, cut more before finishing. "
        "After editing, write a one-line summary of the changes to last_change.txt."
    )
    sidecar_path = wrapper_dir / f"{safe_id}.json"
    import json as _json
    sidecar_path.write_text(
        _json.dumps({
            "reason": payload.reason,
            "reason_label": reason_label,
            "details": details_text,
            "iterate": bool(payload.iterate),
            "instruction": instruction_text,
            "queued_at": now_iso(),
        }, indent=2),
        encoding="utf-8",
    )

    # Flip the row to status=tailor + clear resume_path so the processor
    # re-enqueues it. Stamp filter_updated_at so it surfaces in "Recently
    # moved". Stash the feedback summary in last_change for audit.
    if row_idx is None:
        raise HTTPException(status_code=500, detail="row vanished from snapshot")
    last_change_msg = f"Retailor requested: {reason_label}"
    if details_text:
        last_change_msg += f" — {details_text[:140]}"
    now = now_iso()
    ws = _sheet._ws(schema.JOBS_TAB)
    ws.batch_update(
        [
            {"range": f"{schema.col_letter('status')}{row_idx}", "values": [[schema.STATUS_TAILOR]]},
            {"range": f"{schema.col_letter('resume_path')}{row_idx}", "values": [[""]]},
            {"range": f"{schema.col_letter('last_change')}{row_idx}", "values": [[last_change_msg[:200]]]},
            {"range": f"{schema.col_letter('filter_updated_at')}{row_idx}", "values": [[now]]},
        ],
        value_input_option="USER_ENTERED",
    )
    _cache.invalidate()
    return {
        "ok": True,
        "wrapper_path": str(wrapper_path),
        "sidecar_path": str(sidecar_path),
        "reason": payload.reason,
        "iterate": bool(payload.iterate),
        "queued_for": "tailor",
    }


@app.post("/api/jobs/{job_id:path}/resume/reupload")
def resume_reupload(job_id: str, request: Request) -> dict:
    """Re-upload a row's local PDF to Drive and patch the sheet's
    resume_path cell. Heals the rare 'Drive upload failed at tailor time'
    state without requiring an expensive re-tailor (~$0.40 saved).

    400: resume_path already http (nothing to reupload).
    404: resume_path empty OR local file missing (re-tailor needed).
    """
    _require_write_auth(request)
    snap = _load_snapshot()
    row = next((r for r in snap.rows if r.get("id") == job_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    rp = (row.get("resume_path") or "").strip()
    if not rp:
        raise HTTPException(status_code=404, detail="row has no resume_path — re-tailor required")
    if rp.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="resume_path is already an http URL")

    local_path = Path(rp)
    if not local_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"local PDF missing at {rp} — re-tailor required",
        )

    env = load_env()
    if not env.drive_folder_id:
        raise HTTPException(status_code=500, detail="Drive folder not configured")

    # Prefer OAuth user delegation (matches processor); fall back to SA.
    if env.drive_oauth_token_path and env.drive_oauth_token_path.is_file():
        from lib.drive import DriveClient
        drive = DriveClient.from_oauth_token(env.drive_oauth_token_path)
    else:
        from lib.drive import DriveClient
        drive = DriveClient.from_service_account(env.creds_path)

    import re as _re
    _short = lambda s: _re.sub(r"[^A-Za-z0-9 ]+", " ", s or "").strip()[:50]
    suffix = job_id.split(":")[-1][:8]
    display_name = f"{_short(row.get('company') or '')} - {_short(row.get('role') or '')} ({suffix}).pdf"

    drive_url = drive.upload_pdf(
        local_path=local_path,
        folder_id=env.drive_folder_id,
        display_name=display_name,
    )

    ws = _sheet._ws(schema.JOBS_TAB)
    row_idx = snap.by_id.get(job_id)
    if row_idx is None:
        raise HTTPException(status_code=500, detail="row vanished from snapshot")

    now = now_iso()
    ws.batch_update(
        [
            {"range": f"{schema.col_letter('resume_path')}{row_idx}", "values": [[drive_url]]},
            {"range": f"{schema.col_letter('filter_updated_at')}{row_idx}", "values": [[now]]},
        ],
        value_input_option="USER_ENTERED",
    )
    _cache.invalidate()
    return {"ok": True, "drive_url": drive_url}


@app.post("/api/jobs/{job_id:path}/copilot/start")
def copilot_start(job_id: str, request: Request) -> dict:
    """Launch the Apply-Copilot for a single row. Fires a Playwright
    subprocess that pops up a Chromium window pointed at the job's apply
    URL. Returns immediately; the user interacts with the window directly
    and marks the row `applied` themselves when done.

    Currently supports LinkedIn only — other ATSes return 400 until their
    handlers ship.
    """
    _require_write_auth(request)
    snap = _load_snapshot()
    row = next((r for r in snap.rows if r.get("id") == job_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    link = (row.get("link") or "").strip()
    if not link:
        raise HTTPException(status_code=400, detail="row has no link")
    if "linkedin.com" not in link:
        raise HTTPException(
            status_code=400,
            detail=f"copilot only supports LinkedIn for now (link host: {link[:60]})",
        )

    import time as _time
    repo_root = Path(__file__).resolve().parent.parent.parent
    runs_dir = repo_root / "data" / "apply_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    safe_id = job_id.replace(":", "_").replace("/", "_")
    log_path = runs_dir / f"{safe_id}_{int(_time.time())}.log"
    log_handle = open(log_path, "w", buffering=1)  # line-buffered for tail -f
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "apply",
            "linkedin",
            link,
            "--role", row.get("role") or "",
            "--company", row.get("company") or "",
        ],
        cwd=str(repo_root),
        env={
            **os.environ,
            "PYTHONPATH": str(repo_root / "src"),
        },
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "job_id": job_id,
        "link": link,
        "log_path": str(log_path),
    }


@app.get("/api/process/status")
def process_status() -> dict:
    """Real-time state of the processor. Frontend polls this — every 5s
    while a run is active, every 30s otherwise."""
    snap = _load_snapshot()
    queued = sum(1 for r in snap.rows if (r.get("status") or "new") == "tailor")
    ready = sum(1 for r in snap.rows if (r.get("status") or "new") == "ready")
    locks = _active_locks()
    is_running = len(locks) > 0

    current_id: str | None = None
    current_company: str | None = None
    current_role: str | None = None
    if locks:
        latest = max(locks, key=lambda p: p.stat().st_mtime)
        current_id = _id_from_lock(latest.name, snap)
        if current_id:
            row = next((r for r in snap.rows if r.get("id") == current_id), None)
            if row:
                current_company = row.get("company") or None
                current_role = row.get("role") or None

    return {
        "is_running": is_running,
        "current_id": current_id,
        "current_company": current_company,
        "current_role": current_role,
        "queue_remaining": queued,
        "ready_total": ready,
        "active_locks": len(locks),
    }


# ────────────────────────────── bot watchdog ──────────────────────────────


@app.get("/api/bot/health")
def bot_health_endpoint() -> dict:
    """Resume-builder watchdog mirror. Polled by the UI every 30s."""
    h = bot_health.get_health()
    state = (
        "down" if not h.is_alive
        else "hung" if h.is_hung
        else "ok"
    )
    return {
        "state": state,
        "is_alive": h.is_alive,
        "is_hung": h.is_hung,
        "pids": list(h.pids),
        "heartbeat_age_seconds": h.heartbeat_age_seconds,
        "last_heartbeat_iso": h.last_heartbeat_iso,
        "stale_threshold_seconds": bot_health.HEARTBEAT_STALE_SECONDS,
        "auto_watchdog_interval_seconds": _AUTO_WATCHDOG_INTERVAL,
        "restart_cooldown_seconds": bot_health.RESTART_COOLDOWN_SECONDS,
    }


@app.post("/api/bot/restart")
def bot_restart_endpoint(request: Request) -> dict:
    """Owner-only manual restart. Mutates the system."""
    _require_write_auth(request)
    return {"ok": True, **bot_health.restart_bot(
        triggered_by="jobintake-webapp",
        reason="manual-ui",
    )}


# ────────────────────────────── auto-restart background task ──────────────────


_AUTO_WATCHDOG_INTERVAL = 60.0  # seconds


async def _auto_watchdog_loop() -> None:
    """Background task — polls the heartbeat every minute. If the bot is
    hung AND we're past the cooldown, kicks off a restart automatically.

    Coexists with the user's launchd watchdog; both are idempotent so the
    worst case is a brief race that the kill-then-start absorbs."""
    import logging
    log = logging.getLogger("bot_watchdog")
    log.setLevel(logging.INFO)

    while True:
        try:
            h = bot_health.get_health()
            if h.is_hung and bot_health.can_auto_restart():
                log.warning(
                    "auto-restart: bot hung age=%ss pids=%s",
                    h.heartbeat_age_seconds, h.pids,
                )
                # Run restart in a thread so we don't block the event loop
                # (it sleeps several seconds for graceful shutdown).
                await asyncio.to_thread(
                    bot_health.restart_bot,
                    triggered_by="jobintake-webapp",
                    reason=f"auto-hung age={int(h.heartbeat_age_seconds or 0)}s",
                )
            elif not h.is_alive and bot_health.can_auto_restart():
                log.warning("auto-restart: bot down")
                await asyncio.to_thread(
                    bot_health.restart_bot,
                    triggered_by="jobintake-webapp",
                    reason="auto-down",
                )
        except Exception as e:
            log.warning("watchdog tick failed: %s", e)
        await asyncio.sleep(_AUTO_WATCHDOG_INTERVAL)


@app.on_event("startup")
async def _start_watchdog() -> None:
    asyncio.create_task(_auto_watchdog_loop())


# ────────────────────────────── processor ──────────────────────────────


@app.post("/api/process")
async def process_queue(request: Request) -> dict:
    _require_write_auth(request)
    """Spawn `python -m processor.runner` as a subprocess.
    Streams nothing — returns once the process exits.
    Long-running (~5 min per row) so the UI should poll `/api/stats`
    or just optimistically refresh after a while."""
    # tailor_bridge spawns `node dist/cli-tailor.js`. The launchd-managed
    # webapp inherits a PATH that doesn't include fnm's node binary, so
    # explicitly prepend it here too. Belt + suspenders: the plist's
    # EnvironmentVariables PATH is set the same way.
    fnm_node = str(Path.home() / ".local/share/fnm/aliases/default/bin")
    parent_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    augmented_path = f"{fnm_node}:{parent_path}" if fnm_node not in parent_path else parent_path
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "processor.runner",
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        env={
            **os.environ,
            "PATH": augmented_path,
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # Detach from uvicorn's process group so a webapp reload doesn't
        # SIGTERM the processor mid-tailor. (Bug observed 2026-04-29: a
        # rebuild + `launchctl kickstart -k web` killed an in-flight refine
        # step, leaving the row in `tailor` with a stale lock.)
        start_new_session=True,
    )
    stdout, stderr = await proc.communicate()
    _cache.invalidate()
    return {
        "exit_code": proc.returncode,
        "stdout_tail": (stdout or b"").decode("utf-8", errors="replace")[-1000:],
        "stderr_tail": (stderr or b"").decode("utf-8", errors="replace")[-1000:],
    }


# Register the SPA fallback last so all /api/* routes win precedence.
_mount_webapp()
