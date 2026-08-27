"""Phase 0 v8.7 — Change 2: Stock Levels Purchased/Sold/Adjustments bug fix.

Verifies that shop.get_inventory() now returns:
- `purchased` (all-time sum of confirmed bill_items qty, dozen→pcs converted)
- `sold` (all-time sum of non-refunded sale_items qty)
- `adjustments` (all-time sum of stock_adjustments delta)

And that `stock` STILL comes from category_stock_state (source of truth) —
NOT recomputed as `purchased - sold + adjustments`.

Run with: pytest tests/test_inventory_movement.py -v
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest
from test_helpers import setup_test_db, cleanup

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v87_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log",
                  "category_stock_state"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_get_inventory_returns_purchased_field():
    """get_inventory() returns `purchased` for each category."""
    test_dir = setup_test_db()
    try:
        from app import shop
        items = shop.get_inventory()
        assert len(items) > 0
        for it in items:
            assert "purchased" in it, f"Missing 'purchased' field: {it}"
            assert isinstance(it["purchased"], (int, float))
    finally:
        cleanup(test_dir)


def test_get_inventory_purchased_matches_confirmed_bills():
    """purchased for category A = 50 (from sample_data.sql bill 1)."""
    test_dir = setup_test_db()
    try:
        from app import shop
        items = shop.get_inventory()
        cat_a = next(it for it in items if it["code"] == "A")
        assert cat_a["purchased"] == 50, (
            f"Category A purchased should be 50, got {cat_a['purchased']}"
        )
    finally:
        cleanup(test_dir)


def test_get_inventory_sold_excludes_refunded_sales():
    """sold excludes refunded sales (payment_status='refunded')."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Sample data: category A sold=11 (from sale 1)
        items_before = shop.get_inventory()
        cat_a_before = next(it for it in items_before if it["code"] == "A")
        assert cat_a_before["sold"] == 11, (
            f"Category A sold should be 11, got {cat_a_before['sold']}"
        )

        # Mark sale 1 as refunded
        with db.conn() as c:
            c.execute("UPDATE sales SET payment_status='refunded' WHERE id=1")

        items_after = shop.get_inventory()
        cat_a_after = next(it for it in items_after if it["code"] == "A")
        # Sale 1 had 11 pcs of A — refunded → sold should drop to 0
        assert cat_a_after["sold"] == 0, (
            f"Category A sold should be 0 after refunding sale 1, "
            f"got {cat_a_after['sold']}"
        )
    finally:
        cleanup(test_dir)


def test_get_inventory_adjustments_field():
    """get_inventory() returns `adjustments` for each category."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Add a stock adjustment for category A: +5 (damaged/lost)
        with db.conn() as c:
            c.execute(
                "INSERT INTO stock_adjustments(category_id, delta, reason) "
                "VALUES(1, 5, 'test adjustment')"
            )

        items = shop.get_inventory()
        cat_a = next(it for it in items if it["code"] == "A")
        assert cat_a["adjustments"] == 5, (
            f"Category A adjustments should be 5, got {cat_a['adjustments']}"
        )
    finally:
        cleanup(test_dir)


def test_get_inventory_stock_still_from_category_stock_state():
    """`stock` STILL comes from category_stock_state (NOT recomputed).
    Verify by manually changing category_stock_state and checking stock reflects it.
    """
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Sample: category A stock=39 (from category_stock_state)
        items_before = shop.get_inventory()
        cat_a_before = next(it for it in items_before if it["code"] == "A")
        assert cat_a_before["stock"] == 39

        # Manually change category_stock_state (simulate drift)
        with db.conn() as c:
            c.execute(
                "UPDATE category_stock_state SET current_qty=999 "
                "WHERE category_id=1"
            )

        items_after = shop.get_inventory()
        cat_a_after = next(it for it in items_after if it["code"] == "A")
        # stock should reflect the manual change (NOT purchased - sold + adj)
        assert cat_a_after["stock"] == 999, (
            f"stock should reflect category_stock_state (999), "
            f"got {cat_a_after['stock']}"
        )
        # purchased/sold/adjustments should be UNCHANGED
        assert cat_a_after["purchased"] == cat_a_before["purchased"]
        assert cat_a_after["sold"] == cat_a_before["sold"]
    finally:
        cleanup(test_dir)


def test_get_inventory_dozen_unit_converted_to_pieces():
    """bill_items with unit='dozen' are converted to pieces in `purchased`."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Add a new bill with 2 dozen of category A (should add 24 to purchased)
        with db.conn() as c:
            bill_id = c.execute(
                "INSERT INTO bills(supplier_name, bill_date, bill_no, "
                "written_total, computed_total, status, payment_status, created_at) "
                "VALUES('Dozen Test', '2026-08-20', 'B-DOZ', 1920, 1920, "
                "'confirmed', 'paid', '2026-08-20 10:00:00')"
            ).lastrowid
            c.execute(
                "INSERT INTO bill_items(bill_id, raw, item_code, price, qty, unit, "
                "line_total, category_id, page_no) "
                "VALUES(?, 'Dozen Test A', 'A', 80, 2, 'dozen', 1920, 1, 1)",
                (bill_id,),
            )
        # Rebuild stock state so category_stock_state reflects the new purchase
        from app import profit
        profit.rebuild_stock_state()

        items = shop.get_inventory()
        cat_a = next(it for it in items if it["code"] == "A")
        # Was 50, +24 (2 dozen) = 74
        assert cat_a["purchased"] == 74, (
            f"Category A purchased should be 74 (50 + 2 dozen = 24 pcs), "
            f"got {cat_a['purchased']}"
        )
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    import traceback
    tests = [
        test_get_inventory_returns_purchased_field,
        test_get_inventory_purchased_matches_confirmed_bills,
        test_get_inventory_sold_excludes_refunded_sales,
        test_get_inventory_adjustments_field,
        test_get_inventory_stock_still_from_category_stock_state,
        test_get_inventory_dozen_unit_converted_to_pieces,
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
