"""Phase 0 PR 2: Tests for connection-aware profit_engine functions.

Verifies:
- When `c` is provided, the function uses that connection and does NOT commit
- When `c` is None, the function opens its own write_tx() (backward compat)
- Return shapes are unchanged
- Rollback works correctly when caller raises an exception
"""
import pytest
import sqlite3

from app import db
from app.profit_engine import (
    apply_purchase_to_state,
    apply_sale_to_state,
    apply_adjustment_to_state,
    apply_transfer_out_to_state,
    reverse_sale_in_state,
    peek_avg_cost,
)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Create a temp DB with price_categories + category_stock_state."""
    db_path = tmp_path / "test_engine.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    with sqlite3.connect(db_path) as c:
        c.execute("""
            CREATE TABLE price_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT,
                sell_price REAL NOT NULL,
                color TEXT DEFAULT '#10b981',
                sort_order INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE category_stock_state (
                category_id INTEGER PRIMARY KEY,
                current_qty REAL NOT NULL DEFAULT 0,
                current_value REAL NOT NULL DEFAULT 0,
                current_avg_cost REAL NOT NULL DEFAULT 0,
                last_txn_at TEXT,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        c.execute("""
            CREATE TABLE bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'review',
                deleted_at TEXT DEFAULT NULL,
                bill_date TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        c.execute("""
            CREATE TABLE bill_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER,
                category_id INTEGER,
                price REAL,
                qty REAL,
                unit TEXT DEFAULT 'pcs'
            )
        """)
        c.execute("""
            CREATE TABLE sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER,
                category_id INTEGER,
                qty REAL,
                cost_price REAL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_status TEXT DEFAULT 'paid',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        c.execute("""
            CREATE TABLE stock_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                delta REAL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        c.execute("""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                entity_type TEXT,
                entity_id INTEGER,
                description TEXT,
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Insert a category
        c.execute("INSERT INTO price_categories(id, name, code, sell_price) VALUES (1, 'Test A', 'A', 250)")

    # Patch profit_engine's conn to use our temp DB
    from app import profit_engine
    original_conn = profit_engine.conn
    profit_engine.conn = db.conn  # use the patched db.conn

    yield db_path

    profit_engine.conn = original_conn


def get_state(category_id=1):
    """Helper to read current stock state."""
    with db.read_tx() as c:
        row = c.execute(
            "SELECT current_qty, current_value, current_avg_cost FROM category_stock_state WHERE category_id=?",
            (category_id,)
        ).fetchone()
    if row is None:
        return {"qty": 0, "value": 0, "avg": 0}
    return {"qty": float(row["current_qty"]), "value": float(row["current_value"]), "avg": float(row["current_avg_cost"])}


# ─── Backward compatibility tests (c=None) ──────────────────────────────────

def test_apply_purchase_backward_compat(temp_db):
    """Without c=, apply_purchase_to_state should still work (own write_tx)."""
    result = apply_purchase_to_state(1, 100, 200.0)
    assert result["qty"] == 100
    assert result["value"] == 20000
    assert result["avg"] == 200.0
    assert get_state(1)["qty"] == 100


def test_apply_sale_backward_compat(temp_db):
    """Without c=, apply_sale_to_state should still work."""
    apply_purchase_to_state(1, 100, 200.0)
    result = apply_sale_to_state(1, 10)
    assert result["qty"] == 90
    assert result["cogs"] == 2000
    assert get_state(1)["qty"] == 90


# ─── Connection-aware tests (c=provided) ─────────────────────────────────────

def test_apply_sale_with_connection_does_not_commit_on_exception(temp_db):
    """When c is provided and an exception is raised, stock should NOT change."""
    # Setup: purchase 100 @ Rs 200
    apply_purchase_to_state(1, 100, 200.0)
    assert get_state(1)["qty"] == 100

    # Try to sell inside a transaction that fails
    with pytest.raises(RuntimeError):
        with db.write_tx() as c:
            apply_sale_to_state(1, 10, c=c)
            raise RuntimeError("fail mid-transaction")

    # Stock should be unchanged (rollback)
    assert get_state(1)["qty"] == 100


def test_apply_sale_with_connection_commits_when_caller_commits(temp_db):
    """When c is provided and the caller commits, stock should be updated."""
    apply_purchase_to_state(1, 100, 200.0)

    with db.write_tx() as c:
        apply_sale_to_state(1, 10, c=c)

    assert get_state(1)["qty"] == 90
    assert get_state(1)["value"] == 18000


def test_apply_purchase_with_connection_does_not_open_new_tx(temp_db, monkeypatch):
    """When c is provided, the function should NOT call db.write_tx()."""
    # Patch write_tx to raise if called
    original_write_tx = db.write_tx
    call_count = [0]

    def spy_write_tx():
        call_count[0] += 1
        return original_write_tx()

    monkeypatch.setattr(db, "write_tx", spy_write_tx)

    with db.write_tx() as c:
        apply_purchase_to_state(1, 100, 200.0, c=c)

    # write_tx should have been called once (by us), not twice (by apply_purchase)
    assert call_count[0] == 1


# ─── Return shape tests ─────────────────────────────────────────────────────

def test_apply_purchase_return_shape(temp_db):
    result = apply_purchase_to_state(1, 100, 200.0)
    assert "qty" in result
    assert "value" in result
    assert "avg" in result


def test_apply_sale_return_shape(temp_db):
    apply_purchase_to_state(1, 100, 200.0)
    result = apply_sale_to_state(1, 10)
    assert "qty" in result
    assert "value" in result
    assert "avg" in result
    assert "cogs" in result


def test_apply_adjustment_return_shape(temp_db):
    apply_purchase_to_state(1, 100, 200.0)
    result = apply_adjustment_to_state(1, -5)
    assert "qty" in result
    assert "value" in result
    assert "avg" in result


def test_apply_transfer_out_return_shape(temp_db):
    apply_purchase_to_state(1, 100, 200.0)
    result = apply_transfer_out_to_state(1, 10)
    assert "qty" in result
    assert "value" in result
    assert "avg" in result
    assert "unit_cost" in result
    assert "line_value" in result


def test_reverse_sale_return_shape(temp_db):
    apply_purchase_to_state(1, 100, 200.0)
    apply_sale_to_state(1, 10)
    result = reverse_sale_in_state(1, 10, 2000)
    assert "qty" in result
    assert "value" in result
    assert "avg" in result


# ─── Multi-operation transaction test ───────────────────────────────────────

def test_multiple_operations_in_one_transaction(temp_db):
    """Multiple stock operations in one write_tx should all commit or all rollback."""
    with pytest.raises(RuntimeError):
        with db.write_tx() as c:
            apply_purchase_to_state(1, 100, 200.0, c=c)
            apply_sale_to_state(1, 10, c=c)
            apply_adjustment_to_state(1, -5, c=c)
            raise RuntimeError("fail after 3 operations")

    # All should be rolled back
    assert get_state(1)["qty"] == 0

    # Now do it without the error
    with db.write_tx() as c:
        apply_purchase_to_state(1, 100, 200.0, c=c)
        apply_sale_to_state(1, 10, c=c)
        apply_adjustment_to_state(1, -5, c=c)

    # 100 - 10 - 5 = 85
    assert get_state(1)["qty"] == 85
