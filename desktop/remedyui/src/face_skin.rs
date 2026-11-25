//! Declarative face skins for Remedy's presence.
//!
//! A theme pack picks a skin by id (`[layout.presence] skin`). Each skin is a
//! TOML file that names its colors, geometry, mood poses, motion, and growth
//! stages. Bundled skins live in `theme/faces/`. A file at
//! `$REMEDY_HOME/theme/faces/{id}.toml` overrides the bundled one. RemedyLua
//! can later register a skin with [`register`]. A bad or partial file never
//! takes down the shell — we fall back to the bundled Square.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use serde::Deserialize;

use crate::studio::Studio;
use crate::work::StepStatus;

const BUNDLED: &[(&str, &str)] = &[
    ("square", include_str!("../theme/faces/square.toml")),
    (
        "morning-star",
        include_str!("../theme/faces/morning-star.toml"),
    ),
    ("wolf", include_str!("../theme/faces/wolf.toml")),
    ("bear", include_str!("../theme/faces/bear.toml")),
];

static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();

struct Registry {
    bundled: BTreeMap<String, FaceSkin>,
    extra: BTreeMap<String, FaceSkin>,
}

fn registry() -> &'static Mutex<Registry> {
    REGISTRY.get_or_init(|| {
        let mut bundled = BTreeMap::new();
        for (id, src) in BUNDLED {
            match parse(src) {
                Ok(skin) => {
                    bundled.insert((*id).to_string(), skin);
                }
                Err(e) => {
                    eprintln!("face skin `{id}` failed to parse ({e}); skipped");
                }
            }
        }
        Mutex::new(Registry {
            bundled,
            extra: BTreeMap::new(),
        })
    })
}

/// Parse a face skin. Used by tests and by a later pack loader.
pub fn parse(src: &str) -> Result<FaceSkin, String> {
    toml::from_str::<FaceSkin>(src).map_err(|e| e.to_string())
}

/// Register (or replace) a skin at runtime. Safe to call from an extension.
pub fn register(skin: FaceSkin) {
    let id = skin.id.clone();
    if let Ok(mut reg) = registry().lock() {
        reg.extra.insert(id, skin);
    }
}

/// Load a skin by id. User files win, then registered, then bundled, then Square.
pub fn load(id: &str) -> FaceSkin {
    let user = user_path(id);
    if user.exists() {
        if let Ok(src) = std::fs::read_to_string(&user) {
            match parse(&src) {
                Ok(skin) => return skin,
                Err(e) => {
                    eprintln!(
                        "user face `{}` failed to parse ({e}); falling back",
                        user.display()
                    );
                }
            }
        }
    }
    if let Ok(reg) = registry().lock() {
        if let Some(s) = reg.extra.get(id) {
            return s.clone();
        }
        if let Some(s) = reg.bundled.get(id) {
            return s.clone();
        }
        if let Some(s) = reg.bundled.get("square") {
            return s.clone();
        }
    }
    fallback_square()
}

fn user_path(id: &str) -> PathBuf {
    crate::grove::remedy_home()
        .join("theme")
        .join("faces")
        .join(format!("{id}.toml"))
}

fn fallback_square() -> FaceSkin {
    parse(include_str!("../theme/faces/square.toml")).unwrap_or_else(|_| FaceSkin {
        id: "square".into(),
        name: "Square".into(),
        blurb: "A living square face.".into(),
        ..FaceSkin::default()
    })
}

fn default_grid() -> u32 {
    24
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct FaceSkin {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub blurb: String,
    #[serde(default = "default_grid")]
    pub grid: u32,
    /// `"legacy"` draws with the older packed painters (bundled optional skins).
    /// Anything else is drawn from this file's parts.
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub colors: BTreeMap<String, String>,
    #[serde(default)]
    pub parts: Vec<Part>,
    #[serde(default)]
    pub moods: BTreeMap<String, MoodPose>,
    #[serde(default)]
    pub growth: Vec<GrowthStage>,
    #[serde(default)]
    pub motion: MotionSpec,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct MotionSpec {
    #[serde(default = "default_true")]
    pub breath: bool,
    #[serde(default = "default_true")]
    pub blink: bool,
    #[serde(default)]
    pub glance_moods: Vec<String>,
}

fn default_true() -> bool {
    true
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct MoodPose {
    #[serde(default)]
    pub same_as: Option<String>,
    /// Optional color overrides for this mood (`hair`, `ink`, …).
    #[serde(default)]
    pub hair: Option<String>,
    #[serde(default)]
    pub ink: Option<String>,
    #[serde(default)]
    pub fill: Option<String>,
    #[serde(default)]
    pub mute: Option<String>,
    #[serde(default)]
    pub parts: Vec<Part>,
    #[serde(default)]
    pub hide: Vec<String>,
    #[serde(default)]
    pub glance: Option<bool>,
    #[serde(default)]
    pub blink: Option<bool>,
    #[serde(default)]
    pub breath: Option<bool>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct GrowthStage {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub stage: u8,
    #[serde(default)]
    pub when: String,
    #[serde(default)]
    pub parts: Vec<Part>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Part {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub x: f32,
    #[serde(default)]
    pub y: f32,
    #[serde(default)]
    pub w: f32,
    #[serde(default)]
    pub h: f32,
    #[serde(default)]
    pub right_x: f32,
    #[serde(default)]
    pub pupil_x: f32,
    #[serde(default)]
    pub pupil_y: f32,
    #[serde(default)]
    pub pupil_w: f32,
    #[serde(default)]
    pub pupil_h: f32,
    #[serde(default)]
    pub lid_top: f32,
    #[serde(default)]
    pub lid_bot: f32,
    #[serde(default)]
    pub color: Option<String>,
    #[serde(default)]
    pub hair: Option<String>,
    #[serde(default)]
    pub fill: Option<String>,
    #[serde(default)]
    pub rim: Option<String>,
    #[serde(default)]
    pub sock: Option<String>,
    #[serde(default)]
    pub pupil: Option<String>,
    #[serde(default)]
    pub shape: Option<String>,
    #[serde(default)]
    pub rects: Vec<Rect>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Rect {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    #[serde(default)]
    pub color: Option<String>,
}

/// A resolved pose: base parts, with same-named mood parts replacing them,
/// plus every growth layer up to the current stage.
pub struct Resolved<'a> {
    pub skin: &'a FaceSkin,
    pub pose: MoodPose,
    pub parts: Vec<Part>,
}

impl FaceSkin {
    pub fn resolve(&self, mood: &str, stage: u8) -> Resolved<'_> {
        let key = resolve_mood_key(self, mood);
        let pose = self.moods.get(&key).cloned().unwrap_or_default();
        let mut parts: Vec<Part> = self.parts.clone();
        for g in &self.growth {
            if g.stage <= stage && g.stage > 0 {
                for p in &g.parts {
                    upsert(&mut parts, p.clone());
                }
            }
        }
        for name in &pose.hide {
            parts.retain(|p| p.name != *name);
        }
        for p in &pose.parts {
            upsert(&mut parts, p.clone());
        }
        Resolved {
            skin: self,
            pose,
            parts,
        }
    }

    pub fn allows_glance(&self, mood: &str, pose: &MoodPose) -> bool {
        if let Some(g) = pose.glance {
            return g;
        }
        self.motion
            .glance_moods
            .iter()
            .any(|m| m == mood || m == &resolve_mood_key(self, mood))
    }

    pub fn allows_blink(&self, pose: &MoodPose) -> bool {
        pose.blink.unwrap_or(self.motion.blink)
    }

    pub fn allows_breath(&self, pose: &MoodPose) -> bool {
        pose.breath.unwrap_or(self.motion.breath)
    }
}

fn resolve_mood_key(skin: &FaceSkin, mood: &str) -> String {
    let mut key = alias_mood(mood).to_string();
    let mut guard = 0;
    while guard < 8 {
        guard += 1;
        match skin.moods.get(&key).and_then(|p| p.same_as.clone()) {
            Some(next) if next != key => key = alias_mood(&next).to_string(),
            _ => break,
        }
    }
    key
}

fn alias_mood(mood: &str) -> &str {
    match mood {
        "done" => "happy",
        "happy" => "happy",
        other => other,
    }
}

fn upsert(parts: &mut Vec<Part>, part: Part) {
    if let Some(i) = parts.iter().position(|p| p.name == part.name && !p.name.is_empty())
    {
        parts[i] = part;
    } else {
        parts.push(part);
    }
}

/// Runtime facts already on hand. We do not store a new counter.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Facts {
    pub sessions: u32,
    pub messages: u32,
    pub days: u32,
    pub goals: u32,
}

impl Facts {
    pub fn from_studio(studio: &Studio) -> Self {
        let sessions = studio.sessions.get();
        let n = sessions.len() as u32;
        let messages = sessions.iter().map(|s| s.message_count).sum();
        let mut days = 0u32;
        for s in sessions.iter() {
            if let Some(d) = days_since(s.updated_at.as_deref()) {
                days = days.max(d);
            }
        }
        for m in studio.messages.get().iter() {
            if let Some(d) = days_since(m.created_at.as_deref()) {
                days = days.max(d);
            }
        }
        let goals = crate::grove::planted_goal_count();
        Facts {
            sessions: n,
            messages,
            days,
            goals,
        }
    }
}

/// How many growth marks `facts` have earned. Logarithmic, so early use shows
/// quickly and a long companionship keeps adding.
pub fn growth_points(facts: Facts) -> u32 {
    let log = |n: u32| 32 - (n + 1).leading_zeros() - 1;
    // About 6 marks in the first weeks, 11 after a few months, the last of 19 after years.
    (log(facts.messages) + log(facts.sessions)) / 2 + log(facts.days) + facts.goals.min(4) / 2
}

/// Remedy's face seed. The runtime derives it from the setup (the owner's
/// name, Remedy's name, this install) and writes `$REMEDY_HOME/face.seed`;
/// the desktop only reads it. Before setup there is none, and she wears the
/// plain design face.
pub fn seed() -> Option<u64> {
    let path = crate::grove::remedy_home().join("face.seed");
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}
/// Growth stage 0..=3 from existing Studio / Grove facts.
///
/// - New: default
/// - Familiar: at least 8 messages, or 3 sessions, or 1 planted goal
/// - Companion: at least 40 messages, or 12 sessions, or 21 days
/// - Seasoned: 200 messages and 60 days, or 40 sessions, or 180 days
pub fn stage_from(facts: Facts) -> u8 {
    if (facts.messages >= 200 && facts.days >= 60)
        || facts.sessions >= 40
        || facts.days >= 180
    {
        return 3;
    }
    if facts.messages >= 40 || facts.sessions >= 12 || facts.days >= 21 {
        return 2;
    }
    if facts.messages >= 8 || facts.sessions >= 3 || facts.goals >= 1 {
        return 1;
    }
    0
}

fn days_since(stamp: Option<&str>) -> Option<u32> {
    let raw = stamp?;
    // RFC3339 prefix YYYY-MM-DD.
    if raw.len() < 10 {
        return None;
    }
    let y: i32 = raw[0..4].parse().ok()?;
    let mo: u32 = raw[5..7].parse().ok()?;
    let d: u32 = raw[8..10].parse().ok()?;
    let then = ymd_ord(y, mo, d)?;
    let now = local_ymd();
    Some(now.saturating_sub(then))
}

fn ymd_ord(y: i32, m: u32, d: u32) -> Option<u32> {
    if m == 0 || m > 12 || d == 0 || d > 31 {
        return None;
    }
    let mut days = 0u32;
    for yy in 1970..y {
        days = days.saturating_add(if leap(yy) { 366 } else { 365 });
    }
    const MD: [u32; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    for mm in 1..m {
        days = days.saturating_add(MD[(mm - 1) as usize]);
        if mm == 2 && leap(y) {
            days = days.saturating_add(1);
        }
    }
    Some(days.saturating_add(d))
}

fn leap(y: i32) -> bool {
    y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)
}

fn local_ymd() -> u32 {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Local offset is not critical for a coarseness of days; use UTC date.
    let days = (secs / 86400) as i64;
    // 1970-01-01 is ord 1 in ymd_ord? ymd_ord(1970,1,1) = 1
    // Unix day 0 is 1970-01-01, so ord = days + 1
    (days as u32).saturating_add(1)
}

/// Helper used by tests and by the concerned/error mapping.
pub fn last_turn_steps(studio: &Studio) -> (u32, u32) {
    let messages = studio.messages.get();
    let Some(last) = messages.iter().rev().find(|m| m.role == "assistant") else {
        return (0, 0);
    };
    let ok = last
        .steps
        .iter()
        .filter(|s| s.status == StepStatus::Done)
        .count() as u32;
    let err = last
        .steps
        .iter()
        .filter(|s| s.status == StepStatus::Error)
        .count() as u32;
    (ok, err)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn square_skin_parses_and_has_every_mood() {
        let skin = parse(include_str!("../theme/faces/square.toml")).expect("square");
        assert_eq!(skin.id, "square");
        assert_eq!(skin.grid, 24);
        for mood in [
            "idle",
            "listening",
            "thinking",
            "working",
            "focused",
            "curious",
            "happy",
            "proud",
            "sleepy",
            "concerned",
            "error",
            "surprised",
        ] {
            let r = skin.resolve(mood, 2);
            assert!(
                r.parts.iter().any(|p| p.kind == "eyes" || p.kind == "happy-eyes" || p.kind == "stepped-frame"),
                "{mood} should have drawable parts"
            );
        }
        let idle = skin.resolve("idle", 2);
        assert!(idle.parts.iter().any(|p| p.name == "studs"));
        let neu = skin.resolve("idle", 0);
        assert!(!neu.parts.iter().any(|p| p.name == "ring" || p.name == "studs"));
    }

    #[test]
    fn bundled_optional_skins_parse() {
        for id in ["morning-star", "wolf", "bear"] {
            let skin = parse(
                BUNDLED
                    .iter()
                    .find(|(k, _)| *k == id)
                    .map(|(_, s)| *s)
                    .unwrap(),
            )
            .expect(id);
            assert_eq!(skin.engine, "legacy");
            assert_eq!(skin.id, id);
        }
    }

    #[test]
    fn growth_thresholds() {
        assert_eq!(stage_from(Facts::default()), 0);
        assert_eq!(
            stage_from(Facts {
                messages: 8,
                ..Facts::default()
            }),
            1
        );
        assert_eq!(
            stage_from(Facts {
                sessions: 3,
                ..Facts::default()
            }),
            1
        );
        assert_eq!(
            stage_from(Facts {
                messages: 40,
                ..Facts::default()
            }),
            2
        );
        assert_eq!(
            stage_from(Facts {
                messages: 200,
                days: 60,
                ..Facts::default()
            }),
            3
        );
        assert_eq!(
            stage_from(Facts {
                messages: 200,
                days: 10,
                ..Facts::default()
            }),
            2
        );
    }
}
