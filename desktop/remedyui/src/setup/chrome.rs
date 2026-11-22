//! Shared shell chrome: backdrop, card, chips, choice rows, buttons.

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::model::{self, STEP_LABELS};

pub fn when(ui: &Ui, on: impl Fn() -> bool + 'static, body: impl Fn() -> El + 'static) -> El {
    let u = ui.clone();
    show(ui, on, body).fallback(move || u.el("off")).el()
}

/// Centered card on the setup wash.
pub fn center(ui: &Ui, inner: El, width: f32) -> El {
    crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
    let colors = ui.theme().colors.clone();
    let wash = colors.accent.mix_oklab(colors.bg, 0.88);
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(Insets::all(ui.theme().spacing.unit * 3.0))
        .style(StylePatch::new().background(wash))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(card(ui, inner, width))
}

pub fn card(ui: &Ui, inner: El, width: f32) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 4.0))
        .style(
            StylePatch::new()
                .background(colors.card.mix_oklab(colors.bg, 0.08))
                .radius(ui.theme().radii.large)
                .border(line)
                .border_width(1.0),
        )
        .layout(|l| {
            l.width = Length::Px(width);
            l.max_width = Length::Percent(1.0);
            l.shrink = 1.0;
        })
        .child(inner)
}

pub fn progress_bar(ui: &Ui, fraction: Signal<Option<f32>>) -> El {
    w::progress(ui, "Setup progress", fraction).layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Px(8.0);
    })
}

/// Two chip rows. The active step is filled; earlier steps are quiet.
pub fn step_chips(ui: &Ui, step: Signal<usize>) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    for (row_i, range) in [(0usize, 4usize), (4, 7)].into_iter().enumerate() {
        let mut row = w::row(ui)
            .gap(unit)
            .align(Align::Center)
            .layout(|l| l.width = Length::Percent(1.0));
        for i in range.0..range.1 {
            row = row.child(chip(ui, STEP_LABELS[i], i, step.clone()));
        }
        let _ = row_i;
        col = col.child(row);
    }
    col
}

fn chip(ui: &Ui, label: &'static str, index: usize, step: Signal<usize>) -> El {
    let colors = ui.theme().colors.clone();
    let el = w::text(ui, label).variant("muted");
    let el2 = el.clone();
    let unit = ui.theme().spacing.unit;
    let wrapped = el
        .padding(Insets {
            top: unit * 0.4,
            end: unit * 1.2,
            bottom: unit * 0.4,
            start: unit * 1.2,
        })
        .style(StylePatch::new().radius(ui.theme().radii.small));
    let wrapped2 = wrapped.clone();
    wrapped.bind(move || {
        let cur = step.get();
        let (bg, fg) = if index == cur {
            (colors.accent, colors.on_accent)
        } else if index < cur {
            (colors.accent.mix_oklab(colors.bg, 0.75), colors.text)
        } else {
            (colors.panel, colors.muted)
        };
        wrapped2.style(
            StylePatch::new()
                .background(bg)
                .foreground(fg)
                .radius(ui.theme().radii.small),
        );
        let _ = &el2;
    })
}

/// A multi-line choice. The button label is the title, so tests can find it.
pub fn choice(
    ui: &Ui,
    title: &'static str,
    body: &'static str,
    selected: impl Fn() -> bool + 'static,
    on_press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let el = w::column(ui)
        .gap(unit * 0.4)
        .align(Align::Start)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| l.width = Length::Percent(1.0))
        .child(w::text(ui, title).variant("strong"))
        .child(w::text(ui, body).variant("muted"))
        .on_click(on_press)
        .semantics(Semantics::new(Role::Button).label(title).action(Action::Click));
    let el2 = el.clone();
    el.bind(move || {
        let on = selected();
        let bg = if on {
            colors.accent.mix_oklab(colors.bg, 0.82)
        } else {
            colors.panel
        };
        let border = if on { colors.accent } else { colors.line.unwrap_or(colors.muted) };
        el2.style(
            StylePatch::new()
                .background(bg)
                .border(border)
                .border_width(if on { 1.5 } else { 1.0 })
                .radius(ui.theme().radii.medium),
        );
    })
}

pub fn primary(ui: &Ui, label: impl Into<String>, on_press: impl Fn() + 'static) -> El {
    w::button(ui, label.into(), on_press).variant("primary")
}

pub fn quiet(ui: &Ui, label: impl Into<String>, on_press: impl Fn() + 'static) -> El {
    w::button(ui, label.into(), on_press).variant("quiet")
}

pub fn actions(ui: &Ui, children: Vec<El>) -> El {
    let mut row = w::row(ui)
        .gap(ui.theme().spacing.unit)
        .align(Align::Center)
        .justify(Justify::End)
        .layout(|l| l.width = Length::Percent(1.0));
    for child in children {
        row = row.child(child);
    }
    row
}

pub fn stack(ui: &Ui, children: Vec<El>) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit * 1.5)
        .layout(|l| l.width = Length::Percent(1.0));
    for child in children {
        col = col.child(child);
    }
    col
}

pub fn heading(ui: &Ui, text: &str) -> El {
    w::text(ui, text.to_string()).variant("heading")
}

pub fn sub(ui: &Ui, text: &str) -> El {
    w::text(ui, text.to_string()).variant("muted")
}

pub fn error_text(ui: &Ui, text: Signal<String>) -> El {
    let colors = ui.theme().colors.clone();
    when(
        ui,
        {
            let text = text.clone();
            move || !text.get().trim().is_empty()
        },
        move || {
            w::text(ui, "")
                .variant("muted")
                .text({
                    let text = text.clone();
                    move || text.get()
                })
                .style(StylePatch::new().foreground(colors.danger))
        },
    )
}

/// Fraction signal kept in step with the wizard index.
pub fn track_progress(step: Signal<usize>) -> Signal<Option<f32>> {
    let pct = Signal::new(Some(model::progress_fraction(step.get())));
    let pct2 = pct.clone();
    // The caller binds the view; this just seeds the value. Live updates happen in the view.
    let _ = pct2;
    pct
}
