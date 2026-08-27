"""v8.1 Phase 1 — Browser E2E for the setup wizard.

Boots a fresh DB (no password), opens /setup-wizard in headless Chromium,
walks through all 4 steps, and verifies zero console errors + correct redirect.
"""
import os, sys, time, tempfile, shutil, signal, subprocess, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_fresh_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_browser_")
    os.environ["APP_PASSWORD"] = ""
    os.environ["BILLBOOK_DATA_DIR"] = test_dir
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    # Clear password + setup_completed so the wizard appears
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key IN ('password_hash', 'setup_completed', 'start_page')")
        c.execute("DELETE FROM price_categories")
    return test_dir


def cleanup(t):
    shutil.rmtree(t, ignore_errors=True)


async def run_browser_e2e():
    from playwright.async_api import async_playwright
    import httpx

    test_dir = setup_fresh_env()
    proc = None
    results = []

    def check(label, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        results.append((mark, label, detail))
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["APP_PASSWORD"] = ""
        env["BILLBOOK_DATA_DIR"] = test_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8798", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # Wait for server
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8798", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.3)

        print("\n=== 1. Fresh DB → /login redirects to /setup-wizard ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR: {exc}"))

            # Go to login — should redirect to wizard
            await page.goto("http://127.0.0.1:8798/login", wait_until="networkidle")
            await asyncio.sleep(2.0)
            check("Login page redirects to /setup-wizard", "/setup-wizard" in page.url,
                  f"URL: {page.url}")

            print("\n=== 2. Wizard Step 1 — Set Password ===")
            await page.screenshot(path="/tmp/v81_wizard_step1.png", full_page=True)
            content = await page.content()
            check("Step 1 title visible", "Set Your Password" in content)
            check("Password field visible", await page.locator("#w-pw").count() > 0)
            check("Confirm field visible", await page.locator("#w-pw2").count() > 0)
            check("Strength meter visible", await page.locator("#w-strength").count() > 0)
            # Fill password
            await page.locator("#w-pw").fill("mysecret123")
            await page.locator("#w-pw2").fill("mysecret123")
            check("Strength meter updates", await page.locator("#w-strength-label").inner_text() != "Enter a password")
            err_before = len(console_errors)
            await page.locator("#w-next").click()
            await asyncio.sleep(0.5)
            check("Step 1 → 2 (Next click) zero console errors", len(console_errors) == err_before)

            print("\n=== 3. Wizard Step 2 — Business Type ===")
            await page.screenshot(path="/tmp/v81_wizard_step2.png", full_page=True)
            content = await page.content()
            check("Step 2 title visible", "Business Type" in content)
            check("Wholesale card visible", "Wholesale" in content)
            check("Retail card visible", "Retail" in content)
            check("Custom card visible", "Custom" in content)
            # Select wholesale (should be default)
            await page.locator(".biz-card[data-biz='wholesale']").click()
            await asyncio.sleep(0.3)
            err_before = len(console_errors)
            await page.locator("#w-next").click()
            await asyncio.sleep(0.5)
            check("Step 2 → 3 zero console errors", len(console_errors) == err_before)

            print("\n=== 4. Wizard Step 3 — Confirm Categories ===")
            await page.screenshot(path="/tmp/v81_wizard_step3.png", full_page=True)
            content = await page.content()
            check("Step 3 title visible", "Confirm Categories" in content)
            check("Category rows visible", await page.locator(".cat-row").count() >= 4)
            check("Add Category button visible", "Add Category" in content)
            # Edit a category price
            cat_inputs = page.locator(".cat-row input[data-cat-f='sell_price']")
            await cat_inputs.first.fill("999")
            await asyncio.sleep(0.3)
            err_before = len(console_errors)
            await page.locator("#w-next").click()
            await asyncio.sleep(0.5)
            check("Step 3 → 4 zero console errors", len(console_errors) == err_before)

            print("\n=== 5. Wizard Step 4 — Optional AI + Finish ===")
            await page.screenshot(path="/tmp/v81_wizard_step4.png", full_page=True)
            content = await page.content()
            check("Step 4 title visible", "Optional AI + Finish" in content)
            check("Gemini key field visible", "Gemini API Key" in content)
            check("Start Page options visible", "Launcher" in content and "Dashboard" in content and "POS" in content)
            check("Finish button visible", "Finish Setup" in content)
            # Select Dashboard start page
            await page.locator("input[name='start'][value='dashboard']").click()
            await asyncio.sleep(0.3)
            # Finish
            err_before = len(console_errors)
            await page.locator("#w-finish").click()
            await asyncio.sleep(3.0)
            check("Finish click zero console errors", len(console_errors) == err_before)

            print("\n=== 6. Verify redirect + setup completed ===")
            # Should redirect to the app (dashboard)
            check("Redirected to app (not still on wizard)", "/setup-wizard" not in page.url,
                  f"URL: {page.url}")
            # Verify via API that setup is complete
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8798", timeout=10.0) as hx:
                r = await hx.get("/api/setup/state")
                state = r.json()
                check("API: setup_completed=true", state["setup_completed"] is True)
                check("API: initialized=true", state["initialized"] is True)
                check("API: has_categories=true", state["has_categories"] is True)
                check("API: category_count=4", state["category_count"] == 4, f"got {state['category_count']}")
                check("API: start_page=dashboard", state["start_page"] == "dashboard")

            print("\n=== 7. Re-launch → wizard skipped, login shown ===")
            # Navigate to /setup-wizard directly — should redirect to login (since setup is done)
            await page.goto("http://127.0.0.1:8798/login", wait_until="networkidle")
            await asyncio.sleep(2.0)
            # Should stay on login (not redirect to wizard)
            check("Re-launch: login page shown (not wizard)", "/setup-wizard" not in page.url,
                  f"URL: {page.url}")
            check("Login form visible", "Enter your password" in await page.content())

            # Console errors summary
            check("ZERO console errors across entire wizard flow", len(console_errors) == 0,
                  f"{len(console_errors)} errors: {console_errors[:3]}" if console_errors else "")

            await browser.close()

        # Summary
        print("\n" + "=" * 60)
        print("=== v8.1 PHASE 1 BROWSER E2E SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
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
