//! RemedyUI customize: theme packs on top of the selected palette.

use std::cell::RefCell;

use remedy_ui::customize::Customizations;
use remedy_ui::{App, Ui};

thread_local! {
    static LIVE: RefCell<Option<Customizations>> = const { RefCell::new(None) };
}

/// Turn on theme packs for this window. Live launches only; previews stay off
/// this slot so goldens keep the stock palettes.
pub fn attach(ui: &Ui, app: &App) {
    let custom = Customizations::new(crate::runtime::remedy_home());
    custom.enable_theme(ui.theme());
    let app = app.clone();
    custom.set_file_picker(move |dialog, done| {
        app.file_dialog(None, dialog, move |paths| done(paths));
    });
    custom.attach(ui);
    LIVE.with(|slot| *slot.borrow_mut() = Some(custom));
}

/// The live customizations, if this window attached them.
pub fn current() -> Option<Customizations> {
    LIVE.with(|slot| slot.borrow().clone())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn previews_have_no_live_packs() {
        assert!(current().is_none());
    }
}
