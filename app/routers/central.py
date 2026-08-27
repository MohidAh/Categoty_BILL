"""v8.0 Phase 5 — Central Purchasing & Distribution router.

Central Warehouse is a virtual branch (branch_id='BR-CENTRAL'). HQ records bulk
bills here, then distributes to branches via transfer challans (reuses Phase 4).

Endpoints:
- POST /api/central-purchases  — record a bulk bill (applies purchase to local state as BR-CENTRAL)
- GET  /api/central-purchases  — list central purchases
- GET  /api/central-purchases/{id}  — get a central purchase with items + distribution status
- POST /api/central-purchases/{id}/distribute  — distribute items to a branch (creates a transfer challan)
"""
import json, secrets
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from .. import db
from ..profit_engine import apply_purchase_to_state, peek_avg_cost

router = APIRouter()

CENTRAL_BRANCH_ID = "BR-CENTRAL"


class CentralPurchaseLineIn(BaseModel):
    category_id: int
    qty: float
    unit_cost: float


class CentralPurchaseIn(BaseModel):
    supplier_name: str = ""
    lines: list  # [{category_id, qty, unit_cost}]
    notes: str = ""


class DistributeIn(BaseModel):
    to_branch_id: str
    lines: list  # [{category_id, qty}] — qty per line to distribute
    notes: str = ""


@router.post("/api/central-purchases")
def create_central_purchase(payload: CentralPurchaseIn) -> Any:
    """Record a bulk bill at Central Warehouse. Applies purchase to local state."""
    if not payload.lines:
        raise HTTPException(400, "At least one line is required")
    purchase_no = "CP-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
    total_qty = 0.0
    total_cost = 0.0
    processed_lines = []
    # Apply each line as a purchase to local state (Central Warehouse stock)
    for line in payload.lines:
        cat_id = line.get("category_id")
        qty = float(line.get("qty", 0))
        unit_cost = float(line.get("unit_cost", 0))
        if not cat_id or qty <= 0 or unit_cost <= 0:
            raise HTTPException(400, f"Invalid line: {line}")
        apply_purchase_to_state(cat_id, qty, unit_cost)
        line_value = round(qty * unit_cost, 4)
        processed_lines.append({
            "category_id": cat_id, "qty": qty, "unit_cost": unit_cost,
            "line_value": line_value, "remaining_qty": qty,
        })
        total_qty += qty
        total_cost += line_value
    # Insert the central purchase + items
    with db.conn() as c:
        existing = c.execute("SELECT id FROM central_purchases WHERE purchase_no=?", (purchase_no,)).fetchone()
        if existing:
            raise HTTPException(409, f"Purchase {purchase_no} already exists")
        cur = c.execute(
            "INSERT INTO central_purchases(purchase_no, supplier_name, total_qty, total_cost, notes, status) "
            "VALUES(?,?,?,?,?, 'recorded')",
            (purchase_no, payload.supplier_name, total_qty, total_cost, payload.notes),
        )
        purchase_id = cur.lastrowid
        for line in processed_lines:
            # Look up category code
            row = c.execute("SELECT code FROM price_categories WHERE id=?", (line["category_id"],)).fetchone()
            cat_code = row["code"] if row else "?"
            line["category_code"] = cat_code
            c.execute(
                "INSERT INTO central_purchase_items(purchase_id, category_id, category_code, "
                "qty, unit_cost, line_value, distributed_qty, remaining_qty) "
                "VALUES(?,?,?,?,?, ?, 0, ?)",
                (purchase_id, line["category_id"], cat_code, line["qty"], line["unit_cost"],
                 line["line_value"], line["qty"]),
            )
    db.log_activity(
        "central_purchase_created", "central_purchase", purchase_id,
        f"Central purchase {purchase_no}: {total_qty} pcs, Rs {total_cost:,.0f}",
        {"purchase_no": purchase_no, "supplier": payload.supplier_name},
    )
    return {
        "purchase_id": purchase_id, "purchase_no": purchase_no,
        "total_qty": total_qty, "total_cost": total_cost,
        "lines": processed_lines,
    }


@router.get("/api/central-purchases")
def list_central_purchases(limit: int = 100) -> Any:
    """List central purchases."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM central_purchases ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"purchases": [dict(r) for r in rows], "count": len(rows)}


@router.get("/api/central-purchases/{purchase_id}")
def get_central_purchase(purchase_id: int) -> Any:
    """Get a central purchase with items + distribution status."""
    with db.conn() as c:
        p = c.execute("SELECT * FROM central_purchases WHERE id=?", (purchase_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Central purchase not found")
        items = c.execute(
            "SELECT * FROM central_purchase_items WHERE purchase_id=? ORDER BY id",
            (purchase_id,),
        ).fetchall()
    return {"purchase": dict(p), "items": [dict(i) for i in items]}


@router.post("/api/central-purchases/{purchase_id}/distribute")
def distribute_central_purchase(purchase_id: int, payload: DistributeIn) -> Any:
    """Distribute items from a central purchase to a branch.

    Creates a transfer challan from BR-CENTRAL to the destination branch.
    The unit_cost is taken from the central purchase line (not the current avg cost)
    so branches receive at the central bulk-buy price.
    """
    with db.conn() as c:
        p = c.execute("SELECT * FROM central_purchases WHERE id=?", (purchase_id,)).fetchone()
        if not p:
            raise HTTPException(404, "Central purchase not found")
        # Load the central purchase items
        cp_items = c.execute(
            "SELECT * FROM central_purchase_items WHERE purchase_id=?",
            (purchase_id,),
        ).fetchall()
        cp_by_cat = {i["category_id"]: dict(i) for i in cp_items}
    # Validate distribution lines + check remaining qty
    challan_lines = []
    total_qty = 0.0
    total_value = 0.0
    for line in payload.lines:
        cat_id = line.get("category_id")
        qty = float(line.get("qty", 0))
        if not cat_id or qty <= 0:
            raise HTTPException(400, f"Invalid line: {line}")
        cp_item = cp_by_cat.get(cat_id)
        if not cp_item:
            raise HTTPException(400, f"Category {cat_id} not in this central purchase")
        if qty > cp_item["remaining_qty"]:
            raise HTTPException(400, f"Cannot distribute {qty} pcs of Cat {cat_id} — only {cp_item['remaining_qty']} remaining")
        challan_lines.append({
            "category_id": cat_id, "qty": qty,
            "unit_cost": cp_item["unit_cost"],  # locked at central bulk-buy price
            "line_value": round(qty * cp_item["unit_cost"], 4),
            "category_code": cp_item["category_code"],
        })
        total_qty += qty
        total_value += challan_lines[-1]["line_value"]
    # Create the transfer challan from BR-CENTRAL
    challan_no = "CH-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper()
    # Apply transfer OUT from Central Warehouse (reduce local state)
    from ..profit_engine import apply_transfer_out_to_state
    for line in challan_lines:
        result = apply_transfer_out_to_state(line["category_id"], line["qty"])
        if result["qty"] < 0:
            raise HTTPException(400, f"Insufficient stock for category {line['category_id']}")
    # Insert the challan + items
    with db.conn() as c:
        existing = c.execute("SELECT id FROM transfer_challans WHERE challan_no=?", (challan_no,)).fetchone()
        if existing:
            raise HTTPException(409, f"Challan {challan_no} already exists")
        cur = c.execute(
            "INSERT INTO transfer_challans(challan_no, from_branch_id, to_branch_id, status, "
            "total_qty, total_value, notes) VALUES(?,?,?,?,?,?,?)",
            (challan_no, CENTRAL_BRANCH_ID, payload.to_branch_id, "in_transit",
             total_qty, total_value, payload.notes or f"Distribution from {p['purchase_no']}"),
        )
        challan_id = cur.lastrowid
        for line in challan_lines:
            c.execute(
                "INSERT INTO transfer_challan_items(challan_id, category_id, category_code, "
                "qty, unit_cost, line_value) VALUES(?,?,?,?,?,?)",
                (challan_id, line["category_id"], line["category_code"],
                 line["qty"], line["unit_cost"], line["line_value"]),
            )
        # Update the central purchase items' distributed_qty + remaining_qty
        for line in challan_lines:
            c.execute(
                "UPDATE central_purchase_items SET distributed_qty = distributed_qty + ?, "
                "remaining_qty = remaining_qty - ? WHERE purchase_id=? AND category_id=?",
                (line["qty"], line["qty"], purchase_id, line["category_id"]),
            )
        # Update central purchase status if fully distributed
        all_distributed = c.execute(
            "SELECT COUNT(*) AS n FROM central_purchase_items "
            "WHERE purchase_id=? AND remaining_qty > 0",
            (purchase_id,),
        ).fetchone()["n"] == 0
        if all_distributed:
            c.execute(
                "UPDATE central_purchases SET status='distributed' WHERE id=?",
                (purchase_id,),
            )
    db.log_activity(
        "central_purchase_distributed", "central_purchase", purchase_id,
        f"Distributed {total_qty} pcs from {p['purchase_no']} to {payload.to_branch_id} via {challan_no}",
        {"purchase_no": p["purchase_no"], "challan_no": challan_no, "to": payload.to_branch_id},
    )
    return {
        "challan_id": challan_id, "challan_no": challan_no,
        "from_branch_id": CENTRAL_BRANCH_ID, "to_branch_id": payload.to_branch_id,
        "total_qty": total_qty, "total_value": total_value,
        "purchase_status": "distributed" if all_distributed else "partial",
    }
