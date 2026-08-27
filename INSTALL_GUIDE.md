# BillBook v8.1 — "Everywhere" Installation Guide

BillBook v8.1 runs on **Windows**, **macOS**, **Android**, and **iOS** — all using free tools, no paid developer accounts required. v8.0 adds **multi-branch support**. v8.1 adds **first-launch wizard, QR pairing, one-click remote access, auto-maintenance, and daily-use friction fixes**.

---

## Quick Start (Desktop — Windows)

### Option A: Pre-built Installer (Recommended)
1. Download `BillBookSetup-v8.13.5.exe` (built by your vendor using the included `build\build_windows.ps1` script).
2. Double-click to install. Windows SmartScreen may warn "Unrecognized app" — click **"More info"** → **"Run anyway"**. This is normal for unsigned indie builds. (Vendors with a code-signing cert can sign the installer — see §"Building from Source" below.)
3. Launch BillBook from the Start Menu or desktop shortcut. The app starts the backend automatically and opens the dashboard.
4. Set your password on first launch. The app creates its data folder at `C:\Program Files\BillBook\data\` (or `%LOCALAPPDATA%\BillBook\data\` if you chose a per-user install during setup).

**Code-protection note:** The installer ships only a compiled `billbook.exe` (PyInstaller --onefile) plus runtime DLLs. No Python source files (`.py`) are written to your disk, no `.env` file is leaked, and no git history is bundled. A casual user cannot read the application logic. For commercial-grade protection (PyArmor encryption + per-machine license keys + Nuitka C compilation), your vendor may also apply the layered strategies documented in `build\windows\STRONGER_PROTECTION.md`.

### Option B: From Source (Developer)
```bat
mkdir C:\billbook
cd C:\billbook
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Edit `.env` and set `APP_PASSWORD` (minimum 8 characters; `change-me-now` is rejected on first launch). Then:
```bat
venv\Scripts\activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open http://localhost:8000 in your browser.

---

## Building the Windows Installer (For Vendors)

If you are the vendor distributing BillBook to clients, build a single `BillBookSetup-v8.13.5.exe` that:
- Contains NO Python source code (compiled `.pyc` only)
- Contains NO `.env`, NO `.git` history, NO test fixtures
- Is one self-contained `.exe` your clients double-click to install
- Optionally signed with your Authenticode cert

### Prerequisites (Windows 10/11 machine)

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3.11+ | Compiles the app to bytecode | https://www.python.org/downloads/windows/ |
| PyInstaller 6+ | Bundles bytecode + runtime into one .exe | `pip install pyinstaller` |
| Inno Setup 6+ | Builds the polished installer UI | https://jrsoftware.org/isdl.php |
| Tauri CLI (optional) | Builds the native desktop shell (.msi) | `cargo install tauri-cli` OR `npm i -g @tauri-apps/cli` |
| signtool.exe (optional) | Signs the .exe + installer with your cert | Part of Windows SDK |

### Build steps

```powershell
# 1. Open PowerShell in the BILL_MANAGEMENT_SOFTWARE\ folder
cd C:\path\to\BILL_MANAGEMENT_SOFTWARE

# 2. (One-time) Generate Tauri updater keys
powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
# Paste the printed public key into desktop\tauri.conf.json (updater.pubkey).
# Keep desktop\.tauri\updater-private.key secret — you'll need it to sign updates.

# 3. Build everything (.exe + installer + optional .msi)
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1

# Or skip Tauri for a faster first build:
powershell -ExecutionPolicy Bypass -File build\build_windows.ps1 -SkipTauri
```

### Build outputs

```
build\dist\billbook\billbook.exe         (portable binary — runs anywhere, no install)
installer\BillBookSetup-v8.13.5.exe      (the file you GIVE to clients)
desktop\src-tauri\target\release\bundle\msi\BillBook_8.13.5_x64.msi  (Tauri shell)
```

### Code-signing (optional, recommended)

To stop Windows SmartScreen from showing the "Unrecognized publisher" warning on first install, sign the `.exe` and installer with an Authenticode code-signing certificate:

1. Buy a cert from DigiCert / Sectigo / SSL.com (~$200/yr OV; ~$300/yr EV with hardware token).
2. Export to `.pfx` with a password.
3. Set env vars before building:
   ```powershell
   $env:CODESIGN_PFX = "C:\path\to\cert.pfx"
   $env:CODESIGN_PASS = "the-pfx-password"
   ```
4. Run the build with `-SignCode`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1 -SignCode
   ```

### Code-protection guarantees verified by the build script

- ✅ No `app\*.py` files in the installer (PyInstaller bundles `.pyc`, not `.py`)
- ✅ No `.env` or `.env.example` shipped (env vars must be set on the OS)
- ✅ No `.git\` history bundled (the `build\` folder is in `.gitignore`)
- ✅ No `tests\` fixtures (excluded in `billbook.spec:excluded_imports`)
- ✅ No Tauri private key leaked (excluded via `.gitignore` + Inno Setup file list)
- ✅ Single `.exe` (no extracted source folders visible to the end user after install)

### For stronger protection (commercial-grade)

Read `build\windows\STRONGER_PROTECTION.md` — three layered strategies:
- **Strategy A**: PyArmor encrypts bytecode + binds to per-machine license key
- **Strategy B**: Nuitka compiles Python to C (no `.pyc` to extract at all)
- **Strategy C**: Combine A + Inno Setup — issue per-customer `.lic` files from your server

---

## Quick Start (Desktop — macOS)

### Option A: Pre-built DMG
1. Download `BillBook_8.1.0_x64.dmg` from GitHub Releases.
2. Open the DMG and drag BillBook to Applications.
3. On first launch, macOS Gatekeeper will block it: right-click → **"Open"** → confirm **"Open"** in the dialog. This is required for unsigned apps.
4. The app runs the backend as a sidecar process and opens a webview window.

### Option B: From Source
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env, set APP_PASSWORD
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## Android App

### Install
1. Download `BillBook-8.1.0.apk` from GitHub Releases on your Android phone.
2. Allow "Install from unknown sources" for your browser (Settings → Security).
3. Tap the downloaded APK to install.

### Connect to Your Shop PC
1. Make sure BillBook is running on the shop PC (desktop app or `start.bat`).
2. On the shop PC, go to **Settings → General** and enable **LAN Mode**.
3. Note the shop PC's IP address (shown on the BillBook screen).
4. Open BillBook on your phone → enter the IP address (e.g., `192.168.1.100:8000`).
5. On the shop PC, go to **Settings → Devices → Generate Pairing Code** (choose role: cashier or manager).
6. Enter the 6-digit code on your phone → **Pair**.
7. Your phone now has access to the full POS with the assigned role.

### In-App Updates
When a new version is released, the app checks GitHub Releases and prompts you to download the new APK.

---

## iOS (PWA — No App Store Needed)

Apple's App Store requires a $99/year developer account. BillBook uses a **Progressive Web App (PWA)** instead — free, no App Store.

### Install on iPhone/iPad
1. Open Safari on your iPhone.
2. Navigate to your shop's BillBook URL (LAN IP or Cloudflare Tunnel URL).
3. Log in.
4. Tap the **Share** button → **"Add to Home Screen"**.
5. Name it "BillBook" → tap **Add**.
6. BillBook now appears on your home screen with its own icon. Tap to launch in fullscreen mode (no Safari chrome).

### Pairing
Same as Android — generate a pairing code on the shop PC, enter it on your iPhone.

---

## Remote Access (Cloudflare Tunnel — Free)

When you're away from the shop and want to check reports on your phone:

### Quick Tunnel (No Account — For One-Off Testing Only)
1. On the shop PC, run `scripts/remote-access.bat` (Windows) or `scripts/remote-access.sh` (Mac/Linux).
2. The script installs Cloudflare's free `cloudflared` tool and prints a public URL like `https://billbook-abc123.trycloudflare.com`.
3. Open this URL on your phone's browser → log in → access all features.
4. The tunnel is outbound-only — **no port-forwarding or router config needed**.
5. Press `Ctrl+C` on the shop PC to stop the tunnel.

> **⚠ Important:** Quick tunnel URLs are **ephemeral** — the URL changes every time you restart the tunnel. This is fine for one-off access (checking reports from home), but impractical for daily use. For a persistent URL that survives restarts, use the Named Tunnel below.

### Named Tunnel (Persistent URL — Free Account)
For a stable URL that survives restarts:
1. Create a free Cloudflare account at cloudflare.com.
2. `cloudflared tunnel login` → authenticate.
3. `cloudflared tunnel create billbook` → create the tunnel.
4. `cloudflared tunnel route dns billbook billbook.yourdomain.com` → map a subdomain.
5. `cloudflared tunnel run billbook` → start the tunnel.

---

## Auto-Update (Desktop)

BillBook checks for updates on launch and via the system tray menu ("Check for Updates").

### How It Works
1. Each GitHub Release includes a `latest.json` manifest with version + download URLs.
2. The Tauri shell fetches `latest.json` on launch.
3. If a newer version exists, it downloads the new bundle (NSIS installer on Windows, DMG on macOS).
4. The bundle is signed with an ed25519 key (generated locally, free).
5. Click "Install and Restart" → the update applies and the app restarts.

### Generating Update Keys (One-Time)
```bash
cargo tauri signer generate -w ~/.tauri/billbook.key
```
- **Private key**: store as GitHub Secret `TAURI_PRIVATE_KEY`.
- **Public key**: paste into `desktop/tauri.conf.json` → `plugins.updater.pubkey`.

---

## Security Model

### Shop PC (Desktop)
- Password-protected (bcrypt, 12 rounds).
- Binds to 127.0.0.1 by default (localhost only).
- LAN Mode: binds to 0.0.0.0 + mDNS advertising.
- Tunnel Mode: accepts Cloudflare Tunnel origin.

### Mobile Pairing
- 6-digit pairing code, 5-minute expiry, single-use.
- Role chosen at code generation (cashier or manager).
- Device token is SHA-256 hashed (not stored in plaintext).
- Manager can revoke any device at any time (Settings → Devices).

### RBAC
- **Cashier**: POS only (no settings, no financial reports, no device management).
- **Manager**: full access except changing the admin password.
- **Admin**: unrestricted.

---

## AI Features (v7.0+)

BillBook v8.1 ships with a full AI engine — agent loop, 18 READ tools, approval queue, kill switch, and automation toggles. **All automations are OFF by default** — you opt in to each one.

### Where to find them
- **AI Assistant** (`/insights/agent`) — Ask questions like "What is my actual overall margin?" or "How much cash can I safely withdraw?". The agent calls READ tools (margins, profit, cash buckets, inventory, etc.) — answers match your reports exactly. Tool trace is collapsible. **v8.0: the agent now answers multi-branch questions too** — try "How are all my branches doing?" or "Show me recent transfers" or "What's my consolidated sales?".
- **Approval Queue** (`/insights/approval-queue`) — THE BACKBONE. Every AI-drafted action lands here for your approval. Price changes require a manager PIN. Batched actions (e.g. season-prep) are grouped. Sidebar shows a pending-count badge.
- **AI Usage Dashboard** (`/insights/ai-usage`) — Monitor calls, tokens, cache hit rate, 14-day chart, recent failures. Clear cache button. Kill switch toggle.
- **AI Automations** (`/settings/ai-automations`) — Toggle 9 automations on/off (all OFF by default). Includes a "Prepare for Season" trigger that drafts a batch of POs + happy-hour rule + customer broadcast into the Approval Queue.

### v8.0: Multi-branch agent tools
The AI Assistant now has 3 new READ tools for multi-branch awareness:
- `get_owner_hub` — consolidated dashboard (sales, GP, cash across all branches + leaderboard + stale flags)
- `get_branches` — list of registered branches
- `get_transfers` — recent inter-branch transfer challans

Ask the assistant: "What's my consolidated sales across all branches?" or "Which branches haven't synced?" or "Show me recent transfers." The agent calls the right tool and answers with real numbers from the Owner Hub data.

### Kill Switch
The red **Disable AI** button on the AI Usage page (and on the AI Automations page) flips a global kill switch:
- **AI stops**: agent, ai_call, help assistant all blocked.
- **Heuristics continue**: trends, break-even, margin alerts, profit reports, internal signals.
- All AI surfaces show a degraded-state badge (orange "AI OFF" pill on the floating help button, banner on chat/usage/automations pages).
- The kill switch persists across restarts.

### Approval Queue guarantees
- WRITE tools **never** execute directly — always through `pending_actions` + your approval.
- Price changes additionally require a manager PIN (4–8 digits, stored as bcrypt hash).
- You can **Edit** any pending action's payload, reason, or impact summary before approving.
- You can **Reject** any action — it's discarded.
- **Approve All** / **Reject All** work per batch.

### Data safety
- AI calls go through `ai_router.ai_call()` which enforces cache → budget → logging.
- Cache TTLs: BI answers 15min, narratives 24h, trends 6h, extraction forever.
- Daily budget per provider (Groq 500 calls, Gemini 100 calls) — over-budget → stale cache fallback.
- All AI calls logged in `ai_usage` table (auditable).
- Clearing the cache logs an `ai_cache_cleared` activity entry.

### Optional: LLM integration
The agent ships with an offline-safe heuristic tool selector. To use a real LLM (Groq or Gemini):
1. Get a free Groq API key at console.groq.com.
2. Set `GROQ_API_KEY` in your `.env`.
3. The agent will call the LLM with tool definitions; on any error it falls back to the heuristic.

---

## v8.1 New Features

### First-Launch Wizard
Fresh install → a 4-step guided wizard appears (instead of raw login):
1. **Set Password** — with a live strength meter (weak/fair/strong)
2. **Business Type** — pick Wholesale (default), Retail, or Custom
3. **Confirm Categories** — pre-filled from the template, inline-editable
4. **Optional AI + Finish** — paste a Gemini key (skippable), choose start page (Launcher/Dashboard/POS)

Existing v8.0 installs are unaffected — the wizard only appears on fresh databases.

### QR-Code Pairing
- **Device pairing:** Go to Settings → Devices → "Show QR" → scan with your phone's camera → auto-pairs (no manual IP/code entry).
- **Branch registration:** Go to AI Insights → HQ Branches → "Add Branch via QR" → the branch scans it → auto-registers.
- The existing 6-digit code flow still works as a fallback.

### One-Click Remote Access
Settings → Remote Access → toggle **Enable** → a Cloudflare quick tunnel starts → shows the HTTPS URL with a copy button. Toggle **Disable** to stop. The state persists across restarts.

> Quick tunnel URLs change on restart. For a permanent URL, connect a free Cloudflare account (named tunnel).

### Auto-Maintenance
- **Auto-backup** (Zero-Config): a timestamped backup is created daily into `data/backups/`, retaining the last 10. No user action needed.
- **Update check**: on startup, checks GitHub Releases for a newer version. Shows a non-blocking banner if available.
- **Diagnose** (One-Click): Help → "Diagnose" → runs 6 health checks (DB integrity, disk space, AI provider, tunnel status, backup age, negative stock) with green/amber/red indicators.

### Daily-Use Friction Fixes
- **Start page**: the wizard's chosen start page is honored on each boot (POS for cashiers, Dashboard for owners).
- **Drag-drop bill upload**: drop a PDF/image on ANY page → auto-navigates to `/bills/new` and starts extraction.
- **Today's Profit ticker**: a green chip in the topbar showing today's gross profit. Click → opens Store Profit Dashboard.
- **Quick Expense FAB**: a red floating "+" button (bottom-right) → 2-field modal (amount + category) → saves instantly.

---

## Multi-Store Setup (v8.0+)

v8.0 transforms BillBook from a single-shop system into a multi-branch system. Every branch runs its own independent BillBook instance (local SQLite, offline-first). A lightweight HQ (Owner Hub) aggregates read-only summaries from all branches.

> **Full guide:** For a complete walkthrough of the 5 sync mechanisms, step-by-step setup, transfer/price-push/central-purchase flows, and troubleshooting, see **[MULTI_STORE_GUIDE.md](MULTI_STORE_GUIDE.md)**.

### Governing principle: Local Autonomy + Central Visibility

- If the internet drops in Lahore, Branch A keeps selling normally.
- No branch ever depends on another branch to make a sale.
- Sync is eventual — summaries, transfers, and price updates flow when connectivity allows.
- **Single-shop mode is the default.** A fresh install with no HQ configured behaves exactly as v7.2. You only need this section if you have 2+ physical locations.

### Step 1: Set up the HQ instance

1. Install BillBook on the owner's PC (the one that will act as the aggregation hub).
2. Open **Settings → Branch**.
3. Set **Role** to `Headquarters (aggregation hub)`.
4. Set **Branch Name** (e.g., "Central HQ") and **Region**.
5. Save. A `branch_id` is auto-generated (e.g., `BR-A1B2C3D4`).
6. Set up a Cloudflare Tunnel (see "Remote Access" above) so branches can reach this HQ over the internet. Note the tunnel URL (e.g., `https://billbook-hq.yourdomain.com`).

### Step 2: Register a branch

1. On the HQ instance, go to **AI Insights → HQ Branches**.
2. Click **Generate Code**. A 6-digit registration code appears (valid for 5 minutes, single-use).
3. Share this code with the branch owner (phone, WhatsApp, etc.).

### Step 3: Set up the Branch instance

1. Install BillBook on the branch's PC (e.g., the Karachi shop).
2. Open **Settings → Branch**.
3. Set **Branch Name** (e.g., "Karachi Branch") and **Region**.
4. Click **Register with Code**.
5. Enter:
   - **Hub URL**: the HQ's Cloudflare Tunnel URL (from Step 1).
   - **Registration Code**: the 6-digit code from Step 2.
   - **Your Branch's Tunnel URL**: the branch's own Cloudflare Tunnel URL (so HQ can push to it).
6. Click **Register**. The branch calls HQ, gets a sync token, and stores it locally (hashed).
7. The branch now appears in HQ's **HQ Branches** list with `active` status.

### Step 4: View the Owner Hub

On the HQ instance, go to **AI Insights → Owner Hub**. You'll see:
- Consolidated P&L (all branches summed)
- Branch Leaderboard (sales by branch, sorted descending)
- Per-branch breakdown (COGS, GP, margin, expenses, cash, stock value)
- Stale badge for branches that haven't synced in 24h

### Inter-branch stock transfers

To move stock from one branch to another:

1. On the SENDER branch, go to **Inventory → Transfer Out**.
2. Select the destination branch from the dropdown.
3. Add line items (category + qty). The unit cost is auto-filled from the sender's current running weighted average — it's locked into the challan.
4. Click **Create Transfer Challan**. The sender's stock reduces immediately; the avg cost is UNCHANGED (transfers are inventory movements, not sales — no COGS, no revenue).
5. On the RECEIVER branch, go to **Inventory → Transfer In**. The challan appears in the list.
6. Click **Accept** (stock increases at the locked unit cost) or **Reject** (no state change).

**The 185.88 integrity is preserved:** Branch A (17,000 pcs @ Rs 185.88) transfers 100 pcs to Branch B → Branch A still @ 185.88 (unchanged), Branch B now @ 185.88 (received at the captured unit cost), total stock = 17,000 (16,900 + 100), no COGS/revenue on either side.

### Central purchasing & distribution

HQ can act as a virtual "Central Warehouse" branch (`branch_id=BR-CENTRAL`):

1. On HQ, go to **Inventory → Central Buys**.
2. Click **New Central Buy**. Enter the supplier name + line items (category, qty, unit cost). Stock is added to HQ's local state at the central bulk-buy price.
3. Click **Distribute**. Select the destination branch + quantities. A transfer challan is created from BR-CENTRAL to that branch at the central unit cost.
4. The branch accepts the challan → its moving average updates at the central cost.

This is useful for bulk purchases where one buy is split across multiple branches.

### Global price push

To update a price category across all branches simultaneously:

1. On HQ, go to **AI Insights → Price Push**.
2. Click **New Price Push**. Select a category + new sell price.
3. Click **Push to All Branches**. HQ creates a `price_push_id` + shows the list of branches to deliver to.
4. Each branch receives the push (via its tunnel URL) + applies it idempotently. The activity log records `source='hq'`.

### Sync retry (eventual consistency)

If HQ is unreachable when a branch tries to sync:
- The branch keeps selling normally (sync never blocks a sale).
- The pending sync is queued in the local `sync_outbox` table.
- When HQ comes back, the next flush delivers the accumulated entries.
- `GET /api/sync/outbox` shows pending/sent/failed counts.
- `POST /api/sync/outbox/flush` with `{dest_url, bearer_token}` triggers a manual flush.

### Multi-store roles

| Role | What it does | Where to set it |
|---|---|---|
| `branch` (default) | Independent shop. Sells locally, pushes summaries, sends/receives transfers. | Settings → Branch → Role |
| `hq` | Aggregation hub. Maintains branch registry, hosts Owner Hub, routes price pushes + central purchases. Can also act as a Central Warehouse virtual branch. | Settings → Branch → Role |

**Single-shop mode:** with `role='branch'` + empty Hub URL (the defaults), the app behaves exactly as v7.2. No sync attempts, no UI friction, no behavioral change.

---

## Free vs Paid Signing

| Feature | Free (Default) | Paid |
|---------|---------------|------|
| Windows SmartScreen | Shows "Run anyway" warning | No warning |
| macOS Gatekeeper | Requires right-click → Open | No warning |
| Android APK | "Unknown sources" toggle | Play Store install |
| iOS | PWA (Safari) | App Store install |
| Cost | $0 | $99/yr (Apple) + $25 (Google) + $300+/yr (EV cert) |

BillBook v8.1 ships unsigned. The warnings are cosmetic — the app is fully functional. If you later want to remove the warnings, the codebase supports adding code-signing certificates without changes.

---

## Backup Strategy

1. **In-app**: Settings → Backups → "Backup Now" (manual).
2. **Scheduled**: Run `backup.bat` via Windows Task Scheduler nightly.
3. **Cloud sync**: Point Google Drive / OneDrive at the `data/backups/` folder.
4. **Desktop app**: The sidecar stores data in `AppData/Local/BillBook/data/` — back up this folder.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Windows SmartScreen | Click "More info" → "Run anyway" |
| macOS Gatekeeper | Right-click app → "Open" → confirm |
| Android "Unknown sources" | Settings → Security → enable for browser |
| Server not reachable from phone | Check LAN Mode is on; disable firewall for port 8000 |
| Pairing code expired | Generate a new code (5-min expiry) |
| Tunnel not working | Check internet on shop PC; restart cloudflared |
| App won't start | Check `data/app.log` for errors |
| Database locked | Stop all instances; delete `data/billbook.db-wal` and `data/billbook.db-shm` |
| AI Assistant says "AI is currently disabled" | Kill switch is ON. Go to AI Usage page → "Enable AI" |
| AI Assistant gives wrong numbers | Agent uses READ tools — numbers always match your reports. If they don't, click "Rebuild Stock State" in Inventory first. |
| Approval Queue shows stale count | Sidebar badge refreshes every 60s. Click the Approval Queue link to force-refresh. |
| Price change approval fails | Manager PIN required (4-8 digits). If forgotten, reset via Settings → Security → "Reset PIN". |
| "Daily AI budget exhausted" | Groq limit 500 calls/day, Gemini 100/day. Resets at midnight local time. Clear cache to reduce calls. |
| Season-prep button disabled | Kill switch is ON. Disable it first on the AI Usage page. |
| AI Usage chart empty | No AI calls yet. Use the AI Assistant or run "Prepare for Season" to generate activity. |
| Branch won't register | Check the 6-digit code hasn't expired (5-min limit) + hasn't been used (single-use). Generate a new code on HQ. |
| Branch can't reach HQ | Verify HQ's Cloudflare Tunnel URL is correct + HQ is running. Test by opening the URL in a browser. |
| Owner Hub shows stale branch | Branch hasn't synced in 24h. Check the branch's internet + that it has a valid sync token (Settings → Branch → has_sync_token). |
| Transfer challan stuck "in transit" | The receiver must accept/reject it. On the receiver, go to Inventory → Transfer In. If the receiver is offline, the challan waits. |
| Price push didn't apply | Check the branch's activity log for `price_push_applied` with `source='hq'. If missing, the branch's tunnel URL may be wrong — verify on HQ Branches page. |
| Sync outbox growing | HQ unreachable. Check `GET /api/sync/outbox` for pending count. Once HQ is back, run `POST /api/sync/outbox/flush` or wait for the next auto-flush. |
| Branch DB has other branches' data | This should never happen — branch purity is enforced. If it does, the branch's DB was manually edited. Restore from backup. |
