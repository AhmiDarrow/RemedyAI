use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, DragDropEvent, Emitter, Manager, State};

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
    process: Arc<Mutex<Option<Child>>>,
    /// Path to the sidecar binary discovered at startup.
    sidecar_cmd: Arc<Mutex<Option<String>>>,
    /// Files dropped from OS (Explorer). Frontend polls this because WebView
    /// event delivery is unreliable for drag-drop on Windows.
    pending_drops: Arc<Mutex<Vec<DroppedFilePayload>>>,
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

fn start_sidecar(process: &Arc<Mutex<Option<Child>>>, cmd: &str) -> Result<(), String> {
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

// ---------------------------------------------------------------------------
// In-app update (Ollama-style): check → download progress UI → install → relaunch
// ---------------------------------------------------------------------------

#[derive(serde::Serialize, Clone)]
struct DesktopUpdateInfo {
    current_version: String,
    latest_version: String,
    update_available: bool,
    download_url: Option<String>,
    release_notes: Option<String>,
    error: Option<String>,
}

#[derive(serde::Serialize, Clone)]
struct UpdateProgress {
    phase: String,
    percent: u8,
    message: String,
}

fn app_version(app: &AppHandle) -> String {
    app.package_info().version.to_string()
}

fn parse_semver(raw: &str) -> (u64, u64, u64) {
    let s = raw.trim().trim_start_matches('v').trim_start_matches('V');
    let mut parts = s.split(|c| c == '.' || c == '-' || c == '+');
    let major = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    let minor = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    let patch = parts.next().and_then(|p| p.parse().ok()).unwrap_or(0);
    (major, minor, patch)
}

fn is_newer(latest: &str, current: &str) -> bool {
    parse_semver(latest) > parse_semver(current)
}

/// Fetch latest desktop release metadata. Tries multiple sources; never fails
/// the whole check because the first URL errored (common with redirects / rate limits).
fn fetch_latest_desktop() -> Result<(String, Option<String>, Option<String>), String> {
    // Prefer Tauri latest.json (has platform installer URL + signature).
    let urls = [
        "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json",
        "https://api.github.com/repos/AhmiDarrow/RemedyAI/releases/latest",
    ];
    let mut errors: Vec<String> = Vec::new();

    for url in urls {
        let resp = match ureq::get(url)
            .set("User-Agent", "RemedyDesktop-Updater/0.10")
            .set("Accept", "application/json")
            .timeout(Duration::from_secs(15))
            .call()
        {
            Ok(r) => r,
            Err(e) => {
                errors.push(format!("{url}: {e}"));
                continue;
            }
        };
        let status = resp.status();
        if status != 200 {
            errors.push(format!("{url}: HTTP {status}"));
            continue;
        }
        let v: serde_json::Value = match resp.into_json() {
            Ok(v) => v,
            Err(e) => {
                errors.push(format!("{url}: invalid JSON ({e})"));
                continue;
            }
        };

        // latest.json shape
        if let Some(ver) = v.get("version").and_then(|x| x.as_str()) {
            let download = v
                .pointer("/platforms/windows-x86_64/url")
                .and_then(|x| x.as_str())
                .or_else(|| v.get("url").and_then(|x| x.as_str()))
                .map(|s| s.to_string());
            let notes = v
                .get("notes")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string());
            return Ok((ver.to_string(), download, notes));
        }

        // GitHub API shape
        if let Some(tag) = v.get("tag_name").and_then(|x| x.as_str()) {
            let notes = v
                .get("body")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string());
            let mut download = None;
            if let Some(assets) = v.get("assets").and_then(|a| a.as_array()) {
                for a in assets {
                    let name = a.get("name").and_then(|n| n.as_str()).unwrap_or("");
                    let asset_url = a
                        .get("browser_download_url")
                        .and_then(|u| u.as_str())
                        .unwrap_or("");
                    let lower = name.to_lowercase();
                    if name.ends_with("-setup.exe")
                        || name.ends_with("_x64-setup.exe")
                        || (name.ends_with(".exe")
                            && (lower.contains("setup") || lower.contains("remedy")))
                    {
                        download = Some(asset_url.to_string());
                        break;
                    }
                }
            }
            return Ok((tag.to_string(), download, notes));
        }

        errors.push(format!("{url}: unrecognized update metadata shape"));
    }

    Err(if errors.is_empty() {
        "Could not reach GitHub releases for update metadata".into()
    } else {
        format!("Update check failed: {}", errors.join(" | "))
    })
}

fn desktop_update_result(current: String) -> DesktopUpdateInfo {
    match fetch_latest_desktop() {
        Ok((latest, download_url, notes)) => {
            let available = is_newer(&latest, &current);
            DesktopUpdateInfo {
                current_version: current,
                latest_version: latest
                    .trim()
                    .trim_start_matches('v')
                    .trim_start_matches('V')
                    .to_string(),
                update_available: available,
                download_url,
                release_notes: notes,
                error: None,
            }
        }
        Err(e) => DesktopUpdateInfo {
            current_version: current.clone(),
            latest_version: current,
            update_available: false,
            download_url: None,
            release_notes: None,
            error: Some(e),
        },
    }
}

/// Non-blocking update check (network I/O off the UI thread).
#[tauri::command]
async fn check_desktop_update(app: AppHandle) -> Result<DesktopUpdateInfo, String> {
    let current = app_version(&app);
    tauri::async_runtime::spawn_blocking(move || desktop_update_result(current))
        .await
        .map_err(|e| format!("Update check task failed: {e}"))
}

fn emit_progress(app: &AppHandle, phase: &str, percent: u8, message: &str) {
    let _ = app.emit(
        "update-progress",
        UpdateProgress {
            phase: phase.to_string(),
            percent,
            message: message.to_string(),
        },
    );
}

/// Download the NSIS installer, launch it, then exit so files can be replaced.
/// Progress is streamed to the UI via `update-progress` events.
#[tauri::command]
fn start_desktop_update(app: AppHandle, download_url: String) -> Result<(), String> {
    if download_url.is_empty() {
        return Err("No download URL for this release".into());
    }
    if !(download_url.starts_with("https://github.com/")
        || download_url.starts_with("https://objects.githubusercontent.com/")
        || download_url.starts_with("https://release-assets.githubusercontent.com/"))
    {
        return Err("Download URL is not a trusted GitHub release host".into());
    }

    let app_for_thread = app.clone();
    // Clone Arc before spawn — State<'_, T> cannot be borrowed inside the worker.
    let process_slot = app.state::<ServerState>().process.clone();
    thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            emit_progress(&app_for_thread, "downloading", 0, "Connecting to update server…");

            let resp = ureq::get(&download_url)
                .set("User-Agent", "RemedyDesktop-Updater")
                .call()
                .map_err(|e| format!("Download failed: {e}"))?;
            if resp.status() != 200 {
                return Err(format!("Download HTTP {}", resp.status()));
            }

            let len = resp
                .header("Content-Length")
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);

            let temp = env::temp_dir().join(format!(
                "RemedyDesktop-Update-{}.exe",
                Instant::now().elapsed().as_millis()
            ));
            let mut file = std::fs::File::create(&temp)
                .map_err(|e| format!("Cannot create temp installer: {e}"))?;

            let mut reader = resp.into_reader();
            let mut buf = [0u8; 64 * 1024];
            let mut done: u64 = 0;
            loop {
                let n = reader
                    .read(&mut buf)
                    .map_err(|e| format!("Download interrupted: {e}"))?;
                if n == 0 {
                    break;
                }
                file.write_all(&buf[..n])
                    .map_err(|e| format!("Write failed: {e}"))?;
                done += n as u64;
                let pct = if len > 0 {
                    ((done * 100) / len).min(99) as u8
                } else {
                    // indeterminate-ish progress
                    ((done / (512 * 1024)) % 90) as u8
                };
                let mb = done as f64 / (1024.0 * 1024.0);
                emit_progress(
                    &app_for_thread,
                    "downloading",
                    pct,
                    &format!("Downloading update… {mb:.1} MB"),
                );
            }
            drop(file);

            emit_progress(
                &app_for_thread,
                "installing",
                100,
                "Starting installer… Remedy will close and relaunch.",
            );

            // Stop sidecar so installer can replace files.
            match process_slot.lock() {
                Ok(mut guard) => kill_child(&mut guard),
                Err(poisoned) => {
                    let mut guard = poisoned.into_inner();
                    kill_child(&mut guard);
                }
            }

            // Launch NSIS installer. PassivePassive shows a simple progress UI;
            // without it, /S is fully silent. Prefer visible progress for UX.
            #[cfg(target_os = "windows")]
            {
                use std::os::windows::process::CommandExt;
                Command::new(&temp)
                    .args(["/PASSIVE"])
                    .creation_flags(0) // show installer window
                    .spawn()
                    .map_err(|e| format!("Failed to launch installer: {e}"))?;
            }
            #[cfg(not(target_os = "windows"))]
            {
                Command::new(&temp)
                    .spawn()
                    .map_err(|e| format!("Failed to launch installer: {e}"))?;
            }

            // Give the installer a moment to start, then exit.
            thread::sleep(Duration::from_millis(800));
            emit_progress(
                &app_for_thread,
                "relaunch",
                100,
                "Installer running — closing Remedy…",
            );
            app_for_thread.exit(0);
            Ok(())
        })();

        if let Err(e) = result {
            log::error!("Update failed: {}", e);
            emit_progress(&app_for_thread, "error", 0, &e);
        }
    });

    Ok(())
}

// ---------------------------------------------------------------------------
// Native file drag-drop (WebView2 often blocks HTML5 File drops from Explorer)
// ---------------------------------------------------------------------------

const MAX_DROP_FILE_BYTES: u64 = 15 * 1024 * 1024;

#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
#[serde(rename_all = "snake_case")]
struct DroppedFilePayload {
    filename: String,
    content_type: String,
    data_base64: String,
    size: u64,
}

fn guess_content_type(path: &Path) -> String {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "svg" => "image/svg+xml",
        "txt" | "log" | "md" | "csv" => "text/plain",
        "json" => "application/json",
        "pdf" => "application/pdf",
        "py" => "text/x-python",
        "ts" | "tsx" => "text/typescript",
        "js" | "jsx" => "text/javascript",
        "html" | "htm" => "text/html",
        "css" => "text/css",
        "toml" | "yaml" | "yml" | "xml" => "text/plain",
        _ => "application/octet-stream",
    }
    .to_string()
}

fn load_paths_as_payloads(paths: &[String]) -> Result<Vec<DroppedFilePayload>, String> {
    use base64::Engine;

    let mut out = Vec::new();
    for raw in paths {
        let path = PathBuf::from(raw);
        if !path.is_file() {
            continue;
        }
        let meta = std::fs::metadata(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        if meta.len() > MAX_DROP_FILE_BYTES {
            return Err(format!(
                "{} is too large (max {} MB)",
                path.file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("file"),
                MAX_DROP_FILE_BYTES / (1024 * 1024)
            ));
        }
        let bytes = std::fs::read(&path).map_err(|e| format!("Read {}: {e}", path.display()))?;
        let filename = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("file")
            .to_string();
        let content_type = guess_content_type(&path);
        let data_base64 = base64::engine::general_purpose::STANDARD.encode(&bytes);
        out.push(DroppedFilePayload {
            filename,
            content_type,
            data_base64,
            size: bytes.len() as u64,
        });
        if out.len() >= 12 {
            break;
        }
    }
    if out.is_empty() {
        return Err("No readable files in drop".into());
    }
    Ok(out)
}

/// Read OS-dropped file paths into base64 payloads for the web UI to upload.
#[tauri::command]
fn read_dropped_files(paths: Vec<String>) -> Result<Vec<DroppedFilePayload>, String> {
    load_paths_as_payloads(&paths)
}

/// Drain files captured by the last native OS drop (reliable path for the UI).
#[tauri::command]
fn take_pending_file_drops(
    state: State<'_, ServerState>,
) -> Result<Vec<DroppedFilePayload>, String> {
    let mut guard = state
        .pending_drops
        .lock()
        .map_err(|_| "pending drops lock poisoned".to_string())?;
    if guard.is_empty() {
        return Ok(vec![]);
    }
    let items = std::mem::take(&mut *guard);
    log::info!("UI took {} pending dropped file(s)", items.len());
    Ok(items)
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
            process: Arc::new(Mutex::new(None)),
            sidecar_cmd: Arc::new(Mutex::new(None)),
            pending_drops: Arc::new(Mutex::new(Vec::new())),
        })
        .invoke_handler(tauri::generate_handler![
            open_data_folder,
            restart_server,
            check_desktop_update,
            start_desktop_update,
            read_dropped_files,
            take_pending_file_drops
        ])
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
            match event {
                tauri::WindowEvent::Destroyed => {
                    let state = window.state::<ServerState>();
                    let mut guard = state.process.lock().unwrap();
                    kill_child(&mut guard);
                }
                // Native OS file drops (Explorer → app). WebView2 often won't
                // deliver HTML5 DataTransfer.files for external drops.
                tauri::WindowEvent::DragDrop(DragDropEvent::Enter { paths, .. }) => {
                    let paths: Vec<String> = paths
                        .iter()
                        .map(|p| p.to_string_lossy().into_owned())
                        .collect();
                    let _ = window.emit("file-drag", serde_json::json!({ "phase": "enter", "paths": paths }));
                }
                tauri::WindowEvent::DragDrop(DragDropEvent::Over { .. }) => {
                    let _ = window.emit("file-drag", serde_json::json!({ "phase": "over" }));
                }
                tauri::WindowEvent::DragDrop(DragDropEvent::Leave) => {
                    let _ = window.emit("file-drag", serde_json::json!({ "phase": "leave" }));
                }
                tauri::WindowEvent::DragDrop(DragDropEvent::Drop { paths, .. }) => {
                    let path_strs: Vec<String> = paths
                        .iter()
                        .map(|p| p.to_string_lossy().into_owned())
                        .collect();
                    log::info!("Native file drop: {} path(s)", path_strs.len());
                    match load_paths_as_payloads(&path_strs) {
                        Ok(payloads) => {
                            log::info!(
                                "Read {} dropped file(s) for composer",
                                payloads.len()
                            );
                            // Queue for polling (primary — WebView event delivery is flaky).
                            {
                                let pending = window.state::<ServerState>().pending_drops.clone();
                                let mut q = pending.lock().unwrap_or_else(|e| e.into_inner());
                                q.extend(payloads.clone());
                                drop(q);
                            }
                            // Also emit for listeners that work.
                            let _ = window.emit("file-drop-ready", &payloads);
                            let _ = window.app_handle().emit("file-drop-ready", &payloads);
                        }
                        Err(e) => {
                            log::error!("Failed to read dropped files: {}", e);
                            let _ = window.emit(
                                "file-drop-error",
                                serde_json::json!({ "message": e }),
                            );
                            let _ = window.app_handle().emit(
                                "file-drop-error",
                                serde_json::json!({ "message": e }),
                            );
                        }
                    }
                }
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
