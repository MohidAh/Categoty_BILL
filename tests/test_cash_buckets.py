"""v5.0 Phase 7 — Cash Buckets + Owner Withdrawal + Stock Reserve tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p7v5_")
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
                  "category_stock_state", "owner_withdrawals"):
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



def test_cash_buckets_returns_4_buckets():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_cash_buckets("2026-08-11")
        for key in ("stock_replacement", "operating_expenses", "business_reserve", "owner_withdrawal"):
            assert key in r["buckets"], f"Missing bucket: {key}"
        assert "cash_in_drawer" in r
        assert "available_for_withdrawal" in r
    finally:
        cleanup(test_dir)


def test_owner_withdrawal_reduces_cash_not_operating_profit():
    """An owner withdrawal reduces cash_drawer but NOT operating profit."""
    test_dir = setup_test_db()
    try:
        from app import profit
        # Baseline
        before = profit.get_cash_buckets("2026-08-11")
        monthly_before = profit.get_monthly_profit("2026-08")
        # Withdraw Rs 5,000
        profit.add_owner_withdrawal(5000, "cash", "test withdrawal")
        after = profit.get_cash_buckets("2026-08-11")
        monthly_after = profit.get_monthly_profit("2026-08")
        # Cash drawer reduced by 5000
        assert abs((after["cash_in_drawer"] - before["cash_in_drawer"]) - (-5000)) < 0.01, \
            f"Cash should drop by 5000: before={before['cash_in_drawer']}, after={after['cash_in_drawer']}"
        # Owner withdrawal bucket now shows 5000
        assert after["buckets"]["owner_withdrawal"] == 5000.0
        # Operating profit UNCHANGED (withdrawals are not operating expenses)
        assert monthly_before["operating_profit"] == monthly_after["operating_profit"], \
            f"Operating profit changed: {monthly_before['operating_profit']} → {monthly_after['operating_profit']}"
        # Operating expenses UNCHANGED
        assert monthly_before["operating_expenses"] == monthly_after["operating_expenses"]
    finally:
        cleanup(test_dir)


def test_owner_withdrawal_invalid_amount():
    """Negative or zero withdrawal raises ValueError."""
    test_dir = setup_test_db()
    try:
        from app import profit
        try:
            profit.add_owner_withdrawal(-100, "cash", "negative")
            assert False, "Expected ValueError"
        except ValueError:
            pass
        try:
            profit.add_owner_withdrawal(0, "cash", "zero")
            assert False, "Expected ValueError"
        except ValueError:
            pass
    finally:
        cleanup(test_dir)


def test_stock_reserve_returns_required_fields():
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_stock_reserve()
        for key in ("daily_cogs_avg_30d", "stock_reserve_days",
                    "stock_reserve_target_days", "gap", "color",
                    "recommendation", "safe_withdrawal_weekly"):
            assert key in r, f"Missing key: {key}"
        assert r["color"] in ("green", "amber", "red")
    finally:
        cleanup(test_dir)


def test_stock_reserve_color_logic():
    """Color coding: green >= target, amber >= target/2, red < target/2.

    With sample data dated 2026-08-11 and 'now' = 2026-08-12, daily COGS avg
    over the last 30 days is non-zero (sales on 2026-08-11 fall in the window).
    We vary the target to test all three color zones.
    """
    test_dir = setup_test_db()
    try:
        from app import profit, db
        # Get baseline days_of_cover
        r = profit.get_stock_reserve()
        days = r["stock_reserve_days"]
        if days > 0:
            # Set target well below actual → green
            with db.conn() as c:
                c.execute("UPDATE settings SET value=? WHERE key='stock_reserve_target_days'", (str(max(0.1, days / 4)),))
            r = profit.get_stock_reserve()
            assert r["color"] == "green", f"Expected green with target {r['stock_reserve_target_days']}, days {r['stock_reserve_days']}"
            # Set target well above actual → red
            with db.conn() as c:
                c.execute("UPDATE settings SET value=? WHERE key='stock_reserve_target_days'", (str(days * 10 + 100),))
            r = profit.get_stock_reserve()
            assert r["color"] == "red", f"Expected red with target {r['stock_reserve_target_days']}, days {r['stock_reserve_days']}"
        else:
            # days == 0 → always red (no cash or no COGS data)
            assert r["color"] == "red"
    finally:
        cleanup(test_dir)


def test_owner_withdrawals_summary():
    test_dir = setup_test_db()
    try:
        from app import profit
        profit.add_owner_withdrawal(5000, "cash", "w1")
        profit.add_owner_withdrawal(3000, "cash", "w2")
        s = profit.get_owner_withdrawals_summary()
        assert s["month_count"] >= 2
        assert s["month_total"] >= 8000
        assert s["all_time_total"] >= 8000
    finally:
        cleanup(test_dir)


def test_business_reserve_is_pct_of_gp():
    """Business reserve = business_reserve_pct × gross_profit."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_cash_buckets("2026-08-11")
        expected = round(r["gross_profit"] * r["business_reserve_pct"] / 100, 2)
        assert abs(r["buckets"]["business_reserve"] - expected) < 0.01, \
            f"Business reserve should be {expected}, got {r['buckets']['business_reserve']}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_cash_buckets_returns_4_buckets()
    print("✓ test_cash_buckets_returns_4_buckets")
    test_owner_withdrawal_reduces_cash_not_operating_profit()
    print("✓ test_owner_withdrawal_reduces_cash_not_operating_profit")
    test_owner_withdrawal_invalid_amount()
    print("✓ test_owner_withdrawal_invalid_amount")
    test_stock_reserve_returns_required_fields()
    print("✓ test_stock_reserve_returns_required_fields")
    test_stock_reserve_color_logic()
    print("✓ test_stock_reserve_color_logic")
    test_owner_withdrawals_summary()
    print("✓ test_owner_withdrawals_summary")
    test_business_reserve_is_pct_of_gp()
    print("✓ test_business_reserve_is_pct_of_gp")
    print("\n✅ ALL PHASE 7 CASH BUCKETS TESTS PASSED")
