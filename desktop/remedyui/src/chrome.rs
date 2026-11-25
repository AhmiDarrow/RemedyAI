//! Window chrome: title bar, overlays, shortcuts, tray, and quit.
//!
//! The Studio shell used by the existing goldens stays bare. This frame is the
//! one the app opens. The title bar is frameless on every platform so Windows
//! does not stack a system caption on top of these buttons (the Tauri app uses
//! system buttons on Windows and custom ones on Linux).

use std::time::Duration;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::{Color, Insets};
use remedy_ui::host::Notification;
use remedy_ui::input::{Key, KeyEvent, Modifiers};
use remedy_ui::layout::{Align, Justify, Length, Position, AUTO};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{catch_errors, App, El, Ui, Window};

use std::cell::{Cell, RefCell};

use crate::help_view;
use crate::messages;
use crate::palette;
use crate::prefs;
use crate::runtime::{self, Client};
use crate::server_menu;
use crate::studio::{Studio, WorkMode};
use crate::titlebar;
use crate::updates::{self, VERSION};
use crate::{fonts, Fonts};

thread_local! {
    static LIVE: Cell<Option<Chrome>> = const { Cell::new(None) };
    /// Panel mounted by `hosted`. `DIAG_FRESH` is set only by `install_diag`,
    /// so one golden's open sample is not reused by the next test.
    static DIAG: RefCell<Option<crate::diagnostics::DiagnosticsPanel>> = RefCell::new(None);
    static DIAG_FRESH: Cell<bool> = const { Cell::new(false) };
}

/// The next `hosted` frame mounts this panel once, instead of a closed one.
pub fn install_diag(panel: crate::diagnostics::DiagnosticsPanel) {
    DIAG.with(|slot| *slot.borrow_mut() = Some(panel));
    DIAG_FRESH.with(|flag| flag.set(true));
}

fn current_diag() -> crate::diagnostics::DiagnosticsPanel {
    DIAG.with(|slot| {
        let fresh = DIAG_FRESH.with(|flag| flag.replace(false));
        let mut guard = slot.borrow_mut();
        if fresh {
            if let Some(panel) = guard.clone().filter(|panel| panel.is_alive()) {
                return panel;
            }
        }
        let panel = crate::diagnostics::DiagnosticsPanel::new();
        *guard = Some(panel.clone());
        panel
    })
}

fn diag_now() -> Option<crate::diagnostics::DiagnosticsPanel> {
    DIAG.with(|slot| slot.borrow().clone().filter(|panel| panel.is_alive()))
}

fn close_diag() {
    if let Some(panel) = diag_now() {
        if panel.is_open() {
            panel.close();
        }
    }
}

thread_local! {
    /// Help panel mounted by `hosted`. `HELP_FRESH` is set only by `install_help`.
    static HELP: RefCell<Option<crate::help::HelpPanel>> = RefCell::new(None);
    static HELP_FRESH: Cell<bool> = const { Cell::new(false) };
}

/// The next `hosted` frame mounts this manual once, instead of a closed one.
pub fn install_help(panel: crate::help::HelpPanel) {
    HELP.with(|slot| *slot.borrow_mut() = Some(panel));
    HELP_FRESH.with(|flag| flag.set(true));
}

fn current_help() -> crate::help::HelpPanel {
    HELP.with(|slot| {
        let fresh = HELP_FRESH.with(|flag| flag.replace(false));
        let mut guard = slot.borrow_mut();
        if fresh {
            if let Some(panel) = guard.clone().filter(|panel| panel.is_alive()) {
                return panel;
            }
        }
        let panel = crate::help::HelpPanel::new();
        *guard = Some(panel.clone());
        panel
    })
}

fn help_now() -> Option<crate::help::HelpPanel> {
    HELP.with(|slot| slot.borrow().clone().filter(|panel| panel.is_alive()))
}

/// Open a bundled Help chapter in the in-app manual (same as F1).
pub fn open_help_topic(id: &str) {
    let id = id.trim();
    if id.is_empty() {
        return;
    }
    if let Some(panel) = help_now() {
        panel.open(Some(id));
    }
}

fn close_help() {
    if let Some(panel) = help_now() {
        if panel.is_open() {
            panel.close();
        }
    }
}

fn bind_live(chrome: Chrome) {
    LIVE.with(|slot| slot.set(Some(chrome)));
}

/// Hide the Grove/Studio switcher on setup, splash, and other first-run surfaces.
pub fn show_home_switcher(grove: Option<crate::grove::Grove>) {
    LIVE.with(|slot| {
        let Some(c) = slot.get() else { return };
        c.grove.set(grove);
    });
}

/// Opens or closes the status-bar server menu when a chrome frame is mounted.
pub fn toggle_server_menu() {
    LIVE.with(|slot| {
        let Some(c) = slot.get() else { return };
        let open = !c.server_open.try_get().unwrap_or(false);
        c.server_open.set(open);
        if open {
            c.menu_open.set(false);
        }
    });
}

/// Toggle a chrome overlay from the status dock (Help, Memory, Skills, Time Travel).
pub fn toggle_overlay(overlay: Overlay) {
    LIVE.with(|slot| {
        if let Some(c) = slot.get() {
            c.toggle(overlay);
        }
    });
}

/// Persist Simple / Advanced from the status dock.
pub fn set_advanced(on: bool) {
    LIVE.with(|slot| {
        if let Some(c) = slot.get() {
            c.advanced.set(on);
            c.save_prefs();
        }
    });
    if let Some(s) = crate::settings::current() {
        s.mode.set(if on {
            "advanced".into()
        } else {
            "simple".into()
        });
    }
}

/// Hide the desktop and open the Web UI, matching the 0.70.5 status-bar control.
pub fn open_web_now() {
    LIVE.with(|slot| {
        if let Some(c) = slot.get() {
            if let Some(ui) = c.ui_slot.get() {
                c.open_web(&ui);
            }
        }
    });
}

/// Usage dashboard from the status bar. The landed panel owns the modal.
pub fn open_usage(studio: &Studio) -> bool {
    let Some(c) = LIVE.with(|slot| slot.get()) else {
        return false;
    };
    let Some(ui) = c.ui_slot.get() else {
        return false;
    };
    c.present_usage(&ui, studio);
    true
}

/// App-control `open_panel` for the overlays this frame owns.
/// False leaves Grove's previous behavior in place.
pub fn handle_panel(panel: &str, article: Option<&str>, studio: &Studio) -> bool {
    let Some(c) = LIVE.with(|slot| slot.get()) else {
        return false;
    };
    match panel {
        "usage" => open_usage(studio),
        "diagnostics" => {
            c.open_diagnostics(studio);
            true
        }
        "help" => {
            let id = article
                .filter(|s| !s.is_empty())
                .unwrap_or(help_view::default_id());
            c.open_help(studio, id);
            true
        }
        "time_travel" => {
            c.open_time_travel(studio);
            true
        }
        "about" => {
            c.open(Overlay::About);
            true
        }
        _ => false,
    }
}

/// Phone-portal shell: hide the session rail. Dead signals count as off.
pub fn compact_now() -> bool {
    let from_sig = LIVE.with(|slot| {
        slot.get()
            .and_then(|c| c.connect_compact.try_get().ok())
            .unwrap_or(false)
    });
    from_sig || connect_flag(&std::env::var("REMEDY_CONNECT").unwrap_or_default())
}

/// Advanced UI mode shows Usage. Settings mode wins when that rail is alive.
pub fn advanced_now() -> bool {
    if let Some(s) = crate::settings::current() {
        if s.mode.try_get().ok().as_deref() == Some("advanced") {
            return true;
        }
    }
    LIVE.with(|slot| {
        slot.get()
            .and_then(|c| c.advanced.try_get().ok())
            .unwrap_or(false)
    })
}

pub const TRAY_SHOW: u32 = 1;
pub const TRAY_QUIT: u32 = 2;
pub const TRAY_SETTINGS: u32 = 3;
pub const TRAY_UPDATES: u32 = 4;
pub const TRAY_ABOUT: u32 = 5;

pub const FONT_ZOOM: [f32; 4] = [0.9, 1.0, 1.15, 1.3];
const TURN_NOTE: u64 = 7;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Overlay {
    None,
    Palette,
    Help,
    About,
    Diagnostics,
    TimeTravel,
    Quit,
    Settings,
    Memory,
    Skills,
}

#[derive(Clone, Debug)]
pub struct Agent {
    pub name: String,
    pub description: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Confirm {
    None,
    TimeTravel { session: String, message: String },
}

/// Copy handle for the chrome signals. The values stay on the UI thread.
#[derive(Clone, Copy)]
pub struct Chrome {
    pub overlay: Signal<Overlay>,
    pub menu_open: Signal<bool>,
    pub server_open: Signal<bool>,
    pub server_output: Signal<Vec<String>>,
    pub server_output_open: Signal<bool>,
    pub toast_text: Signal<String>,
    pub font_idx: Signal<u8>,
    pub advanced: Signal<bool>,
    pub connect_compact: Signal<bool>,
    pub locale: Signal<String>,
    pub update_available: Signal<bool>,
    pub update_note: Signal<String>,
    pub update_url: Signal<String>,
    pub update_version: Signal<String>,
    pub download_caption: Signal<String>,
    pub download_pct: Signal<Option<f32>>,
    pub skip_quit: Signal<bool>,
    pub start_in_tray: Signal<bool>,
    pub launch_at_login: Signal<bool>,
    pub maximized: Signal<bool>,
    pub help_id: Signal<String>,
    pub help_query: Signal<String>,
    pub palette_query: Signal<String>,
    pub palette_idx: Signal<usize>,
    pub confirm: Signal<Confirm>,
    pub agents: Signal<Vec<Agent>>,
    pub diag: Signal<serde_json::Value>,
    pub hive: Signal<serde_json::Value>,
    pub timeline: Signal<serde_json::Value>,
    usage_win: Signal<Option<crate::usage::UsageWindow>>,
    pub tooltip: Signal<String>,
    app: Signal<Option<App>>,
    window: Signal<Option<Window>>,
    ui_slot: Signal<Option<Ui>>,
    was_streaming: Signal<bool>,
    armed: Signal<bool>,
    /// Window Grove handle, so the title bar can switch surfaces.
    pub grove: Signal<Option<crate::grove::Grove>>,
    pub studio: Signal<Option<crate::studio::Studio>>,
    preview: bool,
}

impl Chrome {
    fn fresh(preview: bool) -> Self {
        Self {
            overlay: Signal::new(Overlay::None),
            menu_open: Signal::new(false),
            server_open: Signal::new(false),
            server_output: Signal::new(Vec::new()),
            server_output_open: Signal::new(false),
            toast_text: Signal::new(String::new()),
            font_idx: Signal::new(1),
            advanced: Signal::new(false),
            connect_compact: Signal::new(false),
            locale: Signal::new("auto".into()),
            update_available: Signal::new(false),
            update_note: Signal::new(String::new()),
            update_url: Signal::new(String::new()),
            update_version: Signal::new(String::new()),
            download_caption: Signal::new(String::new()),
            download_pct: Signal::new(None),
            skip_quit: Signal::new(false),
            start_in_tray: Signal::new(false),
            launch_at_login: Signal::new(false),
            maximized: Signal::new(false),
            help_id: Signal::new(help_view::default_id().into()),
            help_query: Signal::new(String::new()),
            palette_query: Signal::new(String::new()),
            palette_idx: Signal::new(0),
            confirm: Signal::new(Confirm::None),
            agents: Signal::new(Vec::new()),
            diag: Signal::new(serde_json::Value::Null),
            hive: Signal::new(serde_json::Value::Null),
            timeline: Signal::new(serde_json::Value::Null),
            usage_win: Signal::new(None),
            tooltip: Signal::new(String::new()),
            app: Signal::new(None),
            window: Signal::new(None),
            ui_slot: Signal::new(None),
            was_streaming: Signal::new(false),
            armed: Signal::new(false),
            grove: Signal::new(None),
            studio: Signal::new(None),
            preview,
        }
    }

    /// In-memory chrome for goldens. No disk, no network.
    pub fn preview(overlay: Overlay) -> Self {
        let c = Self::fresh(true);
        c.overlay.set(overlay);
        c.agents.set(vec![Agent {
            name: "remedy".into(),
            description: "House partner".into(),
        }]);
        c.diag.set(sample_diag());
        c.hive.set(sample_hive());
        c.timeline.set(sample_timeline());
        c.help_id.set(help_view::default_id().into());
        c
    }

    /// Prefs from `~/.remedy/desktop.json`, or defaults while spawn is disabled.
    pub fn load() -> Self {
        let prefs = prefs::DesktopPrefs::load();
        let c = Self::fresh(false);
        c.skip_quit.set(prefs.skip_quit_server_warning);
        c.start_in_tray.set(prefs.start_in_tray);
        c.launch_at_login.set(prefs.launch_at_login);
        c.locale.set(prefs.ui_language);
        c.advanced.set(prefs.ui_mode == "advanced");
        c.font_idx.set(prefs::font_index(&prefs.font_scale));
        c.connect_compact.set(connect_flag(
            &std::env::var("REMEDY_CONNECT").unwrap_or_default(),
        ));
        c
    }

    pub fn attach(self, app: &App, window: &Window, ui: &Ui) {
        self.app.set(Some(app.clone()));
        self.window.set(Some(window.clone()));
        self.ui_slot.set(Some(ui.clone()));
        // The title-bar maximize/restore icon must follow the real window
        // (Aero Snap, drag-unmaximize, Win+Up/Down), not only our button.
        self.maximized.set_if_changed(window.is_maximized());
        let chrome = self.clone();
        let win = window.clone();
        window.on_geometry(move |_, _| {
            chrome.maximized.set_if_changed(win.is_maximized());
        });
    }

    pub fn app(self) -> Option<App> {
        self.app.get()
    }

    pub fn win(self) -> Option<Window> {
        self.window.get()
    }


    /// Switch the window to Grove, when this chrome has a Grove handle.
    pub fn show_grove(self) {
        if let Some(grove) = self.grove.get() {
            grove.show_grove();
        }
    }

    /// Switch the window to Studio, when this chrome has a Grove handle.
    pub fn show_studio(self) {
        if let Some(grove) = self.grove.get() {
            grove.show_studio();
        }
    }

    pub fn close_overlay(self) {
        self.overlay.set(Overlay::None);
        self.confirm.set(Confirm::None);
    }

    pub fn toggle(self, overlay: Overlay) {
        if self.overlay.get() == overlay {
            self.close_overlay();
        } else {
            self.open(overlay);
        }
    }

    pub fn open(self, overlay: Overlay) {
        self.menu_open.set(false);
        close_diag();
        close_help();
        if overlay == Overlay::Palette {
            self.palette_query.set(String::new());
            self.palette_idx.set(0);
        }
        if overlay == Overlay::Help && self.help_id.get().is_empty() {
            self.help_id.set(help_view::default_id().into());
        }
        if overlay == Overlay::Skills {
            if let Some(studio) = self.studio.get() {
                crate::depth::load_skills(&studio);
            }
        }
        if overlay == Overlay::Memory {
            if let Some(studio) = self.studio.get() {
                studio.depth.memory_ready.set(false);
                crate::depth::load_memory(&studio);
            }
        }
        self.overlay.set(overlay);
    }

    /// The window's `Ui`, for work that finishes after the click that started it.
    pub(crate) fn ui(self) -> Option<Ui> {
        self.ui_slot.get()
    }

    pub fn toast(self, _ui: &Ui, text: impl Into<String>) {
        self.toast_text.set(text.into());
    }

    pub fn push_output(self, line: impl Into<String>) {
        let line = line.into();
        self.server_output.update(|v| {
            v.push(line);
            if v.len() > 80 {
                let extra = v.len() - 80;
                v.drain(0..extra);
            }
        });
    }

    /// Title-bar download chip. `pct` of `None` (or ≤ 2%) draws the indeterminate fill.
    /// Packaging (PR #76) can drive this; the chip is already on the bar.
    pub fn set_download(self, caption: impl Into<String>, pct: Option<f32>) {
        self.download_caption.set(caption.into());
        self.download_pct.set(pct);
    }

    pub fn request_quit(self) {
        self.menu_open.set(false);
        close_diag();
        close_help();
        let skipped = self.skip_quit.get()
            || crate::settings::current()
                .is_some_and(|s| s.skip_quit_warn.try_get().unwrap_or(false));
        if skipped {
            runtime::note_exiting();
            if let Some(app) = self.app() {
                app.quit();
            }
            return;
        }
        // Title-bar close hides the window (close-to-tray) and keeps the
        // process running. Tray Quit must bring the window back, or the
        // confirm sheet is invisible and the app never exits.
        if let Some(w) = self.win() {
            w.set_visible(true);
            w.focus();
        }
        self.overlay.set(Overlay::Quit);
    }

    /// Ctrl+, and the title-bar Settings item. The settings rail owns this when it is mounted.
    pub fn open_settings(self) {
        self.menu_open.set(false);
        let ev = KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL);
        if !crate::settings::shell_chord_current(&ev) {
            self.open(Overlay::Settings);
        }
    }

    pub fn confirm_quit(self, skip: bool) {
        if skip {
            self.skip_quit.set(true);
            self.save_prefs();
        }
        runtime::note_exiting();
        if let Some(app) = self.app() {
            app.quit();
        }
    }

    /// Title-bar close hides to the tray. It does not stop the server.
    pub fn hide(self) {
        if let Some(w) = self.win() {
            w.set_visible(false);
        }
    }

    pub fn minimize(self) {
        if let Some(w) = self.win() {
            w.set_minimized(true);
        }
    }

    pub fn toggle_max(self) {
        if let Some(w) = self.win() {
            let on = !w.is_maximized();
            w.set_maximized(on);
            self.maximized.set(on);
        } else {
            self.maximized.update(|v| *v = !*v);
        }
    }

    pub fn begin_move(self) {
        if let Some(w) = self.win() {
            w.begin_move();
        }
    }

    pub fn set_font(self, ui: &Ui, idx: u8) {
        let idx = idx.min(3);
        self.font_idx.set_if_changed(idx);
        if let Some(w) = self.win() {
            w.set_zoom(FONT_ZOOM[idx as usize]);
        }
        let _ = ui;
        self.save_prefs();
    }

    pub fn set_locale(self, ui: &Ui, id: &str) {
        self.locale.set(id.to_string());
        let tag = crate::tray_copy::fluent_tag(id);
        ui.set_locale(tag.clone());
        if let Some(app) = self.app() {
            app.set_locale(Some(tag));
        }
        self.save_prefs();
    }

    pub fn save_prefs(self) {
        if self.preview || runtime::spawn_disabled() {
            return;
        }
        let prefs = prefs::DesktopPrefs {
            close_to_tray: true,
            start_in_tray: self.start_in_tray.get(),
            skip_quit_server_warning: self.skip_quit.get(),
            ui_language: self.locale.get(),
            ui_mode: if self.advanced.get() {
                "advanced".into()
            } else {
                "simple".into()
            },
            font_scale: prefs::font_name(self.font_idx.get()).into(),
            launch_at_login: self.launch_at_login.get(),
        };
        prefs.save();
    }

    pub fn open_url(self, url: &str) {
        if url.is_empty() {
            return;
        }
        if let Some(app) = self.app() {
            app.open_url(url.to_string(), |_| {});
        }
    }

    pub fn open_web(self, ui: &Ui) {
        let origin = runtime::origin();
        let fallback = format!("{origin}/");
        // Desktop sidecar leaves browser bootstrap off by default. A minted
        // one-time link is explicit owner consent and is what actually signs
        // the Web UI in. Opening bare "/" shows a signed-out or missing-asset page.
        let client = self.studio.get().and_then(|s| s.client());
        let Some(client) = client else {
            self.open_url(&fallback);
            self.toast(ui, messages().text(ui, "toast-web"));
            return;
        };
        let Ok(wake) = remedy_ui::reactive::waker() else {
            self.open_url(&fallback);
            self.toast(ui, messages().text(ui, "toast-web"));
            return;
        };
        let c = self;
        runtime::post_json(&client, "auth/bootstrap-link", serde_json::json!({}), move |res| {
            let _ = wake.post(move || {
                let url = res
                    .ok()
                    .and_then(|v| {
                        v.get("url")
                            .and_then(|u| u.as_str())
                            .map(|s| s.to_string())
                    })
                    .filter(|s| s.starts_with("http://127.0.0.1") || s.starts_with("http://localhost") || s.starts_with("http://[::1]"))
                    .unwrap_or(fallback);
                c.open_url(&url);
            });
        });
        self.toast(ui, messages().text(ui, "toast-web"));
    }

    pub fn check_updates(self, ui: &Ui) {
        self.begin_check();
        let _ = ui;
    }

    pub fn begin_check(self) {
        if self.preview {
            self.toast_text.set(format!(
                "Remedy {VERSION} is the newest desktop build in the feed."
            ));
            return;
        }
        let c = self;
        let waker = remedy_ui::reactive::waker().ok();
        updates::fetch(move |res| {
            let Some(waker) = waker else { return };
            let _ = waker.post(move || apply_feed(c, res));
        });
    }

    pub fn install_update(self, ui: &Ui) {
        let url = self.update_url.get();
        if url.is_empty() || !self.update_available.get() {
            self.check_updates(ui);
            return;
        }
        // Quiet background download. Restart on the ready notice applies it.
        if let Some(app) = self.app() {
            crate::update::install_from_menu(&app);
        }
    }

    pub fn load_diag(self, studio: &Studio) {
        if self.preview {
            return;
        }
        let Some(client) = studio.client() else {
            return;
        };
        fetch_slot(&client, "diagnostics", self.diag);
        fetch_slot(&client, "hive/roster", self.hive);
    }

    /// The landed diagnostics window. The thin sheet is only the fallback
    /// when this frame has not mounted a panel yet.
    pub fn open_diagnostics(self, studio: &Studio) {
        self.menu_open.set(false);
        self.server_open.set(false);
        self.close_overlay();
        if let Some(prev) = self.usage_win.get() {
            prev.close();
            self.usage_win.set(None);
        }
        close_help();
        studio.time_travel.close();
        if let Some(panel) = diag_now() {
            panel.open();
        } else {
            self.overlay.set(Overlay::Diagnostics);
            self.load_diag(studio);
        }
    }

    /// The landed Help wiki. The thin sheet is only the fallback before mount.
    pub fn open_help(self, studio: &Studio, id: &str) {
        self.menu_open.set(false);
        self.server_open.set(false);
        self.close_overlay();
        close_diag();
        studio.time_travel.close();
        if let Some(prev) = self.usage_win.get() {
            prev.close();
            self.usage_win.set(None);
        }
        self.help_id.set(id.into());
        self.help_query.set(String::new());
        if let Some(panel) = help_now() {
            panel.open(Some(id));
        } else {
            self.overlay.set(Overlay::Help);
        }
    }

    /// The landed Time Travel rail.
    pub fn open_time_travel(self, studio: &Studio) {
        self.menu_open.set(false);
        self.server_open.set(false);
        self.close_overlay();
        close_diag();
        close_help();
        if let Some(prev) = self.usage_win.get() {
            prev.close();
            self.usage_win.set(None);
        }
        studio.time_travel.open();
    }

    /// The usage dashboard from PR #83. One modal, not a second sheet.
    fn present_usage(self, ui: &Ui, studio: &Studio) {
        self.menu_open.set(false);
        self.server_open.set(false);
        self.close_overlay();
        close_diag();
        close_help();
        studio.time_travel.close();
        if let Some(prev) = self.usage_win.get() {
            prev.close();
        }
        let win = crate::usage::UsagePanel::open(
            ui,
            crate::usage::UsageContext::new(
                studio.active.get(),
                studio.provider.get(),
                studio.model.get(),
            ),
        );
        self.usage_win.set(Some(win));
    }

    pub fn load_timeline(self, studio: &Studio) {
        if self.preview {
            return;
        }
        let Some(client) = studio.client() else {
            return;
        };
        let Some(id) = studio.active.get() else {
            return;
        };
        fetch_slot(
            &client,
            &format!("sessions/{}/timeline", path_seg(&id)),
            self.timeline,
        );
    }

    pub fn restore_step(self, studio: &Studio, message: &str) {
        if self.preview {
            return;
        }
        let Some(client) = studio.client() else {
            return;
        };
        let Some(id) = studio.active.get() else {
            return;
        };
        let body = serde_json::json!({ "message_id": message });
        let path = format!("sessions/{}/time-travel", path_seg(&id));
        let c = self;
        let s = studio.clone();
        post_then(&client, &path, body, move |_| c.load_timeline(&s));
    }

    pub fn retire_hive(self, studio: &Studio, hive_id: &str) {
        if self.preview {
            return;
        }
        let Some(client) = studio.client() else {
            return;
        };
        let body = serde_json::json!({ "hive_id": hive_id });
        let c = self;
        let s = studio.clone();
        post_then(&client, "hive/retire", body, move |_| c.load_diag(&s));
    }

    /// One-shot agent list and partner tray line. A thin wire to the server.
    pub fn watch_runtime(self, studio: &Studio) {
        if self.preview || studio.local_only {
            return;
        }
        let Some(client) = studio.client() else {
            return;
        };
        let agents = self.agents;
        let waker = remedy_ui::reactive::waker().ok();
        runtime::get_json(&client, "agents", {
            let waker = waker.clone();
            move |res| {
                let Ok(v) = res else { return };
                let list = parse_agents(&v);
                if let Some(w) = waker {
                    let _ = w.post(move || agents.set(list));
                }
            }
        });
        let tip = self.tooltip;
        runtime::get_json(&client, "partner/status", move |res| {
            let Ok(v) = res else { return };
            let Some(text) = tooltip_from(&v) else { return };
            if let Some(w) = waker {
                let _ = w.post(move || tip.set(text));
            }
        });
    }

    pub fn on_key(self, ui: &Ui, studio: &Studio, e: &KeyEvent) -> bool {
        if !e.pressed || e.composing || e.modifiers.meta || e.modifiers.alt {
            return false;
        }
        let font = is_font(e);
        if e.repeat && !font {
            return false;
        }
        if font {
            // Settings scales the type ramp. The window zoom is only the fallback.
            if !crate::settings::shell_chord_current(e) {
                self.apply_font(ui, e);
            }
            return true;
        }
        if let Some(win) = self.usage_win.get() {
            if !win.is_open() {
                self.usage_win.set(None);
            } else if e.key == Key::Escape {
                win.close();
                self.usage_win.set(None);
                return true;
            } else {
                // The dashboard is a dialog. Shift+Tab stays with it.
                return false;
            }
        }
        if let Some(panel) = diag_now() {
            if panel.is_open() {
                if e.key == Key::Escape {
                    panel.close();
                    return true;
                }
                // Same rule as the usage dialog: other keys stay with the panel.
                return false;
            }
        }
        if let Some(panel) = help_now() {
            if panel.is_open() {
                if e.key == Key::Escape {
                    panel.close();
                    return true;
                }
                return false;
            }
        }
        if studio.time_travel.is_open() {
            if e.key == Key::Escape {
                studio.time_travel.close();
                return true;
            }
            return false;
        }
        if self.confirm.get() != Confirm::None {
            if e.key == Key::Escape {
                self.confirm.set(Confirm::None);
                return true;
            }
            return false;
        }
        if self.overlay.get() != Overlay::None {
            if self.overlay.get() == Overlay::Palette && self.palette_key(ui, studio, e) {
                return true;
            }
            if e.key == Key::Escape {
                self.close_overlay();
                return true;
            }
            // A dialog owns every other key. Shift+Tab still moves focus.
            return false;
        }
        let typing = field_focused(ui);
        if typing && !allow_in_input(e) {
            return false;
        }
        self.global_key(ui, studio, e)
    }

    fn palette_key(self, ui: &Ui, studio: &Studio, e: &KeyEvent) -> bool {
        let items = palette::filter(&palette::commands(studio, &self), &self.palette_query.get());
        let len = items.len();
        match &e.key {
            Key::ArrowUp => {
                self.palette_idx.update(|i| *i = i.saturating_sub(1));
                true
            }
            Key::ArrowDown => {
                if len > 0 {
                    let next = (self.palette_idx.get() + 1).min(len - 1);
                    self.palette_idx.set_if_changed(next);
                }
                true
            }
            Key::Home => {
                self.palette_idx.set_if_changed(0);
                true
            }
            Key::End => {
                if len > 0 {
                    self.palette_idx.set_if_changed(len - 1);
                }
                true
            }
            Key::Enter => {
                if let Some(item) = items.get(self.palette_idx.get().min(len.saturating_sub(1))) {
                    palette::run(&item.id.clone(), studio, self, ui);
                }
                true
            }
            _ => false,
        }
    }

    fn global_key(self, _ui: &Ui, studio: &Studio, e: &KeyEvent) -> bool {
        let ctrl = e.modifiers.ctrl;
        match &e.key {
            Key::Character(c) if ctrl && c == "n" => {
                studio.add();
                true
            }
            Key::Character(c) if ctrl && !e.modifiers.shift && !e.modifiers.alt && !e.modifiers.meta && c == "1" => {
                self.show_grove();
                true
            }
            Key::Character(c) if ctrl && !e.modifiers.shift && !e.modifiers.alt && !e.modifiers.meta && c == "2" => {
                self.show_studio();
                true
            }
            Key::Character(c) if ctrl && (c == "p" || c == "k") => {
                self.toggle(Overlay::Palette);
                true
            }
            Key::Character(c) if ctrl && c == "b" => {
                studio.cycle_mode();
                true
            }
            Key::Character(c) if ctrl && e.modifiers.shift && (c == "[" || c == "{") => {
                if studio.mode.get() == WorkMode::Plan {
                    crate::plan_dock::toggle(studio);
                    true
                } else {
                    false
                }
            }
            Key::Tab if e.modifiers.shift && !ctrl => {
                studio.cycle_mode();
                true
            }
            Key::Character(c) if ctrl && c == "," => {
                self.open_settings();
                true
            }
            Key::Character(c) if ctrl && c == "/" => {
                self.open_help(studio, help_view::default_id());
                true
            }
            Key::F(1) => {
                self.open_help(studio, help_view::default_id());
                true
            }
            Key::Escape => {
                if self.menu_open.get() {
                    self.menu_open.set(false);
                    true
                } else if self.server_open.get() {
                    self.server_open.set(false);
                    true
                } else if crate::settings::shell_escape_current(e) {
                    // The chrome frame is focusable and often holds focus, so
                    // the shell column's bubble handler never sees Escape.
                    // Handle it here in capture so Settings always closes.
                    true
                } else {
                    false
                }
            }
            _ => false,
        }
    }

    fn apply_font(self, ui: &Ui, e: &KeyEvent) {
        let Some(ch) = char_key(e) else { return };
        match ch {
            "0" => self.set_font(ui, 1),
            "-" => {
                let next = self.font_idx.get().saturating_sub(1);
                self.set_font(ui, next);
            }
            "=" | "+" => {
                let next = (self.font_idx.get() + 1).min(3);
                self.set_font(ui, next);
            }
            _ => {}
        }
    }
}

pub fn connect_flag(raw: &str) -> bool {
    matches!(
        raw.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn is_font(e: &KeyEvent) -> bool {
    if !e.modifiers.ctrl {
        return false;
    }
    matches!(char_key(e), Some("=" | "+" | "-" | "0"))
}

fn allow_in_input(e: &KeyEvent) -> bool {
    if is_font(e) || e.key == Key::Escape || e.key == Key::F(1) {
        return true;
    }
    if e.key == Key::Tab && e.modifiers.shift && !e.modifiers.ctrl {
        return true;
    }
    match &e.key {
        Key::Character(c) if e.modifiers.ctrl && (c == "b" || c == "/" || c == "1" || c == "2") => true,
        Key::Character(c) if e.modifiers.ctrl && e.modifiers.shift && (c == "[" || c == "{") => {
            true
        }
        _ => false,
    }
}

fn char_key(e: &KeyEvent) -> Option<&str> {
    match &e.key {
        Key::Character(c) => Some(c.as_str()),
        _ => None,
    }
}

fn field_focused(ui: &Ui) -> bool {
    ui.reader_view().into_iter().any(|n| {
        n.focused
            && matches!(
                n.role,
                Role::TextInput
                    | Role::SearchInput
                    | Role::MultilineTextInput
                    | Role::PasswordInput
            )
    })
}

fn apply_feed(c: Chrome, res: Result<updates::Feed, String>) {
    let ui = c.ui_slot.get();
    let say = |id: &str, args: &[(&str, remedy_ui::i18n::Arg)]| -> String {
        if let Some(ui) = &ui {
            messages().format(ui, id, args)
        } else {
            let tag = crate::tray_copy::fluent_tag(&c.locale.get());
            messages().format_in(&tag, id, None, args)
        }
    };
    let text = match res {
        // A newer version with no download for this platform is not installable here.
        Ok(feed) if updates::is_newer(&feed.version, VERSION) && !feed.url.is_empty() => {
            c.update_available.set(true);
            c.update_version.set(feed.version.clone());
            c.update_url.set(feed.url);
            c.update_note.set(feed.notes);
            say("toast-updateFound", &[("version", feed.version.into())])
        }
        Ok(_) => {
            c.update_available.set(false);
            say("toast-updateNone", &[])
        }
        Err(_) => say("toast-updateFail", &[]),
    };
    c.toast_text.set(text);
}

fn fetch_slot(client: &Client, path: &str, slot: Signal<serde_json::Value>) {
    let Some(waker) = remedy_ui::reactive::waker().ok() else {
        return;
    };
    runtime::get_json(client, path, move |res| {
        let _ = waker.post(move || match res {
            Ok(v) => slot.set(v),
            Err(e) => slot.set(serde_json::json!({ "error": e })),
        });
    });
}

fn post_then(
    client: &Client,
    path: &str,
    body: serde_json::Value,
    done: impl FnOnce(Result<serde_json::Value, String>) + Send + 'static,
) {
    let Some(waker) = remedy_ui::reactive::waker().ok() else {
        return;
    };
    runtime::post_json(client, path, body, move |res| {
        let _ = waker.post(move || done(res));
    });
}

fn parse_agents(v: &serde_json::Value) -> Vec<Agent> {
    let Some(arr) = v.get("agents").and_then(|a| a.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|a| {
            let name = a.get("name").and_then(|n| n.as_str())?;
            if name.is_empty() {
                return None;
            }
            Some(Agent {
                name: name.to_string(),
                description: a
                    .get("description")
                    .and_then(|d| d.as_str())
                    .unwrap_or("")
                    .to_string(),
            })
        })
        .collect()
}

fn tooltip_from(v: &serde_json::Value) -> Option<String> {
    for ptr in ["/soma/tray_tooltip", "/hearth/line"] {
        if let Some(s) = v.pointer(ptr).and_then(|n| n.as_str()) {
            let s = s.trim();
            if !s.is_empty() {
                return Some(s.to_string());
            }
        }
    }
    None
}

fn path_seg(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn sample_diag() -> serde_json::Value {
    serde_json::json!({
        "ok": true,
        "overall": "ok",
        "checked_at": "2026-11-22T21:00:00Z",
        "issues": [],
        "remedy": {
            "version": "1.0.0",
            "api": { "host": "127.0.0.1", "port": 7400, "listening": true },
            "active_provider": "local",
            "active_model": "remedy",
            "sessions_count": 3,
            "home_dir": "~/.remedy"
        },
        "rmb": { "ok": true, "ready": true, "model_name": "remedy" },
        "hardware": { "platform": "linux", "system": "Linux" },
        "providers": { "connected_count": 1 },
        "computer": { "ok": true },
        "vision": { "ok": true, "ready": true }
    })
}

fn sample_hive() -> serde_json::Value {
    serde_json::json!({
        "daughters": [{
            "id": "hive-preview",
            "cadence": "forager",
            "status": "idle",
            "goal": "Keep the house",
            "done": false
        }],
        "live_posts": 0,
        "live_foragers": 1,
        "count": 1
    })
}

fn sample_timeline() -> serde_json::Value {
    serde_json::json!({
        "steps": [{
            "step": 1,
            "id": "m1",
            "kind": "user",
            "label": "Set the house rules",
            "preview": "Keep answers short.",
            "message_id": "m1",
            "can_restore": true
        }]
    })
}

pub fn preview_frame(ui: &Ui, overlay: Overlay, menu: bool) -> El {
    let chrome = Chrome::preview(overlay);
    if menu {
        chrome.menu_open.set(true);
    }
    let studio = Studio::preview();
    chrome.studio.set(Some(studio.clone()));
    let body_studio = studio.clone();
    hosted(ui, studio, chrome, move |ui| {
        crate::shell_fixed(ui, body_studio.clone()).layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.max_height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
    })
}

thread_local! {
    static DOCK_IN_HOST: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// True while the window chrome owns the status dock, so Grove/Studio shells
/// do not paint a second copy (and the dock cannot be clipped by their caps).
pub fn dock_in_host() -> bool {
    DOCK_IN_HOST.with(|c| c.get())
}

/// Title bar, overlays, and shortcuts around `body` (Grove, or a fixed Studio shell).
pub fn hosted(ui: &Ui, studio: Studio, chrome: Chrome, body: impl Fn(&Ui) -> El + 'static) -> El {
    chrome.studio.set(Some(studio.clone()));
    bind_live(chrome);
    let u_err = ui.clone();
    let u_build = ui.clone();
    catch_errors(
        ui,
        move |err, retry| crate::panels::crash(&u_err, err, retry, chrome),
        move || {
            DOCK_IN_HOST.with(|c| c.set(true));
            let body = body(&u_build);
            build_hosted(&u_build, studio.clone(), chrome, body)
        },
    )
}

fn build_hosted(ui: &Ui, studio: Studio, chrome: Chrome, body: El) -> El {
    fonts::prepare_theme(ui, &Fonts::load());
    DOCK_IN_HOST.with(|c| c.set(true));
    let shell = w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(titlebar::bar(ui, &studio, chrome))
        .child({
            let body = body.layout(|l| {
                l.grow = 1.0;
                l.shrink = 1.0;
                l.basis = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            });
            let cap = body.clone();
            let cap_ui = ui.clone();
            body.bind(move || {
                // Title bar (36) + status dock (~44). Leave the rest for Grove/Studio.
                let notice = if crate::update::ready_signal()
                    .try_get()
                    .unwrap_or_default()
                    .is_empty()
                {
                    0.0
                } else {
                    36.0
                };
                let h = (cap_ui.window_size().height - 36.0 - 44.0 - notice).max(160.0);
                let _ = cap.clone().layout(|l| {
                    l.height = Length::Px(h);
                    l.max_height = Length::Px(h);
                    l.min_height = Length::Px(0.0);
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.basis = Length::Px(0.0);
                    l.width = Length::Percent(1.0);
                });
            })
        })
        .child(crate::update::ready_notice(ui))
        .child(crate::status::status_bar(ui, studio.clone(), || {
            crate::settings::toggle()
        }));
    let overlays = overlay_slot(ui, studio.clone(), chrome);
    let toast = toast_layer(ui, chrome);
    // Closed, the host is a zero-size absolute child. The open panel draws
    // through `ui.overlay`, so studio and chrome goldens keep their pixels.
    let diag = current_diag();
    let diag_host = crate::diagnostics::DiagnosticsPanel::view(ui, &diag).layout(|l| {
        l.position = Position::Absolute;
        l.width = Length::Px(0.0);
        l.height = Length::Px(0.0);
        l.inset.top = 0.0;
        l.inset.start = 0.0;
    });
    let help = current_help();
    let help_host = help.view(ui).layout(|l| {
        l.position = Position::Absolute;
        l.width = Length::Px(0.0);
        l.height = Length::Px(0.0);
        l.inset.top = 0.0;
        l.inset.start = 0.0;
    });
    studio.time_travel.mount(ui, studio.clone());

    let frame = ui.el("chrome").column().layout(|l| {
        l.position = Position::Relative;
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    });
    let key_ui = ui.clone();
    let key_studio = studio.clone();
    let frame = frame
        .focusable(true)
        .on_key_capture(move |e| chrome.on_key(&key_ui, &key_studio, e))
        .child(shell)
        .child(overlays)
        .child(toast)
        .child(diag_host)
        .child(help_host)
        .on_removed(|| DOCK_IN_HOST.with(|c| c.set(false)));

    let root_ui = ui.clone();
    let root_studio = studio.clone();
    ui.root()
        .on_key(move |e| chrome.on_key(&root_ui, &root_studio, e));

    let watch = frame.clone();
    let watch_ui = ui.clone();
    let watch_studio = studio.clone();
    let frame = frame.bind(move || {
        let overlay = chrome.overlay.get();
        let wants_field = matches!(overlay, Overlay::Palette | Overlay::Help);
        if !wants_field && !chrome.armed.get() {
            chrome.armed.set_if_changed(true);
            if !field_focused(&watch_ui) {
                watch.focus();
            }
        }
        if let Some(s) = crate::settings::current() {
            let lang = s.ui_language.get();
            let c = chrome;
            let u = watch_ui.clone();
            remedy_ui::reactive::untrack(move || {
                if c.locale.get() != lang {
                    c.set_locale(&u, &lang);
                }
            });
        }
        let now = watch_studio.streaming.get();
        let was = chrome.was_streaming.get();
        if was != now {
            chrome.was_streaming.set_if_changed(now);
            if was && !now && !watch_studio.local_only {
                let c = chrome;
                let u = watch_ui.clone();
                let s = watch_studio.clone();
                remedy_ui::reactive::untrack(move || {
                    let title = session_title(&s);
                    let head = messages().text(&u, "toast-turn");
                    c.toast_text.set(format!("{head} — {title}"));
                    if let Some(app) = c.app() {
                        app.notify(
                            Notification::new(TURN_NOTE, head, title).silent(true),
                            |_| {},
                        );
                    }
                });
            }
        }
    });
    frame
}

fn session_title(studio: &Studio) -> String {
    let id = studio.active.get();
    studio
        .sessions
        .get()
        .into_iter()
        .find(|s| Some(&s.id) == id.as_ref())
        .map(|s| {
            if s.title.is_empty() {
                "Untitled".into()
            } else {
                s.title
            }
        })
        .unwrap_or_else(|| "Remedy".into())
}

fn overlay_slot(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let host = ui
        .el("chrome-overlays")
        .layout(|l| crate::abs_fill(l))
        .pass_through();
    let slot = host.clone();
    let u = ui.clone();
    let s = studio.clone();
    let host = host.bind(move || {
        let overlay = chrome.overlay.get();
        let menu = chrome.menu_open.get();
        let server = chrome.server_open.get();
        let confirm = chrome.confirm.get();
        ensure_loaded(chrome, &s, overlay);
        slot.clear();
        // `El::child` takes the handle by value. Clone it; the clone is the same node.
        remedy_ui::reactive::untrack(|| {
            if menu {
                let _ = slot
                    .clone()
                    .child(dismiss_layer(&u, move || chrome.menu_open.set(false)));
                let _ =
                    slot.clone()
                        .child(placed(&u, 36.0, 6.0, true, titlebar::menu(&u, &s, chrome)));
            }
            if server {
                let _ = slot
                    .clone()
                    .child(dismiss_layer(&u, move || chrome.server_open.set(false)));
                let _ = slot.clone().child(placed(
                    &u,
                    0.0,
                    8.0,
                    false,
                    server_menu::panel(&u, s.clone(), chrome),
                ));
            }
            if overlay != Overlay::None {
                let _ = slot
                    .clone()
                    .child(overlay_layer(&u, s.clone(), chrome, overlay));
            }
            if confirm != Confirm::None {
                let _ = slot.clone().child(modal(
                    &u,
                    false,
                    move || chrome.confirm.set(Confirm::None),
                    crate::panels::confirm_card(&u, s.clone(), chrome),
                ));
            }
        });
    });
    host
}

fn ensure_loaded(chrome: Chrome, studio: &Studio, overlay: Overlay) {
    match overlay {
        Overlay::Diagnostics if chrome.diag.get().is_null() => chrome.load_diag(studio),
        Overlay::TimeTravel if chrome.timeline.get().is_null() => chrome.load_timeline(studio),
        _ => {}
    }
}

fn overlay_layer(ui: &Ui, studio: Studio, chrome: Chrome, overlay: Overlay) -> El {
    let top = overlay == Overlay::Palette;
    let content = match overlay {
        Overlay::None => ui.el("none"),
        Overlay::Palette => palette::panel(ui, studio, chrome),
        Overlay::Help => sheet(
            ui,
            &messages().text(ui, "menu-help"),
            true,
            true,
            help_view::body(ui, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::About => sheet(
            ui,
            &messages().text(ui, "menu-about"),
            false,
            false,
            crate::panels::about_body(ui, &studio, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::Diagnostics => sheet(
            ui,
            &messages().text(ui, "diag-title"),
            true,
            false,
            crate::panels::diagnostics_body(ui, studio, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::TimeTravel => sheet(
            ui,
            &messages().text(ui, "time-title"),
            false,
            false,
            crate::panels::timeline_body(ui, studio, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::Quit => sheet(
            ui,
            &messages().text(ui, "quit-title"),
            false,
            false,
            crate::panels::quit_body(ui, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::Settings => sheet(
            ui,
            &messages().text(ui, "menu-settings"),
            false,
            false,
            crate::panels::settings_body(ui, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::Memory => sheet(
            ui,
            &messages().text(ui, "memory-title"),
            true,
            true,
            crate::panels::memory_body(ui, chrome),
            move || chrome.close_overlay(),
        ),
        Overlay::Skills => sheet(
            ui,
            &messages().text(ui, "skills-title"),
            true,
            true,
            crate::panels::skills_body(ui, chrome),
            move || chrome.close_overlay(),
        ),
    };
    modal(ui, top, move || chrome.close_overlay(), content)
}

fn sheet(
    ui: &Ui,
    title: &str,
    wide: bool,
    fill: bool,
    body: El,
    on_close: impl Fn() + 'static,
) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let title_text = title.to_string();
    let heading = w::heading(ui, 2, title_text.clone()).layout(|l| l.grow = 1.0);
    let close_ui = ui.clone();
    let close = w::button(
        ui,
        move || messages().text(&close_ui, "diag-close"),
        on_close,
    )
    .variant("quiet")
    .style(StylePatch::new().foreground(colors.muted));
    w::column(ui)
        .style(crate::surface_of(ui))
        .semantics(Semantics::new(Role::Dialog).label(title_text).modal(true))
        .layout(|l| {
            if wide {
                l.width = Length::Percent(0.92);
                l.height = Length::Percent(0.86);
                l.max_width = Length::Px(980.0);
            } else {
                l.width = Length::Px(440.0);
                l.max_width = Length::Percent(0.94);
                l.max_height = Length::Percent(0.9);
            }
        })
        .child(
            w::row(ui)
                .align(Align::Center)
                .justify(Justify::SpaceBetween)
                .gap(unit * 2.0)
                .padding(Insets::all(unit * 3.0))
                .child(heading)
                .child(close),
        )
        .child(w::divider(ui, false))
        .child(if fill {
            body.padding(Insets::all(unit * 2.0)).layout(|l| {
                l.grow = 1.0;
                l.min_height = Length::Px(0.0);
                l.height = Length::Percent(1.0);
            })
        } else {
            w::scroll(ui)
                .child(body.padding(Insets::all(unit * 3.0)))
                .layout(|l| {
                    l.grow = 1.0;
                    l.min_height = Length::Px(0.0);
                })
        })
}

fn modal(ui: &Ui, top: bool, on_close: impl Fn() + 'static, content: El) -> El {
    let scrim = ui
        .el("scrim")
        .style(StylePatch::new().background(Color::rgba8(0, 0, 0, 122)))
        .layout(|l| crate::abs_fill(l))
        .on_click(on_close);
    let mut col = w::column(ui)
        .align(Align::Center)
        .pass_through()
        .layout(|l| crate::abs_fill(l));
    if top {
        col = col
            .child(ui.el("palette-gap").layout(|l| {
                l.height = Length::Percent(0.14);
                l.width = Length::Percent(1.0);
                l.shrink = 0.0;
            }))
            .child(content)
            .child(ui.el("palette-rest").layout(|l| l.grow = 1.0));
    } else {
        col = col.justify(Justify::Center).child(content);
    }
    ui.el("modal")
        .layout(|l| crate::abs_fill(l))
        .child(scrim)
        .child(col)
}

fn dismiss_layer(ui: &Ui, on_close: impl Fn() + 'static) -> El {
    ui.el("dismiss")
        .layout(|l| crate::abs_fill(l))
        .on_click(on_close)
}

fn placed(ui: &Ui, top: f32, start: f32, from_top: bool, child: El) -> El {
    let host = ui
        .el("placed")
        .pass_through()
        .layout(|l| crate::abs_fill(l));
    host.child(child.layout(move |l| {
        l.position = Position::Absolute;
        if from_top {
            l.inset.top = top;
            l.inset.bottom = AUTO;
        } else {
            l.inset.bottom = 44.0;
            l.inset.top = AUTO;
        }
        l.inset.start = start;
        l.inset.end = AUTO;
    }))
}

fn toast_layer(ui: &Ui, chrome: Chrome) -> El {
    let host = ui.el("toast-layer").layout(|l| {
        l.position = Position::Absolute;
        l.inset.bottom = 56.0;
        l.inset.end = 16.0;
        l.inset.top = AUTO;
        l.inset.start = AUTO;
    });
    let slot = host.clone();
    let u = ui.clone();
    let host = host.bind(move || {
        let text = chrome.toast_text.get();
        slot.clear();
        if text.is_empty() {
            return;
        }
        let colors = u.theme().colors.clone();
        let _ = slot.clone().child(
            w::text(&u, text.clone()).style(
                StylePatch::new()
                    .background(colors.card)
                    .foreground(colors.text)
                    .radius(u.theme().radii.medium)
                    .padding(Insets::all(u.theme().spacing.unit * 2.0)),
            ),
        );
        let c = chrome;
        let current = text;
        u.after(Duration::from_secs(4), move || {
            if c.toast_text.get() == current {
                c.toast_text.set(String::new());
            }
        });
    });
    host
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quit_label_matches_the_menu() {
        let m = crate::messages();
        assert_eq!(m.format_in("en-US", "menu-quit", None, &[]), "Quit Remedy");
        assert_eq!(
            m.format_in("en-US", "menu-about", None, &[]),
            "About Remedy"
        );
        // Fluent wraps the placeable in bidi isolates. The menu label is built
        // without them so the accessible name is the visible version string.
        let about = m.format_in(
            "en-US",
            "menu-aboutVersion",
            None,
            &[("version", VERSION.into())],
        );
        let about = about.replace(['\u{2068}', '\u{2069}'], "");
        assert_eq!(about, "About Remedy (v1.1.0)");
    }

    #[test]
    fn connect_flag_matches_the_env_switch() {
        assert!(connect_flag("1"));
        assert!(connect_flag("true"));
        assert!(!connect_flag(""));
        assert!(!connect_flag("0"));
    }
}
