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
    theme_from_storage(&saved_storage(dir)?)
}

fn saved_storage(dir: &std::path::Path) -> Option<LocalStorage> {
    let store = match ChromiumStorage::read_dir(dir) {
        Ok(store) => store,
        Err(WebStorageError::NotFound { .. }) => return None,
        Err(_) => return None,
    };
    match store.first_of(&TAURI_WINDOWS_ORIGINS) {
        Ok(Some(saved)) => Some(saved),
        _ => None,
    }
}

/// The localStorage key the old web shell kept pins, tags and archive flags under.
pub const SESSION_META_KEY: &str = "remedy.sessionMeta.v1";

/// How the old web shell had one session filed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OldFiling {
    pub id: String,
    pub pinned: bool,
    pub archived: bool,
    pub tags: Vec<String>,
}

/// The filing in the old shell's saved JSON (`{ "<session id>": { pinned, archived, tags } }`).
/// Entries that file nothing are left out; anything unreadable gives an empty list.
pub fn filing_from_json(raw: &str) -> Vec<OldFiling> {
    let Ok(serde_json::Value::Object(map)) = serde_json::from_str::<serde_json::Value>(raw) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (id, v) in map {
        let flag = |k: &str| v.get(k).and_then(|x| x.as_bool()).unwrap_or(false);
        let tags: Vec<String> = v
            .get("tags")
            .and_then(|t| t.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(str::trim).filter(|s| !s.is_empty()).map(String::from)).collect())
            .unwrap_or_default();
        let (pinned, archived) = (flag("pinned"), flag("archived"));
        if !id.trim().is_empty() && (pinned || archived || !tags.is_empty()) {
            out.push(OldFiling { id, pinned, archived, tags });
        }
    }
    out
}

/// Pins, tags and archive flags from the old webview store, if it is present.
pub fn filing_from_webview() -> Vec<OldFiling> {
    let Some(dir) = tauri_windows_dir(BUNDLE_ID) else { return Vec::new() };
    let Some(saved) = saved_storage(&dir) else { return Vec::new() };
    saved.get(SESSION_META_KEY).map(filing_from_json).unwrap_or_default()
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
    fn old_filing_is_read_and_empty_entries_are_dropped() {
        let raw = r#"{"a":{"pinned":true,"tags":[" ui ",""]},"b":{},"c":{"archived":true},"d":{"tags":[]}}"#;
        let mut got = filing_from_json(raw);
        got.sort_by(|x, y| x.id.cmp(&y.id));
        assert_eq!(
            got,
            vec![
                OldFiling { id: "a".into(), pinned: true, archived: false, tags: vec!["ui".into()] },
                OldFiling { id: "c".into(), pinned: false, archived: true, tags: vec![] },
            ]
        );
        assert!(filing_from_json("not json").is_empty());
        assert!(filing_from_json("[1,2]").is_empty());
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
