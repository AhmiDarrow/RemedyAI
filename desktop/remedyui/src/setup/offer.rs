//! House-rules pop-up shown after an update. It is not the installer screen.
//!
//! Buttons clear `open`. `go` and `later` tell the gate which one was pressed.

use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;
use super::model::{OFFER_BODY, OFFER_PRIMARY, OFFER_SECONDARY, OFFER_TITLE};
use crate::logo;

pub fn offer_layer(ui: &Ui, open: Signal<bool>, go: Signal<bool>, later: Signal<bool>) -> El {
    chrome::when(
        ui,
        {
            let open = open.clone();
            move || open.get()
        },
        move || card(ui, open.clone(), go.clone(), later.clone()),
    )
}

fn card(ui: &Ui, open: Signal<bool>, go: Signal<bool>, later: Signal<bool>) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let open_go = open.clone();
    let body = w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| l.width = Length::Px(420.0))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 1.5)
                .child(logo::wordmark(ui, 36.0))
                .child(w::text(ui, OFFER_TITLE).variant("strong")),
        )
        .child(w::text(ui, OFFER_BODY).variant("muted"))
        .child(
            w::row(ui)
                .gap(unit)
                .align(Align::Center)
                .justify(Justify::End)
                .child(chrome::quiet(ui, OFFER_SECONDARY, {
                    let open = open.clone();
                    move || {
                        later.set(true);
                        open.set(false);
                    }
                }))
                .child(chrome::primary(ui, OFFER_PRIMARY, move || {
                    go.set(true);
                    open_go.set(false);
                })),
        );
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .style(StylePatch::new().background(colors.bg.mix_oklab(colors.text, 0.35)))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(
            w::column(ui)
                .padding(Insets::all(unit * 3.0))
                .style(
                    StylePatch::new()
                        .background(colors.card)
                        .radius(ui.theme().radii.large)
                        .border(line)
                        .border_width(1.0),
                )
                .child(body),
        )
}
