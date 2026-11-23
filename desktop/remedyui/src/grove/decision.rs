//! One decision at a time, docked above the talk bar.
//!
//! Y / N / E / ? / Esc apply only while the card itself has focus, so typing
//! in the composer is not a decision. A new unseen decision untucks the card.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::api::Approval;
use super::logic::{self, CommandExplain};
use super::state::Grove;

pub(super) fn layer(ui: &Ui, grove: Grove) -> El {
    let card = card(ui, grove);
    ui.el("grove-decision")
        .column()
        .align(Align::Center)
        .layout(|l| {
            l.position = remedy_ui::layout::Position::Absolute;
            l.inset = Insets {
                top: remedy_ui::layout::AUTO,
                end: 0.0,
                bottom: 92.0,
                start: 0.0,
            };
            l.width = Length::Percent(1.0);
        })
        .child(card)
}

fn card(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    let open = show(
        ui,
        move || g.current_decision().is_some() && !g.tucked.get(),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || open_card(&ui, grove.clone())
        },
    )
    .el();
    let g = grove.clone();
    let pill = show(
        ui,
        move || g.current_decision().is_some() && g.tucked.get(),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || {
                let g = grove.clone();
                w::button(
                    &ui,
                    move || {
                        let n = g.ordered_decisions().len();
                        if n == 1 {
                            "1 decision waiting".into()
                        } else {
                            format!("{n} decisions waiting")
                        }
                    },
                    {
                        let g = grove.clone();
                        move || g.tuck(false)
                    },
                )
                .semantics(
                    Semantics::new(Role::Button)
                        .label("Show the decision waiting for you")
                        .action(Action::Click),
                )
            }
        },
    )
    .el();
    let g = grove.clone();
    let flash = show(
        ui,
        move || g.current_decision().is_none() && g.flash.get().is_some(),
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                w::text(&ui, "").text({
                    let g = g.clone();
                    move || g.flash.get().map(|(t, _)| t).unwrap_or_default()
                })
            }
        },
    )
    .el();
    w::column(ui).child(open).child(pill).child(flash)
}

fn open_card(ui: &Ui, grove: Grove) -> El {
    let ui = ui.clone();
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let seen = Rc::new(Cell::new(String::new()));
    let body = w::column(ui)
        .gap(unit * 1.5)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.max_width = Length::Px(560.0);
        });
    let body2 = body.clone();
    let g_focus = grove.clone();
    let theme_ui = ui.clone();
    let body = body.bind(move || {
        let id = g_focus
            .current_decision()
            .map(|d| d.id)
            .unwrap_or_default();
        let sensitive = g_focus
            .current_decision()
            .map(|d| d.sensitive)
            .unwrap_or(false);
        let border = if sensitive { colors.warn } else { colors.accent };
        let _ = body2.clone().style(
            StylePatch::new()
                .background(colors.card)
                .foreground(colors.text)
                .radius(theme_ui.theme().radii.large)
                .border(border)
                .border_width(if sensitive { 2.0 } else { 1.0 }),
        );
        if !id.is_empty() && seen.get() != id && !g_focus.tucked.get() {
            seen.set(id);
            body2.focus();
        }
    });
    let g_key = grove.clone();
    let body = body.on_key(move |e| on_decision_key(e, &g_key)).semantics({
        let g = grove.clone();
        move || {
            let headline = g
                .current_decision()
                .map(|d| headline_of(&d))
                .unwrap_or_default();
            Semantics::new(Role::Dialog).label(headline).modal(false)
        }
    });

    let g = grove.clone();
    let kicker = w::text(ui, "").text(move || {
        let Some(d) = g.current_decision() else {
            return String::new();
        };
        if d.sensitive {
            "Your call — money, password or send".into()
        } else {
            "Remedy is asking".into()
        }
    });
    let g = grove.clone();
    let count = w::text(ui, "").variant("muted").text(move || {
        let n = g.ordered_decisions().len();
        if n > 1 {
            format!("1 of {n}")
        } else {
            String::new()
        }
    });
    let g = grove.clone();
    let later = w::button(ui, "Later", move || g.tuck(true));
    let top = w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit)
        .child(kicker)
        .child(count)
        .child(later);

    let g = grove.clone();
    let headline = w::heading(ui, 3, move || {
        g.current_decision()
            .map(|d| {
                let h = headline_of(&d);
                if h.is_empty() {
                    "Remedy needs your OK to continue.".into()
                } else {
                    h
                }
            })
            .unwrap_or_default()
    });
    let g = grove.clone();
    let sub = w::text(ui, "").text(move || {
        let Some(d) = g.current_decision() else {
            return String::new();
        };
        if d.sensitive {
            "Nothing happens until you say yes. This step always asks, in every mode.".into()
        } else if d.blocking {
            "Remedy is paused here until you decide.".into()
        } else {
            "Remedy will pick this up once you decide.".into()
        }
    });
    let explain = explain_block(ui, grove.clone());
    let actions = actions(ui, grove);
    body.child(top).child(headline).child(sub).child(explain).child(actions)
}

fn headline_of(d: &Approval) -> String {
    logic::approval_headline(&d.summary, &d.reason, &d.origin, &d.channel)
}

fn explain_block(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || {
            let id = g.explain_id.get();
            g.current_decision().is_some_and(|d| id.as_deref() == Some(d.id.as_str()))
        },
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                let Some(d) = g.current_decision() else {
                    return ui.el("grove-explain-empty");
                };
                let head = headline_of(&d);
                let mut col = w::column(&ui).gap(ui.theme().spacing.unit);
                if !logic::is_generic_reason(&d.reason) && d.reason != head {
                    col = col.child(w::text(&ui, d.reason.clone()));
                }
                col = col.child(w::text(&ui, "Exactly what Remedy would do:").variant("muted"));
                match logic::explain_command(&d.command) {
                    CommandExplain::Raw(s) => col = col.child(w::text(&ui, s).variant("code")),
                    CommandExplain::Fields(rows) => {
                        for row in rows {
                            col = col.child(w::text(&ui, format!("{}: {}", row.label, row.value)));
                        }
                    }
                }
                col
            }
        },
    )
    .el()
}

fn actions(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    let yes = w::button(
        ui,
        move || {
            if g.decision_busy.get() {
                "Working…".into()
            } else if g.current_decision().is_some_and(|d| d.sensitive) {
                "Yes, go ahead".into()
            } else {
                "Yes".into()
            }
        },
        {
            let g = grove.clone();
            move || g.decide(true, "once")
        },
    )
    .variant("primary")
    .disabled({
        let g = grove.clone();
        move || g.decision_busy.get()
    });
    let g = grove.clone();
    let no = w::button(
        ui,
        move || {
            if g.current_decision().is_some_and(|d| d.sensitive) {
                "Not now".into()
            } else {
                "No".into()
            }
        },
        {
            let g = grove.clone();
            move || g.decide(false, "once")
        },
    )
    .disabled({
        let g = grove.clone();
        move || g.decision_busy.get()
    });
    let g = grove.clone();
    let explain = w::button(
        ui,
        move || {
            let open = g
                .current_decision()
                .is_some_and(|d| g.explain_id.get().as_deref() == Some(d.id.as_str()));
            if open { "Hide details".into() } else { "Explain".into() }
        },
        {
            let g = grove.clone();
            move || g.toggle_explain()
        },
    );
    let g = grove.clone();
    let allow = show(
        ui,
        move || g.current_decision().is_some_and(|d| !d.sensitive),
        {
            let ui = ui.clone();
            let g = grove.clone();
            move || {
                w::button(&ui, "Allow for this chat", move || g.decide(true, "session")).disabled({
                    let g = g.clone();
                    move || g.decision_busy.get()
                })
            }
        },
    )
    .el();
    w::row(ui)
        .gap(unit)
        .align(Align::Center)
        .child(yes)
        .child(no)
        .child(explain)
        .child(allow)
}

fn on_decision_key(e: &remedy_ui::input::KeyEvent, grove: &Grove) -> bool {
    if !e.pressed || e.composing || e.modifiers.ctrl || e.modifiers.alt || e.modifiers.meta {
        return false;
    }
    if e.key == Key::char('y') {
        grove.decide(true, "once");
        true
    } else if e.key == Key::char('n') {
        grove.decide(false, "once");
        true
    } else if e.key == Key::char('e') || e.key == Key::char('?') {
        grove.toggle_explain();
        true
    } else if e.key == Key::Escape {
        grove.tuck(true);
        true
    } else {
        false
    }
}
