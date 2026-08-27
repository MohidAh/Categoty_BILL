"""Phase 0 PR 1: Tests for write_tx() and read_tx() transaction helpers.

Verifies:
- write_tx() commits on success
- write_tx() rolls back on exception
- write_tx() enforces foreign keys
- write_tx() rolls back on BaseException (KeyboardInterrupt)
- read_tx() closes the connection
"""
import sqlite3
import pytest

from app import db


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Create a temp database with a test table for write_tx tests."""
    db_path = tmp_path / "test_write_tx.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    with sqlite3.connect(db_path) as c:
        c.execute("""
            CREATE TABLE t (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                v TEXT
            )
        """)
        c.execute("""
            CREATE TABLE parent (
                id INTEGER PRIMARY KEY AUTOINCREMENT
            )
        """)
        c.execute("""
            CREATE TABLE child (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES parent(id)
            )
        """)

    return db_path


def count_rows(db_path, table="t"):
    """Count rows in a table."""
    with sqlite3.connect(db_path) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ─── write_tx: commit tests ──────────────────────────────────────────────────

def test_write_tx_commits_on_success(temp_db):
    """A successful write_tx block should commit the data."""
    with db.write_tx() as c:
        c.execute("INSERT INTO t(v) VALUES ('hello')")

    assert count_rows(temp_db) == 1


def test_write_tx_commits_multiple_writes(temp_db):
    """Multiple writes in the same transaction should all commit."""
    with db.write_tx() as c:
        c.execute("INSERT INTO t(v) VALUES ('first')")
        c.execute("INSERT INTO t(v) VALUES ('second')")
        c.execute("INSERT INTO t(v) VALUES ('third')")

    assert count_rows(temp_db) == 3


# ─── write_tx: rollback tests ─────────────────────────────────────────────────

def test_write_tx_rolls_back_on_exception(temp_db):
    """If an exception is raised inside write_tx, nothing should be committed."""
    with pytest.raises(RuntimeError):
        with db.write_tx() as c:
            c.execute("INSERT INTO t(v) VALUES ('hello')")
            raise RuntimeError("boom")

    assert count_rows(temp_db) == 0


def test_write_tx_rolls_back_on_keyboard_interrupt(temp_db):
    """BaseException (like KeyboardInterrupt) should also trigger rollback."""
    with pytest.raises(KeyboardInterrupt):
        with db.write_tx() as c:
            c.execute("INSERT INTO t(v) VALUES ('hello')")
            raise KeyboardInterrupt()

    assert count_rows(temp_db) == 0


def test_write_tx_rolls_back_on_sql_error(temp_db):
    """If a SQL error occurs mid-transaction, previous writes should roll back."""
    with pytest.raises(sqlite3.OperationalError):
        with db.write_tx() as c:
            c.execute("INSERT INTO t(v) VALUES ('first')")
            c.execute("INSERT INTO nonexistent_table(v) VALUES ('fail')")  # error

    assert count_rows(temp_db) == 0


# ─── write_tx: foreign key enforcement ───────────────────────────────────────

def test_write_tx_enforces_foreign_keys(temp_db):
    """write_tx should enforce foreign key constraints."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.write_tx() as c:
            c.execute("INSERT INTO child(parent_id) VALUES (999)")

    assert count_rows(temp_db, "child") == 0


# ─── write_tx: connection lifecycle ──────────────────────────────────────────

def test_write_tx_closes_connection_after_commit(temp_db):
    """The connection should be closed after a successful transaction."""
    with db.write_tx() as c:
        c.execute("INSERT INTO t(v) VALUES ('hello')")

    # Attempting to use the closed connection should raise
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_write_tx_closes_connection_after_rollback(temp_db):
    """The connection should be closed even after a failed transaction."""
    with pytest.raises(RuntimeError):
        with db.write_tx() as c:
            c.execute("INSERT INTO t(v) VALUES ('hello')")
            raise RuntimeError("fail")

    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


# ─── read_tx tests ────────────────────────────────────────────────────────────

def test_read_tx_closes_connection(temp_db):
    """read_tx should close the connection on exit."""
    with db.read_tx() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM t").fetchone()
        assert row["n"] == 0

    # Connection should be closed
    with pytest.raises(sqlite3.ProgrammingError):
        c.execute("SELECT 1")


def test_write_tx_works_after_read_tx(temp_db):
    """write_tx should work normally after a read_tx has been used and closed."""
    # Use read_tx for a read query
    with db.read_tx() as rc:
        rc.execute("SELECT COUNT(*) FROM t").fetchone()

    # write_tx should work fine after read_tx closes
    with db.write_tx() as wc:
        wc.execute("INSERT INTO t(v) VALUES ('from_write')")

    assert count_rows(temp_db) == 1
