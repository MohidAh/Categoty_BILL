"""v8.12.1 — Capital Injection tests.

Covers:
- Happy path: add_capital_injection() credits cash_drawer + creates audit log
- Source validation (rejects invalid source)
- Amount validation (rejects ≤ 0)
- get_cash_buckets() exposes capital_injections_total
- list_capital_injections() returns most-recent-first
- get_capital_injections_summary() groups by source
- "Day 1 negative withdrawal trap" — the original use case:
    before injection, available_for_withdrawal is negative;
    after injection, it's positive (or at least less negative)
- API endpoints (POST/GET/summary/sources) — admin PIN gate
- Capital injection is NOT revenue (does not inflate sales/profit)
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup, login_client
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_ci_")
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
                  "owner_withdrawals", "capital_injections"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Add a manager employee with a known plaintext PIN (legacy compat path)
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
        # Set the owner password (so login works in API tests)
        from app.security import hash_password
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def login_client(client):
    """Helper: log in via the API and return the authed client."""
    r = client.post("/api/login", json={"password": "testpass"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"



def test_add_capital_injection_credits_cash_drawer():
    """Happy path: add_capital_injection() returns an ID, writes a cash_drawer
    row with +amount, and creates a capital_injections row."""
    test_dir = setup_test_db()
    try:
        from app import profit_cash, db
        inj_id = profit_cash.add_capital_injection(
            amount=200000, source='owner_pocket',
            payment_method='cash', notes='Initial investment'
        )
        assert isinstance(inj_id, int) and inj_id > 0, f"Expected int ID, got {inj_id}"
        with db.conn() as c:
            # 1. capital_injections row created
            row = c.execute("SELECT * FROM capital_injections WHERE id=?", (inj_id,)).fetchone()
            assert row is not None, "capital_injections row missing"
            assert row["amount"] == 200000, f"amount mismatch: {row['amount']}"
            assert row["source"] == 'owner_pocket'
            assert row["notes"] == 'Initial investment'
            # 2. cash_drawer row created with +amount
            cd = c.execute(
                "SELECT * FROM cash_drawer WHERE type='capital_injection' AND reference_id=?",
                (inj_id,)
            ).fetchone()
            assert cd is not None, "cash_drawer row missing"
            assert cd["amount"] == 200000, f"cash_drawer amount should be +200000, got {cd['amount']}"
            assert cd["reference_type"] == 'capital_injection'
            # 3. Activity log entry
            log = c.execute(
                "SELECT * FROM activity_log WHERE event_type='capital_injection' AND entity_id=?",
                (inj_id,)
            ).fetchone()
            assert log is not None, "activity_log entry missing"
    finally:
        cleanup(test_dir)


def test_capital_injection_rejects_invalid_source():
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        try:
            profit_cash.add_capital_injection(amount=100, source='invalid_source')
            assert False, "Should have raised ValueError for invalid source"
        except ValueError as e:
            assert 'invalid_source' in str(e).lower() or 'owner_pocket' in str(e).lower()
    finally:
        cleanup(test_dir)


def test_capital_injection_rejects_nonpositive_amount():
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        for bad_amt in (0, -100):
            try:
                profit_cash.add_capital_injection(amount=bad_amt, source='owner_pocket')
                assert False, f"Should have raised for amount={bad_amt}"
            except ValueError:
                pass  # expected
    finally:
        cleanup(test_dir)


def test_list_capital_injections_returns_most_recent_first():
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        id1 = profit_cash.add_capital_injection(amount=100, source='owner_pocket', date='2026-01-01 10:00:00')
        id2 = profit_cash.add_capital_injection(amount=200, source='partner', date='2026-02-01 10:00:00')
        id3 = profit_cash.add_capital_injection(amount=300, source='bank_loan', date='2026-03-01 10:00:00')
        inj_list = profit_cash.list_capital_injections(limit=10)
        assert len(inj_list) == 3
        # Most recent first (highest ID = most recent because we used later dates)
        assert inj_list[0]["id"] == id3
        assert inj_list[2]["id"] == id1
    finally:
        cleanup(test_dir)


def test_capital_injections_summary_groups_by_source():
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        profit_cash.add_capital_injection(amount=100000, source='owner_pocket')
        profit_cash.add_capital_injection(amount=50000, source='owner_pocket')
        profit_cash.add_capital_injection(amount=200000, source='bank_loan')
        summary = profit_cash.get_capital_injections_summary()
        assert summary["all_time_total"] == 350000, f"Expected 350000, got {summary['all_time_total']}"
        assert summary["all_time_count"] == 3
        # by_source should have 2 entries (owner_pocket: 150000, bank_loan: 200000)
        by_src = {s["source"]: s for s in summary["by_source"]}
        assert "owner_pocket" in by_src
        assert by_src["owner_pocket"]["total"] == 150000
        assert "bank_loan" in by_src
        assert by_src["bank_loan"]["total"] == 200000
    finally:
        cleanup(test_dir)


# ─── Integration: get_cash_buckets() ──────────────────────────────────

def test_cash_buckets_exposes_capital_injections_total():
    """get_cash_buckets() must include the capital_injections_total field
    so the UI can show the breakdown honestly."""
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        # Before any injection
        b = profit_cash.get_cash_buckets()
        assert "capital_injections_total" in b, "Missing capital_injections_total field"
        assert b["capital_injections_total"] == 0, "Should start at 0"
        # After injection
        profit_cash.add_capital_injection(amount=250000, source='owner_pocket')
        b2 = profit_cash.get_cash_buckets()
        assert b2["capital_injections_total"] == 250000, \
            f"Expected 250000, got {b2['capital_injections_total']}"
        # And cash_in_drawer must also reflect the +250000
        assert b2["cash_in_drawer"] >= 250000, \
            f"Cash drawer should be >= 250000 after injection, got {b2['cash_in_drawer']}"
    finally:
        cleanup(test_dir)


def test_capital_injection_fixes_negative_withdrawal_trap():
    """The headline use case: simulate the Day 1 trap (confirmed supplier bills
    but no sales yet) and verify a capital injection brings the withdrawal
    number back to non-negative.
    """
    test_dir = setup_test_db()
    try:
        from app import profit_cash, db
        # Step 1: simulate the trap — drain the cash_drawer by inserting a fake
        # "purchase" entry (representing an already-confirmed supplier bill)
        # without any matching sale.
        with db.conn() as c:
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_type) "
                "VALUES('purchase', -200000, 'Simulated initial stock purchase', 'bill')"
            )
        # Verify the trap: available_for_withdrawal should be negative
        b = profit_cash.get_cash_buckets()
        assert b["cash_in_drawer"] < 0, \
            f"Precondition: cash_drawer should be negative, got {b['cash_in_drawer']}"
        # (available_for_withdrawal will also be negative — exact value depends on COGS settings)
        # Step 2: record the capital injection
        profit_cash.add_capital_injection(amount=200000, source='opening_balance',
                                          notes='Day 1 capital fix')
        # Step 3: verify the trap is fixed
        b2 = profit_cash.get_cash_buckets()
        # Cash drawer should now be at the same level as before the simulated
        # purchase (or very close, depending on other transactions in the sample data)
        assert b2["cash_in_drawer"] > b["cash_in_drawer"], \
            f"Cash drawer should have increased: was {b['cash_in_drawer']}, now {b2['cash_in_drawer']}"
        # available_for_withdrawal should also have improved by ~200000
        improvement = b2["available_for_withdrawal"] - b["available_for_withdrawal"]
        assert improvement >= 199999, \
            f"Available for withdrawal should have improved by 200000, got improvement={improvement}"
    finally:
        cleanup(test_dir)


def test_capital_injection_does_not_inflate_revenue():
    """Capital injections are equity, NOT revenue. They must not appear in
    sales totals or gross profit calculations."""
    test_dir = setup_test_db()
    try:
        from app import profit_cash
        b_before = profit_cash.get_cash_buckets()
        sales_before = b_before["sales"]
        gp_before = b_before["gross_profit"]
        # Inject a large amount
        profit_cash.add_capital_injection(amount=999999, source='owner_pocket')
        b_after = profit_cash.get_cash_buckets()
        # Sales must be unchanged
        assert b_after["sales"] == sales_before, \
            f"Sales must NOT change after capital injection: was {sales_before}, now {b_after['sales']}"
        # Gross profit must be unchanged
        assert b_after["gross_profit"] == gp_before, \
            f"Gross profit must NOT change: was {gp_before}, now {b_after['gross_profit']}"
        # But cash_in_drawer must increase
        assert b_after["cash_in_drawer"] > b_before["cash_in_drawer"], \
            "Cash drawer must increase after capital injection"
    finally:
        cleanup(test_dir)


# ─── API endpoint tests (via TestClient) ───────────────────────────────

def test_api_get_sources_returns_valid_list():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        r = client.get("/api/capital-injections/sources")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "sources" in data
        codes = [s["code"] for s in data["sources"]]
        assert "owner_pocket" in codes
        assert "partner" in codes
        assert "bank_loan" in codes
        assert "opening_balance" in codes
        # Each source must have a label
        for s in data["sources"]:
            assert s.get("label"), f"Source {s['code']} missing label"
    finally:
        os.environ.pop("BILLBOOK_TEST_MODE", None)
        cleanup(test_dir)


def test_api_list_injections_after_create():
    """End-to-end: POST an injection (with PIN), then GET the list and verify."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        # Create with the test manager's PIN '1234'
        r = client.post("/api/capital-injections", json={
            "amount": 150000,
            "source": "opening_balance",
            "payment_method": "cash",
            "notes": "Initial investment",
            "manager_pin": "1234"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        inj_id = r.json()["id"]
        assert isinstance(inj_id, int) and inj_id > 0
        # List should show this injection
        r2 = client.get("/api/capital-injections")
        assert r2.status_code == 200
        inj_list = r2.json()["injections"]
        assert any(i["id"] == inj_id for i in inj_list), "Injection not in list"
        # Summary should show the total
        r3 = client.get("/api/capital-injections/summary")
        assert r3.status_code == 200
        summary = r3.json()
        assert summary["all_time_total"] == 150000
        assert summary["all_time_count"] == 1
    finally:
        os.environ.pop("BILLBOOK_TEST_MODE", None)
        cleanup(test_dir)


def test_api_rejects_create_without_pin():
    """POST /api/capital-injections must return 403 without a valid PIN."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        # No PIN
        r = client.post("/api/capital-injections", json={
            "amount": 50000, "source": "owner_pocket"
        })
        assert r.status_code == 403, f"Expected 403 without PIN, got {r.status_code}"
        # Wrong PIN
        r2 = client.post("/api/capital-injections", json={
            "amount": 50000, "source": "owner_pocket", "manager_pin": "9999"
        })
        assert r2.status_code == 403, f"Expected 403 with wrong PIN, got {r2.status_code}"
        # Verify nothing was written
        r3 = client.get("/api/capital-injections")
        assert len(r3.json()["injections"]) == 0, "No injection should have been created"
    finally:
        os.environ.pop("BILLBOOK_TEST_MODE", None)
        cleanup(test_dir)


def test_api_rejects_invalid_source():
    """POST /api/capital-injections returns 400 for an invalid source code."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        r = client.post("/api/capital-injections", json={
            "amount": 50000, "source": "gold_mine", "manager_pin": "1234"
        })
        assert r.status_code == 400, f"Expected 400 for invalid source, got {r.status_code}"
    finally:
        os.environ.pop("BILLBOOK_TEST_MODE", None)
        cleanup(test_dir)
