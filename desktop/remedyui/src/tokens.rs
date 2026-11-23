//! Shared spacing, type, radius and elevation for the Studio shell.

use remedy_ui::core::Color;
use remedy_ui::style::{ColorRoles, Shadow};

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

/// Icon color with more contrast than `muted`, for rails and toolbars at rest.
pub fn icon_fg(colors: &ColorRoles) -> Color {
    colors.text.mix_oklab(colors.muted, 0.28)
}

/// A soft lift under the composer and menus.
pub fn elevation(colors: &ColorRoles) -> Shadow {
    let ink = colors.text.mix_oklab(colors.bg, 0.15);
    Shadow::new(0.0, 1.0, 10.0, ink.with_alpha(0.22))
}
