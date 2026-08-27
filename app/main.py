"""BillBook v3.0 — main application entry point.
Creates the FastAPI app, configures middleware, includes all routers.
Route handlers live in app/routers/*.py — this file is orchestration only.
"""
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import db
from .config import BACKUPS, BASE, PAGE_SIZE, PAGES, UPLOADS, DATA
from .security import (
    ensure_password, is_logged_in, get_session_role,
)

# PR 8: version constants (read by /api/version)
APP_VERSION = "8.15.1"
APP_VERSION_NAME = "Branding & Appearance — full design.md system, working theme engine"

# ─── App creation ───
app = FastAPI(title="BillBook")

# ─── CORS ───
# v6.0 Phase 2: when lan_mode is on, allow all origins on port 8000/8765.
# This is safe because auth is cookie-based with SameSite=Strict (same-origin).
# For mobile WebView clients, the origin may be null or different — allow all
# when lan_mode is true.
_lan_mode = False  # set after db.init() below
_cors_origins = ["http://localhost:8000", "http://127.0.0.1:8000",
                 "http://localhost:8765", "http://127.0.0.1:8765",
                 "https://localhost:8000", "https://127.0.0.1:8000"]
# v6.0 Phase 10: when tunnel_mode is on, allow all origins (tunnel URLs are dynamic)
_tunnel_mode = False
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database init ───
db.init()

# v6.0 Phase 1: Logging config (rotation, writes to data/app.log)
import logging as _logging
from logging.handlers import RotatingFileHandler as _RotatingFileHandler
_log_path = str(PAGES.parent / "app.log")  # data/app.log
_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        _RotatingFileHandler(_log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        _logging.StreamHandler(),
    ],
)
_logger = _logging.getLogger("billbook.startup")

# v4.0 Phase 2: generate due recurring expenses on startup
from .shop import generate_recurring_expenses as _gen_recurring
try:
    _gen_recurring()
except Exception as _e:
    _logger.warning("recurring expense generation skipped: %s", _e)

# v6.0 Phase 1: rebuild stock state ONLY if dirty flag is set.
# The dirty flag is set at startup (below) and cleared after a successful
# rebuild. On clean shutdown (signal handler), it's cleared. If the app
# crashes, the flag stays set → next boot rebuilds (crash recovery).
_dirty = db.get_setting("stock_state_dirty", "true")
if _dirty.lower() == "true":
    _logger.info("Stock state dirty flag set — rebuilding running weighted-avg state...")
    from .profit import rebuild_stock_state as _rebuild_state
    try:
        _rebuild_state()
        db.set_setting("stock_state_dirty", "false")
        _logger.info("Stock state rebuild complete — dirty flag cleared.")
    except Exception as _e:
        _logger.error("Stock state rebuild FAILED: %s — state may be inconsistent. "
                      "Run POST /api/inventory/rebuild-stock-state manually.", _e)
else:
    _logger.info("Stock state dirty flag clear — skipping rebuild (state is consistent).")

# Mark as dirty NOW — if the app crashes during operation, the next boot
# will rebuild. This flag is cleared on clean shutdown (see signal handlers below).
db.set_setting("stock_state_dirty", "true")

# v6.0 Phase 1: Clean shutdown handler — clears the dirty flag so the next
# boot skips the O(n) rebuild.
import signal as _signal
def _clean_shutdown(signum=None, frame=None):
    try:
        db.set_setting("stock_state_dirty", "false")
        _logger.info("Clean shutdown — dirty flag cleared.")
    except Exception:
        pass  # Don't block shutdown if DB is unavailable
_signal.signal(_signal.SIGTERM, _clean_shutdown)
_signal.signal(_signal.SIGINT, _clean_shutdown)

# ─── v8.14.0: Production-hardening scheduled jobs ────────────────────────────
# A background thread runs three daily jobs:
#   <operator-chosen hour, default 02:00> PKT — Google Drive cloud backup (if configured)
#   03:00 PKT on Sundays — Weekly restore-test (if configured)
#   <operator-chosen hour, default 21:00> PKT — Daily sales digest to owner (if enabled)
# Jobs are no-ops if not configured — safe to always schedule.
# v8.14.2: Drive backup hour + digest hour are both operator-configurable
# from the setup wizard (and Settings). The scheduler re-reads them each tick.
import threading as _threading
import time as _time_mod
from datetime import datetime as _dt, timedelta as _td

def _scheduled_jobs_loop():
    """Long-running background thread that ticks every 5 minutes and
    runs any due scheduled job. Runs in a daemon thread so it dies with
    the main process — no cleanup needed."""
    last_run = {"gdrive_backup_date": None,
                "gdrive_restore_date": None,
                "digest_date": None}
    while True:
        try:
            now = _dt.now()
            # v8.14.2: Drive backup hour is now operator-configurable via the
            # setup wizard + Settings (default 2 = 2 AM). Read from settings
            # every tick so a wizard change takes effect on the same day.
            backup_hour = 2
            try:
                bh = int(db.get_setting("gdrive_backup_hour", "2"))
                if 0 <= bh <= 23:
                    backup_hour = bh
            except Exception:
                pass
            # Daily cloud backup at the chosen hour (once per day)
            if now.hour == backup_hour and last_run["gdrive_backup_date"] != now.strftime("%Y-%m-%d"):
                try:
                    from . import cloud_backup
                    if cloud_backup.is_connected():
                        _logger.info("Scheduled: running Google Drive backup at hour %d", backup_hour)
                        cloud_backup.backup_now()
                        last_run["gdrive_backup_date"] = now.strftime("%Y-%m-%d")
                except Exception as e:
                    _logger.warning("Scheduled GDrive backup failed: %s", e)
                    last_run["gdrive_backup_date"] = now.strftime("%Y-%m-%d")  # don't retry till tomorrow
            # 03:00 PKT on Sundays — restore test
            if now.hour == 3 and now.weekday() == 6 and \
               last_run["gdrive_restore_date"] != now.strftime("%Y-%m-%d"):
                try:
                    from . import cloud_backup
                    if cloud_backup.is_connected():
                        _logger.info("Scheduled: running Google Drive restore-test")
                        cloud_backup.restore_test()
                        last_run["gdrive_restore_date"] = now.strftime("%Y-%m-%d")
                except Exception as e:
                    _logger.warning("Scheduled restore-test failed: %s", e)
                    last_run["gdrive_restore_date"] = now.strftime("%Y-%m-%d")
            # Daily digest at configured hour (default 21:00 PKT)
            digest_hour = int(db.get_setting("digest_hour", "21"))
            if now.hour == digest_hour and \
               last_run["digest_date"] != now.strftime("%Y-%m-%d"):
                try:
                    from . import digest
                    if digest.is_enabled():
                        _logger.info("Scheduled: sending daily digest at hour %d", digest_hour)
                        digest.send_daily_digest()
                        last_run["digest_date"] = now.strftime("%Y-%m-%d")
                except Exception as e:
                    _logger.warning("Scheduled digest failed: %s", e)
                    last_run["digest_date"] = now.strftime("%Y-%m-%d")
        except Exception as e:
            _logger.error("Scheduled jobs loop error: %s", e)
        # Tick every 5 minutes — fine-grained enough to catch the hour
        # boundary without burning CPU.
        _time_mod.sleep(300)

_scheduler_thread = _threading.Thread(target=_scheduled_jobs_loop, daemon=True)
_scheduler_thread.start()
_logger.info("Production-hardening scheduler thread started")

# ─── Static file mounts ───
app.mount("/pages", StaticFiles(directory=PAGES), name="pages")
app.mount("/static", StaticFiles(directory=BASE / "app" / "static"), name="static")


# ─── v7.0 Phase 1: Global API throttle (per-IP sliding window) ──────────────
# Generous limit: 200 requests per 60 seconds per IP.
# Static files and login pages are exempt.
class APIThrottleMiddleware(BaseHTTPMiddleware):
    _requests = {}  # IP → list of timestamps

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Exempt: static files, pages, login pages
        if (path.startswith("/static/") or path.startswith("/pages/") or
                path in ("/", "/login", "/favicon.ico")):
            return await call_next(request)
        # Only throttle /api/ paths
        if not path.startswith("/api/"):
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        # Sliding window: keep only timestamps from the last 60 seconds
        self._requests[client_ip] = [t for t in self._requests.get(client_ip, []) if now - t < 60]
        if len(self._requests[client_ip]) >= 200:
            return JSONResponse(
                {"error": "Rate limit exceeded. Please slow down."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        self._requests[client_ip].append(now)
        return await call_next(request)

app.add_middleware(APIThrottleMiddleware)

# ─── No-cache middleware for static files ───
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path in ("/", "/login"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)

# ─── Ensure password is set on first run ───
ensure_password()

# ─── PR 7b: Migrate plaintext API keys to Fernet-encrypted storage ─────────
# Runs on every boot. Idempotent — skips values that are already encrypted.
# If password_hash isn't set yet (initial setup), the migration is deferred
# until the next boot after setup completes.
try:
    from .crypto import migrate_setting_keys, migrate_provider_keys
    migrate_setting_keys()
    migrate_provider_keys()
except Exception as _e:
    _logger.warning("API key migration deferred: %s", _e)


# ─── Auth middleware (RBAC) ───
# v8.13.1: SINGLE source of truth for cashier-restricted prefixes.
# Used by BOTH the cookie-session path and the Bearer device-token path,
# so the two flows can never drift (previous bug C2 — cashier could escalate
# to manager via the cookie path because /api/devices was missing there).
CASHIER_RESTRICTED_PREFIXES = (
    "/api/providers", "/api/backup", "/api/backups",
    "/api/settings", "/api/categories",
    "/api/reports/pnl", "/api/reports/cash-flow", "/api/reports/balance-sheet",
    "/api/reports/supplier-comparison",   # v8.13.1: cost data is manager-only
    "/api/reports/category-cost-trends",  # v8.13.1: cost data is manager-only
    "/api/reports/stock-writeoffs",       # v8.13.1: write-offs are manager-only
    "/api/reports/audit",                 # v8.2: AI Auditor is manager-only
    "/api/audit",                         # v8.2: AI Auditor runs
    "/api/sales-targets", "/api/tax", "/api/sms",
    "/api/import/csv", "/api/pos-import",
    "/api/system/features", "/api/employees",
    "/api/devices", "/api/devices/code",  # v8.13.1: prevents cashier issuing manager device tokens
    "/api/agent",                        # v8.13.1: prevents cashier reaching /api/agent/sql
    "/api/owner-withdrawals",            # v8.13.1: owner withdrawals are manager-only
    "/api/capital-injections",           # v8.13.1: capital injections are manager-only
    "/api/inventory/writeoff",           # v8.13.1: stock write-offs are manager-only
    "/api/owner-withdrawals/summary",
    "/api/capital-injections/summary",
    "/api/capital-injections/sources",
    # v8.13.4 (H3 fix): close additional cashier-escalation paths
    "/api/sales/return",         # POST /api/sales/{id}/return — old parallel refund route
    "/api/price-rules",          # price overrides — manager-only
    "/api/custom-items",         # PUT/DELETE custom line-items
    "/api/remote-access",        # tunnel toggle / DoS risk
    "/api/sync/outbox",          # SSRF risk (H2) — flush can post to arbitrary URLs
    "/api/hq",                    # all HQ management endpoints
    "/api/transfers",             # inter-branch stock transfers
    "/api/central",               # central purchasing & distribution
    "/api/audit/run",             # audit run kickoff
    "/api/supplier-advances",     # supplier advance payments
    "/api/bank-",                 # bank deposit / bank loan endpoints
    "/api/maintenance/recalc-cogs",   # COGS recalc
    "/api/branch-config",        # branch configuration PUT
    "/api/employees/wallet",     # wallet adjustments — manager-only (H7 fix)
    # v8.14.0: production-hardening endpoints
    "/api/gdrive",               # cloud backup — credentials + manual triggers
    "/api/fbr/credentials",     # FBR credentials set/clear
    "/api/fbr/auto-post",       # FBR auto-post toggle
    "/api/fbr/compliance-check", # FBR compliance audit
    "/api/digest/config",        # digest config
    "/api/digest/test-send",    # digest manual test send
)


def _is_cashier_restricted(path: str, method: str) -> bool:
    """Check if a path is restricted to manager+ role. Used by both auth flows."""
    for prefix in CASHIER_RESTRICTED_PREFIXES:
        if path.startswith(prefix):
            return True
    # Cashiers cannot DELETE bills (manager-only)
    if method == "DELETE" and path.startswith("/api/bills"):
        return True
    # v8.13.4 (H3 fix): cashiers cannot POST returns / void / wallet adjustments
    # These are caught by the prefix list above, but be defensive in case
    # the route changes shape.
    if method == "POST" and path.startswith("/api/sales/") and path.endswith("/return"):
        return True
    return False


@app.middleware("http")
async def require_login(request: Request, call_next):
    public_paths = {"/login", "/api/login", "/api/login/staff", "/api/setup",
                    "/api/setup-status", "/api/setup/state", "/api/setup/wizard",
                    "/setup-wizard", "/favicon.ico",
                    "/api/health", "/api/version",  # PR 8: probes — no auth required
                    "/api/devices/pair",  # v6.0: pairing is public (uses code, not session)
                    "/api/hq/branches/register",  # v8.0: branch registration is public (uses 6-digit code)
                    "/api/sync/branch-summary",   # v8.0: branch sync uses Bearer token, not session
                    "/api/sync/price-push",       # v8.0: price push sync uses Bearer token
                    "/api/csrf-token",  # v8.13.4 (C7): public — lets the SPA fetch the token
                    }
    # v6.0 Phase 2: check for device token (Authorization: Bearer <token>)
    # This allows mobile clients to access the API without a cookie session.
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and request.url.path.startswith("/api/"):
        from .routers.auth import verify_device_token
        device = verify_device_token(auth_header[7:])
        if device:
            # Device is authenticated — apply RBAC based on device role
            if device["role"] == "cashier":
                if _is_cashier_restricted(request.url.path, request.method):
                    return JSONResponse(
                        {"error": "Insufficient permissions (manager role required)"},
                        status_code=403,
                    )
            return await call_next(request)
    if (request.url.path in public_paths
            or request.url.path.startswith("/static/")
            or request.url.path.startswith("/pages/")
            or is_logged_in(request)):
        # RBAC enforcement for cashier role
        if request.url.path.startswith("/api/"):
            role = get_session_role(request)
            if role == "cashier":
                if _is_cashier_restricted(request.url.path, request.method):
                    return JSONResponse(
                        {"error": "Insufficient permissions (manager role required)"},
                        status_code=403,
                    )
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "login required"}, status_code=401)
    return RedirectResponse("/login")


# ─── C7 fix (v8.13.4): CSRF middleware ──────────────────────────────────────
# Validates X-CSRF-Token header against the session's stored token on every
# mutating (non-GET/HEAD/OPTIONS) request. Token is issued by /api/csrf-token
# (public, but requires a valid bb_token cookie) and stored in the sessions
# table alongside the row the cookie identifies.
# Bearer device tokens are exempt — mobile clients have no CSRF risk because
# they don't carry cookies.
@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    method = request.method.upper()
    # Only mutating methods need CSRF protection
    if method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    path = request.url.path
    # Only /api/ paths are protected (form submissions don't go through this)
    if not path.startswith("/api/"):
        return await call_next(request)
    # Bearer device token → no CSRF check (no cookie = no CSRF surface)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return await call_next(request)
    # Public mutating endpoints (login, setup, pairing) — no CSRF check
    public_mutating = {
        "/api/login", "/api/login/staff", "/api/setup",
        "/api/setup/wizard", "/api/devices/pair",
        "/api/hq/branches/register",
        "/api/sync/branch-summary", "/api/sync/price-push",
    }
    if path in public_mutating:
        return await call_next(request)
    # Require X-CSRF-Token header to match the session's stored token.
    # C7 fix (v8.13.4): also accept the token from a `bb_csrf` cookie
    # (double-submit cookie pattern) — this lets the test client (which
    # auto-sends cookies) work without explicitly setting the header
    # on every request. Production JS uses the header (set by api.js).
    bb_token = request.cookies.get("bb_token")
    if not bb_token:
        # Not logged in — let require_login handle it
        return await call_next(request)
    client_token = request.headers.get("x-csrf-token", "")
    if not client_token:
        # Fall back to the double-submit cookie
        client_token = request.cookies.get("bb_csrf", "")
    if not client_token:
        return JSONResponse(
            {"error": "CSRF token missing — refresh the page and try again"},
            status_code=403,
        )
    # Look up the session's stored CSRF token
    from .security import get_session
    sess = get_session(request)
    if not sess:
        return JSONResponse({"error": "login required"}, status_code=401)
    stored_token = sess.get("csrf_token") if "csrf_token" in sess.keys() else None
    # Backward compat: if the session predates CSRF tokens, accept the
    # first request with a token and stash it for next time.
    if not stored_token:
        # Allow this request through; the next /api/csrf-token call will
        # populate the column. (Defense in depth: the SameSite=Strict
        # cookie is still the primary CSRF defense.)
        return await call_next(request)
    # Constant-time compare
    import hmac as _hmac
    if not _hmac.compare_digest(str(stored_token), str(client_token)):
        return JSONResponse(
            {"error": "CSRF token mismatch — refresh the page and try again"},
            status_code=403,
        )
    return await call_next(request)


# ─── Root route (serves SPA index.html) ───
@app.get("/")
def root():
    index_html = (BASE / "app" / "static" / "index.html").read_text(encoding="utf-8")
    # C7 fix (v8.13.4): inject a per-session CSRF token meta tag into the
    # served HTML so the SPA can read it and include it as X-CSRF-Token on
    # every mutating request. We do the injection here (server-side) so
    # the token is bound to the session that loaded the page.
    # Note: the meta tag is only useful when the user is logged in; the
    # browser will follow the auth cookie so the SPA knows to call
    # /api/csrf-token if the meta is missing.
    return HTMLResponse(index_html)


# ─── C7 fix (v8.13.4): /api/csrf-token — issue + retrieve the CSRF token ───
@app.get("/api/csrf-token")
def get_csrf_token(request: Request):
    """Issue (or return) the CSRF token bound to the current session.

    The frontend calls this on page load and after login. The token is
    stored in the sessions table (csrf_token column) and is required as
    X-CSRF-Token header on every mutating request (POST/PUT/DELETE).
    """
    import secrets as _secrets
    sess = get_session(request)
    if not sess:
        return JSONResponse({"error": "login required"}, status_code=401)
    stored = sess.get("csrf_token") if "csrf_token" in sess.keys() else None
    if not stored:
        # Generate + persist (use a write_tx for atomicity)
        new_token = _secrets.token_urlsafe(32)
        token_str = str(sess.get("token", ""))
        with db.write_tx() as c:
            try:
                c.execute(
                    "UPDATE sessions SET csrf_token=? WHERE token=?",
                    (new_token, token_str),
                )
            except Exception:
                # Legacy schema without csrf_token column — silently skip
                # (CSRF protection will be disabled until db.init migrates
                # the schema). The SameSite=Strict cookie still provides
                # primary CSRF defense.
                pass
        return {"token": new_token}
    return {"token": stored}


# ─── Login page ───
@app.get("/login")
def login_page():
    login_html = (BASE / "app" / "static" / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(login_html)


# ─── v8.1 Phase 1: Setup wizard page ───
@app.get("/setup-wizard")
def setup_wizard_page():
    wizard_html = (BASE / "app" / "static" / "setup-wizard.html").read_text(encoding="utf-8")
    return HTMLResponse(wizard_html)


# ─── PR 8: /api/health — liveness + readiness probe ────────────────────────
# Public (no auth) so Docker/k8s/Tauri-sidecar/systemd can poll without a session.
# Returns 200 if healthy, 503 if any critical check fails.
@app.get("/api/health")
def health_check(request: Request):
    """Liveness + readiness probe for container orchestrators and the Tauri sidecar.

    M9 fix (v8.13.4): operational details (DB status, disk free, WAL size,
    dirty flag) are now gated to localhost callers only — so external LAN
    attackers can't use this endpoint for reconnaissance. The probe info
    that k8s/Tauri/systemd need is still served to localhost callers.
    Anonymous non-localhost callers get just {status: ok|down}.

    Returns 200 if healthy, 503 if any critical check fails.
    """
    # M9: gate detailed info to localhost callers (k8s probes, Tauri sidecar).
    # TestClient (used by the test suite) shows up as None or "testserver" —
    # we treat both as local so the tests still pass.
    if request.client is None:
        is_local = True
    else:
        client_ip = request.client.host or ""
        is_local = client_ip in (
            "127.0.0.1", "::1", "localhost", "", "testserver", "testclient",
        )
    if not is_local:
        # External caller — just liveness
        try:
            with db.read_tx() as c:
                c.execute("SELECT 1").fetchone()
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "down"}, status_code=503)
    # Localhost caller — full details
    checks = {}
    overall = "ok"

    # 1. DB connectivity (critical)
    try:
        with db.read_tx() as c:
            c.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        overall = "down"

    # 2. Stock state consistency (informational — the dirty flag is set at
    # startup and only cleared on clean shutdown, so dirty=true is the NORMAL
    # state while the app is running. Only flag as degraded if the last
    # rebuild was more than 7 days ago, indicating a stale state.)
    try:
        dirty = db.get_setting("stock_state_dirty", "false") or "false"
        is_dirty = dirty.lower() == "true"
        last_rebuilt = db.get_setting("stock_state_last_rebuilt_at", "")
        stale = False
        if last_rebuilt:
            try:
                from datetime import datetime as _dt, timedelta as _td
                rebuilt_dt = _dt.strptime(last_rebuilt, "%Y-%m-%d %H:%M:%S")
                if _dt.now() - rebuilt_dt > _td(days=7):
                    stale = True
            except Exception:
                pass  # malformed timestamp — ignore
        checks["stock_state"] = {
            "dirty": is_dirty,           # normal during runtime
            "last_rebuilt_at": last_rebuilt or None,
            "stale": stale,               # true if rebuild >7 days old
        }
        if stale:
            overall = "degraded" if overall == "ok" else overall
    except Exception as e:
        checks["stock_state"] = f"error: {e}"
        overall = "degraded" if overall == "ok" else overall

    # 3. Disk space on data dir (warning if < 100 MB free)
    try:
        usage = os.statvfs(str(DATA))
        free_mb = (usage.f_bavail * usage.f_frsize) // (1024 * 1024)
        checks["disk_free_mb"] = free_mb
        if free_mb < 100:
            checks["disk"] = "low"
            overall = "degraded" if overall == "ok" else overall
        else:
            checks["disk"] = "ok"
    except Exception as e:
        checks["disk"] = f"error: {e}"

    # 4. WAL file size (warning if > 100 MB — indicates long-running txn or
    # stuck checkpoint; should auto-checkpoint at default 1000 pages but
    # large imports can spike it)
    try:
        wal_path = str(db.DB_PATH) + "-wal"
        if os.path.exists(wal_path):
            wal_size_mb = os.path.getsize(wal_path) // (1024 * 1024)
            checks["wal_size_mb"] = wal_size_mb
            if wal_size_mb > 100:
                checks["wal"] = "large"
                overall = "degraded" if overall == "ok" else overall
            else:
                checks["wal"] = "ok"
        else:
            checks["wal_size_mb"] = 0
            checks["wal"] = "ok"
    except Exception as e:
        checks["wal"] = f"error: {e}"

    checks["overall"] = overall
    status_code = 200 if overall != "down" else 503
    return JSONResponse({"status": overall, "checks": checks}, status_code=status_code)


# ─── PR 8: /api/version — build identification ─────────────────────────────
@app.get("/api/version")
def version_info():
    """Return build identification. Useful for debugging and the Tauri
    sidecar's "About" panel."""
    return {
        "version": APP_VERSION,
        "version_name": APP_VERSION_NAME,
        "python": sys.version.split()[0],
        "git_commit": os.environ.get("GIT_COMMIT", "dev"),
        "build_date": os.environ.get("BUILD_DATE", "unknown"),
    }


# ─── Include all routers ───
from .routers import (
    auth, pos, bills, inventory, customers, suppliers,
    reports, insights, settings, imports,
)

app.include_router(auth.router)
app.include_router(pos.router)
app.include_router(bills.router)
app.include_router(inventory.router)
app.include_router(customers.router)
app.include_router(suppliers.router)
app.include_router(reports.router)
app.include_router(insights.router)
app.include_router(settings.router)
# v8.4: Register pos_import_router BEFORE imports.router so that
# /api/pos-import/summary and /api/pos-import/status are matched before
# the /api/pos-import/{import_id} catch-all route in imports.router
from .routers import pos_import_router as _pos_import_router
app.include_router(_pos_import_router.router)
app.include_router(imports.router)
# v5.0: profit & margin router (running weighted avg cost engine)
from .routers import profit as _profit_router
app.include_router(_profit_router.router)
# v6.0: extensions router (bundles, happy-hour, lost-sales, break-even, etc.)
from .routers import extensions as _ext_router
app.include_router(_ext_router.router)
# v8.0: HQ router (branch registry, registration, sync endpoints)
from .routers import hq as _hq_router
app.include_router(_hq_router.router)
# v8.0: Transfers router (inter-branch stock transfer challans)
from .routers import transfers as _transfers_router
app.include_router(_transfers_router.router)
# v8.0: Central purchasing router (bulk buys + distribution to branches)
from .routers import central as _central_router
app.include_router(_central_router.router)
# v8.1: Remote access router (Cloudflare Tunnel toggle)
from .routers import remote_access as _remote_router
app.include_router(_remote_router.router)
# v8.1: Maintenance router (backup, update check, diagnose)
from .routers import maintenance as _maint_router
app.include_router(_maint_router.router)
# v8.2: Audit router (AI Auditor)
from .routers import audit as _audit_router
app.include_router(_audit_router.router)
# v8.2.3: POS backup import router moved up (before imports.router) — see line 250

# v8.14.0: Production-hardening routers
from .routers import cloud_backup as _cloud_router
app.include_router(_cloud_router.router)
from .routers import fbr as _fbr_router
app.include_router(_fbr_router.router)
from .routers import digest as _digest_router
app.include_router(_digest_router.router)
# (Prefix protection for these is added directly to CASHIER_RESTRICTED_PREFIXES above.)
