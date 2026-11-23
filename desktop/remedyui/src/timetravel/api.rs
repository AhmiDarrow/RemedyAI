//! Timeline and restore calls.
//!
//! Same routes as the Tauri app (`apiFetch` under `/api` on loopback `:7400`):
//! `GET /sessions/{id}/timeline` and `POST /sessions/{id}/time-travel`.
//! Snapshot storage stays in the runtime. This module only presents it.
//!
//! `runtime::Client`'s HTTP handle is private to that module, so the client
//! here is built the same way as [`crate::runtime::Client::new`]: base
//! `{origin}/api`, bearer from [`crate::runtime::load_token`].

use remedy_ui::net::{Http, NetError, Request};
use remedy_ui::reactive::Cancel;
use serde_json::Value;

/// One node from `GET /sessions/{id}/timeline`.
///
/// Field names match the runtime JSON. The rail draws `kind == "user"` rows
/// and hangs the first assistant child (`parent_user_id`) under each one.
#[derive(Clone, Debug, PartialEq)]
pub struct Step {
    /// 1-based step number from the server. Assistant rows share their parent's.
    pub step: u32,
    /// Row id. The server uses the message id.
    pub id: String,
    /// `user`, `assistant`, or `system`.
    pub kind: String,
    /// Short title, such as `Step 3`.
    pub label: String,
    /// Clipped message text.
    pub preview: String,
    /// RFC3339, when the runtime sent one.
    pub created_at: Option<String>,
    /// Message the restore call sends back.
    pub message_id: String,
    /// Tool calls rolled into this step. `0` hides the count on the row.
    pub tool_count: u32,
    /// Tool names, capped by the runtime.
    pub tools: Vec<String>,
    /// False for system rows and for anything the runtime will not roll back.
    pub can_restore: bool,
    /// Set on assistant rows: the user message they belong to.
    pub parent_user_id: Option<String>,
    /// First assistant preview, also copied onto the user row by the runtime.
    pub assistant_preview: Option<String>,
}

/// `GET /sessions/{id}/timeline`, blocking. Call from a worker thread.
pub fn fetch_timeline(session_id: &str) -> Result<Vec<Step>, String> {
    let session_id = session_id.trim();
    if session_id.is_empty() {
        return Ok(Vec::new());
    }
    let http = client();
    let cancel = Cancel::new();
    let path = format!("sessions/{session_id}/timeline");
    let route = format!("/sessions/{session_id}/timeline");
    http.send_blocking(Request::get(&path), &cancel)
        .map_err(|err| explain(err, &route))
        .and_then(|response| response.json::<Value>().map_err(|err| explain(err, &route)))
        .map(|value| parse_steps(&value))
}

/// `POST /sessions/{id}/time-travel` with `{ "message_id": ... }`, blocking.
pub fn restore(session_id: &str, message_id: &str) -> Result<(), String> {
    let session_id = session_id.trim();
    let message_id = message_id.trim();
    if session_id.is_empty() || message_id.is_empty() {
        return Err("message_id required".into());
    }
    let http = client();
    let cancel = Cancel::new();
    let path = format!("sessions/{session_id}/time-travel");
    let route = format!("/sessions/{session_id}/time-travel");
    let body = serde_json::json!({ "message_id": message_id });
    http.send_blocking(Request::post(&path).json(&body), &cancel)
        .map(|_| ())
        .map_err(|err| explain(err, &route))
}

/// User rows, in server order. The rail does not draw assistant or system nodes.
pub fn user_steps(steps: &[Step]) -> Vec<Step> {
    steps
        .iter()
        .filter(|step| step.kind == "user")
        .cloned()
        .collect()
}

/// `→ preview` of the first assistant row under `user_id`, when it has text.
pub fn assistant_line(steps: &[Step], user_id: &str) -> Option<String> {
    let preview = steps
        .iter()
        .find(|step| {
            step.kind == "assistant" && step.parent_user_id.as_deref() == Some(user_id)
        })?
        .preview
        .trim();
    if preview.is_empty() {
        None
    } else {
        Some(format!("→ {preview}"))
    }
}

/// True when `id` is the earliest user row.
pub fn is_first_user(steps: &[Step], id: &str) -> bool {
    steps
        .iter()
        .find(|step| step.kind == "user")
        .is_some_and(|step| step.message_id == id)
}

/// True when `id` is the latest user row (the accent dot).
pub fn is_last_user(steps: &[Step], id: &str) -> bool {
    steps
        .iter()
        .rev()
        .find(|step| step.kind == "user")
        .is_some_and(|step| step.message_id == id)
}

/// Reads the `steps` array. A missing or non-array `steps` is an empty rail,
/// matching the Tauri client (`Array.isArray(data.steps) ? data.steps : []`).
pub fn parse_steps(value: &Value) -> Vec<Step> {
    let Some(rows) = value.get("steps").and_then(Value::as_array) else {
        return Vec::new();
    };
    rows.iter().filter_map(parse_step).collect()
}

fn parse_step(value: &Value) -> Option<Step> {
    let id = text_field(value, "id").unwrap_or_default();
    let message_id = text_field(value, "message_id").unwrap_or(id.clone());
    if message_id.is_empty() {
        return None;
    }
    let id = if id.is_empty() { message_id.clone() } else { id };
    Some(Step {
        step: number_field(value, "step"),
        id,
        kind: text_field(value, "kind").unwrap_or_default(),
        label: text_field(value, "label").unwrap_or_default(),
        preview: text_field(value, "preview").unwrap_or_default(),
        created_at: text_field(value, "created_at"),
        message_id,
        tool_count: number_field(value, "tool_count"),
        tools: string_list(value, "tools"),
        // Absent `can_restore` disables the row, same as `!undefined` in the button.
        can_restore: value
            .get("can_restore")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        parent_user_id: text_field(value, "parent_user_id"),
        assistant_preview: text_field(value, "assistant_preview"),
    })
}

fn text_field(value: &Value, key: &str) -> Option<String> {
    let text = value.get(key)?.as_str()?.trim();
    if text.is_empty() { None } else { Some(text.to_string()) }
}

fn number_field(value: &Value, key: &str) -> u32 {
    value
        .get(key)
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .min(u64::from(u32::MAX)) as u32
}

fn string_list(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// A sentence for the rail. Wording follows `formatApiErrorBody` / `apiFetch` in
/// `desktop/src/api/client.ts`: JSON `detail` when the server sent one, otherwise
/// the status, a timeout that names the route, or a reachability line.
pub fn explain(err: NetError, path: &str) -> String {
    match err {
        NetError::Http(http) => http_message(&http),
        NetError::Network(_) => format!(
            "Cannot reach local API at {} ({path}). Check the connection and try again.",
            crate::runtime::origin()
        ),
        NetError::Timeout => format!("Request timed out after 30000ms ({path})"),
        NetError::Cancelled => "Request cancelled".into(),
        other => other.to_string(),
    }
}

/// The text `apiFetch` throws for a non-2xx body.
fn http_message(http: &remedy_ui::net::HttpError) -> String {
    if let Some(detail) = http.detail.as_deref().map(str::trim).filter(|d| !d.is_empty()) {
        return detail.to_string();
    }
    if let Ok(value) = serde_json::from_str::<Value>(&http.body) {
        if let Some(text) = error_text(&value) {
            return text;
        }
    }
    let body = http.body.trim();
    if body.is_empty() || body == "{}" || body == "[]" {
        format!("HTTP {}", http.status)
    } else {
        body.to_string()
    }
}

/// `detail`, then `error`, then `message`, as `formatApiErrorBody` reads them.
fn error_text(value: &Value) -> Option<String> {
    let object = value.as_object()?;
    for key in ["detail", "error", "message"] {
        let Some(item) = object.get(key) else {
            continue;
        };
        if let Some(text) = item.as_str().map(str::trim).filter(|d| !d.is_empty()) {
            return Some(text.to_string());
        }
        if !item.is_null() && item.as_str().is_none() {
            let rendered = item.to_string();
            if rendered != "{}" && rendered != "[]" {
                return Some(rendered);
            }
        }
    }
    None
}

fn client() -> Http {
    let origin = crate::runtime::origin();
    let base = format!("{}/api", origin.trim_end_matches('/'));
    let mut http = Http::new(&base);
    if let Some(token) = crate::runtime::load_token().filter(|token| !token.is_empty()) {
        http = http.header("Authorization", format!("Bearer {token}"));
    }
    http
}

/// A timeline in the runtime's shape, for the preview rail (no network).
pub fn sample_timeline() -> Value {
    serde_json::json!({
        "session_id": "s1",
        "count": 5,
        "steps": [
            {
                "step": 1,
                "id": "u1",
                "kind": "user",
                "label": "Step 1",
                "preview": "Add a timeline so I can roll this chat, and the files Remedy wrote, back to an earlier moment.",
                "created_at": "2026-11-22T18:04:00Z",
                "message_id": "u1",
                "tool_count": 3,
                "tools": ["read_file", "write_file", "search"],
                "assistant_ids": ["a1"],
                "can_restore": true,
                "assistant_preview": "The rail is up. Restore asks first, then rolls the chat, checkpoints, and workspace files back together."
            },
            {
                "step": 1,
                "id": "a1",
                "kind": "assistant",
                "label": "Step 1 · reply",
                "preview": "The rail is up. Restore asks first, then rolls the chat, checkpoints, and workspace files back together.",
                "created_at": "2026-11-22T18:05:00Z",
                "message_id": "a1",
                "parent_user_id": "u1",
                "tool_count": 3,
                "tools": ["read_file", "write_file", "search"],
                "can_restore": true
            },
            {
                "step": 2,
                "id": "u2",
                "kind": "user",
                "label": "Step 2",
                "preview": "Keep the latest step on the accent, and leave the older ones on the hairline.",
                "created_at": "2026-11-22T18:12:00Z",
                "message_id": "u2",
                "tool_count": 0,
                "tools": [],
                "can_restore": true
            },
            {
                "step": 2,
                "id": "a2",
                "kind": "assistant",
                "label": "Step 2 · reply",
                "preview": "The latest dot takes the accent. Earlier steps stay on the rail until you restore.",
                "created_at": "2026-11-22T18:13:00Z",
                "message_id": "a2",
                "parent_user_id": "u2",
                "tool_count": 0,
                "tools": [],
                "can_restore": true
            },
            {
                "step": 3,
                "id": "u3",
                "kind": "user",
                "label": "Step 3",
                "preview": "Ask before restoring. Later messages and checkpoints go with the rollback.",
                "created_at": "2026-11-22T18:20:00Z",
                "message_id": "u3",
                "tool_count": 0,
                "tools": [],
                "can_restore": true
            }
        ]
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_user_rows_and_assistant_children() {
        let steps = parse_steps(&sample_timeline());
        let users = user_steps(&steps);
        assert_eq!(users.len(), 3);
        assert_eq!(users[0].step, 1);
        assert_eq!(users[0].message_id, "u1");
        assert_eq!(users[0].tool_count, 3);
        assert_eq!(users[0].tools, ["read_file", "write_file", "search"]);
        assert_eq!(
            users[0].created_at.as_deref(),
            Some("2026-11-22T18:04:00Z")
        );
        assert!(users[0].assistant_preview.as_deref().unwrap_or("").starts_with("The rail is up."));
        assert!(users[0].can_restore);
        assert!(is_first_user(&steps, "u1"));
        assert!(is_last_user(&steps, "u3"));
        assert!(!is_last_user(&steps, "u1"));
        let line = assistant_line(&steps, "u1").expect("assistant child");
        assert!(line.starts_with("→ The rail is up."));
        assert!(assistant_line(&steps, "u3").is_none());
    }

    #[test]
    fn missing_steps_and_restore_flag_match_the_tauri_client() {
        assert!(parse_steps(&serde_json::json!({})).is_empty());
        assert!(parse_steps(&serde_json::json!({"steps": {}})).is_empty());
        let steps = parse_steps(&serde_json::json!({
            "steps": [
                {"kind": "system", "message_id": "sys", "label": "System", "preview": "boot"},
                {"kind": "user", "id": "", "message_id": "", "label": "dropped"},
                {"kind": "user", "message_id": "u", "label": "Step 1", "preview": "hi", "can_restore": true}
            ]
        }));
        assert_eq!(steps.len(), 2);
        assert!(!steps[0].can_restore);
        assert_eq!(steps[1].id, "u");
        assert!(steps[1].can_restore);
        assert_eq!(user_steps(&steps).len(), 1);
    }

    #[test]
    fn network_errors_stay_short() {
        let path = "/sessions/s1/timeline";
        assert_eq!(
            explain(NetError::Timeout, path),
            "Request timed out after 30000ms (/sessions/s1/timeline)"
        );
        assert_eq!(explain(NetError::Cancelled, path), "Request cancelled");
        let http = serde_json::json!({"error": "rolled back nothing"});
        // `HttpError` is non_exhaustive, so this checks the same keys the rail reads.
        assert_eq!(
            error_text(&http).as_deref(),
            Some("rolled back nothing")
        );
        assert!(error_text(&serde_json::json!({})).is_none());
    }
}
