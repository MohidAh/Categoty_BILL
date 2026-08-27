"""v8.0 Phase 6 — Global Price Push tests.

Verifies:
- price_pushes table exists
- POST /api/hq/price-push creates a push with price_push_id + delivery_targets
- POST /api/sync/price-push (Bearer auth) applies the push to local price_categories
- Idempotent: re-delivery with same price_push_id returns 'already_applied', no re-apply
- Activity log records source='hq'
- GET /api/hq/price-pushes lists history
- Branch-side: local sell_price actually changes
- 185.88 still passes
"""
import os, sys, tempfile, shutil, json, hashlib
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_p6_")
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
                  "central_purchases", "central_purchase_items",
                  "price_pushes"):
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



def _register_branch(branch_id="BR-A", name="Branch A", tunnel_url="https://branch-a.trycloudflare.com"):
    from app.routers.hq import generate_branch_pairing_code, register_branch, BranchRegisterIn
    code_r = generate_branch_pairing_code({})
    reg_r = register_branch(BranchRegisterIn(
        code=code_r["code"], branch_name=name, branch_id=branch_id, tunnel_url=tunnel_url,
    ))
    return branch_id, reg_r["token"]


class FakeRequest:
    def __init__(self, token=None):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}


def test_price_pushes_table_exists():
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "price_pushes" in tables
    finally:
        cleanup(test_dir)


def test_create_price_push():
    """POST /api/hq/price-push creates a push + returns delivery_targets."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A", "https://a.trycloudflare.com")
        _register_branch("BR-B", "Branch B", "https://b.trycloudflare.com")
        from app.routers.hq import create_price_push, PricePushIn
        r = create_price_push(PricePushIn(
            category_id=2, new_sell_price=550, notes="Eid special",
        ))
        assert r["price_push_id"].startswith("PP-")
        assert r["category_id"] == 2
        assert r["new_sell_price"] == 550
        assert len(r["delivery_targets"]) == 2
        assert r["delivery_targets"][0]["tunnel_url"] in (
            "https://a.trycloudflare.com", "https://b.trycloudflare.com")
    finally:
        cleanup(test_dir)


def test_create_price_push_invalid_category():
    """Creating a push for a non-existent category returns 404."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import create_price_push, PricePushIn
        from fastapi import HTTPException
        try:
            create_price_push(PricePushIn(category_id=9999, new_sell_price=100))
            assert False, "should raise 404"
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        cleanup(test_dir)


def test_create_price_push_invalid_price():
    """Creating a push with price <= 0 returns 400."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import create_price_push, PricePushIn
        from fastapi import HTTPException
        try:
            create_price_push(PricePushIn(category_id=1, new_sell_price=0))
            assert False, "should raise 400"
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        cleanup(test_dir)


def test_apply_price_push_changes_local_price():
    """POST /api/sync/price-push (Bearer auth) updates local sell_price."""
    test_dir = setup_test_db()
    try:
        bid, token = _register_branch("BR-A", "Branch A")
        from app.routers.hq import apply_price_push, PricePushApplyIn
        from app import db
        # Record original price
        with db.conn() as c:
            orig = c.execute("SELECT sell_price FROM price_categories WHERE id=2").fetchone()["sell_price"]
        # Apply a push
        apply_price_push(PricePushApplyIn(
            price_push_id="PP-TEST-001", category_id=2, category_code="B",
            new_sell_price=999,
        ), FakeRequest(token))
        # Verify the price changed
        with db.conn() as c:
            new = c.execute("SELECT sell_price FROM price_categories WHERE id=2").fetchone()["sell_price"]
        assert new == 999, f"price not updated: {new}"
        assert new != orig
    finally:
        cleanup(test_dir)


def test_apply_price_push_idempotent():
    """Re-delivery with same price_push_id returns 'already_applied', no re-apply."""
    test_dir = setup_test_db()
    try:
        bid, token = _register_branch("BR-A", "Branch A")
        from app.routers.hq import apply_price_push, PricePushApplyIn
        from app import db
        # First apply
        r1 = apply_price_push(PricePushApplyIn(
            price_push_id="PP-IDEM-001", category_id=2, category_code="B",
            new_sell_price=777,
        ), FakeRequest(token))
        assert r1["status"] == "applied"
        # Second apply (re-delivery)
        r2 = apply_price_push(PricePushApplyIn(
            price_push_id="PP-IDEM-001", category_id=2, category_code="B",
            new_sell_price=888,  # different price — should NOT be applied
        ), FakeRequest(token))
        assert r2["status"] == "already_applied"
        # Verify the price is still 777 (not 888)
        with db.conn() as c:
            price = c.execute("SELECT sell_price FROM price_categories WHERE id=2").fetchone()["sell_price"]
        assert price == 777, f"price was re-applied: {price}"
    finally:
        cleanup(test_dir)


def test_apply_price_push_requires_token():
    """POST /api/sync/price-push without token returns 401."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import apply_price_push, PricePushApplyIn
        from fastapi import HTTPException
        try:
            apply_price_push(PricePushApplyIn(
                price_push_id="PP-AUTH-001", category_id=1, new_sell_price=100,
            ), FakeRequest(None))
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_apply_price_push_invalid_token():
    """POST /api/sync/price-push with invalid token returns 401."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import apply_price_push, PricePushApplyIn
        from fastapi import HTTPException
        try:
            apply_price_push(PricePushApplyIn(
                price_push_id="PP-AUTH-002", category_id=1, new_sell_price=100,
            ), FakeRequest("invalid-token"))
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_apply_price_push_logs_activity_with_source_hq():
    """Price push application logs an activity entry with source='hq'."""
    test_dir = setup_test_db()
    try:
        bid, token = _register_branch("BR-A", "Branch A")
        from app.routers.hq import apply_price_push, PricePushApplyIn
        from app import db
        apply_price_push(PricePushApplyIn(
            price_push_id="PP-LOG-001", category_id=2, category_code="B",
            new_sell_price=500,
        ), FakeRequest(token))
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='price_push_applied' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        meta = json.loads(row["metadata"])
        assert meta.get("source") == "hq", f"source not 'hq': {meta}"
        assert meta.get("price_push_id") == "PP-LOG-001"
    finally:
        cleanup(test_dir)


def test_list_price_pushes():
    """GET /api/hq/price-pushes lists push history."""
    test_dir = setup_test_db()
    try:
        bid, token = _register_branch("BR-A", "Branch A")
        from app.routers.hq import (
            create_price_push, PricePushIn, apply_price_push, PricePushApplyIn, list_price_pushes,
        )
        # HQ creates a push
        cp = create_price_push(PricePushIn(category_id=2, new_sell_price=600))
        # Branch applies it (same price_push_id — idempotent, so the row is the same one
        # created by HQ, but now has applied_at set)
        apply_price_push(PricePushApplyIn(
            price_push_id=cp["price_push_id"], category_id=2, category_code="B",
            new_sell_price=600,
        ), FakeRequest(token))
        r = list_price_pushes()
        # On HQ: 1 row (the create). On branch: 1 row (the apply, same push_id).
        # Since this test runs on a single DB simulating both roles, the apply
        # found the existing row + returned already_applied. So count == 1.
        assert r["count"] >= 1
        # The push should be present
        push_ids = [p["price_push_id"] for p in r["pushes"]]
        assert cp["price_push_id"] in push_ids
    finally:
        cleanup(test_dir)


def test_18588_still_passes():
    """The 185.88 running weighted avg test still passes after Phase 6 changes."""
    test_dir = setup_test_db()
    try:
        from app.profit_engine import apply_purchase_to_state, apply_sale_to_state, peek_avg_cost
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        apply_purchase_to_state(1, 10000, 180.0)
        apply_sale_to_state(1, 3000)
        apply_purchase_to_state(1, 10000, 190.0)
        with db.conn() as c:
            avg = peek_avg_cost(c, 1)
            assert abs(avg - 185.88) < 0.01, f"expected 185.88, got {avg}"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_price_pushes_table_exists(); print("OK table exists")
    test_create_price_push(); print("OK create price push")
    test_create_price_push_invalid_category(); print("OK invalid category 404")
    test_create_price_push_invalid_price(); print("OK invalid price 400")
    test_apply_price_push_changes_local_price(); print("OK apply changes local price")
    test_apply_price_push_idempotent(); print("OK apply idempotent")
    test_apply_price_push_requires_token(); print("OK apply requires token")
    test_apply_price_push_invalid_token(); print("OK apply rejects invalid token")
    test_apply_price_push_logs_activity_with_source_hq(); print("OK logs with source=hq")
    test_list_price_pushes(); print("OK list price pushes")
    test_18588_still_passes(); print("OK 185.88 still passes")
    print("\nALL v8.0 PHASE 6 TESTS PASSED")
