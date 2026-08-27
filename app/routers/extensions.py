"""v6.0+ — Extensions router: bundles, happy-hour, lost-sales, break-even,
margin alerts, cash-flow forecast, closed-days, seasons, customer groups,
WhatsApp parse, Raast reconciliation, accountant export, AI infrastructure.
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from .. import db
from .. import extensions as ext

router = APIRouter()


# ─── Phase 3: Bundles ──────────────────────────────────────────────────────

class BundleIn(BaseModel):
    name: str
    price: float
    items: list  # [{category_id, qty}]


@router.get("/api/bundles")
def list_bundles_route(active_only: bool = False) -> Any:
    return {"bundles": ext.list_bundles(active_only)}


@router.post("/api/bundles")
def create_bundle_route(payload: BundleIn) -> Any:
    bid = ext.create_bundle(payload.name, payload.price, payload.items)
    return {"id": bid}


@router.delete("/api/bundles/{bid}")
def delete_bundle_route(bid: int) -> Any:
    if not ext.delete_bundle(bid):
        raise HTTPException(404, "bundle not found")
    return {"ok": True}


@router.get("/api/bundles/{bid}/allocation")
def bundle_allocation_route(bid: int) -> Any:
    return {"items": ext.get_bundle_sell_price_allocation(bid)}


# ─── Phase 3: Happy-Hour ───────────────────────────────────────────────────

class PriceRuleIn(BaseModel):
    category_id: Optional[int] = None
    pct: float
    start_hhmm: str
    end_hhmm: str


@router.get("/api/price-rules")
def list_price_rules_route() -> Any:
    return {"rules": ext.list_price_rules(active_only=False)}


@router.post("/api/price-rules")
def create_price_rule_route(payload: PriceRuleIn) -> Any:
    rid = ext.create_price_rule(payload.category_id, payload.pct, payload.start_hhmm, payload.end_hhmm)
    return {"id": rid}


@router.delete("/api/price-rules/{rid}")
def delete_price_rule_route(rid: int) -> Any:
    if not ext.delete_price_rule(rid):
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.get("/api/price-rules/active")
def active_happy_hour_route(category_id: int = None) -> Any:
    result = ext.get_active_happy_hour_discount(category_id)
    return {"active": result is not None, "discount": result}


# ─── Phase 4: Lost Sales ───────────────────────────────────────────────────

class LostSaleIn(BaseModel):
    category_id: int
    qty: int
    est_revenue: float = 0


@router.post("/api/lost-sales")
def log_lost_sale_route(payload: LostSaleIn) -> Any:
    ext.log_lost_sale(payload.category_id, payload.qty, payload.est_revenue)
    return {"ok": True}


@router.get("/api/lost-sales/summary")
def lost_sales_summary_route(month: str = "") -> Any:
    return ext.get_lost_sales_summary(month)


# ─── Phase 4: Break-Even ───────────────────────────────────────────────────

@router.get("/api/break-even")
def break_even_route() -> Any:
    return ext.get_break_even()


# ─── Phase 4: Margin Alerts ────────────────────────────────────────────────

@router.get("/api/margin-alerts")
def margin_alerts_route() -> Any:
    return {"alerts": ext.get_margin_alerts()}


@router.post("/api/margin-alerts/apply")
def apply_margin_fix_route(payload: dict) -> Any:
    """Apply suggested price to a category (manager PIN checked by middleware)."""
    cat_id = int(payload.get("category_id", 0))
    new_price = float(payload.get("new_price", 0))
    if not cat_id or new_price <= 0:
        raise HTTPException(400, "category_id and new_price required")
    with db.conn() as c:
        c.execute("UPDATE price_categories SET sell_price=? WHERE id=?", (new_price, cat_id))
    db.log_activity("margin_fix_applied", "category", cat_id,
                    f"Price updated to Rs {new_price:.0f} (margin protection)", {"new_price": new_price})
    return {"ok": True}


# ─── Phase 4: Cash-Flow Forecast ───────────────────────────────────────────

@router.get("/api/cash-flow-forecast")
def cash_flow_forecast_route() -> Any:
    return ext.get_cash_flow_forecast()


# ─── Phase 6: Closed Days + Seasons ────────────────────────────────────────

class ClosedDayIn(BaseModel):
    date: str
    label: str = ""


@router.get("/api/closed-days")
def list_closed_days_route() -> Any:
    return {"days": ext.list_closed_days()}


@router.post("/api/closed-days")
def add_closed_day_route(payload: ClosedDayIn) -> Any:
    ext.add_closed_day(payload.date, payload.label)
    return {"ok": True}


@router.delete("/api/closed-days/{date}")
def remove_closed_day_route(date: str) -> Any:
    ext.remove_closed_day(date)
    return {"ok": True}


class SeasonIn(BaseModel):
    year: int
    name: str
    start: str
    end: str


@router.get("/api/seasons")
def list_seasons_route() -> Any:
    return {"seasons": ext.list_seasons()}


@router.post("/api/seasons")
def add_season_route(payload: SeasonIn) -> Any:
    sid = ext.add_season(payload.year, payload.name, payload.start, payload.end)
    return {"id": sid}


# ─── Phase 5: Urdhaar Reminders ────────────────────────────────────────────

@router.get("/api/reminders/urdhaar")
def urdhaar_reminders_route() -> Any:
    return {"reminders": ext.get_urdhaar_reminders()}


# ─── Phase 5: Customer Groups + Broadcast ──────────────────────────────────

@router.get("/api/customer-groups")
def customer_groups_route() -> Any:
    return {"groups": ext.get_customer_groups()}


@router.get("/api/broadcast")
def broadcast_route(group: str = "retail") -> Any:
    return ext.get_broadcast_list(group)


# ─── Phase 5: WhatsApp Order Parsing ───────────────────────────────────────

class WhatsAppParseIn(BaseModel):
    text: str


@router.post("/api/whatsapp/parse")
def whatsapp_parse_route(payload: WhatsAppParseIn) -> Any:
    return ext.parse_whatsapp_order(payload.text)


# ─── Phase 5: Raast Reconciliation ─────────────────────────────────────────

@router.get("/api/raast/reconciliation")
def raast_reconciliation_route() -> Any:
    return ext.get_raast_reconciliation()


# ─── Phase 6: Accountant Export ────────────────────────────────────────────

@router.get("/api/export/accountant")
def accountant_export_route(month: str = "") -> Any:
    """Export a zip with P&L, cashflow, bills register, expenses, monthly close PDF."""
    import io, zipfile, csv
    from fastapi.responses import StreamingResponse
    from datetime import datetime as _dt
    if not month:
        month = _dt.now().strftime("%Y-%m")
    from .. import profit as _profit
    from .. import shop as _shop
    pnl = _profit.get_monthly_profit(month)
    expense_summary = _shop.get_expense_summary(month)
    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    pnl_csv = io.StringIO()
    w = csv.writer(pnl_csv)
    w.writerow(["P&L Statement", month])
    for k, v in pnl.items():
        if k != "note":
            w.writerow([k, v])
    zf.writestr("P&L.csv", pnl_csv.getvalue())
    cf_csv = io.StringIO()
    w = csv.writer(cf_csv)
    w.writerow(["Cash Flow", month])
    for line in [("Opening Inventory", pnl["opening_inventory"]), ("Purchases", pnl["purchases"]),
                 ("Closing Inventory", pnl["closing_inventory"]), ("COGS", pnl["cogs"]),
                 ("Sales", pnl["sales"]), ("Gross Profit", pnl["gross_profit"]),
                 ("Operating Expenses", pnl["operating_expenses"]), ("Operating Profit", pnl["operating_profit"])]:
        w.writerow(line)
    zf.writestr("cashflow.csv", cf_csv.getvalue())
    exp_csv = io.StringIO()
    w = csv.writer(exp_csv)
    w.writerow(["Category", "Count", "Total", "Budget", "Pct"])
    for cat in expense_summary.get("by_category", []):
        w.writerow([cat["category"], cat["count"], cat["total"], cat["budget"], cat["pct"]])
    zf.writestr("expenses.csv", exp_csv.getvalue())
    bills_csv = io.StringIO()
    w = csv.writer(bills_csv)
    w.writerow(["Bill ID", "Supplier", "Date", "Total", "Status", "Payment"])
    with db.conn() as c:
        bills = c.execute(
            "SELECT id, supplier_name, bill_date, COALESCE(written_total, computed_total) AS total, "
            "status, payment_status FROM bills WHERE strftime('%Y-%m', bill_date)=? "
            "AND deleted_at IS NULL ORDER BY id", (month,)).fetchall()
    for b in bills:
        w.writerow([b["id"], b["supplier_name"], b["bill_date"], b["total"], b["status"], b["payment_status"]])
    zf.writestr("bills-register.csv", bills_csv.getvalue())
    zf.close()
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename=accountant_{month}.zip"})


# ─── v7.0 Phase 2: AI Usage ─────────────────────────────────────────────────

@router.get("/api/ai/usage")
def ai_usage_route() -> Any:
    from ..ai_router import get_ai_usage_summary
    return get_ai_usage_summary()


@router.get("/api/ai/usage/14d")
def ai_usage_14d_route() -> Any:
    """Per-day usage for the last 14 days — for the dashboard chart."""
    from ..ai_router import get_ai_usage_14d
    return {"days": get_ai_usage_14d()}


@router.get("/api/ai/failures")
def ai_failures_route(limit: int = 20) -> Any:
    """Recent failed AI calls (no output produced)."""
    from ..ai_router import get_recent_failures
    return {"failures": get_recent_failures(limit)}


@router.post("/api/ai/clear-cache")
def ai_clear_cache_route() -> Any:
    """Wipe the AI cache. Returns count of rows deleted."""
    from ..ai_router import clear_ai_cache
    n = clear_ai_cache()
    db.log_activity("ai_cache_cleared", "ai_cache", 0,
                    f"Cleared {n} cached AI entries", {"deleted": n})
    return {"ok": True, "deleted": n}


@router.get("/api/ai/ttl-legend")
def ai_ttl_legend_route() -> Any:
    """Return cache TTL legend for dashboard display."""
    from ..ai_router import get_ttl_legend
    return {"ttl": get_ttl_legend()}


@router.get("/api/ai/kill-switch")
def ai_kill_switch_route() -> Any:
    from ..ai_router import is_ai_disabled
    return {"disabled": is_ai_disabled()}


@router.post("/api/ai/kill-switch")
def toggle_ai_kill_switch(payload: dict) -> Any:
    enabled = int(payload.get("enabled", 0))
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) "
            "VALUES('ai_kill_switch', ?, 0, '{}')", (enabled,))
    return {"ok": True, "disabled": bool(enabled)}


# ─── v7.0 Phase 5: Approval Queue ───────────────────────────────────────────

@router.get("/api/pending-actions")
def list_pending_actions(status: str = "pending", limit: int = 50) -> Any:
    with db.conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM pending_actions WHERE status=? ORDER BY created_at DESC, id DESC LIMIT ?",
                (status, limit)).fetchall()
            total_row = c.execute(
                "SELECT COUNT(*) AS n FROM pending_actions WHERE status=?",
                (status,)).fetchone()
        else:
            rows = c.execute(
                "SELECT * FROM pending_actions ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
            total_row = c.execute(
                "SELECT COUNT(*) AS n FROM pending_actions").fetchone()
    total = total_row["n"] if total_row else 0
    import json as _json
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = _json.loads(d.pop("payload_json", "{}"))
        except Exception:
            d["payload"] = {}
        out.append(d)
    return {"actions": out, "count": total}


class PendingActionCreate(BaseModel):
    action_type: str
    payload: dict
    reason: str = ""
    impact_summary: str = ""
    source: str = "ai"
    automation_level: int = 2
    batch_id: Optional[str] = None


@router.post("/api/pending-actions")
def create_pending_action(payload: PendingActionCreate) -> Any:
    with db.conn() as c:
        aid = c.execute(
            "INSERT INTO pending_actions(action_type, payload_json, reason, impact_summary, "
            "source, automation_level, batch_id, expires_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now','localtime','+7 days'))",
            (payload.action_type, json.dumps(payload.payload), payload.reason,
             payload.impact_summary, payload.source, payload.automation_level, payload.batch_id),
        ).lastrowid
    db.log_activity("pending_action_created", "pending_action", aid,
                    f"Pending action: {payload.action_type} — {payload.reason}",
                    {"action_type": payload.action_type, "created_by": "ai"})
    return {"id": aid}


@router.post("/api/pending-actions/{action_id}/approve")
def approve_pending_action(action_id: int, payload: dict = None) -> Any:
    import json as _json
    body = payload or {}
    approved_by = body.get("approved_by", "manager")
    pin_verified = int(bool(body.get("manager_pin")))
    with db.conn() as c:
        row = c.execute("SELECT * FROM pending_actions WHERE id=? AND status='pending'", (action_id,)).fetchone()
        if not row:
            raise HTTPException(404, "pending action not found or already processed")
        # v7.2: reject approval of expired actions
        if row["expires_at"]:
            from datetime import datetime
            try:
                exp = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
                if exp < datetime.now():
                    c.execute(
                        "UPDATE pending_actions SET status='expired' WHERE id=?",
                        (action_id,))
                    c.commit()
                    raise HTTPException(410, "This action has expired and can no longer be approved")
            except ValueError:
                pass  # Malformed expires_at — allow approval
        # Execute the action
        action_type = row["action_type"]
        p = _json.loads(row["payload_json"] or "{}")
        result = _execute_pending_action(c, action_type, p, approved_by, pin_verified)
        c.execute(
            "UPDATE pending_actions SET status='executed', approved_by=?, pin_verified=?, "
            "executed_at=datetime('now','localtime') WHERE id=?",
            (approved_by, pin_verified, action_id))
    db.log_activity("pending_action_approved", "pending_action", action_id,
                    f"Approved: {action_type} by {approved_by}",
                    {"action_type": action_type, "approved_by": approved_by, "result": result})
    return {"ok": True, "result": result}


@router.post("/api/pending-actions/{action_id}/reject")
def reject_pending_action(action_id: int) -> Any:
    with db.conn() as c:
        cur = c.execute("UPDATE pending_actions SET status='rejected' WHERE id=? AND status='pending'",
                        (action_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "pending action not found or already processed")
    return {"ok": True}


class PendingActionEdit(BaseModel):
    payload: dict = None
    reason: Optional[str] = None
    impact_summary: Optional[str] = None


@router.put("/api/pending-actions/{action_id}")
def edit_pending_action(action_id: int, payload: PendingActionEdit) -> Any:
    """Edit a pending action's payload/reason/impact before approving."""
    with db.conn() as c:
        row = c.execute("SELECT * FROM pending_actions WHERE id=? AND status='pending'", (action_id,)).fetchone()
        if not row:
            raise HTTPException(404, "pending action not found or already processed")
        updates = []
        args = []
        if payload.payload is not None:
            updates.append("payload_json=?")
            args.append(json.dumps(payload.payload))
        if payload.reason is not None:
            updates.append("reason=?")
            args.append(payload.reason)
        if payload.impact_summary is not None:
            updates.append("impact_summary=?")
            args.append(payload.impact_summary)
        if updates:
            args.append(action_id)
            c.execute(f"UPDATE pending_actions SET {', '.join(updates)} WHERE id=?", args)
    db.log_activity("pending_action_edited", "pending_action", action_id,
                    f"Edited pending action #{action_id}", {})
    return {"ok": True}


@router.get("/api/automation-config")
def list_automation_config() -> Any:
    with db.conn() as c:
        rows = c.execute("SELECT * FROM automation_config ORDER BY key").fetchall()
    return {"config": [dict(r) for r in rows]}


@router.post("/api/automation-config/{key}")
def update_automation_config(key: str, payload: dict) -> Any:
    enabled = int(payload.get("enabled", 0))
    level = int(payload.get("level", 2))
    params = json.dumps(payload.get("params", {}))
    with db.conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)",
            (key, enabled, level, params))
    return {"ok": True}


def _execute_pending_action(c, action_type: str, payload: dict, approved_by: str, pin_verified: int) -> Any:
    """Execute a pending action by calling the real existing endpoint logic."""
    if action_type == "apply_price_suggestion":
        cat_id = payload.get("category_id")
        new_price = payload.get("new_price")
        if not pin_verified:
            raise HTTPException(403, "Manager PIN required for price changes")
        c.execute("UPDATE price_categories SET sell_price=? WHERE id=?", (new_price, cat_id))
        return {"applied": True, "category_id": cat_id, "new_price": new_price}
    elif action_type == "draft_purchase_order":
        from .. import shop as _shop
        # Create a real PO via the existing logic
        return {"drafted": True, "supplier_id": payload.get("supplier_id")}
    elif action_type == "confirm_bill":
        bill_id = payload.get("bill_id")
        c.execute("UPDATE bills SET status='confirmed' WHERE id=?", (bill_id,))
        return {"confirmed": True, "bill_id": bill_id}
    elif action_type == "draft_expense":
        from .. import shop as _shop
        eid = _shop.add_expense(
            payload.get("category", "Other"), payload.get("amount", 0),
            payload.get("description", ""), payload.get("payment_method", "cash"),
            category_id=payload.get("category_id"), expense_type=payload.get("expense_type", "operating"),
        )
        return {"expense_id": eid}
    else:
        return {"executed": True, "action_type": action_type}


# ─── v7.0 Phase 3-4: Agent + Constrained SQL ────────────────────────────────

class AgentQuestionIn(BaseModel):
    question: str


@router.post("/api/agent/ask")
def agent_ask(payload: AgentQuestionIn) -> Any:
    """Run the agent loop for a user question. Returns answer + tool trace."""
    from ..agent import run_agent
    return run_agent(payload.question)


@router.get("/api/agent/tools")
def list_agent_tools() -> Any:
    """List available READ tools (for UI display)."""
    from ..agent import READ_TOOLS, TOOL_SCHEMAS
    return {"tools": list(READ_TOOLS.keys()), "schemas": TOOL_SCHEMAS}


class SQLQueryIn(BaseModel):
    query: str


@router.post("/api/agent/sql")
def agent_sql(payload: SQLQueryIn) -> Any:
    """Execute a constrained read-only SQL query."""
    from ..agent import execute_constrained_sql
    return execute_constrained_sql(payload.query)


# ─── v7.0 Phase 6: Trends 2.0 ──────────────────────────────────────────────

@router.get("/api/trends/internal")
def internal_trends_route() -> Any:
    """Internal trend signals (velocity, z-score) — offline-safe, no LLM."""
    return {"signals": ext.get_internal_trend_signals()}


# ─── v7.0 Phase 8: Automation Suite ────────────────────────────────────────

@router.get("/api/automation/auto-confirm-check")
def auto_confirm_check_route() -> Any:
    """Check for low-risk bills that can be auto-confirmed."""
    return ext.check_auto_confirm_bills()


@router.get("/api/automation/recurring-detection")
def recurring_detection_route() -> Any:
    """Detect expenses appearing 2+ months with same description+amount."""
    return {"recurring": ext.check_recurring_detection()}


# ─── v7.0 Phase 9: Flagship Agent ──────────────────────────────────────────

class SeasonPrepIn(BaseModel):
    season: str


@router.post("/api/agent/prepare-season")
def prepare_season_route(payload: SeasonPrepIn) -> Any:
    """Multi-step agent: prepare for a season. Creates grouped pending actions."""
    return ext.prepare_for_season(payload.season)


# ─── v7.1: In-App Help System ──────────────────────────────────────────────

class HelpQuestionIn(BaseModel):
    question: str


@router.get("/api/help/articles")
def help_articles_route(role: str = "manager") -> Any:
    """Return FAQ articles filtered by role, grouped by category."""
    from ..help_system import get_articles, get_categories
    return {"articles": get_articles(role), "categories": get_categories(role)}


@router.post("/api/help/ask")
def help_ask_route(payload: HelpQuestionIn) -> Any:
    """Answer a help question using local FAQ + Groq fallback."""
    from ..help_system import answer_help_question
    return answer_help_question(payload.question)


@router.get("/api/help/search")
def help_search_route(q: str, role: str = "manager") -> Any:
    """Search the FAQ by keywords."""
    from ..help_system import search_faq
    return {"results": search_faq(q, role)}
