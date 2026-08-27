# BillBook Multi-Branch Setup & Sync Architecture

> **Version:** v8.0+ | **Audience:** Shop owners with 2+ physical locations | **Prerequisite:** BillBook v8.0 installed on each machine

This guide explains how BillBook's multi-branch system works, how to set it up, and how to verify it's running correctly. If you only have one shop, you can skip this guide entirely — single-shop mode is the default and requires no configuration.

---

## The Big Picture

BillBook v8.1 uses a **Hub-and-Spoke** model. Every branch is an independent BillBook instance that can sell offline. The HQ is the aggregator that collects summaries and distributes stock/prices.

```
                    ┌─────────────────────┐
                    │       HQ / Hub      │
                    │  (Owner Dashboard)  │
                    │                     │
                    │  • branches table   │
                    │  • branch_summaries │
                    │  • Owner Hub page   │
                    │  • Price Push       │
                    │  • Central Purchase │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         ┌────▼────┐     ┌────▼────┐     ┌────▼────┐
         │ Branch A│     │ Branch B│     │ Branch C│
         │ Lahore  │     │ Karachi │     │ Faisal  │
         │         │     │         │     │         │
         │ Own DB  │     │ Own DB  │     │ Own DB  │
         │ Own POS │     │ Own POS │     │ Own POS │
         └─────────┘     └─────────┘     └─────────┘
```

**Key principle:** Branches never depend on each other or on HQ to make a sale. If the internet drops in Lahore, Branch A keeps selling normally. Sync is **eventual** — it catches up when connectivity returns.

### When you need this

| Scenario | Multi-branch? |
|---|---|
| One shop, one machine | **No** — single-shop mode (the default) |
| One shop + owner's laptop for reports | **No** — use Cloudflare Tunnel to access the single instance remotely |
| Two shops in different cities | **Yes** — set up HQ on the owner's machine + a branch on each shop machine |
| One warehouse + multiple retail fronts | **Yes** — HQ acts as the Central Warehouse, each retail front is a branch |

---

## How Sync Works (The 5 Mechanisms)

### 1. Branch → HQ: Daily Summary Push

Every branch pushes a summary of its day to HQ. This is how the owner sees consolidated numbers.

**What gets pushed:**
```json
{
  "branch_id": "BR-LAHORE",
  "summary_date": "2026-08-12",
  "sales": 45000,
  "cogs": 31500,
  "gross_profit": 13500,
  "expenses": 4500,
  "cash_in_drawer": 28000,
  "stock_snapshot": {
    "1": {"qty": 340, "value": 61200, "avg_cost": 180},
    "2": {"qty": 120, "value": 22200, "avg_cost": 185}
  }
}
```

**How it's sent:**
- Branch calls `POST /api/sync/branch-summary` on HQ
- Authenticated via `Authorization: Bearer <branch_token>`
- The token was issued during branch registration (6-digit pairing code)

**When it's sent:**
- Automatically on shift close
- Once daily (background job)
- Manually via the outbox flush endpoint

**Idempotency:** The HQ stores summaries keyed by `(branch_id, summary_date)`. If the same branch pushes the same date twice, the second push updates the existing row — no duplicates.

---

### 2. HQ → Branch: Stock Transfer (Challan)

When Branch A sends stock to Branch B, the transfer flows through a **challan** (delivery note).

**The flow:**
```
Branch A (Lahore)                    HQ                         Branch B (Karachi)
      │                               │                               │
      │  1. Create Transfer Out       │                               │
      │  (select Branch B, qty, cat)  │                               │
      │  (stock reduced immediately   │                               │
      │   at Branch A's avg cost)     │                               │
      │                               │                               │
      │                               │  2. Branch B sees             │
      │                               │  "Incoming Transfer"          │
      │                               │  in its Transfer In page      │
      │                               │                               │
      │                               │                               │  3. Branch B clicks
      │                               │                               │  "Accept" — stock
      │                               │                               │  increases at the
      │                               │                               │  locked unit_cost
```

> **Note:** In v8.0, the challan lives in the sender's local DB. The receiver sees it in their Transfer In page after the challan is synced (via the outbox) or entered manually. In a future release, HQ will route challans automatically; for now, the receiver accepts challans that appear in their local `transfer_challans` table.

**The critical accounting rule (the load-bearing 185.88 test):**
- **Transfer OUT (Branch A):** `apply_transfer_out_to_state` reduces qty and stock-value at Branch A's current average cost. **No COGS, no revenue** — it's an inventory movement, not a sale. The average cost per piece is UNCHANGED.
- **Transfer IN (Branch B):** Applies as a **purchase at the transferred unit_cost** (captured from Branch A's running average at the moment of transfer). This calls the existing `apply_purchase_to_state`, so Branch B's moving average updates correctly.
- The challan carries `unit_cost` per line explicitly. Never recomputed on the receiving side.

**Why this matters:** Branch A has 17,000 pcs @ Rs 185.88 and transfers 100 pcs to Branch B:
- Branch A still @ 185.88 (unchanged)
- Branch B now @ 185.88 (received at captured cost)
- Total stock = 17,000 (16,900 + 100)
- No phantom COGS or revenue on either side

**Idempotency:** Each challan has a unique `challan_no`. Accepting an already-accepted challan returns success without re-applying.

---

### 3. HQ → Branch: Global Price Push

The owner can update a price category at HQ and push it to all branches.

**The flow:**
```
HQ: Change Cat B price from Rs 500 → Rs 550
    │
    ├─> Push to Branch A (price_push_id = "PP-20260812-001")
    ├─> Push to Branch B (price_push_id = "PP-20260812-001")
    └─> Push to Branch C (price_push_id = "PP-20260812-001")

Each branch:
    1. Receives price push (POST /api/sync/price-push, Bearer auth)
    2. Checks if price_push_id already applied (idempotency)
    3. Updates local price_categories table
    4. Logs to activity_log with source='hq'
```

**Idempotency:** The `price_push_id` is unique. If the same push is delivered twice, the second delivery returns `already_applied` — the local price is not changed again.

---

### 4. HQ → Branch: Central Purchase Distribution

The owner buys in bulk at HQ and distributes to branches. HQ acts as a virtual "Central Warehouse" branch (`branch_id='BR-CENTRAL'`).

**The flow:**
```
HQ: Record bulk bill (10,000 pcs @ Rs 175)
    │  (stock added to HQ's local state at the central bulk-buy price)
    │
    ├─> Distribute 4,000 to Branch A → creates transfer challan from BR-CENTRAL
    ├─> Distribute 6,000 to Branch B → creates transfer challan from BR-CENTRAL
    └─> Track distribution status per central purchase line item
        (distributed_qty + remaining_qty per category)

Branches receive the challans and accept them,
updating their stock at the central unit cost (Rs 175).
```

**Key detail:** The challan uses the **central unit_cost** (Rs 175), not HQ's current average cost. This ensures branches receive stock at the bulk-buy price, regardless of what other stock HQ may have mixed in.

---

### 5. Sync Failure Retry (The Outbox Pattern)

This is the **most important** mechanism. It ensures sync never blocks a sale and never loses data.

**How it works:**

```
┌─────────────────────────────────────────────────────────────┐
│                    sync_outbox table                         │
├──────────┬──────────────┬─────────────┬──────────┬─────────┤
│ id       │ dest_branch  │ entity_type │ payload  │ status  │
│          │              │             │          │ attempts│
├──────────┼──────────────┼─────────────┼──────────┼─────────┤
│ 1        │ HQ           │ branch_     │ {...}    │ pending │
│          │              │ summary     │          │ 1       │
│ 2        │ BR-KARACHI   │ challan     │ {...}    │ pending │
│          │              │             │          │ 0       │
│ 3        │ BR-LAHORE    │ price_push  │ {...}    │ sent    │
│          │              │             │          │ 2       │
└──────────┴──────────────┴─────────────┴──────────┴─────────┘
```

**The retry flow:**
1. **Queue:** When a sync action is needed (summary push, challan delivery, price push), it's written to the local `sync_outbox` table with `status='pending'`.
2. **HQ unreachable:** If the destination is down, `flush_sync_outbox()` fails. The entry stays `pending`, `attempts` is incremented, `last_attempt_at` is set.
3. **HQ comes back:** On the next flush attempt (triggered by shift close, daily job, or manual flush), the entry is delivered successfully. Status changes to `sent`.
4. **Idempotency:** If the same entity is re-queued (same `entity_key`), it returns the existing entry — no duplicates.

**The definitive test (verified in v8.0.1):**
```
1. Queue a summary push → sync_outbox (pending, attempts=0)
2. HQ unreachable (dead port) → flush fails → entry stays pending, attempts=1
3. HQ comes back (live port) → flush succeeds → entry marked 'sent'
4. HQ actually received it → Owner Hub shows the synced summary
5. Re-queue with same key → returns same entry_id (idempotent)
```

This proves the "never blocks a sale" claim: if HQ is unreachable, the branch keeps selling normally. When HQ comes back, the next flush delivers the accumulated entries.

**Manual flush:** `POST /api/sync/outbox/flush` with `{"dest_url": "https://hq.yourdomain.com", "bearer_token": "..."}` triggers a flush. `GET /api/sync/outbox` shows pending/sent/failed counts.

---

## How to Set Up Multi-Branch

### Step 1: Set Up the HQ (Central Hub)

On the **HQ machine** (the owner's PC that will act as the aggregation hub):

**v8.1:** If this is a fresh install, the First-Launch Wizard will guide you through password setup, business type, and categories automatically. After the wizard, continue here.

```bash
# Install BillBook v8.1
pip install -r requirements.txt

# In .env, set a strong password
APP_PASSWORD=warehouse-secret-123

# Start the server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Configure HQ:
1. Open `http://localhost:8000` in your browser, log in.
2. Go to **Settings → Branch**
3. Set:
   - **Branch Name**: "Central Warehouse" (or your business name)
   - **Role**: `Headquarters (aggregation hub)` ← this is critical
   - **Region**: your city
4. Click **Save Branch Settings**. A `branch_id` is auto-generated (e.g., `BR-A1B2C3D4`).
5. Set up a Cloudflare Tunnel (see INSTALL_GUIDE.md → "Remote Access") so branches can reach this HQ over the internet. Note the tunnel URL (e.g., `https://billbook-hq.yourdomain.com`).

### Step 2: Generate a Registration Code

On the **HQ machine**:
1. Go to **AI Insights → HQ Branches**
2. Click **Generate Code**
3. A 6-digit code appears (valid for 5 minutes, single-use)
4. Share this code with the branch owner (phone, WhatsApp, etc.)

**v8.1 QR option:** Click **"Add Branch via QR"** instead — a QR code appears encoding the HQ URL + registration code. The branch can scan it with their camera for one-tap registration (no manual URL/code entry).

### Step 3: Set Up Each Branch

On **each branch machine** (Lahore, Karachi, etc.):

**v8.1:** If this is a fresh install, the First-Launch Wizard will appear. Complete it, then continue here.

```bash
# Install BillBook v8.1
pip install -r requirements.txt

# In .env, set the branch password
APP_PASSWORD=lahore-branch-123

# Start the server
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Configure the branch:
1. Open `http://localhost:8000` in your browser, log in.
2. Go to **Settings → Branch**
3. Set:
   - **Branch Name**: "Lahore Shop"
   - **Role**: `Branch (independent shop)` (the default)
   - **Region**: "Punjab"
4. Click **Save Branch Settings**. A `branch_id` is auto-generated.
5. Click **Register with Code**
6. Enter:
   - **Hub URL**: `https://billbook-hq.yourdomain.com` (HQ's Cloudflare Tunnel URL)
   - **Registration Code**: the 6-digit code from Step 2
   - **Your Branch's Tunnel URL**: `https://billbook-lahore.yourdomain.com` (the branch's own Cloudflare Tunnel URL, so HQ can push to it)
7. Click **Register**. The branch calls HQ, validates the code, and receives a sync token.
8. The token is stored locally (SHA-256 hashed) in `branch_config.sync_token_hash`.

**Repeat Steps 2-3 for each branch.** Generate a new code for each — codes are single-use.

### Step 4: Verify Sync

On the **HQ**:
1. Go to **AI Insights → HQ Branches** → should see all registered branches with `active` status + a "last seen" timestamp.
2. Go to **AI Insights → Owner Hub** → should show:
   - Consolidated P&L (all branches summed)
   - Branch leaderboard (sorted by sales)
   - Per-branch stock snapshots
   - Stale badge for branches that haven't synced in 24h

On each **branch**:
1. Make a test sale.
2. Wait for the next sync (shift close, daily job, or manual flush).
3. Check HQ's Owner Hub → should show the sale in consolidated totals.

---

## The Sync Data Flow (Complete Picture)

```
BRANCH (Lahore)                          HQ                              BRANCH (Karachi)
     │                                    │                                    │
     │  1. Make sales all day             │                                    │
     │  (offline, local DB)               │                                    │
     │                                    │                                    │
     │  2. Shift close → push summary     │                                    │
     │───────────────────────────────────>│                                    │
     │                                    │  3. Store in branch_summaries      │
     │                                    │                                    │
     │  4. Create transfer to Karachi     │                                    │
     │  (Transfer Out page)               │                                    │
     │  (stock reduced at avg cost)       │                                    │
     │                                    │                                    │
     │                                    │                                    │  5. Karachi sees
     │                                    │                                    │  challan in Transfer
     │                                    │                                    │  In page, clicks Accept
     │                                    │                                    │  (stock increased at
     │                                    │                                    │  locked unit_cost)
     │                                    │                                    │
     │  6. Owner pushes price update      │                                    │
     │<───────────────────────────────────│                                    │
     │  7. Update local prices            │<───────────────────────────────────│
     │  (activity log: source='hq')       │  (Karachi also gets the push)       │
     │                                    │                                    │
     │  8. If HQ was down, outbox retries │                                    │
     │  (never blocks sales)              │                                    │
```

---

## Inter-Branch Stock Transfer — Step by Step

This is the load-bearing operation. Follow these steps exactly.

### On the SENDER (Branch A):

1. Go to **Inventory → Transfer Out**
2. Select the destination branch from the dropdown
3. Click **Add Line** for each category you want to transfer:
   - Select the category (shows current stock + avg cost)
   - Enter the qty
   - The **unit cost** is auto-filled from your current running weighted average — it's locked into the challan
4. Click **Create Transfer Challan**
5. Your stock reduces immediately. Your average cost is **unchanged**. No COGS or revenue is recorded.

### On the RECEIVER (Branch B):

1. Go to **Inventory → Transfer In**
2. The challan appears in the list with status `In Transit`
3. Click **Accept**:
   - Your stock increases by the qty
   - Your moving average updates at the locked unit_cost
   - The challan status changes to `Accepted`
4. OR click **Reject** (no state change on your side; the sender's stock was already reduced)

### Verify the 185.88 integrity

After a transfer, check both branches' stock state:
- **Sender:** avg cost unchanged, qty reduced by the transferred amount
- **Receiver:** avg cost = the locked unit_cost, qty increased by the transferred amount
- **Total stock across both:** unchanged (conservation of inventory)

---

## Central Purchasing — Step by Step

Use this when you buy in bulk at HQ and want to distribute to branches at the bulk-buy price.

### On HQ:

1. Go to **Inventory → Central Buys**
2. Click **New Central Buy**
3. Enter the supplier name + line items (category, qty, unit cost)
4. Click **Create**. Stock is added to HQ's local state at the central bulk-buy price.
5. Click **Distribute** on the central purchase
6. Select the destination branch + quantities per line
7. Click **Distribute**. A transfer challan is created from `BR-CENTRAL` to that branch at the central unit cost.
8. The central purchase status changes from `recorded` → `partial` → `distributed` as lines are fully distributed.

### On the receiving branch:

1. Go to **Inventory → Transfer In**
2. The challan from `BR-CENTRAL` appears
3. Click **Accept** — stock increases at the central bulk-buy price

---

## Global Price Push — Step by Step

Use this to update a price category across all branches simultaneously.

### On HQ:

1. Go to **AI Insights → Price Push**
2. Click **New Price Push**
3. Select a category + enter the new sell price
4. Click **Push to All Branches**
5. HQ creates a `price_push_id` + shows the list of branches to deliver to
6. Each branch receives the push (via its tunnel URL) + applies it idempotently

### On each branch:

- The local `price_categories.sell_price` updates
- The activity log records `price_push_applied` with `source='hq'` in the metadata
- Re-delivery of the same `price_push_id` returns `already_applied` — no double-application

---

## Key Guarantees

| Guarantee | How It's Enforced |
|---|---|
| **Branches sell offline** | Each branch has its own SQLite DB. No dependency on HQ. |
| **Sync never blocks a sale** | Outbox pattern. Sync happens in background. |
| **No duplicate data** | Idempotency keys on every synced entity (`summary_date+branch_id`, `challan_no`, `price_push_id`). |
| **No lost data** | Outbox retries until delivered. |
| **Stock integrity across branches** | Transfer OUT reduces at avg cost (no COGS). Transfer IN applies as purchase at transferred cost. The 185.88 accounting identity is preserved. |
| **Price consistency** | Global price push with idempotent delivery. |
| **Consolidated visibility** | Daily summary push to HQ. Owner Hub aggregates. |
| **Branch purity** | A branch's local DB is NOT polluted with other branches' transactional data. Cross-branch identity is applied at sync time, not stored on every local row. |
| **Single-shop mode** | With `role='branch'` + empty `hub_url` (the defaults), the app behaves exactly as v7.2. All multi-branch tables exist but are empty. |

---

> **Note:** Some UI elements are conditional — they only appear when the right data exists. The "Add Line" button on Transfer Out only shows when ≥1 other branch is registered. The "Branch Leaderboard" on Owner Hub only renders when branches have synced. The "Distribute" button on Central Buys only appears on purchases that aren't fully distributed. If you don't see a button referenced in this guide, complete the earlier setup steps first.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| **Branch won't register** | The 6-digit code expires after 5 minutes and is single-use. Generate a new code on HQ (AI Insights → HQ Branches → Generate Code). |
| **Branch can't reach HQ** | Verify HQ's Cloudflare Tunnel URL is correct + HQ is running. Test by opening the URL in a browser — you should see the BillBook login page. |
| **Owner Hub shows stale branch** | The branch hasn't synced in 24h. Check the branch's internet + that it has a valid sync token (Settings → Branch → "has_sync_token"). |
| **Transfer challan stuck "in transit"** | The receiver must accept/reject it. On the receiver, go to Inventory → Transfer In. If the receiver is offline, the challan waits. |
| **Price push didn't apply** | Check the branch's activity log for `price_push_applied` with `source='hq'`. If missing, the branch's tunnel URL may be wrong — verify on HQ Branches page. |
| **Sync outbox growing** | HQ unreachable. Check `GET /api/sync/outbox` for pending count. Once HQ is back, run `POST /api/sync/outbox/flush` with `{dest_url, bearer_token}` or wait for the next auto-flush. |
| **Branch DB has other branches' data** | This should never happen — branch purity is enforced. If it does, the branch's DB was manually edited. Restore from backup. |
| **Registration code says "invalid"** | The code was already used (single-use), expired (5-min limit), or HQ was restarted (codes are in-memory). Generate a new one. |

---

## Testing the Full Flow

To verify your multi-branch setup is working end-to-end, run through this checklist:

1. **Registration:** HQ generates a code → branch registers → branch appears in HQ Branches list with `active` status
2. **Summary sync:** Branch makes a sale → branch pushes summary → HQ Owner Hub shows the sale in consolidated totals
3. **Transfer:** Branch A creates a transfer to Branch B → Branch A's stock reduces (avg unchanged) → Branch B accepts → Branch B's stock increases at the locked unit_cost
4. **Price push:** HQ pushes a price change → branch's local price updates → activity log shows `source='hq'`
5. **Sync retry:** Stop HQ → branch queues a summary → start HQ → trigger flush → HQ receives the summary

For automated testing, the repository includes:
- `scripts/v8_p7_multi_branch_sim.py` — boots two isolated SQLite DBs + runs the full flow via real HTTP (28 checks)
- `tests/test_v8_01_sync_retry.py` — tests the outbox failure-retry mechanism (3 checks)
- `scripts/v8_p7_browser_e2e.py` — loads all 24 pages (7 new v8.0 + 17 v7.2) with zero console errors

---

## What's Next

- **v8.1 (deferred):** Real-time WebSocket sync (instead of eventual), branch-level RBAC nuances beyond cashier/manager, cross-branch customer loyalty merging, consolidated FBR export across branches, multi-currency, automated branch failover.
- **Before v9.0:** Split `db.py` (currently 1,036 lines) into `schema.py` + `migrations.py` + `conn.py`.

For single-shop setup, desktop/mobile installation, AI features, and general troubleshooting, see **INSTALL_GUIDE.md** and **README.md**.
