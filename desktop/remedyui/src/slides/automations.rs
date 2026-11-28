//! Background tasks. Same roster, spawn, reassign, and two-step stop as
//! `AutomationsSlide.tsx`. Status colors use the theme (ok / warn / danger)
//! instead of the hard-coded greens and yellows in the React file.

use super::api;
use crate::runtime::Client;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Color;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Length};
use remedy_ui::net::Request;
use remedy_ui::reactive::Signal;
use remedy_ui::style::{StylePatch, RADIUS_FULL};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use serde_json::json;
use std::cell::Cell;
use std::rc::Rc;
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Cadence {
    Forager,
    Post,
}

#[derive(Clone, Debug, PartialEq)]
struct HiveRow {
    id: String,
    cadence: String,
    status: String,
    goal: String,
    outcome: String,
    blockers: Vec<String>,
    pulse_s: u64,
    pulse_count: u64,
}

#[derive(Clone)]
pub struct AutomationsModel {
    pub client: Signal<Option<Client>>,
    pub preview: bool,
    roster: Signal<Vec<HiveRow>>,
    posts: Signal<u64>,
    foragers: Signal<u64>,
    error: Signal<String>,
    loading: Signal<bool>,
    goal: Signal<String>,
    cadence: Signal<Cadence>,
    steps: Signal<f64>,
    pulse: Signal<f64>,
    spawning: Signal<bool>,
    spawn_msg: Signal<String>,
    spawn_ok: Signal<bool>,
    confirm: Signal<Option<String>>,
    assign: Signal<Option<String>>,
    assign_draft: Signal<String>,
    busy: Signal<Option<String>>,
    action_err: Signal<String>,
    show_stopped: Signal<bool>,
    generation: Rc<Cell<u64>>,
    alive: Rc<Cell<bool>>,
    next_local: Rc<Cell<u64>>,
}

impl AutomationsModel {
    pub fn new(client: Signal<Option<Client>>, preview: bool) -> Self {
        let (roster, posts, foragers) = if preview {
            let rows = preview_roster();
            (rows.clone(), live_posts(&rows), live_foragers(&rows))
        } else {
            (Vec::new(), 0, 0)
        };
        Self {
            client,
            preview,
            roster: Signal::new(roster),
            posts: Signal::new(posts),
            foragers: Signal::new(foragers),
            error: Signal::new(String::new()),
            loading: Signal::new(false),
            goal: Signal::new(String::new()),
            cadence: Signal::new(Cadence::Forager),
            steps: Signal::new(8.0),
            pulse: Signal::new(60.0),
            spawning: Signal::new(false),
            spawn_msg: Signal::new(String::new()),
            spawn_ok: Signal::new(true),
            confirm: Signal::new(None),
            assign: Signal::new(None),
            assign_draft: Signal::new(String::new()),
            busy: Signal::new(None),
            action_err: Signal::new(String::new()),
            show_stopped: Signal::new(false),
            generation: Rc::new(Cell::new(0)),
            alive: Rc::new(Cell::new(true)),
            next_local: Rc::new(Cell::new(1)),
        }
    }

    fn load(&self) {
        if self.preview {
            return;
        }
        let Some(client) = self.client.get() else {
            self.loading.set(false);
            self.error.set("not connected".into());
            return;
        };
        let generation = self.generation.get().wrapping_add(1);
        self.generation.set(generation);
        let model = self.clone();
        api::request(&client, Request::get("hive/roster"), move |res| {
            if !model.alive.get() || model.generation.get() != generation {
                return;
            }
            model.loading.set(false);
            match res {
                Ok(v) => {
                    let rows = parse_roster(&v);
                    let posts = v
                        .get("live_posts")
                        .and_then(|n| n.as_u64())
                        .unwrap_or_else(|| live_posts(&rows));
                    let foragers = v
                        .get("live_foragers")
                        .and_then(|n| n.as_u64())
                        .unwrap_or_else(|| live_foragers(&rows));
                    model.posts.set(posts);
                    model.foragers.set(foragers);
                    model.roster.set(rows);
                    model.error.set(String::new());
                }
                Err(e) => model.error.set(e),
            }
        });
    }

    fn spawn(&self) {
        let goal = self.goal.get().trim().to_string();
        if goal.is_empty() || self.spawning.get() {
            return;
        }
        let cadence = self.cadence.get();
        let steps = self.steps.get().clamp(1.0, 16.0).round() as u64;
        let pulse = if cadence == Cadence::Post {
            self.pulse.get().clamp(30.0, 3600.0).round() as u64
        } else {
            0
        };
        if self.preview {
            let n = self.next_local.get();
            self.next_local.set(n + 1);
            let row = HiveRow {
                id: format!("local{n:04}"),
                cadence: if cadence == Cadence::Post { "post" } else { "forager" }.into(),
                status: if cadence == Cadence::Post { "asleep" } else { "running" }.into(),
                goal: goal.clone(),
                outcome: String::new(),
                blockers: Vec::new(),
                pulse_s: pulse,
                pulse_count: 0,
            };
            let mut rows = self.roster.get();
            rows.insert(0, row);
            self.roster.set(rows.clone());
            self.posts.set(live_posts(&rows));
            self.foragers.set(live_foragers(&rows));
            self.goal.set(String::new());
            self.spawn_ok.set(true);
            self.spawn_msg.set(
                if cadence == Cadence::Post {
                    "Repeating task started."
                } else {
                    "Task started."
                }
                .into(),
            );
            return;
        }
        let Some(client) = self.client.get() else {
            self.spawn_ok.set(false);
            self.spawn_msg.set("not connected".into());
            return;
        };
        self.spawning.set(true);
        self.spawn_msg.set(String::new());
        let body = json!({
            "goal": goal,
            "cadence": if cadence == Cadence::Post { "post" } else { "forager" },
            "budget_steps": steps,
            "pulse_s": pulse,
        });
        let model = self.clone();
        api::request(&client, Request::post("hive/spawn").json(&body), move |res| {
            model.spawning.set(false);
            match res {
                Ok(v) if v.get("ok").and_then(|o| o.as_bool()).unwrap_or(false) => {
                    model.spawn_ok.set(true);
                    model.spawn_msg.set(
                        if cadence == Cadence::Post {
                            "Repeating task started."
                        } else {
                            "Task started."
                        }
                        .into(),
                    );
                    model.goal.set(String::new());
                    model.load();
                }
                Ok(v) => {
                    model.spawn_ok.set(false);
                    model.spawn_msg.set(
                        v.get("error")
                            .and_then(|e| e.as_str())
                            .unwrap_or("spawn failed")
                            .to_string(),
                    );
                }
                Err(e) => {
                    model.spawn_ok.set(false);
                    model.spawn_msg.set(e);
                }
            }
        });
    }

    fn retire(&self, id: &str) {
        if self.busy.get().is_some() {
            return;
        }
        if self.confirm.get().as_deref() != Some(id) {
            self.confirm.set(Some(id.to_string()));
            self.assign.set(None);
            return;
        }
        if self.preview {
            let mut rows = self.roster.get();
            if let Some(row) = rows.iter_mut().find(|r| r.id == id) {
                row.status = "retired".into();
            }
            self.roster.set(rows.clone());
            self.posts.set(live_posts(&rows));
            self.foragers.set(live_foragers(&rows));
            self.confirm.set(None);
            return;
        }
        let Some(client) = self.client.get() else {
            self.action_err.set("not connected".into());
            return;
        };
        self.busy.set(Some(id.to_string()));
        self.action_err.set(String::new());
        let body = json!({ "hive_id": id });
        let model = self.clone();
        api::request(&client, Request::post("hive/retire").json(&body), move |res| {
            model.busy.set(None);
            match res {
                Ok(v) if v.get("ok").and_then(|o| o.as_bool()).unwrap_or(false) => {
                    model.confirm.set(None);
                    model.load();
                }
                Ok(v) => model.action_err.set(
                    v.get("error")
                        .and_then(|e| e.as_str())
                        .unwrap_or("Could not stop this task.")
                        .to_string(),
                ),
                Err(e) => model.action_err.set(e),
            }
        });
    }

    fn assign_goal(&self, id: &str) {
        let goal = self.assign_draft.get().trim().to_string();
        if goal.is_empty() || self.busy.get().is_some() {
            return;
        }
        if self.preview {
            let mut rows = self.roster.get();
            if let Some(row) = rows.iter_mut().find(|r| r.id == id) {
                row.goal = goal;
            }
            self.roster.set(rows);
            self.assign.set(None);
            self.assign_draft.set(String::new());
            return;
        }
        let Some(client) = self.client.get() else {
            self.action_err.set("not connected".into());
            return;
        };
        self.busy.set(Some(id.to_string()));
        self.action_err.set(String::new());
        let body = json!({ "hive_id": id, "goal": goal });
        let model = self.clone();
        api::request(&client, Request::post("hive/assign").json(&body), move |res| {
            model.busy.set(None);
            match res {
                Ok(v) if v.get("ok").and_then(|o| o.as_bool()).unwrap_or(false) => {
                    model.assign.set(None);
                    model.assign_draft.set(String::new());
                    model.load();
                }
                Ok(v) => model.action_err.set(
                    v.get("error")
                        .and_then(|e| e.as_str())
                        .unwrap_or("Could not update this task.")
                        .to_string(),
                ),
                Err(e) => model.action_err.set(e),
            }
        });
    }
}

fn preview_roster() -> Vec<HiveRow> {
    vec![
        HiveRow {
            id: "forage01".into(),
            cadence: "forager".into(),
            status: "running".into(),
            goal: "Watch the build".into(),
            outcome: String::new(),
            blockers: Vec::new(),
            pulse_s: 0,
            pulse_count: 0,
        },
        HiveRow {
            id: "post0001".into(),
            cadence: "post".into(),
            status: "asleep".into(),
            goal: "Summarize the day".into(),
            outcome: "Waiting for the next pulse".into(),
            blockers: Vec::new(),
            pulse_s: 60,
            pulse_count: 2,
        },
    ]
}

fn parse_roster(v: &serde_json::Value) -> Vec<HiveRow> {
    v.get("daughters")
        .and_then(|d| d.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|row| {
                    Some(HiveRow {
                        id: row.get("id")?.as_str()?.to_string(),
                        cadence: row.get("cadence").and_then(|c| c.as_str()).unwrap_or("").into(),
                        status: row.get("status").and_then(|c| c.as_str()).unwrap_or("").into(),
                        goal: row.get("goal").and_then(|c| c.as_str()).unwrap_or("").into(),
                        outcome: row.get("outcome").and_then(|c| c.as_str()).unwrap_or("").into(),
                        blockers: row
                            .get("blockers")
                            .and_then(|b| b.as_array())
                            .map(|a| {
                                a.iter()
                                    .filter_map(|x| x.as_str().map(str::to_string))
                                    .collect()
                            })
                            .unwrap_or_default(),
                        pulse_s: row.get("pulse_s").and_then(|n| n.as_u64()).unwrap_or(0),
                        pulse_count: row.get("pulse_count").and_then(|n| n.as_u64()).unwrap_or(0),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn hive_is_live(row: &HiveRow) -> bool {
    row.status != "retired" && row.status != "cancelled"
}

fn live_posts(rows: &[HiveRow]) -> u64 {
    rows.iter()
        .filter(|r| r.cadence == "post" && hive_is_live(r))
        .count() as u64
}

fn live_foragers(rows: &[HiveRow]) -> u64 {
    rows.iter()
        .filter(|r| r.cadence == "forager" && (r.status == "pending" || r.status == "running"))
        .count() as u64
}

fn status_color(colors: &remedy_ui::style::ColorRoles, status: &str) -> Color {
    match status {
        "running" => colors.ok,
        "asleep" => colors.warn,
        "reported" => colors.accent,
        "blocked" | "cancelled" => colors.danger,
        _ => colors.muted,
    }
}

pub fn body(ui: &Ui, model: &AutomationsModel) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let root = w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(StylePatch::new().background(colors.panel).foreground(colors.text));

    if !model.preview {
        model.alive.set(true);
        model.loading.set(true);
        model.load();
        let alive = model.alive.clone();
        let poll = model.clone();
        schedule_poll(&root, alive, poll);
        let alive = model.alive.clone();
        let generation = model.generation.clone();
        let _ = root.clone().on_removed(move || {
            alive.set(false);
            generation.set(generation.get().wrapping_add(1));
        });
    }

    let ids: Signal<Vec<String>> = Signal::new(displayed_ids(model));
    let last = Rc::new(std::cell::RefCell::new(ids.get()));
    let m = model.clone();
    let ids_w = ids.clone();
    let _ = root.clone().bind(move || {
        let next = displayed_ids(&m);
        if *last.borrow() != next {
            *last.borrow_mut() = next.clone();
            ids_w.set(next);
        }
    });

    let list = ui.el("tasks").column().scroll().layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    let ui_rows = ui.clone();
    let m = model.clone();
    let _ = w::for_each(&list, ids, |id: &String| id.clone(), move |id| {
        task_row(&ui_rows, &m, id)
    });

    root.child(task_header(ui, model, unit, &colors))
        .child(w::divider(ui, false))
        .child(spawn_form(ui, model, unit, &colors))
        .child(w::divider(ui, false))
        .child(roster_bar(ui, model, unit, &colors))
        .child(w::divider(ui, false))
        .child(alerts(ui, model))
        .child(empty(ui, model))
        .child(list)
}

fn displayed_ids(model: &AutomationsModel) -> Vec<String> {
    model
        .roster
        .get()
        .into_iter()
        .filter(|r| model.show_stopped.get() || hive_is_live(r))
        .map(|r| r.id)
        .collect()
}

fn schedule_poll(host: &El, alive: Rc<Cell<bool>>, model: AutomationsModel) {
    let host2 = host.clone();
    let alive2 = alive.clone();
    let _ = host.ui().after(Duration::from_secs(6), move || {
        if !alive2.get() {
            return;
        }
        model.load();
        schedule_poll(&host2, alive2, model);
    });
}

fn task_header(ui: &Ui, model: &AutomationsModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let one = badge(ui, colors.ok, {
        let m = model.clone();
        move || format!("{} one-time", m.foragers.get())
    });
    let rep = badge(ui, colors.accent, {
        let m = model.clone();
        move || format!("{} repeating", m.posts.get())
    });
    let refresh = show(
        ui,
        {
            let m = model.clone();
            move || !m.loading.get()
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            move || {
                let b = w::button(&ui, "↺", {
                    let m = m.clone();
                    move || m.load()
                })
                .variant("quiet");
                let _ = b.clone().semantics(
                    Semantics::new(Role::Button)
                        .label("Refresh tasks")
                        .action(Action::Click),
                );
                w::tooltip(&b, "Refresh tasks");
                b
            }
        },
    )
    .fallback({
        let ui = ui.clone();
        let muted = colors.muted;
        move || w::text(&ui, "syncing…").variant("muted").style(StylePatch::new().foreground(muted))
    })
    .el();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(pad(unit, unit * 2.0))
        .style(StylePatch::new().background(colors.card))
        .child(w::text(ui, "BACKGROUND TASKS").variant("muted"))
        .child(one)
        .child(rep)
        .child(w::spacer(ui).layout(|l| l.grow = 1.0))
        .child(refresh)
}

fn badge(ui: &Ui, color: Color, label: impl Fn() -> String + 'static) -> El {
    w::text(ui, "")
        .text(label)
        .padding(pad(2.0, 6.0))
        .style(
            StylePatch::new()
                .background(color.with_alpha(0.18))
                .foreground(color)
                .radius(ui.theme().radii.small),
        )
}

fn spawn_form(ui: &Ui, model: &AutomationsModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let field = w::text_area(ui, "Task instructions")
        .placeholder("What should Remedy work on in the background?");
    let f = field.clone();
    let m = model.clone();
    let writing = Rc::new(Cell::new(false));
    let field = field.on_change({
        let writing = writing.clone();
        move || {
            if writing.get() {
                return;
            }
            m.goal.set(f.value());
        }
    });
    let f = field.clone();
    let m = model.clone();
    let writing2 = writing;
    let el = field.el();
    let _ = el.clone().bind(move || {
        let v = m.goal.get();
        if f.value() != v {
            writing2.set(true);
            f.set_value(&v);
            writing2.set(false);
        }
    });
    let m = model.clone();
    let _ = el.clone().on_key_capture(move |e| {
        if e.pressed
            && !e.composing
            && e.key == Key::Enter
            && (e.modifiers.ctrl || e.modifiers.meta)
        {
            m.spawn();
            return true;
        }
        false
    });

    let once = cadence_button(ui, model, Cadence::Forager, "Run once");
    let repeat = cadence_button(ui, model, Cadence::Post, "Repeat");
    let steps = w::stepper(ui, "Steps", model.steps, 1.0, 16.0, 1.0);
    let pulse = show(
        ui,
        {
            let m = model.clone();
            move || m.cadence.get() == Cadence::Post
        },
        {
            let ui = ui.clone();
            let pulse = model.pulse;
            move || w::stepper(&ui, "Every (seconds)", pulse, 30.0, 3600.0, 30.0)
        },
    )
    .el();
    let start = w::button(
        ui,
        {
            let m = model.clone();
            move || {
                if m.spawning.get() {
                    "Starting…".to_string()
                } else {
                    "Start task".to_string()
                }
            }
        },
        {
            let m = model.clone();
            move || m.spawn()
        },
    )
    .variant("primary")
    .disabled({
        let m = model.clone();
        move || m.spawning.get() || m.goal.get().trim().is_empty()
    });

    let msg = show(
        ui,
        {
            let m = model.clone();
            move || !m.spawn_msg.get().is_empty()
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let ok = colors.ok;
            let danger = colors.danger;
            move || {
                w::text(&ui, "").text({
                    let m = m.clone();
                    move || {
                        let mark = if m.spawn_ok.get() { "✓ " } else { "✗ " };
                        format!("{mark}{}", m.spawn_msg.get())
                    }
                }).style({
                    let m = m.clone();
                    move || StylePatch::new().foreground(if m.spawn_ok.get() { ok } else { danger })
                })
            }
        },
    )
    .el();

    w::column(ui)
        .gap(unit)
        .padding(pad(unit * 2.0, unit * 2.0))
        .child(el.layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(52.0);
        }))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                // Five controls do not fit a 360 px slide on one line.
                .layout(|l| {
                    l.wrap = true;
                    l.min_width = Length::Px(0.0);
                })
                .child(once)
                .child(repeat)
                .child(steps)
                .child(pulse)
                .child(w::spacer(ui).layout(|l| l.grow = 1.0))
                .child(start),
        )
        .child(msg)
}

fn cadence_button(ui: &Ui, model: &AutomationsModel, which: Cadence, label: &'static str) -> El {
    w::button(ui, label, {
        let m = model.clone();
        move || m.cadence.set(which)
    })
    .variant({
        let m = model.clone();
        move || {
            if m.cadence.get() == which {
                "primary"
            } else {
                "quiet"
            }
        }
    })
}

fn roster_bar(ui: &Ui, model: &AutomationsModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let m = model.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(pad(unit, unit * 2.0))
        .child(w::text(ui, "").variant("muted").text(move || {
            let n = displayed_ids(&m).len();
            format!("{n} task{}", if n == 1 { "" } else { "s" })
        }))
        .child(w::spacer(ui).layout(|l| l.grow = 1.0))
        .child(w::check(ui, "Include stopped", model.show_stopped))
        .style(StylePatch::new().foreground(colors.muted))
}

fn alerts(ui: &Ui, model: &AutomationsModel) -> El {
    let err = show(
        ui,
        {
            let m = model.clone();
            move || !m.error.get().is_empty()
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let danger = ui.theme().colors.danger;
            move || {
                w::text(&ui, "")
                    .text({
                        let m = m.clone();
                        move || m.error.get()
                    })
                    .style(StylePatch::new().foreground(danger))
                    .semantics(Semantics::new(Role::Status).label("Roster error"))
                    .padding(pad(4.0, 12.0))
            }
        },
    )
    .el();
    let action = show(
        ui,
        {
            let m = model.clone();
            move || !m.action_err.get().is_empty()
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let danger = ui.theme().colors.danger;
            move || {
                w::text(&ui, "")
                    .text({
                        let m = m.clone();
                        move || m.action_err.get()
                    })
                    .style(StylePatch::new().foreground(danger))
                    .padding(pad(2.0, 12.0))
            }
        },
    )
    .el();
    w::column(ui).child(err).child(action)
}

fn empty(ui: &Ui, model: &AutomationsModel) -> El {
    let m = model.clone();
    show(
        ui,
        move || {
            displayed_ids(&m).is_empty() && m.error.get().is_empty() && !m.loading.get()
        },
        {
            let ui = ui.clone();
            move || {
                w::empty_state(
                    &ui,
                    "No background tasks yet",
                    "Give Remedy a task above. Run it once, or repeat it at an interval.",
                )
                .padding(pad(12.0, 12.0))
            }
        },
    )
    .el()
}

fn task_row(ui: &Ui, model: &AutomationsModel, id: &str) -> El {
    let id = id.to_string();
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let dot = ui.el("status-dot").style({
        let m = model.clone();
        let id = id.clone();
        let colors = colors.clone();
        move || {
            let status = row_of(&m, &id).map(|r| r.status).unwrap_or_default();
            let color = status_color(&colors, &status);
            StylePatch::new().background(color).radius(RADIUS_FULL)
        }
    }).layout(|l| {
        l.width = Length::Px(7.0);
        l.height = Length::Px(7.0);
        l.min_width = Length::Px(7.0);
        l.shrink = 0.0;
    });
    let goal = w::text(ui, "").text({
        let m = model.clone();
        let id = id.clone();
        move || row_of(&m, &id).map(|r| if r.goal.is_empty() { "untitled".into() } else { r.goal }).unwrap_or_default()
    });
    let meta = w::text(ui, "").variant("muted").text({
        let m = model.clone();
        let id = id.clone();
        move || {
            let Some(row) = row_of(&m, &id) else {
                return String::new();
            };
            let kind = if row.cadence == "post" { "Repeating" } else { "One-time" };
            let short: String = row.id.chars().take(8).collect();
            let mut s = format!("{kind} · {} · {short}", row.status);
            if row.pulse_count > 0 {
                s.push_str(&format!(
                    " · {} pulse{}",
                    row.pulse_count,
                    if row.pulse_count == 1 { "" } else { "s" }
                ));
            }
            if row.pulse_s > 0 {
                s.push_str(&format!(" · every {}s", row.pulse_s));
            }
            s
        }
    });
    let outcome = show(
        ui,
        {
            let m = model.clone();
            let id = id.clone();
            move || row_of(&m, &id).is_some_and(|r| !r.outcome.is_empty())
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let id = id.clone();
            move || {
                w::text(&ui, "").variant("muted").text({
                    let m = m.clone();
                    let id = id.clone();
                    move || row_of(&m, &id).map(|r| r.outcome).unwrap_or_default()
                })
            }
        },
    )
    .el();
    let blockers = show(
        ui,
        {
            let m = model.clone();
            let id = id.clone();
            move || row_of(&m, &id).is_some_and(|r| !r.blockers.is_empty())
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let id = id.clone();
            let danger = colors.danger;
            move || {
                w::text(&ui, "").text({
                    let m = m.clone();
                    let id = id.clone();
                    move || {
                        row_of(&m, &id)
                            .map(|r| format!("⚠ {}", r.blockers.join(" · ")))
                            .unwrap_or_default()
                    }
                }).style(StylePatch::new().foreground(danger))
            }
        },
    )
    .el();

    let actions = show(
        ui,
        {
            let m = model.clone();
            let id = id.clone();
            move || row_of(&m, &id).is_some_and(|r| hive_is_live(&r))
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let id = id.clone();
            move || {
                let reassign = show(
                    &ui,
                    {
                        let m = m.clone();
                        let id = id.clone();
                        move || row_of(&m, &id).is_some_and(|r| r.cadence == "post")
                    },
                    {
                        let ui = ui.clone();
                        let m = m.clone();
                        let id = id.clone();
                        move || {
                            let press_m = m.clone();
                            let press_id = id.clone();
                            w::button(&ui, "Reassign", move || {
                                let current = row_of(&press_m, &press_id)
                                    .map(|r| r.goal)
                                    .unwrap_or_default();
                                if press_m.assign.get().as_deref() == Some(press_id.as_str()) {
                                    press_m.assign.set(None);
                                } else {
                                    press_m.assign.set(Some(press_id.clone()));
                                    press_m.assign_draft.set(current);
                                    press_m.confirm.set(None);
                                }
                            })
                            .variant("quiet")
                            .disabled({
                                let m = m.clone();
                                move || m.busy.get().is_some()
                            })
                        }
                    },
                )
                .el();
                let stop = w::button(
                    &ui,
                    {
                        let m = m.clone();
                        let id = id.clone();
                        move || {
                            if m.busy.get().is_some() {
                                "…".to_string()
                            } else if m.confirm.get().as_deref() == Some(id.as_str()) {
                                "Confirm stop".to_string()
                            } else {
                                "Stop".to_string()
                            }
                        }
                    },
                    {
                        let m = m.clone();
                        let id = id.clone();
                        move || {
                            m.assign.set(None);
                            m.retire(&id);
                        }
                    },
                )
                .variant({
                    let m = m.clone();
                    let id = id.clone();
                    move || {
                        if m.confirm.get().as_deref() == Some(id.as_str()) {
                            "danger_solid"
                        } else {
                            "quiet"
                        }
                    }
                })
                .disabled({
                    let m = m.clone();
                    move || m.busy.get().is_some()
                });
                w::column(&ui).gap(crate::tokens::space() * 1.0).child(reassign).child(stop)
            }
        },
    )
    .el();

    let editor = show(
        ui,
        {
            let m = model.clone();
            let id = id.clone();
            move || m.assign.get().as_deref() == Some(id.as_str())
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let id = id.clone();
            move || assign_editor(&ui, &m, &id)
        },
    )
    .el();

    w::column(ui)
        .gap(unit)
        .padding(pad(unit * 2.0, unit * 2.0))
        .style({
            let m = model.clone();
            let id = id.clone();
            let line = colors.line.unwrap_or(colors.muted.with_alpha(0.35));
            move || {
                let live = row_of(&m, &id).is_some_and(|r| hive_is_live(&r));
                StylePatch::new().border_side(
                    remedy_ui::style::BorderEdge::Bottom,
                    remedy_ui::style::BorderSide::new(1.0, line),
                ).foreground(if live { colors.text } else { colors.muted })
            }
        })
        .child(
            w::row(ui)
                .align(Align::Start)
                .gap(unit)
                .child(dot)
                .child(
                    w::column(ui)
                        .gap(crate::tokens::space() * 0.5)
                        .layout(|l| {
                            l.grow = 1.0;
                            l.min_width = Length::Px(0.0);
                        })
                        .child(goal)
                        .child(meta)
                        .child(outcome)
                        .child(blockers),
                )
                .child(actions),
        )
        .child(editor)
}

fn assign_editor(ui: &Ui, model: &AutomationsModel, id: &str) -> El {
    let field = w::text_input(ui, "Updated task instructions").placeholder("New goal…");
    let f = field.clone();
    let m = model.clone();
    let writing = Rc::new(Cell::new(false));
    let field = field.on_change({
        let writing = writing.clone();
        move || {
            if writing.get() {
                return;
            }
            m.assign_draft.set(f.value());
        }
    });
    let f = field.clone();
    let m = model.clone();
    let writing2 = writing;
    let el = field.el();
    let _ = el.clone().bind(move || {
        let v = m.assign_draft.get();
        if f.value() != v {
            writing2.set(true);
            f.set_value(&v);
            writing2.set(false);
        }
    });
    let id = id.to_string();
    let m = model.clone();
    let id_key = id.clone();
    let _ = el.clone().on_key_capture(move |e| {
        if !e.pressed || e.composing {
            return false;
        }
        if e.key == Key::Enter {
            m.assign_goal(&id_key);
            return true;
        }
        if e.key == Key::Escape {
            m.assign.set(None);
            return true;
        }
        false
    });
    let save = w::button(ui, "Save", {
        let m = model.clone();
        let id = id.clone();
        move || m.assign_goal(&id)
    })
    .variant("primary")
    .disabled({
        let m = model.clone();
        move || m.busy.get().is_some() || m.assign_draft.get().trim().is_empty()
    });
    let cancel = w::button(ui, "×", {
        let m = model.clone();
        move || m.assign.set(None)
    })
    .variant("quiet");
    let _ = cancel.clone().semantics(
        Semantics::new(Role::Button)
            .label("Cancel task edit")
            .action(Action::Click),
    );
    w::row(ui)
        .align(Align::Center)
        .gap(crate::tokens::space() * 1.5)
        .child(el.layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(save)
        .child(cancel)
}

fn row_of(model: &AutomationsModel, id: &str) -> Option<HiveRow> {
    model.roster.get().into_iter().find(|r| r.id == id)
}

fn pad(y: f32, x: f32) -> remedy_ui::core::Insets {
    remedy_ui::core::Insets {
        top: y,
        bottom: y,
        start: x,
        end: x,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_counts_skip_stopped_tasks() {
        let mut rows = preview_roster();
        assert_eq!(live_foragers(&rows), 1);
        assert_eq!(live_posts(&rows), 1);
        rows[0].status = "retired".into();
        assert_eq!(live_foragers(&rows), 0);
        assert!(!hive_is_live(&rows[0]));
        assert!(hive_is_live(&rows[1]));
    }
}
