//! Where browser files live. The Tauri host already uses `~/.remedy`.
//!
//! Tests set `REMEDY_DESKTOP_NO_SPAWN` and must not write the owner's real profile.
//! `REMEDY_HOME` wins when a caller points the app at another folder.

use std::path::PathBuf;

/// Profile root: `REMEDY_HOME`, else a temp dir while spawn is disabled, else
/// `%USERPROFILE%\.remedy` on Windows and `$HOME/.remedy` elsewhere.
pub fn remedy_home() -> PathBuf {
    if let Ok(h) = std::env::var("REMEDY_HOME") {
        let p = PathBuf::from(h.trim());
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    // Unit tests compile this crate with cfg(test). Integration tests do not;
    // they set REMEDY_DESKTOP_NO_SPAWN instead. Either way, stay out of the
    // real profile.
    if cfg!(test) || spawn_disabled() {
        return std::env::temp_dir().join("remedy-desktop-browser-test");
    }
    let base = if cfg!(windows) {
        std::env::var("USERPROFILE").unwrap_or_else(|_| ".".into())
    } else {
        std::env::var("HOME").unwrap_or_else(|_| ".".into())
    };
    PathBuf::from(base).join(".remedy")
}

pub fn spawn_disabled() -> bool {
    matches!(
        std::env::var("REMEDY_DESKTOP_NO_SPAWN")
            .unwrap_or_default()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// `~/.remedy/browser_rail.json` — same file the Tauri host reads.
pub fn rail_prefs_path() -> PathBuf {
    remedy_home().join("browser_rail.json")
}

/// `~/.remedy/privacy-shield/privacy_shield.json`.
pub fn shield_prefs_path() -> PathBuf {
    remedy_home().join("privacy-shield").join("privacy_shield.json")
}

/// Bookmarks were in the Tauri web view's localStorage. The native shell keeps
/// the same records under the profile so they survive without that web view.
pub fn bookmarks_path() -> PathBuf {
    remedy_home().join("desktop").join("browser-bookmarks.json")
}

/// Workspace rails were in localStorage (`remedy.workspaceLayout.v3`).
pub fn layout_path() -> PathBuf {
    remedy_home().join("desktop").join("workspace-layout.json")
}

/// WebView2 / WebKit profile (cookies, cache). Not the Tauri window profile.
pub fn webview_data_dir() -> PathBuf {
    remedy_home().join("browser-profile")
}

pub fn write_json(path: &std::path::Path, value: &impl serde::Serialize) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(raw) = serde_json::to_string_pretty(value) {
        let _ = std::fs::write(path, raw);
    }
}

pub fn read_to_string(path: &std::path::Path) -> Option<String> {
    std::fs::read_to_string(path).ok()
}
