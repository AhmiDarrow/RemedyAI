//! The Remedy circuit-R monogram, bundled with the app.

use std::sync::OnceLock;

use remedy_ui::image::decode;
use remedy_ui::paint::Image;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

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

/// The 88 px monogram used on the empty well, theme-aware (light strokes on dark).
pub fn empty_mark(ui: &Ui) -> El {
    let dark = ui.theme().derive().dark;
    if let Some(image) = monogram(dark) {
        w::image(ui, image, "Remedy").layout(|l| {
            l.width = remedy_ui::layout::Length::Px(88.0);
            l.height = remedy_ui::layout::Length::Px(88.0);
            l.shrink = 0.0;
        })
    } else {
        w::text(ui, "R").variant("heading")
    }
}
