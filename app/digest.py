"""
BillBook — Daily Sales Digest via Twilio WhatsApp
=================================================

Sends a single WhatsApp message at a configurable hour (default 9 PM PKT)
to the owner's phone, summarizing the day's sales:

    BillBook Daily — Branch A — 2026-08-26
    Total: Rs 145,000 (87 sales)
    Cash: Rs 98,000  Card: Rs 22,000  Online: Rs 15,000  Credit: Rs 10,000
    Top items:
      1. Basmati 5kg — 24 units (Rs 36,000)
      2. Sugar 1kg   — 18 units (Rs 1,800)
      3. Cooking Oil 5L — 9 units (Rs 13,500)
    Low stock alerts:
      • Tea 950g (3 left)
      • Flour 10kg (0 left — restock urgent)
    Cash drawer: Rs 4,500 (expected Rs 4,500 — match ✓)

DEPENDENCIES
------------
- twilio Python SDK:  pip install twilio

COST
----
Twilio WhatsApp Business API:
  - ~$0.0045 per message in Pakistan (after free 1k/month tier)
  - For 50 clients × 1 daily msg × 30 days = 1500 msgs/mo ≈ $7/month
  - Plus a flat $25/mo for a dedicated WhatsApp Business number
    (cheaper than a phone line, and supports 2-way chat for support)

SIGNUP
------
1. Create account at twilio.com
2. Enable WhatsApp Business API (sandbox works for free; production needs
   Meta Business verification, takes ~3 days)
3. Get a WhatsApp sender (e.g. whatsapp:+14155238886)
4. Set SID + Auth Token in Settings > Owner Digest > Twilio
5. Set the recipient phone (owner's WhatsApp, E.164 format)

The Twilio Auth Token is stored encrypted (Fernet) in settings.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Any

from . import crypto, db

logger = logging.getLogger(__name__)


# ─── Settings helpers ─────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return db.get_setting("digest_enabled", "0") == "1"


def get_config() -> dict:
    return {
        "enabled": is_enabled(),
        "hour": int(db.get_setting("digest_hour", "21")),
        "phone": db.get_setting("digest_phone", ""),
        "twilio_sid": db.get_setting("digest_twilio_sid", ""),
        "twilio_has_token": bool(db.get_setting("digest_twilio_token_enc", "")),
        "whatsapp_from": db.get_setting("digest_twilio_whatsapp_from", ""),
        "last_sent_at": db.get_setting("digest_last_sent_at", ""),
        "last_sent_ok": db.get_setting("digest_last_sent_ok", "") == "1",
        "last_error": db.get_setting("digest_last_error", ""),
    }


class DigestConfigIn:
    pass  # defined in routers below


def update_config(payload: dict) -> None:
    """Update digest settings. If twilio_token is provided, re-encrypt + store."""
    if "enabled" in payload:
        db.set_setting("digest_enabled", "1" if payload["enabled"] else "0")
    if "hour" in payload:
        # 0-23 hour, PKT
        h = int(payload["hour"])
        if 0 <= h <= 23:
            db.set_setting("digest_hour", str(h))
    if "phone" in payload:
        db.set_setting("digest_phone", str(payload["phone"]).strip())
    if "twilio_sid" in payload:
        db.set_setting("digest_twilio_sid", str(payload["twilio_sid"]).strip())
    if "twilio_token" in payload and payload["twilio_token"]:
        # Encrypt + persist (only if non-empty — empty = leave unchanged)
        enc = crypto.encrypt_value(str(payload["twilio_token"]))
        db.set_setting("digest_twilio_token_enc", enc)
    if "whatsapp_from" in payload:
        db.set_setting("digest_twilio_whatsapp_from", str(payload["whatsapp_from"]).strip())


# ─── Digest computation ───────────────────────────────────────────────────────

def build_digest_message(today_only: bool = True) -> str:
    """Compute the digest text from today's sales + cash drawer + low stock.

    Args:
        today_only: True for production daily digest. False for tests
            (uses yesterday's data so you can verify the message looks right
            without waiting until 9 PM).

    Returns a single multi-line WhatsApp-ready string. Gracefully handles
    fresh installs with no sales/categories yet — returns a minimal "no
    data yet" message instead of crashing.
    """
    target_date = (datetime.now() if today_only else datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    branch_name = db.get_setting("branch_name", "Branch")
    if not branch_name or branch_name == "Branch":
        branch_name = db.get_setting("shop_name", "BillBook")

    # Default to zeros — defensive against fresh installs with empty schemas
    n_sales = total = cash_total = card_total = online_total = credit_total = 0
    top_items: list = []
    low_stock: list = []
    sales_in = refunds_out = opening = closing_counted = 0.0

    try:
        with db.conn() as c:
            # Aggregate today's paid/credit/partial sales
            sales = c.execute(
                "SELECT COUNT(*) AS n, "
                "COALESCE(SUM(total), 0) AS total, "
                "COALESCE(SUM(CASE WHEN payment_method LIKE '%cash%' THEN total ELSE 0 END), 0) AS cash_total, "
                "COALESCE(SUM(CASE WHEN payment_method LIKE '%card%' THEN total ELSE 0 END), 0) AS card_total, "
                "COALESCE(SUM(CASE WHEN payment_method LIKE '%online%' OR payment_method LIKE '%raast%' "
                "  OR payment_method LIKE '%jazzcash%' OR payment_method LIKE '%easypaisa%' THEN total ELSE 0 END), 0) AS online_total, "
                "COALESCE(SUM(CASE WHEN payment_method LIKE '%credit%' THEN total ELSE 0 END), 0) AS credit_total "
                f"FROM sales WHERE date(created_at)='{target_date}' "
                f"AND payment_status IN ('paid','credit','partial') "
            ).fetchone()
            if sales:
                n_sales = sales["n"] or 0
                total = float(sales["total"] or 0)
                cash_total = float(sales["cash_total"] or 0)
                card_total = float(sales["card_total"] or 0)
                online_total = float(sales["online_total"] or 0)
                credit_total = float(sales["credit_total"] or 0)

            # Top 3 items by revenue today — guarded against missing columns
            try:
                top_items = c.execute(
                    "SELECT c.name AS category_name, "
                    "SUM(si.qty) AS qty, SUM(si.qty * si.unit_price) AS revenue "
                    "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
                    "LEFT JOIN categories c ON si.category_id = c.id "
                    f"WHERE date(s.created_at)='{target_date}' "
                    f"AND s.payment_status IN ('paid','credit','partial') "
                    "GROUP BY si.category_id ORDER BY revenue DESC LIMIT 3"
                ).fetchall()
            except Exception:
                top_items = []

            # Low stock alerts — guarded against missing tables
            try:
                low_stock = c.execute(
                    "SELECT c.name, s.current_qty, s.reorder_point FROM category_stock_state s "
                    "JOIN categories c ON s.category_id = c.id "
                    "WHERE (s.current_qty < COALESCE(s.reorder_point, 5) OR s.current_qty < 5) "
                    "ORDER BY s.current_qty ASC LIMIT 5"
                ).fetchall()
            except Exception:
                low_stock = []

            # Cash drawer — guarded against missing tables/columns
            try:
                cd = c.execute(
                    "SELECT "
                    "COALESCE((SELECT SUM(amount) FROM cash_drawer "
                    f"  WHERE date(created_at)='{target_date}' AND type='sale'), 0) AS sales_in, "
                    "COALESCE((SELECT SUM(amount) FROM cash_drawer "
                    f"  WHERE date(created_at)='{target_date}' AND type='refund'), 0) AS refunds_out, "
                    "COALESCE((SELECT opening_float FROM cash_drawer_sessions "
                    f"  WHERE date(opened_at)='{target_date}' ORDER BY opened_at DESC LIMIT 1), 0) AS opening, "
                    "COALESCE((SELECT closing_counted FROM cash_drawer_sessions "
                    f"  WHERE date(opened_at)='{target_date}' AND closed_at IS NOT NULL "
                    f"  ORDER BY closed_at DESC LIMIT 1), 0) AS closing_counted"
                ).fetchone()
                if cd:
                    # v8.14.1 FIX: previous version probed Row keys with
                    # `if "sales_in" in cd.keys()` — works on sqlite3.Row but
                    # silently returns False if cd is a tuple or a plain dict.
                    # Use cd[key] defensively with a real None-default.
                    sales_in = float(cd["sales_in"]) if cd["sales_in"] is not None else 0.0
                    refunds_out = float(cd["refunds_out"]) if cd["refunds_out"] is not None else 0.0
                    opening = float(cd["opening"]) if cd["opening"] is not None else 0.0
                    closing_counted = float(cd["closing_counted"]) if cd["closing_counted"] is not None else 0.0
            except Exception:
                pass
    except Exception as e:
        logger.warning("Digest compute failed: %s — sending fallback", e)

    expected = opening + sales_in - refunds_out
    drawer_match = abs(expected - closing_counted) < 1

    # Compose the WhatsApp message — keep under 1024 chars (Twilio limit)
    lines = []
    lines.append(f"*BillBook Daily — {branch_name}*")
    lines.append(f"Date: {target_date}")
    lines.append("")
    lines.append(f"Total: Rs {total:,.0f} ({n_sales} sales)")
    lines.append(f"Cash Rs {cash_total:,.0f} | Card Rs {card_total:,.0f} | "
                 f"Online Rs {online_total:,.0f} | Credit Rs {credit_total:,.0f}")
    if top_items:
        lines.append("")
        lines.append("*Top items:*")
        for i, it in enumerate(top_items, 1):
            lines.append(f"  {i}. {it['category_name'] or 'Unknown'} — "
                         f"{float(it['qty'] or 0):.0f} units (Rs {float(it['revenue'] or 0):,.0f})")
    if low_stock:
        lines.append("")
        lines.append("*Low stock:*")
        for ls in low_stock:
            qty = float(ls["current_qty"] or 0)
            tag = " — restock urgent" if qty <= 0 else ""
            lines.append(f"  • {ls['name']} ({qty:.0f} left{tag})")
    if closing_counted:
        lines.append("")
        match_str = "match ✓" if drawer_match else "MISMATCH — investigate"
        lines.append(f"Cash drawer: Rs {closing_counted:,.0f} counted "
                     f"(expected Rs {expected:,.0f} — {match_str})")
    return "\n".join(lines)


# ─── Twilio send ──────────────────────────────────────────────────────────────

def _send_via_twilio(to_phone: str, body: str) -> dict:
    """Send a WhatsApp message via Twilio API. Returns {ok, error, sid}."""
    sid = db.get_setting("digest_twilio_sid", "").strip()
    enc = db.get_setting("digest_twilio_token_enc", "")
    from_ = db.get_setting("digest_twilio_whatsapp_from", "").strip()
    if not sid or not enc or not from_:
        return {"ok": False, "error": "Twilio not configured (SID / token / from missing)"}
    try:
        token = crypto.decrypt_value(enc)
    except Exception as e:
        return {"ok": False, "error": f"Twilio token decryption failed: {e}"}

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        msg = client.messages.create(
            from_=from_,
            body=body,
            to=f"whatsapp:{to_phone}" if not to_phone.startswith("whatsapp:") else to_phone,
        )
        return {"ok": True, "sid": msg.sid, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "sid": None}


# ─── Public send + scheduled entrypoint ──────────────────────────────────────

def send_daily_digest(force: bool = False) -> dict:
    """Compute today's digest + send via Twilio WhatsApp.

    Args:
        force: True to bypass the enabled-flag check (for the 'Test send'
            button in the Settings UI).
    """
    if not force and not is_enabled():
        return {"ok": False, "error": "Digest disabled — enable in Settings > Owner Digest"}
    phone = db.get_setting("digest_phone", "").strip()
    if not phone:
        return {"ok": False, "error": "Owner phone not set — enter in Settings > Owner Digest"}

    body = build_digest_message(today_only=True)
    result = _send_via_twilio(phone, body)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.set_setting("digest_last_sent_at", now_str)
    db.set_setting("digest_last_sent_ok", "1" if result.get("ok") else "0")
    db.set_setting("digest_last_error", result.get("error", ""))
    if result.get("ok"):
        db.log_activity("digest_sent", "comms", None,
                        f"Daily digest sent to {phone}",
                        {"sid": result.get("sid")})
    else:
        db.log_activity("digest_failed", "comms", None,
                        f"Digest send failed: {result.get('error')}", {})
    return result
