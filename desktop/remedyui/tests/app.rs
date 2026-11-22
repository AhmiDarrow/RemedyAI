//! The app on the in-memory host, and its previews as golden images.

use remedy_ui::a11y::Role;
use remedy_ui::devtools::{check_golden, GoldenError, Preview};
use remedy_ui::testing::TestApp;
use remedy_ui::themes::PALETTES;
use remedy_ui::AppConfig;

const SHELL: (f32, f32) = (1100.0, 700.0);
const THEMES: &[&str] = &[
    "forest", "dark", "neutral", "light", "green", "purple", "alien", "orange", "cyan", "deutan",
    "protan", "tritan",
];

#[test]
fn the_app_opens_its_window() {
    let app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.ui().is_some(), "a window opened");
}

#[test]
fn new_session_and_close_are_visible_and_work() {
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

#[test]
fn previews_match_their_golden_images() {
    let preview = Preview::new("studio", |ui| remedy_desktop::home(ui))
        .sizes(&[SHELL])
        .themes(THEMES);
    // The first run writes the images to tests/golden for review; commit them.
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}
