//! Chat column: session header, empty monogram, bubbles, composer.

use crate::depth::logic::{self, reaction_label};
use crate::depth::{self, react};
use crate::logo;
use crate::studio::{ChatMessage, Studio};
use crate::work::{self, WorkStep};
use crate::{messages, say};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::core::Vec2;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

/// The chat column: header, feed or empty well, composer.
pub fn well(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let thread = w::column(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(depth::banners::lead(ui, studio.clone()))
        .child(feed(ui, studio.clone()))
        .child(depth::banners::status(ui, studio.clone()));
    w::column(ui)
        .style(StylePatch::new().background(colors.bg))
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(240.0);
            l.height = Length::Percent(1.0);
        })
        .child(header(ui, studio.clone()))
        .child(
            w::row(ui)
                .layout(|l| {
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                    l.min_height = Length::Px(0.0);
                    l.width = Length::Percent(1.0);
                })
                .child(thread)
                .child(depth::panels::side(ui, studio.clone())),
        )
        .child(depth::banners::ticker(ui, studio.clone()))
        .child(depth::banners::library(ui, studio.clone()))
        .child(depth::composer::composer(ui, studio.clone()))
        .child(depth::dialogs::mount(ui, studio))
}

fn header(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let u_title = ui.clone();
    let st_title = studio.clone();
    let u_sub = ui.clone();
    let st_sub = studio.clone();
    let cycle = studio.clone();

    let left = w::column(ui)
        .gap(unit * 0.4)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, "")
                .variant("strong")
                .text(move || {
                    let active = st_title.active.get();
                    let list = st_title.sessions.get();
                    match active
                        .and_then(|id| list.iter().find(|s| s.id == id).map(|s| s.title.clone()))
                    {
                        Some(title) => title,
                        None => messages().text(&u_title, "no-session"),
                    }
                })
                .nowrap(),
        )
        .child(
            w::text(ui, "")
                .variant("muted")
                .text(move || {
                    let partner = messages().text(&u_sub, "partner-name");
                    let provider = st_sub.provider.get();
                    let model = logic::short_model(&st_sub.model.get());
                    let active = st_sub.active.get();
                    let list = st_sub.sessions.get();
                    let count = active
                        .and_then(|id| list.iter().find(|s| s.id == id).map(|s| s.message_count))
                        .unwrap_or(0);
                    let mut sub = partner;
                    if !provider.is_empty() || !model.is_empty() {
                        sub.push_str("  ·  ");
                        if !provider.is_empty() && !model.is_empty() {
                            sub.push_str(&format!("{provider} / {model}"));
                        } else {
                            sub.push_str(if provider.is_empty() {
                                &model
                            } else {
                                &provider
                            });
                        }
                    }
                    if count > 0 {
                        sub.push_str("  ·  ");
                        sub.push_str(&format!("{count} msg{}", if count == 1 { "" } else { "s" }));
                    }
                    sub
                })
                .nowrap(),
        );

    let live_st = studio.clone();
    let mem = studio.clone();
    let skills_st = studio.clone();
    let chips = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(panel_button(ui, studio.clone(), "memory", move || {
            let open = mem.depth.panel.get() == depth::SidePanel::Memory;
            if open {
                mem.depth.panel.set(depth::SidePanel::None);
            } else {
                mem.depth.panel.set(depth::SidePanel::Memory);
                mem.depth.panel_tab.set("memory".into());
                depth::load_memory(&mem);
            }
        }))
        .child(panel_button(ui, studio.clone(), "skills", move || {
            let open = skills_st.depth.panel.get() == depth::SidePanel::Skills;
            if open {
                skills_st.depth.panel.set(depth::SidePanel::None);
            } else {
                skills_st.depth.panel.set(depth::SidePanel::Skills);
                skills_st.depth.panel_tab.set("installed".into());
                depth::load_skills(&skills_st);
            }
        }))
        .child(
            show(ui, move || live_st.streaming.get(), {
                let u = ui.clone();
                let accent = colors.accent;
                move || {
                    w::row(&u)
                        .align(Align::Center)
                        .gap(6.0)
                        .padding(Insets {
                            top: 4.0,
                            end: 8.0,
                            bottom: 4.0,
                            start: 8.0,
                        })
                        .style(
                            StylePatch::new()
                                .background(accent.with_alpha(0.16))
                                .foreground(accent)
                                .radius(999.0),
                        )
                        .child(
                            u.el("live-dot")
                                .style(StylePatch::new().background(accent).radius(999.0))
                                .layout(|l| {
                                    l.width = Length::Px(6.0);
                                    l.height = Length::Px(6.0);
                                    l.min_width = Length::Px(6.0);
                                    l.shrink = 0.0;
                                }),
                        )
                        .child(
                            w::text(&u, "Live")
                                .style(StylePatch::new().foreground(accent).weight(500.0)),
                        )
                }
            })
            .el(),
        )
        .child(
            w::button(
                ui,
                {
                    let cycle = studio.clone();
                    move || cycle.mode.get().label().to_string()
                },
                move || cycle.cycle_mode(),
            )
            .variant("quiet")
            .style(
                StylePatch::new()
                    .radius(ui.theme().radii.medium)
                    .border(colors.line.unwrap_or(colors.muted))
                    .border_width(1.0)
                    .foreground(colors.text),
            )
            .semantics({
                let u = ui.clone();
                let st = studio.clone();
                move || {
                    Semantics::new(Role::Button)
                        .label(format!(
                            "{} — {}",
                            messages().text(&u, "work-mode"),
                            st.mode.get().label()
                        ))
                        .action(Action::Click)
                }
            }),
        );

    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .padding(Insets {
            top: unit * 3.0,
            end: unit * 4.0,
            bottom: unit * 2.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(left)
        .child(chips)
}

fn feed(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let empty =
        move || st.messages.get().is_empty() && st.partial.get().is_empty() && !st.streaming.get();
    let u_empty = ui.clone();
    let u_list = ui.clone();
    let studio_empty = studio.clone();
    let studio_list = studio.clone();
    show(ui, empty, move || {
        empty_well(&u_empty, studio_empty.clone())
    })
    .fallback(move || message_list(&u_list, studio_list.clone()))
    .el()
    .layout(|l| {
        l.width = Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
    })
}

fn message_list(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let items: Signal<Vec<ChatMessage>> = Signal::new(Vec::new());
    let items_out = items.clone();
    let st = studio.clone();
    let col = w::column(ui)
        .gap(unit * 3.0)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 4.0,
            bottom: unit * 2.0,
            start: unit * 4.0,
        })
        .layout(|l| l.width = Length::Percent(1.0));
    let _ = col.clone().bind(move || {
        let mut msgs = st.messages.get();
        let partial = st.partial.get();
        let streaming = st.streaming.get();
        if streaming {
            if let Some(id) = st.live_message_id.get() {
                msgs.retain(|m| m.id != id);
            }
        }
        if !partial.is_empty() || streaming {
            msgs.push(ChatMessage {
                streaming: true,
                ..ChatMessage::plain(
                    "live",
                    "assistant",
                    if partial.is_empty() {
                        "…".to_string()
                    } else {
                        partial
                    },
                )
            });
        }
        items_out.set(msgs);
    });
    let u = ui.clone();
    let studio_rows = studio.clone();
    let col = w::for_each(
        &col,
        items,
        |m| m.id.clone(),
        move |m| {
            if m.id == "live" {
                return live_turn(&u, studio_rows.clone());
            }
            let name = if m.role == "user" {
                studio_rows.user_name.get()
            } else {
                "Remedy".into()
            };
            saved_turn(&u, studio_rows.clone(), m, &name)
        },
    );
    let scroll = w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(col);
    let scroller = follow_scroll(scroll, studio.clone());
    let latest = latest_row(ui, studio);
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(scroller)
        .child(latest)
}

fn empty_well(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let name = studio.greeting_name();
    let u = ui.clone();
    let title = w::text(ui, "")
        .variant("heading")
        .text(move || messages().text(&u, "ready-named").replace("{name}", &name));
    let mut starters = w::row(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    for (label_id, prompt_id) in [
        ("starter-help", "starter-help-prompt"),
        ("starter-explore", "starter-explore-prompt"),
        ("starter-review", "starter-review-prompt"),
        ("starter-plan", "starter-plan-prompt"),
    ] {
        let u = ui.clone();
        let s = studio.clone();
        let u2 = ui.clone();
        starters = starters.child(
            w::button(
                ui,
                {
                    let u = u.clone();
                    move || messages().text(&u, label_id)
                },
                move || s.send(messages().text(&u2, prompt_id)),
            )
            .variant("quiet")
            .style(
                StylePatch::new()
                    .radius(ui.theme().radii.medium)
                    .border(ui.theme().colors.line.unwrap_or(ui.theme().colors.muted))
                    .border_width(1.0),
            ),
        );
    }
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 8.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(logo::empty_mark(ui))
        .child(title)
        .child(say(ui, "ready-hint", "muted").layout(|l| {
            l.max_width = Length::Px(520.0);
            l.width = Length::Percent(1.0);
        }))
        .child(starters)
        .child(say(ui, "ready-keys", "muted").layout(|l| {
            l.max_width = Length::Px(520.0);
            l.width = Length::Percent(1.0);
        }))
}

fn mid_turn_beats(msg: &ChatMessage) -> Vec<String> {
    msg.steps
        .iter()
        .filter_map(|s| s.narration.as_deref())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

fn sealed_bubble(ui: &Ui, studio: Studio, text: &str, name: &str) -> El {
    let body = depth::blocks(ui, text, true, {
        let st = studio;
        move |src| st.depth.show_image(src)
    });
    message_row(ui, body, false, false, name)
}

fn saved_turn(ui: &Ui, studio: Studio, msg: &ChatMessage, name: &str) -> El {
    let user = msg.role == "user";
    let system = msg.role == "system";
    let assistant = msg.role == "assistant";
    let beats = mid_turn_beats(msg);
    let show_body = !msg.content.trim().is_empty();
    let show_meta = assistant
        && work::visible(
            &work::summarize(&msg.steps),
            !msg.thinking.trim().is_empty(),
        );
    let unit = ui.theme().spacing.unit;
    let bubble_gap = if beats.is_empty() { 4.0 } else { unit * 3.0 };
    let mut col = w::column(ui)
        .gap(if show_meta && beats.is_empty() {
            2.0
        } else {
            bubble_gap
        })
        .layout(|l| l.width = Length::Percent(1.0));
    if show_meta {
        let steps = msg.steps.clone();
        let thinking = msg.thinking.clone();
        col = col.child(work_meta(
            ui,
            move || steps.clone(),
            move || thinking.clone(),
            false,
        ));
    }
    for beat in &beats {
        col = col.child(sealed_bubble(ui, studio.clone(), beat, name));
    }
    if show_body {
        let body = depth::blocks(ui, &msg.content, assistant, {
            let st = studio.clone();
            move |src| st.depth.show_image(src)
        });
        col = col.child(message_row(ui, body, user, system, name));
    }
    if assistant && (show_body || !beats.is_empty()) {
        col = col.child(reactions(ui, studio, msg.id.clone()));
    } else if user {
        col = col.child(agent_reaction(ui, studio, msg.id.clone()));
    }
    col
}

fn live_turn(ui: &Ui, studio: Studio) -> El {
    let steps = {
        let s = studio.clone();
        move || s.live_steps.get()
    };
    let thinking = {
        let s = studio.clone();
        move || s.live_thinking.get()
    };
    let unit = ui.theme().spacing.unit;
    let beats: Signal<Vec<(usize, String)>> = Signal::new(Vec::new());
    let beats_out = beats.clone();
    let st_beats = studio.clone();
    let sealed = w::column(ui)
        .gap(unit * 3.0)
        .layout(|l| l.width = Length::Percent(1.0));
    let _ = sealed.clone().bind(move || {
        let next: Vec<(usize, String)> =
            work::narration_beats(&st_beats.partial.get(), &st_beats.live_breaks.get())
                .into_iter()
                .enumerate()
                .collect();
        if beats_out.get() != next {
            beats_out.set(next);
        }
    });
    let u_beat = ui.clone();
    let st_beat = studio.clone();
    let sealed = w::for_each(
        &sealed,
        beats,
        |b| format!("{}:{}", b.0, b.1),
        move |(_i, beat)| sealed_bubble(&u_beat, st_beat.clone(), &beat, "Remedy"),
    );
    let st_react_when = studio.clone();
    let u_react = ui.clone();
    let st_react = studio.clone();
    let sealed = sealed.child(
        show(
            ui,
            move || {
                let has_beats = !work::narration_beats(
                    &st_react_when.partial.get(),
                    &st_react_when.live_breaks.get(),
                )
                .is_empty();
                has_beats
                    && st_react_when
                        .live_message_id
                        .get()
                        .is_some_and(|id| !id.is_empty())
            },
            move || {
                let id = st_react.live_message_id.get().unwrap_or_default();
                reactions(&u_react, st_react.clone(), id)
            },
        )
        .el(),
    );
    let u = ui.clone();
    let s_card = studio.clone();
    let s_body = studio.clone();
    w::column(ui)
        .gap(unit * 3.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(work_meta(ui, steps, thinking, true))
        .child(sealed)
        .child(
            show(
                ui,
                move || live_shows_bubble(&s_card),
                move || {
                    let s = s_body.clone();
                    let u = u.clone();
                    message_row(
                        &u,
                        live_body(&u, move || {
                            let reply = work::reply_after(&s.partial.get(), &s.live_breaks.get());
                            if reply.trim().is_empty() {
                                "…".to_string()
                            } else {
                                reply
                            }
                        }),
                        false,
                        false,
                        "Remedy",
                    )
                },
            )
            .el(),
        )
}

fn live_shows_bubble(studio: &Studio) -> bool {
    let reply = work::reply_after(&studio.partial.get(), &studio.live_breaks.get());
    if !reply.trim().is_empty() {
        return true;
    }
    let sum = work::summarize(&studio.live_steps.get());
    !work::visible(&sum, !studio.live_thinking.get().trim().is_empty())
}

/// "Worked · N steps · N errors" above an assistant reply. Hidden for a plain chat.
fn work_meta(
    ui: &Ui,
    steps: impl Fn() -> Vec<WorkStep> + Clone + 'static,
    thinking: impl Fn() -> String + Clone + 'static,
    live: bool,
) -> El {
    let steps_when = steps.clone();
    let think_when = thinking.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            let sum = work::summarize(&steps_when());
            work::visible(&sum, !think_when().trim().is_empty())
        },
        move || work_meta_row(&u, steps.clone(), thinking.clone(), live),
    )
    .el()
}

fn work_meta_row(
    ui: &Ui,
    steps: impl Fn() -> Vec<WorkStep> + Clone + 'static,
    thinking: impl Fn() -> String + Clone + 'static,
    live: bool,
) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let dot_color = if live { colors.accent } else { colors.muted };
    let steps_title = steps.clone();
    let think_title = thinking.clone();
    let u_title = ui.clone();
    let title = w::text(ui, "")
        .text(move || {
            let sum = work::summarize(&steps_title());
            let thought = !think_title().trim().is_empty();
            messages().text(&u_title, work::title_key(&sum, thought, live))
        })
        .nowrap()
        .style(
            StylePatch::new()
                .foreground(colors.text)
                .text_size(11.0)
                .weight(600.0)
                .mono(true),
        );
    let steps_n = steps.clone();
    let u_steps = ui.clone();
    let muted = colors.muted;
    let step_part = show(ui, move || work::summarize(&steps_n()).total > 0, {
        let steps = steps.clone();
        move || count_chip(&u_steps, steps.clone(), muted, false)
    })
    .el();
    let steps_err = steps.clone();
    let u_err = ui.clone();
    let danger = colors.danger;
    let error_part = show(ui, move || work::summarize(&steps_err()).errors > 0, {
        let steps = steps.clone();
        move || count_chip(&u_err, steps.clone(), danger, true)
    })
    .el();
    let steps_sem = steps;
    let think_sem = thinking;
    let u_sem = ui.clone();
    let dot = ui
        .el("work-dot")
        .layout(|l| {
            l.width = Length::Px(6.0);
            l.height = Length::Px(6.0);
            l.min_width = Length::Px(6.0);
            l.min_height = Length::Px(6.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().background(dot_color).radius(3.0));
    w::row(ui)
        .layout(|l| l.width = Length::Percent(1.0))
        .padding(Insets {
            top: 0.0,
            end: 0.0,
            bottom: 0.0,
            start: 28.0 + unit,
        })
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(6.0)
                .padding(Insets {
                    top: 2.0,
                    end: 4.0,
                    bottom: 2.0,
                    start: 10.0,
                })
                .layout(|l| l.width = Length::Percent(1.0))
                .style(StylePatch::new().border_side(
                    BorderEdge::Start,
                    BorderSide::new(2.0, colors.accent.with_alpha(0.30)),
                ))
                .semantics(move || {
                    let sum = work::summarize(&steps_sem());
                    let thought = !think_sem().trim().is_empty();
                    let mut label = messages().text(&u_sem, work::title_key(&sum, thought, live));
                    if sum.total > 0 {
                        label.push_str(&format!(
                            ", {} {}",
                            sum.total,
                            messages().text(
                                &u_sem,
                                if sum.total == 1 {
                                    "work-step"
                                } else {
                                    "work-steps"
                                }
                            )
                        ));
                    }
                    if sum.errors > 0 {
                        label.push_str(&format!(
                            ", {} {}",
                            sum.errors,
                            messages().text(
                                &u_sem,
                                if sum.errors == 1 {
                                    "work-error"
                                } else {
                                    "work-errors"
                                }
                            )
                        ));
                    }
                    Semantics::new(Role::Label).label(label)
                })
                .child(dot)
                .child(title)
                .child(step_part)
                .child(error_part),
        )
}

fn count_chip(
    ui: &Ui,
    steps: impl Fn() -> Vec<WorkStep> + 'static,
    color: remedy_ui::core::Color,
    errors: bool,
) -> El {
    let u = ui.clone();
    w::text(ui, "")
        .text(move || {
            let sum = work::summarize(&steps());
            let n = if errors { sum.errors } else { sum.total };
            let key = if errors {
                if n == 1 { "work-error" } else { "work-errors" }
            } else if n == 1 {
                "work-step"
            } else {
                "work-steps"
            };
            format!("· {n} {}", messages().text(&u, key))
        })
        .nowrap()
        .style(
            StylePatch::new()
                .foreground(color)
                .text_size(11.0)
                .weight(500.0)
                .mono(true),
        )
}

fn message_row(ui: &Ui, body: El, user: bool, system: bool, name: &str) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let (bg, fg, border) = if user {
        (colors.accent, colors.on_accent, colors.accent)
    } else if system {
        (
            colors.card,
            colors.danger,
            colors
                .danger
                .mix_oklab(colors.line.unwrap_or(colors.muted), 0.4),
        )
    } else {
        (
            colors.card,
            colors.text,
            colors
                .line
                .unwrap_or(colors.muted.mix_oklab(colors.card, 0.4)),
        )
    };
    let radius = ui.theme().radii.large;
    let bubble = w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(|l| {
            l.max_width = Length::Px(640.0);
            l.shrink = 1.0;
        })
        .style(
            StylePatch::new()
                .background(bg)
                .foreground(fg)
                .radius(radius)
                .border(border)
                .border_width(1.0),
        )
        .child(body);
    let avatar_color = if user { colors.accent } else { colors.card };
    let avatar_name = if user { name } else { "Remedy" };
    let row = w::row(ui)
        .align(Align::End)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    if user {
        row.justify(Justify::End).child(bubble).child(w::avatar(
            ui,
            avatar_name,
            28.0,
            avatar_color,
        ))
    } else {
        row.justify(Justify::Start)
            .child(w::avatar(ui, avatar_name, 28.0, avatar_color))
            .child(bubble)
    }
}

fn live_body(ui: &Ui, text: impl Fn() -> String + 'static) -> El {
    let chip = ui
        .theme()
        .colors
        .accent
        .mix_oklab(ui.theme().colors.bg, 0.72);
    ui.el("bubble-body")
        .text(move || depth::inline_rich(&text(), chip))
        .layout(|l| l.width = Length::Percent(1.0))
}

fn panel_button(
    ui: &Ui,
    _studio: Studio,
    label: &'static str,
    on_press: impl Fn() + 'static,
) -> El {
    let u = ui.clone();
    w::button(ui, move || messages().text(&u, label), on_press)
        .variant("quiet")
        .style(
            StylePatch::new()
                .weight(500.0)
                .foreground(ui.theme().colors.text),
        )
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Button)
                    .label(messages().text(&u, label))
                    .action(Action::Click)
            }
        })
}

fn reaction_is_on(studio: &Studio, id: &str, emoji: &str) -> bool {
    if let Some(m) = studio.messages.get().iter().find(|m| m.id == id) {
        return m.user_reaction == emoji;
    }
    studio.live_message_id.get().as_deref() == Some(id) && studio.live_user_reaction.get() == emoji
}

fn reactions(ui: &Ui, studio: Studio, id: String) -> El {
    let mut row = w::row(ui).gap(4.0).align(Align::Center);
    for (emoji, _) in logic::REACTIONS {
        let emoji = (*emoji).to_string();
        let st_press = studio.clone();
        let id_press = id.clone();
        let em_press = emoji.clone();
        let accent = ui.theme().colors.accent;
        let muted = ui.theme().colors.muted;
        let st_style = studio.clone();
        let id_style = id.clone();
        let em_style = emoji.clone();
        let em_name = emoji.clone();
        row = row.child(
            w::button(ui, emoji, move || react(&st_press, &id_press, &em_press))
                .variant("quiet")
                .style(move || {
                    let on = reaction_is_on(&st_style, &id_style, &em_style);
                    StylePatch::new().foreground(if on { accent } else { muted })
                })
                .semantics(move || {
                    Semantics::new(Role::Button)
                        .label(reaction_label(&em_name).to_string())
                        .action(Action::Click)
                }),
        );
    }
    row
}

fn agent_reaction(ui: &Ui, studio: Studio, id: String) -> El {
    let st = studio.clone();
    let id_when = id.clone();
    let u = ui.clone();
    show(
        ui,
        move || {
            st.messages
                .get()
                .iter()
                .find(|m| m.id == id_when)
                .is_some_and(|m| !m.agent_reaction.is_empty())
        },
        move || {
            let st = studio.clone();
            let id = id.clone();
            w::text(&u, "").text(move || {
                st.messages
                    .get()
                    .iter()
                    .find(|m| m.id == id)
                    .map(|m| m.agent_reaction.clone())
                    .unwrap_or_default()
            })
        },
    )
    .el()
}

fn follow_scroll(scroll: El, studio: Studio) -> El {
    let last_y = Signal::new(0.0f32);
    let stuck = studio.depth.stuck.clone();
    let scroller_key = scroll.clone();
    let scroller_bind = scroll.clone();
    let st_key = studio.clone();
    scroll
        .on_scroll(move |off| {
            let y = off.y;
            if y + 2.0 < last_y.get() {
                stuck.set(false);
            }
            last_y.set(y);
        })
        .on_key(move |e| {
            if !e.pressed {
                return false;
            }
            match e.key {
                Key::Home | Key::PageUp | Key::ArrowUp => {
                    st_key.depth.stuck.set(false);
                    false
                }
                Key::End => {
                    st_key.depth.stuck.set(true);
                    scroller_key.scroll_to(Vec2::new(0.0, 1.0e9));
                    true
                }
                _ => false,
            }
        })
        .bind({
            let scroller = scroller_bind;
            let st = studio.clone();
            move || {
                let _ = st.messages.get();
                let _ = st.partial.get();
                let _ = st.depth.tick.get();
                if st.depth.stuck.get() {
                    scroller.scroll_to(Vec2::new(0.0, 1.0e9));
                }
            }
        })
}

fn latest_row(ui: &Ui, studio: Studio) -> El {
    let st_when = studio.clone();
    let st_go = studio.clone();
    let u = ui.clone();
    let u_btn = ui.clone();
    show(
        ui,
        move || {
            !st_when.depth.stuck.get()
                && (!st_when.messages.get().is_empty() || st_when.streaming.get())
        },
        move || {
            let st = st_go.clone();
            w::button(
                &u_btn,
                {
                    let u = u.clone();
                    move || messages().text(&u, "latest")
                },
                move || {
                    st.depth.stuck.set(true);
                    st.depth.tick.update(|n| *n = n.wrapping_add(1));
                },
            )
            .variant("quiet")
        },
    )
    .el()
}
