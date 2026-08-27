"""v4.0 Phase 5 — Wholesale Money Flows tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_p5_")
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
                  "supplier_advances", "supplier_rates",
                  "bank_accounts", "bank_transactions"):
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
    return test_dir



def test_supplier_advance_basic():
    """Add advance → listed + cash_drawer entry created."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        # Supplier 1 (ABC Trading) exists in sample data
        aid = shop.add_supplier_advance(1, 50000, payment_method="cash", notes="peshgi")
        advances = shop.list_supplier_advances(1)
        assert len(advances) == 1
        assert advances[0]["amount"] == 50000.0
        # Cash drawer entry
        with db.conn() as c:
            cd = c.execute(
                "SELECT amount FROM cash_drawer WHERE type='supplier_advance' AND reference_id=?",
                (aid,),
            ).fetchone()
        assert cd is not None and cd["amount"] == -50000.0
        # Balance
        bal = shop.get_supplier_advance_balance(1)
        assert bal == 50000.0
    finally:
        cleanup(test_dir)


def test_supplier_advance_apply_to_bill():
    """Apply advance to a bill → applied_to_bill_id set."""
    test_dir = setup_test_db()
    try:
        from app import shop
        aid = shop.add_supplier_advance(1, 50000, "cash", "peshgi")
        # Bill 1 exists in sample data, supplier_id=1
        ok = shop.apply_supplier_advance_to_bill(aid, 1)
        assert ok is True
        advances = shop.list_supplier_advances(1)
        assert advances[0]["applied_to_bill_id"] == 1
        # Re-apply → should fail (already applied)
        ok2 = shop.apply_supplier_advance_to_bill(aid, 2)
        assert ok2 is False
    finally:
        cleanup(test_dir)


def test_supplier_rate_crud():
    """Set, list, delete supplier rates."""
    test_dir = setup_test_db()
    try:
        from app import shop
        rid = shop.set_supplier_rate(1, "Shampoo 100ml", 50.0)
        # Update same item
        rid2 = shop.set_supplier_rate(1, "shampoo 100ml", 55.0)  # case-insensitive
        assert rid == rid2, "Setting rate for same item should update, not insert"
        rates = shop.list_supplier_rates(1)
        assert len(rates) == 1
        assert rates[0]["agreed_price"] == 55.0
        # Delete
        ok = shop.delete_supplier_rate(rid)
        assert ok is True
        rates = shop.list_supplier_rates(1)
        assert len(rates) == 0
    finally:
        cleanup(test_dir)


def test_check_bill_items_against_rates_flags():
    """Items priced above agreed rate → flags generated."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.set_supplier_rate(1, "Shampoo", 50.0)
        items = [
            {"raw": "Shampoo", "price": 75.0},  # 50% over → flag
            {"raw": "Soap", "price": 30.0},     # no rate → no flag
        ]
        flags = shop.check_bill_items_against_rates(items, 1)
        assert len(flags) == 1, f"Expected 1 flag, got {len(flags)}: {flags}"
        assert "Shampoo" in flags[0]
        assert "exceeds agreed rate" in flags[0]
    finally:
        cleanup(test_dir)


def test_check_bill_items_no_rate_for_supplier():
    """No rates defined for supplier → no flags."""
    test_dir = setup_test_db()
    try:
        from app import shop
        items = [{"raw": "Anything", "price": 9999}]
        flags = shop.check_bill_items_against_rates(items, 2)  # supplier 2 has no rates
        assert flags == []
    finally:
        cleanup(test_dir)


def test_bank_account_balance():
    """Bank account balance = opening + deposits - withdrawals."""
    test_dir = setup_test_db()
    try:
        from app import shop
        aid = shop.add_bank_account("HBL", 10000)
        assert shop.get_bank_account_balance(aid) == 10000.0
        shop.add_bank_transaction(aid, "deposit", 5000, "Cash deposit")
        assert shop.get_bank_account_balance(aid) == 15000.0
        shop.add_bank_transaction(aid, "withdrawal", 3000, "ATM withdrawal")
        assert shop.get_bank_account_balance(aid) == 12000.0
        shop.add_bank_transaction(aid, "supplier_payment", 2000, "Paid supplier")
        assert shop.get_bank_account_balance(aid) == 10000.0
    finally:
        cleanup(test_dir)


def test_cash_to_bank_deposit_pairs():
    """Cash-to-bank deposit creates paired bank_transactions + cash_drawer entries."""
    test_dir = setup_test_db()
    try:
        from app import shop, db
        aid = shop.add_bank_account("HBL", 0)
        result = shop.record_cash_to_bank_deposit(aid, 5000, "Daily deposit")
        # Verify bank_transactions entry
        with db.conn() as c:
            btx = c.execute(
                "SELECT * FROM bank_transactions WHERE id=?", (result["bank_tx_id"],)
            ).fetchone()
            cd = c.execute(
                "SELECT * FROM cash_drawer WHERE id=?", (result["cash_drawer_id"],)
            ).fetchone()
        assert btx["type"] == "deposit"
        assert btx["amount"] == 5000.0
        assert cd["type"] == "bank_deposit"
        assert cd["amount"] == -5000.0
        # Bank balance increased
        assert shop.get_bank_account_balance(aid) == 5000.0
    finally:
        cleanup(test_dir)


def test_owner_draw_in_cash_flow():
    """Owner draws already supported (Phase 2). Verify they appear in expense summary's owner_draw_total."""
    test_dir = setup_test_db()
    try:
        from app import shop
        shop.add_expense("Owner Draw", 10000, "draw", "cash",
                         expense_type="owner_draw", date_str="2026-08-15")
        s = shop.get_expense_summary("2026-08")
        assert s["owner_draw_total"] == 10000.0
        assert s["operating_total"] == 0.0  # owner_draw excluded from operating
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_supplier_advance_basic()
    test_supplier_advance_apply_to_bill()
    test_supplier_rate_crud()
    test_check_bill_items_against_rates_flags()
    test_check_bill_items_no_rate_for_supplier()
    test_bank_account_balance()
    test_cash_to_bank_deposit_pairs()
    test_owner_draw_in_cash_flow()
    print("\n✅ ALL PHASE 5 WHOLESALE MONEY FLOW TESTS PASSED")
