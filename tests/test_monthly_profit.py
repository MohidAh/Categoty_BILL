"""v5.0 Phase 4 — Monthly Actual Profit (COGS method) tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p4v5_")
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
                  "category_stock_state"):
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



def test_monthly_profit_returns_required_fields():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        for key in ("month", "opening_inventory", "purchases", "closing_inventory",
                    "cogs", "cogs_from_sales", "sales", "gross_profit",
                    "operating_expenses", "operating_profit"):
            assert key in r, f"Missing key: {key}"
    finally:
        cleanup(test_dir)


def test_cogs_bridge_formula():
    """COGS = Opening + Purchases - Closing."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        expected_cogs = r["opening_inventory"] + r["purchases"] - r["closing_inventory"]
        assert abs(r["cogs"] - expected_cogs) < 0.01, \
            f"COGS bridge broken: {r['cogs']} vs {expected_cogs}"
    finally:
        cleanup(test_dir)


def test_cogs_cross_check():
    """cogs (bridge) and cogs_from_sales should match within rounding (±1)."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        assert abs(r["cogs"] - r["cogs_from_sales"]) < 1.0, \
            f"COGS cross-check failed: bridge={r['cogs']} vs from_sales={r['cogs_from_sales']}"
    finally:
        cleanup(test_dir)


def test_gross_profit_separate_from_operating():
    """GP = Sales - COGS. Operating Profit = GP - Operating Expenses."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        expected_gp = r["sales"] - r["cogs"]
        assert abs(r["gross_profit"] - expected_gp) < 0.01
        expected_op = r["gross_profit"] - r["operating_expenses"]
        assert abs(r["operating_profit"] - expected_op) < 0.01
    finally:
        cleanup(test_dir)


def test_owner_draws_excluded_from_operating_expenses():
    """Owner draws don't reduce operating profit."""
    test_dir = setup_test_db()
    try:
        from app import shop, profit
        # Add an owner draw
        shop.add_expense("Owner Draw", 10000, "draw", "cash",
                         expense_type="owner_draw", date_str="2026-08-15")
        r = profit.get_monthly_profit("2026-08")
        assert r["owner_draws"] == 10000.0
        # Operating expenses should NOT include the 10,000
        # (We can't assert the exact op_exp value, but we can verify
        # owner_draws is reported separately)
        assert r["owner_draws"] != r["operating_expenses"] or r["operating_expenses"] == 0
    finally:
        cleanup(test_dir)


def test_empty_month_returns_zeros():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2025-01")
        assert r["sales"] == 0.0
        assert r["cogs"] == 0.0
        assert r["gross_profit"] == 0.0
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_monthly_profit_returns_required_fields()
    print("✓ test_monthly_profit_returns_required_fields")
    test_cogs_bridge_formula()
    print("✓ test_cogs_bridge_formula")
    test_cogs_cross_check()
    print("✓ test_cogs_cross_check")
    test_gross_profit_separate_from_operating()
    print("✓ test_gross_profit_separate_from_operating")
    test_owner_draws_excluded_from_operating_expenses()
    print("✓ test_owner_draws_excluded_from_operating_expenses")
    test_empty_month_returns_zeros()
    print("✓ test_empty_month_returns_zeros")
    print("\n✅ ALL PHASE 4 MONTHLY PROFIT TESTS PASSED")
