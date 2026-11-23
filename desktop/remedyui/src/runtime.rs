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

    /// The bearer-bearing client Settings uses for `/api` calls.
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

fn remedy_home() -> PathBuf {
    if let Ok(h) = std::env::var("REMEDY_HOME") {
        let p = PathBuf::from(h);
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    std::env::var("HOME")
        .map(|h| PathBuf::from(h).join(".remedy"))
        .unwrap_or_else(|_| PathBuf::from(".remedy"))
}

/// Starts `remedy-runtime --serve` (or `remedy serve`) if nothing is already on the port.
pub fn ensure_running() {
    if spawn_disabled() {
        return;
    }
    if ping_blocking(&origin(), load_token().as_deref()) {
        return;
    }
    let Some(cmd) = find_runtime() else {
        return;
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
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let _ = command.spawn();
}

fn file_name_starts(p: &Path, prefix: &str) -> bool {
    p.file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.to_ascii_lowercase().starts_with(prefix))
        .unwrap_or(false)
}

fn find_runtime() -> Option<PathBuf> {
    let names = [
        "remedy-runtime",
        "remedy-runtime-x86_64-unknown-linux-gnu",
        "remedy-runtime-aarch64-unknown-linux-gnu",
    ];
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in names {
                let p = dir.join(name);
                if p.is_file() {
                    return Some(p);
                }
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for rel in ["bin", "desktop/bin"] {
            for name in names {
                let p = cwd.join(rel).join(name);
                if p.is_file() {
                    return Some(p);
                }
            }
        }
        for rel in [
            ".venv/bin/remedy",
            "../.venv/bin/remedy",
            "../../.venv/bin/remedy",
        ] {
            let p = cwd.join(rel);
            if p.is_file() {
                return Some(p);
            }
        }
    }
    if let Ok(uv) = std::env::var("UV_PROJECT_ENVIRONMENT") {
        let p = PathBuf::from(uv).join("bin").join("remedy");
        if p.is_file() {
            return Some(p);
        }
    }
    which("remedy-runtime").or_else(|| which("remedy"))
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let p = dir.join(name);
        if p.is_file() {
            return Some(p);
        }
    }
    None
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
            Some(Session {
                id,
                title,
                project_path,
                pinned: false,
                archived: false,
                tags: Vec::new(),
                message_count,
                updated_at,
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
            let created_at = row
                .get("created_at")
                .and_then(|t| t.as_str())
                .map(|s| s.to_string());
            Some(ChatMessage {
                id,
                role,
                content,
                created_at,
                streaming: false,
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

/// POST `/api/sessions/{id}/abort`.
pub fn abort_session(
    client: &Client,
    id: &str,
    done: impl FnOnce(Result<(), String>) + Send + 'static,
) {
    let http = client.http.clone();
    let path = format!("sessions/{id}/abort");
    std::thread::Builder::new()
        .name("remedy-abort".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(
                    Request::post(&path).json(&serde_json::json!({ "reason": "stop" })),
                    &cancel,
                )
                .map(|_| ())
                .map_err(|e| e.to_string());
            done(out);
        })
        .ok();
}

/// POST `/api/sessions/{id}/messages/stream` and deliver SSE frames to `on_frame`.
///
/// `on_frame` runs on a worker thread; hop to the UI with a [`Waker`] before touching signals.
pub fn stream_message(
    client: &Client,
    session_id: &str,
    text: &str,
    model: &str,
    provider: &str,
    mode: WorkMode,
    on_frame: impl Fn(String, String) + Send + Sync + 'static,
) {
    let http = client.http.clone();
    let path = format!("sessions/{session_id}/messages/stream");
    let body = serde_json::json!({
        "message": text,
        "model": model,
        "provider": provider,
        "plan_mode": matches!(mode, WorkMode::Plan),
        "chat_mode": matches!(mode, WorkMode::Chat),
    });
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
        move |out: Result<remedy_ui::net::Response, remedy_ui::net::NetError>| {
            if let Err(e) = out {
                let data =
                    serde_json::json!({ "type": "error", "message": e.to_string() }).to_string();
                on_frame("error".into(), data);
            } else {
                on_frame("done".into(), "{\"type\":\"done\"}".into());
            }
        }
    };
    http.stream(
        Request::post(&path).json(&body),
        &Cancel::new(),
        on_chunk,
        on_done,
    );
}

// Request::delete — confirm it exists; if not we'll use a generic method.
