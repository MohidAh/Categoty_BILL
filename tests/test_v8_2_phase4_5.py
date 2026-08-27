"""v8.2 Phase 4-5 — Safe-Withdrawal Enforcement + Bill Intelligence tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_p45_")
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
                  "price_pushes","audit_runs","audit_findings","bill_intelligence"):
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


def test_safe_withdrawal_returns_correct_amount():
    test_dir = setup_test_db()
    try:
        from app.auditor import get_safe_withdrawal_amount
        sw = get_safe_withdrawal_amount()
        # safe_withdrawal = cash - stock_replacement - op_exp - reserve
        assert abs(sw["safe_withdrawal"] - (sw["cash"] - sw["stock_replacement"] -
                sw["operating_expenses"] - sw["business_reserve"])) < 0.01
    finally:
        cleanup(test_dir)

def test_safe_withdrawal_over_detected():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import get_safe_withdrawal_amount
        # Seed cash into cash_drawer so safe_withdrawal is positive
        with db.conn() as c:
            c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'test opening')")
        sw = get_safe_withdrawal_amount()
        assert sw["safe_withdrawal"] > 0, f"expected positive safe_withdrawal, got {sw['safe_withdrawal']}"
        # Withdraw more than safe
        over = sw["safe_withdrawal"] + 1000
        with db.conn() as c:
            c.execute("INSERT INTO owner_withdrawals(amount,payment_method,notes) VALUES(?,?,?)",
                      (over, "cash", "over-test"))
            c.execute("INSERT INTO cash_drawer(type,amount,description,reference_type) VALUES('owner_withdrawal',?,'test','owner_withdrawal')",
                      (-over,))
        sw2 = get_safe_withdrawal_amount()
        assert sw2["is_over"] is True, f"expected is_over=True, got {sw2}"
        assert sw2["over_amount"] > 0
    finally:
        cleanup(test_dir)

def test_safe_withdrawal_within_safe():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.auditor import get_safe_withdrawal_amount
        sw = get_safe_withdrawal_amount()
        if sw["safe_withdrawal"] > 100:
            # Withdraw within safe limit
            safe_amount = sw["safe_withdrawal"] * 0.5
            with db.conn() as c:
                c.execute("INSERT INTO owner_withdrawals(amount,payment_method,notes) VALUES(?,?,?)",
                          (safe_amount, "cash", "safe-test"))
                c.execute("INSERT INTO cash_drawer(type,amount,description,reference_type) VALUES('owner_withdrawal',?,'test','owner_withdrawal')",
                          (-safe_amount,))
            sw2 = get_safe_withdrawal_amount()
            assert sw2["is_over"] is False or sw2["over_amount"] == 0
    finally:
        cleanup(test_dir)

def test_existing_withdrawal_endpoints_unchanged():
    """Existing POST /api/owner-withdrawals + GET still work."""
    test_dir = setup_test_db()
    try:
        from app.profit_cash import add_owner_withdrawal, list_owner_withdrawals, get_owner_withdrawals_summary
        wid = add_owner_withdrawal(500, "cash", "test")
        assert wid > 0
        withdrawals = list_owner_withdrawals()
        assert len(withdrawals) >= 1
        summary = get_owner_withdrawals_summary()
        assert summary["month_total"] >= 500
    finally:
        cleanup(test_dir)


# ─── Phase 5: Bill Intelligence ─────────────────────────────────────────────

def test_bill_intelligence_table_exists():
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "bill_intelligence" in tables
    finally:
        cleanup(test_dir)

def test_compute_bill_intelligence_first_purchase():
    """First-ever purchase of a category → skip (verdict='first_purchase')."""
    test_dir = setup_test_db()
    try:
        from app.bill_intel import compute_bill_intelligence
        # Bill 1 in sample data is the first purchase — should be first_purchase
        results = compute_bill_intelligence(1)
        # At least one result
        assert len(results) > 0
        # First bill should have first_purchase verdict for at least some categories
        first_purchases = [r for r in results if r["verdict"] == "first_purchase"]
        assert len(first_purchases) >= 1, f"expected first_purchase, got {[r['verdict'] for r in results]}"
    finally:
        cleanup(test_dir)

def test_compute_bill_intelligence_sell_through():
    """After a sale, confirming a new bill computes sell-through."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.bill_intel import compute_bill_intelligence
        # Sample data has bills 1-4 confirmed + sales. Create a new bill for cat 1.
        with db.conn() as c:
            cur = c.execute(
                "INSERT INTO bills(supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status) "
                "VALUES(1, 'Test', '2026-08-14', 'TEST-BI-1', 1000, 1000, 'confirmed', 'paid')"
            )
            new_bill_id = cur.lastrowid
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, qty, unit, price) "
                "VALUES(?, 1, 50, 'piece', 20)",
                (new_bill_id,)
            )
        results = compute_bill_intelligence(new_bill_id)
        cat1_result = [r for r in results if r["category_id"] == 1]
        assert len(cat1_result) == 1
        # Should have a sell_through_pct (not None, not first_purchase)
        if cat1_result[0]["verdict"] != "first_purchase":
            assert cat1_result[0]["sell_through_pct"] is not None
            assert cat1_result[0]["sell_through_pct"] >= 0
    finally:
        cleanup(test_dir)

def test_verdict_tiers():
    """Verdict tiers: >=80% well_timed, 40-80% partial, <40% overstock_risk."""
    test_dir = setup_test_db()
    try:
        from app.bill_intel import compute_bill_intelligence
        from app import db
        # The sample data has sales after bills, so sell-through should be computed
        results = compute_bill_intelligence(1)
        for r in results:
            if r["verdict"] != "first_purchase":
                pct = r["sell_through_pct"]
                if pct >= 80:
                    assert r["verdict"] == "well_timed"
                elif pct >= 40:
                    assert r["verdict"] == "partial"
                else:
                    assert r["verdict"] == "overstock_risk"
    finally:
        cleanup(test_dir)

def test_get_overstock_categories():
    test_dir = setup_test_db()
    try:
        from app.bill_intel import compute_bill_intelligence, get_overstock_categories_for_bill
        results = compute_bill_intelligence(1)
        overstock = get_overstock_categories_for_bill(1)
        # Should only return unacknowledged overstock
        for o in overstock:
            assert o["verdict"] == "overstock_risk"
            assert o["acknowledged"] == 0
    finally:
        cleanup(test_dir)

def test_acknowledge_bill_intelligence():
    test_dir = setup_test_db()
    try:
        from app.bill_intel import compute_bill_intelligence, acknowledge_bill_intelligence, get_overstock_categories_for_bill
        compute_bill_intelligence(1)
        overstock = get_overstock_categories_for_bill(1)
        if overstock:
            cat_id = overstock[0]["category_id"]
            ok = acknowledge_bill_intelligence(1, cat_id, "seasonal")
            assert ok is True
            # Verify it's now acknowledged
            remaining = get_overstock_categories_for_bill(1)
            assert all(r["category_id"] != cat_id for r in remaining)
    finally:
        cleanup(test_dir)

def test_acknowledged_not_re_flagged():
    """An acknowledged overstock finding isn't re-flagged on a new bill."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.bill_intel import compute_bill_intelligence, acknowledge_bill_intelligence, get_overstock_categories_for_bill
        # Compute intelligence for bill 1
        compute_bill_intelligence(1)
        overstock = get_overstock_categories_for_bill(1)
        if overstock:
            cat_id = overstock[0]["category_id"]
            acknowledge_bill_intelligence(1, cat_id, "intentional")
            # Create a new bill for the same category
            with db.conn() as c:
                cur = c.execute(
                    "INSERT INTO bills(supplier_id, supplier_name, bill_date, bill_no, "
                    "written_total, computed_total, status, payment_status) "
                    "VALUES(1, 'Test', '2026-08-14', 'TEST-BI-2', 500, 500, 'confirmed', 'paid')"
                )
                new_bill_id = cur.lastrowid
                c.execute(
                    "INSERT INTO bill_items(bill_id, category_id, qty, unit, price) "
                    "VALUES(?, ?, 10, 'piece', 20)",
                    (new_bill_id, cat_id)
                )
            compute_bill_intelligence(new_bill_id)
            new_overstock = get_overstock_categories_for_bill(new_bill_id)
            # The acknowledged category should NOT be in the new overstock
            assert all(r["category_id"] != cat_id for r in new_overstock), \
                "acknowledged category was re-flagged"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_safe_withdrawal_returns_correct_amount(); print("OK safe withdrawal correct amount")
    test_safe_withdrawal_over_detected(); print("OK over-withdrawal detected")
    test_safe_withdrawal_within_safe(); print("OK within-safe withdrawal")
    test_existing_withdrawal_endpoints_unchanged(); print("OK existing withdrawal endpoints unchanged")
    test_bill_intelligence_table_exists(); print("OK bill_intelligence table exists")
    test_compute_bill_intelligence_first_purchase(); print("OK first purchase skip")
    test_compute_bill_intelligence_sell_through(); print("OK sell-through computed")
    test_verdict_tiers(); print("OK verdict tiers")
    test_get_overstock_categories(); print("OK get overstock categories")
    test_acknowledge_bill_intelligence(); print("OK acknowledge bill intelligence")
    test_acknowledged_not_re_flagged(); print("OK acknowledged not re-flagged")
    print("\nALL v8.2 PHASE 4-5 TESTS PASSED")
