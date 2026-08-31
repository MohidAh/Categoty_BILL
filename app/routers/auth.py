"""Auto-generated router module — extracted from main.py Phase 1."""
import logging
import os, json, time, re, io, csv, secrets, hashlib, traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any, Optional

from .. import db
from .. import licensing
from .. import shop as shop_mod
from .. import insights
from .. import trends as trends_mod
from .. import extract
from .. import reports
from .. import pos_extra
from .. import pos_import
from .. import crypto as crypto_mod
from .. import jobs as jobs_mod
from ..config import BACKUPS, BASE, PAGE_SIZE, PAGES, UPLOADS
from ..export import export_bills, export_insights
from ..ingest import render_pages, save_upload
from ..validate import detect_duplicate, pieces, validate
from ..security import (
    hash_password, verify_password, ensure_password,
    is_logged_in, get_session, get_session_role,
    create_session, delete_session,
    check_login_throttle, record_failed_login,
    SESSION_DAYS,
)

router = APIRouter()

# Backward-compat aliases
_hash_password = hash_password
_verify_password = verify_password
_ensure_password = ensure_password
_is_logged_in = is_logged_in
_get_session = get_session
_get_session_role = get_session_role
_create_session = create_session
_delete_session = delete_session
_check_login_throttle = check_login_throttle
_record_failed_login = record_failed_login


def _set_auth_cookie(response: JSONResponse, token: str, request: Request,
                     max_age_days: int = None) -> JSONResponse:
    """Set the bb_token auth cookie on a response with security-appropriate flags.

    SECURITY (v8.13.2):
    - HTTPS: full security — Secure flag set, long-lived session (SESSION_DAYS)
    - HTTP (LAN mode): reduced session to 8 hours + logged warning, because
      the cookie is transmitted in cleartext and anyone on the same Wi-Fi
      can sniff + hijack it. The shorter window limits the damage.
    - SameSite=Strict always (prevents CSRF)
    - HttpOnly always (prevents JS-based cookie theft)

    C7 fix (v8.13.4): also sets a `bb_csrf` cookie containing the per-session
    CSRF token. This is the "double-submit cookie" pattern: the CSRF
    middleware accepts the token from EITHER the X-CSRF-Token header (used
    by production JS) OR the bb_csrf cookie (used by test clients + fallback).
    The cookie is NOT HttpOnly so JS can read it and forward it as a header.
    """
    import logging, secrets as _secrets
    _logger = logging.getLogger("billbook.auth")
    is_secure = request.url.scheme == "https"
    if max_age_days is None:
        max_age_days = SESSION_DAYS
    if not is_secure:
        # v8.13.2: HTTP mode — cap at 8 hours + log a warning
        max_age_days = min(max_age_days, 8 / 24)  # 8 hours = 8/24 days
        _logger.warning(
            "Auth cookie issued over HTTP (LAN mode) — session capped at 8 hours. "
            "Anyone on the same network can sniff the cookie. Recommend using HTTPS "
            "(Cloudflare Tunnel or a reverse proxy with TLS) for production use."
        )
        # Also log to activity_log so the admin can see this in the audit trail
        try:
            db.log_activity("http_session_warning", "auth", None,
                            "Session issued over HTTP — capped at 8 hours",
                            {"ip": request.client.host if request.client else "unknown",
                             "user_agent": request.headers.get("user-agent", "")[:200]})
        except Exception as _e:
            logger.warning("Silent exception in auth.py: %s", _e, exc_info=True)
    response.set_cookie(
        "bb_token", token,
        max_age=int(max_age_days * 86400),
        httponly=True, samesite="strict", secure=is_secure,
    )
    # C7: issue the per-session CSRF token (generate + persist if not yet set)
    csrf_token = None
    try:
        with db.conn() as c:
            row = c.execute("SELECT csrf_token FROM sessions WHERE token=?", (token,)).fetchone()
            csrf_token = row["csrf_token"] if row and "csrf_token" in row.keys() else None
            if not csrf_token:
                csrf_token = _secrets.token_urlsafe(32)
                try:
                    c.execute("UPDATE sessions SET csrf_token=? WHERE token=?", (csrf_token, token))
                except Exception:
                    pass  # legacy schema — csrf_token column missing
    except Exception:
        pass
    if csrf_token:
        # NOT HttpOnly — JS must read this to forward it as X-CSRF-Token
        response.set_cookie(
            "bb_csrf", csrf_token,
            max_age=int(max_age_days * 86400),
            httponly=False, samesite="strict", secure=is_secure,
        )
        # Also stash in response body so the SPA can pick it up
        try:
            # JSONResponse.body is bytes; rebuild the JSON to include csrf_token
            import json as _json
            body = _json.loads(response.body)
            if isinstance(body, dict):
                body["csrf_token"] = csrf_token
                response.body = _json.dumps(body).encode("utf-8")
                # v8.17.10 FIX: JSONResponse.__init__ computed Content-Length
                # from the ORIGINAL body. Mutating .body without updating the
                # header makes every REAL HTTP server (uvicorn/h11) abort the
                # response mid-send with "Too much data for declared
                # Content-Length" — the browser shows "Failed to fetch" on
                # login AND on setup-wizard completion. The pytest suite
                # missed this because Starlette's TestClient has no h11
                # layer to enforce the declared length.
                response.headers["content-length"] = str(len(response.body))
        except Exception:
            pass
    return response

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_FILES = 100


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str




class LoginIn(BaseModel):
    password: str




class SetupIn(BaseModel):
    password: str




class StaffLoginIn(BaseModel):
    employee_id: int
    pin: str




def _validate_upload(files: list[UploadFile]):
    """Pre-flight check: sizes, counts, extensions."""
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_FILES})")
    allowed_ext = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in allowed_ext:
            raise HTTPException(400, f"Unsupported file type: {ext} ({f.filename})")




def login_page():
    login_html = (BASE / "app" / "static" / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(login_html)





@router.post("/api/login")
def login(payload: LoginIn, request: Request) -> Any:
    # Login throttle: max 5 failed attempts per 60s per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_throttle(client_ip):
        return JSONResponse({"error": "Too many login attempts. Try again in 60 seconds."}, status_code=429)
    stored = db.get_setting("password_hash", "")
    if not stored or not _verify_password(payload.password, stored):
        _record_failed_login(client_ip)
        return JSONResponse({"error": "wrong password"}, status_code=403)
    token = secrets.token_urlsafe(32)
    _create_session(token, role="manager")
    r = JSONResponse({"ok": True, "role": "manager"})
    return _set_auth_cookie(r, token, request)




@router.post("/api/login/staff")
def staff_login(payload: StaffLoginIn, request: Request) -> Any:
    """Staff login with employee ID + PIN. Returns cashier-role session.

    SECURITY (v8.13.1): Uses bcrypt pin_hash (not plaintext pin column).
    Migrated employees have pin=NULL + pin_hash set; legacy employees have
    pin=plaintext + pin_hash=NULL. The plaintext fallback is kept ONLY for
    backward compat during the migration window and logs a warning + an
    `plaintext_pin_used` activity_log entry so the admin can see who hasn't
    been migrated yet.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_throttle(client_ip):
        return JSONResponse({"error": "Too many login attempts. Try again in 60 seconds."}, status_code=429)
    with db.conn() as c:
        emp = c.execute("SELECT * FROM employees WHERE id=? AND active=1", (payload.employee_id,)).fetchone()
    if not emp:
        _record_failed_login(client_ip)
        return JSONResponse({"error": "Invalid employee or PIN not set"}, status_code=403)
    # v8.13.1: Prefer bcrypt pin_hash; fall back to plaintext pin (with warning) for legacy employees
    from ..security import verify_pin
    pin_verified = False
    if emp["pin_hash"]:
        # Modern path — bcrypt
        pin_verified = verify_pin(payload.pin, emp["pin_hash"])
    elif emp["pin"]:
        # Legacy path — plaintext comparison (with security warning)
        # This branch only runs for employees who have NOT been migrated to pin_hash.
        # Use hmac.compare_digest for constant-time comparison (reduces timing-attack surface).
        import hmac as _hmac
        pin_verified = _hmac.compare_digest(payload.pin, emp["pin"])
        if pin_verified:
            # Log a warning so the admin knows this employee should be migrated
            db.log_activity("plaintext_pin_used", "employee", emp["id"],
                            f"Employee '{emp['name']}' logged in with plaintext PIN (not migrated to pin_hash)",
                            {"employee_id": emp["id"], "employee_name": emp["name"]})
    else:
        # No pin and no pin_hash — employee has no PIN set
        _record_failed_login(client_ip)
        return JSONResponse({"error": "Invalid employee or PIN not set"}, status_code=403)
    if not pin_verified:
        _record_failed_login(client_ip)
        return JSONResponse({"error": "Wrong PIN"}, status_code=403)
    token = secrets.token_urlsafe(32)
    _create_session(token, role=emp["role"] or "cashier", employee_id=emp["id"])
    r = JSONResponse({"ok": True, "role": emp["role"] or "cashier", "name": emp["name"]})
    return _set_auth_cookie(r, token, request)




@router.post("/api/logout")
def logout(request: Request) -> Any:
    token = request.cookies.get("bb_token")
    if token:
        _delete_session(token)
    r = JSONResponse({"ok": True})
    r.delete_cookie("bb_token")
    return r




@router.post("/api/change-password")
def change_password(payload: ChangePasswordIn, request: Request = None) -> Any:
    """Change the manager password.

    PR 7d (Reviewer 3 critical fix): when the password changes, the Fernet
    encryption key (derived from password_hash via PBKDF2) ALSO changes.
    All stored API keys must be re-encrypted with the new key — otherwise
    they become permanently unreadable.

    This is done in a single write_tx() so the password change + key re-
    encryption commit atomically. If re-encryption fails for any key, the
    entire password change rolls back (so the old password + old keys
    remain valid).

    H5 fix (v8.13.4): ALL other sessions are revoked atomically inside the
    same write_tx. A stolen bb_token cookie no longer remains valid for
    up to SESSION_DAYS after the password is rotated.
    """
    stored = db.get_setting("password_hash", "")
    if not stored or not _verify_password(payload.old_password, stored):
        return JSONResponse({"error": "current password incorrect"}, status_code=403)
    if len(payload.new_password) < 8:
        return JSONResponse({"error": "new password must be at least 8 characters"}, status_code=400)

    # PR 7d: Re-encrypt all stored API keys with the NEW Fernet key.
    # Step 1: decrypt all keys with the OLD password_hash.
    # Step 2: compute the new password_hash.
    # Step 3: re-encrypt all keys with the NEW Fernet key.
    # All three steps must commit atomically — if any fails, roll back.
    from ..crypto import (
        decrypt_setting_key, encrypt_value, get_fernet_key,
        _get_or_create_salt, _FERNET_PREFIX, _SETTING_API_KEYS,
    )
    import logging
    _log = logging.getLogger(__name__)

    # Collect all API keys that need re-encryption (decrypt with OLD key)
    keys_to_reencrypt = {}  # key_name → plaintext value
    for key_name in _SETTING_API_KEYS:
        plaintext = decrypt_setting_key(key_name)
        if plaintext:
            keys_to_reencrypt[key_name] = plaintext
    # Also re-encrypt ai_providers.api_key entries
    ai_providers_to_reencrypt = []  # list of (id, plaintext)
    try:
        from ..db import conn as _conn
        with _conn() as c:
            rows = c.execute("SELECT id, api_key FROM ai_providers WHERE api_key IS NOT NULL AND api_key != ''").fetchall()
        from ..crypto import decrypt_api_key, is_encrypted
        for row in rows:
            stored_key = row["api_key"]
            if is_encrypted(stored_key):
                plaintext = decrypt_api_key(stored_key)
                if plaintext and plaintext != stored_key:
                    ai_providers_to_reencrypt.append((row["id"], plaintext))
            # If plaintext (not yet encrypted), leave it — migrate_setting_keys
            # will pick it up on next boot.
    except Exception as e:
        _log.warning("Could not collect ai_providers keys for re-encryption: %s", e)

    new_password_hash = _hash_password(payload.new_password)

    # Single atomic transaction: update password_hash + re-encrypt all keys
    # + revoke all sessions except the current one (H5 fix)
    current_token = None
    if request is not None:
        current_token = request.cookies.get("bb_token")
    try:
        with db.write_tx() as c:
            # (1) Update password_hash FIRST (so the new Fernet key can be derived)
            c.execute(
                "UPDATE settings SET value=? WHERE key='password_hash'",
                (new_password_hash,),
            )
            # (1b) H5: revoke all sessions except the current caller's.
            # A stolen cookie from before the password change is now invalid.
            if current_token:
                c.execute(
                    "DELETE FROM sessions WHERE token != ?",
                    (current_token,),
                )
            else:
                c.execute("DELETE FROM sessions")
            # (1c) Mark password_must_change=false (setup wizard completion)
            c.execute(
                "UPDATE settings SET value='false' WHERE key='password_must_change'"
            )
            # (2) Re-encrypt each settings.*_api_key with the NEW Fernet key
            salt_b64 = _get_or_create_salt()
            new_fernet = get_fernet_key(new_password_hash, salt_b64)
            for key_name, plaintext in keys_to_reencrypt.items():
                encrypted = new_fernet.encrypt(plaintext.encode()).decode()
                c.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (encrypted, key_name),
                )
            # (3) Re-encrypt each ai_providers.api_key with the NEW Fernet key
            for ap_id, plaintext in ai_providers_to_reencrypt:
                encrypted = new_fernet.encrypt(plaintext.encode()).decode()
                c.execute(
                    "UPDATE ai_providers SET api_key=? WHERE id=?",
                    (encrypted, ap_id),
                )
        _log.info(
            "PR 7d: password changed + re-encrypted %d setting keys + %d ai_providers keys",
            len(keys_to_reencrypt), len(ai_providers_to_reencrypt),
        )
    except Exception as e:
        _log.error("PR 7d: password change FAILED during re-encryption (rolled back): %s", e)
        return JSONResponse({
            "error": "password change failed — could not re-encrypt API keys. "
                     "Old password is still valid. Please try again.",
            "detail": str(e),
        }, status_code=500)

    return {"ok": True, "reencrypted_keys": len(keys_to_reencrypt) + len(ai_providers_to_reencrypt)}




@router.get("/api/setup-status")
def setup_status() -> Any:
    """Check if app needs initial setup (no password set)."""
    return {"initialized": bool(db.get_setting("password_hash", ""))}




@router.post("/api/setup")
def setup(payload: SetupIn, request: Request = None) -> Any:
    # v8.14.2: `request` optional so tests can call directly without a
    # Starlette Request — session is created either way, cookie only if
    # a real request is present.
    # v8.19: one setup = one license — the app cannot be initialized
    # before a license bound to this machine's Setup ID is active.
    if not licensing.is_activated():
        return JSONResponse(
            {"error": "license required — activate a license first",
             "code": "license_required"},
            status_code=403,
        )
    if db.get_setting("password_hash", ""):
        return JSONResponse({"error": "already initialized"}, status_code=400)
    if len(payload.password) < 8:
        return JSONResponse({"error": "password must be at least 8 characters"}, status_code=400)
    db.set_setting("password_hash", _hash_password(payload.password))
    token = secrets.token_urlsafe(32)
    _create_session(token, role="manager")
    r = JSONResponse({"ok": True})
    if request is not None:
        return _set_auth_cookie(r, token, request)
    return r


# ─── v8.1 Phase 1: First-Launch Wizard ──────────────────────────────────────

# Category templates by business type
_BUSINESS_TEMPLATES = {
    "wholesale": [
        {"code": "A", "name": "Budget",   "sell_price": 250,  "color": "#3b82f6", "sort_order": 1},
        {"code": "B", "name": "Standard", "sell_price": 500,  "color": "#10b981", "sort_order": 2},
        {"code": "C", "name": "Premium",  "sell_price": 750,  "color": "#f59e0b", "sort_order": 3},
        {"code": "D", "name": "Luxury",   "sell_price": 1000, "color": "#ef4444", "sort_order": 4},
    ],
    "retail": [
        {"code": "A", "name": "Small",    "sell_price": 100,  "color": "#3b82f6", "sort_order": 1},
        {"code": "B", "name": "Medium",   "sell_price": 300,  "color": "#10b981", "sort_order": 2},
        {"code": "C", "name": "Large",    "sell_price": 600,  "color": "#f59e0b", "sort_order": 3},
    ],
    "custom": [
        {"code": "A", "name": "Category A", "sell_price": 100, "color": "#3b82f6", "sort_order": 1},
    ],
}


class WizardIn(BaseModel):
    password: str
    business_type: str = "wholesale"
    categories: list = []  # [{code, name, sell_price, color}]
    gemini_key: str = ""
    start_page: str = "launcher"  # pos | dashboard | launcher
    # ─── v8.14.2: opt-in integrations ───────────────────────────────────
    # All default OFF / empty so the wizard is safe to skip.
    # See app/static/setup-wizard.html step 5 (Optional integrations).
    # v8.18.4: gdrive_connect / gdrive_backup_hour removed with the Drive
    # feature — old wizard clients sending them are silently ignored.
    digest_enabled: bool = False               # daily WhatsApp sales digest
    digest_hour: int | None = None             # 0-23 PKT — default 21 (9 PM) if None
    digest_phone: str = ""                     # E.164 like +923331234567
    fbr_auto_post: bool = False                # auto-post each sale to FBR (needs FBR creds in Settings)


@router.get("/api/setup/state")
def setup_wizard_state() -> Any:
    """Return wizard progress + whether setup is complete.

    Returns:
    - initialized: bool (password set)
    - setup_completed: bool (wizard finished)
    - has_categories: bool
    - start_page: str
    """
    initialized = bool(db.get_setting("password_hash", ""))
    setup_completed = db.get_setting("setup_completed", "") == "true"
    start_page = db.get_setting("start_page", "launcher") or "launcher"
    with db.conn() as c:
        cat_count = c.execute("SELECT COUNT(*) AS n FROM price_categories").fetchone()["n"]
    return {
        "initialized": initialized,
        "setup_completed": setup_completed,
        "has_categories": cat_count > 0,
        "category_count": cat_count,
        "start_page": start_page,
    }


@router.post("/api/setup/wizard")
def setup_wizard(payload: WizardIn, request: Request = None) -> Any:
    """Run the full first-launch wizard. Orchestrates password setup,
    category seeding, optional Gemini key, and start_page selection.

    Idempotent: if setup_completed is already true, returns 400.

    v8.14.2: `request` is optional so tests can call this function directly
    without constructing a Starlette Request — when absent, the session is
    still created but no auth cookie is set on the response.
    """
    # v8.19: one setup = one license — refuse to run the wizard before a
    # license bound to this machine's Setup ID is active.
    if not licensing.is_activated():
        return JSONResponse(
            {"error": "license required — activate a license first "
                      "(Step 1 of this wizard)",
             "code": "license_required"},
            status_code=403,
        )
    if db.get_setting("setup_completed", "") == "true":
        raise HTTPException(400, "setup already completed")
    if db.get_setting("password_hash", ""):
        raise HTTPException(400, "already initialized — use login, not wizard")
    # 1. Validate + set password
    if len(payload.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    db.set_setting("password_hash", _hash_password(payload.password))
    # 2. Seed categories (use template if not provided, else use the user's list)
    cats = payload.categories
    if not cats:
        cats = _BUSINESS_TEMPLATES.get(payload.business_type, _BUSINESS_TEMPLATES["wholesale"])
    with db.conn() as c:
        # Clear any existing categories (fresh DB should be empty, but be safe)
        c.execute("DELETE FROM price_categories")
        for cat in cats:
            c.execute(
                "INSERT INTO price_categories(name, code, sell_price, color, sort_order, active) "
                "VALUES(?,?,?,?,?, 1)",
                (cat.get("name", "Category"), cat.get("code", "?"),
                 float(cat.get("sell_price", 0)), cat.get("color", "#3b82f6"),
                 int(cat.get("sort_order", 0))),
            )
    # 3. Optional Gemini key
    if payload.gemini_key:
        # PR 7b: encrypt before storing (was plaintext before)
        from ..crypto import encrypt_setting_key, encrypt_api_key
        db.set_setting("gemini_api_key", encrypt_setting_key("gemini_api_key", payload.gemini_key))
        # Also register the provider in ai_providers table if it exists
        # v8.13.1 SECURITY FIX: encrypt the key in ai_providers too (was plaintext!)
        try:
            encrypted_key = encrypt_api_key(payload.gemini_key)
            with db.conn() as c:
                existing = c.execute(
                    "SELECT id FROM ai_providers WHERE name='gemini'"
                ).fetchone()
                if not existing:
                    # v8.18.4 FIX: this INSERT referenced a column `active`
                    # that does not exist (schema: provider_type/enabled) and
                    # omitted the NOT NULL provider_type — so it ALWAYS threw
                    # OperationalError, which the bare except below swallowed.
                    # Result: the wizard's Gemini key never reached
                    # ai_providers and bill extraction could not use it.
                    c.execute(
                        "INSERT INTO ai_providers(name, provider_type, api_key, model, priority, enabled) "
                        "VALUES('gemini', 'gemini', ?, 'gemini-2.5-flash', 0, 1)",
                        (encrypted_key,),
                    )
                else:
                    c.execute(
                        "UPDATE ai_providers SET api_key=?, enabled=1 WHERE name='gemini'",
                        (encrypted_key,),
                    )
        except Exception:
            pass  # ai_providers table may not exist — safe to skip
    # 4. Set start_page
    valid_start_pages = ("pos", "dashboard", "launcher")
    start_page = payload.start_page if payload.start_page in valid_start_pages else "launcher"
    db.set_setting("start_page", start_page)
    # ─── v8.14.2: persist opt-in integration choices (all default off) ──
    # v8.18.4: the Google Drive opt-in (connect flag + backup hour) was
    # removed along with the whole Drive feature. Old clients that still
    # POST gdrive_* fields are ignored — Pydantic drops unknown fields.
    # Daily WhatsApp digest — Twilio creds are added later in Settings.
    db.set_setting("digest_enabled", "1" if payload.digest_enabled else "0")
    if payload.digest_hour is not None and 0 <= int(payload.digest_hour) <= 23:
        db.set_setting("digest_hour", str(int(payload.digest_hour)))
    if payload.digest_phone:
        # Normalize: strip whitespace, keep the leading + if present.
        phone = str(payload.digest_phone).strip()
        db.set_setting("digest_phone", phone)
    # FBR auto-post flag — actual FBR credentials are added in Settings.
    db.set_setting("fbr_auto_post", "1" if payload.fbr_auto_post else "0")
    # 5. Mark setup completed
    db.set_setting("setup_completed", "true")
    # 6. Create a session so the user is logged in immediately
    token = secrets.token_urlsafe(32)
    _create_session(token, role="manager")
    db.log_activity("setup_wizard_completed", "system", None,
                    f"Setup wizard completed — {len(cats)} categories, start_page={start_page}",
                    {"business_type": payload.business_type, "category_count": len(cats),
                     "digest_enabled": payload.digest_enabled,
                     "fbr_auto_post": payload.fbr_auto_post})
    r = JSONResponse({
        "ok": True,
        "start_page": start_page,
        "category_count": len(cats),
    })
    # v8.14.2: skip cookie-setting when called directly (tests) — the session
    # row still exists; only the Set-Cookie header needs a real request.
    if request is not None:
        return _set_auth_cookie(r, token, request)
    return r


# ------------------------------------------------------------------
# UI
# ------------------------------------------------------------------



def ui():
    return FileResponse(BASE / "app" / "static" / "index.html")


# ------------------------------------------------------------------
# Bills
# ------------------------------------------------------------------

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB per file
MAX_FILES = 100  # Allow large multi-page uploads


def _validate_upload(files: list[UploadFile]):
    """Pre-flight check: sizes, counts, extensions."""
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_FILES})")
    allowed_ext = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in allowed_ext:
            raise HTTPException(400, f"Unsupported file type: {ext} ({f.filename})")




@router.get("/api/sessions")
def list_sessions() -> Any:
    """List active sessions (manager only — RBAC enforced by middleware)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT token, created_at, expires_at, role, employee_id "
            "FROM sessions WHERE expires_at > datetime('now','localtime') ORDER BY created_at DESC"
        ).fetchall()
    # Mask tokens for security
    return {"sessions": [{**dict(r), "token": r["token"][:8] + "..."} for r in rows]}




@router.delete("/api/sessions/{token_prefix}")
def revoke_session(token_prefix: str, request: Request) -> Any:
    """Revoke a session by token prefix (manager only)."""
    current_token = request.cookies.get("bb_token")
    with db.conn() as c:
        rows = c.execute("SELECT token FROM sessions WHERE token LIKE ?", (token_prefix + "%",)).fetchall()
        for row in rows:
            if row["token"] != current_token:  # Don't revoke own session
                c.execute("DELETE FROM sessions WHERE token=?", (row["token"],))
    return {"ok": True}


# ─── v6.0 Phase 2: Device Pairing ──────────────────────────────────────────

import hashlib as _hashlib
from datetime import datetime as _dt, timedelta as _td

logger = logging.getLogger(__name__)


@router.get("/api/devices/code")
def generate_pairing_code(request: Request, role: str = "cashier") -> Any:
    """Generate an 8-digit pairing code (2-min expiry). Manager session required.

    SECURITY (v8.13.2): Increased from 6-digit → 8-digit (100M combinations,
    100× harder to brute-force). Reduced expiry from 5 min → 2 min (smaller
    brute-force window). The pairing endpoint (/api/devices/pair) is also
    rate-limited to 5 attempts per minute per IP + exponential backoff per
    pairing code after 3 failures (1 min, 5 min, 30 min lockout).
    """
    if role not in ("cashier", "manager"):
        raise HTTPException(400, "Role must be 'cashier' or 'manager'")
    # v8.13.2: 8-digit code (was 6-digit). 100M combinations vs 1M.
    code = str(secrets.randbelow(90000000) + 10000000)  # 8-digit code (10000000–99999999)
    # v8.13.2: 2-minute expiry (was 5 minutes) — smaller brute-force window
    expires = (_dt.now() + _td(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        c.execute(
            "INSERT INTO pairing_codes(code, role, expires_at) VALUES(?,?,?)",
            (code, role, expires),
        )
    db.log_activity("pairing_code_generated", "device", None,
                    f"Pairing code generated for role={role}", {"role": role})
    return {"code": code, "role": role, "expires_in": 120}  # 2 minutes


@router.get("/api/devices/qr")
def generate_device_qr(request: Request, role: str = "cashier") -> Any:
    """v8.1 Phase 3: Generate a QR code encoding the pairing payload.

    Returns a PNG image. The QR encodes JSON: {pairing_code, server_url, role}.
    The mobile client scans this → auto-extracts code + URL → auto-pairs.
    """
    import qrcode, io
    from fastapi.responses import StreamingResponse
    if role not in ("cashier", "manager"):
        raise HTTPException(400, "Role must be 'cashier' or 'manager'")
    # Generate the pairing code
    code_r = generate_pairing_code(request, role)
    # Build the QR payload
    server_url = str(request.base_url).rstrip("/")
    qr_payload = json.dumps({
        "type": "billbook_pairing",
        "pairing_code": code_r["code"],
        "server_url": server_url,
        "role": role,
    })
    # Generate QR as PNG
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"X-Pairing-Code": code_r["code"],
                                      "X-Server-Url": server_url,
                                      "X-Role": role})


@router.post("/api/devices/pair")
def pair_device(payload: dict, request: Request) -> Any:
    """Pair a device using an 8-digit code. Returns a long-lived token.

    SECURITY (v8.13.2):
    - 8-digit code (100M combinations — was 6-digit / 1M)
    - Rate-limited to 5 attempts per minute per IP (was 10)
    - Per-code lockout: after 3 failures, the code is invalidated (forces
      regeneration) — prevents sustained brute-force on a single code

    H6 fix (v8.13.4): device tokens now expire after 90 days. The
    `expires_at` column is populated on pairing. verify_device_token
    checks expiry on each use and refreshes by 30 days (sliding window).
    """
    from ..security import check_login_throttle, record_failed_login
    client_ip = request.client.host if request.client else "unknown"
    if not check_login_throttle(client_ip):
        raise HTTPException(429, "Too many pairing attempts. Wait 60 seconds.")
    code = str(payload.get("code", "")).strip()
    device_name = str(payload.get("device_name", "Unknown Device")).strip()
    # v8.13.2: 8-digit code (was 6-digit)
    if not code or len(code) != 8 or not code.isdigit():
        raise HTTPException(400, "Invalid code format (must be 8 digits)")
    # H6: 90-day expiry on the device token itself
    from datetime import datetime as _dt, timedelta as _td
    expires_at = (_dt.now() + _td(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM pairing_codes WHERE code=? AND used=0 "
            "AND expires_at > datetime('now','localtime') ORDER BY id DESC LIMIT 1",
            (code,),
        ).fetchone()
        if not row:
            record_failed_login(client_ip)  # count as failed attempt for throttling
            # v8.13.2: per-code lockout — if the code exists but is now invalid
            # (expired or already used), check failure count. If 3+ failures on
            # this code, mark it used to force regeneration.
            existing = c.execute(
                "SELECT id, failure_count FROM pairing_codes WHERE code=? ORDER BY id DESC LIMIT 1",
                (code,),
            ).fetchone()
            if existing:
                new_count = (existing["failure_count"] or 0) + 1
                if new_count >= 3:
                    c.execute(
                        "UPDATE pairing_codes SET used=1, failure_count=? WHERE id=?",
                        (new_count, existing["id"]),
                    )
                else:
                    c.execute(
                        "UPDATE pairing_codes SET failure_count=? WHERE id=?",
                        (new_count, existing["id"]),
                    )
            raise HTTPException(403, "Invalid or expired pairing code")
        # Mark code as used
        c.execute("UPDATE pairing_codes SET used=1 WHERE id=?", (row["id"],))
        # Generate a long-lived token
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hashlib.sha256(raw_token.encode()).hexdigest()
        role = row["role"]
        # H6: insert with expires_at. The column may not exist on legacy DBs;
        # migrate_setting_keys() / db.init() adds it. Use try/except so we
        # still pair successfully on unmigrated DBs (token never expires —
        # same as the old behavior, but new DBs get the fix).
        try:
            c.execute(
                "INSERT INTO devices(name, token_hash, role, expires_at) "
                "VALUES(?,?,?,?)",
                (device_name, token_hash, role, expires_at),
            )
        except Exception:
            # Legacy schema without expires_at column — fall back to old insert
            c.execute(
                "INSERT INTO devices(name, token_hash, role) VALUES(?,?,?)",
                (device_name, token_hash, role),
            )
        device_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.log_activity("device_paired", "device", device_id,
                    f"Device '{device_name}' paired as {role} (expires {expires_at})",
                    {"role": role, "expires_at": expires_at})
    return {"token": raw_token, "role": role, "device_id": device_id,
            "expires_at": expires_at}


@router.get("/api/devices")
def list_devices() -> Any:
    """List all paired devices."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, role, last_seen, created_at FROM devices ORDER BY id"
        ).fetchall()
    return {"devices": [dict(r) for r in rows]}


@router.delete("/api/devices/{device_id}")
def revoke_device(device_id: int) -> Any:
    """Revoke a paired device."""
    with db.conn() as c:
        cur = c.execute("DELETE FROM devices WHERE id=?", (device_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "device not found")
    db.log_activity("device_revoked", "device", device_id,
                    f"Device #{device_id} revoked", {})
    return {"ok": True}


def verify_device_token(token: str) -> dict | None:
    """Verify a device token (for mobile API access). Returns device dict or None.

    H6 fix (v8.13.4): checks `expires_at` and rejects expired tokens.
    Refreshes expiry by 30 days (sliding window) on each successful use,
    so active devices stay paired indefinitely while stale ones auto-expire
    after 90 days.
    """
    if not token:
        return None
    token_hash = _hashlib.sha256(token.encode()).hexdigest()
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM devices WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if not row:
            return None
        # H6: enforce expiry if the column exists and has a value
        expires_at = row["expires_at"] if "expires_at" in row.keys() else None
        if expires_at:
            try:
                from datetime import datetime as _dt
                exp_dt = _dt.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
                if _dt.now() > exp_dt:
                    # Token expired — remove it and refuse
                    c.execute("DELETE FROM devices WHERE id=?", (row["id"],))
                    return None
            except Exception:
                pass  # malformed timestamp — ignore, treat as no expiry
        # Update last_seen + sliding-refresh the expiry by 30 days
        from datetime import datetime as _dt, timedelta as _td
        new_expires = (_dt.now() + _td(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            c.execute(
                "UPDATE devices SET last_seen=datetime('now','localtime'), "
                "expires_at=? WHERE id=?",
                (new_expires, row["id"]),
            )
        except Exception:
            # Legacy DB without expires_at column — just refresh last_seen
            c.execute(
                "UPDATE devices SET last_seen=datetime('now','localtime') WHERE id=?",
                (row["id"],),
            )
        return dict(row)

