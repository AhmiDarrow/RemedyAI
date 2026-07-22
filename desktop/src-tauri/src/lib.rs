use std::env;
use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct ServerState {
    process: Mutex<Option<Child>>,
}

fn current_exe_dir() -> Option<std::path::PathBuf> {
    env::current_exe().ok()?.parent().map(|p| p.to_path_buf())
}

fn find_remedy() -> String {
    if let Some(dir) = current_exe_dir() {
        // Tauri externalBin convention: name-target_triple.exe
        let triple_name = dir.join("remedy-desktop-x86_64-pc-windows-msvc.exe");
        if triple_name.exists() {
            log::info!("Found sidecar at: {}", triple_name.display());
            return triple_name.to_string_lossy().to_string();
        }
        let plain_name = dir.join("remedy-desktop.exe");
        if plain_name.exists() {
            log::info!("Found sidecar at: {}", plain_name.display());
            return plain_name.to_string_lossy().to_string();
        }
    }
    // Fallback: look in PATH without running --version (avoid 2s delay)
    log::info!("Sidecar not found in app directory, trying PATH");
    "remedy-desktop.exe".to_string()
}

fn spawn_remedy(cmd: &str) -> Option<Child> {
    let args = ["serve", "--host", "127.0.0.1", "--port", "8000"];

    #[cfg(target_os = "windows")]
    {
        Command::new(cmd)
            .args(&args)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .ok()
    }
    #[cfg(not(target_os = "windows"))]
    {
        Command::new(cmd).args(&args).spawn().ok()
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerState {
            process: Mutex::new(None),
        })
        .setup(|app| {
            let _shell = app.handle().plugin(tauri_plugin_shell::init())?;
            let app_handle = app.handle().clone();

            let remedy_cmd = find_remedy();
            log::info!("Starting remedy: {}", remedy_cmd);
            let _ = app_handle.emit("server-starting", ());

            let child = spawn_remedy(&remedy_cmd);

            if let Some(c) = child {
                let state = app.state::<ServerState>();
                *state.process.lock().unwrap() = Some(c);

                let started = Instant::now();
                let max_wait = Duration::from_secs(30);
                let mut backoff = Duration::from_millis(250);
                let mut server_ready = false;

                while started.elapsed() < max_wait {
                    match TcpStream::connect_timeout(
                        &"127.0.0.1:8000".parse().unwrap(),
                        Duration::from_millis(500),
                    ) {
                        Ok(_) => {
                            server_ready = true;
                            break;
                        }
                        Err(_) => {
                            thread::sleep(backoff);
                            backoff = (backoff * 2).min(Duration::from_secs(2));
                        }
                    }
                }

                if server_ready {
                    log::info!("Remedy server ready in {:.1}s", started.elapsed().as_secs_f32());
                    let _ = app_handle.emit("server-ready", ());
                } else {
                    log::error!("Server failed to start within {}s", max_wait.as_secs());
                    let _ = app_handle.emit("server-error", "Server failed to start after 30s");
                }
            } else {
                log::error!("Failed to spawn remedy process: {}", remedy_cmd);
                let _ = app_handle.emit("server-error", "Failed to start remedy process");
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
                let lock_result = state.process.lock();
                if let Ok(mut guard) = lock_result {
                    if let Some(ref mut child) = *guard {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
