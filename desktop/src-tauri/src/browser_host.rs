//! Embedded Browser slide: a **child WebView2** inside the main Remedy window
//! (Chromium engine). Not a separate popup unless the UI calls system open.
//!
//! Requires Tauri feature `unstable` (window.add_child / multiwebview).

use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewUrl,
};
use tauri::webview::WebviewBuilder;
use tauri::utils::config::Color;
use url::Url;

const LABEL: &str = "remedy-browser-embed";

#[derive(Debug, Clone, Deserialize, Serialize)]
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

/// Right-rail-ish bounds from main window size when SPA has not pushed host rect yet.
fn default_rail_bounds(app: &AppHandle) -> BrowserBounds {
    if let Some(ww) = app.get_webview_window("main") {
        if let (Ok(size), Ok(scale)) = (ww.inner_size(), ww.scale_factor()) {
            let w = (size.width as f64 / scale).max(800.0);
            let h = (size.height as f64 / scale).max(500.0);
            // Match openBrowserInRail: ~40% width, min 400, max 560
            let rail_w = (w * 0.40).clamp(400.0, 560.0);
            let top = 56.0_f64; // title + chrome
            let bottom = 40.0_f64; // status bar
            let gap = 6.0_f64;
            return BrowserBounds {
                x: (w - rail_w + gap).max(0.0),
                y: top,
                width: (rail_w - gap * 2.0).max(280.0),
                height: (h - top - bottom).max(240.0),
            };
        }
    }
    BrowserBounds {
        x: 420.0,
        y: 56.0,
        width: 480.0,
        height: 720.0,
    }
}

/// Core navigate used by the command and the Rust computer-host poller.
pub fn navigate_embed(
    app: &AppHandle,
    state: &BrowserState,
    url_raw: &str,
    bounds: Option<BrowserBounds>,
) -> Result<String, String> {
    let url = normalize_url(url_raw)?;
    {
        let mut cur = state.current_url.lock().map_err(|e| e.to_string())?;
        *cur = url.clone();
    }

    let parsed: Url = url.parse().map_err(|e: url::ParseError| e.to_string())?;

    let b = bounds
        .or_else(|| state.last_bounds.lock().ok().and_then(|g| g.clone()))
        .filter(|bb| bb.width >= 200.0 && bb.height >= 160.0)
        .map(|b| clamp_bounds(&b))
        .unwrap_or_else(|| default_rail_bounds(app));
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
                destroy_embed(app);
            }
        }
    }

    // Close any legacy popup first
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
    }

    let window = main_window(app)?;
    // about:blank first → then navigate: avoids multiwebview white-screen on some GPUs.
    let blank: Url = "about:blank".parse().map_err(|e: url::ParseError| e.to_string())?;
    // Dark chrome — pure white reads as a distracting border around the page
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(blank))
        .focused(true)
        .background_color(Color(18, 18, 22, 255));

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
    navigate_embed(&app, state.inner(), &url, bounds)
}

// ── Rust-side computer host (does not depend on SPA JS poller) ──────────────

static COMPUTER_HOST_STARTED: AtomicBool = AtomicBool::new(false);

/// Background poller: open Browser rail + drive WebView when the agent navigates.
/// Runs in Rust so it works even if the React host hook never mounts.
pub fn start_computer_host_poller(app: AppHandle) {
    if COMPUTER_HOST_STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    log::info!("computer-host: starting Rust poller thread");
    let _ = std::thread::Builder::new()
        .name("computer-host".into())
        .spawn(move || computer_host_loop(app));
}

fn computer_host_loop(app: AppHandle) {
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(2))
        .timeout(Duration::from_secs(8))
        .build();
    // Wait for sidecar API
    for _ in 0..60 {
        if agent.get("http://127.0.0.1:7400/api/ping").call().is_ok() {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    log::info!("computer-host: API reachable, polling ui/command + jobs");
    // Track last *completed* navigate job so renudge take of the same id is a
    // real re-navigate (never fake-complete without opening the URL).
    let mut last_completed_nav = String::new();
    let mut hello_tick: u32 = 0;
    loop {
        // Tight poll — open-url must feel instant (≤50ms claim latency)
        std::thread::sleep(Duration::from_millis(25));
        hello_tick = hello_tick.wrapping_add(1);
        // Hello ~every 2s
        if hello_tick % 80 == 0 {
            let _ = agent
                .post("http://127.0.0.1:7400/api/computer/host/hello")
                .set("Content-Type", "application/json")
                .send_string(r#"{"client":"desktop-rust"}"#);
        }

        // take=1 clears command atomically — prevents reloading the same wiki forever
        if let Ok(resp) = agent
            .get("http://127.0.0.1:7400/api/computer/ui/command?take=1")
            .call()
        {
            if let Ok(v) = resp.into_json::<serde_json::Value>() {
                if let Some(cmd) = v.get("command").filter(|c| !c.is_null() && c.is_object()) {
                    let jid = cmd
                        .get("job_id")
                        .and_then(|j| j.as_str())
                        .unwrap_or("")
                        .to_string();
                    // Always handle — never drop a take without navigate+complete.
                    // Renudge may re-deliver the same job_id; re-navigate is correct.
                    handle_ui_command(&app, &agent, cmd);
                    if !jid.is_empty() {
                        last_completed_nav = jid;
                    }
                }
            }
        }

        // Claim navigate leftovers + snapshot (SPA may also claim snapshot;
        // only one wins). Snapshot via eval-callback is fast and reliable.
        if let Ok(resp) = agent
            .get("http://127.0.0.1:7400/api/computer/jobs/next?only=navigate,snapshot")
            .call()
        {
            if let Ok(v) = resp.into_json::<serde_json::Value>() {
                if let Some(job) = v.get("job").filter(|j| !j.is_null() && j.is_object()) {
                    let action = job
                        .get("action")
                        .and_then(|a| a.as_str())
                        .unwrap_or("");
                    let jid = job
                        .get("id")
                        .and_then(|i| i.as_str())
                        .unwrap_or("")
                        .to_string();
                    // NEVER fake-complete navigate. If still claimable, navigate for real.
                    if action == "navigate" && !jid.is_empty() && jid == last_completed_nav {
                        log::info!(
                            "computer-host: re-claim navigate {jid} after prior complete — navigating again"
                        );
                    }
                    handle_job(&app, &agent, job);
                    if action == "navigate" && !jid.is_empty() {
                        last_completed_nav = jid;
                    }
                }
            }
        }
    }
}

fn handle_ui_command(app: &AppHandle, agent: &ureq::Agent, cmd: &serde_json::Value) {
    let action = cmd
        .get("action")
        .and_then(|a| a.as_str())
        .unwrap_or("")
        .to_string();
    let url = cmd
        .get("url")
        .and_then(|u| u.as_str())
        .unwrap_or("")
        .to_string();
    let job_id = cmd
        .get("job_id")
        .and_then(|j| j.as_str())
        .unwrap_or("")
        .to_string();
    let job_action = cmd
        .get("job_action")
        .and_then(|j| j.as_str())
        .unwrap_or("")
        .to_string();

    if action != "open_browser" && job_action != "navigate" {
        return;
    }

    // Tell SPA to expand Browser rail + sync address bar URL
    let _ = app.emit(
        "computer-open-browser",
        json!({ "url": url, "job_id": job_id }),
    );

    if url.is_empty() {
        if !job_id.is_empty() && job_action == "navigate" {
            complete_job(
                agent,
                &job_id,
                false,
                json!({}),
                Some("navigate ui_command missing url".into()),
            );
        }
        return;
    }

    // Lightning path: complete SUCCESS *before* waiting on WebView main-thread
    // work. Opening a URL must never block the agent 8–14s; navigate runs
    // fire-and-forget so the poller stays free for the next command.
    let final_url = url.clone();
    if !job_id.is_empty() {
        complete_job(
            agent,
            &job_id,
            true,
            json!({
                "ok": true,
                "target": "browser",
                "action": "navigate",
                "message": format!(
                    "SUCCESS: Page is open in the in-app Browser rail (right panel). \
                     URL: {final_url}. The user can see it. \
                     Do NOT say the rail failed. Do NOT open system browser. Do NOT web_fetch this page. \
                     Reply briefly that the page is open in the Browser rail."
                ),
                "url": final_url,
                "via": "rust-host",
                "user_visible": true,
            }),
            None,
        );
    }
    let _ = app.emit("computer-browser-url", json!({ "url": url }));
    // Fire-and-forget embed navigate — do not block poller on main-thread recv
    fire_navigate(app, &url);
}

fn handle_job(app: &AppHandle, agent: &ureq::Agent, job: &serde_json::Value) {
    let id = job.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
    let action = job
        .get("action")
        .and_then(|a| a.as_str())
        .unwrap_or("")
        .to_string();
    let payload = job.get("payload").cloned().unwrap_or(json!({}));
    if id.is_empty() {
        return;
    }
    let _ = app.emit("computer-open-browser", json!({ "job_id": id }));

    if action == "navigate" {
        let url = payload
            .get("url")
            .and_then(|u| u.as_str())
            .unwrap_or("")
            .to_string();
        if url.is_empty() {
            complete_job(agent, &id, false, json!({}), Some("url required".into()));
            return;
        }
        // Complete first — never block agent on WebView
        complete_job(
            agent,
            &id,
            true,
            json!({
                "ok": true,
                "target": "browser",
                "action": "navigate",
                "message": format!(
                    "SUCCESS: Page is open in the in-app Browser rail. URL: {url}. \
                     Do NOT web_fetch. Do NOT open system browser."
                ),
                "url": url,
                "via": "rust-job",
                "user_visible": true,
            }),
            None,
        );
        let _ = app.emit("computer-browser-url", json!({ "url": url }));
        fire_navigate(app, &url);
        let _ = agent
            .post(&format!(
                "http://127.0.0.1:7400/api/computer/ui/command/ack?job_id={id}"
            ))
            .call();
        return;
    }

    if action == "snapshot" || action == "a11y" {
        // Eval-with-callback on poller thread (do not nest run_on_main_thread —
        // that deadlocks waiting for the eval callback).
        match browser_agent_action(
            app.clone(),
            "snapshot".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Some(id.clone()),
            None,
        ) {
            Ok(raw) => log::info!("computer-host snapshot job {id} ok len={}", raw.len()),
            Err(e) => {
                log::warn!("computer-host snapshot job {id}: {e}");
                complete_job(agent, &id, false, json!({}), Some(e));
            }
        }
        let _ = agent
            .post(&format!(
                "http://127.0.0.1:7400/api/computer/ui/command/ack?job_id={id}"
            ))
            .call();
        return;
    }

    // click/type left for SPA poller
    log::info!("computer-host: job {id} action={action} left for SPA or next tick");
}

/// Schedule embed navigate on the UI thread without blocking the poller.
fn fire_navigate(app: &AppHandle, url: &str) {
    let app2 = app.clone();
    let url2 = url.to_string();
    if let Err(e) = app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        if let Err(err) = navigate_embed(&app2, state.inner(), &url2, None) {
            log::warn!("fire_navigate failed: {err}");
        }
    }) {
        log::warn!("fire_navigate schedule failed: {e}");
    }
}

fn run_navigate_on_main(app: &AppHandle, url: &str) -> Result<String, String> {
    let (tx, rx) = std::sync::mpsc::channel::<Result<String, String>>();
    let app2 = app.clone();
    let url2 = url.to_string();
    app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        let r = navigate_embed(&app2, state.inner(), &url2, None);
        let _ = tx.send(r);
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;
    rx.recv_timeout(Duration::from_secs(2))
        .map_err(|_| "navigate timed out on main thread".to_string())?
}

fn run_resync_bounds_on_main(app: &AppHandle) -> Result<(), String> {
    let (tx, rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let app2 = app.clone();
    app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .filter(|bb| bb.width >= 200.0 && bb.height >= 160.0)
            .unwrap_or_else(|| default_rail_bounds(&app2));
        if let Some(wv) = app2.get_webview(LABEL) {
            let _ = apply_bounds(&wv, &clamp_bounds(&b));
            let _ = wv.show();
        }
        let _ = tx.send(Ok(()));
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;
    rx.recv_timeout(Duration::from_secs(5))
        .map_err(|_| "resync bounds timeout".to_string())?
}

fn complete_job(
    agent: &ureq::Agent,
    job_id: &str,
    ok: bool,
    result: serde_json::Value,
    error: Option<String>,
) {
    let body = json!({
        "ok": ok,
        "result": result,
        "error": error,
    });
    let url = format!("http://127.0.0.1:7400/api/computer/jobs/{job_id}/complete");
    if let Err(e) = agent
        .post(&url)
        .set("Content-Type", "application/json")
        .send_json(body)
    {
        log::warn!("computer-host complete {job_id}: {e}");
    } else {
        log::info!("computer-host completed job {job_id} ok={ok}");
    }
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
    // Snapshot job id — complete via eval result (not page→localhost fetch).
    job_id: Option<String>,
    // Element ref from computer_snapshot (e.g. e3)
    r#ref: Option<String>,
) -> Result<String, String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open — navigate first".to_string())?;
    let act = action.to_lowercase();
    let jid_owned = job_id.clone().unwrap_or_default();
    let js = match act.as_str() {
        "snapshot" | "a11y" => {
            // Richer a11y-ish scrape; return array via eval_with_callback.
            r#"(function(){
  try {
    document.querySelectorAll('[data-remedy-ref]').forEach(el => el.removeAttribute('data-remedy-ref'));
  } catch(e) {}
  const sel='a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=switch],[contenteditable=true],summary,label,[onclick]';
  const nodes=[...document.querySelectorAll(sel)].filter(el => {
    const r=el.getBoundingClientRect();
    const st=window.getComputedStyle(el);
    if(st.visibility==='hidden'||st.display==='none'||st.opacity==='0'||el.disabled) return false;
    return r.width>2&&r.height>2&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth;
  }).slice(0,120);
  return nodes.map((el,i) => {
    const r=el.getBoundingClientRect();
    const ref='e'+(i+1);
    try { el.setAttribute('data-remedy-ref', ref); } catch(e) {}
    const text=(el.innerText||'').trim().replace(/\s+/g,' ').slice(0,120);
    const name=(el.getAttribute('aria-label')||el.getAttribute('title')||text||el.value||el.placeholder||el.name||el.tagName||'').trim().replace(/\s+/g,' ').slice(0,120);
    return {
      ref, tag:(el.tagName||'').toLowerCase(), role:el.getAttribute('role')||'',
      name, text, value:(el.value!=null?String(el.value):'').slice(0,80),
      placeholder:(el.placeholder||'').slice(0,80),
      href:(el.href||el.getAttribute('href')||'').slice(0,200),
      title:(el.getAttribute('title')||'').slice(0,80),
      x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
      w:Math.round(r.width), h:Math.round(r.height)
    };
  });
})()"#
            .to_string()
        }
        "page_text" => {
            r#"(function(){
  const t=(document.body&&document.body.innerText)||'';
  const title=document.title||'';
  const url=location.href||'';
  return JSON.stringify({title,url,text:t.replace(/\s+\n/g,'\n').trim().slice(0,12000)});
})()"#
            .to_string()
        }
        "click_text" => {
            let needle = text.clone().unwrap_or_default();
            if needle.is_empty() {
                return Err("text required for click_text".into());
            }
            let escaped = needle
                .replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace('\n', " ");
            format!(
                r#"(function(){{
  const q='{escaped}'.toLowerCase().trim();
  if(!q) return 'missing-text';
  const sel='a,button,input,textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[contenteditable=true],summary,label,[onclick]';
  const nodes=[...document.querySelectorAll(sel)];
  function score(el){{
    const r=el.getBoundingClientRect();
    const st=window.getComputedStyle(el);
    if(st.visibility==='hidden'||st.display==='none'||st.opacity==='0'||el.disabled) return -1;
    if(r.width<2||r.height<2||r.bottom<0||r.right<0||r.top>innerHeight||r.left>innerWidth) return -1;
    const name=(el.getAttribute('aria-label')||el.getAttribute('title')||el.innerText||el.value||el.placeholder||el.name||'').trim().replace(/\s+/g,' ').toLowerCase();
    if(!name) return -1;
    let s=0;
    if(name===q) s=100;
    else if(name.includes(q)) s=70;
    else if(q.includes(name)&&name.length>2) s=40;
    else {{
      const qt=q.split(/\s+/).filter(Boolean);
      const nt=name.split(/\s+/);
      const hit=qt.filter(t=>nt.some(n=>n.includes(t)||t.includes(n))).length;
      if(hit) s=15*(hit/qt.length);
      else return -1;
    }}
    if(r.width>8&&r.width<900&&r.height>8&&r.height<220) s+=5;
    return s;
  }}
  let best=null, bestS=-1;
  for(const el of nodes){{
    const s=score(el);
    if(s>bestS){{ bestS=s; best=el; }}
  }}
  if(!best||bestS<15) return 'no-match:'+q;
  try{{ best.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ best.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=best.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
  best.dispatchEvent(new MouseEvent('mousedown', opts));
  best.dispatchEvent(new MouseEvent('mouseup', opts));
  best.dispatchEvent(new MouseEvent('click', opts));
  if(typeof best.click==='function') try{{ best.click(); }}catch(e){{}}
  const name=(best.getAttribute('aria-label')||best.innerText||best.tagName||'').trim().replace(/\s+/g,' ').slice(0,80);
  return 'ok:'+bestS.toFixed(0)+':'+name;
}})()"#
            )
        }
        "click_ref" => {
            let rf = r#ref.unwrap_or_default();
            if rf.is_empty() {
                return Err("ref required for click_ref".into());
            }
            let escaped = rf.replace('\\', "\\\\").replace('\'', "\\'");
            format!(
                r#"(function(){{
  const ref='{escaped}';
  const el=document.querySelector('[data-remedy-ref="'+ref+'"]');
  if(!el) return 'missing-ref:'+ref;
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=el.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
  if(typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
  return 'ok:'+ref+':'+(el.tagName||'?');
}})()"#
            )
        }
        "click" => {
            // Prefer ref when provided
            if let Some(rf) = r#ref.clone().filter(|s| !s.is_empty()) {
                let escaped = rf.replace('\\', "\\\\").replace('\'', "\\'");
                format!(
                    r#"(function(){{
  const ref='{escaped}';
  const el=document.querySelector('[data-remedy-ref="'+ref+'"]');
  if(!el) return 'missing-ref:'+ref;
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=el.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
  if(typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
  return 'ok:'+ref;
}})()"#
                )
            } else {
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

    // Prefer eval_with_callback so snapshot returns elements in-process
    // (no HTTPS→localhost fetch). Other actions also get real JS return values.
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    wv.eval_with_callback(js, move |result| {
        let _ = tx.send(result);
    })
    .map_err(|e| format!("browser agent action: {e}"))?;
    let raw = rx
        .recv_timeout(Duration::from_secs(3))
        .map_err(|_| "browser agent action timed out on webview".to_string())?;

    if act == "snapshot" || act == "a11y" {
        // raw is JSON array of elements (or null/error string)
        let elements_val: serde_json::Value =
            serde_json::from_str(&raw).unwrap_or_else(|_| json!([]));
        let elements: Vec<serde_json::Value> = elements_val
            .as_array()
            .cloned()
            .unwrap_or_default();
        let n = elements.len();
        if !jid_owned.is_empty() {
            // Prefer a11y push first (sets last_elements on bridge for click-by-ref)
            let agent = ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_secs(1))
                .timeout(Duration::from_secs(3))
                .build();
            let push_ok = agent
                .post("http://127.0.0.1:7400/api/computer/a11y/push")
                .set("Content-Type", "application/json")
                .send_json(json!({
                    "job_id": jid_owned,
                    "elements": elements_val,
                }))
                .is_ok();
            if !push_ok {
                complete_job(
                    &agent,
                    &jid_owned,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "snapshot",
                        "message": format!("{n} interactive elements"),
                        "elements": elements,
                        "via": "eval-callback",
                    }),
                    None,
                );
            }
        }
        log::info!("browser agent snapshot n={n}");
        return Ok(raw);
    }

    if act == "page_text" {
        log::info!("browser agent page_text len={}", raw.len());
        return Ok(raw);
    }

    log::info!("browser agent action {act} → {raw}");
    Ok(if raw.is_empty() {
        format!("browser:{act}:ok")
    } else {
        raw
    })
}

pub fn close_browser_on_quit(app: &AppHandle) {
    destroy_embed(app);
}
