"""v8.19.1: pagination clamping — never serve a page that no longer exists.

User scenarios covered:
  1. User is on the LAST page of a list and deletes everything on it
     (single delete or bulk delete) -> the next request for that page
     number must serve the new last page, not an empty table.
  2. User is on page 5 and applies a filter that shrinks the result to a
     single page -> the response is page 1, not a stuck-empty page 5.
  3. Out-of-range / zero / negative page params normalize to a valid page.

Every paginated list endpoint shares db.clamp_page(); these tests prove the
wiring end-to-end through the API for the main tables (bills, items/bills,
sales, customers, suppliers, expenses, activity).
"""
import pytest

from app import db
from app.shop import add_expense


# ─── helpers ────────────────────────────────────────────────────────────────

def _seed_bills(n, supplier="ACME Trading"):
    with db.conn() as c:
        c.execute("DELETE FROM bills")          # deterministic count (fixture loads sample data)
        for i in range(n):
            c.execute(
                "INSERT INTO bills(supplier_name, bill_no, bill_date, status, "
                "payment_status) VALUES(?,?,?,?,?)",
                (f"{supplier} #{i+1}", f"B-{i+1:03d}",
                 f"2026-08-{(i % 28) + 1:02d}", "confirmed", "paid"),
            )


def _seed_sales(n):
    with db.conn() as c:
        c.execute("DELETE FROM sales")           # deterministic count
        for i in range(n):
            c.execute(
                "INSERT INTO sales(invoice_no, total, payment_method, "
                "payment_status, created_at) VALUES(?,?,?,?,?)",
                (f"INV-{i+1:03d}", 100.0 + i, "cash", "paid",
                 f"2026-08-{(i % 28) + 1:02d} 10:00:00"),
            )


def _count_bills():
    with db.conn() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM bills WHERE deleted_at IS NULL"
        ).fetchone()["n"]


# ─── unit: the shared helper ────────────────────────────────────────────────

def test_clamp_page_unit():
    # normal range — untouched (45 rows at size 10 = 5 pages)
    assert db.clamp_page(1, 45, 10) == 1
    assert db.clamp_page(3, 45, 10) == 3
    assert db.clamp_page(5, 45, 10) == 5
    # beyond the last page -> last page
    assert db.clamp_page(6, 45, 10) == 5
    assert db.clamp_page(99, 45, 10) == 5
    # exact multiple of page size: page N+1 does not exist
    assert db.clamp_page(11, 100, 10) == 10
    assert db.clamp_page(10, 100, 10) == 10
    # non-exact multiple: last partial page is valid
    assert db.clamp_page(10, 95, 10) == 10
    # below 1 -> 1
    assert db.clamp_page(0, 45, 10) == 1
    assert db.clamp_page(-3, 45, 10) == 1
    # empty result -> page 1 (callers render their empty state)
    assert db.clamp_page(7, 0, 10) == 1
    # degenerate page_size -> 1
    assert db.clamp_page(5, 45, 0) == 1


# ─── /api/bills — the main list table ───────────────────────────────────────

def test_bills_list_serves_last_valid_page(authed_client):
    _seed_bills(25)                      # 3 pages at page_size=10
    r = authed_client.get("/api/bills?page=5&page_size=10")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 25
    assert data["pages_total"] == 3
    assert data["page"] == 3, "page 5 must clamp to the last valid page (3)"
    assert len(data["bills"]) == 5       # rows of page 3, not an empty list


def test_bills_delete_last_page_drops_to_previous(authed_client):
    """THE user scenario: delete everything on the last page -> auto-fallback
    to the previous page instead of a stuck empty table."""
    _seed_bills(30)                      # exactly 3 pages at page_size=10
    r = authed_client.get("/api/bills?page=3&page_size=10")
    assert r.json()["page"] == 3
    last_page_ids = [b["id"] for b in r.json()["bills"]]
    assert len(last_page_ids) == 10
    # bulk-delete everything on page 3 (soft delete, same as the UI)
    with db.conn() as c:
        c.execute(
            "UPDATE bills SET deleted_at=datetime('now') WHERE id IN "
            f"({','.join('?' * len(last_page_ids))})", last_page_ids)
    assert _count_bills() == 20
    # user is still on page 3 — must be served page 2 now
    r = authed_client.get("/api/bills?page=3&page_size=10")
    data = r.json()
    assert data["page"] == 2 and data["pages_total"] == 2
    assert len(data["bills"]) == 10


def test_bills_filter_shrink_clamps_to_page_one(authed_client):
    """User on page 5 applies a filter matching a single bill -> page 1."""
    _seed_bills(45)                      # 5 pages at page_size=10
    r = authed_client.get("/api/bills?page=5&page_size=10")
    assert r.json()["page"] == 5
    # filter that only matches ONE bill
    r = authed_client.get("/api/bills?page=5&page_size=10&q=B-007")
    data = r.json()
    assert data["total"] == 1
    assert data["page"] == 1 and data["pages_total"] == 1
    assert len(data["bills"]) == 1


def test_bills_empty_result_reports_page_one(authed_client):
    _seed_bills(5)
    r = authed_client.get("/api/bills?page=4&page_size=10&q=zzz-no-match")
    data = r.json()
    assert data["total"] == 0
    assert data["page"] == 1 and data["pages_total"] == 0


# ─── /api/items/bills — the Items by Bill page ──────────────────────────────

def test_items_bills_page_clamps(authed_client):
    _seed_bills(35)                      # 4 pages at default page_size
    r = authed_client.get("/api/items/bills?page=9&page_size=10")
    data = r.json()
    assert data["total"] == 35
    assert data["page"] == 4
    assert len(data["bills"]) == 5


def test_items_bills_search_shrink_clamps(authed_client):
    _seed_bills(25)
    r = authed_client.get("/api/items/bills?page=3&page_size=10&q=B-007")
    data = r.json()
    assert data["page"] == 1
    assert len(data["bills"]) == 1


# ─── /api/sales — POS sales history ─────────────────────────────────────────

def test_sales_list_page_clamps(authed_client):
    _seed_sales(25)
    r = authed_client.get("/api/sales?page=7&page_size=10")
    data = r.json()
    assert data["total"] == 25
    assert data["page"] == 3
    assert len(data["sales"]) == 5


def test_sales_delete_last_page_drops_to_previous(authed_client):
    _seed_sales(20)                      # 2 pages at page_size=10
    r = authed_client.get("/api/sales?page=2&page_size=10")
    ids = [s["id"] for s in r.json()["sales"]]
    with db.conn() as c:
        c.execute(f"DELETE FROM sales WHERE id IN ({','.join('?' * len(ids))})",
                  ids)
    r = authed_client.get("/api/sales?page=2&page_size=10")
    data = r.json()
    assert data["page"] == 1 and data["pages_total"] == 1
    assert len(data["sales"]) == 10


# ─── /api/customers + /api/suppliers + /api/expenses + /api/activity ────────

def test_customers_page_clamps(authed_client):
    with db.conn() as c:
        c.execute("DELETE FROM customers")       # deterministic count
        for i in range(30):
            c.execute(
                "INSERT INTO customers(name, phone) VALUES(?,?)",
                (f"Customer {i}", f"0300-{i:07d}"))
    r = authed_client.get("/api/customers?page=9&page_size=10")
    data = r.json()
    assert data["page"] == 3
    assert len(data["customers"]) == 10


def test_suppliers_page_clamps(authed_client):
    with db.conn() as c:
        c.execute("DELETE FROM suppliers")       # deterministic count
        for i in range(30):
            c.execute("INSERT INTO suppliers(name) VALUES(?)",
                      (f"Supplier {i}",))
    r = authed_client.get("/api/suppliers?page=1&page_size=10&page=9")
    data = r.json()
    assert data["page"] == 3
    assert len(data["suppliers"]) == 10


def test_suppliers_search_shrink_clamps(authed_client):
    with db.conn() as c:
        c.execute("DELETE FROM suppliers")
        for i in range(30):
            c.execute("INSERT INTO suppliers(name) VALUES(?)",
                      (f"Supplier {i}",))
    r = authed_client.get("/api/suppliers?page=5&page_size=10&q=Supplier 7")
    data = r.json()
    assert data["page"] == 1
    assert len(data["suppliers"]) == 1


def test_expenses_page_clamps(authed_client):
    with db.conn() as c:
        c.execute("DELETE FROM expenses")        # deterministic count
        c.execute("INSERT INTO expense_categories(name) VALUES('Test Cat')")
        cat_id = c.execute(
            "SELECT id FROM expense_categories ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    for i in range(15):
        add_expense(category="Test Cat", amount=10.0,
                    description=f"exp {i}", category_id=cat_id,
                    date_str="2026-08-01")
    r = authed_client.get("/api/expenses?page=4&page_size=10")
    data = r.json()
    assert data["page"] == 2
    assert len(data["expenses"]) == 5


def test_activity_page_clamps(authed_client):
    with db.conn() as c:
        c.execute("DELETE FROM activity_log")    # deterministic count
    for i in range(12):
        db.log_activity("test_event", "test", None, f"event {i}")
    r = authed_client.get("/api/activity?page=3&page_size=10")
    data = r.json()
    assert data["page"] == 2
    assert len(data["activity"]) == 2


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
