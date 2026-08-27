"""v4.0 Phase 3 — Actual Earnings tests."""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_ae_")
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
                  "cash_drawer"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Re-seed expense categories
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)", (name, is_fixed, budget, 1, sort_order),
            )
    return test_dir



def test_actual_earnings_identity():
    """actual_earnings == total_sales - cogs - operating_expenses."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Add an operating expense to make it interesting
        shop.add_expense("Electricity", 1000, "bill", "cash",
                         expense_type="operating", date_str="2026-08-10")
        ae = shop.get_actual_earnings("2026-08")
        identity = ae["total_sales"] - ae["cogs"] - ae["operating_expenses"]
        assert abs(identity - ae["actual_earnings"]) < 0.01, \
            f"Identity broken: {identity} vs {ae['actual_earnings']}"
    finally:
        cleanup(test_dir)


def test_owner_draw_excluded():
    """Owner draw does NOT reduce actual_earnings (only operating expenses do)."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Baseline with no expenses
        ae1 = shop.get_actual_earnings("2026-08")
        # Add an owner draw
        shop.add_expense("Owner Draw", 10000, "draw", "cash",
                         expense_type="owner_draw", date_str="2026-08-11")
        ae2 = shop.get_actual_earnings("2026-08")
        assert ae1["actual_earnings"] == ae2["actual_earnings"], \
            f"Owner draw changed actual_earnings: {ae1['actual_earnings']} → {ae2['actual_earnings']}"
        assert ae2["owner_draws"] == 10000.0
    finally:
        cleanup(test_dir)


def test_cash_reality_fields_present():
    """cash_reality dict has all 4 required keys with numeric values."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2026-08")
        cr = ae["cash_reality"]
        for key in ("cash_in_drawer", "tied_in_unsold_stock",
                    "owed_to_you", "you_owe_suppliers"):
            assert key in cr, f"Missing cash_reality.{key}"
            assert isinstance(cr[key], (int, float)), \
                f"cash_reality.{key} not numeric: {type(cr[key])}"
    finally:
        cleanup(test_dir)


def test_purchases_shown_separately():
    """purchases = total confirmed bills this month, NOT subtracted from actual_earnings."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2026-08")
        # Sample data has 3 confirmed bills in August 2026: 4000 + 7500 + 2250 = 13750
        assert ae["purchases"] == 13750.0, f"Expected purchases 13750, got {ae['purchases']}"
        # Verify purchases is NOT subtracted from actual_earnings
        identity = ae["total_sales"] - ae["cogs"] - ae["operating_expenses"]
        assert abs(identity - ae["actual_earnings"]) < 0.01
        # The two should NOT be equal — purchases should not affect actual_earnings
        identity_with_purchases = ae["total_sales"] - ae["cogs"] - ae["operating_expenses"] - ae["purchases"]
        assert abs(identity_with_purchases - ae["actual_earnings"]) > 0.01, \
            "purchases appears to be subtracted from actual_earnings (should be shown separately)"
    finally:
        cleanup(test_dir)


def test_expenses_by_category_with_budget():
    """Expenses by category includes budget + pct fields."""
    test_dir = setup_test_db()
    try:
        from app import shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        shop.update_expense_category(rent_id, budget_monthly=30000)
        shop.add_expense("Rent", 30000, "August rent", "bank",
                         category_id=rent_id, expense_type="operating",
                         date_str="2026-08-05")
        ae = shop.get_actual_earnings("2026-08")
        rent_row = next((r for r in ae["expenses_by_category"] if r["category"] == "Rent"), None)
        assert rent_row is not None, "Rent missing from expenses_by_category"
        assert rent_row["total"] == 30000.0
        assert rent_row["budget"] == 30000.0
        assert rent_row["pct"] == 100.0
    finally:
        cleanup(test_dir)


def test_comparison_with_last_month():
    """comparison returns last_month_earnings and delta_pct."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2026-08")
        comp = ae["comparison"]
        assert "last_month" in comp
        assert comp["last_month"] == "2026-07"
        assert "last_month_earnings" in comp
        assert "delta_pct" in comp
        # Last month has no data, so last_month_earnings = 0, delta_pct = 0
        assert comp["last_month_earnings"] == 0.0
        assert comp["delta_pct"] == 0.0
    finally:
        cleanup(test_dir)


def test_empty_month_returns_zeros():
    """Empty month returns all zeros, not crashes."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2025-01")
        assert ae["total_sales"] == 0.0
        assert ae["cogs"] == 0.0
        assert ae["actual_earnings"] == 0.0
        assert ae["expenses_by_category"] == []
        assert ae["cash_reality"]["cash_in_drawer"] == 0.0
    finally:
        cleanup(test_dir)


def test_owed_to_you_includes_customer_credit():
    """owed_to_you = sum of customers.total_credit."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2026-08")
        # Sample data: Credit Customer has total_credit = 5500
        assert ae["cash_reality"]["owed_to_you"] == 5500.0, \
            f"Expected owed_to_you=5500, got {ae['cash_reality']['owed_to_you']}"
    finally:
        cleanup(test_dir)


def test_you_owe_suppliers_includes_credit_bills():
    """you_owe_suppliers = sum of unpaid credit bills."""
    test_dir = setup_test_db()
    try:
        from app import shop
        ae = shop.get_actual_earnings("2026-08")
        # Sample data: Bill 2 is credit 7,500
        assert ae["cash_reality"]["you_owe_suppliers"] == 7500.0, \
            f"Expected you_owe_suppliers=7500, got {ae['cash_reality']['you_owe_suppliers']}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_actual_earnings_identity()
    test_owner_draw_excluded()
    test_cash_reality_fields_present()
    test_purchases_shown_separately()
    test_expenses_by_category_with_budget()
    test_comparison_with_last_month()
    test_empty_month_returns_zeros()
    test_owed_to_you_includes_customer_credit()
    test_you_owe_suppliers_includes_credit_bills()
    print("\n✅ ALL PHASE 3 ACTUAL EARNINGS TESTS PASSED")
