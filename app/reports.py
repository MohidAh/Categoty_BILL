"""Report generation: monthly summary, profit estimate, supplier ranking, billwise, category-wise."""
import statistics
from collections import defaultdict
from .db import conn
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant
from .validate import pieces

# Category code mapping
CAT_CODES = {250: "A", 500: "B", 750: "C", 1000: "D"}


def get_cat_code(sell_price):
    """Map sell price to category code A/B/C/D."""
    if sell_price is None:
        return "—"
    return CAT_CODES.get(int(sell_price), "—")


def monthly_summary(start_date: str, end_date: str) -> dict:
    """Daily spend trend + KPIs over a date range. Only confirmed, non-deleted bills."""
    with conn() as c:
        rows = c.execute(
            "SELECT bill_date, written_total, computed_total, id, supplier_name "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND bill_date >= ? AND bill_date <= ? "
            "AND bill_date IS NOT NULL ORDER BY bill_date",
            (start_date, end_date),
        ).fetchall()

    by_day = defaultdict(lambda: {"bills": 0, "spend": 0.0})
    for r in rows:
        d = r["bill_date"][:10]
        total = r["written_total"] or r["computed_total"] or 0
        by_day[d]["bills"] += 1
        by_day[d]["spend"] += total

    days = sorted(by_day.keys())
    series = [
        {"date": d, "bills": by_day[d]["bills"], "spend": round(by_day[d]["spend"], 2)}
        for d in days
    ]
    total_spend = sum(d["spend"] for d in series)
    total_bills = sum(d["bills"] for d in series)
    n_days = len(series) if series else 1

    return {
        "start": start_date,
        "end": end_date,
        "series": series,
        "kpis": {
            "total_spend": round(total_spend, 2),
            "total_bills": total_bills,
            "avg_per_bill": round(total_spend / total_bills, 2) if total_bills else 0,
            "avg_per_day": round(total_spend / n_days, 2),
        },
    }


def billwise_report(start_date: str, end_date: str, status: str = "all",
                     include_items: bool = False) -> dict:
    """Bill-by-bill breakdown: each bill with totals + categories + items.

    v8.5.2: added `status` filter so the report can show:
      - status="all"       → both 'confirmed' AND 'review' bills (default — user
                              wants to see ALL uploaded bills, not just confirmed)
      - status="confirmed" → only confirmed bills (old behavior)
      - status="review"    → only pending review bills

    v8.7: added `include_items` parameter (default False).
      - include_items=False (default): returns bill headers + precomputed
        aggregates (item_count, category_count, total_cost, total_revenue,
        total_profit) WITHOUT the items[] array. Use this for the master list
        in the lazy-load UI (lightweight payload).
      - include_items=True (legacy): returns the full items[] array per bill
        (for Excel export + backward compat with existing callers).

    Most users expect to see all their uploaded bills by default. The previous
    behavior (only 'confirmed') made newly-uploaded bills invisible until the
    user opened each one and clicked Save & Confirm — confusing.
    """
    with conn() as c:
        # v8.5.2: status filter
        if status == "confirmed":
            status_clause = "AND b.status='confirmed'"
        elif status == "review":
            status_clause = "AND b.status='review'"
        else:  # "all" (default)
            status_clause = "AND b.status IN ('confirmed', 'review')"

        bills = c.execute(
            f"SELECT b.id, b.supplier_name, b.phone, b.bill_date, b.bill_no, "
            f"b.written_total, b.computed_total, b.payment_status, b.unit, b.status "
            f"FROM bills b WHERE b.deleted_at IS NULL "
            f"AND b.bill_date IS NOT NULL "
            f"AND b.bill_date >= ? AND b.bill_date <= ? "
            f"{status_clause} "
            f"ORDER BY b.bill_date DESC, b.id DESC",
            (start_date, end_date),
        ).fetchall()

        # Also count bills without a bill_date (they shouldn't disappear from the report
        # just because the AI couldn't extract a date). v8.5.2: include them if their
        # created_at falls in the range.
        bills_no_date = c.execute(
            f"SELECT b.id, b.supplier_name, b.phone, b.bill_date, b.bill_no, "
            f"b.written_total, b.computed_total, b.payment_status, b.unit, b.status "
            f"FROM bills b WHERE b.deleted_at IS NULL "
            f"AND b.bill_date IS NULL "
            f"AND date(b.created_at) >= ? AND date(b.created_at) <= ? "
            f"{status_clause} "
            f"ORDER BY b.id DESC",
            (start_date, end_date),
        ).fetchall()
        all_bills = list(bills) + list(bills_no_date)

        result = []
        for b in all_bills:
            # v8.7: precompute aggregates in SQL (one query per bill) — fast + lightweight
            agg = c.execute(
                "SELECT "
                "COUNT(bi.id) AS item_count, "
                "COUNT(DISTINCT bi.category_id) AS category_count, "
                "COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END "
                "           * bi.price), 0) AS total_cost, "
                "COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END "
                "           * COALESCE(bi.sell_price, pc.sell_price, 0)), 0) AS total_revenue "
                "FROM bill_items bi "
                "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
                "WHERE bi.bill_id=?",
                (b["id"],),
            ).fetchone()
            total_cost = float(agg["total_cost"] or 0)
            total_revenue = float(agg["total_revenue"] or 0)
            total_profit = round(total_revenue - total_cost, 2)

            item_list = []
            if include_items:
                # v8.7: only fetch + compute per-item profit when explicitly requested
                items = c.execute(
                    "SELECT bi.raw, bi.item_code, bi.price, bi.qty, bi.unit, bi.line_total, "
                    "bi.category_id, bi.page_no, bi.confidence, "
                    "pc.name AS cat_name, pc.sell_price, pc.color "
                    "FROM bill_items bi "
                    "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
                    "WHERE bi.bill_id=? ORDER BY bi.id",
                    (b["id"],),
                ).fetchall()
                for idx, it in enumerate(items, 1):
                    p = pieces(it["qty"], it["unit"])
                    sell = it["sell_price"] or 0
                    cost = (it["price"] or 0) * p
                    revenue = sell * p
                    profit = revenue - cost
                    margin = profit / revenue if revenue > 0 else 0
                    item_list.append({
                        "sr_no": idx,
                        "raw": it["raw"],
                        "item_code": it["item_code"],
                        "price": it["price"],
                        "qty": it["qty"],
                        "unit": it["unit"],
                        "pieces": p,
                        "line_total": it["line_total"],
                        "cat_name": it["cat_name"],
                        "cat_code": get_cat_code(it["sell_price"]),
                        "sell_price": it["sell_price"],
                        "page_no": it["page_no"],
                        "cost": round(cost, 2),
                        "revenue": round(revenue, 2),
                        "profit": round(profit, 2),
                        "margin": round(margin, 2),
                        "margin_pct": f"{margin*100:.1f}%",
                        "confidence": it["confidence"],
                    })
            total = b["written_total"] or b["computed_total"] or 0
            bill_dict = {
                "bill_id": b["id"],
                "supplier_name": b["supplier_name"],
                "phone": b["phone"],
                "bill_date": b["bill_date"],
                "bill_no": b["bill_no"],
                "total": total,
                "payment_status": b["payment_status"],
                "status": b["status"],  # v8.5.2: expose status to frontend
                "item_count": agg["item_count"],
                # v8.7: precomputed aggregates for the lazy-load master list
                "category_count": agg["category_count"],
                "total_cost": round(total_cost, 2),
                "total_revenue": round(total_revenue, 2),
                "total_profit": total_profit,
            }
            if include_items:
                bill_dict["items"] = item_list
            result.append(bill_dict)
    return {"bills": result, "total_bills": len(result)}


def category_report(start_date: str = None, end_date: str = None) -> dict:
    """Category-wise breakdown: total products, total cost, avg amount, profit margin per category.
    Categories use codes A (250), B (500), C (750), D (1000).
    """
    where = "WHERE b.status='confirmed' AND b.deleted_at IS NULL"
    args = []
    if start_date and end_date:
        where += " AND b.bill_date >= ? AND b.bill_date <= ? AND b.bill_date IS NOT NULL"
        args = [start_date, end_date]

    with conn() as c:
        rows = c.execute(
            f"SELECT bi.raw, bi.price, bi.qty, bi.unit, bi.line_total, bi.category_id, "
            f"pc.name AS cat_name, pc.sell_price, pc.color, b.bill_date, b.id AS bill_id "
            f"FROM bill_items bi "
            f"JOIN bills b ON bi.bill_id = b.id "
            f"AND b.deleted_at IS NULL "
            f"LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            f"{where} ORDER BY pc.sell_price ASC",
            args,
        ).fetchall()

    by_cat = defaultdict(lambda: {
        "name": "", "code": "—", "sell_price": 0, "color": "",
        "products": 0, "pieces": 0, "cost": 0.0, "revenue": 0.0, "profit": 0.0,
        "bills": set(), "items": [],
    })
    grand = {"products": 0, "pieces": 0, "cost": 0.0, "revenue": 0.0, "profit": 0.0}

    for r in rows:
        sell = r["sell_price"] or 0
        cat_name = r["cat_name"] or "Uncategorized"
        cat_code = get_cat_code(r["sell_price"])
        p = pieces(r["qty"], r["unit"])
        cost = (r["price"] or 0) * p
        revenue = sell * p
        profit = revenue - cost

        entry = by_cat[cat_code]
        entry["name"] = cat_name
        entry["code"] = cat_code
        entry["sell_price"] = sell
        entry["color"] = r["color"] or ""
        entry["products"] += 1
        entry["pieces"] += p
        entry["cost"] += cost
        entry["revenue"] += revenue
        entry["profit"] += profit
        entry["bills"].add(r["bill_id"])

        grand["products"] += 1
        grand["pieces"] += p
        grand["cost"] += cost
        grand["revenue"] += revenue
        grand["profit"] += profit

    cats = []
    for code, e in sorted(by_cat.items()):
        bill_count = len(e["bills"])
        margin = e["profit"] / e["revenue"] if e["revenue"] > 0 else 0
        avg_cost_per_piece = e["cost"] / e["pieces"] if e["pieces"] else 0
        avg_amount = e["cost"] / e["products"] if e["products"] else 0
        cats.append({
            "code": e["code"],
            "name": e["name"],
            "sell_price": e["sell_price"],
            "color": e["color"],
            "total_products": e["products"],
            "total_pieces": e["pieces"],
            "total_cost": round(e["cost"], 2),
            "total_revenue": round(e["revenue"], 2),
            "total_profit": round(e["profit"], 2),
            "avg_amount": round(avg_amount, 2),
            "avg_cost_per_piece": round(avg_cost_per_piece, 2),
            "profit_margin_pct": f"{margin*100:.1f}%",
            "margin": round(margin, 2),
            "bill_count": bill_count,
        })

    grand_margin = grand["profit"] / grand["revenue"] if grand["revenue"] > 0 else 0
    grand_avg = grand["cost"] / grand["products"] if grand["products"] else 0
    grand_out = {
        "total_products": grand["products"],
        "total_pieces": grand["pieces"],
        "total_cost": round(grand["cost"], 2),
        "total_revenue": round(grand["revenue"], 2),
        "total_profit": round(grand["profit"], 2),
        "avg_amount": round(grand_avg, 2),
        "profit_margin_pct": f"{grand_margin*100:.1f}%",
        "margin": round(grand_margin, 2),
    }
    return {"categories": cats, "grand": grand_out}


def profit_estimate(start_date: str, end_date: str) -> dict:
    """Per-category profit breakdown over date range (kept for backward compat)."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.raw, bi.price, bi.qty, bi.unit, bi.category_id, "
            "pc.name AS cat_name, pc.sell_price, b.bill_date, b.supplier_name, b.id AS bill_id "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND b.bill_date IS NOT NULL "
            "AND b.bill_date >= ? AND b.bill_date <= ?",
            (start_date, end_date),
        ).fetchall()

    by_cat = defaultdict(lambda: {
        "name": "", "code": "—", "pieces": 0, "cost": 0.0, "revenue": 0.0, "profit": 0.0, "bills": set()
    })
    grand = {"pieces": 0, "cost": 0.0, "revenue": 0.0, "profit": 0.0}

    for r in rows:
        cat_name = r["cat_name"] or "Uncategorized"
        cat_code = get_cat_code(r["sell_price"])
        sell = r["sell_price"] or 0
        p = pieces(r["qty"], r["unit"])
        cost = (r["price"] or 0) * p
        revenue = sell * p
        profit = revenue - cost

        entry = by_cat[cat_code]
        entry["name"] = cat_name
        entry["code"] = cat_code
        entry["pieces"] += p
        entry["cost"] += cost
        entry["revenue"] += revenue
        entry["profit"] += profit
        entry["bills"].add(r["bill_id"])

        grand["pieces"] += p
        grand["cost"] += cost
        grand["revenue"] += revenue
        grand["profit"] += profit

    cats = []
    for code, e in sorted(by_cat.items()):
        bills = len(e["bills"])
        margin = e["profit"] / e["revenue"] if e["revenue"] > 0 else 0
        cats.append({
            "code": e["code"],
            "name": e["name"],
            "pieces": e["pieces"],
            "cost": round(e["cost"], 2),
            "revenue": round(e["revenue"], 2),
            "profit": round(e["profit"], 2),
            "margin": round(margin, 2),
            "margin_pct": f"{margin*100:.1f}%",
            "bills": bills,
            "avg_pieces_per_bill": round(e["pieces"] / bills, 1) if bills else 0,
            "avg_cost_per_piece": round(e["cost"] / e["pieces"], 2) if e["pieces"] else 0,
            "avg_sell_per_piece": round(e["revenue"] / e["pieces"], 2) if e["pieces"] else 0,
            "avg_cost_per_bill": round(e["cost"] / bills, 2) if bills else 0,
        })

    grand_margin = grand["profit"] / grand["revenue"] if grand["revenue"] > 0 else 0
    grand_out = {
        "pieces": grand["pieces"],
        "cost": round(grand["cost"], 2),
        "revenue": round(grand["revenue"], 2),
        "profit": round(grand["profit"], 2),
        "margin": round(grand_margin, 2),
    }

    return {"categories": cats, "grand": grand_out}


def supplier_ranking() -> dict:
    """Rank suppliers by total spend. Only confirmed, non-deleted bills."""
    with conn() as c:
        rows = c.execute(
            "SELECT s.id, s.name, s.phone, "
            "COUNT(b.id) AS bill_count, "
            "COALESCE(SUM(CASE WHEN b.written_total IS NOT NULL THEN b.written_total "
            "ELSE b.computed_total END), 0) AS total_spent, "
            "SUM(CASE WHEN b.payment_status='credit' THEN COALESCE(b.written_total, b.computed_total, 0) "
            "ELSE 0 END) AS outstanding, "
            "MAX(b.bill_date) AS last_purchase "
            "FROM suppliers s LEFT JOIN bills b ON s.id = b.supplier_id "
            "AND b.status='confirmed' AND b.deleted_at IS NULL "
            "WHERE s.deleted_at IS NULL "
            "GROUP BY s.id ORDER BY total_spent DESC"
        ).fetchall()

    suppliers = []
    for r in rows:
        suppliers.append({
            "id": r["id"],
            "name": r["name"],
            "phone": r["phone"],
            "bill_count": r["bill_count"],
            "total_spent": round(r["total_spent"], 2),
            "outstanding": round(r["outstanding"], 2),
            "last_purchase": r["last_purchase"],
        })
    return {"suppliers": suppliers}


# ════════════════════════════════════════════════════════════════════════════════
# v8.7 — NEW REPORTS: Profit Analysis + Sold Stock
# ════════════════════════════════════════════════════════════════════════════════

def profit_analysis_report(start: str, end: str, group_by: str = "category") -> dict:
    """v8.7: Date-range profit analysis — by category (default) or by month.

    For group_by='category':
        Returns per-category: revenue, cogs, gross_profit, margin_pct,
        qty_sold, avg_selling_price.
        Computed from sale_items JOIN sales JOIN price_categories.

    For group_by='month':
        Returns per-month (YYYY-MM): revenue, cogs, gross_profit, margin_pct,
        operating_expenses, operating_profit.
        Operating expenses are read from the `expenses` table
        (expense_type='operating').

    Excludes refunded sales (payment_status='refunded').

    Uses COALESCE(s.bill_date, date(s.created_at)) for date filtering
    (consistent with v8.5.5 fix).

    Reviewer 1 note: uses sale_items.cost_price (captured at sale time by
    PR 3) — NOT category_stock_state.current_avg_cost. This is correct for
    historical profit reporting.
    """
    if not start or not end:
        return {"error": "start and end dates are required (YYYY-MM-DD)"}
    result = {"start": start, "end": end, "group_by": group_by}

    with conn() as c:
        if group_by == "month":
            # Monthly breakdown
            # v8.18.16: revenue now comes from sales.total (what the customer
            # actually paid — post line-level AND sale-level discount), NOT
            # SUM(si.sell_price * qty). The old raw-item-sum basis made the
            # month view of THIS report disagree with its own category view
            # (which allocates sales.total, v8.16.13) and with P&L / Store
            # Profit whenever any discount was applied — the exact
            # "report shows extra sales" complaint.
            rows = c.execute(
                "SELECT strftime('%Y-%m', COALESCE(s.created_at, datetime('now'))) AS month, "
                "COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs, "
                "COALESCE(SUM(si.qty), 0) AS qty_sold "
                "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
                f"WHERE {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= ? AND date(s.created_at) <= ? "
                "GROUP BY month ORDER BY month",
                (start, end),
            ).fetchall()
            rev_map = {r["month"]: float(r["v"] or 0) for r in c.execute(
                "SELECT strftime('%Y-%m', COALESCE(created_at, datetime('now'))) AS month, "
                "COALESCE(SUM(total), 0) AS v FROM sales "
                f"WHERE {db.VALID_SALE_FILTER_NO_ALIAS} "
                "AND date(created_at) >= ? AND date(created_at) <= ? GROUP BY month",
                (start, end)).fetchall()}
            # Get operating expenses per month
            exp_rows = c.execute(
                "SELECT strftime('%Y-%m', e.date) AS month, "
                "COALESCE(SUM(e.amount), 0) AS operating_expenses "
                "FROM expenses e WHERE e.expense_type='operating' "
                "AND e.date >= ? AND e.date <= ? "
                "GROUP BY month",
                (start, end),
            ).fetchall()
            exp_map = {r["month"]: float(r["operating_expenses"] or 0) for r in exp_rows}
            # v8.18.14: extra (non-POS) sales income per month — other
            # income with no COGS. Own column, never merged into POS
            # revenue, so the two income streams stay differentiable.
            try:
                extra_rows = {r["month"]: float(r["v"] or 0) for r in c.execute(
                    "SELECT strftime('%Y-%m', sale_date) AS month, "
                    "COALESCE(SUM(total), 0) AS v FROM extra_sales "
                    "WHERE sale_date >= ? AND sale_date <= ? GROUP BY 1",
                    (start, end)).fetchall()}
            except Exception:
                extra_rows = {}  # table not migrated yet
            months = []
            seen_months = set()
            for r in rows:
                # v8.18.16: revenue from sales.total (see note above);
                # gross profit derives from it so the P&L identity
                # gross_profit = revenue - cogs holds month by month.
                rev = rev_map.get(r["month"], 0.0)
                cogs = float(r["cogs"] or 0)
                gp = rev - cogs
                op_exp = exp_map.get(r["month"], 0)
                m_extra = extra_rows.get(r["month"], 0.0)
                # v8.18.14: operating profit includes extra-sales income
                op_profit = gp + m_extra - op_exp
                margin = round((gp / rev) * 100, 2) if rev > 0 else 0
                months.append({
                    "month": r["month"],
                    "revenue": round(rev, 2),
                    "cogs": round(cogs, 2),
                    "gross_profit": round(gp, 2),
                    "margin_pct": margin,
                    "qty_sold": int(r["qty_sold"] or 0),
                    "operating_expenses": round(op_exp, 2),
                    # v8.18.14: non-POS income — own column
                    "extra_sales_income": round(m_extra, 2),
                    "operating_profit": round(op_profit, 2),
                })
                seen_months.add(r["month"])
            # v8.18.14: months with ONLY extra sales (no POS sales) still get
            # a row — otherwise income would silently vanish from the table
            for m_key in sorted(set(extra_rows) - seen_months):
                op_exp = exp_map.get(m_key, 0)
                m_extra = extra_rows.get(m_key, 0.0)
                months.append({
                    "month": m_key, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0,
                    "margin_pct": 0, "qty_sold": 0, "operating_expenses": round(op_exp, 2),
                    "extra_sales_income": round(m_extra, 2),
                    "operating_profit": round(m_extra - op_exp, 2),
                })
            months.sort(key=lambda m: m["month"])
            total_rev = sum(m["revenue"] for m in months)
            total_cogs = sum(m["cogs"] for m in months)
            total_gp = sum(m["gross_profit"] for m in months)
            total_op_exp = sum(m["operating_expenses"] for m in months)
            total_extra = sum(m["extra_sales_income"] for m in months)
            total_op_profit = sum(m["operating_profit"] for m in months)
            result["months"] = months
            result["totals"] = {
                "revenue": round(total_rev, 2),
                "cogs": round(total_cogs, 2),
                "gross_profit": round(total_gp, 2),
                "margin_pct": round((total_gp / total_rev) * 100, 2) if total_rev > 0 else 0,
                "qty_sold": sum(m["qty_sold"] for m in months),
                "operating_expenses": round(total_op_exp, 2),
                # v8.18.14: non-POS income total (own line)
                "extra_sales_income": round(total_extra, 2),
                "operating_profit": round(total_op_profit, 2),
            }
        else:
            # Default: by category
            # v8.16.9: also fetch current_avg_cost so we can show the "current margin"
            # (forward-looking) alongside the historical margin (backward-looking).
            # This lets users reconcile the Store Profit dashboard vs this report.
            #
            # v8.16.13: Use sales.total (what customer actually paid) for revenue,
            # NOT SUM(si.sell_price * qty). The two can differ when there are
            # per-sale discounts, price overrides, or returns mixed with sales.
            # This fixes the Rs 540 difference between Store Profit and Profit Analysis.
            #
            # Strategy: We need to attribute the sale's total to the categories of its line items.
            # We allocate sales.total proportionally based on each line item's sell_price * qty.
            rows = c.execute(
                "SELECT pc.id AS category_id, pc.code, pc.name, pc.sell_price, "
                # COGS is unambiguous — uses cost_price captured at sale time
                "COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs, "
                # Qty + line items sum (for proportional revenue allocation)
                # v8.18.16: weight by line_total (actual charged) instead of
                # sell_price*qty — line discounts now attribute correctly.
                "COALESCE(SUM(si.qty), 0) AS qty_sold, "
                "COALESCE(SUM(COALESCE(si.line_total, si.sell_price * si.qty)), 0) AS items_revenue, "
                "COUNT(DISTINCT si.sale_id) AS sale_count, "
                "COALESCE(css.current_avg_cost, 0) AS current_avg_cost "
                "FROM sale_items si "
                "JOIN sales s ON si.sale_id = s.id "
                "LEFT JOIN price_categories pc ON si.category_id = pc.id "
                "LEFT JOIN category_stock_state css ON css.category_id = pc.id "
                f"WHERE {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= ? AND date(s.created_at) <= ? "
                "AND si.category_id IS NOT NULL "
                "GROUP BY pc.id "
                "ORDER BY pc.code",
                (start, end),
            ).fetchall()
            # v8.16.13: Compute total sales.total across all sales in the date range
            # (so we can proportionally allocate the sale-level discount to each category)
            total_sales_total_row = c.execute(
                f"SELECT COALESCE(SUM(s.total), 0) AS v FROM sales s "
                f"WHERE {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= ? AND date(s.created_at) <= ?",
                (start, end),
            ).fetchone()
            total_sales_total = float(total_sales_total_row["v"] or 0)
            # Compute total items_revenue (to compute the proportional ratio)
            total_items_revenue = sum(float(r["items_revenue"] or 0) for r in rows)
            # Proportional ratio: total_sales_total / total_items_revenue
            # If items_revenue > sales.total (some sales had discounts), this ratio < 1
            # If items_revenue < sales.total (returns mixed in as negative totals), this ratio > 1
            revenue_ratio = (total_sales_total / total_items_revenue) \
                if total_items_revenue > 0 else 1.0
            categories = []
            for r in rows:
                items_rev = float(r["items_revenue"] or 0)
                # v8.16.13: Allocate sales.total proportionally based on items_revenue
                # This ensures Profit Analysis revenue = Store Profit revenue (same source)
                rev = round(items_rev * revenue_ratio, 2)
                cogs = float(r["cogs"] or 0)
                gp = round(rev - cogs, 2)
                qty = int(r["qty_sold"] or 0)
                sell_price = float(r["sell_price"] or 0)
                current_avg_cost = float(r["current_avg_cost"] or 0)
                # Historical margin: actual margin realized on past sales
                margin = round((gp / rev) * 100, 2) if rev > 0 else 0
                # v8.16.9: Current margin (forward-looking) — what margin would I make
                # if I sold a unit at the current sell_price vs current avg cost?
                current_margin = round(((sell_price - current_avg_cost) / sell_price) * 100, 2) \
                    if sell_price > 0 else 0
                # v8.16.9: Avg historical cost per unit (for reconciliation)
                avg_historical_cost = round(cogs / qty, 2) if qty > 0 else 0
                # Cost change since the period (positive = cost went up)
                cost_change = round(current_avg_cost - avg_historical_cost, 2) if qty > 0 else 0
                avg_price = round(rev / qty, 2) if qty > 0 else 0
                # v8.16.12: NEW COLUMNS for richer analysis
                # Profit per unit (historical)
                profit_per_unit = round(gp / qty, 2) if qty > 0 else 0
                # Current profit per unit (forward-looking)
                current_profit_per_unit = round(sell_price - current_avg_cost, 2)
                # Markup % = (Sell - Cost) / Cost * 100  (different from margin % which is /Sell)
                markup_pct = round((gp / cogs) * 100, 2) if cogs > 0 else 0
                current_markup_pct = round(((sell_price - current_avg_cost) / current_avg_cost) * 100, 2) \
                    if current_avg_cost > 0 else 0
                # Margin per unit (Rs) — historical
                margin_per_unit = round((rev - cogs) / qty, 2) if qty > 0 else 0
                categories.append({
                    "category_id": r["category_id"],
                    "code": r["code"] or "—",
                    "name": r["name"] or "Unknown",
                    "sell_price": sell_price,
                    "qty_sold": qty,
                    "revenue": round(rev, 2),
                    "cogs": round(cogs, 2),
                    "gross_profit": round(gp, 2),
                    "margin_pct": margin,
                    "current_avg_cost": current_avg_cost,
                    "current_margin_pct": current_margin,
                    "avg_historical_cost": avg_historical_cost,
                    "cost_change": cost_change,
                    "avg_selling_price": avg_price,
                    # v8.16.12 NEW columns:
                    "profit_per_unit": profit_per_unit,
                    "current_profit_per_unit": current_profit_per_unit,
                    "markup_pct": markup_pct,
                    "current_markup_pct": current_markup_pct,
                    "margin_per_unit": margin_per_unit,
                    "sale_count": int(r["sale_count"] or 0),
                })
            total_rev = sum(cat["revenue"] for cat in categories)
            total_cogs = sum(cat["cogs"] for cat in categories)
            total_gp = sum(cat["gross_profit"] for cat in categories)
            total_qty = sum(cat["qty_sold"] for cat in categories)
            result["categories"] = categories
            # v8.18.14: extra (non-POS) sales income for the range — cartons,
            # raddi/scrap etc. Reported as its OWN top-level total, NOT mixed
            # into any category row: extra sales have no category and no
            # COGS, so folding them into the category table would silently
            # distort category margins. The UI + exports show it as a
            # separate, clearly-labeled line.
            try:
                extra_income = float(c.execute(
                    "SELECT COALESCE(SUM(total), 0) AS v FROM extra_sales "
                    "WHERE sale_date >= ? AND sale_date <= ?",
                    (start, end)).fetchone()["v"] or 0)
            except Exception:
                extra_income = 0.0  # table not migrated yet
            result["extra_sales_income"] = round(extra_income, 2)
            result["totals"] = {
                "revenue": round(total_rev, 2),
                "cogs": round(total_cogs, 2),
                "gross_profit": round(total_gp, 2),
                "margin_pct": round((total_gp / total_rev) * 100, 2) if total_rev > 0 else 0,
                "qty_sold": total_qty,
                # v8.18.14: non-POS income total (own line, excluded from margin)
                "extra_sales_income": round(extra_income, 2),
            }
    return result


def sold_stock_report(start: str, end: str, group_by: str = "category") -> dict:
    """v8.7: Date-range sold stock report — by category (default) or by item.

    Reviewer 3 note: 'By Category' is the default + primary view because
    AI-extracted item_names are too noisy (e.g. "Toy Car Red" vs "Red Toy Car"
    vs "toy car" would group as 3 separate items). Grouping by category_id
    is mathematically sound + matches the weighted-average cost engine.
    'By Item' is a secondary, drill-down view.

    For group_by='category' (DEFAULT):
        Per category: qty_sold, revenue, cogs, gross_profit, margin_pct,
        distinct_items_sold, sale_count.

    For group_by='item':
        Per (item_name, category): qty_sold, revenue, cogs, gross_profit,
        margin_pct, avg_selling_price, avg_cost_price, sale_count,
        first_sold_date, last_sold_date.
        NOTE: item_name is AI-extracted free text — expect fragmentation.

    Excludes refunded sales (payment_status='refunded').

    v8.18.16: revenue/gross_profit are line_total-based (what was actually
    charged per line, incl. line discounts) — consistent with Profit
    Analysis / Store Profit instead of overstating them. Top-level
    sale_discounts/loyalty_discounts expose the sale-level discounts that
    cannot be attributed to a category, so the report page can reconcile
    against the Profit Analysis total.
    """
    if not start or not end:
        return {"error": "start and end dates are required (YYYY-MM-DD)"}
    result = {"start": start, "end": end, "group_by": group_by}

    with conn() as c:
        # v8.18.16: sale-level discounts given in the period. Sold Stock
        # revenue is per-line (line_total), so a sale-level discount is not
        # attributable to any category/item — exposing it here lets the
        # report page reconcile: line revenue + sale discounts (+ loyalty
        # discounts) = what the customer paid (Profit Analysis revenue,
        # before any tax). Previously users just saw two reports disagree.
        try:
            disc_row = c.execute(
                f"SELECT COALESCE(SUM(discount), 0) AS d, "
                f"COALESCE(SUM(loyalty_discount), 0) AS l "
                f"FROM sales WHERE date(created_at) >= ? AND date(created_at) <= ? "
                f"AND {db.VALID_SALE_FILTER_NO_ALIAS}",
                (start, end),
            ).fetchone()
            result["sale_discounts"] = round(float(disc_row["d"] or 0), 2)
            result["loyalty_discounts"] = round(float(disc_row["l"] or 0), 2)
        except Exception:
            result["sale_discounts"] = 0.0
            result["loyalty_discounts"] = 0.0
        if group_by == "item":
            # Per-item breakdown (Reviewer 3: use LOWER(item_name) to merge
            # case variants like "Toy Car" and "toy car")
            rows = c.execute(
                "SELECT LOWER(si.item_name) AS item_key, "
                "MAX(si.item_name) AS item_name, "
                "si.category_id, pc.code AS cat_code, pc.name AS cat_name, "
                "COALESCE(SUM(COALESCE(si.line_total, si.sell_price * si.qty)), 0) AS revenue, "
                "COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs, "
                "COALESCE(SUM(COALESCE(si.line_total, si.sell_price * si.qty) - si.cost_price * si.qty), 0) AS gross_profit, "
                "COALESCE(SUM(si.qty), 0) AS qty_sold, "
                "COUNT(DISTINCT si.sale_id) AS sale_count, "
                "MIN(date(s.created_at)) AS first_sold, "
                "MAX(date(s.created_at)) AS last_sold "
                "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
                "LEFT JOIN price_categories pc ON si.category_id = pc.id "
                f"WHERE {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= ? AND date(s.created_at) <= ? "
                "GROUP BY item_key, si.category_id "
                "ORDER BY qty_sold DESC",
                (start, end),
            ).fetchall()
            items = []
            for r in rows:
                rev = float(r["revenue"] or 0)
                cogs_v = float(r["cogs"] or 0)
                gp = float(r["gross_profit"] or 0)
                qty = int(r["qty_sold"] or 0)
                margin = round((gp / rev) * 100, 2) if rev > 0 else 0
                items.append({
                    "item_name": r["item_name"] or "Unknown",
                    "category_id": r["category_id"],
                    "cat_code": r["cat_code"] or "—",
                    "cat_name": r["cat_name"] or "Uncategorized",
                    "qty_sold": qty,
                    "revenue": round(rev, 2),
                    "cogs": round(cogs_v, 2),
                    "gross_profit": round(gp, 2),
                    "margin_pct": margin,
                    "avg_selling_price": round(rev / qty, 2) if qty > 0 else 0,
                    "avg_cost_price": round(cogs_v / qty, 2) if qty > 0 else 0,
                    "sale_count": int(r["sale_count"] or 0),
                    "first_sold": r["first_sold"],
                    "last_sold": r["last_sold"],
                })
            total_rev = sum(it["revenue"] for it in items)
            total_cogs = sum(it["cogs"] for it in items)
            total_gp = sum(it["gross_profit"] for it in items)
            total_qty = sum(it["qty_sold"] for it in items)
            result["items"] = items
            result["totals"] = {
                "revenue": round(total_rev, 2),
                "cogs": round(total_cogs, 2),
                "gross_profit": round(total_gp, 2),
                "margin_pct": round((total_gp / total_rev) * 100, 2) if total_rev > 0 else 0,
                "qty_sold": total_qty,
                "distinct_items": len(items),
            }
        else:
            # v8.18.16: revenue = what was actually charged per line
            # (COALESCE(line_total, sell_price*qty) for legacy rows) so this
            # report no longer inflates revenue above Profit Analysis /
            # Store Profit when line-level discounts are used. COGS stays
            # cost-captured-at-sale-time, which is the historical basis.
            rows = c.execute(
                "SELECT pc.id AS category_id, pc.code, pc.name, pc.sell_price, "
                "COALESCE(SUM(COALESCE(si.line_total, si.sell_price * si.qty)), 0) AS revenue, "
                "COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs, "
                "COALESCE(SUM(COALESCE(si.line_total, si.sell_price * si.qty) - si.cost_price * si.qty), 0) AS gross_profit, "
                "COALESCE(SUM(si.qty), 0) AS qty_sold, "
                "COUNT(DISTINCT si.sale_id) AS sale_count, "
                "COUNT(DISTINCT si.item_name) AS distinct_items "
                "FROM sale_items si "
                "JOIN sales s ON si.sale_id = s.id "
                "LEFT JOIN price_categories pc ON si.category_id = pc.id "
                f"WHERE {db.VALID_SALE_FILTER} "
                "AND date(s.created_at) >= ? AND date(s.created_at) <= ? "
                "AND si.category_id IS NOT NULL "
                "GROUP BY pc.id "
                "ORDER BY pc.code",
                (start, end),
            ).fetchall()
            categories = []
            for r in rows:
                rev = float(r["revenue"] or 0)
                cogs_v = float(r["cogs"] or 0)
                gp = float(r["gross_profit"] or 0)
                qty = int(r["qty_sold"] or 0)
                margin = round((gp / rev) * 100, 2) if rev > 0 else 0
                categories.append({
                    "category_id": r["category_id"],
                    "code": r["code"] or "—",
                    "name": r["name"] or "Unknown",
                    "sell_price": float(r["sell_price"] or 0),
                    "qty_sold": qty,
                    "revenue": round(rev, 2),
                    "cogs": round(cogs_v, 2),
                    "gross_profit": round(gp, 2),
                    "margin_pct": margin,
                    "avg_selling_price": round(rev / qty, 2) if qty > 0 else 0,
                    "sale_count": int(r["sale_count"] or 0),
                    "distinct_items": int(r["distinct_items"] or 0),
                })
            total_rev = sum(cat["revenue"] for cat in categories)
            total_cogs = sum(cat["cogs"] for cat in categories)
            total_gp = sum(cat["gross_profit"] for cat in categories)
            total_qty = sum(cat["qty_sold"] for cat in categories)
            result["categories"] = categories
            result["totals"] = {
                "revenue": round(total_rev, 2),
                "cogs": round(total_cogs, 2),
                "gross_profit": round(total_gp, 2),
                "margin_pct": round((total_gp / total_rev) * 100, 2) if total_rev > 0 else 0,
                "qty_sold": total_qty,
                "distinct_categories": len(categories),
            }
    return result
