"""v7.0 Phase 1 — Profit CASH (buckets, withdrawals, reserve, forecast).

Extracted from profit.py. Imports _get_setting from profit_engine.
"""
from datetime import datetime, timedelta
from .db import conn, log_activity
from . import db  # v8.14.0: needed for db.VALID_SALE_FILTER constant
from .profit_engine import _get_setting, month_to_range, month_to_date_range


def get_cash_buckets(date: str = "") -> dict:
    """The 4 cash buckets: Stock Replacement, Operating Expenses, Business Reserve, Owner Withdrawal.
    v8.12.1: Also exposes capital_injections_total so the UI can honestly show
    where the cash came from (initial investment / top-ups / partner contributions).
    v8.13.2: SCALABILITY — rewrote strftime('%Y-%m', created_at)=? to range
    form (created_at >= ? AND created_at < ?) so the query uses idx_sales_created
    instead of a full table scan. ~10-100× faster at 1M+ rows.
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    month = date[:7]
    # v8.13.2: Convert month to range for index-friendly queries
    ts_start, ts_end = month_to_range(month)  # for created_at (timestamp)
    dt_start, dt_end = month_to_date_range(month)  # for date columns (no time)
    with conn() as c:
        # v8.13.2: range form uses idx_sales_created + idx_sale_items_sale
        cogs_row = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND s.created_at >= ? AND s.created_at < ?",
            (ts_start, ts_end)).fetchone()
        sales_row = c.execute(
            f"SELECT COALESCE(SUM(total), 0) AS v FROM sales WHERE created_at >= ? AND created_at < ? AND {db.VALID_SALE_FILTER_NO_ALIAS}",
            (ts_start, ts_end)).fetchone()
        op_exp_row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM expenses WHERE date >= ? AND date < ? AND expense_type='operating'",
            (dt_start, dt_end)).fetchone()
        withdrawals_row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM owner_withdrawals WHERE created_at >= ? AND created_at < ?",
            (ts_start, ts_end)).fetchone()
        # v8.13.1: cash_drawer reads from cash_summary (O(1) via trigger-maintained total)
        cash_row = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer").fetchone()
        # v8.12.1: capital injections (all-time, NOT month-filtered — capital is permanent equity)
        capital_row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM capital_injections"
        ).fetchone()
    sales = float(sales_row["v"] or 0); cogs = float(cogs_row["v"] or 0)
    gross_profit = sales - cogs; op_exp = float(op_exp_row["v"] or 0)
    owner_withdrawals = float(withdrawals_row["v"] or 0); cash = float(cash_row["v"] or 0)
    capital_injections_total = float(capital_row["v"] or 0)
    reserve_pct = float(_get_setting("business_reserve_pct", "10") or "10")
    business_reserve = round(gross_profit * reserve_pct / 100, 2)
    available = cash - cogs - op_exp - business_reserve
    return {"date": date, "month": month, "sales": round(sales, 2), "cogs": round(cogs, 2),
            "gross_profit": round(gross_profit, 2),
            "buckets": {"stock_replacement": round(cogs, 2), "operating_expenses": round(op_exp, 2),
                        "business_reserve": business_reserve, "owner_withdrawal": round(owner_withdrawals, 2)},
            "cash_in_drawer": round(cash, 2), "available_for_withdrawal": round(available, 2),
            "business_reserve_pct": reserve_pct,
            "capital_injections_total": round(capital_injections_total, 2),
            "note": "Stock Replacement = COGS (reinvest to maintain stock). Business Reserve = % of GP. Owner Withdrawal excludes operating expenses. Capital Injections are owner-invested equity (initial investment + top-ups) and are visible in cash_in_drawer."}


def get_stock_reserve() -> dict:
    """Stock reserve: daily COGS avg (30d), days of cover, target, gap, recommendation."""
    today = datetime.now().strftime("%Y-%m-%d")
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    with conn() as c:
        cogs_30d = c.execute(
            "SELECT COALESCE(SUM(si.cost_price * si.qty), 0) AS v FROM sale_items si JOIN sales s ON si.sale_id=s.id "
            f"WHERE {db.VALID_SALE_FILTER} AND date(s.created_at) >= ?", (thirty_days_ago,)).fetchone()["v"]
        cash = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer").fetchone()["v"]
    daily_cogs_avg = float(cogs_30d or 0) / 30.0; cash = float(cash or 0)
    days_of_cover = round(cash / daily_cogs_avg, 1) if daily_cogs_avg > 0 else 0.0
    target_days = float(_get_setting("stock_reserve_target_days", "15") or "15")
    gap = round(target_days - days_of_cover, 1)
    if days_of_cover >= target_days:
        color = "green"; recommendation = f"Healthy — {days_of_cover:.1f} days of stock-purchase cover (target {target_days:.0f} days). Safe to withdraw surplus cash."
    elif days_of_cover >= target_days / 2:
        color = "amber"; recommendation = f"Tight — only {days_of_cover:.1f} days of cover (target {target_days:.0f}). Limit withdrawals; prioritize restocking."
    else:
        color = "red"; recommendation = f"Critical — {days_of_cover:.1f} days of cover. Do NOT withdraw cash; reinvest in stock immediately."
    if daily_cogs_avg > 0:
        safe_weekly = round(max(0, cash - target_days * daily_cogs_avg) / 4, 2)
    else:
        safe_weekly = round(cash / 4, 2) if cash > 0 else 0.0
    return {"date": today, "daily_cogs_avg_30d": round(daily_cogs_avg, 2),
            "cogs_30d_total": round(float(cogs_30d or 0), 2), "cash_in_drawer": round(cash, 2),
            "stock_reserve_days": days_of_cover, "stock_reserve_target_days": target_days,
            "gap": gap, "color": color, "recommendation": recommendation,
            "safe_withdrawal_weekly": safe_weekly}


def add_owner_withdrawal(amount: float, payment_method: str = "cash", notes: str = "") -> int:
    """Record an owner withdrawal. Reduces cash_drawer but is NOT an operating expense."""
    if amount <= 0:
        raise ValueError("Withdrawal amount must be positive")
    with conn() as c:
        wid = c.execute(
            "INSERT INTO owner_withdrawals(amount, payment_method, notes) VALUES(?,?,?)",
            (amount, payment_method, notes)).lastrowid
        if payment_method == "cash":
            c.execute(
                "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type) "
                "VALUES('owner_withdrawal', ?, ?, ?, 'owner_withdrawal')",
                (-amount, f"Owner withdrawal #{wid}", wid))
    log_activity("owner_withdrawal", "cash", wid, f"Owner withdrew Rs {amount:.0f} ({payment_method})",
                 {"amount": amount, "method": payment_method})
    return wid


def list_owner_withdrawals(limit: int = 100) -> list:
    with conn() as c:
        rows = c.execute("SELECT * FROM owner_withdrawals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_owner_withdrawals_summary(month: str = "") -> dict:
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # v8.13.2: range form uses idx_owner_withdrawals_created
    ts_start, ts_end = month_to_range(month)
    with conn() as c:
        month_total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM owner_withdrawals WHERE created_at >= ? AND created_at < ?",
            (ts_start, ts_end)).fetchone()["v"]
        all_time = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM owner_withdrawals").fetchone()["v"]
        count = c.execute(
            "SELECT COUNT(*) n FROM owner_withdrawals WHERE created_at >= ? AND created_at < ?",
            (ts_start, ts_end)).fetchone()["n"]
    return {"month": month, "month_total": round(float(month_total or 0), 2),
            "all_time_total": round(float(all_time or 0), 2), "month_count": count}


# ─── v8.12.1: Capital Injections ───────────────────────────────────
# Owner-invested capital. Each row writes a matching +amount cash_drawer entry
# with type='capital_injection' so the cash drawer sum reflects reality (the
# owner had money in the drawer before any sale happened).
#
# WHY: Without this, the "Available for Withdrawal" formula goes negative on
# Day 1 because every confirmed supplier bill writes a -amount cash_drawer row
# (purchase), but there was never a matching +amount row for the initial
# capital the owner used to buy that stock.
#
# SOURCES:
#   - owner_pocket  : Money from the owner's personal savings
#   - partner       : Capital from a business partner / co-owner
#   - bank_loan     : Loan injected into the business (must be repaid separately)
#   - opening_balance : One-time entry to fix the "Day 1 negative cash" trap

VALID_SOURCES = ('owner_pocket', 'partner', 'bank_loan', 'opening_balance')


def add_capital_injection(amount: float, source: str = 'owner_pocket',
                          payment_method: str = 'cash', notes: str = '',
                          date: str = "") -> int:
    """Record a capital injection. Adds to cash_drawer (+amount) AND creates
    a capital_injections row for tracking. NOT an operating expense, NOT revenue.

    Args:
        amount: Must be > 0.
        source: One of VALID_SOURCES.
        payment_method: 'cash' (default) or 'bank'. If 'bank', cash_drawer is
            still credited (bank transfers settle into the business bank account
            which is part of total cash position).
        notes: Free-text description.
        date: Optional ISO date string. If empty, uses today.

    Returns:
        The new capital_injections.id.
    """
    if amount <= 0:
        raise ValueError("Capital injection amount must be positive")
    if source not in VALID_SOURCES:
        raise ValueError(f"Invalid source '{source}'. Must be one of: {', '.join(VALID_SOURCES)}")
    if not date:
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        inj_id = c.execute(
            "INSERT INTO capital_injections(amount, source, payment_method, notes, created_at) "
            "VALUES(?,?,?,?,?)",
            (amount, source, payment_method, notes, date)
        ).lastrowid
        # Always credit cash_drawer (+amount) — capital injections increase
        # the business's cash position regardless of whether they came in as
        # cash or bank transfer.
        c.execute(
            "INSERT INTO cash_drawer(type, amount, description, reference_id, reference_type, created_at) "
            "VALUES('capital_injection', ?, ?, ?, 'capital_injection', ?)",
            (amount, f"Capital injection #{inj_id} ({source})", inj_id, date)
        )
    log_activity("capital_injection", "cash", inj_id,
                 f"Owner injected Rs {amount:.0f} ({source}, {payment_method})",
                 {"amount": amount, "source": source, "method": payment_method, "notes": notes})
    return inj_id


def list_capital_injections(limit: int = 100) -> list:
    """List capital injections, most recent first."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM capital_injections ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_capital_injections_summary() -> dict:
    """Summary of all capital injections for the cash-buckets header card."""
    with conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count "
            "FROM capital_injections"
        ).fetchone()
        by_source = c.execute(
            "SELECT source, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count "
            "FROM capital_injections GROUP BY source ORDER BY total DESC"
        ).fetchall()
    return {
        "all_time_total": round(float(row["total"] or 0), 2),
        "all_time_count": row["count"],
        "by_source": [
            {"source": r["source"], "total": round(float(r["total"] or 0), 2), "count": r["count"]}
            for r in by_source
        ]
    }


# ─── v8.13.0: Stock Write-offs ───────────────────────────────────────
# Damage / expiry / theft / sample / display — reduces stock_state AND
# records the loss value (qty × avg_cost) in a dedicated audit table
# (stock_writeoffs) so the monthly P&L can show a "Shrinkage" line item
# separately from regular stock adjustments.

VALID_WRITEOFF_REASONS = ('damage', 'expiry', 'theft', 'sample', 'display', 'other')


def add_stock_writeoff(category_id: int, qty: float, reason: str,
                       notes: str = "", manager_pin_verified: bool = False) -> int:
    """Record a stock write-off. Reduces category_stock_state AND creates a
    stock_writeoffs row with the loss value (qty × avg_cost at time of write-off).

    Args:
        category_id: The price_categories.id.
        qty: Must be > 0. Will be deducted from stock_state.current_qty.
        reason: One of VALID_WRITEOFF_REASONS.
        notes: Free-text description (optional).
        manager_pin_verified: True if the caller verified a manager PIN
            (write-offs are an admin operation). If False, the row is still
            recorded but flagged in audit (used for the UI to know whether
            to prompt).

    Returns:
        The new stock_writeoffs.id.

    Side effects (all in a single transaction):
        1. INSERT into stock_writeoffs (snapshot of unit_cost + loss_value)
        2. INSERT into stock_adjustments (delta = -qty, reason = 'writeoff: <reason>')
           — keeps the existing adjustments ledger intact for stock reconciliation
        3. UPDATE category_stock_state (current_qty -= qty, current_value -= loss_value,
           avg_cost recomputed)
        4. log_activity('stock_writeoff', ...)
    """
    if qty <= 0:
        raise ValueError("Write-off quantity must be positive")
    if reason not in VALID_WRITEOFF_REASONS:
        raise ValueError(f"Invalid reason '{reason}'. Must be one of: {', '.join(VALID_WRITEOFF_REASONS)}")

    from .profit_engine import _get_state, _save_state
    with conn() as c:
        # Snapshot the current avg_cost for the loss-value computation
        state = _get_state(c, category_id)
        if not state:
            raise ValueError(f"Category {category_id} has no stock_state — nothing to write off")
        current_qty = float(state.get("qty") or 0)
        current_avg_cost = float(state.get("avg") or 0)
        current_value = float(state.get("value") or 0)
        if current_qty <= 0:
            raise ValueError(f"Category {category_id} has 0 stock — cannot write off {qty} units")
        if qty > current_qty:
            raise ValueError(
                f"Write-off qty {qty} exceeds current stock {current_qty} for category {category_id}"
            )
        unit_cost = current_avg_cost
        loss_value = round(qty * unit_cost, 2)

        # 1. Insert into stock_writeoffs (snapshot table)
        woff_id = c.execute(
            "INSERT INTO stock_writeoffs(category_id, qty, unit_cost, loss_value, "
            "reason, notes, manager_pin_verified, created_at) "
            "VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))",
            (category_id, qty, unit_cost, loss_value, reason, notes,
             1 if manager_pin_verified else 0)
        ).lastrowid

        # 2. Insert into stock_adjustments (existing ledger)
        c.execute(
            "INSERT INTO stock_adjustments(category_id, delta, reason, created_at) "
            "VALUES(?,?,?,datetime('now','localtime'))",
            (category_id, -qty, f"writeoff: {reason}" + (f" — {notes}" if notes else ""))
        )

        # 3. Reduce category_stock_state
        new_qty = max(0, current_qty - qty)
        new_value = max(0, current_value - loss_value)
        new_avg = new_value / new_qty if new_qty > 0 else 0
        _save_state(c, category_id, new_qty, new_value, new_avg)

    log_activity("stock_writeoff", "inventory", woff_id,
                 f"Wrote off {qty} units of category #{category_id} "
                 f"({reason}) — loss Rs {loss_value:.0f}",
                 {"category_id": category_id, "qty": qty, "unit_cost": unit_cost,
                  "loss_value": loss_value, "reason": reason, "notes": notes})
    return woff_id
