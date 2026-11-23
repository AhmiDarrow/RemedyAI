//! Setup, house-rules, and update screens on the in-memory host.
//!
//! `REMEDY_DESKTOP_NO_SPAWN` is set so nothing launches a sidecar. These tests do not
//! press Update & Relaunch.

use remedy_ui::AppConfig;
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use std::sync::Once;

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") }
    });
}

#[test]
fn wizard_get_started_opens_the_provider_step() {
    no_spawn();
    let mut app = TestApp::new(AppConfig::new("Remedy setup test"), remedy_desktop::preview_wizard);
    assert!(app.find(Role::Button, "Get Started").is_some(), "welcome shows Get Started");
    assert!(app.press(Role::Button, "Get Started"), "Get Started");
    assert!(
        app.find(Role::Button, "Demo · no signup").is_some(),
        "provider step shows Demo"
    );
}

#[test]
fn house_rules_offer_names_the_choice() {
    no_spawn();
    let app = TestApp::new(AppConfig::new("Remedy offer test"), remedy_desktop::preview_offer);
    assert!(app.find(Role::Button, "Let's go").is_some(), "Let's go");
    assert!(app.find(Role::Button, "Not now").is_some(), "Not now");
}

#[test]
fn update_screen_offers_later_and_relaunch() {
    no_spawn();
    let app = TestApp::new(AppConfig::new("Remedy update test"), remedy_desktop::preview_update);
    assert!(app.find(Role::Button, "Later").is_some(), "Later");
    assert!(
        app.find(Role::Button, "Update & Relaunch").is_some(),
        "Update & Relaunch"
    );
}

#[test]
fn conversation_keeps_traditional_setup_and_skip_all() {
    no_spawn();
    let app = TestApp::new(
        AppConfig::new("Remedy house rules test"),
        remedy_desktop::preview_conversation,
    );
    assert!(app.find(Role::Button, "Traditional setup").is_some());
    assert!(app.find(Role::Button, "Skip all, use defaults").is_some());
}

#[test]
fn mind_screen_offers_traditional_setup() {
    no_spawn();
    let app = TestApp::new(AppConfig::new("Remedy mind test"), remedy_desktop::preview_mind);
    assert!(app.find(Role::Button, "Traditional setup").is_some());
}
