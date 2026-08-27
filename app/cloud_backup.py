"""
BillBook — Google Drive Cloud Backup
=====================================

Uploads encrypted DB backups to the operator's own Google Drive.

DESIGN
------
- Uses OAuth (not a service account) so the operator owns their data
  in their own Google account — BillBook never sees their files.
- One-time browser-based consent flow produces a refresh_token stored
  encrypted (Fernet, same crypto as API keys) in the `settings` table.
- The refresh_token auto-rotates the access_token on each backup (Google
  access_tokens expire after 1 hour).
- Daily 2 AM backup runs as an APScheduler cron; also a manual button.
- Each backup is named `billbook-YYYY-MM-DD-HHMM.db` and lands in a
  dedicated Google Drive folder named "BillBook Backups" (created on
  first run; folder_id cached in settings).
- Backup retention: keeps the last 30 days, deletes older Drive files.
- Weekly restore-test: downloads the latest Drive backup, mounts it
  in a temp DB, runs `PRAGMA integrity_check`. Logs result. NEVER
  touches the live DB.

DEPENDENCIES
------------
- google-auth>=2.28
- google-api-python-client>=2.100

ENV
---
- GOOGLE_CLIENT_ID       (from Google Cloud Console > API credentials)
- GOOGLE_CLIENT_SECRET   (from same)
- REDIRECT_URI           (default http://localhost:8000/api/gdrive/callback)

The client_id/secret ship with the installer (read-only OAuth client;
client can revoke access at any time from their Google account).
"""
from __future__ import annotations
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, crypto, db

logger = logging.getLogger(__name__)

# Drive folder name for BillBook backups (created on first run)
DRIVE_FOLDER_NAME = "BillBook Backups"
RETENTION_DAYS = 30

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    # drive.file = only files created BY this app, not the user's whole Drive
]


# ─── OAuth flow ─────────────────────────────────────────────────────────────

def get_oauth_url(redirect_uri: str | None = None) -> str:
    """Build the Google OAuth consent URL for the operator to visit.

    BillBook requests drive.file scope — the operator grants the app
    permission to read/write ONLY files it creates. We never see their
    other Drive files.
    """
    from google_auth_oauthlib.flow import Flow

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars must be set. "
            "Get them from https://console.cloud.google.com/apis/credentials"
        )
    redir = redirect_uri or os.getenv(
        "GDRIVE_REDIRECT_URI", "http://localhost:8000/api/gdrive/callback"
    )
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redir],
            }
        },
        scopes=SCOPES,
        redirect_uri=redir,
    )
    url, _state = flow.authorization_url(
        access_type="offline",          # forces a refresh_token to be issued
        prompt="consent",               # always ask, even if already granted
        include_granted_scopes="true",
    )
    return url


def exchange_code_for_token(code: str, redirect_uri: str | None = None) -> dict:
    """Exchange the OAuth code returned by Google for a refresh_token + access_token.

    Stores the refresh_token encrypted in the settings table.
    """
    from google_auth_oauthlib.flow import Flow

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redir = redirect_uri or os.getenv(
        "GDRIVE_REDIRECT_URI", "http://localhost:8000/api/gdrive/callback"
    )
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redir],
            }
        },
        scopes=SCOPES,
        redirect_uri=redir,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    token_data = {
        "refresh_token": creds.refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": SCOPES,
    }
    # Encrypt + persist in settings. The refresh_token is a long-lived
    # credential — treat it like a password.
    encrypted = crypto.encrypt_value(json.dumps(token_data))
    db.set_setting("gdrive_refresh_token_enc", encrypted)
    db.set_setting("gdrive_connected_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"connected": True, "refresh_token": creds.refresh_token is not None}


def disconnect() -> None:
    """Forget the stored refresh_token. Operator can revoke access from
    their Google account at https://myaccount.google.com/permissions."""
    db.set_setting("gdrive_refresh_token_enc", "")
    db.set_setting("gdrive_connected_at", "")
    db.set_setting("gdrive_folder_id", "")
    db.set_setting("gdrive_last_backup_at", "")
    db.set_setting("gdrive_last_restore_test_at", "")
    db.set_setting("gdrive_last_restore_test_ok", "")


def is_connected() -> bool:
    return bool(db.get_setting("gdrive_refresh_token_enc", ""))


# ─── Auto-backup hour ───────────────────────────────────────────────────────
# v8.14.2: the operator picks the daily Drive backup hour in the setup wizard
# (or Settings). Scheduler in main.py reads this each tick. Default 2 = 2 AM.

def get_auto_backup_hour() -> int:
    """Return the hour (0-23 PKT) at which the daily Drive backup runs.

    Falls back to 2 (2 AM) if missing/invalid — never raises.
    """
    try:
        h = int(db.get_setting("gdrive_backup_hour", "2"))
        return h if 0 <= h <= 23 else 2
    except (TypeError, ValueError):
        return 2


def set_auto_backup_hour(hour: int) -> int:
    """Persist the daily Drive backup hour. Returns the stored value."""
    h = int(hour)
    if not (0 <= h <= 23):
        raise ValueError("hour must be 0-23")
    db.set_setting("gdrive_backup_hour", str(h))
    return h


# ─── Drive API client ────────────────────────────────────────────────────────

def _get_drive_service():
    """Build an authorized Drive service object using the stored refresh_token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    enc = db.get_setting("gdrive_refresh_token_enc", "")
    if not enc:
        raise RuntimeError("Google Drive not connected — call /api/gdrive/connect first")
    token_data = json.loads(crypto.decrypt_value(enc))
    creds = Credentials(
        token=None,                       # forces refresh
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"],
    )
    # Refresh to get a fresh access_token (Google access_tokens expire after 1h)
    from google.auth.transport.requests import Request
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, static_discovery=False)


def _get_or_create_folder_id(service) -> str:
    """Get the BillBook Backups folder id, creating it if it doesn't exist."""
    cached = db.get_setting("gdrive_folder_id", "")
    if cached:
        return cached
    # Look for an existing folder with our name in the user's My Drive root.
    results = service.files().list(
        q=f"name='{DRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    items = results.get("files", [])
    if items:
        folder_id = items[0]["id"]
    else:
        body = {"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
        created = service.files().create(body=body, fields="id").execute()
        folder_id = created["id"]
    db.set_setting("gdrive_folder_id", folder_id)
    return folder_id


# ─── Backup + retention ───────────────────────────────────────────────────────

def backup_now() -> dict:
    """Take a VACUUM INTO backup of the live DB, gzip it, upload to Drive.

    Returns {ok, file_id, file_name, size_mb}.
    """
    if not is_connected():
        raise RuntimeError("Google Drive not connected")
    from .maintenance import create_backup   # local-only import avoids cycle
    # Step 1: produce a local snapshot using the atomic VACUUM INTO path
    #         (already v8.13.4 — see app/routers/maintenance.py:create_backup)
    snap_path = create_backup(label="gdrive")
    if not snap_path or not Path(snap_path).exists():
        raise RuntimeError(f"Snapshot creation failed: {snap_path}")
    # Step 2: gzip the .db so we save Drive quota + transfer time
    # v8.14.1 FIX: previous version had a typo `fout.writelen = fin.read()`
    # that consumed the input stream BEFORE the next line — resulting in an
    # EMPTY gzip being uploaded to Drive. Now we just write the bytes once.
    gzip_path = str(snap_path) + ".gz"
    import gzip
    with open(snap_path, "rb") as fin, gzip.open(gzip_path, "wb") as fout:
        fout.write(fin.read())
    if Path(gzip_path).stat().st_size == 0:
        raise RuntimeError(
            "Backup gzip is empty (0 bytes) — refusing to upload a corrupt "
            "backup. Source DB likely empty or read failed."
        )
    size_mb = round(Path(gzip_path).stat().st_size / 1024 / 1024, 2)

    # Step 3: upload to Drive
    service = _get_drive_service()
    folder_id = _get_or_create_folder_id(service)
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    file_name = f"billbook-{ts}.db.gz"
    file_metadata = {"name": file_name, "parents": [folder_id]}
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(gzip_path, mimetype="application/gzip", resumable=True)
    uploaded = service.files().create(
        body=file_metadata, media_body=media, fields="id, name"
    ).execute()

    # Step 4: update settings + clean local snapshot
    db.set_setting(
        "gdrive_last_backup_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.set_setting("gdrive_last_backup_file", file_name)
    try:
        Path(snap_path).unlink(missing_ok=True)
        Path(gzip_path).unlink(missing_ok=True)
    except Exception:
        pass

    # Step 5: prune Drive backups older than RETENTION_DAYS
    _prune_old_backups(service, folder_id)

    return {
        "ok": True,
        "file_id": uploaded.get("id"),
        "file_name": file_name,
        "size_mb": size_mb,
    }


def _prune_old_backups(service, folder_id: str) -> int:
    """Delete Drive backups older than RETENTION_DAYS. Returns count."""
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    results = service.files().list(
        q=f"'{folder_id}' in parents and modifiedTime < '{cutoff}' and trashed=false",
        spaces="drive",
        fields="files(id, name, modifiedTime)",
    ).execute()
    deleted = 0
    for f in results.get("files", []):
        try:
            service.files().update(fileId=f["id"], body={"trashed": True}).execute()
            deleted += 1
            logger.info("Pruned old Drive backup %s (%s)", f["name"], f["modifiedTime"])
        except Exception as e:
            logger.warning("Failed to prune %s: %s", f["name"], e)
    return deleted


# ─── Weekly restore-test ─────────────────────────────────────────────────────

def restore_test() -> dict:
    """Download the latest Drive backup, mount it in a temp DB, verify integrity.

    NEVER touches the live DB. The test result is stored in settings for
    the Settings UI to display "Last restore-test: 2026-08-26 02:00 — OK".
    """
    import sqlite3 as _sqlite3
    import tempfile

    if not is_connected():
        raise RuntimeError("Google Drive not connected")
    service = _get_drive_service()
    folder_id = _get_or_create_folder_id(service)
    # Find the newest .gz in the folder
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        spaces="drive",
        orderBy="modifiedTime desc",
        pageSize=1,
        fields="files(id, name, modifiedTime)",
    ).execute()
    items = results.get("files", [])
    if not items:
        return {"ok": False, "error": "No backups in Drive yet"}
    latest = items[0]
    # Download
    request = service.files().get_media(fileId=latest["id"])
    buf = io.BytesIO()
    for chunk in request.stream():
        buf.write(chunk)
    buf.seek(0)
    # Gunzip
    import gzip
    decompressed = gzip.decompress(buf.read())
    # Mount in a temp file
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(decompressed)
        tmp_path = tmp.name
    try:
        conn = _sqlite3.connect(tmp_path)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        ok = (result[0] == "ok")
        # Record the test result
        db.set_setting(
            "gdrive_last_restore_test_at",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.set_setting("gdrive_last_restore_test_ok", "1" if ok else "0")
        db.set_setting(
            "gdrive_last_restore_test_file", latest["name"]
        )
        if not ok:
            db.log_activity("gdrive_restore_test", "backup", None,
                            f"Restore-test FAILED: {result[0]}",
                            {"file": latest["name"]})
        return {
            "ok": ok,
            "integrity_check": result[0],
            "file_name": latest["name"],
            "file_modified_at": latest["modifiedTime"],
        }
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─── Status endpoint helper ──────────────────────────────────────────────────

def get_status() -> dict:
    """Return current backup status for the Settings UI."""
    return {
        "connected": is_connected(),
        "connected_at": db.get_setting("gdrive_connected_at", ""),
        "folder_id": db.get_setting("gdrive_folder_id", ""),
        "last_backup_at": db.get_setting("gdrive_last_backup_at", ""),
        "last_backup_file": db.get_setting("gdrive_last_backup_file", ""),
        "last_restore_test_at": db.get_setting("gdrive_last_restore_test_at", ""),
        "last_restore_test_ok": db.get_setting("gdrive_last_restore_test_ok", "") == "1",
        "last_restore_test_file": db.get_setting("gdrive_last_restore_test_file", ""),
        "retention_days": RETENTION_DAYS,
        "folder_name": DRIVE_FOLDER_NAME,
        # v8.14.2: daily auto-backup hour (0-23 PKT). Drives the scheduler
        # in main.py. Set via setup wizard or Settings > Cloud Backup.
        "auto_backup_hour": get_auto_backup_hour(),
    }
