//! Spawn remedy-runtime and talk to it over HTTP+SSE on 127.0.0.1:7400.

use crate::sse::Parser;
use crate::studio::{ChatMessage, Session, WorkMode};
use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::Cancel;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

/// Default loopback origin (no `/api` suffix).
pub const DEFAULT_ORIGIN: &str = "http://127.0.0.1:7400";

/// HTTP client for one runtime, with the bearer already set.
#[derive(Clone)]
pub struct Client {
    http: Http,
}

impl Client {
    /// A client for `origin` (e.g. `http://127.0.0.1:7400`) with `token` as Bearer, if any.
    pub fn new(origin: &str, token: Option<&str>) -> Self {
        let base = format!("{}/api", origin.trim_end_matches('/'));
        let mut http = Http::new(&base);
        if let Some(t) = token.filter(|t| !t.is_empty()) {
            http = http.header("Authorization", format!("Bearer {t}"));
        }
        Client { http }
    }

    /// A clone of the bearer-backed client.
    ///
    /// Setup, chat-depth, Grove, diagnostics, and the rails call through it.
    /// Settings moves one onto a worker thread. Cloning shares the cookie jar
    /// and the headers. The base is already `{origin}/api`.
    pub(crate) fn http(&self) -> Http {
        self.http.clone()
    }
}

/// Origin override: `$REMEDY_API`, else the default loopback port.
pub fn origin() -> String {
    std::env::var("REMEDY_API")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_ORIGIN.into())
}

/// True when the app should not spawn a sidecar (tests, or an explicit opt-out).
pub fn spawn_disabled() -> bool {
    matches!(
        std::env::var("REMEDY_DESKTOP_NO_SPAWN")
            .unwrap_or_default()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// Bearer: `$REMEDY_API_KEY`, then `~/.remedy/auth/local_api_token` (or `.posix`).
pub fn load_token() -> Option<String> {
    if let Ok(k) = std::env::var("REMEDY_API_KEY") {
        let k = k.trim().to_string();
        if k.len() >= 8 {
            return Some(k);
        }
    }
    let home = remedy_home();
    for name in ["local_api_token", "local_api_token.posix"] {
        let path = home.join("auth").join(name);
        if let Ok(raw) = std::fs::read_to_string(&path) {
            let t = raw.trim().to_string();
            if t.len() >= 8 && !t.starts_with('{') {
                return Some(t);
            }
        }
    }
    None
}

/// Same directory the Tauri shell uses: `REMEDY_HOME`, else `%USERPROFILE%\.remedy`
/// on Windows (even when a Git Bash `HOME` is set), else `$HOME/.remedy`.
/// Updates must keep reading this tree. Nothing here deletes that folder;
/// callers only add their own files under it.
pub fn remedy_home() -> PathBuf {
    let remedy = std::env::var("REMEDY_HOME").ok();
    let profile = std::env::var("USERPROFILE").ok();
    let home = std::env::var("HOME").ok();
    remedy_home_from(remedy.as_deref(), profile.as_deref(), home.as_deref())
}

pub(crate) fn remedy_home_from(
    remedy_home: Option<&str>,
    userprofile: Option<&str>,
    home: Option<&str>,
) -> PathBuf {
    if let Some(h) = remedy_home.map(str::trim).filter(|s| !s.is_empty()) {
        return PathBuf::from(h);
    }
    // Windows prefers USERPROFILE even when a Git Bash HOME is also set.
    let base = if cfg!(windows) {
        userprofile
    } else {
        home
    };
    let base = base.map(str::trim).filter(|s| !s.is_empty()).unwrap_or(".");
    PathBuf::from(base).join(".remedy")
}

/// What [`ensure_running`] did. The splash uses `MissingBinary` to fail
/// immediately instead of waiting ~90s for a server that will never start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnsureOutcome {
    /// Tests, or an explicit opt-out.
    Disabled,
    /// `/api/ping` or `/api/status` already answered.
    AlreadyUp,
    /// A sidecar process was started.
    Spawned,
    /// No `remedy-runtime` (or `remedy`) next to the app or on PATH.
    MissingBinary,
    /// The process could not be started.
    SpawnFailed,
}

/// Starts `remedy-runtime --serve` (or `remedy serve`) if nothing is already on the port.
pub fn ensure_running() -> EnsureOutcome {
    if spawn_disabled() {
        return EnsureOutcome::Disabled;
    }
    if ping_blocking(&origin(), load_token().as_deref()) {
        return EnsureOutcome::AlreadyUp;
    }
    let Some(cmd) = find_runtime() else {
        log_desktop("sidecar missing: no remedy-runtime next to the app or on PATH");
        return EnsureOutcome::MissingBinary;
    };
    let mut command = Command::new(&cmd);
    if file_name_starts(&cmd, "remedy-runtime") {
        command.arg("--serve");
    } else {
        command.args(["serve", "--host", "127.0.0.1", "--port", "7400"]);
    }
    if let Ok(key) = std::env::var("REMEDY_API_KEY") {
        command.env("REMEDY_API_KEY", key);
    }
    if let Some(dir) = cmd.parent() {
        command.current_dir(dir);
    }
    command.stdin(Stdio::null()).stdout(Stdio::null());
    let log_path = remedy_home().join("logs").join("desktop.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
        Ok(f) => {
            command.stderr(Stdio::from(f));
        }
        Err(_) => {
            command.stderr(Stdio::null());
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    match command.spawn() {
        Ok(child) => {
            log_desktop(&format!(
                "sidecar spawned pid={} cmd={}",
                child.id(),
                cmd.display()
            ));
            // The child is supervised only by staying alive; we must not wait
            // on it here. Forgetting the handle is the point.
            std::mem::forget(child);
            EnsureOutcome::Spawned
        }
        Err(err) => {
            log_desktop(&format!("sidecar spawn failed cmd={} err={err}", cmd.display()));
            EnsureOutcome::SpawnFailed
        }
    }
}

fn log_desktop(msg: &str) {
    let path = remedy_home().join("logs").join("desktop.log");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        use std::io::Write;
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| format!("{}.{:03}", d.as_secs(), d.subsec_millis()))
            .unwrap_or_else(|_| "0".into());
        let _ = writeln!(f, "{ts} {msg}");
    }
}

fn file_name_starts(p: &Path, prefix: &str) -> bool {
    p.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.to_ascii_lowercase().starts_with(prefix))
        .unwrap_or(false)
}

/// File names the installer and the old Tauri bundle use, plus PATH fallbacks.
pub(crate) const RUNTIME_NAMES: &[&str] = &[
    "remedy-runtime.exe",
    "remedy-runtime",
    "remedy-runtime-x86_64-pc-windows-msvc.exe",
    "remedy-runtime-x86_64-pc-windows-msvc",
    "remedy-runtime-aarch64-pc-windows-msvc.exe",
    "remedy-runtime-aarch64-pc-windows-msvc",
    "remedy-runtime-x86_64-unknown-linux-gnu",
    "remedy-runtime-aarch64-unknown-linux-gnu",
    "remedy-runtime-x86_64-apple-darwin",
    "remedy-runtime-aarch64-apple-darwin",
];

fn find_runtime() -> Option<PathBuf> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf));
    let cwd = std::env::current_dir().ok();
    find_runtime_from(exe_dir.as_deref(), cwd.as_deref(), std::env::var_os("PATH").as_deref())
}

/// Locate the sidecar given explicit search roots. Used by tests so a
/// `remedy-runtime.exe` next to the app is found on every host, including Linux CI.
pub(crate) fn find_runtime_from(
    exe_dir: Option<&Path>,
    cwd: Option<&Path>,
    path: Option<&std::ffi::OsStr>,
) -> Option<PathBuf> {
    if let Some(dir) = exe_dir {
        for name in RUNTIME_NAMES {
            if let Some(p) = existing_file(&dir.join(name)) {
                return Some(p);
            }
        }
    }
    if let Some(cwd) = cwd {
        for rel in ["bin", "desktop/bin"] {
            for name in RUNTIME_NAMES {
                if let Some(p) = existing_file(&cwd.join(rel).join(name)) {
                    return Some(p);
                }
            }
        }
        for rel in [
            ".venv/bin/remedy",
            ".venv/Scripts/remedy.exe",
            ".venv/Scripts/remedy",
            "../.venv/bin/remedy",
            "../.venv/Scripts/remedy.exe",
            "../../.venv/bin/remedy",
            "../../.venv/Scripts/remedy.exe",
        ] {
            if let Some(p) = existing_file(&cwd.join(rel)) {
                return Some(p);
            }
        }
    }
    if let Ok(uv) = std::env::var("UV_PROJECT_ENVIRONMENT") {
        for name in ["remedy", "remedy.exe"] {
            if let Some(p) = existing_file(&PathBuf::from(&uv).join("bin").join(name)) {
                return Some(p);
            }
            if let Some(p) = existing_file(&PathBuf::from(&uv).join("Scripts").join(name)) {
                return Some(p);
            }
        }
    }
    which_in("remedy-runtime", path).or_else(|| which_in("remedy", path))
}

/// `path` is a file, or on Windows the same path with `.exe` appended.
fn existing_file(p: &Path) -> Option<PathBuf> {
    if p.is_file() {
        return Some(p.to_path_buf());
    }
    if p.extension().is_none() {
        let exe = p.with_extension("exe");
        if exe.is_file() {
            return Some(exe);
        }
    }
    None
}

fn which(name: &str) -> Option<PathBuf> {
    which_in(name, std::env::var_os("PATH").as_deref())
}

fn which_in(name: &str, path: Option<&std::ffi::OsStr>) -> Option<PathBuf> {
    let path = path?;
    for dir in std::env::split_paths(path) {
        if let Some(p) = existing_file(&dir.join(name)) {
            return Some(p);
        }
    }
    None
}

/// Probe `/api/ping` then `/api/status` up to `attempts` times, with the same
/// backoff the splash uses (250ms doubling to 2s). True once the backend answers.
pub fn wait_until_up(origin: &str, token: Option<&str>, attempts: u32) -> bool {
    let attempts = attempts.max(1);
    for n in 1..=attempts {
        if ping_blocking(origin, token) {
            return true;
        }
        if n == attempts {
            break;
        }
        let shift = (n - 1).min(3);
        let wait = 250u64.saturating_mul(1u64 << shift).min(2000);
        std::thread::sleep(std::time::Duration::from_millis(wait));
    }
    false
}

fn ping_blocking(origin: &str, token: Option<&str>) -> bool {
    let client = Client::new(origin, token);
    let cancel = Cancel::new();
    let ok_ping = client
        .http
        .send_blocking(Request::get("ping"), &cancel)
        .is_ok();
    ok_ping
        || client
            .http
            .send_blocking(Request::get("status"), &cancel)
            .is_ok()
}

/// Probes `/api/ping` (then `/api/status`) and reports readiness on the worker thread.
pub fn probe(client: &Client, done: impl FnOnce(bool, Option<String>) + Send + 'static) {
    let http = client.http.clone();
    std::thread::Builder::new()
        .name("remedy-ping".into())
        .spawn(move || {
            let cancel = Cancel::new();
            match http.send_blocking(Request::get("ping"), &cancel) {
                Ok(_) => done(true, None),
                Err(_) => match http.send_blocking(Request::get("status"), &cancel) {
                    Ok(_) => done(true, None),
                    Err(e) => done(false, Some(e.to_string())),
                },
            }
        })
        .ok();
}

/// GET `/api/sessions?limit=100`. Grove sessions are hidden.
pub fn list_sessions(
    client: &Client,
    done: impl FnOnce(Result<Vec<Session>, String>) + Send + 'static,
) {
    let http = client.http.clone();
    std::thread::Builder::new()
        .name("remedy-sessions".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::get("sessions?limit=100"), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()))
                .map(|v| parse_sessions(&v));
            done(out);
        })
        .ok();
}

fn parse_sessions(v: &serde_json::Value) -> Vec<Session> {
    let arr = v
        .get("sessions")
        .and_then(|s| s.as_array())
        .cloned()
        .unwrap_or_default();
    arr.into_iter()
        .filter_map(|row| {
            let origin = row
                .get("origin_channel")
                .and_then(|o| o.as_str())
                .unwrap_or("");
            if origin == "grove" {
                return None;
            }
            let id = row.get("id")?.as_str()?.to_string();
            let title = row
                .get("title")
                .and_then(|t| t.as_str())
                .unwrap_or("New chat")
                .to_string();
            let project_path = row
                .get("project_path")
                .and_then(|p| p.as_str())
                .map(|s| s.to_string())
                .filter(|s| !s.is_empty());
            let message_count = row
                .get("message_count")
                .and_then(|n| n.as_u64())
                .unwrap_or(0) as u32;
            let updated_at = row
                .get("updated_at")
                .and_then(|t| t.as_str())
                .map(|s| s.to_string());
            let pinned = row.get("pinned").and_then(|p| p.as_bool()).unwrap_or(false);
            let archived = row.get("archived").and_then(|p| p.as_bool()).unwrap_or(false);
            let tags = row
                .get("tags")
                .and_then(|t| t.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let claimed = row.get("claimed").and_then(|p| p.as_bool()).unwrap_or(false);
            let active_request_id = row
                .get("active_request_id")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            Some(Session {
                id,
                title,
                project_path,
                pinned,
                archived,
                tags,
                message_count,
                updated_at,
                claimed,
                active_request_id,
            })
        })
        .collect()
}

/// POST `/api/sessions`.
pub fn create_session(
    client: &Client,
    done: impl FnOnce(Result<Session, String>) + Send + 'static,
) {
    let http = client.http.clone();
    std::thread::Builder::new()
        .name("remedy-new-session".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let body = serde_json::json!({ "title": "New chat" });
            let out = http
                .send_blocking(Request::post("sessions").json(&body), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()))
                .and_then(|v| {
                    parse_sessions(&serde_json::json!({ "sessions": [v] }))
                        .into_iter()
                        .next()
                        .ok_or_else(|| "the runtime did not return a session".into())
                });
            done(out);
        })
        .ok();
}

/// DELETE `/api/sessions/{id}`.
pub fn delete_session(
    client: &Client,
    id: &str,
    done: impl FnOnce(Result<(), String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = format!("sessions/{id}");
    std::thread::Builder::new()
        .name("remedy-delete-session".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::delete(&path), &cancel)
                .map(|_| ())
                .map_err(|e| e.to_string());
            done(out);
        })
        .ok();
}

/// GET `/api/sessions/{id}/messages`.
pub fn list_messages(
    client: &Client,
    id: &str,
    done: impl FnOnce(Result<Vec<ChatMessage>, String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = format!("sessions/{id}/messages?limit=200");
    std::thread::Builder::new()
        .name("remedy-messages".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::get(&path), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()))
                .map(|v| parse_messages(&v));
            done(out);
        })
        .ok();
}

fn parse_messages(v: &serde_json::Value) -> Vec<ChatMessage> {
    let arr = v
        .get("messages")
        .and_then(|s| s.as_array())
        .cloned()
        .unwrap_or_default();
    arr.into_iter()
        .filter_map(|row| {
            let id = row
                .get("id")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            let role = row.get("role")?.as_str()?.to_string();
            if role == "tool" {
                return None;
            }
            let content = row
                .get("content")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            let calls = crate::work::json_array(row.get("tool_calls"));
            let results = crate::work::json_array(row.get("tool_results"));
            let steps = crate::work::steps_from_history(&calls, &results);
            let narrations: Vec<String> = steps.iter().filter_map(|s| s.narration.clone()).collect();
            let content = crate::work::strip_narration(&content, &narrations);
            let thinking = match row.get("thinking") {
                Some(serde_json::Value::String(s)) => s.clone(),
                Some(serde_json::Value::Array(items)) => {
                    items.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>().join("")
                }
                _ => String::new(),
            };
            let created_at = row
                .get("created_at")
                .and_then(|t| t.as_str())
                .map(|s| s.to_string());
            let user_reaction = row
                .get("user_reaction")
                .or_else(|| row.get("reaction"))
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            let agent_reaction = row
                .get("agent_reaction")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            Some(ChatMessage {
                created_at,
                steps,
                thinking,
                user_reaction,
                agent_reaction,
                ..ChatMessage::plain(id, role, content)
            })
        })
        .collect()
}

/// GET `/api/models` — id strings for the status-bar picker.
pub fn list_models(
    client: &Client,
    done: impl FnOnce(Result<Vec<String>, String>) + Send + 'static,
) {
    let http = client.http.clone();
    std::thread::Builder::new()
        .name("remedy-models".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::get("models"), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()))
                .map(|v| parse_models(&v));
            done(out);
        })
        .ok();
}

/// GET a JSON path (`diagnostics`, or a full `https://` address) on a worker thread.
pub fn get_json(
    client: &Client,
    path: &str,
    done: impl FnOnce(Result<serde_json::Value, String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = path.to_string();
    std::thread::Builder::new()
        .name("remedy-get".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::get(&path), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json().map_err(|e| e.to_string()));
            done(out);
        })
        .ok();
}

/// POST a JSON body on a worker thread.
pub fn post_json(
    client: &Client,
    path: &str,
    body: serde_json::Value,
    done: impl FnOnce(Result<serde_json::Value, String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = path.to_string();
    std::thread::Builder::new()
        .name("remedy-post".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::post(&path).json(&body), &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json().map_err(|e| e.to_string()));
            done(out);
        })
        .ok();
}

fn parse_models(v: &serde_json::Value) -> Vec<String> {
    let arr = v
        .get("models")
        .or_else(|| v.as_array().map(|_| v))
        .and_then(|s| s.as_array())
        .cloned()
        .unwrap_or_default();
    arr.iter()
        .filter_map(|m| {
            if let Some(s) = m.as_str() {
                return Some(s.to_string());
            }
            m.get("id").and_then(|i| i.as_str()).map(|s| s.to_string())
        })
        .collect()
}

/// POST `/api/sessions/{id}/abort` with reason `stop`.
pub fn abort_session(
    client: &Client,
    id: &str,
    done: impl FnOnce(Result<(), String>) + Send + 'static,
) {
    abort_with(client, id, "stop", done);
}

/// POST `/api/sessions/{id}/abort` with `reason` (`stop` or `supersede`).
pub fn abort_with(
    client: &Client,
    id: &str,
    reason: &str,
    done: impl FnOnce(Result<(), String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = format!("sessions/{id}/abort");
    let body = serde_json::json!({ "reason": reason });
    std::thread::Builder::new()
        .name("remedy-abort".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(Request::post(&path).json(&body), &cancel)
                .map(|_| ())
                .map_err(|e| e.to_string());
            done(out);
        })
        .ok();
}

/// POST `/api/sessions/{id}/messages/stream` and deliver SSE frames to `on_frame`.
///
/// A 409 (this session is already streaming) becomes `steered` when there are no
/// attachments, and `queued` when there are. `on_frame` runs on a worker thread.
pub fn stream_message(
    client: &Client,
    session_id: &str,
    text: &str,
    model: &str,
    provider: &str,
    mode: WorkMode,
    attachments: &[serde_json::Value],
    on_frame: impl Fn(String, String) + Send + Sync + 'static,
) {
    let path = format!("sessions/{session_id}/messages/stream");
    let mut body = serde_json::json!({
        "message": text,
        "model": model,
        "provider": provider,
        "plan_mode": matches!(mode, WorkMode::Plan),
        "chat_mode": matches!(mode, WorkMode::Chat),
    });
    let files = !attachments.is_empty();
    if files {
        body["attachments"] = serde_json::Value::Array(attachments.to_vec());
    }
    drive_stream_inner(client.http(), Request::post(&path).json(&body), files, on_frame);
}

/// GET or POST an SSE request and deliver frames to `on_frame` (worker thread).
pub fn drive_stream(
    http: Http,
    request: Request,
    on_frame: impl Fn(String, String) + Send + Sync + 'static,
) {
    drive_stream_inner(http, request, false, on_frame);
}

fn drive_stream_inner(
    http: Http,
    request: Request,
    files: bool,
    on_frame: impl Fn(String, String) + Send + Sync + 'static,
) {
    let parser = Arc::new(Mutex::new(Parser::new()));
    let on_frame = Arc::new(on_frame);
    let on_chunk = {
        let parser = parser.clone();
        let on_frame = on_frame.clone();
        move |_res: &remedy_ui::net::Response, chunk: &[u8]| {
            let text = String::from_utf8_lossy(chunk);
            let frames = parser
                .lock()
                .ok()
                .map(|mut p| p.push(&text))
                .unwrap_or_default();
            for f in frames {
                on_frame(f.kind, f.data);
            }
        }
    };
    let on_done = {
        let on_frame = on_frame.clone();
        move |out: Result<remedy_ui::net::Response, remedy_ui::net::NetError>| match out {
            Ok(res) if res.status == 409 => busy_frame(&on_frame, files),
            Err(e) if e.status() == Some(409) => busy_frame(&on_frame, files),
            Err(e) => {
                let data =
                    serde_json::json!({ "type": "error", "message": e.to_string() }).to_string();
                on_frame("error".into(), data);
            }
            Ok(res) if !(200..300).contains(&res.status) => {
                let data = serde_json::json!({
                    "type": "error",
                    "message": format!("HTTP {}", res.status),
                })
                .to_string();
                on_frame("error".into(), data);
            }
            Ok(_) => on_frame("done".into(), "{\"type\":\"done\"}".into()),
        }
    };
    http.stream(request, &Cancel::new(), on_chunk, on_done);
}

fn busy_frame(on_frame: &Arc<impl Fn(String, String)>, files: bool) {
    if files {
        on_frame("queued".into(), "{}".into());
    } else {
        on_frame("steered".into(), "{}".into());
    }
}

#[cfg(test)]
mod tests {
    use super::parse_sessions;
    use super::remedy_home_from;
    use serde_json::json;
    use std::path::PathBuf;

    #[test]
    fn grove_sessions_stay_out_of_the_studio_list() {
        let rows = parse_sessions(&json!({
            "sessions": [
                {"id": "g1", "title": "Home", "origin_channel": "grove", "project_path": ""},
                {"id": "s1", "title": "New chat", "origin_channel": "studio"},
                {"id": "s2", "title": "Older", "message_count": 3}
            ]
        }));
        let ids: Vec<&str> = rows.iter().map(|s| s.id.as_str()).collect();
        assert_eq!(ids, ["s1", "s2"]);
        assert_eq!(rows[1].message_count, 3);
    }

    #[test]
    fn windows_home_follows_userprofile() {
        let profile = r"C:\Users\Ada";
        let git_bash = r"C:\GitBashHome";
        let home = remedy_home_from(None, Some(profile), Some(git_bash));
        // Windows uses USERPROFILE. Other hosts use HOME. The join follows
        // the host separator, so a Linux run does not expect a backslash.
        let root = if cfg!(windows) { profile } else { git_bash };
        assert_eq!(home, PathBuf::from(root).join(".remedy"));
    }

    #[test]
    fn remedy_home_env_wins() {
        let home = remedy_home_from(Some(r"D:\custom\.remedy"), Some(r"C:\Users\Ada"), Some("/home/ada"));
        assert_eq!(home, PathBuf::from(r"D:\custom\.remedy"));
    }

    #[test]
    fn finds_windows_runtime_exe_next_to_the_app() {
        use super::{find_runtime_from, wait_until_up};
        use std::io::{Read, Write};
        use std::net::TcpListener;

        let dir = std::env::temp_dir().join(format!("remedy-find-runtime-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let exe = dir.join("remedy-runtime.exe");
        std::fs::write(&exe, b"not-a-real-binary").unwrap();
        let found = find_runtime_from(Some(&dir), None, None).expect("exe next to the app");
        assert_eq!(found, exe);
        let _ = std::fs::remove_dir_all(&dir);

        // A listening backend is a connected state: wait_until_up must return
        // true once /api/ping answers, which is what the splash waits for.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 2048];
                let _ = stream.read(&mut buf);
                let body = b"{\"ok\":true}";
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(resp.as_bytes());
                let _ = stream.write_all(body);
            }
        });
        let origin = format!("http://127.0.0.1:{port}");
        assert!(
            wait_until_up(&origin, None, 8),
            "splash must reach a connected state when the backend answers /api/ping"
        );
        let _ = server.join();
    }
}
