//! Remedy Browser **Privacy Shield** — network + cosmetic filtering via
//! Brave's [`adblock`] crate (MPL-2.0).
//!
//! Filter lists: EasyList + EasyPrivacy (GPL-3 / CC-BY-SA dual license).
//! Not uBlock Origin code — uBO-compatible list syntax only.
//!
//! Phase 1: document navigation block + cosmetic hide inject.
//! Phase 2: on/off prefs + list refresh / auto-update.
//! Phase 3 (later): full WebResourceRequested subresource block on Win32.

use adblock::Engine;
use adblock::lists::{FilterSet, ParseOptions};
use adblock::request::Request;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::State;

const PREFS_NAME: &str = "privacy_shield.json";
const ENGINE_CACHE: &str = "engine.dat";
const EASYLIST_FILE: &str = "easylist.txt";
const EASYPRIVACY_FILE: &str = "easyprivacy.txt";
const LIST_HASHES_FILE: &str = "list_hashes.json";
/// Refresh lists if older than 3 days.
const LIST_MAX_AGE_SECS: u64 = 3 * 24 * 3600;
/// Minimum credible EasyList body size (guards empty/truncated MitM pages).
const LIST_MIN_BYTES: usize = 10_000;
/// Disable EasyList/EasyPrivacy scriptlet injection by default (CSS hide only).
const ALLOW_SCRIPTLET_INJECT: bool = false;

const EASYLIST_URL: &str = "https://easylist.to/easylist/easylist.txt";
const EASYPRIVACY_URL: &str = "https://easylist.to/easylist/easyprivacy.txt";

/// Attribution for Settings / About (EasyList dual licence).
pub const FILTER_ATTRIBUTION: &str =
    "Filter lists: EasyList authors (https://easylist.to/) — GPL-3.0 or CC-BY-SA 3.0. \
     Engine: Brave adblock-rust (MPL-2.0).";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PrivacyShieldPrefs {
    /// Master switch for the in-app Browser Privacy Shield.
    pub enabled: bool,
    /// Unix seconds of last successful list download (0 = never).
    pub lists_updated_at: u64,
}

impl Default for PrivacyShieldPrefs {
    fn default() -> Self {
        Self {
            enabled: true,
            lists_updated_at: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PrivacyShieldStatus {
    pub enabled: bool,
    pub ready: bool,
    pub lists_updated_at: u64,
    pub blocked_navigations: u64,
    pub attribution: String,
    pub message: String,
}

struct ShieldInner {
    engine: Option<Engine>,
    prefs: PrivacyShieldPrefs,
}

pub struct PrivacyShieldState {
    inner: Mutex<ShieldInner>,
    enabled: AtomicBool,
    blocked_nav: AtomicU64,
}

impl Default for PrivacyShieldState {
    fn default() -> Self {
        Self {
            inner: Mutex::new(ShieldInner {
                engine: None,
                prefs: PrivacyShieldPrefs::default(),
            }),
            enabled: AtomicBool::new(true),
            blocked_nav: AtomicU64::new(0),
        }
    }
}

static GLOBAL: OnceLock<Arc<PrivacyShieldState>> = OnceLock::new();

fn global() -> Arc<PrivacyShieldState> {
    GLOBAL
        .get_or_init(|| Arc::new(PrivacyShieldState::default()))
        .clone()
}

/// Call once from app setup so browser_host can use the same state without AppHandle.
pub fn install_global(state: Arc<PrivacyShieldState>) {
    let _ = GLOBAL.set(state);
}

fn shield_dir() -> PathBuf {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").unwrap_or_else(|_| ".".into())
    } else {
        std::env::var("HOME").unwrap_or_else(|_| ".".into())
    };
    PathBuf::from(home).join(".remedy").join("privacy-shield")
}

fn prefs_path() -> PathBuf {
    shield_dir().join(PREFS_NAME)
}

fn load_prefs() -> PrivacyShieldPrefs {
    let p = prefs_path();
    if let Ok(raw) = fs::read_to_string(&p) {
        if let Ok(prefs) = serde_json::from_str::<PrivacyShieldPrefs>(&raw) {
            return prefs;
        }
    }
    PrivacyShieldPrefs::default()
}

fn save_prefs(prefs: &PrivacyShieldPrefs) {
    let dir = shield_dir();
    let _ = fs::create_dir_all(&dir);
    if let Ok(raw) = serde_json::to_string_pretty(prefs) {
        let _ = fs::write(prefs_path(), raw);
    }
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn list_paths() -> (PathBuf, PathBuf) {
    let d = shield_dir();
    (d.join(EASYLIST_FILE), d.join(EASYPRIVACY_FILE))
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

fn list_hashes_path() -> PathBuf {
    shield_dir().join(LIST_HASHES_FILE)
}

fn load_list_hashes() -> HashMap<String, String> {
    let p = list_hashes_path();
    if let Ok(raw) = fs::read_to_string(&p) {
        if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(&raw) {
            return map;
        }
    }
    HashMap::new()
}

fn save_list_hashes(map: &HashMap<String, String>) {
    let dir = shield_dir();
    let _ = fs::create_dir_all(&dir);
    if let Ok(raw) = serde_json::to_string_pretty(map) {
        let _ = fs::write(list_hashes_path(), raw);
    }
}

fn validate_list_body(url: &str, body: &str) -> Result<(), String> {
    if body.len() < LIST_MIN_BYTES {
        return Err(format!(
            "list too short from {url} ({} bytes, need ≥{LIST_MIN_BYTES})",
            body.len()
        ));
    }
    // EasyList files start with a comment header; reject HTML error pages.
    let head = body.chars().take(200).collect::<String>().to_lowercase();
    if head.contains("<html") || head.contains("<!doctype") {
        return Err(format!("list from {url} looks like HTML, not a filter list"));
    }
    if !body.contains("[") && !body.contains("!") && !body.contains("||") {
        return Err(format!("list from {url} missing expected filter syntax"));
    }
    Ok(())
}

fn verify_list_file(path: &Path, expected: Option<&str>) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| format!("read {}: {e}", path.display()))?;
    if bytes.len() < LIST_MIN_BYTES {
        return Err(format!(
            "{} too small ({} bytes)",
            path.display(),
            bytes.len()
        ));
    }
    let hex = sha256_hex(&bytes);
    if let Some(exp) = expected {
        if exp != hex {
            return Err(format!(
                "integrity check failed for {} (hash mismatch — re-download)",
                path.display()
            ));
        }
    }
    Ok(hex)
}

fn download_list(url: &str, dest: &Path) -> Result<String, String> {
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(8))
        .timeout(Duration::from_secs(60))
        .build();
    let resp = agent
        .get(url)
        .set(
            "User-Agent",
            "RemedyAI-PrivacyShield/0.19 (+https://github.com/AhmiDarrow/RemedyAI)",
        )
        .call()
        .map_err(|e| format!("download {url}: {e}"))?;
    let body = resp
        .into_string()
        .map_err(|e| format!("read {url}: {e}"))?;
    validate_list_body(url, &body)?;
    if let Some(parent) = dest.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let bytes = body.into_bytes();
    let hex = sha256_hex(&bytes);
    fs::write(dest, &bytes).map_err(|e| format!("write {}: {e}", dest.display()))?;
    Ok(hex)
}

fn ensure_lists(force: bool, prefs: &mut PrivacyShieldPrefs) -> Result<(), String> {
    let (el, ep) = list_paths();
    let age_ok = prefs.lists_updated_at > 0
        && now_unix().saturating_sub(prefs.lists_updated_at) < LIST_MAX_AGE_SECS;
    let hashes = load_list_hashes();
    let el_name = EASYLIST_FILE.to_string();
    let ep_name = EASYPRIVACY_FILE.to_string();
    let integrity_ok = el.is_file()
        && ep.is_file()
        && verify_list_file(&el, hashes.get(&el_name).map(|s| s.as_str())).is_ok()
        && verify_list_file(&ep, hashes.get(&ep_name).map(|s| s.as_str())).is_ok();
    if integrity_ok && age_ok && !force {
        return Ok(());
    }
    if el.is_file() && ep.is_file() && !integrity_ok {
        log::warn!("privacy-shield: list integrity failed — re-fetching");
    }
    log::info!("privacy-shield: fetching EasyList + EasyPrivacy (force={force})");
    let h1 = download_list(EASYLIST_URL, &el)?;
    let h2 = download_list(EASYPRIVACY_URL, &ep)?;
    let mut new_hashes = HashMap::new();
    new_hashes.insert(el_name, h1);
    new_hashes.insert(ep_name, h2);
    save_list_hashes(&new_hashes);
    prefs.lists_updated_at = now_unix();
    save_prefs(prefs);
    Ok(())
}

fn build_engine_from_lists() -> Result<Engine, String> {
    let (el, ep) = list_paths();
    let hashes = load_list_hashes();
    // Fail closed if on-disk lists were tampered with after download.
    verify_list_file(
        &el,
        hashes.get(EASYLIST_FILE).map(|s| s.as_str()),
    )?;
    verify_list_file(
        &ep,
        hashes.get(EASYPRIVACY_FILE).map(|s| s.as_str()),
    )?;
    let mut set = FilterSet::new(true);
    let opts = ParseOptions::default();
    for path in [&el, &ep] {
        let text = fs::read_to_string(path)
            .map_err(|e| format!("read {}: {e}", path.display()))?;
        set.add_filter_list(text, opts.clone());
    }
    Ok(Engine::new_with_filter_set(set))
}

fn try_load_cached_engine() -> Option<Engine> {
    let path = shield_dir().join(ENGINE_CACHE);
    let bytes = fs::read(&path).ok()?;
    if bytes.len() < 64 {
        return None;
    }
    let mut engine = Engine::default();
    engine.deserialize(&bytes).ok()?;
    Some(engine)
}

fn save_engine_cache(engine: &Engine) {
    let dir = shield_dir();
    let _ = fs::create_dir_all(&dir);
    let bytes = engine.serialize();
    let _ = fs::write(dir.join(ENGINE_CACHE), bytes);
}

/// Background init: prefs + lists + engine. Safe to call multiple times.
pub fn bootstrap() {
    let g = global();
    let prefs = load_prefs();
    g.enabled.store(prefs.enabled, Ordering::SeqCst);

    // Prefer fast cache; refresh lists async if stale.
    let cache = try_load_cached_engine();
    {
        let mut guard = match g.inner.lock() {
            Ok(g) => g,
            Err(_) => return,
        };
        guard.prefs = prefs.clone();
        if let Some(eng) = cache {
            guard.engine = Some(eng);
            log::info!("privacy-shield: loaded engine from cache");
        }
    }

    // Ensure lists + rebuild if needed (network; do not block UI long)
    let g2 = g.clone();
    std::thread::Builder::new()
        .name("privacy-shield-init".into())
        .spawn(move || {
            let mut prefs = load_prefs();
            let need_build = {
                let guard = g2.inner.lock().ok();
                guard.map(|g| g.engine.is_none()).unwrap_or(true)
            };
            let lists_stale = prefs.lists_updated_at == 0
                || now_unix().saturating_sub(prefs.lists_updated_at) >= LIST_MAX_AGE_SECS;
            if let Err(e) = ensure_lists(false, &mut prefs) {
                log::warn!("privacy-shield: list ensure failed: {e}");
                return;
            }
            if need_build || lists_stale {
                match build_engine_from_lists() {
                    Ok(eng) => {
                        save_engine_cache(&eng);
                        if let Ok(mut guard) = g2.inner.lock() {
                            guard.engine = Some(eng);
                            guard.prefs = prefs.clone();
                        }
                        log::info!("privacy-shield: engine ready");
                    }
                    Err(e) => log::warn!("privacy-shield: build failed: {e}"),
                }
            }
            g2.enabled.store(prefs.enabled, Ordering::SeqCst);
        })
        .ok();
}

fn is_enabled() -> bool {
    global().enabled.load(Ordering::SeqCst)
}

/// Hosts used by major OAuth / SSO identity providers.
/// Document navigations to these must never be cancelled by EasyPrivacy-style lists
/// (same-window login in the Browser rail).
const IDP_HOST_EXACT: &[&str] = &[
    "accounts.google.com",
    "accounts.youtube.com",
    "myaccount.google.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "content.googleapis.com",
    "appleid.apple.com",
    "login.microsoftonline.com",
    "login.live.com",
    "login.windows.net",
    "account.live.com",
    "login.yahoo.com",
    "api.twitter.com",
    "api.x.com",
    "auth0.com",
    "discord.com",
    "slack.com",
    "login.openai.com",
    "auth.openai.com",
    "id.proton.me",
    "account.proton.me",
    "ssl.reddit.com",
    "www.reddit.com",
    "reddit.com",
    // Common OAuth callback / CDN hops after Google consent
    "gstatic.com",
    "www.gstatic.com",
    "ssl.gstatic.com",
    "apis.google.com",
];

/// Suffix match (e.g. `foo.auth0.com`, `company.okta.com`).
const IDP_HOST_SUFFIXES: &[&str] = &[
    ".auth0.com",
    ".okta.com",
    ".oktacdn.com",
    ".microsoftonline.com",
    ".microsoft.com",
    ".live.com",
    ".google.com",
    ".googleusercontent.com",
    ".gstatic.com", // Google account assets sometimes hop here mid-flow
];

/// True when this document URL is part of an identity / OAuth login hop.
/// Used to allowlist navigations that filter lists would otherwise block.
pub fn is_identity_provider_url(url: &str) -> bool {
    let Ok(parsed) = url::Url::parse(url) else {
        return false;
    };
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    if host.is_empty() {
        return false;
    }
    let path = parsed.path().to_lowercase();

    if IDP_HOST_EXACT.iter().any(|h| host == *h) {
        return true;
    }
    if IDP_HOST_SUFFIXES
        .iter()
        .any(|suf| host.ends_with(suf) || host == suf.trim_start_matches('.'))
    {
        return true;
    }

    // GitHub / GitLab login & OAuth paths (host is multipurpose)
    if matches!(
        host.as_str(),
        "github.com" | "www.github.com" | "gitlab.com" | "www.gitlab.com"
    ) && (path.starts_with("/login")
        || path.starts_with("/sessions")
        || path.starts_with("/oauth")
        || path.starts_with("/users/sign_in")
        || path.contains("/authorize")
        || path.starts_with("/signin"))
    {
        return true;
    }

    // Facebook / Meta
    if matches!(
        host.as_str(),
        "facebook.com" | "www.facebook.com" | "m.facebook.com" | "web.facebook.com"
    ) && (path.contains("login") || path.contains("oauth") || path.starts_with("/dialog"))
    {
        return true;
    }

    // X / Twitter auth
    if matches!(host.as_str(), "twitter.com" | "www.twitter.com" | "x.com" | "www.x.com")
        && (path.contains("oauth") || path.starts_with("/i/flow/login") || path.starts_with("/login"))
    {
        return true;
    }

    // Discord / Slack oauth paths
    if host == "discord.com" && path.starts_with("/api/oauth2") {
        return true;
    }
    if host == "slack.com" && (path.contains("oauth") || path.starts_with("/signin")) {
        return true;
    }

    // Generic authorize on known SaaS IdP-ish hosts
    if (path.contains("/oauth2/") || path.contains("/oauth/") || path.ends_with("/authorize"))
        && (host.contains("auth") || host.contains("login") || host.contains("sso") || host.contains("idp"))
    {
        return true;
    }

    false
}

/// True if a main-frame document navigation should be cancelled.
pub fn should_block_navigation(url: &str) -> bool {
    if !is_enabled() {
        return false;
    }
    if url.starts_with("about:") || url.starts_with("data:") || url.starts_with("blob:") {
        return false;
    }
    // Never block OAuth / SSO identity hops (same-window Browser rail login).
    if is_identity_provider_url(url) {
        return false;
    }
    let g = global();
    let guard = match g.inner.lock() {
        Ok(g) => g,
        Err(_) => return false,
    };
    let Some(engine) = guard.engine.as_ref() else {
        return false;
    };
    let Ok(req) = Request::new(url, url, "document", "get") else {
        return false;
    };
    let result = engine.check_network_request(&req);
    if result.should_block() {
        g.blocked_nav.fetch_add(1, Ordering::Relaxed);
        log::info!("privacy-shield: blocked navigation {url}");
        true
    } else {
        false
    }
}

/// CSS + optional scriptlet for the page (cosmetic phase).
pub fn cosmetic_inject_js(page_url: &str) -> Option<String> {
    if !is_enabled() {
        return None;
    }
    if page_url.starts_with("about:") {
        return None;
    }
    let g = global();
    let guard = g.inner.lock().ok()?;
    let engine = guard.engine.as_ref()?;
    let resources = engine.url_cosmetic_resources(page_url);
    let mut parts: Vec<String> = Vec::new();

    if !resources.hide_selectors.is_empty() {
        let sel: Vec<String> = resources
            .hide_selectors
            .iter()
            .take(800)
            .cloned()
            .collect();
        // Escape for embedding in a JS string used as CSS text
        let css = sel.join(",\n");
        let css_escaped = css
            .replace('\\', "\\\\")
            .replace('`', "\\`")
            .replace("${", "\\${");
        parts.push(format!(
            r#"(function(){{try{{
  var s=document.getElementById('remedy-privacy-shield-css');
  if(!s){{s=document.createElement('style');s.id='remedy-privacy-shield-css';
    (document.documentElement||document.head||document.body).appendChild(s);}}
  s.textContent=`{css_escaped}{{display:none!important;visibility:hidden!important;height:0!important;max-height:0!important;overflow:hidden!important;}}`;
}}catch(e){{}}}})();"#
        ));
    }

    // Scriptlets from filter lists are powerful (arbitrary page JS). Off by default.
    if ALLOW_SCRIPTLET_INJECT && !resources.injected_script.is_empty() {
        let script = resources
            .injected_script
            .replace("</script>", "<\\/script>");
        parts.push(format!(
            r#"(function(){{try{{{script}}}catch(e){{}}}})();"#
        ));
    }

    if parts.is_empty() {
        None
    } else {
        Some(parts.join("\n"))
    }
}

fn status_of(state: &PrivacyShieldState) -> PrivacyShieldStatus {
    let enabled = state.enabled.load(Ordering::SeqCst);
    let (ready, lists_updated_at) = state
        .inner
        .lock()
        .map(|g| (g.engine.is_some(), g.prefs.lists_updated_at))
        .unwrap_or((false, 0));
    let blocked = state.blocked_nav.load(Ordering::Relaxed);
    let message = if !enabled {
        "Privacy Shield off — ads/trackers not filtered in Browser.".into()
    } else if !ready {
        "Privacy Shield starting — downloading filter lists…".into()
    } else {
        format!("Privacy Shield on — {blocked} navigations blocked this session.")
    };
    PrivacyShieldStatus {
        enabled,
        ready,
        lists_updated_at,
        blocked_navigations: blocked,
        attribution: FILTER_ATTRIBUTION.into(),
        message,
    }
}

#[tauri::command]
pub fn privacy_shield_status(state: State<'_, Arc<PrivacyShieldState>>) -> PrivacyShieldStatus {
    status_of(state.inner())
}

#[tauri::command]
pub fn privacy_shield_set_enabled(
    state: State<'_, Arc<PrivacyShieldState>>,
    enabled: bool,
) -> Result<PrivacyShieldStatus, String> {
    state.enabled.store(enabled, Ordering::SeqCst);
    if let Ok(mut g) = state.inner.lock() {
        g.prefs.enabled = enabled;
        save_prefs(&g.prefs);
    }
    Ok(status_of(state.inner()))
}

#[tauri::command]
pub fn privacy_shield_refresh_lists(
    state: State<'_, Arc<PrivacyShieldState>>,
) -> Result<PrivacyShieldStatus, String> {
    let mut prefs = load_prefs();
    ensure_lists(true, &mut prefs)?;
    let eng = build_engine_from_lists()?;
    save_engine_cache(&eng);
    if let Ok(mut g) = state.inner.lock() {
        g.engine = Some(eng);
        g.prefs = prefs;
    }
    Ok(status_of(state.inner()))
}


