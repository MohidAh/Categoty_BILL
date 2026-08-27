"""v8.0.1 — Sync failure + retry test (the eventual-consistency proof).

THE missing proof from v8.0 Phase 7: if HQ is unreachable, the branch keeps
selling normally; the outbox accumulates pending entries; when HQ comes back,
the next flush delivers them. Never blocks a sale.

Flow:
1. Branch A queues a summary push into its local sync_outbox
2. HQ is unreachable (we point dest_url at a dead port) → flush fails →
   entry stays 'pending', attempts incremented
3. HQ comes back (we restart it on the real port) → flush succeeds →
   entry marked 'sent', HQ has the summary

This proves the "eventual consistency" claim from the master prompt.
"""
import os, sys, time, tempfile, shutil, signal, subprocess, json
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v801_retry_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
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
    return test_dir



def test_sync_failure_then_retry():
    """The eventual-consistency proof: block HQ → queue → unblock → verify flush."""
    import httpx
    test_dir = setup_test_db()
    hq_dir = tempfile.mkdtemp(prefix="billbook_v801_hq_")
    hq_proc = None
    results = []

    def check(label, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        results.append((mark, label, detail))
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))

    try:
        # Set up a separate HQ DB + server
        os.environ["APP_PASSWORD"] = "manager123"
        os.environ["BILLBOOK_DATA_DIR"] = hq_dir
        from app import config as _cfg, db as _db, security
        _db.DB_PATH = os.path.join(hq_dir, "billbook.db")
        _cfg.DATA = Path(hq_dir)
        for n in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
            os.makedirs(getattr(_cfg, n), exist_ok=True)
        _db.init()
        security.ensure_password()
        if not _db.get_setting("password_hash", ""):
            _db.set_setting("password_hash", security.hash_password("manager123"))
        # Register Branch A on HQ + get a token
        from app.routers.hq import generate_branch_pairing_code, register_branch, BranchRegisterIn
        code_r = generate_branch_pairing_code({})
        reg_r = register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Branch A", branch_id="BR-A",
            tunnel_url="http://127.0.0.1:8790",
        ))
        branch_token = reg_r["token"]

        # Start HQ server on port 8790
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), "APP_PASSWORD": "manager123",
               "BILLBOOK_DATA_DIR": hq_dir}
        hq_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", "8790", "--log-level", "warning"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        # Wait for HQ to boot
        for _ in range(40):
            try:
                r = httpx.get("http://127.0.0.1:8790/login", timeout=2)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.3)

        # Now point the BRANCH-side DB at test_dir (the branch's own DB)
        os.environ["BILLBOOK_DATA_DIR"] = test_dir
        _db.DB_PATH = os.path.join(test_dir, "billbook.db")
        _cfg.DATA = Path(test_dir)
        _db.init()

        from app.sync import queue_sync_outbox, flush_sync_outbox, get_outbox_status
        from app import db as _branch_db

        print("\n=== Step 1: Queue a summary push into the outbox ===")
        today = time.strftime("%Y-%m-%d")
        summary_payload = {
            "summary_date": today, "sales": 12000, "cogs": 7000,
            "gross_profit": 5000, "expenses": 1500, "cash_in_drawer": 3000,
            "stock_snapshot": {"1": {"qty": 5000, "value": 900000, "avg_cost": 180}},
        }
        entry_id = queue_sync_outbox(
            dest_branch_id="BR-HQ",
            entity_type="branch_summary",
            entity_key=f"{today}_BR-A",
            payload=summary_payload,
        )
        check("Outbox entry queued", entry_id > 0, f"entry_id={entry_id}")
        status = get_outbox_status()
        check("Outbox has 1 pending entry", status["pending"] == 1, f"pending={status['pending']}")

        print("\n=== Step 2: HQ unreachable → flush fails → entry stays pending ===")
        # Point dest_url at a DEAD port (8799 — nothing listening there)
        dead_url = "http://127.0.0.1:8799"
        flush_r = flush_sync_outbox(dead_url, branch_token)
        check("Flush to dead URL: 0 sent", flush_r["sent"] == 0, f"sent={flush_r['sent']}")
        check("Flush to dead URL: 1 failed", flush_r["failed"] == 1, f"failed={flush_r['failed']}")
        check("Flush to dead URL: 1 remaining", flush_r["remaining"] == 1, f"remaining={flush_r['remaining']}")
        # Verify the entry is still pending + attempts was incremented
        with _branch_db.conn() as c:
            row = c.execute("SELECT status, attempts FROM sync_outbox WHERE id=?", (entry_id,)).fetchone()
        check("Entry still 'pending' after failed flush", row["status"] == "pending", f"status={row['status']}")
        check("Entry attempts incremented to 1", row["attempts"] == 1, f"attempts={row['attempts']}")

        print("\n=== Step 3: HQ comes back → flush succeeds → entry marked 'sent' ===")
        # Now flush to the LIVE HQ URL
        live_url = "http://127.0.0.1:8790"
        flush_r2 = flush_sync_outbox(live_url, branch_token)
        check("Flush to live URL: 1 sent", flush_r2["sent"] == 1, f"sent={flush_r2['sent']}")
        check("Flush to live URL: 0 failed", flush_r2["failed"] == 0, f"failed={flush_r2['failed']}")
        check("Flush to live URL: 0 remaining", flush_r2["remaining"] == 0, f"remaining={flush_r2['remaining']}")
        # Verify the entry is now 'sent'
        with _branch_db.conn() as c:
            row = c.execute("SELECT status, attempts FROM sync_outbox WHERE id=?", (entry_id,)).fetchone()
        check("Entry now 'sent'", row["status"] == "sent", f"status={row['status']}")

        print("\n=== Step 4: Verify HQ actually received the summary ===")
        # Login on HQ + check Owner Hub
        with httpx.Client(base_url="http://127.0.0.1:8790", timeout=10) as hq_client:
            hq_client.post("/api/login", json={"username": "manager", "password": "manager123"})
            hub_r = hq_client.get(f"/api/hq/owner-hub?date={today}")
            hub_data = hub_r.json()
            check("HQ Owner Hub shows the synced summary (sales=12000)",
                  hub_data["consolidated"]["sales"] == 12000,
                  f"sales={hub_data['consolidated']['sales']}")

        print("\n=== Step 5: Idempotency — re-queue the same key + flush ===")
        # Re-queue with the same entity_key but updated sales
        entry_id2 = queue_sync_outbox(
            dest_branch_id="BR-HQ",
            entity_type="branch_summary",
            entity_key=f"{today}_BR-A",
            payload={**summary_payload, "sales": 15000},  # updated sales
        )
        check("Re-queue with same key returns same entry_id (idempotent)",
              entry_id2 == entry_id, f"entry_id={entry_id}, entry_id2={entry_id2}")
        # Flush — should deliver the updated payload
        flush_r3 = flush_sync_outbox(live_url, branch_token)
        check("Re-flush: 1 sent", flush_r3["sent"] == 1, f"sent={flush_r3['sent']}")
        # Verify HQ now shows the updated sales
        with httpx.Client(base_url="http://127.0.0.1:8790", timeout=10) as hq_client:
            hq_client.post("/api/login", json={"username": "manager", "password": "manager123"})
            hub_r = hq_client.get(f"/api/hq/owner-hub?date={today}")
            hub_data = hub_r.json()
            check("HQ Owner Hub shows UPDATED sales (15000)",
                  hub_data["consolidated"]["sales"] == 15000,
                  f"sales={hub_data['consolidated']['sales']}")

        # Summary
        print("\n" + "=" * 60)
        print("=== v8.0.1 SYNC FAILURE-RETRY SUMMARY ===")
        print("=" * 60)
        passed = sum(1 for m, _, _ in results if m == "PASS")
        failed = sum(1 for m, _, _ in results if m == "FAIL")
        print(f"  {passed} passed, {failed} failed, {len(results)} total")
        return failed == 0
    finally:
        if hq_proc:
            hq_proc.send_signal(signal.SIGINT)
            try: hq_proc.wait(timeout=5)
            except: hq_proc.kill()
        cleanup(test_dir)
        cleanup(hq_dir)


def test_outbox_status_endpoint():
    """GET /api/sync/outbox returns the outbox status."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import sync_outbox_status
        r = sync_outbox_status()
        assert "pending" in r and "sent" in r and "failed" in r
        assert "recent" in r
        assert r["pending"] == 0  # fresh DB
    finally:
        cleanup(test_dir)


def test_queue_idempotent():
    """Re-queueing the same entity_key updates the existing entry, doesn't duplicate."""
    test_dir = setup_test_db()
    try:
        from app.sync import queue_sync_outbox, get_outbox_status
        from app import db
        id1 = queue_sync_outbox("BR-HQ", "branch_summary", "2026-01-01_BR-A", {"sales": 100})
        id2 = queue_sync_outbox("BR-HQ", "branch_summary", "2026-01-01_BR-A", {"sales": 200})
        assert id1 == id2, f"expected same id, got {id1} vs {id2}"
        status = get_outbox_status()
        assert status["pending"] == 1, f"expected 1 pending, got {status['pending']}"
        # Verify the payload was updated
        with db.conn() as c:
            row = c.execute("SELECT payload_json FROM sync_outbox WHERE id=?", (id1,)).fetchone()
        import json
        payload = json.loads(row["payload_json"])
        assert payload["sales"] == 200, f"payload not updated: {payload}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    ok1 = test_sync_failure_then_retry()
    print()
    ok2 = test_outbox_status_endpoint(); print(f"{'PASS' if ok2 else 'FAIL'}: outbox status endpoint")
    ok3 = test_queue_idempotent(); print(f"{'PASS' if ok3 else 'FAIL'}: queue idempotent")
    sys.exit(0 if (ok1 and ok2 and ok3) else 1)
