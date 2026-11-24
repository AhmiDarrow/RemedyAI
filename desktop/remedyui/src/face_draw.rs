//! Paint a resolved face skin onto a 24-unit grid.
//!
//! One rectangle per mark, snapped to the pixel grid. No glow, blur,
//! gradients, glass, or blobs. Blink and glance are applied here; breath
//! and settle live on the host frame in `presence`.

use std::collections::BTreeMap;

use remedy_ui::core::Color;
use remedy_ui::layout::{Length, Position};
use remedy_ui::style::{ColorRoles, StylePatch};
use remedy_ui::{El, Ui};

use crate::face_skin::{FaceSkin, Part, Resolved};
use crate::presence::{Mood, State};
use crate::tokens;

pub struct Motion {
    pub blink: bool,
    pub glance_x: i8,
}

pub fn paint(
    ui: &Ui,
    host: &El,
    skin: &FaceSkin,
    mood: Mood,
    scale: f32,
    colors: &ColorRoles,
    stage: u8,
    motion: Motion,
) {
    if skin.engine == "legacy" {
        paint_legacy(ui, host, skin, mood, scale, colors, motion.blink);
        return;
    }
    let resolved = skin.resolve(mood.key(), stage);
    let palette = Palette::new(skin, &resolved, colors);
    for part in &resolved.parts {
        draw_part(ui, host, part, &palette, scale, &motion);
    }
}

fn paint_legacy(
    ui: &Ui,
    host: &El,
    _skin: &FaceSkin,
    mood: Mood,
    scale: f32,
    colors: &ColorRoles,
    blink: bool,
) {
    let state = mood.as_state();
    let p = crate::presence_draw::palette(state, colors);
    match crate::presence::kind() {
        crate::presence::Kind::MorningStar => {
            crate::presence_draw::morning_star(ui, host, state, scale, &p, blink)
        }
        crate::presence::Kind::Wolf => {
            crate::presence_draw::wolf(ui, host, state, scale, &p, blink)
        }
        crate::presence::Kind::Bear => {
            crate::presence_draw::bear(ui, host, state, scale, &p, blink)
        }
        crate::presence::Kind::Square => {
            // Should not happen; square is declarative.
            crate::presence_draw::morning_star(ui, host, state, scale, &p, blink)
        }
    }
}

struct Palette {
    map: BTreeMap<String, Color>,
}

impl Palette {
    fn new(skin: &FaceSkin, resolved: &Resolved<'_>, colors: &ColorRoles) -> Self {
        let mut map = BTreeMap::new();
        let line = tokens::hairline(colors);
        map.insert("bg".into(), colors.bg);
        map.insert("panel".into(), colors.panel);
        map.insert("card".into(), colors.card);
        map.insert("text".into(), colors.text);
        map.insert("muted".into(), colors.muted);
        map.insert("accent".into(), colors.accent);
        map.insert("on_accent".into(), colors.on_accent);
        map.insert("danger".into(), colors.danger);
        map.insert("warn".into(), colors.warn);
        map.insert("ok".into(), colors.ok);
        map.insert("line".into(), line);
        for (k, v) in &skin.colors {
            map.insert(k.clone(), resolve_named(v, &map, colors, line));
        }
        // Mood color overrides (hair / ink / fill / mute).
        if let Some(v) = &resolved.pose.hair {
            let c = resolve_named(v, &map, colors, line);
            map.insert("hair".into(), c);
        }
        if let Some(v) = &resolved.pose.ink {
            let c = resolve_named(v, &map, colors, line);
            map.insert("ink".into(), c);
        }
        if let Some(v) = &resolved.pose.fill {
            let c = resolve_named(v, &map, colors, line);
            map.insert("fill".into(), c);
        }
        if let Some(v) = &resolved.pose.mute {
            let c = resolve_named(v, &map, colors, line);
            map.insert("mute".into(), c);
        }
        // Defaults if the skin omitted them.
        map.entry("hair".into()).or_insert(line);
        map.entry("fill".into()).or_insert(colors.panel);
        map.entry("ink".into()).or_insert(colors.accent);
        map.entry("mute".into()).or_insert(colors.muted);
        map.entry("sock".into()).or_insert(colors.bg);
        Palette { map }
    }

    fn get(&self, name: &str) -> Color {
        if let Some(c) = self.map.get(name) {
            return *c;
        }
        if let Some(c) = parse_hex(name) {
            return c;
        }
        self.map.get("ink").copied().unwrap_or(Color::WHITE)
    }

    fn of<'a>(&self, given: Option<&'a String>, fallback: &str) -> Color {
        match given {
            Some(n) => self.get(n),
            None => self.get(fallback),
        }
    }
}

fn resolve_named(
    v: &str,
    map: &BTreeMap<String, Color>,
    colors: &ColorRoles,
    line: Color,
) -> Color {
    if let Some(c) = map.get(v) {
        return *c;
    }
    match v {
        "bg" => colors.bg,
        "panel" => colors.panel,
        "card" => colors.card,
        "text" => colors.text,
        "muted" => colors.muted,
        "accent" => colors.accent,
        "on_accent" => colors.on_accent,
        "danger" => colors.danger,
        "warn" => colors.warn,
        "ok" => colors.ok,
        "line" => line,
        _ => parse_hex(v).unwrap_or(line),
    }
}

fn parse_hex(v: &str) -> Option<Color> {
    Color::from_hex(v).ok()
}

fn draw_part(ui: &Ui, host: &El, part: &Part, pal: &Palette, scale: f32, motion: &Motion) {
    match part.kind.as_str() {
        "stepped-frame" => {
            let hair = pal.of(part.hair.as_ref(), "hair");
            let fill = pal.of(part.fill.as_ref(), "fill");
            px(ui, host, 2.0, 1.0, 20.0, 22.0, scale, fill);
            px(ui, host, 1.0, 2.0, 22.0, 20.0, scale, fill);
            px(ui, host, 2.0, 0.0, 20.0, 1.0, scale, hair);
            px(ui, host, 2.0, 23.0, 20.0, 1.0, scale, hair);
            px(ui, host, 0.0, 2.0, 1.0, 20.0, scale, hair);
            px(ui, host, 23.0, 2.0, 1.0, 20.0, scale, hair);
            px(ui, host, 1.0, 1.0, 1.0, 1.0, scale, hair);
            px(ui, host, 22.0, 1.0, 1.0, 1.0, scale, hair);
            px(ui, host, 1.0, 22.0, 1.0, 1.0, scale, hair);
            px(ui, host, 22.0, 22.0, 1.0, 1.0, scale, hair);
        }
        "inner-ring" => {
            let c = pal.of(part.color.as_ref(), "hair");
            px(ui, host, 3.0, 2.0, 18.0, 1.0, scale, c);
            px(ui, host, 3.0, 21.0, 18.0, 1.0, scale, c);
            px(ui, host, 2.0, 3.0, 1.0, 18.0, scale, c);
            px(ui, host, 21.0, 3.0, 1.0, 18.0, scale, c);
            px(ui, host, 3.0, 3.0, 1.0, 1.0, scale, c);
            px(ui, host, 20.0, 3.0, 1.0, 1.0, scale, c);
            px(ui, host, 3.0, 20.0, 1.0, 1.0, scale, c);
            px(ui, host, 20.0, 20.0, 1.0, 1.0, scale, c);
        }
        "eyes" => draw_eyes(ui, host, part, pal, scale, motion),
        "happy-eyes" => draw_happy_eyes(ui, host, part, pal, scale, motion),
        "brow" => draw_brow(ui, host, part, pal, scale),
        "mouth" => draw_mouth(ui, host, part, pal, scale),
        "rects" => {
            let fallback = pal.of(part.color.as_ref(), "ink");
            for r in &part.rects {
                let c = match r.color.as_ref() {
                    Some(n) => pal.get(n),
                    None => fallback,
                };
                px(ui, host, r.x, r.y, r.w, r.h, scale, c);
            }
        }
        _ => {}
    }
}

fn draw_eyes(ui: &Ui, host: &El, part: &Part, pal: &Palette, scale: f32, motion: &Motion) {
    let w = if part.w > 0.0 { part.w } else { 6.0 };
    let h = if part.h > 0.0 { part.h } else { 4.0 };
    let right = if part.right_x > 0.0 {
        part.right_x
    } else {
        23.0 - (part.x + w - 1.0)
    };
    let rim = pal.of(part.rim.as_ref(), "ink");
    let sock = pal.of(part.sock.as_ref(), "sock");
    let pupil_c = pal.of(part.pupil.as_ref(), "ink");
    let fill = pal.get("fill");
    let pw = if part.pupil_w > 0.0 { part.pupil_w } else { 2.0 };
    let ph = if part.pupil_h > 0.0 { part.pupil_h } else { 2.0 };
    let mut pxp = part.pupil_x + motion.glance_x as f32;
    // Keep the pupil inside the interior (1 .. w-1-pw).
    let max_x = (w - 1.0 - pw).max(1.0);
    if pxp < 1.0 {
        pxp = 1.0;
    }
    if pxp > max_x {
        pxp = max_x;
    }
    let pyp = part.pupil_y;
    if motion.blink {
        let mid = part.y + (h * 0.5).floor();
        for x in [part.x, right] {
            px(ui, host, x, mid, w, 1.0, scale, rim);
        }
        return;
    }
    for x in [part.x, right] {
        px(ui, host, x, part.y, w, h, scale, sock);
        px(ui, host, x + pxp, part.y + pyp, pw, ph, scale, pupil_c);
        if part.lid_top > 0.0 {
            px(
                ui,
                host,
                x + 1.0,
                part.y + 1.0,
                (w - 2.0).max(1.0),
                part.lid_top.min((h - 2.0).max(0.0)),
                scale,
                fill,
            );
        }
        if part.lid_bot > 0.0 {
            px(
                ui,
                host,
                x + 1.0,
                part.y + h - 1.0 - part.lid_bot,
                (w - 2.0).max(1.0),
                part.lid_bot,
                scale,
                fill,
            );
        }
        px(ui, host, x, part.y, w, 1.0, scale, rim);
        px(ui, host, x, part.y + h - 1.0, w, 1.0, scale, rim);
        px(ui, host, x, part.y, 1.0, h, scale, rim);
        px(ui, host, x + w - 1.0, part.y, 1.0, h, scale, rim);
    }
}

fn draw_happy_eyes(ui: &Ui, host: &El, part: &Part, pal: &Palette, scale: f32, motion: &Motion) {
    let c = pal.of(part.color.as_ref(), "ink");
    let y = if part.y > 0.0 { part.y } else { 10.0 };
    if motion.blink {
        px(ui, host, 5.0, y, 6.0, 1.0, scale, c);
        px(ui, host, 13.0, y, 6.0, 1.0, scale, c);
        return;
    }
    // ∪ mid higher, outer corners lower. Left 5–10, right 13–18.
    px(ui, host, 6.0, y, 4.0, 1.0, scale, c);
    px(ui, host, 5.0, y + 1.0, 1.0, 1.0, scale, c);
    px(ui, host, 10.0, y + 1.0, 1.0, 1.0, scale, c);
    px(ui, host, 14.0, y, 4.0, 1.0, scale, c);
    px(ui, host, 13.0, y + 1.0, 1.0, 1.0, scale, c);
    px(ui, host, 18.0, y + 1.0, 1.0, 1.0, scale, c);
}

fn draw_brow(ui: &Ui, host: &El, part: &Part, pal: &Palette, scale: f32) {
    let c = pal.of(part.color.as_ref(), "ink");
    let w = if part.w > 0.0 { part.w } else { 6.0 };
    match part.shape.as_deref().unwrap_or("flat") {
        "arc" => {
            px(ui, host, part.x, part.y + 1.0, 1.0, 1.0, scale, c);
            px(ui, host, part.x + 1.0, part.y, (w - 2.0).max(1.0), 1.0, scale, c);
            px(ui, host, part.x + w - 1.0, part.y + 1.0, 1.0, 1.0, scale, c);
        }
        "knit" => {
            // Inner lower, outer higher.
            px(ui, host, 5.0, part.y, 2.0, 1.0, scale, c);
            px(ui, host, 7.0, part.y + 1.0, 4.0, 1.0, scale, c);
            px(ui, host, 13.0, part.y + 1.0, 4.0, 1.0, scale, c);
            px(ui, host, 17.0, part.y, 2.0, 1.0, scale, c);
        }
        "worry" => {
            px(ui, host, 5.0, part.y + 1.0, 2.0, 1.0, scale, c);
            px(ui, host, 7.0, part.y, 4.0, 1.0, scale, c);
            px(ui, host, 13.0, part.y, 4.0, 1.0, scale, c);
            px(ui, host, 17.0, part.y + 1.0, 2.0, 1.0, scale, c);
        }
        _ => {
            px(ui, host, part.x, part.y, w, 1.0, scale, c);
        }
    }
}

fn draw_mouth(ui: &Ui, host: &El, part: &Part, pal: &Palette, scale: f32) {
    let c = pal.of(part.color.as_ref(), "ink");
    let mute = pal.get("mute");
    let ink = pal.get("ink");
    match part.shape.as_deref().unwrap_or("line") {
        "open" => {
            let w = if part.w > 0.0 { part.w } else { 2.0 };
            let h = if part.h > 0.0 { part.h } else { 2.0 };
            let y = if part.y > 0.0 { part.y } else { 16.0 };
            let x = ((24.0 - w) / 2.0).floor();
            px(ui, host, x, y, w, h, scale, c);
        }
        "slot" => {
            // A small open mouth: top bar, lower corners — not a solid block.
            let w = if part.w > 0.0 { part.w } else { 4.0 };
            let h = if part.h > 0.0 { part.h } else { 2.0 };
            let y = if part.y > 0.0 { part.y } else { 16.0 };
            let x = ((24.0 - w) / 2.0).floor();
            px(ui, host, x, y, w, 1.0, scale, c);
            if h >= 2.0 {
                px(ui, host, x, y + h - 1.0, 1.0, 1.0, scale, c);
                px(ui, host, x + w - 1.0, y + h - 1.0, 1.0, 1.0, scale, c);
                if h > 2.0 {
                    px(ui, host, x, y + 1.0, 1.0, h - 2.0, scale, c);
                    px(ui, host, x + w - 1.0, y + 1.0, 1.0, h - 2.0, scale, c);
                }
            }
        }
        "o" => {
            let y = if part.y > 0.0 { part.y } else { 16.0 };
            px(ui, host, 11.0, y, 2.0, 1.0, scale, c);
            px(ui, host, 10.0, y + 1.0, 1.0, 1.0, scale, c);
            px(ui, host, 13.0, y + 1.0, 1.0, 1.0, scale, c);
            px(ui, host, 11.0, y + 2.0, 2.0, 1.0, scale, c);
        }
        "smile" => {
            // ∪ mid higher (lower y), corners lower on the screen.
            px(ui, host, 8.0, 17.0, 2.0, 1.0, scale, c);
            px(ui, host, 10.0, 16.0, 4.0, 1.0, scale, c);
            px(ui, host, 14.0, 17.0, 2.0, 1.0, scale, c);
        }
        "small-smile" => {
            px(ui, host, 9.0, 16.0, 1.0, 1.0, scale, c);
            px(ui, host, 10.0, 15.0, 4.0, 1.0, scale, c);
            px(ui, host, 14.0, 16.0, 1.0, 1.0, scale, c);
        }
        "frown" => {
            px(ui, host, 8.0, 16.0, 2.0, 1.0, scale, c);
            px(ui, host, 10.0, 17.0, 4.0, 1.0, scale, c);
            px(ui, host, 14.0, 16.0, 2.0, 1.0, scale, c);
        }
        "hmm" => {
            let y = if part.y > 0.0 { part.y } else { 17.0 };
            px(ui, host, 8.0, y, 2.0, 1.0, scale, mute);
            px(ui, host, 11.0, y, 2.0, 1.0, scale, ink);
            px(ui, host, 14.0, y, 2.0, 1.0, scale, mute);
        }
        "grit" => {
            let y = if part.y > 0.0 { part.y } else { 16.0 };
            let w = if part.w > 0.0 { part.w } else { 6.0 };
            let x = ((24.0 - w) / 2.0).floor();
            px(ui, host, x, y, w, 1.0, scale, c);
        }
        _ => {
            let y = if part.y > 0.0 { part.y } else { 16.0 };
            let w = if part.w > 0.0 { part.w } else { 4.0 };
            let x = ((24.0 - w) / 2.0).floor();
            px(ui, host, x, y, w, 1.0, scale, c);
        }
    }
}

fn px(ui: &Ui, host: &El, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) {
    if w <= 0.0 || h <= 0.0 {
        return;
    }
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

// Quiet the unused import if State is only used via Mood.
#[allow(dead_code)]
fn _state_link(s: State) -> State {
    s
}
