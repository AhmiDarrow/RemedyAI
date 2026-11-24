//! Pixel-crafted marks for Grove starter cards and quiet status. Hairline
//! geometry in the same 24-unit language as presence — not emoji.

use remedy_ui::core::Color;
use remedy_ui::layout::{Length, Position};
use remedy_ui::style::StylePatch;
use remedy_ui::{El, Ui};

/// Starter-card marks. Names describe the errand, not a cartoon.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlotMark {
    Receipts,
    Trip,
    Resume,
    Usuals,
    /// A small pine, used where a plant or growth cue is needed.
    Pine,
}

pub fn mark(ui: &Ui, kind: PlotMark, size: f32) -> El {
    let size = size.max(16.0);
    let scale = size / 24.0;
    let colors = ui.theme().colors.clone();
    let ink = colors.accent;
    let line = colors.line.unwrap_or(colors.muted);
    let host = ui
        .el("grove-mark")
        .layout(move |l| {
            l.position = Position::Relative;
            l.width = Length::Px(size);
            l.height = Length::Px(size);
            l.min_width = Length::Px(size);
            l.min_height = Length::Px(size);
            l.shrink = 0.0;
        });
    match kind {
        PlotMark::Receipts => receipts(ui, &host, scale, line, ink),
        PlotMark::Trip => trip(ui, &host, scale, line, ink),
        PlotMark::Resume => resume(ui, &host, scale, line, ink),
        PlotMark::Usuals => usuals(ui, &host, scale, line, ink),
        PlotMark::Pine => pine(ui, &host, scale, ink),
    }
    host
}

fn receipts(ui: &Ui, host: &El, scale: f32, line: Color, ink: Color) {
    // Two slightly offset slips, hairline, with a total mark.
    rect(ui, host, 5.0, 4.0, 12.0, 14.0, scale, line);
    rect(ui, host, 7.0, 6.0, 12.0, 14.0, scale, line);
    px(ui, host, 9.0, 10.0, 8.0, 1.0, scale, ink);
    px(ui, host, 9.0, 13.0, 6.0, 1.0, scale, ink);
    px(ui, host, 9.0, 16.0, 4.0, 1.0, scale, ink);
}

fn trip(ui: &Ui, host: &El, scale: f32, line: Color, ink: Color) {
    // Stepped hills and a path — a weekend landscape, not a plane.
    // Back peak.
    px(ui, host, 13.0, 8.0, 2.0, 1.0, scale, line);
    px(ui, host, 11.0, 9.0, 6.0, 1.0, scale, line);
    px(ui, host, 9.0, 10.0, 10.0, 1.0, scale, line);
    // Front peak, accent.
    px(ui, host, 6.0, 11.0, 2.0, 1.0, scale, ink);
    px(ui, host, 4.0, 12.0, 6.0, 1.0, scale, ink);
    px(ui, host, 3.0, 13.0, 8.0, 1.0, scale, ink);
    // Ground and a stepped path.
    px(ui, host, 3.0, 16.0, 18.0, 1.0, scale, line);
    px(ui, host, 8.0, 15.0, 2.0, 1.0, scale, ink);
    px(ui, host, 11.0, 14.0, 2.0, 1.0, scale, ink);
    px(ui, host, 14.0, 15.0, 2.0, 1.0, scale, ink);
}

fn resume(ui: &Ui, host: &El, scale: f32, line: Color, ink: Color) {
    rect(ui, host, 6.0, 3.0, 12.0, 18.0, scale, line);
    // Stepped corner fold.
    px(ui, host, 15.0, 3.0, 3.0, 1.0, scale, line);
    px(ui, host, 16.0, 4.0, 2.0, 1.0, scale, line);
    px(ui, host, 17.0, 5.0, 1.0, 1.0, scale, line);
    px(ui, host, 8.0, 8.0, 8.0, 1.0, scale, ink);
    px(ui, host, 8.0, 11.0, 8.0, 1.0, scale, ink);
    px(ui, host, 8.0, 14.0, 6.0, 1.0, scale, ink);
}

fn usuals(ui: &Ui, host: &El, scale: f32, line: Color, ink: Color) {
    // A basket: handle, then a stepped bowl.
    px(ui, host, 8.0, 5.0, 1.0, 4.0, scale, line);
    px(ui, host, 8.0, 5.0, 8.0, 1.0, scale, line);
    px(ui, host, 15.0, 5.0, 1.0, 4.0, scale, line);
    px(ui, host, 5.0, 9.0, 14.0, 1.0, scale, ink);
    px(ui, host, 4.0, 10.0, 1.0, 6.0, scale, line);
    px(ui, host, 19.0, 10.0, 1.0, 6.0, scale, line);
    px(ui, host, 5.0, 16.0, 14.0, 1.0, scale, line);
    // Stepped sides, hairline only.
    px(ui, host, 5.0, 15.0, 1.0, 1.0, scale, line);
    px(ui, host, 18.0, 15.0, 1.0, 1.0, scale, line);
}

fn pine(ui: &Ui, host: &El, scale: f32, color: Color) {
    px(ui, host, 11.0, 3.0, 2.0, 1.0, scale, color);
    px(ui, host, 9.0, 4.0, 6.0, 1.0, scale, color);
    px(ui, host, 7.0, 5.0, 10.0, 1.0, scale, color);
    px(ui, host, 9.0, 6.0, 6.0, 1.0, scale, color);
    px(ui, host, 6.0, 7.0, 12.0, 1.0, scale, color);
    px(ui, host, 8.0, 8.0, 8.0, 1.0, scale, color);
    px(ui, host, 4.0, 9.0, 16.0, 1.0, scale, color);
    px(ui, host, 11.0, 10.0, 2.0, 6.0, scale, color);
}

fn rect(ui: &Ui, host: &El, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) {
    px(ui, host, x, y, w, 1.0, scale, color);
    px(ui, host, x, y + h - 1.0, w, 1.0, scale, color);
    px(ui, host, x, y, 1.0, h, scale, color);
    px(ui, host, x + w - 1.0, y, 1.0, h, scale, color);
}

fn px(ui: &Ui, host: &El, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) {
    let _ = host.clone().child(px_el(ui, x, y, w, h, scale, color));
}

fn px_el(ui: &Ui, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) -> El {
    ui.el("px")
        .style(StylePatch::new().background(color))
        .layout(move |l| {
            l.position = Position::Absolute;
            l.inset.start = (x * scale).round();
            l.inset.top = (y * scale).round();
            l.width = Length::Px((w * scale).round().max(1.0));
            l.height = Length::Px((h * scale).round().max(1.0));
            l.shrink = 0.0;
        })
}
