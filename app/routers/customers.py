"""Auto-generated router module — extracted from main.py Phase 1."""
import os, json, time, sqlite3, re, io, csv, secrets, hashlib, traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any, Optional

from .. import db
from .. import shop as shop_mod
from .. import insights
from .. import trends as trends_mod
from .. import extract
from .. import reports
from .. import pos_extra
from .. import pos_import
from .. import crypto as crypto_mod
from .. import jobs as jobs_mod
from ..config import BACKUPS, BASE, PAGE_SIZE, PAGES, UPLOADS
from ..export import export_bills, export_insights
from ..ingest import render_pages, save_upload
from ..validate import detect_duplicate, pieces, validate
from ..security import (
    hash_password, verify_password, ensure_password,
    is_logged_in, get_session, get_session_role,
    create_session, delete_session,
    check_login_throttle, record_failed_login,
    SESSION_DAYS,
)

router = APIRouter()

# Backward-compat aliases
_hash_password = hash_password
_verify_password = verify_password
_ensure_password = ensure_password
_is_logged_in = is_logged_in
_get_session = get_session
_get_session_role = get_session_role
_create_session = create_session
_delete_session = delete_session
_check_login_throttle = check_login_throttle
_record_failed_login = record_failed_login

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_FILES = 100


class CustomerImportIn(BaseModel):
    rows: list[dict]


class CustomerPaymentIn(BaseModel):
    customer_id: int
    customer_name: str = ""
    amount: float
    payment_method: str = "cash"
    notes: str = ""
    client_uuid: Optional[str] = None  # v3.1: idempotency key


class CustomerUpdateIn(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None


class ExpenseIn(BaseModel):
    category: str
    amount: float
    description: str = ""
    payment_method: str = "cash"
    # v4.0 Phase 2 additions (all optional for backward compat)
    category_id: Optional[int] = None
    expense_type: str = "operating"  # 'operating' | 'owner_draw'
    date: Optional[str] = None  # YYYY-MM-DD; defaults to today


@router.get("/api/customers")
def list_customers(q: str = "", page: int = 1, page_size: int = 50,
                   sort_by: str = "", sort_order: str = "desc") -> Any:
    if q:
        return {"customers": shop_mod.search_customers(q)}
    # v8.15.0: Dynamic sort
    order_clause = db.validate_sort(sort_by, sort_order, {
        "name": "name",
        "phone": "phone",
        "spent": "total_spent",
        "credit": "total_credit",
        "points": "loyalty_points",
        "created": "created_at",
    }, default="total_spent DESC, id DESC")
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM customers WHERE deleted_at IS NULL").fetchone()["n"]
        # v8.19.1: clamp the page (last-page deletion / filter shrink)
        page = db.clamp_page(page, total, page_size)
        rows = c.execute(
            f"SELECT * FROM customers WHERE deleted_at IS NULL ORDER BY {order_clause} LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    return {"customers": [dict(r) for r in rows], "total": total, "page": page, "pages_total": (total + page_size - 1) // page_size}


@router.get("/api/customers/rfm")
def rfm_analysis() -> Any:
    """RFM segmentation: Recency, Frequency, Monetary — quintile scores 1-5."""
    from datetime import datetime
    with db.conn() as c:
        customers = c.execute(
            "SELECT cust.id, cust.name, cust.phone, cust.total_spent, "
            "COUNT(DISTINCT s.id) AS frequency, "
            "MAX(s.created_at) AS last_purchase "
            "FROM customers cust LEFT JOIN sales s ON s.customer_id = cust.id "
            f"WHERE cust.deleted_at IS NULL AND ({db.VALID_SALE_FILTER} OR s.id IS NULL) "
            "GROUP BY cust.id HAVING frequency > 0 ORDER BY cust.total_spent DESC"
        ).fetchall()
        if not customers:
            return {"segments": {}, "customers": []}
        # Calculate RFM scores (quintiles)
        recencies = []
        frequencies = []
        monetaries = []
        now = datetime.now()
        for cust in customers:
            try:
                days = (now - datetime.strptime(cust["last_purchase"][:19], "%Y-%m-%d %H:%M:%S")).days
            except Exception:
                days = 999
            recencies.append(days)
            frequencies.append(cust["frequency"])
            monetaries.append(cust["total_spent"])
        def quintile(values, reverse=False):
            sorted_vals = sorted(values, reverse=reverse)
            n = len(sorted_vals)
            scores = {}
            for i, v in enumerate(values):
                rank = sorted_vals.index(v)
                scores[i] = min(5, max(1, (rank // max(1, n // 5)) + 1))
            return scores
        r_scores = quintile(recencies, reverse=True)  # lower days = higher score
        f_scores = quintile(frequencies)
        m_scores = quintile(monetaries)
        segments = {"Champions": [], "Loyal": [], "At-Risk": [], "Sleeping": [], "New": []}
        results = []
        for i, cust in enumerate(customers):
            r, f, m = r_scores[i], f_scores[i], m_scores[i]
            avg_gap = (recencies[i] / max(1, frequencies[i])) if frequencies[i] > 0 else 999
            if r >= 4 and f >= 4 and m >= 4:
                seg = "Champions"
            elif f >= 3 and m >= 3:
                seg = "Loyal"
            elif r <= 2 and f >= 2:
                seg = "At-Risk"
            elif r <= 2 and f <= 2:
                seg = "Sleeping"
            else:
                seg = "New"
            entry = {"id": cust["id"], "name": cust["name"], "phone": cust["phone"],
                     "r": r, "f": f, "m": m, "segment": seg,
                     "recency_days": recencies[i], "frequency": frequencies[i],
                     "monetary": monetaries[i], "total_spent": cust["total_spent"]}
            segments[seg].append(entry)
            results.append(entry)
    return {"segments": {k: len(v) for k, v in segments.items()}, "customers": results}


@router.get("/api/customers/birthdays")
def birthday_list(month: int = None) -> Any:
    """List customers with birthdays in given month (or current month)."""
    if month is None:
        month = datetime.now().month
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, name, phone, birthday FROM customers "
            "WHERE birthday IS NOT NULL AND deleted_at IS NULL AND CAST(substr(birthday, 6, 2) AS INTEGER) = ? "
            "ORDER BY substr(birthday, 9, 2)",
            (month,),
        ).fetchall()
    return {"customers": [dict(r) for r in rows], "month": month}


@router.get("/api/customers/ar-aging")
def ar_aging() -> Any:
    """Accounts Receivable aging report — customer urdhaar by age bucket."""
    with db.conn() as c:
        customers = c.execute(
            "SELECT id, name, phone, total_credit, credit_limit, terms_days FROM customers "
            "WHERE total_credit > 0 AND deleted_at IS NULL ORDER BY total_credit DESC"
        ).fetchall()
        buckets = {"0-30": [], "31-60": [], "61-90": [], "90+": []}
        totals = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for cust in customers:
            # Find oldest unpaid credit sale
            oldest = c.execute(
                "SELECT MIN(created_at) AS oldest FROM sales WHERE customer_id=? AND payment_status='credit'",
                (cust["id"],),
            ).fetchone()
            if not oldest or not oldest["oldest"]:
                continue
            from datetime import datetime
            days_old = (datetime.now() - datetime.strptime(oldest["oldest"], "%Y-%m-%d %H:%M:%S")).days
            entry = {
                "customer_id": cust["id"], "name": cust["name"], "phone": cust["phone"],
                "outstanding": cust["total_credit"],
                "credit_limit": cust["credit_limit"],
                "terms_days": cust["terms_days"],
                "days_old": days_old,
                "oldest_sale": oldest["oldest"],
            }
            if days_old <= 30:
                buckets["0-30"].append(entry); totals["0-30"] += cust["total_credit"]
            elif days_old <= 60:
                buckets["31-60"].append(entry); totals["31-60"] += cust["total_credit"]
            elif days_old <= 90:
                buckets["61-90"].append(entry); totals["61-90"] += cust["total_credit"]
            else:
                buckets["90+"].append(entry); totals["90+"] += cust["total_credit"]
    grand_total = sum(totals.values())
    return {"buckets": buckets, "totals": totals, "grand_total": grand_total}




@router.get("/api/customers/{cid}")
def get_customer_detail(cid: int) -> Any:
    c = shop_mod.get_customer(cid)
    if not c:
        raise HTTPException(404, "customer not found")
    return c


@router.post("/api/customers")
def add_customer(name: str = "", phone: str = "") -> Any:
    cid = shop_mod.get_or_create_customer(name, phone)
    return {"id": cid}


@router.put("/api/customers/{cid}")
def update_customer_route(cid: int, payload: CustomerUpdateIn) -> Any:
    ok = shop_mod.update_customer(cid, payload.name, payload.phone, payload.address)
    if not ok:
        raise HTTPException(404, "customer not found or no fields to update")
    db.log_activity("customer_updated", "customer", cid,
                    f"Customer {cid} updated",
                    {"name": payload.name, "phone": payload.phone})
    return {"ok": True}


@router.delete("/api/customers/{cid}")
def delete_customer_route(cid: int) -> Any:
    ok = shop_mod.delete_customer(cid)
    if not ok:
        raise HTTPException(404, "customer not found")
    db.log_activity("customer_deleted", "customer", cid, f"Customer {cid} deleted", {})
    return {"ok": True}


@router.get("/api/customers/{cid}/loyalty-redemptions")
def list_customer_redemptions(cid: int) -> Any:
    return {"redemptions": shop_mod.list_loyalty_redemptions(cid)}


@router.get("/api/loyalty/redemptions")
def list_all_redemptions(limit: int = 50) -> Any:
    return {"redemptions": shop_mod.list_loyalty_redemptions(None, limit)}


@router.post("/api/customers/import")
def import_customers_route(payload: CustomerImportIn) -> Any:
    res = shop_mod.import_customers_csv(payload.rows)
    db.log_activity("customers_imported", "customer", 0,
                    f"Imported {res['added']} customers (skipped {res['skipped']})",
                    res)
    return res


# ------------------------------------------------------------------
# Expenses
# ------------------------------------------------------------------


@router.get("/api/expenses")
def list_expenses(date: str = "", limit: int = 50, month: str = "",
                  category_id: int = None, expense_type: str = "",
                  page: int = 0, page_size: int = 0) -> Any:
    # Lazy recurring generation: run before listing so newly-due recurring
    # expenses appear in the current month's list.
    shop_mod.generate_recurring_expenses()
    result = shop_mod.get_expenses(date, limit, month, category_id, expense_type, page, page_size)
    # v8.4: get_expenses now returns a paginated dict when page/page_size are used,
    # or a plain list when not. Normalize the response.
    if isinstance(result, dict):
        return result
    return {"expenses": result}


@router.post("/api/expenses")
def add_expense_route(payload: ExpenseIn) -> Any:
    eid = shop_mod.add_expense(
        payload.category, payload.amount, payload.description, payload.payment_method,
        category_id=payload.category_id, expense_type=payload.expense_type,
        date_str=payload.date,
    )
    db.log_activity("expense_added", "expense", eid,
                    f"Expense: {payload.category} — Rs {payload.amount:.0f} ({payload.expense_type})",
                    {"category": payload.category, "amount": payload.amount,
                     "expense_type": payload.expense_type, "category_id": payload.category_id})
    return {"id": eid}


@router.delete("/api/expenses/{eid}")
def delete_expense(eid: int) -> Any:
    with db.conn() as c:
        c.execute("DELETE FROM expenses WHERE id=?", (eid,))
    db.log_activity("expense_deleted", "expense", eid, f"Deleted expense #{eid}", {"eid": eid})
    return {"ok": True}


@router.get("/api/expenses/summary")
def expense_summary(month: str = "") -> Any:
    shop_mod.generate_recurring_expenses()
    return shop_mod.get_expense_summary(month)


# ------------------------------------------------------------------
# v4.0 Phase 2 — Expense Categories CRUD
# ------------------------------------------------------------------

class ExpenseCategoryIn(BaseModel):
    name: str
    is_fixed: bool = False
    budget_monthly: float = 0
    sort_order: int = 0
    active: bool = True


class ExpenseCategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_fixed: Optional[bool] = None
    budget_monthly: Optional[float] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@router.get("/api/expense-categories")
def list_expense_categories_route(active_only: bool = False) -> Any:
    return {"categories": shop_mod.list_expense_categories(active_only)}


@router.post("/api/expense-categories")
def add_expense_category_route(payload: ExpenseCategoryIn) -> Any:
    try:
        cid = shop_mod.add_expense_category(
            payload.name, payload.is_fixed, payload.budget_monthly, payload.sort_order
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity("expense_category_added", "expense_category", cid,
                    f"Added expense category: {payload.name}",
                    {"name": payload.name, "budget": payload.budget_monthly})
    return {"id": cid}


@router.put("/api/expense-categories/{cid}")
def update_expense_category_route(cid: int, payload: ExpenseCategoryUpdate) -> Any:
    ok = shop_mod.update_expense_category(
        cid, payload.name, payload.is_fixed, payload.budget_monthly,
        payload.active, payload.sort_order,
    )
    if not ok:
        raise HTTPException(404, "expense category not found")
    return {"ok": True}


@router.delete("/api/expense-categories/{cid}")
def delete_expense_category_route(cid: int) -> Any:
    ok = shop_mod.delete_expense_category(cid)
    if not ok:
        raise HTTPException(404, "expense category not found")
    return {"ok": True}


# ------------------------------------------------------------------
# v4.0 Phase 2 — Recurring Expenses CRUD + generate
# ------------------------------------------------------------------

class RecurringExpenseIn(BaseModel):
    category_id: int
    amount: float
    description: str = ""
    payment_method: str = "cash"
    day_of_month: int = 1
    active: bool = True


class RecurringExpenseUpdate(BaseModel):
    category_id: Optional[int] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    payment_method: Optional[str] = None
    day_of_month: Optional[int] = None
    active: Optional[bool] = None


@router.get("/api/recurring-expenses")
def list_recurring_expenses_route(active_only: bool = False) -> Any:
    return {"recurring": shop_mod.list_recurring_expenses(active_only)}


@router.post("/api/recurring-expenses")
def add_recurring_expense_route(payload: RecurringExpenseIn) -> Any:
    rid = shop_mod.add_recurring_expense(
        payload.category_id, payload.amount, payload.description,
        payload.payment_method, payload.day_of_month, payload.active,
    )
    db.log_activity("recurring_expense_added", "recurring_expense", rid,
                    f"Added recurring expense (cat {payload.category_id}, Rs {payload.amount:.0f}, day {payload.day_of_month})",
                    {"category_id": payload.category_id, "amount": payload.amount})
    return {"id": rid}


@router.put("/api/recurring-expenses/{rid}")
def update_recurring_expense_route(rid: int, payload: RecurringExpenseUpdate) -> Any:
    ok = shop_mod.update_recurring_expense(
        rid, payload.category_id, payload.amount, payload.description,
        payload.payment_method, payload.day_of_month, payload.active,
    )
    if not ok:
        raise HTTPException(404, "recurring expense not found")
    return {"ok": True}


@router.delete("/api/recurring-expenses/{rid}")
def delete_recurring_expense_route(rid: int) -> Any:
    ok = shop_mod.delete_recurring_expense(rid)
    if not ok:
        raise HTTPException(404, "recurring expense not found")
    return {"ok": True}


@router.post("/api/recurring-expenses/generate")
def generate_recurring_expenses_route(force_month: str = "") -> Any:
    """Force-generate recurring expenses. Pass force_month=YYYY-MM to backfill a past month."""
    result = shop_mod.generate_recurring_expenses(force_month=force_month or None)
    if result["generated"] > 0:
        db.log_activity("recurring_expenses_generated", "recurring_expense", 0,
                        f"Generated {result['generated']} recurring expenses (skipped {result['skipped']})",
                        result)
    return result


# ------------------------------------------------------------------
# Cash Drawer
# ------------------------------------------------------------------


@router.get("/api/customers/payments/all")
def list_customer_payments_route() -> Any:
    return {"payments": shop_mod.list_customer_payments()}


@router.get("/api/customers/{cid}/payments")
def list_one_customer_payments(cid: int) -> Any:
    return {"payments": shop_mod.list_customer_payments(cid)}


@router.post("/api/customers/payments")
def add_customer_payment_route(payload: CustomerPaymentIn) -> Any:
    # v3.1.1: Idempotency — check first, then UNIQUE index catches race
    if payload.client_uuid:
        with db.conn() as c:
            existing = c.execute("SELECT * FROM customer_payments WHERE notes LIKE ?", (f"%uuid:{payload.client_uuid}%",)).fetchone()
            if existing:
                return {"id": existing["id"], "idempotent": True}
    notes = payload.notes or ""
    if payload.client_uuid:
        notes = (notes + " " if notes else "") + f"[uuid:{payload.client_uuid}]"
    try:
        pid = shop_mod.add_customer_payment(
            payload.customer_id, payload.customer_name or "",
            payload.amount, payload.payment_method, notes,
        )
    except sqlite3.IntegrityError:
        # Race condition — another request already inserted with this uuid
        with db.conn() as c:
            existing = c.execute("SELECT * FROM customer_payments WHERE notes LIKE ?", (f"%uuid:{payload.client_uuid}%",)).fetchone()
            if existing:
                return {"id": existing["id"], "idempotent": True}
        raise HTTPException(500, "Idempotency conflict but could not find existing record")
    db.log_activity("customer_payment", "customer", payload.customer_id,
                    f"Credit payment Rs {payload.amount:.0f} from {payload.customer_name}",
                    {"amount": payload.amount, "method": payload.payment_method})
    return {"id": pid}


# ------------------------------------------------------------------
# Loyalty
# ------------------------------------------------------------------


@router.get("/api/loyalty/rate")
def get_loyalty_rate_route() -> Any:
    return {"rate": shop_mod.get_loyalty_rate(), "points_per_rs": 100}


@router.post("/api/loyalty/redeem")
def redeem_loyalty_route(customer_id: int, points: int, sale_id: int = None) -> Any:
    res = shop_mod.redeem_loyalty_points(customer_id, points, sale_id)
    return res


# ------------------------------------------------------------------
# Cash drawer extra (cash in / cash out)
# ------------------------------------------------------------------


# ═══════════════════════════════════════════════════
# Phase 3: Wholesale Money Features
# ═══════════════════════════════════════════════════

class CustomerTierIn(BaseModel):
    price_tier: str | None = None
    credit_limit: float | None = None
    terms_days: int | None = None


@router.put("/api/customers/{cid}/tier")
def update_customer_tier(cid: int, payload: CustomerTierIn) -> Any:
    """Update customer price tier, credit limit, and payment terms."""
    fields, vals = [], []
    if payload.price_tier is not None and payload.price_tier in ('retail', 'wholesale', 'vip'):
        fields.append("price_tier = ?"); vals.append(payload.price_tier)
    if payload.credit_limit is not None:
        fields.append("credit_limit = ?"); vals.append(payload.credit_limit)
    if payload.terms_days is not None:
        fields.append("terms_days = ?"); vals.append(payload.terms_days)
    if not fields:
        raise HTTPException(400, "No fields to update")
    vals.append(cid)
    with db.conn() as c:
        cur = c.execute(f"UPDATE customers SET {', '.join(fields)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise HTTPException(404, "customer not found")
    db.log_activity("customer_tier_updated", "customer", cid, f"Tier/credit/terms updated for customer {cid}", {})
    return {"ok": True}


@router.get("/api/customers/{cid}/statement")
def customer_statement(cid: int) -> Any:
    """Customer statement: all sales + payments with running balance. Printable A4."""
    with db.conn() as c:
        cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not cust:
            raise HTTPException(404, "customer not found")
        sales = c.execute(
            "SELECT id, invoice_no, created_at, total, payment_status, payment_method "
            "FROM sales WHERE customer_id=? ORDER BY created_at", (cid,)
        ).fetchall()
        payments = c.execute(
            "SELECT id, amount, payment_method, notes, created_at "
            "FROM customer_payments WHERE customer_id=? ORDER BY created_at", (cid,)
        ).fetchall()
    # Merge into timeline with running balance
    timeline = []
    for s in sales:
        timeline.append({"type": "sale", "id": s["id"], "ref": s["invoice_no"],
                         "date": s["created_at"], "debit": s["total"], "credit": 0,
                         "status": s["payment_status"]})
    for p in payments:
        timeline.append({"type": "payment", "id": p["id"], "ref": f"PAY-{p['id']}",
                         "date": p["created_at"], "debit": 0, "credit": p["amount"],
                         "notes": p["notes"]})
    timeline.sort(key=lambda x: x["date"])
    running = 0
    for t in timeline:
        running += t["debit"] - t["credit"]
        t["balance"] = running
    return {
        "customer": dict(cust),
        "timeline": timeline,
        "opening_balance": 0,
        "closing_balance": running,
        "total_sales": sum(s["total"] for s in sales),
        "total_payments": sum(p["amount"] for p in payments),
    }


@router.get("/api/customers/{cid}/statement.html")
def customer_statement_html(cid: int) -> Any:
    """Printable A4 customer statement as HTML."""
    stmt = customer_statement(cid)
    cust = stmt["customer"]
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Statement — {cust['name']}</title>
<style>
body{{font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:40px;font-size:13px}}
h1{{text-align:center;margin:0 0 4px}}
.sub{{text-align:center;color:#666;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}
th{{background:#f5f5f5;padding:8px;text-align:left;border-bottom:2px solid #ddd;font-size:11px}}
td{{padding:8px;border-bottom:1px solid #eee}}
.num{{text-align:right}}
.total-row{{font-weight:bold;background:#f9f9f9}}
</style></head><body>
<h1>{cust['name']}</h1>
<div class="sub">Customer Statement — Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div>Phone: {cust.get('phone','') or '—'}</div>
<div>Credit Limit: Rs {cust.get('credit_limit',0):,.0f} | Terms: {cust.get('terms_days',0)} days</div>
<table>
<thead><tr><th>Date</th><th>Ref</th><th class="num">Debit</th><th class="num">Credit</th><th class="num">Balance</th></tr></thead>
<tbody>"""
    for t in stmt["timeline"]:
        html += f"<tr><td>{t['date']}</td><td>{t['ref']}</td>"
        html += f"<td class='num'>{'Rs {:,.0f}'.format(t['debit']) if t['debit'] else '—'}</td>"
        html += f"<td class='num'>{'Rs {:,.0f}'.format(t['credit']) if t['credit'] else '—'}</td>"
        html += f"<td class='num'>Rs {t['balance']:,.0f}</td></tr>"
    html += f"""</tbody>
<tfoot><tr class="total-row"><td colspan="4">Closing Balance</td><td class="num">Rs {stmt['closing_balance']:,.0f}</td></tr></tfoot>
</table>
</body></html>"""
    return HTMLResponse(html)


@router.get("/api/customers/{cid}/tier-pricing")
def get_tier_pricing(cid: int) -> Any:
    """Get tier-adjusted sell prices for all categories based on customer's price tier."""
    with db.conn() as c:
        cust = c.execute("SELECT price_tier FROM customers WHERE id=?", (cid,)).fetchone()
        if not cust:
            raise HTTPException(404, "customer not found")
        tier = cust["price_tier"] or "retail"
        cats = c.execute("SELECT id, name, code, sell_price, sell_wholesale, sell_vip FROM price_categories WHERE active=1 ORDER BY sort_order").fetchall()
    prices = []
    for cat in cats:
        if tier == "wholesale" and cat["sell_wholesale"] and cat["sell_wholesale"] > 0:
            price = cat["sell_wholesale"]
        elif tier == "vip" and cat["sell_vip"] and cat["sell_vip"] > 0:
            price = cat["sell_vip"]
        else:
            price = cat["sell_price"]
        prices.append({
            "category_id": cat["id"], "name": cat["name"], "code": cat["code"],
            "sell_price": price, "retail_price": cat["sell_price"],
            "wholesale_price": cat["sell_wholesale"] or 0,
            "vip_price": cat["sell_vip"] or 0,
            "tier": tier,
        })
    return {"customer_id": cid, "tier": tier, "prices": prices}


# ═══════════════════════════════════════════════════
# Phase 6: Customer Intelligence
# ═══════════════════════════════════════════════════


@router.get("/api/customers/{cid}/clv")
def customer_clv(cid: int) -> Any:
    """Customer Lifetime Value: avg_order × orders/year × gross_margin_pct."""
    with db.conn() as c:
        cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not cust:
            raise HTTPException(404, "customer not found")
        sales = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(AVG(total), 0) AS avg_order, "
            "COALESCE(MIN(created_at), '') AS first, COALESCE(MAX(created_at), '') AS last "
            "FROM sales WHERE customer_id=? AND payment_status IN ('paid', 'credit', 'partial')", (cid,)
        ).fetchone()
        cogs = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si "
            "JOIN sales s ON si.sale_id = s.id WHERE s.customer_id=? AND s.payment_status IN ('paid', 'credit', 'partial')", (cid,)
        ).fetchone()["v"]
    total_orders = sales["n"]
    avg_order = sales["avg_order"]
    revenue = avg_order * total_orders
    gross_margin_pct = ((revenue - cogs) / revenue * 100) if revenue > 0 else 0
    # Estimate orders/year from date range
    from datetime import datetime
    try:
        first = datetime.strptime(sales["first"][:19], "%Y-%m-%d %H:%M:%S")
        last = datetime.strptime(sales["last"][:19], "%Y-%m-%d %H:%M:%S")
        days = max(1, (last - first).days)
        orders_per_year = (total_orders / days) * 365
    except Exception:
        orders_per_year = total_orders
    clv = avg_order * orders_per_year * (gross_margin_pct / 100)
    return {
        "customer_id": cid, "total_orders": total_orders, "avg_order_value": round(avg_order, 2),
        "orders_per_year": round(orders_per_year, 1), "gross_margin_pct": round(gross_margin_pct, 1),
        "clv": round(clv, 2),
    }


@router.post("/api/customers/{cid}/referral")
def process_referral(cid: int, payload: dict) -> Any:
    """Process referral: both referrer and new customer earn points."""
    referred_by = payload.get("referred_by")
    points = int(payload.get("points", 10))
    if not referred_by:
        raise HTTPException(400, "referred_by is required")
    with db.conn() as c:
        # Check if referral already processed
        existing = c.execute("SELECT referred_by FROM customers WHERE id=?", (cid,)).fetchone()
        if existing and existing["referred_by"]:
            raise HTTPException(409, "Referral already processed for this customer")
        c.execute("UPDATE customers SET referred_by=? WHERE id=?", (referred_by, cid))
        # Award points to both
        c.execute("UPDATE customers SET loyalty_points = loyalty_points + ? WHERE id IN (?, ?)", (points, cid, referred_by))
        db.log_activity("referral_processed", "customer", cid, f"Referral: customer {cid} referred by {referred_by}, both earned {points} points", {})
    return {"ok": True, "points_awarded": points}




# ═══════════════════════════════════════════════════
# FIX 3.1: RFM Win-Back WhatsApp
# ═══════════════════════════════════════════════════

@router.get("/api/customers/{cid}/winback")
def winback_whatsapp(cid: int) -> Any:
    """Generate WhatsApp win-back message for At-Risk customer."""
    with db.conn() as c:
        cust = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not cust:
            raise HTTPException(404, "customer not found")
    phone = cust["phone"] or ""
    phone_clean = re.sub(r"[\s\-+]", "", phone)
    if phone_clean.startswith("03"):
        phone_clean = "92" + phone_clean[1:]
    msg = f"Dear {cust['name']}, we miss you at our store! Come back and enjoy special discounts on your next purchase. Reply STOP to opt out."
    url = f"https://wa.me/{phone_clean}?text={quote(msg)}"
    return {"url": url, "phone": phone_clean, "message": msg}
