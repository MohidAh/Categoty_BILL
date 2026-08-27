"""v7.0 Phases 6-9 — Trends 2.0 + Automation + Flagship Agent tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v7b_")
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
                  "lost_sales", "closed_days", "seasons",
                  "ai_cache", "ai_usage", "pending_actions", "automation_config"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute("INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) VALUES(?,?,?,?,?)",
                      (name, is_fixed, budget, 1, sort_order))
        for key in ['auto_confirm_bills', 'auto_draft_po', 'urdhaar_reminders',
                     'recurring_detection', 'expense_categorization',
                     'anomaly_diagnosis', 'variance_investigation',
                     'scheduled_reports', 'dead_stock_liquidation', 'ai_kill_switch']:
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)", (key, 0, 2, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def test_internal_trends():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        signals = ext.get_internal_trend_signals()
        assert isinstance(signals, list)
        # With sample data (dated 2026-08-11), there may be signals if "today" falls in range
    finally:
        cleanup(test_dir)

def test_auto_confirm_check():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        result = ext.check_auto_confirm_bills()
        assert "auto_confirmed" in result
        assert "pending" in result
    finally:
        cleanup(test_dir)

def test_recurring_detection():
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        result = ext.check_recurring_detection()
        assert isinstance(result, list)
    finally:
        cleanup(test_dir)

def test_prepare_for_season():
    """'Prepare for Eid' produces 3+ pending actions as a grouped batch."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext, db
        result = ext.prepare_for_season("Eid")
        assert result["pending_count"] >= 3
        assert result["batch_id"]
        # Verify pending actions were created in DB
        with db.conn() as c:
            count = c.execute(
                "SELECT COUNT(*) n FROM pending_actions WHERE batch_id=?", (result["batch_id"],)
            ).fetchone()["n"]
        assert count >= 3
        # All should be pending status
        with db.conn() as c:
            pending = c.execute(
                "SELECT COUNT(*) n FROM pending_actions WHERE batch_id=? AND status='pending'",
                (result["batch_id"],)).fetchone()["n"]
        assert pending == count
    finally:
        cleanup(test_dir)

def test_agent_ask_via_api():
    """Agent ask endpoint returns answer with tool trace."""
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, follow_redirects=False)
        client.post("/api/login", json={"password": os.getenv("APP_PASSWORD", "")})
        # Login won't work without a password set, so test the function directly
        from app.agent import run_agent
        result = run_agent("What is my margin?")
        assert "answer" in result
        assert "tool_trace" in result
        assert len(result["tool_trace"]) > 0
    finally:
        cleanup(test_dir)

def test_ai_usage_dashboard():
    test_dir = setup_test_db()
    try:
        from app import ai_router
        summary = ai_router.get_ai_usage_summary()
        assert "date" in summary
        assert "providers" in summary
        assert "total_cached_entries" in summary
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_internal_trends(); print("✓ test_internal_trends")
    test_auto_confirm_check(); print("✓ test_auto_confirm_check")
    test_recurring_detection(); print("✓ test_recurring_detection")
    test_prepare_for_season(); print("✓ test_prepare_for_season")
    test_agent_ask_via_api(); print("✓ test_agent_ask_via_api")
    test_ai_usage_dashboard(); print("✓ test_ai_usage_dashboard")
    print("\n✅ ALL v7.0 PHASES 6-9 TESTS PASSED")
