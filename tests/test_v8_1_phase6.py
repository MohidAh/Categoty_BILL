"""v8.1 Phase 6 — Daily-Use Friction Fixes tests."""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))




def test_start_page_setting_stored():
    """The wizard stores start_page and /api/setup/state returns it."""
    test_dir = setup_test_db()
    try:
        from app import db, security
        db.set_setting("password_hash", security.hash_password("test12345"))
        db.set_setting("setup_completed", "true")
        db.set_setting("start_page", "dashboard")
        from app.routers.auth import setup_wizard_state
        state = setup_wizard_state()
        assert state["start_page"] == "dashboard"
        assert state["setup_completed"] is True
    finally:
        cleanup(test_dir)


def test_app_js_has_start_page_logic():
    """app.js has the start_page redirect logic."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "start_page" in js
    assert "/api/setup/state" in js
    assert "reports/store-profit" in js  # dashboard redirect target


def test_app_js_has_drag_drop():
    """app.js has the global drag-drop bill upload handler."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "dragenter" in js
    assert "dragover" in js
    assert "drop" in js
    assert "Drop to upload bill" in js
    assert "_bb_dropped_file" in js
    assert "/bills/new" in js


def test_app_js_has_profit_ticker():
    """app.js has the profit ticker chip."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "bb-profit-ticker" in js
    assert "Today:" in js
    assert "/reports/store-profit" in js
    assert "/api/profit/dashboard" in js


def test_app_js_has_expense_fab():
    """app.js has the quick expense FAB."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "bb-expense-fab" in js
    assert "Quick Expense" in js
    assert "qe-amount" in js
    assert "qe-category" in js
    assert "/api/expenses" in js


def test_app_js_uses_snowui_tokens():
    """app.js Phase 6 additions use SnowUI CSS tokens, not hardcoded colors where possible."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    # The profit ticker uses var(--success-soft) and var(--success-text)
    assert "var(--success-soft" in js or "var(--success" in js
    # The expense FAB uses var(--danger)
    assert "var(--danger" in js
    # No emoji
    for i, ch in enumerate(js):
        if ord(ch) > 0x1F000:
            assert False, f"emoji at position {i}: {ch!r}"


if __name__ == "__main__":
    test_start_page_setting_stored(); print("OK start_page stored")
    test_app_js_has_start_page_logic(); print("OK start_page redirect logic")
    test_app_js_has_drag_drop(); print("OK drag-drop handler")
    test_app_js_has_profit_ticker(); print("OK profit ticker")
    test_app_js_has_expense_fab(); print("OK expense FAB")
    test_app_js_uses_snowui_tokens(); print("OK SnowUI tokens, no emoji")
    print("\nALL v8.1 PHASE 6 TESTS PASSED")
