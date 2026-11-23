//! Settings for Remedy Desktop.
//!
//! The panel is a right-hand rail. It stays out of the tree until it is open,
//! so studio goldens keep their layout. Previews call [`view`] directly and do
//! not paint a theme: the golden harness owns the palette.

mod bits;
mod form;
mod http;
mod logic;
mod panel;
mod prefs;
mod sec_general;
mod sec_links;
mod sec_models;
mod sec_system;
mod sections;
mod state;

use std::cell::Cell;

use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::Length;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};

use crate::studio::Studio;

use panel::{close_panel, open_panel, view};
use sec_system::HELP_HOME;
use state::Settings;

thread_local! {
    static CURRENT: Cell<Option<Settings>> = const { Cell::new(None) };
    /// Appearance is read once per process window. Re-entering Studio must not
    /// reload the file over edits the owner already made.
    static APPEARANCE: Cell<bool> = const { Cell::new(false) };
}

pub(crate) fn bind_host(app: &App) {
    prefs::bind_host(app);
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
    show(ui, move || s.open.try_get().unwrap_or(false), move || {
        open_rail(&ui_rail, s)
    })
    .el()
    .layout(|l| {
        l.shrink = 0.0;
        l.height = Length::Percent(1.0);
    })
}

fn open_rail(ui: &Ui, s: Settings) -> El {
    w::row(ui)
        .layout(|l| {
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
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
        l.shrink = 0.0;
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
            bits::open_http(HELP_HOME);
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
                toggle();
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
                bits::open_http(HELP_HOME);
            }
            true
        }
        _ => false,
    }
}

/// Escape clears a search first, then closes the panel. Modal scrims sit
/// outside this shell, so their Escape is not taken.
pub(crate) fn shell_escape(s: Settings, ev: &KeyEvent) -> bool {
    if !ev.pressed || ev.repeat || ev.key != Key::Escape || !s.open.get() {
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

pub fn preview_connect(ui: &Ui) -> El {
    let s = Settings::sample();
    s.category.set("connections".into());
    s.open_ids.set(vec!["connect".into()]);
    s.force.set(Some("connect".into()));
    view(ui, s, false)
}

pub fn preview_error(ui: &Ui) -> El {
    view(ui, Settings::error_sample(), false)
}
