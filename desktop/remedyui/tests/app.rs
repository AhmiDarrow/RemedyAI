//! The app on the in-memory host, and its previews as golden images.

use remedy_ui::AppConfig;
use remedy_ui::a11y::{Action, Role};
use remedy_ui::devtools::{GoldenError, Preview, check_golden};
use remedy_ui::input::{Key, KeyEvent, Modifiers};
use remedy_ui::testing::TestApp;
use remedy_ui::themes::PALETTES;
use std::path::PathBuf;
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
        // Grove prefs live under REMEDY_HOME. A fresh directory per process, so a
        // previous run that switched to Studio cannot open this one there.
        // Rails persist beside that home, not in the user's ~/.remedy.
        let home = std::env::temp_dir().join(format!(
            "remedy-desktop-test-home-{}",
            std::process::id()
        ));
        let layout = std::env::temp_dir().join(format!(
            "remedy-desktop-rails-layout-{}",
            std::process::id()
        ));
        let _ = std::fs::create_dir_all(&home);
        let _ = std::fs::create_dir_all(&layout);
        unsafe {
            std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1");
            std::env::set_var("REMEDY_HOME", &home);
            std::env::set_var("REMEDY_LAYOUT_DIR", &layout);
        }
    });
}

/// Live-app tests start on Grove. Drop a surface another test in this process saved.
fn fresh_profile() {
    no_spawn();
    if let Ok(home) = std::env::var("REMEDY_HOME") {
        let _ = std::fs::remove_file(std::path::Path::new(&home).join("ui").join("grove.json"));
    }
}

/// Drop a layout a previous test saved so `app()` starts from the defaults.
fn reset_saved_layout() {
    no_spawn();
    if let Ok(dir) = std::env::var("REMEDY_LAYOUT_DIR") {
        let _ = std::fs::remove_file(PathBuf::from(dir).join("workspace-layout.v3.json"));
    }
}

fn saved_layout_text() -> String {
    let dir = std::env::var("REMEDY_LAYOUT_DIR").expect("layout dir");
    std::fs::read_to_string(PathBuf::from(dir).join("workspace-layout.v3.json")).expect("saved layout")
}

#[test]
fn the_app_opens_its_window() {
    fresh_profile();
    let app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.ui().is_some(), "a window opened");
}

#[test]
fn new_session_and_close_are_visible_and_work() {
    fresh_profile();
    reset_saved_layout();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(
        app.press(Role::Button, "Studio"),
        "Grove is home; Studio is one tap away"
    );
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
fn sidebar_rows_are_disclosures_and_the_helper_line_is_gone() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        !names
            .iter()
            .any(|n| n.contains("Pin, archive, and close stay visible")),
        "helper text should be gone: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Session options")),
        "session options disclosure: {names:?}"
    );
    assert!(
        names
            .iter()
            .any(|n| n.contains("No project") && n.contains('3')),
        "project header keeps its count: {names:?}"
    );
    let options = app.reader_view().into_iter().find(|n| {
        n.role == Role::Button
            && n.name
                .as_deref()
                .is_some_and(|s| s.contains("Session options"))
    });
    assert!(
        options.is_some_and(|n| n.expanded == Some(false)),
        "session options starts collapsed"
    );
}

#[test]
fn chat_preview_shows_worked_steps_and_errors() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_chat);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Worked")
            && n.contains("3")
            && n.contains("step")
            && n.contains("1")
            && n.contains("error")),
        "worked meta above the assistant reply: {names:?}"
    );
}

fn preview_window(build: fn(&remedy_ui::Ui) -> remedy_ui::El) -> TestApp {
    TestApp::new(AppConfig::new("Remedy Desktop test"), move |app| {
        app.open(
            remedy_ui::WindowSpec::new("preview").size(1100.0, 700.0),
            move |ui, _| {
                ui.root().child(build(ui));
            },
        );
    })
}

fn accessible_names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect()
}

#[test]
fn every_palette_is_covered_by_the_shell_preview() {
    let ids: Vec<&str> = PALETTES.iter().map(|p| p.id).collect();
    assert_eq!(ids, THEMES);
}

fn assert_goldens(name: &str, themes: &[&str], build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    let preview = Preview::new(name, build).sizes(&[SHELL]).themes(themes);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) => {}
        // Missing is written, then reported, so a deleted golden cannot pass quietly.
        Err(e) => panic!("{e}"),
    }
}

/// Forest, a light theme, and a colorblind palette. Missing PNGs are not a failure.
const SETTINGS_THEMES: &[&str] = &["forest", "light", "deutan"];

fn assert_settings_goldens(name: &str, build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    let preview = Preview::new(name, build).sizes(&[SHELL]).themes(SETTINGS_THEMES);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn previews_match_their_golden_images() {
    assert_goldens("studio", THEMES, remedy_desktop::home);
    assert_goldens("studio-chat", THEMES, remedy_desktop::home_chat);
    let rails = &["forest", "deutan"][..];
    assert_goldens("rails-left", rails, remedy_desktop::home_rails_left);
    assert_goldens("rails-right", rails, remedy_desktop::home_rails_right);
    assert_goldens("rails-dual", rails, remedy_desktop::home_rails_dual);
}

#[test]
fn grove_preview_matches_its_golden_images() {
    assert_goldens("grove-home", THEMES, remedy_desktop::grove_home);
}

#[test]
fn settings_previews_match_their_golden_images() {
    assert_settings_goldens("settings-general", remedy_desktop::preview_general);
    assert_settings_goldens("settings-appearance", remedy_desktop::preview_appearance);
    assert_settings_goldens("settings-models", remedy_desktop::preview_models);
    assert_settings_goldens("settings-connect", remedy_desktop::preview_connect);
    assert_settings_goldens("settings-error", remedy_desktop::preview_error);
}

const CHROME_THEMES: &[&str] = &["forest", "deutan"];

fn assert_chrome_goldens(name: &str, build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    let preview = Preview::new(name, build).sizes(&[SHELL]).themes(CHROME_THEMES);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn chrome_previews_match_their_golden_images() {
    assert_chrome_goldens("chrome-palette", remedy_desktop::home_palette);
    assert_chrome_goldens("chrome-help", remedy_desktop::home_help);
    assert_chrome_goldens("chrome-about", remedy_desktop::home_about);
    assert_chrome_goldens("chrome-diagnostics", remedy_desktop::home_diagnostics);
    assert_chrome_goldens("chrome-menu", remedy_desktop::home_menu);
}

fn has(names: &[String], needle: &str) -> bool {
    names.iter().any(|n| n.contains(needle))
}

#[test]
fn palette_opens_and_closes_from_the_keyboard() {
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(app.press(Role::Button, "Studio"), "leave Grove before the chord");
    app.key(KeyEvent::press(Key::char('k')).with_modifiers(Modifiers::CTRL));
    app.frames(1);
    let names = accessible_names(&app);
    for needle in [
        "New Session",
        "Time Travel",
        "Export Session",
        "Import Session",
        "Memory",
        "Skills",
        "Diagnostics",
        "Troubleshooting",
        "Slash commands reference",
    ] {
        assert!(has(&names, needle), "{needle} missing: {names:?}");
    }
    app.key(KeyEvent::press(Key::Escape));
    app.frames(1);
    assert!(
        !has(&accessible_names(&app), "Search commands"),
        "escape closes the palette"
    );
    app.key(KeyEvent::press(Key::char('p')).with_modifiers(Modifiers::CTRL));
    app.frames(1);
    assert!(has(&accessible_names(&app), "New Session"), "Ctrl+P reopens");
}

#[test]
fn help_about_diagnostics_and_quit_use_the_title_menu() {
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    app.key(KeyEvent::press(Key::F(1)));
    app.frames(2);
    assert!(
        has(&accessible_names(&app), "Overview"),
        "F1 opens the manual: {:?}",
        accessible_names(&app)
    );
    app.key(KeyEvent::press(Key::Escape));
    app.frames(1);
    assert!(app.press(Role::Button, "Open Remedy menu"));
    assert!(app.press(Role::Button, "About Remedy (v1.0.0)"));
    app.frames(1);
    assert!(
        has(&accessible_names(&app), "Hope this helps"),
        "about signature: {:?}",
        accessible_names(&app)
    );
    app.key(KeyEvent::press(Key::Escape));
    app.frames(1);
    assert!(app.press(Role::Button, "Open Remedy menu"));
    assert!(app.press(Role::Button, "Diagnostics"));
    app.frames(2);
    let names = accessible_names(&app);
    // The live app has no server under REMEDY_DESKTOP_NO_SPAWN, so the badge is
    // "Checking…" or the offline line. The preview golden is the Healthy sample.
    assert!(
        has(&names, "Healthy") || has(&names, "Checking") || has(&names, "not connected"),
        "diagnostics: {names:?}"
    );
    assert!(has(&names, "Hive"), "hive: {names:?}");
    app.key(KeyEvent::press(Key::Escape));
    app.frames(1);
    assert!(app.press(Role::Button, "Open Remedy menu"));
    assert!(app.press(Role::Button, "Quit Remedy"));
    app.frames(1);
    assert!(
        has(&accessible_names(&app), "stop the local server"),
        "quit warning: {:?}",
        accessible_names(&app)
    );
}

#[test]
fn settings_chord_opens_the_rail_and_composer_ignores_new_session() {
    fresh_profile();
    reset_saved_layout();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(app.press(Role::Button, "Studio"));
    app.key(KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL));
    app.frames(2);
    let names = accessible_names(&app);
    // The live app has no settings client under REMEDY_DESKTOP_NO_SPAWN, so
    // ensure_loaded stops on "Settings couldn’t load" and never mounts
    // You & Agent / Appearance. The rail is still open. Language and font
    // size render once a client loads; the settings goldens cover that.
    assert!(
        has(&names, "Search all settings") || has(&names, "Settings couldn’t load"),
        "settings rail: {names:?}"
    );
    let composer = "Message Remedy — /command · @file · Shift+Tab Chat/Plan/Build";
    let field = app.find(Role::TextInput, composer);
    assert!(
        field.is_some(),
        "composer missing among text fields: {:?}",
        app.reader_view()
            .into_iter()
            .filter(|n| n.role == Role::TextInput)
            .filter_map(|n| n.name)
            .collect::<Vec<_>>()
    );
    field.unwrap().focus();
    app.frames(1);
    app.key(KeyEvent::press(Key::char('n')).with_modifiers(Modifiers::CTRL));
    app.frames(1);
    assert!(
        app.find(Role::Button, "Close New chat 2").is_none(),
        "Ctrl+N does not fire while typing"
    );
    app.key(KeyEvent::press(Key::Tab).with_modifiers(Modifiers::SHIFT));
    app.frames(1);
    assert!(
        has(&accessible_names(&app), "CHAT"),
        "Shift+Tab cycles Build to Chat: {:?}",
        accessible_names(&app)
    );
}

fn press_nth(app: &mut TestApp, role: Role, name: &str, nth: usize) -> bool {
    let Some(ui) = app.ui() else { return false };
    let nodes: Vec<_> = app
        .reader_view()
        .into_iter()
        .filter(|n| n.role == role && n.name.as_deref() == Some(name))
        .collect();
    let Some(node) = nodes.get(nth) else { return false };
    let Some(el) = ui.element_by_node_id(node.id) else { return false };
    let done = ui.accessibility_action(el.node_id(), Action::Click);
    app.frames(1);
    done
}

fn press_last(app: &mut TestApp, role: Role, name: &str) -> bool {
    let count = app
        .reader_view()
        .into_iter()
        .filter(|n| n.role == role && n.name.as_deref() == Some(name))
        .count();
    count > 0 && press_nth(app, role, name, count - 1)
}

#[test]
fn swap_exchanges_which_rail_is_thin() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(1);
    assert!(app.find(Role::Button, "Expand right rail").is_some());
    assert!(app.find(Role::Button, "Expand left rail").is_none());
    assert!(app.press(Role::Button, "Swap left and right panels"), "swap");
    assert!(
        app.find(Role::Button, "Expand left rail").is_some(),
        "after swap the left rail is the thin one"
    );
    assert!(app.find(Role::Button, "Expand right rail").is_none());
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "sessions moved with the open rail"
    );
}

#[test]
fn status_settings_toggles_the_right_rail() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(1);
    assert!(app.find(Role::Button, "Expand right rail").is_some());
    assert!(press_last(&mut app, Role::Button, "Settings"), "status Settings");
    assert!(
        app.find(Role::Button, "Expand right rail").is_none(),
        "settings opened the right rail"
    );
    assert!(
        app.find(Role::Toolbar, "Right workspace rail").is_some(),
        "right rail is icons plus the panel"
    );
    assert!(press_last(&mut app, Role::Button, "Settings"), "status Settings again");
    assert!(
        app.find(Role::Button, "Expand right rail").is_some(),
        "the second press collapses the right rail to thin"
    );
}

#[test]
fn narrow_window_keeps_one_panel_and_restores_the_thin_rail() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(1);
    let id = app.host().window_ids()[0];
    app.host().user_resize(id, remedy_ui::core::Size::new(700.0, 700.0));
    app.frames(2);
    assert!(app.find(Role::Button, "Expand left rail").is_none());
    assert!(app.find(Role::Button, "Expand right rail").is_none());
    assert!(app.find(Role::Toolbar, "Left workspace rail").is_some());
    assert!(app.find(Role::Toolbar, "Right workspace rail").is_some());
    assert!(
        press_nth(&mut app, Role::Button, "Terminal", 1),
        "the right rail's Terminal"
    );
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("In-app terminal on this PC")),
        "the right panel opened for this width only: {names:?}"
    );
    app.host().user_resize(id, remedy_ui::core::Size::new(1100.0, 700.0));
    app.frames(2);
    assert!(
        app.find(Role::Button, "Expand right rail").is_some(),
        "the saved thin right rail comes back"
    );
    assert!(app.find(Role::Button, "Expand left rail").is_none());
    let names = accessible_names(&app);
    assert!(
        !names.iter().any(|n| n.contains("In-app terminal on this PC")),
        "widening does not keep the temporary panel: {names:?}"
    );
}

#[test]
fn terminal_pops_out_of_the_right_rail() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(1);
    assert!(app.press(Role::Button, "Expand right rail"));
    assert!(press_nth(&mut app, Role::Button, "Terminal", 1), "right Terminal");
    assert!(app.press(Role::Button, "Pop out to floating window"), "pop out");
    assert!(app.find(Role::Dialog, "Terminal").is_some(), "floating terminal");
    app.key(KeyEvent::press(Key::Escape));
    assert!(app.find(Role::Dialog, "Terminal").is_none(), "escape closes it");
}

#[test]
fn resize_left_panel_from_the_keyboard() {
    fresh_profile();
    reset_saved_layout();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.press(Role::Button, "Studio"), "open Studio");
    let handle = app
        .find(Role::Separator, "Resize left panel")
        .expect("left resize handle");
    handle.focus();
    app.frames(1);
    app.key(KeyEvent::press(Key::ArrowRight));
    let saved = saved_layout_text();
    assert!(
        saved.contains("\"leftWidth\":290"),
        "arrow grows the left body by 10: {saved}"
    );
}
