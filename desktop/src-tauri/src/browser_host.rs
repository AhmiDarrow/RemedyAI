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
use tauri::utils::config::Color;
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
    // Prefer explicit Window handle; WebviewWindow apps often only register via webview.
    if let Some(w) = app.get_window("main") {
        return Ok(w);
    }
    if let Some(wv) = app.get_webview("main") {
        return Ok(wv.window());
    }
    if let Some(ww) = app.get_webview_window("main") {
        // WebviewWindow shares label with its primary webview.
        if let Some(wv) = app.get_webview(ww.label()) {
            return Ok(wv.window());
        }
    }
    // Last resort: any window (single-window desktop).
    if let Some((_, w)) = app.windows().into_iter().next() {
        log::warn!("browser: using fallback window label={}", w.label());
        return Ok(w);
    }
    Err("main window not found — cannot embed browser".into())
}

fn clamp_bounds(b: &BrowserBounds) -> BrowserBounds {
    BrowserBounds {
        x: b.x.max(0.0),
        y: b.y.max(0.0),
        width: b.width.max(80.0),
        height: b.height.max(80.0),
    }
}

fn apply_bounds(wv: &tauri::Webview, b: &BrowserBounds) -> Result<(), String> {
    wv.set_position(LogicalPosition::new(b.x, b.y))
        .map_err(|e| format!("set_position: {e}"))?;
    wv.set_size(LogicalSize::new(b.width, b.height))
        .map_err(|e| format!("set_size: {e}"))?;
    let _ = wv.show();
    Ok(())
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
        apply_bounds(&wv, &b)?;
    }
    Ok(())
}

fn schedule_reload(wv: tauri::Webview, url: String, delay_ms: u64) {
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(delay_ms));
        if let Ok(u) = url.parse::<Url>() {
            if let Err(e) = wv.navigate(u) {
                log::warn!("browser delayed navigate failed: {e}");
            }
        }
        let _ = wv.show();
        // Force paint if WebView2 stayed white (known multiwebview glitch).
        let _ = wv.eval(
            "try{if(!document.body||document.body.childElementCount===0){location.reload()}}catch(e){}",
        );
    });
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

    // Already embedded — navigate + re-bounds; recreate if navigate fails (stale child).
    if let Some(wv) = app.get_webview(LABEL) {
        if let Err(e) = apply_bounds(&wv, &b) {
            log::warn!("browser bounds on existing embed failed: {e}");
        }
        match wv.navigate(parsed.clone()) {
            Ok(()) => {
                let _ = wv.show();
                schedule_reload(wv.clone(), url.clone(), 200);
                log::info!("browser embed navigate {url}");
                return Ok(url);
            }
            Err(e) => {
                log::warn!("browser navigate on existing embed failed ({e}); recreating");
                destroy_embed(&app);
            }
        }
    }

    // Close any legacy popup first
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
    }

    let window = main_window(&app)?;
    // about:blank first → then navigate: avoids multiwebview white-screen on some GPUs.
    let blank: Url = "about:blank".parse().map_err(|e: url::ParseError| e.to_string())?;
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(blank))
        .focused(true)
        .background_color(Color(255, 255, 255, 255));

    // add_child already runs builder on main thread internally
    let wv = window
        .add_child(
            builder,
            LogicalPosition::new(b.x, b.y),
            LogicalSize::new(b.width, b.height),
        )
        .map_err(|e| {
            log::error!("browser add_child failed: {e}");
            format!(
                "embed browser failed: {e}. Try ↗ system browser, or reinstall WebView2 Runtime."
            )
        })?;

    let _ = wv.show();
    // Immediate navigate to target
    if let Err(e) = wv.navigate(parsed) {
        log::warn!("browser initial navigate failed: {e}");
    }
    // Delayed re-navigate + paint (known multiwebview white-screen workaround)
    schedule_reload(wv.clone(), url.clone(), 120);
    schedule_reload(wv.clone(), url.clone(), 400);

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

/// CSS-pixel bounds of the embed (for host screenshot crop / layout).
#[tauri::command]
pub fn browser_last_bounds(state: State<'_, BrowserState>) -> Result<Option<BrowserBounds>, String> {
    state
        .last_bounds
        .lock()
        .map(|g| g.clone())
        .map_err(|e| e.to_string())
}

/// Agent-driven input into the embed (coordinates relative to the page viewport).
/// Used by the computer-use host poller — not a feature gate, task completion path.
#[tauri::command]
pub fn browser_agent_action(
    app: AppHandle,
    action: String,
    x: Option<f64>,
    y: Option<f64>,
    x2: Option<f64>,
    y2: Option<f64>,
    text: Option<String>,
    key: Option<String>,
    button: Option<String>,
    dy: Option<i32>,
) -> Result<String, String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open — navigate first".to_string())?;
    let act = action.to_lowercase();
    let js = match act.as_str() {
        "click" => {
            let cx = x.unwrap_or(0.0);
            let cy = y.unwrap_or(0.0);
            let btn = button.unwrap_or_else(|| "left".into());
            let js_btn = if btn == "right" {
                "contextmenu"
            } else {
                "click"
            };
            format!(
                r#"(function(){{
  const x={cx}, y={cy};
  const el=document.elementFromPoint(x,y)||document.body;
  if(!el) return 'no element';
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:{btn_code}}};
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('{js_btn}', opts));
  return 'ok:'+ (el.tagName||'?');
}})()"#,
                cx = cx,
                cy = cy,
                js_btn = js_btn,
                btn_code = if btn == "right" { 2 } else { 0 },
            )
        }
        "type" | "type_text" => {
            let t = text.unwrap_or_default();
            // Escape for JS string
            let escaped = t
                .replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace('\n', "\\n")
                .replace('\r', "\\r");
            format!(
                r#"(function(){{
  const t='{escaped}';
  const el=document.activeElement||document.body;
  if(el && (el.isContentEditable || /^(INPUT|TEXTAREA)$/.test(el.tagName))){{
    const start=el.selectionStart??el.value?.length??0;
    const end=el.selectionEnd??start;
    if(typeof el.value==='string'){{
      el.value=el.value.slice(0,start)+t+el.value.slice(end);
      try{{ el.selectionStart=el.selectionEnd=start+t.length; }}catch(e){{}}
      el.dispatchEvent(new Event('input',{{bubbles:true}}));
    }} else {{
      document.execCommand('insertText', false, t);
    }}
    return 'ok';
  }}
  for(const ch of t){{
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{{key:ch,bubbles:true}}));
    document.activeElement?.dispatchEvent(new KeyboardEvent('keypress',{{key:ch,bubbles:true}}));
    document.activeElement?.dispatchEvent(new KeyboardEvent('keyup',{{key:ch,bubbles:true}}));
  }}
  return 'ok-fallback';
}})()"#
            )
        }
        "key" => {
            let k = key.unwrap_or_else(|| "Enter".into());
            let escaped = k.replace('\\', "\\\\").replace('\'', "\\'");
            format!(
                r#"(function(){{
  const key='{escaped}';
  const el=document.activeElement||document.body;
  const opts={{key:key,code:key,bubbles:true,cancelable:true}};
  el.dispatchEvent(new KeyboardEvent('keydown', opts));
  el.dispatchEvent(new KeyboardEvent('keyup', opts));
  if(key==='Enter' && el.form) try{{ el.form.requestSubmit(); }}catch(e){{}}
  return 'ok';
}})()"#
            )
        }
        "scroll" => {
            let cx = x.unwrap_or(0.0);
            let cy = y.unwrap_or(0.0);
            // dy notches: negative = scroll down (increase scrollTop)
            let delta_y = -(dy.unwrap_or(-3) * 80);
            format!(
                r#"(function(){{
  const el=document.elementFromPoint({cx},{cy})||document.scrollingElement||document.documentElement;
  el.scrollBy({{top:{delta_y},left:0,behavior:'instant'}});
  return 'ok';
}})()"#,
                cx = cx,
                cy = cy,
                delta_y = delta_y,
            )
        }
        "drag" => {
            let x1 = x.unwrap_or(0.0);
            let y1 = y.unwrap_or(0.0);
            let x2 = x2.unwrap_or(x1);
            let y2 = y2.unwrap_or(y1);
            format!(
                r#"(function(){{
  const el=document.elementFromPoint({x1},{y1})||document.body;
  el.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true,clientX:{x1},clientY:{y1}}}));
  el.dispatchEvent(new MouseEvent('mousemove',{{bubbles:true,clientX:{x2},clientY:{y2}}}));
  el.dispatchEvent(new MouseEvent('mouseup',{{bubbles:true,clientX:{x2},clientY:{y2}}}));
  return 'ok';
}})()"#
            )
        }
        other => return Err(format!("unknown browser agent action: {other}")),
    };
    wv.eval(&js).map_err(|e| format!("browser agent action: {e}"))?;
    log::info!("browser agent action {act}");
    Ok(format!("browser:{act}:ok"))
}

pub fn close_browser_on_quit(app: &AppHandle) {
    destroy_embed(app);
}
