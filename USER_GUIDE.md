# BillBook User Guide

> **Version:** v8.12 | **For:** Shop owners and managers | **Audience:** Non-technical users

This guide covers everything you need to use BillBook day-to-day. If you're setting up for the first time, start with "Day 1: First Launch." If you're already running, jump to the section you need.

---

## Table of Contents

1. [Day 1: First Launch (Setup Wizard)](#day-1-first-launch)
2. [Daily POS Operations](#daily-pos-operations)
3. [Uploading Supplier Bills](#uploading-supplier-bills)
4. [Importing from Your Existing POS (Ezi POS)](#importing-from-ezi-pos)
5. [Inventory & Stock Management](#inventory--stock-management)
6. [Reports & Profit](#reports--profit)
7. [AI Auditor & Safe Withdrawal](#ai-auditor--safe-withdrawal)
8. [Expenses](#expenses)
9. [Customers & Credit](#customers--credit)
10. [Suppliers & Payables](#suppliers--payables)
11. [Multi-Branch Setup](#multi-branch-setup)
12. [Remote Access (Access from Phone)](#remote-access)
13. [Backup & Maintenance](#backup--maintenance)
14. [Troubleshooting](#troubleshooting)
15. [Keyboard Shortcuts (Global)](#keyboard-shortcuts-global)

---

## Day 1: First Launch

When you first install BillBook, a **4-step wizard** appears automatically:

### Step 1: Set Your Password
- Enter a password (minimum 8 characters)
- The strength meter shows Weak/Fair/Strong
- Confirm the password
- Click **Next**
- **Security note (v8.6+)**: Your password is bcrypt-hashed and never stored in plaintext. It also derives a Fernet encryption key that protects all API keys you add later. If you ever change your password, BillBook re-encrypts every stored API key atomically — no key ever becomes unreadable.

### Step 2: Pick Your Business Type
- **Wholesale** (default): Pre-fills 4 categories (A=Rs 250, B=Rs 500, C=Rs 750, D=Rs 1000)
- **Retail**: Pre-fills 3 categories (Small=Rs 100, Medium=Rs 300, Large=Rs 600)
- **Custom**: Starts with 1 category you can expand

### Step 3: Confirm Categories
- Edit category names, codes, and prices
- Click **+ Add Category** to add more
- Click the **×** button to remove a category
- These become your POS price buttons

### Step 4: Optional AI + Finish
- **Gemini API Key** (optional): Paste a Google Gemini key to enable AI features. Skip if you don't have one — the system works fully offline without it. The key is **encrypted at rest** (v8.6+).
- **Start Page**: Choose where BillBook opens each day:
  - **Launcher** (default): App picker screen
  - **Dashboard**: Store profit overview
  - **POS**: Cashier kiosk mode (for cashier devices)
- Click **Finish Setup**

You're now logged in and on your chosen start page. The wizard never appears again.

> **Already using BillBook?** If you're upgrading from a previous version, the wizard is skipped automatically — your existing password and settings are preserved.

---

## Daily POS Operations

The POS screen (v8.12) uses a refined three-column layout with a **dark navy theme** for high-contrast visibility at the cashier counter. The left column shows your category tiles; the center column shows the live cart; the right column shows totals, payment method, and the checkout button.

### Making a Sale
1. Open BillBook → go to **POS** (or press `P`)
2. (Optional) Pick a **QTY multiplier** at the top of the items panel: ×1 / ×2 / ×3 / ×5 / ×10. Each tap of a category button will add that quantity in one click. This is a huge time-saver when a customer buys 10 of the same item.
3. Tap a **category tile** (A, B, C, D, …) to add it to the cart. Tiles are colored by tier (emerald / sky / amber / violet / pink / teal / rose) and show a **hotkey number** in the top-right corner (1–7) for keyboard users.
4. Adjust the quantity using the **−** / **+** stepper on the cart line, or click the quantity number itself to open the numeric keypad.
5. (Optional) Apply a **per-line discount** — tap the edit (pencil) icon on the cart line. You can pick a quick % (0/5/10/15/20/25/50), enter a custom %, enter a fixed Rs amount, or override the unit price entirely (manager PIN required).
6. (Optional) Pick a customer (type a name + phone, or use the search button).
7. (Optional) Apply an **invoice-level discount** at the top of the right panel (0/5/10/15/20% quick chips, or a custom % / Rs amount).
8. Pick a **payment method**: Cash, Card, Online, Credit, or Split. For online payments, you can also choose a sub-method (Easypaisa, JazzCash, Raast QR, Bank Transfer) for cleaner reporting.
9. For cash payments: enter the cash received (use the **+100 / +500 / +1000 / +5000 / Exact** quick buttons), and the change due is calculated automatically.
10. Click **Complete Sale** (or press `F9`).
11. A **Sale Complete** confirmation modal appears with a green checkmark, the invoice number, the total, the payment method, and any change due. Click **New Sale** to clear and start fresh, or **Receipt** to print.

### Keyboard Shortcuts (POS)
| Key | Action |
|-----|--------|
| 1–7 | Add categories 1–7 (the number badge on each tile shows its hotkey) |
| 1–5 (when not in a field) | Select QTY multiplier ×1 / ×2 / ×3 / ×5 / ×10 |
| F1–F7 | Add categories 1–7 (legacy, still supported) |
| F8 | Scan barcode |
| F9 | Checkout |
| F10 | Hold order (park current sale) |
| F11 | Clear cart (with confirm) |
| F12 | Save cart as a quote |

### Returns & Refunds
1. Go to **POS → History** to find the original sale (by invoice number, customer, or date).
2. Open the sale detail → click **Refund**.
3. The refund is processed **atomically** (v8.6+): the sale is marked refunded, stock is reversed at the original cost, the cash drawer is debited only by the original cash portion (split/card/online refunds do NOT touch the drawer), customer loyalty points are restored, and any commission is reversed. All of this happens in a single database transaction — if any step fails, the entire refund rolls back.
4. A manager PIN is required for refunds.

### Voiding a Sale (Admin Only)
For sales that were entered by mistake (wrong customer, wrong total, never actually happened), an admin can **void** the sale from the sale detail page. Voiding is a soft-delete — the sale row is preserved for audit but marked `voided`. Voided sales:
- Disappear from all KPI tiles, reports, and customer totals
- Are detected by the POS Import sync (if the voided sale exists in your Ezi POS backup, the importer will skip it on the next sync)

### Shift Management
1. **Start shift**: Open Cash Drawer → enter opening cash → Start
2. **End shift**: Close Cash Drawer → count cash → enter denominations → Close
3. The shift report shows: sales, cash in drawer, variance

### Offline Mode
If your internet drops (or you're on a flaky connection), the POS keeps working. Sales are queued locally and synced automatically when the connection comes back. The Sale Complete modal will show "Sale Queued (Offline)" with a "Try Sync Now" button.

---

## Uploading Supplier Bills

### Drag & Drop (Easiest)
1. Drag a PDF or photo of a supplier bill onto **any page** in BillBook
2. A "Drop to upload bill" overlay appears
3. BillBook navigates to the New Bill page and starts AI extraction automatically

### Manual Upload
1. Go to **Billing → New Bill**
2. Click **Upload** and select the file(s)
3. BillBook renders the pages and extracts the data using AI

### Reviewing & Confirming
1. After extraction, review the items, prices, and quantities
2. Assign each item to a **category** (A, B, C, D)
3. Check the supplier name and bill date
4. Click **Confirm Bill**
5. **Bill Intelligence** checks sell-through:
   - **Green** (≥80% sold): Well-timed — previous stock nearly sold out
   - **Yellow** (40–80%): Partially sold — check current stock
   - **Red** (<40%): Overstock risk — a soft pause asks you to confirm or cancel

> **Crash safety (v8.6+)**: Confirming a bill runs as a single atomic transaction. If anything fails mid-confirm (power outage, bug, etc.), the entire confirm rolls back — you'll never end up with stock_state updated but the bill still in "review" status, or vice versa.

> **Optimistic concurrency**: If two staff members open the same bill for editing and both hit Confirm, the second confirm gets a 409 "version mismatch" error instead of silently overwriting the first one's changes. Just refresh and re-confirm.

> **Note:** The original uploaded file is automatically deleted after page rendering. Only the rendered images are stored, saving disk space.

---

## Importing from Ezi POS

If you use a separate Ezi POS system, you can import daily sales data:

### Daily Workflow
1. At end of day, create a backup in your Ezi POS (it produces a `BU*.zip` file, e.g., `BU20260813.zip`)
2. Open BillBook → go to **Inventory → POS Import Sync**
3. Click **Choose File** and select the backup zip
4. Click **Run Dry-Run Analysis** first (v8.11+) — this scans the backup and reports:
   - Number of sales to import (new since last sync)
   - **Deleted sales detected** — sales that exist in BillBook but not in the backup. The importer will soft-delete these in BillBook to keep the two systems in sync.
   - **Modified sales detected** — sales whose line items have changed (SHA-256 checksum comparison). The importer will re-import these.
   - A configurable threshold (default 5%) — if the number of deletions exceeds this, you'll be asked to confirm before proceeding.
5. If the dry-run looks correct, click **Confirm & Import**.
6. BillBook extracts all sales and expenses from the backup chronologically (oldest first) so the running weighted-average cost is computed in the correct order.

### How Deduplication Works
Each backup zip contains the **full cumulative database** (all transactions from day 1). BillBook uses a unique code (`UNQCODE`) to skip records that were already imported. This means:
- **Re-importing the same backup**: 0 new records (all skipped as duplicates)
- **Importing a newer backup**: Only new transactions since last import are added
- **Safe to import every day**: No data ever duplicates

### Deleted-Sale Detection (v8.11+)
If a sale was deleted in your Ezi POS (e.g., a mistaken entry that the cashier voided), it will be missing from the latest backup. The importer detects this and offers to soft-delete the matching sale in BillBook so your reports stay consistent. The sale is NOT hard-deleted — it stays in the database with `payment_status='voided'` for audit purposes, but disappears from all reports and KPI tiles.

### Modified-Sale Detection (v8.11+)
If a sale's line items have changed in your Ezi POS (e.g., a refund was processed on the Ezi side that modified the original sale), the importer detects this via a SHA-256 checksum of the line items and re-imports the sale. The original sale is soft-deleted (preserved for audit) and the new version is imported fresh.

### Crash Safety (v8.6+)
The importer sets a `stock_state_dirty` flag **at the start** of the import (not the end). If the process crashes mid-import (e.g., power outage at sale #500 of #1000), the dirty flag is already set → the next time BillBook starts, it automatically rebuilds the stock state from scratch, correctly replaying all imported sales in chronological order.

### What Gets Imported
- **Sales**: All sale transactions (invoice number, date, amount, payment method)
- **Expenses**: Diary entries with amounts (e.g., "Ali = 360")
- **Shop info**: Company name and address

### After Import
- Sales appear in **Reports → Monthly Profit** and the **Store Profit Dashboard**
- Expenses appear in **Reports → Expenses**
- The profit ticker in the topbar updates with today's gross profit

---

## Inventory & Stock Management

### Stock Overview
Go to **Inventory → Stock Overview** to see:
- Current stock levels per category (the single source of truth — `category_stock_state`)
- Stock value (at running weighted average cost)
- All-time Purchased / Sold / Adjustments aggregates (informational — restored in v8.7)
- Negative stock warnings (red rows)

### Items Search (Bill-Wise)
Go to **Inventory → Items** (v8.7+): a **bill-wise master-detail view**:
- Lightweight list of bills with item_count, category_count, total_cost aggregates
- Click a bill row to expand its items inline (lazy-loaded via the existing bill detail endpoint and cached client-side)
- Search filters by supplier, bill_no, or item name

### Stock Adjustments
1. Go to **Inventory → Adjustments**
2. Select a category
3. Enter the adjustment (positive for additions, negative for reductions)
4. Enter a reason
5. The running weighted average cost is preserved

### Reorder Suggestions
Go to **Inventory → Reorder** to see categories below their reorder point.

### Dead Stock
Go to **Inventory → Dead Stock** to see categories with no sales in 90+ days.

### Rebuild Stock State
If your stock ever drifts (e.g., negative quantities, suspicious averages), go to **Inventory → Stock Overview → Rebuild Stock State**. This replays every confirmed purchase and every non-refunded sale from day 1 to recompute `category_stock_state` from scratch. It also clears the `stock_state_dirty` flag. Safe to run any time — it's idempotent.

---

## Reports & Profit

### Store Profit Dashboard (Main Report)
Go to **Reports → Store Profit** (or press `R` then `1`):
- **Actual Overall Gross Margin** (primary KPI)
- Total sales, COGS, gross profit
- Cash buckets breakdown
- Stock reserve days of cover
- Monthly and YTD profit

### Margins
Go to **Reports → Margins**:
- Category Average Margin (informational)
- Actual Overall Margin (primary KPI, sales-mix weighted)
- Per-category breakdown

### Monthly Profit
Go to **Reports → Monthly Profit**:
- COGS bridge: Opening + Purchases − Closing = COGS
- Gross Profit and Operating Profit shown separately

### Profit Analysis (v8.7+)
Go to **Reports → Profit Analysis**:
- Pick a date range
- Toggle between **By Category** (default) or **By Month**
- See revenue, COGS, gross profit, margin %, qty sold per grouping
- CSV export available

### Sold Stock (v8.7+)
Go to **Reports → Sold Stock**:
- Pick a date range
- Default view is **By Category** (AI-extracted item names are too noisy for the primary view)
- Switch to **By Item** for a drill-down with case-insensitive grouping
- See qty_sold, revenue, COGS, gross_profit, margin_pct, avg_selling_price
- CSV export available

### Billwise Report (v8.7+)
Go to **Reports → Billwise**:
- Lightweight list of bills with precomputed aggregates (item_count, category_count, total_cost, total_revenue, total_profit)
- Click a bill row to lazy-load its items inline (cached client-side)
- CSV export includes the full item detail (legacy behavior preserved)

### Cash Buckets
Go to **Reports → Cash Buckets**:
- **Cash Waterfall**: Sales → Stock Replacement → Operating Expenses → Business Reserve → Available for Withdrawal
- **Stock Reserve**: Days of cover + recommendation
- **Withdraw button**: Opens the safe-withdrawal modal (see AI Auditor section)

### AI Auditor
Go to **Reports → AI Auditor**:
- Click **Run Audit** to check your business health
- 8 checks across 5 domains:
  - Earnings formula integrity
  - COGS bridge integrity
  - Over-withdrawal detection
  - Restock funding adequacy
  - Stock reserve days of cover
  - Negative stock
  - Refund anomaly
  - Unconfirmed bills
- Findings are ranked: Critical (red) → Warning (amber) → Info (blue)
- Click **Acknowledge** on a finding to dismiss it

### Data Reconciliation (v8.11+)
Go to **Settings → Data Reconciliation** (admin only):
- **Discrepancy Report** — runs a full integrity scan and lists any mismatches between sales, stock_state, cash_drawer, and bill_items
- **Repair Tool** — fixes the detected discrepancies in a single atomic transaction

---

## AI Auditor & Safe Withdrawal

### Safe Withdrawal Formula
```
Safe Withdrawal = Cash − Stock Replacement − Operating Expenses − Business Reserve
```

### Why is "Available for Withdrawal" negative? (The Day-1 Trap)

If you just set up BillBook and uploaded your existing supplier bills to get your stock into the system, the "Available for Withdrawal" number will likely show as **negative** — even though you have cash in the drawer. Here's why:

When you confirm a supplier bill, BillBook writes a row to `cash_drawer` with `type='purchase'` and a **negative** amount (cash went OUT to buy the stock). But on Day 1, you never recorded the matching **positive** cash-in — the money you originally took out of your savings / pocket to buy that stock before BillBook existed.

So the database sees: "Cash went out (purchases) but never came in" → cash_drawer sum is negative → Available for Withdrawal is negative.

**The fix: Record a Capital Injection.** This credits cash_drawer by +amount, representing the capital you invested before BillBook was tracking. It's equity, NOT revenue — your profit numbers stay accurate, but the withdrawal number becomes positive.

#### How to Record a Capital Injection
1. Go to **Billing → Cash Buckets**
2. Look for the yellow warning card under "Available for Withdrawal" — it tells you to record a capital injection.
3. Click the **Capital Injection** button (next to the Withdraw button, top-right of the page).
4. The modal will show your current negative withdrawal number and suggest the minimum amount to record (e.g. "Record at least Rs 173,000 to bring it back to zero").
5. Enter:
   - **Amount**: The capital you invested (best estimate is fine — e.g. Rs 200,000)
   - **Source**: Pick the closest match:
     - **Owner's Pocket (personal savings)** — money from your own savings
     - **Partner Contribution** — capital from a co-owner
     - **Bank Loan** — loan injected into the business
     - **Opening Balance (one-time fix for Day 1)** — use this if you're just fixing the Day-1 trap and aren't sure which category fits
   - **Payment Method**: Cash or Bank Transfer (both credit the cash drawer the same way)
   - **Date**: Defaults to today. If you want to backdate the entry to match when you actually invested the money, change this.
   - **Notes**: Optional — e.g. "Initial investment to set up the shop"
   - **Manager PIN**: Admin PIN required for audit trail
6. Click **Record Injection**.
7. The page reloads and you'll see:
   - A new green "Owner-invested capital (all-time): +Rs X" card under the cash waterfall
   - The Available for Withdrawal number is now positive (or at least much less negative)
   - A new "Recent Capital Injections" table below the withdrawals table showing your entry

> **Tip**: You can record multiple injections over time. If you invest more of your own money later (e.g. to buy more stock), just record another injection with the appropriate amount and source. The system tracks each one separately for full audit.

### Withdrawing Cash
1. Go to **Reports → Cash Buckets**
2. Check the **verdict banner** at the top:
   - **Green**: "Safe to withdraw Rs X this month"
   - **Red**: "Over-withdrawn by Rs Y"
3. Click **Withdraw**
4. Enter the amount
5. **Live feedback** shows as you type:
   - **Green**: "Within safe limit (Rs X available)" — proceed without PIN
   - **Red**: "Exceeds safe limit by Rs X. Manager PIN required."
6. If over-safe, enter your **manager PIN** to proceed
7. The over-withdrawal is logged and appears in the next audit run

> **You are never blocked from your own money.** The PIN gate creates a deliberate pause, but you can always proceed with a PIN. The system just wants you to be aware of the risk.

### Month-End Audit
When you run the monthly close (Reports → Monthly Close), the auditor runs automatically with `trigger='month_end'`. The results are available in the Audit Report page.

---

## Expenses

### Quick Expense (FAB)
1. Click the **red "+" button** at the bottom-right of any page
2. Enter the amount
3. Select a category (Rent, Salaries, Electricity, etc.)
4. Click **Save Expense**
5. The expense is recorded instantly

### Full Expense Management
Go to **Reports → Expenses**:
- Add expenses with full details (amount, category, description, payment method, date)
- Set monthly budgets per category
- View budget bars (green/amber/red)
- See expense summary by category

---

## Customers & Credit

### Customer List
Go to **Customers → All Customers**:
- Search by name or phone
- See total credit outstanding per customer
- Click a customer to see their history, loyalty points, and RFM score

### Customer Soft-Delete (v8.11+)
If you delete a customer from the list (admin only), the customer is **soft-deleted** — they disappear from the list, KPI tiles, and credit outstanding report, but their historical sales remain intact for audit. A credit-limit protection prevents deleting a customer who still owes you money (you must clear their balance first).

### Credit Outstanding
Go to **Customers → Credit Outstanding**:
- See all customers with outstanding credit
- Total credit amount
- WhatsApp reminder links (pre-filled wa.me links)
- AR aging buckets (0–30 / 31–60 / 61–90 / 90+ days)

### Customer RFM Analysis
Go to **Customers → RFM Analysis**:
- Recency, Frequency, Monetary scores per customer (quintile 1–5)
- Identifies your best customers (555) and at-risk ones (low recency)
- Use this for targeted promotions

### Birthday Reminders
Customers with a birthday in the current month appear in a sidebar reminder card on the dashboard — use this for personalized outreach.

---

## Suppliers & Payables

### Supplier List
Go to **Suppliers → All Suppliers**:
- Search by name, phone, or address
- See total spent and outstanding per supplier
- Click a supplier to see their full purchase history, statements, and rate cards

### Supplier Soft-Delete (v8.11+)
Deleted suppliers disappear from the list, KPI tiles ("Top suppliers by spend"), and the AP aging report, but their bills remain intact for audit. The system will warn you if the supplier still has unpaid credit bills.

### Supplier Statements
- Each supplier has a running statement showing all confirmed bills + payments
- AP aging buckets (0–30 / 31–60 / 61–90 / 90+ days)
- Exportable to Excel

### Supplier Rates
- Pre-configure per-supplier price rates for each category
- When you confirm a new bill, BillBook checks each item against the saved rate and flags any price changes (increase or decrease) for your review

---

## Multi-Branch Setup

> Only needed if you have 2+ physical locations. Single-shop users can skip this.

### Quick Setup
1. **HQ machine**: Settings → Branch → Role = Headquarters
2. **Generate code**: AI Insights → HQ Branches → Generate Code
3. **Branch machine**: Settings → Branch → Register with Code → enter the 6-digit code
4. **View dashboard**: AI Insights → Owner Hub (on HQ) — consolidated P&L across all branches

### Stock Transfers
1. **Sender**: Inventory → Transfer Out → select destination + items → Create Challan
2. **Receiver**: Inventory → Transfer In → Accept → stock increases at the locked unit cost

### Full Guide
See **[MULTI_STORE_GUIDE.md](MULTI_STORE_GUIDE.md)** for the complete multi-branch architecture, sync protocol, and step-by-step setup.

---

## Remote Access

### One-Click Tunnel
1. Go to **Settings → Remote Access**
2. Toggle **Enable**
3. BillBook starts a Cloudflare tunnel and shows the HTTPS URL
4. Copy the URL and open it on your phone
5. Toggle **Disable** to stop

> Quick tunnel URLs change on restart. For a permanent URL, set up a named Cloudflare tunnel (free account).

---

## Backup & Maintenance

### Automatic Backups
- BillBook creates a **daily backup** automatically (stored in `data/backups/`)
- Keeps the last 10 backups (oldest are auto-deleted)
- Check backup status: Settings → Backups

### Manual Backup
Go to **Settings → Backups → Backup Now**

### Diagnose (Health Check)
Go to **Help → Diagnose**:
- Database integrity check
- Free disk space
- AI provider status
- Tunnel status
- Last backup age
- Negative stock categories
Each check shows green/amber/red.

You can also hit the unauthenticated endpoints `/api/health` and `/api/version` from any monitoring tool to keep an eye on BillBook from outside (v8.6+).

### POS Backup Import
See [Importing from Ezi POS](#importing-from-ezi-pos) above.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Forgot password | Delete `data/billbook.db` and re-run (loses all data — restore from backup first) |
| Database locked | Stop all instances; delete `data/billbook.db-wal` and `data/billbook.db-shm` |
| AI says "disabled" | Go to AI Usage page → Enable AI (kill switch is OFF) |
| Can't see reports | Make sure you're logged in as Manager (not Cashier) |
| POS backup import fails | Ensure the zip contains ACCTRANS.DBF; run `pip install dbfread` |
| Over-withdrawal warning | You've withdrawn more than the safe limit. Check Cash Buckets for details. |
| Bill upload not working | Ensure the file is a PDF or image (JPG/PNG); max 50MB |
| Profit ticker not showing | Wait 2-3 seconds after navigating — it mounts after the shell renders |
| Negative stock | Go to Inventory → Stock Overview → click "Rebuild Stock State" |
| Deleted supplier still showing in tiles | Run **Settings → Data Reconciliation → Repair** (v8.11+); also try reloading the page |
| POS looks too dark | The dark navy POS theme (v8.12) is by design for cashier visibility. If you prefer light mode, ask your admin to toggle the POS theme setting |
| POS Import sync "deleted sale" threshold | If you see a warning that the deletion count exceeds the 5% threshold, check your Ezi POS for accidental deletions before confirming. You can also adjust the threshold in Settings. |
| Refund failed mid-process | The refund is atomic (v8.6+) — no partial state was written. Just retry. If it keeps failing, check the activity log for the specific error. |
| `bills.version` mismatch on confirm | Another user edited the same bill simultaneously. Refresh the bill edit page and re-confirm. |

---

## Keyboard Shortcuts (Global)

| Key | Action |
|-----|--------|
| D | Dashboard |
| P | POS |
| B | Bills |
| F | Items |
| S | Suppliers |
| R | Reports |
| I | AI Insights |
| C | Customers |
| , | Settings |
| N | New Bill |
| H | Launcher |
| Ctrl+K | Command Palette |

---

## Need More Help?

- **In-app help**: Click the **?** button at the bottom-right of any page
- **AI Assistant**: Go to AI Insights → AI Assistant — ask any business question. The assistant knows about your stock, sales, customers, suppliers, and cash position. It uses real-time tool calls — it never guesses numbers.
- **Diagnose**: Help → Diagnose — runs health checks
- **Multi-store guide**: See MULTI_STORE_GUIDE.md
- **Installation guide**: See INSTALL_GUIDE.md
- **Health endpoint**: `GET /api/health` (no login required) — returns DB status, stock_state freshness, disk free, WAL size
- **Version endpoint**: `GET /api/version` (no login required) — returns app version, Python version, git commit, build date
