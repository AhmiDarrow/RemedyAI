//! The Remedy circuit-R monogram, bundled with the app.

use std::sync::OnceLock;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::image::decode;
use remedy_ui::layout::{Align, Justify};
use remedy_ui::paint::Image;
use remedy_ui::style::{LetterSpacing, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::tokens;

const LIGHT_ON_DARK: &[u8] = include_bytes!("../assets/icon-mono-light.png");
const DARK_ON_LIGHT: &[u8] = include_bytes!("../assets/icon-mono-dark.png");

fn face(bytes: &'static [u8]) -> Option<Image> {
    let decoded = decode(bytes, Some((88, 88))).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}

fn monogram(dark: bool) -> Option<Image> {
    static LIGHT: OnceLock<Option<Image>> = OnceLock::new();
    static DARK: OnceLock<Option<Image>> = OnceLock::new();
    if dark {
        LIGHT.get_or_init(|| face(LIGHT_ON_DARK)).clone()
    } else {
        DARK.get_or_init(|| face(DARK_ON_LIGHT)).clone()
    }
}

/// The circuit-R monogram at `size` px, theme-aware (light strokes on a dark theme).
pub fn mark(ui: &Ui, size: f32) -> El {
    let dark = ui.theme().derive().dark;
    if let Some(image) = monogram(dark) {
        w::image(ui, image, "Remedy").layout(move |l| {
            l.width = remedy_ui::layout::Length::Px(size);
            l.height = remedy_ui::layout::Length::Px(size);
            l.shrink = 0.0;
        })
    } else {
        w::text(ui, "R").variant("heading")
    }
}

/// The 88 px monogram used on the empty well, theme-aware (light strokes on dark).
pub fn empty_mark(ui: &Ui) -> El {
    mark(ui, 88.0)
}

/// A circular persona: the Remedy mark, or initials for a person.
pub fn persona(ui: &Ui, name: &str, user: bool, size: f32) -> El {
    let colors = ui.theme().colors.clone();
    let (bg, border) = if user {
        (
            colors.accent,
            colors.accent.mix_oklab(colors.bg, 0.18),
        )
    } else {
        (
            colors.card.mix_oklab(colors.accent, 0.2),
            tokens::hairline(&colors),
        )
    };
    let face = if user {
        let initials: String = name
            .split_whitespace()
            .filter_map(|w| w.chars().next())
            .take(2)
            .flat_map(char::to_uppercase)
            .collect();
        let initials = if initials.is_empty() {
            "?".into()
        } else {
            initials
        };
        w::text(ui, initials)
            .nowrap()
            .style(
                StylePatch::new()
                    .foreground(colors.on_accent)
                    .weight(600.0)
                    .text_size((size * 0.38).round())
                    .letter_spacing(LetterSpacing::Em(0.04)),
            )
    } else {
        mark(ui, (size * 0.58).round())
    };
    ui.el("persona")
        .width(size)
        .height(size)
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .style(
            StylePatch::new()
                .background(bg)
                .radius(size / 2.0)
                .border(border)
                .border_width(1.0),
        )
        .clip()
        .child(face)
        .semantics(Semantics::new(Role::Image).label(name.to_owned()))
        .layout(move |l| {
            l.width = remedy_ui::layout::Length::Px(size);
            l.height = remedy_ui::layout::Length::Px(size);
            l.min_width = remedy_ui::layout::Length::Px(size);
            l.min_height = remedy_ui::layout::Length::Px(size);
            l.shrink = 0.0;
        })
}
