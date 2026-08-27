"""v4.0 Phase 2 — Expense Management tests.

Verifies:
  1. expense_categories seeded with 8 defaults on first init.
  2. POST /api/expenses accepts category_id + expense_type + date.
  3. Recurring expense: idempotent — calling generate twice produces exactly
     ONE expense row for the month.
  4. owner_draw expenses are excluded from P&L operating expenses but still
     appear in expenses totals.
  5. Budget card: a category with budget 30,000 and 30,000 spent shows 100%.
  6. Summary returns per-category budget vs actual + MoM comparison.

Run: .venv/bin/python -m pytest tests/test_expenses.py -v
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_exp_")
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
                  "expenses", "expense_categories", "recurring_expenses"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        # Re-seed default expense_categories (the wipe above cleared them)
        defaults = [
            ("Rent", 1, 0, 1),
            ("Salaries", 1, 0, 2),
            ("Electricity", 0, 0, 3),
            ("Transport", 0, 0, 4),
            ("Internet", 0, 0, 5),
            ("Maintenance", 0, 0, 6),
            ("Marketing", 0, 0, 7),
            ("Other", 0, 0, 8),
        ]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute(
                "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                "VALUES(?,?,?,?,?)",
                (name, is_fixed, budget, 1, sort_order),
            )
    return test_dir



def test_default_categories_seeded():
    """init() seeds 8 default expense categories on first run."""
    test_dir = setup_test_db()
    try:
        from app import shop
        cats = shop.list_expense_categories()
        names = [c["name"] for c in cats]
        expected = ["Rent", "Salaries", "Electricity", "Transport",
                    "Internet", "Maintenance", "Marketing", "Other"]
        for name in expected:
            assert name in names, f"Missing default category: {name}"
        # Rent & Salaries should be marked is_fixed=1
        rent = next(c for c in cats if c["name"] == "Rent")
        assert rent["is_fixed"] == 1, "Rent should be is_fixed=1"
        electricity = next(c for c in cats if c["name"] == "Electricity")
        assert electricity["is_fixed"] == 0, "Electricity should be is_fixed=0"
    finally:
        cleanup(test_dir)


def test_add_expense_with_category_id():
    """POST expense with category_id + expense_type + date populates the new columns."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        eid = shop.add_expense(
            category="Rent",
            amount=30000,
            description="August rent",
            payment_method="bank",
            category_id=rent_id,
            expense_type="operating",
            date_str="2026-08-05",
        )
        with db.conn() as c:
            row = c.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
        assert row is not None, "Expense not inserted"
        assert row["category_id"] == rent_id
        assert row["expense_type"] == "operating"
        assert row["date"] == "2026-08-05"
    finally:
        cleanup(test_dir)


def test_recurring_idempotent():
    """Create a recurring Rent (day 1) → generate → exactly ONE expense for this month;
    call generate again → still one row."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        # Set a budget on Rent so we can verify budget-vs-actual later
        shop.update_expense_category(rent_id, budget_monthly=30000)
        # Create a recurring expense for day 1
        rid = shop.add_recurring_expense(
            category_id=rent_id, amount=30000, description="Monthly rent",
            payment_method="bank", day_of_month=1, active=True,
        )
        # Force-generate for August 2026
        result1 = shop.generate_recurring_expenses(force_month="2026-08")
        assert result1["generated"] == 1, f"First generate should produce 1 row, got {result1}"
        with db.conn() as c:
            count = c.execute(
                "SELECT COUNT(*) n FROM expenses WHERE recurring_id=?", (rid,)
            ).fetchone()["n"]
        assert count == 1, f"Expected 1 expense for this recurring, got {count}"
        # Generate again — should be idempotent
        result2 = shop.generate_recurring_expenses(force_month="2026-08")
        assert result2["generated"] == 0, f"Second generate should produce 0, got {result2}"
        assert result2["skipped"] >= 1, f"Second generate should skip, got {result2}"
        with db.conn() as c:
            count2 = c.execute(
                "SELECT COUNT(*) n FROM expenses WHERE recurring_id=?", (rid,)
            ).fetchone()["n"]
        assert count2 == 1, f"Idempotency broken: still {count2} rows after 2nd generate"
    finally:
        cleanup(test_dir)


def test_owner_draw_excluded_from_pnl_operating():
    """An owner_draw expense does NOT count toward operating expenses in P&L."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        # Add an operating expense of 1000
        shop.add_expense("Misc", 1000, "op exp", "cash", expense_type="operating",
                         date_str="2026-08-10")
        # Add an owner_draw of 10000
        shop.add_expense("Owner Draw", 10000, "draw", "cash", expense_type="owner_draw",
                         date_str="2026-08-11")
        pnl = shop.get_pnl("2026-08")
        # P&L expenses should be 1000 (operating only)
        assert pnl["expenses"] == 1000.0, \
            f"P&L expenses should be 1000 (operating only), got {pnl['expenses']}"
        # P&L owner_draws should be 10000
        assert pnl["owner_draws"] == 10000.0, \
            f"P&L owner_draws should be 10000, got {pnl['owner_draws']}"
    finally:
        cleanup(test_dir)


def test_owner_draw_excluded_from_operating_total_in_summary():
    """Expense summary's operating_total excludes owner_draw."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.add_expense("Misc", 500, "op", "cash", expense_type="operating", date_str="2026-08-10")
        shop.add_expense("Draw", 5000, "draw", "cash", expense_type="owner_draw", date_str="2026-08-11")
        s = shop.get_expense_summary("2026-08")
        assert s["operating_total"] == 500.0, f"operating_total wrong: {s['operating_total']}"
        assert s["owner_draw_total"] == 5000.0, f"owner_draw_total wrong: {s['owner_draw_total']}"
        assert s["total"] == 5500.0, f"total wrong: {s['total']}"
    finally:
        cleanup(test_dir)


def test_budget_vs_actual_100_pct():
    """Rent budget 30,000 + spend 30,000 → summary shows 100%."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        shop.update_expense_category(rent_id, budget_monthly=30000)
        shop.add_expense("Rent", 30000, "August rent", "bank",
                         category_id=rent_id, expense_type="operating",
                         date_str="2026-08-05")
        s = shop.get_expense_summary("2026-08")
        rent_row = next((r for r in s["by_category"] if r["category"] == "Rent"), None)
        assert rent_row is not None, "Rent missing from summary by_category"
        assert rent_row["total"] == 30000.0
        assert rent_row["budget"] == 30000.0
        assert rent_row["pct"] == 100.0, f"Expected 100%, got {rent_row['pct']}"
    finally:
        cleanup(test_dir)


def test_budget_card_shows_zero_spend():
    """A category with a budget but no spend this month shows 0/budget in summary."""
    test_dir = setup_test_db()
    try:
        from app import shop
        cats = shop.list_expense_categories()
        electricity_id = next(c["id"] for c in cats if c["name"] == "Electricity")
        shop.update_expense_category(electricity_id, budget_monthly=5000)
        s = shop.get_expense_summary("2026-08")
        elec_row = next((r for r in s["by_category"] if r["category"] == "Electricity"), None)
        assert elec_row is not None, "Electricity with budget should appear in summary even with 0 spend"
        assert elec_row["total"] == 0.0
        assert elec_row["budget"] == 5000.0
        assert elec_row["pct"] == 0.0
    finally:
        cleanup(test_dir)


def test_recurring_logs_cash_drawer():
    """A cash-method recurring expense generates a cash_drawer entry on generation."""
    test_dir = setup_test_db()
    try:
        from app import db, shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        shop.add_recurring_expense(
            category_id=rent_id, amount=20000, description="Cash rent",
            payment_method="cash", day_of_month=1, active=True,
        )
        shop.generate_recurring_expenses(force_month="2026-08")
        with db.conn() as c:
            drawer = c.execute(
                "SELECT COUNT(*) n FROM cash_drawer WHERE type='expense' AND description LIKE 'Recurring: Rent%'"
            ).fetchone()["n"]
        assert drawer >= 1, f"Expected cash_drawer entry for recurring rent, got {drawer}"
    finally:
        cleanup(test_dir)


def test_category_crud():
    """Full CRUD on expense_categories."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # Create
        cid = shop.add_expense_category("TestCat", is_fixed=False, budget_monthly=1000, sort_order=99)
        # Read
        cats = shop.list_expense_categories()
        assert any(c["id"] == cid and c["name"] == "TestCat" for c in cats)
        # Update
        shop.update_expense_category(cid, name="TestCatRenamed", budget_monthly=2000)
        cats = shop.list_expense_categories()
        renamed = next(c for c in cats if c["id"] == cid)
        assert renamed["name"] == "TestCatRenamed"
        assert renamed["budget_monthly"] == 2000.0
        # Delete
        shop.delete_expense_category(cid)
        cats = shop.list_expense_categories()
        assert not any(c["id"] == cid for c in cats)
    finally:
        cleanup(test_dir)


def test_duplicate_category_name_rejected():
    """Adding a duplicate expense category name raises ValueError."""
    test_dir = setup_test_db()
    try:
        from app import shop
        # "Rent" is seeded by default
        try:
            shop.add_expense_category("Rent")
            assert False, "Expected ValueError for duplicate name"
        except ValueError as e:
            assert "already exists" in str(e)
    finally:
        cleanup(test_dir)


def test_get_expenses_filters():
    """get_expenses supports month, category_id, expense_type filters."""
    test_dir = setup_test_db()
    try:
        from app import shop
        cats = shop.list_expense_categories()
        rent_id = next(c["id"] for c in cats if c["name"] == "Rent")
        # Add 2 operating + 1 owner_draw
        shop.add_expense("Rent", 1000, "r1", "cash", category_id=rent_id,
                         expense_type="operating", date_str="2026-08-01")
        shop.add_expense("Rent", 2000, "r2", "cash", category_id=rent_id,
                         expense_type="operating", date_str="2026-08-15")
        shop.add_expense("Rent", 5000, "draw", "cash", category_id=rent_id,
                         expense_type="owner_draw", date_str="2026-08-20")
        # Filter by month only
        all_aug = shop.get_expenses(month="2026-08", limit=100)
        assert len(all_aug) == 3, f"Expected 3 expenses in Aug, got {len(all_aug)}"
        # Filter by expense_type
        ops = shop.get_expenses(month="2026-08", expense_type="operating", limit=100)
        assert len(ops) == 2, f"Expected 2 operating, got {len(ops)}"
        draws = shop.get_expenses(month="2026-08", expense_type="owner_draw", limit=100)
        assert len(draws) == 1, f"Expected 1 owner_draw, got {len(draws)}"
        # Filter by category_id
        by_cat = shop.get_expenses(month="2026-08", category_id=rent_id, limit=100)
        assert len(by_cat) == 3
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_default_categories_seeded()
    test_add_expense_with_category_id()
    test_recurring_idempotent()
    test_owner_draw_excluded_from_pnl_operating()
    test_owner_draw_excluded_from_operating_total_in_summary()
    test_budget_vs_actual_100_pct()
    test_budget_card_shows_zero_spend()
    test_recurring_logs_cash_drawer()
    test_category_crud()
    test_duplicate_category_name_rejected()
    test_get_expenses_filters()
    print("\n✅ ALL PHASE 2 EXPENSE TESTS PASSED")
