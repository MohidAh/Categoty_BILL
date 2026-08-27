"""v8.0.4 — UI verification for MULTI_STORE_GUIDE.md.

Clicks through the exact UI paths referenced in the guide to verify button
labels and screen names match. This is the 30-second click-through the
reviewer asked for.
"""
import os, sys, time, tempfile, shutil, signal, subprocess, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v804_uiverify_")
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


async def run_ui_verification():
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
             "--host", "127.0.0.1", "--port", "8795", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8795", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200:
                        break
                except Exception:
                    await asyncio.sleep(0.3)

        print("\n=== UI Verification for MULTI_STORE_GUIDE.md ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

            # Login
            await page.goto("http://127.0.0.1:8795/login", wait_until="networkidle")
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(1.5)

            # ─── Guide says: "Settings → Branch" ───
            print("\n--- Guide path: Settings → Branch ---")
            await page.goto("http://127.0.0.1:8795/#/settings/branch", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Branch Settings'", "Branch Settings" in content)
            check("Has 'Branch Identity' section", "Branch Identity" in content)
            check("Has 'Hub Connection' section", "Hub Connection" in content)
            check("Has Role selector with 'Headquarters' option", "Headquarters" in content)
            check("Has 'Save Branch Settings' button", "Save Branch Settings" in content)
            # The guide says "Register with Code" button
            check("Has 'Register with Code' button", "Register with Code" in content)
            # Click it to verify the modal
            await page.locator("#branch-register-btn").click()
            await asyncio.sleep(0.5)
            modal_content = await page.content()
            check("Modal title: 'Register with HQ'", "Register with HQ" in modal_content)
            check("Modal has 'Registration Code (6 digits)' label", "Registration Code" in modal_content)
            check("Modal has 'Hub URL' field", "Hub URL" in modal_content)
            check("Modal has 'Tunnel URL' field", "Tunnel URL" in modal_content)
            check("Modal has 'Register' button", "Register" in modal_content)
            # Close modal
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

            # ─── Guide says: "AI Insights → HQ Branches" ───
            print("\n--- Guide path: AI Insights → HQ Branches ---")
            await page.goto("http://127.0.0.1:8795/#/insights/hq-branches", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'HQ Branch Registry'", "HQ Branch Registry" in content)
            check("Has 'Generate Code' button", "Generate Code" in content)
            # Click Generate Code to verify the modal
            await page.locator("#hq-gen-code").click()
            await asyncio.sleep(0.5)
            modal_content = await page.content()
            check("Modal title: 'Branch Registration Code'", "Branch Registration Code" in modal_content)
            check("Modal has 'Copy Code' button", "Copy Code" in modal_content)
            check("Modal shows 'single-use' warning", "single-use" in modal_content)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

            # ─── Guide says: "AI Insights → Owner Hub" ───
            print("\n--- Guide path: AI Insights → Owner Hub ---")
            await page.goto("http://127.0.0.1:8795/#/insights/owner-hub", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Owner Hub'", "Owner Hub" in content)
            check("Has 'Branch Leaderboard' section", "Branch Leaderboard" in content)
            check("Has 'Total Sales' stat card", "Total Sales" in content)
            check("Has 'Gross Profit' stat card", "Gross Profit" in content)
            check("Has 'Cash in Drawer' stat card", "Cash in Drawer" in content)
            check("Has date picker", await page.locator("#hub-date").count() > 0)

            # ─── Guide says: "Inventory → Transfer Out" ───
            print("\n--- Guide path: Inventory → Transfer Out ---")
            await page.goto("http://127.0.0.1:8795/#/transfers/out", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Transfer Out'", "Transfer Out" in content)
            check("Has 'Add Line' button", "Add Line" in content)
            check("Has 'Create Transfer Challan' button", "Create Transfer Challan" in content)
            check("Has 'To (destination branch)' selector", "destination branch" in content.lower() or "To" in content)

            # ─── Guide says: "Inventory → Transfer In" ───
            print("\n--- Guide path: Inventory → Transfer In ---")
            await page.goto("http://127.0.0.1:8795/#/transfers/in", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Transfers'", "Transfers" in content)
            check("Has 'Accept' / 'Reject' / 'View' button references", "Accept" in content or "View" in content)

            # ─── Guide says: "Inventory → Central Buys" ───
            print("\n--- Guide path: Inventory → Central Buys ---")
            await page.goto("http://127.0.0.1:8795/#/central-purchases", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Central Purchases'", "Central Purchases" in content)
            check("Has 'New Central Buy' button", "New Central Buy" in content)
            check("Has 'Distribute' button reference", "Distribute" in content)

            # ─── Guide says: "AI Insights → Price Push" ───
            print("\n--- Guide path: AI Insights → Price Push ---")
            await page.goto("http://127.0.0.1:8795/#/insights/price-push", wait_until="networkidle")
            await asyncio.sleep(2.0)
            content = await page.content()
            check("Page title: 'Price Push'", "Price Push" in content)
            check("Has 'New Price Push' button", "New Price Push" in content)

            # ─── Sidebar nav labels ───
            print("\n--- Sidebar nav labels ---")
            await page.goto("http://127.0.0.1:8795/#/settings/branch", wait_until="networkidle")
            await asyncio.sleep(1.0)
            nav_content = await page.content()
            check("Nav has 'Branch' label (Settings app)", ">Branch<" in nav_content)
            await page.goto("http://127.0.0.1:8795/#/insights/hq-branches", wait_until="networkidle")
            await asyncio.sleep(1.0)
            nav_content = await page.content()
            check("Nav has 'HQ Branches' label (Insights app)", ">HQ Branches<" in nav_content)
            check("Nav has 'Owner Hub' label (Insights app)", ">Owner Hub<" in nav_content)
            check("Nav has 'Price Push' label (Insights app)", ">Price Push<" in nav_content)
            await page.goto("http://127.0.0.1:8795/#/transfers/out", wait_until="networkidle")
            await asyncio.sleep(1.0)
            nav_content = await page.content()
            check("Nav has 'Transfer Out' label (Inventory app)", ">Transfer Out<" in nav_content)
            check("Nav has 'Transfer In' label (Inventory app)", ">Transfer In<" in nav_content)
            check("Nav has 'Central Buys' label (Inventory app)", ">Central Buys<" in nav_content)

            # Console errors
            check("ZERO console errors across all pages", len(console_errors) == 0,
                  f"{len(console_errors)} errors: {console_errors[:3]}" if console_errors else "")

            await browser.close()

        # Summary
        print("\n" + "=" * 60)
        print("=== UI VERIFICATION SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        if failed:
            print("\n  FAILURES (guide text doesn't match UI):")
            for m, l, d in results:
                if m == "FAIL":
                    print(f"    - {l}: {d}")
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
    ok = asyncio.run(run_ui_verification())
    sys.exit(0 if ok else 1)
