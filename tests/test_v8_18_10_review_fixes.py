"""v8.18.10 whole-system review — contract fixes for three dead UI wiring bugs.

Found by the system-wide frontend<->backend field-contract audit
(scripts/verify_api_contract.py). All three are the SAME bug class as the
v8.18.9 monthly-close fix: the UI called endpoints/fields that never existed,
with a silent catch, so the feature quietly showed nothing.

1. POS tax loader (pos.js):  fetched /api/settings (route NEVER existed) and
   read tax_rate/tax_inclusive -> window._pos_tax_rate stayed 0 -> POS always
   charged 0% tax even when configured. Real endpoint: GET /api/tax/config
   -> {rate: FRACTION, inclusive: BOOL}. (Cart math uses the fraction.)
2. Shift close expected cash (cash-controls-pages.js): called
   /api/cash-drawer/status (404) -> Expected Cash always blank in non-blind
   close. Real endpoint: GET /api/cash-drawer -> {current_cash, ...}.
3. /reorder page (inventory-pages.js): rendered fields the API never
   returned (suggested_qty, avg_cost, category_name, current_stock, code,
   color, reason, last_sold) while the endpoint returns trends rows with
   different names and NO id -> stats all 0, rows empty shells, and the
   Mark Ordered / Dismiss buttons POSTed to /undefined/... endpoints.
   Fixed by: GET /api/reorder-reminders now upserts generated reminders
   into the reorder_reminders TABLE (which nothing ever wrote, despite the
   dismiss/ordered/auto-PO endpoints all operating on it), returns rows
   with real ids; page reads the real schema. auto-po/master-po endpoints
   also fixed (phantom columns + status='active' filter that matched
   nothing ever).
"""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

JS = PROJECT_ROOT / "app" / "static" / "js"


def setup_test_db():
    return setup_test_db_with_password(prefix="billbook_v81810_")


# ---------------------------------------------------------------- reorder API
def test_reorder_get_persists_rows_with_ids():
    """GET must return table rows WITH ids (the page buttons need them)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.get("/api/reorder-reminders")
            assert r.status_code == 200
            body = r.json()
            assert "reminders" in body
            for row in body["reminders"]:
                assert isinstance(row["id"], int), "rows must carry table ids"
                assert row["status"] == "new"
                # real schema fields, present and typed
                assert "item_name" in row
                assert "suggested_quantity" in row
                assert "avg_price" in row
            # dashboard contract (reads item_name/days_since/avg_gap_days/
            # suggested_quantity/supplier_name/priority) unchanged
            if body["reminders"]:
                row = body["reminders"][0]
                for k in ("item_name", "days_since", "avg_gap_days",
                          "suggested_quantity", "supplier_name", "priority"):
                    assert k in row
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_reorder_dismiss_and_ordered_lifecycle(monkeypatch):
    """dismiss/ordered must make a reminder disappear from the active list.

    The generator is monkeypatched to a fixed item so the lifecycle is
    deterministic (sample data has no reorder-pattern history).
    """
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        import app.trends as trends_mod

        GEN = [{
            "item_name": "Test Item", "supplier_name": "ACME",
            "avg_gap_days": 10, "last_purchased": "2026-08-01",
            "days_since": 30, "suggested_quantity": 5,
            "avg_price": 120.0, "total_purchases": 4,
            "priority": "high", "seasonal_note": "",
        }]
        monkeypatch.setattr(trends_mod, "generate_reorder_reminders", lambda: [dict(g) for g in GEN])
        with TestClient(app) as tc:
            login_client(tc)
            body = tc.get("/api/reorder-reminders").json()
            ids = [x["id"] for x in body["reminders"]]
            assert len(ids) == 1, "generated row persisted and returned"
            rid = ids[0]
            # second GET -> upsert, no duplicate
            body = tc.get("/api/reorder-reminders").json()
            assert [x["id"] for x in body["reminders"]] == [rid]
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM reorder_reminders "
                              "WHERE item_name='Test Item'").fetchone()["n"]
            assert n == 1, "upsert must not duplicate rows"
            # dismiss it -> gone from list, kept in table
            r = tc.post(f"/api/reorder-reminders/{rid}/dismiss")
            assert r.status_code == 200
            body = tc.get("/api/reorder-reminders").json()
            assert rid not in [x["id"] for x in body["reminders"]]
            with db.conn() as c:
                st = c.execute("SELECT status FROM reorder_reminders WHERE id=?",
                               (rid,)).fetchone()["status"]
            assert st == "dismissed"
            # generator keeps producing it, but it stays dismissed (upsert
            # must NOT reset status) and is not re-inserted
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM reorder_reminders "
                              "WHERE item_name='Test Item'").fetchone()["n"]
            assert n == 1, "dismissed row must not be re-inserted as new"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_reorder_stale_new_rows_are_cleaned():
    """A 'new' row the generator no longer produces must be dropped."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            with db.conn() as c:
                c.execute(
                    "INSERT INTO reorder_reminders(item_name, status) "
                    "VALUES('Ghost Item No Longer Generated','new')")
            tc.get("/api/reorder-reminders")
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) n FROM reorder_reminders "
                              "WHERE item_name='Ghost Item No Longer Generated'"
                              ).fetchone()["n"]
            assert n == 0, "stale 'new' rows must be cleaned up on refresh"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_auto_po_uses_real_columns():
    """auto-po must not reference phantom columns (would 500 before)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import db
        with TestClient(app) as tc:
            login_client(tc)
            with db.conn() as c:
                c.execute(
                    "INSERT INTO reorder_reminders(item_name, supplier_name, "
                    "suggested_quantity, avg_price, priority, status) "
                    "VALUES('Auto PO Item','ACME',7,90.5,'high','new')")
                rid = c.execute("SELECT id FROM reorder_reminders "
                                "WHERE item_name='Auto PO Item'").fetchone()["id"]
            r = tc.post("/api/reorder-reminders/auto-po",
                        json={"reminder_ids": [rid]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == 1
            assert abs(body["total"] - 7 * 90.5) < 0.01
            # reminder marked ordered
            with db.conn() as c:
                st = c.execute("SELECT status FROM reorder_reminders WHERE id=?",
                               (rid,)).fetchone()["status"]
            assert st == "ordered"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_db_migration_adds_new_reorder_columns():
    """Old databases get avg_price / total_purchases / seasonal_note."""
    d = tempfile.mkdtemp(prefix="billbook_v81810mig_")
    try:
        from app import config, db
        db_path = os.path.join(d, "billbook.db")
        db.DB_PATH = db_path
        config.DATA = d
        # create a legacy-shaped table (pre-v8.18.10 schema) first
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE reorder_reminders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL, "
            "supplier_name TEXT, avg_gap_days INTEGER, last_purchased TEXT, "
            "days_since INTEGER, suggested_quantity INTEGER, "
            "priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'new', "
            "created_at TEXT DEFAULT (datetime('now','localtime')))")
        con.execute("INSERT INTO reorder_reminders(item_name) VALUES('Legacy')")
        con.commit()
        con.close()
        db.init()  # migrations must add the new columns
        with db.conn() as c:
            cols = {r["name"] for r in c.execute(
                "PRAGMA table_info(reorder_reminders)").fetchall()}
        assert {"avg_price", "total_purchases", "seasonal_note"} <= cols
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- tax + cash
def test_tax_config_contract():
    """/api/tax/config returns {rate: fraction, inclusive: bool} — matches
    the fixed pos.js loader (which must NOT divide by 100)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app import pos_extra
        with TestClient(app) as tc:
            login_client(tc)
            pos_extra.set_tax_rate(0.17)
            pos_extra.set_tax_inclusive(True)
            r = tc.get("/api/tax/config")
            assert r.status_code == 200
            body = r.json()
            assert body["rate"] == 0.17          # fraction
            assert body["inclusive"] is True      # bool
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_cash_drawer_contract():
    """GET /api/cash-drawer returns current_cash (what shift close reads)."""
    d = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as tc:
            login_client(tc)
            r = tc.get("/api/cash-drawer")
            assert r.status_code == 200
            body = r.json()
            assert "current_cash" in body
            assert "status" in body
            # and the old (wrong) URL is really gone
            assert tc.get("/api/cash-drawer/status").status_code == 404
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------- static JS guards
def _read(path):
    return (JS / path).read_text(encoding="utf-8")


def test_pos_js_uses_real_tax_endpoint():
    src = _read("pages/pos.js")
    assert "/api/tax/config" in src, "loader must call the real endpoint"
    assert "api('/api/settings')" not in src, "phantom /api/settings call removed"
    assert "window._pos_tax_rate = parseFloat(r.rate || 0)" in src
    # the old percent-divide must be gone (rate is already a fraction)
    assert "parseFloat(r.tax_rate || 0) / 100" not in src


def test_cash_controls_js_uses_real_drawer_endpoint():
    src = _read("pages/cash-controls-pages.js")
    assert "api('/api/cash-drawer')" in src
    # guard against actual CALLS to the phantom URL (comments may mention it)
    import re
    calls = re.findall(r"api\(\s*['\"]([^'\"]*cash-drawer[^'\"]*)['\"]\s*\)", src)
    assert all(c == '/api/cash-drawer' for c in calls), f"phantom drawer calls: {calls}"


def test_reorder_page_reads_real_fields():
    src = _read("pages/inventory-pages.js")
    reorder_section = src.split("route('/reorder'")[1]
    for good in ("rem.suggested_quantity", "rem.avg_price", "rem.item_name",
                 "rem.avg_gap_days", "rem.days_since", "data-re-order=\"${rem.id}\""):
        assert good in reorder_section, f"missing real field usage: {good}"
    for phantom in ("r.suggested_qty", "r.avg_cost", "r.category_name",
                    "r.current_stock", "r.last_sold", "r.code", "r.color"):
        assert phantom not in reorder_section, f"phantom field still read: {phantom}"


def test_dashboard_reorder_usage_unchanged():
    """dashboard.js already read the correct fields — lock that in."""
    src = _read("pages/dashboard.js")
    for k in ("rem.item_name", "rem.days_since", "rem.avg_gap_days",
              "rem.suggested_quantity", "rem.supplier_name", "rem.priority"):
        assert k in src
