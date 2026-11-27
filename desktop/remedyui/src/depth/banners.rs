//! Approval, plan, life, library, cost, todos, and the live trace.

use super::logic::{self, show_life_banner};
use super::{
    approve_plan, background_title, dismiss_library, focused_steps, install_library,
    refresh_banners, request_plan_changes, resolve_approval, send_life, show_plan,
    visible_approval,
};
use super::Approval;
use crate::messages;
use crate::tokens;
use crate::studio::{Studio, WorkMode};
use crate::work::WorkStep;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

/// Approval, life-task, and plan, in that order, above the feed.
pub fn lead(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .padding(Insets {
            top: 0.0,
            end: unit * 4.0,
            bottom: 0.0,
            start: unit * 4.0,
        })
        .child(approval(ui, studio.clone()))
        .child(life(ui, studio.clone()))
        .child(plan(ui, studio, false))
}

/// Remote-busy, background job, todos, progress, and the live trace.
pub fn status(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .padding(Insets {
            top: 0.0,
            end: unit * 4.0,
            bottom: 0.0,
            start: unit * 4.0,
        })
        .child(remote(ui, studio.clone()))
        .child(background(ui, studio.clone()))
        // BUILD todos render in the Actions pane, not under the chat bubbles.
        .child(progress(ui, studio.clone()))
        .child(trace(ui, studio))
}

/// Run and session token counts, just above the composer.
pub fn ticker(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            if st.depth.hide_ticker.get() {
                return false;
            }
            let run = st.depth.usage_run.get();
            let session = st.depth.usage_session.get();
            run.input + run.output + session.input + session.output > 0
        },
        move || ticker_row(&u, studio.clone()),
    )
    .el()
    .layout(|l| l.width = Length::Percent(1.0))
}

/// The library suggestion chip under the ticker.
pub fn library(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || st.depth.library.get().is_some(),
        move || library_card(&u, studio.clone()),
    )
    .el()
}

fn approval(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            let sid = st.active.get().unwrap_or_default();
            visible_approval(&st.depth, &sid).is_some()
        },
        move || approval_card(&u, studio.clone()),
    )
    .el()
}

fn approval_card(ui: &Ui, studio: Studio) -> El {
    let sid = studio.active.get().unwrap_or_default();
    let Some(card) = visible_approval(&studio.depth, &sid) else {
        return w::column(ui);
    };
    let summary = if card.summary.is_empty() {
        card.tool.clone()
    } else {
        card.summary.clone()
    };
    card_shell(ui, &messages().text(ui, "approval-title"), &summary).child(
        w::row(ui)
            .gap(8.0)
            .child(scope_button(
                ui,
                studio.clone(),
                &card,
                "approval-once",
                "once",
                true,
            ))
            .child(scope_button(
                ui,
                studio.clone(),
                &card,
                "approval-session",
                "session",
                true,
            ))
            .child(scope_button(
                ui,
                studio.clone(),
                &card,
                "approval-always",
                "always",
                true,
            ))
            .child(scope_button(
                ui,
                studio,
                &card,
                "approval-deny",
                "once",
                false,
            )),
    )
}

fn scope_button(
    ui: &Ui,
    studio: Studio,
    card: &Approval,
    label: &'static str,
    scope: &'static str,
    approve: bool,
) -> El {
    let id = card.id.clone();
    let u = ui.clone();
    w::button(
        ui,
        move || messages().text(&u, label),
        move || resolve_approval(&studio, &id, approve, scope),
    )
    .variant("quiet")
    .style(if approve && scope == "once" {
        tokens::primary_button(&ui.theme().colors)
    } else {
        StylePatch::new()
    })
}

pub(crate) fn plan(ui: &Ui, studio: Studio, for_dock: bool) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            let mode = st.mode.get();
            let visible = st
                .depth
                .plan
                .get()
                .as_ref()
                .is_some_and(|card| show_plan(card, mode));
            visible && (for_dock == (mode == WorkMode::Plan))
        },
        move || plan_card(&u, studio.clone()),
    )
    .el()
}

pub(crate) fn plan_empty(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            st.mode.get() == WorkMode::Plan
                && !st
                    .depth
                    .plan
                    .get()
                    .as_ref()
                    .is_some_and(|card| show_plan(card, WorkMode::Plan))
        },
        move || {
            let colors = u.theme().colors.clone();
            w::column(&u)
                .gap(8.0)
                .padding(Insets::all(12.0))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .style(
                    StylePatch::new()
                        .background(colors.card)
                        .border(colors.line.unwrap_or(colors.muted).with_alpha(0.5))
                        .border_width(1.0)
                        .radius(u.theme().radii.medium),
                )
                .child(
                    w::text(&u, {
                        let u = u.clone();
                        move || messages().text(&u, "plan-empty")
                    })
                    .variant("strong")
                    .layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                    }),
                )
                .child(
                    w::text(&u, {
                        let u = u.clone();
                        move || messages().text(&u, "plan-empty-hint")
                    })
                    .variant("muted")
                    .layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                    }),
                )
        },
    )
    .el()
}

fn plan_card(ui: &Ui, studio: Studio) -> El {
    let Some(card) = studio.depth.plan.get() else {
        return w::column(ui);
    };
    let title = messages().text(ui, "plan-title");
    let body = if card.goal.is_empty() {
        messages().text(ui, "plan-ready")
    } else {
        card.goal.clone()
    };
    let count = format!("{}/{}", card.done, card.total);
    let st_ok = studio.clone();
    let st_no = studio.clone();
    let st_re = studio.clone();
    let u = ui.clone();
    let mut card_el = card_shell(ui, &title, &body).child(w::text(ui, &count).variant("muted"));
    if !card.steps.is_empty() {
        let mut steps = w::column(ui).gap(4.0).layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
        for (i, (title, reason)) in card.steps.iter().enumerate() {
            let line = if reason.is_empty() {
                format!("{}. {title}", i + 1)
            } else {
                format!("{}. {title} — {reason}", i + 1)
            };
            steps = steps.child(w::text(ui, line).variant("muted").layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            }));
        }
        card_el = card_el.child(steps);
    }
    card_el.child(
        w::row(ui)
            .gap(8.0)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.wrap = true;
                l.min_width = Length::Px(0.0);
            })
            .child(
                w::button(
                    ui,
                    move || messages().text(&u, "plan-approve"),
                    move || approve_plan(&st_ok),
                )
                .variant("quiet")
                .style(tokens::primary_button(&ui.theme().colors)),
            )
            .child(
                w::button(
                    ui,
                    {
                        let u = ui.clone();
                        move || messages().text(&u, "plan-changes")
                    },
                    move || request_plan_changes(&st_no),
                )
                .variant("quiet"),
            )
            .child(
                w::button(
                    ui,
                    {
                        let u = ui.clone();
                        move || messages().text(&u, "plan-refresh")
                    },
                    move || refresh_banners(&st_re),
                )
                .variant("quiet"),
            ),
    )
}

fn life(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            st.depth.life.get().is_some_and(|card| {
                show_life_banner(&card.status, &card.spoken, card.steps.len(), card.approval)
            })
        },
        move || life_card(&u, studio.clone()),
    )
    .el()
}

fn life_card(ui: &Ui, studio: Studio) -> El {
    let Some(card) = studio.depth.life.get() else {
        return w::column(ui);
    };
    let line = logic::life_headline(&card.spoken, &card.goal, &card.title, card.step, card.total);
    let yes = word_button(ui, studio.clone(), "life-yes", "yes");
    let no = word_button(ui, studio.clone(), "life-no", "no");
    let explain = word_button(ui, studio, "life-explain", "explain");
    card_shell(ui, &messages().text(ui, "life-title"), &line)
        .child(w::row(ui).gap(8.0).child(yes).child(no).child(explain))
}

fn word_button(ui: &Ui, studio: Studio, label: &'static str, word: &'static str) -> El {
    let u = ui.clone();
    w::button(
        ui,
        move || messages().text(&u, label),
        move || send_life(&studio, word),
    )
    .variant("quiet")
    .style(if word == "yes" {
        tokens::primary_button(&ui.theme().colors)
    } else {
        StylePatch::new()
    })
}

fn library_card(ui: &Ui, studio: Studio) -> El {
    let Some(chip) = studio.depth.library.get() else {
        return w::column(ui);
    };
    let body = if chip.reason.is_empty() {
        chip.name.clone()
    } else {
        format!("{} — {}", chip.name, chip.reason)
    };
    let st_in = studio.clone();
    let st_out = studio.clone();
    let u = ui.clone();
    card_shell(ui, &messages().text(ui, "library-title"), &body).child(
        w::row(ui)
            .gap(8.0)
            .child(
                w::button(
                    ui,
                    move || messages().text(&u, "library-install"),
                    move || install_library(&st_in),
                )
                .variant("quiet")
                .style(tokens::primary_button(&ui.theme().colors)),
            )
            .child(
                w::button(
                    ui,
                    {
                        let u = ui.clone();
                        move || messages().text(&u, "library-dismiss")
                    },
                    move || dismiss_library(&st_out),
                )
                .variant("quiet"),
            ),
    )
}

fn remote(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || st.depth.remote_busy.get(),
        move || {
            let label = u.clone();
            w::text(&u, "")
                .variant("muted")
                .text(move || messages().text(&label, "remote-busy"))
        },
    )
    .el()
}

fn background(ui: &Ui, studio: Studio) -> El {
    let st_when = studio.clone();
    let st_body = studio;
    let u = ui.clone();
    show(
        ui,
        move || background_title(&st_when).is_some(),
        move || {
            let st = st_body.clone();
            let label = u.clone();
            w::text(&u, "").variant("muted").text(move || {
                let title = background_title(&st).unwrap_or_default();
                format!("{} {title}", messages().text(&label, "background-job"))
            })
        },
    )
    .el()
}

fn todos(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || !st.depth.todos.get().is_empty(),
        move || {
            let title = messages().text(&u, "todos-title");
            let col = w::column(&u)
                .gap(2.0)
                .child(w::text(&u, title).variant("strong"));
            let rows = studio.depth.todos.clone();
            w::for_each(
                &col,
                rows,
                |item: &super::TodoItem| {
                    if item.id.is_empty() {
                        item.content.clone()
                    } else {
                        item.id.clone()
                    }
                },
                {
                    let u = u.clone();
                    move |item| {
                        let line = format!("{}  {}", mark(&item.status), item.content);
                        w::text(&u, line).variant("muted")
                    }
                },
            )
        },
    )
    .el()
}

fn mark(status: &str) -> &'static str {
    match status {
        "done" | "completed" | "complete" => "✓",
        "doing" | "in_progress" | "active" => "→",
        _ => "·",
    }
}

fn progress(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || st.depth.progress.get().is_some(),
        move || {
            let label = messages().text(&u, "progress-label");
            w::progress(&u, label, studio.depth.progress.clone())
        },
    )
    .el()
}

fn trace(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    let steps = studio.live_steps.clone();
    show(
        ui,
        move || !focused_steps(&st).is_empty(),
        move || {
            let open = studio.depth.trace_open.clone();
            let toggle = open.clone();
            let label = u.clone();
            let colors = u.theme().colors.clone();
            let line = colors.line.unwrap_or(colors.muted);
            let radius = u.theme().radii.medium;
            let open_style = open.clone();
            let button = w::button(
                &u,
                move || messages().text(&label, "trace"),
                move || toggle.update(|v| *v = !*v),
            )
            .variant("quiet")
            .style({
                let colors = colors.clone();
                move || {
                    let on = open_style.get();
                    StylePatch::new()
                        .weight(500.0)
                        .foreground(if on { colors.text } else { colors.muted })
                        .border(line)
                        .border_width(1.0)
                        .radius(radius)
                }
            })
            .semantics({
                let u = u.clone();
                let open = open.clone();
                move || {
                    Semantics::new(Role::Button)
                        .label(messages().text(&u, "trace"))
                        .expanded(open.get())
                        .action(Action::Click)
                }
            });
            let lines_ui = u.clone();
            let rows = steps.clone();
            w::column(&u)
                .gap(6.0)
                .child(w::row(&u).child(button.layout(|l| {
                    l.shrink = 0.0;
                    l.grow = 0.0;
                })))
                .child(
                    show(
                        &u,
                        move || open.get(),
                        move || {
                            let host = w::column(&lines_ui).gap(2.0);
                            w::for_each(&host, rows.clone(), step_key, {
                                let u = lines_ui.clone();
                                move |step| {
                                    let line = logic::trace_lines(std::slice::from_ref(step))
                                        .into_iter()
                                        .next()
                                        .map(|(name, state)| format!("{name}  {state}"))
                                        .unwrap_or_default();
                                    w::text(&u, line).variant("muted")
                                }
                            })
                        },
                    )
                    .el(),
                )
        },
    )
    .el()
}

fn step_key(step: &WorkStep) -> String {
    if step.id.is_empty() {
        step.name.clone()
    } else {
        format!("{}|{}", step.id, step.name)
    }
}

fn ticker_row(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let run = studio.depth.usage_run.get();
    let session = studio.depth.usage_session.get();
    let run_line = usage_line(
        &messages().text(ui, "cost-run"),
        run.input,
        run.output,
    );
    let session_line = usage_line(
        &messages().text(ui, "cost-session"),
        session.input,
        session.output,
    );
    let st_hide = studio.clone();
    let u = ui.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: 0.0,
            end: unit * 4.0,
            bottom: 0.0,
            start: unit * 4.0,
        })
        .child(w::text(ui, run_line).variant("muted"))
        .child(w::text(ui, session_line).variant("muted"))
        .child(
            w::button(
                ui,
                move || messages().text(&u, "cost-hide"),
                move || st_hide.depth.hide_ticker.set(true),
            )
            .variant("quiet"),
        )
}

/// Tokens in and out. There is no price beside them: Remedy has no price
/// list, and a figure of her own would be a guess.
fn usage_line(label: &str, input: u64, output: u64) -> String {
    format!("{label}  {} in · {} out", compact(input), compact(output))
}

fn compact(n: u64) -> String {
    if n < 1000 {
        n.to_string()
    } else if n < 1_000_000 {
        format!("{:.1}k", n as f64 / 1000.0)
    } else {
        format!("{:.1}m", n as f64 / 1_000_000.0)
    }
}

fn card_shell(ui: &Ui, title: &str, body: &str) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let mut col = w::column(ui)
        .gap(unit)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| l.width = Length::Percent(1.0))
        .style(
            StylePatch::new()
                .background(colors.card)
                .radius(ui.theme().radii.medium)
                .border(line)
                .border_width(1.0),
        )
        .child(w::text(ui, title).variant("strong"));
    if !body.is_empty() {
        col = col.child(w::text(ui, body));
    }
    col
}
