//! In-app browser: a managed Tauri WebviewWindow (real WebView2, not iframe).
//! Sites that block framing still load here.

use std::sync::Mutex;
use tauri::{AppHandle, Manager, State, WebviewUrl, WebviewWindowBuilder};

const LABEL: &str = "remedy-browser";

pub struct BrowserState {
    current_url: Mutex<String>,
}

impl Default for BrowserState {
    fn default() -> Self {
        Self {
            current_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
        }
    }
}

fn normalize_url(raw: &str) -> Result<String, String> {
    let u = raw.trim();
    if u.is_empty() {
        return Err("empty url".into());
    }
    if u.starts_with("javascript:") || u.starts_with("data:") || u.starts_with("file:") {
        return Err("unsupported url scheme".into());
    }
    if u.starts_with("http://") || u.starts_with("https://") || u.starts_with("about:") {
        return Ok(u.to_string());
    }
    Ok(format!("https://{u}"))
}

/// Open or navigate the in-app browser window to `url`.
#[tauri::command]
pub fn browser_navigate(
    app: AppHandle,
    state: State<'_, BrowserState>,
    url: String,
) -> Result<String, String> {
    let url = normalize_url(&url)?;
    {
        let mut cur = state.current_url.lock().map_err(|e| e.to_string())?;
        *cur = url.clone();
    }

    let parsed: url::Url = url
        .parse()
        .map_err(|e: url::ParseError| e.to_string())?;

    if let Some(win) = app.get_webview_window(LABEL) {
        win.navigate(parsed.clone())
            .map_err(|e| format!("navigate: {e}"))?;
        let _ = win.show();
        let _ = win.set_focus();
        return Ok(url);
    }

    WebviewWindowBuilder::new(&app, LABEL, WebviewUrl::External(parsed))
        .title("Remedy Browser")
        .inner_size(1100.0, 720.0)
        .center()
        .build()
        .map_err(|e| format!("create browser: {e}"))?;
    Ok(url)
}

#[tauri::command]
pub fn browser_reload(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    // Re-navigate current page via eval if available; otherwise re-show
    win.eval("window.location.reload()")
        .map_err(|e| format!("reload: {e}"))
}

#[tauri::command]
pub fn browser_go_back(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    win.eval("window.history.back()")
        .map_err(|e| format!("back: {e}"))
}

#[tauri::command]
pub fn browser_go_forward(app: AppHandle) -> Result<(), String> {
    let win = app
        .get_webview_window(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    win.eval("window.history.forward()")
        .map_err(|e| format!("forward: {e}"))
}

#[tauri::command]
pub fn browser_current_url(state: State<'_, BrowserState>) -> Result<String, String> {
    state
        .current_url
        .lock()
        .map(|g| g.clone())
        .map_err(|e| e.to_string())
}
