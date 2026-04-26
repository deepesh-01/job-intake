"""Resume-builder watchdog mirror — detect hangs and restart from job-intake.

Mirrors the contract of the existing `resume-builder/scripts/watchdog.sh`:
- Heartbeat file at `~/bot/.heartbeat` ticks every 60s (the bot writes it).
- Stale threshold = 180s (3 min). Beyond that, the bot is considered hung.
- Restart logic: SIGINT then SIGKILL grace, then `nohup npm start &` from
  the resume-builder repo cwd.

We co-exist with the user's launchd watchdog (`com.deepesh.resume-bot-
watchdog`). Both can race on a hang; the kill is idempotent and Telegram's
`getUpdates` 409 conflict will eventually leave one bot polling.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


HEARTBEAT_FILE = Path.home() / "bot" / ".heartbeat"
RESUME_BUILDER_ROOT = Path.home() / "Documents" / "resume-builder"
HEARTBEAT_STALE_SECONDS = 180
RESTART_COOLDOWN_SECONDS = 300  # don't auto-restart more than once per 5 min


@dataclass(frozen=True)
class BotHealth:
    is_alive: bool                # any matching process is running
    pids: tuple[int, ...]         # the matching PIDs (cwd = resume-builder)
    heartbeat_age_seconds: float | None  # None = no heartbeat file
    last_heartbeat_iso: str | None
    is_hung: bool                 # alive AND heartbeat stale OR missing


def _read_heartbeat() -> tuple[float | None, float | None]:
    """Return (heartbeat_unix_seconds, age_seconds). Both None if absent."""
    if not HEARTBEAT_FILE.is_file():
        return None, None
    try:
        ts_ms = int(HEARTBEAT_FILE.read_text().strip())
    except (ValueError, OSError):
        return None, None
    ts = ts_ms / 1000.0
    return ts, max(0.0, time.time() - ts)


def _find_bot_pids() -> tuple[int, ...]:
    """All PIDs of `node dist/index.js` whose cwd is the resume-builder repo."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", r"node dist/index\.js"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ()
    pids: list[int] = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if _pid_cwd(pid) == RESUME_BUILDER_ROOT:
            pids.append(pid)
    return tuple(pids)


def _pid_cwd(pid: int) -> Path | None:
    """Return the cwd of `pid` via lsof. None if we can't read it."""
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in (out.stdout or "").splitlines():
        if line.startswith("n"):
            return Path(line[1:])
    return None


def get_health() -> BotHealth:
    pids = _find_bot_pids()
    ts, age = _read_heartbeat()
    is_alive = len(pids) > 0
    heartbeat_stale = age is None or age > HEARTBEAT_STALE_SECONDS
    is_hung = is_alive and heartbeat_stale
    iso = (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        if ts is not None
        else None
    )
    return BotHealth(
        is_alive=is_alive,
        pids=pids,
        heartbeat_age_seconds=age,
        last_heartbeat_iso=iso,
        is_hung=is_hung,
    )


# ────────────────────────────── restart ──────────────────────────────


_last_restart_at: float = 0.0


def can_auto_restart() -> bool:
    """Cooldown gate so we don't fight the launchd watchdog or restart-loop."""
    return (time.time() - _last_restart_at) >= RESTART_COOLDOWN_SECONDS


def restart_bot() -> dict:
    """Kill any existing bot PIDs (SIGINT, then SIGKILL), then `nohup npm start`
    from the resume-builder root. Returns a small status dict."""
    global _last_restart_at
    _last_restart_at = time.time()

    pids = _find_bot_pids()
    killed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGINT)
            killed.append(pid)
        except OSError:
            pass

    # Grace period for clean shutdown.
    time.sleep(5)

    # Force-kill any survivors.
    for pid in pids:
        try:
            os.kill(pid, 0)  # check still alive
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    time.sleep(1)

    # Spawn fresh bot. Detached so it survives uvicorn restart / SIGTERM.
    log_path = Path.home() / "bot" / "logs" / "watchdog-jobintake.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            ["npm", "start"],
            cwd=str(RESUME_BUILDER_ROOT),
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from our process group
            env={
                **os.environ,
                # fnm node binary; mirrors what their watchdog.sh prepends.
                "PATH": str(Path.home() / ".local/share/fnm/aliases/default/bin")
                + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            },
        )
    finally:
        log_fh.close()

    time.sleep(4)
    new_pids = _find_bot_pids()
    return {
        "killed_pids": list(killed),
        "spawn_pid": proc.pid,
        "new_pids": list(new_pids),
        "log_path": str(log_path),
    }
