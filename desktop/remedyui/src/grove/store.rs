//! Grove preferences under `~/.remedy/ui/grove.json`.
//!
//! The same facts the current app keeps in localStorage: which surface is
//! showing, the home and goal sessions, the split positions, and the bubble.
//! Other files in `~/.remedy` are left alone.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::logic::BubblePos;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct Bookmark {
    pub title: String,
    pub url: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct Prefs {
    #[serde(default = "grove_surface")]
    pub surface: String,
    #[serde(default)]
    pub home_session: String,
    #[serde(default)]
    pub goal_sessions: BTreeMap<String, String>,
    #[serde(default = "home_split")]
    pub home_split: f32,
    #[serde(default = "room_split")]
    pub room_split: f32,
    #[serde(default)]
    pub bubble_x: Option<f32>,
    #[serde(default)]
    pub bubble_y: Option<f32>,
    #[serde(default)]
    pub bookmarks: Vec<Bookmark>,
    #[serde(default)]
    pub shield: bool,
    #[serde(default)]
    pub desktop_site: bool,
    #[serde(default = "stage_home")]
    pub stage_url: String,
}

fn grove_surface() -> String {
    "grove".into()
}

fn home_split() -> f32 {
    0.44
}

fn room_split() -> f32 {
    0.6
}

fn stage_home() -> String {
    super::logic::BROWSER_HOME.into()
}

impl Default for Prefs {
    fn default() -> Self {
        Prefs {
            surface: grove_surface(),
            home_session: String::new(),
            goal_sessions: BTreeMap::new(),
            home_split: home_split(),
            room_split: room_split(),
            bubble_x: None,
            bubble_y: None,
            bookmarks: Vec::new(),
            shield: true,
            desktop_site: false,
            stage_url: stage_home(),
        }
    }
}

impl Prefs {
    pub(crate) fn bubble(&self) -> Option<BubblePos> {
        Some(BubblePos {
            x: self.bubble_x?,
            y: self.bubble_y?,
        })
    }
}

/// `REMEDY_HOME`, else `%USERPROFILE%\.remedy`, else `$HOME/.remedy`.
pub(crate) fn remedy_home() -> PathBuf {
    if let Ok(h) = std::env::var("REMEDY_HOME") {
        let p = PathBuf::from(h);
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    if let Ok(h) = std::env::var("USERPROFILE") {
        let p = PathBuf::from(&h);
        if !h.is_empty() {
            return p.join(".remedy");
        }
    }
    if let Ok(h) = std::env::var("HOME") {
        let p = PathBuf::from(&h);
        if !h.is_empty() {
            return p.join(".remedy");
        }
    }
    PathBuf::from(".remedy")
}

fn prefs_path() -> PathBuf {
    remedy_home().join("ui").join("grove.json")
}

pub(crate) fn load() -> Prefs {
    let path = prefs_path();
    let Ok(raw) = fs::read_to_string(&path) else {
        return Prefs::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

/// Add or remove one bookmark. `true` when the list changed.
pub(crate) fn set_star(list: &mut Vec<Bookmark>, url: &str, on: bool) -> bool {
    let has = list.iter().any(|b| b.url == url);
    if on && !has {
        list.push(Bookmark {
            title: url.to_string(),
            url: url.to_string(),
        });
        true
    } else if !on && has {
        list.retain(|b| b.url != url);
        true
    } else {
        false
    }
}

/// Writes Grove's own file. A failure leaves the previous file in place.
pub(crate) fn save(prefs: &Prefs) {
    let path = prefs_path();
    let Some(dir) = path.parent() else {
        return;
    };
    if fs::create_dir_all(dir).is_err() {
        return;
    }
    let Ok(body) = serde_json::to_vec_pretty(prefs) else {
        return;
    };
    let tmp = path.with_extension("json.tmp");
    if fs::write(&tmp, body).is_ok() {
        let _ = fs::rename(&tmp, &path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_star_adds_then_removes_one_url() {
        let mut list = Vec::new();
        assert!(set_star(&mut list, "https://example.com/docs", true));
        assert!(!set_star(&mut list, "https://example.com/docs", true));
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].url, "https://example.com/docs");
        assert!(set_star(&mut list, "https://example.com/docs", false));
        assert!(list.is_empty());
        assert!(!set_star(&mut list, "https://example.com/docs", false));
    }

    #[test]
    fn fresh_prefs_open_grove() {
        assert_eq!(Prefs::default().surface, "grove");
        assert_eq!(grove_surface(), "grove");
    }
}
