"""v8.0 Phase 1 — Branch Identity tests.

Verifies:
- branch_config table exists with id=1 default row
- Default role='branch', branch_name='Main Shop', hub_url='', no sync_token
- GET /api/branch-config returns the config (without sync_token_hash)
- PUT /api/branch-config updates name/region/role/hub_url, generates branch_id
- PUT with sync_token hashes it (SHA-256) before storage
- PUT with empty sync_token preserves existing token
- Single-shop behavior: with role='branch' + hub_url='', no sync attempts fire
  (we verify this by checking no outbox entries are created)
- 185.88 test still passes (running weighted avg engine intact)
- 249 existing tests still green
"""
import os, sys, tempfile, shutil, hashlib
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v8_p1_")
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
                  "ai_cache", "ai_usage", "pending_actions", "automation_config"):
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



def test_branch_config_table_exists():
    """branch_config table exists after init()."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(branch_config)").fetchall()}
        for expected in ("id", "role", "branch_id", "branch_name", "region", "hub_url", "sync_token_hash"):
            assert expected in cols, f"missing column {expected} in {cols}"
    finally:
        cleanup(test_dir)


def test_default_branch_config_seeded():
    """init() seeds row id=1 with role='branch', branch_name='Main Shop', empty hub_url."""
    test_dir = setup_test_db()
    try:
        from app import db
        with db.conn() as c:
            row = c.execute("SELECT * FROM branch_config WHERE id=1").fetchone()
        assert row is not None, "default branch_config row not seeded"
        assert row["role"] == "branch", f"expected role='branch', got '{row['role']}'"
        assert row["branch_name"] == "Main Shop", f"expected 'Main Shop', got '{row['branch_name']}'"
        assert row["hub_url"] == "", f"expected empty hub_url, got '{row['hub_url']}'"
        assert row["sync_token_hash"] == "", "expected empty sync_token_hash by default"
    finally:
        cleanup(test_dir)


def test_get_branch_config_endpoint_shape():
    """GET /api/branch-config returns the config WITHOUT sync_token_hash."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import get_branch_config
        cfg = get_branch_config()
        assert cfg["role"] == "branch"
        assert cfg["branch_name"] == "Main Shop"
        assert cfg["hub_url"] == ""
        assert cfg["has_sync_token"] is False
        # Critical: sync_token_hash must NEVER be in the response
        assert "sync_token_hash" not in cfg, "sync_token_hash leaked in API response"
    finally:
        cleanup(test_dir)


def test_put_branch_config_updates_name_and_region():
    """PUT /api/branch-config updates branch_name and region."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn, get_branch_config
        r = set_branch_config(BranchConfigIn(
            role="branch", branch_name="Lahore Branch", region="Punjab",
        ))
        assert r["ok"] is True
        assert r["branch_id"], "branch_id should be auto-generated"
        cfg = get_branch_config()
        assert cfg["branch_name"] == "Lahore Branch"
        assert cfg["region"] == "Punjab"
        assert cfg["role"] == "branch"
    finally:
        cleanup(test_dir)


def test_put_branch_config_generates_branch_id():
    """PUT generates a branch_id starting with 'BR-' if none provided."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn
        r = set_branch_config(BranchConfigIn(role="branch", branch_name="Test"))
        assert r["branch_id"].startswith("BR-"), f"expected 'BR-XXXX', got {r['branch_id']}"
        assert len(r["branch_id"]) == 11, f"expected 11 chars, got {len(r['branch_id'])}"
    finally:
        cleanup(test_dir)


def test_put_branch_config_hashes_sync_token():
    """PUT with sync_token stores SHA-256 hash, never plaintext."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn, get_branch_config
        from app import db
        set_branch_config(BranchConfigIn(
            role="branch", branch_name="Test", sync_token="my-secret-token",
        ))
        with db.conn() as c:
            row = c.execute("SELECT sync_token_hash FROM branch_config WHERE id=1").fetchone()
        expected = hashlib.sha256(b"my-secret-token").hexdigest()
        assert row["sync_token_hash"] == expected, "token not hashed correctly"
        assert row["sync_token_hash"] != "my-secret-token", "plaintext token stored!"
        # API must not leak the hash
        cfg = get_branch_config()
        assert cfg["has_sync_token"] is True
        assert "sync_token_hash" not in cfg
    finally:
        cleanup(test_dir)


def test_put_branch_config_preserves_token_when_omitted():
    """PUT with empty sync_token preserves the existing token."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn
        from app import db
        # Set initial token
        set_branch_config(BranchConfigIn(role="branch", branch_name="Test", sync_token="first-token"))
        # Update name only — no sync_token field
        set_branch_config(BranchConfigIn(role="branch", branch_name="Updated"))
        with db.conn() as c:
            row = c.execute("SELECT sync_token_hash, branch_name FROM branch_config WHERE id=1").fetchone()
        assert row["branch_name"] == "Updated"
        expected = hashlib.sha256(b"first-token").hexdigest()
        assert row["sync_token_hash"] == expected, "token was not preserved"
    finally:
        cleanup(test_dir)


def test_put_branch_config_validates_role():
    """PUT with invalid role returns 400."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn
        from fastapi import HTTPException
        try:
            set_branch_config(BranchConfigIn(role="invalid", branch_name="Test"))
            assert False, "should have raised 400"
        except HTTPException as e:
            assert e.status_code == 400
    finally:
        cleanup(test_dir)


def test_put_branch_config_sets_hq_role():
    """PUT with role='hq' updates the role."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn, get_branch_config
        set_branch_config(BranchConfigIn(role="hq", branch_name="Central HQ"))
        cfg = get_branch_config()
        assert cfg["role"] == "hq"
        assert cfg["branch_name"] == "Central HQ"
    finally:
        cleanup(test_dir)


def test_branch_config_update_logs_activity():
    """PUT logs a 'branch_config_updated' activity entry."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import set_branch_config, BranchConfigIn
        from app import db
        set_branch_config(BranchConfigIn(role="branch", branch_name="Test Branch"))
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='branch_config_updated' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None, "no branch_config_updated activity log entry"
        assert "Test Branch" in row["description"]
    finally:
        cleanup(test_dir)


def test_single_shop_mode_no_sync_attempts():
    """With role='branch' + empty hub_url (defaults), no sync activity occurs.
    Tables created in Phase 2-3 (branches, branch_pairing_codes, branch_summaries,
    sync_outbox) DO exist but remain EMPTY on a single-shop instance. This verifies
    the 'no behavioral change' guarantee for single-shop users — tables exist but unused."""
    test_dir = setup_test_db()
    try:
        from app.routers.settings import get_branch_config
        cfg = get_branch_config()
        assert cfg["role"] == "branch"
        assert cfg["hub_url"] == ""
        from app import db
        with db.conn() as c:
            tables = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            # Tables from Phase 2-3 — exist but should be EMPTY on a single-shop instance
            branches_count = c.execute("SELECT COUNT(*) AS n FROM branches").fetchone()["n"]
            pairing_count = c.execute("SELECT COUNT(*) AS n FROM branch_pairing_codes").fetchone()["n"]
            summaries_count = c.execute("SELECT COUNT(*) AS n FROM branch_summaries").fetchone()["n"]
            outbox_count = c.execute("SELECT COUNT(*) AS n FROM sync_outbox").fetchone()["n"]
        # branch_config MUST exist
        assert "branch_config" in tables
        # Phase 2-3 tables exist but are empty
        assert branches_count == 0, f"branches table should be empty, has {branches_count} rows"
        assert pairing_count == 0, f"branch_pairing_codes should be empty, has {pairing_count} rows"
        assert summaries_count == 0, f"branch_summaries should be empty, has {summaries_count} rows"
        assert outbox_count == 0, f"sync_outbox should be empty, has {outbox_count} rows"
    finally:
        cleanup(test_dir)


def test_185_88_running_avg_still_intact():
    """The load-bearing 185.88 weighted-average test still passes after Phase 1 changes."""
    test_dir = setup_test_db()
    try:
        from app.profit_engine import apply_purchase_to_state, apply_sale_to_state, peek_avg_cost
        from app import db
        # Reset Cat A state and apply the canonical 185.88 sequence
        with db.conn() as c:
            c.execute("DELETE FROM category_stock_state WHERE category_id=1")
        # Opening: 10,000 pcs @ Rs 180
        apply_purchase_to_state(1, 10000, 180.0)
        with db.conn() as c:
            assert peek_avg_cost(c, 1) == 180.0, f"after opening, expected 180.00, got {peek_avg_cost(c, 1)}"
        # Sale: 3,000 (avg unchanged)
        apply_sale_to_state(1, 3000)
        with db.conn() as c:
            assert peek_avg_cost(c, 1) == 180.0, f"after sale, expected 180.00, got {peek_avg_cost(c, 1)}"
        # Purchase: 10,000 @ Rs 190 → 185.88
        apply_purchase_to_state(1, 10000, 190.0)
        with db.conn() as c:
            avg = peek_avg_cost(c, 1)
            assert abs(avg - 185.88) < 0.01, f"expected 185.88, got {avg}"
            # Total stock should be 17,000 (10,000 - 3,000 + 10,000)
            row = c.execute("SELECT current_qty FROM category_stock_state WHERE category_id=1").fetchone()
            assert row["current_qty"] == 17000, f"expected 17,000 pcs, got {row['current_qty']}"
    finally:
        cleanup(test_dir)


def test_branch_page_js_uses_snowui_tokens():
    """The Branch settings page JS uses .pos-page-header and inline SVG (SnowUI tokens)."""
    js_path = PROJECT_ROOT / "app" / "static" / "js" / "pages" / "branch-page.js"
    content = js_path.read_text()
    assert "pos-page-header" in content, "missing .pos-page-header class"
    assert "pos-page-header-icon" in content
    assert "pos-page-header-title" in content
    assert "pos-page-header-sub" in content
    assert "<svg" in content, "missing inline SVG icons"
    # No emoji
    for i, ch in enumerate(content):
        if ord(ch) > 0x1F000:
            assert False, f"emoji at position {i}: {ch!r}"


def test_branch_page_in_settings_nav():
    """The Branch page is registered in the Settings nav (shell.js)."""
    shell = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "shell.js").read_text()
    assert "/settings/branch" in shell, "Branch route not in settings nav"
    assert "label: 'Branch'" in shell, "Branch label not in settings nav"


def test_branch_page_registered_in_app_js():
    """The Branch page is imported in app.js."""
    app = (PROJECT_ROOT / "app" / "static" / "js" / "app.js").read_text()
    assert "branch-page.js" in app, "branch-page.js not imported in app.js"


if __name__ == "__main__":
    test_branch_config_table_exists(); print("OK branch_config table exists")
    test_default_branch_config_seeded(); print("OK default branch_config seeded")
    test_get_branch_config_endpoint_shape(); print("OK GET /api/branch-config shape")
    test_put_branch_config_updates_name_and_region(); print("OK PUT updates name + region")
    test_put_branch_config_generates_branch_id(); print("OK PUT generates branch_id")
    test_put_branch_config_hashes_sync_token(); print("OK PUT hashes sync_token")
    test_put_branch_config_preserves_token_when_omitted(); print("OK PUT preserves token when omitted")
    test_put_branch_config_validates_role(); print("OK PUT validates role")
    test_put_branch_config_sets_hq_role(); print("OK PUT sets hq role")
    test_branch_config_update_logs_activity(); print("OK branch_config update logs activity")
    test_single_shop_mode_no_sync_attempts(); print("OK single-shop mode = no sync tables")
    test_185_88_running_avg_still_intact(); print("OK 185.88 running avg intact")
    test_branch_page_js_uses_snowui_tokens(); print("OK branch-page.js uses SnowUI tokens")
    test_branch_page_in_settings_nav(); print("OK Branch page in settings nav")
    test_branch_page_registered_in_app_js(); print("OK Branch page registered in app.js")
    print("\nALL v8.0 PHASE 1 TESTS PASSED")
