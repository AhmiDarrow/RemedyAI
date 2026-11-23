//! House-rules pop-up shown after an update. It is not the installer screen.
//!
//! Buttons clear `open`. `go` and `later` tell the gate which one was pressed.
//! The copy comes from the server when it has any, otherwise the fallback that
//! says some major things have changed.

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;
use super::model::{OfferCopy, OFFER_BODY, OFFER_PRIMARY, OFFER_SECONDARY, OFFER_TITLE};
use crate::logo;

#[derive(Clone)]
pub struct Offer {
    pub open: Signal<bool>,
    pub title: Signal<String>,
    pub body: Signal<String>,
    pub primary: Signal<String>,
    pub secondary: Signal<String>,
    pub go: Signal<bool>,
    pub later: Signal<bool>,
}

impl Offer {
    pub fn closed() -> Self {
        Self::with(false)
    }

    /// Open, with the fallback that names the big changes.
    pub fn preview() -> Self {
        Self::with(true)
    }

    fn with(open: bool) -> Self {
        let copy = OfferCopy::default();
        Self {
            open: Signal::new(open),
            title: Signal::new(copy.title),
            body: Signal::new(copy.body),
            primary: Signal::new(copy.primary),
            secondary: Signal::new(copy.secondary),
            go: Signal::new(false),
            later: Signal::new(false),
        }
    }

    pub fn show(&self, copy: OfferCopy) {
        let copy = fill_blanks(copy);
        self.title.set(copy.title);
        self.body.set(copy.body);
        self.primary.set(copy.primary);
        self.secondary.set(copy.secondary);
        self.go.set(false);
        self.later.set(false);
        self.open.set(true);
    }
}

fn fill_blanks(copy: OfferCopy) -> OfferCopy {
    OfferCopy {
        title: if copy.title.trim().is_empty() { OFFER_TITLE.into() } else { copy.title },
        body: if copy.body.trim().is_empty() { OFFER_BODY.into() } else { copy.body },
        primary: if copy.primary.trim().is_empty() { OFFER_PRIMARY.into() } else { copy.primary },
        secondary: if copy.secondary.trim().is_empty() {
            OFFER_SECONDARY.into()
        } else {
            copy.secondary
        },
    }
}

pub fn offer_layer(ui: &Ui, offer: Offer) -> El {
    let open = offer.open.clone();
    let ui_body = ui.clone();
    chrome::when(ui, move || open.get(), move || card(&ui_body, offer.clone()))
}

/// Overlay with no fallback, so a dismissed pop-up does not cover the studio.
pub fn offer_overlay(ui: &Ui, offer: Offer) -> El {
    let open = offer.open.clone();
    let ui_body = ui.clone();
    remedy_ui::show(ui, move || open.get(), move || card(&ui_body, offer.clone())).el()
}

fn card(ui: &Ui, offer: Offer) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let title = offer.title.clone();
    let body = w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| l.width = Length::Px(420.0))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 1.5)
                .child(logo::mark(ui, 36.0))
                .child(w::text(ui, "").variant("strong").text({
                    let title = title.clone();
                    move || title.get()
                })),
        )
        .child(w::text(ui, "").variant("muted").text({
            let body = offer.body.clone();
            move || body.get()
        }))
        .child(
            w::row(ui)
                .gap(unit)
                .align(Align::Center)
                .justify(Justify::End)
                .child(chrome::quiet(ui, offer.secondary.get(), {
                    let offer = offer.clone();
                    move || {
                        offer.later.set(true);
                        offer.open.set(false);
                    }
                }))
                .child(chrome::primary(ui, offer.primary.get(), {
                    let offer = offer.clone();
                    move || {
                        offer.go.set(true);
                        offer.open.set(false);
                    }
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
        .semantics({
            let title = title.clone();
            move || Semantics::new(Role::Dialog).label(title.get()).modal(true)
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
