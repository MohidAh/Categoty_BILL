"""v4.0 Phase 4 — Cash & Theft Controls tests."""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p4_")
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
                  "cash_drawer", "shifts", "employees"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Add a manager employee with a known PIN for PIN tests
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
    # Phase 0 PR 3: create_sale() now reads stock from category_stock_state
    # (the v8.5 source of truth) instead of computing purchased-sold+adj on
    # the fly. The sample_data.sql seeds bills + sale_items directly, so we
    # must rebuild the materialized state before any sale test runs.
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_count_denominations_basic():
    """count_denominations sums correctly."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # 2x5000 + 1x1000 + 3x100 + 50 coins = 10000 + 1000 + 300 + 50 = 11350
        denom = {"5000": 2, "1000": 1, "100": 3, "coins": 50}
        total = shop.count_denominations(denom)
        assert total == 11350.0, f"Expected 11350, got {total}"
    finally:
        cleanup(test_dir)


def test_count_denominations_empty():
    """Empty/None denominations → 0."""
    test_dir = setup_test_db()
    try:
        from app import shop
        assert shop.count_denominations({}) == 0.0
        assert shop.count_denominations(None) == 0.0
    finally:
        cleanup(test_dir)


def test_verify_manager_pin_valid():
    """Valid manager PIN returns employee dict."""
    test_dir = setup_test_db()
    try:
        from app import shop
        mgr = shop.verify_manager_pin("1234")
        assert mgr is not None
        assert mgr["name"] == "Test Manager"
        assert mgr["role"] == "manager"
    finally:
        cleanup(test_dir)


def test_verify_manager_pin_invalid():
    """Invalid PIN returns None."""
    test_dir = setup_test_db()
    try:
        from app import shop
        assert shop.verify_manager_pin("9999") is None
        assert shop.verify_manager_pin("") is None
        assert shop.verify_manager_pin(None) is None
    finally:
        cleanup(test_dir)


def test_discount_threshold_blocks_without_pin():
    """15% discount with default threshold 10% → 403 discount_pin_required."""
    test_dir = setup_test_db()
    try:
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        # Category A: 50 purchased, 11 already sold → 39 available
        payload = SaleIn(
            customer_name="Discount Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            discount=15,  # 15% > 10% threshold
            discount_type="percent",
        )
        result = create_sale(payload)
        # Should be a JSONResponse with 403 status
        assert hasattr(result, "status_code"), f"Expected JSONResponse, got {result}"
        assert result.status_code == 403
        body = json.loads(result.body)
        assert body.get("code") == "discount_pin_required", f"Wrong code: {body}"
    finally:
        cleanup(test_dir)


def test_discount_threshold_allows_with_pin():
    """15% discount with valid manager_pin → sale succeeds, logged as suspicious."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        with db.conn() as c:
            before = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]
        payload = SaleIn(
            customer_name="Discount Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            discount=15,
            discount_type="percent",
            manager_pin="1234",
        )
        result = create_sale(payload)
        assert not hasattr(result, "status_code"), \
            f"Sale should succeed with PIN, got {result.status_code if hasattr(result, 'status_code') else result}"
        assert "id" in result, f"Sale creation failed: {result}"
        # Verify suspicious event logged
        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]
            last = c.execute(
                "SELECT * FROM activity_log WHERE event_type='suspicious' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert after == before + 1, f"Suspicious event not logged: before={before} after={after}"
        assert "discount_override" in last["description"]
    finally:
        cleanup(test_dir)


def test_discount_below_threshold_no_pin_needed():
    """5% discount with default threshold 10% → succeeds without PIN."""
    test_dir = setup_test_db()
    try:
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        payload = SaleIn(
            customer_name="Small Discount",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            discount=5,
            discount_type="percent",
        )
        result = create_sale(payload)
        assert "id" in result, f"Sale should succeed: {result}"
    finally:
        cleanup(test_dir)


def test_refund_requires_pin():
    """Refund without PIN → 403."""
    test_dir = setup_test_db()
    try:
        from app.routers.pos import refund_sale
        # Sale 1 exists in sample data
        try:
            refund_sale(1, payload={})
            assert False, "Expected HTTPException(403)"
        except Exception as e:
            assert "403" in str(e) or "Manager PIN required" in str(e), \
                f"Wrong error: {e}"
    finally:
        cleanup(test_dir)


def test_refund_with_pin_logs_suspicious():
    """Refund with valid PIN → succeeds, logged as suspicious."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import refund_sale
        with db.conn() as c:
            before = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]
        result = refund_sale(1, payload={"manager_pin": "1234", "reason": "Customer return"})
        assert result["ok"] is True
        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]
            last = c.execute(
                "SELECT * FROM activity_log WHERE event_type='suspicious' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert after == before + 1, "Suspicious event not logged for refund"
        assert "refund" in last["description"].lower()
        assert "Customer return" in last["description"]
    finally:
        cleanup(test_dir)


def test_end_shift_with_denominations():
    """End shift with denomination count → variance computed correctly."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Start a shift with opening 5000 (employee 99 exists in test setup)
        sid = shop.start_shift(employee_id=99, opening_cash=5000)
        # Make a cash sale of 250 (creates cash_drawer entry of +250)
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        create_sale(SaleIn(
            customer_name="Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
        ))
        # End shift with counted denominations summing to 5250 (exact match)
        denom = {"5000": 1, "100": 2, "50": 1}  # 5000 + 200 + 50 = 5250
        result = shop.end_shift_with_denominations(denominations=denom, blind=False)
        assert result["ok"] is True
        assert result["counted_cash"] == 5250.0
        # Expected = cash_drawer sum = opening 5000 + sale 250 = 5250
        assert result["expected_cash"] == 5250.0, f"Expected 5250, got {result['expected_cash']}"
        assert result["variance"] == 0.0, f"Expected 0 variance, got {result['variance']}"
    finally:
        cleanup(test_dir)


def test_end_shift_blind():
    """Blind close marks the shift with blind_close=1."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        shop.start_shift(employee_id=99, opening_cash=5000)
        result = shop.end_shift_with_denominations(
            closing_cash=5000, blind=True, manager_pin="1234"
        )
        assert result["blind"] is True
        with db.conn() as c:
            row = c.execute(
                "SELECT blind_close FROM shifts WHERE id=?", (result["shift_id"],)
            ).fetchone()
        assert row["blind_close"] == 1
    finally:
        cleanup(test_dir)


def test_end_shift_variance_logged_suspicious():
    """Variance > Rs 100 logs a suspicious event."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        shop.start_shift(employee_id=99, opening_cash=5000)
        with db.conn() as c:
            before = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious' AND description LIKE '%variance%'"
            ).fetchone()["n"]
        # Counted = 6000 but expected ~5000 → variance of 1000
        result = shop.end_shift_with_denominations(closing_cash=6000)
        assert abs(result["variance"] - 1000) < 1, f"Variance wrong: {result['variance']}"
        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious' AND description LIKE '%variance%'"
            ).fetchone()["n"]
        assert after == before + 1, "Variance suspicious event not logged"
    finally:
        cleanup(test_dir)


def test_employee_variance_history():
    """get_employee_variance_history returns closed-shift variance rows."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Start + end a shift for employee 99 with a known variance
        shop.start_shift(employee_id=99, opening_cash=1000)
        # Expected = 1000 (opening cash_drawer entry); counted = 1100 → variance = 100
        shop.end_shift_with_denominations(closing_cash=1100)
        history = shop.get_employee_variance_history(99)
        assert len(history) >= 1
        assert history[0]["variance"] == 100.0, f"Expected 100, got {history[0]['variance']}"
    finally:
        cleanup(test_dir)


def test_list_suspicious_events():
    """list_suspicious_events returns suspicious activity entries."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.log_suspicious("test_event", "test", 1, "Test suspicious", {"k": "v"})
        events = shop.list_suspicious_events(limit=10)
        assert any("test_event" in e["description"] for e in events)
    finally:
        cleanup(test_dir)


def test_settings_defaults_seeded():
    """init() seeds the 4 cash-control default settings."""
    test_dir = setup_test_db()
    try:
        from app import db
        assert db.get_setting("max_discount_pct_without_pin", "") == "10"
        assert db.get_setting("require_pin_for_refund", "") == "true"
        assert db.get_setting("require_pin_for_price_override", "") == "true"
        assert db.get_setting("blind_close_enabled", "") == "false"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_count_denominations_basic()
    test_count_denominations_empty()
    test_verify_manager_pin_valid()
    test_verify_manager_pin_invalid()
    test_discount_threshold_blocks_without_pin()
    test_discount_threshold_allows_with_pin()
    test_discount_below_threshold_no_pin_needed()
    test_refund_requires_pin()
    test_refund_with_pin_logs_suspicious()
    test_end_shift_with_denominations()
    test_end_shift_blind()
    test_end_shift_variance_logged_suspicious()
    test_employee_variance_history()
    test_list_suspicious_events()
    test_settings_defaults_seeded()
    print("\n✅ ALL PHASE 4 CASH & THEFT CONTROL TESTS PASSED")
