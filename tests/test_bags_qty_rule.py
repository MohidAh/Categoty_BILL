"""v8.18.18 — Bags QTY rule: the user's exact spec, end to end.

USER SPEC (verbatim intent):
    "when we import from POS and our system has bags QTY (300), and when we
    are importing from POS we see the qty SOLD is greater than purchased,
    then we increase the purchased QTY so every time our purchased QTY is
    equal to SOLD. And if purchased QTY is greater than sold, then don't
    increase it."
    …and (v8.18.18 follow-up): "when I haven't added a bags bill it should
    be 0 — I haven't purchased any bags."

Model on EVERY path (Ezi import, generic CSV import, built-in POS sale,
full rebuild, scoped replay, refund, void, import-delete):

    virtual purchased = max(purchases + adjustments, sold)   (display side)
    on-hand qty       = max(purchases + adjustments − sold, 0)  (state side)

    → no bags bill (the normal case): purchased = SOLD (raised, per the
      rule) and the Current Stock page shows Qty 0 (nothing on the shelf).
    → real bag bill of 500 with 300 sold: purchased stays 500 (NOT
      increased — "don't increase") and on-hand = 200 (the surplus).

Tests below use the user's exact numbers (300 / 450 / 500 / 300) through the
REAL Ezi DBF import pipeline (FakeDBF patch, same pattern as test_pos_import.py),
the REAL built-in POS sale creation (FastAPI TestClient), the REAL refund
endpoint, the REAL rebuild, and the REAL /api/inventory + daily-stock report.
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


def _mk_bill(cat, qty, price=16.0, bill_date="2026-07-01"):
    """A real purchase bill for a category. Returns bill_item id."""
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


def _inv_row(cid):
    from app.shop import get_inventory
    for row in get_inventory():
        if row["category_id"] == cid:
            return row
    return None


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

def test_users_exact_scenario_300_raised_to_450_no_bill(tmp_db_path, ezi_patch):
    """System has bags QTY 300 (v8.18.17-style phantom state — no bags bill);
    import brings total sold to 450.

    Per the rule: purchased QTY is raised so purchased == SOLD (450 — the
    Purchased column shows 450), and the Current Stock page shows the
    ON-HAND number: 0. No bags bill was ever entered, so there is nothing
    on the shelf. The phantom 300 in the qty slot is healed to 0."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)                    # system's current bag QTY: 300
    ezi_patch.reset([("BG001", 150, "2026-09-01"),
                     ("BG002", 300, "2026-09-02")])   # +450 sold this import
    result = _run_ezi_import("300to450")
    assert result["imported_sales"] == 2
    assert _stock(bag) == 0.0, (
        "no bags bill → on-hand qty must be 0 (sold 450 > purchased 0; the "
        "virtual purchased total is raised to 450 at display time)")
    # the heal is reported back to the caller (300 → 0)
    assert any(ch["category_id"] == bag and ch["from_qty"] == 300.0
               and ch["to_qty"] == 0.0
               for ch in result["bags_stock_synced"])
    # display side: the user's rule — purchased QTY equal to SOLD
    row = _inv_row(bag)
    assert row["purchased"] == 450, "purchased must be raised to equal SOLD"
    assert row["sold"] == 450
    assert row["stock"] == 0


def test_users_exact_scenario_bill_300_sold_450(tmp_db_path, ezi_patch):
    """Same scenario, but the 300 is a REAL bill: purchased 300, import
    brings sold to 450 → purchased display raised to 450, on-hand 0
    (300 real bags + the rest auto-raised — all given away)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 300)
    _set_stock(bag, 300.0, avg=16.0)
    ezi_patch.reset([("BG003", 150, "2026-09-01"),
                     ("BG004", 300, "2026-09-02")])
    _run_ezi_import("bill300")
    assert _stock(bag) == 0.0, "on-hand = max(300 − 450, 0) = 0"
    row = _inv_row(bag)
    assert row["purchased"] == 450, "purchased display raised to SOLD (450)"
    assert row["stock"] == 0


def test_users_exact_scenario_500_above_sold_300_untouched(tmp_db_path, ezi_patch):
    """Real bag bill of 500; import shows sold 300 → purchased is NOT
    increased (stays 500) and on-hand keeps the purchased-surplus (200)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 500)
    _set_stock(bag, 500.0, avg=16.0)
    ezi_patch.reset([("BG010", 300, "2026-09-01")])   # sold 300 < 500
    result = _run_ezi_import("500over300")
    assert _stock(bag) == 200.0, (
        "purchased (500) > sold (300) → on-hand keeps the surplus 500−300")
    row = _inv_row(bag)
    assert row["purchased"] == 500, "purchased must NOT be increased past the real bill"
    assert row["stock"] == 200


def test_import_repeated_stays_zero_without_bills(tmp_db_path, ezi_patch):
    """Cumulative re-imports: sold rises (100 → 150) but with no bags bill
    the on-hand stays 0 and the purchased display follows sold."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 0.0)
    ezi_patch.reset([("BG020", 100, "2026-09-01")])
    _run_ezi_import("raise1")
    assert _stock(bag) == 0.0
    assert _inv_row(bag)["purchased"] == 100
    ezi_patch.reset([("BG020", 100, "2026-09-01"),
                     ("BG021", 50, "2026-09-02")])
    r2 = _run_ezi_import("raise2")            # cumulative backup: +1 new sale
    assert r2["imported_sales"] == 1
    assert _stock(bag) == 0.0, "still no bags bill → still 0 on-hand"
    assert _inv_row(bag)["purchased"] == 150


# ─── rebuild paths compute the same number ─────────────────────────────────

def test_rebuild_with_bill_500_sold_300_lands_on_200(tmp_db_path):
    """A bag category with a purchase bill of 500 and 300 sold: the full
    rebuild must land on the on-hand number 200 (the v8.18.17 bug kept 500
    in the qty slot — purchased total, not on-hand)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 500)
    _mk_sale([(bag, 300)], created_at="2026-08-05 10:00:00")
    _set_stock(bag, 500.0, avg=16.0)

    rebuild_stock_state()
    assert _stock(bag) == 200.0, (
        "rebuild: on-hand = purchased 500 − sold 300 = 200")
    assert _inv_row(bag)["purchased"] == 500


def test_rebuild_no_bills_sold_450_lands_on_0(tmp_db_path):
    """No bag bills (bags bought as expenses — the normal case): rebuild
    leaves the bag at 0 on-hand (not 450, not −450)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_sale([(bag, 200)], created_at="2026-08-01 10:00:00")
    _mk_sale([(bag, 250)], created_at="2026-08-02 10:00:00")
    _set_stock(bag, 200.0)
    rebuild_stock_state()
    assert _stock(bag) == 0.0


def test_rebuild_preserves_imported_bag_cost_price(tmp_db_path):
    """v8.18.17 side-fix kept: the full rebuild must not overwrite imported
    bag sale_items.cost_price with the bag pool avg (0 when no bills) — the
    real INVTRANS.COST COGS stays. Skipping bag sale events preserves it."""
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
    """Deleting a bag bill: purchases drop to 0 → on-hand lands on 0 (sold
    300 with nothing purchased); non-bag categories replay normally."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    bid = _mk_bill(bag, 500)
    with db.conn() as c:
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

    # delete the bag bill → purchased drops to 0 → on-hand must be 0
    with db.write_tx() as c:
        c.execute("UPDATE bills SET deleted_at=? WHERE id=?",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), bid))
        rebuild_categories_state([bag, normal], c=c)

    assert _stock(bag) == 0.0, (
        "bag bill deleted → purchased 0 < sold 300 → on-hand 0")
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


def test_pos_bag_sale_no_bill_stays_zero_and_never_negative(client):
    """Selling bags from the built-in POS with no bags bill: on-hand stays 0
    forever (never negative — the old model could go below zero)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 0.0)

    assert _pos_sale(client, bag, 10).status_code == 200
    assert _pos_sale(client, bag, 150).status_code == 200
    assert _stock(bag) == 0.0
    assert _inv_row(bag)["purchased"] == 160, "purchased display follows sold"


def test_pos_bag_sale_with_bill_eats_into_surplus(client):
    """Real bag bill of 500: each POS bag sale reduces the on-hand surplus
    via the scoped sync (500 → 490 → 475)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 500)
    _set_stock(bag, 500.0, avg=16.0)

    assert _pos_sale(client, bag, 10).status_code == 200
    assert _stock(bag) == 490.0
    assert _pos_sale(client, bag, 15).status_code == 200
    assert _stock(bag) == 475.0
    assert _inv_row(bag)["purchased"] == 500, "bill ahead of sold → not raised"


def test_pos_bag_sale_not_blocked_by_strict_stock_guard(client):
    """v8.18.18: the strict stock strategy must NEVER block a bag sale —
    bag on-hand is intentionally 0/derived (auto-managed category)."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 0.0)                      # on-hand 0
    db.set_setting("stock_strategy", "strict")
    try:
        r = _pos_sale(client, bag, 5)
        assert r.status_code == 200, (
            f"strict guard must not block bag sales: {r.status_code} {r.text}")
    finally:
        db.set_setting("stock_strategy", "strict")
    # a normal category IS still guarded (guard itself still works)
    normal = _mk_category("Item 250", "A")
    _set_stock(normal, 0.0)
    r = client.post("/api/sales", json={
        "items": [{"category_id": normal, "item_name": "item",
                   "qty": 1, "sell_price": 250.0}],
        "payment_method": "cash"})
    assert r.status_code == 409, "guard still active for normal categories"


def test_refund_of_pos_bag_sale(client):
    """Refunding a bag sale: with no bills, on-hand stays 0 (no bump);
    with a real bill the returned bag goes back on the shelf."""
    # (a) no bags bill — nothing to bump
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 0.0)
    db.set_setting("require_pin_for_refund", "false")
    r = _pos_sale(client, bag, 40)
    sale_id = r.json()["id"]
    assert _stock(bag) == 0.0

    rr = client.post(f"/api/sales/{sale_id}/refund", json={"reason": "test"})
    assert rr.status_code == 200, rr.text
    assert _stock(bag) == 0.0, "no bills → refund must not bump bag stock"

    # (b) real bill — the refunded bags return to the shelf
    bag2 = _mk_category("Bag Rs 30", "BAG30")
    _mk_bill(bag2, 500)
    _set_stock(bag2, 500.0, avg=16.0)
    r2 = _pos_sale(client, bag2, 40)
    sale_id2 = r2.json()["id"]
    assert _stock(bag2) == 460.0
    rr2 = client.post(f"/api/sales/{sale_id2}/refund", json={"reason": "test"})
    assert rr2.status_code == 200, rr2.text
    assert _stock(bag2) == 500.0, "refunded bags go back on the shelf"


# ─── generic CSV import path ────────────────────────────────────────────────

def test_generic_csv_import_applies_bags_rule(tmp_db_path):
    """The generic CSV import (legacy path): bag categories get the
    end-of-import sync — on-hand 0 with no bags bill, purchased display
    raised to sold."""
    from app import pos_import
    bag = _mk_category("Bag Rs 20", "BAG20")
    _set_stock(bag, 300.0)                    # legacy phantom state

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
    assert _stock(bag) == 0.0, (
        "CSV import: no bags bill → on-hand 0 (phantom 300 healed)")
    assert _inv_row(bag)["purchased"] == 450


# ─── daily stock report never shows bags negative ───────────────────────────

def test_daily_stock_report_bags_never_negative(tmp_db_path):
    """The 11-column daily stock report: bag rows are clamped (never
    negative), purchased_qty shows the virtual raise so the row balances,
    and closing is 0 when no bags bill exists."""
    from app.profit_analytics import get_daily_stock_report
    bag = _mk_category("Bag Rs 20", "BAG20")
    # 12 bags sold on 2026-09-01, none before, no bills
    _mk_sale([(bag, 12)], created_at="2026-09-01 10:00:00")

    rep = get_daily_stock_report("2026-09-01")
    row = next(r for r in rep["rows"] if r["category_id"] == bag)
    assert row["opening_qty"] == 0
    assert row["purchased_qty"] == 12, "purchased shows the virtual raise (= sold)"
    assert row["sold_qty"] == 12
    assert row["closing_qty"] == 0, "no bills → closing 0, never negative"


def test_daily_stock_report_bags_with_real_bill(tmp_db_path):
    """Real bill of 500 (before the report date), 300 sold before the date:
    opening 200, and the day's sales just reduce it."""
    from app.profit_analytics import get_daily_stock_report
    bag = _mk_category("Bag Rs 20", "BAG20")
    _mk_bill(bag, 500, bill_date="2026-07-01")
    _mk_sale([(bag, 300)], created_at="2026-08-05 10:00:00")
    _mk_sale([(bag, 5)], created_at="2026-09-01 10:00:00")

    rep = get_daily_stock_report("2026-09-01")
    row = next(r for r in rep["rows"] if r["category_id"] == bag)
    assert row["opening_qty"] == 200, "500 bought − 300 sold = 200 opening"
    assert row["purchased_qty"] == 0, "bill ahead of sold → no raise today"
    assert row["sold_qty"] == 5
    assert row["closing_qty"] == 195


# ─── /api/inventory presentation of the rule ────────────────────────────────

def test_inventory_bag_row_flags_and_columns(tmp_db_path):
    """Bag rows: purchased == sold (no bills), stock 0, and NO low/out
    alerts — bags are auto-managed, not restock items."""
    bag = _mk_category("Bag Rs 20", "BAG20")
    normal = _mk_category("Item 250", "A")
    _mk_bill(normal, 5, price=10.0)            # normal cat: 5 on hand → low
    _mk_sale([(bag, 7)], created_at="2026-08-01 10:00:00")
    rebuild_stock_state()                       # builds normal's state row

    brow = _inv_row(bag)
    assert brow["purchased"] == 7 and brow["sold"] == 7
    assert brow["stock"] == 0
    assert brow["auto_managed_stock"] is True
    assert brow["low_stock"] is False, "bags never raise low-stock alerts"
    assert brow["out_of_stock"] is False, "bags never raise out-of-stock alerts"

    nrow = _inv_row(normal)
    assert nrow["stock"] == 5
    assert nrow["auto_managed_stock"] is False
    assert nrow["low_stock"] is True, "normal low-stock alert still works"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
