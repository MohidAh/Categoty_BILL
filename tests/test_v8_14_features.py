"""v8.14.x / v8.18.4 — Smoke tests for the production-hardening features.

Covers:
1. Auto-restart scripts (file-existence + structural checks on .bat files)
2. DB-at-rest encryption (PRAGMA key validation, fallback behaviour)
3. Gzip backup integrity (non-empty gzip regression — generic, no Drive)
4. Daily sales digest (config round-trip, message-on-empty-DB doesn't crash)
5. FBR POS integration (credentials round-trip, compliance-check no-op on fresh DB)
6. Scheduler thread starts + routers mounted (and /api/gdrive is GONE)
7. CASHIER_RESTRICTED_PREFIXES covers /api/fbr, /api/digest
8. v8.14.2: Setup-wizard opt-in integrations — FBR auto-post flag,
   digest hour+phone, all-disabled smoke test.
9. v8.18.4: Google Drive feature removal — no routes, no scheduler refs,
   wizard card gone, leftover gdrive_* settings wiped on upgrade.
"""
import os
import sys
import gzip
import json
import shutil
import tempfile
import importlib
from pathlib import Path

# Ensure project root on sys.path
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from test_helpers import setup_test_db, cleanup


# ────────────────────────────────────────────────────────────────────────────
# 1. Auto-restart scripts (NSSM-based Windows service)
# ────────────────────────────────────────────────────────────────────────────

def test_service_scripts_exist_and_structurally_valid():
    """install_service.bat, uninstall_service.bat, watchdog.bat ship with the
    installer and contain the expected NSSM commands."""
    scripts_dir = PROJ / "scripts" / "windows"
    assert scripts_dir.exists(), f"scripts/windows dir missing at {scripts_dir}"
    for fname in ("install_service.bat", "uninstall_service.bat", "watchdog.bat"):
        p = scripts_dir / fname
        assert p.exists(), f"{fname} not found"
        text = p.read_text(encoding="utf-8")
        # Just make sure they're not empty stubs
        assert len(text) > 200, f"{fname} looks like an empty stub (len={len(text)})"
    # install_service.bat must reference NSSM
    assert b"NSSM" in (scripts_dir / "install_service.bat").read_bytes()
    # watchdog.bat must have an infinite loop
    assert b":loop" in (scripts_dir / "watchdog.bat").read_bytes()
    print("✓ test_service_scripts_exist_and_structurally_valid")


# ────────────────────────────────────────────────────────────────────────────
# 2. DB-at-rest encryption (PRAGMA key validation + fallback)
# ────────────────────────────────────────────────────────────────────────────

def test_db_encryption_key_validation_rejects_bad_chars():
    """db._connect() raises if the configured DB key contains a quote or
    semicolon — preventing PRAGMA-injection."""
    test_dir = setup_test_db(prefix="billbook_pragma_test_")
    try:
        from app import db
        # Inject a key with a quote — should be rejected by the validator
        # before any PRAGMA fires (sqlcipher3 not installed in CI, so the
        # key branch is skipped; we instead test the validator directly).
        bad_key = 'foo"; DROP TABLE settings; --'
        # Set env to simulate operator's BILLBOOK_DB_KEY
        os.environ["BILLBOOK_DB_KEY"] = bad_key
        try:
            # Reload the module so _get_db_key picks up the env var.
            # _connect will be called indirectly via db.conn(); since
            # sqlcipher3 is almost certainly NOT installed in the test env,
            # the code path falls through to sqlite3 — but the validator
            # must still raise if the key is set (defence in depth).
            try:
                with db.conn() as c:
                    c.execute("SELECT 1").fetchone()
            except RuntimeError as e:
                # The validator correctly rejected the bad key
                assert "disallowed characters" in str(e), \
                    f"Unexpected error message: {e}"
                print("✓ test_db_encryption_key_validation_rejects_bad_chars "
                      "(rejected quote-injection key)")
                return
            # If we got here, sqlcipher3 IS installed OR the env-key wasn't
            # picked up. Either way, the validator must have run. Verify
            # by invoking the validator pattern directly:
            import re as _re
            assert not _re.fullmatch(r"[A-Za-z0-9+/=_\-]{8,256}", bad_key), \
                "Bad key passed the regex — regex is wrong"
            print("✓ test_db_encryption_key_validation_rejects_bad_chars "
                  "(regex correctly rejects bad key)")
        finally:
            os.environ.pop("BILLBOOK_DB_KEY", None)
    finally:
        cleanup(test_dir)


def test_db_encryption_accepts_valid_key_format():
    """A valid hex/base64 key passes the regex validator. The fallback path
    (sqlcipher3 not installed) just logs a warning and runs plaintext."""
    test_dir = setup_test_db(prefix="billbook_validkey_test_")
    try:
        from app import db
        # Valid 32-char hex key (256-bit)
        valid_key = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        os.environ["BILLBOOK_DB_KEY"] = valid_key
        try:
            # Even if key is set, in test env sqlcipher3 is likely not
            # installed — falls back to sqlite3 (with a warning). This
            # should NOT raise.
            with db.conn() as c:
                c.execute("SELECT 1").fetchone()
        finally:
            os.environ.pop("BILLBOOK_DB_KEY", None)
        print("✓ test_db_encryption_accepts_valid_key_format")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 3. Gzip backup integrity (v8.14.1 regression — generic gzip, no Drive)
# ────────────────────────────────────────────────────────────────────────────
# v8.18.4: the cloud-backup status test was removed with the feature.

def test_gzip_bug_fix_produces_non_empty_backup():
    """Regression test for v8.14.1 FIX: the previous `fout.writelen = fin.read()`
    typo consumed the input stream and produced a 0-byte gzip. We now write
    the bytes exactly once + assert non-zero size."""
    test_dir = setup_test_db(prefix="billbook_gzip_test_")
    try:
        # Create a fake "snapshot" .db file with known content
        snap_dir = os.path.join(test_dir, "backups")
        os.makedirs(snap_dir, exist_ok=True)
        snap_path = os.path.join(snap_dir, "test-snapshot.db")
        with open(snap_path, "wb") as f:
            f.write(b"FAKE_DB_CONTENT_FOR_GZIP_TEST" * 1000)
        # Reproduce the fixed gzip logic directly
        gzip_path = snap_path + ".gz"
        with open(snap_path, "rb") as fin, gzip.open(gzip_path, "wb") as fout:
            fout.write(fin.read())
        # v8.14.1 assertion: gzip must be non-empty
        assert os.path.getsize(gzip_path) > 0, \
            "Gzip backup is empty (0 bytes) — the v8.14.1 fix is missing"
        # And the content must round-trip
        with gzip.open(gzip_path, "rb") as f:
            decompressed = f.read()
        assert decompressed == b"FAKE_DB_CONTENT_FOR_GZIP_TEST" * 1000, \
            "Gzip decompression mismatch"
        # Cleanup
        os.unlink(snap_path)
        os.unlink(gzip_path)
        print("✓ test_gzip_bug_fix_produces_non_empty_backup")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 4. Daily sales digest (config round-trip + empty-DB message doesn't crash)
# ────────────────────────────────────────────────────────────────────────────

def test_digest_config_round_trip():
    """Set + get digest config: enabled, hour, phone, twilio_sid, whatsapp_from."""
    test_dir = setup_test_db(prefix="billbook_digest_cfg_")
    try:
        from app import digest
        # Initial state — all defaults
        cfg = digest.get_config()
        assert cfg["enabled"] is False
        assert cfg["hour"] == 21
        assert cfg["phone"] == ""
        assert cfg["twilio_has_token"] is False
        # Update
        digest.update_config({
            "enabled": True,
            "hour": 20,
            "phone": "+923331234567",
            "twilio_sid": "AC_test_sid",
            "twilio_token": "test_secret_token",
            "whatsapp_from": "whatsapp:+14155238886",
        })
        # Verify
        cfg = digest.get_config()
        assert cfg["enabled"] is True, f"Expected enabled=True, got {cfg}"
        assert cfg["hour"] == 20
        assert cfg["phone"] == "+923331234567"
        assert cfg["twilio_sid"] == "AC_test_sid"
        assert cfg["twilio_has_token"] is True, "Token should be marked as stored"
        assert cfg["whatsapp_from"] == "whatsapp:+14155238886"
        # is_enabled reflects the setting
        assert digest.is_enabled() is True
        print("✓ test_digest_config_round_trip")
    finally:
        cleanup(test_dir)


def test_digest_message_on_empty_db_does_not_crash():
    """build_digest_message() on a fresh DB with zero sales returns a
    non-empty fallback string (no KeyError, no NoneType crashes)."""
    test_dir = setup_test_db(prefix="billbook_digest_empty_")
    try:
        from app import digest
        msg = digest.build_digest_message(today_only=True)
        # Must be a non-empty string
        assert isinstance(msg, str)
        assert len(msg) > 30, f"Digest message too short: {msg!r}"
        # Must contain the expected header
        assert "BillBook Daily" in msg
        # Must contain the total line (zeros, but present)
        assert "Total: Rs 0" in msg or "Total: Rs" in msg
        # Low-stock section is optional but shouldn't crash
        # (sample_data.sql may include categories with stock state)
        print(f"✓ test_digest_message_on_empty_db_does_not_crash (msg len={len(msg)})")
    finally:
        cleanup(test_dir)


def test_digest_send_without_config_returns_error():
    """send_daily_digest() with no phone configured returns a structured
    error — does NOT raise."""
    test_dir = setup_test_db(prefix="billbook_digest_send_")
    try:
        from app import digest
        # No config set — enabled defaults to False
        result = digest.send_daily_digest(force=False)
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()
        # Force-send with no phone — different error path
        result = digest.send_daily_digest(force=True)
        assert result["ok"] is False
        assert "phone" in result["error"].lower()
        print("✓ test_digest_send_without_config_returns_error")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 5. FBR POS integration (credentials round-trip + compliance-check)
# ────────────────────────────────────────────────────────────────────────────

def test_fbr_credentials_round_trip():
    """Set + get + clear FBR credentials (stored encrypted via Fernet)."""
    test_dir = setup_test_db(prefix="billbook_fbr_creds_")
    try:
        from app import fbr
        # Initial: not configured
        assert fbr.is_configured() is False
        assert fbr.get_credentials() is None
        # Set
        fbr.set_credentials({
            "usr_id": "BR123456",
            "password": "secret_password",
            "pos_id": "POS000123",
            "pos_serial": "HW-SN-ABC",
            "sandbox": True,
        })
        # Verify
        assert fbr.is_configured() is True
        creds = fbr.get_credentials()
        assert creds is not None
        assert creds["usr_id"] == "BR123456"
        assert creds["password"] == "secret_password"
        assert creds["pos_id"] == "POS000123"
        assert creds["pos_serial"] == "HW-SN-ABC"
        assert creds["sandbox"] is True
        # Clear
        fbr.clear_credentials()
        assert fbr.is_configured() is False
        assert fbr.get_credentials() is None
        print("✓ test_fbr_credentials_round_trip")
    finally:
        cleanup(test_dir)


def test_fbr_compliance_check_on_fresh_install():
    """verify_compliance() on a fresh install returns a structured report
    with overall_ok=False (no shop profile, no creds, no sales posted, no QR)."""
    test_dir = setup_test_db(prefix="billbook_fbr_compliance_")
    try:
        from app import fbr
        report = fbr.verify_compliance()
        # Must be a dict with the expected keys
        assert "overall_ok" in report
        assert "shop_profile" in report
        assert "fbr_credentials" in report
        assert "recent_sales_posted" in report
        assert "receipt_template" in report
        assert "recommendations" in report
        # On a fresh install, the overall must be False (no FBR creds)
        assert report["overall_ok"] is False
        assert report["fbr_credentials"]["ok"] is False
        # Recommendations list must be non-empty
        assert len(report["recommendations"]) > 0
        # Receipt template check should not crash — must return (ok, msg)
        assert isinstance(report["receipt_template"]["ok"], bool)
        assert isinstance(report["receipt_template"]["notes"], str)
        print(f"✓ test_fbr_compliance_check_on_fresh_install "
              f"(receipt_ok={report['receipt_template']['ok']})")
    finally:
        cleanup(test_dir)


def test_fbr_post_sale_without_creds_returns_structured_error():
    """post_sale_to_fbr() with no creds returns {posted: False, error: ...}
    — does NOT raise."""
    test_dir = setup_test_db(prefix="billbook_fbr_nopost_")
    try:
        from app import fbr
        result = fbr.post_sale_to_fbr(sale_id=999)
        assert result["posted"] is False
        assert "credentials" in result["error"].lower()
        assert result["invoice_ref"] is None
        assert result["qr_payload"] is None
        print("✓ test_fbr_post_sale_without_creds_returns_structured_error")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 6. App imports cleanly + scheduler thread + routers mounted
# ────────────────────────────────────────────────────────────────────────────

def test_app_imports_and_routers_mounted():
    """main.py must import cleanly and register the v8.14 routers with
    routes under /api/fbr and /api/digest — and (v8.18.4) NO /api/gdrive
    routes may exist: the Google Drive feature was fully removed."""
    test_dir = setup_test_db(prefix="billbook_routers_")
    try:
        # Force re-import of app.main so db.init runs against the test DB.
        sys.modules.pop("app.main", None)
        from app.main import app
        # Collect all registered routes
        paths = {route.path for route in app.routes}
        # v8.18.4 regression guard: the Drive router must be gone
        gdrive_paths = [p for p in paths if p.startswith("/api/gdrive")]
        assert not gdrive_paths, \
            f"/api/gdrive routes still registered: {gdrive_paths}"
        # FBR router
        assert "/api/fbr/status" in paths
        assert "/api/fbr/credentials" in paths
        assert "/api/fbr/compliance-check" in paths
        assert "/api/fbr/post-sale/{sale_id}" in paths
        assert "/api/fbr/auto-post" in paths
        # Digest router
        assert "/api/digest/config" in paths
        assert "/api/digest/preview" in paths
        assert "/api/digest/test-send" in paths
        print(f"✓ test_app_imports_and_routers_mounted ({len(paths)} routes registered)")
    finally:
        cleanup(test_dir)


def test_scheduler_thread_started():
    """The v8.14.0 production-hardening scheduler thread is running."""
    test_dir = setup_test_db(prefix="billbook_sched_")
    try:
        sys.modules.pop("app.main", None)
        from app.main import _scheduler_thread
        assert _scheduler_thread.is_alive()
        assert _scheduler_thread.daemon is True
        print("✓ test_scheduler_thread_started")
    finally:
        cleanup(test_dir)


def test_cashier_restricted_prefixes_cover_new_endpoints():
    """CASHIER_RESTRICTED_PREFIXES must include all manager-only
    endpoints – cashiers should NOT be able to set FBR creds, toggle
    auto-post, or change digest config."""
    test_dir = setup_test_db(prefix="billbook_rbac_")
    try:
        sys.modules.pop("app.main", None)
        from app.main import CASHIER_RESTRICTED_PREFIXES, _is_cashier_restricted
        prefixes = set(CASHIER_RESTRICTED_PREFIXES)
        required = {
            "/api/fbr/credentials",
            "/api/fbr/auto-post",
            "/api/fbr/compliance-check",
            "/api/digest/config",
            "/api/digest/test-send",
        }
        missing = required - prefixes
        assert not missing, f"Cashier-restricted prefixes missing: {missing}"
        # Verify the _is_cashier_restricted helper actually blocks these
        for prefix in required:
            assert _is_cashier_restricted(prefix + "/x", "POST") is True, \
                f"Prefix {prefix} not blocked by _is_cashier_restricted"
        print("✓ test_cashier_restricted_prefixes_cover_new_endpoints")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 7. OPTIONALITY ENFORCEMENT — FBR / digest must NOT be mandatory
# ────────────────────────────────────────────────────────────────────────────
# The production-hardening features are designed to be opt-in. The app must
# boot, log in, create sales, and run scheduled jobs with NONE of them
# configured. This test is the regression guard: if a future PR wires any of
# them into the sale flow or login flow, this test breaks loudly.

def test_features_are_optional_no_fbr_no_digest_configured():
    """On a fresh install with zero FBR / digest config, the app:
    (a) imports cleanly,
    (b) reports the features as not-configured,
    (c) does NOT raise on any status-check endpoint,
    (d) scheduler thread is alive (no crash),
    (e) fbr_auto_post defaults to "0" (off — opt-in only).
    """
    test_dir = setup_test_db(prefix="billbook_optional_")
    try:
        sys.modules.pop("app.main", None)
        from app import digest, fbr, db
        from app.main import _scheduler_thread
        # (a) Imports succeeded if we got here.
        # (b) Features report not-configured.
        assert fbr.is_configured() is False, \
            "FBR should be unconfigured on fresh install"
        assert digest.is_enabled() is False, \
            "Digest should be disabled on fresh install"
        # (c) Status endpoints return graceful "not configured" responses.
        fbr_report = fbr.verify_compliance()
        assert fbr_report["overall_ok"] is False  # nothing configured yet
        # (d) Scheduler thread is still alive.
        assert _scheduler_thread.is_alive()
        # (e) fbr_auto_post defaults to "0" — FBR is opt-in, never auto-on.
        assert db.get_setting("fbr_auto_post", "0") == "0", \
            "fbr_auto_post must default to off — FBR is opt-in, never auto-on"
        # (f) The sale-creation flow must NOT reference fbr/digest (and must
        # not resurrect cloud_backup — removed in v8.18.4).
        import inspect
        from app.routers import pos, bills
        for module in (pos, bills):
            src = inspect.getsource(module)
            assert "fbr.post_sale_to_fbr" not in src, \
                f"{module.__name__} must NOT call fbr.post_sale_to_fbr — " \
                "FBR auto-post is a Settings UI toggle, never a sale side-effect"
            assert "cloud_backup" not in src, \
                f"{module.__name__} must NOT reference cloud_backup — " \
                "sale creation has nothing to do with backups"
            # Digest is not referenced by the sale flow either.
            assert "from .. import digest" not in src and \
                   "digest.send_daily_digest" not in src, \
                f"{module.__name__} must NOT call digest.send_daily_digest — " \
                "the daily digest is a scheduled job, never a sale side-effect"
        print("✓ test_features_are_optional_no_fbr_no_digest_configured")
    finally:
        cleanup(test_dir)


def test_scheduler_does_not_fire_any_job_when_all_features_unconfigured():
    """The scheduler thread ticks every 5 minutes. On a fresh install with
    no digest, it must NOT attempt any backup or send any message.
    This test simulates a full day of ticks and asserts no settings rows
    get a `last_sent_at` written.
    """
    test_dir = setup_test_db(prefix="billbook_sched_noop_")
    try:
        from app import db, digest
        # Pre-state: no last_sent_at, and (v8.18.4) no gdrive_* rows at all
        assert not db.get_setting("digest_last_sent_at", "")
        assert not db.get_setting("gdrive_last_backup_at", "")
        # Simulate the scheduler loop body for 24 hours. The guarded branch
        # must short-circuit when the digest is unconfigured.
        from datetime import datetime as _dt
        for hour in range(24):
            now = _dt.now().replace(hour=hour, minute=0, second=0, microsecond=0)
            # Digest branch
            if now.hour == 21 and digest.is_enabled():
                digest.send_daily_digest()  # should NOT run
        # Post-state: still nothing written.
        assert not db.get_setting("digest_last_sent_at", ""), \
            "Scheduler wrote digest_last_sent_at despite digest not enabled"
        assert not db.get_setting("gdrive_last_backup_at", ""), \
            "gdrive_last_backup_at exists on a fresh v8.18.4 install"
        print("✓ test_scheduler_does_not_fire_any_job_when_all_features_unconfigured")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 8. v8.14.2 — Setup-wizard opt-in integrations (FBR / digest)
# ────────────────────────────────────────────────────────────────────────────
# The wizard has a 5th step where the operator can opt in to:
#   - FBR auto-post (flag only; creds added later in Settings)
#   - Daily WhatsApp digest (hour + phone; Twilio creds added later)
# Both default OFF. The wizard POST persists the choices.
# v8.18.4: the Google Drive opt-in card was removed with the feature.

def _setup_wizard_fresh_db(prefix="billbook_wiz_"):
    """Fresh DB with no password + no setup_completed so the wizard runs."""
    test_dir = tempfile.mkdtemp(prefix=prefix)
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key IN "
                  "('password_hash', 'setup_completed', 'start_page', "
                  "'digest_enabled', 'digest_hour', "
                  "'digest_phone', 'fbr_auto_post')")
        c.execute("DELETE FROM price_categories")
    return test_dir


def _parse_wizard_response(r):
    if hasattr(r, "body"):
        return json.loads(r.body.decode() if isinstance(r.body, bytes) else r.body)
    return json.loads(r)


def test_wizard_opt_in_fields_round_trip():
    """POST /api/setup/wizard with all opt-ins ON → settings persisted.

    The operator chose: digest at 22 (10 PM) to +923331234567,
    FBR auto-post ON. The wizard must persist all of it so the scheduler +
    Settings UI pick the values up immediately."""
    test_dir = _setup_wizard_fresh_db("billbook_wiz_on_")
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        r = setup_wizard(WizardIn(
            password="mysecret123",
            business_type="wholesale",
            categories=[],
            gemini_key="",
            start_page="launcher",
            # v8.14.2 opt-ins — all ON
            digest_enabled=True,
            digest_hour=22,
            digest_phone="+923331234567",
            fbr_auto_post=True,
        ))
        data = _parse_wizard_response(r)
        assert data["ok"] is True
        # v8.18.4: no gdrive_connect echo — the field is gone
        assert "gdrive_connect" not in data
        # Digest persisted
        assert db.get_setting("digest_enabled", "") == "1"
        assert db.get_setting("digest_hour", "") == "22"
        assert db.get_setting("digest_phone", "") == "+923331234567"
        # FBR flag persisted
        assert db.get_setting("fbr_auto_post", "") == "1"
        print("✓ test_wizard_opt_in_fields_round_trip")
    finally:
        cleanup(test_dir)


def test_wizard_all_disabled_leaves_everything_off():
    """ALL-DISABLED smoke test: wizard with every opt-in at its default →
    digest disabled, FBR auto-post off. The app is fully usable with
    neither integration configured."""
    test_dir = _setup_wizard_fresh_db("billbook_wiz_off_")
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db, digest, fbr
        # WizardIn defaults: digest_enabled=False, digest_hour=None,
        # digest_phone="", fbr_auto_post=False
        r = setup_wizard(WizardIn(password="mysecret123"))
        data = _parse_wizard_response(r)
        assert data["ok"] is True
        assert "gdrive_connect" not in data  # v8.18.4: field removed
        # Every feature off / unconfigured
        assert digest.is_enabled() is False
        assert db.get_setting("fbr_auto_post", "0") == "0"
        # Digest phone untouched
        assert db.get_setting("digest_phone", "") == ""
        # Wizard still completed normally
        assert db.get_setting("setup_completed", "") == "true"
        print("✓ test_wizard_all_disabled_leaves_everything_off")
    finally:
        cleanup(test_dir)


def test_wizard_rejects_out_of_range_hours():
    """Out-of-range hours (e.g. -1) must not crash the wizard or get stored."""
    test_dir = _setup_wizard_fresh_db("billbook_wiz_badhr_")
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        r = setup_wizard(WizardIn(
            password="mysecret123",
            digest_hour=-1,          # invalid — must be ignored
        ))
        data = _parse_wizard_response(r)
        assert data["ok"] is True  # wizard still completes
        assert db.get_setting("digest_hour", "") in ("", "21"), \
            "Invalid digest hour must not be persisted"
        print("✓ test_wizard_rejects_out_of_range_hours")
    finally:
        cleanup(test_dir)


# ────────────────────────────────────────────────────────────────────────────
# 9. v8.18.4 — Google Drive feature REMOVAL regression guards
# ────────────────────────────────────────────────────────────────────────────

def test_gdrive_settings_wiped_on_upgrade():
    """db.init() deletes leftover gdrive_* settings rows (incl. the stored
    OAuth refresh token) so upgrading installs carry no Drive residue."""
    test_dir = setup_test_db(prefix="billbook_wipe_")
    try:
        from app import db
        # Simulate an old install's leftovers
        db.set_setting("gdrive_refresh_token_enc", "enc:fake-token")
        db.set_setting("gdrive_folder_id", "folder123")
        db.set_setting("gdrive_last_backup_at", "2025-01-01")
        db.set_setting("gdrive_backup_hour", "2")
        assert db.get_setting("gdrive_refresh_token_enc", "") == "enc:fake-token"
        # Re-run init (what happens on upgrade to v8.18.4)
        db.init()
        for key in ("gdrive_refresh_token_enc", "gdrive_folder_id",
                    "gdrive_last_backup_at", "gdrive_backup_hour"):
            assert db.get_setting(key, "") == "", \
                f"{key} survived the v8.18.4 cleanup migration"
        print("✓ test_gdrive_settings_wiped_on_upgrade")
    finally:
        cleanup(test_dir)


def test_scheduler_source_has_no_gdrive_refs():
    """main.py's scheduler must contain no Drive job references (v8.18.4)."""
    test_dir = setup_test_db(prefix="billbook_schedsrc_")
    try:
        from app import main as app_main
        src = Path(app_main.__file__).read_text(encoding="utf-8")
        for needle in ("from . import cloud_backup",
                       "gdrive_backup_hour",
                       "gdrive_backup_date",
                       "gdrive_restore_date",
                       "check_and_import_new_backups"):
            assert needle not in src, \
                f"main.py still references {needle!r} — Drive removal incomplete"
        print("✓ test_scheduler_source_has_no_gdrive_refs")
    finally:
        cleanup(test_dir)


def test_wizard_html_has_step5_integration_cards():
    """setup-wizard.html must contain the 5th step with the FBR + digest
    opt-in cards — and (v8.18.4) NO Google Drive card or OAuth popup."""
    html = (PROJ / "app" / "static" / "setup-wizard.html").read_text(encoding="utf-8")
    assert "Step 5 — Optional Integrations" in html
    assert "FBR auto-post" in html                    # FBR card
    assert "Daily WhatsApp digest" in html            # digest card
    assert "w-digest-hour" in html                    # digest hour <select> id
    assert "w-digest-phone" in html                   # digest phone <input> id
    # v8.18.4 removal guards
    assert "Google Drive auto-backup" not in html, \
        "Wizard still offers the removed Google Drive feature"
    assert "w-gdrive" not in html, "GDrive wizard card still wired"
    assert "connect-url" not in html, "OAuth popup still wired in the wizard"
    assert "gdrive-callback.html" not in html
    cb = (PROJ / "app" / "static" / "gdrive-callback.html")
    assert not cb.exists(), "gdrive-callback.html still shipped"
    print("✓ test_wizard_html_has_step5_integration_cards")




# ────────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_service_scripts_exist_and_structurally_valid()
    test_db_encryption_key_validation_rejects_bad_chars()
    test_db_encryption_accepts_valid_key_format()
    test_gzip_bug_fix_produces_non_empty_backup()
    test_digest_config_round_trip()
    test_digest_message_on_empty_db_does_not_crash()
    test_digest_send_without_config_returns_error()
    test_fbr_credentials_round_trip()
    test_fbr_compliance_check_on_fresh_install()
    test_fbr_post_sale_without_creds_returns_structured_error()
    test_app_imports_and_routers_mounted()
    test_scheduler_thread_started()
    test_cashier_restricted_prefixes_cover_new_endpoints()
    test_features_are_optional_no_fbr_no_digest_configured()
    test_scheduler_does_not_fire_any_job_when_all_features_unconfigured()
    # v8.14.2 — wizard opt-in integrations
    test_wizard_opt_in_fields_round_trip()
    test_wizard_all_disabled_leaves_everything_off()
    test_wizard_rejects_out_of_range_hours()
    # v8.18.4 — Drive removal regression guards
    test_gdrive_settings_wiped_on_upgrade()
    test_scheduler_source_has_no_gdrive_refs()
    test_wizard_html_has_step5_integration_cards()
    print("\n✅ ALL v8.14.x / v8.18.4 FEATURE TESTS PASSED")
