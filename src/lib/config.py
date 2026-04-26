from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


_REQUIRED_ENV = [
    "GOOGLE_SHEETS_CREDS_PATH",
    "SHEET_ID",
    "DATA_DIR",
    "TAG_RULES_PATH",
    "BOARDS_PATH",
    "EXCLUDE_PATH",
    "SYSTEM_A_PATH",
    "SYSTEM_A_BASE_RESUME",
    "SYSTEM_A_USER_CHAT_ID",
]


@dataclass(frozen=True)
class Env:
    creds_path: Path
    sheet_id: str
    data_dir: Path
    tag_rules_path: Path
    boards_path: Path
    exclude_path: Path
    system_a_path: Path
    system_a_base_resume: Path
    system_a_user_chat_id: int
    drive_folder_id: str | None  # if set, processor uploads PDFs to Drive
    drive_oauth_client_path: Path | None  # OAuth client_secret JSON (one-time)
    drive_oauth_token_path: Path | None   # OAuth refresh-token JSON (auto-managed)
    tailor_dry_run: bool
    log_level: str


def load_env() -> Env:
    load_dotenv()
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")

    creds = Path(os.environ["GOOGLE_SHEETS_CREDS_PATH"]).expanduser()
    if not creds.is_file():
        raise RuntimeError(f"GOOGLE_SHEETS_CREDS_PATH does not exist: {creds}")

    data_dir = Path(os.environ["DATA_DIR"]).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "jds").mkdir(exist_ok=True)
    (data_dir / "tailored").mkdir(exist_ok=True)
    (data_dir / "locks").mkdir(exist_ok=True)

    tag_rules = Path(os.environ["TAG_RULES_PATH"]).expanduser()
    boards = Path(os.environ["BOARDS_PATH"]).expanduser()
    exclude = Path(os.environ["EXCLUDE_PATH"]).expanduser()
    for p in (tag_rules, boards, exclude):
        if not p.is_file():
            raise RuntimeError(f"config file missing: {p}")

    chat_id_raw = os.environ["SYSTEM_A_USER_CHAT_ID"]
    try:
        chat_id = int(chat_id_raw)
    except ValueError as e:
        raise RuntimeError(f"SYSTEM_A_USER_CHAT_ID must be int: {chat_id_raw!r}") from e

    drive_folder = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
    oauth_client = os.environ.get("GOOGLE_OAUTH_CLIENT_PATH", "").strip()
    oauth_token = os.environ.get("GOOGLE_OAUTH_TOKEN_PATH", "").strip()
    oauth_client_path = Path(oauth_client).expanduser() if oauth_client else None
    oauth_token_path = Path(oauth_token).expanduser() if oauth_token else None

    return Env(
        creds_path=creds,
        sheet_id=os.environ["SHEET_ID"],
        data_dir=data_dir,
        tag_rules_path=tag_rules,
        boards_path=boards,
        exclude_path=exclude,
        system_a_path=Path(os.environ["SYSTEM_A_PATH"]).expanduser(),
        system_a_base_resume=Path(os.environ["SYSTEM_A_BASE_RESUME"]).expanduser(),
        system_a_user_chat_id=chat_id,
        drive_folder_id=drive_folder,
        drive_oauth_client_path=oauth_client_path,
        drive_oauth_token_path=oauth_token_path,
        tailor_dry_run=os.environ.get("TAILOR_DRY_RUN", "false").lower() == "true",
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)
