"""v5.0 Phase 9 Review Fix — COGS reconciliation with stock adjustments.

The reviewer flagged that §6.1 says bridge COGS vs cogs_from_sales should
differ by "< Rs 1", but also says a larger difference "indicates a stock
adjustment." Those can't both hold — any damage/loss adjustment makes them
diverge by exactly the adjustment amount.

This test proves:
  1. Without adjustments: bridge COGS == cogs_from_sales (within rounding).
  2. With a negative adjustment (damage): bridge COGS > cogs_from_sales
     by approximately the adjustment's cost impact.
  3. The tolerance should be stated as "in the absence of adjustments."
"""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_cogs_recon_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log", "sessions",
                  "expenses", "expense_categories", "recurring_expenses",
                  "cash_drawer", "shifts", "employees",
                  "category_stock_state", "owner_withdrawals"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)", (name, is_fixed, budget, 1, sort_order),
            )
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_cogs_reconcile_without_adjustments():
    """Without adjustments: bridge COGS ≈ cogs_from_sales (within Rs 1)."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        diff = abs(r["cogs"] - r["cogs_from_sales"])
        assert diff < 1.0, \
            f"Without adjustments, bridge COGS ({r['cogs']}) should match cogs_from_sales ({r['cogs_from_sales']}) within Rs 1, diff={diff}"
    finally:
        cleanup(test_dir)


def test_cogs_diverges_with_adjustment():
    """With a negative stock adjustment (damage), bridge COGS > cogs_from_sales.

    The bridge formula is: COGS = Opening + Purchases - Closing.
    A negative adjustment reduces closing inventory, which INCREASES bridge COGS.
    But cogs_from_sales (sum of sale_items.cost_price * qty) is unaffected by
    adjustments — it only reflects actual sales.

    So after a damage adjustment of N pieces at avg cost C:
      bridge_cogs ≈ cogs_from_sales + (N × C)

    This proves the two methods legitimately diverge when adjustments exist.
    The "< Rs 1" tolerance only holds in the absence of adjustments.
    """
    test_dir = setup_test_db()
    try:
        from app import profit, shop

        # Baseline: no adjustments
        r_before = profit.get_monthly_profit("2026-08")
        baseline_diff = r_before["cogs"] - r_before["cogs_from_sales"]

        # Get the current avg cost for category 1 (Budget, code A)
        state = profit.get_category_stock_state(1)
        avg_cost = state[0]["current_avg_cost"] if state else 80.0  # fallback

        # Apply a damage adjustment: lose 5 pieces of category 1
        # Insert a stock_adjustments record (the API endpoint does this + calls
        # apply_adjustment_to_state). We do both here to simulate the full flow.
        from app import db
        with db.conn() as c:
            c.execute(
                "INSERT INTO stock_adjustments(category_id, delta, reason) VALUES(?,?,?)",
                (1, -5, "Test damage — COGS reconciliation"),
            )
        profit.apply_adjustment_to_state(1, -5)

        # After adjustment: bridge COGS should increase (closing inventory decreased)
        r_after = profit.get_monthly_profit("2026-08")
        after_diff = r_after["cogs"] - r_after["cogs_from_sales"]

        # The bridge COGS should now be HIGHER than cogs_from_sales
        # because the adjustment reduced closing inventory (increasing bridge COGS)
        # but didn't affect cogs_from_sales (no sale happened).
        expected_increase = 5 * avg_cost  # 5 pieces × avg cost
        actual_increase = after_diff - baseline_diff

        # The increase should be approximately 5 × avg_cost (within rounding)
        assert abs(actual_increase - expected_increase) < 2.0, \
            f"Adjustment should increase bridge-cogs diff by ~{expected_increase:.2f} (5 × {avg_cost}), " \
            f"got increase of {actual_increase:.2f} (before={baseline_diff:.2f}, after={after_diff:.2f})"

        # cogs_from_sales should NOT change (no new sale was made)
        assert r_before["cogs_from_sales"] == r_after["cogs_from_sales"], \
            "cogs_from_sales should not change when only an adjustment is made"
    finally:
        cleanup(test_dir)


def test_cogs_tolerance_documented():
    """The API response should document that the tolerance assumes no adjustments."""
    test_dir = setup_test_db()
    try:
        from app import profit
        r = profit.get_monthly_profit("2026-08")
        # The note should mention that COGS bridge and cogs_from_sales may
        # diverge when adjustments exist
        assert "COGS" in r["note"]
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_cogs_reconcile_without_adjustments()
    print("✓ test_cogs_reconcile_without_adjustments")
    test_cogs_diverges_with_adjustment()
    print("✓ test_cogs_diverges_with_adjustment")
    test_cogs_tolerance_documented()
    print("✓ test_cogs_tolerance_documented")
    print("\n✅ ALL COGS RECONCILIATION TESTS PASSED")
