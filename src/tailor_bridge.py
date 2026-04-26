"""Bridge from System B (Python) to System A (TypeScript/Node).

System A exposes (per its ADR-021):

    node $SYSTEM_A_PATH/dist/cli-tailor.js \\
        --jd-path PATH --chat-id INT [--output-dir PATH] \\
        --output-format json

Returns a JSON object on stdout with
{ok, pdf_path, last_change, score, refinement_applied, duration_ms, error}.

If TAILOR_DRY_RUN=true, the bridge mocks the call: writes a placeholder
PDF and returns ok=true. Useful for validating the Sheet flow without
spending Claude API budget.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TailorResult:
    ok: bool
    pdf_path: str | None
    last_change: str | None
    error: str | None
    duration_ms: int | None = None
    score: int | None = None
    refinement_applied: bool = False


def run_tailor(
    *,
    job_id: str,
    jd_path: str,
    chat_id: int,
    output_dir: str,
    system_a_path: Path,
    dry_run: bool = False,
    timeout: int = 600,
) -> TailorResult:
    """Invoke System A's cli-tailor and parse the JSON response.

    `chat_id` selects whose `~/bot/users/<chat_id>/base_resume.md` is used.
    `output_dir` (System B's `data/tailored/`) is where the produced PDF
    will be copied so System B has a stable, predictable path.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return _mock_tailor(job_id, jd_path, out_dir)

    cli = system_a_path / "dist" / "cli-tailor.js"
    if not cli.is_file():
        return TailorResult(
            ok=False,
            pdf_path=None,
            last_change=None,
            error=(
                f"system_a_cli_missing: {cli} not built. "
                "From System A: `npm run build`. Or set TAILOR_DRY_RUN=true."
            ),
        )

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "node", str(cli),
                "--jd-path", jd_path,
                "--chat-id", str(chat_id),
                "--output-dir", str(out_dir),
                "--output-format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            # System A's config.ts loads its .env from CWD via dotenv/config.
            # Without this, the bot's required env vars (TELEGRAM_BOT_TOKEN
            # etc.) won't be found and the CLI exits 1 before doing anything.
            cwd=str(system_a_path),
        )
    except subprocess.TimeoutExpired:
        return TailorResult(False, None, None, "system_a_timeout")
    except Exception as e:
        return TailorResult(False, None, None, f"bridge_error: {e}")

    duration_ms = int((time.monotonic() - started) * 1000)

    # System A's CLI emits a JSON line on stdout even on caught failures
    # (exit 1). Try to parse first; fall back to stderr-based error if
    # stdout isn't JSON (exit 2 — bad args).
    data: dict | None = None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else None
    except (json.JSONDecodeError, IndexError):
        pass

    if data is None:
        return TailorResult(
            ok=False,
            pdf_path=None,
            last_change=None,
            error=(proc.stderr or proc.stdout or f"exit={proc.returncode}")[:500],
            duration_ms=duration_ms,
        )

    return TailorResult(
        ok=bool(data.get("ok")),
        pdf_path=data.get("pdf_path"),
        last_change=data.get("last_change"),
        error=data.get("error"),
        duration_ms=duration_ms,
        score=data.get("score"),
        refinement_applied=bool(data.get("refinement_applied", False)),
    )


def _mock_tailor(job_id: str, jd_path: str, out_dir: Path) -> TailorResult:
    """Stub used when TAILOR_DRY_RUN=true."""
    pdf = out_dir / f"{_safe(job_id)}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% Stub PDF written by tailor_bridge dry-run mode.\n")
    return TailorResult(
        ok=True,
        pdf_path=str(pdf),
        last_change=f"[dry-run] would tailor against {Path(jd_path).name}",
        error=None,
        duration_ms=0,
    )


def _safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")
