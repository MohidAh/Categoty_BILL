"""Phase 0 v8.7 — Change 4: New reports (Profit Analysis + Sold Stock).

Verifies the new endpoints return correct aggregates + exclude refunded sales.
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest
from test_helpers import setup_test_db, cleanup

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v87r_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log",
                  "category_stock_state", "expenses"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_profit_analysis_by_category_returns_correct_totals():
    test_dir = setup_test_db()
    try:
        from app.reports import profit_analysis_report
        # Sample data is dated 2026-08-11 — use a wide range
        r = profit_analysis_report("2026-08-01", "2026-08-31", group_by="category")
        assert "categories" in r
        assert len(r["categories"]) > 0
        # Verify totals
        t = r["totals"]
        # v8.16.13+: revenue allocates sales.total (what the customer paid),
        # not raw item sums. Sale 2's total is 5,500 while its items sum to
        # 6,500 (the sample data intentionally carries this inconsistency to
        # prove allocation is active): revenue = 10,150 + 5,500 = 15,650.
        assert abs(t["revenue"] - 15650) < 0.01, f"Expected 15650, got {t['revenue']}"
        # COGS uses cost at sale time; rebuild_stock_state() rewrites D's
        # sale cost to its purchase avg (150, from bill 3) — historical
        # weighted-average basis: 880 + 2600 + 2400 + 1200 = 7,080
        assert abs(t["cogs"] - 7080) < 0.01, f"Expected 7080, got {t['cogs']}"
        # Gross profit
        assert abs(t["gross_profit"] - 8570) < 0.01, f"Expected 8570, got {t['gross_profit']}"
    finally:
        cleanup(test_dir)


def test_profit_analysis_excludes_refunded_sales():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.reports import profit_analysis_report
        # Refund sale 1
        with db.conn() as c:
            c.execute("UPDATE sales SET payment_status='refunded' WHERE id=1")
        r = profit_analysis_report("2026-08-01", "2026-08-31", group_by="category")
        t = r["totals"]
        # Only sale 2 remains. v8.16.13+: its items sum to 6,500 but its
        # sales.total is 5,500 — proportional allocation scales category
        # revenue down so the TOTAL equals what the customer actually
        # paid: 5,500 (not the raw 6,500 of the v8.7-era math).
        assert abs(t["revenue"] - 5500) < 0.01, (
            f"After refunding sale 1, revenue should be 5500, got {t['revenue']}"
        )
    finally:
        cleanup(test_dir)


def test_profit_analysis_by_month():
    test_dir = setup_test_db()
    try:
        from app.reports import profit_analysis_report
        r = profit_analysis_report("2026-08-01", "2026-08-31", group_by="month")
        assert "months" in r
        assert len(r["months"]) == 1
        assert r["months"][0]["month"] == "2026-08"
        # v8.18.16: month view derives revenue from sales.total (10,150 +
        # 5,500), matching the category view of the same report and P&L —
        # previously it summed raw item lines (16,650) and disagreed.
        assert abs(r["months"][0]["revenue"] - 15650) < 0.01
        # month view and category view of the SAME report must agree
        rc = profit_analysis_report("2026-08-01", "2026-08-31", group_by="category")
        assert abs(r["months"][0]["revenue"] - rc["totals"]["revenue"]) < 0.01
        assert abs(r["months"][0]["cogs"] - rc["totals"]["cogs"]) < 0.01
        assert abs(r["months"][0]["gross_profit"] - rc["totals"]["gross_profit"]) < 0.01
    finally:
        cleanup(test_dir)


def test_sold_stock_by_category_default():
    test_dir = setup_test_db()
    try:
        from app.reports import sold_stock_report
        # DEFAULT group_by='category'
        r = sold_stock_report("2026-08-01", "2026-08-31")
        assert r["group_by"] == "category"
        assert "categories" in r
        # Should have categories A, B, C, D
        assert len(r["categories"]) >= 4
        t = r["totals"]
        assert abs(t["revenue"] - 16650) < 0.01
        assert abs(t["cogs"] - 7080) < 0.01
    finally:
        cleanup(test_dir)


def test_sold_stock_by_item():
    test_dir = setup_test_db()
    try:
        from app.reports import sold_stock_report
        r = sold_stock_report("2026-08-01", "2026-08-31", group_by="item")
        assert "items" in r
        assert len(r["items"]) > 0
        # Each item should have qty_sold, revenue, cogs, gross_profit, margin_pct
        it = r["items"][0]
        for k in ("item_name", "qty_sold", "revenue", "cogs", "gross_profit", "margin_pct"):
            assert k in it, f"Missing {k}"
        t = r["totals"]
        assert abs(t["revenue"] - 16650) < 0.01
    finally:
        cleanup(test_dir)


def test_sold_stock_excludes_refunded_sales():
    test_dir = setup_test_db()
    try:
        from app import db
        from app.reports import sold_stock_report
        with db.conn() as c:
            c.execute("UPDATE sales SET payment_status='refunded' WHERE id=1")
        r = sold_stock_report("2026-08-01", "2026-08-31", group_by="category")
        t = r["totals"]
        # Only sale 2 remains
        assert abs(t["revenue"] - 6500) < 0.01
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    import traceback
    tests = [
        test_profit_analysis_by_category_returns_correct_totals,
        test_profit_analysis_excludes_refunded_sales,
        test_profit_analysis_by_month,
        test_sold_stock_by_category_default,
        test_sold_stock_by_item,
        test_sold_stock_excludes_refunded_sales,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  [PASS] {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
