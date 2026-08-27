"""v7.0 Phases 2-5 — AI infrastructure tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v7_")
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
        # Re-seed automation_config (wiped above)
        for key in ['auto_confirm_bills', 'auto_draft_po', 'urdhaar_reminders',
                     'recurring_detection', 'expense_categorization',
                     'anomaly_diagnosis', 'variance_investigation',
                     'scheduled_reports', 'dead_stock_liquidation',
                     'ai_kill_switch']:
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)",
                      (key, 0, 2, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def test_ai_cache_hit_miss():
    test_dir = setup_test_db()
    try:
        from app import ai_router
        call_count = [0]
        def fake_call():
            call_count[0] += 1
            return {"response": "test answer", "provider": "groq", "tokens_in": 100, "tokens_out": 50}
        # First call: cache miss → executes
        r1 = ai_router.ai_call("test_task", {"q": "hello"}, execute_fn=fake_call)
        assert r1["cached"] is False
        assert call_count[0] == 1
        # Second call: cache hit → does NOT execute
        r2 = ai_router.ai_call("test_task", {"q": "hello"}, execute_fn=fake_call)
        assert r2["cached"] is True
        assert call_count[0] == 1  # still 1 — no new API call
    finally:
        cleanup(test_dir)

def test_ai_kill_switch():
    test_dir = setup_test_db()
    try:
        from app import ai_router, db
        # Enable kill switch
        with db.conn() as c:
            c.execute("UPDATE automation_config SET enabled=1 WHERE key='ai_kill_switch'")
        assert ai_router.is_ai_disabled() is True
        r = ai_router.ai_call("test", {}, execute_fn=lambda: {"response": "should not run"})
        assert r["disabled"] is True
        assert r["response"] == ""
    finally:
        cleanup(test_dir)

def test_ai_usage_logged():
    test_dir = setup_test_db()
    try:
        from app import ai_router, db
        # Call twice — second should be a cache hit
        ai_router.ai_call("test_task", {"q": "x"}, execute_fn=lambda: {"response": "ans", "provider": "groq"})
        ai_router.ai_call("test_task", {"q": "x"}, execute_fn=lambda: {"response": "ans", "provider": "groq"})
        with db.conn() as c:
            count = c.execute("SELECT COUNT(*) n FROM ai_usage").fetchone()["n"]
        assert count >= 2  # 1 API call + 1 cache hit
    finally:
        cleanup(test_dir)

# Phase 3: Agent
def test_agent_returns_real_numbers():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        result = run_agent("What is my actual overall margin?")
        assert "answer" in result
        assert "tool_trace" in result
        assert len(result["tool_trace"]) > 0
        # The answer should contain a percentage (from get_margins)
        assert "%" in result["answer"]
    finally:
        cleanup(test_dir)

def test_agent_multi_step():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        result = run_agent("What is my monthly profit and YTD margin?")
        # Should call at least 2 tools
        tool_calls = [t for t in result["tool_trace"] if t.get("step") == "tool_call"]
        assert len(tool_calls) >= 2
    finally:
        cleanup(test_dir)

def test_agent_6_iteration_cap():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        result = run_agent("margin monthly ytd cash break stock lost alert expense shift trend", max_iterations=3)
        tool_calls = [t for t in result["tool_trace"] if t.get("step") == "tool_call"]
        assert len(tool_calls) <= 3
    finally:
        cleanup(test_dir)

# Phase 4: Constrained SQL
def test_sql_select_allowed():
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("SELECT COUNT(*) AS n FROM sales")
        assert "error" not in result
        assert result["row_count"] >= 1
    finally:
        cleanup(test_dir)

def test_sql_blocks_drop():
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("DROP TABLE sales")
        assert "error" in result
        assert "SELECT" in result["error"] or "DROP" in result["error"]
    finally:
        cleanup(test_dir)

def test_sql_blocks_settings_table():
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("SELECT * FROM settings")
        assert "error" in result
        assert "forbidden" in result["error"].lower()
    finally:
        cleanup(test_dir)

def test_sql_injects_limit():
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        result = execute_constrained_sql("SELECT * FROM sales")
        assert "error" not in result, f"SQL failed: {result.get('error')}"
        assert result["row_count"] <= 500
    finally:
        cleanup(test_dir)

# Phase 5: Approval Queue
def test_pending_action_create_approve_reject():
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import create_pending_action, PendingActionCreate, approve_pending_action, reject_pending_action
        # Create
        pa = PendingActionCreate(action_type="draft_expense", payload={"amount": 500, "category": "Electricity"},
                                  reason="AI detected recurring electricity bill", impact_summary="Rs 500 expense")
        r = create_pending_action(pa)
        assert r["id"] > 0
        # Approve (executes)
        r2 = approve_pending_action(r["id"], {"approved_by": "manager"})
        assert r2["ok"] is True
        # Create another and reject
        pa2 = PendingActionCreate(action_type="draft_expense", payload={"amount": 1000, "category": "Rent"})
        r3 = create_pending_action(pa2)
        r4 = reject_pending_action(r3["id"])
        assert r4["ok"] is True
    finally:
        cleanup(test_dir)

def test_price_change_requires_pin():
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import create_pending_action, approve_pending_action, PendingActionCreate
        from fastapi import HTTPException
        pa = PendingActionCreate(action_type="apply_price_suggestion",
                                  payload={"category_id": 1, "new_price": 300})
        r = create_pending_action(pa)
        # Approve without PIN → should fail
        try:
            approve_pending_action(r["id"], {"approved_by": "manager"})
            assert False, "Should require PIN"
        except HTTPException as e:
            assert e.status_code == 403
        # The action should still be pending (not executed)
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT status FROM pending_actions WHERE id=?", (r["id"],)).fetchone()
        assert row["status"] == "pending"
    finally:
        cleanup(test_dir)

def test_automation_config_toggle():
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import update_automation_config, list_automation_config
        update_automation_config("auto_confirm_bills", {"enabled": 1, "level": 3})
        config = list_automation_config()
        auto_confirm = next(c for c in config["config"] if c["key"] == "auto_confirm_bills")
        assert auto_confirm["enabled"] == 1
        assert auto_confirm["level"] == 3
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_ai_cache_hit_miss(); print("✓ test_ai_cache_hit_miss")
    test_ai_kill_switch(); print("✓ test_ai_kill_switch")
    test_ai_usage_logged(); print("✓ test_ai_usage_logged")
    test_agent_returns_real_numbers(); print("✓ test_agent_returns_real_numbers")
    test_agent_multi_step(); print("✓ test_agent_multi_step")
    test_agent_6_iteration_cap(); print("✓ test_agent_6_iteration_cap")
    test_sql_select_allowed(); print("✓ test_sql_select_allowed")
    test_sql_blocks_drop(); print("✓ test_sql_blocks_drop")
    test_sql_blocks_settings_table(); print("✓ test_sql_blocks_settings_table")
    test_sql_injects_limit(); print("✓ test_sql_injects_limit")
    test_pending_action_create_approve_reject(); print("✓ test_pending_action_create_approve_reject")
    test_price_change_requires_pin(); print("✓ test_price_change_requires_pin")
    test_automation_config_toggle(); print("✓ test_automation_config_toggle")
    print("\n✅ ALL v7.0 PHASES 2-5 TESTS PASSED")
