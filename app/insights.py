"""Business insights: ABC analysis, dead stock, price comparison,
margin erosion, alerts, supplier reliability, simple forecasting."""
import logging
from collections import defaultdict
import statistics
from statistics import mean
from .db import conn
from .validate import pieces

logger = logging.getLogger(__name__)


# ---------- ABC / Pareto analysis ----------

def abc_analysis() -> dict:
    """Classify items by revenue contribution: A (top 80%), B (next 15%), C (bottom 5%)."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, bi.price, bi.qty, bi.unit, bi.category_id, "
            "pc.sell_price, COUNT(*) AS bill_count "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "GROUP BY bi.raw ORDER BY SUM(COALESCE(pc.sell_price,0) * "
            "CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) DESC"
        ).fetchall()

    items = []
    for r in rows:
        p = pieces(r["qty"], r["unit"])
        revenue = (r["sell_price"] or 0) * p
        cost = (r["price"] or 0) * p
        profit = revenue - cost
        margin = profit / revenue if revenue > 0 else 0
        items.append({
            "raw": r["raw"],
            "total_qty": round(p, 1),
            "bill_count": r["bill_count"],
            "revenue": round(revenue, 2),
            "cost": round(cost, 2),
            "profit": round(profit, 2),
            "margin": round(margin, 2),
            "class": None,
        })

    total_rev = sum(i["revenue"] for i in items)
    cumulative = 0
    for i in items:
        cumulative += i["revenue"]
        share = cumulative / total_rev if total_rev > 0 else 0
        if share <= 0.80:
            i["class"] = "A"
        elif share <= 0.95:
            i["class"] = "B"
        else:
            i["class"] = "C"

    class_a = [i for i in items if i["class"] == "A"]
    class_b = [i for i in items if i["class"] == "B"]
    class_c = [i for i in items if i["class"] == "C"]

    return {
        "items": items,
        "summary": {
            "A": {"count": len(class_a), "revenue": round(sum(i["revenue"] for i in class_a), 2)},
            "B": {"count": len(class_b), "revenue": round(sum(i["revenue"] for i in class_b), 2)},
            "C": {"count": len(class_c), "revenue": round(sum(i["revenue"] for i in class_c), 2)},
        },
    }


# ---------- Dead stock ----------

def dead_stock(days: int = 60) -> dict:
    """Items not purchased in N days."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, MAX(b.bill_date) AS last_seen, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_qty, "
            "AVG(bi.price) AS avg_cost "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "GROUP BY bi.raw "
            f"HAVING date(last_seen) < date('now','-{int(days)} days') "
            "ORDER BY last_seen DESC"
        ).fetchall()

    items = [{
        "raw": r["raw"],
        "last_seen": r["last_seen"],
        "total_qty": round(r["total_qty"], 1),
        "tied_capital": round((r["avg_cost"] or 0) * r["total_qty"], 2),
    } for r in rows]
    return {"items": items, "total_tied_capital": round(sum(i["tied_capital"] for i in items), 2)}


# ---------- Price comparison across suppliers ----------

def price_comparison() -> dict:
    """Find items bought from 2+ suppliers and show price variance."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, bi.price, bi.qty, bi.unit, b.supplier_name, b.bill_date "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.raw IS NOT NULL AND TRIM(bi.raw) <> '' "
            "AND bi.price IS NOT NULL AND b.supplier_name IS NOT NULL"
        ).fetchall()

    by_item = defaultdict(list)
    for r in rows:
        unit_price = r["price"] / pieces(1, r["unit"])  # normalize to per-piece
        by_item[r["raw"]].append({
            "supplier": r["supplier_name"],
            "unit_price": round(unit_price, 2),
            "bill_date": r["bill_date"],
        })

    items = []
    for raw, suppliers in by_item.items():
        if len({s["supplier"] for s in suppliers}) < 2:
            continue
        prices = [s["unit_price"] for s in suppliers]
        best = min(suppliers, key=lambda x: x["unit_price"])
        worst = max(suppliers, key=lambda x: x["unit_price"])
        savings = worst["unit_price"] - best["unit_price"]
        savings_pct = savings / worst["unit_price"] if worst["unit_price"] > 0 else 0
        items.append({
            "raw": raw,
            "best_supplier": best["supplier"],
            "best_price": best["unit_price"],
            "worst_supplier": worst["supplier"],
            "worst_price": worst["unit_price"],
            "savings": round(savings, 2),
            "savings_pct": round(savings_pct, 2),
            "suppliers": suppliers,
        })
    items.sort(key=lambda x: x["savings"], reverse=True)
    return {"items": items}


# ---------- Margin erosion ----------

def margin_erosion() -> dict:
    """Detect items whose cost increased >20% over time."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, bi.price, bi.unit, b.bill_date, b.supplier_name "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.raw IS NOT NULL AND bi.price IS NOT NULL "
            "AND bi.raw IN (SELECT raw FROM bill_items GROUP BY raw HAVING COUNT(DISTINCT price) > 1) "
            "ORDER BY bi.raw, b.bill_date"
        ).fetchall()

    by_item = defaultdict(list)
    for r in rows:
        per_piece = r["price"] / pieces(1, r["unit"])
        by_item[r["raw"]].append({
            "price": round(per_piece, 2),
            "date": r["bill_date"],
            "supplier": r["supplier_name"],
        })

    alerts = []
    for raw, history in by_item.items():
        if len(history) < 2:
            continue
        first, last = history[0], history[-1]
        if first["price"] == 0:
            continue
        change = (last["price"] - first["price"]) / first["price"]
        if abs(change) > 0.20:
            alerts.append({
                "raw": raw,
                "first_price": first["price"],
                "last_price": last["price"],
                "change_pct": round(change, 2),
                "direction": "up" if change > 0 else "down",
                "first_date": first["date"],
                "last_date": last["date"],
                "history": history,
            })
    alerts.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return {"alerts": alerts}


# ---------- Active alerts ----------

def active_alerts() -> dict:
    """Aggregate critical / warning / info alerts."""
    alerts = {"critical": [], "warning": [], "info": []}

    with conn() as c:
        # Critical: margin < 10%
        low_margin = c.execute(
            "SELECT bi.raw, AVG(bi.price) AS cost, AVG(pc.sell_price) AS sell "
            "FROM bill_items bi "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "JOIN bills b ON bi.bill_id = b.id WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "GROUP BY bi.raw HAVING sell > 0 AND (sell - cost) / sell < 0.10"
        ).fetchall()
        for r in low_margin:
            margin = (r["sell"] - r["cost"]) / r["sell"]
            alerts["critical"].append({
                "type": "low_margin",
                "item": r["raw"],
                "message": f"Margin critically low on '{r['raw']}': {margin*100:.0f}%",
                "action": "Reconsider pricing or find a cheaper supplier.",
            })

        # Critical: credit overdue > 60 days
        overdue = c.execute(
            "SELECT b.id, b.supplier_name, b.written_total, b.bill_date, b.credit_due_date "
            "FROM bills b WHERE b.payment_status='credit' AND b.status='confirmed' AND deleted_at IS NULL "
            "AND credit_due_date IS NOT NULL "
            "AND date(credit_due_date) < date('now','-60 days')"
        ).fetchall()
        for r in overdue:
            alerts["critical"].append({
                "type": "credit_overdue",
                "bill_id": r["id"],
                "message": f"Bill #{r['id']} from {r['supplier_name']} (Rs {r['written_total']:.0f}) is overdue >60 days",
                "action": "Send WhatsApp reminder to supplier.",
            })

        # Warning: dead stock
        ds = dead_stock(60)
        if ds["items"]:
            alerts["warning"].append({
                "type": "dead_stock",
                "message": f"{len(ds['items'])} items not purchased in 60+ days",
                "action": "Review and consider discounting.",
                "detail": ds["items"][:10],
            })

        # Warning: credit overdue > 30 days
        overdue30 = c.execute(
            "SELECT id, supplier_name, written_total FROM bills "
            "WHERE payment_status='credit' AND status='confirmed' "
            "AND credit_due_date IS NOT NULL "
            "AND date(credit_due_date) < date('now','-30 days') "
            "AND date(credit_due_date) >= date('now','-60 days')"
        ).fetchall()
        for r in overdue30:
            alerts["warning"].append({
                "type": "credit_warning",
                "bill_id": r["id"],
                "message": f"Bill #{r['id']} from {r['supplier_name']} (Rs {r['written_total']:.0f}) overdue 30-60 days",
                "action": "Follow up with supplier.",
            })

        # Warning: margin < 15%
        low15 = c.execute(
            "SELECT bi.raw, AVG(bi.price) AS cost, AVG(pc.sell_price) AS sell "
            "FROM bill_items bi LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "JOIN bills b ON bi.bill_id = b.id WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "GROUP BY bi.raw HAVING sell > 0 AND (sell - cost) / sell BETWEEN 0.10 AND 0.15"
        ).fetchall()
        for r in low15:
            margin = (r["sell"] - r["cost"]) / r["sell"]
            alerts["warning"].append({
                "type": "margin_warning",
                "item": r["raw"],
                "message": f"Margin below 15% on '{r['raw']}': {margin*100:.0f}%",
                "action": "Consider moving to a higher price tier.",
            })

    return alerts


# ---------- Supplier reliability ----------

def supplier_reliability(supplier_id: int) -> dict:
    """Compute a 0-100 reliability score for a supplier."""
    with conn() as c:
        bills = c.execute(
            "SELECT id, written_total, computed_total, payment_status, bill_date "
            "FROM bills WHERE supplier_id=? AND status='confirmed' AND deleted_at IS NULL", (supplier_id,)
        ).fetchall()
        name = c.execute("SELECT name FROM suppliers WHERE id=?", (supplier_id,)).fetchone()

    if not bills:
        return {"name": name["name"] if name else "", "score": 0, "reason": "no confirmed bills"}

    n = len(bills)
    # Frequency score: more bills = stronger relationship (cap at 20 bills = 100%)
    freq_score = min(n / 20, 1.0) * 40

    # Payment consistency: less outstanding = more reliable
    outstanding = sum(b["written_total"] or 0 for b in bills if b["payment_status"] == "credit")
    total = sum(b["written_total"] or 0 for b in bills)
    pay_score = (1 - (outstanding / total) if total > 0 else 1.0) * 30

    # Price stability: lower variance in bill totals = more reliable
    totals = [b["written_total"] or 0 for b in bills]
    if len(totals) > 1 and mean(totals) > 0:
        cv = statistics.stdev(totals) / mean(totals)  # coefficient of variation
        price_score = max(0, 1 - cv) * 30
    else:
        price_score = 30

    score = int(freq_score + pay_score + price_score)
    return {
        "name": name["name"] if name else "",
        "score": min(score, 100),
        "components": {
            "frequency": round(freq_score, 1),
            "payment": round(pay_score, 1),
            "price_stability": round(price_score, 1),
        },
        "bill_count": n,
        "total_spent": round(total, 2),
        "outstanding": round(outstanding, 2),
    }


# ---------- Simple forecasting (exponential smoothing) ----------

def forecast(item_raw: str = None, periods: int = 3) -> dict:
    """Simple exponential smoothing forecast of monthly spend."""
    with conn() as c:
        if item_raw:
            rows = c.execute(
                "SELECT strftime('%Y-%m', b.bill_date) AS month, "
                "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS qty "
                "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
                "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.raw=? "
                "GROUP BY month ORDER BY month", (item_raw,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT strftime('%Y-%m', bill_date) AS month, "
                "SUM(COALESCE(written_total, computed_total)) AS qty "
                "FROM bills WHERE status='confirmed' AND deleted_at IS NULL AND bill_date IS NOT NULL "
                "GROUP BY month ORDER BY month"
            ).fetchall()

    if len(rows) < 2:
        return {"history": [dict(r) for r in rows], "forecast": [], "method": "insufficient_data"}

    series = [r["qty"] for r in rows]

    # Pick alpha based on data length
    if len(series) >= 12:
        alpha = 0.3
        method = "exponential_smoothing_alpha_0.3"
    else:
        alpha = 0.5
        method = "exponential_smoothing_alpha_0.5"

    # Fit
    level = series[0]
    for v in series[1:]:
        level = alpha * v + (1 - alpha) * level

    # Forecast flat for N periods
    forecast_vals = [round(level, 2)] * periods
    history = [{"month": r["month"], "value": round(r["qty"], 2)} for r in rows]

    # Build forecast month labels
    last_month = rows[-1]["month"]
    y, m = last_month.split("-")
    y, m = int(y), int(m)
    labels = []
    for _ in range(periods):
        m += 1
        if m > 12:
            m = 1
            y += 1
        labels.append(f"{y:04d}-{m:02d}")

    forecast_out = [{"month": labels[i], "value": forecast_vals[i]} for i in range(periods)]
    return {"history": history, "forecast": forecast_out, "method": method}


# ---------- Sparklines for dashboard KPIs ----------

def sparklines(days: int = 14) -> dict:
    """Return small time-series for each dashboard KPI for sparkline rendering.

    Returns:
      {
        "bills": [count per day for last N days],
        "spend": [total spend per day],
        "outstanding": [cumulative outstanding credit per day],
        "suppliers": [cumulative supplier count per day],
        "labels": [date strings for x-axis]
      }
    """
    from datetime import datetime, timedelta

    today = datetime.now().date()
    start = today - timedelta(days=days - 1)
    start_str = start.isoformat()
    end_str = today.isoformat()

    with conn() as c:
        # Daily bill count + spend (confirmed bills only)
        rows = c.execute(
            "SELECT bill_date, COUNT(*) AS n, "
            "SUM(COALESCE(written_total, computed_total, 0)) AS spend "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND bill_date IS NOT NULL AND bill_date >= ? AND bill_date <= ? "
            "GROUP BY bill_date ORDER BY bill_date",
            (start_str, end_str),
        ).fetchall()
        daily = {r["bill_date"][:10]: {"n": r["n"], "spend": r["spend"] or 0} for r in rows}

        # Cumulative supplier count over time (snapshot at each date)
        # v8.11.1: exclude soft-deleted suppliers
        supplier_rows = c.execute(
            "SELECT DATE(created_at) AS d, COUNT(*) AS n FROM suppliers "
            "WHERE created_at IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY d ORDER BY d"
        ).fetchall()
        # Build cumulative
        sup_by_date = {}
        running = 0
        for r in supplier_rows:
            running += r["n"]
            sup_by_date[r["d"]] = running

        # Outstanding credit: cumulative sum of credit bills up to each date
        credit_rows = c.execute(
            "SELECT bill_date, COALESCE(written_total, computed_total, 0) AS amt "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL AND payment_status='credit' "
            "AND bill_date IS NOT NULL AND bill_date <= ? ORDER BY bill_date",
            (end_str,),
        ).fetchall()

    # Build per-day series
    bills_series, spend_series = [], []
    outstanding_series, suppliers_series = [], []
    labels = []
    outstanding_running = 0
    suppliers_running = 0
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        labels.append(d[5:])  # MM-DD
        info = daily.get(d, {"n": 0, "spend": 0})
        bills_series.append(info["n"])
        spend_series.append(round(info["spend"], 0))

        # Add any credit bills dated on this day to running outstanding
        for r in credit_rows:
            if r["bill_date"][:10] == d:
                outstanding_running += r["amt"]
        outstanding_series.append(round(outstanding_running, 0))

        # Suppliers cumulative up to this date
        for sd, n in sup_by_date.items():
            if sd <= d and n > suppliers_running:
                suppliers_running = n
        suppliers_series.append(suppliers_running)

    return {
        "bills": bills_series,
        "spend": spend_series,
        "outstanding": outstanding_series,
        "suppliers": suppliers_series,
        "labels": labels,
    }


# ---------- Recurring bill detection ----------
def recurring_reminders() -> list:
    """Detect suppliers you usually buy from regularly but haven't recently.

    For each supplier with ≥3 confirmed bills, compute the average gap between bills.
    If it's been longer than 1.5x the average gap since the last bill, flag it.
    """
    from datetime import datetime, timedelta
    reminders = []
    try:
        with conn() as c:
            # Get all confirmed bills grouped by supplier, ordered by date
            rows = c.execute(
                "SELECT supplier_id, supplier_name, bill_date "
                "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
                "AND supplier_id IS NOT NULL AND bill_date IS NOT NULL "
                "ORDER BY supplier_id, bill_date"
            ).fetchall()
        # Group by supplier
        from collections import defaultdict
        by_supplier = defaultdict(list)
        for r in rows:
            by_supplier[r["supplier_id"]].append({
                "name": r["supplier_name"],
                "date": r["bill_date"][:10] if r["bill_date"] else None,
            })
        now = datetime.now().date()
        for sup_id, bills in by_supplier.items():
            if len(bills) < 3:
                continue  # Need at least 3 bills to establish a pattern
            # Parse dates and compute gaps
            dates = []
            for b in bills:
                try:
                    d = datetime.fromisoformat(b["date"]).date()
                    dates.append(d)
                except Exception:
                    continue
            if len(dates) < 3:
                continue
            dates.sort()
            gaps = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
            avg_gap = sum(gaps) / len(gaps)
            if avg_gap <= 0:
                continue
            last_date = dates[-1]
            days_since = (now - last_date).days
            # Flag if it's been >1.5x the average gap
            if days_since > avg_gap * 1.5 and days_since > 7:
                reminders.append({
                    "supplier_id": sup_id,
                    "supplier_name": bills[0]["name"],
                    "avg_gap_days": round(avg_gap),
                    "last_bill_date": last_date.isoformat(),
                    "days_since": days_since,
                    "expected_by": (last_date + timedelta(days=int(avg_gap))).isoformat(),
                    "bill_count": len(bills),
                })
        # Sort by days_since descending (most overdue first)
        reminders.sort(key=lambda x: x["days_since"], reverse=True)
    except Exception as _e:
        logger.warning("Silent exception in insights.py: %s", _e, exc_info=True)
    return reminders[:10]  # Top 10


# ---------- Monthly close ----------
def monthly_close(year: int, month: int) -> dict:
    """Snapshot all bills and items for a specific month for accounting closure."""
    from .validate import pieces
    month_str = f"{year:04d}-{month:02d}"
    with conn() as c:
        bills = c.execute(
            "SELECT * FROM bills WHERE deleted_at IS NULL AND status='confirmed' "
            "AND bill_date IS NOT NULL AND strftime('%Y-%m', bill_date) = ? "
            "ORDER BY bill_date, id",
            (month_str,)
        ).fetchall()
        items = []
        for b in bills:
            bill_items = c.execute(
                "SELECT bi.*, pc.name AS cat_name, pc.sell_price "
                "FROM bill_items bi LEFT JOIN price_categories pc ON bi.category_id = pc.id "
                "WHERE bi.bill_id=?", (b["id"],)
            ).fetchall()
            for it in bill_items:
                d = dict(it)
                d["bill_id"] = b["id"]
                d["supplier_name"] = b["supplier_name"]
                d["bill_date"] = b["bill_date"]
                items.append(d)
    # Summary
    total_spent = sum(b["written_total"] or b["computed_total"] or 0 for b in bills)
    total_credit = sum(
        (b["written_total"] or b["computed_total"] or 0)
        for b in bills if b["payment_status"] == "credit"
    )
    total_paid = sum(
        (b["written_total"] or b["computed_total"] or 0)
        for b in bills if b["payment_status"] == "paid"
    )
    suppliers = list(set(b["supplier_name"] for b in bills if b["supplier_name"]))
    # By category
    from collections import defaultdict
    by_cat = defaultdict(lambda: {"pieces": 0, "cost": 0, "revenue": 0, "items": 0})
    for it in items:
        cat = it["cat_name"] or "Uncategorized"
        p = pieces(it["qty"], it["unit"])
        sell = it["sell_price"] or 0
        by_cat[cat]["pieces"] += p
        by_cat[cat]["cost"] += it["line_total"] or 0
        by_cat[cat]["revenue"] += sell * p
        by_cat[cat]["items"] += 1
    return {
        "month": month_str,
        "total_bills": len(bills),
        "total_spent": round(total_spent, 2),
        "total_paid": round(total_paid, 2),
        "total_credit": round(total_credit, 2),
        "suppliers": suppliers,
        "supplier_count": len(suppliers),
        "items": items,
        "by_category": dict(by_cat),
        "bills": [dict(b) for b in bills],
    }


def monthly_close_with_audit(year: int, month: int) -> dict:
    """v8.2: Run monthly close, then trigger a month-end audit run."""
    close_result = monthly_close(year, month)
    # Trigger the auditor for this period
    try:
        from .auditor import run_audit
        audit_result = run_audit(trigger="month_end", period=f"{year:04d}-{month:02d}")
        close_result["audit"] = {
            "run_id": audit_result["run_id"],
            "findings_count": audit_result["findings_count"],
            "critical_count": audit_result["critical_count"],
            "warning_count": audit_result["warning_count"],
            "info_count": audit_result["info_count"],
        }
    except Exception as e:
        close_result["audit"] = {"error": str(e)}
    return close_result
