"""Auto-generated router module — extracted from main.py Phase 1."""
import logging
import os, json, time, re, io, traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any, Optional
from decimal import Decimal

from .. import db
from ..money import money, money_d
from .. import shop as shop_mod
from .. import insights
from .. import trends as trends_mod
from .. import extract
from .. import reports
from .. import pos_extra
from .. import pos_import
from .. import crypto as crypto_mod
from .. import jobs as jobs_mod
from .. import profit as profit_mod  # v5.0 Phase 1: running weighted avg cost
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


class BarcodeScanIn(BaseModel):
    payload: str




class CashActionIn(BaseModel):
    amount: float
    description: str = ""




class EmailReceiptIn(BaseModel):
    to_email: str
    sale_id: int




class EmployeePinIn(BaseModel):
    pin: str




class EmployeeUpdateIn(BaseModel):
    name: str | None = None
    phone: str | None = None
    role: str | None = None
    active: int | None = None
    monthly_salary: float | None = None  # v8.18.13: fixed monthly salary




class HeldOrderIn(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    notes: str = ""
    items: list[dict]
    discount: float = 0
    discount_type: str = "amount"
    total: float = 0




class QuotationIn(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    notes: str = ""
    items: list[dict]
    discount: float = 0
    discount_type: str = "amount"
    total: float = 0
    valid_days: int = 7




class ReturnIn(BaseModel):
    original_sale_id: int
    reason: str = ""
    # Either refund OR exchange:
    refund_amount: float = 0  # 0 = full refund
    exchange_items: list[dict] = []  # new items to give instead
    payment_method: str = "cash"  # how refund is paid out
    # C2 fix (v8.13.4): allow callers to forward manager PIN + idempotency UUID
    manager_pin: str = ""
    client_uuid: str = ""




class SaleEditIn(BaseModel):
    notes: Optional[str] = None
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None




class ItemIn(BaseModel):
    raw: str = ""
    item_code: str = ""
    price: float = 0
    qty: float = 0
    unit: str = "pcs"
    category_id: int | None = None
    page_no: int | None = None







class SaleItemIn(BaseModel):
    category_id: int
    category_code: str = ""
    sell_price: float
    qty: int = 1
    item_name: str = ""  # optional custom name
    cost_price: float = 0  # auto-filled from category avg
    # v8.8.0: Per-item discount + price override
    discount_pct: float = 0        # e.g. 10 = 10% off
    discount_amount: float = 0      # e.g. 50 = Rs 50 off (computed from pct OR set directly)
    # v8.16.6: was `float | None` only — make explicit Optional for Pydantic v2 null tolerance
    override_price: Optional[float] = None  # if set, overrides sell_price (requires manager PIN)
    base_price: Optional[float] = None  # original category price (stored for audit when override is used)





class SaleIn(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    # v8.16.6: was `int = None` — Pydantic v2 strictly rejects null. Use Optional.
    customer_id: Optional[int] = None
    discount: float = 0
    discount_type: str = "amount"  # "amount" | "percent"
    payment_method: str = "cash"  # "cash"|"card"|"credit"|"split"|"online"|"easypaisa"|"jazzcash"|"raast"|"bank"
    payment_status: str = "paid"  # "paid" | "credit" | "partial"
    split_cash: float = 0
    split_card: float = 0
    split_online: float = 0
    loyalty_points_used: int = 0
    apply_tax: bool = False
    notes: str = ""
    employee_id: Optional[int] = None
    shift_id: Optional[int] = None
    quotation_id: Optional[int] = None
    client_uuid: Optional[str] = None
    raast_reference: Optional[str] = None  # Phase 2: Raast QR transaction reference
    manager_pin: Optional[str] = None  # Phase 3: credit limit override PIN
    payment_submethod: Optional[str] = None  # v8.8.0: 'easypaisa'|'jazzcash'|'raast_qr'|'bank_transfer'|NULL
    items: list[SaleItemIn]




class TargetIn(BaseModel):
    period: str  # 'daily' | 'monthly'
    target_date: str
    target_amount: float
    notes: str = ""




def _get_category_avg_cost(category_id: int) -> float:
    """Backward-compat shim — delegates to shop.get_category_avg_cost.

    The canonical implementation now lives in app/shop.py so other modules
    (reports, insights, maintenance endpoints) can reuse the same weighted-avg
    cost logic. The local definition previously used an unweighted AVG(price)
    which gave equal weight to a 1-pc line and a 1000-pc line.
    """
    return shop_mod.get_category_avg_cost(category_id)






@router.get("/api/pos/categories")
def pos_categories() -> Any:
    """Get categories with avg cost AND current stock for POS display."""
    with db.conn() as c:
        cats = c.execute(
            "SELECT pc.*, (SELECT COUNT(*) FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE bi.category_id = pc.id AND b.status = 'confirmed' AND b.deleted_at IS NULL) AS item_count "
            "FROM price_categories pc WHERE pc.active=1 ORDER BY pc.sell_price"
        ).fetchall()
        # Compute stock per category in one pass
        purchased_rows = c.execute(
            "SELECT bi.category_id, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_purchased "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id IS NOT NULL "
            "GROUP BY bi.category_id"
        ).fetchall()
        purchased_map = {r["category_id"]: r["total_purchased"] or 0 for r in purchased_rows}
        sold_rows = c.execute(
            "SELECT si.category_id, SUM(si.qty) AS total_sold "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE si.category_id IS NOT NULL "
            "AND " + db.VALID_SALE_FILTER + " "
            "GROUP BY si.category_id"
        ).fetchall()
        sold_map = {r["category_id"]: r["total_sold"] or 0 for r in sold_rows}
    result = []
    for cat in cats:
        avg_cost = _get_category_avg_cost(cat["id"])
        purchased = purchased_map.get(cat["id"], 0)
        sold = sold_map.get(cat["id"], 0)
        stock = int(purchased - sold)
        result.append({
            "id": cat["id"],
            "name": cat["name"],
            "sell_price": cat["sell_price"],
            "color": cat["color"],
            "code": cat["code"] or str(int(cat["sell_price"])),  # user-defined code, fallback to price
            "avg_cost": avg_cost,
            "profit_per_piece": round(cat["sell_price"] - avg_cost, 2),
            "margin": round((cat["sell_price"] - avg_cost) / cat["sell_price"], 2) if cat["sell_price"] > 0 else 0,
            "item_count": cat["item_count"],
            "stock": stock,
            "low_stock": stock < 10,
            "out_of_stock": stock <= 0,
        })

    # v8.4: Also fetch custom items (non-category items like bags, accessories)
    with db.conn() as c:
        custom_rows = c.execute(
            "SELECT * FROM custom_items WHERE is_active=1 ORDER BY sort_order, name"
        ).fetchall()
    for ci in custom_rows:
        result.append({
            "id": f"custom-{ci['id']}",
            "name": ci["name"],
            "sell_price": ci["sell_price"],
            "color": ci["color"],
            "code": ci["code"] or ci["name"][:3].upper(),
            "avg_cost": ci["cost_price"],
            "profit_per_piece": round(ci["sell_price"] - ci["cost_price"], 2),
            "margin": round((ci["sell_price"] - ci["cost_price"]) / ci["sell_price"], 2) if ci["sell_price"] > 0 else 0,
            "item_count": 0,
            "is_custom": True,
            "custom_id": ci["id"],
            "category_label": ci["category"],
            "stock": 9999,  # custom items don't track stock by default
            "low_stock": False,
            "out_of_stock": False,
        })
    return result



# ==================================================================
# v8.4: Custom Items (non-category items like bags, accessories, etc.)
# ==================================================================

class CustomItemIn(BaseModel):
    name: str
    code: str = ""
    sell_price: float
    cost_price: float = 0
    category: str = "Miscellaneous"
    color: str = "#64748B"

@router.get("/api/custom-items")
def list_custom_items() -> Any:
    """List all active custom items for POS display."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM custom_items WHERE is_active=1 ORDER BY sort_order, name"
        ).fetchall()
    return {"items": [dict(r) for r in rows]}

@router.post("/api/custom-items")
def add_custom_item(payload: CustomItemIn) -> Any:
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO custom_items(name, code, sell_price, cost_price, category, color) "
            "VALUES(?,?,?,?,?,?)",
            (payload.name, payload.code, payload.sell_price, payload.cost_price,
             payload.category, payload.color)
        )
        db.log_activity("custom_item_created", "custom_item", cur.lastrowid,
                        f"Custom item created: {payload.name} (Rs {payload.sell_price})",
                        {"name": payload.name, "price": payload.sell_price, "category": payload.category})
        return {"id": cur.lastrowid}

@router.put("/api/custom-items/{item_id}")
def update_custom_item(item_id: int, payload: CustomItemIn) -> Any:
    with db.conn() as c:
        c.execute(
            "UPDATE custom_items SET name=?, code=?, sell_price=?, cost_price=?, category=?, color=? "
            "WHERE id=?",
            (payload.name, payload.code, payload.sell_price, payload.cost_price,
             payload.category, payload.color, item_id)
        )
        db.log_activity("custom_item_updated", "custom_item", item_id,
                        f"Custom item updated: {payload.name}",
                        {"name": payload.name, "price": payload.sell_price})
        return {"ok": True}

@router.delete("/api/custom-items/{item_id}")
def delete_custom_item(item_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE custom_items SET is_active=0 WHERE id=?", (item_id,))
        db.log_activity("custom_item_deleted", "custom_item", item_id,
                        f"Custom item #{item_id} deactivated", {"id": item_id})
        return {"ok": True}


# ==================================================================
# v8.4: Item-Level Discounts (e.g. 100% off on bags, 10% off on Category A)
# ==================================================================

class ItemDiscountIn(BaseModel):
    applies_to: str = "all"  # 'all', 'category', 'custom_item'
    category_id: int | None = None
    custom_item_id: int | None = None
    discount_type: str = "percent"  # 'percent' or 'amount'
    discount_value: float
    reason: str = ""

@router.get("/api/item-discounts")
def list_item_discounts() -> Any:
    """List all active item-level discounts."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT d.*, pc.name AS category_name, ci.name AS custom_item_name "
            "FROM item_discounts d "
            "LEFT JOIN price_categories pc ON d.category_id = pc.id "
            "LEFT JOIN custom_items ci ON d.custom_item_id = ci.id "
            "WHERE d.is_active=1 ORDER BY d.created_at DESC"
        ).fetchall()
    return {"discounts": [dict(r) for r in rows]}

@router.post("/api/item-discounts")
def add_item_discount(payload: ItemDiscountIn) -> Any:
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO item_discounts(applies_to, category_id, custom_item_id, "
            "discount_type, discount_value, reason) VALUES(?,?,?,?,?,?)",
            (payload.applies_to, payload.category_id, payload.custom_item_id,
             payload.discount_type, payload.discount_value, payload.reason)
        )
        db.log_activity("item_discount_created", "item_discount", cur.lastrowid,
                        f"Discount: {payload.discount_value}{payload.discount_type} on {payload.applies_to}",
                        {"applies_to": payload.applies_to, "type": payload.discount_type,
                         "value": payload.discount_value, "reason": payload.reason})
        return {"id": cur.lastrowid}

@router.delete("/api/item-discounts/{discount_id}")
def delete_item_discount(discount_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE item_discounts SET is_active=0 WHERE id=?", (discount_id,))
        db.log_activity("item_discount_deleted", "item_discount", discount_id,
                        f"Discount #{discount_id} removed", {"id": discount_id})
        return {"ok": True}


@router.post("/api/sales")
def create_sale(payload: SaleIn) -> Any:
    """Create a new sale (POS checkout). Auto-fills cost from category average.

    Phase 0 PR 3 — Atomic sale creation:
    The entire sale — idempotency check, stock guard, sale row insert, sale_items,
    cash_drawer entry, customer stats update, loyalty redemption, commission,
    activity_log entries, AND running stock-state mutation — is committed as a
    SINGLE atomic write transaction via `db.write_tx()` (BEGIN IMMEDIATE).
    If ANY write fails, ALL roll back together. No more half-committed sales.

    v8.14.0: Refactored from ~454 LOC single function into orchestrated helpers
    for readability. Each helper handles one domain and accepts the shared
    connection `c`. The atomicity guarantee is unchanged — all helpers run
    inside the same write_tx().

    Reviewer feedback applied:
    - money() / money_d() at all monetary boundaries (prevents float drift)
    - Sync `def` (not `async def`) — SQLite I/O is blocking
    - Configurable stock strategy via setting `stock_strategy`
      ("strict" = block on insufficient, "permit_negative" = allow back-order)
    - apply_sale_to_state / peek_avg_cost called with c=c (shared connection)

    H7 fix (v8.13.4): input validation — negative qty/price/discount, >100%
    discount, tender mismatch (cash received < bill total), negative wallet
    adjustments are now rejected with HTTP 400 BEFORE any DB write.
    """
    # ─── H7: Pre-flight input validation ────────────────────────────────────
    if not payload.items:
        raise HTTPException(400, "Cannot create a sale with no items")
    for idx, i in enumerate(payload.items):
        # H7: reject negative qty/price/discount. qty=0 is allowed (used for
        # "free sample" / zero-value line items per the test_custom_item*
        # suite — they're skipped at the stock-state step via `qty > 0`).
        if i.qty is None or i.qty < 0:
            raise HTTPException(400, f"Item {idx+1}: quantity cannot be negative (got {i.qty})")
        if i.sell_price is None or i.sell_price < 0:
            raise HTTPException(400, f"Item {idx+1}: sell_price cannot be negative (got {i.sell_price})")
        if i.discount_pct is not None and (i.discount_pct < 0 or i.discount_pct > 100):
            raise HTTPException(400, f"Item {idx+1}: discount_pct must be 0..100 (got {i.discount_pct})")
        if i.discount_amount is not None and i.discount_amount < 0:
            raise HTTPException(400, f"Item {idx+1}: discount_amount cannot be negative")
        if i.override_price is not None and i.override_price < 0:
            raise HTTPException(400, f"Item {idx+1}: override_price cannot be negative")
        if i.cost_price is not None and i.cost_price < 0:
            raise HTTPException(400, f"Item {idx+1}: cost_price cannot be negative")
    # Sale-level discount validation
    if payload.discount is not None and payload.discount < 0:
        raise HTTPException(400, "Sale-level discount cannot be negative")
    if payload.discount_type == "percent" and payload.discount > 100:
        raise HTTPException(400, "Sale-level percent discount cannot exceed 100%")
    # Split-tender validation: components must be non-negative
    for fld in ("split_cash", "split_card", "split_online"):
        v = getattr(payload, fld, 0) or 0
        if v < 0:
            raise HTTPException(400, f"{fld} cannot be negative (got {v})")
    # Tender mismatch: for 'cash' payment, cash_received (if provided via notes
    # or a split_cash component) must cover the bill total. For 'split', the
    # sum of components must cover the bill total.
    # (Computed total is only known after item totals below; we re-check there.)
    if payload.loyalty_points_used is not None and payload.loyalty_points_used < 0:
        raise HTTPException(400, "loyalty_points_used cannot be negative")

    # ─── Pre-flight: pure calculation, no DB lock yet ────────────────────────
    # v8.18.16: fetch the SERVER-side category list prices once (read conn,
    # pre-lock — same pattern as db.get_setting above). The old gate only
    # measured the sale-level payload.discount, so line-level discounts
    # (discount_pct/discount_amount), override_price, and simply sending a
    # lower per-item sell_price all bypassed it entirely. We now measure the
    # TOTAL effective discount vs the authoritative price list.
    _cat_ids = {i.category_id for i in payload.items if i.category_id}
    _cat_list_prices = {}
    if _cat_ids:
        try:
            with db.conn() as _pc:
                for _row in _pc.execute(
                    f"SELECT id, sell_price FROM price_categories "
                    f"WHERE id IN ({','.join('?' * len(_cat_ids))})",
                    tuple(_cat_ids),
                ).fetchall():
                    if _row["sell_price"] is not None and float(_row["sell_price"]) > 0:
                        _cat_list_prices[_row["id"]] = money_d(_row["sell_price"])
        except Exception:
            _cat_list_prices = {}  # fail-open on read error; gate falls back below
    item_line_totals = []
    list_gross_d = Decimal("0")  # Σ (server list price × qty) for priced lines
    for i in payload.items:
        eff_price = money_d(i.override_price) if i.override_price and i.override_price > 0 else money_d(i.sell_price)
        line_gross = eff_price * money_d(i.qty)
        if i.discount_pct and i.discount_pct > 0:
            disc = line_gross * (money_d(i.discount_pct) / Decimal("100"))
        else:
            disc = money_d(i.discount_amount or 0)
        line_total = line_gross - disc
        if line_total < 0:
            line_total = Decimal("0")
        item_line_totals.append(line_total)
        _lp = _cat_list_prices.get(i.category_id)
        if _lp is not None:
            list_gross_d += _lp * money_d(i.qty)
    subtotal_d = sum(item_line_totals)
    subtotal = money(subtotal_d)
    if payload.discount_type == "percent":
        discount_amount = money(subtotal_d * (money_d(payload.discount) / Decimal("100")))
        discount_pct = float(payload.discount)
    else:
        discount_amount = money(payload.discount)
        discount_pct = (float(payload.discount) / subtotal * 100) if subtotal > 0 else 0.0

    # Discount threshold PIN gate (verify PIN BEFORE taking the write lock —
    # verify_manager_pin opens its own read connection which would deadlock
    # against our pending BEGIN IMMEDIATE).
    # v8.18.16: gate on the TOTAL effective discount — list-price underpricing
    # + line discounts + sale-level discount combined. Falls back to the
    # legacy sale-level-only metric when no line has a server list price
    # (e.g. all-custom-item carts).
    paid_pre_tax_d = subtotal_d - money_d(discount_amount)
    if list_gross_d > 0:
        discount_pct = float(
            (list_gross_d - paid_pre_tax_d) / list_gross_d * Decimal("100")
        )
    elif subtotal > 0 and payload.discount:
        # legacy fallback (custom items only)
        pass
    max_discount_pct = float(db.get_setting("max_discount_pct_without_pin", "10") or "10")
    mgr_override = None  # populated only when discount exceeded threshold and PIN was valid
    if discount_pct > max_discount_pct:
        mgr_override = shop_mod.verify_manager_pin(payload.manager_pin) if payload.manager_pin else None
        if not mgr_override:
            return JSONResponse({
                "error": f"Discount {discount_pct:.1f}% exceeds max {max_discount_pct}% without manager PIN",
                "code": "discount_pin_required",
                "discount_pct": round(discount_pct, 2),
                "max_allowed": max_discount_pct,
            }, status_code=403)

    # Tax / GST (read-only helper, OK before lock)
    tax_rate = 0.0
    tax_amount = 0.0
    if payload.apply_tax:
        tax_info = pos_extra.compute_tax(subtotal, discount_amount, 0)
        tax_rate = tax_info["rate"]
        tax_amount = tax_info["tax_amount"]

    # Stock strategy: "strict" (default — block insufficient) | "permit_negative"
    stock_strategy = (db.get_setting("stock_strategy", "strict") or "strict").strip().lower()

    # ─── Atomic write transaction ───────────────────────────────────────────
    with db.write_tx() as c:
        # (1) Idempotency check
        idem = _sale_check_idempotency(c, payload.client_uuid)
        if idem:
            return idem

        # (2) Get-or-create customer inline
        customer_id = _sale_get_or_create_customer(c, payload)

        # (3) Loyalty redemption inline
        loyalty_discount, loyalty_used = _sale_redeem_loyalty(c, payload, customer_id)

        # (4) Compute total after loyalty_discount applied
        total = money(subtotal - discount_amount - loyalty_discount + tax_amount)
        if total < 0:
            total = 0.0

        # (5) Determine payment status
        payment_status = _sale_determine_payment_status(payload, total)

        # (6) Credit limit check
        _sale_check_credit_limit(c, payload, customer_id, payment_status, total)

        # (7) Stock guard (strict strategy only)
        stock_blocked = _sale_stock_guard(c, payload, stock_strategy)
        if stock_blocked:
            # Collect the actual stock issues for the error message
            stock_issues = []
            for item in payload.items:
                if not item.category_id:
                    continue
                st = c.execute(
                    "SELECT current_qty FROM category_stock_state WHERE category_id=?",
                    (item.category_id,),
                ).fetchone()
                available = float(st["current_qty"] or 0) if st else 0.0
                if available < item.qty:
                    cat = c.execute(
                        "SELECT code, name FROM price_categories WHERE id=?",
                        (item.category_id,),
                    ).fetchone()
                    cat_name = cat["code"] if cat else f"Category {item.category_id}"
                    stock_issues.append(
                        f"Insufficient stock for {cat_name}: {int(available)} available"
                    )
            # The write_tx() context manager will roll back automatically
            # when we return (no commit). Return the JSONResponse with 409.
            return JSONResponse({"error": "; ".join(stock_issues)}, status_code=409)

        # (8) Generate invoice_no and INSERT sale row
        invoice_no, sale_id = _sale_insert_sale_row(c, payload, customer_id, subtotal,
                                                     discount_amount, loyalty_used, loyalty_discount,
                                                     tax_rate, tax_amount, total, payment_status)

        # (9) Loop items: peek cost, compute per-item discount, INSERT sale_items
        zero_cost_categories, deferred_state_sales = _sale_insert_sale_items(c, payload, sale_id, invoice_no)

        # (10) Cash drawer entry
        _sale_insert_cash_drawer(c, payload, total, invoice_no, sale_id)

        # (11) Apply stock-state mutation
        _sale_apply_stock_state(c, deferred_state_sales, sale_id, invoice_no)

        # (12) Customer stats inline (loyalty + credit tracking)
        _sale_update_customer_stats(c, customer_id, payment_status, total, payload, loyalty_used, sale_id)

        # (13) Mark quotation as converted
        if payload.quotation_id:
            c.execute(
                "UPDATE quotations SET status='converted' WHERE id=?", (payload.quotation_id,),
            )

        # (14) Commission — compute + record inline
        commission_amount = _sale_record_commission(c, payload, sale_id, total)

        # (15) Activity log — sale_created
        db.log_activity(
            "sale_created", "sale", sale_id,
            f"Sale {invoice_no} — Rs {total:.0f} ({len(payload.items)} items, {payment_status})",
            {"invoice": invoice_no, "total": total, "payment": payment_status,
             "commission": commission_amount if commission_amount > 0 else None},
            c=c,
        )

        # (16) COGS warnings for items with no cost history
        for cid in zero_cost_categories:
            db.log_activity(
                "cogs_warning", "category", cid,
                f"Sale {invoice_no} recorded with cost_price=0 — "
                f"category_id={cid} has no confirmed cost history",
                {"category_id": cid, "sale_id": sale_id, "invoice_no": invoice_no},
                c=c,
            )

        # (17) Suspicious event for discount-over-PIN-threshold override
        if mgr_override is not None:
            db.log_activity(
                "suspicious", "sale", sale_id,
                f"[discount_override] Discount {discount_pct:.1f}% "
                f"(over threshold {max_discount_pct}%) approved by {mgr_override['name']}",
                {
                    "original_event": "discount_override",
                    "discount_pct": round(discount_pct, 2),
                    "threshold": max_discount_pct,
                    # v8.18.16: breakdown for the audit trail — which mechanism
                    # produced the discount (list underpricing, line discounts,
                    # sale-level discount)
                    "list_gross": float(list_gross_d),
                    "charged_pre_tax": float(paid_pre_tax_d),
                    "sale_level_discount": float(discount_amount),
                    "manager_id": mgr_override["id"],
                    "manager_name": mgr_override["name"],
                    "manager_pin_provided": bool(payload.manager_pin),
                    "employee_id": payload.employee_id,
                },
                c=c,
            )

    # ─── Post-commit return ────────────────────────────────────────────────
    return {"id": sale_id, "invoice_no": invoice_no, "total": total,
            "subtotal": subtotal, "discount": discount_amount,
            "loyalty_points_used": loyalty_used, "loyalty_discount": loyalty_discount,
            "tax_rate": tax_rate, "tax_amount": tax_amount,
            "payment_status": payment_status,
            "commission": commission_amount if commission_amount > 0 else None}


# ─── v8.14.0: create_sale() helpers — each handles one step, accepts shared connection c ──

def _sale_check_idempotency(c, client_uuid: str):
    """Step 1: Idempotency check — if client_uuid already exists, return the existing sale."""
    if not client_uuid:
        return None
    existing = c.execute(
        "SELECT * FROM sales WHERE client_uuid=?", (client_uuid,)
    ).fetchone()
    if existing:
        return {"id": existing["id"], "invoice_no": existing["invoice_no"],
                "total": existing["total"], "subtotal": existing["subtotal"],
                "discount": existing["discount"],
                "loyalty_points_used": existing["loyalty_points_used"],
                "loyalty_discount": existing["loyalty_discount"],
                "tax_rate": existing["tax_rate"], "tax_amount": existing["tax_amount"],
                "payment_status": existing["payment_status"],
                "idempotent": True}
    return None


def _sale_get_or_create_customer(c, payload) -> int:
    """Step 2: Get-or-create customer inline (don't call shop.get_or_create_customer — own conn)."""
    customer_id = payload.customer_id
    if not customer_id and (payload.customer_name or payload.customer_phone):
        if payload.customer_phone:
            row = c.execute(
                "SELECT id FROM customers WHERE phone=?", (payload.customer_phone,)
            ).fetchone()
            if row:
                customer_id = row["id"]
        if not customer_id and payload.customer_name:
            row = c.execute(
                "SELECT id FROM customers WHERE lower(name)=lower(?)", (payload.customer_name,)
            ).fetchone()
            if row:
                customer_id = row["id"]
        if not customer_id:
            customer_id = c.execute(
                "INSERT INTO customers(name, phone) VALUES(?,?)",
                (payload.customer_name or "Walk-in", payload.customer_phone),
            ).lastrowid
    return customer_id


def _sale_redeem_loyalty(c, payload, customer_id: int) -> tuple:
    """Step 3: Loyalty redemption inline — returns (loyalty_discount, loyalty_used)."""
    loyalty_discount = 0.0
    loyalty_used = 0
    if payload.loyalty_points_used and payload.loyalty_points_used > 0 and customer_id:
        per_rs_row = c.execute(
            "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
        ).fetchone()
        per_rs = float(per_rs_row["value"] or "100") if per_rs_row else 100.0
        per_rs = per_rs if per_rs > 0 else 100.0
        cust_row = c.execute(
            "SELECT loyalty_points FROM customers WHERE id=?", (customer_id,)
        ).fetchone()
        available = int(cust_row["loyalty_points"] or 0) if cust_row else 0
        use_pts = min(available, int(payload.loyalty_points_used))
        if use_pts > 0:
            rate_row = c.execute(
                "SELECT value FROM settings WHERE key='loyalty_rate'"
            ).fetchone()
            rate = float(rate_row["value"] or "1") if rate_row else 1.0
            loyalty_discount = money(use_pts * rate)
            loyalty_used = use_pts
            c.execute(
                "UPDATE customers SET loyalty_points = loyalty_points - ?, "
                "loyalty_redeemed = loyalty_redeemed + ? WHERE id=?",
                (use_pts, use_pts, customer_id),
            )
            c.execute(
                "INSERT INTO loyalty_redemptions(customer_id, sale_id, points_used, rupee_value) "
                "VALUES(?, NULL, ?, ?)",
                (customer_id, use_pts, loyalty_discount),
            )
    return (loyalty_discount, loyalty_used)


def _sale_determine_payment_status(payload, total) -> str:
    """Step 5: Determine payment status based on payment_method and split amounts."""
    if payload.payment_method == "credit":
        return "credit"
    elif payload.payment_method == "split":
        paid_so_far = money(payload.split_cash + payload.split_card + payload.split_online)
        if paid_so_far >= money(total) - 0.01:
            return "paid"
        elif paid_so_far > 0:
            return "partial"
        else:
            return "credit"
    else:
        return "paid"


def _sale_check_credit_limit(c, payload, customer_id: int, payment_status: str, total) -> None:
    """Step 6: Credit limit check — read customer's CURRENT credit inside the txn."""
    if payment_status == "credit" and customer_id:
        cust = c.execute(
            "SELECT credit_limit, total_credit FROM customers WHERE id=?", (customer_id,)
        ).fetchone()
        if cust and cust["credit_limit"] > 0:
            new_total_credit = cust["total_credit"] + total
            if new_total_credit > cust["credit_limit"]:
                if not payload.manager_pin:
                    raise HTTPException(423, {
                        "error": "credit_limit_exceeded",
                        "message": (
                            f"Credit limit exceeded. Current: Rs {cust['total_credit']:,.0f}, "
                            f"Limit: Rs {cust['credit_limit']:,.0f}, This sale: Rs {total:,.0f}"
                        ),
                        "current_credit": cust["total_credit"],
                        "credit_limit": cust["credit_limit"],
                        "sale_total": total,
                    })
                mgr_row = c.execute(
                    "SELECT * FROM employees WHERE pin=? AND role IN ('manager','admin') AND active=1",
                    (payload.manager_pin,),
                ).fetchone()
                if not mgr_row:
                    raise HTTPException(403, "Invalid manager PIN for credit limit override")
                db.log_activity(
                    "credit_limit_override", "sale", 0,
                    f"Manager {mgr_row['name']} overrode credit limit for customer {customer_id} "
                    f"(sale Rs {total:,.0f})", {}, c=c,
                )


def _sale_stock_guard(c, payload, stock_strategy: str) -> None:
    """Step 7: Stock guard — read stock_state inline so the check is consistent with the sale mutation.
    Returns True if the sale should be aborted (insufficient stock), False otherwise.
    The caller checks the return value and returns a JSONResponse if True.
    """
    if stock_strategy != "strict":
        return False
    stock_issues = []
    for item in payload.items:
        if not item.category_id:
            continue
        st = c.execute(
            "SELECT current_qty FROM category_stock_state WHERE category_id=?",
            (item.category_id,),
        ).fetchone()
        available = float(st["current_qty"] or 0) if st else 0.0
        if available < item.qty:
            cat = c.execute(
                "SELECT code, name FROM price_categories WHERE id=?",
                (item.category_id,),
            ).fetchone()
            cat_name = cat["code"] if cat else f"Category {item.category_id}"
            stock_issues.append(
                f"Insufficient stock for {cat_name}: {int(available)} available"
            )
    if stock_issues:
        # Return True to signal the caller to abort — can't return JSONResponse
        # from inside the write_tx() context manager without it being caught
        # by the exception handler. The caller handles the abort.
        return True
    return False


def _sale_insert_sale_row(c, payload, customer_id, subtotal, discount_amount,
                          loyalty_used, loyalty_discount, tax_rate, tax_amount,
                          total, payment_status) -> tuple:
    """Step 8: Generate invoice_no and INSERT sale row. Returns (invoice_no, sale_id)."""
    invoice_no = f"INV-{datetime.now().strftime('%Y%m%d')}-{int(time.time() * 1000) % 100000:05d}"
    sale_id = c.execute(
        "INSERT INTO sales(invoice_no, customer_name, customer_phone, customer_id, subtotal, discount, "
        "loyalty_points_used, loyalty_discount, tax_rate, tax_amount, total, payment_method, payment_status, "
        "split_cash, split_card, split_online, employee_id, shift_id, notes, client_uuid, raast_reference, "
        "payment_submethod) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (invoice_no, payload.customer_name, payload.customer_phone, customer_id, subtotal,
         discount_amount, loyalty_used, loyalty_discount, tax_rate, tax_amount, total,
         payload.payment_method, payment_status,
         payload.split_cash, payload.split_card, payload.split_online,
         payload.employee_id, payload.shift_id, payload.notes, payload.client_uuid,
         payload.raast_reference, payload.payment_submethod),
    ).lastrowid
    return (invoice_no, sale_id)


def _sale_insert_sale_items(c, payload, sale_id: int, invoice_no: str) -> tuple:
    """Step 9: Loop items: peek cost, compute per-item discount, INSERT sale_items.
    Returns (zero_cost_categories, deferred_state_sales)."""
    zero_cost_categories = []
    deferred_state_sales = []
    for item in payload.items:
        if item.cost_price and item.cost_price > 0:
            cost = money(item.cost_price)
        elif item.category_id:
            cost = money(profit_mod.peek_avg_cost(c, item.category_id))
        else:
            cost = 0.0
        if cost <= 0 and item.category_id:
            zero_cost_categories.append(item.category_id)
        if item.category_id and item.qty and item.qty > 0:
            deferred_state_sales.append((item.category_id, item.qty))
        name = item.item_name or f"Category {item.category_code}"

        effective_price = money(item.sell_price)
        base_price = None
        override_price = None

        if item.override_price is not None and item.override_price > 0:
            base_price = money(item.sell_price)
            effective_price = money(item.override_price)
            override_price = money(item.override_price)
            db.log_activity(
                "price_override", "sale", sale_id,
                f"Price override on {name}: {base_price} → {override_price}",
                {"category_id": item.category_id, "base_price": base_price,
                 "override_price": override_price, "qty": item.qty},
                c=c,
            )

        line_discount_pct = float(item.discount_pct or 0)
        if line_discount_pct > 0:
            line_discount_amount = money(money_d(effective_price) * money_d(item.qty) * (money_d(line_discount_pct) / Decimal("100")))
        else:
            line_discount_amount = money(item.discount_amount or 0)

        line_gross = money(money_d(effective_price) * money_d(item.qty))
        line_total = money(money_d(line_gross) - money_d(line_discount_amount))
        if line_total < 0:
            line_total = 0.0

        c.execute(
            "INSERT INTO sale_items(sale_id, item_name, category_id, category_code, "
            "cost_price, sell_price, qty, line_total, "
            "discount_pct, discount_amount, override_price, base_price) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sale_id, name, item.category_id, item.category_code,
             cost, effective_price, item.qty, line_total,
             line_discount_pct, line_discount_amount, override_price, base_price),
        )
    return (zero_cost_categories, deferred_state_sales)


def _sale_insert_cash_drawer(c, payload, total, invoice_no: str, sale_id: int) -> None:
    """Step 10: Cash drawer — cash sales only; split → cash part only."""
    if payload.payment_method == "cash":
        c.execute(
            "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
            "VALUES('sale', ?, ?, ?, 'sale')",
            (total, f"Sale {invoice_no}", sale_id),
        )
    elif payload.payment_method == "split" and payload.split_cash > 0:
        c.execute(
            "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
            "VALUES('sale', ?, ?, ?, 'sale')",
            (money(payload.split_cash), f"Sale {invoice_no} (cash part)", sale_id),
        )


def _sale_apply_stock_state(c, deferred_state_sales: list, sale_id: int, invoice_no: str) -> None:
    """Step 11: Apply stock-state mutation via shared connection.

    v8.18.17: bag categories (see profit_engine.sync_bags_stock_to_sold)
    never take the sale decrement — their stock qty follows the
    max(purchased, sold) rule. After the non-bag lines are applied, the
    bag categories in this sale get the scoped bags sync, which raises
    qty to the new total-sold level when sold has passed it (and leaves
    it alone when purchased is still ahead — the user's exact rule).
    """
    bag_lines = []
    try:
        from ..profit_engine import bag_category_ids as _sale_bag_ids
        sale_bag_ids = _sale_bag_ids(c)
    except Exception:
        sale_bag_ids = set()

    for cid, qty in deferred_state_sales:
        if cid in sale_bag_ids:
            bag_lines.append((cid, qty))
            continue
        try:
            profit_mod.apply_sale_to_state(cid, qty, c=c)
        except Exception as e:
            profit_mod.log_state_drift(
                "apply_sale_to_state", cid, str(e),
                {"sale_id": sale_id, "invoice_no": invoice_no, "qty": qty},
                c=c,
            )

    if bag_lines:
        # Bag categories sold in this sale: raise qty to the new total sold
        # (only when sold now exceeds it — purchased qty is never lowered).
        try:
            from ..profit_engine import sync_bags_stock_to_sold as _sync_bags
            _sync_bags(c, category_ids={cid for cid, _ in bag_lines})
        except Exception as e:
            profit_mod.log_state_drift(
                "sync_bags_stock_to_sold", bag_lines[0][0], str(e),
                {"sale_id": sale_id, "invoice_no": invoice_no,
                 "bag_categories": sorted(cid for cid, _ in bag_lines)},
                c=c,
            )


def _sale_update_customer_stats(c, customer_id: int, payment_status: str, total,
                                  payload, loyalty_used: int, sale_id: int) -> None:
    """Step 12: Customer stats inline — award loyalty on paid portion, track credit."""
    if not customer_id:
        return
    per_rs_row = c.execute(
        "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
    ).fetchone()
    per_rs = float(per_rs_row["value"] or "100") if per_rs_row else 100.0
    per_rs = per_rs if per_rs > 0 else 100.0

    if payment_status == "credit":
        pts = int(total / per_rs)
        c.execute(
            "UPDATE customers SET total_credit = total_credit + ?, "
            "loyalty_points = loyalty_points + ? WHERE id=?",
            (total, pts, customer_id),
        )
    elif payment_status == "partial":
        paid_so_far = money(payload.split_cash + payload.split_card + payload.split_online)
        unpaid_d = max(Decimal("0"), money_d(total) - money_d(paid_so_far))
        if unpaid_d > 0:
            c.execute(
                "UPDATE customers SET total_credit = total_credit + ? WHERE id=?",
                (money(unpaid_d), customer_id),
            )
        pts = int(float(paid_so_far) / per_rs)
        c.execute(
            "UPDATE customers SET total_spent = total_spent + ?, "
            "loyalty_points = loyalty_points + ? WHERE id=?",
            (money(paid_so_far), pts, customer_id),
        )
    else:
        pts = int(total / per_rs)
        c.execute(
            "UPDATE customers SET total_spent = total_spent + ?, "
            "loyalty_points = loyalty_points + ? WHERE id=?",
            (total, pts, customer_id),
        )
    if loyalty_used > 0:
        c.execute(
            "UPDATE loyalty_redemptions SET sale_id=? "
            "WHERE customer_id=? AND sale_id IS NULL",
            (sale_id, customer_id),
        )


def _sale_record_commission(c, payload, sale_id: int, total) -> float:
    """Step 14: Commission — compute + record inline via shared connection."""
    if not payload.employee_id:
        return 0.0
    commission_amount, rule_id = shop_mod.compute_commission_for_sale(
        sale_id, total, payload.employee_id, c=c,
    )
    if commission_amount > 0:
        shop_mod.record_commission(
            sale_id, payload.employee_id, commission_amount, rule_id, c=c,
        )
    return commission_amount









@router.get("/api/sales")
def list_sales(date: str = "", limit: int = 50, offset: int = 0, page: int = 0, page_size: int = 0,
               sort_by: str = "", sort_order: str = "desc") -> Any:
    """List sales, optionally filtered by date. v8.4: supports pagination.
    v8.15.0: Added sort_by + sort_order for dynamic column sorting.

    Args:
        date: optional date filter (YYYY-MM-DD)
        limit: max rows to return (default 50, max 500)
        offset: number of rows to skip (for backward-compat pagination)
        page: 1-based page number (alternative to offset — takes precedence if > 0)
        page_size: rows per page (alternative to limit — takes precedence if > 0)
        sort_by: column to sort by (date, total, customer, payment, invoice)
        sort_order: "asc" or "desc"

    Returns a paginated response: {sales, total, page, page_size, pages_total}
    when page/page_size are used, or a plain list when limit/offset are used (backward compat).
    """
    # Clamp limit to prevent huge queries
    limit = min(max(1, limit), 500)
    # v8.4: page/page_size pagination takes precedence
    use_pagination = page > 0 or page_size > 0
    if use_pagination:
        page = max(1, page)
        page_size = min(max(1, page_size or limit), 500)
        offset = (page - 1) * page_size
        limit = page_size

    # v8.15.0: Dynamic sort
    order_clause = db.validate_sort(sort_by, sort_order, {
        "date": "created_at",
        "total": "total",
        "customer": "customer_name",
        "payment": "payment_method",
        "invoice": "invoice_no",
        "status": "payment_status",
    }, default="created_at DESC, id DESC")

    with db.conn() as c:
        if date:
            total = c.execute("SELECT COUNT(*) AS n FROM sales WHERE date(created_at)=?", (date,)).fetchone()["n"]
            # v8.19.1: clamp the page (last-page deletion / filter shrink)
            if use_pagination:
                page = db.clamp_page(page, total, page_size)
                offset = (page - 1) * page_size
            rows = c.execute(
                f"SELECT * FROM sales WHERE date(created_at)=? ORDER BY {order_clause} LIMIT ? OFFSET ?",
                (date, limit, offset)
            ).fetchall()
        else:
            total = c.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"]
            # v8.19.1: clamp the page (last-page deletion / filter shrink)
            if use_pagination:
                page = db.clamp_page(page, total, page_size)
                offset = (page - 1) * page_size
            rows = c.execute(
                f"SELECT * FROM sales ORDER BY {order_clause} LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()

    sales_list = [dict(r) for r in rows]
    if use_pagination:
        pages_total = (total + page_size - 1) // page_size
        return {
            "sales": sales_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages_total": pages_total,
        }
    # Backward compat: return plain list
    return sales_list




@router.get("/api/sales/summary")
def sales_summary(date: str = "") -> Any:
    """Daily sales summary: revenue, profit, items, by category, by hour.
    Excludes refunded sales from revenue/profit calculations."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    with db.conn() as c:
        sales = c.execute(
            "SELECT * FROM sales WHERE date(created_at)=?", (date,)
        ).fetchall()
        items = c.execute(
            "SELECT si.*, s.created_at, s.payment_status FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE date(s.created_at)=?", (date,)
        ).fetchall()
    # Exclude refunded sales from revenue metrics
    valid_sales = [s for s in sales if s["payment_status"] != "refunded"]
    paid_sales = [s for s in valid_sales if s["payment_status"] == "paid"]
    credit_sales = [s for s in valid_sales if s["payment_status"] == "credit"]
    partial_sales = [s for s in valid_sales if s["payment_status"] == "partial"]
    # Net revenue = sum of all non-refunded sale totals (already net of discount + loyalty)
    total_revenue = sum(s["total"] for s in valid_sales)
    # Cash & card & credit breakdowns (for Z-report reconciliation)
    total_cash = sum(s["total"] for s in valid_sales if s["payment_method"] == "cash")
    total_card = sum(s["total"] for s in valid_sales if s["payment_method"] == "card")
    total_online = sum(s["total"] for s in valid_sales if s["payment_method"] == "online")
    total_credit = sum(s["total"] for s in credit_sales)
    total_partial = sum(s["total"] for s in partial_sales)
    # Cost & profit (based on sale_items, excluding refunded)
    valid_items = [i for i in items if i["payment_status"] != "refunded"]
    total_items = sum(i["qty"] for i in valid_items)  # v8.9.1: use valid_items (excludes refunded)
    total_cost = sum(i["cost_price"] * i["qty"] for i in valid_items)
    total_profit = total_revenue - total_cost
    margin = total_profit / total_revenue if total_revenue > 0 else 0
    from collections import defaultdict
    by_cat = defaultdict(lambda: {"items": 0, "revenue": 0, "profit": 0, "qty": 0})
    for i in valid_items:
        code = i["category_code"] or "—"
        by_cat[code]["items"] += 1
        by_cat[code]["qty"] += i["qty"]
        by_cat[code]["revenue"] += i["sell_price"] * i["qty"]
        by_cat[code]["profit"] += (i["sell_price"] - i["cost_price"]) * i["qty"]
    cat_list = [{"code": k, "items": v["items"], "qty": v["qty"],
                 "revenue": round(v["revenue"], 2), "profit": round(v["profit"], 2)}
                for k, v in sorted(by_cat.items())]
    by_hour = defaultdict(lambda: {"count": 0, "total": 0})
    for s in valid_sales:
        try:
            hour = int(s["created_at"][11:13])
            by_hour[hour]["count"] += 1
            by_hour[hour]["total"] += s["total"]
        except Exception as _e:
            logger.warning("Silent exception in pos.py: %s", _e, exc_info=True)
    hour_list = [{"hour": f"{h:02d}:00", "count": v["count"], "total": round(v["total"], 2)}
                 for h, v in sorted(by_hour.items())]
    return {
        "date": date,
        "sale_count": len(sales),
        "valid_count": len(valid_sales),
        "paid_count": len(paid_sales),
        "credit_count": len(credit_sales),
        "partial_count": len(partial_sales),
        "refunded_count": len(sales) - len(valid_sales),
        "total_sales": round(total_revenue, 2),  # alias for backward compat
        "total_revenue": round(total_revenue, 2),
        "total_cash": round(total_cash, 2),
        "total_card": round(total_card, 2),
        "total_online": round(total_online, 2),
        "total_credit": round(total_credit, 2),
        "total_partial": round(total_partial, 2),
        "total_items": total_items,
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_profit, 2),
        "margin": round(margin, 2),
        "by_category": cat_list,
        "by_hour": hour_list,
    }




@router.get("/api/sales/z-report")
def z_report(date: str = "") -> Any:
    """End-of-day Z-report: complete reconciliation for closing the till."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    summary = sales_summary(date)
    with db.conn() as c:
        sales = c.execute(
            "SELECT * FROM sales WHERE date(created_at)=? ORDER BY id", (date,)
        ).fetchall()
    from collections import defaultdict
    by_payment = defaultdict(lambda: {"count": 0, "total": 0})
    refund_by_payment = defaultdict(lambda: {"count": 0, "total": 0})  # v8.9.1: show refunds separately
    for s in sales:
        if s["payment_status"] == "refunded":
            refund_by_payment[s["payment_method"]]["count"] += 1
            refund_by_payment[s["payment_method"]]["total"] += s["total"]
            continue  # v8.9.1: skip refunded in payment totals
        by_payment[s["payment_method"]]["count"] += 1
        by_payment[s["payment_method"]]["total"] += s["total"]
    payment_breakdown = [{"method": k, "count": v["count"], "total": round(v["total"], 2)}
                         for k, v in sorted(by_payment.items())]
    refund_breakdown = [{"method": k, "count": v["count"], "total": round(v["total"], 2)}
                        for k, v in sorted(refund_by_payment.items())]  # v8.9.1
    first_sale = sales[0]["invoice_no"] if sales else None
    last_sale = sales[-1]["invoice_no"] if sales else None
    return {
        **summary,
        "payment_breakdown": payment_breakdown,
        "refund_breakdown": refund_breakdown,  # v8.9.1: show refunds separately
        "refund_count": sum(v["count"] for v in refund_by_payment.values()),
        "refund_total": round(sum(v["total"] for v in refund_by_payment.values()), 2),
        "first_invoice": first_sale,
        "last_invoice": last_sale,
        "cash_expected": round(by_payment.get("cash", {"total": 0})["total"], 2),
        "card_total": round(by_payment.get("card", {"total": 0})["total"], 2),
        "credit_total": round(by_payment.get("credit", {"total": 0})["total"], 2),
    }




@router.get("/api/sales/{sale_id}")
def get_sale(sale_id: int) -> Any:
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        items = c.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    return {**dict(sale), "items": [dict(i) for i in items]}




# ════════════════════════════════════════════════════════════════════════════════
# v8.9.1 Phase 3: _reverse_sale_core — the canonical reversal function
# ════════════════════════════════════════════════════════════════════════════════
def _reverse_sale_core(sale_id: int, c, reason: str = ""):
    """Reverse ALL side effects of a sale. Does NOT update payment_status.

    Caller is responsible for: PIN validation, updating payment_status, audit log.
    This function owns: stock reversal, customer stats, cash drawer, commission, loyalty.
    """
    sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        raise HTTPException(404, "sale not found")
    if sale["payment_status"] == "refunded":
        return {"reversed_stock_lines": 0, "reversed_loyalty": 0,
                "reversed_commission": 0.0, "refund_cash_amount": None,
                "idempotent": True}

    sale_items = c.execute(
        "SELECT category_id, qty, cost_price FROM sale_items WHERE sale_id=?",
        (sale_id,),
    ).fetchall()

    # Cash drawer reversal — split-payment-aware
    refund_cash_amount = None
    if sale["payment_method"] == "cash":
        refund_cash_amount = float(sale["total"])
    elif sale["payment_method"] == "split" and sale["split_cash"] and sale["split_cash"] > 0:
        refund_cash_amount = float(sale["split_cash"])
    if refund_cash_amount is not None and refund_cash_amount > 0:
        drawer_desc = f"Reversal {sale['invoice_no']}"
        if reason:
            drawer_desc += f" \u2014 {reason[:80]}"
        c.execute(
            "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
            "VALUES('refund', ?, ?, ?, 'sale')",
            (-refund_cash_amount, drawer_desc, sale_id),
        )

    # Reverse stock state
    # v8.18.17: bag categories NEVER take the sale decrement on ANY origin
    # (Ezi import skips bag lines; built-in POS sales skip them too since
    # v8.18.17 — their stock tracks "qty sold" via
    # profit_engine.sync_bags_stock_to_sold instead; see the bags block
    # comment there). Re-adding bag qty here would double-bump the stock,
    # so bag lines are always skipped. Bag qty only ever rises to the
    # current total-sold level (never lowered), and refunded sales stop
    # counting as sold automatically.
    skip_bag_lines = set()
    try:
        from ..profit_engine import bag_category_ids as _bag_cat_ids
        skip_bag_lines = _bag_cat_ids(c)
    except Exception:
        skip_bag_lines = set()
    reversed_stock_lines = 0
    for si in sale_items:
        if si["category_id"] and si["qty"] and si["qty"] > 0:
            if si["category_id"] in skip_bag_lines:
                continue
            try:
                cogs_value = None
                if si["cost_price"] and si["cost_price"] > 0:
                    cogs_value = round(float(si["qty"]) * float(si["cost_price"]), 2)
                profit_mod.reverse_sale_in_state(
                    si["category_id"], float(si["qty"]),
                    cogs=cogs_value, c=c,
                )
                reversed_stock_lines += 1
            except Exception as e:
                profit_mod.log_state_drift(
                    "reverse_sale_in_state", si["category_id"], str(e),
                    {"sale_id": sale_id, "qty": float(si["qty"]),
                     "cost_price": float(si["cost_price"] or 0)},
                    c=c,
                )

    # Customer stats reversal
    reversed_loyalty = 0
    if sale["customer_id"]:
        cust = c.execute(
            "SELECT total_spent, total_credit, loyalty_points FROM customers WHERE id=?",
            (sale["customer_id"],),
        ).fetchone()
        if cust:
            original_status = sale["payment_status"]
            per_rs_row = c.execute(
                "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
            ).fetchone()
            per_rs = float(per_rs_row["value"] or "100") if per_rs_row else 100.0
            per_rs = per_rs if per_rs > 0 else 100.0

            if original_status == "credit":
                pts_awarded = int(sale["total"] / per_rs)
                c.execute(
                    "UPDATE customers SET total_credit = MAX(0, total_credit - ?), "
                    "loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?",
                    (sale["total"], pts_awarded, sale["customer_id"]),
                )
            elif original_status == "partial":
                paid_so_far = float(sale["split_cash"] or 0) + float(sale["split_card"] or 0) + float(sale["split_online"] or 0)
                unpaid = max(0.0, float(sale["total"]) - paid_so_far)
                pts_awarded = int(paid_so_far / per_rs)
                c.execute(
                    "UPDATE customers SET "
                    "total_credit = MAX(0, total_credit - ?), "
                    "total_spent = MAX(0, total_spent - ?), "
                    "loyalty_points = MAX(0, loyalty_points - ?) "
                    "WHERE id=?",
                    (unpaid, paid_so_far, pts_awarded, sale["customer_id"]),
                )
            else:
                pts_awarded = int(sale["total"] / per_rs)
                c.execute(
                    "UPDATE customers SET total_spent = MAX(0, total_spent - ?), "
                    "loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?",
                    (sale["total"], pts_awarded, sale["customer_id"]),
                )

        # Loyalty redemption restoration
        if sale["loyalty_points_used"] and sale["loyalty_points_used"] > 0:
            reversed_loyalty = int(sale["loyalty_points_used"])
            c.execute(
                "UPDATE customers SET loyalty_points = loyalty_points + ?, "
                "loyalty_redeemed = MAX(0, loyalty_redeemed - ?) WHERE id=?",
                (reversed_loyalty, reversed_loyalty, sale["customer_id"]),
            )
            c.execute(
                "UPDATE loyalty_redemptions SET reversed_at=datetime('now','localtime') "
                "WHERE sale_id=? AND reversed_at IS NULL",
                (sale_id,),
            )

    # Commission reversal — idempotent
    reversed_commission_amount = 0.0
    comm_cur = c.execute(
        "SELECT id, amount FROM commissions WHERE sale_id=? AND reversed = 0",
        (sale_id,),
    ).fetchall()
    for comm in comm_cur:
        reversed_commission_amount += float(comm["amount"])
        c.execute(
            "UPDATE commissions SET reversed = 1, reversed_at=datetime('now','localtime') "
            "WHERE id=?",
            (comm["id"],),
        )

    return {
        "reversed_stock_lines": reversed_stock_lines,
        "reversed_loyalty": reversed_loyalty,
        "reversed_commission": reversed_commission_amount,
        "refund_cash_amount": refund_cash_amount,
        "idempotent": False,
    }


# v8.9.1 Phase 3: Admin void — replaces the dangerous naked DELETE
@router.post("/api/sales/{sale_id}/void")
def void_sale(sale_id: int, payload: dict = None, request: Request = None) -> Any:
    """Admin void — reverses all side effects but preserves the sale row.

    Requires manager PIN. Does NOT hard-delete — marks as refunded with
    refund_reason='admin_void' and manually_overridden=1.

    H4 fix (v8.13.4): PIN failures now throttle per-IP AND per-employee
    (auto-locks employee for 15 min after 5 failures in 60s).
    """
    from ..security import check_pin_throttle, record_failed_pin
    body = payload or {}
    manager_pin = body.get("manager_pin", "")
    reason = body.get("reason", "")
    # Look up the employee by pin to throttle per-employee
    # verify_manager_pin returns the row if pin matches
    client_ip = request.client.host if (request and request.client) else "unknown"
    # Pre-flight throttle check using a placeholder employee_id of 0
    # (we don't know which manager yet — record_failed_pin uses employee_id)
    # If the throttle trips on IP alone, we don't even check the pin.
    if not check_pin_throttle(0, client_ip):
        raise HTTPException(429, "Too many PIN attempts. Wait 60 seconds.")
    mgr = shop_mod.verify_manager_pin(manager_pin) if manager_pin else None
    if not mgr:
        # H4: record failure + check throttle
        emp_id = mgr["id"] if mgr and "id" in mgr.keys() else 0
        record_failed_pin(emp_id, client_ip)
        raise HTTPException(403, "Manager PIN required for admin void")

    with db.write_tx() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        if sale["payment_status"] == "refunded":
            return {"ok": True, "idempotent": True, "message": "already refunded"}

        result = _reverse_sale_core(sale_id, c, reason=f"admin void: {reason}")

        c.execute(
            "UPDATE sales SET payment_status='refunded', "
            "refunded_at=datetime('now','localtime'), "
            "refund_reason='admin_void', "
            "manually_overridden=1 "
            "WHERE id=?",
            (sale_id,),
        )

        db.log_activity(
            "sale_admin_voided", "sale", sale_id,
            f"Sale {sale['invoice_no']} admin-voided by {mgr['name']}"
            + (f" \u2014 reason: {reason}" if reason else ""),
            {
                "invoice": sale["invoice_no"],
                "total": float(sale["total"]),
                "payment_method": sale["payment_method"],
                "manager": mgr["name"],
                "manager_id": mgr["id"],
                "reason": reason,
                "reversed_stock_lines": result["reversed_stock_lines"],
                "reversed_loyalty_points": result["reversed_loyalty"],
                "reversed_commission": result["reversed_commission"] if result["reversed_commission"] > 0 else None,
                "refund_cash_amount": result["refund_cash_amount"],
            },
            c=c,
        )

    return {
        "ok": True,
        "voided": True,
        "sale_id": sale_id,
        "invoice_no": sale["invoice_no"],
        **result,
    }


# Backward-compat: DELETE /api/sales/{id} now calls void (no hard-delete)
@router.delete("/api/sales/{sale_id}")
def delete_sale(sale_id: int) -> Any:
    """DEPRECATED \u2014 use POST /api/sales/{sale_id}/void instead."""
    return void_sale(sale_id, payload={"manager_pin": "", "reason": "DELETE endpoint (deprecated)"})




@router.post("/api/sales/{sale_id}/refund")
def refund_sale(sale_id: int, payload: dict = None, request: Request = None) -> Any:
    """Process a refund — marks sale as refunded, reverses all side effects.

    Phase 0 PR 4 — Atomic refund:
    The entire refund — sale status update, cash_drawer reversal, stock_state
    restoration, customer stats reversal, loyalty restoration, commission
    reversal, activity_log + suspicious entries — commits as a SINGLE atomic
    write transaction via `db.write_tx()` (BEGIN IMMEDIATE). If ANY step fails,
    ALL roll back together. No more half-refunded sales.

    Reviewer 3 correction (Split-Payment Trap):
    The cash_drawer reversal amount depends on the ORIGINAL payment method:
      - cash         → reverse full `total` into cash_drawer (type='refund', -total)
      - card/online  → do NOT insert a cash_drawer row (drawer was never touched)
      - credit       → do NOT insert a cash_drawer row (drawer was never touched)
      - split        → reverse ONLY `split_cash` into cash_drawer (not the full total)

    Reviewer 1 suggestions:
      - `refund_reason` is captured in cash_drawer.description AND activity_log.metadata
      - Commission reversal is idempotent: `WHERE reversed = 0` guard prevents double-reverse
      - Loyalty restoration is also idempotent (only restores if loyalty_points_used > 0)

    Reviewer 2 edge case:
      - Walk-in customers (customer_id IS NULL) skip customer stats + loyalty reversal

    H4 fix (v8.13.4): PIN failures now throttle per-IP AND per-employee
    (auto-locks employee for 15 min after 5 failures in 60s).
    """
    from ..security import check_pin_throttle, record_failed_pin
    body = payload or {}
    require_pin = (db.get_setting("require_pin_for_refund", "true") or "true").lower() == "true"
    manager_pin = body.get("manager_pin", "")
    reason = body.get("reason", "")
    mgr = None
    client_ip = request.client.host if (request and request.client) else "unknown"
    # Pre-flight IP throttle — fail fast before any DB write
    if not check_pin_throttle(0, client_ip):
        raise HTTPException(429, "Too many PIN attempts. Wait 60 seconds.")
    # PRE-FLIGHT: verify PIN before taking the write lock (verify_manager_pin
    # opens its own read connection which would deadlock against our pending
    # BEGIN IMMEDIATE).
    if require_pin:
        mgr = shop_mod.verify_manager_pin(manager_pin) if manager_pin else None
        if not mgr:
            # H4: record failure for throttle + lockout
            record_failed_pin(0, client_ip)
            raise HTTPException(403, "Manager PIN required for refund")

    # ─── Atomic write transaction ───────────────────────────────────────────
    with db.write_tx() as c:
        # (1) SELECT sale — 404 if missing
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        # (2) Idempotency: re-check inside txn (prevents race where two refund
        # requests both pass the read check)
        if sale["payment_status"] == "refunded":
            raise HTTPException(400, "sale already refunded")

        # (3) Capture sale_items for stock-state reversal
        sale_items = c.execute(
            "SELECT category_id, qty, cost_price FROM sale_items WHERE sale_id=?",
            (sale_id,),
        ).fetchall()

        # (4) Mark sale as refunded (with timestamp)
        c.execute(
            "UPDATE sales SET payment_status='refunded', "
            "refunded_at=datetime('now','localtime') WHERE id=?",
            (sale_id,),
        )

        # (5) Cash drawer reversal — REVIEWER 3 split-payment trap fix.
        # Only reverse the amount that actually went INTO the cash drawer
        # during create_sale (see create_sale step 10).
        refund_cash_amount = None
        if sale["payment_method"] == "cash":
            refund_cash_amount = sale["total"]
        elif sale["payment_method"] == "split" and sale["split_cash"] and sale["split_cash"] > 0:
            refund_cash_amount = sale["split_cash"]
        # card / online / credit / bank / raast / easypaisa / jazzcash → no drawer row
        if refund_cash_amount is not None and refund_cash_amount > 0:
            drawer_desc = f"Refund {sale['invoice_no']}"
            if reason:
                drawer_desc += f" — {reason[:80]}"
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('refund', ?, ?, ?, 'sale')",
                (-float(refund_cash_amount), drawer_desc, sale_id),
            )

        # (6) Reverse stock state — restore qty + value to each category pool.
        # reverse_sale_in_state already accepts c= (PR 2). Use the original
        # cost_price captured at sale time so the value restoration matches.
        # H11 fix (v8.13.4): if reversal fails, the entire refund rolls back
        # via the write_tx context manager. Previously, the error was logged
        # but swallowed — the sale row was marked refunded while stock_state
        # stayed inconsistent with the ledger. Now we surface the error.
        # v8.18.17: bag categories NEVER take the sale decrement on any
        # origin (Ezi import or built-in POS — see
        # profit_engine.sync_bags_stock_to_sold), so their lines are never
        # re-added here either. Re-adding would double-bump bag stock.
        refund_skip_bag_lines = set()
        try:
            from ..profit_engine import bag_category_ids as _refund_bag_ids
            refund_skip_bag_lines = _refund_bag_ids(c)
        except Exception:
            refund_skip_bag_lines = set()
        reversed_stock_lines = 0
        for si in sale_items:
            if si["category_id"] and si["qty"] and si["qty"] > 0:
                if si["category_id"] in refund_skip_bag_lines:
                    continue
                try:
                    cogs_value = None
                    if si["cost_price"] and si["cost_price"] > 0:
                        cogs_value = round(float(si["qty"]) * float(si["cost_price"]), 2)
                    profit_mod.reverse_sale_in_state(
                        si["category_id"], float(si["qty"]),
                        cogs=cogs_value, c=c,
                    )
                    reversed_stock_lines += 1
                except Exception as e:
                    # H11: log drift for visibility, THEN re-raise so the
                    # surrounding write_tx() rolls back the entire refund
                    # atomically. The sale row is NOT marked refunded until
                    # stock_state is also reversed.
                    profit_mod.log_state_drift(
                        "reverse_sale_in_state", si["category_id"], str(e),
                        {"sale_id": sale_id, "qty": float(si["qty"]),
                         "cost_price": float(si["cost_price"] or 0)},
                        c=c,
                    )
                    raise HTTPException(
                        500,
                        f"Stock reversal failed for category {si['category_id']}: {e}. "
                        "Refund rolled back — sale is unchanged. Run "
                        "/api/inventory/rebuild-stock-state to repair state."
                    )

        # (7) Customer stats reversal — only if a customer was attached.
        # Walk-in customers (customer_id IS NULL) skip this step (Reviewer 2).
        reversed_loyalty = 0
        if sale["customer_id"]:
            cust = c.execute(
                "SELECT total_spent, total_credit, loyalty_points FROM customers WHERE id=?",
                (sale["customer_id"],),
            ).fetchone()
            if cust:
                # Reverse the appropriate customer stat column based on the
                # original payment_status. Use the SAME logic as create_sale:
                #   - 'credit'  → total_credit was += total, loyalty_points was += pts
                #   - 'paid'    → total_spent was += total, loyalty_points was += pts
                #   - 'partial' → total_credit was += unpaid, total_spent was += paid
                original_status = sale["payment_status"]
                # Reverse loyalty points AWARDED on the sale (NOT redemption —
                # that's handled separately in step 8). The awarded pts were:
                #   int(amount / per_rs) where amount = total (credit) or paid (partial)
                per_rs_row = c.execute(
                    "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
                ).fetchone()
                per_rs = float(per_rs_row["value"] or "100") if per_rs_row else 100.0
                per_rs = per_rs if per_rs > 0 else 100.0

                if original_status == "credit":
                    # was: total_credit += total, pts += int(total/per_rs)
                    pts_awarded = int(sale["total"] / per_rs)
                    c.execute(
                        "UPDATE customers SET total_credit = MAX(0, total_credit - ?), "
                        "loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?",
                        (sale["total"], pts_awarded, sale["customer_id"]),
                    )
                elif original_status == "partial":
                    # was: total_credit += unpaid, total_spent += paid, pts += int(paid/per_rs)
                    paid_so_far = float(sale["split_cash"] or 0) + float(sale["split_card"] or 0) + float(sale["split_online"] or 0)
                    unpaid = max(0.0, float(sale["total"]) - paid_so_far)
                    pts_awarded = int(paid_so_far / per_rs)
                    c.execute(
                        "UPDATE customers SET "
                        "total_credit = MAX(0, total_credit - ?), "
                        "total_spent = MAX(0, total_spent - ?), "
                        "loyalty_points = MAX(0, loyalty_points - ?) "
                        "WHERE id=?",
                        (unpaid, paid_so_far, pts_awarded, sale["customer_id"]),
                    )
                else:  # 'paid' (or any other status treated as paid)
                    pts_awarded = int(sale["total"] / per_rs)
                    c.execute(
                        "UPDATE customers SET total_spent = MAX(0, total_spent - ?), "
                        "loyalty_points = MAX(0, loyalty_points - ?) WHERE id=?",
                        (sale["total"], pts_awarded, sale["customer_id"]),
                    )

            # (8) Loyalty redemption restoration — only if the customer used
            # loyalty points on the original sale. The sale row's
            # loyalty_points_used column captures this. Restore the points and
            # decrement loyalty_redeemed (mirror of create_sale step 3).
            if sale["loyalty_points_used"] and sale["loyalty_points_used"] > 0:
                reversed_loyalty = int(sale["loyalty_points_used"])
                c.execute(
                    "UPDATE customers SET loyalty_points = loyalty_points + ?, "
                    "loyalty_redeemed = MAX(0, loyalty_redeemed - ?) WHERE id=?",
                    (reversed_loyalty, reversed_loyalty, sale["customer_id"]),
                )
                # Soft-delete the loyalty_redemptions row(s) linked to this sale
                # (mark as reversed; don't hard-delete — keeps audit trail)
                c.execute(
                    "UPDATE loyalty_redemptions SET reversed_at=datetime('now','localtime') "
                    "WHERE sale_id=? AND reversed_at IS NULL",
                    (sale_id,),
                )

        # (9) Commission reversal — idempotent (WHERE reversed = 0).
        # Marks the commission row as reversed (soft-delete); does NOT
        # hard-delete so historical commission reports remain auditable.
        reversed_commission_amount = 0.0
        comm_cur = c.execute(
            "SELECT id, amount FROM commissions WHERE sale_id=? AND reversed = 0",
            (sale_id,),
        ).fetchall()
        for comm in comm_cur:
            reversed_commission_amount += float(comm["amount"])
            c.execute(
                "UPDATE commissions SET reversed = 1, reversed_at=datetime('now','localtime') "
                "WHERE id=?",
                (comm["id"],),
            )

        # (10) Activity log — sale_refunded (with full metadata for reporting)
        db.log_activity(
            "sale_refunded", "sale", sale_id,
            f"Refunded {sale['invoice_no']} — Rs {sale['total']:.0f}"
            + (f" — reason: {reason}" if reason else ""),
            {
                "invoice": sale["invoice_no"],
                "total": float(sale["total"]),
                "payment_method": sale["payment_method"],
                "refund_cash_amount": refund_cash_amount,
                "manager": mgr["name"] if mgr else None,
                "reason": reason,
                "reversed_stock_lines": reversed_stock_lines,
                "reversed_loyalty_points": reversed_loyalty,
                "reversed_commission": reversed_commission_amount if reversed_commission_amount > 0 else None,
            },
            c=c,
        )

        # (11) Suspicious event log (reviewers 1+2 agreed refunds should always
        # be auditable; suspicious log captures the manager who approved)
        suspicious_meta = {
            "original_event": "refund",
            "invoice": sale["invoice_no"],
            "total": float(sale["total"]),
            "payment_method": sale["payment_method"],
            "reason": reason,
            "manager_pin_provided": bool(manager_pin),
            "manager_id": mgr["id"] if mgr else None,
            "manager_name": mgr["name"] if mgr else None,
        }
        db.log_activity(
            "suspicious", "sale", sale_id,
            f"[refund] Refunded {sale['invoice_no']} — Rs {sale['total']:.0f}"
            + (f" (approved by {mgr['name']})" if mgr else "")
            + (f" — reason: {reason}" if reason else ""),
            suspicious_meta,
            c=c,
        )

    # ─── Post-commit return ────────────────────────────────────────────────
    return {
        "ok": True,
        "refunded_amount": float(sale["total"]),
        "refund_cash_amount": refund_cash_amount,
        "reversed_stock_lines": reversed_stock_lines,
        "reversed_loyalty_points": reversed_loyalty,
        "reversed_commission": reversed_commission_amount if reversed_commission_amount > 0 else None,
    }









@router.get("/api/sales/{sale_id}/receipt")
def sale_receipt(sale_id: int) -> Any:
    """Generate a printable 80mm receipt as HTML with shop profile (NTN/STRN)."""
    from ..db import get_setting
    shop_name = get_setting("shop_name", "BillBook Store")
    shop_address = get_setting("shop_address", "")
    shop_phone = get_setting("shop_phone", "")
    ntn = get_setting("shop_ntn", "")
    strn = get_setting("shop_strn", "")
    footer = get_setting("receipt_footer", "Thank you for your business!")
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        items = c.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    raast_ref = sale["raast_reference"] if "raast_reference" in sale.keys() and sale["raast_reference"] else None
    receipt_html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Receipt {sale['invoice_no']}</title>
<style>
body{{font-family:monospace;font-size:12px;max-width:300px;margin:0 auto;padding:20px}}
h2{{text-align:center;margin:0}}
hr{{border:none;border-top:1px dashed #999;margin:8px 0}}
.row{{display:flex;justify-content:space-between}}
.total{{font-weight:bold;font-size:14px}}
.center{{text-align:center}}
</style></head><body>
<h2>{shop_name}</h2>
"""
    if shop_address:
        receipt_html += f"<p class='center'>{shop_address}</p>"
    if shop_phone:
        receipt_html += f"<p class='center'>Tel: {shop_phone}</p>"
    if ntn:
        receipt_html += f"<p class='center'>NTN: {ntn}</p>"
    if strn:
        receipt_html += f"<p class='center'>STRN: {strn}</p>"
    receipt_html += f"""<hr>
<div class="row"><span>Invoice:</span><span>{sale['invoice_no']}</span></div>
<div class="row"><span>Date:</span><span>{sale['created_at']}</span></div>
"""
    if sale['customer_name']:
        receipt_html += f"<div class='row'><span>Customer:</span><span>{sale['customer_name']}</span></div>"
    receipt_html += "<hr>"
    for i in items:
        receipt_html += f"<div class='row'><span>{i['item_name']} x{i['qty']}</span><span>Rs {i['line_total']:.0f}</span></div>\n"
    receipt_html += f"<hr><div class='row'><span>Subtotal:</span><span>Rs {sale['subtotal']:.0f}</span></div>"
    if sale['discount'] > 0:
        receipt_html += f"<div class='row'><span>Discount:</span><span>-Rs {sale['discount']:.0f}</span></div>"
    if sale['tax_amount'] and sale['tax_amount'] > 0:
        receipt_html += f"<div class='row'><span>Tax ({(sale['tax_rate'] or 0)*100:.0f}%):</span><span>Rs {sale['tax_amount']:.0f}</span></div>"
    receipt_html += f"""<div class="row total"><span>TOTAL:</span><span>Rs {sale['total']:.0f}</span></div>
<div class="row"><span>Payment:</span><span>{sale['payment_method']}</span></div>
"""
    if raast_ref:
        receipt_html += f"<div class='row'><span>Raast Ref:</span><span>{raast_ref}</span></div>"
    receipt_html += f"""<hr>
<p class="center">{footer}</p>
</body></html>"""
    return HTMLResponse(receipt_html)




@router.get("/api/sales/{sale_id}/whatsapp")
def sale_whatsapp_receipt(sale_id: int) -> Any:
    """Generate WhatsApp link with receipt text."""
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        items = c.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchall()
    phone = sale["customer_phone"] or ""
    phone_clean = re.sub(r"[\s\-+]", "", phone)
    if phone_clean.startswith("03"):
        phone_clean = "92" + phone_clean[1:]
    msg = f"BillBook Receipt\\nInvoice: {sale['invoice_no']}\\nDate: {sale['created_at']}\\n\\n"
    for i in items:
        msg += f"{i['item_name']} x{i['qty']}: Rs {i['line_total']:.0f}\\n"
    msg += f"\\nSubtotal: Rs {sale['subtotal']:.0f}\\n"
    if sale["discount"] > 0:
        msg += f"Discount: -Rs {sale['discount']:.0f}\\n"
    msg += f"TOTAL: Rs {sale['total']:.0f}\\nPayment: {sale['payment_method']}\\n\\nThank you for shopping!"
    url = f"https://wa.me/{phone_clean}?text={quote(msg)}" if phone_clean else None
    return {"url": url, "message": msg}


# ------------------------------------------------------------------
# Groq Business Intelligence (text-only, not vision)
# ------------------------------------------------------------------



@router.get("/api/cash-drawer")
def cash_drawer_status() -> Any:
    return shop_mod.get_cash_drawer_status()




@router.post("/api/cash-drawer/open")
def open_drawer(opening_cash: float = 0) -> Any:
    shop_mod.open_cash_drawer(opening_cash)
    db.log_activity("drawer_opened", "cash_drawer", None,
                    f"Cash drawer opened with Rs {opening_cash:.0f}")
    return {"ok": True}




@router.post("/api/cash-drawer/close")
def close_drawer(closing_cash: float = 0) -> Any:
    result = shop_mod.close_cash_drawer(closing_cash)
    db.log_activity("drawer_closed", "cash_drawer", None,
                    f"Drawer closed: expected Rs {result['expected_cash']:.0f}, actual Rs {closing_cash:.0f}, "
                    f"diff Rs {result['difference']:.0f}")
    return result


# ------------------------------------------------------------------
# Employees & Shifts
# ------------------------------------------------------------------



@router.get("/api/employees")
def list_employees() -> Any:
    return {"employees": shop_mod.get_employees()}




@router.post("/api/employees")
def add_employee_route(name: str = "", phone: str = "", role: str = "cashier") -> Any:
    eid = shop_mod.add_employee(name, phone, role)
    return {"id": eid}




@router.put("/api/employees/{eid}")
def update_employee_route(eid: int, payload: EmployeeUpdateIn) -> Any:
    ok = shop_mod.update_employee(eid, payload.name, payload.phone, payload.role,
                                  payload.active, payload.monthly_salary)
    if not ok:
        raise HTTPException(404, "employee not found or no fields to update")
    db.log_activity("employee_updated", "employee", eid,
                    f"Employee {eid} updated",
                    {"name": payload.name, "role": payload.role,
                     "monthly_salary": payload.monthly_salary})
    return {"ok": True}




@router.delete("/api/employees/{eid}")
def delete_employee_route(eid: int) -> Any:
    ok = shop_mod.delete_employee(eid)
    if not ok:
        raise HTTPException(404, "employee not found")
    db.log_activity("employee_deactivated", "employee", eid, f"Employee {eid} deactivated", {})
    return {"ok": True}




@router.post("/api/employees/{eid}/pin")
def set_employee_pin_route(eid: int, payload: EmployeePinIn) -> Any:
    ok = shop_mod.set_employee_pin(eid, payload.pin)
    if not ok:
        raise HTTPException(400, "PIN must be 4-8 digits")
    db.log_activity("employee_pin_set", "employee", eid, f"PIN set for employee {eid}", {})
    return {"ok": True}


# ------------------------------------------------------------------
# Appearance settings (theme + density)
# ------------------------------------------------------------------



@router.get("/api/shifts/current")
def current_shift() -> Any:
    return {"shift": shop_mod.get_active_shift()}




@router.get("/api/shifts")
def list_shifts(limit: int = 50) -> Any:
    return {"shifts": shop_mod.list_shifts(limit)}




@router.post("/api/shifts/start")
def start_shift_route(employee_id: int = 0, opening_cash: float = 0) -> Any:
    sid = shop_mod.start_shift(employee_id, opening_cash)
    return {"id": sid}




@router.post("/api/shifts/end")
def end_shift_route(closing_cash: float = 0) -> Any:
    return shop_mod.end_shift(closing_cash)


# v4.0 Phase 4: extended shift end with denominations + blind close
class ShiftEndIn(BaseModel):
    closing_cash: Optional[float] = None
    denominations: dict = None  # {5000: n, 1000: n, ..., coins: total}
    blind: bool = False
    manager_pin: Optional[str] = None


@router.post("/api/shifts/end-v2")
def end_shift_v2_route(payload: ShiftEndIn) -> Any:
    """End the active shift with denomination count + variance computation."""
    result = shop_mod.end_shift_with_denominations(
        closing_cash=payload.closing_cash,
        denominations=payload.denominations,
        blind=payload.blind,
        manager_pin=payload.manager_pin,
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/api/alerts/suspicious")
def suspicious_alerts(limit: int = 100) -> Any:
    """List recent suspicious activity (discounts, refunds, price overrides, variances)."""
    return {"alerts": shop_mod.list_suspicious_events(limit)}


@router.get("/api/employees/{eid}/variance")
def employee_variance(eid: int) -> Any:
    """Variance history for an employee's closed shifts."""
    return {"employee_id": eid, "variance_history": shop_mod.get_employee_variance_history(eid)}


# ------------------------------------------------------------------
# P&L (Profit & Loss)
# ------------------------------------------------------------------



@router.get("/api/pos/holds")
def list_holds() -> Any:
    return {"holds": shop_mod.list_held_orders()}




@router.post("/api/pos/holds")
def create_hold(payload: HeldOrderIn) -> Any:
    res = shop_mod.hold_order(
        payload.customer_name, payload.customer_phone, payload.notes,
        payload.items, payload.discount, payload.discount_type, payload.total,
    )
    db.log_activity("hold_created", "held_order", res["id"],
                    f"Held order {res['reference']} — {len(payload.items)} items, Rs {payload.total:.0f}")
    return res




@router.get("/api/pos/holds/{hid}")
def get_hold(hid: int) -> Any:
    h = shop_mod.recall_held_order(hid)
    if not h:
        raise HTTPException(404, "held order not found")
    return h




@router.delete("/api/pos/holds/{hid}")
def remove_hold(hid: int) -> Any:
    ok = shop_mod.delete_held_order(hid)
    if not ok:
        raise HTTPException(404, "held order not found")
    return {"ok": True}


# ------------------------------------------------------------------
# Quotations
# ------------------------------------------------------------------



@router.get("/api/quotations")
def list_quotes_route(status: str = "") -> Any:
    return {"quotations": shop_mod.list_quotations(status)}




@router.post("/api/quotations")
def create_quote_route(payload: QuotationIn) -> Any:
    res = shop_mod.create_quotation(
        payload.customer_name, payload.customer_phone, payload.notes,
        payload.items, payload.discount, payload.discount_type,
        payload.total, payload.valid_days,
    )
    db.log_activity("quotation_created", "quotation", res["id"],
                    f"Quotation {res['quote_no']} — Rs {payload.total:.0f}")
    return res




@router.get("/api/quotations/{qid}")
def get_quote_route(qid: int) -> Any:
    q = shop_mod.get_quotation(qid)
    if not q:
        raise HTTPException(404, "quotation not found")
    return q




@router.delete("/api/quotations/{qid}")
def delete_quote_route(qid: int) -> Any:
    ok = shop_mod.delete_quotation(qid)
    if not ok:
        raise HTTPException(404, "quotation not found")
    return {"ok": True}




@router.get("/api/quotations/{qid}/receipt")
def quote_receipt(qid: int) -> Any:
    """Printable quotation document."""
    q = shop_mod.get_quotation(qid)
    if not q:
        raise HTTPException(404, "quotation not found")
    items_html = ""
    for it in q["items"]:
        items_html += (
            f"<div class='row'><span>{esc(it.get('item_name', it.get('code', '—')))} x{it.get('qty', 1)}</span>"
            f"<span>Rs {it.get('price', 0) * it.get('qty', 1):.0f}</span></div>"
        )
    valid_until = q.get("valid_until") or "—"
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Quotation {q['quote_no']}</title>
<style>
body{{font-family:monospace;font-size:12px;max-width:320px;margin:0 auto;padding:20px}}
h2{{text-align:center;margin:0}}
hr{{border:none;border-top:1px dashed #999;margin:8px 0}}
.row{{display:flex;justify-content:space-between}}
.total{{font-weight:bold;font-size:14px}}
.center{{text-align:center}}
</style></head><body>
<h2>BillBook</h2>
<p class="center">Quotation</p>
<hr>
<div class="row"><span>Quote #:</span><span>{q['quote_no']}</span></div>
<div class="row"><span>Date:</span><span>{q['created_at']}</span></div>
<div class="row"><span>Valid Until:</span><span>{valid_until}</span></div>
"""
    if q.get("customer_name"):
        html += f"<div class='row'><span>Customer:</span><span>{esc(q['customer_name'])}</span></div>"
    if q.get("customer_phone"):
        html += f"<div class='row'><span>Phone:</span><span>{esc(q['customer_phone'])}</span></div>"
    html += "<hr>" + items_html + f"<hr>"
    subtotal = sum(it.get("price", 0) * it.get("qty", 1) for it in q["items"])
    html += f"<div class='row'><span>Subtotal:</span><span>Rs {subtotal:.0f}</span></div>"
    if q.get("discount", 0) > 0:
        html += f"<div class='row'><span>Discount:</span><span>-Rs {q['discount']:.0f}</span></div>"
    html += f"<div class='row total'><span>TOTAL:</span><span>Rs {q.get('total', 0):.0f}</span></div>"
    if q.get("notes"):
        html += f"<hr><p>Notes: {esc(q['notes'])}</p>"
    html += "<hr><p class='center'>Thank you for your inquiry!</p></body></html>"
    return HTMLResponse(html)


# ------------------------------------------------------------------
# Customer Payments (settle outstanding credit / urdhaar)
# ------------------------------------------------------------------



@router.post("/api/cash-drawer/in")
def cash_in_route(payload: CashActionIn) -> Any:
    cid = shop_mod.cash_in(payload.amount, payload.description)
    db.log_activity("cash_in", "cash_drawer", cid,
                    f"Cash in Rs {payload.amount:.0f} — {payload.description}")
    return {"id": cid}




@router.post("/api/cash-drawer/out")
def cash_out_route(payload: CashActionIn) -> Any:
    cid = shop_mod.cash_out(payload.amount, payload.description)
    db.log_activity("cash_out", "cash_drawer", cid,
                    f"Cash out Rs {payload.amount:.0f} — {payload.description}")
    return {"id": cid}


# ------------------------------------------------------------------
# Sale edit (notes / payment method only — items immutable)
# ------------------------------------------------------------------



@router.put("/api/sales/{sale_id}")
def edit_sale(sale_id: int, payload: SaleEditIn) -> Any:
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        updates = []
        params = []
        if payload.notes is not None:
            updates.append("notes=?")
            params.append(payload.notes)
        if payload.payment_method:
            updates.append("payment_method=?")
            params.append(payload.payment_method)
        if payload.payment_status:
            updates.append("payment_status=?")
            params.append(payload.payment_status)
        if not updates:
            return {"ok": True, "changed": 0}
        params.append(sale_id)
        c.execute(f"UPDATE sales SET {', '.join(updates)} WHERE id=?", params)
    db.log_activity("sale_edited", "sale", sale_id,
                    f"Edited sale {sale['invoice_no']}",
                    {"updates": updates})
    return {"ok": True, "changed": len(updates)}


# ==================================================================
# Tax / GST configuration
# ==================================================================



@router.post("/api/sales/{sale_id}/sms")
def send_sale_sms_route(sale_id: int) -> Any:
    res = pos_extra.send_sale_sms(sale_id)
    if not res["success"]:
        raise HTTPException(400, res.get("error", "SMS failed"))
    return res


# ==================================================================
# CSV / Excel import
# ==================================================================



@router.get("/api/barcodes")
def list_barcodes() -> Any:
    return {"barcodes": pos_extra.list_category_barcodes()}




@router.get("/api/barcodes/{category_id}")
def get_barcode(category_id: int) -> Any:
    res = pos_extra.get_category_barcode_data(category_id)
    if "error" in res:
        raise HTTPException(404, res["error"])
    return res




@router.post("/api/barcodes/scan")
def scan_barcode(payload: BarcodeScanIn) -> Any:
    res = pos_extra.parse_barcode_scan(payload.payload)
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ==================================================================
# App launcher — which features are available
# ==================================================================



@router.post("/api/sales-targets")
def set_sales_target(payload: TargetIn) -> Any:
    return pos_import.set_target(payload.period, payload.target_date, payload.target_amount, payload.notes)




@router.get("/api/sales-targets/progress")
def target_progress(period: str = "daily", target_date: str = "") -> Any:
    if not target_date:
        if period == "monthly":
            target_date = datetime.now().strftime("%Y-%m")
        else:
            target_date = datetime.now().strftime("%Y-%m-%d")
    return pos_import.get_target_progress(period, target_date)


# ==================================================================
# Top-selling items report
# ==================================================================



@router.post("/api/sales/{sale_id}/return")
def process_return(sale_id: int, payload: ReturnIn, request: Request) -> Any:
    """Process a return/exchange for a sale.

    C2 fix (v8.13.4): The old parallel refund path was dangerous — it used
    `db.conn()` (NOT `write_tx`), did no PIN check, never restored stock
    state, and never reversed loyalty/commission. A cashier could refund
    anything by calling this endpoint directly.

    The canonical refund path is `/api/sales/{id}/refund` (`refund_sale`)
    which does PIN-gated, atomic, full side-effect reversal via
    `_reverse_sale_core()`. This `/return` route is now a thin wrapper
    that delegates to `refund_sale` for pure refunds, and rejects
    exchange-style payloads (which should use the proper exchange flow).

    For an exchange (items returned AND new items taken), the cashier must
    issue a refund on the original sale and create a new sale for the
    exchanged items separately — there is no atomic "exchange" primitive
    that should be silently stitched together by an unprivileged cashier.
    """
    # Reject exchange payloads — they require a separate atomic exchange flow
    # that does not exist. Sending them through here would silently allow a
    # cashier to "exchange" any item for any other item with no inventory
    # state update.
    if getattr(payload, "exchange_items", None):
        raise HTTPException(
            400,
            "Exchange-style returns are not supported via this endpoint. "
            "Issue a refund on the original sale and create a new sale for "
            "the exchanged items separately."
        )

    # v3.1.1: Idempotency — check existing first, then rely on UNIQUE index
    if payload.client_uuid:
        with db.conn() as c:
            existing = c.execute(
                "SELECT * FROM activity_log WHERE description LIKE ?",
                (f"%uuid:{payload.client_uuid}%",)
            ).fetchone()
            if existing:
                return {"ok": True, "idempotent": True, "message": "Return already processed"}

    # Delegate to the canonical refund path. This ensures:
    #  - manager PIN is verified (when require_pin_for_refund is on)
    #  - the entire reversal is atomic (write_tx)
    #  - cash_drawer, stock_state, loyalty, commission are all reversed
    refund_payload = {
        "manager_pin": getattr(payload, "manager_pin", "") or "",
        "reason": (payload.reason or "return/exchange")[:200],
        "client_uuid": getattr(payload, "client_uuid", "") or "",
    }
    result = refund_sale(sale_id, refund_payload)
    # Coerce to legacy return shape for backward compat with old clients
    return {
        "ok": True,
        "refund_amount": result.get("refund_amount", 0) if isinstance(result, dict) else 0,
        "new_sale_id": None,  # exchanges are now rejected above
        "new_status": "refunded",
        "delegated_to": "/api/sales/{id}/refund",
    }


# ==================================================================
# Email receipt
# ==================================================================



@router.post("/api/sales/{sale_id}/email")
def email_receipt(sale_id: int, payload: EmailReceiptIn) -> Any:
    """Send a receipt via email. Uses a simple SMTP send (configurable in settings)."""
    # For now we just return the receipt URL — actual SMTP integration needs email config
    # In production, this would queue an email send via SMTP/SendGrid/etc.
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
    receipt_url = f"/api/sales/{sale_id}/receipt"
    return {
        "ok": True,
        "message": f"Receipt link emailed to {payload.to_email}",
        "receipt_url": receipt_url,
        "note": "Configure SMTP in settings to enable actual email sending. For now, share this link directly.",
    }


# ==================================================================
# Phase 2: Inventory Adjustments + Stock Guard API
# ==================================================================



# ═══════════════════════════════════════════════════
# Raast QR (Phase 2 — Pakistan Payments)
# ═══════════════════════════════════════════════════

@router.get("/api/raast/qr")
def get_raast_qr(amount: float = 0, reference: str = "") -> Any:
    """Get Raast QR code URL for the shop.
    Returns a QR code URL encoding the shop's Raast ID + amount + reference.
    The frontend displays this QR in a 'Scan to Pay' modal.
    """
    from ..db import get_setting
    from urllib.parse import quote
    raast_id = get_setting("raast_id", "")
    if not raast_id:
        return {"error": "Raast ID not configured. Set it in Settings → Shop Profile.", "qr_url": None}
    # Build QR payload (simplified Raast format)
    payload = f"raast://{raast_id}?amount={amount}"
    if reference:
        payload += f"&ref={quote(reference)}"
    # Generate QR code URL using public API
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={quote(payload)}"
    return {
        "qr_url": qr_url,
        "raast_id": raast_id,
        "amount": amount,
        "reference": reference,
    }


# ═══════════════════════════════════════════════════
# Phase 5: POS Speed, Delight & Decomposition
# ═══════════════════════════════════════════════════

@router.get("/api/pos/upsell")
def get_upsell_suggestions(category_id: int = None) -> Any:
    """Get top-3 co-occurrence suggestions from sale_items."""
    with db.conn() as c:
        if category_id:
            # v8.9.1: exclude refunded sales from upsell co-occurrence
            rows = c.execute(
                "SELECT si2.category_id, si2.category_code, pc.name, pc.color, pc.sell_price, COUNT(*) AS co_count "
                "FROM sale_items si1 "
                "JOIN sale_items si2 ON si1.sale_id = si2.sale_id "
                "JOIN sales s ON si1.sale_id = s.id "
                "LEFT JOIN price_categories pc ON si2.category_id = pc.id "
                "WHERE si1.category_id = ? AND si2.category_id != ? AND pc.active = 1 "
                "AND " + db.VALID_SALE_FILTER + " "
                "GROUP BY si2.category_id ORDER BY co_count DESC LIMIT 3",
                (category_id, category_id),
            ).fetchall()
        else:
            # v8.9.1: exclude refunded sales from top-selling
            rows = c.execute(
                "SELECT si.category_id, si.category_code, pc.name, pc.color, pc.sell_price, SUM(si.qty) AS qty "
                "FROM sale_items si "
                "JOIN sales s ON si.sale_id = s.id "
                "LEFT JOIN price_categories pc ON si.category_id = pc.id "
                "WHERE pc.active = 1 AND " + db.VALID_SALE_FILTER + " "
                "GROUP BY si.category_id ORDER BY qty DESC LIMIT 3"
            ).fetchall()
    return {"suggestions": [dict(r) for r in rows]}


@router.post("/api/pos/upsell/log")
def log_upsell(payload: dict) -> Any:
    """Log upsell acceptance/dismissal."""
    with db.conn() as c:
        c.execute(
            "INSERT INTO upsell_logs(category_id, suggested_category_id, accepted, sale_id) VALUES(?,?,?,?)",
            (payload.get("category_id"), payload.get("suggested_category_id"),
             1 if payload.get("accepted") else 0, payload.get("sale_id")),
        )
    return {"ok": True}


@router.get("/api/pos/shift-leaderboard")
def shift_leaderboard() -> Any:
    """Revenue + tickets per employee today."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT e.id, e.name, e.role, "
            "COUNT(s.id) AS ticket_count, COALESCE(SUM(s.total), 0) AS revenue "
            "FROM employees e LEFT JOIN sales s ON s.employee_id = e.id "
            "AND date(s.created_at) = date('now','localtime') AND " + db.VALID_SALE_FILTER + " "
            "WHERE e.active = 1 GROUP BY e.id ORDER BY revenue DESC"
        ).fetchall()
    return {"leaderboard": [dict(r) for r in rows]}


@router.post("/api/customers/{cid}/wallet")
def adjust_wallet(cid: int, payload: dict) -> Any:
    """Add or deduct from customer store credit wallet."""
    amount = float(payload.get("amount", 0))
    reason = payload.get("reason", "")
    with db.conn() as c:
        cur = c.execute("UPDATE customers SET wallet_balance = wallet_balance + ? WHERE id = ?", (amount, cid))
        if cur.rowcount == 0:
            raise HTTPException(404, "customer not found")
        wallet = c.execute("SELECT wallet_balance FROM customers WHERE id=?", (cid,)).fetchone()
    db.log_activity("wallet_adjusted", "customer", cid, f"Wallet {'+' if amount>=0 else ''}{amount}: {reason}", {})
    return {"ok": True, "new_balance": wallet["wallet_balance"]}


@router.put("/api/sales/{sale_id}/layaway")
def update_layaway(sale_id: int, payload: dict) -> Any:
    """Update layaway payment — reduces balance, marks as layaway."""
    payment = float(payload.get("payment", 0))
    due_date = payload.get("due_date", "")
    with db.conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            raise HTTPException(404, "sale not found")
        new_balance = (sale["layaway_balance"] or sale["total"]) - payment
        status = "paid" if new_balance <= 0.01 else "layaway"
        c.execute(
            "UPDATE sales SET payment_status=?, layaway_balance=?, layaway_due_date=? WHERE id=?",
            (status, max(0, new_balance), due_date or sale.get("layaway_due_date"), sale_id),
        )
    return {"ok": True, "payment_status": status, "remaining_balance": max(0, new_balance)}
