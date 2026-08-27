"""v8.15.0: Shared test helpers — replaces the 59 copy-pasted setup_test_db()
and cleanup() functions across the test suite.

This module is importable from any test file:
    from test_helpers import setup_test_db, cleanup

It eliminates ~1,400 LOC of duplicated boilerplate. Each test file that
previously had its own 25-line setup_test_db() can now just import from here.

For files that need CUSTOM setup (e.g., test_cogs.py sets stock_strategy):
    from test_helpers import setup_test_db_base, cleanup
    def setup_test_db():
        test_dir = setup_test_db_base()
        from app import db
        db.set_setting("stock_strategy", "permit_negative")
        return test_dir
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Ensure project root is on sys.path
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

SAMPLE_SQL = Path(__file__).parent / "sample_data.sql"

# All tables that might have leftover data from a previous test run
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


def setup_test_db(prefix="billbook_test_"):
    """Create a fresh temp DB with sample data + stock state + test manager (PIN '1234').

    Returns the temp directory path. Pass to cleanup() when done.
    """
    test_dir = tempfile.mkdtemp(prefix=prefix)
    from app import config, db, profit
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in _CLEAR_TABLES:
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        if SAMPLE_SQL.exists():
            with open(SAMPLE_SQL) as f:
                c.executescript(f.read())
        c.execute(
            "INSERT OR REPLACE INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
    profit.rebuild_stock_state()
    return test_dir


def cleanup(test_dir):
    """Clean up the temp directory created by setup_test_db()."""
    shutil.rmtree(test_dir, ignore_errors=True)


def setup_test_db_with_password(prefix="billbook_apitest_"):
    """Like setup_test_db() but also sets up password_hash for API login tests.
    Sets the owner password to 'testpass'.
    """
    test_dir = setup_test_db(prefix)
    from app import db
    from app.security import hash_password
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    return test_dir


def login_client(client):
    """Helper: log in via the API with password 'testpass'.
    Requires setup_test_db_with_password() to have been called.
    """
    r = client.post("/api/login", json={"password": "testpass"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
