"""v7.2 Phase 1 — Approval queue edit endpoint + extensions split tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_")
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


def test_edit_pending_action_payload():
    """PUT /api/pending-actions/{id} edits payload before approving."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import create_pending_action, PendingActionCreate, edit_pending_action, PendingActionEdit, approve_pending_action
        # Create
        pa = PendingActionCreate(action_type="draft_expense", payload={"amount": 500, "category": "Electricity"},
                                  reason="Original amount", impact_summary="Rs 500")
        r = create_pending_action(pa)
        pa_id = r["id"]
        # Edit payload
        edit = PendingActionEdit(payload={"amount": 750, "category": "Electricity"}, reason="Updated amount")
        r2 = edit_pending_action(pa_id, edit)
        assert r2["ok"] is True
        # Approve — should use the UPDATED payload
        r3 = approve_pending_action(pa_id, {"approved_by": "manager"})
        assert r3["ok"] is True
        assert r3["result"]["expense_id"] > 0
        # Verify the expense was created with amount 750 (not 500)
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT amount FROM expenses WHERE id=?", (r3["result"]["expense_id"],)).fetchone()
        assert row["amount"] == 750.0, f"Expected 750 (edited), got {row['amount']}"
    finally:
        cleanup(test_dir)


def test_edit_rejected_action_fails():
    """Cannot edit an already-rejected action."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import create_pending_action, PendingActionCreate, reject_pending_action, edit_pending_action, PendingActionEdit
        from fastapi import HTTPException
        pa = PendingActionCreate(action_type="draft_expense", payload={"amount": 100})
        r = create_pending_action(pa)
        reject_pending_action(r["id"])
        try:
            edit_pending_action(r["id"], PendingActionEdit(reason="try edit after reject"))
            assert False, "Should raise 404"
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        cleanup(test_dir)


def test_extensions_split_imports():
    """Verify all functions are importable from the extensions shim."""
    test_dir = setup_test_db()
    try:
        from app import extensions
        # POS
        assert callable(extensions.list_bundles)
        assert callable(extensions.get_break_even)
        assert callable(extensions.get_margin_alerts)
        # Intel
        assert callable(extensions.get_internal_trend_signals)
        assert callable(extensions.prepare_for_season)
        assert callable(extensions.list_closed_days)
        # Comm
        assert callable(extensions.get_urdhaar_reminders)
        assert callable(extensions.parse_whatsapp_order)
        assert callable(extensions.get_raast_reconciliation)
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_edit_pending_action_payload(); print("✓ test_edit_pending_action_payload")
    test_edit_rejected_action_fails(); print("✓ test_edit_rejected_action_fails")
    test_extensions_split_imports(); print("✓ test_extensions_split_imports")
    print("\n✅ ALL v7.2 PHASE 1 TESTS PASSED")
