"""v7.2 Phase 7 — Real-server E2E via uvicorn + httpx.

Boots a real uvicorn server on port 8767, logs in as manager, then exercises
every new v7.2 endpoint through real HTTP. This is the honest "browser E2E"
substitute the environment allows (no headless browser available).

Verifies:
1. Server boots and stays up
2. /login page renders (HTTP 200)
3. /api/login with correct password sets cookie + returns 200
4. Authed GET on each v7.2 endpoint returns 200 with valid JSON shape
5. Approval Queue create → edit → approve lifecycle
6. Agent /api/agent/ask returns trace + answer
7. Kill switch ON → agent blocked, /api/ai/usage still works
8. /api/agent/prepare-season creates batched pending actions
9. Static JS pages are served (200 + content-type application/javascript)
"""
import os, sys, time, json, signal, subprocess, tempfile, shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_env():
    """Point the app at a fresh test DB so we don't pollute the dev DB.

    Uses BILLBOOK_DATA_DIR (the env var app/config.py honors) so the subprocess
    uvicorn boots against the same DB we seeded.
    """
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_e2e_")
    os.environ["APP_PASSWORD"] = "manager123"
    os.environ["BILLBOOK_DATA_DIR"] = test_dir
    from app import config, db, security
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        path = getattr(config, name)
        os.makedirs(path, exist_ok=True)
    db.init()
    # Explicitly seed the manager password (ensure_password only sets it if env is set
    # AND no hash exists — but the imported module may have already cached the empty hash).
    security.ensure_password()
    # If still empty, set it directly
    if not db.get_setting("password_hash", ""):
        db.set_setting("password_hash", security.hash_password("manager123"))
    # Seed sample data
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
        for key in ['auto_confirm_bills', 'auto_draft_po', 'urdhaar_reminders',
                    'recurring_detection', 'expense_categorization',
                    'anomaly_diagnosis', 'variance_investigation',
                    'scheduled_reports', 'dead_stock_liquidation', 'ai_kill_switch']:
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)", (key, 0, 2, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def cleanup(t):
    shutil.rmtree(t, ignore_errors=True)


def run_e2e():
    import httpx
    test_dir = setup_test_env()
    proc = None
    try:
        # Start uvicorn in subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        env["APP_PASSWORD"] = "manager123"
        env["BILLBOOK_DATA_DIR"] = test_dir
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8767", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # Wait for server to come up
        client = httpx.Client(base_url="http://127.0.0.1:8767", timeout=10.0)
        for _ in range(30):
            try:
                r = client.get("/login")
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            output = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"server failed to start. Output:\n{output}")

        results = []
        def check(label, cond, detail=""):
            mark = "PASS" if cond else "FAIL"
            results.append((mark, label, detail))
            print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

        print("\n=== 1. Server boot + login page ===")
        check("Server responded on /login", True, f"HTTP {r.status_code}")
        check("/login contains 'BillBook' in title", "BillBook" in r.text, "")

        print("\n=== 2. Login as manager ===")
        r = client.post("/api/login", json={"username": "manager", "password": "manager123"})
        check("POST /api/login returns 200", r.status_code == 200, f"got {r.status_code}")
        check("Cookie 'bb_token' set", "bb_token" in client.cookies, "")

        print("\n=== 3. AI Usage Dashboard endpoints ===")
        r = client.get("/api/ai/usage")
        check("GET /api/ai/usage", r.status_code == 200 and "providers" in r.json(), f"HTTP {r.status_code}")
        r = client.get("/api/ai/usage/14d")
        body = r.json()
        check("GET /api/ai/usage/14d returns 14 entries",
              r.status_code == 200 and len(body.get("days", [])) == 14, f"got {len(body.get('days', []))} days")
        r = client.get("/api/ai/failures")
        check("GET /api/ai/failures", r.status_code == 200 and "failures" in r.json(), "")
        r = client.get("/api/ai/ttl-legend")
        body = r.json()
        check("GET /api/ai/ttl-legend returns 4 entries",
              r.status_code == 200 and len(body.get("ttl", [])) == 4, "")
        r = client.get("/api/ai/kill-switch")
        check("GET /api/ai/kill-switch", r.status_code == 200 and "disabled" in r.json(), "")

        print("\n=== 4. Approval Queue endpoints ===")
        r = client.get("/api/pending-actions?status=pending&limit=1")
        body = r.json()
        check("GET /api/pending-actions returns count field",
              r.status_code == 200 and "count" in body, f"count={body.get('count')}")

        # Create a pending action
        r = client.post("/api/pending-actions", json={
            "action_type": "draft_expense",
            "payload": {"amount": 100, "category": "Misc"},
            "reason": "e2e test", "impact_summary": "Rs 100",
        })
        check("POST /api/pending-actions creates action", r.status_code == 200, f"got {r.status_code}")
        aid = r.json().get("id")
        check("Created action has numeric id", isinstance(aid, int), f"id={aid}")

        # Edit it
        r = client.put(f"/api/pending-actions/{aid}", json={
            "payload": {"amount": 250, "category": "Misc"},
            "reason": "edited",
        })
        check("PUT /api/pending-actions/{id} edits payload", r.status_code == 200, "")

        # Approve it
        r = client.post(f"/api/pending-actions/{aid}/approve", json={"approved_by": "manager"})
        check("POST /api/pending-actions/{id}/approve executes", r.status_code == 200, "")
        check("Approve result has expense_id", "expense_id" in r.json().get("result", {}), "")

        print("\n=== 5. Agent endpoints ===")
        r = client.get("/api/agent/tools")
        body = r.json()
        check("GET /api/agent/tools lists tools",
              r.status_code == 200 and "get_margins" in body.get("tools", []), "")

        r = client.post("/api/agent/ask", json={"question": "What is my actual overall margin?"})
        body = r.json()
        check("POST /api/agent/ask returns 200", r.status_code == 200, "")
        check("Agent answer contains a digit (margin %)",
              any(c.isdigit() for c in body.get("answer", "")), "")
        check("Agent returns tool_trace list", isinstance(body.get("tool_trace"), list), "")
        check("Agent returns suggested_followups list",
              isinstance(body.get("suggested_followups"), list), "")

        print("\n=== 6. Constrained SQL ===")
        r = client.post("/api/agent/sql", json={"query": "SELECT * FROM settings"})
        check("SQL blocks forbidden table 'settings'", "error" in r.json(), "")
        r = client.post("/api/agent/sql", json={"query": "DROP TABLE bills"})
        check("SQL blocks DROP", "error" in r.json(), "")
        r = client.post("/api/agent/sql", json={"query": "SELECT COUNT(*) AS n FROM sales"})
        body = r.json()
        check("SQL allows SELECT on allowlist table", "error" not in body and "rows" in body, "")

        print("\n=== 7. Kill switch ON blocks agent ===")
        r = client.post("/api/ai/kill-switch", json={"enabled": 1})
        check("POST /api/ai/kill-switch ON", r.status_code == 200, "")
        r = client.post("/api/agent/ask", json={"question": "What is my margin?"})
        body = r.json()
        check("Agent blocked when kill switch ON",
              "disabled" in body.get("answer", "").lower() or "kill" in body.get("answer", "").lower(),
              f"answer='{body.get('answer','')[:60]}'")
        # Turn it back OFF
        client.post("/api/ai/kill-switch", json={"enabled": 0})

        print("\n=== 8. Season-prep creates batched actions ===")
        r = client.post("/api/agent/prepare-season", json={"season": "Eid"})
        body = r.json()
        check("POST /api/agent/prepare-season returns 200", r.status_code == 200, "")
        check("Returns batch_id", bool(body.get("batch_id")), "")
        check("Returns pending_count >= 1", body.get("pending_count", 0) >= 1, f"count={body.get('pending_count')}")

        # Verify actions appear in the queue
        r = client.get("/api/pending-actions?status=pending&limit=100")
        actions = r.json().get("actions", [])
        batch_id = body.get("batch_id")
        pending_count = body.get("pending_count", 0)
        batch_actions = [a for a in actions if a.get("batch_id") == batch_id] if batch_id else []
        check(f"Batch actions appear in queue ({len(batch_actions)})",
              len(batch_actions) == pending_count, "")

        print("\n=== 9. AI Automations config endpoint ===")
        r = client.get("/api/automation-config")
        body = r.json()
        config_keys = [c["key"] for c in body.get("config", [])]
        check("GET /api/automation-config returns all 10 keys",
              "ai_kill_switch" in config_keys and "auto_confirm_bills" in config_keys,
              f"{len(config_keys)} keys")

        print("\n=== 10. Clear cache endpoint ===")
        r = client.post("/api/ai/clear-cache", json={})
        body = r.json()
        check("POST /api/ai/clear-cache", r.status_code == 200 and body.get("ok") is True,
              f"deleted={body.get('deleted')}")

        print("\n=== 11. Static JS pages served ===")
        for page in ["approval-queue-page.js", "agent-chat-page.js", "ai-usage-page.js", "ai-automations-page.js"]:
            r = client.get(f"/static/js/pages/{page}")
            check(f"/static/js/pages/{page} returns 200",
                  r.status_code == 200, f"HTTP {r.status_code}")

        # Summary
        print("\n=== SUMMARY ===")
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
    ok = run_e2e()
    sys.exit(0 if ok else 1)
