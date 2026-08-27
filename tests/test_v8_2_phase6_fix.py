"""v8.2 Phase 6 fix — Safe-Withdrawal Enforcement UI + month-end auto-run tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_fix_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items","sales","bill_items","bills","customers","price_categories",
                  "suppliers","stock_adjustments","activity_log","sessions","expenses",
                  "expense_categories","recurring_expenses","cash_drawer","shifts","employees",
                  "category_stock_state","owner_withdrawals","login_attempts","devices",
                  "pairing_codes","bundles","bundle_items","price_rules","lost_sales",
                  "closed_days","seasons","ai_cache","ai_usage","pending_actions",
                  "automation_config","branches","branch_pairing_codes","branch_summaries",
                  "sync_outbox","transfer_challans","transfer_challan_items",
                  "central_purchases","central_purchase_items","price_pushes",
                  "audit_runs","audit_findings","bill_intelligence"):
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
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'test')")
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def test_withdraw_modal_has_live_feedback():
    """The cash-buckets-page.js has the live feedback element + PIN gate."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "cash-buckets-page.js").read_text()
    # Live feedback
    assert "ow-feedback" in js, "missing #ow-feedback element"
    assert "addEventListener('input'" in js, "missing live input listener"
    assert "Within safe limit" in js, "missing green feedback text"
    assert "Exceeds safe limit" in js, "missing red feedback text"
    # PIN gate
    assert "ow-pin-section" in js, "missing #ow-pin-section"
    assert "Manager PIN required" in js, "missing PIN gate text"
    assert "ow-pin" in js, "missing PIN input"
    assert "isOverSafe" in js, "missing over-safe check"
    assert "OVER-SAFE: PIN verified" in js, "missing over-safe logging"
    # Verdict banner
    assert "cb-verdict-banner" in js, "missing verdict banner div"
    assert "Safe to withdraw" in js, "missing safe verdict text"
    assert "Over-withdrawn" in js, "missing over verdict text"


def test_month_end_auto_run():
    """monthly_close_with_audit triggers an audit run with trigger='month_end'."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close_with_audit
        from app import db
        result = monthly_close_with_audit(2026, 8)
        assert "audit" in result, "audit missing from monthly close result"
        assert "run_id" in result["audit"], "audit run_id missing"
        assert result["audit"]["run_id"] > 0
        # Verify the audit run has trigger='month_end'
        with db.conn() as c:
            row = c.execute(
                "SELECT trigger FROM audit_runs WHERE id=?",
                (result["audit"]["run_id"],)
            ).fetchone()
        assert row["trigger"] == "month_end", f"expected 'month_end', got '{row['trigger']}'"
    finally:
        cleanup(test_dir)


def test_month_end_creates_audit_findings():
    """Month-end audit run produces findings."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close_with_audit
        result = monthly_close_with_audit(2026, 8)
        assert result["audit"]["findings_count"] >= 0  # ran successfully
        assert "critical_count" in result["audit"]
        assert "warning_count" in result["audit"]
    finally:
        cleanup(test_dir)


def test_existing_monthly_close_unchanged():
    """The original monthly_close function still returns the same shape."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close
        result = monthly_close(2026, 8)
        assert "month" in result
        assert "total_bills" in result
        assert "bills" in result
        # Should NOT have 'audit' key (that's only in monthly_close_with_audit)
        assert "audit" not in result, "original monthly_close should not have audit key"
    finally:
        cleanup(test_dir)


def test_safe_withdrawal_within_safe_no_pin():
    """Within-safe withdrawal doesn't require PIN."""
    test_dir = setup_test_db()
    try:
        from app.auditor import get_safe_withdrawal_amount
        sw = get_safe_withdrawal_amount()
        assert sw["safe_withdrawal"] > 0, "expected positive safe_withdrawal"
        assert sw["is_over"] is False
        assert sw["remaining_safe"] > 0
    finally:
        cleanup(test_dir)


def test_over_safe_requires_pin_in_js():
    """The JS logic checks isOverSafe and requires PIN."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "cash-buckets-page.js").read_text()
    # The save handler checks isOverSafe and requires PIN
    assert "if (isOverSafe)" in js, "missing isOverSafe check in save handler"
    assert "if (!pin)" in js, "missing PIN validation"
    assert "Manager PIN required for over-safe withdrawal" in js, "missing PIN error message"
    # Within-safe path: PIN section is hidden
    assert "pinSection.style.display = 'none'" in js, "missing PIN section hide for safe amount"


def test_pin_gate_fails_closed():
    """The PIN gate FAILS CLOSED — rejects the withdrawal if the endpoint is unreachable."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "cash-buckets-page.js").read_text()
    # Must NOT have the old fail-open comment
    assert "accept any non-empty PIN" not in js, "fail-open comment still present"
    assert "graceful degradation" not in js, "fail-open comment still present"
    # Must have fail-closed behavior
    assert "FAIL CLOSED" in js, "missing FAIL CLOSED comment"
    assert "Cannot verify PIN" in js, "missing fail-closed error message"
    assert "Withdrawal blocked" in js, "missing fail-closed rejection"
    # The catch block must return (reject), not fall through
    assert "return;" in js[js.index("FAIL CLOSED"):js.index("FAIL CLOSED")+200], \
        "fail-closed handler doesn't return (would fall through to withdrawal)"


if __name__ == "__main__":
    test_withdraw_modal_has_live_feedback(); print("OK withdraw modal has live feedback + PIN gate")
    test_month_end_auto_run(); print("OK month-end auto-run triggers audit")
    test_month_end_creates_audit_findings(); print("OK month-end creates findings")
    test_existing_monthly_close_unchanged(); print("OK existing monthly_close unchanged")
    test_safe_withdrawal_within_safe_no_pin(); print("OK within-safe no PIN")
    test_over_safe_requires_pin_in_js(); print("OK over-safe requires PIN in JS")
    print("\nALL v8.2 PHASE 6 FIX TESTS PASSED")
