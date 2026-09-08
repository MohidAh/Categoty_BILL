"""v8.18.19 — LIVE MATH: calculation traces for every key metric.

WHY THIS EXISTS
    Users were doubting the numbers the system computes ("why is this margin
    X%?"). This module makes the math VISIBLE: for any reported metric it
    returns a step-by-step trace — every input number, the formula, the
    expression with the live numbers substituted in, and the result — plus
    provenance (which sales/bills were counted) and plain-language notes.

DESIGN PRINCIPLE — "the math you see is the math you get"
    Every trace derives its numbers from the SAME source functions the
    reports/pages use (profit_analytics.get_margins, get_monthly_profit,
    get_ytd_profit, shop.get_pnl, shop.get_actual_earnings,
    reports.profit_analysis_report). We never re-implement a formula here —
    we decompose the existing one. If a report ever changes, the trace
    changes with it; they cannot disagree.

USAGE
    GET /api/calc/trace?metric=overall_margin
    GET /api/calc/trace?metric=category_margin&category_id=2
    GET /api/calc/trace?metric=avg_cost&category_id=2
    GET /api/calc/trace?metric=monthly_margin&month=2026-08
    GET /api/calc/trace?metric=ytd_margin
    GET /api/calc/trace?metric=pnl_gross_margin&month=2026-08
    GET /api/calc/trace?metric=pnl_net_margin&month=2026-08
    GET /api/calc/trace?metric=actual_earnings_margin&month=2026-08
    GET /api/calc/trace?metric=category_average_margin
    GET /api/calc/trace?metric=pa_margin&start=2026-08-01&end=2026-08-31[&category_id=2]

TRACE SHAPE (consumed by static/js/components/live-math.js)
    {
      "metric": str, "title": str, "subtitle": str,
      "result": float, "result_unit": "percent" | "rs",
      "as_of": "YYYY-MM-DD HH:MM",
      "steps": [
        {"n": 1, "label": str, "formula": str,      # the general rule
         "expression": str | None,                  # numbers substituted in
         "value": float, "unit": "rs"|"pct"|"qty"|"count",
         "note": str | None, "is_result": bool}
      ],
      "events": [...]            # optional chronological replay (avg_cost)
      "provenance": [{"label": str, "value": str}],
      "notes": [str, ...],
      "replay_matches_state": bool | None   # avg_cost replay fidelity check
    }
"""
from datetime import datetime

from .db import conn
from . import db as db_mod

__all__ = ["get_trace", "UnknownMetric", "BadParams", "SUPPORTED_METRICS"]


class UnknownMetric(Exception):
    pass


class BadParams(Exception):
    pass


# ─── helpers ────────────────────────────────────────────────────────────────

def _n(x) -> float:
    """Coerce a DB number to float safely."""
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _num(n: float) -> str:
    """Plain number with 2 decimals + thousands commas, for expressions."""
    return f"{_n(n):,.2f}"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _step(n, label, formula, expression, value, unit, note=None, is_result=False) -> dict:
    return {"n": n, "label": label, "formula": formula, "expression": expression,
            "value": round(_n(value), 4), "unit": unit, "note": note,
            "is_result": is_result}


def _sale_status_counts(c) -> dict:
    row = c.execute(
        "SELECT payment_status, COUNT(*) AS n FROM sales "
        "GROUP BY payment_status").fetchall()
    return {r["payment_status"]: int(r["n"] or 0) for r in row}


def _valid_sale_provenance(c) -> list:
    counts = _sale_status_counts(c)
    paid, credit, partial = counts.get("paid", 0), counts.get("credit", 0), counts.get("partial", 0)
    refunded = counts.get("refunded", 0)
    span = c.execute(
        "SELECT MIN(created_at) AS a, MAX(created_at) AS b FROM sales "
        f"WHERE {db_mod.VALID_SALE_FILTER_NO_ALIAS}").fetchone()
    items = c.execute(
        "SELECT COUNT(*) AS n FROM sale_items si JOIN sales s ON si.sale_id=s.id "
        f"WHERE {db_mod.VALID_SALE_FILTER}").fetchone()["n"]
    prov = [
        {"label": "Sales counted (valid)", "value": f"{paid + credit + partial} — paid {paid}, credit {credit}, partial {partial}"},
        {"label": "Sales excluded (refunded)", "value": str(refunded)},
        {"label": "Sale line items counted", "value": str(int(items or 0))},
    ]
    if span and span["a"]:
        prov.append({"label": "Included sales range", "value": f"{span['a'][:16]} → {span['b'][:16]}"})
    return prov


# ─── metric: overall_margin (Margins page hero, Store Profit dashboard) ─────

def _trace_overall_margin(params: dict) -> dict:
    """Actual Overall Gross Margin = Total GP ÷ Total Sales (all-time).

    Basis (same as profit_analytics.get_margins): line-level
    Σ(sell_price × qty) over valid sales — NOT sales.total. Cost uses
    sale_items.cost_price captured at sale time.
    """
    from .profit_analytics import get_margins
    m = get_margins()
    total_sales = _n(m["total_sales"])
    total_cogs = _n(m["total_cogs"])
    gp = _n(m["total_gross_profit"])
    margin = _n(m["actual_overall_margin"])

    steps = [
        _step(1, "Total Sales", "Σ (line sell_price × qty) over all valid sales",
              None, total_sales, "rs",
              "Line-level basis — before sale-level discounts. See P&L for the post-discount revenue view."),
        _step(2, "Total COGS", "Σ (cost_price × qty) — cost captured at the moment of each sale",
              None, total_cogs, "rs",
              "Historical cost: the running average cost at the time the sale happened."),
        _step(3, "Total Gross Profit", "Sales − COGS",
              f"{_num(total_sales)} − {_num(total_cogs)}", gp, "rs"),
        _step(4, "Actual Overall Gross Margin", "Gross Profit ÷ Sales × 100",
              f"{_num(gp)} ÷ {_num(total_sales)} × 100", margin, "percent",
              "Sales-mix weighted: every rupee of every valid sale counts.", is_result=True),
    ]
    with conn() as c:
        prov = _valid_sale_provenance(c)
    return {
        "metric": "overall_margin", "title": "Actual Overall Gross Margin",
        "subtitle": "The primary KPI on the Margins page and Store Profit dashboard",
        "result": margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps, "provenance": prov,
        "notes": [
            "Refunded sales are fully excluded (payment_status 'refunded').",
            "Cost prices are frozen at sale time — later purchase bills do NOT rewrite past margins.",
        ],
    }


# ─── metric: category_average_margin ───────────────────────────────────────

def _trace_category_average(params: dict) -> dict:
    """Simple mean of per-category margin % — informational only."""
    from .profit_analytics import get_margins
    m = get_margins()
    included, excluded = [], []
    for cat in m["categories"]:
        sell, cost = _n(cat["sell_price"]), _n(cat["avg_cost"])
        if sell > 0 and cost > 0:
            included.append(cat)
        else:
            excluded.append(cat)

    steps = [
        _step(1, "Per-category margin %", "For each category: (Sell Price − Avg Cost) ÷ Sell Price × 100",
              None, len(included), "count",
              f"{len(included)} categories included, {len(excluded)} excluded (see provenance)."),
    ]
    n = 2
    if included:
        expr = " + ".join(f"({cat['margin_pct']:.2f})" for cat in included)
        total = sum(_n(cat["margin_pct"]) for cat in included)
        steps.append(_step(n, "Sum of category margins", "Add every included category's margin %",
                           expr, total, "percent")); n += 1
        steps.append(_step(n, "Category Average Margin", "Sum ÷ number of included categories",
                           f"{_num(total)} ÷ {len(included)}", _n(m["category_average_margin"]), "percent",
                           "Plain mean — every category weighs the same, regardless of how much it sells.", is_result=True))
    else:
        steps.append(_step(n, "Category Average Margin", "No categories with cost > 0",
                           None, 0.0, "percent", is_result=True))

    prov = []
    for cat in included:
        prov.append({"label": f"{cat['code']} · {cat['name']}", "value":
                     f"sell {_num(cat['sell_price'])} − cost {_num(cat['avg_cost'])} = {cat['margin_pct']}%"})
    for cat in excluded:
        why = "no sell price" if _n(cat["sell_price"]) <= 0 else "avg cost is 0 (no purchase bill yet)"
        prov.append({"label": f"{cat['code']} · {cat['name']} (excluded)", "value": why})
    return {
        "metric": "category_average_margin", "title": "Category Average Margin",
        "subtitle": "Informational mean of category margins — ignores sales mix",
        "result": _n(m["category_average_margin"]), "result_unit": "percent", "as_of": _now(),
        "steps": steps, "provenance": prov,
        "notes": [
            "Categories with avg cost 0 (never purchased) are excluded — they would show a misleading 100% margin.",
            "The Actual Overall Gross Margin is the primary KPI; this number is shown for comparison only.",
        ],
    }


# ─── metric: category_margin (one row of the Margins page) ─────────────────

def _get_category(c, category_id: int):
    return c.execute(
        "SELECT id, code, name, sell_price FROM price_categories WHERE id=?",
        (category_id,)).fetchone()


def _category_state(c, category_id: int) -> dict:
    row = c.execute(
        "SELECT current_qty, current_value, current_avg_cost FROM category_stock_state "
        "WHERE category_id=?", (category_id,)).fetchone()
    if row is None:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0}
    return {"qty": _n(row["current_qty"]), "value": _n(row["current_value"]),
            "avg": _n(row["current_avg_cost"])}


def _trace_category_margin(params: dict) -> dict:
    category_id = params.get("category_id")
    if not category_id:
        raise BadParams("category_id is required for category_margin")
    with conn() as c:
        cat = _get_category(c, int(category_id))
        if cat is None:
            raise BadParams(f"Category {category_id} not found")
        st = _category_state(c, int(category_id))
        purchased = c.execute(
            "SELECT COALESCE(SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END),0) AS q "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE bi.category_id=? AND b.status='confirmed' AND b.deleted_at IS NULL",
            (int(category_id),)).fetchone()["q"]
        sold = c.execute(
            "SELECT COALESCE(SUM(si.qty),0) AS q FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db_mod.VALID_SALE_FILTER} AND si.category_id=?",
            (int(category_id),)).fetchone()["q"]

    sell = _n(cat["sell_price"])
    cost = st["avg"]
    margin_rs = sell - cost
    margin_pct = round((margin_rs / sell) * 100, 2) if sell > 0 else 0.0

    steps = [
        _step(1, "Sell Price (current)", "The category's price-list sell price", None, sell, "rs"),
        _step(2, "Avg Cost (running)", "category_stock_state.current_avg_cost — pool value ÷ pool qty",
              f"{_num(st['value'])} ÷ {_num(st['qty'])}", cost, "rs",
              f"Pool: {_num(st['qty'])} units worth Rs {_num(st['value'])} from confirmed bills."),
        _step(3, "Margin (Rs)", "Sell Price − Avg Cost",
              f"{_num(sell)} − {_num(cost)}", margin_rs, "rs"),
        _step(4, "Margin %", "(Sell − Cost) ÷ Sell × 100",
              f"({_num(sell)} − {_num(cost)}) ÷ {_num(sell)} × 100", margin_pct, "percent",
              "Forward-looking: the margin the NEXT sale at list price would make.", is_result=True),
    ]
    prov = [
        {"label": "Purchased (all time)", "value": f"{_num(purchased)} units"},
        {"label": "Sold (all time, valid sales)", "value": f"{_num(sold)} units"},
        {"label": "On hand now", "value": f"{_num(st['qty'])} units"},
        {"label": "Pool value now", "value": f"Rs {_num(st['value'])}"},
    ]
    return {
        "metric": "category_margin", "title": f"Margin — {cat['code']} · {cat['name']}",
        "subtitle": "Per-category margin on the Margins page (current, forward-looking)",
        "result": margin_pct, "result_unit": "percent", "as_of": _now(),
        "steps": steps, "provenance": prov,
        "notes": [
            "Avg cost is the running weighted average of all confirmed purchases less what was sold — click any Avg Cost on the Current Stock page to see its full replay.",
        ],
    }


# ─── metric: avg_cost (with full chronological replay) ──────────────────────

def _trace_avg_cost(params: dict) -> dict:
    """The weighted average cost, PLUS the event-by-event replay of how it
    came to be — every confirmed purchase adds qty×price to the pool, every
    valid sale removes qty×avg at that moment, exactly like the engine does.

    The replay ends at the same numbers as category_stock_state when the
    state is consistent; if it doesn't, we SAY SO (visible honesty beats
    silent trust).
    """
    category_id = params.get("category_id")
    if not category_id:
        raise BadParams("category_id is required for avg_cost")
    category_id = int(category_id)
    with conn() as c:
        cat = _get_category(c, category_id)
        if cat is None:
            raise BadParams(f"Category {category_id} not found")
        st = _category_state(c, category_id)

        # Purchases: confirmed, non-deleted bills. Engine applies them at
        # confirm time; we order by the bill's created_at (processing order).
        purchases = c.execute(
            "SELECT b.bill_no, COALESCE(b.bill_date, date(b.created_at)) AS d, "
            "b.created_at AS applied_at, "
            "CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END AS qty, "
            "bi.price, bi.line_total "
            "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
            "WHERE bi.category_id=? AND b.status='confirmed' AND b.deleted_at IS NULL "
            "ORDER BY b.created_at, bi.id",
            (category_id,)).fetchall()
        # Sales: valid (refunds were reversed in state too).
        sales = c.execute(
            "SELECT s.invoice_no, si.qty, si.cost_price, s.created_at "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db_mod.VALID_SALE_FILTER} AND si.category_id=? "
            "ORDER BY s.created_at, si.id",
            (category_id,)).fetchall()
        # Adjustments (damaged/lost/found).
        adjs = c.execute(
            "SELECT delta, reason, created_at FROM stock_adjustments "
            "WHERE category_id=? ORDER BY created_at, id",
            (category_id,)).fetchall()

    events = []
    for r in purchases:
        events.append({"at": r["applied_at"] or "", "sort": r["applied_at"] or "",
                       "type": "purchase", "label": f"Bill {r['bill_no'] or '—'} ({r['d']})",
                       "qty": _n(r["qty"]), "price": _n(r["price"])})
    for r in sales:
        events.append({"at": r["created_at"] or "", "sort": r["created_at"] or "",
                       "type": "sale", "label": f"Sale {r['invoice_no'] or '—'}",
                       "qty": _n(r["qty"]), "price": None})
    for r in adjs:
        events.append({"at": r["created_at"] or "", "sort": r["created_at"] or "",
                       "type": "adjustment", "label": f"Adjustment ({r['reason'] or '—'})",
                       "qty": _n(r["delta"]), "price": None})
    events.sort(key=lambda e: e["sort"])

    # Replay exactly like profit_engine._apply_*_to_state:
    #  purchase: qty += q, value += q*price, avg = round(value/qty, 2)
    #  sale:     cogs = round(q * avg, 2); qty -= q; value -= cogs (avg unchanged)
    #  adjust:   delta<0: value -= round(|d|*avg,2); delta>=0: value += round(d*avg,2),
    #            avg recomputed
    qty, value, avg = 0.0, 0.0, 0.0
    for e in events:
        if e["type"] == "purchase":
            q, price = e["qty"], e["price"]
            qty += q
            value = round(value + q * price, 2)
            avg = round(value / qty, 2) if qty > 0 else 0.0
            e["qty_delta"], e["value_delta"] = q, round(q * price, 2)
        elif e["type"] == "sale":
            q = e["qty"]
            cogs = round(q * avg, 2)
            qty -= q
            value = round(value - cogs, 2)
            e["qty_delta"], e["value_delta"] = -q, -cogs
            e["label"] += f" — COGS @ Rs {avg:,.2f}"
        else:  # adjustment
            d = e["qty"]
            if d < 0:
                ch = round(abs(d) * avg, 2)
            else:
                ch = round(d * avg, 2)
            qty += d
            value = round(value + (ch if d >= 0 else -ch), 2)
            avg = round(value / qty, 2) if qty > 0 else 0.0
            e["qty_delta"], e["value_delta"] = d, (ch if d >= 0 else -ch)
        e["qty_after"], e["value_after"], e["avg_after"] = qty, value, avg

    replay_matches = (abs(qty - st["qty"]) < 0.011
                      and abs(value - st["value"]) < 0.011
                      and abs(avg - st["avg"]) < 0.011)

    steps = [
        _step(1, "Pool quantity now", "category_stock_state.current_qty — purchases − sold ± adjustments",
              None, st["qty"], "qty"),
        _step(2, "Pool value now", "category_stock_state.current_value — total cost of what's on the shelf",
              None, st["value"], "rs"),
        _step(3, "Avg Cost", "Pool value ÷ Pool qty",
              f"{_num(st['value'])} ÷ {_num(st['qty'])}", st["avg"], "rs",
              "The weighted average cost of every unit currently in stock.", is_result=True),
    ]
    notes = [
        "Each confirmed purchase bill ADDS (qty × unit price) to the pool value — that's the 'weighted' part.",
        "Each sale REMOVES qty × avg-cost-at-that-moment (that becomes COGS). Selling never changes the avg.",
        "Refunds and voided sales are excluded — they were already reversed out of the pool.",
    ]
    if not replay_matches:
        notes.append(
            f"Heads-up: the chronological replay of bills/sales ends at qty {_num(qty)} / value "
            f"Rs {_num(value)}, but the stored state says qty {_num(st['qty'])} / Rs "
            f"{_num(st['value'])}. This can happen after back-dated bills, imports, or manual "
            "repairs. The stored state is what the app uses; run Stock → Repair if you want them re-synced.")
    prov = [
        {"label": "Confirmed purchase bill lines", "value": str(len(purchases))},
        {"label": "Valid sale lines", "value": str(len(sales))},
        {"label": "Stock adjustments", "value": str(len(adjs))},
    ]
    return {
        "metric": "avg_cost", "title": f"Avg Cost — {cat['code']} · {cat['name']}",
        "subtitle": "Running weighted average cost, replayed event by event",
        "result": st["avg"], "result_unit": "rs", "as_of": _now(),
        "steps": steps, "events": events, "provenance": prov,
        "notes": notes, "replay_matches_state": replay_matches,
    }


# ─── metric: monthly_margin (COGS bridge) ──────────────────────────────────

def _trace_monthly(params: dict) -> dict:
    month = params.get("month") or datetime.now().strftime("%Y-%m")
    from .profit_analytics import get_monthly_profit
    r = get_monthly_profit(month)
    if "error" in r:
        raise BadParams(r["error"])

    sales, cogs = _n(r["sales"]), _n(r["cogs"])
    gp = _n(r["gross_profit"])
    margin = _n(r["monthly_margin"])
    cfs = _n(r["cogs_from_sales"])

    steps = [
        _step(1, "Opening Inventory (value)", "All confirmed purchases − all COGS + adjustments, before the month started",
              None, _n(r["opening_inventory"]), "rs"),
        _step(2, "Inventory after Purchases", "Purchases = confirmed bills dated this month (Σ qty × unit price)",
              f"{_num(r['opening_inventory'])} + {_num(r['purchases'])}",
              _n(r["opening_inventory"]) + _n(r["purchases"]), "rs"),
        _step(3, "COGS (bridge)", "Closing inventory = same formula, measured at month end",
              f"{_num(_n(r['opening_inventory']) + _n(r['purchases']))} − {_num(r['closing_inventory'])}",
              cogs, "rs",
              "COGS = Opening + Purchases − Closing (the bridge method)."),
        _step(4, "Sales this month", "Σ sales.total — what customers actually paid (after discounts)",
              None, sales, "rs"),
        _step(5, "Gross Profit", "Sales − COGS",
              f"{_num(sales)} − {_num(cogs)}", gp, "rs"),
        _step(6, "Monthly Margin", "Gross Profit ÷ Sales × 100",
              f"{_num(gp)} ÷ {_num(sales)} × 100", margin, "percent", is_result=True),
        _step(7, "Cross-check: COGS from sales lines", "Σ (cost_price × qty) of this month's sale items",
              None, cfs, "rs",
              f"Bridge COGS − line COGS = Rs {_num(_n(r['cogs_difference']))}. Small differences are rounding + stock adjustments."),
    ]
    with conn() as c:
        month_sales = c.execute(
            "SELECT COUNT(*) AS n FROM sales WHERE strftime('%Y-%m', created_at)=? "
            f"AND {db_mod.VALID_SALE_FILTER_NO_ALIAS}", (month,)).fetchone()["n"]
        month_bills = c.execute(
            "SELECT COUNT(*) AS n FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', COALESCE(bill_date, date(created_at)))=?", (month,)).fetchone()["n"]
    prov = [
        {"label": "Month", "value": month},
        {"label": "Valid sales in month", "value": str(int(month_sales or 0))},
        {"label": "Confirmed bills in month", "value": str(int(month_bills or 0))},
        {"label": "Extra (non-POS) income in month", "value": f"Rs {_num(r['extra_sales_income'])}"},
    ]
    return {
        "metric": "monthly_margin", "title": f"Monthly Margin — {month}",
        "subtitle": "COGS bridge method: Opening + Purchases − Closing",
        "result": margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps, "provenance": prov,
        "notes": [
            "The bridge measures profit by inventory movement — it catches unrecorded sales/losses that pure line-COGS misses.",
            "Operating expenses are NOT part of margin — see the Operating Profit card on the same page.",
        ],
    }


# ─── metric: ytd_margin ─────────────────────────────────────────────────────

def _trace_ytd(params: dict) -> dict:
    from .profit_analytics import get_ytd_profit
    r = get_ytd_profit()
    sales, cogs = _n(r["ytd_sales"]), _n(r["ytd_cogs"])
    gp = _n(r["ytd_gross_profit"])
    margin = _n(r["ytd_margin"])

    steps = [
        _step(1, "YTD Sales", "Σ sales.total from the opening date to today",
              None, sales, "rs"),
        _step(2, "YTD COGS", "Σ (cost_price × qty) of all valid sale items in the period",
              None, cogs, "rs"),
        _step(3, "YTD Gross Profit", "Sales − COGS",
              f"{_num(sales)} − {_num(cogs)}", gp, "rs"),
        _step(4, "YTD Margin", "Cumulative GP ÷ Cumulative Sales × 100",
              f"{_num(gp)} ÷ {_num(sales)} × 100", margin, "percent",
              "NOT the average of monthly margins — each month weighs by its sales volume.", is_result=True),
        _step(5, "Compare: average of monthly margins",
              "Sum of monthly margin % ÷ number of months",
              None, _n(r["avg_of_monthly_margins"]), "percent",
              f"Difference {_num(_n(r['method_difference']))}% — the cumulative method is the correct one."),
    ]
    return {
        "metric": "ytd_margin", "title": "YTD Gross Margin",
        "subtitle": f"{r['opening_date']} → today, cumulative",
        "result": margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps,
        "provenance": [
            {"label": "Opening date", "value": r["opening_date"]},
            {"label": "Months on record", "value": str(len(r.get("monthly", [])))},
            {"label": "YTD operating expenses", "value": f"Rs {_num(r['ytd_operating_expenses'])}"},
            {"label": "YTD extra (non-POS) income", "value": f"Rs {_num(r['ytd_extra_sales_income'])}"},
        ],
        "notes": ["Extra (non-POS) sales income is NOT in margin — it has no cost, so a margin on it is meaningless."],
    }


# ─── metrics: pnl_gross_margin / pnl_net_margin ────────────────────────────

def _trace_pnl(params: dict, which: str) -> dict:
    month = params.get("month") or datetime.now().strftime("%Y-%m")
    from . import shop
    r = shop.get_pnl(month)
    revenue, cogs = _n(r["net_revenue"]), _n(r["cost_of_goods"])
    gp = _n(r["gross_profit"])
    exp = _n(r["expenses"])
    other = _n(r["other_income"])
    net = _n(r["net_profit"])
    gross_pct = round(gp / revenue * 100, 2) if revenue > 0 else 0.0
    net_pct = round(net / revenue * 100, 2) if revenue > 0 else 0.0

    steps = [
        _step(1, "Net Revenue", "Σ sales.total of the month's valid sales (already after discounts)",
              None, revenue, "rs"),
        _step(2, "Cost of Goods Sold", "Σ (cost_price × qty) of the month's sale items",
              None, cogs, "rs"),
        _step(3, "Gross Profit", "Net Revenue − COGS",
              f"{_num(revenue)} − {_num(cogs)}", gp, "rs"),
        _step(4, "Gross Margin", "GP ÷ Revenue × 100",
              f"{_num(gp)} ÷ {_num(revenue)} × 100", gross_pct, "percent"),
        # v8.18.20: labels describe the running VALUE, not the input
        _step(5, "After Operating Expenses", "Operating expenses = expenses where expense_type = 'operating'",
              f"{_num(gp)} − {_num(exp)}", gp - exp, "rs",
              "Owner draws are NOT here — they are equity, not an expense."),
        _step(6, "After Other Income", "Other income = extra (non-POS) sales — cartons, raddi — no COGS",
              f"{_num(gp - exp)} + {_num(other)}", gp - exp + other, "rs"),
    ]
    if which == "gross":
        result, result_unit, title = gross_pct, "percent", "P&L Gross Margin"
        steps[3]["is_result"] = True
    else:
        steps.append(_step(7, "Net Profit", "Gross Profit + Other Income − Operating Expenses",
                           f"{_num(gp)} + {_num(other)} − {_num(exp)}", net, "rs"))
        steps.append(_step(8, "Net Margin", "Net Profit ÷ Net Revenue × 100",
                           f"{_num(net)} ÷ {_num(revenue)} × 100", net_pct, "percent", is_result=True))
        result, result_unit, title = net_pct, "percent", "P&L Net Margin"

    return {
        "metric": f"pnl_{which}_margin", "title": f"{title} — {month}",
        "subtitle": "Monthly Profit & Loss statement",
        "result": result, "result_unit": "percent", "as_of": _now(),
        "steps": steps,
        "provenance": [
            {"label": "Month", "value": month},
            {"label": "Discounts given (already inside revenue)", "value": f"Rs {_num(r['discounts'])}"},
            {"label": "Purchases (bills) same month", "value": f"Rs {_num(r['purchases'])} (shown separately — cash view)"},
            {"label": "Owner draws (equity, not expense)", "value": f"Rs {_num(r['owner_draws'])}"},
        ],
        "notes": ["Purchases are NOT subtracted from profit — only COGS is. Buying stock is not losing money; selling below cost is."],
    }


# ─── metric: actual_earnings_margin ────────────────────────────────────────

def _trace_actual_earnings(params: dict) -> dict:
    month = params.get("month") or datetime.now().strftime("%Y-%m")
    from . import shop
    r = shop.get_actual_earnings(month)
    sales, cogs = _n(r["total_sales"]), _n(r["cogs"])
    gp = _n(r["gross_profit"])
    exp = _n(r["operating_expenses"])
    other = _n(r["extra_sales_income"])
    earnings = _n(r["actual_earnings"])
    net_margin = round(earnings / sales * 100, 2) if sales > 0 else 0.0

    # v8.18.20: waterfall step LABELS describe the VALUE (the running total),
    # not the input being applied — a step labelled '− COGS' showing the gross
    # profit number made users think COGS itself was that number.
    steps = [
        _step(1, "Sales (revenue)", "Σ sales.total of the month's valid sales", None, sales, "rs"),
        _step(2, "Gross Profit (Sales − COGS)", "COGS = Σ (cost_price × qty) of the month's sale items",
              f"{_num(sales)} − {_num(cogs)}", gp, "rs"),
        _step(3, "After Other Income", "Other income = extra (non-POS) sales — cartons, raddi — no COGS",
              f"{_num(gp)} + {_num(other)}", gp + other, "rs"),
        _step(4, "Actual Earnings (after Expenses)", "Operating expenses: rent, salaries, bills… for the month",
              f"{_num(gp + other)} − {_num(exp)}", earnings, "rs"),
        _step(5, "Actual Earnings Margin", "Actual Earnings ÷ Sales × 100",
              f"{_num(earnings)} ÷ {_num(sales)} × 100", net_margin, "percent", is_result=True),
    ]
    return {
        "metric": "actual_earnings_margin", "title": f"Actual Earnings Margin — {month}",
        "subtitle": "The 'truth' dashboard: what the business actually kept",
        "result": net_margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps,
        "provenance": [
            {"label": "Month", "value": month},
            {"label": "Purchases (bills) shown separately", "value": f"Rs {_num(r['purchases'])}"},
            {"label": "Owner draws (equity, not expense)", "value": f"Rs {_num(r['owner_draws'])}"},
            {"label": "Last month earnings", "value": f"Rs {_num(r['comparison']['last_month_earnings'])}"},
        ],
        "notes": ["Same formula as P&L Net Profit — Actual Earnings is the cash-side view of the same truth."],
    }


# ─── metric: pa_margin (Profit Analysis date-range) ────────────────────────

def _trace_pa(params: dict) -> dict:
    start, end = params.get("start"), params.get("end")
    if not start or not end:
        raise BadParams("start and end dates are required for pa_margin (YYYY-MM-DD)")
    category_id = params.get("category_id")
    from .reports import profit_analysis_report
    r = profit_analysis_report(start, end, "category")
    if "error" in r:
        raise BadParams(r["error"])

    cats = r.get("categories", [])
    title = f"Profit Analysis Margin — {start} → {end}"
    if category_id:
        row = next((x for x in cats if int(x.get("category_id") or 0) == int(category_id)), None)
        if row is None:
            raise BadParams(f"No sales for category {category_id} in this period")
        rev, cogs = _n(row["revenue"]), _n(row["cogs"])
        gp = _n(row["gross_profit"])
        margin = _n(row["margin_pct"])
        title = f"Margin — {row.get('code')} · {row.get('name')} ({start} → {end})"
        cat_note = ("Revenue = the category's share of sales.total, allocated by each line's charged value. "
                    "COGS = Σ cost_price × qty at sale time.")
    else:
        t = r.get("totals", {})
        rev, cogs = _n(t["revenue"]), _n(t["cogs"])
        gp = _n(t["gross_profit"])
        margin = _n(t["margin_pct"])
        cat_note = "Revenue uses sales.total (post-discount) allocated to categories by their share of each sale."

    steps = [
        _step(1, "Revenue in range", "Σ sales.total (post-discount), allocated to this scope",
              None, rev, "rs", cat_note),
        _step(2, "COGS in range", "Σ (cost_price × qty) of sale items in range",
              None, cogs, "rs", "Costs are the historical ones captured at sale time."),
        _step(3, "Gross Profit", "Revenue − COGS",
              f"{_num(rev)} − {_num(cogs)}", gp, "rs"),
        _step(4, "Margin", "GP ÷ Revenue × 100",
              f"{_num(gp)} ÷ {_num(rev)} × 100", margin, "percent",
              "Historical margin — what you actually realized on past sales (vs the Margins page's forward-looking one).",
              is_result=True),
    ]
    return {
        "metric": "pa_margin", "title": title,
        "subtitle": "Date-range profit analysis",
        "result": margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps,
        "provenance": [
            {"label": "Date range", "value": f"{start} → {end}"},
            {"label": "Extra (non-POS) income in range", "value": f"Rs {_num(r.get('totals', {}).get('extra_sales_income', 0))}"},
        ],
        "notes": ["Refunded sales are excluded."],
    }


# ─── metric: daily_margin (Store Profit dashboard "Today" card) ─────────────

def _trace_daily(params: dict) -> dict:
    d = params.get("date") or datetime.now().strftime("%Y-%m-%d")
    from .profit_analytics import get_daily_stock_report
    r = get_daily_stock_report(d)
    t = r.get("totals", {})
    sales, cogs = _n(t.get("sales_value")), _n(t.get("cogs"))
    gp = _n(t.get("gross_profit"))
    margin = round(gp / sales * 100, 2) if sales > 0 else 0.0

    steps = [
        _step(1, "Today's Sales", "Σ (line sell_price × qty) of today's valid sales",
              None, sales, "rs", "Line-level basis for the daily card."),
        _step(2, "Today's COGS", "Σ (cost_price × qty) of today's sale items",
              None, cogs, "rs"),
        _step(3, "Today's Gross Profit", "Sales − COGS",
              f"{_num(sales)} − {_num(cogs)}", gp, "rs"),
        _step(4, "Today's Margin", "Gross Profit ÷ Sales × 100",
              f"{_num(gp)} ÷ {_num(sales)} × 100", margin, "percent", is_result=True),
    ]
    return {
        "metric": "daily_margin", "title": f"Today's Margin — {d}",
        "subtitle": "Store Profit Dashboard, the Today card",
        "result": margin, "result_unit": "percent", "as_of": _now(),
        "steps": steps,
        "provenance": [{"label": "Date", "value": d}],
        "notes": ["A single day is a small sample — one big discounted sale can swing it wildly."],
    }


# ─── registry ──────────────────────────────────────────────────────────────

_REGISTRY = {
    "overall_margin": _trace_overall_margin,
    "category_average_margin": _trace_category_average,
    "category_margin": _trace_category_margin,
    "avg_cost": _trace_avg_cost,
    "daily_margin": _trace_daily,
    "monthly_margin": _trace_monthly,
    "ytd_margin": _trace_ytd,
    "pnl_gross_margin": lambda p: _trace_pnl(p, "gross"),
    "pnl_net_margin": lambda p: _trace_pnl(p, "net"),
    "actual_earnings_margin": _trace_actual_earnings,
    "pa_margin": _trace_pa,
}

SUPPORTED_METRICS = sorted(_REGISTRY.keys())


def get_trace(metric: str, params: dict) -> dict:
    """Build the live-math trace for a metric. Raises UnknownMetric / BadParams."""
    fn = _REGISTRY.get(metric or "")
    if fn is None:
        raise UnknownMetric(
            f"Unknown metric '{metric}'. Supported: {', '.join(SUPPORTED_METRICS)}")
    trace = fn(params or {})
    # Contract guards — the frontend depends on these keys.
    trace.setdefault("steps", [])
    trace.setdefault("provenance", [])
    trace.setdefault("notes", [])
    trace.setdefault("events", None)
    trace.setdefault("replay_matches_state", None)
    trace.setdefault("subtitle", "")
    return trace
