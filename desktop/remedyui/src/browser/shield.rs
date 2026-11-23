//! Privacy Shield switch shared with the Tauri host.
//!
//! The EasyList engine still lives in `desktop/src-tauri/src/privacy_shield.rs`.
//! This module reads and writes the same prefs file. With no engine loaded,
//! `should_block_navigation` stays false — that is also what the Tauri host
//! does before the lists are ready. Identity-provider hops are never blocked.

use serde::{Deserialize, Serialize};

use super::oauth::is_identity_provider_url;
use super::paths;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ShieldPrefs {
    pub enabled: bool,
    pub lists_updated_at: u64,
}

impl Default for ShieldPrefs {
    fn default() -> Self {
        Self { enabled: true, lists_updated_at: 0 }
    }
}

pub fn load_shield() -> ShieldPrefs {
    paths::read_to_string(&paths::shield_prefs_path())
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

pub fn save_shield(prefs: &ShieldPrefs) {
    paths::write_json(&paths::shield_prefs_path(), prefs);
}

pub fn set_enabled(on: bool) -> ShieldPrefs {
    let mut prefs = load_shield();
    prefs.enabled = on;
    save_shield(&prefs);
    prefs
}

/// Document navigations the rail should cancel. Without the EasyList engine
/// this only refuses nothing, matching a shield that is still warming up.
/// The caller still applies the OAuth rewrite before this check.
pub fn should_block_navigation(url: &str, enabled: bool) -> bool {
    if !enabled {
        return false;
    }
    if url.starts_with("about:") || url.starts_with("data:") || url.starts_with("blob:") {
        return false;
    }
    if is_identity_provider_url(url) {
        return false;
    }
    // The list engine is not in this process. Do not invent a blocklist.
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_enabled_shield_without_lists_does_not_invent_blocks() {
        assert!(!should_block_navigation("https://example.com/ad", true));
        assert!(!should_block_navigation("https://accounts.example/oauth/authorize", true));
        assert!(!should_block_navigation("https://example.com/", false));
    }
}
