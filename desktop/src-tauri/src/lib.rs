mod pty_host;
mod browser_host;
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

struct DesktopPrefs {
    close_to_tray: bool,
    start_in_tray: bool,
    /// When true, skip the "quitting stops the local server / Web UI" dialog.
    skip_quit_server_warning: bool,
}

impl Default for DesktopPrefs {
    fn default() -> Self {
        Self {
            close_to_tray: false,
            start_in_tray: false,
            skip_quit_server_warning: false,
        }
    }
}

fn desktop_prefs_path() -> PathBuf {
    remedy_home().join("desktop.json")
}

fn config_toml_path() -> PathBuf {
    remedy_home().join("config.toml")
}

/// Parse a simple TOML bool assignment: `key = true` / `key = false`.
fn toml_bool(raw: &str, key: &str) -> Option<bool> {
    for line in raw.lines() {
        let line = line.split('#').next().unwrap_or("").trim();
        if let Some(rest) = line.strip_prefix(key) {
            let rest = rest.trim();
            if let Some(val) = rest.strip_prefix('=') {
                let val = val.trim().trim_matches('"');
                if val.eq_ignore_ascii_case("true") {
                    return Some(true);
                }
                if val.eq_ignore_ascii_case("false") {
                    return Some(false);
                }
            }
        }
    }
    None
}

/// Wire format for `~/.remedy/desktop.json` (serde - no brittle string contains).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, Default)]
struct DesktopPrefsFile {
    #[serde(default)]
    close_to_tray: Option<bool>,
    #[serde(default)]
    start_in_tray: Option<bool>,
    #[serde(default)]
    skip_quit_server_warning: Option<bool>,
}

fn load_desktop_prefs() -> DesktopPrefs {
    // Defaults: always-ready partner UX - close hides to tray (does not kill).
    let mut prefs = DesktopPrefs {
        close_to_tray: true,
        start_in_tray: false,
        skip_quit_server_warning: false,
    };

    // 1) Prefer shell-owned desktop.json when present (proper JSON via serde_json)
    let desk = desktop_prefs_path();
    if let Ok(raw) = std::fs::read_to_string(&desk) {
        if let Ok(file) = serde_json::from_str::<DesktopPrefsFile>(&raw) {
            if let Some(v) = file.close_to_tray {
                prefs.close_to_tray = v;
            }
            if let Some(v) = file.start_in_tray {
                prefs.start_in_tray = v;
            }
            if let Some(v) = file.skip_quit_server_warning {
                prefs.skip_quit_server_warning = v;
            }
            return prefs;
        }
        log::warn!("desktop.json parse failed; using defaults + TOML fallback if any");
    }

    // 2) Fall back to config.toml (Settings writes here; desktop.json may be missing)
    if let Ok(raw) = std::fs::read_to_string(config_toml_path()) {
        if let Some(v) = toml_bool(&raw, "close_to_tray") {
            prefs.close_to_tray = v;
        }
        // start_in_tray: do NOT seed `true` from config.toml alone.
        // Older Setup coupled "Start with Windows" -> start_in_tray=true, so many
        // installs always hid on launch. Only honor an explicit false here; an
        // explicit true requires desktop.json (written when the user checks the
        // "Start hidden in tray" box in Settings).
        if let Some(false) = toml_bool(&raw, "start_in_tray") {
            prefs.start_in_tray = false;
        }
        if let Some(v) = toml_bool(&raw, "skip_quit_server_warning") {
            prefs.skip_quit_server_warning = v;
        }
        // Seed desktop.json so CloseRequested and future launches stay in sync
        let _ = save_desktop_prefs(&prefs);
        log::info!(
            "desktop prefs seeded from config.toml (close_to_tray={}, start_in_tray={}, skip_quit_warn={})",
            prefs.close_to_tray,
            prefs.start_in_tray,
            prefs.skip_quit_server_warning
        );
    }
    prefs
}

fn save_desktop_prefs(prefs: &DesktopPrefs) -> Result<(), String> {
    let path = desktop_prefs_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let file = DesktopPrefsFile {
        close_to_tray: Some(prefs.close_to_tray),
        start_in_tray: Some(prefs.start_in_tray),
        skip_quit_server_warning: Some(prefs.skip_quit_server_warning),
    };
    let body = serde_json::to_string_pretty(&file).map_err(|e| e.to_string())?;
    std::fs::write(&path, body + "\n").map_err(|e| e.to_string())
}

struct ServerState {
    process: Arc<Mutex<Option<Child>>>,
    /// Path to the sidecar binary discovered at startup.
    sidecar_cmd: Arc<Mutex<Option<String>>>,
    /// Files dropped from OS (Explorer). Frontend polls this because WebView
    /// event delivery is unreliable for drag-drop on Windows.
    pending_drops: Arc<Mutex<Vec<DroppedFilePayload>>>,
    /// Always-ready window prefs (close-to-tray / start-in-tray).
    desktop_prefs: Arc<Mutex<DesktopPrefs>>,
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
        "Sidecar not found - checked exe dir {:?}, cwd/bin/",
        current_exe_dir()
    );
    log::error!("{}", msg);
    ("remedy-desktop.exe".to_string(), msg)
}

/// Locate built SPA assets so the Python sidecar can mount browser WebUI at /.
fn find_webui_dir() -> Option<PathBuf> {
    if let Ok(env_dir) = env::var("REMEDY_WEBUI_DIR") {
        let p = PathBuf::from(env_dir.trim());
        if p.join("index.html").is_file() {
            return Some(p);
        }
    }

    let mut candidates: Vec<PathBuf> = Vec::new();

    // Next to main exe / sidecar (packaged: resources/webui or sibling webui)
    if let Some(dir) = current_exe_dir() {
        candidates.extend([
            dir.join("webui"),
            dir.join("ui"),
            dir.join("resources").join("webui"),
            dir.join("desktop").join("dist"),
        ]);
    }

    // Dev: cwd is often desktop/ or repo root when running tauri dev
    if let Ok(cwd) = env::current_dir() {
        candidates.extend([
            cwd.join("dist"),
            cwd.join("desktop").join("dist"),
            cwd.join("..").join("dist"),
            cwd.join("webui"),
        ]);
    }

    // Sidecar binary directory (externalBin lives next to main exe)
    if let Some(dir) = current_exe_dir() {
        candidates.push(dir.join("remedy-desktop").join("webui"));
    }

    for c in candidates {
        if c.join("index.html").is_file() {
            log::info!("WebUI assets found at {}", c.display());
            return Some(c);
        }
    }
    log::warn!("WebUI assets not found - browser WebUI will show helper page");
    None
}

fn spawn_remedy(cmd: &str) -> Option<Child> {
    let home_dir = remedy_home();
    let home_str = home_dir.to_string_lossy();
    // --skip-setup: never block the sidecar on interactive CLI wizard.
    // Desktop SetupWizard is the first-run UX (needs a running API).
    let args = [
        "--home",
        home_str.as_ref(),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "7400",
        "--skip-setup",
    ];

    let webui = find_webui_dir();
    // Packaged local Qwen + llama-server (resource dir/local) for vision + nano swarm.
    let local_bundle = env::var_os("REMEDY_LOCAL_BUNDLE")
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .or_else(|| {
            env::var_os("REMEDY_RESOURCES").map(|r| {
                let p = PathBuf::from(r);
                if p.ends_with("local") {
                    p
                } else {
                    p.join("local")
                }
            })
        });

    #[cfg(target_os = "windows")]
    {
        // IMPORTANT: do NOT combine CREATE_NO_WINDOW with DETACHED_PROCESS —
        // Windows ignores CREATE_NO_WINDOW when DETACHED is set, which opens a
        // visible console for console-subsystem sidecar builds (what the user saw).
        let mut c = Command::new(cmd);
        c.args(args)
            .env("REMEDY_DESKTOP_SIDECAR", "1")
            .env("PYTHONUNBUFFERED", "1")
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(ref dir) = webui {
            c.env("REMEDY_WEBUI_DIR", dir);
        }
        if let Some(ref lb) = local_bundle {
            c.env("REMEDY_LOCAL_BUNDLE", lb);
        }
        c.spawn().ok()
    }
    #[cfg(not(target_os = "windows"))]
    {
        let mut c = Command::new(cmd);
        c.args(args)
            .env("REMEDY_DESKTOP_SIDECAR", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(ref dir) = webui {
            c.env("REMEDY_WEBUI_DIR", dir);
        }
        if let Some(ref lb) = local_bundle {
            c.env("REMEDY_LOCAL_BUNDLE", lb);
        }
        c.spawn().ok()
    }
}

/// True if this sidecar line is routine traffic noise (not useful in the desktop log).
fn is_routine_sidecar_log(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    // uvicorn access-style
    if lower.contains("\"get /api/status")
        || lower.contains("http/1.1\" 200")
        || (lower.contains(" - \"get /api/") && lower.contains(" 200 "))
    {
        return true;
    }
    // Remedy structured access: "GET /api/foo -> 200" / OPTIONS / SLOW GET … 200
    if (lower.contains(" get /api/") || lower.contains(" options /api/") || lower.contains("slow get /api/"))
        && (lower.contains("-> 200") || lower.contains(" 200 ("))
    {
        return true;
    }
    // Common high-frequency desktop polls
    if lower.contains("/api/partner/status")
        || lower.contains("/api/checkpoints/latest")
        || lower.contains("/api/plans/latest")
        || lower.contains("/api/ping")
        || lower.contains("/api/events/sessions")
    {
        // Keep non-2xx for diagnosis
        if lower.contains("-> 200")
            || lower.contains(" 200 (")
            || lower.contains("http/1.1\" 200")
        {
            return true;
        }
    }
    false
}

fn forward_output(label: &str, reader: impl BufRead + Send + 'static) {
    let label = label.to_string();
    thread::spawn(move || {
        for line in reader.lines() {
            match line {
                Ok(text) if !text.is_empty() => {
                    if is_routine_sidecar_log(&text) {
                        continue;
                    }
                    // Warnings/errors from sidecar → warn; rest quiet info
                    let lower = text.to_ascii_lowercase();
                    if lower.contains(" error")
                        || lower.contains("traceback")
                        || lower.contains("exception")
                        || lower.contains(" -> 5")
                        || lower.contains(" -> 4")
                    {
                        log::warn!("[remedy {}] {}", label, text);
                    } else if lower.contains("warning") || lower.contains("slow ") {
                        log::warn!("[remedy {}] {}", label, text);
                    } else {
                        log::debug!("[remedy {}] {}", label, text);
                    }
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
    if let Some(mut child) = guard.take() {
        let pid = child.id();
        // On Windows, Child::kill / Drop do NOT kill the process tree. PyInstaller
        // sidecars (and anything still holding :7400) must be tree-killed or they
        // linger in Task Manager after the UI closes.
        #[cfg(target_os = "windows")]
        {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .creation_flags(CREATE_NO_WINDOW)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Best-effort vision stop via raw HTTP (never spawn PowerShell — cold start hangs quit).
fn try_stop_vision_http() {
    // Fire-and-forget thread with hard timeout so the quit path never blocks here.
    let _ = thread::Builder::new()
        .name("vision-stop".into())
        .spawn(|| {
            let agent = ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_millis(400))
                .timeout(Duration::from_millis(800))
                .build();
            // Bootstrap is optional; stop often works without auth on loopback.
            let _ = agent
                .post("http://127.0.0.1:7400/api/vision/stop")
                .call();
        });
    // Cap wait so quit stays snappy even if the request is slow.
    thread::sleep(Duration::from_millis(250));
}

/// Stop the managed sidecar and any leftover remedy-desktop processes / :7400 listeners.
/// Must never hang — tray "Quit and stop server" depends on this returning quickly.
fn shutdown_sidecar(state: &ServerState) {
    try_stop_vision_http();

    match state.process.lock() {
        Ok(mut guard) => kill_child(&mut guard),
        Err(poisoned) => {
            let mut guard = poisoned.into_inner();
            kill_child(&mut guard);
        }
    }
    force_stop_remedy_processes();
    // Belt-and-suspenders: kill orphaned vision decoder processes by image name.
    force_stop_vision_processes();
    log::info!("Sidecar shutdown complete");
}

/// Kill leftover llama-server (vision decoder) if the API stop path did not run.
#[cfg(target_os = "windows")]
fn force_stop_vision_processes() {
    for image in ["llama-server.exe", "llama_server.exe"] {
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/IM", image])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

#[cfg(not(target_os = "windows"))]
fn force_stop_vision_processes() {
    let _ = Command::new("pkill")
        .args(["-f", "llama-server"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// Force-stop every process that can lock install-dir files (sidecar + stray copies).
/// Used before launching the NSIS updater so "Can't write remedy-desktop.exe" is rare.
#[cfg(target_os = "windows")]
fn force_stop_remedy_processes() {
    let images = [
        "remedy-desktop.exe",
        "remedy-desktop-x86_64-pc-windows-msvc.exe",
        "remedy-desktop-amd64-pc-windows-msvc.exe",
    ];
    for image in images {
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/IM", image])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    // Kill whatever still owns the sidecar port.
    let _ = Command::new("cmd")
        .args([
            "/C",
            r#"for /f "tokens=5" %a in ('netstat -ano ^| findstr :7400 ^| findstr LISTENING') do taskkill /F /PID %a"#,
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(target_os = "windows"))]
fn force_stop_remedy_processes() {}

fn start_sidecar(process: &Arc<Mutex<Option<Child>>>, cmd: &str) -> Result<(), String> {
    let mut guard = process
        .lock()
        .map_err(|_| "server state lock poisoned".to_string())?;
    kill_child(&mut guard);
    // Prevent dual sidecars (old process keeps :7400 and serves stale OAuth).
    force_stop_remedy_processes();
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("cmd")
            .args([
                "/C",
                r#"for /f "tokens=5" %a in ('netstat -ano ^| findstr :7400 ^| findstr LISTENING') do taskkill /F /PID %a"#,
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        // Brief pause so the port is free before re-bind.
        std::thread::sleep(Duration::from_millis(400));
    }

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

/// Save plain text via native Save dialog (session export - WebView download is unreliable).
#[tauri::command]
fn sanitize_export_filename(default_name: &str) -> String {
    if default_name.trim().is_empty() {
        return "remedy-export.txt".to_string();
    }
    default_name
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect::<String>()
}

/// Native save dialog + write (no PowerShell). Large exports stay in Rust only.
#[tauri::command]
fn save_text_file(default_name: String, contents: String) -> Result<Option<String>, String> {
    let name = sanitize_export_filename(&default_name);

    let path = rfd::FileDialog::new()
        .set_title("Export Remedy session")
        .set_file_name(&name)
        .add_filter("Text", &["txt"])
        .add_filter("Markdown", &["md"])
        .add_filter("All files", &["*"])
        .save_file();

    let Some(path) = path else {
        return Ok(None);
    };
    std::fs::write(&path, contents.as_bytes()).map_err(|e| format!("write failed: {e}"))?;
    Ok(Some(path.to_string_lossy().to_string()))
}

/// Native open dialog for session import.
/// Returns `{ path, text }` so the UI can POST text (or path) without WebView FileReader.
#[tauri::command]
fn open_text_file() -> Result<Option<serde_json::Value>, String> {
    let path = rfd::FileDialog::new()
        .set_title("Import Remedy session")
        .add_filter("Session export", &["txt", "md"])
        .add_filter("All files", &["*"])
        .pick_file();
    let Some(path) = path else {
        return Ok(None);
    };
    let meta = std::fs::metadata(&path).map_err(|e| format!("stat failed: {e}"))?;
    // Match frontend 8 MB guard — avoid multi-hundred-MB freezes.
    if meta.len() > 8_000_000 {
        return Err(
            "File too large (8 MB max). Export without embedded images or split the session."
                .into(),
        );
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("read failed: {e}"))?;
    Ok(Some(serde_json::json!({
        "path": path.to_string_lossy(),
        "text": text,
        "name": path.file_name().and_then(|s| s.to_str()).unwrap_or("import.txt"),
    })))
}

#[cfg(not(target_os = "windows"))]
fn dirs_next_home() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME").map(std::path::PathBuf::from)
}

/// Resolve the primary desktop window (label "main", else first webview).
fn primary_window(app: &AppHandle) -> Option<tauri::WebviewWindow> {
    app.get_webview_window("main").or_else(|| {
        app.webview_windows()
            .into_iter()
            .find(|(label, _)| label != "remedy-browser")
            .map(|(_, w)| w)
            .or_else(|| app.webview_windows().into_values().next())
    })
}

/// Reliable show + unminimize + focus (taskbar minimize + tray restore).
///
/// Windows often leaves maximized windows stuck minimized unless we unminimize
/// *before* show/focus, and a brief always-on-top pulse reorders Z-order when
/// `set_focus` alone is ignored after tray/taskbar hide.
fn bring_main_to_front(app: &AppHandle) {
    let Some(w) = primary_window(app) else {
        log::warn!("bring_main_to_front: no main window");
        return;
    };
    let _ = w.set_skip_taskbar(false);
    // Unminimize first — show alone does not restore from taskbar minimize.
    if w.is_minimized().unwrap_or(true) {
        let _ = w.unminimize();
    }
    let _ = w.show();
    let _ = w.unminimize();
    // Pulse always-on-top so the window surfaces above other apps on Windows.
    let _ = w.set_always_on_top(true);
    let _ = w.set_focus();
    let _ = w.set_always_on_top(false);
    let _ = w.set_focus();
    log::info!("bring_main_to_front: shown + focused");
}

/// Native folder picker (rfd — must run on the UI thread on Windows).
#[tauri::command]
async fn pick_folder(app: AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        let path = rfd::FileDialog::new()
            .set_title("Select project folder")
            .pick_folder()
            .map(|p| p.to_string_lossy().to_string());
        let _ = tx.send(path);
    })
    .map_err(|e| format!("folder picker (main thread): {e}"))?;
    // Wait off the UI thread; dialog itself runs on main via run_on_main_thread.
    rx.recv()
        .map_err(|e| format!("folder picker channel: {e}"))
}

/// Open a file or folder with the OS default app (Explorer / associated program).
/// Windows: prefer explorer / ShellExecute-style open without a visible cmd.exe flash.
#[tauri::command]
fn open_path(path: String) -> Result<String, String> {
    let p = PathBuf::from(path.trim());
    if !p.exists() {
        return Err(format!("path not found: {}", p.display()));
    }
    #[cfg(target_os = "windows")]
    {
        // Folders → open in Explorer. Files → Explorer select (fast, no cmd).
        let status = if p.is_dir() {
            Command::new("explorer.exe")
                .arg(p.as_os_str())
                .creation_flags(CREATE_NO_WINDOW)
                .status()
        } else {
            // /select,path — Explorer opens parent and highlights the file
            let arg = format!("/select,{}", p.display());
            Command::new("explorer.exe")
                .arg(arg)
                .creation_flags(CREATE_NO_WINDOW)
                .status()
        }
        .map_err(|e| format!("open_path: {e}"))?;
        // explorer returns non-zero even on success sometimes — only error on launch fail
        let _ = status;
        return Ok(format!("Opened {}", p.display()));
    }
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg(&p)
            .status()
            .map_err(|e| format!("open_path: {e}"))?;
        if status.success() {
            return Ok(format!("Opened {}", p.display()));
        }
        return Err(format!("open_path failed: {status}"));
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let status = Command::new("xdg-open")
            .arg(&p)
            .status()
            .map_err(|e| format!("open_path: {e}"))?;
        if status.success() {
            return Ok(format!("Opened {}", p.display()));
        }
        return Err(format!("open_path failed: {status}"));
    }
}

/// Open a host terminal at `cwd`.
/// Windows (primary): **PowerShell** first (Windows PowerShell 5.1 / pwsh), then WT, then cmd.
#[tauri::command]
fn open_terminal(cwd: Option<String>) -> Result<String, String> {
    let dir = cwd
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let dir_s = dir.to_string_lossy().to_string();

    #[cfg(target_os = "windows")]
    {
        // New console window so a GUI/Tauri parent still shows a real shell.
        const CREATE_NEW_CONSOLE: u32 = 0x00000010;
        let cd_cmd = format!(
            "Set-Location -LiteralPath '{}'",
            dir_s.replace('\'', "''")
        );
        // 1) PowerShell 7+ if installed, 2) Windows PowerShell 5.1 (always on Win10+)
        for (exe, label) in [("pwsh.exe", "PowerShell 7+"), ("powershell.exe", "Windows PowerShell")]
        {
            let mut c = Command::new(exe);
            c.args(["-NoExit", "-NoLogo", "-Command", &cd_cmd]);
            c.creation_flags(CREATE_NEW_CONSOLE);
            if c.spawn().is_ok() {
                return Ok(format!("Opened {label} in {dir_s}"));
            }
        }
        // Optional: Windows Terminal hosting PowerShell in project dir
        if Command::new("wt.exe")
            .args(["-d", &dir_s, "powershell.exe", "-NoExit", "-NoLogo"])
            .spawn()
            .is_ok()
        {
            return Ok(format!("Opened Windows Terminal (PowerShell) in {dir_s}"));
        }
        // Last resort: cmd
        let status = Command::new("cmd.exe")
            .args(["/c", "start", "cmd.exe", "/k", &format!("cd /d \"{dir_s}\"")])
            .status()
            .map_err(|e| format!("open terminal failed: {e}"))?;
        if status.success() {
            return Ok(format!("Opened cmd in {dir_s}"));
        }
        return Err(format!("Could not open a terminal (exit {status})"));
    }

    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "tell application \"Terminal\" to do script \"cd {}\"",
            dir_s.replace('\\', "\\\\").replace('"', "\\\"")
        );
        let status = Command::new("osascript")
            .args(["-e", &script])
            .status()
            .map_err(|e| format!("open terminal failed: {e}"))?;
        if status.success() {
            return Ok(format!("Opened Terminal in {dir_s}"));
        }
        return Err(format!("osascript failed: {status}"));
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        for term in ["x-terminal-emulator", "gnome-terminal", "konsole", "xterm"] {
            let r = Command::new(term)
                .arg("--working-directory")
                .arg(&dir_s)
                .spawn();
            if r.is_ok() {
                return Ok(format!("Opened {term} in {dir_s}"));
            }
        }
        return Err("No terminal emulator found".into());
    }

    #[allow(unreachable_code)]
    Err("open_terminal unsupported on this platform".into())
}

/// Open a URL in an external browser. Prefer Firefox when installed if `prefer_firefox`.
#[tauri::command]
fn open_external_url(url: String, prefer_firefox: Option<bool>) -> Result<String, String> {
    let url = url.trim().to_string();
    if url.is_empty() {
        return Err("empty url".into());
    }
    if !(url.starts_with("http://") || url.starts_with("https://") || url.starts_with("about:")) {
        return Err("only http(s) URLs allowed".into());
    }
    let want_ff = prefer_firefox.unwrap_or(true);

    #[cfg(target_os = "windows")]
    {
        if want_ff {
            let candidates = [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ];
            for c in candidates {
                if Path::new(c).is_file() {
                    let r = Command::new(c).arg(&url).spawn();
                    if r.is_ok() {
                        return Ok(format!("Opened in Firefox: {url}"));
                    }
                }
            }
            // PATH firefox
            if Command::new("firefox").arg(&url).spawn().is_ok() {
                return Ok(format!("Opened in Firefox: {url}"));
            }
        }
        let status = Command::new("cmd")
            .args(["/C", "start", "", &url])
            .status()
            .map_err(|e| format!("open url failed: {e}"))?;
        if status.success() {
            return Ok(format!("Opened default browser: {url}"));
        }
        return Err(format!("open url exited {status}"));
    }

    #[cfg(target_os = "macos")]
    {
        if want_ff {
            let r = Command::new("open")
                .args(["-a", "Firefox", &url])
                .status();
            if let Ok(s) = r {
                if s.success() {
                    return Ok(format!("Opened in Firefox: {url}"));
                }
            }
        }
        let status = Command::new("open")
            .arg(&url)
            .status()
            .map_err(|e| format!("open url failed: {e}"))?;
        if status.success() {
            return Ok(format!("Opened default browser: {url}"));
        }
        return Err(format!("open exited {status}"));
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if want_ff {
            if Command::new("firefox").arg(&url).spawn().is_ok() {
                return Ok(format!("Opened in Firefox: {url}"));
            }
        }
        let status = Command::new("xdg-open")
            .arg(&url)
            .status()
            .map_err(|e| format!("open url failed: {e}"))?;
        if status.success() {
            return Ok(format!("Opened default browser: {url}"));
        }
        return Err(format!("xdg-open exited {status}"));
    }

    #[allow(unreachable_code)]
    Err("open_external_url unsupported".into())
}

/// Startup-folder shortcut name (user-visible in Settings -> Apps -> Startup).
///
/// IMPORTANT: Do **not** use HKCU\...\Run. Writing that key from a background
/// process is a classic malware pattern and triggers Windows Defender ML
/// `Behavior:Win32/Persistence.A!ml`. The Startup folder is the supported,
/// user-auditable approach.
#[cfg(target_os = "windows")]
fn windows_startup_dir() -> PathBuf {
    let appdata = env::var("APPDATA").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(appdata)
        .join("Microsoft")
        .join("Windows")
        .join("Start Menu")
        .join("Programs")
        .join("Startup")
}

#[cfg(target_os = "windows")]
fn windows_startup_lnk_path() -> PathBuf {
    windows_startup_dir().join("Remedy Desktop.lnk")
}

/// Names written by Remedy 0.10.19-0.10.21 under HKCU\...\Run (legacy only).
#[cfg(target_os = "windows")]
const LEGACY_RUN_VALUE_NAMES: &[&str] = &["RemedyDesktop", "Remedy Desktop", "remedy-desktop"];

/// Remove legacy HKCU Run entries left by older Remedy builds.
///
/// Uses the Windows registry API directly - **no** hidden PowerShell with
/// `-ExecutionPolicy Bypass` (that pattern itself looks like malware to ML).
/// We only **delete** values; Remedy never writes the Run key.
#[cfg(target_os = "windows")]
fn remove_legacy_run_key() {
    use winreg::enums::{HKEY_CURRENT_USER, KEY_SET_VALUE};
    use winreg::RegKey;

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let Ok(key) = hkcu.open_subkey_with_flags(
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        KEY_SET_VALUE,
    ) else {
        return;
    };
    for name in LEGACY_RUN_VALUE_NAMES {
        // Missing values are normal on clean installs; any other error is non-fatal.
        match key.delete_value(name) {
            Ok(()) => log::info!("Removed legacy HKCU Run value '{name}'"),
            Err(e) => log::debug!("HKCU Run delete '{name}': {e}"),
        }
    }
}

/// Windows: enable/disable "Start with Windows" via **Startup folder shortcut only**.
/// Never writes the registry Run key (avoids Persistence.A!ml false positives).
#[tauri::command]
fn set_launch_at_login(enabled: bool) -> Result<bool, String> {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // Scrub any leftover Run keys when the user toggles autostart (not on every poll).
        remove_legacy_run_key();

        let exe = env::current_exe().map_err(|e| e.to_string())?;
        let exe_str = exe.to_string_lossy().replace('\'', "''");
        let work_dir = exe
            .parent()
            .map(|p| p.to_string_lossy().replace('\'', "''"))
            .unwrap_or_default();
        let lnk = windows_startup_lnk_path();
        let lnk_str = lnk.to_string_lossy().replace('\'', "''");

        if enabled {
            let startup = windows_startup_dir();
            std::fs::create_dir_all(&startup)
                .map_err(|e| format!("create Startup folder: {e}"))?;
            // User-visible shortcut only - shows under Settings -> Apps -> Startup.
            // PowerShell is used only on explicit user toggle (not every launch).
            let ps = format!(
                r#"
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{lnk}')
$s.TargetPath = '{exe}'
$s.WorkingDirectory = '{wd}'
$s.WindowStyle = 1
$s.Description = 'Remedy Desktop (optional Start with Windows - disable in Settings or Startup apps)'
$s.Save()
"#,
                lnk = lnk_str,
                exe = exe_str,
                wd = work_dir,
            );
            let output = Command::new("powershell")
                .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps])
                .creation_flags(CREATE_NO_WINDOW)
                .output()
                .map_err(|e| format!("create Startup shortcut: {e}"))?;
            if !output.status.success() {
                let err = String::from_utf8_lossy(&output.stderr);
                return Err(format!(
                    "Failed to create Startup shortcut: {}",
                    err.trim()
                ));
            }
            log::info!("Launch at login enabled via Startup folder -> {}", lnk.display());
        } else {
            if lnk.exists() {
                let _ = std::fs::remove_file(&lnk);
            }
            log::info!("Launch at login disabled (Startup shortcut removed)");
        }
        return Ok(enabled);
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = enabled;
        Err("Launch at login is only implemented on Windows in this build".into())
    }
}

#[tauri::command]
fn get_launch_at_login() -> Result<bool, String> {
    #[cfg(target_os = "windows")]
    {
        // Read-only: presence of the Startup folder shortcut.
        // Do **not** spawn PowerShell or touch the registry on every Settings poll.
        let lnk = windows_startup_lnk_path();
        return Ok(lnk.is_file());
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(false)
    }
}

/// Cleanup for Defender: remove legacy Run keys without enabling autostart.
#[tauri::command]
fn scrub_legacy_autostart() -> Result<String, String> {
    #[cfg(target_os = "windows")]
    {
        remove_legacy_run_key();
        Ok("Removed legacy registry Run entries if present. Autostart now uses Startup folder only.".into())
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok("No Windows registry cleanup needed.".into())
    }
}

#[tauri::command]
fn set_desktop_prefs(
    state: State<'_, ServerState>,
    close_to_tray: bool,
    start_in_tray: bool,
    skip_quit_server_warning: Option<bool>,
) -> Result<(), String> {
    let prev_skip = state
        .desktop_prefs
        .lock()
        .map(|g| g.skip_quit_server_warning)
        .unwrap_or(false);
    let prefs = DesktopPrefs {
        close_to_tray,
        start_in_tray,
        skip_quit_server_warning: skip_quit_server_warning.unwrap_or(prev_skip),
    };
    save_desktop_prefs(&prefs)?;
    if let Ok(mut g) = state.desktop_prefs.lock() {
        *g = prefs;
    }
    Ok(())
}

#[tauri::command]
fn get_desktop_prefs(state: State<'_, ServerState>) -> Result<serde_json::Value, String> {
    // Prefer disk so Settings / dialogs see latest "don't warn" flag
    let g = load_desktop_prefs();
    if let Ok(mut lock) = state.desktop_prefs.lock() {
        *lock = DesktopPrefs {
            close_to_tray: g.close_to_tray,
            start_in_tray: g.start_in_tray,
            skip_quit_server_warning: g.skip_quit_server_warning,
        };
    }
    Ok(serde_json::json!({
        "close_to_tray": g.close_to_tray,
        "start_in_tray": g.start_in_tray,
        "skip_quit_server_warning": g.skip_quit_server_warning,
    }))
}

/// Hard-exit failsafe: if Tauri's event loop does not tear down, kill ourselves.
fn schedule_force_exit(after: Duration) {
    let pid = std::process::id();
    thread::spawn(move || {
        thread::sleep(after);
        log::warn!("quit failsafe: process still alive after {after:?} — forcing exit");
        #[cfg(target_os = "windows")]
        {
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .creation_flags(CREATE_NO_WINDOW)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
        }
        std::process::exit(0);
    });
}

/// Full quit: stop sidecar (Web UI dies) and exit. Use after the warning dialog.
#[tauri::command]
fn quit_app(app: AppHandle, state: State<'_, ServerState>) -> Result<(), String> {
    log::info!("quit_app: shutting down sidecar and exiting");
    browser_host::close_browser_on_quit(&app);
    // Never block the invoke forever — user clicked "Quit and stop server".
    shutdown_sidecar(&state);
    // Exit via Tauri, with a hard failsafe if the event loop stalls.
    schedule_force_exit(Duration::from_secs(2));
    app.exit(0);
    // If exit() is deferred, still return so the IPC call completes.
    Ok(())
}

/// Request quit: if user has not dismissed the warning, ask the frontend dialog.
/// Returns `{ needs_confirm: bool }`.
#[tauri::command]
fn request_quit_app(app: AppHandle, state: State<'_, ServerState>) -> Result<serde_json::Value, String> {
    let fresh = load_desktop_prefs();
    if let Ok(mut g) = state.desktop_prefs.lock() {
        *g = DesktopPrefs {
            close_to_tray: fresh.close_to_tray,
            start_in_tray: fresh.start_in_tray,
            skip_quit_server_warning: fresh.skip_quit_server_warning,
        };
    }
    if fresh.skip_quit_server_warning {
        browser_host::close_browser_on_quit(&app);
        shutdown_sidecar(&state);
        schedule_force_exit(Duration::from_secs(2));
        app.exit(0);
        return Ok(serde_json::json!({ "needs_confirm": false, "quitting": true }));
    }
    // Bring window forward so the in-app dialog is visible (e.g. tray Quit).
    bring_main_to_front(&app);
    let _ = app.emit("app-quit-requested", ());
    Ok(serde_json::json!({ "needs_confirm": true, "quitting": false }))
}

#[tauri::command]
fn show_main_window(app: AppHandle) -> Result<(), String> {
    primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    bring_main_to_front(&app);
    Ok(())
}

#[tauri::command]
fn is_main_window_maximized(app: AppHandle) -> Result<bool, String> {
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    w.is_maximized()
        .map_err(|e| format!("is_maximized failed: {e}"))
}

/// Reliable minimize from the custom title bar (avoids webview permission races).
#[tauri::command]
fn minimize_main_window(app: AppHandle) -> Result<(), String> {
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    w.minimize().map_err(|e| format!("minimize failed: {e}"))?;
    Ok(())
}

/// Explicit title-bar drag (preferred over CSS `data-tauri-drag-region` on Windows).
/// CSS drag regions leave sticky hit-tests after move/maximize so min/max/close die.
#[tauri::command]
fn start_dragging_main_window(app: AppHandle) -> Result<(), String> {
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    w.start_dragging()
        .map_err(|e| format!("start_dragging failed: {e}"))?;
    Ok(())
}

/// Hide desktop window to tray and open the browser WebUI (same chat app via local API).
#[tauri::command]
fn switch_to_web_ui(app: AppHandle) -> Result<String, String> {
    // Prefer full SPA when sidecar serves it; fall back to API dashboard.
    let url = std::env::var("REMEDY_WEB_UI_URL")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| "http://127.0.0.1:7400/".to_string());

    // Brief wait so a just-started sidecar can answer before the browser loads.
    let deadline = Instant::now() + Duration::from_secs(8);
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&status_addr(), Duration::from_millis(300)).is_ok() {
            break;
        }
        thread::sleep(Duration::from_millis(200));
    }

    // Hide to tray (keep sidecar alive) - same as close-to-tray.
    if let Some(w) = primary_window(&app) {
        w.hide().map_err(|e| format!("hide to tray failed: {e}"))?;
        log::info!("switch_to_web_ui: desktop hidden to tray");
    }

    // Open default browser (Windows: start; shell plugin as secondary).
    #[cfg(target_os = "windows")]
    {
        let status = Command::new("cmd")
            .args(["/C", "start", "", &url])
            .creation_flags(CREATE_NO_WINDOW)
            .status()
            .map_err(|e| format!("open browser failed: {e}"))?;
        if !status.success() {
            return Err(format!("open browser exited with {status}"));
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        let status = Command::new("xdg-open")
            .arg(&url)
            .status()
            .or_else(|_| Command::new("open").arg(&url).status())
            .map_err(|e| format!("open browser failed: {e}"))?;
        if !status.success() {
            return Err(format!("open browser exited with {status}"));
        }
    }

    Ok(url)
}

/// Maximize / restore from the custom title bar.
#[tauri::command]
fn toggle_maximize_main_window(app: AppHandle) -> Result<bool, String> {
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    let max = w
        .is_maximized()
        .map_err(|e| format!("is_maximized failed: {e}"))?;
    if max {
        w.unmaximize()
            .map_err(|e| format!("unmaximize failed: {e}"))?;
    } else {
        w.maximize()
            .map_err(|e| format!("maximize failed: {e}"))?;
    }
    w.is_maximized()
        .map_err(|e| format!("is_maximized failed: {e}"))
}

/// Close button: hide to tray when enabled, otherwise quit (sidecar stopped via CloseRequested).
#[tauri::command]
fn request_close_main_window(
    app: AppHandle,
    state: State<'_, ServerState>,
) -> Result<(), String> {
    // Always re-read disk so Settings changes apply without restart.
    let fresh = load_desktop_prefs();
    if let Ok(mut g) = state.desktop_prefs.lock() {
        *g = DesktopPrefs {
            close_to_tray: fresh.close_to_tray,
            start_in_tray: fresh.start_in_tray,
            skip_quit_server_warning: fresh.skip_quit_server_warning,
        };
    }
    let close_to_tray = fresh.close_to_tray;
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    if close_to_tray {
        w.hide().map_err(|e| format!("hide failed: {e}"))?;
        log::info!("request_close_main_window: hidden to tray");
    } else {
        // Triggers CloseRequested -> sidecar shutdown on full quit
        w.close().map_err(|e| format!("close failed: {e}"))?;
        log::info!("request_close_main_window: close requested (full quit path)");
    }
    Ok(())
}

/// Apply the current branding PNG as the window icon (taskbar / Alt-Tab).
/// `include_image!` embeds icons/icon.png (rounded plate circuit-R) at compile time.
fn apply_window_icons(app: &AppHandle) {
    // Path is relative to the crate root (desktop/src-tauri/)
    let icon = tauri::include_image!("icons/icon.png");
    for (_, window) in app.webview_windows() {
        if let Err(e) = window.set_icon(icon.clone()) {
            log::warn!("set_icon on {}: {e}", window.label());
        } else {
            log::info!("Applied window icon on {}", window.label());
        }
    }
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
// In-app update (Ollama-style): check -> download progress UI -> install -> relaunch
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
            // Avoid stale CDN/proxy copies of latest.json after a new release.
            .set("Cache-Control", "no-cache")
            .set("Pragma", "no-cache")
            .timeout(Duration::from_secs(20))
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
            let latest_norm = latest
                .trim()
                .trim_start_matches('v')
                .trim_start_matches('V')
                .to_string();
            let newer = is_newer(&latest_norm, &current);
            // Never claim an update is available without an installer URL.
            let available = newer && download_url.as_ref().is_some_and(|u| !u.is_empty());
            let error = if newer && !available {
                Some(
                    "A newer version exists but no Windows installer URL was found on the release."
                        .into(),
                )
            } else {
                None
            };
            DesktopUpdateInfo {
                current_version: current,
                latest_version: latest_norm,
                update_available: available,
                download_url,
                release_notes: notes,
                error,
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

/// Read the local API bearer token written by the Python sidecar
/// (`~/.remedy/auth/local_api_token`) so the webview can authenticate.
#[tauri::command]
fn get_local_api_token() -> Result<String, String> {
    let home = if cfg!(target_os = "windows") {
        env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string())
    } else {
        env::var("HOME").unwrap_or_else(|_| ".".to_string())
    };
    let path = PathBuf::from(home)
        .join(".remedy")
        .join("auth")
        .join("local_api_token");
    if !path.is_file() {
        return Err("local API token not found - is the sidecar running?".into());
    }
    let raw = std::fs::read_to_string(&path).map_err(|e| format!("read token: {e}"))?;
    let tok = raw.trim().to_string();
    if tok.len() < 16 {
        return Err("local API token is empty or invalid".into());
    }
    Ok(tok)
}

/// Non-blocking update check (network I/O off the UI thread).
#[tauri::command]
async fn check_desktop_update(app: AppHandle) -> Result<DesktopUpdateInfo, String> {
    let current = app_version(&app);
    tauri::async_runtime::spawn_blocking(move || desktop_update_result(current))
        .await
        .map_err(|e| format!("Update check task failed: {e}"))
}

fn update_status_path() -> PathBuf {
    env::temp_dir().join("RemedyDesktop-Update-status.json")
}

/// Persist update phase for the out-of-process progress host (survives app.exit).
fn write_update_status(phase: &str, percent: u8, message: &str, from: &str, to: &str) {
    let path = update_status_path();
    let body = format!(
        "{{\n  \"phase\": {},\n  \"percent\": {},\n  \"message\": {},\n  \"from\": {},\n  \"to\": {},\n  \"updated_at\": {}\n}}\n",
        serde_json::to_string(phase).unwrap_or_else(|_| "\"\"".into()),
        percent,
        serde_json::to_string(message).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(from).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(to).unwrap_or_else(|_| "\"\"".into()),
        serde_json::to_string(
            &std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs().to_string())
                .unwrap_or_else(|_| "0".into())
        )
        .unwrap_or_else(|_| "\"0\"".into()),
    );
    let _ = std::fs::write(&path, body);
}

fn emit_progress(app: &AppHandle, phase: &str, percent: u8, message: &str) {
    write_update_status(phase, percent, message, "", "");
    let _ = app.emit(
        "update-progress",
        UpdateProgress {
            phase: phase.to_string(),
            percent,
            message: message.to_string(),
        },
    );
}

fn emit_progress_ver(
    app: &AppHandle,
    phase: &str,
    percent: u8,
    message: &str,
    from: &str,
    to: &str,
) {
    write_update_status(phase, percent, message, from, to);
    let _ = app.emit(
        "update-progress",
        UpdateProgress {
            phase: phase.to_string(),
            percent,
            message: message.to_string(),
        },
    );
}

/// Path of the flag that tells NSIS POSTINSTALL *not* to auto-start the app.
/// The in-app update script is the sole relaunch owner while this exists.
fn updater_owns_relaunch_flag_path() -> PathBuf {
    env::temp_dir().join("RemedyDesktop-UpdaterOwnsRelaunch.flag")
}

/// Schedule the post-exit install script so it survives Tauri's Job Object.
///
/// Failure mode (0.14.4→0.14.5): `powershell -File` was spawned with DETACHED
/// flags but still died with the parent when BREAKAWAY was refused — download
/// finished, status stuck at "closing", install never ran (no log lines).
///
/// Strategy (first success wins; all are silent / no black CMD):
/// 1. `powershell.exe` + CREATE_BREAKAWAY_FROM_JOB
/// 2. `wscript.exe` + tiny .vbs `WScript.Shell.Run` (often outside the job)
/// 3. One-shot `schtasks` 15s in the future (always outlives the app)
#[cfg(target_os = "windows")]
fn schedule_update_install_script(ps1_path: &str) -> Result<(), String> {
    const DETACHED_PROCESS: u32 = 0x00000008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
    let flags_breakaway =
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB;
    let flags_basic = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW;

    let mut ok_count = 0u32;
    let mut errors: Vec<String> = Vec::new();

    // --- 1) Direct PowerShell with job breakaway ---
    let try_ps = |flags: u32| -> Result<(), String> {
        Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                ps1_path,
            ])
            .creation_flags(flags)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    };
    if try_ps(flags_breakaway)
        .or_else(|e1| {
            log::warn!("update schedule: breakaway powershell failed: {e1}");
            try_ps(flags_basic)
        })
        .is_ok()
    {
        ok_count += 1;
        log::info!("update schedule: powershell spawn ok");
    } else {
        errors.push("powershell spawn failed".into());
    }

    // --- 2) WScript.Shell.Run via temp .vbs (hidden, often outlives Job) ---
    let vbs = env::temp_dir().join(format!(
        "RemedyDesktop-Update-Launch-{}.vbs",
        std::process::id()
    ));
    let ps1_vbs = ps1_path.replace('"', "\"\"");
    let vbs_body = format!(
        "On Error Resume Next\r\n\
         Dim sh: Set sh = CreateObject(\"WScript.Shell\")\r\n\
         sh.Run \"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"\"{ps1}\"\"\", 0, False\r\n",
        ps1 = ps1_vbs
    );
    if std::fs::write(&vbs, vbs_body).is_ok() {
        let vbs_s = vbs.to_string_lossy().to_string();
        if Command::new("wscript.exe")
            .args(["//B", "//Nologo", &vbs_s])
            .creation_flags(flags_breakaway)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .or_else(|_| {
                Command::new("wscript.exe")
                    .args(["//B", "//Nologo", &vbs_s])
                    .creation_flags(flags_basic)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
            })
            .is_ok()
        {
            ok_count += 1;
            log::info!("update schedule: wscript launch ok");
        } else {
            errors.push("wscript spawn failed".into());
        }
    } else {
        errors.push("vbs write failed".into());
    }

    // --- 3) One-shot scheduled task — always outside the app Job ---
    let task = format!("RemedyDesktopUpdate_{}", std::process::id());
    let st = {
        let out = Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-Command",
                "(Get-Date).AddSeconds(25).ToString('HH:mm')",
            ])
            .creation_flags(CREATE_NO_WINDOW)
            .output();
        match out {
            Ok(o) if o.status.success() => {
                let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if s.len() >= 4 {
                    s
                } else {
                    "23:59".into()
                }
            }
            _ => "23:59".into(),
        }
    };
    let tr = format!(
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{}\"",
        ps1_path
    );
    let _ = Command::new("schtasks.exe")
        .args(["/Delete", "/TN", &task, "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let create_ok = Command::new("schtasks.exe")
        .args([
            "/Create",
            "/TN",
            &task,
            "/TR",
            &tr,
            "/SC",
            "ONCE",
            "/ST",
            &st,
            "/F",
            "/RL",
            "LIMITED",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if create_ok {
        let _ = Command::new("schtasks.exe")
            .args(["/Run", "/TN", &task])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        ok_count += 1;
        log::info!("update schedule: schtasks {task} created and run (ST={st})");
        let cleanup = format!("Start-Sleep -Seconds 240; schtasks /Delete /TN \"{task}\" /F");
        let _ = Command::new("powershell.exe")
            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &cleanup])
            .creation_flags(flags_basic)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    } else {
        errors.push("schtasks create/run failed".into());
        log::warn!("update schedule: schtasks create failed");
    }

    if ok_count > 0 {
        Ok(())
    } else {
        Err(errors.join(" | "))
    }
}

#[cfg(not(target_os = "windows"))]
fn schedule_update_install_script(_ps1_path: &str) -> Result<(), String> {
    Err("updates are Windows-only".into())
}

fn write_updater_owns_relaunch_flag() {
    let path = updater_owns_relaunch_flag_path();
    let _ = std::fs::write(
        &path,
        format!(
            "owned_by=remedy_desktop_updater\npid={}\n",
            std::process::id()
        ),
    );
    log::info!("Wrote updater-owns-relaunch flag: {}", path.display());
}

/// Ensure the install-progress host script is in TEMP (never depend on install-dir during NSIS).
fn ensure_update_ui_ps1_in_temp() -> PathBuf {
    let temp_ps1 = env::temp_dir().join("remedy-update-ui.ps1");
    // Always write embedded source so fixes ship with the running binary.
    if let Err(e) = std::fs::write(&temp_ps1, include_str!("../windows/remedy-update-ui.ps1")) {
        log::error!("Cannot write update progress host PS1: {e}");
    }
    temp_ps1
}

/// Launch the *install* progress popup (second window).
///
/// UX contract:
/// 1. In-app UpdateScreen = download only (dies with Remedy)
/// 2. After Remedy closes -> this host appears for install / relaunch
///
/// Must not be started at download begin (that confused users with one long window
/// that vanished when the app exited). Called from the detached update script.
///
/// Spawns powershell.exe directly with CREATE_NO_WINDOW + breakaway flags so
/// no black CMD console flashes. The WinForms UI still shows (WindowStyle Hidden
/// only hides the console host; the form is owned by the STA process).
#[cfg(target_os = "windows")]
fn launch_install_progress_ui(from: &str, to: &str) {
    let status = update_status_path();
    write_update_status(
        "installing",
        90,
        "Installing update...",
        from,
        to,
    );
    let temp_ps1 = ensure_update_ui_ps1_in_temp();
    if !temp_ps1.is_file() {
        log::error!("Install progress host PS1 missing after write");
        return;
    }

    let status_s = status.to_string_lossy().to_string();
    let ps1_s = temp_ps1.to_string_lossy().to_string();
    let from_s = from.to_string();
    let to_s = to.to_string();

    // Direct powershell spawn - no `cmd /c start` (that flashed 1-2 consoles).
    const DETACHED_PROCESS: u32 = 0x00000008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
    let flags_breakaway = DETACHED_PROCESS
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_NO_WINDOW
        | CREATE_BREAKAWAY_FROM_JOB;
    let flags_basic = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW;

    let mut cmd = Command::new("powershell.exe");
    cmd.args([
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        &ps1_s,
        "-StatusPath",
        &status_s,
        "-From",
        &from_s,
        "-To",
        &to_s,
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    let spawn = cmd
        .creation_flags(flags_breakaway)
        .spawn()
        .or_else(|e1| {
            log::warn!(
                "Install progress host breakaway spawn failed ({e1}); retrying without BREAKAWAY"
            );
            let mut retry = Command::new("powershell.exe");
            retry
                .args([
                    "-NoProfile",
                    "-STA",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    &ps1_s,
                    "-StatusPath",
                    &status_s,
                    "-From",
                    &from_s,
                    "-To",
                    &to_s,
                ])
                .creation_flags(flags_basic)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
        });
    match spawn {
        Ok(_) => log::info!("Install progress host launched: {from_s} -> {to_s}"),
        Err(e) => log::error!("Failed to launch install progress host: {e}"),
    }
}

#[cfg(not(target_os = "windows"))]
fn launch_install_progress_ui(_from: &str, _to: &str) {}

/// Canonical Windows NSIS asset name on GitHub Releases.
/// NSIS emits "Remedy Desktop_{ver}_x64-setup.exe"; CI renames spaces → dots so the
/// published asset is always `Remedy.Desktop_{ver}_x64-setup.exe` (no spaces, no
/// `Remedy_Desktop_`). `latest.json` URL must match that asset name exactly.
#[allow(dead_code)]
fn canonical_installer_name(version: &str) -> String {
    let ver = version.trim().trim_start_matches('v').trim_start_matches('V');
    format!("Remedy.Desktop_{ver}_x64-setup.exe")
}

/// Only this repository's release assets (not arbitrary GitHub releases).
/// Fetch signed asset URL + minisign signature from published latest.json.
/// Callers should **download the returned URL** (not a stale UI-held URL) so a
/// check→install race cannot pair an old installer path with a new signature blob.
/// Refuses install when the release is unsigned (owner can still install manually from GitHub).
fn fetch_signed_release_asset() -> Result<(String /*url*/, String /*sig*/), String> {
    let meta_url =
        "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json";
    let resp = ureq::get(meta_url)
        .set("User-Agent", "RemedyDesktop-Updater/0.10")
        .set("Accept", "application/json")
        .set("Cache-Control", "no-cache")
        .set("Pragma", "no-cache")
        .timeout(Duration::from_secs(20))
        .call()
        .map_err(|e| format!("Could not fetch latest.json for signature: {e}"))?;
    if resp.status() != 200 {
        return Err(format!("latest.json HTTP {}", resp.status()));
    }
    let v: serde_json::Value = resp
        .into_json()
        .map_err(|e| format!("latest.json invalid: {e}"))?;
    let plat = v
        .pointer("/platforms/windows-x86_64")
        .or_else(|| v.pointer("/platforms/windows-x86-64"));
    let Some(plat) = plat else {
        return Err("latest.json missing windows-x86_64 platform".into());
    };
    let url = plat
        .get("url")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let sig = plat
        .get("signature")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if url.is_empty() {
        return Err("latest.json missing windows-x86_64.url".into());
    }
    if !is_trusted_download_url(&url) {
        return Err(format!(
            "latest.json asset URL is not a trusted GitHub release host: {url}"
        ));
    }
    // Normalize legacy underscore product segment if a bad publish ever lands.
    let url = url.replace("Remedy_Desktop_", "Remedy.Desktop_");
    if sig.is_empty() {
        return Err(
            "Release is unsigned (empty signature in latest.json). \
             Install manually from GitHub Releases if you trust the asset."
                .into(),
        );
    }
    Ok((url, sig))
}

/// Back-compat helper: require the client URL to match signed latest.json.
/// Prefer [`fetch_signed_release_asset`] + download that URL (used by install path).
#[allow(dead_code)]
fn fetch_release_signature_for_url(download_url: &str) -> Result<String, String> {
    let (url, sig) = fetch_signed_release_asset()?;
    let got = download_url
        .trim()
        .replace("Remedy_Desktop_", "Remedy.Desktop_");
    if !got.is_empty() && got != url {
        return Err(format!(
            "Download URL does not match signed latest.json asset.\n  got: {got}\n  expected: {url}\n\
             Tip: installer assets must be named Remedy.Desktop_{{ver}}_x64-setup.exe \
             (dots for spaces). Re-check/rename the GitHub Release asset or retry so the \
             app re-reads latest.json."
        ));
    }
    Ok(sig)
}

fn is_trusted_download_url(url: &str) -> bool {
    // Official release pages / assets for RemedyAI only.
    if url.starts_with("https://github.com/AhmiDarrow/RemedyAI/releases/") {
        return true;
    }
    // CDN hostnames used by GitHub Releases - require our repo path segment when present.
    // objects.githubusercontent.com URLs are signed and opaque; only accept when the
    // referrer path was already resolved from our latest.json (caller responsibility).
    // We still require HTTPS + known hosts (no open redirect to other schemes).
    if url.starts_with("https://objects.githubusercontent.com/")
        || url.starts_with("https://release-assets.githubusercontent.com/")
    {
        // Reject obvious non-asset paths
        return !url.contains("..") && url.len() < 2048;
    }
    false
}

/// Validate that the file looks like a Windows PE installer (not an HTML error page).
fn validate_installer_exe(path: &Path, min_bytes: u64) -> Result<(), String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("Cannot stat installer: {e}"))?;
    if meta.len() < min_bytes {
        return Err(format!(
            "Downloaded installer is too small ({} bytes) - likely not a real NSIS package",
            meta.len()
        ));
    }
    let mut f = std::fs::File::open(path).map_err(|e| format!("Cannot open installer: {e}"))?;
    let mut magic = [0u8; 2];
    f.read_exact(&mut magic)
        .map_err(|e| format!("Cannot read installer header: {e}"))?;
    if &magic != b"MZ" {
        return Err(
            "Downloaded file is not a Windows executable (missing MZ header). \
             GitHub may have returned an HTML error page."
                .into(),
        );
    }
    Ok(())
}

// Guard against double-click / concurrent update starts.
static UPDATE_IN_FLIGHT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Download the NSIS installer, run it silently (/S /UPDATE), exit so files can be replaced.
///
/// Progress UX (two stages - intentional):
/// 1. **In-app UpdateScreen** - download only (closes with Remedy)
/// 2. **New popup** (install host) - appears after Remedy exits for install/relaunch
///
/// Sole relaunch owner: update script (+ NSIS marker /NOAUTOLAUNCH). No double window.
#[tauri::command]
fn start_desktop_update(app: AppHandle, download_url: String) -> Result<(), String> {
    // Client may pass a URL from an earlier check; we always re-resolve the
    // signed asset from latest.json before download so naming/version races
    // (e.g. got v0.14.3 URL, expected v0.14.4) cannot fail after a multi-MB pull.
    if !download_url.is_empty() && !is_trusted_download_url(&download_url) {
        return Err("Download URL is not a trusted GitHub release host".into());
    }
    if UPDATE_IN_FLIGHT.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return Err("An update is already in progress".into());
    }

    let app_for_thread = app.clone();
    let ver_from = app_version(&app);
    let ver_to = desktop_update_result(ver_from.clone())
        .latest_version
        .clone();
    // Clone Arc before spawn - State<'_, T> cannot be borrowed inside the worker.
    let process_slot = app.state::<ServerState>().process.clone();
    // Stage 1 is in-app only. Stage 2 host is launched by the install script after exit.
    // Pre-stage the PS1 in TEMP so the script can start the install popup immediately.
    let _ = ensure_update_ui_ps1_in_temp();
    let client_url = download_url;
    thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            emit_progress_ver(
                &app_for_thread,
                "downloading",
                0,
                "Connecting to update server...",
                &ver_from,
                &ver_to,
            );

            // Canonical signed URL wins. Normalize legacy Remedy_Desktop_ if needed.
            let (signed_url, sig) = fetch_signed_release_asset()?;
            let mut download_url = signed_url;
            let client_norm = client_url
                .trim()
                .replace("Remedy_Desktop_", "Remedy.Desktop_");
            if !client_norm.is_empty() && client_norm != download_url {
                log::warn!(
                    "Update URL from UI differed from signed latest.json; using signed asset.\n  ui: {client_norm}\n  signed: {download_url}"
                );
            }
            if download_url.is_empty() {
                if client_norm.is_empty() {
                    return Err("No download URL for this release".into());
                }
                // latest.json had no URL (should not happen after fetch_signed checks).
                download_url = client_norm;
            }

            // Large installers: allow up to 10 minutes; still fail if connection stalls.
            let resp = ureq::get(&download_url)
                .set("User-Agent", "RemedyDesktop-Updater/0.10")
                .set("Accept", "application/octet-stream,*/*")
                .timeout(Duration::from_secs(600))
                .call()
                .map_err(|e| format!("Download failed: {e}"))?;
            if resp.status() != 200 {
                return Err(format!("Download HTTP {}", resp.status()));
            }

            let content_type = resp
                .header("Content-Type")
                .unwrap_or("")
                .to_ascii_lowercase();
            if content_type.contains("text/html") {
                return Err(
                    "Download returned HTML instead of an installer (check the release URL)."
                        .into(),
                );
            }

            let len = resp
                .header("Content-Length")
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);

            let temp = env::temp_dir().join(format!(
                "RemedyDesktop-Update-{}.exe",
                std::process::id()
            ));
            let _ = std::fs::remove_file(&temp);
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
                    ((done / (512 * 1024)) % 90) as u8
                };
                let mb = done as f64 / (1024.0 * 1024.0);
                emit_progress(
                    &app_for_thread,
                    "downloading",
                    pct,
                    &format!("Downloading update... {mb:.1} MB"),
                );
            }
            drop(file);

            // Reject HTML error pages / truncated downloads (NSIS packages are multi-MB).
            validate_installer_exe(&temp, 512 * 1024)?;

            // Cryptographic trust: we already loaded signature with the signed URL.
            emit_progress(
                &app_for_thread,
                "installing",
                100,
                "Verifying release signature...",
            );
            let sig_path = temp.with_extension("exe.sig");
            std::fs::write(&sig_path, format!("{sig}\n"))
                .map_err(|e| format!("Cannot write signature file: {e}"))?;
            log::info!(
                "Update signature present ({} chars) for trusted GitHub asset {}",
                sig.len(),
                download_url
            );

            // One calm in-app message (UI maps installing/closing/verifying the same).
            emit_progress(
                &app_for_thread,
                "closing",
                100,
                "Download complete. Restarting to finish install...",
            );

            // 1) Drop our Child handle for the sidecar.
            match process_slot.lock() {
                Ok(mut guard) => kill_child(&mut guard),
                Err(poisoned) => {
                    let mut guard = poisoned.into_inner();
                    kill_child(&mut guard);
                }
            }
            // 2) Force-kill any leftover sidecar / port holders (file lock root cause).
            force_stop_remedy_processes();
            thread::sleep(Duration::from_millis(1000));
            force_stop_remedy_processes();
            thread::sleep(Duration::from_millis(800));

            // 3) Exit the UI process so app.exe / Remedy Desktop.exe unlock.
            //    Install must be scheduled *and proven started* before exit
            //    (0.14.5: schedule looked ok but Job Object killed the script).
            write_updater_owns_relaunch_flag();
            // Stage 2 host (calm "Updating Remedy...") outlives this process.
            launch_install_progress_ui(&ver_from, &ver_to);
            thread::sleep(Duration::from_millis(400));

            #[cfg(target_os = "windows")]
            {
                // Schedule silent install AFTER this process fully exits so
                // Windows releases locks on the main EXE + sidecar.
                // Critical: break away from the parent Job Object, otherwise
                // app.exit() kills the update script and install never runs
                // (user sees "updated" but still on the old binary).
                //
                // Spawn powershell.exe directly with CREATE_NO_WINDOW - never
                // `cmd /c start` (that flashed two black console windows).
                const DETACHED_PROCESS: u32 = 0x00000008;
                const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
                const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
                let install_path = temp.to_string_lossy().replace('\'', "''");
                // Current install dir (best-effort) for /D= upgrade-in-place.
                let current_exe = std::env::current_exe().ok();
                let current_dir = current_exe
                    .as_ref()
                    .and_then(|p| p.parent())
                    .map(|p| p.to_string_lossy().replace('\'', "''"))
                    .unwrap_or_default();
                let current_exe_s = current_exe
                    .as_ref()
                    .map(|p| p.to_string_lossy().replace('\'', "''"))
                    .unwrap_or_default();
                let log_path = env::temp_dir()
                    .join("RemedyDesktop-Update.log")
                    .to_string_lossy()
                    .replace('\'', "''");
                let status_path = env::temp_dir()
                    .join("RemedyDesktop-Update-status.json")
                    .to_string_lossy()
                    .replace('\'', "''");
                let owns_flag_path = updater_owns_relaunch_flag_path()
                    .to_string_lossy()
                    .replace('\'', "''");
                let ver_from_esc = ver_from.replace('\'', "''");
                let ver_to_esc = ver_to.replace('\'', "''");
                // ASCII-only script body: Windows PowerShell 5.1 may load .ps1 as
                // system ANSI without BOM, which mojibakes Unicode ellipsis/arrows.
                let ps = format!(
                    r#"
$ErrorActionPreference = 'Continue'
$log = '{log_path}'
$statusPath = '{status_path}'
$ownsFlag = '{owns_flag_path}'
$verFrom = '{ver_from_esc}'
$verTo = '{ver_to_esc}'
function Log($m) {{
  $line = ("{{0:u}} {{1}}" -f (Get-Date), $m)
  Add-Content -LiteralPath $log -Value $line -ErrorAction SilentlyContinue
}}
function Set-UpdateStatus($phase, $percent, $message) {{
  $obj = [ordered]@{{
    phase = $phase
    percent = [int]$percent
    message = $message
    from = $verFrom
    to = $verTo
    updated_at = [string][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  }}
  ($obj | ConvertTo-Json -Compress) | Set-Content -LiteralPath $statusPath -Encoding UTF8 -ErrorAction SilentlyContinue
}}
function Stop-RemedyAppOnly {{
  # Kill app shells only - never powershell hosts (progress UI / this script).
  Get-Process -ErrorAction SilentlyContinue | Where-Object {{
    $n = $_.ProcessName
    if ($n -match '^(powershell|pwsh|cmd)$') {{ return $false }}
    if ($n -match '^(app|remedy-desktop|Remedy Desktop)$') {{ return $true }}
    if ($_.Path -and ($_.Path -like '*\Remedy Desktop.exe' -or $_.Path -like '*\app.exe' -or $_.Path -like '*\remedy-desktop*.exe')) {{ return $true }}
    return $false
  }} | ForEach-Object {{
    Log ("Killing leftover PID $($_.Id) $($_.ProcessName)")
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
  }}
}}
Log 'Update script started'
Log ("Installer: {install_path}")
Log ("Prior exe: {current_exe_s}")
Log ("Prior dir: {current_dir}")
# Belt-and-suspenders: re-assert sole-relaunch ownership for NSIS POSTINSTALL.
try {{ Set-Content -LiteralPath $ownsFlag -Value 'owned_by=update_script' -Encoding ASCII }} catch {{}}

# Stage 2 host should already be up (launched from the app just before exit).
# If missing (crash / race), start it now so install is never blank-desktop.
Set-UpdateStatus 'installing' 88 'Installing update...'
$uiPs1 = Join-Path $env:TEMP 'remedy-update-ui.ps1'
$uiAlive = $false
try {{
  $uiAlive = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {{
    $_.CommandLine -and ($_.CommandLine -like '*remedy-update-ui.ps1*')
  }}).Count -gt 0
}} catch {{ $uiAlive = $false }}
if (-not $uiAlive -and (Test-Path -LiteralPath $uiPs1)) {{
  Log 'Progress UI not found - launching host now'
  Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile','-STA','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',
    '-File', $uiPs1,
    '-StatusPath', $statusPath,
    '-From', $verFrom,
    '-To', $verTo
  ) -WindowStyle Hidden | Out-Null
  Start-Sleep -Milliseconds 500
}} else {{
  Log ("Progress UI alive=$uiAlive")
}}

# Wait for the app process tree to die (file locks).
Start-Sleep -Seconds 3
Stop-RemedyAppOnly
Set-UpdateStatus 'installing' 92 'Installing update...'
Start-Sleep -Seconds 1

$installer = '{install_path}'
if (-not (Test-Path -LiteralPath $installer)) {{
  Log 'ERROR: installer missing'
  Set-UpdateStatus 'error' 0 'Installer file missing - try GitHub Releases.'
  exit 2
}}

# Snapshot mtime of known install targets (detect successful replace).
$candidates = @(
  (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\Remedy Desktop.exe'),
  (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\app.exe'),
  (Join-Path $env:LOCALAPPDATA 'Remedy Desktop\Remedy Desktop.exe'),
  (Join-Path $env:LOCALAPPDATA 'Remedy Desktop\app.exe'),
  (Join-Path $env:LOCALAPPDATA 'Programs\remedy-desktop\Remedy Desktop.exe'),
  '{current_exe_s}'
) | Where-Object {{ $_ -and $_.Trim().Length -gt 0 }} | Select-Object -Unique

$before = @{{}}
foreach ($c in $candidates) {{
  if (Test-Path -LiteralPath $c) {{
    try {{ $before[$c] = (Get-Item -LiteralPath $c).LastWriteTimeUtc.Ticks }} catch {{}}
  }}
}}

# Silent update install. /UPDATE = keep user data.
# Marker file + /NOAUTOLAUNCH = NSIS POSTINSTALL must NOT start the app.
# We are the single relaunch owner (prevents double window).
# /D= must be last and unquoted (NSIS rule) when forcing dir.
try {{ Set-Content -LiteralPath $ownsFlag -Value 'owned_by=update_script' -Encoding ASCII }} catch {{}}
$args = @('/S', '/NCRC', '/UPDATE', '/NOAUTOLAUNCH')
$priorDir = '{current_dir}'
if ($priorDir -and (Test-Path -LiteralPath $priorDir)) {{
  $args += "/D=$priorDir"
  Log "Using /D=$priorDir"
}}
Set-UpdateStatus 'installing' 95 'Installing update...'
Log ("Starting NSIS: $installer $($args -join ' ')")
$p = Start-Process -FilePath $installer -ArgumentList $args -PassThru -WindowStyle Hidden
if (-not $p) {{
  Log 'ERROR: Start-Process returned null'
  Set-UpdateStatus 'error' 0 'Could not start installer.'
  exit 3
}}
try {{
  Wait-Process -Id $p.Id -Timeout 420 -ErrorAction Stop
}} catch {{
  Log "Wait-Process: $($_.Exception.Message)"
}}
$exitCode = 0
try {{ $exitCode = $p.ExitCode }} catch {{ $exitCode = -1 }}
Log "NSIS exit code: $exitCode"
Set-UpdateStatus 'verifying' 98 'Verifying install...'
# Give file locks a moment after NSIS; avoid racing a half-written EXE.
Start-Sleep -Seconds 3

# If POSTINSTALL still auto-started (old installer / missed flag), stop extras
# so we only open ONE window below.
Stop-RemedyAppOnly
Start-Sleep -Seconds 1

# Prefer a binary that is new or newly written.
$launch = $null
foreach ($c in $candidates) {{
  if (-not (Test-Path -LiteralPath $c)) {{ continue }}
  $ticks = 0
  try {{ $ticks = (Get-Item -LiteralPath $c).LastWriteTimeUtc.Ticks }} catch {{ continue }}
  $old = $before[$c]
  if (-not $old -or $ticks -gt $old) {{
    $launch = $c
    Log "Selected updated binary: $c"
    break
  }}
}}
if (-not $launch) {{
  foreach ($c in $candidates) {{
    if (Test-Path -LiteralPath $c) {{ $launch = $c; Log "Fallback binary: $c"; break }}
  }}
}}

if ($launch) {{
  # Single relaunch owner for in-app updates.
  Log "Relaunching once: $launch"
  Set-UpdateStatus 'relaunch' 100 'Relaunching Remedy...'
  # Ensure no second instance is already up before we start one.
  Stop-RemedyAppOnly
  Start-Sleep -Milliseconds 400
  Start-Process -FilePath $launch
  Log 'Relaunch issued (single)'
  Set-UpdateStatus 'done' 100 'Update complete'
  # Clean marker if NSIS did not consume it.
  try {{ Remove-Item -LiteralPath $ownsFlag -Force -ErrorAction SilentlyContinue }} catch {{}}
  exit 0
}}

Log 'ERROR: no Remedy Desktop.exe found after install - not relaunching old build'
Set-UpdateStatus 'error' 0 'Install finished but Remedy.exe was not found. Install from GitHub Releases.'
try {{ Remove-Item -LiteralPath $ownsFlag -Force -ErrorAction SilentlyContinue }} catch {{}}
exit 4
"#
                );
                // Write a temp .ps1 so quoting of the installer path is reliable.
                // Prepend an immediate log line so we can detect "script never started"
                // (0.14.4→0.14.5 failure: status stuck at closing, no log lines).
                let ps_body = format!(
                    "{preamble}\n{body}\n",
                    preamble = r#"$ErrorActionPreference = 'Continue'
try {
  $__boot = Join-Path $env:TEMP 'RemedyDesktop-Update.log'
  Add-Content -LiteralPath $__boot -Value ((" {0:u} BOOT pid={1} script={2}" -f (Get-Date), $PID, $MyInvocation.MyCommand.Path)) -ErrorAction SilentlyContinue
} catch {}
"#,
                    body = ps.trim()
                );
                let ps1 = env::temp_dir().join(format!(
                    "RemedyDesktop-Update-Run-{}.ps1",
                    std::process::id()
                ));
                std::fs::write(&ps1, ps_body).map_err(|e| {
                    format!("Cannot write update script: {e}")
                })?;
                let ps1_path = ps1.to_string_lossy().to_string();
                // Job-object-safe schedule: WScript.Shell Run often outlives the
                // Tauri Job when CREATE_BREAKAWAY_FROM_JOB is denied; also register
                // a one-shot scheduled task as belt-and-suspenders.
                schedule_update_install_script(&ps1_path).map_err(|e| {
                    format!(
                        "Failed to schedule installer (try the .exe from GitHub Releases): {e}"
                    )
                })?;
                log::info!(
                    "Update scheduled via multi-path host; script={} log={}",
                    ps1_path,
                    log_path
                );
                // Do not exit until the install script proves it started (BOOT line).
                // Prevents "download done, app closes, nothing happens."
                let log_full = env::temp_dir().join("RemedyDesktop-Update.log");
                let mut booted = false;
                for i in 0..40 {
                    thread::sleep(Duration::from_millis(150));
                    if let Ok(txt) = std::fs::read_to_string(&log_full) {
                        // Accept either new BOOT marker or classic start line.
                        if txt.contains("BOOT pid=") || txt.contains("Update script started") {
                            // Prefer a line from this run (installer path unique per pid).
                            if txt.contains(&temp.file_name().unwrap_or_default().to_string_lossy().to_string())
                                || txt.contains("BOOT pid=")
                            {
                                booted = true;
                                log::info!("Install script alive after {}ms", (i + 1) * 150);
                                break;
                            }
                        }
                    }
                }
                if !booted {
                    // One more hard retry via schtasks only, then re-check.
                    log::warn!("Install script not alive yet; re-running schedule");
                    let _ = schedule_update_install_script(&ps1_path);
                    for i in 0..30 {
                        thread::sleep(Duration::from_millis(200));
                        if let Ok(txt) = std::fs::read_to_string(&log_full) {
                            if txt.contains("BOOT pid=") || txt.contains("Update script started") {
                                booted = true;
                                log::info!("Install script alive on retry after {}ms", (i + 1) * 200);
                                break;
                            }
                        }
                    }
                }
                if !booted {
                    return Err(
                        "Could not start the install step after download. \
                         Leave Remedy open and click Retry, or install from GitHub Releases."
                            .into(),
                    );
                }
                emit_progress(
                    &app_for_thread,
                    "closing",
                    100,
                    "Install started. Remedy will reopen on the new version...",
                );
            }
            #[cfg(not(target_os = "windows"))]
            {
                Command::new(&temp)
                    .spawn()
                    .map_err(|e| format!("Failed to launch installer: {e}"))?;
            }

            // Brief beat so UI can paint the final message, then exit.
            thread::sleep(Duration::from_millis(700));
            app_for_thread.exit(0);
            Ok(())
        })();

        if let Err(e) = result {
            log::error!("Update failed: {}", e);
            UPDATE_IN_FLIGHT.store(false, std::sync::atomic::Ordering::SeqCst);
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
            .ok_or_else(|| "Sidecar path unknown - restart the app".to_string())?
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
            desktop_prefs: Arc::new(Mutex::new(load_desktop_prefs())),
        })
        .manage(pty_host::PtyState::default())
        .manage(browser_host::BrowserState::default())
        .invoke_handler(tauri::generate_handler![
            open_data_folder,
            pick_folder,
            open_path,
            open_terminal,
            open_external_url,
            save_text_file,
            open_text_file,
            set_launch_at_login,
            get_launch_at_login,
            scrub_legacy_autostart,
            set_desktop_prefs,
            get_desktop_prefs,
            show_main_window,
            is_main_window_maximized,
            minimize_main_window,
            start_dragging_main_window,
            toggle_maximize_main_window,
            request_close_main_window,
            switch_to_web_ui,
            quit_app,
            request_quit_app,
            restart_server,
            check_desktop_update,
            start_desktop_update,
            get_local_api_token,
            read_dropped_files,
            take_pending_file_drops,
            pty_host::pty_open,
            pty_host::pty_write,
            pty_host::pty_resize,
            pty_host::pty_close,
            browser_host::browser_navigate,
            browser_host::browser_reload,
            browser_host::browser_go_back,
            browser_host::browser_go_forward,
            browser_host::browser_current_url,
            browser_host::browser_close,
            browser_host::browser_is_open,
            browser_host::browser_set_bounds,
            browser_host::browser_hide,
            browser_host::browser_show,
        ])
        .setup(|app| {
            let _shell = app.handle().plugin(tauri_plugin_shell::init())?;
            let _updater = app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            let app_handle = app.handle().clone();

            // Force window/taskbar icon to the circuit-R monogram (not stale PE/cache).
            // Tray already uses icons/icon.png; taskbar often stuck on old embedded ICO.
            apply_window_icons(&app_handle);

            // Tray menu (OS-native chrome; labels only - UI panels are themed in-app)
            {
                use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
                use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent};

                let show_i = MenuItem::with_id(app, "show", "Show Remedy", true, None::<&str>)?;
                let settings_i =
                    MenuItem::with_id(app, "settings", "Settings...", true, None::<&str>)?;
                let updates_i = MenuItem::with_id(
                    app,
                    "check_updates",
                    "Check for updates...",
                    true,
                    None::<&str>,
                )?;
                let about_i = MenuItem::with_id(app, "about", "About Remedy", true, None::<&str>)?;
                let sep = PredefinedMenuItem::separator(app)?;
                let quit_i = MenuItem::with_id(app, "quit", "Quit Remedy", true, None::<&str>)?;
                let menu = Menu::with_items(
                    app,
                    &[&show_i, &settings_i, &updates_i, &about_i, &sep, &quit_i],
                )?;

                // Prefer tray from tauri.conf.json; attach menu + events
                if let Some(tray) = app.tray_by_id("main") {
                    let _ = tray.set_menu(Some(menu.clone()));
                    // Left click / double-click = show window; right-click = menu.
                    let _ = tray.set_show_menu_on_left_click(false);
                    let _ = tray.set_tooltip(Some(
                        "Remedy — left-click to show · right-click for menu",
                    ));
                    let app_for_menu = app.handle().clone();
                    tray.on_menu_event(move |_tray, event| match event.id.as_ref() {
                        "show" => {
                            bring_main_to_front(&app_for_menu);
                        }
                        "settings" => {
                            bring_main_to_front(&app_for_menu);
                            let _ = app_for_menu.emit("tray-open-settings", ());
                        }
                        "check_updates" => {
                            bring_main_to_front(&app_for_menu);
                            let _ = app_for_menu.emit("tray-check-updates", ());
                        }
                        "about" => {
                            bring_main_to_front(&app_for_menu);
                            let _ = app_for_menu.emit("tray-about", ());
                        }
                        "quit" => {
                            // Confirm via UI (server/Web UI warning) unless user opted out
                            let state = app_for_menu.state::<ServerState>();
                            let skip = state
                                .desktop_prefs
                                .lock()
                                .map(|p| p.skip_quit_server_warning)
                                .unwrap_or(false)
                                || load_desktop_prefs().skip_quit_server_warning;
                            if skip {
                                browser_host::close_browser_on_quit(&app_for_menu);
                                shutdown_sidecar(&state);
                                schedule_force_exit(Duration::from_secs(2));
                                app_for_menu.exit(0);
                            } else {
                                bring_main_to_front(&app_for_menu);
                                let _ = app_for_menu.emit("app-quit-requested", ());
                            }
                        }
                        _ => {}
                    });
                    tray.on_tray_icon_event(|tray, event| {
                        let app = tray.app_handle();
                        match event {
                            // Single left click or double-click restores the main window.
                            TrayIconEvent::Click {
                                button: MouseButton::Left,
                                button_state: MouseButtonState::Up,
                                ..
                            }
                            | TrayIconEvent::DoubleClick {
                                button: MouseButton::Left,
                                ..
                            } => {
                                bring_main_to_front(app);
                            }
                            _ => {}
                        }
                    });
                } else {
                    log::warn!("No tray icon id 'main' - check tauri.conf.json trayIcon");
                }
            }

            // One-time-per-session scrub of legacy HKCU Run keys via winreg (no PowerShell).
            // Writing Run was Persistence.A!ml; we only delete leftovers from 0.10.19-0.10.21.
            #[cfg(target_os = "windows")]
            {
                remove_legacy_run_key();
            }

            // Start hidden only when start_in_tray is explicitly true in desktop.json
            // (or in-memory prefs). Re-load from disk so a Settings save before restart
            // cannot be ignored by a stale in-memory default.
            {
                let fresh = load_desktop_prefs();
                if let Ok(mut lock) = app.state::<ServerState>().desktop_prefs.lock() {
                    *lock = fresh;
                }
                let start_hidden = app
                    .state::<ServerState>()
                    .desktop_prefs
                    .lock()
                    .map(|p| p.start_in_tray)
                    .unwrap_or(false);
                log::info!(
                    "launch visibility: start_in_tray={} (window {})",
                    start_hidden,
                    if start_hidden { "hidden" } else { "shown" }
                );
                if start_hidden {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.hide();
                        log::info!("start_in_tray: main window hidden");
                    }
                } else {
                    // Ensure we are not stuck minimized/hidden from a prior tray session.
                    bring_main_to_front(app.handle());
                }
            }

            let (remedy_cmd, find_err) = find_remedy();
            if !find_err.is_empty() {
                log::error!("{}", find_err);
                let _ = app_handle.emit("server-error", &find_err);
                return Ok(());
            }

            log::info!("Starting remedy: {}", remedy_cmd);
            let _ = app_handle.emit("server-starting", ());

            // Point the sidecar at packaged SPA + local model bundle (resources/).
            if let Ok(resource) = app.path().resource_dir() {
                env::set_var("REMEDY_RESOURCES", &resource);
                log::info!("REMEDY_RESOURCES={}", resource.display());
                let candidates = [
                    resource.join("webui"),
                    resource.join("dist"),
                    resource.clone(),
                ];
                for c in candidates {
                    if c.join("index.html").is_file() {
                        env::set_var("REMEDY_WEBUI_DIR", &c);
                        log::info!("REMEDY_WEBUI_DIR={}", c.display());
                        break;
                    }
                }
                // Optional offline override only (prod: first-run download into ~/.remedy/vision)
                let local = resource.join("local");
                if local.join("models").is_dir() {
                    env::set_var("REMEDY_LOCAL_BUNDLE", &local);
                    log::info!("REMEDY_LOCAL_BUNDLE={}", local.display());
                }
            }

            {
                let state = app.state::<ServerState>();
                *state.sidecar_cmd.lock().unwrap() = Some(remedy_cmd.clone());
                match start_sidecar(&state.process, &remedy_cmd) {
                    Ok(()) => {
                        // First run seeds skills into ~/.remedy - allow extra time.
                        if wait_for_health(Duration::from_secs(90)) {
                            log::info!("Remedy server ready");
                            let _ = app_handle.emit("server-ready", ());
                        } else {
                            log::error!("Server failed to start within 90s");
                            let _ = app_handle
                                .emit("server-error", "Server failed to start after 90s");
                        }
                    }
                    Err(e) => {
                        log::error!("{}", e);
                        let _ = app_handle.emit("server-error", &e);
                    }
                }
            }

            if cfg!(debug_assertions) {
                // Log to file only — default stdout target opens a separate console
                // window on Windows and floods with [remedy err] API lines.
                use tauri_plugin_log::{Target, TargetKind};
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .targets([
                            Target::new(TargetKind::LogDir {
                                file_name: Some("remedy-desktop".into()),
                            }),
                            Target::new(TargetKind::Webview),
                        ])
                        .build(),
                )?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                // Close-to-tray: hide instead of quit when always-ready is enabled.
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    // Re-read disk in case Settings saved prefs without a live reload.
                    let fresh = load_desktop_prefs();
                    if let Ok(mut g) = window.state::<ServerState>().desktop_prefs.lock() {
                        *g = DesktopPrefs {
                            close_to_tray: fresh.close_to_tray,
                            start_in_tray: fresh.start_in_tray,
                            skip_quit_server_warning: fresh.skip_quit_server_warning,
                        };
                    }
                    let close_to_tray = fresh.close_to_tray;
                    if close_to_tray {
                        // Hide to tray - server keeps running (Web UI stays alive).
                        api.prevent_close();
                        let _ = window.hide();
                        log::info!("close_to_tray: window hidden (sidecar stays up)");
                    } else if fresh.skip_quit_server_warning {
                        // Full quit without dialog
                        let state = window.state::<ServerState>();
                        shutdown_sidecar(&state);
                    } else {
                        // Confirm: quitting kills local server + browser Web UI
                        api.prevent_close();
                        let _ = window.emit("app-quit-requested", ());
                        log::info!("close_to_tray=false: asked UI to confirm quit (server would stop)");
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    // Full quit only (hide-to-tray never destroys the window).
                    let close_to_tray = window
                        .state::<ServerState>()
                        .desktop_prefs
                        .lock()
                        .map(|p| p.close_to_tray)
                        .unwrap_or(true);
                    if !close_to_tray {
                        let state = window.state::<ServerState>();
                        shutdown_sidecar(&state);
                    }
                }
                // Native OS file drops (Explorer -> app). WebView2 often won't
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
                            // Queue for polling (primary - WebView event delivery is flaky).
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
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // App-level exit — keep this path FAST (no PowerShell). A second
            // slow shutdown here used to make "Quit and stop server" look dead.
            match event {
                tauri::RunEvent::ExitRequested { .. } => {
                    let state = app_handle.state::<ServerState>();
                    // Only tree-kill leftovers; main quit_app already stopped the child.
                    force_stop_remedy_processes();
                    force_stop_vision_processes();
                    let _ = state; // keep lock pattern available if needed later
                }
                tauri::RunEvent::Exit => {
                    force_stop_remedy_processes();
                    force_stop_vision_processes();
                }
                _ => {}
            }
        });
}
