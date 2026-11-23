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
    mark(ui, 88.0)
}

/// Framed-size monogram for the house-rules pop-up and the name prompt.
pub fn mark(ui: &Ui, size: f32) -> El {
    let dark = ui.theme().derive().dark;
    if let Some(image) = monogram(dark) {
        w::image(ui, image, "Remedy").layout(|l| {
            l.width = remedy_ui::layout::Length::Px(size);
            l.height = remedy_ui::layout::Length::Px(size);
            l.shrink = 0.0;
        })
    } else {
        w::text(ui, "R").variant("heading")
    }
}

fn wordmark_image() -> Option<(Image, f32)> {
    static LOGO: OnceLock<Option<(Image, f32)>> = OnceLock::new();
    LOGO.get_or_init(|| {
        let decoded = decode(include_bytes!("../../public/logo.png"), None).ok()?;
        let w = decoded.image.width.max(1);
        let h = decoded.image.height.max(1);
        let aspect = w as f32 / h as f32;
        let image = Image::new(w, h, decoded.image.pixels);
        Some((image, aspect))
    })
    .clone()
}

/// The wide Remedy wordmark. `height` is in px; width follows the image.
pub fn wordmark(ui: &Ui, height: f32) -> El {
    if let Some((image, aspect)) = wordmark_image() {
        let width = (height * aspect).clamp(height, 280.0);
        w::image(ui, image, "Remedy").layout(move |l| {
            l.width = remedy_ui::layout::Length::Px(width);
            l.height = remedy_ui::layout::Length::Px(height);
            l.shrink = 0.0;
        })
    } else {
        w::text(ui, "Remedy").variant("heading")
    }
}
