"""v8.1 Phase 5 — Auto-Maintenance tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_p5_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "category_stock_state", "activity_log"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def test_create_backup():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import create_backup
        r = create_backup()
        assert r["ok"] is True
        assert "billbook_" in r["backup"]
        assert r["total_backups"] >= 1
        # Verify the backup file exists
        from app.config import BACKUPS
        files = list(BACKUPS.glob("billbook_*.db"))
        assert len(files) >= 1
    finally:
        cleanup(test_dir)


def test_backup_prunes_to_10():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import create_backup, MAX_BACKUPS
        # Create 12 backups
        for _ in range(12):
            create_backup()
        from app.config import BACKUPS
        files = list(BACKUPS.glob("billbook_*.db"))
        assert len(files) == MAX_BACKUPS, f"expected {MAX_BACKUPS}, got {len(files)}"
    finally:
        cleanup(test_dir)


def test_list_backups():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import create_backup, list_backups
        create_backup()
        r = list_backups()
        assert r["count"] >= 1
        assert "backups" in r
        assert "last_backup_at" in r
        assert r["auto_backup_enabled"] is True  # default
        b = r["backups"][0]
        assert "name" in b and "size_mb" in b and "age_hours" in b
    finally:
        cleanup(test_dir)


def test_update_check_returns_shape():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import check_for_update
        r = check_for_update()
        assert "current_version" in r
        assert "latest_version" in r
        assert "update_available" in r
    finally:
        cleanup(test_dir)


def test_compare_versions():
    from app.routers.maintenance import _compare_versions
    assert _compare_versions("8.2.0", "8.1.0") == 1
    assert _compare_versions("8.1.0", "8.1.0") == 0
    assert _compare_versions("8.0.0", "8.1.0") == -1
    assert _compare_versions("9.0.0", "8.1.0") == 1


def test_diagnose_returns_results():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import run_diagnostics
        r = run_diagnostics()
        assert "results" in r
        assert r["total"] >= 6  # at least 6 checks
        assert "green" in r and "amber" in r and "red" in r
        # DB integrity should be green
        db_check = [x for x in r["results"] if x["check"] == "Database Integrity"][0]
        assert db_check["status"] == "green"
    finally:
        cleanup(test_dir)


def test_diagnose_negative_stock():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import run_diagnostics
        from app import db
        # Force negative stock + verify it's set
        with db.conn() as c:
            c.execute("UPDATE category_stock_state SET current_qty=-5 WHERE category_id=1")
            row = c.execute("SELECT current_qty FROM category_stock_state WHERE category_id=1").fetchone()
        assert row["current_qty"] == -5, f"expected -5, got {row['current_qty']}"
        r = run_diagnostics()
        neg = [x for x in r["results"] if x["check"] == "Negative Stock"][0]
        assert neg["status"] in ("red", "amber"), f"expected red or amber, got {neg['status']}"
        assert "negative" in neg["detail"].lower() or "-5" in neg["detail"] or "could not" in neg["detail"].lower()
    finally:
        cleanup(test_dir)


def test_auto_backup_toggle():
    test_dir = setup_test_db()
    try:
        from app.routers.maintenance import toggle_auto_backup
        from app import db
        r = toggle_auto_backup({"enabled": False})
        assert r["ok"] is True
        assert db.get_setting("auto_backup_enabled", "") == "false"
        toggle_auto_backup({"enabled": True})
        assert db.get_setting("auto_backup_enabled", "") == "true"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_create_backup(); print("OK create backup")
    test_backup_prunes_to_10(); print("OK backup prunes to 10")
    test_list_backups(); print("OK list backups")
    test_update_check_returns_shape(); print("OK update check shape")
    test_compare_versions(); print("OK compare versions")
    test_diagnose_returns_results(); print("OK diagnose returns results")
    test_diagnose_negative_stock(); print("OK diagnose negative stock")
    test_auto_backup_toggle(); print("OK auto backup toggle")
    print("\nALL v8.1 PHASE 5 TESTS PASSED")
