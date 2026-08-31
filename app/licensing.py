"""BillBook licensing — one setup = one license.

Model (offline, hardware-bound, single-use):
--------------------------------------------
1. Every install derives a stable **Setup ID** from the machine it runs on
   (Windows MachineGuid / macOS IOPlatformUUID / Linux machine-id, with a
   MAC-address fallback). It is displayed to the operator as
   ``XXXX-XXXX-XXXX-XXXX`` and is the same after reinstalls on the same
   machine, but different on any other machine.

2. The owner issues a **license key** per Setup ID using
   ``scripts/generate_license.py`` and the owner's Ed25519 PRIVATE key
   (kept off-repo). The key is a signed binary blob:

       BBL1.<base64url( payload (21 B) || ed25519 signature (64 B) )>

       payload = ver(1) | setup_id(8) | license_no(4) | issued_at(4) |
                 expires_at(4, 0 = perpetual)

3. The app embeds only the PUBLIC half of the signing key, so a leaked
   setup cannot mint licenses. A license only verifies on the Setup ID it
   was issued for — sharing the installer OR a copy of an activated
   database with another machine fails the fingerprint check and the app
   stays locked ("one license can be used one time only", by construction).

4. The stored license is re-verified against the LIVE machine fingerprint
   on every request (signature verification itself is cached per
   (key, setup_id) pair), so moving the data directory to another PC — or
   restoring a foreign backup — locks the app. Expiry (if the license is
   time-limited) is checked against the wall clock on every request.

Threat model / accepted risks:
- Determined attackers with debugger access can patch the binary; this
  feature targets casual sharing of installers and data folders.
- Clock rollback can extend an expiring license until the clock catches
  up again (offline apps cannot do better without a phone-home server).
"""
import base64
import hashlib
import json
import logging
import platform as _platform
import re
import struct
import subprocess
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from . import db

logger = logging.getLogger(__name__)

# ─── Owner signing key (PUBLIC half only — the private key never ships) ─────
# Generated once by the owner via `python scripts/generate_license.py --init`.
# Regenerating the keypair invalidates every previously issued license.
_PUBLIC_KEY_B64 = "5jZOnJflhRo2wfYxHkpKcNXXSK7LfVmwzZUAZB/9SBQ="

# License blob layout: ver | setup_id | license_no | issued_at | expires_at
_FORMAT = ">B8sIII"
_PAYLOAD_LEN = struct.calcsize(_FORMAT)      # 21
_SIG_LEN = 64
_KEY_PREFIX = "BBL1"

# Reasons a license is not active (surfaced to the UI).
R_MISSING = "missing"            # no license stored yet
R_INVALID = "invalid"            # malformed / bad signature / wrong version
R_MACHINE = "machine_mismatch"   # valid license, but for another machine
R_EXPIRED = "expired"            # valid license, time limit passed


# ─── Machine fingerprint → Setup ID ─────────────────────────────────────────

def _read_windows_machine_guid() -> str:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(guid)
    except Exception:
        return ""


def _read_macos_platform_uuid() -> str:
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _read_linux_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            val = Path(p).read_text(encoding="utf-8", errors="ignore").strip()
            if val:
                return val
        except Exception:
            continue
    return ""


def _read_mac_addresses() -> str:
    """Stable fallback: sorted list of non-loopback interface MACs."""
    macs = []
    try:
        net = Path("/sys/class/net")
        if net.is_dir():
            for iface in sorted(net.iterdir()):
                try:
                    addr = (iface / "address").read_text().strip().lower()
                    if addr and addr != "00:00:00:00:00:00":
                        macs.append(addr)
                except Exception:
                    continue
    except Exception:
        pass
    return ",".join(macs)


@lru_cache(maxsize=1)
def _machine_fingerprint() -> str:
    """A stable per-machine string (best effort, see module docstring)."""
    raw = ""
    if sys.platform == "win32":
        raw = _read_windows_machine_guid()
    elif sys.platform == "darwin":
        raw = _read_macos_platform_uuid()
    else:
        raw = _read_linux_machine_id()
    if not raw:
        macs = _read_mac_addresses()
        if macs:
            raw = "macs:" + macs
        else:
            raw = "host:" + (_platform.node() or "unknown") + ":" + sys.platform
    return hashlib.sha256(("billbook-setup-v1:" + raw).encode()).hexdigest()


def setup_id() -> str:
    """The Setup ID of THIS machine, formatted XXXX-XXXX-XXXX-XXXX."""
    f = _machine_fingerprint()
    h = f[:16].upper()
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


def _normalize_sid(value: str) -> str:
    """Normalize a Setup ID to 16 uppercase hex chars (dashes optional)."""
    return re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).upper()


# ─── License key encoding / verification ────────────────────────────────────

def _public_key() -> Ed25519PublicKey:
    """Derive the public key object from the embedded key constant.

    Re-derived (and cached) whenever ``_PUBLIC_KEY_B64`` changes so tests
    can inject a test keypair by monkeypatching the constant — there is NO
    runtime/env override path in production.
    """
    global _pub_cache
    if _pub_cache[0] != _PUBLIC_KEY_B64:
        raw = base64.b64decode(_PUBLIC_KEY_B64)
        _pub_cache = (_PUBLIC_KEY_B64, Ed25519PublicKey.from_public_bytes(raw))
    return _pub_cache[1]


_pub_cache = (None, None)


def make_license_key(private_key_pem: bytes, setup_id_value: str,
                     license_no: int, issued_at: int,
                     expires_at: int | None) -> str:
    """Issue a license key (owner-side helper — needs the PRIVATE key).

    Used by scripts/generate_license.py and the test suite. The app itself
    only ever verifies: signing requires the private key, which is never
    bundled with the app.
    """
    priv = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise ValueError("Not an Ed25519 private key")
    sid_hex = _normalize_sid(setup_id_value)
    if len(sid_hex) != 16:
        raise ValueError(f"Setup ID must be 16 hex chars, got: {setup_id_value!r}")
    payload = struct.pack(
        _FORMAT, 1, bytes.fromhex(sid_hex),
        int(license_no), int(issued_at), int(expires_at or 0),
    )
    sig = priv.sign(payload)
    return _KEY_PREFIX + "." + base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def _normalize_key(raw: str) -> str:
    """Strip everything a human might paste in: the BBL1 tag (any case,
    '.' or '-' separator), whitespace, line wraps and base64 padding.

    NOTE: '-' and '_' are NOT stripped from the body — they are literal
    characters of the base64url alphabet."""
    s = str(raw or "").strip()
    low = s.lower()
    for tag in (_KEY_PREFIX.lower() + ".", _KEY_PREFIX.lower() + "-"):
        if low.startswith(tag):
            s = s[len(tag):]
            break
    else:
        if low.startswith(_KEY_PREFIX.lower()):
            s = s[len(_KEY_PREFIX):]
    return re.sub(r"[\s=]+", "", s)


def verify_license_key(raw_key: str) -> tuple[bool, dict, str]:
    """Verify a pasted license key against THIS machine.

    Returns (ok, payload, reason). ``ok`` is True only when the signature
    is valid AND the license was issued for this machine's Setup ID AND it
    has not expired. ``payload`` is {no, iat, exp} on success.
    """
    try:
        s = _normalize_key(raw_key)
        blob = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        if len(blob) != _PAYLOAD_LEN + _SIG_LEN:
            return False, {}, R_INVALID
        payload_b, sig_b = blob[:_PAYLOAD_LEN], blob[_PAYLOAD_LEN:]
        _public_key().verify(sig_b, payload_b)  # raises InvalidSignature
        ver, sid_b, no, iat, exp = struct.unpack(_FORMAT, payload_b)
        if ver != 1:
            return False, {}, R_INVALID
        if sid_b.hex().upper() != _normalize_sid(setup_id()):
            return False, {"no": no, "iat": iat, "exp": exp or None}, R_MACHINE
        payload = {"no": no, "iat": iat, "exp": exp or None}
        if exp and time.time() > exp:
            return False, payload, R_EXPIRED
        return True, payload, ""
    except InvalidSignature:
        return False, {}, R_INVALID
    except Exception:
        return False, {}, R_INVALID


# ─── Activation state (persisted in the settings table) ─────────────────────

# Cache of the last verification: ((license_key, setup_id) -> (ok, payload,
# reason)). Re-verified only when the stored key or the machine changes;
# expiry is re-checked against the wall clock on every license_state() call.
_state_cache: tuple | None = None


def _reset_cache():
    global _state_cache
    _state_cache = None


def _license_display(payload: dict) -> dict:
    exp = payload.get("exp")
    return {
        "no": payload.get("no"),
        "issued_at": datetime.fromtimestamp(payload["iat"]).isoformat(timespec="seconds")
        if payload.get("iat") else None,
        "expires_at": datetime.fromtimestamp(exp).isoformat(timespec="seconds")
        if exp else None,
        "perpetual": not exp,
    }


def license_state() -> dict:
    """Full license state for the UI + middleware."""
    global _state_cache
    sid = setup_id()
    key = db.get_setting("license_key", "")
    if not key:
        return {"required": True, "activated": False, "setup_id": sid,
                "license": None, "reason": R_MISSING}
    cache_key = (key, sid)
    if _state_cache and _state_cache[0] == cache_key:
        ok, payload, reason = _state_cache[1]
    else:
        ok, payload, reason = verify_license_key(key)
        _state_cache = (cache_key, (ok, payload, reason))
    if ok:
        # Expiry is re-checked against the wall clock on EVERY call — the
        # cached verification only covers the (expensive) signature check,
        # so a license that expires while the app runs locks it live.
        if payload.get("exp") and time.time() > payload["exp"]:
            return {"required": True, "activated": False, "setup_id": sid,
                    "license": _license_display(payload), "reason": R_EXPIRED}
        return {"required": True, "activated": True, "setup_id": sid,
                "license": _license_display(payload), "reason": None}
    # Keep the stored license info for display when it exists but is not
    # usable (expired / belongs to another machine).
    display = _license_display(payload) if payload.get("iat") else None
    return {"required": True, "activated": False, "setup_id": sid,
            "license": display, "reason": reason or R_INVALID}


def is_activated() -> bool:
    """Fast gate used by the middleware and the setup endpoints."""
    try:
        return license_state()["activated"]
    except Exception:
        # Never crash the request pipeline over licensing internals —
        # fail closed (locked) but log loudly.
        logger.exception("license_state() failed — treating as unlicensed")
        return False


def activate(raw_key: str) -> tuple[bool, dict, str]:
    """Validate + persist a license key for this machine. Idempotent:
    activating a new (e.g. renewed) key on the same machine replaces the
    stored one.
    """
    if not str(raw_key or "").strip():
        return False, {}, R_MISSING
    ok, payload, reason = verify_license_key(raw_key)
    if not ok:
        return False, payload, reason
    db.set_setting("license_key", str(raw_key).strip())
    db.set_setting("license_payload", json.dumps(payload))
    db.set_setting("license_activated_at",
                   datetime.now().isoformat(timespec="seconds"))
    _reset_cache()
    try:
        db.log_activity(
            "license_activated", "system", None,
            f"License #{payload.get('no')} activated for setup {setup_id()}",
            {"license_no": payload.get("no"), "expires_at": payload.get("exp")},
        )
    except Exception:
        pass  # activity log must never block activation
    logger.info("License #%s activated (expires %s)",
                payload.get("no"), payload.get("exp") or "never")
    return True, payload, ""


# ─── License vs. backups (v8.19: "license never travels in backups") ────────
#
# Backups are raw SQLite snapshots of the whole DB, and the license lives in
# the settings table — so without these helpers a backup would carry the
# license key to whatever machine restores it. That is both a leak vector and
# a support trap: restoring PC-A's backup on licensed PC-B would WIPE PC-B's
# own license and replace it with PC-A's (machine_mismatch -> locked app).
#
# Policy implemented here:
#   * create_backup() SCRUBS license_* rows from the backup file ->
#     backups are pure data, safe to move between machines.
#   * restore_backup() re-applies THIS machine's license rows after the
#     overwrite -> a licensed machine that restores any backup (foreign or
#     made by an older app version) keeps its own license and stays unlocked.
#     An unlicensed machine gains nothing: a scrubbed backup carries no
#     license, and a legacy backup's foreign license fails the fingerprint
#     check exactly as before.

_LICENSE_SETTING_LIKE = "license_%"


def local_license_settings() -> dict:
    """All license_* settings rows of THIS install, as {key: value}.

    Snapshotted before a DB restore so the machine's own license survives
    the restore (see restore_backup in routers/maintenance.py).
    """
    try:
        with db.conn() as c:
            rows = c.execute(
                "SELECT key, value FROM settings WHERE key LIKE ?",
                (_LICENSE_SETTING_LIKE,),
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        logger.exception("local_license_settings() failed — returning {}")
        return {}


def reapply_license_settings(rows: dict) -> int:
    """Re-write saved license_* rows into the live DB (post-restore).

    Returns the number of rows re-applied. Must never raise into the
    restore flow — a failure is logged and swallowed (worst case the
    operator re-activates with their key).
    """
    applied = 0
    try:
        for k, v in (rows or {}).items():
            if not str(k).startswith("license_"):
                continue  # defensive: only license rows belong here
            db.set_setting(str(k), str(v))
            applied += 1
        _reset_cache()
        if applied:
            logger.info("Re-applied %d license setting(s) after DB restore", applied)
    except Exception:
        logger.exception("reapply_license_settings() failed — "
                         "operator may need to re-activate their license")
    return applied


def scrub_license_from_db_file(path) -> int:
    """Delete license_* rows from a STANDALONE DB file (a backup snapshot).

    Opens the file directly (not via db.conn — it may live outside the data
    dir), removes the license rows, commits, and drops any -wal/-shm
    sidecars so the scrubbed main file is authoritative. Returns the number
    of rows removed. A file without a settings table (or without license
    rows) is left untouched and returns 0.
    """
    import sqlite3 as _sqlite3
    p = Path(path)
    try:
        con = _sqlite3.connect(str(p))
        try:
            has_settings = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone()
            if not has_settings:
                return 0
            cur = con.execute(
                "DELETE FROM settings WHERE key LIKE ?", (_LICENSE_SETTING_LIKE,))
            removed = cur.rowcount or 0
            con.commit()
        finally:
            con.close()
        # File-copy backups may carry WAL/SHM sidecars; after our commit+close
        # the main file holds everything, so drop the sidecars if they remain.
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(p) + suffix).unlink(missing_ok=True)
            except Exception:
                pass
        if removed:
            logger.info("Scrubbed %d license row(s) from backup %s", removed, p.name)
        return removed
    except Exception:
        logger.exception("scrub_license_from_db_file(%s) failed", p)
        return 0
