//! Chat column: session header, empty monogram, bubbles, composer.

use std::cell::RefCell;
use std::rc::Rc;

use crate::depth::logic::{self, reaction_label};
use crate::depth::{self, react};
use crate::icons::{glyph, reaction_glyph, Glyph};
use crate::logo;
use crate::runtime;
use crate::studio::{ChatMessage, Studio, WorkMode};
use crate::tokens;
use crate::work::{self, WorkStep};
use crate::{messages, say, time};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::core::Vec2;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, States, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

/// The chat well: session header, the two-pane workspace, composer.
pub fn well(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    w::column(ui)
        .style(StylePatch::new().background(tokens::surface_base(&colors)))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
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
                .child({
                    let empty_st = studio.clone();
                    let empty = move || {
                        // Plan mode always shows the plan list, even on a new session.
                        empty_st.mode.get() != WorkMode::Plan
                            && empty_st.messages.get().is_empty()
                            && empty_st.partial.get().is_empty()
                            && !empty_st.streaming.get()
                    };
                    show(ui, empty, {
                        let u = ui.clone();
                        let studio = studio.clone();
                        move || home_surface(&u, studio.clone())
                    })
                    .fallback({
                        let u = ui.clone();
                        let studio = studio.clone();
                        move || studio_workspace(&u, studio.clone())
                    })
                    .el()
                    .layout(|l| {
                        l.grow = 1.0;
                        l.basis = Length::Px(0.0);
                        l.min_width = Length::Px(0.0);
                        l.min_height = Length::Px(0.0);
                        l.width = Length::Percent(1.0);
                        l.height = Length::Percent(1.0);
                    })
                })
                .child(depth::panels::side(ui, studio.clone(), depth::SidePanel::Memory))
                .child(depth::panels::side(ui, studio.clone(), depth::SidePanel::Skills)),
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
    let left = w::column(ui)
        .gap(unit * 0.4)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            // A fixed basis: the chips wrap below once the title has under 160 px.
            l.basis = Length::Px(160.0);
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
                .truncate()
                .layout(|l| l.min_width = Length::Px(0.0))
                .style(tokens::typo(tokens::TITLE).foreground(colors.text)),
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
                .truncate()
                .layout(|l| l.min_width = Length::Px(0.0))
                .style(tokens::typo(tokens::META).foreground(colors.muted)),
        );

    let live_st = studio.clone();
    let mem = studio.clone();
    let skills_st = studio.clone();
    let chips = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(panel_button(
            ui,
            studio.clone(),
            "memory",
            "memory-tip",
            {
                let st = studio.clone();
                move || st.depth.panel.get() == depth::SidePanel::Memory
            },
            move || {
                let open = mem.depth.panel.get() == depth::SidePanel::Memory;
                if open {
                    mem.depth.panel.set(depth::SidePanel::None);
                } else {
                    mem.depth.panel.set(depth::SidePanel::Memory);
                    mem.depth.panel_tab.set("memory".into());
                    depth::load_memory(&mem);
                }
            },
        ))
        .child(panel_button(
            ui,
            studio.clone(),
            "skills",
            "skills-tip",
            {
                let st = studio.clone();
                move || st.depth.panel.get() == depth::SidePanel::Skills
            },
            move || {
                let open = skills_st.depth.panel.get() == depth::SidePanel::Skills;
                if open {
                    skills_st.depth.panel.set(depth::SidePanel::None);
                } else {
                    skills_st.depth.panel.set(depth::SidePanel::Skills);
                    skills_st.depth.panel_tab.set("installed".into());
                    depth::load_skills(&skills_st);
                }
            },
        ))
        .child(
            show(ui, move || live_st.streaming.get(), {
                let u = ui.clone();
                let accent = colors.accent;
                move || {
                    w::row(&u)
                        .align(Align::Center)
                        .gap(crate::tokens::space() * 1.5)
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
                                .radius(crate::tokens::corner(crate::tokens::Corner::Pill)),
                        )
                        .child(
                            u.el("live-dot")
                                .style(StylePatch::new().background(accent).radius(crate::tokens::corner(crate::tokens::Corner::Pill)))
                                .layout(|l| {
                                    l.width = Length::Px(6.0);
                                    l.height = Length::Px(6.0);
                                    l.min_width = Length::Px(6.0);
                                    l.shrink = 0.0;
                                }),
                        )
                        .child(
                            w::text(&u, "Live")
                                .style(tokens::typo(tokens::META).foreground(accent).weight(620.0)),
                        )
                }
            })
            .el(),
        )
        .child(mode_switcher(ui, studio.clone()));

    let chips = chips.layout(|l| {
        l.shrink = 0.0;
        // Never wider than the pane: the last chips wrap rather than clip.
        l.max_width = Length::Percent(1.0);
        l.wrap = true;
    });
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit)
        .padding(Insets {
            top: tokens::gutter(),
            end: tokens::gutter(),
            bottom: unit * 3.0,
            start: tokens::gutter(),
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.shrink = 1.0;
            // Narrow panes: chips drop to their own line instead of clipping.
            l.wrap = true;
        })
        .clip()
        .child(left)
        .child(chips)
}

pub(crate) fn feed(ui: &Ui, studio: Studio) -> El {
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
        .gap(tokens::space() * 2.0)
        .padding(Insets {
            top: unit * 2.0,
            end: tokens::gutter(),
            bottom: unit * 2.0,
            start: tokens::gutter(),
        })
        .layout(|l| l.width = Length::Percent(1.0));
    // Whether she has said anything yet in the live turn. The list below reads
    // this, not the text itself: reading the text made every streamed word
    // copy and re-diff the whole conversation.
    let speaking = Signal::new(false);
    let _ = col.clone().bind({
        let (st, speaking) = (studio.clone(), speaking.clone());
        move || speaking.set_if_changed(!st.partial.with(|p| p.is_empty()))
    });
    let earlier = Signal::new(0usize);
    let earlier_out = earlier.clone();
    let _ = col.clone().bind(move || {
        let (mut msgs, hidden) = st.recent_messages();
        earlier_out.set_if_changed(hidden);
        if hidden > 0 {
            // One row standing for everything above.
            msgs.insert(0, ChatMessage::plain("earlier", "system", ""));
        }
        let has_partial = speaking.get();
        let streaming = st.streaming.get();
        if streaming {
            if let Some(id) = st.live_message_id.get() {
                msgs.retain(|m| m.id != id);
            }
        }
        if has_partial || streaming {
            // Read without subscribing: the job record changes with every
            // word, and only its start time is wanted here.
            let started = remedy_ui::reactive::untrack(|| {
                st.active
                    .get()
                    .and_then(|sid| st.depth.job(&sid))
                    // A pinned clock (design shots) stands in for the real start.
                    .map(|j| time::rfc3339(if st.clock.get().is_some() { st.now() } else { j.started_secs }))
            });
            // The live row draws from the live text itself (`live_turn`).
            msgs.push(ChatMessage {
                streaming: true,
                created_at: started,
                ..ChatMessage::plain("live", "assistant", "…")
            });
        }
        // Sent while she works and not taken in yet: under her live reply,
        // where the conversation is, not above it.
        if let Some(sid) = st.active.get() {
            for (i, (_, text)) in st.depth.steers.get().iter().filter(|(s, _)| *s == sid).enumerate() {
                msgs.push(ChatMessage {
                    created_at: Some(time::rfc3339(st.now())),
                    ..ChatMessage::plain(format!("steer-{i}"), "user", text.clone())
                });
            }
        }
        let _ = items_out.try_set(msgs);
    });
    let u = ui.clone();
    let studio_rows = studio.clone();
    let col = w::for_each(
        &col,
        items,
        |m| m.id.clone(),
        move |m| {
            if m.id == "earlier" {
                return earlier_row(&u, studio_rows.clone(), earlier.clone());
            }
            let day = day_heading_for(&studio_rows, m);
            let body = if m.id == "live" {
                live_turn(&u, studio_rows.clone())
            } else {
                let name = if m.role == "user" {
                    studio_rows.user_name.get()
                } else {
                    studio_rows.partner_name.get()
                };
                saved_turn(&u, studio_rows.clone(), m, &name)
            };
            if let Some(label) = day {
                w::column(&u)
                    .gap(unit * 2.0)
                    .layout(|l| l.width = Length::Percent(1.0))
                    .child(day_chip(&u, &label))
                    .child(body)
            } else {
                body
            }
        },
    );
    let scroll = w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .semantics(Semantics::new(Role::Region).label("Chat messages"))
        .child(col);
    let scroller = follow_scroll(ui, scroll, studio.clone());
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

fn studio_workspace(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(ui, move || st.mode.get() == WorkMode::Plan, {
        let u = u.clone();
        let studio = studio.clone();
        move || crate::plan_dock::workspace(&u, studio.clone())
    })
    .fallback({
        let u = u.clone();
        let studio = studio.clone();
        move || crate::chat_split::workspace(&u, studio.clone())
    })
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    })
}

/// Agent-first Home: presence, a greeting, status, and intent starters.
/// The composer below is the single clear input. Tools stay in the rails.
fn home_surface(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let name = studio.greeting_name();
    let u = ui.clone();
    let title = w::text(ui, "")
        .variant("heading")
        .text(move || messages().text(&u, "ready-named").replace("{name}", &name))
        .text_align(remedy_ui::style::TextAlign::Center)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.max_width = Length::Px(480.0);
            l.min_width = Length::Px(0.0);
        })
        .style(tokens::typo(tokens::HERO).foreground(colors.text));

    let st_status = studio.clone();
    let u_status = ui.clone();
    let status_line = w::text(ui, "")
        .text(move || match st_status.conn.get() {
            crate::studio::Conn::Ready => messages().text(&u_status, "home-status-ready"),
            crate::studio::Conn::Connecting => messages().text(&u_status, "status-connecting"),
            crate::studio::Conn::Disconnected => messages().text(&u_status, "status-disconnected"),
            crate::studio::Conn::Error(e) => e,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(tokens::typo(tokens::BODY).foreground(colors.text));
    let st_detail = studio.clone();
    let detail = w::text(ui, "")
        .text(move || {
            let model = st_detail.model.get();
            let provider = st_detail.provider.get();
            if model.is_empty() {
                String::new()
            } else if provider.is_empty() {
                model
            } else {
                format!("{provider} / {model}")
            }
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(tokens::typo(tokens::META).foreground(colors.muted));
    let status = w::column(ui)
        .gap(tokens::space())
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .child(status_line)
        .child(detail);

    let mut starters = w::row(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.wrap = true;
        });
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
                tokens::chip(&ui.theme().colors, false)
                    .weight(550.0)
                    .padding(Insets {
                        top: 8.0,
                        end: 12.0,
                        bottom: 8.0,
                        start: 12.0,
                    }),
            ),
        );
    }

    let face_studio = studio.clone();
    let face_ui = ui.clone();
    let face = crate::presence::swap(
        &face_ui,
        move || crate::presence::from_studio(&face_studio),
        crate::theme_pack::layout().presence_home,
    );

    let status_panel = tokens::info_panel(ui, messages().text(ui, "home-status-title"), status)
        .layout(|l| {
            l.max_width = Length::Px(400.0);
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });

    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .semantics(Semantics::new(Role::Region).label("Home"))
        .child(
            w::column(ui)
                .align(Align::Center)
                .justify(Justify::Center)
                .gap(tokens::gutter())
                .padding(Insets::all(tokens::gutter()))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                    l.min_height = Length::Px(0.0);
                })
                .child(face)
                .child(title)
                .child(status_panel)
                .child(starters),
        )
}

fn empty_well(ui: &Ui, studio: Studio) -> El {
    home_surface(ui, studio)
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

fn day_heading_for(studio: &Studio, msg: &ChatMessage) -> Option<String> {
    if msg.id == "live" {
        return None;
    }
    let label = msg
        .created_at
        .as_deref()
        .map(time::day_label)
        .filter(|s| !s.is_empty())?;
    let msgs = studio.messages.get();
    let idx = msgs.iter().position(|m| m.id == msg.id)?;
    if idx == 0 {
        return Some(label);
    }
    let prev = msgs[idx - 1]
        .created_at
        .as_deref()
        .map(time::day_label)
        .unwrap_or_default();
    if prev == label {
        None
    } else {
        Some(label)
    }
}

/// Stands for the messages above the ones built, and builds more of them.
fn earlier_row(ui: &Ui, studio: Studio, hidden: Signal<usize>) -> El {
    let label = move || match hidden.get() {
        1 => "Show 1 earlier message".to_string(),
        n => format!("Show earlier messages ({n} more)"),
    };
    w::row(ui)
        .justify(Justify::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(
            w::button(ui, label, move || {
                studio.depth.stuck.set(false);
                studio.depth.shown.update(|n| *n += crate::depth::EARLIER_STEP);
            })
            .variant("quiet"),
        )
}

fn day_chip(ui: &Ui, label: &str) -> El {
    let colors = ui.theme().colors.clone();
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .padding(Insets {
            top: 4.0,
            end: 0.0,
            bottom: 2.0,
            start: 0.0,
        })
        .semantics({
            let label = label.to_string();
            move || Semantics::new(Role::Label).label(label.clone())
        })
        .child(w::text(ui, label).style(tokens::section_kicker(&colors)))
}

fn bubble_card(ui: &Ui, body: El, user: bool, system: bool) -> El {
    let colors = ui.theme().colors.clone();
    let pad = Insets {
        top: tokens::space() * 2.0,
        end: tokens::space() * 2.0,
        bottom: tokens::space() * 2.0,
        start: tokens::space() * 2.0,
    };
    if user {
        return w::column(ui)
            .gap(tokens::space())
            .padding(pad)
            .layout(|l| {
                l.max_width = Length::Px(tokens::BUBBLE_MAX);
                l.shrink = 1.0;
            })
            .style(tokens::well_surface(&colors).foreground(colors.text))
            .child(body);
    }
    if system {
        return w::column(ui)
            .gap(tokens::space())
            .padding(pad)
            .layout(|l| {
                l.max_width = Length::Px(tokens::BUBBLE_MAX);
                l.shrink = 1.0;
            })
            .style(
                StylePatch::new()
                    .background(colors.panel)
                    .foreground(colors.danger)
                    .radius(0.0)
                    .border(colors.danger)
                    .border_width(1.0),
            )
            .child(body);
    }
    // Her side reads as text in the conversation, the way a person writes: no box.
    w::column(ui)
        .gap(tokens::space())
        .padding(Insets {
            top: tokens::space(),
            end: 0.0,
            bottom: tokens::space(),
            start: 0.0,
        })
        .layout(|l| {
            l.max_width = Length::Px(tokens::BUBBLE_MAX);
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .style(StylePatch::new().foreground(colors.text))
        .child(body)
}

/// A quiet line in her reply: a run of steps, or the thought she is on. Hidden while empty.
fn quiet_line(ui: &Ui, prefix: &'static str, text: impl Fn() -> String + 'static) -> El {
    let colors = ui.theme().colors.clone();
    let text = Rc::new(text);
    let shown = text.clone();
    let el = w::text(ui, "")
        .text(move || {
            let t = shown();
            if t.is_empty() { String::new() } else { format!("{prefix}{t}") }
        })
        .style(tokens::typo(tokens::META).foreground(colors.muted))
        .layout(|l| {
            l.max_width = Length::Px(tokens::BUBBLE_MAX);
            l.min_width = Length::Px(0.0);
        });
    let flag = el.clone();
    el.bind(move || {
        flag.clone().hidden(text().is_empty());
    })
}

/// Her thinking inside the message: closed it is one line (the thought she is on, or
/// "Thinking"), open it is all of it. `live` follows the thinking as it streams.
fn thinking_row(ui: &Ui, thinking: impl Fn() -> String + 'static, live: bool) -> El {
    let colors = ui.theme().colors.clone();
    let open = Signal::new(false);
    let thinking = Rc::new(thinking);
    let (head_src, body_src, has_src) = (thinking.clone(), thinking.clone(), thinking.clone());
    let head = w::text(ui, "")
        .text(move || {
            let mark = if open.get() { "▾" } else { "▸" };
            match (live, work::latest_thought(&head_src())) {
                (true, Some(now)) if !open.get() => format!("{mark} Thinking · {now}"),
                _ => format!("{mark} Thinking"),
            }
        })
        .style(tokens::typo(tokens::META).foreground(colors.muted))
        .on_click(move || open.update(|v| *v = !*v))
        .semantics(move || {
            Semantics::new(Role::Button)
                .label("Thinking")
                .expanded(open.get())
                .action(Action::Click)
        });
    let u = ui.clone();
    let body = show(ui, move || open.get(), move || {
        let src = body_src.clone();
        w::text(&u, "")
            .text(move || src())
            .selectable(true)
            .style(tokens::typo(tokens::META).foreground(u.theme().colors.muted))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            })
    })
    .el();
    let row = w::column(ui)
        .gap(crate::tokens::space() * 1.0)
        .layout(|l| {
            l.max_width = Length::Px(tokens::BUBBLE_MAX);
            l.min_width = Length::Px(0.0);
        })
        .child(head)
        .child(body);
    let flag = row.clone();
    row.bind(move || {
        flag.clone().hidden(has_src().trim().is_empty());
    })
}

/// When a message was said, on the local clock ("2:14 PM"). The day is the
/// heading above it.
fn stamp_text(ui: &Ui, created_at: Option<&str>) -> Option<String> {
    let at = time::parse_rfc3339(created_at?)?;
    Some(time::clock(ui.local_time_at(at).local_seconds()))
}

fn speaker_meta(ui: &Ui, name: &str, created_at: Option<&str>, now: i64, user: bool) -> El {
    let colors = ui.theme().colors.clone();
    let name_el = w::text(ui, name).nowrap().style(
        tokens::typo(tokens::META)
            .weight(620.0)
            .foreground(colors.text),
    );
    let mut row = w::row(ui)
        .align(Align::Baseline)
        .gap(crate::tokens::space() * 2.0)
        .layout(|l| l.shrink = 1.0);
    let _ = now;
    let stamp = stamp_text(ui, created_at).map(|s| {
        w::text(ui, s)
            .nowrap()
            .style(tokens::typo(tokens::META).foreground(colors.muted))
    });
    if user {
        if let Some(s) = stamp {
            row = row.child(s);
        }
        row.child(name_el)
    } else {
        row = row.child(name_el);
        if let Some(s) = stamp {
            row = row.child(s);
        }
        row
    }
}

fn speaker_cluster(
    ui: &Ui,
    name: &str,
    user: bool,
    created_at: Option<&str>,
    now: i64,
    cards: El,
    meta_action: Option<El>,
    below: Option<El>,
    extras: Option<(El, f32)>,
) -> El {
    let meta = speaker_meta(ui, name, created_at, now, user);
    let head = if let Some(action) = meta_action {
        w::row(ui)
            .align(Align::Baseline)
            .gap(tokens::space())
            .layout(|l| {
                l.min_width = Length::Px(0.0);
                l.shrink = 1.0;
            })
            .child(meta)
            .child(action)
    } else {
        meta
    };
    let content = w::column(ui)
        .gap(tokens::space())
        .layout(|l| {
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
            l.max_width = Length::Px(tokens::BUBBLE_MAX + 8.0);
        })
        .child(head)
        .child(cards);
    let content = if let Some(tr) = below {
        content.child(tr)
    } else {
        content
    };
    let avatar = if user {
        logo::persona(ui, name, true, tokens::AVATAR)
    } else {
        crate::presence::swap(ui, || crate::presence::State::Done.into(), tokens::AVATAR)
    };
    let mut row = w::row(ui)
        .align(Align::Start)
        .gap(tokens::space() * 2.0)
        .layout(|l| l.width = Length::Percent(1.0));
    row = if user {
        row.justify(Justify::End).child(content).child(avatar)
    } else {
        row.justify(Justify::Start).child(avatar).child(content)
    };
    if let Some((extras, width)) = extras {
        w::reveal_on_hover(row, &extras, width)
    } else {
        row
    }
}

/// Gives a card its time as a hover tip ("3:46 PM"). No time known, no tip.
fn with_time(ui: &Ui, card: El, at: Option<i64>) -> El {
    if let Some(at) = at {
        w::tooltip(&card, time::clock(ui.local_time_at(at).local_seconds()));
    }
    card
}

/// Something she said in the reply, drawn in place. No fade: a fade moved and softened the
/// text for a moment each time a piece of her reply was finished.
fn sealed_card(ui: &Ui, studio: Studio, text: &str) -> El {
    let body = depth::blocks(ui, text, true, {
        let st = studio;
        move |src| st.depth.show_image(src)
    });
    bubble_card(ui, body, false, false)
}

fn saved_turn(ui: &Ui, studio: Studio, msg: &ChatMessage, name: &str) -> El {
    let user = msg.role == "user";
    let system = msg.role == "system";
    let assistant = msg.role == "assistant";
    let beats = mid_turn_beats(msg);
    let show_body = !msg.content.trim().is_empty();
    let mut cards = w::column(ui).gap(crate::tokens::space() * 2.0).layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    });
    if assistant && !msg.thinking.trim().is_empty() {
        let thinking = msg.thinking.clone();
        cards = cards.child(thinking_row(ui, move || thinking.clone(), false));
    }
    // What she said before a run of steps carries the time of the first of them (hover),
    // and the steps follow as one quiet line.
    for (said, steps) in work::flow(&msg.steps) {
        if let Some(text) = said {
            let card = sealed_card(ui, studio.clone(), &text);
            cards = cards.child(with_time(ui, card, steps.first().and_then(|s| s.at)));
        }
        if let Some(line) = work::steps_line(&steps) {
            cards = cards.child(quiet_line(ui, "▸ ", move || line.clone()));
        }
    }
    if show_body {
        let body = depth::blocks(ui, &msg.content, assistant, {
            let st = studio.clone();
            move |src| st.depth.show_image(src)
        });
        let closing = msg
            .steps
            .last()
            .and_then(|s| s.done_at.or(s.at))
            .or_else(|| msg.created_at.as_deref().and_then(time::parse_rfc3339));
        cards = cards.child(with_time(ui, bubble_card(ui, body, user, system), closing));
    }
    let streaming = studio.streaming.get();
    let copy = if streaming {
        None
    } else {
        let text = logic::message_copy_text(&msg.content, &beats);
        if text.trim().is_empty() {
            None
        } else {
            Some(copy_button(ui, text))
        }
    };
    let (meta_action, below, extras) = if assistant && (show_body || !beats.is_empty()) {
        let (react, extras) = reaction_bar(ui, studio.clone(), msg.id.clone());
        let regen = if streaming {
            None
        } else {
            Some(regen_button(ui, studio.clone(), msg.id.clone()))
        };
        (pack_actions(ui, copy, regen), react, extras)
    } else if user {
        let edit = if streaming {
            None
        } else {
            Some(edit_button(
                ui,
                studio.clone(),
                msg.id.clone(),
                msg.content.clone(),
            ))
        };
        (
            pack_actions(ui, copy, edit),
            Some(agent_reaction(ui, studio.clone(), msg.id.clone())),
            None,
        )
    } else {
        (None, None, None)
    };
    speaker_cluster(
        ui,
        name,
        user,
        msg.created_at.as_deref(),
        studio.now(),
        cards,
        meta_action,
        below,
        extras,
    )
}

fn live_turn(ui: &Ui, studio: Studio) -> El {
    let beats: Signal<Vec<(usize, String)>> = Signal::new(Vec::new());
    let beats_out = beats.clone();
    let st_beats = studio.clone();
    let sealed = w::column(ui)
        .gap(crate::tokens::space() * 2.0)
        .layout(|l| l.width = Length::Percent(1.0));
    let _ = sealed.clone().bind(move || {
        let next: Vec<(usize, String)> =
            work::narration_beats(&st_beats.partial.get(), &st_beats.live_breaks.get())
                .into_iter()
                .enumerate()
                .collect();
        match beats_out.try_get() {
            Ok(cur) if cur != next => {
                let _ = beats_out.try_set(next);
            }
            _ => {}
        }
    });
    let u_beat = ui.clone();
    let st_beat = studio.clone();
    let sealed = w::for_each(
        &sealed,
        beats,
        |b| format!("{}:{}", b.0, b.1),
        move |(i, beat)| {
            // The i-th thing she said before a call: said by the time of that call.
            let at = remedy_ui::reactive::untrack(|| {
                st_beat
                    .live_steps
                    .get()
                    .iter()
                    .filter(|s| s.narration.as_deref().is_some_and(|n| !n.trim().is_empty()))
                    .nth(*i)
                    .and_then(|s| s.at)
            });
            let i = *i;
            let st_line = st_beat.clone();
            w::column(&u_beat)
                .gap(crate::tokens::space() * 2.0)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(with_time(&u_beat, sealed_card(&u_beat, st_beat.clone(), beat), at))
                .child(quiet_line(&u_beat, "▸ ", move || {
                    work::flow(&st_line.live_steps.get())
                        .into_iter()
                        .filter(|(said, _)| said.is_some())
                        .nth(i)
                        .and_then(|(_, steps)| work::steps_line(&steps))
                        .unwrap_or_default()
                }))
        },
    );
    // Steps she ran before saying anything.
    let st_lead = studio.clone();
    let lead = quiet_line(ui, "▸ ", move || {
        work::flow(&st_lead.live_steps.get())
            .into_iter()
            .next()
            .filter(|(said, _)| said.is_none())
            .and_then(|(_, steps)| work::steps_line(&steps))
            .unwrap_or_default()
    });
    let st_think = studio.clone();
    let thought = thinking_row(ui, move || st_think.live_thinking.get(), true);
    let react_id = studio
        .live_message_id
        .get()
        .filter(|id| !id.is_empty())
        .unwrap_or_default();
    let (trailing, react_ex) = if react_id.is_empty() {
        (None, None)
    } else {
        reaction_bar(ui, studio.clone(), react_id)
    };

    let u = ui.clone();
    let s_card = studio.clone();
    let s_body = studio.clone();
    let live = show(
        ui,
        move || live_shows_bubble(&s_card),
        move || {
            let s = s_body.clone();
            let u = u.clone();
            bubble_card(
                    &u,
                    live_body(&u, move || {
                        let reply = work::reply_after(&s.partial.get(), &s.live_breaks.get());
                        let quiet = s.partial.get().trim().is_empty()
                            && s.live_thinking.get().trim().is_empty()
                            && s.live_steps.get().is_empty();
                        if reply.trim().is_empty() && quiet {
                            "…".to_string()
                        } else {
                            reply
                        }
                    }),
                    false,
                    false,
                )
        },
    )
    .el();

    let mut cards = w::column(ui)
        .gap(crate::tokens::space() * 2.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(thought)
        .child(lead)
        .child(sealed);
    if let Some(bar) = trailing {
        cards = cards.child(bar);
    }
    let cards = cards.child(live);

    speaker_cluster(
        ui,
        &studio.partner_name.get(),
        false,
        None,
        studio.now(),
        cards,
        None,
        None,
        react_ex,
    )
}

fn live_shows_bubble(studio: &Studio) -> bool {
    // The live turn stays mounted for the whole stream, including tool steps
    // before any tokens arrive. Hiding the bubble then left a "Remedy" header
    // with an empty body while Coding showed "Working".
    let _ = studio;
    true
}

/// "Worked · N steps · N errors" for a turn. Hidden for a plain chat.
pub(crate) fn work_meta(
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
            tokens::typo(tokens::MONO)
                .foreground(colors.text)
                .weight(600.0),
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
        .style(StylePatch::new().background(dot_color).radius(crate::tokens::corner(crate::tokens::Corner::Small)));
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
                .gap(crate::tokens::space() * 1.5)
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
                if n == 1 {
                    "work-error"
                } else {
                    "work-errors"
                }
            } else if n == 1 {
                "work-step"
            } else {
                "work-steps"
            };
            format!("· {n} {}", messages().text(&u, key))
        })
        .nowrap()
        .style(tokens::typo(tokens::MONO).foreground(color))
}

fn live_body(ui: &Ui, text: impl Fn() -> String + 'static) -> El {
    let chip = ui
        .theme()
        .colors
        .accent
        .mix_oklab(ui.theme().colors.bg, 0.72);
    ui.el("bubble-body")
        .text(move || depth::inline_rich(&text(), chip))
        .selectable(true)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
}

fn mode_switcher(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .semantics(Semantics::new(Role::Group).label(messages().text(ui, "work-mode")));
    let modes = [
        (WorkMode::Chat, "work-mode-chat", "work-mode-chat-tip"),
        (WorkMode::Plan, "work-mode-plan", "work-mode-plan-tip"),
        (WorkMode::Build, "work-mode-build", "work-mode-build-tip"),
    ];
    for (mode, label, tip) in modes {
        let st = studio.clone();
        let u = ui.clone();
        let b = w::button(ui, move || messages().text(&u, label), {
            let st = studio.clone();
            move || st.set_mode(mode)
        })
        .variant("quiet");
        let paint = b.clone();
        let colors = ui.theme().colors.clone();
        let b = b.bind(move || {
            let on = st.mode.get() == mode;
            paint.state(States::SELECTED, on);
            let _ = paint.clone().style(mode_chip_style(&colors, on));
        });
        let u = ui.clone();
        w::tooltip(&b, move || messages().text(&u, tip));
        let st = studio.clone();
        let u = ui.clone();
        row = row.child(b.semantics(move || {
            Semantics::new(Role::Button)
                .label(messages().text(&u, label))
                .checked(st.mode.get() == mode)
                .action(Action::Click)
        }));
    }
    row
}

fn mode_chip_style(colors: &remedy_ui::style::ColorRoles, on: bool) -> StylePatch {
    tokens::chip(colors, on).weight(550.0)
}

fn panel_button(
    ui: &Ui,
    _studio: Studio,
    label: &'static str,
    tip: &'static str,
    selected: impl Fn() -> bool + 'static,
    on_press: impl Fn() + 'static,
) -> El {
    let u = ui.clone();
    let b = w::button(ui, move || messages().text(&u, label), on_press).variant("quiet");
    let paint = b.clone();
    let colors = ui.theme().colors.clone();
    let b = b.bind(move || {
        let on = selected();
        paint.state(States::SELECTED, on);
        let _ = paint.clone().style(mode_chip_style(&colors, on));
    });
    let u = ui.clone();
    w::tooltip(&b, move || messages().text(&u, tip));
    let u = ui.clone();
    b.semantics(move || {
        Semantics::new(Role::Button)
            .label(messages().text(&u, label))
            .action(Action::Click)
    })
}

fn copy_button(ui: &Ui, text: String) -> El {
    let u = ui.clone();
    compact_icon_action(ui, w::Icon::Copy, "chat-copy", move || {
        u.copy_text(text.clone());
    })
}

fn pack_actions(ui: &Ui, first: Option<El>, second: Option<El>) -> Option<El> {
    match (first, second) {
        (Some(a), Some(b)) => Some(
            w::row(ui)
                .align(Align::Baseline)
                .gap(tokens::space())
                .child(a)
                .child(b),
        ),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        (None, None) => None,
    }
}

fn edit_button(ui: &Ui, studio: Studio, id: String, content: String) -> El {
    compact_icon_action(ui, w::Icon::Note, "chat-edit", move || {
        depth::edit_user_message(&studio, &id, &content)
    })
}

fn regen_button(ui: &Ui, studio: Studio, id: String) -> El {
    compact_icon_action(ui, w::Icon::Refresh, "chat-regenerate", move || {
        depth::regenerate(&studio, &id)
    })
}

/// Same cap height as the speaker timestamp, so the name row does not grow
/// and clip the last line of a tight chat preview.
fn compact_icon_action(
    ui: &Ui,
    glyph: w::Icon,
    label_key: &'static str,
    on_press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    let tip_ui = ui.clone();
    let btn = w::row(ui)
        .align(Align::Center)
        .child(w::icon(ui, glyph, tokens::META.size))
        .on_click(on_press)
        .style(StylePatch::new().foreground(colors.muted))
        .semantics(move || {
            Semantics::new(Role::Button)
                .label(messages().text(&u, label_key))
                .action(Action::Click)
        });
    w::tooltip(&btn, move || messages().text(&tip_ui, label_key));
    btn
}

fn current_user_reaction(studio: &Studio, id: &str) -> String {
    if let Some(m) = studio.messages.get().iter().find(|m| m.id == id) {
        return m.user_reaction.clone();
    }
    if studio.live_message_id.get().as_deref() == Some(id) {
        return studio.live_user_reaction.get();
    }
    String::new()
}

fn open_reaction_picker(ui: &Ui, anchor: &El, studio: Studio, id: String) {
    let current = current_user_reaction(&studio, &id);
    let colors = ui.theme().colors.clone();
    let accent = colors.accent;
    let muted = colors.muted;
    let layer_slot: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let mut list = w::column(ui).gap(crate::tokens::space() * 0.5).padding(Insets {
        top: 4.0,
        end: 4.0,
        bottom: 4.0,
        start: 4.0,
    });
    for (emoji, label) in logic::REACTIONS {
        let selected = current == *emoji;
        let title = if selected {
            format!("{label} (selected, press to remove)")
        } else {
            (*label).to_string()
        };
        let mark = reaction_glyph(emoji).unwrap_or(Glyph::Star);
        let st = studio.clone();
        let mid = id.clone();
        let em = (*emoji).to_string();
        let close = layer_slot.clone();
        let fg = if selected { accent } else { muted };
        let title_sem = title.clone();
        let row = w::row(ui)
            .gap(crate::tokens::space() * 2.0)
            .align(Align::Center)
            .padding(Insets {
                top: 6.0,
                end: 12.0,
                bottom: 6.0,
                start: 8.0,
            })
            .child(glyph(ui, mark, 16.0).style(StylePatch::new().foreground(fg)))
            .child(
                w::text(ui, (*label).to_string()).style(StylePatch::new().foreground(fg)),
            )
            .style(if selected {
                StylePatch::new()
                    .background(accent.with_alpha(0.14))
                    .radius(crate::tokens::corner(crate::tokens::Corner::Medium))
                    .foreground(fg)
            } else {
                StylePatch::new().radius(crate::tokens::corner(crate::tokens::Corner::Medium)).foreground(fg)
            })
            .semantics(move || {
                Semantics::new(Role::Button)
                    .label(title_sem.clone())
                    .action(Action::Click)
            })
            .on_click(move || {
                react(&st, &mid, &em);
                if let Some(layer) = close.borrow().clone() {
                    layer.remove();
                }
            });
        list = list.child(row);
    }
    let layer = w::popover(ui, anchor, list);
    *layer_slot.borrow_mut() = Some(layer);
}

/// One "Add reaction" trigger plus a compact pill for the chosen reaction.
/// The four options live in a keyboard-accessible popover of stroke icons
/// so emoji font fallback cannot split or blur the marks, and the footer
/// does not clip at narrow widths the way four always-visible buttons did.
fn reaction_bar(ui: &Ui, studio: Studio, id: String) -> (Option<El>, Option<(El, f32)>) {
    let colors = ui.theme().colors.clone();
    let accent = colors.accent;
    let muted = colors.muted;
    let current = current_user_reaction(&studio, &id);

    let mut row = w::row(ui).gap(crate::tokens::space() * 1.0).align(Align::Center).padding(Insets {
        top: 2.0,
        end: 0.0,
        bottom: 0.0,
        start: 0.0,
    });

    if !current.is_empty() {
        let st_press = studio.clone();
        let id_press = id.clone();
        let em_name = current.clone();
        let label = format!("You reacted {}. Press to remove.", reaction_label(&em_name));
        let mark = reaction_glyph(&current).unwrap_or(Glyph::Star);
        let pill = w::row(ui)
            .align(Align::Center)
            .padding(Insets {
                top: 2.0,
                end: 6.0,
                bottom: 2.0,
                start: 6.0,
            })
            .child(glyph(ui, mark, 14.0).style(StylePatch::new().foreground(accent)))
            .style({
                StylePatch::new()
                    .foreground(accent)
                    .background(accent.with_alpha(0.14))
                    .radius(crate::tokens::corner(crate::tokens::Corner::Medium))
            })
            .on_click(move || react(&st_press, &id_press, ""))
            .semantics(move || {
                Semantics::new(Role::Button)
                    .label(label.clone())
                    .action(Action::Click)
            });
        row = row.child(pill);
    }

    let anchor: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let open_anchor = anchor.clone();
    let open_ui = ui.clone();
    let st_open = studio.clone();
    let id_open = id.clone();
    let add = w::row(ui)
        .align(Align::Center)
        .padding(Insets {
            top: 2.0,
            end: 4.0,
            bottom: 2.0,
            start: 4.0,
        })
        .child(glyph(ui, Glyph::AddReaction, 16.0).style(StylePatch::new().foreground(muted)))
        .on_click(move || {
            let Some(anchor_el) = open_anchor.borrow().clone() else {
                return;
            };
            open_reaction_picker(&open_ui, &anchor_el, st_open.clone(), id_open.clone());
        })
        .style(StylePatch::new().foreground(muted).radius(crate::tokens::corner(crate::tokens::Corner::Medium)))
        .semantics(|| {
            Semantics::new(Role::Button)
                .label("Add reaction")
                .action(Action::Click)
        });
    *anchor.borrow_mut() = Some(add.clone());
    row = row.child(add);
    (Some(row), None)
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
            let mark = st
                .messages
                .get()
                .iter()
                .find(|m| m.id == id)
                .map(|m| m.agent_reaction.clone())
                .unwrap_or_default();
            let g = reaction_glyph(&mark).unwrap_or(Glyph::Star);
            let colors = u.theme().colors.clone();
            glyph(&u, g, 14.0).style(StylePatch::new().foreground(colors.muted))
        },
    )
    .el()
}

/// How long after a wheel turn, a key or a press a move of the feed counts as the
/// person's own.
const READ_BACK_WINDOW: std::time::Duration = std::time::Duration::from_millis(600);

/// Keeps the feed on its newest message. Only the person lets go of it: by turning the
/// wheel up, dragging the bar, or pressing Up, Page Up or Home. The feed moving because
/// it got shorter, or because new rows were not measured yet, is not them. It takes hold
/// again when they send a message, open another conversation, press End or Latest.
fn follow_scroll(ui: &Ui, scroll: El, studio: Studio) -> El {
    use std::cell::Cell;
    let last_y = Rc::new(Cell::new(0.0f32));
    // Set before a move the feed makes itself, so that move is not read as the person's.
    let own_move = Rc::new(Cell::new(false));
    let touched: Rc<Cell<Option<std::time::Instant>>> = Rc::new(Cell::new(None));
    let shown = Rc::new(RefCell::new(None::<String>));
    let was_streaming = Rc::new(Cell::new(false));
    // Follow-up scrolls still to run (see the binding below).
    let waiting = Rc::new(Cell::new(0u8));
    let stuck = studio.depth.stuck.clone();
    let scroller_key = scroll.clone();
    let scroller_bind = scroll.clone();
    let st_key = studio.clone();
    let ui = ui.clone();
    let (touch_wheel, touch_press, touch_key) = (touched.clone(), touched.clone(), touched.clone());
    let (last_s, own_s, touched_s) = (last_y.clone(), own_move.clone(), touched.clone());
    scroll
        .on_wheel(move |_| {
            touch_wheel.set(Some(std::time::Instant::now()));
            false
        })
        .on_pointer(move |e| {
            if matches!(e.phase, remedy_ui::input::PointerPhase::Down) {
                touch_press.set(Some(std::time::Instant::now()));
            }
            false
        })
        .on_scroll(move |off| {
            let y = off.y;
            let own = own_s.replace(false);
            let by_person = touched_s.get().is_some_and(|at| at.elapsed() < READ_BACK_WINDOW);
            if y + 2.0 < last_s.get() && !own && by_person {
                stuck.set(false);
            }
            last_s.set(y);
        })
        .on_key(move |e| {
            if !e.pressed {
                return false;
            }
            match e.key {
                Key::Home | Key::PageUp | Key::ArrowUp => {
                    touch_key.set(Some(std::time::Instant::now()));
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
                let session = st.active.get();
                let streaming = st.streaming.get();
                let moved = *shown.borrow() != session;
                if moved {
                    *shown.borrow_mut() = session;
                }
                // Another conversation, or a turn just started (they sent something):
                // the newest message is what they want to see.
                if moved || (streaming && !was_streaming.get()) {
                    st.depth.stuck.set(true);
                }
                was_streaming.set(streaming);
                if st.depth.stuck.get() {
                    own_move.set(true);
                    scroller.scroll_to(Vec2::new(0.0, 1.0e9));
                    // New rows have no height until the next layout, so the end is not
                    // the end yet: go there again once they have been measured. One
                    // pair of follow-ups at a time: a pair per streamed word was a
                    // hundred timers a second, each forcing a layout.
                    if waiting.get() == 0 {
                        waiting.set(2);
                        for ms in [60u64, 350] {
                            let (again, tree, own, held, waiting) =
                                (scroller.clone(), ui.clone(), own_move.clone(), st.depth.stuck.clone(), waiting.clone());
                            let _ = ui.after(std::time::Duration::from_millis(ms), move || {
                                waiting.set(waiting.get().saturating_sub(1));
                                if held.get() && tree.tree().contains(again.id()) {
                                    own.set(true);
                                    again.scroll_to(Vec2::new(0.0, 1.0e9));
                                }
                            });
                        }
                    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_bubble_stays_up_while_a_tool_step_is_running() {
        let studio = Studio::preview_chat_live();
        studio.partial.set(String::new());
        studio.live_breaks.set(Vec::new());
        assert!(
            !studio.live_steps.get().is_empty(),
            "preview live turn has a running step"
        );
        assert!(
            live_shows_bubble(&studio),
            "the chat bubble must stay visible during a tool turn"
        );
    }
}
