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

use std::cell::RefCell;

use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::Length;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};

use crate::studio::Studio;

use panel::{close_panel, open_panel, view};
use sec_system::HELP_HOME;
use state::Settings;

thread_local! {
    static CURRENT: RefCell<Option<Settings>> = RefCell::new(None);
}

pub(crate) fn bind_host(app: &App) {
    prefs::bind_host(app);
}

/// Loads appearance for a real window. Studio previews (`local_only`) skip
/// that so a golden palette is not replaced with Forest.
pub(crate) fn install(ui: &Ui, studio: Studio) -> Settings {
    prefs::bind_ui(ui);
    let s = Settings::live();
    CURRENT.with(|c| *c.borrow_mut() = Some(s));
    if !studio.local_only {
        prefs::load_into(s);
        prefs::capture_system(s);
        prefs::apply(s);
    }
    s
}

pub(crate) fn toggle() {
    let Some(s) = CURRENT.with(|c| *c.borrow()) else { return };
    if s.open.get() {
        close_panel(s);
    } else {
        open_panel(s);
    }
}

pub(crate) fn rail(ui: &Ui, s: Settings) -> El {
    let ui_rail = ui.clone();
    show(ui, move || s.open.get(), move || {
        w::row(&ui_rail)
            .layout(|l| {
                l.height = Length::Percent(1.0);
                l.shrink = 0.0;
            })
            .child(w::divider(&ui_rail, true))
            .child(view(&ui_rail, s, true))
    })
    .el()
    .layout(|l| {
        l.shrink = 0.0;
        l.height = Length::Percent(1.0);
    })
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
