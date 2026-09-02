"""v8.18.15 — Bill delete restores stock + deleted-data leaks + report freshness.

User-reported issues this suite locks in:

  1. "if I delete a bill, stock should update" — deleting a CONFIRMED bill
     now reverses its category_stock_state contribution atomically; the undo
     (restore) re-applies it. Root cause of the old behavior: delete only
     set deleted_at, leaving phantom stock until the next boot-time
     rebuild_stock_state — which is ALSO why "reports only updated after
     restarting the app".
  2. "make sure we are not showing deleted data" — insights overdue alerts,
     30-day cash-forecast dues and duplicate-bill detection all skipped
     soft-deleted bills before; reports/lists already filtered.
  3. "refresh issue" — /api/* responses now carry Cache-Control: no-store
     so the Tauri webview HTTP cache can never serve stale report data.
  4. Pre-existing bug found while fixing 1: app/profit.py (the shim) did
     NOT re-export reverse_purchase_in_state, so the re-confirm reversal
     (bills._confirm_reverse_old_purchases) raised AttributeError that was
     swallowed as state drift.

Run: python -m pytest tests/test_v8_18_15_delete_stock_freshness.py -v
"""
import sys
from pathlib import Path
import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

TODAY = datetime.date.today().strftime("%Y-%m-%d")
MONTH = TODAY[:7]
YEAR = TODAY[:4]


def setup_test_db():
    return setup_test_db_with_password(prefix="billbook_v1815_")


def _stock(cat_id):
    from app import db
    with db.conn() as c:
        r = c.execute(
            "SELECT current_qty, current_value FROM category_stock_state "
            "WHERE category_id=?", (cat_id,)).fetchone()
        return (round(float(r["current_qty"]), 2),
                round(float(r["current_value"]), 2)) if r else (0.0, 0.0)


def _make_confirmed_bill(tc, code="T15", qty=15, price=100, phone="0300-0000015"):
    """Create a fresh category + confirmed bill on it. Returns (bill_id, cat_id)."""
    r = tc.post("/api/categories", json={"code": code, "name": f"Cat {code}",
                                         "sell_price": price * 1.5})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    r = tc.post("/api/bills/empty")
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    r = tc.post(f"/api/bills/{bid}/confirm", json={
        "supplier_name": f"Sup {code}", "phone": phone,
        "bill_date": TODAY, "bill_no": f"{code}-1",
        "written_total": qty * price, "payment_status": "paid", "unit": "pcs",
        "notes": "",
        "items": [{"raw": f"item {code}", "item_code": code, "price": price,
                   "qty": qty, "unit": "pcs", "category_id": cid}]})
    assert r.status_code == 200, r.text[:300]
    return bid, cid


# ------------------------------------------------------------------
# 1. Bill delete → stock reversal (the core fix)
# ------------------------------------------------------------------

def test_soft_delete_reverses_stock():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "A15")
            assert _stock(cid) == (15.0, 1500.0)

            r = tc.delete(f"/api/bills/{bid}")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["soft_deleted"] is True
            assert body["reversed_stock_lines"] == 1
            # qty AND value reversed at ORIGINAL price
            assert _stock(cid) == (0.0, 0.0)
    finally:
        cleanup(d)


def test_restore_reapplies_stock_symmetric():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "B15")
            before = _stock(cid)
            tc.delete(f"/api/bills/{bid}")
            r = tc.post(f"/api/bills/{bid}/restore")
            assert r.status_code == 200, r.text
            assert r.json()["reapplied_stock_lines"] == 1
            # EXACT same state as before the delete
            assert _stock(cid) == before == (15.0, 1500.0)
    finally:
        cleanup(d)


def test_double_delete_and_double_restore_idempotent():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "C15")
            tc.delete(f"/api/bills/{bid}")
            # second soft delete: no double reversal
            r = tc.delete(f"/api/bills/{bid}")
            assert r.status_code == 200 and r.json().get("idempotent") is True
            assert _stock(cid) == (0.0, 0.0)
            # restore, then restore again: no double re-apply
            tc.post(f"/api/bills/{bid}/restore")
            r = tc.post(f"/api/bills/{bid}/restore")
            assert r.status_code == 200 and r.json().get("idempotent") is True
            assert _stock(cid) == (15.0, 1500.0)
    finally:
        cleanup(d)


def test_permanent_delete_reverses_stock_and_cascades_items():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "D15")
            r = tc.delete(f"/api/bills/{bid}?permanent=true")
            assert r.status_code == 200, r.text
            assert _stock(cid) == (0.0, 0.0)
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM bills WHERE id=?", (bid,)).fetchone()["n"]
                ni = c.execute("SELECT COUNT(*) n FROM bill_items WHERE bill_id=?",
                               (bid,)).fetchone()["n"]
            assert n == 0 and ni == 0
    finally:
        cleanup(d)


def test_permanent_delete_of_soft_deleted_does_not_reverse_twice():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "E15")
            tc.delete(f"/api/bills/{bid}")            # reversal #1 → 0
            r = tc.delete(f"/api/bills/{bid}?permanent=true")  # must NOT reverse again
            assert r.status_code == 200, r.text
            assert r.json().get("reversed_stock_lines", 0) == 0
            assert _stock(cid) == (0.0, 0.0)
    finally:
        cleanup(d)


def test_review_bill_delete_touches_no_stock():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.post("/api/categories", json={"code": "F15", "name": "Cat F15",
                                                 "sell_price": 150})
            cid = r.json()["id"]
            r = tc.post("/api/bills/empty")
            bid = r.json()["id"]  # status='review' — never applied stock
            r = tc.delete(f"/api/bills/{bid}")
            assert r.status_code == 200
            assert r.json().get("reversed_stock_lines", 0) == 0
            assert _stock(cid) == (0.0, 0.0)
    finally:
        cleanup(d)


def test_delete_missing_bill_404():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.delete("/api/bills/999999")
            assert r.status_code == 404
            r = tc.post("/api/bills/999999/restore")
            assert r.status_code == 404
    finally:
        cleanup(d)


def test_confirm_deleted_bill_rejected():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "G15")
            tc.delete(f"/api/bills/{bid}")
            # confirming a deleted bill would re-add stock for a hidden bill
            r = tc.post(f"/api/bills/{bid}/confirm", json={
                "supplier_name": "Sup G15", "phone": "0300-0000015",
                "bill_date": TODAY, "bill_no": "G15-1", "written_total": 1500,
                "payment_status": "paid", "unit": "pcs", "notes": "",
                "items": [{"raw": "item G15", "item_code": "G15", "price": 100,
                           "qty": 15, "unit": "pcs", "category_id": cid}]})
            assert r.status_code == 409
            assert _stock(cid) == (0.0, 0.0)
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# 2. Reports reflect deletes immediately (no restart)
# ------------------------------------------------------------------

def test_monthly_report_drops_deleted_bill_same_process():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            rng = f"start={YEAR}-01-01&end={YEAR}-12-31"
            m1 = tc.get(f"/api/reports/monthly?{rng}").json()
            bid, _ = _make_confirmed_bill(tc, "H15")
            m2 = tc.get(f"/api/reports/monthly?{rng}").json()
            assert m2["kpis"]["total_bills"] == m1["kpis"]["total_bills"] + 1
            assert m2["kpis"]["total_spend"] > m1["kpis"]["total_spend"]

            tc.delete(f"/api/bills/{bid}")
            m3 = tc.get(f"/api/reports/monthly?{rng}").json()
            # deleted bill vanishes WITHOUT any restart/rebuild
            assert m3["kpis"]["total_bills"] == m1["kpis"]["total_bills"]
            assert m3["kpis"]["total_spend"] == m1["kpis"]["total_spend"]

            lst = tc.get(f"/api/bills?{rng}").json()
            assert bid not in [b["id"] for b in lst["bills"]]
    finally:
        cleanup(d)


def test_running_state_matches_rebuild_after_delete():
    """The live state and a full replay must agree — delete included."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import profit
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "I15")
            tc.delete(f"/api/bills/{bid}")
            live = _stock(cid)
            profit.rebuild_stock_state()
            replayed = _stock(cid)
            assert live == replayed == (0.0, 0.0)
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# 3. Deleted data leaks (alerts / forecast / duplicate detection)
# ------------------------------------------------------------------

def test_deleted_credit_bill_not_in_insights_alerts():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db, insights
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.post("/api/bills/empty")
            bid = r.json()["id"]
            r = tc.post(f"/api/bills/{bid}/confirm", json={
                "supplier_name": "Leak Sup", "phone": "0300-0000111",
                "bill_date": f"{YEAR}-01-05", "bill_no": "LEAK-1",
                "written_total": 9000, "payment_status": "credit",
                "credit_due_date": (datetime.date.today() - datetime.timedelta(days=45)).isoformat(),
                "unit": "pcs", "notes": "",
                "items": []})
            assert r.status_code == 200, r.text[:200]
            tc.delete(f"/api/bills/{bid}")
            alerts = insights.active_alerts()
            flat = str(alerts)
            # the deleted bill's id must not appear in any alert payload
            assert f"'bill_id': {bid}" not in flat and f'"bill_id": {bid}' not in flat
    finally:
        cleanup(d)


def test_deleted_bill_not_detected_as_duplicate():
    d = setup_test_db()
    try:
        from app.validate import detect_duplicate
        r = detect_duplicate("Leak Sup", "0300-0000222", f"{YEAR}-02-05")
        assert r is None
        from app import db
        with db.conn() as c:
            c.execute(
                "INSERT INTO bills(supplier_name, phone, bill_date, written_total, status) "
                "VALUES('Leak Sup', '0300-0000222', ?, 500, 'confirmed')",
                (f"{YEAR}-02-05",))
        assert detect_duplicate("Leak Sup", "0300-0000222", f"{YEAR}-02-05") is not None
        with db.conn() as c:
            c.execute("UPDATE bills SET deleted_at='2026-01-01T00:00:00' "
                      "WHERE phone='0300-0000222'")
        # deleted → NOT a duplicate anymore (re-entering the same bill is fine)
        assert detect_duplicate("Leak Sup", "0300-0000222", f"{YEAR}-02-05") is None
    finally:
        cleanup(d)


def test_forecast_ignores_deleted_bill_dues():
    d = setup_test_db()
    try:
        from app import db, ext_pos
        due = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        with db.conn() as c:
            b1 = c.execute(
                "INSERT INTO bills(supplier_name, bill_date, written_total, status, "
                "payment_status, credit_due_date) "
                "VALUES('FC Sup', ?, 5000, 'confirmed', 'credit', ?)",
                (TODAY, due)).lastrowid
        fc1 = ext_pos.get_cash_flow_forecast() if hasattr(ext_pos, "get_cash_flow_forecast") else None
        if fc1 is None:
            # function name differs — probe the module's public entry instead
            names = [n for n in dir(ext_pos) if "forecast" in n.lower()]
            assert names, "no forecast function found in ext_pos"
            fn = getattr(ext_pos, names[0])
            fc1 = fn()
        total1 = sum(float(x.get("outflow") or 0) for x in
                     (fc1.get("daily") or fc1.get("days") or []))
        with db.conn() as c:
            c.execute("UPDATE bills SET deleted_at='2026-01-01T00:00:00' WHERE id=?", (b1,))
        fn = ext_pos.get_cash_flow_forecast if hasattr(ext_pos, "get_cash_flow_forecast") \
            else getattr(ext_pos, [n for n in dir(ext_pos) if "forecast" in n.lower()][0])
        fc2 = fn()
        total2 = sum(float(x.get("outflow") or 0) for x in
                     (fc2.get("daily") or fc2.get("days") or []))
        assert total2 == total1 - 5000.0, (total1, total2)
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# 4. Webview/API cache — no-store headers
# ------------------------------------------------------------------

def test_api_responses_have_no_store_headers():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            for url in (f"/api/reports/monthly?start={YEAR}-01-01&end={YEAR}-12-31",
                        "/api/inventory", "/api/reports/pnl"):
                r = tc.get(url)
                cc = r.headers.get("Cache-Control", "")
                assert "no-store" in cc, (url, cc)
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# 5. Pre-existing shim bug — reverse_purchase_in_state re-exported
# ------------------------------------------------------------------

def test_profit_shim_exports_reverse_purchase():
    from app import profit
    assert hasattr(profit, "reverse_purchase_in_state")
    from app.profit_engine import reverse_purchase_in_state
    assert profit.reverse_purchase_in_state is reverse_purchase_in_state
    assert "reverse_purchase_in_state" in profit.__all__


def test_reconfirm_reverses_old_stock_before_applying_new():
    """Pre-v8.18.15 the reversal raised AttributeError (missing shim export)
    and was swallowed — old qty stayed until the post-commit rebuild."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            bid, cid = _make_confirmed_bill(tc, "J15", qty=15, price=100)
            assert _stock(cid) == (15.0, 1500.0)
            # re-confirm the same bill with 10 pcs @ 100
            r = tc.post(f"/api/bills/{bid}/confirm", json={
                "supplier_name": "Sup J15", "phone": "0300-0000015",
                "bill_date": TODAY, "bill_no": "J15-2", "written_total": 1000,
                "payment_status": "paid", "unit": "pcs", "notes": "",
                "items": [{"raw": "item J15", "item_code": "J15", "price": 100,
                           "qty": 10, "unit": "pcs", "category_id": cid}]})
            assert r.status_code == 200, r.text[:300]
            # old 15 reversed + new 10 applied — WITHOUT relying on the
            # post-commit rebuild to repair it
            assert _stock(cid) == (10.0, 1000.0)
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# 6. Salary quick guard (user asked to verify; full suite lives in
#    test_v8_18_13_salary_extra_sales.py)
# ------------------------------------------------------------------

def test_salary_e2e_cash_reconciles_with_expense():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.post("/api/salary/employees", json={
                "name": "Verify Staff", "phone": "0302-2222222",
                "role": "cashier", "monthly_salary": 30000})
            assert r.status_code == 200, r.text
            eid = r.json()["id"]
            r = tc.post("/api/salary/records", json={
                "employee_id": eid, "month": MONTH, "off_days_taken": 2})
            assert r.status_code == 200, r.text
            rec = r.json().get("record", r.json())
            # 30k + 2 extra days × 1k = 32k gross; expense must equal gross
            assert abs(rec["final_payable"] - 32000.0) < 0.01
            tc.post("/api/salary/advances", json={
                "employee_id": eid, "amount": 5000, "date": TODAY})
            tc.post(f"/api/salary/records/{rec['id']}/pay", json={"payment_method": "cash"})
            with db.conn() as c:
                cash_out = -float(c.execute(
                    "SELECT COALESCE(SUM(amount),0) v FROM cash_drawer").fetchone()["v"])
                exp = float(c.execute(
                    "SELECT COALESCE(SUM(amount),0) v FROM expenses e "
                    "WHERE e.category='Salaries'").fetchone()["v"])
            # cash out (5k advance + 27k final) == expense (32k) — no double count
            assert abs(cash_out - 32000.0) < 0.01, cash_out
            assert abs(exp - 32000.0) < 0.01, exp
    finally:
        cleanup(d)
