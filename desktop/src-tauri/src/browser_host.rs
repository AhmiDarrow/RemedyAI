//! Embedded Browser slide: a **child WebView2** inside the main Remedy window
//! (Chromium engine). Not a separate popup unless the UI calls system open.
//!
//! Requires Tauri feature `unstable` (window.add_child / multiwebview).

use serde::Deserialize;
use std::sync::Mutex;
use tauri::{
    AppHandle, LogicalPosition, LogicalSize, Manager, State, WebviewUrl,
};
use tauri::webview::WebviewBuilder;
use url::Url;

const LABEL: &str = "remedy-browser-embed";

#[derive(Debug, Clone, Deserialize)]
pub struct BrowserBounds {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

pub struct BrowserState {
    current_url: Mutex<String>,
    last_bounds: Mutex<Option<BrowserBounds>>,
}

impl Default for BrowserState {
    fn default() -> Self {
        Self {
            current_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
            last_bounds: Mutex::new(None),
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

fn main_window(app: &AppHandle) -> Result<tauri::Window, String> {
    app.get_window("main")
        .or_else(|| app.get_webview("main").map(|w| w.window()))
        .or_else(|| {
            app.get_webview_window("main")
                .and_then(|ww| app.get_window(ww.label()))
        })
        .ok_or_else(|| "main window not found".to_string())
}

fn clamp_bounds(b: &BrowserBounds) -> BrowserBounds {
    BrowserBounds {
        x: b.x.max(0.0),
        y: b.y.max(0.0),
        width: b.width.max(80.0),
        height: b.height.max(80.0),
    }
}

fn destroy_embed(app: &AppHandle) {
    // Child webview (embedded)
    if let Some(wv) = app.get_webview(LABEL) {
        let _ = wv.hide();
        let _ = wv.close();
        log::info!("browser embed closed ({LABEL})");
    }
    // Legacy popup window from older builds
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
        log::info!("legacy popup browser destroyed");
    }
}

/// Destroy embedded browser (idempotent).
#[tauri::command]
pub fn browser_close(app: AppHandle) -> Result<(), String> {
    destroy_embed(&app);
    Ok(())
}

#[tauri::command]
pub fn browser_is_open(app: AppHandle) -> bool {
    app.get_webview(LABEL).is_some()
}

#[tauri::command]
pub fn browser_hide(app: AppHandle) -> Result<(), String> {
    if let Some(wv) = app.get_webview(LABEL) {
        wv.hide().map_err(|e| format!("hide: {e}"))?;
    }
    Ok(())
}

#[tauri::command]
pub fn browser_show(app: AppHandle) -> Result<(), String> {
    if let Some(wv) = app.get_webview(LABEL) {
        wv.show().map_err(|e| format!("show: {e}"))?;
    }
    Ok(())
}

/// Reposition/resize the embedded webview to match the Browser slide host.
#[tauri::command]
pub fn browser_set_bounds(
    app: AppHandle,
    state: State<'_, BrowserState>,
    bounds: BrowserBounds,
) -> Result<(), String> {
    let b = clamp_bounds(&bounds);
    if let Ok(mut g) = state.last_bounds.lock() {
        *g = Some(b.clone());
    }
    if let Some(wv) = app.get_webview(LABEL) {
        wv.set_position(LogicalPosition::new(b.x, b.y))
            .map_err(|e| format!("set_position: {e}"))?;
        wv.set_size(LogicalSize::new(b.width, b.height))
            .map_err(|e| format!("set_size: {e}"))?;
        let _ = wv.show();
    }
    Ok(())
}

/// Open or navigate the **embedded** WebView2 inside the main window.
/// Pass CSS-pixel bounds of the Browser slide content area (from getBoundingClientRect).
///
/// Must stay `async` on Windows — sync create can deadlock WebView2.
#[tauri::command]
pub async fn browser_navigate(
    app: AppHandle,
    state: State<'_, BrowserState>,
    url: String,
    bounds: Option<BrowserBounds>,
) -> Result<String, String> {
    let url = normalize_url(&url)?;
    {
        let mut cur = state.current_url.lock().map_err(|e| e.to_string())?;
        *cur = url.clone();
    }

    let parsed: Url = url.parse().map_err(|e: url::ParseError| e.to_string())?;

    let b = bounds
        .or_else(|| state.last_bounds.lock().ok().and_then(|g| g.clone()))
        .map(|b| clamp_bounds(&b))
        .unwrap_or(BrowserBounds {
            x: 300.0,
            y: 80.0,
            width: 640.0,
            height: 480.0,
        });
    if let Ok(mut g) = state.last_bounds.lock() {
        *g = Some(b.clone());
    }

    // Already embedded — navigate + re-bounds
    if let Some(wv) = app.get_webview(LABEL) {
        wv.set_position(LogicalPosition::new(b.x, b.y))
            .map_err(|e| format!("set_position: {e}"))?;
        wv.set_size(LogicalSize::new(b.width, b.height))
            .map_err(|e| format!("set_size: {e}"))?;
        wv.navigate(parsed.clone())
            .map_err(|e| format!("navigate: {e}"))?;
        let _ = wv.show();
        // Second paint — mitigates multiwebview white flash on some GPUs
        let wv2 = wv.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(150));
            let _ = wv2.eval(
                "try{if(!document.body||document.body.childElementCount===0)location.reload()}catch(e){}",
            );
        });
        log::info!("browser embed navigate {url}");
        return Ok(url);
    }

    // Close any legacy popup first
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
    }

    let window = main_window(&app)?;
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(parsed));

    // add_child already runs builder on main thread internally
    let wv = window
        .add_child(
            builder,
            LogicalPosition::new(b.x, b.y),
            LogicalSize::new(b.width, b.height),
        )
        .map_err(|e| format!("embed browser (add_child): {e}"))?;

    let _ = wv.show();
    // Re-navigate after create — known multiwebview white-screen workaround
    let wv2 = wv.clone();
    let url_reload = url.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        if let Ok(u) = url_reload.parse::<Url>() {
            let _ = wv2.navigate(u);
        }
        let _ = wv2.show();
    });

    log::info!(
        "browser embed created {url} @ ({},{}) {}x{}",
        b.x,
        b.y,
        b.width,
        b.height
    );
    Ok(url)
}

#[tauri::command]
pub fn browser_reload(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.location.reload()")
        .map_err(|e| format!("reload: {e}"))
}

#[tauri::command]
pub fn browser_go_back(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.history.back()")
        .map_err(|e| format!("back: {e}"))
}

#[tauri::command]
pub fn browser_go_forward(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.history.forward()")
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

pub fn close_browser_on_quit(app: &AppHandle) {
    destroy_embed(app);
}
