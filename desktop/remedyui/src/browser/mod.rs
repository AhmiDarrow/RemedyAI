//! In-app browser and the Rust computer-use host.
//!
//! The Tauri sources this follows are `useComputerHost.ts`, `browser_host.rs`,
//! `BrowserSlide.tsx`, and `layoutPrefs.ts`. Grove, settings, and packaging
//! stay out of this module.

mod actions;
mod bookmarks;
mod host;
mod layout;
mod oauth;
mod paths;
mod scripts;
mod shield;
mod slide;
mod ua;
mod url;

pub use host::live;
pub use slide::{bind_app, body, page, preview, preview_at, rail};

/// Start the poller and register the Browser slide. Safe to call again.
/// Loops wait for a UI-thread waker, so call this from the window callback.
pub fn boot(studio: crate::studio::Studio) {
    let _ = host::ensure(studio);
    crate::slides::set_browser_body(body);
}

/// Open `url` in the in-app browser. `keep_settings` leaves the Settings form mounted.
/// False when the host is not installed, so the caller can fall back to the OS browser.
pub fn open_in_rail(url: &str, keep_settings: bool) -> bool {
    let Some(model) = host::live() else { return false };
    if keep_settings {
        model.navigate_keeping_settings(url);
    } else {
        model.navigate_url(url, None);
    }
    true
}
