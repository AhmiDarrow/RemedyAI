//! HTTP against the local mind. Paths are under `/api` because [`Client`]
//! already includes that prefix. Calls leave the UI thread and come back
//! through a waker.

use remedy_ui::net::Request;
use remedy_ui::reactive::{Cancel, waker};
use serde_json::{Value, json};

use crate::runtime::Client;

use super::state::Settings;

pub fn call(client: &Client, req: Request, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    let http = client.http();
    let _ = std::thread::Builder::new().name("remedy-settings".into()).spawn(move || {
        let cancel = Cancel::new();
        let out = http
            .send_blocking(req, &cancel)
            .map_err(|e| e.to_string())
            .and_then(|r| r.json::<Value>().map_err(|e| e.to_string()));
        done(out);
    });
}

pub fn get(client: &Client, path: &str, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    call(client, Request::get(path), done);
}

pub fn post(client: &Client, path: &str, body: &Value, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    call(client, Request::post(path).json(body), done);
}

pub fn put(client: &Client, path: &str, body: &Value, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    call(client, Request::put(path).json(body), done);
}

pub fn delete(client: &Client, path: &str, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    call(client, Request::delete(path), done);
}

/// Runs `done` on the UI thread. Drops the result if the UI is gone.
/// `done` is `Send` because the waker posts it across threads.
pub fn ui(done: impl FnOnce(Result<Value, String>) + Send + 'static) -> impl FnOnce(Result<Value, String>) + Send + 'static {
    let wake = waker().ok();
    move |out| {
        if let Some(wake) = wake {
            let _ = wake.post(move || done(out));
        }
    }
}

pub fn put_settings(s: Settings, body: Value, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    let Some(client) = s.client.get() else {
        done(Err("Remedy is not connected.".into()));
        return;
    };
    put(&client, "settings", &body, ui(done));
}

pub fn get_settings(s: Settings, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    let Some(client) = s.client.get() else {
        done(Err("Remedy is not connected.".into()));
        return;
    };
    get(&client, "settings", ui(done));
}

pub fn request(s: Settings, method: &str, path: String, body: Option<Value>, done: impl FnOnce(Result<Value, String>) + Send + 'static) {
    let Some(client) = s.client.get() else {
        done(Err("Remedy is not connected.".into()));
        return;
    };
    let req = match method {
        "POST" => Request::post(&path).json(&body.unwrap_or_else(|| json!({}))),
        "PUT" => Request::put(&path).json(&body.unwrap_or_else(|| json!({}))),
        "DELETE" => Request::delete(&path),
        _ => Request::get(&path),
    };
    call(&client, req, ui(done));
}
