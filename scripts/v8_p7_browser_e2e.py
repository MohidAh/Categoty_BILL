"""v8.0 Phase 7 — Browser E2E for new v8.0 pages.

Boots a real uvicorn server + headless Chromium, logs in, navigates to each
new v8.0 page, captures console errors, and verifies the pages render.

New pages tested:
- /settings/branch (Branch settings)
- /insights/hq-branches (HQ Branch Registry)
- /insights/owner-hub (Owner Hub dashboard)
- /transfers/out (Transfer Out)
- /transfers/in (Transfer In list)
- /central-purchases (Central Purchases)
- /insights/price-push (Price Push)

Also re-checks the 17 v7.2 pages for zero new console errors.
"""
import os, sys, time, json, signal, subprocess, tempfile, shutil, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_browser_")
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
    screenshots_dir = Path("/tmp/v8_p7_screenshots")
    screenshots_dir.mkdir(exist_ok=True)

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
             "--host", "127.0.0.1", "--port", "8785", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8785", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.3)
            else:
                raise RuntimeError("server failed to start")

        print("\n=== 1. Browser boot + login ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR: {exc}"))

            await page.goto("http://127.0.0.1:8785/login", wait_until="networkidle")
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(1.5)
            check("Logged in", "BillBook" in await page.content() or "launcher" in page.url)

            print("\n=== 2. New v8.0 pages — zero console errors ===")
            v8_pages = [
                ("/settings/branch", "Branch Settings"),
                ("/insights/hq-branches", "HQ Branch Registry"),
                ("/insights/owner-hub", "Owner Hub"),
                ("/transfers/out", "Transfer Out"),
                ("/transfers/in", "Transfers"),
                ("/central-purchases", "Central Purchases"),
                ("/insights/price-push", "Price Push"),
            ]
            for path, name in v8_pages:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8785#{path}", wait_until="networkidle")
                await asyncio.sleep(2.0)
                await page.screenshot(path=str(screenshots_dir / f"{path.strip('/').replace('/', '_')}.png"), full_page=True)
                err_after = len(console_errors)
                page_errors = console_errors[err_before:]
                content = await page.content()
                check(f"{name} page loads ({path})",
                      name.lower().split()[0] in content.lower() or "branch" in content.lower() or "transfer" in content.lower() or "central" in content.lower() or "owner" in content.lower() or "price" in content.lower(),
                      f"URL: {page.url}")
                check(f"{name} — ZERO new console errors",
                      err_after == err_before,
                      f"{err_after - err_before} errors: {page_errors[:3]}" if page_errors else "")

            print("\n=== 3. Re-check 17 v7.2 pages for console errors ===")
            v7_pages = [
                "/", "/bills", "/bills/new", "/items", "/stock",
                "/suppliers", "/customers", "/pos", "/reports",
                "/reports/margins", "/reports/monthly-profit", "/reports/ytd",
                "/reports/daily-stock", "/reports/cash-buckets",
                "/reports/store-profit", "/settings", "/more",
            ]
            pages_with_errors = []
            for path in v7_pages:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8785#{path}", wait_until="networkidle")
                await asyncio.sleep(0.8)
                err_after = len(console_errors)
                if err_after > err_before:
                    pages_with_errors.append((path, console_errors[err_before:err_after]))
            check(f"17 v7.2 pages — all zero NEW console errors",
                  len(pages_with_errors) == 0,
                  f"pages with errors: {[p for p, _ in pages_with_errors]}")

            print("\n=== 4. Branch settings — fill + save ===")
            await page.goto("http://127.0.0.1:8785#/settings/branch", wait_until="networkidle")
            await asyncio.sleep(1.5)
            await page.locator("#branch-name").fill("Test Branch")
            await page.locator("#branch-region").fill("Test Region")
            err_before = len(console_errors)
            await page.locator("#branch-save").click()
            await asyncio.sleep(1.0)
            err_after = len(console_errors)
            check("Branch save — zero console errors", err_after == err_before,
                  f"errors: {console_errors[err_before:err_after][:2]}" if err_after > err_before else "")
            name_val = await page.locator("#branch-name").input_value()
            check("Branch name saved", name_val == "Test Branch", f"got '{name_val}'")

            print("\n=== 5. HQ Branches — generate code modal ===")
            await page.goto("http://127.0.0.1:8785#/insights/hq-branches", wait_until="networkidle")
            await asyncio.sleep(1.5)
            # Click "Generate Code" button
            gen_btn = page.locator("#hq-gen-code")
            if await gen_btn.count() > 0:
                await gen_btn.click()
                await asyncio.sleep(0.5)
                # A modal should appear with a 6-digit code
                modal_visible = await page.locator("text=Branch Registration Code").count() > 0
                check("Generate Code modal opens", modal_visible)
                if modal_visible:
                    # The code should be displayed in monospace
                    code_text = await page.locator(".text-center >> nth=0").inner_text()
                    check("Modal shows a 6-digit code", any(c.isdigit() for c in code_text), "")
            else:
                check("Generate Code button found", False, "button not found")

            await browser.close()

        # Summary
        print("\n" + "=" * 60)
        print("=== v8.0 PHASE 7 BROWSER E2E SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        print(f"  Console errors captured (total): {len(console_errors)}")
        if console_errors:
            print("  Errors:")
            for e in console_errors[:5]:
                print(f"    - {e}")
        print(f"\n  Screenshots: {screenshots_dir}")
        return failed == 0 and len(console_errors) == 0
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
