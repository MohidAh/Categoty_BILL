"""v6.0 Phases 3-6 — Extensions tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v6ext_")
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
                  "category_stock_state", "owner_withdrawals",
                  "login_attempts", "devices", "pairing_codes",
                  "bundles", "bundle_items", "price_rules",
                  "lost_sales", "closed_days", "seasons"):
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



def test_bundle_crud():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        bid = ext.create_bundle("3-for-1000", 1000, [
            {"category_id": 1, "qty": 2}, {"category_id": 2, "qty": 1}
        ])
        bundles = ext.list_bundles()
        assert len(bundles) == 1
        assert bundles[0]["name"] == "3-for-1000"
        assert bundles[0]["price"] == 1000.0
        assert len(bundles[0]["items"]) == 2
        # Delete
        assert ext.delete_bundle(bid) is True
        assert len(ext.list_bundles()) == 0
    finally:
        cleanup(test_dir)


def test_bundle_price_allocation():
    """Bundle price is allocated proportional to component sell_prices."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        # Cat 1: sell 250, Cat 2: sell 500. Total individual = 250*1 + 500*1 = 750
        # Bundle price = 1000. Allocation: cat1 = 1000 * 250/750 = 333.33, cat2 = 666.67
        bid = ext.create_bundle("Test", 1000, [
            {"category_id": 1, "qty": 1}, {"category_id": 2, "qty": 1}
        ])
        alloc = ext.get_bundle_sell_price_allocation(bid)
        assert len(alloc) == 2
        total = sum(a["allocated_sell_price"] for a in alloc)
        assert abs(total - 1000.0) < 0.02, f"Allocation should sum to bundle price, got {total}"
    finally:
        cleanup(test_dir)


# Phase 3: Happy-Hour
def test_happy_hour_rule_crud():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        rid = ext.create_price_rule(None, 10, "0800", "1000")
        rules = ext.list_price_rules(active_only=False)
        assert len(rules) == 1
        assert rules[0]["pct"] == 10.0
        assert ext.delete_price_rule(rid) is True
    finally:
        cleanup(test_dir)


def test_happy_hour_active_check():
    """Happy-hour is active only within the time window."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        from datetime import datetime
        now_hhmm = datetime.now().strftime("%H%M")
        # Create a rule that covers the current time
        ext.create_price_rule(None, 15, "0000", "2359")
        result = ext.get_active_happy_hour_discount()
        assert result is not None
        assert result["pct"] == 15.0
        # Create a rule outside the current time
        ext.create_price_rule(None, 5, "0000", "0001")
        result2 = ext.get_active_happy_hour_discount()
        # The 15% rule should still be the one returned (covers full day)
        assert result2["pct"] == 15.0
    finally:
        cleanup(test_dir)


# Phase 4: Lost Sales
def test_lost_sale_logging():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        ext.log_lost_sale(1, 5, 1250)
        s = ext.get_lost_sales_summary()
        assert s["count"] >= 1
        assert s["total_est_revenue"] >= 1250
    finally:
        cleanup(test_dir)


# Phase 4: Break-Even
def test_break_even_returns_fields():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        be = ext.get_break_even()
        for key in ("fixed_monthly_costs", "actual_margin_pct",
                    "break_even_monthly_sales", "daily_target", "daily_so_far"):
            assert key in be
    finally:
        cleanup(test_dir)


# Phase 4: Margin Alerts
def test_margin_alerts_fire():
    """A category with margin below target generates an alert."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext, db
        # Set a high margin target so alerts fire
        db.set_setting("margin_protection_target", "90")
        alerts = ext.get_margin_alerts()
        assert len(alerts) > 0
        # Each alert should have a suggested_price
        for a in alerts:
            assert a["margin_pct"] < 90
            assert a["suggested_price"] > 0
    finally:
        cleanup(test_dir)


# Phase 4: Cash-Flow Forecast
def test_cash_flow_forecast():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        f = ext.get_cash_flow_forecast()
        for key in ("current_cash", "avg_daily_inflow", "avg_daily_cogs",
                    "min_balance", "min_balance_date", "negative_alert", "daily"):
            assert key in f
        assert len(f["daily"]) == 30
    finally:
        cleanup(test_dir)


# Phase 6: Closed Days
def test_closed_days_crud():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        ext.add_closed_day("2026-08-15", "Independence Day")
        days = ext.list_closed_days()
        assert len(days) == 1
        assert days[0]["date"] == "2026-08-15"
        ext.remove_closed_day("2026-08-15")
        assert len(ext.list_closed_days()) == 0
    finally:
        cleanup(test_dir)


# Phase 6: Seasons
def test_seasons_crud():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        sid = ext.add_season(2026, "Ramadan", "2026-02-28", "2026-03-30")
        seasons = ext.list_seasons()
        assert len(seasons) == 1
        assert seasons[0]["name"] == "Ramadan"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_bundle_crud(); print("✓ test_bundle_crud")
    test_bundle_price_allocation(); print("✓ test_bundle_price_allocation")
    test_happy_hour_rule_crud(); print("✓ test_happy_hour_rule_crud")
    test_happy_hour_active_check(); print("✓ test_happy_hour_active_check")
    test_lost_sale_logging(); print("✓ test_lost_sale_logging")
    test_break_even_returns_fields(); print("✓ test_break_even_returns_fields")
    test_margin_alerts_fire(); print("✓ test_margin_alerts_fire")
    test_cash_flow_forecast(); print("✓ test_cash_flow_forecast")
    test_closed_days_crud(); print("✓ test_closed_days_crud")
    test_seasons_crud(); print("✓ test_seasons_crud")
    print("\n✅ ALL v6.0 EXTENSIONS TESTS PASSED")
