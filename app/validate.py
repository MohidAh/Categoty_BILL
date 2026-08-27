"""Validation, profit/margin calculation, duplicate detection."""
import logging
import re
from .db import conn

logger = logging.getLogger(__name__)


def _num(tok):
    if tok is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(tok))
    return float(m.group()) if m else None


def normalize_name(name: str) -> str:
    """Normalize item names for comparison (lowercase, trim, collapse spaces)."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


def get_historical_prices() -> dict:
    """Build a lookup of item name → list of historical prices from confirmed bills."""
    hist = {}
    try:
        with conn() as c:
            rows = c.execute(
                "SELECT bi.raw, bi.price, bi.qty, bi.unit FROM bill_items bi "
                "JOIN bills b ON bi.bill_id = b.id "
                "WHERE b.status = 'confirmed' AND b.deleted_at IS NULL "
                "AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
                "ORDER BY b.id DESC LIMIT 500"
            ).fetchall()
        for r in rows:
            key = normalize_name(r["raw"])
            if key and r["price"] and r["price"] > 0:
                if key not in hist:
                    hist[key] = []
                hist[key].append(r["price"])
    except Exception as _e:
        logger.warning("Silent exception in validate.py: %s", _e, exc_info=True)
    return hist


def check_price_anomaly(item_name: str, price: float, hist: dict) -> str | None:
    """Check if a price deviates significantly from historical data.

    Returns a warning string if anomalous, None if OK.
    """
    if not price or price <= 0:
        return None
    key = normalize_name(item_name)
    if not key or key not in hist:
        return None  # No history for this item
    prices = hist[key]
    if len(prices) < 2:
        return None  # Need at least 2 historical data points
    avg = sum(prices) / len(prices)
    if avg <= 0:
        return None
    ratio = price / avg
    if ratio > 2.0:
        return (f"⚠ '{item_name[:30]}' price Rs {price:.0f} is {ratio:.1f}x higher than "
                f"past average Rs {avg:.0f} — likely digit error")
    if ratio < 0.5:
        return (f"⚠ '{item_name[:30]}' price Rs {price:.0f} is {ratio:.1f}x lower than "
                f"past average Rs {avg:.0f} — likely digit error")
    return None


def pieces(qty, unit):
    return qty * 12 if unit == "dozen" else qty


def choose_unit(parsed, written):
    """Try 'pcs' and 'dozen' to see which matches the written total."""
    for unit in ("pcs", "dozen"):
        tot = sum(p["price"] * pieces(p["qty"], unit) for p in parsed)
        if written and abs(tot - written) <= max(1.0, 0.01 * written):
            return unit, tot
    return "pcs", sum(p["price"] * p["qty"] for p in parsed)


def detect_duplicate(supplier_name: str, phone: str, bill_date: str, exclude_id=None) -> dict | None:
    """Check if a bill with same supplier + date already exists."""
    if not (supplier_name or phone) or not bill_date:
        return None
    with conn() as c:
        sql = "SELECT id, supplier_name, bill_date, written_total FROM bills WHERE bill_date=?"
        args = [bill_date]
        if phone:
            sql += " AND phone=?"
            args.append(phone)
        elif supplier_name:
            sql += " AND lower(supplier_name)=lower(?)"
            args.append(supplier_name)
        if exclude_id:
            sql += " AND id != ?"
            args.append(exclude_id)
        row = c.execute(sql, args).fetchone()
    return dict(row) if row else None


def validate(ex: dict) -> dict:
    flags, parsed = [], []
    confs = ex.get("line_confidence") or []

    # Surface partial extraction errors (chunked processing)
    for err in ex.get("_partial_errors") or []:
        flags.append(f"partial extraction issue: {err}")

    for i, ln in enumerate(ex.get("lines") or []):
        q = _num(ln.get("qty_as_written"))
        # Use per-line confidence field, fall back to line_confidence array
        conf = ln.get("confidence")
        if conf is None:
            conf = confs[i] if i < len(confs) else None
        needs_review = ln.get("needs_review", False)

        if ln.get("price") is None or q is None:
            flags.append(f"line {i + 1} unreadable: {ln.get('raw', '')}")
            continue

        # Flag low-confidence items with item name for easy identification
        if needs_review:
            raw_name = ln.get("raw", "")[:30]
            flags.append(f"⚠ '{raw_name}' needs review (AI flagged)")
        elif conf is not None and conf < 0.8:
            raw_name = ln.get("raw", "")[:30]
            flags.append(f"⚠ '{raw_name}' low confidence ({conf:.0%})")

        item = {
            "raw": ln.get("raw", ""),
            "price": float(ln["price"]),
            "qty": q,
            "confidence": conf,
            "needs_review": needs_review,
            "sell_price": ln.get("sell_price"),
            "page_no": ln.get("page_no"),  # which page this was extracted from
        }
        parsed.append(item)

    written = ex.get("written_total")
    unit, computed = choose_unit(parsed, written)

    # ---- Auto-detect line-total vs unit-price format ----
    # If sum(price × qty) is way off but sum(price alone) is close to written total,
    # it means the AI treated LINE TOTALS as unit prices. Auto-correct.
    if written and parsed:
        sum_price_times_qty = sum(p["price"] * pieces(p["qty"], unit) for p in parsed)
        sum_prices_only = sum(p["price"] for p in parsed)
        ratio_pq = sum_price_times_qty / written if written > 0 else 1
        ratio_po = sum_prices_only / written if written > 0 else 1
        if ratio_pq > 3 and 0.8 <= ratio_po <= 1.2:
            # The first numbers are line totals, not unit prices
            for p in parsed:
                if p["qty"] and p["qty"] > 0:
                    p["price"] = round(p["price"] / pieces(p["qty"], unit), 2)
            # Recompute
            computed = sum(p["price"] * pieces(p["qty"], unit) for p in parsed)
            flags.append(f"ℹ Auto-corrected: first numbers were line totals, not unit prices. "
                        f"Divided each by quantity to get actual cost per piece.")
        elif ratio_pq > 3 and ratio_po < 0.2:
            # Sum of prices alone is also way off — some items might be line totals, some unit prices
            flags.append(f"⚠ Total mismatch is very large (items sum Rs {sum_price_times_qty:.0f} vs written Rs {written:.0f}). "
                        f"Some line items may have line-total format instead of unit-price — check each item against the image.")

    if written is None:
        flags.append("no written total found - verify manually")
    elif abs(computed - written) > max(1.0, 0.01 * written):
        flags.append(f"total mismatch: lines sum {computed:.0f} vs written {written:.0f}")

    phone = ex.get("phone")
    if phone:
        cleaned = re.sub(r"[\s\-]", "", phone)
        if not re.match(r"^(\+92|92|0)?3\d{9}$", cleaned):
            flags.append(f"phone looks odd: {phone}")

    for it in parsed:
        it["unit"] = unit
        it["line_total"] = it["price"] * pieces(it["qty"], unit)

    # ---- Post-extraction validation (catch obvious AI errors) ----
    if parsed and written:
        # Check 1: If computed total is wildly off from written (e.g. 10x), likely digit error
        ratio = computed / written if written > 0 else 1
        if ratio > 5 or ratio < 0.2:
            flags.append(f"⚠ Total mismatch is very large (items sum Rs {computed:.0f} vs written Rs {written:.0f}) — "
                        f"check prices and quantities against the image carefully.")
        # Check 2: Find items whose line_total is disproportionately large
        avg_line = computed / len(parsed) if parsed else 0
        for i, it in enumerate(parsed):
            if avg_line > 0 and it["line_total"] > avg_line * 10:
                raw_name = it["raw"][:30]
                flags.append(f"ℹ '{raw_name}' line total Rs {it['line_total']:.0f} is much higher than "
                            f"average Rs {avg_line:.0f} — verify price Rs {it['price']:.0f} × qty {it['qty']:.0f} "
                            f"matches the image (this may be correct for bulk purchases)")
            # Check 3: Price or qty of 0 is suspicious
            if it["price"] == 0:
                raw_name = it["raw"][:30]
                flags.append(f"⚠ '{raw_name}' has price=0 — likely unread, not actually free")
            if it["qty"] == 0:
                raw_name = it["raw"][:30]
                flags.append(f"⚠ '{raw_name}' has qty=0 — likely unread, check the image")
            # Check 4: Unreasonably large prices (> 100,000 for a single item is rare in wholesale)
            if it["price"] > 100000:
                raw_name = it["raw"][:30]
                flags.append(f"ℹ '{raw_name}' price Rs {it['price']:.0f} is unusually high — verify against image")
            # Check 5: Unreasonably large quantities (> 1000 pieces in one line is unusual)
            if it["qty"] > 1000:
                raw_name = it["raw"][:30]
                flags.append(f"ℹ '{raw_name}' qty {it['qty']:.0f} is unusually high — verify against image")
    # Check 6: Valid sell_price values (must be 250, 500, 750, or 1000)
    valid_sell_prices = {250, 500, 750, 1000}
    for it in parsed:
        sp = it.get("sell_price")
        if sp is not None and sp not in valid_sell_prices:
            # Snap to nearest valid category
            closest = min(valid_sell_prices, key=lambda v: abs(v - sp))
            raw_name = it["raw"][:30]
            flags.append(f"⚠ '{raw_name}' sell_price {sp} is not a valid category — "
                        f"snapped to {closest}, verify")
            it["sell_price"] = closest

    # Check 7: Historical price anomaly detection
    hist = get_historical_prices()
    for it in parsed:
        anomaly = check_price_anomaly(it["raw"], it["price"], hist)
        if anomaly:
            flags.append(anomaly)

    # Check 8: Date validation
    bill_date = ex.get("bill_date")
    if bill_date:
        from datetime import datetime, timedelta
        try:
            # Parse the date (handle various formats)
            d_str = str(bill_date).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    d = datetime.strptime(d_str, fmt)
                    break
                except ValueError:
                    continue
            else:
                d = None
            if d:
                now = datetime.now()
                if d > now + timedelta(days=7):
                    flags.append(f"⚠ Bill date ({d.strftime('%Y-%m-%d')}) is in the future — verify")
                elif d < now - timedelta(days=365):
                    flags.append(f"⚠ Bill date ({d.strftime('%Y-%m-%d')}) is over a year old — verify")
        except Exception as _e:
            logger.warning("Silent exception in validate.py: %s", _e, exc_info=True)
    return {
        "items": parsed,
        "unit": unit,
        "computed_total": computed,
        "written_total": written,
        "flags": flags,
        "phone": phone,
        "supplier_guess": ex.get("supplier_guess"),
        "bill_date": ex.get("bill_date"),
        "status": "confirmed" if (not flags and written is not None) else "review",
    }


# ---- Profit / margin calculations ----

def margin_color(margin: float) -> str:
    """Color code: green >= 30%, amber 20-30%, red < 20%."""
    if margin >= 0.30:
        return "green"
    if margin >= 0.20:
        return "amber"
    return "red"


def line_profit(price: float, qty: float, unit: str, sell_price: float) -> dict:
    """Calculate profit for a single line item."""
    p = pieces(qty, unit)
    cost = price * p
    revenue = sell_price * p
    profit = revenue - cost
    margin = profit / revenue if revenue > 0 else 0
    return {
        "pieces": p,
        "cost": round(cost, 2),
        "revenue": round(revenue, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "color": margin_color(margin),
    }
