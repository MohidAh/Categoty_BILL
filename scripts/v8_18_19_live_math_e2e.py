#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════
# v8.18.19 — LIVE MATH browser E2E (Playwright)
#
# Verifies the feature end-to-end in the REAL UI:
#   "users doubt the calculation system — click on margin and it shows
#    the whole live math and how it's calculating"
#
# Flow: /reports/margins hero + row cells → modal with steps, the
# substituted expression and the provenance; avg_cost event replay;
# monthly-profit, ytd, pnl, store-profit, stock pages all carry
# clickable values; zero page JS errors.
#
# License note: same E2E-only wrapper as v8_18_10_reorder_e2e.py
# (private key lost with the workspace snapshot). No production file
# is touched.
#
# Run: python scripts/v8_18_19_live_math_e2e.py
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

# ── 1. temp data dir + DB + password + sample data ───────────────────
data_dir = tempfile.mkdtemp(prefix="bb_lm_e2e_")
os.environ["BILLBOOK_DATA_DIR"] = data_dir
sys.path.insert(0, str(PROJ))
from app import db
from app.security import hash_password
from app.profit_engine import rebuild_stock_state

db.init()
with db.conn() as c:
    for t in ("sale_items", "sales", "bill_items", "bills", "customers",
              "price_categories", "suppliers", "stock_adjustments",
              "category_stock_state", "expenses"):
        c.execute(f"DELETE FROM {t}")
    with open(SAMPLE_SQL) as f:
        c.executescript(f.read())
    c.execute("DELETE FROM settings WHERE key='password_hash'")
    c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
              ("password_hash", hash_password("testpass")))
rebuild_stock_state()
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

# ── 3. API sanity ────────────────────────────────────────────────────
import requests
sess = requests.Session()
r0 = sess.post(f"{BASE}/api/login", json={"password": "testpass"}, timeout=10)
check("API login ok", r0.status_code == 200, r0.text[:200])
r = sess.get(f"{BASE}/api/calc/trace?metric=overall_margin", timeout=10)
body = r.json()
check("trace API 200", r.status_code == 200, r.text[:200])
check("trace result = 57.48 (matches get_margins)", body.get("result") == 57.48, body.get("result"))
check("trace has 4 steps", len(body.get("steps", [])) == 4, len(body.get("steps", [])))
r = sess.get(f"{BASE}/api/calc/trace?metric=avg_cost&category_id=1", timeout=10)
ev = r.json().get("events") or []
check("avg_cost replay has events", len(ev) == 2, len(ev))
check("avg_cost replay matches state", r.json().get("replay_matches_state") is True)

# ── 4. browser: the REAL pages ───────────────────────────────────────
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    def _on_pageerror(e):
        errors.append(f"{e}\n{getattr(e, 'stack', '')[:600]}")
    page.on("pageerror", _on_pageerror)

    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("#p", "testpass")
    page.click(".login-btn")
    page.wait_for_selector(".launcher-root", timeout=20000)
    page.wait_for_timeout(400)
    check("login → launcher", True)

    # ---- /reports/margins ----
    page.goto(f"{BASE}/#/reports/margins")
    page.wait_for_selector("#m-out .card", timeout=20000)
    page.wait_for_timeout(600)
    n_clickable = page.locator(".live-math").count()
    check("margins page: clickable values present", n_clickable >= 10,
          f"only {n_clickable} live-math buttons")

    page.locator(".live-math", has_text="57.5").first.click()
    page.wait_for_selector(".lm-step-expr", timeout=10000)
    page.wait_for_timeout(300)
    steps = page.locator(".lm-step").count()
    check("overall margin modal: 4 steps", steps == 4, f"steps={steps}")
    modal_text = page.locator(".modal").inner_text()
    check("modal shows the substituted expression",
          "9,570.00 ÷ 16,650.00 × 100" in modal_text, modal_text[:300])
    check("modal shows the result badge", "57.48%" in modal_text, modal_text[:120])
    check("modal shows provenance (sales counted)", "Sales counted (valid)" in modal_text,
          modal_text[:400])
    check("modal has copy button", page.locator("#lm-copy").count() == 1)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("modal closes on Esc", page.locator(".lm-step").count() == 0)

    # per-category margin cell → category trace (A: sell 250 cost 80 → 68%)
    page.locator(".live-math", has_text="68.0").first.click()
    page.wait_for_selector(".lm-step-expr", timeout=10000)
    page.wait_for_timeout(400)
    modal_text = page.locator(".modal").inner_text()
    check("category margin modal: pool math shown",
          "3,120.00" in modal_text and "39.00" in modal_text, modal_text[:400])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # avg cost cell → event replay table
    page.locator(".live-math", has_text="Rs 80").first.click()
    page.wait_for_selector(".lm-events", timeout=10000)
    replay_rows = page.locator(".lm-events tbody tr").count()
    check("avg cost modal: replay table with 2 events", replay_rows == 2, replay_rows)
    check("avg cost modal: replay matches note visible",
          "Replay matches the stored stock state" in page.locator(".modal").inner_text())
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # ---- /reports/monthly-profit ----
    page.goto(f"{BASE}/#/reports/monthly-profit")
    page.wait_for_selector("#mp-out .card, #mp-out .card .text-dim", timeout=20000)
    page.fill("#mp-month", "2026-08")
    page.dispatch_event("#mp-month", "change")
    page.wait_for_timeout(800)
    page.locator(".live-math", has_text="54.8").first.click()
    page.wait_for_selector(".lm-step-expr", timeout=10000)
    page.wait_for_timeout(300)
    mt = page.locator(".modal").inner_text()
    check("monthly modal: COGS bridge steps", "Opening Inventory" in mt and "COGS Bridge" not in mt, mt[:200])
    check("monthly modal: bridge expression", "13,750.00" in mt, mt[:400])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # ---- /reports/ytd ----
    page.goto(f"{BASE}/#/reports/ytd")
    page.wait_for_selector("#ytd-out", timeout=20000)
    page.wait_for_timeout(700)
    page.locator(".live-math", has_text="54.8").first.click()
    page.wait_for_selector(".lm-step-expr", timeout=10000)
    page.wait_for_timeout(300)
    yt = page.locator(".modal").inner_text()
    check("ytd modal: cumulative method shown", "Cumulative" in yt, yt[:300])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # ---- /reports/pnl (month input → 2026-08 first) ----
    page.goto(f"{BASE}/#/reports/pnl")
    page.wait_for_selector("#pnl-out", timeout=20000)
    page.fill("#pnl-month", "2026-08")
    page.dispatch_event("#pnl-month", "change")
    page.wait_for_timeout(800)
    page.locator(".live-math", has_text="margin").first.click()
    page.wait_for_selector(".lm-step-expr", timeout=10000)
    page.wait_for_timeout(300)
    pt = page.locator(".modal").inner_text()
    check("pnl modal: net revenue / COGS steps", "Net Revenue" in pt and "Cost of Goods Sold" in pt,
          pt[:300])
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    # ---- /reports/store-profit ----
    page.goto(f"{BASE}/#/reports/store-profit")
    page.wait_for_selector("#sp-out .card", timeout=20000)
    page.wait_for_timeout(700)
    n_sp = page.locator(".live-math").count()
    check("store dashboard: clickable values", n_sp >= 8, f"only {n_sp}")

    # ---- /stock (current stock avg cost) ----
    page.goto(f"{BASE}/#/stock")
    page.wait_for_selector("#st-table", timeout=20000)
    page.wait_for_timeout(600)
    page.locator(".live-math", has_text="Rs 80").first.click()
    page.wait_for_selector(".lm-events", timeout=10000)
    check("stock page: avg cost clickable → replay", True)
    page.keyboard.press("Escape")

    check("no page JS errors anywhere", not errors, errors[:3])
    browser.close()

print(f"\n{'='*50}")
print(f"RESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
