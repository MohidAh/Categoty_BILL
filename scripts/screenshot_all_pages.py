"""v8.2.5 — Screenshot every page in the system.

Boots a real uvicorn server, logs in, navigates to every page,
captures a full-page screenshot, and saves them to /home/z/my-project/download/screenshots/
"""
import os, sys, time, tempfile, shutil, signal, subprocess, asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_screens_")
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
    db.set_setting("setup_completed", "true")
    db.set_setting("start_page", "launcher")
    SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"
    with db.conn() as c:
        for t in ("sale_items","sales","bill_items","bills","customers","price_categories",
                  "suppliers","stock_adjustments","activity_log","sessions","expenses",
                  "expense_categories","recurring_expenses","cash_drawer","shifts","employees",
                  "category_stock_state","owner_withdrawals","login_attempts","devices",
                  "pairing_codes","bundles","bundle_items","price_rules","lost_sales",
                  "closed_days","seasons","ai_cache","ai_usage","pending_actions",
                  "automation_config","branches","branch_pairing_codes","branch_summaries",
                  "sync_outbox","transfer_challans","transfer_challan_items",
                  "central_purchases","central_purchase_items","price_pushes",
                  "audit_runs","audit_findings","bill_intelligence","ezi_pos_imports",
                  "pos_expense_imports"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        defaults = [("Rent",1,0,1),("Salaries",1,0,2),("Electricity",0,0,3),
                    ("Transport",0,0,4),("Internet",0,0,5),("Maintenance",0,0,6),
                    ("Marketing",0,0,7),("Other",0,0,8)]
        for name,is_fixed,budget,sort_order in defaults:
            c.execute("INSERT INTO expense_categories(name,is_fixed,budget_monthly,active,sort_order) VALUES(?,?,?,?,?)",
                      (name,is_fixed,budget,1,sort_order))
        _auto_levels = {'auto_confirm_bills':3,'auto_draft_po':2,'urdhaar_reminders':1,
                    'recurring_detection':1,'expense_categorization':2,'anomaly_diagnosis':1,
                    'variance_investigation':1,'scheduled_reports':1,'dead_stock_liquidation':2,
                    'ai_kill_switch':0}
        for key,level in _auto_levels.items():
            c.execute("INSERT OR REPLACE INTO automation_config(key,enabled,level,params_json) VALUES(?,?,?,?)",
                      (key,0,level,'{}'))
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'test')")
        c.execute("INSERT INTO employees(name, role, pin, active) VALUES('Test Manager', 'manager', '1234', 1)")
    from app import profit
    profit.rebuild_stock_state()
    return test_dir

def cleanup(t): shutil.rmtree(t, ignore_errors=True)


async def screenshot_all():
    from playwright.async_api import async_playwright
    import httpx

    test_dir = setup_test_env()
    proc = None
    out_dir = Path("/home/z/my-project/download/screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["APP_PASSWORD"] = "manager123"
        env["BILLBOOK_DATA_DIR"] = test_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8810", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8810", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200: break
                except: await asyncio.sleep(0.3)

        pages = [
            # Dashboard / Launcher
            ("/", "01_launcher"),
            ("/reports/store-profit", "02_store_profit_dashboard"),
            # POS
            ("/pos", "03_pos_kiosk"),
            ("/pos/returns", "04_pos_returns"),
            ("/pos/shifts", "05_pos_shifts"),
            ("/pos/cash-drawer", "06_pos_cash_drawer"),
            ("/pos/z-report", "07_pos_z_report"),
            # Bills
            ("/bills", "08_bills_list"),
            ("/bills/new", "09_bill_new_upload"),
            # Inventory
            ("/items", "10_items_search"),
            ("/stock", "11_stock_levels"),
            ("/stock/adjustments", "12_stock_adjustments"),
            ("/reorder", "13_reorder"),
            ("/dead-stock", "14_dead_stock"),
            ("/transfers/out", "15_transfer_out"),
            ("/transfers/in", "16_transfer_in"),
            ("/central-purchases", "17_central_purchases"),
            ("/pos-import", "18_pos_import"),
            # Customers
            ("/customers", "19_customers"),
            ("/customers/credit", "20_credit_outstanding"),
            ("/customers/loyalty", "21_loyalty_tiers"),
            # Suppliers
            ("/suppliers", "22_suppliers"),
            # Reports
            ("/reports/margins", "23_margins"),
            ("/reports/monthly-profit", "24_monthly_profit"),
            ("/reports/ytd", "25_ytd_profit"),
            ("/reports/daily-stock", "26_daily_stock"),
            ("/reports/cash-buckets", "27_cash_buckets"),
            ("/reports/audit", "28_ai_auditor"),
            ("/more", "29_more"),
            # AI Insights
            ("/insights", "30_ai_assistant"),
            ("/insights/abc", "31_abc_analysis"),
            ("/insights/trends", "32_trends"),
            ("/insights/forecast", "33_forecast"),
            ("/insights/agent", "34_agent_chat"),
            ("/insights/approval-queue", "35_approval_queue"),
            ("/insights/ai-usage", "36_ai_usage"),
            ("/insights/hq-branches", "37_hq_branches"),
            ("/insights/owner-hub", "38_owner_hub"),
            ("/insights/price-push", "39_price_push"),
            # Settings
            ("/settings", "40_settings_general"),
            ("/settings/employees", "41_settings_employees"),
            ("/settings/tax-sms", "42_settings_tax_sms"),
            ("/settings/backups", "43_settings_backups"),
            ("/settings/security", "44_settings_security"),
            ("/settings/appearance", "45_settings_appearance"),
            ("/settings/ai-automations", "46_ai_automations"),
            ("/settings/branch", "47_branch_settings"),
            # Help
            ("/help", "48_help"),
        ]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)

            # Login
            await page.goto("http://127.0.0.1:8810/login", wait_until="networkidle")
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(2.0)

            print(f"\nCapturing {len(pages)} page screenshots...\n")
            for route, name in pages:
                err_before = len(console_errors)
                try:
                    await page.goto(f"http://127.0.0.1:8810#{route}", wait_until="networkidle")
                    await asyncio.sleep(1.5)
                    filepath = out_dir / f"{name}.png"
                    await page.screenshot(path=str(filepath), full_page=True)
                    err_after = len(console_errors)
                    has_errors = err_after > err_before
                    status = "ERROR" if has_errors else "OK"
                    results.append((name, route, status, console_errors[err_before:err_after][:2] if has_errors else []))
                    print(f"  [{status}] {name}.png  ({route})")
                except Exception as e:
                    results.append((name, route, "FAIL", [str(e)[:100]]))
                    print(f"  [FAIL] {name}.png  ({route}) — {str(e)[:80]}")

            await browser.close()

        # Summary
        ok = sum(1 for _, _, s, _ in results if s == "OK")
        errors = sum(1 for _, _, s, _ in results if s == "ERROR")
        fails = sum(1 for _, _, s, _ in results if s == "FAIL")
        print(f"\n{'='*60}")
        print(f"=== SCREENSHOT SUMMARY ===")
        print(f"  {ok} OK, {errors} with console errors, {fails} failed, {len(results)} total")
        print(f"  Saved to: {out_dir}/")
        if errors or fails:
            print(f"\n  Issues:")
            for name, route, status, errs in results:
                if status != "OK":
                    print(f"    [{status}] {name} ({route}): {errs}")
        return fails == 0
    finally:
        if proc:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=5)
            except: proc.kill()
        cleanup(test_dir)

if __name__ == "__main__":
    ok = asyncio.run(screenshot_all())
    sys.exit(0 if ok else 1)
