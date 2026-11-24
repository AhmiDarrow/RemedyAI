//! Form pieces shared by every settings section.
//!
//! Labels sit above controls. Hints use the small text style so a 320 px rail
//! still fits 16 px body labels. Sections stay mounted while the panel is open
//! and hide with `hidden`, so a typed API key survives collapsing a section.

use remedy_ui::core::{Color, Insets};
use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::style::StylePatch;
use remedy_ui::view::IntoProp;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::logic;
use super::state::{self, Settings};

pub fn gap(ui: &Ui) -> f32 {
    ui.theme().spacing.unit
}

pub fn stack(ui: &Ui) -> El {
    w::column(ui).gap(gap(ui) * 2.0).fill_width()
}

pub fn hint(ui: &Ui, text: impl IntoProp<String>) -> El {
    w::text(ui, text).variant("small")
}

pub fn label(ui: &Ui, text: &str) -> El {
    w::text(ui, text.to_string()).variant("strong")
}

pub fn notice(ui: &Ui, tone: &str, text: impl IntoProp<String>) -> El {
    let colors = ui.theme().colors.clone();
    let tint = match tone {
        "error" => colors.danger,
        "warn" => colors.warn,
        "ok" => colors.ok,
        _ => colors.accent,
    };
    w::text(ui, text)
        .variant(if tone == "error" { "error" } else { "small" })
        .style(
            StylePatch::new()
                .background(colors.card.mix_oklab(tint, 0.18))
                .foreground(colors.text)
                .radius(ui.theme().radii.small)
                .padding(Insets::all(gap(ui) * 1.5)),
        )
}

pub fn bound_line(
    ui: &Ui,
    name: impl Into<String>,
    value: Signal<String>,
    placeholder: &str,
) -> El {
    let field = w::text_input(ui, name).placeholder(placeholder);
    let initial = value.get();
    if !initial.is_empty() {
        field.set_value(&initial);
    }
    let watch = field.clone();
    field.observe_text(move || {
        value.set_if_changed(watch.value());
    });
    let sync = field.clone();
    let el = field.el().layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
        l.shrink = 1.0;
    });
    el.clone().bind(move || {
        let next = value.get();
        if sync.value() != next {
            sync.set_value(&next);
        }
    });
    el
}

pub fn secret_line(ui: &Ui, name: &str) -> El {
    let field = w::secret_input(ui, name).placeholder("Leave blank to keep the saved key");
    state::store_api_key(field.clone());
    w::row(ui)
        .align(Align::Center)
        .gap(gap(ui))
        .fill_width()
        .child(field.el().grow(1.0))
        .child(w::reveal_toggle(ui, &field))
}

pub fn pick(ui: &Ui, name: &str, pairs: &[(String, String)], value: Signal<String>) -> El {
    let mut ids: Vec<String> = pairs.iter().map(|(id, _)| id.clone()).collect();
    let mut labels: Vec<String> = pairs.iter().map(|(_, label)| label.clone()).collect();
    if ids.is_empty() {
        ids.push(String::new());
        labels.push("—".into());
    }
    let current = value.get();
    let start = ids.iter().position(|id| id == &current).unwrap_or(0);
    let selected = Signal::new(start);
    let el = w::select(ui, name, labels, selected);
    let ids_from_index = ids.clone();
    Effect::new(move || {
        let i = selected.get();
        if let Some(id) = ids_from_index.get(i) {
            value.set_if_changed(id.clone());
        }
    });
    let ids_from_value = ids;
    Effect::new(move || {
        let current = value.get();
        let i = ids_from_value
            .iter()
            .position(|id| id == &current)
            .unwrap_or(0);
        selected.set_if_changed(i);
    });
    el
}

pub fn segments(ui: &Ui, name: &str, pairs: &[(&str, &str)], value: Signal<String>) -> El {
    let _ = name;
    if pairs.is_empty() {
        return hint(ui, "No choices.");
    }
    let colors = ui.theme().colors.clone();
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(crate::tokens::SPACE * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        });
    for (id, label) in pairs {
        let id = (*id).to_string();
        let value = value.clone();
        let colors = colors.clone();
        row = row.child(
            w::button(ui, (*label).to_string(), {
                let value = value.clone();
                let id = id.clone();
                move || value.set_if_changed(id.clone())
            })
            .variant("quiet")
            .style(move || crate::tokens::chip(&colors, value.get() == id).weight(550.0)),
        );
    }
    row
}

pub fn toggle(ui: &Ui, title: &str, description: &str, value: Signal<bool>) -> El {
    let col = stack(ui);
    let col = col.child(w::switch(ui, title.to_string(), value));
    if description.is_empty() {
        col
    } else {
        col.child(hint(ui, description.to_string()))
    }
}

pub fn action(
    ui: &Ui,
    title: impl Into<String>,
    variant: &'static str,
    on_press: impl Fn() + 'static,
) -> El {
    if variant == "primary" {
        w::button(ui, title.into(), on_press)
            .variant("quiet")
            .style(crate::tokens::primary_button(&ui.theme().colors))
    } else {
        w::button(ui, title.into(), on_press).variant(variant)
    }
}

pub fn flow(
    ui: &Ui,
    title: &str,
    summary: &str,
    immediate: bool,
    build: impl Fn(&Ui) -> El + 'static,
) -> El {
    let title_owned = title.to_string();
    let build: std::rc::Rc<dyn Fn(&Ui) -> El> = std::rc::Rc::new(build);
    let open_title = title_owned.clone();
    let open_build = build.clone();
    let ui_open = ui.clone();
    w::column(ui)
        .gap(gap(ui) * 0.5)
        .fill_width()
        .child(w::text(ui, title_owned).variant("strong"))
        .child(hint(ui, summary.to_string()))
        .child(action(ui, "Open", "quiet", move || {
            open_flow(&ui_open, open_title.clone(), immediate, open_build.clone());
        }))
}

fn open_flow(ui: &Ui, title: String, immediate: bool, build: std::rc::Rc<dyn Fn(&Ui) -> El>) {
    let note = if immediate {
        "Changes apply as you make them."
    } else {
        "Use Save in Settings to apply your changes."
    };
    let holder: std::rc::Rc<std::cell::RefCell<Option<w::Modal>>> =
        std::rc::Rc::new(std::cell::RefCell::new(None));
    let close_holder = holder.clone();
    let done = action(ui, "Done", "primary", move || {
        if let Some(modal) = close_holder.borrow().as_ref() {
            modal.close();
        }
    });
    let body = stack(ui)
        .child(build(ui))
        .child(hint(ui, note.to_string()))
        .child(done);
    let modal = w::modal(ui, title, body, true);
    *holder.borrow_mut() = Some(modal);
}

/// A collapsible section. Hidden (no gap) when the category, mode, or search
/// leaves it out. The body stays mounted so fields keep what was typed.
pub fn block(
    ui: &Ui,
    s: Settings,
    id: &'static str,
    body: impl Fn(&Ui, Settings) -> El + 'static,
) -> El {
    let Some(sec) = logic::section(id) else {
        return ui.el("settings-missing");
    };
    let colors = ui.theme().colors.clone();
    let title = logic::shown_title(sec).to_string();
    let summary = logic::shown_summary(sec).to_string();
    let mark = w::text(ui, {
        let s = s;
        move || -> String {
            if s.is_open(id) {
                "▾".into()
            } else {
                "▸".into()
            }
        }
    });
    let head = w::row(ui)
        .align(Align::Center)
        .gap(gap(ui))
        .fill_width()
        .child(mark)
        .child(w::text(ui, title).variant("strong").grow(1.0))
        .on_click(move || {
            if s.is_open(id) {
                s.note_closed(id);
            } else {
                s.note_opened(id);
            }
        });
    let sum = hint(ui, summary);
    let sum_flag = sum.clone();
    sum.clone().bind(move || {
        sum_flag.clone().hidden(s.is_open(id));
    });
    let inner = body(ui, s);
    let inner_flag = inner.clone();
    inner.clone().bind(move || {
        inner_flag.clone().hidden(!s.is_open(id));
    });
    let card = stack(ui)
        .padding(Insets::all(gap(ui) * 2.0))
        .style(
            StylePatch::new()
                .background(colors.card)
                .radius(ui.theme().radii.medium)
                .border(colors.line.unwrap_or(colors.muted))
                .border_width(1.0),
        )
        .child(head)
        .child(sum)
        .child(inner);
    let card_flag = card.clone();
    card.clone().bind(move || {
        let visible =
            logic::section_visible(sec, &s.category.get(), &s.mode.get(), &s.search.get());
        card_flag.clone().hidden(!visible);
    });
    card
}

pub fn swatch(ui: &Ui, bg: Color, accent: Color, split: Option<Color>) -> El {
    let left = ui
        .el("swatch-l")
        .style(StylePatch::new().background(bg))
        .layout(|l| {
            l.width = Length::Px(14.0);
            l.height = Length::Px(14.0);
        });
    let right_color = split.unwrap_or(accent);
    let right = ui
        .el("swatch-r")
        .style(StylePatch::new().background(right_color))
        .layout(|l| {
            l.width = Length::Px(14.0);
            l.height = Length::Px(14.0);
        });
    w::row(ui).child(left).child(right)
}

pub fn parse_hex(raw: &str) -> Option<Color> {
    Color::from_hex(raw.trim()).ok()
}
