//! Remedy at the head of the house: side jobs, routines, and what she did
//! while the owner was away. Settings and the new-routine form open from here.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::api::{Activity, HeadChild, HeadRoutine};
use super::logic::{self, ScheduleChoice};
use super::state::Grove;

pub(super) fn head_block(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 3.0,
            bottom: unit,
            start: unit * 3.0,
        })
        .semantics({
            let g = grove.clone();
            move || Semantics::new(Role::Group).label(format!("{} and her side jobs", g.partner()))
        })
        .child(head_row(ui, grove.clone()))
        .child(settings_panel(ui, grove.clone()))
        .child(toast(ui, g))
        .child(children_block(ui, grove.clone()))
        .child(add_job(ui, grove))
}

pub(super) fn house_block(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let seen = ui.el("grove-seen").bind({
        let g = grove.clone();
        move || {
            let unseen = g.head.get().map(|h| h.activity_unseen).unwrap_or(0);
            if unseen > 0 {
                g.mark_seen_later();
            }
        }
    });
    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .semantics({
            let g = grove.clone();
            move || {
                Semantics::new(Role::Group)
                    .label(format!("{}'s routines and recent activity", g.partner()))
            }
        })
        .child(seen)
        .child(toast(ui, grove.clone()))
        .child(routines(ui, grove.clone()))
        .child(activity(ui, grove))
}

fn head_row(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let open = {
        let g = grove.clone();
        w::row(ui)
            .align(Align::Center)
            .gap(unit)
            .child(crate::logo::mark(ui, 26.0))
            .on_click(move || g.go_home())
            .semantics({
                let g = grove.clone();
                move || {
                    Semantics::new(Role::Button)
                        .label(format!("Open {}'s own conversation", g.partner()))
                        .action(Action::Click)
                }
            })
    };
    let name = w::text(ui, "")
        .variant("strong")
        .text({
            let g = grove.clone();
            move || g.partner()
        })
        .nowrap()
        .layout(|l| l.shrink = 0.0);
    let tag = w::text(ui, "")
        .variant("muted")
        .text({
            let g = grove.clone();
            move || {
                if g.head.get().is_some_and(|h| h.heartbeat.enabled) {
                    "Always on".to_string()
                } else {
                    "On call".to_string()
                }
            }
        })
        .nowrap()
        .layout(|l| l.shrink = 0.0);
    let status = w::text(ui, "").text({
        let g = grove.clone();
        move || status_line(&g)
    });
    let watch = {
        let g = grove.clone();
        w::button(ui, "Keep watch", move || {
            let on = g.head.get().is_some_and(|h| h.heartbeat.enabled);
            g.toggle_watch(!on);
        })
        .variant("quiet")
        .semantics({
            let g = grove.clone();
            move || {
                let on = g.head.get().is_some_and(|h| h.heartbeat.enabled);
                Semantics::new(Role::Switch)
                    .label("Keep watch")
                    .checked(on)
                    .action(Action::Click)
            }
        })
    };
    let g = grove.clone();
    let gear = w::button(ui, "Settings", move || {
        g.settings_open.update(|v| *v = !*v);
    })
    .variant("quiet");
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            // Narrow column: the buttons drop below rather than crush the status.
            l.wrap = true;
        })
        .child(open.layout(|l| l.shrink = 0.0))
        .child(
            w::column(ui)
                .gap(unit)
                .layout(|l| {
                    l.shrink = 1.0;
                    l.min_width = Length::Px(150.0);
                })
                .child(
                    w::row(ui)
                        .align(Align::Center)
                        .gap(unit)
                        .child(name)
                        .child(tag),
                )
                .child(status),
        )
        .child(watch.layout(|l| l.shrink = 0.0))
        .child(gear.layout(|l| l.shrink = 0.0))
}

fn status_line(grove: &Grove) -> String {
    let Some(head) = grove.head.get() else {
        return "Here when you need me".into();
    };
    let children = grove.children.get();
    let working = children.iter().filter(|c| c.state == "working").count();
    let any = head.state == "working" || working > 0;
    if any {
        return if working > 0 {
            format!(
                "Working — {working} side job{} going",
                if working > 1 { "s" } else { "" }
            )
        } else {
            "Working on something for you".into()
        };
    }
    let need = children.iter().filter(|c| logic::needs_owner(&c.state)).count();
    if need > 0 {
        return format!(
            "{need} side job{} need{} you",
            if need > 1 { "s" } else { "" },
            if need > 1 { "" } else { "s" }
        );
    }
    if head.heartbeat.enabled && !head.heartbeat.last_note.is_empty() {
        return head.heartbeat.last_note;
    }
    if head.heartbeat.enabled {
        "Keeping watch".into()
    } else {
        "Here when you need me".into()
    }
}

fn toast(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || !g.note.get().is_empty() || !g.err.get().is_empty(), {
        let ui = ui.clone();
        let g = grove;
        move || {
            w::text(&ui, "").text(move || {
                let err = g.err.get();
                if err.is_empty() { g.note.get() } else { err }
            })
        }
    })
    .el()
}

fn resting(state: &str) -> bool {
    matches!(state, "done" | "stopped" | "error")
}

fn children_block(ui: &Ui, grove: Grove) -> El {
    let rows: Signal<Vec<HeadChild>> = Signal::new(Vec::new());
    let watch = ui.el("grove-children").bind({
        let g = grove.clone();
        let rows = rows;
        move || {
            let mut live = Vec::new();
            let mut rest = Vec::new();
            for child in g.children.get() {
                if resting(&child.state) {
                    rest.push(child);
                } else {
                    live.push(child);
                }
            }
            live.extend(rest.into_iter().take(4));
            rows.set(live);
        }
    });
    let col = w::column(ui).gap(ui.theme().spacing.unit);
    w::for_each(&col, rows, |c: &HeadChild| c.child_id.clone(), {
        let ui = ui.clone();
        let g = grove.clone();
        move |c| child_row(&ui, &g, c)
    });
    w::column(ui).child(watch).child(col)
}

fn child_row(ui: &Ui, grove: &Grove, child: &HeadChild) -> El {
    let unit = ui.theme().spacing.unit;
    let id = child.child_id.clone();
    let label = child.label.clone();
    let g = grove.clone();
    let open = w::button(ui, format!("Open side job: {label}"), move || g.open_child(&id));
    let sub = format!(
        "{}{}",
        logic::state_words(&child.state),
        if child.last_report.is_empty() {
            String::new()
        } else {
            format!(" · {}", child.last_report)
        }
    );
    let stop = if !resting(&child.state) && child.state != "done" {
        let g = grove.clone();
        let id = child.child_id.clone();
        let label = child.label.clone();
        Some(
            w::button(ui, format!("Stop {label}"), move || g.stop_child(&id)).variant("quiet"),
        )
    } else {
        None
    };
    let clear = if resting(&child.state) {
        let g = grove.clone();
        let id = child.child_id.clone();
        let label = child.label.clone();
        Some(w::button(ui, format!("Clear {label}"), move || g.forget_child(&id)).variant("quiet"))
    } else {
        None
    };
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(open)
        .child(w::text(ui, child.label.clone()).variant("strong"))
        .child(w::text(ui, sub).variant("muted"));
    if let Some(b) = stop {
        row = row.child(b);
    }
    if let Some(b) = clear {
        row = row.child(b);
    }
    row
}

fn add_job(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    let button = show(ui, move || !g.job_open.get(), {
        let ui = ui.clone();
        let g = grove.clone();
        move || {
            let cap = w::text(&ui, "").variant("muted").text({
                let g = g.clone();
                move || {
                    g.head
                        .get()
                        .map(|h| {
                            if h.capacity.paused {
                                "PC busy — jobs wait".into()
                            } else {
                                let free = (h.capacity.slots - h.capacity.busy).max(0);
                                format!("{free} of {} free", h.capacity.slots)
                            }
                        })
                        .unwrap_or_default()
                }
            });
            w::row(&ui)
                .align(Align::Center)
                .gap(ui.theme().spacing.unit)
                .child(w::button(&ui, {
                    let g = g.clone();
                    move || format!("+ Give {} a side job", g.partner())
                }, {
                    let g = g.clone();
                    move || g.job_open.set(true)
                }).variant("quiet"))
                .child(cap)
        }
    })
    .el();
    let g = grove.clone();
    let form = show(ui, move || g.job_open.get(), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || job_form(&ui, grove.clone())
    })
    .el();
    w::column(ui).child(button).child(form)
}

fn job_form(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let task = w::text_area(ui, "Side job")
        .placeholder("What should this side job do? Be complete — it works on its own and reports back.");
    let label = w::text_input(ui, "Short name").placeholder("Short name (optional)").max_length(60);
    let t = task.clone();
    let l = label.clone();
    let g = grove.clone();
    let start = w::button(ui, "Start", move || g.spawn_job(&t.value(), &l.value()));
    let g = grove.clone();
    let cancel = w::button(ui, "Cancel", move || g.job_open.set(false)).variant("quiet");
    w::column(ui)
        .gap(unit)
        .child(task.el())
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                .child(label.el().layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                }))
                .child(cancel)
                .child(start),
        )
}

fn settings_panel(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || g.settings_open.get(), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || settings(&ui, grove.clone())
    })
    .el()
}

fn settings(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let head = grove.head.get();
    let every_now = head.as_ref().map(|h| h.heartbeat.every_minutes).unwrap_or(60);
    let choices = [15_i64, 30, 60, 120, 240];
    let selected = Signal::new(
        choices
            .iter()
            .position(|m| *m == every_now)
            .unwrap_or(2),
    );
    let labels: Vec<String> = choices
        .iter()
        .map(|m| {
            if *m < 60 {
                format!("{m} minutes")
            } else {
                let h = m / 60;
                format!("{h} hour{}", if h > 1 { "s" } else { "" })
            }
        })
        .collect();
    let every = w::select(ui, "How often", labels, selected);
    let qs = w::text_input(ui, "Quiet from");
    let qe = w::text_input(ui, "Quiet until");
    if let Some(h) = &head {
        qs.set_value(&h.heartbeat.quiet_start);
        qe.set_value(&h.heartbeat.quiet_end);
    }
    let recent = head.as_ref().map(|h| h.recent_channels.clone()).unwrap_or_default();
    let mut channel_labels = vec!["Grove only".to_string()];
    for c in &recent {
        let who = if c.username.is_empty() { c.chat_id.as_str() } else { c.username.as_str() };
        channel_labels.push(format!("{} · {who}", c.channel));
    }
    let current = head.as_ref().and_then(|h| h.owner_channel.clone());
    let channel_index = current
        .as_ref()
        .and_then(|c| recent.iter().position(|r| r.channel == c.channel && r.chat_id == c.chat_id))
        .map(|i| i + 1)
        .unwrap_or(0);
    let channel_sel = Signal::new(channel_index);
    let channel = w::select(ui, "Send updates to", channel_labels, channel_sel);
    let seen = Rc::new(Cell::new(channel_index));
    let recent_c = recent.clone();
    let g = grove.clone();
    let channel = channel.bind(move || {
        let i = channel_sel.get();
        if i == seen.get() {
            return;
        }
        seen.set(i);
        if i == 0 {
            g.set_channel(None);
        } else if let Some(c) = recent_c.get(i - 1) {
            g.set_channel(Some(c.clone()));
        }
    });
    let note = head
        .as_ref()
        .map(|h| {
            let mut s = format!("This PC: {}", h.capacity_note);
            if h.capacity.memory_known {
                s.push_str(&format!(" {}% memory free.", h.capacity.free_mem_pct));
            }
            s
        })
        .unwrap_or_default();
    let qs_f = qs.clone();
    let qe_f = qe.clone();
    let g = grove.clone();
    let save = w::button(ui, "Save", move || {
        let idx = selected.get().min(choices.len() - 1);
        g.save_heartbeat(choices[idx], qs_f.value(), qe_f.value());
    });
    let g = grove.clone();
    let close = w::button(ui, "Close", move || g.settings_open.set(false)).variant("quiet");
    let mut col = w::column(ui)
        .gap(unit)
        .semantics(Semantics::new(Role::Group).label("Always-on settings"))
        .child(w::text(ui, "Look around every"))
        .child(every)
        .child(w::text(ui, "Quiet hours"))
        .child(qs.el())
        .child(w::text(ui, "to"))
        .child(qe.el())
        .child(w::text(ui, "Send updates to"))
        .child(channel);
    if recent.is_empty() {
        col = col.child(w::text(
            ui,
            "To get updates on your phone, message Remedy once from Telegram, Discord or another connected chat, then pick it here.",
        ).variant("muted"));
    }
    // Her nights: off until the owner turns them on. The check follows the
    // runtime's state, and a click asks the runtime to change it.
    let nights = Signal::new(grove.vigil_on.get());
    let night_check = w::check(ui, "Give her the quiet hours for her own thinking", nights).bind({
        let g = grove.clone();
        move || {
            let (want, have) = (nights.get(), g.vigil_on.get());
            if want != have {
                g.set_vigil(want, nights);
            }
        }
    });
    let follow = ui.el("vigil-follow").bind({
        let g = grove.clone();
        move || nights.set_if_changed(g.vigil_on.get())
    });
    col.child(night_check)
        .child(follow)
        .child(
            w::text(
                ui,
                "At night she tidies what she learned, dreams on recent work, and tells you in the morning. It runs on this PC, calls no model, and sends nothing.",
            )
            .variant("muted")
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            }),
        )
        .child(w::text(ui, note).variant("muted"))
        .child(w::row(ui).gap(unit).child(close).child(save))
}

fn routines(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let header = w::row(ui)
        .align(Align::Center)
        .justify(remedy_ui::layout::Justify::SpaceBetween)
        .child(w::heading(ui, 3, "Routines"))
        .child(w::button(ui, "+ New", {
            let ui = ui.clone();
            let g = grove.clone();
            move || open_routine(&ui, g.clone(), None)
        }));
    let empty = {
        let g = grove.clone();
        show(ui, move || g.routines.get().is_empty(), {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                w::text(
                    &ui,
                    format!(
                        "Standing jobs on a clock — “every weekday at 7, brief me”. Reminders cost nothing; tasks have {} do the work.",
                        g.partner()
                    ),
                )
                .variant("muted")
            }
        })
        .el()
    };
    let col = w::column(ui).gap(unit);
    w::for_each(&col, grove.routines, |r: &HeadRoutine| {
        // Rebuild a row when anything it shows changes.
        format!(
            "{}:{}:{}:{}:{}:{}:{}",
            r.routine_id, r.enabled, r.title, r.when, r.next_run, r.last_run, r.runs
        )
    }, {
        let ui = ui.clone();
        let g = grove.clone();
        move |r| routine_row(&ui, &g, r)
    });
    w::column(ui).gap(unit).child(header).child(empty).child(col)
}

fn routine_row(ui: &Ui, grove: &Grove, routine: &HeadRoutine) -> El {
    let unit = ui.theme().spacing.unit;
    let next = if routine.enabled {
        logic::relative_when(Some(&routine.next_run), now_ms())
    } else {
        String::new()
    };
    let mut sub = routine.when.clone();
    if !next.is_empty() {
        sub.push_str(" · next ");
        sub.push_str(&next);
    }
    if !routine.last_state.is_empty() && routine.last_state != "done" {
        sub.push_str(" · ");
        sub.push_str(logic::state_words(&routine.last_state));
    }
    // Run history as the server keeps it: last run, count, and what it came to.
    let mut history = String::new();
    if routine.runs > 0 {
        let last = logic::relative_when(Some(&routine.last_run), now_ms());
        history = if routine.runs == 1 { "Ran once".into() } else { format!("Ran {} times", routine.runs) };
        if !last.is_empty() {
            history.push_str(" · last ");
            history.push_str(&last);
        }
        if !routine.last_summary.is_empty() {
            history.push_str(" · ");
            history.push_str(&routine.last_summary);
        }
    }
    let edit = w::button(ui, format!("Edit {}", routine.title), {
        let (ui, g, r) = (ui.clone(), grove.clone(), routine.clone());
        move || open_routine(&ui, g.clone(), Some(r.clone()))
    })
    .variant("quiet");
    let id = routine.routine_id.clone();
    let title = routine.title.clone();
    let g = grove.clone();
    let session = grove.head.get().map(|h| h.session_id).unwrap_or_default();
    let open = if routine.target == "head" && !session.is_empty() {
        let g = grove.clone();
        w::button(ui, title.clone(), move || g.open_linked(&session))
    } else {
        w::text(ui, title.clone()).variant("strong")
    };
    let on = routine.enabled;
    let g_sw = grove.clone();
    let id_sw = id.clone();
    let switch = w::button(ui, if on { "Pause" } else { "Resume" }, move || {
        g_sw.toggle_routine(&id_sw, !on);
    });
    let g_run = grove.clone();
    let id_run = id.clone();
    let title_run = title.clone();
    let run = w::button(ui, format!("Run {title} now"), move || {
        g_run.run_routine(&id_run, &title_run);
    })
    .variant("quiet");
    let g_del = g;
    let id_del = id;
    let title_del = title;
    let ui_d = ui.clone();
    let retire = w::button(ui, format!("Retire {title_del}"), move || {
        let g = g_del.clone();
        let id = id_del.clone();
        let title = title_del.clone();
        w::dialog(
            &ui_d,
            "Retire this routine",
            format!("Retire “{title}”?"),
            vec![
                w::DialogButton::new("Cancel", "quiet", || {}),
                w::DialogButton::new("Retire", "danger", move || g.retire_routine(&id)),
            ],
        );
    })
    .variant("quiet");
    let row = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(w::text(ui, if routine.mode == "remind" { "Remind" } else { "Task" }).variant("muted"))
        .child(open)
        .child(w::text(ui, sub).variant("muted"))
        .child(switch)
        .child(run)
        .child(edit)
        .child(retire);
    if history.is_empty() {
        return row;
    }
    w::column(ui)
        .child(row)
        .child(w::text(ui, history).variant("muted").layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        }))
}

fn activity(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    show(ui, move || !g.activity.get().is_empty(), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let shown: Signal<Vec<Activity>> = Signal::new(Vec::new());
            let watch = ui.el("grove-activity").bind({
                let g = grove.clone();
                let shown = shown;
                move || {
                    let all = g.activity.get();
                    let list = if g.show_all.get() {
                        all
                    } else {
                        all.into_iter().take(5).collect()
                    };
                    shown.set(list);
                }
            });
            let col = w::column(&ui).gap(unit);
            w::for_each(&col, shown, |a: &Activity| a.id.clone(), {
                let ui = ui.clone();
                let g = grove.clone();
                move |a| activity_row(&ui, &g, a)
            });
            let more = show(&ui, {
                let g = grove.clone();
                move || g.activity.get().len() > 5
            }, {
                let ui = ui.clone();
                let g = grove.clone();
                move || {
                    w::button(&ui, {
                        let g = g.clone();
                        move || {
                            let n = g.activity.get().len();
                            if g.show_all.get() {
                                "Show less".into()
                            } else {
                                format!("Show all {n}")
                            }
                        }
                    }, move || g.show_all.update(|v| *v = !*v))
                }
            })
            .el();
            let unseen = w::text(&ui, "").text({
                let g = grove.clone();
                move || {
                    let n = g.head.get().map(|h| h.activity_unseen).unwrap_or(0);
                    if n > 99 {
                        "While you were away  99+".into()
                    } else if n > 0 {
                        format!("While you were away  {n}")
                    } else {
                        "While you were away".into()
                    }
                }
            });
            let g = grove.clone();
            w::column(&ui)
                .gap(unit)
                .child(
                    w::row(&ui)
                        .align(Align::Center)
                        .child(unseen)
                        .child(w::button(&ui, "Check now", move || g.check_now())),
                )
                .child(watch)
                .child(col)
                .child(more)
        }
    })
    .el()
}

fn activity_row(ui: &Ui, grove: &Grove, item: &Activity) -> El {
    let when = logic::relative_when(Some(&item.ts), now_ms());
    let title = if !item.state.is_empty() && logic::needs_owner(&item.state) {
        format!("{}  needs you", item.title)
    } else {
        item.title.clone()
    };
    let text = if item.detail.is_empty() {
        format!("{title}  {when}")
    } else {
        format!("{title} — {}  {when}", item.detail)
    };
    if item.session_id.is_empty() {
        w::text(ui, text)
    } else {
        let g = grove.clone();
        let sid = item.session_id.clone();
        w::button(ui, text, move || g.open_linked(&sid)).variant("quiet")
    }
}

/// New routine, or an edit of `existing` with its fields filled in.
fn open_routine(ui: &Ui, grove: Grove, existing: Option<HeadRoutine>) -> () {
    let unit = ui.theme().spacing.unit;
    let name = grove.partner();
    let hours = [2_i64, 3, 4, 6, 8, 12];
    let old = existing.clone().unwrap_or_default();
    let edit_id = existing.as_ref().map(|r| r.routine_id.clone());
    let heading = if existing.is_some() { "Edit routine" } else { "New routine" };
    // weekdays, daily, weekly, hourly, every, once
    let (choice0, hours0) = match (old.kind.as_str(), old.every_minutes) {
        ("daily", _) => (1, 0),
        ("weekly", _) => (2, 0),
        ("every", 60) => (3, 0),
        ("every", m) => (4, hours.iter().position(|h| h * 60 == m).unwrap_or(0)),
        ("once", _) => (5, 0),
        _ => (0, 0),
    };
    let was_once = choice0 == 5;
    let task_mode = Signal::new(old.mode != "remind");
    let choice = Signal::new(choice0);
    let every_hours = Signal::new(hours0); // 2, 3, 4, 6, 8, 12
    let days: Signal<Vec<String>> = Signal::new(if old.days.is_empty() {
        vec!["mon".into()]
    } else {
        old.days.clone()
    });
    let phone = Signal::new(matches!(old.deliver.as_str(), "both" | "messenger"));
    let what = w::text_area(ui, "What should Remedy do")
        .placeholder("e.g. Check my inbox and calendar, then give me a short brief for the day");
    what.set_value(&old.what);
    let title = w::text_input(ui, "Name").placeholder("Name (optional)").max_length(120);
    title.set_value(&old.title);
    let at = w::text_input(ui, "At").placeholder("08:00");
    at.set_value(if old.at.is_empty() { "08:00" } else { &old.at });
    let once = w::text_input(ui, "Date and time").placeholder(if was_once {
        "YYYY-MM-DDTHH:MM (blank keeps the current time)"
    } else {
        "YYYY-MM-DDTHH:MM"
    });
    let hour_labels: Vec<String> = hours.iter().map(|h| format!("every {h} hours")).collect();
    let when_labels = vec![
        "Weekdays".into(),
        "Every day".into(),
        "Certain days".into(),
        "Every hour".into(),
        "Every few hours".into(),
        "Once".into(),
    ];
    let can_phone = grove.head.get().is_some_and(|h| h.owner_channel.is_some());
    let slot: Rc<RefCell<Option<w::Modal>>> = Rc::new(RefCell::new(None));
    let err = Signal::new(String::new());
    let what_f = what.clone();
    let title_f = title.clone();
    let at_f = at.clone();
    let once_f = once.clone();
    let g = grove.clone();
    // Turns true when the server has the routine; only then does the dialog close.
    let saved = Signal::new(false);
    let save = w::button(ui, "Save routine", move || {
        let body_text = what_f.value().trim().to_string();
        if body_text.is_empty() {
            err.set("Say what this routine does.".into());
            return;
        }
        let picked = match choice.get() {
            1 => ScheduleChoice::Daily,
            2 => ScheduleChoice::Weekly,
            3 => ScheduleChoice::Hourly,
            4 => ScheduleChoice::Every,
            5 => ScheduleChoice::Once,
            _ => ScheduleChoice::Weekdays,
        };
        let hour = hours.get(every_hours.get()).copied().unwrap_or(2) as f64;
        let day_list = days.get();
        let schedule = logic::schedule_from_choice(
            picked,
            Some(&at_f.value()),
            Some(hour),
            &day_list,
            Some(&once_f.value()),
            now_ms(),
        );
        let mut body = serde_json::json!({
            "what": body_text,
            "mode": if task_mode.get() { "task" } else { "remind" },
            "schedule": {
                "kind": schedule.kind,
                "every_minutes": schedule.every_minutes,
                "at": schedule.at,
                "days": schedule.days,
                "once_at": schedule.once_at,
            },
            "deliver": if phone.get() { "both" } else { "grove" },
        });
        let named = title_f.value().trim().to_string();
        if !named.is_empty() {
            body["title"] = serde_json::Value::String(named);
        }
        // An edited one-off with no new time keeps the time it has.
        if was_once && picked == ScheduleChoice::Once && once_f.value().trim().is_empty() {
            if let Some(map) = body.as_object_mut() {
                map.remove("schedule");
            }
        }
        err.set(String::new());
        g.save_routine(edit_id.clone(), body, saved, err);
    });
    let slot_c = slot.clone();
    let cancel = w::button(ui, "Cancel", move || {
        if let Some(m) = slot_c.borrow().clone() {
            m.close();
        }
    })
    .variant("quiet");
    let mut form = w::column(ui)
        .gap(unit)
        .child(w::heading(ui, 2, heading))
        .child(w::button(ui, format!("{name} does it"), move || task_mode.set(true)))
        .child(w::button(ui, "Just remind me", move || task_mode.set(false)))
        .child(what.el())
        .child(title.el())
        .child(w::text(ui, "When"))
        .child(w::select(ui, "When", when_labels, choice))
        .child(at.el())
        .child(w::select(ui, "Hours", hour_labels, every_hours))
        .child(once.el())
        .child(day_row(ui, days))
        .child(w::text(ui, "").variant("muted").text({
            let name = name.clone();
            move || {
                if task_mode.get() {
                    format!("{name} does this in the background when your PC has room, and stops to ask before paying, sending or deleting anything.")
                } else {
                    "Reminders run on your PC and use no AI.".into()
                }
            }
        }));
    if can_phone {
        form = form.child(w::check(ui, "Also send it to my phone chat", phone));
    }
    form = form
        .child(w::text(ui, "").variant("error").text(move || err.get()))
        .child(w::row(ui).gap(unit).child(cancel).child(save));
    let slot_w = slot.clone();
    let form = form.child(ui.el("routine-saved").bind(move || {
        if saved.get() {
            if let Some(m) = slot_w.borrow().clone() {
                m.close();
            }
        }
    }));
    let modal = w::modal(ui, heading, form, true);
    *slot.borrow_mut() = Some(modal);
}

fn day_row(ui: &Ui, days: Signal<Vec<String>>) -> El {
    let codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
    let mut row = w::row(ui).gap(ui.theme().spacing.unit);
    for code in codes {
        let code = code.to_string();
        let label = {
            let mut c = code.chars();
            match c.next() {
                Some(ch) => ch.to_uppercase().collect::<String>() + c.as_str(),
                None => code.clone(),
            }
        };
        let days_b = days;
        let code_b = code.clone();
        row = row.child(w::button(ui, label, move || {
            days_b.update(|list| {
                if list.iter().any(|d| d == &code_b) {
                    list.retain(|d| d != &code_b);
                } else {
                    list.push(code_b.clone());
                }
            });
        }));
    }
    row
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}
