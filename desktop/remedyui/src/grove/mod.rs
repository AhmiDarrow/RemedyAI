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
mod marks;
mod stage;
mod state;
mod store;
mod view;

pub(crate) use logic::StudioKind;
pub(crate) use marks::PlotMark;
pub(crate) use state::{Grove, StudioHandoff};

use std::cell::RefCell;

use remedy_ui::{App, El, Ui};

thread_local! {
    static LIVE: RefCell<Option<Grove>> = const { RefCell::new(None) };
}

/// The Grove the window opened. Previews stay off this slot.
pub(crate) fn bind_live(grove: Grove) {
    LIVE.with(|slot| *slot.borrow_mut() = Some(grove));
}

fn live() -> Option<Grove> {
    LIVE.with(|slot| slot.borrow().clone())
}

pub(crate) fn unbind_live() {
    LIVE.with(|slot| *slot.borrow_mut() = None);
}

/// A star in the browser rail changed `url`. Grove prefs are a second store.
/// A live Grove is updated in memory so its next save does not put the old list back.
pub(crate) fn sync_browser_bookmark(url: &str, on: bool) {
    let url = url.trim();
    if url.is_empty() {
        return;
    }
    if let Some(grove) = live() {
        grove.apply_browser_star(url, on);
        return;
    }
    let mut prefs = store::load();
    if store::set_star(&mut prefs.bookmarks, url, on) {
        store::save(&prefs);
    }
}

/// Grove or, when the owner switched, a way back plus the Studio shell.
pub(crate) fn root(
    ui: &Ui,
    grove: Grove,
    app: Option<App>,
    offer: Option<crate::setup::Offer>,
) -> El {
    view::root(ui, grove, app, offer)
}

/// Morning Grove with example plots, for goldens. It does not touch disk or the network.
pub(crate) fn preview_home(ui: &Ui) -> El {
    view::frame(ui, Grove::preview(crate::Studio::preview()), None, None)
}

/// Morning Grove with the post-update house-rules banner open, for goldens.
pub(crate) fn preview_home_offer(ui: &Ui) -> El {
    view::frame(
        ui,
        Grove::preview(crate::Studio::preview()),
        None,
        Some(crate::setup::Offer::preview()),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn browser_star_updates_a_live_grove_without_a_second_copy() {
        let grove = Grove::new(crate::Studio::preview(), false);
        bind_live(grove.clone());
        sync_browser_bookmark("https://example.com/docs", true);
        sync_browser_bookmark("https://example.com/docs", true);
        let list = grove.bookmarks.get();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].url, "https://example.com/docs");
        sync_browser_bookmark("https://example.com/docs", false);
        assert!(grove.bookmarks.get().is_empty());
        unbind_live();
    }
}

/// Number of planted goals already in Grove prefs. Used by presence growth.
pub(crate) fn planted_goal_count() -> u32 {
    store::load().goal_sessions.len() as u32
}

pub(crate) fn remedy_home() -> std::path::PathBuf {
    store::remedy_home()
}
