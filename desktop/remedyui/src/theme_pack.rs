//! The shipped theme package: tokens plus layout, with Dark Forest as default.
//!
//! The declarative source lives in `theme/dark-forest.toml`. This module is the
//! typed form the shell reads. A later RemedyUI loader can parse a user pack
//! over this default (unknown keys ignored, missing keys inherited). Custom
//! panels and behavior belong in RemedyLua, not in the theme file.

/// Color tokens as hex strings (no `#` prefix required, but ours include it).
#[derive(Clone, Copy, Debug)]
pub struct ColorTokens {
    pub bg: &'static str,
    pub panel: &'static str,
    pub card: &'static str,
    pub line: &'static str,
    pub text: &'static str,
    pub muted: &'static str,
    pub accent: &'static str,
    pub on_accent: &'static str,
    pub ok: &'static str,
}

/// Stepped planes, bevel edges, and hard-shadow ink. No blur or glow.
#[derive(Clone, Copy, Debug)]
pub struct DepthTokens {
    /// Raised cards and presence frames, a step above panel.
    pub raised: &'static str,
    /// Menus, popovers, and other overlay planes.
    pub overlay: &'static str,
    /// Top and start hairline on raised surfaces.
    pub highlight: &'static str,
    /// Inner top and start edge of sunken wells.
    pub well: &'static str,
    /// Earth accent for selected / hover edges.
    pub earth: &'static str,
    /// Solid hard-shadow ink (used at 2px offset, zero blur).
    pub shadow: &'static str,
}

/// Type scale in px. Font *families* stay on the three config-driven roles.
#[derive(Clone, Copy, Debug)]
pub struct TypeTokens {
    pub small: f32,
    pub body: f32,
    pub title: f32,
    pub section: f32,
    pub section_tracking: f32,
}

/// Spacing grid.
#[derive(Clone, Copy, Debug)]
pub struct SpaceTokens {
    pub unit: f32,
    pub gutter: f32,
    pub control_height: f32,
}

/// Stepped, not rounded. Chip and hairline stay sharp.
#[derive(Clone, Copy, Debug)]
pub struct RadiiTokens {
    pub none: f32,
    pub hair: f32,
    pub chip: f32,
    pub pane: f32,
}

/// Chrome measures that themes may retune.
#[derive(Clone, Copy, Debug)]
pub struct ChromeTokens {
    pub hairline_px: f32,
    pub select_mark: f32,
    pub elevation_blur: f32,
    pub elevation_offset: f32,
}

/// How the face is drawn. Dark Forest ships a geometric pixel mark.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PresenceStyle {
    /// Stepped frame, square marks, no glow or orb.
    GeometricPixel,
}

/// Which bundled face skin the presence draws.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PresenceKind {
    Square,
    MorningStar,
    Wolf,
    Bear,
}

/// Layout: pane placement, docks, rails, sizes, and the face.
#[derive(Clone, Copy, Debug)]
pub struct LayoutSpec {
    pub actions_width: f32,
    pub actions_width_min: f32,
    pub actions_width_max: f32,
    pub chat_pane_min: f32,
    pub rail_width: f32,
    pub status_height: f32,
    pub connected_mark: f32,
    pub presence_home: f32,
    pub presence_avatar: f32,
    pub presence_review: f32,
    pub bubble_max: f32,
    pub presence_style: PresenceStyle,
    pub presence_kind: PresenceKind,
    pub presence_grid: f32,
    pub presence_enabled_default: bool,
    pub select_mark: f32,
    pub pane_radius: f32,
}

/// One complete theme package.
#[derive(Clone, Copy, Debug)]
pub struct ThemePack {
    pub id: &'static str,
    pub name: &'static str,
    pub colors: ColorTokens,
    pub typo: TypeTokens,
    pub space: SpaceTokens,
    pub radii: RadiiTokens,
    pub chrome: ChromeTokens,
    pub layout: LayoutSpec,
    pub depth: DepthTokens,
}

/// Shipped default, matching `theme/dark-forest.toml`.
pub const DARK_FOREST: ThemePack = ThemePack {
    id: "dark-forest",
    name: "Dark Forest",
    colors: ColorTokens {
        bg: "#040a09",
        panel: "#071412",
        card: "#0b1a17",
        line: "#24504a",
        text: "#e2f3ee",
        muted: "#86a39c",
        accent: "#6fdc86",
        on_accent: "#040a09",
        ok: "#6fdc86",
    },
    depth: DepthTokens {
        raised: "#0b1a17",
        overlay: "#122821",
        highlight: "#3a6d66",
        well: "#020807",
        earth: "#C4A36A",
        shadow: "#010403",
    },
    typo: TypeTokens {
        small: 12.0,
        body: 15.0,
        title: 16.0,
        section: 12.0,
        section_tracking: 0.08,
    },
    space: SpaceTokens {
        unit: 4.0,
        gutter: 16.0,
        control_height: 32.0,
    },
    radii: RadiiTokens {
        none: 0.0,
        hair: 2.0,
        chip: 0.0,
        pane: 2.0,
    },
    chrome: ChromeTokens {
        hairline_px: 1.0,
        select_mark: 4.0,
        elevation_blur: 0.0,
        elevation_offset: 2.0,
    },
    layout: LayoutSpec {
        actions_width: 380.0,
        actions_width_min: 240.0,
        actions_width_max: 720.0,
        chat_pane_min: 280.0,
        rail_width: 44.0,
        status_height: 44.0,
        connected_mark: 6.0,
        presence_home: 72.0,
        presence_avatar: 24.0,
        presence_review: 72.0,
        bubble_max: 560.0,
        presence_style: PresenceStyle::GeometricPixel,
        presence_kind: PresenceKind::Square,
        presence_grid: 24.0,
        presence_enabled_default: true,
        select_mark: 4.0,
        pane_radius: 2.0,
    },
};

/// The active package. Today this is always Dark Forest; a user pack will
/// overlay it once the RemedyUI loader lands.
pub fn current() -> &'static ThemePack {
    &DARK_FOREST
}

pub fn layout() -> &'static LayoutSpec {
    &DARK_FOREST.layout
}

pub fn presence_kind() -> crate::presence::Kind {
    presence_skin_kind()
}

pub fn presence_skin() -> &'static str {
    match DARK_FOREST.layout.presence_kind {
        PresenceKind::Square => "square",
        PresenceKind::MorningStar => "morning-star",
        PresenceKind::Wolf => "wolf",
        PresenceKind::Bear => "bear",
    }
}

pub fn presence_skin_kind() -> crate::presence::Kind {
    match DARK_FOREST.layout.presence_kind {
        PresenceKind::Square => crate::presence::Kind::Square,
        PresenceKind::MorningStar => crate::presence::Kind::MorningStar,
        PresenceKind::Wolf => crate::presence::Kind::Wolf,
        PresenceKind::Bear => crate::presence::Kind::Bear,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shipped_default_is_dark_forest() {
        let pack = current();
        assert_eq!(pack.id, "dark-forest");
        assert_eq!(pack.colors.accent, "#6fdc86");
        assert_eq!(pack.layout.connected_mark, 6.0);
        assert_eq!(pack.radii.chip, 0.0);
        assert_eq!(pack.presence_style_is_geometric(), true);
        assert!(pack.layout.presence_enabled_default);
        assert_eq!(pack.depth.raised, "#0b1a17");
        assert_eq!(pack.depth.earth, "#C4A36A");
        assert_eq!(pack.chrome.elevation_blur, 0.0);
        assert_eq!(pack.chrome.elevation_offset, 2.0);
    }
}

impl ThemePack {
    fn presence_style_is_geometric(&self) -> bool {
        self.layout.presence_style == PresenceStyle::GeometricPixel
    }
}

/// Depth tokens from the active pack.
pub fn depth() -> &'static DepthTokens {
    &current().depth
}
