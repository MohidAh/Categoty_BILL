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


class AppearanceIn(BaseModel):
    theme: str | None = None
    accent_color: str | None = None
    density: str | None = None
    font_scale: str | None = None
    # v8.15.0 (design.md): serif display headlines + radius scale
    serif_headings: bool | None = None
    radius: str | None = None


# v8.15.0 (design.md): canonical defaults for the Claude-warm design system.
# Cream canvas is the brand default floor, coral (#cc785c) the brand accent.
APPEARANCE_DEFAULTS = {
    "theme": "light",
    "accent_color": "#cc785c",
    "density": "comfortable",
    "font_scale": "100",
    "serif_headings": "1",
    "radius": "standard",
}
APPEARANCE_RADIUS_OPTIONS = ("compact", "standard", "roomy")
APPEARANCE_DENSITY_OPTIONS = ("comfortable", "compact")




class SMSConfigIn(BaseModel):
    enabled: bool = False
    provider: str = "twilio"
    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""




class TaxConfigIn(BaseModel):
    rate: float
    inclusive: bool = False




@router.get("/api/appearance")
def get_appearance() -> Any:
    from ..db import get_setting
    return {
        "theme": get_setting("appearance_theme", APPEARANCE_DEFAULTS["theme"]),
        "accent_color": get_setting("appearance_accent", APPEARANCE_DEFAULTS["accent_color"]),
        "density": get_setting("appearance_density", APPEARANCE_DEFAULTS["density"]),
        "font_scale": get_setting("appearance_font_scale", APPEARANCE_DEFAULTS["font_scale"]),
        "serif_headings": get_setting("appearance_serif_headings", APPEARANCE_DEFAULTS["serif_headings"]) == "1",
        "radius": get_setting("appearance_radius", APPEARANCE_DEFAULTS["radius"]),
    }




@router.post("/api/appearance")
def set_appearance(payload: AppearanceIn) -> Any:
    # v8.15.0: validate accent color server-side — a malformed hex would
    # poison every CSS variable the theme engine sets on the client.
    if payload.accent_color is not None:
        import re
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", payload.accent_color):
            raise HTTPException(status_code=400, detail="accent_color must be a 6-digit hex like #cc785c")
    if payload.radius is not None and payload.radius not in APPEARANCE_RADIUS_OPTIONS:
        raise HTTPException(status_code=400, detail=f"radius must be one of {APPEARANCE_RADIUS_OPTIONS}")
    if payload.density is not None and payload.density not in APPEARANCE_DENSITY_OPTIONS:
        raise HTTPException(status_code=400, detail=f"density must be one of {APPEARANCE_DENSITY_OPTIONS}")
    if payload.font_scale is not None:
        try:
            fs = int(payload.font_scale)
            if not (90 <= fs <= 120):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="font_scale must be an integer between 90 and 120")
    with db.conn() as c:
        if payload.theme is not None and payload.theme in ('light', 'dark'):
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_theme', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (payload.theme, payload.theme))
        if payload.accent_color is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_accent', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (payload.accent_color, payload.accent_color))
        if payload.density is not None and payload.density in APPEARANCE_DENSITY_OPTIONS:
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_density', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (payload.density, payload.density))
        if payload.font_scale is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_font_scale', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (payload.font_scale, payload.font_scale))
        if payload.serif_headings is not None:
            v = "1" if payload.serif_headings else "0"
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_serif_headings', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (v, v))
        if payload.radius is not None and payload.radius in APPEARANCE_RADIUS_OPTIONS:
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_radius', ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = ?", (payload.radius, payload.radius))
    return {"ok": True}




@router.get("/api/tax/config")
def get_tax_config() -> Any:
    return {
        "rate": pos_extra.get_tax_rate(),
        "inclusive": pos_extra.get_tax_inclusive(),
    }




@router.post("/api/tax/config")
def set_tax_config(payload: TaxConfigIn) -> Any:
    pos_extra.set_tax_rate(payload.rate)
    pos_extra.set_tax_inclusive(payload.inclusive)
    db.log_activity("tax_configured", "settings", None,
                    f"Tax rate set to {payload.rate*100:.1f}% ({'inclusive' if payload.inclusive else 'exclusive'})")
    return {"ok": True}


# ==================================================================
# Purchase Orders
# ==================================================================



@router.get("/api/sms/config")
def get_sms_config_route() -> Any:
    cfg = pos_extra.get_sms_config()
    # Don't expose the auth token in full — mask it
    masked_token = ""
    if cfg["auth_token"]:
        masked_token = "•" * max(0, len(cfg["auth_token"]) - 4) + cfg["auth_token"][-4:]
    return {
        "enabled": cfg["enabled"],
        "provider": cfg["provider"],
        "account_sid": cfg["account_sid"],
        "auth_token_masked": masked_token,
        "from_number": cfg["from_number"],
    }




@router.post("/api/sms/config")
def set_sms_config_route(payload: SMSConfigIn) -> Any:
    cfg = {
        "enabled": payload.enabled,
        "provider": payload.provider,
        "account_sid": payload.account_sid,
        "from_number": payload.from_number,
    }
    # Only update auth_token if user provided a non-masked value
    if payload.auth_token and not payload.auth_token.startswith("•"):
        cfg["auth_token"] = payload.auth_token
    pos_extra.set_sms_config(cfg)
    db.log_activity("sms_configured", "settings", None,
                    f"SMS {'enabled' if payload.enabled else 'disabled'}")
    return {"ok": True}




@router.get("/api/system/features")
def system_features() -> Any:
    """Return a list of available features — used by the landing page to show what's enabled."""
    cfg = pos_extra.get_sms_config()
    return {
        "pos": True,
        "bills": True,
        "suppliers": True,
        "reports": True,
        "insights": True,
        "settings": True,
        "inventory": True,
        "customers": True,
        "expenses": True,
        "cash_drawer": True,
        "pwa": True,
        "employees": True,
        "loyalty": True,
        "tax": pos_extra.get_tax_rate() > 0,
        "purchase_orders": True,
        "barcodes": True,
        "csv_import": True,
        "cash_flow_report": True,
        "balance_sheet_report": True,
        "sms": cfg["enabled"],
        "multi_shop": False,
        "cloud_backup": False,
        "daraz_integration": False,
        "external_pos_import": True,
        "sales_targets": True,
        "returns_exchange": True,
        "peak_hours_report": True,
        "top_items_report": True,
        "email_receipts": True,
    }


# ==================================================================
# External POS Backup Import
# ==================================================================



# ═══════════════════════════════════════════════════
# Shop Profile (Phase 2 — FBR-Ready Compliance)
# ═══════════════════════════════════════════════════

class ShopProfileIn(BaseModel):
    shop_name: str | None = None
    address: str | None = None
    phone: str | None = None
    ntn: str | None = None
    strn: str | None = None
    logo: str | None = None
    receipt_footer: str | None = None
    raast_id: str | None = None


@router.get("/api/shop-profile")
def get_shop_profile() -> Any:
    """Get shop profile for receipts and FBR compliance."""
    from ..db import get_setting
    return {
        "shop_name": get_setting("shop_name", "BillBook Store"),
        "address": get_setting("shop_address", ""),
        "phone": get_setting("shop_phone", ""),
        "ntn": get_setting("shop_ntn", ""),
        "strn": get_setting("shop_strn", ""),
        "logo": get_setting("shop_logo", ""),
        "receipt_footer": get_setting("receipt_footer", "Thank you for your business!"),
        "raast_id": get_setting("raast_id", ""),
    }


@router.post("/api/shop-profile")
def set_shop_profile(payload: ShopProfileIn) -> Any:
    """Save shop profile."""
    with db.conn() as c:
        if payload.shop_name is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_name', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.shop_name, payload.shop_name))
        if payload.address is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_address', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.address, payload.address))
        if payload.phone is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_phone', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.phone, payload.phone))
        if payload.ntn is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_ntn', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.ntn, payload.ntn))
        if payload.strn is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_strn', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.strn, payload.strn))
        if payload.logo is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_logo', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.logo, payload.logo))
        if payload.receipt_footer is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('receipt_footer', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.receipt_footer, payload.receipt_footer))
        if payload.raast_id is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('raast_id', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.raast_id, payload.raast_id))
    return {"ok": True}


# ═══════════════════════════════════════════════════
# Phase 10: Settings & Admin
# ═══════════════════════════════════════════════════

class ReceiptTemplateIn(BaseModel):
    show_logo: bool | None = None
    show_ntn: bool | None = None
    show_strn: bool | None = None
    show_qr: bool | None = None
    header_text: str | None = None
    footer_text: str | None = None
    logo_url: str | None = None


@router.get("/api/receipt-template")
def get_receipt_template() -> Any:
    """Get receipt template configuration."""
    from ..db import get_setting
    return {
        "show_logo": get_setting("rcpt_show_logo", "1") == "1",
        "show_ntn": get_setting("rcpt_show_ntn", "1") == "1",
        "show_strn": get_setting("rcpt_show_strn", "1") == "1",
        "show_qr": get_setting("rcpt_show_qr", "0") == "1",
        "header_text": get_setting("rcpt_header", ""),
        "footer_text": get_setting("receipt_footer", "Thank you for your business!"),
        "logo_url": get_setting("shop_logo", ""),
    }


@router.post("/api/receipt-template")
def set_receipt_template(payload: ReceiptTemplateIn) -> Any:
    """Save receipt template configuration."""
    with db.conn() as c:
        if payload.show_logo is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('rcpt_show_logo', ?) ON CONFLICT(key) DO UPDATE SET value = ?", ("1" if payload.show_logo else "0", "1" if payload.show_logo else "0"))
        if payload.show_ntn is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('rcpt_show_ntn', ?) ON CONFLICT(key) DO UPDATE SET value = ?", ("1" if payload.show_ntn else "0", "1" if payload.show_ntn else "0"))
        if payload.show_strn is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('rcpt_show_strn', ?) ON CONFLICT(key) DO UPDATE SET value = ?", ("1" if payload.show_strn else "0", "1" if payload.show_strn else "0"))
        if payload.show_qr is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('rcpt_show_qr', ?) ON CONFLICT(key) DO UPDATE SET value = ?", ("1" if payload.show_qr else "0", "1" if payload.show_qr else "0"))
        if payload.header_text is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('rcpt_header', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.header_text, payload.header_text))
        if payload.footer_text is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('receipt_footer', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.footer_text, payload.footer_text))
        if payload.logo_url is not None:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_logo', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.logo_url, payload.logo_url))
    return {"ok": True}


class AccentColorIn(BaseModel):
    accent_color: str


@router.post("/api/appearance/accent")
def set_accent_color(payload: AccentColorIn) -> Any:
    """Set the SnowUI definable brand color."""
    with db.conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES('appearance_accent', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.accent_color, payload.accent_color))
    return {"ok": True, "accent_color": payload.accent_color}


@router.get("/api/sessions/audit")
def sessions_audit() -> Any:
    """Audit log of active sessions with IP and role."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT token, substr(token, 1, 8) AS token_prefix, role, employee_id, "
            "created_at, expires_at FROM sessions ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return {"sessions": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════
# Phase 9: UX Professionalization
# ═══════════════════════════════════════════════════

class LanguagePrefIn(BaseModel):
    language: str  # 'en' | 'ur'
    density: str | None = None  # 'comfortable' | 'compact'


@router.get("/api/preferences")
def get_preferences() -> Any:
    """Get user UI preferences (language, density)."""
    from ..db import get_setting
    return {
        "language": get_setting("ui_language", "en"),
        "density": get_setting("appearance_density", "comfortable"),
    }


@router.post("/api/preferences")
def set_preferences(payload: LanguagePrefIn) -> Any:
    """Save user UI preferences."""
    with db.conn() as c:
        if payload.language in ('en', 'ur'):
            c.execute("INSERT INTO settings(key, value) VALUES('ui_language', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.language, payload.language))
        if payload.density in ('comfortable', 'compact'):
            c.execute("INSERT INTO settings(key, value) VALUES('appearance_density', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (payload.density, payload.density))
    return {"ok": True}


@router.get("/api/onboarding/status")
def onboarding_status() -> Any:
    """Check onboarding checklist completion."""
    with db.conn() as c:
        has_supplier = c.execute("SELECT COUNT(*) n FROM suppliers WHERE deleted_at IS NULL").fetchone()["n"] > 0
        has_bill = c.execute("SELECT COUNT(*) n FROM bills WHERE deleted_at IS NULL").fetchone()["n"] > 0
        has_confirmed = c.execute("SELECT COUNT(*) n FROM bills WHERE status='confirmed' AND deleted_at IS NULL").fetchone()["n"] > 0
        has_sale = c.execute("SELECT COUNT(*) n FROM sales").fetchone()["n"] > 0
    steps = [
        {"key": "add_supplier", "label": "Add a supplier", "done": has_supplier},
        {"key": "upload_bill", "label": "Upload a bill", "done": has_bill},
        {"key": "confirm_bill", "label": "Confirm a bill", "done": has_confirmed},
        {"key": "first_sale", "label": "Make your first sale", "done": has_sale},
    ]
    completed = sum(1 for s in steps if s["done"])
    return {"steps": steps, "completed": completed, "total": len(steps), "all_done": completed == len(steps)}


class FBRNightlyIn(BaseModel):
    enabled: bool = False
    export_path: str = "data/fbr"


@router.get("/api/fbr/nightly")
def get_fbr_nightly_config() -> Any:
    """Get FBR nightly export configuration."""
    from ..db import get_setting
    return {
        "enabled": get_setting("fbr_nightly_enabled", "0") == "1",
        "export_path": get_setting("fbr_nightly_path", "data/fbr"),
    }


@router.post("/api/fbr/nightly")
def set_fbr_nightly_config(payload: FBRNightlyIn) -> Any:
    """Configure FBR nightly export.

    C4 fix (v8.13.4): validate that export_path resolves under the data/
    directory to prevent path traversal (an attacker who reaches this
    manager-only endpoint could otherwise write invoices-*.json anywhere).
    Relative paths are accepted and resolved under DATA.
    """
    from pathlib import Path
    import os
    from ..config import DATA
    base = Path(DATA).resolve()
    if os.path.isabs(payload.export_path):
        candidate = Path(payload.export_path).resolve()
    else:
        candidate = (base / payload.export_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(
            400,
            f"export_path must be inside {base} (got {candidate})"
        )
    with db.conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES('fbr_nightly_enabled', ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                  ("1" if payload.enabled else "0", "1" if payload.enabled else "0"))
        c.execute("INSERT INTO settings(key, value) VALUES('fbr_nightly_path', ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                  (str(candidate), str(candidate)))
    return {"ok": True}


@router.post("/api/fbr/export-now")
def fbr_export_now() -> Any:
    """Trigger immediate FBR export for today.

    C4 fix (v8.13.4): the export_path from settings is now resolved and
    checked against DATA before any file write — prevents path traversal
    via a tampered settings row. Relative paths are resolved under DATA
    (matches the previous default 'data/fbr' behavior but keeps it inside
    the configured data directory, even under test infrastructure).
    """
    from ..db import get_setting
    import os, json
    from datetime import datetime
    from pathlib import Path
    from ..config import DATA
    export_path_str = get_setting("fbr_nightly_path", "data/fbr")
    # Validate resolved path is under DATA.
    # Relative paths are resolved under DATA so the test fixture (which
    # sets config.DATA = temp_dir) keeps working with the default value.
    base = Path(DATA).resolve()
    if os.path.isabs(export_path_str):
        export_path = Path(export_path_str).resolve()
    else:
        export_path = (base / export_path_str).resolve()
    try:
        export_path.relative_to(base)
    except ValueError:
        raise HTTPException(
            400,
            f"export_path must be inside {base} (got {export_path})"
        )
    os.makedirs(export_path, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    filepath = export_path / f"invoices-{today}.json"
    with db.conn() as c:
        sales = c.execute(
            "SELECT * FROM sales WHERE date(created_at)=? AND payment_status IN ('paid', 'credit', 'partial')", (today,)
        ).fetchall()
        ntn = get_setting("shop_ntn", "")
        strn = get_setting("shop_strn", "")
        invoices = []
        for s in sales:
            items = c.execute("SELECT * FROM sale_items WHERE sale_id=?", (s["id"],)).fetchall()
            invoices.append({
                "invoice_no": s["invoice_no"],
                "datetime": s["created_at"],
                "ntn": ntn, "strn": strn,
                "items": [{"name": i["item_name"], "qty": i["qty"], "price": i["sell_price"],
                           "total": i["line_total"]} for i in items],
                "total": s["total"],
                "payment_method": s["payment_method"],
            })
    with open(filepath, "w") as f:
        json.dump({"date": today, "invoices": invoices, "count": len(invoices)}, f, indent=2)
    return {"ok": True, "file": str(filepath), "count": len(invoices)}


# Payment Methods manager
class PaymentMethodUpdateIn(BaseModel):
    name: str | None = None
    type: str | None = None
    icon: str | None = None
    sort_order: int | None = None
    active: int | None = None


@router.put("/api/payment-methods/{pm_id}")
def update_payment_method(pm_id: int, payload: PaymentMethodUpdateIn) -> Any:
    """Update a payment method."""
    fields, vals = [], []
    if payload.name is not None: fields.append("name = ?"); vals.append(payload.name)
    if payload.type is not None: fields.append("type = ?"); vals.append(payload.type)
    if payload.icon is not None: fields.append("icon = ?"); vals.append(payload.icon)
    if payload.sort_order is not None: fields.append("sort_order = ?"); vals.append(payload.sort_order)
    if payload.active is not None: fields.append("active = ?"); vals.append(payload.active)
    if not fields:
        raise HTTPException(400, "No fields to update")
    vals.append(pm_id)
    with db.conn() as c:
        cur = c.execute(f"UPDATE payment_methods SET {', '.join(fields)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise HTTPException(404, "payment method not found")
    return {"ok": True}


# ─── v4.0 Phase 1: COGS maintenance ────────────────────────────────────────────

@router.post("/api/maintenance/recalc-cogs")
def recalc_cogs() -> Any:
    """Recompute cost_price for sale_items where cost_price = 0.

    Uses TODAY's weighted-average cost per category (shop.get_category_avg_cost).
    Historical sales keep their stored cost_price unless it was zero — this is
    an approximation since the avg cost at sale time is not preserved.

    Idempotent: running twice only touches rows that still have cost_price=0.
    Returns the count of rows updated.
    """
    month = datetime.now().strftime("%Y-%m")
    affected = 0
    skipped_categories = []
    with db.conn() as c:
        cats = c.execute(
            "SELECT DISTINCT category_id FROM sale_items "
            "WHERE cost_price = 0 AND category_id IS NOT NULL"
        ).fetchall()
        for r in cats:
            cid = r["category_id"]
            avg_cost = shop_mod.get_category_avg_cost(cid)
            if avg_cost <= 0:
                skipped_categories.append(cid)
                continue
            cur = c.execute(
                "UPDATE sale_items SET cost_price = ? "
                "WHERE cost_price = 0 AND category_id = ?",
                (avg_cost, cid),
            )
            affected += cur.rowcount
    db.log_activity(
        "recalc_cogs", "maintenance", None,
        f"Recalculated COGS for {affected} sale_items using {month} avg cost (approximation)",
        {"affected": affected, "month": month, "skipped_categories": skipped_categories},
    )
    return {
        "ok": True,
        "affected": affected,
        "month": month,
        "skipped_categories": skipped_categories,
        "note": "Approximation based on current weighted-avg cost per category",
    }


# ─── v8.0 Phase 1: Branch Identity ─────────────────────────────────────────

class BranchConfigIn(BaseModel):
    role: str = "branch"
    branch_name: str = "Main Shop"
    region: str = ""
    hub_url: str = ""
    sync_token: str = ""  # plaintext on write; hashed before storage
    branch_id: Optional[str] = None


@router.get("/api/branch-config")
def get_branch_config() -> Any:
    """Return the local branch_config row (id=1).

    Never returns the sync_token_hash — only a boolean `has_sync_token`.
    With role='branch' + hub_url='' (the defaults), the app behaves EXACTLY
    as v7.2 (single-shop, no sync attempts).
    """
    with db.conn() as c:
        row = c.execute("SELECT * FROM branch_config WHERE id=1").fetchone()
    if not row:
        return {"role": "branch", "branch_id": None, "branch_name": "Main Shop",
                "region": "", "hub_url": "", "has_sync_token": False}
    d = dict(row)
    has_token = bool(d.get("sync_token_hash"))
    return {
        "role": d.get("role", "branch"),
        "branch_id": d.get("branch_id"),
        "branch_name": d.get("branch_name", "Main Shop"),
        "region": d.get("region", ""),
        "hub_url": d.get("hub_url", ""),
        "has_sync_token": has_token,
    }


@router.put("/api/branch-config")
def set_branch_config(payload: BranchConfigIn) -> Any:
    """Update the local branch_config (upsert into row id=1).

    If sync_token is non-empty, hash it before storage (never store plaintext).
    If sync_token is empty/omitted, preserve the existing token.
    """
    import hashlib
    # Validate role
    if payload.role not in ("branch", "hq"):
        raise HTTPException(400, "role must be 'branch' or 'hq'")
    # Generate a branch_id if missing (deterministic UUID-ish from name)
    branch_id = payload.branch_id
    if not branch_id:
        import secrets
        branch_id = "BR-" + secrets.token_hex(4).upper()
    # Hash the sync_token if provided
    sync_token_hash = ""
    if payload.sync_token:
        sync_token_hash = hashlib.sha256(payload.sync_token.encode()).hexdigest()
    with db.conn() as c:
        existing = c.execute("SELECT id FROM branch_config WHERE id=1").fetchone()
        if existing:
            # Preserve existing token if caller didn't send a new one
            if not sync_token_hash and not payload.sync_token:
                old = c.execute("SELECT sync_token_hash FROM branch_config WHERE id=1").fetchone()
                sync_token_hash = old["sync_token_hash"] if old else ""
            c.execute(
                "UPDATE branch_config SET role=?, branch_id=?, branch_name=?, region=?, "
                "hub_url=?, sync_token_hash=? WHERE id=1",
                (payload.role, branch_id, payload.branch_name, payload.region,
                 payload.hub_url, sync_token_hash),
            )
        else:
            c.execute(
                "INSERT INTO branch_config(id, role, branch_id, branch_name, region, hub_url, sync_token_hash) "
                "VALUES(1,?,?,?,?,?,?)",
                (payload.role, branch_id, payload.branch_name, payload.region,
                 payload.hub_url, sync_token_hash),
            )
    db.log_activity(
        "branch_config_updated", "branch_config", 1,
        f"Branch config updated: {payload.branch_name} ({payload.role})",
        {"role": payload.role, "branch_id": branch_id, "hub_url": payload.hub_url},
    )
    return {"ok": True, "branch_id": branch_id}


# ─── v8.2: PIN verification endpoint ────────────────────────────────────────

class PinVerifyIn(BaseModel):
    pin: str


@router.post("/api/security/verify-pin")
def verify_pin(payload: PinVerifyIn) -> Any:
    """Verify a manager/admin PIN. Returns {ok: bool}."""
    from .. import shop as _shop
    emp = _shop.verify_manager_pin(payload.pin)
    return {"ok": emp is not None, "name": emp["name"] if emp else None}
