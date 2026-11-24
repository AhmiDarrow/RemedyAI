//! Three font roles: UI, display, and code. Defaults and config, never hardcoded at widgets.
//!
//! Defaults are Joe's faces: Remedy Sans UI for the interface, Ahmi Pixel for
//! display headings and section labels, AhmiPrint for code and monospace.
//! All three ship inside RemedyUI (`remedy-ui-fonts`). The person can pick
//! other bundled or system families by setting the `ui`, `display`, and `code`
//! keys under `[fonts]` in `config.toml`. Widgets go through the theme tokens
//! (`font_families`, `heading_families`, `mono_families`) so a swap still holds.

use remedy_ui::core::Color;
use remedy_ui::style::Theme;

/// Default interface family (the RemedySans UI typographic name).
pub const DEFAULT_UI_FONT: &str = "Remedy Sans UI";
/// Default code and monospace family.
pub const DEFAULT_CODE_FONT: &str = "AhmiPrint";
/// Default display / heading family (titles and section labels).
pub const DEFAULT_DISPLAY_FONT: &str = "Ahmi Pixel";
/// Older name for [`DEFAULT_DISPLAY_FONT`].
pub const DEFAULT_HEADING_FONT: &str = DEFAULT_DISPLAY_FONT;

/// Chosen interface, display, and code families.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fonts {
    /// Interface text (chat, menus, settings).
    pub ui: String,
    /// Display headings and section labels (theme `heading_families`).
    pub display: String,
    /// Code, diffs, logs, and other monospace.
    pub code: String,
}

impl Default for Fonts {
    fn default() -> Self {
        Fonts {
            ui: DEFAULT_UI_FONT.into(),
            display: DEFAULT_DISPLAY_FONT.into(),
            code: DEFAULT_CODE_FONT.into(),
        }
    }
}

impl Fonts {
    /// Reads `[fonts]` from `$REMEDY_HOME/config.toml`, or `~/.remedy/config.toml`.
    /// Missing file, section or keys keep the defaults. Empty values are ignored.
    pub fn load() -> Self {
        load_from_path(&config_path()).unwrap_or_default()
    }

    /// Parses a TOML document the same way [`Fonts::load`] reads the config file.
    pub fn from_toml(text: &str) -> Self {
        parse_fonts_toml(text)
    }
}

/// `$REMEDY_HOME/config.toml`, or `%USERPROFILE%\.remedy\config.toml` on Windows,
/// or `~/.remedy/config.toml` elsewhere. Same home the Tauri shell keeps across updates.
pub fn config_path() -> std::path::PathBuf {
    crate::runtime::remedy_home().join("config.toml")
}

fn load_from_path(path: &std::path::Path) -> Option<Fonts> {
    let text = std::fs::read_to_string(path).ok()?;
    Some(parse_fonts_toml(&text))
}

/// A small `[fonts]` reader: `ui` / `display` / `code` string keys, quoted or bare.
/// `heading` is accepted as an alias for `display`.
fn parse_fonts_toml(text: &str) -> Fonts {
    let mut out = Fonts::default();
    let mut in_fonts = false;
    let mut display = None;
    let mut heading = None;
    for raw in text.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with("//") {
            continue;
        }
        if let Some(rest) = line.strip_prefix('[') {
            let name = rest.trim_end_matches(']').trim();
            in_fonts = name == "fonts";
            continue;
        }
        if !in_fonts {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let key = k.trim();
        let val = unquote(v.trim());
        if val.is_empty() {
            continue;
        }
        match key {
            "ui" | "ui_font" | "font_ui" => out.ui = val,
            "code" | "code_font" | "font_code" => out.code = val,
            "display" | "display_font" | "font_display" => display = Some(val),
            "heading" | "heading_font" | "font_heading" => heading = Some(val),
            _ => {}
        }
    }
    if let Some(v) = display.or(heading) {
        out.display = v;
    }
    out
}

fn unquote(v: &str) -> String {
    let v = v.trim();
    let v = v
        .strip_prefix('"')
        .and_then(|s| s.strip_suffix('"'))
        .or_else(|| v.strip_prefix('\'').and_then(|s| s.strip_suffix('\'')))
        .unwrap_or(v);
    v.trim().to_string()
}

fn hex(s: &str) -> Color {
    Color::from_hex(s).unwrap_or(Color::BLACK)
}

fn is_dark_forest(theme: &Theme) -> bool {
    if !theme.derive().dark {
        return false;
    }
    let bg = theme.colors.bg.to_hex();
    matches!(bg.to_ascii_uppercase().as_str(), "#0A0E0B" | "#040A09")
}

/// Applies the desktop type scale, the three font roles, and (on Dark Forest)
/// Joe's site-inspired flat palette to `theme`.
///
/// Body is 15 px, with a display title at 16 (a multiple of 8, so Ahmi Pixel
/// stays crisp) and small at 12. Radii stay sharp. Widgets must use the theme
/// family tokens, not hard-coded family names, so a user swap still holds up.
pub fn apply_to_theme(theme: &mut Theme, fonts: &Fonts) {
    let pack = crate::theme_pack::current();
    theme.font_families = vec![fonts.ui.clone()];
    theme.mono_families = vec![fonts.code.clone(), fonts.ui.clone()];
    theme.heading_families = vec![fonts.display.clone(), fonts.ui.clone()];
    theme.type_scale.small = pack.typo.small;
    theme.type_scale.body = pack.typo.body;
    theme.type_scale.title = pack.typo.title;
    theme.radii.small = pack.radii.none;
    theme.radii.medium = pack.radii.hair;
    theme.radii.large = pack.radii.hair;
    theme.spacing.unit = pack.space.unit;
    theme.spacing.control_height = pack.space.control_height;
    if is_dark_forest(theme) {
        // Colors come from the Dark Forest theme package (Joe's site tokens).
        // Site glass / mist / hero gradients are not brought over.
        theme.colors.bg = hex(pack.colors.bg);
        theme.colors.panel = hex(pack.colors.panel);
        theme.colors.card = hex(pack.colors.card);
        theme.colors.line = Some(hex(pack.colors.line));
        theme.colors.text = hex(pack.colors.text);
        theme.colors.muted = hex(pack.colors.muted);
        theme.colors.accent = hex(pack.colors.accent);
        theme.colors.on_accent = hex(pack.colors.on_accent);
        theme.colors.ok = hex(pack.colors.ok);
    }
}

/// Installs [`apply_to_theme`] on this UI if the tokens are not already set.
pub fn prepare_theme(ui: &remedy_ui::Ui, fonts: &Fonts) {
    let mut theme = ui.theme();
    let before = theme.clone();
    apply_to_theme(&mut theme, fonts);
    if theme != before {
        let sheet = remedy_ui::themes::sheet(&theme);
        ui.set_theme(theme, sheet);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_joes_faces() {
        let f = Fonts::default();
        assert_eq!(f.ui, "Remedy Sans UI");
        assert_eq!(f.display, "Ahmi Pixel");
        assert_eq!(f.code, "AhmiPrint");
        assert_eq!(DEFAULT_HEADING_FONT, DEFAULT_DISPLAY_FONT);
    }

    #[test]
    fn reads_ui_display_and_code_keys() {
        let f = Fonts::from_toml(
            r#"
[advanced]
something = true

[fonts]
ui = "Inter"
display = "Playfair Display"
code = "Cascadia Code"
"#,
        );
        assert_eq!(f.ui, "Inter");
        assert_eq!(f.display, "Playfair Display");
        assert_eq!(f.code, "Cascadia Code");
    }

    #[test]
    fn heading_is_an_alias_for_display_but_display_wins() {
        let heading_only = Fonts::from_toml(
            r#"
[fonts]
heading = "Remedy Sans"
"#,
        );
        assert_eq!(heading_only.display, "Remedy Sans");
        let both = Fonts::from_toml(
            r#"
[fonts]
heading = "Remedy Sans"
display = "Ahmi Pixel"
"#,
        );
        assert_eq!(both.display, "Ahmi Pixel");
    }

    #[test]
    fn missing_or_empty_keys_keep_defaults() {
        assert_eq!(Fonts::from_toml(""), Fonts::default());
        let f = Fonts::from_toml("[fonts]\nui = \"\"\ncode_font = 'Fira Code'\n");
        assert_eq!(f.ui, DEFAULT_UI_FONT);
        assert_eq!(f.display, DEFAULT_DISPLAY_FONT);
        assert_eq!(f.code, "Fira Code");
    }

    #[test]
    fn apply_to_theme_uses_the_configured_roles() {
        let mut theme = Theme::dark_forest();
        apply_to_theme(
            &mut theme,
            &Fonts {
                ui: "Inter".into(),
                display: "Playfair Display".into(),
                code: "Fira Code".into(),
            },
        );
        assert_eq!(theme.font_families[0], "Inter");
        assert_eq!(theme.heading_families[0], "Playfair Display");
        assert_eq!(theme.mono_families[0], "Fira Code");
        assert_eq!(theme.colors.bg.to_hex().to_ascii_uppercase(), "#040A09");
        assert_eq!(theme.radii.small, 0.0);
    }
}
