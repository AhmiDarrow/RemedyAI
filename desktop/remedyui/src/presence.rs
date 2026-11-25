//! Remedy's living presence — a skinnable face with moods and quiet growth.
//!
//! The theme pack picks a face skin (`[layout.presence] skin`). Motion is a
//! 1px breath, a blink, an idle glance, or a short settle, and only when
//! motion is allowed. Advanced users can turn the face off.

use std::cell::Cell;
use std::time::Duration;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Color;
use remedy_ui::layout::{Length, Position};
use remedy_ui::reactive::Signal;
use remedy_ui::{El, Ui};

use crate::face_draw;
use crate::face_proc;
use crate::face_skin;
use crate::studio::{Conn, Studio, WorkMode};
use crate::work::StepStatus;

thread_local! {
    static SKIN_OVERRIDE: Cell<Option<&'static str>> = const { Cell::new(None) };
    static STAGE_OVERRIDE: Cell<Option<u8>> = const { Cell::new(None) };
    static FACTS: Cell<face_skin::Facts> = const { Cell::new(face_skin::Facts { sessions: 0, messages: 0, days: 0, goals: 0 }) };
    static HOUR_OVERRIDE: Cell<Option<u32>> = const { Cell::new(None) };
    static ENABLED_OVERRIDE: Cell<Option<bool>> = const { Cell::new(None) };
}

/// Bundled face ids. The source of truth is the skin file; this enum is
/// a convenience for design previews and the theme pack.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Square,
    MorningStar,
    Wolf,
    Bear,
}

impl Kind {
    pub fn id(self) -> &'static str {
        match self {
            Self::Square => "square",
            Self::MorningStar => "morning-star",
            Self::Wolf => "wolf",
            Self::Bear => "bear",
        }
    }

    pub fn from_id(id: &str) -> Option<Self> {
        match id {
            "square" => Some(Self::Square),
            "morning-star" | "morning_star" => Some(Self::MorningStar),
            "wolf" => Some(Self::Wolf),
            "bear" => Some(Self::Bear),
            _ => None,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Square => "Square",
            Self::MorningStar => "Morning Star",
            Self::Wolf => "Wolf Guide",
            Self::Bear => "Bear Guide",
        }
    }

    pub fn blurb(self) -> &'static str {
        match self {
            Self::Square => {
                "A living square face. Neutral, crafted, and skinnable with the theme."
            }
            Self::MorningStar => {
                "An abstract forest spirit. Optional bundled skin."
            }
            Self::Wolf => {
                "A geometric wolf, drawn like beadwork. Optional bundled skin."
            }
            Self::Bear => {
                "A broader bear, with a small pine. Optional bundled skin."
            }
        }
    }
}

pub fn set_kind_override(kind: Option<Kind>) {
    SKIN_OVERRIDE.with(|c| c.set(kind.map(Kind::id)));
}

pub fn set_skin_override(id: Option<&'static str>) {
    SKIN_OVERRIDE.with(|c| c.set(id));
}

pub fn set_stage_override(stage: Option<u8>) {
    STAGE_OVERRIDE.with(|c| c.set(stage));
}

pub fn set_hour_override(hour: Option<u32>) {
    HOUR_OVERRIDE.with(|c| c.set(hour));
}

/// Force presence on or off, or `None` to follow the Appearance pref.
pub fn set_enabled_override(on: Option<bool>) {
    ENABLED_OVERRIDE.with(|c| c.set(on));
}

pub fn kind() -> Kind {
    Kind::from_id(skin_id()).unwrap_or(Kind::Square)
}

pub fn skin_id() -> &'static str {
    SKIN_OVERRIDE
        .with(|c| c.get())
        .unwrap_or_else(crate::theme_pack::presence_skin)
}

pub fn current_stage() -> u8 {
    STAGE_OVERRIDE
        .with(|c| c.get())
        .unwrap_or(0)
}

/// How Remedy is holding her face — the rich mood set.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mood {
    Idle,
    Listening,
    Thinking,
    Working,
    Focused,
    Curious,
    Happy,
    Proud,
    Sleepy,
    Concerned,
    Error,
    Surprised,
}

impl Mood {
    pub fn all() -> [Mood; 12] {
        [
            Mood::Idle,
            Mood::Listening,
            Mood::Thinking,
            Mood::Working,
            Mood::Focused,
            Mood::Curious,
            Mood::Happy,
            Mood::Proud,
            Mood::Sleepy,
            Mood::Concerned,
            Mood::Error,
            Mood::Surprised,
        ]
    }

    pub fn key(self) -> &'static str {
        match self {
            Mood::Idle => "idle",
            Mood::Listening => "listening",
            Mood::Thinking => "thinking",
            Mood::Working => "working",
            Mood::Focused => "focused",
            Mood::Curious => "curious",
            Mood::Happy => "happy",
            Mood::Proud => "proud",
            Mood::Sleepy => "sleepy",
            Mood::Concerned => "concerned",
            Mood::Error => "error",
            Mood::Surprised => "surprised",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Mood::Idle => "Idle",
            Mood::Listening => "Listening",
            Mood::Thinking => "Thinking",
            Mood::Working => "Working",
            Mood::Focused => "Focused",
            Mood::Curious => "Curious",
            Mood::Happy => "Happy",
            Mood::Proud => "Proud",
            Mood::Sleepy => "Sleepy",
            Mood::Concerned => "Concerned",
            Mood::Error => "Error",
            Mood::Surprised => "Surprised",
        }
    }

    pub fn caption(self) -> &'static str {
        match self {
            Mood::Idle => "Remedy is here when you need her.",
            Mood::Listening => "Ready for your next ask.",
            Mood::Thinking => "Considering the work.",
            Mood::Working => "At work on your request.",
            Mood::Focused => "Locked on the task.",
            Mood::Curious => "Waiting on what you'll say next.",
            Mood::Happy => "The last turn is finished.",
            Mood::Proud => "A solid piece of work, done.",
            Mood::Sleepy => "Quiet hours. She's still here.",
            Mood::Concerned => "Something in the last turn went off.",
            Mood::Error => "Something needs your attention.",
            Mood::Surprised => "A new ask just landed.",
        }
    }

    /// Collapse a mood onto the older six-state set (for legacy skins).
    pub fn as_state(self) -> State {
        match self {
            Mood::Idle | Mood::Sleepy => State::Idle,
            Mood::Listening | Mood::Curious => State::Listening,
            Mood::Thinking => State::Thinking,
            Mood::Working | Mood::Focused => State::Working,
            Mood::Happy | Mood::Proud | Mood::Surprised => State::Done,
            Mood::Concerned | Mood::Error => State::Error,
        }
    }
}

/// Older six-state set, kept so existing call sites and design6 previews compile.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Idle,
    Listening,
    Thinking,
    Working,
    Done,
    Error,
}

impl State {
    pub fn label(self) -> &'static str {
        Mood::from(self).label()
    }

    pub fn caption(self) -> &'static str {
        Mood::from(self).caption()
    }
}

impl From<State> for Mood {
    fn from(s: State) -> Self {
        match s {
            State::Idle => Mood::Idle,
            State::Listening => Mood::Listening,
            State::Thinking => Mood::Thinking,
            State::Working => Mood::Working,
            State::Done => Mood::Happy,
            State::Error => Mood::Error,
        }
    }
}

/// Presence is on unless the person turned it off in Appearance.
///
/// `None` means no settings are loaded yet, so the theme pack default applies
/// (Dark Forest ships this as on).
pub fn enabled_with(pref: Option<bool>) -> bool {
    pref.unwrap_or(crate::theme_pack::layout().presence_enabled_default)
}

/// Presence is on unless the person turned it off in Appearance.
pub fn enabled() -> bool {
    if let Some(on) = ENABLED_OVERRIDE.with(|c| c.get()) {
        return on;
    }
    enabled_with(
        crate::settings::current()
            .and_then(|s| s.show_presence.try_get().ok()),
    )
}

/// Pick a mood from the live Studio and the clock.
///
/// Priority, first match wins:
/// 1. Error — connection failure, or the last turn's steps all failed
/// 2. Concerned — the last turn mixed success and failure
/// 3. Working — tools are running (Focused if Build, or three or more steps)
/// 4. Thinking — a turn is streaming with thinking text
/// 5. Surprised — the first user message of a session is in flight, no thinking yet
/// 6. Proud — the last turn finished with three or more clean tool steps
/// 7. Happy — the last turn finished cleanly
/// 8. Curious — the composer has a draft, and she is not mid-turn
/// 9. Sleepy — idle or listening, in the quiet hours (midnight–5)
/// 10. Listening — connected, waiting
/// 11. Idle — default rest
pub fn from_studio(studio: &Studio) -> Mood {
    // Recount only when the session list changes size; the count is what growth reads.
    if studio.sessions.get().len() as u32 != FACTS.with(|c| c.get()).sessions {
        FACTS.with(|c| c.set(face_skin::Facts::from_studio(studio)));
    }
    mood_from(studio)
}

pub fn mood_from(studio: &Studio) -> Mood {
    match studio.conn.get() {
        Conn::Error(_) => return Mood::Error,
        _ => {}
    }

    if studio.streaming.get() {
        let steps = studio.live_steps.get();
        let running = steps.iter().any(|s| s.status == StepStatus::Running);
        if running {
            if steps.len() >= 3 || studio.mode.get() == WorkMode::Build {
                return Mood::Focused;
            }
            return Mood::Working;
        }
        let thinking = !studio.live_thinking.get().trim().is_empty();
        let messages = studio.messages.get();
        let only_first_user = messages.len() == 1 && messages[0].role == "user";
        if only_first_user && !thinking && steps.is_empty() {
            return Mood::Surprised;
        }
        if thinking {
            return Mood::Thinking;
        }
        return Mood::Working;
    }

    let messages = studio.messages.get();
    if messages.is_empty() {
        let draft = composer_len(studio);
        if draft > 0 {
            return Mood::Curious;
        }
        let rest = match studio.conn.get() {
            Conn::Ready => Mood::Listening,
            _ => Mood::Idle,
        };
        return maybe_sleepy(rest);
    }

    let (ok, err) = face_skin::last_turn_steps(studio);
    if err > 0 && ok == 0 {
        return Mood::Error;
    }
    if err > 0 {
        return Mood::Concerned;
    }
    if ok >= 3 {
        return Mood::Proud;
    }

    let draft = composer_len(studio);
    if draft > 0 {
        return Mood::Curious;
    }

    let last_user_q = messages
        .iter()
        .rev()
        .find(|m| m.role == "user")
        .is_some_and(|m| m.content.trim().ends_with('?'));
    if last_user_q && messages.last().is_some_and(|m| m.role == "user") {
        return Mood::Curious;
    }

    maybe_sleepy(Mood::Happy)
}

fn composer_len(studio: &Studio) -> usize {
    let a = studio.depth.composer_value.get();
    let b = studio.depth.draft_text.get();
    a.trim().len().max(b.trim().len())
}

fn maybe_sleepy(rest: Mood) -> Mood {
    if !matches!(rest, Mood::Idle | Mood::Listening) {
        return rest;
    }
    let hour = HOUR_OVERRIDE
        .with(|c| c.get())
        .unwrap_or_else(local_hour);
    if hour < 6 {
        Mood::Sleepy
    } else {
        rest
    }
}

fn local_hour() -> u32 {
    #[cfg(windows)]
    {
        #[repr(C)]
        struct SystemTime {
            _year: u16,
            _month: u16,
            _dow: u16,
            _day: u16,
            hour: u16,
            _minute: u16,
            _second: u16,
            _milli: u16,
        }
        unsafe extern "system" {
            fn GetLocalTime(out: *mut SystemTime);
        }
        unsafe {
            let mut st = std::mem::zeroed::<SystemTime>();
            GetLocalTime(&mut st);
            return u32::from(st.hour);
        }
    }
    #[cfg(not(windows))]
    {
        unsafe extern "C" {
            fn time(t: *mut i64) -> i64;
            fn localtime_r(t: *const i64, out: *mut u8) -> *mut u8;
        }
        unsafe {
            let mut secs = 0i64;
            time(&mut secs);
            let mut buf = [0u8; 64];
            if !localtime_r(&secs, buf.as_mut_ptr()).is_null() {
                let hour = i32::from_ne_bytes(buf[8..12].try_into().unwrap_or([0; 4]));
                if (0..24).contains(&hour) {
                    return hour as u32;
                }
            }
        }
        9
    }
}

/// The mark at `size` px (a multiple of 8 stays sharp). Hidden when turned off.
pub fn mark(ui: &Ui, mood: impl Into<Mood>, size: f32) -> El {
    if !enabled() {
        return ui.el("presence-off").layout(|l| {
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
        });
    }
    draw(ui, mood.into(), size, current_stage())
}

/// Always drawn, for design-review (ignores the setting).
pub fn preview(ui: &Ui, mood: impl Into<Mood>, size: f32) -> El {
    draw(ui, mood.into(), size, current_stage())
}

/// Design-review at an explicit growth stage.
pub fn preview_stage(ui: &Ui, mood: impl Into<Mood>, size: f32, stage: u8) -> El {
    draw(ui, mood.into(), size, stage)
}

/// Design-review of a grown face for an explicit seed and usage.
pub fn preview_grown(ui: &Ui, mood: impl Into<Mood>, size: f32, seed: u64, facts: face_skin::Facts) -> El {
    draw_with(ui, mood.into(), size, 0, Some((seed, facts)))
}

/// The face design shots and tests show when no install seed applies.
const DESIGN_SEED: u64 = 0x5EED;

fn draw(ui: &Ui, mood: Mood, size: f32, stage: u8) -> El {
    // Live faces grow from this install's seed and real use. Design shots and
    // tests pin a stage instead.
    let facts = FACTS.with(|c| c.get());
    let live = STAGE_OVERRIDE.with(|c| c.get()).is_none() && facts.sessions > 0;
    draw_with(ui, mood, size, stage, live.then(|| (face_skin::seed().unwrap_or(DESIGN_SEED), facts)))
}

fn draw_with(ui: &Ui, mood: Mood, size: f32, stage: u8, grow: Option<(u64, face_skin::Facts)>) -> El {
    let size = size.max(16.0);
    let grid = crate::theme_pack::layout().presence_grid.max(1.0);
    let scale = size / grid;
    let colors = ui.theme().colors.clone();
    let motion_on = !ui.reduced_motion();
    let phase = Signal::new(0u8);
    let blink = Signal::new(false);
    let glance = Signal::new(0i8);
    let settle = Signal::new(0i8);

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
        .semantics(Semantics::new(Role::Image).label(format!("Remedy, {}", mood.label())));

    let skin = face_skin::load(skin_id());
    let resolved = skin.resolve(mood.key(), stage);
    let do_blink = skin.allows_blink(&resolved.pose);
    let do_glance = skin.allows_glance(mood.key(), &resolved.pose);
    let do_breath = skin.allows_breath(&resolved.pose);
    let motion = face_draw::Motion {
        blink: blink.clone(),
        glance_x: glance.clone(),
    };

    // Square, the default, is procedural. A user or legacy skin keeps its own painter.
    if skin.id == "square" && skin.engine != "legacy" {
        let (seed, facts) = grow.unwrap_or((DESIGN_SEED, face_proc::facts_for_stage(stage)));
        face_proc::paint(ui, &frame, seed, facts, mood, size, &colors, motion);
    } else {
        face_draw::paint(ui, &frame, &skin, mood, scale, &colors, stage, motion);
    }

    if motion_on && (do_breath || do_blink || do_glance) {
        let frame_b = frame.clone();
        let phase_b = phase.clone();
        let blink_b = blink.clone();
        let glance_b = glance.clone();
        let settle_b = settle.clone();
        frame.clone().bind(move || {
            let p = phase_b.get();
            let breath = if do_breath && p % 8 >= 4 { 1.0 } else { 0.0 };
            let settle_y = settle_b.get() as f32;
            frame_b.clone().layout(move |l| {
                l.margin.top = breath + settle_y;
            });
            let _ = blink_b.get();
            let _ = glance_b.get();
        });
        // A one-tick settle when the face first appears.
        settle.set(1);
        let settle_c = settle.clone();
        let ui_s = ui.clone();
        ui_s.after(Duration::from_millis(120), move || {
            // The face may already be gone if the surface switched.
            let _ = settle_c.try_set(0);
        });
        schedule_motion(
            ui.clone(),
            phase,
            blink,
            glance,
            do_blink,
            do_glance,
        );
    }

    frame
}

fn schedule_motion(
    ui: Ui,
    phase: Signal<u8>,
    blink: Signal<bool>,
    glance: Signal<i8>,
    do_blink: bool,
    do_glance: bool,
) {
    let again = ui.clone();
    ui.after(Duration::from_millis(600), move || {
        // Stop quietly if the face was torn down (Grove/Studio switch, unmount).
        if phase.try_update(|n| *n = n.wrapping_add(1)).is_err() {
            return;
        }
        let n = match phase.with_untracked(|n| *n) {
            Ok(n) => n,
            Err(_) => return,
        };
        if do_glance && n % 10 == 3 {
            let dir = if n % 20 == 3 { 1 } else { -1 };
            let _ = glance.try_set(dir);
        } else if do_glance && n % 10 == 6 {
            let _ = glance.try_set(0);
        }
        if do_blink && n % 16 == 0 {
            if blink.try_set(true).is_err() {
                return;
            }
            let blink = blink.clone();
            let phase = phase.clone();
            let glance = glance.clone();
            let again2 = again.clone();
            again.after(Duration::from_millis(90), move || {
                let _ = blink.try_set(false);
                if phase.with_untracked(|_| ()).is_ok() {
                    schedule_motion(again2, phase, blink, glance, do_blink, do_glance);
                }
            });
        } else {
            schedule_motion(again, phase, blink, glance, do_blink, do_glance);
        }
    });
}

/// Unused import guard for Color in this module (layout only uses Size now).
#[allow(dead_code)]
fn _color(c: Color) -> Color {
    c
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_mood_has_a_plain_name() {
        for mood in Mood::all() {
            assert!(!mood.label().is_empty());
            assert!(!mood.caption().is_empty());
            assert!(!mood.key().is_empty());
        }
    }

    #[test]
    fn state_maps_into_mood() {
        assert_eq!(Mood::from(State::Done), Mood::Happy);
        assert_eq!(Mood::from(State::Idle), Mood::Idle);
        assert_eq!(Mood::Happy.as_state(), State::Done);
    }

    #[test]
    fn presence_defaults_on_and_the_pref_can_hide_it() {
        assert!(crate::theme_pack::layout().presence_enabled_default);
        assert!(enabled_with(None));
        assert!(enabled_with(Some(true)));
        assert!(!enabled_with(Some(false)));
    }
}
