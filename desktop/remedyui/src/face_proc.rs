//! Remedy's procedural face.
//!
//! A face is a few bytes: a seed (her lasting look: eye style, brow and mouth
//! style, frame, the run of her circuitry), a mood, the eye state for blink and
//! glance, how much she has grown with the owner, and the theme's colors. Those
//! bytes are an image format of our own: the image engine renders them on its
//! workers at the size they are shown, sharp at any size, and caches them.
//!
//! The renderer paints a bevelled body with a mottled surface and etched
//! circuit traces, recessed sockets, neon eyes, brows and mouth, and a glow.
//! Growth adds circuitry, lit vias and bezel detail; moods move lids, brows
//! and mouth. Same bytes, same face.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::{Arc, Once};

use remedy_ui::core::Color;
use remedy_ui::image::{self as img, DecodeError, Decoded, Fit, Format, Source};
use remedy_ui::layout::{Length, Position};
use remedy_ui::paint::Image;
use remedy_ui::style::ColorRoles;
use remedy_ui::{El, Ui};

use crate::face_draw::Motion;
use crate::face_skin::{growth_points, Facts};
use crate::presence::Mood;

const MAGIC: &[u8; 8] = b"RMDYFACE";
/// Natural size; the format is scalable, so it renders sharper when shown larger.
const NATURAL: u32 = 512;

// ---------- the face spec (the image bytes) ----------

/// Everything a face image depends on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Spec {
    pub seed: u64,
    pub mood: u8,
    pub glance: i8,
    pub closed: bool,
    pub growth: u16,
    pub neon: [u8; 3],
    pub body: [u8; 3],
    pub alarm: [u8; 3],
}

impl Spec {
    fn encode(&self) -> Vec<u8> {
        let mut b = MAGIC.to_vec();
        b.extend(self.seed.to_le_bytes());
        b.extend([self.mood, self.glance as u8, self.closed as u8]);
        b.extend(self.growth.to_le_bytes());
        b.extend(self.neon);
        b.extend(self.body);
        b.extend(self.alarm);
        b
    }

    fn decode(b: &[u8]) -> Option<Spec> {
        if b.len() < 30 || &b[..8] != MAGIC {
            return None;
        }
        let rgb = |i: usize| [b[i], b[i + 1], b[i + 2]];
        Some(Spec {
            seed: u64::from_le_bytes(b[8..16].try_into().ok()?),
            mood: b[16],
            glance: b[17] as i8,
            closed: b[18] != 0,
            growth: u16::from_le_bytes([b[19], b[20]]),
            neon: rgb(21),
            body: rgb(24),
            alarm: rgb(27),
        })
    }
}

struct FaceFormat;

impl Format for FaceFormat {
    fn sniff(&self, head: &[u8]) -> bool {
        head.starts_with(MAGIC)
    }
    fn dimensions(&self, _bytes: &[u8]) -> Result<(u32, u32), DecodeError> {
        Ok((NATURAL, NATURAL))
    }
    fn decode(&self, bytes: &[u8], show_at: Option<(u32, u32)>) -> Result<Decoded, DecodeError> {
        let spec = Spec::decode(bytes).ok_or(DecodeError::Unrecognized)?;
        let n = show_at.map(|(w, h)| w.min(h)).unwrap_or(NATURAL).clamp(16, 2048);
        let px = render(&spec, n as usize);
        Ok(Decoded::new(Image::new(n, n, px.into()), (NATURAL, NATURAL)))
    }
    fn scalable(&self) -> bool {
        true
    }
}

fn install() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| img::add_format(Arc::new(FaceFormat)));
}

// ---------- seed traits and mood poses ----------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum EyeStyle {
    Lens,
    Dots,
    Slit,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Rim {
    Line,
    Bezel,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Shape {
    Square,
    Tall,
    Wide,
    Circle,
    Hexagon,
    Octagon,
    Shield,
}

/// What a seed fixes for life.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Traits {
    shape: Shape,
    /// Degrees her colors are turned from the theme's; 0 keeps the theme.
    hue: f32,
    eye: EyeStyle,
    rim: Rim,
    brows: bool,
    dashed: bool,
    corner: f32,
    eye_dx: f32,
    eye_y: f32,
    eye_w: f32,
    eye_h: f32,
    mouth_w: f32,
    mouth_y: f32,
    moss: f32,
}

fn mix64(state: &mut u64) -> u64 {
    // splitmix64
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

fn unit(state: &mut u64) -> f32 {
    (mix64(state) >> 40) as f32 / (1u64 << 24) as f32
}

impl Traits {
    pub fn from_seed(seed: u64) -> Self {
        let mut s = seed;
        let eye = match mix64(&mut s) % 3 {
            0 => EyeStyle::Lens,
            1 => EyeStyle::Dots,
            _ => EyeStyle::Slit,
        };
        let rim = if mix64(&mut s) % 2 == 0 { Rim::Line } else { Rim::Bezel };
        let brows = unit(&mut s) < 0.7;
        let dashed = unit(&mut s) < 0.3;
        let shape = [Shape::Square, Shape::Tall, Shape::Wide, Shape::Circle, Shape::Hexagon, Shape::Octagon, Shape::Shield]
            [(mix64(&mut s) % 7) as usize];
        // One face in three keeps the theme's color; the rest take a hue of their own.
        let hue = if unit(&mut s) < 0.33 { 0.0 } else { (unit(&mut s) * 12.0).floor() * 30.0 };
        let mut pick = |lo: f32, hi: f32| lo + (hi - lo) * unit(&mut s);
        let (w, h) = match eye {
            EyeStyle::Lens => (pick(0.17, 0.2), pick(0.16, 0.19)),
            EyeStyle::Dots => (pick(0.15, 0.18), 0.0),
            EyeStyle::Slit => (pick(0.19, 0.23), pick(0.09, 0.12)),
        };
        // Narrower outlines pull the features in so nothing meets the rim.
        let fit = if matches!(shape, Shape::Square | Shape::Wide) { 1.0 } else { 0.88 };
        let (w, h) = (w * fit, h * fit);
        Traits {
            shape,
            hue,
            eye,
            rim,
            brows,
            dashed,
            corner: pick(0.09, 0.16),
            eye_dx: pick(0.15, 0.18) * fit,
            eye_y: pick(0.42, 0.46),
            eye_w: w,
            eye_h: if h > 0.0 { h } else { w },
            mouth_w: pick(0.18, 0.30),
            mouth_y: pick(0.69, 0.73),
            moss: pick(0.4, 1.0),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum MouthKind {
    Curve,
    Oval,
    Bars,
    Dots,
}

#[derive(Clone, Copy, Debug)]
struct Pose {
    /// Eye aperture, 0 shut to 1 open (1.2 wide).
    open: f32,
    /// Lid slant: positive pulls the inner corners down (stern), negative up (worried).
    lid_tilt: f32,
    /// Per-eye aperture factor, for a lopsided curious look.
    open_r: f32,
    arc_eyes: bool,
    iris: f32,
    iris_dx: f32,
    iris_dy: f32,
    brow_dy: (f32, f32),
    brow_tilt: f32,
    mouth: MouthKind,
    curve: f32,
    mouth_w: f32,
    mouth_h: f32,
    mouth_dx: f32,
    glow: f32,
    alarm: bool,
    scan: bool,
}

fn pose(mood: Mood) -> Pose {
    let p = Pose {
        open: 1.0,
        lid_tilt: 0.0,
        open_r: 1.0,
        arc_eyes: false,
        iris: 1.0,
        iris_dx: 0.0,
        iris_dy: 0.0,
        brow_dy: (0.0, 0.0),
        brow_tilt: 0.0,
        mouth: MouthKind::Curve,
        curve: 0.0,
        mouth_w: 1.0,
        mouth_h: 0.0,
        mouth_dx: 0.0,
        glow: 1.0,
        alarm: false,
        scan: false,
    };
    match mood {
        Mood::Idle => Pose { open: 0.85, curve: 0.018, mouth_w: 0.85, glow: 0.85, ..p },
        Mood::Listening => Pose {
            open: 1.1,
            brow_dy: (-0.02, -0.02),
            mouth: MouthKind::Bars,
            mouth_w: 0.75,
            mouth_h: 0.05,
            ..p
        },
        Mood::Thinking => Pose {
            open: 0.8,
            iris_dx: -0.35,
            iris_dy: -0.45,
            brow_dy: (-0.035, 0.005),
            brow_tilt: 0.0,
            mouth: MouthKind::Dots,
            mouth_w: 0.55,
            mouth_dx: 0.03,
            ..p
        },
        Mood::Working => Pose { open: 0.55, lid_tilt: 0.12, brow_tilt: 0.012, curve: 0.0, mouth_w: 0.8, scan: true, ..p },
        Mood::Focused => Pose { open: 0.42, lid_tilt: 0.22, iris: 0.8, brow_tilt: 0.022, brow_dy: (0.012, 0.012), mouth_w: 0.65, ..p },
        Mood::Curious => Pose {
            open: 1.0,
            open_r: 1.25,
            iris_dx: 0.25,
            brow_dy: (0.004, -0.04),
            mouth: MouthKind::Oval,
            mouth_w: 0.32,
            mouth_h: 0.03,
            mouth_dx: 0.02,
            ..p
        },
        Mood::Happy => Pose { arc_eyes: true, brow_dy: (-0.02, -0.02), curve: 0.07, mouth_w: 1.15, glow: 1.15, ..p },
        Mood::Proud => Pose { open: 0.6, lid_tilt: -0.05, brow_dy: (-0.015, -0.015), curve: 0.05, mouth_w: 1.0, glow: 1.1, ..p },
        Mood::Sleepy => Pose { open: 0.18, brow_dy: (0.015, 0.015), curve: 0.0, mouth_w: 0.55, glow: 0.45, ..p },
        Mood::Concerned => Pose {
            open: 0.9,
            lid_tilt: -0.2,
            brow_tilt: -0.03,
            curve: -0.03,
            mouth_w: 0.9,
            glow: 0.85,
            ..p
        },
        Mood::Error => Pose {
            open: 0.6,
            lid_tilt: 0.38,
            brow_tilt: 0.04,
            curve: -0.045,
            mouth_w: 0.95,
            alarm: true,
            ..p
        },
        Mood::Surprised => Pose {
            open: 1.25,
            iris: 0.6,
            brow_dy: (-0.05, -0.05),
            mouth: MouthKind::Oval,
            mouth_w: 0.42,
            mouth_h: 0.06,
            glow: 1.1,
            ..p
        },
    }
}

fn mood_id(m: Mood) -> u8 {
    Mood::all().iter().position(|x| *x == m).unwrap_or(0) as u8
}

fn mood_of(id: u8) -> Mood {
    Mood::all().get(id as usize).copied().unwrap_or(Mood::Idle)
}

// ---------- small math ----------

type V3 = [f32; 3];

fn lerp3(a: V3, b: V3, t: f32) -> V3 {
    [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

fn scale3(a: V3, k: f32) -> V3 {
    [a[0] * k, a[1] * k, a[2] * k]
}

fn smooth(e0: f32, e1: f32, x: f32) -> f32 {
    let t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

fn rgb(c: [u8; 3]) -> V3 {
    // sRGB to linear, so light adds up the way light does.
    let f = |v: u8| (v as f32 / 255.0).powf(2.2);
    [f(c[0]), f(c[1]), f(c[2])]
}

fn sd_round_box(x: f32, y: f32, cx: f32, cy: f32, hw: f32, hh: f32, r: f32) -> f32 {
    let r = r.min(hw).min(hh);
    let qx = (x - cx).abs() - hw + r;
    let qy = (y - cy).abs() - hh + r;
    let ox = qx.max(0.0);
    let oy = qy.max(0.0);
    (ox * ox + oy * oy).sqrt() + qx.max(qy).min(0.0) - r
}

fn sd_seg(x: f32, y: f32, a: (f32, f32), b: (f32, f32)) -> f32 {
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    let t = (((x - a.0) * dx + (y - a.1) * dy) / (dx * dx + dy * dy).max(1e-9)).clamp(0.0, 1.0);
    ((a.0 + t * dx - x).powi(2) + (a.1 + t * dy - y).powi(2)).sqrt()
}

fn hash2(x: i32, y: i32, seed: u64) -> f32 {
    let mut s = seed ^ ((x as u32 as u64) << 32 | y as u32 as u64);
    unit(&mut s)
}

fn value_noise(x: f32, y: f32, seed: u64) -> f32 {
    let (xi, yi) = (x.floor() as i32, y.floor() as i32);
    let (fx, fy) = (x - xi as f32, y - yi as f32);
    let (sx, sy) = (fx * fx * (3.0 - 2.0 * fx), fy * fy * (3.0 - 2.0 * fy));
    let a = hash2(xi, yi, seed);
    let b = hash2(xi + 1, yi, seed);
    let c = hash2(xi, yi + 1, seed);
    let d = hash2(xi + 1, yi + 1, seed);
    a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy
}

fn fbm(x: f32, y: f32, seed: u64) -> f32 {
    let (mut sum, mut amp, mut f) = (0.0, 0.5, 1.0);
    for o in 0..4 {
        sum += amp * value_noise(x * f, y * f, seed.wrapping_add(o));
        amp *= 0.5;
        f *= 2.03;
    }
    sum
}

/// Signed distance to the body outline, in the unit square. Every shape
/// leaves room for the eyes and mouth where the traits can put them.
fn body_sd(t: &Traits, x: f32, y: f32) -> f32 {
    let (dx, dy) = ((x - 0.5).abs(), y - 0.5);
    let ady = dy.abs();
    let r = t.corner;
    match t.shape {
        Shape::Square => sd_round_box(x, y, 0.5, 0.5, 0.40, 0.40, r),
        Shape::Tall => sd_round_box(x, y, 0.5, 0.5, 0.345, 0.42, r),
        Shape::Wide => sd_round_box(x, y, 0.5, 0.51, 0.43, 0.37, r),
        Shape::Circle => (dx * dx + dy * dy).sqrt() - 0.43,
        Shape::Hexagon => ady.max(dx * 0.866 + ady * 0.5) - 0.385,
        Shape::Octagon => (dx.max(ady) - 0.40).max((dx + ady) * 0.7071 - 0.44),
        Shape::Shield => {
            let top = sd_round_box(x, y, 0.5, 0.5, 0.40, 0.42, r);
            top.max(dx * 0.8 + dy * 0.6 - 0.40)
        }
    }
}

/// Half-width of the body at height `y`, `inset` inside its edge.
fn half_width(t: &Traits, y: f32, inset: f32) -> f32 {
    let (mut lo, mut hi) = (0.0f32, 0.5f32);
    for _ in 0..16 {
        let mid = (lo + hi) * 0.5;
        if body_sd(t, 0.5 + mid, y) < -inset { lo = mid } else { hi = mid }
    }
    lo
}

/// Turn a linear color's hue by `deg`, keeping its lightness (a YIQ rotation).
fn turn_hue(c: V3, deg: f32) -> V3 {
    if deg == 0.0 {
        return c;
    }
    let (s, k) = deg.to_radians().sin_cos();
    let y = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
    let i = 0.596 * c[0] - 0.274 * c[1] - 0.322 * c[2];
    let q = 0.211 * c[0] - 0.523 * c[1] + 0.312 * c[2];
    let (i2, q2) = (i * k - q * s, i * s + q * k);
    [
        (y + 0.956 * i2 + 0.621 * q2).max(0.0),
        (y - 0.272 * i2 - 0.647 * q2).max(0.0),
        (y - 1.106 * i2 + 1.703 * q2).max(0.0),
    ]
}

// ---------- the canvas ----------

struct Canvas {
    n: usize,
    color: Vec<V3>,
    alpha: Vec<f32>,
    /// Light the features give off, blurred into a glow at the end.
    emit: Vec<V3>,
}

impl Canvas {
    fn new(n: usize) -> Self {
        Canvas { n, color: vec![[0.0; 3]; n * n], alpha: vec![0.0; n * n], emit: vec![[0.0; 3]; n * n] }
    }

    /// Visit pixels whose centers fall in the normalized box.
    fn each(&mut self, bbox: (f32, f32, f32, f32), mut f: impl FnMut(&mut Canvas, usize, f32, f32)) {
        let n = self.n as f32;
        let lo = |v: f32| ((v * n).floor().max(0.0) as usize).min(self.n - 1);
        let hi = |v: f32| ((v * n).ceil().max(0.0) as usize).min(self.n - 1);
        let (x0, y0, x1, y1) = (lo(bbox.0), lo(bbox.1), hi(bbox.2), hi(bbox.3));
        for py in y0..=y1 {
            for px in x0..=x1 {
                let (x, y) = ((px as f32 + 0.5) / n, (py as f32 + 0.5) / n);
                f(self, py * self.n + px, x, y);
            }
        }
    }

    /// Antialiased coverage of a signed distance at this canvas's resolution.
    fn cover(&self, sd: f32) -> f32 {
        (0.5 - sd * self.n as f32).clamp(0.0, 1.0)
    }

    fn blend(&mut self, i: usize, c: V3, a: f32) {
        if a > 0.0 {
            self.color[i] = lerp3(self.color[i], c, a);
        }
    }

    /// Paint a neon shape (signed distance `sd`) inside the body: a bright core
    /// fading to the neon color, plus emitted light for the glow.
    fn neon(&mut self, i: usize, sd: f32, neon: V3, power: f32) {
        let a = self.cover(sd);
        if a <= 0.0 {
            return;
        }
        let w = 3.0 / self.n as f32;
        let core = smooth(0.0, -w * 1.6, sd);
        let hot = lerp3(neon, lerp3(neon, [1.0, 1.0, 0.92], 0.55), core);
        self.blend(i, scale3(hot, power.min(1.2)), a);
        let e = scale3(neon, a * power);
        self.emit[i] = [self.emit[i][0].max(e[0]), self.emit[i][1].max(e[1]), self.emit[i][2].max(e[2])];
    }
}

/// Three box blurs make a close Gaussian.
fn blur(src: &[V3], n: usize, radius: usize) -> Vec<V3> {
    let mut a = src.to_vec();
    let mut b = vec![[0.0f32; 3]; n * n];
    for _ in 0..3 {
        for horizontal in [true, false] {
            for line in 0..n {
                let at = |k: usize| if horizontal { line * n + k } else { k * n + line };
                let mut sum = [0.0f32; 3];
                let r = radius as isize;
                for k in -r..=r {
                    let kk = k.clamp(0, n as isize - 1) as usize;
                    let v = a[at(kk)];
                    sum = [sum[0] + v[0], sum[1] + v[1], sum[2] + v[2]];
                }
                let inv = 1.0 / (2 * radius + 1) as f32;
                for k in 0..n {
                    b[at(k)] = scale3(sum, inv);
                    let out = (k as isize - r).clamp(0, n as isize - 1) as usize;
                    let inn = (k as isize + r + 1).clamp(0, n as isize - 1) as usize;
                    let (vo, vi) = (a[at(out)], a[at(inn)]);
                    sum = [sum[0] - vo[0] + vi[0], sum[1] - vo[1] + vi[1], sum[2] - vo[2] + vi[2]];
                }
            }
            std::mem::swap(&mut a, &mut b);
        }
    }
    a
}

// ---------- circuitry ----------

struct Trace {
    pts: Vec<(f32, f32)>,
    lit: bool,
}

/// Etched traces that avoid the features. More of them as she grows.
fn circuits(seed: u64, t: &Traits, growth: u32) -> (Vec<Trace>, Vec<(f32, f32, f32, f32)>) {
    let mut s = seed ^ 0xC1C1_7000;
    let keep_out = |x: f32, y: f32| {
        let eye = [0.5 - t.eye_dx, 0.5 + t.eye_dx]
            .iter()
            .any(|ex| (x - ex).abs() < t.eye_w * 0.75 && (y - t.eye_y).abs() < t.eye_h * 0.8 + 0.07);
        let mouth = (x - 0.5).abs() < t.mouth_w * 0.65 && (y - t.mouth_y).abs() < 0.07;
        let inside = body_sd(t, x, y) < -0.06;
        eye || mouth || !inside
    };
    let step = 1.0 / 64.0;
    let count = 70 + growth as usize * 9;
    let mut traces = Vec::new();
    for _ in 0..count {
        let mut x = (0.14 + 0.72 * unit(&mut s) / step).round() * step;
        let mut y = (0.14 + 0.72 * unit(&mut s) / step).round() * step;
        if keep_out(x, y) {
            continue;
        }
        let mut pts = vec![(x, y)];
        let mut dir = (mix64(&mut s) % 8) as i32;
        for _ in 0..(4 + mix64(&mut s) % 7) {
            if unit(&mut s) < 0.5 {
                dir = (dir + if unit(&mut s) < 0.5 { 1 } else { 7 }) % 8;
            }
            // Mostly straight runs, now and then a 45° jog.
            let d = if dir % 2 == 1 && unit(&mut s) < 0.6 { dir - 1 } else { dir };
            let (dx, dy) = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)][d as usize];
            let len = (2 + mix64(&mut s) % 6) as f32 * step;
            let (nx, ny) = (x + dx as f32 * len, y + dy as f32 * len);
            if keep_out(nx, ny) || keep_out((x + nx) * 0.5, (y + ny) * 0.5) {
                break;
            }
            x = nx;
            y = ny;
            pts.push((x, y));
        }
        if pts.len() > 1 {
            traces.push(Trace { pts, lit: false });
        }
    }
    // Light one trace per growth point, spread across the ones that were kept.
    let every = (traces.len() / growth.max(1) as usize).max(1);
    for tr in traces.iter_mut().step_by(every).take(growth as usize) {
        tr.lit = true;
    }
    // Little chips with pin rows.
    let mut chips = Vec::new();
    for _ in 0..(5 + growth / 2).min(16) {
        let (cx, cy) = (0.18 + 0.64 * unit(&mut s), 0.16 + 0.68 * unit(&mut s));
        let (hw, hh) = (0.018 + 0.02 * unit(&mut s), 0.014 + 0.016 * unit(&mut s));
        if !keep_out(cx - hw, cy - hh) && !keep_out(cx + hw, cy + hh) {
            chips.push((cx, cy, hw, hh));
        }
    }
    (traces, chips)
}

// ---------- the renderer ----------

/// Premultiplied RGBA8, `n` by `n`.
pub fn render(spec: &Spec, n: usize) -> Vec<u8> {
    let t = Traits::from_seed(spec.seed);
    let p = pose(mood_of(spec.mood));
    // Alarm keeps the theme's danger color on every face, so an error always reads as one.
    let neon = if p.alarm { rgb(spec.alarm) } else { turn_hue(rgb(spec.neon), t.hue) };
    let neon_dim = scale3(neon, 0.35);
    let body = turn_hue(rgb(spec.body), t.hue);
    let growth = spec.growth as u32;
    let mut c = Canvas::new(n);
    let px = 1.0 / n as f32;
    let seed = spec.seed;

    // Body: bevelled pillow, mottled surface, fine grain.
    let light = (-0.6f32, -0.8f32);
    c.each((0.0, 0.0, 1.0, 1.0), |c, i, x, y| {
        let sd = body_sd(&t, x, y);
        let a = c.cover(sd);
        if a <= 0.0 {
            return;
        }
        let e = 1.5 * px;
        let gx = body_sd(&t, x + e, y) - body_sd(&t, x - e, y);
        let gy = body_sd(&t, x, y + e) - body_sd(&t, x, y - e);
        let len = (gx * gx + gy * gy).sqrt().max(1e-6);
        let edge = 1.0 - smooth(0.0, 0.075, -sd);
        let lit = -(gx / len * light.0 + gy / len * light.1) * edge;
        let moss = fbm(x * 7.0, y * 7.0, seed) - 0.5;
        let grain = value_noise(x * n as f32 * 0.5, y * n as f32 * 0.5, seed ^ 0x6A) - 0.5;
        let center = 1.0 - 0.25 * ((x - 0.5).powi(2) + (y - 0.5).powi(2)) * 4.0;
        let fine = fbm(x * 31.0, y * 31.0, seed ^ 0x51) - 0.5;
        let k = (1.0 + 1.1 * lit + 1.9 * moss * t.moss + 0.9 * fine + 0.35 * grain) * center;
        c.color[i] = scale3(body, k.max(0.05));
        c.alpha[i] = a;
    });

    // Rim: a neon line, or a raised bezel with joints and a lit edge.
    let rim_in = if t.rim == Rim::Bezel { 0.045 } else { 0.018 };
    c.each((0.0, 0.0, 1.0, 1.0), |c, i, x, y| {
        if c.alpha[i] <= 0.0 {
            return;
        }
        let sd = body_sd(&t, x, y);
        if t.rim == Rim::Bezel {
            // The bezel's inner wall catches shadow, its top the light.
            let wall = smooth(0.0, 4.0 * px, (sd + rim_in).abs()) ;
            if sd > -rim_in {
                let k = 0.85 + 0.35 * smooth(-rim_in, -rim_in * 0.4, sd);
                c.color[i] = scale3(c.color[i], k * (0.6 + 0.4 * wall));
            } else {
                c.color[i] = scale3(c.color[i], 0.75 + 0.25 * smooth(0.0, 0.02, -(sd + rim_in)));
            }
            // Joints: short cuts across the bezel at seeded places.
            let joints = [0.2f32, 0.8];
            for j in joints {
                let near_top = (x - j).abs() < px * 1.2 && y < 0.5;
                let near_side = (y - j).abs() < px * 1.2 && x < 0.5;
                if (near_top || near_side) && sd > -rim_in {
                    c.color[i] = scale3(c.color[i], 0.45);
                }
            }
            let d = (sd + rim_in * 0.92).abs() - 1.1 * px;
            c.neon(i, d, neon, 0.9 * p.glow);
        } else {
            let d = (sd + rim_in).abs() - 1.3 * px;
            c.neon(i, d, neon, 0.95 * p.glow);
        }
    });

    // Circuitry: etched lines with a highlight below, vias and pins.
    let (traces, chips) = circuits(seed, &t, growth);
    let w = (1.2 * px).max(0.0018);
    for tr in &traces {
        for seg in tr.pts.windows(2) {
            let (a, b) = (seg[0], seg[1]);
            let bb = (a.0.min(b.0) - 0.01, a.1.min(b.1) - 0.01, a.0.max(b.0) + 0.01, a.1.max(b.1) + 0.01);
            c.each(bb, |c, i, x, y| {
                let d = sd_seg(x, y, a, b);
                let shade = c.cover(d - w * 0.5);
                let shine = c.cover(sd_seg(x, y - 1.5 * px, a, b) - w * 0.5) * (1.0 - shade);
                let base = c.color[i];
                c.blend(i, scale3(base, 0.3), shade * 0.95);
                c.blend(i, scale3(base, 1.7), shine * 0.75);
                if tr.lit {
                    c.blend(i, neon_dim, shade * 0.5);
                }
            });
        }
        // A via at each end; lit vias are her earned LEDs.
        for (k, &(vx, vy)) in [tr.pts[0], *tr.pts.last().unwrap()].iter().enumerate() {
            let r = 0.0065;
            c.each((vx - 0.02, vy - 0.02, vx + 0.02, vy + 0.02), |c, i, x, y| {
                let d = ((x - vx).powi(2) + (y - vy).powi(2)).sqrt();
                let ring = (d - r).abs() - w * 0.5;
                let base = c.color[i];
                c.blend(i, scale3(base, 0.4), c.cover(ring));
                if tr.lit && k == 1 {
                    c.neon(i, d - r * 0.6, neon, 0.9 * p.glow);
                }
            });
        }
    }
    for &(cx, cy, hw, hh) in &chips {
        c.each((cx - hw - 0.01, cy - hh - 0.01, cx + hw + 0.01, cy + hh + 0.01), |c, i, x, y| {
            let d = sd_round_box(x, y, cx, cy, hw, hh, 0.002);
            let base = c.color[i];
            c.blend(i, scale3(base, 0.55), c.cover(d));
            c.blend(i, scale3(base, 1.3), c.cover(d.abs() - 0.6 * px) * 0.6);
            // Pins along the long sides.
            let pin = ((x - cx) / 0.008).fract().abs() < 0.45;
            let side = (y - cy).abs() > hh && (y - cy).abs() < hh + 0.008 && (x - cx).abs() < hw - 0.004;
            if pin && side {
                c.blend(i, scale3(base, 1.5), 0.7);
            }
        });
    }

    // Grown in: side LED strip on the bezel, and a lit seam at the top.
    if growth >= 6 {
        let leds = (growth as usize - 5).min(9);
        for k in 0..leds {
            let ly = 0.38 + k as f32 * 0.028;
            let reach = half_width(&t, ly, rim_in * 0.5);
            for lx in [0.5 - reach, 0.5 + reach] {
                c.each((lx - 0.01, ly - 0.01, lx + 0.01, ly + 0.01), |c, i, x, y| {
                    let d = sd_round_box(x, y, lx, ly, 0.0035, 0.006, 0.002);
                    c.neon(i, d, neon, 0.85 * p.glow);
                });
            }
        }
    }

    // Sockets and eyes.
    let blink = spec.closed;
    for (side, open_k) in [(-1.0f32, 1.0f32), (1.0, p.open_r)] {
        let ex = 0.5 + side * t.eye_dx;
        let ey = t.eye_y;
        let (hw, hh) = (t.eye_w * 0.5, t.eye_h * 0.5);
        let open = if blink { 0.06 } else { (p.open * open_k).min(1.3) };
        let sock_r = match t.eye {
            EyeStyle::Lens => hw * 0.38,
            EyeStyle::Dots => hw,
            EyeStyle::Slit => hw * 0.16,
        };
        // A recessed socket, a little larger than the eye, shadowed at the top.
        let (sw, sh) = (hw + 0.022, hh * open.max(0.55).min(1.2) + 0.022);
        c.each((ex - sw - 0.03, ey - sh - 0.03, ex + sw + 0.03, ey + sh + 0.03), |c, i, x, y| {
            let d = sd_round_box(x, y, ex, ey, sw, sh, sock_r + 0.02);
            let a = c.cover(d);
            let inner = smooth(0.0, 0.02, -d);
            let top = smooth(ey - sh, ey + sh, y);
            let base = c.color[i];
            c.blend(i, scale3(base, 0.22 + 0.25 * top * inner), a);
            // Lip around the socket: light below, shadow above.
            let lip = c.cover(d.abs() - 1.2 * px);
            c.blend(i, scale3(base, if y > ey { 1.45 } else { 0.55 }), lip * 0.7);
        });

        // The lid line at the top of the opening, slanted by mood.
        let tilt = p.lid_tilt * -side;
        let lid = move |x: f32| ey - hh * open + (x - ex) / hw * hh * tilt * 1.4;
        let below_lid = |x: f32, y: f32| y > lid(x);
        let glance = spec.glance as f32 * 0.32 + p.iris_dx;

        if p.arc_eyes && !blink {
            // Happy: glowing upturned arcs.
            let (a, m, b) = ((ex - hw * 0.85, ey + hh * 0.25), (ex, ey - hh * 0.95), (ex + hw * 0.85, ey + hh * 0.25));
            let pts: Vec<(f32, f32)> = (0..=20)
                .map(|k| {
                    let s = k as f32 / 20.0;
                    let u = 1.0 - s;
                    (u * u * a.0 + 2.0 * u * s * m.0 + s * s * b.0, u * u * a.1 + 2.0 * u * s * m.1 + s * s * b.1)
                })
                .collect();
            c.each((ex - hw - 0.02, ey - hh - 0.02, ex + hw + 0.02, ey + hh + 0.02), |c, i, x, y| {
                let d = pts.windows(2).map(|s| sd_seg(x, y, s[0], s[1])).fold(f32::MAX, f32::min);
                c.neon(i, d - 0.014, neon, p.glow);
            });
            continue;
        }
        if blink || open < 0.1 {
            c.each((ex - hw, ey - 0.02, ex + hw, ey + 0.02), |c, i, x, y| {
                let d = sd_round_box(x, y, ex, ey + hh * 0.1, hw * 0.85, 0.006, 0.006);
                c.neon(i, d, neon, 0.9 * p.glow);
            });
            continue;
        }

        let box_d = |x: f32, y: f32| sd_round_box(x, y, ex, ey, hw, hh * open.min(1.2), sock_r);
        match t.eye {
            EyeStyle::Lens => {
                // Glass: dark, with a neon ring, an iris square and a reticle.
                let (ix, iy) = (ex + glance * hw * 0.35, ey + p.iris_dy * hh * 0.4);
                let ir = hw * 0.42 * p.iris;
                c.each((ex - hw - 0.02, ey - hh * 1.3 - 0.02, ex + hw + 0.02, ey + hh * 1.3 + 0.02), |c, i, x, y| {
                    let d = box_d(x, y);
                    if d > 2.0 * px {
                        return;
                    }
                    let open_here = below_lid(x, y);
                    let glass = lerp3(scale3(body, 0.12), scale3(neon, 0.18), smooth(hw, 0.0, ((x - ix).powi(2) + (y - iy).powi(2)).sqrt()));
                    c.blend(i, glass, c.cover(d) * if open_here { 1.0 } else { 0.0 });
                    if open_here {
                        let di = sd_round_box(x, y, ix, iy, ir, ir, ir * 0.45);
                        c.neon(i, di.abs() - 0.006, neon, p.glow);
                        let core = sd_round_box(x, y, ix, iy, ir * 0.55, ir * 0.55, ir * 0.25);
                        let speck = value_noise(x * n as f32 * 0.35, y * n as f32 * 0.35, seed ^ 0xE7);
                        if core < 0.0 {
                            c.blend(i, lerp3(scale3(neon, 0.6), neon, speck), 0.85);
                        }
                        // Reticle hairlines.
                        let hair = ((x - ix).abs() - 0.4 * px).min((y - iy).abs() - 0.4 * px);
                        if di < 0.0 && di > -ir * 0.9 && hair < 0.0 {
                            c.blend(i, neon, 0.35);
                        }
                        if p.scan && ((y * 90.0).fract() < 0.18) {
                            c.blend(i, neon, 0.25 * c.cover(d));
                        }
                        // Catchlight.
                        let (hx, hy) = (ix - ir * 0.45, iy - ir * 0.45);
                        let dh = ((x - hx).powi(2) + (y - hy).powi(2)).sqrt() - ir * 0.16;
                        c.blend(i, [1.0, 1.0, 0.95], c.cover(dh) * 0.9);
                    }
                    // Lens ring and lid edge.
                    let ring = d.abs() - 0.007;
                    if open_here || y > ey {
                        c.neon(i, ring, neon, p.glow);
                    }
                    let lid_d = (y - lid(x)).abs() - 0.005;
                    if d < 0.0 && p.open < 0.95 {
                        c.neon(i, lid_d, neon, 0.9 * p.glow);
                    }
                });
            }
            EyeStyle::Dots => {
                // A round lens: neon rim, dark dome, a pupil of lit dots.
                let r = hw * open.min(1.15).max(0.5);
                let (ix, iy) = (ex + glance * hw * 0.3, ey + p.iris_dy * r * 0.35);
                c.each((ex - hw - 0.03, ey - hw - 0.03, ex + hw + 0.03, ey + hw + 0.03), |c, i, x, y| {
                    let d = ((x - ex).powi(2) + (y - ey).powi(2)).sqrt() - r;
                    if d > 0.02 {
                        return;
                    }
                    let open_here = below_lid(x, y);
                    let dome = smooth(r, 0.0, ((x - ex + r * 0.3).powi(2) + (y - ey + r * 0.35).powi(2)).sqrt());
                    c.blend(i, lerp3(scale3(body, 0.18), scale3(body, 0.6), dome * 0.6), c.cover(d));
                    if !open_here {
                        c.blend(i, scale3(body, 0.5), c.cover(d));
                    }
                    c.neon(i, d.abs() - 0.0065, neon, p.glow);
                    // Dot-grid pupil.
                    let pr = r * 0.42 * p.iris;
                    let (gx, gy) = ((x - ix) / 0.0105, (y - iy) / 0.0105);
                    let (fx, fy) = (gx - gx.round(), gy - gy.round());
                    let dot = (fx * fx + fy * fy).sqrt() * 0.0105 - 0.0032;
                    let inside = ((gx.round() * 0.0105).powi(2) + (gy.round() * 0.0105).powi(2)).sqrt() < pr;
                    if inside && open_here && d < 0.0 {
                        c.neon(i, dot, neon, 0.95 * p.glow);
                    }
                    let lid_d = (y - lid(x)).abs() - 0.004;
                    if d < 0.0 && p.open < 0.95 {
                        c.neon(i, lid_d, neon, 0.8 * p.glow);
                    }
                });
            }
            EyeStyle::Slit => {
                // A bar of light in a dark window; its height is the aperture.
                let bar_h = (hh * 0.42 * open).max(0.006);
                let (bx, by) = (ex + glance * hw * 0.25, ey + p.iris_dy * hh * 0.3);
                let bar_w = hw * 0.78;
                c.each((ex - hw - 0.02, ey - hh - 0.02, ex + hw + 0.02, ey + hh + 0.02), |c, i, x, y| {
                    let d = sd_round_box(x, y, ex, ey, hw, hh, sock_r);
                    c.blend(i, scale3(body, 0.08), c.cover(d));
                    c.blend(i, scale3(body, 0.5), c.cover(d.abs() - 0.6 * px) * 0.8);
                    // Slant the bar's top with the lids, like the stern references.
                    let cut = by - bar_h + (x - bx) / bar_w * bar_h * tilt * 3.0;
                    let db = sd_round_box(x, y, bx, by, bar_w, bar_h, 0.004).max(cut - y);
                    c.neon(i, db, neon, p.glow);
                    if p.scan && ((x * 70.0).fract() < 0.12) && db < 0.0 {
                        c.blend(i, scale3(neon, 0.5), 0.5);
                    }
                });
            }
        }
    }

    // Brows: neon bars or dashes, in grooves.
    if t.brows || p.brow_tilt.abs() > 0.02 {
        for (side, dy) in [(-1.0f32, p.brow_dy.0), (1.0, p.brow_dy.1)] {
            let ex = 0.5 + side * t.eye_dx;
            let y = t.eye_y - t.eye_h * 0.5 * p.open.clamp(0.6, 1.2) - 0.075 + dy;
            let half = t.eye_w * 0.55;
            let inner = (ex - side * half, y + p.brow_tilt);
            let outer = (ex + side * half, y - p.brow_tilt * 0.5);
            let len = ((outer.0 - inner.0).powi(2) + (outer.1 - inner.1).powi(2)).sqrt();
            c.each((ex - half - 0.03, y - 0.06, ex + half + 0.03, y + 0.06), |c, i, x, y| {
                let d = sd_seg(x, y, inner, outer);
                let base = c.color[i];
                c.blend(i, scale3(base, 0.3), c.cover(d - 0.021));
                let mut core = d - 0.0125;
                if t.dashed {
                    let s = (((x - inner.0) * (outer.0 - inner.0) + (y - inner.1) * (outer.1 - inner.1)) / len) / len;
                    if (s * 4.0).fract() < 0.22 {
                        core = core.max(0.004);
                    }
                }
                c.neon(i, core, neon, 0.95 * p.glow);
            });
        }
    }

    // Mouth.
    let (mx, my) = (0.5 + p.mouth_dx, t.mouth_y);
    let mw = t.mouth_w * 0.5 * p.mouth_w;
    c.each((mx - mw - 0.04, my - 0.09, mx + mw + 0.04, my + 0.09), |c, i, x, y| {
        let base = c.color[i];
        match p.mouth {
            MouthKind::Curve => {
                let (a, m, b) = ((mx - mw, my - p.curve * 0.5), (mx, my + p.curve * 1.5), (mx + mw, my - p.curve * 0.5));
                let mut d = f32::MAX;
                let mut along = 0.0;
                for k in 0..20 {
                    let s0 = k as f32 / 20.0;
                    let s1 = (k + 1) as f32 / 20.0;
                    let at = |s: f32| {
                        let u = 1.0 - s;
                        (u * u * a.0 + 2.0 * u * s * m.0 + s * s * b.0, u * u * a.1 + 2.0 * u * s * m.1 + s * s * b.1)
                    };
                    let dd = sd_seg(x, y, at(s0), at(s1));
                    if dd < d {
                        d = dd;
                        along = s0;
                    }
                }
                c.blend(i, scale3(base, 0.3), c.cover(d - 0.018));
                let mut core = d - 0.0105;
                if t.dashed && (along * 5.0).fract() < 0.2 {
                    core = core.max(0.004);
                }
                c.neon(i, core, neon, p.glow);
            }
            MouthKind::Oval => {
                let d = sd_round_box(x, y, mx, my, mw, p.mouth_h, p.mouth_h * 0.9);
                c.blend(i, scale3(body, 0.06), c.cover(d));
                c.neon(i, d.abs() - 0.011, neon, p.glow);
            }
            MouthKind::Bars => {
                // A small level meter: she is listening.
                let k = ((x - (mx - mw)) / (2.0 * mw) * 7.0).floor();
                if (0.0..7.0).contains(&k) {
                    let cx = mx - mw + (k + 0.5) * 2.0 * mw / 7.0;
                    let h = p.mouth_h * (0.25 + 0.75 * hash2(k as i32, 3, seed ^ 0xBA));
                    let d = sd_round_box(x, y, cx, my, mw / 7.0 * 0.55, h, 0.003);
                    c.neon(i, d, neon, p.glow);
                }
            }
            MouthKind::Dots => {
                for k in -1..=1 {
                    let cx = mx + k as f32 * mw * 0.6;
                    let d = ((x - cx).powi(2) + (y - my).powi(2)).sqrt() - 0.011;
                    c.neon(i, d, neon, p.glow);
                }
            }
        }
    });

    // Glow: emitted light, blurred, laid over everything and around the body.
    let radius = ((n as f32) * 0.011).round().max(1.0) as usize;
    let halo = blur(&c.emit, n, radius);
    let wide = blur(&c.emit, n, radius * 3);
    let mut out = vec![0u8; n * n * 4];
    for i in 0..n * n {
        let k = if c.alpha[i] > 0.5 { (0.42, 0.10) } else { (0.75, 0.22) };
        let g = [halo[i][0] * k.0 + wide[i][0] * k.1, halo[i][1] * k.0 + wide[i][1] * k.1, halo[i][2] * k.0 + wide[i][2] * k.1];
        // Drop the faint tail of the blur, which showed as a square behind the body.
        let gl = ((g[0].max(g[1]).max(g[2]) - 0.035).max(0.0) * 1.04).min(1.0);
        let a = c.alpha[i];
        // Body color plus glow inside; glow alone outside.
        let col = [c.color[i][0] * a + g[0], c.color[i][1] * a + g[1], c.color[i][2] * a + g[2]];
        let alpha = (a + gl * (1.0 - a)).min(1.0);
        // Straight color, encoded to sRGB, then premultiplied in sRGB as the renderer expects.
        let enc = |v: f32| {
            let straight = if alpha > 0.0 { (v / alpha).clamp(0.0, 1.0) } else { 0.0 };
            (straight.powf(1.0 / 2.2) * alpha * 255.0).round() as u8
        };
        out[i * 4] = enc(col[0]);
        out[i * 4 + 1] = enc(col[1]);
        out[i * 4 + 2] = enc(col[2]);
        out[i * 4 + 3] = (alpha * 255.0).round() as u8;
    }
    out
}

// ---------- the element ----------

thread_local! {
    /// One shared byte buffer per face, so the image engine's cache recognizes it.
    static SOURCES: RefCell<HashMap<Spec, Arc<[u8]>>> = RefCell::new(HashMap::new());
}

fn source(spec: Spec) -> Source {
    let bytes = SOURCES.with(|m| {
        let mut m = m.borrow_mut();
        if m.len() > 256 {
            m.clear();
        }
        m.entry(spec).or_insert_with(|| spec.encode().into()).clone()
    });
    Source::Bytes(bytes)
}

fn rgb8(c: Color) -> [u8; 3] {
    let v = c.to_rgba8();
    [v[0], v[1], v[2]]
}

/// The spec for a face in the current theme.
pub fn spec(seed: u64, facts: Facts, mood: Mood, colors: &ColorRoles, glance: i8, closed: bool) -> Spec {
    // The body is the theme's panel pulled toward its accent, so every theme gets a face of its own.
    let body = colors.panel.mix_oklab(colors.accent, 0.22);
    Spec {
        seed,
        mood: mood_id(mood),
        glance,
        closed,
        growth: growth_points(facts).min(u16::MAX as u32) as u16,
        neon: rgb8(colors.accent),
        body: rgb8(body),
        alarm: rgb8(colors.danger),
    }
}

/// Paint the face into `host`, a `size` px square.
pub fn paint(ui: &Ui, host: &El, seed: u64, facts: Facts, mood: Mood, size: f32, colors: &ColorRoles, motion: Motion) {
    install();
    // Four frames; exactly one is visible at a time.
    for (glance, closed) in [(0i8, false), (-1, false), (1, false), (0, true)] {
        let el = img::image(ui, source(spec(seed, facts, mood, colors, glance, closed)), "", Fit::Contain).layout(
            move |l| {
                l.position = Position::Absolute;
                l.inset.start = 0.0;
                l.inset.top = 0.0;
                l.width = Length::Px(size);
                l.height = Length::Px(size);
            },
        );
        let flag = el.clone();
        let (blink, look) = (motion.blink.clone(), motion.glance_x.clone());
        let _ = host.clone().child(el.bind(move || {
            let b = blink.get();
            let shown = if closed { b } else { !b && look.get().signum() == glance };
            flag.clone().hidden(!shown);
        }));
    }
}

/// Representative use for a pinned growth stage (design shots and tests).
pub fn facts_for_stage(stage: u8) -> Facts {
    match stage {
        0 => Facts::default(),
        1 => Facts { sessions: 3, messages: 20, days: 5, goals: 0 },
        2 => Facts { sessions: 30, messages: 600, days: 90, goals: 2 },
        _ => Facts { sessions: 400, messages: 20_000, days: 900, goals: 6 },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec_for(seed: u64, mood: Mood, growth: u16) -> Spec {
        Spec {
            seed,
            mood: mood_id(mood),
            glance: 0,
            closed: false,
            growth,
            neon: [124, 255, 42],
            body: [30, 60, 32],
            alarm: [230, 80, 70],
        }
    }

    fn lit(px: &[u8]) -> usize {
        px.chunks(4).filter(|p| p[0].max(p[1]).max(p[2]) > 200 && p[3] > 200).count()
    }

    /// A PNG with stored (uncompressed) deflate blocks: enough for review sheets.
    fn png(w: usize, h: usize, rgb: &[u8]) -> Vec<u8> {
        fn crc(data: &[u8]) -> u32 {
            let mut c = !0u32;
            for &b in data {
                c ^= b as u32;
                for _ in 0..8 {
                    c = if c & 1 != 0 { 0xEDB8_8320 ^ (c >> 1) } else { c >> 1 };
                }
            }
            !c
        }
        fn chunk(out: &mut Vec<u8>, kind: &[u8], data: &[u8]) {
            out.extend((data.len() as u32).to_be_bytes());
            let mut body = kind.to_vec();
            body.extend(data);
            out.extend(&body);
            out.extend(crc(&body).to_be_bytes());
        }
        let mut raw = Vec::new();
        for row in rgb.chunks(w * 3) {
            raw.push(0);
            raw.extend(row);
        }
        let mut z = vec![0x78, 0x01];
        for (k, block) in raw.chunks(65_535).enumerate() {
            let last = (k + 1) * 65_535 >= raw.len();
            z.push(last as u8);
            z.extend((block.len() as u16).to_le_bytes());
            z.extend((!(block.len() as u16)).to_le_bytes());
            z.extend(block);
        }
        let (mut a, mut b) = (1u32, 0u32);
        for &v in &raw {
            a = (a + v as u32) % 65_521;
            b = (b + a) % 65_521;
        }
        z.extend(((b << 16) | a).to_be_bytes());
        let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
        let mut ihdr = (w as u32).to_be_bytes().to_vec();
        ihdr.extend((h as u32).to_be_bytes());
        ihdr.extend([8, 2, 0, 0, 0]);
        chunk(&mut out, b"IHDR", &ihdr);
        chunk(&mut out, b"IDAT", &z);
        chunk(&mut out, b"IEND", &[]);
        out
    }

    /// Review sheets: `REMEDY_FACE_OUT=dir cargo test --lib write_face_sheets`.
    #[test]
    fn write_face_sheets() {
        let Ok(dir) = std::env::var("REMEDY_FACE_OUT") else { return };
        let dir = std::path::Path::new(&dir);
        let _ = std::fs::create_dir_all(dir);
        let cell = 300;
        let bg = [6u8, 14, 9];
        let sheet = |cells: &[Vec<(u64, u16, Mood)>], name: &str| {
            let (w, h) = (cell * cells[0].len(), cell * cells.len());
            let mut out = vec![0u8; w * h * 3];
            for px in out.chunks_mut(3) {
                px.copy_from_slice(&bg);
            }
            for (r, row) in cells.iter().enumerate() {
                for (k, &(seed, growth, mood)) in row.iter().enumerate() {
                    let face = render(&spec_for(seed, mood, growth), cell);
                    for y in 0..cell {
                        for x in 0..cell {
                            let s = &face[(y * cell + x) * 4..][..4];
                            let a = s[3] as f32 / 255.0;
                            let o = ((r * cell + y) * w + k * cell + x) * 3;
                            for ch in 0..3 {
                                out[o + ch] = (s[ch] as f32 + bg[ch] as f32 * (1.0 - a)).round().min(255.0) as u8;
                            }
                        }
                    }
                }
            }
            std::fs::write(dir.join(name), png(w, h, &out)).expect("write sheet");
        };
        // Twelve people's Remedys, a few months in, at rest.
        let people = [1u64, 3, 22, 4, 12, 15, 13, 19, 28, 32, 36, 30];
        let row = |s: &[u64]| s.iter().map(|&seed| (seed, 7, Mood::Idle)).collect::<Vec<_>>();
        sheet(&[row(&people[..6]), row(&people[6..])], "gallery.png");
        // Three of them across moods.
        let moods = [Mood::Idle, Mood::Listening, Mood::Thinking, Mood::Happy, Mood::Surprised, Mood::Error];
        let more = [Mood::Working, Mood::Focused, Mood::Curious, Mood::Proud, Mood::Sleepy, Mood::Concerned];
        let across = |seed: u64, m: &[Mood]| m.iter().map(|&mood| (seed, 7, mood)).collect::<Vec<_>>();
        sheet(&[across(3, &moods), across(3, &more), across(22, &moods), across(22, &more), across(4, &moods), across(4, &more)], "moods.png");
        // One Remedy growing: first week, a few months, years.
        sheet(&[vec![(1, 0, Mood::Idle), (1, 7, Mood::Idle), (1, 17, Mood::Idle)]], "growth.png");
    }
    #[test]
    fn spec_round_trips_and_the_format_sniffs_it() {
        let s = Spec { glance: -1, closed: true, ..spec_for(99, Mood::Error, 17) };
        let bytes = s.encode();
        assert_eq!(Spec::decode(&bytes), Some(s));
        assert!(FaceFormat.sniff(&bytes));
        assert!(!FaceFormat.sniff(b"\x89PNG\r\n"));
    }

    #[test]
    fn same_spec_same_pixels_and_every_mood_renders() {
        let a = render(&spec_for(7, Mood::Idle, 5), 96);
        assert_eq!(a, render(&spec_for(7, Mood::Idle, 5), 96));
        assert_eq!(a.len(), 96 * 96 * 4);
        let idle = lit(&a);
        for mood in Mood::all() {
            let px = render(&spec_for(7, mood, 5), 96);
            // Sleepy is dim on purpose, so count any clearly lit pixel.
            let glowing = px.chunks(4).filter(|p| p[0].max(p[1]).max(p[2]) > 110 && p[3] > 200).count();
            assert!(glowing > 20, "{mood:?} shows neon");
            // Premultiplied: no channel brighter than its alpha.
            assert!(px.chunks(4).all(|p| p[0] <= p[3] && p[1] <= p[3] && p[2] <= p[3]));
        }
        assert!(idle > 0);
    }

    #[test]
    fn moods_and_blink_and_glance_change_the_face() {
        let base = render(&spec_for(3, Mood::Idle, 5), 128);
        assert_ne!(base, render(&spec_for(3, Mood::Error, 5), 128));
        assert_ne!(base, render(&spec_for(3, Mood::Listening, 5), 128));
        let shut = render(&Spec { closed: true, ..spec_for(3, Mood::Idle, 5) }, 128);
        assert!(lit(&shut) < lit(&base), "a blink shows less light");
        let left = render(&Spec { glance: -1, ..spec_for(3, Mood::Idle, 5) }, 128);
        let right = render(&Spec { glance: 1, ..spec_for(3, Mood::Idle, 5) }, 128);
        assert_ne!(left, right);
    }

    #[test]
    fn growth_adds_detail_and_seeds_pick_different_looks() {
        let t = Traits::from_seed(11);
        let (few, _) = circuits(11, &t, 0);
        let (many, _) = circuits(11, &t, 18);
        assert!(many.len() > few.len());
        assert!(many.iter().any(|tr| tr.lit) && !few.iter().any(|tr| tr.lit));
        let styles: std::collections::HashSet<_> = (0..40).map(|s| format!("{:?}", Traits::from_seed(s).eye)).collect();
        assert_eq!(styles.len(), 3, "every eye style appears");
    }

    #[test]
    fn every_shape_appears_and_holds_its_features() {
        let mut shapes = std::collections::HashSet::new();
        for seed in 0..300u64 {
            let t = Traits::from_seed(seed);
            shapes.insert(t.shape);
            // The farthest the features reach in any mood: raised brows, wide eyes, a broad smile.
            let half = t.eye_w * 0.55;
            let brow = (0.5 + t.eye_dx + half, t.eye_y - t.eye_h * 0.6 - 0.075 - 0.05 - 0.02);
            let socket = (0.5 + t.eye_dx + t.eye_w * 0.5 + 0.022, t.eye_y + t.eye_h * 0.6 + 0.022);
            let mouth = (0.5 + t.mouth_w * 0.575 + 0.02, t.mouth_y + 0.07);
            for (name, (x, y)) in [("brow", brow), ("socket", socket), ("mouth", mouth)] {
                for px in [x, 1.0 - x] {
                    assert!(body_sd(&t, px, y) < -0.012, "seed {seed} {:?}: {name} at {px:.3},{y:.3} leaves the body", t.shape);
                }
            }
        }
        assert_eq!(shapes.len(), 7, "all seven shapes turn up");
    }

    #[test]
    fn the_face_stays_in_its_square_and_glows_past_the_body() {
        let n = 128;
        let px = render(&spec_for(5, Mood::Happy, 8), n);
        // Corners are clear; the halo reaches just outside the body edge.
        assert_eq!(px[3], 0);
        let t = Traits::from_seed(5);
        let reach = half_width(&t, 0.5, 0.0);
        let edge = (n / 2) * n + ((0.5 - reach - 0.012) * n as f32) as usize;
        assert!(px[edge * 4 + 3] > 0, "glow outside the body edge");
    }
}
