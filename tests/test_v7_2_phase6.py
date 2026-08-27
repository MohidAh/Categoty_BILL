"""v7.2 Phase 6 — Kill Switch Sweep.

Verifies that when the AI kill switch is ON:
1. AI calls (ai_call, agent, help_assistant) are blocked
2. Heuristic features (trends, break-even, margin alerts, internal signals) continue
3. Kill switch state is correctly reported via /api/ai/kill-switch
4. Kill switch state can be toggled via POST /api/ai/kill-switch
5. Kill switch persists across db reinit
6. Help system returns faq_fuzzy (not AI) when kill switch is ON
7. ai_router.ai_call returns a 'disabled' response shape

Covers the "AI stops, heuristics continue" guarantee.
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_p6_")
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


def test_kill_switch_reports_correct_state():
    """GET /api/ai/kill-switch returns the current state."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import ai_kill_switch_route
        # Default OFF
        r = ai_kill_switch_route()
        assert r["disabled"] is False
        # Toggle ON
        _set_kill_switch(True)
        r = ai_kill_switch_route()
        assert r["disabled"] is True
        # Toggle OFF
        _set_kill_switch(False)
        r = ai_kill_switch_route()
        assert r["disabled"] is False
    finally:
        cleanup(test_dir)


def test_toggle_kill_switch_via_endpoint():
    """POST /api/ai/kill-switch toggles the state and returns the new state."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import toggle_ai_kill_switch
        r = toggle_ai_kill_switch({"enabled": 1})
        assert r["ok"] is True
        assert r["disabled"] is True
        r = toggle_ai_kill_switch({"enabled": 0})
        assert r["ok"] is True
        assert r["disabled"] is False
    finally:
        cleanup(test_dir)


def test_ai_call_blocked_when_kill_switch_on():
    """ai_call returns a 'disabled' response when kill switch is ON."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.ai_router import ai_call
        r = ai_call("bi_chat", {"q": "test"}, execute_fn=lambda: {"response": "should not be called"})
        assert r["disabled"] is True
        assert r["response"] == ""
        assert r["provider"] == "none"
    finally:
        cleanup(test_dir)


def test_ai_call_runs_when_kill_switch_off():
    """ai_call executes the function when kill switch is OFF."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(False)
        from app.ai_router import ai_call
        called = {"n": 0}
        def fake_execute():
            called["n"] += 1
            return {"response": "real answer", "provider": "groq", "tokens_in": 10, "tokens_out": 20}
        r = ai_call("bi_chat_test", {"q": "test"}, execute_fn=fake_execute)
        assert r["disabled"] is False
        assert r["response"] == "real answer"
        assert called["n"] == 1, "execute_fn should have been called once"
    finally:
        cleanup(test_dir)


def test_agent_blocked_when_kill_switch_on():
    """Agent returns kill_switch trace + disabled message when kill switch is ON."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.agent import run_agent
        r = run_agent("What is my margin?")
        assert "disabled" in r["answer"].lower() or "kill" in r["answer"].lower()
        steps = [s.get("step") for s in r["tool_trace"]]
        assert "kill_switch" in steps
    finally:
        cleanup(test_dir)


def test_agent_runs_when_kill_switch_off():
    """Agent runs tools and returns a real answer when kill switch is OFF."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(False)
        from app.agent import run_agent
        r = run_agent("What is my actual overall margin?")
        # Should NOT have a kill_switch step
        steps = [s.get("step") for s in r["tool_trace"]]
        assert "kill_switch" not in steps
        # Should have at least one tool call
        assert "tool_call" in steps
        # Answer should contain a number (margin %)
        assert any(c.isdigit() for c in r["answer"])
    finally:
        cleanup(test_dir)


def test_heuristics_continue_when_kill_switch_on():
    """Heuristic features (trends, break-even, margin alerts, internal signals) are NOT blocked by kill switch."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        # Internal trend signals — should still work
        from app.ext_intel import get_internal_trend_signals, check_auto_confirm_bills, check_recurring_detection
        sig = get_internal_trend_signals()
        assert isinstance(sig, list)
        # auto-confirm check — should still work
        ac = check_auto_confirm_bills()
        assert "auto_confirmed" in ac and "pending" in ac
        # recurring detection — should still work
        rec = check_recurring_detection()
        assert isinstance(rec, list)
        # Break-even — should still work
        from app.extensions import get_break_even
        be = get_break_even()
        assert "daily_target" in be
        # Margin alerts — should still work
        from app.extensions import get_margin_alerts
        alerts = get_margin_alerts()
        assert isinstance(alerts, list)
        # Trend alerts — should still work
        from app.trends import get_trend_alerts
        ta = get_trend_alerts()
        assert isinstance(ta, list)
    finally:
        cleanup(test_dir)


def test_profit_endpoints_continue_when_kill_switch_on():
    """Profit / margins / cash-buckets endpoints are NOT blocked by kill switch."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.profit import get_margins, get_monthly_profit, get_cash_buckets, get_ytd_profit
        m = get_margins()
        assert "actual_overall_margin" in m
        mp = get_monthly_profit()
        assert "sales" in mp
        cb = get_cash_buckets()
        assert "cash_in_drawer" in cb
        ytd = get_ytd_profit()
        assert "ytd_sales" in ytd
    finally:
        cleanup(test_dir)


def test_help_system_degrades_when_kill_switch_on():
    """Help system returns faq_fuzzy or 'none' (not 'ai') when kill switch is ON."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.help_system import answer_help_question
        # Ask a question that's likely in the FAQ
        r = answer_help_question("How do I make a sale?")
        # Source should be faq or faq_fuzzy, not ai
        assert r["source"] in ("faq", "faq_fuzzy", "none"), f"unexpected source: {r['source']}"
        assert "answer" in r
    finally:
        cleanup(test_dir)


def test_kill_switch_persists_across_reinit():
    """Kill switch state survives a db reinit (init() uses INSERT-if-not-exists)."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app import db
        db.init()
        from app.ai_router import is_ai_disabled
        assert is_ai_disabled() is True
    finally:
        cleanup(test_dir)


def test_kill_switch_off_by_default():
    """Kill switch defaults to OFF when DB is initialized."""
    test_dir = setup_test_db()
    try:
        from app.ai_router import is_ai_disabled
        assert is_ai_disabled() is False, "kill switch should default to OFF"
    finally:
        cleanup(test_dir)


def test_kill_switch_blocks_help_via_endpoint():
    """POST /api/help/ask with kill switch ON never returns source='ai'."""
    test_dir = setup_test_db()
    try:
        _set_kill_switch(True)
        from app.routers.extensions import help_ask_route, HelpQuestionIn
        r = help_ask_route(HelpQuestionIn(question="How do I make a sale?"))
        assert r["source"] != "ai", f"AI should be blocked but source was {r['source']}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_kill_switch_reports_correct_state(); print("OK kill switch reports correct state")
    test_toggle_kill_switch_via_endpoint(); print("OK toggle kill switch via endpoint")
    test_ai_call_blocked_when_kill_switch_on(); print("OK ai_call blocked when kill switch ON")
    test_ai_call_runs_when_kill_switch_off(); print("OK ai_call runs when kill switch OFF")
    test_agent_blocked_when_kill_switch_on(); print("OK agent blocked when kill switch ON")
    test_agent_runs_when_kill_switch_off(); print("OK agent runs when kill switch OFF")
    test_heuristics_continue_when_kill_switch_on(); print("OK heuristics continue when kill switch ON")
    test_profit_endpoints_continue_when_kill_switch_on(); print("OK profit endpoints continue when kill switch ON")
    test_help_system_degrades_when_kill_switch_on(); print("OK help system degrades when kill switch ON")
    test_kill_switch_persists_across_reinit(); print("OK kill switch persists across reinit")
    test_kill_switch_off_by_default(); print("OK kill switch OFF by default")
    test_kill_switch_blocks_help_via_endpoint(); print("OK kill switch blocks help via endpoint")
    print("\nALL v7.2 PHASE 6 TESTS PASSED")
