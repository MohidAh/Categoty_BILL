"""v8.18.22 — review fixes on the v8.18.21 date-restatement feature.

WHAT THIS GUARDS
    The v8.18.21 windows in _bill_date_crossed_sales did not match the
    engine's replay tie-break (sort key (ts, seq): a bill dated on day X
    sorts at X MIDNIGHT, i.e. BEFORE every sale made that day). Three
    consequences, all fixed and all guarded here:

    1. FIRST-CONFIRM GAP (correctness): a back-dated bill whose only
       crossed sales were ON the back-date day itself never realigned —
       the sale kept a stale cost until an unrelated rebuild. Now the
       first-confirm window is >= new_date and the realign fires.
    2. FORWARD WINDOW SHIFTED: moving a date FORWARD used (old, new] —
       it missed the same-day-as-old-date sale that flips, and counted
       the same-day-as-new-date sale that does NOT. Now [old, new),
       the exact mirror of the back window.
    3. PREVIEW BLIND FOR FIRST EDITS: a bill edited for the first time
       has no bill_items rows yet (items ride the confirm payload), so
       the pre-save date-impact warning was blind exactly for the
       back-dated first-confirm case. The edit page now sends the
       about-to-be-saved category_ids; the endpoint accepts them.

    Plus: the warning dialog wording is direction-aware (a forward move
    takes stock AWAY from the crossed sales), and the dead data-orig
    attribute is gone.

TIMELINE (T = today):
    B0 confirmed T-10  10 units @ Rs 80
    SALE         T-5   5 units          (cost recorded Rs 80)
    back-dated bill dated T-5 crosses the sale (bill-midnight < sale noon)

Run: python -m pytest tests/test_v8_18_22_window_review.py -v
"""
import sys
import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_helpers import setup_test_db_with_password, cleanup, login_client

TODAY = datetime.date.today()
def d(n): return (TODAY + datetime.timedelta(days=n)).strftime("%Y-%m-%d")


def _setup():
    test_dir = setup_test_db_with_password(prefix="billbook_v1822_")
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    login_client(client)
    return client, test_dir


def _mk_cat(client, code="T22", sell=150.0) -> int:
    r = client.post("/api/categories", json={"code": code, "name": f"Cat {code}",
                                             "sell_price": sell})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _confirm_bill(client, cat_id, date, qty=10, price=80.0, code="B22") -> int:
    bid = client.post("/api/bills/empty").json()["id"]
    r = client.post(f"/api/bills/{bid}/confirm", json={
        "supplier_name": f"Sup {code}", "phone": "0300-0000022",
        "bill_date": date, "bill_no": f"{code}-{date}",
        "payment_status": "paid", "notes": "",
        "items": [{"raw": f"item {code}", "item_code": code, "price": price,
                   "qty": qty, "unit": "pcs", "category_id": cat_id}]})
    assert r.status_code == 200, r.text[:400]
    return bid


def _mk_sale(client, cat_id, qty=5, price=150.0, when=None) -> int:
    r = client.post("/api/sales", json={
        "items": [{"category_id": cat_id, "item_name": "Cat",
                   "qty": qty, "sell_price": price}],
        "payment_method": "cash"})
    assert r.status_code == 200, r.text[:400]
    sale_id = r.json().get("sale_id") or r.json().get("id")
    if when:
        from app import db
        with db.conn() as c:
            c.execute("UPDATE sales SET created_at=? WHERE id=?",
                      (f"{when} 12:00:00", sale_id))
    return sale_id


def _sale_line_cost(cat_id):
    from app import db
    with db.conn() as c:
        return c.execute(
            "SELECT si.cost_price FROM sale_items si "
            "JOIN sales s ON si.sale_id=s.id WHERE si.category_id=? "
            "AND s.payment_status IN ('paid','credit','partial') "
            "ORDER BY si.id DESC LIMIT 1", (cat_id,)).fetchone()["cost_price"]


# ─── 1. first-confirm gap: same-day sale must realign + restamp ─────────────

def test_backdated_first_confirm_restates_same_day_sale():
    """A sale made at NOON on the back-date day flips (the bill sorts at
    midnight, before it) — the 8.18.21 window missed it and the sale kept
    its stale cost forever."""
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        _confirm_bill(client, cat, d(-10), qty=10, price=80.0, code="B0")
        _mk_sale(client, cat, qty=5, when=d(-5))      # noon on T-5, cost 80
        # first confirm of a bill BACK-DATED to the sale's own day
        bid = _confirm_bill(client, cat, d(-5), qty=10, price=100.0, code="B1")
        from app import db
        with db.conn() as c:
            row = c.execute(
                "SELECT si.cost_price, si.cost_recalc_at FROM sale_items si "
                "JOIN sales s ON si.sale_id=s.id WHERE si.category_id=?",
                (cat,)).fetchone()
        # replay: the T-5 midnight bill precedes the T-5 noon sale -> blended 90
        assert abs(float(row["cost_price"]) - 90.0) < 0.005, row["cost_price"]
        assert row["cost_recalc_at"] is not None, "same-day sale must be stamped"
    finally:
        cleanup(test_dir)


# ─── 2. forward window: [old, new) — exact mirror of the back window ───────

def test_forward_window_membership():
    """Sale ON the old date flips (counted); sale ON the new date does not
    (not counted). The 8.18.21 window (old, new] got both ends wrong."""
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        _confirm_bill(client, cat, d(-10), qty=20, price=80.0, code="A")
        bidB = _confirm_bill(client, cat, d(-7), qty=10, price=100.0, code="B")
        _mk_sale(client, cat, qty=1, when=d(-7))      # noon on B's own date
        _mk_sale(client, cat, qty=1, when=d(-2))      # noon on the target date
        # preview: move B FORWARD d(-7) -> d(-2): only the d(-7) sale flips
        r = client.get(f"/api/bills/{bidB}/date-impact?new_date={d(-2)}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["affected_sale_lines"] == 1, body
        assert body["affected_sales"] == 1, body
    finally:
        cleanup(test_dir)


def test_forward_patch_restates_old_date_sale():
    """End-to-end: PATCHing a confirmed bill's date FORWARD past a same-day
    sale must restate that sale (it loses the stock it had consumed)."""
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        _confirm_bill(client, cat, d(-10), qty=20, price=80.0, code="A")
        bidB = _confirm_bill(client, cat, d(-7), qty=10, price=100.0, code="B")
        _mk_sale(client, cat, qty=1, when=d(-7))     # noon, after B's midnight
        # pool at sale time: 20@80 + 10@100 = 2600/30 -> 86.67
        assert abs(_sale_line_cost(cat) - 86.67) < 0.01
        r = client.patch(f"/api/bills/{bidB}",
                         json={"bill_date": d(-2)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stock_rebuilt"] is True, body
        assert body["crossed_sale_lines"] == 1, body
        # after: the T-7 noon sale precedes B -> it consumed only A @ 80
        assert abs(_sale_line_cost(cat) - 80.0) < 0.005, _sale_line_cost(cat)
        assert body["restated_sale_costs"] >= 1, body
    finally:
        cleanup(test_dir)


# ─── 3. category_ids param: the preview works for first-time edits ─────────

def test_date_impact_category_ids_param():
    """A bill with no stored items (first-time edit) gets its about-to-be-
    saved categories from the query param; malformed input is ignored."""
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        _confirm_bill(client, cat, d(-10), qty=10, price=80.0, code="B0")
        _mk_sale(client, cat, qty=5, when=d(-5))
        bid = client.post("/api/bills/empty").json()["id"]  # no items yet
        # DB fallback: nothing stored -> 0
        r = client.get(f"/api/bills/{bid}/date-impact?new_date={d(-5)}")
        assert r.json()["affected_sale_lines"] == 0, r.json()
        # with the edit page's categories -> the sale is visible
        r = client.get(f"/api/bills/{bid}/date-impact"
                       f"?new_date={d(-5)}&category_ids={cat}")
        body = r.json()
        assert body["affected_sale_lines"] == 1, body
        assert body["category_ids"] == [cat], body
        # malformed values are ignored, not a 500
        r = client.get(f"/api/bills/{bid}/date-impact"
                       f"?new_date={d(-5)}&category_ids=x,abc,,{cat}")
        assert r.status_code == 200, r.text
        assert r.json()["affected_sale_lines"] == 1, r.json()
    finally:
        cleanup(test_dir)


# ─── 4. back window unchanged (regression on the original 8.18.21 tests) ───

def test_back_window_unchanged():
    """Moving BACK still crosses [new, old) — same expectations as the
    v8.18.21 suite (guards against over-correcting)."""
    client, test_dir = _setup()
    try:
        cat = _mk_cat(client)
        b1 = _confirm_bill(client, cat, d(-10), qty=10, price=80.0, code="B1")
        _mk_sale(client, cat, qty=5, when=d(-5))
        b2 = _confirm_bill(client, cat, d(-3), qty=10, price=100.0, code="B2")
        # back move over the sale
        body = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-8)}").json()
        assert body["affected_sale_lines"] == 1, body
        # same date -> 0
        body = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-3)}").json()
        assert body["affected_sale_lines"] == 0, body
        # sale ON the target date of a back move IS crossed (>= new_date)
        _mk_sale(client, cat, qty=1, when=d(-8))
        body = client.get(f"/api/bills/{b2}/date-impact?new_date={d(-8)}").json()
        assert body["affected_sale_lines"] == 2, body  # T-5 sale + T-8 sale
        assert b1 > 0
    finally:
        cleanup(test_dir)


# ─── 5. static JS guards: direction-aware wording + cats passed ────────────

def test_js_direction_aware_warning_and_cats():
    src = (PROJECT_ROOT / "app/static/js/apps/pos/components/bill-edit-extras.js"
           ).read_text(encoding="utf-8")
    # the forward-move wording says stock is TAKEN AWAY, not given
    assert "no longer consumed this stock" in src, "forward wording missing"
    assert "category_ids=" in src, "edit page must send its categories"
    assert "items.map(i => i.category_id)" in src, "cats come from the items"
    # the old dead attribute is gone from the page template
    page = (PROJECT_ROOT / "app/static/js/pages/bill-edit.js"
            ).read_text(encoding="utf-8")
    assert "data-orig" not in page, "dead data-orig attribute should be gone"
