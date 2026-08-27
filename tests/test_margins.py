"""v5.0 Phase 3 — Dual Margin Display tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p3v5_")
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



def test_margins_returns_required_fields():
    test_dir = setup_test_db()
    try:
        from app import profit
        m = profit.get_margins()
        for key in ("categories", "category_average_margin", "actual_overall_margin",
                    "difference_pct", "note"):
            assert key in m, f"Missing key: {key}"
        assert "Category Average is informational" in m["note"]
        assert "Actual Overall is the primary KPI" in m["note"]
    finally:
        cleanup(test_dir)


def test_actual_overall_margin_is_sales_weighted():
    """actual_overall_margin = total_gp / total_sales (NOT mean of category margins)."""
    test_dir = setup_test_db()
    try:
        from app import profit
        m = profit.get_margins()
        if m["total_sales"] > 0:
            expected = round((m["total_gross_profit"] / m["total_sales"]) * 100, 2)
            assert abs(m["actual_overall_margin"] - expected) < 0.01, \
                f"actual_overall_margin should be {expected}, got {m['actual_overall_margin']}"
    finally:
        cleanup(test_dir)


def test_category_average_margin_is_simple_mean():
    """category_average_margin = simple mean of per-category margins."""
    test_dir = setup_test_db()
    try:
        from app import profit
        m = profit.get_margins()
        margins = [c["margin_pct"] for c in m["categories"] if c["sell_price"] > 0]
        if margins:
            expected = round(sum(margins) / len(margins), 2)
            assert abs(m["category_average_margin"] - expected) < 0.01, \
                f"category_average_margin should be {expected}, got {m['category_average_margin']}"
    finally:
        cleanup(test_dir)


def test_categories_have_margin_pct():
    """Each category dict has sell_price, avg_cost, margin_pct."""
    test_dir = setup_test_db()
    try:
        from app import profit
        m = profit.get_margins()
        for c in m["categories"]:
            assert "code" in c
            assert "sell_price" in c
            assert "avg_cost" in c
            assert "margin_pct" in c
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_margins_returns_required_fields()
    print("✓ test_margins_returns_required_fields")
    test_actual_overall_margin_is_sales_weighted()
    print("✓ test_actual_overall_margin_is_sales_weighted")
    test_category_average_margin_is_simple_mean()
    print("✓ test_category_average_margin_is_simple_mean")
    test_categories_have_margin_pct()
    print("✓ test_categories_have_margin_pct")
    print("\n✅ ALL PHASE 3 DUAL MARGIN TESTS PASSED")
