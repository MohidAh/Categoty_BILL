"""v4.0 Phase 6 — Owner Awareness & Staff tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p6_")
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
                  "supplier_advances", "supplier_rates",
                  "bank_accounts", "bank_transactions",
                  "commission_rules", "commissions"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Create a test cashier + manager
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(50, 'Test Cashier', 'cashier', '1111', 1)"
        )
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)", (name, is_fixed, budget, 1, sort_order),
            )
    # Phase 0 PR 3: create_sale() now reads stock from category_stock_state.
    # Rebuild after seeding sample data so the materialized state is current.
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_daily_summary_basic():
    """get_daily_summary returns the expected fields with numeric values."""
    test_dir = setup_test_db()
    try:
        from app import shop
        s = shop.get_daily_summary("2026-08-11")  # sample data date
        assert s["date"] == "2026-08-11"
        assert s["sales_total"] > 0, "Should have sales from sample data"
        assert s["sale_count"] >= 2, "Should have ≥2 sales in sample data"
        assert s["cash_sales"] > 0
        assert isinstance(s["top_categories"], list)
        assert isinstance(s["low_stock_count"], int)
        assert isinstance(s["shift_variances"], list)
    finally:
        cleanup(test_dir)


def test_daily_summary_text_renders():
    """build_daily_summary_text returns a non-empty string with key sections."""
    test_dir = setup_test_db()
    try:
        from app import shop
        text = shop.build_daily_summary_text("2026-08-11")
        assert "BillBook Daily Summary" in text
        assert "Total Sales" in text
        assert "Rs" in text
        # Should include top categories section if there are sales
        assert "Top Categories" in text or s_empty_top_cats(text)
    finally:
        cleanup(test_dir)


def s_empty_top_cats(text):
    return "Top Categories" in text


def test_whatsapp_link_format():
    """build_whatsapp_summary_link returns a valid wa.me URL with encoded text."""
    test_dir = setup_test_db()
    try:
        from app import shop
        link = shop.build_whatsapp_summary_link("03001234567", "2026-08-11")
        assert link.startswith("https://wa.me/"), f"Wrong prefix: {link[:30]}"
        # Pakistani number: 03001234567 → 923001234567
        assert "923001234567" in link, f"Phone not normalized: {link}"
        assert "?text=" in link
        assert "%0A" in link or "%E2%80%94" in link  # encoded newline or em-dash
    finally:
        cleanup(test_dir)


def test_whatsapp_link_empty_phone():
    """Empty phone returns empty string."""
    test_dir = setup_test_db()
    try:
        from app import shop
        assert shop.build_whatsapp_summary_link("", "2026-08-11") == ""
    finally:
        cleanup(test_dir)


def test_commission_percent_rule():
    """A 2% percent rule accrues correctly on a sale."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Add a 2% rule for cashiers (employee_id=NULL applies to role)
        rule_id = shop.add_commission_rule(employee_id=None, role="cashier", type_="percent", value=2)
        # Create a sale for employee 50 (cashier)
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        result = create_sale(SaleIn(
            customer_name="Commission Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],  # total 500
            payment_method="cash",
            employee_id=50,
        ))
        sale_id = result["id"]
        # 2% of 500 = 10
        assert result["commission"] == 10.0, f"Expected commission 10, got {result.get('commission')}"
        # Verify commission recorded
        summary = shop.get_commissions_summary("2026-08")
        assert any(e["employee_id"] == 50 and e["total_commission"] == 10.0
                   for e in summary["by_employee"]), \
            f"Commission not in summary: {summary}"
    finally:
        cleanup(test_dir)


def test_commission_flat_rule():
    """A flat-Rs-per-sale rule accrues correctly."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.add_commission_rule(employee_id=50, role="cashier", type_="flat", value=50)
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        result = create_sale(SaleIn(
            customer_name="Flat Commission Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            employee_id=50,
        ))
        assert result["commission"] == 50.0, f"Expected flat 50, got {result.get('commission')}"
    finally:
        cleanup(test_dir)


def test_commission_no_rule_for_employee():
    """Sale by employee with no matching rule → no commission."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # No rules defined. Sale by employee 99 (manager).
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        result = create_sale(SaleIn(
            customer_name="No Commission",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            employee_id=99,
        ))
        assert result.get("commission") is None or result.get("commission") == 0, \
            f"Expected no commission, got {result.get('commission')}"
    finally:
        cleanup(test_dir)


def test_commission_employee_rule_overrides_role_rule():
    """Employee-specific rule takes precedence over role-level rule."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Role-level: 2% for cashiers
        shop.add_commission_rule(employee_id=None, role="cashier", type_="percent", value=2)
        # Employee-specific: 5% for employee 50
        shop.add_commission_rule(employee_id=50, role="cashier", type_="percent", value=5)
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        result = create_sale(SaleIn(
            customer_name="Override Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],  # total 500
            payment_method="cash",
            employee_id=50,
        ))
        # Should use 5% (employee-specific) → 25, not 2% → 10
        assert result["commission"] == 25.0, f"Expected 25 (5%), got {result.get('commission')}"
    finally:
        cleanup(test_dir)


def test_cashier_scorecard():
    """Employee scorecard returns revenue, avg txn, discount rate, refund count, variance history."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Make a sale attributed to employee 50
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        create_sale(SaleIn(
            customer_name="Scorecard Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],
            payment_method="cash",
            employee_id=50,
        ))
        sc = shop.get_employee_scorecard(50, "2026-08")
        assert sc["employee_id"] == 50
        assert sc["employee_name"] == "Test Cashier"
        assert sc["role"] == "cashier"
        assert sc["sale_count"] >= 1
        assert sc["revenue"] >= 500.0
        assert sc["avg_transaction"] > 0
        assert "discount_rate" in sc
        assert "refund_count" in sc
        assert "variance_history" in sc
        assert "commission_total" in sc
    finally:
        cleanup(test_dir)


def test_cashier_scorecard_employee_not_found():
    """Scorecard for non-existent employee → error."""
    test_dir = setup_test_db()
    try:
        from app import shop
        result = shop.get_employee_scorecard(99999, "2026-08")
        assert "error" in result
    finally:
        cleanup(test_dir)


def test_commission_rules_crud():
    """Add and list commission rules."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.add_commission_rule(employee_id=None, role="cashier", type_="percent", value=3)
        shop.add_commission_rule(employee_id=50, role="cashier", type_="flat", value=25)
        rules = shop.list_commission_rules(active_only=False)
        assert len(rules) >= 2
        # Verify the role-level rule
        role_rule = next(r for r in rules if r["employee_id"] is None)
        assert role_rule["type"] == "percent"
        assert role_rule["value"] == 3.0
    finally:
        cleanup(test_dir)


def test_invalid_commission_type_rejected():
    """Invalid commission type raises ValueError."""
    test_dir = setup_test_db()
    try:
        from app import shop
        try:
            shop.add_commission_rule(employee_id=None, role="cashier", type_="invalid", value=5)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "type" in str(e).lower()
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_daily_summary_basic()
    test_daily_summary_text_renders()
    test_whatsapp_link_format()
    test_whatsapp_link_empty_phone()
    test_commission_percent_rule()
    test_commission_flat_rule()
    test_commission_no_rule_for_employee()
    test_commission_employee_rule_overrides_role_rule()
    test_cashier_scorecard()
    test_cashier_scorecard_employee_not_found()
    test_commission_rules_crud()
    test_invalid_commission_type_rejected()
    print("\n✅ ALL PHASE 6 OWNER AWARENESS & STAFF TESTS PASSED")
