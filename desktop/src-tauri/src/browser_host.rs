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

/// Force OAuth / SSO into the same rail WebView (no popup window).
///
/// Handles:
/// - `window.open(url)` → same-tab navigation
/// - `open('about:blank')` then `popup.location = authUrl`
/// - `window.close()` after IdP → return to site that started OAuth
/// - "You can close this window" dead-ends after Google login
/// - `target=_blank` login links
const SAME_WINDOW_OAUTH_JS: &str = r#"(function(){
  if (window.__remedySameWindowOpen) return;
  window.__remedySameWindowOpen = true;
  var SS_RET = '__remedy_oauth_return';
  var SS_TS = '__remedy_oauth_ts';
  var origOpen = window.open;
  var origClose = window.close;

  function rememberReturn(){
    try {
      var href = String(window.location.href || '');
      if (!href || href.indexOf('about:')===0) return;
      // Don't overwrite return with IdP pages
      var h = (window.location.hostname || '').toLowerCase();
      if (/google\.|microsoftonline\.|live\.com|github\.com|apple\.com|okta\.|auth0\.|facebook\.|twitter\.|x\.com/.test(h)
          && /accounts\.|login\.|oauth|signin|authorize/.test(href.toLowerCase()+h)) {
        return;
      }
      sessionStorage.setItem(SS_RET, href);
      sessionStorage.setItem(SS_TS, String(Date.now()));
    } catch(e) {}
  }

  function returnUrl(){
    try {
      var r = sessionStorage.getItem(SS_RET) || '';
      var ts = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
      if (!r || !ts || (Date.now() - ts) > 20*60*1000) return '';
      return r;
    } catch(e) { return ''; }
  }

  /** Google GIS popup mode cannot finish in a single WebView — force redirect UX. */
  function rewriteGooglePopupMode(abs){
    try {
      var u = new URL(abs);
      var h = (u.hostname || '').toLowerCase();
      if (h !== 'accounts.google.com' && h.indexOf('.google.com') < 0) return abs;
      // gsi/select and oauth authorize with ux_mode=popup
      var path = (u.pathname || '').toLowerCase();
      if (path.indexOf('/gsi/') >= 0 || path.indexOf('/o/oauth') >= 0 || path.indexOf('/signin') >= 0
          || path.indexOf('/v3/signin') >= 0 || u.search.indexOf('ux_mode') >= 0) {
        if (u.searchParams.get('ux_mode') === 'popup') {
          u.searchParams.set('ux_mode', 'redirect');
        }
        // card UI is popup-oriented; full page is more reliable in-rail
        if (u.searchParams.get('ui_mode') === 'card') {
          u.searchParams.delete('ui_mode');
        }
        return u.toString();
      }
    } catch(e) {}
    return abs;
  }

  function go(u){
    try {
      var abs = new URL(String(u), window.location.href).href;
      abs = rewriteGooglePopupMode(abs);
      if (/^https?:/i.test(abs)) {
        rememberReturn();
        window.location.assign(abs);
        return true;
      }
      // Non-http (storagerelay / intent) — leave for native rewrite if any
      if (abs.indexOf('about:')===0) {
        return false;
      }
    } catch(e) {}
    try {
      rememberReturn();
      window.location.href = String(u);
      return true;
    } catch(e2) {}
    return false;
  }

  function bounceHomeIfStuck(){
    try {
      var ret = returnUrl();
      if (!ret) return;
      var text = ((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 1200).toLowerCase();
      var path = (window.location.pathname || '').toLowerCase();
      var host = (window.location.hostname || '').toLowerCase();
      var search = (window.location.search || '').toLowerCase();
      var stuckClose = /you may now close|you can close this|close this window|return to the app|authentication complete|sign-?in complete|login successful|successfully signed in|returned to the app/.test(text);
      var stuckPath = /\/oauth\/(success|complete|done|callback)|\/signin\/oauth\/consent\/approval|\/gsi\/(status|issue)/.test(path);
      // GIS popup card left open after account pick (no redirect)
      var gsiPopupStuck = host === 'accounts.google.com' && path.indexOf('/gsi/') >= 0
        && (search.indexOf('ux_mode=popup') >= 0 || search.indexOf('ui_mode=card') >= 0);
      var idpHost = /(^|\.)accounts\.google\.com$|(^|\.)login\.microsoftonline\.com$|(^|\.)login\.live\.com$/.test(host);
      if (stuckClose || stuckPath) {
        window.location.assign(ret);
        return;
      }
      // Still on GSI popup URL after user likely finished — force return to site
      if (gsiPopupStuck) {
        var ts0 = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts0 && (Date.now() - ts0) > 8000) {
          // Prefer continue= if Google put one in the URL
          try {
            var cont = new URLSearchParams(window.location.search).get('continue')
              || new URLSearchParams(window.location.search).get('redirect_uri');
            if (cont && /^https?:/i.test(cont)) {
              window.location.assign(cont);
              return;
            }
          } catch(e) {}
          window.location.assign(ret);
          return;
        }
      }
      // If Google shows account home after OAuth without redirect, bounce home
      if (idpHost && /myaccount\.google|ManageAccount|Sign out/.test(text) && !/oauth|authorize|consent|challenge|gsi/.test(path+text.slice(0,200))) {
        var ts = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts && (Date.now() - ts) > 2500) {
          window.location.assign(ret);
        }
      }
    } catch(e) {}
  }

  function blankStub(){
    var closed = false;
    var stub = {
      get closed(){ return closed; },
      close: function(){
        closed = true;
        var ret = returnUrl();
        if (ret) { try { window.location.assign(ret); } catch(e) {} }
      },
      focus: function(){},
      blur: function(){},
      // Sites postMessage to popup; forward onto current window (same-tab OAuth)
      postMessage: function(msg, origin){
        try {
          var o = (origin && origin !== '*') ? origin : window.location.origin;
          window.postMessage(msg, o === '/' ? window.location.origin : origin || '*');
        } catch(e) {
          try { window.postMessage(msg, '*'); } catch(e2) {}
        }
      },
      document: document,
      window: window,
      self: null,
      frames: window.frames,
      parent: window,
      top: window
    };
    stub.self = stub;
    // opener = current window so GIS / OAuth can postMessage back
    try {
      Object.defineProperty(stub, 'opener', {
        get: function(){ return window; },
        set: function(){},
        configurable: true
      });
    } catch(e) { stub.opener = window; }
    var loc = {
      get href(){ return window.location.href; },
      set href(v){ go(v); },
      assign: function(v){ go(v); },
      replace: function(v){ try { rememberReturn(); window.location.replace(String(v)); } catch(e){ go(v); } },
      reload: function(){ try { window.location.reload(); } catch(e){} },
      toString: function(){ return window.location.href; }
    };
    try {
      Object.defineProperty(stub, 'location', {
        get: function(){ return loc; },
        set: function(v){ go(v); },
        configurable: true
      });
    } catch(e) { stub.location = loc; }
    return stub;
  }

  window.open = function(url, name, features){
    try {
      var u = (url==null || url==='') ? '' : String(url);
      if (!u || u === 'about:blank' || u.indexOf('about:blank')===0) {
        return blankStub();
      }
      go(u);
      // Never return null (sites treat that as "popup blocked" and hang)
      return blankStub();
    } catch(e) {
      try { return origOpen ? origOpen.apply(window, arguments) : blankStub(); } catch(e2) { return blankStub(); }
    }
  };

  // Popup flows call close() when done — take user back to the app site
  window.close = function(){
    var ret = returnUrl();
    if (ret) {
      try { window.location.assign(ret); return; } catch(e) {}
    }
    try { if (origClose) origClose.call(window); } catch(e2) {}
  };

  // target=_blank login buttons
  document.addEventListener('click', function(ev){
    try {
      var a = ev.target && ev.target.closest ? ev.target.closest('a[target="_blank"], area[target="_blank"]') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (!href || href.charAt(0)==='#') return;
      var abs = new URL(href, window.location.href).href;
      if (!/^https?:/i.test(abs)) return;
      // Only force same-window for likely auth links
      if (!/oauth|authorize|login|signin|accounts\.google|microsoftonline|github\.com\/login|auth0|okta/i.test(abs+href)) return;
      ev.preventDefault();
      ev.stopPropagation();
      go(abs);
    } catch(e) {}
  }, true);

  // After load: unstick "close this window" / stranded IdP pages
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(bounceHomeIfStuck, 400);
    setTimeout(bounceHomeIfStuck, 2000);
  } else {
    document.addEventListener('DOMContentLoaded', function(){
      setTimeout(bounceHomeIfStuck, 400);
      setTimeout(bounceHomeIfStuck, 2000);
    });
  }
  window.addEventListener('load', function(){ setTimeout(bounceHomeIfStuck, 600); });
})();"#;

/// Visible scrollbars + fit desktop sites in a narrow Browser rail.
/// Gmail/Docs use internal scroll regions; filter CSS + missing scrollbar gutters
/// made panes look “cut off” with no way to scroll.
const RAIL_LAYOUT_JS: &str = r#"(function(){
  if (window.__remedyRailLayout) return;
  window.__remedyRailLayout = true;
  try {
    var s = document.getElementById('remedy-rail-layout-css');
    if (!s) {
      s = document.createElement('style');
      s.id = 'remedy-rail-layout-css';
      (document.documentElement || document.head || document.body).appendChild(s);
    }
    // Always-visible scrollbar gutters (WebView2 overlay bars are easy to miss)
    s.textContent = [
      'html{scrollbar-gutter:stable both-edges;}',
      '::-webkit-scrollbar{width:12px;height:12px;}',
      '::-webkit-scrollbar-track{background:rgba(127,127,127,0.15);}',
      '::-webkit-scrollbar-thumb{background:rgba(127,127,127,0.55);border-radius:6px;}',
      '::-webkit-scrollbar-thumb:hover{background:rgba(127,127,127,0.75);}',
      /* If the document itself overflows, allow page scroll (many marketing sites) */
      'html.remedy-doc-scroll,html.remedy-doc-scroll body{overflow:auto!important;}'
    ].join('');
  } catch(e) {}

  function applyFit(){
    try {
      var w = window.innerWidth || document.documentElement.clientWidth || 0;
      var h = window.innerHeight || document.documentElement.clientHeight || 0;
      // Mild zoom-out in narrow rail so multi-column apps (Gmail) fit better
      var z = 1;
      if (w > 0 && w < 520) z = 0.82;
      else if (w > 0 && w < 640) z = 0.88;
      else if (w > 0 && w < 780) z = 0.92;
      try { document.documentElement.style.zoom = String(z); } catch(e) {}
      // Document-level scroll when content is taller than the viewport
      var sh = Math.max(
        document.documentElement ? document.documentElement.scrollHeight : 0,
        document.body ? document.body.scrollHeight : 0
      );
      if (sh > h + 48) {
        document.documentElement.classList.add('remedy-doc-scroll');
      }
    } catch(e) {}
  }
  applyFit();
  window.addEventListener('resize', applyFit);
  setTimeout(applyFit, 300);
  setTimeout(applyFit, 1200);
})();"#;

/// Rewrite OAuth navigations that cannot complete inside a single WebView.
/// - `storagerelay://…` / Android `intent:` → real https
/// - Google GIS `ux_mode=popup` → `ux_mode=redirect` (popup handshake needs opener)
fn rewrite_oauth_navigation(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let lower = s.to_ascii_lowercase();

    // Google Identity Services / OAuth: popup mode never finishes without a real popup.
    if lower.starts_with("https://accounts.google.com/")
        || lower.starts_with("http://accounts.google.com/")
    {
        if let Ok(mut u) = Url::parse(s) {
            let mut changed = false;
            let pairs: Vec<(String, String)> = u
                .query_pairs()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            if pairs.iter().any(|(k, v)| k == "ux_mode" && v == "popup") {
                // Rebuild query with ux_mode=redirect
                let mut ser = url::form_urlencoded::Serializer::new(String::new());
                for (k, v) in &pairs {
                    if k == "ux_mode" {
                        ser.append_pair("ux_mode", "redirect");
                        changed = true;
                    } else if k == "ui_mode" && v == "card" {
                        // drop card chrome (popup-oriented)
                        changed = true;
                    } else {
                        ser.append_pair(k, v);
                    }
                }
                if changed {
                    u.set_query(Some(&ser.finish()));
                    let out = u.to_string();
                    if out != s {
                        log::info!("browser oauth rewrite Google popup→redirect");
                        return Some(out);
                    }
                }
            }
        }
    }

    // Google GIS / GSI: storagerelay://https/www.example.com?id=authz_cb
    if lower.starts_with("storagerelay://") {
        let rest = s.get("storagerelay://".len()..)?;
        let scheme_host = rest.split('?').next().unwrap_or(rest);
        // scheme_host = "https/www.example.com" or "https/www.example.com/path"
        let mut parts = scheme_host.splitn(2, '/');
        let scheme = parts.next().unwrap_or("https");
        let hostpath = parts.next().unwrap_or("");
        if hostpath.is_empty() {
            return None;
        }
        let host = hostpath.split('/').next().unwrap_or(hostpath);
        if host.is_empty() {
            return None;
        }
        let scheme = if scheme.eq_ignore_ascii_case("http") {
            "http"
        } else {
            "https"
        };
        let out = format!("{scheme}://{host}/");
        log::info!("browser oauth rewrite storagerelay → {out}");
        return Some(out);
    }

    // Android intent://…;S.browser_fallback_url=https%3A%2F%2F…
    if lower.starts_with("intent:") {
        if let Some(idx) = lower.find("s.browser_fallback_url=") {
            let start = idx + "s.browser_fallback_url=".len();
            let rest = s.get(start..)?;
            let end = rest.find(';').unwrap_or(rest.len());
            let enc = rest.get(..end)?.trim();
            if let Ok(dec) = urlencoding_minimal(enc) {
                if dec.starts_with("http://") || dec.starts_with("https://") {
                    log::info!("browser oauth rewrite intent fallback → {dec}");
                    return Some(dec);
                }
            }
        }
    }

    None
}

fn urlencoding_minimal(enc: &str) -> Result<String, ()> {
    let bytes = enc.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let h = |c: u8| -> Option<u8> {
                    match c {
                        b'0'..=b'9' => Some(c - b'0'),
                        b'a'..=b'f' => Some(c - b'a' + 10),
                        b'A'..=b'F' => Some(c - b'A' + 10),
                        _ => None,
                    }
                };
                if let (Some(a), Some(b)) = (h(bytes[i + 1]), h(bytes[i + 2])) {
                    out.push((a << 4) | b);
                    i += 3;
                    continue;
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8(out).map_err(|_| ())
}

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
    /// When true, do not show() the embed (Settings/Help/overlays cover the host).
    /// Prevents the native WebView2 HWND from floating above React chrome.
    stack_suppressed: AtomicBool,
}

impl Default for BrowserState {
    fn default() -> Self {
        Self {
            current_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
            last_bounds: Mutex::new(None),
            stack_suppressed: AtomicBool::new(false),
        }
    }
}

fn normalize_url(raw: &str) -> Result<String, String> {
    let u = raw.trim();
    if u.is_empty() {
        return Err("empty url".into());
    }
    // Block task-text leaks: "gmail sign in, type user@…" must never become
    // https://gmail sign in… in the address bar.
    if u.contains(' ') || u.contains('\n') || u.contains('\t') {
        return Err("invalid url: spaces (refusing task-text leak)".into());
    }
    if u.contains('@') && !u.starts_with("http://") && !u.starts_with("https://") {
        return Err("invalid url: looks like email, not a page URL".into());
    }
    // Commas in host (before ?) are never valid
    let host_part = u.split('?').next().unwrap_or(u).split('#').next().unwrap_or(u);
    if host_part.contains(',') || host_part.contains(';') || host_part.contains('"') {
        return Err("invalid url: illegal characters in host".into());
    }
    if u.starts_with("javascript:") || u.starts_with("data:") || u.starts_with("file:") {
        return Err("unsupported url scheme".into());
    }
    let candidate = if u.starts_with("http://")
        || u.starts_with("https://")
        || u.starts_with("about:")
    {
        u.to_string()
    } else {
        format!("https://{u}")
    };
    if candidate.starts_with("about:") {
        return Ok(candidate);
    }
    let parsed: Url = candidate
        .parse()
        .map_err(|e: url::ParseError| format!("invalid url: {e}"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return Err(format!("unsupported url scheme: {scheme}"));
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| "invalid url: missing host".to_string())?;
    if host.contains(' ') || host.is_empty() {
        return Err("invalid url: bad host".into());
    }
    // Require a real domain (has a dot) or localhost / IPv4
    let ok_host = host == "localhost"
        || host.parse::<std::net::Ipv4Addr>().is_ok()
        || host.contains('.');
    if !ok_host {
        return Err(format!("invalid url host: {host}"));
    }
    Ok(candidate)
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
    // Never park the embed over the in-app title strip (36px). SPA also clamps
    // against live chrome; this is a hard floor when defaults/stale bounds win.
    const MIN_TOP: f64 = 36.0;
    let mut y = b.y.max(0.0);
    let mut height = b.height.max(80.0);
    if y < MIN_TOP {
        let delta = MIN_TOP - y;
        y = MIN_TOP;
        height = (height - delta).max(80.0);
    }
    BrowserBounds {
        x: b.x.max(0.0),
        y,
        width: b.width.max(80.0),
        height,
    }
}

fn apply_bounds(wv: &tauri::Webview, b: &BrowserBounds, allow_show: bool) -> Result<(), String> {
    wv.set_position(LogicalPosition::new(b.x, b.y))
        .map_err(|e| format!("set_position: {e}"))?;
    wv.set_size(LogicalSize::new(b.width, b.height))
        .map_err(|e| format!("set_size: {e}"))?;
    if allow_show {
        let _ = wv.show();
    } else {
        let _ = wv.hide();
    }
    Ok(())
}

fn embed_may_show(state: &BrowserState) -> bool {
    !state.stack_suppressed.load(Ordering::SeqCst)
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
pub fn browser_show(app: AppHandle, state: State<'_, BrowserState>) -> Result<(), String> {
    if !embed_may_show(state.inner()) {
        return Ok(());
    }
    if let Some(wv) = app.get_webview(LABEL) {
        wv.show().map_err(|e| format!("show: {e}"))?;
    }
    Ok(())
}

/// Suppress (hide) or restore the embed while React overlays cover the host.
/// Does not destroy the webview — navigations may continue while hidden.
#[tauri::command]
pub fn browser_set_stack_suppressed(
    app: AppHandle,
    state: State<'_, BrowserState>,
    suppressed: bool,
) -> Result<(), String> {
    state
        .stack_suppressed
        .store(suppressed, Ordering::SeqCst);
    if suppressed {
        if let Some(wv) = app.get_webview(LABEL) {
            let _ = wv.hide();
        }
        log::debug!("browser embed stack suppressed");
    } else if let Some(wv) = app.get_webview(LABEL) {
        // Re-apply last bounds then show — SPA should also push fresh host rect.
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| default_rail_bounds(&app));
        let _ = apply_bounds(&wv, &clamp_bounds(&b), true);
        let _ = app.emit("browser-stack-restored", json!({ "ok": true }));
        log::debug!("browser embed stack restored");
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
    let may_show = embed_may_show(state.inner());
    if let Some(wv) = app.get_webview(LABEL) {
        apply_bounds(&wv, &b, may_show)?;
    }
    Ok(())
}

fn schedule_reload(wv: tauri::Webview, url: String, delay_ms: u64, allow_show: bool) {
    // Never force re-navigate IdP / OAuth pages — reloads wipe mid-login state
    // (user stuck after password / account picker).
    if crate::privacy_shield::is_identity_provider_url(&url)
        || url.to_ascii_lowercase().contains("accounts.google.com")
        || url.to_ascii_lowercase().contains("ux_mode=")
        || url.to_ascii_lowercase().contains("/gsi/")
        || url.to_ascii_lowercase().contains("oauth")
    {
        log::info!("browser skip delayed reload on auth URL");
        if allow_show {
            let _ = wv.show();
        }
        return;
    }
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(delay_ms));
        if let Ok(u) = url.parse::<Url>() {
            if let Err(e) = wv.navigate(u) {
                log::warn!("browser delayed navigate failed: {e}");
            }
        }
        if allow_show {
            let _ = wv.show();
        } else {
            let _ = wv.hide();
        }
        // Force paint if WebView2 stayed white (known multiwebview glitch).
        if allow_show {
            let _ = wv.eval(
                "try{if(!document.body||document.body.childElementCount===0){location.reload()}}catch(e){}",
            );
        }
    });
}

/// Right-rail-ish bounds from main window size when SPA has not pushed host rect yet.
fn default_rail_bounds(app: &AppHandle) -> BrowserBounds {
    if let Some(ww) = app.get_webview_window("main") {
        if let (Ok(size), Ok(scale)) = (ww.inner_size(), ww.scale_factor()) {
            let w = (size.width as f64 / scale).max(800.0);
            let h = (size.height as f64 / scale).max(500.0);
            // Match SPA rail max (~624 body + icon strip); prefer ~40% when smaller
            let rail_w = (w * 0.40).clamp(400.0, 624.0);
            // title (36) + panel header (~30) + URL toolbar (~40)
            let top = 108.0_f64;
            let bottom = 48.0_f64; // app status bar
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
        y: 108.0,
        width: 480.0,
        height: 640.0,
    }
}

/// Core navigate used by the command and the Rust computer-host poller.
pub fn navigate_embed(
    app: &AppHandle,
    state: &BrowserState,
    url_raw: &str,
    bounds: Option<BrowserBounds>,
) -> Result<String, String> {
    let mut url = normalize_url(url_raw)?;
    // Rewrite Google popup-mode GIS → redirect before navigating (same-window).
    if let Some(fixed) = rewrite_oauth_navigation(&url) {
        url = fixed;
    }
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

    let may_show = embed_may_show(state);

    // Already embedded — navigate + re-bounds; recreate if navigate fails (stale child).
    if let Some(wv) = app.get_webview(LABEL) {
        if let Err(e) = apply_bounds(&wv, &b, may_show) {
            log::warn!("browser bounds on existing embed failed: {e}");
        }
        match wv.navigate(parsed.clone()) {
            Ok(()) => {
                if may_show {
                    let _ = wv.show();
                } else {
                    let _ = wv.hide();
                }
                schedule_reload(wv.clone(), url.clone(), 200, may_show);
                log::info!("browser embed navigate {url} show={may_show}");
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
    // Keep SPA address bar in sync when the user clicks links inside the page.
    let app_for_load = app.clone();
    let app_for_nav = app.clone();
    // Dark chrome — pure white reads as a distracting border around the page
    // Do not focus on create — steals focus and worsens z-order over React chrome.
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(blank))
        .focused(false)
        .background_color(Color(18, 18, 22, 255))
        // Privacy Shield + OAuth special-scheme rewrite (storagerelay / intent)
        .on_navigation(move |url| {
            let s = url.as_str();
            if let Some(fixed) = rewrite_oauth_navigation(s) {
                let app2 = app_for_nav.clone();
                let fixed2 = fixed.clone();
                let _ = app2.clone().run_on_main_thread(move || {
                    if let Some(wv) = app2.get_webview(LABEL) {
                        if let Ok(u) = fixed2.parse::<Url>() {
                            if let Err(e) = wv.navigate(u) {
                                log::warn!("browser oauth rewrite navigate failed: {e}");
                            }
                        }
                    }
                });
                return false;
            }
            if crate::privacy_shield::should_block_navigation(s) {
                return false;
            }
            true
        })
        .on_page_load(move |wv, payload| {
            let u = payload.url().as_str().to_string();
            if u.is_empty() || u.starts_with("about:") {
                return;
            }
            if let Some(st) = app_for_load.try_state::<BrowserState>() {
                if let Ok(mut g) = st.current_url.lock() {
                    *g = u.clone();
                }
            }
            let _ = app_for_load.emit("browser-url-changed", json!({ "url": u }));
            // Same-window OAuth: window.open → location.assign (no popup surface).
            let _ = wv.eval(SAME_WINDOW_OAUTH_JS);
            // Scrollbars + mild zoom so narrow-rail desktop sites stay usable
            let _ = wv.eval(RAIL_LAYOUT_JS);
            // Phase 1 cosmetic: hide ad/tracker elements via EasyList CSS
            // (skipped on Gmail/Docs/etc. — those lists break scroll panes)
            if let Some(js) = crate::privacy_shield::cosmetic_inject_js(&u) {
                let _ = wv.eval(&js);
            }
            // Re-fit after SPA chrome mounts
            let wv2 = wv.clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(800));
                let _ = wv2.eval(RAIL_LAYOUT_JS);
            });
        });

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

    if may_show {
        let _ = wv.show();
    } else {
        let _ = wv.hide();
    }
    // Immediate navigate to target
    if let Err(e) = wv.navigate(parsed) {
        log::warn!("browser initial navigate failed: {e}");
    }
    // Delayed re-navigate + paint (known multiwebview white-screen workaround)
    schedule_reload(wv.clone(), url.clone(), 120, may_show);
    schedule_reload(wv.clone(), url.clone(), 400, may_show);

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

        // Claim navigate leftovers + DOM jobs (SPA may also claim; only one wins).
        // page_text/ready must be claimable by Rust — SPA alone is not enough when
        // the React host is busy or mid-bootstrap after navigate.
        if let Ok(resp) = agent
            .get(
                "http://127.0.0.1:7400/api/computer/jobs/next?only=navigate,snapshot,a11y,page_text,ready,click",
            )
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
                    // DOM jobs can take several seconds (page load + eval).
                    // Run them off the poller thread so navigate stays snappy.
                    if matches!(
                        action,
                        "snapshot" | "a11y" | "page_text" | "ready" | "click"
                    ) {
                        let app2 = app.clone();
                        let agent2 = agent.clone();
                        let job2 = job.clone();
                        let _ = std::thread::Builder::new()
                            .name("computer-dom".into())
                            .spawn(move || handle_job(&app2, &agent2, &job2));
                    } else {
                        handle_job(&app, &agent, job);
                        if action == "navigate" && !jid.is_empty() {
                            last_completed_nav = jid;
                        }
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
        // Optimistic navigate completes before paint; wait for ready then
        // retry snapshot — WebView2 eval hangs mid-navigation until load ends.
        let _ = wait_page_ready(app, 4);
        match run_snapshot_with_retry(app, &id, 2) {
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

    if action == "page_text" {
        let _ = wait_page_ready(app, 4);
        match run_page_text_with_retry(app, 2) {
            Ok(raw) => {
                let parsed = parse_page_text_raw(&raw);
                let text = parsed
                    .get("text")
                    .and_then(|t| t.as_str())
                    .unwrap_or("")
                    .to_string();
                complete_job(
                    agent,
                    &id,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "page_text",
                        "message": format!("Page text {} chars", text.len()),
                        "via": "rust-host",
                        "title": parsed.get("title").cloned().unwrap_or(json!("")),
                        "url": parsed.get("url").cloned().unwrap_or(json!("")),
                        "text": text,
                    }),
                    None,
                );
            }
            Err(e) => {
                log::warn!("computer-host page_text job {id}: {e}");
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

    if action == "ready" {
        match browser_agent_action(
            app.clone(),
            "ready".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => {
                let parsed: serde_json::Value =
                    serde_json::from_str(&raw).unwrap_or_else(|_| json!({ "raw": raw }));
                complete_job(
                    agent,
                    &id,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "ready",
                        "message": "page ready probe",
                        "via": "rust-host",
                        "ready": parsed,
                    }),
                    None,
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        return;
    }

    if action == "click" {
        let text = payload
            .get("text")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let r#ref = payload
            .get("ref")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let x = payload.get("x").and_then(|v| v.as_f64());
        let y = payload.get("y").and_then(|v| v.as_f64());
        let button = payload
            .get("button")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let click_text = payload
            .get("click_text")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
            || (text.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
                && r#ref.as_ref().map(|s| s.is_empty()).unwrap_or(true)
                && x.is_none());
        let act = if click_text {
            "click_text".to_string()
        } else if r#ref.as_ref().map(|s| !s.is_empty()).unwrap_or(false) {
            "click_ref".to_string()
        } else {
            "click".to_string()
        };
        match browser_agent_action(
            app.clone(),
            act,
            x,
            y,
            None,
            None,
            text,
            None,
            button,
            None,
            None,
            r#ref.clone(),
        ) {
            Ok(raw) => {
                let ok = !raw.starts_with("no-match")
                    && !raw.starts_with("missing-ref")
                    && !raw.starts_with("missing-text");
                complete_job(
                    agent,
                    &id,
                    ok,
                    json!({
                        "ok": ok,
                        "target": "browser",
                        "action": "click",
                        "message": if ok {
                            format!("Clicked ({raw})")
                        } else {
                            format!("click failed: {raw}")
                        },
                        "detail": raw,
                        "ref": r#ref,
                        "via": "rust-host",
                    }),
                    if ok { None } else { Some(format!("click failed")) },
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        let _ = agent
            .post(&format!(
                "http://127.0.0.1:7400/api/computer/ui/command/ack?job_id={id}"
            ))
            .call();
        return;
    }

    // type/key/scroll left for SPA poller
    log::info!("computer-host: job {id} action={action} left for SPA or next tick");
}

/// Poll document.readyState briefly so snapshot/page_text do not eval mid-nav.
fn wait_page_ready(app: &AppHandle, attempts: u32) -> bool {
    for i in 0..attempts {
        match browser_agent_action(
            app.clone(),
            "ready".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => {
                if raw.contains("\"ok\":true")
                    || raw.contains("\"complete\"")
                    || raw.contains("\"interactive\"")
                {
                    return true;
                }
            }
            Err(_) => {
                // WebView may not exist yet right after open_browser.
                if i == 0 {
                    std::thread::sleep(Duration::from_millis(250));
                }
            }
        }
        std::thread::sleep(Duration::from_millis(200 + i as u64 * 100));
    }
    false
}

fn run_snapshot_with_retry(app: &AppHandle, job_id: &str, attempts: u32) -> Result<String, String> {
    let mut last_err = "snapshot failed".to_string();
    for i in 0..attempts {
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
            Some(job_id.to_string()),
            None,
        ) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last_err = e;
                log::warn!(
                    "computer-host snapshot attempt {}/{}: {}",
                    i + 1,
                    attempts,
                    last_err
                );
                std::thread::sleep(Duration::from_millis(350 + i as u64 * 200));
            }
        }
    }
    Err(last_err)
}

fn run_page_text_with_retry(app: &AppHandle, attempts: u32) -> Result<String, String> {
    let mut last_err = "page_text failed".to_string();
    for i in 0..attempts {
        match browser_agent_action(
            app.clone(),
            "page_text".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last_err = e;
                std::thread::sleep(Duration::from_millis(300 + i as u64 * 150));
            }
        }
    }
    Err(last_err)
}

/// WebView2 may return either a JSON object string or a double-encoded JSON
/// string (because page_text JS uses JSON.stringify and the host serializes again).
fn parse_page_text_raw(raw: &str) -> serde_json::Value {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return json!({ "text": "" });
    }
    let mut val: serde_json::Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => return json!({ "text": trimmed }),
    };
    // Double-encoded: "\"{...}\"" or "\"plain text\""
    if let Some(s) = val.as_str() {
        if let Ok(inner) = serde_json::from_str::<serde_json::Value>(s) {
            val = inner;
        } else {
            return json!({ "text": s });
        }
    }
    if val.is_object() {
        return val;
    }
    if let Some(s) = val.as_str() {
        return json!({ "text": s });
    }
    json!({ "text": trimmed })
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
    // Keep signature for potential future use; prefer browser_set_bounds from SPA.
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
            let may = embed_may_show(state.inner());
            let _ = apply_bounds(&wv, &clamp_bounds(&b), may);
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

/// Live URL of the embed. Prefers WebView2's real location (link clicks / history);
/// falls back to last navigated URL in state.
#[tauri::command]
pub fn browser_current_url(
    app: AppHandle,
    state: State<'_, BrowserState>,
) -> Result<String, String> {
    if let Some(wv) = app.get_webview(LABEL) {
        if let Ok(u) = wv.url() {
            let s = u.as_str().to_string();
            if !s.is_empty() && !s.starts_with("about:") {
                if let Ok(mut g) = state.current_url.lock() {
                    *g = s.clone();
                }
                return Ok(s);
            }
        }
        // Fallback: ask the page (some navigations lag wv.url())
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        if wv
            .eval_with_callback(
                "try{String(location.href||'')}catch(e){''}",
                move |result| {
                    let _ = tx.send(result);
                },
            )
            .is_ok()
        {
            if let Ok(raw) = rx.recv_timeout(Duration::from_millis(400)) {
                let s = raw.trim().trim_matches('"').to_string();
                if !s.is_empty() && !s.starts_with("about:") {
                    if let Ok(mut g) = state.current_url.lock() {
                        *g = s.clone();
                    }
                    return Ok(s);
                }
            }
        }
    }
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
    const tag=(el.tagName||'').toLowerCase();
    const itype=String(el.type||'').toLowerCase();
    const auto=String(el.getAttribute('autocomplete')||'').toLowerCase();
    // Never ship password/OTP/secret field values into tool results → LLM.
    const sensitive = tag==='input' && (
      itype==='password' || itype==='hidden' ||
      auto.includes('password') || auto.includes('one-time') || auto==='one-time-code' ||
      auto.includes('cc-') || auto.includes('card') ||
      (el.getAttribute('name')||'').toLowerCase().match(/pass|otp|cvv|cvc|secret|token/)
    );
    const rawVal = (el.value!=null?String(el.value):'');
    const hasVal = rawVal.length>0;
    // Prefer labels/placeholder over raw value for name (avoids password in name).
    const name=(el.getAttribute('aria-label')||el.getAttribute('title')||text||el.placeholder||el.name||(sensitive?'':rawVal)||el.tagName||'').trim().replace(/\s+/g,' ').slice(0,120);
    return {
      ref, tag, role:el.getAttribute('role')||'',
      name, text,
      value: sensitive ? (hasVal ? '[filled]' : '') : rawVal.slice(0,80),
      value_redacted: !!sensitive,
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
  // Cap page text so less personal content reaches the agent/LLM by default.
  return JSON.stringify({title,url,text:t.replace(/\s+\n/g,'\n').trim().slice(0,8000)});
})()"#
            .to_string()
        }
        "ready" => {
            // Page settle signal for agents (document + optional network quiet)
            r#"(function(){
  const ready=document.readyState||'';
  const busy=!!(window.__remedy_nav_busy);
  const title=document.title||'';
  const url=location.href||'';
  return JSON.stringify({ready,busy,title,url,ok: ready==='complete'||ready==='interactive'});
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
  // Scroll passes: find off-screen matches (OSWorld: re-observe after scroll)
  if(!best||bestS<15){{
    for(let pass=0; pass<4 && (!best||bestS<15); pass++){{
      window.scrollBy(0, Math.floor(innerHeight*0.75));
      const nodes2=[...document.querySelectorAll(sel)];
      for(const el of nodes2){{
        const s=score(el);
        if(s>bestS){{ bestS=s; best=el; }}
      }}
    }}
  }}
  if(!best||bestS<15) return 'no-match:'+q;
  try{{ best.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ best.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=best.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  const name=(best.getAttribute('aria-label')||best.innerText||best.placeholder||best.tagName||'').trim().replace(/\s+/g,' ').slice(0,80);
  const tag=(best.tagName||'').toLowerCase();
  const itype=(best.type||'').toLowerCase();
  const out='ok:'+bestS.toFixed(0)+':'+tag+':'+itype+':'+name;
  // Defer click — navigating <a> would tear down the document before return,
  // so eval_with_callback never fires (host timeout).
  setTimeout(function(){{
    const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
    try{{ best.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
    try{{ best.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
    try{{ best.dispatchEvent(new MouseEvent('click', opts)); }}catch(e){{}}
    if(typeof best.click==='function') try{{ best.click(); }}catch(e){{}}
    if(/^(INPUT|TEXTAREA)$/.test(best.tagName)){{
      try{{ best.select&&best.select(); }}catch(e){{}}
    }}
  }}, 0);
  return out;
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
  const out='ok:'+ref+':'+(el.tagName||'?');
  // Defer — navigating links destroy the document before return.
  setTimeout(function(){{
    const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
    try{{ el.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('click', opts)); }}catch(e){{}}
    if(typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
  }}, 0);
  return out;
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
  const out='ok:'+ref;
  setTimeout(function(){{
    const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:0}};
    try{{ el.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('click', opts)); }}catch(e){{}}
    if(typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
  }}, 0);
  return out;
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
  const out='ok:'+(el.tagName||'?');
  setTimeout(function(){{
    const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:{btn_code}}};
    try{{ el.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('{js_btn}', opts)); }}catch(e){{}}
  }}, 0);
  return out;
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
    // Snapshot/page_text need a longer budget: after fire-and-forget navigate the
    // WebView2 may still be loading and eval callbacks stall until the load settles.
    let eval_timeout_s: u64 = match act.as_str() {
        // Keep under executor wait (~12–14s) with at most 2 attempts + ready poll.
        "snapshot" | "a11y" | "page_text" => 5,
        "click" | "click_ref" | "click_text" | "type" | "type_text" | "key" | "scroll"
        | "drag" => 6,
        "ready" => 2,
        _ => 4,
    };
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    wv.eval_with_callback(js, move |result| {
        let _ = tx.send(result);
    })
    .map_err(|e| format!("browser agent action: {e}"))?;
    let raw = rx
        .recv_timeout(Duration::from_secs(eval_timeout_s))
        .map_err(|_| {
            format!("browser agent action timed out on webview ({eval_timeout_s}s / {act})")
        })?;

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
        // Normalize double-encoded JSON.stringify results so callers always
        // get a JSON object string with title/url/text fields.
        let parsed = parse_page_text_raw(&raw);
        let normalized = parsed.to_string();
        log::info!(
            "browser agent page_text len={} text_len={}",
            normalized.len(),
            parsed
                .get("text")
                .and_then(|t| t.as_str())
                .map(|s| s.len())
                .unwrap_or(0)
        );
        return Ok(normalized);
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
