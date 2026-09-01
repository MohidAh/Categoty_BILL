# BillBook — Tauri Auto-Updater Testing Guide (v2, corrected)

This guide walks you through testing the v8.14 → v8.15 update flow on
**your own dev machine**, no GitHub, no clients, no internet required.

> **v8.15.1 corrections vs the old guide** (these were Tauri v1-isms):
> 1. `updater.active` is not a Tauri v2 key — it was silently ignored.
>    v2 activates the updater via `bundle.createUpdaterArtifacts: true`
>    + the `plugins.updater` block. Already wired in `desktop/tauri.conf.json`.
> 2. **Release builds hard-reject plain `http://` endpoints.** Local testing
>    uses the override file `desktop/tauri.test.conf.json` (sets
>    `dangerousInsecureTransportProtocol: true`) via `--config`.
> 3. **`cargo tauri build` does NOT generate latest.json in v2** —
>    `build/windows/make_latest_json.py` creates it from the `.sig` file.
> 4. Correct output paths: `desktop\target\release\bundle\nsis\` (there is no
>    `desktop\src-tauri\` — the Tauri project root IS `desktop\`).
> 5. The update check now runs **Rust-side ~8s after launch** (no DevTools
>    console needed) — see `desktop/src/main.rs`.

---

## Prerequisites

1. Windows 10/11 dev machine with admin rights.
2. Rust + Tauri CLI installed:
   ```powershell
   winget install Rustlang.Rustup
   npm install -g @tauri-apps/cli
   # (reopen the terminal afterwards so PATH refreshes)
   ```
3. Python 3.10+ + PyInstaller (already installed per INSTALL_GUIDE.md).
4. WebView2 runtime (pre-installed on Win11; on Win10 download from
   https://developer.microsoft.com/microsoft-edge/webview2/).

---

## Phase 1 — Keys + config (5 min, ONE TIME)

```powershell
cd D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE

# 1. Generate the Ed25519 keypair
powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
#    When asked for a password: press ENTER for none (simplest), or set one
#    and remember it forever.
```

Copy the printed public key (`dW50cnVzdGVkIGNvbW1lbnQ6...==`) and paste it
into `desktop\tauri.conf.json` → `plugins.updater.pubkey` (replacing
`REPLACE_WITH_REAL_ED25519_PUBKEY_BEFORE_ENABLING`).

The fake-CDN endpoint override for local testing is already prepared in
`desktop\tauri.test.conf.json` (points at `http://127.0.0.1:8085/latest.json`
and allows plain http). Nothing to edit there.

---

## Phase 2 — Build + install the "old" version (v8.14.0)

```powershell
# 1. Set the OLD version + wire the signing key
notepad desktop\tauri.conf.json
#    change "version": "8.15.0"  ->  "8.14.0"

# 2. Point TAURI_SIGNING_PRIVATE_KEY at your key (must be set BEFORE building;
#    .env files do NOT work)
$env:TAURI_SIGNING_PRIVATE_KEY = "$PWD\desktop\.tauri\updater-private.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""   # empty if you chose no password

# 3. Build with the TEST config merged over the base config
cd desktop
cargo tauri build --config tauri.test.conf.json
cd ..
```

Outputs land in (note: no `src-tauri` segment — `desktop` IS the project root):

```
desktop\target\release\bundle\nsis\BillBook_8.14.0_x64-setup.exe
desktop\target\release\bundle\nsis\BillBook_8.14.0_x64-setup.exe.sig   <- Ed25519 signature
```

**Verify the `.sig` file exists** — if it does not, the build could not find
your signing key (check `$env:TAURI_SIGNING_PRIVATE_KEY` and re-run).

```powershell
# 4. Install v8.14
.\desktop\target\release\bundle\nsis\BillBook_8.14.0_x64-setup.exe
```

Launch BillBook from the Start menu when the installer finishes.
v8.14 is now installed and (8 seconds after every launch) quietly asks
`http://127.0.0.1:8085/latest.json` whether a newer version exists.
The fake CDN is not running yet, so nothing happens. That is expected.

---

## Phase 3 — Build the "new" version (v8.15.0)

```powershell
notepad desktop\tauri.conf.json
#    change "version": "8.14.0"  ->  "8.15.0"

# (optional but recommended) leave a visible trace that the update worked:
notepad app\static\index.html
#    add near the top:  <!-- v8.15.0 test build -->

# Same env vars still set in this session? If you opened a NEW terminal, re-set:
$env:TAURI_SIGNING_PRIVATE_KEY = "$PWD\desktop\.tauri\updater-private.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""

cd desktop
cargo tauri build --config tauri.test.conf.json
cd ..
```

---

## Phase 4 — Set up the fake CDN (the "release server")

```powershell
# 1. Folder to host the release files
mkdir C:\BillBook\fake-cdn
cd C:\BillBook\fake-cdn

# 2. Copy the NEW build's installer + signature
copy D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE\desktop\target\release\bundle\nsis\BillBook_8.15.0_x64-setup.exe .
copy D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE\desktop\target\release\bundle\nsis\BillBook_8.15.0_x64-setup.exe.sig .

# 3. Generate latest.json (reads version + signature automatically).
#    localhost is detected -> flat URLs (http://127.0.0.1:8085/<file>)
cd D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE
python build\windows\make_latest_json.py --base-url http://127.0.0.1:8085 `
    --notes "Test release v8.15.0" --out C:\BillBook\fake-cdn\latest.json

# 4. Run the fake CDN server (leave the terminal open!)
cd C:\BillBook\fake-cdn
python D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE\build\windows\test_updater_locally.py
```

You should see:

```
[fake-cdn] Serving C:\BillBook\fake-cdn on http://127.0.0.1:8085/
[fake-cdn] Press Ctrl+C to stop.
```

Your `latest.json` now looks like:

```json
{
  "version": "8.15.0",
  "notes": "Test release v8.15.0",
  "pub_date": "2026-08-27T15:30:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "dW50cnVzdGVk...==",
      "url": "http://127.0.0.1:8085/BillBook_8.15.0_x64-setup.exe"
    }
  }
}
```

---

## Phase 5 — Trigger the update from v8.14

Close BillBook (right-click tray icon → Quit) and launch it again from the
Start Menu. **Nothing to click in DevTools** — about 8 seconds after launch
the Rust shell checks the endpoint by itself:

1. Native dialog appears: *"BillBook v8.15.0 is available. Update now? The
   update downloads in the background while you keep working..."* → click
   **Update now**.
2. IMMEDIATELY a small dark progress toast appears in the bottom-right
   corner of the window (v8.18.8): *"Downloading BillBook v8.15.0...
   45% · 12.3 of 27.1 MB"* with a green progress bar. The POS underneath
   stays usable — click around the app while it downloads.
3. The updater downloads `BillBook_8.15.0_x64-setup.exe`, verifying its
   Ed25519 signature against the pubkey compiled into v8.14 **before**
   anything runs.
4. When the download finishes the toast turns blue: *"Update ready —
   closing BillBook to install"*. About 1.5s later BillBook exits, the
   NSIS installer runs in passive mode (progress bar, no clicks) and
   relaunches the app when done.
   (If the download FAILS, the toast turns red, stays ~10s, removes
   itself, and the app keeps running — retry on next launch.)

Watch the fake-cdn terminal — you should see both fetches:

```
[fake-cdn] 127.0.0.1 - GET /latest.json
[fake-cdn] 127.0.0.1 - GET /BillBook_8.15.0_x64-setup.exe
```

---

## Phase 6 — Verify the update applied

- The visible trace from Phase 3 shows up (e.g. view source / the HTML comment).
- In BillBook: Help → About → version shows **8.15.0**.
- The old v8.14 installer is gone from "Apps & features" — replaced in place
  by 8.15.0 (user data is untouched; NSIS upgrade installs over).

---

## Phase 7 — Test the failure modes (important!)

The updater is crypto-strict. Verify each mode once, then restore the files.

### 7a. Tampered update must be REJECTED
1. Edit `C:\BillBook\fake-cdn\latest.json` → change the `signature` value
   to `"INVALID"`.
2. Downgrade-first trick: reinstall v8.14 (run the v8.14 installer again),
   then relaunch.
3. Expected: the update is detected but fails with `signature verification
   failed`. **The app stays on v8.14.** Restore the real signature after.

### 7b. Wrong private key must be REJECTED
1. Generate a SECOND keypair somewhere:
   `cargo tauri signer generate -w C:\BillBook\fake-key.key`
2. Re-sign the v8.15 installer with it:
   `cargo tauri signer sign -k C:\BillBook\fake-key.key -f C:\BillBook\fake-cdn\BillBook_8.15.0_x64-setup.exe`
3. Put the new signature into `latest.json` (the `.sig` file next to the exe
   now contains it).
4. Expected: `signature mismatch` — v8.14's compiled-in pubkey is the REAL
   one, so the fake-signed bundle is refused.

### 7c. Version downgrade must be IGNORED
1. Edit `latest.json` → `"version": "8.13.0"` (older than installed).
2. Relaunch BillBook.
3. Expected: no dialog — the updater only moves forward.

### 7d. Endpoint down must fail GRACEFULLY
1. Stop the fake CDN (Ctrl+C).
2. Relaunch BillBook.
3. Expected: app starts normally; only a console line
   `[billbook-updater] ...` (visible if launched from a terminal). The POS
   is never blocked by update problems.

---

## Phase 8 — Move to production (real GitHub releases)

Once Phases 1-7 pass:

```powershell
# 1. Flip endpoints to the real GitHub URL
notepad desktop\tauri.conf.json
#    endpoints: ["https://github.com/YOUR-USER/BillBook/releases/latest/download/latest.json"]
#    (the test override is NOT used in production builds)

# 2. Create a GitHub repo (public — private repo release downloads need
#    authentication, which the updater cannot do) and push your code
gh repo create BillBook --public --source=. --push

# 3. Build the release WITHOUT the test override
$env:TAURI_SIGNING_PRIVATE_KEY = "$PWD\desktop\.tauri\updater-private.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
cd desktop ; cargo tauri build ; cd ..

# 4. Publish (generates latest.json + creates the GitHub release)
powershell -ExecutionPolicy Bypass -File build\windows\publish_release.ps1 `
    -Owner YOUR-USER -Repo BillBook -Notes "Bug fixes" -GitHub

# 5. Ship the v8.15.0 installer to a friendly beta-tester client once
#    (their existing Inno-Setup v8.15 install cannot self-update — the
#    auto-update chain starts with the first TAURI-shell install).
# 6. Next time you release v8.16.0 (bump "version" in tauri.conf.json,
#    rebuild, publish), every client picks it up on their next launch.
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Build error "A public key has been found, but no private key" | `TAURI_SIGNING_PRIVATE_KEY` not set in this session | `$env:TAURI_SIGNING_PRIVATE_KEY = "$PWD\desktop\.tauri\updater-private.key"` then rebuild |
| Build error mentions invalid password | Key was generated WITH a password | `$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "your password"` |
| Config error at app startup: `InsecureTransportProtocol` | Release build + `http://` endpoint without the override | Build with `--config tauri.test.conf.json` (production uses https) |
| No dialog ever appears | Endpoint unreachable / version in `latest.json` <= installed | Check fake CDN is running; check `version` > installed version |
| "no update available" but v8.15 is on the CDN | `latest.json` version field still says the installed version | Regenerate with `make_latest_json.py` |
| `signature verification failed` | Wrong signature in `latest.json`, or rebuilt with a different key | Re-run `make_latest_json.py` (it reads the fresh `.sig`) |
| Update downloads but installer fails | BillBook data dir locked | Rare; relaunch and retry — the updater exits the app before installing |
| App fetches but installer "hangs" | `installMode` is `basicUi` waiting for clicks | Keep `passive` (progress bar, no interaction) |

---

## Files involved

- `desktop/tauri.conf.json` — updater endpoints, pubkey, `createUpdaterArtifacts`
- `desktop/tauri.test.conf.json` — localhost endpoint override (testing ONLY)
- `desktop/src/main.rs` — Rust-side auto-check ~8s after launch
- `build/windows/generate_updater_keys.ps1` — Ed25519 keypair generator
- `build/windows/make_latest_json.py` — builds `latest.json` from the `.sig`
- `build/windows/test_updater_locally.py` — fake CDN on `127.0.0.1:8085`
- `build/windows/publish_release.ps1` — stages + creates the GitHub release
- `desktop/.tauri/updater-private.key` — your SECRET key (never commit, never ship)

**BACK UP `desktop/.tauri/updater-private.key` to two places** (USB + cloud).
If you lose it, every existing install can never auto-update again — clients
would need a fresh full install of the next version.
