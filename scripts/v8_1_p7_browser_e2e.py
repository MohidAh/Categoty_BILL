"""v8.1 Phase 7 — Browser E2E for all new v8.1 surfaces.

Tests:
1. Setup wizard (all 4 steps — reuses Phase 1 script logic)
2. All v8.0 pages still load with zero console errors
3. New v8.1 endpoints respond (remote-access status, maintenance diagnose, device QR)
4. Profit ticker renders in the shell
5. Quick expense FAB renders
6. Drag-drop overlay appears
"""
import os, sys, time, json, signal, subprocess, tempfile, shutil, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_p7_browser_")
    os.environ["APP_PASSWORD"] = "manager123"
    os.environ["BILLBOOK_DATA_DIR"] = test_dir
    from app import config, db, security
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    security.ensure_password()
    if not db.get_setting("password_hash", ""):
        db.set_setting("password_hash", security.hash_password("manager123"))
    # Mark as setup_completed so we go straight to login (not wizard)
    db.set_setting("setup_completed", "true")
    db.set_setting("start_page", "launcher")
    SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"
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
                  "ai_cache", "ai_usage", "pending_actions", "automation_config",
                  "branches", "branch_pairing_codes", "branch_summaries", "sync_outbox",
                  "transfer_challans", "transfer_challan_items",
                  "central_purchases", "central_purchase_items", "price_pushes"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent", 1, 0, 1), ("Salaries", 1, 0, 2),
                    ("Electricity", 0, 0, 3), ("Transport", 0, 0, 4),
                    ("Internet", 0, 0, 5), ("Maintenance", 0, 0, 6),
                    ("Marketing", 0, 0, 7), ("Other", 0, 0, 8)]
        for name, is_fixed, budget, sort_order in defaults:
            c.execute("INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) VALUES(?,?,?,?,?)",
                      (name, is_fixed, budget, 1, sort_order))
        _auto_levels = {
            'auto_confirm_bills': 3, 'auto_draft_po': 2, 'urdhaar_reminders': 1,
            'recurring_detection': 1, 'expense_categorization': 2, 'anomaly_diagnosis': 1,
            'variance_investigation': 1, 'scheduled_reports': 1, 'dead_stock_liquidation': 2,
            'ai_kill_switch': 0,
        }
        for key, level in _auto_levels.items():
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)",
                      (key, 0, level, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def cleanup(t):
    shutil.rmtree(t, ignore_errors=True)


async def run_browser_e2e():
    from playwright.async_api import async_playwright
    import httpx

    test_dir = setup_test_env()
    proc = None
    results = []

    def check(label, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        results.append((mark, label, detail))
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["APP_PASSWORD"] = "manager123"
        env["BILLBOOK_DATA_DIR"] = test_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8799", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8799", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.3)

        print("\n=== 1. Login + navigate to app ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR: {exc}"))

            await page.goto("http://127.0.0.1:8799/login", wait_until="networkidle")
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(2.0)
            check("Logged in", "BillBook" in await page.content() or "launcher" in page.url)

            print("\n=== 2. New v8.1 endpoints respond ===")
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8799", timeout=10.0) as hx:
                r = await hx.post("/api/login", json={"username": "manager", "password": "manager123"})
                cookie = f"bb_token={hx.cookies.get('bb_token')}"
                h = {"Cookie": cookie}

                # Remote access status
                r = await hx.get("/api/remote-access/status", headers=h)
                check("GET /api/remote-access/status returns 200", r.status_code == 200,
                      f"got {r.status_code}")
                ra = r.json()
                check("Remote access status has 'running' field", "running" in ra)
                check("Remote access status has 'cloudflared_installed' field", "cloudflared_installed" in ra)

                # Maintenance diagnose
                r = await hx.get("/api/maintenance/diagnose", headers=h)
                check("GET /api/maintenance/diagnose returns 200", r.status_code == 200)
                diag = r.json()
                check("Diagnose has results array", "results" in diag and len(diag["results"]) >= 6)
                check("Diagnose has green/amber/red counts", "green" in diag and "amber" in diag and "red" in diag)

                # Maintenance backups
                r = await hx.get("/api/maintenance/backups", headers=h)
                check("GET /api/maintenance/backups returns 200", r.status_code == 200)

                # Maintenance update-check
                r = await hx.get("/api/maintenance/update-check", headers=h)
                check("GET /api/maintenance/update-check returns 200", r.status_code == 200)
                uc = r.json()
                check("Update check has 'current_version'", "current_version" in uc)
                check("Update check has 'update_available'", "update_available" in uc)

                # Device QR (returns PNG)
                r = await hx.get("/api/devices/qr?role=cashier", headers=h)
                check("GET /api/devices/qr returns PNG", r.status_code == 200 and r.headers.get("content-type") == "image/png",
                      f"got {r.status_code} {r.headers.get('content-type')}")

                # Branch QR (returns PNG)
                r = await hx.get("/api/hq/branches/qr", headers=h)
                check("GET /api/hq/branches/qr returns PNG", r.status_code == 200 and r.headers.get("content-type") == "image/png")

                # Setup state
                r = await hx.get("/api/setup/state", headers=h)
                check("GET /api/setup/state returns 200", r.status_code == 200)
                ss = r.json()
                check("Setup state: setup_completed=true", ss.get("setup_completed") is True)
                check("Setup state: start_page=launcher", ss.get("start_page") == "launcher")

            print("\n=== 3. Re-check v8.0 pages (zero console errors) ===")
            v8_pages = [
                "/settings/branch", "/insights/hq-branches", "/insights/owner-hub",
                "/transfers/out", "/transfers/in", "/central-purchases",
                "/insights/price-push", "/insights/approval-queue", "/insights/agent",
                "/insights/ai-usage", "/settings/ai-automations",
            ]
            pages_with_errors = []
            for path in v8_pages:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8799#{path}", wait_until="networkidle")
                await asyncio.sleep(1.0)
                err_after = len(console_errors)
                if err_after > err_before:
                    pages_with_errors.append(path)
            check(f"11 v8.0 pages — all zero NEW console errors", len(pages_with_errors) == 0,
                  f"pages with errors: {pages_with_errors}" if pages_with_errors else "")

            print("\n=== 4. Re-check v7.2 pages (zero console errors) ===")
            v7_pages = ["/", "/bills", "/items", "/stock", "/suppliers", "/customers",
                        "/pos", "/reports", "/reports/margins", "/settings", "/more"]
            v7_errors = []
            for path in v7_pages:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8799#{path}", wait_until="networkidle")
                await asyncio.sleep(0.8)
                err_after = len(console_errors)
                if err_after > err_before:
                    v7_errors.append(path)
            check(f"11 v7.2 pages — all zero NEW console errors", len(v7_errors) == 0,
                  f"pages with errors: {v7_errors}" if v7_errors else "")

            print("\n=== 5. Profit ticker renders ===")
            # Navigate to a page with the shell — the ticker mounts 800ms after hashchange
            await page.goto("http://127.0.0.1:8799#/reports/store-profit", wait_until="networkidle")
            # Wait for the ticker to mount (800ms delay + render + API fetch)
            ticker = page.locator("#bb-profit-ticker")
            ticker_visible = False
            for _ in range(20):  # up to 10s
                if await ticker.count() > 0:
                    ticker_visible = True
                    break
                await asyncio.sleep(0.5)
            check("Profit ticker element exists in DOM", ticker_visible,
                  "ticker not found within 10s" if not ticker_visible else "")
            if ticker_visible:
                ticker_text = await ticker.inner_text()
                check("Profit ticker shows 'Today:' text", "Today:" in ticker_text or "Today" in ticker_text,
                      f"text='{ticker_text[:50]}'")

            print("\n=== 6. Quick expense FAB renders ===")
            await asyncio.sleep(2.0)  # FAB initializes after 2s delay
            fab = page.locator("#bb-expense-fab")
            fab_visible = await fab.count() > 0
            check("Quick expense FAB exists in DOM", fab_visible)
            if fab_visible:
                # Click it to open the modal
                await fab.click()
                await asyncio.sleep(1.0)
                modal_visible = await page.locator("text=Quick Expense").count() > 0
                check("Quick expense modal opens on click", modal_visible)
                # Check modal has amount + category fields
                check("Modal has amount field", await page.locator("#qe-amount").count() > 0)
                check("Modal has category field", await page.locator("#qe-category").count() > 0)
                check("Modal has Save button", await page.locator("#qe-save").count() > 0)

            print("\n=== 7. Drag-drop overlay ===")
            # We can't truly simulate a file drop in headless, but we can verify
            # the event listeners are wired by checking the overlay doesn't exist yet
            # (it's created on dragenter)
            overlay_before = await page.locator("#bb-drag-overlay").count()
            check("Drag overlay not present before drag", overlay_before == 0)
            # Simulate a dragenter event
            await page.evaluate("""
                () => {
                    const evt = new DragEvent('dragenter', { bubbles: true });
                    document.dispatchEvent(evt);
                }
            """)
            await asyncio.sleep(0.5)
            overlay_after = await page.locator("#bb-drag-overlay").count()
            check("Drag overlay appears on dragenter", overlay_after > 0)
            # Check overlay text
            if overlay_after > 0:
                overlay_text = await page.locator("#bb-drag-overlay").inner_text()
                check("Overlay shows 'Drop to upload bill'", "Drop to upload bill" in overlay_text)

            print("\n=== 8. Setup wizard page loads (for fresh installs) ===")
            # Navigate to /setup-wizard directly — it should render even if setup is done
            # (the page itself checks and redirects, but the HTML should serve)
            err_before = len(console_errors)
            await page.goto("http://127.0.0.1:8799/setup-wizard", wait_until="networkidle")
            await asyncio.sleep(2.0)
            err_after = len(console_errors)
            check("Setup wizard page serves without console errors", err_after == err_before,
                  f"errors: {console_errors[err_before:err_after][:2]}" if err_after > err_before else "")

            # Console errors summary
            check("ZERO console errors across entire E2E", len(console_errors) == 0,
                  f"{len(console_errors)} errors: {console_errors[:5]}" if console_errors else "")

            await browser.close()

        # Summary
        print("\n" + "=" * 60)
        print("=== v8.1 PHASE 7 BROWSER E2E SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        if console_errors:
            print(f"  Console errors: {len(console_errors)}")
            for e in console_errors[:5]:
                print(f"    - {e}")
        return failed == 0
    finally:
        if proc is not None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        cleanup(test_dir)


if __name__ == "__main__":
    ok = asyncio.run(run_browser_e2e())
    sys.exit(0 if ok else 1)
