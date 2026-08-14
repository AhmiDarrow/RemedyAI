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
/// Brand-agnostic: path/query heuristics, not site-specific hosts.
const SAME_WINDOW_OAUTH_JS: &str = r#"(function(){
  if (window.__remedySameWindowOpen) return;
  window.__remedySameWindowOpen = true;
  var SS_RET = '__remedy_oauth_return';
  var SS_TS = '__remedy_oauth_ts';
  var origOpen = window.open;
  var origClose = window.close;

  /** True when this page looks like an auth / SSO hop (any provider). */
  function looksLikeAuthUrl(href, host, path, search){
    var h = (host || '').toLowerCase();
    var p = (path || '').toLowerCase();
    var s = (search || '').toLowerCase();
    var blob = (href || '').toLowerCase();
    if (/[?&](ux_mode|response_type|client_id|redirect_uri|scope)=/.test(s) || /[?&](ux_mode|response_type|client_id)=/.test(blob)) return true;
    if (/\/(oauth2?|oidc|saml|sso|authorize|signin|sign-in|sign_in|login|log-in|log_in|auth|session|connect)(\/|$|\?)/.test(p)) return true;
    if (/^(login|accounts|account|auth|sso|id|identity|signin)\./.test(h)) return true;
    if (/\.(auth0|okta|onelogin|pingidentity|duosecurity)\./.test(h) || /\.(auth0|okta)\.com$/.test(h)) return true;
    return false;
  }

  function rememberReturn(){
    try {
      var href = String(window.location.href || '');
      if (!href || href.indexOf('about:')===0) return;
      var h = (window.location.hostname || '').toLowerCase();
      var p = (window.location.pathname || '').toLowerCase();
      var s = (window.location.search || '').toLowerCase();
      // Don't overwrite return URL while already on an auth hop
      if (looksLikeAuthUrl(href, h, p, s)) return;
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

  /** Popup OAuth (ux_mode=popup) cannot finish in a single WebView — use redirect UX. */
  function rewritePopupAuthMode(abs){
    try {
      var u = new URL(abs);
      var changed = false;
      if (u.searchParams.get('ux_mode') === 'popup') {
        u.searchParams.set('ux_mode', 'redirect');
        changed = true;
      }
      // display=popup is a common OIDC/OAuth hint — prefer page
      if (u.searchParams.get('display') === 'popup') {
        u.searchParams.set('display', 'page');
        changed = true;
      }
      if (u.searchParams.get('ui_mode') === 'card') {
        u.searchParams.delete('ui_mode');
        changed = true;
      }
      return changed ? u.toString() : abs;
    } catch(e) {}
    return abs;
  }

  function go(u){
    try {
      var abs = new URL(String(u), window.location.href).href;
      abs = rewritePopupAuthMode(abs);
      if (/^https?:/i.test(abs)) {
        rememberReturn();
        window.location.assign(abs);
        return true;
      }
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
      var href = String(window.location.href || '');
      var stuckClose = /you may now close|you can close this|close this window|return to the app|authentication complete|sign-?in complete|login successful|successfully signed in|returned to the app|you can return to/.test(text);
      var stuckPath = /\/oauth\/(success|complete|done|callback)|\/auth\/(success|complete|callback)|\/signin\/.*\/(approval|complete)/.test(path);
      var popupModeStuck = looksLikeAuthUrl(href, host, path, search)
        && (search.indexOf('ux_mode=popup') >= 0 || search.indexOf('display=popup') >= 0 || search.indexOf('ui_mode=card') >= 0);
      if (stuckClose || stuckPath) {
        window.location.assign(ret);
        return;
      }
      // Auth page still open in popup mode after user likely finished
      if (popupModeStuck) {
        var ts0 = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts0 && (Date.now() - ts0) > 8000) {
          try {
            var sp = new URLSearchParams(window.location.search);
            var cont = sp.get('continue') || sp.get('redirect_uri') || sp.get('return') || sp.get('return_to') || sp.get('next');
            if (cont && /^https?:/i.test(cont)) {
              window.location.assign(cont);
              return;
            }
          } catch(e) {}
          window.location.assign(ret);
          return;
        }
      }
      // Generic account-home dead-end after auth (no active challenge in path)
      if (looksLikeAuthUrl(href, host, path, search)
          && /sign out|log out|manage account|your account|security settings/.test(text)
          && !/oauth|authorize|consent|challenge|password|verify|mfa|2fa|otp/.test(path + text.slice(0, 200))) {
        var ts = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts && (Date.now() - ts) > 4000) {
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
      // Only force same-window for likely auth links (any provider)
      try {
        var au = new URL(abs);
        if (!looksLikeAuthUrl(abs, au.hostname, au.pathname, au.search)
            && !/oauth|authorize|signin|sign-in|login|sso|saml|oidc/i.test(abs)) return;
      } catch(e) {
        if (!/oauth|authorize|signin|login|sso/i.test(abs)) return;
      }
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

/// Minimal rail helpers only — no zoom, no aggressive !important chrome overrides.
/// (Those made icons invisible-but-clickable on Gmail and other SPAs.)
const RAIL_LAYOUT_JS: &str = r#"(function(){
  if (window.__remedyRailLayout) return;
  window.__remedyRailLayout = true;
  try {
    try { document.documentElement.style.zoom = ''; } catch(e) {}
    try { if (document.body) document.body.style.zoom = ''; } catch(e) {}
    // Remove prior experimental styles if a long-lived page kept them
    var old = document.getElementById('remedy-rail-layout-css');
    if (old) old.remove();
    var oldShield = document.getElementById('remedy-privacy-shield-css');
    if (oldShield) oldShield.remove();

    var s = document.createElement('style');
    s.id = 'remedy-rail-layout-css';
    (document.documentElement || document.head || document.body).appendChild(s);
    // Scrollbars only — do not touch color/fill/transform/display of app chrome
    s.textContent = [
      'html{scrollbar-gutter:stable both-edges;}',
      '::-webkit-scrollbar{width:12px;height:12px;}',
      '::-webkit-scrollbar-track{background:rgba(127,127,127,0.15);}',
      '::-webkit-scrollbar-thumb{background:rgba(127,127,127,0.55);border-radius:6px;}',
      '::-webkit-scrollbar-thumb:hover{background:rgba(127,127,127,0.75);}',
      'html.remedy-doc-scroll,html.remedy-doc-scroll body{overflow:auto!important;}'
    ].join('');
  } catch(e) {}

  function applyFit(){
    try {
      try { document.documentElement.style.zoom = ''; } catch(e) {}
      var h = window.innerHeight || document.documentElement.clientHeight || 0;
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
  setTimeout(applyFit, 400);
})();"#;

/// Rewrite auth navigations that cannot complete inside a single WebView.
/// Brand-agnostic: query flags + special schemes only.
fn rewrite_oauth_navigation(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let lower = s.to_ascii_lowercase();

    // Popup OAuth (any provider using ux_mode/display=popup) needs redirect/page in-rail.
    if lower.starts_with("http://") || lower.starts_with("https://") {
        if let Ok(mut u) = Url::parse(s) {
            let pairs: Vec<(String, String)> = u
                .query_pairs()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            let needs = pairs.iter().any(|(k, v)| {
                (k == "ux_mode" && v == "popup")
                    || (k == "display" && v == "popup")
                    || (k == "ui_mode" && v == "card")
            });
            if needs {
                let mut ser = url::form_urlencoded::Serializer::new(String::new());
                let mut changed = false;
                for (k, v) in &pairs {
                    if k == "ux_mode" && v == "popup" {
                        ser.append_pair("ux_mode", "redirect");
                        changed = true;
                    } else if k == "display" && v == "popup" {
                        ser.append_pair("display", "page");
                        changed = true;
                    } else if k == "ui_mode" && v == "card" {
                        changed = true; // drop
                    } else {
                        ser.append_pair(k, v);
                    }
                }
                if changed {
                    u.set_query(Some(&ser.finish()));
                    let out = u.to_string();
                    if out != s {
                        log::info!("browser oauth rewrite popup→redirect/page");
                        return Some(out);
                    }
                }
            }
        }
    }

    // Cross-origin storage relay used by some SSO SDKs: storagerelay://https/host?…
    if lower.starts_with("storagerelay://") {
        let rest = s.get("storagerelay://".len()..)?;
        let scheme_host = rest.split('?').next().unwrap_or(rest);
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

/// Path/query heuristics for auth flows (no brand host hardcoding).
fn looks_like_auth_url_str(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    if lower.contains("ux_mode=")
        || lower.contains("response_type=")
        || lower.contains("client_id=")
        || lower.contains("redirect_uri=")
    {
        return true;
    }
    // Path segments common to OAuth/OIDC/SAML/login
    for token in [
        "/oauth",
        "/oauth2",
        "/oidc",
        "/saml",
        "/sso/",
        "/authorize",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/login",
        "/log-in",
        "/log_in",
        "/auth/",
        "/session",
        "/connect/",
    ] {
        if lower.contains(token) {
            return true;
        }
    }
    false
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

/// Chrome mobile — sites serve compact / mobile templates suited to the rail.
const UA_MOBILE: &str = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36";
/// Desktop Edge — full multi-column layouts when user requests desktop site.
const UA_DESKTOP: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0";

fn browser_rail_prefs_path() -> std::path::PathBuf {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").unwrap_or_else(|_| ".".into())
    } else {
        std::env::var("HOME").unwrap_or_else(|_| ".".into())
    };
    std::path::PathBuf::from(home)
        .join(".remedy")
        .join("browser_rail.json")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct BrowserRailPrefs {
    /// When true, embed uses desktop UA; default false = mobile (better in narrow rail).
    pub desktop_site: bool,
}

impl Default for BrowserRailPrefs {
    fn default() -> Self {
        Self {
            desktop_site: false,
        }
    }
}

fn load_rail_prefs() -> BrowserRailPrefs {
    let p = browser_rail_prefs_path();
    if let Ok(raw) = std::fs::read_to_string(&p) {
        if let Ok(prefs) = serde_json::from_str::<BrowserRailPrefs>(&raw) {
            return prefs;
        }
    }
    BrowserRailPrefs::default()
}

fn save_rail_prefs(prefs: &BrowserRailPrefs) {
    let p = browser_rail_prefs_path();
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(raw) = serde_json::to_string_pretty(prefs) {
        let _ = std::fs::write(p, raw);
    }
}

pub struct BrowserState {
    current_url: Mutex<String>,
    last_bounds: Mutex<Option<BrowserBounds>>,
    /// When true, do not show() the embed (Settings/Help/overlays cover the host).
    /// Prevents the native WebView2 HWND from floating above React chrome.
    stack_suppressed: AtomicBool,
    /// Request desktop site (full UA); default mobile for rail formatting.
    desktop_site: AtomicBool,
    /// HTML/video fullscreen active — SPA rail bounds must not shrink the embed.
    page_fullscreen: AtomicBool,
    /// Navigate job waiting for on_page_load (complete after the page is actually open).
    pending_navigate: Mutex<Option<(String, String)>>,
}

impl Default for BrowserState {
    fn default() -> Self {
        let prefs = load_rail_prefs();
        Self {
            current_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
            last_bounds: Mutex::new(None),
            stack_suppressed: AtomicBool::new(false),
            desktop_site: AtomicBool::new(prefs.desktop_site),
            page_fullscreen: AtomicBool::new(false),
            pending_navigate: Mutex::new(None),
        }
    }
}

fn rail_user_agent(desktop: bool) -> &'static str {
    if desktop {
        UA_DESKTOP
    } else {
        UA_MOBILE
    }
}

/// Ensure mobile pages get a proper viewport (some sites omit it and look huge).
const MOBILE_VIEWPORT_JS: &str = r#"(function(){
  try {
    var m = document.querySelector('meta[name="viewport"]');
    if (!m) {
      m = document.createElement('meta');
      m.setAttribute('name', 'viewport');
      (document.head || document.documentElement).appendChild(m);
    }
    m.setAttribute('content', 'width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover');
  } catch(e) {}
})();"#;

/// Desktop mode: wide viewport so sites don't stay stuck in mobile layout after toggle.
const DESKTOP_VIEWPORT_JS: &str = r#"(function(){
  try {
    var m = document.querySelector('meta[name="viewport"]');
    if (!m) {
      m = document.createElement('meta');
      m.setAttribute('name', 'viewport');
      (document.head || document.documentElement).appendChild(m);
    }
    // Wide fixed width — many sites key off this for "desktop" CSS.
    m.setAttribute('content', 'width=1280, initial-scale=1');
  } catch(e) {}
})();"#;

/// Tell the page the WebView (rail host) *is* the screen — no polyfill of
/// requestFullscreen. Sites use screen/inner dimensions; matching them to the
/// rail makes native fullscreen fill this surface only.
const RAIL_AS_SCREEN_JS: &str = r#"(function(){
  if (window.__remedyRailAsScreen) return;
  window.__remedyRailAsScreen = true;
  function wh() {
    var w = window.innerWidth || document.documentElement.clientWidth || 1;
    var h = window.innerHeight || document.documentElement.clientHeight || 1;
    return { w: Math.max(1, Math.round(w)), h: Math.max(1, Math.round(h)) };
  }
  function patch(proto, keys, pick) {
    keys.forEach(function(k) {
      try {
        Object.defineProperty(proto, k, {
          configurable: true,
          enumerable: true,
          get: function() { return pick(wh()); }
        });
      } catch (e) {}
    });
  }
  try {
    patch(Screen.prototype, ['width', 'availWidth'], function(d) { return d.w; });
    patch(Screen.prototype, ['height', 'availHeight'], function(d) { return d.h; });
  } catch (e) {}
  // Minimal CSS: when native :fullscreen fires, fill the WebView (the rail).
  try {
    var s = document.createElement('style');
    s.id = 'remedy-rail-fs';
    s.textContent = [
      ':fullscreen,:-webkit-full-screen{box-sizing:border-box;width:100%!important;height:100%!important;max-width:100vw!important;max-height:100vh!important;background:#000!important;}',
      'video:fullscreen,video:-webkit-full-screen{object-fit:contain;width:100%!important;height:100%!important;}'
    ].join('');
    (document.documentElement || document.head).appendChild(s);
  } catch (e) {}
})();"#;

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
    // Reject userinfo (user:pass@host or empty @host) — credentials must not
    // land in the rail address bar.
    if let Some(after) = candidate.splitn(2, "://").nth(1) {
        let authority = after.split(['/', '?', '#']).next().unwrap_or(after);
        if authority.contains('@') {
            return Err("blocked url userinfo".into());
        }
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| "invalid url: missing host".to_string())?;
    if host.contains(' ') || host.is_empty() {
        return Err("invalid url: bad host".into());
    }
    // Unwrap IPv4-mapped IPv6 before private-IP / metadata checks.
    let host_for_ip = if let Ok(v6) = host.parse::<std::net::Ipv6Addr>() {
        v6.to_ipv4_mapped()
            .map(|v4| v4.to_string())
            .unwrap_or_else(|| host.to_string())
    } else {
        host.to_string()
    };
    // Require a real domain (has a dot) or localhost / IPv4 / mapped IPv4
    let ok_host = host == "localhost"
        || host.parse::<std::net::Ipv4Addr>().is_ok()
        || host_for_ip.parse::<std::net::Ipv4Addr>().is_ok()
        || host.contains('.');
    if !ok_host {
        return Err(format!("invalid url host: {host}"));
    }
    // Mirror Python is_valid_navigate_url: IMDS / public IPs stay out of the rail.
    let host_lc = host.to_ascii_lowercase();
    let ip_lc = host_for_ip.to_ascii_lowercase();
    if host_lc == "metadata.google.internal"
        || host_lc == "metadata.goog"
        || host_lc == "metadata"
        || host_lc == "instance-data"
        || host_lc.ends_with(".internal")
        || host_lc.ends_with(".nip.io")
        || host_lc.ends_with(".sslip.io")
        || host_lc.ends_with(".xip.io")
        || host_lc.starts_with("169.254.")
        || ip_lc.starts_with("169.254.")
    {
        return Err(format!("blocked metadata / link-local host: {host}"));
    }
    if let Ok(ip) = host_for_ip.parse::<std::net::Ipv4Addr>() {
        let o = ip.octets();
        let loopback = o[0] == 127 || ip.is_unspecified();
        let rfc1918 = o[0] == 10
            || (o[0] == 192 && o[1] == 168)
            || (o[0] == 172 && (16..=31).contains(&o[1]));
        if !loopback && !rfc1918 {
            return Err(format!("blocked non-private IP: {host}"));
        }
    }
    Ok(candidate)
}

#[cfg(test)]
mod normalize_url_tests {
    use super::normalize_url;

    #[test]
    fn rejects_userinfo() {
        assert!(normalize_url("https://user:pass@example.com/").is_err());
        assert!(normalize_url("https://@mail.google.com/").is_err());
    }

    #[test]
    fn unwraps_ipv4_mapped_link_local() {
        assert!(normalize_url("http://[::ffff:169.254.169.254]/").is_err());
        assert!(normalize_url("http://169.254.169.254/").is_err());
    }

    #[test]
    fn allows_https_public_host() {
        assert!(normalize_url("https://mail.google.com").is_ok());
    }
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

/// WebView2: page/video fullscreen stays inside the rail host.
/// SPA may hide toolbar/header so the host grows to the full rail panel;
/// we never expand past last_bounds into the whole app window.
///
/// Handler is intentionally leaked after attach — COM callbacks must outlive
/// the with_webview closure or events never fire.
#[cfg(windows)]
fn attach_fullscreen_handler(app: AppHandle, wv: tauri::Webview) {
    let app_cb = app.clone();
    let result = wv.with_webview(move |platform| {
        use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
        use webview2_com::ContainsFullScreenElementChangedEventHandler;

        let controller = platform.controller();
        let core = match unsafe { controller.CoreWebView2() } {
            Ok(c) => c,
            Err(e) => {
                log::warn!("browser fullscreen: CoreWebView2 failed: {e}");
                return;
            }
        };
        let app_inner = app_cb.clone();
        let handler = ContainsFullScreenElementChangedEventHandler::create(Box::new(
            move |sender: Option<ICoreWebView2>, _args| {
                let Some(sender) = sender else {
                    return Ok(());
                };
                let mut is_fs = windows::core::BOOL(0);
                if unsafe { sender.ContainsFullScreenElement(&mut is_fs) }.is_err() {
                    return Ok(());
                }
                let fullscreen = is_fs.as_bool();
                let app3 = app_inner.clone();
                let _ = app3.clone().run_on_main_thread(move || {
                    apply_page_fullscreen(&app3, fullscreen);
                });
                Ok(())
            },
        ));
        let mut token = 0i64;
        if let Err(e) = unsafe { core.add_ContainsFullScreenElementChanged(&handler, &mut token) }
        {
            log::warn!("browser fullscreen handler attach failed: {e}");
        } else {
            // Keep COM handler alive for the lifetime of the process/embed.
            std::mem::forget(handler);
            log::info!("browser fullscreen handler attached (token={token})");
        }
    });
    if let Err(e) = result {
        log::warn!("browser with_webview for fullscreen failed: {e}");
    }
}

#[cfg(not(windows))]
fn attach_fullscreen_handler(_app: AppHandle, _wv: tauri::Webview) {}

fn apply_page_fullscreen(app: &AppHandle, fullscreen: bool) {
    let Some(state) = app.try_state::<BrowserState>() else {
        return;
    };
    state
        .page_fullscreen
        .store(fullscreen, Ordering::SeqCst);
    let Some(wv) = app.get_webview(LABEL) else {
        return;
    };
    // Stay inside the Browser rail: re-apply last host rect (SPA will grow the
    // host to fill the rail panel after chrome hides). Never cover the whole app.
    let rail = state
        .last_bounds
        .lock()
        .ok()
        .and_then(|g| g.clone())
        .unwrap_or_else(|| default_rail_bounds(app));
    let b = clamp_bounds(&rail);
    // Overlay suppress always wins — page fullscreen must not paint over
    // Settings / Help / the image lightbox (native HWND sits above CSS).
    let may_show = embed_may_show(state.inner());
    if let Err(e) = apply_bounds(&wv, &b, may_show) {
        log::warn!("browser rail fullscreen bounds failed: {e}");
    } else {
        log::info!(
            "browser page fullscreen {} — rail embed {}x{} @ ({},{})",
            if fullscreen { "ON" } else { "OFF" },
            b.width,
            b.height,
            b.x,
            b.y
        );
    }
    let _ = app.emit(
        "browser-page-fullscreen",
        json!({ "fullscreen": fullscreen }),
    );
}

fn destroy_embed(app: &AppHandle) {
    // Child webview (embedded)
    if let Some(state) = app.try_state::<BrowserState>() {
        state.page_fullscreen.store(false, Ordering::SeqCst);
    }
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
/// Current rail view mode: mobile (default) or desktop site.
#[tauri::command]
pub fn browser_view_mode(state: State<'_, BrowserState>) -> Result<serde_json::Value, String> {
    let desktop = state.desktop_site.load(Ordering::SeqCst);
    Ok(json!({
        "desktop_site": desktop,
        "mode": if desktop { "desktop" } else { "mobile" },
    }))
}

/// Apply mobile/desktop User-Agent on a live embed (WebView2 Settings2).
#[cfg(windows)]
fn set_embed_user_agent(wv: &tauri::Webview, desktop: bool) -> Result<(), String> {
    let ua = rail_user_agent(desktop).to_string();
    let (tx, rx) = std::sync::mpsc::sync_channel::<Result<(), String>>(1);
    wv.with_webview(move |platform| {
        use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings2;
        use windows::core::{Interface, HSTRING};

        let result = (|| {
            let controller = platform.controller();
            let core = unsafe { controller.CoreWebView2() }.map_err(|e| e.to_string())?;
            let settings = unsafe { core.Settings() }.map_err(|e| e.to_string())?;
            let settings2: ICoreWebView2Settings2 =
                settings.cast().map_err(|e| format!("Settings2 cast: {e}"))?;
            let hs = HSTRING::from(ua.as_str());
            unsafe { settings2.SetUserAgent(&hs) }.map_err(|e| format!("SetUserAgent: {e}"))?;
            Ok(())
        })();
        let _ = tx.send(result);
    })
    .map_err(|e| format!("with_webview: {e}"))?;
    rx.recv().map_err(|e| format!("UA channel: {e}"))?
}

#[cfg(not(windows))]
fn set_embed_user_agent(_wv: &tauri::Webview, _desktop: bool) -> Result<(), String> {
    Err("User-Agent switch requires Windows WebView2".into())
}

/// Toggle desktop vs mobile site for the Browser rail.
///
/// Prefers **in-place** User-Agent change + hard reload (no destroy). Falls back
/// to destroy+recreate if Settings2 is unavailable. SPA should pass current URL
/// + host bounds when available.
#[tauri::command]
pub fn browser_set_desktop_site(
    app: AppHandle,
    state: State<'_, BrowserState>,
    enabled: bool,
    url: Option<String>,
    bounds: Option<BrowserBounds>,
) -> Result<serde_json::Value, String> {
    state.desktop_site.store(enabled, Ordering::SeqCst);
    save_rail_prefs(&BrowserRailPrefs {
        desktop_site: enabled,
    });
    log::info!(
        "browser rail view mode → {}",
        if enabled { "desktop" } else { "mobile" }
    );

    let target = url
        .map(|u| u.trim().to_string())
        .filter(|u| !u.is_empty() && !u.starts_with("about:"))
        .or_else(|| {
            state
                .current_url
                .lock()
                .ok()
                .map(|g| g.clone())
                .filter(|u| !u.is_empty() && !u.starts_with("about:"))
        });

    let mut method = "prefs_only";
    if let Some(ref target_url) = target {
        if let Some(wv) = app.get_webview(LABEL) {
            match set_embed_user_agent(&wv, enabled) {
                Ok(()) => {
                    // Hard reload with new UA (same URL).
                    if let Some(ref b) = bounds {
                        let cb = clamp_bounds(b);
                        if let Ok(mut g) = state.last_bounds.lock() {
                            *g = Some(cb.clone());
                        }
                        let _ = apply_bounds(&wv, &cb, embed_may_show(state.inner()));
                    }
                    match target_url.parse::<Url>() {
                        Ok(parsed) => {
                            if let Err(e) = wv.navigate(parsed) {
                                log::warn!("browser UA toggle navigate failed: {e}");
                            } else {
                                method = "ua_inplace";
                                log::info!("browser UA applied in-place, reloading {target_url}");
                            }
                        }
                        Err(e) => log::warn!("browser UA toggle bad url: {e}"),
                    }
                    // Viewport script after a beat (page load handler also runs).
                    let js = if enabled {
                        DESKTOP_VIEWPORT_JS
                    } else {
                        MOBILE_VIEWPORT_JS
                    };
                    let wv2 = wv.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(400));
                        let _ = wv2.eval(js);
                        std::thread::sleep(std::time::Duration::from_millis(600));
                        let _ = wv2.eval(js);
                    });
                }
                Err(e) => {
                    log::warn!("browser set UA failed ({e}); recreate embed");
                    destroy_embed(&app);
                    match navigate_embed(&app, state.inner(), target_url, bounds.clone()) {
                        Ok(_) => method = "recreate",
                        Err(ne) => {
                            log::warn!("browser recreate after UA fail: {ne}");
                            return Err(ne);
                        }
                    }
                }
            }
        } else {
            // No embed yet — flag is set; next navigate creates with correct UA.
            method = "prefs_next_open";
            if let Some(b) = bounds {
                if let Ok(mut g) = state.last_bounds.lock() {
                    *g = Some(clamp_bounds(&b));
                }
            }
        }
    }

    let _ = app.emit(
        "browser-view-mode",
        json!({
            "desktop_site": enabled,
            "mode": if enabled { "desktop" } else { "mobile" },
        }),
    );
    Ok(json!({
        "desktop_site": enabled,
        "mode": if enabled { "desktop" } else { "mobile" },
        "method": method,
        "recreate": method == "recreate",
    }))
}

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
    // Always remember rail host bounds (SPA grows host while video is fullscreen).
    if let Ok(mut g) = state.last_bounds.lock() {
        *g = Some(b.clone());
    }
    let may_show = embed_may_show(state.inner());
    if let Some(wv) = app.get_webview(LABEL) {
        apply_bounds(&wv, &b, may_show)?;
    }
    Ok(())
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
    let desktop = state.desktop_site.load(Ordering::SeqCst);
    let ua = rail_user_agent(desktop);
    log::info!(
        "browser embed create mode={} ua={}",
        if desktop { "desktop" } else { "mobile" },
        if desktop { "desktop-chrome" } else { "mobile-chrome" }
    );
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(blank))
        .focused(false)
        .user_agent(ua)
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
            complete_pending_navigate_if_any(&app_for_load, &u);
            // Same-window OAuth: window.open → location.assign (no popup surface).
            let _ = wv.eval(SAME_WINDOW_OAUTH_JS);
            // Scrollbars only (no zoom / chrome overrides)
            let _ = wv.eval(RAIL_LAYOUT_JS);
            // Screen/viewport = this WebView (the rail) so native fullscreen fills it.
            let _ = wv.eval(RAIL_AS_SCREEN_JS);
            // Viewport meta for mobile vs desktop rail mode (re-applied every load).
            if let Some(st) = app_for_load.try_state::<BrowserState>() {
                if st.desktop_site.load(Ordering::SeqCst) {
                    let _ = wv.eval(DESKTOP_VIEWPORT_JS);
                } else {
                    let _ = wv.eval(MOBILE_VIEWPORT_JS);
                }
            }
            if let Some(js) = crate::privacy_shield::cosmetic_inject_js(&u) {
                let _ = wv.eval(&js);
            }
            let wv2 = wv.clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(800));
                let _ = wv2.eval(RAIL_LAYOUT_JS);
                let _ = wv2.eval(RAIL_AS_SCREEN_JS);
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

    // Video fullscreen: expand child WebView2 to the app window on request.
    attach_fullscreen_handler(app.clone(), wv.clone());

    if may_show {
        let _ = wv.show();
    } else {
        let _ = wv.hide();
    }
    // Immediate navigate to target (no delayed re-navigate — that wiped SPAs).
    if let Err(e) = wv.navigate(parsed) {
        log::warn!("browser initial navigate failed: {e}");
    }

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

/// Cached Bearer for host/jobs/ui (API requires auth; a11y push stays job_id-only).
/// Refresh every ~30s so DPAPI is not hit on the 25ms poll hot path.
fn host_bearer_cached() -> String {
    use std::sync::Mutex;
    use std::time::Instant;
    static CACHE: Mutex<Option<(Instant, String)>> = Mutex::new(None);
    const TTL: Duration = Duration::from_secs(30);
    if let Ok(guard) = CACHE.lock() {
        if let Some((t, tok)) = guard.as_ref() {
            if t.elapsed() < TTL && !tok.is_empty() {
                return tok.clone();
            }
        }
    }
    let tok = crate::get_local_api_token().unwrap_or_default();
    if tok.is_empty() {
        // Do not cache empty — sidecar may write the token moments later.
        log::debug!("computer-host: no local API bearer yet — host routes will 401");
        return String::new();
    }
    if let Ok(mut guard) = CACHE.lock() {
        *guard = Some((Instant::now(), tok.clone()));
    }
    tok
}

/// Attach Authorization when we have a token (host/jobs/ui require Bearer).
fn auth_req(req: ureq::Request) -> ureq::Request {
    let tok = host_bearer_cached();
    if tok.is_empty() {
        req
    } else {
        req.set("Authorization", &format!("Bearer {tok}"))
    }
}

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
    // Wait briefly for token file (sidecar may write DPAPI envelope just after ping).
    for _ in 0..40 {
        if !host_bearer_cached().is_empty() {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    log::info!("computer-host: API reachable, polling ui/command + jobs (Bearer auth)");
    // Track last *completed* navigate job so renudge take of the same id is a
    // real re-navigate (never fake-complete without opening the URL).
    let mut last_completed_nav = String::new();
    let mut hello_tick: u32 = 0;
    loop {
        // Navigate-only poll — 50ms is enough for open-url without dual-claiming DOM jobs.
        std::thread::sleep(Duration::from_millis(50));
        hello_tick = hello_tick.wrapping_add(1);
        // Hello ~every 2s
        if hello_tick % 80 == 0 {
            let _ = auth_req(
                agent
                    .post("http://127.0.0.1:7400/api/computer/host/hello")
                    .set("Content-Type", "application/json"),
            )
            .send_string(r#"{"client":"desktop-rust"}"#);
        }

        // take=1 clears command atomically — prevents reloading the same wiki forever
        if let Ok(resp) = auth_req(
            agent.get("http://127.0.0.1:7400/api/computer/ui/command?take=1"),
        )
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
        if let Ok(resp) = auth_req(agent.get(
            "http://127.0.0.1:7400/api/computer/jobs/next?only=navigate",
        ))
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

    let final_url = url.clone();
    if !job_id.is_empty() {
        arm_pending_navigate(app, job_id.clone(), final_url.clone());
    }
    let _ = app.emit("computer-browser-url", json!({ "url": url }));
    // Fire-and-forget embed navigate — complete from on_page_load (or 8s timeout).
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
        arm_pending_navigate(app, id.clone(), url.clone());
        let _ = app.emit("computer-browser-url", json!({ "url": url }));
        fire_navigate(app, &url);
        ack_ui_command(agent, &id);
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
        ack_ui_command(agent, &id);
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
        ack_ui_command(agent, &id);
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
        ack_ui_command(agent, &id);
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

fn arm_pending_navigate(app: &AppHandle, job_id: String, url: String) {
    if let Some(st) = app.try_state::<BrowserState>() {
        if let Ok(mut g) = st.pending_navigate.lock() {
            *g = Some((job_id.clone(), url.clone()));
        }
    }
    let app2 = app.clone();
    let jid = job_id;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        if let Some(st) = app2.try_state::<BrowserState>() {
            let leftover = if let Ok(mut g) = st.pending_navigate.lock() {
                if g.as_ref().map(|(id, _)| id == &jid).unwrap_or(false) {
                    g.take()
                } else {
                    None
                }
            } else {
                None
            };
            if let Some((id, dest)) = leftover {
                let agent = ureq::AgentBuilder::new()
                    .timeout_connect(Duration::from_secs(2))
                    .timeout(Duration::from_secs(8))
                    .build();
                complete_job(
                    &agent,
                    &id,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "navigate",
                        "message": format!(
                            "SUCCESS: Navigation issued in the in-app Browser rail. URL: {dest}."
                        ),
                        "url": dest,
                        "via": "rust-host-timeout",
                        "user_visible": true,
                    }),
                    None,
                );
            }
        }
    });
}

fn complete_pending_navigate_if_any(app: &AppHandle, loaded_url: &str) {
    let pending = if let Some(st) = app.try_state::<BrowserState>() {
        st.pending_navigate.lock().ok().and_then(|mut g| g.take())
    } else {
        None
    };
    let Some((id, dest)) = pending else {
        return;
    };
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(2))
        .timeout(Duration::from_secs(8))
        .build();
    complete_job(
        &agent,
        &id,
        true,
        json!({
            "ok": true,
            "target": "browser",
            "action": "navigate",
            "message": format!(
                "SUCCESS: Page is open in the in-app Browser rail. URL: {loaded_url}."
            ),
            "url": loaded_url,
            "requested_url": dest,
            "via": "rust-host",
            "user_visible": true,
        }),
        None,
    );
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
    if let Err(e) = auth_req(
        agent
            .post(&url)
            .set("Content-Type", "application/json"),
    )
    .send_json(body)
    {
        log::warn!("computer-host complete {job_id}: {e}");
    } else {
        log::info!("computer-host completed job {job_id} ok={ok}");
    }
}

fn ack_ui_command(agent: &ureq::Agent, job_id: &str) {
    let url = format!("http://127.0.0.1:7400/api/computer/ui/command/ack?job_id={job_id}");
    let _ = auth_req(agent.post(&url)).call();
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
