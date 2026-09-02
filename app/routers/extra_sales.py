"""v8.18.13 — Extra (non-stock) Sales API.

For sales made OUTSIDE the POS of items that are not stock products
(cardboard cartons, scrap/raddi, empty drums, packing material...).
Other income: no stock movement, no COGS; flows into Actual Earnings,
P&L (other income), cash flow and the daily summary.
"""
from typing import Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import shop as shop_mod
from .. import db

router = APIRouter()


class ExtraSaleIn(BaseModel):
    item_name: str
    quantity: float = 1
    unit_price: float = 0
    description: str = ""
    payment_method: str = "cash"
    date: str = ""


@router.get("/api/extra-sales")
def list_extra_sales_route(month: str = "", limit: int = 200, q: str = "") -> Any:
    return {"extra_sales": shop_mod.list_extra_sales(month, limit, q)}


@router.post("/api/extra-sales")
def add_extra_sale_route(payload: ExtraSaleIn) -> Any:
    try:
        sid = shop_mod.add_extra_sale(
            payload.item_name, payload.quantity, payload.unit_price,
            payload.description, payload.payment_method,
            date_str=payload.date or None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    total = round(float(payload.quantity or 0) * float(payload.unit_price or 0), 2)
    db.log_activity("extra_sale_added", "extra_sale", sid,
                    f"Extra sale: {payload.item_name} — Rs {total:,.0f}",
                    {"item_name": payload.item_name, "quantity": payload.quantity,
                     "unit_price": payload.unit_price, "total": total,
                     "payment_method": payload.payment_method})
    return {"id": sid, "total": total}


@router.delete("/api/extra-sales/{sid}")
def delete_extra_sale_route(sid: int) -> Any:
    ok = shop_mod.delete_extra_sale(sid)
    if not ok:
        raise HTTPException(404, "extra sale not found")
    db.log_activity("extra_sale_deleted", "extra_sale", sid,
                    f"Deleted extra sale #{sid}", {"sid": sid})
    return {"ok": True}


@router.get("/api/extra-sales/summary")
def extra_sales_summary_route(month: str = "") -> Any:
    return shop_mod.get_extra_sales_summary(month)
