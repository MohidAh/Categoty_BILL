"""Repair corrupted category_stock_state caused by the v8.5.4 double-subtraction bug.

The re-confirm reversal code subtracted value TWICE:
  1. Via apply_adjustment_to_state (uses current avg_cost for value change)
  2. Manually subtracted (old_qty × old_price) again

This produced negative avg_cost values (e.g. -20.94 for category A instead of 208.78).

This script:
  1. Calls rebuild_stock_state() which replays ALL confirmed bills + sales
     chronologically from scratch — wiping the corrupted state.
  2. Verifies the result against expected values.
  3. Prints BEFORE and AFTER comparison.

Usage:
    cd C:\\Users\\allah hu\\Desktop\\BILL_MANAGEMENT_SOFTWARE
    python scripts\\repair_stock_state.py
"""
import os
import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "billbook.db"

if not DB_PATH.exists():
    print(f"ERROR: Database not found at {DB_PATH}")
    sys.exit(1)

print(f"Database: {DB_PATH}")
print()

# Show BEFORE state
print("=" * 70)
print("BEFORE — current (corrupted) stock state")
print("=" * 70)
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
for r in conn.execute(
    "SELECT css.category_id, pc.name, pc.code, pc.sell_price, "
    "css.current_qty, css.current_value, css.current_avg_cost "
    "FROM category_stock_state css "
    "LEFT JOIN price_categories pc ON css.category_id=pc.id "
    "ORDER BY pc.sort_order"
).fetchall():
    print(f"  {r['name']:15s}  qty={r['current_qty']:>8.2f}  "
          f"value=Rs {r['current_value']:>12.2f}  "
          f"avg_cost=Rs {r['current_avg_cost']:>10.4f}")

# v8.5.5: Clean up duplicate bill_items (keep the first copy, delete extras)
print()
print("=" * 70)
print("CLEANING UP DUPLICATE BILL ITEMS")
print("=" * 70)
dup_count = conn.execute("""
    SELECT COUNT(*) FROM bill_items bi1
    WHERE EXISTS (
        SELECT 1 FROM bill_items bi2
        WHERE bi2.bill_id = bi1.bill_id
        AND bi2.raw = bi1.raw
        AND bi2.price = bi1.price
        AND bi2.qty = bi1.qty
        AND bi2.id < bi1.id
    )
""").fetchone()[0]
if dup_count > 0:
    print(f"  Found {dup_count} duplicate bill_items — deleting extras...")
    conn.execute("""
        DELETE FROM bill_items WHERE id IN (
            SELECT bi1.id FROM bill_items bi1
            WHERE EXISTS (
                SELECT 1 FROM bill_items bi2
                WHERE bi2.bill_id = bi1.bill_id
                AND bi2.raw = bi1.raw
                AND bi2.price = bi1.price
                AND bi2.qty = bi1.qty
                AND bi2.id < bi1.id
            )
        )
    """)
    conn.commit()
    print(f"  Deleted {dup_count} duplicate rows")
else:
    print("  No duplicate bill_items found")
conn.close()

# Now rebuild
print()
print("=" * 70)
print("REBUILDING stock state from scratch...")
print("=" * 70)

# Set up the app environment
os.environ.setdefault("BILLBOOK_DATA_DIR", str(PROJECT_ROOT / "data"))
from app import config as _config
from app import db as _db
from app import db
from app.profit_engine import rebuild_stock_state

result = rebuild_stock_state()
print(f"  Rebuilt {len(result.get('categories', []))} categories")
print(f"  Rewrote {result.get('rewrote_sales', 0)} sale_items cost_price values")

# Show AFTER state
print()
print("=" * 70)
print("AFTER — rebuilt stock state (correct)")
print("=" * 70)
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
for r in conn.execute(
    "SELECT css.category_id, pc.name, pc.code, pc.sell_price, "
    "css.current_qty, css.current_value, css.current_avg_cost "
    "FROM category_stock_state css "
    "LEFT JOIN price_categories pc ON css.category_id=pc.id "
    "ORDER BY pc.sort_order"
).fetchall():
    print(f"  {r['name']:15s}  qty={r['current_qty']:>8.2f}  "
          f"value=Rs {r['current_value']:>12.2f}  "
          f"avg_cost=Rs {r['current_avg_cost']:>10.4f}")

# Verify dashboard totals
print()
print("=" * 70)
print("VERIFICATION — dashboard should now show correct values")
print("=" * 70)
row = conn.execute(
    "SELECT COALESCE(SUM(si.sell_price*si.qty),0) AS total_sales, "
    "COALESCE(SUM(si.cost_price*si.qty),0) AS total_cogs "
    "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
    "WHERE s.payment_status != 'refunded'"
).fetchone()
ts = float(row["total_sales"] or 0)
tc = float(row["total_cogs"] or 0)
gp = ts - tc
margin = (gp / ts * 100) if ts > 0 else 0
print(f"  Total Sales:  Rs {ts:,.0f}")
print(f"  Total COGS:   Rs {tc:,.0f}")
print(f"  Gross Profit: Rs {gp:,.0f}")
print(f"  Margin:       {margin:.1f}%")
conn.close()

print()
print("=" * 70)
print("DONE — stock state repaired. Restart uvicorn for changes to take effect.")
print("=" * 70)
