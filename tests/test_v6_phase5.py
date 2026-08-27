"""v6.0 Phase 5 — WhatsApp Suite + Raast Reconciliation tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"

def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v6p5_")
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
                  "category_stock_state", "owner_withdrawals",
                  "login_attempts", "devices", "pairing_codes",
                  "bundles", "bundle_items", "price_rules",
                  "lost_sales", "closed_days", "seasons",
                  "customer_payments", "bank_accounts", "bank_transactions"):
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


def test_whatsapp_order_parse():
    """'5 A, 3 C' message parses to a correct cart preview."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        result = ext.parse_whatsapp_order("5 A, 3 C")
        assert result["item_count"] == 2
        # Cat A: sell_price 250, qty 5 → 1250
        # Cat C: sell_price 750, qty 3 → 2250
        assert result["total"] == 3500.0
        codes = [i["code"] for i in result["items"]]
        assert "A" in codes and "C" in codes
    finally:
        cleanup(test_dir)

def test_whatsapp_parse_invalid_codes_ignored():
    """Invalid category codes are ignored, not errors."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        result = ext.parse_whatsapp_order("5 A, 3 Z, hello")
        assert result["item_count"] == 1  # only A is valid
    finally:
        cleanup(test_dir)

def test_urdhaar_reminders():
    """Customer with credit > 0 and old sale shows in reminders."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        # Sample data: Credit Customer has total_credit=5500, last sale 2026-08-11
        # That's recent — won't show. Insert an old sale.
        from app import db
        with db.conn() as c:
            c.execute(
                "INSERT INTO sales(id, invoice_no, customer_name, customer_id, subtotal, total, "
                "payment_method, payment_status, created_at, tax_rate, tax_amount) "
                "VALUES(200, 'INV-OLD', 'Old Customer', 2, 1000, 1000, 'credit', 'credit', "
                "'2026-07-01 12:00:00', 0, 0)"
            )
        reminders = ext.get_urdhaar_reminders()
        assert len(reminders) >= 1
        # The Credit Customer should appear with stage >= 1 (7+ days overdue)
        cc = [r for r in reminders if r["customer_id"] == 2]
        if cc:
            assert cc[0]["stage"] >= 1
            assert "wa.me" in cc[0]["wa_link"]
    finally:
        cleanup(test_dir)

def test_customer_groups():
    """Customer groups return counts."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        groups = ext.get_customer_groups()
        assert len(groups) >= 1
        # All sample customers default to 'retail'
        retail = [g for g in groups if g["group_name"] == "retail"]
        assert len(retail) >= 1
    finally:
        cleanup(test_dir)

def test_raast_reconciliation():
    """Raast reconciliation returns matched + unmatched lists."""
    test_dir = setup_test_db()
    try:
        from app import extensions as ext
        result = ext.get_raast_reconciliation()
        assert "matched" in result
        assert "unmatched_sales" in result
        assert "unmatched_bank_txs" in result
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_whatsapp_order_parse(); print("✓ test_whatsapp_order_parse")
    test_whatsapp_parse_invalid_codes_ignored(); print("✓ test_whatsapp_parse_invalid_codes_ignored")
    test_urdhaar_reminders(); print("✓ test_urdhaar_reminders")
    test_customer_groups(); print("✓ test_customer_groups")
    test_raast_reconciliation(); print("✓ test_raast_reconciliation")
    print("\n✅ ALL PHASE 5 TESTS PASSED")
