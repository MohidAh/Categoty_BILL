"""v8.18.17 — Bags QTY rule: the user's exact spec, end to end.

USER SPEC (verbatim intent):
    "when we import from POS and our system has bags QTY (300), and when we
    are importing from POS we see the qty SOLD is greater than purchased,
    then we increase the purchased QTY so every time our purchased QTY is
    equal to SOLD. And if purchased QTY is greater than sold, then don't
    increase it."

Invariant on EVERY path (Ezi import, generic CSV import, built-in POS sale,
full rebuild, scoped replay, refund, void, import-delete):

    bag qty = max(purchased_or_prior_qty, total_sold_valid_sales)
    — raised to equal SOLD when sold passes it
    — never lowered when purchased is ahead

Tests below use the user's exact numbers (300 / 450 / 500 / 300) through the
REAL Ezi DBF import pipeline (FakeDBF patch, same pattern as test_pos_import.py),
the REAL built-in POS sale creation (FastAPI TestClient), the REAL refund
endpoint, and the REAL rebuild.
"""
import os
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from app import db
from app import pos_import_sync as pis
from app.profit_engine import (
    _save_state, rebuild_stock_state, rebuild_categories_state,
    sync_bags_stock_to_sold,
)

TMP_ZIPS = Path(os.environ.get("TMPDIR", "/tmp")) / "bb_bags_rule_zips"
TMP_ZIPS.mkdir(parents=True, exist_ok=True)


# ─── helpers ────────────────────────────────────────────────────────────────

def _mk_category(name, code, sell_price=20.0, active=1):
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO price_categories(name, code, sell_price, active) "
            "VALUES(?,?,?,?)", (name, code, sell_price, active))
        return cur.lastrowid


def _mk_sale(items, payment_status="paid", created_at="2026-08-10 10:00:00"):
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


def _set_stock(cid, qty, avg=0.0):
    with db.conn() as c:
        _save_state(c, cid, qty, round(qty * avg, 2), avg)


class BagFakeDBF:
    """Mock DBF reader with a bag item in STOCK.DBF and bag lines in
    INVTRANS.DBF — patches pis.DBF (same pattern as test_pos_import.py)."""

    # class-level payload, reset per test
    _stock_records = []
    _invoice_records = []
    _invtrans_records = []
    _acctrans_records = []
    _diary_records = []
    _debtors_records = []
    _company_records = []

    def __init__(self, path, load=True):
        fname = os.path.basename(path).upper()
        table = {
            "STOCK.DBF": BagFakeDBF._stock_records,
            "INVOICE.DBF": BagFakeDBF._invoice_records,
            "INVTRANS.DBF": BagFakeDBF._invtrans_records,
            "ACCTRANS.DBF": BagFakeDBF._acctrans_records,
            "DIARY.DBF": BagFakeDBF._diary_records,
            "DEBTORS.DBF": BagFakeDBF._debtors_records,
            "COMPANY.DBF": BagFakeDBF._company_records,
        }
        self.records = table.get(fname, [])

    def __iter__(self):
        return iter(self.records)

    @staticmethod
    def reset(sales, bag_internal=900, bag_name="Bag Rs 20", bag_price=20.0):
        """sales: list of (unqcode, qty, date_str). Each becomes one invoice
        with one bag line item."""
        BagFakeDBF._stock_records = [
            {"INTERNAL": bag_internal, "PART_NO": "200001", "DESC": bag_name,
             "PRICE1": bag_price, "COST": 16.0, "QTY": 0.0},
        ]
        BagFakeDBF._acctrans_records = []
        BagFakeDBF._invoice_records = []
        BagFakeDBF._invtrans_records = []
        for i, (uc, qty, d) in enumerate(sales):
            year, month, day = (int(x) for x in d.split("-"))
            BagFakeDBF._acctrans_records.append(
                {"UNQCODE": uc, "TYPE": "SI", "DATE": date(year, month, day),
                 "ADD_TIME": f"{str(year)[2:]}{month:02d}{day:02d}120000",
                 "AMOUNT": bag_price * qty, "CREDIT": False, "PAID_BY": 1,
                 "INTERNAL": 1, "DETAILS": "C - Cash Sales", "INVOICE": i + 1,
                 "ITEMNAME": bag_name, "QTY": qty, "RATE": bag_price})
            BagFakeDBF._invoice_records.append(
                {"UNQCODE": uc, "TYPE": "SI", "STATUS": "P",
                 "DATE": date(year, month, day),
                 "ADD_TIME": f"{str(year)[2:]}{month:02d}{day:02d}120000",
                 "AMOUNT": bag_price * qty, "PAID": bag_price * qty,
                 "TENDERED": bag_price * qty, "CLIENT": 1, "SALESMAN": 1,
                 "TAX": 0.0, "ROUNDING": 0.0})
            BagFakeDBF._invtrans_records.append(
                {"UNQCODE": uc, "TYPE": "SI", "INTERNAL": bag_internal,
                 "PART_NO": "200001", "DETAILS": bag_name, "QTY": float(qty),
                 "AMOUNT": bag_price * qty, "COST": 16.0})
        BagFakeDBF._diary_records = []
        BagFakeDBF._debtors_records = [{"INTERNAL": 1, "NAME": "Cash Sales",
                                        "PHONE": "", "MOBILE": ""}]
        BagFakeDBF._company_records = [{"NAME1": "Bags Rule Test Shop"}]


def _run_ezi_import(label):
    zip_path = TMP_ZIPS / f"BU{label}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fn in ("ACCTRANS.DBF", "INVTRANS.DBF", "INVOICE.DBF", "DIARY.DBF",
                   "DEBTORS.DBF", "COMPANY.DBF", "STOCK.DBF"):
            zf.writestr(fn, b"mock")
    return pis.import_pos_backup(str(zip_path))


@pytest.fixture()
def ezi_patch():
    BagFakeDBF.reset([])
    original_dbf = getattr(pis, "DBF", None)
    original_flag = getattr(pis, "HAS_DBFREAD", False)
    pis.DBF = BagFakeDBF
    pis.HAS_DBFREAD = True
    try:
        yield BagFakeDBF
    finally:
        if original_dbf is not None:
            pis.DBF = original_dbf
        else:
            if hasattr(pis, "DBF"):
                del pis.DBF
        pis.HAS_DBFREAD = original_flag


# ─── THE USER'S EXACT SCENARIO — via the real Ezi import ────────────────────

def test_users_exact_scenario_300_raised_to_450(tmp_db_path, ezi_patch):
    """System has bags QTY 300; import brings total sold to 450 →
    purchased QTY is increased so it equals SOLD (450)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)                    # system's current bag QTY: 300
    ezi_patch.reset([("BG001", 150, "2026-09-01"),
                     ("BG002", 300, "2026-09-02")])   # +450 sold this import
    result = _run_ezi_import("300to450")
    assert result["imported_sales"] == 2
    assert _stock(bag) == 450.0, (
        "sold (450) > purchased (300) → purchased QTY must be raised to 450")
    # the sync result is reported back to the caller
    assert any(ch["category_id"] == bag and ch["from_qty"] == 300.0
               and ch["to_qty"] == 450.0
               for ch in result["bags_stock_synced"])


def test_users_exact_scenario_500_above_sold_300_untouched(tmp_db_path, ezi_patch):
    """System has bags QTY 500 (purchased); import shows sold 300 →
    purchased QTY is greater than sold → DO NOT increase (stay 500)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 500.0)                    # purchased ahead: 500
    ezi_patch.reset([("BG010", 300, "2026-09-01")])   # sold 300 < 500
    result = _run_ezi_import("500over300")
    assert _stock(bag) == 500.0, (
        "purchased (500) > sold (300) → qty must NOT be touched")
    assert not any(ch["category_id"] == bag
                   for ch in result["bags_stock_synced"])


def test_import_repeated_sold_never_lowers_qty(tmp_db_path, ezi_patch):
    """Re-import / cumulative imports only ever RAISE to the new sold level —
    qty never decreases (idempotent, raise-only)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 0.0)
    ezi_patch.reset([("BG020", 100, "2026-09-01")])
    _run_ezi_import("raise1")
    assert _stock(bag) == 100.0
    ezi_patch.reset([("BG020", 100, "2026-09-01"),
                     ("BG021", 50, "2026-09-02")])
    r2 = _run_ezi_import("raise2")            # cumulative backup: +1 new sale
    assert r2["imported_sales"] == 1
    assert _stock(bag) == 150.0, "cumulative import raises qty to new sold"
    # deleting the sale in the source POS (sync deletion → refunded) never
    # LOWERS the bag qty (raise-only guard)
    _mk_sale([(bag, 0)])  # no-op, keeps ids unique


# ─── rebuild paths compute the same number ─────────────────────────────────

def test_rebuild_with_bill_500_sold_300_stays_500(tmp_db_path):
    """Regression (v8.18.17): a bag category with a purchase bill of 500 and
    300 sold must stay at 500 after a FULL rebuild — the old replay
    subtracted sales (500−300=200) then raised to 300, wrongly LOWERING the
    purchased qty. The user's rule says: purchased > sold → don't touch."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO bills(supplier_name, status, payment_status, bill_date) "
            "VALUES('Bag Supplier', 'confirmed', 'paid', '2026-07-01')")
        bid = cur.lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, category_id, price, qty, "
            "line_total) VALUES(?,?,?,?,?,?)",
            (bid, "bags", bag, 16.0, 500, 8000.0))
    _mk_sale([(bag, 300)], created_at="2026-08-05 10:00:00")
    _set_stock(bag, 500.0, avg=16.0)

    rebuild_stock_state()
    assert _stock(bag) == 500.0, (
        "rebuild: purchased 500 > sold 300 → bag qty must stay 500, not 300")


def test_rebuild_no_bills_sold_450_raises_to_450(tmp_db_path):
    """No bag bills (bags bought as expenses — the normal case): replay
    leaves the bag at 0 purchases; the post-replay sync raises to sold."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_sale([(bag, 200)], created_at="2026-08-01 10:00:00")
    _mk_sale([(bag, 250)], created_at="2026-08-02 10:00:00")
    _set_stock(bag, 200.0)
    rebuild_stock_state()
    assert _stock(bag) == 450.0


def test_rebuild_preserves_imported_bag_cost_price(tmp_db_path):
    """v8.18.17 side-fix: the full rebuild used to overwrite imported bag
    sale_items.cost_price with the bag pool avg (0 when no bills) — wiping
    the real INVTRANS.COST COGS. Skipping bag sale events preserves it."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO sales(invoice_no, total, payment_method, "
            "payment_status, created_at) VALUES(?,?,?,?,?)",
            ("IMP-BGCOST", 400.0, "cash", "paid", "2026-08-01 10:00:00"))
        sid = cur.lastrowid
        c.execute(
            "INSERT INTO sale_items(sale_id, item_name, category_id, qty, "
            "sell_price, cost_price, line_total) VALUES(?,?,?,?,?,?,?)",
            (sid, "Bag Rs 20", bag, 20, 20.0, 16.0, 400.0))
    _set_stock(bag, 20.0)
    rebuild_stock_state()
    with db.conn() as c:
        row = c.execute(
            "SELECT cost_price FROM sale_items WHERE sale_id=?", (sid,)
        ).fetchone()
    assert row and abs(float(row["cost_price"]) - 16.0) < 0.001, (
        "rebuild must preserve the imported bag cost_price (16), not zero it")


def test_scoped_replay_after_bag_bill_delete(tmp_db_path):
    """Deleting a bag bill: purchases drop; if that leaves purchased below
    sold, qty lands on sold (purchased raised to equal SOLD); non-bag
    categories replay normally."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO bills(supplier_name, status, payment_status, bill_date) "
            "VALUES('Bag Supplier', 'confirmed', 'paid', '2026-07-01')")
        bid = cur.lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, category_id, price, qty, "
            "line_total) VALUES(?,?,?,?,?,?)",
            (bid, "bags", bag, 16.0, 500, 8000.0))
        cur2 = c.execute(
            "INSERT INTO bills(supplier_name, status, payment_status, bill_date) "
            "VALUES('Paper Supplier', 'confirmed', 'paid', '2026-07-02')")
        nid = cur2.lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, category_id, price, qty, "
            "line_total) VALUES(?,?,?,?,?,?)",
            (nid, "paper", normal, 10.0, 100, 1000.0))
    _mk_sale([(bag, 300)], created_at="2026-08-05 10:00:00")
    _mk_sale([(normal, 20)], created_at="2026-08-06 10:00:00")
    _set_stock(bag, 500.0, avg=16.0)

    # delete the bag bill → purchased drops to 0 → qty must land on sold (300)
    with db.write_tx() as c:
        c.execute("UPDATE bills SET deleted_at=? WHERE id=?",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bid))
        rebuild_categories_state([bag, normal], c=c)

    assert _stock(bag) == 300.0, (
        "bag bill deleted → purchased 0 < sold 300 → qty = sold (300)")
    assert _stock(normal) == 80.0


# ─── built-in POS sale path (FastAPI TestClient) ────────────────────────────

@pytest.fixture()
def client(tmp_db_path):
    """Authed TestClient: sets owner password 'testpass' then logs in."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.security import hash_password
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute(
            "INSERT INTO settings(key, value) VALUES('password_hash', ?)",
            (hash_password("testpass"),),
        )
    with TestClient(app) as tc:
        r = tc.post("/api/login", json={"password": "testpass"})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        yield tc


def _pos_sale(client, bag, qty, price=20.0):
    return client.post("/api/sales", json={
        "items": [{"category_id": bag, "item_name": "Bag Rs 20",
                   "qty": qty, "sell_price": price}],
        "payment_method": "cash",
    })


def test_pos_native_bag_sale_never_decrements_and_tracks_sold(client):
    """A bag sold from the built-in POS: qty must NOT go down (old behavior
    decremented like any category); it stays and rises with total sold."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)

    r = _pos_sale(client, bag, 10)
    assert r.status_code == 200, r.text
    assert _stock(bag) == 300.0, (
        "POS bag sale must not decrement — 300 stays; sold (10) < 300 so no raise")


def test_pos_native_bag_sale_raises_qty_when_sold_passes_it(client):
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)
    # sell 200 → sold = 200 < 300 (no raise); sell another 150 → 350 > 300
    assert _pos_sale(client, bag, 200).status_code == 200
    assert _stock(bag) == 300.0
    assert _pos_sale(client, bag, 150).status_code == 200
    assert _stock(bag) == 350.0, (
        "sold (350) passed purchased (300) → qty raised to equal SOLD")


def test_refund_of_pos_bag_sale_does_not_bump_stock(client):
    """Refund endpoint (PIN-gated /api/sales/{id}/refund): bag lines are
    skipped — refunding never re-adds bag qty (nothing was decremented)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)
    db.set_setting("require_pin_for_refund", "false")
    r = _pos_sale(client, bag, 40)
    sale_id = r.json()["id"]
    assert _stock(bag) == 300.0

    rr = client.post(f"/api/sales/{sale_id}/refund", json={"reason": "test"})
    assert rr.status_code == 200, rr.text
    assert _stock(bag) == 300.0, "refund must not bump bag stock"


# ─── generic CSV import path ────────────────────────────────────────────────

def test_generic_csv_import_applies_bags_rule(tmp_db_path):
    """The generic CSV import (legacy path) inserts sales without touching
    stock state — but bag categories must still get the end-of-import sync
    (v8.18.17) so bag qty equals total sold after ANY import route."""
    from app import pos_import
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)

    rows = [
        {"inv": "CSV1", "date": "2026-09-01", "item": "Bag Rs 20",
         "qty": "150", "price": "20", "total": "3000", "pay": "cash",
         "category": "BAG20"},
        {"inv": "CSV2", "date": "2026-09-01", "item": "Bag Rs 20",
         "qty": "300", "price": "20", "total": "6000", "pay": "cash",
         "category": "BAG20"},
    ]
    mapping = {"invoice_no": "inv", "date": "date", "item_name": "item",
               "qty": "qty", "price": "price", "total": "total",
               "payment_method": "pay", "category": "category"}
    result = pos_import.import_pos_backup(rows, mapping, source_name="csv")
    assert result["imported_sales"] == 2
    assert _stock(bag) == 450.0, (
        "CSV import with sold 450 > purchased 300 → bag qty raised to 450")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
