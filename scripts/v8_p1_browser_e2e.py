"""v8.0 Phase 1 — Browser E2E for the new Branch settings page.

Boots real uvicorn + headless Chromium, logs in, navigates to /settings/branch,
verifies zero console errors + that the page renders with the expected sections.
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
                  "ai_cache", "ai_usage", "pending_actions", "automation_config"):
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
    screenshots_dir = Path("/tmp/v8_p1_screenshots")
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
             "--host", "127.0.0.1", "--port", "8773", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8773", timeout=10.0) as hx:
            for _ in range(30):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.3)
            else:
                raise RuntimeError("server failed to start")

        print("\n=== 1. Browser boot + login via form ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR: {exc}"))

            await page.goto("http://127.0.0.1:8773/login", wait_until="networkidle")
            check("Login page renders", "BillBook" in await page.content())
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(1.5)
            check("Logged in", "BillBook" in await page.content() or "launcher" in page.url)

            print("\n=== 2. Navigate to /settings/branch ===")
            err_before = len(console_errors)
            await page.goto("http://127.0.0.1:8773/#/settings/branch", wait_until="networkidle")
            await asyncio.sleep(2.5)
            await page.screenshot(path=str(screenshots_dir / "settings_branch.png"), full_page=True)
            err_after = len(console_errors)
            page_errors = console_errors[err_before:]
            content = await page.content()
            check("Branch page header visible", "Branch Settings" in content)
            check("Branch Identity section visible", "Branch Identity" in content)
            check("Hub Connection section visible", "Hub Connection" in content)
            check("Single-shop mode banner visible", "Single-shop mode" in content)
            check("Save button visible", "Save Branch Settings" in content)
            check("Branch page has ZERO console errors",
                  err_after == err_before,
                  f"{err_after - err_before} errors: {page_errors[:3]}" if page_errors else "")

            print("\n=== 3. Fill form + save ===")
            await page.locator("#branch-name").fill("Lahore Branch")
            await page.locator("#branch-region").fill("Punjab")
            err_before = len(console_errors)
            await page.locator("#branch-save").click()
            await asyncio.sleep(1.5)
            err_after = len(console_errors)
            page_errors = console_errors[err_before:]
            check("Save click produced zero console errors",
                  err_after == err_before,
                  f"{err_after - err_before} errors: {page_errors[:3]}" if page_errors else "")
            # Verify the name was saved (input should still show the new value)
            name_val = await page.locator("#branch-name").input_value()
            check("Branch name saved to UI", name_val == "Lahore Branch", f"got '{name_val}'")

            print("\n=== 4. Re-check a few v7.2 pages for console errors ===")
            for path in ["/settings", "/settings/ai-automations", "/insights/approval-queue"]:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8773#{path}", wait_until="networkidle")
                await asyncio.sleep(1.0)
                err_after = len(console_errors)
                check(f"{path} — zero new console errors", err_after == err_before,
                      f"errors: {console_errors[err_before:err_after][:2]}" if err_after > err_before else "")

            print("\n=== 5. Re-verify Branch page after navigation back ===")
            err_before = len(console_errors)
            await page.goto("http://127.0.0.1:8773/#/settings/branch", wait_until="networkidle")
            await asyncio.sleep(2.0)
            err_after = len(console_errors)
            check("Branch page still loads clean after nav", err_after == err_before,
                  f"errors: {console_errors[err_before:err_after][:2]}" if err_after > err_before else "")
            name_val2 = await page.locator("#branch-name").input_value()
            check("Branch name persisted across navigation", name_val2 == "Lahore Branch", f"got '{name_val2}'")

            await browser.close()

        # Summary
        print("\n" + "=" * 60)
        print("=== v8.0 PHASE 1 BROWSER E2E SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        print(f"  Console errors captured (total): {len(console_errors)}")
        if console_errors:
            print("  Errors:")
            for e in console_errors[:5]:
                print(f"    - {e}")
        print(f"\n  Screenshot: {screenshots_dir}/settings_branch.png")
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
