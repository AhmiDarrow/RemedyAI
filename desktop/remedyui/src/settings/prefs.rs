//! Appearance and font choices. They apply immediately and are stored in
//! `config.toml` next to the families `fonts.rs` already reads.
//!
//! `[fonts]` keeps `ui`, `display`, and `code` as family names (`Remedy Sans UI`,
//! `Ahmi Pixel`, `AhmiPrint`). `heading` is still read and written as a
//! display alias. Reading and accent are written beside them.
//! `[appearance]` holds theme, density, scale, motion, contrast, accent, and
//! the last settings section. Other tables in the file are left alone.

use std::cell::RefCell;
use std::fs;
use std::path::PathBuf;

use remedy_ui::core::Color;
use remedy_ui::style::Density;
use remedy_ui::themes::{ThemeChoice, sheet};
use remedy_ui::{App, Ui};

use crate::fonts::{self, Fonts};
use crate::settings::logic;

use super::state::Settings;

thread_local! {
    static UI: RefCell<Option<Ui>> = RefCell::new(None);
    static HOST: RefCell<Option<App>> = RefCell::new(None);
}

pub fn bind_ui(ui: &Ui) {
    UI.with(|c| *c.borrow_mut() = Some(ui.clone()));
}

pub fn bind_host(app: &App) {
    HOST.with(|c| *c.borrow_mut() = Some(app.clone()));
}

pub fn host() -> Option<App> {
    HOST.with(|c| c.borrow().clone())
}

/// Reads `[appearance]` and the font roles. Missing keys keep today's defaults.
pub fn load_into(s: Settings) {
    let text = fs::read_to_string(fonts::config_path()).unwrap_or_default();
    let fonts = Fonts::from_toml(&text);
    s.font_ui.set(logic::face_for_family(&fonts.ui));
    s.font_code.set(logic::face_for_family(&fonts.code));
    s.font_heading.set(logic::face_for_family(&fonts.display));
    if let Some(h) = logic::table_string(&text, "fonts", "reading") {
        if !h.is_empty() {
            s.font_reading.set(logic::face_for_family(&h));
        }
    }
    if let Some(h) = logic::table_string(&text, "fonts", "accent") {
        if !h.is_empty() {
            s.font_accent.set(logic::face_for_family(&h));
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "theme") {
        if !v.is_empty() {
            s.theme_id.set(v);
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "density") {
        if v == "compact" || v == "cozy" {
            s.density.set(v);
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "font_scale") {
        if matches!(v.as_str(), "sm" | "md" | "lg" | "xl") {
            s.font_scale.set(v);
        }
    }
    if let Some(v) = logic::table_bool(&text, "appearance", "reduce_motion") {
        s.reduce_motion.set(v);
    }
    if let Some(v) = logic::table_bool(&text, "appearance", "high_contrast") {
        s.high_contrast.set(v);
    }
    if let Some(v) = logic::table_string(&text, "appearance", "custom_accent") {
        s.custom_accent.set(v);
    }
    if let Some(v) = logic::table_string(&text, "appearance", "settings_mode") {
        if v == "simple" || v == "advanced" {
            s.mode.set(v);
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "last_section") {
        if logic::section(&v).is_some() {
            s.last_section.set(v);
        }
    }
    if let Some(v) = logic::table_bool(&text, "appearance", "privacy_shield") {
        s.privacy_shield.set(v);
    }
    if let Some(v) = logic::table_bool(&text, "appearance", "skip_quit_warn") {
        s.skip_quit_warn.set(v);
    }
}

pub fn capture_system(s: Settings) {
    let Some(app) = host() else { return };
    if let Some((dark, hc)) = app.appearance() {
        s.system_dark.set(dark);
        s.system_hc.set(hc);
    }
    app.on_appearance(move |dark, hc| {
        s.system_dark.set(dark);
        s.system_hc.set(hc);
        apply(s);
    });
}

/// Paints the current choices onto the open UI. Does not write the file.
pub fn apply(s: Settings) {
    let Some(ui) = UI.with(|c| c.borrow().clone()) else { return };
    let choice = if s.theme_id.get() == "system" {
        ThemeChoice::System
    } else {
        ThemeChoice::Palette(s.theme_id.get())
    };
    let high = s.high_contrast.get() || s.system_hc.get();
    let mut theme = choice.resolve(s.system_dark.get(), high);
    let fonts = Fonts {
        ui: logic::family_for_face(&s.font_ui.get()),
        display: logic::family_for_face(&s.font_heading.get()),
        code: logic::family_for_face(&s.font_code.get()),
    };
    fonts::apply_to_theme(&mut theme, &fonts);
    let factor = logic::font_scale_factor(&s.font_scale.get());
    theme.type_scale.small *= factor;
    theme.type_scale.body *= factor;
    theme.type_scale.title *= factor;
    if s.density.get() == "compact" {
        theme.density = Density::Compact;
        theme.spacing.unit = (theme.spacing.unit * 0.85).max(2.0);
        theme.spacing.control_height = (theme.spacing.control_height * 0.85).max(28.0);
    }
    if let Some(accent) = parse_accent(&s.custom_accent.get()) {
        theme.colors.on_accent = on_accent(accent);
        theme.colors.accent = accent;
    }
    theme.motion.reduced = s.reduce_motion.get();
    let sheet = sheet(&theme);
    ui.set_theme(theme, sheet);
    ui.set_reduced_motion(s.reduce_motion.get());
    if let Some(app) = host() {
        app.set_reduce_motion(s.reduce_motion.get());
    }
}

pub fn persist(s: Settings) {
    if s.preview {
        return;
    }
    let path = fonts::config_path();
    let text = fs::read_to_string(&path).unwrap_or_default();
    let fonts = [
        ("ui", logic::toml_quote(&logic::family_for_face(&s.font_ui.get()))),
        ("code", logic::toml_quote(&logic::family_for_face(&s.font_code.get()))),
        ("display", logic::toml_quote(&logic::family_for_face(&s.font_heading.get()))),
        ("heading", logic::toml_quote(&logic::family_for_face(&s.font_heading.get()))),
        ("reading", logic::toml_quote(&logic::family_for_face(&s.font_reading.get()))),
        ("accent", logic::toml_quote(&logic::family_for_face(&s.font_accent.get()))),
    ];
    let text = logic::upsert_keys(&text, "fonts", &fonts);
    let appearance = [
        ("theme", logic::toml_quote(&s.theme_id.get())),
        ("density", logic::toml_quote(&s.density.get())),
        ("font_scale", logic::toml_quote(&s.font_scale.get())),
        ("reduce_motion", bool_toml(s.reduce_motion.get())),
        ("high_contrast", bool_toml(s.high_contrast.get())),
        ("custom_accent", logic::toml_quote(&s.custom_accent.get())),
        ("settings_mode", logic::toml_quote(&s.mode.get())),
        ("last_section", logic::toml_quote(&s.last_section.get())),
        ("privacy_shield", bool_toml(s.privacy_shield.get())),
        ("skip_quit_warn", bool_toml(s.skip_quit_warn.get())),
    ];
    let text = logic::upsert_keys(&text, "appearance", &appearance);
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if write_if_changed(&path, &text).is_err() {
        s.banner.set("Could not write config.toml. The change still applies for this session.".into());
    }
}

fn write_if_changed(path: &PathBuf, text: &str) -> std::io::Result<()> {
    if fs::read_to_string(path).ok().as_deref() == Some(text) {
        return Ok(());
    }
    fs::write(path, text)
}

fn bool_toml(on: bool) -> String {
    if on { "true".into() } else { "false".into() }
}

fn parse_accent(raw: &str) -> Option<Color> {
    let t = raw.trim();
    if t.is_empty() {
        return None;
    }
    Color::from_hex(t).ok()
}

fn on_accent(c: Color) -> Color {
    let y = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
    if y > 0.62 { Color::BLACK } else { Color::WHITE }
}

pub fn set_theme(s: Settings, id: &str) {
    s.theme_id.set(id.to_string());
    apply(s);
    persist(s);
}

pub fn set_density(s: Settings, id: &str) {
    s.density.set(id.to_string());
    apply(s);
    persist(s);
}

pub fn set_scale(s: Settings, id: &str) {
    s.font_scale.set(id.to_string());
    apply(s);
    persist(s);
}

pub fn set_motion(s: Settings, on: bool) {
    s.reduce_motion.set(on);
    apply(s);
    persist(s);
}

pub fn set_contrast(s: Settings, on: bool) {
    s.high_contrast.set(on);
    apply(s);
    persist(s);
}

pub fn set_accent(s: Settings, hex: &str) {
    s.custom_accent.set(hex.trim().to_string());
    apply(s);
    persist(s);
}

pub fn set_font(s: Settings, role: &str, face: &str) {
    match role {
        "ui" => s.font_ui.set(face.to_string()),
        "heading" | "display" => s.font_heading.set(face.to_string()),
        "reading" => s.font_reading.set(face.to_string()),
        "accent" => s.font_accent.set(face.to_string()),
        "code" => s.font_code.set(face.to_string()),
        _ => return,
    }
    apply(s);
    persist(s);
}

pub fn reset_fonts(s: Settings) {
    s.font_ui.set("remedy-sans-ui".into());
    s.font_heading.set("ahmi-pixel".into());
    s.font_reading.set("ahmi-print".into());
    s.font_accent.set("ahmi-pixel".into());
    s.font_code.set("ahmi-print".into());
    apply(s);
    persist(s);
}

pub fn fonts_are_default(s: Settings) -> bool {
    s.font_ui.get() == "remedy-sans-ui"
        && s.font_heading.get() == "ahmi-pixel"
        && s.font_reading.get() == "ahmi-print"
        && s.font_accent.get() == "ahmi-pixel"
        && s.font_code.get() == "ahmi-print"
}
