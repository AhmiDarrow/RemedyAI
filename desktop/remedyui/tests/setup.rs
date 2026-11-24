//! Setup, house-rules, and update screens on the in-memory host.
//!
//! `REMEDY_DESKTOP_NO_SPAWN` is set so nothing launches a sidecar. These tests do not
//! start a download. The update preview looks for Restart.

use remedy_ui::AppConfig;
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use std::sync::Once;

fn names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect()
}

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") });
}

#[test]
fn wizard_more_options_opens_the_provider_step() {
    no_spawn();
    let mut app = TestApp::new(
        AppConfig::new("Remedy setup test"),
        remedy_desktop::preview_wizard,
    );
    assert!(
        app.find(Role::Button, "Use the recommended setup").is_some(),
        "welcome shows the recommended CTA"
    );
    assert!(
        app.find(Role::Button, "More options").is_some(),
        "welcome shows More options"
    );
    assert!(app.press(Role::Button, "More options"), "More options");
    assert!(
        app.find(Role::Button, "On this PC").is_some(),
        "provider step leads with On this PC"
    );
    assert!(
        app.find(Role::Button, "Demo · no signup").is_some(),
        "provider step still shows Demo"
    );
    let names = names(&app);
    assert!(
        names.iter().any(|n| n.contains("Or a free key")),
        "provider step offers a free-key list: {names:?}"
    );
}

#[test]
fn wizard_recommended_setup_skips_to_the_folder_step() {
    no_spawn();
    let mut app = TestApp::new(
        AppConfig::new("Remedy setup recommended"),
        remedy_desktop::preview_wizard,
    );
    assert!(
        app.press(Role::Button, "Use the recommended setup"),
        "Use the recommended setup"
    );
    assert!(
        app.find(Role::Button, "Demo · no signup").is_none(),
        "recommended path skips the provider step"
    );
    assert!(
        app.find(Role::Button, "Continue").is_some(),
        "folder step is next"
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
fn update_notice_offers_restart() {
    no_spawn();
    let app = TestApp::new(
        AppConfig::new("Remedy update test"),
        remedy_desktop::preview_update,
    );
    assert!(
        app.find(Role::Button, "Restart").is_some(),
        "Restart"
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


#[test]
fn status_dock_stays_on_screen_in_hosted_grove() {
    no_spawn();
    let mut app = TestApp::new(AppConfig::new("Remedy status dock test"), move |app| {
        app.open(
            remedy_ui::WindowSpec::new("preview").size(1180.0, 740.0),
            move |ui, _| {
                ui.root().child(remedy_desktop::grove_home_chrome(ui));
            },
        );
    });
    app.frames(4);
    let on_screen = |app: &TestApp, role: Role, name: &str| -> bool {
        app.find(role, name)
            .and_then(|el| el.bounds())
            .is_some_and(|r| r.min.y >= 0.0 && r.max.y <= 740.0 && r.max.y - r.min.y >= 8.0)
    };
    assert!(
        app.find(Role::Toolbar, "Status").is_some(),
        "status dock is in the tree"
    );
    assert!(
        on_screen(&app, Role::Toolbar, "Status") || on_screen(&app, Role::Button, "Help"),
        "status dock must be visible at the bottom of an 1180x740 window"
    );
    // Opening Settings must not push the dock off the bottom (it is a sibling rail).
    if app.press(Role::Button, "Settings") {
        app.frames(4);
        assert!(
            on_screen(&app, Role::Toolbar, "Status") || on_screen(&app, Role::Button, "Help"),
            "status dock must stay on screen while Settings is open"
        );
    }
}
