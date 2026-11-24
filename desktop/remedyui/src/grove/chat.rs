//! Grove's conversation: the feed, starters, the attachment rail, and the talk bar.
//!
//! Enter sends. Shift+Enter breaks the line. A live turn with no attachment
//! steers instead of starting another turn. "I want to …" on Home plants a goal
//! inside `begin_send`.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::text::{RichText, TextStylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};

use crate::studio::Conn;

use super::api::Attachment;
use super::logic::{self, StoryMessage, TodoRow};
use super::state::Grove;

/// Which room the talk bar belongs to.
pub(super) enum RoomKind {
    /// Home plots. "I want to …" plants.
    Home,
    /// Alongside, on Home or in a goal. Home still plants.
    Alongside,
    /// A goal room. The id is read once for the placeholder.
    Goal(String),
}

pub(super) fn pane(ui: &Ui, grove: Grove, app: Option<App>, kind: RoomKind) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(feed(ui, grove.clone(), &kind))
        .child(errors(ui, grove.clone()))
        .child(attach_rail(ui, grove.clone()))
        .child(turn_line(ui, grove.clone()))
        .child(mode_row(ui, &grove, &kind))
        .child(composer(ui, grove, app, kind))
}

fn mode_row(ui: &Ui, grove: &Grove, kind: &RoomKind) -> El {
    // Talk / Plan / Build live with the composer — the request's mode, not a
    // second title-bar switcher.
    match kind {
        RoomKind::Home | RoomKind::Alongside => {
            let unit = ui.theme().spacing.unit;
            super::view::mode_chips(ui, grove).layout(|l| {
                l.margin = Insets {
                    start: unit * 2.0,
                    end: unit * 2.0,
                    top: 0.0,
                    bottom: 0.0,
                };
            })
        }
        RoomKind::Goal(_) => ui.el("grove-mode-off"),
    }
}

fn feed(ui: &Ui, grove: Grove, kind: &RoomKind) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let list = w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| l.width = Length::Percent(1.0));
    w::for_each(&list, grove.messages, |m: &StoryMessage| m.id.clone(), {
        let ui = ui.clone();
        let g = grove.clone();
        move |m| message_row(&ui, &g, m)
    });
    let partial = {
        let watch = grove.clone();
        let body = grove.clone();
        show(ui, move || watch.streaming.get(), {
            let ui = ui.clone();
            let g = body;
            move || {
                let who = g.partner();
                let g2 = g.clone();
                bubble(&ui, &who, false, move || {
                    let t = g2.partial.get();
                    RichText::plain(if t.is_empty() { "…".into() } else { t })
                })
            }
        })
        .el()
    };
    w::scroll(ui)
        .style(StylePatch::new().background(colors.bg))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .semantics(Semantics::new(Role::Region).label("Chat feed"))
        .child(list)
        .child(partial)
        .child(todo_block(ui, grove.clone()))
        .child(process_block(ui, grove.clone()))
        .child(empty_state(ui, grove, kind))
}

fn message_row(ui: &Ui, grove: &Grove, msg: &StoryMessage) -> El {
    if msg.reverted || (msg.content.trim().is_empty() && msg.tool_calls.is_empty()) {
        return ui.el("grove-skip");
    }
    let user = msg.role == "user";
    let text = if user {
        logic::strip_about_child(&msg.content)
    } else {
        msg.content.clone()
    };
    let who = if user {
        let n = grove.studio.user_name.get();
        if n.trim().is_empty() { "You".into() } else { n }
    } else {
        grove.partner()
    };
    bubble(ui, &who, user, move || plain_rich(&text))
}

fn bubble(ui: &Ui, who: &str, user: bool, text: impl Fn() -> RichText + 'static) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let (bg, fg, border) = if user {
        (
            colors.bg,
            colors.text,
            crate::tokens::hairline(&colors),
        )
    } else {
        (
            colors.card,
            colors.text,
            crate::tokens::hairline(&colors),
        )
    };
    let who_owned = who.to_string();
    let body = w::column(ui)
        .gap(unit * 0.5)
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
                .radius(ui.theme().radii.large)
                .border(border)
                .border_width(1.0),
        )
        .child(w::text(ui, who_owned.clone()).variant("muted"))
        .child(w::text(ui, "").text(text));
    let row = w::row(ui)
        .align(Align::End)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    let mark = w::avatar(ui, &who_owned, 28.0, if user { colors.accent } else { colors.card });
    if user {
        row.justify(Justify::End).child(body).child(mark)
    } else {
        row.justify(Justify::Start).child(mark).child(body)
    }
}

fn plain_rich(text: &str) -> RichText {
    let mut rich = RichText::new();
    let mut code = TextStylePatch::default();
    code.families = Some(vec!["AhmiPrint".into()]);
    code.weight = Some(600.0);
    for (i, line) in text.split('\n').enumerate() {
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
    rich
}

fn todo_block(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || logic::todos_open(&g.todos.get()),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || {
                let col = w::column(&ui).gap(ui.theme().spacing.unit);
                w::for_each(&col, grove.todos, |t: &TodoRow| t.id.clone(), {
                    let ui = ui.clone();
                    move |t| {
                        let mark = logic::todo_mark(&t.status);
                        let line = if mark.is_empty() {
                            t.content.clone()
                        } else {
                            format!("{mark} {}", t.content)
                        };
                        w::text(&ui, line)
                    }
                })
            }
        },
    )
    .el()
}

fn process_block(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || g.streaming.get() && !g.process.get().is_empty(),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || {
                let col = w::column(&ui);
                w::for_each(&col, grove.process, |s: &String| s.clone(), {
                    let ui = ui.clone();
                    move |s| w::text(&ui, s.clone()).variant("muted")
                })
            }
        },
    )
    .el()
}

fn empty_state(ui: &Ui, grove: Grove, kind: &RoomKind) -> El {
    let starters: &'static [(&str, &str)] = match kind {
        RoomKind::Goal(_) => logic::goal_starters(),
        _ => logic::home_starters(),
    };
    let g = grove.clone();
    show(
        ui,
        move || {
            g.messages.get().is_empty() && g.partial.get().is_empty() && !g.streaming.get()
        },
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || {
                let unit = ui.theme().spacing.unit;
                let colors = ui.theme().colors.clone();
                let g = grove.clone();
                let size = crate::theme_pack::layout().presence_home;
                let face = crate::presence::mark(
                    &ui,
                    crate::presence::from_studio(&g.studio),
                    size,
                );
                let title = w::text(&ui, "")
                    .text_align(remedy_ui::style::TextAlign::Center)
                    .text(move || greeting_title(&g))
                    .style(
                        crate::tokens::typo(crate::tokens::HERO)
                            .foreground(colors.text),
                    )
                    .layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                    });
                let sub = w::text(
                    &ui,
                    "A goal, an errand, a receipt, a bug — tell me, and I’ll take it from here.",
                )
                .text_align(remedy_ui::style::TextAlign::Center)
                .style(crate::tokens::typo(crate::tokens::BODY).foreground(colors.muted))
                .layout(|l| {
                    l.max_width = Length::Px(400.0);
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                });
                let hints = w::text(&ui, hint_line(&grove.agent_gender.get()))
                    .text_align(remedy_ui::style::TextAlign::Center)
                    .style(crate::tokens::typo(crate::tokens::META).foreground(colors.muted))
                    .layout(|l| {
                        l.max_width = Length::Px(416.0);
                        l.width = Length::Percent(1.0);
                    });
                let mut row = w::wrap(&ui)
                    .gap(unit)
                    .justify(Justify::Center)
                    .layout(|l| l.width = Length::Percent(1.0));
                for (label, text) in starters {
                    let label = (*label).to_string();
                    let text = (*text).to_string();
                    let g = grove.clone();
                    let b = w::button(&ui, label, move || starter(&g, &text))
                        .variant("quiet")
                        .style(crate::tokens::chip(&colors, false));
                    row = row.child(b);
                }
                w::column(&ui)
                    .align(Align::Center)
                    .justify(Justify::Center)
                    .gap(unit * 3.0)
                    .padding(Insets::all(unit * 4.0))
                    .layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.grow = 1.0;
                        l.min_height = Length::Px(0.0);
                    })
                    .child(face)
                    .child(title)
                    .child(sub)
                    .child(row)
                    .child(hints)
            }
        },
    )
    .el()
}

fn starter(grove: &Grove, text: &str) {
    if logic::starter_prefills(text) {
        grove.prefill.set(Some(text.to_string()));
        grove.focus_composer();
    } else {
        let _ = grove.begin_send(text.to_string());
    }
}

fn greeting_title(grove: &Grove) -> String {
    // messages().text needs a Ui, but this is a plain string helper used
    // from a text closure. The English defaults match en-US.ftl hello keys.
    let hello = match logic::hello_key(grove.hour()) {
        "grove-hello-late" => "Good evening",
        "grove-hello-morning" => "Good morning",
        "grove-hello-afternoon" => "Good afternoon",
        _ => "Good evening",
    };
    let name = grove.studio.user_name.get();
    match name.split_whitespace().next() {
        Some(first) if !first.is_empty() => format!("{hello}, {first}"),
        _ => hello.to_string(),
    }
}

fn hint_line(_gender: &str) -> String {
    "Enter send · Shift+Enter new line · Attach · Speak · Enter steers mid-work".into()
}

fn steer_label(gender: &str) -> String {
    match gender.to_ascii_lowercase().as_str() {
        "male" => "Steer — he takes it in without stopping".into(),
        "neutral" => "Steer — they take it in without stopping".into(),
        _ => "Steer — she takes it in without stopping".into(),
    }
}

fn errors(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || {
            !g.plant_error.get().is_empty()
                || !g.mic_error.get().is_empty()
                || !g.attach_error.get().is_empty()
        },
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                w::text(&ui, "").variant("error").text({
                    let g = g.clone();
                    move || {
                        let plant = g.plant_error.get();
                        let mic = g.mic_error.get();
                        let attach = g.attach_error.get();
                        if !plant.is_empty() {
                            plant
                        } else if !mic.is_empty() {
                            mic
                        } else {
                            attach
                        }
                    }
                })
            }
        },
    )
    .el()
}

fn attach_rail(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || !g.attachments.get().is_empty() || g.uploading.get(),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || {
                let row = w::row(&ui).gap(ui.theme().spacing.unit);
                w::for_each(&row, grove.attachments, |a: &Attachment| format!("{}:{}", a.path, a.name), {
                    let ui = ui.clone();
                    let g = grove.clone();
                    move |a| {
                        let name = a.name.clone();
                        let idx_name = name.clone();
                        let g2 = g.clone();
                        w::row(&ui)
                            .align(Align::Center)
                            .gap(4.0)
                            .child(w::text(&ui, name))
                            .child(w::button(&ui, "Remove", move || {
                                let list = g2.attachments.get();
                                if let Some(i) = list.iter().position(|x| x.name == idx_name) {
                                    g2.remove_attachment(i);
                                }
                            }))
                    }
                });
                let g = grove.clone();
                let uploading = show(&ui, move || g.uploading.get(), {
                    let ui = ui.clone();
                    move || w::text(&ui, "uploading…").variant("muted")
                })
                .el();
                w::column(&ui).child(row).child(uploading)
            }
        },
    )
    .el()
}

fn turn_line(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || g.waiting_on_you() || g.streaming.get(),
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                let g = g.clone();
                w::text(&ui, "").variant("muted").text(move || {
                    if g.waiting_on_you() {
                        "Waiting for you…".to_string()
                    } else {
                        "Working…".to_string()
                    }
                })
            }
        },
    )
    .el()
}

fn ready(grove: &Grove) -> bool {
    grove.local_only || matches!(grove.studio.conn.get(), Conn::Ready)
}

fn placeholder_for(grove: &Grove, kind: &RoomKind) -> String {
    match kind {
        RoomKind::Home => "Tell me anything — a goal, an errand, a receipt, a bug…".into(),
        RoomKind::Alongside => "Tell me what to do — I’ll drive, you watch.".into(),
        RoomKind::Goal(id) => {
            let title = grove
                .goal(id)
                .map(|g| g.title)
                .filter(|t| !t.is_empty())
                .unwrap_or_else(|| "this plot".into());
            format!("Talk inside “{title}” — attach anything…")
        }
    }
}

fn composer(ui: &Ui, grove: Grove, app: Option<App>, kind: RoomKind) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.accent.mix_oklab(colors.bg, 0.65));
    let placeholder = placeholder_for(&grove, &kind);
    let field = w::text_area(ui, "Talk to Remedy")
        .placeholder(placeholder)
        .submit_on_enter(true);
    let f_change = field.clone();
    let g_change = grove.clone();
    let field = field.on_change(move || {
        let empty = f_change.value().trim().is_empty();
        g_change.draft_empty.set(empty);
    });
    // Enter sends. Shift+Enter stays a newline (the field keeps it). A composing
    // Enter is an IME confirm, not a send.
    let f_key = field.clone();
    let g_key = grove.clone();
    field.intercept_keys(move |e| {
        if !e.pressed || e.composing || e.key != Key::Enter {
            return false;
        }
        if e.modifiers.shift {
            return false;
        }
        send_field(&g_key, &f_key);
        true
    });

    let seen = Rc::new(Cell::new(grove.focus_nonce.get()));
    let el = field.el();
    let el_bind = el.clone();
    let f_bind = field.clone();
    let g_bind = grove.clone();
    let el = el.bind(move || {
        if let Some(text) = g_bind.prefill.get() {
            f_bind.set_value(&text);
            g_bind.prefill.set(None);
            g_bind.draft_empty.set(text.trim().is_empty());
            el_bind.focus();
        }
        let n = g_bind.focus_nonce.get();
        if n != seen.get() {
            seen.set(n);
            if n > 0 {
                el_bind.focus();
            }
        }
    });
    let g_dis = grove.clone();
    let el = el.disabled(move || !ready(&g_dis));

    // Idle copy lives in the field's placeholder. This line is only the
    // connecting and steer states, which replace that placeholder on desktop.
    let hint = show(
        ui,
        {
            let g = grove.clone();
            move || !ready(&g) || g.streaming.get()
        },
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                let g = g.clone();
                w::text(&ui, "").variant("muted").text(move || {
                    if !ready(&g) {
                        "Connecting…".to_string()
                    } else {
                        "Steer me — say it and I fold it in as I go".to_string()
                    }
                })
            }
        },
    )
    .el();

    let g_att = grove.clone();
    let app_att = app.clone();
    let attach = w::button(ui, "Attach", move || {
        if let Some(app) = &app_att {
            g_att.pick_files(app);
        } else {
            g_att
                .attach_error
                .set("The file picker is not available in this preview.".into());
        }
    })
    .semantics(Semantics::new(Role::Button).label("Attach a file, receipt, or image").action(Action::Click));

    let g_mic = grove.clone();
    let mic = w::button(ui, "Speak", move || g_mic.mic())
        .semantics(Semantics::new(Role::Button).label("Speak instead of typing").action(Action::Click));

    let g_stop = grove.clone();
    let pause = show(ui, move || g_stop.streaming.get(), {
        let ui = ui.clone();
        let g = grove.clone();
        move || {
            let g = g.clone();
            w::button(&ui, "Pause", move || g.stop())
                .semantics(Semantics::new(Role::Button).label("Pause").action(Action::Click))
        }
    })
    .el();

    let g_go = grove.clone();
    let f_go = field.clone();
    let send = w::button(
        ui,
        {
            let g = grove.clone();
            move || {
                if g.streaming.get() {
                    "Steer".to_string()
                } else {
                    "Send".to_string()
                }
            }
        },
        move || send_field(&g_go, &f_go),
    )
    .disabled({
        let g = grove.clone();
        move || {
            let no_files = g.attachments.get().is_empty();
            g.busy_ui.get() || g.uploading.get() || (g.draft_empty.get() && no_files)
        }
    })
    .semantics({
        let g = grove.clone();
        move || {
            let label = if g.streaming.get() {
                steer_label(&g.agent_gender.get())
            } else {
                "Send".into()
            };
            Semantics::new(Role::Button).label(label).action(Action::Click)
        }
    });

    let shell = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets::all(unit * 1.5))
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
        .child(el.layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(mic)
        .child(pause)
        .child(send);

    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 2.0,
            bottom: unit * 2.0,
            start: unit * 2.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(hint)
        .child(shell)
}

fn send_field(grove: &Grove, field: &remedy_ui::TextField) {
    let text = field.value();
    if grove.begin_send(text) {
        field.set_value("");
        grove.draft_empty.set(true);
    }
}


