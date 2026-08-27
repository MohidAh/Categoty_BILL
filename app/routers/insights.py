"""Auto-generated router module — extracted from main.py Phase 1."""
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

router = APIRouter()

def list_activity(limit=10):
    """Get recent activity log entries."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"activity": [dict(r) for r in rows]}



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


class BIQuery(BaseModel):
    question: str




@router.get("/api/insights/abc")
def i_abc() -> Any:
    return insights.abc_analysis()




@router.get("/api/insights/dead-stock")
def i_dead_stock(days: int = 60) -> Any:
    return insights.dead_stock(days)




@router.get("/api/insights/price-comparison")
def i_price_comparison() -> Any:
    return insights.price_comparison()




@router.get("/api/insights/margin-erosion")
def i_margin_erosion() -> Any:
    return insights.margin_erosion()




@router.get("/api/insights/alerts")
def i_alerts() -> Any:
    return insights.active_alerts()




@router.get("/api/insights/forecast")
def i_forecast(item: str = "", periods: int = 3) -> Any:
    return insights.forecast(item if item else None, periods)




@router.get("/api/insights/dashboard")
def i_dashboard() -> Any:
    """KPI summary for home dashboard."""
    from datetime import datetime as _dt
    now = _dt.now()
    this_month = f"{now.year:04d}-{now.month:02d}"
    last_month_dt = now.replace(day=1) - timedelta(days=1)
    last_month = f"{last_month_dt.year:04d}-{last_month_dt.month:02d}"

    with db.conn() as c:
        total_bills = c.execute("SELECT COUNT(*) n FROM bills WHERE deleted_at IS NULL").fetchone()["n"]
        confirmed = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL"
        ).fetchone()["n"]
        review = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE status='review' AND deleted_at IS NULL"
        ).fetchone()["n"]
        total_spent = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL"
        ).fetchone()["v"]
        outstanding = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v "
            "FROM bills WHERE status='confirmed' AND payment_status='credit' AND deleted_at IS NULL"
        ).fetchone()["v"]
        suppliers_count = c.execute("SELECT COUNT(*) n FROM suppliers WHERE deleted_at IS NULL").fetchone()["n"]
        recent = c.execute(
            "SELECT id, supplier_name, bill_date, COALESCE(written_total, computed_total) AS total, "
            "status, payment_status FROM bills WHERE deleted_at IS NULL ORDER BY COALESCE(bill_date, date(created_at)) DESC, id DESC LIMIT 8"
        ).fetchall()

        # This month vs last month
        this_month_spent = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', bill_date) = ?", (this_month,)
        ).fetchone()["v"]
        this_month_bills = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', bill_date) = ?", (this_month,)
        ).fetchone()["n"]
        last_month_spent = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', bill_date) = ?", (last_month,)
        ).fetchone()["v"]
        last_month_bills = c.execute(
            "SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', bill_date) = ?", (last_month,)
        ).fetchone()["n"]

        # Top suppliers by spend (all time) — v8.12.0: exclude soft-deleted suppliers
        top_suppliers = c.execute(
            "SELECT s.name, s.phone, COUNT(b.id) AS bill_count, "
            "COALESCE(SUM(COALESCE(b.written_total, b.computed_total)), 0) AS total_spent "
            "FROM suppliers s LEFT JOIN bills b ON s.id = b.supplier_id "
            "AND b.status='confirmed' AND b.deleted_at IS NULL "
            "WHERE s.deleted_at IS NULL "
            "GROUP BY s.id ORDER BY total_spent DESC LIMIT 5"
        ).fetchall()

        # Category breakdown this month
        cat_breakdown = c.execute(
            "SELECT pc.name, pc.color, pc.sell_price, "
            "COUNT(bi.id) AS item_count, "
            "COALESCE(SUM(bi.line_total), 0) AS cost "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND strftime('%Y-%m', b.bill_date) = ? "
            "GROUP BY pc.id ORDER BY cost DESC", (this_month,)
        ).fetchall()

        alerts = insights.active_alerts()
    return {
        "kpis": {
            "total_bills": total_bills,
            "confirmed": confirmed,
            "review": review,
            "total_spent": round(total_spent, 2),
            "outstanding": round(outstanding, 2),
            "suppliers": suppliers_count,
        },
        "recent": [dict(r) for r in recent],
        "alerts": alerts,
        "sparklines": insights.sparklines(14),
        "activity": list_activity(8)["activity"],
        "recurring": insights.recurring_reminders(),
        "reorder_reminders": [],
        "trend_alerts": [],  # Populated by separate /api/trends endpoint
        "month_comparison": {
            "this_month": this_month,
            "this_month_spent": round(this_month_spent, 2),
            "this_month_bills": this_month_bills,
            "last_month_spent": round(last_month_spent, 2),
            "last_month_bills": last_month_bills,
            "spent_change_pct": round(
                ((this_month_spent - last_month_spent) / last_month_spent * 100) if last_month_spent > 0 else 0, 1
            ),
            "bills_change_pct": round(
                ((this_month_bills - last_month_bills) / last_month_bills * 100) if last_month_bills > 0 else 0, 1
            ),
        },
        "top_suppliers": [dict(r) for r in top_suppliers],
        "category_breakdown": [dict(r) for r in cat_breakdown],
    }


# ------------------------------------------------------------------
# Recurring bill reminders + Monthly close
# ------------------------------------------------------------------



@router.get("/api/insights/recurring")
def recurring_route() -> Any:
    """Suppliers you haven't bought from in a while (based on past patterns)."""
    return {"reminders": insights.recurring_reminders()}


# ------------------------------------------------------------------
# Market Trends + Reorder Reminders
# ------------------------------------------------------------------



@router.get("/api/trends")
def get_trends() -> Any:
    """Get active trend alerts."""
    from .. import trends as trends_mod
    return {"alerts": trends_mod.get_trend_alerts(10)}




@router.get("/api/trends/seasonal")
def get_seasonal() -> Any:
    """Get seasonal/festival alerts for the current month."""
    from .. import trends as trends_mod
    return {"alerts": trends_mod.get_seasonal_alerts()}




@router.get("/api/trends/dead-stock")
def get_dead_stock_trends() -> Any:
    """Get dead stock clearance suggestions."""
    from .. import trends as trends_mod
    return {"alerts": trends_mod.generate_dead_stock_alerts()}




@router.get("/api/trends/all")
def get_all_trends() -> Any:
    """Get all trend alerts (including dismissed)."""
    from .. import trends as trends_mod
    return {"alerts": trends_mod.get_all_trend_alerts(50)}




@router.post("/api/trends/refresh")
async def refresh_trends():
    """Trigger a trend analysis (fetch + AI). Runs in background."""
    from .. import trends as trends_mod
    import asyncio
    # Run in a thread so it doesn't block
    def _run():
        return trends_mod.run_trend_analysis()
    result = await asyncio.to_thread(_run)
    return result




@router.post("/api/trends/{alert_id}/dismiss")
def dismiss_trend(alert_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE trend_alerts SET status='dismissed' WHERE id=?", (alert_id,))
    return {"ok": True}




@router.post("/api/trends/{alert_id}/acted")
def acted_trend(alert_id: int) -> Any:
    with db.conn() as c:
        c.execute("UPDATE trend_alerts SET status='acted_on' WHERE id=?", (alert_id,))
    return {"ok": True}




@router.post("/api/insights/ask")
async def ask_groq(payload: BIQuery):
    """Ask a natural language question about your business data.
    Uses Groq (text-only) for fast business intelligence.
    """
    import httpx as _httpx
    # Gather data summary for context
    with db.conn() as c:
        total_bills = c.execute("SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL").fetchone()["n"]
        total_spent = c.execute("SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v FROM bills WHERE status='confirmed' AND deleted_at IS NULL").fetchone()["v"]
        outstanding = c.execute("SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v FROM bills WHERE status='confirmed' AND payment_status='credit' AND deleted_at IS NULL").fetchone()["v"]
        suppliers = c.execute("SELECT COUNT(*) n FROM suppliers WHERE deleted_at IS NULL").fetchone()["n"]
        top_sup = c.execute(
            "SELECT s.name, COUNT(b.id) AS bills, COALESCE(SUM(COALESCE(b.written_total, b.computed_total, 0)), 0) AS spent "
            "FROM suppliers s LEFT JOIN bills b ON s.id = b.supplier_id AND b.status='confirmed' AND b.deleted_at IS NULL "
            "WHERE s.deleted_at IS NULL "
            "GROUP BY s.id ORDER BY spent DESC LIMIT 5"
        ).fetchall()
        cat_stats = c.execute(
            "SELECT pc.name, pc.sell_price, COUNT(bi.id) AS items, COALESCE(SUM(bi.line_total), 0) AS cost "
            "FROM price_categories pc LEFT JOIN bill_items bi ON bi.category_id = pc.id "
            "LEFT JOIN bills b ON bi.bill_id = b.id AND b.status='confirmed' AND b.deleted_at IS NULL "
            "GROUP BY pc.id ORDER BY pc.sell_price"
        ).fetchall()

    data_summary = f"""Business Data Summary:
- Total confirmed bills: {total_bills}
- Total spent: Rs {total_spent:,.0f}
- Outstanding credit (urdhaar): Rs {outstanding:,.0f}
- Active suppliers: {suppliers}
- Top suppliers by spend: {', '.join(f'{s["name"]} (Rs {s["spent"]:,.0f}, {s["bills"]} bills)' for s in top_sup)}
- Categories: {', '.join(f'{c["name"]}/Rs{c["sell_price"]} ({c["items"]} items, Rs {c["cost"]:,.0f})' for c in cat_stats)}
"""
    # Find a Groq provider
    groq_key = None
    groq_model = "meta-llama/llama-4-scout-17b-16e-instruct"
    with db.conn() as c:
        row = c.execute("SELECT api_key, model FROM ai_providers WHERE provider_type='groq' AND enabled=1 ORDER BY priority LIMIT 1").fetchone()
        if row:
            groq_key = crypto_mod.decrypt_api_key(row["api_key"])
            if row["model"]:
                groq_model = row["model"]
    if not groq_key:
        groq_key = os.getenv("GROQ_KEY")
    if not groq_key:
        return {"answer": "No Groq provider configured. Add a Groq API key in Settings to use business intelligence."}

    try:
        r = _httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": groq_model,
                "temperature": 0.3,
                "max_tokens": 500,
                "messages": [
                    {"role": "system", "content": f"You are a business analyst helping a Pakistani wholesale shopkeeper. Answer questions based on this data:\n\n{data_summary}\n\nKeep answers concise (2-3 sentences max). Use Rs for amounts."},
                    {"role": "user", "content": payload.question},
                ],
            },
            headers={"Authorization": f"Bearer {groq_key}"},
            timeout=30,
        )
        r.raise_for_status()
        answer = r.json()["choices"][0]["message"]["content"]
        return {"answer": answer, "model": groq_model}
    except Exception as e:
        return {"answer": f"Error: {e}", "model": groq_model}




# ═══════════════════════════════════════════════════
# Phase 8: AI-Native Insights
# ═══════════════════════════════════════════════════

@router.get("/api/insights/morning-briefing")
def morning_briefing() -> Any:
    """Yesterday's summary + today's action items. Cached per day."""
    from ..db import get_setting
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_key = f"briefing_{yesterday}"
    cached = get_setting(cache_key, "")
    if cached:
        return json.loads(cached)
    with db.conn() as c:
        # Yesterday's metrics
        rev = c.execute(f"SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE date(created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}", (yesterday,)).fetchone()["v"]
        paid = c.execute("SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE date(created_at)=? AND payment_status='paid'", (yesterday,)).fetchone()["v"]
        credit = c.execute("SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE date(created_at)=? AND payment_status='credit'", (yesterday,)).fetchone()["v"]
        cogs = c.execute(f"SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si JOIN sales s ON si.sale_id=s.id WHERE date(s.created_at)=? AND {db.VALID_SALE_FILTER}", (yesterday,)).fetchone()["v"]
        # 4-week same-weekday average
        weekday = datetime.now().weekday()
        avg_rev = c.execute(
            "SELECT COALESCE(AVG(daily_rev), 0) AS v FROM ("
            "SELECT date(created_at) AS d, SUM(total) AS daily_rev FROM sales "
            f"WHERE {db.VALID_SALE_FILTER_NO_ALIAS} AND date(created_at) >= date('now','-28 days') "
            "AND strftime('%w', created_at) = strftime('%w', ?) "
            "GROUP BY d)", (yesterday,),
        ).fetchone()["v"]
        # Credits due today
        due_today = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_credit), 0) AS v FROM customers WHERE total_credit > 0 AND deleted_at IS NULL"
        ).fetchone()
        # Low stock top 3
        inv = shop_mod.get_inventory()
        low_stock = [i for i in inv if i.get("low_stock") or i.get("out_of_stock")][:3]
        margin = ((rev - cogs) / rev * 100) if rev > 0 else 0
        change_pct = ((rev - avg_rev) / avg_rev * 100) if avg_rev > 0 else 0
    briefing = {
        "date": yesterday,
        "revenue": round(rev, 2),
        "vs_4week_avg": round(avg_rev, 2),
        "change_pct": round(change_pct, 1),
        "margin_pct": round(margin, 1),
        "cash_vs_credit": {"paid": round(paid, 2), "credit": round(credit, 2)},
        "credits_due": {"count": due_today["n"], "total": due_today["v"]},
        "low_stock_top3": [{"name": i["category_name"], "stock": i["stock"]} for i in low_stock],
        "suggestion": "Review low-stock items and create purchase orders." if low_stock else "All stock levels healthy.",
    }
    # Cache for the day
    from ..db import set_setting
    set_setting(cache_key, json.dumps(briefing))
    return briefing


@router.get("/api/insights/anomalies")
def anomaly_alerts() -> Any:
    """Detect sales dips/spikes >25% vs same-weekday baseline."""
    from datetime import datetime, timedelta
    alerts = []
    with db.conn() as c:
        # Check last 7 days for anomalies
        for days_ago in range(1, 8):
            date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            rev = c.execute(f"SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE date(created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}", (date,)).fetchone()["v"]
            avg = c.execute(
                "SELECT COALESCE(AVG(daily_rev), 0) AS v FROM ("
                "SELECT date(created_at) AS d, SUM(total) AS daily_rev FROM sales "
                f"WHERE {db.VALID_SALE_FILTER_NO_ALIAS} AND date(created_at) >= date('now','-35 days') "
                "AND strftime('%w', created_at) = strftime('%w', ?) AND date(created_at) != ? "
                "GROUP BY d)", (date, date),
            ).fetchone()["v"]
            if avg > 0:
                change = ((rev - avg) / avg) * 100
                if abs(change) > 25:
                    alerts.append({
                        "date": date, "revenue": round(rev, 2), "baseline": round(avg, 2),
                        "change_pct": round(change, 1),
                        "type": "spike" if change > 0 else "dip",
                        "severity": "high" if abs(change) > 50 else "medium",
                    })
    return {"alerts": alerts, "count": len(alerts)}


class WhatIfIn(BaseModel):
    category_id: int
    new_price: float
    elasticity: float = 0  # 0 = no volume change, -0.5 = elastic


@router.post("/api/insights/what-if")
def what_if_simulator(payload: WhatIfIn) -> Any:
    """Simulate price change impact on revenue and margin."""
    with db.conn() as c:
        cat = c.execute("SELECT * FROM price_categories WHERE id=?", (payload.category_id,)).fetchone()
        if not cat:
            raise HTTPException(404, "category not found")
        # Last 30 days volume
        vol = c.execute(
            "SELECT COALESCE(SUM(si.qty), 0) AS qty, COALESCE(SUM(si.line_total), 0) AS rev, "
            "COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            f"WHERE si.category_id=? AND {db.VALID_SALE_FILTER} "
            "AND date(s.created_at) >= date('now','-30 days')",
            (payload.category_id,),
        ).fetchone()
    old_price = cat["sell_price"]
    new_price = payload.new_price
    qty = vol["qty"]
    # Elasticity: % price change * elasticity = % volume change
    price_change_pct = (new_price - old_price) / old_price if old_price > 0 else 0
    volume_change_pct = price_change_pct * payload.elasticity
    new_qty = qty * (1 + volume_change_pct)
    old_revenue = vol["rev"]
    new_revenue = new_price * new_qty
    avg_cost = c.execute("SELECT AVG(price) AS v FROM bill_items WHERE category_id=?", (payload.category_id,)).fetchone()["v"] if 'c' in dir() else 0
    # Get avg cost from inventory
    inv = shop_mod.get_inventory()
    cat_inv = next((i for i in inv if i["category_id"] == payload.category_id), None)
    avg_cost = cat_inv["avg_cost"] if cat_inv else 0
    old_margin = old_revenue - vol["cogs"]
    new_margin = new_revenue - (avg_cost * new_qty)
    return {
        "category": cat["name"], "old_price": old_price, "new_price": new_price,
        "old_qty_30d": qty, "new_qty_projected": round(new_qty, 0),
        "old_revenue_30d": round(old_revenue, 2), "new_revenue_projected": round(new_revenue, 2),
        "old_margin_30d": round(old_margin, 2), "new_margin_projected": round(new_margin, 2),
        "revenue_change": round(new_revenue - old_revenue, 2),
        "margin_change": round(new_margin - old_margin, 2),
        "elasticity": payload.elasticity,
    }


# ═══════════════════════════════════════════════════
# FIX 3.2: Briefing Action Buttons
# ═══════════════════════════════════════════════════

@router.get("/api/insights/briefing/actions")
def briefing_actions() -> Any:
    """Get actionable items from morning briefing with one-click action buttons."""
    briefing = morning_briefing()
    actions = []
    # Low stock → Create PO
    for item in briefing.get("low_stock_top3", []):
        actions.append({
            "type": "create_po",
            "label": f"Create PO for {item['name']}",
            "endpoint": "/api/reorder-reminders/auto-po",
            "category": item.get("name", ""),
            "icon": "package",
        })
    # Credits due → WhatsApp reminder
    if briefing.get("credits_due", {}).get("count", 0) > 0:
        actions.append({
            "type": "whatsapp_reminder",
            "label": f"Send WhatsApp to {briefing['credits_due']['count']} customers with credit",
            "endpoint": "/api/customers/ar-aging",
            "icon": "phone",
        })
    # Suggestion → Open report
    actions.append({
        "type": "open_report",
        "label": "View Sales by Customer report",
        "endpoint": "/api/reports/sales-by-customer",
        "icon": "chart",
    })
    return {"actions": actions, "briefing_date": briefing.get("date", "")}


# ─── v4.0 Phase 6 — Daily summary, commissions, cashier scorecard ─────────────

@router.get("/api/summary/daily")
def daily_summary_route(date: str = "") -> Any:
    """Today's key numbers for the owner daily summary."""
    return shop_mod.get_daily_summary(date or None)


@router.get("/api/summary/daily-text")
def daily_summary_text_route(date: str = "") -> Any:
    """Plain-text daily summary (WhatsApp-friendly)."""
    return {"text": shop_mod.build_daily_summary_text(date or None)}


@router.get("/api/summary/whatsapp-link")
def whatsapp_link_route(phone: str = "", date: str = "") -> Any:
    """Build a wa.me link with the daily summary pre-filled."""
    if not phone:
        phone = db.get_setting("owner_phone", "")
    link = shop_mod.build_whatsapp_summary_link(phone, date or None)
    return {"link": link, "phone": phone}


# ─── Commissions ──────────────────────────────────────────────

class CommissionRuleIn(BaseModel):
    employee_id: Optional[int] = None
    role: str = "cashier"
    type: str = "percent"  # "percent" | "flat"
    value: float = 0


@router.get("/api/commissions/rules")
def list_commission_rules_route() -> Any:
    return {"rules": shop_mod.list_commission_rules(active_only=False)}


@router.post("/api/commissions/rules")
def add_commission_rule_route(payload: CommissionRuleIn) -> Any:
    try:
        rid = shop_mod.add_commission_rule(payload.employee_id, payload.role, payload.type, payload.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rid}


@router.get("/api/commissions/summary")
def commissions_summary_route(month: str = "") -> Any:
    return shop_mod.get_commissions_summary(month)


# ─── Cashier Scorecard ────────────────────────────────────────

@router.get("/api/employees/{eid}/scorecard")
def employee_scorecard_route(eid: int, month: str = "") -> Any:
    result = shop_mod.get_employee_scorecard(eid, month)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ─── v8.16.0: AI Market Intelligence Agent ──────────────────────────────────

@router.post("/api/ai/market-intelligence")
def generate_market_intel_route() -> Any:
    """AI Market Intelligence Agent — searches the web for trending wholesale
    products, seasonal items, and market opportunities, then uses the LLM
    to generate structured recommendations mapped to the shop's price categories.

    Architecture: Web Search → LLM Analysis → Structured Recommendations

    Returns:
        {
            "recommendations": [{product_name, estimated_wholesale_cost, suggested_category, ...}],
            "search_results": [{title, url, snippet}],
            "shop_context": {categories, seasonal_context, current_month},
            "generated_at": "2026-08-22 ...",
            "ai_provider": "gemini" | "groq" | "none"
        }
    """
    from ..market_intel import generate_market_intelligence
    try:
        result = generate_market_intelligence()
        return result
    except Exception as e:
        raise HTTPException(500, f"Market intelligence failed: {str(e)}")
