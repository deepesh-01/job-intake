"""Processor entrypoint per §8.3.

Sweeps Sheet for status=tailor rows with empty resume_path. For each:
1. Acquire per-row file lock (data/locks/<id>).
2. Call System A via tailor_bridge.run_tailor.
3. Move PDF into data/tailored/<id>.pdf.
4. Update Sheet row: resume_path, last_change, tailored_at, status=ready.
5. Release lock.

Lock orphan policy: locks older than 1h are auto-cleared (§10.7).
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path

from lib.config import load_env
from lib.drive import DriveClient
from lib.logging import configure
from sheet import schema
from sheet.client import SheetClient, now_iso
from tailor_bridge import run_tailor

_LOCK_ORPHAN_SECONDS = 60 * 60  # 1h per §10.7


def main() -> int:
    env = load_env()
    log = configure(env.log_level)
    log.info("processor_started")

    sheet = SheetClient(env.creds_path, env.sheet_id)
    sheet.assert_schema_compatible()

    drive: DriveClient | None = None
    if env.drive_folder_id:
        # OAuth mode preferred — files owned by the user, normal quota.
        # SA mode only works for Workspace Shared Drives.
        if env.drive_oauth_token_path and env.drive_oauth_token_path.is_file():
            try:
                drive = DriveClient.from_oauth_token(env.drive_oauth_token_path)
                log.info(
                    "drive_upload_enabled",
                    mode="oauth",
                    folder_id=env.drive_folder_id,
                )
            except Exception as e:
                log.warning("drive_oauth_load_failed", err=str(e))
        if drive is None:
            try:
                drive = DriveClient.from_service_account(env.creds_path)
                log.info(
                    "drive_upload_enabled",
                    mode="service_account",
                    folder_id=env.drive_folder_id,
                    note="SA mode only works for Workspace Shared Drives",
                )
            except Exception as e:
                log.warning("drive_sa_load_failed", err=str(e))
    else:
        log.info("drive_upload_disabled", reason="GOOGLE_DRIVE_FOLDER_ID not set")

    locks_dir = env.data_dir / "locks"
    tailored_dir = env.data_dir / "tailored"
    locks_dir.mkdir(parents=True, exist_ok=True)
    tailored_dir.mkdir(parents=True, exist_ok=True)

    rows = sheet.read_tailor_queue()
    log.info("processor_queue_size", count=len(rows))

    processed = errored = skipped = 0
    for row_idx, row in rows:
        job_id = row.get("id", "")
        if not job_id:
            continue
        lock = locks_dir / f"{_safe(job_id)}.lock"
        if not _acquire_lock(lock, log):
            skipped += 1
            continue
        try:
            jd_path = row.get("jd_full_path", "")
            if not jd_path or not Path(jd_path).exists():
                _mark_error(sheet, row_idx, f"jd_missing: {jd_path}")
                errored += 1
                continue

            result = run_tailor(
                job_id=job_id,
                jd_path=jd_path,
                chat_id=env.system_a_user_chat_id,
                output_dir=str(tailored_dir),
                system_a_path=env.system_a_path,
                dry_run=env.tailor_dry_run,
            )
            if not result.ok:
                _mark_error(sheet, row_idx, result.error or "unknown_error")
                errored += 1
                log.warning("tailor_failed", job_id=job_id, error=result.error)
                continue

            # Move PDF into canonical local location if needed.
            final_pdf = tailored_dir / f"{_safe(job_id)}.pdf"
            if result.pdf_path and Path(result.pdf_path) != final_pdf:
                shutil.move(result.pdf_path, final_pdf)

            # If Drive is configured, upload + use the share URL as the
            # resume_path cell value (clickable from any device). Local PDF
            # is kept on disk as a backup. On Drive failure, fall back to
            # local path so the user still has a working reference.
            sheet_resume_path = str(final_pdf)
            if drive is not None:
                try:
                    company = (row.get("company") or "company").strip()
                    role = (row.get("role") or "role").strip()
                    display_name = (
                        _drive_filename(company, role, job_id)
                    )
                    drive_url = drive.upload_pdf(
                        local_path=final_pdf,
                        folder_id=env.drive_folder_id,  # type: ignore[arg-type]
                        display_name=display_name,
                    )
                    sheet_resume_path = drive_url
                    log.info("drive_uploaded", job_id=job_id, url=drive_url)
                except Exception as e:
                    log.warning(
                        "drive_upload_failed",
                        job_id=job_id,
                        err=str(e)[:200],
                    )
                    # Fall back to local path — non-fatal.

            sheet.update_row_after_tailor(
                row_idx,
                resume_path=sheet_resume_path,
                last_change=result.last_change or "",
                tailored_at=now_iso(),
                new_status=schema.STATUS_READY,
            )
            sheet.append_log(now_iso(), "processor", "tailored", job_id)
            processed += 1
            log.info("tailor_ok", job_id=job_id, pdf=str(final_pdf))
        finally:
            _release_lock(lock)

    sheet.append_log(
        now_iso(),
        "processor",
        "finished",
        f"processed={processed} errored={errored} skipped={skipped}",
    )
    log.info(
        "processor_finished",
        processed=processed,
        errored=errored,
        skipped=skipped,
    )
    return 0


def _safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def _drive_filename(company: str, role: str, job_id: str) -> str:
    """Human-readable Drive name. The job_id suffix is for uniqueness in
    case the user marks the same role multiple times."""
    import re
    short = lambda s: re.sub(r"[^A-Za-z0-9 ]+", " ", s).strip()[:50]
    suffix = job_id.split(":")[-1][:8]
    return f"{short(company)} - {short(role)} ({suffix}).pdf"


def _acquire_lock(lock: Path, log) -> bool:
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < _LOCK_ORPHAN_SECONDS:
            log.info("lock_busy_skipping", lock=str(lock), age_s=int(age))
            return False
        log.warning("lock_orphan_clearing", lock=str(lock), age_s=int(age))
        lock.unlink(missing_ok=True)
    lock.touch()
    return True


def _release_lock(lock: Path) -> None:
    lock.unlink(missing_ok=True)


def _mark_error(sheet: SheetClient, row_idx: int, message: str) -> None:
    sheet.update_row_after_tailor(
        row_idx,
        resume_path="",
        last_change=message[:200],
        tailored_at=now_iso(),
        new_status=schema.STATUS_ERROR,
    )


if __name__ == "__main__":
    raise SystemExit(main())
