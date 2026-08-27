"""v7.2 Phase 1 — WhatsApp suite + Raast reconciliation (extensions split 3/3).

Urdhaar escalation, customer groups, broadcast, WhatsApp parse, Raast.
Extracted from extensions.py (~110 lines).
"""
import re, urllib.parse
from datetime import datetime
from .db import conn
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant


# ─── Urdhaar Escalation Ladder ─────────────────────────────────────────────

def get_urdhaar_reminders() -> list:
    """Customers with outstanding credit, categorized by days overdue."""
    with conn() as c:
        rows = c.execute(
            "SELECT cu.id, cu.name, cu.phone, cu.total_credit, "
            "MAX(s.created_at) AS last_sale_date "
            "FROM customers cu LEFT JOIN sales s ON s.customer_id=cu.id "
            "WHERE cu.total_credit > 0 "
            "GROUP BY cu.id ORDER BY cu.total_credit DESC").fetchall()
    now = datetime.now()
    reminders = []
    for r in rows:
        days_overdue = 0
        if r["last_sale_date"]:
            try:
                last = datetime.strptime(r["last_sale_date"][:19], "%Y-%m-%d %H:%M:%S")
                days_overdue = (now - last).days
            except Exception: pass
        if days_overdue >= 30:
            stage = 3; tone = "urgent"
            text = f"Dear {r['name']}, your outstanding balance of Rs {r['total_credit']:,.0f} is now {days_overdue} days overdue. Please settle immediately."
        elif days_overdue >= 15:
            stage = 2; tone = "firm"
            text = f"Dear {r['name']}, this is a reminder that Rs {r['total_credit']:,.0f} is outstanding ({days_overdue} days). Please arrange payment this week."
        elif days_overdue >= 7:
            stage = 1; tone = "gentle"
            text = f"Dear {r['name']}, just a friendly reminder that you have Rs {r['total_credit']:,.0f} outstanding. Please settle at your earliest convenience."
        else: continue
        phone_clean = (r["phone"] or "").replace(" ", "")
        wa_link = f"https://wa.me/{phone_clean}?text=" + urllib.parse.quote(text)
        reminders.append({"customer_id": r["id"], "name": r["name"], "phone": r["phone"],
                          "amount": float(r["total_credit"] or 0), "days_overdue": days_overdue,
                          "stage": stage, "tone": tone, "text": text, "wa_link": wa_link})
    return reminders


# ─── Customer Groups + Broadcast ───────────────────────────────────────────

def get_customer_groups() -> list:
    with conn() as c:
        rows = c.execute("SELECT group_name, COUNT(*) AS n FROM customers GROUP BY group_name ORDER BY n DESC").fetchall()
    return [dict(r) for r in rows]


def get_broadcast_list(group: str = "retail") -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT name, phone FROM customers WHERE group_name=? AND phone IS NOT NULL AND phone != ''", (group,)).fetchall()
    phones = [r["phone"] for r in rows if r["phone"]]
    return {"group": group, "count": len(phones), "phones": phones, "phones_csv": ", ".join(phones)}


# ─── WhatsApp Order Parsing ────────────────────────────────────────────────

def parse_whatsapp_order(text: str) -> dict:
    """Parse a WhatsApp order message like '5 A, 3 C' against shop categories."""
    with conn() as c:
        cats = {r["code"].upper(): {"id": r["id"], "code": r["code"], "name": r["name"],
                "sell_price": float(r["sell_price"] or 0)}
                for r in c.execute("SELECT * FROM price_categories WHERE active=1").fetchall() if r["code"]}
    items = []
    for match in re.finditer(r'(\d+)\s*(?:pcs?\s*)?([A-Za-z]+)', text):
        qty = int(match.group(1)); code = match.group(2).upper()
        if code in cats:
            cat = cats[code]
            items.append({"category_id": cat["id"], "code": cat["code"], "name": cat["name"], "qty": qty,
                          "sell_price": cat["sell_price"], "line_total": round(qty * cat["sell_price"], 2)})
    total = sum(i["line_total"] for i in items)
    return {"items": items, "total": round(total, 2), "item_count": len(items), "raw_text": text}


# ─── Raast Reconciliation ──────────────────────────────────────────────────

def get_raast_reconciliation() -> dict:
    """List sales with digital payment methods vs bank_transactions."""
    digital_methods = ("raast", "easypaisa", "jazzcash", "online")
    with conn() as c:
        sales = c.execute(
            "SELECT s.id, s.invoice_no, s.total, s.payment_method, s.created_at, s.split_online "
            f"FROM sales s WHERE {db.VALID_SALE_FILTER} "
            f"AND (s.payment_method IN ({','.join('?'*len(digital_methods))}) OR s.split_online > 0) "
            "ORDER BY s.id DESC LIMIT 100", digital_methods).fetchall()
        bank_txs = c.execute(
            "SELECT bt.*, ba.name AS account_name FROM bank_transactions bt "
            "LEFT JOIN bank_accounts ba ON bt.account_id=ba.id ORDER BY bt.id DESC LIMIT 100").fetchall()
    matched = []; unmatched_sales = []; used_tx_ids = set()
    for s in sales:
        sale_amount = float(s["split_online"] or 0) if s["split_online"] and float(s["split_online"] or 0) > 0 else float(s["total"] or 0)
        match = None
        for bt in bank_txs:
            if bt["id"] in used_tx_ids: continue
            if abs(float(bt["amount"] or 0) - sale_amount) < 1.0:
                match = bt; used_tx_ids.add(bt["id"]); break
        if match: matched.append({"sale": dict(s), "bank_tx": dict(match)})
        else: unmatched_sales.append(dict(s))
    unmatched_txs = [dict(bt) for bt in bank_txs if bt["id"] not in used_tx_ids]
    return {"matched": matched, "unmatched_sales": unmatched_sales, "unmatched_bank_txs": unmatched_txs}
