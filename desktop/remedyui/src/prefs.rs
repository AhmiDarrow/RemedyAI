//! Desktop prefs (`~/.remedy/desktop.json`) and launch-at-login.
//!
//! `REMEDY_DESKTOP_NO_SPAWN` (tests, this shared box) never reads or writes
//! the real home directory. `close_to_tray` is always on, matching the
//! current app: the title-bar close hides the window.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::runtime::{self, spawn_disabled};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DesktopPrefs {
    #[serde(default = "yes")]
    pub close_to_tray: bool,
    #[serde(default)]
    pub start_in_tray: bool,
    #[serde(default)]
    pub skip_quit_server_warning: bool,
    #[serde(default = "auto_lang")]
    pub ui_language: String,
    #[serde(default = "simple")]
    pub ui_mode: String,
    #[serde(default = "font_md")]
    pub font_scale: String,
    #[serde(default)]
    pub launch_at_login: bool,
    /// The terminal's shell: an id from `slides::pty::shells`, a full path, or
    /// empty for the machine's default.
    #[serde(default)]
    pub terminal_shell: String,
}

fn yes() -> bool {
    true
}
fn auto_lang() -> String {
    "auto".into()
}
fn simple() -> String {
    "simple".into()
}
fn font_md() -> String {
    "md".into()
}

impl Default for DesktopPrefs {
    fn default() -> Self {
        Self {
            close_to_tray: true,
            start_in_tray: false,
            skip_quit_server_warning: false,
            ui_language: "auto".into(),
            ui_mode: "simple".into(),
            font_scale: "md".into(),
            launch_at_login: false,
            terminal_shell: String::new(),
        }
    }
}

impl DesktopPrefs {
    pub fn load() -> Self {
        if spawn_disabled() {
            return Self::default();
        }
        let path = prefs_path();
        let Ok(text) = std::fs::read_to_string(&path) else {
            return Self::default();
        };
        let mut prefs: Self = serde_json::from_str(&text).unwrap_or_default();
        prefs.close_to_tray = true;
        prefs
    }

    pub fn save(&self) {
        if spawn_disabled() {
            return;
        }
        let mut prefs = self.clone();
        prefs.close_to_tray = true;
        let path = prefs_path();
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(text) = serde_json::to_string_pretty(&prefs) {
            let _ = std::fs::write(path, text);
        }
    }
}

pub fn prefs_path() -> PathBuf {
    runtime::remedy_home().join("desktop.json")
}

pub fn font_index(scale: &str) -> u8 {
    match scale {
        "sm" => 0,
        "lg" => 2,
        "xl" => 3,
        _ => 1,
    }
}

pub fn font_name(index: u8) -> &'static str {
    match index {
        0 => "sm",
        2 => "lg",
        3 => "xl",
        _ => "md",
    }
}

/// Writes or removes the login entry. No-op while spawn is disabled.
pub fn set_launch_at_login(on: bool) {
    if spawn_disabled() {
        return;
    }
    apply_launch_at_login(on, &autostart_dir(), &std::env::current_exe().unwrap_or_default());
}

pub fn apply_launch_at_login(on: bool, dir: &Path, exe: &Path) {
    let _ = std::fs::create_dir_all(dir);
    let path = dir.join(entry_name());
    // Older names from early builds.
    for old in ["remedy.desktop", "Remedy.desktop", "remedy-desktop.desktop"] {
        let p = dir.join(old);
        if p != path {
            let _ = std::fs::remove_file(p);
        }
    }
    if !on {
        let _ = std::fs::remove_file(&path);
        return;
    }
    let exe = exe.display();
    let body = if cfg!(target_os = "macos") {
        format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.remedy.desktop</string>
<key>ProgramArguments</key><array><string>{exe}</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
"#
        )
    } else if cfg!(target_os = "windows") {
        format!("@echo off\r\nstart \"\" \"{exe}\"\r\n")
    } else {
        format!(
            "[Desktop Entry]\nType=Application\nVersion=1.0\nName=Remedy Desktop\nExec={exe}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n"
        )
    };
    let _ = std::fs::write(path, body);
}

fn entry_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "com.remedy.desktop.plist"
    } else if cfg!(target_os = "windows") {
        "Remedy Desktop.cmd"
    } else {
        "com.remedy.desktop.desktop"
    }
}

fn autostart_dir() -> PathBuf {
    if cfg!(target_os = "macos") {
        home_dir().join("Library/LaunchAgents")
    } else if cfg!(target_os = "windows") {
        std::env::var("APPDATA")
            .map(|p| PathBuf::from(p).join("Microsoft/Windows/Start Menu/Programs/Startup"))
            .unwrap_or_else(|_| PathBuf::from("."))
    } else if let Ok(xdg) = std::env::var("XDG_CONFIG_HOME") {
        if !xdg.is_empty() {
            return PathBuf::from(xdg).join("autostart");
        }
        home_dir().join(".config/autostart")
    } else {
        home_dir().join(".config/autostart")
    }
}

fn home_dir() -> PathBuf {
    std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}

/// Opens a folder in the file manager. Returns false when spawn is disabled
/// or nothing could be launched (the caller toasts the path).
pub fn reveal(path: &Path) -> bool {
    if spawn_disabled() {
        return false;
    }
    let _ = std::fs::create_dir_all(path);
    let (cmd, arg) = if cfg!(target_os = "macos") {
        ("open", path.display().to_string())
    } else if cfg!(target_os = "windows") {
        ("explorer", path.display().to_string())
    } else {
        ("xdg-open", path.display().to_string())
    };
    std::process::Command::new(cmd).arg(arg).spawn().is_ok()
}

pub fn logs_dir() -> PathBuf {
    runtime::remedy_home().join("logs")
}

pub fn data_dir() -> PathBuf {
    runtime::remedy_home()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn login_entry_is_a_desktop_file_and_clears() {
        let dir = std::env::temp_dir().join(format!("remedy-chrome-autostart-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let exe = PathBuf::from("/usr/bin/remedy-desktop");
        apply_launch_at_login(true, &dir, &exe);
        let path = dir.join(entry_name());
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("com.remedy.desktop") || text.contains("remedy-desktop"));
        assert!(text.contains("/usr/bin/remedy-desktop"));
        apply_launch_at_login(false, &dir, &exe);
        assert!(!path.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn font_scale_round_trips() {
        assert_eq!(font_name(font_index("lg")), "lg");
        assert_eq!(font_index("nope"), 1);
    }
}
