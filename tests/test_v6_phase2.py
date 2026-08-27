"""v6.0 Phase 2 — Multi-Client Server Readiness tests."""
import os, sys, tempfile, shutil, hashlib
from pathlib import Path
from unittest.mock import Mock
from test_helpers import setup_test_db, cleanup

class MockRequest:
    def __init__(self):
        self.client = Mock()
        self.client.host = "127.0.0.1"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v6p2_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log", "sessions",
                  "expenses", "expense_categories", "recurring_expenses",
                  "cash_drawer", "shifts", "employees",
                  "category_stock_state", "owner_withdrawals",
                  "login_attempts", "devices", "pairing_codes"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)", (name, is_fixed, budget, 1, sort_order),
            )
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_pairing_code_generation():
    """GET /api/devices/code generates a 6-digit code with 5-min expiry."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_pairing_code
        from fastapi import Request
        # Create a mock request (not needed for the function logic)
        result = generate_pairing_code(request=None, role="cashier")
        assert "code" in result
        assert len(result["code"]) == 6
        assert result["code"].isdigit()
        assert result["role"] == "cashier"
        assert result["expires_in"] == 300
    finally:
        cleanup(test_dir)


def test_device_pairing_full_flow():
    """Full pairing flow: generate code → pair → get token → verify token."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_pairing_code, pair_device, verify_device_token

        # Step 1: Generate a pairing code
        code_result = generate_pairing_code(request=None, role="manager")
        code = code_result["code"]

        # Step 2: Pair a device with the code
        pair_result = pair_device({"code": code, "device_name": "Test Phone"}, request=MockRequest())
        assert "token" in pair_result
        assert pair_result["role"] == "manager"
        assert pair_result["device_id"] > 0

        # Step 3: Verify the token works
        device = verify_device_token(pair_result["token"])
        assert device is not None
        assert device["name"] == "Test Phone"
        assert device["role"] == "manager"

        # Step 4: Verify the token is hashed in the DB (not stored in plaintext)
        token_hash = hashlib.sha256(pair_result["token"].encode()).hexdigest()
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT token_hash FROM devices WHERE id=?", (pair_result["device_id"],)
            ).fetchone()
        assert row["token_hash"] == token_hash
        assert row["token_hash"] != pair_result["token"]  # Not plaintext
    finally:
        cleanup(test_dir)


def test_pairing_code_single_use():
    """A pairing code can only be used once."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_pairing_code, pair_device
        from fastapi import HTTPException

        code_result = generate_pairing_code(request=None, role="cashier")
        code = code_result["code"]

        # First use: success
        pair_device({"code": code, "device_name": "Phone 1"}, request=MockRequest())

        # Second use: should fail
        try:
            pair_device({"code": code, "device_name": "Phone 2"}, request=MockRequest())
            assert False, "Expected HTTPException for reused code"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        cleanup(test_dir)


def test_pairing_code_expiry():
    """An expired pairing code cannot be used."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import pair_device
        from fastapi import HTTPException
        from app import db
        from datetime import datetime, timedelta

        # Insert an expired code directly
        expired = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        with db.conn() as c:
            c.execute(
                "INSERT INTO pairing_codes(code, role, expires_at) VALUES(?,?,?)",
                ("999999", "cashier", expired),
            )

        try:
            pair_device({"code": "999999", "device_name": "Test"}, request=MockRequest())
            assert False, "Expected HTTPException for expired code"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        cleanup(test_dir)


def test_device_revoke():
    """Revoking a device kills its token."""
    test_dir = setup_test_db()
    try:
        from app.routers.auth import generate_pairing_code, pair_device, verify_device_token, revoke_device

        code_result = generate_pairing_code(request=None, role="cashier")
        pair_result = pair_device({"code": code_result["code"], "device_name": "Test"}, request=MockRequest())

        # Verify token works before revoke
        assert verify_device_token(pair_result["token"]) is not None

        # Revoke
        revoke_device(pair_result["device_id"])

        # Verify token no longer works
        assert verify_device_token(pair_result["token"]) is None
    finally:
        cleanup(test_dir)


def test_lan_mode_setting_default():
    """lan_mode setting defaults to false."""
    test_dir = setup_test_db()
    try:
        from app import db
        assert db.get_setting("lan_mode", "false") == "false"
    finally:
        cleanup(test_dir)


def test_billbook_data_dir_env():
    """config.py honors BILLBOOK_DATA_DIR env."""
    test_dir = setup_test_db()
    try:
        import importlib
        from app import config
        # Set the env var and reload config
        os.environ["BILLBOOK_DATA_DIR"] = "/tmp/billbook_test_data_dir"
        importlib.reload(config)
        assert str(config.DATA) == "/tmp/billbook_test_data_dir"
        assert str(config.UPLOADS) == "/tmp/billbook_test_data_dir/uploads"
        # Cleanup
        os.environ.pop("BILLBOOK_DATA_DIR", None)
        importlib.reload(config)
    finally:
        cleanup(test_dir)
        shutil.rmtree("/tmp/billbook_test_data_dir", ignore_errors=True)


if __name__ == "__main__":
    test_pairing_code_generation()
    print("✓ test_pairing_code_generation")
    test_device_pairing_full_flow()
    print("✓ test_device_pairing_full_flow")
    test_pairing_code_single_use()
    print("✓ test_pairing_code_single_use")
    test_pairing_code_expiry()
    print("✓ test_pairing_code_expiry")
    test_device_revoke()
    print("✓ test_device_revoke")
    test_lan_mode_setting_default()
    print("✓ test_lan_mode_setting_default")
    test_billbook_data_dir_env()
    print("✓ test_billbook_data_dir_env")
    print("\n✅ ALL PHASE 2 MULTI-CLIENT TESTS PASSED")
