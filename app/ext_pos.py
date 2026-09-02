"""v7.2 Phase 1 — POS mechanics + money intelligence (extensions split 1/3).

Bundles, happy-hour, lost-sales, break-even, margin alerts, cash-flow forecast.
Extracted from extensions.py (was 693 lines; this module is ~240 lines).
"""
from datetime import datetime, timedelta
from .db import conn, get_setting, log_activity
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant


# ─── Bundles ───────────────────────────────────────────────────────────────

def list_bundles(active_only: bool = False) -> list:
    with conn() as c:
        sql = ("SELECT b.*, GROUP_CONCAT(bi.category_id || ':' || bi.qty) AS items_str "
               "FROM bundles b LEFT JOIN bundle_items bi ON bi.bundle_id=b.id ")
        if active_only: sql += "WHERE b.active=1 "
        sql += "GROUP BY b.id ORDER BY b.id"
        rows = c.execute(sql).fetchall()
    out = []
    for r in rows:
        d = dict(r); items = []
        if d.get("items_str"):
            for pair in d["items_str"].split(","):
                cid, qty = pair.split(":"); items.append({"category_id": int(cid), "qty": int(qty)})
        d["items"] = items; del d["items_str"]; out.append(d)
    return out


def create_bundle(name: str, price: float, items: list) -> int:
    with conn() as c:
        bid = c.execute("INSERT INTO bundles(name, price) VALUES(?,?)", (name, price)).lastrowid
        for item in items:
            c.execute("INSERT INTO bundle_items(bundle_id, category_id, qty) VALUES(?,?,?)",
                      (bid, item["category_id"], item["qty"]))
    return bid


def delete_bundle(bid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM bundles WHERE id=?", (bid,)); return cur.rowcount > 0


def get_bundle_sell_price_allocation(bundle_id: int) -> list:
    with conn() as c:
        bundle = c.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not bundle: return []
        items = c.execute(
            "SELECT bi.*, pc.sell_price, pc.code FROM bundle_items bi "
            "LEFT JOIN price_categories pc ON bi.category_id=pc.id WHERE bi.bundle_id=?", (bundle_id,)).fetchall()
    total_individual = sum(float(r["sell_price"] or 0) * r["qty"] for r in items)
    result = []
    for r in items:
        if total_individual > 0:
            allocated = round(float(bundle["price"]) * (float(r["sell_price"] or 0) * r["qty"]) / total_individual, 2)
        else:
            allocated = round(float(bundle["price"]) / len(items), 2)
        result.append({"category_id": r["category_id"], "code": r["code"] or "",
                       "qty": r["qty"], "allocated_sell_price": allocated})
    return result


# ─── Happy-Hour Pricing ────────────────────────────────────────────────────

def list_price_rules(active_only: bool = True) -> list:
    with conn() as c:
        sql = "SELECT * FROM price_rules"
        if active_only: sql += " WHERE active=1"
        sql += " ORDER BY id"
        rows = c.execute(sql).fetchall()
    return [dict(r) for r in rows]


def create_price_rule(category_id: int, pct: float, start_hhmm: str, end_hhmm: str) -> int:
    with conn() as c:
        return c.execute("INSERT INTO price_rules(category_id, pct, start_hhmm, end_hhmm) VALUES(?,?,?,?)",
                         (category_id, pct, start_hhmm, end_hhmm)).lastrowid


def delete_price_rule(rid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM price_rules WHERE id=?", (rid,)); return cur.rowcount > 0


def get_active_happy_hour_discount(category_id: int = None) -> dict | None:
    now_hhmm = datetime.now().strftime("%H%M")
    with conn() as c:
        for cid_filter in ([category_id, None] if category_id else [None]):
            row = c.execute(
                "SELECT * FROM price_rules WHERE active=1 "
                "AND (category_id=? OR category_id IS NULL) "
                "AND start_hhmm <= ? AND end_hhmm > ? "
                "ORDER BY (category_id IS NOT NULL) DESC LIMIT 1",
                (cid_filter, now_hhmm, now_hhmm)).fetchone()
            if row:
                return {"pct": float(row["pct"]), "rule_id": row["id"], "category_id": row["category_id"]}
    return None


# ─── Lost-Sale Tracking ────────────────────────────────────────────────────

def log_lost_sale(category_id: int, qty: int, est_revenue: float = 0):
    with conn() as c:
        c.execute("INSERT INTO lost_sales(category_id, qty, est_revenue) VALUES(?,?,?)",
                  (category_id, qty, est_revenue))


def get_lost_sales_summary(month: str = "") -> dict:
    if not month: month = datetime.now().strftime("%Y-%m")
    with conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(est_revenue), 0) AS total "
            "FROM lost_sales WHERE strftime('%Y-%m', created_at)=?", (month,)).fetchone()
        by_cat = c.execute(
            "SELECT ls.category_id, pc.code, pc.name, COUNT(*) AS n, SUM(ls.qty) AS qty, "
            "COALESCE(SUM(ls.est_revenue),0) AS revenue FROM lost_sales ls "
            "LEFT JOIN price_categories pc ON ls.category_id=pc.id "
            "WHERE strftime('%Y-%m', ls.created_at)=? GROUP BY ls.category_id ORDER BY revenue DESC", (month,)).fetchall()
    return {"month": month, "count": row["n"], "total_est_revenue": round(float(row["total"] or 0), 2),
            "by_category": [dict(r) for r in by_cat]}


# ─── Break-Even ────────────────────────────────────────────────────────────

def get_break_even() -> dict:
    from .profit import get_margins
    margins = get_margins()
    margin_pct = margins["actual_overall_margin"] / 100 if margins["actual_overall_margin"] else 0
    with conn() as c:
        fixed_costs = c.execute(
            "SELECT COALESCE(SUM(budget_monthly), 0) AS v FROM expense_categories WHERE is_fixed=1 AND active=1").fetchone()["v"]
    break_even_sales = float(fixed_costs or 0) / margin_pct if margin_pct > 0 else 0
    today = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        today_sales = c.execute(
            f"SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE date(created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}", (today,)).fetchone()["v"]
        closed_count = c.execute(
            "SELECT COUNT(*) AS n FROM closed_days WHERE strftime('%Y-%m', date)=?", (datetime.now().strftime("%Y-%m"),)).fetchone()["n"]
    elapsed_days = max(1, datetime.now().day - closed_count)
    return {"fixed_monthly_costs": round(float(fixed_costs or 0), 2), "actual_margin_pct": margins["actual_overall_margin"],
            "break_even_monthly_sales": round(break_even_sales, 2),
            "daily_target": round(break_even_sales / 30 if break_even_sales > 0 else 0, 2),
            "daily_so_far": round(float(today_sales or 0), 2), "elapsed_days": elapsed_days}


# ─── Margin-Protection Alerts ──────────────────────────────────────────────

def get_margin_alerts() -> list:
    target = float(get_setting("margin_protection_target", "20") or "20")
    from .profit import get_category_stock_state
    with conn() as c:
        cats = c.execute(
            "SELECT pc.*, COALESCE(css.current_avg_cost, 0) AS avg_cost FROM price_categories pc "
            "LEFT JOIN category_stock_state css ON css.category_id=pc.id WHERE pc.active=1 ORDER BY pc.sort_order").fetchall()
    alerts = []
    for cat in cats:
        sell = float(cat["sell_price"] or 0); cost = float(cat["avg_cost"] or 0)
        if sell <= 0: continue
        margin_pct = ((sell - cost) / sell) * 100
        if margin_pct < target:
            suggested = round(cost / (1 - target / 100), 2) if cost > 0 else sell
            alerts.append({"category_id": cat["id"], "code": cat["code"] or "", "name": cat["name"],
                           "sell_price": sell, "avg_cost": cost, "margin_pct": round(margin_pct, 2),
                           "target_pct": target, "suggested_price": suggested})
    return alerts


# ─── 30-Day Cash-Flow Forecast ─────────────────────────────────────────────

def get_cash_flow_forecast() -> dict:
    today = datetime.now(); thirty_days_ago = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    with conn() as c:
        cash_inflow = c.execute(
            f"SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE payment_method='cash' AND {db.VALID_SALE_FILTER_NO_ALIAS} AND date(created_at)>=?", (thirty_days_ago,)).fetchone()["v"]
        avg_customer_payments = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM customer_payments WHERE date(created_at)>=?", (thirty_days_ago,)).fetchone()["v"]
        closed_count = c.execute("SELECT COUNT(*) AS n FROM closed_days WHERE date>=?", (thirty_days_ago,)).fetchone()["n"]
        active_days = max(1, 30 - closed_count)
        avg_daily_inflow = (float(cash_inflow or 0) + float(avg_customer_payments or 0)) / active_days
        recurring = c.execute("SELECT * FROM recurring_expenses WHERE active=1").fetchall()
        credit_bills = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) AS v FROM bills WHERE status='confirmed' AND deleted_at IS NULL AND payment_status='credit' AND credit_due_date IS NOT NULL AND date(credit_due_date)>=date('now','localtime') AND date(credit_due_date)<=date('now','localtime','+30 days')").fetchone()["v"]
        cogs_30d = c.execute(
            f"SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si JOIN sales s ON si.sale_id=s.id WHERE {db.VALID_SALE_FILTER} AND date(s.created_at)>=?", (thirty_days_ago,)).fetchone()["v"]
        avg_daily_cogs = float(cogs_30d or 0) / active_days
        cash = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer").fetchone()["v"]
    daily = []; running_balance = float(cash or 0); min_balance = running_balance; min_date = today.strftime("%Y-%m-%d")
    for i in range(30):
        d = today + timedelta(days=i); date_str = d.strftime("%Y-%m-%d")
        inflow = avg_daily_inflow; outflow = avg_daily_cogs
        for r in recurring:
            if int(r["day_of_month"]) == d.day: outflow += float(r["amount"] or 0)
        with conn() as c:
            # v8.18.15: deleted_at IS NULL — deleted bills have no future dues
            bills_due = c.execute("SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) AS v FROM bills WHERE status='confirmed' AND deleted_at IS NULL AND payment_status='credit' AND date(credit_due_date)=?", (date_str,)).fetchone()["v"]
            is_closed = c.execute("SELECT COUNT(*) n FROM closed_days WHERE date=?", (date_str,)).fetchone()["n"]
        outflow += float(bills_due or 0)
        if is_closed: inflow = 0
        running_balance += inflow - outflow
        daily.append({"date": date_str, "inflow": round(inflow, 2), "outflow": round(outflow, 2), "balance": round(running_balance, 2)})
        if running_balance < min_balance: min_balance = running_balance; min_date = date_str
    return {"current_cash": round(float(cash or 0), 2), "avg_daily_inflow": round(avg_daily_inflow, 2),
            "avg_daily_cogs": round(avg_daily_cogs, 2), "credit_bills_due_30d": round(float(credit_bills or 0), 2),
            "min_balance": round(min_balance, 2), "min_balance_date": min_date,
            "negative_alert": min_balance < 0, "daily": daily}
