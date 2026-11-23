//! Dark Forest splash. It ignores the saved theme so a light theme never flashes first.

use remedy_ui::core::Color;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::model::{SPLASH_CONNECTING, SPLASH_FAILED, SPLASH_READY, SPLASH_STARTING};
use crate::logo;

fn forest(hex: &str, fallback: (u8, u8, u8)) -> Color {
    Color::from_hex(hex).unwrap_or(Color::rgb8(fallback.0, fallback.1, fallback.2))
}

pub fn splash_view(ui: &Ui, status: Signal<String>) -> El {
    let bg = forest("#0a0e0b", (10, 14, 11));
    let fg = forest("#e6ebe7", (230, 235, 231));
    let muted = forest("#9aa89e", (154, 168, 158));
    let danger = forest("#f87171", (248, 113, 113));
    let status2 = status.clone();
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(ui.theme().spacing.unit * 2.5)
        .style(StylePatch::new().background(bg))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(logo::wordmark(ui, 72.0))
        .child(status_line(ui, status2, muted, danger, fg))
}

fn status_line(ui: &Ui, status: Signal<String>, muted: Color, danger: Color, fg: Color) -> El {
    let status_color = status.clone();
    let line_el = w::text(ui, "")
        .variant("muted")
        .text(move || line(&status.get()))
        .style(StylePatch::new().foreground(fg));
    let painted = line_el.clone();
    line_el.bind(move || {
        let color = if status_color.get() == "error" { danger } else { muted };
        painted.style(StylePatch::new().foreground(color));
    });
    line_el
}

fn line(status: &str) -> String {
    match status {
        "connecting" => SPLASH_CONNECTING.into(),
        "ready" => SPLASH_READY.into(),
        "error" => SPLASH_FAILED.into(),
        _ => SPLASH_STARTING.into(),
    }
}
