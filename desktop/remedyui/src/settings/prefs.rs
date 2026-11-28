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
use remedy_ui::themes::sheet;
use remedy_ui::{App, Ui};

use crate::fonts::{self, Fonts};
use crate::settings::logic;

use super::state::Settings;

thread_local! {
    static UI: RefCell<Option<Ui>> = RefCell::new(None);
    static HOST: RefCell<Option<App>> = RefCell::new(None);
    /// The app's other windows (a popped-out pane), each `None` once closed.
    static EXTRA: RefCell<Vec<Box<dyn Fn() -> Option<Ui>>>> = RefCell::new(Vec::new());
    static LOOK: RefCell<Option<remedy_ui::reactive::Signal<u64>>> = const { RefCell::new(None) };
    /// The windows Settings has given their look.
    static PAINTED: RefCell<Vec<remedy_ui::view::WeakUi>> = const { RefCell::new(Vec::new()) };
}

/// True once Settings has given `ui` its look (theme, fonts, text size). From then on that
/// window's look is Settings' alone: a surface being built must not reset it, or the reset
/// and Settings' answer to it rebuild each other without end.
pub(crate) fn look_applied(ui: &Ui) -> bool {
    PAINTED.with(|p| p.borrow().iter().filter_map(|w| w.upgrade()).any(|u| u.is_same(ui)))
}

/// Counts the changes of the look (theme, accent, density, text size). A surface copies
/// colours and sizes out of the theme while it is built, so it has to be built again
/// when this moves: [`with_look`].
fn look() -> remedy_ui::reactive::Signal<u64> {
    LOOK.with(|slot| {
        if let Some(existing) = slot.borrow().as_ref().copied() {
            if existing.is_alive() {
                return existing;
            }
        }
        // Detached: it must outlive whatever binding first asks for it.
        let (created, _scope) =
            remedy_ui::reactive::Scope::detached(|| remedy_ui::reactive::Signal::new(0u64));
        *slot.borrow_mut() = Some(created);
        created
    })
}

/// `build`'s surface, built again whenever the look changes, so a theme picked in
/// Settings reaches every colour on it at once. What the surface shows must live in
/// state outside it. `fill` gives it the whole height of its parent.
pub(crate) fn with_look(ui: &Ui, fill: bool, build: impl Fn(&Ui) -> remedy_ui::El + 'static) -> remedy_ui::El {
    let host = super::bits::rebuild_on(
        ui,
        || {
            let _ = look().get();
        },
        build,
    );
    if !fill {
        return host;
    }
    host.layout(|l| {
        l.height = remedy_ui::layout::Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = remedy_ui::layout::Length::Px(0.0);
        l.min_height = remedy_ui::layout::Length::Px(0.0);
    })
}

/// Another window of the app wears the main window's look, now and after every change.
pub fn bind_window(ui: &Ui) {
    let weak = ui.downgrade();
    EXTRA.with(|e| e.borrow_mut().push(Box::new(move || weak.upgrade())));
    if let Some(main) = UI.with(|c| c.borrow().clone()) {
        if look_applied(&main) {
            mark_painted(ui);
        }
        let theme = main.theme();
        let sheet = sheet(&theme);
        ui.set_theme(theme, sheet);
    }
}

fn mark_painted(ui: &Ui) {
    PAINTED.with(|p| {
        let mut p = p.borrow_mut();
        p.retain(|w| w.upgrade().is_some());
        if !p.iter().filter_map(|w| w.upgrade()).any(|u| u.is_same(ui)) {
            p.push(ui.downgrade());
        }
    });
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
    let mut imported = false;
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
    } else if !s.preview {
        if let Some(v) = crate::theme_import::from_webview() {
            s.theme_id.set(v);
            imported = true;
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "density") {
        if v == "compact" || v == "cozy" {
            s.density.set(v);
        }
    }
    if let Some(v) = logic::table_string(&text, "appearance", "corners") {
        if v == "square" || v == "rounded" {
            s.corners.set(v);
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
    if let Some(v) = logic::table_bool(&text, "appearance", "show_presence") {
        s.show_presence.set(v);
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
    if imported {
        persist(s);
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
    let high = s.high_contrast.get() || s.system_hc.get();
    let mut theme = crate::theme_pack::theme_for_id(&s.theme_id.get(), s.system_dark.get(), high);
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
    let rounded = s.corners.get() == "rounded";
    if rounded {
        theme.radii.small = 6.0;
        theme.radii.medium = 10.0;
        theme.radii.large = 14.0;
    }
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
    EXTRA.with(|e| {
        e.borrow_mut().retain(|window| {
            let Some(other) = window() else { return false };
            mark_painted(&other);
            other.set_theme(theme.clone(), sheet(&theme));
            true
        })
    });
    paint(&ui, theme);
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
        ("corners", logic::toml_quote(&s.corners.get())),
        ("font_scale", logic::toml_quote(&s.font_scale.get())),
        ("reduce_motion", bool_toml(s.reduce_motion.get())),
        ("show_presence", bool_toml(s.show_presence.get())),
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

/// Gives the window `theme`, and has the surfaces that copied the old one built again.
fn paint(ui: &Ui, theme: remedy_ui::core::style::Theme) {
    mark_painted(ui);
    crate::tokens::adopt(&theme);
    let changed = ui.theme() != theme;
    let sheet = sheet(&theme);
    ui.set_theme(theme, sheet);
    if changed {
        look().update(|n| *n = n.wrapping_add(1));
    }
}

fn on_accent(c: Color) -> Color {
    crate::tokens::on_color(c)
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

/// Show Remedy's face, or the R mark in its place.
pub fn set_presence(s: Settings, on: bool) {
    s.show_presence.set(on);
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

#[cfg(test)]
mod look_tests {
    use super::*;
    use remedy_ui::testing::TestApp;
    use remedy_ui::widgets as w;
    use std::rc::Rc;

    /// A larger text size, then a surface that prepares the house theme while it is built
    /// (Grove's frame does). Preparing must not undo the size: when it did, Settings
    /// painted it again, which rebuilt the surface, without end ("Starting Remedy" hung).
    #[test]
    fn a_surface_built_again_keeps_the_chosen_text_size() {
        let built: Rc<RefCell<usize>> = Rc::default();
        let mut app = TestApp::window({
            let built = built.clone();
            move |ui, _| {
                bind_ui(ui);
                let built = built.clone();
                let surface = with_look(ui, false, move |ui| {
                    *built.borrow_mut() += 1;
                    assert!(*built.borrow() < 20, "the look and the surface rebuild each other");
                    crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
                    w::text(ui, "surface")
                });
                ui.root().child(surface);
            }
        });
        app.frames(2);
        let ui = app.ui().expect("window");
        let mut large = ui.theme();
        crate::fonts::apply_to_theme(&mut large, &crate::fonts::Fonts::load());
        large.type_scale.body *= 1.15;
        paint(&ui, large.clone());
        app.frames(3);
        assert_eq!(ui.theme().type_scale.body, large.type_scale.body, "the text size stays");
        assert!(*built.borrow() <= 3, "built {} times", built.borrow());
    }

    /// A surface copies colours while it is built. Under `with_look` a theme change
    /// builds it again, so the copy is the new theme's; the same theme again does not.
    #[test]
    fn a_theme_change_builds_the_surface_again() {
        let built: Rc<RefCell<Vec<Color>>> = Rc::default();
        let mut app = TestApp::window({
            let built = built.clone();
            move |ui, _| {
                let built = built.clone();
                let surface = with_look(ui, false, move |ui| {
                    built.borrow_mut().push(ui.theme().colors.bg);
                    w::text(ui, "surface")
                });
                ui.root().child(surface);
            }
        });
        app.frames(2);
        let ui = app.ui().expect("window");
        let before = built.borrow().len();
        let light = crate::theme_pack::theme_for_id("light", None, false);
        let bg = light.colors.bg;
        assert_ne!(ui.theme().colors.bg, bg, "the test needs a theme that differs");
        paint(&ui, light.clone());
        app.frames(2);
        assert_eq!(built.borrow().len(), before + 1, "built again once");
        assert_eq!(built.borrow().last().copied(), Some(bg));
        paint(&ui, light);
        app.frames(2);
        assert_eq!(built.borrow().len(), before + 1, "the same theme builds nothing");
    }
}
