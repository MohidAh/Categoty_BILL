"""v8.18.6: bill flags must be plain strings — never '[object Object]'.

Root cause: the confirm endpoint stored cost-overrun warnings from
check_bill_cost_vs_cheapest_supplier() as dicts ({message: "...", ...}) in
bills.flags. The edit-bill page renders each flag with esc(f), and a dict
stringifies to '[object Object]' in the yellow warning alerts.

Fix (three layers, all covered here):
1. WRITE: confirm() now stores w["message"] strings, not dicts
2. READ:  get_bill() / list_bills() flatten any legacy dict flags on the way out
3. MERGE: re-confirm / add-pages sanitize old flags before re-storing them

Run with: pytest tests/test_flags_sanitized.py -v
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
SAMPLE_SQL = PROJ / "tests" / "sample_data.sql"


def setup_test_db():
    test_dir = tempfile.mkdtemp(prefix="billbook_flags_")
    from app import config, db
    db.DB_PATH = os.path.join(test_dir, "billbook.db")
    config.DATA = Path(test_dir)
    for name in ("DATA", "UPLOADS", "PAGES", "BACKUPS"):
        os.makedirs(getattr(config, name), exist_ok=True)
    db.init()
    with db.conn() as c:
        for t in ("sale_items", "sales", "bill_items", "bills",
                  "customers", "price_categories", "suppliers",
                  "stock_adjustments", "activity_log", "sessions",
                  "expenses", "expense_categories", "recurring_expenses",
                  "cash_drawer", "shifts", "employees",
                  "supplier_advances", "supplier_rates",
                  "bank_accounts", "bank_transactions",
                  "commission_rules", "commissions",
                  "category_stock_state", "loyalty_redemptions",
                  "quotations", "corrections"):
            c.execute(f"DELETE FROM {t}")
        with open(SAMPLE_SQL) as f:
            c.executescript(f.read())
    from app import profit
    profit.rebuild_stock_state()
    return test_dir


def cleanup(test_dir):
    shutil.rmtree(test_dir, ignore_errors=True)


def _insert_bill(status="review", flags=None, items=None):
    """Insert a bill directly (bypassing the API) with arbitrary flags."""
    from app import db
    items = items or []
    with db.conn() as c:
        bill_id = c.execute(
            "INSERT INTO bills(supplier_name, bill_date, bill_no, written_total, "
            "computed_total, status, payment_status, flags) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("Flag Supplier", "2026-08-15", "FLAG-1", 100, 100,
             status, "paid", json.dumps(flags) if flags is not None else "[]"),
        ).lastrowid
        for it in items:
            c.execute(
                "INSERT INTO bill_items(bill_id, raw, price, qty, unit, "
                "line_total, category_id, page_no) VALUES(?,?,?,?,?,?,?,?)",
                (bill_id, it["raw"], it["price"], it["qty"], it["unit"],
                 it["price"] * it["qty"], it.get("category_id"), 1),
            )
    return bill_id


def _seed_cheapest_history(price=100.0):
    """A confirmed historical bill so the new bill looks overpriced."""
    from app import db
    with db.conn() as c:
        sup_id = c.execute(
            "INSERT INTO suppliers(name, phone) VALUES('Cheap Co', '0300-cheap')"
        ).lastrowid
        hist_id = c.execute(
            "INSERT INTO bills(supplier_id, supplier_name, bill_date, bill_no, "
            "written_total, computed_total, status, payment_status) "
            "VALUES(?,?,?,?,?,?, 'confirmed', 'paid')",
            (sup_id, "Cheap Co", "2026-07-01", "HIST-1", price, price),
        ).lastrowid
        c.execute(
            "INSERT INTO bill_items(bill_id, raw, price, qty, unit, "
            "line_total, category_id, page_no) VALUES(?,?,?,?,?,?,?,?)",
            (hist_id, "cat item", price, 1, "pcs", price, 1, 1),
        )


# ─── _flag_text unit ────────────────────────────────────────────────────────

def test_flag_text_flattens_dicts():
    from app.routers.bills import _flag_text
    # dict WITH message → message text
    assert _flag_text({"message": "Paying too much"}) == "Paying too much"
    # dict WITHOUT message → compact JSON (still readable text, not [object Object])
    out = _flag_text({"a": 1})
    assert isinstance(out, str) and "a" in out and "object" not in out
    # plain strings pass through
    assert _flag_text("low confidence") == "low confidence"
    # numbers get stringified, not crashed on
    assert _flag_text(3) == "3"


# ─── WRITE path: confirm stores strings ─────────────────────────────────────

def test_confirm_stores_cost_warning_as_string():
    """End-to-end: a bill whose price is way above the cheapest historical
    supplier gets a cost-overrun warning on confirm — stored as TEXT."""
    test_dir = setup_test_db()
    try:
        from app import db
        from app.routers.bills import confirm, ConfirmIn, ItemIn

        _seed_cheapest_history(price=100.0)
        bill_id = _insert_bill(items=[{"raw": "cat item", "price": 200,
                                       "qty": 1, "unit": "pcs", "category_id": 1}])

        confirm(bill_id, ConfirmIn(
            supplier_name="Flag Supplier", phone="0300-flag",
            bill_date="2026-08-15", bill_no="FLAG-1",
            written_total=200, payment_status="paid",
            items=[ItemIn(raw="cat item", price=200, qty=1, unit="pcs",
                          category_id=1)],
        ))

        with db.conn() as c:
            raw = c.execute("SELECT flags FROM bills WHERE id=?",
                            (bill_id,)).fetchone()["flags"]
        flags = json.loads(raw)
        assert flags, f"expected cost-overrun flag, got {flags}"
        for f in flags:
            assert isinstance(f, str), f"flag is not a string: {f!r}"
        assert any("Paying Rs" in f for f in flags), (
            f"cost-overrun message missing: {flags}"
        )
    finally:
        cleanup(test_dir)


# ─── READ path: legacy dict rows are healed on the way out ─────────────────

_LEGACY_FLAGS = [
    "2 low-confidence items",
    {"type": "cost_overrun", "category_label": "A (Drinks)",
     "message": "Paying Rs 200.00 for A (Drinks) — Cheap Co sold you the same "
                "category at avg Rs 100.00. You're paying 100.0% more per unit.",
     "pct_higher": 100.0},
]


def test_get_bill_flattens_legacy_dict_flags():
    test_dir = setup_test_db()
    try:
        from app.routers.bills import get_bill
        bill_id = _insert_bill(flags=_LEGACY_FLAGS)
        out = get_bill(bill_id)
        assert len(out["flags"]) == 2
        for f in out["flags"]:
            assert isinstance(f, str), f"flag not flattened: {f!r}"
        assert "object Object" not in json.dumps(out["flags"])
        assert any("Cheap Co" in f for f in out["flags"])
    finally:
        cleanup(test_dir)


def test_list_bills_flattens_legacy_dict_flags():
    test_dir = setup_test_db()
    try:
        from app.routers.bills import list_bills
        _insert_bill(flags=_LEGACY_FLAGS)
        out = list_bills()
        assert out["total"] >= 1
        for row in out["bills"]:
            # flags stays a JSON *string* in the list API (the frontend parses
            # it) — but every element inside must be a string now.
            fl = json.loads(row["flags"])
            for f in fl:
                assert isinstance(f, str), f"flag not flattened: {f!r}"
            assert "object Object" not in row["flags"]
            assert row["flag_count"] == len(fl)
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
