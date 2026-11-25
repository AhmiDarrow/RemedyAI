//! Hive inspector inside Diagnostics, plus spawn and reassign.
//!
//! Retire, the roster, and the pronoun line follow `HiveInspector.tsx`.
//! Spawn and reassign follow the task form in `AutomationsSlide.tsx`: the
//! parity row's backing includes `POST /hive/spawn` and `POST /hive/assign`,
//! and this panel is where those actions are presented. The automations rail
//! itself belongs to another slice.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::a11y::{Live, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::DiagnosticsPanel;
use super::model::{self, HiveRow};

/// The hive card. Always present, including while diagnostics are still loading.
pub(crate) fn section(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let unit = ui.theme().spacing.unit;
    w::card(ui)
        .gap(unit * 2.0)
        .semantics(Semantics::new(Role::Group).label("Hive"))
        .child(heading(ui, panel))
        .child(hive_error(ui, panel))
        .child(spawn_form(ui, panel))
        .child(w::divider(ui, false))
        .child(roster(ui, panel))
}

fn heading(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let ready = panel.hive_ready;
    let rows = panel.rows;
    let posts = panel.live_posts;
    let foragers = panel.live_foragers;
    w::column(ui)
        .gap(ui.theme().spacing.unit * 0.5)
        .child(w::text(ui, "Hive").variant("strong"))
        .child(
            w::text(ui, move || {
                let mut line = model::HIVE_INTRO.to_string();
                if ready.get() {
                    let live = rows.get().iter().filter(|r| model::hive_is_live(r)).count();
                    line.push_str(&format!(
                        " · {live} live · {} posts · {} foraging",
                        posts.get(),
                        foragers.get()
                    ));
                }
                line
            })
            .variant("small"),
        )
}

fn hive_error(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let err = panel.hive_error;
    show(ui, move || err.get().is_some(), {
        let ui = ui.clone();
        move || {
            let err_t = err;
            w::text(&ui, move || err_t.get().unwrap_or_default())
                .variant("error")
                .semantics(move || {
                    Semantics::new(Role::Alert)
                        .live(Live::Assertive)
                        .label(err_t.get().unwrap_or_default())
                })
        }
    })
    .el()
}

fn spawn_form(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let unit = ui.theme().spacing.unit;
    let goal = panel.goal;
    let field = w::text_area(ui, "Task instructions")
        .placeholder("What should Remedy work on in the background?");
    let sink = field.clone();
    let field = field.on_change(move || goal.set(sink.value()));
    let input = field.el().layout(|l| l.min_height = Length::Px(56.0));
    let panel_key = panel.clone();
    let input = ui
        .el("spawn-goal")
        .column()
        .fill_width()
        .child(input)
        .on_key_capture(move |e| {
            if e.pressed
                && !e.composing
                && e.key == Key::Enter
                && (e.modifiers.ctrl || e.modifiers.meta)
            {
                panel_key.spawn_task();
                true
            } else {
                false
            }
        });
    let input = watch_clear(input, field, panel);
    let steps = labeled_stepper(ui, "Steps", panel.budget, 1.0, 16.0, 1.0);
    let pulse = show(
        ui,
        {
            let cadence = panel.cadence;
            move || cadence.get() == 1
        },
        {
            let ui = ui.clone();
            let pulse = panel.pulse;
            move || labeled_stepper(&ui, "Every (seconds)", pulse, 30.0, 3600.0, 30.0)
        },
    )
    .el();
    let controls = w::wrap(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .child(w::segmented(
            ui,
            "Cadence",
            &["Run once", "Repeat"],
            panel.cadence,
        ))
        .child(steps)
        .child(pulse)
        .child(start_button(ui, panel));
    w::column(ui)
        .gap(unit * 2.0)
        .fill_width()
        .child(input)
        .child(controls)
        .child(spawn_note(ui, panel))
}

fn watch_clear(host: El, field: remedy_ui::TextField, panel: &DiagnosticsPanel) -> El {
    let seen = Rc::new(Cell::new(panel.clear_goal.get_untracked().unwrap_or(0)));
    let clear = panel.clear_goal;
    let goal = panel.goal;
    host.bind(move || {
        let n = clear.get();
        if seen.get() != n {
            seen.set(n);
            remedy_ui::reactive::untrack(|| {
                field.set_value("");
                goal.set(String::new());
            });
        }
    })
}

fn labeled_stepper(ui: &Ui, label: &str, value: Signal<f64>, min: f64, max: f64, step: f64) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(w::text(ui, label).variant("small"))
        .child(w::stepper(ui, label, value, min, max, step))
}

fn start_button(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let spawning = panel.spawning;
    let goal = panel.goal;
    let panel = panel.clone();
    w::button(
        ui,
        move || -> String {
            if spawning.get() {
                "Starting…".into()
            } else {
                "Start task".into()
            }
        },
        move || panel.spawn_task(),
    )
    .variant("primary")
    .disabled(move || spawning.get() || goal.get().trim().is_empty())
}

fn spawn_note(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let note = panel.spawn_note;
    show(ui, move || note.get().is_some(), {
        let ui = ui.clone();
        move || {
            let note_t = note;
            let ui_c = ui.clone();
            w::text(&ui, move || {
                note_t
                    .get()
                    .map(|n| format!("{} {}", if n.ok { "✓" } else { "✗" }, n.text))
                    .unwrap_or_default()
            })
            .style(move || {
                let colors = ui_c.theme().colors;
                let ok = note_t.get().map(|n| n.ok).unwrap_or(false);
                StylePatch::new()
                    .foreground(if ok { colors.ok } else { colors.danger })
                    .text_size(ui_c.theme().type_scale.small)
            })
            .semantics(move || {
                Semantics::new(Role::Status)
                    .live(Live::Polite)
                    .label(note_t.get().map(|n| n.text).unwrap_or_default())
            })
        }
    })
    .el()
}

fn roster(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let rows = panel.rows;
    let empty = show(ui, move || rows.get().is_empty(), {
        let ui = ui.clone();
        move || {
            w::text(&ui, "No daughters hired.")
                .variant("small")
                .padding(Insets::symmetric(ui.theme().spacing.unit * 2.0, 0.0))
        }
    })
    .el();
    let list = ui
        .el("hive-rows")
        .column()
        .gap(ui.theme().spacing.unit)
        .scroll()
        .layout(|l| {
            l.max_height = Length::Px(192.0);
            l.grow = 0.0;
            l.min_height = Length::Px(0.0);
        });
    let panel_r = panel.clone();
    let ui_r = ui.clone();
    let list = w::for_each(
        &list,
        rows,
        |row| row.id.clone(),
        move |row| hive_row(&ui_r, &panel_r, row),
    );
    w::column(ui)
        .gap(ui.theme().spacing.unit)
        .child(empty)
        .child(list)
}

fn hive_row(ui: &Ui, panel: &DiagnosticsPanel, row: &HiveRow) -> El {
    let unit = ui.theme().spacing.unit;
    let small = ui.theme().type_scale.small;
    let id = row.id.clone();
    let rows = panel.rows;
    let id_label = id.clone();
    let label = w::text(ui, move || {
        rows.get()
            .iter()
            .find(|r| r.id == id_label)
            .map(model::hive_row_label)
            .unwrap_or_default()
    })
    .style(StylePatch::new().weight(600.0).text_size(small))
    .truncate()
    .layout(|l| l.min_width = Length::Px(0.0));
    let rows_tip = panel.rows;
    let id_tip = id.clone();
    w::tooltip(&label, move || {
        rows_tip
            .get()
            .iter()
            .find(|r| r.id == id_tip)
            .map(|r| r.goal.clone())
            .unwrap_or_default()
    });
    let text_col = w::column(ui)
        .gap(unit * 0.5)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(label)
        .child(outcome_line(ui, panel, &id))
        .child(blocker_line(ui, panel, &id));
    w::column(ui)
        .gap(unit)
        .padding(Insets::symmetric(unit, 0.0))
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(ui.theme().stroke, ui.theme().derive().line.with_alpha(0.5)),
        ))
        .child(
            w::row(ui)
                .align(Align::Start)
                .justify(Justify::SpaceBetween)
                .gap(unit * 2.0)
                .child(text_col)
                .child(actions(ui, panel, &id).layout(|l| l.shrink = 0.0)),
        )
        .child(assign_editor(ui, panel, &id))
}

fn outcome_line(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let rows = panel.rows;
    let id_when = id.to_string();
    let id_text = id.to_string();
    show(
        ui,
        move || {
            rows.get()
                .iter()
                .any(|r| r.id == id_when && !r.outcome.is_empty())
        },
        {
            let ui = ui.clone();
            move || {
                let rows = rows;
                let id = id_text.clone();
                let line = w::text(&ui, move || {
                    rows.get()
                        .iter()
                        .find(|r| r.id == id)
                        .map(|r| r.outcome.clone())
                        .unwrap_or_default()
                })
                .variant("small")
                .truncate();
                w::full_text_tooltip(&line);
                line
            }
        },
    )
    .el()
}

fn blocker_line(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let rows = panel.rows;
    let id_when = id.to_string();
    let id_text = id.to_string();
    let warn = ui.theme().colors.warn;
    let small = ui.theme().type_scale.small;
    show(
        ui,
        move || {
            rows.get()
                .iter()
                .any(|r| r.id == id_when && !r.blockers.is_empty())
        },
        {
            let ui = ui.clone();
            move || {
                let rows = rows;
                let id = id_text.clone();
                w::text(&ui, move || {
                    rows.get()
                        .iter()
                        .find(|r| r.id == id)
                        .map(|r| {
                            r.blockers
                                .iter()
                                .take(2)
                                .cloned()
                                .collect::<Vec<_>>()
                                .join("; ")
                        })
                        .unwrap_or_default()
                })
                .style(StylePatch::new().foreground(warn).text_size(small))
            }
        },
    )
    .el()
}

fn actions(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .align(Align::End)
        .child(reassign_button(ui, panel, id))
        .child(retire_button(ui, panel, id))
}

fn reassign_button(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let rows = panel.rows;
    let id_when = id.to_string();
    show(
        ui,
        move || {
            rows.get()
                .iter()
                .any(|r| r.id == id_when && model::hive_is_live(r) && r.cadence == "post")
        },
        {
            let ui = ui.clone();
            let panel = panel.clone();
            let id = id.to_string();
            move || {
                let busy = panel.hive_busy;
                let panel_c = panel.clone();
                let id_c = id.clone();
                w::button(&ui, "Reassign", move || toggle_assign(&panel_c, &id_c))
                    .variant("quiet")
                    .disabled(move || busy.get())
            }
        },
    )
    .el()
}

fn retire_button(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let rows = panel.rows;
    let id_when = id.to_string();
    show(
        ui,
        move || {
            rows.get()
                .iter()
                .any(|r| r.id == id_when && model::hive_is_live(r))
        },
        {
            let ui = ui.clone();
            let panel = panel.clone();
            let id = id.to_string();
            move || {
                let pending = panel.pending_retire;
                let busy = panel.hive_busy;
                let id_label = id.clone();
                let id_tone = id.clone();
                let panel_c = panel.clone();
                let id_c = id.clone();
                w::button(
                    &ui,
                    move || -> String {
                        if pending.get().as_deref() == Some(id_label.as_str()) {
                            "Confirm stop".into()
                        } else {
                            "Stop".into()
                        }
                    },
                    move || panel_c.retire_task(&id_c),
                )
                .variant(move || {
                    if pending.get().as_deref() == Some(id_tone.as_str()) {
                        "danger_solid"
                    } else {
                        "danger"
                    }
                })
                .disabled(move || busy.get())
            }
        },
    )
    .el()
}

fn assign_editor(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let target = panel.assign_target;
    let id_when = id.to_string();
    show(
        ui,
        move || target.get().as_deref() == Some(id_when.as_str()),
        {
            let ui = ui.clone();
            let panel = panel.clone();
            let id = id.to_string();
            move || editor(&ui, &panel, &id)
        },
    )
    .el()
}

fn editor(ui: &Ui, panel: &DiagnosticsPanel, id: &str) -> El {
    let unit = ui.theme().spacing.unit;
    let draft = panel.assign_draft.get();
    let field = w::text_input(ui, "Updated task instructions").placeholder("New goal…");
    field.set_value(&draft);
    let sink = field.clone();
    let draft_sig = panel.assign_draft;
    let field = field.on_change(move || draft_sig.set(sink.value()));
    let panel_s = panel.clone();
    let id_s = id.to_string();
    let field = field.on_submit(move || panel_s.assign_task(&id_s));
    let input = field.el();
    let busy = panel.hive_busy;
    let draft_now = panel.assign_draft;
    let panel_b = panel.clone();
    let id_b = id.to_string();
    let save = w::button(ui, "Save", move || panel_b.assign_task(&id_b))
        .variant("primary")
        .disabled(move || busy.get() || draft_now.get().trim().is_empty());
    let panel_c = panel.clone();
    let cancel = w::button(ui, "Cancel", move || panel_c.assign_target.set(None)).variant("quiet");
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .child(input.layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        }))
        .child(save)
        .child(cancel)
}

fn toggle_assign(panel: &DiagnosticsPanel, id: &str) {
    if panel.hive_busy.get() {
        return;
    }
    if panel.assign_target.get().as_deref() == Some(id) {
        panel.assign_target.set(None);
        return;
    }
    let goal = panel
        .rows
        .get()
        .into_iter()
        .find(|r| r.id == id)
        .map(|r| r.goal)
        .unwrap_or_default();
    panel.assign_draft.set(goal);
    panel.pending_retire.set(None);
    panel.assign_target.set(Some(id.to_string()));
}
