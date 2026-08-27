"""v8.2 Phase 3 — Audit Report page tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_p3_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items","sales","bill_items","bills","customers","price_categories",
                  "suppliers","stock_adjustments","activity_log","sessions","expenses",
                  "expense_categories","recurring_expenses","cash_drawer","shifts",
                  "employees","category_stock_state","owner_withdrawals","login_attempts",
                  "devices","pairing_codes","bundles","bundle_items","price_rules",
                  "lost_sales","closed_days","seasons","ai_cache","ai_usage",
                  "pending_actions","automation_config","branches","branch_pairing_codes",
                  "branch_summaries","sync_outbox","transfer_challans",
                  "transfer_challan_items","central_purchases","central_purchase_items",
                  "price_pushes","audit_runs","audit_findings"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent",1,0,1),("Salaries",1,0,2),("Electricity",0,0,3),
                    ("Transport",0,0,4),("Internet",0,0,5),("Maintenance",0,0,6),
                    ("Marketing",0,0,7),("Other",0,0,8)]
        for name,is_fixed,budget,sort_order in defaults:
            c.execute("INSERT INTO expense_categories(name,is_fixed,budget_monthly,active,sort_order) VALUES(?,?,?,?,?)",
                      (name,is_fixed,budget,1,sort_order))
        _auto_levels = {'auto_confirm_bills':3,'auto_draft_po':2,'urdhaar_reminders':1,
                    'recurring_detection':1,'expense_categorization':2,'anomaly_diagnosis':1,
                    'variance_investigation':1,'scheduled_reports':1,'dead_stock_liquidation':2,
                    'ai_kill_switch':0}
        for key,level in _auto_levels.items():
            c.execute("INSERT OR REPLACE INTO automation_config(key,enabled,level,params_json) VALUES(?,?,?,?)",
                      (key,0,level,'{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def test_audit_report_page_exists():
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "audit-report-page.js").read_text()
    assert "AI Auditor" in js
    assert "/api/audit/latest" in js
    assert "/api/audit/run" in js
    assert "pos-page-header" in js
    assert "Critical" in js and "Warnings" in js and "Info" in js

def test_audit_nav_registered():
    shell = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "shell.js").read_text()
    assert "/reports/audit" in shell
    assert "AI Auditor" in shell

def test_audit_page_imported():
    app = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "audit-report-page" in app

def test_audit_run_creates_pending_action():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import run_audit
        from app.routers.audit import trigger_run
        # Seed an over-withdrawal to trigger an actionable finding
        from app.profit_cash import get_cash_buckets
        buckets = get_cash_buckets()
        over = buckets["available_for_withdrawal"] + 5000
        with db.conn() as c:
            c.execute("INSERT INTO owner_withdrawals(amount,payment_method,notes) VALUES(?,?,?)",
                      (over, "cash", "test"))
            c.execute("INSERT INTO cash_drawer(type,amount,description,reference_type) VALUES('owner_withdrawal',?,'test','owner_withdrawal')",
                      (-over,))
        # Trigger audit via router
        r = trigger_run(trigger="manual")
        # Check if a pending_action was created for the over_withdrawal
        with db.conn() as c:
            pa = c.execute("SELECT * FROM pending_actions WHERE source='ai_auditor' AND action_type='audit_finding'").fetchall()
        assert len(pa) >= 1, "expected pending_action for actionable finding"
    finally:
        cleanup(test_dir)

def test_safe_withdrawal_endpoint():
    test_dir = setup_test_db()
    try:
        from app.routers.audit import safe_withdrawal
        r = safe_withdrawal()
        assert "safe_withdrawal" in r
        assert "is_over" in r
        assert "over_amount" in r
    finally:
        cleanup(test_dir)

def test_acknowledge_endpoint():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit
        from app.routers.audit import ack_finding, AckIn
        from app import db
        r = run_audit()
        if r["findings_count"] > 0:
            with db.conn() as c:
                fid = c.execute("SELECT id FROM audit_findings WHERE run_id=? LIMIT 1", (r["run_id"],)).fetchone()
            if fid:
                result = ack_finding(fid["id"], AckIn(reason="test"))
                assert result["ok"] is True
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_audit_report_page_exists(); print("OK audit report page exists")
    test_audit_nav_registered(); print("OK nav registered")
    test_audit_page_imported(); print("OK page imported")
    test_audit_run_creates_pending_action(); print("OK creates pending action")
    test_safe_withdrawal_endpoint(); print("OK safe withdrawal endpoint")
    test_acknowledge_endpoint(); print("OK acknowledge endpoint")
    print("\nALL v8.2 PHASE 3 TESTS PASSED")
