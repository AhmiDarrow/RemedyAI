//! Chat column: session header, empty monogram, bubbles, composer.

use crate::logo;
use crate::studio::{ChatMessage, Studio};
use crate::{messages, say, tool_button};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::text::{RichText, TextStylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

/// The chat column: header, feed or empty well, composer.
pub fn well(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    w::column(ui)
        .style(StylePatch::new().background(colors.bg))
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        })
        .child(header(ui, studio.clone()))
        .child(feed(ui, studio.clone()))
        .child(composer(ui, studio))
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
                    let model = st_sub.model.get();
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
    let chips = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(
            show(ui, move || live_st.streaming.get(), {
                let u = ui.clone();
                let accent = colors.accent;
                move || {
                    w::text(&u, "Live")
                        .variant("muted")
                        .style(StylePatch::new().foreground(accent))
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
                move || {
                    Semantics::new(Role::Button)
                        .label(messages().text(&u, "work-mode"))
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
        if !partial.is_empty() || streaming {
            msgs.push(ChatMessage {
                id: "live".into(),
                role: "assistant".into(),
                content: if partial.is_empty() {
                    "…".into()
                } else {
                    partial
                },
                created_at: None,
                streaming: true,
            });
        }
        items_out.set(msgs);
    });
    let u = ui.clone();
    let user = studio.user_name.clone();
    let col = w::for_each(
        &col,
        items,
        |m| m.id.clone(),
        move |m| {
            let name = if m.role == "user" {
                user.get()
            } else {
                "Remedy".into()
            };
            bubble(&u, m, &name, m.streaming)
        },
    );
    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(col)
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

fn bubble(ui: &Ui, msg: &ChatMessage, name: &str, _live: bool) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let user = msg.role == "user";
    let system = msg.role == "system";
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
    let body = bubble_body(ui, &msg.content);
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

/// Plain text with `backtick` runs in AhmiPrint, wrapping as one rich paragraph.
fn bubble_body(ui: &Ui, content: &str) -> El {
    let mut rich = RichText::new();
    let mut code = TextStylePatch::default();
    code.families = Some(vec!["AhmiPrint".into()]);
    code.weight = Some(600.0);
    for (i, line) in content.split('\n').enumerate() {
        if i > 0 {
            rich.push_text("\n");
        }
        let mut is_code = false;
        for part in line.split('`') {
            if !part.is_empty() {
                if is_code {
                    rich.push_styled(part, code.clone());
                } else {
                    rich.push_text(part);
                }
            }
            is_code = !is_code;
        }
    }
    ui.el("bubble-body")
        .text(rich)
        .layout(|l| l.width = Length::Percent(1.0))
}

fn composer(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors
        .line
        .unwrap_or(colors.accent.mix_oklab(colors.bg, 0.65));
    let field = w::text_input(ui, messages().text(ui, "composer-placeholder"))
        .placeholder(messages().text(ui, "composer-placeholder"));
    let f2 = field.clone();
    let s = studio.clone();
    let field = field.on_submit(move || {
        let text = f2.value();
        if !text.trim().is_empty() {
            f2.set_value("");
            s.send(text);
        }
    });
    let attach = {
        let u = ui.clone();
        tool_button(
            ui,
            w::Icon::File,
            move || messages().text(&u, "composer-attach"),
            || {},
        )
        .style(
            StylePatch::new()
                .background(colors.card)
                .radius(18.0)
                .foreground(colors.text),
        )
    };
    let streaming = {
        let s = studio.clone();
        move || s.streaming.get()
    };
    let send_or_stop = show(ui, streaming, {
        let s = studio.clone();
        let u = ui.clone();
        move || {
            let s = s.clone();
            tool_button(
                &u,
                w::Icon::Close,
                {
                    let u = u.clone();
                    move || messages().text(&u, "composer-stop")
                },
                move || s.stop(),
            )
        }
    })
    .fallback({
        let s = studio.clone();
        let f = field.clone();
        let u = ui.clone();
        move || {
            tool_button(
                &u,
                w::Icon::Send,
                {
                    let u = u.clone();
                    move || messages().text(&u, "composer-send")
                },
                {
                    let s = s.clone();
                    let f = f.clone();
                    move || {
                        let text = f.value();
                        if !text.trim().is_empty() {
                            f.set_value("");
                            s.send(text);
                        }
                    }
                },
            )
        }
    })
    .el();
    let shell = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit * 1.5,
            end: unit * 1.5,
            bottom: unit * 1.5,
            start: unit * 1.5,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(52.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .radius(ui.theme().radii.large)
                .border(line)
                .border_width(1.0),
        )
        .child(attach)
        .child(field.el().layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(send_or_stop);
    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 4.0,
            bottom: unit * 2.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(shell)
        .child(say(ui, "composer-hint", "muted"))
}
