"""One-time OAuth authorization for Drive uploads.

Opens a browser, you pick your Google account + click Allow, and a
refresh token is saved to $GOOGLE_OAUTH_TOKEN_PATH. Subsequent processor
runs read that token and auto-refresh — you never need to repeat this.

Prereqs:
- GCP OAuth Client ID JSON (Desktop app type) at $GOOGLE_OAUTH_CLIENT_PATH
- $GOOGLE_OAUTH_TOKEN_PATH set to where the token should be written
- Drive folder shared with your Google account (you own it, so this is
  automatic if you created it)

Usage:
    uv run python scripts/auth_drive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from lib.config import load_env  # noqa: E402
from lib.logging import configure  # noqa: E402

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main() -> int:
    env = load_env()
    log = configure(env.log_level)

    if not env.drive_oauth_client_path:
        print("ERROR: GOOGLE_OAUTH_CLIENT_PATH is not set in .env", file=sys.stderr)
        return 2
    if not env.drive_oauth_token_path:
        print("ERROR: GOOGLE_OAUTH_TOKEN_PATH is not set in .env", file=sys.stderr)
        return 2
    if not env.drive_oauth_client_path.is_file():
        print(
            f"ERROR: client JSON not found at {env.drive_oauth_client_path}\n"
            "Download it from GCP Console → Credentials → your OAuth client → Download JSON",
            file=sys.stderr,
        )
        return 2

    print(f"Loading OAuth client from {env.drive_oauth_client_path}")
    print("Opening browser — pick your Google account and click Allow.\n")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(env.drive_oauth_client_path), _SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)

    env.drive_oauth_token_path.parent.mkdir(parents=True, exist_ok=True)
    env.drive_oauth_token_path.write_text(creds.to_json())
    env.drive_oauth_token_path.chmod(0o600)

    log.info("drive_oauth_token_saved", path=str(env.drive_oauth_token_path))
    print(f"\nOK — refresh token saved to {env.drive_oauth_token_path}")
    print("From now on the processor will upload PDFs as you (your Drive quota).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
