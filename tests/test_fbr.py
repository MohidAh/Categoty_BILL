"""FIX 2.3: FBR export tests — valid JSON file with shop NTN/STRN."""
import sys, os, tempfile, shutil, json
from datetime import datetime
from test_helpers import setup_test_db, cleanup
sys.path.insert(0, '.')



def test_fbr_export():
    """POST /api/fbr/export-now writes valid JSON with NTN/STRN.

    Note: sample_data.sql is dated 2026-08-11 (fixed), but fbr_export_now
    filters by today's date. So we inject ONE sale with today's date before
    the test runs — the export logic itself is unchanged.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        today = datetime.now().strftime("%Y-%m-%d")
        # Insert a minimal "paid" sale dated today so fbr_export_now finds at
        # least one invoice. Sample data has fixed historical dates — without
        # this injection the test will always fail when run after 2026-08-11.
        with db.write_tx() as c:
            c.execute(
                "INSERT INTO sales (id, invoice_no, customer_name, customer_phone, "
                "subtotal, discount, total, payment_method, payment_status, "
                "created_at, tax_rate, tax_amount) "
                "VALUES (9001, 'FBR-TEST-001', 'Walk-in Customer', '', "
                "1500, 0, 1500, 'cash', 'paid', ?, 0, 0)",
                (today + " 12:00:00",)
            )
            c.execute(
                "INSERT INTO sale_items (sale_id, item_name, qty, sell_price, "
                "line_total, category_id) "
                "VALUES (9001, 'Test Item', 1, 1500, 1500, 1)"
            )
        # Set shop NTN/STRN so the export has them
        with db.conn() as c:
            c.execute("INSERT INTO settings(key, value) VALUES('shop_ntn', '1234567-8')")
            c.execute("INSERT INTO settings(key, value) VALUES('shop_strn', 'S-9999999-9')")

        from app.routers.settings import fbr_export_now
        result = fbr_export_now()
        assert result["ok"] == True, f"Export failed: {result}"
        assert result["count"] >= 1, f"Expected at least 1 invoice, got {result['count']}"
        # Read the file
        filepath = result["file"]
        assert os.path.exists(filepath), f"File not created: {filepath}"
        with open(filepath) as f:
            data = json.load(f)
        assert "invoices" in data, "Missing 'invoices' key in JSON"
        assert data["count"] >= 1, "Count mismatch"
        # Check NTN/STRN in first invoice
        inv = data["invoices"][0]
        assert inv["ntn"] == "1234567-8", f"NTN mismatch: {inv['ntn']}"
        assert inv["strn"] == "S-9999999-9", f"STRN mismatch: {inv['strn']}"
        assert "invoice_no" in inv, "Missing invoice_no"
        assert "items" in inv, "Missing items array"
        assert len(inv["items"]) >= 1, "No items in invoice"
        print(f"✓ test_fbr_export: {result['count']} invoices, NTN={inv['ntn']}, STRN={inv['strn']}")
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_fbr_export()
    print("\n✅ ALL FBR TESTS PASSED")
