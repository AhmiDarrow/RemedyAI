//! Workspace rails from `desktop/src/workspace/layoutPrefs.ts`.
//!
//! Computer use opens the Browser slide on the right rail, at least 440 px
//! wide, without unmounting a Settings form that is already on the left.

use serde::{Deserialize, Serialize};

use super::paths;

/// Panel body open, icon rail only, or a thin click strip.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RailMode {
    Open,
    Icons,
    Thin,
}

/// Slides the Tauri workspace knows. Only Browser is drawn in this port.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SlideId {
    Sessions,
    Settings,
    Files,
    Terminal,
    Browser,
    Scratch,
    Automations,
}

impl SlideId {
    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "sessions" => Self::Sessions,
            "settings" => Self::Settings,
            "files" => Self::Files,
            "terminal" => Self::Terminal,
            "browser" => Self::Browser,
            "scratch" => Self::Scratch,
            "automations" => Self::Automations,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Sessions => "sessions",
            Self::Settings => "settings",
            Self::Files => "files",
            Self::Terminal => "terminal",
            Self::Browser => "browser",
            Self::Scratch => "scratch",
            Self::Automations => "automations",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct WorkspaceLayout {
    pub left: SlideId,
    pub right: SlideId,
    pub left_width: f32,
    pub right_width: f32,
    pub left_open: bool,
    pub right_open: bool,
    pub left_rail: RailMode,
    pub right_rail: RailMode,
}

/// Side panel body width. Shared by the Tauri drag clamp.
pub const RAIL_WIDTH_MIN: f32 = 200.0;
/// About 30% past the old 480 px cap so Browser and Settings can open wider.
pub const RAIL_WIDTH_MAX: f32 = 624.0;
/// OAuth and computer-use open the browser at least this wide.
pub const OAUTH_BROWSER_MIN: f32 = 440.0;

impl Default for WorkspaceLayout {
    fn default() -> Self {
        Self {
            left: SlideId::Sessions,
            right: SlideId::Settings,
            left_width: 280.0,
            right_width: 300.0,
            left_open: true,
            right_open: false,
            left_rail: RailMode::Open,
            right_rail: RailMode::Thin,
        }
    }
}

pub fn coerce_slide(value: &str, fallback: SlideId) -> SlideId {
    SlideId::parse(value).unwrap_or(fallback)
}

pub fn coerce_rail(value: &str, fallback: RailMode) -> RailMode {
    match value {
        "open" => RailMode::Open,
        "icons" => RailMode::Icons,
        "thin" => RailMode::Thin,
        _ => fallback,
    }
}

pub fn clamp_rail_width(n: f32, fallback: f32) -> f32 {
    if !n.is_finite() {
        return fallback;
    }
    n.floor().clamp(RAIL_WIDTH_MIN, RAIL_WIDTH_MAX)
}

/// Any slide on the right. Sessions stay on the left. Browser uses the wider opener.
pub fn layout_open_slide(prev: &WorkspaceLayout, id: SlideId) -> WorkspaceLayout {
    if id == SlideId::Browser {
        return layout_open_browser(prev);
    }
    if id == SlideId::Sessions {
        let mut next = prev.clone();
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
        return next;
    }
    let mut next = prev.clone();
    next.right = id;
    next.right_rail = RailMode::Open;
    next.right_open = true;
    if next.left == id {
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
    }
    next
}

/// Agent / computer-use: Browser takes the right rail (it may replace Settings).
pub fn layout_open_browser(prev: &WorkspaceLayout) -> WorkspaceLayout {
    let mut next = prev.clone();
    next.right = SlideId::Browser;
    next.right_open = true;
    next.right_rail = RailMode::Open;
    next.right_width = clamp_rail_width(prev.right_width.max(OAUTH_BROWSER_MIN), OAUTH_BROWSER_MIN);
    next.left_open = prev.left_rail == RailMode::Open || prev.left_open;
    if next.left == SlideId::Browser {
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
    }
    next
}

/// OAuth: open Browser without unmounting Settings so the form can update.
pub fn layout_open_browser_beside_settings(prev: &WorkspaceLayout) -> WorkspaceLayout {
    if prev.left == SlideId::Settings {
        let mut next = prev.clone();
        next.right = SlideId::Browser;
        next.right_rail = RailMode::Open;
        next.right_open = true;
        next.right_width = clamp_rail_width(prev.right_width.max(OAUTH_BROWSER_MIN), OAUTH_BROWSER_MIN);
        next.left = SlideId::Settings;
        next.left_rail = RailMode::Open;
        next.left_open = true;
        return next;
    }
    let mut next = prev.clone();
    next.left = SlideId::Browser;
    next.left_rail = RailMode::Open;
    next.left_open = true;
    next.left_width = clamp_rail_width(prev.left_width.max(OAUTH_BROWSER_MIN), OAUTH_BROWSER_MIN);
    next.right = SlideId::Settings;
    next.right_rail = RailMode::Open;
    next.right_open = true;
    next.right_width = clamp_rail_width(prev.right_width.max(300.0), 300.0);
    next
}

/// The browser column is on screen.
pub fn browser_is_open(layout: &WorkspaceLayout) -> bool {
    (layout.right == SlideId::Browser && layout.right_rail == RailMode::Open)
        || (layout.left == SlideId::Browser && layout.left_rail == RailMode::Open)
}

/// Width of the open browser column.
pub fn browser_width(layout: &WorkspaceLayout) -> f32 {
    if layout.left == SlideId::Browser && layout.left_rail == RailMode::Open {
        layout.left_width
    } else {
        layout.right_width
    }
}

#[derive(Deserialize)]
struct RawLayout {
    left: Option<String>,
    right: Option<String>,
    #[serde(rename = "leftWidth")]
    left_width: Option<f64>,
    #[serde(rename = "rightWidth")]
    right_width: Option<f64>,
    #[serde(rename = "leftOpen")]
    left_open: Option<bool>,
    #[serde(rename = "rightOpen")]
    right_open: Option<bool>,
    #[serde(rename = "leftRail")]
    left_rail: Option<String>,
    #[serde(rename = "rightRail")]
    right_rail: Option<String>,
}

pub fn parse_layout(raw: &str) -> Option<WorkspaceLayout> {
    let p: RawLayout = serde_json::from_str(raw).ok()?;
    let defaults = WorkspaceLayout::default();
    let left_open = p.left_open.unwrap_or(true);
    let right_open = p.right_open.unwrap_or(false);
    let (left_rail, right_rail, left_open, right_open) = if p.left_rail.is_some() || p.right_rail.is_some() {
        let left_rail = coerce_rail(p.left_rail.as_deref().unwrap_or(""), if left_open { RailMode::Open } else { RailMode::Icons });
        let right_rail = coerce_rail(
            p.right_rail.as_deref().unwrap_or(""),
            if right_open { RailMode::Open } else { RailMode::Thin },
        );
        (left_rail, right_rail, left_rail == RailMode::Open, right_rail == RailMode::Open)
    } else {
        (
            if left_open { RailMode::Open } else { RailMode::Icons },
            if right_open { RailMode::Open } else { RailMode::Thin },
            left_open,
            right_open,
        )
    };
    Some(WorkspaceLayout {
        left: coerce_slide(p.left.as_deref().unwrap_or(""), defaults.left),
        right: coerce_slide(p.right.as_deref().unwrap_or(""), defaults.right),
        left_width: clamp_rail_width(p.left_width.unwrap_or(defaults.left_width as f64) as f32, defaults.left_width),
        right_width: clamp_rail_width(p.right_width.unwrap_or(defaults.right_width as f64) as f32, defaults.right_width),
        left_open,
        right_open,
        left_rail,
        right_rail,
    })
}

fn to_json(layout: &WorkspaceLayout) -> serde_json::Value {
    serde_json::json!({
        "left": layout.left.as_str(),
        "right": layout.right.as_str(),
        "leftWidth": layout.left_width as u32,
        "rightWidth": layout.right_width as u32,
        "leftOpen": layout.left_rail == RailMode::Open,
        "rightOpen": layout.right_rail == RailMode::Open,
        "leftRail": match layout.left_rail {
            RailMode::Open => "open",
            RailMode::Icons => "icons",
            RailMode::Thin => "thin",
        },
        "rightRail": match layout.right_rail {
            RailMode::Open => "open",
            RailMode::Icons => "icons",
            RailMode::Thin => "thin",
        },
    })
}

pub fn load_workspace_layout() -> WorkspaceLayout {
    let path = paths::layout_path();
    if let Some(raw) = paths::read_to_string(&path) {
        if let Some(layout) = parse_layout(&raw) {
            return layout;
        }
    }
    WorkspaceLayout::default()
}

pub fn save_workspace_layout(layout: &WorkspaceLayout) {
    let left_rail = layout.left_rail;
    let right_rail = layout.right_rail;
    let safe = WorkspaceLayout {
        left: layout.left,
        right: layout.right,
        left_width: clamp_rail_width(layout.left_width, 280.0),
        right_width: clamp_rail_width(layout.right_width, 300.0),
        left_rail,
        right_rail,
        left_open: left_rail == RailMode::Open,
        right_open: right_rail == RailMode::Open,
    };
    paths::write_json(&paths::layout_path(), &to_json(&safe));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn computer_use_opens_browser_on_the_right_at_least_440() {
        let next = layout_open_browser(&WorkspaceLayout::default());
        assert_eq!(next.right, SlideId::Browser);
        assert_eq!(next.right_rail, RailMode::Open);
        assert!(next.right_open);
        assert!(next.right_width >= OAUTH_BROWSER_MIN);
        assert_eq!(next.left, SlideId::Sessions);
        assert!(browser_is_open(&next));
    }

    #[test]
    fn browser_on_the_left_moves_back_to_sessions() {
        let mut prev = WorkspaceLayout::default();
        prev.left = SlideId::Browser;
        let next = layout_open_browser(&prev);
        assert_eq!(next.left, SlideId::Sessions);
        assert_eq!(next.right, SlideId::Browser);
    }

    #[test]
    fn settings_on_the_left_keeps_its_form() {
        let mut prev = WorkspaceLayout::default();
        prev.left = SlideId::Settings;
        let next = layout_open_browser_beside_settings(&prev);
        assert_eq!(next.left, SlideId::Settings);
        assert_eq!(next.right, SlideId::Browser);
        assert!(next.left_open && next.right_open);
    }

    #[test]
    fn corrupt_slides_fall_back_and_widths_clamp() {
        let raw = r#"{"left":"nope","right":"browser","leftWidth":10,"rightWidth":9000,"leftRail":"open","rightRail":"open"}"#;
        let layout = parse_layout(raw).unwrap();
        assert_eq!(layout.left, SlideId::Sessions);
        assert_eq!(layout.right, SlideId::Browser);
        assert_eq!(layout.left_width, RAIL_WIDTH_MIN);
        assert_eq!(layout.right_width, RAIL_WIDTH_MAX);
        assert!(parse_layout("not json").is_none());
    }
}
