"""v8.1 Phase 4 — Remote Access toggle tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))




def test_status_returns_shape():
    test_dir = setup_test_db()
    try:
        from app.routers.remote_access import remote_access_status
        r = remote_access_status()
        assert "running" in r
        assert "url" in r
        assert "uptime_seconds" in r
        assert "enabled" in r
        assert "cloudflared_installed" in r
    finally:
        cleanup(test_dir)


def test_status_default_not_running():
    test_dir = setup_test_db()
    try:
        from app.routers.remote_access import remote_access_status
        r = remote_access_status()
        assert r["running"] is False
        assert r["enabled"] is False
    finally:
        cleanup(test_dir)


def test_start_without_cloudflared_returns_400():
    test_dir = setup_test_db()
    try:
        from app.routers.remote_access import remote_access_start, _find_cloudflared
        # Mock: cloudflared not found
        import app.routers.remote_access as ra
        orig = ra._find_cloudflared
        ra._find_cloudflared = lambda: None
        try:
            from fastapi import HTTPException
            try:
                remote_access_start()
                assert False, "should raise 400"
            except HTTPException as e:
                assert e.status_code == 400
                assert "cloudflared" in e.detail.lower()
        finally:
            ra._find_cloudflared = orig
    finally:
        cleanup(test_dir)


def test_stop_when_not_running():
    test_dir = setup_test_db()
    try:
        from app.routers.remote_access import remote_access_stop
        r = remote_access_stop()
        assert r["ok"] is True
        assert "was not running" in r.get("note", "")
    finally:
        cleanup(test_dir)


def test_stop_persists_setting():
    test_dir = setup_test_db()
    try:
        from app.routers.remote_access import remote_access_stop
        from app import db
        db.set_setting("remote_access_enabled", "true")
        remote_access_stop()
        assert db.get_setting("remote_access_enabled", "") == "false"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_status_returns_shape(); print("OK status returns shape")
    test_status_default_not_running(); print("OK status default not running")
    test_start_without_cloudflared_returns_400(); print("OK start without cloudflared 400")
    test_stop_when_not_running(); print("OK stop when not running")
    test_stop_persists_setting(); print("OK stop persists setting")
    print("\nALL v8.1 PHASE 4 TESTS PASSED")
