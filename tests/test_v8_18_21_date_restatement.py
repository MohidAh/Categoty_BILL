"""v8.18.21 — bill-date restatement: visible, logged, and aligned.

WHAT THIS GUARDS
    Users were confused why avg cost moves when a bill's date changes. The
    mechanics are correct (chronological weighted average), but the change
    used to be SILENT. Now:
      1. PATCH /api/bills/{id} with a date change re-aligns stock state and
         restates crossed sales' costs (previously: silent stale state).
      2. GET /api/bills/{id}/date-impact reports how many sales a date
         change crosses, so the edit page can warn BEFORE saving.
      3. rebuild_stock_state(reason=...) only stamps sale_items.cost_recalc_at
         on REAL cost changes and writes a 'sale_cost_restated' activity entry.
      4. Confirming a back-dated bill for the first time also realigns (the
         incremental apply alone landed the purchase at 'now').
      5. The avg_cost live-math trace marks restated lines.

TIMELINE used by most tests (T = today):
      B1 confirmed  T-10  10 units @ Rs 80
      SALE          T-5   5 units        (cost recorded Rs 80)
      B2 confirmed  T-3   10 units @ Rs 100
    State: 15 units, Rs 1,400, avg Rs 93.33 — sale cost Rs 80.
    Moving B2's date to T-8 (before the sale) restates the sale to Rs 90
    (blended avg of B1+B2) and lands the state at 15 / Rs 1,350 / Rs 90.

Run: python -m pytest tests/test_v8_18_21_date_restatement.py -v
"""
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

TODAY = datetime.date.today()
def d(days_from_today: int) -> str:
    return (TODAY + datetime.timedelta(days=days_from_today)).strftime("%Y-%m-%d")


def _setup():
    """Fresh DB + logged-in manager client. Returns (client, cleanup_fn)."""
    test_dir = setup_test_db_with_password(prefix="billbook_v1821_")
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    login_client(client)
    return client, test_dir


def _mk_cat(client, code="T21", sell=150.0) -> int:
    r = client.post("/api/categories", json={"code": code, "name": f"Cat {code}",
                                             "sell_price": sell})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _confirm_bill(client, cat_id, date, qty=10, price=80.0, code="B21",
                  review_first=False):
    """Create + confirm a bill. Returns bill id."""
    if review_first:
        bid = client.post("/api/bills/empty").json()["id"]
    else:
        bid = client.post("/api/bills/empty").json()["id"]
    r = client.post(f"/api/bills/{bid}/confirm", json={
        "supplier_name": f"Sup {code}", "phone": "0300-0000021",
        "bill_date": date, "bill_no": f"{code}-{date}",
        "payment_status": "paid", "notes": "",
        "items": [{"raw": f"item {code}", "item_code": code, "price": price,
                   "qty": qty, "unit": "pcs", "category_id": cat_id}]})
    assert r.status_code == 200, r.text[:400]
    return bid


def _mk_sale(client, cat_id, qty=5, price=150.0, when=None) -> int:
    r = client.post("/api/sales", json={
        "items": [{"category_id": cat_id, "item_name": "Cat T21",
                   "qty": qty, "sell_price": price}],
        "payment_method": "cash"})
    assert r.status_code == 200, r.text[:400]
    sale_id = r.json()["sale_id"] if "sale_id" in r.json() else r.json().get("id")
    if when:
        from app import db
        with db.conn() as c:
            c.execute("UPDATE sales SET created_at=? WHERE id=?",
                      (f"{when} 12:00:00", sale_id))
    return sale_id


def _sale_line(cat_id):
    from app import db
    with db.conn() as c:
        return c.execute(
            "SELECT si.id, si.cost_price, si.cost_recalc_at FROM sale_items si "
            "JOIN sales s ON si.sale_id=s.id WHERE si.category_id=? "
            "AND s.payment_status IN ('paid','credit','partial')",
            (cat_id,)).fetchone()


def _state(cat_id):
    from app import db
    with db.conn() as c:
        r = c.execute("SELECT current_qty, current_value, current_avg_cost "
                      "FROM category_stock_state WHERE category_id=?", (cat_id,)).fetchone()
    return (round(float(r["current_qty"]), 2), round(float(r["current_value"]), 2),
            round(float(r["current_avg_cost"]), 2)) if r else (0.0, 0.0, 0.0)


def _build_timeline(client, cat_id):
    """B1(T-10, 10@80) → SALE(T-5, 5 units) → B2(T-3, 10@100)."""
    b1 = _confirm_bill(client, cat_id, d(-10), qty=10, price=80.0, code="B1")
    _mk_sale(client, cat_id, qty=5, when=d(-5))
    b2 = _confirm_bill(client, cat_id, d(-3), qty=10, price=100.0, code="B2")
    return b1, b2


# ─── 1. PATCH: date change realigns stock + restates crossed sales ─────────

def test_patch_bill_date_realigns_and_restates():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        assert _state(cat) == (15.0, 1400.0, 93.33), _state(cat)
        line = _sale_line(cat)
        assert abs(line["cost_price"] - 80.0) < 0.01
        assert line["cost_recalc_at"] is None

        # Move B2 back before the sale → the sale's cost must be restated
        r = client.patch(f"/api/bills/{b2}", json={"bill_date": d(-8)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stock_rebuilt"] is True
        assert body["restated_sale_costs"] == 1, body
        assert body["crossed_sale_lines"] == 1, body

        # Sale consumed the B1+B2 blend: Rs 90 (was Rs 80)
        line = _sale_line(cat)
        assert abs(line["cost_price"] - 90.0) < 0.01, line["cost_price"]
        assert line["cost_recalc_at"] is not None, "restatement must be stamped"
        # State: 15 units, 1800 − 450 = 1350, avg 90
        assert _state(cat) == (15.0, 1350.0, 90.0), _state(cat)

        # Audit trail: bill_edited (date) + sale_cost_restated (with reason).
        # NOTE: the sample data loaded by setup has its own drift, so a
        # restatement entry may already exist from the initial setup rebuild —
        # assert on THIS bill's entry specifically.
        from app import db
        with db.conn() as c:
            rows = c.execute(
                "SELECT event_type, description, metadata FROM activity_log "
                "WHERE event_type IN ('bill_edited','sale_cost_restated') "
                "ORDER BY id").fetchall()
        types = [r["event_type"] for r in rows]
        assert "bill_edited" in types and "sale_cost_restated" in types, types
        edit_row = next(r for r in rows if r["event_type"] == "bill_edited")
        assert f"Bill #{b2} date changed" in (edit_row["description"] or "")
        restate_row = next(
            r for r in rows
            if r["event_type"] == "sale_cost_restated"
            and f"bill {b2} date changed" in (r["description"] or ""))
        assert restate_row["description"], "restatement must carry its reason"
    finally:
        cleanup(test_dir)


def test_patch_same_date_or_other_field_no_rebuild():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        before = _state(cat)
        # same date → no rebuild
        r = client.patch(f"/api/bills/{b2}", json={"bill_date": d(-3)})
        assert r.status_code == 200 and r.json()["stock_rebuilt"] is False
        # unrelated field → no rebuild
        r = client.patch(f"/api/bills/{b2}", json={"payment_status": "credit"})
        assert r.status_code == 200 and r.json()["stock_rebuilt"] is False
        # bad date → 400
        r = client.patch(f"/api/bills/{b2}", json={"bill_date": "not-a-date"})
        assert r.status_code == 400
        assert _state(cat) == before
        assert _sale_line(cat)["cost_recalc_at"] is None
    finally:
        cleanup(test_dir)


def test_cashier_cannot_change_bill_date_but_can_patch_status():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        # swap session to a cashier
        from app import db
        from app.security import hash_pin
        with db.conn() as c:
            c.execute("DELETE FROM employees WHERE id=200")
            c.execute("INSERT INTO employees(id, name, role, pin, pin_hash, active) "
                      "VALUES(200, 'Test Cashier', 'cashier', NULL, ?, 1)",
                      (hash_pin("1234"),))
        r = client.post("/api/login/staff", json={"employee_id": 200, "pin": "1234"})
        assert r.status_code == 200 and r.json()["role"] == "cashier"

        r = client.patch(f"/api/bills/{b2}", json={"bill_date": d(-8)})
        assert r.status_code == 403, r.text
        # operational field still allowed (pre-existing behavior preserved)
        r = client.patch(f"/api/bills/{b2}", json={"payment_status": "credit"})
        assert r.status_code == 200, r.text
    finally:
        cleanup(test_dir)


# ─── 2. date-impact endpoint ────────────────────────────────────────────────

def test_date_impact_counts_crossed_sales():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)

        # moving B2 back across the sale → 1 sale / 1 line
        r = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-8)}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["old_date"] == d(-3)
        assert body["affected_sales"] == 1 and body["affected_sale_lines"] == 1, body

        # same date → nothing crossed
        r = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-3)}")
        assert r.json()["affected_sale_lines"] == 0

        # moving far back still only crosses the one sale
        r = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-20)}")
        assert r.json()["affected_sale_lines"] == 1

        # validation + 404
        assert client.get(f"/api/bills/{b2}/date-impact?new_date=oops").status_code == 400
        assert client.get("/api/bills/99999/date-impact?new_date=2026-01-01").status_code == 404
    finally:
        cleanup(test_dir)


# ─── 3. confirm: back-dated first realignment ──────────────────────────────

def test_confirm_backdated_bill_restates_past_sales():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        # timeline WITHOUT the sale first: B1 then B2, then the sale, then a
        # NEW back-dated bill confirmed AFTER the sale exists.
        b1 = _confirm_bill(client, cat, d(-10), qty=10, price=80.0, code="B1")
        _mk_sale(client, cat, qty=5, when=d(-5))
        # sale consumed avg 80 (only B1 existed)
        assert abs(_sale_line(cat)["cost_price"] - 80.0) < 0.01

        # NEW bill, back-dated before the sale (T-12), confirmed TODAY
        r = client.post("/api/bills/empty")
        bid = r.json()["id"]
        r = client.post(f"/api/bills/{bid}/confirm", json={
            "supplier_name": "Sup BACK", "phone": "0300-0000099",
            "bill_date": d(-12), "bill_no": "BACK-1", "payment_status": "paid",
            "notes": "",
            "items": [{"raw": "back", "item_code": "BACK", "price": 60.0,
                       "qty": 10, "unit": "pcs", "category_id": cat}]})
        assert r.status_code == 200, r.text[:400]
        assert r.json()["restated_sale_costs"] == 1, r.json()

        # Replay: BACK(10@60) → B1(10@80) → sale(5 @ avg 70) → state avg 70
        line = _sale_line(cat)
        assert abs(line["cost_price"] - 70.0) < 0.01, line["cost_price"]
        assert line["cost_recalc_at"] is not None
        # 20 bought − 5 sold = 15 units; value (600+800) − 350 = 1050
        assert _state(cat) == (15.0, 1050.0, 70.0), _state(cat)
    finally:
        cleanup(test_dir)


def test_confirm_dated_today_does_not_restate():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1 = _confirm_bill(client, cat, d(-10), qty=10, price=80.0, code="B1")
        _mk_sale(client, cat, qty=5, when=d(-5))   # before TODAY → not crossed
        r = client.post("/api/bills/empty")
        bid = r.json()["id"]
        r = client.post(f"/api/bills/{bid}/confirm", json={
            "supplier_name": "Sup TODAY", "phone": "0300-0000088",
            "bill_date": d(0), "bill_no": "TDY-1", "payment_status": "paid",
            "notes": "",
            "items": [{"raw": "today", "item_code": "TDY", "price": 90.0,
                       "qty": 8, "unit": "pcs", "category_id": cat}]})
        assert r.status_code == 200
        assert r.json()["restated_sale_costs"] == 0, r.json()
        assert _sale_line(cat)["cost_recalc_at"] is None
        # incremental apply as before: 10−5+8 = 13 units, 400+720 = 1120
        assert _state(cat) == (13.0, 1120.0, 86.15), _state(cat)
    finally:
        cleanup(test_dir)


def test_reconfirm_with_date_change_restates():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        # re-confirm B2 with an earlier date (edit → save path)
        r = client.post(f"/api/bills/{b2}/confirm", json={
            "supplier_name": "Sup B2", "phone": "0300-0000021",
            "bill_date": d(-8), "bill_no": "B2-edit", "payment_status": "paid",
            "notes": "",
            "items": [{"raw": "item B2", "item_code": "B2", "price": 100.0,
                       "qty": 10, "unit": "pcs", "category_id": cat}]})
        assert r.status_code == 200, r.text[:400]
        assert r.json()["restated_sale_costs"] >= 1, r.json()
        line = _sale_line(cat)
        assert abs(line["cost_price"] - 90.0) < 0.01
        assert line["cost_recalc_at"] is not None
        assert _state(cat) == (15.0, 1350.0, 90.0), _state(cat)
    finally:
        cleanup(test_dir)


# ─── 4. rebuild: restatement stamping is precise + idempotent ──────────────

def test_rebuild_restamps_only_real_changes_and_is_idempotent():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        # direct date change in DB (bypassing PATCH) + rebuild with reason
        from app import db
        from app.profit_engine import rebuild_stock_state
        with db.conn() as c:
            c.execute("UPDATE bills SET bill_date=? WHERE id=?", (d(-8), b2))
        result = rebuild_stock_state(reason="test: B2 moved back")
        assert result["restated_sale_costs"] == 1, result["restated_sale_costs"]
        line = _sale_line(cat)
        assert abs(line["cost_price"] - 90.0) < 0.01
        assert line["cost_recalc_at"] is not None

        # second rebuild: nothing changes → no restatement, stamp preserved
        result2 = rebuild_stock_state(reason="test: second pass")
        assert result2["restated_sale_costs"] == 0
        line2 = _sale_line(cat)
        assert abs(line2["cost_price"] - 90.0) < 0.01
        assert line2["cost_recalc_at"] is not None, "stamp must survive idempotent rebuild"

        # exactly ONE new restatement entry was caused by THIS timeline move
        # (the sample data's own drift may add one during setup — filter by reason)
        with db.conn() as c:
            n = c.execute(
                "SELECT COUNT(*) AS n FROM activity_log "
                "WHERE event_type='sale_cost_restated' "
                "AND description LIKE '%test: B2 moved back%'").fetchone()["n"]
        assert n == 1, f"expected 1 restatement entry for this reason, got {n}"
    finally:
        cleanup(test_dir)


# ─── 5. avg_cost live-math trace surfaces restatements ─────────────────────

def test_avg_cost_trace_marks_restated_lines():
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1, b2 = _build_timeline(client, cat)
        r = client.patch(f"/api/bills/{b2}", json={"bill_date": d(-8)})
        assert r.status_code == 200

        r = client.get(f"/api/calc/trace?metric=avg_cost&category_id={cat}")
        assert r.status_code == 200, r.text
        t = r.json()
        sale_events = [e for e in t["events"] if e["type"] == "sale"]
        assert sale_events, t["events"]
        assert any("cost restated" in e["label"] for e in sale_events), \
            [e["label"] for e in sale_events]
        assert any("Cost restated" in n or "restated" in n for n in t["notes"]), t["notes"]
        assert t["replay_matches_state"] is True
    finally:
        cleanup(test_dir)


# ─── 6. migration ───────────────────────────────────────────────────────────

def test_cost_recalc_at_column_exists():
    client, test_dir = _setup()
    try:
        from app import db
        with db.conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(sale_items)").fetchall()}
        assert "cost_recalc_at" in cols, cols
    finally:
        cleanup(test_dir)
