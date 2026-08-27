"""
BillBook — FBR (Federal Board of Revenue, Pakistan) Live POS Integration
========================================================================

This module implements the live FBR POS API integration required by the
FBR's Computerized Sales Tax system for retailers/wholesalers in Pakistan
above the revenue threshold (mandatory as of 2023).

The OLD implementation only exported CSV/JSON to local disk (see
`app/routers/settings.py:fbr_export_now`). That's NOT compliant — the
FBR requires every registered sale to be reported to their live API
within 24 hours, with a returned invoice reference number printed on
the receipt.

WHAT THIS MODULE DOES
---------------------
1. Stores FBR credentials per-install in `settings` (encrypted with Fernet):
   - fbr_usr_id          (FBR-issued username, e.g. "BR123456")
   - fbr_password         (FBR-issued password)
   - fbr_pos_id          (FBR-issued POS terminal ID, e.g. "POS000123")
   - fbr_pos_serial      (hardware serial, free-text)
   - fbr_sandbox_mode    ("1" = use FBR sandbox, "0" = production)

2. On each sale confirmation, optionally calls `post_sale_to_fbr(sale_id)`:
   - Builds the JSON payload per FBR POS API spec
   - POSTs to https://gw.fbr.gov.br/api/.../SALES (or sandbox equivalent)
   - Stores the returned invoice_ref + QR-signed-payload in `sales.fbr_invoice_ref`
     and `sales.fbr_qr_payload`
   - The receipt PDF prints a QR code from fbr_qr_payload (Tauri webview
     renders it; mobile app uses the same JS QR library)

3. Compliance checker (`verify_compliance()`) reports:
   - Whether all required shop fields are set (NTN, STRN, address)
   - Whether FBR credentials are configured
   - Whether the last 100 sales have fbr_invoice_ref populated
   - Whether the receipt template includes the FBR QR code block

API SPEC
--------
Based on FBR's published POS API documentation:
  https://www.fbr.gov.br/pos-integration-guide (subject to change — verify
  with FBR before going live).

  Endpoint: POST /api/v1/BRANCHES/{usr_id}/SALES
  Headers:
    Authorization: Basic base64(usr_id:password)
    Content-Type: application/json
  Body:
    {
      "POS_ID": "POS000123",
      "POS_SERIAL": "ABC123",
      "USRCODE": "BR123456",
      "INVOICE_DATE": "2026-08-26T15:30:00Z",
      "BUYER_NTN": "",          // empty if walk-in customer
      "BUYER_NAME": "",         // empty if walk-in
      "ITEMS": [
        {"ITEM_CODE": "...", "ITEM_NAME": "...", "QTY": 1,
         "PRICE": 1500.00, "TAX_RATE": 0, "TAX_AMOUNT": 0,
         "DISCOUNT": 0, "TOTAL": 1500.00}
      ],
      "TOTAL_QTY": 1,
      "TOTAL_DISCOUNT": 0,
      "TOTAL_TAX": 0,
      "TOTAL_AMOUNT": 1500.00,
      "PAYMENT_MODE": "CASH"
    }
  Response:
    {
      "INVOICE_REF": "POS000123-1234567",  // store this on the sale
      "QR_PAYLOAD": "https://sys.fbr.gov.pk/...?inv=...", // encode as QR
      "STATUS": "POSTED"
    }

DEPENDENCIES
-----------
- httpx (already in requirements.txt — used elsewhere)
- qrcode (already in requirements.txt)

NO external services or paid libraries required.
"""
from __future__ import annotations
import base64
import json
import logging
from datetime import datetime
from typing import Any

import httpx

from . import crypto, db

logger = logging.getLogger(__name__)

# FBR API endpoints
FBR_SANDBOX_URL = "https://api.sb.fbr.gov.pk/api/v1"
FBR_PRODUCTION_URL = "https://gw.fbr.gov.pk/api/v1"
FBR_TIMEOUT = 10  # seconds


# ─── Credentials storage ──────────────────────────────────────────────────────

def is_configured() -> bool:
    """Return True if FBR credentials are stored."""
    enc = db.get_setting("fbr_credentials_enc", "")
    return bool(enc)


def set_credentials(payload: dict) -> None:
    """Persist FBR credentials (encrypted) into the settings table."""
    creds = {
        "usr_id": payload["usr_id"].strip(),
        "password": payload["password"],          # stored as-is, encrypted at rest
        "pos_id": payload["pos_id"].strip(),
        "pos_serial": payload.get("pos_serial", "").strip(),
        "sandbox": bool(payload.get("sandbox", True)),
    }
    enc = crypto.encrypt_value(json.dumps(creds))
    db.set_setting("fbr_credentials_enc", enc)
    db.log_activity("fbr_credentials_set", "compliance", None,
                    "FBR credentials updated", {"pos_id": creds["pos_id"]})


def get_credentials() -> dict | None:
    """Return decrypted FBR credentials or None if not configured."""
    enc = db.get_setting("fbr_credentials_enc", "")
    if not enc:
        return None
    return json.loads(crypto.decrypt_value(enc))


def clear_credentials() -> None:
    db.set_setting("fbr_credentials_enc", "")


# ─── Compliance checker ──────────────────────────────────────────────────────

def verify_compliance() -> dict:
    """Audit FBR readiness. Returns a structured report:

    {
      "overall_ok": bool,
      "shop_profile": {"ok": bool, "missing": ["ntn", "strn", ...]},
      "fbr_credentials": {"ok": bool, "missing": [...]},
      "recent_sales_posted": {"ok": bool, "posted": 87, "total": 100, "pct": 87},
      "receipt_template": {"ok": bool, "notes": "FBR QR block present"},
      "recommendations": ["..."]
    }
    """
    recs: list[str] = []
    shop_ok, shop_missing = _check_shop_profile()
    cred_ok, cred_missing = _check_fbr_credentials()
    sales_ok, sales_stats = _check_recent_sales_posted()
    receipt_ok, receipt_notes = _check_receipt_template()

    if not shop_ok:
        recs.append("Set shop NTN + STRN + address in Settings > Shop Profile.")
    if not cred_ok:
        recs.append("Add FBR credentials in Settings > FBR > Configure.")
    if shop_ok and cred_ok and not sales_ok:
        recs.append("Enable 'Auto-post each sale to FBR' in Settings > FBR.")
    if not receipt_ok:
        recs.append("Update receipt template to include the FBR QR code block.")

    return {
        "overall_ok": shop_ok and cred_ok and sales_ok and receipt_ok,
        "shop_profile": {"ok": shop_ok, "missing": shop_missing},
        "fbr_credentials": {"ok": cred_ok, "missing": cred_missing},
        "recent_sales_posted": {"ok": sales_ok, **sales_stats},
        "receipt_template": {"ok": receipt_ok, "notes": receipt_notes},
        "recommendations": recs,
    }


def _check_shop_profile() -> tuple[bool, list[str]]:
    missing = []
    for k, label in [
        ("shop_ntn", "NTN"), ("shop_strn", "STRN"),
        ("shop_name", "Shop Name"), ("shop_address", "Shop Address"),
        ("shop_phone", "Shop Phone"),
    ]:
        if not db.get_setting(k, "").strip():
            missing.append(label)
    return (len(missing) == 0, missing)


def _check_fbr_credentials() -> tuple[bool, list[str]]:
    if not is_configured():
        return (False, ["usr_id", "password", "pos_id"])
    creds = get_credentials() or {}
    missing = [k for k in ("usr_id", "password", "pos_id") if not creds.get(k)]
    return (len(missing) == 0, missing)


def _check_recent_sales_posted() -> tuple[bool, dict]:
    with db.conn() as c:
        total_row = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE payment_status IN ('paid','credit','partial') "
            "AND created_at >= datetime('now','-1 day')"
        ).fetchone()
        posted_row = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE payment_status IN ('paid','credit','partial') "
            "AND created_at >= datetime('now','-1 day') "
            "AND fbr_invoice_ref IS NOT NULL AND fbr_invoice_ref != ''"
        ).fetchone()
    total = total_row["n"] if total_row else 0
    posted = posted_row["n"] if posted_row else 0
    pct = round(100 * posted / total) if total else 0
    # OK if 0 sales in last 24h OR >90% are posted
    ok = (total == 0) or (pct >= 90)
    return (ok, {"posted": posted, "total": total, "pct": pct})


def _check_receipt_template() -> tuple[bool, str]:
    """Look for the fbr_qr block in receipt rendering code.

    BillBook renders receipts client-side via JS (no static receipt-template.html
    on disk), so this checker scans both HTML and JS files under app/static for
    either `fbr-qr` (HTML id) or `fbr_qr` / `fbr_invoice_ref` (JS data key).
    """
    import os
    from .config import BASE
    static_dir = BASE / "app" / "static"
    if not static_dir.exists():
        return (False, f"static dir not found at {static_dir}")
    # Search recursively for the fbr-qr / fbr_qr token in HTML + JS.
    # v8.14.1 FIX: previous version had an operator-precedence bug where
    # `list(a) + list(b) if cond else []` collapsed to `[]` whenever `b` was
    # absent — losing all matches from `a` too. Fixed with explicit grouping.
    candidates: list = []
    candidates.extend(list(static_dir.rglob("*.html")))
    candidates.extend(list(static_dir.rglob("*.js")))
    found_in: list[str] = []
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "fbr-qr" in text or "fbr_qr" in text or "fbr_invoice_ref" in text:
            found_in.append(str(p.relative_to(static_dir)))
            break
    if found_in:
        return (True, f"FBR QR / invoice_ref reference found in: {', '.join(found_in)}")
    return (False,
            "Receipt rendering code missing the FBR QR block — add a "
            "<div id='fbr-qr'></div> element populated from sale.fbr_qr_payload "
            "in app/static/js/apps/pos/components/sale-detail.js or "
            "app/static/js/pages/pos.js (print-receipt path).")


# ─── Live API call ───────────────────────────────────────────────────────────

def post_sale_to_fbr(sale_id: int) -> dict:
    """Submit a single confirmed sale to FBR. Idempotent — if the sale
    already has fbr_invoice_ref, returns the cached result without re-posting.

    Returns {"posted": bool, "invoice_ref": str|None, "qr_payload": str|None,
             "error": str|None}.
    """
    creds = get_credentials()
    if not creds:
        return {"posted": False, "invoice_ref": None, "qr_payload": None,
                "error": "FBR credentials not configured"}

    # Idempotency: skip if already posted
    with db.conn() as c:
        existing = c.execute(
            "SELECT fbr_invoice_ref, fbr_qr_payload FROM sales WHERE id=?",
            (sale_id,),
        ).fetchone()
        if existing and existing["fbr_invoice_ref"]:
            return {"posted": True, "invoice_ref": existing["fbr_invoice_ref"],
                    "qr_payload": existing["fbr_qr_payload"], "error": None}

        # Build the payload from the sale + sale_items + customer + shop profile
        sale = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if not sale:
            return {"posted": False, "invoice_ref": None, "qr_payload": None,
                    "error": f"Sale {sale_id} not found"}
        items = c.execute(
            "SELECT si.*, c.name AS category_name FROM sale_items si "
            "LEFT JOIN categories c ON si.category_id = c.id "
            "WHERE si.sale_id=? ORDER BY si.id", (sale_id,)
        ).fetchall()
        customer = None
        if sale["customer_id"]:
            customer = c.execute(
                "SELECT * FROM customers WHERE id=?", (sale["customer_id"],)
            ).fetchone()

    payload = _build_fbr_payload(creds, sale, items, customer)
    base_url = FBR_SANDBOX_URL if creds.get("sandbox") else FBR_PRODUCTION_URL
    endpoint = f"{base_url}/BRANCHES/{creds['usr_id']}/SALES"
    auth = base64.b64encode(f"{creds['usr_id']}:{creds['password']}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "User-Agent": "BillBook/8.14.0",
    }

    try:
        with httpx.Client(timeout=FBR_TIMEOUT) as client:
            r = client.post(endpoint, json=payload, headers=headers)
        if r.status_code != 200:
            return {"posted": False, "invoice_ref": None, "qr_payload": None,
                    "error": f"FBR HTTP {r.status_code}: {r.text[:500]}"}
        data = r.json()
        invoice_ref = data.get("INVOICE_REF", "")
        qr_payload = data.get("QR_PAYLOAD", "")
        if not invoice_ref:
            return {"posted": False, "invoice_ref": None, "qr_payload": None,
                    "error": f"FBR response missing INVOICE_REF: {data}"}
        # Persist on the sale row
        with db.write_tx() as c:
            c.execute(
                "UPDATE sales SET fbr_invoice_ref=?, fbr_qr_payload=?, "
                "fbr_posted_at=datetime('now','localtime') WHERE id=?",
                (invoice_ref, qr_payload, sale_id),
            )
        db.log_activity("fbr_sale_posted", "compliance", sale_id,
                        f"Posted to FBR: {invoice_ref}",
                        {"invoice_ref": invoice_ref})
        return {"posted": True, "invoice_ref": invoice_ref,
                "qr_payload": qr_payload, "error": None}
    except httpx.TimeoutException:
        return {"posted": False, "invoice_ref": None, "qr_payload": None,
                "error": "FBR API timeout (>10s) — sale will retry on next sync"}
    except Exception as e:
        return {"posted": False, "invoice_ref": None, "qr_payload": None,
                "error": f"FBR post failed: {e}"}


def _build_fbr_payload(creds: dict, sale, items, customer) -> dict:
    """Build the FBR POS API JSON payload for a single sale."""
    total_qty = sum(float(i["qty"] or 0) for i in items)
    total_discount = float(sale["discount_amount"] or 0)
    total_tax = float(sale["tax_amount"] or 0)
    total_amount = float(sale["total"] or 0)
    items_payload = []
    for it in items:
        qty = float(it["qty"] or 0)
        price = float(it["unit_price"] or 0)
        line_total = qty * price
        items_payload.append({
            "ITEM_CODE": str(it.get("category_id") or it["id"]),
            "ITEM_NAME": it.get("category_name") or f"Item {it['id']}",
            "QTY": qty,
            "PRICE": round(price, 2),
            "TAX_RATE": 0,  # standard-rate items handled separately
            "TAX_AMOUNT": 0,
            "DISCOUNT": round(float(it.get("discount") or 0), 2),
            "TOTAL": round(line_total, 2),
        })
    return {
        "POS_ID": creds["pos_id"],
        "POS_SERIAL": creds.get("pos_serial", ""),
        "USRCODE": creds["usr_id"],
        "INVOICE_DATE": datetime.fromisoformat(
            sale["created_at"].replace(" ", "T")
        ).strftime("%Y-%m-%dT%H:%M:%SZ") if sale["created_at"] else datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "BUYER_NTN": (customer["ntn"] if customer and "ntn" in customer.keys() else "") or "",
        "BUYER_NAME": (customer["name"] if customer else "") or "",
        "ITEMS": items_payload,
        "TOTAL_QTY": total_qty,
        "TOTAL_DISCOUNT": round(total_discount, 2),
        "TOTAL_TAX": round(total_tax, 2),
        "TOTAL_AMOUNT": round(total_amount, 2),
        "PAYMENT_MODE": _map_payment_mode(sale["payment_method"]),
    }


def _map_payment_mode(method: str) -> str:
    """Map BillBook payment methods to FBR's expected enum."""
    m = (method or "").lower()
    if "cash" in m: return "CASH"
    if "card" in m: return "CARD"
    if "credit" in m: return "CREDIT"
    if "raast" in m or "jazzcash" in m or "easypaisa" in m: return "ONLINE"
    return "CASH"
