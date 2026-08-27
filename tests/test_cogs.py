"""v4.0 Phase 1 — COGS Integrity tests.

Verifies:
  1. shop.get_category_avg_cost() returns weighted avg (not unweighted AVG) with
     dozen conversion.
  2. Categories with no cost history return 0.0 AND log an activity warning.
  3. create_sale() populates sale_items.cost_price from the category avg at sale
     time, without requiring the frontend to send cost_price.
  4. POST /api/maintenance/recalc-cogs backfills zero-cost rows idempotently.
  5. P&L arithmetic: net_profit == net_revenue - cost_of_goods - expenses.

Run: .venv/bin/python -m pytest tests/test_cogs.py -v
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from test_helpers import setup_test_db as _setup_test_db, cleanup

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_SQL = Path(__file__).parent / "sample_data.sql"


# ───────────────────────── helpers ─────────────────────────

def setup_test_db():
    """Fresh temp DB with the canonical sample data loaded."""
    test_dir = tempfile.mkdtemp(prefix="billbook_cogs_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    # Phase 0 PR 3: create_sale()'s new stock guard reads from
    # category_stock_state (the v8.5 source of truth). This test file
    # intentionally does NOT call rebuild_stock_state() because it asserts
    # specific cost_price values that rebuild would overwrite (the sample data
    # has D cost_price=201.25 while the running avg from bills is 150).
    # Use permit_negative strategy so the stock guard is skipped entirely —
    # these tests are about cost_price population, not stock enforcement.
    db.set_setting("stock_strategy", "permit_negative")
    return test_dir



def test_weighted_avg_single_bill():
    """Category A: one bill, 50 pcs @ 80 → avg = 80."""
    test_dir = setup_test_db()
    try:
        from app import shop
        avg = shop.get_category_avg_cost(1)
        assert avg == 80.0, f"Expected 80.0, got {avg}"
    finally:
        cleanup(test_dir)


def test_weighted_avg_multi_bill():
    """Category C: two bills.
       Bill 2: 5 pcs @ 300 = 1500
       Bill 3: 5 pcs @ 300 = 1500
       Weighted avg = (1500+1500)/(5+5) = 300.
    """
    test_dir = setup_test_db()
    try:
        from app import shop
        avg = shop.get_category_avg_cost(3)
        assert avg == 300.0, f"Expected 300.0, got {avg}"
    finally:
        cleanup(test_dir)


def test_weighted_avg_actually_weights():
    """Verify the avg is weighted (not a simple AVG of price rows).

    Add a second bill for category A with a different unit price & qty, then
    confirm the avg moves toward the higher-qty line.
    """
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Category A baseline: 50 pcs @ 80 (cost 4000)
        # Add a new confirmed bill: 10 pcs @ 100 (cost 1000)
        with db.conn() as c:
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, "
                "bill_no, written_total, computed_total, status, payment_status, created_at) "
                "VALUES(99, 1, 'ABC Trading', '2026-08-10', 'B099', 1000, 1000, 'confirmed', 'paid', '2026-08-10 13:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(99, 1, 'Bulk A', 'A', 100, 10, 'pcs', 1000, 1)"
            )
        # Weighted: (4000 + 1000) / (50 + 10) = 5000/60 = 83.33
        avg = shop.get_category_avg_cost(1)
        assert abs(avg - 83.33) < 0.01, f"Expected 83.33, got {avg}"
        # Sanity: simple AVG would have been (80+100)/2 = 90 — confirm we're NOT that
        assert avg != 90.0, "Weighted avg is matching simple AVG — weighting broken"
    finally:
        cleanup(test_dir)


def test_weighted_avg_dozen_conversion():
    """A dozen-quantity line contributes 12x pieces to the weighted avg."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Add a 1-dozen bill for category A at price 60/piece
        # (line_total = 60 * 12 = 720)
        with db.conn() as c:
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, "
                "bill_no, written_total, computed_total, status, payment_status, created_at) "
                "VALUES(98, 1, 'ABC Trading', '2026-08-10', 'B098', 720, 720, 'confirmed', 'paid', '2026-08-10 14:00:00')"
            )
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) "
                "VALUES(98, 1, 'Dozen A', 'A', 60, 1, 'dozen', 720, 1)"
            )
        # Category A now: 50 pcs @ 80 (4000) + 12 pcs @ 60 (720) = 4720 / 62 = 76.13
        avg = shop.get_category_avg_cost(1)
        assert abs(avg - 76.13) < 0.01, f"Expected 76.13 with dozen conversion, got {avg}"
    finally:
        cleanup(test_dir)


def test_no_cost_history_returns_zero_and_logs():
    """Category with no confirmed bills → 0.0; log_cogs_warning writes the audit entry."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Create a fresh category with no bills
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(99, 'NoHistory', 'Z', 100, '#999999', 99, 1)"
            )
        avg = shop.get_category_avg_cost(99)
        assert avg == 0.0, f"Expected 0.0 for no-history category, got {avg}"
        # get_category_avg_cost itself does NOT log (to avoid deadlocks when called
        # from inside a write transaction). Callers log via log_cogs_warning after commit.
        with db.conn() as c:
            before = c.execute("SELECT COUNT(*) n FROM activity_log WHERE event_type='cogs_warning'").fetchone()["n"]
        shop.log_cogs_warning(99, sale_id=1234, invoice_no="INV-TEST")
        with db.conn() as c:
            after = c.execute("SELECT COUNT(*) n FROM activity_log WHERE event_type='cogs_warning'").fetchone()["n"]
            last = c.execute(
                "SELECT * FROM activity_log WHERE event_type='cogs_warning' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert after == before + 1, f"Warning not logged: before={before}, after={after}"
        assert last["entity_id"] == 99, f"Warning entity_id mismatch: {last['entity_id']}"
        assert "INV-TEST" in last["description"], f"Invoice no missing from description: {last['description']}"
    finally:
        cleanup(test_dir)


def test_create_sale_populates_cost_price():
    """A sale created via the API auto-fills cost_price from category avg."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        # Category A: 50 purchased, 11 already sold in sample data → 39 available
        payload = SaleIn(
            customer_name="COGS Test Customer",
            items=[SaleItemIn(category_id=1, category_code="A", sell_price=250, qty=1)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result, f"create_sale failed: {result}"
        sale_id = result["id"]
        with db.conn() as c:
            row = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=? AND category_id=1",
                (sale_id,),
            ).fetchone()
        assert row and row["cost_price"] == 80.0, \
            f"Expected cost_price=80.0 (category A avg), got {row['cost_price'] if row else 'no row'}"
    finally:
        cleanup(test_dir)


def test_create_sale_no_cost_history():
    """Sale for a category with no cost history → cost_price=0, no crash."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.pos import SaleIn, SaleItemIn, create_sale
        # Create category 99 + add a stock adjustment so the stock guard passes
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(id, name, code, sell_price, color, sort_order, active) "
                "VALUES(99, 'NoHistory', 'Z', 100, '#999999', 99, 1)"
            )
            c.execute(
                "INSERT INTO stock_adjustments(category_id, delta, reason) VALUES(99, 5, 'test seed')"
            )
        payload = SaleIn(
            customer_name="No-Cost Test",
            items=[SaleItemIn(category_id=99, category_code="Z", sell_price=100, qty=1)],
            payment_method="cash",
        )
        result = create_sale(payload)
        assert "id" in result, f"create_sale failed for no-cost category: {result}"
        sale_id = result["id"]
        with db.conn() as c:
            row = c.execute(
                "SELECT cost_price FROM sale_items WHERE sale_id=? AND category_id=99",
                (sale_id,),
            ).fetchone()
            warn = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='cogs_warning' AND entity_id=99"
            ).fetchone()["n"]
        assert row and row["cost_price"] == 0.0, \
            f"Expected cost_price=0 for no-history category, got {row['cost_price'] if row else 'no row'}"
        assert warn >= 1, "Expected at least one cogs_warning activity entry"
    finally:
        cleanup(test_dir)


def test_recalc_cogs_backfills_zero_rows():
    """recalc_cogs updates sale_items where cost_price=0 using current avg."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.settings import recalc_cogs
        # Force one sale_item to cost_price=0 (simulate a legacy zero-cost sale)
        with db.conn() as c:
            c.execute("UPDATE sale_items SET cost_price=0 WHERE id=1")
            before = c.execute("SELECT cost_price FROM sale_items WHERE id=1").fetchone()["cost_price"]
        assert before == 0, "Setup failed: couldn't zero out cost_price"
        result = recalc_cogs()
        assert result["ok"] is True
        assert result["affected"] >= 1, f"Expected ≥1 affected, got {result['affected']}"
        # Category 1's avg cost is 80 → the row should now be 80
        with db.conn() as c:
            after = c.execute("SELECT cost_price FROM sale_items WHERE id=1").fetchone()["cost_price"]
        assert after == 80.0, f"Expected cost_price=80 after recalc, got {after}"
        # Idempotent: run again → 0 affected (no zero rows left)
        result2 = recalc_cogs()
        assert result2["affected"] == 0, f"Expected 0 on second run, got {result2['affected']}"
    finally:
        cleanup(test_dir)


def test_pnl_arithmetic():
    """P&L: net_profit == net_revenue - cost_of_goods - expenses."""
    test_dir = setup_test_db()
    try:
        from app import shop
        pnl = shop.get_pnl("2026-08")
        # Verify the identity the spec calls out
        identity = pnl["net_revenue"] - pnl["cost_of_goods"] - pnl["expenses"]
        assert abs(identity - pnl["net_profit"]) < 0.01, \
            f"P&L identity broken: revenue({pnl['net_revenue']}) - cogs({pnl['cost_of_goods']}) " \
            f"- exp({pnl['expenses']}) = {identity}, but net_profit={pnl['net_profit']}"
        # Verify COGS is non-zero (sample data has cost_price populated)
        assert pnl["cost_of_goods"] > 0, f"COGS should be > 0 with sample data, got {pnl['cost_of_goods']}"
        # Verify the known sample-data total: 7,490
        assert abs(pnl["cost_of_goods"] - 7490) < 0.01, \
            f"COGS should match sample invariant 7,490, got {pnl['cost_of_goods']}"
    finally:
        cleanup(test_dir)


def test_recalc_cogs_logs_activity():
    """recalc_cogs writes an activity_log entry describing what it did."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.settings import recalc_cogs
        with db.conn() as c:
            c.execute("UPDATE sale_items SET cost_price=0 WHERE id=1")
            before = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='recalc_cogs'"
            ).fetchone()["n"]
        recalc_cogs()
        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) n FROM activity_log WHERE event_type='recalc_cogs'"
            ).fetchone()["n"]
            entry = c.execute(
                "SELECT * FROM activity_log WHERE event_type='recalc_cogs' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert after == before + 1, "recalc_cogs activity not logged"
        assert entry and "Recalculated COGS" in entry["description"], \
            f"Activity description wrong: {entry['description'] if entry else 'no row'}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_weighted_avg_single_bill()
    test_weighted_avg_multi_bill()
    test_weighted_avg_actually_weights()
    test_weighted_avg_dozen_conversion()
    test_no_cost_history_returns_zero_and_logs()
    test_create_sale_populates_cost_price()
    test_create_sale_no_cost_history()
    test_recalc_cogs_backfills_zero_rows()
    test_pnl_arithmetic()
    test_recalc_cogs_logs_activity()
    print("\n✅ ALL PHASE 1 COGS TESTS PASSED")
