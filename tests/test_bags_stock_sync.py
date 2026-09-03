"""v8.19.1 → v8.18.18: Bags categories — on-hand model.

Business rule (user-defined): shopping-bag categories ("Bag Rs 20", "Bags",
code BAG…) never have purchase bills entered in BillBook — bags are bought
as EXPENSES. So on POS import (and on every stock-state rebuild):

    virtual purchased = max(purchases + adjustments, sold)   (display side)
    on-hand (state)   = max(purchases + adjustments − sold, 0)

Covered here:
  1. sync_bags_stock_to_sold: derives on-hand (heals legacy negative AND
     legacy v8.18.17 phantom states that stored the purchased total)
  2. a real bill's surplus survives the sync (purchased > sold untouched)
  3. refunded sales don't count towards "sold"
  4. rebuild_stock_state applies the rule (dirty-flag boot rebuild too)
  5. deleting an import run recomputes bag on-hand (sales gone → sold drops)
  6. refunding ANY bag sale (imported or built-in POS) recomputes on-hand:
     no bills → stays 0 (no bump); real bills → the bag returns to the shelf
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


def _mk_bill(cat, qty, price=16.0, bill_date="2026-07-01"):
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO bills(supplier_name, status, payment_status, bill_date) "
            "VALUES('Bag Supplier', 'confirmed', 'paid', ?)", (bill_date,))
        bid = cur.lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, category_id, price, qty, "
            "line_total) VALUES(?,?,?,?,?,?)",
            (bid, "bags", cat, price, qty, price * qty))
        return bid


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

def test_sync_heals_legacy_negative_stock(tmp_db_path):
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_sale([(bag, 5)])
    _mk_sale([(bag, 7)])                       # total sold = 12, no bills
    with db.conn() as c:
        _save_state(c, bag, -12.0, 0.0, 0.0)   # legacy negative stock
    changed = sync_bags_stock_to_sold()
    assert _stock(bag) == 0.0, "no bills → on-hand 0 (was -12)"
    assert any(ch["category_id"] == bag for ch in changed)


def test_sync_heals_v8_18_17_phantom_purchased_total(tmp_db_path):
    """v8.18.17 stored the virtual purchased total in the qty slot (the
    user's '342 bags in stock with no bags bill' complaint) — the sync
    heals it to the on-hand number 0."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_sale([(bag, 342)])                     # sold 342, no bills
    with db.conn() as c:
        _save_state(c, bag, 342.0, 0.0, 0.0)   # v8.18.17 phantom state
    changed = sync_bags_stock_to_sold()
    assert _stock(bag) == 0.0
    assert changed and changed[0]["from_qty"] == 342.0 and changed[0]["to_qty"] == 0.0


def test_sync_keeps_real_bill_surplus(tmp_db_path):
    """A real bag bill ahead of sold: purchased is NOT increased and the
    on-hand surplus stays (the user's 'purchased > sold → don't increase')."""
    bag = _mk_category("Bag Rs 30", "BAG30")
    _mk_bill(bag, 100)                         # real purchase of 100
    _mk_sale([(bag, 4)])                       # sold = 4
    with db.conn() as c:
        _save_state(c, bag, 96.0, 96.0 * 16.0, 16.0)   # already correct
    changed = sync_bags_stock_to_sold()
    assert _stock(bag) == 96.0, "on-hand keeps the 100 − 4 surplus"
    assert not any(ch["category_id"] == bag for ch in changed), (
        "state already correct — nothing to change")


def test_sync_excludes_refunded_sales(tmp_db_path):
    bag = _mk_category("Bag Rs 10", "BAG10")
    _mk_bill(bag, 20)
    _mk_sale([(bag, 5)])                       # active
    _mk_sale([(bag, 7)], payment_status="refunded")   # refunded — not sold
    with db.conn() as c:
        _save_state(c, bag, 0.0, 0.0, 0.0)
    sync_bags_stock_to_sold()
    assert _stock(bag) == 15.0, "20 bought − 5 valid sold = 15 on-hand"


def test_sync_idempotent(tmp_db_path):
    bag = _mk_category("Bag Rs 50", "BAG50")
    _mk_sale([(bag, 9)])
    sync_bags_stock_to_sold()
    assert _stock(bag) == 0.0
    changed = sync_bags_stock_to_sold()        # second run: nothing to do
    assert not any(ch["category_id"] == bag for ch in changed)
    assert _stock(bag) == 0.0


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
    # legacy phantom state, as a v8.18.17 install would have it
    with db.conn() as c:
        _save_state(c, bag, 15.0, 0.0, 0.0)

    result = rebuild_stock_state()
    assert _stock(bag) == 0.0, "bag on-hand must be 0 (no bills), not -15 or +15"
    assert _stock(normal) == 80.0
    # the rebuild wiped the phantom bag state itself (replay: purchases only
    # → 0), so the post-replay sync had nothing left to change — that's
    # correct; bags_raised only reports cats the sync actually moved.
    assert "bags_raised" in result


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


def test_delete_import_run_resyncs_bag_on_hand(tmp_db_path):
    from app.pos_import_sync import delete_pos_import
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    run_id, sale_id = _mk_import_run_with_sale([(bag, 6), (normal, 3)], "run1")
    # state as it would be after the import: bag phantom 6, normal -3
    with db.conn() as c:
        _save_state(c, bag, 6.0, 0.0, 0.0)
        _save_state(c, normal, -3.0, 0.0, 0.0)

    res = delete_pos_import(run_id)
    assert res["ok"] is True
    # bag on-hand recomputed after the run's sales are deleted: sold 6 → 0
    assert _stock(bag) == 0.0
    # normal stock IS reversed (+3 back):
    assert _stock(normal) == 0.0


def test_delete_import_run_bag_with_real_bill_returns_to_shelf(tmp_db_path):
    from app.pos_import_sync import delete_pos_import
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 100)
    run_id, sale_id = _mk_import_run_with_sale([(bag, 40)], "runB")
    sync_bags_stock_to_sold()
    assert _stock(bag) == 60.0                # 100 bought − 40 sold

    res = delete_pos_import(run_id)
    assert res["ok"] is True
    assert _stock(bag) == 100.0, (
        "deleting the import run removes the 40 bag sales → all 100 back")


def test_reverse_imported_bag_sale_and_pos_bag_sale_recompute(tmp_db_path):
    """v8.18.18: reversing a bag sale (imported OR built-in POS) never
    re-adds the qty directly — the scoped sync recomputes on-hand from the
    tables: no bills → 0; real bills → the bag goes back on the shelf."""
    from app.routers.pos import _reverse_sale_core
    bag = _mk_category("Bag Rs 20", "BAG20")
    # imported bag sale (has the ezi_pos_imports marker)
    _, imported_sale = _mk_import_run_with_sale([(bag, 4)], "run2")
    # built-in POS bag sale (no marker)
    pos_sale = _mk_sale([(bag, 2)])
    with db.conn() as c:
        _save_state(c, bag, 10.0, 0.0, 0.0)   # legacy phantom

    with db.write_tx() as c:
        _reverse_sale_core(imported_sale, c, reason="test imported refund")
    assert _stock(bag) == 0.0, (
        "no bills → on-hand 0 (sold dropped to 2; phantom 10 healed)")

    with db.write_tx() as c:
        _reverse_sale_core(pos_sale, c, reason="test POS refund")
    assert _stock(bag) == 0.0, "still no bills → still 0 (no bump)"


def test_reverse_bag_sale_with_real_bill_returns_to_shelf(tmp_db_path):
    from app.routers.pos import _reverse_sale_core
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 50)
    sale = _mk_sale([(bag, 30)])
    sync_bags_stock_to_sold()
    assert _stock(bag) == 20.0                # 50 bought − 30 sold

    with db.write_tx() as c:
        _reverse_sale_core(sale, c, reason="test refund")
    assert _stock(bag) == 50.0, (
        "refunded bag sale stops counting as sold → 50 back on the shelf")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
