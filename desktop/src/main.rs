// BillBook Tauri Shell — main entry point
// Spawns the Python sidecar, waits for health check, opens webview.

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
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Spawn the sidecar binary
            let sidecar = tauri_plugin_shell::process::CommandEvent::Terminated;
            let (mut rx, _child) = app.shell().sidecar("billbook_sidecar")
                .expect("failed to spawn sidecar")
                .spawn()
                .expect("failed to spawn sidecar process");

            // Listen for the health line: BILLBOOK_READY port=XXXX
            tauri::async_runtime::spawn(async move {
                let mut port: u16 = 8000;
                while let Some(event) = rx.recv().await {
                    if let tauri_plugin_shell::process::CommandEvent::Stdout(line) = event {
                        let line_str = String::from_utf8_lossy(&line);
                        if line_str.contains("BILLBOOK_READY") {
                            // Parse port from "BILLBOOK_READY port=XXXX"
                            if let Some(port_str) = line_str.split("port=").nth(1) {
                                port = port_str.trim().parse().unwrap_or(8000);
                            }
                            // Navigate the webview to the sidecar URL
                            // (Done via emit to the frontend)
                            break;
                        }
                    }
                }
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running BillBook");
}
