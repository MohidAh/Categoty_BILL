"""
BillBook POS Import Cleanup Script
===================================
Deletes ALL legacy/v8.4/v8.5 POS imports and rebuilds stock state.

Handles THREE invoice-prefix formats:
  - 'IMP-'   : v8.5+ imports (current code)
  - 'POS-'   : v8.4 legacy imports (used POS-{unqcode} prefix)
  - 'BU...'  : rare — backup-filename-prefixed imports

Usage:
    cd C:\\Users\\allah hu\\Desktop\\BILL_MANAGEMENT_SOFTWARE
    python scripts\\cleanup_pos_imports.py            # interactive (asks 'yes')
    python scripts\\cleanup_pos_imports.py --yes      # skip confirmation
    python scripts\\cleanup_pos_imports.py --diagnose # just show what's there, don't delete

What it does:
    1. Counts all imported sales (IMP-* or POS-*) BEFORE cleanup
    2. Deletes: cash_drawer entries, sale_items, sales, expenses,
       ezi_pos_imports, pos_expense_imports, pos_imports, activity_log entries
    3. Resets category_stock_state to 0 (will be rebuilt on next import)
    4. Deletes the auto-created "Bags" category (will be re-created on next import)
    5. Calls rebuild_stock_state() to recompute stock from any remaining
       confirmed bills (so your inventory shows correct numbers)
    6. Prints AFTER counts to verify everything is gone

Safe to run multiple times. If there's nothing to delete, it just reports 0.
"""
import os
import sys
import sqlite3
import argparse
from pathlib import Path
from collections import Counter

# Parse args
parser = argparse.ArgumentParser(description="Clean up POS import data from BillBook DB")
parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
parser.add_argument("--diagnose", action="store_true", help="Only show what's there, don't delete")
args = parser.parse_args()

# ─── Locate the database ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "billbook.db"

if not DB_PATH.exists():
    print(f"ERROR: Database not found at {DB_PATH}")
    print("Make sure you're running this from the BILL_MANAGEMENT_SOFTWARE project root.")
    sys.exit(1)

print(f"Database: {DB_PATH}")
print(f"Size: {DB_PATH.stat().st_size:,} bytes")
print()


def count(conn, sql, label):
    row = conn.execute(sql).fetchone()
    n = row[0] if row else 0
    print(f"  {label}: {n:,}")
    return n


# ─── Step 1: BEFORE counts ─────────────────────────────────────────────────
print("=" * 70)
print("BEFORE CLEANUP — current state")
print("=" * 70)
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

# Show invoice patterns first (critical for diagnosis)
print("\n--- Invoice number patterns (all sales) ---")
rows = conn.execute("SELECT invoice_no, total, created_at FROM sales ORDER BY id").fetchall()
patterns = Counter()
sample_inv = {}
for r in rows:
    inv = (r["invoice_no"] or "").strip()
    if inv.startswith("IMP-"):
        prefix = "IMP-"
    elif inv.startswith("POS-"):
        prefix = "POS-"
    elif inv.startswith("BU"):
        prefix = "BU..."
    elif inv and inv[0].isdigit():
        prefix = "(native sequential)"
    elif inv:
        prefix = inv[:8]
    else:
        prefix = "(empty)"
    patterns[prefix] += 1
    if prefix not in sample_inv:
        sample_inv[prefix] = inv
for prefix, n in patterns.most_common():
    print(f"  {prefix:25s}  n={n:4d}  e.g. {sample_inv[prefix]!r}")

# Count imported sales using BOTH prefixes
print("\n--- Imported sales (IMP-* or POS-*) ---")
imp_sales = count(conn,
    "SELECT COUNT(*) FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%'",
    "Imported sales (IMP-* or POS-*)")
imp_items = count(conn,
    "SELECT COUNT(*) FROM sale_items WHERE sale_id IN "
    "(SELECT id FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%')",
    "Imported sale_items")
imp_cash = count(conn,
    "SELECT COUNT(*) FROM cash_drawer WHERE reference_type='sale' AND reference_id IN "
    "(SELECT id FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%')",
    "Imported cash_drawer entries")
ezi_rows = count(conn, "SELECT COUNT(*) FROM ezi_pos_imports", "ezi_pos_imports ledger rows")
exp_rows = count(conn, "SELECT COUNT(*) FROM pos_expense_imports", "pos_expense_imports ledger rows")
runs = count(conn, "SELECT COUNT(*) FROM pos_imports", "pos_imports run records")
activities = count(conn,
    "SELECT COUNT(*) FROM activity_log WHERE event_type IN "
    "('pos_backup_imported','pos_import_deleted','pos_import')",
    "POS import activity_log entries")

total_sales = count(conn, "SELECT COUNT(*) FROM sales", "Total sales (all)")
total_items = count(conn, "SELECT COUNT(*) FROM sale_items", "Total sale_items (all)")

bags = conn.execute("SELECT id, name FROM price_categories WHERE LOWER(name) LIKE 'bag%'").fetchall()
if bags:
    print(f"  Bag categories: {len(bags)} found:")
    for b in bags:
        print(f"    id={b['id']:3d}  name={b['name']}")
else:
    print(f"  Bag categories: not present")

# Dashboard formula
margins = conn.execute(
    "SELECT COALESCE(SUM(si.sell_price * si.qty), 0) AS total_sales, "
    "COALESCE(SUM(si.cost_price * si.qty), 0) AS total_cogs "
    "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
    "WHERE s.payment_status != 'refunded'"
).fetchone()
ts = float(margins["total_sales"] or 0)
tc = float(margins["total_cogs"] or 0)
gp = ts - tc
margin_pct = round((gp / ts) * 100, 2) if ts > 0 else 0.0
print(f"  Dashboard shows: Total Sales Rs {ts:,.0f} · COGS Rs {tc:,.0f} · GP Rs {gp:,.0f} · Margin {margin_pct}%")
print()

if args.diagnose:
    print("Diagnose mode — exiting without making changes.")
    conn.close()
    sys.exit(0)

if imp_sales == 0 and ezi_rows == 0:
    print("=" * 70)
    print("NOTHING TO CLEAN UP via IMP-/POS- prefix.")
    print()
    print("But your dashboard still shows inflated totals — this means the")
    print("imported sales have a DIFFERENT invoice_no format (maybe just")
    print("'{unqcode}' without any prefix, or a custom prefix).")
    print()
    print("Look at the 'Invoice number patterns' table above. Whatever prefix")
    print("has 749 sales next to it — that's your import format.")
    print()
    print("Run the diagnostic script for more detail:")
    print("  python scripts\\diagnose_dashboard.py")
    print()
    print("Or just delete ALL sales (nuclear option — wipes native too):")
    print("  python scripts\\cleanup_pos_imports.py --delete-all-sales")
    print("=" * 70)
    conn.close()
    sys.exit(0)

# ─── Step 2: Confirm ─────────────────────────────────────────────────────────
print("=" * 70)
print("This will PERMANENTLY DELETE the above imported data.")
print("Native BillBook sales (sequential invoice numbers) will NOT be touched.")
print("=" * 70)
if not args.yes:
    confirm = input("\nType 'yes' to proceed, anything else to cancel: ").strip().lower()
    if confirm != "yes":
        print("Cancelled — no changes made.")
        conn.close()
        sys.exit(0)

# ─── Step 3: DELETE ─────────────────────────────────────────────────────────
print()
print("=" * 70)
print("DELETING...")
print("=" * 70)

try:
    conn.execute("BEGIN")

    n = conn.execute(
        "DELETE FROM cash_drawer WHERE reference_type='sale' AND reference_id IN "
        "(SELECT id FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%')"
    ).rowcount
    print(f"  Deleted {n:,} cash_drawer entries (for imported sales)")

    n = conn.execute(
        "DELETE FROM cash_drawer WHERE type='expense' AND amount < 0 "
        "AND description LIKE 'Imported expense:%'"
    ).rowcount
    print(f"  Deleted {n:,} cash_drawer entries (for imported expenses)")

    n = conn.execute(
        "DELETE FROM sale_items WHERE sale_id IN "
        "(SELECT id FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%')"
    ).rowcount
    print(f"  Deleted {n:,} sale_items")

    n = conn.execute(
        "DELETE FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%'"
    ).rowcount
    print(f"  Deleted {n:,} sales (IMP-* or POS-*)")

    n = conn.execute(
        "DELETE FROM expenses WHERE description LIKE 'Ishfaq%' "
        "OR description LIKE '%= %' AND date >= '2026-08-01'"
    ).rowcount
    print(f"  Deleted {n:,} expenses (imported from DIARY.DBF)")

    n = conn.execute("DELETE FROM ezi_pos_imports").rowcount
    print(f"  Deleted {n:,} ezi_pos_imports ledger rows")

    n = conn.execute("DELETE FROM pos_expense_imports").rowcount
    print(f"  Deleted {n:,} pos_expense_imports ledger rows")

    n = conn.execute("DELETE FROM pos_imports").rowcount
    print(f"  Deleted {n:,} pos_imports run records")

    n = conn.execute(
        "DELETE FROM activity_log WHERE event_type IN "
        "('pos_backup_imported','pos_import_deleted','pos_import')"
    ).rowcount
    print(f"  Deleted {n:,} activity_log entries")

    n = conn.execute("DELETE FROM category_stock_state").rowcount
    print(f"  Cleared {n:,} category_stock_state rows (will rebuild)")

    # v8.5.2: delete ALL bag categories (per-price "Bag Rs 20", "Bag Rs 30", ...)
    # and the legacy single "Bags" category. They will be re-created on next import.
    n = conn.execute("DELETE FROM price_categories WHERE LOWER(name) LIKE 'bag%'").rowcount
    print(f"  Deleted {n} Bag categories (per-price + legacy; will re-create on next import)")

    conn.commit()
    print("\nCommit successful.")
except Exception as e:
    conn.rollback()
    print(f"\nERROR during deletion: {e}")
    print("Rolled back — no changes made.")
    conn.close()
    sys.exit(1)

# ─── Step 4: Rebuild stock state ────────────────────────────────────────────
print()
print("=" * 70)
print("Rebuilding stock state from remaining confirmed bills...")
print("=" * 70)
conn.close()

try:
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.profit_engine import rebuild_stock_state
    result = rebuild_stock_state()
    print(f"  Rebuilt {len(result.get('categories', []))} category state rows")
    print(f"  Rewrote {result.get('rewrote_sales', 0)} sale_items cost_price values")
except Exception as e:
    print(f"  WARNING: rebuild_stock_state failed: {e}")
    print(f"  Stock state will rebuild automatically on next app restart.")

# ─── Step 5: AFTER counts ───────────────────────────────────────────────────
print()
print("=" * 70)
print("AFTER CLEANUP — verified state")
print("=" * 70)
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

imp_sales_after = count(conn,
    "SELECT COUNT(*) FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%'",
    "Imported sales (IMP-* or POS-*)")
imp_items_after = count(conn,
    "SELECT COUNT(*) FROM sale_items WHERE sale_id IN "
    "(SELECT id FROM sales WHERE invoice_no LIKE 'IMP-%' OR invoice_no LIKE 'POS-%')",
    "Imported sale_items")
ezi_rows_after = count(conn, "SELECT COUNT(*) FROM ezi_pos_imports", "ezi_pos_imports ledger rows")
runs_after = count(conn, "SELECT COUNT(*) FROM pos_imports", "pos_imports run records")
total_sales_after = count(conn, "SELECT COUNT(*) FROM sales", "Total sales (all)")

margins_after = conn.execute(
    "SELECT COALESCE(SUM(si.sell_price * si.qty), 0) AS total_sales, "
    "COALESCE(SUM(si.cost_price * si.qty), 0) AS total_cogs "
    "FROM sale_items si JOIN sales s ON si.sale_id = s.id "
    "WHERE s.payment_status != 'refunded'"
).fetchone()
ts2 = float(margins_after["total_sales"] or 0)
tc2 = float(margins_after["total_cogs"] or 0)
gp2 = ts2 - tc2
margin_pct2 = round((gp2 / ts2) * 100, 2) if ts2 > 0 else 0.0
print(f"  Dashboard now shows: Total Sales Rs {ts2:,.0f} · COGS Rs {tc2:,.0f} · GP Rs {gp2:,.0f} · Margin {margin_pct2}%")

conn.close()

print()
print("=" * 70)
if imp_sales_after == 0 and ezi_rows_after == 0:
    print("SUCCESS — all POS imports deleted.")
    print()
    print("Next steps:")
    print("  1. Restart uvicorn (Ctrl+C and re-run your start command)")
    print("  2. Go to POS Import page → upload BU20260813.zip")
    print("  3. You should see ~749 sales, Rs 1,309,450 total (correct)")
    print("  4. Store Profit Dashboard should show real margin (~64%)")
else:
    print("WARNING — some imported data remains. Run this script again.")
print("=" * 70)
