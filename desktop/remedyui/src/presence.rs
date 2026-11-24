//! A geometric, pixel-crafted presence for Remedy.
//!
//! Drawn on a 24-unit grid with stepped corners and square marks — the same
//! language as Joe's site, not a blob, orb, or glow. Motion is a 1px breath
//! or a blink, and only when motion is allowed. Advanced users can turn it off.

use std::time::Duration;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Color;
use remedy_ui::layout::{Length, Position};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::{El, Ui};

use std::cell::Cell;

use crate::studio::{Conn, Studio};
use crate::tokens;
use crate::work::StepStatus;

thread_local! {
    static KIND_OVERRIDE: Cell<Option<Kind>> = const { Cell::new(None) };
}

/// Which forest-spirit form to draw. Joe can pick; the theme pack holds the default.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// Abstract spirit with a morning-star motif from Lakota star quilts.
    MorningStar,
    /// Beadwork-style wolf guide.
    Wolf,
    /// Beadwork-style bear guide, with Black Hills pines.
    Bear,
}

impl Kind {
    pub fn label(self) -> &'static str {
        match self {
            Self::MorningStar => "Morning Star",
            Self::Wolf => "Wolf Guide",
            Self::Bear => "Bear Guide",
        }
    }

    pub fn blurb(self) -> &'static str {
        match self {
            Self::MorningStar => {
                "An abstract forest spirit. The morning star from Lakota star quilts sits at the brow, over a short beadwork band."
            }
            Self::Wolf => {
                "A geometric wolf, drawn like beadwork: stepped ears, a banded muzzle, and a square nose. Ears and gaze carry the states."
            }
            Self::Bear => {
                "A broader bear, with stepped ears, a quiet earth band, and a small pine between the ears for Black Hills forest growth."
            }
        }
    }
}

/// Set for a design preview. Left in place until the next shot sets it.
pub fn set_kind_override(kind: Option<Kind>) {
    KIND_OVERRIDE.with(|c| c.set(kind));
}

pub fn kind() -> Kind {
    KIND_OVERRIDE
        .with(|c| c.get())
        .unwrap_or_else(crate::theme_pack::presence_kind)
}

/// How Remedy's mark is holding itself.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// At rest, nothing asked of her yet.
    Idle,
    /// Ready for the next thing the person types.
    Listening,
    /// Reading, planning, or thinking.
    Thinking,
    /// Tools or a turn in flight.
    Working,
    /// The last turn finished cleanly.
    Done,
    /// A connection or tool failure.
    Error,
}

impl State {
    pub fn label(self) -> &'static str {
        match self {
            State::Idle => "Idle",
            State::Listening => "Listening",
            State::Thinking => "Thinking",
            State::Working => "Working",
            State::Done => "Done",
            State::Error => "Error",
        }
    }

    pub fn caption(self) -> &'static str {
        match self {
            State::Idle => "Remedy is here when you need her.",
            State::Listening => "Ready for your next ask.",
            State::Thinking => "Considering the work.",
            State::Working => "At work on your request.",
            State::Done => "The last turn is finished.",
            State::Error => "Something needs your attention.",
        }
    }
}

/// Presence is on unless the person turned it off in Appearance.
pub fn enabled() -> bool {
    crate::settings::current()
        .and_then(|s| s.show_presence.try_get().ok())
        .unwrap_or(crate::theme_pack::layout().presence_enabled_default)
}

/// Pick a state from the live Studio.
pub fn from_studio(studio: &Studio) -> State {
    match studio.conn.get() {
        Conn::Error(_) => return State::Error,
        _ => {}
    }
    if studio.streaming.get() {
        let running = studio.live_steps.get().iter().any(|s| s.status == StepStatus::Running);
        if running {
            return State::Working;
        }
        if !studio.live_thinking.get().trim().is_empty() {
            return State::Thinking;
        }
        return State::Working;
    }
    let messages = studio.messages.get();
    if messages.is_empty() {
        return match studio.conn.get() {
            Conn::Ready => State::Listening,
            _ => State::Idle,
        };
    }
    let failed = messages.iter().rev().any(|m| {
        m.role == "assistant" && m.steps.iter().any(|s| s.status == StepStatus::Error)
    });
    if failed {
        return State::Error;
    }
    State::Done
}

/// The mark at `size` px (a multiple of 8 stays sharp). Hidden when turned off.
pub fn mark(ui: &Ui, state: State, size: f32) -> El {
    if !enabled() {
        return ui.el("presence-off").layout(|l| {
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
        });
    }
    draw(ui, state, size, false)
}

/// Always drawn, for design-review of each state (ignores the setting).
pub fn preview(ui: &Ui, state: State, size: f32) -> El {
    draw(ui, state, size, true)
}

fn draw(ui: &Ui, state: State, size: f32, force: bool) -> El {
    let _ = force;
    let size = size.max(16.0);
    let scale = size / 24.0;
    let colors = ui.theme().colors.clone();
    let motion = !ui.reduced_motion();
    let phase = Signal::new(0u8);
    let blink = Signal::new(false);

    let frame = ui
        .el("presence")
        .layout(move |l| {
            l.position = Position::Relative;
            l.width = Length::Px(size);
            l.height = Length::Px(size);
            l.min_width = Length::Px(size);
            l.min_height = Length::Px(size);
            l.max_width = Length::Px(size);
            l.max_height = Length::Px(size);
            l.shrink = 0.0;
        })
        .semantics(Semantics::new(Role::Image).label(format!("Remedy, {}", state.label())));

    paint(ui, &frame, state, scale, &colors, blink.get());

    if motion {
        let frame_b = frame.clone();
        let phase_b = phase.clone();
        let blink_b = blink.clone();
        frame.clone().bind(move || {
            let p = phase_b.get();
            let y = if p % 8 < 4 { 0.0 } else { 1.0 };
            frame_b.clone().layout(move |l| {
                l.margin.top = y;
            });
            let _ = blink_b.get();
        });
        schedule_breath(ui.clone(), phase, blink);
    }

    frame
}

fn schedule_breath(ui: Ui, phase: Signal<u8>, blink: Signal<bool>) {
    let again = ui.clone();
    ui.after(Duration::from_millis(500), move || {
        phase.update(|n| *n = n.wrapping_add(1));
        let n = phase.get();
        // A one-tick blink every sixteen steps. No fade.
        blink.set(n % 16 == 0);
        if blink.get() {
            let blink = blink.clone();
            let phase = phase.clone();
            let again2 = again.clone();
            again.after(Duration::from_millis(80), move || {
                blink.set(false);
                schedule_breath(again2, phase, blink);
            });
        } else {
            schedule_breath(again, phase, blink);
        }
    });
}

fn paint(ui: &Ui, host: &El, state: State, scale: f32, colors: &remedy_ui::style::ColorRoles, blink: bool) {
    let p = crate::presence_draw::palette(state, colors);
    match kind() {
        Kind::MorningStar => crate::presence_draw::morning_star(ui, host, state, scale, &p, blink),
        Kind::Wolf => crate::presence_draw::wolf(ui, host, state, scale, &p, blink),
        Kind::Bear => crate::presence_draw::bear(ui, host, state, scale, &p, blink),
    }
}

#[allow(dead_code)]
fn paint_legacy(ui: &Ui, host: &El, state: State, scale: f32, colors: &remedy_ui::style::ColorRoles, blink: bool) {
    let hair = match state {
        State::Error => colors.danger,
        State::Working | State::Listening | State::Done => colors.accent,
        _ => tokens::hairline(colors),
    };
    let fill = colors.panel;
    let ink = match state {
        State::Error => colors.danger,
        _ => colors.accent,
    };
    let mute = colors.muted;

    // Stepped frame, corners cut by 2 units. No rounded blob.
    let _ = host.clone().child(px(ui, 2.0, 1.0, 20.0, 22.0, scale, fill));
    let _ = host.clone().child(px(ui, 1.0, 2.0, 22.0, 20.0, scale, fill));
    let _ = host.clone().child(px(ui, 2.0, 0.0, 20.0, 1.0, scale, hair));
    let _ = host.clone().child(px(ui, 2.0, 23.0, 20.0, 1.0, scale, hair));
    let _ = host.clone().child(px(ui, 0.0, 2.0, 1.0, 20.0, scale, hair));
    let _ = host.clone().child(px(ui, 23.0, 2.0, 1.0, 20.0, scale, hair));
    let _ = host.clone().child(px(ui, 1.0, 1.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px(ui, 22.0, 1.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px(ui, 1.0, 22.0, 1.0, 1.0, scale, hair));
    let _ = host.clone().child(px(ui, 22.0, 22.0, 1.0, 1.0, scale, hair));

    match state {
        State::Idle => {
            // Half-lidded, at rest.
            eyes(ui, host, scale, ink, 6.0, 9.0, 3.0, 2.0, blink);
            let _ = host.clone().child(px(ui, 10.0, 16.0, 4.0, 1.0, scale, mute));
        }
        State::Listening => {
            // Open eyes, raised brows, a small open mouth.
            // Right marks sit at 15+w, same as eyes().
            let _ = host.clone().child(px(ui, 6.0, 6.0, 3.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 18.0, 6.0, 3.0, 1.0, scale, ink));
            eyes(ui, host, scale, ink, 6.0, 8.0, 3.0, 4.0, blink);
            let _ = host.clone().child(px(ui, 11.0, 16.0, 2.0, 2.0, scale, mute));
        }
        State::Thinking => {
            // Gaze shifts right; one brow lifts; a stepped ellipsis.
            let _ = host.clone().child(px(ui, 8.0, 7.0, 3.0, 1.0, scale, mute));
            let _ = host.clone().child(px(ui, 18.0, 6.0, 3.0, 1.0, scale, ink));
            eyes(ui, host, scale, ink, 8.0, 8.0, 3.0, 3.0, blink);
            let _ = host.clone().child(px(ui, 9.0, 17.0, 2.0, 2.0, scale, mute));
            let _ = host.clone().child(px(ui, 12.0, 17.0, 2.0, 2.0, scale, ink));
            let _ = host.clone().child(px(ui, 15.0, 17.0, 2.0, 2.0, scale, mute));
        }
        State::Working => {
            // Lowered brows, a squint, working ticks.
            let _ = host.clone().child(px(ui, 5.0, 8.0, 4.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 19.0, 8.0, 4.0, 1.0, scale, ink));
            eyes(ui, host, scale, ink, 5.0, 10.0, 4.0, 2.0, blink);
            let _ = host.clone().child(px(ui, 8.0, 17.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 11.0, 17.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 14.0, 17.0, 2.0, 1.0, scale, mute));
        }
        State::Done => {
            // Open, pleased eyes and a clear stepped uptick — not a curved smile.
            let _ = host.clone().child(px(ui, 6.0, 7.0, 3.0, 1.0, scale, mute));
            let _ = host.clone().child(px(ui, 18.0, 7.0, 3.0, 1.0, scale, mute));
            eyes(ui, host, scale, ink, 6.0, 8.0, 3.0, 4.0, blink);
            let _ = host.clone().child(px(ui, 7.0, 18.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 9.0, 17.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 11.0, 16.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 13.0, 17.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 15.0, 18.0, 2.0, 1.0, scale, ink));
        }
        State::Error => {
            // Brows step inward, stern eyes, a downturn.
            let _ = host.clone().child(px(ui, 6.0, 6.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 8.0, 7.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 18.0, 7.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 20.0, 6.0, 2.0, 1.0, scale, ink));
            eyes(ui, host, scale, ink, 6.0, 9.0, 3.0, 1.0, false);
            let _ = host.clone().child(px(ui, 8.0, 16.0, 2.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 10.0, 17.0, 4.0, 1.0, scale, ink));
            let _ = host.clone().child(px(ui, 14.0, 16.0, 2.0, 1.0, scale, ink));
        }
    }
}

fn eyes(
    ui: &Ui,
    host: &El,
    scale: f32,
    color: Color,
    x: f32,
    y: f32,
    w: f32,
    h: f32,
    blink: bool,
) {
    let (ey, eh) = if blink { (y + (h - 1.0).max(0.0), 1.0) } else { (y, h) };
    let gap = 15.0 - x; // second eye mirrored around the 24-grid
    let x2 = x + w + gap;
    let _ = host.clone().child(px(ui, x, ey, w, eh, scale, color));
    let _ = host.clone().child(px(ui, x2, ey, w, eh, scale, color));
}

fn px(ui: &Ui, x: f32, y: f32, w: f32, h: f32, scale: f32, color: Color) -> El {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_state_has_a_plain_name() {
        for state in [
            State::Idle,
            State::Listening,
            State::Thinking,
            State::Working,
            State::Done,
            State::Error,
        ] {
            assert!(!state.label().is_empty());
            assert!(!state.caption().is_empty());
        }
    }
}
