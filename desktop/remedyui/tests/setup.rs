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
    ONCE.call_once(|| unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") });
}

#[test]
fn wizard_get_started_opens_the_provider_step() {
    no_spawn();
    let mut app = TestApp::new(
        AppConfig::new("Remedy setup test"),
        remedy_desktop::preview_wizard,
    );
    assert!(
        app.find(Role::Button, "Get Started").is_some(),
        "welcome shows Get Started"
    );
    assert!(app.press(Role::Button, "Get Started"), "Get Started");
    assert!(
        app.find(Role::Button, "Demo · no signup").is_some(),
        "provider step shows Demo"
    );
}

#[test]
fn house_rules_offer_names_the_choice() {
    no_spawn();
    let app = TestApp::new(
        AppConfig::new("Remedy offer test"),
        remedy_desktop::preview_offer,
    );
    assert!(app.find(Role::Button, "Let's go").is_some(), "Let's go");
    assert!(app.find(Role::Button, "Not now").is_some(), "Not now");
}

#[test]
fn update_screen_offers_later_and_relaunch() {
    no_spawn();
    let app = TestApp::new(
        AppConfig::new("Remedy update test"),
        remedy_desktop::preview_update,
    );
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
    let app = TestApp::new(
        AppConfig::new("Remedy mind test"),
        remedy_desktop::preview_mind,
    );
    assert!(app.find(Role::Button, "Traditional setup").is_some());
}

#[test]
fn house_rules_offer_names_what_ships() {
    no_spawn();
    let app = TestApp::new(
        AppConfig::new("Remedy offer test"),
        remedy_desktop::preview_offer,
    );
    let names: Vec<String> = app
        .reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect();
    let blob = names.join("\n");
    assert!(blob.contains("smarter token saving"), "{blob}");
    assert!(blob.contains("A2A") || blob.contains("MCP"), "{blob}");
    assert!(
        blob.contains("house rules") || blob.contains("House rules"),
        "{blob}"
    );
    assert!(!blob.contains("built-in mind"), "{blob}");
}

#[test]
fn hosted_home_keeps_composer_and_name_at_install_size() {
    no_spawn();
    let mut app = TestApp::new(AppConfig::new("Remedy offer test"), move |app| {
        app.open(
            remedy_ui::WindowSpec::new("preview").size(1024.0, 642.0),
            move |ui, _| {
                ui.root().child(remedy_desktop::grove_home_offer(ui));
            },
        );
    });
    app.frames(2);
    assert!(app.find(Role::Button, "Let's go").is_some(), "Let's go");
    assert!(app.find(Role::Button, "Not now").is_some(), "Not now");
    let on_screen = |app: &TestApp, role: Role, name: &str| -> bool {
        app.find(role, name)
            .and_then(|el| el.bounds())
            .is_some_and(|r| r.min.y >= 0.0 && r.max.y <= 642.0 && r.max.y - r.min.y >= 8.0)
    };
    assert!(
        on_screen(&app, Role::Button, "Send") || on_screen(&app, Role::Button, "Attach"),
        "composer must be on screen, not only in the tree"
    );
    assert!(
        on_screen(&app, Role::Button, "Settings"),
        "status bar must stay at the bottom of the window"
    );
    let names: Vec<String> = app
        .reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect();
    assert!(
        names
            .iter()
            .any(|n| n == "Remedy" || n.starts_with("Remedy")),
        "partner name must not clip to Reme: {names:?}"
    );
    assert!(
        !names.iter().any(|n| n == "Reme"),
        "header name was truncated: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Your space")
            || n.contains("Good morning")
            || n.contains("Ready when you are")),
        "home content should be visible, not an empty well: {names:?}"
    );
    // The banner stays inside the window, below the 36 px title bar.
    if let Some(go) = app.find(Role::Button, "Let's go") {
        if let Some(r) = go.bounds() {
            assert!(r.min.y >= 36.0, "banner clipped off the top: {r:?}");
            assert!(r.max.y <= 642.0, "banner ran past the window: {r:?}");
            assert!(
                r.min.x >= 0.0 && r.max.x <= 1024.0,
                "banner ran past the sides: {r:?}"
            );
        }
    }
    assert!(app.press(Role::Button, "Not now"), "dismiss the banner");
    app.frames(2);
    assert!(
        app.find(Role::Button, "Let's go").is_none(),
        "banner dismissed"
    );
    assert!(
        app.find(Role::Button, "Send").is_some() || app.find(Role::Button, "Attach").is_some(),
        "composer still visible after Not now"
    );
}
