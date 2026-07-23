use std::env;
use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
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

fn find_remedy() -> (String, String) {
    let searched = |label: &str, p: &std::path::Path| -> Option<String> {
        if p.exists() {
            log::info!("Found sidecar at: {} ({})", p.display(), label);
            Some(p.to_string_lossy().to_string())
        } else {
            None
        }
    };

    if let Some(dir) = current_exe_dir() {
        if let Some(path) = searched(
            "triple",
            &dir.join("remedy-desktop-x86_64-pc-windows-msvc.exe"),
        ) {
            return (path, String::new());
        }
        if let Some(path) = searched("plain", &dir.join("remedy-desktop.exe")) {
            return (path, String::new());
        }
    }

    if let Ok(cwd) = env::current_dir() {
        let dev_path = cwd.join("bin").join("remedy-desktop.exe");
        if let Some(path) = searched("dev", &dev_path) {
            return (path, String::new());
        }
    }

    let msg = format!(
        "Sidecar not found — checked exe dir {:?}, cwd/bin/",
        current_exe_dir()
    );
    log::error!("{}", msg);
    ("remedy-desktop.exe".to_string(), msg)
}

fn spawn_remedy(cmd: &str) -> Option<Child> {
    let home_dir = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE")
    } else {
        std::env::var("HOME")
    }
    .unwrap_or_else(|_| ".".to_string())
    .to_owned()
    + "\\.remedy";

    #[cfg(target_os = "windows")]
    {
        Command::new(cmd)
            .args([
                "--home",
                &home_dir,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "7400",
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .ok()
    }
    #[cfg(not(target_os = "windows"))]
    {
        Command::new(cmd)
            .args([
                "--home",
                &home_dir,
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                "7400",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .ok()
    }
}

fn forward_output(label: &str, reader: impl BufRead + Send + 'static) {
    let label = label.to_string();
    thread::spawn(move || {
        for line in reader.lines() {
            match line {
                Ok(text) if !text.is_empty() => {
                    log::info!("[remedy {}] {}", label, text);
                }
                _ => {}
            }
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerState {
            process: Mutex::new(None),
        })
        .setup(|app| {
            let _shell = app.handle().plugin(tauri_plugin_shell::init())?;
            let _updater = app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            let app_handle = app.handle().clone();

            let (remedy_cmd, find_err) = find_remedy();
            if !find_err.is_empty() {
                log::error!("{}", find_err);
                let _ = app_handle.emit("server-error", &find_err);
                return Ok(());
            }

            log::info!("Starting remedy: {}", remedy_cmd);
            let _ = app_handle.emit("server-starting", ());

            let child = spawn_remedy(&remedy_cmd);

            if let Some(mut c) = child {
                if let Some(stdout) = c.stdout.take() {
                    forward_output("out", BufReader::new(stdout));
                }
                if let Some(stderr) = c.stderr.take() {
                    forward_output("err", BufReader::new(stderr));
                }

                let state = app.state::<ServerState>();
                *state.process.lock().unwrap() = Some(c);

                let started = Instant::now();
                let max_wait = Duration::from_secs(30);
                let mut backoff = Duration::from_millis(250);
                let mut server_ready = false;

                while started.elapsed() < max_wait {
                    match TcpStream::connect_timeout(
                        &"127.0.0.1:7400".parse().unwrap(),
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
