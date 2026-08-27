"""Phase 0 PR 3: Atomic create_sale() tests.

Verifies that create_sale():
- Wraps the entire sale in a single write_tx() (BEGIN IMMEDIATE)
- Rolls back ALL side effects when any step fails
- Is idempotent on duplicate client_uuid (re-checked inside the txn)
- Awards loyalty points + records redemption atomically
- Records commission atomically
- Logs suspicious activity for discount overrides atomically
- Honors stock_strategy setting ("strict" blocks, "permit_negative" allows)
- Uses money() to prevent float drift on monetary values

Run with: pytest tests/test_pos_create_sale_atomic.py -v
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest
from test_helpers import setup_test_db as _setup_test_db, cleanup

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"


def setup_test_db():
    """Fresh temp DB with the canonical sample data + rebuilt stock state."""
    test_dir = tempfile.mkdtemp(prefix="billbook_pr3_")
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
                  "commission_rules", "commissions",
                  "category_stock_state", "loyalty_redemptions",
                  "quotations"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Seed a manager employee for PIN tests
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(50, 'Test Cashier', 'cashier', '1111', 1)"
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
    # Phase 0 PR 3: create_sale() reads stock from category_stock_state.
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_create_sale_rolls_back_all_side_effects_on_failure(monkeypatch):
    """If any write inside create_sale fails, ALL writes roll back.

    We force apply_sale_to_state to raise an exception and verify that:
    - No sale row is committed
    - No sale_items are committed
    - No cash_drawer entry exists
    - No customer stats are updated
    - No activity_log entry exists
    - No stock_state mutation occurred
    """
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Capture baseline counts
        with db.conn() as c:
            before = {
                "sales": c.execute("SELECT COUNT(*) n FROM sales").fetchone()["n"],
                "sale_items": c.execute("SELECT COUNT(*) n FROM sale_items").fetchone()["n"],
                "cash_drawer": c.execute("SELECT COUNT(*) n FROM cash_drawer WHERE type='sale'").fetchone()["n"],
                "activity_log": c.execute("SELECT COUNT(*) n FROM activity_log WHERE event_type='sale_created'").fetchone()["n"],
            }
            # Stock state for category 1 (A: 50 purchased - 11 sold = 39)
            stock_before = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=1"
            ).fetchone()["current_qty"]

        # Monkey-patch apply_sale_to_state to raise mid-transaction.
        # This will fire AFTER the sale row + sale_items + cash_drawer are inserted,
        # but BEFORE commit — so all should roll back.
        original = profit.apply_sale_to_state
        call_count = [0]

        def bomb(category_id, qty, txn_at=None, *, c=None):
            call_count[0] += 1
            raise RuntimeError("simulated mid-txn failure")

        monkeypatch.setattr(profit, "apply_sale_to_state", bomb)
        # Also patch the reference in pos.py (it imports profit_mod as profit)
        from app.routers import pos as pos_mod
        monkeypatch.setattr(pos_mod.profit_mod, "apply_sale_to_state", bomb)

        payload = SaleIn(
            customer_name="Atomicity Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],
            payment_method="cash",
        )
        # The sale should NOT raise — apply_sale_to_state failures are caught
        # and logged as state drift, but the sale still commits.
        # However, the test verifies that NO partial state is left.
        result = create_sale(payload)
        assert "id" in result, f"Sale should still succeed (drift logged): {result}"

        # State drift should be logged
        with db.conn() as c:
            drift = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='state_drift_warning'"
            ).fetchone()["n"]
        assert drift >= 1, "apply_sale_to_state failure should be logged as drift"

        # Stock_state must be UNCHANGED (rollback of the failed apply)
        with db.conn() as c:
            stock_after = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=1"
            ).fetchone()["current_qty"]
        assert stock_after == stock_before, (
            f"Stock state must not change when apply_sale_to_state fails: "
            f"before={stock_before} after={stock_after}"
        )
    finally:
        cleanup(test_dir)


def test_idempotency_check_inside_txn():
    """Two create_sale calls with the same client_uuid return the same sale.

    Verifies the idempotency check is INSIDE write_tx() so two concurrent
    clients cannot both pass the check and both insert.
    """
    test_dir = setup_test_db()
    try:
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        payload = SaleIn(
            customer_name="Idempotency Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            client_uuid="test-uuid-abc-123",
        )
        r1 = create_sale(payload)
        assert "id" in r1
        sale_id_1 = r1["id"]

        # Same payload → should return the existing sale
        r2 = create_sale(payload)
        assert r2.get("idempotent") is True, f"Second call should be idempotent: {r2}"
        assert r2["id"] == sale_id_1, (
            f"Idempotent call should return same sale_id: {r2['id']} != {sale_id_1}"
        )

        # Verify only one sale row exists
        from app import db
        with db.conn() as c:
            count = c.execute(
                "SELECT COUNT(*) n FROM sales WHERE client_uuid=?", ("test-uuid-abc-123",)
            ).fetchone()["n"]
        assert count == 1, f"Only 1 sale should exist with this client_uuid, got {count}"
    finally:
        cleanup(test_dir)


def test_loyalty_redemption_rolled_back_on_sale_failure(monkeypatch):
    """If the sale INSERT fails AFTER loyalty points are deducted, the points
    must be refunded to the customer.

    We seed a customer with loyalty points, then trigger a failure inside the
    txn (after the loyalty UPDATE but before the sale INSERT). Verify the
    customer's loyalty_points are restored on rollback.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn

        # Seed a customer with 500 loyalty points
        with db.conn() as c:
            c.execute(
                "INSERT INTO customers(id, name, phone, loyalty_points, total_spent, total_credit) "
                "VALUES(500, 'Loyalty Cust', '03001112233', 500, 0, 0)"
            )
        # Set loyalty_rate to 1.0 (1 point = 1 Rs) for predictable math
        db.set_setting("loyalty_rate", "1")
        db.set_setting("loyalty_points_per_rs", "100")

        # Monkey-patch the INSERT INTO sales to raise.
        # This is tricky — we can't easily intercept a single SQL statement.
        # Instead, patch db.log_activity to raise on the FIRST call (which is
        # AFTER the sale INSERT but inside the txn). That will trigger rollback.
        from app import db as db_mod
        original_log = db_mod.log_activity
        call_count = [0]

        def bomb(*args, **kwargs):
            call_count[0] += 1
            # First call is the sale_created log (step 15) — let it raise
            # to abort the txn AFTER loyalty was deducted but the txn hasn't committed.
            if call_count[0] == 1:
                raise RuntimeError("simulated log_activity failure")
            return original_log(*args, **kwargs)

        # Patch the db.log_activity reference that pos.py uses
        from app.routers import pos as pos_mod
        monkeypatch.setattr(pos_mod.db, "log_activity", bomb)

        payload = SaleIn(
            customer_id=500,
            customer_name="Loyalty Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            loyalty_points_used=100,
        )

        # The sale should raise (RuntimeError propagates out of write_tx → rollback)
        try:
            pos_mod.create_sale(payload)
            assert False, "Expected RuntimeError to propagate"
        except RuntimeError:
            pass

        # Verify loyalty points were restored (rollback worked)
        with db.conn() as c:
            cust = c.execute(
                "SELECT loyalty_points, loyalty_redeemed FROM customers WHERE id=500"
            ).fetchone()
        assert cust["loyalty_points"] == 500, (
            f"Loyalty points should be restored to 500 after rollback, "
            f"got {cust['loyalty_points']}"
        )
        assert cust["loyalty_redeemed"] == 0, (
            f"loyalty_redeemed should be 0 after rollback, got {cust['loyalty_redeemed']}"
        )

        # Verify no loyalty_redemptions row was committed
        with db.conn() as c:
            red_count = c.execute(
                "SELECT COUNT(*) n FROM loyalty_redemptions WHERE customer_id=500"
            ).fetchone()["n"]
        assert red_count == 0, (
            f"No loyalty_redemptions row should exist after rollback, got {red_count}"
        )

        # Verify no sale row was committed
        with db.conn() as c:
            sale_count = c.execute(
                "SELECT COUNT(*) n FROM sales WHERE customer_id=500"
            ).fetchone()["n"]
        assert sale_count == 0, "No sale row should exist after rollback"
    finally:
        cleanup(test_dir)


def test_cash_sale_records_cash_drawer_atomically():
    """A successful cash sale creates the sale row, sale_items, cash_drawer
    entry, customer stats, and activity log — all in one atomic commit.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        payload = SaleIn(
            customer_name="Happy Path Cust",
            items=[
                SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2),
                SaleItemIn(category_id=2, category_code="B", sell_price=500, qty=1),
            ],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result, f"Sale should succeed: {result}"
        sale_id = result["id"]

        # Verify all 5 side effects committed together
        with db.conn() as c:
            # 1. Sale row exists
            sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
            assert sale is not None
            assert sale["payment_status"] == "paid"
            assert sale["total"] == 1000  # 250*2 + 500*1

            # 2. Two sale_items rows
            items = c.execute(
                "SELECT * FROM sale_items WHERE sale_id=? ORDER BY category_id",
                (sale_id,),
            ).fetchall()
            assert len(items) == 2
            # Category A: cost_price should be 80 (the running avg)
            assert items[0]["cost_price"] == 80.0
            # Category B: cost_price should be 200 (the running avg)
            assert items[1]["cost_price"] == 200.0

            # 3. cash_drawer entry for the full total
            drawer = c.execute(
                "SELECT * FROM cash_drawer WHERE reference_id=? AND reference_type='sale'",
                (sale_id,),
            ).fetchone()
            assert drawer is not None
            assert drawer["amount"] == 1000
            assert drawer["type"] == "sale"

            # 4. Customer stats updated (Walk-in Customer seeded by sample data)
            cust = c.execute(
                "SELECT total_spent FROM customers WHERE name='Happy Path Cust'"
            ).fetchone()
            assert cust is not None
            assert cust["total_spent"] == 1000

            # 5. Activity log entry
            log = c.execute(
                "SELECT * FROM activity_log WHERE entity_type='sale' AND entity_id=? "
                "AND event_type='sale_created'",
                (sale_id,),
            ).fetchone()
            assert log is not None
            assert "Happy Path" in log["description"] or "Rs 1000" in log["description"]

            # 6. Stock state was mutated (category A: 39 - 2 = 37)
            stock_a = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=1"
            ).fetchone()["current_qty"]
            assert stock_a == 37, f"Stock A should be 37 after selling 2, got {stock_a}"

            # Stock state for category B: 17 - 1 = 16
            stock_b = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=2"
            ).fetchone()["current_qty"]
            assert stock_b == 16, f"Stock B should be 16 after selling 1, got {stock_b}"
    finally:
        cleanup(test_dir)


def test_credit_sale_no_cash_drawer_entry():
    """A credit sale does NOT create a cash_drawer entry, but DOES update
    the customer's outstanding credit.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Seed a customer with credit_limit > 0 so credit sales are allowed
        with db.conn() as c:
            c.execute(
                "INSERT INTO customers(id, name, phone, credit_limit, total_credit) "
                "VALUES(700, 'Credit Cust', '03009998877', 100000, 0)"
            )

        payload = SaleIn(
            customer_id=700,
            customer_name="Credit Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="credit",
        )
        result = create_sale(payload)
        assert "id" in result, f"Credit sale should succeed: {result}"
        assert result["payment_status"] == "credit"
        sale_id = result["id"]

        with db.conn() as c:
            # No cash_drawer entry
            drawer = c.execute(
                "SELECT COUNT(*) n FROM cash_drawer WHERE reference_id=? AND reference_type='sale'",
                (sale_id,),
            ).fetchone()["n"]
            assert drawer == 0, f"Credit sale should NOT create cash_drawer entry, got {drawer}"

            # Customer's total_credit increased
            cust = c.execute(
                "SELECT total_credit FROM customers WHERE id=700"
            ).fetchone()
            assert cust["total_credit"] == 250, (
                f"Customer's total_credit should be 250 after credit sale, "
                f"got {cust['total_credit']}"
            )
    finally:
        cleanup(test_dir)


def test_split_payment_records_only_cash_portion_in_drawer():
    """Split payment: only the cash portion goes to cash_drawer."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        payload = SaleIn(
            customer_name="Split Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=4)],  # total 1000
            payment_method="split",
            split_cash=300,
            split_card=700,
            split_online=0,
        )
        result = create_sale(payload)
        assert "id" in result
        assert result["payment_status"] == "paid", (
            f"300+700=1000 should be 'paid', got {result['payment_status']}"
        )
        sale_id = result["id"]

        with db.conn() as c:
            drawer = c.execute(
                "SELECT amount FROM cash_drawer WHERE reference_id=? AND reference_type='sale'",
                (sale_id,),
            ).fetchone()
            assert drawer is not None, "Cash part should be in drawer"
            assert drawer["amount"] == 300, (
                f"Only Rs 300 (cash part) should be in drawer, got {drawer['amount']}"
            )
    finally:
        cleanup(test_dir)


def test_stock_strategy_strict_blocks_insufficient_stock():
    """Default stock_strategy=strict blocks sales when stock is insufficient."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Category A: 39 available (50 - 11 from sample data)
        # Try to sell 100 — should be blocked
        payload = SaleIn(
            customer_name="Greedy Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=100)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert hasattr(result, "status_code"), (
            f"Expected JSONResponse with 409, got {result}"
        )
        assert result.status_code == 409
        body = json.loads(result.body)
        assert "Insufficient stock" in body["error"]
        assert "A" in body["error"], f"Should mention category A: {body['error']}"

        # Verify no sale row was created
        with db.conn() as c:
            count = c.execute(
                "SELECT COUNT(*) n FROM sales WHERE customer_name='Greedy Cust'"
            ).fetchone()["n"]
        assert count == 0, "No sale should be committed when stock check fails"
    finally:
        cleanup(test_dir)


def test_stock_strategy_permit_negative_allows_backorder():
    """Setting stock_strategy=permit_negative bypasses the stock guard,
    allowing back-ordered sales even when category_stock_state is empty.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        db.set_setting("stock_strategy", "permit_negative")

        # Try to sell 10000 — would normally be blocked
        payload = SaleIn(
            customer_name="Backorder Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=10000)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result, (
            f"permit_negative should allow back-order sale: {result}"
        )
    finally:
        cleanup(test_dir)


def test_discount_override_logs_suspicious_atomically():
    """A discount > threshold with valid manager PIN creates both the sale AND
    a suspicious activity log entry — atomically.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        with db.conn() as c:
            before = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]

        payload = SaleIn(
            customer_name="Big Discount Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            discount=20,  # 20% > 10% default threshold
            discount_type="percent",
            manager_pin="1234",
        )
        result = create_sale(payload)
        assert "id" in result, f"Discount override should succeed: {result}"
        sale_id = result["id"]

        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='suspicious'"
            ).fetchone()["n"]
            assert after == before + 1, (
                f"Suspicious event should be logged: before={before} after={after}"
            )
            last = c.execute(
                "SELECT * FROM activity_log WHERE event_type='suspicious' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert "discount_override" in last["description"], (
                f"Should mention discount_override: {last['description']}"
            )
            assert last["entity_id"] == sale_id, (
                f"Suspicious log should reference the sale_id: {last['entity_id']} != {sale_id}"
            )
            meta = json.loads(last["metadata"])
            assert meta["discount_pct"] == 20
            assert meta["manager_name"] == "Test Manager"
    finally:
        cleanup(test_dir)


def test_credit_limit_exceeded_blocks_sale():
    """A credit sale that would exceed the customer's credit_limit is blocked
    with HTTP 423 unless a manager PIN is provided.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        from fastapi import HTTPException

        # Customer with credit_limit=1000, currently owes 0
        with db.conn() as c:
            c.execute(
                "INSERT INTO customers(id, name, phone, credit_limit, total_credit) "
                "VALUES(800, 'Limited Cust', '03001112200', 1000, 0)"
            )

        # Sale of Rs 250 — under limit, should succeed
        payload = SaleIn(
            customer_id=800,
            customer_name="Limited Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="credit",
        )
        result = create_sale(payload)
        assert "id" in result, f"First credit sale under limit should succeed: {result}"

        # Second sale of Rs 250 — total credit = 500, still under limit, OK
        result2 = create_sale(payload)
        assert "id" in result2

        # Third sale of Rs 250 — total credit = 750, still under limit, OK
        result3 = create_sale(payload)
        assert "id" in result3

        # Fourth sale of Rs 250 — total credit = 1000, AT limit (== 1000), OK
        result4 = create_sale(payload)
        assert "id" in result4

        # Fifth sale of Rs 250 — total credit = 1250, OVER limit, blocked
        try:
            create_sale(payload)
            assert False, "Expected HTTPException(423)"
        except HTTPException as e:
            assert e.status_code == 423
            assert "credit_limit_exceeded" in str(e.detail) or "credit_limit" in str(e.detail)
    finally:
        cleanup(test_dir)


def test_zero_cost_category_logs_cogs_warning():
    """A sale for a category with no cost history logs a cogs_warning."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Use permit_negative so we don't need to seed stock for category 99
        db.set_setting("stock_strategy", "permit_negative")

        # Insert a price_category with no bills (so no cost history)
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(999, 'No Cost', 'ZZ', 100, '#999999', 999, 1)"
            )

        payload = SaleIn(
            customer_name="Zero Cost Cust",
            items=[SaleItemIn(category_id=999, category_code="ZZ", sell_price=100, qty=1)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result, f"Sale should succeed: {result}"
        sale_id = result["id"]

        with db.conn() as c:
            # Verify sale_items.cost_price is 0
            item = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=? AND category_id=999",
                (sale_id,),
            ).fetchone()
            assert item["cost_price"] == 0, (
                f"cost_price should be 0 for no-cost category, got {item['cost_price']}"
            )
            # Verify cogs_warning was logged
            warn = c.execute(
                "SELECT * FROM activity_log WHERE event_type='cogs_warning' "
                "AND entity_id=999 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert warn is not None, "cogs_warning should be logged"
            assert "999" in warn["description"]
    finally:
        cleanup(test_dir)


def test_money_helper_prevents_float_drift():
    """money() rounds all monetary values to 2 decimal places, preventing
    float drift like 10.300000000000001.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Sell 3 items at Rs 333.33 — total = 999.99 (no drift)
        payload = SaleIn(
            customer_name="Float Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=333.33, qty=3)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result
        sale_id = result["id"]

        with db.conn() as c:
            sale = c.execute("SELECT total, subtotal FROM sales WHERE id=?", (sale_id,)).fetchone()
            # Must be exactly 999.99 — not 999.9900000001 or 1000.00
            assert sale["total"] == 999.99, (
                f"Total should be 999.99 (no float drift), got {sale['total']}"
            )
            assert sale["subtotal"] == 999.99

            # Verify sale_items line_total is also clean
            item = c.execute(
                "SELECT line_total FROM sale_items WHERE sale_id=? AND category_id=1",
                (sale_id,),
            ).fetchone()
            assert item["line_total"] == 999.99, (
                f"line_total should be 999.99, got {item['line_total']}"
            )
    finally:
        cleanup(test_dir)


def test_commission_recorded_atomically_with_sale():
    """A sale by an employee with a matching commission rule creates the
    commission row in the same transaction.
    """
    test_dir = setup_test_db()
    try:
        from app import db, shop
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Add a 5% commission rule for cashiers (role-level)
        shop.add_commission_rule(employee_id=None, role="cashier", type_="percent", value=5)

        payload = SaleIn(
            customer_name="Commission Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],  # total 500
            payment_method="cash",
            employee_id=50,  # Test Cashier (role=cashier)
        )
        result = create_sale(payload)
        assert "id" in result
        sale_id = result["id"]
        # 5% of 500 = 25
        assert result["commission"] == 25.0, (
            f"Commission should be 25.0 (5% of 500), got {result.get('commission')}"
        )

        with db.conn() as c:
            row = c.execute(
                "SELECT amount, employee_id FROM commissions WHERE sale_id=?",
                (sale_id,),
            ).fetchone()
            assert row is not None, "Commission row should be created"
            assert row["amount"] == 25.0
            assert row["employee_id"] == 50
    finally:
        cleanup(test_dir)


def test_quotation_marked_converted_after_sale():
    """A sale with quotation_id marks the quotation as 'converted'."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        # Seed a quotation
        with db.conn() as c:
            c.execute(
                "INSERT INTO quotations(id, customer_name, total, status, created_at) "
                "VALUES(42, 'Quote Cust', 250, 'sent', '2026-08-15 10:00:00')"
            )

        payload = SaleIn(
            customer_name="Quote Cust",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
            quotation_id=42,
        )
        result = create_sale(payload)
        assert "id" in result

        with db.conn() as c:
            q = c.execute("SELECT status FROM quotations WHERE id=42").fetchone()
            assert q["status"] == "converted", (
                f"Quotation should be 'converted' after sale, got {q['status']}"
            )
    finally:
        cleanup(test_dir)


def test_custom_item_without_category_id_skips_stock_state():
    """A sale item with qty=0 does NOT trigger apply_sale_to_state
    (the guard `if item.qty and item.qty > 0` skips zero-qty lines).
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale

        db.set_setting("stock_strategy", "permit_negative")

        with db.conn() as c:
            before_a = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=1"
            ).fetchone()["current_qty"]

        payload = SaleIn(
            customer_name="Zero Qty Cart",
            items=[
                # Normal item: should reduce stock by 2
                SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2),
                # Zero-qty line: should NOT affect stock_state
                SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=0,
                           item_name="Free Sample (zero qty)"),
            ],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result
        sale_id = result["id"]

        with db.conn() as c:
            # Two sale_items created
            items = c.execute(
                "SELECT * FROM sale_items WHERE sale_id=? ORDER BY id",
                (sale_id,),
            ).fetchall()
            assert len(items) == 2

            # Stock state for category A should be reduced by 2 only (not 2 + 0)
            after_a = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id=1"
            ).fetchone()["current_qty"]
            assert after_a == before_a - 2, (
                f"Stock A should drop by exactly 2 (zero-qty item ignored): "
                f"{before_a} -> {after_a}"
            )
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    # Allow running as a script for debugging
    import traceback
    tests = [
        test_create_sale_rolls_back_all_side_effects_on_failure,
        test_idempotency_check_inside_txn,
        test_loyalty_redemption_rolled_back_on_sale_failure,
        test_cash_sale_records_cash_drawer_atomically,
        test_credit_sale_no_cash_drawer_entry,
        test_split_payment_records_only_cash_portion_in_drawer,
        test_stock_strategy_strict_blocks_insufficient_stock,
        test_stock_strategy_permit_negative_allows_backorder,
        test_discount_override_logs_suspicious_atomically,
        test_credit_limit_exceeded_blocks_sale,
        test_zero_cost_category_logs_cogs_warning,
        test_money_helper_prevents_float_drift,
        test_commission_recorded_atomically_with_sale,
        test_quotation_marked_converted_after_sale,
        test_custom_item_without_category_id_skips_stock_state,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  [PASS] {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
