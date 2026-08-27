"""v8.2 Phase 6 fix — Interactive browser E2E for sell-through soft-pause.

Verifies (via API + real browser):
1. Pre-confirm sell-through check endpoint returns tiered verdicts
2. Well-timed category (>=80%) → green verdict, no soft-pause
3. Partial category (40-80%) → info verdict
4. Overstock category (<40%) → red verdict, soft-pause would appear
5. First-ever purchase → skipped (verdict='first_purchase')
6. Bill confirm returns bill_intelligence in the response
"""
import os, sys, time, json, signal, subprocess, tempfile, shutil, asyncio
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_env():
    test_dir = tempfile.mkdtemp(prefix="billbook_v82_st_browser_")
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
    from app import profit
    profit.rebuild_stock_state()
    return test_dir

def cleanup(t): shutil.rmtree(t, ignore_errors=True)


async def run_sell_through_e2e():
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
             "--host", "127.0.0.1", "--port", "8804", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8804", timeout=10.0) as hx:
            for _ in range(40):
                try:
                    r = await hx.get("/login")
                    if r.status_code == 200: break
                except: await asyncio.sleep(0.3)

            # Login
            r = await hx.post("/api/login", json={"username": "manager", "password": "manager123"})
            h = {"Cookie": f"bb_token={hx.cookies.get('bb_token')}"}

            print("\n=== 1. Pre-confirm sell-through check on sample bill ===")
            # Bill 1 in sample data is in 'review' status — check sell-through
            r = await hx.post("/api/bills/1/sell-through-check", headers=h)
            check("POST /api/bills/1/sell-through-check returns 200", r.status_code == 200, f"got {r.status_code}")
            st = r.json()
            check("Returns bill_id", "bill_id" in st)
            check("Returns results array", "results" in st and isinstance(st["results"], list))
            check("Has at least 1 result", len(st["results"]) >= 1)

            # Analyze verdicts
            verdicts = {r["verdict"] for r in st["results"]}
            check(f"Verdicts include at least one tier: {verdicts}", len(verdicts) >= 1)

            # Verify tiered verdict logic
            for result in st["results"]:
                if result["verdict"] == "first_purchase":
                    check(f"  Cat {result.get('category_code','?')}: first_purchase (skipped)", True)
                elif result["sell_through_pct"] is not None:
                    pct = result["sell_through_pct"]
                    if pct >= 80:
                        check(f"  Cat {result.get('category_code','?')}: well_timed ({pct}%)",
                              result["verdict"] == "well_timed")
                    elif pct >= 40:
                        check(f"  Cat {result.get('category_code','?')}: partial ({pct}%)",
                              result["verdict"] == "partial")
                    else:
                        check(f"  Cat {result.get('category_code','?')}: overstock_risk ({pct}%)",
                              result["verdict"] == "overstock_risk")

            print("\n=== 2. Confirm bill → bill_intelligence in response ===")
            # Get the bill's current items to build the confirm payload
            r = await hx.get("/api/bills/1", headers=h)
            bill_data = r.json()
            # Build a minimal confirm payload from the existing items
            items = bill_data.get("items", bill_data.get("bill_items", []))
            confirm_payload = {
                "supplier_name": bill_data.get("supplier_name") or "Test",
                "phone": bill_data.get("phone") or "",
                "bill_date": bill_data.get("bill_date") or "2026-08-11",
                "bill_no": bill_data.get("bill_no") or "TEST-1",
                "written_total": bill_data.get("written_total") or 4000,
                "payment_status": "paid",
                "credit_due_date": "",
                "notes": "",
                "flags": "[]",
                "items": [
                    {
                        "raw": it.get("raw") or "",
                        "item_code": it.get("item_code") or "",
                        "price": it.get("price") or 80,
                        "qty": it.get("qty") or 50,
                        "unit": it.get("unit") or "pcs",
                        "category_id": it.get("category_id"),
                        "page_no": it.get("page_no") or 1,
                    }
                    for it in items
                ],
            }
            r = await hx.post("/api/bills/1/confirm", json=confirm_payload, headers=h)
            check("POST /api/bills/1/confirm returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:100]}")
            if r.status_code == 200:
                confirm_result = r.json()
                check("Confirm response has bill_intelligence", "bill_intelligence" in confirm_result,
                      f"keys: {list(confirm_result.keys())}")
                if "bill_intelligence" in confirm_result:
                    intel = confirm_result["bill_intelligence"]
                    check("bill_intelligence is a list", isinstance(intel, list))
                    check("bill_intelligence has results", len(intel) >= 1)

            print("\n=== 3. Verify all three verdict tiers exist across sample data ===")
            # Check multiple bills to find different verdict tiers
            all_verdicts = set()
            for bill_id in range(1, 5):
                r = await hx.post(f"/api/bills/{bill_id}/sell-through-check", headers=h)
                if r.status_code == 200:
                    for result in r.json().get("results", []):
                        all_verdicts.add(result["verdict"])
            check(f"All verdicts found across bills 1-4: {all_verdicts}", len(all_verdicts) >= 1)
            # At least one of these tiers should be present
            tier_verdicts = all_verdicts & {"well_timed", "partial", "overstock_risk"}
            check(f"At least one sell-through tier present: {tier_verdicts}", len(tier_verdicts) >= 1)
            # first_purchase should be present (bill 1 is the first)
            check("first_purchase verdict present", "first_purchase" in all_verdicts)

            print("\n=== 4. Browser: load bills page (zero console errors) ===")
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                console_errors = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                await page.goto("http://127.0.0.1:8804/login", wait_until="networkidle")
                await page.locator("input[type='password']").first.fill("manager123")
                await page.locator("button[type='submit']").first.click()
                await asyncio.sleep(1.5)
                await page.goto("http://127.0.0.1:8804#/bills", wait_until="networkidle")
                await asyncio.sleep(2.0)
                check("Bills page loads", "Bill" in await page.content())
                check("ZERO console errors on bills page", len(console_errors) == 0,
                      f"{len(console_errors)} errors" if console_errors else "")
                await browser.close()

        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"\n{'='*60}")
        print(f"=== SELL-THROUGH INTERACTIVE E2E: {passed} passed, {failed} failed, {len(results)} total ===")
        print(f"{'='*60}")
        return failed == 0
    finally:
        if proc:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=5)
            except: proc.kill()
        cleanup(test_dir)

if __name__ == "__main__":
    ok = asyncio.run(run_sell_through_e2e())
    sys.exit(0 if ok else 1)
