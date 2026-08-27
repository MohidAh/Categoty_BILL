"""FIX 2.1: Idempotency tests — POST same client_uuid twice, assert exactly 1 row."""
import sys, os, tempfile, shutil
from test_helpers import setup_test_db, cleanup
sys.path.insert(0, '.')

SAMPLE_SQL = os.path.join(os.path.dirname(__file__), "sample_data.sql")

def setup_test_db():
    test_dir = tempfile.mkdtemp()
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for d in ['DATA', 'UPLOADS', 'PAGES', 'BACKUPS']:
        os.makedirs(getattr(config, d), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ['sale_items', 'sales', 'bill_items', 'bills', 'customers', 'price_categories', 'suppliers', 'stock_adjustments']:
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    return test_dir


def test_idempotent_customer_payment():
    """POST same client_uuid twice to /api/customers/payments → exactly 1 row."""
    test_dir = setup_test_db()
    try:
        from app.routers.customers import CustomerPaymentIn, add_customer_payment_route
        uuid = "test-uuid-pay-001"
        payload1 = CustomerPaymentIn(customer_id=2, customer_name="Credit Customer", amount=500, payment_method="cash", client_uuid=uuid)
        r1 = add_customer_payment_route(payload1)
        assert "id" in r1, f"First call failed: {r1}"
        first_id = r1["id"]
        payload2 = CustomerPaymentIn(customer_id=2, customer_name="Credit Customer", amount=500, payment_method="cash", client_uuid=uuid)
        r2 = add_customer_payment_route(payload2)
        assert r2.get("idempotent") == True, f"Second call should be idempotent: {r2}"
        assert r2["id"] == first_id, f"Should return same id: {first_id} vs {r2['id']}"
        # Verify only 1 row
        from app import db
        with db.conn() as c:
            count = c.execute("SELECT COUNT(*) n FROM customer_payments WHERE notes LIKE ?", (f"%uuid:{uuid}%",)).fetchone()["n"]
        assert count == 1, f"Expected 1 row, got {count}"
        print("✓ test_idempotent_customer_payment: 1 row, 2nd call returned original")
    finally:
        cleanup(test_dir)

def test_idempotent_stock_adjustment():
    """POST same client_uuid twice to /api/inventory/adjust → exactly 1 row."""
    test_dir = setup_test_db()
    try:
        from app.routers.inventory import StockAdjustmentIn, create_adjustment
        uuid = "test-uuid-adj-001"
        payload1 = StockAdjustmentIn(category_id=1, delta=5, reason="Test adjustment", client_uuid=uuid)
        r1 = create_adjustment(payload1)
        assert "id" in r1, f"First call failed: {r1}"
        first_id = r1["id"]
        payload2 = StockAdjustmentIn(category_id=1, delta=5, reason="Test adjustment", client_uuid=uuid)
        r2 = create_adjustment(payload2)
        assert r2.get("idempotent") == True, f"Second call should be idempotent: {r2}"
        assert r2["id"] == first_id, f"Should return same id"
        from app import db
        with db.conn() as c:
            count = c.execute("SELECT COUNT(*) n FROM stock_adjustments WHERE reason LIKE ?", (f"%uuid:{uuid}%",)).fetchone()["n"]
        assert count == 1, f"Expected 1 row, got {count}"
        print("✓ test_idempotent_stock_adjustment: 1 row, 2nd call returned original")
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_idempotent_customer_payment()
    test_idempotent_stock_adjustment()
    print("\n✅ ALL IDEMPOTENCY TESTS PASSED")
