// BillBook Tauri Shell — main entry point
// Spawns the Python sidecar, waits for health check, opens webview.
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
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// v8.15.2 FIX: the Manager trait provides AppHandle::get_webview_window
// (E0599 in the CI log: "no method named `get_webview_window` found").
use tauri::Manager;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

// v8.15.2 FIX: a bin crate must have a `main` function (E0601). The bare
// `pub fn run()` below is the lib-style entry used by mobile templates;
// we keep it (mobile_entry_point attribute needs it) but add the real
// entry point that calls it.
fn main() {
    run();
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
            let (mut rx, _child) = app
                .shell()
                .sidecar("billbook_sidecar")
                .expect("failed to spawn sidecar (is desktop/binaries/billbook_sidecar-<triple>.exe present? run scripts/build_sidecar.bat and re-run the build)")
                .spawn()
                .expect("failed to spawn sidecar process");

            // Listen for the health line: BILLBOOK_READY port=XXXX
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let tauri_plugin_shell::process::CommandEvent::Stdout(line) = event {
                        let line_str = String::from_utf8_lossy(&line);
                        if line_str.contains("BILLBOOK_READY") {
                            println!("[billbook] sidecar ready: {}", line_str.trim());
                            break;
                        }
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
                    tokio::time::sleep(std::time::Duration::from_secs(8)).await;
                    if let Err(e) = check_for_updates(&handle).await {
                        eprintln!("[billbook-updater] {e}");
                    }
                });
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running BillBook");
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

    let mut downloaded: u64 = 0;
    update
        .download_and_install(
            |chunk_length, content_length| {
                downloaded += chunk_length as u64;
                if let Some(total) = content_length {
                    println!("[billbook-updater] {downloaded}/{total} bytes");
                }
            },
            || println!("[billbook-updater] download finished; starting installer"),
        )
        .await?;

    // On Windows, download_and_install() exits the process (std::process::exit)
    // right after launching the NSIS installer, so this line is unreachable
    // there. The installer was launched with /P /R: passive UI + relaunch.
    // On macOS/Linux we restart explicitly.
    #[cfg(not(windows))]
    app.restart();
    Ok(())
}
