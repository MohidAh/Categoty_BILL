#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# v8.18.9 — Monthly Close browser E2E (Playwright)
#
# Reproduces the user's exact complaint in the REAL UI:
#   "report month close — why no data is showing"
#
# Root cause was the UI reading API fields that never existed, so the
# page rendered zeros forever. This drives the real login → shell →
# /reports/monthly-close flow and asserts REAL numbers from the sample
# data, the empty-month state, and month switching.
#
# License note: the signing PRIVATE key was lost with the workspace
# snapshot, so this E2E boots the server through an E2E-ONLY wrapper
# module that patches licensing.is_activated() BEFORE app.main loads.
# No production file is touched (same approach as the v8.18.8-session
# pagination E2E).
#
# Run: python scripts/v8_18_9_monthly_close_e2e.py
# ═══════════════════════════════════════════════════════════════════
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PORT = 8818
BASE = f"http://127.0.0.1:{PORT}"
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} — {detail}")

# ── 1. temp data dir + DB + password + sample data ───────────────────
data_dir = tempfile.mkdtemp(prefix="bb_mc_e2e_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password

db.init()
with db.conn() as c:
    for t in ("sale_items", "sales", "bill_items", "bills", "customers",
              "price_categories", "suppliers", "stock_adjustments",
              "category_stock_state", "expenses"):
        c.execute(f"DELETE FROM {t}")
    with open(SAMPLE_SQL) as f:
        c.executescript(f.read())
    # An operating expense in August so the expense row is non-zero too
    c.execute("INSERT INTO expenses(category, description, amount, "
              "payment_method, date, expense_type) "
              "VALUES('Rent', 'e2e', 2000, 'cash', '2026-08-05 10:00:00', 'operating')")
    c.execute("DELETE FROM settings WHERE key='password_hash'")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
from app import profit
profit.rebuild_stock_state()
print(f"[setup] DB at {data_dir}")

# Expected values (sample data 2026-08 + rent 2000; rebuild normalizes
# D's cost to 150, so COGS = 7080):
EXP = {
    "sales_count": 2,
    "revenue": 15650,
    "cogs": 7080,
    "gross": 8570,
    "net": 6570,       # 8570 - 2000 rent
    "bills": 3,
    "spent": 13750,
}

# ── 2. E2E-only license-bypass wrapper + uvicorn ──────────────────────
# (kills any stale server on the port first — sandbox learning)
subprocess.run(["pkill", "-9", "-f", f"uvicorn.*--port {PORT}"], capture_output=True)
time.sleep(0.5)
wrapper = Path(data_dir) / "e2e_wrapper.py"
wrapper.write_text(
    "# E2E ONLY — never shipped. Patches the license gate before app.main loads.\n"
    "import app.licensing as _lic\n"
    "_lic.is_activated = lambda: True\n"
    "_lic.license_state = lambda: {'required': True, 'activated': True, "
    "'setup_id': 'E2E', 'license': None, 'reason': None}\n"
    "from app.main import app\n"          # noqa: import AFTER patch
    "app = app\n"
)
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "e2e_wrapper:app",
     "--app-dir", str(data_dir), "--port", str(PORT), "--log-level", "warning"],
    cwd=PROJ, env={**os.environ, "PYTHONPATH": f"{PROJ}{os.pathsep}{data_dir}"},
    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
)
_ready = False
for _ in range(90):
    try:
        urllib.request.urlopen(f"{BASE}/login", timeout=1)
        _ready = True
        break
    except Exception:
        time.sleep(0.5)
assert _ready, "server never became ready"
assert server.poll() is None, "uvicorn child died"
print(f"[setup] server on :{PORT} (pid {server.pid})")

# ── 3. sanity: the API itself returns the fixed fields ────────────────
import requests
# (login first to get a session cookie)
sess = requests.Session()
r0 = sess.post(f"{BASE}/api/login", json={"password": "testpass"}, timeout=10)
check("API login ok", r0.status_code == 200, r0.text[:200])
r = sess.get(f"{BASE}/api/reports/monthly-close?year=2026&month=8", timeout=10).json()
check("API sales_count", r.get("sales_count") == EXP["sales_count"], r.get("sales_count"))
check("API total_revenue", abs(r.get("total_revenue", -1) - EXP["revenue"]) < 0.01, r.get("total_revenue"))
check("API net_profit", abs(r.get("net_profit", -1) - EXP["net"]) < 0.01, r.get("net_profit"))
check("API bills_count", r.get("bills_count") == EXP["bills"], r.get("bills_count"))
check("API details dict", isinstance(r.get("details"), dict) and len(r["details"]) >= 10)
check("API sales_by_category", len(r.get("sales_by_category", [])) == 4)
pdf = sess.get(f"{BASE}/api/reports/monthly-close.pdf?year=2026&month=8", timeout=30)
check("PDF endpoint 200 + application/pdf",
      pdf.status_code == 200 and pdf.headers.get("content-type", "").startswith("application/pdf"),
      f"{pdf.status_code} {pdf.headers.get('content-type')}")

# ── 4. browser: the REAL page in the REAL shell ───────────────────────
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()

    # login
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("#p", "testpass")
    page.click(".login-btn")
    page.wait_for_selector(".launcher-root", timeout=20000)
    page.wait_for_timeout(400)
    check("login → launcher", True)

    # open the monthly close page
    page.goto(f"{BASE}/#/reports/monthly-close")
    page.wait_for_selector("#mc-month", timeout=20000)
    page.wait_for_timeout(800)

    # Today is 2026-09 — sample data lives in 2026-08, so the DEFAULT
    # month must show the explicit empty state (the old page showed a
    # misleading wall of zeros here instead).
    empty_ok = page.locator(".empty-state").count() >= 1
    check("default (empty) month shows the empty-state, not zeros", empty_ok,
          page.locator("#mc-out").inner_text()[:200])

    # switch to August 2026 — the data month
    page.fill("#mc-month", "2026-08")
    page.dispatch_event("#mc-month", "change")
    page.wait_for_timeout(1200)

    body = page.locator("#mc-out").inner_text()
    cards = page.locator(".stat-card").all_inner_texts()
    print(f"[ui] stat cards: {cards}")
    check("POS Sales card = 2", any(c.upper().startswith("POS SALES") and "\n2" in c for c in cards), cards)
    check("Revenue card = Rs 15,650", any("Rs 15,650" in c for c in cards), cards)
    check("Net Profit card = Rs 6,570", any("Rs 6,570" in c for c in cards), cards)
    check("Bills card = 3", any(c.upper().startswith("BILLS PROCESSED") and "\n3\n" in c for c in cards), cards)
    check("Sales & Profit card present", "Sales & Profit" in body, body[:120])
    check("Purchases card present", "Purchases (Bills)" in body)
    check("COGS row = Rs 7,080", "Rs 7,080" in body)
    check("Operating Expenses row = Rs 2,000", "Rs 2,000" in body)
    check("supplier chips render", page.locator("#mc-out .chip").count() >= 2,
          "expected ABC Trading + XYZ Imports chips")
    cat_rows = page.locator("#mc-out table.table tbody tr").count()
    check("sales-by-category table has 4 rows", cat_rows == 4, cat_rows)

    # switch to a truly empty month — empty state returns
    page.fill("#mc-month", "2025-01")
    page.dispatch_event("#mc-month", "change")
    page.wait_for_timeout(1000)
    check("empty month (2025-01) shows empty-state",
          page.locator(".empty-state").count() >= 1,
          page.locator("#mc-out").inner_text()[:120])

    # and back — data comes back (listener not dead after empty state)
    page.fill("#mc-month", "2026-08")
    page.dispatch_event("#mc-month", "change")
    page.wait_for_timeout(1000)
    check("switching back re-renders the data",
          any("Rs 15,650" in c for c in page.locator(".stat-card").all_inner_texts()))

    # console errors during the whole flow?
    browser.close()

print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES: ' + str(FAIL)}  ({PASS} passed, {FAIL} failed)")
server.terminate()
try:
    server.wait(timeout=5)
except Exception:
    server.kill()
import shutil
shutil.rmtree(data_dir, ignore_errors=True)
sys.exit(1 if FAIL else 0)
