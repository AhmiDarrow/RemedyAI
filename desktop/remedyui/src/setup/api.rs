//! Loopback calls for setup, settings, vision, and xAI device login.
//! Paths are relative to the client's `/api` base.

use std::time::Duration;

use crate::runtime::Client;
use remedy_ui::net::{Cancel, Request};
use serde_json::{Value, json};

pub fn get_json(client: &Client, path: &str) -> Result<Value, String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(Request::get(path), &cancel)
        .map_err(|e| e.to_string())
        .and_then(|r| r.json::<Value>().map_err(|e| e.to_string()))
}

pub fn post_json(client: &Client, path: &str, body: &Value) -> Result<Value, String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(Request::post(path).json(body), &cancel)
        .map_err(|e| e.to_string())
        .and_then(|r| json_body(r))
}

pub fn put_json(client: &Client, path: &str, body: &Value) -> Result<Value, String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(Request::put(path).json(body), &cancel)
        .map_err(|e| e.to_string())
        .and_then(|r| json_body(r))
}

fn json_body(r: remedy_ui::net::Response) -> Result<Value, String> {
    match r.json::<Value>() {
        Ok(v) => Ok(v),
        Err(_) => Ok(Value::Null),
    }
}

pub fn put_setup(client: &Client, body: &Value) -> Result<Value, String> {
    put_json(client, "setup", body)
}

pub fn put_settings(client: &Client, body: &Value) -> Result<Value, String> {
    put_json(client, "settings", body)
}

pub fn begin_setup(client: &Client, mode: &str) -> Result<Value, String> {
    put_setup(
        client,
        &json!({
            "action": "begin",
            "onboarding_pending": true,
            "mode": mode,
        }),
    )
}

pub fn create_house_session(client: &Client) -> Result<String, String> {
    let v = post_json(
        client,
        "sessions",
        &json!({ "title": "House rules", "origin_channel": "desktop" }),
    )?;
    v.get("id")
        .and_then(|i| i.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| "the runtime did not return a session".into())
}

/// Device-code login. Does not open a browser; the code and URL stay on screen.
pub fn xai_login(client: &Client) -> Result<Value, String> {
    post_json(client, "auth/xai/login", &json!({}))
}

pub fn xai_status(client: &Client, session_id: &str) -> Result<Value, String> {
    get_json(client, &format!("auth/xai/login/status?session_id={session_id}"))
}

pub fn vision_status(client: &Client) -> Result<Value, String> {
    get_json(client, "vision/status")
}

pub fn vision_install(client: &Client) -> Result<Value, String> {
    post_json(client, "vision/install", &json!({ "prefer_cuda": true }))
}

pub fn mind_retry(client: &Client) -> Result<Value, String> {
    post_json(client, "setup/mind/retry", &json!({}))
}

pub fn vision_start(client: &Client) -> Result<Value, String> {
    post_json(client, "vision/start", &json!({}))
}

pub fn list_messages(client: &Client, session_id: &str) -> Result<Vec<(String, String, String)>, String> {
    let v = get_json(client, &format!("sessions/{session_id}/messages?limit=50"))?;
    let rows = v
        .get("messages")
        .and_then(|m| m.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let role = row.get("role")?.as_str()?.to_string();
            if role != "user" && role != "assistant" {
                return None;
            }
            let id = row
                .get("id")
                .and_then(|i| i.as_str())
                .unwrap_or("")
                .to_string();
            let text = row
                .get("content")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();
            Some((id, role, text))
        })
        .collect())
}

/// Signed desktop manifest. A failure leaves the update screen closed.
pub fn fetch_latest_json() -> Result<String, String> {
    let http = remedy_ui::net::Http::new(
        "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download",
    )
    .header("User-Agent", "RemedyDesktop-Updater/1.0")
    .header("Accept", "application/json")
    .header("Cache-Control", "no-cache");
    let cancel = Cancel::new();
    let res = http
        .send_blocking(
            Request::get("latest.json").timeout(Some(Duration::from_secs(20))),
            &cancel,
        )
        .map_err(|e| e.to_string())?;
    Ok(res.text())
}
