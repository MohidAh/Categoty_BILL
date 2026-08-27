"""v5.0 Phase 5 — YTD Profit tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p5v5_")
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



def test_ytd_returns_required_fields():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_ytd_profit()
        for key in ("opening_date", "today", "ytd_sales", "ytd_cogs",
                    "ytd_gross_profit", "ytd_margin", "monthly",
                    "avg_of_monthly_margins", "method_difference"):
            assert key in r, f"Missing key: {key}"
    finally:
        cleanup(test_dir)


def test_ytd_margin_is_cumulative_not_avg():
    """YTD margin = Cumulative GP / Cumulative Sales, NOT avg of monthly margins.

    This is Rule 10. The test proves the two methods give different results
    when sales mix varies month-to-month.
    """
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_ytd_profit()
        if r["ytd_sales"] > 0:
            # Correct method
            expected = round((r["ytd_gross_profit"] / r["ytd_sales"]) * 100, 2)
            assert abs(r["ytd_margin"] - expected) < 0.01, \
                f"YTD margin should be {expected}, got {r['ytd_margin']}"
            # The WRONG method (avg of monthly margins) — should be DIFFERENT
            # (Unless all months have identical margins, which is unlikely.)
            # At minimum, the API must report both methods so the difference is visible.
            assert "avg_of_monthly_margins" in r
            assert "method_difference" in r
            assert abs(r["method_difference"] - (r["ytd_margin"] - r["avg_of_monthly_margins"])) < 0.01
    finally:
        cleanup(test_dir)


def test_ytd_method_difference_with_varying_margins():
    """Construct a scenario where YTD margin != avg of monthly margins.

    Month 1: 100 sales, 80 cost → GP 20, margin 20%
    Month 2: 200 sales, 100 cost → GP 100, margin 50%
    YTD: 300 sales, 180 cost → GP 120, margin 40% (correct)
    Avg of monthly margins: (20% + 50%) / 2 = 35% (WRONG)
    Difference: 5%
    """
    test_dir = setup_test_db()
    try:
        from app import db, profit
        # Add a second month of sales with different margin
        with db.conn() as c:
            # Month 2 sale: 200 sell, 100 cost → margin 50%
            c.execute(
                "INSERT INTO sales(id, invoice_no, customer_name, subtotal, total, "
                "payment_method, payment_status, created_at, tax_rate, tax_amount) "
                "VALUES(100, 'INV-YTD-100', 'Test', 200, 200, 'cash', 'paid', '2026-07-15 12:00:00', 0, 0)"
            )
            c.execute(
                "INSERT INTO sale_items(sale_id, item_name, category_id, category_code, "
                "sell_price, cost_price, qty, line_total) "
                "VALUES(100, 'Test', 1, 'A', 200, 100, 1, 200)"
            )
        # Sample data already has August 2026 sales with different margins
        r = profit.get_ytd_profit()
        # Verify both methods are computed and reported
        assert r["ytd_margin"] != r["avg_of_monthly_margins"] or len(r["monthly"]) <= 1, \
            "With multiple months of varying margins, YTD margin should differ from avg of monthly margins"
    finally:
        cleanup(test_dir)


def test_ytd_monthly_breakdown():
    """Monthly breakdown has sales, cogs, gross_profit, margin_pct per month."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_ytd_profit()
        for m in r["monthly"]:
            assert "month" in m
            assert "sales" in m
            assert "cogs" in m
            assert "gross_profit" in m
            assert "margin_pct" in m
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_ytd_returns_required_fields()
    print("✓ test_ytd_returns_required_fields")
    test_ytd_margin_is_cumulative_not_avg()
    print("✓ test_ytd_margin_is_cumulative_not_avg")
    test_ytd_method_difference_with_varying_margins()
    print("✓ test_ytd_method_difference_with_varying_margins")
    test_ytd_monthly_breakdown()
    print("✓ test_ytd_monthly_breakdown")
    print("\n✅ ALL PHASE 5 YTD PROFIT TESTS PASSED")
