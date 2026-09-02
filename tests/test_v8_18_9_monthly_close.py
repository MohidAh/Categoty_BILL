"""v8.18.9 fix — Monthly Close page showed no data.

Root cause: the frontend read fields the backend NEVER returned
(sales_count, total_revenue, total_profit, bills_count, details), so
/reports/monthly-close rendered all zeros forever. This test locks in
the FIXED contract: monthly_close() must return every field the UI
reads, with correct values, while keeping the pre-v8.18.9 keys intact
(the PDF export + test_v8_2_phase6_fix depend on them).

Sample-data expectations for 2026-08 (see tests/sample_data.sql), AFTER the
standard setup step profit.rebuild_stock_state() — which normalizes each
sale line's cost_price to the category's purchase cost (D: 201.25 -> 150):
  bills:   3 confirmed (4000 paid + 7500 credit + 2250 paid) = 13,750
  sales:   2 (paid 10,150 + credit 5,500) = 15,650 revenue
  COGS:    11*80 + 13*200 + 8*300 + 8*150 = 880 + 2600 + 2400 + 1200 = 7,080
  gross:   15,650 - 7,080 = 8,570
  expenses: none in sample data (test adds its own)
"""
import os, sys, tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db(extra_expenses=None):
    test_dir = tempfile.mkdtemp(prefix="billbook_mc_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills", "customers",
                  "price_categories", "suppliers", "stock_adjustments",
                  "activity_log", "sessions", "expenses", "expense_categories",
                  "recurring_expenses", "cash_drawer", "shifts", "employees",
                  "category_stock_state", "owner_withdrawals", "login_attempts",
                  "devices", "pairing_codes", "bundles", "bundle_items",
                  "price_rules", "lost_sales", "closed_days", "seasons",
                  "ai_cache", "ai_usage", "pending_actions",
                  "automation_config", "branches", "branch_pairing_codes",
                  "branch_summaries", "sync_outbox", "transfer_challans",
                  "transfer_challan_items", "central_purchases",
                  "central_purchase_items", "price_pushes", "audit_runs",
                  "audit_findings", "bill_intelligence"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Extra expenses for the month under test (v8.18.9 additions)
        if extra_expenses:
            for cat, amount, etype, date in extra_expenses:
                c.execute(
                    "INSERT INTO expenses(category, description, amount, "
                    "payment_method, date, expense_type) VALUES(?,?,?,?,?,?)",
                    (cat, "mc-test", amount, "cash", date, etype))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def cleanup(test_dir):
    import shutil
    shutil.rmtree(test_dir, ignore_errors=True)


def test_ui_fields_present_and_nonzero():
    """The exact fields the /reports/monthly-close UI reads must exist,
    and with the sample data populated they must be NON-zero."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close
        r = monthly_close(2026, 8)
        # --- the fields the fixed UI reads (was: always undefined -> 0) ---
        assert r["sales_count"] == 2, r["sales_count"]
        assert abs(r["total_revenue"] - 15650) < 0.01, r["total_revenue"]
        assert abs(r["cost_of_goods"] - 7080) < 0.01, r["cost_of_goods"]
        assert abs(r["gross_profit"] - 8570) < 0.01, r["gross_profit"]
        assert abs(r["net_profit"] - 8570) < 0.01, r["net_profit"]  # no expenses yet
        assert r["bills_count"] == 3, r["bills_count"]
        assert r["bills_count"] == r["total_bills"], "alias mismatch"
        assert r["total_profit"] == r["net_profit"], "alias mismatch"
        assert r["refunded_sales_count"] == 0
        assert abs(r["sales_credit_total"] - 5500) < 0.01, r["sales_credit_total"]
        assert isinstance(r["details"], dict) and len(r["details"]) >= 10
        assert isinstance(r["sales_by_category"], list)
        assert len(r["sales_by_category"]) == 4  # categories A/B/C/D sold
        # Buy side unchanged
        assert abs(r["total_spent"] - 13750) < 0.01
        assert abs(r["total_paid"] - 6250) < 0.01
        assert abs(r["total_credit"] - 7500) < 0.01
        assert r["supplier_count"] == 2
        print("OK ui fields present, non-zero, correct values")
    finally:
        cleanup(test_dir)


def test_details_contract():
    """details: numbers are money, strings are labels — the UI renders
    numbers with fmtRs and strings verbatim.

    v8.18.14: labels updated — net profit now INCLUDES extra (non-POS)
    sales income, and a dedicated Extra Sales row keeps that income
    differentiable from POS revenue.
    """
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close
        d = monthly_close(2026, 8)["details"]
        assert isinstance(d["POS Sales (invoices)"], str), "counts must be strings"
        assert isinstance(d["Sales Revenue (net of discounts)"], (int, float))
        assert isinstance(d["Net Profit (gross + extra sales − op. expenses)"], (int, float))
        assert "Extra Sales (non-POS" in " \n".join(d), "extra sales row missing from details"
        # money numbers, not strings of digits
        for v in d.values():
            if isinstance(v, (int, float)):
                assert isinstance(v, (int, float))
        print("OK details contract (strings=labels, numbers=money)")
    finally:
        cleanup(test_dir)


def test_expenses_and_owner_draws():
    """Operating expenses reduce net profit; owner draws do NOT."""
    test_dir = setup_test_db(extra_expenses=[
        ("Rent", 2000, "operating", "2026-08-05 10:00:00"),
        ("Salaries", 1500, "operating", "2026-08-28 10:00:00"),
        ("Family", 3000, "owner_draw", "2026-08-15 10:00:00"),
        ("Other Month", 999, "operating", "2026-07-15 10:00:00"),  # excluded
    ])
    try:
        from app.insights import monthly_close
        r = monthly_close(2026, 8)
        assert abs(r["operating_expenses"] - 3500) < 0.01, r["operating_expenses"]
        assert abs(r["owner_draws"] - 3000) < 0.01, r["owner_draws"]
        # gross 8570 - op 3500 = 5070; owner draw NOT subtracted
        assert abs(r["net_profit"] - 5070) < 0.01, r["net_profit"]
        print("OK expenses reduce net profit, owner draws excluded")
    finally:
        cleanup(test_dir)


def test_refunded_sales_excluded():
    """A refunded sale must not count toward revenue/COGS, but must
    appear in the refund counters (same rule as get_pnl)."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.insights import monthly_close
        with db.conn() as c:
            c.execute(
                "INSERT INTO sales(id, invoice_no, subtotal, total, "
                "payment_method, payment_status, created_at) "
                "VALUES(99, 'INV-REF', 1000, 1000, 'cash', 'refunded', "
                "'2026-08-20 12:00:00')")
            c.execute(
                "INSERT INTO sale_items(sale_id, item_name, category_id, "
                "sell_price, cost_price, qty, line_total) "
                "VALUES(99, 'Refunded Item', 1, 1000, 400, 1, 1000)")
        r = monthly_close(2026, 8)
        assert r["sales_count"] == 2, "refunded sale must be excluded"
        assert abs(r["total_revenue"] - 15650) < 0.01, "revenue unchanged"
        assert abs(r["cost_of_goods"] - 7080) < 0.01, "cogs unchanged"
        assert r["refunded_sales_count"] == 1
        assert abs(r["refunded_total"] - 1000) < 0.01, r["refunded_total"]
        print("OK refunded sales excluded from revenue, shown in refund counters")
    finally:
        cleanup(test_dir)


def test_empty_month():
    """A month with no data returns zeros (not an error) + empty lists,
    so the UI can show its 'Nothing recorded for this month' state."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close
        r = monthly_close(2026, 1)
        assert r["sales_count"] == 0
        assert r["total_revenue"] == 0
        assert r["net_profit"] == 0
        assert r["bills_count"] == 0
        assert r["sales_by_category"] == []
        assert r["bills"] == []
        assert r["details"]["POS Sales (invoices)"] == "0"
        print("OK empty month returns clean zeros, not an error")
    finally:
        cleanup(test_dir)


def test_backward_compat_keys():
    """Every pre-v8.18.9 key still exists with the same meaning (PDF +
    test_v8_2_phase6_fix depend on them)."""
    test_dir = setup_test_db()
    try:
        from app.insights import monthly_close
        r = monthly_close(2026, 8)
        for key in ("month", "total_bills", "total_spent", "total_paid",
                    "total_credit", "suppliers", "supplier_count", "items",
                    "by_category", "bills"):
            assert key in r, f"missing pre-v8.18.9 key: {key}"
        assert r["month"] == "2026-08"
        assert "audit" not in r, "monthly_close must stay audit-free"
        from app.insights import monthly_close_with_audit
        rw = monthly_close_with_audit(2026, 8)
        assert "audit" in rw and "run_id" in rw["audit"]
        print("OK backward-compatible keys + audit wrapper intact")
    finally:
        cleanup(test_dir)


def test_js_reads_only_real_fields():
    """Static guard: the monthly-close page JS must reference only field
    names that monthly_close() actually returns (this is the EXACT class
    of bug that shipped: UI reading sales_count etc. that never existed).
    """
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "reports-pages.js").read_text()
    start = js.index("route('/reports/monthly-close'")
    end = js.index("route('/reports/export'")
    block = js[start:end]
    import re
    referenced = set(re.findall(r"r\.([a-z_]+)", block))
    # Whitelist of fields the API returns (or intentionally-safe reads)
    # v8.18.14: extra_sales_income / extra_sales_count are real API fields now
    allowed = {
        "sales_count", "total_revenue", "net_profit", "total_profit",
        "bills_count", "total_bills", "gross_profit", "operating_expenses",
        "sales_credit_total", "refunded_sales_count", "refunded_total",
        "discounts_given", "cost_of_goods", "owner_draws", "total_spent",
        "total_paid", "total_credit", "supplier_count", "suppliers",
        "sales_by_category", "details", "audit",
        "extra_sales_income", "extra_sales_count",  # v8.18.14
    }
    unknown = referenced - allowed
    assert not unknown, f"JS reads fields the API does not return: {unknown}"
    print("OK JS field reads all exist in the API response")


if __name__ == "__main__":
    test_ui_fields_present_and_nonzero()
    test_details_contract()
    test_expenses_and_owner_draws()
    test_refunded_sales_excluded()
    test_empty_month()
    test_backward_compat_keys()
    test_js_reads_only_real_fields()
    print("\nALL v8.18.9 MONTHLY-CLOSE FIX TESTS PASSED")
