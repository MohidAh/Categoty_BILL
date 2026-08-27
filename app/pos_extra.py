"""Extra POS-related features: Purchase Orders, SMS, CSV import, financial reports, barcodes."""
import csv
import io
import json
from datetime import datetime, timedelta
from urllib.parse import quote
from .db import conn, get_setting, set_setting
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant


# ---------- Tax / GST ----------

def get_tax_rate() -> float:
    """Get the default sales tax rate (e.g., 0.17 for 17% GST)."""
    try:
        return float(get_setting("tax_rate", "0"))
    except Exception:
        return 0.0


def set_tax_rate(rate: float):
    set_setting("tax_rate", str(rate))


def get_tax_inclusive() -> bool:
    """Whether prices already include tax (True) or tax is added on top (False)."""
    return get_setting("tax_inclusive", "0") == "1"


def set_tax_inclusive(inclusive: bool):
    set_setting("tax_inclusive", "1" if inclusive else "0")


def compute_tax(subtotal: float, discount: float, loyalty_discount: float = 0) -> dict:
    """Compute tax for a sale. Returns {rate, taxable, tax_amount}."""
    rate = get_tax_rate()
    taxable = max(0, subtotal - discount - loyalty_discount)
    tax_amount = round(taxable * rate, 2)
    return {"rate": rate, "taxable": round(taxable, 2), "tax_amount": tax_amount}


# ---------- Purchase Orders ----------

def create_po(supplier_id: int, supplier_name: str, items: list,
              notes: str = "", expected_date: str = "") -> dict:
    """Create a purchase order. items: [{item_name, qty, est_price, notes}]"""
    with conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM purchase_orders").fetchone()["n"]
        po_no = f"PO-{datetime.now().strftime('%Y%m%d')}-{n + 1:03d}"
        total = sum(i.get("qty", 0) * i.get("est_price", 0) for i in items)
        pid = c.execute(
            "INSERT INTO purchase_orders(po_no, supplier_id, supplier_name, status, total, notes, expected_date) "
            "VALUES(?,?,?,?,?,?,?)",
            (po_no, supplier_id, supplier_name, "draft", total, notes, expected_date),
        ).lastrowid
        for item in items:
            line_total = item.get("qty", 0) * item.get("est_price", 0)
            c.execute(
                "INSERT INTO purchase_order_items(po_id, item_name, qty, est_price, line_total, notes) "
                "VALUES(?,?,?,?,?,?)",
                (pid, item.get("item_name", ""), item.get("qty", 1),
                 item.get("est_price", 0), line_total, item.get("notes", "")),
            )
    return {"id": pid, "po_no": po_no, "total": total}


def list_pos(status: str = "") -> list:
    with conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM purchase_orders WHERE status=? ORDER BY id DESC LIMIT 100",
                (status,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM purchase_orders ORDER BY id DESC LIMIT 100"
            ).fetchall()
    return [dict(r) for r in rows]


def get_po(po_id: int) -> dict | None:
    with conn() as c:
        po = c.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
        if not po:
            return None
        items = c.execute(
            "SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id",
            (po_id,),
        ).fetchall()
    return {**dict(po), "items": [dict(i) for i in items]}


def update_po_status(po_id: int, status: str, sent_via: str = ""):
    with conn() as c:
        if sent_via:
            c.execute("UPDATE purchase_orders SET status=?, sent_via=? WHERE id=?",
                      (status, sent_via, po_id))
        else:
            c.execute("UPDATE purchase_orders SET status=? WHERE id=?",
                      (status, po_id))


def delete_po(po_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM purchase_orders WHERE id=?", (po_id,))
    return cur.rowcount > 0


def po_to_whatsapp(po_id: int) -> dict:
    """Generate a WhatsApp message link for sending the PO to the supplier."""
    po = get_po(po_id)
    if not po:
        return {"error": "PO not found"}
    # Find supplier phone
    phone = ""
    if po["supplier_id"]:
        with conn() as c:
            sup = c.execute("SELECT phone FROM suppliers WHERE id=?", (po["supplier_id"],)).fetchone()
            if sup:
                phone = sup["phone"] or ""
    msg = f"BillBook Purchase Order\nPO #: {po['po_no']}\n"
    msg += f"Supplier: {po['supplier_name'] or '—'}\n"
    if po.get("expected_date"):
        msg += f"Expected by: {po['expected_date']}\n"
    msg += "\nItems:\n"
    for i in po["items"]:
        msg += f"• {i['item_name']} — qty {i['qty']} × Rs {i['est_price']:.0f} = Rs {i['line_total']:.0f}\n"
    msg += f"\nTotal: Rs {po['total']:.0f}\n"
    if po.get("notes"):
        msg += f"Notes: {po['notes']}\n"
    msg += "\nPlease confirm availability and delivery date."
    phone_clean = "".join(ch for ch in phone if ch.isdigit())
    if phone_clean.startswith("03"):
        phone_clean = "92" + phone_clean[1:]
    url = f"https://wa.me/{phone_clean}?text={quote(msg)}" if phone_clean else None
    return {"url": url, "message": msg, "phone": phone_clean}


# ---------- SMS (via Twilio) ----------

def get_sms_config() -> dict:
    return {
        "enabled": get_setting("sms_enabled", "0") == "1",
        "provider": get_setting("sms_provider", ""),
        "account_sid": get_setting("sms_account_sid", ""),
        "auth_token": get_setting("sms_auth_token", ""),
        "from_number": get_setting("sms_from_number", ""),
    }


def set_sms_config(config: dict):
    set_setting("sms_enabled", "1" if config.get("enabled") else "0")
    if "provider" in config:
        set_setting("sms_provider", config["provider"])
    if "account_sid" in config:
        set_setting("sms_account_sid", config["account_sid"])
    if "auth_token" in config:
        set_setting("sms_auth_token", config["auth_token"])
    if "from_number" in config:
        set_setting("sms_from_number", config["from_number"])


def send_sms(to: str, body: str) -> dict:
    """Send an SMS via Twilio. Returns {success, error}."""
    cfg = get_sms_config()
    if not cfg["enabled"]:
        return {"success": False, "error": "SMS not enabled in settings"}
    if not cfg["account_sid"] or not cfg["auth_token"] or not cfg["from_number"]:
        return {"success": False, "error": "Twilio credentials not configured"}
    # Clean phone
    to_clean = "".join(ch for ch in to if ch.isdigit())
    if to_clean.startswith("03"):
        to_clean = "92" + to_clean[1:]
    if not to_clean:
        return {"success": False, "error": "Invalid recipient phone"}
    try:
        import httpx
        url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json"
        r = httpx.post(
            url,
            data={"From": cfg["from_number"], "To": "+" + to_clean, "Body": body},
            auth=(cfg["account_sid"], cfg["auth_token"]),
            timeout=15.0,
        )
        if r.status_code in (200, 201):
            return {"success": True, "sid": r.json().get("sid")}
        return {"success": False, "error": f"Twilio error {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_sale_sms(sale_id: int) -> dict:
    """Send a receipt summary via SMS for a sale."""
    with conn() as c:
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            return {"success": False, "error": "Sale not found"}
    if not sale["customer_phone"]:
        return {"success": False, "error": "No customer phone on sale"}
    msg = (
        f"BillBook Receipt\n"
        f"Invoice: {sale['invoice_no']}\n"
        f"Total: Rs {sale['total']:.0f}\n"
        f"Payment: {sale['payment_method']}\n"
        f"Date: {sale['created_at']}\n"
        f"Thank you for shopping!"
    )
    return send_sms(sale["customer_phone"], msg)


# ---------- CSV/Excel Import ----------

def import_customers_csv(csv_text: str) -> dict:
    """Import customers from CSV. Columns: name, phone, address (optional).
    Returns {imported, skipped, errors}."""
    imported = 0
    skipped = 0
    errors = []
    reader = csv.DictReader(io.StringIO(csv_text))
    with conn() as c:
        for i, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            phone = (row.get("phone") or "").strip()
            address = (row.get("address") or "").strip()
            if not name:
                errors.append(f"Row {i}: missing name")
                continue
            # Check if exists
            if phone:
                existing = c.execute(
                    "SELECT id FROM customers WHERE phone=?", (phone,)
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
            try:
                c.execute(
                    "INSERT INTO customers(name, phone, address) VALUES(?,?,?)",
                    (name, phone, address),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_suppliers_csv(csv_text: str) -> dict:
    """Import suppliers from CSV. Columns: name, phone, address, notes (optional)."""
    imported = 0
    skipped = 0
    errors = []
    reader = csv.DictReader(io.StringIO(csv_text))
    with conn() as c:
        for i, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            phone = (row.get("phone") or "").strip()
            address = (row.get("address") or "").strip()
            notes = (row.get("notes") or "").strip()
            if not name:
                errors.append(f"Row {i}: missing name")
                continue
            if phone:
                existing = c.execute(
                    "SELECT id FROM suppliers WHERE phone=?", (phone,)
                ).fetchone()
                if existing:
                    skipped += 1
                    continue
            try:
                c.execute(
                    "INSERT INTO suppliers(name, phone, address, notes) VALUES(?,?,?,?)",
                    (name, phone, address, notes),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: {e}")
    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_price_categories_csv(csv_text: str) -> dict:
    """Import price categories from CSV. Columns: name, sell_price, color (optional)."""
    imported = 0
    errors = []
    reader = csv.DictReader(io.StringIO(csv_text))
    with conn() as c:
        for i, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            price_str = (row.get("sell_price") or "").strip()
            color = (row.get("color") or "#10b981").strip()
            if not name or not price_str:
                errors.append(f"Row {i}: missing name or sell_price")
                continue
            try:
                price = float(price_str)
                c.execute(
                    "INSERT INTO price_categories(name, sell_price, color) VALUES(?,?,?)",
                    (name, price, color),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: {e}")
    return {"imported": imported, "errors": errors}


# ---------- Financial Reports ----------

def get_cash_flow(month: str = "") -> dict:
    """Monthly cash flow statement: cash in vs cash out by category."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with conn() as c:
        # Cash inflows
        cash_sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) v FROM sales "
            "WHERE strftime('%Y-%m', created_at)=? AND payment_method='cash' "
            f"AND {db.VALID_SALE_FILTER_NO_ALIAS}",
            (month,),
        ).fetchone()["v"]
        split_cash = c.execute(
            "SELECT COALESCE(SUM(split_cash), 0) v FROM sales "
            "WHERE strftime('%Y-%m', created_at)=? AND payment_method='split' "
            f"AND {db.VALID_SALE_FILTER_NO_ALIAS}",
            (month,),
        ).fetchone()["v"]
        card_sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) v FROM sales "
            "WHERE strftime('%Y-%m', created_at)=? AND payment_method='card' "
            f"AND {db.VALID_SALE_FILTER_NO_ALIAS}",
            (month,),
        ).fetchone()["v"]
        online_sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) v FROM sales "
            "WHERE strftime('%Y-%m', created_at)=? AND payment_method='online' "
            f"AND {db.VALID_SALE_FILTER_NO_ALIAS}",
            (month,),
        ).fetchone()["v"]
        customer_payments = c.execute(
            "SELECT COALESCE(SUM(amount), 0) v FROM customer_payments "
            "WHERE strftime('%Y-%m', created_at)=? AND payment_method='cash'",
            (month,),
        ).fetchone()["v"]
        # Cash outflows
        cash_expenses = c.execute(
            "SELECT COALESCE(SUM(amount), 0) v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND payment_method='cash'",
            (month,),
        ).fetchone()["v"]
        cash_drawer_out = c.execute(
            "SELECT COALESCE(SUM(ABS(amount)), 0) v FROM cash_drawer "
            "WHERE type='cash_out' AND strftime('%Y-%m', created_at)=?",
            (month,),
        ).fetchone()["v"]
        # Bill payments (purchases) — assume paid bills are cash outflow
        purchases = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v FROM bills "
            "WHERE status='confirmed' AND deleted_at IS NULL "
            "AND payment_status='paid' AND strftime('%Y-%m', bill_date)=?",
            (month,),
        ).fetchone()["v"]
    # v8.5.5: total_in now includes ALL payment methods (cash + card + online).
    # The old code only counted cash sales as "Total Cash In" which was
    # confusing — users expected the total to match their total sales.
    total_in = cash_sales + split_cash + card_sales + online_sales + customer_payments
    total_out = cash_expenses + cash_drawer_out
    net_cash = total_in - total_out
    return {
        "month": month,
        "inflows": {
            "cash_sales": round(cash_sales, 2),
            "split_cash": round(split_cash, 2),
            "card_sales": round(card_sales, 2),
            "online_sales": round(online_sales, 2),
            "customer_payments": round(customer_payments, 2),
            "total_in": round(total_in, 2),
        },
        "outflows": {
            "cash_expenses": round(cash_expenses, 2),
            "cash_drawer_out": round(cash_drawer_out, 2),
            "purchases": round(purchases, 2),
            "total_out": round(total_out, 2),
        },
        "net_cash": round(net_cash, 2),
    }


def get_balance_sheet(as_of: str = "") -> dict:
    """Simple balance sheet as of a date: assets, liabilities, equity."""
    if not as_of:
        as_of = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        # Assets: cash on hand (drawer balance), inventory value, outstanding credit to us
        # Cash on hand = sum of all cash_drawer entries up to as_of
        cash = c.execute(
            "SELECT COALESCE(SUM(amount), 0) v FROM cash_drawer WHERE date(created_at) <= ?",
            (as_of,),
        ).fetchone()["v"]
        # Inventory value (stock × avg cost)
        inv = c.execute(
            "SELECT bi.category_id, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS purchased, "
            "AVG(bi.price) AS avg_cost, "
            "pc.sell_price "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id IS NOT NULL "
            "AND date(COALESCE(b.bill_date, date(b.created_at))) <= ? "
            "GROUP BY bi.category_id",
            (as_of,),
        ).fetchall()
        # v8.9.1: filter refunded sales + use JOIN for date filter
        from .db import VALID_SALE_FILTER
        sold_rows = c.execute(
            "SELECT si.category_id, SUM(si.qty) AS sold "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE si.category_id IS NOT NULL "
            "AND date(s.created_at) <= ? "
            "AND " + VALID_SALE_FILTER + " "
            "GROUP BY si.category_id",
            (as_of,),
        ).fetchall()
        sold_map = {r["category_id"]: r["sold"] or 0 for r in sold_rows}
        inventory_value = 0
        inventory_potential = 0
        for r in inv:
            stock = (r["purchased"] or 0) - sold_map.get(r["category_id"], 0)
            inventory_value += stock * (r["avg_cost"] or 0)
            inventory_potential += stock * (r["sell_price"] or 0)
        # Receivables: outstanding credit (sales on credit not yet paid)
        receivables = c.execute(
            "SELECT COALESCE(SUM(total), 0) v FROM sales "
            "WHERE payment_status IN ('credit', 'partial') AND date(created_at) <= ?",
            (as_of,),
        ).fetchone()["v"]
        # Less: customer_payments received
        payments_received = c.execute(
            "SELECT COALESCE(SUM(amount), 0) v FROM customer_payments "
            "WHERE date(created_at) <= ?",
            (as_of,),
        ).fetchone()["v"]
        net_receivables = max(0, receivables - payments_received)
        # Liabilities: payables (bills on credit not yet paid)
        payables = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) v FROM bills "
            "WHERE status='confirmed' AND deleted_at IS NULL "
            "AND payment_status='credit' AND date(bill_date) <= ?",
            (as_of,),
        ).fetchone()["v"]
    total_assets = cash + inventory_value + net_receivables
    total_liabilities = payables
    equity = total_assets - total_liabilities
    return {
        "as_of": as_of,
        "assets": {
            "cash_on_hand": round(cash, 2),
            "inventory_value": round(inventory_value, 2),
            "inventory_potential_revenue": round(inventory_potential, 2),
            "receivables": round(net_receivables, 2),
            "total": round(total_assets, 2),
        },
        "liabilities": {
            "payables": round(payables, 2),
            "total": round(total_liabilities, 2),
        },
        "equity": round(equity, 2),
    }


# ---------- Barcode data ----------

def get_category_barcode_data(category_id: int) -> dict:
    """Generate barcode payload for a price category.
    The barcode encodes the category ID + sell_price as a string the POS can scan."""
    with conn() as c:
        cat = c.execute("SELECT * FROM price_categories WHERE id=?", (category_id,)).fetchone()
        if not cat:
            return {"error": "category not found"}
    # Code 128-style payload: BBCAT:{id}:{price}
    payload = f"BBCAT:{cat['id']}:{int(cat['sell_price'])}"
    # Use user-defined code, fallback to price-derived
    code = cat["code"] or str(int(cat["sell_price"]))
    return {
        "id": cat["id"],
        "name": cat["name"],
        "sell_price": cat["sell_price"],
        "code": code,
        "color": cat["color"],
        "barcode_payload": payload,
        # QR code URL using a public API (renders inline)
        "qr_url": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={quote(payload)}",
        # Code128 barcode URL
        "barcode_url": f"https://bwipjs-api.metafloor.com/?text={quote(payload)}&scale=2",
    }


def list_category_barcodes() -> list:
    """Get barcode data for all active categories."""
    with conn() as c:
        cats = c.execute(
            "SELECT * FROM price_categories WHERE active=1 ORDER BY sell_price"
        ).fetchall()
    return [get_category_barcode_data(cat["id"]) for cat in cats]


def parse_barcode_scan(payload: str) -> dict:
    """Parse a scanned barcode payload and return category info."""
    if not payload.startswith("BBCAT:"):
        return {"error": "not a BillBook barcode"}
    parts = payload.split(":")
    if len(parts) < 3:
        return {"error": "malformed barcode"}
    try:
        cat_id = int(parts[1])
        price = float(parts[2])
    except Exception:
        return {"error": "invalid barcode data"}
    with conn() as c:
        cat = c.execute("SELECT * FROM price_categories WHERE id=?", (cat_id,)).fetchone()
    if not cat:
        return {"error": "category not found"}
    return {
        "id": cat["id"],
        "name": cat["name"],
        "sell_price": cat["sell_price"],
        "code": cat["code"] or str(int(cat["sell_price"])),
        "color": cat["color"],
    }
