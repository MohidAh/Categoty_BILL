"""v5.0 Phase 1 — Running Weighted Average Cost tests.

The load-bearing test is `test_doc_example_185_88` which reproduces the exact
example from the owner's spec:

  Opening:  10,000 pcs @ Rs 180  → avg 180.00
  Sale:     sell 3,000           → avg 180.00 (sales don't change avg), qty 7,000
  Purchase: buy 10,000 @ Rs 190  → avg 185.88 (the running weighted average)

A simple "weighted avg of all purchases" would give Rs 185.00 — wrong.
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p1v5_")
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
                  "category_stock_state"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)", (name, is_fixed, budget, 1, sort_order),
            )
    # v5.0 Phase 1: populate category_stock_state from the sample data's bills/sales.
    # This matches what the app does on startup (main.py calls rebuild_stock_state).
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_purchase_only_builds_avg():
    """A single purchase sets avg = unit_price."""
    test_dir = setup_test_db()
    try:
        from app import profit
        # Use category 999 (not in sample data) to start from zero
        result = profit.apply_purchase_to_state(999, 100, 180.0)
        assert abs(result["avg"] - 180.0) < 0.01, f"avg wrong: {result['avg']}"
        assert result["qty"] == 100
        assert abs(result["value"] - 18000.0) < 0.01
    finally:
        cleanup(test_dir)


def test_purchase_then_sale_preserves_avg():
    """A sale does NOT change the avg cost per piece (accounting identity)."""
    test_dir = setup_test_db()
    try:
        from app import profit
        profit.apply_purchase_to_state(999, 100, 180.0)
        result = profit.apply_sale_to_state(999, 30)
        # Avg unchanged
        assert abs(result["avg"] - 180.0) < 0.01, f"avg changed by sale: {result['avg']}"
        # Qty reduced
        assert result["qty"] == 70
        # Value reduced by cogs (= 30 × 180 = 5400)
        assert abs(result["value"] - (18000 - 5400)) < 0.01
        assert abs(result["cogs"] - 5400.0) < 0.01
    finally:
        cleanup(test_dir)


def test_two_purchases_weighted_correctly():
    """Two purchases at different prices → weighted avg, NOT simple avg."""
    test_dir = setup_test_db()
    try:
        from app import profit
        # Buy 100 @ 180 = 18,000
        profit.apply_purchase_to_state(999, 100, 180.0)
        # Buy 100 @ 200 = 20,000 → total 200 pcs, value 38,000 → avg 190
        result = profit.apply_purchase_to_state(999, 100, 200.0)
        assert abs(result["avg"] - 190.0) < 0.01, f"avg wrong: {result['avg']}"
        assert result["qty"] == 200
        assert abs(result["value"] - 38000.0) < 0.01
    finally:
        cleanup(test_dir)


def test_sale_then_purchase_changes_avg():
    """Selling reduces the pool BEFORE the next purchase is averaged in.

    This is the core difference from a simple weighted average of all purchases.
    """
    test_dir = setup_test_db()
    try:
        from app import profit
        # Buy 100 @ 180 = 18,000
        profit.apply_purchase_to_state(999, 100, 180.0)
        # Sell 30 @ avg 180 → cogs 5,400, pool = 70 pcs × 180 = 12,600
        profit.apply_sale_to_state(999, 30)
        # Buy 100 @ 200 = 20,000 → pool = 170 pcs, value = 32,600 → avg = 191.76
        result = profit.apply_purchase_to_state(999, 100, 200.0)
        expected_avg = (12600 + 20000) / 170  # 191.764...
        assert abs(result["avg"] - round(expected_avg, 4)) < 0.01, \
            f"avg wrong: got {result['avg']}, expected {expected_avg:.4f}"
        # If this were a simple weighted avg of purchases only:
        # (100×180 + 100×200) / 200 = 190 — that would be WRONG.
        assert abs(result["avg"] - 190.0) > 0.5, \
            "Got 190 — that's the simple avg, not the running weighted avg"
    finally:
        cleanup(test_dir)


# ─── THE LOAD-BEARING TEST: the doc's 185.88 example ─────────────────────

def test_doc_example_185_88():
    """Reproduce the owner's spec example exactly.

    Step    Event           Qty      Value          Avg Cost
    Opening  10,000 @ 180   10,000   18,00,000      180.00
    Sale     sell 3,000      7,000   12,60,000      180.00  (sale preserves avg)
    Purchase buy 10,000@190 17,000   31,60,000      185.88  ← running weighted avg

    A simple "weighted avg of purchases" would give (10000×180 + 10000×190)/20000 = 185.00.
    """
    test_dir = setup_test_db()
    try:
        from app import profit
        # Use category_id=999 to avoid colliding with sample data categories 1-4
        cat_id = 999
        # Make sure category 99 exists for FK (allow NULL FK so this is fine)
        # Step 1: Opening purchase — 10,000 pcs @ Rs 180
        r1 = profit.apply_purchase_to_state(cat_id, 10000, 180.0)
        assert abs(r1["avg"] - 180.0) < 0.01, f"Step 1 avg wrong: {r1['avg']}"
        assert r1["qty"] == 10000
        assert abs(r1["value"] - 1800000.0) < 0.01
        # Step 2: Sell 3,000 — avg unchanged, qty=7,000, value=12,60,000
        r2 = profit.apply_sale_to_state(cat_id, 3000)
        assert abs(r2["avg"] - 180.0) < 0.01, f"Step 2 avg should be unchanged: {r2['avg']}"
        assert r2["qty"] == 7000
        assert abs(r2["value"] - 1260000.0) < 0.01, f"Step 2 value wrong: {r2['value']}"
        assert abs(r2["cogs"] - 540000.0) < 0.01, f"Step 2 cogs wrong: {r2['cogs']}"
        # Step 3: Buy 10,000 @ Rs 190
        # New pool: 7,000 + 10,000 = 17,000 pcs; value 12,60,000 + 19,00,000 = 31,60,000
        # Avg = 31,60,000 / 17,000 = 185.882...
        r3 = profit.apply_purchase_to_state(cat_id, 10000, 190.0)
        assert r3["qty"] == 17000, f"Step 3 qty wrong: {r3['qty']}"
        assert abs(r3["value"] - 3160000.0) < 0.01, f"Step 3 value wrong: {r3['value']}"
        # The CRITICAL assertion: avg = 185.88 (±0.01)
        assert abs(r3["avg"] - 185.88) < 0.01, \
            f"STEP 3 AVG WRONG: got {r3['avg']}, expected 185.88. " \
            f"(Simple weighted avg of purchases would be 185.00 — that's the bug.)"
        # Verify via the materialized state table too
        state = profit.get_category_stock_state(cat_id)
        assert len(state) == 1
        assert abs(state[0]["current_avg_cost"] - 185.88) < 0.01
        assert abs(state[0]["current_qty"] - 17000) < 0.01
        assert abs(state[0]["current_value"] - 3160000) < 0.01
    finally:
        cleanup(test_dir)


def test_doc_example_via_rebuild():
    """Same 185.88 example, but exercised through rebuild_stock_state()
    (the source of truth) instead of the incremental primitives.

    Uses real bills + sales rows so the replay logic is tested end-to-end.
    """
    test_dir = setup_test_db()
    try:
        from app import db, profit
        cat_id = 999
        # Insert a price category so FK is happy
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(99, 'TestCat', 'Z', 250, '#999999', 99, 1)"
            )
            # Opening purchase: bill 100, bill_date='2026-08-01', 10,000 pcs @ 180
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES(100, 1, 'ABC', '2026-08-01', 'B100', 1800000, 1800000, 'confirmed', 'paid', '2026-08-01 10:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(100, 99, 'Open', 'Z', 180, 10000, 'pcs', 1800000, 1)"
            )
            # Sale: 3,000 pcs, created_at='2026-08-05'
            c.execute(
                "INSERT INTO sales(id, invoice_no, customer_name, subtotal, total, payment_method, "
                "payment_status, created_at, tax_rate, tax_amount) "
                "VALUES(100, 'INV-100', 'Test', 540000, 540000, 'cash', 'paid', '2026-08-05 14:00:00', 0, 0)"
            )
            c.execute(
                "INSERT INTO sale_items(sale_id, item_name, category_id, category_code, "
                "sell_price, cost_price, qty, line_total) "
                "VALUES(100, 'Test', 99, 'Z', 180, 0, 3000, 540000)"
            )
            # Second purchase: bill 101, bill_date='2026-08-10', 10,000 pcs @ 190
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES(101, 1, 'ABC', '2026-08-10', 'B101', 1900000, 1900000, 'confirmed', 'paid', '2026-08-10 11:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(101, 99, 'More', 'Z', 190, 10000, 'pcs', 1900000, 1)"
            )
        # Now rebuild
        result = profit.rebuild_stock_state()
        cat_99 = next(c for c in result["categories"] if c["category_id"] == 99)
        assert abs(cat_99["qty"] - 17000) < 0.01, f"qty wrong: {cat_99['qty']}"
        assert abs(cat_99["value"] - 3160000) < 0.01, f"value wrong: {cat_99['value']}"
        # THE assertion: 185.88
        assert abs(cat_99["avg_cost"] - 185.88) < 0.01, \
            f"avg_cost wrong: got {cat_99['avg_cost']}, expected 185.88"
        # Verify the sale's cost_price was rewritten to the avg at time of sale (180.00)
        with db.conn() as c:
            si = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=100 AND category_id=99"
            ).fetchone()
        assert abs(si["cost_price"] - 180.0) < 0.01, \
            f"sale_items.cost_price should be 180.00 (avg at time of sale), got {si['cost_price']}"
        # Verify rewrote_sales count
        assert result["rewrote_sales"] >= 1, f"Expected ≥1 rewrote sale, got {result['rewrote_sales']}"
    finally:
        cleanup(test_dir)


# ─── Idempotency ──────────────────────────────────────────────────────────

def test_rebuild_idempotent():
    """Rebuild twice → identical results."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r1 = profit.rebuild_stock_state()
        r2 = profit.rebuild_stock_state()
        # Compare per-category state
        def key(c):
            return (c["category_id"], round(c["qty"], 4), round(c["value"], 4),
                    round(c["avg_cost"], 4))
        s1 = sorted(key(c) for c in r1["categories"])
        s2 = sorted(key(c) for c in r2["categories"])
        assert s1 == s2, f"Idempotency broken: {s1} vs {s2}"
        assert r1["rewrote_sales"] == r2["rewrote_sales"]
    finally:
        cleanup(test_dir)


# ─── Dozen conversion ─────────────────────────────────────────────────────

def test_dozen_conversion_in_rebuild():
    """A bill_item with unit='dozen' contributes qty×12 to the pool."""
    test_dir = setup_test_db()
    try:
        from app import db, profit
        cat_id = 999
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(99, 'DozenCat', 'Z', 250, '#999999', 99, 1)"
            )
            # 10 dozen @ Rs 60/piece → 120 pcs @ 60 = 7,200
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES(100, 1, 'ABC', '2026-08-01', 'B100', 720, 720, 'confirmed', 'paid', '2026-08-01 10:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(100, 99, 'Dozen', 'Z', 60, 10, 'dozen', 720, 1)"
            )
        profit.rebuild_stock_state()
        state = profit.get_category_stock_state(99)
        assert len(state) == 1
        assert abs(state[0]["current_qty"] - 120) < 0.01, \
            f"10 dozen should give 120 pcs, got {state[0]['current_qty']}"
        assert abs(state[0]["current_avg_cost"] - 60.0) < 0.01
        assert abs(state[0]["current_value"] - 7200) < 0.01
    finally:
        cleanup(test_dir)


# ─── Reversal (refund) ────────────────────────────────────────────────────

def test_reverse_sale_in_state():
    """Reversing a sale adds qty + cogs back to the pool."""
    test_dir = setup_test_db()
    try:
        from app import profit
        cat_id = 999
        # Buy 100 @ 180 = 18,000
        profit.apply_purchase_to_state(cat_id, 100, 180.0)
        # Sell 30 @ 180 → cogs 5,400, pool = 70 × 180 = 12,600
        profit.apply_sale_to_state(cat_id, 30)
        # Reverse the sale: add 30 + 5,400 back
        result = profit.reverse_sale_in_state(cat_id, 30, cogs=5400.0)
        assert result["qty"] == 100
        assert abs(result["value"] - 18000.0) < 0.01
        assert abs(result["avg"] - 180.0) < 0.01  # avg restored
    finally:
        cleanup(test_dir)


# ─── Stock adjustments ────────────────────────────────────────────────────

def test_negative_adjustment_preserves_avg():
    """A negative adjustment (shrinkage) reduces qty & value, avg unchanged."""
    test_dir = setup_test_db()
    try:
        from app import profit
        cat_id = 999
        profit.apply_purchase_to_state(cat_id, 100, 180.0)  # 100 @ 180
        result = profit.apply_adjustment_to_state(cat_id, -5)  # lose 5
        assert result["qty"] == 95
        assert abs(result["value"] - (18000 - 900)) < 0.01  # 95 × 180 = 17,100
        assert abs(result["avg"] - 180.0) < 0.01  # unchanged
    finally:
        cleanup(test_dir)


def test_positive_adjustment_at_current_avg():
    """A positive adjustment (found stock) adds at current avg."""
    test_dir = setup_test_db()
    try:
        from app import profit
        cat_id = 999
        profit.apply_purchase_to_state(cat_id, 100, 180.0)  # 100 @ 180
        result = profit.apply_adjustment_to_state(cat_id, 5)  # find 5
        assert result["qty"] == 105
        assert abs(result["value"] - (18000 + 900)) < 0.01  # 105 × 180 = 18,900
        assert abs(result["avg"] - 180.0) < 0.01  # unchanged
    finally:
        cleanup(test_dir)


# ─── Integration: create_sale uses running avg ────────────────────────────

def test_create_sale_uses_running_avg():
    """create_sale populates sale_items.cost_price from the running avg, not simple avg."""
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        # Sample data: category 1 has bills @ 80/pc. Buy 50 pcs @ 80 → avg 80.
        # Make a sale of 2 pcs → cost_price should be 80 (the running avg).
        result = create_sale(SaleIn(
            customer_name="Running Avg Test",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=2)],
            payment_method="cash",
        ))
        sale_id = result["id"]
        with db.conn() as c:
            row = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=? AND category_id=1",
                (sale_id,),
            ).fetchone()
        assert abs(row["cost_price"] - 80.0) < 0.01, \
            f"cost_price should be 80.0 (running avg), got {row['cost_price']}"
    finally:
        cleanup(test_dir)


def test_create_sale_after_mixed_purchases():
    """After two purchases at different prices, a sale uses the weighted avg."""
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        # Use category 99 (fresh — not in sample data) to control the exact state.
        # Insert a price_category + two bills, then rebuild state, then make a sale.
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(99, 'MixedCat', 'M', 250, '#999999', 99, 1)"
            )
            # Bill 100: 100 pcs @ 80 = 8,000
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES(100, 1, 'ABC', '2026-08-01', 'B100', 8000, 8000, 'confirmed', 'paid', '2026-08-01 10:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(100, 99, 'First', 'M', 80, 100, 'pcs', 8000, 1)"
            )
            # Bill 101: 50 pcs @ 100 = 5,000
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES(101, 1, 'ABC', '2026-08-05', 'B101', 5000, 5000, 'confirmed', 'paid', '2026-08-05 11:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(101, 99, 'Second', 'M', 100, 50, 'pcs', 5000, 1)"
            )
        # Rebuild state so both purchases are applied in chronological order
        profit.rebuild_stock_state()
        # Pool: 150 pcs, value 13,000, avg = 86.67
        # Now make a sale of 1 pc — cost_price should be 86.67 (weighted avg)
        result = create_sale(SaleIn(
            customer_name="Weighted Test",
            items=[SaleItemIn(category_id=99, category_code="M", sell_price=250, qty=1)],
            payment_method="cash",
        ))
        sale_id = result["id"]
        with db.conn() as c:
            row = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=? AND category_id=99",
                (sale_id,),
            ).fetchone()
        # Weighted avg = (100*80 + 50*100) / 150 = 13000/150 = 86.67
        assert abs(row["cost_price"] - 86.67) < 0.02, \
            f"cost_price should be 86.67 (weighted avg), got {row['cost_price']}"
    finally:
        cleanup(test_dir)


# ─── Endpoint smoke test ──────────────────────────────────────────────────

def test_rebuild_endpoint_works():
    """POST /api/inventory/rebuild-stock-state returns the expected shape."""
    test_dir = setup_test_db()
    try:
        from app.routers.inventory import rebuild_stock_state_route
        result = rebuild_stock_state_route()
        assert "categories" in result
        assert "rewrote_sales" in result
        assert isinstance(result["categories"], list)
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_purchase_only_builds_avg()
    print("✓ test_purchase_only_builds_avg")
    test_purchase_then_sale_preserves_avg()
    print("✓ test_purchase_then_sale_preserves_avg")
    test_two_purchases_weighted_correctly()
    print("✓ test_two_purchases_weighted_correctly")
    test_sale_then_purchase_changes_avg()
    print("✓ test_sale_then_purchase_changes_avg")
    test_doc_example_185_88()
    print("✓ test_doc_example_185_88  ← THE LOAD-BEARING TEST")
    test_doc_example_via_rebuild()
    print("✓ test_doc_example_via_rebuild")
    test_rebuild_idempotent()
    print("✓ test_rebuild_idempotent")
    test_dozen_conversion_in_rebuild()
    print("✓ test_dozen_conversion_in_rebuild")
    test_reverse_sale_in_state()
    print("✓ test_reverse_sale_in_state")
    test_negative_adjustment_preserves_avg()
    print("✓ test_negative_adjustment_preserves_avg")
    test_positive_adjustment_at_current_avg()
    print("✓ test_positive_adjustment_at_current_avg")
    test_create_sale_uses_running_avg()
    print("✓ test_create_sale_uses_running_avg")
    test_create_sale_after_mixed_purchases()
    print("✓ test_create_sale_after_mixed_purchases")
    test_rebuild_endpoint_works()
    print("✓ test_rebuild_endpoint_works")
    print("\n✅ ALL PHASE 1 RUNNING AVG TESTS PASSED — 185.88 example verified")
