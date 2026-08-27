"""BillBook v3.0 — Invariant tests with sample data.
Run after every phase to verify no regressions.
Usage: pytest tests/test_invariants.py -v
"""
import os
import sys
import sqlite3
import tempfile
import shutil
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Test fixtures
SAMPLE_DATA_SQL = Path(__file__).parent / "sample_data.sql"
DB_PATH = None
ORIGINAL_DB = None


def setup_module():
    """Create a fresh test database with sample data."""
    global DB_PATH, ORIGINAL_DB
    
    # Save original DB path
    from app import config
    # Coerce to Path — other test files may have set config.DATA to a string
    ORIGINAL_DB = Path(config.DATA) / "billbook.db"
    
    # Create temp directory for test DB
    test_dir = Path(tempfile.mkdtemp())
    DB_PATH = test_dir / "billbook.db"
    
    # Override config paths
    config.DATA = test_dir
    config.UPLOADS = test_dir / "uploads"
    config.PAGES = test_dir / "pages"
    config.BACKUPS = test_dir / "backups"
    for d in [config.DATA, config.UPLOADS, config.PAGES, config.BACKUPS]:
        d.mkdir(exist_ok=True)
    
    # Override db.DB_PATH
    from app import db
    db.DB_PATH = str(DB_PATH)
    
    # Initialize schema
    db.init()
    
    # Clear existing seeded data before loading sample data
    with db.conn() as c:
        for table in ['sale_items', 'sales', 'bill_items', 'bills', 'customers',
                       'price_categories', 'suppliers', 'stock_adjustments']:
            c.execute(f"DELETE FROM {table}")
    
    # Load sample data
    with open(SAMPLE_DATA_SQL) as f:
        sql = f.read()
    with db.conn() as c:
        c.executescript(sql)
    
    print(f"Test DB created at: {DB_PATH}")


def teardown_module():
    """Clean up test database."""
    if DB_PATH and DB_PATH.exists():
        test_dir = DB_PATH.parent
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"Test DB cleaned up")


def get_db():
    """Get a fresh connection to the test DB."""
    from app import db
    return db.conn()


# ═══════════════════════════════════════════════════
# REVENUE INVARIANTS
# ═══════════════════════════════════════════════════

def test_total_revenue():
    """Total revenue (non-refunded sales) = 15,650."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS rev FROM sales WHERE payment_status != 'refunded'"
        ).fetchone()
    assert row["rev"] == 15650, f"Expected revenue 15,650, got {row['rev']}"


def test_paid_revenue():
    """Paid sales total = 10,150."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS rev FROM sales WHERE payment_status = 'paid'"
        ).fetchone()
    assert row["rev"] == 10150, f"Expected paid 10,150, got {row['rev']}"


def test_credit_revenue():
    """Credit sales total = 5,500."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS rev FROM sales WHERE payment_status = 'credit'"
        ).fetchone()
    assert row["rev"] == 5500, f"Expected credit 5,500, got {row['rev']}"


def test_revenue_split():
    """Paid + Credit = Total revenue."""
    with get_db() as c:
        paid = c.execute("SELECT COALESCE(SUM(total),0) AS v FROM sales WHERE payment_status='paid'").fetchone()["v"]
        credit = c.execute("SELECT COALESCE(SUM(total),0) AS v FROM sales WHERE payment_status='credit'").fetchone()["v"]
        total = c.execute("SELECT COALESCE(SUM(total),0) AS v FROM sales WHERE payment_status != 'refunded'").fetchone()["v"]
    assert paid + credit == total, f"Paid({paid}) + Credit({credit}) != Total({total})"


# ═══════════════════════════════════════════════════
# COGS INVARIANT
# ═══════════════════════════════════════════════════

def test_cogs():
    """COGS (sum of cost_price * qty) = 7,490."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_price * qty), 0) AS cogs FROM sale_items"
        ).fetchone()
    assert abs(row["cogs"] - 7490) < 0.01, f"Expected COGS 7,490, got {row['cogs']}"


def test_gross_profit():
    """Gross profit = Revenue - COGS = 15,650 - 7,490 = 8,160."""
    with get_db() as c:
        rev = c.execute("SELECT COALESCE(SUM(total),0) AS v FROM sales WHERE payment_status != 'refunded'").fetchone()["v"]
        cogs = c.execute("SELECT COALESCE(SUM(cost_price * qty),0) AS v FROM sale_items").fetchone()["v"]
    profit = rev - cogs
    assert abs(profit - 8160) < 0.01, f"Expected profit 8,160, got {profit}"


# ═══════════════════════════════════════════════════
# STOCK INVARIANTS
# ═══════════════════════════════════════════════════

def test_stock_a():
    """Stock for category A (id=1) = 39."""
    with get_db() as c:
        purchased = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS q "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id = 1"
        ).fetchone()["q"]
        sold = c.execute("SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items WHERE category_id = 1").fetchone()["q"]
        adj = c.execute("SELECT COALESCE(SUM(delta), 0) AS q FROM stock_adjustments WHERE category_id = 1").fetchone()["q"]
    stock = purchased - sold + adj
    assert stock == 39, f"Expected stock A=39, got {stock} (purchased={purchased}, sold={sold}, adj={adj})"


def test_stock_b():
    """Stock for category B (id=2) = 17."""
    with get_db() as c:
        purchased = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS q "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id = 2"
        ).fetchone()["q"]
        sold = c.execute("SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items WHERE category_id = 2").fetchone()["q"]
        adj = c.execute("SELECT COALESCE(SUM(delta), 0) AS q FROM stock_adjustments WHERE category_id = 2").fetchone()["q"]
    stock = purchased - sold + adj
    assert stock == 17, f"Expected stock B=17, got {stock} (purchased={purchased}, sold={sold}, adj={adj})"


def test_stock_c():
    """Stock for category C (id=3) = 2."""
    with get_db() as c:
        purchased = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS q "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id = 3"
        ).fetchone()["q"]
        sold = c.execute("SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items WHERE category_id = 3").fetchone()["q"]
        adj = c.execute("SELECT COALESCE(SUM(delta), 0) AS q FROM stock_adjustments WHERE category_id = 3").fetchone()["q"]
    stock = purchased - sold + adj
    assert stock == 2, f"Expected stock C=2, got {stock} (purchased={purchased}, sold={sold}, adj={adj})"


def test_stock_d():
    """Stock for category D (id=4) = -3 (negative — sold more than purchased)."""
    with get_db() as c:
        purchased = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS q "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id = 4"
        ).fetchone()["q"]
        sold = c.execute("SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items WHERE category_id = 4").fetchone()["q"]
        adj = c.execute("SELECT COALESCE(SUM(delta), 0) AS q FROM stock_adjustments WHERE category_id = 4").fetchone()["q"]
    stock = purchased - sold + adj
    assert stock == -3, f"Expected stock D=-3, got {stock} (purchased={purchased}, sold={sold}, adj={adj})"


# ═══════════════════════════════════════════════════
# BALANCE SHEET INVARIANT
# ═══════════════════════════════════════════════════

def test_payables():
    """Balance sheet payables = 7,500 (credit bills)."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total, 0)), 0) AS payables "
            "FROM bills WHERE status='confirmed' AND payment_status='credit' AND deleted_at IS NULL"
        ).fetchone()
    assert row["payables"] == 7500, f"Expected payables 7,500, got {row['payables']}"


# ═══════════════════════════════════════════════════
# Z-REPORT INVARIANTS
# ═══════════════════════════════════════════════════

def test_z_report_sale_count():
    """Z-report: 2 sales on 2026-08-11."""
    with get_db() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE date(created_at) = '2026-08-11' AND payment_status != 'refunded'"
        ).fetchone()
    assert row["n"] == 2, f"Expected 2 sales on 2026-08-11, got {row['n']}"


def test_z_report_paid_count():
    """Z-report: 1 paid sale on 2026-08-11."""
    with get_db() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE date(created_at) = '2026-08-11' AND payment_status = 'paid'"
        ).fetchone()
    assert row["n"] == 1, f"Expected 1 paid sale, got {row['n']}"


def test_z_report_credit_count():
    """Z-report: 1 credit sale on 2026-08-11."""
    with get_db() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE date(created_at) = '2026-08-11' AND payment_status = 'credit'"
        ).fetchone()
    assert row["n"] == 1, f"Expected 1 credit sale, got {row['n']}"


def test_z_report_total_revenue():
    """Z-report: total revenue on 2026-08-11 = 15,650."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS rev FROM sales WHERE date(created_at) = '2026-08-11' AND payment_status != 'refunded'"
        ).fetchone()
    assert row["rev"] == 15650, f"Expected Z-report revenue 15,650, got {row['rev']}"


def test_z_report_cash_expected():
    """Z-report: cash expected = 10,150 (paid cash sale)."""
    with get_db() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS cash FROM sales "
            "WHERE date(created_at) = '2026-08-11' AND payment_method = 'cash' AND payment_status != 'refunded'"
        ).fetchone()
    assert row["cash"] == 10150, f"Expected cash 10,150, got {row['cash']}"


# ═══════════════════════════════════════════════════
# APP LOADING INVARIANT
# ═══════════════════════════════════════════════════

def test_app_loads():
    """App loads with all routers included."""
    from app.main import app
    assert app.title == "BillBook"
    # Use the OpenAPI schema to count API paths — router routes are nested
    # in app.routes and don't all expose .path directly at the top level.
    schema = app.openapi()
    api_paths = [p for p in schema.get("paths", {}) if p.startswith("/api")]
    assert len(api_paths) >= 160, f"Expected 160+ API routes, got {len(api_paths)}"


if __name__ == "__main__":
    # Run tests directly
    setup_module()
    try:
        test_total_revenue()
        print("✓ test_total_revenue")
        test_paid_revenue()
        print("✓ test_paid_revenue")
        test_credit_revenue()
        print("✓ test_credit_revenue")
        test_revenue_split()
        print("✓ test_revenue_split")
        test_cogs()
        print("✓ test_cogs")
        test_gross_profit()
        print("✓ test_gross_profit")
        test_stock_a()
        print("✓ test_stock_a")
        test_stock_b()
        print("✓ test_stock_b")
        test_stock_c()
        print("✓ test_stock_c")
        test_stock_d()
        print("✓ test_stock_d")
        test_payables()
        print("✓ test_payables")
        test_z_report_sale_count()
        print("✓ test_z_report_sale_count")
        test_z_report_paid_count()
        print("✓ test_z_report_paid_count")
        test_z_report_credit_count()
        print("✓ test_z_report_credit_count")
        test_z_report_total_revenue()
        print("✓ test_z_report_total_revenue")
        test_z_report_cash_expected()
        print("✓ test_z_report_cash_expected")
        test_app_loads()
        print("✓ test_app_loads")
        print("\n✅ ALL 16 INVARIANT TESTS PASSED")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    finally:
        teardown_module()
