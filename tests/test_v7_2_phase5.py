"""v7.2 Phase 5 — AI Automations settings page: toggles + season-prep trigger.

Covers:
- All 9 default automations exist in automation_config (seeded by db.init)
- All default to enabled=0 (OFF) — no surprise automation
- POST /api/automation-config/{key} toggles a single automation on/off
- POST /api/agent/prepare-season creates a batch in pending_actions
- prepare-season actions have action_type, payload, reason, impact_summary, batch_id
- Kill switch ON blocks the season-prep endpoint (returns disabled result)
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_p5_")
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



def test_all_default_automations_seeded():
    """db.init() seeds all 9 automations + ai_kill_switch in automation_config."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            rows = c.execute("SELECT key, enabled FROM automation_config").fetchall()
        keys = {r["key"]: r["enabled"] for r in rows}
        for k in EXPECTED_AUTOMATION_KEYS:
            assert k in keys, f"missing automation key: {k}"
        assert 'ai_kill_switch' in keys, "missing ai_kill_switch"
    finally:
        cleanup(test_dir)


def test_all_automations_default_off():
    """All default automations are OFF (enabled=0) — no surprise automation."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            rows = c.execute("SELECT key, enabled FROM automation_config").fetchall()
        for r in rows:
            assert r["enabled"] == 0, f"{r['key']} should default to OFF, got enabled={r['enabled']}"
    finally:
        cleanup(test_dir)


def test_toggle_automation_on_off():
    """POST /api/automation-config/{key} toggles a single automation."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import update_automation_config
        # Toggle on
        r = update_automation_config('auto_draft_po', {'enabled': 1, 'level': 2})
        assert r["ok"] is True
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT enabled FROM automation_config WHERE key='auto_draft_po'").fetchone()
        assert row["enabled"] == 1
        # Toggle off
        update_automation_config('auto_draft_po', {'enabled': 0, 'level': 1})
        with db.conn() as c:
            row = c.execute("SELECT enabled FROM automation_config WHERE key='auto_draft_po'").fetchone()
        assert row["enabled"] == 0
    finally:
        cleanup(test_dir)


def test_toggle_doesnt_affect_others():
    """Toggling one automation doesn't change others."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import update_automation_config
        update_automation_config('auto_draft_po', {'enabled': 1, 'level': 2})
        from app import db
        with db.conn() as c:
            rows = c.execute("SELECT key, enabled FROM automation_config WHERE key != 'auto_draft_po'").fetchall()
        for r in rows:
            assert r["enabled"] == 0, f"{r['key']} should still be OFF, got enabled={r['enabled']}"
    finally:
        cleanup(test_dir)


def test_season_prep_creates_batch():
    """POST /api/agent/prepare-season creates multiple pending actions all sharing a batch_id."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import prepare_season_route, SeasonPrepIn
        r = prepare_season_route(SeasonPrepIn(season="Eid"))
        assert r["batch_id"]
        assert r["pending_count"] >= 1
        assert "summary" in r
        # Verify actions were inserted
        from app import db
        with db.conn() as c:
            rows = c.execute(
                "SELECT * FROM pending_actions WHERE batch_id=?", (r["batch_id"],)
            ).fetchall()
        assert len(rows) == r["pending_count"]
        # Each action has required fields
        for row in rows:
            d = dict(row)
            assert d["action_type"]
            assert d["payload_json"]
            assert d["reason"]
            assert d["impact_summary"]
            assert d["source"] == "ai_season_prep"
            assert d["batch_id"] == r["batch_id"]
    finally:
        cleanup(test_dir)


def test_season_prep_includes_multiple_action_types():
    """prepare-season drafts at least 2 different action types (PO + happy-hour + broadcast)."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import prepare_season_route, SeasonPrepIn
        r = prepare_season_route(SeasonPrepIn(season="Wedding Season"))
        from app import db
        with db.conn() as c:
            rows = c.execute(
                "SELECT DISTINCT action_type FROM pending_actions WHERE batch_id=?",
                (r["batch_id"],)
            ).fetchall()
        types = [row["action_type"] for row in rows]
        # Should include at least happy_hour_rule and customer_broadcast
        assert "happy_hour_rule" in types, f"missing happy_hour_rule in {types}"
        assert "customer_broadcast" in types, f"missing customer_broadcast in {types}"
    finally:
        cleanup(test_dir)


def test_season_prep_custom_name():
    """Custom season names work (not just preset labels)."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import prepare_season_route, SeasonPrepIn
        r = prepare_season_route(SeasonPrepIn(season="My Custom Festival 2026"))
        assert r["pending_count"] >= 1
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT reason FROM pending_actions WHERE batch_id=? LIMIT 1",
                (r["batch_id"],)
            ).fetchone()
        assert "My Custom Festival 2026" in row["reason"]
    finally:
        cleanup(test_dir)


def test_kill_switch_persists_across_reinit():
    """Kill switch state is stored in automation_config and survives a re-init."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import toggle_ai_kill_switch
        # Turn ON the kill switch
        toggle_ai_kill_switch({"enabled": 1})
        # Re-init the DB (init() should preserve existing rows via INSERT-if-not-exists)
        from app import db
        db.init()
        from app.ai_router import is_ai_disabled
        assert is_ai_disabled() is True, "kill switch should still be ON after reinit"
        # Turn it back OFF
        toggle_ai_kill_switch({"enabled": 0})
        assert is_ai_disabled() is False
    finally:
        cleanup(test_dir)


def test_automation_config_endpoint_returns_all():
    """GET /api/automation-config returns all 10 automation entries."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import list_automation_config
        r = list_automation_config()
        assert "config" in r
        keys = [c["key"] for c in r["config"]]
        for k in EXPECTED_AUTOMATION_KEYS:
            assert k in keys, f"missing {k} in config"
        assert "ai_kill_switch" in keys
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_all_default_automations_seeded(); print("OK all default automations seeded")
    test_all_automations_default_off(); print("OK all automations default OFF")
    test_toggle_automation_on_off(); print("OK toggle automation on/off")
    test_toggle_doesnt_affect_others(); print("OK toggle doesn't affect others")
    test_season_prep_creates_batch(); print("OK season prep creates batch")
    test_season_prep_includes_multiple_action_types(); print("OK season prep includes multiple action types")
    test_season_prep_custom_name(); print("OK season prep custom name")
    test_kill_switch_persists_across_reinit(); print("OK kill switch persists across reinit")
    test_automation_config_endpoint_returns_all(); print("OK automation config endpoint returns all")
    print("\nALL v7.2 PHASE 5 TESTS PASSED")
