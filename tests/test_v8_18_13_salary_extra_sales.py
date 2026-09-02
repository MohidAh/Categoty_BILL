"""v8.18.13 — Staff Salary Management + Extra (non-stock) Sales tests.

Feature 1 — Extra Sales (things sold OUTSIDE the POS, e.g. cardboard cartons,
scrap/raddi, drums):
  * CRUD + auto cash-drawer entry for cash sales (+ delete cleanup).
  * Income flows into Actual Earnings (extra_sales_income), P&L (other_income),
    Cash Flow (extra_sales_cash/other) and the daily summary.

Feature 2 — Staff Salary Management:
  * Employees with fixed monthly salary (add + set).
  * 4 paid off-days allowed; fewer taken -> extra working days paid at
    salary/30 per day (compute_salary math).
  * Advances recorded + deducted from final payable.
  * final_payable = salary + extra_day_pay - advances.
  * Saving a record AUTO-POSTS an operating expense under 'Salaries' so
    payroll reduces Gross Profit -> Actual Earnings / Net Profit.
  * Pay flow: status paid, cash out, double-pay rejected.
  * Monthly history per employee.
  * Deletes clean up expense + cash entries.

Run: .venv/bin/python -m pytest tests/test_v8_18_13_salary_extra_sales.py -v
"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

JS = PROJECT_ROOT / "app" / "static" / "js"


def setup_test_db():
    return setup_test_db_with_password(prefix="billbook_v81813_")


# ------------------------------------------------------------------
# Pure math
# ------------------------------------------------------------------

def test_compute_salary_math():
    """The client's exact rule: fewer than 4 off-days -> extra days @ salary/30."""
    from app import salary

    # 30000 salary, 2 off days taken (of 4 allowed) -> 2 extra days @ 1000
    m = salary.compute_salary(30000, 4, 2, 0)
    assert m["per_day_rate"] == 1000.0
    assert m["extra_working_days"] == 2
    assert m["extra_day_pay"] == 2000.0
    assert m["gross_salary"] == 32000.0
    assert m["final_payable"] == 32000.0

    # 0 off days -> 4 extra days
    m = salary.compute_salary(21000, 4, 0, 5000)
    assert m["extra_working_days"] == 4
    assert m["extra_day_pay"] == 2800.0
    assert m["final_payable"] == 21000 + 2800 - 5000  # 18800

    # exactly 4 off days -> no extra pay
    m = salary.compute_salary(30000, 4, 4, 0)
    assert m["extra_working_days"] == 0
    assert m["extra_day_pay"] == 0
    assert m["final_payable"] == 30000

    # more than 4 off days -> still no extra pay, no invented deduction
    m = salary.compute_salary(30000, 4, 6, 0)
    assert m["extra_working_days"] == 0
    assert m["final_payable"] == 30000

    # salary/30 basis regardless of month length (client spec)
    m = salary.compute_salary(15000, 4, 1, 2000)
    assert m["per_day_rate"] == 500.0
    assert m["final_payable"] == 15000 + 1500 - 2000


# ------------------------------------------------------------------
# Extra Sales
# ------------------------------------------------------------------

def test_extra_sales_crud_and_cash_drawer():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            # add cash + bank sales
            r = tc.post("/api/extra-sales", json={
                "item_name": "Cardboard cartons", "quantity": 50, "unit_price": 15,
                "payment_method": "cash", "date": "2026-09-03"})
            assert r.status_code == 200
            sid = r.json()["id"]
            assert r.json()["total"] == 750.0

            r = tc.post("/api/extra-sales", json={
                "item_name": "Raddi (scrap)", "quantity": 30, "unit_price": 40,
                "payment_method": "bank", "date": "2026-09-10"})
            assert r.status_code == 200

            # invalid inputs -> clean 400
            assert tc.post("/api/extra-sales", json={
                "item_name": "x", "quantity": 0, "unit_price": 5}).status_code == 400
            assert tc.post("/api/extra-sales", json={
                "item_name": "", "quantity": 1, "unit_price": 5}).status_code == 400

            # list + summary
            r = tc.get("/api/extra-sales?month=2026-09")
            assert len(r.json()["extra_sales"]) == 2
            s = tc.get("/api/extra-sales/summary?month=2026-09").json()
            assert s["month_total"] == 1950.0 and s["entries"] == 2

            # cash drawer: exactly ONE entry for the cash sale (+750)
            with db.conn() as c:
                rows = c.execute(
                    "SELECT * FROM cash_drawer WHERE reference_type='extra_sale' AND reference_id=?",
                    (sid,)).fetchall()
                assert len(rows) == 1 and rows[0]["amount"] == 750.0
                bank_rows = c.execute(
                    "SELECT COUNT(*) n FROM cash_drawer WHERE type='extra_sale'"
                ).fetchone()["n"]
                assert bank_rows == 1  # bank sale wrote no drawer row

            # delete cleans the drawer entry
            assert tc.delete(f"/api/extra-sales/{sid}").status_code == 200
            with db.conn() as c:
                n = c.execute(
                    "SELECT COUNT(*) n FROM cash_drawer WHERE reference_type='extra_sale'"
                ).fetchone()["n"]
                assert n == 0
            assert tc.delete("/api/extra-sales/9999").status_code == 404
    finally:
        cleanup(d)


def test_extra_sales_flow_into_reports():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import shop, pos_extra
        with TestClient(app) as tc:
            login_client(tc)
            tc.post("/api/extra-sales", json={
                "item_name": "Drums", "quantity": 4, "unit_price": 500,
                "payment_method": "cash", "date": "2026-09-05"})
            tc.post("/api/extra-sales", json={
                "item_name": "Raddi", "quantity": 10, "unit_price": 100,
                "payment_method": "bank", "date": "2026-09-15"})

            ae = shop.get_actual_earnings("2026-09")
            assert ae["extra_sales_income"] == 3000.0
            assert ae["actual_earnings"] == round(ae["gross_profit"] + 3000.0
                                                  - ae["operating_expenses"], 2)

            pnl = shop.get_pnl("2026-09")
            assert pnl["other_income"] == 3000.0
            assert pnl["net_profit"] == round(pnl["gross_profit"] + 3000.0
                                              - pnl["expenses"], 2)

            cf = pos_extra.get_cash_flow("2026-09")
            assert cf["inflows"]["extra_sales_cash"] == 2000.0
            assert cf["inflows"]["extra_sales_other"] == 1000.0

            ds = shop.get_daily_summary("2026-09-05")
            assert ds["extra_sales_total"] == 2000.0
            assert ds["extra_sales_count"] == 1
            # WhatsApp text mentions extra sales
            text = shop.build_daily_summary_text("2026-09-05")
            assert "Extra Sales" in text
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# Salary: records + auto expense + pay + advances + history
# ------------------------------------------------------------------

def _add_staff(tc, name, salary):
    r = tc.post("/api/salary/employees", json={"name": name, "monthly_salary": salary})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_salary_record_posts_operating_expense():
    """Saving a record auto-creates the Salaries expense (the client's
    'salary included under Operating Expenses' requirement)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            eid = _add_staff(tc, "Ali Raza", 30000)

            r = tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-09", "off_days_taken": 2})
            assert r.status_code == 200
            rec = r.json()
            assert rec["extra_working_days"] == 2
            assert rec["extra_day_pay"] == 2000.0
            assert rec["final_payable"] == 32000.0

            with db.conn() as c:
                exp = c.execute("SELECT * FROM expenses WHERE id=?",
                                (rec["expense_id"],)).fetchone()
                assert exp is not None
                assert exp["amount"] == 32000.0            # full cost
                assert exp["expense_type"] == "operating"
                assert exp["date"] == "2026-09-01"         # lands in the month
                cat = c.execute("SELECT name FROM expense_categories WHERE id=?",
                                (exp["category_id"],)).fetchone()
                assert cat["name"] == "Salaries"

            # editing off-days re-syncs the expense amount
            r = tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-09", "off_days_taken": 0})
            rec = r.json()
            assert rec["extra_day_pay"] == 4000.0
            with db.conn() as c:
                exp = c.execute("SELECT amount FROM expenses WHERE id=?",
                                (rec["expense_id"],)).fetchone()
                assert exp["amount"] == 34000.0

            # invalid month / employee -> clean 400
            assert tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-13", "off_days_taken": 0}).status_code == 400
            assert tc.post("/api/salary/records", json={
                "employee_id": 9999, "month": "2026-09", "off_days_taken": 0}).status_code == 400
    finally:
        cleanup(d)


def test_salary_flow_advance_pay_history_delete():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            eid = _add_staff(tc, "Bilal", 30000)

            # advance 5000 (cash) -> drawer entry
            r = tc.post("/api/salary/advances", json={
                "employee_id": eid, "amount": 5000, "date": "2026-09-05",
                "description": "Eid advance", "payment_method": "cash"})
            assert r.status_code == 200
            with db.conn() as c:
                adv_cd = c.execute(
                    "SELECT amount FROM cash_drawer WHERE type='salary_advance'").fetchall()
                assert adv_cd and adv_cd[0]["amount"] == -5000.0

            # record with 2 off-days: 30000 + 2000 - 5000 = 27000 payable
            rec = tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-09", "off_days_taken": 2}).json()
            assert rec["advances_total"] == 5000.0
            assert rec["final_payable"] == 27000.0

            # month sheet totals + paid status tracking
            sheet = tc.get("/api/salary/month?month=2026-09").json()
            emp = [e for e in sheet["employees"] if e["id"] == eid][0]
            assert emp["advances_total"] == 5000.0
            assert sheet["totals"]["payroll_cost"] == 32000.0
            assert sheet["totals"]["final_payable_total"] == 27000.0

            # pay -> status paid + cash out 27000
            paid = tc.post(f"/api/salary/records/{rec['id']}/pay",
                           json={"payment_method": "cash"}).json()
            assert paid["status"] == "paid" and paid["paid_date"]
            with db.conn() as c:
                pay_cd = c.execute(
                    "SELECT amount FROM cash_drawer WHERE type='salary_payment' AND reference_id=?",
                    (rec["id"],)).fetchall()
                assert pay_cd and pay_cd[0]["amount"] == -27000.0

            # double pay rejected
            assert tc.post(f"/api/salary/records/{rec['id']}/pay", json={}).status_code == 400

            # history: one paid record for 2026-09
            hist = tc.get(f"/api/salary/history/{eid}").json()["history"]
            assert len(hist) == 1
            assert hist[0]["month"] == "2026-09" and hist[0]["status"] == "paid"
            assert hist[0]["final_payable"] == 27000.0

            # delete record -> expense + cash entries removed
            assert tc.delete(f"/api/salary/records/{rec['id']}").status_code == 200
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM expenses WHERE id=?",
                              (rec["expense_id"],)).fetchone()["n"]
                assert n == 0
                n = c.execute(
                    "SELECT COUNT(*) n FROM cash_drawer WHERE reference_type='salary_record'"
                ).fetchone()["n"]
                assert n == 0

            # delete advance -> cash entry removed
            aids = [a["id"] for a in tc.get(
                f"/api/salary/advances?employee_id={eid}").json()["advances"]]
            assert len(aids) == 1
            assert tc.delete(f"/api/salary/advances/{aids[0]}").status_code == 200
            with db.conn() as c:
                n = c.execute(
                    "SELECT COUNT(*) n FROM cash_drawer WHERE type='salary_advance'"
                ).fetchone()["n"]
                assert n == 0
    finally:
        cleanup(d)


def test_salary_deducted_from_gross_profit_in_earnings():
    """End-to-end: salary expense shows up in operating expenses so
    Actual Earnings = gross profit + extra income - (expenses incl. salary)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import shop
        with TestClient(app) as tc:
            login_client(tc)
            # baseline: sample data has no expenses
            base = shop.get_actual_earnings("2026-09")
            assert base["operating_expenses"] == 0

            eid = _add_staff(tc, "Ali", 30000)
            tc.post("/api/extra-sales", json={
                "item_name": "Raddi", "quantity": 10, "unit_price": 100,
                "payment_method": "cash", "date": "2026-09-08"})
            tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-09", "off_days_taken": 2})

            ae = shop.get_actual_earnings("2026-09")
            # 30000 + 2000 extra-day pay = 32000 posted as operating expense
            assert ae["operating_expenses"] == 32000.0
            assert ae["extra_sales_income"] == 1000.0
            assert ae["actual_earnings"] == round(
                ae["gross_profit"] + 1000.0 - 32000.0, 2)

            # P&L: Salaries category row present
            pnl = shop.get_pnl("2026-09")
            assert pnl["expenses"] == 32000.0
            sal = [e for e in pnl["expenses_by_category"] if e["category"] == "Salaries"]
            assert sal and sal[0]["total"] == 32000.0
            assert pnl["net_profit"] == round(pnl["gross_profit"] + 1000.0 - 32000.0, 2)
    finally:
        cleanup(d)


def test_salary_change_resyncs_draft_records():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            eid = _add_staff(tc, "Change Me", 30000)
            rec = tc.post("/api/salary/records", json={
                "employee_id": eid, "month": "2026-09", "off_days_taken": 0}).json()
            assert rec["final_payable"] == 34000.0

            # salary raise -> draft record + expense follow
            assert tc.put(f"/api/salary/employees/{eid}",
                          json={"monthly_salary": 36000}).status_code == 200
            with db.conn() as c:
                row = c.execute("SELECT * FROM salary_records WHERE id=?",
                                (rec["id"],)).fetchone()
                assert row["monthly_salary"] == 36000.0
                assert row["final_payable"] == 40800.0  # 36000 + 4800
                exp = c.execute("SELECT amount FROM expenses WHERE id=?",
                                (rec["expense_id"],)).fetchone()
                assert exp["amount"] == 40800.0

            # paid records keep their snapshot
            tc.post(f"/api/salary/records/{rec['id']}/pay", json={})
            tc.put(f"/api/salary/employees/{eid}", json={"monthly_salary": 40000})
            with db.conn() as c:
                row = c.execute("SELECT monthly_salary, final_payable FROM salary_records WHERE id=?",
                                (rec["id"],)).fetchone()
                assert row["monthly_salary"] == 36000.0  # unchanged (paid)
    finally:
        cleanup(d)


def test_cashier_cannot_manage_salary():
    """Salary endpoints are manager-only (RBAC)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        from app.security import hash_pin
        with db.conn() as c:
            c.execute("DELETE FROM employees WHERE id=300")
            c.execute(
                "INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                "VALUES(300, 'Cash', 'cashier', NULL, ?, 1)", (hash_pin("1234"),))
        with TestClient(app) as tc:
            r = tc.post("/api/login/staff", json={"employee_id": 300, "pin": "1234"})
            assert r.status_code == 200
            assert tc.get("/api/salary/month").status_code == 403
            assert tc.post("/api/salary/employees", json={
                "name": "X", "monthly_salary": 1}).status_code == 403
    finally:
        cleanup(d)


# ------------------------------------------------------------------
# Frontend wiring (static checks — same style as previous versions)
# ------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    import re
    return re.sub(r"//[^\n]*", "", src)


def test_frontend_pages_registered_and_wired():
    """Both new pages exist, are imported by app.js, live in the Billing app
    nav, and call the real API endpoints (no dead wiring)."""
    app_js = (JS / "app.js").read_text(encoding="utf-8")
    assert "extra-sales-page.js" in app_js
    assert "salary-page.js" in app_js

    shell_js = (JS / "core" / "shell.js").read_text(encoding="utf-8")
    assert "'/bills/extra-sales'" in shell_js
    assert "'/bills/salary'" in shell_js

    xs_js = _strip_comments((JS / "pages" / "extra-sales-page.js").read_text(encoding="utf-8"))
    assert "route('/bills/extra-sales'" in xs_js
    for endpoint in ("/api/extra-sales", "/api/extra-sales/summary"):
        assert endpoint in xs_js, endpoint
    assert "apiDelete(`/api/extra-sales/${" in xs_js

    sal_js = _strip_comments((JS / "pages" / "salary-page.js").read_text(encoding="utf-8"))
    assert "route('/bills/salary'" in sal_js
    for endpoint in ("/api/salary/month", "/api/salary/records",
                     "/api/salary/advances", "/api/salary/history/"):
        assert endpoint in sal_js, endpoint
    assert "/api/salary/records/${rec.id}/pay" in sal_js


def test_frontend_report_pages_read_extra_sales_fields():
    """The pages read the fields the backend actually returns (contract)."""
    ae_js = _strip_comments((JS / "pages" / "actual-earnings-page.js").read_text(encoding="utf-8"))
    assert "r.extra_sales_income" in ae_js
    pnl_js = _strip_comments((JS / "pages" / "reports-financial.js").read_text(encoding="utf-8"))
    assert "r.other_income" in pnl_js
    assert "r.inflows.extra_sales_cash" in pnl_js
