"""Phase 0 PR 7: Security hardening tests.

Verifies:
- 7a: employees.pin_hash (bcrypt) with plaintext pin fallback + migration
- 7b: settings.*_api_key encryption (Fernet) + decrypt_setting_key helper
- 7c: Tauri CSP is restrictive (script-src 'self', no inline)
- 7d (Reviewer 3 critical): password change re-encrypts ALL API keys atomically

Run with: pytest tests/test_security_hardening.py -v
"""
import os
import sys
import tempfile
import shutil
import json
from pathlib import Path

import pytest
from test_helpers import setup_test_db as _setup_test_db, cleanup


def setup_test_db():
    """Custom setup — uses 'test-password-123' as the password (not 'testpass')."""
    test_dir = _setup_test_db()
    from app import db
    from app.security import hash_password
    db.set_setting("password_hash", hash_password("test-password-123"))
    return test_dir

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))




def test_pin_hash_roundtrip():
    """hash_pin + verify_pin round-trip correctly."""
    from app.security import hash_pin, verify_pin
    pin = "1234"
    hashed = hash_pin(pin)
    assert hashed != pin, "Hash should not equal plaintext"
    assert verify_pin(pin, hashed), "verify_pin should return True for correct PIN"
    assert not verify_pin("9999", hashed), "verify_pin should return False for wrong PIN"
    assert not verify_pin("", hashed), "verify_pin should return False for empty PIN"
    assert not verify_pin(pin, ""), "verify_pin should return False for empty hash"


def test_set_employee_pin_writes_pin_hash_not_plaintext():
    """set_employee_pin writes to pin_hash (bcrypt) and NULLs out pin."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Insert an employee with plaintext pin
        with db.conn() as c:
            c.execute(
                "INSERT INTO employees(id, name, role, pin, active) "
                "VALUES(100, 'Test Emp', 'cashier', '5678', 1)"
            )
        # Set PIN via shop helper (should write pin_hash, null pin)
        result = shop.set_employee_pin(100, "9999")
        assert result is True, "set_employee_pin should return True"
        with db.conn() as c:
            row = c.execute("SELECT pin, pin_hash FROM employees WHERE id=100").fetchone()
            assert row["pin"] is None or row["pin"] == "", (
                f"Plaintext pin should be NULL after set_employee_pin, got '{row['pin']}'"
            )
            assert row["pin_hash"] is not None and row["pin_hash"] != "", (
                "pin_hash should be set"
            )
            # Verify the hash is bcrypt (starts with $2b$)
            assert row["pin_hash"].startswith("$2b$"), (
                f"pin_hash should be bcrypt ($2b$...), got {row['pin_hash'][:10]}..."
            )
        # Verify verify_manager_pin works with the new pin_hash
        from app.security import verify_pin
        with db.conn() as c:
            row = c.execute("SELECT pin_hash FROM employees WHERE id=100").fetchone()
        assert verify_pin("9999", row["pin_hash"]), "verify_pin should match the new PIN"
        assert not verify_pin("5678", row["pin_hash"]), "verify_pin should NOT match the old PIN"
    finally:
        cleanup(test_dir)


def test_verify_manager_pin_uses_pin_hash_first():
    """verify_manager_pin checks pin_hash first; plaintext pin is a fallback."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        from app.security import hash_pin
        # Insert a manager with BOTH pin (plaintext) AND pin_hash (bcrypt)
        # for DIFFERENT values — verify the pin_hash wins.
        with db.conn() as c:
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(200, 'Hash Wins', 'manager', '1111', ?, 1)",
                (hash_pin("2222"),),
            )
        # verify with 2222 (the pin_hash value) → should succeed
        result = shop.verify_manager_pin("2222")
        assert result is not None, "verify_manager_pin should succeed with pin_hash value"
        assert result["name"] == "Hash Wins"
        # verify with 1111 (the plaintext pin) → should FAIL (pin_hash takes priority)
        # Note: this assumes the employee does NOT have pin_hash matching 1111
        result_wrong = shop.verify_manager_pin("1111")
        assert result_wrong is None or result_wrong.get("name") != "Hash Wins", (
            "verify_manager_pin should NOT match the plaintext pin when pin_hash is set"
        )
    finally:
        cleanup(test_dir)


def test_verify_manager_pin_falls_back_to_plaintext_pin():
    """If pin_hash is NOT set but plaintext pin is, verify_manager_pin falls back
    with a warning log (backward compat during migration)."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Insert a manager with ONLY plaintext pin (no pin_hash)
        with db.conn() as c:
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(300, 'Legacy Mgr', 'manager', '3333', NULL, 1)"
            )
        # verify with 3333 → should succeed via plaintext fallback
        result = shop.verify_manager_pin("3333")
        assert result is not None, (
            "verify_manager_pin should succeed via plaintext fallback"
        )
        assert result["name"] == "Legacy Mgr"
        # Verify a plaintext_pin_used activity log was written
        with db.conn() as c:
            log = c.execute(
                "SELECT * FROM activity_log WHERE event_type='plaintext_pin_used' "
                "AND entity_id=300 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert log is not None, "plaintext_pin_used activity should be logged"
    finally:
        cleanup(test_dir)


def test_migrate_pin_hash_script_converts_plaintext_to_hash():
    """The migration script converts all plaintext PINs to bcrypt hashes."""
    test_dir = setup_test_db()
    try:
        from app import db
        # Insert 3 employees with plaintext PINs
        with db.conn() as c:
            c.executescript(
                "INSERT INTO employees(id, name, role, pin, active) VALUES(1, 'A', 'manager', '1111', 1);"
                "INSERT INTO employees(id, name, role, pin, active) VALUES(2, 'B', 'cashier', '2222', 1);"
                "INSERT INTO employees(id, name, role, pin, active) VALUES(3, 'C', 'admin', '3333', 1);"
            )
        # Run the migration
        from scripts.migrate_pin_hash import migrate_pin_hash
        migrated, errors = migrate_pin_hash()
        assert migrated >= 3, f"Should migrate at least 3 employees, got {migrated}"
        assert errors == 0
        # Verify all PINs are now hashed
        with db.conn() as c:
            rows = c.execute("SELECT id, pin, pin_hash FROM employees ORDER BY id").fetchall()
        for row in rows:
            assert row["pin"] is None or row["pin"] == "", (
                f"Employee {row['id']} plaintext pin should be NULL after migration"
            )
            assert row["pin_hash"] and row["pin_hash"].startswith("$2b$"), (
                f"Employee {row['id']} pin_hash should be bcrypt, got {row['pin_hash']}"
            )
        # Verify the migration is idempotent (re-run → 0 migrated)
        migrated2, errors2 = migrate_pin_hash()
        assert migrated2 == 0, f"Re-run should migrate 0, got {migrated2}"
    finally:
        cleanup(test_dir)


# ─── 7b: API key encryption ─────────────────────────────────────────────────

def test_encrypt_setting_key_roundtrip():
    """encrypt_setting_key + decrypt_setting_key round-trip correctly."""
    test_dir = setup_test_db()
    try:
        from app.crypto import encrypt_setting_key, decrypt_setting_key
        plaintext = "sk-test-api-key-12345"
        encrypted = encrypt_setting_key("groq_api_key", plaintext)
        assert encrypted != plaintext, "Encrypted should differ from plaintext"
        assert encrypted.startswith("gAAAAA"), "Should have Fernet prefix"
        # Store + decrypt
        from app import db
        db.set_setting("groq_api_key", encrypted)
        decrypted = decrypt_setting_key("groq_api_key")
        assert decrypted == plaintext, (
            f"Decrypted should match original: {decrypted} != {plaintext}"
        )
    finally:
        cleanup(test_dir)


def test_decrypt_setting_key_returns_plaintext_for_unencrypted_values():
    """decrypt_setting_key auto-detects plaintext and returns it as-is
    (backward compat during migration)."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.crypto import decrypt_setting_key
        # Store a plaintext value
        db.set_setting("groq_api_key", "sk-plaintext-key")
        result = decrypt_setting_key("groq_api_key")
        assert result == "sk-plaintext-key", (
            f"Should return plaintext as-is: {result}"
        )
    finally:
        cleanup(test_dir)


def test_migrate_setting_keys_encrypts_plaintext():
    """migrate_setting_keys converts plaintext API keys to encrypted."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.crypto import migrate_setting_keys, decrypt_setting_key, is_encrypted
        # Store plaintext keys
        db.set_setting("groq_api_key", "sk-groq-123")
        db.set_setting("gemini_api_key", "sk-gemini-456")
        # Run migration
        migrated, skipped, errors = migrate_setting_keys()
        assert migrated == 2, f"Should migrate 2 keys, got {migrated}"
        assert errors == 0
        # Verify they're now encrypted
        with db.conn() as c:
            groq = c.execute("SELECT value FROM settings WHERE key='groq_api_key'").fetchone()["value"]
            gem = c.execute("SELECT value FROM settings WHERE key='gemini_api_key'").fetchone()["value"]
        assert is_encrypted(groq), "groq_api_key should be encrypted"
        assert is_encrypted(gem), "gemini_api_key should be encrypted"
        # Verify decrypt still returns the original plaintext
        assert decrypt_setting_key("groq_api_key") == "sk-groq-123"
        assert decrypt_setting_key("gemini_api_key") == "sk-gemini-456"
        # Idempotent: re-run → 0 migrated, 2 skipped
        migrated2, skipped2, errors2 = migrate_setting_keys()
        assert migrated2 == 0
        assert skipped2 == 2
    finally:
        cleanup(test_dir)


# ─── 7c: Tauri CSP ───────────────────────────────────────────────────────────

def test_tauri_csp_is_restrictive():
    """Tauri config has a restrictive CSP that blocks inline scripts and
    restricts connect-src to the local server only."""
    tauri_conf_path = PROJ / "desktop" / "tauri.conf.json"
    if not tauri_conf_path.exists():
        pytest.skip("tauri.conf.json not found")
    conf = json.loads(tauri_conf_path.read_text())
    csp = conf.get("app", {}).get("security", {}).get("csp", "")
    assert csp, "CSP should not be null/empty"
    # script-src must NOT include 'unsafe-inline' or 'unsafe-eval'
    assert "script-src 'self'" in csp, f"script-src should be 'self' only: {csp}"
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], (
        "script-src must NOT include 'unsafe-inline'"
    )
    assert "'unsafe-eval'" not in csp, "CSP must NOT include 'unsafe-eval'"
    # connect-src must restrict to local server
    assert "connect-src 'self'" in csp, f"connect-src should start with 'self': {csp}"
    assert "http://127.0.0.1:8000" in csp, "connect-src should allow local server"
    # frame-ancestors must be 'none' (clickjacking protection)
    assert "frame-ancestors 'none'" in csp, "frame-ancestors should be 'none'"
    # img-src must allow data: and blob: for the bill image viewer
    assert "img-src 'self' data: blob:" in csp, "img-src should allow data: and blob:"


# ─── 7d (Reviewer 3 critical): password change re-encrypts API keys ────────

def test_password_change_reencrypts_api_keys():
    """Reviewer 3 critical fix: when the password changes, ALL stored API keys
    must be re-encrypted with the new Fernet key — else they become unreadable.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.crypto import encrypt_setting_key, decrypt_setting_key
        from app.routers.auth import change_password, ChangePasswordIn

        # Store an encrypted API key with the ORIGINAL password
        db.set_setting("groq_api_key", encrypt_setting_key("groq_api_key", "sk-original-secret"))
        # Verify it decrypts correctly with the original password
        assert decrypt_setting_key("groq_api_key") == "sk-original-secret"

        # Change the password
        payload = ChangePasswordIn(old_password="test-password-123", new_password="new-password-456")
        result = change_password(payload)
        assert result.get("ok") is True, f"Password change failed: {result}"
        assert result.get("reencrypted_keys") >= 1, (
            f"At least 1 key should be re-encrypted, got {result.get('reencrypted_keys')}"
        )

        # Verify the API key can STILL be decrypted with the NEW password
        decrypted = decrypt_setting_key("groq_api_key")
        assert decrypted == "sk-original-secret", (
            f"API key should still decrypt to original after password change: {decrypted}"
        )
    finally:
        cleanup(test_dir)


def test_password_change_rolls_back_on_reencryption_failure(monkeypatch):
    """If re-encryption fails mid-transaction, the password change rolls back
    (old password + old keys remain valid)."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.crypto import encrypt_setting_key, decrypt_setting_key
        from app.routers.auth import change_password, ChangePasswordIn

        # Store an encrypted API key
        db.set_setting("groq_api_key", encrypt_setting_key("groq_api_key", "sk-secret"))

        # Monkey-patch Fernet.encrypt to raise (simulate re-encryption failure)
        from cryptography.fernet import Fernet
        original_encrypt = Fernet.encrypt

        def bomb(self, data):
            raise RuntimeError("simulated encryption failure")

        monkeypatch.setattr(Fernet, "encrypt", bomb)

        # Try to change the password — should fail
        payload = ChangePasswordIn(old_password="test-password-123", new_password="new-password-456")
        result = change_password(payload)
        # Should return a 500 error (not ok)
        assert hasattr(result, "status_code"), f"Expected JSONResponse, got {result}"
        assert result.status_code == 500, f"Expected 500, got {result.status_code}"

        # Restore Fernet.encrypt
        monkeypatch.setattr(Fernet, "encrypt", original_encrypt)

        # Verify the OLD password still works
        from app.security import verify_password
        stored = db.get_setting("password_hash", "")
        assert verify_password("test-password-123", stored), (
            "Old password should still work after rollback"
        )
        assert not verify_password("new-password-456", stored), (
            "New password should NOT work after rollback"
        )

        # Verify the API key is still decryptable with the OLD key
        assert decrypt_setting_key("groq_api_key") == "sk-secret", (
            "API key should still decrypt with old key after rollback"
        )
    finally:
        cleanup(test_dir)


def test_password_change_wrong_old_password_returns_403():
    """Changing password with wrong old password → 403."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import change_password, ChangePasswordIn
        payload = ChangePasswordIn(old_password="wrong-password", new_password="new-password-456")
        result = change_password(payload)
        assert hasattr(result, "status_code"), f"Expected JSONResponse, got {result}"
        assert result.status_code == 403
    finally:
        cleanup(test_dir)


def test_password_change_short_new_password_returns_400():
    """Changing password to a short new password → 400."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import change_password, ChangePasswordIn
        payload = ChangePasswordIn(old_password="test-password-123", new_password="short")
        result = change_password(payload)
        assert hasattr(result, "status_code"), f"Expected JSONResponse, got {result}"
        assert result.status_code == 400
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    import traceback
    tests = [
        test_pin_hash_roundtrip,
        test_set_employee_pin_writes_pin_hash_not_plaintext,
        test_verify_manager_pin_uses_pin_hash_first,
        test_verify_manager_pin_falls_back_to_plaintext_pin,
        test_migrate_pin_hash_script_converts_plaintext_to_hash,
        test_encrypt_setting_key_roundtrip,
        test_decrypt_setting_key_returns_plaintext_for_unencrypted_values,
        test_migrate_setting_keys_encrypts_plaintext,
        test_tauri_csp_is_restrictive,
        test_password_change_reencrypts_api_keys,
        test_password_change_rolls_back_on_reencryption_failure,
        test_password_change_wrong_old_password_returns_403,
        test_password_change_short_new_password_returns_400,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  [PASS] {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
