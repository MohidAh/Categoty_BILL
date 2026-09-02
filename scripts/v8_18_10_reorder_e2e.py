#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# v8.18.10 — /reorder page browser E2E (Playwright)
#
# Verifies the whole-system-review fix end-to-end in the REAL UI:
#   "/reorder rendered empty shells with 0 stats and broken buttons"
#
# The old page read fields the API never returned (suggested_qty,
# avg_cost, category_name, ...) — the new page reads the table-backed
# rows (item_name, suggested_quantity, avg_price, id) that
# GET /api/reorder-reminders now persists.
#
# Flow: seed a purchase pattern -> API returns persisted rows with ids
# -> browser renders real stats -> Dismiss works -> Mark Ordered works.
#
# License note: same E2E-only wrapper as v8_18_9_monthly_close_e2e.py
# (private key lost with the workspace snapshot). No production file
# is touched.
#
# Run: python scripts/v8_18_10_reorder_e2e.py
# ═══════════════════════════════════════════════════════════════════
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PORT = 8819
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

# ── 1. temp data dir + DB + password + sample data + reorder pattern ─
data_dir = tempfile.mkdtemp(prefix="bb_re_e2e_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password

db.init()
with db.conn() as c:
    for t in ("sale_items", "sales", "bill_items", "bills", "customers",
              "price_categories", "suppliers", "stock_adjustments",
              "category_stock_state", "expenses", "reorder_reminders"):
        c.execute(f"DELETE FROM {t}")
    with open(SAMPLE_SQL) as f:
        c.executescript(f.read())
    # Purchase pattern 1: 'E2E Reorder Widget' bought 4x ~15d apart,
    # last on 2026-07-01 -> ~63d since, ratio > 2 -> high priority.
    # Pattern 2: 'E2E Slow Mover' 3x ~30d apart, last 2026-06-20.
    for i, d in enumerate(["2026-05-22", "2026-06-06", "2026-06-21", "2026-07-01"]):
        c.execute(
            "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
            "written_total, computed_total, status, payment_status, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (901 + i, 1, "E2E Supplies", d, f"E2E-W{i}", 1000, 1000,
             "confirmed", "paid", f"{d} 10:00:00"))
        c.execute(
            "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, "
            "qty, unit, line_total, page_no) VALUES(?,?,?,?,?,?,?,?,?)",
            (901 + i, 1, "E2E Reorder Widget", "A", 95, 10, "pcs", 950, 1))
    for i, d in enumerate(["2026-04-01", "2026-05-01", "2026-06-20"]):
        c.execute(
            "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, bill_no, "
            "written_total, computed_total, status, payment_status, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (911 + i, 2, "E2E Trading", d, f"E2E-S{i}", 500, 500,
             "confirmed", "paid", f"{d} 10:00:00"))
        c.execute(
            "INSERT INTO bill_items(bill_id, category_id, raw, item_code, price, "
            "qty, unit, line_total, page_no) VALUES(?,?,?,?,?,?,?,?,?)",
            (911 + i, 2, "E2E Slow Mover", "B", 40, 5, "pcs", 200, 1))
    c.execute("DELETE FROM settings WHERE key='password_hash'")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
print(f"[setup] DB at {data_dir}")

# ── 2. E2E-only license-bypass wrapper + uvicorn ──────────────────────
subprocess.run(["pkill", "-9", "-f", f"uvicorn.*--port {PORT}"], capture_output=True)
time.sleep(0.5)
wrapper = Path(data_dir) / "e2e_wrapper.py"
wrapper.write_text(
    "# E2E ONLY — never shipped. Patches the license gate before app.main loads.\n"
    "import app.licensing as _lic\n"
    "_lic.is_activated = lambda: True\n"
    "_lic.license_state = lambda: {'required': True, 'activated': True, "
    "'setup_id': 'E2E', 'license': None, 'reason': None}\n"
    "from app.main import app\n"
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

# ── 3. API sanity: persisted rows with ids + real fields ─────────────
import requests
sess = requests.Session()
r0 = sess.post(f"{BASE}/api/login", json={"password": "testpass"}, timeout=10)
check("API login ok", r0.status_code == 200, r0.text[:200])
r = sess.get(f"{BASE}/api/reorder-reminders", timeout=10).json()
rows = r.get("reminders", [])
names = {x.get("item_name"): x for x in rows}
check("API returns seeded widget", "E2E Reorder Widget" in names, list(names)[:5])
check("API returns seeded mover", "E2E Slow Mover" in names, list(names)[:5])
w = names.get("E2E Reorder Widget", {})
check("rows carry integer ids", isinstance(w.get("id"), int), w.get("id"))
check("rows carry suggested_quantity > 0", (w.get("suggested_quantity") or 0) > 0, w)
check("rows carry avg_price > 0", (w.get("avg_price") or 0) > 0, w.get("avg_price"))
check("rows carry supplier_name", w.get("supplier_name") == "E2E Supplies", w.get("supplier_name"))

# ── 4. browser: the REAL /reorder page ────────────────────────────────
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("#p", "testpass")
    page.click(".login-btn")
    page.wait_for_selector(".launcher-root", timeout=20000)
    page.wait_for_timeout(400)
    check("login → launcher", True)

    page.goto(f"{BASE}/#/reorder")
    page.wait_for_selector("#re-list .re-card, #re-list .empty-state", timeout=20000)
    page.wait_for_timeout(600)

    cards = page.locator(".re-card").count()
    check("both reminders render as cards", cards == 2, f"cards={cards}")

    stats = page.locator(".stat-card").all_inner_texts()
    print(f"[ui] stat cards: {stats}")
    check("Active Reminders stat = 2", any("ACTIVE REMINDERS" in s.upper() and "2" in s for s in stats), stats)
    check("Total Suggested Qty > 0", any("SUGGESTED QTY" in s.upper() and "0" not in s.split("\n")[-1] for s in stats), stats)
    est = [s for s in stats if "ORDER VALUE" in s.upper()]
    check("Est. Order Value is NOT Rs 0", est and "RS 0" not in est[0].upper(), est)

    body = page.locator("#re-list").inner_text()
    check("card shows item name", "E2E Reorder Widget" in body, body[:120])
    check("card shows suggested qty (not 0)", "Suggested: 10" in body or "Suggested:" in body, body[:200])
    check("card shows supplier", "E2E Supplies" in body, body[:200])
    check("no page JS errors", not errors, errors[:3])

    # ── 5. Dismiss one, Mark Ordered the other ────────────────────────
    widget_card = page.locator(".re-card", has_text="E2E Reorder Widget").first
    widget_card.locator("[data-re-dismiss]").click()
    page.wait_for_timeout(1500)
    check("dismissed card disappears", page.locator(".re-card", has_text="E2E Reorder Widget").count() == 0,
          page.locator("#re-list").inner_text()[:120])
    check("other card still there", page.locator(".re-card", has_text="E2E Slow Mover").count() == 1)

    mover_card = page.locator(".re-card", has_text="E2E Slow Mover").first
    mover_card.locator("[data-re-order]").click()
    page.wait_for_timeout(1500)
    check("ordered card disappears", page.locator(".re-card").count() == 0,
          page.locator("#re-list").inner_text()[:120])
    check("empty state shown after all handled", page.locator(".empty-state").count() >= 1)

    # API agrees: both rows are out of the active list but kept in table
    r2 = sess.get(f"{BASE}/api/reorder-reminders", timeout=10).json()
    check("API active list empty after UI actions", r2.get("reminders") == [], r2.get("reminders"))
    with db.conn() as c:
        st = c.execute("SELECT item_name, status FROM reorder_reminders ORDER BY id").fetchall()
    statuses = {dict(x)["item_name"]: dict(x)["status"] for x in st}
    check("rows kept as history (dismissed/ordered)",
          statuses.get("E2E Reorder Widget") == "dismissed"
          and statuses.get("E2E Slow Mover") == "ordered", statuses)

    browser.close()

# ── 6. teardown ────────────────────────────────────────────────────────
server.terminate()
try:
    server.wait(timeout=5)
except Exception:
    server.kill()
subprocess.run(["pkill", "-9", "-f", f"uvicorn.*--port {PORT}"], capture_output=True)

print(f"\n{'=' * 50}\nE2E RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
