"""v7.2 Phase 1 — Trends + automation + season prep (extensions split 2/3).

Trends 2.0 internal engine, automation suite, flagship season-prep agent.
Extracted from extensions.py (~190 lines).
"""
import json, uuid, logging
from datetime import datetime, timedelta
from .db import conn
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant

logger = logging.getLogger(__name__)


# ─── Trends 2.0 Internal Engine ────────────────────────────────────────────

def get_internal_trend_signals() -> list:
    """Per-category velocity (7d vs prior 7d), spike z-score vs 28-day baseline."""
    now = datetime.now()
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    d14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    d28 = (now - timedelta(days=28)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    signals = []
    with conn() as c:
        cats = c.execute("SELECT id, code, name FROM price_categories WHERE active=1").fetchall()
        for cat in cats:
            cid = cat["id"]
            r7 = c.execute(
                "SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items si JOIN sales s ON si.sale_id=s.id "
                f"WHERE si.category_id=? AND {db.VALID_SALE_FILTER} AND date(s.created_at)>=? AND date(s.created_at)<=?",
                (cid, d7, today)).fetchone()["q"]
            r7p = c.execute(
                "SELECT COALESCE(SUM(qty), 0) AS q FROM sale_items si JOIN sales s ON si.sale_id=s.id "
                f"WHERE si.category_id=? AND {db.VALID_SALE_FILTER} AND date(s.created_at)>=? AND date(s.created_at)<?",
                (cid, d14, d7)).fetchone()["q"]
            daily_rows = c.execute(
                "SELECT date(s.created_at) AS d, SUM(si.qty) AS q FROM sale_items si JOIN sales s ON si.sale_id=s.id "
                f"WHERE si.category_id=? AND {db.VALID_SALE_FILTER} AND date(s.created_at)>=? AND date(s.created_at)<=? "
                "GROUP BY date(s.created_at)", (cid, d28, today)).fetchall()
            if not daily_rows: continue
            daily_qtys = [float(r["q"] or 0) for r in daily_rows]
            avg_28 = sum(daily_qtys) / len(daily_qtys) if daily_qtys else 0
            std_28 = (sum((x - avg_28) ** 2 for x in daily_qtys) / len(daily_qtys)) ** 0.5 if daily_qtys else 0
            today_qty = daily_qtys[-1] if daily_qtys else 0
            z_score = (today_qty - avg_28) / std_28 if std_28 > 0 else 0
            velocity_pct = ((r7 - r7p) / r7p * 100) if r7p > 0 else (100 if r7 > 0 else 0)
            if abs(z_score) > 1.5 or abs(velocity_pct) > 30:
                signals.append({
                    "category_id": cid, "code": cat["code"], "name": cat["name"],
                    "velocity_7d": int(r7), "velocity_prior_7d": int(r7p),
                    "velocity_pct": round(velocity_pct, 1), "today_qty": int(today_qty),
                    "avg_28d": round(avg_28, 1), "z_score": round(z_score, 2),
                    "type": "spike" if z_score > 1.5 else ("drop" if z_score < -1.5 else "velocity"),
                    "source": "internal",
                })
    return signals


# ─── Automation Suite ──────────────────────────────────────────────────────

def check_auto_confirm_bills() -> dict:
    """Check for low-risk bills that can be auto-confirmed (Level 3, bounded)."""
    with conn() as c:
        rows = c.execute(
            "SELECT b.id, b.supplier_name, b.written_total, b.computed_total, b.flags "
            "FROM bills b WHERE b.status='review' AND b.deleted_at IS NULL").fetchall()
    auto_confirmed = []; pending = []
    for b in rows:
        flags = b["flags"] or "[]"
        total_match = abs(float(b["written_total"] or 0) - float(b["computed_total"] or 0)) < 1
        no_flags = flags == "[]" or flags == ""
        if no_flags and total_match:
            auto_confirmed.append({"bill_id": b["id"], "supplier": b["supplier_name"]})
        else:
            pending.append({"bill_id": b["id"], "supplier": b["supplier_name"], "reason": "Has flags or total mismatch"})
    return {"auto_confirmed": auto_confirmed, "pending": pending}


def check_recurring_detection() -> list:
    """Detect expenses that appear 2+ months with same description+amount."""
    with conn() as c:
        rows = c.execute(
            "SELECT description, amount, COUNT(DISTINCT strftime('%Y-%m', date)) AS months "
            "FROM expenses WHERE description IS NOT NULL AND description != '' "
            "GROUP BY description, amount HAVING months >= 2 ORDER BY months DESC").fetchall()
    return [{"description": r["description"], "amount": float(r["amount"]), "months": r["months"]} for r in rows]


# ─── Flagship Season-Prep Agent ────────────────────────────────────────────

def prepare_for_season(season_name: str) -> dict:
    """Multi-step agent: prepare for a season. Creates grouped pending actions.

    v8.4: Deduplicates — if there are already pending season-prep actions for
    the same season_name, returns them instead of creating duplicates.
    """
    # v8.4: Check for existing pending actions for this season
    with conn() as c:
        existing = c.execute(
            "SELECT id, action_type, reason, impact_summary, batch_id, status "
            "FROM pending_actions WHERE source='ai_season_prep' AND status='pending' "
            "AND reason LIKE ?",
            (f"%{season_name}%",)
        ).fetchall()
    if existing:
        return {
            "batch_id": existing[0]["batch_id"],
            "pending_count": len(existing),
            "summary": f"Season prep for {season_name} already has {len(existing)} pending actions (use the Approval Queue to review them).",
            "actions": [dict(r) for r in existing],
            "already_prepared": True,
        }

    batch_id = str(uuid.uuid4())[:8]
    pending = []
    from . import shop
    inv = shop.get_inventory()
    low_stock = [i for i in inv if i.get("low_stock") or i.get("stock", 0) < 10]
    for item in low_stock[:3]:
        pending.append({
            "action_type": "draft_purchase_order",
            "payload": {"category_id": item["category_id"], "qty": 50},
            "reason": f"Low stock for {season_name}: {item.get('code', '?')} has {item.get('stock', 0)} pcs",
            "impact_summary": f"Restock {item.get('code', '?')} — est. cost Rs {50 * item.get('avg_cost', 0):,.0f}",
            "source": "ai_season_prep", "batch_id": batch_id,
        })
    pending.append({
        "action_type": "happy_hour_rule",
        "payload": {"pct": 10, "start_hhmm": "0800", "end_hhmm": "1000"},
        "reason": f"Morning happy-hour to boost {season_name} sales",
        "impact_summary": "10% discount 8-10 AM — est. revenue impact +15% volume",
        "source": "ai_season_prep", "batch_id": batch_id,
    })
    pending.append({
        "action_type": "customer_broadcast",
        "payload": {"group": "retail", "message": f"Visit us for {season_name} specials!"},
        "reason": f"Broadcast {season_name} promotions to retail customers",
        "impact_summary": "WhatsApp broadcast to all retail customers",
        "source": "ai_season_prep", "batch_id": batch_id,
    })
    with conn() as c:
        for pa in pending:
            c.execute(
                "INSERT INTO pending_actions(action_type, payload_json, reason, impact_summary, "
                "source, automation_level, batch_id, expires_at) "
                "VALUES(?,?,?,?,?,2,?,datetime('now','localtime','+7 days'))",
                (pa["action_type"], json.dumps(pa["payload"]), pa["reason"],
                 pa["impact_summary"], pa["source"], pa["batch_id"]))
    return {"batch_id": batch_id, "pending_count": len(pending),
            "summary": f"Prepared {len(pending)} actions for {season_name}", "actions": pending}


# ─── Closed Days + Seasons ─────────────────────────────────────────────────

def list_closed_days() -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM closed_days ORDER BY date").fetchall()
    return [dict(r) for r in rows]


def add_closed_day(date: str, label: str = "") -> bool:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO closed_days(date, label) VALUES(?,?)", (date, label))
    return True


def remove_closed_day(date: str) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM closed_days WHERE date=?", (date,)); return cur.rowcount > 0


def list_seasons() -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM seasons ORDER BY year, start").fetchall()
    return [dict(r) for r in rows]


def add_season(year: int, name: str, start: str, end: str) -> int:
    with conn() as c:
        return c.execute("INSERT INTO seasons(year, name, start, end) VALUES(?,?,?,?)", (year, name, start, end)).lastrowid
