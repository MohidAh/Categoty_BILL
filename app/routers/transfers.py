"""v8.0 Phase 4 — Inter-Branch Stock Transfer router.

Endpoints:
- POST /api/transfers/out  — sender creates a challan (applies transfer OUT to local state)
- GET  /api/transfers  — list challans (filter by status, direction)
- GET  /api/transfers/{id}  — get a single challan with items
- POST /api/transfers/{id}/accept  — receiver accepts (applies transfer IN via apply_purchase_to_state)
- POST /api/transfers/{id}/reject  — receiver rejects (no state change on receiver)

LOAD-BEARING: the challan carries unit_cost per line EXPLICITLY. The sender's
apply_transfer_out_to_state captures the avg cost at the moment of transfer;
the receiver applies via apply_purchase_to_state with that captured unit_cost.
The sender's avg is UNCHANGED; the receiver's avg updates correctly. No COGS
or revenue is recorded on either side — this is an inventory movement, not a sale.
"""
import json, uuid, secrets
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from .. import db
from ..profit_engine import apply_transfer_out_to_state, apply_purchase_to_state, peek_avg_cost

router = APIRouter()


class TransferLineIn(BaseModel):
    category_id: int
    qty: float


class TransferOutIn(BaseModel):
    to_branch_id: str
    from_branch_id: Optional[str] = None  # defaults to local branch_config.branch_id
    lines: list  # [{category_id, qty}]
    notes: str = ""


def _get_local_branch_id() -> str:
    """Read the local branch_id from branch_config."""
    with db.conn() as c:
        row = c.execute("SELECT branch_id FROM branch_config WHERE id=1").fetchone()
    return row["branch_id"] if row and row["branch_id"] else "BR-LOCAL"


@router.post("/api/transfers/out")
def create_transfer_out(payload: TransferOutIn) -> Any:
    """Sender creates a transfer challan. Applies transfer OUT to local state.

    For each line:
    1. Read the sender's current avg cost via peek_avg_cost
    2. apply_transfer_out_to_state(category_id, qty) — reduces qty+value, avg UNCHANGED
    3. Lock the unit_cost + line_value into the challan item

    The challan is created with status='in_transit'. The receiver will accept or reject.

    C3 fix (v8.13.4): the entire operation — state mutation + challan row
    insert + line-item inserts — is now committed as a SINGLE atomic write
    transaction via `db.write_tx()` (BEGIN IMMEDIATE). Previously these were
    in separate transactions; a crash between them would silently lose the
    inventory (state mutated, no challan row to undo it).
    """
    if not payload.lines or len(payload.lines) == 0:
        raise HTTPException(400, "At least one line is required")
    from_branch_id = payload.from_branch_id or _get_local_branch_id()
    if not payload.to_branch_id:
        raise HTTPException(400, "to_branch_id is required")
    if payload.to_branch_id == from_branch_id:
        raise HTTPException(400, "Cannot transfer to the same branch")
    # Generate a unique challan_no
    challan_no = "CH-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
    # Process each line: read avg cost, apply transfer OUT, capture unit_cost
    processed_lines = []
    total_qty = 0.0
    total_value = 0.0
    # C3: SINGLE write_tx for the whole operation (state + challan + items)
    with db.write_tx() as c:
        # Check challan_no doesn't already exist (idempotency)
        existing = c.execute("SELECT id FROM transfer_challans WHERE challan_no=?", (challan_no,)).fetchone()
        if existing:
            raise HTTPException(409, f"Challan {challan_no} already exists")
        # Apply each transfer OUT in the SAME transaction
        for line in payload.lines:
            cat_id = line.get("category_id")
            qty = float(line.get("qty", 0))
            if not cat_id or qty <= 0:
                raise HTTPException(400, f"Invalid line: category_id={cat_id}, qty={qty}")
            # Peek avg cost inside the txn (so it's consistent with the apply)
            avg = peek_avg_cost(c, cat_id)
            if avg <= 0:
                raise HTTPException(400, f"Category {cat_id} has no stock (avg cost = 0)")
            row = c.execute("SELECT code FROM price_categories WHERE id=?", (cat_id,)).fetchone()
            cat_code = row["code"] if row else "?"
            # Apply the transfer OUT in this same transaction
            result = apply_transfer_out_to_state(cat_id, qty, c=c)
            if result["qty"] < 0:
                raise HTTPException(400, f"Insufficient stock for category {cat_id}")
            processed_lines.append({
                "category_id": cat_id, "category_code": cat_code,
                "qty": qty, "unit_cost": result["unit_cost"], "line_value": result["line_value"],
            })
            total_qty += qty
            total_value += result["line_value"]
        # Insert the challan + items in the SAME transaction
        cur = c.execute(
            "INSERT INTO transfer_challans(challan_no, from_branch_id, to_branch_id, status, "
            "total_qty, total_value, notes) VALUES(?,?,?,?,?,?,?)",
            (challan_no, from_branch_id, payload.to_branch_id, "in_transit",
             total_qty, total_value, payload.notes),
        )
        challan_id = cur.lastrowid
        for line in processed_lines:
            c.execute(
                "INSERT INTO transfer_challan_items(challan_id, category_id, category_code, "
                "qty, unit_cost, line_value) VALUES(?,?,?,?,?,?)",
                (challan_id, line["category_id"], line["category_code"],
                 line["qty"], line["unit_cost"], line["line_value"]),
            )
    db.log_activity(
        "transfer_out_created", "transfer_challan", challan_id,
        f"Transfer OUT: {challan_no} ({from_branch_id} → {payload.to_branch_id}) — "
        f"{total_qty} pcs, Rs {total_value:,.0f}",
        {"challan_no": challan_no, "from": from_branch_id, "to": payload.to_branch_id,
         "total_qty": total_qty, "total_value": total_value},
    )
    return {
        "challan_id": challan_id, "challan_no": challan_no,
        "from_branch_id": from_branch_id, "to_branch_id": payload.to_branch_id,
        "status": "in_transit", "total_qty": total_qty, "total_value": total_value,
        "lines": processed_lines,
    }


@router.get("/api/transfers")
def list_transfers(status: str = "", direction: str = "", limit: int = 100) -> Any:
    """List transfer challans. Optional filters: status, direction (in/out).

    direction='in'  → challans where to_branch_id = local branch
    direction='out' → challans where from_branch_id = local branch
    """
    local_branch_id = _get_local_branch_id()
    with db.conn() as c:
        q = "SELECT * FROM transfer_challans WHERE 1=1"
        args = []
        if status:
            q += " AND status=?"
            args.append(status)
        if direction == "in":
            q += " AND to_branch_id=?"
            args.append(local_branch_id)
        elif direction == "out":
            q += " AND from_branch_id=?"
            args.append(local_branch_id)
        q += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(q, args).fetchall()
    return {"transfers": [dict(r) for r in rows], "count": len(rows)}


@router.get("/api/transfers/{challan_id}")
def get_transfer(challan_id: int) -> Any:
    """Get a single challan with its line items."""
    with db.conn() as c:
        ch = c.execute("SELECT * FROM transfer_challans WHERE id=?", (challan_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Challan not found")
        items = c.execute(
            "SELECT * FROM transfer_challan_items WHERE challan_id=? ORDER BY id",
            (challan_id,),
        ).fetchall()
    return {"challan": dict(ch), "items": [dict(i) for i in items]}


@router.post("/api/transfers/{challan_id}/accept")
def accept_transfer(challan_id: int) -> Any:
    """Receiver accepts a challan. Applies transfer IN via apply_purchase_to_state.

    Idempotent: if the challan is already 'accepted', returns success without re-applying.

    C3 fix (v8.13.4): the status update + state mutation now run in a
    SINGLE write_tx(). Previously the status UPDATE was in one transaction
    and apply_purchase_to_state opened its own — a crash between them would
    double-count the inventory on the next accept (state was applied, status
    not updated, so the next accept re-applied).
    """
    with db.write_tx() as c:
        ch = c.execute("SELECT * FROM transfer_challans WHERE id=?", (challan_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Challan not found")
        if ch["status"] == "accepted":
            return {"ok": True, "challan_no": ch["challan_no"], "status": "accepted",
                    "note": "Already accepted (idempotent)"}
        if ch["status"] == "rejected":
            raise HTTPException(400, "Cannot accept a rejected challan")
        # Load the line items (unit_cost is locked in the challan)
        items = c.execute(
            "SELECT * FROM transfer_challan_items WHERE challan_id=?",
            (challan_id,),
        ).fetchall()
        # Apply each line as a purchase at the captured unit_cost,
        # in the SAME transaction as the status UPDATE.
        for item in items:
            apply_purchase_to_state(item["category_id"], item["qty"], item["unit_cost"], c=c)
        # Mark as accepted in the SAME transaction
        c.execute(
            "UPDATE transfer_challans SET status='accepted', "
            "accepted_at=datetime('now','localtime') WHERE id=?",
            (challan_id,),
        )
    db.log_activity(
        "transfer_in_accepted", "transfer_challan", challan_id,
        f"Transfer IN accepted: {ch['challan_no']} — {ch['total_qty']} pcs, Rs {ch['total_value']:,.0f}",
        {"challan_no": ch["challan_no"], "from": ch["from_branch_id"],
         "to": ch["to_branch_id"]},
    )
    return {"ok": True, "challan_no": ch["challan_no"], "status": "accepted"}


@router.post("/api/transfers/{challan_id}/reject")
def reject_transfer(challan_id: int, payload: dict = None) -> Any:
    """Receiver rejects a challan. No state change on receiver.

    The sender's stock was already reduced when the challan was created — rejection
    does NOT auto-reverse it. The sender must manually adjust stock if needed.
    Idempotent: rejecting an already-rejected challan returns success.
    """
    body = payload or {}
    reason = body.get("reason", "")
    with db.conn() as c:
        ch = c.execute("SELECT * FROM transfer_challans WHERE id=?", (challan_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Challan not found")
        if ch["status"] == "rejected":
            return {"ok": True, "challan_no": ch["challan_no"], "status": "rejected",
                    "note": "Already rejected (idempotent)"}
        if ch["status"] == "accepted":
            raise HTTPException(400, "Cannot reject an accepted challan")
        c.execute(
            "UPDATE transfer_challans SET status='rejected', "
            "rejected_at=datetime('now','localtime') WHERE id=?",
            (challan_id,),
        )
    db.log_activity(
        "transfer_in_rejected", "transfer_challan", challan_id,
        f"Transfer IN rejected: {ch['challan_no']}" + (f" — {reason}" if reason else ""),
        {"challan_no": ch["challan_no"], "reason": reason},
    )
    return {"ok": True, "challan_no": ch["challan_no"], "status": "rejected"}
