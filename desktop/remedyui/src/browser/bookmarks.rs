//! Local browser bookmarks. The Tauri slide stored these in localStorage
//! under `remedy.browserBookmarks.v1`. The same records live in the profile.

use serde::{Deserialize, Serialize};

use super::paths;
use super::url::{normalize_browser_url, DEFAULT_BROWSER_HOME};

const MAX: usize = 80;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Bookmark {
    pub id: String,
    pub title: String,
    pub url: String,
    #[serde(rename = "createdAt")]
    pub created_at: i64,
}

pub fn title_from_url(url: &str) -> String {
    if let Ok(u) = url::Url::parse(url) {
        let host = u.host_str().unwrap_or("").trim_start_matches("www.");
        let path = u.path();
        if path.is_empty() || path == "/" {
            return host.to_string();
        }
        return format!("{host}{path}");
    }
    url.chars().take(60).collect()
}

fn sanitize(raw: &str) -> Vec<Bookmark> {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(raw) else {
        return Vec::new();
    };
    let Some(items) = v.as_array() else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for item in items {
        let Some(o) = item.as_object() else { continue };
        let url = normalize_browser_url(o.get("url").and_then(|u| u.as_str()).unwrap_or(""));
        if url.is_empty() || url.starts_with("about:") {
            continue;
        }
        let id = o.get("id").and_then(|s| s.as_str()).filter(|s| !s.is_empty()).map(|s| s.to_string()).unwrap_or_else(|| format!("bm_{}", out.len()));
        let title = o
            .get("title")
            .and_then(|s| s.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.chars().take(120).collect())
            .unwrap_or_else(|| title_from_url(&url));
        let created_at = o.get("createdAt").and_then(|n| n.as_i64()).unwrap_or(0);
        out.push(Bookmark { id, title, url, created_at });
        if out.len() >= MAX {
            break;
        }
    }
    out
}

pub fn load_bookmarks() -> Vec<Bookmark> {
    paths::read_to_string(&paths::bookmarks_path()).map(|raw| sanitize(&raw)).unwrap_or_default()
}

pub fn save_bookmarks(list: &[Bookmark]) {
    let kept: Vec<&Bookmark> = list.iter().take(MAX).collect();
    paths::write_json(&paths::bookmarks_path(), &kept);
}

pub fn is_bookmarked(url: &str, list: &[Bookmark]) -> bool {
    let u = normalize_browser_url(url);
    if u.is_empty() {
        return false;
    }
    list.iter().any(|b| b.url == u)
}

pub fn toggle_bookmark(url: &str, title: Option<&str>, list: &[Bookmark]) -> (Vec<Bookmark>, bool) {
    let u = normalize_browser_url(url);
    if u.is_empty() {
        return (list.to_vec(), false);
    }
    if is_bookmarked(&u, list) {
        let next = list.iter().filter(|b| b.url != u && b.id != url).cloned().collect();
        return (next, false);
    }
    let next_item = Bookmark {
        id: format!("bm_{}", list.len() + 1),
        title: title.map(str::trim).filter(|s| !s.is_empty()).map(|s| s.chars().take(120).collect()).unwrap_or_else(|| title_from_url(&u)),
        url: u,
        created_at: 0,
    };
    let mut out = vec![next_item];
    out.extend(list.iter().filter(|b| b.url != out[0].url).cloned());
    out.truncate(MAX);
    (out, true)
}

/// Seed used when the profile has no bookmarks yet: the Remedy home page.
pub fn default_home_bookmark() -> Bookmark {
    Bookmark {
        id: "bm_home".into(),
        title: title_from_url(DEFAULT_BROWSER_HOME),
        url: DEFAULT_BROWSER_HOME.into(),
        created_at: 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn toggle_adds_then_removes_and_drops_bad_urls() {
        let (list, added) = toggle_bookmark("https://example.com/docs", Some("Docs"), &[]);
        assert!(added);
        assert_eq!(list.len(), 1);
        assert!(is_bookmarked("https://example.com/docs", &list));
        let (list, added) = toggle_bookmark("https://example.com/docs", None, &list);
        assert!(!added);
        assert!(list.is_empty());
        let (list, added) = toggle_bookmark("not a url at all", None, &[]);
        assert!(!added);
        assert!(list.is_empty());
    }

    #[test]
    fn parse_skips_about_and_caps() {
        let raw = r#"[{"id":"a","title":"Hi","url":"https://example.com/a","createdAt":1},{"id":"b","url":"about:blank"}]"#;
        let list = sanitize(raw);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].title, "Hi");
    }
}
