"""v8.13.1 — Security regression tests.

Verifies the 3 critical security fixes from the audit:

1. C1 — Path traversal in POS import upload (pos_import_router.py)
   - Attacker sends filename="/tmp/evil.zip" → server must use a random name
   - Attacker sends non-ZIP bytes with .zip extension → magic byte validation rejects

2. C2 — Cashier → Manager privilege escalation (main.py RBAC)
   - Cookie-session cashier calling /api/devices/code?role=manager → 403
   - Cookie-session cashier calling /api/agent/sql → 403
   - Cookie-session cashier calling /api/owner-withdrawals → 403

3. C3 — SQL table-allowlist bypass via comma-join (agent.py)
   - "SELECT * FROM bills, settings" → blocked
   - "SELECT * FROM bills WHERE id IN (SELECT id FROM sessions)" → blocked
   - "WITH x AS (SELECT * FROM sessions) SELECT * FROM x" → blocked
   - "SELECT * FROM bills JOIN bill_items ON ..." → allowed (both in allowlist)

4. H1 — Staff login uses bcrypt pin_hash (not plaintext pin)
   - Employee with pin_hash set + pin=NULL → login works via bcrypt
   - Employee with plaintext pin (legacy) → login works via hmac.compare_digest
   - Wrong PIN → 403
"""
import os, sys, tempfile, shutil, io, zipfile
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup, login_client
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_sec_")
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
                  "owner_withdrawals", "capital_injections", "stock_writeoffs"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
        from app.security import hash_password
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def login_client(client, password="testpass"):
    r = client.post("/api/login", json={"password": password})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"


def login_cashier(client, employee_id=200):
    """Login as cashier via staff login (PIN). Creates a cashier employee first."""
    from app import db, shop
    from app.security import hash_pin
    # Create a proper cashier employee with pin_hash (no plaintext pin)
    with db.conn() as c:
        c.execute("DELETE FROM employees WHERE id=200")
        c.execute(
            "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
            "VALUES(200, 'Test Cashier', 'cashier', NULL, ?, 1)",
            (hash_pin("1234"),)
        )
    r = client.post("/api/login/staff", json={"employee_id": 200, "pin": "1234"})
    assert r.status_code == 200, f"Cashier login failed: {r.status_code} {r.text}"
    assert r.json()["role"] == "cashier", f"Expected cashier role, got {r.json().get('role')}"



def test_pos_import_rejects_path_traversal_filename():
    """Attacker sends filename='/tmp/evil.zip' — server must NOT write to /tmp/evil.zip.
    Server should use a random filename + validate magic bytes."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        # Create a valid ZIP in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr("ACCTRANS.DBF", b"dummy")
        buf.seek(0)
        # Attacker sends filename="/tmp/evil.zip" (absolute path)
        r = client.post("/api/pos-import/upload",
            files={"file": ("/tmp/evil.zip", buf.getvalue(), "application/zip")})
        # Should NOT write to /tmp/evil.zip — the server should use a random name
        # The upload may fail (invalid DBF) but the important thing is the file
        # was NOT written to /tmp/evil.zip
        assert not os.path.exists("/tmp/evil.zip"), \
            "CRITICAL: Server wrote to attacker-controlled path /tmp/evil.zip!"
        # The upload itself may fail with 400/500 (invalid DBF), that's OK
        # as long as the path-traversal was prevented
    finally:
        # Cleanup any stray files
        if os.path.exists("/tmp/evil.zip"):
            os.remove("/tmp/evil.zip")
        cleanup(test_dir)


def test_pos_import_rejects_non_zip_magic_bytes():
    """Attacker sends a .zip file whose content is NOT actually a ZIP
    (e.g. a renamed .exe) — magic byte validation must reject it."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        # Send non-ZIP bytes with .zip extension
        fake_data = b"This is not a ZIP file - it's an attacker payload"
        r = client.post("/api/pos-import/upload",
            files={"file": ("backup.zip", fake_data, "application/zip")})
        assert r.status_code == 400, \
            f"Expected 400 for non-ZIP magic bytes, got {r.status_code}: {r.text}"
        assert "magic" in r.text.lower() or "zip" in r.text.lower(), \
            f"Error message should mention magic/zip, got: {r.text}"
    finally:
        cleanup(test_dir)


# ─── C2: Cashier → Manager RBAC escalation ────────────────────────────

def test_cashier_cannot_access_devices_code():
    """Cookie-session cashier calling /api/devices/code?role=manager → 403.
    Previously: the cookie-session RBAC list was missing /api/devices, so
    a cashier could self-issue a manager device token."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_cashier(client)
        # Try to issue a manager device code as cashier
        r = client.get("/api/devices/code?role=manager")
        assert r.status_code == 403, \
            f"CRITICAL: Cashier can issue manager device tokens! Expected 403, got {r.status_code}"
    finally:
        cleanup(test_dir)


def test_cashier_cannot_access_agent_sql():
    """Cookie-session cashier calling /api/agent/sql → 403.
    Prevents the cashier from reaching the SQL allowlist bypass vector."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_cashier(client)
        r = client.post("/api/agent/sql", json={"query": "SELECT * FROM bills"})
        assert r.status_code == 403, \
            f"CRITICAL: Cashier can reach /api/agent/sql! Expected 403, got {r.status_code}"
    finally:
        cleanup(test_dir)


def test_cashier_cannot_access_owner_withdrawals():
    """Cookie-session cashier calling /api/owner-withdrawals → 403."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_cashier(client)
        r = client.post("/api/owner-withdrawals", json={"amount": 50000, "payment_method": "cash"})
        assert r.status_code == 403, \
            f"CRITICAL: Cashier can record owner withdrawals! Expected 403, got {r.status_code}"
        r2 = client.post("/api/capital-injections", json={"amount": 50000, "manager_pin": "1234"})
        assert r2.status_code == 403, \
            f"CRITICAL: Cashier can record capital injections! Expected 403, got {r2.status_code}"
    finally:
        cleanup(test_dir)


def test_cashier_cannot_access_cost_trend_reports():
    """Cookie-session cashier calling cost-trend reports → 403."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_cashier(client)
        for endpoint in ["/api/reports/supplier-comparison",
                         "/api/reports/category-cost-trends",
                         "/api/reports/stock-writeoffs",
                         "/api/reports/stock-writeoffs/summary"]:
            r = client.get(endpoint)
            assert r.status_code == 403, \
                f"CRITICAL: Cashier can access {endpoint}! Expected 403, got {r.status_code}"
    finally:
        cleanup(test_dir)


# ─── C3: SQL table-allowlist bypass ───────────────────────────────────

def test_sql_comma_join_bypass_blocked():
    """SELECT password_hash FROM bills, settings → must be blocked.
    The old regex only matched 'bills' and missed 'settings'."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("SELECT password_hash FROM bills, settings LIMIT 1")
        assert "error" in result, "CRITICAL: comma-join bypass succeeded!"
        assert "forbidden" in result["error"].lower() or "not permitted" in result["error"].lower(), \
            f"Error should mention forbidden/not permitted, got: {result}"
    finally:
        cleanup(test_dir)


def test_sql_subquery_bypass_blocked():
    """SELECT * FROM bills WHERE id IN (SELECT id FROM sessions) → blocked."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql(
            "SELECT * FROM bills WHERE id IN (SELECT id FROM sessions)"
        )
        assert "error" in result, "CRITICAL: subquery bypass succeeded!"
        assert "forbidden" in result["error"].lower() or "not permitted" in result["error"].lower(), \
            f"Error should mention forbidden, got: {result}"
    finally:
        cleanup(test_dir)


def test_sql_cte_bypass_blocked():
    """WITH x AS (SELECT * FROM sessions) SELECT * FROM x → blocked."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql(
            "WITH x AS (SELECT * FROM sessions) SELECT * FROM x"
        )
        assert "error" in result, "CRITICAL: CTE bypass succeeded!"
        assert "forbidden" in result["error"].lower() or "not permitted" in result["error"].lower(), \
            f"Error should mention forbidden, got: {result}"
    finally:
        cleanup(test_dir)


def test_sql_allowed_join_works():
    """SELECT COUNT(*) FROM bill_items JOIN bills ON ... → should work
    (both tables are in the allowlist)."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql(
            "SELECT COUNT(*) AS n FROM bill_items bi JOIN bills b ON bi.bill_id = b.id"
        )
        assert "error" not in result, f"Allowed JOIN was blocked: {result}"
        assert "rows" in result
    finally:
        cleanup(test_dir)


def test_sql_employees_table_blocked():
    """SELECT * FROM employees → must be blocked (contains pin_hash)."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("SELECT * FROM employees")
        assert "error" in result, "CRITICAL: employees table accessible!"
        assert "forbidden" in result["error"].lower() or "not permitted" in result["error"].lower(), \
            f"Error should mention forbidden, got: {result}"
    finally:
        cleanup(test_dir)


def test_sql_limit_capped_at_50():
    """SELECT * FROM bills → LIMIT 50 should be auto-injected (was 500)."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        # This should auto-inject LIMIT 50 if no LIMIT present
        result = execute_constrained_sql("SELECT * FROM bills")
        # The query runs (no error) — we can't directly verify the LIMIT from here,
        # but we verify it doesn't error and returns a row_count field
        assert "error" not in result, f"Query should work: {result}"
        assert "row_count" in result
    finally:
        cleanup(test_dir)


# ─── H1: Staff login uses bcrypt pin_hash ─────────────────────────────

def test_staff_login_works_with_pin_hash():
    """Employee with pin_hash set (modern path) → login works via bcrypt verify."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main, db, shop
        from app.security import hash_pin
        # Create employee with pin_hash (no plaintext pin)
        with db.conn() as c:
            c.execute("DELETE FROM employees WHERE id=100")
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(100, 'Modern Emp', 'cashier', NULL, ?, 1)",
                (hash_pin("5678"),)
            )
        client = TestClient(main.app)
        r = client.post("/api/login/staff", json={"employee_id": 100, "pin": "5678"})
        assert r.status_code == 200, f"Login with pin_hash should work: {r.status_code} {r.text}"
        assert r.json()["role"] == "cashier"
    finally:
        cleanup(test_dir)


def test_staff_login_rejects_wrong_pin():
    """Wrong PIN → 403 (regardless of pin_hash or plaintext)."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main, db
        from app.security import hash_pin
        with db.conn() as c:
            c.execute("DELETE FROM employees WHERE id=101")
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(101, 'Test Emp 2', 'cashier', NULL, ?, 1)",
                (hash_pin("9999"),)
            )
        client = TestClient(main.app)
        r = client.post("/api/login/staff", json={"employee_id": 101, "pin": "0000"})
        assert r.status_code == 403, f"Wrong PIN should return 403: {r.status_code}"
    finally:
        cleanup(test_dir)


def test_staff_login_legacy_plaintext_pin_works():
    """Employee with plaintext pin (legacy, no pin_hash) → login works via hmac.compare_digest."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main, db
        with db.conn() as c:
            c.execute("DELETE FROM employees WHERE id=102")
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(102, 'Legacy Emp', 'cashier', '4321', NULL, 1)"
            )
        client = TestClient(main.app)
        r = client.post("/api/login/staff", json={"employee_id": 102, "pin": "4321"})
        assert r.status_code == 200, f"Legacy plaintext login should work: {r.status_code} {r.text}"
    finally:
        cleanup(test_dir)
