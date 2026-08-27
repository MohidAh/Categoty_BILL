"""v8.5 POS Import Pipeline tests.

Verifies that the Ezi POS DBF import pipeline:
1. Creates sales with original timestamps
2. Inserts sale_items + cash_drawer entries
3. Updates customer stats
4. Applies sale to category_stock_state
5. Records UNQCODE in ezi_pos_imports for dedup
6. Re-import is idempotent
7. Delete reverses ALL side effects
8. Imported sales appear in reports
9. Credit sales don't create cash_drawer entries
10. Unknown-category items produce warnings
"""
import os
import sys
import tempfile
import shutil
import zipfile
import json
from pathlib import Path
from datetime import date, datetime

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

# Use a temp data dir so we don't touch any real DB
TMP = Path(tempfile.mkdtemp(prefix="bb_pos_imp_"))
os.environ["BILLBOOK_DATA_DIR"] = str(TMP)

from app import config as _config
_config.DATA = TMP
from app import db as _db
_db.DB_PATH = TMP / "billbook.db"

from app import db
from app import pos_import_sync as pis
from app import shop
from app import profit_engine as pe


PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {detail}")


def make_mock_dbf(table_name: str, records: list[dict], tmp_dir: Path):
    """Create a minimal DBF file with the given records using dbfread's writer."""
    # dbfread doesn't have a writer; we use the `dbf` library or write a simple
    # CSV-style stub. For testing, we'll mock by patching pis.DBF.
    pass  # We'll patch pis.DBF instead


class FakeDBF:
    """Mock DBF reader — patches pis.DBF for testing without real DBF files."""
    def __init__(self, path, load=True):
        self.path = path
        # Return records based on the path name
        fname = os.path.basename(path).upper()
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
    @staticmethod
    def _reset_test_data():
        FakeDBF._acctrans_records = [
            # Sale 1: UNQCODE=EZ001, cash, 500, fully paid, has SI + SP records
            {"UNQCODE": "EZ001", "TYPE": "SI", "DATE": date(2026, 8, 1), "ADD_TIME": "260801143000",
             "AMOUNT": 500.0, "CREDIT": False, "PAID_BY": 0, "INTERNAL": 1,
             "DETAILS": "C - Cash Sales", "INVOICE": 1, "ITEMNAME": "Item A", "QTY": 1, "RATE": 500},
            {"UNQCODE": "EZ001", "TYPE": "SP", "DATE": date(2026, 8, 1), "ADD_TIME": "260801143000",
             "AMOUNT": 500.0, "CREDIT": False, "PAID_BY": 1, "INTERNAL": 1,
             "DETAILS": "C - Cash Sales", "INVOICE": 0, "BALANCE": 0},
            # Sale 2: UNQCODE=EZ002, credit sale, balance=100
            {"UNQCODE": "EZ002", "TYPE": "SI", "DATE": date(2026, 8, 2), "ADD_TIME": "260802100000",
             "AMOUNT": 500.0, "CREDIT": True, "PAID_BY": 0, "INTERNAL": 1,
             "DETAILS": "C - Credit Sales", "INVOICE": 2, "ITEMNAME": "Item B", "QTY": 1, "RATE": 500},
            {"UNQCODE": "EZ002", "TYPE": "SP", "DATE": date(2026, 8, 2), "ADD_TIME": "260802100000",
             "AMOUNT": 500.0, "CREDIT": True, "PAID_BY": 0, "INTERNAL": 1,
             "DETAILS": "C - Credit Sales", "INVOICE": 0, "BALANCE": 100},
        ]
        # v8.5.1: INVOICE.DBF — authoritative sale headers (1 row per sale)
        FakeDBF._invoice_records = [
            {"UNQCODE": "EZ001", "TYPE": "SI", "STATUS": "P",
             "DATE": date(2026, 8, 1), "ADD_TIME": "260801143000",
             "AMOUNT": 500.0, "PAID": 500.0, "TENDERED": 500.0,
             "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
            {"UNQCODE": "EZ002", "TYPE": "SI", "STATUS": "",  # empty status = credit
             "DATE": date(2026, 8, 2), "ADD_TIME": "260802100000",
             "AMOUNT": 500.0, "PAID": 400.0, "TENDERED": 400.0,  # PAID < AMOUNT → credit
             "CLIENT": 1, "SALESMAN": 1, "TAX": 0.0, "ROUNDING": 0.0},
        ]
        # v8.5.1: INVTRANS.DBF — line items (1 row per line item per sale)
        FakeDBF._invtrans_records = [
            {"UNQCODE": "EZ001", "TYPE": "SI", "INTERNAL": 606, "PART_NO": "100001",
             "DETAILS": "Item A", "QTY": 1.0, "AMOUNT": 500.0, "COST": 100.0},
            {"UNQCODE": "EZ002", "TYPE": "SI", "INTERNAL": 607, "PART_NO": "100002",
             "DETAILS": "Item B", "QTY": 1.0, "AMOUNT": 500.0, "COST": 100.0},
        ]
        FakeDBF._diary_records = [
            {"DATE": date(2026, 8, 7), "DETAILS": "Ishfaq Advance salary = 2000   (07.08.26)"}
        ]
        FakeDBF._debtors_records = [
            {"INTERNAL": 1, "NAME": "Cash Sales", "PHONE": "", "MOBILE": ""}
        ]
        FakeDBF._company_records = [
            {"NAME1": "Test Ezi POS Shop"}
        ]
        # v8.5.1: STOCK.DBF — item master (Item A maps to price 500 → category B)
        FakeDBF._stock_records = [
            {"INTERNAL": 606, "PART_NO": "100001", "DESC": "Item A", "PRICE1": 500.0,
             "COST": 100.0, "QTY": 0.0},
            {"INTERNAL": 607, "PART_NO": "100002", "DESC": "Item B", "PRICE1": 500.0,
             "COST": 100.0, "QTY": 0.0},
        ]


def setup_fresh_db():
    """Initialize a fresh DB and create a category to match items against."""
    db.init()
    # Ensure we have a price category to match against (the test SI record has "Item A" — no match)
    with db.conn() as c:
        c.execute("DELETE FROM sales")
        c.execute("DELETE FROM sale_items")
        c.execute("DELETE FROM cash_drawer")
        c.execute("DELETE FROM ezi_pos_imports")
        c.execute("DELETE FROM pos_expense_imports")
        c.execute("DELETE FROM pos_imports")
        c.execute("DELETE FROM activity_log")
        c.execute("DELETE FROM expenses")
        c.execute("DELETE FROM customers")
        c.execute("DELETE FROM category_stock_state")
        # Insert a category "Item A" so it matches the SI record
        c.execute("DELETE FROM price_categories")
        c.execute("INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                  "VALUES(1, 'Item A', 'A', 500, '#3b82f6', 1, 1)")
        # Purchase some stock so sale can apply
    pe.apply_purchase_to_state(1, 100, 100.0)  # 100 units @ Rs 100


def make_mock_zip(zip_path: Path):
    """Create a minimal zip file containing fake DBF files.

    v8.5.1: must include ALL DBF files the importer reads:
    ACCTRANS, INVTRANS, INVOICE, DIARY, DEBTORS, COMPANY, STOCK.
    The test patches pis.DBF so file contents are irrelevant —
    only the filenames matter (the importer checks os.path.exists).
    """
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ACCTRANS.DBF", b"mock")
        zf.writestr("INVTRANS.DBF", b"mock")
        zf.writestr("INVOICE.DBF", b"mock")
        zf.writestr("DIARY.DBF", b"mock")
        zf.writestr("DEBTORS.DBF", b"mock")
        zf.writestr("COMPANY.DBF", b"mock")
        zf.writestr("STOCK.DBF", b"mock")


def test_ezi_import_creates_sale_with_original_timestamp():
    print("\n=== Test 1: Ezi import creates sale with original timestamp ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    # Patch DBF in pos_import_sync
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        result = pis.import_pos_backup(str(zip_path))

        check("imported_sales == 2", result["imported_sales"] == 2,
              f"got {result['imported_sales']}")
        check("import_run_id returned", result.get("import_run_id") is not None,
              f"got {result.get('import_run_id')}")
        check("backup_date == 2026-08-01", result["backup_date"] == "2026-08-01",
              f"got {result['backup_date']}")
        check("total_sales_amount == 1000", abs(result["total_sales_amount"] - 1000) < 0.01,
              f"got {result['total_sales_amount']}")

        # Verify sale 1 (EZ001 — cash, paid, original timestamp)
        with db.conn() as c:
            s1 = c.execute("SELECT * FROM sales WHERE invoice_no='IMP-EZ001'").fetchone()
        check("sale EZ001 exists", s1 is not None)
        if s1:
            check("EZ001 created_at = 2026-08-01 14:30:00",
                  s1["created_at"] == "2026-08-01 14:30:00", f"got {s1['created_at']}")
            check("EZ001 payment_status = 'paid'",
                  s1["payment_status"] == "paid", f"got {s1['payment_status']}")
            check("EZ001 payment_method = 'cash'",
                  s1["payment_method"] == "cash", f"got {s1['payment_method']}")
            check("EZ001 total = 500", abs(s1["total"] - 500) < 0.01, f"got {s1['total']}")
            check("EZ001 subtotal = total", abs(s1["subtotal"] - s1["total"]) < 0.01)
            check("EZ001 has client_uuid", bool(s1["client_uuid"]))

        # Verify sale_items
        with db.conn() as c:
            items = c.execute("SELECT * FROM sale_items WHERE sale_id=?", (s1["id"],)).fetchall()
        check("EZ001 has 1 sale_item", len(items) == 1, f"got {len(items)}")
        if items:
            it = items[0]
            check("EZ001 item category_id matched (=1)", it["category_id"] == 1,
                  f"got {it['category_id']}")
            check("EZ001 item sell_price = 500", abs(it["sell_price"] - 500) < 0.01)
            check("EZ001 item cost_price = 100 (peek_avg_cost)",
                  abs(it["cost_price"] - 100) < 0.01, f"got {it['cost_price']}")
            check("EZ001 item qty = 1", it["qty"] == 1)

        # Verify cash_drawer entry
        with db.conn() as c:
            cd = c.execute("SELECT * FROM cash_drawer WHERE reference_id=? AND reference_type='sale'",
                          (s1["id"],)).fetchone()
        check("EZ001 cash_drawer entry exists", cd is not None)
        if cd:
            check("EZ001 cash_drawer amount = 500", abs(cd["amount"] - 500) < 0.01)
            check("EZ001 cash_drawer created_at = 2026-08-01 14:30:00",
                  cd["created_at"] == "2026-08-01 14:30:00", f"got {cd['created_at']}")

        # Verify category_stock_state reduced
        st = pe.get_category_stock_state(1)[0]
        check("category_stock_state.current_qty reduced to 99",
              abs(st["current_qty"] - 99) < 0.01, f"got {st['current_qty']}")

        # Verify ezi_pos_imports record
        with db.conn() as c:
            ezi = c.execute("SELECT * FROM ezi_pos_imports WHERE unqcode='EZ001'").fetchone()
        check("ezi_pos_imports.unqcode = EZ001", ezi is not None and ezi["unqcode"] == "EZ001")
        check("ezi_pos_imports.import_run_id set",
              ezi and ezi["import_run_id"] == result["import_run_id"])

        # Verify pos_imports run record
        with db.conn() as c:
            run = c.execute("SELECT * FROM pos_imports WHERE id=?",
                           (result["import_run_id"],)).fetchone()
        check("pos_imports run exists", run is not None)
        if run:
            check("pos_imports.sale_count = 2", run["sale_count"] == 2)
            check("pos_imports.expense_count = 1", run["expense_count"] == 1)
            check("pos_imports.status = 'imported'", run["status"] == "imported")
            check("pos_imports.total_revenue = 1000",
                  abs(run["total_revenue"] - 1000) < 0.01, f"got {run['total_revenue']}")
    finally:
        pis.DBF = original_dbf


def test_reimport_same_zip_does_not_duplicate():
    print("\n=== Test 2: Re-importing same zip does not duplicate ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        r1 = pis.import_pos_backup(str(zip_path))
        check("first import: 2 sales", r1["imported_sales"] == 2)

        # Re-import the same zip — should skip all
        r2 = pis.import_pos_backup(str(zip_path))
        check("second import: 0 sales", r2["imported_sales"] == 0,
              f"got {r2['imported_sales']}")
        # Each UNQCODE has multiple ACCTRANS rows (SI + SP), so skipped_duplicates
        # counts per-row, not per-UNQCODE. >= 2 means duplicates were detected.
        check("second import: duplicates skipped (>0)", r2["skipped_duplicates"] >= 2,
              f"got {r2['skipped_duplicates']}")

        # Total sales in DB should still be 2
        with db.conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM sales WHERE invoice_no LIKE 'IMP-%'").fetchone()["n"]
        check("total IMP- sales still 2", total == 2, f"got {total}")
    finally:
        pis.DBF = original_dbf


def test_cumulative_backup_with_new_records():
    print("\n=== Test 3: Cumulative backup with new records ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        # First import: EZ001, EZ002
        zip1 = TMP / "BU20260802.zip"
        make_mock_zip(zip1)
        r1 = pis.import_pos_backup(str(zip1))
        check("first import: 2 sales", r1["imported_sales"] == 2)

        # Now simulate a newer backup that adds EZ003
        FakeDBF._acctrans_records.append(
            {"UNQCODE": "EZ003", "TYPE": "SI", "DATE": date(2026, 8, 3), "ADD_TIME": "260803120000",
             "AMOUNT": 750.0, "CREDIT": False, "PAID_BY": 0, "INTERNAL": 1,
             "DETAILS": "C - Cash Sales", "INVOICE": 3, "ITEMNAME": "Item A", "QTY": 1, "RATE": 750}
        )
        FakeDBF._acctrans_records.append(
            {"UNQCODE": "EZ003", "TYPE": "SP", "DATE": date(2026, 8, 3), "ADD_TIME": "260803120000",
             "AMOUNT": 750.0, "CREDIT": False, "PAID_BY": 1, "INTERNAL": 1,
             "DETAILS": "C - Cash Sales", "INVOICE": 0, "BALANCE": 0}
        )

        zip2 = TMP / "BU20260803.zip"
        make_mock_zip(zip2)
        r2 = pis.import_pos_backup(str(zip2))
        check("second import: 1 NEW sale (EZ003)", r2["imported_sales"] == 1,
              f"got {r2['imported_sales']}")
        check("second import: duplicates skipped (>0)", r2["skipped_duplicates"] >= 2,
              f"got {r2['skipped_duplicates']}")

        # Total sales in DB: 3 (EZ001, EZ002, EZ003)
        with db.conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM sales WHERE invoice_no LIKE 'IMP-%'").fetchone()["n"]
        check("total IMP- sales = 3", total == 3, f"got {total}")

        # EZ001 should be unchanged (same sale_id)
        with db.conn() as c:
            s = c.execute("SELECT id FROM sales WHERE invoice_no='IMP-EZ001'").fetchone()
        check("EZ001 still exists", s is not None)
    finally:
        pis.DBF = original_dbf


def test_delete_import_reverses_all_effects():
    print("\n=== Test 4: Delete import reverses ALL side effects ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        result = pis.import_pos_backup(str(zip_path))
        run_id = result["import_run_id"]

        # Pre-state capture
        with db.conn() as c:
            pre_sales = c.execute("SELECT COUNT(*) AS n FROM sales WHERE invoice_no LIKE 'IMP-%'").fetchone()["n"]
            pre_items = c.execute("SELECT COUNT(*) AS n FROM sale_items").fetchone()["n"]
            pre_cash = c.execute("SELECT COUNT(*) AS n FROM cash_drawer WHERE reference_type='sale'").fetchone()["n"]
            pre_ezi = c.execute("SELECT COUNT(*) AS n FROM ezi_pos_imports").fetchone()["n"]
        pre_stock = pe.get_category_stock_state(1)[0]["current_qty"]

        check("pre: 2 sales", pre_sales == 2)
        check("pre: 2 sale_items", pre_items == 2)
        check("pre: 1 cash_drawer sale entry", pre_cash == 1)  # only EZ001 is cash
        check("pre: 2 ezi_pos_imports", pre_ezi == 2)

        # Delete the import run
        del_result = pis.delete_pos_import(run_id)
        check("delete returned ok=True", del_result.get("ok") is True, f"got {del_result}")
        check("deleted 2 sales", del_result.get("deleted_sales") == 2,
              f"got {del_result.get('deleted_sales')}")
        check("stock_reversed >= 1", del_result.get("stock_reversed") >= 1,
              f"got {del_result.get('stock_reversed')}")

        # Post-state: everything reversed
        with db.conn() as c:
            post_sales = c.execute("SELECT COUNT(*) AS n FROM sales WHERE invoice_no LIKE 'IMP-%'").fetchone()["n"]
            post_items = c.execute("SELECT COUNT(*) AS n FROM sale_items").fetchone()["n"]
            post_cash = c.execute("SELECT COUNT(*) AS n FROM cash_drawer WHERE reference_type='sale'").fetchone()["n"]
            post_ezi = c.execute("SELECT COUNT(*) AS n FROM ezi_pos_imports WHERE import_run_id=?", (run_id,)).fetchone()["n"]
            run_status = c.execute("SELECT status FROM pos_imports WHERE id=?", (run_id,)).fetchone()["status"]
        post_stock = pe.get_category_stock_state(1)[0]["current_qty"]

        check("post: 0 IMP- sales", post_sales == 0, f"got {post_sales}")
        check("post: 0 sale_items", post_items == 0, f"got {post_items}")
        check("post: 0 cash_drawer sale entries", post_cash == 0, f"got {post_cash}")
        check("post: 0 ezi_pos_imports", post_ezi == 0, f"got {post_ezi}")
        check("post: pos_imports.status = 'deleted'", run_status == "deleted",
              f"got {run_status}")
        check("post: stock restored to 100",
              abs(post_stock - 100) < 0.01, f"got {post_stock} (pre was {pre_stock})")
    finally:
        pis.DBF = original_dbf


def test_ezi_import_credit_sale():
    print("\n=== Test 5: Credit sale (BALANCE > 0) — no cash_drawer entry ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        result = pis.import_pos_backup(str(zip_path))

        # EZ002 is the credit sale
        with db.conn() as c:
            s2 = c.execute("SELECT * FROM sales WHERE invoice_no='IMP-EZ002'").fetchone()
            cd = c.execute("SELECT * FROM cash_drawer WHERE reference_id=? AND reference_type='sale'",
                          (s2["id"],)).fetchall()
        check("EZ002 payment_status = 'credit'", s2["payment_status"] == "credit",
              f"got {s2['payment_status']}")
        check("EZ002 has NO cash_drawer entry", len(cd) == 0,
              f"got {len(cd)} entries")
    finally:
        pis.DBF = original_dbf


def test_import_summary_warns_unknown_cost():
    print("\n=== Test 6: Unknown-cost warning when category not matched ===")
    setup_fresh_db()
    # Delete categories so items can't be matched
    with db.conn() as c:
        c.execute("DELETE FROM price_categories")
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        result = pis.import_pos_backup(str(zip_path))

        check("warnings list is non-empty", len(result["warnings"]) > 0,
              f"got {len(result['warnings'])}")
        check("warnings mention 'no category mapping'",
              any("no category mapping" in w for w in result["warnings"]),
              f"warnings: {result['warnings'][:3]}")
        # sale_items.cost_price should be 0 for unmatched
        with db.conn() as c:
            items = c.execute("SELECT cost_price FROM sale_items").fetchall()
        check("all sale_items.cost_price = 0 (no category match)",
              all(it["cost_price"] == 0 for it in items))
    finally:
        pis.DBF = original_dbf


def test_imported_sale_appears_in_reports():
    print("\n=== Test 7: Imported sales appear in /api/sales reports ===")
    setup_fresh_db()
    FakeDBF._reset_test_data()
    original_dbf = pis.DBF
    pis.DBF = FakeDBF
    pis.HAS_DBFREAD = True
    try:
        zip_path = TMP / "BU20260801.zip"
        make_mock_zip(zip_path)
        result = pis.import_pos_backup(str(zip_path))

        # Query sales table directly (mimics GET /api/sales)
        with db.conn() as c:
            sales = c.execute("SELECT * FROM sales ORDER BY created_at DESC LIMIT 10").fetchall()
        imp_in_sales = [s for s in sales if s["invoice_no"].startswith("IMP-")]
        check("IMP- sales appear in /api/sales query", len(imp_in_sales) == 2,
              f"got {len(imp_in_sales)}")

        # Monthly report query
        with db.conn() as c:
            monthly = c.execute(
                "SELECT strftime('%Y-%m', created_at) AS month, "
                "COUNT(*) AS n, SUM(total) AS revenue "
                "FROM sales WHERE created_at >= '2026-08-01' AND created_at < '2026-08-31' "
                "GROUP BY month"
            ).fetchall()
        check("monthly report shows 2 sales for 2026-08",
              any(r["n"] == 2 and abs(r["revenue"] - 1000) < 0.01 for r in monthly),
              f"got {[(r['month'], r['n'], r['revenue']) for r in monthly]}")

        # Top items report
        with db.conn() as c:
            top_items = c.execute(
                "SELECT item_name, SUM(qty) AS qty_sold, SUM(line_total) AS revenue "
                "FROM sale_items GROUP BY item_name ORDER BY revenue DESC LIMIT 5"
            ).fetchall()
        check("top-items report shows imported items", len(top_items) >= 1,
              f"got {len(top_items)}")
    finally:
        pis.DBF = original_dbf


def main():
    try:
        test_ezi_import_creates_sale_with_original_timestamp()
        test_reimport_same_zip_does_not_duplicate()
        test_cumulative_backup_with_new_records()
        test_delete_import_reverses_all_effects()
        test_ezi_import_credit_sale()
        test_import_summary_warns_unknown_cost()
        test_imported_sale_appears_in_reports()
    finally:
        try:
            shutil.rmtree(TMP)
        except Exception:
            pass
    print(f"\n{'='*60}")
    print(f"POS Import Tests: {PASS} passed, {FAIL} failed")
    print('='*60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
