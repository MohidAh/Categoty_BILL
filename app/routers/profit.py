"""v5.0 — Profit & margin router.

Hosts the new /api/profit/* endpoints that depend on the running weighted
average cost engine (app/profit.py). Kept in a separate router to avoid
bloating the existing reports router.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime
from .. import profit as profit_mod
from .. import db
from .. import shop
from ..profit_cash import (
    add_capital_injection, list_capital_injections,
    get_capital_injections_summary, VALID_SOURCES,
)

router = APIRouter()


@router.get("/api/profit/margins")
def profit_margins() -> Any:
    """Per-category margins + Category Average Margin (informational) +
    Actual Overall Gross Margin (primary KPI).
    """
    return profit_mod.get_margins()


@router.get("/api/profit/monthly")
def profit_monthly(month: str = "") -> Any:
    """Monthly actual profit using the COGS method."""
    return profit_mod.get_monthly_profit(month)


@router.get("/api/profit/ytd")
def profit_ytd() -> Any:
    """Year-to-date cumulative profit. YTD margin = Cumulative GP ÷ Cumulative Sales."""
    return profit_mod.get_ytd_profit()


@router.get("/api/profit/dashboard")
def profit_dashboard() -> Any:
    """The hero Store Profit Dashboard."""
    return profit_mod.get_store_profit_dashboard()


@router.get("/api/profit/cash-buckets")
def profit_cash_buckets(date: str = "") -> Any:
    """The 4 cash buckets: Stock Replacement, Operating Expenses, Business Reserve, Owner Withdrawal."""
    return profit_mod.get_cash_buckets(date)


@router.get("/api/stock-reserve")
def stock_reserve() -> Any:
    """Stock reserve: daily COGS avg (30d), days of cover, target days, gap, recommendation."""
    return profit_mod.get_stock_reserve()


@router.get("/api/reports/daily-stock")
def daily_stock_report(date: str = "") -> Any:
    """Per-category daily stock report with the 11 columns from Section 17."""
    return profit_mod.get_daily_stock_report(date)


@router.get("/api/reports/daily-stock/export")
def daily_stock_export(date: str = "") -> Any:
    """CSV export of the daily stock report."""
    import csv, io
    from fastapi.responses import StreamingResponse
    r = profit_mod.get_daily_stock_report(date)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Category", "Code", "Opening Qty", "Purchased Qty",
                "Sold Qty", "Closing Qty", "Average Cost", "Stock Value",
                "Sales Value", "COGS", "Gross Profit"])
    for row in r.get("rows", []):
        w.writerow([row["date"], row["category"], row["code"],
                    row["opening_qty"], row["purchased_qty"], row["sold_qty"],
                    row["closing_qty"], row["average_cost"], row["stock_value"],
                    row["sales_value"], row["cogs"], row["gross_profit"]])
    t = r.get("totals", {})
    w.writerow(["TOTALS", "", "", t.get("opening_qty", 0), t.get("purchased_qty", 0),
                t.get("sold_qty", 0), t.get("closing_qty", 0), "",
                t.get("stock_value", 0), t.get("sales_value", 0),
                t.get("cogs", 0), t.get("gross_profit", 0)])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=daily_stock_{date or 'today'}.csv"},
    )


# ─── v5.0 Phase 7: Owner Withdrawals ───────────────────────────

class OwnerWithdrawalIn(BaseModel):
    amount: float
    payment_method: str = "cash"
    notes: str = ""


@router.post("/api/owner-withdrawals")
def add_owner_withdrawal_route(payload: OwnerWithdrawalIn) -> Any:
    """Record an owner withdrawal. Reduces cash_drawer but is NOT an operating expense."""
    try:
        wid = profit_mod.add_owner_withdrawal(payload.amount, payload.payment_method, payload.notes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": wid}


@router.get("/api/owner-withdrawals")
def list_owner_withdrawals_route(limit: int = 100) -> Any:
    return {"withdrawals": profit_mod.list_owner_withdrawals(limit)}


@router.get("/api/owner-withdrawals/summary")
def owner_withdrawals_summary_route(month: str = "") -> Any:
    return profit_mod.get_owner_withdrawals_summary(month)


# ─── v8.12.1: Capital Injections ────────────────────────────────────
# Owner-invested capital (initial investment, top-ups, partner contributions,
# bank loans). Each injection credits cash_drawer so the "Available for
# Withdrawal" formula no longer goes negative on Day 1.
#
# All endpoints require admin PIN to write (POST) — capital injections are
# equity events, not casual operations. Reads (GET) are open to managers+.

class CapitalInjectionIn(BaseModel):
    amount: float
    source: str = "owner_pocket"
    payment_method: str = "cash"
    notes: str = ""
    date: str = ""  # ISO date — empty = today
    manager_pin: str = ""


@router.post("/api/capital-injections")
def add_capital_injection_route(payload: CapitalInjectionIn) -> Any:
    """Record a capital injection (admin only — requires manager PIN).

    Use cases:
    - Recording the initial investment you put into the business on Day 1
      (this fixes the "negative withdrawal" trap where confirmed supplier
      bills have already drained cash_drawer before any sale happened).
    - Recording a capital top-up later (e.g. you inject another Rs 50k to
      buy more stock).
    - Recording a partner contribution or bank loan injection.
    """
    # PIN gate — admin only
    if not shop.verify_manager_pin_bool(payload.manager_pin):
        raise HTTPException(403, {
            "code": "manager_pin_required",
            "detail": "Manager PIN required to record capital injections. "
                      "Capital injections are equity events and require admin authorization."
        })
    try:
        inj_id = add_capital_injection(
            amount=payload.amount,
            source=payload.source,
            payment_method=payload.payment_method,
            notes=payload.notes,
            date=payload.date,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": inj_id, "message": f"Capital injection of Rs {payload.amount:.2f} recorded"}


@router.get("/api/capital-injections")
def list_capital_injections_route(limit: int = 100) -> Any:
    return {"injections": list_capital_injections(limit)}


@router.get("/api/capital-injections/summary")
def capital_injections_summary_route() -> Any:
    return get_capital_injections_summary()


@router.get("/api/capital-injections/sources")
def capital_injection_sources_route() -> Any:
    """Return the list of valid source codes for the UI dropdown."""
    source_labels = {
        "owner_pocket": "Owner's Pocket (personal savings)",
        "partner": "Partner Contribution",
        "bank_loan": "Bank Loan",
        "opening_balance": "Opening Balance (one-time fix for Day 1)",
    }
    return {"sources": [{"code": s, "label": source_labels.get(s, s)} for s in VALID_SOURCES]}
