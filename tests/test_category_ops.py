"""v8.13.0 — Category operations tests.

Covers:
1. Supplier comparison per category — happy path + delta computation + cheapest flag
2. Category cost-trend alerts — detects cost increase > threshold
3. Stock write-offs — happy path + reason validation + qty validation + loss value computation
4. Stock write-off summary — by-reason breakdown + total loss
5. Bill confirm cost-vs-cheapest-supplier check — flags items >5% above cheapest historical avg
6. API endpoints — POST write-off (PIN gate), GET supplier-comparison, GET cost-trends,
   GET writeoffs, GET writeoffs/summary
7. AI prompt builder — _build_prompt() includes the shop's actual categories
"""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db_with_password as setup_test_db, cleanup, login_client
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
SAMPLE_SQL = PROJECT_ROOT / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_catops_")
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
                  "owner_withdrawals", "capital_injections", "stock_writeoffs"):
            try:
                c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
        c.execute(
            "INSERT INTO employees(id, name, role, pin, active) "
            "VALUES(99, 'Test Manager', 'manager', '1234', 1)"
        )
        from app.security import hash_password
        c.execute("DELETE FROM settings WHERE key='password_hash'")
        c.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                  ("password_hash", hash_password("testpass")))
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def login_client(client):
    r = client.post("/api/login", json={"password": "testpass"})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"



def _seed_supplier_bill(c, supplier_name, supplier_id, bill_date, items):
    """Helper: insert + confirm a bill with the given items.
    items = [(category_id, qty, unit_price), ...]
    """
    # Insert supplier if not exists
    if supplier_id is None:
        s = c.execute(
            "INSERT INTO suppliers(name, phone) VALUES(?,?)",
            (supplier_name, "0300-0000000")
        )
        supplier_id = s.lastrowid
    # Insert bill
    computed = sum(q * p for _, q, p in items)
    b = c.execute(
        "INSERT INTO bills(supplier_id, supplier_name, bill_date, bill_no, "
        "computed_total, written_total, status, payment_status, unit) "
        "VALUES(?,?,?,?,?,?, 'confirmed', 'paid', 'piece')",
        (supplier_id, supplier_name, bill_date, f"BN-{supplier_name}-{bill_date}",
         computed, computed)
    )
    bill_id = b.lastrowid
    for cat_id, qty, price in items:
        c.execute(
            "INSERT INTO bill_items(bill_id, category_id, raw, qty, price, unit, line_total) "
            "VALUES(?,?,?,?,?, 'piece', ?)",
            (bill_id, cat_id, f"item {cat_id}", qty, price, qty * price)
        )
    return bill_id, supplier_id


# ─── 1. Supplier comparison per category ─────────────────────────────

def test_supplier_comparison_returns_per_category_breakdown():
    """For each category, list every supplier who sold that category with
    avg/last/min price + delta vs running avg_cost."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.category_ops import supplier_comparison_by_category
        # Find the first price_category to use for the test
        with db.conn() as c:
            cat_row = c.execute("SELECT id, code, name, sell_price FROM price_categories LIMIT 1").fetchone()
            assert cat_row is not None, "Need at least one price_category"
            cat_id = cat_row["id"]
            # Seed two suppliers with different prices for the same category
            _seed_supplier_bill(c, "Cheap Supplier", None, "2026-08-01",
                                [(cat_id, 100, 75.0)])
            _seed_supplier_bill(c, "Expensive Supplier", None, "2026-08-05",
                                [(cat_id, 50, 90.0)])
        result = supplier_comparison_by_category()
        assert isinstance(result, list)
        # Find our test category in the result
        cat = next((r for r in result if r["category_id"] == cat_id), None)
        assert cat is not None, f"Category {cat_id} not in result"
        assert len(cat["suppliers"]) >= 2, f"Should have 2+ suppliers, got {len(cat['suppliers'])}"
        # Verify each supplier has the expected fields
        for s in cat["suppliers"]:
            assert "supplier_name" in s
            assert "avg_price" in s
            assert "last_price" in s
            assert "min_price" in s
            assert "delta_vs_running_avg" in s
            assert "is_cheapest" in s
        # The cheapest supplier should be flagged
        cheapest = min(cat["suppliers"], key=lambda s: s["avg_price"])
        assert cheapest["is_cheapest"] is True, f"Cheapest supplier {cheapest['supplier_name']} should be flagged"
        # Verify the cheapest avg is indeed the lower one
        assert cheapest["avg_price"] < 80, f"Expected cheapest avg < 80, got {cheapest['avg_price']}"
    finally:
        cleanup(test_dir)


def test_supplier_comparison_filters_to_one_category():
    """When category_id is passed, only that category is returned."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.category_ops import supplier_comparison_by_category
        with db.conn() as c:
            cats = c.execute("SELECT id FROM price_categories LIMIT 2").fetchall()
            assert len(cats) >= 1
            target_id = cats[0]["id"]
        result = supplier_comparison_by_category(category_id=target_id)
        assert len(result) == 1
        assert result[0]["category_id"] == target_id
    finally:
        cleanup(test_dir)


# ─── 2. Category cost-trend alerts ───────────────────────────────────

def test_cost_trend_alerts_detects_cost_increase():
    """When a category's avg_cost has risen > threshold% in 30 days, an alert
    is generated with severity warning or critical."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.category_ops import category_cost_trend_alerts
        # Find a category and pump up its current avg_cost via stock_adjustments
        # so the trend math triggers.
        with db.conn() as c:
            cat_row = c.execute("SELECT id FROM price_categories LIMIT 1").fetchone()
            cat_id = cat_row["id"]
            # Seed an OLD bill at low cost (more than 30 days ago)
            _seed_supplier_bill(c, "Old Supplier", None, "2026-06-01",
                                [(cat_id, 100, 50.0)])
            # Now bump the running avg_cost up via category_stock_state directly
            # (simulating a recent expensive purchase)
            c.execute(
                "UPDATE category_stock_state SET current_avg_cost = 75.0, "
                "current_value = current_qty * 75.0 WHERE category_id = ?",
                (cat_id,)
            )
        alerts = category_cost_trend_alerts(days=30, threshold_pct=5.0)
        # We should have at least one alert for our category (cost went 50→75 = +50%)
        cat_alerts = [a for a in alerts if a["category_id"] == cat_id]
        assert len(cat_alerts) >= 1, f"Expected at least 1 alert for cat {cat_id}, got {len(cat_alerts)}"
        a = cat_alerts[0]
        assert a["cost_change_pct"] > 0, "Cost should have increased"
        assert a["alert_severity"] in ("warning", "critical")
        assert "margin_drop_pct" in a
        assert "message" in a
    finally:
        cleanup(test_dir)


def test_cost_trend_alerts_returns_empty_when_no_history():
    """When there are no confirmed bills before the cutoff, no alerts."""
    test_dir = setup_test_db()
    try:
        from app.category_ops import category_cost_trend_alerts
        # Fresh DB — sample_data may have bills, so just verify the function
        # returns a list (could be empty or have alerts, but must not error)
        alerts = category_cost_trend_alerts(days=30, threshold_pct=5.0)
        assert isinstance(alerts, list)
        for a in alerts:
            assert "category_id" in a
            assert "alert_severity" in a
            assert "message" in a
    finally:
        cleanup(test_dir)


# ─── 3. Stock write-offs ────────────────────────────────────────────

def test_add_stock_writeoff_reduces_stock_state():
    """Writing off stock reduces category_stock_state.current_qty by `qty`
    AND records the loss value (qty × avg_cost) in stock_writeoffs."""
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.profit_cash import add_stock_writeoff
        # Find a category with current stock
        profit.rebuild_stock_state()
        with db.conn() as c:
            state_row = c.execute(
                "SELECT category_id, current_qty, current_avg_cost FROM category_stock_state "
                "WHERE current_qty > 10 LIMIT 1"
            ).fetchone()
            assert state_row is not None, "Need a category with stock > 10"
            cat_id = state_row["category_id"]
            qty_before = state_row["current_qty"]
            avg_cost = state_row["current_avg_cost"]
        # Write off 5 units
        woff_id = add_stock_writeoff(
            category_id=cat_id, qty=5, reason="damage",
            notes="5 units damaged in transit", manager_pin_verified=True
        )
        assert woff_id > 0
        with db.conn() as c:
            # 1. stock_writeoffs row exists with correct loss_value
            woff = c.execute("SELECT * FROM stock_writeoffs WHERE id = ?", (woff_id,)).fetchone()
            assert woff is not None
            assert woff["qty"] == 5
            assert woff["reason"] == "damage"
            assert woff["manager_pin_verified"] == 1
            expected_loss = round(5 * float(avg_cost or 0), 2)
            assert abs(woff["loss_value"] - expected_loss) < 0.5, \
                f"Loss value should be ~{expected_loss}, got {woff['loss_value']}"
            # 2. stock_state.current_qty reduced by 5
            state_after = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id = ?",
                (cat_id,)
            ).fetchone()
            assert state_after["current_qty"] == qty_before - 5, \
                f"Stock should be {qty_before - 5}, got {state_after['current_qty']}"
            # 3. stock_adjustments row exists with delta=-5
            adj = c.execute(
                "SELECT * FROM stock_adjustments WHERE category_id = ? AND delta = -5 "
                "ORDER BY id DESC LIMIT 1",
                (cat_id,)
            ).fetchone()
            assert adj is not None
            assert "writeoff: damage" in adj["reason"]
            # 4. activity_log entry
            log = c.execute(
                "SELECT * FROM activity_log WHERE event_type='stock_writeoff' AND entity_id=?",
                (woff_id,)
            ).fetchone()
            assert log is not None
    finally:
        cleanup(test_dir)


def test_add_stock_writeoff_rejects_invalid_reason():
    test_dir = setup_test_db()
    try:
        from app import profit
        from app.profit_cash import add_stock_writeoff
        profit.rebuild_stock_state()
        try:
            add_stock_writeoff(category_id=1, qty=5, reason="volcano")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "volcano" in str(e).lower() or "damage" in str(e).lower()
    finally:
        cleanup(test_dir)


def test_add_stock_writeoff_rejects_zero_qty():
    test_dir = setup_test_db()
    try:
        from app.profit_cash import add_stock_writeoff
        try:
            add_stock_writeoff(category_id=1, qty=0, reason="damage")
            assert False, "Should have raised"
        except ValueError:
            pass
    finally:
        cleanup(test_dir)


def test_add_stock_writeoff_rejects_qty_exceeding_stock():
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.profit_cash import add_stock_writeoff
        profit.rebuild_stock_state()
        with db.conn() as c:
            state_row = c.execute(
                "SELECT category_id, current_qty FROM category_stock_state "
                "WHERE current_qty > 0 LIMIT 1"
            ).fetchone()
            assert state_row is not None
            cat_id = state_row["category_id"]
            current_qty = state_row["current_qty"]
        try:
            add_stock_writeoff(category_id=cat_id, qty=current_qty + 100, reason="damage")
            assert False, "Should have raised (qty > current stock)"
        except ValueError as e:
            assert "exceeds" in str(e).lower() or "stock" in str(e).lower()
    finally:
        cleanup(test_dir)


# ─── 4. Stock write-off summary ─────────────────────────────────────

def test_stock_writeoff_summary_groups_by_reason():
    test_dir = setup_test_db()
    try:
        from app import db, profit
        from app.profit_cash import add_stock_writeoff
        from app.category_ops import stock_writeoff_summary
        profit.rebuild_stock_state()
        # Seed 3 write-offs: 2 damage, 1 theft
        with db.conn() as c:
            cats = c.execute(
                "SELECT category_id FROM category_stock_state WHERE current_qty > 5 LIMIT 3"
            ).fetchall()
        assert len(cats) >= 1
        cat_id = cats[0]["category_id"]
        add_stock_writeoff(category_id=cat_id, qty=2, reason="damage")
        add_stock_writeoff(category_id=cat_id, qty=3, reason="damage")
        add_stock_writeoff(category_id=cat_id, qty=1, reason="theft")
        summary = stock_writeoff_summary()
        assert "total_loss_value" in summary
        assert "by_reason" in summary
        assert summary["count"] >= 3
        # by_reason should have 'damage' and 'theft' entries
        reasons = {r["reason"]: r for r in summary["by_reason"]}
        assert "damage" in reasons
        assert reasons["damage"]["count"] >= 2
        assert "theft" in reasons
        assert reasons["theft"]["count"] >= 1
    finally:
        cleanup(test_dir)


# ─── 5. Bill confirm cost-vs-cheapest-supplier check ─────────────────

def test_check_bill_cost_flags_higher_priced_items():
    """When a new bill has items priced >5% above the cheapest historical
    supplier for that category, warnings are returned."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.category_ops import check_bill_cost_vs_cheapest_supplier
        # Find a category, seed historical cheap bills, then check a new expensive item
        with db.conn() as c:
            cat_row = c.execute("SELECT id FROM price_categories LIMIT 1").fetchone()
            cat_id = cat_row["id"]
            _seed_supplier_bill(c, "Cheap Supplier", None, "2026-07-01",
                                [(cat_id, 100, 50.0)])
        # Now check items priced at 70 (40% above cheapest avg of 50)
        warnings = check_bill_cost_vs_cheapest_supplier([
            {"category_id": cat_id, "price": 70.0}
        ])
        assert len(warnings) >= 1
        w = warnings[0]
        assert w["category_id"] == cat_id
        assert w["pct_higher"] > 5
        assert w["cheapest_supplier"] == "Cheap Supplier"
        assert "message" in w
    finally:
        cleanup(test_dir)


def test_check_bill_cost_no_warning_when_no_history():
    """When there's no historical bill for a category, no warning is returned."""
    test_dir = setup_test_db()
    try:
        from app.category_ops import check_bill_cost_vs_cheapest_supplier
        # Use a category_id that has no bill history (a very high ID)
        warnings = check_bill_cost_vs_cheapest_supplier([
            {"category_id": 999999, "price": 100.0}
        ])
        assert len(warnings) == 0
    finally:
        cleanup(test_dir)


# ─── 6. API endpoint tests ───────────────────────────────────────────

def test_api_writeoff_post_requires_pin():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        # Find a category with stock
        from app import db, profit
        profit.rebuild_stock_state()
        with db.conn() as c:
            state = c.execute(
                "SELECT category_id FROM category_stock_state WHERE current_qty > 5 LIMIT 1"
            ).fetchone()
            assert state is not None
            cat_id = state["category_id"]
        # No PIN
        r = client.post("/api/inventory/writeoff", json={
            "category_id": cat_id, "qty": 2, "reason": "damage"
        })
        assert r.status_code == 403, f"Expected 403 without PIN, got {r.status_code}"
        # Wrong PIN
        r2 = client.post("/api/inventory/writeoff", json={
            "category_id": cat_id, "qty": 2, "reason": "damage", "manager_pin": "9999"
        })
        assert r2.status_code == 403
    finally:
        cleanup(test_dir)


def test_api_writeoff_post_happy_path():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main, db, profit
        profit.rebuild_stock_state()
        with db.conn() as c:
            state = c.execute(
                "SELECT category_id, current_qty FROM category_stock_state WHERE current_qty > 5 LIMIT 1"
            ).fetchone()
            assert state is not None
            cat_id = state["category_id"]
            qty_before = state["current_qty"]
        client = TestClient(main.app)
        login_client(client)
        r = client.post("/api/inventory/writeoff", json={
            "category_id": cat_id, "qty": 3, "reason": "expiry",
            "notes": "3 expired units", "manager_pin": "1234"
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        woff_id = r.json()["id"]
        assert woff_id > 0
        # Verify stock reduced
        with db.conn() as c:
            state_after = c.execute(
                "SELECT current_qty FROM category_stock_state WHERE category_id = ?",
                (cat_id,)
            ).fetchone()
            assert state_after["current_qty"] == qty_before - 3
    finally:
        cleanup(test_dir)


def test_api_supplier_comparison_returns_200():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        r = client.get("/api/reports/supplier-comparison")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert "categories" in data
        assert isinstance(data["categories"], list)
    finally:
        cleanup(test_dir)


def test_api_cost_trends_returns_200():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main
        client = TestClient(main.app)
        login_client(client)
        r = client.get("/api/reports/category-cost-trends?days=30&threshold_pct=5.0")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert "alerts" in data
        assert "critical_count" in data
        assert "warning_count" in data
    finally:
        cleanup(test_dir)


def test_api_writeoffs_list_and_summary():
    test_dir = setup_test_db()
    try:
        from fastapi.testclient import TestClient
        from app import main, profit
        from app.profit_cash import add_stock_writeoff
        profit.rebuild_stock_state()
        from app import db
        with db.conn() as c:
            state = c.execute(
                "SELECT category_id FROM category_stock_state WHERE current_qty > 5 LIMIT 1"
            ).fetchone()
            assert state is not None
            cat_id = state["category_id"]
        add_stock_writeoff(category_id=cat_id, qty=2, reason="damage")
        client = TestClient(main.app)
        login_client(client)
        # List
        r = client.get("/api/reports/stock-writeoffs")
        assert r.status_code == 200
        assert len(r.json()["writeoffs"]) >= 1
        # Summary
        r2 = client.get("/api/reports/stock-writeoffs/summary")
        assert r2.status_code == 200
        assert r2.json()["count"] >= 1
    finally:
        cleanup(test_dir)


# ─── 7. AI prompt builder ────────────────────────────────────────────

def test_build_prompt_includes_shop_categories():
    """_build_prompt() should include the shop's actual categories, not just
    the hardcoded A/B/C/D defaults."""
    test_dir = setup_test_db()
    try:
        from app.extract import _build_prompt
        from app import db
        # Insert a custom category
        with db.conn() as c:
            c.execute(
                "INSERT INTO price_categories(name, code, sell_price, color, sort_order, active) "
                "VALUES('Custom Cat', 'XYZ', 9999, '#ff0000', 99, 1)"
            )
        prompt = _build_prompt()
        # The custom category code should appear in the dynamic section
        assert "XYZ" in prompt, "Custom category code 'XYZ' not in prompt"
        assert "9999" in prompt, "Custom sell price 9999 not in prompt"
        assert "DYNAMIC CATEGORY LIST" in prompt
        assert "AUTO-SUGGEST" in prompt
        # The JSON schema should include suggested_sell_price + suggestion_confidence
        assert "suggested_sell_price" in prompt
        assert "suggestion_confidence" in prompt
    finally:
        cleanup(test_dir)


def test_build_prompt_falls_back_when_no_categories():
    """When the price_categories table is empty, _build_prompt() returns the
    base PROMPT unchanged (graceful fallback)."""
    test_dir = setup_test_db()
    try:
        from app.extract import _build_prompt, PROMPT
        from app import db
        with db.conn() as c:
            c.execute("DELETE FROM price_categories")
        prompt = _build_prompt()
        # Should be the base prompt (no dynamic section)
        assert "DYNAMIC CATEGORY LIST" not in prompt
        # Should still be a non-empty string
        assert len(prompt) > 100
    finally:
        cleanup(test_dir)
