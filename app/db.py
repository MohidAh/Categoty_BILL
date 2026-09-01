"""Database schema, connection helpers, and migrations."""
import logging
import sqlite3
import sys
from contextlib import contextmanager
from .config import DATA

logger = logging.getLogger(__name__)

DB_PATH = DATA / "billbook.db"

# ─── v8.14.0: Data-at-rest encryption via SQLCipher ─────────────────────────
# If the `sqlcipher3-binary` package is installed AND a key is configured
# (via env BILLBOOK_DB_KEY or settings.db_encryption_key), we use SQLCipher
# (AES-256-CBC + HMAC-SHA256 per page) instead of plain SQLite. The DB file
# on disk is then opaque ciphertext — if the laptop is stolen, the thief
# can't read customer PII (phone numbers, addresses, purchase history,
# cash drawer amounts) without the key.
#
# Key derivation: PBKDF2-HMAC-SHA256, 480k iterations, 16-byte random salt
# persisted in `settings.crypto_salt` (already used by app/crypto.py for
# API-key encryption). The key is derived from the manager's password hash
# (NOT the plaintext password — we never store that).
#
# Migration path: on first run after upgrade, if a plaintext DB exists and
# a key is configured, we copy → encrypt → swap. See `migrate_to_encrypted()`.
# Old code that imports `sqlite3` directly continues to work because the
# sqlcipher3.dbapi2 module is API-compatible with sqlite3.

try:
    from sqlcipher3 import dbapi2 as _sqlcipher
    _HAS_SQLCIPHER = True
except ImportError:
    _sqlcipher = None
    _HAS_SQLCIPHER = False


def _get_db_key() -> str | None:
    """Resolve the encryption key from env or settings. Returns None if
    encryption is not enabled (backwards-compat with plaintext installs)."""
    import os
    key = os.getenv("BILLBOOK_DB_KEY", "").strip()
    if key:
        return key
    # Lazy lookup in settings — wrapped in try/except because settings table
    # may not exist yet during first-run init().
    try:
        row = sqlite3.connect(str(DB_PATH), timeout=2).execute(
            "SELECT value FROM settings WHERE key='db_encryption_key'"
        ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _connect(db_path=None, *, timeout: float = 10.0, isolation_level=None):
    """Open a DB connection — uses SQLCipher if a key is configured, else
    falls back to plain sqlite3. Sets the same PRAGMAs in both paths.

    Args:
        db_path: defaults to module-level DB_PATH at CALL TIME (not at
            function-definition time — tests monkeypatch db.DB_PATH and
            expect _connect to see the new value).
        isolation_level: pass None for manual transaction control (used by
            write_tx); pass 'DEFERRED'/'IMMEDIATE'/'EXCLUSIVE' for the
            sqlite3 module's auto-transaction behaviour; default None.
    """
    if db_path is None:
        # Look up DB_PATH at call time so monkeypatch works in tests.
        db_path = globals()["DB_PATH"]
    key = _get_db_key()
    if key and _HAS_SQLCIPHER:
        c = _sqlcipher.connect(str(db_path), timeout=timeout, isolation_level=isolation_level)
        # SQLCipher requires the key BEFORE any other PRAGMA.
        # v8.14.1 FIX: the previous f-string interpolation `f"PRAGMA key=\"{key}\""`
        # was (a) SQL-injection-shaped if the key ever contained a `"` or `;`, and
        # (b) fragile against keys with backslashes or shell metacharacters.
        # SQLCipher's documented safe form is the parameterised q-mark binding
        # (supported by sqlcipher3 since 0.5.0). We validate the key is hex or
        # alphanumeric to fail-closed on weird inputs, then use ?-binding.
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9+/=_\-]{8,256}", key):
            raise RuntimeError(
                "DB encryption key contains disallowed characters — refusing to "
                "PRAGMA-inject. Use a hex / base64 / alphanumeric key (>=8 chars)."
            )
        # Use ?-binding (works for both passphrase and hex-key forms).
        c.execute("PRAGMA key = ?", (key,))
    else:
        c = sqlite3.connect(str(db_path), timeout=timeout, isolation_level=isolation_level)
        if key and not _HAS_SQLCIPHER:
            logger.warning(
                "DB encryption key is set but sqlcipher3-binary not installed — "
                "running in PLAINTEXT mode. Install with: pip install sqlcipher3-binary"
            )
    c.row_factory = sqlite3.Row
    return c

# ════════════════════════════════════════════════════════════════════════════════
# v8.9.1: Canonical filter constants for deleted-data suppression
# ════════════════════════════════════════════════════════════════════════════════
# BillBook has exactly 4 payment_status values:
#   'paid'     — fully paid (cash/card/online)
#   'credit'   — full credit (customer owes the full amount)
#   'partial'  — split payment with unpaid portion
#   'refunded' — full refund (sale reversed, items returned to stock)
# There are NO partial refunds (no refunded_qty column, no refund_items table).
# When a sale is refunded, payment_status is set to 'refunded' and the ENTIRE
# sale (all items, all qty) is excluded from "sold" aggregations.
#
# USAGE: In any query that aggregates sale_items, JOIN sales and use:
#   AND {VALID_SALE_FILTER}
# where {VALID_SALE_FILTER} is the string below (with the 's.' alias prefix).
# If your query uses a different alias for the sales table, use:
#   VALID_SALE_STATUSES + build the IN clause yourself.
# ────────────────────────────────────────────────────────────────────────────────

VALID_SALE_STATUSES = ("paid", "credit", "partial")
INVALID_SALE_STATUSES = ("refunded",)

VALID_SALE_FILTER = (
    "s.payment_status IN ('paid', 'credit', 'partial')"
)

VALID_SALE_FILTER_NO_ALIAS = (
    "payment_status IN ('paid', 'credit', 'partial')"
)

# v8.15.0: Dynamic sort validation — prevents SQL injection on ORDER BY.
# ORDER BY cannot use ? placeholders, so column names must be validated
# against a strict whitelist before interpolation.
#
# Usage in a router:
#   sort_by, sort_order = db.validate_sort(
#       request.query_params.get("sort_by", ""),
#       request.query_params.get("sort_order", "desc"),
#       {"date": "bill_date", "supplier": "supplier_name", "total": "written_total"},
#       default="bill_date DESC"
#   )
#   sql += f" ORDER BY {sort_by}"
import re as _re

_SORT_COLUMN_RE = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")

def validate_sort(sort_by: str, sort_order: str, allowed_columns: dict,
                  default: str = "created_at DESC, id DESC") -> str:
    """Validate sort parameters against a whitelist and return a safe ORDER BY clause.

    SECURITY: ORDER BY clauses cannot use parameterized ? placeholders in SQLite.
    This function validates the column name against a strict whitelist (dict keys)
    and the direction (asc/desc only) before building the ORDER BY string.

    Args:
        sort_by: Column name from the frontend (e.g. "date", "supplier", "total").
                 Must be a key in allowed_columns.
        sort_order: "asc" or "desc" (case-insensitive).
        allowed_columns: Dict mapping frontend column names to SQL expressions:
            {"date": "bill_date", "supplier": "supplier_name", "total": "COALESCE(written_total, computed_total)"}
        default: Fallback ORDER BY clause if sort_by is empty or invalid.

    Returns:
        A safe ORDER BY string, e.g. "bill_date DESC" or "supplier_name ASC".
    """
    if not sort_by or sort_by not in allowed_columns:
        return default
    col_expr = allowed_columns[sort_by]
    # Validate the SQL expression from the whitelist (defense in depth —
    # the whitelist itself is trusted, but this catches accidental typos)
    if not _SORT_COLUMN_RE.match(col_expr.replace(" ", "").replace(",", "").replace("(", "").replace(")", "")):
        return default
    direction = "DESC" if (sort_order or "desc").upper().strip() == "DESC" else "ASC"
    return f"{col_expr} {direction}, id {direction}"

# Also provide a placeholder-form for f-string interpolation:
#   f"WHERE {VALID_SALE_FILTER}"  →  "WHERE s.payment_status IN ('paid', 'credit', 'partial')"
#   f"WHERE {VALID_SALE_FILTER_NO_ALIAS}"  →  "WHERE payment_status IN ('paid', 'credit', 'partial')"

# Pre-built SQL fragment for active (non-soft-deleted) bills with alias 'b':
ACTIVE_BILL_FILTER = "b.deleted_at IS NULL"
ACTIVE_BILL_CONFIRMED_FILTER = "b.status = 'confirmed' AND b.deleted_at IS NULL"


def clamp_page(page: int, total: int, page_size: int) -> int:
    """v8.19.1: Clamp a requested page number into the valid range [1, pages_total].

    Fixes the "stuck on an empty page" UX bug: when the user is on the LAST
    page of a list and deletes everything on it (or applies a filter that
    shrinks the result set), the next request for that page number would
    return an empty page and the UI would sit on a blank table. Every
    paginated list endpoint now clamps the requested page to the last page
    that still has rows, so the client is automatically served (and told,
    via the response's "page" field) the nearest valid page instead.

    Rules:
      - total == 0  → page 1 (empty list, callers render their empty state)
      - page < 1    → 1
      - page > last → last  (e.g. page 5 of 3 → 3; deleting the last page's
                      rows drops you to the new last page, not a blank one)

    Args:
        page: Requested 1-based page number.
        total: Total matching rows (already filtered).
        page_size: Rows per page (>= 1).

    Returns:
        The clamped page number the endpoint should actually serve.
    """
    if page_size is None or page_size < 1:
        return 1
    page = int(page or 1)
    if total <= 0:
        return 1
    pages_total = (int(total) + page_size - 1) // page_size
    return max(1, min(page, pages_total))


def conn():
    c = _connect()  # v8.14.0: uses SQLCipher if a key is configured
    # v8.5.4: journal_mode=WAL is persistent — set once in init(), not per connection.
    # Executing it on every connection was the #1 import speed bottleneck
    # (each call writes to the DB file header).
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 5000")
    # v8.13.1: Performance PRAGMAs — bigger cache + temp in memory + mmap.
    # Defaults (cache_size=-2000=2MB, temp_store=FILE, mmap_size=0) are too
    # small for tables with >100K rows. These per-connection settings add ~5µs
    # per connect but make sorts/aggregations 5-10× faster.
    c.execute("PRAGMA cache_size = -65536")  # 64MB page cache
    c.execute("PRAGMA temp_store = MEMORY")  # keep temp tables/sorts in RAM
    c.execute("PRAGMA mmap_size = 268435456")  # 256MB mmap for read-only access
    return c


# ─── Phase 0 PR 1: Transaction helpers ────────────────────────────────────
# These provide safe, explicit transaction control for write and read operations.
# write_tx() uses BEGIN IMMEDIATE to acquire the write lock BEFORE any SQL runs,
# preventing race conditions where two concurrent sales both pass the stock check.
# read_tx() is a safe read-only connection that properly closes on exit
# (unlike the existing conn() which does NOT close on context exit).

@contextmanager
def write_tx():
    """Explicit SQLite write transaction with IMMEDIATE lock.

    Acquires a reserved write lock immediately via BEGIN IMMEDIATE.
    This prevents the "two concurrent sales both pass stock check" race:

        Sale A checks stock (5 available)
        Sale B checks stock (5 available)   ← blocked! Sale A holds the lock
        Sale A reduces stock to 4, commits
        Sale B acquires lock, checks stock (4 available) ← sees Sale A's change

    Usage:
        with db.write_tx() as c:
            c.execute("INSERT INTO sales ...")
            c.execute("UPDATE category_stock_state ...")
            # All commit together. If ANY line fails, ALL roll back.

    Key details:
    - isolation_level=None puts sqlite3 in manual transaction mode
      (otherwise Python's sqlite3 module tries to auto-manage transactions
      and will interfere with explicit BEGIN IMMEDIATE)
    - ROLLBACK on ANY exception (including BaseException for KeyboardInterrupt)
    - Connection is always closed in finally
    - v8.14.0: uses _connect() so SQLCipher encryption is honored
    - v8.13.5 (H10 fix): refuses to COMMIT if the caller swallowed an
      exception inside the with-block — surfaces the bug instead of
      persisting a partial transaction
    """
    c = _connect(timeout=10, isolation_level=None)  # manual transaction control
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 5000")
    # v8.13.1: Performance PRAGMAs (mirror of conn())
    c.execute("PRAGMA cache_size = -65536")
    c.execute("PRAGMA temp_store = MEMORY")
    c.execute("PRAGMA mmap_size = 268435456")

    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            # v8.13.5 (H10 fix): Detect "swallowed exception" footgun.
            # If the caller did `try: ...; except: pass` inside the with-block,
            # `yield` returns normally even though an error happened —
            # without this check COMMIT would fire and persist a partial
            # transaction. We refuse to commit while inside an except handler
            # and roll back instead, surfacing the bug loudly.
            if sys.exc_info()[1] is not None:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise RuntimeError(
                    "write_tx() body swallowed an exception — refusing to COMMIT "
                    "a potentially-partial transaction. Re-raise inside the with-block "
                    "or move your try/except OUTSIDE the write_tx() scope."
                ) from sys.exc_info()[1]
            c.execute("COMMIT")
        except BaseException:
            # ROLLBACK on any failure — including KeyboardInterrupt.
            # This prevents partially-committed transactions if the user
            # Ctrl+C's mid-sale.
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # connection may already be in a bad state
            raise
    finally:
        c.close()


@contextmanager
def read_tx():
    """Read-only connection that is properly closed on exit.

    The existing conn() returns a connection that, when used with
    `with conn() as c:`, commits/rolls back on exit but does NOT close
    the connection. This can leak connections in long-running processes.

    read_tx() closes the connection in finally, guaranteeing no leaks.

    Usage:
        with db.read_tx() as c:
            rows = c.execute("SELECT * FROM bills").fetchall()
        # Connection is closed here
    """
    c = conn()
    try:
        yield c
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  address TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS bills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
  supplier_name TEXT,
  phone TEXT,
  bill_date TEXT,
  bill_no TEXT,
  written_total REAL,
  computed_total REAL,
  unit TEXT DEFAULT 'pcs',
  payment_status TEXT DEFAULT 'paid',      -- 'paid' | 'credit'
  credit_due_date TEXT,                     -- when credit should be settled
  status TEXT DEFAULT 'review',             -- 'review' | 'confirmed'
  flags TEXT DEFAULT '[]',
  extraction TEXT,
  provider TEXT,
  deleted_at TEXT DEFAULT NULL,             -- soft-delete timestamp (NULL = active)
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS bill_pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  page_no INTEGER
);

CREATE TABLE IF NOT EXISTS bill_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
  raw TEXT,
  item_code TEXT,
  price REAL,           -- unit cost
  qty REAL,
  unit TEXT,
  line_total REAL,
  category_id INTEGER REFERENCES price_categories(id) ON DELETE SET NULL,
  confidence REAL,
  corrected INTEGER DEFAULT 0,
  page_no INTEGER       -- which bill page this item was extracted from (1-indexed)
);

CREATE TABLE IF NOT EXISTS price_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  code TEXT,                          -- user-defined short code (e.g. A, B, C, D, or 250, 500, etc.)
  sell_price REAL NOT NULL,
  color TEXT DEFAULT '#10b981',
  sort_order INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id INTEGER,
  field TEXT,
  before TEXT,
  after TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS ai_providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  provider_type TEXT NOT NULL,   -- 'gemini' | 'groq' | 'openrouter'
  api_key TEXT NOT NULL,
  model TEXT,
  priority INTEGER DEFAULT 0,
  enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  expires_at TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,          -- 'bill_created' | 'bill_confirmed' | 'bill_deleted' | 'bill_restored' | 'bill_edited' | 'supplier_created' | 'supplier_edited' | 'supplier_deleted' | 'backup_created' | 'category_changed'
  entity_type TEXT,                  -- 'bill' | 'supplier' | 'backup' | 'category'
  entity_id INTEGER,
  description TEXT,                  -- human-readable summary
  metadata TEXT DEFAULT '{}',        -- JSON blob with extra context
  created_at TEXT DEFAULT (datetime('now','localtime')),
  -- H13 fix (v8.13.4): actor columns so we can answer "who did this?" for
  -- refunds, voids, owner withdrawals, etc. Nullable for backward compat
  -- with rows written before the migration.
  actor_employee_id INTEGER,         -- employees.id of the actor (NULL for system)
  actor_session TEXT,                -- sessions.token prefix (first 8 chars) for traceability
  actor_ip TEXT                      -- client IP at the time of the action
);

CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_activity_entity ON activity_log(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS trend_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,              -- e.g. "squid game toys"
  trend_type TEXT NOT NULL,           -- 'rising' | 'peaking' | 'declining' | 'opportunity'
  trend_score INTEGER,                -- relative interest 0-100
  change_pct REAL,                    -- % change vs previous period
  suggestion TEXT,                    -- AI-generated suggestion
  reasoning TEXT,                     -- why this is relevant to this shop
  category_match TEXT,                -- which of the shop's categories this relates to
  status TEXT DEFAULT 'new',          -- 'new' | 'acted_on' | 'dismissed'
  source TEXT DEFAULT 'google_trends',
  fetched_at TEXT DEFAULT (datetime('now','localtime')),
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_trends_status ON trend_alerts(status);
CREATE INDEX IF NOT EXISTS idx_trends_fetched ON trend_alerts(fetched_at);

CREATE TABLE IF NOT EXISTS reorder_reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_name TEXT NOT NULL,
  supplier_name TEXT,
  avg_gap_days INTEGER,              -- average days between purchases
  last_purchased TEXT,               -- date of last purchase
  days_since INTEGER,                -- days since last purchase
  suggested_quantity INTEGER,        -- based on past purchasing pattern
  priority TEXT DEFAULT 'medium',    -- 'high' | 'medium' | 'low'
  status TEXT DEFAULT 'new',         -- 'new' | 'ordered' | 'dismissed'
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_reorder_status ON reorder_reminders(status);

CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_no TEXT,
  customer_name TEXT DEFAULT '',
  customer_phone TEXT DEFAULT '',
  customer_id INTEGER,
  subtotal REAL NOT NULL DEFAULT 0,
  discount REAL DEFAULT 0,
  loyalty_points_used INTEGER DEFAULT 0,
  loyalty_discount REAL DEFAULT 0,
  tax_rate REAL DEFAULT 0,
  tax_amount REAL DEFAULT 0,
  total REAL NOT NULL DEFAULT 0,
  payment_method TEXT DEFAULT 'cash',
  payment_status TEXT DEFAULT 'paid',
  split_cash REAL DEFAULT 0,
  split_card REAL DEFAULT 0,
  split_online REAL DEFAULT 0,
  employee_id INTEGER,
  shift_id INTEGER,
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);

CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  item_name TEXT NOT NULL,
  category_id INTEGER,
  category_code TEXT,
  cost_price REAL DEFAULT 0,
  sell_price REAL NOT NULL,
  qty INTEGER NOT NULL DEFAULT 1,
  line_total REAL NOT NULL,
  bill_item_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_cat ON sale_items(category_id);

CREATE TABLE IF NOT EXISTS payment_methods (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'cash',
  icon TEXT DEFAULT '',
  active INTEGER DEFAULT 1,
  sort_order INTEGER DEFAULT 0
);

-- v8.4: Custom items (non-category items like bags, accessories, etc.)
CREATE TABLE IF NOT EXISTS custom_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  code TEXT,                          -- optional SKU/barcode
  sell_price REAL NOT NULL,
  cost_price REAL DEFAULT 0,
  category TEXT DEFAULT 'Miscellaneous',  -- grouping label (e.g. "Bags", "Accessories")
  color TEXT DEFAULT '#64748B',
  is_active INTEGER DEFAULT 1,
  sort_order INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- v8.4: Item-level discounts (e.g. 100% off on bags, 10% off on Category A)
CREATE TABLE IF NOT EXISTS item_discounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  applies_to TEXT NOT NULL DEFAULT 'all',  -- 'all', 'category', 'custom_item'
  category_id INTEGER,                     -- when applies_to='category'
  custom_item_id INTEGER,                  -- when applies_to='custom_item'
  discount_type TEXT NOT NULL DEFAULT 'percent',  -- 'percent' or 'amount'
  discount_value REAL NOT NULL DEFAULT 0,
  reason TEXT,                             -- why the discount was set (audit trail)
  is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  address TEXT DEFAULT '',
  loyalty_points INTEGER DEFAULT 0,
  total_spent REAL DEFAULT 0,
  total_credit REAL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);

CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  description TEXT,
  amount REAL NOT NULL,
  payment_method TEXT DEFAULT 'cash',
  date TEXT DEFAULT (datetime('now','localtime')),
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);

CREATE TABLE IF NOT EXISTS cash_drawer (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  amount REAL NOT NULL,
  description TEXT,
  reference_id INTEGER,
  reference_type TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_cash_drawer_created ON cash_drawer(created_at);

CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT,
  role TEXT DEFAULT 'cashier',
  active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER REFERENCES employees(id),
  start_time TEXT DEFAULT (datetime('now','localtime')),
  end_time TEXT,
  opening_cash REAL DEFAULT 0,
  closing_cash REAL,
  status TEXT DEFAULT 'open'
);

CREATE INDEX IF NOT EXISTS idx_shifts_status ON shifts(status);

-- Held (parked) POS orders — saved mid-sale to recall later
CREATE TABLE IF NOT EXISTS held_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reference TEXT,                       -- short ref like HOLD-001
  customer_name TEXT DEFAULT '',
  customer_phone TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  items_json TEXT NOT NULL DEFAULT '[]', -- [{category_id,category_code,sell_price,qty,item_name}]
  discount REAL DEFAULT 0,
  discount_type TEXT DEFAULT 'amount',
  total REAL DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_held_orders_created ON held_orders(created_at);

-- Quotations — saved carts a customer can review & convert to a sale later
CREATE TABLE IF NOT EXISTS quotations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote_no TEXT,
  customer_name TEXT DEFAULT '',
  customer_phone TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  items_json TEXT NOT NULL DEFAULT '[]',
  discount REAL DEFAULT 0,
  discount_type TEXT DEFAULT 'amount',
  total REAL DEFAULT 0,
  status TEXT DEFAULT 'open',           -- 'open' | 'converted' | 'expired'
  valid_until TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_quotations_status ON quotations(status);

-- Customer payments — settle outstanding credit
CREATE TABLE IF NOT EXISTS customer_payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  customer_name TEXT,
  amount REAL NOT NULL,
  payment_method TEXT DEFAULT 'cash',
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_customer_payments_customer ON customer_payments(customer_id);

-- Loyalty redemptions — track point usage
CREATE TABLE IF NOT EXISTS loyalty_redemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
  points_used INTEGER NOT NULL,
  rupee_value REAL NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Purchase orders — generated from reorder reminders or manual entry
CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  po_no TEXT,
  supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
  supplier_name TEXT,
  status TEXT DEFAULT 'draft',           -- 'draft' | 'sent' | 'received' | 'cancelled'
  total REAL DEFAULT 0,
  notes TEXT,
  expected_date TEXT,
  sent_via TEXT,                          -- 'whatsapp' | 'sms' | 'email' | 'manual'
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);

CREATE TABLE IF NOT EXISTS purchase_order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  po_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  item_name TEXT,
  qty INTEGER NOT NULL DEFAULT 1,
  est_price REAL DEFAULT 0,
  line_total REAL DEFAULT 0,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_po_items_po ON purchase_order_items(po_id);

-- External POS imports — daily backup uploads from shop's existing POS system
CREATE TABLE IF NOT EXISTS pos_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT,                       -- name of the original POS (e.g. "Incline POS", "Excel export")
  filename TEXT,
  file_format TEXT,                       -- 'csv' | 'xlsx' | 'json'
  row_count INTEGER DEFAULT 0,
  sale_count INTEGER DEFAULT 0,
  total_revenue REAL DEFAULT 0,
  date_range_start TEXT,
  date_range_end TEXT,
  column_mapping TEXT,                    -- JSON: {invoice_no: "Invoice #", date: "Date", ...}
  import_date TEXT,                       -- the date the backup data is FOR (not when uploaded)
  status TEXT DEFAULT 'imported',         -- 'imported' | 'partial' | 'failed'
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pos_imports_date ON pos_imports(import_date);

-- Sales targets — daily/monthly goals for tracking performance
CREATE TABLE IF NOT EXISTS sales_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL,                   -- 'daily' | 'monthly'
  target_date TEXT,                       -- YYYY-MM-DD for daily, YYYY-MM for monthly
  target_amount REAL NOT NULL,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sales_targets_period ON sales_targets(period, target_date);

-- Stock adjustments (manual +/- with reason)
CREATE TABLE IF NOT EXISTS stock_adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER REFERENCES price_categories(id) ON DELETE SET NULL,
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_stock_adjustments_cat ON stock_adjustments(category_id);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bills_supplier ON bills(supplier_id);",
    "CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(status);",
    "CREATE INDEX IF NOT EXISTS idx_bills_payment ON bills(payment_status);",
    "CREATE INDEX IF NOT EXISTS idx_bills_date ON bills(bill_date);",
    "CREATE INDEX IF NOT EXISTS idx_bills_created ON bills(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);",
    "CREATE INDEX IF NOT EXISTS idx_bill_items_cat ON bill_items(category_id);",
    "CREATE INDEX IF NOT EXISTS idx_bill_items_cat_bill ON bill_items(category_id, bill_id);",  # v8.13.1: composite for JOIN
    "CREATE INDEX IF NOT EXISTS idx_bill_pages_bill ON bill_pages(bill_id);",
    "CREATE INDEX IF NOT EXISTS idx_suppliers_phone ON suppliers(phone);",
    "CREATE INDEX IF NOT EXISTS idx_corrections_bill ON corrections(bill_id);",
    # Phase 2: new indexes for performance
    "CREATE INDEX IF NOT EXISTS idx_sales_customer_id ON sales(customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_sales_shift_id ON sales(shift_id);",
    "CREATE INDEX IF NOT EXISTS idx_sales_client_uuid ON sales(client_uuid);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_cash_drawer_shift_id ON cash_drawer(shift_id);",
    "CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_sales_payment_status ON sales(payment_status);",
    "CREATE INDEX IF NOT EXISTS idx_sales_payment_method ON sales(payment_method);",
    "CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);",
    "CREATE INDEX IF NOT EXISTS idx_sale_items_cat ON sale_items(category_id);",
    "CREATE INDEX IF NOT EXISTS idx_sale_items_sale_cat ON sale_items(sale_id, category_id);",  # v8.13.1: JOIN speedup
    "CREATE INDEX IF NOT EXISTS idx_sale_items_cat_sale ON sale_items(category_id, sale_id);",  # v8.13.1: WHERE + JOIN
    "CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);",
    "CREATE INDEX IF NOT EXISTS idx_stock_adj_cat ON stock_adjustments(category_id);",
    "CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier ON purchase_orders(supplier_id);",
    "CREATE INDEX IF NOT EXISTS idx_purchase_orders_status ON purchase_orders(status);",
    "CREATE INDEX IF NOT EXISTS idx_customer_payments_cid ON customer_payments(customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);",
    "CREATE INDEX IF NOT EXISTS idx_cash_drawer_date ON cash_drawer(date(created_at));",
    # v8.9.1: indexes for deleted-data filtering
    "CREATE INDEX IF NOT EXISTS idx_bills_deleted_at ON bills(deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_bills_status_deleted ON bills(status, deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_activity_log_entity ON activity_log(entity_type, entity_id);",
    # v3.1.1: Idempotency — UNIQUE on client_uuid
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_client_uuid ON sales(client_uuid) WHERE client_uuid IS NOT NULL;",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_payments_uuid ON customer_payments(notes) WHERE notes LIKE '%uuid:%';",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_adj_uuid ON stock_adjustments(reason) WHERE reason LIKE '%uuid:%';",
    # NOTE: customers/suppliers deleted_at indexes are created AFTER the
    # ALTER TABLE migrations (below) because the deleted_at column may not
    # exist yet when this INDEXES list runs. See POST_MIGRATION_INDEXES.
    # v8.13.1: Missing created_at indexes on monthly-filtered tables
    "CREATE INDEX IF NOT EXISTS idx_stock_adjustments_created ON stock_adjustments(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_owner_withdrawals_created ON owner_withdrawals(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_loyalty_redemptions_sale ON loyalty_redemptions(sale_id);",
    "CREATE INDEX IF NOT EXISTS idx_loyalty_redemptions_customer ON loyalty_redemptions(customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_commissions_emp_date ON commissions(employee_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_commissions_sale_reversed ON commissions(sale_id, reversed);",
    "CREATE INDEX IF NOT EXISTS idx_cash_drawer_type ON cash_drawer(type);",
    "CREATE INDEX IF NOT EXISTS idx_cash_drawer_ref ON cash_drawer(reference_type, reference_id);",
    "CREATE INDEX IF NOT EXISTS idx_expenses_type_date ON expenses(expense_type, date);",
    "CREATE INDEX IF NOT EXISTS idx_activity_log_event_created ON activity_log(event_type, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_transfer_challan_items_challan ON transfer_challan_items(challan_id);",
    "CREATE INDEX IF NOT EXISTS idx_central_purchase_items_purchase ON central_purchase_items(purchase_id);",
    "CREATE INDEX IF NOT EXISTS idx_bill_intelligence_cat ON bill_intelligence(category_id);",
    "CREATE INDEX IF NOT EXISTS idx_bills_supplier_deleted ON bills(supplier_id, deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_bills_payment_due ON bills(payment_status, credit_due_date) WHERE deleted_at IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_expenses_category_id ON expenses(category_id);",
]

# v8.13.1: These indexes reference columns that are added by ALTER TABLE
# migrations (deleted_at on customers/suppliers). They must be created AFTER
# the migrations run, not in the INDEXES list above (which runs before migrations).
POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_customers_deleted_at ON customers(deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_customers_active_spent ON customers(deleted_at, total_spent);",
    "CREATE INDEX IF NOT EXISTS idx_customers_active_credit ON customers(deleted_at) WHERE total_credit > 0;",
    "CREATE INDEX IF NOT EXISTS idx_suppliers_deleted_at ON suppliers(deleted_at);",
    "CREATE INDEX IF NOT EXISTS idx_suppliers_active_name ON suppliers(deleted_at, name);",
]

# v8.13.1: Materialized summary table — keeps O(1) cash drawer total + capital
# + withdrawals totals. Refreshed by trigger on cash_drawer / owner_withdrawals /
# capital_injections inserts. Eliminates the full-table-scan SUM(*) that runs on
# every dashboard load (was the slowest query in the app at >1M cash_drawer rows).
# Single-row table (id=1 enforced by CHECK).
SUMMARY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cash_summary (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash_in_drawer REAL DEFAULT 0,
  owner_withdrawals_all_time REAL DEFAULT 0,
  capital_injections_all_time REAL DEFAULT 0,
  customers_outstanding_credit REAL DEFAULT 0,
  updated_at TEXT
);
"""

SUMMARY_TRIGGERS_SQL = [
    # cash_drawer → cash_summary.cash_in_drawer (incremental)
    """CREATE TRIGGER IF NOT EXISTS trg_cash_drawer_balance_insert
    AFTER INSERT ON cash_drawer
    BEGIN
      INSERT INTO cash_summary(id, cash_in_drawer, updated_at)
      VALUES (1, NEW.amount, datetime('now','localtime'))
      ON CONFLICT(id) DO UPDATE SET
        cash_in_drawer = cash_in_drawer + NEW.amount,
        updated_at = datetime('now','localtime');
    END;""",
]


def init():
    # v8.5.4: Set WAL mode ONCE here (persistent setting — survives restarts).
    # Previously conn() ran this on every connection (slow — writes to DB header each time).
    with sqlite3.connect(str(DB_PATH), timeout=10) as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")  # faster than FULL, still safe with WAL
    with conn() as c:
        c.executescript(SCHEMA)
        # Migrations: add columns if missing (for existing databases)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(bills)").fetchall()}
        if "deleted_at" not in cols:
            c.execute("ALTER TABLE bills ADD COLUMN deleted_at TEXT DEFAULT NULL")
        item_cols = {r["name"] for r in c.execute("PRAGMA table_info(bill_items)").fetchall()}
        if "page_no" not in item_cols:
            c.execute("ALTER TABLE bill_items ADD COLUMN page_no INTEGER")
        # v8.5.4: store sell_price from AI extraction so the bill editor
        # can auto-assign the category. The AI creates multiple rows directly
        # when it detects multiple categories on a single bill row — no
        # categories_json field needed.
        if "sell_price" not in item_cols:
            c.execute("ALTER TABLE bill_items ADD COLUMN sell_price REAL")
        # Migration: add loyalty_redeemed & discount_reason to sales (for existing DBs)
        sales_cols = {r["name"] for r in c.execute("PRAGMA table_info(sales)").fetchall()}
        if "loyalty_points_used" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN loyalty_points_used INTEGER DEFAULT 0")
        if "loyalty_discount" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN loyalty_discount REAL DEFAULT 0")
        if "employee_id" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN employee_id INTEGER")
        if "shift_id" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN shift_id INTEGER")
        if "customer_id" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN customer_id INTEGER")
        if "split_cash" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN split_cash REAL DEFAULT 0")
        if "split_card" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN split_card REAL DEFAULT 0")
        if "split_online" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN split_online REAL DEFAULT 0")
        if "tax_rate" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN tax_rate REAL DEFAULT 0")
        if "tax_amount" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN tax_amount REAL DEFAULT 0")
        # Migration: add loyalty_points_redeemed_total to customers
        cust_cols = {r["name"] for r in c.execute("PRAGMA table_info(customers)").fetchall()}
        if "loyalty_redeemed" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN loyalty_redeemed INTEGER DEFAULT 0")
        # Phase 2 migrations: RBAC + stock guard + idempotent sales + shift linking
        # sessions.role (for RBAC)
        sess_cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
        if "role" not in sess_cols:
            c.execute("ALTER TABLE sessions ADD COLUMN role TEXT DEFAULT 'manager'")
        if "employee_id" not in sess_cols:
            c.execute("ALTER TABLE sessions ADD COLUMN employee_id INTEGER")
        # employees.pin (for staff login)
        emp_cols = {r["name"] for r in c.execute("PRAGMA table_info(employees)").fetchall()}
        if "pin" not in emp_cols:
            c.execute("ALTER TABLE employees ADD COLUMN pin TEXT")
        # PR 7a: employees.pin_hash — bcrypt hash of the PIN (replaces plaintext pin).
        # The pin column is kept for backward-compat during migration; new writes
        # go to pin_hash. verify_manager_pin checks pin_hash first, falls back to
        # plaintext pin with a warning log.
        emp_cols = {r["name"] for r in c.execute("PRAGMA table_info(employees)").fetchall()}
        if "pin_hash" not in emp_cols:
            c.execute("ALTER TABLE employees ADD COLUMN pin_hash TEXT")
        # cash_drawer.shift_id (link drawer entries to shifts)
        cd_cols = {r["name"] for r in c.execute("PRAGMA table_info(cash_drawer)").fetchall()}
        if "shift_id" not in cd_cols:
            c.execute("ALTER TABLE cash_drawer ADD COLUMN shift_id INTEGER")
        # sales.client_uuid (idempotent sales)
        if "client_uuid" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN client_uuid TEXT")
        # v8.13.1: Indexes are created AFTER all migrations (below) because
        # many indexes reference tables/columns created by migrations
        # (owner_withdrawals, capital_injections, stock_writeoffs, etc.).
        # Previously the INDEXES list ran here, before the migrations, causing
        # "no such table" errors on fresh DBs.
        # Seed default price categories if none exist
        existing = c.execute("SELECT COUNT(*) n FROM price_categories").fetchone()["n"]
        if existing == 0:
            defaults = [
                ("Budget", "A", 250, "#5E6AD2", 1),     # Linear Indigo
                ("Standard", "B", 500, "#00BA7C", 2),    # Linear Green
                ("Premium", "C", 750, "#F91880", 3),     # Linear Pink
                ("Luxury", "D", 1000, "#FFC107", 4),     # Linear Amber
            ]
            for name, code, price, color, order in defaults:
                c.execute(
                    "INSERT INTO price_categories(name, code, sell_price, color, sort_order) VALUES(?,?,?,?,?)",
                    (name, code, price, color, order),
                )
        # Migration: backfill 'code' column for existing categories that don't have one
        pc_cols = {r["name"] for r in c.execute("PRAGMA table_info(price_categories)").fetchall()}
        if "code" not in pc_cols:
            c.execute("ALTER TABLE price_categories ADD COLUMN code TEXT")
        # v8.4: Auto-fill code for any rows where it's NULL or empty — runs every boot
        # to ensure categories always have a displayable code (was causing "—" in stock table)
        rows_missing_code = c.execute(
            "SELECT id, sell_price, name FROM price_categories WHERE code IS NULL OR code=''"
        ).fetchall()
        fallback_map = {"250": "A", "500": "B", "750": "C", "1000": "D"}
        for r in rows_missing_code:
            # Try fallback map first, then first letter of name, then price
            price_str = str(int(r["sell_price"])) if r["sell_price"] else "0"
            fb = fallback_map.get(price_str)
            if not fb and r["name"]:
                fb = r["name"][0].upper()
            if not fb:
                fb = price_str
            c.execute("UPDATE price_categories SET code=? WHERE id=?", (fb, r["id"]))
        # v8.4: Migrate old Tailwind colors to Linear palette
        color_migration = {
            "#3b82f6": "#5E6AD2",  # blue → Linear Indigo
            "#10b981": "#00BA7C",  # green → Linear Green
            "#f59e0b": "#FFC107",  # amber → Linear Amber
            "#ef4444": "#F91880",  # red → Linear Pink
        }
        for old_color, new_color in color_migration.items():
            c.execute("UPDATE price_categories SET color=? WHERE color=?", (new_color, old_color))
        # Seed default payment methods if none exist
        pm_existing = c.execute("SELECT COUNT(*) n FROM payment_methods").fetchone()["n"]
        if pm_existing == 0:
            pm_defaults = [
                ("Cash", "cash", "", 1),
                ("Card", "card", "", 2),
                ("Online", "online", "", 3),
                ("Credit (Urdhaar)", "credit", "", 4),
            ]
            for name, ptype, icon, order in pm_defaults:
                c.execute(
                    "INSERT INTO payment_methods(name, type, icon, sort_order) VALUES(?,?,?,?)",
                    (name, ptype, icon, order),
                )
        # Phase 2: Purge expired sessions on startup
        c.execute("DELETE FROM sessions WHERE expires_at < datetime('now','localtime')")

        # v8.4: Fix imported POS sales timestamps — Ezi POS import created timestamps like
        # '2026-08-13 103000' (time without colons) which SQLite date() can't parse.
        # This migration converts them to '2026-08-13 10:30:00' format.
        # Runs on every boot — only fixes rows that match the broken pattern.
        c.execute(
            "UPDATE sales SET created_at = "
            "substr(created_at, 1, 11) || "
            "substr(created_at, 12, 2) || ':' || substr(created_at, 14, 2) || ':' || substr(created_at, 16, 2) "
            "WHERE created_at LIKE '____-__-__ ______' "
            "AND substr(created_at, 14, 1) != ':' "
            "AND length(created_at) = 17"
        )

        # v8.4: Fix imported POS sales that were incorrectly marked as 'credit'.
        c.execute(
            "UPDATE sales SET payment_status='paid' "
            "WHERE invoice_no LIKE 'POS-%' AND payment_status='credit'"
        )

        # v8.5: Backfill subtotal for imported POS sales (was defaulting to 0)
        c.execute(
            "UPDATE sales SET subtotal=total WHERE invoice_no LIKE 'POS-%' AND subtotal=0 AND total>0"
        )

        # v8.5: Backfill sale_items for imported POS sales that have no sale_items.
        # Creates a summary row so COGS/margin/category reports include them.
        c.execute(
            "INSERT INTO sale_items(sale_id, item_name, category_code, cost_price, sell_price, qty, line_total) "
            "SELECT s.id, 'Imported POS Sale', '', 0, s.total, 1, s.total "
            "FROM sales s "
            "WHERE s.invoice_no LIKE 'POS-%' "
            "AND s.payment_status IN ('paid', 'credit', 'partial') "
            "AND NOT EXISTS (SELECT 1 FROM sale_items si WHERE si.sale_id = s.id)"
        )

        # v3.0 Migration: add raast_reference column to sales
        sales_cols = {r["name"] for r in c.execute("PRAGMA table_info(sales)").fetchall()}
        if "raast_reference" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN raast_reference TEXT DEFAULT NULL")
        # v8.8.0: Payment submethod (easypaisa/jazzcash/raast_qr/bank_transfer)
        sales_cols_v88 = {r["name"] for r in c.execute("PRAGMA table_info(sales)").fetchall()}
        if "payment_submethod" not in sales_cols_v88:
            c.execute("ALTER TABLE sales ADD COLUMN payment_submethod TEXT DEFAULT NULL")
        # v8.9.1: Admin void support + manual override flag for POS import sync
        if "refund_reason" not in sales_cols_v88:
            c.execute("ALTER TABLE sales ADD COLUMN refund_reason TEXT DEFAULT NULL")
        if "manually_overridden" not in sales_cols_v88:
            c.execute("ALTER TABLE sales ADD COLUMN manually_overridden INTEGER DEFAULT 0")

        # v3.0 Phase 3: Wholesale money features
        # Price tiers on customers
        cust_cols = {r["name"] for r in c.execute("PRAGMA table_info(customers)").fetchall()}
        if "price_tier" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN price_tier TEXT DEFAULT 'retail'")
        if "credit_limit" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN credit_limit REAL DEFAULT 0")
        if "terms_days" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN terms_days INTEGER DEFAULT 0")
        if "wallet_balance" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN wallet_balance REAL DEFAULT 0")
        if "referred_by" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN referred_by INTEGER DEFAULT NULL")
        if "birthday" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN birthday TEXT DEFAULT NULL")
        # v8.10: Customer soft-delete
        if "deleted_at" not in cust_cols:
            c.execute("ALTER TABLE customers ADD COLUMN deleted_at TEXT DEFAULT NULL")

        # Wholesale/VIP prices on price_categories
        pc_cols = {r["name"] for r in c.execute("PRAGMA table_info(price_categories)").fetchall()}
        if "sell_wholesale" not in pc_cols:
            c.execute("ALTER TABLE price_categories ADD COLUMN sell_wholesale REAL DEFAULT 0")
        if "sell_vip" not in pc_cols:
            c.execute("ALTER TABLE price_categories ADD COLUMN sell_vip REAL DEFAULT 0")
        if "carton_size" not in pc_cols:
            c.execute("ALTER TABLE price_categories ADD COLUMN carton_size INTEGER DEFAULT 12")

        # Supplier terms
        sup_cols = {r["name"] for r in c.execute("PRAGMA table_info(suppliers)").fetchall()}
        if "terms_days" not in sup_cols:
            c.execute("ALTER TABLE suppliers ADD COLUMN terms_days INTEGER DEFAULT 0")
        if "credit_limit" not in sup_cols:
            c.execute("ALTER TABLE suppliers ADD COLUMN credit_limit REAL DEFAULT 0")
        # v8.9.1: Supplier soft-delete
        if "deleted_at" not in sup_cols:
            c.execute("ALTER TABLE suppliers ADD COLUMN deleted_at TEXT DEFAULT NULL")

        # Bills: auto credit_due_date based on supplier terms
        bill_cols = {r["name"] for r in c.execute("PRAGMA table_info(bills)").fetchall()}
        if "terms_days" not in bill_cols:
            c.execute("ALTER TABLE bills ADD COLUMN terms_days INTEGER DEFAULT 0")

        # v3.0 Phase 4: Inventory Intelligence
        bi_cols = {r["name"] for r in c.execute("PRAGMA table_info(bill_items)").fetchall()}
        if "expiry_date" not in bi_cols:
            c.execute("ALTER TABLE bill_items ADD COLUMN expiry_date TEXT DEFAULT NULL")
        if "lot_no" not in bi_cols:
            c.execute("ALTER TABLE bill_items ADD COLUMN lot_no TEXT DEFAULT NULL")
        c.execute("""CREATE TABLE IF NOT EXISTS stock_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            count_date TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # v3.0 Phase 5: POS features
        # Sales: layaway support
        if "layaway_due_date" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN layaway_due_date TEXT DEFAULT NULL")
        if "layaway_balance" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN layaway_balance REAL DEFAULT 0")
        # PR 4: Sales.refunded_at — timestamp for atomic refunds (set when
        # payment_status is updated to 'refunded'). Used for refund reporting.
        if "refunded_at" not in sales_cols:
            c.execute("ALTER TABLE sales ADD COLUMN refunded_at TEXT DEFAULT NULL")
        # PR 5: bills.version — Optimistic Concurrency Control (OCC) for
        # confirm_bill(). Incremented on every confirm/re-confirm. If two
        # requests try to confirm concurrently, the second sees version
        # mismatch and returns 409 (instead of corrupting stock_state).
        bills_cols = {r["name"] for r in c.execute("PRAGMA table_info(bills)").fetchall()}
        if "version" not in bills_cols:
            c.execute("ALTER TABLE bills ADD COLUMN version INTEGER DEFAULT 1")
        # Customers: wallet already added in Phase 3

        # Upsell tracking
        c.execute("CREATE TABLE IF NOT EXISTS upsell_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER, suggested_category_id INTEGER, accepted INTEGER DEFAULT 0, sale_id INTEGER, created_at TEXT DEFAULT (datetime('now','localtime')))")

        # v3.1 Phase G4: invoice_seq counter for gapless invoice numbers
        c.execute("""CREATE TABLE IF NOT EXISTS invoice_seq (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            next_invoice INTEGER DEFAULT 1
        )""")
        # Initialize if empty
        row = c.execute("SELECT next_invoice FROM invoice_seq WHERE id=1").fetchone()
        if not row:
            # Backfill: find max invoice number and start from there
            max_inv = c.execute("SELECT COUNT(*) AS n FROM sales").fetchone()["n"]
            c.execute("INSERT INTO invoice_seq(id, next_invoice) VALUES(1, ?)", (max_inv + 1,))

        c.execute("""CREATE TABLE IF NOT EXISTS stock_count_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_count_id INTEGER NOT NULL,
            category_id INTEGER,
            book_qty INTEGER DEFAULT 0,
            counted_qty INTEGER DEFAULT 0,
            variance INTEGER DEFAULT 0,
            reason TEXT DEFAULT ''
        )""")

        # ─── v4.0 Phase 2: Expense Management Module ────────────────────────
        # Categorised, recurring, budgeted expenses.
        c.execute("""CREATE TABLE IF NOT EXISTS expense_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_fixed INTEGER DEFAULT 0,
            budget_monthly REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS recurring_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER REFERENCES expense_categories(id) ON DELETE SET NULL,
            description TEXT DEFAULT '',
            amount REAL NOT NULL,
            payment_method TEXT DEFAULT 'cash',
            day_of_month INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            last_generated TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Add columns to existing expenses table (additions only — no rename/drop)
        exp_cols = {r["name"] for r in c.execute("PRAGMA table_info(expenses)").fetchall()}
        if "category_id" not in exp_cols:
            c.execute("ALTER TABLE expenses ADD COLUMN category_id INTEGER REFERENCES expense_categories(id) ON DELETE SET NULL")
        if "expense_type" not in exp_cols:
            c.execute("ALTER TABLE expenses ADD COLUMN expense_type TEXT DEFAULT 'operating'")
        if "recurring_id" not in exp_cols:
            c.execute("ALTER TABLE expenses ADD COLUMN recurring_id INTEGER REFERENCES recurring_expenses(id) ON DELETE SET NULL")
        # Seed default expense categories if none exist (first-run only)
        existing_cats = c.execute("SELECT COUNT(*) n FROM expense_categories").fetchone()["n"]
        if existing_cats == 0:
            defaults = [
                ("Rent", 1, 0, 1),
                ("Salaries", 1, 0, 2),
                ("Electricity", 0, 0, 3),
                ("Transport", 0, 0, 4),
                ("Internet", 0, 0, 5),
                ("Maintenance", 0, 0, 6),
                ("Marketing", 0, 0, 7),
                ("Other", 0, 0, 8),
            ]
            for name, is_fixed, budget, sort_order in defaults:
                c.execute(
                    "INSERT INTO expense_categories(name, is_fixed, budget_monthly, active, sort_order) "
                    "VALUES(?,?,?,?,?)",
                    (name, is_fixed, budget, 1, sort_order),
                )

        # ─── v4.0 Phase 4: Cash & Theft Controls ──────────────────────────
        # Shifts: denomination breakdown + variance + blind-close support
        shift_cols = {r["name"] for r in c.execute("PRAGMA table_info(shifts)").fetchall()}
        if "denominations" not in shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN denominations TEXT DEFAULT NULL")
        if "counted_cash" not in shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN counted_cash REAL DEFAULT NULL")
        if "variance" not in shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN variance REAL DEFAULT 0")
        if "blind_close" not in shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN blind_close INTEGER DEFAULT 0")
        # Default settings (only set if not already in DB)
        def _set_default_setting(key, value):
            existing = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO settings(key, value) VALUES(?,?)", (key, str(value)))
        _set_default_setting("max_discount_pct_without_pin", "10")
        _set_default_setting("require_pin_for_refund", "true")
        _set_default_setting("require_pin_for_price_override", "true")
        _set_default_setting("blind_close_enabled", "false")

        # ─── v4.0 Phase 5: Wholesale Money Flows ──────────────────────────
        # Supplier advances (peshgi): pre-payments to suppliers, applied to bills on confirm.
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_advances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            payment_method TEXT DEFAULT 'cash',
            notes TEXT DEFAULT '',
            applied_to_bill_id INTEGER DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Agreed rate list per supplier per item
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER REFERENCES suppliers(id) ON DELETE CASCADE,
            item_name TEXT NOT NULL,
            agreed_price REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Bank accounts + transactions ledger
        c.execute("""CREATE TABLE IF NOT EXISTS bank_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            opening_balance REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER REFERENCES bank_accounts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,           -- 'deposit' | 'withdrawal' | 'supplier_payment'
            amount REAL NOT NULL,         -- positive for deposit, negative for withdrawal/supplier_payment
            description TEXT DEFAULT '',
            reference TEXT DEFAULT '',
            date TEXT DEFAULT (datetime('now','localtime')),
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_advances_supplier ON supplier_advances(supplier_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_rates_supplier ON supplier_rates(supplier_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bank_tx_account ON bank_transactions(account_id)")

        # ─── v4.0 Phase 6: Commissions ────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS commission_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER DEFAULT NULL,   -- NULL = applies to all employees with the given role
            role TEXT DEFAULT 'cashier',        -- 'cashier' | 'manager' | 'admin'
            type TEXT DEFAULT 'percent',        -- 'percent' | 'flat'
            value REAL NOT NULL,                -- percent of sale total, or flat Rs per sale
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            employee_id INTEGER,
            amount REAL NOT NULL,
            rule_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_commissions_employee ON commissions(employee_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_commissions_sale ON commissions(sale_id)")
        # PR 4: commissions.reversed + reversed_at — used by refund_sale() to
        # mark a commission as reversed when the sale is refunded. Soft-delete
        # pattern (not hard DELETE) so historical commission reports remain
        # auditable.
        comm_cols = {r["name"] for r in c.execute("PRAGMA table_info(commissions)").fetchall()}
        if "reversed" not in comm_cols:
            c.execute("ALTER TABLE commissions ADD COLUMN reversed INTEGER DEFAULT 0")
        if "reversed_at" not in comm_cols:
            c.execute("ALTER TABLE commissions ADD COLUMN reversed_at TEXT")
        # PR 4: loyalty_redemptions.reversed_at — used by refund_sale() to
        # soft-delete a redemption when the sale is refunded (so the points
        # are restored to the customer but the audit trail remains).
        lr_cols = {r["name"] for r in c.execute("PRAGMA table_info(loyalty_redemptions)").fetchall()}
        if "reversed_at" not in lr_cols:
            c.execute("ALTER TABLE loyalty_redemptions ADD COLUMN reversed_at TEXT")

        # ─── v5.0 Phase 1: Running Weighted Average Cost engine ───────────
        # Materialized per-category running state. Source of truth = rebuild_stock_state()
        # which replays bills + sales chronologically. Mutated incrementally by
        # apply_purchase_to_state / apply_sale_to_state on the respective code paths.
        c.execute("""CREATE TABLE IF NOT EXISTS category_stock_state (
            category_id INTEGER PRIMARY KEY,
            current_qty REAL DEFAULT 0,
            current_value REAL DEFAULT 0,
            current_avg_cost REAL DEFAULT 0,
            last_txn_at TEXT
        )""")

        # ─── v5.0 Phase 7: Owner Withdrawals + Cash Buckets ───────────────
        # Withdrawals reduce cash drawer but are NOT operating expenses (Rule 13).
        c.execute("""CREATE TABLE IF NOT EXISTS owner_withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            payment_method TEXT DEFAULT 'cash',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Settings defaults for cash-bucket logic
        def _set_default_setting_v5(key, value):
            existing = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO settings(key, value) VALUES(?,?)", (key, str(value)))
        _set_default_setting_v5("business_reserve_pct", "10")
        _set_default_setting_v5("stock_reserve_target_days", "15")

        # ─── v8.12.1: Capital Injections ───────────────────────────────
        # Owner-invested capital (initial investment, top-ups, partner contributions,
        # bank loans injected into the business). Each row writes a matching
        # cash_drawer entry with type='capital_injection' (+amount) so the
        # "Available for Withdrawal" formula no longer goes negative on Day 1
        # when the owner has invested capital that was already converted to stock
        # before the first sale.
        c.execute("""CREATE TABLE IF NOT EXISTS capital_injections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'owner_pocket',
            payment_method TEXT DEFAULT 'cash',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_capital_injections_created ON capital_injections(created_at)")

        # ─── v8.13.0: Stock Write-offs (damage / expiry / theft / sample) ───
        # Replaces the "negative stock_adjustment + reason" pattern with a
        # dedicated audit-tracked table. Each row reduces stock_state AND
        # records the loss value (qty × avg_cost at time of write-off) so
        # the monthly P&L can show a separate "Shrinkage" line item.
        # Reasons: 'damage' | 'expiry' | 'theft' | 'sample' | 'display' | 'other'
        c.execute("""CREATE TABLE IF NOT EXISTS stock_writeoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES price_categories(id) ON DELETE CASCADE,
            qty REAL NOT NULL,
            unit_cost REAL NOT NULL,        -- avg_cost at time of write-off (snapshot)
            loss_value REAL NOT NULL,       -- qty × unit_cost (computed once for historical accuracy)
            reason TEXT NOT NULL,           -- damage | expiry | theft | sample | display | other
            notes TEXT DEFAULT '',
            manager_pin_verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_stock_writeoffs_cat ON stock_writeoffs(category_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_stock_writeoffs_created ON stock_writeoffs(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_stock_writeoffs_reason ON stock_writeoffs(reason)")

        # ─── v6.0 Phase 1: Persistent login throttle + dirty flag ──────────
        c.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            ts TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts_ts ON login_attempts(ts)")
        # dirty flag: set on startup, cleared after successful rebuild_stock_state
        def _set_default_setting_v6(key, value):
            existing = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO settings(key, value) VALUES(?,?)", (key, str(value)))
        _set_default_setting_v6("stock_state_dirty", "true")  # rebuild on first boot

        # ─── v6.0 Phase 2: Multi-Client Server Readiness ───────────────────
        # Device pairing for mobile/LAN clients
        c.execute("""CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            role TEXT DEFAULT 'cashier',
            last_seen TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_devices_token ON devices(token_hash)")
        # Pairing codes: short-lived 6-digit codes for device pairing
        c.execute("""CREATE TABLE IF NOT EXISTS pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            role TEXT DEFAULT 'cashier',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0
        )""")
        # v8.13.2: Add failure_count column to existing pairing_codes tables
        pc_cols = {r["name"] for r in c.execute("PRAGMA table_info(pairing_codes)").fetchall()}
        if "failure_count" not in pc_cols:
            try:
                c.execute("ALTER TABLE pairing_codes ADD COLUMN failure_count INTEGER DEFAULT 0")
            except Exception as _e:
                logger.warning("Silent exception in db.py: %s", _e, exc_info=True)
        _set_default_setting_v6("lan_mode", "false")

        # ─── v6.0 Phase 3: POS Mechanics ───────────────────────────────────
        # Bundles/combos: group multiple categories sold together at a combined price
        c.execute("""CREATE TABLE IF NOT EXISTS bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bundle_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bundle_id INTEGER REFERENCES bundles(id) ON DELETE CASCADE,
            category_id INTEGER REFERENCES price_categories(id) ON DELETE SET NULL,
            qty INTEGER NOT NULL DEFAULT 1
        )""")
        # Happy-hour pricing rules
        c.execute("""CREATE TABLE IF NOT EXISTS price_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER DEFAULT NULL,
            pct REAL NOT NULL,
            start_hhmm TEXT NOT NULL,
            end_hhmm TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # v6.0 Phase 4: Lost-sale tracking
        c.execute("""CREATE TABLE IF NOT EXISTS lost_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            qty INTEGER NOT NULL,
            est_revenue REAL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_lost_sales_cat ON lost_sales(category_id)")
        # v6.0 Phase 4: Margin-protection target setting
        _set_default_setting_v6("margin_protection_target", "20")
        # v6.0 Phase 6: Closed-days + seasonal calendar
        c.execute("""CREATE TABLE IF NOT EXISTS closed_days (
            date TEXT PRIMARY KEY,
            label TEXT DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            name TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL
        )""")
        # v6.0 Phase 5: Customer groups
        cust_cols_v6 = {r["name"] for r in c.execute("PRAGMA table_info(customers)").fetchall()}
        if "group_name" not in cust_cols_v6:
            c.execute("ALTER TABLE customers ADD COLUMN group_name TEXT DEFAULT 'retail'")
        # v6.0 Phase 6: Expense photo attachments
        exp_cols_v6 = {r["name"] for r in c.execute("PRAGMA table_info(expenses)").fetchall()}
        if "photo_path" not in exp_cols_v6:
            c.execute("ALTER TABLE expenses ADD COLUMN photo_path TEXT DEFAULT NULL")
        # sale_items: bundle_id reference
        si_cols_v6 = {r["name"] for r in c.execute("PRAGMA table_info(sale_items)").fetchall()}
        if "bundle_id" not in si_cols_v6:
            c.execute("ALTER TABLE sale_items ADD COLUMN bundle_id INTEGER DEFAULT NULL")
        # v8.8.0: Per-item discount + price override columns
        si_cols_v88 = {r["name"] for r in c.execute("PRAGMA table_info(sale_items)").fetchall()}
        if "discount_pct" not in si_cols_v88:
            c.execute("ALTER TABLE sale_items ADD COLUMN discount_pct REAL DEFAULT 0")
        if "discount_amount" not in si_cols_v88:
            c.execute("ALTER TABLE sale_items ADD COLUMN discount_amount REAL DEFAULT 0")
        if "override_price" not in si_cols_v88:
            c.execute("ALTER TABLE sale_items ADD COLUMN override_price REAL DEFAULT NULL")
        if "base_price" not in si_cols_v88:
            c.execute("ALTER TABLE sale_items ADD COLUMN base_price REAL DEFAULT NULL")

        # ─── v7.0 Phase 2-5: AI Infrastructure ─────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS ai_cache (
            key TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            response_json TEXT NOT NULL,
            provider TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cached INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_date ON ai_usage(date(created_at))")
        c.execute("""CREATE TABLE IF NOT EXISTS pending_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            reason TEXT,
            impact_summary TEXT,
            source TEXT DEFAULT 'ai',
            automation_level INTEGER DEFAULT 2,
            status TEXT DEFAULT 'pending',
            created_by TEXT DEFAULT 'ai',
            approved_by TEXT,
            pin_verified INTEGER DEFAULT 0,
            batch_id TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            executed_at TEXT,
            expires_at TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pending_expires ON pending_actions(expires_at) WHERE status='pending'")
        c.execute("""CREATE TABLE IF NOT EXISTS automation_config (
            key TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            level INTEGER DEFAULT 2,
            params_json TEXT DEFAULT '{}'
        )""")
        # Seed default automation config (all OFF) — v7.2: per-automation levels
        # L1 = read-only insights, L2 = drafts into queue, L3 = bounded auto-execute
        _auto_levels = {
            'auto_confirm_bills': 3,        # L3 — bounded auto-confirm of low-risk bills
            'auto_draft_po': 2,              # L2 — drafts POs into queue
            'urdhaar_reminders': 1,          # L1 — surfaces insights only
            'recurring_detection': 1,        # L1
            'expense_categorization': 2,     # L2 — drafts category suggestions
            'anomaly_diagnosis': 1,          # L1
            'variance_investigation': 1,     # L1
            'scheduled_reports': 1,          # L1
            'dead_stock_liquidation': 2,     # L2 — drafts promo pricing
            'ai_kill_switch': 0,             # special — not a real automation
        }
        for key, level in _auto_levels.items():
            existing = c.execute("SELECT key, level FROM automation_config WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO automation_config(key, enabled, level, params_json) VALUES(?,?,?,?)",
                         (key, 0, level, '{}'))
            elif existing["level"] != level:
                # v7.2: migrate existing rows to the correct per-automation level
                c.execute("UPDATE automation_config SET level=? WHERE key=?", (level, key))
        _set_default_setting_v6("max_ai_calls_per_day_groq", "500")
        _set_default_setting_v6("max_ai_calls_per_day_gemini", "100")

        # ─── v8.5: Production-hardening settings ────────────────────────────
        # These keys are read by the hardened get_inventory(), cash drawer,
        # loyalty, tax, and crypto modules. Seeded once on first boot.
        def _set_default_setting_v8_5(key, value):
            existing = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if not existing:
                c.execute("INSERT INTO settings(key, value) VALUES(?,?)", (key, str(value)))
        _set_default_setting_v8_5("password_hash", "")            # set on first login
        _set_default_setting_v8_5("loyalty_points_per_rs", "100") # 1 pt per Rs 100 spent
        _set_default_setting_v8_5("loyalty_rate", "1")            # Rs 1 per point redeemed
        _set_default_setting_v8_5("tax_rate", "0")                # 0% default
        _set_default_setting_v8_5("tax_inclusive", "false")       # tax added on top
        # v8.15.0 (design.md): cream canvas is the brand default theme
        # (was "dark" — a Linear-era leftover that contradicted the design system)
        _set_default_setting_v8_5("appearance_theme", "light")     # default UI theme
        # v8.15.0 (design.md): seed the full appearance token defaults
        _set_default_setting_v8_5("appearance_accent", "#cc785c")  # signature coral
        _set_default_setting_v8_5("appearance_density", "comfortable")
        _set_default_setting_v8_5("appearance_font_scale", "100")
        _set_default_setting_v8_5("appearance_serif_headings", "1")  # serif display ON
        _set_default_setting_v8_5("appearance_radius", "standard")
        _set_default_setting_v8_5("stock_state_dirty", "0")      # cleared by rebuild_stock_state

        # v8.9.1: Feature flags for POS import sync (default OFF — opt-in)
        _set_default_setting_v8_5("pos_import_sync_deletions", "false")
        _set_default_setting_v8_5("pos_import_sync_modifications", "false")

        # v8.9.1: Activity log orphan marking (Phase 7)
        al_cols = {r["name"] for r in c.execute("PRAGMA table_info(activity_log)").fetchall()}
        if "entity_deleted" not in al_cols:
            c.execute("ALTER TABLE activity_log ADD COLUMN entity_deleted INTEGER DEFAULT 0")
        # H13 fix (v8.13.4): actor columns on activity_log
        if "actor_employee_id" not in al_cols:
            c.execute("ALTER TABLE activity_log ADD COLUMN actor_employee_id INTEGER")
        if "actor_session" not in al_cols:
            c.execute("ALTER TABLE activity_log ADD COLUMN actor_session TEXT")
        if "actor_ip" not in al_cols:
            c.execute("ALTER TABLE activity_log ADD COLUMN actor_ip TEXT")
        # H6 fix (v8.13.4): device-token expiry column
        try:
            d_cols = {r["name"] for r in c.execute("PRAGMA table_info(devices)").fetchall()}
            if "expires_at" not in d_cols:
                c.execute("ALTER TABLE devices ADD COLUMN expires_at TEXT")
        except Exception:
            pass  # devices table may not exist yet
        # H4 fix (v8.13.4): employee lockout column
        try:
            emp_cols = {r["name"] for r in c.execute("PRAGMA table_info(employees)").fetchall()}
            if "locked_until" not in emp_cols:
                c.execute("ALTER TABLE employees ADD COLUMN locked_until TEXT")
        except Exception:
            pass
        # C7 fix (v8.13.4): CSRF token column on sessions
        try:
            s_cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            if "csrf_token" not in s_cols:
                c.execute("ALTER TABLE sessions ADD COLUMN csrf_token TEXT")
        except Exception:
            pass
        # C1 fix (v8.13.4): password_must_change flag on settings (it's a key/value row)
        if not c.execute("SELECT 1 FROM settings WHERE key='password_must_change' LIMIT 1").fetchone():
            # Only set if password_hash already exists — otherwise the
            # setup wizard will complete and we don't want to flip this
            pw = c.execute("SELECT value FROM settings WHERE key='password_hash' LIMIT 1").fetchone()
            if pw and pw["value"]:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES('password_must_change', 'false') "
                    "ON CONFLICT(key) DO NOTHING"
                )

        # v8.14.0: FBR POS live integration columns on sales
        try:
            s_cols = {r["name"] for r in c.execute("PRAGMA table_info(sales)").fetchall()}
            if "fbr_invoice_ref" not in s_cols:
                c.execute("ALTER TABLE sales ADD COLUMN fbr_invoice_ref TEXT DEFAULT NULL")
            if "fbr_qr_payload" not in s_cols:
                c.execute("ALTER TABLE sales ADD COLUMN fbr_qr_payload TEXT DEFAULT NULL")
            if "fbr_posted_at" not in s_cols:
                c.execute("ALTER TABLE sales ADD COLUMN fbr_posted_at TEXT DEFAULT NULL")
        except Exception:
            pass

        # v8.14.0: Settings rows for FBR auto-post + digest
        _set_default_setting_v8_5("fbr_auto_post", "0")             # off by default
        # v8.18.4: Google Drive cloud backup removed. Delete every gdrive_*
        # settings row left over from older installs — this also drops the
        # stored (encrypted) OAuth refresh token, so no trace of the Drive
        # connection survives the upgrade.
        c.execute("DELETE FROM settings WHERE key LIKE 'gdrive_%'")
        _set_default_setting_v8_5("digest_enabled", "0")           # daily sales digest off by default
        _set_default_setting_v8_5("digest_hour", "21")             # 9 PM PKT
        _set_default_setting_v8_5("digest_phone", "")              # E.164 like +923331234567
        _set_default_setting_v8_5("digest_twilio_sid", "")
        _set_default_setting_v8_5("digest_twilio_token_enc", "")   # encrypted at rest
        _set_default_setting_v8_5("digest_twilio_whatsapp_from", "")  # 'whatsapp:+1415...'
        _set_default_setting_v8_5("db_encryption_key", "")          # set to enable SQLCipher

        # v8.9.1: Canonical sale-status filter constants
        # BillBook has exactly 4 payment_status values:
        #   'paid'     — fully paid (cash/card/online)
        #   'credit'   — full credit (customer owes the full amount)
        #   'partial'  — split payment with unpaid portion
        #   'refunded' — full refund (sale reversed, items returned to stock)
        # There are NO partial refunds (no refunded_qty column, no refund_items table).
        # When a sale is refunded, payment_status is set to 'refunded' and the ENTIRE
        # sale (all items, all qty) is excluded from "sold" aggregations.

        # crypto_salt is generated lazily by crypto._get_or_create_salt()
        # on first encryption — but we ensure the row exists for visibility.
        salt_existing = c.execute("SELECT value FROM settings WHERE key='crypto_salt'").fetchone()
        if not salt_existing:
            import os as _os, base64 as _b64
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?,?)",
                ("crypto_salt", _b64.b64encode(_os.urandom(16)).decode()),
            )

        # v7.2 Phase 8: 7-day expiry on pending_actions
        pa_cols = {r["name"] for r in c.execute("PRAGMA table_info(pending_actions)").fetchall()}
        if "expires_at" not in pa_cols:
            c.execute("ALTER TABLE pending_actions ADD COLUMN expires_at TEXT")
            # Backfill: set expires_at = created_at + 7 days for existing pending rows
            c.execute(
                "UPDATE pending_actions SET expires_at = "
                "datetime(created_at, '+7 days') WHERE status='pending' AND expires_at IS NULL"
            )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_expires ON "
            "pending_actions(expires_at) WHERE status='pending'"
        )
        # Expire any stale pending actions (older than 7 days) on every boot
        c.execute(
            "UPDATE pending_actions SET status='expired' "
            "WHERE status='pending' AND expires_at IS NOT NULL "
            "AND expires_at < datetime('now','localtime')"
        )

        # ─── v8.0 Phase 1: Branch Identity ──────────────────────────────────
        # Single-shop must stay identical: role='branch' + empty hub_url = v7.2 behavior.
        c.execute("""CREATE TABLE IF NOT EXISTS branch_config (
            id INTEGER PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'branch',
            branch_id TEXT,
            branch_name TEXT DEFAULT 'Main Shop',
            region TEXT DEFAULT '',
            hub_url TEXT DEFAULT '',
            sync_token_hash TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Seed default row (id=1) if not present
        existing_cfg = c.execute("SELECT id FROM branch_config WHERE id=1").fetchone()
        if not existing_cfg:
            c.execute(
                "INSERT INTO branch_config(id, role, branch_name, region, hub_url, sync_token_hash) "
                "VALUES(1, 'branch', 'Main Shop', '', '', '')"
            )

        # ─── v8.0 Phase 2: HQ Branch Registry ───────────────────────────────
        # On HQ only — stores the list of registered branches + their auth tokens.
        c.execute("""CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            region TEXT DEFAULT '',
            tunnel_url TEXT DEFAULT '',
            auth_token_hash TEXT NOT NULL,
            last_seen TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS branch_pairing_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            role TEXT DEFAULT 'branch',
            proposed_name TEXT,
            proposed_region TEXT,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # v8.13.2: Add failure_count column to existing branch_pairing_codes tables
        bpc_cols = {r["name"] for r in c.execute("PRAGMA table_info(branch_pairing_codes)").fetchall()}
        if "failure_count" not in bpc_cols:
            try:
                c.execute("ALTER TABLE branch_pairing_codes ADD COLUMN failure_count INTEGER DEFAULT 0")
            except Exception as _e:
                logger.warning("Silent exception in db.py: %s", _e, exc_info=True)
        # ─── v8.0 Phase 3: Consolidated Visibility ─────────────────────────
        # branch_summaries stores daily push from each branch to HQ.
        # Idempotent by UNIQUE(branch_id, summary_date).
        c.execute("""CREATE TABLE IF NOT EXISTS branch_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id TEXT NOT NULL,
            summary_date TEXT NOT NULL,
            sales REAL DEFAULT 0,
            cogs REAL DEFAULT 0,
            gross_profit REAL DEFAULT 0,
            expenses REAL DEFAULT 0,
            cash_in_drawer REAL DEFAULT 0,
            stock_snapshot_json TEXT DEFAULT '{}',
            synced_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(branch_id, summary_date)
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_branch_summaries_date "
            "ON branch_summaries(summary_date)"
        )
        # sync_outbox for reliable event delivery (reuses offline-outbox pattern)
        c.execute("""CREATE TABLE IF NOT EXISTS sync_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dest_branch_id TEXT,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            last_attempt_at TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_outbox_status "
            "ON sync_outbox(status, created_at)"
        )

        # ─── v8.0 Phase 4: Inter-Branch Stock Transfer ─────────────────────
        # transfer_challans carry unit_cost per line EXPLICITLY — never recomputed on
        # the receiving side. Sender's apply_transfer_out_to_state captures the avg cost
        # at the moment of transfer; receiver applies via apply_purchase_to_state.
        c.execute("""CREATE TABLE IF NOT EXISTS transfer_challans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challan_no TEXT UNIQUE NOT NULL,
            from_branch_id TEXT NOT NULL,
            to_branch_id TEXT NOT NULL,
            status TEXT DEFAULT 'in_transit',
            total_qty REAL DEFAULT 0,
            total_value REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            accepted_at TEXT,
            rejected_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS transfer_challan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challan_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            category_code TEXT,
            qty REAL NOT NULL,
            unit_cost REAL NOT NULL,
            line_value REAL NOT NULL,
            FOREIGN KEY (challan_id) REFERENCES transfer_challans(id)
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_challan_status "
            "ON transfer_challans(status)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_challan_to_branch "
            "ON transfer_challans(to_branch_id, status)"
        )

        # ─── v8.0 Phase 5: Central Purchasing & Distribution ───────────────
        # central_purchases tracks bulk buys at HQ (virtual Central Warehouse branch).
        # Distribution happens via transfer_challans from BR-CENTRAL to branches.
        c.execute("""CREATE TABLE IF NOT EXISTS central_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_no TEXT UNIQUE NOT NULL,
            supplier_name TEXT,
            total_qty REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'recorded',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS central_purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            category_code TEXT,
            qty REAL NOT NULL,
            unit_cost REAL NOT NULL,
            line_value REAL NOT NULL,
            distributed_qty REAL DEFAULT 0,
            remaining_qty REAL DEFAULT 0,
            FOREIGN KEY (purchase_id) REFERENCES central_purchases(id)
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_central_purchase_status "
            "ON central_purchases(status)"
        )

        # ─── v8.0 Phase 6: Global Price Push ───────────────────────────────
        # price_pushes tracks HQ-initiated price updates sent to all branches.
        # Idempotent by price_push_id — re-delivery never double-applies.
        c.execute("""CREATE TABLE IF NOT EXISTS price_pushes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price_push_id TEXT UNIQUE NOT NULL,
            category_id INTEGER NOT NULL,
            category_code TEXT,
            new_sell_price REAL NOT NULL,
            notes TEXT DEFAULT '',
            pushed_at TEXT DEFAULT (datetime('now','localtime')),
            applied_at TEXT
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_pushes_category "
            "ON price_pushes(category_id)"
        )

        # ─── v8.2 Phase 1: AI Auditor ───────────────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT DEFAULT (datetime('now','localtime')),
            trigger TEXT DEFAULT 'manual',
            period TEXT,
            status TEXT DEFAULT 'completed',
            findings_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            warning_count INTEGER DEFAULT 0,
            info_count INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS audit_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            domain TEXT NOT NULL,
            check_key TEXT NOT NULL,
            severity TEXT DEFAULT 'info',
            title TEXT,
            detail TEXT,
            amount REAL,
            status TEXT DEFAULT 'open',
            action_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (run_id) REFERENCES audit_runs(id)
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_findings_run "
            "ON audit_findings(run_id)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_findings_severity "
            "ON audit_findings(severity, status)"
        )

        # ─── v8.2 Phase 5: Bill Intelligence ───────────────────────────────
        c.execute("""CREATE TABLE IF NOT EXISTS bill_intelligence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            last_purchase_qty REAL,
            last_purchase_date TEXT,
            sold_since REAL,
            sell_through_pct REAL,
            verdict TEXT,
            acknowledged INTEGER DEFAULT 0,
            ack_reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (bill_id) REFERENCES bills(id)
        )""")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_bill_intel_bill "
            "ON bill_intelligence(bill_id)"
        )

        # ─── v8.2.3: Third-party POS backup import ───────────────────────
        # Tracks imported transactions by UNQCODE for deduplication.
        # Each daily backup zip contains the FULL cumulative DB, so we use
        # UNQCODE to skip already-imported records.
        # NOTE: 'pos_imports' table already exists (v6.0 CSV import), so we use
        # 'ezi_pos_imports' for the DBF dedup tracking.
        c.execute("""CREATE TABLE IF NOT EXISTS ezi_pos_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unqcode TEXT UNIQUE NOT NULL,
            import_date TEXT NOT NULL,
            sale_id INTEGER,
            txn_date TEXT,
            amount REAL,
            source TEXT DEFAULT 'ezi_pos',
            import_run_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # Index on unqcode is created automatically by the UNIQUE constraint above.
        # v8.5: add import_run_id column to existing ezi_pos_imports rows BEFORE
        # creating the index (CREATE INDEX fails with "no such column" if the
        # column doesn't exist yet on a pre-v8.5 database).
        ezi_cols = {r["name"] for r in c.execute("PRAGMA table_info(ezi_pos_imports)").fetchall()}
        if "import_run_id" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN import_run_id INTEGER")
        # v8.10: POS import sync columns (Phase 5)
        if "synced_deleted" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN synced_deleted INTEGER DEFAULT 0")
        if "synced_updated" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN synced_updated INTEGER DEFAULT 0")
        if "source_checksum" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN source_checksum TEXT")
        if "deleted_sync_at" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN deleted_sync_at TEXT")
        if "updated_sync_at" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN updated_sync_at TEXT")
        if "checksum_initialized_at" not in ezi_cols:
            c.execute("ALTER TABLE ezi_pos_imports ADD COLUMN checksum_initialized_at TEXT")
        # v8.5: index on import_run_id for fast rollback (safe to create now)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_ezi_import_run ON ezi_pos_imports(import_run_id)"
        )
        c.execute("""CREATE TABLE IF NOT EXISTS pos_expense_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_hash TEXT UNIQUE NOT NULL,
            description TEXT,
            amount REAL,
            date TEXT,
            import_date TEXT,
            import_run_id INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )""")
        # v8.5: add import_run_id column to existing pos_expense_imports BEFORE
        # creating the index (same pattern as above).
        pe_cols = {r["name"] for r in c.execute("PRAGMA table_info(pos_expense_imports)").fetchall()}
        if "import_run_id" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN import_run_id INTEGER")
        # v8.16.7: track expense_id + synced_deleted for expense deletion sync
        # v8.16.8: also track source_checksum + synced_updated for expense UPDATE sync
        # ON DELETE SET NULL so deleting an expense doesn't fail the FK constraint
        if "expense_id" not in pe_cols:
            # SQLite ALTER TABLE doesn't support ON DELETE on column add,
            # so we add without the FK constraint and rely on app-level nulling.
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN expense_id INTEGER")
        if "synced_deleted" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN synced_deleted INTEGER DEFAULT 0")
        if "deleted_sync_at" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN deleted_sync_at TEXT")
        # v8.16.8: checksum-based modification detection (mirrors ezi_pos_imports pattern)
        if "source_checksum" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN source_checksum TEXT")
        if "checksum_initialized_at" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN checksum_initialized_at TEXT")
        if "synced_updated" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN synced_updated INTEGER DEFAULT 0")
        if "updated_sync_at" not in pe_cols:
            c.execute("ALTER TABLE pos_expense_imports ADD COLUMN updated_sync_at TEXT")
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_pos_exp_run ON pos_expense_imports(import_run_id)"
        )
        # v8.5: add sale_count, expense_count, total_cogs to pos_imports (migration)
        pi_cols = {r["name"] for r in c.execute("PRAGMA table_info(pos_imports)").fetchall()}
        if "sale_count" not in pi_cols:
            c.execute("ALTER TABLE pos_imports ADD COLUMN sale_count INTEGER DEFAULT 0")
        if "expense_count" not in pi_cols:
            c.execute("ALTER TABLE pos_imports ADD COLUMN expense_count INTEGER DEFAULT 0")
        if "total_cogs" not in pi_cols:
            c.execute("ALTER TABLE pos_imports ADD COLUMN total_cogs REAL DEFAULT 0")

        # v8.13.1: ALL indexes now run AFTER all migrations — many indexes
        # reference tables/columns created by migrations (owner_withdrawals,
        # capital_injections, stock_writeoffs, deleted_at columns, etc.).
        # Previously the INDEXES list ran before migrations → "no such table" errors.
        for idx in INDEXES:
            try:
                c.execute(idx)
            except Exception:
                pass  # column/table may not exist yet if migration was skipped
        # Post-migration indexes (deleted_at on customers/suppliers)
        for idx in POST_MIGRATION_INDEXES:
            try:
                c.execute(idx)
            except Exception as _e:
                logger.warning("Silent exception in db.py: %s", _e, exc_info=True)
        # v8.13.1: Materialized summary table + triggers for O(1) cash drawer total
        c.execute(SUMMARY_TABLE_SQL)
        c.execute(
            "INSERT OR IGNORE INTO cash_summary(id, cash_in_drawer, "
            "owner_withdrawals_all_time, capital_injections_all_time, "
            "customers_outstanding_credit, updated_at) "
            "VALUES (1, 0, 0, 0, 0, datetime('now','localtime'))"
        )
        # Backfill the summary from existing data on first migration
        try:
            summary_row = c.execute("SELECT cash_in_drawer FROM cash_summary WHERE id=1").fetchone()
            cd_count = c.execute("SELECT COUNT(*) AS n FROM cash_drawer").fetchone()["n"]
            if summary_row and summary_row["cash_in_drawer"] == 0 and cd_count > 0:
                cd_sum = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM cash_drawer").fetchone()["v"]
                ow_sum = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM owner_withdrawals").fetchone()["v"]
                ci_sum = c.execute("SELECT COALESCE(SUM(amount), 0) AS v FROM capital_injections").fetchone()["v"]
                cc_sum = c.execute("SELECT COALESCE(SUM(total_credit), 0) AS v FROM customers WHERE deleted_at IS NULL").fetchone()["v"]
                c.execute(
                    "UPDATE cash_summary SET cash_in_drawer=?, owner_withdrawals_all_time=?, "
                    "capital_injections_all_time=?, customers_outstanding_credit=?, "
                    "updated_at=datetime('now','localtime') WHERE id=1",
                    (cd_sum, ow_sum, ci_sum, cc_sum)
                )
        except Exception as _e:
            logger.warning("Silent exception in db.py: %s", _e, exc_info=True)
        # Create the triggers
        for trig in SUMMARY_TRIGGERS_SQL:
            try:
                c.execute(trig)
            except Exception as _e:
                logger.warning("Silent exception in db.py: %s", _e, exc_info=True)
def get_setting(key: str, default: str = "") -> str:
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def log_activity(event_type: str, entity_type: str = None, entity_id: int = None,
                 description: str = "", metadata: dict = None, *,
                 c=None, actor_employee_id: int = None,
                 actor_session: str = None, actor_ip: str = None):
    """Record an activity event for the recent-activity feed.

    Phase 0 PR 3: optional keyword-only `c` (SQLite connection). If provided,
    uses that connection and does NOT commit (caller controls the transaction).
    If `c` is None (default), opens its own connection (backward compatible).

    H13 fix (v8.13.4): now accepts actor_employee_id / actor_session / actor_ip
    keyword args. Routers should pass these for any audit-worthy action
    (refund, void, owner withdrawal, price override, etc.). The columns
    are nullable so old calls keep working.
    """
    import json as _json
    payload = (event_type, entity_type, entity_id, description,
               _json.dumps(metadata or {}),
               actor_employee_id, actor_session, actor_ip)
    insert_sql = (
        "INSERT INTO activity_log(event_type, entity_type, entity_id, "
        "description, metadata, actor_employee_id, actor_session, actor_ip) "
        "VALUES(?,?,?,?,?,?,?,?)"
    )
    if c is not None:
        try:
            c.execute(insert_sql, payload)
        except Exception:
            # Legacy schema without actor columns — fall back to old insert
            c.execute(
                "INSERT INTO activity_log(event_type, entity_type, entity_id, description, metadata) "
                "VALUES(?,?,?,?,?)",
                payload[:5],
            )
        return
    with conn() as own_c:
        try:
            own_c.execute(insert_sql, payload)
        except Exception:
            own_c.execute(
                "INSERT INTO activity_log(event_type, entity_type, entity_id, description, metadata) "
                "VALUES(?,?,?,?,?)",
                payload[:5],
            )


def mark_orphaned_activity_logs():
    """v8.9.1 Phase 7: Mark activity_log entries whose entity has been
    HARD-DELETED (row no longer exists in the table at all).

    Soft-deleted entities (bills with deleted_at, suppliers with deleted_at)
    keep their logs visible — the row still exists, so it won't be marked.

    Uses entity_deleted=1 flag (NOT deletion) so the audit trail is preserved.
    UI shows "[deleted]" tag for marked entries.
    """
    marked = 0
    with conn() as c:
        # Bills — only mark if the bill row no longer exists AT ALL
        # (soft-deleted bills still have a row with deleted_at, so they're fine)
        r = c.execute(
            "UPDATE activity_log SET entity_deleted = 1 "
            "WHERE entity_type = 'bill' "
            "AND entity_id NOT IN (SELECT id FROM bills) "
            "AND entity_deleted = 0"
        )
        marked += r.rowcount
        # Customers
        r = c.execute(
            "UPDATE activity_log SET entity_deleted = 1 "
            "WHERE entity_type = 'customer' "
            "AND entity_id NOT IN (SELECT id FROM customers) "
            "AND entity_deleted = 0"
        )
        marked += r.rowcount
        # Suppliers
        r = c.execute(
            "UPDATE activity_log SET entity_deleted = 1 "
            "WHERE entity_type = 'supplier' "
            "AND entity_id NOT IN (SELECT id FROM suppliers) "
            "AND entity_deleted = 0"
        )
        marked += r.rowcount
        # Sales (for historical hard-deletes before v8.9.1)
        r = c.execute(
            "UPDATE activity_log SET entity_deleted = 1 "
            "WHERE entity_type = 'sale' "
            "AND entity_id NOT IN (SELECT id FROM sales) "
            "AND entity_deleted = 0"
        )
        marked += r.rowcount
    return {"marked": marked}

