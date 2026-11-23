//! UI and code fonts: bundled defaults, overridable in config.
//!
//! Defaults are Joe's faces: Remedy Sans UI for the interface, AhmiPrint for
//! code and monospace (same-width figures). Both ship inside RemedyUI
//! (`remedy-ui-fonts`). The person can pick other bundled or system families
//! by setting the `ui` and `code` keys under `[fonts]` in `config.toml`.

use remedy_ui::style::Theme;

/// Default interface family (the RemedySans UI typographic name).
pub const DEFAULT_UI_FONT: &str = "Remedy Sans UI";
/// Default code and monospace family.
pub const DEFAULT_CODE_FONT: &str = "AhmiPrint";
/// Display / heading family when a title is styled `heading`.
pub const DEFAULT_HEADING_FONT: &str = "Remedy Sans";

/// Chosen interface and code families.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fonts {
    /// Interface text (chat, menus, settings).
    pub ui: String,
    /// Code, diffs, logs, and other monospace.
    pub code: String,
}

impl Default for Fonts {
    fn default() -> Self {
        Fonts {
            ui: DEFAULT_UI_FONT.into(),
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

/// A small `[fonts]` reader: `ui` / `code` string keys, quoted or bare.
fn parse_fonts_toml(text: &str) -> Fonts {
    let mut out = Fonts::default();
    let mut in_fonts = false;
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
            _ => {}
        }
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

/// Applies the desktop type scale and the chosen families to `theme`.
///
/// Body is 15 px, with a title at 18 and small at 12, so chrome, chat and meta
/// can sit on a real scale instead of one size. The toolkit default is 13.
pub fn apply_to_theme(theme: &mut Theme, fonts: &Fonts) {
    theme.font_families = vec![fonts.ui.clone()];
    theme.mono_families = vec![fonts.code.clone(), fonts.ui.clone()];
    theme.heading_families = vec![DEFAULT_HEADING_FONT.into()];
    theme.type_scale.small = 12.0;
    theme.type_scale.body = 15.0;
    theme.type_scale.title = 18.0;
    theme.radii.small = 6.0;
    theme.radii.medium = 10.0;
    theme.radii.large = 14.0;
    theme.spacing.unit = 4.0;
    theme.spacing.control_height = 36.0;
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
        assert_eq!(f.code, "AhmiPrint");
    }

    #[test]
    fn reads_ui_and_code_keys() {
        let f = Fonts::from_toml(
            r#"
[advanced]
something = true

[fonts]
ui = "Inter"
code = "Cascadia Code"
"#,
        );
        assert_eq!(f.ui, "Inter");
        assert_eq!(f.code, "Cascadia Code");
    }

    #[test]
    fn missing_or_empty_keys_keep_defaults() {
        assert_eq!(Fonts::from_toml(""), Fonts::default());
        let f = Fonts::from_toml("[fonts]\nui = \"\"\ncode_font = 'Fira Code'\n");
        assert_eq!(f.ui, DEFAULT_UI_FONT);
        assert_eq!(f.code, "Fira Code");
    }
}
