"""v8.5 — Third-party POS backup import (Ezi POS / DBF format).

Imports sales data from a daily full-backup zip (BU*.zip) produced by the
Ezi POS system. Each backup contains the FULL cumulative DBF database, so
deduplication is done via the UNQCODE field in ACCTRANS.DBF.

Pipeline (per spec Part 2.1):
  1. Extract ZIP, find DBF files (case-insensitive).
  2. Parse ACCTRANS.DBF: group by UNQCODE, split SI (sale) vs SP (payment).
  3. Dedup by `ezi_pos_imports.unqcode`.
  4. For each new UNQCODE:
     a. Insert sales row with original ADD_DATE + ADD_TIME as created_at.
     b. Insert sale_items from SI records (with category fuzzy-match).
     c. Insert cash_drawer entry for cash sales (with original timestamp).
     d. Resolve customer from DEBTORS.DBF.INTERNAL → upsert + update stats.
     e. Apply sale to category_stock_state (inventory reduction).
     f. Record UNQCODE in ezi_pos_imports with import_run_id.
  5. Parse DIARY.DBF: MD5-hash each expense for dedup, insert with original date.
  6. Update pos_imports run record with final counts + warnings.

Deduplication guarantees: re-importing the same backup or importing a newer
cumulative backup never duplicates records.
"""
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path

from .db import conn, log_activity, write_tx, set_setting, get_setting

logger = logging.getLogger(__name__)

# Try to import dbfread (required for DBF parsing)
try:
    from dbfread import DBF
    HAS_DBFREAD = True
except ImportError:
    HAS_DBFREAD = False


# ─── Helpers ───────────────────────────────────────────────────────────────

def _normalize_add_time(add_time_raw: str) -> str:
    """Ezi POS stores ADD_TIME as 'YYMMDDHHMMSS' (e.g. '260806234653').
    Returns 'HH:MM:SS' (with colons) so SQLite datetime() can parse it.
    """
    if not add_time_raw or len(add_time_raw) < 6:
        return "00:00:00"
    s = str(add_time_raw).strip()
    # Ezi uses 'YYMMDDHHMMSS' (12 chars) — the time is the last 6 chars
    if len(s) >= 12:
        s = s[-6:]
    elif len(s) == 6:
        pass
    else:
        return "00:00:00"
    try:
        return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
    except Exception:
        return "00:00:00"


def _normalize_date(d) -> str:
    """Convert a DBF date (datetime.date or str) to 'YYYY-MM-DD'."""
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if d:
        return str(d)[:10]
    return datetime.now().strftime("%Y-%m-%d")


def _build_timestamp(d, add_time_raw: str) -> str:
    """Combine DATE + ADD_TIME → 'YYYY-MM-DD HH:MM:SS' (original txn time)."""
    return f"{_normalize_date(d)} {_normalize_add_time(add_time_raw)}"


def _paid_by_to_method(paid_by) -> str:
    """Ezi PAID_BY enum: 1=cash, 2=card, 3=online, 0/other=unknown→cash."""
    try:
        v = int(paid_by or 0)
    except (TypeError, ValueError):
        return "cash"
    return {1: "cash", 2: "card", 3: "online"}.get(v, "cash")


def _match_import_item_to_category(item_name: str, item_code: str = "") -> int | None:
    """Fuzzy-match an imported item name/code against price_categories.

    Strategy (cheap + deterministic):
      1. Exact code match (case-insensitive) on price_categories.code.
      2. Exact name match on price_categories.name.
      3. Substring match (item_name contains category name or vice versa).
      4. Fall back to None (caller logs a warning).
    """
    if not item_name and not item_code:
        return None
    with conn() as c:
        # 1. Exact code match
        if item_code:
            row = c.execute(
                "SELECT id FROM price_categories WHERE UPPER(code)=? AND active=1 LIMIT 1",
                (item_code.strip().upper(),),
            ).fetchone()
            if row:
                return row["id"]
        # 2. Exact name match
        if item_name:
            row = c.execute(
                "SELECT id FROM price_categories WHERE LOWER(name)=? AND active=1 LIMIT 1",
                (item_name.strip().lower(),),
            ).fetchone()
            if row:
                return row["id"]
        # 3. Substring match
        if item_name:
            name_lower = item_name.strip().lower()
            row = c.execute(
                "SELECT id FROM price_categories WHERE active=1 AND "
                "(LOWER(name) LIKE ? OR ? LIKE '%' || LOWER(name) || '%') LIMIT 1",
                (f"%{name_lower}%", name_lower),
            ).fetchone()
            if row:
                return row["id"]
    return None


# ─── Ezi POS item-master mapping (v8.5 — Bags + price-tier support) ────────
#
# Ezi POS stores items in STOCK.DBF with an INTERNAL id:
#   INTERNAL 1-5    : "Bag Rs 10/20/30/50/60" — shopping bags sold alongside products
#   INTERNAL 606-609: "ITEM 250/500/750/1000" — the 4 main price-tier categories
#   INTERNAL 6-605  : 600 "AUTO ACCSRI RS <price> Qty <n>" — sub-categorization
#                     (department × price-tier × pack-size). Mostly QTY=0 placeholders.
#
# BillBook's import must:
#   - Map INTERNAL 606-609 → price_categories A/B/C/D (so apply_sale_to_state reduces
#     the right category_stock_state row).
#   - Map INTERNAL 1-5 → custom_items (Bags group) — and reduce a "Bags" pseudo-
#     category so stock state is tracked. We auto-create the Bags category if missing.
#   - For any other INTERNAL: store as a sale_item with category_id=NULL but
#     cost_price from INVTRANS.COST (real COGS, not 0).
#
# The map is built once per import run from STOCK.DBF.

def _load_ezi_item_master(temp_dir: str) -> dict:
    """Parse STOCK.DBF and return a dict of {INTERNAL: {name, price, cost, qty, is_bag, is_price_tier}}.

    Also returns a 'bags_category_id' key with the BillBook category id for
    "Bags" (auto-created if missing). This lets bag sales decrement a real
    stock state row.
    """
    if not HAS_DBFREAD:
        return {}
    stock_path = os.path.join(temp_dir, "STOCK.DBF")
    if not os.path.exists(stock_path):
        # case-insensitive fallback
        for fn in os.listdir(temp_dir):
            if fn.upper() == "STOCK.DBF":
                stock_path = os.path.join(temp_dir, fn)
                break
    if not os.path.exists(stock_path):
        return {}

    items = {}
    for rec in DBF(stock_path):
        internal = int(rec.get("INTERNAL", 0) or 0)
        if not internal:
            continue
        desc = str(rec.get("DESC", "") or "").strip()
        price1 = float(rec.get("PRICE1", 0) or 0)
        cost = float(rec.get("COST", 0) or 0)
        qty = float(rec.get("QTY", 0) or 0)
        name_upper = desc.upper()
        is_bag = "BAG" in name_upper
        # Price-tier items are named "ITEM <price>" — the 4 main categories
        is_price_tier = name_upper.startswith("ITEM ") and price1 in (250, 500, 750, 1000)
        items[internal] = {
            "internal": internal,
            "part_no": str(rec.get("PART_NO", "") or ""),
            "name": desc,
            "price": price1,
            "cost": cost,
            "qty": qty,
            "is_bag": is_bag,
            "is_price_tier": is_price_tier,
        }
    return items


def _ensure_bags_categories(item_master: dict) -> dict:
    """v8.5.2: Auto-create a SEPARATE price_category for each distinct bag
    found in STOCK.DBF. Returns {ezi_internal_id: billbook_category_id}.

    Why per-price categories instead of one "Bags" category:
      - Bags have very different sell prices (Rs 10, 20, 30, 50, 60) and costs
        (Rs 8, 16, 20, 30, 40). Lumping them into one category produced a
        misleading -128% margin (sell Rs 10, avg_cost Rs 23).
      - Per-price categories give correct margins:
        Bag Rs 20 (cost Rs 16) → 20% margin
        Bag Rs 30 (cost Rs 20) → 33% margin
      - Each bag sale is mapped to its correct category by matching the
        Ezi INTERNAL id (which uniquely identifies the bag price).

    Categories are named "Bag Rs 20", "Bag Rs 30", etc. — they appear in the
    Settings → Price Categories list and in the inventory page alongside the
    main A/B/C/D categories.

    The per-bag stock state starts at 0 (no pre-purchase). When the user
    uploads real supplier bills that contain bags, apply_purchase_to_state()
    will increase each bag category's stock — same as A/B/C/D.

    Args:
        item_master: dict from _load_ezi_item_master() — {INTERNAL: {name, price, cost, is_bag, ...}}

    Returns:
        {ezi_internal_id: billbook_category_id} for bag items only.
        Non-bag items are NOT included in this map.
    """
    bag_map = {}
    if not item_master:
        return bag_map

    bag_items = [(internal, info) for internal, info in item_master.items() if info.get("is_bag")]
    if not bag_items:
        return bag_map

    with conn() as c:
        for internal, info in bag_items:
            price = float(info.get("price", 0) or 0)
            # Category name = the Ezi DESC (e.g. "Bag Rs 20") — keeps it readable
            name = info.get("name", f"Bag Rs {int(price)}").strip()
            # Code = "BAG" + price (e.g. "BAG20") — unique + sortable
            code = f"BAG{int(price)}" if price > 0 else "BAG"
            # Check if a category with this exact name already exists
            row = c.execute(
                "SELECT id FROM price_categories WHERE LOWER(name)=LOWER(?) AND active=1",
                (name,)
            ).fetchone()
            if row:
                bag_map[internal] = row["id"]
                continue
            # Create. sort_order starts at 100 so bags appear after main categories (1-4).
            cur = c.execute(
                "INSERT INTO price_categories(name, code, sell_price, color, sort_order, active) "
                "VALUES(?,?,?,?,?, 1)",
                (name, code, round(price, 2), "#64748B", 100 + int(price))
            )
            bag_map[internal] = cur.lastrowid
    return bag_map


def _ensure_bags_category() -> int:
    """LEGACY v8.5: returns id of a single "Bags" category. Kept as a fallback
    for bag items that don't have a per-price category (shouldn't happen with
    the v8.5.2 path, but safe).

    v8.5.2 callers should use _ensure_bags_categories() instead.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id FROM price_categories WHERE LOWER(name) LIKE 'bag%' AND active=1 "
            "ORDER BY sort_order LIMIT 1"
        ).fetchone()
        if row:
            return row["id"]
        cur = c.execute(
            "INSERT INTO price_categories(name, code, sell_price, color, sort_order, active) "
            "VALUES('Bags', 'BAG', 10, '#64748B', 999, 1)"
        )
        return cur.lastrowid


def _resolve_ezi_item_to_category(item_master_entry: dict,
                                    bags_category_id: int,
                                    bag_internal_to_category: dict = None) -> int | None:
    """Map an Ezi STOCK item to a BillBook price_categories.id.

    v8.5.2: bag items are mapped to their per-price category via the
    bag_internal_to_category dict (built by _ensure_bags_categories).
    Falls back to the generic bags_category_id if the specific bag category
    isn't found.

    Rules:
      1. If item is a price-tier (ITEM 250/500/750/1000) → match BillBook
         category by sell_price (250→A, 500→B, 750→C, 1000→D).
      2. If item is a bag → look up per-price category via bag_internal_to_category.
         Fallback: bags_category_id (generic).
      3. Otherwise → try _match_import_item_to_category(name, part_no).
      4. Final fallback → None (caller logs warning).
    """
    if not item_master_entry:
        return None
    if item_master_entry["is_price_tier"]:
        price = item_master_entry["price"]
        if price > 0:
            with conn() as c:
                row = c.execute(
                    "SELECT id FROM price_categories WHERE sell_price=? AND active=1 "
                    "ORDER BY sort_order LIMIT 1",
                    (price,),
                ).fetchone()
                if row:
                    return row["id"]
        return _match_import_item_to_category(item_master_entry["name"], str(item_master_entry["price"]))
    if item_master_entry["is_bag"]:
        # v8.5.2: look up the per-price bag category
        if bag_internal_to_category:
            cat_id = bag_internal_to_category.get(item_master_entry["internal"])
            if cat_id:
                return cat_id
        # Fallback: generic bags category
        return bags_category_id
    return _match_import_item_to_category(item_master_entry["name"], item_master_entry["part_no"])


def _create_import_run(filename: str, shop_name: str = "") -> int:
    """Insert a new pos_imports row and return its id."""
    with conn() as c:
        return c.execute(
            "INSERT INTO pos_imports(source_name, filename, file_format, status, notes) "
            "VALUES(?,?,?,?,?)",
            ("Ezi POS", filename, "dbf", "importing", shop_name),
        ).lastrowid


def _finalize_import_run(run_id: int, sale_count: int, expense_count: int,
                          total_revenue: float, total_cogs: float,
                          date_start: str, date_end: str,
                          status: str = "imported", notes: str = ""):
    """Update the pos_imports row with final counts."""
    with conn() as c:
        c.execute(
            "UPDATE pos_imports SET sale_count=?, expense_count=?, total_revenue=?, "
            "total_cogs=?, date_range_start=?, date_range_end=?, status=?, notes=? "
            "WHERE id=?",
            (sale_count, expense_count, round(total_revenue, 2), round(total_cogs, 2),
             date_start, date_end, status, notes, run_id),
        )


# ─── Main entry point ──────────────────────────────────────────────────────

def import_pos_backup(zip_path: str) -> dict:
    """Import a third-party POS backup zip file.

    Pipeline:
      1. Extract ZIP, find DBF files
      2. Parse ACCTRANS.DBF — group by UNQCODE, dedup via ezi_pos_imports
      3. For each new UNQCODE: insert sale + sale_items + cash_drawer +
         customer upsert + apply_sale_to_state + record in ezi_pos_imports
      4. Parse DIARY.DBF — MD5-hash expenses for dedup, insert + cash_drawer
      5. Update pos_imports run record

    Returns:
      {import_run_id, imported_sales, imported_payments, imported_expenses,
       skipped_duplicates, backup_date, backup_file, shop_name,
       sales_by_date, total_sales_amount, warnings}
    """
    if not HAS_DBFREAD:
        raise RuntimeError("dbfread library not installed. Run: pip install dbfread")

    backup_filename = os.path.basename(zip_path)
    temp_dir = tempfile.mkdtemp(prefix="pos_import_")
    warnings_list = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_dir)

        # Parse COMPANY.DBF first to get shop name
        shop_name = "Unknown"
        company_path = os.path.join(temp_dir, "COMPANY.DBF")
        if os.path.exists(company_path):
            for rec in DBF(company_path, load=False):
                shop_name = rec.get("NAME1", "Unknown") or "Unknown"
                break

        # Create the import run record
        run_id = _create_import_run(backup_filename, shop_name)

        # ── PR 6 (Reviewer 3 correction): Set stock_state_dirty=true at the START ──
        # If the import process crashes mid-way (sale #500 of #1000), the dirty
        # flag is already set → next boot rebuilds the stock state from scratch,
        # correctly replaying all imported sales in chronological order.
        # Previously this was set at the END, which meant a crash left the
        # dirty flag UNSET → next boot skipped the rebuild → 500 chronologically-
        # drifted sales were permanently baked into the weighted average.
        set_setting("stock_state_dirty", "true")
        logger.info("PR 6: stock_state_dirty=true set at import START (crash-safe rebuild)")

        # ── v8.5.2: Load STOCK.DBF as the Ezi item master ──────────────
        # (INTERNAL → name, price, cost, is_bag, is_price_tier)
        item_master = _load_ezi_item_master(temp_dir)
        # v8.5.2: create ONE category PER distinct bag price (Bag Rs 20, Bag Rs 30, ...)
        # instead of a single "Bags" category. Each gets its own correct sell_price
        # and avg_cost (from the user's future bill uploads).
        bag_internal_to_category = _ensure_bags_categories(item_master) if item_master else {}
        # Also keep a generic bags_category_id as a fallback (legacy v8.5 path)
        bags_category_id = _ensure_bags_category() if item_master else None
        # v8.19.1: bag categories track "qty SOLD", not "purchased − sold"
        # (bag purchases are EXPENSES, never entered as bills — see the
        # block comment in profit_engine.sync_bags_stock_to_sold). Computed
        # AFTER _ensure_bags_categories so freshly auto-created bag
        # categories are included. Bag sale items are EXCLUDED from the
        # normal per-sale stock decrement below; the end-of-import sync
        # raises each bag category's stock to its total sold.
        from .profit_engine import bag_category_ids as _bag_category_ids
        with conn() as c:
            bags_stock_ids = _bag_category_ids(c)
        # NOTE: v8.5.2 does NOT pre-purchase bag stock. The user uploads real
        # supplier bills via BillBook's normal flow — apply_purchase_to_state
        # then increases each bag category's stock + sets the correct avg_cost.
        # Until bills are uploaded, bag stock will be 0 or negative (same as
        # A/B/C/D). This is expected behavior.

        # ── v8.5: Load INVTRANS.DBF as the line-item source of truth ────
        # INVTRANS.DBF has one row per line item per sale, with:
        #   UNQCODE  → sale id (groups lines into sales)
        #   INTERNAL → stock item id (FK to STOCK.DBF)
        #   DETAILS  → item name (e.g. "ITEM 250", "Bag Rs 30")
        #   QTY      → quantity sold
        #   AMOUNT   → line total
        #   COST     → per-unit cost at sale time (CRITICAL for COGS)
        # This replaces the old approach of using ACCTRANS.SI which only had sale totals.
        invtrans_path = os.path.join(temp_dir, "INVTRANS.DBF")
        if not os.path.exists(invtrans_path):
            for fn in os.listdir(temp_dir):
                if fn.upper() == "INVTRANS.DBF":
                    invtrans_path = os.path.join(temp_dir, fn)
                    break
        line_items_by_unqcode = {}
        if os.path.exists(invtrans_path):
            for rec in DBF(invtrans_path):
                if rec.get("TYPE") != "SI":
                    continue
                uc = rec.get("UNQCODE", "")
                if not uc:
                    continue
                internal = int(rec.get("INTERNAL", 0) or 0)
                line_items_by_unqcode.setdefault(uc, []).append({
                    "internal": internal,
                    "details": str(rec.get("DETAILS", "") or "").strip(),
                    "qty": float(rec.get("QTY", 0) or 0),
                    "amount": float(rec.get("AMOUNT", 0) or 0),
                    "cost": float(rec.get("COST", 0) or 0),  # per-unit cost at sale time
                    "part_no": str(rec.get("PART_NO", "") or ""),
                })

        # ── v8.5.1: Load INVOICE.DBF as the sale-header source of truth ──
        # INVOICE.DBF has one row per sale with the authoritative:
        #   UNQCODE  → sale id
        #   STATUS   → 'P' = paid, '' = credit (unpaid)
        #   AMOUNT   → sale total (the REAL total, no double-entry inflation)
        #   PAID     → amount actually paid (PAID < AMOUNT → credit sale)
        #   DATE     → sale date
        #   ADD_TIME → sale time
        #   CLIENT   → customer INTERNAL id (FK to DEBTORS.DBF)
        #   SALESMAN → staff id
        # This replaces ACCTRANS.SP as the source for sale totals (which was
        # 2x-inflated due to double-entry bookkeeping).
        invoice_path = os.path.join(temp_dir, "INVOICE.DBF")
        if not os.path.exists(invoice_path):
            for fn in os.listdir(temp_dir):
                if fn.upper() == "INVOICE.DBF":
                    invoice_path = os.path.join(temp_dir, fn)
                    break
        invoice_by_unqcode = {}
        if os.path.exists(invoice_path):
            for rec in DBF(invoice_path):
                if rec.get("TYPE") != "SI":
                    continue
                uc = rec.get("UNQCODE", "")
                if not uc:
                    continue
                invoice_by_unqcode[uc] = {
                    "status": str(rec.get("STATUS", "") or ""),
                    "amount": float(rec.get("AMOUNT", 0) or 0),
                    "paid": float(rec.get("PAID", 0) or 0),
                    "tendered": float(rec.get("TENDERED", 0) or 0),
                    "date": rec.get("DATE"),
                    "add_time": str(rec.get("ADD_TIME", "") or ""),
                    "client": int(rec.get("CLIENT", 0) or 0),
                    "salesman": int(rec.get("SALESMAN", 0) or 0),
                    "tax": float(rec.get("TAX", 0) or 0),
                    "rounding": float(rec.get("ROUNDING", 0) or 0),
                }

        # ── Parse ACCTRANS.DBF (payment-method info only — NOT totals) ──
        # ACCTRANS.DBF uses double-entry bookkeeping: each sale has 4 SI records
        # (debit Cash + credit Sales + debit COGS + credit Inventory) and 2 SP
        # records (debit Cash + credit AR). We only use it now to determine the
        # payment method (PAID_BY enum) for each sale.
        acctrans_path = os.path.join(temp_dir, "ACCTRANS.DBF")
        if not os.path.exists(acctrans_path):
            # case-insensitive fallback
            for fn in os.listdir(temp_dir):
                if fn.upper() == "ACCTRANS.DBF":
                    acctrans_path = os.path.join(temp_dir, fn)
                    break
        if not os.path.exists(acctrans_path):
            raise FileNotFoundError("ACCTRANS.DBF not found in backup zip")

        # Get already-imported UNQCODEs for dedup
        with conn() as c:
            existing_unqcodes = {row["unqcode"] for row in c.execute(
                "SELECT unqcode FROM ezi_pos_imports"
            ).fetchall()}

        # v8.5.1: Iterate INVOICE.DBF UNQCODEs as the authoritative sale list.
        # Each INVOICE row = 1 sale. We then look up its line items in INVTRANS,
        # its payment method in ACCTRANS.SP, and its customer in DEBTORS.
        # This avoids the double-entry duplication in ACCTRANS (4 SI + 2 SP per sale).
        transactions = {}  # unqcode → list of ACCTRANS records (for payment-method lookup only)
        skipped_duplicates_pre = 0
        if os.path.exists(acctrans_path):
            all_records = list(DBF(acctrans_path))
            for rec in all_records:
                uc = rec.get("UNQCODE", "")
                if not uc:
                    continue
                if uc in existing_unqcodes:
                    skipped_duplicates_pre += 1
                    continue
                transactions.setdefault(uc, []).append(rec)

        # The authoritative list of sales to import = INVOICE.DBF UNQCODEs
        # (filtered to exclude already-imported ones).
        sale_unqcodes = [
            uc for uc in invoice_by_unqcode.keys()
            if uc not in existing_unqcodes
        ]
        # ── PR 6 (Reviewer 1 correction): Sort sales by INVOICE.DATE ──
        # The DBF rows from Ezi POS may be in INSERTION order, not transaction-date
        # order. The per-sale apply_sale_to_state() calls build up the running
        # weighted-average cost — if sales are processed out of chronological
        # order, the avg cost drifts (a sale at time T1 sees a different pool
        # than it should, because a later-dated purchase at T0 hasn't been
        # applied yet). Sorting by INVOICE.DATE ensures the weighted avg is
        # built up correctly.
        # NOTE: We also pass txn_at=created_at_str to apply_sale_to_state, but
        # that only sets the last_txn_at metadata — the ORDER of mutation
        # matters, not the timestamp. So sorting here is the actual fix.
        def _sort_key(uc):
            inv = invoice_by_unqcode.get(uc, {})
            # Combine date + add_time for a stable chronological sort key.
            # Fall back to empty string if missing (sorts first).
            d = str(inv.get("date") or "")
            t = str(inv.get("add_time") or "")
            return (d, t, str(uc))
        sale_unqcodes.sort(key=_sort_key)
        logger.info(f"PR 6: sorted {len(sale_unqcodes)} sales by INVOICE.DATE for chronological processing")

        # Load DEBTORS.DBF for customer resolution (INTERNAL → customer name)
        debtors_by_internal = {}
        debtors_path = os.path.join(temp_dir, "DEBTORS.DBF")
        if os.path.exists(debtors_path):
            for r in DBF(debtors_path):
                debtors_by_internal[int(r.get("INTERNAL", 0) or 0)] = {
                    "name": r.get("NAME", "") or "Cash Sales",
                    "phone": r.get("PHONE", "") or r.get("MOBILE", "") or "",
                }

        sales_imported = 0
        skipped_duplicates = skipped_duplicates_pre
        payments_imported = 0
        sales_by_date = {}
        backup_dates = set()
        total_cogs = 0.0

        for unqcode in sale_unqcodes:
            inv = invoice_by_unqcode.get(unqcode)
            if not inv:
                # No INVOICE header — skip (can't determine total reliably)
                continue
            recs = transactions.get(unqcode, [])
            si_recs = [r for r in recs if r.get("TYPE") == "SI"]
            sp_recs = [r for r in recs if r.get("TYPE") == "SP"]

            # Re-check dedup inside the transaction (race-safe)
            with conn() as c:
                already = c.execute(
                    "SELECT 1 FROM ezi_pos_imports WHERE unqcode=?", (unqcode,)
                ).fetchone()
            if already:
                skipped_duplicates += 1
                continue

            # ── Build the original transaction timestamp ────────────────
            # v8.5.1: prefer INVOICE.DATE + INVOICE.ADD_TIME (authoritative)
            created_at_str = _build_timestamp(inv.get("date"), inv.get("add_time", ""))
            txn_date_str = _normalize_date(inv.get("date"))
            backup_dates.add(txn_date_str)

            # ── Calculate total sale amount (AUTHORITATIVE: from INVOICE.DBF) ─
            # v8.5.1 FIX: ACCTRANS.DBF uses DOUBLE-ENTRY bookkeeping.
            # Each sale produces 4 SI records (debit Cash + credit Sales +
            # debit COGS + credit Inventory) and 2 SP records (debit Cash +
            # credit AR) — each pair has the SAME amount. Summing all SP
            # records double-counts, summing all SI records quadruple-counts.
            #
            # AUTHORITATIVE source for sale total = INVOICE.DBF.AMOUNT (one row per sale).
            # INVTRANS.DBF line items should sum to the same total (used as a sanity check).
            total_amount = inv.get("amount", 0)

            # ── Determine payment method (from SP records) ──────────────
            # Use only the non-CREDIT SP record (the "money received" side)
            non_credit_sp_for_method = [r for r in sp_recs if not r.get("CREDIT")]
            paid_by = non_credit_sp_for_method[0].get("PAID_BY", 1) if non_credit_sp_for_method else 1
            payment_method = _paid_by_to_method(paid_by)

            # ── Determine credit status (v8.5.1: use INVOICE.PAID < AMOUNT) ──
            # The old code checked ACCTRANS.BALANCE which is always 0 in this
            # backup format. The authoritative signal is INVOICE.PAID:
            #   - PAID == AMOUNT → fully paid (cash sale)
            #   - PAID < AMOUNT  → credit sale (customer owes the balance)
            #   - PAID == 0      → pure credit sale (nothing paid yet)
            is_credit = (inv.get("paid", 0) < inv.get("amount", 0)) and inv.get("amount", 0) > 0

            # ── Resolve customer from DEBTORS.DBF via INVOICE.CLIENT ───────
            # v8.5.1: use INVOICE.CLIENT (the customer on the sale) instead of
            # the ACCTRANS.INTERNAL (which is an account code, not a customer).
            customer_id = None
            customer_name = ""
            customer_phone = ""
            client_internal = inv.get("client", 0) or 0
            try:
                client_internal = int(client_internal)
            except (TypeError, ValueError):
                client_internal = 0
            debtor = debtors_by_internal.get(client_internal)
            if debtor:
                customer_name = debtor["name"]
                customer_phone = debtor["phone"]

            # ── Insert sale row with ORIGINAL timestamp ─────────────────
            invoice_no = f"IMP-{unqcode}"
            client_uuid = str(uuid.uuid4())
            # PR 6: use write_tx() (BEGIN IMMEDIATE) instead of conn() so the
            # per-sale writes (sale row + sale_items + cash_drawer + stock_state
            # + customer stats + ezi_pos_imports) all commit atomically. If any
            # step fails, the entire sale rolls back — no half-committed sales.
            with write_tx() as c:
                # Double-check by invoice_no (extra safety)
                existing_sale = c.execute(
                    "SELECT id FROM sales WHERE invoice_no=?", (invoice_no,)
                ).fetchone()
                if existing_sale:
                    skipped_duplicates += 1
                    continue

                sale_id = c.execute(
                    "INSERT INTO sales(invoice_no, customer_name, customer_phone, "
                    "customer_id, subtotal, total, discount, tax_rate, tax_amount, "
                    "payment_method, payment_status, employee_id, created_at, client_uuid) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (invoice_no, customer_name, customer_phone, customer_id,
                     round(total_amount, 2), round(total_amount, 2), 0,
                     0, round(inv.get("tax", 0) or 0, 2),
                     payment_method, "credit" if is_credit else "paid", 1,
                     created_at_str, client_uuid),
                ).lastrowid

                # ── Insert sale_items ───────────────────────────────────
                # v8.5: prefer INVTRANS.DBF (real line items with COST field)
                # over ACCTRANS.SI (which only has sale-level totals).
                sale_items_inserted = []
                unknown_cost_items = 0
                inv_lines = line_items_by_unqcode.get(unqcode, [])
                if inv_lines:
                    # ── v8.5 path: real line items from INVTRANS.DBF ────
                    for ln in inv_lines:
                        item_name = ln["details"] or "Imported POS Item"
                        qty = ln["qty"] if ln["qty"] else 1
                        line_total = ln["amount"]
                        # Use INVTRANS.COST as the real per-unit cost at sale time
                        # This is the COGS — no need for peek_avg_cost fallback
                        cost_price = ln["cost"]
                        # Resolve category via item master
                        master_entry = item_master.get(ln["internal"], {})
                        category_id = None
                        if master_entry:
                            category_id = _resolve_ezi_item_to_category(
                                master_entry, bags_category_id,
                                bag_internal_to_category=bag_internal_to_category,
                            )
                        # Item code: use part_no from INVTRANS or from master
                        item_code = ln["part_no"] or master_entry.get("part_no", "")
                        # Sell price: per-unit, derived from amount / qty
                        sell_price = line_total / qty if qty > 0 else line_total
                        if category_id is None:
                            unknown_cost_items += 1

                        c.execute(
                            "INSERT INTO sale_items(sale_id, item_name, category_id, "
                            "category_code, cost_price, sell_price, qty, line_total) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (sale_id, item_name[:200], category_id, item_code,
                             round(cost_price, 2), round(sell_price, 2), qty, round(line_total, 2)),
                        )
                        sale_items_inserted.append({
                            "category_id": category_id,
                            "qty": qty,
                            "item_name": item_name,
                        })
                        total_cogs += cost_price * qty
                else:
                    # ── Legacy path: ACCTRANS.SI records (fallback when no INVTRANS) ──
                    for si in si_recs:
                        item_name = ""
                        for key in ("ITEMNAME", "ITEM_NAME", "DESC", "DESCRIPTION",
                                    "PARTICULAR", "NARRATION", "DETAILS"):
                            if si.get(key):
                                item_name = str(si[key])[:200]
                                break
                        if not item_name:
                            item_name = "Imported POS Item"

                        qty = 1
                        for key in ("QTY", "QUANTITY", "QTY1"):
                            v = si.get(key)
                            if v:
                                try:
                                    qty = float(v)
                                    if qty == int(qty):
                                        qty = int(qty)
                                    break
                                except (ValueError, TypeError):
                                    pass

                        price = 0
                        for key in ("RATE", "PRICE", "RATE1", "SELL_PRICE"):
                            v = si.get(key)
                            if v:
                                try:
                                    price = float(v)
                                    break
                                except (ValueError, TypeError):
                                    pass

                        line_total = 0
                        for key in ("AMOUNT", "LINE_TOTAL", "TOTAL"):
                            v = si.get(key)
                            if v:
                                try:
                                    line_total = float(v)
                                    break
                                except (ValueError, TypeError):
                                    pass
                        if line_total == 0:
                            line_total = price * qty

                        item_code = ""
                        for key in ("CATCODE", "CODE", "ITEM_CODE", "ICODE"):
                            v = si.get(key)
                            if v:
                                item_code = str(v)
                                break

                        # Category fuzzy-match (Part 3.2)
                        category_id = _match_import_item_to_category(item_name, item_code)

                        # Cost price (peek_avg_cost_as_of if matched, else 0)
                        # v8.16.13: use peek_avg_cost_as_of(sale_date) instead of peek_avg_cost
                        # so cost_price reflects the avg cost AT TIME OF SALE, not today's.
                        # This makes re-importing backups produce the same cost_price each time
                        # (was: re-import would recompute using today's avg cost → wrong margins).
                        cost_price = 0.0
                        if category_id:
                            from .profit_engine import peek_avg_cost_as_of
                            with conn() as c2:
                                cost_price = peek_avg_cost_as_of(c2, category_id, txn_date_str) or 0.0
                        else:
                            unknown_cost_items += 1

                        c.execute(
                            "INSERT INTO sale_items(sale_id, item_name, category_id, "
                            "category_code, cost_price, sell_price, qty, line_total) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (sale_id, item_name, category_id, item_code,
                             round(cost_price, 2), round(price, 2), qty, round(line_total, 2)),
                        )
                        sale_items_inserted.append({
                            "category_id": category_id,
                            "qty": qty,
                            "item_name": item_name,
                        })
                        total_cogs += cost_price * qty

                # ── If no line items at all but we have SP → synthetic summary item
                if not sale_items_inserted and sp_recs:
                    # Try to match the DETAILS text (e.g. "C - Cash Sales" → category 'C')
                    details = sp_recs[0].get("DETAILS", "") or ""
                    cat_code_hint = ""
                    if details and details[0:1].isalpha():
                        cat_code_hint = details[0:1].upper()
                    category_id = None
                    if cat_code_hint:
                        category_id = _match_import_item_to_category("", cat_code_hint)
                    cost_price = 0.0
                    if category_id:
                        # v8.16.13: use historical avg cost based on sale date
                        from .profit_engine import peek_avg_cost_as_of
                        with conn() as c2:
                            cost_price = peek_avg_cost_as_of(c2, category_id, txn_date_str) or 0.0
                    else:
                        unknown_cost_items += 1
                    c.execute(
                        "INSERT INTO sale_items(sale_id, item_name, category_id, "
                        "category_code, cost_price, sell_price, qty, line_total) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (sale_id, "Imported POS Sale", category_id, cat_code_hint,
                         round(cost_price, 2), round(total_amount, 2), 1,
                         round(total_amount, 2)),
                    )
                    sale_items_inserted.append({
                        "category_id": category_id, "qty": 1,
                        "item_name": "Imported POS Sale",
                    })
                    total_cogs += cost_price

                # ── Insert cash_drawer entry (cash sales only) ──────────
                # CRITICAL: use the ORIGINAL sale timestamp, not datetime('now')
                if payment_method == "cash" and not is_credit:
                    c.execute(
                        "INSERT INTO cash_drawer(type, amount, description, "
                        "reference_type, reference_id, created_at) "
                        "VALUES('sale', ?, ?, 'sale', ?, ?)",
                        (round(total_amount, 2), f"Imported: {invoice_no}",
                         sale_id, created_at_str),
                    )

                # ── Record UNQCODE in ezi_pos_imports ────────────────────
                c.execute(
                    "INSERT OR IGNORE INTO ezi_pos_imports(unqcode, import_date, "
                    "sale_id, txn_date, amount, source, import_run_id) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (unqcode, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     sale_id, txn_date_str, round(total_amount, 2), "ezi_pos", run_id),
                )

                # ── PR 6: Apply sale to inventory state INSIDE the txn ──────
                # Was previously done OUTSIDE the txn (each apply_sale_to_state
                # opened its own write_tx). Now uses c=c to share the connection
                # — if the sale INSERT rolls back, the stock_state mutation
                # rolls back too (no orphaned stock reductions).
                from .profit_engine import apply_sale_to_state
                for item in sale_items_inserted:
                    if item["category_id"]:
                        # v8.19.1: bag categories never take the sale decrement
                        # here — their stock tracks "qty sold" instead (bag
                        # purchases are expenses, not bills). The end-of-import
                        # sync_bags_stock_to_sold() raises them to total sold.
                        if item["category_id"] in bags_stock_ids:
                            continue
                        try:
                            apply_sale_to_state(
                                item["category_id"], item["qty"],
                                txn_at=created_at_str, c=c,
                            )
                        except Exception as e:
                            warnings_list.append(
                                f"Failed to apply sale to state for sale {sale_id} "
                                f"category {item['category_id']}: {e}"
                            )
                    else:
                        warnings_list.append(
                            f"Skipping stock update for imported sale {sale_id} "
                            f"({invoice_no}) — no category mapping for item "
                            f"'{item['item_name'][:50]}'"
                        )

                # ── PR 6: Update customer stats INSIDE the txn (inlined) ──
                # Was previously calling shop.update_customer_stats() which
                # opens its own connection (would deadlock against our
                # BEGIN IMMEDIATE). Inlined the same logic here.
                if customer_id:
                    try:
                        per_rs_row = c.execute(
                            "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
                        ).fetchone()
                        per_rs = float(per_rs_row["value"] or "100") if per_rs_row else 100.0
                        per_rs = per_rs if per_rs > 0 else 100.0
                        pts = int(total_amount / per_rs)
                        if is_credit:
                            c.execute(
                                "UPDATE customers SET total_credit = total_credit + ?, "
                                "loyalty_points = loyalty_points + ? WHERE id=?",
                                (total_amount, pts, customer_id),
                            )
                        else:
                            c.execute(
                                "UPDATE customers SET total_spent = total_spent + ?, "
                                "loyalty_points = loyalty_points + ? WHERE id=?",
                                (total_amount, pts, customer_id),
                            )
                    except Exception as e:
                        warnings_list.append(
                            f"Failed to update customer stats for customer_id={customer_id}: {e}"
                        )

            sales_imported += 1
            payments_imported += 1 if sp_recs else 0
            sales_by_date[txn_date_str] = sales_by_date.get(txn_date_str, 0) + total_amount

            if unknown_cost_items > 0:
                warnings_list.append(
                    f"Imported sale {invoice_no}: {unknown_cost_items} item(s) had "
                    f"unknown category — cost_price set to 0, margins may be inflated."
                )

        # ── Parse DIARY.DBF (expenses) ────────────────────────────────────
        expenses_imported = 0
        expenses_updated = 0  # v8.16.8: count expenses whose amount/description changed
        diary_path = os.path.join(temp_dir, "DIARY.DBF")
        if os.path.exists(diary_path):
            for rec in DBF(diary_path):
                details = rec.get("DETAILS", "") or ""
                if not details:
                    continue
                # v8.16.1 FIX: Ezi POS DIARY.DBF format is "AMOUNT = DESCRIPTION"
                # (e.g. "2000 = Ishfaq Advance Salary"). The old code was parsing
                # the amount from AFTER the = (the description side), which always
                # failed the float() conversion → expenses were silently skipped.
                #
                # The new code tries BOTH patterns:
                #   "2000 = Ishfaq Advance Salary"  → amount=2000, desc="Ishfaq Advance Salary"
                #   "Ishfaq = 2000"                  → amount=2000, desc="Ishfaq"
                #   "250 Air Freshener"              → amount=250, desc="Air Freshener"
                amount = 0
                description = details  # default: full details as description
                if "=" in details:
                    parts = details.split("=", 1)
                    left = parts[0].strip()
                    right = parts[1].strip()
                    # Try left side first (Ezi POS format: "2000 = Description")
                    try:
                        amount = float(left.replace(",", "").split()[0])
                        description = right
                    except (ValueError, IndexError):
                        # Try right side (reverse format: "Description = 2000")
                        try:
                            amount = float(right.replace(",", "").split()[0])
                            description = left
                        except (ValueError, IndexError):
                            pass
                if amount <= 0:
                    # No = sign — try to extract amount from the beginning
                    # Pattern: "250 Air Freshener" or "Less Than 1000, August Expense"
                    try:
                        first_word = details.strip().split()[0]
                        amount = float(first_word.replace(",", ""))
                        description = " ".join(details.strip().split()[1:]) or details
                    except (ValueError, IndexError):
                        pass
                if amount <= 0:
                    # Still no amount — skip this record
                    continue
                # MD5 hash for dedup (deterministic across restarts)
                diary_hash = hashlib.md5(
                    (details + str(rec.get("DATE", ""))).encode()
                ).hexdigest()
                # v8.16.8: source_checksum — used to detect amount/description/date modifications
                # between imports. Different from import_hash (which only changes if DETAILS+DATE
                # string changes — won't catch amount-only edits). Source_checksum includes amount.
                source_checksum = hashlib.md5(
                    f"{details}|{amount}|{rec.get('DATE','')}".encode()
                ).hexdigest()
                with conn() as c:
                    existing_exp = c.execute(
                        "SELECT id, source_checksum, expense_id, amount, description "
                        "FROM pos_expense_imports WHERE import_hash=?",
                        (diary_hash,)
                    ).fetchone()
                    if existing_exp:
                        # v8.16.8: Even if we already imported this expense, the AMOUNT may have
                        # changed in EZI POS. If source_checksum differs, update the expense + cash_drawer.
                        if existing_exp["source_checksum"] != source_checksum:
                            old_amount = float(existing_exp["amount"] or 0)
                            new_amount = float(amount)
                            amount_diff = new_amount - old_amount
                            # Update pos_expense_imports.source_checksum
                            c.execute(
                                "UPDATE pos_expense_imports SET source_checksum=?, "
                                "amount=?, description=? WHERE import_hash=?",
                                (source_checksum, amount, description[:500], diary_hash),
                            )
                            # Update the linked expense row
                            linked_expense_id = existing_exp["expense_id"]
                            if linked_expense_id:
                                c.execute(
                                    "UPDATE expenses SET amount=?, description=? WHERE id=?",
                                    (amount, description[:500], linked_expense_id),
                                )
                                # Insert a cash_drawer adjustment for the amount difference
                                # If amount increased (diff>0), more cash went out (negative).
                                # If amount decreased (diff<0), some cash came back (positive).
                                if abs(amount_diff) > 0.01:
                                    c.execute(
                                        "INSERT INTO cash_drawer(type, amount, description, "
                                        "reference_type, reference_id, created_at) "
                                        "VALUES('expense_adjustment', ?, ?, 'expense', NULL, ?)",
                                        (-amount_diff,  # negative if increase, positive if decrease
                                         f"Adjustment: expense '{description[:40]}' updated "
                                         f"from Rs {old_amount:.2f} to Rs {new_amount:.2f}",
                                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                                    )
                                # Log the activity
                                c.execute(
                                    "INSERT INTO activity_log(event_type, entity_type, entity_id, "
                                    "description, metadata, created_at) "
                                    "VALUES('expense_updated', 'expense', ?, ?, ?, datetime('now','localtime'))",
                                    (linked_expense_id,
                                     f"Expense updated via POS import: {description[:60]} "
                                     f"(Rs {old_amount:.2f} → Rs {new_amount:.2f})",
                                     json.dumps({"old_amount": old_amount, "new_amount": new_amount,
                                                 "diff": amount_diff, "expense_id": linked_expense_id})),
                                )
                                expenses_updated += 1
                        continue
                    diary_date_str = _normalize_date(rec.get("DATE"))
                    # v8.16.1: Use parsed description (without the amount) instead of full details
                    c.execute(
                        "INSERT INTO expenses(category, description, amount, date, "
                        "payment_method, created_at) VALUES(?,?,?,?,?,?)",
                        ("Other", description[:500], amount, diary_date_str, "cash",
                         diary_date_str + " 00:00:00"),
                    )
                    # v8.16.7: capture the expense_id so we can sync deletions later
                    new_expense_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                    c.execute(
                        "INSERT INTO pos_expense_imports(import_hash, description, "
                        "amount, date, import_date, import_run_id, expense_id, "
                        "source_checksum, checksum_initialized_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (diary_hash, description[:500], amount, diary_date_str,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), run_id, new_expense_id,
                         source_checksum,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                    # Cash drawer negative entry for cash expenses
                    c.execute(
                        "INSERT INTO cash_drawer(type, amount, description, "
                        "reference_type, created_at) "
                        "VALUES('expense', ?, ?, 'expense', ?)",
                        (-abs(amount), f"Imported expense: {description[:50]}",
                         diary_date_str + " 12:00:00"),
                    )
                expenses_imported += 1

        # ── Determine backup date from filename or data ───────────────────
        # BU20260813.zip → 2026-08-13
        backup_date = ""
        if backup_filename.upper().startswith("BU") and len(backup_filename) >= 10:
            try:
                d = backup_filename[2:10]
                backup_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            except Exception as _e:
                logger.warning("Silent exception in pos_import_sync.py: %s", _e, exc_info=True)
        if not backup_date and backup_dates:
            backup_date = max(backup_dates)

        date_start = min(backup_dates) if backup_dates else backup_date
        date_end = max(backup_dates) if backup_dates else backup_date

        # ── Finalize the import run ──────────────────────────────────────
        total_sales_amount = round(sum(sales_by_date.values()), 2)

        # v8.19.1: Bags rule — after all sales are in, raise every bag
        # category's stock to its total qty sold (never downward — see
        # profit_engine.sync_bags_stock_to_sold). Runs even when this import
        # added no new bag sales, so the first import after upgrading heals
        # legacy negative bag stocks too.
        try:
            from .profit_engine import sync_bags_stock_to_sold as _sync_bags
            bags_stock_synced = _sync_bags()
        except Exception as _e:
            logger.warning("bags stock sync failed: %s", _e, exc_info=True)
            bags_stock_synced = []

        notes_parts = []
        if any("unknown category" in w for w in warnings_list):
            notes_parts.append(f"{sum(1 for w in warnings_list if 'unknown category' in w)} sales had unknown cost")
        if any("margins may be inflated" in w for w in warnings_list):
            notes_parts.append("margins may be inflated")
        notes_str = "; ".join(notes_parts) if notes_parts else ""

        _finalize_import_run(
            run_id, sales_imported, expenses_imported,
            total_sales_amount, total_cogs,
            date_start, date_end, status="imported", notes=notes_str,
        )

        # ── Log the import ────────────────────────────────────────────────
        log_activity(
            "pos_backup_imported", "pos_import", run_id,
            f"Imported POS backup {backup_filename}: {sales_imported} sales, "
            f"{expenses_imported} new expenses, {expenses_updated} updated, "
            f"{skipped_duplicates} duplicates skipped",
            {"backup_file": backup_filename, "backup_date": backup_date,
             "sales_imported": sales_imported, "expenses_imported": expenses_imported,
             "expenses_updated": expenses_updated,
             "skipped": skipped_duplicates, "shop_name": shop_name,
             "total_sales_amount": total_sales_amount, "import_run_id": run_id,
             "bags_stock_synced": bags_stock_synced},
        )

        return {
            "import_run_id": run_id,
            "imported_sales": sales_imported,
            "imported_payments": payments_imported,
            "imported_expenses": expenses_imported,
            "updated_expenses": expenses_updated,  # v8.16.8
            "skipped_duplicates": skipped_duplicates,
            "backup_date": backup_date,
            "backup_file": backup_filename,
            "shop_name": shop_name,
            "sales_by_date": {d: round(v, 2) for d, v in sorted(sales_by_date.items())},
            "total_sales_amount": total_sales_amount,
            "total_cogs": round(total_cogs, 2),
            "warnings": warnings_list[:50],  # cap to avoid huge payloads
            "warning_count": len(warnings_list),
            # v8.19.1: bag categories whose stock was raised to total sold
            "bags_stock_synced": bags_stock_synced,
            # PR 6: signal to the UI that a rebuild is needed on next boot
            "stock_state_dirty": True,
            "rebuild_required": True,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ─── History + Summary ────────────────────────────────────────────────────

def get_pos_import_history(limit: int = 20) -> list:
    """Get history of imported transactions (ledger view)."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM ezi_pos_imports ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_pos_import_summary() -> dict:
    """Get a summary of all POS imports."""
    with conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM ezi_pos_imports").fetchone()["n"]
        total_amount = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS v FROM ezi_pos_imports"
        ).fetchone()["v"]
        imports = c.execute(
            "SELECT * FROM activity_log WHERE event_type='pos_backup_imported' "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        # v8.5: also return pos_imports run records (cleaner than activity_log)
        runs = c.execute(
            "SELECT id, source_name, filename, file_format, row_count, sale_count, "
            "expense_count, total_revenue, total_cogs, date_range_start, date_range_end, "
            "import_date, status, notes, created_at "
            "FROM pos_imports ORDER BY id DESC LIMIT 20"
        ).fetchall()
    return {
        "total_imported_records": total,
        "total_imported_amount": round(float(total_amount or 0), 2),
        "recent_imports": [dict(r) for r in imports],
        "import_runs": [dict(r) for r in runs],
    }


# ─── Delete / Rollback (Part 5) ────────────────────────────────────────────

def delete_pos_import(import_run_id: int) -> dict:
    """v8.5: Delete an import run and reverse ALL its side effects.

    Uses `import_run_id` (not date-prefix matching, which was fragile)
    to find exactly the records created by this run.

    Two-pass strategy:
      PASS 1 (read-only): collect (category_id, qty) pairs from sale_items
                          and (customer_id, total, is_credit) from sales
                          BEFORE deleting anything.
      PASS 2 (write):    - Apply stock reversal (apply_adjustment_to_state
                          opens its own conn, so do this BEFORE the delete
                          transaction).
                          - Then in a single transaction: reverse customer
                          stats, delete cash_drawer entries, sale_items,
                          sales, expenses, ledger rows; update pos_imports.
    """
    # ── PASS 1: read-only collection ─────────────────────────────────────
    with conn() as c:
        run = c.execute(
            "SELECT id, filename, status FROM pos_imports WHERE id=?",
            (import_run_id,)
        ).fetchone()
        if not run:
            # Fallback: maybe caller passed activity_log.id (legacy)
            log_row = c.execute(
                "SELECT metadata FROM activity_log WHERE id=? AND event_type='pos_backup_imported'",
                (import_run_id,)
            ).fetchone()
            if not log_row:
                return {"ok": False, "error": "Import run not found"}
            meta = json.loads(log_row["metadata"] or "{}")
            real_run_id = meta.get("import_run_id")
            if not real_run_id:
                return {"ok": False, "error": "Legacy import has no import_run_id"}
            # Recurse with the real run id
            return delete_pos_import(real_run_id)
        if run["status"] == "deleted":
            return {"ok": False, "error": "Import run already deleted"}
        backup_filename = run["filename"]

        sale_rows = c.execute(
            "SELECT sale_id FROM ezi_pos_imports "
            "WHERE import_run_id=? AND sale_id IS NOT NULL",
            (import_run_id,)
        ).fetchall()
        sale_ids = [r["sale_id"] for r in sale_rows]

        items_to_reverse = []  # list of (category_id, qty)
        customer_reversals = []  # list of (customer_id, total, is_credit)
        # v8.19.1: bag categories never took the sale decrement on import
        # (their stock tracks "qty sold" instead) — reversing them here would
        # double-bump the stock. Exclude them from the reversal.
        from .profit_engine import bag_category_ids as _bag_category_ids
        bags_stock_ids = _bag_category_ids(c)
        for sale_id in sale_ids:
            for it in c.execute(
                "SELECT category_id, qty FROM sale_items WHERE sale_id=?",
                (sale_id,)
            ).fetchall():
                if it["category_id"]:
                    if int(it["category_id"]) in bags_stock_ids:
                        continue  # bag category — stock tracks sold, no reversal
                    try:
                        items_to_reverse.append((int(it["category_id"]), float(it["qty"])))
                    except (TypeError, ValueError):
                        pass
            sale = c.execute(
                "SELECT customer_id, total, payment_status FROM sales WHERE id=?",
                (sale_id,)
            ).fetchone()
            if sale and sale["customer_id"]:
                customer_reversals.append((
                    int(sale["customer_id"]),
                    float(sale["total"] or 0),
                    sale["payment_status"] == "credit",
                ))
        exp_rows = c.execute(
            "SELECT description, amount, date FROM pos_expense_imports "
            "WHERE import_run_id=?",
            (import_run_id,)
        ).fetchall()

    # ── PASS 2a: reverse stock state (opens own connection) ──────────────
    from .profit_engine import apply_adjustment_to_state
    stock_reversed = 0
    for category_id, qty in items_to_reverse:
        try:
            apply_adjustment_to_state(
                category_id, +qty,
                txn_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            stock_reversed += 1
        except Exception as e:
            logger.warning("Stock reversal failed for category %s: %s", category_id, e)

    # ── PASS 2b: customer reversals + deletes in one transaction ────────
    with conn() as c:
        deleted_sales = 0
        deleted_items = 0
        deleted_cash = 0
        customer_reversal_count = 0

        per_rs_row = c.execute(
            "SELECT value FROM settings WHERE key='loyalty_points_per_rs'"
        ).fetchone()
        per_rs = float(per_rs_row["value"]) if per_rs_row and per_rs_row["value"] else 100.0
        for customer_id, total, is_credit in customer_reversals:
            if is_credit:
                c.execute(
                    "UPDATE customers SET total_credit = MAX(0, total_credit - ?) "
                    "WHERE id=?", (total, customer_id)
                )
            else:
                c.execute(
                    "UPDATE customers SET total_spent = MAX(0, total_spent - ?) "
                    "WHERE id=?", (total, customer_id)
                )
            if per_rs > 0:
                pts = int(total / per_rs)
                c.execute(
                    "UPDATE customers SET loyalty_points = MAX(0, loyalty_points - ?) "
                    "WHERE id=?", (pts, customer_id)
                )
            customer_reversal_count += 1

        if sale_ids:
            placeholders = ",".join("?" * len(sale_ids))
            deleted_items = c.execute(
                f"DELETE FROM sale_items WHERE sale_id IN ({placeholders})",
                sale_ids
            ).rowcount
            deleted_cash = c.execute(
                f"DELETE FROM cash_drawer WHERE reference_id IN ({placeholders}) "
                f"AND reference_type='sale'",
                sale_ids
            ).rowcount
            deleted_sales = c.execute(
                f"DELETE FROM sales WHERE id IN ({placeholders})",
                sale_ids
            ).rowcount

        deleted_expenses_imports = 0
        for er in exp_rows:
            c.execute(
                "DELETE FROM expenses WHERE date=? AND amount=? AND description LIKE ?",
                (er["date"], er["amount"], er["description"][:100] + "%")
            )
            c.execute(
                "DELETE FROM cash_drawer WHERE type='expense' AND amount=? "
                "AND date(created_at)=?",
                (-abs(er["amount"]), er["date"])
            )
            deleted_expenses_imports += 1

        c.execute(
            "DELETE FROM ezi_pos_imports WHERE import_run_id=?",
            (import_run_id,)
        )
        c.execute(
            "DELETE FROM pos_expense_imports WHERE import_run_id=?",
            (import_run_id,)
        )
        c.execute(
            "UPDATE pos_imports SET status='deleted' WHERE id=?",
            (import_run_id,)
        )

    # ── Log the deletion (separate connection, after commit) ────────────
    log_activity(
        "pos_import_deleted", "pos_import", import_run_id,
        f"Deleted POS import #{import_run_id} ({backup_filename}): "
        f"{deleted_sales} sales, {deleted_items} items, {deleted_cash} cash entries, "
        f"{deleted_expenses_imports} expense imports, {stock_reversed} stock reversals, "
        f"{customer_reversal_count} customer reversals",
        {"import_run_id": import_run_id, "backup_file": backup_filename,
         "deleted_sales": deleted_sales, "deleted_items": deleted_items,
         "deleted_cash_entries": deleted_cash,
         "deleted_expense_imports": deleted_expenses_imports,
         "stock_reversed": stock_reversed,
         "customer_reversals": customer_reversal_count},
    )

    return {
        "ok": True,
        "import_run_id": import_run_id,
        "backup_file": backup_filename,
        "deleted_sales": deleted_sales,
        "deleted_items": deleted_items,
        "deleted_cash_entries": deleted_cash,
        "deleted_expense_imports": deleted_expenses_imports,
        "stock_reversed": stock_reversed,
        "customer_reversals": customer_reversal_count,
    }


def delete_pos_import_by_activity_log_id(activity_log_id: int) -> dict:
    """Legacy entry point: callers used to pass activity_log.id.
    v8.5: resolve to import_run_id and delegate.
    """
    with conn() as c:
        log_row = c.execute(
            "SELECT metadata FROM activity_log WHERE id=? AND event_type='pos_backup_imported'",
            (activity_log_id,)
        ).fetchone()
    if not log_row:
        return {"ok": False, "error": "Import record not found"}
    meta = json.loads(log_row["metadata"] or "{}")
    run_id = meta.get("import_run_id")
    if not run_id:
        return {"ok": False, "error": "Legacy import has no import_run_id"}
    return delete_pos_import(run_id)


# ════════════════════════════════════════════════════════════════════════════════
# v8.11 Phase 4: Deleted-Sale Detection (dry-run + confirmation model)
# ════════════════════════════════════════════════════════════════════════════════

def detect_deleted_sales(new_backup_unqcodes: set) -> dict:
    """After importing a new backup, find sales that exist in BillBook
    but are NOT in the new backup → they were deleted in the source POS.

    Returns a DRY-RUN summary (does NOT apply any changes).

    Safety measures (per reviewer feedback):
    - Only checks UNQCODEs that were previously imported (exist in ezi_pos_imports)
    - Skips already-synced deletions (synced_deleted=1)
    - Skips already-refunded sales
    - Skips manually-overridden sales (creates a conflict entry instead)
    - Threshold check: if missing_count > 5% of total imported, flags as high_risk

    Returns:
        {missing_sales: [...], missing_count, missing_total_amount,
         conflicts: [...], conflict_count,
         already_synced: int, already_refunded: int,
         high_risk: bool, threshold_exceeded: bool}
    """
    if not new_backup_unqcodes:
        return {"missing_sales": [], "missing_count": 0, "missing_total_amount": 0,
                "conflicts": [], "conflict_count": 0,
                "already_synced": 0, "already_refunded": 0,
                "high_risk": False, "threshold_exceeded": False}

    with conn() as c:
        # Get all previously-imported UNQCODEs that are NOT synced_deleted
        existing = c.execute(
            "SELECT ezi.unqcode, ezi.sale_id, ezi.import_run_id, "
            "s.payment_status, s.total, s.invoice_no, s.customer_name, "
            "s.created_at, s.payment_method, s.manually_overridden "
            "FROM ezi_pos_imports ezi "
            "JOIN sales s ON ezi.sale_id = s.id "
            "WHERE ezi.synced_deleted = 0 "
            "ORDER BY ezi.unqcode"
        ).fetchall()

        total_imported = len(existing)
        missing_sales = []
        conflicts = []
        already_synced = 0
        already_refunded = 0

        for row in existing:
            unqcode = row["unqcode"]
            if unqcode in new_backup_unqcodes:
                continue  # Still in the backup — not deleted

            # This sale is missing from the new backup
            if row["payment_status"] == "refunded":
                already_refunded += 1
                # Mark as synced_deleted without reversing (already refunded)
                # (will be applied in the apply step)
                missing_sales.append({
                    "unqcode": unqcode,
                    "sale_id": row["sale_id"],
                    "invoice_no": row["invoice_no"],
                    "total": float(row["total"] or 0),
                    "payment_status": row["payment_status"],
                    "already_refunded": True,
                    "created_at": row["created_at"],
                })
                continue

            if row["manually_overridden"]:
                # Conflict — manually overridden in BillBook
                conflicts.append({
                    "unqcode": unqcode,
                    "sale_id": row["sale_id"],
                    "invoice_no": row["invoice_no"],
                    "total": float(row["total"] or 0),
                    "reason": "manually_overridden",
                    "detail": "Sale was manually edited/voided in BillBook. "
                              "Source POS deletion may conflict with local changes.",
                })
                continue

            # Valid missing sale — candidate for auto-refund
            missing_sales.append({
                "unqcode": unqcode,
                "sale_id": row["sale_id"],
                "invoice_no": row["invoice_no"],
                "total": float(row["total"] or 0),
                "payment_status": row["payment_status"],
                "payment_method": row["payment_method"],
                "customer_name": row["customer_name"],
                "already_refunded": False,
                "created_at": row["created_at"],
            })

        missing_count = len(missing_sales)
        missing_total = sum(s["total"] for s in missing_sales)
        threshold_pct = 5  # 5% threshold
        threshold_exceeded = (total_imported > 0 and
                              (missing_count / total_imported * 100) > threshold_pct)

    return {
        "missing_sales": missing_sales,
        "missing_count": missing_count,
        "missing_total_amount": round(missing_total, 2),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "already_synced": already_synced,
        "already_refunded": already_refunded,
        "high_risk": threshold_exceeded,
        "threshold_exceeded": threshold_exceeded,
        "threshold_pct": threshold_pct,
        "total_imported": total_imported,
    }


def apply_deleted_sales_sync(missing_sales: list, import_run_id: int) -> dict:
    """Apply the deletion sync — reverses side effects for each missing sale.

    Called AFTER the user confirms the dry-run summary.
    Each sale is processed in its own write_tx() for isolation.

    For each missing sale:
    - If already_refunded: just mark synced_deleted=1 (no reversal needed)
    - If active: call _reverse_sale_core() to reverse stock, customer stats,
      cash drawer, commission, loyalty. Then mark as refunded + synced_deleted=1.

    Returns: {applied: int, skipped: int, errors: [...]}
    """
    from .routers.pos import _reverse_sale_core
    from .db import write_tx, log_activity

    applied = 0
    skipped = 0
    errors = []

    for ms in missing_sales:
        sale_id = ms["sale_id"]
        unqcode = ms["unqcode"]
        try:
            with write_tx() as c:
                if ms.get("already_refunded"):
                    # Already refunded — just mark as synced
                    c.execute(
                        "UPDATE ezi_pos_imports SET synced_deleted=1, "
                        "deleted_sync_at=datetime('now','localtime') "
                        "WHERE unqcode=?", (unqcode,)
                    )
                    applied += 1
                    continue

                # Reverse all side effects using the canonical core function
                _reverse_sale_core(sale_id, c, reason="source POS deletion sync")

                # Mark sale as refunded
                c.execute(
                    "UPDATE sales SET payment_status='refunded', "
                    "refunded_at=datetime('now','localtime'), "
                    "refund_reason='source_pos_deleted' "
                    "WHERE id=?", (sale_id,)
                )

                # Mark ezi_pos_imports as synced
                c.execute(
                    "UPDATE ezi_pos_imports SET synced_deleted=1, "
                    "deleted_sync_at=datetime('now','localtime') "
                    "WHERE unqcode=?", (unqcode,)
                )

                log_activity(
                    "pos_sale_synced_deleted", "sale", sale_id,
                    f"Sale {ms['invoice_no']} auto-refunded (deleted in source POS)",
                    {"unqcode": unqcode, "import_run_id": import_run_id,
                     "total": ms["total"]},
                    c=c,
                )
                applied += 1
        except Exception as e:
            errors.append({"unqcode": unqcode, "sale_id": sale_id, "error": str(e)})
            skipped += 1

    return {"applied": applied, "skipped": skipped, "errors": errors}


def compute_sale_checksum(invoice_row: dict, line_items: list) -> str:
    """v8.11 Phase 6: Compute a SHA-256 checksum of a sale's header + line items.

    Uses Decimal-normalized string representation (not float) to avoid
    checksum instability from floating-point representation differences.

    Includes:
    - Header: amount, paid, status, date, client, payment_method
    - Lines (sorted): item_code, qty, amount, rate, category_code
    """
    import hashlib, json
    from decimal import Decimal

    def normalize_amount(value):
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01")))

    payload = {
        "amount": normalize_amount(invoice_row.get("amount")),
        "paid": normalize_amount(invoice_row.get("paid")),
        "status": str(invoice_row.get("status", "")),
        "date": str(invoice_row.get("date", "")),
        "client": str(invoice_row.get("client", "")),
        "lines": sorted([
            {
                "item_code": str(line.get("PART_NO", line.get("item_code", ""))),
                "qty": normalize_amount(line.get("QTY", line.get("qty", 0))),
                "amount": normalize_amount(line.get("AMOUNT", line.get("amount", 0))),
            }
            for line in line_items
        ], key=lambda x: (x["item_code"], x["qty"], x["amount"])),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def detect_modified_sales(invoice_by_unqcode: dict, line_items_by_unqcode: dict) -> dict:
    """v8.11 Phase 6: Detect sales that were modified in the source POS.

    For each UNQCODE that exists in BillBook AND in the new backup:
    1. Compute new checksum from backup's INVOICE.DBF + INVTRANS.DBF rows
    2. Compare against stored source_checksum
    3. If NULL → set checksum, skip (initialization — NOT a modification)
    4. If different → modification candidate

    Returns a DRY-RUN summary (does NOT apply any changes).

    Safety:
    - Skips already-refunded sales (conflict)
    - Skips manually_overridden sales (conflict)
    - Skips synced_deleted sales (conflict)
    """
    with conn() as c:
        existing = c.execute(
            "SELECT ezi.unqcode, ezi.sale_id, ezi.source_checksum, "
            "ezi.checksum_initialized_at, "
            "s.payment_status, s.total, s.invoice_no, s.manually_overridden, "
            "s.refund_reason "
            "FROM ezi_pos_imports ezi "
            "JOIN sales s ON ezi.sale_id = s.id "
            "WHERE ezi.synced_deleted = 0 "
            "ORDER BY ezi.unqcode"
        ).fetchall()

        modifications = []
        conflicts = []
        initialized = 0
        unchanged = 0

        for row in existing:
            unqcode = row["unqcode"]
            if unqcode not in invoice_by_unqcode:
                continue  # Not in backup → handled by deleted-sale detection

            inv = invoice_by_unqcode[unqcode]
            lines = line_items_by_unqcode.get(unqcode, [])
            new_checksum = compute_sale_checksum(inv, lines)
            stored_checksum = row["source_checksum"]

            # Safe checksum initialization (NULL = skip + set)
            if stored_checksum is None:
                # First time — set checksum, don't flag as modified
                c.execute(
                    "UPDATE ezi_pos_imports SET source_checksum=?, "
                    "checksum_initialized_at=datetime('now','localtime') "
                    "WHERE unqcode=?",
                    (new_checksum, unqcode,)
                )
                initialized += 1
                continue

            if stored_checksum == new_checksum:
                unchanged += 1
                continue

            # Checksum changed → modification candidate
            if row["payment_status"] == "refunded":
                conflicts.append({
                    "unqcode": unqcode,
                    "sale_id": row["sale_id"],
                    "invoice_no": row["invoice_no"],
                    "reason": "already_refunded",
                    "detail": "Sale was modified in source POS but is already refunded in BillBook.",
                })
                continue

            if row["manually_overridden"]:
                conflicts.append({
                    "unqcode": unqcode,
                    "sale_id": row["sale_id"],
                    "invoice_no": row["invoice_no"],
                    "reason": "manually_overridden",
                    "detail": "Sale was manually edited in BillBook. Source POS modification may conflict.",
                })
                continue

            modifications.append({
                "unqcode": unqcode,
                "sale_id": row["sale_id"],
                "invoice_no": row["invoice_no"],
                "old_total": float(row["total"] or 0),
                "new_amount": float(inv.get("amount", 0)),
                "new_paid": float(inv.get("paid", 0)),
                "new_status": str(inv.get("status", "")),
                "new_date": str(inv.get("date", "")),
                "new_checksum": new_checksum,
            })

    return {
        "modifications": modifications,
        "modified_count": len(modifications),
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "initialized": initialized,
        "unchanged": unchanged,
    }


# ════════════════════════════════════════════════════════════════════════════════
# v8.16.7: Expense Deletion Sync — detects expenses deleted in EZI POS
# ════════════════════════════════════════════════════════════════════════════════

def detect_deleted_expenses(new_backup_expense_hashes: set) -> dict:
    """After importing a new backup, find expenses that exist in BillBook
    but are NOT in the new backup's DIARY.DBF → they were deleted in the source POS.

    Returns a DRY-RUN summary (does NOT apply any changes).

    Safety measures:
    - Only checks expense hashes that were previously imported (exist in pos_expense_imports)
    - Skips already-synced deletions (synced_deleted=1)
    - Skips expenses that were manually edited in BillBook (no easy way to detect;
      we trust the source POS as the source of truth for imported expenses)
    - Threshold check: if missing_count > 10% of total imported expenses, flags as high_risk

    Returns:
        {missing_expenses: [...], missing_count, missing_total_amount,
         already_synced: int, high_risk: bool, threshold_exceeded: bool}
    """
    if not new_backup_expense_hashes:
        # Still count total imported so the UI can show "X expenses checked, 0 missing"
        with conn() as c:
            total = c.execute(
                "SELECT COUNT(*) AS n FROM pos_expense_imports WHERE synced_deleted = 0"
            ).fetchone()["n"]
        return {"missing_expenses": [], "missing_count": 0, "missing_total_amount": 0,
                "already_synced": 0, "high_risk": False, "threshold_exceeded": False,
                "threshold_pct": 10, "total_imported": total}

    with conn() as c:
        # Get all previously-imported expense hashes that are NOT synced_deleted
        existing = c.execute(
            "SELECT pei.import_hash, pei.expense_id, pei.import_run_id, "
            "pei.description, pei.amount, pei.date, "
            "e.category, e.payment_method "
            "FROM pos_expense_imports pei "
            "LEFT JOIN expenses e ON pei.expense_id = e.id "
            "WHERE pei.synced_deleted = 0 "
            "ORDER BY pei.import_hash"
        ).fetchall()

        total_imported = len(existing)
        missing_expenses = []
        already_synced = 0

        for row in existing:
            exp_hash = row["import_hash"]
            if exp_hash in new_backup_expense_hashes:
                continue  # Still in the backup — not deleted

            # This expense is missing from the new backup
            missing_expenses.append({
                "import_hash": exp_hash,
                "expense_id": row["expense_id"],
                "description": row["description"],
                "amount": float(row["amount"] or 0),
                "date": row["date"],
                "category": row["category"] if row["category"] else "Other",
                "payment_method": row["payment_method"] if row["payment_method"] else "cash",
            })

        missing_count = len(missing_expenses)
        missing_total = sum(e["amount"] for e in missing_expenses)
        # Higher threshold for expenses (10% vs 5% for sales) since expense lists
        # are typically more volatile
        threshold_pct = 10
        threshold_exceeded = (total_imported > 0 and
                              (missing_count / total_imported * 100) > threshold_pct)

    return {
        "missing_expenses": missing_expenses,
        "missing_count": missing_count,
        "missing_total_amount": round(missing_total, 2),
        "already_synced": already_synced,
        "high_risk": threshold_exceeded,
        "threshold_exceeded": threshold_exceeded,
        "threshold_pct": threshold_pct,
        "total_imported": total_imported,
    }


def apply_deleted_expenses_sync(missing_expenses: list, import_run_id: int) -> dict:
    """Apply the expense deletion sync — for each missing expense:
    1. Delete the row from `expenses` (cascades to cash_drawer if FK set up)
    2. Insert a reversing cash_drawer entry (if it was a cash expense)
    3. Mark `pos_expense_imports.synced_deleted = 1` so we don't re-detect it

    Each expense is processed in its own write_tx() for isolation.

    Args:
        missing_expenses: list of dicts from detect_deleted_expenses()
        import_run_id: the import run that detected these deletions

    Returns:
        {applied: int, skipped: int, errors: [...], reversed_amount: float}
    """
    applied = 0
    skipped = 0
    errors = []
    reversed_amount = 0.0

    for exp in missing_expenses:
        exp_hash = exp.get("import_hash")
        expense_id = exp.get("expense_id")
        amount = float(exp.get("amount") or 0)
        description = exp.get("description") or ""
        payment_method = exp.get("payment_method") or "cash"
        date_str = exp.get("date") or datetime.now().strftime("%Y-%m-%d")

        if not exp_hash or not expense_id:
            skipped += 1
            continue

        try:
            with write_tx() as c:
                # 1. Verify the expense still exists (might have been manually deleted)
                row = c.execute(
                    "SELECT id, amount FROM expenses WHERE id=?", (expense_id,)
                ).fetchone()
                if not row:
                    # Already gone — just mark the import as synced
                    c.execute(
                        "UPDATE pos_expense_imports SET synced_deleted=1, "
                        "deleted_sync_at=datetime('now','localtime') "
                        "WHERE import_hash=?",
                        (exp_hash,),
                    )
                    skipped += 1
                    continue

                # 2. Insert a reversing cash_drawer entry for cash expenses
                # (mirrors the original negative entry, but positive this time)
                # v8.16.7: use NULL reference_id to avoid FK constraint on the
                # new reversal row (we'll delete the original expense_row next)
                if payment_method == "cash" and amount > 0:
                    c.execute(
                        "INSERT INTO cash_drawer(type, amount, description, "
                        "reference_type, reference_id, created_at) "
                        "VALUES('expense_reversal', ?, ?, 'expense', NULL, ?)",
                        (abs(amount),
                         f"Reversal: deleted expense '{description[:40]}' (id={expense_id})",
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    )
                    reversed_amount += amount

                # 3. Null out any FK references to this expense BEFORE deletion
                # v8.16.7: pos_expense_imports.expense_id + cash_drawer.reference_id
                # may have FK constraints — null them out before deleting the expense
                c.execute(
                    "UPDATE pos_expense_imports SET expense_id=NULL "
                    "WHERE expense_id=?",
                    (expense_id,),
                )
                c.execute(
                    "UPDATE cash_drawer SET reference_id=NULL "
                    "WHERE reference_type='expense' AND reference_id=?",
                    (expense_id,),
                )
                # Also null out any other tables that might FK to expenses
                try:
                    c.execute(
                        "UPDATE recurring_expenses SET last_posted_expense_id=NULL "
                        "WHERE last_posted_expense_id=?",
                        (expense_id,),
                    )
                except Exception:
                    pass  # column may not exist

                # 4. Delete the expense row
                c.execute("DELETE FROM expenses WHERE id=?", (expense_id,))

                # 5. Mark the import as synced_deleted
                # (we already nulled expense_id above — synced_deleted is the flag)
                c.execute(
                    "UPDATE pos_expense_imports SET synced_deleted=1, "
                    "deleted_sync_at=datetime('now','localtime') "
                    "WHERE import_hash=?",
                    (exp_hash,),
                )

                # 6. Log the activity
                c.execute(
                    "INSERT INTO activity_log(event_type, entity_type, entity_id, "
                    "description, metadata, created_at) "
                    "VALUES('expense_deleted', 'expense', ?, ?, ?, datetime('now','localtime'))",
                    (expense_id,
                     f"Deleted expense via POS sync: {description[:60]} (Rs {amount:.2f})",
                     json.dumps({"expense_id": expense_id, "amount": amount,
                                 "import_run_id": import_run_id,
                                 "description": description[:200]})),
                )
            applied += 1
        except Exception as e:
            errors.append({
                "import_hash": exp_hash,
                "expense_id": expense_id,
                "error": str(e),
            })

    return {
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "reversed_amount": round(reversed_amount, 2),
    }
