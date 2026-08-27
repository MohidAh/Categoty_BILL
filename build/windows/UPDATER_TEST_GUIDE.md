# BillBook — Tauri Auto-Updater Testing Guide

This guide walks you through testing the v8.14 → v8.15 update flow on
**your own dev machine**, no GitHub, no clients, no internet required.

After you finish this, you'll have confidence the production flow works.

---

## Prerequisites

1. Windows 10/11 dev machine with admin rights.
2. Rust + Tauri CLI installed:
   ```powershell
   # Rust
   winget install Rustlang.Rustup
   # Tauri CLI
   cargo install tauri-cli --version "^2.0.0"
   ```
3. Python 3.11+ + PyInstaller + Inno Setup (already installed per INSTALL_GUIDE.md).
4. WebView2 runtime (pre-installed on Win11; on Win10 download from
   https://developer.microsoft.com/microsoft-edge/webview2/).

---

## Phase 1 — Generate keys + flip the updater on (5 min, ONE TIME)

```powershell
cd C:\path\to\BILL_MANAGEMENT_SOFTWARE

# 1. Generate the Ed25519 keypair
powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
```

Output will print a public key like `dQ4A...XYZ==`. Copy it.

```powershell
# 2. Edit desktop\tauri.conf.json
notepad desktop\tauri.conf.json
```

Change the `updater` block to (paste your real pubkey):

```json
"updater": {
  "active": true,
  "endpoints": [
    "http://localhost:8085/latest.json"
  ],
  "pubkey": "PASTE_YOUR_REAL_PUBKEY_HERE"
}
```

For testing we use `http://localhost:8085` — a tiny fake CDN script
we'll run in Phase 3. For production you'll swap this to:
`https://github.com/USER/REPO/releases/latest/download/latest.json`

---

## Phase 2 — Build + install v8.14 (the "old" version)

```powershell
# 1. Make sure desktop\tauri.conf.json has version "8.14.0"
#    (edit the "version" field at the top of the file)

# 2. Build the .msi + setup.exe (signed with your private key)
cd desktop
cargo tauri build

# Output lands in:
#   src-tauri\target\release\bundle\nsis\BillBook_8.14.0_x64-setup.exe
#   src-tauri\target\release\bundle\msi\BillBook_8.14.0_x64.msi
#   src-tauri\target\release\latest.json   <-- Tauri auto-generates this!
```

**Verify the signature file was created** — Tauri puts a `.sig` file next
to each bundle. You'll see:
- `BillBook_8.14.0_x64-setup.exe`
- `BillBook_8.14.0_x64-setup.exe.sig`  ← the Ed25519 signature

```powershell
# 3. Install v8.14
.\src-tauri\target\release\bundle\nsis\BillBook_8.14.0_x64-setup.exe
```

Walk through the installer. Launch BillBook when it's done.
You should see the dashboard running.

**At this point, v8.14 is installed and looking at `http://localhost:8085/latest.json` for updates.** But the fake CDN isn't running yet, so it silently fails. That's expected.

---

## Phase 3 — Build v8.15 (the "new" version with the bug fix)

```powershell
# 1. Bump the version
notepad desktop\tauri.conf.json
# Change "version": "8.14.0" -> "version": "8.15.0"

# 2. (Optional) Make a tiny visible change so you can verify the update worked.
#    Edit app\static\index.html and add a comment like:
#    <!-- v8.15.0 — bug X fixed -->

# 3. Build v8.15 (signed with the same private key)
cargo tauri build
```

---

## Phase 4 — Set up the fake CDN (the "release server")

```powershell
# 1. Create a folder to host release files
mkdir C:\BillBook\fake-cdn
cd C:\BillBook\fake-cdn

# 2. Copy the v8.15 build outputs there
copy C:\path\to\BILL_MANAGEMENT_SOFTWARE\desktop\src-tauri\target\release\bundle\nsis\BillBook_8.15.0_x64-setup.exe .
copy C:\path\to\BILL_MANAGEMENT_SOFTWARE\desktop\src-tauri\target\release\latest.json .

# 3. The latest.json Tauri auto-generated points at a relative URL.
#    Open it and verify the "url" field says:
#       "http://localhost:8085/BillBook_8.15.0_x64-setup.exe"
#    If not, edit it. Also verify "version": "8.15.0".

notepad latest.json
```

Your `latest.json` should look like:
```json
{
  "version": "8.15.0",
  "notes": "Bug fix release.",
  "pub_date": "2026-08-26T15:30:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "dQ4A8tB...long-string...==",
      "url": "http://localhost:8085/BillBook_8.15.0_x64-setup.exe"
    }
  }
}
```

```powershell
# 4. Run the fake CDN server
python C:\path\to\BILL_MANAGEMENT_SOFTWARE\build\windows\test_updater_locally.py
```

You should see:
```
[fake-cdn] Serving C:\BillBook\fake-cdn on http://127.0.0.1:8085/
[fake-cdn] Press Ctrl+C to stop.
```

**Leave this terminal window open.** This is your "GitHub Releases" for the test.

---

## Phase 5 — Trigger the update from v8.14

The v8.14 BillBook app you installed in Phase 2 is set to fetch
`http://localhost:8085/latest.json` on startup. Three ways to trigger:

### Method A — Just relaunch BillBook
Tauri's updater checks on app start. Close BillBook (right-click
tray icon → Quit) and launch it again from the Start Menu.

### Method B — Use Tauri's DevTools console
1. Launch BillBook v8.14.
2. Right-click inside the window → Inspect Element (or press F12 if dev
   tools are enabled).
3. In the Console, run:
   ```js
   await window.__TAURI__.updater.checkForUpdate()
   ```
4. Returns `{ available: true, version: "8.15.0", ... }` if everything's
   working.

### Method C — Programmatically (what your production app would do)
Add a "Check for updates" button somewhere in the UI that calls:
```js
import { check } from '@tauri-apps/plugin-updater';
const update = await check();
if (update?.available) {
    const ok = confirm(`Update to v${update.version}? Notes: ${update.body}`);
    if (ok) {
        await update.downloadAndInstall();
        await relaunch();
    }
}
```

---

## Phase 6 — Verify the update applied

When you click "Update":
1. The v8.14 app downloads `BillBook_8.15.0_x64-setup.exe` (~80 MB, fast on localhost).
2. It verifies the Ed25519 signature against the pubkey baked into v8.14.
3. It silently runs the installer (NSIS overlay install — preserves data).
4. It relaunches as v8.15.

**How to verify it actually worked:**
- In the fake-cdn terminal, you should see two GET requests:
  ```
  [fake-cdn] 127.0.0.1 - GET /latest.json
  [fake-cdn] 127.0.0.1 - GET /BillBook_8.15.0_x64-setup.exe
  ```
- In BillBook: Help → About → version shows "8.15.0".
- The visible change you made in Phase 3 step 2 should now appear (e.g. the HTML comment).

---

## Phase 7 — Test the failure modes (important)

The updater has crypto-strict guarantees. Verify each failure mode:

### 7a. Unsigned update should be REJECTED
1. Edit `latest.json` — change the `signature` field to `"INVALID"`.
2. Relaunch BillBook v8.14.
3. Expected: update is detected but rejected with an error in the Tauri logs
   like `signature mismatch`. **No silent downgrade**.
4. App stays on v8.14. Restore the real signature after.

### 7b. Wrong private key should be REJECTED
1. Generate a SECOND keypair in a different folder:
   ```
   cargo tauri signer generate -w .tauri/FAKE-private.key -p .tauri/FAKE-public.key
   ```
2. Re-sign the v8.15 setup.exe with the FAKE key:
   ```
   cargo tauri signer sign -k .tauri/FAKE-private.key -f BillBook_8.15.0_x64-setup.exe
   ```
3. Put the new (fake) signature in `latest.json`.
4. Relaunch BillBook v8.14.
5. Expected: `signature mismatch` — the v8.14 app's baked-in pubkey is
   the REAL one, so it rejects the FAKE-signed bundle.

### 7c. Version downgrade should be IGNORED
1. Swap `latest.json` to point at v8.13.0 (older than installed v8.14).
2. Relaunch v8.14.
3. Expected: updater silently does nothing (newer version installed).

### 7d. Endpoint down should fail gracefully
1. Stop the fake-cdn server (Ctrl+C in its terminal).
2. Relaunch BillBook v8.14.
3. Expected: app launches normally; only a console warning about
   unreachable update endpoint.

---

## Phase 8 — Move to production (real GitHub releases)

Once Phase 1-7 all pass:

```powershell
# 1. Edit tauri.conf.json endpoints back to GitHub
notepad desktop\tauri.conf.json
# Change endpoint to:
#   "https://github.com/YOUR-USER/BillBook/releases/latest/download/latest.json"

# 2. Create a private GitHub repo + push code
gh repo create BillBook --private --source=. --push

# 3. Build v8.15.0
cargo tauri build

# 4. Create the GitHub release + upload artifacts
gh release create v8.15.0 `
    src-tauri/target/release/bundle/nsis/BillBook_8.15.0_x64-setup.exe `
    src-tauri/target/release/bundle/msi/BillBook_8.15.0_x64.msi `
    src-tauri/target/release/latest.json `
    --title "v8.15.0 — Bug fixes" `
    --notes "Fixed bug X, Y, Z"

# 5. Ship v8.14 installer to a friendly beta-tester client.
# 6. Push v8.15 to GitHub (step 4 above).
# 7. Their app auto-detects + updates within ~1 minute of next launch.
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "no update available" but you know v8.15 is on the CDN | `latest.json` `version` field still says `8.14.0` | Edit it to `8.15.0` |
| `signature verification failed` | Wrong signature in `latest.json`, OR you rebuilt with a different private key | Re-sign: `cargo tauri signer sign -k desktop/.tauri/updater-private.key -f BillBook_*.exe` |
| App doesn't even try to fetch `latest.json` | `updater.active` is still `false` | Edit `tauri.conf.json`, rebuild, reinstall |
| `connect ECONNREFUSED 127.0.0.1:8085` | Fake CDN script not running | Start it: `python build/windows/test_updater_locally.py` |
| App fetches `latest.json` but never offers update | Tauri doesn't auto-prompt — you have to call `check()` from JS | Add an "Check for updates" button or call it on startup |
| Update downloads but installer fails | Data dir locked (BillBook still running) | Tauri auto-closes BillBook before install; if it doesn't, you may need to call `relaunch()` after `downloadAndInstall()` |

---

## Files this guide adds to your project

- `build/windows/test_updater_locally.py` — fake CDN script (Phase 4)
- `build/windows/UPDATER_TEST_GUIDE.md` — this document
- `desktop/.tauri/updater-private.key` — your SECRET private key (Phase 1, after you run the generator)
- `desktop/.tauri/updater-public.key` — your public key (paste into tauri.conf.json)

**BACK UP `desktop/.tauri/updater-private.key` to two places:**
- USB stick kept in a safe
- Paper printout stored in a locked drawer

If you lose this key, every existing v8.14 install becomes un-updateable —
clients would need a fresh full install of the next version.
