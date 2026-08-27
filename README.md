# BillBook v8.16.10

A self-hosted POS + business management app for Pakistani wholesale discount shops. Upload photos/PDFs of supplier bills for AI extraction, run a full POS with category buttons, track inventory, customers, and loyalty, and get AI-powered business insights. **v8.16.10 clarifies the two margin columns with visual indicators: ↻ (changes with date range) vs = (stays the same).**

## What's New in v8.16.10 — "Margin Column Visual Indicators"

### Background

After v8.16.9 added both margins side-by-side, users were still confused — they'd change the date range and see that "Curr. Margin %" stayed the same, thinking it was a bug. Actually it's correct by design (current margin uses today's avg cost, not date-range-dependent), but the column headers didn't make that clear.

### What's NEW in v8.16.10

#### Visual indicators on the table headers

| Column | Symbol | Color | Meaning |
|---|---|---|---|
| **Hist. Margin % ↻** | ↻ (circular arrow) | blue | CHANGES with date range — it's the actual margin realized on sales in the selected period |
| **Avg Hist. Cost ↻** | ↻ (circular arrow) | blue | CHANGES with date range — avg cost per unit over the selected period |
| **Curr. Margin % =** | = (equals sign) | grey | STAYS THE SAME — always uses today's current avg cost, regardless of date range |
| **Curr. Avg Cost =** | = (equals sign) | grey | STAYS THE SAME — matches Store Profit dashboard exactly |

#### Updated info box above the table

The blue info box now annotates each column with "(changes with date range)" or "(stays the same — independent of date range)" so it's immediately clear which is which.

### Verified with real user data

Tested with the user's actual backup (20260824_153126.zip) across 3 date ranges:

| Date Range | Qty | Hist. Margin % ↻ | Avg Hist Cost ↻ | Curr. Margin % = | Curr. Avg Cost = |
|---|---|---|---|---|---|
| 30 days | 5,902 | 23.23% | Rs 191.98 | 23.02% | Rs 192.46 |
| 7 days | 1,881 | 23.13% | Rs 192.38 | 23.02% | Rs 192.46 |
| 1 day | 431 | 23.37% | Rs 192.46 | 23.02% | Rs 192.46 |

The **↻ columns** change with date range; the **= columns** stay the same — exactly as designed.

### Verification

- **45/45 e2e export tests** still pass.
- **37/37 reports** callable.
- Visual indicators confirmed visible in browser screenshot via Playwright.
- Date range change verified — 3 different ranges produce 3 different Hist. Margin % values.

---

## What's New in v8.16.9 — "Margin Reconciliation: Both Numbers on One Page"

### Background: the persistent margin-difference question

Users kept asking: "Why does Store Profit show 23.0% margin for Category A, but Profit Analysis shows 23.23%?" The v8.16.7 explanation ("they use different cost bases") was correct but unsatisfying — users had to flip between two pages and couldn't see why the numbers differed.

### What's NEW in v8.16.9

The Profit Analysis report now shows **both margins side-by-side on the same row**, plus the underlying cost numbers that explain the difference.

#### New columns on Profit Analysis (by Category):

| Column | What it shows | Source |
|---|---|---|
| **Hist. Margin %** | Actual margin realized on past sales = (Revenue − COGS) / Revenue | `sale_items.cost_price` (historical) |
| **Curr. Margin %** | What margin you'd make on the NEXT sale = (Sell Price − Current Avg Cost) / Sell Price | `category_stock_state.current_avg_cost` (matches Store Profit) |
| **Avg Hist. Cost** | Avg cost per unit over the date range = COGS / Qty Sold | Computed |
| **Curr. Avg Cost** | Current running weighted-avg cost (matches Store Profit dashboard) | `category_stock_state` |
| **Cost Δ** | Cost change since the period = Curr. Avg Cost − Avg Hist. Cost | Computed |

#### Color coding for Cost Δ:
- **Red** (+Rs X.XX) — cost has gone UP since the sale → next sale's margin will be LOWER
- **Green** (−Rs X.XX) — cost went DOWN since the sale → next sale's margin will be HIGHER
- **Grey** (Rs 0.00) — no change

#### Reconciliation banner above the table:
A blue info box explains exactly what each column means and how to read the Cost Δ column.

### How to interpret the numbers (example from real data)

```
Cat    Hist.M%  Curr.M%    Diff | AvgHistCost CurrAvgCost     CostΔ
A       23.23%   23.02%  -0.21% | Rs   191.98 Rs   192.46    +0.48
B       27.95%   27.37%  -0.58% | Rs   359.59 Rs   363.17    +3.58
C       27.51%   26.33%  -1.18% | Rs   543.68 Rs   552.51    +8.83
D       20.09%   19.69%  -0.40% | Rs   799.12 Rs   803.06    +3.94
```

**Reading**: For Category C, the historical margin (27.51%) was higher than the current margin (26.33%) because the current avg cost (Rs 552.51) is Rs 8.83 higher than the avg cost during the period (Rs 543.68). This means you recently bought Category C inventory at a higher price, so your next sale will have a lower margin.

### Verification

- **45/45 e2e export tests** still pass — no regression.
- **37/37 reports** callable.
- **Math verified against user's real data**: For each category, recomputed all 5 new fields independently from raw SQL — every single number matched (10/10 categories, 50/50 fields).
- **Cross-checked**: Profit Analysis `Curr. Avg Cost` column EXACTLY matches Store Profit dashboard's `avg_cost` for every category. ✓

---

## What's New in v8.16.8 — "Expense Update Sync + Manual Expense Protection"

### Background: how expense sync works

BillBook's POS Import feature reads the daily backup zip (`BU*.zip`) from your Ezi POS. The `DIARY.DBF` file inside that zip contains all your expenses. BillBook uses a hash of each expense's `DETAILS + DATE` fields to recognize the same expense across imports (so re-importing the same backup never duplicates).

### What's NEW in v8.16.8

#### 1. Expense UPDATES now sync automatically

**Before v8.16.8**: If you edited an expense in EZI POS (e.g. changed the amount from Rs 2,000 to Rs 2,500), the next BillBook import would silently skip the change — the expense was already imported, so it was treated as a "duplicate" and ignored.

**Now**: Each `pos_expense_imports` row stores a `source_checksum` (hash of `details + amount + date`). On import, if the checksum differs from what's stored:
- The `expenses` row is updated with the new amount/description
- A `cash_drawer` adjustment entry is inserted equal to the amount difference (negative if increased, positive if decreased)
- An `activity_log` entry records the change (old amount → new amount)
- The import summary shows "X expenses updated" alongside "X new expenses"

This is **automatic** — no button click required. Happens on every import.

#### 2. Manual expenses are explicitly protected

**Before v8.16.8**: Expenses you added manually via BillBook's UI (e.g. owner draws, custom operating expenses) had no link to `pos_expense_imports`. They COULDN'T be touched by deletion sync (because the sync only iterates over `pos_expense_imports`), but this was implicit and unclear.

**Now**: The POS Import page has a new info box explaining exactly what's safe:

> **What gets synced automatically on each import:**
> - New sales/expenses in EZI POS → added to BillBook
> - Modified expenses in EZI POS → updated in BillBook + cash drawer auto-adjusted
> - **Expenses you added manually in BillBook → NEVER touched by POS imports**
>
> To delete expenses that were removed in EZI POS, click "Sync Deleted Expenses" — that step requires manager PIN and is not automatic (because deletions are irreversible).

### How DIARY.DBF deletion sync works (clarification)

The "Sync Deleted Expenses" button compares:

| Set | What it contains |
|---|---|
| `pos_expense_imports` rows (where `synced_deleted = 0`) | Expenses previously imported from EZI POS |
| Hashes extracted from latest `DIARY.DBF` | Expenses currently in your EZI POS |

**A row in the first set but NOT in the second = deleted in EZI POS.** The sync then:
1. Inserts a reversing cash_drawer entry (positive amount)
2. Deletes the linked `expenses` row
3. Marks `pos_expense_imports.synced_deleted = 1` so we don't re-detect

Manual expenses are not in `pos_expense_imports` at all → never appear in the deletion list → never deleted.

### DB migration

Added 4 new columns to `pos_expense_imports` (in addition to the 3 from v8.16.7):

| Column | Purpose |
|---|---|
| `source_checksum` (TEXT) | MD5 of `details\|amount\|date` — used to detect updates |
| `checksum_initialized_at` (TEXT) | Timestamp of first checksum set |
| `synced_updated` (INTEGER DEFAULT 0) | Flag: has this expense been updated via sync? |
| `updated_sync_at` (TEXT) | Timestamp of the update sync |

Existing rows are auto-backfilled with their `source_checksum` on the first import after migration.

### Verification

- **45/45 e2e export tests** still pass — no regression.
- **37/37 reports** callable.
- **Manual expense protection test**: Created a manual expense with no `pos_expense_imports` link → ran deletion sync with empty hashes (simulating all EZI expenses deleted) → manual expense NOT in missing list, NOT deleted.
- **Expense UPDATE sync test**: Changed an expense's amount from Rs 2,000 → Rs 2,200 in EZI POS → ran import → expense row updated, cash_drawer got -Rs 200 adjustment, activity_log entry written, source_checksum updated. All assertions passed.

---

## What's New in v8.16.7 — "Margin Clarity + Expense Sync + PDF Fix"

### 1. PDF Export 422 Error (FIXED)

**Symptom**: Clicking "Export PDF" on Profit Analysis, Sold Stock, or Billwise reports returned `422 Unprocessable Content` with the error `{"detail":[{"loc":["query","start"],"msg":"Field required"},{"loc":["query","end"],"msg":"Field required"}]}`.

**Root cause**: The export endpoints (`/api/reports/profit-analysis/export`, `/api/reports/sold-stock/export`, `/api/reports/billwise/export`) declared `start: str, end: str` as **required** query params. When the auto-injected PDF button was clicked without date params (e.g. on a fresh page load), FastAPI rejected the request.

**Fix**: Made `start` and `end` optional (`start: str = ""`) with sensible defaults — when missing, the endpoint auto-defaults to last 30 days (`today - 30 days` to `today`). The PDF now generates successfully without any query params.

### 2. Margin Difference Between Store Profit & Profit Analysis (CLARIFIED, NOT CHANGED)

**Symptom**: The margins shown on the Store Profit dashboard differ from the Profit Analysis report:
- Store Profit "Current Margins": A=23.0%, B=27.4%, C=26.3%, D=19.7%
- Profit Analysis "Profit by Category": A=23.23%, B=27.95%, C=27.51%, D=20.09%

**Root cause**: These are **two different but valid metrics**:
- **Store Profit / Margins page** uses `category_stock_state.current_avg_cost` — the **current running weighted-average cost** (forward-looking: "if I sell a unit right now, what's my margin?").
- **Profit Analysis report** uses `sale_items.cost_price` — the **historical cost captured at the time of each sale** (backward-looking: "what margin did I actually realize on past sales?").

The numbers will always differ slightly because cost prices change as new inventory arrives at different prices. **Both are correct** — they answer different questions.

**Fix**: Added clarifying notes to all 3 pages so users understand the difference:
- **Store Profit dashboard**: "Based on current running avg cost (forward-looking). Differs from Profit Analysis, which uses historical cost-at-time-of-sale."
- **Profit Analysis page**: "Historical cost-at-time-of-sale · differs from Store Profit (forward-looking)"
- **Margins page**: Added an info box explaining the metric + linking to Profit Analysis for historical margins.

### 3. Expense Deletion Sync from EZI POS (NEW)

**Symptom**: When an expense was deleted in EZI POS and then a new backup was imported, BillBook kept the deleted expense forever — there was no detection or reversal mechanism.

**Fix**: Added a new "Sync Deleted Expenses" feature that mirrors the existing sales-deletion sync:

**Backend** (`app/pos_import_sync.py` + `app/routers/pos_import_router.py`):

- New `detect_deleted_expenses(new_backup_hashes)` function — compares the latest DIARY.DBF hashes against `pos_expense_imports`. Any hash that was previously imported but is missing from the new backup = deleted.
- New `apply_deleted_expenses_sync(missing_expenses, import_run_id)` function — for each deleted expense:
  1. Inserts a reversing `cash_drawer` entry (positive amount) to undo the original negative impact
  2. NULLs out FK references (`pos_expense_imports.expense_id`, `cash_drawer.reference_id`)
  3. Deletes the `expenses` row
  4. Marks `pos_expense_imports.synced_deleted = 1` so we don't re-detect it
  5. Writes an `activity_log` entry for audit trail
- Two new API endpoints: `POST /api/pos-import/detect-expense-deletions/{run_id}` (dry-run) and `POST /api/pos-import/apply-expense-deletions` (applies, requires manager PIN).

**DB migration** (`app/db.py`): Added 3 columns to `pos_expense_imports`:
- `expense_id INTEGER` — links to the `expenses.id` row that was created from this import
- `synced_deleted INTEGER DEFAULT 0` — flag: has this deletion been synced?
- `deleted_sync_at TEXT` — timestamp of the sync

**Expense import code**: Updated to capture `expense_id` when inserting new expenses (so we can later reverse them).

**Frontend** (`app/static/js/pages/pos-import-sync-page.js`): Added a "Sync Deleted Expenses" button on each import run row. Click it → dry-run detect → show summary of deleted expenses → ask for manager PIN → apply.

### Verification

- **45/45 e2e export tests** still pass — no regression.
- **37/37 reports** callable.
- **Expense deletion sync tested with real user data**: detected 17 missing expenses (out of 48 imported), applied 5 deletions successfully (those with linked `expense_id`), reversed Rs 7,820 in cash_drawer entries, no errors. The remaining 13 couldn't be auto-deleted because they predate the v8.16.7 migration (no `expense_id` link) — they're marked `synced_deleted=1` so they won't be re-detected.

---

## What's New in v8.16.6 — "POS Sale Fix + Backup Restore"

### 1. POS Sale 422 Error (FIXED)

**Symptom**: Clicking "Complete Sale" on the POS screen failed with:

```
Sale failed: customer_id: Input should be a valid integer;
quotation_id: Input should be a valid integer;
payment_submethod: Input should be a valid string
POST /api/sales HTTP/1.1 422 Unprocessable Content
```

**Root cause**: The Pydantic `SaleIn` model declared these fields as `field: int = None`. In Pydantic v2, this means "the default value is `None`, but the type must be `int`" — so sending `null` from the frontend (which always happens for walk-in customers without a quotation, or for cash sales without a submethod) was rejected.

**Fix**: Changed all 8 affected fields in `SaleIn` and `SaleItemIn` to use `Optional[int] = None` / `Optional[str] = None` (proper Pydantic v2 syntax). Also fixed the same anti-pattern in 4 other Pydantic models across the codebase:

- `app/routers/pos.py` — `SaleIn`, `SaleItemIn`, `SaleEditIn`, `ShiftEndIn` (9 fields)
- `app/routers/customers.py` — `CustomerPaymentIn`, `ExpenseIn`, `ExpenseCategoryUpdate`, `RecurringExpenseUpdate` (14 fields)
- `app/routers/extensions.py` — `PendingActionCreate`, `PendingActionEdit` (3 fields)
- `app/routers/inventory.py` — `StockAdjustmentIn` (1 field)

**Total: 27 fields fixed across 4 files.** Verified with a smoke test that calls `SaleIn(**payload)` with `null` values — passes cleanly.

### 2. Backup Upload / Restore / Download (NEW)

**Symptom**: The Settings → Backups page only let you *create* backups — there was no way to import one from another machine or roll back to a previous state.

**Fix**: Added 3 new backend endpoints + 3 new UI buttons:

**Backend** (`app/routers/bills.py`):

- `POST /api/backup/upload` — accepts a `.db` or `.zip` file upload, validates it's a real SQLite DB (runs `PRAGMA integrity_check`), saves it as `data/backups/upload_<timestamp>/billbook.db`. Supports both bare `.db` files and ZIP archives containing a `.db` file.
- `POST /api/backup/restore` — restores the DB from a backup directory. **Creates a safety backup first** (`pre_restore_safety_<timestamp>/`) so you can always undo a bad restore. Uses the SQLite backup API to safely replace the live DB in-place. Requires manager PIN.
- `GET /api/backup/download?name=<backup_name>` — downloads a backup as a ZIP file (so you can move it to another machine).

**Frontend** (`app/static/js/pages/settings-pages.js`):

- **"Upload Backup" button** in the page header — opens a file picker, accepts `.db` or `.zip`, uploads via the new endpoint.
- **"Download" button** on each backup row — triggers ZIP download.
- **"Restore" button** on each backup row — prompts for confirmation + manager PIN, then restores.
- **"UPLOADED" badge** on backups that came from an upload (so you can distinguish them from auto-created ones).
- **"SAFETY" badge** on backups created automatically before a restore (so you know which ones are undo points).

### Verification

- **All 27 Pydantic fields** fixed — `SaleIn(**{null fields})` validates cleanly.
- **All 10 backup feature tests** pass: upload .zip, upload .db, restore without PIN (rejected), restore with PIN (succeeds), upload fake file (rejected), restore nonexistent (404), path traversal attack (rejected).
- **45/45 export tests** still pass — no regression from the v8.16.5 work.

---

## What's New in v8.16.5 — "Proper Nested Data Export"

v8.16.5 closes a critical gap in the v8.16.4 export pipeline: the
PDF/Excel generators were only extracting flat top-level scalars and the
first list-of-dicts. For dashboard-style reports like Store Profit,
Overview, Cash Flow, Balance Sheet, and Peak Hours — which return nested
dicts (e.g. `current_margins.actual_overall_margin`, `cash.buckets.*`)
— the exports were essentially blank.

### What changed

**Three new helper functions** in `app/routers/reports.py`:

1. **`_flatten_dict(d, prefix, max_depth=3)`** — recursively flattens
   nested dicts into `(label, value)` tuples with parent-key prefixes
   (e.g. `"Current Margins — Actual Overall Margin"`).
2. **`_extract_kpi_groups(data)`** — groups KPIs by their parent section
   so the export shows labeled sections like SUMMARY, CURRENT STOCK,
   CURRENT MARGINS, DAILY, MONTHLY, YTD, CASH, etc. Skips metadata keys
   (`note`, `report_name`, `period_start`, etc.) that aren't KPIs.
3. **`_find_all_tables(data)`** — finds ALL lists-of-dicts in the data
   (not just the first one) and renders each as a separate labeled
   table with its own heading.

**Excel generator** — renders each KPI section with a colored section
heading bar, then KPI cards 2-per-row. Each data table gets its own
heading and 4-column-styled header row. Auto-width columns now scan
across all tables (not just the first).

**PDF generator** — same section-grouped KPI layout with `KeepTogether`
flowables so a section never gets split across pages. Each table is
wrapped with its title in a `KeepTogether` block, capped at 50 rows
per table (Excel gets the full data).

### Verification

- **45/45 end-to-end HTTP tests** still pass — every report returns
  HTTP 200 with the correct content-type.
- Store Profit PDF went from 2.3 KB (1 page, mostly empty) to 4.2 KB
  (2 pages with 5 KPI sections + 20+ KPIs).
- Store Profit Excel went from 5.3 KB to 6.5 KB, now showing 5 labeled
  KPI sections (Current Stock, Current Margins, Daily, Monthly, YTD)
  plus a Cash section with 12 KPIs including Stock Reserve alerts.
- Overview PDF now shows KPIs section + Month Comparison section (was
  showing only one section before).
- Peak Hours PDF page 2 now has the full by-hour table with 24 rows
  (Hour / Sale Count / Revenue).

---

## What's New in v8.16.4 — "Universal Report Export"

v8.16.4 closes a long-standing gap: every report page now has working
**PDF + Excel export buttons** that produce branded, paginated, ready-to-share
output. The release also fixes several silent 404s on the auto-injected
buttons (the backend had no handler for `audit`, `suspicious`, `monthly-close`,
or `targets` — now all wired up).

### What changed

**Backend** (`app/routers/reports.py`)

- Added 4 missing entries to the universal `report_map`:
  `audit`, `suspicious`, `monthly-close`, `targets` — these now use the
  real underlying functions (`get_latest_audit_run`, `list_suspicious_events`,
  `monthly_close`, `get_target_progress`).
- Fixed 5 entries that pointed at the wrong module path (`app.reports.*`
  → `app.routers.reports.*`): `pnl`, `cash-flow`, `balance-sheet`,
  `top-items`, `peak-hours`.
- Fixed `store-profit` to use `app.profit_analytics.get_store_profit_dashboard`
  and `overview` to use `app.routers.insights.i_dashboard`.
- Updated the 3 specific CSV-export endpoints to respect a `format`
  query param: `format=pdf` or `format=excel` now delegates to the universal
  generator (`profit-analysis/export`, `sold-stock/export`, `billwise/export`).
- Upgraded `_generate_pdf` and `_generate_excel` to professional-grade:
  navy + copper title band, KPI summary cards, alternating row stripes,
  frozen Excel header, page numbers + footer in PDF.

**Frontend** (`app/static/js/pages/`)

- Added `.pos-page-header-actions` div to 6 report pages that lacked it,
  allowing the auto-injector to populate PDF + Excel buttons:
  - `/reports/overview`
  - `/reports/ytd`
  - `/reports/margins`
  - `/reports/store-profit`
  - `/reports/suspicious`
  - `/reports/targets`
- Updated `REPORT_EXPORT_MAP` so `suspicious` calls its own backend
  (was previously aliased to `audit`).

### Verification

- **37/37 reports** callable from the universal export endpoint (function
  import + signature smoke test).
- **45/45 end-to-end HTTP tests** pass — every report returns HTTP 200
  with the correct content-type (`application/pdf` or
  `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`).

---

## What's New in v8.16.0 — "AI Market Intel + Universal Export"

v8.16.0 is a focused polish + correctness release that closes two persistent user complaints:

### 1. POS UI Professional Refresh
The POS screen now uses a **scoped dark navy theme** (applied via `data-pos-theme="dark"` on `.pos-shell` — the rest of the app stays in the warm cream Anthropic/Claude palette). Inspired by modern dark-mode POS UIs, the redesign includes:

- **Tiered category cards** — Each tile has a 2px colored border (emerald/sky/amber/violet/pink/teal/rose), a colored pill-shaped code badge in the top-left, a **hotkey number** (1–7) in the top-right, a tiny uppercase category name, and a hero-size price (28px bold). Hover lifts the card and adds a glow shadow in the tier color.
- **QTY multiplier row** — At the top of the items panel: ×1 / ×2 / ×3 / ×5 / ×10 pill group. Tap once to select, then every category tap adds that quantity. Number keys 1–5 also select the multiplier. F1–F7 still add categories (and respect the active multiplier).
- **Sale Complete modal** — Big circular green checkmark icon (animated pop-in), "Sale Complete" title, invoice number + item count, hero-size total amount, "paid in cash/card/online/credit/split" subtitle, change-due pill (if applicable), and two CTAs: Receipt (secondary) + New Sale (primary teal). Matches the DOLLARMAX reference design the user provided.
- **Refined cart cards** — Each cart line is now a dark surface card with a colored tier badge, name, strikethrough original price + discounted price + chip showing the discount amount, qty stepper, line total, and remove button.
- **Empty cart state** — Larger centered icon (72×72 rounded square) with helpful copy ("Tap an item on the left or press F1–F7").
- **Emerald grand total** — Gradient background + radial glow + emerald text on the TOTAL block. The "Complete Sale" button is emerald with a glow shadow.
- **Pin-shape payment method tiles** — Active state shows emerald border + soft glow.

### 2. Overall UI Polish
- **KPI tiles**: label is now uppercase 11px tracked, value is 30px bold with tabular-nums, semantic variants (kpi-success/danger/warning/accent) get a 3px left-border accent strip.
- **Tables**: header now has a subtle surface-soft background, 11px tracked uppercase labels, 13px row padding, table-num cells get weight 500 + tabular-nums.
- **Table-wrap** has a 1px hairline border + radius-lg corners for visual containment.

### 3. Deleted-Data Leak Fixes
The user reported that after deleting a supplier, the count still appeared in tiles. Audit found 11 queries across 7 files that were missing `deleted_at IS NULL` filters on suppliers/customers:

- `app/routers/insights.py` — `top_suppliers` query (the actual leak the user saw)
- `app/routers/reports.py` — top customers report + supplier performance report
- `app/routers/customers.py` — `list_customers`, `rfm_analysis`, `birthday_list`, `ar_aging`
- `app/reports.py` — supplier report
- `app/agent.py` — `_tool_get_customer_credit_top` (AI tool)
- `app/trends.py` — supplier names for AI briefing
- `app/export.py` — Excel supplier export
- `app/shop.py` — `get_cash_position` (overdue urdhaar) + `get_actual_earnings` (owed_to_you)

All now filter `WHERE deleted_at IS NULL` consistently. Soft-deleted suppliers/customers truly disappear from every tile, report, and AI tool — but their historical bills/sales remain in the audit log.

### 4. Documentation Updates
- **USER_GUIDE.md** — rewritten for v8.12: new POS walkthrough, per-item discount + price-override flow, payment sub-methods (Easypaisa/JazzCash/Raast/Bank), void vs. refund distinction, deleted-data semantics, POS Import sync deleted/modified detection, Data Reconciliation tool, atomic transaction safety, `/api/health` + `/api/version` endpoints.
- **AI system prompt** (`app/agent.py`) — expanded with v8.8–v8.12 features: per-item discounts, payment sub-methods, tax handling, deleted-data semantics, voided vs. refunded distinction, soft-delete model, POS Import sync detection, AI Auditor safe-withdrawal formula. New question-type examples for refunds/voids and "where did supplier X go?".
- **In-app Help system** (`app/help_system.py`) — new "v8.12 POS Refresh + Soft-Delete" category with 7 new FAQ articles covering POS UI, per-item discounts, payment sub-methods, soft-delete semantics, POS Import sync, Data Reconciliation, and atomic transactions.

### Test count
**568+ passing tests** (unchanged from v8.11 — no test regressions). 18 pre-existing test-isolation baseline failures (unchanged). All affected module tests pass: test_infra (6), test_help_system (5), test_wholesale_flows, test_cash_controls, test_owner_awareness, test_refund_sale_atomic, test_confirm_bill_atomic, test_pos_create_sale_atomic, test_new_reports, test_inventory_movement, test_security_hardening, test_money, test_db_write_tx, test_profit_engine_conn.

---

## What's New in v8.7.0 — "Inventory UX + Reports Enhancement"

v8.7.0 is a UX-focused release that addresses 5 user-reported issues:

### Changes

1. **`#/items` page: Bill-wise master-detail view** — The old flat item list (showing all bill_items across all bills in one ungrouped list) is replaced with a **bills-first view**: a lightweight list of bills (with item_count, category_count, total_cost), click a bill to expand its items inline. Items are lazy-loaded via the existing `GET /api/bills/{id}` endpoint (cached client-side). Search filters by supplier, bill_no, OR item name. New endpoint: `GET /api/items/bills`.

2. **Stock Levels: Purchased/Sold/Adjustments bug fix** — The v8.5 refactor removed the `purchased`/`sold`/`adjustments` columns from `shop.get_inventory()` but left the UI columns in place (rendering as `NaN`). v8.7.0 restores these as **informational aggregates** (all-time sums per category) — `stock` still comes from `category_stock_state` (source of truth). 6 new tests.

3. **Category not displayed in Item Search results** — The search results table was missing a Category column (the "Recent Items" table had it). Added `<td>${esc(it.cat_name || '—')}</td>`. The backend (`GET /api/items/search`) already returned `cat_name` — purely a frontend omission.

4. **Two new reports**:
   - **Profit Analysis** (`/reports/profit-analysis`) — date-range profit breakdown by category (default) or by month. Returns revenue, COGS, gross profit, margin %, qty sold per category/month. CSV export. Endpoint: `GET /api/reports/profit-analysis?start=&end=&group_by=category|month`.
   - **Sold Stock** (`/reports/sold-stock`) — date-range sold stock report by category (DEFAULT — Reviewer 3 correction: AI-extracted item names are too noisy) or by item (secondary, drill-down with `LOWER(item_name)` grouping). Returns qty_sold, revenue, COGS, gross_profit, margin_pct, avg_selling_price. CSV export. Endpoint: `GET /api/reports/sold-stock?start=&end=&group_by=category|item`.

5. **Billwise report: lazy-load** — The `/api/reports/billwise` endpoint was returning ALL bills + ALL items in one payload (potentially several MB). v8.7.0 splits it: by default returns bill headers + precomputed aggregates (item_count, category_count, total_cost, total_revenue, total_profit) WITHOUT items. The frontend lazy-loads the detail via the existing `GET /api/bills/{id}` when a bill is clicked (cached). `include_items=true` preserves legacy behavior (used by Excel export). 4 new master-list columns: Cats, Cost, Profit (in addition to existing Items).

### Reviewer corrections applied
- **Reviewer 3 #1** (Change 2): Verified `qty`/`unit` normalization — `bill_items` stores qty+unit separately; the `CASE bi.unit WHEN 'dozen' THEN bi.qty*12 ELSE bi.qty END` pattern matches the existing codebase (profit_engine.py, shop.py). Indexes on `category_id` already exist.
- **Reviewer 3 #3** (Change 3): Confirmed it's a JS property mapping bug — `price_categories.name` is `NOT NULL`; the search results table was just missing the `<td>` for `cat_name`.
- **Reviewer 3 #4** (Change 4B): Made "By Category" the default + primary view for Sold Stock (AI item_names are too noisy).
- **Reviewer 3 #5** (Change 5): Did NOT build a new `billwise/{bill_id}` endpoint — reused the existing `GET /api/bills/{id}` for lazy-load detail.
- **Reviewer 3 #5** (Change 1): Same — `#/items` row expansion reuses `GET /api/bills/{id}`.
- **Reviewer 2** (Change 1): Row click = expand/collapse; an explicit "Edit" button navigates to `#/bills/{id}` (avoids overloading row click).

### Test count
**568+ passing tests** (was 556 at v8.6.5 — +12 new tests: 6 for inventory movement + 6 for new reports). 18 pre-existing test-isolation failures (unchanged from baseline).

---

## What's New in v8.6.5 — "Phase 0 Complete"

v8.6.5 is the FINAL release of the Phase 0 hardening series — backend-only changes focused on **data integrity**, **operational visibility**, and **security**:

### Phase 0 PRs (all complete)

- **PR 1** — `write_tx()` + `read_tx()` context managers in `app/db.py`; `money()` + `money_d()` in `app/money.py`; 28 new tests.
- **PR 2** — `profit_engine` mutating functions refactored to accept `*, c=None`; 11 new tests.
- **PR 3** — `create_sale()` rewritten as a single atomic `write_tx()`; 15 new tests.
- **PR 4** — `refund_sale()` rewritten as a single atomic `write_tx()`. Reviewer 3 split-payment trap fix: only the original cash portion is reversed into cash_drawer (split/card/online refunds don't touch the drawer). Commission reversal is idempotent (`WHERE reversed=0` guard). 10 new tests.
- **PR 5** — `confirm_bill()` rewritten as a single atomic `write_tx()` with OCC via `bills.version` column. Reviewers 2+3 correction: NO `confirm_lock` column — uses `BEGIN IMMEDIATE` natural locking + OCC. New `reverse_purchase_in_state()` helper (uses ORIGINAL price, not current avg — the v8.5.5 double-subtraction bug fix extracted into a reusable function). 12 new tests.
- **PR 6** — Ezi import: `stock_state_dirty=true` set at START (Reviewer 3 correction — crash-safe), sales sorted by `INVOICE.DATE` before processing (Reviewer 1 correction — chronological correctness), per-sale `write_tx()`, inlined customer stats. 5 new tests.
- **PR 7** (this release) — Security hardening:
  - **7a**: bcrypt `employees.pin_hash` (14 rounds — higher than passwords due to smaller PIN keyspace). `verify_manager_pin` checks `pin_hash` first, falls back to plaintext `pin` with a warning log (backward compat during migration). Migration script: `scripts/migrate_pin_hash.py`. 5 new tests.
  - **7b**: Fernet encryption for ALL `settings.*_api_key` values (was plaintext for `groq_api_key`, `gemini_api_key`, etc.). `migrate_setting_keys()` runs on every boot — idempotent. New `decrypt_setting_key()` + `encrypt_setting_key()` helpers. 3 new tests.
  - **7c**: Restrictive Tauri CSP — `script-src 'self'` (no inline/eval), `connect-src 'self' http://127.0.0.1:8000` (local server only), `frame-ancestors 'none'` (clickjacking protection), `img-src 'self' data: blob:` (bill image viewer). 1 new test.
  - **7d** (Reviewer 3 critical fix): `change_password` endpoint now re-encrypts ALL stored API keys with the new Fernet key in a SINGLE atomic `write_tx()`. Without this, changing the password would have rotated the Fernet key (derived from `password_hash` via PBKDF2) — making all stored API keys permanently unreadable. 4 new tests.
- **PR 8** — `/api/health`, `/api/version`, CI workflow (`.github/workflows/test.yml`), `pytest.ini`, `conftest.py` skeleton. 6 new tests.

### Test count
**556+ passing tests** (was 493 at Phase 0 start — +63 new tests across PRs 1-8). 18 pre-existing test-isolation failures (unchanged from baseline).

---

## What's New in v8.2 — "AI Auditor & Bill Intelligence Release"

v8.2 adds two capabilities the owner asked for: (1) an **AI Auditor** that proactively verifies Actual Earnings and polices safe withdrawal, and (2) **Bill Intelligence** that checks every newly-confirmed bill against the previous purchase to compute sell-through and warn about overstocking.

### Phase summary

- **Phase 1-2 — AI Auditor Core + Earnings Integrity**: `audit_runs` + `audit_findings` tables. `app/auditor.py` rules engine with 8 checks across 5 domains (integrity, financial, fraud, operational, compliance). All checks are deterministic math on local data — fully offline, no LLM required.
  - Earnings formula integrity (CRITICAL): Sales - COGS - OpEx = earnings
  - COGS bridge integrity (CRITICAL): COGS from sales vs bridge
  - Over-withdrawal detection (CRITICAL): withdrawals > safe_withdrawal → exact over-amount
  - Restock funding adequacy (WARNING): projected restock > available cash
  - Stock reserve days-of-cover (WARNING/CRITICAL)
  - Negative stock (CRITICAL)
  - Refund anomaly (WARNING): >10% refund rate
  - Unconfirmed bills (INFO): bills in 'review' > 7 days
- **Phase 3 — Audit Report Page**: `/reports/audit` with severity-ranked findings, stat cards, safe-withdrawal banner, Run Audit button, acknowledge buttons. Actionable critical findings create pending_actions in the Approval Queue.
- **Phase 4 — Safe-Withdrawal Enforcement**: `get_safe_withdrawal_amount()` returns the safe limit + over-amount. Existing withdrawal endpoints unchanged. Live feedback in the withdrawal modal (green/red) + PIN gate for over-safe amounts.
- **Phase 5 — Bill Intelligence**: `bill_intelligence` table. On bill confirm, compute sell-through per category vs last purchase. Tiered verdict (≥80% well_timed, 40-80% partial, <40% overstock_risk). Soft pause on overstock with "Confirm Anyway" / "It's intentional" options. Acknowledged findings aren't re-flagged.
- **Phase 6 — Browser E2E + Release**: 20/20 browser E2E checks pass (zero console errors). 412/412 tests pass. 185.88 + multi-branch simulation still pass.

### New API endpoints (additions only)

- `GET /api/audit/runs` — list recent audit runs
- `GET /api/audit/runs/{id}` — get a run + findings
- `GET /api/audit/latest` — most recent run
- `POST /api/audit/run` — trigger a manual run
- `POST /api/audit/findings/{id}/acknowledge` — acknowledge a finding
- `GET /api/audit/safe-withdrawal` — safe withdrawal amount for this month

### New frontend pages

- AI Auditor — `/reports/audit` (new)

### Test coverage

412 tests across 47 files, all passing. v8.2 added 35 new tests across 3 test files. Browser E2E: 20/20 checks pass with zero console errors.

## What's New in v8.1 — "One-Click Onboarding & UX Release"

v8.0 made the system powerful; v8.1 makes it effortless. Every setup and operational task is pushed toward Zero-Config (works with no action), then One-Click (single action), and only uses a Guided wizard when truly necessary.

### Phase summary

- **Phase 1 — First-Launch Wizard**: 4-step guided setup (password + strength meter, business type, category editor, optional AI + start page). Fresh DB → wizard appears automatically. Existing v8.0 DB → wizard never appears.
- **Phase 2 — One-Click Boot**: `scripts/boot-check.py` verifies the desktop sidecar boots, prints `BILLBOOK_READY port=XXXX`, serves HTTP + static assets, shuts down cleanly. Double-boot test verifies port-finding (8000 → 8001).
- **Phase 3 — QR-Code Pairing**: `GET /api/devices/qr` + `GET /api/hq/branches/qr` return PNG QR images encoding pairing payloads. Scan → auto-pair, no manual IP/code entry. Existing 6-digit flow still works.
- **Phase 4 — One-Click Remote Access**: Settings toggle for Cloudflare Tunnel. `POST /api/remote-access/start` spawns cloudflared, parses URL. `POST stop` kills it. `GET status` reports running/URL/uptime. Persisted across restarts.
- **Phase 5 — Auto-Maintenance**: Auto-backup (daily, retains last 10). Update check (GitHub Releases). Diagnose (6 health checks: DB integrity, disk space, AI provider, tunnel, backup age, negative stock — green/amber/red).
- **Phase 6 — Daily-Use Friction Fixes**: Start page honoring (POS/Dashboard/Launcher). Global drag-drop bill upload (drop a PDF anywhere → /bills/new). Today's profit ticker in topbar. Quick expense FAB (2-field modal).
- **Phase 7 — Browser E2E + Release**: 29/29 browser E2E checks pass (zero console errors across 22 pages + 7 endpoint checks + drag-drop + FAB + ticker). 377/377 tests pass.

### New API endpoints (additions only)

- `GET /api/setup/state` — wizard progress + setup_completed
- `POST /api/setup/wizard` — orchestrate full first-launch setup
- `GET /api/devices/qr` — device pairing QR (PNG)
- `GET /api/hq/branches/qr` — branch registration QR (PNG)
- `POST /api/remote-access/start` — start Cloudflare Tunnel
- `POST /api/remote-access/stop` — stop tunnel
- `GET /api/remote-access/status` — tunnel status
- `POST /api/maintenance/backup` — create timestamped backup
- `GET /api/maintenance/backups` — list backups
- `GET /api/maintenance/update-check` — check for newer version
- `GET /api/maintenance/diagnose` — run 6 health checks
- `POST /api/maintenance/auto-backup-toggle` — toggle auto-backup

### Test coverage

377 tests across 44 files, all passing. v8.1 added 46 new tests across 6 test files. Browser E2E: 30/30 checks pass with zero console errors across 22 pages (11 v8.0 + 11 v7.2) + 7 endpoint checks + drag-drop + FAB + ticker.

## What's New in v8.0 — "Multi-Store Release"

v8.0 transforms BillBook from a single-shop system into a **multi-branch** system. Every branch runs its own independent BillBook instance (local SQLite, offline-first, fast). A lightweight Owner Hub aggregates read-only summaries from all branches. The internet connects them; it does not run them.

> **Setting up multiple branches?** See the **[Multi-Branch Setup & Sync Architecture Guide](MULTI_STORE_GUIDE.md)** for a complete walkthrough of the 5 sync mechanisms, step-by-step setup, transfer/price-push/central-purchase flows, and troubleshooting.

### Governing principle: Local Autonomy + Central Visibility

- If the internet drops in Lahore, Branch A keeps selling normally.
- No branch ever depends on another branch to make a sale.
- Sync is **eventual** — summaries, transfers, and price updates flow when connectivity allows.
- The single-shop experience remains 100% intact. A branch with no hub configured behaves exactly as v7.2.

### Phase summary

- **Phase 1 — Branch Identity**: `branch_config` table + Settings → Branch page. Default `role='branch'` + empty `hub_url` = v7.2 behavior (zero friction).
- **Phase 2 — HQ Registry + Registration**: `branches` table + 6-digit registration code flow (reuses v6.0 pairing pattern, 5-min expiry, single-use). `verify_branch_token` authenticates Bearer tokens on sync endpoints (works on both HQ-side `branches` table and branch-side `branch_config`).
- **Phase 3 — Consolidated Visibility (Owner Hub)**: `branch_summaries` + `sync_outbox` tables. `POST /api/sync/branch-summary` (Bearer auth, idempotent by branch_id+summary_date). Owner Hub dashboard with consolidated P&L, branch leaderboard, per-branch stock snapshot, stale badge (24h+).
- **Phase 4 — Inter-Branch Stock Transfer (LOAD-BEARING)**: `transfer_challans` + `transfer_challan_items`. New `apply_transfer_out_to_state` primitive: reduces qty+value at sender's current avg cost, **avg UNCHANGED**, no COGS/revenue. Transfer IN reuses `apply_purchase_to_state` at the captured unit_cost. The 185.88 integrity is preserved across branches.
- **Phase 5 — Central Purchasing & Distribution**: `central_purchases` + `central_purchase_items`. HQ records bulk buys at Central Warehouse (virtual branch `BR-CENTRAL`), distributes to branches via transfer challans at the central bulk-buy price. Per-line `distributed_qty` + `remaining_qty` tracking.
- **Phase 6 — Global Price Push**: `price_pushes` table. `POST /api/hq/price-push` creates a push with `price_push_id` + delivery targets. `POST /api/sync/price-push` (Bearer auth) applies idempotently — re-delivery never double-applies. Activity log records `source='hq'`.
- **Phase 7 — Browser E2E + Multi-branch simulation + Release**: 28/28 multi-branch simulation checks pass (two isolated DBs, full flow via real HTTP). 20/20 browser E2E checks pass (7 new v8.0 pages + 17 v7.2 pages, zero console errors). 328/328 tests pass.

### New API endpoints (additions only — no existing paths changed)

- `GET/PUT /api/branch-config` — local branch identity
- `POST /api/hq/branches/code` — generate 6-digit registration code
- `POST /api/hq/branches/register` — branch registers with HQ (public, uses code)
- `GET /api/hq/branches` — list registered branches
- `DELETE /api/hq/branches/{id}` — revoke a branch
- `POST /api/sync/branch-summary` — branch pushes daily summary (Bearer auth, idempotent)
- `GET /api/hq/owner-hub` — consolidated dashboard data
- `POST /api/transfers/out` — sender creates a challan (applies transfer OUT)
- `GET /api/transfers` — list challans (filter by status/direction)
- `GET /api/transfers/{id}` — get a challan with items
- `POST /api/transfers/{id}/accept` — receiver accepts (applies transfer IN)
- `POST /api/transfers/{id}/reject` — receiver rejects (no state change)
- `POST /api/central-purchases` — record a bulk buy at Central Warehouse
- `GET /api/central-purchases` — list central purchases
- `GET /api/central-purchases/{id}` — get a central purchase with items + distribution status
- `POST /api/central-purchases/{id}/distribute` — distribute items to a branch
- `POST /api/hq/price-push` — HQ creates a price push
- `POST /api/sync/price-push` — branch applies a price push (Bearer auth, idempotent)
- `GET /api/hq/price-pushes` — list price push history

### New frontend pages

- Branch Settings — `/settings/branch`
- HQ Branch Registry — `/insights/hq-branches`
- Owner Hub — `/insights/owner-hub`
- Transfer Out — `/transfers/out`
- Transfers (list + Transfer In) — `/transfers/in`
- Central Purchases — `/central-purchases`
- Price Push — `/insights/price-push`

### Multi-store architecture

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│   Branch A   │         │      HQ      │         │   Branch B   │
│  (Lahore)    │         │  (Owner Hub) │         │  (Karachi)   │
│              │         │              │         │              │
│  Local SQLite│ ──────► │  branches    │ ◄────── │  Local SQLite│
│  Offline POS │  push   │  summaries   │  push   │  Offline POS │
│  Fast        │ summary │  leaderboard │ summary │  Fast        │
│              │         │              │         │              │
│  185.88 avg  │ ◄────── │  transfers   │ ──────► │  receives at │
│  (intact)    │ transfer│  price push  │ price   │  locked cost │
│              │  out    │  central buy │ push    │              │
└──────────────┘         └──────────────┘         └──────────────┘
```

### Transfer integrity (the load-bearing rule)

When Branch A sends stock to Branch B, the transfer respects the v5.0 moving weighted average:

- **Transfer OUT (Branch A):** `apply_transfer_out_to_state` reduces qty + stock-value at Branch A's current average cost. **No COGS, no revenue** — it is an inventory movement, not a sale. The average cost per piece is UNCHANGED.
- **Transfer IN (Branch B):** applies as a **purchase at the transferred unit_cost** (captured from Branch A's running average at the moment of transfer) via the existing `apply_purchase_to_state`. Branch B's moving average updates correctly.
- The challan carries `unit_cost` per line explicitly. Never recomputed on the receiving side.

**Verified end-to-end:** Branch A (17,000 pcs @ 185.88) transfers 100 pcs to empty Branch B → Branch A still @ 185.88 (UNCHANGED), Branch B now @ 185.88, total stock = 17,000 (16,900 + 100), no COGS/revenue on either side.

> **HQ as Central Warehouse:** The HQ instance can act as both the aggregation hub *and* a virtual Central Warehouse branch (`branch_id='BR-CENTRAL'`). This means HQ can hold stock, record bulk purchases, and distribute to branches — it is not limited to read-only aggregation. When HQ receives a transfer challan, its local `category_stock_state` updates just like any other branch. This dual role is intentional: a small business with one owner-operated shop + one satellite branch can run the owner's shop as HQ (with the Owner Hub dashboard) and the satellite as a registered branch, without needing a third "central warehouse" instance.

### Sync protocol

- **Branch → HQ (push):** each branch pushes a daily summary (sales, COGS, gross profit, expenses, cash-in-drawer, stock snapshot) to HQ via `POST /api/sync/branch-summary`, authenticated by a per-branch Bearer token.
- **HQ → Branch (push):** HQ pushes transfer challans, price-category updates, and central-purchase distributions to a branch via that branch's Cloudflare Tunnel URL.
- **Idempotency:** every synced entity carries a stable idempotency key (`challan_no`, `summary_date+branch_id`, `price_push_id`). Re-delivery never double-applies.
- **Eventual consistency via outbox:** the `sync_outbox` table stores pending deliveries on the branch side. `app/sync.py` provides `queue_sync_outbox()` (add a pending entry, idempotent by entity_key) and `flush_sync_outbox()` (attempt delivery of all pending entries — on success marks 'sent', on failure increments attempts + leaves 'pending' for the next retry). If HQ is unreachable, the branch keeps selling normally; when HQ comes back, the next flush delivers the accumulated entries. **Never blocks a sale.** Verified end-to-end: queue → HQ unreachable → flush fails (entry stays pending, attempts=1) → HQ back → flush succeeds (entry 'sent', HQ has the summary).
- **Outbox status:** `GET /api/sync/outbox` returns pending/sent/failed counts + recent entries. `POST /api/sync/outbox/flush` with `{dest_url, bearer_token}` triggers a manual flush.

### Test coverage

331 tests across 38 files, all passing. v8.0 added 79 new tests across 6 new test files (`test_v8_phase1.py` through `test_v8_phase6.py`), plus 3 sync-retry tests in `test_v8_01_sync_retry.py`. The multi-branch simulation script (`scripts/v8_p7_multi_branch_sim.py`) boots two isolated SQLite DBs and runs the full flow via real HTTP — 28/28 checks pass. The sync-retry test (`tests/test_v8_01_sync_retry.py`) proves eventual consistency: queue → HQ unreachable → flush fails (entry stays pending) → HQ back → flush succeeds. The browser E2E script (`scripts/v8_p7_browser_e2e.py`) loads all 24 pages (7 new v8.0 + 17 v7.2) with zero console errors.

### Hard constraints satisfied

- No existing API path renamed/removed, additions-only API
- No frontend framework. Vanilla ES modules + SnowUI tokens + `.pos-page-header` + inline SVG
- SQLite only; migrations via `init()` PRAGMA/ALTER; zero data loss
- Files ≤ 700 lines (split when approaching)
- Browser E2E with zero console errors is the release gate (20/20 pass)
- FREE PATH ONLY — no paid APIs/services
- The 185.88 running weighted-average engine is UNCHANGED — inter-branch transfers use `apply_transfer_out_to_state` (new) + `apply_purchase_to_state` (existing), never recompute cost with simple averages
- Branch purity: a branch's local database is NOT polluted with other branches' transactional data. Cross-branch identity is applied at sync time, not stored on every local row.
- Single-shop mode: with `role='branch'` + empty `hub_url` (defaults), the app behaves exactly as v7.2

## What's New in v7.2 — "AI Usability Sprint"

v7.0 built the backend AI infrastructure (agent, tools, cache, budget, approval queue, kill switch, constrained SQL, trends engine, season-prep agent). v7.1 added frontend pages but only API-verified. **v7.2 is the frontend-only sprint that makes the AI engine actually usable**, with **real-server E2E** as the release gate. **v8.0 extended the agent with 3 multi-branch READ tools** (`get_owner_hub`, `get_branches`, `get_transfers`) so the AI can answer consolidated questions across all branches.

### Phase summary

- **Phase 1 — Grounding + debt**: Split `extensions.py` (693 lines → 4 modules under 250 lines each: `ext_pos.py`, `ext_intel.py`, `ext_comm.py` + 48-line shim). Added `PUT /api/pending-actions/{id}` to edit payload before approving.
- **Phase 2 — Approval Queue page (the backbone)**: Action cards with what/why/impact sections, per-type icons, Edit modal, PIN modal for price changes, batch grouping header with per-status counts, collapsible JSON payload preview. Sidebar badge with auto-refresh (60s + on hashchange). Fixed `count` field to return TRUE total matching rows (was returning `len(rows)` capped by `limit`).
- **Phase 3 — Agentic Chat UI**: Collapsible tool trace (summary + full details), kill-switch banner disabling input + send button when ON, clear chat button, parity badge linking to Margins page when answer contains a % number.
- **Phase 4 — AI Usage Dashboard**: 4 stat cards (calls today/14d, cache rate, tokens, cached entries), stacked 14-day Chart.js bar chart (API calls + cache hits), recent failures table, TTL legend card, clear-cache button (audited via activity_log).
- **Phase 5 — AI Automations settings page**: Dedicated `/settings/ai-automations` with friendly per-automation metadata (9 automations with icons + descriptions + levels). All toggles OFF by default — no surprise automation. Kill-switch-aware (toggles disabled visually when ON). "Prepare for Season" button opens modal with 10 preset seasons + custom name field.
- **Phase 6 — Kill Switch Sweep**: Floating help FAB turns orange + shows "AI OFF" badge when kill switch is ON, modal shows degraded-state banner. Auto-refreshes every 60s. Verified that AI stops (ai_call, agent, help_assistant all blocked) while heuristics (trends, break-even, margin alerts, profit endpoints) continue.
- **Phase 7 — E2E + Release**: 35-check real-server E2E (uvicorn subprocess + httpx) — all pass. 236 tests pass (was 188 at v7.1).

### New API endpoints (additions only — no existing paths changed)

- `PUT /api/pending-actions/{id}` — edit payload/reason/impact before approving
- `GET /api/ai/usage/14d` — per-day usage for last 14 days (zero-filled)
- `GET /api/ai/failures` — recent failed AI calls (no output produced)
- `POST /api/ai/clear-cache` — wipe the ai_cache table (audited)
- `GET /api/ai/ttl-legend` — cache TTL legend for dashboard display

### New frontend pages (Insights + Settings apps)

- Approval Queue — `/insights/approval-queue` (enhanced)
- AI Assistant (Agent Chat) — `/insights/agent` (enhanced)
- AI Usage Dashboard — `/insights/ai-usage` (enhanced)
- AI Automations — `/settings/ai-automations` (NEW)

### Test coverage

236 tests across 31 files, all passing. v7.2 added 45 new tests across 5 new files (`test_v7_2_phase1.py` through `test_v7_2_phase6.py`). The 35-check real-server E2E script lives at `scripts/v7_2_e2e.py` and boots a real uvicorn subprocess.

### Hard constraints satisfied

- No existing API path renamed/removed, additions-only API
- SQLite only, additive migrations only
- Vanilla JS ES modules (no framework)
- Files ≤ 700 lines (`extensions.py` shim is 48 lines, was 693)
- SnowUI tokens, `.pos-page-header`, inline SVG throughout
- WRITE tools NEVER execute directly — always through `pending_actions` + approval + PIN for price changes
- All automation toggles OFF by default
- Kill switch verified: AI stops, heuristics continue

### Real-server E2E (Phase 7 release gate)

`scripts/v7_2_e2e.py` boots a real uvicorn server on port 8767 against a temp DB seeded with sample data, then exercises:
1. Server boot + `/login` page render
2. Login as manager + cookie set
3. AI Usage Dashboard endpoints (5 checks)
4. Approval Queue create → edit → approve lifecycle (6 checks)
5. Agent endpoints — `/api/agent/ask` returns answer + trace + followups (5 checks)
6. Constrained SQL — blocks DROP, blocks forbidden tables, allows allowlist (3 checks)
7. Kill switch ON blocks agent (2 checks)
8. Season-prep creates batched pending actions (4 checks)
9. AI Automations config endpoint returns all 10 keys (1 check)
10. Clear-cache endpoint (1 check)
11. Static JS pages served (4 checks — one per new page)

**Result: 35/35 PASS.**

### v7.2 Release Audit — Score Reconciliation

The v7.1 review flagged a documentation discrepancy: a 10-dimension audit table showed dimension scores summing to 870 (mean 87.0) but the stated overall release score was 86. This was a **documentation rounding error**, not a code bug. For v7.2, the audit is reconciled below — the overall is computed as the **arithmetic mean of the 10 dimension scores**, rounded to the nearest integer.

| # | Dimension | Score | Notes |
|---|---|---|---|
| 1 | Scope delivered | 9 | All 4 AI pages built + extensions split + 7-day expiry + L2/L3 badges |
| 2 | Backend test rigor | 9 | 236/236 tests pass, +48 new in v7.2 (5 new test files) |
| 3 | Frontend verification | 9 | 30/30 Playwright browser E2E checks pass, ZERO console errors across 21 pages |
| 4 | Real-server E2E | 9 | 35/35 httpx checks pass (`scripts/v7_2_e2e.py`) |
| 5 | Browser interactive flows | 9 | Margin parity, approve→data-change, PIN gate, Edit modal, kill-switch banner — all by clicking |
| 6 | Hard constraints | 9 | Additions-only API, files ≤700 lines, SQLite only, vanilla JS, all automations OFF default |
| 7 | Honesty | 9 | Browser E2E is actual Playwright + Chromium (not relabeled HTTP E2E); gap from v7.1 closed |
| 8 | Docs | 8 | README + INSTALL_GUIDE both at v7.2; this audit table reconciles the 87-vs-86 math |
| 9 | Kill switch sweep | 9 | 12 tests verify AI stops + heuristics continue; browser confirms banner + disabled input |
| 10 | Process hygiene | 8 | 9 commits, new tag v7.2 (immutable from Phase 7 onward); score-math doc bug acknowledged |
| | **Sum** | **88** | |
| | **Mean (overall)** | **8.8 → 9** | Reconciled: arithmetic mean of 10 dimensions |

The 87-vs-86 discrepancy from v7.1 was caused by mixing a weighted mean (which gave 86) with an unweighted mean (which gave 87) in the same audit table. v7.2 uses a single method throughout: **arithmetic mean of dimension scores**, rounded to the nearest integer. The overall v7.2 score is **9/10**.

## What's New in v7.0 + v7.1 — "AI Engine + Frontend Foundation"

- **v7.0** — AI infrastructure: agent core, READ/WRITE tools, constrained SQL, approval queue, kill switch, AI router (cache + budget + degradation), trends 2.0 (internal velocity + z-score), automation suite, flagship season-prep agent.
- **v7.1** — Frontend foundation: Approval Queue page, Agent Chat page, AI Usage page, Help system with 40+ FAQ + floating AI chat button.

## What's New in v5.0 — "Store Profit & Inventory Management System"

This release rebuilds the profit & inventory engine around **Running Weighted Average Cost** — the accounting method the owner requires. Every COGS, margin, and profit number now correctly accounts for the order of purchases and sales.

### The core fix (Phase 1)

The old system used `AVG(bi.price)` — a simple average of all purchase prices. The new system uses a **running weighted average** that tracks remaining stock:

```
New Avg Cost = (Existing Stock Value + New Purchase Value) ÷ (Existing Stock Qty + New Purchase Qty)
```

**The 185.88 example (verified end-to-end):**

| Step | Event | Qty | Value | Avg Cost |
|---|---|---|---|---|
| Opening | 10,000 pcs @ Rs 180 | 10,000 | 18,00,000 | 180.00 |
| Sale | sell 3,000 | 7,000 | 12,60,000 | 180.00 (sales don't change avg) |
| Purchase | buy 10,000 @ Rs 190 | 17,000 | 31,60,000 | **185.88** |

A simple weighted average of purchases would give Rs 185.00 — wrong by Rs 0.88/piece.

### Phase summary

- **Phase 1 — Running Weighted Average Cost Engine**: New `app/profit.py` with `category_stock_state` table, `apply_purchase_to_state`, `apply_sale_to_state`, `rebuild_stock_state` (source of truth — replays all bills + sales chronologically). Wired into bill confirm, create_sale, refund, and stock adjustments.
- **Phase 2 — Inventory Accuracy**: `get_inventory()` reads from running state; `negative_stock` flag; "Rebuild Stock State" button.
- **Phase 3 — Dual Margin Display**: `GET /api/profit/margins` returns both Category Average Margin (informational) and Actual Overall Gross Margin (primary KPI, sales-mix weighted).
- **Phase 4 — Monthly Actual Profit (COGS method)**: `COGS = Opening + Purchases − Closing`; cross-check field `cogs_from_sales`; GP and Operating Profit shown separately.
- **Phase 5 — YTD Profit**: `YTD margin = Cumulative GP ÷ Cumulative Sales` (NOT avg of monthly margins — Rule 10).
- **Phase 6 — Daily Stock Report**: 11-column per-category daily movement with totals + CSV export.
- **Phase 7 — Cash Buckets + Owner Withdrawal + Stock Reserve**: 4 buckets (Stock Replacement, Operating Expenses, Business Reserve, Owner Withdrawal); stock reserve days-of-cover with color coding; safe weekly withdrawal guidance.
- **Phase 8 — Store Profit Dashboard** (hero page): aggregates all 6 KPI groups; answers the owner's 9 questions on one page; Actual Overall Gross Margin is the most prominent number; default landing for `/reports`.
- **Phase 9 — E2E + release**: 10-item checklist verified, 131 tests pass.

### New API endpoints (additions only — no existing paths changed)

- `POST /api/inventory/rebuild-stock-state`, `GET /api/inventory/stock-state`
- `GET /api/profit/margins`, `/api/profit/monthly`, `/api/profit/ytd`, `/api/profit/dashboard`
- `GET /api/profit/cash-buckets`, `GET /api/stock-reserve`
- `GET /api/reports/daily-stock`, `/api/reports/daily-stock/export`
- `POST/GET /api/owner-withdrawals`, `GET /api/owner-withdrawals/summary`

### New frontend pages (Reports app)

- Store Profit Dashboard (default landing) — `/reports/store-profit`
- Margins — `/reports/margins`
- Monthly Profit — `/reports/monthly-profit`
- YTD Profit — `/reports/ytd`
- Daily Stock Report — `/reports/daily-stock`
- Cash Buckets — `/reports/cash-buckets`

### Test coverage

131 tests across 17 files, all passing. The load-bearing test is `test_doc_example_185_88` which proves the running weighted average produces Rs 185.88 (not Rs 185.00).

## What's New in v4.0 — "Financial Truth & Control"

This release makes the money honest and protected:

- **Phase 1 — COGS Integrity**: Weighted-average cost per category (with dozen conversion), auto-populated at sale time. `POST /api/maintenance/recalc-cogs` for backfill. `cogs_warning` activity log fires when a sale has no cost history.
- **Phase 2 — Expense Management Module**: First-class expenses with categories (Rent, Salaries, Electricity, ...), `is_fixed` flag, monthly budgets, recurring expenses (idempotent auto-generation on app startup + lazy in `GET /api/expenses`), and `expense_type` ('operating' | 'owner_draw'). Owner draws are excluded from P&L operating expenses. New Expenses page in the Reports app with budget-vs-actual progress bars.
- **Phase 3 — Actual Earnings Dashboard**: New hero page at `/reports/earnings` (now the Reports default). Waterfall bridge (Sales → −COGS → =Gross Profit → −Op Exp → =Actual Earnings), expenses-by-category bars, and a "Cash Reality" panel explaining why drawer cash ≠ profit (tied stock, owed to you, you owe suppliers).
- **Phase 4 — Cash & Theft Controls**: Settings-driven approval gates (`max_discount_pct_without_pin`=10, `require_pin_for_refund`=true, `require_pin_for_price_override`=true, `blind_close_enabled`=false). Manager PIN modal in POS checkout. Refunds, discount overrides, and large shift variances logged as `suspicious` activity. Denomination-aware shift close with blind mode. New `/reports/suspicious` and `/reports/audit` pages with CSV export.
- **Phase 5 — Wholesale Money Flows**: Supplier advances (peshgi) with bill linkage, agreed rate list (auto-flags bill items priced above the agreed rate on confirm), bank accounts + transactions ledger with paired cash→bank deposit entries.
- **Phase 6 — Owner Awareness & Staff**: `GET /api/summary/daily` returns today's sales/cash/credit split, top categories, low-stock count, overdue urdhaar, and shift variances. `build_whatsapp_summary_link` produces a pre-filled wa.me link (no API cost). Commission rules (percent or flat, employee-specific overrides role-level). Cashier scorecard per employee.

All 87 tests pass. Each phase has an end-to-end acceptance script in `/home/z/my-project/scripts/`.

## Quick Start

### 1. Install Python (one-time)
Download Python 3.11+ from https://python.org and install. Tick "Add Python to PATH".

### 2. Set up the project
```bat
mkdir C:\billbook
cd C:\billbook
python -m venv venv
venv\Scripts\activate
```

Copy all the files from this package into `C:\billbook\`, then:
```bat
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set `APP_PASSWORD` to a secure password (min 8 characters). Optionally add your free Gemini API key from https://aistudio.google.com/apikey

### 3. Run
Double-click `start.bat`, or:
```bat
venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 in your browser.

## What's New in v2.0 (SnowUI Multi-App)

Complete redesign with a modern multi-app architecture:

- **SnowUI Design System** — Light/dark theme, soft shadows, pastel color chips, no emoji main icons
- **7 Apps** — POS, Billing, Inventory, Customers, Reports, AI Insights, Settings (each with own sidebar nav)
- **App Launcher** — Full-screen launcher with app cards, RBAC-filtered, keyboard shortcuts
- **App Shell** — Per-app topbar + sidebar with breadcrumbs, Ctrl+K command palette
- **PWA Offline** — Cache-first service worker, IndexedDB offline sales queue, background sync
- **Staff UI** — Employee management with PIN-based POS login, roles (cashier/manager/admin)
- **AI Assistant** — Full chat interface with localStorage history, powered by Groq
- **10 Report Pages** — Overview, Billwise, P&L, Cash Flow, Balance Sheet, Top Items, Peak Hours, Targets, Monthly Close, Export Center
- **Zero window.* globals** — All handlers use closures + data-* attribute binding

## Features

### POS App
- **Category buttons** with live stock badges + out-of-stock warnings
- **Cart with qty controls**, line notes, quick discount
- **Held orders** (park & recall), quotations (save cart as quote)
- **Customer search** with loyalty + outstanding credit info
- **Loyalty point redemption** (auto-converts to rupee discount)
- **Split payments** (cash + card + online)
- **Returns & exchanges** with reason tracking
- **Shifts** — start/end with cash reconciliation + variance tracking
- **Cash drawer** — cash in/out with audit trail
- **Z-Report** — end-of-day reconciliation with payment breakdown
- **Barcodes** — print QR + barcodes for category buttons, scan to add to cart
- **Offline sales** — queue sales in IndexedDB when offline, auto-sync on reconnect
- **Kiosk mode** — PIN-protected exit (3-layer security: router + CSS + inline script)

### Billing App
- **Bill upload** — PDF, JPG, PNG, WebP. Multi-page bills supported
- **AI extraction** — Gemini / Groq / OpenRouter with automatic fallback
- **Bill review** — Split-screen image + editable form, real-time total mismatch detection
- **All Bills** — bulk selection, inline payment/total editing, undo on delete
- **Review Queue** — bills awaiting confirmation with flag chips
- **Suppliers** — full CRUD, per-supplier analytics, reliability score, WhatsApp reminders
- **Payments** — outstanding credit tracking, record payments, overdue alerts

### Inventory App
- **Stock Overview** — search across all bill items, compare prices, track history
- **Stock Levels** — live stock per category (purchased - sold + adjustments)
- **Adjustments** — manual stock corrections with audit trail + reason
- **Purchase Orders** — create POs, send via WhatsApp, mark received
- **Reorder Reminders** — auto-generated low-stock alerts, dismiss/mark ordered
- **Dead Stock** — slow-moving inventory clearance suggestions

### Customers App
- **All Customers** — searchable list with tier badges (Bronze/Silver/Gold/Platinum)
- **Credit Outstanding** — customers with unpaid credit, record payments
- **Loyalty Tiers** — tier breakdown, top customers, redemption history
- **Import** — bulk CSV import with phone deduplication
- **Customer Detail** — profile, recent sales, payments, loyalty redemption history

### Reports App
- **Overview** — 4-tab view (Overview/Billwise/Category/Suppliers) with Chart.js trend
- **Billwise** — per-bill drill-down with profit margins
- **P&L Statement** — monthly income statement with COGS + expenses breakdown
- **Cash Flow** — monthly inflows/outflows + net cash position
- **Balance Sheet** — assets, liabilities, owner's equity snapshot
- **Top Items** — best performers by quantity and revenue
- **Peak Hours** — 24-hour heatmap for staff scheduling
- **Sales Targets** — daily/monthly targets with progress bars
- **Monthly Close** — snapshot + PDF download
- **Export Center** — Bills Excel, Insights Excel, Bills CSV

### AI Insights App
- **AI Assistant** — full chat interface with localStorage history, typing indicator, suggestion buttons
- **ABC Analysis** — Pareto classification (A=80%, B=15%, C=5%) with strategy tips
- **Trends** — AI-analyzed trend alerts, seasonal patterns, dismiss/act-on actions
- **Forecast** — 3/6/12-month spend forecast with Chart.js (solid historical + dashed forecast)

### Settings App
- **General** — price categories, AI providers (with test connection), extraction accuracy
- **Employees** — staff CRUD, PIN management (4-8 digits), role assignment, activate/deactivate
- **Tax & SMS** — GST rate (inclusive/exclusive), Twilio SMS config
- **Backups** — one-click backup, history table
- **Security** — change password, active sessions with revoke
- **Appearance** — theme picker (visual previews), accent color, density, font scale

## Security

- **Single-user password auth** with bcrypt hashing
- **Session tokens** are 32-byte cryptographically-random, stored in SQLite (persist across restarts)
- **Login throttling** — IP-based rate limiting after 5 failed attempts
- **Cookies** are HttpOnly, SameSite=Strict
- **CORS** restricted to localhost
- **Binds to 127.0.0.1** — not exposed to LAN by default
- **Employee PINs** — 4-8 digits, stored in DB, required for POS login
- **Fernet encryption** for AI provider API keys at rest

## PWA / Offline

- **Installable** — Add to Home Screen on Android/iOS, install on desktop
- **4 PWA shortcuts** — POS, Bills, Reports, AI Assistant
- **Cache-first static assets** — instant load + offline access
- **Offline sales queue** — POS works offline, sales stored in IndexedDB
- **Background sync** — queued sales auto-sync when back online
- **5-attempt retry** with sync_attempts counter, gives up after 5 tries

## Keyboard Shortcuts

- `D` — Dashboard
- `P` — POS
- `B` — Bills
- `F` — Items (Stock Overview)
- `S` — Suppliers
- `R` — Reports
- `I` — AI Insights
- `C` — Customers
- `,` — Settings
- `N` — New Bill
- `H` — App Launcher
- `Ctrl+K` — Command Palette (search apps, pages, bills)
- `1-8` — POS app sub-pages (when in POS shell)
- `F1-F4` — POS category shortcuts
- `F9` — POS checkout
- `F10` — Hold order
- `F12` — Save quotation

## Project Structure
```
billbook/
├── requirements.txt
├── start.bat
├── backup.bat
├── pytest.ini              # pytest config (PR 8)
├── .github/workflows/
│   ├── desktop.yml         # Tauri desktop build (on tag push)
│   └── test.yml             # pytest CI job (on every push + PR — PR 8)
├── app/
│   ├── __init__.py
│   ├── config.py            Paths & settings
│   ├── db.py                SQLite schema + helpers + write_tx()/read_tx() + log_activity(c=)
│   ├── money.py             Decimal-based money()/money_d() rounding (PR 1)
│   ├── crypto.py            API key encryption/decryption (Fernet)
│   ├── security.py          bcrypt password hashing + session management
│   ├── profit_engine.py     Running weighted-avg cost engine (c-aware mutators — PR 2)
│   ├── main.py              FastAPI app, middleware, /api/health, /api/version (PR 8)
│   ├── ingest.py            Image preprocessing & PDF rendering
│   ├── extract.py           AI extraction (multi-provider)
│   ├── validate.py          Validation, profit calc, duplicate detection
│   ├── reports.py           Monthly / profit / supplier reports
│   ├── insights.py          ABC, dead stock, price comparison, alerts, forecast
│   ├── trends.py            Market trends, reorder reminders, seasonal alerts
│   ├── export.py            Excel export (openpyxl)
│   ├── shop.py              Inventory, customers, expenses, shifts, employees
│   ├── pos_extra.py         Purchase orders, held orders, quotations
│   ├── pos_import_sync.py   Ezi POS DBF import (per-sale atomic — PR 6)
│   ├── ezi_import.py        Legacy Ezi DBF parser
│   ├── jobs.py              Async job queue + SSE streaming
│   ├── routers/             Route handlers (split from main.py in v6.0)
│   │   ├── __init__.py
│   │   ├── auth.py          Login, logout, setup wizard, device pairing
│   │   ├── pos.py           /api/sales (atomic create_sale — PR 3), refunds, cash drawer
│   │   ├── bills.py         Bill confirm (atomic + OCC — PR 5), bill CRUD
│   │   ├── inventory.py     Stock state, adjustments, rebuild
│   │   ├── customers.py     Customer CRUD, loyalty, credit
│   │   ├── suppliers.py     Suppliers, advances, rate lists
│   │   ├── reports.py       P&L, cash flow, balance sheet, daily stock
│   │   ├── insights.py      ABC analysis, dead stock, forecasts
│   │   ├── settings.py      Shop profile, tax, appearance, AI providers
│   │   ├── imports.py       CSV import, generic POS import
│   │   ├── pos_import_router.py  Ezi POS zip upload + delete
│   │   ├── profit.py        Margins, running avg, COGS endpoints
│   │   ├── extensions.py    Bundles, happy-hour, break-even
│   │   ├── hq.py            Multi-branch registry + sync
│   │   ├── transfers.py     Inter-branch stock transfers
│   │   ├── central.py       Central purchasing + distribution
│   │   ├── remote_access.py Cloudflare Tunnel toggle
│   │   ├── maintenance.py   Backup, update check, diagnose
│   │   └── audit.py         AI Auditor
│   └── static/
│       ├── index.html       SPA entry + SW registration + PWA meta
│       ├── manifest.json    PWA manifest with 4 shortcuts
│       ├── sw.js            Service Worker (cache-first + offline queue)
│       ├── css/
│       │   ├── design-system.css   SnowUI design tokens (490 lines)
│       │   ├── launcher.css        App launcher styles
│       │   └── shell.css           App shell + page patterns (830 lines)
│       ├── styles/          Legacy CSS (base, components, layout, pages)
│       └── js/
│           ├── app.js       SPA entry + route imports
│           ├── router.js    Hash router + shell rendering
│           ├── api.js       Fetch wrapper
│           ├── utils.js     Formatters, toast, modal, icons
│           ├── core/
│           │   ├── shell.js       App shell + command palette + getAppForPath
│           │   ├── launcher.js    Full-screen app launcher
│           │   ├── theme.js       Theme engine
│           │   └── offline.js     IndexedDB offline queue (PWA)
│           ├── components/   Reusable UI components
│           └── pages/        18 route modules (each < 700 lines)
├── desktop/                  Tauri desktop shell (Windows + macOS)
│   ├── tauri.conf.json       CSP, window, updater config (PR 7 hardens CSP)
│   ├── Cargo.toml
│   └── src/                  Rust sidecar wrapper
├── mobile/                   Capacitor mobile shell
└── data/                    Auto-created on first run
    ├── billbook.db          SQLite database (WAL mode)
    ├── billbook.db-wal      WAL file (size monitored by /api/health)
    ├── uploads/             Original uploaded files
    ├── pages/               Rendered PNG pages for display
    └── backups/             Manual + scheduled backups
```

## Data Storage

All data lives in `data/`:
- `billbook.db` — SQLite database (WAL mode for concurrent reads)
- `uploads/` — Original uploaded files
- `pages/` — Rendered PNG pages for display
- `backups/` — Manual + scheduled backups (last 10 retained)

## Backup Strategy

1. **In-app**: Settings → Backups → "Backup Now" button
2. **Scheduled**: Run `backup.bat` via Windows Task Scheduler nightly
3. **Cloud sync**: Point Google Drive / OneDrive at `data/backups/` for offsite copies

## Troubleshooting

- **"login required" loop**: Clear cookies for localhost:8000, restart the server
- **AI extraction fails**: Check Settings → General → AI Providers. Make sure at least one has a valid key (use "Test Connection")
- **Port 8000 in use**: Edit `start.bat` and change `--port 8000` to another port
- **Database locked**: Stop all running instances, delete `data/billbook.db-wal` and `data/billbook.db-shm`, restart
- **Offline sales not syncing**: Check browser console for SW errors. Try Settings → Security → revoke sessions, re-login
- **New version not loading**: Hard refresh (Ctrl+Shift+R) or clear cache. SW updates show a toast — reload to activate

## Tech Stack

- **Backend**: FastAPI + SQLite (WAL), 170+ endpoints, 22+ tables
- **Frontend**: Vanilla JS ES modules (no framework), hash router, kiosk mode, PWA
- **Design**: SnowUI design system — light/dark, soft shadows, pastel chips, SVG icons
- **AI**: Gemini (vision extraction) + Groq (business intelligence chat)
- **Charts**: Chart.js 4.4 (trend lines, forecasts, heatmaps)
- **Fonts**: Inter (Google Fonts)
- **PWA**: Service Worker (cache-first), IndexedDB (offline queue), Web App Manifest
