//! Rail user agents. The major version tracks the installed engine when
//! DevTools can report it. A frozen "Chrome/131" on a newer runtime disagrees
//! with client hints, and retail bot walls key on that mismatch.

use serde::{Deserialize, Serialize};
use std::sync::Mutex;

use super::paths;

const UA_FALLBACK_MAJOR: &str = "131";

static MAJOR: Mutex<String> = Mutex::new(String::new());

pub fn engine_major() -> String {
    let guard = MAJOR.lock().unwrap_or_else(|e| e.into_inner());
    if guard.is_empty() {
        UA_FALLBACK_MAJOR.to_string()
    } else {
        guard.clone()
    }
}

/// Accept a Chrome/Edge major from `Browser.getVersion`. WebKit's "2" is not
/// a Chrome major and would make "Chrome/2.0.0.0", which bot walls reject.
pub fn set_engine_major(raw: &str) {
    let m = raw.split(['.', ' ']).next().unwrap_or("").trim();
    if m.is_empty() || !m.chars().all(|c| c.is_ascii_digit()) {
        return;
    }
    match m.parse::<u32>() {
        Ok(n) if n >= 80 => {
            *MAJOR.lock().unwrap_or_else(|e| e.into_inner()) = m.to_string();
        }
        _ => {}
    }
}

pub fn ua_mobile() -> String {
    let m = engine_major();
    format!(
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Mobile Safari/537.36"
    )
}

pub fn ua_desktop() -> String {
    let m = engine_major();
    if cfg!(target_os = "linux") {
        format!(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Safari/537.36"
        )
    } else {
        format!(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Safari/537.36 Edg/{m}.0.0.0"
        )
    }
}

pub fn rail_user_agent(desktop: bool) -> String {
    if desktop { ua_desktop() } else { ua_mobile() }
}

/// `Emulation.setUserAgentOverride` so client hints agree with the UA header.
pub fn ua_override_params(desktop: bool) -> String {
    let m = engine_major();
    let ua = rail_user_agent(desktop);
    let brand = if desktop { "Microsoft Edge" } else { "Google Chrome" };
    let brands = serde_json::json!([
        {"brand": "Chromium", "version": m},
        {"brand": brand, "version": m},
        {"brand": "Not.A/Brand", "version": "99"},
    ]);
    let meta = if desktop {
        serde_json::json!({
            "brands": brands,
            "fullVersionList": brands,
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": false,
            "bitness": "64",
            "wow64": false,
        })
    } else {
        serde_json::json!({
            "brands": brands,
            "fullVersionList": brands,
            "platform": "Android",
            "platformVersion": "14.0.0",
            "architecture": "",
            "model": "Pixel 8",
            "mobile": true,
            "bitness": "",
            "wow64": false,
        })
    };
    serde_json::json!({
        "userAgent": ua,
        "acceptLanguage": "en-US,en;q=0.9",
        "platform": if desktop { "Win32" } else { "Linux armv81" },
        "userAgentMetadata": meta,
    })
    .to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct RailPrefs {
    /// Desktop UA. Mobile view is one click away for a narrow rail.
    pub desktop_site: bool,
}

impl Default for RailPrefs {
    fn default() -> Self {
        Self { desktop_site: true }
    }
}

pub fn load_rail_prefs() -> RailPrefs {
    paths::read_to_string(&paths::rail_prefs_path())
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

pub fn save_rail_prefs(prefs: &RailPrefs) {
    paths::write_json(&paths::rail_prefs_path(), prefs);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fallback_major_is_a_real_chrome_generation() {
        let ua = ua_mobile();
        assert!(ua.contains("Chrome/"), "{ua}");
        assert!(!ua.contains("Chrome/2."), "{ua}");
        assert!(ua.contains("Mobile"));
        assert!(!ua_desktop().contains("Mobile"));
    }

    #[test]
    fn webkit_major_is_ignored() {
        set_engine_major("2.44.0");
        assert_eq!(engine_major(), "131");
        set_engine_major("131.0.2903.86");
        assert_eq!(engine_major(), "131");
        set_engine_major("120");
        assert_eq!(engine_major(), "120");
        set_engine_major("131");
    }
}
