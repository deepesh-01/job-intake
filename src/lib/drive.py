"""Google Drive uploader for tailored PDFs.

Uploads each PDF to a Drive folder and returns a shareable view URL.
Two credential modes:

  - **OAuth user delegation (preferred)** — files are owned BY the user,
    use the user's Drive quota. Avoids the "service accounts have no
    storage quota" issue. Token JSON at $GOOGLE_OAUTH_TOKEN_PATH.

  - **Service-account fallback** — only works if the target folder is
    inside a Google Workspace Shared Drive (where files use the shared
    drive's quota, not the SA's). Will fail with `storageQuotaExceeded`
    on a regular folder owned by a personal Gmail account.

The processor chooses the mode at startup based on which env vars are
set (see lib.config and DriveClient.from_env).
"""
from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials as UserCreds
from google.oauth2.service_account import Credentials as SACreds
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveClient:
    """Thin wrapper around the Drive v3 API."""

    def __init__(self, creds):
        self._svc = build("drive", "v3", credentials=creds, cache_discovery=False)

    @classmethod
    def from_oauth_token(cls, token_path: Path) -> "DriveClient":
        """Load OAuth user credentials from a refresh-token JSON. Auto-refreshes."""
        if not token_path.is_file():
            raise FileNotFoundError(
                f"OAuth token not found at {token_path}. "
                "Run `uv run python scripts/auth_drive.py` first."
            )
        creds = UserCreds.from_authorized_user_file(str(token_path), _SCOPES)
        return cls(creds)

    @classmethod
    def from_service_account(cls, sa_path: Path) -> "DriveClient":
        """Load SA credentials. Only works for Workspace Shared Drives."""
        creds = SACreds.from_service_account_file(str(sa_path), scopes=_SCOPES)
        return cls(creds)

    def upload_pdf(
        self,
        local_path: Path,
        folder_id: str,
        display_name: str,
    ) -> str:
        """Upload `local_path` into `folder_id`. Make it readable via link.
        Returns the shareable web URL."""
        media = MediaFileUpload(
            str(local_path),
            mimetype="application/pdf",
            resumable=False,
        )
        metadata = {
            "name": display_name,
            "parents": [folder_id],
        }
        created = (
            self._svc.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        file_id = created["id"]

        # Make the file readable to anyone with the link (private to the
        # link — not indexed). Lets the user open from phone/laptop without
        # explicit per-file sharing.
        self._svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()

        return created.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
