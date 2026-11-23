//! Loopback client for diagnostics and hive.
//!
//! `runtime::Client`'s HTTP handle is private, and this slice does not own
//! `runtime.rs`. The base URL and bearer are the same ones `Client::new` uses:
//! `{origin}/api` plus `runtime::load_token()`.

use crate::runtime;
use remedy_ui::net::{Http, NetError, Request};
use remedy_ui::reactive::Cancel;
use serde_json::Value;

use super::model::{self, HiveOp};

pub(crate) fn client() -> Http {
    let origin = runtime::origin();
    let token = runtime::load_token();
    let base = format!("{}/api", origin.trim_end_matches('/'));
    let mut http = Http::new(&base);
    if let Some(t) = token.filter(|t| !t.is_empty()) {
        http = http.header("Authorization", format!("Bearer {t}"));
    }
    http
}

pub(crate) fn diagnostics(http: &Http, probe: bool, cancel: &Cancel) -> Result<Value, String> {
    let path = if probe {
        "/diagnostics?probe_providers=true"
    } else {
        "/diagnostics"
    };
    get_json(http, path, cancel)
}

pub(crate) fn rounds(http: &Http, limit: u32, cancel: &Cancel) -> Result<Value, String> {
    get_json(http, &format!("/self-inject/rounds?limit={limit}"), cancel)
}

pub(crate) fn roster(http: &Http, cancel: &Cancel) -> Result<Value, String> {
    get_json(http, "/hive/roster", cancel)
}

pub(crate) fn spawn(http: &Http, body: &Value, cancel: &Cancel) -> Result<HiveOp, String> {
    post_op(http, "/hive/spawn", body, cancel)
}

pub(crate) fn assign(http: &Http, body: &Value, cancel: &Cancel) -> Result<HiveOp, String> {
    post_op(http, "/hive/assign", body, cancel)
}

pub(crate) fn retire(http: &Http, body: &Value, cancel: &Cancel) -> Result<HiveOp, String> {
    post_op(http, "/hive/retire", body, cancel)
}

fn get_json(http: &Http, path: &str, cancel: &Cancel) -> Result<Value, String> {
    let res = http
        .send_blocking(Request::get(path), cancel)
        .map_err(|e| explain(&e, path))?;
    res.json().map_err(|e| explain(&e, path))
}

fn post_op(http: &Http, path: &str, body: &Value, cancel: &Cancel) -> Result<HiveOp, String> {
    let res = http
        .send_blocking(Request::post(path).json(body), cancel)
        .map_err(|e| explain(&e, path))?;
    let value: Value = res.json().map_err(|e| explain(&e, path))?;
    Ok(model::hive_op(&value))
}

/// The sentence the Tauri client would throw: JSON `detail`, then `error`, then `message`.
fn explain(err: &NetError, path: &str) -> String {
    match err {
        NetError::Http(http) => {
            body_message(&http.body).unwrap_or_else(|| format!("HTTP {}", http.status))
        }
        NetError::Timeout => format!("Request timed out after 30000ms ({path})"),
        NetError::Network(_) => {
            format!(
                "Cannot reach local API at {} ({path}). Check the connection and try again.",
                runtime::origin()
            )
        }
        NetError::Cancelled => "Request cancelled".into(),
        other => other.to_string(),
    }
}

/// `formatApiErrorBody`: `detail`, then `error`, then `message`. `None` means use `HTTP {status}`.
fn body_message(body: &str) -> Option<String> {
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return None;
    }
    let Ok(v) = serde_json::from_str::<Value>(trimmed) else {
        return Some(trimmed.to_string());
    };
    if let Some(s) = v.as_str() {
        let s = s.trim();
        return if s.is_empty() {
            None
        } else {
            Some(s.to_string())
        };
    }
    let Some(obj) = v.as_object() else {
        return Some(v.to_string());
    };
    if let Some(msg) = field_message(
        obj.get("detail")
            .or_else(|| obj.get("error"))
            .or_else(|| obj.get("message")),
    ) {
        return Some(msg);
    }
    if let Some(Value::String(s)) = obj.get("error") {
        let s = s.trim();
        if !s.is_empty() {
            return Some(s.to_string());
        }
    }
    if trimmed == "{}" || trimmed == "[]" {
        return None;
    }
    Some(trimmed.to_string())
}

fn field_message(detail: Option<&Value>) -> Option<String> {
    let detail = detail?;
    if let Some(s) = detail.as_str() {
        let s = s.trim();
        return if s.is_empty() {
            None
        } else {
            Some(s.to_string())
        };
    }
    if let Some(arr) = detail.as_array() {
        let parts: Vec<String> = arr
            .iter()
            .map(|item| match item {
                Value::String(s) => s.clone(),
                Value::Object(row) => {
                    let loc = row.get("loc").and_then(Value::as_array).map(|loc| {
                        loc.iter()
                            .map(|part| match part {
                                Value::String(s) => s.clone(),
                                other => other.to_string(),
                            })
                            .collect::<Vec<_>>()
                            .join(".")
                    });
                    let msg = row
                        .get("msg")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .unwrap_or_else(|| item.to_string());
                    match loc {
                        Some(loc) if !loc.is_empty() => format!("{loc}: {msg}"),
                        _ => msg,
                    }
                }
                other => other.to_string(),
            })
            .filter(|s| !s.is_empty())
            .collect();
        return if parts.is_empty() {
            None
        } else {
            Some(parts.join("; "))
        };
    }
    if detail.is_object() || detail.is_array() {
        return Some(detail.to_string());
    }
    None
}
