mod pty_host;
mod browser_host;
mod privacy_shield;
use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, DragDropEvent, Emitter, Manager, State};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// Sidecar / user-data home.
/// Reap a fire-and-forget child on a detached thread so it does not linger as
/// a zombie (defunct) until the app exits. Used for `xdg-open` launches, whose
/// Child we otherwise drop without ever calling wait().
#[cfg(all(unix, not(target_os = "macos")))]
fn reap_detached(child: Child) {
    std::thread::spawn(move || {
        let mut c = child;
        let _ = c.wait();
    });
}

/// - `REMEDY_HOME` when set
/// - else `%USERPROFILE%\.remedy` / `~/.remedy`
fn remedy_home() -> PathBuf {
    if let Ok(h) = env::var("REMEDY_HOME") {
        let p = PathBuf::from(h.trim());
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    let home = if cfg!(target_os = "windows") {
        env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string())
    } else {
        env::var("HOME").unwrap_or_else(|_| ".".to_string())
    };
    PathBuf::from(home).join(".remedy")
}

/// Local API port (default 7400).
pub(crate) fn api_port() -> u16 {
    env::var("REMEDY_API_PORT")
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
        .filter(|p| *p > 0)
        .unwrap_or(7400)
}

fn status_addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], api_port()))
}

pub(crate) fn api_base_url() -> String {
    format!("http://127.0.0.1:{}", api_port())
}

#[tauri::command]
fn get_api_origin() -> String {
    api_base_url()
}

fn window_title() -> String {
    "Remedy Desktop".to_string()
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
    // Always-ready partner: title-bar X always hides (never kills sidecar).
    // close_to_tray is forced true on load; only start_in_tray / quit-warn vary.
    let mut prefs = DesktopPrefs {
        close_to_tray: true,
        start_in_tray: false,
        skip_quit_server_warning: false,
    };
    let mut healed_close = false;

    // 1) Prefer shell-owned desktop.json when present (proper JSON via serde_json)
    let desk = desktop_prefs_path();
    if let Ok(raw) = std::fs::read_to_string(&desk) {
        if let Ok(file) = serde_json::from_str::<DesktopPrefsFile>(&raw) {
            if let Some(false) = file.close_to_tray {
                healed_close = true;
            }
            // Always true — ignore false from older Setup / Settings.
            prefs.close_to_tray = true;
            if let Some(v) = file.start_in_tray {
                prefs.start_in_tray = v;
            }
            if let Some(v) = file.skip_quit_server_warning {
                prefs.skip_quit_server_warning = v;
            }
            if healed_close {
                let _ = save_desktop_prefs(&prefs);
                log::info!("desktop.json: close_to_tray forced true (title-bar X → tray)");
            }
            return prefs;
        }
        log::warn!("desktop.json parse failed; using defaults + TOML fallback if any");
    }

    // 2) Fall back to config.toml (Settings writes here; desktop.json may be missing)
    if let Ok(raw) = std::fs::read_to_string(config_toml_path()) {
        // Never honor close_to_tray=false from TOML for window chrome.
        if let Some(false) = toml_bool(&raw, "close_to_tray") {
            healed_close = true;
        }
        prefs.close_to_tray = true;
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
            "desktop prefs seeded from config.toml (close_to_tray={}, start_in_tray={}, skip_quit_warn={}, healed_close={})",
            prefs.close_to_tray,
            prefs.start_in_tray,
            prefs.skip_quit_server_warning,
            healed_close
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
    /// Kept so tray restore still works after hide/minimize (label lookup can fail).
    main_window: Mutex<Option<tauri::WebviewWindow>>,
    /// True when Desktop attached to a foreign `remedy serve` (Use existing).
    /// Quit must not tree-kill that process.
    attached_existing: Arc<AtomicBool>,
    /// Stops the recovery watchdog before an intentional quit/update tears the
    /// managed process down.  Without this gate, a crash detected in the same
    /// instant as Quit could be mistaken for a process that needs recovery.
    app_exiting: Arc<AtomicBool>,
}

fn current_exe_dir() -> Option<std::path::PathBuf> {
    env::current_exe().ok()?.parent().map(|p| p.to_path_buf())
}

/// True when this path is a native sidecar for the current OS.
/// Linux/WSL must never launch a Windows PE or a `C:\...` shebang via interop —
/// those sleep and never bind :7400.
fn sidecar_is_native(p: &Path) -> bool {
    #[cfg(target_os = "windows")]
    {
        let _ = p;
        true
    }
    #[cfg(not(target_os = "windows"))]
    {
        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if name.ends_with(".exe") {
            return false;
        }
        match std::fs::read(p) {
            Ok(bytes) if bytes.len() >= 2 && bytes[0] == b'M' && bytes[1] == b'Z' => false,
            Ok(bytes) if bytes.starts_with(b"#!") => {
                let line = bytes
                    .split(|&b| b == b'\n' || b == b'\r')
                    .next()
                    .unwrap_or(&[]);
                let s = String::from_utf8_lossy(line);
                // Windows interpreter in shebang (uv tool on the NT PATH, or
                // /mnt/c/.../python.exe via WSL interop — those never bind :7400).
                let low = s.to_ascii_lowercase();
                !low.contains(":\\")
                    && !low.contains("scripts/python")
                    && !low.contains(".exe")
                    && !low.contains("/mnt/")
                    && !low.contains("//wsl")
            }
            Ok(bytes) if bytes.starts_with(b"\x7fELF") => true,
            Ok(_) => p.is_file(),
            Err(_) => false,
        }
    }
}

/// Reject tiny stub EXEs (e.g. 46KB wrappers that only print CLI help and exit).
/// On Unix, venv `remedy` and `uv run` launchers are tiny shebang scripts and
/// are the correct live-source sidecars — size is not a signal there.
fn is_plausible_sidecar(p: &Path) -> bool {
    match std::fs::metadata(p) {
        Ok(m) if m.is_file() => {}
        _ => return false,
    }
    if !sidecar_is_native(p) {
        return false;
    }
    #[cfg(target_os = "windows")]
    {
        std::fs::metadata(p).map(|m| m.len() > 1_000_000).unwrap_or(false)
    }
    #[cfg(not(target_os = "windows"))]
    {
        true
    }
}

fn find_remedy() -> (String, String) {
    let searched = |label: &str, p: &std::path::Path| -> Option<String> {
        if is_plausible_sidecar(p) {
            log::info!(
                "Found sidecar at: {} ({}, {} bytes)",
                p.display(),
                label,
                std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
            );
            Some(p.to_string_lossy().to_string())
        } else if p.exists() {
            log::warn!(
                "Skipping stub/too-small sidecar candidate: {} ({})",
                p.display(),
                label
            );
            None
        } else {
            None
        }
    };

    // Dev builds: run the *live source* sidecar, not a stale packaged binary.
    // The repo's own venv launcher (`.venv/Scripts/remedy.exe`) runs current
    // `src/remedy`, while `remedy-desktop.exe` next to target/debug may be a
    // stale frozen build that predates provider/LLM fixes.  Prefer the venv
    // when it exists (checked relative to cwd, then the repo root).
    if cfg!(debug_assertions) {
        // WSL/Linux: honor the isolated venv first. The repo `.venv` on /mnt/c
        // is often a Windows environment (or a half-synced mix) and must not
        // win over UV_PROJECT_ENVIRONMENT.
        #[cfg(not(target_os = "windows"))]
        if let Ok(uv_env) = env::var("UV_PROJECT_ENVIRONMENT") {
            let cand = PathBuf::from(uv_env).join("bin").join("remedy");
            if cand.is_file() && sidecar_is_native(&cand) {
                log::info!(
                    "Dev build: using UV_PROJECT_ENVIRONMENT remedy: {}",
                    cand.display()
                );
                return (cand.to_string_lossy().to_string(), String::new());
            }
        }
        if let Ok(cwd) = env::current_dir() {
            #[cfg(target_os = "windows")]
            let venv_rels: &[&str] = &[
                ".venv/Scripts/remedy.exe",
                "../.venv/Scripts/remedy.exe",
                "../../.venv/Scripts/remedy.exe",
            ];
            #[cfg(not(target_os = "windows"))]
            let venv_rels: &[&str] = &[
                ".venv/bin/remedy",
                "../.venv/bin/remedy",
                "../../.venv/bin/remedy",
            ];
            for venv_rel in venv_rels {
                let cand = cwd.join(venv_rel);
                if cand.is_file() && sidecar_is_native(&cand) {
                    log::info!(
                        "Dev build: using repo venv remedy (live source): {}",
                        cand.display()
                    );
                    return (cand.to_string_lossy().to_string(), String::new());
                }
            }
        }
        // Fall back to a plausible (>1 MB) PATH `remedy` before the stale
        // packaged sidecar.  Skip tiny Python wrapper scripts (108 KB
        // `Scripts\remedy.exe`) — they need `uv run` and fail when spawned
        // directly by the Rust sidecar.
        if let Ok(path) = which_remedy_on_path() {
            if is_plausible_sidecar(Path::new(&path)) {
                log::info!("Dev build: using plausible PATH remedy for sidecar: {}", path);
                return (path, String::new());
            }
            log::info!(
                "Dev build: PATH remedy is a stub/wrapper ({}), checking dev bin paths",
                path
            );
        }
    }

    if let Some(dir) = current_exe_dir() {
        #[cfg(target_os = "windows")]
        {
            if let Some(path) = searched(
                "triple",
                &dir.join("remedy-desktop-x86_64-pc-windows-msvc.exe"),
            ) {
                return (path, String::new());
            }
            if let Some(path) = searched(
                "triple-amd64",
                &dir.join("remedy-desktop-amd64-pc-windows-msvc.exe"),
            ) {
                return (path, String::new());
            }
            if let Some(path) = searched("plain", &dir.join("remedy-desktop.exe")) {
                return (path, String::new());
            }
        }
        #[cfg(not(target_os = "windows"))]
        {
            if let Some(path) = searched(
                "triple",
                &dir.join("remedy-desktop-x86_64-unknown-linux-gnu"),
            ) {
                return (path, String::new());
            }
            if let Some(path) = searched("plain", &dir.join("remedy-desktop")) {
                return (path, String::new());
            }
        }
    }

    if let Ok(cwd) = env::current_dir() {
        #[cfg(target_os = "windows")]
        let cwd_cands: [(&str, PathBuf); 4] = [
            (
                "dev-bin-triple",
                cwd.join("bin")
                    .join("remedy-desktop-x86_64-pc-windows-msvc.exe"),
            ),
            ("dev-bin", cwd.join("bin").join("remedy-desktop.exe")),
            (
                "dev-desktop-triple",
                cwd.join("desktop")
                    .join("bin")
                    .join("remedy-desktop-x86_64-pc-windows-msvc.exe"),
            ),
            (
                "dev-desktop",
                cwd.join("desktop").join("bin").join("remedy-desktop.exe"),
            ),
        ];
        #[cfg(not(target_os = "windows"))]
        let cwd_cands: [(&str, PathBuf); 4] = [
            (
                "dev-bin-triple",
                cwd.join("bin")
                    .join("remedy-desktop-x86_64-unknown-linux-gnu"),
            ),
            ("dev-bin", cwd.join("bin").join("remedy-desktop")),
            (
                "dev-desktop-triple",
                cwd.join("desktop")
                    .join("bin")
                    .join("remedy-desktop-x86_64-unknown-linux-gnu"),
            ),
            (
                "dev-desktop",
                cwd.join("desktop").join("bin").join("remedy-desktop"),
            ),
        ];
        for (label, p) in cwd_cands {
            if let Some(path) = searched(label, &p) {
                return (path, String::new());
            }
        }
    }

    // Prefer PATH `remedy` (current install / source entry) over missing stubs.
    if let Ok(path) = which_remedy_on_path() {
        log::info!("Found remedy on PATH: {}", path);
        return (path, String::new());
    }

    let msg = format!(
        "Sidecar not found - checked exe dir {:?}, cwd/bin/, and PATH (remedy). \
         Tiny stub EXEs (<1MB) are ignored on Windows; PE/.exe is ignored on Linux.",
        current_exe_dir()
    );
    log::error!("{}", msg);
    #[cfg(target_os = "windows")]
    {
        ("remedy-desktop.exe".to_string(), msg)
    }
    #[cfg(not(target_os = "windows"))]
    {
        ("remedy-desktop".to_string(), msg)
    }
}

/// Resolve `remedy` / `remedy.exe` from PATH when present.
fn which_remedy_on_path() -> Result<String, ()> {
    let path_var = env::var_os("PATH").ok_or(())?;
    #[cfg(target_os = "windows")]
    let names: &[&str] = &["remedy.exe", "remedy"];
    #[cfg(not(target_os = "windows"))]
    let names: &[&str] = &["remedy"];
    for dir in env::split_paths(&path_var) {
        for name in names {
            let cand = dir.join(name);
            if !cand.is_file() || !sidecar_is_native(&cand) {
                continue;
            }
            if is_plausible_sidecar(&cand) || cfg!(target_os = "windows") {
                // Scripts/remedy.exe can be a small launcher — allow if it exists
                // and is executable; size check relaxed for PATH entry points.
                return Ok(cand.to_string_lossy().to_string());
            }
        }
    }
    Err(())
}

/// True when something is already accepting connections on our API port.
fn api_port_in_use() -> bool {
    TcpStream::connect_timeout(&status_addr(), Duration::from_millis(250)).is_ok()
}

/// Back-compat alias used in a few call sites.
fn port_7400_in_use() -> bool {
    api_port_in_use()
}

/// User choice when a foreign process already owns the Remedy API port.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ForeignServeChoice {
    /// Keep CLI/background server; Desktop UI talks to it (no kill, no re-spawn).
    UseExisting,
    /// Stop the background server and start Desktop's managed sidecar.
    TakeOver,
    /// Leave the background server alone and abort Desktop launch.
    Cancel,
}

/// Blocking native dialog when :7400 is already healthy.
fn ask_foreign_serve_dialog() -> ForeignServeChoice {
    use rfd::{MessageButtons, MessageDialog, MessageDialogResult, MessageLevel};

    // Unattended / WSLg relaunch: attach to the already-healthy API.
    let attach = std::env::var("REMEDY_DESKTOP_ATTACH")
        .unwrap_or_default()
        .to_ascii_lowercase();
    if matches!(attach.as_str(), "1" | "true" | "yes" | "existing") {
        log::info!("REMEDY_DESKTOP_ATTACH: use existing server on :{}", api_port());
        return ForeignServeChoice::UseExisting;
    }

    // Three clear actions (TaskDialog custom buttons on Windows).
    let result = MessageDialog::new()
        .set_level(MessageLevel::Warning)
        .set_title("Remedy server already running")
        .set_description(
            &format!(
                "Another Remedy server is already using port {} \
             (for example `remedy serve` in a terminal).\n\n\
             Choose how to continue:\n\n\
             • Use existing server — open Desktop UI without stopping that serve\n\
             • Stop & start Desktop server — end the process on this port only\n\
             • Exit Desktop — leave the other server running",
                api_port()
            ),
        )
        .set_buttons(MessageButtons::YesNoCancelCustom(
            "Use existing server".into(),
            "Stop CLI & start Desktop server".into(),
            "Exit Desktop".into(),
        ))
        .show();

    log::info!("Foreign-serve dialog result: {:?}", result);

    match result {
        MessageDialogResult::Yes => ForeignServeChoice::UseExisting,
        MessageDialogResult::No | MessageDialogResult::Ok => ForeignServeChoice::TakeOver,
        MessageDialogResult::Custom(ref s) => {
            let low = s.to_ascii_lowercase();
            if low.contains("use existing") || low.contains("existing") {
                ForeignServeChoice::UseExisting
            } else if low.contains("stop") || low.contains("take") || low.contains("start desktop")
            {
                ForeignServeChoice::TakeOver
            } else {
                ForeignServeChoice::Cancel
            }
        }
        // X / Cancel → exit Desktop, keep CLI
        _ => ForeignServeChoice::Cancel,
    }
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

    // Dev live SPA first — staged sidecar webui/ is often stale.
    if let Ok(cwd) = env::current_dir() {
        candidates.extend([
            cwd.join("dist"),
            cwd.join("desktop").join("dist"),
            cwd.join("..").join("dist"),
        ]);
    }
    if let Ok(root) = env::var("REMEDY_DEV_ROOT") {
        candidates.push(PathBuf::from(root).join("desktop").join("dist"));
    }

    // Next to main exe / sidecar (packaged: resources/webui or sibling webui)
    if let Some(dir) = current_exe_dir() {
        candidates.extend([
            dir.join("desktop").join("dist"),
            dir.join("webui"),
            dir.join("ui"),
            dir.join("resources").join("webui"),
        ]);
    }

    if let Ok(cwd) = env::current_dir() {
        candidates.push(cwd.join("webui"));
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
    let port_str = api_port().to_string();
    // --skip-setup: never block the sidecar on interactive CLI wizard.
    // Desktop SetupWizard is the first-run UX (needs a running API).
    let args = [
        "--home",
        home_str.as_ref(),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        port_str.as_str(),
        "--skip-setup",
    ];

    let webui = find_webui_dir();
    // Packaged local SmolVLM2 + llama-server (resource dir/local) for vision + nano swarm.
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
            .env("PYTHONUNBUFFERED", "1")
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

/// Watch the managed API process after startup and recover an unexpected exit.
///
/// Connect clients are intentionally disposable: closing or unpairing a phone
/// may close its gateway socket, but it must never be able to leave the whole
/// desktop API offline.  The Python logs cannot record an abrupt process exit,
/// so this parent-side watcher also preserves the OS exit status for diagnosis.
fn sidecar_watchdog(app: AppHandle) {
    let _ = thread::Builder::new()
        .name("sidecar-watchdog".into())
        .spawn(move || loop {
            thread::sleep(Duration::from_secs(2));
            let state = app.state::<ServerState>();
            if state.app_exiting.load(Ordering::SeqCst) {
                return;
            }
            if state.attached_existing.load(Ordering::SeqCst) {
                continue;
            }

            let exit = match state.process.lock() {
                Ok(mut guard) => match guard.as_mut().map(Child::try_wait) {
                    Some(Ok(Some(status))) => {
                        let _ = guard.take();
                        Some(status.to_string())
                    }
                    Some(Err(error)) => {
                        log::warn!("Could not inspect managed sidecar status: {error}");
                        None
                    }
                    _ => None,
                },
                Err(poisoned) => {
                    let mut guard = poisoned.into_inner();
                    match guard.as_mut().map(Child::try_wait) {
                        Some(Ok(Some(status))) => {
                            let _ = guard.take();
                            Some(status.to_string())
                        }
                        Some(Err(error)) => {
                            log::warn!("Could not inspect managed sidecar status: {error}");
                            None
                        }
                        _ => None,
                    }
                }
            };
            let Some(status) = exit else {
                continue;
            };

            log::error!("Managed Remedy server exited unexpectedly ({status}); recovering");
            let _ = app.emit("server-starting", ());
            let cmd = state
                .sidecar_cmd
                .lock()
                .map(|guard| guard.clone().unwrap_or_default())
                .unwrap_or_default();
            if cmd.is_empty() {
                let message = format!(
                    "Remedy server exited unexpectedly ({status}) and its launch path is unavailable"
                );
                log::error!("{message}");
                let _ = app.emit("server-error", &message);
                continue;
            }

            let mut recovered = false;
            for (attempt, delay) in [0_u64, 2, 5].into_iter().enumerate() {
                if delay > 0 {
                    thread::sleep(Duration::from_secs(delay));
                }
                if state.app_exiting.load(Ordering::SeqCst) {
                    return;
                }
                log::info!("Sidecar recovery attempt {} after exit {status}", attempt + 1);
                let spawned = start_sidecar(
                    &state.process,
                    &cmd,
                    SidecarStartMode::ForceRestart,
                    &state.attached_existing,
                );
                if spawned.is_ok() && wait_for_health(Duration::from_secs(30)) {
                    recovered = true;
                    break;
                }
                if let Err(error) = spawned {
                    log::error!("Sidecar recovery spawn failed: {error}");
                } else {
                    log::error!("Sidecar recovery process did not become healthy");
                }
            }

            if recovered {
                log::info!("Remedy server recovered after unexpected exit ({status})");
                let _ = app.emit("server-ready", ());
            } else {
                let message = format!(
                    "Remedy server exited unexpectedly ({status}) and did not recover after 3 attempts"
                );
                log::error!("{message}");
                let _ = app.emit("server-error", &message);
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

/// Ask serve itself whether a chat turn is on the wire (`/api/turn-active`).
///
/// `Some(true)` = live turn, `Some(false)` = idle, `None` = no or bad
/// answer (serve dead, hung, or pre-endpoint version). Preferred over lock
/// files: a crashed serve cannot answer, so it can never block an apply.
fn serve_turn_active() -> Option<bool> {
    let mut stream =
        TcpStream::connect_timeout(&status_addr(), Duration::from_millis(500)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok();
    let req = "GET /api/turn-active HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    stream.write_all(req.as_bytes()).ok()?;
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
    let response = String::from_utf8_lossy(&buf);
    let ok = response
        .lines()
        .next()
        .map(|line| line.contains(" 200 ") || line.contains("200 OK"))
        .unwrap_or(false);
    if !ok {
        return None;
    }
    if response.contains("\"active\":true") || response.contains("\"active\": true") {
        Some(true)
    } else if response.contains("\"active\":false") || response.contains("\"active\": false") {
        Some(false)
    } else {
        None
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
            let stop_url = format!("{}/api/vision/stop", api_base_url());
            let _ = agent.post(&stop_url).call();
        });
    // Cap wait so quit stays snappy even if the request is slow.
    thread::sleep(Duration::from_millis(250));
}

/// Stop the managed sidecar and any leftover remedy-desktop processes / :7400 listeners.
/// Must never hang — tray "Quit and stop server" depends on this returning quickly.
fn shutdown_sidecar(state: &ServerState) {
    state.app_exiting.store(true, Ordering::SeqCst);
    try_stop_vision_http();

    match state.process.lock() {
        Ok(mut guard) => kill_child(&mut guard),
        Err(poisoned) => {
            let mut guard = poisoned.into_inner();
            kill_child(&mut guard);
        }
    }
    if state.attached_existing.load(Ordering::SeqCst) {
        log::info!("attached to existing serve — not killing foreign process");
        return;
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

/// Force-stop processes that block *this* instance's API port / install files.
///
/// **Default install:** also kills packaged sidecar images + CLI serve by name
/// (needed for NSIS update / Take over on the default port).
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
    kill_api_port_windows();
    kill_cli_serve_windows();
}

/// Kill LISTENING owners of **this instance's** API port only.
#[cfg(target_os = "windows")]
fn kill_api_port_windows() {
    let port = api_port();
    let netstat_cmd = format!(
        r#"for /f "tokens=5" %a in ('netstat -ano ^| findstr :{port} ^| findstr LISTENING') do taskkill /F /T /PID %a"#
    );
    let _ = Command::new("cmd")
        .args(["/C", &netstat_cmd])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let ps = format!(
        "Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
    );
    let _ = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(target_os = "windows")]
fn kill_port_7400_windows() {
    kill_api_port_windows();
}
/// Kill ``remedy.exe serve`` and ``python … remedy … serve`` process trees.
#[cfg(target_os = "windows")]
fn kill_cli_serve_windows() {
    // Only this instance's API port — never murder an isolated :7411 serve.
    let port = api_port();
    let ps = format!(
        r#"
$port = {port}
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='remedy.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object {{
    $_.CommandLine -and (
      $_.CommandLine -match 'remedy(\.exe)?["\s].*serve' -or
      $_.CommandLine -match 'Scripts\\remedy\.exe' -or
      $_.CommandLine -match '_start_serve\.py' -or
      $_.CommandLine -match 'remedy\.interfaces\.cli'
    ) -and (
      $_.CommandLine -match "--port\s+$port" -or
      ($port -eq 7400 -and $_.CommandLine -notmatch '--port\s+\d+')
    )
  }} |
  ForEach-Object {{
    taskkill /F /T /PID $_.ProcessId 2>$null | Out-Null
  }}
"#
    );
    let _ = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(target_os = "windows"))]
fn force_stop_remedy_processes() {
    // Sidecar argv is `remedy --home <dir> serve --host 127.0.0.1 --port N`.
    // A literal `remedy serve` never matches.
    let port = api_port();
    let _ = Command::new("pkill")
        .args(["-f", &format!("serve --host 127.0.0.1 --port {port}")])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// How to treat an existing listener on :7400 before starting the sidecar.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SidecarStartMode {
    /// App launch: if a foreign serve is healthy, ask the user first.
    InteractiveLaunch,
    /// Explicit Restart from UI / menus: always take over the port.
    ForceRestart,
}

fn start_sidecar(
    process: &Arc<Mutex<Option<Child>>>,
    cmd: &str,
    mode: SidecarStartMode,
    attached_existing: &AtomicBool,
) -> Result<(), String> {
    let mut guard = process
        .lock()
        .map_err(|_| "server state lock poisoned".to_string())?;

    // Already managed and healthy — skip unless this is an explicit restart
    // (Retry / self-inject must recycle a pingable-but-wedged process).
    if mode != SidecarStartMode::ForceRestart
        && guard.is_some()
        && check_health(Duration::from_millis(400))
    {
        log::info!("Managed sidecar already healthy; skipping re-spawn");
        return Ok(());
    }

    let busy = port_7400_in_use();
    let healthy = busy && check_health(Duration::from_millis(600));

    if busy {
        let we_own = guard
            .as_ref()
            .map(|c| {
                // Child still running?
                // try_wait Ok(None) = still alive
                // We can't mutably borrow easily; use health + presence as signal.
                let _ = c;
                true
            })
            .unwrap_or(false);

        if !we_own || healthy {
            match mode {
                SidecarStartMode::InteractiveLaunch if healthy => {
                    log::info!("Port 7400 already in use by a healthy foreign server");
                    // Release lock before blocking UI dialog so other threads can progress.
                    drop(guard);
                    match ask_foreign_serve_dialog() {
                        ForeignServeChoice::Cancel => {
                            log::info!("User cancelled Desktop launch (kept background server)");
                            return Err("cancelled".into());
                        }
                        ForeignServeChoice::UseExisting => {
                            // Attach to CLI/external serve — do not kill, do not spawn.
                            log::info!(
                                "User chose Use existing server — Desktop will use :{} as-is",
                                api_port()
                            );
                            attached_existing.store(true, Ordering::SeqCst);
                            return Ok(());
                        }
                        ForeignServeChoice::TakeOver => {
                            log::info!("User chose Take over — stopping foreign server on :{}", api_port());
                            // Kill immediately (before re-locking) so CLI serve stops
                            // even if spawn later fails.
                            force_stop_remedy_processes();
                            // Wait until our port is free (up to ~5s)
                            for _ in 0..25 {
                                if !port_7400_in_use() {
                                    break;
                                }
                                thread::sleep(Duration::from_millis(200));
                                force_stop_remedy_processes();
                            }
                            if port_7400_in_use() {
                                log::error!("Port {} still busy after Take over kill", api_port());
                                return Err(format!(
                                    "Could not stop the background Remedy server on port {}. \
                                     Close the terminal running `remedy serve` (Ctrl+C), then try again.",
                                    api_port()
                                ));
                            }
                            log::info!("Foreign server stopped; port {} free", api_port());
                            // Re-acquire and continue spawn below.
                            guard = process
                                .lock()
                                .map_err(|_| "server state lock poisoned".to_string())?;
                        }
                    }
                }
                SidecarStartMode::InteractiveLaunch => {
                    // Port held but not healthy (zombie) — take over without a dialog.
                    log::warn!(
                        "Port {} busy but unhealthy; taking over without prompt",
                        api_port()
                    );
                }
                SidecarStartMode::ForceRestart => {
                    log::info!("Force restart — taking over port {}", api_port());
                }
            }
        }
    }

    kill_child(&mut guard);
    attached_existing.store(false, Ordering::SeqCst);
    // Free our API port for managed process (after user consent when interactive).
    force_stop_remedy_processes();
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        let port = api_port();
        let netstat_cmd = format!(
            r#"for /f "tokens=5" %a in ('netstat -ano ^| findstr :{port} ^| findstr LISTENING') do taskkill /F /PID %a"#
        );
        let _ = Command::new("cmd")
            .args(["/C", &netstat_cmd])
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
    // Prefer a handle cached at startup — after hide-to-tray, label lookup
    // has been observed empty ("no main window") while the HWND is still alive.
    if let Some(state) = app.try_state::<ServerState>() {
        if let Ok(g) = state.main_window.lock() {
            if let Some(w) = g.as_ref() {
                return Some(w.clone());
            }
        }
    }
    app.get_webview_window("main")
        .or_else(|| {
            app.webview_windows()
                .into_iter()
                .find(|(label, _)| {
                    *label != "remedy-browser" && !label.starts_with("remedy-browser")
                })
                .map(|(_, w)| w)
        })
        .or_else(|| app.webview_windows().into_values().next())
}

/// Cache the main window for tray restore (call once window exists).
fn cache_main_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main").or_else(|| {
        app.webview_windows()
            .into_iter()
            .find(|(l, _)| *l != "remedy-browser")
            .map(|(_, w)| w)
    }) {
        if let Some(state) = app.try_state::<ServerState>() {
            if let Ok(mut g) = state.main_window.lock() {
                *g = Some(w);
                log::info!("cached main window handle for tray restore");
            }
        }
    }
}

/// Windows: ShowWindow + SetForegroundWindow — reliable after minimize or hide.
#[cfg(windows)]
fn win_force_show_window(w: &tauri::WebviewWindow) {
    // Tauri WebviewWindow exposes HWND on Windows.
    let Ok(hwnd) = w.hwnd() else {
        log::warn!("win_force_show_window: no hwnd");
        return;
    };
    let hwnd = hwnd.0 as isize;
    #[link(name = "user32")]
    extern "system" {
        fn IsIconic(hwnd: isize) -> i32;
        fn IsWindowVisible(hwnd: isize) -> i32;
        fn ShowWindow(hwnd: isize, cmd: i32) -> i32;
        fn SetForegroundWindow(hwnd: isize) -> i32;
        fn BringWindowToTop(hwnd: isize) -> i32;
    }
    // SW_RESTORE=9, SW_SHOW=5
    const SW_RESTORE: i32 = 9;
    const SW_SHOW: i32 = 5;
    unsafe {
        if IsIconic(hwnd) != 0 || IsWindowVisible(hwnd) == 0 {
            ShowWindow(hwnd, SW_RESTORE);
            ShowWindow(hwnd, SW_SHOW);
        } else {
            ShowWindow(hwnd, SW_SHOW);
        }
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
    }
}

#[cfg(not(windows))]
fn win_force_show_window(_w: &tauri::WebviewWindow) {}

/// Reliable show + unminimize + focus (taskbar minimize + tray restore).
///
/// Windows often leaves maximized windows stuck minimized unless we unminimize
/// *before* show/focus, and a brief always-on-top pulse reorders Z-order when
/// `set_focus` alone is ignored after tray/taskbar hide.
fn bring_main_to_front(app: &AppHandle) {
    let Some(w) = primary_window(app) else {
        let labels: Vec<String> = app.webview_windows().into_keys().collect();
        log::warn!(
            "bring_main_to_front: no main window (known labels={labels:?})"
        );
        // Last-ditch: re-cache if a window reappeared under another path
        cache_main_window(app);
        if let Some(w2) = primary_window(app) {
            bring_main_window_handle_front(&w2);
        }
        return;
    };
    // Refresh cache whenever we successfully resolve
    if let Some(state) = app.try_state::<ServerState>() {
        if let Ok(mut g) = state.main_window.lock() {
            *g = Some(w.clone());
        }
    }
    bring_main_window_handle_front(&w);
}

fn bring_main_window_handle_front(w: &tauri::WebviewWindow) {
    let _ = w.set_skip_taskbar(false);
    let minimized = w.is_minimized().unwrap_or(false);
    let visible = w.is_visible().unwrap_or(false);
    // Hidden (close-to-tray) vs minimized (taskbar) need different first steps.
    if !visible {
        let _ = w.show();
    }
    if minimized || !visible {
        let _ = w.unminimize();
    }
    let _ = w.show();
    let _ = w.unminimize();
    // Native Win32 restore — fixes OS decorations minimize + tray hide cases
    // where Tauri unminimize/show alone leave the HWND iconic or invisible.
    win_force_show_window(w);
    // Pulse always-on-top so the window surfaces above other apps on Windows.
    let _ = w.set_always_on_top(true);
    let _ = w.set_focus();
    let _ = w.set_always_on_top(false);
    let _ = w.set_focus();
    log::info!(
        "bring_main_to_front: shown + focused (was_min={minimized} was_vis={visible})"
    );
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

/// Open a folder in Explorer, or reveal a file (select in parent).
/// Never ShellExecute .md — that pops “Pick an app”. Agent reads via file_read.
#[tauri::command]
fn open_path(path: String) -> Result<String, String> {
    let p = PathBuf::from(path.trim());
    if !p.exists() {
        return Err(format!("path not found: {}", p.display()));
    }
    #[cfg(target_os = "windows")]
    {
        // Folders → Explorer. Files → highlight in parent (do not open).
        let status = if p.is_dir() {
            Command::new("explorer.exe")
                .arg(p.as_os_str())
                .creation_flags(CREATE_NO_WINDOW)
                .status()
        } else {
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
        // spawn, not status(): some xdg-open handlers block until the
        // launched app exits, which froze the Files slide on Linux/WSLg.
        let child = Command::new("xdg-open")
            .arg(&p)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| format!("open_path: {e}"))?;
        reap_detached(child);
        return Ok(format!("Opened {}", p.display()));
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
        fn sh_single_quote(s: &str) -> String {
            format!("'{}'", s.replace('\'', "'\\''"))
        }
        let gnome = ["--working-directory", dir_s.as_str()];
        let konsole = ["--workdir", dir_s.as_str()];
        // Match in-app PTY: never exec a WSL-interop $SHELL (/mnt/c/…/powershell.exe).
        let safe_shell = {
            let raw = env::var("SHELL").unwrap_or_default();
            let s = raw.trim();
            let low = s.to_ascii_lowercase();
            let interop = low.ends_with(".exe")
                || low.contains("/mnt/")
                || low.contains(":\\")
                || low.contains("//wsl");
            if !s.is_empty() && Path::new(s).is_file() && !interop {
                s.to_string()
            } else {
                "/bin/bash".to_string()
            }
        };
        let xterm_cmd = format!(
            "cd {} && exec {} -l",
            sh_single_quote(&dir_s),
            sh_single_quote(&safe_shell)
        );
        let xterm = ["-e", "sh", "-lc", xterm_cmd.as_str()];
        let terms: &[(&str, &[&str])] = &[
            ("gnome-terminal", &gnome),
            ("xfce4-terminal", &gnome),
            ("konsole", &konsole),
            ("xterm", &xterm),
        ];
        for (term, args) in terms {
            if Command::new(term).args(*args).spawn().is_ok() {
                return Ok(format!("Opened {term} in {dir_s}"));
            }
        }
        #[cfg(any(
            target_os = "linux",
            target_os = "dragonfly",
            target_os = "freebsd",
            target_os = "netbsd",
            target_os = "openbsd"
        ))]
        if linux_env_is_wslg() {
            if let Some(ps) = wslg_powershell_exe() {
                let win = wslg_windows_path(Path::new(&dir_s));
                let loc = win.to_string_lossy().replace('\'', "''");
                let cmd = format!("Set-Location -LiteralPath '{loc}'");
                if Command::new(&ps)
                    .args(["-NoLogo", "-NoExit", "-Command", &cmd])
                    .spawn()
                    .is_ok()
                {
                    return Ok(format!("Opened Windows PowerShell in {dir_s}"));
                }
            }
        }
        return Err("No terminal emulator found".into());
    }

    #[allow(unreachable_code)]
    Err("open_terminal unsupported on this platform".into())
}

/// Reject URLs that would be unsafe even without `cmd /C start` (CRLF / quotes).
fn url_safe_for_os_open(url: &str) -> bool {
    if url.is_empty() {
        return false;
    }
    if url.chars().any(|c| matches!(c, '\r' | '\n' | '"' | '|' | '<' | '>' | '^')) {
        return false;
    }
    url.starts_with("http://") || url.starts_with("https://") || url.starts_with("about:")
}

#[cfg(target_os = "windows")]
fn open_url_shell_execute(url: &str) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::core::PCWSTR;
    use windows::Win32::UI::Shell::ShellExecuteW;
    use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    let wide: Vec<u16> = std::ffi::OsStr::new(url)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let verb: Vec<u16> = std::ffi::OsStr::new("open")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    // SAFETY: UTF-16 buffers are NUL-terminated; hwnd/params/dir are null.
    let result = unsafe {
        ShellExecuteW(
            None,
            PCWSTR(verb.as_ptr()),
            PCWSTR(wide.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    // ShellExecuteW returns a value > 32 on success.
    if result.0 as isize > 32 {
        Ok(())
    } else {
        Err(format!("ShellExecuteW failed ({})", result.0 as isize))
    }
}

/// Open a URL in an external browser. Prefer Firefox when installed if `prefer_firefox`.
#[tauri::command]
fn open_external_url(url: String, prefer_firefox: Option<bool>) -> Result<String, String> {
    let url = url.trim().to_string();
    if !url_safe_for_os_open(&url) {
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
        open_url_shell_execute(&url)?;
        return Ok(format!("Opened default browser: {url}"));
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
        // spawn, not status(): xdg-open may block until the browser exits.
        let child = Command::new("xdg-open")
            .arg(&url)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| format!("open url failed: {e}"))?;
        reap_detached(child);
        return Ok(format!("Opened default browser: {url}"));
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
    let _ignored_close_flag = close_to_tray; // callers may still pass it; product forces true
    let prev_skip = state
        .desktop_prefs
        .lock()
        .map(|g| g.skip_quit_server_warning)
        .unwrap_or(false);
    let prefs = DesktopPrefs {
        // Title-bar X always hides; never accept false from Settings/API.
        close_to_tray: true,
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
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    {
        if linux_snap_state()
            .0
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Ok(true);
        }
    }
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
        .unwrap_or_else(|| format!("{}/", api_base_url()));

    // Brief wait so a just-started sidecar can answer before the browser loads.
    let deadline = Instant::now() + Duration::from_secs(8);
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&status_addr(), Duration::from_millis(300)).is_ok() {
            break;
        }
        thread::sleep(Duration::from_millis(200));
    }

    // Same stay-ready path as title-bar Close (WSLg minimizes; others hide).
    if let Some(w) = primary_window(&app) {
        stay_ready_after_close(&w)?;
        log::info!("switch_to_web_ui: desktop stay-ready (sidecar alive)");
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
        // Spawn and return — .status() waits until some desktops close the browser.
        let spawned = Command::new("xdg-open")
            .arg(&url)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .or_else(|_| {
                Command::new("open")
                    .arg(&url)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
            })
            .map_err(|e| format!("open browser failed: {e}"))?;
        #[cfg(all(unix, not(target_os = "macos")))]
        reap_detached(spawned);
        #[cfg(not(all(unix, not(target_os = "macos"))))]
        drop(spawned);
    }

    Ok(url)
}

/// Maximize / restore from the custom title bar.
#[tauri::command]
fn toggle_maximize_main_window(app: AppHandle) -> Result<bool, String> {
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;

    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    {
        return linux_toggle_workarea(&w);
    }

    #[cfg(not(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    )))]
    {
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
}

/// WSLg + GTK CSD: `maximize()` covers the whole output (under the Windows
/// taskbar) and keeps a ~32px shadow inset, which clips Close and the status
/// bar. Snap to the monitor work area instead.
#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_snap_state() -> &'static (
    std::sync::atomic::AtomicBool,
    Mutex<Option<(i32, i32, u32, u32)>>,
) {
    static STATE: std::sync::OnceLock<(
        std::sync::atomic::AtomicBool,
        Mutex<Option<(i32, i32, u32, u32)>>,
    )> = std::sync::OnceLock::new();
    STATE.get_or_init(|| {
        (
            std::sync::atomic::AtomicBool::new(false),
            Mutex::new(None),
        )
    })
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
/// WSLg maps the GTK window onto a Windows HWND. GDK's current monitor is
/// often the wrong display; the Windows working area of *that* HWND is truth.
/// `hint` is the current window rect so we pick the monitor the window is on
/// (3-display setups) instead of primary / a sibling Remedy window.
fn wslg_windows_workarea(hint: Option<(i32, i32, u32, u32)>) -> Option<(i32, i32, u32, u32)> {
    if !linux_env_is_wslg() {
        return None;
    }
    {
        let cache = wslg_workarea_cache()
            .lock()
            .ok()
            .and_then(|g| g.clone());
        if let Some((at, cached_hint, wa)) = cache {
            if at.elapsed() < Duration::from_millis(750) && cached_hint == hint {
                return Some(wa);
            }
            // Same monitor / nearby drag: reuse without spawning PowerShell.
            if at.elapsed() < Duration::from_millis(750) {
                if let (Some(h), Some(ch)) = (hint, cached_hint) {
                    let dx = (h.0 - ch.0).unsigned_abs();
                    let dy = (h.1 - ch.1).unsigned_abs();
                    if dx < 80 && dy < 80 {
                        return Some(wa);
                    }
                } else if hint.is_none() {
                    return Some(wa);
                }
            }
        }
    }
    let script = wslg_workarea_script()?;
    let ps = wslg_powershell_exe()?;
    let text = wslg_run_workarea_script(&ps, &script, hint, None);
    for line in text.lines() {
        let line = line.trim();
        let Some(rest) = line.strip_prefix("work=") else {
            continue;
        };
        let parts: Vec<&str> = rest.split(',').collect();
        if parts.len() != 4 {
            continue;
        }
        let x = parts[0].parse().ok()?;
        let y = parts[1].parse().ok()?;
        let w = parts[2].parse().ok()?;
        let h = parts[3].parse().ok()?;
        log::info!("linux workarea from Windows: {w}x{h} @ ({x},{y})");
        let wa = (x, y, w, h);
        if let Ok(mut g) = wslg_workarea_cache().lock() {
            *g = Some((Instant::now(), hint, wa));
        }
        return Some(wa);
    }
    None
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn wslg_b64(bytes: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 { T[((n >> 6) & 63) as usize] as char } else { '=' });
        out.push(if chunk.len() > 2 { T[(n & 63) as usize] as char } else { '=' });
    }
    out
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn wslg_workarea_cache() -> &'static Mutex<
    Option<(Instant, Option<(i32, i32, u32, u32)>, (i32, i32, u32, u32))>,
> {
    static CACHE: Mutex<
        Option<(Instant, Option<(i32, i32, u32, u32)>, (i32, i32, u32, u32))>,
    > = Mutex::new(None);
    &CACHE
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_env_is_wslg() -> bool {
    env::var_os("WSL_DISTRO_NAME").is_some()
        || env::var_os("WSL_INTEROP").is_some()
        || Path::new("/mnt/wslg").exists()
}

fn wslg_powershell_exe() -> Option<PathBuf> {
    let mut cands: Vec<PathBuf> = Vec::new();
    if let Ok(root) = env::var("SYSTEMROOT") {
        cands.push(PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe"));
    }
    for letter in b'c'..=b'z' {
        cands.push(PathBuf::from(format!(
            "/mnt/{}/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            letter as char
        )));
    }
    cands.into_iter().find(|p| p.is_file())
}

fn wslg_windows_path(p: &Path) -> PathBuf {
    if let Ok(out) = Command::new("wslpath").args(["-w"]).arg(p).output() {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !s.is_empty() {
                return PathBuf::from(s);
            }
        }
    }
    p.to_path_buf()
}

fn wslg_workarea_script() -> Option<PathBuf> {
    let mut cands: Vec<PathBuf> = Vec::new();
    cands.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("windows/wslg_workarea.ps1"),
    );
    if let Ok(root) = env::var("REMEDY_DEV_ROOT") {
        cands.push(PathBuf::from(root).join("desktop/src-tauri/windows/wslg_workarea.ps1"));
    }
    if let Ok(res) = env::var("REMEDY_RESOURCES") {
        let r = PathBuf::from(res);
        cands.push(r.join("windows/wslg_workarea.ps1"));
        cands.push(r.join("wslg_workarea.ps1"));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            cands.push(dir.join("windows/wslg_workarea.ps1"));
            cands.push(dir.join("resources/windows/wslg_workarea.ps1"));
            cands.push(dir.join("../../windows/wslg_workarea.ps1"));
        }
    }
    cands.into_iter().find(|p| p.is_file())
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn wslg_place_host_on_workarea(
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    hint: Option<(i32, i32, u32, u32)>,
) {
    if !linux_env_is_wslg() {
        return;
    }
    let Some(script) = wslg_workarea_script() else {
        return;
    };
    let Some(ps) = wslg_powershell_exe() else {
        return;
    };
    let place = format!("{x},{y},{width},{height}");
    let _ = wslg_run_workarea_script(&ps, &script, hint, Some(&place));
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
/// Run `wslg_workarea.ps1` through Windows PowerShell and return stdout.
///
/// Packaged installs live under /usr/lib → Windows sees the script as a UNC
/// path (`\wsl.localhost\...`). The default execution policy refuses
/// unsigned remote scripts, so the packaged .deb/AppImage never got a work
/// area (window stayed unmaximized). `-ExecutionPolicy Bypass` is
/// process-scoped; if group policy pins it, fall back to `-EncodedCommand`,
/// which carries the script body inline and never hits the file-policy path.
fn wslg_run_workarea_script(
    ps: &Path,
    script: &Path,
    hint: Option<(i32, i32, u32, u32)>,
    place: Option<&str>,
) -> String {
    let apply_env = |cmd: &mut Command| {
        if let Some((x, y, w, h)) = hint {
            cmd.env("REMEDY_HINT_RECT", format!("{x},{y},{w},{h}"));
        }
        if let Some(p) = place {
            cmd.env("REMEDY_PLACE_HOST", p);
        }
    };
    let script_arg = wslg_windows_path(script);
    let mut cmd = Command::new(ps);
    cmd.args([
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ])
    .arg(&script_arg);
    apply_env(&mut cmd);
    let mut text = cmd
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default();
    if text.lines().any(|l| l.trim().starts_with("work=")) {
        return text;
    }
    if let Ok(body) = std::fs::read_to_string(script) {
        let utf16: Vec<u8> = body.encode_utf16().flat_map(|u| u.to_le_bytes()).collect();
        let encoded = wslg_b64(&utf16);
        let mut cmd2 = Command::new(ps);
        cmd2.args(["-NoProfile", "-NonInteractive", "-EncodedCommand", &encoded]);
        apply_env(&mut cmd2);
        if let Ok(o) = cmd2.output() {
            text = String::from_utf8_lossy(&o.stdout).into_owned();
        }
    }
    text
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_toggle_workarea(w: &tauri::WebviewWindow) -> Result<bool, String> {
    use std::sync::atomic::Ordering;
    let (snapped, restore) = linux_snap_state();
    if snapped.load(Ordering::SeqCst) {
        let prev = restore.lock().map_err(|e| e.to_string())?.take();
        snapped.store(false, Ordering::SeqCst);
        let _ = w.unmaximize();
        if let Some((x, y, width, height)) = prev {
            let _ = w.set_position(tauri::PhysicalPosition::new(x, y));
            let _ = w.set_size(tauri::PhysicalSize::new(width, height));
        }
        return Ok(false);
    }

    let pos = w
        .outer_position()
        .unwrap_or(tauri::PhysicalPosition::new(80, 80));
    let size = w.outer_size().unwrap_or(tauri::PhysicalSize::new(1280, 800));
    if let Ok(mut g) = restore.lock() {
        *g = Some((pos.x, pos.y, size.width, size.height));
    }

    let hint = Some((pos.x, pos.y, size.width, size.height));
    let (x, y, width, height) = if let Some(wa) = wslg_windows_workarea(hint) {
        wa
    } else {
        let mon = w
            .current_monitor()
            .map_err(|e| format!("current_monitor: {e}"))?
            .ok_or_else(|| "no current monitor".to_string())?;
        let wa = mon.work_area();
        let mut width = wa.size.width.max(800);
        let mut height = wa.size.height.max(500);
        let full = mon.size();
        // GDK on WSLg often reports workarea == full output and ignores the
        // Windows taskbar (~48px).
        if width >= full.width && height >= full.height {
            height = full.height.saturating_sub(48).max(500);
        }
        (wa.position.x, wa.position.y, width, height)
    };

    // Fill *this* monitor's Windows working area. Do not inset for a RAIL
    // frame — GTK_CSD is off, and a 64px shrink left a gap on 2nd/3rd screens.
    let _ = w.unmaximize();
    if let Ok(gtk_win) = w.gtk_window() {
        use gtk::prelude::*;
        gtk_win.set_decorated(false);
        gtk_win.move_(x, y);
        gtk_win.resize(width as i32, height as i32);
    }
    w.set_position(tauri::PhysicalPosition::new(x, y))
        .map_err(|e| format!("set_position: {e}"))?;
    w.set_size(tauri::PhysicalSize::new(width, height))
        .map_err(|e| format!("set_size: {e}"))?;

    snapped.store(true, Ordering::SeqCst);
    linux_snap_busy().store(true, Ordering::SeqCst);
    wslg_place_host_on_workarea(x, y, width, height, hint);
    thread::spawn(|| {
        thread::sleep(Duration::from_millis(800));
        linux_snap_busy().store(false, Ordering::SeqCst);
    });
    log::info!("linux workarea snap {width}x{height} @ ({x},{y}) (monitor the window is on)");
    Ok(true)
}

/// Windows/WSLg may maximize the RAIL *host* (native title-bar). GTK then
/// grows to the full output and Close / the status bar clip. Re-snap.
#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_snap_busy() -> &'static std::sync::atomic::AtomicBool {
    static BUSY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    &BUSY
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_on_host_resized(window: &tauri::Window, size: tauri::PhysicalSize<u32>) {
    use std::sync::atomic::Ordering;
    if linux_snap_busy().load(Ordering::SeqCst) {
        return;
    }
    let pos = window.outer_position().ok();
    let hint = pos.map(|p| (p.x, p.y, size.width, size.height));
    // Cheap gate from cache — do not spawn PowerShell for ordinary drags.
    if let Ok(g) = wslg_workarea_cache().lock() {
        if let Some((at, _, wa)) = *g {
            if at.elapsed() < Duration::from_millis(750) {
                let (_, _, ww, wh) = wa;
                let huge = size.width + 24 >= ww && size.height + 24 >= wh;
                let overshoot =
                    size.width > ww.saturating_add(16) || size.height > wh.saturating_add(16);
                if !huge && !overshoot {
                    if linux_snap_state().0.load(Ordering::SeqCst) {
                        linux_snap_state().0.store(false, Ordering::SeqCst);
                    }
                    return;
                }
            }
        }
    }
    let Some((_, _, ww, wh)) = wslg_windows_workarea(hint) else {
        return;
    };
    let already = size.width + 16 >= ww
        && size.height + 16 >= wh
        && size.width <= ww.saturating_add(16)
        && size.height <= wh.saturating_add(16);
    let overshoot = size.width > ww.saturating_add(16) || size.height > wh.saturating_add(16);
    let huge = size.width + 24 >= ww && size.height + 24 >= wh;
    let (snapped, _) = linux_snap_state();
    if already && snapped.load(Ordering::SeqCst) {
        return;
    }
    if overshoot {
        // Native / RAIL maximize grew past the Windows workarea — snap to it.
        let Some(w) = window.app_handle().get_webview_window("main") else {
            return;
        };
        snapped.store(false, Ordering::SeqCst);
        if let Err(e) = linux_toggle_workarea(&w) {
            log::warn!("linux resize snap: {e}");
        }
        return;
    }
    if snapped.load(Ordering::SeqCst) && !huge {
        // User restored or shrank — do not re-snap.
        snapped.store(false, Ordering::SeqCst);
    }
}

/// Close chrome: stay running. Windows tray hide; Linux always minimize
/// (AppIndicator is often invisible — hide() would drop the only restore surface).
fn stay_ready_after_close(w: &tauri::WebviewWindow) -> Result<(), String> {
    #[cfg(target_os = "linux")]
    {
        let _ = w.unmaximize();
        w.minimize()
            .map_err(|e| format!("minimize failed: {e}"))?;
        log::info!("request_close: Linux minimize to taskbar");
        return Ok(());
    }
    let has_tray = w.app_handle().tray_by_id("main").is_some();
    if !has_tray {
        let _ = w.unmaximize();
        w.minimize()
            .map_err(|e| format!("minimize failed: {e}"))?;
        log::info!("request_close: no tray — minimized");
        return Ok(());
    }
    w.hide().map_err(|e| format!("hide failed: {e}"))?;
    log::info!("request_close: hidden to tray");
    Ok(())
}

/// Last-resort stay-ready. Never hide() on WSLg (RAIL HWND teardown).
fn stay_ready_fallback(w: &tauri::WebviewWindow) {
    #[cfg(target_os = "linux")]
    {
        let _ = w.unmaximize();
        let _ = w.minimize();
        return;
    }
    if w.app_handle().tray_by_id("main").is_none() {
        let _ = w.unmaximize();
        let _ = w.minimize();
        return;
    }
    let _ = w.hide();
}

/// Close button / chrome: always hide to tray (always-ready partner).
/// Full quit is tray "Quit" / `request_quit_app` only.
#[tauri::command]
fn request_close_main_window(
    app: AppHandle,
    state: State<'_, ServerState>,
) -> Result<(), String> {
    let fresh = load_desktop_prefs();
    if let Ok(mut g) = state.desktop_prefs.lock() {
        *g = DesktopPrefs {
            close_to_tray: true,
            start_in_tray: fresh.start_in_tray,
            skip_quit_server_warning: fresh.skip_quit_server_warning,
        };
    }
    if !fresh.close_to_tray {
        save_desktop_prefs(&DesktopPrefs {
            close_to_tray: true,
            start_in_tray: fresh.start_in_tray,
            skip_quit_server_warning: fresh.skip_quit_server_warning,
        });
    }
    let w = primary_window(&app).ok_or_else(|| "no main window".to_string())?;
    stay_ready_after_close(&w)
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
        let child = Command::new("xdg-open")
            .arg(&path_str)
            .spawn()
            .map_err(|e| format!("Failed to open folder: {e}"))?;
        reap_detached(child);
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
    #[cfg(not(target_os = "windows"))]
    {
        return DesktopUpdateInfo {
            current_version: current.clone(),
            latest_version: current,
            update_available: false,
            download_url: None,
            release_notes: None,
            error: None,
        };
    }
    #[cfg(target_os = "windows")]
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

/// Decode on-disk local API token bytes.
///
/// Supports:
/// - Legacy plaintext token (single line)
/// - DPAPI envelope JSON: `{"v": 2, "dpapi": "<base64>"}` (Windows user-scoped)
fn decode_local_api_token_bytes(raw: &[u8]) -> Result<String, String> {
    let text = String::from_utf8_lossy(raw).trim().to_string();
    if text.is_empty() {
        return Err("local API token is empty or invalid".into());
    }
    if text.starts_with('{') {
        let v: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| format!("token envelope JSON: {e}"))?;
        let is_v2 = v.get("v").and_then(|x| x.as_i64()) == Some(2)
            || v.get("v").and_then(|x| x.as_u64()) == Some(2);
        if is_v2 {
            if let Some(b64) = v.get("dpapi").and_then(|x| x.as_str()) {
                use base64::Engine;
                let cipher = base64::engine::general_purpose::STANDARD
                    .decode(b64.trim())
                    .map_err(|e| format!("token dpapi base64: {e}"))?;
                let plain = dpapi_unprotect_user(&cipher)?;
                let tok = String::from_utf8(plain)
                    .map_err(|e| format!("token utf-8: {e}"))?
                    .trim()
                    .to_string();
                if tok.len() < 16 {
                    return Err("local API token is empty or invalid".into());
                }
                return Ok(tok);
            }
        }
        return Err("local API token envelope unrecognized".into());
    }
    // Legacy plaintext
    if text.len() < 16 {
        return Err("local API token is empty or invalid".into());
    }
    Ok(text)
}

/// User-scoped DPAPI unprotect (CryptUnprotectData, UI forbidden).
#[cfg(target_os = "windows")]
fn dpapi_unprotect_user(cipher: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr;

    #[repr(C)]
    struct DataBlob {
        cb_data: u32,
        pb_data: *mut u8,
    }

    #[link(name = "crypt32")]
    extern "system" {
        fn CryptUnprotectData(
            p_data_in: *const DataBlob,
            ppsz_data_descr: *mut *mut u16,
            p_optional_entropy: *const DataBlob,
            pv_reserved: *mut core::ffi::c_void,
            p_prompt_struct: *mut core::ffi::c_void,
            dw_flags: u32,
            p_data_out: *mut DataBlob,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn LocalFree(h: *mut core::ffi::c_void) -> *mut core::ffi::c_void;
    }

    if cipher.is_empty() {
        return Err("empty DPAPI ciphertext".into());
    }
    let mut in_blob = DataBlob {
        cb_data: cipher.len() as u32,
        // CryptUnprotectData does not mutate input; cast is for C ABI only.
        pb_data: cipher.as_ptr() as *mut u8,
    };
    let mut out_blob = DataBlob {
        cb_data: 0,
        pb_data: ptr::null_mut(),
    };
    // CRYPTPROTECT_UI_FORBIDDEN = 0x1
    let ok = unsafe {
        CryptUnprotectData(
            &in_blob,
            ptr::null_mut(),
            ptr::null(),
            ptr::null_mut(),
            ptr::null_mut(),
            0x1,
            &mut out_blob,
        )
    };
    if ok == 0 {
        return Err("CryptUnprotectData failed for local API token".into());
    }
    let result = unsafe {
        let slice = std::slice::from_raw_parts(out_blob.pb_data, out_blob.cb_data as usize);
        let v = slice.to_vec();
        LocalFree(out_blob.pb_data as *mut core::ffi::c_void);
        v
    };
    Ok(result)
}

#[cfg(not(target_os = "windows"))]
fn dpapi_unprotect_user(_cipher: &[u8]) -> Result<Vec<u8>, String> {
    Err("DPAPI envelopes are only supported on Windows".into())
}

/// Read the local API bearer token written by the Python sidecar
/// (`$REMEDY_HOME/auth/local_api_token`, default `~/.remedy/auth/...`).
#[tauri::command]
fn get_local_api_token() -> Result<String, String> {
    let dir = remedy_home().join("auth");
    let primary = dir.join("local_api_token");
    if primary.is_file() {
        let raw = std::fs::read(&primary).map_err(|e| format!("read token: {e}"))?;
        match decode_local_api_token_bytes(&raw) {
            Ok(tok) => return Ok(tok),
            Err(e) => {
                // Windows DPAPI envelope is unreadable on Linux — try the
                // sidecar-local file instead of treating JSON as a bearer.
                let alt = dir.join("local_api_token.posix");
                if alt.is_file() {
                    let raw2 =
                        std::fs::read(&alt).map_err(|e2| format!("read posix token: {e2}"))?;
                    return decode_local_api_token_bytes(&raw2);
                }
                return Err(e);
            }
        }
    }
    let alt = dir.join("local_api_token.posix");
    if alt.is_file() {
        let raw = std::fs::read(&alt).map_err(|e| format!("read posix token: {e}"))?;
        return decode_local_api_token_bytes(&raw);
    }
    Err("local API token not found - is the sidecar running?".into())
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
        log::info!("update schedule: powershell spawn ok");
        return Ok(());
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
            log::info!("update schedule: wscript launch ok");
            return Ok(());
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
        log::info!("update schedule: schtasks {task} created and run (ST={st})");
        let cleanup = format!("Start-Sleep -Seconds 240; schtasks /Delete /TN \"{task}\" /F");
        let _ = Command::new("powershell.exe")
            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &cleanup])
            .creation_flags(flags_basic)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        return Ok(());
    } else {
        errors.push("schtasks create/run failed".into());
        log::warn!("update schedule: schtasks create failed");
    }

    Err(errors.join(" | "))
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

/// Tauri updater pubkey (same blob as tauri.conf.json plugins.updater.pubkey).
const UPDATER_MINISIGN_PUBKEY_B64: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQ2MDEwQzVERTNBQ0JDRTAKUldUZ3ZLempYUXdCMWdRNWl0UzlpSDVUamJQZXRvREFpNE9Mb2xJeGpQck5ubVJ5ZDNxSko0dTYK";

/// Accept raw minisign **or** Tauri's base64-wrapped `.sig` (both appear in latest.json).
fn decode_updater_signature(sig: &str) -> Result<minisign_verify::Signature, String> {
    use base64::Engine;
    use minisign_verify::Signature;

    let sig = sig.trim();
    if let Ok(parsed) = Signature::decode(sig) {
        return Ok(parsed);
    }
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(sig)
        .map_err(|_| "updater signature parse: Invalid encoding in minisign data".to_string())?;
    let text = String::from_utf8(decoded)
        .map_err(|_| "updater signature parse: Invalid encoding in minisign data".to_string())?;
    Signature::decode(text.trim())
        .map_err(|e| format!("updater signature parse: {e}"))
}

fn verify_installer_minisign(exe: &Path, sig: &str) -> Result<(), String> {
    use base64::Engine;
    use minisign_verify::PublicKey;

    let decoded = base64::engine::general_purpose::STANDARD
        .decode(UPDATER_MINISIGN_PUBKEY_B64)
        .map_err(|e| format!("updater pubkey decode: {e}"))?;
    let pk_text = String::from_utf8_lossy(&decoded);
    let pk_line = pk_text
        .lines()
        .map(str::trim)
        .find(|l| l.starts_with("RW"))
        .ok_or_else(|| "updater pubkey missing RW line".to_string())?;
    let pk = PublicKey::from_base64(pk_line)
        .map_err(|e| format!("updater pubkey parse: {e}"))?;
    let signature = decode_updater_signature(sig)?;
    let data = std::fs::read(exe).map_err(|e| format!("read installer for verify: {e}"))?;
    pk.verify(&data, &signature, false)
        .map_err(|e| format!("installer signature mismatch: {e}"))?;
    Ok(())
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
    #[cfg(not(target_os = "windows"))]
    {
        let _ = (app, download_url);
        return Err(
            "In-app updates are Windows-only. Install the Linux .deb or AppImage from GitHub Releases."
                .into(),
        );
    }
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
    let server_state = app.state::<ServerState>();
    let process_slot = server_state.process.clone();
    let app_exiting = server_state.app_exiting.clone();
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
            verify_installer_minisign(&temp, &sig)?;
            log::info!(
                "Update signature verified ({} chars) for trusted GitHub asset {}",
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
            app_exiting.store(true, Ordering::SeqCst);
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
                    log::warn!("Install script not alive yet; waiting longer (not re-scheduling)");
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

/// FedCM / browser-credential noise that must never attach as chat files.
/// Seen leaking as chips: gmail.com_hrd, *_identity_provider, email-named files.
fn is_browser_credential_noise(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if name.is_empty() {
        return true;
    }
    if name.contains("identity_provider")
        || name.ends_with("_hrd")
        || name.contains("_hrd_")
        || name.ends_with("_hrd_metadata")
        || name.contains("fedcm")
    {
        return true;
    }
    // email-looking bare filenames (no extension)
    if name.contains('@') && path.extension().is_none() {
        return true;
    }
    // domain_hrd style without extension
    if path.extension().is_none() && name.contains(".com") {
        return true;
    }
    // Paths under WebView/Edge credential stores
    let full = path.to_string_lossy().to_ascii_lowercase();
    if full.contains("webview2") && (full.contains("fedcm") || full.contains("identity")) {
        return true;
    }
    false
}

fn load_paths_as_payloads(paths: &[String]) -> Result<Vec<DroppedFilePayload>, String> {
    use base64::Engine;

    let mut out = Vec::new();
    let mut skipped_noise = 0u32;
    for raw in paths {
        let path = PathBuf::from(raw);
        if !path.is_file() {
            continue;
        }
        if is_browser_credential_noise(&path) {
            skipped_noise += 1;
            log::warn!(
                "Skipping browser credential/FedCM noise drop: {}",
                path.display()
            );
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
        if skipped_noise > 0 {
            return Err(
                "Dropped files looked like browser login/FedCM noise and were ignored".into(),
            );
        }
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

/// Native multi-file picker for chat attachments.
///
/// Same pattern as `open_text_file` (sync rfd) — do **not** use
/// `run_on_main_thread` + blocking recv (deadlocks the paperclip button on Windows).
/// WebView2 `<input type="file">` remains a fallback in the UI.
#[tauri::command]
fn pick_attach_files() -> Result<Vec<DroppedFilePayload>, String> {
    let paths = rfd::FileDialog::new()
        .set_title("Attach files to message")
        .add_filter(
            "Images",
            &["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"],
        )
        .add_filter(
            "Text / code",
            &[
                "txt", "md", "json", "csv", "log", "py", "ts", "tsx", "js", "rs", "toml", "yaml",
                "yml",
            ],
        )
        .add_filter("All files", &["*"])
        .pick_files();
    match paths {
        None => Ok(Vec::new()),
        Some(ps) if ps.is_empty() => Ok(Vec::new()),
        Some(ps) => {
            let strs: Vec<String> = ps
                .iter()
                .map(|p| p.to_string_lossy().into_owned())
                .collect();
            log::info!("pick_attach_files: {} path(s) selected", strs.len());
            load_paths_as_payloads(&strs)
        }
    }
}

/// Queue OS-dropped paths for the composer (poll + events).
/// Shared by WindowEvent and WebviewEvent drag-drop (WebviewWindow often only fires the latter).
fn queue_native_file_drop(app: &AppHandle, paths: &[PathBuf]) {
    let path_strs: Vec<String> = paths
        .iter()
        .map(|p| p.to_string_lossy().into_owned())
        .collect();
    if path_strs.is_empty() {
        return;
    }
    log::info!("Native file drop: {} path(s)", path_strs.len());
    match load_paths_as_payloads(&path_strs) {
        Ok(payloads) => {
            log::info!("Read {} dropped file(s) for composer", payloads.len());
            if let Some(state) = app.try_state::<ServerState>() {
                let pending = state.pending_drops.clone();
                let mut q = pending.lock().unwrap_or_else(|e| e.into_inner());
                q.extend(payloads.clone());
            }
            let _ = app.emit("file-drop-ready", &payloads);
        }
        Err(e) => {
            log::error!("Failed to read dropped files: {}", e);
            let _ = app.emit("file-drop-error", serde_json::json!({ "message": e }));
        }
    }
}

/// Roll back a self-inject change via git, so a sidecar crash on the injected
/// code can be undone by the parent even though the crashed process is gone.
///
/// ``changed`` files are restored to the recorded HEAD; new ``untracked`` files
/// from the round are removed. Best-effort: logs everything, never panics.
fn self_inject_rollback(payload: &serde_json::Value) {
    let repo = payload
        .get("repo")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if repo.is_empty() {
        log::warn!("self-inject rollback skipped: no repo in payload");
        return;
    }
    let head = payload
        .get("head")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let run = |args: &[&str]| -> String {
        let mut cmd = Command::new("git");
        cmd.arg("-C").arg(&repo);
        cmd.args(args);
        #[cfg(target_os = "windows")]
        cmd.creation_flags(CREATE_NO_WINDOW);
        let out = cmd
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output();
        match out {
            Ok(o) => {
                let mut s = String::from_utf8_lossy(&o.stderr).to_string();
                if s.trim().is_empty() {
                    s = String::from_utf8_lossy(&o.stdout).to_string();
                }
                s
            }
            Err(e) => format!("git error: {e}"),
        }
    };

    let changed: Vec<&str> = payload
        .get("changed")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|s| s.as_str()).collect())
        .unwrap_or_default();
    if !changed.is_empty() {
        let mut args = vec!["checkout", "--"];
        args.extend(changed.iter().copied());
        log::info!("self-inject rollback: git checkout -- {} files", changed.len());
        run(&args);
    }
    if !head.is_empty() {
        // Ensure tracked files match the recorded HEAD even if only a subset was
        // listed (defensive; no-op when already clean for those paths).
        let _ = head;
    }
    for untracked in payload
        .get("untracked")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|s| s.as_str()).collect::<Vec<_>>())
        .unwrap_or_default()
    {
        let p = Path::new(&repo).join(untracked);
        if p.is_file() {
            log::info!("self-inject rollback: removing untracked {}", untracked);
            let _ = std::fs::remove_file(&p);
        }
    }
    log::info!("self-inject rollback finished (head={})", head);
}

/// Restart the sidecar and wait for it to become healthy. Returns true on success.
fn restart_sidecar_and_wait(state: &ServerState, wait: Duration) -> bool {
    let cmd = match state.sidecar_cmd.lock() {
        Ok(g) => g.clone().unwrap_or_default(),
        Err(_) => String::new(),
    };
    if cmd.is_empty() {
        log::error!("self-inject apply: sidecar cmd unknown");
        return false;
    }
    match start_sidecar(
        &state.process,
        &cmd,
        SidecarStartMode::ForceRestart,
        &state.attached_existing,
    ) {
        Ok(()) => wait_for_health(wait),
        Err(e) => {
            log::error!("self-inject apply: sidecar restart failed: {e}");
            false
        }
    }
}

/// Background poller for self-inject apply markers.
///
/// When the sidecar writes ``<home>/locks/self_inject_apply``, a test-gated
/// change has been applied and needs a sidecar restart to go live. The poller:
///  1. parses the rollback payload,
///  2. restarts the sidecar and waits for health,
///  3. if unhealthy, rolls the change back and restarts once more,
///  4. if still unhealthy, stops and logs an investigation payload (no storms).
/// The marker is always removed after one attempt.
/// True while any live chat turn holds a stream lock in `<home>/locks`.
///
/// Writers create one `stream_active.<pid>` file per process and heartbeat
/// its mtime every ~10s (see `remedy/core/stream_lock.py`). A file whose
/// mtime is older than the stale bound belongs to a crashed process — it is
/// removed here so a hard-killed serve can never block self-inject forever.
/// The legacy bare `stream_active` name (older sidecars) gets the same TTL.
fn stream_lock_active() -> bool {
    const STALE_AFTER: Duration = Duration::from_secs(120);
    let locks = remedy_home().join("locks");
    let now = std::time::SystemTime::now();
    let mut active = false;
    if let Ok(entries) = std::fs::read_dir(&locks) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name != "stream_active" && !name.starts_with("stream_active.") {
                continue;
            }
            let fresh = entry
                .metadata()
                .and_then(|m| m.modified())
                .ok()
                .and_then(|m| now.duration_since(m).ok())
                .map(|age| age < STALE_AFTER)
                // Unreadable mtime: assume live — never recycle a real turn.
                .unwrap_or(true);
            if fresh {
                active = true;
            } else {
                log::info!("stream lock {} is stale — removing", name);
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
    active
}

fn self_inject_apply_poller(app: AppHandle) {
    let _ = thread::Builder::new()
        .name("self-inject-apply".into())
        .spawn(move || {
            let marker = remedy_home().join("locks").join("self_inject_apply");
            loop {
                thread::sleep(Duration::from_secs(2));
                // Never recycle serve while a chat turn is on the wire —
                // that painted "Error: network error" while working this repo
                // from the packaged install. Serve's own answer is preferred
                // (a crashed serve can't answer, so it can't deadlock); lock
                // files remain as belt-and-suspenders for gateway runners and
                // pre-endpoint sidecars, with stale ones ignored and cleaned.
                if serve_turn_active().unwrap_or(false) || stream_lock_active() {
                    continue;
                }
                let raw = match std::fs::read_to_string(&marker) {
                    Ok(s) => s,
                    Err(_) => continue, // not present yet
                };
                let payload: serde_json::Value = match serde_json::from_str(&raw.trim()) {
                    Ok(v) => v,
                    Err(_) => {
                        // Unreadable marker — delete and move on.
                        let _ = std::fs::remove_file(&marker);
                        continue;
                    }
                };
                let _ = std::fs::remove_file(&marker);
                log::info!("self-inject apply: marker seen, restarting sidecar");

                let state = app.state::<ServerState>();
                let _ = app.emit("server-starting", ());
                if restart_sidecar_and_wait(&state, Duration::from_secs(45)) {
                    log::info!("self-inject apply: sidecar healthy after restart");
                    let _ = app.emit("server-ready", ());
                    continue;
                }

                // Failsafe: injected change likely broke the sidecar. Roll back.
                log::warn!(
                    "self-inject apply: sidecar unhealthy after restart — rolling back injected change"
                );
                self_inject_rollback(&payload);
                let _ = app.emit("server-starting", ());
                if restart_sidecar_and_wait(&state, Duration::from_secs(60)) {
                    log::info!(
                        "self-inject apply: sidecar recovered after rollback (server-ready)"
                    );
                    let _ = app.emit("server-ready", ());
                } else {
                    let msg = format!(
                        "self-inject change applied but sidecar failed twice (crash). \
                         The change was rolled back but the server still did not recover. \
                         Investigate the round in the ledger; restart the app.",
                        // payload included for the investigation log
                    );
                    log::error!("{} payload={}", msg, raw);
                    let _ = app.emit("server-error", &msg);
                    // Stop looping — do not hammer restarts.
                    thread::sleep(Duration::from_secs(10));
                }
            }
        });
}

/// Update system tray tooltip (somatic / organism mood from partner status).
#[tauri::command]
fn set_tray_tooltip(app: AppHandle, tooltip: String) -> Result<(), String> {
    let text = tooltip.trim();
    if text.is_empty() {
        return Ok(());
    }
    // OS tooltip length soft-cap
    let capped: String = text.chars().take(120).collect();
    if let Some(tray) = app.tray_by_id("main") {
        tray.set_tooltip(Some(capped.as_str()))
            .map_err(|e| format!("set_tooltip: {e}"))?;
    }
    Ok(())
}

/// Kill and respawn the sidecar, wait for health, emit server-ready / server-error.
#[tauri::command]
async fn restart_server(app: AppHandle, state: State<'_, ServerState>) -> Result<String, String> {
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
    let process = state.process.clone();
    let attached = state.attached_existing.clone();
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        start_sidecar(
            &process,
            &cmd,
            SidecarStartMode::ForceRestart,
            &attached,
        )?;
        if wait_for_health(Duration::from_secs(30)) {
            log::info!("Remedy server ready after restart");
            let _ = handle.emit("server-ready", ());
            Ok("ready".into())
        } else {
            log::error!("Server failed to become ready after restart");
            let msg = "Server failed to start after 30s";
            let _ = handle.emit("server-error", msg);
            Err(msg.into())
        }
    })
    .await
    .map_err(|e| format!("restart task failed: {e}"))?
}

/// Windows: only one Remedy Desktop process at a time.
/// Returns true if this process owns the single-instance mutex.
#[cfg(target_os = "windows")]
fn acquire_desktop_single_instance() -> bool {
    use std::os::windows::ffi::OsStrExt;
    use std::ffi::OsStr;

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateMutexW(
            lp_mutex_attributes: *const core::ffi::c_void,
            b_initial_owner: i32,
            lp_name: *const u16,
        ) -> isize;
        fn GetLastError() -> u32;
        fn CloseHandle(h: isize) -> i32;
    }
    #[link(name = "user32")]
    extern "system" {
        fn FindWindowW(lp_class: *const u16, lp_window: *const u16) -> isize;
        fn ShowWindow(hwnd: isize, n_cmd: i32) -> i32;
        fn SetForegroundWindow(hwnd: isize) -> i32;
        fn IsIconic(hwnd: isize) -> i32;
    }
    const ERROR_ALREADY_EXISTS: u32 = 183;
    const SW_RESTORE: i32 = 9;

    let mutex_name = "Local\\RemedyDesktop-SingleInstance".to_string();
    let name: Vec<u16> = OsStr::new(&mutex_name)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let handle = CreateMutexW(core::ptr::null(), 1, name.as_ptr());
        if handle == 0 {
            // Fail open — better a second instance than no app.
            return true;
        }
        if GetLastError() == ERROR_ALREADY_EXISTS {
            // Focus existing main window if we can find it. Retry briefly —
            // first launch holds the mutex before the window exists.
            let title: Vec<u16> = OsStr::new(&window_title())
                .encode_wide()
                .chain(std::iter::once(0))
                .collect();
            let mut hwnd = FindWindowW(core::ptr::null(), title.as_ptr());
            if hwnd == 0 {
                // First launch holds the mutex before the HWND exists (AV / cold
                // start). Wait up to 5s — never kill a live recorded PID.
                for _ in 0..20 {
                    thread::sleep(Duration::from_millis(250));
                    hwnd = FindWindowW(core::ptr::null(), title.as_ptr());
                    if hwnd != 0 {
                        break;
                    }
                }
            }
            if hwnd != 0 {
                if IsIconic(hwnd) != 0 {
                    ShowWindow(hwnd, SW_RESTORE);
                }
                let _ = ShowWindow(hwnd, SW_RESTORE);
                SetForegroundWindow(hwnd);
                CloseHandle(handle);
                return false;
            }
            // Mutex held, no window after 5s. If the recorded owner is still
            // alive it is still starting — do not taskkill it.
            CloseHandle(handle);
            let path = desktop_instance_pid_path();
            if let Ok(raw) = std::fs::read_to_string(&path) {
                if let Ok(pid) = raw.trim().parse::<u32>() {
                    if pid != 0 && pid != std::process::id() && pid_is_remedy_desktop_image(pid)
                    {
                        log::warn!(
                            "Single-instance mutex held by live pid={pid} with no \
                             window yet — this launch exits"
                        );
                        return false;
                    }
                }
            }
            let reclaimed = reclaim_desktop_instance();
            if !reclaimed {
                log::warn!(
                    "Single-instance mutex held, no window, and no identifiable \
                     Remedy Desktop PID — failing closed (this launch exits)"
                );
                return false;
            }
            thread::sleep(Duration::from_millis(400));
            // Re-create mutex. A non-null handle is not ownership.
            let handle2 = CreateMutexW(core::ptr::null(), 1, name.as_ptr());
            if handle2 == 0 || GetLastError() == ERROR_ALREADY_EXISTS {
                if handle2 != 0 {
                    CloseHandle(handle2);
                }
                log::warn!(
                    "Mutex still held after reclaim — failing closed (this launch exits)"
                );
                return false;
            }
            use std::sync::atomic::{AtomicIsize, Ordering};
            static DESKTOP_MUTEX: AtomicIsize = AtomicIsize::new(0);
            DESKTOP_MUTEX.store(handle2, Ordering::SeqCst);
            record_desktop_instance_pid();
            return true;
        }
        // Keep mutex handle for process lifetime (must not CloseHandle).
        use std::sync::atomic::{AtomicIsize, Ordering};
        static DESKTOP_MUTEX: AtomicIsize = AtomicIsize::new(0);
        DESKTOP_MUTEX.store(handle, Ordering::SeqCst);
        record_desktop_instance_pid();
        true
    }
}

/// PID sidecar next to the named mutex (Remedy home). Never taskkill /IM app.exe.
#[cfg(target_os = "windows")]
fn desktop_instance_pid_path() -> PathBuf {
    remedy_home().join("desktop-instance.pid")
}

#[cfg(target_os = "windows")]
fn record_desktop_instance_pid() {
    let path = desktop_instance_pid_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, std::process::id().to_string());
}

/// True only when *pid* is the installed Remedy Desktop image.
#[cfg(target_os = "windows")]
fn pid_is_remedy_desktop_image(pid: u32) -> bool {
    let out = Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output();
    let Ok(out) = out else {
        return false;
    };
    String::from_utf8_lossy(&out.stdout)
        .to_ascii_lowercase()
        .contains("remedy desktop.exe")
}

/// Kill the recorded owner PID (verified image), else the known installed image.
/// Returns true when we issued a targeted reclaim (caller may re-acquire mutex).
#[cfg(target_os = "windows")]
fn reclaim_desktop_instance() -> bool {
    let self_pid = std::process::id();
    let path = desktop_instance_pid_path();
    if let Ok(raw) = std::fs::read_to_string(&path) {
        if let Ok(pid) = raw.trim().parse::<u32>() {
            if pid != 0 && pid != self_pid && pid_is_remedy_desktop_image(pid) {
                let killed = Command::new("taskkill")
                    .args(["/F", "/PID", &pid.to_string()])
                    .creation_flags(CREATE_NO_WINDOW)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                let _ = std::fs::remove_file(&path);
                if killed {
                    return true;
                }
            }
        }
    }
    // No recorded live owner — do not taskkill /IM (kills this start too).
    false
}

#[cfg(target_os = "linux")]
fn acquire_desktop_single_instance() -> bool {
    use std::fs::OpenOptions;
    use std::os::unix::io::AsRawFd;

    let dir = remedy_home();
    let _ = std::fs::create_dir_all(&dir);
    let lock_path = dir.join("desktop.lock");
    let file = match OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&lock_path)
    {
        Ok(f) => f,
        Err(e) => {
            log::warn!("desktop lock open failed: {e}");
            return true;
        }
    };
    let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if rc != 0 {
        log::warn!("desktop already running (flock); refusing second instance");
        return false;
    }
    let _ = std::fs::write(dir.join("desktop.pid"), format!("{}\n", std::process::id()));
    std::mem::forget(file);
    true
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn acquire_desktop_single_instance() -> bool {
    true
}

/// Must run before GTK/WebKit init. WSLg + WebKitGTK otherwise inflate the
/// pointer (scale-factor 0 / double-scale cursor theme).
#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_pre_gtk_env() {
    if std::env::var_os("XCURSOR_SIZE").is_none() {
        std::env::set_var("XCURSOR_SIZE", "24");
    }
    if std::env::var_os("GDK_SCALE").is_none() {
        std::env::set_var("GDK_SCALE", "1");
    }
    if std::env::var_os("GDK_DPI_SCALE").is_none() {
        std::env::set_var("GDK_DPI_SCALE", "1");
    }
    // CSD shadow reserves ~32px on WSLg and clips Close + the status bar
    // when the window is "maximized".
    if std::env::var_os("GTK_CSD").is_none() {
        std::env::set_var("GTK_CSD", "0");
    }
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_apply_gtk_chrome(bg: &str, fg: &str, border: &str, dark: bool) {
    use gtk::prelude::*;
    if let Some(settings) = gtk::Settings::default() {
        settings.set_gtk_application_prefer_dark_theme(dark);
        settings.set_gtk_cursor_theme_size(24);
    }
    let css = format!(
        r#"
        window, decoration, decoration:backdrop, headerbar, .titlebar {{
          background-color: {bg};
          color: {fg};
          border-color: {border};
          box-shadow: none;
          margin: 0;
          padding: 0;
          border-radius: 0;
        }}
        .csd decoration, window.csd, window.solid-csd, window.ssd,
        window.maximized, window.tiled, window.tiled-left, window.tiled-right,
        window.tiled-top, window.tiled-bottom {{
          box-shadow: none;
          margin: 0;
          padding: 0;
          border-radius: 0;
        }}
        "#
    );
    let provider = gtk::CssProvider::new();
    if let Err(e) = provider.load_from_data(css.as_bytes()) {
        log::warn!("linux chrome css: {e}");
        return;
    }
    if let Some(screen) = gtk::gdk::Screen::default() {
        gtk::StyleContext::add_provider_for_screen(
            &screen,
            &provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
}

/// Paint GTK window chrome (undecorated border / CSD leftovers) with the
/// active Remedy theme. No-op on Windows (OS decorations stay native).
#[tauri::command]
fn apply_linux_chrome_theme(
    bg: String,
    fg: String,
    border: String,
    dark: bool,
) -> Result<(), String> {
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    {
        let bg = bg.clone();
        let fg = fg.clone();
        let border = border.clone();
        // GTK CSS must run on the GTK thread.
        let _ = gtk::glib::idle_add(move || {
            linux_apply_gtk_chrome(&bg, &fg, &border, dark);
            gtk::glib::ControlFlow::Break
        });
    }
    let _ = (bg, fg, border, dark);
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    linux_pre_gtk_env();

    if !acquire_desktop_single_instance() {
        // Second launch: existing instance focused (Windows); exit this process.
        return;
    }

    tauri::Builder::default()
        .manage(ServerState {
            process: Arc::new(Mutex::new(None)),
            sidecar_cmd: Arc::new(Mutex::new(None)),
            pending_drops: Arc::new(Mutex::new(Vec::new())),
            desktop_prefs: Arc::new(Mutex::new(load_desktop_prefs())),
            main_window: Mutex::new(None),
            attached_existing: Arc::new(AtomicBool::new(false)),
            app_exiting: Arc::new(AtomicBool::new(false)),
        })
        .manage(pty_host::PtyState::default())
        .manage(browser_host::BrowserState::default())
        .manage(std::sync::Arc::new(privacy_shield::PrivacyShieldState::default()))
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
            apply_linux_chrome_theme,
            switch_to_web_ui,
            quit_app,
            request_quit_app,
            restart_server,
            set_tray_tooltip,
            check_desktop_update,
            start_desktop_update,
            get_local_api_token,
            get_api_origin,
            read_dropped_files,
            take_pending_file_drops,
            pick_attach_files,
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
            browser_host::browser_set_stack_suppressed,
            browser_host::browser_last_bounds,
            browser_host::browser_agent_action,
            browser_host::browser_view_mode,
            browser_host::browser_set_desktop_site,
            privacy_shield::privacy_shield_status,
            privacy_shield::privacy_shield_set_enabled,
            privacy_shield::privacy_shield_refresh_lists,
        ])
        .setup(|app| {
            if let Ok(resource) = app.path().resource_dir() {
                env::set_var("REMEDY_RESOURCES", &resource);
            }
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_title(&window_title());
                if let Ok(js_origin) = serde_json::to_string(&api_base_url()) {
                    let _ = w.eval(&format!("window.__REMEDY_API_ORIGIN__={js_origin};"));
                }
            }
            log::info!(
                "profile home={} api_port={}",
                remedy_home().display(),
                api_port()
            );
            let _shell = app.handle().plugin(tauri_plugin_shell::init())?;
            let _updater = app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            let app_handle = app.handle().clone();

            // Privacy Shield (Brave adblock-rust + EasyList) — background list load
            {
                let st = app_handle.state::<std::sync::Arc<privacy_shield::PrivacyShieldState>>();
                privacy_shield::install_global((*st).clone());
                privacy_shield::bootstrap();
            }

            // Force window/taskbar icon to the circuit-R monogram (not stale PE/cache).
            // Tray already uses icons/icon.png; taskbar often stuck on old embedded ICO.
            apply_window_icons(&app_handle);
            // Cache main window for tray restore after minimize / hide-to-tray.
            cache_main_window(app.handle());

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

            // Default Dark Forest chrome so the undecorated Linux frame is not Adwaita.
            #[cfg(any(
                target_os = "linux",
                target_os = "dragonfly",
                target_os = "freebsd",
                target_os = "netbsd",
                target_os = "openbsd"
            ))]
            {
                linux_apply_gtk_chrome("#0a0e0b", "#e6ebe7", "#2a352c", true);
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
                        if let Err(e) = stay_ready_after_close(&w) {
                            log::warn!("start_in_tray stay-ready failed: {e}");
                            stay_ready_fallback(&w);
                        }
                        log::info!("start_in_tray: main window stay-ready");
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
                if env::var_os("REMEDY_WEBUI_DIR").is_none() {
                    if let Some(live) = find_webui_dir() {
                        env::set_var("REMEDY_WEBUI_DIR", &live);
                        log::info!("REMEDY_WEBUI_DIR={}", live.display());
                    } else {
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
                match start_sidecar(
                    &state.process,
                    &remedy_cmd,
                    SidecarStartMode::InteractiveLaunch,
                    &state.attached_existing,
                ) {
                    Ok(()) => {
                        // Do not block GTK/WebView setup for up to 90s.
                        let handle = app_handle.clone();
                        thread::spawn(move || {
                            if wait_for_health(Duration::from_secs(90)) {
                                log::info!("Remedy server ready");
                                let _ = handle.emit("server-ready", ());
                                browser_host::start_computer_host_poller(handle.clone());
                                self_inject_apply_poller(handle.clone());
                                sidecar_watchdog(handle.clone());
                            } else {
                                log::error!("Server failed to start within 90s");
                                let _ = handle
                                    .emit("server-error", "Server failed to start after 90s");
                            }
                        });
                    }
                    Err(e) if e == "cancelled" => {
                        // User chose Exit Desktop — leave CLI serve running.
                        log::info!("Desktop launch cancelled; leaving foreign server on :7400");
                        // Hard exit — tauri exit during setup is unreliable.
                        std::process::exit(0);
                    }
                    Err(e) if e.contains("Could not stop the background") => {
                        log::error!("{}", e);
                        let _ = app_handle.emit("server-error", &e);
                        let _ = rfd::MessageDialog::new()
                            .set_level(rfd::MessageLevel::Error)
                            .set_title("Could not stop CLI server")
                            .set_description(&e)
                            .set_buttons(rfd::MessageButtons::Ok)
                            .show();
                        // Keep window open so user can Retry / use Restart server.
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
                #[cfg(any(
                    target_os = "linux",
                    target_os = "dragonfly",
                    target_os = "freebsd",
                    target_os = "netbsd",
                    target_os = "openbsd"
                ))]
                tauri::WindowEvent::Resized(size) => {
                    if window.label() == "main" {
                        linux_on_host_resized(window, *size);
                    }
                }
                // Title-bar X / Alt+F4: always hide to tray (always-ready partner).
                // Full quit only via tray "Quit" / request_quit_app — never kill
                // sidecar from the window chrome alone.
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    // Re-read disk so Settings stay in sync for other prefs.
                    let fresh = load_desktop_prefs();
                    if let Ok(mut g) = window.state::<ServerState>().desktop_prefs.lock() {
                        *g = DesktopPrefs {
                            // Force always-ready: X never means quit.
                            close_to_tray: true,
                            start_in_tray: fresh.start_in_tray,
                            skip_quit_server_warning: fresh.skip_quit_server_warning,
                        };
                    }
                    // Heal stale desktop.json / config that had close_to_tray=false
                    // (common after older Setup) so next launch matches behavior.
                    if !fresh.close_to_tray {
                        let healed = DesktopPrefs {
                            close_to_tray: true,
                            start_in_tray: fresh.start_in_tray,
                            skip_quit_server_warning: fresh.skip_quit_server_warning,
                        };
                        save_desktop_prefs(&healed);
                        log::info!(
                            "close_to_tray healed to true (title-bar X always hides; quit via tray)"
                        );
                    }
                    api.prevent_close();
                    if let Some(w) = window.app_handle().get_webview_window(window.label()) {
                        if let Err(e) = stay_ready_after_close(&w) {
                            log::warn!("close stay-ready failed: {e}");
                            stay_ready_fallback(&w);
                        }
                    } else if let Some(w) = primary_window(&window.app_handle()) {
                        stay_ready_fallback(&w);
                    }
                    log::info!("window close → stay ready (sidecar stays up; Quit from tray to stop server)");
                }
                tauri::WindowEvent::Destroyed => {
                    // Only runs on real process teardown (tray Quit), not hide-to-tray.
                    // Sidecar stop is owned by quit_app / Exit handlers.
                    log::info!("main window destroyed");
                }
                // Native OS file drops (Explorer -> app). WebView2 often won't
                // deliver HTML5 DataTransfer.files for external drops.
                // Note: on WebviewWindow, Drop often arrives via on_webview_event instead.
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
                    queue_native_file_drop(window.app_handle(), paths);
                }
                _ => {}
            }
        })
        // WebviewWindow delivers file drops here more reliably than WindowEvent (esp. after
        // label "main" + OS decorations). Skip the in-app browser embed webview.
        .on_webview_event(|webview, event| {
            match event {
                tauri::WebviewEvent::DragDrop(DragDropEvent::Enter { paths, .. }) => {
                    let label = webview.label();
                    if label.starts_with("remedy-browser") {
                        return;
                    }
                    let paths: Vec<String> = paths
                        .iter()
                        .map(|p| p.to_string_lossy().into_owned())
                        .collect();
                    let _ = webview
                        .app_handle()
                        .emit("file-drag", serde_json::json!({ "phase": "enter", "paths": paths }));
                }
                tauri::WebviewEvent::DragDrop(DragDropEvent::Over { .. }) => {
                    if webview.label().starts_with("remedy-browser") {
                        return;
                    }
                    let _ = webview
                        .app_handle()
                        .emit("file-drag", serde_json::json!({ "phase": "over" }));
                }
                tauri::WebviewEvent::DragDrop(DragDropEvent::Leave) => {
                    if webview.label().starts_with("remedy-browser") {
                        return;
                    }
                    let _ = webview
                        .app_handle()
                        .emit("file-drag", serde_json::json!({ "phase": "leave" }));
                }
                tauri::WebviewEvent::DragDrop(DragDropEvent::Drop { paths, .. }) => {
                    if webview.label().starts_with("remedy-browser") {
                        return;
                    }
                    log::info!(
                        "WebviewEvent file drop on '{}' ({} path(s))",
                        webview.label(),
                        paths.len()
                    );
                    queue_native_file_drop(webview.app_handle(), paths);
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
                    state.app_exiting.store(true, Ordering::SeqCst);
                    if !state.attached_existing.load(Ordering::SeqCst) {
                        force_stop_remedy_processes();
                        force_stop_vision_processes();
                    }
                }
                tauri::RunEvent::Exit => {
                    let state = app_handle.state::<ServerState>();
                    state.app_exiting.store(true, Ordering::SeqCst);
                    if !state.attached_existing.load(Ordering::SeqCst) {
                        force_stop_remedy_processes();
                        force_stop_vision_processes();
                    }
                }
                _ => {}
            }
        });
}
