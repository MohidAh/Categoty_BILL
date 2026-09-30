"""v8.18.23 — POS-import return lines: correct unit price, 'Si' return docs,
data repair, and line-total-based revenue metrics.

WHAT THIS GUARDS (client report: "total sale is different" between screens
and vs the shop POS):
    1. The pre-v8.18.23 importer stored the LINE TOTAL as sell_price on
       every return line (qty<0): `line_total/qty if qty>0 else line_total`.
       Every SUM(sell_price*qty) metric then double-negated the return into
       a POSITIVE sale (client's DB: Margins Total Sales inflated by
       Rs 15,540 vs Profit Analysis).
    2. 'Si' (mixed-case TYPE) documents are POS return/credit notes — the
       POS ledger posts them NEGATIVE. The importer skipped them entirely
       (case-sensitive 'SI' filter), so returns never reduced sales.
    3. A boot-time migration repairs the corrupt rows already in installs.
    4. get_margins / daily report use COALESCE(line_total, sell*qty) so the
       primary KPI agrees with Profit Analysis / P&L on any data.

Run: python -m pytest tests/test_v8_18_23_return_lines.py -v
"""
import sys
import zipfile
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup


# ── FakeDBF: same mock approach as tests/test_pos_import.py ─────────────────

class FakeDBF:
    _acctrans_records = []
    _invoice_records = []
    _invtrans_records = []
    _diary_records = []
    _debtors_records = []
    _company_records = []
    _stock_records = []

    def __init__(self, path, load=True, encoding=None):
        fname = str(path).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if fname == "ACCTRANS.DBF":
            self.records = FakeDBF._acctrans_records
        elif fname == "INVTRANS.DBF":
            self.records = FakeDBF._invtrans_records
        elif fname == "INVOICE.DBF":
            self.records = FakeDBF._invoice_records
        elif fname == "DIARY.DBF":
            self.records = FakeDBF._diary_records
        elif fname == "DEBTORS.DBF":
            self.records = FakeDBF._debtors_records
        elif fname == "COMPANY.DBF":
            self.records = FakeDBF._company_records
        elif fname == "STOCK.DBF":
            self.records = FakeDBF._stock_records
        else:
            self.records = []

    def __iter__(self):
        return iter(self.records)


def _seed():
    """Two sales + one exchange + one posted 'Si' return + one unposted 'Si'."""
    FakeDBF._acctrans_records = [
        {"UNQCODE": "EX001", "TYPE": "SI", "DATE": date(2026, 8, 1),
         "ADD_TIME": "260801143000", "AMOUNT": 500.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales", "INVOICE": 1},
        {"UNQCODE": "EX001", "TYPE": "SP", "DATE": date(2026, 8, 1),
         "ADD_TIME": "260801143000", "AMOUNT": 500.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales"},
        # EXCH: exchange sale (−7 × 250 + 2 × 1000 = net +250), cash
        {"UNQCODE": "EXCH", "TYPE": "SI", "DATE": date(2026, 9, 22),
         "ADD_TIME": "260922193300", "AMOUNT": 250.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales", "INVOICE": 2},
        {"UNQCODE": "EXCH", "TYPE": "SP", "DATE": date(2026, 9, 22),
         "ADD_TIME": "260922193300", "AMOUNT": 250.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales"},
        # RETP: posted return doc — ledger amount NEGATIVE (as in real Ezi data)
        {"UNQCODE": "RETP", "TYPE": "Si", "DATE": date(2026, 9, 13),
         "ADD_TIME": "260913213200", "AMOUNT": -30.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales", "INVOICE": 8},
        {"UNQCODE": "RETP", "TYPE": "SP", "DATE": date(2026, 9, 13),
         "ADD_TIME": "260913213200", "AMOUNT": -30.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales"},
        # RETU: UNPOSTED return doc — must be skipped (ledger draft)
        {"UNQCODE": "RETU", "TYPE": "Si", "DATE": date(2026, 9, 8),
         "ADD_TIME": "260908120000", "AMOUNT": -770.0, "CREDIT": False,
         "PAID_BY": 1, "INTERNAL": 1, "DETAILS": "C - Cash Sales", "INVOICE": 9},
    ]
    FakeDBF._invoice_records = [
        {"UNQCODE": "EX001", "TYPE": "SI", "STATUS": "P",
         "DATE": date(2026, 8, 1), "ADD_TIME": "260801143000",
         "AMOUNT": 500.0, "PAID": 500.0, "TENDERED": 500.0,
         "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
        {"UNQCODE": "EXCH", "TYPE": "SI", "STATUS": "P",
         "DATE": date(2026, 9, 22), "ADD_TIME": "260922193300",
         "AMOUNT": 250.0, "PAID": 250.0, "TENDERED": 250.0,
         "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
        # posted return doc: header amount positive magnitude, STATUS P
        {"UNQCODE": "RETP", "TYPE": "Si", "STATUS": "P",
         "DATE": date(2026, 9, 13), "ADD_TIME": "260913213200",
         "AMOUNT": 30.0, "PAID": 30.0, "TENDERED": 30.0,
         "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
        # unposted return doc: STATUS '' — must be skipped
        {"UNQCODE": "RETU", "TYPE": "Si", "STATUS": "",
         "DATE": date(2026, 9, 8), "ADD_TIME": "260908120000",
         "AMOUNT": 770.0, "PAID": 0.0, "TENDERED": 0.0,
         "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
    ]
    FakeDBF._invtrans_records = [
        {"UNQCODE": "EX001", "TYPE": "SI", "INTERNAL": 606, "PART_NO": "100001",
         "DETAILS": "Item A", "QTY": 1.0, "AMOUNT": 500.0, "COST": 100.0},
        # the exchange: return line (QTY −7) + sale line (QTY 2)
        {"UNQCODE": "EXCH", "TYPE": "SI", "INTERNAL": 606, "PART_NO": "100001",
         "DETAILS": "Item A", "QTY": -7.0, "AMOUNT": -1750.0, "COST": 191.87},
        {"UNQCODE": "EXCH", "TYPE": "SI", "INTERNAL": 607, "PART_NO": "100002",
         "DETAILS": "Item B", "QTY": 2.0, "AMOUNT": 2000.0, "COST": 400.0},
        # posted return doc line: positive magnitudes (doc type carries sign)
        {"UNQCODE": "RETP", "TYPE": "Si", "INTERNAL": 2, "PART_NO": "BAG30",
         "DETAILS": "Bag Rs 30", "QTY": 1.0, "AMOUNT": 30.0, "COST": 30.0},
        # unposted return doc lines (must never be imported)
        {"UNQCODE": "RETU", "TYPE": "Si", "INTERNAL": 606, "PART_NO": "100001",
         "DETAILS": "Item A", "QTY": 1.0, "AMOUNT": 500.0, "COST": 191.87},
        {"UNQCODE": "RETU", "TYPE": "Si", "INTERNAL": 607, "PART_NO": "100002",
         "DETAILS": "Item B", "QTY": 1.0, "AMOUNT": 250.0, "COST": 191.87},
    ]
    FakeDBF._diary_records = []
    FakeDBF._debtors_records = [
        {"INTERNAL": 1, "NAME": "Cash Sales", "PHONE": "", "MOBILE": ""}
    ]
    FakeDBF._company_records = [{"NAME1": "Test Ezi POS Shop"}]
    FakeDBF._stock_records = [
        {"INTERNAL": 606, "PART_NO": "100001", "DESC": "Item A", "PRICE1": 250.0,
         "COST": 191.87, "QTY": 0.0},
        {"INTERNAL": 607, "PART_NO": "100002", "DESC": "Item B", "PRICE1": 1000.0,
         "COST": 400.0, "QTY": 0.0},
    ]


def _run_import(tmp):
    import app.pos_import_sync as pis
    zip_path = Path(tmp) / "BU.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fn in ("ACCTRANS.DBF", "INVTRANS.DBF", "INVOICE.DBF", "DIARY.DBF",
                   "DEBTORS.DBF", "COMPANY.DBF", "STOCK.DBF"):
            zf.writestr(fn, b"mock")
    original_dbf, orig_has = pis.DBF, getattr(pis, "HAS_DBFREAD", True)
    pis.DBF, pis.HAS_DBFREAD = FakeDBF, True
    try:
        return pis.import_pos_backup(str(zip_path))
    finally:
        pis.DBF, pis.HAS_DBFREAD = original_dbf, orig_has


def _prep_db():
    """Fresh DB + one category per item master entry with stock."""
    from app import db, profit_engine as pe
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "cash_drawer", "ezi_pos_imports",
                  "pos_expense_imports", "pos_imports", "activity_log",
                  "category_stock_state", "price_categories"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO price_categories(id, name, code, sell_price, "
                  "color, sort_order, active) VALUES(1,'Item A','A',250,'#3b82f6',1,1)")
        c.execute("INSERT INTO price_categories(id, name, code, sell_price, "
                  "color, sort_order, active) VALUES(2,'Item B','B',1000,'#3b82f6',2,1)")
    pe.apply_purchase_to_state(1, 50, 191.87)   # Item A: 50 @ 191.87
    pe.apply_purchase_to_state(2, 50, 400.0)    # Item B: 50 @ 400


# ─── 1. importer: return lines get the correct unit price ───────────────────

def test_exchange_return_line_unit_price():
    """The exchange's return line must import sell_price=+250 (the unit
    price), NOT −1750 (the line total) — the exact client-DB corruption."""
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        _seed()
        result = _run_import(test_dir)
        assert result["imported_sales"] == 3, result  # EX001, EXCH, RETP
        from app import db
        with db.conn() as c:
            rows = c.execute(
                "SELECT si.item_name, si.sell_price, si.qty, si.line_total "
                "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
                "WHERE s.invoice_no='IMP-EXCH' ORDER BY si.id").fetchall()
        assert len(rows) == 2, rows
        ret, sold = rows[0], rows[1]
        # return line: qty −7, line_total −1750, unit price +250
        assert ret["qty"] == -7 and abs(ret["line_total"] + 1750.0) < 0.01
        assert abs(ret["sell_price"] - 250.0) < 0.01, ret["sell_price"]
        # sale line: qty 2, line_total 2000, unit price 1000
        assert sold["qty"] == 2 and abs(sold["line_total"] - 2000.0) < 0.01
        assert abs(sold["sell_price"] - 1000.0) < 0.01
        with db.conn() as c:
            s = c.execute("SELECT total, payment_status FROM sales "
                          "WHERE invoice_no='IMP-EXCH'").fetchone()
        assert abs(s["total"] - 250.0) < 0.01 and s["payment_status"] == "paid"
    finally:
        cleanup(test_dir)


# ─── 2. importer: posted 'Si' return docs import as negative sales ─────────

def test_posted_return_doc_imports_negative():
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        _seed()
        result = _run_import(test_dir)
        assert result.get("imported_returns") == 1, result
        from app import db
        with db.conn() as c:
            s = c.execute("SELECT total, subtotal, payment_status, tax_amount "
                          "FROM sales WHERE invoice_no='IMP-RETP'").fetchone()
            assert s is not None, "posted return doc must be imported"
        assert abs(s["total"] + 30.0) < 0.01, s["total"]
        assert abs(s["subtotal"] + 30.0) < 0.01
        assert s["payment_status"] == "paid"
        with db.conn() as c:
            it = c.execute(
                "SELECT si.qty, si.line_total, si.sell_price, si.cost_price "
                "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
                "WHERE s.invoice_no='IMP-RETP'").fetchone()
        assert it["qty"] == -1 and abs(it["line_total"] + 30.0) < 0.01
        assert abs(it["sell_price"] - 30.0) < 0.01, it["sell_price"]
        # cash drawer got the negative refund entry
        with db.conn() as c:
            cd = c.execute("SELECT amount FROM cash_drawer WHERE reference_id=?",
                           (c.execute("SELECT id FROM sales WHERE invoice_no='IMP-RETP'"
                                      ).fetchone()["id"],)).fetchone()
        assert cd is not None and abs(cd["amount"] + 30.0) < 0.01, cd
    finally:
        cleanup(test_dir)


def test_unposted_return_doc_skipped_and_not_deduped():
    """Unposted 'Si' drafts are skipped WITHOUT entering dedup — a later
    backup where they're posted imports them then."""
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        _seed()
        _run_import(test_dir)
        from app import db
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM sales WHERE invoice_no='IMP-RETU'"
                          ).fetchone()[0]
            assert n == 0, "unposted return doc must NOT be imported"
            dedup = c.execute("SELECT COUNT(*) FROM ezi_pos_imports "
                              "WHERE unqcode='RETU'").fetchone()[0]
            assert dedup == 0, "skipped draft must not enter dedup"
    finally:
        cleanup(test_dir)


# ─── 3. boot migration repairs corrupted rows ───────────────────────────────

def test_migration_repairs_corrupt_return_lines():
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        from app import db
        with db.conn() as c:
            c.execute("INSERT INTO sales(id, invoice_no, subtotal, total, "
                      "payment_status, created_at) VALUES(900,'IMP-X',250,250,'paid',"
                      "'2026-09-22 19:33:00')")
            # the exact corruption from the client's DB (sale #10760)
            c.execute("INSERT INTO sale_items(id, sale_id, item_name, cost_price, "
                      "sell_price, qty, line_total) VALUES(9001,900,'ITEM 250',191.87,"
                      "-1750.0,-7,-1750.0)")
            c.execute("INSERT INTO sale_items(id, sale_id, item_name, cost_price, "
                      "sell_price, qty, line_total) VALUES(9002,900,'ITEM 1000',791.79,"
                      "1000.0,2,2000.0)")
            # a native-looking return line: sell_price is the unit price —
            # must NOT match the repair signature and stay untouched
            c.execute("INSERT INTO sales(id, invoice_no, subtotal, total, "
                      "payment_status, created_at) VALUES(901,'NAT',150,150,'paid',"
                      "'2026-09-23 10:00:00')")
            c.execute("INSERT INTO sale_items(id, sale_id, item_name, cost_price, "
                      "sell_price, qty, line_total) VALUES(9003,901,'N',100,20.0,-1,-20.0)")
        db.init()  # boot again → migration runs
        with db.conn() as c:
            r1 = c.execute("SELECT sell_price FROM sale_items WHERE id=9001").fetchone()
            r2 = c.execute("SELECT sell_price FROM sale_items WHERE id=9002").fetchone()
            r3 = c.execute("SELECT sell_price FROM sale_items WHERE id=9003").fetchone()
        assert abs(r1["sell_price"] - 250.0) < 0.01, r1["sell_price"]  # repaired
        assert abs(r2["sell_price"] - 1000.0) < 0.01                   # untouched
        assert abs(r3["sell_price"] - 20.0) < 0.01, r3["sell_price"]   # native untouched
        with db.conn() as c:
            act = c.execute("SELECT COUNT(*) FROM activity_log WHERE "
                            "event_type='sale_price_repaired'").fetchone()[0]
        assert act == 1
        # idempotent: booting again must not re-repair or double-log
        db.init()
        with db.conn() as c:
            act2 = c.execute("SELECT COUNT(*) FROM activity_log WHERE "
                             "event_type='sale_price_repaired'").fetchone()[0]
            r1b = c.execute("SELECT sell_price FROM sale_items WHERE id=9001").fetchone()
        assert act2 == 1 and abs(r1b["sell_price"] - 250.0) < 0.01
    finally:
        cleanup(test_dir)


# ─── 4. get_margins uses line_total (agrees with P&L even on corrupt rows) ──

def test_get_margins_uses_line_total():
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        from app import db
        with db.conn() as c:
            c.execute("INSERT INTO sales(id, invoice_no, subtotal, total, "
                      "payment_status, created_at) VALUES(900,'IMP-X',250,250,'paid',"
                      "'2026-09-22 19:33:00')")
            # corrupt row pre-repair: sell_price=-1750 with qty=-7
            c.execute("INSERT INTO sale_items(id, sale_id, category_id, item_name, "
                      "cost_price, sell_price, qty, line_total) VALUES(9001,900,1,"
                      "'ITEM 250',191.87,-1750.0,-7,-1750.0)")
        from app.profit_analytics import get_margins
        m = get_margins()
        # Total Sales must be Σ line_total (−1750), NOT sell×qty (+12250)
        assert abs(m["total_sales"] + 1750.0) < 0.01, m["total_sales"]
        assert abs(m["total_cogs"] + 1343.09) < 0.05, m["total_cogs"]
    finally:
        cleanup(test_dir)


# ─── 5. full-loop: import → margins == sale totals (the client's complaint) ─

def test_imported_data_margins_agree_with_sale_totals():
    """After import, the line-level metrics must equal Σ sales.total —
    the two screens the client compared show the same number."""
    test_dir = setup_test_db_with_password(prefix="bb_v1823_")
    try:
        _prep_db()
        _seed()
        _run_import(test_dir)
        from app import db
        from app.profit_analytics import get_margins
        m = get_margins()
        with db.conn() as c:
            t = c.execute("SELECT COALESCE(SUM(total),0) v FROM sales WHERE "
                          "payment_status IN ('paid','credit','partial')").fetchone()["v"]
        assert abs(m["total_sales"] - float(t)) < 0.05, \
            (m["total_sales"], t)  # 500 + 250 − 30 = 720 both ways
    finally:
        cleanup(test_dir)
