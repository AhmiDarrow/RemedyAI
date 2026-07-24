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
}

impl Default for DesktopPrefs {
    fn default() -> Self {
        Self {
            close_to_tray: false,
            start_in_tray: false,
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

fn load_desktop_prefs() -> DesktopPrefs {
    // Defaults: always-ready partner UX — close hides to tray (does not kill).
    let mut prefs = DesktopPrefs {
        close_to_tray: true,
        start_in_tray: false,
    };

    // 1) Prefer shell-owned desktop.json when present
    let desk = desktop_prefs_path();
    if let Ok(raw) = std::fs::read_to_string(&desk) {
        prefs.close_to_tray = raw.contains("\"close_to_tray\": true")
            || raw.contains("\"close_to_tray\":true");
        prefs.start_in_tray = raw.contains("\"start_in_tray\": true")
            || raw.contains("\"start_in_tray\":true");
        // Also accept false explicitly when file exists
        if raw.contains("\"close_to_tray\": false") || raw.contains("\"close_to_tray\":false") {
            prefs.close_to_tray = false;
        }
        if raw.contains("\"start_in_tray\": false") || raw.contains("\"start_in_tray\":false") {
            prefs.start_in_tray = false;
        }
        return prefs;
    }

    // 2) Fall back to config.toml (Settings writes here; desktop.json may be missing)
    if let Ok(raw) = std::fs::read_to_string(config_toml_path()) {
        if let Some(v) = toml_bool(&raw, "close_to_tray") {
            prefs.close_to_tray = v;
        }
        if let Some(v) = toml_bool(&raw, "start_in_tray") {
            prefs.start_in_tray = v;
        }
        // Seed desktop.json so CloseRequested and future launches stay in sync
        let _ = save_desktop_prefs(&prefs);
        log::info!(
            "desktop prefs seeded from config.toml (close_to_tray={}, start_in_tray={})",
            prefs.close_to_tray,
            prefs.start_in_tray
        );
    }
    prefs
}

fn save_desktop_prefs(prefs: &DesktopPrefs) -> Result<(), String> {
    let path = desktop_prefs_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let body = format!(
        "{{\n  \"close_to_tray\": {},\n  \"start_in_tray\": {}\n}}\n",
        if prefs.close_to_tray { "true" } else { "false" },
        if prefs.start_in_tray { "true" } else { "false" },
    );
    std::fs::write(&path, body).map_err(|e| e.to_string())
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

/// Stop the managed sidecar and any leftover remedy-desktop processes / :7400 listeners.
fn shutdown_sidecar(state: &ServerState) {
    match state.process.lock() {
        Ok(mut guard) => kill_child(&mut guard),
        Err(poisoned) => {
            let mut guard = poisoned.into_inner();
            kill_child(&mut guard);
        }
    }
    force_stop_remedy_processes();
    log::info!("Sidecar shutdown complete");
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

/// Native folder picker for Settings project workspace (Windows Forms / zenity / osascript).
#[tauri::command]
fn pick_folder() -> Result<Option<String>, String> {
    #[cfg(target_os = "windows")]
    {
        // PowerShell FolderBrowserDialog — no extra crate; works from Tauri main thread spawn.
        let script = r#"
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Select project folder'
$d.ShowNewFolderButton = $true
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Write-Output $d.SelectedPath
}
"#;
        let output = Command::new("powershell")
            .args(["-NoProfile", "-STA", "-Command", script])
            .output()
            .map_err(|e| format!("folder picker failed: {e}"))?;
        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            if err.trim().is_empty() {
                return Ok(None);
            }
            return Err(format!("folder picker error: {}", err.trim()));
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None);
        }
        return Ok(Some(path));
    }
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("osascript")
            .args([
                "-e",
                "POSIX path of (choose folder with prompt \"Select project folder\")",
            ])
            .output()
            .map_err(|e| format!("folder picker failed: {e}"))?;
        if !output.status.success() {
            return Ok(None);
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None);
        }
        return Ok(Some(path));
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let output = Command::new("zenity")
            .args(["--file-selection", "--directory", "--title=Select project folder"])
            .output()
            .map_err(|e| format!("folder picker failed (install zenity): {e}"))?;
        if !output.status.success() {
            return Ok(None);
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            return Ok(None);
        }
        return Ok(Some(path));
    }
    #[allow(unreachable_code)]
    Ok(None)
}

/// Startup-folder shortcut name (user-visible in Settings → Apps → Startup).
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

/// Remove legacy HKCU Run entries left by older Remedy builds (Defender false-positive source).
#[cfg(target_os = "windows")]
fn remove_legacy_run_key() {
    use std::os::windows::process::CommandExt;
    // Names used in 0.10.19–0.10.21
    let ps = r#"
$names = @('RemedyDesktop','Remedy Desktop','remedy-desktop')
foreach ($n in $names) {
  Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $n -ErrorAction SilentlyContinue
}
"#;
    let _ = Command::new("powershell")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

/// Windows: enable/disable "Start with Windows" via **Startup folder shortcut only**.
/// Never writes the registry Run key (avoids Persistence.A!ml false positives).
#[tauri::command]
fn set_launch_at_login(enabled: bool) -> Result<bool, String> {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // Always scrub legacy Run keys when toggling.
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
            // User-visible shortcut only — shows under Settings → Apps → Startup.
            let ps = format!(
                r#"
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{lnk}')
$s.TargetPath = '{exe}'
$s.WorkingDirectory = '{wd}'
$s.WindowStyle = 1
$s.Description = 'Remedy Desktop (optional Start with Windows — disable in Settings or Startup apps)'
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
            log::info!("Launch at login enabled via Startup folder → {}", lnk.display());
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
        // Migrate away from registry Run (one-shot cleanup on every status check).
        remove_legacy_run_key();
        let lnk = windows_startup_lnk_path();
        return Ok(lnk.is_file());
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(false)
    }
}

/// One-shot cleanup for Defender: remove legacy Run keys without enabling autostart.
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
) -> Result<(), String> {
    let prefs = DesktopPrefs {
        close_to_tray,
        start_in_tray,
    };
    save_desktop_prefs(&prefs)?;
    if let Ok(mut g) = state.desktop_prefs.lock() {
        *g = prefs;
    }
    Ok(())
}

#[tauri::command]
fn get_desktop_prefs(state: State<'_, ServerState>) -> Result<serde_json::Value, String> {
    let g = state
        .desktop_prefs
        .lock()
        .map_err(|_| "prefs lock poisoned".to_string())?;
    Ok(serde_json::json!({
        "close_to_tray": g.close_to_tray,
        "start_in_tray": g.start_in_tray,
    }))
}

#[tauri::command]
fn show_main_window(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    Ok(())
}

/// Reliable minimize from the custom title bar (avoids webview permission races).
#[tauri::command]
fn minimize_main_window(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        w.minimize().map_err(|e| format!("minimize failed: {e}"))?;
    }
    Ok(())
}

/// Maximize / restore from the custom title bar.
#[tauri::command]
fn toggle_maximize_main_window(app: AppHandle) -> Result<bool, String> {
    if let Some(w) = app.get_webview_window("main") {
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
        return w
            .is_maximized()
            .map_err(|e| format!("is_maximized failed: {e}"));
    }
    Ok(false)
}

/// Close button: hide to tray when enabled, otherwise quit (sidecar stopped via CloseRequested).
#[tauri::command]
fn request_close_main_window(
    app: AppHandle,
    state: State<'_, ServerState>,
) -> Result<(), String> {
    let close_to_tray = state
        .desktop_prefs
        .lock()
        .map(|p| p.close_to_tray)
        .unwrap_or(true);
    if let Some(w) = app.get_webview_window("main") {
        if close_to_tray {
            w.hide().map_err(|e| format!("hide failed: {e}"))?;
            log::info!("request_close_main_window: hidden to tray");
        } else {
            // Triggers CloseRequested → sidecar shutdown on full quit
            w.close().map_err(|e| format!("close failed: {e}"))?;
        }
    }
    Ok(())
}

/// Apply the current branding PNG as the window icon (taskbar / Alt-Tab).
/// `include_image!` embeds icons/icon.png (circuit-R) at compile time.
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

fn is_trusted_download_url(url: &str) -> bool {
    url.starts_with("https://github.com/AhmiDarrow/RemedyAI/")
        || url.starts_with("https://objects.githubusercontent.com/")
        || url.starts_with("https://release-assets.githubusercontent.com/")
        || (url.starts_with("https://github.com/") && url.contains("/releases/download/"))
}

/// Validate that the file looks like a Windows PE installer (not an HTML error page).
fn validate_installer_exe(path: &Path, min_bytes: u64) -> Result<(), String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("Cannot stat installer: {e}"))?;
    if meta.len() < min_bytes {
        return Err(format!(
            "Downloaded installer is too small ({} bytes) — likely not a real NSIS package",
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

/// Download the NSIS installer, run it silently (/S), exit so files can be replaced.
/// NSIS POSTINSTALL hook relaunches Remedy Desktop.
/// Progress is streamed to the UI via `update-progress` events.
#[tauri::command]
fn start_desktop_update(app: AppHandle, download_url: String) -> Result<(), String> {
    if download_url.is_empty() {
        return Err("No download URL for this release".into());
    }
    if !is_trusted_download_url(&download_url) {
        return Err("Download URL is not a trusted GitHub release host".into());
    }
    if UPDATE_IN_FLIGHT.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return Err("An update is already in progress".into());
    }

    let app_for_thread = app.clone();
    // Clone Arc before spawn — State<'_, T> cannot be borrowed inside the worker.
    let process_slot = app.state::<ServerState>().process.clone();
    thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            emit_progress(
                &app_for_thread,
                "downloading",
                0,
                "Connecting to update server…",
            );

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
                    &format!("Downloading update… {mb:.1} MB"),
                );
            }
            drop(file);

            // Reject HTML error pages / truncated downloads (NSIS packages are multi-MB).
            validate_installer_exe(&temp, 512 * 1024)?;

            emit_progress(
                &app_for_thread,
                "installing",
                100,
                "Stopping server and installing… app will relaunch.",
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

            // 3) Exit the UI process FIRST so app.exe / Remedy Desktop.exe unlock.
            //    Then the detached installer can overwrite install-dir files.
            //    (Launching NSIS while we still hold the main EXE caused
            //    "Can't write …\remedy-desktop.exe" / partial aborts.)
            emit_progress(
                &app_for_thread,
                "relaunch",
                100,
                "Closing Remedy and running installer…",
            );

            #[cfg(target_os = "windows")]
            {
                // Schedule silent install AFTER this process exits so Windows
                // releases locks on app.exe / Remedy Desktop.exe / sidecar.
                // PowerShell: wait longer, kill again, run NSIS /S, relaunch if
                // POSTINSTALL did not (belt-and-suspenders for failed hooks).
                // DETACHED_PROCESS keeps the scheduler alive after we exit.
                const DETACHED_PROCESS: u32 = 0x00000008;
                const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
                let install_path = temp.to_string_lossy().replace('\'', "''");
                // Prefer LocalAppData\Programs install path used by Tauri NSIS.
                let ps = format!(
                    r#"
$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 4
Get-Process -ErrorAction SilentlyContinue | Where-Object {{
  $_.ProcessName -match '^(app|remedy-desktop|Remedy Desktop)$' -or
  ($_.Path -and $_.Path -like '*Remedy Desktop*')
}} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$installer = '{install_path}'
if (-not (Test-Path -LiteralPath $installer)) {{
  exit 2
}}
$p = Start-Process -FilePath $installer -ArgumentList '/S','/NCRC' -PassThru -WindowStyle Hidden
if ($p) {{ Wait-Process -Id $p.Id -Timeout 300 -ErrorAction SilentlyContinue }}
Start-Sleep -Seconds 2
$candidates = @(
  (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\Remedy Desktop.exe'),
  (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\app.exe'),
  (Join-Path $env:LOCALAPPDATA 'Programs\remedy-desktop\Remedy Desktop.exe')
)
foreach ($c in $candidates) {{
  if (Test-Path -LiteralPath $c) {{
    Start-Process -FilePath $c
    break
  }}
}}
"#
                );
                // Write a temp .ps1 so quoting of the installer path is reliable.
                let ps1 = env::temp_dir().join(format!(
                    "RemedyDesktop-Update-Run-{}.ps1",
                    std::process::id()
                ));
                std::fs::write(&ps1, ps.trim()).map_err(|e| {
                    format!("Cannot write update script: {e}")
                })?;
                let ps1_path = ps1.to_string_lossy().replace('"', "");
                Command::new("powershell")
                    .args([
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-WindowStyle",
                        "Hidden",
                        "-File",
                        &ps1_path,
                    ])
                    .creation_flags(
                        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                    )
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .map_err(|e| {
                        format!(
                            "Failed to schedule installer (try running the .exe from the release page): {e}"
                        )
                    })?;
            }
            #[cfg(not(target_os = "windows"))]
            {
                Command::new(&temp)
                    .spawn()
                    .map_err(|e| format!("Failed to launch installer: {e}"))?;
            }

            // Exit immediately so file locks clear before the delayed installer runs.
            thread::sleep(Duration::from_millis(250));
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
            desktop_prefs: Arc::new(Mutex::new(load_desktop_prefs())),
        })
        .invoke_handler(tauri::generate_handler![
            open_data_folder,
            pick_folder,
            set_launch_at_login,
            get_launch_at_login,
            scrub_legacy_autostart,
            set_desktop_prefs,
            get_desktop_prefs,
            show_main_window,
            minimize_main_window,
            toggle_maximize_main_window,
            request_close_main_window,
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

            // Force window/taskbar icon to the circuit-R monogram (not stale PE/cache).
            // Tray already uses icons/icon.png; taskbar often stuck on old embedded ICO.
            apply_window_icons(&app_handle);

            // Tray menu (OS-native chrome; labels only — UI panels are themed in-app)
            {
                use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
                use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent};

                let show_i = MenuItem::with_id(app, "show", "Show Remedy", true, None::<&str>)?;
                let settings_i =
                    MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
                let updates_i = MenuItem::with_id(
                    app,
                    "check_updates",
                    "Check for updates…",
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
                    let _ = tray.set_tooltip(Some("Remedy — right-click for Settings"));
                    let app_for_menu = app.handle().clone();
                    tray.on_menu_event(move |_tray, event| match event.id.as_ref() {
                        "show" => {
                            if let Some(w) = app_for_menu.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                        }
                        "settings" => {
                            if let Some(w) = app_for_menu.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                            let _ = app_for_menu.emit("tray-open-settings", ());
                        }
                        "check_updates" => {
                            if let Some(w) = app_for_menu.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                            let _ = app_for_menu.emit("tray-check-updates", ());
                        }
                        "about" => {
                            if let Some(w) = app_for_menu.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                            let _ = app_for_menu.emit("tray-about", ());
                        }
                        "quit" => {
                            let state = app_for_menu.state::<ServerState>();
                            shutdown_sidecar(&state);
                            app_for_menu.exit(0);
                        }
                        _ => {}
                    });
                    tray.on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        } = event
                        {
                            let app = tray.app_handle();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                        }
                    });
                } else {
                    log::warn!("No tray icon id 'main' — check tauri.conf.json trayIcon");
                }
            }

            // Scrub legacy HKCU Run keys on every launch (Defender Persistence.A!ml mitigation).
            #[cfg(target_os = "windows")]
            {
                remove_legacy_run_key();
            }

            // Start hidden when always-ready start_in_tray is on
            {
                let state = app.state::<ServerState>();
                let start_hidden = state
                    .desktop_prefs
                    .lock()
                    .map(|p| p.start_in_tray)
                    .unwrap_or(false);
                if start_hidden {
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.hide();
                        log::info!("start_in_tray: main window hidden");
                    }
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
                // Close-to-tray: hide instead of quit when always-ready is enabled.
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    // Re-read disk in case Settings saved prefs without a live reload.
                    let fresh = load_desktop_prefs();
                    if let Ok(mut g) = window.state::<ServerState>().desktop_prefs.lock() {
                        *g = DesktopPrefs {
                            close_to_tray: fresh.close_to_tray,
                            start_in_tray: fresh.start_in_tray,
                        };
                    }
                    let close_to_tray = fresh.close_to_tray;
                    if close_to_tray {
                        api.prevent_close();
                        let _ = window.hide();
                        log::info!("close_to_tray: window hidden (sidecar stays up)");
                    } else {
                        let state = window.state::<ServerState>();
                        shutdown_sidecar(&state);
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
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // App-level exit (tray quit, process teardown) — window Destroyed may
            // not run if the process is exiting another way.
            match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                    let state = app_handle.state::<ServerState>();
                    shutdown_sidecar(&state);
                }
                _ => {}
            }
        });
}
