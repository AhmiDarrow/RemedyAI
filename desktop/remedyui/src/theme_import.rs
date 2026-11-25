//! Import the theme the old Tauri webview saved in localStorage.

use remedy_ui::webstorage::{
    ChromiumStorage, LocalStorage, TAURI_WINDOWS_ORIGINS, WebStorageError, tauri_windows_dir,
};

/// Bundle id of the Tauri desktop (`identifier` in `tauri.conf.json`).
pub const BUNDLE_ID: &str = "com.remedy.desktop";

/// The localStorage key `useTheme.ts` used.
pub const STORAGE_KEY: &str = "remedy-theme";

/// Theme ids the web shell stores, including System.
pub fn is_theme_id(id: &str) -> bool {
    matches!(
        id,
        "system"
            | "dark"
            | "neutral"
            | "light"
            | "forest"
            | "green"
            | "purple"
            | "alien"
            | "mecha"
            | "orange"
            | "cyan"
            | "deutan"
            | "protan"
            | "tritan"
    )
}

/// True when `config.toml` has not already recorded an appearance theme.
pub fn should_import(config_toml: &str) -> bool {
    match crate::settings::logic::table_string(config_toml, "appearance", "theme") {
        Some(v) => v.trim().is_empty(),
        None => true,
    }
}

/// The saved theme from a decoded localStorage, if it is a known id.
pub fn theme_from_storage(saved: &LocalStorage) -> Option<String> {
    let id = saved.get(STORAGE_KEY)?.trim();
    if is_theme_id(id) {
        Some(id.to_string())
    } else {
        None
    }
}

/// Read the old webview store, if it is present. Never writes to it.
pub fn from_webview() -> Option<String> {
    let dir = tauri_windows_dir(BUNDLE_ID)?;
    from_leveldb(&dir)
}

fn from_leveldb(dir: &std::path::Path) -> Option<String> {
    let store = match ChromiumStorage::read_dir(dir) {
        Ok(store) => store,
        Err(WebStorageError::NotFound { .. }) => return None,
        Err(_) => return None,
    };
    let saved = match store.first_of(&TAURI_WINDOWS_ORIGINS) {
        Ok(Some(saved)) => saved,
        _ => return None,
    };
    theme_from_storage(&saved)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_ids_match_the_webview() {
        for id in [
            "system", "dark", "neutral", "light", "forest", "green", "purple", "alien", "mecha", "orange",
            "cyan", "deutan", "protan", "tritan",
        ] {
            assert!(is_theme_id(id), "{id}");
        }
        assert!(!is_theme_id(""));
        assert!(!is_theme_id("Dark Forest"));
        assert!(!is_theme_id("system "));
    }

    #[test]
    fn import_only_when_config_has_no_theme() {
        assert!(should_import(""));
        assert!(should_import("[appearance]\ndensity = \"cozy\"\n"));
        assert!(should_import("[appearance]\ntheme = \"\"\n"));
        assert!(!should_import("[appearance]\ntheme = \"forest\"\n"));
        assert!(!should_import("[appearance]\ntheme = \"light\"\n"));
    }
}
