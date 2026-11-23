//! Grove, the partner surface. Studio stays the workbench one tap away.
//!
//! Screens, the loopback client, and the storyline rules live in this module
//! so other desktop sessions are not editing `lib.rs` for Grove behavior.

mod api;
mod chat;
mod child;
mod decision;
mod head;
mod logic;
mod stage;
mod state;
mod store;
mod view;

pub(crate) use state::Grove;

use remedy_ui::{App, El, Ui};

/// Grove or, when the owner switched, a way back plus the Studio shell.
pub(crate) fn root(ui: &Ui, grove: Grove, app: Option<App>) -> El {
    view::root(ui, grove, app)
}

/// Morning Grove with example plots, for goldens. It does not touch disk or the network.
pub(crate) fn preview_home(ui: &Ui) -> El {
    view::frame(ui, Grove::preview(crate::Studio::preview()), None)
}
