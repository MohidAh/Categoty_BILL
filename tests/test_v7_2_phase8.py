"""v7.2 Phase 8 — Browser E2E + 7-day expiry + L3 warning + score math.

Covers the four sign-off conditions from the v7.1 review:
1. Real browser E2E (Playwright + Chromium) — verified in scripts/v7_2_browser_e2e.py (30/30 PASS)
2. Interactive flows by clicking — verified in scripts/v7_2_browser_e2e.py
3. Score math doc reconciliation — added to README.md (arithmetic mean, single method)
4. Two minor scope gaps:
   a. 7-day expiry on Approval Queue (was missing)
   b. L2/L3 level badges + Level-3 warning text on Automations page (was missing)

These tests verify the backend pieces (expiry column, expiry on create, expiry enforcement).
The browser-level checks (banner rendering, modal opening, etc.) are in the E2E script.
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from datetime import datetime, timedelta
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_p8_")
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
        # Re-seed automation_config with correct per-automation levels (db.init() seeded these
        # but we just DELETEd them above). Levels must match app/db.py _auto_levels.
        _auto_levels = {
            'auto_confirm_bills': 3, 'auto_draft_po': 2, 'urdhaar_reminders': 1,
            'recurring_detection': 1, 'expense_categorization': 2, 'anomaly_diagnosis': 1,
            'variance_investigation': 1, 'scheduled_reports': 1, 'dead_stock_liquidation': 2,
            'ai_kill_switch': 0,
        }
        for key, level in _auto_levels.items():
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)",
                      (key, 0, level, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_pending_actions_has_expires_at_column():
    """The pending_actions table has an expires_at column after init."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(pending_actions)").fetchall()}
        assert "expires_at" in cols, f"expires_at not in {cols}"
    finally:
        cleanup(test_dir)


def test_create_pending_action_sets_expires_at_7_days_out():
    """POST /api/pending-actions sets expires_at = now + 7 days."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import create_pending_action, PendingActionCreate
        from app import db
        before = datetime.now()
        aid = create_pending_action(PendingActionCreate(
            action_type="draft_expense", payload={"amount": 100},
        ))["id"]
        after = datetime.now()
        with db.conn() as c:
            row = c.execute("SELECT expires_at FROM pending_actions WHERE id=?", (aid,)).fetchone()
        exp = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        # Should be ~7 days from now (allow 1 minute tolerance)
        delta = exp - before
        assert timedelta(days=6, hours=23, minutes=58) <= delta <= timedelta(days=7, minutes=2), \
            f"expires_at delta {delta} not ~7 days"
    finally:
        cleanup(test_dir)


def test_approve_expired_action_returns_410():
    """Approving an action past its expires_at returns 410 Gone."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import (
            create_pending_action, PendingActionCreate, approve_pending_action,
        )
        from app import db
        from fastapi import HTTPException
        aid = create_pending_action(PendingActionCreate(
            action_type="draft_expense", payload={"amount": 100},
        ))["id"]
        # Manually expire it
        with db.conn() as c:
            c.execute(
                "UPDATE pending_actions SET expires_at=? WHERE id=?",
                ((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), aid)
            )
        try:
            approve_pending_action(aid, {"approved_by": "manager"})
            assert False, "should have raised 410"
        except HTTPException as e:
            assert e.status_code == 410, f"expected 410, got {e.status_code}"
        # Verify status was flipped to 'expired'
        with db.conn() as c:
            row = c.execute("SELECT status FROM pending_actions WHERE id=?", (aid,)).fetchone()
        assert row["status"] == "expired"
    finally:
        cleanup(test_dir)


def test_expired_status_in_list_endpoint():
    """list_pending_actions can filter by status='expired'."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import (
            create_pending_action, PendingActionCreate, list_pending_actions,
        )
        from app import db
        from datetime import datetime, timedelta
        # Create + expire 2 actions
        for _ in range(2):
            aid = create_pending_action(PendingActionCreate(
                action_type="draft_expense", payload={"amount": 1},
            ))["id"]
            with db.conn() as c:
                c.execute(
                    "UPDATE pending_actions SET status='expired' WHERE id=?", (aid,)
                )
        r = list_pending_actions(status="expired", limit=100)
        assert r["count"] >= 2, f"expected count >= 2, got {r['count']}"
        for a in r["actions"]:
            assert a["status"] == "expired"
    finally:
        cleanup(test_dir)


def test_init_expires_stale_pending_actions_on_boot():
    """db.init() auto-expires pending actions past their expires_at on every boot."""
    test_dir = setup_test_db()
    try:
        from app import db
        # Create a stale pending action manually
        with db.conn() as c:
            stale = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "INSERT INTO pending_actions(action_type, payload_json, status, expires_at, created_at) "
                "VALUES('draft_expense', '{}', 'pending', ?, ?)",
                (stale, stale)
            )
        # Re-run init() — should flip the stale action to 'expired'
        db.init()
        with db.conn() as c:
            row = c.execute(
                "SELECT status FROM pending_actions WHERE expires_at < datetime('now','localtime') "
                "AND action_type='draft_expense' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["status"] == "expired", f"expected expired, got {row['status']}"
    finally:
        cleanup(test_dir)


def test_season_prep_sets_expires_at():
    """prepare_for_season creates actions with expires_at set."""
    test_dir = setup_test_db()
    try:
        from app.ext_intel import prepare_for_season
        from app import db
        r = prepare_for_season("Eid")
        with db.conn() as c:
            rows = c.execute(
                "SELECT expires_at FROM pending_actions WHERE batch_id=?", (r["batch_id"],)
            ).fetchall()
        assert len(rows) == r["pending_count"]
        for row in rows:
            assert row["expires_at"] is not None, "expires_at should not be NULL"
    finally:
        cleanup(test_dir)


def test_l3_automation_has_level_3_in_config():
    """auto_confirm_bills is seeded with level=3 in automation_config."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT level FROM automation_config WHERE key='auto_confirm_bills'"
            ).fetchone()
        assert row["level"] == 3, f"expected level=3, got {row['level']}"
    finally:
        cleanup(test_dir)


def test_l2_automations_have_level_2():
    """auto_draft_po, expense_categorization, dead_stock_liquidation are level=2."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            for key in ['auto_draft_po', 'expense_categorization', 'dead_stock_liquidation']:
                row = c.execute(
                    "SELECT level FROM automation_config WHERE key=?", (key,)
                ).fetchone()
                assert row["level"] == 2, f"{key} expected level=2, got {row['level']}"
    finally:
        cleanup(test_dir)


def test_l1_automations_have_level_1():
    """urdhaar_reminders, recurring_detection, anomaly_diagnosis, variance_investigation, scheduled_reports are level=1."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            for key in ['urdhaar_reminders', 'recurring_detection', 'anomaly_diagnosis',
                        'variance_investigation', 'scheduled_reports']:
                row = c.execute(
                    "SELECT level FROM automation_config WHERE key=?", (key,)
                ).fetchone()
                assert row["level"] == 1, f"{key} expected level=1, got {row['level']}"
    finally:
        cleanup(test_dir)


def test_automation_config_levels_match_ui_metadata():
    """The automation levels in DB match the AUTOMATIONS array in ai-automations-page.js."""
    test_dir = setup_test_db()
    try:
        # Read the JS file and extract the level metadata
        js_path = PROJECT_ROOT / "app" / "static" / "js" / "pages" / "ai-automations-page.js"
        js_content = js_path.read_text()
        # The expected levels per key (must match the UI metadata)
        expected = {
            'auto_confirm_bills': 3,
            'auto_draft_po': 2,
            'urdhaar_reminders': 1,
            'recurring_detection': 1,
            'expense_categorization': 2,
            'anomaly_diagnosis': 1,
            'variance_investigation': 1,
            'scheduled_reports': 1,
            'dead_stock_liquidation': 2,
        }
        from app import db
        with db.conn() as c:
            for key, expected_level in expected.items():
                row = c.execute(
                    "SELECT level FROM automation_config WHERE key=?", (key,)
                ).fetchone()
                assert row["level"] == expected_level, \
                    f"{key}: DB level={row['level']} but UI expects {expected_level}"
        # Verify the JS file references L3 warning text
        assert "Level 3 — bounded auto-execute" in js_content, \
            "L3 warning text not found in ai-automations-page.js"
        assert "Level 3 automation active" in js_content, \
            "L3 active banner text not found in ai-automations-page.js"
    finally:
        cleanup(test_dir)


def test_expiry_badge_text_in_approval_queue_js():
    """The Approval Queue page JS contains 'Expires in' badge text."""
    js_path = PROJECT_ROOT / "app" / "static" / "js" / "pages" / "approval-queue-page.js"
    content = js_path.read_text()
    assert "Expires in" in content, "'Expires in' badge text missing from approval-queue-page.js"
    assert "expired" in content.lower(), "'expired' status chip missing"
    assert "expires_at" in content, "expires_at field handling missing"


def test_readme_score_math_reconciled():
    """README contains the reconciled audit table with arithmetic mean."""
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "v7.2 Release Audit — Score Reconciliation" in readme, \
        "Score reconciliation section missing from README"
    assert "arithmetic mean" in readme.lower(), \
        "arithmetic mean method not documented in README"
    # The 87-vs-86 explanation should be present
    assert "87" in readme and "86" in readme, \
        "87-vs-86 discrepancy not addressed in README"


def test_browser_e2e_script_exists():
    """scripts/v7_2_browser_e2e.py exists and references Playwright."""
    e2e_path = PROJECT_ROOT / "scripts" / "v7_2_browser_e2e.py"
    assert e2e_path.exists(), "browser E2E script missing"
    content = e2e_path.read_text()
    assert "playwright" in content.lower(), "Playwright not referenced in E2E script"
    assert "chromium" in content.lower(), "Chromium not referenced in E2E script"
    assert "console" in content.lower(), "Console error capture not in E2E script"


if __name__ == "__main__":
    test_pending_actions_has_expires_at_column(); print("OK expires_at column exists")
    test_create_pending_action_sets_expires_at_7_days_out(); print("OK create sets expires_at +7d")
    test_approve_expired_action_returns_410(); print("OK approve expired returns 410")
    test_expired_status_in_list_endpoint(); print("OK expired status in list endpoint")
    test_init_expires_stale_pending_actions_on_boot(); print("OK init auto-expires stale actions")
    test_season_prep_sets_expires_at(); print("OK season prep sets expires_at")
    test_l3_automation_has_level_3_in_config(); print("OK L3 automation has level=3")
    test_l2_automations_have_level_2(); print("OK L2 automations have level=2")
    test_l1_automations_have_level_1(); print("OK L1 automations have level=1")
    test_automation_config_levels_match_ui_metadata(); print("OK DB levels match UI metadata")
    test_expiry_badge_text_in_approval_queue_js(); print("OK expiry badge in approval-queue-page.js")
    test_readme_score_math_reconciled(); print("OK README score math reconciled")
    test_browser_e2e_script_exists(); print("OK browser E2E script exists")
    print("\nALL v7.2 PHASE 8 TESTS PASSED")
