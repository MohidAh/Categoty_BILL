"""v8.18.13 — Staff Salary Management.

Client requirements (implemented here):
  * Employees with a fixed monthly salary (employees.monthly_salary).
  * 4 paid/allowed off-days per month. If an employee takes FEWER, the
    remaining days count as extra working days paid at Salary / 30 per day.
  * Salary advances taken during the month are recorded and deducted.
  * Final payable = monthly salary + extra-working-day pay - advances.
  * Staff salary is AUTOMATICALLY posted under Operating Expenses
    (category 'Salaries'), so it is deducted from Gross Profit when
    calculating Actual Earnings / Net Profit — no manual expense entry.
  * Monthly salary history/report per employee (salary_records table).

Accounting model (avoids double counting):
  * The salary record's FULL COST (monthly salary + extra-day pay) is ONE
    operating expense row, linked via salary_records.expense_id and kept in
    sync on every recompute. It exists as soon as the record is saved, so
    the month's P&L shows the true payroll cost even before payout.
  * An ADVANCE is a cash prepayment, NOT an expense: it leaves the drawer
    when given (cash_drawer row) and reduces the final cash payment at
    month end. Total cash out = advances + final payment = full cost, and
    total expense = full cost — nothing is counted twice.
"""
from datetime import datetime

from . import db
from .db import conn

DEFAULT_ALLOWED_OFF_DAYS = 4   # paid off-days allowed per month (client spec)
SALARY_CATEGORY = "Salaries"   # seeded expense category
DAYS_BASIS = 30                # per-day rate = monthly salary / 30 (client spec)


# ------------------------------------------------------------------
# Pure computation
# ------------------------------------------------------------------

def compute_salary(monthly_salary, allowed_off_days, off_days_taken, advances_total) -> dict:
    """Pure salary math — no DB. Used for live previews and record saves.

    extra_working_days = max(0, allowed_off_days - off_days_taken)
    extra_day_pay      = extra_working_days * (monthly_salary / 30)
    final_payable      = monthly_salary + extra_day_pay - advances_total
    """
    monthly_salary = float(monthly_salary or 0)
    if monthly_salary < 0:
        monthly_salary = 0.0
    allowed = int(allowed_off_days or 0)
    taken = int(off_days_taken or 0)
    if taken < 0:
        taken = 0
    per_day_rate = monthly_salary / DAYS_BASIS if monthly_salary > 0 else 0.0
    extra_working_days = max(0, allowed - taken)
    extra_day_pay = round(extra_working_days * per_day_rate, 2)
    gross = round(monthly_salary + extra_day_pay, 2)
    advances = round(float(advances_total or 0), 2)
    final_payable = round(gross - advances, 2)
    return {
        "per_day_rate": round(per_day_rate, 2),
        "extra_working_days": extra_working_days,
        "extra_day_pay": extra_day_pay,
        "gross_salary": gross,
        "advances_total": advances,
        "final_payable": final_payable,
    }


# ------------------------------------------------------------------
# Internal helpers (all take an open cursor `c`)
# ------------------------------------------------------------------

def _valid_month(month: str) -> bool:
    if not month or not isinstance(month, str):
        return False
    parts = month.split("-")
    if len(parts) != 2:
        return False
    try:
        y, m = int(parts[0]), int(parts[1])
        return 1900 <= y <= 2200 and 1 <= m <= 12
    except ValueError:
        return False


def _month_of(date_str: str) -> str:
    """'2026-09-14' -> '2026-09' ('' for garbage)."""
    if not date_str:
        return ""
    return date_str[:7] if len(date_str) >= 7 else ""


def _salary_category_id(c) -> int:
    """Resolve (or create) the 'Salaries' expense category id."""
    row = c.execute(
        "SELECT id FROM expense_categories WHERE name=? ORDER BY id LIMIT 1",
        (SALARY_CATEGORY,),
    ).fetchone()
    if row:
        return row["id"]
    return c.execute(
        "INSERT INTO expense_categories(name, is_fixed, active, sort_order) VALUES(?,?,1,2)",
        (SALARY_CATEGORY, 1),
    ).lastrowid


def _month_advances_total(c, employee_id: int, month: str) -> float:
    v = c.execute(
        "SELECT COALESCE(SUM(amount), 0) v FROM salary_advances "
        "WHERE employee_id=? AND strftime('%Y-%m', date)=?",
        (employee_id, month),
    ).fetchone()["v"]
    return float(v or 0)


def _recompute_record(c, record_id: int) -> dict:
    """Refresh one salary record's derived columns + sync its linked expense.

    Called after off-day edits, salary changes, and advance add/delete so
    the record (and the P&L expense) always reflect the current truth.
    Returns the refreshed record as a dict.
    """
    rec = c.execute("SELECT * FROM salary_records WHERE id=?", (record_id,)).fetchone()
    if not rec:
        return {}
    rec = dict(rec)
    advances = _month_advances_total(c, rec["employee_id"], rec["month"])
    comp = compute_salary(rec["monthly_salary"], rec["allowed_off_days"],
                          rec["off_days_taken"], advances)
    c.execute(
        "UPDATE salary_records SET per_day_rate=?, extra_working_days=?, "
        "extra_day_pay=?, advances_total=?, final_payable=? WHERE id=?",
        (comp["per_day_rate"], comp["extra_working_days"], comp["extra_day_pay"],
         comp["advances_total"], comp["final_payable"], record_id),
    )
    _sync_expense(c, record_id)
    out = dict(rec)
    out.update(comp)
    return out


def _sync_expense(c, record_id: int) -> None:
    """Create/update the linked operating expense for a salary record.

    Expense amount = full cost (monthly salary + extra-day pay) — NOT the
    final payable — because advances are already cash out (prepayments) and
    will be settled inside the final payment. This keeps:
        total expense for month == total payroll cost
        total cash out == advances + final payments == payroll cost
    """
    rec = c.execute(
        "SELECT sr.*, e.name AS employee_name FROM salary_records sr "
        "LEFT JOIN employees e ON e.id = sr.employee_id WHERE sr.id=?",
        (record_id,),
    ).fetchone()
    if not rec:
        return
    cost = round(float(rec["monthly_salary"] or 0) + float(rec["extra_day_pay"] or 0), 2)
    name = rec["employee_name"] or f"Employee #{rec['employee_id']}"
    description = f"Staff salary — {name} ({rec['month']})"
    # Expense date: first day of the record's month so it lands in the month
    expense_date = f"{rec['month']}-01"
    cat_id = _salary_category_id(c)
    if rec["expense_id"]:
        cur = c.execute(
            "UPDATE expenses SET category=?, category_id=?, amount=?, description=?, "
            "date=?, expense_type='operating' WHERE id=?",
            (SALARY_CATEGORY, cat_id, cost, description, expense_date, rec["expense_id"]),
        )
        if cur.rowcount == 0:
            # linked expense was deleted from the Expenses page — recreate
            eid = c.execute(
                "INSERT INTO expenses(category, category_id, amount, description, "
                "payment_method, date, expense_type) VALUES(?,?,?,?,?,?, 'operating')",
                (SALARY_CATEGORY, cat_id, cost, description,
                 rec["payment_method"] or "cash", expense_date),
            ).lastrowid
            c.execute("UPDATE salary_records SET expense_id=? WHERE id=?", (eid, record_id))
    else:
        eid = c.execute(
            "INSERT INTO expenses(category, category_id, amount, description, "
            "payment_method, date, expense_type) VALUES(?,?,?,?,?,?, 'operating')",
            (SALARY_CATEGORY, cat_id, cost, description,
             rec["payment_method"] or "cash", expense_date),
        ).lastrowid
        c.execute("UPDATE salary_records SET expense_id=? WHERE id=?", (eid, record_id))


# ------------------------------------------------------------------
# Employees (salary side)
# ------------------------------------------------------------------

def add_salary_employee(name: str, phone: str = "", role: str = "cashier",
                        monthly_salary: float = 0) -> int:
    """Add an employee with a fixed monthly salary (salary-page quick add)."""
    if not name or not name.strip():
        raise ValueError("name is required")
    if role not in ("cashier", "manager", "admin"):
        role = "cashier"
    salary = float(monthly_salary or 0)
    if salary < 0:
        raise ValueError("monthly_salary cannot be negative")
    with conn() as c:
        return c.execute(
            "INSERT INTO employees(name, phone, role, monthly_salary) VALUES(?,?,?,?)",
            (name.strip(), phone, role, salary),
        ).lastrowid


def set_employee_salary(employee_id: int, monthly_salary: float) -> bool:
    """Set an employee's fixed monthly salary. Recomputes their DRAFT salary
    records (paid records keep their historical snapshot)."""
    salary = float(monthly_salary or 0)
    if salary < 0:
        raise ValueError("monthly_salary cannot be negative")
    with conn() as c:
        cur = c.execute(
            "UPDATE employees SET monthly_salary=? WHERE id=?", (salary, employee_id)
        )
        if cur.rowcount == 0:
            return False
        # Refresh draft records for this employee (paid ones are history)
        draft_ids = [r["id"] for r in c.execute(
            "SELECT id FROM salary_records WHERE employee_id=? AND status='draft'",
            (employee_id,),
        ).fetchall()]
        for rid in draft_ids:
            c.execute(
                "UPDATE salary_records SET monthly_salary=? WHERE id=?", (salary, rid)
            )
            _recompute_record(c, rid)
    return True


# ------------------------------------------------------------------
# Salary records (one per employee per month)
# ------------------------------------------------------------------

def upsert_salary_record(employee_id: int, month: str, off_days_taken: int = 0,
                         allowed_off_days: int = DEFAULT_ALLOWED_OFF_DAYS,
                         notes: str = "") -> dict:
    """Create or update the salary record for employee+month.

    Saving (or editing off-days) automatically posts/updates the linked
    operating expense, so payroll is deducted from Gross Profit in Actual
    Earnings / P&L without any manual expense entry.
    """
    if not _valid_month(month):
        raise ValueError("month must be YYYY-MM")
    off_days_taken = int(off_days_taken or 0)
    if off_days_taken < 0:
        raise ValueError("off_days_taken cannot be negative")
    allowed_off_days = int(allowed_off_days if allowed_off_days is not None
                           else DEFAULT_ALLOWED_OFF_DAYS)
    if allowed_off_days < 0:
        allowed_off_days = 0
    with conn() as c:
        emp = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not emp:
            raise ValueError("employee not found")
        existing = c.execute(
            "SELECT * FROM salary_records WHERE employee_id=? AND month=?",
            (employee_id, month),
        ).fetchone()
        advances = _month_advances_total(c, employee_id, month)
        salary = float(emp["monthly_salary"] or 0)
        comp = compute_salary(salary, allowed_off_days, off_days_taken, advances)
        if existing:
            c.execute(
                "UPDATE salary_records SET monthly_salary=?, allowed_off_days=?, "
                "off_days_taken=?, notes=? WHERE id=?",
                (salary, allowed_off_days, off_days_taken, notes, existing["id"]),
            )
            rec = _recompute_record(c, existing["id"])
        else:
            rid = c.execute(
                "INSERT INTO salary_records(employee_id, month, monthly_salary, "
                "allowed_off_days, off_days_taken, per_day_rate, extra_working_days, "
                "extra_day_pay, advances_total, final_payable, notes) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (employee_id, month, salary, allowed_off_days, off_days_taken,
                 comp["per_day_rate"], comp["extra_working_days"], comp["extra_day_pay"],
                 comp["advances_total"], comp["final_payable"], notes),
            ).lastrowid
            _sync_expense(c, rid)
            rec = dict(c.execute("SELECT * FROM salary_records WHERE id=?", (rid,)).fetchone())
    return rec


def pay_salary_record(record_id: int, payment_method: str = "cash",
                      date_str: str = None) -> dict:
    """Mark a salary record PAID and record the final cash payment.

    The expense already exists (posted at record save time); paying moves
    the final_payable amount out of the drawer (cash) and stamps paid_date.
    """
    if payment_method not in ("cash", "bank", "card", "online"):
        payment_method = "cash"
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        rec = c.execute("SELECT * FROM salary_records WHERE id=?", (record_id,)).fetchone()
        if not rec:
            raise ValueError("salary record not found")
        if rec["status"] == "paid":
            raise ValueError("already paid")
        rec = dict(rec)
        payable = float(rec["final_payable"] or 0)
        c.execute(
            "UPDATE salary_records SET status='paid', payment_method=?, paid_date=? "
            "WHERE id=?",
            (payment_method, date_str, record_id),
        )
        if payment_method == "cash" and payable != 0:
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('salary_payment', ?, ?, ?, 'salary_record')",
                (-payable, f"Salary payment: {rec['month']} (final payable)", record_id),
            )
        # keep the expense's method in sync with how it was actually paid
        if rec["expense_id"]:
            c.execute(
                "UPDATE expenses SET payment_method=? WHERE id=?",
                (payment_method, rec["expense_id"]),
            )
        out = dict(c.execute("SELECT * FROM salary_records WHERE id=?", (record_id,)).fetchone())
    return out


def delete_salary_record(record_id: int) -> bool:
    """Delete a salary record + its linked expense + its cash entries."""
    with conn() as c:
        rec = c.execute("SELECT * FROM salary_records WHERE id=?", (record_id,)).fetchone()
        if not rec:
            return False
        if rec["expense_id"]:
            c.execute("DELETE FROM expenses WHERE id=?", (rec["expense_id"],))
        c.execute(
            "DELETE FROM cash_drawer WHERE reference_type='salary_record' AND reference_id=?",
            (record_id,),
        )
        c.execute("DELETE FROM salary_records WHERE id=?", (record_id,))
    return True


def get_salary_month(month: str = "") -> dict:
    """The month sheet: every active employee + their record/advances/math."""
    month = month or datetime.now().strftime("%Y-%m")
    if not _valid_month(month):
        raise ValueError("month must be YYYY-MM")
    with conn() as c:
        emps = c.execute(
            "SELECT * FROM employees WHERE active=1 ORDER BY name"
        ).fetchall()
        out = []
        for e in emps:
            e = dict(e)
            rec = c.execute(
                "SELECT * FROM salary_records WHERE employee_id=? AND month=?",
                (e["id"], month),
            ).fetchone()
            rec = dict(rec) if rec else None
            advances = c.execute(
                "SELECT * FROM salary_advances WHERE employee_id=? "
                "AND strftime('%Y-%m', date)=? ORDER BY date DESC, id DESC",
                (e["id"], month),
            ).fetchall()
            advances = [dict(a) for a in advances]
            advances_total = round(sum(a["amount"] for a in advances), 2)
            salary = float(e["monthly_salary"] or 0)
            if rec:
                computed = {
                    "per_day_rate": rec["per_day_rate"],
                    "extra_working_days": rec["extra_working_days"],
                    "extra_day_pay": rec["extra_day_pay"],
                    "gross_salary": round(salary + float(rec["extra_day_pay"] or 0), 2),
                    "advances_total": float(rec["advances_total"] or 0),
                    "final_payable": float(rec["final_payable"] or 0),
                }
                live = compute_salary(salary, rec["allowed_off_days"],
                                      rec["off_days_taken"], advances_total)
                # advance edits since last save → flag "needs recompute"
                computed["needs_save"] = (abs(live["final_payable"] -
                                              float(rec["final_payable"] or 0)) >= 0.01)
            else:
                computed = compute_salary(salary, DEFAULT_ALLOWED_OFF_DAYS, 0, advances_total)
                computed["needs_save"] = False
            out.append({
                "id": e["id"], "name": e["name"], "phone": e["phone"],
                "role": e["role"], "monthly_salary": salary,
                "record": rec, "advances": advances,
                "advances_total": advances_total, "computed": computed,
            })
        totals = {
            "employees": len(out),
            "with_salary": sum(1 for x in out if x["monthly_salary"] > 0),
            "payroll_cost": round(sum(x["computed"]["gross_salary"] for x in out), 2),
            "advances_total": round(sum(x["advances_total"] for x in out), 2),
            "final_payable_total": round(sum(x["computed"]["final_payable"] for x in out), 2),
            "paid_total": round(sum(
                x["computed"]["final_payable"] for x in out
                if x["record"] and x["record"]["status"] == "paid"), 2),
        }
    return {"month": month, "allowed_off_days_default": DEFAULT_ALLOWED_OFF_DAYS,
            "days_basis": DAYS_BASIS, "employees": out, "totals": totals}


def get_salary_history(employee_id: int) -> list:
    """Monthly salary history for one employee (newest first)."""
    with conn() as c:
        emp = c.execute("SELECT id, name, monthly_salary FROM employees WHERE id=?",
                        (employee_id,)).fetchone()
        if not emp:
            raise ValueError("employee not found")
        rows = c.execute(
            "SELECT * FROM salary_records WHERE employee_id=? ORDER BY month DESC",
            (employee_id,),
        ).fetchall()
        history = []
        for r in rows:
            r = dict(r)
            adv = c.execute(
                "SELECT COALESCE(SUM(amount), 0) v FROM salary_advances "
                "WHERE employee_id=? AND strftime('%Y-%m', date)=?",
                (employee_id, r["month"]),
            ).fetchone()["v"]
            r["advances_total"] = round(float(adv or 0), 2)
            history.append(r)
    return history


# ------------------------------------------------------------------
# Advances
# ------------------------------------------------------------------

def record_advance(employee_id: int, amount: float, date_str: str = None,
                   description: str = "", payment_method: str = "cash") -> int:
    """Record a salary advance taken by an employee.

    The advance leaves the drawer immediately (cash) and is deducted from
    the FINAL PAYABLE at month end. It is NOT an expense — the salary
    record's expense already covers the full payroll cost.
    """
    amount = float(amount or 0)
    if amount <= 0:
        raise ValueError("amount must be > 0")
    if payment_method not in ("cash", "bank", "card", "online"):
        payment_method = "cash"
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        emp = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not emp:
            raise ValueError("employee not found")
        aid = c.execute(
            "INSERT INTO salary_advances(employee_id, amount, date, description) "
            "VALUES(?,?,?,?)",
            (employee_id, round(amount, 2), date_str, description),
        ).lastrowid
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('salary_advance', ?, ?, ?, 'salary_advance')",
                (-round(amount, 2), f"Salary advance: {emp['name']}", aid),
            )
        # keep any existing record for that month in sync
        rec = c.execute(
            "SELECT id FROM salary_records WHERE employee_id=? AND month=?",
            (employee_id, _month_of(date_str)),
        ).fetchone()
        if rec:
            _recompute_record(c, rec["id"])
    return aid


def list_advances(month: str = "", employee_id: int = None, limit: int = 100) -> list:
    """List salary advances (newest first), optional month/employee filter."""
    limit = min(max(1, limit), 500)
    with conn() as c:
        sql = ("SELECT sa.*, e.name AS employee_name FROM salary_advances sa "
               "LEFT JOIN employees e ON e.id = sa.employee_id WHERE 1=1")
        args: list = []
        if month:
            sql += " AND strftime('%Y-%m', sa.date)=?"
            args.append(month)
        if employee_id is not None:
            sql += " AND sa.employee_id=?"
            args.append(employee_id)
        sql += " ORDER BY sa.date DESC, sa.id DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_advance(advance_id: int) -> bool:
    """Delete an advance + its cash drawer entry; resync the month record."""
    with conn() as c:
        adv = c.execute("SELECT * FROM salary_advances WHERE id=?", (advance_id,)).fetchone()
        if not adv:
            return False
        c.execute("DELETE FROM salary_advances WHERE id=?", (advance_id,))
        c.execute(
            "DELETE FROM cash_drawer WHERE reference_type='salary_advance' AND reference_id=?",
            (advance_id,),
        )
        rec = c.execute(
            "SELECT id FROM salary_records WHERE employee_id=? AND month=?",
            (adv["employee_id"], _month_of(adv["date"] or "")),
        ).fetchone()
        if rec:
            _recompute_record(c, rec["id"])
    return True
