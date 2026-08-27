"""Diagnostic script — find the imported sales that still show Rs 2,629,704."""
import sqlite3
from pathlib import Path
from collections import Counter

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "billbook.db"
if not DB_PATH.exists():
    print(f"DB not found: {DB_PATH}"); import sys; sys.exit(1)

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

print("=" * 70)
print("DIAGNOSING: 749 sales showing Rs 2,629,704 in dashboard")
print("=" * 70)

# 1. Invoice number patterns
print("\n--- Invoice number patterns ---")
rows = conn.execute("SELECT invoice_no FROM sales ORDER BY id").fetchall()
patterns = Counter()
samples = {}
for r in rows:
    inv = (r["invoice_no"] or "").strip()
    # Extract prefix (everything before first digit or dash)
    if "-" in inv:
        prefix = inv.split("-")[0]
    elif inv[:3].isalpha():
        prefix = inv[:3]
    else:
        prefix = "(no-prefix)"
    patterns[prefix] += 1
    if prefix not in samples:
        samples[prefix] = inv
for prefix, n in patterns.most_common():
    print(f"  {prefix:15s}  n={n:4d}  e.g. {samples[prefix]!r}")

# 2. Total by invoice pattern
print("\n--- Total revenue by invoice prefix ---")
rows = conn.execute(
    "SELECT "
    "  CASE "
    "    WHEN invoice_no LIKE 'IMP-%' THEN 'IMP-' "
    "    WHEN invoice_no LIKE 'POS-%' THEN 'POS-' "
    "    WHEN invoice_no LIKE 'BU%' THEN 'BU...' "
    "    ELSE COALESCE(substr(invoice_no,1,10),'(empty)') "
    "  END AS prefix, "
    "  COUNT(*) AS n, "
    "  SUM(total) AS total, "
    "  MIN(created_at) AS first_at, "
    "  MAX(created_at) AS last_at "
    "FROM sales GROUP BY prefix ORDER BY total DESC"
).fetchall()
for r in rows:
    print(f"  {r['prefix']:15s}  n={r['n']:4d}  total=Rs {r['total']:>14,.0f}  "
          f"first={r['first_at']}  last={r['last_at']}")

# 3. Sample 5 sales (any prefix)
print("\n--- Sample 5 sales (any prefix) ---")
rows = conn.execute(
    "SELECT id, invoice_no, customer_name, total, payment_method, payment_status, created_at "
    "FROM sales ORDER BY id LIMIT 5"
).fetchall()
for r in rows:
    print(f"  id={r['id']:4d}  inv={r['invoice_no']:25s}  cust={r['customer_name']:20s}  "
          f"total={r['total']:>10,.0f}  method={r['payment_method']:8s}  "
          f"status={r['payment_status']:8s}  at={r['created_at']}")

# 4. Sample 5 most recent sales
print("\n--- Sample 5 most recent sales ---")
rows = conn.execute(
    "SELECT id, invoice_no, customer_name, total, payment_method, payment_status, created_at "
    "FROM sales ORDER BY created_at DESC LIMIT 5"
).fetchall()
for r in rows:
    print(f"  id={r['id']:4d}  inv={r['invoice_no']:25s}  cust={r['customer_name']:20s}  "
          f"total={r['total']:>10,.0f}  method={r['payment_method']:8s}  "
          f"status={r['payment_status']:8s}  at={r['created_at']}")

# 5. Check if maybe the import used POS- prefix (legacy v8.4 behavior)
print("\n--- Sales with POS- prefix (legacy v8.4 import format) ---")
row = conn.execute(
    "SELECT COUNT(*) AS n, SUM(total) AS total FROM sales WHERE invoice_no LIKE 'POS-%'"
).fetchone()
print(f"  POS-* sales: {row['n']}, total Rs {row['total']:,.0f}")

# 6. Sale items cost_price distribution
print("\n--- sale_items cost_price distribution ---")
rows = conn.execute(
    "SELECT CASE WHEN cost_price=0 THEN 'zero' WHEN cost_price IS NULL THEN 'null' ELSE 'non-zero' END AS kind, "
    "COUNT(*) AS n, SUM(sell_price*qty) AS sell_total, SUM(cost_price*qty) AS cogs_total "
    "FROM sale_items GROUP BY kind"
).fetchall()
for r in rows:
    print(f"  cost_price {r['kind']:10s}  n={r['n']:4d}  sell_total=Rs {r['sell_total']:,.0f}  cogs_total=Rs {r['cogs_total']:,.0f}")

# 7. Sum the dashboard formula directly
print("\n--- Dashboard formula (sum of sell_price * qty) ---")
row = conn.execute(
    "SELECT COALESCE(SUM(si.sell_price*si.qty),0) AS total_sales, "
    "COALESCE(SUM(si.cost_price*si.qty),0) AS total_cogs "
    "FROM sale_items si JOIN sales s ON si.sale_id=s.id "
    "WHERE s.payment_status != 'refunded'"
).fetchone()
ts = float(row["total_sales"] or 0)
tc = float(row["total_cogs"] or 0)
print(f"  Total sales (sell_price*qty): Rs {ts:,.0f}")
print(f"  Total COGS (cost_price*qty):  Rs {tc:,.0f}")
print(f"  Total from sales.total:       Rs {sum(r['total'] for r in conn.execute('SELECT total FROM sales WHERE payment_status!=\"refunded\"').fetchall()):,.0f}")

# 8. Where the 2,629,704 comes from — check sales.total vs sale_items
print("\n--- Cross-check: sales.total vs SUM(sale_items.line_total) ---")
row = conn.execute(
    "SELECT COALESCE(SUM(s.total),0) AS sales_total, "
    "COALESCE((SELECT SUM(line_total) FROM sale_items),0) AS items_total, "
    "COALESCE((SELECT SUM(sell_price*qty) FROM sale_items),0) AS sell_total "
    "FROM sales s WHERE s.payment_status != 'refunded'"
).fetchone()
print(f"  SUM(sales.total):                Rs {row['sales_total']:,.0f}")
print(f"  SUM(sale_items.line_total):      Rs {row['items_total']:,.0f}")
print(f"  SUM(sale_items.sell_price*qty):  Rs {row['sell_total']:,.0f}")

conn.close()

print()
print("=" * 70)
print("WHAT TO DO:")
print("=" * 70)
print("If you see POS-* sales above, those are legacy v8.4 imports that used")
print("'POS-' prefix instead of 'IMP-'. Run this to delete them:")
print()
print("  python scripts\\cleanup_pos_imports.py --include-pos-prefix")
print()
print("Or paste the output above back to me and I'll tell you exactly what to delete.")
