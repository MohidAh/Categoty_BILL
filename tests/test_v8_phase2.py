"""v8.0 Phase 2 — HQ Branch Registry + Registration tests.

Verifies:
- branches + branch_pairing_codes tables exist
- POST /api/hq/branches/code generates a 6-digit code (5-min expiry)
- POST /api/hq/branches/register validates the code, issues a token, stores branch
- GET /api/hq/branches lists branches (never returns auth_token_hash)
- DELETE /api/hq/branches/{id} revokes (active=0)
- verify_branch_token helper authenticates Bearer tokens correctly
- Re-registration with same branch_id updates the existing row (replaces token)
- Single-use: a code cannot be consumed twice
- Activity log entries are written
"""
import os, sys, tempfile, shutil, hashlib
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_p2_")
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
                  "branches", "branch_pairing_codes"):
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



def test_branches_table_exists():
    """branches + branch_pairing_codes tables exist after init."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "branches" in tables
        assert "branch_pairing_codes" in tables
    finally:
        cleanup(test_dir)


def test_generate_pairing_code():
    """POST /api/hq/branches/code returns a 6-digit code + 300s expiry."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import generate_branch_pairing_code
        r = generate_branch_pairing_code({})
        assert len(r["code"]) == 6, f"expected 6-digit code, got {r['code']}"
        assert r["code"].isdigit(), f"code must be all digits, got {r['code']}"
        assert r["expires_in"] == 300
        # Verify it was stored
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM branch_pairing_codes WHERE code=? AND used=0",
                (r["code"],)
            ).fetchone()
        assert row is not None
    finally:
        cleanup(test_dir)


def test_register_branch_issues_token():
    """POST /api/hq/branches/register validates code + issues a token."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
        )
        code_r = generate_branch_pairing_code({})
        reg_r = register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Lahore Branch", region="Punjab",
            branch_id="BR-AAAA1111", tunnel_url="https://branch-a.trycloudflare.com",
        ))
        assert reg_r["token"], "no token returned"
        assert reg_r["branch_id"] == "BR-AAAA1111"
        assert reg_r["name"] == "Lahore Branch"
        # Verify the branch row was created with hashed token
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT * FROM branches WHERE branch_id=?", ("BR-AAAA1111",)).fetchone()
        assert row is not None
        expected_hash = hashlib.sha256(reg_r["token"].encode()).hexdigest()
        assert row["auth_token_hash"] == expected_hash, "token hash mismatch"
        assert row["auth_token_hash"] != reg_r["token"], "plaintext token stored!"
        assert row["active"] == 1
    finally:
        cleanup(test_dir)


def test_register_invalid_code_returns_403():
    """Registration with an invalid code returns 403."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import register_branch, BranchRegisterIn
        from fastapi import HTTPException
        try:
            register_branch(BranchRegisterIn(
                code="999999", branch_name="Test", branch_id="BR-TEST1",
            ))
            assert False, "should have raised 403"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        cleanup(test_dir)


def test_code_is_single_use():
    """A registration code cannot be consumed twice."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
        )
        from fastapi import HTTPException
        code_r = generate_branch_pairing_code({})
        # First use succeeds
        register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Branch 1", branch_id="BR-TEST2",
        ))
        # Second use fails (single-use)
        try:
            register_branch(BranchRegisterIn(
                code=code_r["code"], branch_name="Branch 2", branch_id="BR-TEST3",
            ))
            assert False, "should have raised 403 (single-use)"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        cleanup(test_dir)


def test_register_invalid_code_format_returns_400():
    """Registration with a non-6-digit code returns 400."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import register_branch, BranchRegisterIn
        from fastapi import HTTPException
        try:
            register_branch(BranchRegisterIn(
                code="12345", branch_name="Test", branch_id="BR-X",
            ))
            assert False, "should have raised 400"
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        cleanup(test_dir)


def test_list_branches():
    """GET /api/hq/branches lists registered branches without auth_token_hash."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn, list_branches,
        )
        for i, name in enumerate(["Branch A", "Branch B", "Branch C"]):
            code_r = generate_branch_pairing_code({})
            register_branch(BranchRegisterIn(
                code=code_r["code"], branch_name=name, region="Region",
                branch_id=f"BR-LIST{i}",
            ))
        r = list_branches()
        assert r["count"] == 3
        for b in r["branches"]:
            assert "auth_token_hash" not in b, "auth_token_hash leaked in list response"
            assert "name" in b
            assert "branch_id" in b
    finally:
        cleanup(test_dir)


def test_list_branches_active_only():
    """GET /api/hq/branches?active_only=true returns only active branches."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
            list_branches, revoke_branch,
        )
        # Register 2 branches
        code_r1 = generate_branch_pairing_code({})
        reg1 = register_branch(BranchRegisterIn(
            code=code_r1["code"], branch_name="Active Branch", branch_id="BR-ACT1",
        ))
        code_r2 = generate_branch_pairing_code({})
        reg2 = register_branch(BranchRegisterIn(
            code=code_r2["code"], branch_name="To Revoke", branch_id="BR-REV1",
        ))
        # Revoke the second one
        from app import db
        with db.conn() as c:
            rev_id = c.execute("SELECT id FROM branches WHERE branch_id='BR-REV1'").fetchone()["id"]
        revoke_branch(rev_id)
        # List active_only
        r = list_branches(active_only=True)
        assert r["count"] == 1
        assert r["branches"][0]["branch_id"] == "BR-ACT1"
        # List all
        r_all = list_branches()
        assert r_all["count"] == 2
    finally:
        cleanup(test_dir)


def test_revoke_branch_sets_inactive():
    """DELETE /api/hq/branches/{id} sets active=0."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn, revoke_branch,
        )
        code_r = generate_branch_pairing_code({})
        register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Test", branch_id="BR-REV2",
        ))
        from app import db
        with db.conn() as c:
            bid = c.execute("SELECT id FROM branches WHERE branch_id='BR-REV2'").fetchone()["id"]
        r = revoke_branch(bid)
        assert r["ok"] is True
        with db.conn() as c:
            row = c.execute("SELECT active FROM branches WHERE id=?", (bid,)).fetchone()
        assert row["active"] == 0
    finally:
        cleanup(test_dir)


def test_revoke_already_revoked_returns_404():
    """Revoking an already-revoked branch returns 404."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn, revoke_branch,
        )
        from fastapi import HTTPException
        code_r = generate_branch_pairing_code({})
        register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Test", branch_id="BR-REV3",
        ))
        from app import db
        with db.conn() as c:
            bid = c.execute("SELECT id FROM branches WHERE branch_id='BR-REV3'").fetchone()["id"]
        revoke_branch(bid)
        try:
            revoke_branch(bid)
            assert False, "should have raised 404"
        except HTTPException as e:
            assert e.status_code == 404
    finally:
        cleanup(test_dir)


def test_verify_branch_token():
    """verify_branch_token authenticates a valid Bearer token + updates last_seen."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn, verify_branch_token,
        )
        code_r = generate_branch_pairing_code({})
        reg_r = register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Test", branch_id="BR-VRF1",
        ))
        # Build a fake Request with the Bearer header
        class FakeRequest:
            def __init__(self, token):
                self.headers = {"Authorization": f"Bearer {token}"}
        branch = verify_branch_token(FakeRequest(reg_r["token"]))
        assert branch["branch_id"] == "BR-VRF1"
        assert branch["name"] == "Test"
        # last_seen should now be set
        assert branch["last_seen"] is not None
    finally:
        cleanup(test_dir)


def test_verify_branch_token_rejects_invalid():
    """verify_branch_token rejects an invalid/missing token with 401."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import verify_branch_token
        from fastapi import HTTPException
        class FakeRequest:
            def __init__(self, auth):
                self.headers = {"Authorization": auth} if auth else {}
        # Missing header
        try:
            verify_branch_token(FakeRequest(None))
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
        # Invalid token
        try:
            verify_branch_token(FakeRequest("Bearer invalid-token-string"))
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_verify_branch_token_rejects_revoked():
    """verify_branch_token rejects a token from a revoked branch."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
            revoke_branch, verify_branch_token,
        )
        from fastapi import HTTPException
        code_r = generate_branch_pairing_code({})
        reg_r = register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Test", branch_id="BR-REVR",
        ))
        from app import db
        with db.conn() as c:
            bid = c.execute("SELECT id FROM branches WHERE branch_id='BR-REVR'").fetchone()["id"]
        revoke_branch(bid)
        class FakeRequest:
            def __init__(self, token):
                self.headers = {"Authorization": f"Bearer {token}"}
        try:
            verify_branch_token(FakeRequest(reg_r["token"]))
            assert False, "should raise 401 (revoked)"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_re_registration_replaces_token():
    """Re-registering with the same branch_id replaces the existing token."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
        )
        # First registration
        code1 = generate_branch_pairing_code({})
        reg1 = register_branch(BranchRegisterIn(
            code=code1["code"], branch_name="Original Name", branch_id="BR-REREG",
        ))
        # Second registration with same branch_id (new code)
        code2 = generate_branch_pairing_code({})
        reg2 = register_branch(BranchRegisterIn(
            code=code2["code"], branch_name="Updated Name", branch_id="BR-REREG",
        ))
        # Token should be different
        assert reg1["token"] != reg2["token"]
        # Old token should no longer work, new one should
        from app.routers.hq import verify_branch_token
        class FakeRequest:
            def __init__(self, token):
                self.headers = {"Authorization": f"Bearer {token}"}
        # Old token fails
        from fastapi import HTTPException
        try:
            verify_branch_token(FakeRequest(reg1["token"]))
            assert False, "old token should be invalid"
        except HTTPException as e:
            assert e.status_code == 401
        # New token works
        branch = verify_branch_token(FakeRequest(reg2["token"]))
        assert branch["name"] == "Updated Name"
    finally:
        cleanup(test_dir)


def test_registration_logs_activity():
    """Branch registration logs a 'branch_registered' activity entry."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            generate_branch_pairing_code, register_branch, BranchRegisterIn,
        )
        from app import db
        code_r = generate_branch_pairing_code({})
        register_branch(BranchRegisterIn(
            code=code_r["code"], branch_name="Activity Test", branch_id="BR-ACTLOG",
        ))
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='branch_registered' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert "Activity Test" in row["description"]
    finally:
        cleanup(test_dir)


def test_185_88_still_passes():
    """The 185.88 running weighted avg test still passes after Phase 2 changes."""
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
            row = c.execute("SELECT current_qty FROM category_stock_state WHERE category_id=1").fetchone()
            assert row["current_qty"] == 17000
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_branches_table_exists(); print("OK branches tables exist")
    test_generate_pairing_code(); print("OK generate pairing code")
    test_register_branch_issues_token(); print("OK register issues token")
    test_register_invalid_code_returns_403(); print("OK invalid code 403")
    test_code_is_single_use(); print("OK code single-use")
    test_register_invalid_code_format_returns_400(); print("OK bad code format 400")
    test_list_branches(); print("OK list branches (no token leak)")
    test_list_branches_active_only(); print("OK list active only")
    test_revoke_branch_sets_inactive(); print("OK revoke sets inactive")
    test_revoke_already_revoked_returns_404(); print("OK revoke twice 404")
    test_verify_branch_token(); print("OK verify token")
    test_verify_branch_token_rejects_invalid(); print("OK verify rejects invalid")
    test_verify_branch_token_rejects_revoked(); print("OK verify rejects revoked")
    test_re_registration_replaces_token(); print("OK re-registration replaces token")
    test_registration_logs_activity(); print("OK registration logs activity")
    test_185_88_still_passes(); print("OK 185.88 still passes")
    print("\nALL v8.0 PHASE 2 TESTS PASSED")
