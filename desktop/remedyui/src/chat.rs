//! Chat column: session header, empty monogram, bubbles, composer.

use crate::depth::logic::{self, reaction_label};
use crate::depth::{self, react};
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
                        empty_st.messages.get().is_empty()
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
                                .style(tokens::typo(tokens::META).foreground(accent).weight(620.0)),
                        )
                }
            })
            .el(),
        )
        .child(mode_switcher(ui, studio.clone()));

    let chips = chips.layout(|l| {
        l.shrink = 0.0;
    });
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit)
        .padding(Insets {
            top: tokens::GUTTER,
            end: tokens::GUTTER,
            bottom: unit * 3.0,
            start: tokens::GUTTER,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.shrink = 1.0;
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
        .gap(tokens::SPACE * 2.0)
        .padding(Insets {
            top: unit * 2.0,
            end: tokens::GUTTER,
            bottom: unit * 2.0,
            start: tokens::GUTTER,
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
            let day = day_heading_for(&studio_rows, m);
            let body = if m.id == "live" {
                live_turn(&u, studio_rows.clone())
            } else {
                let name = if m.role == "user" {
                    studio_rows.user_name.get()
                } else {
                    "Remedy".into()
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
        .gap(tokens::SPACE)
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

    let face = crate::presence::mark(ui, crate::presence::from_studio(&studio), crate::theme_pack::layout().presence_home);

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
                .gap(tokens::GUTTER)
                .padding(Insets::all(tokens::GUTTER))
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
        top: tokens::SPACE * 2.0,
        end: tokens::SPACE * 2.0,
        bottom: tokens::SPACE * 2.0,
        start: tokens::SPACE * 2.0,
    };
    if user {
        return w::column(ui)
            .gap(tokens::SPACE)
            .padding(pad)
            .layout(|l| {
                l.max_width = Length::Px(tokens::BUBBLE_MAX);
                l.shrink = 1.0;
            })
            .style(
                StylePatch::new()
                    .background(colors.panel)
                    .foreground(colors.text)
                    .radius(0.0)
                    .border(tokens::hairline(&colors))
                    .border_width(1.0),
            )
            .child(body);
    }
    if system {
        return w::column(ui)
            .gap(tokens::SPACE)
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
    // Assistant: a left accent rule, no enclosing box.
    let rule = ui
        .el("reply-rule")
        .layout(|l| {
            l.width = Length::Px(2.0);
            l.min_width = Length::Px(2.0);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().background(colors.accent));
    w::row(ui)
        .align(Align::Stretch)
        .gap(tokens::SPACE * 2.0)
        .layout(|l| {
            l.max_width = Length::Px(tokens::BUBBLE_MAX);
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(rule)
        .child(
            w::column(ui)
                .gap(tokens::SPACE)
                .padding(Insets {
                    top: tokens::SPACE,
                    end: tokens::SPACE,
                    bottom: tokens::SPACE,
                    start: 0.0,
                })
                .layout(|l| {
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                    l.grow = 1.0;
                })
                .style(StylePatch::new().foreground(colors.text))
                .child(body),
        )
}

fn stamp_text(created_at: Option<&str>, now: i64) -> Option<String> {
    created_at
        .map(|t| time::relative(t, now))
        .filter(|s| !s.is_empty())
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
        .gap(8.0)
        .layout(|l| l.shrink = 1.0);
    let stamp = stamp_text(created_at, now).map(|s| {
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
    trailing: Option<El>,
    extras: Option<(El, f32)>,
) -> El {
    let content = w::column(ui)
        .gap(tokens::SPACE)
        .layout(|l| {
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
            l.max_width = Length::Px(tokens::BUBBLE_MAX + 8.0);
        })
        .child(speaker_meta(ui, name, created_at, now, user))
        .child(cards);
    let content = if let Some(tr) = trailing {
        content.child(tr)
    } else {
        content
    };
    let avatar = if user {
        logo::persona(ui, name, true, tokens::AVATAR)
    } else if crate::presence::enabled() {
        crate::presence::mark(ui, crate::presence::State::Done, tokens::AVATAR)
    } else {
        logo::persona(ui, name, false, tokens::AVATAR)
    };
    let mut row = w::row(ui)
        .align(Align::Start)
        .gap(tokens::SPACE * 2.0)
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

fn sealed_card(ui: &Ui, studio: Studio, text: &str, animate: bool) -> El {
    let body = depth::blocks(ui, text, true, {
        let st = studio;
        move |src| st.depth.show_image(src)
    });
    let card = bubble_card(ui, body, false, false);
    if animate {
        maybe_seal_anim(ui, card)
    } else {
        card
    }
}

fn maybe_seal_anim(ui: &Ui, el: El) -> El {
    if runtime::spawn_disabled() || ui.reduced_motion() {
        return el;
    }
    use remedy_ui::view::motion::{Easing, Motion, Target};
    el.set_animated(Target::Opacity(0.0));
    el.animate(
        Target::Opacity(1.0),
        Motion::Tween {
            duration: ui.theme().motion.quick,
            easing: Easing::EASE_IN_OUT,
        },
    );
    el
}

fn saved_turn(ui: &Ui, studio: Studio, msg: &ChatMessage, name: &str) -> El {
    let user = msg.role == "user";
    let system = msg.role == "system";
    let assistant = msg.role == "assistant";
    let beats = mid_turn_beats(msg);
    let show_body = !msg.content.trim().is_empty();
    let mut cards = w::column(ui).gap(8.0).layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    });
    for beat in &beats {
        cards = cards.child(sealed_card(ui, studio.clone(), beat, false));
    }
    if show_body {
        let body = depth::blocks(ui, &msg.content, assistant, {
            let st = studio.clone();
            move |src| st.depth.show_image(src)
        });
        cards = cards.child(bubble_card(ui, body, user, system));
    }
    let (trailing, extras) = if assistant && (show_body || !beats.is_empty()) {
        reaction_bar(ui, studio.clone(), msg.id.clone())
    } else if user {
        (
            Some(agent_reaction(ui, studio.clone(), msg.id.clone())),
            None,
        )
    } else {
        (None, None)
    };
    speaker_cluster(
        ui,
        name,
        user,
        msg.created_at.as_deref(),
        studio.now(),
        cards,
        trailing,
        extras,
    )
}

fn live_turn(ui: &Ui, studio: Studio) -> El {
    let beats: Signal<Vec<(usize, String)>> = Signal::new(Vec::new());
    let beats_out = beats.clone();
    let st_beats = studio.clone();
    let sealed = w::column(ui)
        .gap(8.0)
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
        move |(_i, beat)| sealed_card(&u_beat, st_beat.clone(), &beat, true),
    );
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
            maybe_seal_anim(
                &u,
                bubble_card(
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
                ),
            )
        },
    )
    .el();

    let mut cards = w::column(ui)
        .gap(8.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(sealed);
    if let Some(bar) = trailing {
        cards = cards.child(bar);
    }
    let cards = cards.child(live);

    speaker_cluster(
        ui,
        "Remedy",
        false,
        None,
        studio.now(),
        cards,
        None,
        react_ex,
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

fn reaction_is_on(studio: &Studio, id: &str, emoji: &str) -> bool {
    if let Some(m) = studio.messages.get().iter().find(|m| m.id == id) {
        return m.user_reaction == emoji;
    }
    studio.live_message_id.get().as_deref() == Some(id) && studio.live_user_reaction.get() == emoji
}

fn reaction_bar(ui: &Ui, studio: Studio, id: String) -> (Option<El>, Option<(El, f32)>) {
    let colors = ui.theme().colors.clone();
    let mut extras = w::row(ui).gap(2.0).align(Align::Center);
    let mut selected: Option<El> = None;
    let mut extra_count = 0;
    for (emoji, _) in logic::REACTIONS {
        let emoji = (*emoji).to_string();
        let st_press = studio.clone();
        let id_press = id.clone();
        let em_press = emoji.clone();
        let accent = colors.accent;
        let muted = colors.muted;
        let st_style = studio.clone();
        let id_style = id.clone();
        let em_style = emoji.clone();
        let em_name = emoji.clone();
        let on = reaction_is_on(&studio, &id, &emoji);
        let btn = w::button(ui, emoji, move || react(&st_press, &id_press, &em_press))
            .variant("quiet")
            .style(move || {
                let on = reaction_is_on(&st_style, &id_style, &em_style);
                let mut p = tokens::typo(tokens::BODY).foreground(if on { accent } else { muted });
                if on {
                    p = p
                        .background(accent.with_alpha(0.14))
                        .radius(8.0)
                        .padding(Insets {
                            top: 2.0,
                            end: 6.0,
                            bottom: 2.0,
                            start: 6.0,
                        });
                }
                p
            })
            .semantics(move || {
                Semantics::new(Role::Button)
                    .label(reaction_label(&em_name).to_string())
                    .action(Action::Click)
            });
        if on && selected.is_none() {
            selected = Some(btn);
        } else {
            extras = extras.child(btn);
            extra_count += 1;
        }
    }
    let extras_width = 32.0 * extra_count as f32;
    let extras_el = extras;
    let mut row = w::row(ui).gap(4.0).align(Align::Center).padding(Insets {
        top: 2.0,
        end: 0.0,
        bottom: 0.0,
        start: 0.0,
    });
    if let Some(chip) = selected {
        row = row.child(chip);
    }
    row = row.child(extras_el.clone());
    (Some(row), Some((extras_el, extras_width)))
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
