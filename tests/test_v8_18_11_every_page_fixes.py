"""v8.18.11 — every-page runtime sweep fixes.

Found by the every-page browser sweep (scripts/every_page_sweep.py — boots
the real server, visits all 87 registered SPA routes, and records, per page:
4xx/5xx API calls, JS page errors, and DEAD FIELD READS via a fetch/JSON
proxy probe). All the bugs below are the same class as v8.18.9's
monthly-close: the UI read fields/keys the backend never returned, with a
silent fallback, so the feature quietly showed zeros/blank columns.

1. Profit ticker (app.js, on EVERY page): read r.daily_summary from
   /api/profit/dashboard — key never existed (real key: `daily`) — so the
   "Today: Rs" chip was permanently Rs 0.
2. /dead-stock page: read 8 phantom fields (stock_value, stock, color, code,
   category_name, last_sold, days_idle, suggestion) — every stat 0, every
   column blank/'—', while the API returns item_name/last_purchased/
   days_since/total_qty/tied_capital/avg_cost/supplier/suggested_discount/
   action.
3. /insights/trends seasonal table: read s.name/s.month/s.impact/
   s.recommendation — never returned (real: festival/type/priority/message/
   items_to_stock) — every cell rendered '—'.
4. /settings/security sessions table: read s.token_prefix (never returned)
   — token column showed "undefined..." and every Revoke button POSTed to
   /api/sessions/undefined.
5. /reports/pnl page: read r.revenue / r.cogs / r.expenses_total (never
   returned; real keys net_revenue / cost_of_goods / expenses) and treated
   `expenses` (a NUMBER) as an array — the whole P&L statement rendered
   Rs 0.
6. Prefix detail routes (/bills/, /suppliers/, /customers/, /pos/sale/,
   /purchase-orders/) fired API calls with EMPTY ids when hand-typed bare
   (e.g. /api/suppliers//statement -> 404, or a garbage "Bill #undefined"
   render). Now guarded.
"""
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

JS = PROJECT_ROOT / "app" / "static" / "js"


def setup_test_db():
    return setup_test_db_with_password(prefix="billbook_v81811_")


# ---------------------------------------------------------------- ticker
def test_profit_dashboard_has_daily_key():
    """/api/profit/dashboard must return `daily` (the ticker reads r.daily)."""
    d = setup_test_db()
    try:
        from app.profit_analytics import get_store_profit_dashboard
        r = get_store_profit_dashboard()
        assert "daily" in r, "dashboard must expose the `daily` block"
        for k in ("sales", "cogs", "gross_profit", "margin"):
            assert k in r["daily"], f"daily.{k} missing"
        assert "daily_summary" not in r, "phantom key never existed"
    finally:
        cleanup(d)


def _read(path):
    return (JS / path).read_text(encoding="utf-8")


def _strip_line_comments(src):
    """Full-line // comments only — trailing comments and URLs stay intact.
    The fix comments in the sources explain which phantom fields were
    removed, so phantom assertions must run on comment-free code."""
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_app_js_ticker_reads_daily():
    src = _strip_line_comments(_read("app.js"))
    assert "r.daily ||" in src, "ticker must read the real `daily` block"
    assert "daily_summary" not in src, "phantom daily_summary read must be gone"


# ------------------------------------------------------------ dead stock
def test_dead_stock_alert_fields_match_page_reads():
    """Rows must carry every field the rewritten page reads."""
    d = setup_test_db()
    try:
        from app import db
        # one confirmed bill 100 days ago -> guaranteed dead-stock row
        old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        with db.conn() as c:
            c.execute(
                "INSERT INTO bills(id, supplier_id, supplier_name, bill_date, "
                "bill_no, written_total, computed_total, status, payment_status, "
                "created_at) VALUES(901, 1, 'T Supplies', ?, 'T-DS', 500, 500, "
                "'confirmed', 'paid', ?)", (old, f"{old} 10:00:00"))
            c.execute(
                "INSERT INTO bill_items(bill_id, category_id, raw, item_code, "
                "price, qty, unit, line_total, page_no) "
                "VALUES(901, 1, 'T Dead Widget', 'A', 95, 10, 'pcs', 950, 1)")
        from app.trends import generate_dead_stock_alerts
        alerts = generate_dead_stock_alerts()
        row = next((a for a in alerts if a["item_name"] == "T Dead Widget"), None)
        assert row is not None, "seeded old purchase must appear in dead stock"
        for k in ("item_name", "last_purchased", "days_since", "total_qty",
                  "tied_capital", "avg_cost", "supplier",
                  "suggested_discount", "action"):
            assert k in row, f"page reads {k} — row must carry it"
        assert row["days_since"] >= 95
        assert row["tied_capital"] > 0
    finally:
        cleanup(d)


def test_dead_stock_page_reads_real_fields():
    src = _strip_line_comments(_read("apps/pos/components/dead-stock.js"))
    for good in ("a.item_name", "a.total_qty", "a.tied_capital", "a.days_since",
                 "a.last_purchased", "a.avg_cost", "a.suggested_discount",
                 "a.supplier"):
        assert good in src, f"missing real field usage: {good}"
    for phantom in ("a.stock_value", "a.days_idle", "a.category_name",
                    "a.last_sold", "a.suggestion", "a.color", "a.code",
                    "a.stock ||"):
        assert phantom not in src, f"phantom field still read: {phantom}"


# --------------------------------------------------------------- seasonal
def test_seasonal_alert_fields_match_page_reads():
    d = setup_test_db()
    try:
        import app.trends as trends_mod

        class _FakeDT:
            @staticmethod
            def now():
                return datetime(2026, 10, 15)  # October -> Wedding Season

        real = trends_mod.datetime
        trends_mod.datetime = _FakeDT
        try:
            alerts = trends_mod.get_seasonal_alerts()
        finally:
            trends_mod.datetime = real
        assert alerts, "October must produce festival alerts"
        for k in ("type", "festival", "items_to_stock", "category",
                  "message", "priority"):
            assert k in alerts[0], f"page reads {k} — row must carry it"
        assert any(a["type"] == "current" for a in alerts)
    finally:
        cleanup(d)


def test_seasonal_table_reads_real_fields():
    src = _strip_line_comments(_read("pages/insights-pages.js"))
    # split at the LAST render assignment (the table; the first is the
    # empty-state branch)
    seasonal_block = src.split("$('#tr-seasonal').innerHTML")[-1]
    for good in ("s.festival", "s.priority", "s.message", "s.type",
                 "s.items_to_stock"):
        assert good in seasonal_block, f"missing real field usage: {good}"
    for phantom in ("s.name", "s.pattern", "s.month", "s.impact",
                    "s.recommendation"):
        assert phantom not in seasonal_block, f"phantom field still read: {phantom}"


# -------------------------------------------------------------- sessions
def test_sessions_list_carries_token_prefix_and_revoke_works():
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        # a second, known session besides the login one
        token = "deadbeefcafe0123456789abcdef"
        with db.conn() as c:
            c.execute(
                "INSERT INTO sessions(token, created_at, expires_at, role) "
                "VALUES(?, datetime('now','localtime'), "
                "datetime('now','localtime','+30 days'), 'manager')", (token,))
        with TestClient(app) as tc:
            login_client(tc)
            body = tc.get("/api/sessions").json()["sessions"]
            row = next((s for s in body if s.get("token_prefix") == token[:8]), None)
            assert row is not None, "rows must carry token_prefix"
            assert row["token"] == token[:8] + "..."
            # revoke by prefix -> that session gone, own session survives
            r = tc.delete(f"/api/sessions/{token[:8]}")
            assert r.status_code == 200
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM sessions WHERE token=?",
                              (token,)).fetchone()["n"]
            assert n == 0, "revoke by prefix must delete the session"
            body2 = tc.get("/api/sessions").json()["sessions"]
            assert all(s["token_prefix"] != token[:8] for s in body2)
    finally:
        cleanup(d)


def test_security_page_reads_token_prefix():
    src = (JS / "pages/settings-staff.js").read_text(encoding="utf-8")
    assert "s.token_prefix" in src, "page reads token_prefix (now provided)"


# ------------------------------------------------------------------- PnL
def test_pnl_contract_matches_page_reads():
    d = setup_test_db()
    try:
        from app import db
        # deterministic expenses: 2 operating + 1 owner draw in Aug 2026
        with db.conn() as c:
            for name, amt, etype in (("Rent", 20000, "operating"),
                                     ("Salaries", 30000, "operating"),
                                     ("Family", 5000, "owner_draw")):
                c.execute(
                    "INSERT INTO expenses(category, description, amount, date, "
                    "expense_type) VALUES(?,?,?, '2026-08-15', ?)",
                    (name, "test", amt, etype))
        from app.shop import get_pnl
        r = get_pnl("2026-08")
        for k in ("net_revenue", "cost_of_goods", "expenses", "owner_draws",
                  "gross_profit", "net_profit", "discounts", "purchases"):
            assert k in r, f"page reads {k} — PnL must return it"
        assert isinstance(r["expenses"], (int, float)), "`expenses` is a NUMBER"
        assert r["expenses"] == 50000
        assert r["owner_draws"] == 5000
        rows = r["expenses_by_category"]
        assert {x["category"] for x in rows} == {"Rent", "Salaries"}
        assert sum(x["total"] for x in rows) == 50000
        # sample data has August sales -> real numbers, not zeros
        assert r["net_revenue"] > 0
        assert r["cost_of_goods"] > 0
    finally:
        cleanup(d)


def test_pnl_page_reads_real_fields():
    src = _strip_line_comments(_read("pages/reports-financial.js"))
    pnl_block = src.split("route('/reports/pnl'")[1].split("route('/reports/cash-flow'")[0]
    for good in ("r.net_revenue", "r.cost_of_goods", "r.expenses ||",
                 "r.expenses_by_category", "r.owner_draws", "r.gross_profit",
                 "r.net_profit"):
        assert good in pnl_block, f"missing real field usage: {good}"
    for phantom in ("r.revenue", "r.cogs", "r.expenses_total"):
        assert phantom not in pnl_block, f"phantom field still read: {phantom}"


# ------------------------------------------------------- prefix id guards
def test_prefix_routes_guard_empty_ids():
    """Hand-typed bare detail URLs must render a not-found state, not fire
    broken /api/...//  calls or render '#undefined' garbage."""
    guards = {
        "pages/bill-edit.js": "Bill not found",
        "pages/supplier-detail.js": "Supplier not found",
        "apps/pos/components/customer-detail.js": "Customer not found",
        "pages/inventory-pages.js": "No purchase order id",
        "apps/pos/components/sale-detail.js": "Not found",
    }
    for rel, marker in guards.items():
        src = _strip_line_comments(_read(rel))
        assert "/^\\d+$/.test(id)" in src, f"{rel} must guard numeric ids"
        assert marker in src, f"{rel} guard must render a not-found state"
