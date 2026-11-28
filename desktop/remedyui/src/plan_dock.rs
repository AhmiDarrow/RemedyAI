//! Plan mode's plan-list pane: a dock on the start or end edge, resizable,
//! collapsible to a slim strip. Side, width, and collapsed persist under
//! `~/.remedy/ui/plan-dock.json`. Tests that set `REMEDY_DESKTOP_NO_SPAWN`
//! keep the defaults and never touch disk.

use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::chat_split::PANE_RADIUS;
use crate::runtime::{self, spawn_disabled};
use crate::studio::Studio;
use crate::tokens;
use crate::{messages, say};

pub const PLAN_WIDTH_DEFAULT: f32 = 300.0;
pub const PLAN_WIDTH_MIN: f32 = 200.0;
pub const PLAN_WIDTH_MAX: f32 = 560.0;
pub const PLAN_STRIP_WIDTH: f32 = 36.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PlanDockSide {
    Left,
    Right,
}

impl PlanDockSide {
    fn to_dock(self) -> w::DockSide {
        match self {
            PlanDockSide::Left => w::DockSide::Start,
            PlanDockSide::Right => w::DockSide::End,
        }
    }

    fn from_dock(side: w::DockSide) -> Self {
        match side {
            w::DockSide::Start => PlanDockSide::Left,
            w::DockSide::End => PlanDockSide::Right,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PlanDock {
    #[serde(default = "default_side")]
    pub side: PlanDockSide,
    #[serde(default = "default_width")]
    pub width: f32,
    #[serde(default)]
    pub collapsed: bool,
}

fn default_side() -> PlanDockSide {
    PlanDockSide::Left
}

fn default_width() -> f32 {
    PLAN_WIDTH_DEFAULT
}

impl Default for PlanDock {
    fn default() -> Self {
        Self {
            side: default_side(),
            width: default_width(),
            collapsed: false,
        }
    }
}

impl PlanDock {
    fn clamp(self) -> Self {
        Self {
            width: self.width.clamp(PLAN_WIDTH_MIN, PLAN_WIDTH_MAX),
            ..self
        }
    }

    fn to_state(self) -> w::DockState {
        let prefs = self.clamp();
        w::DockState {
            side: prefs.side.to_dock(),
            width: prefs.width,
            collapsed: prefs.collapsed,
        }
    }

    fn from_state(state: w::DockState) -> Self {
        Self {
            side: PlanDockSide::from_dock(state.side),
            width: state.width,
            collapsed: state.collapsed,
        }
        .clamp()
    }
}

fn prefs_path() -> PathBuf {
    runtime::remedy_home().join("ui").join("plan-dock.json")
}

pub fn load() -> PlanDock {
    if spawn_disabled() {
        return PlanDock::default();
    }
    let Ok(raw) = fs::read_to_string(prefs_path()) else {
        return PlanDock::default();
    };
    let prefs: PlanDock = serde_json::from_str(&raw).unwrap_or_default();
    prefs.clamp()
}

pub fn save(prefs: PlanDock) {
    if spawn_disabled() {
        return;
    }
    let path = prefs_path();
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if let Ok(raw) = serde_json::to_string_pretty(&prefs) {
        let _ = fs::write(path, raw);
    }
}

/// Dock state loaded from disk (or the defaults, in tests).
pub fn initial_state() -> w::DockState {
    load().to_state()
}

/// Collapse or expand the plan list, then persist.
pub fn toggle(studio: &Studio) {
    studio.plan_dock.update(|d| d.toggle());
    save(PlanDock::from_state(studio.plan_dock.get()));
}

/// Plan list beside Chat, using the reusable RemedyUI dock pane.
pub fn workspace(ui: &Ui, studio: Studio) -> El {
    let dock_state = studio.plan_dock;
    let persist = dock_state;
    let persist_el = ui.el("plan-dock-persist").bind(move || {
        save(PlanDock::from_state(persist.get()));
    });

    let label = messages().text(ui, "plan-dock");
    let spec = w::DockSpec {
        min_width: PLAN_WIDTH_MIN,
        max_width: PLAN_WIDTH_MAX,
        strip_width: PLAN_STRIP_WIDTH,
        label,
    };
    // The dock leaves Chat as little as 80 px. Below this width the two stack.
    let narrow = remedy_ui::reactive::Signal::new(false);
    let side = remedy_ui::show(ui, move || !narrow.get(), {
        let (ui, studio) = (ui.clone(), studio.clone());
        move || {
            let docked = plan_column(&ui, studio.clone());
            let main = chat_column(&ui, studio.clone());
            w::dock(&ui, dock_state, spec.clone(), docked, main)
        }
    })
    .el()
    .layout(fill);
    let stacked = remedy_ui::show(ui, move || narrow.get(), {
        let ui = ui.clone();
        move || {
            let unit = ui.theme().spacing.unit;
            w::column(&ui)
                .gap(unit)
                .layout(fill)
                .child(plan_column(&ui, studio.clone()).layout(|l| {
                    l.height = Length::Percent(0.45);
                    l.shrink = 1.0;
                }))
                .child(chat_column(&ui, studio.clone()).layout(|l| {
                    l.height = Length::Percent(0.55);
                }))
        }
    })
    .el()
    .layout(fill);
    w::column(ui)
        .layout(fill)
        .on_resize(move |size| narrow.set_if_changed(size.width < PLAN_WIDTH_MIN + STACK_CHAT_MIN))
        .child(persist_el.layout(|l| {
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
            l.shrink = 0.0;
        }))
        .child(side)
        .child(stacked)
}

/// Chat needs this much beside the Plan dock, or the two stack.
const STACK_CHAT_MIN: f32 = 260.0;

fn fill(l: &mut remedy_ui::layout::LayoutStyle) {
    l.width = Length::Percent(1.0);
    l.grow = 1.0;
    l.basis = Length::Px(0.0);
    l.min_width = Length::Px(0.0);
    l.min_height = Length::Px(0.0);
    l.height = Length::Percent(1.0);
}

fn plan_column(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let head = pane_kicker(ui, "plan-dock", "plan-dock-sub");
    let body = w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::column(ui)
                .gap(unit)
                .padding(Insets::all(unit * 1.5))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .child(crate::depth::banners::plan(ui, studio.clone(), true))
                .child(crate::depth::banners::plan_empty(ui, studio)),
        );
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .clip()
        .style(
            StylePatch::new()
                .background(tokens::pane_fill(&colors))
                .border(tokens::hairline(&colors))
                .border_width(1.0)
                .radius(PANE_RADIUS),
        )
        .semantics({
            let u = ui.clone();
            move || Semantics::new(Role::Region).label(messages().text(&u, "plan-dock"))
        })
        .child(head)
        .child(body)
}

fn chat_column(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let thread = w::column(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(crate::depth::banners::lead(ui, studio.clone()))
        .child(crate::chat::feed(ui, studio.clone()))
        .child(crate::depth::banners::status(ui, studio));
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
        })
        .clip()
        .style(
            StylePatch::new()
                .background(tokens::pane_fill(&colors))
                .border(tokens::hairline(&colors))
                .border_width(1.0)
                .radius(PANE_RADIUS),
        )
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Region).label(messages().text(&u, "chat-split-chat-kicker"))
            }
        })
        .child(pane_kicker(
            ui,
            "chat-split-chat-kicker",
            "chat-split-chat-sub",
        ))
        .child(thread)
}

fn pane_kicker(ui: &Ui, kicker: &'static str, sub: &'static str) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    w::row(ui)
        .align(Align::Center)
        .padding(Insets {
            top: 8.0,
            end: tokens::gutter(),
            bottom: 8.0,
            start: tokens::gutter(),
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(1.0, line.with_alpha(0.4)),
        ))
        .child(
            w::column(ui)
                .gap(crate::tokens::space() * 0.25)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                })
                .child(
                    say(ui, kicker, "strong")
                        .truncate()
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        })
                        .style(tokens::section_kicker(&colors)),
                )
                .child(
                    say(ui, sub, "muted")
                        .truncate()
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        })
                        .style(tokens::typo(tokens::META).foreground(colors.muted)),
                ),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefs_round_trip_keeps_side_width_and_collapsed() {
        let raw = r#"{"side":"right","width":240.0,"collapsed":true}"#;
        let prefs: PlanDock = serde_json::from_str(raw).expect("json");
        assert_eq!(prefs.side, PlanDockSide::Right);
        assert_eq!(prefs.width, 240.0);
        assert!(prefs.collapsed);
        let state = prefs.to_state();
        assert_eq!(state.side, w::DockSide::End);
        assert_eq!(state.width, 240.0);
        assert!(state.collapsed);
        let again = PlanDock::from_state(state);
        let out = serde_json::to_string(&again).expect("write");
        assert!(out.contains("right"));
        assert!(out.contains("240"));
        assert!(out.contains("true"));
    }

    #[test]
    fn prefs_clamp_width_to_min_and_max() {
        let wide = PlanDock {
            side: PlanDockSide::Left,
            width: 900.0,
            collapsed: false,
        }
        .clamp();
        assert_eq!(wide.width, PLAN_WIDTH_MAX);
        let thin = PlanDock {
            side: PlanDockSide::Left,
            width: 10.0,
            collapsed: false,
        }
        .clamp();
        assert_eq!(thin.width, PLAN_WIDTH_MIN);
    }
}
