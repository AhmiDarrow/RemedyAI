//! Usage and continuity dashboard.
//!
//! [`UsagePanel::view`] is the panel. [`UsagePanel::open`] puts it on a scrim.
//! Chrome can call either from the palette, menus, or a shortcut later.

mod api;
mod view;

pub use view::{UsageContext, UsageDashboard, UsagePanel, UsageWindow};
