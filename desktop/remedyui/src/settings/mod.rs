//! Settings for Remedy Desktop.
//!
//! The panel is a right-hand rail. It stays out of the tree until it is open,
//! so studio goldens keep their layout. Previews call [`view`] directly and do
//! not paint a theme: the golden harness owns the palette.

mod bits;
mod form;
mod http;
pub(crate) mod logic;
mod panel;
mod prefs;
mod sec_general;
mod sec_links;
mod sec_models;
mod sec_system;
mod sections;
mod state;

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::Length;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};

use crate::studio::Studio;

use panel::{close_panel, open_panel, view};
use state::Settings;

struct StudioHooks {
    id: u64,
    toggle: Rc<dyn Fn()>,
    /// Escape while Studio is up. True when the settings rail handled it.
    dismiss: Rc<dyn Fn() -> bool>,
}

thread_local! {
    static CURRENT: Cell<Option<Settings>> = const { Cell::new(None) };
    /// Appearance is read once per process window. Re-entering Studio must not
    /// reload the file over edits the owner already made.
    static APPEARANCE: Cell<bool> = const { Cell::new(false) };
    /// Studio's shell registers these so Ctrl+, and Escape move the workspace
    /// rail. Grove keeps [`toggle`] while the shell is unmounted. A generation
    /// id keeps a disposed shell from clearing the next shell's hooks.
    static STUDIO: RefCell<Option<StudioHooks>> = const { RefCell::new(None) };
    static STUDIO_IDS: Cell<u64> = const { Cell::new(1) };
    /// Next Settings open should land on Models (from the status-bar picker).
    static OPEN_MODELS: Cell<bool> = const { Cell::new(false) };
    /// Next Settings open should land on Privacy (from the status-bar chip).
    static OPEN_PRIVACY: Cell<bool> = const { Cell::new(false) };
}

/// The window's settings, when [`install`] has run and the signals are alive.
pub(crate) fn current() -> Option<Settings> {
    alive()
}

/// Drop a leftover Settings set so the next preview window does not inherit
/// a failed load (or another theme's rail) from a previous TestApp in-process.
pub(crate) fn forget_current() {
    CURRENT.with(|c| c.set(None));
}

pub(crate) fn take_open_models() -> bool {
    OPEN_MODELS.with(|c| c.replace(false))
}

pub(crate) fn take_open_privacy() -> bool {
    OPEN_PRIVACY.with(|c| c.replace(false))
}

/// Opens Settings on the Models category so the owner can add a provider.
pub(crate) fn open_models() {
    OPEN_MODELS.with(|c| c.set(true));
    if let Some(s) = current() {
        s.last_section.set("provider".into());
        s.category.set("models".into());
        open_panel(s);
        OPEN_MODELS.with(|c| c.set(false));
        return;
    }
    if let Some(toggle) = studio_toggle() {
        toggle();
    }
}

/// Opens Settings on the Privacy section (ClaimIDX and related switches).
pub(crate) fn open_privacy() {
    OPEN_PRIVACY.with(|c| c.set(true));
    if let Some(s) = current() {
        s.last_section.set("privacy".into());
        s.category.set("privacy".into());
        s.note_opened("privacy");
        open_panel(s);
        OPEN_PRIVACY.with(|c| c.set(false));
        return;
    }
    if let Some(toggle) = studio_toggle() {
        toggle();
    }
}


/// Current "speak replies" setting, including a mid-session override.
pub(crate) fn speak_replies_on() -> bool {
    if let Some(on) = crate::depth::speak_override() {
        return on;
    }
    current()
        .and_then(|s| s.voice.try_get().ok())
        .and_then(|v| {
            v.pointer("/settings/speak_replies")
                .and_then(|x| x.as_bool())
        })
        .unwrap_or(false)
}

/// Toggle spoken replies from the status dock and persist the change.
pub(crate) fn set_speak_replies(on: bool) {
    crate::depth::note_speak_replies(on);
    let Some(s) = current() else { return };
    bits::ask(
        s,
        "POST",
        "voice/settings",
        Some(serde_json::json!({ "speak_replies": on })),
        "Voice updated.",
    );
}

/// Settings filling a workspace rail body. The rail header already names the slide.
pub(crate) fn embedded(ui: &Ui, s: Settings) -> El {
    view(ui, s, false)
}

/// The settings slide just opened in a rail. Loads data once.
pub(crate) fn open_for_rail(s: Settings) {
    open_panel(s);
}

/// The settings slide left the rail.
pub(crate) fn close_for_rail(s: Settings) {
    if s.open.get() {
        close_panel(s);
    }
}

pub(crate) fn searching(s: &Settings) -> bool {
    logic::searching(&s.search.get())
}

pub(crate) fn clear_search(s: Settings) {
    state::clear_search(s);
}

/// True while a Studio shell is mounted, so the panel's own Close is hidden.
/// The rail header's ✕ is the close control in that case.
pub(crate) fn studio_rail_owns_close() -> bool {
    STUDIO.with(|c| c.borrow().is_some())
}

/// `toggle` is Ctrl+, and the status button's chord. `dismiss` is Escape.
pub(crate) fn bind_studio_hooks(
    toggle: impl Fn() + 'static,
    dismiss: impl Fn() -> bool + 'static,
) -> u64 {
    let id = STUDIO_IDS.with(|c| {
        let id = c.get();
        c.set(id.wrapping_add(1));
        id
    });
    STUDIO.with(|c| {
        *c.borrow_mut() = Some(StudioHooks {
            id,
            toggle: Rc::new(toggle),
            dismiss: Rc::new(dismiss),
        });
    });
    id
}

pub(crate) fn clear_studio_hooks(id: u64) {
    STUDIO.with(|c| {
        let clear = c.borrow().as_ref().is_some_and(|h| h.id == id);
        if clear {
            *c.borrow_mut() = None;
        }
    });
}

fn studio_toggle() -> Option<Rc<dyn Fn()>> {
    STUDIO.with(|c| c.borrow().as_ref().map(|h| h.toggle.clone()))
}

fn studio_dismiss() -> Option<Rc<dyn Fn() -> bool>> {
    STUDIO.with(|c| c.borrow().as_ref().map(|h| h.dismiss.clone()))
}

pub(crate) fn bind_host(app: &App) {
    prefs::bind_host(app);
}

pub(crate) fn host() -> Option<App> {
    prefs::host()
}

fn alive() -> Option<Settings> {
    let s = CURRENT.with(|c| c.get())?;
    if s.open.is_alive() { Some(s) } else { None }
}

fn load_appearance(s: Settings) {
    if APPEARANCE.with(|c| c.replace(true)) {
        return;
    }
    prefs::load_into(s);
    prefs::capture_system(s);
    prefs::apply(s);
}

/// Loads appearance for a real window. Studio previews (`local_only`) skip
/// that so a golden palette is not replaced with Forest.
///
/// The first live call owns the signals for the whole window. Grove and Studio
/// are `show` branches that dispose each other; a second `install` from the
/// Studio shell reuses the window's settings instead of building a set that
/// dies on the way back to Grove.
pub(crate) fn install(ui: &Ui, studio: Studio) -> Settings {
    prefs::bind_ui(ui);
    if let Some(s) = alive() {
        if !studio.local_only {
            load_appearance(s);
        }
        return s;
    }
    // The previous window's settings are gone. The next live window reads its own file.
    APPEARANCE.with(|c| c.set(false));
    let s = Settings::live();
    CURRENT.with(|c| c.set(Some(s)));
    if !studio.local_only {
        load_appearance(s);
    }
    s
}

/// Fixture settings for design-review shells. `install` will reuse this set.
pub(crate) fn install_preview(ui: &Ui) -> Settings {
    prefs::bind_ui(ui);
    let s = Settings::sample();
    CURRENT.with(|c| c.set(Some(s)));
    s
}

pub(crate) fn toggle() {
    let Some(s) = alive() else { return };
    if s.open.get() {
        close_panel(s);
    } else {
        open_panel(s);
    }
}

pub(crate) fn rail(ui: &Ui, s: Settings) -> El {
    let ui_rail = ui.clone();
    show(
        ui,
        move || s.open.try_get().unwrap_or(false),
        move || open_rail(&ui_rail, s),
    )
    .el()
    .layout(|l| {
        l.shrink = 1.0;
        l.min_height = Length::Px(0.0);
        l.max_height = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    })
}

fn open_rail(ui: &Ui, s: Settings) -> El {
    w::row(ui)
        .layout(|l| {
            l.height = Length::Percent(1.0);
            l.min_width = Length::Px(220.0);
            l.max_width = Length::Px(320.0);
            l.shrink = 1.0;
            l.grow = 0.0;
            l.basis = Length::Px(320.0);
        })
        .clip()
        .child(w::divider(ui, true))
        .child(view(ui, s, true))
}

/// The same rail, beside Grove. Studio's shell has its own copy, so this one
/// stays out of the tree while Studio is showing and while the panel is closed.
/// Grove's morning golden calls [`crate::grove::preview_home`], not this.
pub(crate) fn grove_rail(ui: &Ui, on_grove: impl Fn() -> bool + 'static) -> El {
    let Some(s) = alive() else {
        return ui.el("settings-grove-rail").layout(|l| {
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
        });
    };
    let ui_rail = ui.clone();
    show(
        ui,
        move || on_grove() && s.open.try_get().unwrap_or(false),
        move || open_rail(&ui_rail, s),
    )
    .el()
    .layout(|l| {
        l.shrink = 1.0;
        l.min_width = Length::Px(220.0);
        l.max_width = Length::Px(320.0);
        l.height = Length::Percent(1.0);
    })
}

/// Chords for a parent of both Grove and Studio. No-ops when Settings is gone.
pub(crate) fn shell_chord_current(ev: &KeyEvent) -> bool {
    let Some(s) = alive() else { return false };
    shell_chord(s, ev)
}

/// Escape for a parent of both Grove and Studio. No-ops when Settings is gone.
pub(crate) fn shell_escape_current(ev: &KeyEvent) -> bool {
    let Some(s) = alive() else { return false };
    shell_escape(s, ev)
}

/// Ctrl/Cmd chords. Capture phase, so they work while a field is focused.
pub(crate) fn shell_chord(s: Settings, ev: &KeyEvent) -> bool {
    if !ev.pressed || ev.composing {
        return false;
    }
    if ev.key == Key::F(1) {
        if !ev.repeat {
            bits::open_help_topic(crate::help_view::default_id());
        }
        return true;
    }
    let cmd = ev.modifiers.ctrl || ev.modifiers.meta;
    if !cmd {
        return false;
    }
    match &ev.key {
        Key::Character(c) if c == "," => {
            if !ev.repeat {
                if let Some(toggle_rail) = studio_toggle() {
                    toggle_rail();
                } else {
                    toggle();
                }
            }
            true
        }
        Key::Character(c) if c == "=" || c == "+" => {
            prefs::set_scale(s, logic::step_font_scale(&s.font_scale.get(), 1));
            true
        }
        Key::Character(c) if c == "-" || c == "_" => {
            prefs::set_scale(s, logic::step_font_scale(&s.font_scale.get(), -1));
            true
        }
        Key::Character(c) if c == "0" => {
            prefs::set_scale(s, "md");
            true
        }
        Key::Character(c) if c == "/" => {
            if !ev.repeat {
                bits::open_help_topic(crate::help_view::default_id());
            }
            true
        }
        _ => false,
    }
}

/// Escape clears a search first, then closes the panel. Modal scrims sit
/// outside this shell, so their Escape is not taken.
pub(crate) fn shell_escape(s: Settings, ev: &KeyEvent) -> bool {
    if !ev.pressed || ev.repeat || ev.key != Key::Escape {
        return false;
    }
    if let Some(dismiss) = studio_dismiss() {
        return dismiss();
    }
    if !s.open.get() {
        return false;
    }
    if logic::searching(&s.search.get()) {
        state::clear_search(s);
        return true;
    }
    close_panel(s);
    true
}

pub fn preview_general(ui: &Ui) -> El {
    view(ui, Settings::sample(), false)
}

pub fn preview_appearance(ui: &Ui) -> El {
    let s = Settings::sample();
    s.open_ids.set(vec!["theme".into()]);
    s.force.set(Some("theme".into()));
    view(ui, s, false)
}

pub fn preview_models(ui: &Ui) -> El {
    let s = Settings::sample();
    s.category.set("models".into());
    s.open_ids.set(vec!["provider".into()]);
    s.force.set(Some("provider".into()));
    view(ui, s, false)
}

/// Settings Models form for a single catalog provider. Used by tests that
/// check each row keeps its own label, default URL, and auth controls.
pub fn preview_models_provider(ui: &Ui, provider: &str) -> El {
    let s = Settings::sample();
    let catalog: Vec<state::Prov> = crate::setup::model::OFFLINE_PROVIDERS
        .iter()
        .map(|p| state::Prov::new(p.id, p.name, p.base_url, ""))
        .collect();
    if let Some(p) = catalog.iter().find(|p| p.id == provider) {
        s.provider.set(p.id.clone());
        s.loaded_provider.set(p.id.clone());
        s.base_url.set(p.base_url.clone());
        s.loaded_base_url.set(p.base_url.clone());
    }
    s.catalog.set(catalog);
    s.category.set("models".into());
    s.open_ids.set(vec!["provider".into()]);
    s.force.set(Some("provider".into()));
    view(ui, s, false)
}

pub fn preview_connect(ui: &Ui) -> El {
    let s = Settings::sample();
    s.category.set("connections".into());
    s.open_ids.set(vec!["connect".into()]);
    s.force.set(Some("connect".into()));
    s.pair_qr.set("remedy://pair/preview-connect".into());
    view(ui, s, false)
}

pub fn preview_error(ui: &Ui) -> El {
    view(ui, Settings::error_sample(), false)
}
