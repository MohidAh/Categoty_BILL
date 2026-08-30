// BillBook Tauri Shell — main entry point
// Spawns the Python sidecar, waits for health check, opens webview.
//
// v8.17.10 HARDENING (after "installed it, run it, nothing happens"):
//   1. FILE LOG — every step (and every Python stdout/stderr line) is appended
//      to %APPDATA%\com.billbook.app\logs\desktop.log so a failed start is
//      never silent again. Release builds have no console (see windows_subsystem
//      below), so println! alone was invisible in exactly the situations we
//      need it most.
//   2. VISIBLE FAILURE DIALOGS — if the backend doesn't signal ready within
//      90s, or dies during startup/mid-session, a native dialog says so
//      (instead of a silent exit).
//   3. WINDOW NAVIGATION FIX — the bundled frontend uses relative URLs
//      (fetch('/api/...')). Loaded from the bundled static assets the page
//      origin is tauri.localhost, so every API call would hit the wrong
//      origin and fail. Now, as soon as the sidecar prints
//      "BILLBOOK_READY port=NNNN", the main window is navigated to
//      http://127.0.0.1:NNNN (the FastAPI server that also serves the UI —
//      same as dev mode). This ALSO fixes the latent bug where the sidecar
//      picked port 8001+ when 8000 was busy but the UI assumed 8000.
//
// v8.15.1: auto-update support (Tauri v2 updater plugin, Rust-side check).
//   - ~8s after startup the app asks the update endpoint for a newer version.
//   - If one exists, a native dialog asks the user to confirm.
//   - The downloaded bundle's Ed25519 signature is verified against the
//     pubkey compiled into tauri.conf.json BEFORE anything is installed.
//   - The NSIS installer runs in "passive" mode (progress bar, no clicks)
//     and relaunches BillBook when it finishes (restart_after_install).
//
// See build/windows/TAURI_AUTO_UPDATE_GUIDE.md for the full walkthrough.

// v8.15.2 FIX: standard Tauri attribute - hides the black console window
// in release builds. println!/eprintln! logs then only appear in debug
// builds (a POS should never flash a cmd.exe window at shop staff).
// Downside: release crashes are silent — which is why v8.17.10 adds the
// file log + failure dialogs above.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// v8.15.2 FIX: the Manager trait provides AppHandle::get_webview_window
// (E0599 in the CI log: "no method named `get_webview_window` found").
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;
use std::time::Duration;

// v8.15.2 FIX: a bin crate must have a `main` function (E0601). The bare
// `pub fn run()` below is the lib-style entry used by mobile templates;
// we keep it (mobile_entry_point attribute needs it) but add the real
// entry point that calls it.
// v8.17.14 SIDE CAR LIFECYCLE FIX: the sidecar is a child process of
// BillBook.exe, and Windows does NOT kill a process tree when the parent
// exits. Two consequences, both fixed here:
//   1. The auto-updater exits the app with a hard std::process::exit()
//      right after launching the NSIS installer - the orphaned sidecar
//      kept running, LOCKED billbook_sidecar.exe, and the installer
//      failed with "Error opening file for writing" (v8.17.13 incident).
//   2. Every NORMAL app close also leaked a zombie backend process
//      (the mysterious port-8000 conflicts).
//
// v8.18.2 ZOMBIE SWEEP + UPDATE REORDER: killing OUR OWN child is not
// enough — machines that ran pre-v8.17.14 builds still carry sidecar
// zombies from those versions (they hold port 8000 AND lock the exe,
// which is why updates STILL failed with "Error opening file for
// writing" even after v8.17.14). Fixes:
//   - On startup: taskkill any leftover billbook_sidecar.exe before we
//     spawn our own (also guarantees port 8000 is free for us).
//   - Updater: DOWNLOAD first (POS stays usable), THEN kill the sidecar
//     (child handle + name-based tree kill + short lock-release wait),
//     THEN run the installer. If the install fails we restart the app
//     so the shop is never left with a dead backend.
//   - The NSIS installer itself also sweeps (desktop/installer-hooks.nsh)
//     so even updates launched by OLD app versions can't hit the lock.
static SIDECAR_CHILD: OnceLock<Mutex<Option<CommandChild>>> = OnceLock::new();

/// True while the auto-updater is replacing the app: the sidecar's death
/// is then EXPECTED and must not trigger the "backend stopped" dialog.
static UPDATING: AtomicBool = AtomicBool::new(false);

fn sidecar_child() -> &'static Mutex<Option<CommandChild>> {
    SIDECAR_CHILD.get_or_init(|| Mutex::new(None))
}

/// Terminate the sidecar (no-op if already gone). Called before the
/// updater launches the installer, and on normal app exit.
fn kill_sidecar() -> bool {
    sidecar_child()
        .lock()
        .ok()
        .and_then(|mut guard| guard.take())
        .map(|child| child.kill().is_ok())
        .unwrap_or(false)
}

/// v8.18.2: force-kill EVERY billbook_sidecar.exe process tree by NAME,
/// not just our own child handle. Covers:
///   - zombie sidecars leaked by pre-v8.17.14 builds (they lock the exe
///     file — the "retry" update failures — and hog port 8000)
///   - PyInstaller onefile children orphaned when only the bootloader
///     parent is killed (/T takes down the whole descendant chain)
/// Fails silently when no such process exists (taskkill exit code 128).
/// CREATE_NO_WINDOW: taskkill is a console app and must never flash a
/// black cmd window at shop staff.
#[cfg(windows)]
fn taskkill_sidecar_tree() -> bool {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    match std::process::Command::new("taskkill")
        .args(["/F", "/T", "/IM", "billbook_sidecar.exe"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
    {
        Ok(out) => out.status.success(),
        Err(_) => false,
    }
}

fn main() {
    install_panic_hook();
    run();
}

fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        let msg = format!("[PANIC] {info}");
        eprintln!("[billbook] {msg}");
        let Ok(appdata) = std::env::var("APPDATA") else { return };
        let logs = std::path::Path::new(&appdata)
            .join("com.billbook.app")
            .join("logs");
        let _ = std::fs::create_dir_all(&logs);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(logs.join("desktop.log"))
        {
            use std::io::Write;
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = f.write_all(format!("[{ts}] {msg}\n").as_bytes());
        }
    }));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // Focus existing window when second instance launched
            let _ = app.get_webview_window("main").map(|w| w.set_focus());
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--minimized"]),
        ))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // --- Spawn the sidecar binary ------------------------------------
            // v8.18.2: first sweep away any zombie sidecar left behind by
            // pre-v8.17.14 builds (they leaked one on every app close). A
            // zombie holds port 8000 — forcing us onto 8001+, which broke
            // the Google OAuth redirect — and locks billbook_sidecar.exe,
            // which is what makes updates fail with "Error opening file
            // for writing". Kill it before spawning our own.
            #[cfg(windows)]
            if taskkill_sidecar_tree() {
                log(app.handle(), "cleared a leftover sidecar process before starting");
            }
            // v8.17.10: no more .expect() panics — in a release build (no
            // console) a panic is a completely silent death. Log to file
            // and exit with a written trace instead.
            let command = match app.shell().sidecar("billbook_sidecar") {
                Ok(c) => c,
                Err(e) => {
                    log(app.handle(), &format!("sidecar resolve failed: {e}"));
                    log(app.handle(),
                        "hint: is billbook_sidecar.exe present next to BillBook.exe? \
                         If antivirus quarantined it, this is the moment it fails.");
                    std::process::exit(1);
                }
            };
            let (mut rx, child) = match command.spawn() {
                Ok(v) => v,
                Err(e) => {
                    log(app.handle(), &format!("sidecar spawn failed: {e}"));
                    log(app.handle(),
                        "hint: antivirus real-time protection blocking the Python \
                         sidecar produces exactly this silent failure.");
                    std::process::exit(1);
                }
            };
            // v8.17.14: remember the child so kill_sidecar() can reach it.
            if let Ok(mut guard) = sidecar_child().lock() {
                *guard = Some(child);
            }
            log(app.handle(), "sidecar spawned; waiting for BILLBOOK_READY");

            // --- Health listener + window navigation -------------------------
            // v8.17.10: was "print the ready line and stop". Now it:
            //   - logs ALL backend output (stdout + stderr + IO errors) to
            //     the log file — a Python traceback is captured verbatim
            //   - parses the port and navigates the main window to the
            //     live backend (fixes relative /api/ URLs in release mode)
            //   - before ready: 90s timeout -> dialog; channel closed or
            //     process terminated -> dialog
            //   - after ready: keeps logging; termination -> dialog
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut ready = false;
                loop {
                    let evt = if ready {
                        rx.recv().await
                    } else {
                        match tokio::time::timeout(Duration::from_secs(90), rx.recv()).await {
                            Ok(v) => v,
                            Err(_) => {
                                fatal_dialog(
                                    &handle,
                                    "BillBook's backend did not start within 90 seconds \
                                     (it may be blocked by antivirus, or the disk is very \
                                     slow on first launch).",
                                );
                                return;
                            }
                        }
                    };

                    let Some(event) = evt else {
                        // Stream closed without a Terminated event (rare)
                        if UPDATING.load(Ordering::SeqCst) {
                            return; // expected: updater stopped the sidecar
                        }
                        if !ready {
                            fatal_dialog(
                                &handle,
                                "BillBook's backend exited during startup before it \
                                 became ready.",
                            );
                        }
                        return;
                    };

                    match event {
                        CommandEvent::Stdout(line) => {
                            let s = String::from_utf8_lossy(&line);
                            log(&handle, &format!("[sidecar] {}", s.trim_end()));
                            if let Some(port) = parse_ready_port(&s) {
                                ready = true;
                                log(&handle, &format!("backend ready on port {port}"));
                                navigate_when_ready(&handle, port).await;
                            }
                        }
                        CommandEvent::Stderr(line) => {
                            // Python tracebacks land here — the goldmine for
                            // diagnosing startup crashes.
                            log(&handle, &format!("[sidecar:err] {}", String::from_utf8_lossy(&line).trim_end()));
                        }
                        CommandEvent::Error(e) => {
                            log(&handle, &format!("[sidecar:io] {e}"));
                        }
                        CommandEvent::Terminated(status) => {
                            log(&handle, &format!("backend process terminated: {status:?}"));
                            if UPDATING.load(Ordering::SeqCst) {
                                log(&handle, "sidecar exit expected - update in progress");
                                return;
                            }
                            if ready {
                                fatal_dialog(
                                    &handle,
                                    "BillBook's backend stopped unexpectedly while \
                                     running. Please restart BillBook.",
                                );
                            } else {
                                fatal_dialog(
                                    &handle,
                                    "BillBook's backend exited during startup. The Python \
                                     error is captured in the log file below.",
                                );
                            }
                            return;
                        }
                        _ => {}
                    }
                }
            });

            // --- Auto-update check (desktop only) ----------------------------
            #[cfg(desktop)]
            {
                // Register the updater plugin (desktop platforms only; the
                // updater is not supported on mobile).
                app.handle()
                    .plugin(tauri_plugin_updater::Builder::new().build())?;

                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    // Give the app (and the sidecar) time to settle before
                    // phoning the update server. Errors are logged, never
                    // fatal: an unreachable endpoint must not stop the POS.
                    tokio::time::sleep(Duration::from_secs(8)).await;
                    if let Err(e) = check_for_updates(&handle).await {
                        eprintln!("[billbook-updater] {e}");
                    }
                });
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running BillBook")
        // v8.17.14: on any normal exit (window closed / OS shutdown) take
        // the sidecar down with us. RunEvent::Exit is NOT emitted for the
        // updater's std::process::exit - that path calls kill_sidecar()
        // explicitly above.
        // v8.18.2: the name-based tree sweep makes this airtight — no
        // orphaned backend can outlive the app on any exit path.
        .run(|_app, event| {
            if let tauri::RunEvent::Exit = event {
                let _ = kill_sidecar();
                #[cfg(windows)]
                taskkill_sidecar_tree();
            }
        });
}

/// Append a timestamped line to %APPDATA%\com.billbook.app\logs\desktop.log
/// (identifier comes from tauri.conf.json). Also eprintln for dev builds,
/// where a console exists.
fn log(app: &tauri::AppHandle, msg: &str) {
    eprintln!("[billbook] {msg}");
    let Ok(dir) = app.path().app_data_dir() else { return };
    let logs = dir.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("desktop.log"))
    {
        use std::io::Write;
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let _ = f.write_all(format!("[{ts}] {msg}\n").as_bytes());
    }
}

/// Show a blocking error dialog (safe here: called from async worker
/// threads, NOT the main thread) and log the same message first.
fn fatal_dialog(app: &tauri::AppHandle, msg: &str) {
    log(app, &format!("FATAL: {msg}"));
    let _ = app
        .dialog()
        .message(format!(
            "{msg}\n\nTechnical details are saved in:\n%APPDATA%\\com.billbook.app\\logs\\desktop.log"
        ))
        .title("BillBook Problem")
        .buttons(MessageDialogButtons::OkCustom("Close".into()))
        .blocking_show();
}

/// Parse "BILLBOOK_READY port=8000" -> 8000. Defaults to 8000 when the
/// port token is missing.
fn parse_ready_port(line: &str) -> Option<u16> {
    if !line.contains("BILLBOOK_READY") {
        return None;
    }
    line.split_whitespace()
        .find(|t| t.starts_with("port="))
        .and_then(|t| t[5..].parse::<u16>().ok())
        .or(Some(8000))
}

/// Point the main webview window at the live backend. The window may not
/// exist yet for a short moment after setup returns (creation order races),
/// so retry a few times before giving up.
async fn navigate_when_ready(app: &tauri::AppHandle, port: u16) {
    let url = format!("http://127.0.0.1:{port}");
    for attempt in 1..=10u8 {
        if let Some(w) = app.get_webview_window("main") {
            match w.eval(&format!("window.location.replace('{url}')")) {
                Ok(()) => {
                    log(app, &format!("main window navigated to {url}"));
                    return;
                }
                Err(e) => log(app, &format!("navigate eval failed (attempt {attempt}): {e}")),
            }
        } else {
            log(app, &format!("main window not created yet (attempt {attempt})"));
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
    log(app, "could not navigate main window after 10 attempts");
}

/// Ask the update endpoint for a newer version; if one exists, confirm with
/// the user, download it and hand off to the installer.
///
/// Why Rust-side instead of JS: the BillBook frontend is plain HTML/JS with
/// no bundler, and the check must work even if the webview never loads. All
/// errors are swallowed deliberately — a POS must always start.
#[cfg(desktop)]
async fn check_for_updates(app: &tauri::AppHandle) -> tauri_plugin_updater::Result<()> {
    // Reads endpoints + pubkey from tauri.conf.json (or the --config override).
    let Some(update) = app.updater()?.check().await? else {
        println!("[billbook-updater] no update available (or endpoint unreachable)");
        return Ok(());
    };

    println!(
        "[billbook-updater] update available: v{} (current v{})",
        update.version, update.current_version
    );

    // Native confirm dialog. blocking_show() panics on the main thread; we
    // are on an async-runtime worker thread here, so it is safe.
    let install = app
        .dialog()
        .message(format!(
            "BillBook v{} is available.\n\nInstall the update now? The app will restart automatically when finished.",
            update.version
        ))
        .title("BillBook Update")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Update now".into(),
            "Later".into(),
        ))
        .blocking_show();

    if !install {
        println!("[billbook-updater] user postponed the update");
        return Ok(());
    }

    // v8.18.2 REORDERED: download FIRST while the app (and the POS) stay
    // fully usable — previously the sidecar was killed before a download
    // that could take minutes, leaving a frozen-looking shop terminal.
    // v8.18.4 FIX: tauri-plugin-updater 2.10 split download()/install() —
    // install() now takes the downloaded bytes and is NOT async. The old
    // `update.install().await` form no longer compiles (E0061 + E0277).
    let mut downloaded: u64 = 0;
    let installer_bytes = update
        .download(
            |chunk_length, content_length| {
                downloaded += chunk_length as u64;
                if let Some(total) = content_length {
                    println!("[billbook-updater] {downloaded}/{total} bytes");
                }
            },
            || println!("[billbook-updater] download finished"),
        )
        .await?;

    // v8.17.14 + v8.18.2: stop the sidecar BEFORE the installer runs,
    // in three layers: (1) our child handle, (2) a name-based tree kill
    // that also catches zombies leaked by pre-v8.17.14 builds and any
    // orphaned onefile children, (3) a short wait so Windows actually
    // releases the exe file lock before NSIS opens it for writing.
    UPDATING.store(true, Ordering::SeqCst);
    let _ = kill_sidecar();
    #[cfg(windows)]
    {
        taskkill_sidecar_tree();
        std::thread::sleep(Duration::from_millis(500));
    }
    println!("[billbook-updater] sidecar stopped; launching installer");

    // On Windows, install() launches the NSIS installer and exits the
    // process right after, so anything below it is unreachable there.
    // If launching the installer FAILS, we restart the app: the sidecar
    // is already dead at this point and the shop must not be left with
    // a dead backend — the update is simply offered again on next start.
    if let Err(e) = update.install(installer_bytes) {
        println!("[billbook-updater] install failed: {e}");
        app.restart();
    }

    // macOS/Linux: install() replaced the app bundle in place — restart
    // into the new version explicitly.
    #[cfg(not(windows))]
    app.restart();
    Ok(())
}
