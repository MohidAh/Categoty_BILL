"""v8.18.16 regression suite — report revenue consistency.

The recurring user complaint: "reports show different / extra sales".
Root cause class: different reports computed "revenue" on different bases
(raw sell_price*qty vs charged line_total vs sales.total). This suite
drives the REAL POS sale API (so line_total/totals are exactly what
production writes) with line discounts + sale discounts, then asserts
every report agrees where it must:

  1. No discounts  → sold_stock == profit_analysis(month+category) == P&L
  2. Line discount → sold_stock == profit_analysis == P&L (all post-line-discount)
  3. Sale discount → sold_stock revenue − sale_discounts == P&L revenue
                     (reconciliation field must exist and be exact);
                     profit_analysis month == category == P&L
  4. Refund        → refunded sale vanishes from all three consistently
  5. profit_analysis month view == its own category view (same report!)
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "tests"))

from test_helpers import login_client  # noqa: E402

TODAY = None  # filled per-test


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="bb_v81816_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    # clean slate
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills", "customers",
                  "price_categories", "suppliers", "stock_adjustments",
                  "activity_log", "category_stock_state", "expenses",
                  "extra_sales", "salary_records", "salary_employees"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
    # one category: A, sell 500, cost seed via purchase
    import tests.test_helpers as th
    from app import db as _db
    with _db.conn() as c:
        c.execute("INSERT INTO suppliers(id, name, phone) VALUES(1, 'S', '0300')")
        c.execute("INSERT INTO price_categories(id, name, code, sell_price, active, sort_order) "
                  "VALUES(1,'Cat A','A',500,1,1)")
        c.execute("INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
                  "written_total, computed_total, status, payment_status, created_at) "
                  "VALUES(1, 1, 'S', '2026-01-02', 'B1', 10000, 10000, 'confirmed', 'paid', '2026-01-02 10:00:00')")
        c.execute("INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                  "VALUES(1, 1, 'Cat A', 'A', 200, 50, 'pcs', 10000, 1)")
    from app import profit
    profit.rebuild_stock_state()
    # password auth for API login
    from app import db as _db2
    from app.security import hash_password
    with _db2.conn() as c:
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    return test_dir


def _login(tc):
    login_client(tc)


def _make_sale(tc, items, sale_discount=None, discount_type="amount"):
    payload = {"customer_name": "Walk-in", "items": items, "payment_method": "cash"}
    if sale_discount:
        payload["discount"] = sale_discount
        payload["discount_type"] = discount_type
    r = tc.post("/api/sales", json=payload)
    assert r.status_code == 200, f"sale failed: {r.status_code} {r.text}"
    return r.json()


def _month_range():
    import datetime
    now = datetime.datetime.now()
    start = f"{now.year}-{now.month:02d}-01"
    end = f"{now.year}-{now.month:02d}-28"
    return start, end


def test_no_discount_all_reports_agree():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import reports, shop
        with TestClient(app) as tc:
            _login(tc)
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 2}])
            s, e = _month_range()
            ss = reports.sold_stock_report(s, e)["totals"]
            pa_c = reports.profit_analysis_report(s, e, "category")["totals"]
            pa_m = reports.profit_analysis_report(s, e, "month")["totals"]
            pnl = shop.get_pnl()
            # everyone: revenue 1000, cogs 400 (2 @ 200)
            for name, rev in (("sold_stock", ss["revenue"]),
                              ("pa_category", pa_c["revenue"]),
                              ("pa_month", pa_m["revenue"]),
                              ("pnl", pnl["net_revenue"])):
                assert abs(rev - 1000.0) < 0.01, f"{name} revenue {rev} != 1000"
            assert abs(ss["cogs"] - 400.0) < 0.01, ss["cogs"]
            assert abs(pa_c["cogs"] - 400.0) < 0.01, pa_c["cogs"]
            assert abs(pa_m["cogs"] - 400.0) < 0.01, pa_m["cogs"]
            assert abs(pnl["cost_of_goods"] - 400.0) < 0.01, pnl["cost_of_goods"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_line_discount_all_reports_agree():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import reports, shop
        with TestClient(app) as tc:
            _login(tc)
            # 2 x 500 with 10% line discount -> line_total 900
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 2,
                             "discount_pct": 10}])
            s, e = _month_range()
            ss = reports.sold_stock_report(s, e)["totals"]
            pa_c = reports.profit_analysis_report(s, e, "category")["totals"]
            pa_m = reports.profit_analysis_report(s, e, "month")["totals"]
            pnl = shop.get_pnl()
            # v8.18.16: pre-fix sold_stock said 1000 here (extra Rs 100)
            for name, rev in (("sold_stock", ss["revenue"]),
                              ("pa_category", pa_c["revenue"]),
                              ("pa_month", pa_m["revenue"]),
                              ("pnl", pnl["net_revenue"])):
                assert abs(rev - 900.0) < 0.01, f"{name} revenue {rev} != 900"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_sale_discount_reconciliation_field():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import reports, shop
        with TestClient(app) as tc:
            _login(tc)
            # 2 x 500, Rs 100 sale-level discount -> customer pays 900
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 2}],
                       sale_discount=100)
            s, e = _month_range()
            r = reports.sold_stock_report(s, e)
            ss = r["totals"]
            assert abs(ss["revenue"] - 1000.0) < 0.01, ss["revenue"]  # line charges
            assert abs(r["sale_discounts"] - 100.0) < 0.01, r["sale_discounts"]
            # reconciliation: line charges − sale discount = paid amount
            assert abs(ss["revenue"] - r["sale_discounts"] - 900.0) < 0.01
            pa_c = reports.profit_analysis_report(s, e, "category")["totals"]
            pa_m = reports.profit_analysis_report(s, e, "month")["totals"]
            pnl = shop.get_pnl()
            for name, rev in (("pa_category", pa_c["revenue"]),
                              ("pa_month", pa_m["revenue"]),
                              ("pnl", pnl["net_revenue"])):
                assert abs(rev - 900.0) < 0.01, f"{name} revenue {rev} != 900"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_refund_removes_from_all_reports():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import reports, shop
        with TestClient(app) as tc:
            _login(tc)
            r1 = _make_sale(tc, [{"category_id": 1, "category_code": "A",
                                  "item_name": "Widget", "sell_price": 500, "qty": 2}])
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 1}])
            # refund the first sale (PIN gate disabled for test)
            from app import db as _db3
            with _db3.conn() as c:
                c.execute("INSERT OR REPLACE INTO settings(key, value) "
                          "VALUES('require_pin_for_refund', 'false')")
            rr = tc.post(f"/api/sales/{r1['id']}/refund",
                         json={"reason": "test"})
            assert rr.status_code == 200, rr.text
            s, e = _month_range()
            ss = reports.sold_stock_report(s, e)["totals"]
            pa_c = reports.profit_analysis_report(s, e, "category")["totals"]
            pa_m = reports.profit_analysis_report(s, e, "month")["totals"]
            pnl = shop.get_pnl()
            for name, rev in (("sold_stock", ss["revenue"]),
                              ("pa_category", pa_c["revenue"]),
                              ("pa_month", pa_m["revenue"]),
                              ("pnl", pnl["net_revenue"])):
                assert abs(rev - 500.0) < 0.01, f"{name} revenue {rev} != 500 after refund"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_month_view_matches_category_view():
    """Same report, two views — must agree on totals (v8.18.16)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import reports
        with TestClient(app) as tc:
            _login(tc)
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 2}])
            _make_sale(tc, [{"category_id": 1, "category_code": "A",
                             "item_name": "Widget", "sell_price": 500, "qty": 1,
                             "discount_pct": 10}])
            s, e = _month_range()
            pa_m = reports.profit_analysis_report(s, e, "month")["totals"]
            pa_c = reports.profit_analysis_report(s, e, "category")["totals"]
            assert abs(pa_m["revenue"] - pa_c["revenue"]) < 0.01
            assert abs(pa_m["cogs"] - pa_c["cogs"]) < 0.01
            assert abs(pa_m["gross_profit"] - pa_c["gross_profit"]) < 0.01
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_discount_pin_gate_blocks_line_discount_bypass():
    """v8.18.16: the PIN gate must measure the TOTAL effective discount vs
    the server-side category list price — not just the sale-level discount.
    A cashier giving a 25% LINE discount used to bypass it entirely."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            _login(tc)
            # 25% line discount on a list-priced item -> effective 25% > 10%
            r = tc.post("/api/sales", json={
                "customer_name": "Walk-in",
                "items": [{"category_id": 1, "category_code": "A",
                           "item_name": "Widget", "sell_price": 500, "qty": 1,
                           "discount_pct": 25}],
                "payment_method": "cash"})
            assert r.status_code == 403, (
                f"25% line discount must require PIN: {r.status_code} {r.text}")
            assert r.json().get("code") == "discount_pin_required"
            # 10% line discount passes without PIN
            r = tc.post("/api/sales", json={
                "customer_name": "Walk-in",
                "items": [{"category_id": 1, "category_code": "A",
                           "item_name": "Widget", "sell_price": 500, "qty": 1,
                           "discount_pct": 10}],
                "payment_method": "cash"})
            assert r.status_code == 200, f"10% should pass: {r.text}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_discount_pin_gate_blocks_sell_price_underpricing():
    """v8.18.16: sending a lower sell_price than the category list price is
    an effective discount — the gate must catch it (the client fully
    controls sell_price in the request)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            _login(tc)
            # list price 500, sent sell_price 100 -> 80% effective discount
            r = tc.post("/api/sales", json={
                "customer_name": "Walk-in",
                "items": [{"category_id": 1, "category_code": "A",
                           "item_name": "Widget", "sell_price": 100, "qty": 1}],
                "payment_method": "cash"})
            assert r.status_code == 403, (
                f"underpriced sell_price must require PIN: {r.status_code} {r.text}")
            assert r.json().get("code") == "discount_pin_required"
            # honest list price passes
            r = tc.post("/api/sales", json={
                "customer_name": "Walk-in",
                "items": [{"category_id": 1, "category_code": "A",
                           "item_name": "Widget", "sell_price": 500, "qty": 1}],
                "payment_method": "cash"})
            assert r.status_code == 200, f"list price should pass: {r.text}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
