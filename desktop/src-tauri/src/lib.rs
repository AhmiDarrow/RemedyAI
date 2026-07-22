use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

struct ServerState {
    process: Mutex<Option<Child>>,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerState {
            process: Mutex::new(None),
        })
        .setup(|app| {
            let remedy_path = find_remedy();

            let child = Command::new(&remedy_path)
                .args(["serve", "--host", "127.0.0.1", "--port", "8000"])
                .spawn()
                .ok();

            if let Some(c) = child {
                let state = app.state::<ServerState>();
                *state.process.lock().unwrap() = Some(c);
            }

            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                let state = window.state::<ServerState>();
                if let Ok(mut guard) = state.process.lock() {
                    if let Some(ref mut child) = *guard {
                        let _ = child.kill();
                    }
                }; // drop guard before state
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn find_remedy() -> String {
    // Try project venv first
    let candidates = [
        "../.venv/Scripts/remedy.exe",
        "../.venv/Scripts/python.exe",
        "remedy",
        "python",
    ];

    for c in &candidates {
        if Command::new(c).arg("--version").output().is_ok() {
            return c.to_string();
        }
    }

    "python".to_string()
}
