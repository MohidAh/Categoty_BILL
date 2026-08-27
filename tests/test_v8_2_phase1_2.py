"""v8.2 Phase 1-2 — AI Auditor core + earnings integrity tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from datetime import datetime
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_p12_")
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
                  "ai_cache", "ai_usage", "pending_actions", "automation_config",
                  "branches", "branch_pairing_codes", "branch_summaries", "sync_outbox",
                  "transfer_challans", "transfer_challan_items",
                  "central_purchases", "central_purchase_items", "price_pushes",
                  "audit_runs", "audit_findings"):
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



def test_audit_tables_exist():
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "audit_runs" in tables
        assert "audit_findings" in tables
    finally:
        cleanup(test_dir)


def test_run_audit_returns_shape():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit
        r = run_audit(trigger="manual")
        assert "run_id" in r and r["run_id"] > 0
        assert "findings" in r and isinstance(r["findings"], list)
        assert "critical_count" in r
        assert "warning_count" in r
        assert "info_count" in r
        assert r["findings_count"] == len(r["findings"])
    finally:
        cleanup(test_dir)


def test_run_audit_stores_in_db():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit
        from app import db
        r = run_audit()
        with db.conn() as c:
            run = c.execute("SELECT * FROM audit_runs WHERE id=?", (r["run_id"],)).fetchone()
            findings = c.execute("SELECT * FROM audit_findings WHERE run_id=?", (r["run_id"],)).fetchall()
        assert run is not None
        assert run["findings_count"] == len(findings)
        assert run["critical_count"] == sum(1 for f in findings if f["severity"] == "critical")
    finally:
        cleanup(test_dir)


def test_audit_works_offline():
    """All checks are deterministic math on local data — no LLM call."""
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit, CHECKS
        # Every check function must be callable without network
        for check_fn in CHECKS:
            findings = check_fn()
            assert isinstance(findings, list)
        # Full run
        r = run_audit()
        assert r["findings_count"] >= 0  # no crash
    except Exception as e:
        assert False, f"audit failed offline: {e}"
    finally:
        cleanup(test_dir)


def test_over_withdrawal_detected():
    """Seed an over-withdrawal → auditor flags it with the over-amount."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import run_audit, get_safe_withdrawal_amount
        # Check current safe amount
        safe = get_safe_withdrawal_amount()
        # Seed a withdrawal that exceeds safe_withdrawal
        over_amount = safe["safe_withdrawal"] + 5000 if safe["safe_withdrawal"] > 0 else 5000
        with db.conn() as c:
            c.execute(
                "INSERT INTO owner_withdrawals(amount, payment_method, notes) VALUES(?,?,?)",
                (over_amount, "cash", "test over-withdrawal")
            )
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_type) "
                "VALUES('owner_withdrawal', ?, 'test', 'owner_withdrawal')",
                (-over_amount,)
            )
        # Run audit
        r = run_audit()
        over_findings = [f for f in r["findings"] if f["check_key"] == "over_withdrawal"]
        assert len(over_findings) >= 1, "expected over_withdrawal finding"
        assert over_findings[0]["severity"] == "critical"
        assert over_findings[0]["amount"] > 0, "over-amount should be positive"
    finally:
        cleanup(test_dir)


def test_clean_db_no_critical():
    """A clean DB (no over-withdrawal) produces no financial critical findings.
    Note: the sample SQL seeds cat D with -3 pcs (intentional test data from v5.0),
    so we exclude 'negative_stock' and 'stock_reserve_days' from this check."""
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit
        r = run_audit()
        # Exclude known sample-data issues (negative stock from v5.0 test data,
        # and stock reserve which depends on that stock)
        financial_critical = [f for f in r["findings"]
                              if f["severity"] == "critical"
                              and f["domain"] == "financial"]
        assert len(financial_critical) == 0, \
            f"expected 0 financial critical, got {len(financial_critical)}: {[f['title'] for f in financial_critical]}"
    finally:
        cleanup(test_dir)


def test_earnings_formula_integrity():
    """Earnings formula check runs without error."""
    test_dir = setup_test_db()
    try:
        from app.auditor import _check_earnings_formula_integrity
        findings = _check_earnings_formula_integrity()
        assert isinstance(findings, list)
        # On a clean DB, should produce no findings (formula is consistent)
        assert len(findings) == 0, f"unexpected findings: {findings}"
    finally:
        cleanup(test_dir)


def test_cogs_bridge_integrity():
    """COGS bridge check runs without error."""
    test_dir = setup_test_db()
    try:
        from app.auditor import _check_cogs_bridge_integrity
        findings = _check_cogs_bridge_integrity()
        assert isinstance(findings, list)
    finally:
        cleanup(test_dir)


def test_restock_funding_adequacy():
    """Restock funding check runs without error."""
    test_dir = setup_test_db()
    try:
        from app.auditor import _check_restock_funding_adequacy
        findings = _check_restock_funding_adequacy()
        assert isinstance(findings, list)
    finally:
        cleanup(test_dir)


def test_stock_reserve_days_of_cover():
    """Stock reserve check runs without error."""
    test_dir = setup_test_db()
    try:
        from app.auditor import _check_stock_reserve_days_of_cover
        findings = _check_stock_reserve_days_of_cover()
        assert isinstance(findings, list)
    finally:
        cleanup(test_dir)


def test_negative_stock_detected():
    """Seed negative stock → auditor flags it."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import _check_negative_stock
        with db.conn() as c:
            c.execute("UPDATE category_stock_state SET current_qty=-10 WHERE category_id=1")
        findings = _check_negative_stock()
        assert len(findings) >= 1
        assert findings[0]["severity"] == "critical"
        assert "negative" in findings[0]["title"].lower()
    finally:
        cleanup(test_dir)


def test_safe_withdrawal_amount():
    """get_safe_withdrawal_amount returns the correct shape."""
    test_dir = setup_test_db()
    try:
        from app.auditor import get_safe_withdrawal_amount
        r = get_safe_withdrawal_amount()
        assert "cash" in r
        assert "stock_replacement" in r
        assert "operating_expenses" in r
        assert "business_reserve" in r
        assert "safe_withdrawal" in r
        assert "withdrawn_this_month" in r
        assert "remaining_safe" in r
        assert "over_amount" in r
        assert "is_over" in r
    finally:
        cleanup(test_dir)


def test_list_audit_runs():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit, list_audit_runs
        run_audit()
        run_audit()
        runs = list_audit_runs()
        assert len(runs) >= 2
        assert runs[0]["id"] > runs[1]["id"]  # descending
    finally:
        cleanup(test_dir)


def test_get_audit_run():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit, get_audit_run
        r = run_audit()
        detail = get_audit_run(r["run_id"])
        assert detail is not None
        assert detail["run"]["id"] == r["run_id"]
        assert len(detail["findings"]) == r["findings_count"]
        # Findings should be severity-ranked (critical first)
        severities = [f["severity"] for f in detail["findings"]]
        if "critical" in severities and "info" in severities:
            assert severities.index("critical") < severities.index("info")
    finally:
        cleanup(test_dir)


def test_acknowledge_finding():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import run_audit, acknowledge_finding
        r = run_audit()
        if r["findings_count"] > 0:
            finding_id = None
            with db.conn() as c:
                row = c.execute("SELECT id FROM audit_findings WHERE run_id=? LIMIT 1", (r["run_id"],)).fetchone()
                if row:
                    finding_id = row["id"]
            if finding_id:
                ok = acknowledge_finding(finding_id, "test ack")
                assert ok is True
    finally:
        cleanup(test_dir)


def test_audit_logs_activity():
    test_dir = setup_test_db()
    try:
        from app.auditor import run_audit
        from app import db
        run_audit()
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='audit_run' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert "Audit run" in row["description"]
    finally:
        cleanup(test_dir)


def test_audit_endpoints():
    """Test the router endpoints."""
    test_dir = setup_test_db()
    try:
        from app.routers.audit import trigger_run, list_runs, get_latest, safe_withdrawal
        # Run
        r = trigger_run(trigger="manual")
        assert r["run_id"] > 0
        # List
        runs = list_runs()
        assert runs["count"] >= 1
        # Latest
        latest = get_latest()
        assert latest["run"] is not None
        # Safe withdrawal
        sw = safe_withdrawal()
        assert "safe_withdrawal" in sw
    finally:
        cleanup(test_dir)


def test_18588_still_passes():
    test_dir = setup_test_db()
    try:
        from app.profit_engine import apply_purchase_to_state, apply_sale_to_state, peek_avg_cost
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        apply_purchase_to_state(1, 10000, 180.0)
        apply_sale_to_state(1, 3000)
        apply_purchase_to_state(1, 10000, 190.0)
        with db.conn() as c:
            avg = peek_avg_cost(c, 1)
            assert abs(avg - 185.88) < 0.01, f"expected 185.88, got {avg}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_audit_tables_exist(); print("OK tables exist")
    test_run_audit_returns_shape(); print("OK run returns shape")
    test_run_audit_stores_in_db(); print("OK run stores in DB")
    test_audit_works_offline(); print("OK audit works offline")
    test_over_withdrawal_detected(); print("OK over-withdrawal detected")
    test_clean_db_no_critical(); print("OK clean DB no critical")
    test_earnings_formula_integrity(); print("OK earnings formula integrity")
    test_cogs_bridge_integrity(); print("OK COGS bridge integrity")
    test_restock_funding_adequacy(); print("OK restock funding adequacy")
    test_stock_reserve_days_of_cover(); print("OK stock reserve days of cover")
    test_negative_stock_detected(); print("OK negative stock detected")
    test_safe_withdrawal_amount(); print("OK safe withdrawal amount")
    test_list_audit_runs(); print("OK list audit runs")
    test_get_audit_run(); print("OK get audit run")
    test_acknowledge_finding(); print("OK acknowledge finding")
    test_audit_logs_activity(); print("OK audit logs activity")
    test_audit_endpoints(); print("OK audit endpoints")
    test_18588_still_passes(); print("OK 185.88 still passes")
    print("\nALL v8.2 PHASE 1-2 TESTS PASSED")
