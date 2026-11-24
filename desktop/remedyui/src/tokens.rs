//! Shared spacing, type, surfaces and elevation for the Studio shell.

use remedy_ui::core::{Color, Insets};
use remedy_ui::style::{
    ColorRoles, LetterSpacing, LineHeight, Shadow, StylePatch, TextTransform,
};

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
/// Circular persona badge.
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

/// Session titles and empty-state headlines.
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
    size: 11.5,
    weight: 500.0,
    leading: 1.35,
    tracking: 0.02,
    mono: false,
    display: false,
};
/// Tool names, arguments, and other code-like rows.
pub const MONO: Type = Type {
    size: 12.5,
    weight: 500.0,
    leading: 1.45,
    tracking: 0.0,
    mono: true,
    display: false,
};
/// Thinking paragraphs: a half-step quieter than body.
pub const THINKING: Type = Type {
    size: 13.5,
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

/// Window / well background.
pub fn surface_base(colors: &ColorRoles) -> Color {
    colors.bg
}

/// Raised panels and sidebars.
pub fn surface_raised(colors: &ColorRoles) -> Color {
    colors.panel
}

/// Overlay cards, menus, and assistant bubbles.
pub fn surface_overlay(colors: &ColorRoles) -> Color {
    colors.card
}

/// Fill for the rounded Actions and Chat panes, sitting on the well.
pub fn pane_fill(colors: &ColorRoles) -> Color {
    colors.panel.mix_oklab(colors.bg, 0.06)
}

/// Selected row or chip, accent used sparingly.
pub fn selected_fill(colors: &ColorRoles) -> Color {
    colors.card.mix_oklab(colors.accent, 0.16)
}

/// A soft lift under the composer and menus.
pub fn elevation(colors: &ColorRoles) -> Shadow {
    let ink = colors.text.mix_oklab(colors.bg, 0.15);
    Shadow::new(0.0, 1.0, 0.0, ink.with_alpha(0.35))
}

/// A slightly stronger lift for menus and overlays.
pub fn elevation_overlay(colors: &ColorRoles) -> Shadow {
    let ink = colors.text.mix_oklab(colors.bg, 0.12);
    Shadow::new(0.0, 2.0, 0.0, ink.with_alpha(0.40))
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
        .padding(Insets::all(SPACE * 2.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .border(hairline(&colors))
                .border_width(1.0)
                .radius(0.0),
        )
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
        assert!(TITLE.size > BODY.size);
        assert!(BODY.size > THINKING.size);
        assert!(THINKING.size > MONO.size);
        assert!(MONO.size > META.size);
        assert_eq!(TITLE.size, 16.0);
        assert!(TITLE.display);
        assert!(!SECTION.display);
        assert!(SECTION.size < BODY.size);
        assert!(TITLE.weight > BODY.weight);
    }
}
