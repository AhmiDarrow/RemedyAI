//! Forest-spirit presence forms, drawn on a 24-unit grid.
//!
//! Geometric quillwork and beadwork language: stepped triangles, diamonds,
//! and bands. A morning-star motif from Lakota star quilts. Small pine marks
//! for Black Hills forest growth. Symmetric, crisp, and handcrafted.
//!
//! Not used: sacred or ceremonial items (the pipe, eagle feathers, headdresses,
//! war bonnets), dreamcatcher decor, or cartoon stereotypes.

use remedy_ui::core::Color;
use remedy_ui::{El, Ui};

use super::presence::State;

/// A restrained quill/bead earth tone — a warm companion to forest green.
pub fn earth() -> Color {
    Color::from_hex("#C4A36A").unwrap_or(Color::rgb8(0xC4, 0xA3, 0x6A))
}

pub struct Palette {
    pub hair: Color,
    pub fill: Color,
    pub ink: Color,
    pub mute: Color,
    pub earth: Color,
}

pub fn palette(state: State, colors: &remedy_ui::style::ColorRoles) -> Palette {
    let earth = earth();
    let hair = match state {
        State::Error => colors.danger,
        State::Working | State::Listening | State::Done => colors.accent,
        _ => crate::tokens::hairline(colors),
    };
    let ink = match state {
        State::Error => colors.danger,
        _ => colors.accent,
    };
    Palette {
        hair,
        fill: colors.panel,
        ink,
        mute: colors.muted,
        earth,
    }
}

pub fn morning_star(ui: &Ui, host: &El, state: State, scale: f32, p: &Palette, blink: bool) {
    // Stepped frame. The morning star is the crown; the face sits in open air below.
    frame(ui, host, scale, p.hair, p.fill);
    let star_ink = match state {
        State::Idle => p.mute,
        State::Error => p.ink,
        _ => p.ink,
    };
    star8(ui, host, 11.0, 6.0, scale, star_ink, p.earth);
    // A short beadwork band, not a full collar.
    bead_band(ui, host, 7.0, 12.0, 10, scale, p.earth, p.mute);
    match state {
        State::Idle => {
            diamond_eyes(ui, host, 7.0, 14.0, scale, p.ink, 2, blink);
            px(ui, host, 10.0, 19.0, 4.0, 1.0, scale, p.mute);
        }
        State::Listening => {
            diamond_eyes(ui, host, 7.0, 14.0, scale, p.ink, 3, blink);
            px(ui, host, 11.0, 19.0, 2.0, 2.0, scale, p.mute);
        }
        State::Thinking => {
            diamond_eyes(ui, host, 8.0, 14.0, scale, p.ink, 2, blink);
            px(ui, host, 9.0, 20.0, 2.0, 1.0, scale, p.mute);
            px(ui, host, 12.0, 20.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 20.0, 2.0, 1.0, scale, p.mute);
        }
        State::Working => {
            px(ui, host, 6.0, 15.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 15.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 8.0, 20.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 11.0, 20.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 20.0, 2.0, 1.0, scale, p.mute);
        }
        State::Done => {
            diamond_eyes(ui, host, 7.0, 14.0, scale, p.ink, 3, blink);
            px(ui, host, 7.0, 20.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 9.0, 19.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 11.0, 18.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 13.0, 19.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 20.0, 2.0, 1.0, scale, p.ink);
        }
        State::Error => {
            px(ui, host, 6.0, 13.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 8.0, 14.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 14.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 16.0, 13.0, 2.0, 1.0, scale, p.ink);
            diamond_eyes(ui, host, 7.0, 15.0, scale, p.ink, 1, false);
            px(ui, host, 8.0, 19.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 10.0, 20.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 19.0, 2.0, 1.0, scale, p.ink);
        }
    }
}

pub fn wolf(ui: &Ui, host: &El, state: State, scale: f32, p: &Palette, blink: bool) {
    frame(ui, host, scale, p.hair, p.fill);
    // Ears. Positions change with the state.
    let (ly, ry, tall) = match state {
        State::Idle => (1.0, 1.0, 5.0),
        State::Listening | State::Done => (0.0, 0.0, 6.0),
        State::Thinking => (0.0, 3.0, 5.0),
        State::Working => (2.0, 2.0, 4.0),
        State::Error => (3.0, 3.0, 3.0),
    };
    ear_up(ui, host, 2.0, ly, tall, scale, p.hair, true);
    ear_up(ui, host, 16.0, ry, tall, scale, p.hair, false);
    // Cheek bead bands.
    px(ui, host, 2.0, 11.0, 3.0, 1.0, scale, p.earth);
    px(ui, host, 19.0, 11.0, 3.0, 1.0, scale, p.earth);
    match state {
        State::Idle => {
            eyes_pair(ui, host, 6.0, 10.0, 3.0, 2.0, scale, p.ink, blink);
            // Closed muzzle line.
            px(ui, host, 9.0, 16.0, 6.0, 1.0, scale, p.mute);
            nose(ui, host, 11.0, 14.0, scale, p.ink);
        }
        State::Listening => {
            eyes_pair(ui, host, 6.0, 9.0, 3.0, 3.0, scale, p.ink, blink);
            nose(ui, host, 11.0, 14.0, scale, p.ink);
            px(ui, host, 11.0, 16.0, 2.0, 2.0, scale, p.mute);
        }
        State::Thinking => {
            eyes_pair(ui, host, 8.0, 10.0, 3.0, 2.0, scale, p.ink, blink);
            nose(ui, host, 12.0, 14.0, scale, p.ink);
            px(ui, host, 9.0, 17.0, 2.0, 1.0, scale, p.mute);
            px(ui, host, 12.0, 17.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 17.0, 2.0, 1.0, scale, p.mute);
        }
        State::Working => {
            px(ui, host, 5.0, 11.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 11.0, 4.0, 1.0, scale, p.ink);
            nose(ui, host, 11.0, 14.0, scale, p.ink);
            px(ui, host, 8.0, 17.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 11.0, 17.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 17.0, 2.0, 1.0, scale, p.mute);
        }
        State::Done => {
            eyes_pair(ui, host, 6.0, 9.0, 3.0, 3.0, scale, p.ink, blink);
            nose(ui, host, 11.0, 14.0, scale, p.ink);
            px(ui, host, 8.0, 17.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 10.0, 16.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 17.0, 2.0, 1.0, scale, p.ink);
        }
        State::Error => {
            px(ui, host, 6.0, 9.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 8.0, 10.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 10.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 16.0, 9.0, 2.0, 1.0, scale, p.ink);
            eyes_pair(ui, host, 6.0, 11.0, 3.0, 1.0, scale, p.ink, false);
            nose(ui, host, 11.0, 14.0, scale, p.ink);
            px(ui, host, 8.0, 16.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 10.0, 17.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 16.0, 2.0, 1.0, scale, p.ink);
        }
    }
}

pub fn bear(ui: &Ui, host: &El, state: State, scale: f32, p: &Palette, blink: bool) {
    frame(ui, host, scale, p.hair, p.fill);
    // Broader, shorter ears. Perked when listening or done.
    let (ey, eh) = match state {
        State::Listening | State::Done => (1.0, 3.0),
        State::Error | State::Working => (3.0, 2.0),
        _ => (2.0, 3.0),
    };
    // Stepped "round" ears — squares with a cut corner, not circles.
    px(ui, host, 5.0, ey, 4.0, eh, scale, p.hair);
    px(ui, host, 6.0, ey - 1.0, 2.0, 1.0, scale, p.hair);
    px(ui, host, 15.0, ey, 4.0, eh, scale, p.hair);
    px(ui, host, 16.0, ey - 1.0, 2.0, 1.0, scale, p.hair);
    // A pine between the ears — forest, not a crown.
    let pine_color = if matches!(state, State::Idle) {
        p.mute
    } else if matches!(state, State::Error) {
        p.ink
    } else {
        p.earth
    };
    pine(ui, host, 10.0, 1.0, scale, pine_color);
    // Earth bead band across the brow.
    bead_band(ui, host, 4.0, 7.0, 16, scale, p.earth, p.mute);
    match state {
        State::Idle => {
            // Closer, smaller sleepy eyes.
            eyes_pair(ui, host, 7.0, 11.0, 2.0, 2.0, scale, p.ink, blink);
            px(ui, host, 10.0, 17.0, 4.0, 1.0, scale, p.mute);
            nose(ui, host, 11.0, 15.0, scale, p.ink);
        }
        State::Listening => {
            eyes_pair(ui, host, 7.0, 10.0, 2.0, 3.0, scale, p.ink, blink);
            nose(ui, host, 11.0, 15.0, scale, p.ink);
            px(ui, host, 11.0, 17.0, 2.0, 2.0, scale, p.mute);
        }
        State::Thinking => {
            eyes_pair(ui, host, 8.0, 11.0, 2.0, 2.0, scale, p.ink, blink);
            nose(ui, host, 12.0, 15.0, scale, p.ink);
            px(ui, host, 9.0, 18.0, 2.0, 1.0, scale, p.mute);
            px(ui, host, 12.0, 18.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 18.0, 2.0, 1.0, scale, p.mute);
        }
        State::Working => {
            px(ui, host, 6.0, 12.0, 3.0, 1.0, scale, p.ink);
            px(ui, host, 15.0, 12.0, 3.0, 1.0, scale, p.ink);
            nose(ui, host, 11.0, 15.0, scale, p.ink);
            px(ui, host, 8.0, 18.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 11.0, 18.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 18.0, 2.0, 1.0, scale, p.mute);
        }
        State::Done => {
            eyes_pair(ui, host, 7.0, 10.0, 2.0, 3.0, scale, p.ink, blink);
            nose(ui, host, 11.0, 15.0, scale, p.ink);
            px(ui, host, 8.0, 18.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 10.0, 17.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 18.0, 2.0, 1.0, scale, p.ink);
        }
        State::Error => {
            px(ui, host, 6.0, 10.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 8.0, 11.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 11.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 16.0, 10.0, 2.0, 1.0, scale, p.ink);
            eyes_pair(ui, host, 7.0, 12.0, 2.0, 1.0, scale, p.ink, false);
            nose(ui, host, 11.0, 15.0, scale, p.ink);
            px(ui, host, 8.0, 17.0, 2.0, 1.0, scale, p.ink);
            px(ui, host, 10.0, 18.0, 4.0, 1.0, scale, p.ink);
            px(ui, host, 14.0, 17.0, 2.0, 1.0, scale, p.ink);
        }
    }
}

fn frame(ui: &Ui, host: &El, scale: f32, hair: Color, fill: Color) {
    let _ = host.clone().child(px_el(ui, 2.0, 1.0, 20.0, 22.0, scale, fill));
    let _ = host.clone().child(px_el(ui, 1.0, 2.0, 22.0, 20.0, scale, fill));
    let _ = host.clone().child(px_el(ui, 2.0, 0.0, 20.0, 1.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 2.0, 23.0, 20.0, 1.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 0.0, 2.0, 1.0, 20.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 23.0, 2.0, 1.0, 20.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 1.0, 1.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 22.0, 1.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 1.0, 22.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px_el(ui, 22.0, 22.0, 1.0, 1.0, scale, hair));
}

/// Eight-pointed morning star in the quilt geometry: four cardinal points
/// and four stepped diagonals around a diamond center.
fn star8(ui: &Ui, host: &El, cx: f32, cy: f32, scale: f32, ink: Color, earth: Color) {
    // N / S
    px(ui, host, cx, cy - 5.0, 2.0, 2.0, scale, ink);
    px(ui, host, cx - 1.0, cy - 3.0, 4.0, 1.0, scale, ink);
    px(ui, host, cx, cy + 4.0, 2.0, 2.0, scale, ink);
    px(ui, host, cx - 1.0, cy + 3.0, 4.0, 1.0, scale, ink);
    // W / E
    px(ui, host, cx - 5.0, cy, 2.0, 2.0, scale, ink);
    px(ui, host, cx - 3.0, cy - 1.0, 1.0, 4.0, scale, ink);
    px(ui, host, cx + 5.0, cy, 2.0, 2.0, scale, ink);
    px(ui, host, cx + 4.0, cy - 1.0, 1.0, 4.0, scale, ink);
    // Diagonal points, stepped (NE, NW, SE, SW).
    for i in 2..=4 {
        let d = i as f32;
        px(ui, host, cx - d, cy - d, 1.0, 1.0, scale, ink);
        px(ui, host, cx + 1.0 + d, cy - d, 1.0, 1.0, scale, ink);
        px(ui, host, cx - d, cy + 1.0 + d, 1.0, 1.0, scale, ink);
        px(ui, host, cx + 1.0 + d, cy + 1.0 + d, 1.0, 1.0, scale, ink);
    }
    // Center diamond: earth ring, ink core.
    px(ui, host, cx, cy - 2.0, 2.0, 1.0, scale, earth);
    px(ui, host, cx - 1.0, cy - 1.0, 4.0, 1.0, scale, earth);
    px(ui, host, cx - 2.0, cy, 6.0, 2.0, scale, earth);
    px(ui, host, cx - 1.0, cy + 2.0, 4.0, 1.0, scale, earth);
    px(ui, host, cx, cy + 3.0, 2.0, 1.0, scale, earth);
    px(ui, host, cx, cy - 1.0, 2.0, 1.0, scale, ink);
    px(ui, host, cx - 1.0, cy, 4.0, 2.0, scale, ink);
    px(ui, host, cx, cy + 2.0, 2.0, 1.0, scale, ink);
}

fn diamond_eyes(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color, h: i32, blink: bool) {
    let h = if blink { 1 } else { h };
    diamond(ui, host, x, y, scale, color, h);
    // Mirror: left eye at x (width ~3), right at 15+3 = 18 if x=7? Use same 24-grid as before.
    // For x=7, w≈3, right = 24 - 7 - 3 = 14. Use 14 for closer spirit eyes... 
    // Keep readable spacing: right origin = 23 - x - 3 = 20 - x.
    let x2 = 20.0 - x; // x=7 → 13; two diamonds with a gap.
    diamond(ui, host, x2, y, scale, color, h);
}

fn diamond(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color, h: i32) {
    if h <= 1 {
        px(ui, host, x, y, 3.0, 1.0, scale, color);
        return;
    }
    if h == 2 {
        px(ui, host, x + 1.0, y, 1.0, 1.0, scale, color);
        px(ui, host, x, y + 1.0, 3.0, 1.0, scale, color);
        return;
    }
    // h >= 3: a full stepped diamond.
    px(ui, host, x + 1.0, y, 1.0, 1.0, scale, color);
    px(ui, host, x, y + 1.0, 3.0, 1.0, scale, color);
    px(ui, host, x + 1.0, y + 2.0, 1.0, 1.0, scale, color);
}

fn eyes_pair(ui: &Ui, host: &El, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color, blink: bool) {
    let (ey, eh) = if blink {
        (y + (h - 1.0).max(0.0), 1.0)
    } else {
        (y, h)
    };
    let x2 = 24.0 - x - w;
    px(ui, host, x, ey, w, eh, scale, color);
    px(ui, host, x2, ey, w, eh, scale, color);
}

fn ear_up(ui: &Ui, host: &El, x: f32, y: f32, h: f32, scale: f32, color: Color, left: bool) {
    // Stepped triangle, pointing up. left=true grows from the right edge inward? 
    // Both ears point up; the long side is toward the outside.
    let h = h.max(2.0);
    if left {
        px(ui, host, x + 2.0, y, 3.0, 1.0, scale, color);
        px(ui, host, x + 1.0, y + 1.0, 4.0, 1.0, scale, color);
        if h > 2.0 {
            px(ui, host, x, y + 2.0, 5.0, h - 2.0, scale, color);
        }
    } else {
        px(ui, host, x, y, 3.0, 1.0, scale, color);
        px(ui, host, x, y + 1.0, 4.0, 1.0, scale, color);
        if h > 2.0 {
            px(ui, host, x, y + 2.0, 5.0, h - 2.0, scale, color);
        }
    }
}

fn nose(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color) {
    px(ui, host, x, y, 2.0, 2.0, scale, color);
}

fn pine(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color) {
    // Stepped tree. x is the left of a 5-wide mark; crown is 3 rows + a trunk.
    px(ui, host, x + 2.0, y, 1.0, 1.0, scale, color);
    px(ui, host, x + 1.0, y + 1.0, 3.0, 1.0, scale, color);
    px(ui, host, x, y + 2.0, 5.0, 1.0, scale, color);
    px(ui, host, x + 2.0, y + 3.0, 1.0, 1.0, scale, color);
}

fn bead_band(ui: &Ui, host: &El, x: f32, y: f32, n: i32, scale: f32, a: Color, b: Color) {
    for i in 0..n {
        let c = if i % 2 == 0 { a } else { b };
        px(ui, host, x + i as f32, y, 1.0, 1.0, scale, c);
    }
}

fn chevron_right(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color) {
    px(ui, host, x, y, 1.0, 1.0, scale, color);
    px(ui, host, x + 1.0, y + 1.0, 1.0, 1.0, scale, color);
    px(ui, host, x, y + 2.0, 1.0, 1.0, scale, color);
}

fn chevron_left(ui: &Ui, host: &El, x: f32, y: f32, scale: f32, color: Color) {
    px(ui, host, x, y, 1.0, 1.0, scale, color);
    px(ui, host, x - 1.0, y + 1.0, 1.0, 1.0, scale, color);
    px(ui, host, x, y + 2.0, 1.0, 1.0, scale, color);
}

fn px(ui: &Ui, host: &El, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) {
    let _ = host.clone().child(px_el(ui, x, y, w, h, scale, color));
}

fn px_el(ui: &Ui, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) -> El {
    use remedy_ui::layout::{Length, Position};
    use remedy_ui::style::StylePatch;
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
