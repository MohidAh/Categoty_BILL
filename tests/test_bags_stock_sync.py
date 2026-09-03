"""v8.19.1: Bags categories — stock tracks qty SOLD, not purchased − sold.

Business rule (user-defined): shopping-bag categories ("Bag Rs 20", "Bags",
code BAG…) never have purchase bills entered in BillBook — bags are bought
as EXPENSES. So on POS import (and on every stock-state rebuild):

    sold = total qty of non-refunded sale_items for the category
    if current_qty < sold:  current_qty = sold
    else:                   leave it alone  (never decreases)

Covered here:
  1. sync_bags_stock_to_sold: raises legacy negative stock to total sold
  2. the guard: stock above sold is never lowered
  3. refunded sales don't count towards "sold"
  4. rebuild_stock_state applies the rule (dirty-flag boot rebuild too)
  5. deleting an import run does NOT reverse bag stock (never decremented)
  6. refunding ANY bag sale (imported or built-in POS) never bumps stock
     (v8.18.17 unified model — no origin decrements, none re-adds)
"""
import pytest

from app import db
from app.profit_engine import (bag_category_ids, sync_bags_stock_to_sold,
                               rebuild_stock_state, _save_state)


# ─── helpers ────────────────────────────────────────────────────────────────

def _mk_category(name, code, sell_price=20.0, active=1):
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO price_categories(name, code, sell_price, active) "
            "VALUES(?,?,?,?)", (name, code, sell_price, active))
        return cur.lastrowid


def _mk_sale(items, payment_status="paid", created_at="2026-08-10 10:00:00"):
    """items: list of (category_id, qty). Returns sale_id."""
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO sales(invoice_no, total, payment_method, "
            "payment_status, created_at) VALUES(?,?,?,?,?)",
            ("T-INV-%d" % (c.execute("SELECT COUNT(*) AS n FROM sales")
                           .fetchone()["n"] + 1), 100.0, "cash",
             payment_status, created_at))
        sale_id = cur.lastrowid
        for cid, qty in items:
            c.execute(
                "INSERT INTO sale_items(sale_id, item_name, category_id, "
                "qty, sell_price, line_total) VALUES(?,?,?,?,?,?)",
                (sale_id, "test item", cid, qty, 20.0, 20.0 * qty))
    return sale_id


def _stock(cid):
    with db.conn() as c:
        row = c.execute(
            "SELECT current_qty FROM category_stock_state WHERE category_id=?",
            (cid,)).fetchone()
        return float(row["current_qty"]) if row else 0.0


# ─── identification ─────────────────────────────────────────────────────────

def test_bag_category_ids_matches_bag_names_and_codes(tmp_db_path):
    bag_named = _mk_category("Bag Rs 20", "BAG20")
    bag_named2 = _mk_category("Bags", "MISC")
    bag_coded = _mk_category("Carrier", "BAG60")
    not_bag = _mk_category("Item 250", "A")
    not_bag2 = _mk_category("Shoes", "SHOE")
    inactive_bag = _mk_category("Bag Rs 99", "BAG99", active=0)
    with db.conn() as c:
        ids = bag_category_ids(c)
    assert {bag_named, bag_named2, bag_coded} <= ids
    assert not_bag not in ids and not_bag2 not in ids
    assert inactive_bag not in ids, "inactive categories are ignored"


# ─── the sync rule ──────────────────────────────────────────────────────────

def test_sync_raises_negative_stock_to_total_sold(tmp_db_path):
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_sale([(bag, 5)])
    _mk_sale([(bag, 7)])                       # total sold = 12
    with db.conn() as c:
        _save_state(c, bag, -12.0, 0.0, 0.0)   # legacy negative stock
    changed = sync_bags_stock_to_sold()
    assert _stock(bag) == 12.0
    assert any(ch["category_id"] == bag for ch in changed)


def test_sync_never_lowers_stock_above_sold(tmp_db_path):
    bag = _mk_category("Bag Rs 30", "BAG30")
    _mk_sale([(bag, 4)])                       # sold = 4
    with db.conn() as c:
        _save_state(c, bag, 100.0, 0.0, 0.0)   # manual stock far above sold
    changed = sync_bags_stock_to_sold()
    assert _stock(bag) == 100.0, "stock above sold must be left alone"
    assert not any(ch["category_id"] == bag for ch in changed)


def test_sync_excludes_refunded_sales(tmp_db_path):
    bag = _mk_category("Bag Rs 10", "BAG10")
    _mk_sale([(bag, 5)])                       # active
    _mk_sale([(bag, 7)], payment_status="refunded")   # refunded — not sold
    with db.conn() as c:
        _save_state(c, bag, 0.0, 0.0, 0.0)
    sync_bags_stock_to_sold()
    assert _stock(bag) == 5.0


def test_sync_idempotent(tmp_db_path):
    bag = _mk_category("Bag Rs 50", "BAG50")
    _mk_sale([(bag, 9)])
    sync_bags_stock_to_sold()
    assert _stock(bag) == 9.0
    changed = sync_bags_stock_to_sold()        # second run: nothing to do
    assert not any(ch["category_id"] == bag for ch in changed)
    assert _stock(bag) == 9.0


# ─── rebuild applies the rule ───────────────────────────────────────────────

def test_rebuild_stock_state_applies_bags_rule(tmp_db_path):
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    # bags: 3 sales totalling 15 — no purchase bills (they're expenses)
    _mk_sale([(bag, 5)], created_at="2026-08-01 10:00:00")
    _mk_sale([(bag, 10)], created_at="2026-08-02 10:00:00")
    # normal: bought 100 @ 10, sold 20 -> expect 80
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO bills(supplier_name, status, payment_status, bill_date) "
            "VALUES('S', 'confirmed', 'paid', '2026-07-01')")
        bid = cur.lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, category_id, price, qty, "
            "line_total) VALUES(?,?,?,?,?,?)", (bid, "item", normal, 10.0, 100, 1000.0))
    _mk_sale([(normal, 20)], created_at="2026-08-03 10:00:00")

    result = rebuild_stock_state()
    assert _stock(bag) == 15.0, "bag stock must show total sold, not -15"
    assert _stock(normal) == 80.0
    assert any(ch["category_id"] == bag for ch in result["bags_raised"])


# ─── deletion / reversal paths ──────────────────────────────────────────────

def _mk_import_run_with_sale(items, run_label):
    """Simulate an Ezi POS import run: pos_imports row + ezi_pos_imports
    marker + sale + sale_items. Returns (run_id, sale_id)."""
    sale_id = _mk_sale(items)
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO pos_imports(source_name, filename, file_format, "
            "row_count, sale_count, status) VALUES('ezi','BU.zip','dbf',1,1,'imported')")
        run_id = cur.lastrowid
        c.execute(
            "INSERT INTO ezi_pos_imports(unqcode, import_date, sale_id, "
            "amount, source, import_run_id) VALUES(?,?,?,?, 'ezi_pos', ?)",
            (f"U-{run_label}", "2026-08-10 10:00:00", sale_id, 100.0, run_id))
    return run_id, sale_id


def test_delete_import_run_skips_bag_stock_reversal(tmp_db_path):
    from app.pos_import_sync import delete_pos_import
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    run_id, sale_id = _mk_import_run_with_sale([(bag, 6), (normal, 3)], "run1")
    # state as it would be after the import: bag = sold (6), normal = -3
    with db.conn() as c:
        _save_state(c, bag, 6.0, 0.0, 0.0)
        _save_state(c, normal, -3.0, 0.0, 0.0)

    res = delete_pos_import(run_id)
    assert res["ok"] is True
    # bag stock NOT reversed (import never decremented it):
    assert _stock(bag) == 6.0
    # normal stock IS reversed (+3 back):
    assert _stock(normal) == 0.0


def test_reverse_imported_bag_sale_skips_stock_and_pos_bag_sale_too(tmp_db_path):
    """v8.18.17 unified model: NO sale origin decrements bag stock, so NO
    origin re-adds it on reversal either (imported or built-in POS)."""
    from app.routers.pos import _reverse_sale_core
    bag = _mk_category("Bag Rs 20", "BAG20")
    # imported bag sale (has the ezi_pos_imports marker)
    _, imported_sale = _mk_import_run_with_sale([(bag, 4)], "run2")
    # built-in POS bag sale (no marker)
    pos_sale = _mk_sale([(bag, 2)])
    with db.conn() as c:
        _save_state(c, bag, 10.0, 0.0, 0.0)

    with db.write_tx() as c:
        _reverse_sale_core(imported_sale, c, reason="test imported refund")
    assert _stock(bag) == 10.0, "imported bag sale refund must not bump stock"

    with db.write_tx() as c:
        _reverse_sale_core(pos_sale, c, reason="test POS refund")
    assert _stock(bag) == 10.0, "v8.18.17: POS bag sale refund must not bump stock either (POS bag sales never decremented)"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
