"""v8.13.0 — Category operations for wholesale category-based selling.

Three new capabilities:

1. Supplier comparison per category
   For each category, list every supplier who has sold you that category
   (via confirmed bills) with their avg/last/min price + delta vs the
   shop's running avg_cost. Used to decide who to buy from next time.

2. Category cost-trend alerts
   Per category, track the running avg_cost over time and flag categories
   whose cost has risen >X% in 30 days without a corresponding sell-price
   increase. Surfaces in the dashboard + a dedicated report.

3. Stock write-offs
   See app/profit_cash.py::add_stock_writeoff() (added in this PR).
   This module exposes the read-side helpers used by the reports.
"""
from datetime import datetime, timedelta
from .db import conn


# ─── 1. Supplier comparison per category ──────────────────────────────

def supplier_comparison_by_category(category_id: int = None) -> list:
    """For each category (or one specific category), list every supplier who
    has sold you that category via confirmed bills. Returns a list of dicts:

        [{
            "category_id": 1, "category_code": "A", "category_name": "Budget",
            "sell_price": 250,
            "running_avg_cost": 80.0,        # current category_stock_state.avg_cost
            "suppliers": [
                {
                    "supplier_id": 5, "supplier_name": "ABC Traders",
                    "bill_count": 8, "total_qty": 1200,
                    "avg_price": 78.50, "last_price": 80.0, "min_price": 72.0,
                    "last_bill_date": "2026-08-12",
                    "delta_vs_running_avg": -1.50,   # negative = cheaper than current
                    "is_cheapest": true
                },
                ...
            ]
        }]

    The frontend uses this to render a "Buy from X — Y% cheaper" recommendation.
    """
    # v8.13.2 SCHEMA C4 FIX: Eliminated N+1 by inlining last_price as a
    # correlated subquery in the main SELECT. The previous code ran a
    # separate query per (category, supplier) pair inside the Python loop
    # — 20 categories × 5 suppliers = 100 sub-queries per call. Now it's a
    # single SQL roundtrip.
    sql = """
        SELECT pc.id AS category_id, pc.code AS category_code, pc.name AS category_name,
               pc.sell_price,
               COALESCE(css.current_avg_cost, 0) AS running_avg_cost,
               COALESCE(css.current_qty, 0) AS current_qty,
               s.id AS supplier_id, s.name AS supplier_name,
               COUNT(b.id) AS bill_count,
               SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_qty,
               AVG(bi.price) AS avg_price,
               MIN(bi.price) AS min_price,
               MAX(b.bill_date) AS last_bill_date,
               /* v8.13.2: correlated subquery for last_price — eliminates the N+1 */
               (SELECT bi2.price
                  FROM bill_items bi2
                  JOIN bills b2 ON bi2.bill_id = b2.id
                 WHERE bi2.category_id = pc.id
                   AND b2.supplier_id = s.id
                   AND b2.status = 'confirmed'
                   AND b2.deleted_at IS NULL
                 ORDER BY b2.bill_date DESC, bi2.id DESC
                 LIMIT 1) AS last_price
        FROM bill_items bi
        JOIN bills b ON bi.bill_id = b.id
        JOIN suppliers s ON b.supplier_id = s.id
        JOIN price_categories pc ON bi.category_id = pc.id
        LEFT JOIN category_stock_state css ON pc.id = css.category_id
        WHERE b.status = 'confirmed'
          AND b.deleted_at IS NULL
          AND s.deleted_at IS NULL
          AND bi.category_id IS NOT NULL
    """
    params = []
    if category_id is not None:
        sql += " AND pc.id = ?"
        params.append(category_id)
    sql += """
        GROUP BY pc.id, s.id
        ORDER BY pc.sort_order, pc.code, avg_price ASC
    """
    with conn() as c:
        rows = c.execute(sql, params).fetchall()

    # Group by category — single pass, no sub-queries
    by_cat = {}
    for r in rows:
        cid = r["category_id"]
        if cid not in by_cat:
            by_cat[cid] = {
                "category_id": cid,
                "category_code": r["category_code"],
                "category_name": r["category_name"],
                "sell_price": r["sell_price"],
                "running_avg_cost": round(r["running_avg_cost"] or 0, 2),
                "current_qty": r["current_qty"] or 0,
                "suppliers": [],
            }
        # v8.13.2: last_price comes directly from the correlated subquery
        last_price = float(r["last_price"] if r["last_price"] is not None else 0)
        delta = round(last_price - float(r["running_avg_cost"] or 0), 2)

        by_cat[cid]["suppliers"].append({
            "supplier_id": r["supplier_id"],
            "supplier_name": r["supplier_name"],
            "bill_count": r["bill_count"],
            "total_qty": r["total_qty"] or 0,
            "avg_price": round(float(r["avg_price"] or 0), 2),
            "last_price": round(last_price, 2),
            "min_price": round(float(r["min_price"] or 0), 2),
            "last_bill_date": r["last_bill_date"],
            "delta_vs_running_avg": delta,
            "is_cheaper_than_avg": delta < 0,
        })

    # Mark the cheapest supplier per category
    result = list(by_cat.values())
    for cat in result:
        if cat["suppliers"]:
            cheapest = min(cat["suppliers"], key=lambda s: s["avg_price"])
            cheapest["is_cheapest"] = True
            for s in cat["suppliers"]:
                if s is not cheapest:
                    s["is_cheapest"] = False
        else:
            for s in cat["suppliers"]:
                s["is_cheapest"] = False
    return result


def check_bill_cost_vs_cheapest_supplier(items: list) -> list:
    """Given a list of {category_id, price} dicts from a bill being confirmed,
    return a list of warnings for items whose price is HIGHER than the cheapest
    historical supplier for that category.

    Used by the bill-confirm endpoint to flag cost overruns BEFORE the user
    commits the bill — gives them a chance to negotiate or cancel.
    """
    warnings = []
    if not items:
        return warnings
    with conn() as c:
        for item in items:
            cat_id = item.get("category_id")
            new_price = float(item.get("price") or 0)
            if not cat_id or new_price <= 0:
                continue
            # Find the cheapest historical supplier for this category
            row = c.execute(
                """
                SELECT s.name AS supplier_name, AVG(bi.price) AS avg_price,
                       MIN(bi.price) AS min_price, MAX(b.bill_date) AS last_bill_date
                FROM bill_items bi
                JOIN bills b ON bi.bill_id = b.id
                JOIN suppliers s ON b.supplier_id = s.id
                WHERE bi.category_id = ?
                  AND b.status = 'confirmed'
                  AND b.deleted_at IS NULL
                  AND s.deleted_at IS NULL
                GROUP BY s.id
                ORDER BY avg_price ASC
                LIMIT 1
                """,
                (cat_id,)
            ).fetchone()
            if not row:
                continue  # no history — nothing to compare against
            cheapest_avg = float(row["avg_price"] or 0)
            cheapest_min = float(row["min_price"] or 0)
            cheapest_name = row["supplier_name"]
            # Flag if the new price is >5% above the cheapest historical avg
            if cheapest_avg > 0:
                pct_higher = ((new_price - cheapest_avg) / cheapest_avg) * 100
                if pct_higher > 5:
                    # Fetch the category code for a friendlier message
                    cat_row = c.execute(
                        "SELECT code, name, sell_price FROM price_categories WHERE id = ?",
                        (cat_id,)
                    ).fetchone()
                    cat_label = f"{cat_row['code']} ({cat_row['name']})" if cat_row else f"#{cat_id}"
                    warnings.append({
                        "category_id": cat_id,
                        "category_label": cat_label,
                        "new_price": round(new_price, 2),
                        "cheapest_supplier": cheapest_name,
                        "cheapest_avg_price": round(cheapest_avg, 2),
                        "cheapest_min_price": round(cheapest_min, 2),
                        "last_cheapest_bill_date": row["last_bill_date"],
                        "pct_higher": round(pct_higher, 1),
                        "extra_cost_per_unit": round(new_price - cheapest_avg, 2),
                        "message": (
                            f"Paying Rs {new_price:.2f} for {cat_label} — "
                            f"{cheapest_name} sold you the same category at avg Rs {cheapest_avg:.2f} "
                            f"(min Rs {cheapest_min:.2f}). You're paying {pct_higher:.1f}% more per unit."
                        ),
                    })
    return warnings


# ─── 2. Category cost-trend alerts ───────────────────────────────────

def category_cost_trend_alerts(days: int = 30, threshold_pct: float = 5.0) -> list:
    """For each category, compute the avg_cost trend over the last `days` days.

    Compares the rolling avg_cost from 30 days ago to the current avg_cost,
    and flags categories whose cost has risen > `threshold_pct`% in that
    window WITHOUT a corresponding sell-price increase.

    Returns a list of dicts:
        [{
            "category_id": 1, "category_code": "A", "category_name": "Budget",
            "sell_price": 250,
            "avg_cost_30d_ago": 75.0, "avg_cost_now": 82.5,
            "cost_change_pct": 10.0,
            "margin_30d_ago_pct": 70.0, "margin_now_pct": 67.0,
            "margin_drop_pct": 3.0,
            "alert_severity": "warning",   # "info" | "warning" | "critical"
            "message": "Category A avg_cost up 10% (Rs 75 → Rs 82.5) in 30 days.
                       At sell price Rs 250, margin dropped from 70% to 67%."
        }]
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    alerts = []
    with conn() as c:
        # Current state
        current_rows = c.execute(
            "SELECT pc.id, pc.code, pc.name, pc.sell_price, "
            "COALESCE(css.current_avg_cost, 0) AS avg_cost_now, "
            "COALESCE(css.current_qty, 0) AS current_qty "
            "FROM price_categories pc "
            "LEFT JOIN category_stock_state css ON pc.id = css.category_id "
            "WHERE pc.active = 1 "
            "ORDER BY pc.sort_order, pc.code"
        ).fetchall()
        for row in current_rows:
            cat_id = row["id"]
            avg_cost_now = float(row["avg_cost_now"] or 0)
            sell_price = float(row["sell_price"] or 0)
            # Skip categories with no current stock state
            if avg_cost_now <= 0 or sell_price <= 0:
                continue
            # Compute avg_cost from bills confirmed BEFORE the cutoff date
            old_row = c.execute(
                """
                SELECT AVG(bi.price) AS avg_cost_old, MAX(b.bill_date) AS last_old_bill_date
                FROM bill_items bi
                JOIN bills b ON bi.bill_id = b.id
                WHERE bi.category_id = ?
                  AND b.status = 'confirmed'
                  AND b.deleted_at IS NULL
                  AND b.bill_date <= ?
                """,
                (cat_id, cutoff)
            ).fetchone()
            avg_cost_old = float(old_row["avg_cost_old"] or 0) if old_row else 0
            if avg_cost_old <= 0:
                continue  # no history before cutoff — can't compute trend

            cost_change_pct = ((avg_cost_now - avg_cost_old) / avg_cost_old) * 100
            # Skip categories whose cost hasn't moved meaningfully
            if abs(cost_change_pct) < 0.5:
                continue

            margin_old_pct = ((sell_price - avg_cost_old) / sell_price) * 100 if sell_price > 0 else 0
            margin_now_pct = ((sell_price - avg_cost_now) / sell_price) * 100 if sell_price > 0 else 0
            margin_drop_pct = margin_old_pct - margin_now_pct

            # Severity
            if cost_change_pct > threshold_pct * 2:
                severity = "critical"
            elif cost_change_pct > threshold_pct:
                severity = "warning"
            else:
                severity = "info"

            direction = "up" if cost_change_pct > 0 else "down"
            arrow = "↑" if cost_change_pct > 0 else "↓"
            msg = (
                f"Category {row['code']} avg_cost {arrow} {abs(cost_change_pct):.1f}% "
                f"(Rs {avg_cost_old:.2f} → Rs {avg_cost_now:.2f}) in {days} days. "
                f"At sell price Rs {sell_price:.0f}, margin "
                f"{'dropped' if margin_drop_pct > 0 else 'gained'} {abs(margin_drop_pct):.1f}pp "
                f"({margin_old_pct:.1f}% → {margin_now_pct:.1f}%)."
            )
            alerts.append({
                "category_id": cat_id,
                "category_code": row["code"],
                "category_name": row["name"],
                "sell_price": sell_price,
                "avg_cost_30d_ago": round(avg_cost_old, 2),
                "avg_cost_now": round(avg_cost_now, 2),
                "cost_change_pct": round(cost_change_pct, 1),
                "margin_30d_ago_pct": round(margin_old_pct, 1),
                "margin_now_pct": round(margin_now_pct, 1),
                "margin_drop_pct": round(margin_drop_pct, 1),
                "alert_severity": severity,
                "direction": direction,
                "message": msg,
            })
    # Sort: most cost increase first (worst erosion on top)
    alerts.sort(key=lambda a: a["cost_change_pct"], reverse=True)
    return alerts


# ─── 3. Stock write-offs (read-side helpers; write-side is in profit_cash.py) ────

def list_stock_writeoffs(month: str = "", limit: int = 200) -> list:
    """List stock write-offs, most recent first. If month (YYYY-MM) given,
    filter to that month.

    v8.13.2: SCALABILITY — rewrote strftime to range form so idx_stock_writeoffs_created is used.
    """
    from .profit_engine import month_to_range
    sql = (
        "SELECT sw.id, sw.category_id, pc.code AS category_code, pc.name AS category_name, "
        "pc.sell_price, sw.qty, sw.unit_cost, sw.loss_value, sw.reason, sw.notes, "
        "sw.manager_pin_verified, sw.created_at "
        "FROM stock_writeoffs sw "
        "LEFT JOIN price_categories pc ON sw.category_id = pc.id "
    )
    params = []
    if month:
        # v8.13.2: range form uses idx_stock_writeoffs_created
        ts_start, ts_end = month_to_range(month)
        sql += "WHERE sw.created_at >= ? AND sw.created_at < ? "
        params.extend([ts_start, ts_end])
    sql += "ORDER BY sw.created_at DESC LIMIT ?"
    params.append(limit)
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def stock_writeoff_summary(month: str = "") -> dict:
    """Summary of stock write-offs for the monthly P&L 'Shrinkage' line.
    Returns: {month, total_loss_value, count, by_reason: [{reason, total_loss, count}]}

    v8.13.2: SCALABILITY — rewrote strftime to range form.
    """
    from .profit_engine import month_to_range
    if not month:
        month = datetime.now().strftime("%Y-%m")
    ts_start, ts_end = month_to_range(month)
    with conn() as c:
        total_row = c.execute(
            "SELECT COALESCE(SUM(loss_value), 0) AS total, COUNT(*) AS count "
            "FROM stock_writeoffs WHERE created_at >= ? AND created_at < ?",
            (ts_start, ts_end)
        ).fetchone()
        by_reason = c.execute(
            "SELECT reason, COALESCE(SUM(loss_value), 0) AS total, COUNT(*) AS count "
            "FROM stock_writeoffs WHERE created_at >= ? AND created_at < ? "
            "GROUP BY reason ORDER BY total DESC",
            (ts_start, ts_end)
        ).fetchall()
    return {
        "month": month,
        "total_loss_value": round(float(total_row["total"] or 0), 2),
        "count": total_row["count"],
        "by_reason": [
            {"reason": r["reason"], "total_loss": round(float(r["total"] or 0), 2), "count": r["count"]}
            for r in by_reason
        ],
    }
