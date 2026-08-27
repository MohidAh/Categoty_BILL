"""BillBook test configuration — shared fixtures for the test suite.

v8.14.1: Enhanced from skeleton to full-featured fixtures. Existing test
files that have their own copy-pasted `setup_test_db()` can now use these
fixtures instead:

    BEFORE (25 lines of boilerplate per file):
        def test_something():
            test_dir = setup_test_db()
            try:
                from app import shop
                result = shop.get_margins()
                assert result["overall_margin"] > 0
            finally:
                cleanup(test_dir)

    AFTER (0 lines of boilerplate):
        def test_something(sample_db):
            from app import shop
            result = shop.get_margins()
            assert result["overall_margin"] > 0

Available fixtures:
  - tmp_db_path: Clean temp DB (empty — no sample data)
  - sample_db: Temp DB with sample_data.sql loaded + stock state rebuilt
  - client: FastAPI TestClient wired to tmp_db_path (no auth)
  - authed_client: TestClient with manager login (password "testpass")
  - cashier_client: TestClient with cashier login (PIN "1234")
  - api_test_db: Like sample_db but also sets password_hash for API tests

NOTE: The 59 existing test files still have their own setup_test_db() —
this conftest provides fixtures for NEW tests and for incremental migration.
Full migration of all 59 files is a future task (see worklog v8.14.0 H8).
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# Ensure project root is on sys.path so `from app import ...` works
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    """Yield a path to a clean temp DB file.

    Sets BILLBOOK_DATA_DIR + config.DATA + db.DB_PATH to point at the temp dir.
    Calls db.init() to create the schema.
    Cleans up on teardown.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Set env var before importing config (in case anything reads it fresh)
    monkeypatch.setenv("BILLBOOK_DATA_DIR", str(data_dir))

    # Patch config + db paths
    from app import config
    monkeypatch.setattr(config, "DATA", data_dir)
    for name in ("UPLOADS", "PAGES", "BACKUPS"):
        sub = data_dir / name.lower()
        sub.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, name, sub)

    from app import db
    db_path = data_dir / "billbook.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init()

    yield str(db_path)


@pytest.fixture()
def sample_db(tmp_db_path):
    """Like tmp_db_path but also loads tests/sample_data.sql + rebuilds stock state.

    Use this for tests that need canonical data (categories, sample bills,
    sample sales) instead of starting from an empty DB.

    Also adds a test manager employee with PIN '1234' (id=99).
    """
    if not SAMPLE_SQL.exists():
        pytest.skip("sample_data.sql not found")
    from app import db, profit
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log", "sessions",
                  "expenses", "expense_categories", "recurring_expenses",
                  "cash_drawer", "shifts", "employees",
                  "owner_withdrawals", "capital_injections", "stock_writeoffs"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Add a manager employee with a known PIN for tests
        c.execute(
            "INSERT OR REPLACE INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
    profit.rebuild_stock_state()
    return tmp_db_path


@pytest.fixture()
def api_test_db(sample_db):
    """Like sample_db but also sets up password_hash for API login tests.

    Sets the owner password to 'testpass' so tests can call:
        client.post('/api/login', json={'password': 'testpass'})
    """
    from app import db
    from app.security import hash_password
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    return sample_db


@pytest.fixture()
def client(tmp_db_path):
    """Yield a FastAPI TestClient wired to the temp DB (no auth — use authed_client for logged-in tests)."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def authed_client(api_test_db):
    """Yield a TestClient with manager login (password 'testpass').

    Requires api_test_db (which sets up the password_hash).
    """
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/login", json={"password": "testpass"})
        assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
        yield c


@pytest.fixture()
def cashier_client(sample_db):
    """Yield a TestClient with cashier login (employee PIN '1234').

    Creates a cashier employee with pin_hash, then logs in via /api/login/staff.
    """
    from fastapi.testclient import TestClient
    from app.main import app, db
    from app.security import hash_pin
    with db.conn() as c:
        c.execute("DELETE FROM employees WHERE id=200")
        c.execute(
            "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
            "VALUES(200, 'Test Cashier', 'cashier', NULL, ?, 1)",
            (hash_pin("1234"),)
        )
    with TestClient(app) as c:
        r = c.post("/api/login/staff", json={"employee_id": 200, "pin": "1234"})
        assert r.status_code == 200, f"Cashier login failed: {r.status_code} {r.text}"
        assert r.json()["role"] == "cashier"
        yield c


# ═══════════════════════════════════════════════════════════════════════════════
# v8.15.0: Shared setup_test_db() + cleanup() helpers for backward compat.
#
# The 59 existing test files each have a ~25-line copy-pasted setup_test_db()
# function. Instead of modifying every test function signature (risky — broke
# 18 files last time), we replace the function BODY with a call to this shared
# helper. This eliminates ~1,400 LOC of duplicated boilerplate WITHOUT touching
# any test function signatures.
#
# Migration pattern per file:
#   BEFORE (25 lines):
#       def setup_test_db():
#           test_dir = tempfile.mkdtemp(prefix="billbook_xxx_")
#           from app import config, db
#           db.DB_PATH = ...
#           ...20 more lines...
#           return test_dir
#
#   AFTER (3 lines):
#       from conftest import shared_setup_test_db as setup_test_db, shared_cleanup as cleanup
#
#   OR (if the file needs custom setup):
#       from conftest import shared_setup_test_db, shared_cleanup
#       def setup_test_db():
#           test_dir = shared_setup_test_db()
#           # custom setup (e.g. db.set_setting("stock_strategy", "permit_negative"))
#           return test_dir
#       def cleanup(test_dir):
#           shared_cleanup(test_dir)
# ═══════════════════════════════════════════════════════════════════════════════

import tempfile as _tempfile
import os as _os
import shutil as _shutil
from pathlib import Path as _Path

_SAMPLE_SQL = _Path(__file__).parent / "sample_data.sql"

# Tables to clear before loading sample data (comprehensive — covers all tables
# that might have leftover data from a previous test run)
_CLEAR_TABLES = (
    "sale_items", "sales", "bill_items", "bills",
    "customers", "price_categories", "suppliers",
    "stock_adjustments", "activity_log", "sessions",
    "expenses", "expense_categories", "recurring_expenses",
    "cash_drawer", "shifts", "employees",
    "owner_withdrawals", "capital_injections", "stock_writeoffs",
    "ezi_pos_imports", "pos_expense_imports", "pos_imports",
    "loyalty_redemptions", "customer_payments",
    "held_orders", "quotations", "corrections",
    "commissions", "lost_sales", "closed_days",
    "bill_intelligence", "pending_actions",
    "pairing_codes", "devices",
    "transfer_challan_items", "transfer_challans",
    "central_purchase_items", "central_purchases",
    "price_pushes", "branch_summaries",
    "audit_findings", "audit_runs",
    "ai_usage",
)


def shared_setup_test_db(prefix: str = "billbook_test_") -> str:
    """Create a fresh temp DB with sample data loaded + stock state rebuilt +
    test manager employee (id=99, PIN '1234').

    This is the shared implementation that replaces the 59 copy-pasted
    setup_test_db() functions. Each test file can either import this directly
    or wrap it with custom setup.

    Returns the temp directory path (pass to shared_cleanup() when done).
    """
    test_dir = _tempfile.mkdtemp(prefix=prefix)
    from app import config, db, profit
    db.DB_PATH = _os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        _os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in _CLEAR_TABLES:
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        if _SAMPLE_SQL.exists():
            with open(_SAMPLE_SQL) as f:
                c.executescript(f.read())
        # Add test manager employee with PIN '1234'
        c.execute(
            "INSERT OR REPLACE INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
    profit.rebuild_stock_state()
    return test_dir


def shared_cleanup(test_dir: str) -> None:
    """Clean up the temp directory created by shared_setup_test_db()."""
    _shutil.rmtree(test_dir, ignore_errors=True)


def shared_login_client(client) -> None:
    """Helper: log in via the API with password 'testpass'.
    Requires shared_setup_test_db_with_password() to have been called.
    """
    r = client.post("/api/login", json={"password": "testpass"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"


def shared_setup_test_db_with_password(prefix: str = "billbook_apitest_") -> str:
    """Like shared_setup_test_db() but also sets up password_hash for API login tests.
    Sets the owner password to 'testpass'.
    """
    test_dir = shared_setup_test_db(prefix)
    from app import db
    from app.security import hash_password
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    return test_dir
