//! Owner's manual: the offline Help wiki.
//!
//! The chapters are the same files the Tauri app bundles (`help/articles`,
//! `help/catalog`). Nothing here calls the runtime. [`HelpPanel::view`] is the
//! overlay RA-chrome mounts; [`HelpPanel::open`] shows a chapter. F1 and
//! Ctrl+/ stay with the chrome — this module does not bind them.

mod article;
mod catalog;
mod panel;
mod search;

pub use panel::HelpPanel;
