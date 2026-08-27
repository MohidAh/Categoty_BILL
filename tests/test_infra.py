"""Phase 0 PR 8: Infrastructure tests — /api/health + /api/version.

Verifies:
- /api/health returns 200 + "ok" status on a healthy fresh DB
- /api/health returns 503 + "down" status when DB is unreachable
- /api/health reports stock_state dirty flag + last_rebuilt_at timestamp
- /api/health reports disk_free_mb + wal_size_mb
- /api/version returns the 4 expected keys (version, version_name, python, git_commit)
- /api/health + /api/version are PUBLIC (accessible without a session cookie)

Run with: pytest tests/test_infra.py -v
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest
from test_helpers import setup_test_db, cleanup

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))




def test_health_endpoint_returns_200_when_healthy():
    """/api/health on a fresh DB returns 200 + status='ok' (or 'degraded' if
    stock_state is stale, but never 'down')."""
    test_dir = setup_test_db()
    try:
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/health")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert "status" in body, f"Missing 'status' key: {body}"
        assert body["status"] in ("ok", "degraded"), (
            f"Status should be 'ok' or 'degraded' on a healthy DB, got {body['status']}"
        )
        assert "checks" in body
        checks = body["checks"]
        # DB check must be ok
        assert checks.get("db") == "ok", f"DB check should be 'ok': {checks.get('db')}"
        # Stock state must include dirty + last_rebuilt_at
        assert "stock_state" in checks, f"Missing stock_state check: {checks}"
        ss = checks["stock_state"]
        assert isinstance(ss, dict), f"stock_state should be a dict: {ss}"
        assert "dirty" in ss, f"Missing 'dirty' flag: {ss}"
        assert "last_rebuilt_at" in ss, f"Missing 'last_rebuilt_at': {ss}"
        # Disk check
        assert "disk_free_mb" in checks, f"Missing disk_free_mb: {checks}"
        assert isinstance(checks["disk_free_mb"], int)
        assert checks["disk_free_mb"] > 0, "Should have >0 MB free"
        # WAL check
        assert "wal_size_mb" in checks, f"Missing wal_size_mb: {checks}"
        assert isinstance(checks["wal_size_mb"], int)
        assert checks["wal_size_mb"] >= 0
    finally:
        cleanup(test_dir)


def test_health_endpoint_returns_503_when_db_locked():
    """/api/health returns 503 + status='down' when the DB is unreachable.

    We simulate this by pointing DB_PATH at a path that requires a directory
    which doesn't exist — sqlite3.connect will fail.
    """
    test_dir = setup_test_db()
    try:
        from app import db
        from app.main import app
        from fastapi.testclient import TestClient

        # Corrupt DB_PATH: point at a non-existent directory
        # (sqlite3.connect won't fail directly, but the SELECT 1 will)
        db.DB_PATH = "/nonexistent/path/that/does/not/exist/billbook.db"

        client = TestClient(app)
        r = client.get("/api/health")
        assert r.status_code == 503, (
            f"Expected 503 when DB unreachable, got {r.status_code}: {r.text}"
        )
        body = r.json()
        assert body["status"] == "down", (
            f"Status should be 'down' when DB fails, got {body['status']}"
        )
        assert "error" in body["checks"]["db"], (
            f"DB check should contain 'error: ...', got {body['checks']['db']}"
        )
    finally:
        cleanup(test_dir)


def test_health_endpoint_reports_stale_rebuild():
    """/api/health flags 'degraded' if the last stock_state rebuild was
    more than 7 days ago (the 'stale' indicator)."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.main import app
        from fastapi.testclient import TestClient

        # Seed an old rebuild timestamp (10 days ago)
        from datetime import datetime, timedelta
        old_ts = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_setting("stock_state_last_rebuilt_at", old_ts)

        client = TestClient(app)
        r = client.get("/api/health")
        body = r.json()
        # Stale rebuild → degraded
        assert body["status"] == "degraded", (
            f"Stale rebuild (>7d) should be 'degraded', got {body['status']}"
        )
        assert body["checks"]["stock_state"]["stale"] is True, (
            f"stale flag should be True: {body['checks']['stock_state']}"
        )
    finally:
        cleanup(test_dir)


def test_version_endpoint_returns_version_dict():
    """/api/version returns version + version_name + python + git_commit."""
    test_dir = setup_test_db()
    try:
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert "version" in body, f"Missing 'version': {body}"
        assert "version_name" in body, f"Missing 'version_name': {body}"
        assert "python" in body, f"Missing 'python': {body}"
        assert "git_commit" in body, f"Missing 'git_commit': {body}"
        # version should match APP_VERSION constant
        assert body["version"] == "8.16.0", f"Version should be 8.16.0, got {body['version']}"
        # Python version should be a non-empty string
        assert body["python"], "Python version should be non-empty"
        # git_commit defaults to 'dev' if env var not set
        assert body["git_commit"] in ("dev", os.environ.get("GIT_COMMIT", "dev"))
    finally:
        cleanup(test_dir)


def test_health_and_version_are_public():
    """/api/health and /api/version must be accessible WITHOUT a session cookie
    (they're added to public_paths so the auth middleware skips them)."""
    test_dir = setup_test_db()
    try:
        from app.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app)
        # No cookies set — direct GET
        r1 = client.get("/api/health", follow_redirects=False)
        assert r1.status_code != 401, "Health endpoint should not require auth"
        assert r1.status_code != 307, "Health endpoint should not redirect to /login"
        r2 = client.get("/api/version", follow_redirects=False)
        assert r2.status_code != 401, "Version endpoint should not require auth"
        assert r2.status_code != 307, "Version endpoint should not redirect to /login"
    finally:
        cleanup(test_dir)


def test_rebuild_stock_state_records_last_rebuilt_at():
    """Calling rebuild_stock_state() updates the stock_state_last_rebuilt_at
    setting, which /api/health reads."""
    test_dir = setup_test_db()
    try:
        from app import db, profit
        # Before rebuild: setting should be empty or absent on a fresh DB
        before = db.get_setting("stock_state_last_rebuilt_at", "")
        # Run rebuild
        profit.rebuild_stock_state()
        after = db.get_setting("stock_state_last_rebuilt_at", "")
        assert after, f"last_rebuilt_at should be set after rebuild, got '{after}'"
        assert after != before, f"Timestamp should change: before='{before}' after='{after}'"
        # Verify format: YYYY-MM-DD HH:MM:SS
        from datetime import datetime
        datetime.strptime(after, "%Y-%m-%d %H:%M:%S")  # raises if malformed
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    import traceback
    tests = [
        test_health_endpoint_returns_200_when_healthy,
        test_health_endpoint_returns_503_when_db_locked,
        test_health_endpoint_reports_stale_rebuild,
        test_version_endpoint_returns_version_dict,
        test_health_and_version_are_public,
        test_rebuild_stock_state_records_last_rebuilt_at,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  [PASS] {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
