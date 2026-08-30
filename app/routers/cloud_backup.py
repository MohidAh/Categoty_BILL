"""Cloud backup (Google Drive) API endpoints.

All endpoints are manager-only (/api/gdrive is in CASHIER_RESTRICTED_PREFIXES
in main.py — the single RBAC source of truth).

v8.14.2: Added a GET form of the OAuth callback so Google's redirect
actually works (the original POST-only callback never received the
browser GET that Google sends after consent). Also added a small
thank-you redirect page so the operator sees confirmation in the popup
they used for OAuth.

v8.18.0: POS backup auto-import endpoints — the Settings > Google Drive
page toggles the folder watcher, shows its status, and can trigger an
immediate check ("Check Now" button).
"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from .. import cloud_backup, security

router = APIRouter()


class GDriveCallbackIn(BaseModel):
    code: str
    redirect_uri: str | None = None


class GDriveAutoBackupIn(BaseModel):
    hour: int  # 0-23 PKT


class GDriveAutoImportIn(BaseModel):
    enabled: bool


@router.get("/api/gdrive/status")
def get_gdrive_status() -> Any:
    """Return current Google Drive backup configuration + last backup status.

    v8.14.2: now also returns `auto_backup_hour` (0-23 PKT) so the Settings
    UI and the setup wizard can show / update the daily Drive backup hour.
    """
    return cloud_backup.get_status()


@router.get("/api/gdrive/connect-url")
def get_connect_url() -> Any:
    """Get the OAuth consent URL the operator visits in their browser."""
    try:
        url = cloud_backup.get_oauth_url()
        return {"url": url}
    except RuntimeError as e:
        raise HTTPException(500, str(e))


# v8.14.2: GET form of the OAuth callback. Google redirects here with
# ?code=XYZ after the operator grants consent. We exchange the code,
# persist the encrypted refresh_token, then redirect the browser to a
# small static "Connected" page so the popup the operator opened shows
# a confirmation instead of raw JSON.
#
# The POST form is kept for backward compat (any API client that posts
# JSON {code} still works).
@router.get("/api/gdrive/callback")
def gdrive_callback_get(request: Request) -> Any:
    code = request.query_params.get("code", "").strip()
    if not code:
        # Google also calls us with ?error=access_denied if the user cancels
        err = request.query_params.get("error", "missing code")
        return RedirectResponse(f"/static/gdrive-callback.html?status=error&reason={err}", status_code=302)
    try:
        cloud_backup.exchange_code_for_token(code, None)
        return RedirectResponse("/static/gdrive-callback.html?status=ok", status_code=302)
    except Exception as e:
        reason = str(e)[:200]
        return RedirectResponse(f"/static/gdrive-callback.html?status=error&reason={reason}", status_code=302)


@router.post("/api/gdrive/callback")
def gdrive_callback(payload: GDriveCallbackIn) -> Any:
    """JSON form of the OAuth callback (kept for backward compat).

    Google redirects here with ?code=XYZ after operator grants consent.
    We exchange the code for a refresh_token + persist it encrypted.
    """
    try:
        return cloud_backup.exchange_code_for_token(payload.code, payload.redirect_uri)
    except Exception as e:
        raise HTTPException(400, f"OAuth exchange failed: {e}")


@router.post("/api/gdrive/disconnect")
def gdrive_disconnect() -> Any:
    """Forget the stored refresh_token. Operator can also revoke access at
    https://myaccount.google.com/permissions — we recommend both."""
    cloud_backup.disconnect()
    # v8.18.0: also forget auto-import state so a future reconnect starts clean.
    cloud_backup.set_auto_import_enabled(False)
    return {"ok": True}


@router.post("/api/gdrive/backup-now")
def gdrive_backup_now() -> Any:
    """Manual backup trigger. The scheduler calls the same code at the
    operator-chosen hour (default 2 AM PKT, configurable)."""
    try:
        return cloud_backup.backup_now()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Backup failed: {e}")


@router.post("/api/gdrive/restore-test")
def gdrive_restore_test() -> Any:
    """Download the latest Drive backup, verify integrity in a temp DB.
    Never touches the live DB."""
    try:
        return cloud_backup.restore_test()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Restore-test failed: {e}")


# v8.14.2: operator-chosen daily auto-backup hour.
# Setup wizard calls this when the user opts in to GDrive and picks a time.
# Settings UI calls it when the user changes the dropdown.
@router.post("/api/gdrive/auto-backup")
def set_gdrive_auto_backup(payload: GDriveAutoBackupIn) -> Any:
    """Set the daily Drive backup hour (0-23 PKT).

    Safe to call even before OAuth is complete — the hour is persisted and
    will be used by the scheduler once the operator finishes the OAuth flow.
    """
    if not (0 <= payload.hour <= 23):
        raise HTTPException(400, "hour must be 0-23")
    stored = cloud_backup.set_auto_backup_hour(payload.hour)
    return {"ok": True, "hour": stored}


# ─── v8.18.0: POS backup auto-import ─────────────────────────────────────────

@router.get("/api/gdrive/auto-import")
def get_auto_import_status() -> Any:
    """Auto-import status for the Settings > Google Drive page: enabled flag,
    last check time, last result summary, and the recent processed files."""
    return cloud_backup.get_auto_import_status()


@router.post("/api/gdrive/auto-import")
def set_auto_import(payload: GDriveAutoImportIn) -> Any:
    """Enable/disable the Drive folder watcher (checkbox in Settings).

    Safe to toggle before OAuth is complete — the preference is persisted
    and takes effect once the operator connects their Google account.
    """
    enabled = cloud_backup.set_auto_import_enabled(payload.enabled)
    return {"ok": True, "enabled": enabled}


@router.post("/api/gdrive/auto-import/check")
def auto_import_check_now() -> Any:
    """Run a folder check RIGHT NOW (the "Check Now" button) instead of
    waiting for the next scheduler tick. Returns the import summary."""
    return cloud_backup.check_and_import_new_backups(force=True)
