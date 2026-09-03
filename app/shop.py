"""Inventory, Customers, Expenses, Cash Drawer, Employees, Shifts."""
import json
import re
import urllib.parse
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant
from .db import conn
from datetime import datetime


# ---------- Payment Methods ----------

def get_payment_methods() -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM payment_methods WHERE active=1 ORDER BY sort_order").fetchall()
    return [dict(r) for r in rows]


# ---------- Inventory (computed from bills - sales) ----------

def get_inventory() -> list:
    """Current stock per category. v8.5 HARDENED: reads ONLY from
    category_stock_state — the single source of truth. The legacy
    `purchased - sold + adjustments` computation has been removed to
    guarantee that the running weighted-average state never drifts from
    what is displayed to the operator. If a category has no state row
    (e.g. freshly created and never purchased), it is shown as empty.

    v8.7: ALSO returns `purchased`, `sold`, `adjustments` as informational
    aggregates (all-time totals per category). These are NOT used to compute
    `stock` — `stock` still comes from `category_stock_state`. The three new
    columns exist purely to give the operator visibility into "how much was
    purchased vs sold historically" (the `#/stock` page renders them).
    There may be a small drift between `purchased - sold + adjustments` and
    `stock` if the materialized state was rebuilt — that's expected; the
    materialized state is authoritative.

    v8.18.18: BAG categories apply the user's sold rule at display time:
    `purchased` shows the VIRTUAL total max(purchases + adjustments, sold)
    ("our purchased QTY is equal to SOLD"; never above it when a real bill
    is ahead — "don't increase"), while `stock` is the on-hand number
    max(purchases + adjustments − sold, 0) from the state — 0 when no bags
    bill was ever entered. Bag rows never raise low/out-of-stock alerts
    (their supply is auto-managed by the rule, not by restocking).
    """
    with conn() as c:
        # v8.7.1 fix: include ALL categories (active=0 too) so that stock_state
        # rows referencing inactive categories still show their real name/code.
        # The previous WHERE active=1 filter hid categories that were soft-deleted
        # but still had stock_state entries (e.g. from Ezi import).
        cats = c.execute(
            "SELECT pc.id, pc.name, pc.code, pc.sell_price, pc.color, pc.sort_order, pc.active "
            "FROM price_categories pc "
            "ORDER BY pc.sort_order, pc.code"
        ).fetchall()
        state_rows = c.execute(
            "SELECT category_id, current_qty, current_value, current_avg_cost "
            "FROM category_stock_state"
        ).fetchall()
        # v8.7: movement aggregates (all-time, for informational display)
        # Pattern matches profit_engine.peek_avg_cost + shop.get_daily_summary
        # (CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) —
        # bill_items stores qty+unit separately; conversion happens at read time.
        purchased_rows = c.execute(
            "SELECT bi.category_id, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.category_id IS NOT NULL "
            "GROUP BY bi.category_id"
        ).fetchall()
        sold_rows = c.execute(
            "SELECT si.category_id, SUM(si.qty) AS total "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND si.category_id IS NOT NULL "
            "GROUP BY si.category_id"
        ).fetchall()
        adj_rows = c.execute(
            "SELECT category_id, SUM(delta) AS total "
            "FROM stock_adjustments WHERE category_id IS NOT NULL "
            "GROUP BY category_id"
        ).fetchall()
        # v8.18.18: bag categories (user's sold rule — see the docstring)
        bag_ids = set()
        try:
            from .profit_engine import bag_category_ids as _inv_bag_ids
            bag_ids = _inv_bag_ids(c)
        except Exception:
            bag_ids = set()
    state_map = {r["category_id"]: r for r in state_rows}
    purchased_map = {r["category_id"]: float(r["total"] or 0) for r in purchased_rows}
    sold_map = {r["category_id"]: float(r["total"] or 0) for r in sold_rows}
    adj_map = {r["category_id"]: float(r["total"] or 0) for r in adj_rows}

    result = []
    # v8.7.2: iterate the UNION of price_categories + orphan stock_state
    # category_ids. Previously only price_categories were iterated, which hid
    # stock_state rows for category_ids that don't exist in price_categories
    # (e.g. categories that were hard-deleted, or Ezi import rows with
    # category_ids that were never inserted). Now those orphans appear as
    # "Category #N (missing)" with a red color so the user can see the data
    # integrity issue and fix it.
    seen_ids = set()
    for p in cats:
        cat_id = p["id"]
        seen_ids.add(cat_id)
        st = state_map.get(cat_id)
        if st is not None:
            stock = float(st["current_qty"] or 0)
            avg_cost = float(st["current_avg_cost"] or 0)
            stock_value = float(st["current_value"] or 0)
        else:
            # No state row yet — category exists but has never been purchased.
            # Display as empty rather than computing on-the-fly.
            stock = 0.0
            avg_cost = 0.0
            stock_value = 0.0

        # v8.7: informational movement aggregates
        purchased = purchased_map.get(cat_id, 0)
        sold = sold_map.get(cat_id, 0)
        adjustments = adj_map.get(cat_id, 0)

        sell_price = float(p["sell_price"] or 0)
        is_bag = cat_id in bag_ids
        if is_bag:
            # v8.18.18 user's rule, display side: purchased is raised to
            # SOLD when sold passes it ("our purchased QTY is equal to
            # SOLD"), and NOT increased when a real bill is ahead
            # ("purchased > sold → don't increase").
            purchased = max(purchased + adjustments, sold)
        low_flag = stock < 10
        out_flag = stock <= 0
        neg_flag = stock < 0
        if is_bag:
            # bags are auto-managed by the sold rule — never alert on them
            low_flag = out_flag = neg_flag = False
        result.append({
            "category_id": cat_id,
            "category_name": p["name"] or "Unknown",
            "code": p["code"] or "",
            "sell_price": sell_price,
            "color": p["color"] or "",
            "stock": int(stock) if stock == int(stock) else round(stock, 2),
            "avg_cost": round(avg_cost, 2),
            "stock_value": round(stock_value, 2),
            "potential_revenue": round(stock * sell_price, 2),
            "potential_profit": round(stock * (sell_price - avg_cost), 2),
            "low_stock": low_flag,
            "out_of_stock": out_flag,
            "negative_stock": neg_flag,
            # v8.7: informational movement columns (NOT used to compute `stock`)
            "purchased": int(purchased) if purchased == int(purchased) else round(purchased, 2),
            "sold": int(sold) if sold == int(sold) else round(sold, 2),
            "adjustments": int(adjustments) if adjustments == int(adjustments) else round(adjustments, 2),
            # v8.7.2: orphan flag (False here — this category exists)
            "missing_category": False,
            # v8.18.18: bag rule marker (frontend can style/annotate)
            "auto_managed_stock": is_bag,
        })

    # v8.7.2: append orphan stock_state rows (category_ids not in price_categories)
    for cid, st in state_map.items():
        if cid in seen_ids:
            continue  # already covered above
        stock = float(st["current_qty"] or 0)
        avg_cost = float(st["current_avg_cost"] or 0)
        stock_value = float(st["current_value"] or 0)
        purchased = purchased_map.get(cid, 0)
        sold = sold_map.get(cid, 0)
        adjustments = adj_map.get(cid, 0)
        result.append({
            "category_id": cid,
            "category_name": f"Category #{cid} (missing)",
            "code": f"#{cid}",
            "sell_price": 0.0,
            "color": "#ef4444",  # red — signals data integrity issue
            "stock": int(stock) if stock == int(stock) else round(stock, 2),
            "avg_cost": round(avg_cost, 2),
            "stock_value": round(stock_value, 2),
            "potential_revenue": 0.0,
            "potential_profit": 0.0,
            "low_stock": stock < 10,
            "out_of_stock": stock <= 0,
            "negative_stock": stock < 0,
            "purchased": int(purchased) if purchased == int(purchased) else round(purchased, 2),
            "sold": int(sold) if sold == int(sold) else round(sold, 2),
            "adjustments": int(adjustments) if adjustments == int(adjustments) else round(adjustments, 2),
            "missing_category": True,  # v8.7.2: flag for UI warning
        })
    return result


# ---------- Customers ----------

def get_or_create_customer(name: str, phone: str = "") -> int:
    """Find or create a customer by phone (or name if no phone)."""
    with conn() as c:
        if phone:
            row = c.execute("SELECT id FROM customers WHERE phone=? AND deleted_at IS NULL", (phone,)).fetchone()
            if row:
                return row["id"]
        if name:
            row = c.execute("SELECT id FROM customers WHERE lower(name)=lower(?) AND deleted_at IS NULL", (name,)).fetchone()
            if row:
                return row["id"]
        return c.execute(
            "INSERT INTO customers(name, phone) VALUES(?,?)",
            (name or "Walk-in", phone),
        ).lastrowid


def update_customer_stats(customer_id: int, amount: float, is_credit: bool):
    """Update customer's total_spent and loyalty points after a sale.

    v8.5: loyalty_points_per_rs is read from the `settings` table
    (default 100 = 1 point per Rs 100). The loyalty *redemption* rate
    is read separately by get_loyalty_rate(). The two settings are
    decoupled so an owner can grant 1 point per Rs 50 spend while still
    redeeming at Rs 1 per point.
    """
    per_rs = _get_loyalty_points_per_rs()
    points = int(amount / per_rs) if per_rs > 0 else 0
    with conn() as c:
        if is_credit:
            c.execute(
                "UPDATE customers SET total_credit = total_credit + ?, loyalty_points = loyalty_points + ? WHERE id=?",
                (amount, points, customer_id),
            )
        else:
            c.execute(
                "UPDATE customers SET total_spent = total_spent + ?, loyalty_points = loyalty_points + ? WHERE id=?",
                (amount, points, customer_id),
            )


def _get_loyalty_points_per_rs() -> float:
    """Read loyalty_points_per_rs from settings. Default 100 (= 1 pt per Rs 100)."""
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key='loyalty_points_per_rs'").fetchone()
    if not row or not row["value"]:
        return 100.0
    try:
        v = float(row["value"])
        return v if v > 0 else 100.0
    except (TypeError, ValueError):
        return 100.0


def get_customer(customer_id: int) -> dict:
    with conn() as c:
        cust = c.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not cust:
            return None
        sales = c.execute(
            "SELECT * FROM sales WHERE customer_name LIKE ? OR customer_phone LIKE ? ORDER BY id DESC LIMIT 20",
            (f"%{cust['name']}%", f"%{cust['phone'] or ''}%"),
        ).fetchall()
        payments = c.execute(
            "SELECT * FROM customer_payments WHERE customer_id=? ORDER BY id DESC LIMIT 10",
            (customer_id,),
        ).fetchall()
    return {
        **dict(cust),
        "loyalty_rate": get_loyalty_rate(),
        "loyalty_value": round((cust["loyalty_points"] or 0) * get_loyalty_rate(), 2),
        "recent_sales": [dict(s) for s in sales],
        "recent_payments": [dict(p) for p in payments],
    }


def search_customers(q: str) -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM customers WHERE deleted_at IS NULL AND (name LIKE ? OR phone LIKE ?) ORDER BY total_spent DESC LIMIT 20",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- Expenses ----------

def add_expense(category: str, amount: float, description: str = "", payment_method: str = "cash",
                category_id: int = None, expense_type: str = "operating",
                date_str: str = None, recurring_id: int = None) -> int:
    """Record an expense. Backward-compatible: legacy callers pass `category` as text.

    v4.0 Phase 2 additions:
      - category_id: link to expense_categories row (nullable for legacy rows)
      - expense_type: 'operating' (default) | 'owner_draw' (excluded from P&L operating expenses)
      - date_str: explicit date (YYYY-MM-DD); defaults to today
      - recurring_id: link to recurring_expenses row that generated this
    """
    if expense_type not in ("operating", "owner_draw"):
        expense_type = "operating"
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        eid = c.execute(
            "INSERT INTO expenses(category, description, amount, payment_method, date, "
            "category_id, expense_type, recurring_id) VALUES(?,?,?,?,?,?,?,?)",
            (category, description, amount, payment_method, date_str,
             category_id, expense_type, recurring_id),
        ).lastrowid
        # Log to cash drawer if cash
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('expense', ?, ?, ?, 'expense')",
                (-amount, f"Expense: {category}", eid),
            )
    return eid


def get_expenses(date: str = "", limit: int = 50, month: str = "",
                 category_id: int = None, expense_type: str = "",
                 page: int = 0, page_size: int = 0) -> dict | list:
    """List expenses with optional filters. v8.4: supports pagination.

    When page & page_size > 0, returns {expenses, total, page, page_size, pages_total}.
    Otherwise returns a plain list (backward compat).
    """
    use_pagination = page > 0 or page_size > 0
    if use_pagination:
        page = max(1, page)
        page_size = min(max(1, page_size or limit), 500)
        offset = (page - 1) * page_size
    else:
        page_size = min(max(1, limit), 500)
        offset = 0

    with conn() as c:
        sql = (
            "SELECT e.*, ec.name AS category_name, ec.budget_monthly "
            "FROM expenses e "
            "LEFT JOIN expense_categories ec ON e.category_id = ec.id "
            "WHERE 1=1"
        )
        args = []
        if date:
            sql += " AND date(e.date)=?"
            args.append(date)
        if month:
            sql += " AND strftime('%Y-%m', e.date)=?"
            args.append(month)
        if category_id is not None:
            sql += " AND e.category_id=?"
            args.append(category_id)
        if expense_type:
            sql += " AND e.expense_type=?"
            args.append(expense_type)

        # Count total matching rows
        count_sql = sql.replace("SELECT e.*, ec.name AS category_name, ec.budget_monthly ", "SELECT COUNT(*) AS n ", 1)
        total = c.execute(count_sql, args).fetchone()["n"]

        # v8.19.1: clamp the page (last-page deletion / filter shrink)
        if use_pagination:
            page = db.clamp_page(page, total, page_size)
            offset = (page - 1) * page_size

        sql += " ORDER BY e.id DESC LIMIT ? OFFSET ?"
        args += [page_size, offset]
        rows = c.execute(sql, args).fetchall()

    expenses_list = [dict(r) for r in rows]
    if use_pagination:
        pages_total = (total + page_size - 1) // page_size
        return {
            "expenses": expenses_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages_total": pages_total,
        }
    return expenses_list


def get_expense_summary(month: str = "") -> dict:
    """Monthly expense summary by category, with budget vs actual + MoM comparison."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # Compute last month for comparison
    try:
        y, m = month.split("-")
        y, m = int(y), int(m)
        last_month = f"{y}-{m-1:02d}" if m > 1 else f"{y-1}-12"
    except Exception:
        last_month = month

    with conn() as c:
        # Current month by category (operating only — owner_draw excluded from operating totals)
        rows = c.execute(
            "SELECT COALESCE(ec.name, e.category) AS category, "
            "COALESCE(e.category_id, 0) AS category_id, "
            "COUNT(*) AS count, SUM(e.amount) AS total, "
            "COALESCE(ec.budget_monthly, 0) AS budget_monthly "
            "FROM expenses e "
            "LEFT JOIN expense_categories ec ON e.category_id = ec.id "
            "WHERE strftime('%Y-%m', e.date)=? AND e.expense_type='operating' "
            "GROUP BY COALESCE(ec.name, e.category), COALESCE(e.category_id, 0) "
            "ORDER BY total DESC",
            (month,),
        ).fetchall()
        # Current month totals (operating + owner_draw separately)
        operating_total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'",
            (month,),
        ).fetchone()["v"]
        owner_draw_total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='owner_draw'",
            (month,),
        ).fetchone()["v"]
        # Last month operating total
        last_month_total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'",
            (last_month,),
        ).fetchone()["v"]
        # All categories (active) with their budget
        all_cats = c.execute(
            "SELECT id, name, is_fixed, budget_monthly, active, sort_order "
            "FROM expense_categories WHERE active=1 ORDER BY sort_order, name"
        ).fetchall()

    by_category = []
    for r in rows:
        budget = r["budget_monthly"] or 0
        total = r["total"] or 0
        pct = round(100 * total / budget, 1) if budget > 0 else 0
        by_category.append({
            "category": r["category"],
            "category_id": r["category_id"],
            "count": r["count"],
            "total": round(total, 2),
            "budget": round(budget, 2),
            "pct": pct,
        })

    # Add categories that have a budget but no spend this month (so the UI shows the 0/budget card)
    spent_cat_ids = {r["category_id"] for r in rows if r["category_id"]}
    for cat in all_cats:
        if cat["id"] in spent_cat_ids:
            continue
        if cat["budget_monthly"] and cat["budget_monthly"] > 0:
            by_category.append({
                "category": cat["name"],
                "category_id": cat["id"],
                "count": 0,
                "total": 0.0,
                "budget": round(cat["budget_monthly"], 2),
                "pct": 0.0,
            })

    delta_pct = 0.0
    if last_month_total > 0:
        delta_pct = round(100 * (operating_total - last_month_total) / last_month_total, 1)
    return {
        "month": month,
        "operating_total": round(operating_total, 2),
        "owner_draw_total": round(owner_draw_total, 2),
        "total": round(operating_total + owner_draw_total, 2),
        "last_month": last_month,
        "last_month_total": round(last_month_total, 2),
        "delta_pct": delta_pct,
        "by_category": by_category,
        "categories": [dict(c) for c in all_cats],
    }


# ---------- Recurring Expenses (v4.0 Phase 2) ----------

def generate_recurring_expenses(force_month: str = None) -> dict:
    """Generate expense rows for active recurring_expenses whose due day has passed
    in the current month and which haven't already been generated this month.

    Idempotent: each recurring_expense has a `last_generated` field storing the
    YYYY-MM of its last auto-generation. If last_generated == current month, skip.

    Returns {"generated": n, "skipped": n, "details": [...]}.
    """
    today = datetime.now()
    current_month = force_month or today.strftime("%Y-%m")
    current_day = today.day
    generated = 0
    skipped = 0
    details = []

    with conn() as c:
        rows = c.execute(
            "SELECT * FROM recurring_expenses WHERE active=1"
        ).fetchall()
        for r in rows:
            r = dict(r)
            # Idempotency: skip if already generated this month
            if r["last_generated"] == current_month:
                skipped += 1
                details.append({"id": r["id"], "action": "skip", "reason": "already generated"})
                continue
            # Only generate if today's day >= day_of_month (so the bill is "due")
            try:
                due_day = int(r["day_of_month"] or 1)
            except (TypeError, ValueError):
                due_day = 1
            due_day = max(1, min(31, due_day))
            if force_month is None and current_day < due_day:
                skipped += 1
                details.append({"id": r["id"], "action": "skip", "reason": f"not due until day {due_day}"})
                continue
            # Look up the category name
            cat_row = c.execute(
                "SELECT name FROM expense_categories WHERE id=?",
                (r["category_id"],),
            ).fetchone() if r["category_id"] else None
            cat_name = cat_row["name"] if cat_row else "Recurring"
            # Determine date for the expense — use the due_day of current month
            try:
                expense_date = f"{current_month}-{min(due_day, 28):02d}"
            except Exception:
                expense_date = today.strftime("%Y-%m-%d")
            # Insert the expense
            eid = c.execute(
                "INSERT INTO expenses(category, description, amount, payment_method, date, "
                "category_id, expense_type, recurring_id) VALUES(?,?,?,?,?,?,?,?)",
                (cat_name, r["description"] or cat_name, r["amount"], r["payment_method"] or "cash",
                 expense_date, r["category_id"], "operating", r["id"]),
            ).lastrowid
            # Cash drawer entry
            if (r["payment_method"] or "cash") == "cash":
                c.execute(
                    "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                    "VALUES('expense', ?, ?, ?, 'expense')",
                    (-r["amount"], f"Recurring: {cat_name}", eid),
                )
            # Mark as generated
            c.execute(
                "UPDATE recurring_expenses SET last_generated=? WHERE id=?",
                (current_month, r["id"]),
            )
            generated += 1
            details.append({
                "id": r["id"],
                "action": "generate",
                "expense_id": eid,
                "amount": r["amount"],
                "category": cat_name,
            })
    return {"generated": generated, "skipped": skipped, "details": details}


# ---------- Expense Categories CRUD (v4.0 Phase 2) ----------

def list_expense_categories(active_only: bool = False) -> list:
    with conn() as c:
        sql = "SELECT * FROM expense_categories"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY sort_order, name"
        rows = c.execute(sql).fetchall()
    return [dict(r) for r in rows]


def add_expense_category(name: str, is_fixed: bool = False, budget_monthly: float = 0,
                         sort_order: int = 0) -> int:
    with conn() as c:
        try:
            cid = c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,1,?)",
                (name, 1 if is_fixed else 0, budget_monthly, sort_order),
            ).lastrowid
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError(f"Expense category '{name}' already exists")
            raise
    return cid


def update_expense_category(cid: int, name: str = None, is_fixed: bool = None,
                             budget_monthly: float = None, active: bool = None,
                             sort_order: int = None):
    fields, vals = [], []
    if name is not None: fields.append("name=?"); vals.append(name)
    if is_fixed is not None: fields.append("is_fixed=?"); vals.append(1 if is_fixed else 0)
    if budget_monthly is not None: fields.append("budget_monthly=?"); vals.append(budget_monthly)
    if active is not None: fields.append("active=?"); vals.append(1 if active else 0)
    if sort_order is not None: fields.append("sort_order=?"); vals.append(sort_order)
    if not fields:
        return False
    vals.append(cid)
    with conn() as c:
        cur = c.execute(f"UPDATE expense_categories SET {', '.join(fields)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_expense_category(cid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM expense_categories WHERE id=?", (cid,))
        return cur.rowcount > 0


# ---------- Recurring Expenses CRUD (v4.0 Phase 2) ----------

def list_recurring_expenses(active_only: bool = False) -> list:
    with conn() as c:
        sql = (
            "SELECT re.*, ec.name AS category_name, ec.budget_monthly "
            "FROM recurring_expenses re "
            "LEFT JOIN expense_categories ec ON re.category_id = ec.id"
        )
        if active_only:
            sql += " WHERE re.active=1"
        sql += " ORDER BY re.id"
        rows = c.execute(sql).fetchall()
    return [dict(r) for r in rows]


def add_recurring_expense(category_id: int, amount: float, description: str = "",
                          payment_method: str = "cash", day_of_month: int = 1,
                          active: bool = True) -> int:
    day_of_month = max(1, min(31, int(day_of_month)))
    with conn() as c:
        rid = c.execute(
            "INSERT INTO recurring_expenses(category_id, description, amount, payment_method, "
            "day_of_month, active) VALUES(?,?,?,?,?,?)",
            (category_id, description, amount, payment_method, day_of_month, 1 if active else 0),
        ).lastrowid
    return rid


def update_recurring_expense(rid: int, category_id: int = None, amount: float = None,
                              description: str = None, payment_method: str = None,
                              day_of_month: int = None, active: bool = None):
    fields, vals = [], []
    if category_id is not None: fields.append("category_id=?"); vals.append(category_id)
    if amount is not None: fields.append("amount=?"); vals.append(amount)
    if description is not None: fields.append("description=?"); vals.append(description)
    if payment_method is not None: fields.append("payment_method=?"); vals.append(payment_method)
    if day_of_month is not None:
        day_of_month = max(1, min(31, int(day_of_month)))
        fields.append("day_of_month=?"); vals.append(day_of_month)
    if active is not None: fields.append("active=?"); vals.append(1 if active else 0)
    if not fields:
        return False
    vals.append(rid)
    with conn() as c:
        cur = c.execute(f"UPDATE recurring_expenses SET {', '.join(fields)} WHERE id=?", vals)
        return cur.rowcount > 0


def delete_recurring_expense(rid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM recurring_expenses WHERE id=?", (rid,))
        return cur.rowcount > 0


# ---------- v4.0 Phase 5: Wholesale Money Flows ----------

# ─── Supplier Advances (peshgi) ────────────────────────────────

def add_supplier_advance(supplier_id: int, amount: float, payment_method: str = "cash",
                          notes: str = "") -> int:
    """Record a pre-payment (peshgi) to a supplier. Logs to cash_drawer if cash."""
    with conn() as c:
        aid = c.execute(
            "INSERT INTO supplier_advances(supplier_id, amount, payment_method, notes) "
            "VALUES(?,?,?,?)",
            (supplier_id, amount, payment_method, notes),
        ).lastrowid
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('supplier_advance', ?, ?, ?, 'supplier_advance')",
                (-amount, f"Advance to supplier #{supplier_id}", aid),
            )
    return aid


def list_supplier_advances(supplier_id: int = None, limit: int = 100) -> list:
    with conn() as c:
        if supplier_id:
            rows = c.execute(
                "SELECT sa.*, s.name AS supplier_name "
                "FROM supplier_advances sa LEFT JOIN suppliers s ON sa.supplier_id=s.id "
                "WHERE sa.supplier_id=? ORDER BY sa.id DESC LIMIT ?",
                (supplier_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT sa.*, s.name AS supplier_name "
                "FROM supplier_advances sa LEFT JOIN suppliers s ON sa.supplier_id=s.id "
                "ORDER BY sa.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_supplier_advance_balance(supplier_id: int) -> float:
    """Outstanding advance = sum of advances - sum applied to bills."""
    with conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM supplier_advances WHERE supplier_id=?",
            (supplier_id,),
        ).fetchone()
    return round(float(row["v"] or 0), 2)


def apply_supplier_advance_to_bill(advance_id: int, bill_id: int) -> bool:
    """Mark an advance as applied to a specific bill."""
    with conn() as c:
        cur = c.execute(
            "UPDATE supplier_advances SET applied_to_bill_id=? WHERE id=? AND applied_to_bill_id IS NULL",
            (bill_id, advance_id),
        )
        return cur.rowcount > 0


# ─── Agreed Rate List ──────────────────────────────────────────

def list_supplier_rates(supplier_id: int = None) -> list:
    with conn() as c:
        if supplier_id:
            rows = c.execute(
                "SELECT sr.*, s.name AS supplier_name "
                "FROM supplier_rates sr LEFT JOIN suppliers s ON sr.supplier_id=s.id "
                "WHERE sr.supplier_id=? ORDER BY sr.id",
                (supplier_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT sr.*, s.name AS supplier_name "
                "FROM supplier_rates sr LEFT JOIN suppliers s ON sr.supplier_id=s.id "
                "ORDER BY sr.id"
            ).fetchall()
    return [dict(r) for r in rows]


def set_supplier_rate(supplier_id: int, item_name: str, agreed_price: float) -> int:
    """Insert or update an agreed rate for a (supplier, item_name) pair."""
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM supplier_rates WHERE supplier_id=? AND lower(item_name)=lower(?)",
            (supplier_id, item_name),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE supplier_rates SET agreed_price=?, updated_at=datetime('now','localtime') WHERE id=?",
                (agreed_price, existing["id"]),
            )
            return existing["id"]
        rid = c.execute(
            "INSERT INTO supplier_rates(supplier_id, item_name, agreed_price) VALUES(?,?,?)",
            (supplier_id, item_name, agreed_price),
        ).lastrowid
    return rid


def delete_supplier_rate(rate_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM supplier_rates WHERE id=?", (rate_id,))
        return cur.rowcount > 0


def check_bill_items_against_rates(items: list, supplier_id: int,
                                     *, c=None) -> list:
    """For each item in a bill being confirmed, check if its price exceeds the
    supplier's agreed rate for that item name. Returns a list of flag strings.

    Phase 0 PR 5: optional keyword-only `c` (SQLite connection). If provided,
    uses that connection (so confirm_bill() can call this inside its write_tx()
    without opening a second connection that would deadlock).
    """
    if not supplier_id or not items:
        return []
    # Read rates inline if c is provided (avoid list_supplier_rates' own conn)
    if c is not None:
        rows = c.execute(
            "SELECT item_name, agreed_price FROM supplier_rates WHERE supplier_id=?",
            (supplier_id,),
        ).fetchall()
    else:
        rows = list_supplier_rates(supplier_id)
    rates = {(r["item_name"] or "").strip().lower(): r["agreed_price"]
             for r in rows}
    flags = []
    for it in items:
        raw = (it.get("raw") or "").strip().lower()
        price = float(it.get("price") or 0)
        if raw and raw in rates and price > 0:
            agreed = rates[raw]
            if price > agreed:
                pct_over = round(100 * (price - agreed) / agreed, 1) if agreed > 0 else 0
                flags.append(
                    f"⚠ '{it.get('raw', '')[:40]}' price Rs {price:.0f} exceeds agreed rate "
                    f"Rs {agreed:.0f} by {pct_over}%"
                )
    return flags


# ─── Bank Ledger ───────────────────────────────────────────────

def list_bank_accounts(active_only: bool = False) -> list:
    with conn() as c:
        sql = "SELECT * FROM bank_accounts"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY id"
        rows = c.execute(sql).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["current_balance"] = get_bank_account_balance(r["id"])
        out.append(d)
    return out


def add_bank_account(name: str, opening_balance: float = 0) -> int:
    with conn() as c:
        aid = c.execute(
            "INSERT INTO bank_accounts(name, opening_balance) VALUES(?,?)",
            (name, opening_balance),
        ).lastrowid
    return aid


def get_bank_account_balance(account_id: int) -> float:
    """Current balance = opening_balance + sum(deposits) - sum(withdrawals)."""
    with conn() as c:
        row = c.execute(
            "SELECT opening_balance + COALESCE(SUM(amount), 0) AS v "
            "FROM bank_accounts ba LEFT JOIN bank_transactions bt ON bt.account_id=ba.id "
            "WHERE ba.id=? GROUP BY ba.id",
            (account_id,),
        ).fetchone()
    return round(float(row["v"] or 0), 2) if row else 0.0


def add_bank_transaction(account_id: int, type_: str, amount: float,
                          description: str = "", reference: str = "",
                          date: str = None) -> int:
    """Record a bank transaction. type='deposit' → positive; 'withdrawal'/'supplier_payment' → negative."""
    if type_ not in ("deposit", "withdrawal", "supplier_payment"):
        raise ValueError(f"Invalid bank tx type: {type_}")
    if type_ == "deposit":
        signed = abs(amount)
    else:
        signed = -abs(amount)
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        tid = c.execute(
            "INSERT INTO bank_transactions(account_id, type, amount, description, reference, date) "
            "VALUES(?,?,?,?,?,?)",
            (account_id, type_, signed, description, reference, date),
        ).lastrowid
    return tid


def list_bank_transactions(account_id: int = None, limit: int = 100) -> list:
    with conn() as c:
        if account_id:
            rows = c.execute(
                "SELECT bt.*, ba.name AS account_name FROM bank_transactions bt "
                "LEFT JOIN bank_accounts ba ON bt.account_id=ba.id "
                "WHERE bt.account_id=? ORDER BY bt.id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT bt.*, ba.name AS account_name FROM bank_transactions bt "
                "LEFT JOIN bank_accounts ba ON bt.account_id=ba.id "
                "ORDER BY bt.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def record_cash_to_bank_deposit(account_id: int, amount: float, description: str = "") -> dict:
    """Deposit cash from drawer into bank. Creates:
       - bank_transactions row (type='deposit', +amount)
       - cash_drawer row (type='bank_deposit', -amount) to remove from drawer
    Returns {bank_tx_id, cash_drawer_id}.
    """
    if amount <= 0:
        raise ValueError("Deposit amount must be positive")
    with conn() as c:
        btx_id = c.execute(
            "INSERT INTO bank_transactions(account_id, type, amount, description, reference, date) "
            "VALUES(?,?,?,?,'cash_deposit',datetime('now','localtime'))",
            (account_id, "deposit", amount, description or "Cash deposit to bank"),
        ).lastrowid
        cd_id = c.execute(
            "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
            "VALUES('bank_deposit', ?, ?, ?, 'bank_transaction')",
            (-amount, f"Bank deposit to account #{account_id}", btx_id),
        ).lastrowid
    return {"bank_tx_id": btx_id, "cash_drawer_id": cd_id}


# ---------- v4.0 Phase 6: Daily Summary, Commissions, Scorecard ----------

def get_daily_summary(date: str = None) -> dict:
    """Today's key numbers for the owner daily summary message.

    Returns: sales_total, cash_sales, credit_sales, top_categories (top 3 by revenue),
    low_stock_count, overdue_urdhaar_total, shift_variances (list of {employee, variance}).
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        # Sales totals for the day
        sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS v FROM sales "
            "WHERE date(created_at)=? AND payment_status IN ('paid', 'credit', 'partial')",
            (date,),
        ).fetchone()
        cash_sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS v FROM sales "
            "WHERE date(created_at)=? AND payment_status IN ('paid', 'credit', 'partial') AND payment_method='cash'",
            (date,),
        ).fetchone()
        credit_sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS v FROM sales "
            "WHERE date(created_at)=? AND payment_status IN ('paid', 'credit', 'partial') AND payment_method='credit'",
            (date,),
        ).fetchone()
        sale_count = c.execute(
            "SELECT COUNT(*) n FROM sales WHERE date(created_at)=? AND payment_status IN ('paid', 'credit', 'partial')",
            (date,),
        ).fetchone()["n"]
        # Top categories by revenue today
        top_cats = c.execute(
            "SELECT si.category_code, SUM(si.sell_price * si.qty) AS revenue, SUM(si.qty) AS qty "
            "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            "WHERE date(s.created_at)=? AND s.payment_status IN ('paid', 'credit', 'partial') "
            "GROUP BY si.category_code ORDER BY revenue DESC LIMIT 3",
            (date,),
        ).fetchall()
        # Low-stock count (categories with stock < 10)
        inv = get_inventory()
        low_stock_count = sum(1 for i in inv if i.get("low_stock"))
        # Overdue urdhaar total
        overdue = c.execute(
            "SELECT COALESCE(SUM(total_credit), 0) AS v FROM customers WHERE total_credit > 0 AND deleted_at IS NULL"
        ).fetchone()["v"]
        # Today's closed-shift variances
        variances = c.execute(
            "SELECT s.id, s.employee_id, e.name AS employee_name, s.variance, s.blind_close "
            "FROM shifts s LEFT JOIN employees e ON s.employee_id=e.id "
            "WHERE s.status='closed' AND date(s.end_time)=? AND s.variance IS NOT NULL "
            "ORDER BY s.id DESC",
            (date,),
        ).fetchall()
        # v8.18.13: extra (non-stock) sales today
        extra = c.execute(
            "SELECT COALESCE(SUM(total), 0) v, COUNT(*) n FROM extra_sales "
            "WHERE date(sale_date)=?",
            (date,),
        ).fetchone()
    return {
        "date": date,
        "sales_total": round(float(sales["v"] or 0), 2),
        "cash_sales": round(float(cash_sales["v"] or 0), 2),
        "credit_sales": round(float(credit_sales["v"] or 0), 2),
        "sale_count": sale_count,
        "extra_sales_total": round(float(extra["v"] or 0), 2),
        "extra_sales_count": int(extra["n"] or 0),
        "top_categories": [
            {"code": r["category_code"], "revenue": round(float(r["revenue"] or 0), 2),
             "qty": int(r["qty"] or 0)}
            for r in top_cats
        ],
        "low_stock_count": low_stock_count,
        "overdue_urdhaar_total": round(float(overdue or 0), 2),
        "shift_variances": [
            {"shift_id": r["id"], "employee_id": r["employee_id"],
             "employee_name": r["employee_name"], "variance": round(float(r["variance"] or 0), 2),
             "blind": bool(r["blind_close"])}
            for r in variances
        ],
    }


def build_daily_summary_text(date: str = None) -> str:
    """Build a WhatsApp-friendly plain-text daily summary."""
    s = get_daily_summary(date)
    lines = [f"📊 *BillBook Daily Summary — {s['date']}*", ""]
    lines.append(f"💰 Total Sales: Rs {s['sales_total']:,.0f} ({s['sale_count']} sales)")
    lines.append(f"   Cash: Rs {s['cash_sales']:,.0f}")
    if s["credit_sales"] > 0:
        lines.append(f"   Credit: Rs {s['credit_sales']:,.0f}")
    # v8.18.13: extra (non-stock) sales — raddi, cartons etc.
    if s.get("extra_sales_total", 0) > 0:
        lines.append(
            f"♻️ Extra Sales (non-stock): Rs {s['extra_sales_total']:,.0f} "
            f"({s.get('extra_sales_count', 0)} entries)"
        )
    if s["top_categories"]:
        lines.append("")
        lines.append("📈 Top Categories:")
        for cat in s["top_categories"]:
            lines.append(f"   {cat['code']}: Rs {cat['revenue']:,.0f} ({cat['qty']} units)")
    if s["low_stock_count"] > 0:
        lines.append("")
        lines.append(f"⚠️ Low-stock categories: {s['low_stock_count']}")
    if s["overdue_urdhaar_total"] > 0:
        lines.append("")
        lines.append(f"💳 Outstanding urdhaar: Rs {s['overdue_urdhaar_total']:,.0f}")
    if s["shift_variances"]:
        lines.append("")
        lines.append("🔒 Shift Variances:")
        for v in s["shift_variances"]:
            name = v["employee_name"] or f"Employee #{v['employee_id']}"
            blind_tag = " (blind)" if v["blind"] else ""
            lines.append(f"   {name}{blind_tag}: Rs {v['variance']:+,.0f}")
    return "\n".join(lines)


def build_whatsapp_summary_link(owner_phone: str, date: str = None) -> str:
    """Build a wa.me link with the daily summary pre-filled as the message."""
    if not owner_phone:
        return ""
    # Sanitise phone: strip non-digits, ensure leading country code
    cleaned = re.sub(r"\D", "", owner_phone)
    if not cleaned.startswith("92") and cleaned.startswith("0"):
        cleaned = "92" + cleaned[1:]
    text = build_daily_summary_text(date)
    # wa.me expects URL-encoded message
    encoded = urllib.parse.quote(text)
    return f"https://wa.me/{cleaned}?text={encoded}"


# ─── Commissions ───────────────────────────────────────────────

def add_commission_rule(employee_id: int = None, role: str = "cashier",
                         type_: str = "percent", value: float = 0) -> int:
    """Create a commission rule. If employee_id is NULL, applies to all employees with the role."""
    if type_ not in ("percent", "flat"):
        raise ValueError("type must be 'percent' or 'flat'")
    if value < 0:
        raise ValueError("value must be non-negative")
    with conn() as c:
        rid = c.execute(
            "INSERT INTO commission_rules(employee_id, role, type, value, active) "
            "VALUES(?,?,?,?,1)",
            (employee_id, role, type_, value),
        ).lastrowid
    return rid


def list_commission_rules(active_only: bool = True) -> list:
    with conn() as c:
        sql = "SELECT cr.*, e.name AS employee_name FROM commission_rules cr " \
              "LEFT JOIN employees e ON cr.employee_id=e.id"
        if active_only:
            sql += " WHERE cr.active=1"
        sql += " ORDER BY cr.id DESC"
        rows = c.execute(sql).fetchall()
    return [dict(r) for r in rows]


def compute_commission_for_sale(sale_id: int, sale_total: float,
                                 employee_id: int = None, *, c=None) -> tuple:
    """Compute the commission for a sale. Returns (amount, rule_id) — rule_id
    is None when no rule applied.

    Phase 0 PR 3: now accepts an optional keyword-only `c` (SQLite connection).
    If provided, uses that connection and does NOT commit (caller controls the
    transaction). If `c` is None, opens its own connection (backward compatible).

    Lookup order:
      1. Rule with matching employee_id
      2. Rule with employee_id=NULL and matching role
      3. Otherwise 0
    """
    if not employee_id:
        return (0.0, None)
    # Compute is read-only — single function body works for both paths.
    use_c = c if c is not None else conn()
    own = c is None
    try:
        emp = use_c.execute("SELECT role FROM employees WHERE id=?", (employee_id,)).fetchone()
        role = emp["role"] if emp else "cashier"
        rule = use_c.execute(
            "SELECT * FROM commission_rules WHERE active=1 AND employee_id=? "
            "ORDER BY id DESC LIMIT 1",
            (employee_id,),
        ).fetchone()
        if not rule:
            rule = use_c.execute(
                "SELECT * FROM commission_rules WHERE active=1 AND employee_id IS NULL AND role=? "
                "ORDER BY id DESC LIMIT 1",
                (role,),
            ).fetchone()
        if not rule:
            return (0.0, None)
        if rule["type"] == "percent":
            return (round(sale_total * rule["value"] / 100, 2), rule["id"])
        else:  # flat
            return (round(float(rule["value"]), 2), rule["id"])
    finally:
        if own:
            use_c.close()


def record_commission(sale_id: int, employee_id: int, amount: float,
                      rule_id: int = None, *, c=None) -> int:
    """Insert a commission row for a sale.

    Phase 0 PR 3: optional `c` keyword-only parameter for transaction sharing.
    """
    if amount <= 0:
        return 0
    payload = (sale_id, employee_id, amount, rule_id)
    if c is not None:
        return c.execute(
            "INSERT INTO commissions(sale_id, employee_id, amount, rule_id) VALUES(?,?,?,?)",
            payload,
        ).lastrowid
    with conn() as own_c:
        return own_c.execute(
            "INSERT INTO commissions(sale_id, employee_id, amount, rule_id) VALUES(?,?,?,?)",
            payload,
        ).lastrowid


def get_commissions_summary(month: str = "") -> dict:
    """Per-employee commission totals for a month."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with conn() as c:
        rows = c.execute(
            "SELECT cm.employee_id, e.name AS employee_name, "
            "COUNT(cm.id) AS sale_count, SUM(cm.amount) AS total_commission "
            "FROM commissions cm "
            "LEFT JOIN employees e ON cm.employee_id=e.id "
            "JOIN sales s ON cm.sale_id=s.id "
            "WHERE strftime('%Y-%m', s.created_at)=? "
            "GROUP BY cm.employee_id ORDER BY total_commission DESC",
            (month,),
        ).fetchall()
    return {
        "month": month,
        "by_employee": [
            {"employee_id": r["employee_id"], "employee_name": r["employee_name"],
             "sale_count": r["sale_count"], "total_commission": round(float(r["total_commission"] or 0), 2)}
            for r in rows
        ],
    }


# ─── Cashier Scorecard ────────────────────────────────────────

def get_employee_scorecard(employee_id: int, month: str = "") -> dict:
    """Per-employee scorecard: revenue, avg transaction, discount rate, refund count, variance history."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    with conn() as c:
        emp = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not emp:
            return {"error": "Employee not found"}
        # Sales attributed to this employee this month
        sales = c.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS revenue, "
            "COALESCE(SUM(discount), 0) AS discounts "
            "FROM sales WHERE employee_id=? AND strftime('%Y-%m', created_at)=? "
            "AND payment_status IN ('paid', 'credit', 'partial')",
            (employee_id, month),
        ).fetchone()
        # Refund count for sales by this employee
        refunds = c.execute(
            "SELECT COUNT(*) AS n FROM sales "
            "WHERE employee_id=? AND strftime('%Y-%m', created_at)=? AND payment_status='refunded'",
            (employee_id, month),
        ).fetchone()["n"]
        # Commission total this month
        commission_total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM commissions "
            "WHERE employee_id=? AND strftime('%Y-%m', created_at)=?",
            (employee_id, month),
        ).fetchone()["v"]
        # Variance history
        variance_history = c.execute(
            "SELECT id, start_time, end_time, opening_cash, counted_cash, variance, blind_close "
            "FROM shifts WHERE employee_id=? AND status='closed' AND variance IS NOT NULL "
            "ORDER BY id DESC LIMIT 10",
            (employee_id,),
        ).fetchall()
    sale_count = sales["n"] or 0
    revenue = float(sales["revenue"] or 0)
    discounts = float(sales["discounts"] or 0)
    avg_txn = revenue / sale_count if sale_count > 0 else 0
    discount_rate = (discounts / revenue * 100) if revenue > 0 else 0
    return {
        "employee_id": employee_id,
        "employee_name": emp["name"],
        "role": emp["role"],
        "month": month,
        "sale_count": sale_count,
        "revenue": round(revenue, 2),
        "avg_transaction": round(avg_txn, 2),
        "discount_rate": round(discount_rate, 2),
        "refund_count": refunds,
        "commission_total": round(float(commission_total or 0), 2),
        "variance_history": [
            {"shift_id": r["id"], "start_time": r["start_time"], "end_time": r["end_time"],
             "opening_cash": float(r["opening_cash"] or 0),
             "counted_cash": float(r["counted_cash"] or 0) if r["counted_cash"] is not None else None,
             "variance": float(r["variance"] or 0), "blind": bool(r["blind_close"])}
            for r in variance_history
        ],
    }


# ---------- Cash Drawer ----------

def open_cash_drawer(opening_cash: float, employee_id: int = None) -> int:
    """Start a new cash drawer session (open shift)."""
    with conn() as c:
        drawer_id = c.execute(
            "INSERT INTO cash_drawer(type, amount, description) VALUES('opening', ?, 'Opening cash')",
            (opening_cash,),
        ).lastrowid
        if employee_id:
            c.execute(
                "INSERT INTO shifts(employee_id, opening_cash, status) VALUES(?,?, 'open')",
                (employee_id, opening_cash),
            )
    return drawer_id


def close_cash_drawer(closing_cash: float) -> dict:
    """Close the cash drawer and return reconciliation.

    v8.5: now includes `cash_in` (manual float injections) and `cash_out`
    (petty-cash withdrawals) types in the expected-cash calculation.

    Expected = opening
             + sum(sales settled in cash)        # type='sale', amount>0
             + sum(cash_in entries)              # type='cash_in'
             - sum(expenses paid in cash)        # type='expense'
             - sum(cash_out entries)             # type='cash_out'
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM cash_drawer WHERE date(created_at) = date('now','localtime') ORDER BY id"
        ).fetchall()
        c.execute(
            "INSERT INTO cash_drawer(type, amount, description) VALUES('closing', ?, 'Closing cash')",
            (closing_cash,),
        )

    opening = sum(r["amount"] for r in rows if r["type"] == "opening")
    cash_in_sales = sum(r["amount"] for r in rows
                        if r["type"] == "sale" and float(r["amount"] or 0) > 0)
    cash_in_manual = sum(r["amount"] for r in rows if r["type"] == "cash_in")
    cash_out_expense = sum(abs(r["amount"]) for r in rows if r["type"] == "expense")
    cash_out_manual = sum(abs(r["amount"]) for r in rows if r["type"] == "cash_out")

    expected = (opening + cash_in_sales + cash_in_manual
                - cash_out_expense - cash_out_manual)
    difference = closing_cash - expected

    return {
        "opening_cash": round(opening, 2),
        "cash_in": round(cash_in_sales + cash_in_manual, 2),
        "cash_in_sales": round(cash_in_sales, 2),
        "cash_in_manual": round(cash_in_manual, 2),
        "cash_out": round(cash_out_expense + cash_out_manual, 2),
        "cash_out_expense": round(cash_out_expense, 2),
        "cash_out_manual": round(cash_out_manual, 2),
        "expected_cash": round(expected, 2),
        "actual_cash": round(closing_cash, 2),
        "difference": round(difference, 2),
        "entries": len(rows),
    }


def get_cash_drawer_status() -> dict:
    """Get today's cash drawer status.

    v8.5: same fix as close_cash_drawer — `current_cash` now reflects
    cash_in/cash_out entries in addition to opening, sale, and expense.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM cash_drawer WHERE date(created_at) = date('now','localtime') ORDER BY id"
        ).fetchall()
    if not rows:
        return {"status": "closed", "opening_cash": 0, "current_cash": 0, "entries": 0}

    opening = sum(r["amount"] for r in rows if r["type"] == "opening")
    cash_in_sales = sum(r["amount"] for r in rows
                        if r["type"] == "sale" and float(r["amount"] or 0) > 0)
    cash_in_manual = sum(r["amount"] for r in rows if r["type"] == "cash_in")
    cash_out_expense = sum(abs(r["amount"]) for r in rows if r["type"] == "expense")
    cash_out_manual = sum(abs(r["amount"]) for r in rows if r["type"] == "cash_out")
    current = opening + cash_in_sales + cash_in_manual - cash_out_expense - cash_out_manual
    has_closing = any(r["type"] == "closing" for r in rows)

    return {
        "status": "closed" if has_closing else "open",
        "opening_cash": round(opening, 2),
        "current_cash": round(current, 2),
        "cash_in": round(cash_in_sales + cash_in_manual, 2),
        "cash_out": round(cash_out_expense + cash_out_manual, 2),
        "entries": len(rows),
        "history": [dict(r) for r in rows[-20:]],
    }


# ---------- Employees & Shifts ----------

def get_employees() -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM employees WHERE active=1 ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def add_employee(name: str, phone: str = "", role: str = "cashier") -> int:
    with conn() as c:
        return c.execute(
            "INSERT INTO employees(name, phone, role) VALUES(?,?,?)",
            (name, phone, role),
        ).lastrowid


def update_employee(employee_id: int, name: str = None, phone: str = None,
                    role: str = None, active: int = None,
                    monthly_salary: float = None) -> bool:
    """Update employee fields. Returns True if updated.

    v8.18.13: monthly_salary — the employee's fixed monthly salary. Changing
    it re-syncs their DRAFT salary records (paid ones keep the snapshot).
    """
    fields = []
    values = []
    if name is not None and name.strip():
        fields.append("name = ?")
        values.append(name.strip())
    if phone is not None:
        fields.append("phone = ?")
        values.append(phone.strip())
    if role is not None and role in ('cashier', 'manager', 'admin'):
        fields.append("role = ?")
        values.append(role)
    if active is not None:
        fields.append("active = ?")
        values.append(1 if active else 0)
    if monthly_salary is not None:
        salary = float(monthly_salary)
        if salary < 0:
            salary = 0.0
        fields.append("monthly_salary = ?")
        values.append(salary)
    if not fields:
        return False
    values.append(employee_id)
    with conn() as c:
        cur = c.execute(f"UPDATE employees SET {', '.join(fields)} WHERE id = ?", values)
        updated = cur.rowcount > 0
        # v8.18.13: keep draft salary records in sync with the new salary
        if updated and monthly_salary is not None:
            from . import salary as _salary_mod
            _draft_ids = [r["id"] for r in c.execute(
                "SELECT id FROM salary_records WHERE employee_id=? AND status='draft'",
                (employee_id,),
            ).fetchall()]
            for _rid in _draft_ids:
                c.execute(
                    "UPDATE salary_records SET monthly_salary=? WHERE id=?",
                    (float(monthly_salary), _rid),
                )
                _salary_mod._recompute_record(c, _rid)
        return updated


def delete_employee(employee_id: int) -> bool:
    """Soft-delete employee by marking inactive (preserves audit trail)."""
    with conn() as c:
        cur = c.execute("UPDATE employees SET active = 0 WHERE id = ?", (employee_id,))
        return cur.rowcount > 0


def set_employee_pin(employee_id: int, pin: str) -> bool:
    """Set PIN for employee login. PIN must be 4-8 digits.

    PR 7a: writes to BOTH pin_hash (bcrypt — the new secure path) AND pin
    (plaintext — for backward-compat with any code that still reads it).
    The pin column will be removed in a future release once all code paths
    use verify_manager_pin (which checks pin_hash first).
    """
    if not pin or not pin.isdigit() or not (4 <= len(pin) <= 8):
        return False
    from .security import hash_pin
    pin_hash = hash_pin(pin)
    with conn() as c:
        # PR 7a: write pin_hash (primary). Also keep pin in sync for any
        # legacy code that still reads the plaintext column.
        cur = c.execute(
            "UPDATE employees SET pin_hash = ?, pin = NULL WHERE id = ?",
            (pin_hash, employee_id),
        )
        return cur.rowcount > 0


def get_active_shift() -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def start_shift(employee_id: int, opening_cash: float) -> int:
    with conn() as c:
        sid = c.execute(
            "INSERT INTO shifts(employee_id, opening_cash, status) VALUES(?,?, 'open')",
            (employee_id, opening_cash),
        ).lastrowid
    open_cash_drawer(opening_cash, employee_id)
    return sid


def end_shift(closing_cash: float) -> dict:
    with conn() as c:
        row = c.execute("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"error": "No open shift"}
        c.execute(
            "UPDATE shifts SET status='closed', end_time=datetime('now','localtime'), closing_cash=? WHERE id=?",
            (closing_cash, row["id"]),
        )
    return close_cash_drawer(closing_cash)


# ---------- v4.0 Phase 4: Cash & Theft Controls ----------

# Pakistani rupee denominations (notes + coins bucket)
DENOMINATIONS = [5000, 1000, 500, 100, 50, 20, 10, 5, 2, 1]


def count_denominations(denom: dict) -> float:
    """Compute total cash from a {denom_value: count} dict.

    Accepts string keys (JSON-friendly) or int keys. Coins are a single bucket
    under 'coins' (treated as a single total).
    """
    total = 0.0
    for k, v in (denom or {}).items():
        try:
            if k == "coins":
                total += float(v)
            else:
                total += float(k) * int(v)
        except (ValueError, TypeError):
            continue
    return round(total, 2)


def get_expected_cash_for_shift(shift_id: int) -> float:
    """Compute expected cash for a shift = opening_cash + sum(cash_drawer entries during shift)."""
    with conn() as c:
        row = c.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
        if not row:
            return 0.0
        opening = float(row["opening_cash"] or 0)
        start = row["start_time"]
        # Cash drawer entries from shift start until now (or shift end)
        end_clause = "AND created_at <= ?" if row["end_time"] else ""
        args = [start]
        if row["end_time"]:
            args.append(row["end_time"])
        # Link by shift_id if present, else by time window
        cd_row = c.execute(
            f"SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer "
            f"WHERE created_at >= ? {end_clause}",
            args,
        ).fetchone()
        return round(opening + float(cd_row["v"] or 0), 2)


def end_shift_with_denominations(closing_cash: float = None, denominations: dict = None,
                                  blind: bool = False, manager_pin: str = None) -> dict:
    """End the active shift with denomination count + variance computation.

    - denominations: dict of {5000: n, 1000: n, ..., coins: total}
    - blind: if True, mark this as a blind close (cashier didn't see expected)
    - closing_cash: legacy field; if denominations is provided, computed total overrides this
    - Returns {ok, shift_id, counted_cash, expected_cash, variance, blind}

    Expected cash = SUM(cash_drawer.amount) for entries created during the shift window.
    (Opening cash is itself a cash_drawer entry of type='opening', so it's already in the SUM.)
    """
    # Compute counted cash from denominations, fallback to closing_cash
    counted_cash = count_denominations(denominations) if denominations else float(closing_cash or 0)
    with conn() as c:
        row = c.execute("SELECT * FROM shifts WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"error": "No open shift"}
        shift_id = row["id"]
        # Expected = sum of cash_drawer entries since shift start (includes opening)
        cd_row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer WHERE created_at >= ?",
            (row["start_time"],),
        ).fetchone()
        expected_cash = round(float(cd_row["v"] or 0), 2)
        variance = round(counted_cash - expected_cash, 2)
        c.execute(
            "UPDATE shifts SET status='closed', end_time=datetime('now','localtime'), "
            "closing_cash=?, counted_cash=?, variance=?, denominations=?, blind_close=? WHERE id=?",
            (counted_cash, counted_cash, variance,
             json.dumps(denominations) if denominations else None,
             1 if blind else 0, shift_id),
        )
    close_cash_drawer(counted_cash)
    # Log suspicious if variance is significant
    if abs(variance) > 100:
        log_suspicious(
            "shift_variance", "shift", shift_id,
            f"Shift #{shift_id} closed with variance Rs {variance:.2f} "
            f"(counted={counted_cash:.2f}, expected={expected_cash:.2f})",
            {"shift_id": shift_id, "counted": counted_cash,
             "expected": expected_cash, "variance": variance, "blind": blind},
            manager_pin=manager_pin,
        )
    return {
        "ok": True, "shift_id": shift_id,
        "counted_cash": counted_cash, "expected_cash": expected_cash,
        "variance": variance, "blind": blind,
    }


def log_suspicious(event_type: str, entity_type: str = None, entity_id: int = None,
                   description: str = "", metadata: dict = None,
                   manager_pin: str = None, employee_id: int = None):
    """Log a suspicious event to the activity_log with event_type='suspicious'.

    The original event_type is stored in metadata.original_event for filtering.
    """
    from .db import log_activity
    meta = dict(metadata or {})
    meta["original_event"] = event_type
    meta["manager_pin_provided"] = bool(manager_pin)
    if employee_id:
        meta["employee_id"] = employee_id
    log_activity(
        "suspicious", entity_type, entity_id,
        f"[{event_type}] {description}",
        meta,
    )


def verify_manager_pin(pin: str) -> dict:
    """Verify a manager/admin PIN.

    Returns the employee dict on success, or None on failure.

    PR 7a: now checks `employees.pin_hash` (bcrypt) FIRST. Falls back to the
    legacy plaintext `employees.pin` column (with a warning log) for backward
    compat during migration. Migration script: scripts/migrate_pin_hash.py.

    v8.5: also accepts the main login password as a fallback so the
    owner is never locked out of risky operations if no employee PINs
    are configured. Callers that just need a boolean should call
    `verify_manager_pin_bool()` instead.
    """
    if not pin:
        return None
    from .security import verify_pin, verify_password as _verify_password
    from .db import get_setting, log_activity
    import logging
    _log = logging.getLogger(__name__)

    with conn() as c:
        # PR 7a: try pin_hash FIRST (bcrypt — the new secure path)
        rows = c.execute(
            "SELECT * FROM employees WHERE role IN ('manager','admin') AND active=1"
        ).fetchall()
        for row in rows:
            pin_hash = row["pin_hash"] if "pin_hash" in row.keys() else None
            if pin_hash and verify_pin(pin, pin_hash):
                return dict(row)
        # Fallback: legacy plaintext pin (with warning) — ONLY for employees
        # that have NO pin_hash set (i.e., truly legacy, not yet migrated).
        # If an employee HAS pin_hash, we do NOT check their plaintext pin
        # (prevents a stale plaintext pin from bypassing the bcrypt hash).
        for row in rows:
            pin_hash = row["pin_hash"] if "pin_hash" in row.keys() else None
            if pin_hash:
                continue  # already has pin_hash — skip plaintext fallback
            legacy_pin = row["pin"] if "pin" in row.keys() else None
            if legacy_pin and legacy_pin == pin:
                _log.warning(
                    "Employee %s (id=%s) is using a plaintext PIN — should be "
                    "re-saved to migrate to pin_hash. Run scripts/migrate_pin_hash.py.",
                    row["name"], row["id"],
                )
                log_activity(
                    "plaintext_pin_used", "employee", row["id"],
                    f"Employee {row['name']} verified via legacy plaintext PIN "
                    f"(pin_hash not set) — re-save the PIN to migrate.",
                    {"employee_id": row["id"], "employee_name": row["name"]},
                )
                return dict(row)
    # Fallback: main login password (manager-of-last-resort)
    stored = get_setting("password_hash", "")
    if stored and _verify_password(pin, stored):
        return {"id": 0, "name": "owner", "role": "admin", "_via": "password"}
    return None


def verify_manager_pin_bool(pin: str) -> bool:
    """Boolean wrapper for verify_manager_pin — convenient for guards
    inside request handlers that don't need the employee record.
    """
    return verify_manager_pin(pin) is not None


def list_suspicious_events(limit: int = 100) -> list:
    """Return recent suspicious activity entries."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM activity_log WHERE event_type='suspicious' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_employee_variance_history(employee_id: int, limit: int = 20) -> list:
    """Return variance history for an employee's closed shifts."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, start_time, end_time, opening_cash, counted_cash, variance, blind_close "
            "FROM shifts WHERE employee_id=? AND status='closed' AND variance IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (employee_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_shifts(limit: int = 50) -> list:
    """Return recent shifts with employee name and computed variance."""
    with conn() as c:
        rows = c.execute(
            """
            SELECT s.*, e.name AS employee_name
            FROM shifts s
            LEFT JOIN employees e ON e.id = s.employee_id
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # Compute expected = opening + cash sales during shift window (best-effort)
        # Variance = closing - expected (only meaningful for closed shifts)
        if d.get("status") == "closed" and d.get("closing_cash") is not None:
            d["variance"] = round(float(d["closing_cash"]) - float(d["opening_cash"]), 2)
        else:
            d["variance"] = None
        out.append(d)
    return out


# ---------- COGS (Cost of Goods Sold) ----------

def get_category_avg_cost(category_id: int) -> float:
    """Weighted average per-piece cost for a category from confirmed, non-deleted bills.

    Weighted by pieces (qty converted via pieces() for dozen support).
    Returns 0.0 if the category has no cost history.

    Note: this function does NOT log a warning when there's no history — callers
    that need an audit trail (e.g. create_sale) should call log_activity themselves
    AFTER their write transaction commits, to avoid SQLite write-write deadlocks.

    bill_items.price is always stored as per-piece cost (validate.py normalises
    line-total inputs by dividing by pieces), so we only need to weight by pieces.
    """
    if category_id is None:
        return 0.0

    with conn() as c:
        row = c.execute(
            "SELECT "
            "SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_cost, "
            "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_pieces "
            "FROM bill_items bi "
            "JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.category_id = ? AND bi.price > 0 AND bi.qty > 0",
            (category_id,),
        ).fetchone()

    total_cost = row["total_cost"] if row and row["total_cost"] else 0
    total_pieces = row["total_pieces"] if row and row["total_pieces"] else 0

    if total_pieces <= 0:
        return 0.0
    return round(total_cost / total_pieces, 2)


def log_cogs_warning(category_id: int, sale_id: int = None, invoice_no: str = ""):
    """Log a cogs_warning activity entry. Call AFTER the sale's transaction commits.

    Separated from get_category_avg_cost so callers don't trigger writes from
    inside a read helper — that caused SQLite write-write deadlocks when the
    helper was invoked from within create_sale's INSERT transaction.
    """
    from .db import log_activity
    log_activity(
        "cogs_warning", "category", category_id,
        f"Sale {invoice_no or sale_id or '(unknown)'} recorded with cost_price=0 — "
        f"category_id={category_id} has no confirmed cost history",
        {"category_id": category_id, "sale_id": sale_id, "invoice_no": invoice_no},
    )


# ---------- P&L (Profit & Loss) ----------

def get_pnl(month: str = "") -> dict:
    """Monthly Profit & Loss statement."""
    if not month:
        month = datetime.now().strftime("%Y-%m")

    with conn() as c:
        # Revenue from sales
        sales = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS revenue, COALESCE(SUM(discount), 0) AS discounts "
            "FROM sales WHERE strftime('%Y-%m', created_at)=? AND payment_status IN ('paid', 'credit', 'partial')",
            (month,),
        ).fetchone()
        # Cost of goods sold
        cogs = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS cost "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE strftime('%Y-%m', s.created_at)=? AND s.payment_status IN ('paid', 'credit', 'partial')",
            (month,),
        ).fetchone()
        # Expenses — split operating vs owner_draw (v4.0 Phase 2)
        # Operating expenses reduce net_profit. Owner draws are shown separately
        # and do NOT reduce net_profit (they're equity reductions, not expenses).
        expenses = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'",
            (month,),
        ).fetchone()
        owner_draws = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE strftime('%Y-%m', date)=? AND expense_type='owner_draw'",
            (month,),
        ).fetchone()
        # Purchase cost (bills)
        purchases = c.execute(
            "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) AS total "
            "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
            "AND strftime('%Y-%m', bill_date)=?",
            (month,),
        ).fetchone()
        # v8.18.11: per-category breakdown for the P&L page — same shape as
        # actual-earnings' expenses_by_category. The page previously read
        # r.revenue / r.cogs / r.expenses_total / r.expenses[] — none of which
        # this endpoint returned (expenses is a NUMBER), so the whole statement
        # rendered zeros.
        exp_rows = c.execute(
            "SELECT COALESCE(ec.name, e.category) AS category, "
            "SUM(e.amount) AS total "
            "FROM expenses e "
            "LEFT JOIN expense_categories ec ON e.category_id = ec.id "
            "WHERE strftime('%Y-%m', e.date)=? AND e.expense_type='operating' "
            "GROUP BY COALESCE(ec.name, e.category) "
            "ORDER BY total DESC",
            (month,),
        ).fetchall()
        # v8.18.13: extra (non-stock) sales — other income, no COGS
        extra_income = _extra_sales_month_total(c, month)

    revenue = sales["revenue"] or 0
    discounts = sales["discounts"] or 0
    net_revenue = revenue  # already net of discount in sales.total
    cost = cogs["cost"] or 0
    gross_profit = net_revenue - cost
    expense_total = expenses["total"] or 0
    owner_draw_total = owner_draws["total"] or 0
    # v8.18.13: net profit = gross profit + other income - operating expenses
    net_profit = gross_profit + extra_income - expense_total

    return {
        "month": month,
        "gross_revenue": round(revenue + discounts, 2),
        "discounts": round(discounts, 2),
        "net_revenue": round(net_revenue, 2),
        "cost_of_goods": round(cost, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_margin": round(gross_profit / net_revenue, 2) if net_revenue > 0 else 0,
        "expenses": round(expense_total, 2),
        "owner_draws": round(owner_draw_total, 2),
        # v8.18.13: extra (non-stock) sales income — added to net profit
        "other_income": round(extra_income, 2),
        "net_profit": round(net_profit, 2),
        "net_margin": round(net_profit / net_revenue, 2) if net_revenue > 0 else 0,
        "purchases": round(purchases["total"] or 0, 2),
        # v8.18.11: additive — per-category operating expenses for the P&L page
        "expenses_by_category": [
            {"category": r["category"], "total": round(r["total"] or 0, 2)}
            for r in exp_rows
        ],
    }


# ---------- v4.0 Phase 3: Actual Earnings ----------

def get_actual_earnings(month: str = "") -> dict:
    """The 'truth' dashboard: revenue - COGS - operating_expenses = actual_earnings.

    Also exposes:
      - purchases (total confirmed bills this month, shown separately, NOT subtracted)
      - expenses_by_category [{category, total, budget, pct}]
      - cash_reality: {cash_in_drawer, tied_in_unsold_stock, owed_to_you, you_owe_suppliers}
      - comparison: {last_month_earnings, delta_pct}

    v8.14.0: Refactored from ~178 LOC single function into orchestrated helpers
    for readability + testability. Each helper handles one domain (sales, expenses,
    cash_reality, comparison). The public API is unchanged.
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # Compute last month
    try:
        y, m = month.split("-")
        y, m = int(y), int(m)
        last_month = f"{y}-{m-1:02d}" if m > 1 else f"{y-1}-12"
    except Exception:
        last_month = month

    with conn() as c:
        sales_data = _earnings_sales_and_cogs(c, month)
        expenses_data = _earnings_expenses(c, month)
        purchases_data = _earnings_purchases(c, month)
        cash_reality = _earnings_cash_reality(c)
        comparison = _earnings_comparison(c, last_month)
        # v8.18.13: extra (non-stock) sales — other income, no COGS
        extra_income = _extra_sales_month_total(c, month)

    # Compute derived metrics
    total_sales = sales_data["revenue"]
    cost = sales_data["cogs"]
    gross_profit = total_sales - cost
    op_exp = expenses_data["operating"]
    # v8.18.13: actual earnings = gross profit + other income - operating expenses
    actual_earnings = gross_profit + extra_income - op_exp
    net_margin = (actual_earnings / total_sales) if total_sales > 0 else 0

    last_month_earnings = (
        comparison["last_sales"] - comparison["last_cogs"]
        - comparison["last_exp"] + comparison.get("last_extra_sales", 0)
    )
    delta_pct = 0.0
    if last_month_earnings > 0:
        delta_pct = round(100 * (actual_earnings - last_month_earnings) / last_month_earnings, 1)

    return {
        "month": month,
        "total_sales": round(total_sales, 2),
        "cogs": round(cost, 2),
        "gross_profit": round(gross_profit, 2),
        "operating_expenses": round(op_exp, 2),
        # v8.18.13: extra (non-stock) sales income — added to actual earnings
        "extra_sales_income": round(extra_income, 2),
        "actual_earnings": round(actual_earnings, 2),
        "net_margin": round(net_margin, 2),
        "owner_draws": round(expenses_data["owner_draws"], 2),
        "purchases": round(purchases_data, 2),
        "expenses_by_category": expenses_data["by_category"],
        "cash_reality": cash_reality,
        "comparison": {
            "last_month": last_month,
            "last_month_earnings": round(last_month_earnings, 2),
            "delta_pct": delta_pct,
        },
    }


def _earnings_sales_and_cogs(c, month: str) -> dict:
    """Revenue + COGS for the month. v8.14.0: extracted from get_actual_earnings."""
    sales = c.execute(
        "SELECT COALESCE(SUM(total), 0) AS revenue "
        f"FROM sales WHERE strftime('%Y-%m', created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}",
        (month,),
    ).fetchone()
    cogs = c.execute(
        "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS cost "
        "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
        f"WHERE strftime('%Y-%m', s.created_at)=? AND {db.VALID_SALE_FILTER}",
        (month,),
    ).fetchone()
    return {"revenue": float(sales["revenue"] or 0), "cogs": float(cogs["cost"] or 0)}


def _earnings_expenses(c, month: str) -> dict:
    """Operating expenses + owner draws + by-category breakdown. v8.14.0: extracted."""
    operating_exp = c.execute(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
        "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'",
        (month,),
    ).fetchone()
    owner_draws = c.execute(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
        "WHERE strftime('%Y-%m', date)=? AND expense_type='owner_draw'",
        (month,),
    ).fetchone()
    exp_rows = c.execute(
        "SELECT COALESCE(ec.name, e.category) AS category, "
        "COALESCE(e.category_id, 0) AS category_id, "
        "SUM(e.amount) AS total, COALESCE(ec.budget_monthly, 0) AS budget "
        "FROM expenses e "
        "LEFT JOIN expense_categories ec ON e.category_id = ec.id "
        "WHERE strftime('%Y-%m', e.date)=? AND e.expense_type='operating' "
        "GROUP BY COALESCE(ec.name, e.category), COALESCE(e.category_id, 0) "
        "ORDER BY total DESC",
        (month,),
    ).fetchall()
    by_category = []
    for r in exp_rows:
        budget = r["budget"] or 0
        total = r["total"] or 0
        pct = round(100 * total / budget, 1) if budget > 0 else 0
        by_category.append({
            "category": r["category"],
            "category_id": r["category_id"],
            "total": round(float(total), 2),
            "budget": round(float(budget), 2),
            "pct": pct,
        })
    return {
        "operating": float(operating_exp["v"] or 0),
        "owner_draws": float(owner_draws["v"] or 0),
        "by_category": by_category,
    }


def _earnings_purchases(c, month: str) -> float:
    """Total confirmed bill purchases this month. v8.14.0: extracted."""
    purchases = c.execute(
        "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) AS total "
        "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
        "AND strftime('%Y-%m', bill_date)=?",
        (month,),
    ).fetchone()
    return float(purchases["total"] or 0)


def _earnings_cash_reality(c) -> dict:
    """Cash position: drawer + tied stock + receivables + payables. v8.14.0: extracted."""
    cash_drawer = c.execute(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer WHERE date(created_at)=date('now','localtime')"
    ).fetchone()["v"]
    # Tied in unsold stock
    stock_rows = c.execute(
        "SELECT bi.category_id, "
        "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS purchased, "
        "AVG(bi.price) AS avg_cost "
        "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
        "WHERE b.status='confirmed' AND b.deleted_at IS NULL AND bi.category_id IS NOT NULL "
        "GROUP BY bi.category_id"
    ).fetchall()
    sold_map = {r["category_id"]: r["qty"] for r in c.execute(
        "SELECT si.category_id, SUM(si.qty) AS qty "
        "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
        f"WHERE si.category_id IS NOT NULL AND {db.VALID_SALE_FILTER} "
        "GROUP BY si.category_id"
    ).fetchall()}
    adj_map = {r["category_id"]: r["delta"] for r in c.execute(
        "SELECT category_id, SUM(delta) AS delta FROM stock_adjustments WHERE category_id IS NOT NULL GROUP BY category_id"
    ).fetchall()}
    tied_stock = 0.0
    for r in stock_rows:
        purchased = r["purchased"] or 0
        sold = sold_map.get(r["category_id"], 0)
        adj = adj_map.get(r["category_id"], 0)
        stock = max(0, purchased - sold + adj)
        tied_stock += stock * (r["avg_cost"] or 0)
    owed_to_you = c.execute(
        "SELECT COALESCE(SUM(total_credit), 0) AS v FROM customers WHERE deleted_at IS NULL"
    ).fetchone()["v"]
    you_owe = c.execute(
        "SELECT COALESCE(SUM(COALESCE(written_total, computed_total)), 0) AS v "
        "FROM bills WHERE status='confirmed' AND deleted_at IS NULL "
        "AND payment_status='credit'"
    ).fetchone()["v"]
    return {
        "cash_in_drawer": round(float(cash_drawer or 0), 2),
        "tied_in_unsold_stock": round(float(tied_stock or 0), 2),
        "owed_to_you": round(float(owed_to_you or 0), 2),
        "you_owe_suppliers": round(float(you_owe or 0), 2),
    }


def _earnings_comparison(c, last_month: str) -> dict:
    """Last month's sales + cogs + expenses for delta comparison. v8.14.0: extracted.

    v8.18.13: also returns last month's extra-sales income so the MoM delta
    compares like-for-like (actual earnings includes other income now).
    """
    last_sales = c.execute(
        "SELECT COALESCE(SUM(total), 0) AS v FROM sales "
        f"WHERE strftime('%Y-%m', created_at)=? AND {db.VALID_SALE_FILTER_NO_ALIAS}",
        (last_month,),
    ).fetchone()["v"]
    last_cogs = c.execute(
        "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v "
        "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
        f"WHERE strftime('%Y-%m', s.created_at)=? AND {db.VALID_SALE_FILTER}",
        (last_month,),
    ).fetchone()["v"]
    last_exp = c.execute(
        "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses "
        "WHERE strftime('%Y-%m', date)=? AND expense_type='operating'",
        (last_month,),
    ).fetchone()["v"]
    last_extra = c.execute(
        "SELECT COALESCE(SUM(total), 0) AS v FROM extra_sales "
        "WHERE strftime('%Y-%m', sale_date)=?",
        (last_month,),
    ).fetchone()["v"]
    return {
        "last_sales": float(last_sales or 0),
        "last_cogs": float(last_cogs or 0),
        "last_exp": float(last_exp or 0),
        "last_extra_sales": float(last_extra or 0),
    }


# ---------- v8.18.13: Extra (non-stock) Sales ----------

def add_extra_sale(item_name: str, quantity: float = 1, unit_price: float = 0,
                   description: str = "", payment_method: str = "cash",
                   date_str: str = None) -> int:
    """Record a sale made OUTSIDE the POS (scrap/raddi, empty cartons, drums...).

    These are not stock products: no inventory movement, no COGS — pure other
    income. Cash sales credit the cash drawer so the drawer balance and the
    Cash Reality panel stay truthful.
    """
    if not item_name or not item_name.strip():
        raise ValueError("item_name is required")
    quantity = float(quantity or 0)
    unit_price = float(unit_price or 0)
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    if unit_price < 0:
        raise ValueError("unit_price cannot be negative")
    total = round(quantity * unit_price, 2)
    if total <= 0:
        raise ValueError("total must be > 0 (quantity x unit price)")
    if payment_method not in ("cash", "bank", "card", "online"):
        payment_method = "cash"
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    with conn() as c:
        sid = c.execute(
            "INSERT INTO extra_sales(item_name, description, quantity, unit_price, "
            "total, payment_method, sale_date) VALUES(?,?,?,?,?,?,?)",
            (item_name.strip(), description, quantity, unit_price, total,
             payment_method, date_str),
        ).lastrowid
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('extra_sale', ?, ?, ?, 'extra_sale')",
                (total, f"Extra sale: {item_name.strip()}", sid),
            )
    return sid


def list_extra_sales(month: str = "", limit: int = 200, q: str = "") -> list:
    """List extra sales, newest first. Optional month + free-text filter."""
    limit = min(max(1, limit), 1000)
    with conn() as c:
        sql = "SELECT * FROM extra_sales WHERE 1=1"
        args: list = []
        if month:
            sql += " AND strftime('%Y-%m', sale_date)=?"
            args.append(month)
        if q:
            sql += " AND (item_name LIKE ? OR description LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY sale_date DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def delete_extra_sale(sale_id: int) -> bool:
    """Delete an extra sale + its linked cash drawer entry (if cash)."""
    with conn() as c:
        cur = c.execute("DELETE FROM extra_sales WHERE id=?", (sale_id,))
        c.execute(
            "DELETE FROM cash_drawer WHERE reference_type='extra_sale' AND reference_id=?",
            (sale_id,),
        )
        return cur.rowcount > 0


def get_extra_sales_summary(month: str = "") -> dict:
    """Monthly summary for the Extra Sales page: total, count, MoM, top items."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    try:
        y, m = month.split("-")
        y, m = int(y), int(m)
        last_month = f"{y}-{m-1:02d}" if m > 1 else f"{y-1}-12"
    except Exception:
        last_month = month
    with conn() as c:
        total = c.execute(
            "SELECT COALESCE(SUM(total), 0) v, COUNT(*) n, COALESCE(SUM(quantity), 0) qty "
            "FROM extra_sales WHERE strftime('%Y-%m', sale_date)=?",
            (month,),
        ).fetchone()
        last_total = c.execute(
            "SELECT COALESCE(SUM(total), 0) v FROM extra_sales "
            "WHERE strftime('%Y-%m', sale_date)=?",
            (last_month,),
        ).fetchone()["v"]
        by_item = c.execute(
            "SELECT item_name, SUM(total) AS total, SUM(quantity) AS qty, COUNT(*) AS times "
            "FROM extra_sales WHERE strftime('%Y-%m', sale_date)=? "
            "GROUP BY item_name ORDER BY total DESC LIMIT 10",
            (month,),
        ).fetchall()
    cur_total = float(total["v"] or 0)
    prev = float(last_total or 0)
    delta_pct = round(100 * (cur_total - prev) / prev, 1) if prev > 0 else 0.0
    return {
        "month": month,
        "last_month": last_month,
        "month_total": round(cur_total, 2),
        "entries": int(total["n"] or 0),
        "total_qty": round(float(total["qty"] or 0), 2),
        "last_month_total": round(prev, 2),
        "delta_pct": delta_pct,
        "by_item": [
            {"item_name": r["item_name"], "total": round(float(r["total"] or 0), 2),
             "qty": round(float(r["qty"] or 0), 2), "times": int(r["times"] or 0)}
            for r in by_item
        ],
    }


def _extra_sales_month_total(c, month: str) -> float:
    """Total extra-sales income for a month (report helper, takes an open cursor)."""
    try:
        v = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS v FROM extra_sales "
            "WHERE strftime('%Y-%m', sale_date)=?",
            (month,),
        ).fetchone()["v"]
    except Exception:
        v = 0  # table not yet migrated (first boot before init() reruns)
    return float(v or 0)


def get_extra_sales_report(month: str = "") -> dict:
    """v8.18.14: standalone Extra Sales report for the universal export route
    (PDF / Excel / CSV buttons on the Extra Sales page). Returns the month
    summary KPIs + the full entries table + top-items table. Every number
    here is non-POS income (cartons, raddi, scrap...) — the report title
    and labels keep it differentiable from POS sales reports."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    summary = get_extra_sales_summary(month)
    entries = list_extra_sales(month=month, limit=1000)
    # Curated columns for the export table (skip internal created/updated_by)
    sales_list = [
        {
            "sale_date": e.get("sale_date", ""),
            "item_name": e.get("item_name", ""),
            "description": e.get("description", ""),
            "quantity": e.get("quantity", 0),
            "unit_price": e.get("unit_price", 0),
            "total": e.get("total", 0),
            "payment_method": e.get("payment_method", "cash"),
        }
        for e in entries
    ]
    return {
        "month": month,
        "report_title": "Extra Sales (Non-POS) Report",
        "total_income": summary["month_total"],
        "entries": summary["entries"],
        "last_month_total": summary["last_month_total"],
        "delta_pct": summary["delta_pct"],
        "total_qty": summary["total_qty"],
        "sales_list": sales_list,   # entries table (rows of extra sales)
        "top_items": summary["by_item"],
    }


# ---------- Held Orders (park & recall) ----------

def hold_order(customer_name: str, customer_phone: str, notes: str,
               items: list, discount: float, discount_type: str, total: float) -> dict:
    """Save a cart as a held order so the cashier can serve the next customer."""
    with conn() as c:
        # Generate a short reference like HOLD-001
        n = c.execute("SELECT COUNT(*) n FROM held_orders").fetchone()["n"]
        ref = f"HOLD-{n + 1:03d}"
        hid = c.execute(
            "INSERT INTO held_orders(reference, customer_name, customer_phone, notes, "
            "items_json, discount, discount_type, total) VALUES(?,?,?,?,?,?,?,?)",
            (ref, customer_name, customer_phone, notes,
             json.dumps(items), discount, discount_type, total),
        ).lastrowid
    return {"id": hid, "reference": ref}


def list_held_orders() -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM held_orders ORDER BY id DESC LIMIT 50"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d.pop("items_json", "[]"))
        except Exception:
            d["items"] = []
        out.append(d)
    return out


def recall_held_order(hid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM held_orders WHERE id=?", (hid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["items"] = json.loads(d.pop("items_json", "[]"))
    except Exception:
        d["items"] = []
    return d


def delete_held_order(hid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM held_orders WHERE id=?", (hid,))
    return cur.rowcount > 0


# ---------- Quotations ----------

def create_quotation(customer_name: str, customer_phone: str, notes: str,
                     items: list, discount: float, discount_type: str,
                     total: float, valid_days: int = 7) -> dict:
    with conn() as c:
        n = c.execute("SELECT COUNT(*) n FROM quotations").fetchone()["n"]
        quote_no = f"Q-{datetime.now().strftime('%Y%m%d')}-{n + 1:03d}"
        valid_until = None
        if valid_days > 0:
            from datetime import timedelta
            valid_until = (datetime.now() + timedelta(days=valid_days)).strftime("%Y-%m-%d")
        qid = c.execute(
            "INSERT INTO quotations(quote_no, customer_name, customer_phone, notes, "
            "items_json, discount, discount_type, total, valid_until) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (quote_no, customer_name, customer_phone, notes,
             json.dumps(items), discount, discount_type, total, valid_until),
        ).lastrowid
    return {"id": qid, "quote_no": quote_no, "valid_until": valid_until}


def list_quotations(status: str = "") -> list:
    with conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM quotations WHERE status=? ORDER BY id DESC LIMIT 100",
                (status,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM quotations ORDER BY id DESC LIMIT 100"
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["items"] = json.loads(d.pop("items_json", "[]"))
        except Exception:
            d["items"] = []
        out.append(d)
    return out


def get_quotation(qid: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["items"] = json.loads(d.pop("items_json", "[]"))
    except Exception:
        d["items"] = []
    return d


def mark_quotation_converted(qid: int, sale_id: int):
    with conn() as c:
        c.execute(
            "UPDATE quotations SET status='converted' WHERE id=?", (qid,),
        )


def delete_quotation(qid: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM quotations WHERE id=?", (qid,))
    return cur.rowcount > 0


# ---------- Customer payments (settle credit) ----------

def add_customer_payment(customer_id: int, customer_name: str, amount: float,
                         payment_method: str = "cash", notes: str = "") -> int:
    with conn() as c:
        pid = c.execute(
            "INSERT INTO customer_payments(customer_id, customer_name, amount, payment_method, notes) "
            "VALUES(?,?,?,?,?)",
            (customer_id, customer_name, amount, payment_method, notes),
        ).lastrowid
        # Reduce customer's outstanding credit
        c.execute(
            "UPDATE customers SET total_credit = MAX(0, total_credit - ?) WHERE id=?",
            (amount, customer_id),
        )
        # Log to cash drawer if cash
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('payment_in', ?, ?, ?, 'customer_payment')",
                (amount, f"Credit payment: {customer_name}", pid),
            )
    return pid


def list_customer_payments(customer_id: int = None, limit: int = 50) -> list:
    with conn() as c:
        if customer_id:
            rows = c.execute(
                "SELECT * FROM customer_payments WHERE customer_id=? ORDER BY id DESC LIMIT ?",
                (customer_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM customer_payments ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ---------- Loyalty ----------

# Conversion: 1 point = Rs 1 (configurable via settings table)
def get_loyalty_rate() -> float:
    from .db import get_setting
    try:
        return float(get_setting("loyalty_rate", "1"))
    except Exception:
        return 1.0


def get_loyalty_points_value(points: int) -> float:
    return round(points * get_loyalty_rate(), 2)


def redeem_loyalty_points(customer_id: int, points: int, sale_id: int = None) -> dict:
    """Redeem customer's loyalty points and return the rupee discount value."""
    if points <= 0:
        return {"points_used": 0, "rupee_value": 0}
    with conn() as c:
        cust = c.execute("SELECT loyalty_points, name FROM customers WHERE id=?", (customer_id,)).fetchone()
        if not cust:
            raise ValueError("Customer not found")
        available = cust["loyalty_points"]
        use = min(available, points)
        if use <= 0:
            return {"points_used": 0, "rupee_value": 0}
        value = use * get_loyalty_rate()
        c.execute(
            "UPDATE customers SET loyalty_points = loyalty_points - ?, loyalty_redeemed = loyalty_redeemed + ? WHERE id=?",
            (use, use, customer_id),
        )
        c.execute(
            "INSERT INTO loyalty_redemptions(customer_id, sale_id, points_used, rupee_value) VALUES(?,?,?,?)",
            (customer_id, sale_id, use, value),
        )
    return {"points_used": use, "rupee_value": round(value, 2)}


def list_loyalty_redemptions(customer_id: int = None, limit: int = 50) -> list:
    """List loyalty redemptions, optionally filtered by customer."""
    with conn() as c:
        if customer_id:
            rows = c.execute(
                "SELECT lr.*, cu.name AS customer_name "
                "FROM loyalty_redemptions lr "
                "LEFT JOIN customers cu ON lr.customer_id = cu.id "
                "WHERE lr.customer_id = ? ORDER BY lr.id DESC LIMIT ?",
                (customer_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT lr.*, cu.name AS customer_name "
                "FROM loyalty_redemptions lr "
                "LEFT JOIN customers cu ON lr.customer_id = cu.id "
                "ORDER BY lr.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_customer(customer_id: int, name: str = None, phone: str = None,
                    address: str = None) -> bool:
    """Update editable customer fields. Returns True if updated."""
    fields = []
    values = []
    if name is not None and name.strip():
        fields.append("name = ?")
        values.append(name.strip())
    if phone is not None:
        fields.append("phone = ?")
        values.append(phone.strip())
    if address is not None:
        fields.append("address = ?")
        values.append(address.strip())
    if not fields:
        return False
    values.append(customer_id)
    with conn() as c:
        cur = c.execute(f"UPDATE customers SET {', '.join(fields)} WHERE id = ?", values)
        return cur.rowcount > 0


def delete_customer(customer_id: int) -> bool:
    """v8.10: Soft-delete customer — sets deleted_at timestamp.

    Blocks deletion if customer has outstanding credit balance.
    Warns (but allows) if customer has unredeemed loyalty points.
    Preserves the row for audit trail + historical references.
    """
    with conn() as c:
        cust = c.execute(
            "SELECT name, phone, total_credit, loyalty_points, deleted_at "
            "FROM customers WHERE id=?", (customer_id,)
        ).fetchone()
        if not cust:
            return False
        if cust["deleted_at"]:
            return True  # already soft-deleted — idempotent
        # Block if outstanding credit
        if cust["total_credit"] and cust["total_credit"] > 0:
            raise ValueError(
                f"Cannot delete customer '{cust['name']}': outstanding credit balance "
                f"of Rs {cust['total_credit']:.0f}. Settle the credit first."
            )
        # Soft-delete — preserves audit trail + historical sales references
        c.execute(
            "UPDATE customers SET deleted_at=datetime('now','localtime') WHERE id=?",
            (customer_id,)
        )
        return True


def import_customers_csv(rows: list) -> dict:
    """Bulk-import customers from list of dicts (name, phone, address)."""
    added = 0
    skipped = 0
    errors = []
    with conn() as c:
        for i, row in enumerate(rows):
            name = (row.get("name") or "").strip()
            phone = (row.get("phone") or "").strip()
            address = (row.get("address") or "").strip()
            if not name:
                skipped += 1
                continue
            try:
                # Avoid duplicates by phone (if provided)
                if phone:
                    existing = c.execute("SELECT id FROM customers WHERE phone=? AND deleted_at IS NULL", (phone,)).fetchone()
                    if existing:
                        skipped += 1
                        continue
                c.execute(
                    "INSERT INTO customers(name, phone, address) VALUES(?,?,?)",
                    (name, phone, address),
                )
                added += 1
            except Exception as e:
                errors.append(f"Row {i + 1}: {e}")
    return {"added": added, "skipped": skipped, "errors": errors}


# ---------- Cash drawer extra actions ----------

def cash_in(amount: float, description: str = "") -> int:
    """Add extra cash to the drawer (e.g., starting float top-up)."""
    with conn() as c:
        cid = c.execute(
            "INSERT INTO cash_drawer(type, amount, description) VALUES('cash_in', ?, ?)",
            (amount, description or "Cash in"),
        ).lastrowid
    return cid


def cash_out(amount: float, description: str = "") -> int:
    """Remove cash from the drawer (e.g., for petty expense)."""
    with conn() as c:
        cid = c.execute(
            "INSERT INTO cash_drawer(type, amount, description) VALUES('cash_out', ?, ?)",
            (-abs(amount), description or "Cash out"),
        ).lastrowid
    return cid


def get_next_invoice_no() -> str:
    """Get gapless sequential invoice number. v3.1.1 — atomic with BEGIN IMMEDIATE."""
    from .db import conn
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT next_invoice FROM invoice_seq WHERE id=1").fetchone()
        if not row:
            c.execute("INSERT INTO invoice_seq(id, next_invoice) VALUES(1, 1)")
            next_num = 1
        else:
            next_num = row["next_invoice"]
        c.execute("UPDATE invoice_seq SET next_invoice = next_invoice + 1 WHERE id=1")
        c.execute("COMMIT")
    return f"INV-{next_num:06d}"
