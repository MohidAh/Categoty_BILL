"""v8.0 Phase 3 — Consolidated Visibility (Owner Hub) tests.

Verifies:
- branch_summaries + sync_outbox tables exist
- POST /api/sync/branch-summary (Bearer auth, idempotent by branch_id+summary_date)
- Sync endpoint rejects invalid/missing tokens (401)
- Re-delivery updates the same row (idempotent)
- GET /api/hq/owner-hub returns consolidated totals + leaderboard + per-branch breakdown
- Stale badge appears when a branch hasn't synced in 24h
- 185.88 still passes
"""
import os, sys, tempfile, shutil, json, hashlib
from pathlib import Path
from datetime import datetime, timedelta
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_p3_")
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
                  "branches", "branch_pairing_codes", "branch_summaries", "sync_outbox"):
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



def _register_branch(branch_id="BR-TEST1", name="Test Branch"):
    """Helper: generate code + register a branch. Returns (branch_id, raw_token)."""
    from app.routers.hq import generate_branch_pairing_code, register_branch, BranchRegisterIn
    code_r = generate_branch_pairing_code({})
    reg_r = register_branch(BranchRegisterIn(
        code=code_r["code"], branch_name=name, branch_id=branch_id,
    ))
    return branch_id, reg_r["token"]


class FakeRequest:
    def __init__(self, token=None):
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}


def test_branch_summaries_table_exists():
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "branch_summaries" in tables
        assert "sync_outbox" in tables
    finally:
        cleanup(test_dir)


def test_receive_branch_summary_requires_token():
    """POST /api/sync/branch-summary without a Bearer token returns 401."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import receive_branch_summary, BranchSummaryIn
        from fastapi import HTTPException
        try:
            receive_branch_summary(
                BranchSummaryIn(summary_date="2026-08-13", sales=1000),
                FakeRequest(None),
            )
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_receive_branch_summary_invalid_token():
    """POST /api/sync/branch-summary with invalid token returns 401."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import receive_branch_summary, BranchSummaryIn
        from fastapi import HTTPException
        try:
            receive_branch_summary(
                BranchSummaryIn(summary_date="2026-08-13", sales=1000),
                FakeRequest("invalid-token"),
            )
            assert False, "should raise 401"
        except HTTPException as e:
            assert e.status_code == 401
    finally:
        cleanup(test_dir)


def test_receive_branch_summary_stores_row():
    """Valid token + summary → row stored in branch_summaries."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import receive_branch_summary, BranchSummaryIn
        from app import db
        branch_id, token = _register_branch()
        r = receive_branch_summary(
            BranchSummaryIn(
                summary_date="2026-08-13", sales=1500, cogs=900,
                gross_profit=600, expenses=200, cash_in_drawer=400,
                stock_snapshot={"1": {"qty": 100, "value": 8000, "avg_cost": 80}},
            ),
            FakeRequest(token),
        )
        assert r["ok"] is True
        assert r["branch_id"] == branch_id
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM branch_summaries WHERE branch_id=? AND summary_date=?",
                (branch_id, "2026-08-13"),
            ).fetchone()
        assert row is not None
        assert row["sales"] == 1500
        assert row["gross_profit"] == 600
        assert row["cash_in_drawer"] == 400
        stock = json.loads(row["stock_snapshot_json"])
        assert stock["1"]["qty"] == 100
    finally:
        cleanup(test_dir)


def test_receive_branch_summary_idempotent():
    """Re-delivery with same (branch_id, summary_date) updates the row, doesn't duplicate."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import receive_branch_summary, BranchSummaryIn
        from app import db
        branch_id, token = _register_branch()
        # First push
        receive_branch_summary(
            BranchSummaryIn(summary_date="2026-08-13", sales=1000),
            FakeRequest(token),
        )
        # Second push (re-delivery) with updated sales
        receive_branch_summary(
            BranchSummaryIn(summary_date="2026-08-13", sales=1500),
            FakeRequest(token),
        )
        with db.conn() as c:
            rows = c.execute(
                "SELECT * FROM branch_summaries WHERE branch_id=? AND summary_date=?",
                (branch_id, "2026-08-13"),
            ).fetchall()
        assert len(rows) == 1, f"expected 1 row, got {len(rows)} (not idempotent)"
        assert rows[0]["sales"] == 1500, "sales not updated"
    finally:
        cleanup(test_dir)


def test_owner_hub_consolidated_totals():
    """GET /api/hq/owner-hub sums sales across all branches."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            receive_branch_summary, BranchSummaryIn, owner_hub_dashboard,
        )
        # Register 2 branches + push summaries
        bid1, tok1 = _register_branch("BR-HUB1", "Branch A")
        bid2, tok2 = _register_branch("BR-HUB2", "Branch B")
        today = datetime.now().strftime("%Y-%m-%d")
        receive_branch_summary(
            BranchSummaryIn(summary_date=today, sales=1000, gross_profit=400, cash_in_drawer=200),
            FakeRequest(tok1),
        )
        receive_branch_summary(
            BranchSummaryIn(summary_date=today, sales=2000, gross_profit=800, cash_in_drawer=300),
            FakeRequest(tok2),
        )
        r = owner_hub_dashboard(date=today)
        assert r["consolidated"]["sales"] == 3000
        assert r["consolidated"]["gross_profit"] == 1200
        assert r["consolidated"]["cash_in_drawer"] == 500
        assert r["branch_count"] == 2
        assert r["active_branches_synced_today"] == 2
    finally:
        cleanup(test_dir)


def test_owner_hub_leaderboard_sorted_by_sales():
    """Leaderboard is sorted by sales descending."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import (
            receive_branch_summary, BranchSummaryIn, owner_hub_dashboard,
        )
        bid1, tok1 = _register_branch("BR-LOW", "Low Sales Branch")
        bid2, tok2 = _register_branch("BR-HIGH", "High Sales Branch")
        today = datetime.now().strftime("%Y-%m-%d")
        receive_branch_summary(
            BranchSummaryIn(summary_date=today, sales=500),
            FakeRequest(tok1),
        )
        receive_branch_summary(
            BranchSummaryIn(summary_date=today, sales=5000),
            FakeRequest(tok2),
        )
        r = owner_hub_dashboard(date=today)
        lb = r["leaderboard"]
        assert lb[0]["branch_id"] == "BR-HIGH"
        assert lb[0]["sales"] == 5000
        assert lb[1]["branch_id"] == "BR-LOW"
        assert lb[1]["sales"] == 500
    finally:
        cleanup(test_dir)


def test_owner_hub_stale_branch_flagged():
    """Branches that haven't synced in 24h show stale=True."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import owner_hub_dashboard
        from app import db
        # Register a branch with an old last_seen (3 days ago)
        with db.conn() as c:
            old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "INSERT INTO branches(branch_id, name, region, auth_token_hash, last_seen, active) "
                "VALUES(?,?,?,?,?,1)",
                ("BR-STALE", "Stale Branch", "Region", "fakehash", old),
            )
        today = datetime.now().strftime("%Y-%m-%d")
        r = owner_hub_dashboard(date=today)
        stale = [b for b in r["branches"] if b["stale"]]
        assert len(stale) == 1
        assert stale[0]["branch_id"] == "BR-STALE"
    finally:
        cleanup(test_dir)


def test_owner_hub_fresh_branch_not_stale():
    """Branches that synced recently are NOT stale."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import owner_hub_dashboard
        from app import db
        with db.conn() as c:
            recent = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute(
                "INSERT INTO branches(branch_id, name, region, auth_token_hash, last_seen, active) "
                "VALUES(?,?,?,?,?,1)",
                ("BR-FRESH", "Fresh Branch", "Region", "fakehash", recent),
            )
        today = datetime.now().strftime("%Y-%m-%d")
        r = owner_hub_dashboard(date=today)
        fresh = [b for b in r["branches"] if b["branch_id"] == "BR-FRESH"]
        assert len(fresh) == 1
        assert fresh[0]["stale"] is False
    finally:
        cleanup(test_dir)


def test_owner_hub_no_branches():
    """Owner Hub with zero registered branches returns empty results."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import owner_hub_dashboard
        r = owner_hub_dashboard()
        assert r["branch_count"] == 0
        assert r["consolidated"]["sales"] == 0
        assert len(r["leaderboard"]) == 0
    finally:
        cleanup(test_dir)


def test_owner_hub_default_date_is_today():
    """GET /api/hq/owner-hub with no date param defaults to today."""
    test_dir = setup_test_db()
    try:
        from app.routers.hq import owner_hub_dashboard
        r = owner_hub_dashboard()
        today = datetime.now().strftime("%Y-%m-%d")
        assert r["date"] == today
    finally:
        cleanup(test_dir)


def test_185_88_still_passes():
    """The 185.88 running weighted avg test still passes after Phase 3 changes."""
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
    test_branch_summaries_table_exists(); print("OK tables exist")
    test_receive_branch_summary_requires_token(); print("OK sync requires token")
    test_receive_branch_summary_invalid_token(); print("OK sync rejects invalid token")
    test_receive_branch_summary_stores_row(); print("OK sync stores row")
    test_receive_branch_summary_idempotent(); print("OK sync idempotent")
    test_owner_hub_consolidated_totals(); print("OK consolidated totals")
    test_owner_hub_leaderboard_sorted_by_sales(); print("OK leaderboard sorted")
    test_owner_hub_stale_branch_flagged(); print("OK stale branch flagged")
    test_owner_hub_fresh_branch_not_stale(); print("OK fresh branch not stale")
    test_owner_hub_no_branches(); print("OK no branches empty result")
    test_owner_hub_default_date_is_today(); print("OK default date is today")
    test_185_88_still_passes(); print("OK 185.88 still passes")
    print("\nALL v8.0 PHASE 3 TESTS PASSED")
