//! Workspace rails: which slide sits on each side, how wide it is, and whether
//! the side is open, an icon rail, or a thin strip.
//!
//! This is the current app's `layoutPrefs.ts` (localStorage
//! `remedy.workspaceLayout.v3`, with v2 and v1 migration). The port stores the
//! same JSON under the Remedy home directory:
//! `workspace-layout.v3.json`, and reads `workspace-layout.v2.json` /
//! `workspace-layout.v1.json` once when v3 is absent.
//!
//! `REMEDY_LAYOUT_DIR` overrides that directory (tests). Otherwise the files
//! sit next to `config.toml` (`$REMEDY_HOME` or `~/.remedy`).

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

/// One workspace tool. The order is the icon rail, top to bottom.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SlideId {
    /// Chats and projects.
    Sessions,
    /// Provider, mind, appearance.
    Settings,
    /// Project files.
    Files,
    /// Shell on this machine.
    Terminal,
    /// Research pane.
    Browser,
    /// Session notepad.
    Scratch,
    /// Background tasks.
    Automations,
}

/// Every slide, in rail order.
pub const ALL_SLIDES: [SlideId; 7] = [
    SlideId::Sessions,
    SlideId::Settings,
    SlideId::Files,
    SlideId::Terminal,
    SlideId::Browser,
    SlideId::Scratch,
    SlideId::Automations,
];

impl SlideId {
    /// Stable id, the same string the current app stores.
    pub fn as_str(self) -> &'static str {
        match self {
            SlideId::Sessions => "sessions",
            SlideId::Settings => "settings",
            SlideId::Files => "files",
            SlideId::Terminal => "terminal",
            SlideId::Browser => "browser",
            SlideId::Scratch => "scratch",
            SlideId::Automations => "automations",
        }
    }

    /// Fluent id for the visible name.
    pub fn label_id(self) -> &'static str {
        match self {
            SlideId::Sessions => "slide-sessions",
            SlideId::Settings => "slide-settings",
            SlideId::Files => "slide-files",
            SlideId::Terminal => "slide-terminal",
            SlideId::Browser => "slide-browser",
            SlideId::Scratch => "slide-scratch",
            SlideId::Automations => "slide-automations",
        }
    }

    /// One-line description while the full slide body is still another port.
    pub fn blurb_id(self) -> &'static str {
        match self {
            SlideId::Sessions => "slide-blurb-sessions",
            SlideId::Settings => "slide-blurb-settings",
            SlideId::Files => "slide-blurb-files",
            SlideId::Terminal => "slide-blurb-terminal",
            SlideId::Browser => "slide-blurb-browser",
            SlideId::Scratch => "slide-blurb-scratch",
            SlideId::Automations => "slide-blurb-automations",
        }
    }

    /// Terminal, Browser, and Scratch can leave the rail.
    pub fn popout(self) -> bool {
        matches!(self, SlideId::Terminal | SlideId::Browser | SlideId::Scratch)
    }

    fn parse(value: &str) -> Option<SlideId> {
        Some(match value {
            "sessions" => SlideId::Sessions,
            "settings" => SlideId::Settings,
            "files" => SlideId::Files,
            "terminal" => SlideId::Terminal,
            "browser" => SlideId::Browser,
            "scratch" => SlideId::Scratch,
            "automations" => SlideId::Automations,
            _ => return None,
        })
    }
}

/// Panel body open, icon rail only, or a thin click strip.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RailMode {
    /// Icons plus the panel body.
    Open,
    /// Icon rail only.
    Icons,
    /// 12 px strip. Click expands to icons.
    Thin,
}

impl RailMode {
    /// Stable id stored on disk.
    pub fn as_str(self) -> &'static str {
        match self {
            RailMode::Open => "open",
            RailMode::Icons => "icons",
            RailMode::Thin => "thin",
        }
    }

    fn parse(value: &str) -> Option<RailMode> {
        Some(match value {
            "open" => RailMode::Open,
            "icons" => RailMode::Icons,
            "thin" => RailMode::Thin,
            _ => return None,
        })
    }
}

/// Which edge of the chat a rail occupies.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    /// Outer edge on the left.
    Left,
    /// Outer edge on the right.
    Right,
}

/// Both rails, persisted together. There is no separate left/right/dual setting:
/// dual is both rails open, left is the left rail open and the right thin, and
/// right is the opposite (including after swap).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WorkspaceLayout {
    /// Slide on the left edge.
    pub left: SlideId,
    /// Slide on the right edge.
    pub right: SlideId,
    /// Left body width in px, before the icon rail.
    pub left_width: u32,
    /// Right body width in px, before the icon rail.
    pub right_width: u32,
    /// True when [`Self::left_rail`] is open. Kept so v1/v2 files still load.
    pub left_open: bool,
    /// True when [`Self::right_rail`] is open.
    pub right_open: bool,
    /// Left rail mode. This is what the chrome shows.
    pub left_rail: RailMode,
    /// Right rail mode.
    pub right_rail: RailMode,
}

/// Narrow viewport: only one panel is temporarily open. Matches
/// `max-width: 760px`.
pub const NARROW_MAX: f32 = 760.0;

/// Body width clamp, shared with the resize handle.
pub const RAIL_WIDTH_MIN: u32 = 200;
/// ~30% past the old 480 px cap.
pub const RAIL_WIDTH_MAX: u32 = 624;
/// Double-click on the resize handle.
pub const RAIL_WIDTH_DEFAULT: u32 = 300;

const DEFAULT_LEFT_WIDTH: u32 = 280;
const DEFAULT_RIGHT_WIDTH: u32 = 300;
/// Browser opened for OAuth / computer-use is at least this wide.
const OAUTH_BROWSER_MIN: u32 = 440;

const FILE_V3: &str = "workspace-layout.v3.json";
const FILE_V2: &str = "workspace-layout.v2.json";
const FILE_V1: &str = "workspace-layout.v1.json";

impl Default for WorkspaceLayout {
    fn default() -> Self {
        WorkspaceLayout {
            left: SlideId::Sessions,
            right: SlideId::Settings,
            left_width: DEFAULT_LEFT_WIDTH,
            right_width: DEFAULT_RIGHT_WIDTH,
            left_open: true,
            right_open: false,
            left_rail: RailMode::Open,
            right_rail: RailMode::Thin,
        }
    }
}

impl WorkspaceLayout {
    /// The slide on `side`.
    pub fn slide(self, side: Side) -> SlideId {
        match side {
            Side::Left => self.left,
            Side::Right => self.right,
        }
    }

    /// Body width on `side`.
    pub fn width(self, side: Side) -> u32 {
        match side {
            Side::Left => self.left_width,
            Side::Right => self.right_width,
        }
    }

    /// Persisted rail mode on `side` (not the temporary narrow-viewport mode).
    pub fn rail(self, side: Side) -> RailMode {
        match side {
            Side::Left => self.left_rail,
            Side::Right => self.right_rail,
        }
    }
}

/// A shallow merge, plus the rule that Browser is never on both sides.
#[derive(Clone, Copy, Debug, Default)]
pub struct LayoutPatch {
    /// Replace the left slide.
    pub left: Option<SlideId>,
    /// Replace the right slide.
    pub right: Option<SlideId>,
    /// Replace the left body width.
    pub left_width: Option<u32>,
    /// Replace the right body width.
    pub right_width: Option<u32>,
    /// Replace the deprecated left-open flag.
    pub left_open: Option<bool>,
    /// Replace the deprecated right-open flag.
    pub right_open: Option<bool>,
    /// Replace the left rail mode.
    pub left_rail: Option<RailMode>,
    /// Replace the right rail mode.
    pub right_rail: Option<RailMode>,
}

/// Coerce an untrusted slide id. Unknown values become `fallback`.
pub fn coerce_slide_id(value: &str, fallback: SlideId) -> SlideId {
    SlideId::parse(value).unwrap_or(fallback)
}

/// Coerce an untrusted rail mode. Unknown values become `fallback`.
pub fn coerce_rail_mode(value: &str, fallback: RailMode) -> RailMode {
    RailMode::parse(value).unwrap_or(fallback)
}

/// Clamp a side-panel width. Non-finite numbers become `fallback`.
pub fn clamp_rail_width(n: f64, fallback: u32) -> u32 {
    if !n.is_finite() {
        return fallback;
    }
    let v = n.floor();
    if v < f64::from(RAIL_WIDTH_MIN) {
        RAIL_WIDTH_MIN
    } else if v > f64::from(RAIL_WIDTH_MAX) {
        RAIL_WIDTH_MAX
    } else {
        v as u32
    }
}

fn clamp_stored(n: u32) -> u32 {
    n.clamp(RAIL_WIDTH_MIN, RAIL_WIDTH_MAX)
}

/// Widths clamped, open-flags following the rail modes.
pub fn normalize(layout: WorkspaceLayout) -> WorkspaceLayout {
    let left_rail = layout.left_rail;
    let right_rail = layout.right_rail;
    WorkspaceLayout {
        left: layout.left,
        right: layout.right,
        left_width: clamp_stored(layout.left_width),
        right_width: clamp_stored(layout.right_width),
        left_rail,
        right_rail,
        left_open: left_rail == RailMode::Open,
        right_open: right_rail == RailMode::Open,
    }
}

/// Merge `patch` into `prev`. Two Browsers collapse to one: the side just set
/// to Browser wins, and the other side keeps its previous slide (or Files).
pub fn layout_patch(prev: &WorkspaceLayout, patch: &LayoutPatch) -> WorkspaceLayout {
    let mut next = *prev;
    if let Some(v) = patch.left {
        next.left = v;
    }
    if let Some(v) = patch.right {
        next.right = v;
    }
    if let Some(v) = patch.left_width {
        next.left_width = v;
    }
    if let Some(v) = patch.right_width {
        next.right_width = v;
    }
    if let Some(v) = patch.left_open {
        next.left_open = v;
    }
    if let Some(v) = patch.right_open {
        next.right_open = v;
    }
    if let Some(v) = patch.left_rail {
        next.left_rail = v;
    }
    if let Some(v) = patch.right_rail {
        next.right_rail = v;
    }
    if next.left == SlideId::Browser && next.right == SlideId::Browser {
        if patch.left == Some(SlideId::Browser) {
            next.right = if prev.right == SlideId::Browser {
                SlideId::Files
            } else {
                prev.right
            };
        } else if patch.right == Some(SlideId::Browser) {
            next.left = if prev.left == SlideId::Browser {
                SlideId::Files
            } else {
                prev.left
            };
        } else {
            next.right = SlideId::Files;
        }
    }
    next
}

/// Swap left and right: slide, width, and rail mode.
pub fn layout_swap(prev: &WorkspaceLayout) -> WorkspaceLayout {
    WorkspaceLayout {
        left: prev.right,
        right: prev.left,
        left_width: prev.right_width,
        right_width: prev.left_width,
        left_open: prev.right_open,
        right_open: prev.left_open,
        left_rail: prev.right_rail,
        right_rail: prev.left_rail,
    }
}

/// Open any Studio rail on the right. Sessions stay on the left.
pub fn layout_open_slide(prev: &WorkspaceLayout, id: SlideId) -> WorkspaceLayout {
    if id == SlideId::Browser {
        return layout_open_browser(prev);
    }
    if id == SlideId::Sessions {
        let mut next = *prev;
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
        return next;
    }
    let mut next = *prev;
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

/// Browser takes the right rail and is at least [`OAUTH_BROWSER_MIN`] wide.
/// A Browser that was on the left becomes Sessions.
pub fn layout_open_browser(prev: &WorkspaceLayout) -> WorkspaceLayout {
    let mut next = *prev;
    next.right = SlideId::Browser;
    next.right_open = true;
    next.right_rail = RailMode::Open;
    next.right_width = prev.right_width.max(OAUTH_BROWSER_MIN);
    next.left_open = prev.left_rail == RailMode::Open || prev.left_open;
    if next.left == SlideId::Browser {
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
    }
    next
}

/// OAuth: open Browser without unmounting Settings.
pub fn layout_open_browser_beside_settings(prev: &WorkspaceLayout) -> WorkspaceLayout {
    if prev.left == SlideId::Settings {
        let mut next = *prev;
        next.right = SlideId::Browser;
        next.right_rail = RailMode::Open;
        next.right_open = true;
        next.right_width = prev.right_width.max(OAUTH_BROWSER_MIN);
        next.left = SlideId::Settings;
        next.left_rail = RailMode::Open;
        next.left_open = true;
        return next;
    }
    let mut next = *prev;
    next.left = SlideId::Browser;
    next.left_rail = RailMode::Open;
    next.left_open = true;
    next.left_width = prev.left_width.max(OAUTH_BROWSER_MIN);
    next.right = SlideId::Settings;
    next.right_rail = RailMode::Open;
    next.right_open = true;
    next.right_width = prev.right_width.max(DEFAULT_RIGHT_WIDTH);
    next
}

/// Settings from the command palette: always the right rail. If Settings was
/// parked on the left, Sessions returns there. A thin left rail becomes icons
/// so the left edge does not disappear while Settings opens.
pub fn layout_open_settings(prev: &WorkspaceLayout) -> WorkspaceLayout {
    let mut next = *prev;
    if prev.left == SlideId::Settings {
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
    } else {
        if prev.left_rail == RailMode::Thin {
            next.left_rail = RailMode::Icons;
        }
        next.left_open = prev.left_rail == RailMode::Open || prev.left_open;
    }
    next.right = SlideId::Settings;
    next.right_rail = RailMode::Open;
    next.right_open = true;
    next
}

/// Status-bar Settings. Open on the right, or collapse to thin when it is
/// already the open right slide. Does not bump a thin left rail to icons.
pub fn layout_toggle_settings(prev: &WorkspaceLayout) -> WorkspaceLayout {
    if prev.right == SlideId::Settings && prev.right_rail == RailMode::Open {
        let mut next = *prev;
        next.right_rail = RailMode::Thin;
        next.right_open = false;
        return next;
    }
    let mut next = *prev;
    if prev.left == SlideId::Settings {
        next.left = SlideId::Sessions;
        next.left_rail = RailMode::Open;
        next.left_open = true;
    }
    next.right = SlideId::Settings;
    next.right_rail = RailMode::Open;
    next.right_open = true;
    next
}

/// What a narrow viewport shows. `narrow` is the viewport, not a preference.
/// Crossing into or out of narrow clears the temporary side. A layout change
/// while narrow shows the side that just became open; it does not clear that
/// side when a rail closes (the rail's own collapse button does).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NarrowRails {
    /// Viewport is at most [`NARROW_MAX`].
    pub narrow: bool,
    /// The one panel temporarily open, if any.
    pub side: Option<Side>,
    previous: WorkspaceLayout,
}

impl NarrowRails {
    /// Desktop preferences, nothing temporary yet.
    pub fn new(layout: WorkspaceLayout) -> Self {
        NarrowRails {
            narrow: false,
            side: None,
            previous: layout,
        }
    }

    /// `width` is the viewport in px. Zero means the window has not been
    /// measured yet and is treated as desktop.
    pub fn observe(&mut self, width: f32, layout: &WorkspaceLayout) -> (RailMode, RailMode) {
        let narrow = width > 0.0 && width <= NARROW_MAX;
        let crossed = narrow != self.narrow;
        if crossed {
            self.narrow = narrow;
            self.side = None;
        }
        let old = self.previous;
        if !crossed && self.narrow && old != *layout {
            if layout.right_rail == RailMode::Open
                && (old.right_rail != RailMode::Open || old.right != layout.right)
            {
                self.side = Some(Side::Right);
            } else if layout.left_rail == RailMode::Open
                && (old.left_rail != RailMode::Open || old.left != layout.left)
            {
                self.side = Some(Side::Left);
            }
        }
        self.previous = *layout;
        (self.mode(Side::Left, layout), self.mode(Side::Right, layout))
    }

    /// The mode `side` shows right now.
    pub fn mode(&self, side: Side, layout: &WorkspaceLayout) -> RailMode {
        if self.narrow {
            if self.side == Some(side) {
                RailMode::Open
            } else {
                RailMode::Icons
            }
        } else {
            layout.rail(side)
        }
    }
}

/// Directory that holds `workspace-layout.v3.json`.
pub fn layout_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("REMEDY_LAYOUT_DIR") {
        let p = PathBuf::from(dir);
        if !p.as_os_str().is_empty() {
            return p;
        }
    }
    let config = crate::fonts::config_path();
    config
        .parent()
        .map(Path::to_path_buf)
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Load the persisted layout, migrating v2 or v1 into v3 when needed.
pub fn load_workspace_layout() -> WorkspaceLayout {
    load_workspace_layout_from(&layout_dir())
}

/// Save `layout` as v3, clamped and coerced the way the current app saves.
pub fn save_workspace_layout(layout: &WorkspaceLayout) {
    save_workspace_layout_to(&layout_dir(), layout);
}

/// [`load_workspace_layout`] against an explicit directory.
pub fn load_workspace_layout_from(dir: &Path) -> WorkspaceLayout {
    if let Some(v3) = read_layout(dir, FILE_V3) {
        return v3;
    }
    if let Some(v2) = read_layout(dir, FILE_V2) {
        let migrated = WorkspaceLayout {
            right_rail: if v2.right_open {
                RailMode::Open
            } else {
                RailMode::Thin
            },
            left_rail: if v2.left_open {
                RailMode::Open
            } else {
                RailMode::Icons
            },
            ..v2
        };
        let migrated = normalize(migrated);
        save_workspace_layout_to(dir, &migrated);
        return migrated;
    }
    if let Some(v1) = read_layout(dir, FILE_V1) {
        let migrated = WorkspaceLayout {
            right_open: false,
            right_rail: RailMode::Thin,
            left_rail: if v1.left_open {
                RailMode::Open
            } else {
                RailMode::Icons
            },
            ..v1
        };
        let migrated = normalize(migrated);
        save_workspace_layout_to(dir, &migrated);
        return migrated;
    }
    WorkspaceLayout::default()
}

/// [`save_workspace_layout`] against an explicit directory.
pub fn save_workspace_layout_to(dir: &Path, layout: &WorkspaceLayout) {
    let safe = normalize(*layout);
    let text = serde_json::to_string(&layout_json(&safe)).unwrap_or_default();
    if text.is_empty() {
        return;
    }
    let path = dir.join(FILE_V3);
    let _ = std::fs::create_dir_all(dir);
    let tmp = dir.join("workspace-layout.v3.json.tmp");
    if std::fs::write(&tmp, text.as_bytes()).is_ok() {
        if std::fs::rename(&tmp, &path).is_err() {
            let _ = std::fs::write(&path, text.as_bytes());
            let _ = std::fs::remove_file(&tmp);
        }
    }
}

fn read_layout(dir: &Path, name: &str) -> Option<WorkspaceLayout> {
    let text = std::fs::read_to_string(dir.join(name)).ok()?;
    parse_layout(&text)
}

fn layout_json(layout: &WorkspaceLayout) -> Value {
    serde_json::json!({
        "left": layout.left.as_str(),
        "right": layout.right.as_str(),
        "leftWidth": layout.left_width,
        "rightWidth": layout.right_width,
        "leftOpen": layout.left_open,
        "rightOpen": layout.right_open,
        "leftRail": layout.left_rail.as_str(),
        "rightRail": layout.right_rail.as_str(),
    })
}

fn parse_layout(raw: &str) -> Option<WorkspaceLayout> {
    let value: Value = serde_json::from_str(raw).ok()?;
    let obj = value.as_object()?;
    let defaults = WorkspaceLayout::default();
    // `leftOpen !== false`: only a real false closes the left side.
    let left_open = !matches!(obj.get("leftOpen"), Some(Value::Bool(false)));
    let right_open = js_bool(obj.get("rightOpen"));
    let rails_present = rail_present(obj, "leftRail") || rail_present(obj, "rightRail");
    let (left_rail, right_rail, left_open, right_open) = if rails_present {
        let left_rail = coerce_rail_value(
            obj.get("leftRail"),
            if left_open {
                RailMode::Open
            } else {
                RailMode::Icons
            },
        );
        let right_rail = coerce_rail_value(
            obj.get("rightRail"),
            if right_open {
                RailMode::Open
            } else {
                RailMode::Thin
            },
        );
        (
            left_rail,
            right_rail,
            left_rail == RailMode::Open,
            right_rail == RailMode::Open,
        )
    } else {
        (
            if left_open {
                RailMode::Open
            } else {
                RailMode::Icons
            },
            if right_open {
                RailMode::Open
            } else {
                RailMode::Thin
            },
            left_open,
            right_open,
        )
    };
    Some(WorkspaceLayout {
        left: coerce_slide_value(obj.get("left"), defaults.left),
        right: coerce_slide_value(obj.get("right"), defaults.right),
        left_width: read_width(obj.get("leftWidth"), defaults.left_width),
        right_width: read_width(obj.get("rightWidth"), defaults.right_width),
        left_open,
        right_open,
        left_rail,
        right_rail,
    })
}

fn rail_present(obj: &Map<String, Value>, key: &str) -> bool {
    !matches!(obj.get(key), None | Some(Value::Null))
}

fn coerce_slide_value(value: Option<&Value>, fallback: SlideId) -> SlideId {
    match value {
        Some(Value::String(s)) => coerce_slide_id(s, fallback),
        _ => fallback,
    }
}

fn coerce_rail_value(value: Option<&Value>, fallback: RailMode) -> RailMode {
    match value {
        Some(Value::String(s)) => coerce_rail_mode(s, fallback),
        _ => fallback,
    }
}

fn read_width(value: Option<&Value>, fallback: u32) -> u32 {
    match value {
        Some(Value::Number(n)) => clamp_rail_width(n.as_f64().unwrap_or(f64::NAN), fallback),
        Some(Value::String(s)) => clamp_rail_width(s.parse().unwrap_or(f64::NAN), fallback),
        _ => fallback,
    }
}

/// JavaScript `Boolean(value)` for the right-open flag.
fn js_bool(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0) != 0.0,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(_) | Value::Object(_)) => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "remedy-layout-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    fn write_raw(dir: &Path, name: &str, body: &str) {
        std::fs::write(dir.join(name), body).unwrap();
    }

    #[test]
    fn defaults_when_empty_left_open_right_thin() {
        let dir = temp_dir("empty");
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Sessions);
        assert_eq!(layout.left_rail, RailMode::Open);
        assert_eq!(layout.right_rail, RailMode::Thin);
        assert!(layout.left_open);
        assert!(!layout.right_open);
        assert!(layout.left_width >= RAIL_WIDTH_MIN);
        assert_eq!(layout.left_width, 280);
        assert_eq!(layout.right, SlideId::Settings);
        assert_eq!(layout.right_width, 300);
    }

    #[test]
    fn round_trip_clamps_widths() {
        let dir = temp_dir("round");
        save_workspace_layout_to(
            &dir,
            &WorkspaceLayout {
                left: SlideId::Files,
                right: SlideId::Scratch,
                left_width: 50,
                right_width: 9999,
                left_open: false,
                right_open: true,
                left_rail: RailMode::Icons,
                right_rail: RailMode::Open,
            },
        );
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Files);
        assert_eq!(layout.right, SlideId::Scratch);
        assert_eq!(layout.left_width, 200);
        assert_eq!(layout.right_width, 624);
        assert_eq!(layout.left_rail, RailMode::Icons);
        assert_eq!(layout.right_rail, RailMode::Open);
        assert!(!layout.left_open);
        assert!(layout.right_open);
    }

    #[test]
    fn rejects_unknown_slide_ids() {
        assert_eq!(coerce_slide_id("not-a-slide", SlideId::Sessions), SlideId::Sessions);
        assert_eq!(coerce_slide_id("browser", SlideId::Sessions), SlideId::Browser);
        assert_eq!(coerce_rail_mode("bogus", RailMode::Thin), RailMode::Thin);
        assert_eq!(coerce_rail_mode("icons", RailMode::Thin), RailMode::Icons);
        let dir = temp_dir("bogus");
        write_raw(
            &dir,
            FILE_V3,
            r#"{"left":"bogus","right":42,"leftWidth":"x","leftRail":"nope"}"#,
        );
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Sessions);
        assert_eq!(layout.right, SlideId::Settings);
        assert_eq!(layout.left_width, 280);
        // coerceRailMode('nope') falls back to leftOpen, which defaults true → open.
        assert_eq!(layout.left_rail, RailMode::Open);
    }

    #[test]
    fn migrates_v2_to_v3() {
        let dir = temp_dir("v2");
        write_raw(
            &dir,
            FILE_V2,
            r#"{"left":"settings","right":"terminal","leftWidth":300,"rightWidth":320,"leftOpen":true,"rightOpen":false}"#,
        );
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Settings);
        assert_eq!(layout.right, SlideId::Terminal);
        assert_eq!(layout.left_rail, RailMode::Open);
        assert_eq!(layout.right_rail, RailMode::Thin);
        assert_eq!(layout.left_width, 300);
        assert_eq!(layout.right_width, 320);
        assert!(dir.join(FILE_V3).is_file());
    }

    #[test]
    fn migrates_v1_right_rail_to_thin() {
        let dir = temp_dir("v1");
        write_raw(
            &dir,
            FILE_V1,
            r#"{"left":"files","right":"browser","leftWidth":280,"rightWidth":400,"leftOpen":true,"rightOpen":true}"#,
        );
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Files);
        assert_eq!(layout.left_rail, RailMode::Open);
        assert_eq!(layout.right_rail, RailMode::Thin);
        assert!(!layout.right_open);
        assert!(dir.join(FILE_V3).is_file());
    }

    #[test]
    fn v3_wins_over_older_files() {
        let dir = temp_dir("prefer-v3");
        write_raw(
            &dir,
            FILE_V2,
            r#"{"left":"files","right":"terminal","leftOpen":true,"rightOpen":true}"#,
        );
        write_raw(
            &dir,
            FILE_V3,
            r#"{"left":"scratch","right":"settings","leftWidth":220,"rightWidth":240,"leftRail":"icons","rightRail":"thin"}"#,
        );
        let layout = load_workspace_layout_from(&dir);
        assert_eq!(layout.left, SlideId::Scratch);
        assert_eq!(layout.left_rail, RailMode::Icons);
        assert_eq!(layout.right_rail, RailMode::Thin);
    }

    #[test]
    fn opens_browser_on_the_right_and_not_twice() {
        let next = layout_open_browser(&WorkspaceLayout {
            left: SlideId::Sessions,
            right: SlideId::Settings,
            left_width: 280,
            right_width: 300,
            left_open: true,
            right_open: true,
            left_rail: RailMode::Open,
            right_rail: RailMode::Open,
        });
        assert_eq!(next.right, SlideId::Browser);
        assert_eq!(next.left, SlideId::Sessions);
        assert_eq!(next.right_rail, RailMode::Open);
        assert!(next.right_width >= OAUTH_BROWSER_MIN);

        let from_left = layout_open_browser(&WorkspaceLayout {
            left: SlideId::Browser,
            right: SlideId::Settings,
            ..WorkspaceLayout::default()
        });
        assert_eq!(from_left.right, SlideId::Browser);
        assert_eq!(from_left.left, SlideId::Sessions);
        assert_ne!(from_left.left, from_left.right);
    }

    #[test]
    fn patch_refuses_two_browsers() {
        let prev = WorkspaceLayout {
            left: SlideId::Sessions,
            right: SlideId::Browser,
            ..WorkspaceLayout::default()
        };
        let next = layout_patch(
            &prev,
            &LayoutPatch {
                left: Some(SlideId::Browser),
                ..LayoutPatch::default()
            },
        );
        assert_eq!(next.left, SlideId::Browser);
        // The right side was already Browser, so it falls back to Files.
        assert_eq!(next.right, SlideId::Files);

        let both = layout_patch(
            &WorkspaceLayout {
                left: SlideId::Browser,
                right: SlideId::Browser,
                ..WorkspaceLayout::default()
            },
            &LayoutPatch::default(),
        );
        assert_eq!(both.left, SlideId::Browser);
        assert_eq!(both.right, SlideId::Files);
    }

    #[test]
    fn keeps_settings_mounted_beside_browser() {
        let from_right = layout_open_browser_beside_settings(&WorkspaceLayout {
            left: SlideId::Sessions,
            right: SlideId::Settings,
            left_width: 280,
            right_width: 320,
            left_open: true,
            right_open: true,
            left_rail: RailMode::Open,
            right_rail: RailMode::Open,
        });
        assert_eq!(from_right.right, SlideId::Settings);
        assert_eq!(from_right.left, SlideId::Browser);
        assert_eq!(from_right.left_rail, RailMode::Open);
        assert_eq!(from_right.right_rail, RailMode::Open);
        assert!(from_right.left_width >= OAUTH_BROWSER_MIN);

        let from_left = layout_open_browser_beside_settings(&WorkspaceLayout {
            left: SlideId::Settings,
            right: SlideId::Terminal,
            left_width: 300,
            right_width: 320,
            left_open: true,
            right_open: false,
            left_rail: RailMode::Open,
            right_rail: RailMode::Thin,
        });
        assert_eq!(from_left.left, SlideId::Settings);
        assert_eq!(from_left.right, SlideId::Browser);
        assert_eq!(from_left.right_rail, RailMode::Open);
        assert!(from_left.right_width >= OAUTH_BROWSER_MIN);
    }

    #[test]
    fn opens_terminal_on_the_right_without_duplicating() {
        let next = layout_open_slide(
            &WorkspaceLayout {
                left: SlideId::Terminal,
                right: SlideId::Settings,
                left_width: 280,
                right_width: 300,
                left_open: true,
                right_open: false,
                left_rail: RailMode::Open,
                right_rail: RailMode::Thin,
            },
            SlideId::Terminal,
        );
        assert_eq!(next.right, SlideId::Terminal);
        assert_eq!(next.left, SlideId::Sessions);
        assert_eq!(next.right_rail, RailMode::Open);
    }

    #[test]
    fn sessions_open_on_the_left() {
        let next = layout_open_slide(
            &WorkspaceLayout {
                left: SlideId::Files,
                left_rail: RailMode::Thin,
                ..WorkspaceLayout::default()
            },
            SlideId::Sessions,
        );
        assert_eq!(next.left, SlideId::Sessions);
        assert_eq!(next.left_rail, RailMode::Open);
        assert!(next.left_open);
    }

    #[test]
    fn swap_exchanges_slide_width_and_mode() {
        let next = layout_swap(&WorkspaceLayout::default());
        assert_eq!(next.left, SlideId::Settings);
        assert_eq!(next.right, SlideId::Sessions);
        assert_eq!(next.left_width, 300);
        assert_eq!(next.right_width, 280);
        assert_eq!(next.left_rail, RailMode::Thin);
        assert_eq!(next.right_rail, RailMode::Open);
        let back = layout_swap(&next);
        assert_eq!(back, normalize(WorkspaceLayout::default()));
    }

    #[test]
    fn settings_toggle_opens_then_collapses_the_right_rail() {
        let open = layout_toggle_settings(&WorkspaceLayout::default());
        assert_eq!(open.right, SlideId::Settings);
        assert_eq!(open.right_rail, RailMode::Open);
        assert_eq!(open.left, SlideId::Sessions);
        assert_eq!(open.left_rail, RailMode::Open);
        let shut = layout_toggle_settings(&open);
        assert_eq!(shut.right_rail, RailMode::Thin);
        assert!(!shut.right_open);
        assert_eq!(shut.left_rail, RailMode::Open);
    }

    #[test]
    fn narrow_keeps_desktop_prefs_and_shows_one_panel() {
        let saved = WorkspaceLayout::default();
        let mut rails = NarrowRails::new(saved);
        let (left, right) = rails.observe(1100.0, &saved);
        assert_eq!((left, right), (RailMode::Open, RailMode::Thin));

        let (left, right) = rails.observe(700.0, &saved);
        assert_eq!((left, right), (RailMode::Icons, RailMode::Icons));
        assert!(rails.narrow);

        let mut opened = saved;
        opened.right_rail = RailMode::Open;
        opened.right_open = true;
        let (left, right) = rails.observe(700.0, &opened);
        assert_eq!((left, right), (RailMode::Icons, RailMode::Open));

        let (left, right) = rails.observe(1100.0, &opened);
        assert_eq!((left, right), (RailMode::Open, RailMode::Open));
        assert_eq!(opened.left_width, saved.left_width);
    }

    #[test]
    fn clamp_matches_floor_then_bounds() {
        assert_eq!(clamp_rail_width(50.0, 280), 200);
        assert_eq!(clamp_rail_width(9999.0, 280), 624);
        assert_eq!(clamp_rail_width(280.9, 280), 280);
        assert_eq!(clamp_rail_width(f64::NAN, 280), 280);
        assert_eq!(clamp_rail_width(200.0, 280), 200);
        assert_eq!(clamp_rail_width(624.0, 280), 624);
    }
}
