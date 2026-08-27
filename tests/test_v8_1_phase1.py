"""v8.1 Phase 1 — First-Launch Wizard tests.

Verifies:
- GET /api/setup/state returns correct shape (initialized, setup_completed, has_categories, start_page)
- POST /api/setup/wizard orchestrates password + categories + start_page + setup_completed
- Wizard is idempotent (second call returns 400)
- Fresh DB → wizard available; completed DB → wizard rejected
- Existing v8.0 DB (setup_completed absent but password set) → wizard rejects, login works
- Categories are seeded from the template when not provided
- Custom categories are used when provided
- Gemini key is stored (optional)
- start_page is validated + stored
- Activity log entry written
"""
import os, sys, tempfile, shutil, hashlib, json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_fresh_db():
    """Fresh DB with no password, no setup_completed — wizard should be available."""
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_fresh_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    # Clear any password + setup_completed that init() might set
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key IN ('password_hash', 'setup_completed', 'start_page')")
        c.execute("DELETE FROM price_categories")
    return test_dir


def setup_completed_db():
    """DB with password + setup_completed=true — wizard should be rejected."""
    test_dir = tempfile.mkdtemp(prefix="billbook_v81_done_")
    from app import config, db, security
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    db.set_setting("password_hash", security.hash_password("existing123"))
    db.set_setting("setup_completed", "true")
    db.set_setting("start_page", "launcher")
    return test_dir


def setup_v80_db():
    """Existing v8.0 DB — password set but setup_completed absent (pre-wizard)."""
    test_dir = tempfile.mkdtemp(prefix="billbook_v80_legacy_")
    from app import config, db, security
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = test_dir
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    db.set_setting("password_hash", security.hash_password("legacy123"))
    # NO setup_completed setting — this is a pre-v8.1 DB
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key='setup_completed'")
    return test_dir


def cleanup(t): shutil.rmtree(t, ignore_errors=True)


def _parse_response(r):
    """Helper: parse a JSONResponse body into a dict."""
    import json
    if hasattr(r, 'body'):
        return json.loads(r.body.decode('utf-8'))
    return r


def test_setup_state_fresh_db():
    """Fresh DB: initialized=false, setup_completed=false, has_categories=false."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard_state
        state = setup_wizard_state()
        assert state["initialized"] is False
        assert state["setup_completed"] is False
        assert state["has_categories"] is False
        assert state["category_count"] == 0
        assert state["start_page"] == "launcher"
    finally:
        cleanup(test_dir)


def test_setup_state_completed_db():
    """Completed DB: initialized=true, setup_completed=true."""
    test_dir = setup_completed_db()
    try:
        from app.routers.auth import setup_wizard_state
        state = setup_wizard_state()
        assert state["initialized"] is True
        assert state["setup_completed"] is True
    finally:
        cleanup(test_dir)


def test_wizard_completes_on_fresh_db():
    """POST /api/setup/wizard on fresh DB → password set, categories seeded, setup_completed=true."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db, security
        r = setup_wizard(WizardIn(
            password="mysecret123",
            business_type="wholesale",
            categories=[],  # should use template
            gemini_key="",
            start_page="dashboard",
        ))
        data = _parse_response(r)
        assert data["ok"] is True
        assert data["start_page"] == "dashboard"
        assert data["category_count"] == 4  # wholesale template has 4
        # Verify password was set
        assert db.get_setting("password_hash", "")
        # Verify setup_completed
        assert db.get_setting("setup_completed", "") == "true"
        # Verify start_page
        assert db.get_setting("start_page", "") == "dashboard"
        # Verify categories were seeded
        with db.conn() as c:
            cats = c.execute("SELECT * FROM price_categories ORDER BY sort_order").fetchall()
        assert len(cats) == 4
        assert cats[0]["code"] == "A"
        assert cats[0]["sell_price"] == 250
        assert cats[3]["code"] == "D"
        assert cats[3]["sell_price"] == 1000
    finally:
        cleanup(test_dir)


def test_wizard_rejects_short_password():
    """Wizard rejects passwords < 8 chars."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from fastapi import HTTPException
        try:
            setup_wizard(WizardIn(password="short"))
            assert False, "should raise 400"
        except HTTPException as e:
            assert "at least 8" in e.detail.lower() or e.status_code == 400
    finally:
        cleanup(test_dir)


def test_wizard_rejects_already_completed():
    """Wizard on completed DB returns 400."""
    test_dir = setup_completed_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from fastapi import HTTPException
        try:
            setup_wizard(WizardIn(password="newpassword123"))
            assert False, "should raise 400"
        except HTTPException as e:
            assert e.status_code == 400
            assert "already" in e.detail.lower()
    finally:
        cleanup(test_dir)


def test_wizard_rejects_already_initialized():
    """Wizard on v8.0 DB (password set, no setup_completed) returns 400."""
    test_dir = setup_v80_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from fastapi import HTTPException
        try:
            setup_wizard(WizardIn(password="newpassword123"))
            assert False, "should raise 400"
        except HTTPException as e:
            assert e.status_code == 400
            assert "already initialized" in e.detail.lower()
    finally:
        cleanup(test_dir)


def test_wizard_custom_categories():
    """Wizard with custom categories uses them instead of template."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        custom = [
            {"code": "X", "name": "Custom X", "sell_price": 150, "color": "#ff0000", "sort_order": 1},
            {"code": "Y", "name": "Custom Y", "sell_price": 300, "color": "#00ff00", "sort_order": 2},
        ]
        r = setup_wizard(WizardIn(
            password="mysecret123",
            business_type="custom",
            categories=custom,
            start_page="launcher",
        ))
        data = _parse_response(r)
        assert data["category_count"] == 2
        with db.conn() as c:
            cats = c.execute("SELECT * FROM price_categories ORDER BY sort_order").fetchall()
        assert len(cats) == 2
        assert cats[0]["code"] == "X"
        assert cats[0]["name"] == "Custom X"
        assert cats[1]["sell_price"] == 300
    finally:
        cleanup(test_dir)


def test_wizard_retail_template():
    """Wizard with retail business type seeds 3 categories."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        r = setup_wizard(WizardIn(
            password="mysecret123",
            business_type="retail",
            categories=[],
        ))
        data = _parse_response(r)
        assert data["category_count"] == 3
    finally:
        cleanup(test_dir)


def test_wizard_stores_gemini_key():
    """Wizard with gemini_key stores it in settings."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        from app.crypto import decrypt_setting_key
        setup_wizard(WizardIn(
            password="mysecret123",
            gemini_key="test-gemini-key-123",
        ))
        # PR 7b: gemini_api_key is now stored encrypted — use decrypt_setting_key
        assert decrypt_setting_key("gemini_api_key") == "test-gemini-key-123", (
            f"gemini_api_key should decrypt to original: {decrypt_setting_key('gemini_api_key')}"
        )
    finally:
        cleanup(test_dir)


def test_wizard_validates_start_page():
    """Wizard validates start_page against allowed values."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        r = setup_wizard(WizardIn(
            password="mysecret123",
            start_page="invalid_page",
        ))
        data = _parse_response(r)
        # Should default to launcher
        assert data["start_page"] == "launcher"
        assert db.get_setting("start_page", "") == "launcher"
    finally:
        cleanup(test_dir)


def test_wizard_logs_activity():
    """Wizard completion logs an activity entry."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        from app import db
        setup_wizard(WizardIn(password="mysecret123", business_type="wholesale"))
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='setup_wizard_completed' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert "4 categories" in row["description"]
    finally:
        cleanup(test_dir)


def test_wizard_creates_session():
    """Wizard returns a session cookie so the user is logged in immediately."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup_wizard, WizardIn
        r = setup_wizard(WizardIn(password="mysecret123"))
        # The response should have set a cookie (we can't check the cookie directly
        # in a function call, but we can verify a session was created in the DB)
        from app import db
        with db.conn() as c:
            sessions = c.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        assert sessions["n"] >= 1
    finally:
        cleanup(test_dir)


def test_existing_setup_endpoint_still_works():
    """The v8.0 /api/setup endpoint still works (additions only, no breaking change)."""
    test_dir = setup_fresh_db()
    try:
        from app.routers.auth import setup, SetupIn
        from app import db
        r = setup(SetupIn(password="legacysetup123"))
        # Should return a JSONResponse with ok=True
        assert r.status_code == 200
        # Password should be set
        assert db.get_setting("password_hash", "")
        # setup_completed should NOT be set (legacy endpoint doesn't set it)
        assert db.get_setting("setup_completed", "") == ""
    finally:
        cleanup(test_dir)


def test_setup_wizard_html_exists():
    """The setup-wizard.html file exists and contains the 5 steps.

    v8.14.2: step 4 was renamed from "Optional AI + Finish" to "Optional AI"
    (the Finish button moved to step 5 — Optional Integrations)."""
    html = (PROJECT_ROOT / "app" / "static" / "setup-wizard.html").read_text()
    assert "Welcome to BillBook" in html
    assert "Set Your Password" in html
    assert "Business Type" in html
    assert "Confirm Categories" in html
    assert "Optional AI" in html          # step 4 (was "Optional AI + Finish")
    assert "Optional Integrations" in html  # v8.14.2: step 5 added
    assert "5 quick steps" in html          # subtitle bumped 4 → 5
    assert "Google Drive auto-backup" in html    # v8.14.2: GDrive opt-in card
    assert "FBR auto-post" in html               # v8.14.2: FBR opt-in card
    assert "Daily WhatsApp digest" in html       # v8.14.2: digest opt-in card
    assert "/api/setup/wizard" in html


def test_login_html_redirects_to_wizard():
    """login.html fetches /api/setup/state and redirects to /setup-wizard if not initialized."""
    html = (PROJECT_ROOT / "app" / "static" / "login.html").read_text()
    assert "/api/setup/state" in html
    assert "/setup-wizard" in html


def test_public_paths_includes_wizard():
    """The auth middleware includes /setup-wizard and /api/setup/wizard in public_paths."""
    main_py = (PROJECT_ROOT / "app" / "main.py").read_text()
    assert "/setup-wizard" in main_py
    assert "/api/setup/wizard" in main_py
    assert "/api/setup/state" in main_py


if __name__ == "__main__":
    test_setup_state_fresh_db(); print("OK fresh DB state")
    test_setup_state_completed_db(); print("OK completed DB state")
    test_wizard_completes_on_fresh_db(); print("OK wizard completes on fresh DB")
    test_wizard_rejects_short_password(); print("OK wizard rejects short password")
    test_wizard_rejects_already_completed(); print("OK wizard rejects completed DB")
    test_wizard_rejects_already_initialized(); print("OK wizard rejects v8.0 DB")
    test_wizard_custom_categories(); print("OK wizard custom categories")
    test_wizard_retail_template(); print("OK wizard retail template")
    test_wizard_stores_gemini_key(); print("OK wizard stores gemini key")
    test_wizard_validates_start_page(); print("OK wizard validates start_page")
    test_wizard_logs_activity(); print("OK wizard logs activity")
    test_wizard_creates_session(); print("OK wizard creates session")
    test_existing_setup_endpoint_still_works(); print("OK existing /api/setup still works")
    test_setup_wizard_html_exists(); print("OK setup-wizard.html exists with 4 steps")
    test_login_html_redirects_to_wizard(); print("OK login.html redirects to wizard")
    test_public_paths_includes_wizard(); print("OK public_paths includes wizard")
    print("\nALL v8.1 PHASE 1 TESTS PASSED")
