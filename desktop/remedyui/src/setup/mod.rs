//! First-run setup, the house-rules pop-up, and the update screen.

mod api;
mod chrome;
mod conversation;
mod downloads;
mod flow;
mod messengers;
mod mind;
mod model;
mod name;
mod offer;
mod splash;
mod update;
mod wizard;

pub(crate) use flow::host;

use remedy_ui::{App, WindowSpec};

use crate::fonts::{self, Fonts};

const PREVIEW_W: f32 = 1100.0;
const PREVIEW_H: f32 = 700.0;

fn open_preview(app: &App, title: &str, build: impl Fn(&remedy_ui::Ui) -> remedy_ui::El + 'static) {
    let title = title.to_string();
    app.open(
        WindowSpec::new(title).size(PREVIEW_W, PREVIEW_H),
        move |ui, _| {
            fonts::prepare_theme(ui, &Fonts::load());
            ui.root().child(build(ui));
        },
    );
}

/// Welcome card. Tests press Get Started and look for the provider choices.
pub fn preview_wizard(app: &App) {
    open_preview(app, "Remedy setup", |ui| {
        wizard::wizard_screen(ui, wizard::Wizard::preview())
    });
}

/// House-rules conversation, with Traditional setup and Skip all on the card.
pub fn preview_conversation(app: &App) {
    open_preview(app, "House rules", |ui| {
        conversation::conversation_view(ui, conversation::Conversation::preview())
    });
}

/// Mind download step. Traditional setup stays on the card.
pub fn preview_mind(app: &App) {
    open_preview(app, "Mind", |ui| mind::mind_view(ui, mind::Mind::preview()));
}

/// The update pop-up: some major things have changed. Let's go / Not now.
pub fn preview_offer(app: &App) {
    open_preview(app, "House rules offer", |ui| {
        offer::offer_overlay(ui, offer::Offer::preview())
    });
}

/// Ready to install. auto-start is off, so nothing downloads.
pub fn preview_update(app: &App) {
    open_preview(app, "Remedy Update", |ui| {
        update::update_screen(ui, update::Updater::preview())
    });
}
