"""Auto-generated router module — extracted from main.py Phase 1."""
import logging
import os, json, time, re, io, csv, secrets, hashlib, traceback
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

logger = logging.getLogger(__name__)

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


class SupplierIn(BaseModel):
    name: str
    phone: str = ""
    address: str = ""
    notes: str = ""




@router.get("/api/suppliers")
def list_suppliers(q: str = "", page: int = 0, page_size: int = 0,
                   sort_by: str = "", sort_order: str = "asc") -> Any:
    """List suppliers with optional search. v8.4: supports pagination.
    v8.15.0: Added sort_by + sort_order for dynamic column sorting.

    When page & page_size are provided (> 0), returns a paginated response:
    {suppliers, total, page, page_size, pages_total}
    Otherwise returns a plain list (backward compat).
    """
    use_pagination = page > 0 or page_size > 0
    if use_pagination:
        page = max(1, page)
        page_size = min(max(1, page_size or 50), 500)
    else:
        page_size = 500  # safety cap for non-paginated calls

    with db.conn() as c:
        # v8.15.0: Dynamic sort
        order_clause = db.validate_sort(sort_by, sort_order, {
            "name": "name",
            "phone": "phone",
            "address": "address",
            "created": "created_at",
        }, default="name ASC, id DESC")
        if q:
            base_where = ("deleted_at IS NULL AND (name LIKE ? OR phone LIKE ? OR address LIKE ?)")
            base_args = [f"%{q}%", f"%{q}%", f"%{q}%"]
            total = c.execute(
                f"SELECT COUNT(*) AS n FROM suppliers WHERE {base_where}",
                base_args,
            ).fetchone()["n"]
        else:
            base_where = "deleted_at IS NULL"
            base_args = []
            total = c.execute(
                "SELECT COUNT(*) AS n FROM suppliers WHERE deleted_at IS NULL"
            ).fetchone()["n"]
        # v8.19.1: clamp the page (last-page deletion / filter shrink)
        if use_pagination:
            page = db.clamp_page(page, total, page_size)
        rows = c.execute(
            f"SELECT * FROM suppliers WHERE {base_where} "
            f"ORDER BY {order_clause} LIMIT ? OFFSET ?",
            base_args + [page_size, (page - 1) * page_size if use_pagination else 0],
        ).fetchall()

    suppliers_list = [dict(r) for r in rows]
    if use_pagination:
        pages_total = (total + page_size - 1) // page_size
        return {
            "suppliers": suppliers_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages_total": pages_total,
        }
    return suppliers_list





@router.get("/api/suppliers/ap-aging")
def ap_aging() -> Any:
    """Accounts Payable aging report — supplier urdhaar by age bucket."""
    with db.conn() as c:
        bills = c.execute(
            "SELECT b.id, b.supplier_id, b.supplier_name, b.bill_date, b.written_total, b.computed_total, "
            "b.credit_due_date, s.phone, s.terms_days "
            "FROM bills b LEFT JOIN suppliers s ON b.supplier_id = s.id "
            "WHERE b.status='confirmed' AND b.payment_status='credit' AND b.deleted_at IS NULL "
            "ORDER BY b.bill_date"
        ).fetchall()
        buckets = {"0-30": [], "31-60": [], "61-90": [], "90+": []}
        totals = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        from datetime import datetime
        for b in bills:
            amount = b["written_total"] or b["computed_total"] or 0
            ref_date = b["credit_due_date"] or b["bill_date"]
            try:
                days_old = (datetime.now() - datetime.strptime(ref_date[:10], "%Y-%m-%d")).days
            except Exception:
                days_old = 0
            entry = {
                "bill_id": b["id"], "supplier_name": b["supplier_name"],
                "phone": b["phone"] if b["phone"] else "",
                "bill_date": b["bill_date"], "amount": amount,
                "due_date": b["credit_due_date"], "days_old": days_old,
            }
            if days_old <= 30:
                buckets["0-30"].append(entry); totals["0-30"] += amount
            elif days_old <= 60:
                buckets["31-60"].append(entry); totals["31-60"] += amount
            elif days_old <= 90:
                buckets["61-90"].append(entry); totals["61-90"] += amount
            else:
                buckets["90+"].append(entry); totals["90+"] += amount
    grand_total = sum(totals.values())
    return {"buckets": buckets, "totals": totals, "grand_total": grand_total}




@router.get("/api/suppliers/{supplier_id}")
def get_supplier(supplier_id: int) -> Any:
    with db.conn() as c:
        s = c.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not s:
            raise HTTPException(404, "supplier not found")
        bills = c.execute(
            "SELECT * FROM bills WHERE supplier_id=? AND deleted_at IS NULL ORDER BY COALESCE(bill_date, date(created_at)) DESC, id DESC", (supplier_id,)
        ).fetchall()
    reliability = insights.supplier_reliability(supplier_id)
    return {**dict(s), "bills": [dict(b) for b in bills], "reliability": reliability}




@router.get("/api/suppliers/{supplier_id}/statement")
def supplier_statement(supplier_id: int) -> Any:
    """Running balance statement: all bills + payments + current outstanding."""
    with db.conn() as c:
        s = c.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not s:
            raise HTTPException(404, "supplier not found")
        bills = c.execute(
            "SELECT id, supplier_name, bill_date, bill_no, written_total, computed_total, "
            "payment_status, credit_due_date, status, created_at "
            "FROM bills WHERE supplier_id=? AND deleted_at IS NULL "
            "ORDER BY bill_date ASC, id ASC", (supplier_id,)
        ).fetchall()
    # Build running balance
    statement = []
    balance = 0.0
    for b in bills:
        amount = b["written_total"] or b["computed_total"] or 0
        if b["status"] != "confirmed":
            continue  # Skip review bills
        if b["payment_status"] == "credit":
            balance += amount
        # Paid bills reduce balance (assuming payment happened on bill_date)
        entry = {
            "bill_id": b["id"],
            "date": b["bill_date"],
            "bill_no": b["bill_no"],
            "description": f"Bill #{b['id']}" + (f" ({b['bill_no']})" if b["bill_no"] else ""),
            "debit": amount if b["payment_status"] == "credit" else 0,
            "credit": amount if b["payment_status"] == "paid" else 0,
            "balance": round(balance, 2),
            "payment_status": b["payment_status"],
            "due_date": b["credit_due_date"],
            "is_overdue": False,
        }
        # Check overdue
        if b["payment_status"] == "credit" and b["credit_due_date"]:
            from datetime import datetime
            try:
                due = datetime.fromisoformat(b["credit_due_date"]).date()
                if due < datetime.now().date():
                    entry["is_overdue"] = True
            except Exception as _e:
                logger.warning("Silent exception in suppliers.py: %s", _e, exc_info=True)
        statement.append(entry)
    # Summary
    total_purchased = sum(e["debit"] + e["credit"] for e in statement)
    total_paid = sum(e["credit"] for e in statement)
    total_outstanding = sum(e["debit"] for e in statement)
    overdue_count = sum(1 for e in statement if e["is_overdue"])
    return {
        "supplier": dict(s),
        "statement": list(reversed(statement)),  # Most recent first
        "summary": {
            "total_bills": len(statement),
            "total_purchased": round(total_purchased, 2),
            "total_paid": round(total_paid, 2),
            "total_outstanding": round(total_outstanding, 2),
            "overdue_count": overdue_count,
        },
    }




@router.get("/api/suppliers/{supplier_id}/whatsapp")
def supplier_whatsapp_reminder(supplier_id: int) -> Any:
    """Generate a WhatsApp deep link with a reminder message for outstanding credit."""
    with db.conn() as c:
        s = c.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not s:
            raise HTTPException(404, "supplier not found")
        overdue = c.execute(
            "SELECT id, bill_date, written_total, computed_total, credit_due_date "
            "FROM bills WHERE supplier_id=? AND deleted_at IS NULL "
            "AND status='confirmed' AND payment_status='credit' "
            "ORDER BY bill_date", (supplier_id,)
        ).fetchall()
    if not overdue:
        return {"message": "No outstanding credit", "url": None}
    phone = s["phone"] or ""
    # Clean phone for WhatsApp (remove spaces, dashes; need country code without +)
    phone_clean = re.sub(r"[\s\-+]", "", phone)
    if phone_clean.startswith("92"):
        pass  # Already has country code
    elif phone_clean.startswith("03"):
        phone_clean = "92" + phone_clean[1:]  # Replace leading 0 with 92
    total = sum(b["written_total"] or b["computed_total"] or 0 for b in overdue)
    msg = (
        f"Assalam o Alaikum {s['name']},\n\n"
        f"This is a reminder regarding pending payments:\n\n"
    )
    for b in overdue:
        amt = b["written_total"] or b["computed_total"] or 0
        due = b["credit_due_date"][:10] if b["credit_due_date"] else "—"
        msg += f"• Bill #{b['id']} (dated {b['bill_date'][:10] if b['bill_date'] else '—'}): Rs {amt:.0f} — due {due}\n"
    msg += f"\nTotal outstanding: Rs {total:.0f}\n\nPlease arrange payment at your earliest convenience. JazakAllah."
    url = f"https://wa.me/{phone_clean}?text={quote(msg)}" if phone_clean else None
    return {"message": msg, "url": url, "phone": phone_clean, "total": total, "count": len(overdue)}




@router.post("/api/suppliers")
def create_supplier(payload: SupplierIn) -> Any:
    with db.conn() as c:
        sid = c.execute(
            "INSERT INTO suppliers(name, phone, address, notes) VALUES(?,?,?,?)",
            (payload.name, payload.phone, payload.address, payload.notes),
        ).lastrowid
    db.log_activity(
        "supplier_created", "supplier", sid,
        f"Added supplier {payload.name}",
        {"name": payload.name, "phone": payload.phone},
    )
    return {"id": sid}




@router.put("/api/suppliers/{supplier_id}")
def update_supplier(supplier_id: int, payload: SupplierIn) -> Any:
    with db.conn() as c:
        c.execute(
            "UPDATE suppliers SET name=?, phone=?, address=?, notes=? WHERE id=?",
            (payload.name, payload.phone, payload.address, payload.notes, supplier_id),
        )
    db.log_activity(
        "supplier_edited", "supplier", supplier_id,
        f"Updated supplier {payload.name}",
        {"name": payload.name},
    )
    return {"ok": True}




@router.delete("/api/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int) -> Any:
    """v8.9.1: Soft-delete supplier — sets deleted_at timestamp.

    Checks for active (non-soft-deleted) bills before allowing deletion.
    Also checks for unpaid credit bills.
    """
    with db.conn() as c:
        # Check for active bills (deleted_at IS NULL)
        count = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE supplier_id=? AND deleted_at IS NULL",
            (supplier_id,)
        ).fetchone()["n"]
        if count > 0:
            return JSONResponse(
                {"error": f"Cannot delete: {count} active bills reference this supplier"},
                status_code=400,
            )
        # v8.9.1: Check for unpaid credit bills
        unpaid_count = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE supplier_id=? AND deleted_at IS NULL "
            "AND status='confirmed' AND payment_status='credit'",
            (supplier_id,)
        ).fetchone()["n"]
        if unpaid_count > 0:
            return JSONResponse(
                {"error": f"Cannot delete: {unpaid_count} unpaid credit bills reference this supplier"},
                status_code=400,
            )
        # Soft-delete — preserves audit trail + historical references
        c.execute(
            "UPDATE suppliers SET deleted_at=datetime('now','localtime') WHERE id=?",
            (supplier_id,)
        )
    return {"ok": True, "soft_deleted": True}


# ------------------------------------------------------------------
# Price categories
# ------------------------------------------------------------------



# ═══════════════════════════════════════════════════
# Phase 3: Wholesale Money Features
# ═══════════════════════════════════════════════════

class SupplierTermsIn(BaseModel):
    terms_days: int | None = None
    credit_limit: float | None = None


@router.put("/api/suppliers/{sid}/terms")
def update_supplier_terms(sid: int, payload: SupplierTermsIn) -> Any:
    """Update supplier payment terms and credit limit."""
    fields, vals = [], []
    if payload.terms_days is not None:
        fields.append("terms_days = ?"); vals.append(payload.terms_days)
    if payload.credit_limit is not None:
        fields.append("credit_limit = ?"); vals.append(payload.credit_limit)
    if not fields:
        raise HTTPException(400, "No fields to update")
    vals.append(sid)
    with db.conn() as c:
        cur = c.execute(f"UPDATE suppliers SET {', '.join(fields)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise HTTPException(404, "supplier not found")
    return {"ok": True}


@router.get("/api/suppliers/{sid}/scorecard")
def supplier_scorecard(sid: int) -> Any:
    """Supplier scorecard: price drift, PO fill rate, avg days between bills."""
    with db.conn() as c:
        sup = c.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not sup:
            raise HTTPException(404, "supplier not found")
        # Price drift: for each item bought from this supplier, calculate price change %
        items = c.execute(
            "SELECT raw, item_code, price, qty, bill_date FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.supplier_id=? AND b.status='confirmed' AND b.deleted_at IS NULL "
            "ORDER BY bi.raw, b.bill_date", (sid,)
        ).fetchall()
        price_drifts = []
        from collections import defaultdict
        by_item = defaultdict(list)
        for it in items:
            by_item[it["raw"]].append(it)
        for item_name, entries in by_item.items():
            if len(entries) >= 2:
                first_price = entries[0]["price"]
                last_price = entries[-1]["price"]
                if first_price > 0:
                    drift_pct = ((last_price - first_price) / first_price) * 100
                    price_drifts.append({
                        "item": item_name, "first_price": first_price,
                        "last_price": last_price, "drift_pct": round(drift_pct, 1),
                        "first_date": entries[0]["bill_date"], "last_date": entries[-1]["bill_date"],
                    })
        # PO fill rate
        pos = c.execute("SELECT * FROM purchase_orders WHERE supplier_id=?", (sid,)).fetchall()
        total_pos = len(pos)
        received_pos = len([p for p in pos if p["status"] == "received"])
        fill_rate = (received_pos / total_pos * 100) if total_pos > 0 else 0
        # Avg days between bills
        bill_dates = c.execute(
            "SELECT DISTINCT bill_date FROM bills WHERE supplier_id=? AND status='confirmed' "
            "AND deleted_at IS NULL ORDER BY bill_date", (sid,)
        ).fetchall()
        if len(bill_dates) >= 2:
            from datetime import datetime
            dates = [datetime.strptime(d["bill_date"][:10], "%Y-%m-%d") for d in bill_dates if d["bill_date"]]
            gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0
        else:
            avg_gap = 0
    return {
        "supplier": dict(sup),
        "price_drifts": sorted(price_drifts, key=lambda x: abs(x["drift_pct"]), reverse=True)[:10],
        "po_fill_rate": round(fill_rate, 1),
        "total_pos": total_pos,
        "received_pos": received_pos,
        "avg_days_between_bills": round(avg_gap, 1),
        "total_bills": len(bill_dates),
    }


@router.get("/api/suppliers/{sid}/statement")
def supplier_statement_api(sid: int) -> Any:
    """Supplier statement: all bills + running balance."""
    with db.conn() as c:
        sup = c.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        if not sup:
            raise HTTPException(404, "supplier not found")
        bills = c.execute(
            "SELECT id, bill_no, bill_date, written_total, computed_total, payment_status, created_at "
            "FROM bills WHERE supplier_id=? AND status='confirmed' AND deleted_at IS NULL ORDER BY bill_date",
            (sid,)
        ).fetchall()
    timeline = []
    for b in bills:
        amount = b["written_total"] or b["computed_total"] or 0
        timeline.append({
            "type": "bill", "id": b["id"], "ref": b["bill_no"] or f"#{b['id']}",
            "date": b["bill_date"] or b["created_at"], "debit": amount, "credit": 0,
            "status": b["payment_status"],
        })
    # Add payment entries (bills marked as paid = credit)
    paid_bills = [t for t in timeline if t["status"] == "paid"]
    for t in paid_bills:
        t["credit"] = t["debit"]
    timeline.sort(key=lambda x: x["date"])
    running = 0
    for t in timeline:
        running += t["debit"] - t["credit"]
        t["balance"] = running
    return {
        "supplier": dict(sup),
        "timeline": timeline,
        "total_bills": len(bills),
        "total_spent": sum(t["debit"] for t in timeline),
        "outstanding": running,
    }


# ─── v4.0 Phase 5: Supplier Advances (peshgi) ──────────────────

class SupplierAdvanceIn(BaseModel):
    supplier_id: int
    amount: float
    payment_method: str = "cash"
    notes: str = ""


@router.get("/api/supplier-advances")
def list_advances_route(supplier_id: int = None, limit: int = 100) -> Any:
    return {"advances": shop_mod.list_supplier_advances(supplier_id, limit)}


@router.post("/api/supplier-advances")
def add_advance_route(payload: SupplierAdvanceIn) -> Any:
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    aid = shop_mod.add_supplier_advance(
        payload.supplier_id, payload.amount, payload.payment_method, payload.notes
    )
    db.log_activity("supplier_advance_added", "supplier", payload.supplier_id,
                    f"Advance Rs {payload.amount:.0f} to supplier #{payload.supplier_id}",
                    {"amount": payload.amount, "method": payload.payment_method})
    return {"id": aid}


@router.post("/api/supplier-advances/{aid}/apply")
def apply_advance_route(aid: int, payload: dict) -> Any:
    bill_id = int(payload.get("bill_id", 0))
    if not bill_id:
        raise HTTPException(400, "bill_id required")
    ok = shop_mod.apply_supplier_advance_to_bill(aid, bill_id)
    if not ok:
        raise HTTPException(400, "Advance not found or already applied")
    return {"ok": True}


@router.get("/api/suppliers/{sid}/advance-balance")
def advance_balance_route(sid: int) -> Any:
    return {"supplier_id": sid, "advance_balance": shop_mod.get_supplier_advance_balance(sid)}


# ─── v4.0 Phase 5: Agreed Rate List ────────────────────────────

class SupplierRateIn(BaseModel):
    supplier_id: int
    item_name: str
    agreed_price: float


@router.get("/api/supplier-rates")
def list_rates_route(supplier_id: int = None) -> Any:
    return {"rates": shop_mod.list_supplier_rates(supplier_id)}


@router.post("/api/supplier-rates")
def set_rate_route(payload: SupplierRateIn) -> Any:
    if payload.agreed_price <= 0:
        raise HTTPException(400, "agreed_price must be positive")
    rid = shop_mod.set_supplier_rate(payload.supplier_id, payload.item_name, payload.agreed_price)
    return {"id": rid}


@router.delete("/api/supplier-rates/{rid}")
def delete_rate_route(rid: int) -> Any:
    ok = shop_mod.delete_supplier_rate(rid)
    if not ok:
        raise HTTPException(404, "rate not found")
    return {"ok": True}


# ─── v4.0 Phase 5: Bank Ledger ─────────────────────────────────

class BankAccountIn(BaseModel):
    name: str
    opening_balance: float = 0


class BankTxIn(BaseModel):
    account_id: int
    type: str  # 'deposit' | 'withdrawal' | 'supplier_payment'
    amount: float
    description: str = ""
    reference: str = ""


class CashDepositIn(BaseModel):
    account_id: int
    amount: float
    description: str = ""


@router.get("/api/bank-accounts")
def list_accounts_route(active_only: bool = False) -> Any:
    return {"accounts": shop_mod.list_bank_accounts(active_only)}


@router.post("/api/bank-accounts")
def add_account_route(payload: BankAccountIn) -> Any:
    aid = shop_mod.add_bank_account(payload.name, payload.opening_balance)
    return {"id": aid}


@router.get("/api/bank-transactions")
def list_txs_route(account_id: int = None, limit: int = 100) -> Any:
    return {"transactions": shop_mod.list_bank_transactions(account_id, limit)}


@router.post("/api/bank-transactions")
def add_tx_route(payload: BankTxIn) -> Any:
    try:
        tid = shop_mod.add_bank_transaction(
            payload.account_id, payload.type, payload.amount,
            payload.description, payload.reference,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": tid}


@router.post("/api/bank-deposits")
def cash_deposit_route(payload: CashDepositIn) -> Any:
    """Deposit cash from drawer into bank. Creates paired bank_transactions + cash_drawer entries."""
    if payload.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    try:
        result = shop_mod.record_cash_to_bank_deposit(
            payload.account_id, payload.amount, payload.description
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.log_activity("bank_deposit", "bank_account", payload.account_id,
                    f"Cash deposit Rs {payload.amount:.0f} to account #{payload.account_id}",
                    {"amount": payload.amount})
    return result
