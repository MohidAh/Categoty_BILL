"""v7.0 Phase 1 — Running Weighted Average Cost ENGINE.

State primitives, chronological replay, and stock-state reads.
Extracted from profit.py (was 1,187 lines; this module is ~370 lines).

Core accounting identity: under weighted-average costing, a sale does NOT
change the average cost per piece — it reduces qty and value proportionally.
Only purchases shift the average. COGS for a sale = qty × avg_cost_at_time_of_sale.

Phase 0 PR 2: All mutating functions now accept an optional keyword-only
`c` parameter (a SQLite connection). If provided, the function uses that
connection and does NOT commit (caller controls the transaction). If not
provided, the function opens its own `write_tx()` (backward compatible).
"""
import logging
from datetime import datetime
from . import db
from .db import conn, log_activity
from .validate import pieces

logger = logging.getLogger(__name__)


def month_to_range(month: str) -> tuple:
    """Convert a 'YYYY-MM' month string to a (start, end) range suitable for
    use with `WHERE created_at >= ? AND created_at < ?`.

    SCALABILITY (v8.13.2): SQLite cannot use a B-tree index when the column
    is wrapped in a function like `strftime('%Y-%m', created_at)=?` — every
    query that does this falls back to a full table scan. At 1M+ sales rows,
    the monthly KPI/dashboard/P&L queries become 10-100× slower than they
    should be.

    This helper converts a month string to a range so the query can use the
    existing `idx_sales_created` index:

        BEFORE (full table scan):
            SELECT SUM(total) FROM sales
            WHERE strftime('%Y-%m', created_at) = '2026-08'
              AND {db.VALID_SALE_FILTER_NO_ALIAS}

        AFTER (uses idx_sales_created):
            SELECT SUM(total) FROM sales
            WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01'
              AND {db.VALID_SALE_FILTER_NO_ALIAS}

    Args:
        month: 'YYYY-MM' string (e.g. '2026-08'). Defaults to current month if empty.

    Returns:
        Tuple of (start_str, end_str) where:
        - start_str = 'YYYY-MM-01 00:00:00' (first day of month)
        - end_str = first day of NEXT month (exclusive upper bound)
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")
    # Parse the month
    try:
        y, m = month.split("-")
        y, m = int(y), int(m)
    except (ValueError, AttributeError):
        # Invalid month format — default to current month
        month = datetime.now().strftime("%Y-%m")
        y, m = month.split("-")
        y, m = int(y), int(m)
    # Compute next month (handles December → January of next year)
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    start_str = f"{y:04d}-{m:02d}-01 00:00:00"
    end_str = f"{next_y:04d}-{next_m:02d}-01 00:00:00"
    return (start_str, end_str)


def month_to_date_range(month: str) -> tuple:
    """Like month_to_range but returns DATE strings (no time component) for
    tables that store dates as 'YYYY-MM-DD' (e.g. expenses.date, bills.bill_date).
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")
    try:
        y, m = month.split("-")
        y, m = int(y), int(m)
    except (ValueError, AttributeError):
        month = datetime.now().strftime("%Y-%m")
        y, m = month.split("-")
        y, m = int(y), int(m)
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    start_str = f"{y:04d}-{m:02d}-01"
    end_str = f"{next_y:04d}-{next_m:02d}-01"
    return (start_str, end_str)


def log_state_drift(operation: str, category_id: int, error: str,
                    context: dict = None, *, c=None):
    """Log a state-mutation failure to BOTH the Python logger AND the activity_log.

    Phase 0 PR 3: optional keyword-only `c` (SQLite connection). Pass the
    caller's connection when called from inside a write_tx() — otherwise
    this would try to open a SECOND connection (which deadlocks on the
    parent's BEGIN IMMEDIATE lock).
    """
    msg = f"STATE DRIFT: {operation} failed for category_id={category_id}: {error}"
    logger.warning(msg, extra=context or {})
    log_activity(
        "state_drift_warning", "category_stock_state", category_id,
        msg, {"operation": operation, "category_id": category_id,
              "error": str(error), **(context or {})},
        c=c,
    )


def _get_state(c, category_id: int) -> dict:
    """Read current state for a category."""
    row = c.execute(
        "SELECT current_qty, current_value, current_avg_cost, last_txn_at "
        "FROM category_stock_state WHERE category_id=?",
        (category_id,),
    ).fetchone()
    if row is None:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0, "last_txn_at": None}
    return {
        "qty": float(row["current_qty"] or 0),
        "value": float(row["current_value"] or 0),
        "avg": float(row["current_avg_cost"] or 0),
        "last_txn_at": row["last_txn_at"],
    }


def _save_state(c, category_id: int, qty: float, value: float, avg: float,
                last_txn_at: str = None):
    """Upsert the state row for a category."""
    if last_txn_at is None:
        last_txn_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "INSERT INTO category_stock_state(category_id, current_qty, current_value, "
        "current_avg_cost, last_txn_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(category_id) DO UPDATE SET "
        "current_qty=excluded.current_qty, current_value=excluded.current_value, "
        "current_avg_cost=excluded.current_avg_cost, last_txn_at=excluded.last_txn_at",
        (category_id, qty, value, avg, last_txn_at),
    )


# ─── Purchase ────────────────────────────────────────────────────────────────

def apply_purchase_to_state(category_id: int, qty: float, unit_price: float,
                             txn_at: str = None, *, c=None) -> dict:
    """Apply a purchase to the running state.

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).
    """
    if category_id is None or qty is None or qty <= 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0}
    if c is not None:
        return _apply_purchase_to_state(c, category_id, qty, unit_price, txn_at)
    with db.write_tx() as own_c:
        return _apply_purchase_to_state(own_c, category_id, qty, unit_price, txn_at)


def _apply_purchase_to_state(c, category_id, qty, unit_price, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    unit_price = float(unit_price or 0)
    st = _get_state(c, category_id)
    new_qty = st["qty"] + qty
    new_value = st["value"] + (qty * unit_price)
    new_avg = (new_value / new_qty) if new_qty > 0 else 0.0
    new_avg = round(new_avg, 2)
    _save_state(c, category_id, new_qty, new_value, new_avg, txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": new_avg}


# ─── Reverse Purchase (PR 5) ───────────────────────────────────────────────
# Used by confirm_bill() when re-confirming an already-confirmed bill: the
# OLD bill_items must be reversed (qty + value subtracted at ORIGINAL price)
# BEFORE applying the NEW bill_items. This is symmetric to apply_purchase_to_state
# but uses the original unit_price (NOT current avg_cost) — see v8.5.5 fix note
# in confirm_bill() for why this matters (double-subtraction bug).

def reverse_purchase_in_state(category_id: int, qty: float, unit_price: float,
                                txn_at: str = None, *, c=None) -> dict:
    """Reverse a purchase from the running state. Returns {qty, value, avg}.

    Subtracts `qty` from current_qty and `(qty × unit_price)` from current_value,
    using the ORIGINAL unit_price (NOT current avg_cost). This is critical for
    correctness when reversing an old bill during re-confirm: using current avg
    would double-subtract value (v8.5.5 bug).

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).

    PR 5: introduced for atomic confirm_bill() — replaces the inline
    `_get_state` + `_save_state` code that was duplicated in confirm().
    """
    if category_id is None or qty is None or qty <= 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0}
    if c is not None:
        return _reverse_purchase_in_state(c, category_id, qty, unit_price, txn_at)
    with db.write_tx() as own_c:
        return _reverse_purchase_in_state(own_c, category_id, qty, unit_price, txn_at)


def _reverse_purchase_in_state(c, category_id, qty, unit_price, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    unit_price = float(unit_price or 0)
    st = _get_state(c, category_id)
    new_qty = st["qty"] - qty
    new_value = st["value"] - (qty * unit_price)
    # Recompute avg (only if qty > 0; if qty goes to 0 or negative, value should also be 0)
    if new_qty > 0:
        new_avg = round(new_value / new_qty, 2)
    else:
        new_avg = 0.0
        new_value = 0.0  # clamp negative values when pool is empty
    _save_state(c, category_id, new_qty, new_value, new_avg, txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": new_avg}


# ─── Sale ───────────────────────────────────────────────────────────────────

def apply_sale_to_state(category_id: int, qty: float,
                         txn_at: str = None, *, c=None) -> dict:
    """Apply a sale to the running state. Returns {qty, value, avg, cogs}.

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).
    """
    if category_id is None or qty is None or qty <= 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0, "cogs": 0.0}
    if c is not None:
        return _apply_sale_to_state(c, category_id, qty, txn_at)
    with db.write_tx() as own_c:
        return _apply_sale_to_state(own_c, category_id, qty, txn_at)


def _apply_sale_to_state(c, category_id, qty, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    st = _get_state(c, category_id)
    cogs = round(qty * st["avg"], 2)
    new_qty = st["qty"] - qty
    new_value = st["value"] - cogs
    _save_state(c, category_id, new_qty, new_value, st["avg"], txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": st["avg"], "cogs": cogs}


# ─── Transfer OUT ───────────────────────────────────────────────────────────

def apply_transfer_out_to_state(category_id: int, qty: float,
                                 txn_at: str = None, *, c=None) -> dict:
    """v8.0 Phase 4 — Apply an inter-branch transfer OUT.

    Reduces qty + value at the SENDER's current average cost. The average cost
    is UNCHANGED (transfers are inventory movements, not sales — no COGS, no revenue).
    The receiving branch applies this as a purchase at the captured unit_cost
    via the existing apply_purchase_to_state.

    Returns {qty, value, avg, unit_cost, line_value}.

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).
    """
    if category_id is None or qty is None or qty <= 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0, "unit_cost": 0.0, "line_value": 0.0}
    if c is not None:
        return _apply_transfer_out_to_state(c, category_id, qty, txn_at)
    with db.write_tx() as own_c:
        return _apply_transfer_out_to_state(own_c, category_id, qty, txn_at)


def _apply_transfer_out_to_state(c, category_id, qty, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    st = _get_state(c, category_id)
    unit_cost = st["avg"]  # capture the sender's current avg cost
    line_value = round(qty * unit_cost, 2)
    new_qty = st["qty"] - qty
    new_value = st["value"] - line_value
    # avg UNCHANGED — transfers don't affect the running weighted avg
    _save_state(c, category_id, new_qty, new_value, st["avg"], txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": st["avg"],
            "unit_cost": unit_cost, "line_value": line_value}


# ─── Peek avg cost ──────────────────────────────────────────────────────────

def peek_avg_cost(c, category_id: int) -> float:
    """Read-only: get the current avg cost using an EXISTING connection."""
    if category_id is None:
        return 0.0
    st = _get_state(c, category_id)
    if st["avg"] > 0 and st["qty"] > 0:
        return st["avg"]
    row = c.execute(
        "SELECT SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_cost, "
        "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_pieces "
        "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
        "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
        "AND bi.category_id=? AND bi.price > 0 AND bi.qty > 0",
        (category_id,),
    ).fetchone()
    total_cost = float(row["total_cost"] or 0)
    total_pieces = float(row["total_pieces"] or 0)
    if total_pieces <= 0:
        return 0.0
    return round(total_cost / total_pieces, 2)


def peek_avg_cost_as_of(c, category_id: int, as_of_date: str) -> float:
    """v8.16.13: Read-only: get the historical avg cost as of a specific date.

    Computes the weighted-avg cost using only confirmed bills with bill_date <= as_of_date.
    Used by POS import to capture the correct cost_price for historical sales —
    prevents the "re-import re-computes cost using today's avg cost" bug.

    Args:
        c: an open DB connection (read-only)
        category_id: the category to look up
        as_of_date: YYYY-MM-DD string — only bills dated <= this are included

    Returns:
        Weighted-avg cost as of that date, or 0 if no purchases yet.
    """
    if category_id is None or not as_of_date:
        # Fallback: use current avg cost if no date provided
        return peek_avg_cost(c, category_id)
    # Compute weighted avg from confirmed bills up to as_of_date
    # COALESCE(b.bill_date, date(b.created_at)) handles bills where bill_date is NULL
    row = c.execute(
        "SELECT SUM(bi.price * CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_cost, "
        "SUM(CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END) AS total_pieces "
        "FROM bill_items bi JOIN bills b ON bi.bill_id=b.id "
        "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
        "AND bi.category_id=? AND bi.price > 0 AND bi.qty > 0 "
        "AND date(COALESCE(b.bill_date, date(b.created_at))) <= date(?)",
        (category_id, as_of_date),
    ).fetchone()
    total_cost = float(row["total_cost"] or 0)
    total_pieces = float(row["total_pieces"] or 0)
    if total_pieces <= 0:
        # No purchases as of that date — fall back to current avg cost
        return peek_avg_cost(c, category_id)
    return round(total_cost / total_pieces, 2)


# ─── Reverse sale (refund) ──────────────────────────────────────────────────

def reverse_sale_in_state(category_id: int, qty: float, cogs: float = None,
                           txn_at: str = None, *, c=None) -> dict:
    """Reverse a sale (for refunds).

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).
    """
    if category_id is None or qty is None or qty <= 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0}
    if c is not None:
        return _reverse_sale_in_state(c, category_id, qty, cogs, txn_at)
    with db.write_tx() as own_c:
        return _reverse_sale_in_state(own_c, category_id, qty, cogs, txn_at)


def _reverse_sale_in_state(c, category_id, qty, cogs, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    st = _get_state(c, category_id)
    if cogs is None:
        cogs = round(qty * st["avg"], 2)
    new_qty = st["qty"] + qty
    new_value = st["value"] + cogs
    new_avg = (new_value / new_qty) if new_qty > 0 else 0.0
    new_avg = round(new_avg, 2)
    _save_state(c, category_id, new_qty, new_value, new_avg, txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": new_avg}


# ─── Adjustment ─────────────────────────────────────────────────────────────

def apply_adjustment_to_state(category_id: int, delta: float,
                               txn_at: str = None, *, c=None) -> dict:
    """Apply a stock adjustment (damaged/lost/returned).

    If `c` is provided, uses that connection and does NOT commit.
    If `c` is None, opens its own write_tx() (backward compatible).
    """
    if category_id is None or delta is None or delta == 0:
        return {"qty": 0.0, "value": 0.0, "avg": 0.0}
    if c is not None:
        return _apply_adjustment_to_state(c, category_id, delta, txn_at)
    with db.write_tx() as own_c:
        return _apply_adjustment_to_state(own_c, category_id, delta, txn_at)


def _apply_adjustment_to_state(c, category_id, delta, txn_at=None):
    """Internal: uses the provided connection. Does NOT commit."""
    st = _get_state(c, category_id)
    if delta < 0:
        value_change = round(abs(delta) * st["avg"], 2)
        new_qty = st["qty"] + delta
        new_value = st["value"] - value_change
        new_avg = st["avg"]
    else:
        value_change = round(delta * st["avg"], 2)
        new_qty = st["qty"] + delta
        new_value = st["value"] + value_change
        new_avg = (new_value / new_qty) if new_qty > 0 else 0.0
        new_avg = round(new_avg, 2)
    _save_state(c, category_id, new_qty, new_value, new_avg, txn_at)
    return {"qty": new_qty, "value": round(new_value, 2), "avg": new_avg}


# ─── Bags categories: stock tracks qty SOLD (v8.19.1, unified v8.18.17) ─────
#
# BUSINESS RULE (user-defined): shopping-bag categories ("Bag Rs 10/20/30/…",
# auto-created by the Ezi POS import, or any category named "Bag…"/"Bags" /
# coded BAG…) do NOT have their purchase bills entered in BillBook — bags are
# bought as EXPENSES. So the normal "stock = purchased − sold" model can only
# ever go negative for bags. Instead, the bag category's stock qty follows:
#
#     sold = total qty of non-refunded sale_items for the category
#     if current_qty < sold:  current_qty = sold   ("purchased is raised
#     else:                   leave it alone        to equal SOLD")
#
# i.e. qty = max(purchased, sold) — the number only ever rises to the
# total-sold level; it is NEVER lowered by the sync (a manual stock
# adjustment or a higher historical value is never clobbered downward).
#
# UNIFIED MODEL (v8.18.17): bag SALE events never decrement the bag pool on
# ANY path, so every path computes the same thing:
#   - Ezi POS import:      bag lines skip the per-sale decrement; the
#                          end-of-import sync raises qty to max(qty, sold).
#   - built-in POS sales:  bag lines skip the decrement too; the sale applies
#                          the same sync (floor at sold).
#   - refunds/voids:       bag lines are NEVER re-added (nothing to undo).
#   - full rebuild:        bag sale events are skipped in the replay; the
#                          post-replay sync raises qty to max(purchases +
#                          adjustments, sold). Skipping also preserves the
#                          original sale_items.cost_price for imported bags
#                          (INVTRANS.COST), instead of overwriting it with
#                          the (usually zero) bag pool average.

def bag_category_ids(c) -> set:
    """Active price_categories that are bag categories.

    Matched by name ("Bag Rs 20", "Bags", …) or code ("BAG20", …) prefix —
    the same convention the Ezi import uses when auto-creating them.
    """
    rows = c.execute(
        "SELECT id FROM price_categories WHERE active=1 AND ("
        "LOWER(TRIM(name)) LIKE 'bag%' OR UPPER(TRIM(COALESCE(code,''))) LIKE 'BAG%')"
    ).fetchall()
    return {r["id"] for r in rows}


def sync_bags_stock_to_sold(c=None, category_ids=None) -> list:
    """Apply the bags rule (see block comment above). Returns a list of
    {category_id, name, from_qty, to_qty} for the categories that were
    raised; empty list when nothing needed changing.

    Safe to call repeatedly (idempotent: once qty == sold it stops matching).
    If `c` is provided, uses that connection and does NOT commit;
    otherwise opens its own write_tx().
    `category_ids` (optional) restricts the sync to a subset of bag
    categories — used by the POS sale path so a sale only touches the
    bag categories actually sold, not every bag in the shop.
    """
    def _run(c):
        ids = bag_category_ids(c)
        if category_ids:
            ids &= {int(x) for x in category_ids if x is not None}
        changed = []
        for cid in ids:
            sold = c.execute(
                "SELECT COALESCE(SUM(si.qty), 0) AS s FROM sale_items si "
                "JOIN sales s ON si.sale_id = s.id "
                f"WHERE si.category_id=? AND si.qty > 0 AND {db.VALID_SALE_FILTER}",
                (cid,),
            ).fetchone()["s"]
            st = _get_state(c, cid)
            if st["qty"] < sold:
                name_row = c.execute(
                    "SELECT name FROM price_categories WHERE id=?", (cid,)
                ).fetchone()
                _save_state(c, cid, sold, round(sold * st["avg"], 2), st["avg"])
                changed.append({
                    "category_id": cid,
                    "name": name_row["name"] if name_row else str(cid),
                    "from_qty": round(st["qty"], 2),
                    "to_qty": round(sold, 2),
                })
        return changed

    if c is not None:
        return _run(c)
    with db.write_tx() as own_c:
        return _run(own_c)


# ─── Rebuild (recovery tool — NOT called from normal transactions) ──────────

def rebuild_stock_state() -> dict:
    """Rebuild category_stock_state from scratch by replaying all confirmed
    bills and non-refunded sales chronologically. Also rewrites every
    sale_items.cost_price. Idempotent.

    This is a RECOVERY tool — it uses its own connections and is NOT part
    of the normal transaction-aware write path. It should only be called:
    - On boot if stock_state_dirty=1
    - From the repair_stock_state.py script
    - After historical POS imports (where chronological order matters)
    - After re-confirming old bills that affect past periods
    """
    with conn() as c:
        purchases = c.execute(
            "SELECT bi.category_id, "
            "CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END AS qty, "
            "bi.price, COALESCE(b.bill_date, b.created_at) AS event_ts, "
            "b.id AS bill_id, bi.id AS bill_item_id "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            "WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.category_id IS NOT NULL AND bi.price > 0 AND bi.qty > 0 "
            "ORDER BY event_ts, b.id, bi.id"
        ).fetchall()
        sales = c.execute(
            "SELECT si.id AS sale_item_id, si.sale_id, si.category_id, si.qty, "
            "s.created_at, s.payment_status "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE si.category_id IS NOT NULL AND si.qty > 0 "
            f"AND {db.VALID_SALE_FILTER} "
            "ORDER BY s.created_at, si.id"
        ).fetchall()
        adjustments = c.execute(
            "SELECT category_id, delta, created_at, id "
            "FROM stock_adjustments WHERE category_id IS NOT NULL "
            "ORDER BY created_at, id"
        ).fetchall()
        before_state = {}
        for r in c.execute("SELECT * FROM category_stock_state").fetchall():
            before_state[r["category_id"]] = {
                "qty": float(r["current_qty"] or 0),
                "value": float(r["current_value"] or 0),
                "avg_cost": float(r["current_avg_cost"] or 0),
            }
        # v8.18.17: bag sale events are excluded from the replay entirely —
        # bag categories follow the max(purchased, sold) rule (see the bags
        # block comment above), and skipping the events also preserves the
        # imported INVTRANS.COST in sale_items.cost_price (a sale-event
        # replay would overwrite it with the bag pool's avg — usually 0).
        bag_ids = bag_category_ids(c)

    events = []
    for p in purchases:
        # NOTE: bag PURCHASES (if the user ever uploads bag bills) DO replay —
        # they are the "purchased" side of the max(purchased, sold) rule.
        # Only bag SALE events are excluded (see bag_ids note above).
        events.append({"ts": p["event_ts"] or "", "seq": 0, "category_id": p["category_id"],
                       "qty": float(p["qty"]), "unit_price": float(p["price"]),
                       "sale_item_id": None, "type": "purchase"})
    for a in adjustments:
        events.append({"ts": a["created_at"] or "", "seq": 1, "category_id": a["category_id"],
                       "qty": float(a["delta"]), "unit_price": None,
                       "sale_item_id": None, "type": "adjustment"})
    for s in sales:
        if s["category_id"] in bag_ids:
            continue  # v8.18.17: bag sales never decrement the pool (unified model)
        events.append({"ts": s["created_at"] or "", "seq": 2, "category_id": s["category_id"],
                       "qty": float(s["qty"]), "unit_price": None,
                       "sale_item_id": s["sale_item_id"], "type": "sale"})
    events.sort(key=lambda e: (e["ts"], e["seq"], e.get("sale_item_id") or 0))

    pools = {}
    new_cost_prices = {}
    for ev in events:
        cid = ev["category_id"]
        if cid not in pools:
            pools[cid] = {"qty": 0.0, "value": 0.0, "avg": 0.0}
        pool = pools[cid]
        if ev["type"] == "purchase":
            new_qty = pool["qty"] + ev["qty"]
            new_value = pool["value"] + ev["qty"] * ev["unit_price"]
            new_avg = (new_value / new_qty) if new_qty > 0 else 0.0
            pool["qty"] = new_qty; pool["value"] = new_value; pool["avg"] = round(new_avg, 2)
        elif ev["type"] == "adjustment":
            delta = ev["qty"]
            if delta < 0:
                value_change = round(abs(delta) * pool["avg"], 2)
                pool["qty"] += delta; pool["value"] -= value_change
            else:
                value_change = round(delta * pool["avg"], 2)
                pool["qty"] += delta; pool["value"] += value_change
                if pool["qty"] > 0:
                    pool["avg"] = round(pool["value"] / pool["qty"], 2)
        elif ev["type"] == "sale":
            cogs = round(ev["qty"] * pool["avg"], 2)
            pool["qty"] -= ev["qty"]; pool["value"] -= cogs
            new_cost_prices[ev["sale_item_id"]] = pool["avg"]

    # v8.13.5 (H8 fix): Atomic rebuild via single write_tx.
    # Old code used conn() — a crash mid-INSERT left category_stock_state
    # half-empty (DELETE succeeded but not all INSERTs ran), and downstream
    # dashboards/COGS reports silently read wrong numbers until someone
    # manually re-ran rebuild. Single write_tx (BEGIN IMMEDIATE … COMMIT)
    # means SQLite rolls back the DELETE if any INSERT/UPDATE fails — the
    # table is never observed in a half-rebuilt state by other connections.
    rewrote_sales = 0
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.write_tx() as c:
        c.execute("DELETE FROM category_stock_state")
        for cid, pool in pools.items():
            _save_state(c, cid, pool["qty"], round(pool["value"], 2), pool["avg"],
                        last_txn_at=now_ts)
        for sale_item_id, new_cost in new_cost_prices.items():
            c.execute("UPDATE sale_items SET cost_price=? WHERE id=?", (new_cost, sale_item_id))
            rewrote_sales += 1
        # v8.19.1: bags rule — bag purchases are EXPENSES (never entered as
        # bills), so a pure replay leaves bag categories at −sold. Raise each
        # bag category's qty to its total sold inside the same transaction.
        bags_raised = sync_bags_stock_to_sold(c)

    categories = []
    for cid, pool in pools.items():
        categories.append({
            "category_id": cid, "qty": round(pool["qty"], 2),
            "value": round(pool["value"], 2), "avg_cost": round(pool["avg"], 2),
            "before": before_state.get(cid, {"qty": 0, "value": 0, "avg_cost": 0}),
            "after": {"qty": round(pool["qty"], 2), "value": round(pool["value"], 2),
                      "avg_cost": round(pool["avg"], 2)},
        })
    log_activity("rebuild_stock_state", "inventory", None,
                 f"Rebuilt stock state: {len(categories)} categories, rewrote {rewrote_sales} sale_items"
                 + (f", raised {len(bags_raised)} bag category stock(s) to sold" if bags_raised else ""),
                 {"categories": len(categories), "rewrote_sales": rewrote_sales,
                  "bags_raised": bags_raised})
    # PR 8: record last-rebuilt timestamp + clear dirty flag.
    # Both writes are safe to skip if they fail — the rebuild itself already
    # committed via the conn() above; these are just metadata for /api/health.
    try:
        from datetime import datetime as _dt
        with conn() as _c:
            _c.execute(
                "INSERT INTO settings(key, value) VALUES('stock_state_last_rebuilt_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_dt.now().strftime("%Y-%m-%d %H:%M:%S"),),
            )
            # Clear the dirty flag here too — main.py also clears it post-rebuild,
            # but if rebuild is called from anywhere else (e.g., POST /api/inventory/rebuild),
            # the flag should be cleared consistently.
            _c.execute(
                "INSERT INTO settings(key, value) VALUES('stock_state_dirty', 'false') "
                "ON CONFLICT(key) DO UPDATE SET value='false'"
            )
    except Exception:
        pass  # don't fail the rebuild over a metadata write
    return {"categories": categories, "rewrote_sales": rewrote_sales,
            "bags_raised": bags_raised}


def rebuild_categories_state(category_ids, *, c=None) -> dict:
    """v8.18.16: scoped stock-state replay for specific categories.

    Recomputes (qty, value, avg) for ONLY the given categories from the
    same authoritative event sources as the full rebuild_stock_state()
    — confirmed non-deleted bills, valid (non-refunded) sales, stock
    adjustments — using identical replay semantics and event ordering,
    then writes the result. Historical sale_items.cost_price is NOT
    rewritten here (that stays a full-rebuild responsibility; set the
    stock_state_dirty flag if you want the next boot to re-derive it).

    WHY: the incremental _reverse/_apply mirrors are exact only while the
    pool stays positive. When a deleted bill's stock was already
    partially sold, qty can pass <= 0 mid-reversal, the value clamp
    zeroes pool value, and the matching restore then re-applies the full
    original value — leaving the running avg cost permanently inflated
    vs what a full rebuild would compute. Scoped replay after every
    bill delete/restore makes the live state EXACTLY equal to a full
    rebuild at all times (drift becomes impossible), and the
    delete → restore round trip returns to the exact pre-delete state.

    If `c` is provided, runs inside the caller's write_tx and does NOT
    commit; otherwise opens its own.
    Returns {category_id: {"qty","value","avg"}} for the replayed set.
    """
    ids = sorted({int(x) for x in (category_ids or []) if x is not None})
    if not ids:
        return {}

    def _run(c):
        placeholders = ",".join("?" * len(ids))
        purchases = c.execute(
            "SELECT bi.category_id, "
            "CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END AS qty, "
            "bi.price, COALESCE(b.bill_date, b.created_at) AS event_ts, "
            "b.id AS bill_id, bi.id AS bill_item_id "
            "FROM bill_items bi JOIN bills b ON bi.bill_id = b.id "
            f"WHERE b.status='confirmed' AND b.deleted_at IS NULL "
            "AND bi.category_id IS NOT NULL AND bi.price > 0 AND bi.qty > 0 "
            f"AND bi.category_id IN ({placeholders}) "
            "ORDER BY event_ts, b.id, bi.id",
            tuple(ids),
        ).fetchall()
        sales = c.execute(
            "SELECT si.id AS sale_item_id, si.category_id, si.qty, s.created_at "
            "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
            "WHERE si.category_id IS NOT NULL AND si.qty > 0 "
            f"AND {db.VALID_SALE_FILTER} AND si.category_id IN ({placeholders}) "
            "ORDER BY s.created_at, si.id",
            tuple(ids),
        ).fetchall()
        adjustments = c.execute(
            "SELECT category_id, delta, created_at, id "
            f"FROM stock_adjustments WHERE category_id IN ({placeholders}) "
            "ORDER BY created_at, id",
            tuple(ids),
        ).fetchall()
        # v8.18.17: bag sale events never decrement the pool (unified bags
        # model — max(purchased, sold); see the block comment above). Only
        # the requested categories are checked, and purchases/adjustments
        # still replay normally for bags.
        bag_ids = bag_category_ids(c) & set(ids)
        events = []
        for p in purchases:
            events.append({"ts": p["event_ts"] or "", "seq": 0, "tie": p["bill_item_id"] or 0,
                           "category_id": p["category_id"],
                           "qty": float(p["qty"]),
                           "unit_price": float(p["price"]), "type": "purchase"})
        for a in adjustments:
            events.append({"ts": a["created_at"] or "", "seq": 1, "tie": a["id"] or 0,
                           "category_id": a["category_id"],
                           "qty": float(a["delta"]), "unit_price": None,
                           "type": "adjustment"})
        for s in sales:
            if s["category_id"] in bag_ids:
                continue  # v8.18.17: bag sales never decrement the pool (unified model)
            events.append({"ts": s["created_at"] or "", "seq": 2, "tie": s["sale_item_id"] or 0,
                           "category_id": s["category_id"],
                           "qty": float(s["qty"]), "unit_price": None,
                           "type": "sale"})
        # identical ordering to the full rebuild: (ts, type, id)
        events.sort(key=lambda e: (e["ts"], e["seq"], e["tie"]))
        pools = {}
        for ev in events:
            cid = ev["category_id"]
            if cid not in pools:
                pools[cid] = {"qty": 0.0, "value": 0.0, "avg": 0.0}
            pool = pools[cid]
            if ev["type"] == "purchase":
                new_qty = pool["qty"] + ev["qty"]
                new_value = pool["value"] + ev["qty"] * ev["unit_price"]
                new_avg = (new_value / new_qty) if new_qty > 0 else 0.0
                pool["qty"] = new_qty; pool["value"] = new_value
                pool["avg"] = round(new_avg, 2)
            elif ev["type"] == "adjustment":
                delta = ev["qty"]
                if delta < 0:
                    pool["qty"] += delta
                    pool["value"] -= round(abs(delta) * pool["avg"], 2)
                else:
                    pool["qty"] += delta
                    pool["value"] += round(delta * pool["avg"], 2)
                    if pool["qty"] > 0:
                        pool["avg"] = round(pool["value"] / pool["qty"], 2)
            elif ev["type"] == "sale":
                cogs = round(ev["qty"] * pool["avg"], 2)
                pool["qty"] -= ev["qty"]
                pool["value"] -= cogs
        now_ts = datetime.now().strftime("%Y-%m %H:%M:%S")
        out = {}
        for cid in ids:
            pool = pools.get(cid, {"qty": 0.0, "value": 0.0, "avg": 0.0})
            _save_state(c, cid, pool["qty"], round(pool["value"], 2),
                        pool["avg"], last_txn_at=now_ts)
            out[cid] = {"qty": round(pool["qty"], 2),
                        "value": round(pool["value"], 2),
                        "avg": round(pool["avg"], 2)}
        # bags rule parity with full rebuild (idempotent no-op for
        # already-synced categories)
        sync_bags_stock_to_sold(c)
        return out

    if c is not None:
        return _run(c)
    with db.write_tx() as own:
        return _run(own)


# ─── Read-only helpers ──────────────────────────────────────────────────────

def get_category_stock_state(category_id: int = None) -> list:
    """Read the materialized state."""
    with conn() as c:
        if category_id is not None:
            rows = c.execute("SELECT * FROM category_stock_state WHERE category_id=?", (category_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM category_stock_state ORDER BY category_id").fetchall()
    return [dict(r) for r in rows]


def _get_setting(key: str, default: str = "") -> str:
    """Read a setting value (shared helper for analytics + cash modules)."""
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default
