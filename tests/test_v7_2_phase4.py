"""v7.2 Phase 4 — AI Usage Dashboard: 14-day history, failures, clear-cache, TTL legend.

Covers:
- GET /api/ai/usage/14d returns 14 entries (one per day, zero-filled)
- GET /api/ai/failures returns recent failed AI calls
- POST /api/ai/clear-cache wipes the ai_cache table, returns count
- GET /api/ai/ttl-legend returns 4 TTL entries (bi, narrative, trends, extraction)
- Each TTL entry has key, label, human-readable string
- 14d history is zero-filled for missing days
- Clear-cache logs an activity entry (auditable)
"""
import os, sys, tempfile, shutil, json
from pathlib import Path
from datetime import datetime, timedelta
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_v72_p4_")
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
        for key in ['auto_confirm_bills', 'auto_draft_po', 'urdhaar_reminders',
                    'recurring_detection', 'expense_categorization',
                    'anomaly_diagnosis', 'variance_investigation',
                    'scheduled_reports', 'dead_stock_liquidation', 'ai_kill_switch']:
            c.execute("INSERT OR REPLACE INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)", (key, 0, 2, '{}'))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir



def _seed_ai_usage_today(n_calls: int = 5, n_cached: int = 2, n_failures: int = 1):
    """Insert n_calls+n_cached+n_failures rows into ai_usage for today."""
    from app import db
    with db.conn() as c:
        for i in range(n_calls):
            c.execute(
                "INSERT INTO ai_usage(task, provider, model, tokens_in, tokens_out, cached, duration_ms) "
                "VALUES(?,?,?,?,?,?,?)",
                ("bi_chat", "groq", "llama3-70b", 100 + i, 200 + i, 0, 500 + i))
        for i in range(n_cached):
            c.execute(
                "INSERT INTO ai_usage(task, provider, model, tokens_in, tokens_out, cached, duration_ms) "
                "VALUES(?,?,?,?,?,?,?)",
                ("bi_chat", "groq", "llama3-70b", 0, 0, 1, 0))
        for i in range(n_failures):
            # Failed call: cached=0, tokens=0, duration_ms=0 (no output produced)
            c.execute(
                "INSERT INTO ai_usage(task, provider, model, tokens_in, tokens_out, cached, duration_ms) "
                "VALUES(?,?,?,?,?,?,?)",
                ("bi_chat", "groq", "", 0, 0, 0, 0))


def _seed_ai_cache(n: int = 3):
    from app import db
    with db.conn() as c:
        for i in range(n):
            c.execute(
                "INSERT OR REPLACE INTO ai_cache(key, task, response_json, provider, tokens_in, tokens_out) "
                "VALUES(?,?,?,?,?,?)",
                (f"key-{i}", "bi_chat", "cached response", "groq", 100, 200))


def test_usage_14d_returns_14_entries():
    """GET /api/ai/usage/14d returns 14 day entries (one per day)."""
    test_dir = setup_test_db()
    try:
        _seed_ai_usage_today(n_calls=5, n_cached=2, n_failures=1)
        from app.routers.extensions import ai_usage_14d_route
        r = ai_usage_14d_route()
        assert "days" in r
        days = r["days"]
        assert len(days) == 14, f"expected 14 days, got {len(days)}"
        # Each entry must have the required fields
        for d in days:
            assert "d" in d
            assert "calls" in d
            assert "api_calls" in d
            assert "cache_hits" in d
            assert "tokens" in d
        # Today's entry should have 5 api_calls + 2 cache_hits + 1 failure = 8 total
        today = datetime.now().strftime("%Y-%m-%d")
        today_entry = [d for d in days if d["d"] == today][0]
        assert today_entry["calls"] == 8, f"expected 8 calls, got {today_entry['calls']}"
        assert today_entry["api_calls"] == 6, f"expected 6 api_calls (5 real + 1 failed), got {today_entry['api_calls']}"
        assert today_entry["cache_hits"] == 2, f"expected 2 cache_hits, got {today_entry['cache_hits']}"
    finally:
        cleanup(test_dir)


def test_usage_14d_zero_fills_missing_days():
    """Days with no AI activity appear as zeros."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import ai_usage_14d_route
        r = ai_usage_14d_route()
        days = r["days"]
        # All days except possibly today should be zero (we didn't seed anything)
        zero_days = [d for d in days if d["calls"] == 0]
        assert len(zero_days) >= 13, f"expected >= 13 zero days, got {len(zero_days)}"
    finally:
        cleanup(test_dir)


def test_recent_failures():
    """GET /api/ai/failures returns failed AI calls (no output)."""
    test_dir = setup_test_db()
    try:
        _seed_ai_usage_today(n_calls=3, n_failures=2)
        from app.routers.extensions import ai_failures_route
        r = ai_failures_route(limit=20)
        assert "failures" in r
        fails = r["failures"]
        assert len(fails) == 2, f"expected 2 failures, got {len(fails)}"
        # Each failure has the expected fields
        for f in fails:
            assert "id" in f
            assert "task" in f
            assert "provider" in f
            assert "created_at" in f
            assert f["cached"] == 0
            assert (f["tokens_in"] or 0) == 0
            assert (f["tokens_out"] or 0) == 0
    finally:
        cleanup(test_dir)


def test_clear_cache_wipes_table():
    """POST /api/ai/clear-cache deletes all rows from ai_cache, returns count."""
    test_dir = setup_test_db()
    try:
        _seed_ai_cache(n=5)
        from app.routers.extensions import ai_clear_cache_route
        r = ai_clear_cache_route()
        assert r["ok"] is True
        assert r["deleted"] == 5, f"expected 5 deleted, got {r['deleted']}"
        # Verify table is now empty
        from app import db
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM ai_cache").fetchone()["n"]
        assert n == 0
    finally:
        cleanup(test_dir)


def test_clear_cache_logs_activity():
    """Clear-cache writes an activity_log entry for auditability."""
    test_dir = setup_test_db()
    try:
        _seed_ai_cache(n=2)
        from app.routers.extensions import ai_clear_cache_route
        ai_clear_cache_route()
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT * FROM activity_log WHERE event_type='ai_cache_cleared' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None, "no ai_cache_cleared activity log entry found"
        assert "Cleared 2" in row["description"]
    finally:
        cleanup(test_dir)


def test_ttl_legend():
    """GET /api/ai/ttl-legend returns 4 TTL entries with required fields."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import ai_ttl_legend_route
        r = ai_ttl_legend_route()
        assert "ttl" in r
        ttl = r["ttl"]
        assert len(ttl) == 4, f"expected 4 TTL entries, got {len(ttl)}"
        keys = [t["key"] for t in ttl]
        assert "bi" in keys
        assert "narrative" in keys
        assert "trends" in keys
        assert "extraction" in keys
        for t in ttl:
            assert "label" in t
            assert "human" in t
            assert isinstance(t["human"], str)
    finally:
        cleanup(test_dir)


def test_clear_cache_when_empty():
    """Clear-cache on an empty cache returns 0 deleted."""
    test_dir = setup_test_db()
    try:
        from app.routers.extensions import ai_clear_cache_route
        r = ai_clear_cache_route()
        assert r["ok"] is True
        assert r["deleted"] == 0
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_usage_14d_returns_14_entries(); print("OK 14d returns 14 entries")
    test_usage_14d_zero_fills_missing_days(); print("OK 14d zero-fills missing days")
    test_recent_failures(); print("OK recent failures endpoint")
    test_clear_cache_wipes_table(); print("OK clear cache wipes table")
    test_clear_cache_logs_activity(); print("OK clear cache logs activity")
    test_ttl_legend(); print("OK TTL legend endpoint")
    test_clear_cache_when_empty(); print("OK clear cache when empty returns 0")
    print("\nALL v7.2 PHASE 4 TESTS PASSED")
