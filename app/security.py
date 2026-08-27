"""Shared security helpers — auth, sessions, password hashing.
Imported by all router modules and main.py middleware.
"""
import os
import time
import logging
import bcrypt
from datetime import datetime, timedelta

from fastapi import Request

from . import db

logger = logging.getLogger(__name__)
SESSION_DAYS = 30


def hash_password(pw: str) -> str:
    """Bcrypt hash. Truncate to 72 bytes (bcrypt limit)."""
    pw_bytes = pw.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


# ─── PR 7a: PIN hashing (replaces plaintext employees.pin) ────────────────
# PINs are short (4-6 digits) so we use a higher bcrypt cost (14 rounds vs 12
# for passwords) to compensate for the smaller keyspace. verify_pin is the
# primary check; shop.verify_manager_pin falls back to plaintext pin (with
# a warning log) for backward-compat during migration.

def hash_pin(pin: str) -> str:
    """Bcrypt hash a PIN. Uses 14 rounds (higher than passwords) because PINs
    have a smaller keyspace (10^4 to 10^6 vs 95^8+ for passwords).
    """
    if not pin:
        return ""
    pin_bytes = pin.encode("utf-8")[:72]
    return bcrypt.hashpw(pin_bytes, bcrypt.gensalt(rounds=14)).decode("utf-8")


def verify_pin(pin: str, hashed: str) -> bool:
    """Verify a PIN against its bcrypt hash."""
    if not pin or not hashed:
        return False
    try:
        return bcrypt.checkpw(pin.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


# Known-bad placeholder passwords that must NEVER be accepted as the seed
# admin password. Matches the .env.example template values and similar
# weak defaults. v8.13.5: extended to catch the new template default
# "please-change-this-to-a-real-password" too.
_BAD_PLACEHOLDER_PASSWORDS = {
    "", "change-me-now", "changeme", "password", "admin", "secret",
    "billbook", "billbook-change-this-secret",
    "please-change-this", "please-change-this-to-a-real-password",
    "your-password-here", "set-a-real-password", "set-your-password",
}


def ensure_password():
    """On first run, if no password hash is set, store one from APP_PASSWORD env.

    SECURITY (v8.13.4 fix for C1): refuses to seed from known-bad placeholder
    values. If APP_PASSWORD is missing or matches a placeholder, the app
    starts without an admin password and the setup wizard MUST be completed
    before any login is possible. This prevents the "shipped .env with
    change-me-now" footgun.
    """
    stored = db.get_setting("password_hash", "")
    if stored:
        return
    env_pw = os.getenv("APP_PASSWORD", "").strip()
    if env_pw and env_pw not in _BAD_PLACEHOLDER_PASSWORDS and len(env_pw) >= 8:
        db.set_setting("password_hash", hash_password(env_pw))
        db.set_setting("password_must_change", "true")
    else:
        # Leave password_hash unset — setup wizard must complete first.
        # Log a loud warning so the operator notices.
        logger.warning(
            "ensure_password(): APP_PASSWORD is missing or a known-bad "
            "placeholder. Refusing to seed admin password. Complete the "
            "setup wizard at /setup-wizard to set an initial password."
        )


def is_logged_in(request: Request) -> bool:
    token = request.cookies.get("bb_token")
    if not token:
        return False
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM sessions WHERE token=? AND expires_at > datetime('now','localtime')",
            (token,),
        ).fetchone()
    return row is not None


def get_session(request: Request) -> dict | None:
    """Get the full session row including role."""
    token = request.cookies.get("bb_token")
    if not token:
        return None
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM sessions WHERE token=? AND expires_at > datetime('now','localtime')",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def get_session_role(request: Request) -> str:
    """Get the role for the current session. Default 'manager' for backward compat."""
    sess = get_session(request)
    if not sess:
        return "manager"
    return sess.get("role") or "manager"


def create_session(token: str, role: str = "manager", employee_id: int = None):
    """Create a session in SQLite."""
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        c.execute(
            "INSERT INTO sessions(token, expires_at, role, employee_id) VALUES(?,?,?,?)",
            (token, expires, role, employee_id),
        )


def delete_session(token: str):
    """Delete a session from SQLite."""
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def check_login_throttle(ip: str) -> bool:
    """Returns True if login is allowed, False if throttled.

    v6.0 Phase 1: persists to login_attempts table (survives restart).
    Throttle: 5 failed attempts within 60 seconds → blocked.
    """
    now = time.time()
    cutoff = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Count attempts in the last 60 seconds
    with db.conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE ip=? AND ts > ?",
            (ip, datetime.fromtimestamp(now - 60).strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
    return (row["n"] if row else 0) < 5


def record_failed_login(ip: str):
    """Record a failed login attempt to the login_attempts table."""
    with db.conn() as c:
        c.execute(
            "INSERT INTO login_attempts(ip, ts) VALUES(?, datetime('now','localtime'))",
            (ip,),
        )
        # Clean up attempts older than 24 hours to prevent table growth
        c.execute(
            "DELETE FROM login_attempts WHERE ts < datetime('now','localtime','-1 day')"
        )


# ─── H4 fix (v8.13.4): throttling for manager-PIN failures ────────────────
# The login throttle above uses IP. PIN failures don't have a separate
# "PIN attempts" table, so we reuse login_attempts with a synthetic
# "pin:{employee_id}" key. This means N PIN failures from one IP also
# count toward the login throttle (defense in depth).
def record_failed_pin(employee_id: int, ip: str = ""):
    """Record a failed manager-PIN attempt.

    Counts toward BOTH the per-IP throttle (via the ip key) AND a per-
    employee synthetic key (so a distributed attack still locks the
    employee out).
    """
    with db.conn() as c:
        c.execute(
            "INSERT INTO login_attempts(ip, ts) VALUES(?, datetime('now','localtime'))",
            (ip or f"pin:{employee_id}",),
        )
        c.execute(
            "INSERT INTO login_attempts(ip, ts) VALUES(?, datetime('now','localtime'))",
            (f"pin:{employee_id}",),
        )
        # Auto-lock employee after 5 PIN failures in 60s
        row = c.execute(
            "SELECT COUNT(*) AS n FROM login_attempts "
            "WHERE ip=? AND ts > datetime('now','localtime','-1 minute')",
            (f"pin:{employee_id}",),
        ).fetchone()
        if row and row["n"] >= 5:
            try:
                c.execute(
                    "UPDATE employees SET locked_until="
                    "datetime('now','localtime','+15 minutes') WHERE id=?",
                    (employee_id,),
                )
            except Exception:
                pass  # legacy schema without locked_until column


def check_pin_throttle(employee_id: int, ip: str = "") -> bool:
    """Returns True if PIN attempt is allowed, False if throttled."""
    now = time.time()
    cutoff_ip = datetime.fromtimestamp(now - 60).strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        # Per-IP throttle (reuse login throttle)
        row_ip = c.execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE ip=? AND ts > ?",
            (ip, cutoff_ip),
        ).fetchone() if ip else None
        if row_ip and row_ip["n"] >= 5:
            return False
        # Per-employee throttle
        row_emp = c.execute(
            "SELECT COUNT(*) AS n FROM login_attempts "
            "WHERE ip=? AND ts > datetime('now','localtime','-1 minute')",
            (f"pin:{employee_id}",),
        ).fetchone()
        if row_emp and row_emp["n"] >= 5:
            return False
        # Check explicit lockout column
        try:
            row_lock = c.execute(
                "SELECT locked_until FROM employees WHERE id=?",
                (employee_id,),
            ).fetchone()
            if row_lock and row_lock["locked_until"]:
                lock_str = row_lock["locked_until"]
                try:
                    lock_dt = datetime.strptime(lock_str, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() < lock_dt:
                        return False
                except Exception:
                    pass
        except Exception:
            pass
    return True
