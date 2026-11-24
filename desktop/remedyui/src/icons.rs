//! Shell glyphs that the pinned RemedyUI icon set does not yet include (Mic, Paperclip,
//! Stop, Star). Strokes match the engine's 24-unit grid, 2 px round stroke, so they sit
//! with [`w::Icon`]. When the engine gains these variants and the pin moves, call sites
//! can switch to `w::Icon` directly.

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Point;
use remedy_ui::layout::Length;
use remedy_ui::paint::{Brush, Item, Path, Stroke, StrokeCap, StrokeJoin};
use remedy_ui::widgets as w;
use remedy_ui::{El, PaintCx, Ui};

/// A toolbar glyph: a stock engine icon, or one of the shell glyphs.
#[derive(Clone, Copy, Debug)]
pub enum Glyph {
    Stock(w::Icon),
    Mic,
    Paperclip,
    Stop,
    Star,
    ThumbUp,
    ThumbDown,
    Heart,
    BrokenHeart,
    /// A simple face: the "Add reaction" trigger, never an emoji glyph.
    AddReaction,
}

impl From<w::Icon> for Glyph {
    fn from(icon: w::Icon) -> Self {
        Glyph::Stock(icon)
    }
}

/// An icon `size` logical pixels square, decorative (its parent names it).
pub fn glyph(ui: &Ui, icon: impl Into<Glyph>, size: f32) -> El {
    match icon.into() {
        Glyph::Stock(stock) => w::icon(ui, stock, size),
        Glyph::Mic => paint_glyph(ui, strokes_mic, size),
        Glyph::Paperclip => paint_glyph(ui, strokes_paperclip, size),
        Glyph::Stop => paint_glyph(ui, strokes_stop, size),
        Glyph::Star => paint_glyph(ui, strokes_star, size),
        Glyph::ThumbUp => paint_glyph(ui, strokes_thumb_up, size),
        Glyph::ThumbDown => paint_glyph(ui, strokes_thumb_down, size),
        Glyph::Heart => paint_glyph(ui, strokes_heart, size),
        Glyph::BrokenHeart => paint_glyph(ui, strokes_broken_heart, size),
        Glyph::AddReaction => paint_glyph(ui, strokes_add_reaction, size),
    }
}

/// The Dark Forest mark for a closed reaction, if `emoji` is one of the four.
pub fn reaction_glyph(emoji: &str) -> Option<Glyph> {
    match emoji {
        "👍" => Some(Glyph::ThumbUp),
        "👎" => Some(Glyph::ThumbDown),
        "❤️" | "❤" => Some(Glyph::Heart),
        "💔" => Some(Glyph::BrokenHeart),
        _ => None,
    }
}

fn paint_glyph(ui: &Ui, strokes: fn() -> Path, size: f32) -> El {
    ui.el("icon")
        .width(size)
        .height(size)
        .layout(|l| {
            l.width = Length::Px(size);
            l.height = Length::Px(size);
            l.min_width = Length::Px(size);
            l.min_height = Length::Px(size);
            l.shrink = 0.0;
        })
        .paint(move |cx| draw(strokes(), cx))
        .semantics(Semantics::new(Role::Image).hidden(true))
}

fn p(x: f32, y: f32) -> Point {
    Point::new(x, y)
}

fn arc(path: &mut Path, c: Point, r: f32, from: f32, to: f32) {
    let steps = 24;
    for i in 0..=steps {
        let a = from + (to - from) * i as f32 / steps as f32;
        let pt = p(c.x + r * a.cos(), c.y + r * a.sin());
        if i == 0 {
            path.move_to(pt);
        } else {
            path.line_to(pt);
        }
    }
}

fn strokes_mic() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    arc(&mut s, p(12.0, 6.0), 4.0, PI, 2.0 * PI);
    s.move_to(p(16.0, 6.0)).line_to(p(16.0, 11.0));
    arc(&mut s, p(12.0, 11.0), 4.0, 0.0, PI);
    s.move_to(p(8.0, 11.0)).line_to(p(8.0, 6.0));
    arc(&mut s, p(12.0, 11.0), 7.0, 0.0, PI);
    s.move_to(p(12.0, 18.0)).line_to(p(12.0, 21.0));
    s.move_to(p(8.0, 21.0)).line_to(p(16.0, 21.0));
    s
}

fn strokes_paperclip() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    // Inner tip, up the inner wire, tight top, down, wider bottom, up the outer wire.
    s.move_to(p(15.0, 15.5)).line_to(p(15.0, 7.0));
    arc(&mut s, p(12.0, 7.0), 3.0, 0.0, -PI);
    s.line_to(p(9.0, 17.0));
    arc(&mut s, p(13.0, 17.0), 4.0, PI, 2.0 * PI);
    s.line_to(p(17.0, 6.5));
    s
}

fn strokes_stop() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    s.move_to(p(8.0, 6.0)).line_to(p(16.0, 6.0));
    arc(&mut s, p(16.0, 8.0), 2.0, -PI * 0.5, 0.0);
    s.line_to(p(18.0, 16.0));
    arc(&mut s, p(16.0, 16.0), 2.0, 0.0, PI * 0.5);
    s.line_to(p(8.0, 18.0));
    arc(&mut s, p(8.0, 16.0), 2.0, PI * 0.5, PI);
    s.line_to(p(6.0, 8.0));
    arc(&mut s, p(8.0, 8.0), 2.0, PI, PI * 1.5);
    s.close();
    s
}

fn strokes_star() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    let (cx, cy, ro, ri) = (12.0, 12.2, 9.5, 3.8);
    for i in 0..10 {
        let a = -PI * 0.5 + i as f32 * PI * 0.2;
        let r = if i % 2 == 0 { ro } else { ri };
        let pt = p(cx + r * a.cos(), cy + r * a.sin());
        if i == 0 {
            s.move_to(pt);
        } else {
            s.line_to(pt);
        }
    }
    s.close();
    s
}

fn strokes_thumb_up() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    // Fist.
    s.move_to(p(7.0, 12.0)).line_to(p(17.0, 12.0));
    arc(&mut s, p(17.0, 14.0), 2.0, -PI * 0.5, 0.0);
    s.line_to(p(19.0, 19.0));
    arc(&mut s, p(17.0, 19.0), 2.0, 0.0, PI * 0.5);
    s.line_to(p(7.0, 21.0));
    arc(&mut s, p(7.0, 19.0), 2.0, PI * 0.5, PI);
    s.line_to(p(5.0, 14.0));
    arc(&mut s, p(7.0, 14.0), 2.0, PI, PI * 1.5);
    s.close();
    // Thumb.
    s.move_to(p(10.5, 12.0)).line_to(p(10.5, 5.5));
    arc(&mut s, p(12.5, 5.5), 2.0, PI, 2.0 * PI);
    s.line_to(p(14.5, 12.0));
    s
}

fn strokes_thumb_down() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    // Fist (mirrored over the mid line of thumb-up).
    s.move_to(p(7.0, 12.0)).line_to(p(17.0, 12.0));
    arc(&mut s, p(17.0, 10.0), 2.0, PI * 0.5, 0.0);
    s.line_to(p(19.0, 5.0));
    arc(&mut s, p(17.0, 5.0), 2.0, 0.0, -PI * 0.5);
    s.line_to(p(7.0, 3.0));
    arc(&mut s, p(7.0, 5.0), 2.0, -PI * 0.5, -PI);
    s.line_to(p(5.0, 10.0));
    arc(&mut s, p(7.0, 10.0), 2.0, PI, PI * 0.5);
    s.close();
    // Thumb.
    s.move_to(p(10.5, 12.0)).line_to(p(10.5, 18.5));
    arc(&mut s, p(12.5, 18.5), 2.0, PI, 0.0);
    s.line_to(p(14.5, 12.0));
    s
}

fn strokes_heart() -> Path {
    let mut s = Path::new();
    s.move_to(p(12.0, 20.0));
    s.cubic_to(p(3.0, 13.0), p(4.5, 5.5), p(12.0, 9.0));
    s.cubic_to(p(19.5, 5.5), p(21.0, 13.0), p(12.0, 20.0));
    s.close();
    s
}

fn strokes_broken_heart() -> Path {
    let mut s = Path::new();
    // Left lobe, ending at the crack.
    s.move_to(p(12.0, 20.0));
    s.cubic_to(p(3.0, 13.0), p(4.5, 5.5), p(12.0, 9.0));
    s.line_to(p(10.5, 11.5));
    s.line_to(p(13.0, 14.0));
    s.line_to(p(11.0, 16.5));
    s.line_to(p(12.0, 20.0));
    s.close();
    // Right lobe, starting at the crack.
    s.move_to(p(12.0, 9.0));
    s.cubic_to(p(19.5, 5.5), p(21.0, 13.0), p(12.0, 20.0));
    s.line_to(p(11.0, 16.5));
    s.line_to(p(13.0, 14.0));
    s.line_to(p(10.5, 11.5));
    s.line_to(p(12.0, 9.0));
    s.close();
    s
}

fn strokes_add_reaction() -> Path {
    use std::f32::consts::PI;
    let mut s = Path::new();
    // Face.
    arc(&mut s, p(12.0, 12.0), 8.0, 0.0, 2.0 * PI);
    // Eyes.
    s.move_to(p(9.0, 9.5)).line_to(p(9.0, 11.0));
    s.move_to(p(15.0, 9.5)).line_to(p(15.0, 11.0));
    // Smile (downward bulge; y grows downward).
    arc(&mut s, p(12.0, 12.5), 4.0, PI * 0.2, PI * 0.8);
    s
}

fn draw(path: Path, cx: &mut PaintCx<'_>) {
    let size = cx.size.width.min(cx.size.height);
    if size <= 0.0 {
        return;
    }
    let k = size / 24.0;
    let ox = (cx.size.width - size) * 0.5;
    let oy = (cx.size.height - size) * 0.5;
    let mut scaled = Path::new();
    for el in path.elements() {
        let t = |q: Point| p(ox + q.x * k, oy + q.y * k);
        match *el {
            remedy_ui::paint::PathEl::MoveTo(a) => {
                scaled.move_to(t(a));
            }
            remedy_ui::paint::PathEl::LineTo(a) => {
                scaled.line_to(t(a));
            }
            remedy_ui::paint::PathEl::QuadTo(a, b) => {
                scaled.quad_to(t(a), t(b));
            }
            remedy_ui::paint::PathEl::CubicTo(a, b, c) => {
                scaled.cubic_to(t(a), t(b), t(c));
            }
            remedy_ui::paint::PathEl::Close => {
                scaled.close();
            }
        }
    }
    let stroke = Stroke {
        width: (2.0 * k).max(1.0),
        cap: StrokeCap::Round,
        join: StrokeJoin::Round,
        dashes: Vec::new(),
    };
    cx.list.push(Item::StrokePath {
        path: scaled,
        stroke,
        brush: Brush::Solid(cx.style.foreground),
    });
}

#[cfg(test)]
mod tests {
    use super::{
        reaction_glyph, strokes_add_reaction, strokes_broken_heart, strokes_heart,
        strokes_mic, strokes_paperclip, strokes_star, strokes_stop, strokes_thumb_down,
        strokes_thumb_up,
    };
    use remedy_ui::paint::PathEl;

    fn assert_in_grid(name: &str, path: remedy_ui::paint::Path) {
        assert!(path.elements().len() > 1, "{name} draws nothing");
        for el in path.elements() {
            if let PathEl::MoveTo(q) | PathEl::LineTo(q) = *el {
                assert!(
                    (0.0..=24.0).contains(&q.x) && (0.0..=24.0).contains(&q.y),
                    "{name} leaves the grid at {q:?}"
                );
            }
        }
    }

    #[test]
    fn shell_glyphs_stay_on_the_24_grid() {
        assert_in_grid("Mic", strokes_mic());
        assert_in_grid("Paperclip", strokes_paperclip());
        assert_in_grid("Stop", strokes_stop());
        assert_in_grid("Star", strokes_star());
        assert_in_grid("ThumbUp", strokes_thumb_up());
        assert_in_grid("ThumbDown", strokes_thumb_down());
        assert_in_grid("Heart", strokes_heart());
        assert_in_grid("BrokenHeart", strokes_broken_heart());
        assert_in_grid("AddReaction", strokes_add_reaction());
    }

    #[test]
    fn reaction_glyphs_cover_the_closed_set() {
        assert!(matches!(reaction_glyph("👍"), Some(super::Glyph::ThumbUp)));
        assert!(matches!(reaction_glyph("👎"), Some(super::Glyph::ThumbDown)));
        assert!(matches!(reaction_glyph("❤️"), Some(super::Glyph::Heart)));
        assert!(matches!(reaction_glyph("❤"), Some(super::Glyph::Heart)));
        assert!(matches!(reaction_glyph("💔"), Some(super::Glyph::BrokenHeart)));
        assert!(reaction_glyph("☺").is_none());
    }
}
