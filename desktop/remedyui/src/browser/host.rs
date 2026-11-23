//! Computer-use host. The page never sees this.
//!
//! Two loops, matching the Tauri split:
//! - `ui-host` peeks `/computer/ui/command` (it does not take) and says hello
//!   with bounds every 4 seconds. A new command opens the Browser rail once.
//! - `computer-host` is the only `jobs/next` consumer (`take=1&driver=rust`).
//!
//! WebView is `Rc` and not `Send`. Worker threads hold a [`Waker`] and post
//! onto the UI thread, then wait on a channel. They never wait from the UI
//! thread, and a posted closure never calls back into `eval_blocking`.

use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::{Cancel, Waker};
use remedy_ui::webview::WebView;
use serde_json::{Value, json};

use super::actions::{
    AgentRequest, action_script, eval_timeout_secs, finish_action_result, key_down_params, key_up_params,
    mouse_params, named_key, page_ready, parse_okxy, parse_page_text_raw, parse_xy, press_hold_explicit,
    press_hold_locator, request_from_job, rust_action_ok, synthetic_pointer_js, synthetic_press_hold_js,
    ui_command_identity, unquote_eval,
};
use super::layout::{self, WorkspaceLayout};
use super::oauth::{self, bounce_target_for_gsi_transformer, navigate_load_matches, should_remember_oauth_return};
use super::scripts::{
    DESKTOP_VIEWPORT_JS, MOBILE_VIEWPORT_JS, RAIL_AS_SCREEN_JS, RAIL_LAYOUT_JS, SAME_WINDOW_OAUTH_JS,
};
use super::shield;
use super::ua::{self, RailPrefs, rail_user_agent, set_engine_major, ua_override_params};
use super::url::{self, normalize_url, resolve_browser_address_bar, resolve_browser_home};
use crate::studio::Studio;

thread_local! {
    static INSTALLED: RefCell<Option<Model>> = const { RefCell::new(None) };
}

static WAKER: Mutex<Option<Waker>> = Mutex::new(None);
static LOOPS: AtomicBool = AtomicBool::new(false);

struct TokenCache {
    at: Instant,
    value: String,
}

static TOKEN: Mutex<Option<TokenCache>> = Mutex::new(None);

/// How long a status line stays up. Failures hold at least 5 seconds.
pub fn status_hold(msg: &str) -> Duration {
    let lower = msg.to_ascii_lowercase();
    let long = ["fail", "error", "blocked", "too small", "not available"].iter().any(|k| lower.contains(k));
    Duration::from_millis(if long { 5_000 } else { 3_200 })
}

/// Rust poller backoff. Busy: sleep 50 ms and do not long-poll. Then 150, 800, 2000.
pub fn poll_wait(idle_streak: u32) -> (u64, u64) {
    if idle_streak == 0 {
        (50, 0)
    } else if idle_streak < 8 {
        (0, 150)
    } else if idle_streak < 16 {
        (0, 800)
    } else {
        (0, 2000)
    }
}

/// `Browser.getVersion` product (`Chrome/120.0...`) or `browserVersion`, as a major.
pub fn major_from_browser_version(raw: &str) -> Option<String> {
    let v: Value = serde_json::from_str(raw).ok()?;
    let product = v.get("product").and_then(|p| p.as_str()).unwrap_or("");
    let from_product = product.split('/').nth(1).unwrap_or("").trim();
    let src = if !from_product.is_empty() {
        from_product
    } else {
        v.get("browserVersion").and_then(|p| p.as_str()).unwrap_or("")
    };
    let major = src.split(['.', ' ']).next().unwrap_or("").trim();
    if major.is_empty() { None } else { Some(major.to_string()) }
}

struct Inner {
    live: bool,
    layout: remedy_ui::reactive::Signal<WorkspaceLayout>,
    address: remedy_ui::reactive::Signal<String>,
    live_url: remedy_ui::reactive::Signal<String>,
    status: remedy_ui::reactive::Signal<String>,
    busy: remedy_ui::reactive::Signal<bool>,
    loaded: remedy_ui::reactive::Signal<bool>,
    desktop: remedy_ui::reactive::Signal<bool>,
    shield: remedy_ui::reactive::Signal<bool>,
    fullscreen: remedy_ui::reactive::Signal<bool>,
    home: remedy_ui::reactive::Signal<String>,
    bookmarks: remedy_ui::reactive::Signal<Vec<super::bookmarks::Bookmark>>,
    editing: Cell<bool>,
    view: RefCell<Option<WebView>>,
    current_url: RefCell<String>,
    last_committed: RefCell<String>,
    pending: RefCell<Option<(String, String)>>,
    oauth_return: RefCell<Option<String>>,
    wanted: RefCell<Option<String>>,
    /// Logical x, y, width, height of the embed host.
    bounds: RefCell<(f32, f32, f32, f32)>,
    scale: Cell<f32>,
    last_ui_key: RefCell<String>,
    studio: Option<Studio>,
    flash_gen: Cell<u64>,
    auto_started: Cell<bool>,
}

/// Browser rail state. Clone is a refcount; workers never receive one.
#[derive(Clone)]
pub struct Model {
    inner: Rc<Inner>,
}

impl Model {
    fn build(live: bool, studio: Option<Studio>) -> Self {
        let prefs = ua::load_rail_prefs();
        let shield = shield::load_shield();
        let layout = layout::load_workspace_layout();
        let home = resolve_browser_home(None);
        Model {
            inner: Rc::new(Inner {
                live,
                layout: remedy_ui::reactive::Signal::new(layout),
                address: remedy_ui::reactive::Signal::new(String::new()),
                live_url: remedy_ui::reactive::Signal::new(String::new()),
                status: remedy_ui::reactive::Signal::new(String::new()),
                busy: remedy_ui::reactive::Signal::new(false),
                loaded: remedy_ui::reactive::Signal::new(false),
                desktop: remedy_ui::reactive::Signal::new(prefs.desktop_site),
                shield: remedy_ui::reactive::Signal::new(shield.enabled),
                fullscreen: remedy_ui::reactive::Signal::new(false),
                home: remedy_ui::reactive::Signal::new(home),
                bookmarks: remedy_ui::reactive::Signal::new(super::bookmarks::load_bookmarks()),
                editing: Cell::new(false),
                view: RefCell::new(None),
                current_url: RefCell::new(String::new()),
                last_committed: RefCell::new(String::new()),
                pending: RefCell::new(None),
                oauth_return: RefCell::new(None),
                wanted: RefCell::new(None),
                bounds: RefCell::new((0.0, 0.0, 0.0, 0.0)),
                scale: Cell::new(1.0),
                last_ui_key: RefCell::new(String::new()),
                studio,
                flash_gen: Cell::new(0),
                auto_started: Cell::new(false),
            }),
        }
    }

    /// Chrome preview. Not installed, so it cannot start pollers or clobber the live rail.
    pub fn preview() -> Self {
        Self::build(false, None)
    }

    pub fn is_live(&self) -> bool {
        self.inner.live
    }

    pub fn layout(&self) -> WorkspaceLayout {
        self.inner.layout.get()
    }

    pub fn address(&self) -> String {
        self.inner.address.get()
    }

    pub fn live_url(&self) -> String {
        self.inner.live_url.get()
    }

    pub fn status(&self) -> String {
        self.inner.status.get()
    }

    pub fn busy(&self) -> bool {
        self.inner.busy.get()
    }

    pub fn loaded(&self) -> bool {
        self.inner.loaded.get()
    }

    pub fn desktop(&self) -> bool {
        self.inner.desktop.get()
    }

    pub fn shield_on(&self) -> bool {
        self.inner.shield.get()
    }

    pub fn fullscreen(&self) -> bool {
        self.inner.fullscreen.get()
    }

    pub fn home(&self) -> String {
        self.inner.home.get()
    }

    pub fn bookmarks(&self) -> Vec<super::bookmarks::Bookmark> {
        self.inner.bookmarks.get()
    }

    pub fn editing(&self) -> bool {
        self.inner.editing.get()
    }

    pub fn set_editing(&self, on: bool) {
        self.inner.editing.set(on);
    }

    pub fn set_address_text(&self, text: String) {
        self.inner.address.set(text);
    }

    fn view(&self) -> Option<WebView> {
        self.inner.view.borrow().clone()
    }

    /// The element exists. Navigate anything that was waiting, and apply the UA.
    pub fn attach_view(&self, view: WebView) {
        *self.inner.view.borrow_mut() = Some(view.clone());
        if !self.is_live() {
            return;
        }
        self.kick_ua();
        if let Some(url) = self.inner.wanted.borrow_mut().take() {
            self.drive(&view, &url);
            return;
        }
        let cur = self.inner.current_url.borrow().clone();
        if !cur.is_empty() && !cur.starts_with("about:") {
            self.drive(&view, &cur);
        }
    }

    /// First time the rail is on screen: restore the open page, or load home.
    pub fn autostart(&self) {
        if !self.is_live() || self.inner.auto_started.get() {
            return;
        }
        if self.inner.wanted.borrow().is_some() || self.loaded() {
            self.inner.auto_started.set(true);
            return;
        }
        let cur = self.inner.current_url.borrow().clone();
        if !cur.is_empty() && !cur.starts_with("about:") {
            self.navigate_prepared(&cur, None);
            self.flash("Restored previous page");
            return;
        }
        let home = self.home();
        self.navigate_prepared(&home, None);
    }

    pub fn note_bounds(&self, x: f32, y: f32, width: f32, height: f32, scale: f32) {
        *self.inner.bounds.borrow_mut() = (x, y, width, height);
        if scale.is_finite() && scale > 0.0 {
            self.inner.scale.set(scale);
        }
    }

    pub fn open_browser_rail(&self) {
        self.remember_layout(false);
        crate::rails::request_open_browser();
    }

    /// OAuth and provider sign-in: keep the Settings form mounted.
    pub fn open_browser_beside_settings(&self) {
        self.remember_layout(true);
        crate::rails::request_open_browser_beside_settings();
    }

    fn remember_layout(&self, beside_settings: bool) {
        let next = if beside_settings {
            layout::layout_open_browser_beside_settings(&self.layout())
        } else {
            layout::layout_open_browser(&self.layout())
        };
        if next != self.layout() {
            self.inner.layout.set(next);
        }
    }

    pub fn go_address(&self, raw: &str) {
        let resolved = resolve_browser_address_bar(raw, None);
        if resolved.is_empty() {
            self.flash("Enter a URL or a search");
            return;
        }
        self.navigate_url(&resolved, None);
    }

    pub fn navigate_url(&self, raw: &str, job_id: Option<String>) {
        self.navigate(raw, job_id, false);
    }

    /// Same navigation as [`Self::navigate_url`], without replacing an open Settings rail.
    pub fn navigate_keeping_settings(&self, raw: &str) {
        self.navigate(raw, None, true);
    }

    /// Grove's Alongside stage is already showing the page. Do not move the Studio rails.
    pub fn navigate_here(&self, raw: &str) {
        let Some(url) = self.prepare_url(raw) else { return };
        self.navigate_prepared(&url, None);
    }

    fn navigate(&self, raw: &str, job_id: Option<String>, keep_settings: bool) {
        let Some(url) = self.prepare_url(raw) else { return };
        if keep_settings {
            self.open_browser_beside_settings();
        } else {
            self.open_browser_rail();
        }
        self.navigate_prepared(&url, job_id);
    }

    fn prepare_url(&self, raw: &str) -> Option<String> {
        let url = match normalize_url(raw) {
            Ok(u) => u,
            Err(e) => {
                self.flash(&e);
                return None;
            }
        };
        Some(oauth::rewrite_oauth_navigation(&url).unwrap_or(url))
    }

    fn navigate_prepared(&self, url: &str, job_id: Option<String>) {
        self.inner.auto_started.set(true);
        self.inner.editing.set(false);
        self.inner.address.set(url.to_string());
        self.inner.live_url.set(url.to_string());
        *self.inner.current_url.borrow_mut() = url.to_string();
        if let Some(id) = job_id {
            self.arm_pending(id, url.to_string());
        }
        self.inner.loaded.set(true);
        self.inner.busy.set(true);
        if let Some(view) = self.view() {
            self.drive(&view, url);
        } else {
            *self.inner.wanted.borrow_mut() = Some(url.to_string());
        }
    }

    fn drive(&self, view: &WebView, url: &str) {
        view.set_user_agent(rail_user_agent(self.desktop()));
        view.navigate(url);
        self.inner.busy.set(true);
        self.inner.loaded.set(true);
    }

    pub fn go_back(&self) {
        if let Some(view) = self.view() {
            view.back();
            self.flash("Back");
        } else {
            self.flash("Cannot go back");
        }
    }

    pub fn go_forward(&self) {
        if let Some(view) = self.view() {
            view.forward();
            self.flash("Forward");
        } else {
            self.flash("Cannot go forward");
        }
    }

    pub fn reload(&self) {
        if let Some(view) = self.view() {
            view.reload();
            self.flash("Reload");
        }
    }

    pub fn go_home(&self) {
        let home = self.home();
        self.navigate_url(&home, None);
    }

    pub fn close_embed(&self) {
        self.inner.loaded.set(false);
        self.inner.busy.set(false);
        self.inner.auto_started.set(false);
        self.inner.wanted.borrow_mut().take();
        if let Some(view) = self.view() {
            view.navigate("about:blank");
        }
        self.flash("Browser closed");
    }

    pub fn toggle_shield(&self) {
        self.set_shield(!self.shield_on());
    }

    pub fn set_shield(&self, on: bool) {
        if self.shield_on() == on {
            return;
        }
        if self.is_live() {
            let prefs = shield::set_enabled(on);
            self.inner.shield.set(prefs.enabled);
        } else {
            self.inner.shield.set(on);
        }
        self.flash(if on { "Privacy Shield on" } else { "Privacy Shield off" });
    }

    pub fn toggle_desktop(&self) {
        self.set_desktop_site(!self.desktop());
    }

    pub fn set_desktop_site(&self, on: bool) {
        if self.desktop() == on {
            return;
        }
        self.inner.desktop.set(on);
        if self.is_live() {
            ua::save_rail_prefs(&RailPrefs { desktop_site: on });
        }
        self.flash(if on { "Desktop site — reloading…" } else { "Mobile view — reloading…" });
        let url = self.inner.current_url.borrow().clone();
        if let Some(view) = self.view() {
            view.set_user_agent(rail_user_agent(on));
            if !url.is_empty() && !url.starts_with("about:") {
                view.navigate(&url);
                self.inner.busy.set(true);
            }
        }
        self.kick_ua();
    }

    pub fn toggle_bookmark(&self, url: &str) {
        let (next, on) = super::bookmarks::toggle_bookmark(url, None, &self.bookmarks());
        if self.is_live() {
            super::bookmarks::save_bookmarks(&next);
        }
        self.inner.bookmarks.set(next);
        self.flash(if on { "Bookmark added" } else { "Bookmark removed" });
    }

    pub fn bookmarked(&self, url: &str) -> bool {
        super::bookmarks::is_bookmarked(url, &self.bookmarks())
    }

    /// Allow-list for the web view. OAuth popup URLs are rewritten into this same view.
    /// The engine never opens a new window; same-window script plus this rewrite is the path.
    pub fn allow_navigation(&self, url: &str) -> bool {
        if let Some(fixed) = oauth::rewrite_oauth_navigation(url) {
            // Defer the corrected navigation to the next frame so the engine's
            // allow callback is not re-entered.
            if let Ok(waker) = remedy_ui::reactive::waker() {
                let _ = waker.post(move || {
                    let Some(model) = installed() else { return };
                    if let Some(view) = model.view() {
                        if view.url() != fixed {
                            view.navigate(fixed);
                        }
                    } else {
                        *model.inner.wanted.borrow_mut() = Some(fixed);
                    }
                });
            } else {
                *self.inner.wanted.borrow_mut() = Some(fixed);
            }
            return false;
        }
        if shield::should_block_navigation(url, self.shield_on()) {
            self.flash(&format!("blocked {url}"));
            return false;
        }
        let lower = url.to_ascii_lowercase();
        lower.starts_with("http://")
            || lower.starts_with("https://")
            || lower.starts_with("about:")
            || lower.starts_with("data:")
            || lower.starts_with("blob:")
    }

    /// Top-level load finished (or a duplicate signal). Called from the slide's binding.
    pub fn on_progress(&self, url: &str, loading: bool) {
        if loading {
            self.inner.busy.set(true);
            return;
        }
        if url.is_empty() || url.starts_with("about:") {
            return;
        }
        let prev = self.inner.last_committed.borrow().clone();
        *self.inner.current_url.borrow_mut() = url.to_string();
        *self.inner.last_committed.borrow_mut() = url.to_string();
        self.inner.live_url.set(url.to_string());
        if !self.editing() {
            self.inner.address.set(url.to_string());
        }
        self.inner.busy.set(false);
        self.inner.loaded.set(true);
        if should_remember_oauth_return(&prev, url) {
            *self.inner.oauth_return.borrow_mut() = Some(prev.clone());
        }
        let saved = self.inner.oauth_return.borrow().clone();
        if let Some(ret) = bounce_target_for_gsi_transformer(url, saved.as_deref()) {
            eprintln!("computer-host: oauth bounce gsi/transform → {ret}");
            if let Ok(waker) = remedy_ui::reactive::waker() {
                let _ = waker.post(move || {
                    if let Some(model) = installed() {
                        if let Some(view) = model.view() {
                            view.navigate(ret);
                        }
                    }
                });
            }
            return;
        }
        self.settle_pending(url, &prev);
        self.inject();
    }

    fn inject(&self) {
        let Some(view) = self.view() else { return };
        let viewport = if self.desktop() { DESKTOP_VIEWPORT_JS } else { MOBILE_VIEWPORT_JS };
        for script in [SAME_WINDOW_OAUTH_JS, RAIL_LAYOUT_JS, RAIL_AS_SCREEN_JS, viewport] {
            view.eval(script, |_| {});
        }
        if !self.is_live() {
            return;
        }
        thread::spawn(|| {
            thread::sleep(Duration::from_millis(800));
            let _ = on_ui(Duration::from_secs(2), |m| {
                if let Some(view) = m.view() {
                    view.eval(RAIL_LAYOUT_JS, |_| {});
                    view.eval(RAIL_AS_SCREEN_JS, |_| {});
                }
            });
        });
    }

    fn settle_pending(&self, loaded: &str, prev: &str) {
        let pending = {
            let mut slot = self.inner.pending.borrow_mut();
            let hit = slot.as_ref().is_some_and(|(_, dest)| navigate_load_matches(loaded, dest, prev));
            if hit { slot.take() } else { None }
        };
        let Some((id, dest)) = pending else { return };
        let loaded = loaded.to_string();
        thread::spawn(move || {
            complete_job(
                &id,
                true,
                json!({
                    "ok": true,
                    "target": "browser",
                    "action": "navigate",
                    "message": format!("SUCCESS: Page is open in the in-app Browser rail. URL: {loaded}."),
                    "url": loaded,
                    "requested_url": dest,
                    "via": "rust-host",
                    "user_visible": true,
                }),
                None,
            );
        });
    }

    fn arm_pending(&self, job_id: String, dest: String) {
        *self.inner.pending.borrow_mut() = Some((job_id.clone(), dest.clone()));
        thread::spawn(move || {
            thread::sleep(Duration::from_secs(8));
            let leftover = on_ui(Duration::from_secs(2), move |m| {
                let mut slot = m.inner.pending.borrow_mut();
                if slot.as_ref().is_some_and(|(id, _)| id == &job_id) { slot.take() } else { None }
            })
            .ok()
            .flatten();
            if let Some((id, dest)) = leftover {
                complete_job(
                    &id,
                    false,
                    json!({
                        "ok": false,
                        "target": "browser",
                        "action": "navigate",
                        "message": format!("Navigation timed out — the in-app Browser rail did not finish loading {dest}."),
                        "url": dest,
                        "via": "rust-host-timeout",
                        "user_visible": true,
                    }),
                    Some("navigation timed out".into()),
                );
                let _ = on_ui(Duration::from_secs(1), |m| m.flash("navigation timed out"));
            }
        });
    }

    pub fn flash(&self, msg: &str) {
        self.inner.status.set(msg.to_string());
        let tick = self.inner.flash_gen.get().wrapping_add(1);
        self.inner.flash_gen.set(tick);
        if !self.is_live() {
            return;
        }
        let hold = status_hold(msg);
        thread::spawn(move || {
            thread::sleep(hold);
            let _ = on_ui(Duration::from_secs(1), move |m| {
                if m.inner.flash_gen.get() == tick {
                    m.inner.status.set(String::new());
                }
            });
        });
    }

    fn kick_ua(&self) {
        if !self.is_live() {
            return;
        }
        thread::spawn(|| {
            below_normal();
            if let Ok(raw) = devtools_blocking("Browser.getVersion".into(), "{}".into(), Duration::from_secs(4)) {
                if let Some(major) = major_from_browser_version(&raw) {
                    set_engine_major(&major);
                }
            }
            let desktop = on_ui(Duration::from_secs(2), |m| m.desktop()).unwrap_or(true);
            let _ = devtools_blocking(
                "Emulation.setUserAgentOverride".into(),
                ua_override_params(desktop),
                Duration::from_secs(4),
            );
            let _ = on_ui(Duration::from_secs(2), move |m| {
                if let Some(view) = m.view() {
                    view.set_user_agent(rail_user_agent(desktop));
                }
            });
        });
    }

    fn session_id(&self) -> String {
        self.inner.studio.as_ref().and_then(|s| s.active.get()).unwrap_or_default()
    }

    /// Settings → `browser_home_url`. Seeds the address only while it is still the default.
    pub fn set_home_url(&self, raw: &str) {
        let home = resolve_browser_home(Some(raw));
        let previous = self.home();
        self.inner.home.set(home.clone());
        let seed = |value: &str| {
            let trimmed = value.trim();
            trimmed.is_empty()
                || trimmed == super::url::DEFAULT_BROWSER_HOME
                || trimmed == "https://example.com"
                || trimmed == previous
        };
        if seed(&self.address()) {
            self.inner.address.set(home.clone());
        }
        if !self.loaded() && seed(&self.live_url()) {
            self.inner.live_url.set(home);
        }
    }
}

/// Install the live rail and start the host loops. Safe to call again.
pub fn ensure(studio: Studio) -> Model {
    let model = if let Some(existing) = installed() {
        existing
    } else {
        let model = Model::build(true, Some(studio));
        INSTALLED.with(|slot| *slot.borrow_mut() = Some(model.clone()));
        model
    };
    start_if_ready();
    refresh_home_once();
    model
}

/// The installed computer-use host, if [`ensure`] has run.
pub fn live() -> Option<Model> {
    installed()
}

fn installed() -> Option<Model> {
    INSTALLED.with(|slot| slot.borrow().clone())
}

fn start_if_ready() {
    if super::paths::spawn_disabled() || LOOPS.load(Ordering::SeqCst) {
        return;
    }
    if let Ok(waker) = remedy_ui::reactive::waker() {
        if let Ok(mut slot) = WAKER.lock() {
            *slot = Some(waker);
        }
        start_loops();
    }
}

fn refresh_home_once() {
    if super::paths::spawn_disabled() {
        return;
    }
    static ASKED: AtomicBool = AtomicBool::new(false);
    if ASKED.swap(true, Ordering::SeqCst) {
        return;
    }
    thread::spawn(|| {
        below_normal();
        let Ok(resp) = send(Request::get("settings").timeout(Duration::from_secs(4))) else {
            return;
        };
        let Ok(v) = resp.json::<Value>() else { return };
        let raw = v
            .get("browser_home_url")
            .and_then(|u| u.as_str())
            .or_else(|| {
                v.get("settings")
                    .and_then(|s| s.get("browser_home_url"))
                    .and_then(|u| u.as_str())
            })
            .unwrap_or("")
            .to_string();
        let _ = on_ui(Duration::from_secs(2), move |m| m.set_home_url(&raw));
    });
}

fn waker_handle() -> Result<Waker, String> {
    WAKER.lock().unwrap_or_else(|e| e.into_inner()).clone().ok_or_else(|| "ui waker is missing".into())
}

fn on_ui<T: Send + 'static>(timeout: Duration, f: impl FnOnce(&Model) -> T + Send + 'static) -> Result<T, String> {
    let (tx, rx) = mpsc::channel();
    waker_handle()?.post(move || {
        let value = installed().map(|m| f(&m));
        let _ = tx.send(value);
    }).map_err(|e| e.to_string())?;
    match rx.recv_timeout(timeout) {
        Ok(Some(v)) => Ok(v),
        Ok(None) => Err("browser host is not installed".into()),
        Err(_) => Err("ui hop timed out".into()),
    }
}

fn eval_blocking(script: String, timeout: Duration) -> Result<String, String> {
    let (tx, rx) = mpsc::channel();
    waker_handle()?.post(move || match installed().and_then(|m| m.view()) {
        Some(view) => {
            view.eval(script, move |result| {
                let _ = tx.send(result);
            });
        }
        None => {
            let _ = tx.send(Err("browser not open — navigate first".into()));
        }
    }).map_err(|e| e.to_string())?;
    match rx.recv_timeout(timeout) {
        Ok(result) => result,
        Err(_) => Err(format!("browser agent action timed out ({timeout:?})")),
    }
}

fn devtools_blocking(method: String, params: String, timeout: Duration) -> Result<String, String> {
    let (tx, rx) = mpsc::channel();
    let method2 = method.clone();
    waker_handle()?.post(move || match installed().and_then(|m| m.view()) {
        Some(view) => {
            view.devtools(method, params, move |result| {
                let _ = tx.send(result);
            });
        }
        None => {
            let _ = tx.send(Err("browser not open — navigate first".into()));
        }
    }).map_err(|e| e.to_string())?;
    match rx.recv_timeout(timeout) {
        Ok(result) => result,
        Err(_) => Err(format!("devtools {method2} timed out")),
    }
}

fn cdp_mouse(kind: &str, x: f64, y: f64, button: &str, click_count: i32) -> Result<(), String> {
    devtools_blocking(
        "Input.dispatchMouseEvent".into(),
        mouse_params(kind, x, y, button, click_count),
        Duration::from_secs(4),
    )
    .map(|_| ())
}

fn cdp_click(x: f64, y: f64, button: &str) -> Result<(), String> {
    cdp_mouse("mouseMoved", x, y, "none", 0)?;
    cdp_mouse("mousePressed", x, y, button, 1)?;
    cdp_mouse("mouseReleased", x, y, button, 1)?;
    Ok(())
}

fn cdp_hover(x: f64, y: f64) -> Result<(), String> {
    cdp_mouse("mouseMoved", x, y, "none", 0)
}

/// Runs on the poller or a `computer-dom` thread. Never call this from the UI thread.
pub fn agent_action(req: &AgentRequest) -> Result<String, String> {
    let act = req.action.to_ascii_lowercase();
    if act == "press_hold" {
        return press_hold(req);
    }
    if matches!(act.as_str(), "type" | "type_text") {
        if let Some(done) = try_trusted_type(req)? {
            return Ok(done);
        }
    }
    if act == "key" {
        if let Some(done) = try_trusted_key(req) {
            return Ok(done);
        }
    }
    let script = action_script(req)?;
    let timeout = Duration::from_secs(eval_timeout_secs(&act));
    let raw = eval_blocking(script, timeout)?;
    if let Some((x, y, meta)) = parse_okxy(&raw) {
        let hover = act.starts_with("hover");
        let button = req.button.clone().unwrap_or_else(|| "left".into());
        let trusted = if hover { cdp_hover(x, y).is_ok() } else { cdp_click(x, y, &button).is_ok() };
        if !trusted {
            let synth = synthetic_pointer_js(hover, x, y, &button);
            let _ = eval_blocking(synth, Duration::from_secs(4));
        }
        eprintln!(
            "computer-host: {act} → ok:{meta} ({})",
            if trusted { "trusted" } else { "synthetic" }
        );
        return Ok(format!("ok:{meta}"));
    }
    if act == "page_text" {
        return Ok(parse_page_text_raw(&raw).to_string());
    }
    Ok(finish_action_result(&act, &raw))
}

fn try_trusted_type(req: &AgentRequest) -> Result<Option<String>, String> {
    let Some(text) = req.text.clone().filter(|s| !s.is_empty()) else {
        return Ok(None);
    };
    let mut focus = req.clone();
    focus.action = "type_focus".into();
    let script = action_script(&focus)?;
    let raw = match eval_blocking(script, Duration::from_secs(2)) {
        Ok(raw) => raw,
        Err(_) => return Ok(None),
    };
    let flag = unquote_eval(&raw);
    if flag.starts_with("no-match") || flag.starts_with("missing-ref") || flag.starts_with("ambiguous") {
        return Ok(Some(flag));
    }
    if flag == "rm-editable" {
        let params = json!({ "text": text }).to_string();
        if devtools_blocking("Input.insertText".into(), params, Duration::from_secs(4)).is_ok() {
            eprintln!("computer-host: type → ok:trusted-type");
            return Ok(Some("ok:trusted-type".into()));
        }
    }
    Ok(None)
}

fn try_trusted_key(req: &AgentRequest) -> Option<String> {
    let key = req.key.as_deref()?;
    let (canon, vk, text) = named_key(key)?;
    let down = devtools_blocking(
        "Input.dispatchKeyEvent".into(),
        key_down_params(canon, vk, text),
        Duration::from_secs(4),
    );
    let up = devtools_blocking(
        "Input.dispatchKeyEvent".into(),
        key_up_params(canon, vk),
        Duration::from_secs(4),
    );
    if down.is_ok() && up.is_ok() {
        eprintln!("computer-host: key → ok:trusted-key");
        Some("ok:trusted-key".into())
    } else {
        None
    }
}

fn press_hold(req: &AgentRequest) -> Result<String, String> {
    let hold_ms = req.dy.map(|v| v.clamp(300, 12_000) as u64).unwrap_or(2600);
    let (x, y) = resolve_point(req)?;
    let trusted = cdp_mouse("mouseMoved", x, y, "none", 0).is_ok()
        && cdp_mouse("mousePressed", x, y, "left", 1).is_ok();
    if trusted {
        thread::sleep(Duration::from_millis(hold_ms));
        cdp_mouse("mouseReleased", x, y, "left", 1)?;
    } else {
        let js = synthetic_press_hold_js(x, y, hold_ms);
        let _ = eval_blocking(js, Duration::from_secs(4));
    }
    Ok(format!("ok:press_hold:{}:{}:{hold_ms}ms", x.round(), y.round()))
}

fn resolve_point(req: &AgentRequest) -> Result<(f64, f64), String> {
    if let Some(point) = press_hold_explicit(req.x, req.y) {
        return Ok(point);
    }
    let script = press_hold_locator(req.reference.as_deref(), req.text.as_deref())?;
    let raw = eval_blocking(script, Duration::from_secs(8))?;
    let raw = unquote_eval(&raw);
    if raw.starts_with("missing-ref") || raw.starts_with("no-match") {
        return Err(raw);
    }
    parse_xy(&raw).ok_or(raw)
}

fn start_loops() {
    if super::paths::spawn_disabled() {
        return;
    }
    if LOOPS.swap(true, Ordering::SeqCst) {
        return;
    }
    eprintln!("computer-host: starting Rust poller");
    let _ = thread::Builder::new().name("computer-host".into()).spawn(|| {
        below_normal();
        poll_loop();
    });
    let _ = thread::Builder::new().name("ui-host".into()).spawn(|| {
        below_normal();
        ui_loop();
    });
    let _ = thread::Builder::new().name("browser-fullscreen".into()).spawn(|| {
        below_normal();
        fullscreen_loop();
    });
}

fn poll_loop() {
    for _ in 0..60 {
        if send(Request::get("ping").timeout(Duration::from_secs(2))).is_ok() {
            break;
        }
        thread::sleep(Duration::from_millis(500));
    }
    for _ in 0..40 {
        if !bearer().is_empty() {
            break;
        }
        thread::sleep(Duration::from_millis(250));
    }
    eprintln!("computer-host: polling ui/command + jobs");
    let mut last_completed_nav = String::new();
    let mut idle_streak: u32 = 0;
    loop {
        let (sleep_ms, wait_ms) = poll_wait(idle_streak);
        if sleep_ms > 0 {
            thread::sleep(Duration::from_millis(sleep_ms));
        }
        let mut saw_work = false;
        if let Ok(resp) = send(Request::get("computer/ui/command?take=1&driver=rust").timeout(Duration::from_secs(8))) {
            if let Ok(v) = resp.json::<Value>() {
                if let Some(cmd) = v.get("command").filter(|c| c.is_object()) {
                    let jid = cmd.get("job_id").and_then(|j| j.as_str()).unwrap_or("").to_string();
                    saw_work = true;
                    handle_ui_command(cmd);
                    if !jid.is_empty() {
                        last_completed_nav = jid;
                    }
                }
            }
        }
        let path = if wait_ms > 0 {
            format!("computer/jobs/next?wait_ms={wait_ms}&driver=rust")
        } else {
            "computer/jobs/next?driver=rust".into()
        };
        let budget = Duration::from_millis(wait_ms.saturating_add(8_000));
        if let Ok(resp) = send(Request::get(path).timeout(budget)) {
            if let Ok(v) = resp.json::<Value>() {
                if let Some(job) = v.get("job").filter(|j| j.is_object()) {
                    let action = job.get("action").and_then(|a| a.as_str()).unwrap_or("").to_string();
                    let jid = job.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
                    saw_work = true;
                    if action == "navigate" && !jid.is_empty() && jid == last_completed_nav {
                        eprintln!("computer-host: skip jobs/next navigate {jid} — ui/command already drove it");
                    } else if matches!(
                        action.as_str(),
                        "snapshot" | "a11y" | "page_text" | "ready" | "click" | "type" | "key" | "scroll"
                            | "drag" | "press_hold" | "select" | "hover" | "screenshot"
                    ) {
                        let job = job.clone();
                        let _ = thread::Builder::new().name("computer-dom".into()).spawn(move || {
                            below_normal();
                            handle_job(&job);
                        });
                    } else {
                        handle_job(job);
                        if action == "navigate" && !jid.is_empty() {
                            last_completed_nav = jid;
                        }
                    }
                }
            }
        }
        idle_streak = if saw_work { 0 } else { idle_streak.saturating_add(1) };
    }
}

fn handle_ui_command(cmd: &Value) {
    let action = cmd.get("action").and_then(|a| a.as_str()).unwrap_or("");
    let job_action = cmd.get("job_action").and_then(|a| a.as_str()).unwrap_or("");
    if action != "open_browser" && job_action != "navigate" {
        return;
    }
    let url = cmd.get("url").and_then(|u| u.as_str()).unwrap_or("").to_string();
    let job_id = cmd.get("job_id").and_then(|j| j.as_str()).unwrap_or("").to_string();
    let _ = on_ui(Duration::from_secs(2), |m| m.open_browser_rail());
    if url.is_empty() {
        if !job_id.is_empty() && job_action == "navigate" {
            complete_job(&job_id, false, json!({}), Some("navigate ui_command missing url".into()));
        }
        return;
    }
    let _ = on_ui(Duration::from_secs(2), move |m| {
        let id = if job_id.is_empty() { None } else { Some(job_id) };
        m.navigate_url(&url, id);
    });
}

fn handle_job(job: &Value) {
    let id = job.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
    let action = job.get("action").and_then(|a| a.as_str()).unwrap_or("").to_string();
    let payload = job.get("payload").cloned().unwrap_or_else(|| json!({}));
    if id.is_empty() {
        return;
    }
    let _ = on_ui(Duration::from_secs(2), |m| m.open_browser_rail());

    if action == "navigate" {
        let url = payload.get("url").and_then(|u| u.as_str()).unwrap_or("").to_string();
        if url.is_empty() {
            complete_job(&id, false, json!({}), Some("url required".into()));
            return;
        }
        let id2 = id.clone();
        let _ = on_ui(Duration::from_secs(2), move |m| m.navigate_url(&url, Some(id2)));
        ack_ui(&id);
        return;
    }

    if action == "snapshot" || action == "a11y" {
        let _ = wait_page_ready();
        match run_snapshot(&id, 2) {
            Ok(raw) => {
                let elements = parse_elements(&raw);
                let n = elements.as_array().map(|a| a.len()).unwrap_or(0);
                if !push_a11y(&id, &elements) {
                    complete_job(
                        &id,
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
            Err(e) => {
                eprintln!("computer-host: snapshot job {id}: {e}");
                complete_job(&id, false, json!({}), Some(e));
            }
        }
        ack_ui(&id);
        return;
    }

    if action == "page_text" {
        let _ = wait_page_ready();
        match run_page_text(2) {
            Ok(raw) => {
                let parsed = parse_page_text_raw(&raw);
                let text = parsed.get("text").and_then(|t| t.as_str()).unwrap_or("").to_string();
                complete_job(
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
            Err(e) => complete_job(&id, false, json!({}), Some(e)),
        }
        ack_ui(&id);
        return;
    }

    if action == "ready" {
        match agent_action(&AgentRequest::new("ready")) {
            Ok(raw) => {
                let parsed: Value = serde_json::from_str(&raw).unwrap_or_else(|_| json!({ "raw": raw }));
                complete_job(
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
            Err(e) => complete_job(&id, false, json!({}), Some(e)),
        }
        return;
    }

    if action == "screenshot" {
        thread::sleep(Duration::from_millis(200));
        let snap = on_ui(Duration::from_secs(2), |m| {
            let (x, y, w, h) = *m.inner.bounds.borrow();
            (m.inner.scale.get(), x, y, w, h, m.inner.current_url.borrow().clone())
        });
        let (scale, x, y, w, h, page_url) = snap.unwrap_or((1.0, 0.0, 0.0, 0.0, 0.0, String::new()));
        let mut body = json!({ "label": "browser_rail", "scale": scale });
        if w > 40.0 && h > 40.0 {
            body["x"] = json!(x.round() as i64);
            body["y"] = json!(y.round() as i64);
            body["width"] = json!(w.round() as i64);
            body["height"] = json!(h.round() as i64);
        }
        match send(Request::post("computer/capture").json(&body).timeout(Duration::from_secs(8))) {
            Ok(resp) => {
                let v: Value = resp.json().unwrap_or_else(|_| json!({}));
                let api_ok = v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false);
                if !api_ok {
                    let err = v.get("error").and_then(|x| x.as_str()).unwrap_or("capture refused").to_string();
                    complete_job(&id, false, json!({ "ok": false }), Some(err));
                } else {
                    let capture = v.get("capture").cloned().unwrap_or_else(|| json!({}));
                    let mut result = json!({
                        "ok": true,
                        "target": "browser",
                        "action": "screenshot",
                        "message": "Browser rail capture",
                        "via": "rust-host",
                        "url": page_url,
                        "scale": scale,
                    });
                    if let (Some(obj), Some(map)) = (capture.as_object(), result.as_object_mut()) {
                        for (k, val) in obj {
                            map.insert(k.clone(), val.clone());
                        }
                    }
                    complete_job(&id, true, result, None);
                }
            }
            Err(e) => complete_job(&id, false, json!({}), Some(format!("screenshot capture failed: {e}"))),
        }
        ack_ui(&id);
        return;
    }

    if matches!(action.as_str(), "click" | "hover" | "press_hold" | "type" | "key" | "scroll" | "drag" | "select") {
        let mut req = request_from_job(&action, &payload);
        req.job_id = Some(id.clone());
        if action == "press_hold" && req.dy.is_none() {
            req.dy = payload.get("hold_ms").and_then(|v| v.as_i64()).map(|v| v as i32);
        }
        match agent_action(&req) {
            Ok(raw) => {
                let ok = rust_action_ok(&raw);
                let message = if ok {
                    format!("{action} ok ({raw})")
                } else {
                    format!("{action} failed: {raw}")
                };
                complete_job(
                    &id,
                    ok,
                    json!({
                        "ok": ok,
                        "target": "browser",
                        "action": action,
                        "message": message,
                        "detail": raw,
                        "via": "rust-host",
                    }),
                    if ok { None } else { Some(format!("{action} failed")) },
                );
            }
            Err(e) => complete_job(&id, false, json!({}), Some(e)),
        }
        ack_ui(&id);
        return;
    }

    eprintln!("computer-host: job {id} action={action} has no Rust handler");
    complete_job(&id, false, json!({}), Some(format!("unsupported browser job action: {action}")));
}

fn wait_page_ready() -> bool {
    for i in 0..4 {
        match agent_action(&AgentRequest::new("ready")) {
            Ok(raw) if page_ready(&raw) => return true,
            Err(_) if i == 0 => thread::sleep(Duration::from_millis(250)),
            _ => {}
        }
        thread::sleep(Duration::from_millis(200 + i as u64 * 100));
    }
    false
}

fn run_snapshot(job_id: &str, attempts: u32) -> Result<String, String> {
    let mut last = "snapshot failed".to_string();
    for i in 0..attempts {
        let mut req = AgentRequest::new("snapshot");
        req.job_id = Some(job_id.to_string());
        match agent_action(&req) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last = e;
                eprintln!("computer-host: snapshot attempt {}/{attempts}: {last}", i + 1);
                thread::sleep(Duration::from_millis(350 + i as u64 * 200));
            }
        }
    }
    Err(last)
}

fn run_page_text(attempts: u32) -> Result<String, String> {
    let mut last = "page_text failed".to_string();
    for i in 0..attempts {
        match agent_action(&AgentRequest::new("page_text")) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last = e;
                thread::sleep(Duration::from_millis(300 + i as u64 * 150));
            }
        }
    }
    Err(last)
}

fn parse_elements(raw: &str) -> Value {
    let raw = unquote_eval(raw.trim());
    serde_json::from_str(&raw).unwrap_or_else(|_| json!([]))
}

fn ui_loop() {
    hello();
    let mut last_session = session_now();
    let mut hello_at = Instant::now();
    let mut ui_at = Instant::now();
    let mut ui_wait = Duration::from_millis(250);
    let mut idle: u32 = 0;
    loop {
        let session = session_now();
        if !session.is_empty() && session != last_session {
            last_session = session;
            hello();
            hello_at = Instant::now();
        }
        if hello_at.elapsed() >= Duration::from_secs(4) {
            hello();
            hello_at = Instant::now();
        }
        if ui_at.elapsed() >= ui_wait {
            let had = peek_ui();
            if had {
                idle = 0;
                ui_wait = Duration::from_millis(250);
            } else {
                idle = idle.saturating_add(1);
                ui_wait = Duration::from_millis(if idle >= 8 { 2_000 } else { 800 });
            }
            ui_at = Instant::now();
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn session_now() -> String {
    on_ui(Duration::from_secs(1), |m| m.session_id()).unwrap_or_default()
}

fn hello() {
    let Ok((x, y, w, h, scale, session)) = on_ui(Duration::from_secs(1), |m| {
        let (x, y, w, h) = *m.inner.bounds.borrow();
        (x, y, w, h, m.inner.scale.get(), m.session_id())
    }) else {
        return;
    };
    let mut body = json!({ "client": "desktop", "scale": scale });
    if w > 40.0 && h > 40.0 {
        body["bounds"] = json!({ "x": x, "y": y, "width": w, "height": h });
    }
    if !session.is_empty() {
        body["session_id"] = json!(session);
    }
    let _ = send(Request::post("computer/host/hello").json(&body).timeout(Duration::from_secs(4)));
}

/// Peek, do not take. Opens the rail once per command identity (`ts` excluded).
fn peek_ui() -> bool {
    let session = session_now();
    let path = if session.is_empty() {
        "computer/ui/command".to_string()
    } else {
        format!("computer/ui/command?session_id={}", encode_query(&session))
    };
    let Ok(resp) = send(Request::get(path).timeout(Duration::from_secs(4))) else {
        return false;
    };
    let Ok(v) = resp.json::<Value>() else { return false };
    let Some(cmd) = v.get("command").filter(|c| c.is_object()) else { return false };
    let Some(key) = ui_command_identity(cmd) else { return false };
    let fresh = on_ui(Duration::from_secs(1), move |m| {
        let mut last = m.inner.last_ui_key.borrow_mut();
        if *last == key {
            false
        } else {
            *last = key;
            true
        }
    })
    .unwrap_or(false);
    if fresh {
        let _ = on_ui(Duration::from_secs(1), |m| m.open_browser_rail());
    }
    fresh
}

fn fullscreen_loop() {
    loop {
        thread::sleep(Duration::from_millis(800));
        let on = match eval_blocking("!!document.fullscreenElement".into(), Duration::from_secs(1)) {
            Ok(raw) => {
                let flag = unquote_eval(&raw);
                flag == "true"
            }
            Err(_) => continue,
        };
        let _ = on_ui(Duration::from_secs(1), move |m| {
            if m.fullscreen() != on {
                m.inner.fullscreen.set(on);
            }
        });
    }
}

fn bearer() -> String {
    if let Ok(guard) = TOKEN.lock() {
        if let Some(cache) = guard.as_ref() {
            if cache.at.elapsed() < Duration::from_secs(30) && !cache.value.is_empty() {
                return cache.value.clone();
            }
        }
    }
    let tok = crate::runtime::load_token().unwrap_or_default();
    if tok.is_empty() {
        return String::new();
    }
    if let Ok(mut guard) = TOKEN.lock() {
        *guard = Some(TokenCache { at: Instant::now(), value: tok.clone() });
    }
    tok
}

fn clear_bearer() {
    if let Ok(mut guard) = TOKEN.lock() {
        *guard = None;
    }
}

fn http() -> Http {
    let base = format!("{}/api", crate::runtime::origin().trim_end_matches('/'));
    let mut http = Http::new(base).timeout(Duration::from_secs(20));
    let tok = bearer();
    if !tok.is_empty() {
        http = http.header("Authorization", format!("Bearer {tok}"));
    }
    http
}

fn send(req: Request) -> Result<remedy_ui::net::Response, String> {
    let cancel = Cancel::new();
    match http().send_blocking(req.clone(), &cancel) {
        Ok(resp) => Ok(resp),
        Err(e) if e.status() == Some(401) => {
            clear_bearer();
            let cancel = Cancel::new();
            http().send_blocking(req, &cancel).map_err(|err| err.to_string())
        }
        Err(e) => Err(e.to_string()),
    }
}

fn complete_job(job_id: &str, ok: bool, result: Value, error: Option<String>) {
    let body = json!({ "ok": ok, "result": result, "error": error });
    let path = format!("computer/jobs/{job_id}/complete");
    match send(Request::post(path).json(&body).timeout(Duration::from_secs(8))) {
        Ok(_) => eprintln!("computer-host: completed job {job_id} ok={ok}"),
        Err(e) => eprintln!("computer-host: complete {job_id}: {e}"),
    }
}

fn ack_ui(job_id: &str) {
    let path = format!("computer/ui/command/ack?job_id={}", encode_query(job_id));
    let _ = send(Request::post(path).timeout(Duration::from_secs(4)));
}

fn push_a11y(job_id: &str, elements: &Value) -> bool {
    let body = json!({ "job_id": job_id, "elements": elements });
    send(Request::post("computer/a11y/push").json(&body).timeout(Duration::from_secs(4))).is_ok()
}

fn encode_query(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn below_normal() {
    #[cfg(windows)]
    unsafe {
        unsafe extern "system" {
            fn GetCurrentThread() -> isize;
            fn SetThreadPriority(thread: isize, priority: i32) -> i32;
        }
        SetThreadPriority(GetCurrentThread(), -1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn poll_backs_off_the_way_the_tauri_host_does() {
        assert_eq!(poll_wait(0), (50, 0));
        assert_eq!(poll_wait(7), (0, 150));
        assert_eq!(poll_wait(8), (0, 800));
        assert_eq!(poll_wait(16), (0, 2000));
    }

    #[test]
    fn failures_hold_the_status_line_longer() {
        assert_eq!(status_hold("error: blocked").as_millis(), 5_000);
        assert_eq!(status_hold("not available").as_millis(), 5_000);
        assert_eq!(status_hold("Back").as_millis(), 3_200);
    }

    #[test]
    fn browser_version_product_yields_a_chrome_major() {
        let raw = r#"{"product":"Chrome/120.0.6099.130","browserVersion":"120.0.6099.130"}"#;
        assert_eq!(major_from_browser_version(raw).as_deref(), Some("120"));
        assert_eq!(major_from_browser_version(r#"{"browserVersion":"131.0.0"}"#).as_deref(), Some("131"));
        assert!(major_from_browser_version("not json").is_none());
    }
}
