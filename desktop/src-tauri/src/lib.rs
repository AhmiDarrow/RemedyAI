use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, State};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// Sidecar / user-data home: `%USERPROFILE%\.remedy` on Windows, `~/.remedy` elsewhere.
fn remedy_home() -> PathBuf {
    let home = if cfg!(target_os = "windows") {
        env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string())
    } else {
        env::var("HOME").unwrap_or_else(|_| ".".to_string())
    };
    PathBuf::from(home).join(".remedy")
}

fn status_addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 7400))
}

struct ServerState {
    process: Mutex<Option<Child>>,
    /// Path to the sidecar binary discovered at startup.
    sidecar_cmd: Mutex<Option<String>>,
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
        // From desktop/ when running tauri dev (cwd may be desktop/)
        let alt = cwd.join("desktop").join("bin").join("remedy-desktop.exe");
        if let Some(path) = searched("dev-desktop", &alt) {
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
    let home_dir = remedy_home();
    let home_str = home_dir.to_string_lossy();
    let args = [
        "--home",
        home_str.as_ref(),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "7400",
    ];

    #[cfg(target_os = "windows")]
    {
        Command::new(cmd)
            .args(args)
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .ok()
    }
    #[cfg(not(target_os = "windows"))]
    {
        Command::new(cmd)
            .args(args)
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
                    // Drop noisy uvicorn access lines (status polls / routine 200s).
                    let lower = text.to_ascii_lowercase();
                    if lower.contains("\"get /api/status")
                        || lower.contains("http/1.1\" 200")
                        || (lower.contains(" - \"get /api/") && lower.contains(" 200 "))
                    {
                        continue;
                    }
                    log::info!("[remedy {}] {}", label, text);
                }
                _ => {}
            }
        }
    });
}

fn check_health(timeout: Duration) -> bool {
    match TcpStream::connect_timeout(&status_addr(), timeout) {
        Ok(mut stream) => {
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .ok();
            let req = "GET /api/status HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
            if stream.write_all(req.as_bytes()).is_err() {
                return false;
            }
            let mut buf = Vec::with_capacity(1024);
            let mut chunk = [0u8; 512];
            loop {
                match stream.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => {
                        buf.extend_from_slice(&chunk[..n]);
                        if buf.len() >= 4096 {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
            if buf.is_empty() {
                return false;
            }
            let response = String::from_utf8_lossy(&buf);
            // Require both HTTP 200 and body status=ok (AND, not OR).
            let status_ok = response
                .lines()
                .next()
                .map(|line| line.contains(" 200 ") || line.contains("200 OK"))
                .unwrap_or(false);
            // Prefer structured check for {"status":"ok"...}
            let body_ok = response.contains("\"status\"")
                && (response.contains("\"ok\"") || response.contains("'ok'"));
            status_ok && body_ok
        }
        Err(_) => false,
    }
}

fn wait_for_health(max_wait: Duration) -> bool {
    let started = Instant::now();
    let mut backoff = Duration::from_millis(250);
    while started.elapsed() < max_wait {
        if check_health(Duration::from_millis(500)) {
            return true;
        }
        thread::sleep(backoff);
        backoff = (backoff * 2).min(Duration::from_secs(2));
    }
    false
}

fn kill_child(guard: &mut Option<Child>) {
    if let Some(ref mut child) = *guard {
        let _ = child.kill();
        let _ = child.wait();
    }
    *guard = None;
}

fn start_sidecar(process: &Mutex<Option<Child>>, cmd: &str) -> Result<(), String> {
    let mut guard = process
        .lock()
        .map_err(|_| "server state lock poisoned".to_string())?;
    kill_child(&mut guard);

    let mut child = spawn_remedy(cmd).ok_or_else(|| format!("Failed to spawn: {cmd}"))?;
    if let Some(stdout) = child.stdout.take() {
        forward_output("out", BufReader::new(stdout));
    }
    if let Some(stderr) = child.stderr.take() {
        forward_output("err", BufReader::new(stderr));
    }
    *guard = Some(child);
    Ok(())
}

/// Open the Remedy user-data folder in the OS file manager.
#[tauri::command]
fn open_data_folder() -> Result<String, String> {
    let dir = remedy_home();
    std::fs::create_dir_all(&dir).map_err(|e| format!("create data folder: {e}"))?;
    let path_str = dir.to_string_lossy().to_string();

    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .arg(&path_str)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&path_str)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {e}"))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&path_str)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {e}"))?;
    }

    Ok(path_str)
}

/// Kill and respawn the sidecar, wait for health, emit server-ready / server-error.
#[tauri::command]
fn restart_server(app: AppHandle, state: State<'_, ServerState>) -> Result<String, String> {
    let cmd = {
        let guard = state
            .sidecar_cmd
            .lock()
            .map_err(|_| "sidecar cmd lock poisoned".to_string())?;
        guard
            .clone()
            .ok_or_else(|| "Sidecar path unknown — restart the app".to_string())?
    };

    log::info!("Restarting remedy sidecar: {}", cmd);
    let _ = app.emit("server-starting", ());

    start_sidecar(&state.process, &cmd)?;

    if wait_for_health(Duration::from_secs(30)) {
        log::info!("Remedy server ready after restart");
        let _ = app.emit("server-ready", ());
        Ok("ready".into())
    } else {
        log::error!("Server failed to become ready after restart");
        let msg = "Server failed to start after 30s";
        let _ = app.emit("server-error", msg);
        Err(msg.into())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerState {
            process: Mutex::new(None),
            sidecar_cmd: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![open_data_folder, restart_server])
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

            {
                let state = app.state::<ServerState>();
                *state.sidecar_cmd.lock().unwrap() = Some(remedy_cmd.clone());
                match start_sidecar(&state.process, &remedy_cmd) {
                    Ok(()) => {
                        if wait_for_health(Duration::from_secs(30)) {
                            log::info!("Remedy server ready");
                            let _ = app_handle.emit("server-ready", ());
                        } else {
                            log::error!("Server failed to start within 30s");
                            let _ = app_handle
                                .emit("server-error", "Server failed to start after 30s");
                        }
                    }
                    Err(e) => {
                        log::error!("{}", e);
                        let _ = app_handle.emit("server-error", &e);
                    }
                }
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
                let mut guard = state.process.lock().unwrap();
                kill_child(&mut guard);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
