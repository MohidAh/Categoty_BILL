# BillBook — Complete Tauri Auto-Update Setup (v8.15.1)

This document is the master guide for the auto-update system of the BillBook
Tauri desktop shell. It explains **how the pieces fit together**, the **one-time
setup**, the **every-release workflow**, and the **architecture decisions**.

Companion docs:
- `build/windows/UPDATER_TEST_GUIDE.md` — step-by-step local end-to-end test
- `build/windows/generate_updater_keys.ps1` — keypair generator
- `build/windows/make_latest_json.py` — update manifest generator
- `build/windows/publish_release.ps1` — one-shot release publisher

---

## 1. How it works (the 60-second version)

```
   CLIENT (shop PC, runs v8.15.0)                      YOU (release day)
   ┌─────────────────────────────┐
   │ BillBook.exe (Tauri shell)  │
   │  └─ spawns billbook_sidecar │
   │     (Python POS backend)    │
   │                             │        1. cargo tauri build
   │  8s after launch:           │           (signs installer with
   │  GET <endpoint>/latest.json │◄──────────  updater-private.key)
   │         │                   │        2. make_latest_json.py
   │  v_remote > v_local ?       │        3. publish_release.ps1
   │         │ yes               │           (GitHub Release:
   │  native dialog "Update?"    │             exe + .sig + latest.json)
   │         │ user clicks Yes   │
   │  download setup.exe         │
   │  (v8.18.8: progress toast   │
   │   in the corner; POS usable)│
   │  verify Ed25519 signature   │
   │    against compiled pubkey  │
   │         │ ok                │
   │  toast: "closing to install"│
   │  run installer (passive UI) │
   │  app exits, NSIS relaunches │
   │  -> v8.16.0 runs            │
   └─────────────────────────────┘
```

Three guarantees make this safe:

1. **Signature or nothing.** Every bundle is Ed25519-signed with your private
   key at build time; clients verify it against the public key compiled into
   their binary BEFORE anything is executed. Tampered or wrongly-signed
   updates are refused. This cannot be disabled.
2. **Forward-only.** The updater only installs versions strictly greater
   (SemVer) than the installed one. No downgrades.
3. **Never blocks the POS.** The check runs ~8s after launch on a background
   task; every error (endpoint down, timeout, bad JSON) is swallowed and
   logged. The worst case is "no update this launch".

---

## 2. The moving parts in THIS repo

| File | Role |
|---|---|
| `desktop/tauri.conf.json` | `bundle.createUpdaterArtifacts: true` makes the build emit the installer + `.sig`; `plugins.updater.{endpoints, pubkey, windows.installMode}` configures the client |
| `desktop/tauri.test.conf.json` | Local-testing override (http endpoint + `dangerousInsecureTransportProtocol`), used via `cargo tauri build --config tauri.test.conf.json` |
| `desktop/src/main.rs` | Rust-side `check_for_updates()` — runs 8s after launch, confirms with a native dialog, downloads, verifies, installs, relaunches |
| `desktop/Cargo.toml` | `tauri-plugin-updater`, `tauri-plugin-dialog`, `tokio` (delay) |
| `desktop/capabilities/default.json` | IPC permissions (`updater:default`, `process:default`) — only needed if you later add a JS "Check for updates" button |
| `desktop/.tauri/updater-private.key` | **SECRET.** Signs every release. Never commit, never ship, BACK IT UP |
| `desktop/.tauri/updater-public.key` | Paste its content into `tauri.conf.json` once |
| `build/build_windows.ps1` | Full build: keypair (if missing) → sidecar staging → sets `TAURI_SIGNING_PRIVATE_KEY` → `tauri build` |
| `build/windows/make_latest_json.py` | Creates `latest.json` (the manifest clients fetch) from the built `.sig` |
| `build/windows/publish_release.ps1` | Stages exe + .sig + latest.json and optionally creates the GitHub Release |

---

## 3. One-time setup (do this once, ever)

### 3.1 Prerequisites

```powershell
winget install Rustlang.Rustup          # Rust toolchain (rustc + cargo)
npm install -g @tauri-apps/cli          # Tauri CLI v2
# reopen the terminal afterwards
```

### 3.2 Generate the keypair

```powershell
powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
```

When prompted for a password: ENTER for none (simplest) or set one and never
lose it. The script prints the PUBLIC key — paste it into
`desktop/tauri.conf.json` → `plugins.updater.pubkey`.

**Back up `desktop\.tauri\updater-private.key` to two places now** (USB +
cloud storage). If you lose it, every installed client can never auto-update
again — they would each need a fresh full install.

### 3.3 Set the production endpoint

In `desktop/tauri.conf.json` (already the default pattern):

```json
"endpoints": [
  "https://github.com/YOUR-USER/BillBook/releases/latest/download/latest.json"
]
```

Requirements for the host:
- **HTTPS is mandatory** in release builds (plain http is hard-rejected).
- It must serve `latest.json` + the installer at stable URLs.
- **GitHub Releases only works with a PUBLIC repo** — private-repo asset
  downloads require authentication, which the client updater cannot do.
  Alternatives: any HTTPS static host (Cloudflare R2, Netlify, your VPS);
  then set `endpoints` to `https://your-host/latest.json` and pass
  `--flat` to `make_latest_json.py`.

### 3.4 That's it

The updater code, signing wiring, and build-script integration are already
in place. Nothing to enable, no `active` flag (that was Tauri v1).

---

## 4. Every-release workflow (5 minutes)

```powershell
cd D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE

# 1. Bump the version (SemVer: 8.15.0 -> 8.16.0)
notepad desktop\tauri.conf.json          # "version": "8.16.0"
notepad desktop\Cargo.toml               # keep in sync (cosmetic)

# 2. Set signing env vars for THIS terminal session (.env files do NOT work)
$env:TAURI_SIGNING_PRIVATE_KEY = "$PWD\desktop\.tauri\updater-private.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""     # or your password

# 3. Build (or use the full pipeline: build\build_windows.ps1, which sets
#    these env vars for you)
cd desktop
cargo tauri build
cd ..

# 4. Publish (creates latest.json + GitHub release)
powershell -ExecutionPolicy Bypass -File build\windows\publish_release.ps1 `
    -Owner YOUR-USER -Repo BillBook -Notes "What changed" -GitHub
```

Done. Every client detects v8.16.0 within seconds of their next launch and
updates after one confirmation click.

**Shortcut:** `build\build_windows.ps1` (without `-SkipTauri`) performs steps
2-3 automatically — keypair check, sidecar staging, signing env vars, tauri
build. Step 4 stays manual on purpose (you decide the release moment).

---

## 5. What each build produces

```
desktop\target\release\bundle\nsis\
    BillBook_8.16.0_x64-setup.exe        <- full installer (app + sidecar)
    BillBook_8.16.0_x64-setup.exe.sig    <- Ed25519 signature of the above
build\updater\
    latest.json                          <- manifest generated by
                                             publish_release.ps1 / make_latest_json.py
```

`latest.json` format (Tauri validates the WHOLE file before checking versions):

```json
{
  "version": "8.16.0",
  "notes": "What changed",
  "pub_date": "2026-08-27T15:30:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<CONTENT of the .sig file — not a path, not a URL>",
      "url": "https://github.com/YOUR-USER/BillBook/releases/download/v8.16.0/BillBook_8.16.0_x64-setup.exe"
    }
  }
}
```

Upload the three files together to the same release. If `latest.json` and
the installer disagree (stale signature, old version), clients see either
"no update" or a signature failure — regenerate with `make_latest_json.py`
instead of hand-editing.

---

## 6. Design decisions (why it is wired this way)

### Update check in Rust, not JavaScript
The BillBook frontend is plain HTML/JS with no bundler and no npm runtime —
loading `@tauri-apps/plugin-updater` would mean vendoring IIFE bundles into
`app/static`. Doing the check in Rust (main.rs) means:
- zero frontend changes;
- it works even if the webview is still loading;
- it cannot be forgotten when pages change.

If you later want a "Check for updates" button in the UI, the capability
permissions are already granted (`desktop/capabilities/default.json`); the
frontend snippet is:

```js
// requires @tauri-apps/plugin-updater + plugin-process IIFE bundles vendored
// into app/static/js/vendor/ and withGlobalTauri: true in tauri.conf.json
const update = await window.__TAURI__.updater.check();
if (update) {
    await update.downloadAndInstall();
    await window.__TAURI__.process.relaunch();
}
```

### installMode: "passive"
The NSIS installer runs with a progress bar and **no user interaction**
(`/P`), then relaunches BillBook (`/R`). Shop staff cannot cancel halfway
or click through wrong options. `basicUi` would add click-through dialogs;
`quiet` hides all feedback and needs admin — neither suits a POS.

### One confirmation dialog
The check fires 8s after launch — after the dashboard is visible, before
anyone is deep into a sale. One click ("Update now" / "Later") and the rest
is automatic. Update runs while the shop is open are safe: the sidecar
shutdown + passive install takes ~10-20s.

### v8.18.8 — download progress toast
Clicking "Update now" used to be followed by a 30-60s SILENT gap (the
download — release builds have no console, so the byte counters were
invisible) after which the app closed "out of nowhere" and the setup
opened. The flow is now visible end-to-end, with zero frontend files
touched:

1. The confirm dialog says up front: the download runs in the background,
   the app closes when it's done, and restarts after the install.
2. Immediately after the click a small dark toast appears in the
   bottom-right corner of the main webview — `Downloading BillBook
   v8.19.0...  45% · 12.3 of 27.1 MB` with a progress bar. It is
   `pointer-events: none`, so the POS underneath stays fully usable.
3. The toast is painted from Rust every 600ms via `WebviewWindow::eval`
   (`paint_update_progress` in `desktop/src/main.rs`), so it survives POS
   page navigation (each tick re-creates the element if the page wiped
   it) and works on every page, including the splash.
4. When the download finishes, the toast switches to a blue
   "Update ready — closing BillBook to install" note and the app exits
   ~1.5s later — the close is announced, never a surprise.
5. If the download FAILS, the toast turns red ("Update download failed —
   you can keep working; the update will be offered again next launch"),
   stays ~10s, then removes itself. The app keeps running.

The injected script builds its DOM with `createElement` + CSSOM style
writes only (no `innerHTML`, no inline style attributes, `textContent`
for all text), so page CSP can never block it and nothing from
latest.json is ever interpreted as markup.

### Sidecar rides along
The Python POS backend is bundled as `billbook_sidecar` INSIDE the installer
(`bundle.externalBin`). An app update therefore updates the backend, the
frontend assets, and the shell together — there is exactly one version on
any machine, and the DB schema migrations in the backend run on first launch
of the new version. (Build requirement: `desktop\binaries\billbook_sidecar-<target-triple>.exe`
must exist — `build_windows.ps1` builds and stages it automatically.)

### Windows exit-and-relaunch
On Windows the app MUST exit while the installer runs (installer limitation).
`download_and_install()` handles this: it launches the installer, runs the
`on_before_exit` hook, and exits the process; NSIS relaunches BillBook when
finished. On macOS/Linux the code calls `app.restart()` explicitly.

---

## 7. Security model & operational rules

1. **The private key is the whole ballgame.** Anyone holding it can ship an
   update to every client. It lives only on YOUR dev machine (never in git —
   `desktop/.tauri/` is gitignored; never in the installer — the build script
   excludes it).
2. **Ed25519 ≠ Authenticode.** The updater signature proves the update came
   from you; it does NOT remove Windows SmartScreen warnings on first
   install. That is a separate, optional step (`signtool` / certificate);
   the build script has a `-SignCode` hook for it.
3. **Endpoint HTTPS only.** Browsers/clients between the shop and the host
   cannot tamper with `latest.json` or the installer undetected (signature
   would fail anyway).
4. **Rollback policy:** forward-only by design. If a release is bad, ship a
   HOTFIX with a higher version number (e.g. 8.16.1) — never try to downgrade.
5. **Version discipline:** the version in `desktop/tauri.conf.json` is the
   single source of truth for updates. Never build a release with the same
   version as one already published (clients would see "no update").

---

## 8. Rollout plan for existing shops

Existing v8.14/v8.15 clients run the Inno-Setup build (no Tauri shell) —
they **cannot** self-update into the Tauri world. The bootstrap:

1. Publish the first Tauri-shell release (e.g. v8.16.0) to GitHub.
2. Manually install it on each shop PC (once — USB or download; data is
   preserved; both installers use the same app data locations).
3. From v8.16.0 on, every future release is a 1-click auto-update.

The updater keypair prepared in v8.14 means clients compiled TODAY already
trust the key you will use for years — no re-installs caused by key changes.
