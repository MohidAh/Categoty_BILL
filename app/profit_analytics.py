"""v7.0 Phase 1 — Profit ANALYTICS (margins, monthly, YTD, dashboard, daily stock).

Extracted from profit.py. Imports state reads from profit_engine.
"""
from datetime import datetime
from .db import conn
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant
from .profit_engine import get_category_stock_state, _get_setting


def get_margins() -> dict:
    """Per-category margins + Category Average (informational) + Actual Overall (primary KPI)."""
    with conn() as c:
        cats = c.execute(
            "SELECT pc.id, pc.code, pc.name, pc.sell_price, "
            "COALESCE(css.current_avg_cost, 0) AS avg_cost "
            "FROM price_categories pc "
            "LEFT JOIN category_stock_state css ON css.category_id = pc.id "
            "WHERE pc.active = 1 ORDER BY pc.sort_order, pc.code"
        ).fetchall()
        totals = c.execute(
            "SELECT COALESCE(SUM(si.sell_price * si.qty), 0) AS total_sales, "
            "COALESCE(SUM(si.cost_price * si.qty), 0) AS total_cogs "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            f"WHERE {db.VALID_SALE_FILTER}"
        ).fetchone()
    categories = []
    margins_list = []
    for cat in cats:
        sell = float(cat["sell_price"] or 0)
        cost = float(cat["avg_cost"] or 0)
        margin_pct = round(((sell - cost) / sell) * 100, 2) if sell > 0 else 0.0
        categories.append({"code": cat["code"] or "—", "name": cat["name"],
                           "sell_price": round(sell, 2), "avg_cost": round(cost, 2),
                           "margin_pct": margin_pct})
        # v8.5.5: only include categories with cost > 0 in the average.
        # Categories with cost=0 (bags with no purchase bill yet) show 100%
        # margin which skews the category average upward unrealistically.
        if sell > 0 and cost > 0:
            margins_list.append(margin_pct)
    category_average_margin = round(sum(margins_list) / len(margins_list), 2) if margins_list else 0.0
    total_sales = float(totals["total_sales"] or 0)
    total_cogs = float(totals["total_cogs"] or 0)
    total_gp = total_sales - total_cogs
    actual_overall_margin = round((total_gp / total_sales) * 100, 2) if total_sales > 0 else 0.0
    difference_pct = round(category_average_margin - actual_overall_margin, 2)
    return {"categories": categories, "category_average_margin": category_average_margin,
            "actual_overall_margin": actual_overall_margin, "difference_pct": difference_pct,
            "total_sales": round(total_sales, 2), "total_cogs": round(total_cogs, 2),
            "total_gross_profit": round(total_gp, 2),
            "note": "Category Average is informational. Actual Overall is the primary KPI."}


def get_monthly_profit(month: str = "") -> dict:
    """Monthly actual profit using COGS = Opening + Purchases - Closing."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    try:
        y, m = month.split("-"); y, m = int(y), int(m)
    except Exception:
        return {"error": f"Invalid month format: {month}"}
    month_start = f"{y:04d}-{m:02d}-01"
    next_month_start = f"{y+1:04d}-01-01" if m == 12 else f"{y:04d}-{m+1:02d}-01"

    with conn() as c:
        purchases_before = c.execute(
            "SELECT COALESCE(SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS v "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND COALESCE(b.bill_date, date(b.created_at)) < ?", (month_start,)).fetchone()["v"]
        cogs_before = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND s.created_at < ?", (month_start,)).fetchone()["v"]
        adj_value_before = c.execute(
            "SELECT COALESCE(SUM(sa.delta * COALESCE(css.current_avg_cost, "
            "(SELECT AVG(bi.price) FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id=sa.category_id), 0)), 0) AS v "
            "FROM stock_adjustments sa LEFT JOIN category_stock_state css ON css.category_id=sa.category_id "
            "WHERE sa.created_at < ?", (month_start,)).fetchone()["v"]
        opening_inventory = float(purchases_before or 0) - float(cogs_before or 0) + float(adj_value_before or 0)
        purchases = c.execute(
            "SELECT COALESCE(SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS v "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND strftime('%Y-%m', COALESCE(b.bill_date, date(b.created_at)))=?", (month,)).fetchone()["v"]
        purchases_thru_month = c.execute(
            "SELECT COALESCE(SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END), 0) AS v "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND COALESCE(b.bill_date, date(b.created_at)) < ?", (next_month_start,)).fetchone()["v"]
        cogs_thru_month = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND s.created_at < ?", (next_month_start,)).fetchone()["v"]
        adj_value_thru_month = c.execute(
            "SELECT COALESCE(SUM(sa.delta * COALESCE(css.current_avg_cost, "
            "(SELECT AVG(bi.price) FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id=sa.category_id), 0)), 0) AS v "
            "FROM stock_adjustments sa LEFT JOIN category_stock_state css ON css.category_id=sa.category_id "
            "WHERE sa.created_at < ?", (next_month_start,)).fetchone()["v"]
        closing_inventory = float(purchases_thru_month or 0) - float(cogs_thru_month or 0) + float(adj_value_thru_month or 0)
        cogs = opening_inventory + float(purchases or 0) - closing_inventory
        cogs_from_sales = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND strftime('%Y-%m', s.created_at)=?", (month,)).fetchone()["v"]
        sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS v FROM sales "
            f"WHERE strftime('%Y-%m', created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}", (month,)).fetchone()["v"]
        op_exp = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'", (month,)).fetchone()["v"]
        owner_draws = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='owner_draw'", (month,)).fetchone()["v"]
        # v8.18.14: extra (non-POS) sales income for the month — cartons,
        # raddi/scrap etc. Pure income, no COGS, NOT part of `sales`.
        extra_income = _extra_sales_total(c, month, "", "")
    sales = float(sales or 0); cogs = float(cogs); cogs_from_sales = float(cogs_from_sales or 0)
    gross_profit = sales - cogs
    monthly_margin = (gross_profit / sales * 100) if sales > 0 else 0.0
    # v8.18.14: operating profit now includes extra-sales income
    # (gross profit + other income - operating expenses), consistent with
    # get_pnl()/get_actual_earnings(). Extra sales stay a SEPARATE line so
    # they are differentiable from POS sales in every report.
    operating_profit = gross_profit + extra_income - float(op_exp or 0)
    return {"month": month, "opening_inventory": round(opening_inventory, 2),
            "purchases": round(float(purchases or 0), 2), "closing_inventory": round(closing_inventory, 2),
            "cogs": round(cogs, 2), "cogs_from_sales": round(cogs_from_sales, 2),
            "cogs_difference": round(cogs - cogs_from_sales, 2), "sales": round(sales, 2),
            "gross_profit": round(gross_profit, 2), "monthly_margin": round(monthly_margin, 2),
            # v8.18.14: extra (non-POS) sales income — own line, added to operating profit
            "extra_sales_income": round(extra_income, 2),
            "operating_expenses": round(float(op_exp or 0), 2), "owner_draws": round(float(owner_draws or 0), 2),
            "operating_profit": round(operating_profit, 2),
            "note": "COGS = Opening + Purchases - Closing (includes stock adjustments). GP and Operating Profit shown separately. Extra Sales (non-POS: cartons, raddi) are other income with no COGS and are added to Operating Profit. Bridge COGS and cogs_from_sales should match within rounding in the absence of stock adjustments; with adjustments they diverge by the adjustment cost impact."}


def _extra_sales_total(c, month: str = "", start: str = "", end: str = "") -> float:
    """Extra (non-POS) sales income. Takes an open cursor.

    v8.18.14: shared by get_monthly_profit/get_ytd_profit/
    get_store_profit_dashboard. Falls back to 0 when the extra_sales
    table hasn't been migrated yet (first boot on a legacy DB).
    """
    try:
        if month:
            v = c.execute(
                "SELECT COALESCE(SUM(total), 0) AS v FROM extra_sales "
                "WHERE strftime('%Y-%m', sale_date)=?", (month,)).fetchone()["v"]
        elif start or end:
            v = c.execute(
                "SELECT COALESCE(SUM(total), 0) AS v FROM extra_sales "
                "WHERE sale_date >= ? AND sale_date <= ?",
                (start or "0000-01-01", end or "9999-12-31")).fetchone()["v"]
        else:
            v = 0
    except Exception:
        v = 0
    return float(v or 0)


def get_ytd_profit() -> dict:
    """YTD cumulative profit: margin = Cumulative GP / Cumulative Sales (NOT avg of months)."""
    from .profit_cash import get_cash_buckets, get_stock_reserve
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        opening_date_row = c.execute("SELECT value FROM settings WHERE key='store_opening_date'").fetchone()
        if opening_date_row and opening_date_row["value"]:
            opening_date = opening_date_row["value"]
        else:
            earliest = c.execute("SELECT MIN(date(created_at)) AS d FROM sales").fetchone()
            opening_date = earliest["d"] or today
        totals = c.execute(
            "SELECT COALESCE(SUM(s.total), 0) AS ytd_sales, "
            "COALESCE(SUM(si.cost_price * si.qty), 0) AS ytd_cogs "
            "FROM sales s LEFT JOIN sale_items si ON si.sale_id = s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND date(s.created_at) >= ? AND date(s.created_at) <= ?",
            (opening_date, today)).fetchone()
        monthly_rows = c.execute(
            "SELECT strftime('%Y-%m', s.created_at) AS month, "
            "COALESCE(SUM(s.total), 0) AS sales, COALESCE(SUM(si.cost_price * si.qty), 0) AS cogs "
            "FROM sales s LEFT JOIN sale_items si ON si.sale_id = s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND date(s.created_at) >= ? AND date(s.created_at) <= ? "
            "GROUP BY strftime('%Y-%m', s.created_at) ORDER BY month",
            (opening_date, today)).fetchall()
        op_exp_ytd = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE expense_type='operating' AND date(date) >= ? AND date(date) <= ?",
            (opening_date, today)).fetchone()["v"]
        # v8.18.14: extra (non-POS) sales income — YTD total + per-month rows.
        # Keep separate from `sales` so it stays differentiable.
        ytd_extra = _extra_sales_total(c, "", opening_date, today)
        extra_rows = {}
        try:
            for r in c.execute(
                "SELECT strftime('%Y-%m', sale_date) AS month, "
                "COALESCE(SUM(total), 0) AS v FROM extra_sales "
                "WHERE sale_date >= ? AND sale_date <= ? GROUP BY 1",
                (opening_date, today)).fetchall():
                extra_rows[r["month"]] = float(r["v"] or 0)
        except Exception:
            pass  # table not migrated yet
    ytd_sales = float(totals["ytd_sales"] or 0); ytd_cogs = float(totals["ytd_cogs"] or 0)
    ytd_gp = ytd_sales - ytd_cogs
    ytd_margin = (ytd_gp / ytd_sales * 100) if ytd_sales > 0 else 0.0
    # v8.18.14: YTD operating profit includes extra-sales (other) income
    ytd_op_profit = ytd_gp + ytd_extra - float(op_exp_ytd or 0)
    monthly = []
    for r in monthly_rows:
        m_sales = float(r["sales"] or 0); m_cogs = float(r["cogs"] or 0); m_gp = m_sales - m_cogs
        m_margin = (m_gp / m_sales * 100) if m_sales > 0 else 0.0
        m_extra = extra_rows.get(r["month"], 0.0)
        monthly.append({"month": r["month"], "sales": round(m_sales, 2), "cogs": round(m_cogs, 2),
                        "gross_profit": round(m_gp, 2), "margin_pct": round(m_margin, 2),
                        # v8.18.14: non-POS income for that month (own column)
                        "extra_sales_income": round(m_extra, 2)})
        extra_rows.pop(r["month"], None)
    # v8.18.14: months with ONLY extra sales (no POS sales) still get a row
    # so the income is visible in the YTD monthly trend + chart
    for m_key in sorted(extra_rows):
        monthly.append({"month": m_key, "sales": 0.0, "cogs": 0.0,
                        "gross_profit": 0.0, "margin_pct": 0.0,
                        "extra_sales_income": round(extra_rows[m_key], 2)})
    monthly.sort(key=lambda m: m["month"])
    avg_of_monthly_margins = round(sum(m["margin_pct"] for m in monthly) / len(monthly), 2) if monthly else 0.0
    return {"opening_date": opening_date, "today": today, "ytd_sales": round(ytd_sales, 2),
            "ytd_cogs": round(ytd_cogs, 2), "ytd_gross_profit": round(ytd_gp, 2),
            "ytd_margin": round(ytd_margin, 2), "ytd_operating_expenses": round(float(op_exp_ytd or 0), 2),
            # v8.18.14: extra (non-POS) sales income — own line, included in operating profit
            "ytd_extra_sales_income": round(ytd_extra, 2),
            "ytd_operating_profit": round(ytd_op_profit, 2), "monthly": monthly,
            "avg_of_monthly_margins": avg_of_monthly_margins,
            "method_difference": round(ytd_margin - avg_of_monthly_margins, 2),
            "note": "YTD margin = Cumulative GP / Cumulative Sales (NOT avg of monthly margins). Extra Sales (non-POS) income is included in YTD Operating Profit but NOT in sales/margin (no COGS)."}


def get_store_profit_dashboard() -> dict:
    """Aggregate all 6 KPI groups for the Store Profit Dashboard."""
    from .profit_cash import get_cash_buckets, get_stock_reserve
    today = datetime.now().strftime("%Y-%m-%d")
    this_month = datetime.now().strftime("%Y-%m")
    stock_state = get_category_stock_state()
    margins = get_margins()
    daily = get_daily_stock_report(today)
    monthly = get_monthly_profit(this_month)
    ytd = get_ytd_profit()
    cash_buckets = get_cash_buckets(today)
    stock_reserve = get_stock_reserve()
    with conn() as c:
        # v8.7.1 fix: include ALL categories (active=0 too) so that stock_state
        # rows referencing inactive/deleted categories still show their real
        # name/code/sell_price instead of "Unknown"/"—". The previous
        # WHERE active=1 filter caused categories that were soft-deleted
        # (or created by the Ezi import with active=0) to appear as "Unknown"
        # in the Store Profit Dashboard.
        cat_meta = {r["id"]: r for r in c.execute(
            "SELECT id, name, code, sell_price, color FROM price_categories "
            "ORDER BY sort_order, code").fetchall()}
    per_category = []; total_qty = 0.0; total_value = 0.0
    missing_category_ids = []  # v8.7.2: track orphans for the warning
    for st in stock_state:
        cid = st["category_id"]
        meta = cat_meta.get(cid)
        if meta is None:
            # v8.7.2: stock_state references a category_id that doesn't exist
            # in price_categories. This happens when:
            #   - Categories were deleted (hard delete) but stock_state rows remain
            #   - The Ezi import created stock_state rows with category_ids that
            #     were never inserted into price_categories
            # Show "Category #N (missing)" so the user understands the data
            # integrity issue rather than just "Unknown" (which was confusing).
            missing_category_ids.append(cid)
            name = f"Category #{cid} (missing)"
            code = f"#{cid}"
            color = "#ef4444"  # red — signals data integrity issue
            sell_price = 0.0
        else:
            name = meta["name"] or "Unknown"
            code = meta["code"] or "—"
            color = meta["color"] or ""
            sell_price = float(meta["sell_price"] or 0)
        qty = float(st["current_qty"] or 0); value = float(st["current_value"] or 0)
        avg_cost = float(st["current_avg_cost"] or 0)
        margin_pct = round(((sell_price - avg_cost) / sell_price) * 100, 2) if sell_price > 0 else 0.0
        per_category.append({"category_id": cid, "code": code,
                             "name": name, "color": color,
                             "qty": round(qty, 2), "value": round(value, 2), "avg_cost": round(avg_cost, 2),
                             "sell_price": round(sell_price, 2), "margin_pct": margin_pct,
                             # v8.7.2: flag orphan rows so the UI can show a warning
                             "missing_category": meta is None})
        total_qty += qty; total_value += value
    today_sales = daily["totals"].get("sales_value", 0)
    today_cogs = daily["totals"].get("cogs", 0)
    today_gp = daily["totals"].get("gross_profit", 0)
    today_margin = round((today_gp / today_sales * 100), 2) if today_sales > 0 else 0.0
    reserve_color = stock_reserve["color"] if stock_reserve["color"] == "green" else \
                    "var(--warning-text)" if stock_reserve["color"] == "amber" else "var(--danger-text)"
    return {"date": today,
            "current_stock": {"total_qty": round(total_qty, 2), "total_value": round(total_value, 2),
                              "per_category": per_category},
            "current_margins": {"categories": margins["categories"],
                                "category_average_margin": margins["category_average_margin"],
                                "actual_overall_margin": margins["actual_overall_margin"],
                                "difference_pct": margins["difference_pct"],
                                "total_sales": margins["total_sales"],
                                "total_gross_profit": margins["total_gross_profit"]},
            "daily": {"sales": round(today_sales, 2), "cogs": round(today_cogs, 2),
                      "gross_profit": round(today_gp, 2), "margin": today_margin},
            "monthly": {"sales": monthly["sales"], "cogs": monthly["cogs"],
                        "gross_profit": monthly["gross_profit"], "monthly_margin": monthly["monthly_margin"],
                        "operating_expenses": monthly["operating_expenses"],
                        # v8.18.14: non-POS income — own line, inside operating profit
                        "extra_sales_income": monthly["extra_sales_income"],
                        "operating_profit": monthly["operating_profit"]},
            "ytd": {"sales": ytd["ytd_sales"], "cogs": ytd["ytd_cogs"],
                    "gross_profit": ytd["ytd_gross_profit"], "ytd_margin": ytd["ytd_margin"],
                    # v8.18.14: non-POS income YTD + operating profit (now includes it)
                    "ytd_extra_sales_income": ytd["ytd_extra_sales_income"],
                    "ytd_operating_profit": ytd["ytd_operating_profit"],
                    "opening_date": ytd["opening_date"]},
            "cash": {"buckets": cash_buckets["buckets"], "cash_in_drawer": cash_buckets["cash_in_drawer"],
                     "available_for_withdrawal": cash_buckets["available_for_withdrawal"],
                     "stock_reserve_days": stock_reserve["stock_reserve_days"],
                     "stock_reserve_target_days": stock_reserve["stock_reserve_target_days"],
                     "stock_reserve_color": stock_reserve["color"],
                     "stock_reserve_recommendation": stock_reserve["recommendation"],
                     "safe_withdrawal_weekly": stock_reserve["safe_withdrawal_weekly"],
                     "daily_cogs_avg_30d": stock_reserve["daily_cogs_avg_30d"]},
            # v8.7.2: surface orphan stock_state rows so the UI can warn the user
            "missing_categories": missing_category_ids,
            "missing_categories_warning": (
                f"{len(missing_category_ids)} stock_state row(s) reference category_ids "
                f"({', '.join(str(c) for c in missing_category_ids)}) that don't exist in "
                f"price_categories. Run POST /api/inventory/fix-missing-categories to auto-create them, "
                f"or POST /api/inventory/rebuild-stock-state to clear orphan stock_state rows."
            ) if missing_category_ids else None,
            "note": "Actual Overall Gross Margin is the primary KPI."}


def get_daily_stock_report(date: str = "") -> dict:
    """Per-category daily stock report with 11 columns."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    try:
        from datetime import timedelta as _td
        d = datetime.strptime(date, "%Y-%m-%d")
        next_day = (d + _td(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return {"error": f"Invalid date format: {date}"}
    with conn() as c:
        opening_rows = c.execute(
            "SELECT bi.category_id, SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS purchased, "
            "AVG(bi.price) AS avg_cost FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND COALESCE(b.bill_date, date(b.created_at)) < ? AND bi.category_id IS NOT NULL "
            "GROUP BY bi.category_id", (date,)).fetchall()
        opening_map = {r["category_id"]: {"qty": float(r["purchased"] or 0), "value": float(r["purchased"] or 0) * float(r["avg_cost"] or 0), "avg_cost": float(r["avg_cost"] or 0)} for r in opening_rows}
        sold_before = c.execute(
            "SELECT si.category_id, SUM(si.qty) AS qty, SUM(si.cost_price * si.qty) AS cogs "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            "WHERE s.payment_status IN ('paid', 'credit', 'partial') AND s.created_at < ? AND si.category_id IS NOT NULL GROUP BY si.category_id", (date,)).fetchall()
        sold_before_map = {r["category_id"]: {"qty": float(r["qty"] or 0), "cogs": float(r["cogs"] or 0)} for r in sold_before}
        adj_before = c.execute(
            "SELECT category_id, SUM(delta) AS delta FROM stock_adjustments WHERE created_at < ? AND category_id IS NOT NULL GROUP BY category_id", (date,)).fetchall()
        adj_before_map = {r["category_id"]: float(r["delta"] or 0) for r in adj_before}
        today_purchases = c.execute(
            "SELECT bi.category_id, SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS qty, "
            "SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS value "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND date(COALESCE(b.bill_date, date(b.created_at)))=? AND bi.category_id IS NOT NULL "
            "GROUP BY bi.category_id", (date,)).fetchall()
        today_purchases_map = {r["category_id"]: {"qty": float(r["qty"] or 0), "value": float(r["value"] or 0)} for r in today_purchases}
        today_sales = c.execute(
            "SELECT si.category_id, SUM(si.qty) AS qty, SUM(si.sell_price * si.qty) AS sales_value, SUM(si.cost_price * si.qty) AS cogs "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            "WHERE s.payment_status IN ('paid', 'credit', 'partial') AND date(s.created_at)=? AND si.category_id IS NOT NULL GROUP BY si.category_id", (date,)).fetchall()
        today_sales_map = {r["category_id"]: {"qty": float(r["qty"] or 0), "sales_value": float(r["sales_value"] or 0), "cogs": float(r["cogs"] or 0)} for r in today_sales}
        today_adj = c.execute(
            "SELECT category_id, SUM(delta) AS delta FROM stock_adjustments WHERE date(created_at)=? AND category_id IS NOT NULL GROUP BY category_id", (date,)).fetchall()
        today_adj_map = {r["category_id"]: float(r["delta"] or 0) for r in today_adj}
        # v8.18.18: bag categories follow the user's sold rule (see the
        # bags block comment in profit_engine) — their purchases are
        # virtual (raised so purchased ≥ sold) and their qty never goes
        # negative. Compute them with the clamped formula below.
        bag_ids = set()
        try:
            from .profit_engine import bag_category_ids as _dsr_bag_ids
            bag_ids = _dsr_bag_ids(c)
        except Exception:
            bag_ids = set()
        all_cat_ids = set(); all_cat_ids.update(opening_map.keys()); all_cat_ids.update(today_purchases_map.keys())
        all_cat_ids.update(today_sales_map.keys()); all_cat_ids.update(today_adj_map.keys())
        cat_meta = {r["id"]: dict(r) for r in c.execute("SELECT id, name, code, sell_price FROM price_categories").fetchall()}
    rows = []; totals = {"opening_qty": 0, "purchased_qty": 0, "sold_qty": 0, "closing_qty": 0,
                         "stock_value": 0, "sales_value": 0, "cogs": 0, "gross_profit": 0}
    for cat_id in sorted(all_cat_ids):
        meta = cat_meta.get(cat_id, {"name": "Unknown", "code": "—", "sell_price": 0})
        op = opening_map.get(cat_id, {"qty": 0, "value": 0, "avg_cost": 0})
        sb = sold_before_map.get(cat_id, {"qty": 0, "cogs": 0})
        ab = adj_before_map.get(cat_id, 0)
        state = get_category_stock_state(cat_id)
        avg_cost = state[0]["current_avg_cost"] if state else op["avg_cost"]
        tp = today_purchases_map.get(cat_id, {"qty": 0, "value": 0})
        ts = today_sales_map.get(cat_id, {"qty": 0, "sales_value": 0, "cogs": 0})
        ta = today_adj_map.get(cat_id, 0)
        if cat_id in bag_ids:
            # v8.18.18: bags — on-hand = max(purchases+adjustments − sold, 0)
            # on both sides of the day, and purchased_qty absorbs the
            # virtual raise so the row balances
            # (opening + purchased − sold = closing) and never shows
            # bags oversold.
            p_before = op["qty"] + ab
            p_total = op["qty"] + tp["qty"] + ab + ta
            s_before = sb["qty"]
            s_total = sb["qty"] + ts["qty"]
            opening_qty = max(p_before - s_before, 0.0)
            closing_qty = max(p_total - s_total, 0.0)
            purchased_qty = ts["qty"] + (closing_qty - opening_qty)
        else:
            opening_qty = op["qty"] - sb["qty"] + ab
            closing_qty = opening_qty + tp["qty"] - ts["qty"] + ta
            purchased_qty = tp["qty"]
        stock_value = closing_qty * avg_cost; sales_value = ts["sales_value"]; cogs = ts["cogs"]
        gross_profit = sales_value - cogs
        rows.append({"date": date, "category_id": cat_id,
                     "category": meta["name"] if isinstance(meta, dict) else "Unknown",
                     "code": meta["code"] if isinstance(meta, dict) else "—",
                     "opening_qty": round(opening_qty, 2), "purchased_qty": round(purchased_qty, 2),
                     "sold_qty": round(ts["qty"], 2), "closing_qty": round(closing_qty, 2),
                     "average_cost": round(avg_cost, 2), "stock_value": round(stock_value, 2),
                     "sales_value": round(sales_value, 2), "cogs": round(cogs, 2),
                     "gross_profit": round(gross_profit, 2)})
        totals["opening_qty"] += opening_qty; totals["purchased_qty"] += purchased_qty
        totals["sold_qty"] += ts["qty"]; totals["closing_qty"] += closing_qty
        totals["stock_value"] += stock_value; totals["sales_value"] += sales_value
        totals["cogs"] += cogs; totals["gross_profit"] += gross_profit
    totals = {k: round(v, 2) for k, v in totals.items()}
    return {"date": date, "rows": rows, "totals": totals}
