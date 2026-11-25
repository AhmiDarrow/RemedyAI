//! Desktop update check against the GitHub `latest.json` feed.
//!
//! Signed install and relaunch stay with the packaging work (PR #76). This
//! module only reads the feed the current app already publishes and opens
//! the platform installer URL.

use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::Cancel;

pub const FEED: &str =
    "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Feed {
    pub version: String,
    pub notes: String,
    pub url: String,
}

/// `latest` is newer than `current` (semver major.minor.patch, `v` prefix ignored).
pub fn is_newer(latest: &str, current: &str) -> bool {
    let a = parse_semver(latest);
    let b = parse_semver(current);
    for i in 0..3 {
        if a[i] > b[i] {
            return true;
        }
        if a[i] < b[i] {
            return false;
        }
    }
    false
}

pub fn parse_semver(raw: &str) -> [u64; 3] {
    let s = raw.trim().trim_start_matches(['v', 'V']);
    let s = s.split(['-', '+']).next().unwrap_or("");
    let mut out = [0u64; 3];
    for (i, part) in s.split('.').take(3).enumerate() {
        let digits: String = part.chars().take_while(|c| c.is_ascii_digit()).collect();
        out[i] = digits.parse().unwrap_or(0);
    }
    out
}

pub fn platform_key() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows-x86_64"
    } else if cfg!(target_os = "macos") {
        if cfg!(target_arch = "aarch64") {
            "darwin-aarch64"
        } else {
            "darwin-x86_64"
        }
    } else {
        "linux-x86_64"
    }
}

pub fn parse_feed(v: &serde_json::Value) -> Option<Feed> {
    let version = v.get("version")?.as_str()?.to_string();
    let notes = v.get("notes").and_then(|n| n.as_str()).unwrap_or("").to_string();
    let url = v
        .pointer(&format!("/platforms/{}/url", platform_key()))
        .and_then(|u| u.as_str())
        .unwrap_or("")
        .to_string();
    Some(Feed { version, notes, url })
}

/// Reads the feed on a worker thread. `done` runs on that thread.
pub fn fetch(done: impl FnOnce(Result<Feed, String>) + Send + 'static) {
    std::thread::Builder::new()
        .name("remedy-update".into())
        .spawn(move || {
            let http = Http::new("https://github.com");
            let cancel = Cancel::new();
            let out = http
                .send_blocking(
                    Request::get(FEED)
                        .header("User-Agent", concat!("RemedyDesktop-Updater/", env!("CARGO_PKG_VERSION")))
                        .header("Accept", "application/json")
                        .header("Cache-Control", "no-cache"),
                    &cancel,
                )
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()))
                .and_then(|v| parse_feed(&v).ok_or_else(|| "the feed has no version".into()));
            done(out);
        })
        .ok();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_oh_is_newer_than_the_published_feed() {
        assert!(!is_newer("v0.70.5", "1.0.0"));
        assert!(is_newer("v1.1.0", "1.0.0"));
        assert!(!is_newer("1.0.0", "1.0.0"));
        assert_eq!(parse_semver("v0.70.5-beta"), [0, 70, 5]);
    }

    #[test]
    fn feed_json_picks_this_platform() {
        let v = serde_json::json!({
            "version": "v9.0.0",
            "notes": "n",
            "platforms": { "linux-x86_64": { "url": "https://example.com/a" } }
        });
        let feed = parse_feed(&v).unwrap();
        assert_eq!(feed.version, "v9.0.0");
        if cfg!(target_os = "linux") {
            assert_eq!(feed.url, "https://example.com/a");
        }
    }
}
