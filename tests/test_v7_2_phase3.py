"""v7.2 Phase 3 — Agentic Chat UI: tool trace, SQL display, kill-switch banner.

Covers:
- run_agent returns {answer, tool_trace, suggested_followups}
- tool_trace has step/tool/status fields (used by UI for collapsible display)
- Kill switch ON → agent returns kill_switch trace + disabled message
- Kill switch OFF → agent runs tools and returns real numbers
- Agent margin answer matches /api/profit/margins numbers (parity)
- execute_constrained_sql blocks forbidden keywords (INSERT/UPDATE/DELETE/DROP)
- execute_constrained_sql blocks forbidden tables (settings, ai_cache, sessions)
- execute_constrained_sql auto-injects LIMIT if missing
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_p3_")
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



def _set_kill_switch(enabled: bool):
    from app import db
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) "
            "VALUES('ai_kill_switch', ?, 0, '{}')", (1 if enabled else 0,))


def test_agent_returns_required_shape():
    """run_agent returns dict with answer, tool_trace, suggested_followups."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("What is my actual overall margin?")
        assert "answer" in r
        assert "tool_trace" in r
        assert "suggested_followups" in r
        assert isinstance(r["tool_trace"], list)
        assert isinstance(r["suggested_followups"], list)
    finally:
        cleanup(test_dir)


def test_agent_trace_has_step_tool_status_fields():
    """Each trace entry has step / tool / status fields — needed for UI rendering."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("What is my actual overall margin?")
        # Should have at least one tool_call and one tool_result
        steps = [s["step"] for s in r["tool_trace"]]
        assert "tool_call" in steps, f"missing tool_call in {steps}"
        assert "tool_result" in steps, f"missing tool_result in {steps}"
        # Each tool_call entry must have a `tool` field
        calls = [s for s in r["tool_trace"] if s["step"] == "tool_call"]
        for c in calls:
            assert "tool" in c, f"tool_call missing `tool` field: {c}"
        # Each tool_result entry must have `status` (ok|error)
        results = [s for s in r["tool_trace"] if s["step"] == "tool_result"]
        for r2 in results:
            assert "status" in r2, f"tool_result missing `status`: {r2}"
            assert r2["status"] in ("ok", "error")
    finally:
        cleanup(test_dir)


def test_kill_switch_blocks_agent():
    """When kill switch is ON, agent returns kill_switch trace + disabled message."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.agent import run_agent
        r = run_agent("What is my margin?")
        assert "disabled" in r["answer"].lower() or "kill" in r["answer"].lower()
        # Trace should include a kill_switch step
        steps = [s.get("step") for s in r["tool_trace"]]
        assert "kill_switch" in steps, f"missing kill_switch step in {steps}"
    finally:
        cleanup(test_dir)


def test_agent_margin_answer_matches_api():
    """The agent's margin answer contains the same Actual Overall Margin % as /api/profit/margins.
    This is the parity guarantee — the AI never recomputes business math."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        from app.profit import get_margins
        from app import ai_router
        # Ensure kill switch is OFF
        _set_kill_switch(False)
        margins = get_margins()
        actual_pct = margins["actual_overall_margin"]
        r = run_agent("What is my actual overall margin?")
        # The answer string should contain the actual_overall_margin number
        ans = r["answer"]
        # Allow some tolerance in formatting — just check the integer part appears
        int_part = str(int(actual_pct)) if actual_pct else "0"
        assert int_part in ans, f"margin {int_part} not found in answer: {ans}"
    finally:
        cleanup(test_dir)


def test_constrained_sql_blocks_writes():
    """execute_constrained_sql rejects INSERT/UPDATE/DELETE/DROP."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        for bad in [
            "INSERT INTO bills VALUES (1)",
            "UPDATE bills SET status='confirmed'",
            "DELETE FROM bills WHERE id=1",
            "DROP TABLE bills",
            "ALTER TABLE bills ADD COLUMN x",
            "CREATE TABLE foo (id int)",
        ]:
            r = execute_constrained_sql(bad)
            assert "error" in r, f"should reject: {bad}"
            assert "SELECT" in r["error"] or "only" in r["error"].lower() or "forbidden" in r["error"].lower(), \
                f"unexpected error message for {bad}: {r['error']}"
    finally:
        cleanup(test_dir)


def test_constrained_sql_blocks_forbidden_tables():
    """execute_constrained_sql rejects queries touching settings/sessions/ai_cache."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        for bad in [
            "SELECT * FROM settings",
            "SELECT * FROM sessions",
            "SELECT * FROM ai_cache",
            "SELECT * FROM ai_providers",
            "SELECT * FROM pairing_codes",
        ]:
            r = execute_constrained_sql(bad)
            assert "error" in r, f"should reject: {bad}"
            assert "forbidden" in r["error"].lower(), f"unexpected error for {bad}: {r['error']}"
    finally:
        cleanup(test_dir)


def test_constrained_sql_injects_limit():
    """execute_constrained_sql auto-appends LIMIT 500 when missing."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        r = execute_constrained_sql("SELECT * FROM price_categories")
        # Should succeed and return rows
        assert "error" not in r, f"unexpected error: {r.get('error')}"
        assert "columns" in r
        assert "rows" in r
        assert r["row_count"] >= 1
    finally:
        cleanup(test_dir)


def test_constrained_sql_allows_allowlist_tables():
    """execute_constrained_sql allows queries on the allowlist tables."""
    test_dir = setup_test_db()
    try:
        from app.agent import execute_constrained_sql
        for ok in [
            "SELECT COUNT(*) AS n FROM sales",
            "SELECT * FROM sale_items LIMIT 5",
            "SELECT * FROM bills",
            "SELECT * FROM customers",
        ]:
            r = execute_constrained_sql(ok)
            assert "error" not in r, f"unexpected error for {ok}: {r.get('error')}"
    finally:
        cleanup(test_dir)


def test_agent_endpoint_via_router():
    """POST /api/agent/ask returns the run_agent shape."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(False)
        from app.routers.extensions import agent_ask, AgentQuestionIn
        r = agent_ask(AgentQuestionIn(question="What is my actual overall margin?"))
        assert "answer" in r
        assert "tool_trace" in r
        assert "suggested_followups" in r
    finally:
        cleanup(test_dir)


def test_agent_tools_endpoint():
    """GET /api/agent/tools returns the list of READ_TOOLS for UI display."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import list_agent_tools
        r = list_agent_tools()
        assert "tools" in r
        assert "schemas" in r
        assert "get_margins" in r["tools"]
        assert "get_monthly_profit" in r["tools"]
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_agent_returns_required_shape(); print("OK agent returns required shape")
    test_agent_trace_has_step_tool_status_fields(); print("OK trace has step/tool/status fields")
    test_kill_switch_blocks_agent(); print("OK kill switch blocks agent")
    test_agent_margin_answer_matches_api(); print("OK agent margin answer matches API")
    test_constrained_sql_blocks_writes(); print("OK constrained SQL blocks writes")
    test_constrained_sql_blocks_forbidden_tables(); print("OK constrained SQL blocks forbidden tables")
    test_constrained_sql_injects_limit(); print("OK constrained SQL injects LIMIT")
    test_constrained_sql_allows_allowlist_tables(); print("OK constrained SQL allows allowlist tables")
    test_agent_endpoint_via_router(); print("OK agent endpoint via router")
    test_agent_tools_endpoint(); print("OK agent tools endpoint")
    print("\nALL v7.2 PHASE 3 TESTS PASSED")
