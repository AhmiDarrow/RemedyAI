//! Shared spacing, type, surfaces and elevation for the Studio shell.

use remedy_ui::core::{Color, Insets};
use remedy_ui::style::{
    BorderEdge, BorderSide, ColorRoles, LetterSpacing, LineHeight, Shadow, StylePatch,
    TextTransform,
};

use crate::theme_pack::{self, DepthTokens};

/// Default tool control (composer, status, title bar).
pub const TOOL: f32 = 32.0;
/// Glyph inside [`TOOL`].
pub const TOOL_GLYPH: f32 = 18.0;
/// Session-row tool (fits beside the title).
pub const ROW_TOOL: f32 = 28.0;
/// Glyph inside [`ROW_TOOL`].
pub const ROW_GLYPH: f32 = 16.0;
/// Thin icon rail width.
pub const RAIL: f32 = 44.0;
/// Hit target on the icon rail.
pub const RAIL_BTN: f32 = 36.0;
/// Glyph on the icon rail.
pub const RAIL_GLYPH: f32 = 18.0;
/// Width reserved for the three session-row actions when they are revealed.
pub const ROW_ACTIONS: f32 = ROW_TOOL * 3.0 + 8.0;

/// 4 px grid unit the shell is built on (matches the theme default).
pub const SPACE: f32 = crate::theme_pack::DARK_FOREST.space.unit;
/// Shared gutter for headers, feeds, the composer and the sidebar.
pub const GUTTER: f32 = crate::theme_pack::DARK_FOREST.space.gutter;
/// Persona badge (square, hairline).
pub const AVATAR: f32 = crate::theme_pack::DARK_FOREST.layout.presence_avatar;
/// Chat bubble cap, inside a split pane.
pub const BUBBLE_MAX: f32 = crate::theme_pack::DARK_FOREST.layout.bubble_max;

/// A named step on the type scale: size, weight, leading and tracking.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Type {
    /// Size in logical pixels.
    pub size: f32,
    /// Font weight (400 regular, 600 semibold).
    pub weight: f32,
    /// Line height as a multiple of size.
    pub leading: f32,
    /// Tracking, in em. Negative tightens titles.
    pub tracking: f32,
    /// Monospace (the theme's code family) when true.
    pub mono: bool,
    /// Display / heading family (the theme's heading token) when true.
    pub display: bool,
}

/// Home greeting and other display headlines. 24 is a multiple of 8 (Ahmi Pixel is sharp there).
pub const HERO: Type = Type {
    size: 24.0,
    weight: 600.0,
    leading: 1.15,
    tracking: 0.0,
    mono: false,
    display: true,
};
/// Smaller display titles (presence state names, compact headings).
pub const TITLE: Type = Type {
    size: 16.0,
    weight: 600.0,
    leading: 1.25,
    tracking: 0.0,
    mono: false,
    display: true,
};
/// Section kickers (CODING / ACTIONS, day stamps). UI face, small caps — not the display font.
pub const SECTION: Type = Type {
    size: 12.0,
    weight: 600.0,
    leading: 1.2,
    tracking: 0.08,
    mono: false,
    display: false,
};
/// Chat body and readable prose.
pub const BODY: Type = Type {
    size: 15.0,
    weight: 450.0,
    leading: 1.55,
    tracking: 0.0,
    mono: false,
    display: false,
};
/// Timestamps, helper lines, session ages.
pub const META: Type = Type {
    size: 12.0,
    weight: 500.0,
    leading: 1.35,
    tracking: 0.02,
    mono: false,
    display: false,
};
/// Tool names, arguments, and other code-like rows.
pub const MONO: Type = Type {
    size: 12.0,
    weight: 500.0,
    leading: 1.45,
    tracking: 0.0,
    mono: true,
    display: false,
};
/// Thinking paragraphs: a half-step quieter than body.
pub const THINKING: Type = Type {
    size: 14.0,
    weight: 430.0,
    leading: 1.5,
    tracking: 0.0,
    mono: false,
    display: false,
};

/// Style for one step on the type scale.
pub fn typo(t: Type) -> StylePatch {
    let mut p = StylePatch::new()
        .text_size(t.size)
        .weight(t.weight)
        .line_height(LineHeight::Relative(t.leading));
    if t.tracking != 0.0 {
        p = p.letter_spacing(LetterSpacing::Em(t.tracking));
    }
    if t.mono {
        p = p.mono(true);
    }
    if t.display {
        p = p.heading(true);
    }
    p
}

/// Uppercase section kicker in the muted color.
pub fn section_kicker(colors: &ColorRoles) -> StylePatch {
    typo(SECTION)
        .text_transform(TextTransform::Upper)
        .foreground(colors.muted)
}

/// Icon color with more contrast than `muted`, for rails and toolbars at rest.
pub fn icon_fg(colors: &ColorRoles) -> Color {
    colors.text.mix_oklab(colors.muted, 0.28)
}

/// Crisp 1px hairline between surfaces.
pub fn hairline(colors: &ColorRoles) -> Color {
    colors
        .line
        .unwrap_or_else(|| colors.muted.mix_oklab(colors.bg, 0.35))
}

/// Parsed depth colors from the active theme pack (Dark Forest).
pub fn depth() -> DepthColors {
    DepthColors::from_pack(theme_pack::depth())
}

fn is_dark_forest(colors: &ColorRoles) -> bool {
    matches!(
        colors.bg.to_hex().to_ascii_uppercase().as_str(),
        "#040A09"
    )
}

/// Dark Forest uses the pack; other palettes derive planes and bevels from the theme.
pub fn depth_for(colors: &ColorRoles) -> DepthColors {
    if is_dark_forest(colors) {
        return depth();
    }
    let line = hairline(colors);
    DepthColors {
        raised: colors.card,
        overlay: colors.card.mix_oklab(colors.text, 0.08),
        highlight: line.mix_oklab(colors.text, 0.28),
        well: colors.bg.mix_oklab(Color::BLACK, 0.28),
        earth: colors.accent,
        shadow: colors.bg.mix_oklab(Color::BLACK, 0.45),
    }
}

/// Solid hex colors for planes, bevels, and hard shadows.
#[derive(Clone, Copy, Debug)]
pub struct DepthColors {
    pub raised: Color,
    pub overlay: Color,
    pub highlight: Color,
    pub well: Color,
    pub earth: Color,
    pub shadow: Color,
}

impl DepthColors {
    fn from_pack(pack: &DepthTokens) -> Self {
        Self {
            raised: Color::from_hex(pack.raised).unwrap_or(Color::BLACK),
            overlay: Color::from_hex(pack.overlay).unwrap_or(Color::BLACK),
            highlight: Color::from_hex(pack.highlight).unwrap_or(Color::BLACK),
            well: Color::from_hex(pack.well).unwrap_or(Color::BLACK),
            earth: Color::from_hex(pack.earth).unwrap_or(Color::BLACK),
            shadow: Color::from_hex(pack.shadow).unwrap_or(Color::BLACK),
        }
    }
}

/// Window / void background.
pub fn surface_base(colors: &ColorRoles) -> Color {
    colors.bg
}

/// Recessed panels and sidebars, one step above the void.
pub fn surface_panel(colors: &ColorRoles) -> Color {
    colors.panel
}

/// Raised cards, presence frames, and settings blocks.
pub fn surface_raised(colors: &ColorRoles) -> Color {
    depth_for(colors).raised
}

/// Overlay cards, menus, and popovers.
pub fn surface_overlay(colors: &ColorRoles) -> Color {
    depth_for(colors).overlay
}

/// Fill for the rounded Actions and Chat panes, sitting on the well.
pub fn pane_fill(colors: &ColorRoles) -> Color {
    colors.panel.mix_oklab(colors.bg, 0.06)
}

/// Selected row or chip, accent used sparingly.
pub fn selected_fill(colors: &ColorRoles) -> Color {
    colors.card.mix_oklab(colors.accent, 0.16)
}

/// Hard 2px solid offset. Blur stays zero.
pub fn elevation(colors: &ColorRoles) -> Shadow {
    let d = depth_for(colors);
    let off = theme_pack::current().chrome.elevation_offset;
    Shadow::new(off, off, theme_pack::current().chrome.elevation_blur, d.shadow)
}

/// A slightly longer hard offset for menus and overlays.
pub fn elevation_overlay(colors: &ColorRoles) -> Shadow {
    let d = depth_for(colors);
    let off = theme_pack::current().chrome.elevation_offset + 1.0;
    Shadow::new(off, off, 0.0, d.shadow)
}

fn bevel_edges(highlight: Color, shade: Color) -> StylePatch {
    StylePatch::new()
        .border_width(1.0)
        .border_side(BorderEdge::Top, BorderSide::new(1.0, highlight))
        .border_side(BorderEdge::Start, BorderSide::new(1.0, highlight))
        .border_side(BorderEdge::Bottom, BorderSide::new(1.0, shade))
        .border_side(BorderEdge::End, BorderSide::new(1.0, shade))
}

/// Raised plane: line box, lit top/start, shaded bottom/end, hard shadow.
pub fn raised_surface(colors: &ColorRoles) -> StylePatch {
    let d = depth_for(colors);
    bevel_edges(d.highlight, d.well)
        .background(surface_raised(colors))
        .border(hairline(colors))
        .shadow(elevation(colors))
        .radius(2.0)
}

/// Overlay plane for menus and popovers.
pub fn overlay_surface(colors: &ColorRoles) -> StylePatch {
    let d = depth_for(colors);
    bevel_edges(d.highlight, d.well)
        .background(surface_overlay(colors))
        .border(hairline(colors))
        .shadow(elevation_overlay(colors))
        .radius(2.0)
}

/// Panel sitting on the void: hairline only, no drop shadow.
pub fn panel_surface(colors: &ColorRoles) -> StylePatch {
    StylePatch::new()
        .background(surface_panel(colors))
        .border(hairline(colors))
        .border_width(1.0)
        .radius(2.0)
}

/// Sunken well for inputs, code, and the chat composer.
pub fn well_surface(colors: &ColorRoles) -> StylePatch {
    let d = depth_for(colors);
    bevel_edges(d.well, d.highlight)
        .background(colors.bg)
        .border(hairline(colors))
        .radius(2.0)
}

/// Hover: lift the plane and use the earth edge.
pub fn hover_edge(colors: &ColorRoles) -> StylePatch {
    let d = depth_for(colors);
    raised_surface(colors).border(d.earth)
}

/// Pressed: inset, earth edge, no drop shadow.
pub fn pressed_surface(colors: &ColorRoles) -> StylePatch {
    let d = depth_for(colors);
    well_surface(colors).border(d.earth).shadow(Shadow::NONE)
}

/// Selected: green accent edge on a raised plane (earth for hover).
pub fn selected_surface(colors: &ColorRoles) -> StylePatch {
    raised_surface(colors).border(colors.accent)
}

/// Presence / face frame in Grove.
pub fn presence_frame(colors: &ColorRoles) -> StylePatch {
    raised_surface(colors)
}


/// Selected chip: accent hairline and text, no fill. Unselected: quiet hairline.
pub fn chip(colors: &ColorRoles, on: bool) -> StylePatch {
    let line = hairline(colors);
    let p = StylePatch::new()
        .radius(0.0)
        .border_width(1.0)
        .background(colors.bg.with_alpha(0.0));
    if on {
        p.foreground(colors.accent).border(colors.accent)
    } else {
        p.foreground(colors.text).border(line)
    }
}

/// A primary action in the hairline system: accent stroke and text, no fill.
pub fn primary_button(colors: &ColorRoles) -> StylePatch {
    chip(colors, true).weight(600.0).padding(Insets {
        top: SPACE * 2.0,
        end: SPACE * 3.0,
        bottom: SPACE * 2.0,
        start: SPACE * 3.0,
    })
}

/// Session metadata and similar quiet lines: muted small caps.
pub fn meta_caps(colors: &ColorRoles) -> StylePatch {
    typo(META)
        .text_transform(TextTransform::Upper)
        .letter_spacing(LetterSpacing::Em(0.06))
        .foreground(colors.muted)
}

/// A flat, hairline info panel. Title uses the UI kicker, not the display face.
pub fn info_panel(ui: &remedy_ui::Ui, title: impl Into<String>, body: remedy_ui::El) -> remedy_ui::El {
    use remedy_ui::layout::Length;
    use remedy_ui::widgets as w;
    let colors = ui.theme().colors.clone();
    let title = title.into();
    w::column(ui)
        .gap(SPACE * 2.0)
        .padding(Insets::all(SPACE * 3.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(raised_surface(&colors))
        .child(
            w::text(ui, title).style(section_kicker(&colors)).layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            }),
        )
        .child(body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scale_steps_are_distinct() {
        assert_eq!(HERO.size, 24.0);
        assert_eq!(TITLE.size, 16.0);
        assert_eq!(META.size, 12.0);
        assert_eq!(MONO.size, 12.0);
        assert_eq!(THINKING.size, 14.0);
        assert!(HERO.size > TITLE.size);
        assert!(TITLE.size > BODY.size);
        assert!(BODY.size > THINKING.size);
        assert!(THINKING.size > MONO.size);
        assert!(HERO.display && TITLE.display);
        assert!(!SECTION.display && !BODY.display && !MONO.display);
        assert_eq!(HERO.size % 8.0, 0.0);
        assert_eq!(TITLE.size % 8.0, 0.0);
        assert!(TITLE.weight > BODY.weight);
    }
}
