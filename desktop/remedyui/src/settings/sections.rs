//! Every settings section, in the same order the desktop form uses.

use remedy_ui::{El, Ui};

use super::form;
use super::state::Settings;

pub fn all(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(super::sec_general::sections(ui, s))
        .child(super::sec_models::sections(ui, s))
        .child(super::sec_links::sections(ui, s))
        .child(super::sec_system::sections(ui, s))
}
