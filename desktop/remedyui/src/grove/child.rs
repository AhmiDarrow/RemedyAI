//! A side job's room, and the floating chat with Remedy that opens over it.
//!
//! Words typed here go to Remedy, named for this job. She answers in the
//! bubble, and the same lines stay in her main thread.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::input::{Key, PointerPhase};
use remedy_ui::layout::{Align, Length, Position, AUTO};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::logic::{self, BubblePos, StoryMessage};
use super::state::{Grove, Place};

const BUBBLE_W: f32 = 340.0;
const BUBBLE_H: f32 = 380.0;
/// The desktop window. The widget has no live viewport here, so the bubble
/// clamps inside the same 1180×740 frame the app opens.
const VIEW_W: f32 = 1180.0;
const VIEW_H: f32 = 740.0;

pub(super) fn room(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let missing = {
        let g = grove.clone();
        let id = id.clone();
        show(ui, move || child_of(&g, &id).is_none(), {
            let ui = ui.clone();
            move || w::text(&ui, "This side job is no longer listed.")
        })
        .el()
    };
    let body = {
        let g = grove.clone();
        let id_flag = id.clone();
        let present_g = grove.clone();
        let present_id = id.clone();
        show(ui, move || child_of(&g, &id_flag).is_some(), {
            let ui = ui.clone();
            move || present(&ui, present_g.clone(), present_id.clone())
        })
        .el()
    };
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(missing)
        .child(body)
}

fn child_of(grove: &Grove, id: &str) -> Option<super::api::HeadChild> {
    grove.children.get().into_iter().find(|c| c.child_id == id)
}

fn present(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.grow = 1.0;
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(child_head(ui, grove.clone(), id.clone()))
        .child(child_feed(ui, grove.clone(), id.clone()))
        .child(child_talk(ui, grove, id))
}

fn child_head(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    let id_l = id.clone();
    let title = w::text(ui, "").variant("strong").text(move || {
        child_of(&g, &id_l)
            .map(|c| c.label)
            .unwrap_or_else(|| "Side job".into())
    });
    let g = grove.clone();
    let id_s = id.clone();
    let state = w::text(ui, "").text(move || {
        child_of(&g, &id_s)
            .map(|c| {
                let words = logic::state_words(&c.state);
                if logic::needs_owner(&c.state) {
                    format!("{words} · needs you")
                } else {
                    words.into()
                }
            })
            .unwrap_or_default()
    });
    let g = grove.clone();
    let id_t = id.clone();
    let task = w::text(ui, "").variant("muted").text(move || {
        let name = g.partner();
        child_of(&g, &id_t)
            .map(|c| format!("{name}’s side job · {}", c.task))
            .unwrap_or_default()
    });
    let g = grove.clone();
    let stop = show(
        ui,
        {
            let id = id.clone();
            move || {
                child_of(&g, &id)
                    .is_some_and(|c| c.state == "working" || c.state == "queued")
            }
        },
        {
            let ui = ui.clone();
            let g = grove.clone();
            let id = id.clone();
            move || {
                let g = g.clone();
                let id = id.clone();
                w::button(&ui, "Stop", move || {
                    if let Some(c) = child_of(&g, &id) {
                        g.stop_child(&c.child_id);
                    }
                })
                .variant("danger")
            }
        },
    )
    .el();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets::all(unit * 2.0))
        .child(
            w::column(ui)
                .gap(unit * 0.4)
                .layout(|l| l.grow = 1.0)
                .child(title)
                .child(state)
                .child(task),
        )
        .child(stop)
}

fn child_feed(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let list = w::column(ui).gap(unit).padding(Insets::all(unit * 2.0));
    let row_g = grove.clone();
    let row_id = id.clone();
    w::for_each(&list, grove.child_messages, |m: &StoryMessage| m.id.clone(), {
        let ui = ui.clone();
        move |m| {
            let who = if m.role == "user" {
                row_g.partner()
            } else {
                child_of(&row_g, &row_id)
                    .map(|c| c.label)
                    .unwrap_or_else(|| "Side job".into())
            };
            w::column(&ui)
                .child(w::text(&ui, who).variant("muted"))
                .child(w::text(&ui, m.content.clone()))
        }
    });
    let work_g = grove.clone();
    let work_id = id.clone();
    let working = show(
        ui,
        move || {
            child_of(&work_g, &work_id)
                .is_some_and(|c| c.state == "working" || c.state == "queued")
        },
        {
            let ui = ui.clone();
            move || w::text(&ui, "Working — new steps appear here as they happen").variant("muted")
        },
    )
    .el();
    let empty_watch = grove.clone();
    let empty_body = grove;
    let empty_id = id;
    let empty = show(
        ui,
        move || empty_watch.child_messages.get().is_empty(),
        {
            let ui = ui.clone();
            move || {
                let empty_body = empty_body.clone();
                let empty_id = empty_id.clone();
                w::text(&ui, "").variant("muted").text(move || {
                    if empty_body.child_loading.get() {
                        "Opening…".to_string()
                    } else if child_of(&empty_body, &empty_id)
                        .is_some_and(|c| c.state == "working" || c.state == "queued")
                    {
                        "Starting up…".to_string()
                    } else {
                        "Nothing here yet.".to_string()
                    }
                })
            }
        },
    )
    .el();
    w::scroll(ui).child(list).child(working).child(empty)
}

fn child_talk(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    let prompt = move || {
        format!(
            "Tell {} about this job — she’ll answer right here",
            g.partner()
        )
    };
    let field = w::text_input(ui, "Message Remedy about this side job")
        .placeholder("Tell Remedy about this job — she’ll answer right here");
    let label_field = field.clone();
    let g_label = grove.clone();
    let id_label = id.clone();
    let _ = field.el().bind(move || {
        let job = child_of(&g_label, &id_label)
            .map(|c| c.label)
            .unwrap_or_else(|| "this side job".into());
        let _ = label_field
            .clone()
            .label(format!("Message {} about {job}", g_label.partner()));
    });
    let empty = Rc::new(Cell::new(true));
    let f = field.clone();
    let empty_c = empty.clone();
    let field = field.on_change(move || empty_c.set(f.value().trim().is_empty()));
    let f = field.clone();
    let g = grove.clone();
    let id_key = id.clone();
    field.intercept_keys(move |e| {
        if e.pressed && !e.composing && e.key == Key::Enter && !e.modifiers.shift {
            let label = child_of(&g, &id_key)
                .map(|c| c.label)
                .unwrap_or_else(|| "this side job".into());
            if g.send_about_child(&label, &f.value()) {
                f.set_value("");
            }
            return true;
        }
        false
    });
    let f = field.clone();
    let g = grove.clone();
    let go = w::button(ui, "Send", move || {
        let label = child_of(&g, &id)
            .map(|c| c.label)
            .unwrap_or_else(|| "this side job".into());
        if g.send_about_child(&label, &f.value()) {
            f.set_value("");
        }
    })
    .disabled(move || empty.get());
    w::column(ui)
        .gap(unit * 0.5)
        .padding(Insets::all(unit * 2.0))
        .child(w::text(ui, "").variant("muted").text(prompt))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                .child(field.el().layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                }))
                .child(go),
        )
}

pub(super) fn bubble_layer(ui: &Ui, grove: Grove) -> El {
    let host = ui.el("grove-bubble").column();
    let host_l = host.clone();
    let g = grove.clone();
    let host = host.bind(move || {
        let on = g.bubble_from.get().is_some() && matches!(g.place.get(), Place::Child(_));
        let _ = host_l.clone().layout(|l| {
            l.position = Position::Absolute;
            if !on {
                l.width = Length::Px(0.0);
                l.height = Length::Px(0.0);
                return;
            }
            let p = g.bubble_pos.get();
            l.width = Length::Px(BUBBLE_W);
            l.height = Length::Px(BUBBLE_H);
            l.inset = Insets {
                top: p.y,
                end: AUTO,
                bottom: AUTO,
                start: p.x,
            };
        });
    });
    let g = grove.clone();
    let body = show(
        ui,
        move || g.bubble_from.get().is_some() && matches!(g.place.get(), Place::Child(_)),
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || bubble(&ui, grove.clone())
        },
    )
    .el();
    host.child(body)
}

fn bubble(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let drag: Rc<Cell<Option<(f32, f32)>>> = Rc::new(Cell::new(None));
    let head = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets::all(unit))
        .child(crate::logo::mark(ui, 16.0))
        .child(w::text(ui, "").text({
            let g = grove.clone();
            move || g.partner()
        }))
        .child(w::text(ui, "Also in her main thread").variant("muted"))
        .child({
            let g = grove.clone();
            w::button(ui, "Close", move || g.bubble_from.set(None))
                .semantics(Semantics::new(Role::Button).label("Close chat").action(Action::Click))
        })
        .on_pointer({
            let g = grove.clone();
            let drag = drag.clone();
            move |e| on_drag(e, &g, drag.as_ref())
        })
        .on_key({
            let g = grove.clone();
            move |e| on_nudge(e, &g)
        })
        .semantics(Semantics::new(Role::Button).label("Move chat (arrow keys)").action(Action::Click));
    let list = w::column(ui).gap(unit).padding(Insets::all(unit));
    let items = remedy_ui::reactive::Signal::new(Vec::<(String, String, String)>::new());
    let pub_el = ui.el("grove-bubble-slice").bind({
        let g = grove.clone();
        let items = items;
        move || {
            let anchor = g.bubble_from.get().unwrap_or_default();
            let rows: Vec<(String, String, String)> = logic::bubble_slice(&g.messages.get(), &anchor)
                .into_iter()
                .map(|m| (m.id, m.role, m.text))
                .collect();
            items.set(rows);
        }
    });
    w::for_each(&list, items, |m: &(String, String, String)| m.0.clone(), {
        let ui = ui.clone();
        let g = grove.clone();
        move |m| {
            let who = if m.1 == "user" {
                let n = g.studio.user_name.get();
                if n.trim().is_empty() { "You".into() } else { n }
            } else {
                g.partner()
            };
            w::column(&ui)
                .child(w::text(&ui, who).variant("muted"))
                .child(w::text(&ui, m.2.clone()))
        }
    });
    let live = show(ui, {
        let g = grove.clone();
        move || g.streaming.get()
    }, {
        let ui = ui.clone();
        let g = grove.clone();
        move || {
            let g = g.clone();
            w::text(&ui, "").text(move || {
                let t = g.partial.get();
                if t.is_empty() { "…".to_string() } else { t }
            })
        }
    })
    .el();
    let field = w::text_input(ui, "Reply to Remedy").placeholder("Reply to Remedy…");
    let label_field = field.clone();
    let g_label = grove.clone();
    let _ = field.el().bind(move || {
        let _ = label_field
            .clone()
            .label(format!("Reply to {}", g_label.partner()));
    });
    let f = field.clone();
    let g = grove.clone();
    field.intercept_keys(move |e| {
        if e.pressed && !e.composing && e.key == Key::Enter {
            let label = current_label(&g);
            if g.send_about_child(&label, &f.value()) {
                f.set_value("");
            }
            return true;
        }
        false
    });
    let f = field.clone();
    let g = grove;
    let go = w::button(ui, "Send", move || {
        let label = current_label(&g);
        if g.send_about_child(&label, &f.value()) {
            f.set_value("");
        }
    });
    w::column(ui)
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text)
                .radius(ui.theme().radii.large)
                .border(colors.line.unwrap_or(colors.muted))
                .border_width(1.0),
        )
        .layout(|l| {
            l.width = Length::Px(BUBBLE_W);
            l.height = Length::Px(BUBBLE_H);
        })
        .child(head)
        .child(w::scroll(ui).child(list).child(live).child(pub_el))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                .padding(Insets::all(unit))
                .child(field.el().layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                }))
                .child(go),
        )
}

fn current_label(grove: &Grove) -> String {
    let Place::Child(id) = grove.place.get() else {
        return "this side job".into();
    };
    child_of(grove, &id)
        .map(|c| c.label)
        .unwrap_or_else(|| "this side job".into())
}

fn on_drag(e: &remedy_ui::input::PointerEvent, grove: &Grove, drag: &Cell<Option<(f32, f32)>>) -> bool {
    match e.phase {
        PointerPhase::Down => {
            let p = grove.bubble_pos.get();
            drag.set(Some((e.position.x - p.x, e.position.y - p.y)));
            true
        }
        PointerPhase::Move => {
            if let Some((dx, dy)) = drag.get() {
                let next = logic::clamp_bubble(
                    BubblePos { x: e.position.x - dx, y: e.position.y - dy },
                    VIEW_W,
                    VIEW_H,
                );
                grove.bubble_pos.set(next);
            }
            drag.get().is_some()
        }
        PointerPhase::Up | PointerPhase::Cancel => {
            if drag.get().is_some() {
                drag.set(None);
                grove.save();
                true
            } else {
                false
            }
        }
    }
}

fn on_nudge(e: &remedy_ui::input::KeyEvent, grove: &Grove) -> bool {
    if !e.pressed || e.composing {
        return false;
    }
    if e.key == Key::Escape {
        grove.bubble_from.set(None);
        return true;
    }
    let step = if e.modifiers.shift { 40.0 } else { 12.0 };
    let (dx, dy) = match e.key {
        Key::ArrowLeft => (-step, 0.0),
        Key::ArrowRight => (step, 0.0),
        Key::ArrowUp => (0.0, -step),
        Key::ArrowDown => (0.0, step),
        _ => return false,
    };
    let p = grove.bubble_pos.get();
    let next = logic::clamp_bubble(BubblePos { x: p.x + dx, y: p.y + dy }, VIEW_W, VIEW_H);
    grove.bubble_pos.set(next);
    grove.save();
    true
}
