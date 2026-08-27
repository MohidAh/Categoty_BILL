"""v8.2 Phase 6 — Browser E2E + regression + release."""
import os, sys, time, json, signal, subprocess, tempfile, shutil, asyncio
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_p6_browser_")
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
                  "audit_runs","audit_findings","bill_intelligence"):
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
        # Seed cash so safe_withdrawal is positive
        c.execute("INSERT INTO cash_drawer(type, amount, description) VALUES('opening', 50000, 'test')")
    from app import profit
    profit.rebuild_stock_state()
    return test_dir

def cleanup(t): shutil.rmtree(t, ignore_errors=True)


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
             "--host", "127.0.0.1", "--port", "8802", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8802", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200: break
                except: await asyncio.sleep(0.3)

        print("\n=== 1. Login + audit endpoints ===")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(f"PAGEERROR: {exc}"))

            await page.goto("http://127.0.0.1:8802/login", wait_until="networkidle")
            await page.locator("input[type='password']").first.fill("manager123")
            await page.locator("button[type='submit']").first.click()
            await asyncio.sleep(2.0)
            check("Logged in", "BillBook" in await page.content() or "launcher" in page.url)

            # API checks
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8802", timeout=10.0) as hx:
                r = await hx.post("/api/login", json={"username": "manager", "password": "manager123"})
                h = {"Cookie": f"bb_token={hx.cookies.get('bb_token')}"}
                # Run audit
                r = await hx.post("/api/audit/run", headers=h)
                check("POST /api/audit/run returns 200", r.status_code == 200, f"got {r.status_code}")
                audit = r.json()
                check("Audit returns findings_count", "findings_count" in audit)
                check("Audit returns critical_count", "critical_count" in audit)
                # Get latest
                r = await hx.get("/api/audit/latest", headers=h)
                check("GET /api/audit/latest returns 200", r.status_code == 200)
                latest = r.json()
                check("Latest audit has run", latest.get("run") is not None)
                check("Latest audit has findings", isinstance(latest.get("findings"), list))
                # Safe withdrawal
                r = await hx.get("/api/audit/safe-withdrawal", headers=h)
                check("GET /api/audit/safe-withdrawal returns 200", r.status_code == 200)
                sw = r.json()
                check("Safe withdrawal has 'safe_withdrawal' field", "safe_withdrawal" in sw)
                check("Safe withdrawal has 'is_over' field", "is_over" in sw)
                # Audit runs list
                r = await hx.get("/api/audit/runs", headers=h)
                check("GET /api/audit/runs returns 200", r.status_code == 200)

            print("\n=== 2. Audit Report page loads ===")
            err_before = len(console_errors)
            await page.goto("http://127.0.0.1:8802#/reports/audit", wait_until="networkidle")
            await asyncio.sleep(3.0)
            await page.screenshot(path="/tmp/v82_audit_report.png", full_page=True)
            err_after = len(console_errors)
            content = await page.content()
            check("Audit Report page header visible", "AI Auditor" in content)
            check("Audit Report has Run Audit button", "Run Audit" in content)
            check("Audit Report — zero console errors", err_after == err_before,
                  f"errors: {console_errors[err_before:err_after][:2]}" if err_after > err_before else "")

            # Check for stat cards
            check("Audit Report has Critical stat card", "Critical" in content)
            check("Audit Report has Warnings stat card", "Warnings" in content)

            print("\n=== 3. Run Audit from UI ===")
            err_before = len(console_errors)
            btn = page.locator("#audit-run")
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(2.0)
                err_after = len(console_errors)
                check("Run Audit click — zero console errors", err_after == err_before)
            else:
                check("Run Audit button found", False)

            print("\n=== 4. Cash Buckets page (safe-withdrawal banner) ===")
            err_before = len(console_errors)
            await page.goto("http://127.0.0.1:8802#/reports/cash-buckets", wait_until="networkidle")
            await asyncio.sleep(2.0)
            err_after = len(console_errors)
            check("Cash Buckets page — zero console errors", err_after == err_before)

            print("\n=== 5. Re-check v8.1 pages (zero console errors) ===")
            v81_pages = ["/reports/audit", "/insights/agent", "/insights/approval-queue",
                         "/settings/branch", "/insights/owner-hub", "/transfers/out"]
            pages_with_errors = []
            for path in v81_pages:
                err_before = len(console_errors)
                await page.goto(f"http://127.0.0.1:8802#{path}", wait_until="networkidle")
                await asyncio.sleep(1.0)
                err_after = len(console_errors)
                if err_after > err_before:
                    pages_with_errors.append(path)
            check("6 v8.1 pages — all zero NEW console errors", len(pages_with_errors) == 0,
                  f"pages: {pages_with_errors}" if pages_with_errors else "")

            check("ZERO console errors across entire E2E", len(console_errors) == 0,
                  f"{len(console_errors)} errors: {console_errors[:3]}" if console_errors else "")
            await browser.close()

        print("\n" + "=" * 60)
        print("=== v8.2 PHASE 6 BROWSER E2E SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        return failed == 0
    finally:
        if proc:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=5)
            except: proc.kill()
        cleanup(test_dir)

if __name__ == "__main__":
    ok = asyncio.run(run_browser_e2e())
    sys.exit(0 if ok else 1)
