"""v5.0 Phase 6 — Daily Stock Report tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p6v5_")
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



def test_daily_stock_returns_required_fields():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_daily_stock_report("2026-08-11")
        assert "date" in r
        assert "rows" in r
        assert "totals" in r
        # Each row has the 11 columns
        if r["rows"]:
            row = r["rows"][0]
            for key in ("date", "category", "opening_qty", "purchased_qty",
                        "sold_qty", "closing_qty", "average_cost",
                        "stock_value", "sales_value", "cogs", "gross_profit"):
                assert key in row, f"Missing column: {key}"
    finally:
        cleanup(test_dir)


def test_opening_plus_purchased_minus_sold_equals_closing():
    """For every category: opening + purchased - sold = closing (±adjustments)."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_daily_stock_report("2026-08-11")
        for row in r["rows"]:
            expected = row["opening_qty"] + row["purchased_qty"] - row["sold_qty"]
            # Allow small rounding difference (no adjustments in sample data for this date)
            assert abs(row["closing_qty"] - expected) < 0.1, \
                f"Category {row['category']}: opening({row['opening_qty']}) + purchased({row['purchased_qty']}) - sold({row['sold_qty']}) = {expected}, but closing = {row['closing_qty']}"
    finally:
        cleanup(test_dir)


def test_totals_row_reconciles():
    """Totals row sums the per-category rows."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_daily_stock_report("2026-08-11")
        t = r["totals"]
        # Sum each column
        for col in ("opening_qty", "purchased_qty", "sold_qty", "closing_qty",
                    "stock_value", "sales_value", "cogs", "gross_profit"):
            expected = round(sum(row[col] for row in r["rows"]), 2)
            assert abs(t[col] - expected) < 0.1, \
                f"Total {col}: expected {expected}, got {t[col]}"
    finally:
        cleanup(test_dir)


def test_empty_date_returns_empty_rows():
    """A date with no activity returns empty rows."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_daily_stock_report("2025-01-01")
        # May still have opening balances from earlier dates, but no purchased/sold
        assert r["totals"]["purchased_qty"] == 0
        assert r["totals"]["sold_qty"] == 0
    finally:
        cleanup(test_dir)


def test_gross_profit_reconciles():
    """gross_profit = sales_value - cogs for each row."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_daily_stock_report("2026-08-11")
        for row in r["rows"]:
            expected = row["sales_value"] - row["cogs"]
            assert abs(row["gross_profit"] - expected) < 0.01, \
                f"GP mismatch: {row['gross_profit']} vs {expected}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_daily_stock_returns_required_fields()
    print("✓ test_daily_stock_returns_required_fields")
    test_opening_plus_purchased_minus_sold_equals_closing()
    print("✓ test_opening_plus_purchased_minus_sold_equals_closing")
    test_totals_row_reconciles()
    print("✓ test_totals_row_reconciles")
    test_empty_date_returns_empty_rows()
    print("✓ test_empty_date_returns_empty_rows")
    test_gross_profit_reconciles()
    print("✓ test_gross_profit_reconciles")
    print("\n✅ ALL PHASE 6 DAILY STOCK REPORT TESTS PASSED")
