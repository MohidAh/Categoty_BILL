"""v5.0 Phase 8 — Store Profit Dashboard tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p8v5_")
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



def test_dashboard_returns_6_kpi_groups():
    test_dir = setup_test_db()
    try:
        from app import profit
        d = profit.get_store_profit_dashboard()
        for key in ("current_stock", "current_margins", "daily", "monthly", "ytd", "cash"):
            assert key in d, f"Missing KPI group: {key}"
    finally:
        cleanup(test_dir)


def test_dashboard_answers_9_questions():
    """Each of the owner's 9 questions must be answerable from the dashboard response."""
    test_dir = setup_test_db()
    try:
        from app import profit
        d = profit.get_store_profit_dashboard()
        # 1. How much stock do I have now?
        assert "total_qty" in d["current_stock"]
        assert "total_value" in d["current_stock"]
        # 2. Current average cost?
        assert "per_category" in d["current_stock"]
        if d["current_stock"]["per_category"]:
            assert "avg_cost" in d["current_stock"]["per_category"][0]
        # 3. Each category's current margin?
        assert "categories" in d["current_margins"]
        # 4. Today's sales & gross profit?
        assert "sales" in d["daily"]
        assert "gross_profit" in d["daily"]
        # 5. This month's actual gross margin?
        assert "monthly_margin" in d["monthly"]
        # 6. YTD margin since opening?
        assert "ytd_margin" in d["ytd"]
        # 7. Operating profit after expenses?
        assert "operating_profit" in d["monthly"]
        # 8. How much to reserve for next purchase?
        assert "stock_reserve_days" in d["cash"]
        # 9. How much can I safely withdraw?
        assert "safe_withdrawal_weekly" in d["cash"]
    finally:
        cleanup(test_dir)


def test_dashboard_actual_overall_margin_is_primary_kpi():
    """The actual_overall_margin must be present and equal to total_gp / total_sales."""
    test_dir = setup_test_db()
    try:
        from app import profit
        d = profit.get_store_profit_dashboard()
        cm = d["current_margins"]
        if cm["total_sales"] > 0:
            expected = round((cm["total_gross_profit"] / cm["total_sales"]) * 100, 2)
            assert abs(cm["actual_overall_margin"] - expected) < 0.01
    finally:
        cleanup(test_dir)


def test_dashboard_endpoint_works():
    test_dir = setup_test_db()
    try:
        from app.routers.profit import profit_dashboard
        d = profit_dashboard()
        assert "current_stock" in d
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_dashboard_returns_6_kpi_groups()
    print("✓ test_dashboard_returns_6_kpi_groups")
    test_dashboard_answers_9_questions()
    print("✓ test_dashboard_answers_9_questions")
    test_dashboard_actual_overall_margin_is_primary_kpi()
    print("✓ test_dashboard_actual_overall_margin_is_primary_kpi")
    test_dashboard_endpoint_works()
    print("✓ test_dashboard_endpoint_works")
    print("\n✅ ALL PHASE 8 DASHBOARD TESTS PASSED")
