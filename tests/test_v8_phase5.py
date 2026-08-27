"""v8.0 Phase 5 — Central Purchasing & Distribution tests.

Verifies:
- central_purchases + central_purchase_items tables exist
- POST /api/central-purchases records a bulk buy (applies purchase to local state)
- GET /api/central-purchases lists purchases
- GET /api/central-purchases/{id} returns purchase + items + distribution status
- POST /api/central-purchases/{id}/distribute creates a transfer challan from BR-CENTRAL
- Distribution uses the central unit_cost (not current avg) — branches receive at bulk-buy price
- distributed_qty + remaining_qty track per-line distribution
- Status flips to 'distributed' when all items fully distributed
- Cannot distribute more than remaining
- 185.88 still passes
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_p5_")
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
                  "central_purchases", "central_purchase_items"):
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



def _register_branch(branch_id="BR-A", name="Branch A"):
    from app.routers.hq import generate_branch_pairing_code, register_branch, BranchRegisterIn
    code_r = generate_branch_pairing_code({})
    reg_r = register_branch(BranchRegisterIn(
        code=code_r["code"], branch_name=name, branch_id=branch_id,
    ))
    return branch_id


def test_central_purchases_tables_exist():
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "central_purchases" in tables
        assert "central_purchase_items" in tables
    finally:
        cleanup(test_dir)


def test_create_central_purchase():
    """POST /api/central-purchases records a bulk buy + applies to local state."""
    test_dir = setup_test_db()
    try:
        from app.routers.central import create_central_purchase, CentralPurchaseIn
        from app.profit_engine import peek_avg_cost
        from app import db
        # Reset Cat 1 state
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        r = create_central_purchase(CentralPurchaseIn(
            supplier_name="ABC Trading",
            lines=[{"category_id": 1, "qty": 10000, "unit_cost": 180}],
            notes="Eid bulk buy",
        ))
        assert r["purchase_no"].startswith("CP-")
        assert r["total_qty"] == 10000
        assert r["total_cost"] == 1800000  # 10000 * 180
        # Verify local state now has 10000 @ 180
        with db.conn() as c:
            assert peek_avg_cost(c, 1) == 180.0
            row = c.execute("SELECT current_qty FROM category_stock_state WHERE category_id=1").fetchone()
            assert row["current_qty"] == 10000
    finally:
        cleanup(test_dir)


def test_get_central_purchase_with_items():
    """GET /api/central-purchases/{id} returns purchase + items with distribution status."""
    test_dir = setup_test_db()
    try:
        from app.routers.central import create_central_purchase, CentralPurchaseIn, get_central_purchase
        r = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 1000, "unit_cost": 100}],
        ))
        detail = get_central_purchase(r["purchase_id"])
        assert detail["purchase"]["purchase_no"] == r["purchase_no"]
        assert len(detail["items"]) == 1
        item = detail["items"][0]
        assert item["qty"] == 1000
        assert item["unit_cost"] == 100
        assert item["distributed_qty"] == 0
        assert item["remaining_qty"] == 1000
    finally:
        cleanup(test_dir)


def test_distribute_creates_challan_at_central_unit_cost():
    """POST /api/central-purchases/{id}/distribute creates a challan at the central unit_cost."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
        )
        from app import db
        # Reset Cat 1 state + create a central purchase
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 10000, "unit_cost": 180}],
        ))
        # Distribute 4000 to Branch A
        dist = distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-A",
            lines=[{"category_id": 1, "qty": 4000}],
        ))
        assert dist["challan_no"].startswith("CH-")
        assert dist["from_branch_id"] == "BR-CENTRAL"
        assert dist["to_branch_id"] == "BR-A"
        assert dist["total_qty"] == 4000
        assert abs(dist["total_value"] - 720000) < 0.01  # 4000 * 180
        assert dist["purchase_status"] == "partial"
        # Verify the challan item has the central unit_cost (180), not current avg
        with db.conn() as c:
            ci = c.execute(
                "SELECT unit_cost FROM transfer_challan_items WHERE challan_id=?",
                (dist["challan_id"],),
            ).fetchone()
        assert ci["unit_cost"] == 180, f"expected 180 (central), got {ci['unit_cost']}"
    finally:
        cleanup(test_dir)


def test_distribute_updates_distributed_qty():
    """Distribution updates distributed_qty + remaining_qty on central purchase items."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
            get_central_purchase,
        )
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 10000, "unit_cost": 180}],
        ))
        distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-A",
            lines=[{"category_id": 1, "qty": 4000}],
        ))
        detail = get_central_purchase(cp["purchase_id"])
        item = detail["items"][0]
        assert item["distributed_qty"] == 4000
        assert item["remaining_qty"] == 6000
    finally:
        cleanup(test_dir)


def test_distribute_fully_sets_status_distributed():
    """When all items are fully distributed, central purchase status → 'distributed'."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
            get_central_purchase,
        )
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 10000, "unit_cost": 180}],
        ))
        distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-A",
            lines=[{"category_id": 1, "qty": 10000}],  # all of it
        ))
        detail = get_central_purchase(cp["purchase_id"])
        assert detail["purchase"]["status"] == "distributed"
        assert detail["items"][0]["remaining_qty"] == 0
    finally:
        cleanup(test_dir)


def test_cannot_distribute_more_than_remaining():
    """Distributing more than remaining returns 400."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
        )
        from fastapi import HTTPException
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 1000, "unit_cost": 100}],
        ))
        # Try to distribute 2000 (more than 1000)
        try:
            distribute_central_purchase(cp["purchase_id"], DistributeIn(
                to_branch_id="BR-A",
                lines=[{"category_id": 1, "qty": 2000}],
            ))
            assert False, "should raise 400"
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        cleanup(test_dir)


def test_distribute_to_multiple_branches():
    """Buy 10,000 centrally → distribute 4,000 to A + 6,000 to B → each at central cost."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        _register_branch("BR-B", "Branch B")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
            get_central_purchase,
        )
        from app.routers.transfers import accept_transfer
        from app.profit_engine import peek_avg_cost
        from app import db
        # Reset Cat 1 state
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        # Buy 10,000 @ 180 centrally
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 10000, "unit_cost": 180}],
        ))
        # Distribute 4,000 to A
        dist_a = distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-A",
            lines=[{"category_id": 1, "qty": 4000}],
        ))
        # Distribute 6,000 to B
        dist_b = distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-B",
            lines=[{"category_id": 1, "qty": 6000}],
        ))
        # Verify central purchase is fully distributed
        detail = get_central_purchase(cp["purchase_id"])
        assert detail["purchase"]["status"] == "distributed"
        assert detail["items"][0]["remaining_qty"] == 0
        # Verify both challans use the central unit_cost (180)
        with db.conn() as c:
            for dist in [dist_a, dist_b]:
                ci = c.execute(
                    "SELECT unit_cost FROM transfer_challan_items WHERE challan_id=?",
                    (dist["challan_id"],),
                ).fetchone()
                assert ci["unit_cost"] == 180
        # Simulate Branch A accepting (zero out local state, then accept)
        with db.conn() as c:
            c.execute("UPDATE category_stock_state SET current_qty=0, current_value=0, current_avg_cost=0 WHERE category_id=1")
        accept_transfer(dist_a["challan_id"])
        with db.conn() as c:
            assert peek_avg_cost(c, 1) == 180.0  # A's avg = central cost
            row = c.execute("SELECT current_qty FROM category_stock_state WHERE category_id=1").fetchone()
            assert row["current_qty"] == 4000
    finally:
        cleanup(test_dir)


def test_distribute_logs_activity():
    """Distribution logs a 'central_purchase_distributed' activity entry."""
    test_dir = setup_test_db()
    try:
        _register_branch("BR-A", "Branch A")
        from app.routers.central import (
            create_central_purchase, CentralPurchaseIn, distribute_central_purchase, DistributeIn,
        )
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        cp = create_central_purchase(CentralPurchaseIn(
            lines=[{"category_id": 1, "qty": 1000, "unit_cost": 100}],
        ))
        distribute_central_purchase(cp["purchase_id"], DistributeIn(
            to_branch_id="BR-A",
            lines=[{"category_id": 1, "qty": 500}],
        ))
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='central_purchase_distributed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert cp["purchase_no"] in row["description"]
    finally:
        cleanup(test_dir)


def test_18588_still_passes():
    """The 185.88 running weighted avg test still passes after Phase 5 changes."""
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
    test_central_purchases_tables_exist(); print("OK tables exist")
    test_create_central_purchase(); print("OK create central purchase")
    test_get_central_purchase_with_items(); print("OK get with items")
    test_distribute_creates_challan_at_central_unit_cost(); print("OK distribute at central cost")
    test_distribute_updates_distributed_qty(); print("OK distribute updates qty")
    test_distribute_fully_sets_status_distributed(); print("OK full distribute → status")
    test_cannot_distribute_more_than_remaining(); print("OK cannot over-distribute")
    test_distribute_to_multiple_branches(); print("OK distribute to multiple branches")
    test_distribute_logs_activity(); print("OK distribute logs activity")
    test_18588_still_passes(); print("OK 185.88 still passes")
    print("\nALL v8.0 PHASE 5 TESTS PASSED")
