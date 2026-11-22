//! The app on the in-memory host, and its previews as golden images.

use remedy_ui::AppConfig;
use remedy_ui::a11y::Role;
use remedy_ui::devtools::{GoldenError, Preview, check_golden};
use remedy_ui::testing::TestApp;
use remedy_ui::themes::PALETTES;
use std::sync::Once;

const SHELL: (f32, f32) = (1100.0, 700.0);
const THEMES: &[&str] = &[
    "forest", "dark", "neutral", "light", "green", "purple", "alien", "orange", "cyan", "deutan",
    "protan", "tritan",
];

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        // Integration tests compile the lib without cfg(test); never launch a sidecar.
        unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") }
    });
}

#[test]
fn the_app_opens_its_window() {
    no_spawn();
    let app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.ui().is_some(), "a window opened");
}

#[test]
fn new_session_and_close_are_visible_and_work() {
    no_spawn();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "New session is visible without hover"
    );
    assert!(
        app.find(Role::Button, "Close New chat").is_some(),
        "close is visible on the first session"
    );
    assert!(app.press(Role::Button, "New session"), "new session");
    assert!(
        app.find(Role::Button, "Close New chat 2").is_some(),
        "the new row has a visible close"
    );
    assert!(
        app.press(Role::Button, "Close New chat 2"),
        "close the new row"
    );
    assert!(
        app.find(Role::Button, "Close New chat 2").is_none(),
        "the closed row is gone"
    );
    assert!(
        app.find(Role::Button, "Close New chat").is_some(),
        "the first session remains"
    );
}

#[test]
fn every_palette_is_covered_by_the_shell_preview() {
    let ids: Vec<&str> = PALETTES.iter().map(|p| p.id).collect();
    assert_eq!(ids, THEMES);
}

fn assert_goldens(name: &str, build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    let preview = Preview::new(name, build).sizes(&[SHELL]).themes(THEMES);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn previews_match_their_golden_images() {
    assert_goldens("studio", remedy_desktop::home);
    assert_goldens("studio-chat", remedy_desktop::home_chat);
}
