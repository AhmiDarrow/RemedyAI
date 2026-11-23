//! Health diagnostics and the hive inspector that lives inside it.
//!
//! Wire-to-existing: `GET /diagnostics`, `GET /self-inject/rounds`,
//! `GET /hive/roster`, `POST /hive/spawn`, `POST /hive/assign`, `POST /hive/retire`
//! on the loopback runtime (`runtime::origin()` + `/api`). This module does not
//! reimplement collection or the hive. Mount [`DiagnosticsPanel::view`] once;
//! [`DiagnosticsPanel::open`] shows it. Command palette and shortcuts stay with chrome.
//!
//! `browserStackHold` is a Tauri webview stack and has no RemedyUI equivalent.
//! The panel is a window overlay instead.

mod api;
mod hive;
mod model;
mod view;

use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

use remedy_ui::reactive::{Signal, waker};
use remedy_ui::{El, Ui};
use serde_json::{Value, json};

use model::{HiveOp, HiveRoster};

/// Diagnostics window: server, RMB host, hardware, and the hive inspector.
///
/// Create one, mount [`view`](Self::view) once, then call [`open`](Self::open)
/// from a menu or the command palette. Auto-refresh, the last snapshot, and the
/// probe checkbox survive close. The hive form does not: it unmounts with the overlay.
#[derive(Clone)]
pub struct DiagnosticsPanel {
    local_only: bool,
    open: Signal<bool>,
    auto: Signal<bool>,
    probe: Signal<bool>,
    loading: Signal<bool>,
    error: Signal<Option<String>>,
    snapshot: Signal<Option<model::DiagnosticsView>>,
    rounds: Signal<Vec<model::RoundLine>>,
    rows: Signal<Vec<model::HiveRow>>,
    live_posts: Signal<i64>,
    live_foragers: Signal<i64>,
    hive_ready: Signal<bool>,
    hive_error: Signal<Option<String>>,
    hive_busy: Signal<bool>,
    pending_retire: Signal<Option<String>>,
    assign_target: Signal<Option<String>>,
    assign_draft: Signal<String>,
    spawning: Signal<bool>,
    spawn_note: Signal<Option<SpawnNote>>,
    cadence: Signal<usize>,
    budget: Signal<f64>,
    pulse: Signal<f64>,
    goal: Signal<String>,
    clear_goal: Signal<u64>,
    generation: Signal<u64>,
    in_flight: Signal<Option<u64>>,
    hive_generation: Signal<u64>,
    hive_in_flight: Signal<Option<u64>>,
    /// Bumped only when the overlay closes. Spawn, assign, and retire capture it
    /// so a roster poll (which bumps `hive_generation`) does not drop their result.
    session: Signal<u64>,
    timer: Signal<Option<u64>>,
}

/// Spawn success or the sentence the runtime returned.
#[derive(Clone, Debug)]
pub(crate) struct SpawnNote {
    pub ok: bool,
    pub text: String,
}

impl DiagnosticsPanel {
    /// A panel that talks to the runtime. Nothing is drawn until [`view`](Self::view)
    /// is mounted and [`open`](Self::open) runs.
    pub fn new() -> Self {
        Self::build(false)
    }

    /// Opens the overlay. Mount [`view`](Self::view) first (or at the same time).
    pub fn open(&self) {
        self.open.set(true);
    }

    /// Closes the overlay. The mounted view removes it.
    pub fn close(&self) {
        if self.open.is_alive() {
            self.open.set(false);
        }
    }

    /// Whether the overlay should be up.
    pub fn is_open(&self) -> bool {
        self.open.try_get().unwrap_or(false)
    }

    /// False after the runtime that created the panel has been disposed.
    /// Thread-local install slots outlive a single test.
    pub(crate) fn is_alive(&self) -> bool {
        self.open.is_alive()
    }

    /// Host element. Mount it once under the window. It draws nothing until [`open`](Self::open).
    pub fn view(ui: &Ui, panel: &DiagnosticsPanel) -> El {
        crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
        let layer: Rc<RefCell<Option<El>>> = Rc::default();
        let host = ui.el("diagnostics-host");
        let panel_b = panel.clone();
        let layer_b = layer.clone();
        let ui_b = ui.clone();
        let host = host.bind(move || {
            let is_open = panel_b.open.try_get().unwrap_or(false);
            let panel = panel_b.clone();
            let ui = ui_b.clone();
            let layer = layer_b.clone();
            remedy_ui::reactive::untrack(|| sync_layer(&panel, &ui, &layer, is_open));
        });
        let panel_r = panel.clone();
        let ui_r = ui.clone();
        host.on_removed(move || {
            panel_r.disarm(&ui_r);
            if let Some(el) = layer.borrow_mut().take() {
                el.remove();
            }
            panel_r.open.set(false);
        })
    }

    /// Sample snapshot for a golden. No network and no poll. The layer fills its parent.
    pub(crate) fn sample() -> Self {
        let panel = Self::build(true);
        panel
            .snapshot
            .set(Some(model::view_from(&model::sample_snapshot())));
        panel
            .rounds
            .set(model::rounds_from(&model::sample_rounds()));
        let roster = model::sample_roster();
        panel.apply_roster(roster);
        panel.hive_ready.set(true);
        panel.open.set(true);
        panel
    }

    fn build(local_only: bool) -> Self {
        Self {
            local_only,
            open: Signal::new(false),
            auto: Signal::new(true),
            probe: Signal::new(false),
            loading: Signal::new(false),
            error: Signal::new(None),
            snapshot: Signal::new(None),
            rounds: Signal::new(Vec::new()),
            rows: Signal::new(Vec::new()),
            live_posts: Signal::new(0),
            live_foragers: Signal::new(0),
            hive_ready: Signal::new(false),
            hive_error: Signal::new(None),
            hive_busy: Signal::new(false),
            pending_retire: Signal::new(None),
            assign_target: Signal::new(None),
            assign_draft: Signal::new(String::new()),
            spawning: Signal::new(false),
            spawn_note: Signal::new(None),
            cadence: Signal::new(0usize),
            budget: Signal::new(8.0),
            pulse: Signal::new(60.0),
            goal: Signal::new(String::new()),
            clear_goal: Signal::new(0u64),
            generation: Signal::new(0u64),
            in_flight: Signal::new(None),
            hive_generation: Signal::new(0u64),
            hive_in_flight: Signal::new(None),
            session: Signal::new(0u64),
            timer: Signal::new(None),
        }
    }

    fn disarm(&self, ui: &Ui) {
        if let Some(id) = self.timer.get() {
            ui.cancel_timer(id);
        }
        self.timer.set(None);
        self.generation.update(|g| *g = g.wrapping_add(1));
        self.in_flight.set(None);
        self.hive_generation.update(|g| *g = g.wrapping_add(1));
        self.hive_in_flight.set(None);
        self.session.update(|g| *g = g.wrapping_add(1));
        self.loading.set(false);
        self.reset_hive();
    }

    fn reset_hive(&self) {
        self.rows.set(Vec::new());
        self.live_posts.set(0);
        self.live_foragers.set(0);
        self.hive_ready.set(false);
        self.hive_error.set(None);
        self.hive_busy.set(false);
        self.pending_retire.set(None);
        self.assign_target.set(None);
        self.assign_draft.set(String::new());
        self.spawning.set(false);
        self.spawn_note.set(None);
        self.goal.set(String::new());
        self.cadence.set(0);
        self.budget.set(8.0);
        self.pulse.set(60.0);
    }

    fn arm_timer(&self, ui: &Ui) {
        if self.local_only || self.timer.get().is_some() {
            return;
        }
        let panel = self.clone();
        let tick = ui.clone();
        let id = ui.after(Duration::from_secs(8), move || {
            panel.timer.set(None);
            if !panel.open.get() {
                return;
            }
            if panel.auto.get() {
                panel.load_diagnostics();
            }
            panel.load_hive(false);
            panel.arm_timer(&tick);
        });
        self.timer.set(Some(id));
    }

    pub(crate) fn load_diagnostics(&self) {
        if self.local_only || !self.open.get() {
            return;
        }
        let seen = self.generation.get();
        if self.in_flight.get() == Some(seen) {
            return;
        }
        let current = seen.wrapping_add(1);
        self.generation.set(current);
        self.in_flight.set(Some(current));
        self.loading.set(true);
        self.error.set(None);
        let probe = self.probe.get();
        let panel = self.clone();
        let Ok(waker) = waker() else {
            self.fail_diag(current, "Could not reach the runtime.".into());
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-diag".into())
            .spawn(move || {
                let http = api::client();
                let cancel = remedy_ui::reactive::Cancel::new();
                let snap = api::diagnostics(&http, probe, &cancel);
                let ledger = api::rounds(&http, 12, &cancel);
                let _ = waker.post(move || panel.apply_diag(current, snap, ledger));
            });
        if spawned.is_err() {
            self.fail_diag(current, "Could not reach the runtime.".into());
        }
    }

    fn apply_diag(&self, current: u64, snap: Result<Value, String>, ledger: Result<Value, String>) {
        if self.generation.get() != current {
            return;
        }
        match snap {
            Ok(v) => self.snapshot.set(Some(model::view_from(&v))),
            Err(e) => self.error.set(Some(e)),
        }
        // A ledger failure clears the rounds list and leaves the snapshot alone.
        self.rounds.set(match ledger {
            Ok(v) => model::rounds_from(&v),
            Err(_) => Vec::new(),
        });
        self.loading.set(false);
        if self.in_flight.get() == Some(current) {
            self.in_flight.set(None);
        }
    }

    fn fail_diag(&self, current: u64, message: String) {
        if self.generation.get() != current {
            return;
        }
        self.loading.set(false);
        self.in_flight.set(None);
        self.error.set(Some(message));
    }

    pub(crate) fn load_hive(&self, force: bool) {
        if self.local_only || !self.open.get() {
            return;
        }
        let seen = self.hive_generation.get();
        if !force && self.hive_in_flight.get() == Some(seen) {
            return;
        }
        let current = seen.wrapping_add(1);
        self.hive_generation.set(current);
        self.hive_in_flight.set(Some(current));
        let panel = self.clone();
        let Ok(waker) = waker() else {
            self.hive_in_flight.set(None);
            self.hive_error
                .set(Some("Could not reach the runtime.".into()));
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-hive".into())
            .spawn(move || {
                let http = api::client();
                let cancel = remedy_ui::reactive::Cancel::new();
                let roster = api::roster(&http, &cancel).and_then(|v| model::roster_from(&v));
                let _ = waker.post(move || panel.apply_hive(current, roster));
            });
        if spawned.is_err() {
            self.hive_in_flight.set(None);
            self.hive_error
                .set(Some("Could not reach the runtime.".into()));
        }
    }

    fn apply_hive(&self, current: u64, roster: Result<HiveRoster, String>) {
        if self.hive_generation.get() != current {
            return;
        }
        match roster {
            Ok(roster) => self.apply_roster(roster),
            Err(e) => self.hive_error.set(Some(e)),
        }
        if self.hive_in_flight.get() == Some(current) {
            self.hive_in_flight.set(None);
        }
    }

    fn apply_roster(&self, roster: HiveRoster) {
        self.rows.set(roster.rows);
        self.live_posts.set(roster.live_posts);
        self.live_foragers.set(roster.live_foragers);
        self.hive_ready.set(true);
        self.hive_error.set(None);
    }

    pub(crate) fn spawn_task(&self) {
        if self.local_only || self.spawning.get() {
            return;
        }
        let goal = self.goal.get();
        let goal = goal.trim().to_string();
        if goal.is_empty() {
            return;
        }
        let repeating = self.cadence.get() == 1;
        let cadence = if repeating { "post" } else { "forager" };
        let budget = self.budget.get().round().clamp(1.0, 16.0) as i64;
        let pulse = if repeating {
            self.pulse.get().round().clamp(30.0, 3600.0) as i64
        } else {
            0
        };
        self.spawning.set(true);
        self.spawn_note.set(None);
        let epoch = self.session.get();
        let body = json!({
            "goal": goal,
            "cadence": cadence,
            "budget_steps": budget,
            "pulse_s": pulse,
        });
        self.post_hive(
            "remedy-hive-spawn",
            epoch,
            body,
            api::spawn,
            move |panel, op| match op {
                Ok(op) if op.ok => {
                    let text = if repeating {
                        "Repeating task started."
                    } else {
                        "Task started."
                    };
                    panel.spawn_note.set(Some(SpawnNote {
                        ok: true,
                        text: text.into(),
                    }));
                    panel.clear_goal.update(|n| *n = n.wrapping_add(1));
                    panel.spawning.set(false);
                    panel.hive_in_flight.set(None);
                    panel.load_hive(true);
                }
                Ok(op) => {
                    panel.spawn_note.set(Some(SpawnNote {
                        ok: false,
                        text: op.error.unwrap_or_else(|| "spawn failed".into()),
                    }));
                    panel.spawning.set(false);
                }
                Err(e) => {
                    panel.spawn_note.set(Some(SpawnNote { ok: false, text: e }));
                    panel.spawning.set(false);
                }
            },
        );
    }

    pub(crate) fn assign_task(&self, id: &str) {
        if self.local_only || self.hive_busy.get() {
            return;
        }
        let goal = self.assign_draft.get().trim().to_string();
        if goal.is_empty() {
            return;
        }
        let id = id.to_string();
        self.hive_busy.set(true);
        self.hive_error.set(None);
        let epoch = self.session.get();
        let body = json!({ "hive_id": id, "goal": goal });
        self.post_hive(
            "remedy-hive-assign",
            epoch,
            body,
            api::assign,
            move |panel, op| match op {
                Ok(op) if op.ok => {
                    panel.assign_target.set(None);
                    panel.assign_draft.set(String::new());
                    panel.hive_busy.set(false);
                    panel.hive_in_flight.set(None);
                    panel.load_hive(true);
                }
                Ok(op) => {
                    panel.hive_error.set(Some(
                        op.error
                            .unwrap_or_else(|| "Could not update this task.".into()),
                    ));
                    panel.hive_busy.set(false);
                }
                Err(e) => {
                    panel.hive_error.set(Some(e));
                    panel.hive_busy.set(false);
                }
            },
        );
    }

    pub(crate) fn retire_task(&self, id: &str) {
        if self.local_only || self.hive_busy.get() {
            return;
        }
        self.assign_target.set(None);
        if self.pending_retire.get().as_deref() != Some(id) {
            self.pending_retire.set(Some(id.to_string()));
            return;
        }
        let id = id.to_string();
        self.hive_busy.set(true);
        self.hive_error.set(None);
        let epoch = self.session.get();
        let body = json!({ "hive_id": id });
        self.post_hive(
            "remedy-hive-retire",
            epoch,
            body,
            api::retire,
            move |panel, op| match op {
                Ok(op) if op.ok => {
                    panel.pending_retire.set(None);
                    panel.hive_busy.set(false);
                    panel.hive_in_flight.set(None);
                    panel.load_hive(true);
                }
                Ok(op) => {
                    panel
                        .hive_error
                        .set(Some(op.error.unwrap_or_else(|| "retire failed".into())));
                    panel.hive_busy.set(false);
                }
                Err(e) => {
                    panel.hive_error.set(Some(e));
                    panel.hive_busy.set(false);
                }
            },
        );
    }

    fn post_hive<F>(
        &self,
        thread_name: &str,
        epoch: u64,
        body: Value,
        call: fn(
            &remedy_ui::net::Http,
            &Value,
            &remedy_ui::reactive::Cancel,
        ) -> Result<HiveOp, String>,
        apply: F,
    ) where
        F: FnOnce(&DiagnosticsPanel, Result<HiveOp, String>) + Send + 'static,
    {
        // The callback has to stay callable if the thread never starts. A failed
        // `spawn` drops its closure, so the callback lives in a slot both sides share.
        let slot = std::sync::Arc::new(std::sync::Mutex::new(Some(apply)));
        let panel = self.clone();
        let fail = "Could not reach the runtime.".to_string();
        let Ok(waker) = waker() else {
            if let Some(apply) = take_apply(&slot) {
                apply(self, Err(fail));
            }
            return;
        };
        let slot_thread = std::sync::Arc::clone(&slot);
        let spawned = std::thread::Builder::new()
            .name(thread_name.into())
            .spawn(move || {
                let http = api::client();
                let cancel = remedy_ui::reactive::Cancel::new();
                let result = call(&http, &body, &cancel);
                let _ = waker.post(move || {
                    // Close bumped `session` and already reset the form. A poll did not.
                    if panel.session.get() != epoch {
                        return;
                    }
                    if let Some(apply) = take_apply(&slot_thread) {
                        apply(&panel, result);
                    }
                });
            });
        if spawned.is_err() {
            if let Some(apply) = take_apply(&slot) {
                apply(self, Err(fail));
            }
        }
    }
}

fn take_apply<F>(slot: &std::sync::Mutex<Option<F>>) -> Option<F>
where
    F: FnOnce(&DiagnosticsPanel, Result<HiveOp, String>),
{
    slot.lock().ok().and_then(|mut guard| guard.take())
}

fn sync_layer(panel: &DiagnosticsPanel, ui: &Ui, layer: &Rc<RefCell<Option<El>>>, is_open: bool) {
    if is_open {
        if layer.borrow().is_some() {
            return;
        }
        let scrim = view::scrim(ui, panel, true);
        let mounted = ui.overlay(scrim);
        *layer.borrow_mut() = Some(mounted.clone());
        panel.load_diagnostics();
        panel.load_hive(true);
        panel.arm_timer(ui);
        let _ = mounted.focus_first();
    } else {
        panel.disarm(ui);
        if let Some(el) = layer.borrow_mut().take() {
            el.remove();
        }
    }
}

/// Golden root: the open panel with sample server, host, hardware, and hive data.
pub fn preview(ui: &Ui) -> El {
    crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
    let panel = DiagnosticsPanel::sample();
    view::scrim(ui, &panel, false)
}
