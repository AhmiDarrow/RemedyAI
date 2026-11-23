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

pub use slide::{bind_app, preview, rail};
