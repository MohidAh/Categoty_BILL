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
    # v8.18.0: read-only access so the POS auto-importer can also see backup
    # zips uploaded to the "BillBook POS Imports" folder manually (from a
    # phone or any device via drive.google.com). drive.file alone only
    # exposes files created by THIS app. Existing connections keep working
    # (app-created files only) — reconnect once to enable manual uploads.
    "https://www.googleapis.com/auth/drive.readonly",
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


def _get_or_create_folder(service, name: str, setting_key: str) -> str:
    """Get a folder id by name in My Drive root, creating it if needed.

    v8.18.0: generalized from the old backups-only helper so the POS
    auto-importer can maintain its own "BillBook POS Imports" folder.
    """
    cached = db.get_setting(setting_key, "")
    if cached:
        return cached
    # Look for an existing folder with our name in the user's My Drive root.
    results = service.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    items = results.get("files", [])
    if items:
        folder_id = items[0]["id"]
    else:
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        created = service.files().create(body=body, fields="id").execute()
        folder_id = created["id"]
    db.set_setting(setting_key, folder_id)
    return folder_id


def _get_or_create_folder_id(service) -> str:
    """Get the BillBook Backups folder id, creating it if it doesn't exist."""
    return _get_or_create_folder(service, DRIVE_FOLDER_NAME, "gdrive_folder_id")


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
    # v8.18.0 FIX: request.stream() does not exist in googleapiclient — this
    # used to throw AttributeError, which the scheduler swallowed, so the
    # weekly restore-test silently never ran. MediaIoBaseDownload is the
    # documented download API.
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=latest["id"])
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
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


# ─── POS backup auto-import (v8.18.0) ────────────────────────────────────────
#
# USE CASE: the shop's Ezi POS machine (or any device — phone, laptop) drops
# a POS backup zip (BU*.zip) into the "BillBook POS Imports" folder in the
# operator's Google Drive. BillBook notices it within ~15 minutes, downloads
# it and runs the SAME idempotent import pipeline as the manual upload page
# (app/routers/pos_import_router.py -> pos_import_sync.import_pos_backup),
# so UNQCODE dedup applies — re-importing the same or an overlapping backup
# never duplicates data.
#
# FLOW: list .zip files in the watched folder -> skip already-processed
# (tracked by Drive file id in settings) -> validate ZIP magic bytes ->
# import -> on SUCCESS move the Drive file into an "Processed" subfolder so
# the watched folder stays clean for humans -> record the result.
#
# Scope note: with only the legacy drive.file scope the app sees files IT
# created (e.g. backups pushed by another BillBook device with the same
# client id). Files uploaded manually via drive.google.com require the
# drive.readonly scope added in v8.18.0 — get_auto_import_status() reports
# `needs_reconnect` when the stored token predates it.

AUTO_IMPORT_FOLDER_NAME = "BillBook POS Imports"
AUTO_IMPORT_PROCESSED_SUBFOLDER = "Processed"
AUTO_IMPORT_CHECK_INTERVAL_MIN = 15     # scheduler re-check cadence
AUTO_IMPORT_MAX_FILE_MB = 100           # same cap as the manual upload page
AUTO_IMPORT_MAX_FILES_PER_RUN = 5       # be gentle: a few per check
AUTO_IMPORT_PROCESSED_KEEP = 200        # remembered file ids (rolling)


def is_auto_import_enabled() -> bool:
    """True when the Drive folder watcher should run (setting, default off)."""
    return db.get_setting("gdrive_autoimport_enabled", "0") == "1"


def set_auto_import_enabled(on: bool) -> bool:
    db.set_setting("gdrive_autoimport_enabled", "1" if on else "0")
    return is_auto_import_enabled()


def _get_processed_files() -> dict:
    try:
        return json.loads(db.get_setting("gdrive_autoimport_processed", "{}") or "{}")
    except (ValueError, TypeError):
        return {}


def _set_processed_files(d: dict) -> None:
    # Keep the newest N entries only (drop oldest by timestamp).
    if len(d) > AUTO_IMPORT_PROCESSED_KEEP:
        keep = sorted(d.items(), key=lambda kv: kv[1].get("at", ""), reverse=True)
        d = dict(keep[: AUTO_IMPORT_PROCESSED_KEEP])
    db.set_setting("gdrive_autoimport_processed", json.dumps(d))


def _stored_token_has_readonly() -> bool:
    """True if the stored refresh token was granted the drive.readonly scope."""
    enc = db.get_setting("gdrive_refresh_token_enc", "")
    if not enc:
        return False
    try:
        return "drive.readonly" in (json.loads(crypto.decrypt_value(enc)).get("scopes") or [])
    except Exception:
        return False


def get_auto_import_status() -> dict:
    """Status block for the Settings > Google Drive page."""
    processed = _get_processed_files()
    recent = sorted(
        processed.values(), key=lambda v: v.get("at", ""), reverse=True
    )[:5]
    last_result = {}
    try:
        last_result = json.loads(db.get_setting("gdrive_autoimport_last_result", "{}") or "{}")
    except (ValueError, TypeError):
        pass
    return {
        "enabled": is_auto_import_enabled(),
        "connected": is_connected(),
        "folder_name": AUTO_IMPORT_FOLDER_NAME,
        "check_interval_min": AUTO_IMPORT_CHECK_INTERVAL_MIN,
        "last_check_at": db.get_setting("gdrive_autoimport_last_check", ""),
        "last_result": last_result,
        "processed_count": len(processed),
        "recent": recent,
        # True when the operator should reconnect to grant the read scope
        # needed to see manually-uploaded files (v8.18.0).
        "needs_reconnect": is_connected() and not _stored_token_has_readonly(),
    }


def check_and_import_new_backups(force: bool = False) -> dict:
    """Scan the watched Drive folder and import any new POS backup zips.

    Called by the scheduler every ~15 min and by the "Check Now" button
    (force=True). Never raises — errors are returned in the result dict so
    a background tick can never take the app down.
    """
    now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "ok": True, "checked": 0, "imported_files": 0,
        "imported_sales": 0, "imported_expenses": 0,
        "skipped_duplicates": 0, "errors": [],
    }
    if not is_connected():
        result["ok"] = False
        result["errors"] = ["Google Drive not connected"]
        return result
    if not force and not is_auto_import_enabled():
        result["ok"] = False
        result["errors"] = ["Auto-import is disabled"]
        return result
    # Same dependency check as the manual upload endpoint.
    try:
        from .pos_import_sync import HAS_DBFREAD, import_pos_backup
    except ImportError as e:
        result["ok"] = False
        result["errors"] = [f"Import module unavailable: {e}"]
        return result
    if not HAS_DBFREAD:
        result["ok"] = False
        result["errors"] = ["dbfread library not installed (pip install dbfread)"]
        return result

    try:
        service = _get_drive_service()
        folder_id = _get_or_create_folder(
            service, AUTO_IMPORT_FOLDER_NAME, "gdrive_autoimport_folder_id"
        )
        # List zips in the watched folder (newest first).
        listing = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false and name contains '.zip'",
            spaces="drive",
            orderBy="modifiedTime desc",
            pageSize=50,
            fields="files(id, name, size, modifiedTime)",
        ).execute()
        files = [
            f for f in listing.get("files", [])
            if (f.get("name") or "").lower().endswith(".zip")
        ]
        processed = _get_processed_files()
        fresh = [f for f in files if f["id"] not in processed]
        result["checked"] = len(fresh)

        for f in fresh[: AUTO_IMPORT_MAX_FILES_PER_RUN]:
            name = f.get("name") or "unknown.zip"
            try:
                size_mb = (int(f.get("size") or 0)) / 1024 / 1024
                if size_mb > AUTO_IMPORT_MAX_FILE_MB:
                    processed[f["id"]] = {
                        "name": name, "at": now_s, "ok": False,
                        "error": f"too large ({size_mb:.1f} MB > {AUTO_IMPORT_MAX_FILE_MB} MB cap)",
                    }
                    result["errors"].append(f"{name}: too large, skipped")
                    continue
                # Download to a temp file (import_pos_backup takes a path).
                # v8.18.0: MediaIoBaseDownload is the ONLY correct download
                # API — the old request.stream() pattern used by
                # restore_test() does not exist in googleapiclient and
                # throws AttributeError (fixed below).
                import tempfile
                from googleapiclient.http import MediaIoBaseDownload
                request = service.files().get_media(fileId=f["id"])
                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                data = buf.getvalue()
                # SECURITY: same magic-byte validation as the manual upload
                # path (v8.13.1) — a Drive file named .zip must BE a zip.
                if not data.startswith(b"PK\x03\x04") and not data.startswith(b"PK\x05\x06"):
                    processed[f["id"]] = {
                        "name": name, "at": now_s, "ok": False,
                        "error": "not a valid zip (bad magic bytes)",
                    }
                    result["errors"].append(f"{name}: not a valid zip")
                    continue
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name
                try:
                    r = import_pos_backup(tmp_path)
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
                processed[f["id"]] = {
                    "name": name, "at": now_s, "ok": True,
                    "sales": r.get("imported_sales", 0),
                    "expenses": r.get("imported_expenses", 0),
                    "skipped": r.get("skipped_duplicates", 0),
                    "run_id": r.get("import_run_id"),
                }
                result["imported_files"] += 1
                result["imported_sales"] += r.get("imported_sales", 0) or 0
                result["imported_expenses"] += r.get("imported_expenses", 0) or 0
                result["skipped_duplicates"] += r.get("skipped_duplicates", 0) or 0
                db.log_activity(
                    "gdrive_autoimport", "pos_import", r.get("import_run_id"),
                    f"Auto-imported {name} from Google Drive", {
                        "sales": r.get("imported_sales", 0),
                        "expenses": r.get("imported_expenses", 0),
                        "skipped": r.get("skipped_duplicates", 0),
                    },
                )
                logger.info("Drive auto-import: %s -> %s sales", name, r.get("imported_sales", 0))
                # Move to the "Processed" subfolder so the watched folder
                # stays clean and humans can see what already ran.
                try:
                    sub = _find_or_create_subfolder(service, folder_id, AUTO_IMPORT_PROCESSED_SUBFOLDER)
                    service.files().update(
                        fileId=f["id"], addParents=sub, removeParents=folder_id, fields="id"
                    ).execute()
                except Exception as move_err:
                    # Non-fatal: file stays in place but is tracked above.
                    logger.warning("Drive auto-import: could not move %s: %s", name, move_err)
            except Exception as e:
                processed[f["id"]] = {
                    "name": name, "at": now_s, "ok": False, "error": str(e)[:300],
                }
                result["errors"].append(f"{name}: {str(e)[:120]}")
                logger.warning("Drive auto-import failed for %s: %s", name, e)

        _set_processed_files(processed)
        db.set_setting("gdrive_autoimport_last_check", now_s)
        db.set_setting("gdrive_autoimport_last_result", json.dumps(result))
        return result
    except Exception as e:
        # Most common cause: 403 from Google because the stored token only
        # has drive.file scope and the folder contains manually-uploaded
        # files the app cannot see.
        msg = str(e)
        result["ok"] = False
        hint = ""
        if isinstance(msg, str) and ("403" in msg or "insufficientPermissions" in msg):
            hint = (" Google Drive denied listing the folder. If you connected "
                    "before v8.18.0, disconnect and re-connect once so BillBook "
                    "gets the new read permission.")
        result["errors"] = [msg[:300] + hint]
        db.set_setting("gdrive_autoimport_last_check", now_s)
        db.set_setting("gdrive_autoimport_last_result", json.dumps(result))
        logger.warning("Drive auto-import check failed: %s", e)
        return result


def _find_or_create_subfolder(service, parent_id: str, name: str) -> str:
    """Find a child folder by name (or create it) inside parent_id."""
    results = service.files().list(
        q=f"'{parent_id}' in parents and name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    items = results.get("files", [])
    if items:
        return items[0]["id"]
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = service.files().create(body=body, fields="id").execute()
    return created["id"]
