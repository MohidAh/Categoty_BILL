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





class CategoryIn(BaseModel):
    name: str
    code: str = ""
    sell_price: float
    color: str = "#10b981"
    sort_order: int = 0






class POStatusIn(BaseModel):
    status: str
    sent_via: str = ""






class ReorderIn(BaseModel):
    order: list[int]






class StockAdjustmentIn(BaseModel):
    category_id: int
    delta: int  # positive = add stock, negative = remove
    reason: str
    client_uuid: Optional[str] = None  # v3.1.1: idempotency key








class POItemIn(BaseModel):
    item_name: str = ""
    qty: int = 1
    est_price: float = 0
    notes: str = ""








class POIn(BaseModel):
    supplier_id: Optional[int] = None
    supplier_name: str = ""
    items: list[POItemIn]
    notes: str = ""
    expected_date: str = ""






class ItemIn(BaseModel):
    raw: str = ""
    item_code: str = ""
    price: float = 0
    qty: float = 0
    unit: str = "pcs"
    category_id: int | None = None
    page_no: int | None = None





@router.get("/api/categories")
def list_categories() -> Any:
    with db.conn() as c:
        rows = c.execute(
            "SELECT pc.*, (SELECT COUNT(*) FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "AND b.deleted_at IS NULL "
            "WHERE bi.category_id = pc.id AND b.status = 'confirmed' AND b.deleted_at IS NULL) AS item_count "
            "FROM price_categories pc WHERE pc.active=1 ORDER BY pc.sort_order, pc.id"
        ).fetchall()
    return [dict(r) for r in rows]




@router.post("/api/categories")
def create_category(payload: CategoryIn) -> Any:
    with db.conn() as c:
        cid = c.execute(
            "INSERT INTO price_categories(name, code, sell_price, color, sort_order) VALUES(?,?,?,?,?)",
            (payload.name, payload.code, payload.sell_price, payload.color, payload.sort_order),
        ).lastrowid
    return {"id": cid}




@router.put("/api/categories/{cid}")
def update_category(cid: int, payload: CategoryIn) -> Any:
    with db.conn() as c:
        c.execute(
            "UPDATE price_categories SET name=?, code=?, sell_price=?, color=?, sort_order=? WHERE id=?",
            (payload.name, payload.code, payload.sell_price, payload.color, payload.sort_order, cid),
        )
    return {"ok": True}




@router.delete("/api/categories/{cid}")
def delete_category(cid: int) -> Any:
    with db.conn() as c:
        count = c.execute(
            "SELECT COUNT(*) n FROM bill_items WHERE category_id=?", (cid,)
        ).fetchone()["n"]
        if count > 0:
            c.execute("UPDATE price_categories SET active=0 WHERE id=?", (cid,))
        else:
            c.execute("DELETE FROM price_categories WHERE id=?", (cid,))
    return {"ok": True}




@router.post("/api/categories/reorder")
def reorder_categories(payload: ReorderIn) -> Any:
    with db.conn() as c:
        for i, cid in enumerate(payload.order):
            c.execute(
                "UPDATE price_categories SET sort_order=? WHERE id=?", (i, cid)
            )
    return {"ok": True}


# ------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------



@router.get("/api/reorder-reminders")
def get_reorders() -> Any:
    """Get active reorder reminders.

    v8.18.10 FIX ("/reorder page broken"): this route used to return the
    trends-generated list WITHOUT ids, while the dismiss/ordered/auto-PO
    endpoints (and the /reorder page's action buttons) operate on the
    reorder_reminders TABLE by id — a table nothing ever inserted into.
    The page therefore showed 'Suggested: 0 / Rs 0' and its buttons POSTed
    to /api/reorder-reminders/undefined/... (error toast).

    Now: generated reminders are upserted into the table (keyed by
    item_name + supplier_name, existing status preserved so dismissed /
    ordered items stay gone), stale 'new' rows are dropped, and the
    response returns table rows with status='new'.
    """
    from .. import trends as trends_mod
    generated = trends_mod.generate_reorder_reminders()
    gen_keys = {(g["item_name"], g.get("supplier_name")) for g in generated}
    with db.conn() as c:
        for g in generated:
            row = c.execute(
                "SELECT id FROM reorder_reminders "
                "WHERE item_name=? AND IFNULL(supplier_name,'')=IFNULL(?,'')",
                (g["item_name"], g.get("supplier_name")),
            ).fetchone()
            if row:
                # refresh stats; status is intentionally NOT touched
                c.execute(
                    "UPDATE reorder_reminders SET supplier_name=?, avg_gap_days=?, "
                    "last_purchased=?, days_since=?, suggested_quantity=?, avg_price=?, "
                    "total_purchases=?, priority=?, seasonal_note=? WHERE id=?",
                    (g.get("supplier_name"), g.get("avg_gap_days"), g.get("last_purchased"),
                     g.get("days_since"), g.get("suggested_quantity"), g.get("avg_price"),
                     g.get("total_purchases"), g.get("priority"), g.get("seasonal_note"),
                     row["id"]),
                )
            else:
                c.execute(
                    "INSERT INTO reorder_reminders(item_name, supplier_name, avg_gap_days, "
                    "last_purchased, days_since, suggested_quantity, avg_price, "
                    "total_purchases, seasonal_note, priority, status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'new')",
                    (g["item_name"], g.get("supplier_name"), g.get("avg_gap_days"),
                     g.get("last_purchased"), g.get("days_since"), g.get("suggested_quantity"),
                     g.get("avg_price"), g.get("total_purchases"), g.get("seasonal_note"),
                     g.get("priority")),
                )
        # Drop stale 'new' rows the generator no longer produces (item was
        # bought again, pattern dropped below 3 purchases, or fell under the
        # 1.2x-gap threshold). Dismissed/ordered rows are kept as history.
        keep_ids = [r["id"] for r in c.execute(
            "SELECT id FROM reorder_reminders WHERE status='new'"
        ).fetchall()]
        for rid in keep_ids:
            row = c.execute(
                "SELECT item_name, supplier_name FROM reorder_reminders WHERE id=?",
                (rid,),
            ).fetchone()
            if row and (row["item_name"], row["supplier_name"]) not in gen_keys:
                c.execute("DELETE FROM reorder_reminders WHERE id=?", (rid,))
        rows = c.execute(
            "SELECT * FROM reorder_reminders WHERE status='new' "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "days_since DESC"
        ).fetchall()
    return {"reminders": [dict(r) for r in rows]}




@router.post("/api/reorder-reminders/{reminder_id}/dismiss")
def dismiss_reorder(reminder_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE reorder_reminders SET status='dismissed' WHERE id=?", (reminder_id,))
    return {"ok": True}




@router.post("/api/reorder-reminders/{reminder_id}/ordered")
def ordered_reorder(reminder_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE reorder_reminders SET status='ordered' WHERE id=?", (reminder_id,))
    return {"ok": True}


# ------------------------------------------------------------------
# POS (Point of Sale) — Category-based selling
# ------------------------------------------------------------------



@router.get("/api/inventory")
def get_inventory() -> Any:
    """Current stock per category: purchased (bills) - sold (sales)."""
    return {"items": shop_mod.get_inventory()}


# ------------------------------------------------------------------
# Customers
# ------------------------------------------------------------------



@router.get("/api/purchase-orders")
def list_purchase_orders(status: str = "") -> Any:
    return {"purchase_orders": pos_extra.list_pos(status)}




@router.post("/api/purchase-orders")
def create_purchase_order(payload: POIn) -> Any:
    items = [i.dict() for i in payload.items]
    res = pos_extra.create_po(
        payload.supplier_id, payload.supplier_name, items,
        payload.notes, payload.expected_date,
    )
    db.log_activity("po_created", "purchase_order", res["id"],
                    f"PO {res['po_no']} created — Rs {res['total']:.0f}")
    return res




@router.get("/api/purchase-orders/{po_id}")
def get_purchase_order(po_id: int) -> Any:
    po = pos_extra.get_po(po_id)
    if not po:
        raise HTTPException(404, "PO not found")
    return po




@router.delete("/api/purchase-orders/{po_id}")
def delete_purchase_order(po_id: int) -> Any:
    ok = pos_extra.delete_po(po_id)
    if not ok:
        raise HTTPException(404, "PO not found")
    return {"ok": True}




@router.put("/api/purchase-orders/{po_id}/status")
def update_purchase_order_status(po_id: int, payload: POStatusIn) -> Any:
    pos_extra.update_po_status(po_id, payload.status, payload.sent_via)
    db.log_activity("po_status_changed", "purchase_order", po_id,
                    f"PO status → {payload.status}" + (f" via {payload.sent_via}" if payload.sent_via else ""))
    return {"ok": True}




@router.get("/api/purchase-orders/{po_id}/whatsapp")
def purchase_order_whatsapp(po_id: int) -> Any:
    return pos_extra.po_to_whatsapp(po_id)


# ==================================================================
# SMS notifications (Twilio)
# ==================================================================



@router.get("/api/inventory/adjust")
def list_adjustments() -> Any:
    """List all stock adjustments."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT sa.*, pc.code, pc.name AS cat_name "
            "FROM stock_adjustments sa "
            "LEFT JOIN price_categories pc ON sa.category_id = pc.id "
            "ORDER BY sa.id DESC LIMIT 100"
        ).fetchall()
    return {"adjustments": [dict(r) for r in rows]}




@router.post("/api/inventory/adjust")
def create_adjustment(payload: StockAdjustmentIn) -> Any:
    # v3.1: Idempotency check
    if payload.client_uuid:
        with db.conn() as c:
            existing = c.execute("SELECT * FROM stock_adjustments WHERE reason LIKE ?", (f"%uuid:{payload.client_uuid}%",)).fetchone()
            if existing:
                return {"id": existing["id"], "idempotent": True}
    """Create a stock adjustment (manual +/- with reason)."""
    if payload.delta == 0:
        raise HTTPException(400, "Delta must be non-zero")
    if not payload.reason or len(payload.reason.strip()) < 3:
        raise HTTPException(400, "Reason is required (min 3 chars)")
    with db.conn() as c:
        reason = payload.reason.strip()
        if payload.client_uuid:
            reason = reason + f" [uuid:{payload.client_uuid}]"
        aid = c.execute(
            "INSERT INTO stock_adjustments(category_id, delta, reason) VALUES(?,?,?)",
            (payload.category_id, payload.delta, reason),
        ).lastrowid
    # v5.0 Phase 1: apply the adjustment to the running weighted-avg state.
    # Negative delta = shrinkage (avg unchanged, qty & value drop).
    # Positive delta = found stock (added at current avg).
    try:
        from .. import profit as profit_mod
        profit_mod.apply_adjustment_to_state(payload.category_id, payload.delta)
    except Exception as e:
        from .. import profit as profit_mod
        profit_mod.log_state_drift("apply_adjustment_to_state", payload.category_id, str(e),
                                   {"adjustment_id": aid, "delta": payload.delta})
    db.log_activity("stock_adjustment", "inventory", aid,
                    f"Stock adjustment: {payload.delta:+d} for category {payload.category_id} — {payload.reason}",
                    {"category_id": payload.category_id, "delta": payload.delta})
    return {"id": aid}


# ==================================================================
# v8.13.0: Stock Write-offs (damage / expiry / theft / sample / display)
# ==================================================================

class StockWriteoffIn(BaseModel):
    category_id: int
    qty: float
    reason: str  # damage | expiry | theft | sample | display | other
    notes: str = ""
    manager_pin: str = ""


@router.post("/api/inventory/writeoff")
def create_stock_writeoff(payload: StockWriteoffIn) -> Any:
    """Record a stock write-off (admin only — requires manager PIN).

    Reduces category_stock_state AND creates:
    - a stock_writeoffs row (snapshot of unit_cost + loss_value)
    - a stock_adjustments row (delta = -qty, reason = 'writeoff: <reason>')
    - an activity_log entry

    Use this instead of `/api/inventory/adjust` with a negative delta when
    stock is damaged, expired, stolen, given as sample, or used for display —
    the loss value is tracked separately so the monthly P&L can show a
    'Shrinkage' line item.
    """
    # PIN gate — admin only
    if not shop_mod.verify_manager_pin_bool(payload.manager_pin):
        raise HTTPException(403, {
            "code": "manager_pin_required",
            "detail": "Manager PIN required to record stock write-offs. "
                      "Write-offs are an equity-affecting operation and require admin authorization."
        })
    from ..profit_cash import add_stock_writeoff, VALID_WRITEOFF_REASONS
    try:
        woff_id = add_stock_writeoff(
            category_id=payload.category_id,
            qty=payload.qty,
            reason=payload.reason,
            notes=payload.notes,
            manager_pin_verified=True,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": woff_id, "message": f"Write-off recorded — {payload.qty} units of category #{payload.category_id}"}


# ==================================================================
# Phase 2: Active Sessions API
# ==================================================================



# ═══════════════════════════════════════════════════
# Phase 3: Auto-PO from reorder reminders
# ═══════════════════════════════════════════════════

class AutoPOIn(BaseModel):
    reminder_ids: list[int]
    supplier_id: int | None = None
    supplier_name: str = ""
    notes: str = ""


@router.post("/api/reorder-reminders/auto-po")
def create_auto_po(payload: AutoPOIn) -> Any:
    """Draft a Purchase Order from selected reorder reminders.
    Groups reminders by category and creates a single PO.
    """
    if not payload.reminder_ids:
        raise HTTPException(400, "No reminders selected")
    with db.conn() as c:
        # Fetch selected reminders
        # v8.18.10: status filter fixed — rows are written with status='new'
        # (the old 'active' filter matched nothing, ever).
        placeholders = ",".join("?" * len(payload.reminder_ids))
        reminders = c.execute(
            f"SELECT * FROM reorder_reminders WHERE id IN ({placeholders}) AND status IN ('new','active')",
            payload.reminder_ids,
        ).fetchall()
        if not reminders:
            raise HTTPException(404, "No active reminders found for given IDs")
        # Build PO items from reminders
        # v8.18.10: field names fixed to the actual table columns (the old
        # code read category_id/category_name/suggested_qty which never
        # existed on this table and would have raised on every call).
        po_items = []
        for r in reminders:
            d = dict(r)
            est_price = d.get("avg_price") or 0
            po_items.append({
                "item_name": d.get("item_name") or f"Reminder #{d['id']}",
                "qty": d.get("suggested_quantity") or 10,
                "est_price": est_price,
                "notes": f"Auto-PO from reminder #{d['id']}",
            })
        # Get supplier name
        supplier_name = payload.supplier_name
        if not supplier_name and payload.supplier_id:
            sup = c.execute("SELECT name FROM suppliers WHERE id=?", (payload.supplier_id,)).fetchone()
            supplier_name = sup["name"] if sup else "Auto-PO Supplier"
        elif not supplier_name:
            supplier_name = "Auto-PO Supplier"
        # Create the PO
        total = sum(item["qty"] * item["est_price"] for item in po_items)
        po_no = f"PO-{datetime.now().strftime('%Y%m%d')}-{int(time.time() * 1000) % 100000:05d}"
        po_id = c.execute(
            "INSERT INTO purchase_orders(po_no, supplier_id, supplier_name, status, total, notes) "
            "VALUES(?,?,?,?,?,?)",
            (po_no, payload.supplier_id, supplier_name, "draft", total,
             payload.notes or f"Auto-generated from {len(reminders)} reorder reminders"),
        ).lastrowid
        for item in po_items:
            c.execute(
                "INSERT INTO purchase_order_items(po_id, item_name, qty, est_price, line_total, notes) "
                "VALUES(?,?,?,?,?,?)",
                (po_id, item["item_name"], item["qty"], item["est_price"],
                 item["qty"] * item["est_price"], item["notes"]),
            )
        # Mark reminders as ordered
        for rid in payload.reminder_ids:
            c.execute("UPDATE reorder_reminders SET status='ordered' WHERE id=?", (rid,))
        db.log_activity("auto_po_created", "purchase_order", po_id,
                        f"Auto-PO {po_no} created from {len(reminders)} reminders — Rs {total:,.0f}", {})
    return {"id": po_id, "po_no": po_no, "total": total, "items": len(po_items)}


@router.post("/api/reorder-reminders/master-po")
def create_master_po(payload: AutoPOIn) -> Any:
    """Consolidate multiple reorder reminders into one master PO per supplier.
    Groups reminders by their category's default supplier (if any).
    """
    if not payload.reminder_ids:
        raise HTTPException(400, "No reminders selected")
    with db.conn() as c:
        # v8.18.10: status filter + field names fixed (see create_auto_po).
        placeholders = ",".join("?" * len(payload.reminder_ids))
        reminders = c.execute(
            f"SELECT * FROM reorder_reminders WHERE id IN ({placeholders}) AND status IN ('new','active')",
            payload.reminder_ids,
        ).fetchall()
        if not reminders:
            raise HTTPException(404, "No active reminders found")
        # Group by supplier: use the supplier the item was actually bought
        # from (carried on the reminder row), falling back to payload.
        by_supplier = {}
        for r in reminders:
            d = dict(r)
            row_supplier = (d.get("supplier_name") or "").strip()
            if payload.supplier_name:
                sname = payload.supplier_name
                sid = payload.supplier_id
            elif row_supplier:
                # resolve supplier id by name when possible
                sup = c.execute("SELECT id FROM suppliers WHERE name=? ORDER BY id LIMIT 1",
                                (row_supplier,)).fetchone()
                sname = row_supplier
                sid = sup["id"] if sup else None
            else:
                sname = "General Supplier"
                sid = payload.supplier_id
            key = sid or (sname or 0)
            if key not in by_supplier:
                by_supplier[key] = {"supplier_id": sid, "supplier_name": sname, "items": []}
            est_price = d.get("avg_price") or 0
            by_supplier[key]["items"].append({
                "item_name": d.get("item_name") or f"Reminder #{d['id']}",
                "qty": d.get("suggested_quantity") or 10,
                "est_price": est_price,
            })
        # Create one PO per supplier
        created_pos = []
        for sid, data in by_supplier.items():
            total = sum(item["qty"] * item["est_price"] for item in data["items"])
            po_no = f"PO-{datetime.now().strftime('%Y%m%d')}-{int(time.time() * 1000) % 100000:05d}"
            po_id = c.execute(
                "INSERT INTO purchase_orders(po_no, supplier_id, supplier_name, status, total, notes) "
                "VALUES(?,?,?,?,?,?)",
                (po_no, data["supplier_id"], data["supplier_name"], "draft", total,
                 f"Master PO from {len(data['items'])} consolidated reminders"),
            ).lastrowid
            for item in data["items"]:
                c.execute(
                    "INSERT INTO purchase_order_items(po_id, item_name, qty, est_price, line_total, notes) "
                    "VALUES(?,?,?,?,?,?)",
                    (po_id, item["item_name"], item["qty"], item["est_price"],
                     item["qty"] * item["est_price"], ""),
                )
            created_pos.append({"id": po_id, "po_no": po_no, "supplier": data["supplier_name"], "total": total, "items": len(data["items"])})
        # Mark all reminders as ordered
        for rid in payload.reminder_ids:
            c.execute("UPDATE reorder_reminders SET status='ordered' WHERE id=?", (rid,))
        db.log_activity("master_po_created", "purchase_order", 0,
                        f"Created {len(created_pos)} master POs from {len(reminders)} reminders", {})
    return {"created_pos": created_pos, "total_pos": len(created_pos)}


# ═══════════════════════════════════════════════════
# Phase 4: Inventory Intelligence
# ═══════════════════════════════════════════════════

class StockCountIn(BaseModel):
    count_date: str
    notes: str = ""
    items: list[dict] = []


@router.post("/api/inventory/stock-count")
def create_stock_count(payload: StockCountIn) -> Any:
    """Create a stock count session. When finalized, creates shrinkage adjustments."""
    with db.conn() as c:
        sc_id = c.execute(
            "INSERT INTO stock_counts(count_date, status, notes) VALUES(?,?,?)",
            (payload.count_date, "draft", payload.notes),
        ).lastrowid
        # Get current book qty per category
        for item in payload.items:
            cat_id = item.get("category_id")
            counted = item.get("counted_qty", 0)
            inv = shop_mod.get_inventory()
            book = next((i["stock"] for i in inv if i["category_id"] == cat_id), 0)
            variance = counted - book
            c.execute(
                "INSERT INTO stock_count_items(stock_count_id, category_id, book_qty, counted_qty, variance, reason) "
                "VALUES(?,?,?,?,?,?)",
                (sc_id, cat_id, book, counted, variance, item.get("reason", "")),
            )
            # If variance != 0, create a stock adjustment
            if variance != 0:
                c.execute(
                    "INSERT INTO stock_adjustments(category_id, delta, reason) VALUES(?,?,?)",
                    (cat_id, variance, f"Stock count #{sc_id} adjustment: {item.get('reason', 'counted variance')}"),
                )
        c.execute("UPDATE stock_counts SET status='completed' WHERE id=?", (sc_id,))
        db.log_activity("stock_count", "inventory", sc_id,
                        f"Stock count completed on {payload.count_date}", {})
    return {"id": sc_id, "status": "completed"}


@router.get("/api/inventory/stock-counts")
def list_stock_counts() -> Any:
    """List all stock count sessions."""
    with db.conn() as c:
        rows = c.execute("SELECT * FROM stock_counts ORDER BY created_at DESC, id DESC LIMIT 50").fetchall()
    return {"counts": [dict(r) for r in rows]}


@router.get("/api/inventory/kpis")
def inventory_kpis() -> Any:
    """Inventory KPIs: Turnover, GMROI, Sell-Through, Days-on-Hand."""
    with db.conn() as c:
        # COGS (last 30 days)
        cogs = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si "
            "JOIN sales s ON si.sale_id = s.id "
            "WHERE s.payment_status IN ('paid', 'credit', 'partial') AND date(s.created_at) >= date('now','-30 days')"
        ).fetchone()["v"]
        # Avg inventory value (at cost)
        inv = shop_mod.get_inventory()
        total_inv_value = sum(i.get("stock_value", 0) for i in inv)
        avg_inv_value = total_inv_value  # simplified — would need daily snapshots for true average
        # Gross margin (last 30 days)
        revenue = c.execute(
            "SELECT COALESCE(SUM(s.total), 0) AS v FROM sales s "
            "WHERE s.payment_status IN ('paid', 'credit', 'partial') AND date(s.created_at) >= date('now','-30 days')"
        ).fetchone()["v"]
        gross_margin = revenue - cogs
        # Total received (purchased) in last 30 days
        received = c.execute(
            "SELECT COALESCE(SUM(bi.line_total), 0) AS v FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND date(b.bill_date) >= date('now','-30 days')"
        ).fetchone()["v"]
        # Total sold (qty) in last 30 days
        sold_qty = c.execute(
            "SELECT COALESCE(SUM(si.qty), 0) AS v FROM sale_items si "
            "JOIN sales s ON si.sale_id = s.id "
            "WHERE s.payment_status IN ('paid', 'credit', 'partial') AND date(s.created_at) >= date('now','-30 days')"
        ).fetchone()["v"]
        # Total received (qty)
        received_qty = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS v "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND date(b.bill_date) >= date('now','-30 days')"
        ).fetchone()["v"]
    turnover = (cogs / avg_inv_value) if avg_inv_value > 0 else 0
    gmroi = (gross_margin / avg_inv_value) if avg_inv_value > 0 else 0
    sell_through = (sold_qty / received_qty * 100) if received_qty > 0 else 0
    days_on_hand = (avg_inv_value / cogs * 30) if cogs > 0 else 0
    return {
        "inventory_turnover": round(turnover, 2),
        "gmroi": round(gmroi, 2),
        "sell_through_pct": round(sell_through, 1),
        "days_on_hand": round(days_on_hand, 1),
        "cogs_30d": round(cogs, 2),
        "avg_inventory_value": round(avg_inv_value, 2),
        "gross_margin_30d": round(gross_margin, 2),
        "received_qty_30d": received_qty,
        "sold_qty_30d": sold_qty,
    }


@router.get("/api/inventory/expiring")
def expiring_items(days: int = 30) -> Any:
    """Items expiring within N days."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT bi.*, b.supplier_name, b.bill_date, pc.name AS cat_name, pc.code "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.deleted_at IS NULL AND bi.expiry_date IS NOT NULL "
            "AND date(bi.expiry_date) <= date('now', '+' || ? || ' days') "
            "AND b.status='confirmed' AND b.deleted_at IS NULL "
            "ORDER BY bi.expiry_date",
            (str(days),),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "days_ahead": days}


@router.get("/api/inventory/shrinkage")
def shrinkage_report(start: str = "", end: str = "") -> Any:
    """Shrinkage report from stock adjustments."""
    if not start:
        start = datetime.now().strftime("%Y-%m-01")
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")
    with db.conn() as c:
        rows = c.execute(
            "SELECT sa.*, pc.name AS cat_name, pc.code "
            "FROM stock_adjustments sa "
            "LEFT JOIN price_categories pc ON sa.category_id = pc.id "
            "WHERE date(sa.created_at) >= ? AND date(sa.created_at) <= ? AND sa.delta < 0 "
            "ORDER BY sa.created_at DESC, sa.id DESC",
            (start, end),
        ).fetchall()
        total_loss = sum(abs(r["delta"]) * (c.execute(
            "SELECT AVG(price) AS avg_cost FROM bill_items WHERE category_id=?", (r["category_id"],)
        ).fetchone()["avg_cost"] or 0) for r in rows)
    return {"adjustments": [dict(r) for r in rows], "total_loss_estimate": round(total_loss, 2)}


# ═══════════════════════════════════════════════════════════════════════
# v5.0 Phase 1 — Running Weighted Average Cost: rebuild endpoint
# ═══════════════════════════════════════════════════════════════════════

@router.post("/api/inventory/rebuild-stock-state")
def rebuild_stock_state_route() -> Any:
    """Rebuild category_stock_state from scratch by replaying all confirmed
    bills (purchases) and non-refunded sales chronologically.

    Also rewrites every sale_items.cost_price to the correct avg-at-time-of-sale.
    Idempotent: running twice produces identical results.
    """
    from .. import profit as profit_mod
    return profit_mod.rebuild_stock_state()


@router.post("/api/inventory/fix-missing-categories")
def fix_missing_categories_route() -> Any:
    """v8.7.2: Auto-create price_categories rows for any category_id that
    exists in category_stock_state but not in price_categories.

    This happens when:
      - Categories were hard-deleted (DELETE FROM price_categories) but
        stock_state rows remain
      - The Ezi import created stock_state rows with category_ids that were
        never inserted into price_categories

    The auto-created categories get:
      - name = f"Category #{id}"
      - code = f"#{id}"
      - sell_price = 0 (user must edit to set the real sell price)
      - color = "#ef4444" (red — signals needs attention)
      - active = 1

    After creating the categories, the user should edit each one to set the
    correct name/code/sell_price via Settings → Categories.

    Returns: {created: [...], skipped: [...]} where created is a list of
    {id, name, code} for newly created categories.
    """
    from .. import db
    created = []
    skipped = []
    with db.conn() as c:
        # Find orphan category_ids (in stock_state but not in price_categories)
        orphans = c.execute(
            "SELECT s.category_id, s.current_qty, s.current_value, s.current_avg_cost "
            "FROM category_stock_state s "
            "LEFT JOIN price_categories p ON s.category_id = p.id "
            "WHERE p.id IS NULL ORDER BY s.category_id"
        ).fetchall()
        for r in orphans:
            cid = r["category_id"]
            name = f"Category #{cid}"
            code = f"#{cid}"
            try:
                c.execute(
                    "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                    "VALUES(?,?,?,?,?, ?,1)",
                    (cid, name, code, 0, "#ef4444", cid),
                )
                created.append({"id": cid, "name": name, "code": code,
                                 "stock_qty": float(r["current_qty"] or 0),
                                 "stock_value": float(r["current_value"] or 0)})
            except Exception as e:
                skipped.append({"id": cid, "error": str(e)})
        db.log_activity(
            "fix_missing_categories", "inventory", None,
            f"Auto-created {len(created)} missing price_categories rows",
            {"created": [c["id"] for c in created], "skipped": [s["id"] for s in skipped]},
        )
    return {"created": created, "skipped": skipped,
            "message": f"Created {len(created)} missing categories. Edit them in Settings → Categories to set the correct name/code/sell_price."}


@router.get("/api/inventory/stock-state")
def get_stock_state_route(category_id: int = None) -> Any:
    """Read the materialized running state (fast path). Optionally filter by category."""
    from .. import profit as profit_mod
    return {"state": profit_mod.get_category_stock_state(category_id)}
