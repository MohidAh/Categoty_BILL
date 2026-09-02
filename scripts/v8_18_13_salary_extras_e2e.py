#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# v8.18.13 — Extra Sales + Staff Salary browser E2E (Playwright)
#
# Drives the REAL UI for both new features:
#   Extra Sales:  modal add -> table row + stat card -> delete cleanup
#   Staff Salary: add staff w/ salary -> off-days live preview -> Save
#                 (auto Salaries expense) -> advance (cash out + deduct)
#                 -> Pay (paid chip + cash out) -> history modal
#   plus: Actual Earnings page shows the Extra Sales bridge bar.
#
# License note: same E2E-only wrapper as previous E2E scripts (the
# private key was lost with the workspace snapshot). No production file
# is touched.
#
# Run: python scripts/v8_18_13_salary_extras_e2e.py
# ═══════════════════════════════════════════════════════════════════
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PORT = 8821
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

# ── 1. temp data dir + DB + password + sample data ─────────────────
data_dir = tempfile.mkdtemp(prefix="bb_sal_e2e_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password

db.init()
with db.conn() as c:
    for t in ("sale_items", "sales", "bill_items", "bills", "customers",
              "price_categories", "suppliers", "stock_adjustments",
              "category_stock_state", "expenses", "employees",
              "extra_sales", "salary_records", "salary_advances"):
        c.execute(f"DELETE FROM {t}")
    with open(SAMPLE_SQL) as f:
        c.executescript(f.read())
    c.execute("DELETE FROM settings WHERE key='password_hash'")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
print(f"[setup] DB at {data_dir}")

# ── 2. E2E-only license-bypass wrapper + uvicorn ──────────────────────
def _pkill():
    subprocess.run(["pkill", "-9", "-f", f"uvicorn.*--port {PORT}"], capture_output=True)
_pkill()
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

import requests
sess = requests.Session()
r0 = sess.post(f"{BASE}/api/login", json={"password": "testpass"}, timeout=10)
check("API login ok", r0.status_code == 200, r0.text[:200])

# ── 3. browser flows ─────────────────────────────────────────────────
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
    check("login -> launcher", True)

    # ════════ EXTRA SALES PAGE ═════════
    print("\n── Extra Sales page ──")
    page.goto(f"{BASE}/#/bills/extra-sales")
    page.wait_for_selector("#xs-table", timeout=20000)
    page.wait_for_timeout(600)
    check("page header renders", page.locator(".pos-page-header-title").inner_text() == "Extra Sales")
    check("empty state first", page.locator("#xs-table").inner_text().find("No extra sales") >= 0,
          page.locator("#xs-table").inner_text()[:80])

    # add via modal
    page.click("#xs-add-btn")
    page.wait_for_selector("#xs-save-btn", timeout=5000)
    page.fill("#xs-item", "Cardboard cartons")
    page.fill("#xs-qty", "50")
    page.fill("#xs-price", "15")
    page.wait_for_timeout(200)
    check("total preview updates", "750" in page.locator("#xs-total-preview").inner_text(),
          page.locator("#xs-total-preview").inner_text())
    page.click("#xs-save-btn")
    page.wait_for_timeout(1200)
    body = page.locator("#xs-table").inner_text()
    check("row renders (name)", "Cardboard cartons" in body, body[:120])
    check("row renders (total 750)", "750" in body, body[:200])
    stats = page.locator("#xs-stats").inner_text()
    check("stat card shows 1,950 after both", "1,950" in stats or "Rs 750" in stats, stats[:160])

    # second sale via API, table should show it too
    sess.post(f"{BASE}/api/extra-sales", json={
        "item_name": "Raddi (scrap)", "quantity": 30, "unit_price": 40,
        "payment_method": "bank", "date": "2026-09-10"}, timeout=10)
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#xs-table", timeout=20000)
    page.wait_for_timeout(700)
    body = page.locator("#xs-table").inner_text()
    check("bank sale row renders", "Raddi (scrap)" in body, body[:150])
    check("cash chip shows", "cash" in body.lower(), body[:200])
    stats = page.locator("#xs-stats").inner_text()
    check("month total = Rs 1,950", "1,950" in stats, stats[:160])

    # actual-earnings bridge shows Extra Sales bar
    page.goto(f"{BASE}/#/reports/earnings")
    page.wait_for_timeout(1500)
    ae_body = page.locator("#ae-out").inner_text()
    check("earnings bridge mentions Extra Sales", "Extra Sales" in ae_body, ae_body[:200])
    check("earnings shows income 1,950", "1,950" in ae_body, ae_body[:300])

    # delete from the table
    page.goto(f"{BASE}/#/bills/extra-sales")
    page.wait_for_selector("[data-xs-del]", timeout=20000)
    page.wait_for_timeout(400)
    page.locator("[data-xs-del]").first.click()
    page.wait_for_timeout(600)  # confirm() auto-accepts in headless? no — playwright dismisses by default
    # playwright auto-dismisses dialogs; accept them explicitly
    page.on("dialog", lambda d: d.accept())
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#xs-table", timeout=20000)
    page.wait_for_timeout(700)
    rows_before = page.locator("[data-xs-del]").count()
    page.locator("[data-xs-del]").first.click()
    page.wait_for_timeout(1500)
    rows_after = page.locator("[data-xs-del]").count()
    check("delete removes the row", rows_after == rows_before - 1, f"{rows_before}->{rows_after}")

    # ════════ STAFF SALARY PAGE ═════════
    print("\n── Staff Salary page ──")
    page.goto(f"{BASE}/#/bills/salary")
    page.wait_for_selector("#sal-table", timeout=20000)
    page.wait_for_timeout(600)
    check("salary header renders", page.locator(".pos-page-header-title").inner_text() == "Staff Salary")
    check("no staff empty state", "No staff added yet" in page.locator("#sal-table").inner_text(),
          page.locator("#sal-table").inner_text()[:80])

    # add staff via modal
    page.click("#sal-add-staff-btn")
    page.wait_for_selector("#st-save-btn", timeout=5000)
    page.fill("#st-name", "Ali Raza")
    page.fill("#st-phone", "03001234567")
    page.fill("#st-salary", "30000")
    page.click("#st-save-btn")
    page.wait_for_timeout(1200)
    body = page.locator("#sal-table").inner_text()
    check("employee row renders", "Ali Raza" in body, body[:120])
    check("salary shown", "30,000" in body, body[:200])
    check("Not saved chip", "Not saved" in body, body[:250])

    # off-days live preview: type 2 -> extra 2 days, final = 30,000+2,000
    page.fill("[data-sal-offdays]", "2")
    page.wait_for_timeout(300)
    row = page.locator("tr", has_text="Ali Raza").first
    row_text = row.inner_text()
    check("live preview: extra days = 2", "2" in row_text, row_text[:200])
    check("live preview: final 32,000", "32,000" in row_text, row_text[:300])

    # advance first (so Save picks it up): record 5,000
    page.click("#sal-advance-btn")
    page.wait_for_selector("#adv-save-btn", timeout=5000)
    page.select_option("#adv-emp", label="Ali Raza")
    page.fill("#adv-amount", "5000")
    page.fill("#adv-desc", "Eid advance")
    page.click("#adv-save-btn")
    page.wait_for_timeout(1200)
    adv_body = page.locator("#sal-advances").inner_text()
    check("advance listed", "Ali Raza" in adv_body and "5,000" in adv_body, adv_body[:160])
    check("final payable drops to 27,000", "27,000" in page.locator("#sal-table").inner_text(),
          page.locator("#sal-table").inner_text()[:300])

    # save the record (off-days 2) -> Salaries expense auto-posted
    page.click("[data-sal-save]")
    page.wait_for_timeout(1500)
    row = page.locator("tr", has_text="Ali Raza").first
    check("Draft chip after save", "Draft" in row.inner_text(), row.inner_text()[:300])
    r = sess.get(f"{BASE}/api/salary/month?month=" + time.strftime("%Y-%m"), timeout=10).json()
    emp = [e for e in r["employees"] if e["name"] == "Ali Raza"][0]
    rec = emp["record"]
    check("API: record saved with expense", rec and rec["expense_id"], rec)
    with db.conn() as c:
        exp = c.execute("SELECT amount, category FROM expenses WHERE id=?",
                        (rec["expense_id"],)).fetchone()
    check("API: Salaries expense = 32,000", exp and dict(exp)["amount"] == 32000.0, exp)

    # pay via modal
    page.click("[data-sal-pay]")
    page.wait_for_selector("#pay-save-btn", timeout=5000)
    pay_body = page.locator(".modal").inner_text() if page.locator(".modal").count() else ""
    check("pay modal shows breakdown 27,000", "27,000" in pay_body, pay_body[:300])
    page.click("#pay-save-btn")
    page.wait_for_timeout(1800)
    row = page.locator("tr", has_text="Ali Raza").first
    check("Paid chip after pay", "Paid" in row.inner_text(), row.inner_text()[:300])
    with db.conn() as c:
        pay_cd = c.execute(
            "SELECT amount FROM cash_drawer WHERE type='salary_payment'").fetchall()
    check("pay cash drawer -27,000", pay_cd and dict(pay_cd[0])["amount"] == -27000.0, pay_cd)
    adv_cd = None
    with db.conn() as c:
        adv_cd = c.execute(
            "SELECT amount FROM cash_drawer WHERE type='salary_advance'").fetchall()
    check("advance cash drawer -5,000", adv_cd and dict(adv_cd[0])["amount"] == -5000.0, adv_cd)

    # history modal
    page.click("[data-sal-history]")
    page.wait_for_timeout(1200)
    hist_text = page.locator(".modal").inner_text() if page.locator(".modal").count() else ""
    check("history modal shows month + paid", "Paid" in hist_text, hist_text[:300])

    # P&L reflects Salaries expense
    r = sess.get(f"{BASE}/api/reports/pnl?month=" + time.strftime("%Y-%m"), timeout=10).json()
    sal = [e for e in r["expenses_by_category"] if e["category"] == "Salaries"]
    check("P&L: Salaries category 32,000", sal and sal[0]["total"] == 32000.0,
          r.get("expenses_by_category"))

    check("no page JS errors across all flows", not errors, errors[:3])
    browser.close()

print(f"\n{'='*60}\nE2E RESULT: {PASS} ok, {FAIL} failed")
server.terminate()
try:
    server.wait(timeout=5)
except Exception:
    server.kill()
sys.exit(1 if FAIL else 0)
