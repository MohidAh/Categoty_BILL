"""v8.2 Phase 5 — Bill Intelligence: sell-through check on bill confirm.

On bill confirm, for each category line: find the last confirmed purchase,
compute sold_since and sell_through_pct. Tiered verdict + soft pause.
"""
import logging
from datetime import datetime
from .db import conn, log_activity

logger = logging.getLogger(__name__)


def compute_bill_intelligence(bill_id: int) -> list:
    """Compute sell-through intelligence for each category in a bill.

    For each category line in the bill:
    1. Find the last confirmed purchase of that category BEFORE this bill
    2. Compute sold_since = units sold since that purchase date
    3. sell_through_pct = sold_since / last_purchase_qty * 100
    4. Tiered verdict: >=80% green, 40-80% info, <40% red

    Returns a list of intelligence records.
    """
    results = []
    with conn() as c:
        # Get the bill's items
        bill_items = c.execute(
            "SELECT bi.*, b.bill_date, b.supplier_name FROM bill_items bi "
            "JOIN bills b ON bi.bill_id=b.id WHERE bi.bill_id=? AND b.deleted_at IS NULL",
            (bill_id,)
        ).fetchall()
        if not bill_items:
            return []
        bill_date = bill_items[0]["bill_date"] if bill_items else datetime.now().strftime("%Y-%m-%d")
        for bi in bill_items:
            cat_id = bi["category_id"]
            if not cat_id:
                continue
            # Find the last confirmed purchase of this category BEFORE this bill
            # (exclude the current bill)
            last_purchase = c.execute(
                "SELECT bi2.qty, bi2.unit, bi2.price, b2.bill_date, b2.supplier_name "
                "FROM bill_items bi2 JOIN bills b2 ON bi2.bill_id=b2.id "
                "WHERE bi2.category_id=? AND b2.status='confirmed' AND b2.deleted_at IS NULL "
                "AND b2.id != ? AND b2.bill_date <= ? "
                "ORDER BY b2.bill_date DESC, b2.id DESC LIMIT 1",
                (cat_id, bill_id, bill_date)
            ).fetchone()
            if not last_purchase:
                # First-ever purchase — skip
                results.append({
                    "bill_id": bill_id, "category_id": cat_id,
                    "last_purchase_qty": None, "last_purchase_date": None,
                    "sold_since": None, "sell_through_pct": None,
                    "verdict": "first_purchase", "acknowledged": 1,
                })
                continue
            # Compute pieces from the last purchase
            last_qty = float(last_purchase["qty"] or 0)
            if last_purchase["unit"] == "dozen":
                last_qty *= 12
            last_date = last_purchase["bill_date"]
            # Compute sold_since: units sold since last_date
            sold_row = c.execute(
                "SELECT COALESCE(SUM(si.qty), 0) AS v FROM sale_items si "
                "JOIN sales s ON si.sale_id=s.id "
                "WHERE si.category_id=? AND s.payment_status IN ('paid', 'credit', 'partial') "
                "AND date(s.created_at) >= date(?)",
                (cat_id, last_date)
            ).fetchone()
            sold_since = float(sold_row["v"] or 0)
            sell_through = (sold_since / last_qty * 100) if last_qty > 0 else 0
            # Tiered verdict
            if sell_through >= 80:
                verdict = "well_timed"
            elif sell_through >= 40:
                verdict = "partial"
            else:
                verdict = "overstock_risk"
            # Check if this category was already acknowledged for a previous overstock
            existing_ack = c.execute(
                "SELECT acknowledged, ack_reason FROM bill_intelligence "
                "WHERE category_id=? AND verdict='overstock_risk' AND acknowledged=1 "
                "ORDER BY id DESC LIMIT 1",
                (cat_id,)
            ).fetchone()
            is_acknowledged = 1 if existing_ack else 0
            ack_reason = existing_ack["ack_reason"] if existing_ack else ""
            # v8.4: Delete existing intelligence for this bill+category before inserting
            # to prevent duplicates if compute_bill_intelligence is called multiple times
            c.execute(
                "DELETE FROM bill_intelligence WHERE bill_id=? AND category_id=?",
                (bill_id, cat_id)
            )
            # Store the intelligence record
            c.execute(
                "INSERT INTO bill_intelligence(bill_id, category_id, last_purchase_qty, "
                "last_purchase_date, sold_since, sell_through_pct, verdict, acknowledged, ack_reason) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (bill_id, cat_id, last_qty, last_date, sold_since,
                 round(sell_through, 1), verdict, is_acknowledged, ack_reason)
            )
            results.append({
                "bill_id": bill_id, "category_id": cat_id,
                "last_purchase_qty": last_qty, "last_purchase_date": last_date,
                "sold_since": sold_since, "sell_through_pct": round(sell_through, 1),
                "verdict": verdict, "acknowledged": is_acknowledged,
                "ack_reason": ack_reason,
            })
    return results


def get_bill_intelligence(bill_id: int) -> list:
    """Get stored intelligence for a bill."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.*, pc.code, pc.name FROM bill_intelligence bi "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE bi.bill_id=? ORDER BY bi.id",
            (bill_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_overstock_categories_for_bill(bill_id: int) -> list:
    """Get categories in a bill that have overstock_risk verdict and are NOT acknowledged."""
    with conn() as c:
        rows = c.execute(
            "SELECT bi.*, pc.code, pc.name FROM bill_intelligence bi "
            "LEFT JOIN price_categories pc ON bi.category_id = pc.id "
            "WHERE bi.bill_id=? AND bi.verdict='overstock_risk' AND bi.acknowledged=0",
            (bill_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def acknowledge_bill_intelligence(bill_id: int, category_id: int, reason: str = "") -> bool:
    """Acknowledge an overstock finding for a bill+category."""
    with conn() as c:
        cur = c.execute(
            "UPDATE bill_intelligence SET acknowledged=1, ack_reason=? "
            "WHERE bill_id=? AND category_id=? AND verdict='overstock_risk'",
            (reason, bill_id, category_id)
        )
        # Also acknowledge future overstock findings for this category
        if cur.rowcount > 0:
            c.execute(
                "UPDATE bill_intelligence SET acknowledged=1, ack_reason=? "
                "WHERE category_id=? AND verdict='overstock_risk'",
                (reason, category_id)
            )
    return cur.rowcount > 0
