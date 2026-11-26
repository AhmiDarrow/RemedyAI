//! The app on the in-memory host, and its previews as golden images.

use remedy_ui::a11y::{Action, Role};
use remedy_ui::core::{Point, Vec2};
use remedy_ui::devtools::{check_golden, GoldenError, Preview};
use remedy_ui::host::{HostEvent, TrayEvent};
use remedy_ui::input::{Key, KeyEvent, Modifiers};
use remedy_ui::testing::TestApp;
use remedy_ui::themes::PALETTES;
use remedy_ui::AppConfig;
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
        let home =
            std::env::temp_dir().join(format!("remedy-desktop-test-home-{}", std::process::id()));
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
fn layout_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

/// The saved layout is one file per process: the returned guard keeps tests
/// that depend on it from running at the same time.
#[must_use]
fn reset_saved_layout() -> std::sync::MutexGuard<'static, ()> {
    static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());
    no_spawn();
    let serial = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    let _g = layout_lock();
    if let Ok(dir) = std::env::var("REMEDY_LAYOUT_DIR") {
        let _ = std::fs::remove_file(PathBuf::from(dir).join("workspace-layout.v3.json"));
    }
    serial
}

fn saved_layout_text() -> String {
    let dir = std::env::var("REMEDY_LAYOUT_DIR").expect("layout dir");
    let path = PathBuf::from(dir).join("workspace-layout.v3.json");
    std::fs::read_to_string(&path).expect("saved layout")
}

#[test]
fn the_app_opens_its_window() {
    fresh_profile();
    let app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.ui().is_some(), "a window opened");
}

#[test]
fn new_session_and_close_are_visible_and_work() {
    let _layout = reset_saved_layout();
    fresh_profile();
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
fn sidebar_select_lets_you_move_or_delete_several_chats() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        !names.iter().any(|n| n == "Select" || n.starts_with("Select ")),
        "Select stays inside collapsed session options: {names:?}"
    );
    assert!(
        !names.iter().any(|n| n.ends_with(" selected")),
        "no selection bar on the default home: {names:?}"
    );
    assert!(
        app.press(Role::Button, "Session options"),
        "open session options"
    );
    app.frames(2);
    assert!(
        app.find(Role::Button, "Select").is_some(),
        "Select is in session options: {:?}",
        accessible_names(&app)
    );
    assert!(app.press(Role::Button, "Select"), "enter select mode");
    app.frames(2);
    let names = accessible_names(&app);
    for title in ["New chat", "Garden planner", "House rules"] {
        assert!(
            app.find(Role::Check, &format!("Select {title}")).is_some(),
            "checkbox for {title}: {names:?}"
        );
    }
    assert!(
        names.iter().any(|n| n == "0 selected"),
        "selection bar opens with select mode: {names:?}"
    );
    assert!(
        app.press(Role::Check, "Select Garden planner"),
        "check the first chat"
    );
    app.frames(2);
    assert!(
        accessible_names(&app).iter().any(|n| n == "1 selected"),
        "one chat checked: {:?}",
        accessible_names(&app)
    );
    assert!(
        app.press(Role::Check, "Select House rules"),
        "check a second chat"
    );
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "2 selected"),
        "two chats checked: {names:?}"
    );
    assert!(
        app.find(Role::Button, "Delete").is_some(),
        "bulk delete: {names:?}"
    );
    assert!(
        app.find(Role::Button, "Clear").is_some(),
        "clear selection: {names:?}"
    );
    assert!(app.press(Role::Button, "Delete"), "confirm bulk delete");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "Delete 2 chats?"),
        "confirm title: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Partner Memory")),
        "same delete warning as a single chat: {names:?}"
    );
    assert!(app.press(Role::Button, "Cancel"), "keep the chats");
    app.frames(2);
    assert!(
        app.find(Role::Button, "Garden planner").is_some(),
        "cancel leaves the chats"
    );
    assert!(app.press(Role::Button, "Done selecting"), "leave select mode");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Check, "Select Garden planner").is_none(),
        "checkboxes hide when selection is cleared: {names:?}"
    );
}

#[test]
fn sidebar_tags_can_be_shown_and_used_to_filter() {
    no_spawn();
    let mut tagged = preview_window(remedy_desktop::home_tagged);
    tagged.frames(2);
    let names = accessible_names(&tagged);
    assert!(
        names.iter().any(|n| n == "recipes"),
        "saved tag chip: {names:?}"
    );
    assert!(
        names.iter().any(|n| n == "ui"),
        "second tag chip: {names:?}"
    );
    assert!(tagged.press(Role::Button, "recipes"), "filter by tag");
    tagged.frames(2);
    let names = accessible_names(&tagged);
    assert!(
        names.iter().any(|n| n.contains("Garden planner")),
        "tagged chat stays: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("No project") && n.contains("1")),
        "searching a tag leaves only tagged chats: {names:?}"
    );

    let mut app = preview_window(remedy_desktop::home);
    app.frames(2);
    assert!(app.press(Role::Button, "Session options"), "open session options");
    app.frames(2);
    assert!(app.press(Role::Button, "Add tag"), "add a tag to the active chat");
    app.frames(2);
    let field = app
        .find(Role::TextInput, "Tag name")
        .expect("tag field opens on the active session");
    field.focus();
    app.frames(1);
    app.type_text("recipes");
    app.frames(1);
    assert!(app.press(Role::Button, "Add this tag"), "commit the tag");
    app.frames(4);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "recipes"),
        "new tag appears on the row: {names:?}"
    );
}

#[test]
fn status_dock_shows_other_sessions_at_work() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        !names.iter().any(|n| n.contains("other session")),
        "idle dock hides the siblings chip: {names:?}"
    );

    let mut app = preview_window(remedy_desktop::home_siblings);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("1 other session") && n.contains("xai/grok-4.5")),
        "siblings chip names the other session: {names:?}"
    );
}

#[test]
fn skills_sheet_offers_export_import_and_archive() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_skills);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "Export"),
        "export is on the existing skills sheet: {names:?}"
    );
    assert!(
        names.iter().any(|n| n == "Import"),
        "import is on the existing skills sheet: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Archive unused")),
        "archive unused is on the existing skills sheet: {names:?}"
    );
}

#[test]
fn skills_sheet_shows_budget_learning_and_filters() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_skills);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("72 / 80") && n.contains("archive or pack")),
        "budget banner: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Active budget") || n.contains("72 of 80")),
        "budget meter line: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Learned 6") && n.contains("41 uses")),
        "learning summary: {names:?}"
    );
    assert!(names.iter().any(|n| n == "All"), "All filter: {names:?}");
    assert!(names.iter().any(|n| n.contains("recipe-notes")), "learned skill: {names:?}");
    assert!(names.iter().any(|n| n.contains("imported-pack-alpha")), "quarantine skill: {names:?}");
    assert!(names.iter().any(|n| n.contains("old-scraper")), "archived skill: {names:?}");
    assert!(app.press(Role::Button, "Learned"), "filter to learned");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("recipe-notes") || n.contains("house-style")),
        "learned filter keeps learned skills: {names:?}"
    );
    assert!(app.press(Role::Button, "All"), "clear filter");
    app.frames(2);
}

#[test]
fn skills_sheet_stays_in_the_box_at_400_700_and_1180() {
    no_spawn();
    for &(w, h) in &[(400.0, 800.0), (700.0, 800.0), (1180.0, 800.0)] {
        let mut app = preview_window_size(remedy_desktop::home_skills, w, h);
        app.frames(3);
        let bad = horizontal_overflow(&app, w);
        assert!(
            bad.is_empty(),
            "skills sheet at {w}x{h} painted off-box:
  {}",
            bad.join("\n  ")
        );
        let names = accessible_names(&app);
        assert!(
            names.iter().any(|n| n == "Export") && names.iter().any(|n| n == "All"),
            "skills chrome stays available at {w}: {names:?}"
        );
    }
}


#[test]
fn image_markup_shows_tools_colors_and_actions() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_markup);
    app.frames(3);
    let names = accessible_names(&app);
    for need in [
        "Pen",
        "Highlight",
        "Arrow",
        "Box",
        "Text",
        "Red",
        "Orange",
        "Yellow",
        "Green",
        "Blue",
        "White",
        "Black",
        "S",
        "M",
        "L",
        "Undo",
        "Clear",
        "Download",
        "Save & attach to prompt",
        "Close · Esc",
        "settings-general.png",
        "Ready to attach",
    ] {
        assert!(
            names.iter().any(|n| n == need || n.contains(need)),
            "markup editor is missing {need}: {names:?}"
        );
    }
    assert!(
        names.iter().any(|n| n.contains("Ctrl+Z undo") && n.contains("Attach markup")),
        "footer hint: {names:?}"
    );
    let pen = app.node(Role::Button, "Pen").expect("pen");
    assert_eq!(pen.selected, Some(true), "pen starts selected: {pen:?}");
}

#[test]
fn image_markup_keyboard_picks_tools_and_resets_zoom() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_markup);
    app.frames(3);
    app.key(KeyEvent::press(Key::char('h')));
    app.frames(2);
    let highlight = app.node(Role::Button, "Highlight").expect("highlight");
    assert_eq!(highlight.selected, Some(true), "H selects highlight: {highlight:?}");
    app.key(KeyEvent::press(Key::char('=')));
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "115%"),
        "zoom in labels the percent: {names:?}"
    );
    app.key(KeyEvent::press(Key::char('0')));
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "100%"),
        "0 resets zoom: {names:?}"
    );
    app.key(KeyEvent::press(Key::char('z')).with_modifiers(Modifiers::CTRL));
    app.frames(2);
}

#[test]
fn image_markup_text_dialog_can_be_cancelled() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_markup_text);
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "Text label"),
        "text label card: {names:?}"
    );
    assert!(
        names.iter().any(|n| n == "Add label"),
        "add label: {names:?}"
    );
    assert!(app.press(Role::Button, "Cancel"), "cancel text card");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().all(|n| n != "Add label"),
        "cancel closes the card: {names:?}"
    );
}

#[test]
fn image_markup_stays_in_the_box_at_400_700_and_1180() {
    no_spawn();
    for &(w, h) in &[(400.0, 800.0), (700.0, 800.0), (1180.0, 800.0)] {
        let mut app = preview_window_size(remedy_desktop::home_markup, w, h);
        app.frames(3);
        let bad = horizontal_overflow(&app, w);
        assert!(
            bad.is_empty(),
            "markup editor at {w}x{h} painted off-box:\n  {}",
            bad.join("\n  ")
        );
        let names = accessible_names(&app);
        assert!(
            names.iter().any(|n| n == "Pen")
                && names.iter().any(|n| n == "Save & attach to prompt"),
            "markup chrome stays available at {w}: {names:?}"
        );
    }
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

#[test]
fn live_turn_seals_mid_turn_speech_with_reactions() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_chat_live);
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names
            .iter()
            .any(|n| n.contains("I'll reach the folder through the shell.")),
        "sealed mid-turn speech is a bubble: {names:?}"
    );
    assert!(
        names
            .iter()
            .any(|n| n.contains("Cmd is eating the search patterns")),
        "later sealed beat is its own bubble: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Patching the entry path")),
        "in-progress reply stays live: {names:?}"
    );
    // Reactions live in the picker behind "Add reaction".
    assert!(app.press(Role::Button, "Add reaction"), "open the picker");
    app.frames(2);
    let names = accessible_names(&app);
    for label in ["Thumbs up", "Thumbs down", "Heart", "Broken heart"] {
        assert!(
            names.iter().any(|n| n == label || n.contains(label)),
            "reaction {label} on sealed bubbles: {names:?}"
        );
    }
}

#[test]
fn chat_workspace_is_two_panes_with_a_swap() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home_chat);
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Coding / Actions")),
        "actions pane: {names:?}"
    );
    assert!(
        names
            .iter()
            .any(|n| n == "Chat" || n.contains("Messages to you")),
        "chat pane: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Actions on the left")),
        "default side: {names:?}"
    );
    // Thinking starts collapsed; open it to read the text.
    assert!(app.press(Role::Button, "Thinking"), "expand thinking");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names
            .iter()
            .any(|n| n.contains("The manifest still named init.lua")),
        "thinking lives in the actions pane: {names:?}"
    );
    assert!(app.press(Role::Button, "Swap Actions and Chat"), "swap");
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Actions on the right")),
        "swap persists in the session: {names:?}"
    );
}

#[test]
fn chat_pop_out_opens_a_real_window_and_has_no_pip() {
    no_spawn();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), move |app| {
        let host = app.clone();
        app.open(
            remedy_ui::WindowSpec::new("preview").size(1180.0, 800.0),
            move |ui, window| {
                remedy_desktop::bind_host(&host, window);
                ui.root().child(remedy_desktop::home_chat(ui));
            },
        );
    });
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n == "Pop out"),
        "Pop out control: {names:?}"
    );
    assert!(
        names.iter().all(|n| n != "PiP"),
        "PiP is merged into Pop out: {names:?}"
    );
    let before = app.host().window_ids().len();
    assert!(app.press(Role::Button, "Pop out"), "pop out");
    app.frames(3);
    let ids = app.host().window_ids();
    assert!(
        ids.len() > before,
        "Pop out must open a real window, had {before} now {}",
        ids.len()
    );
    let wins = app.runner().app().windows();
    let pop = wins
        .iter()
        .rev()
        .find_map(|w| w.ui())
        .expect("popped-out window has a UI");
    let pop_names: Vec<String> = pop
        .reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect();
    assert!(
        pop_names.iter().any(|n| n == "Dock back"),
        "popped window has Dock back: {pop_names:?}"
    );
    assert!(
        pop_names.iter().any(|n| n.contains("Coding")
            || n.contains("Chat")
            || n.contains("Actions")
            || n.contains("The importer")
            || n.contains("I'll reach")),
        "popped window must render live content: {pop_names:?}"
    );
}

#[test]
fn chat_fullscreen_keeps_the_pane_visible() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_chat, 1180.0, 800.0);
    app.frames(3);
    let before = accessible_names(&app);
    assert!(
        before
            .iter()
            .any(|n| n.contains("The manifest still named init.lua")
                || n.contains("recipes")
                || n.contains("Coding / Actions")),
        "preview content before fullscreen: {before:?}"
    );
    assert!(app.press(Role::Button, "Fullscreen"), "fullscreen");
    app.frames(3);
    let after = accessible_names(&app);
    assert!(
        after
            .iter()
            .any(|n| n.contains("The manifest still named init.lua")
                || n.contains("recipes")
                || n.contains("Coding / Actions")
                || n.contains("Dock back")),
        "fullscreen must show the pane, not hide it: {after:?}"
    );
    assert!(
        after.iter().any(|n| n == "Dock back"),
        "Dock back in fullscreen: {after:?}"
    );
}

fn region_box(app: &TestApp, name: &str) -> remedy_ui::core::Rect {
    app.find(Role::Region, name)
        .and_then(|el| el.bounds())
        .unwrap_or_else(|| panic!("missing region {name}"))
}

#[test]
fn chat_panes_keep_size_on_swap_and_fill_when_rails_collapse() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_chat, 1180.0, 740.0);
    app.frames(4);
    let actions0 = region_box(&app, "Coding / Actions");
    let chat0 = region_box(&app, "Chat");
    assert!(
        actions0.width() > 80.0 && chat0.width() > 80.0,
        "both panes visible before swap: actions={:?} chat={:?}",
        actions0,
        chat0
    );
    assert!(
        actions0.min.x < chat0.min.x,
        "actions start on the left: actions={:?} chat={:?}",
        actions0,
        chat0
    );
    let actions_w = actions0.width();
    let chat_w = chat0.width();

    assert!(app.press(Role::Button, "Swap Actions and Chat"), "swap");
    app.frames(4);
    let actions1 = region_box(&app, "Coding / Actions");
    let chat1 = region_box(&app, "Chat");
    assert!(
        actions1.width() > 80.0 && chat1.width() > 80.0,
        "both panes must stay rendered after swap: actions={:?} chat={:?}",
        actions1,
        chat1
    );
    assert!(
        (actions1.width() - actions_w).abs() < 8.0,
        "actions width kept: before={actions_w} after={}",
        actions1.width()
    );
    assert!(
        (chat1.width() - chat_w).abs() < 8.0,
        "chat width kept: before={chat_w} after={}",
        chat1.width()
    );
    assert!(
        actions1.min.x > chat1.min.x,
        "actions move to the right: actions={:?} chat={:?}",
        actions1,
        chat1
    );

    // Collapse the open Sessions rail so the well can grow.
    assert!(
        app.press(Role::Button, "Collapse rail"),
        "collapse left rail"
    );
    app.frames(4);
    let actions2 = region_box(&app, "Coding / Actions");
    let chat2 = region_box(&app, "Chat");
    assert!(
        actions2.width() > 80.0 && chat2.width() > 80.0,
        "both panes stay after collapse: actions={:?} chat={:?}",
        actions2,
        chat2
    );
    let sum1 = actions1.width() + chat1.width();
    let sum2 = actions2.width() + chat2.width();
    assert!(
        sum2 > sum1 + 80.0,
        "center must fill the width the rail freed: before={sum1} after={sum2}"
    );
}

#[test]
fn session_list_scrolls_with_the_wheel() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_sessions_long, 900.0, 740.0);
    app.frames(4);
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "sessions rail is open"
    );
    let last = app
        .find(Role::Button, "Queued task 39")
        .and_then(|el| el.bounds())
        .unwrap_or_else(|| panic!("last session missing"));
    let first = app
        .find(Role::Button, "Queued task 0")
        .and_then(|el| el.bounds())
        .unwrap_or_else(|| panic!("first extra session missing"));
    assert!(
        last.min.y > first.max.y,
        "rows stack: first={:?} last={:?}",
        first,
        last
    );
    assert!(
        last.min.y > 740.0,
        "the list must overflow the window so it can scroll, last={last:?}"
    );
    let window = app.host().window_ids().first().copied().expect("window");
    // Wheel inside the window, over the open Sessions rail (not past the fold).
    let over_y = first.min.y.clamp(80.0, 700.0);
    let over = Point::new(first.min.x + 20.0, over_y);
    app.host().wheel(window, over, Vec2::new(0.0, 400.0));
    app.frames(4);
    let mut last_after = app
        .find(Role::Button, "Queued task 39")
        .and_then(|el| el.bounds())
        .unwrap_or_else(|| panic!("last session missing after wheel"));
    let mut first_after = app
        .find(Role::Button, "Queued task 0")
        .and_then(|el| el.bounds())
        .unwrap_or_else(|| panic!("first session missing after wheel"));
    if (first_after.min.y - first.min.y).abs() < 20.0
        && (last_after.min.y - last.min.y).abs() < 20.0
    {
        // Some hosts sign the wheel the other way; try the opposite delta once.
        app.host().wheel(window, over, Vec2::new(0.0, -400.0));
        app.frames(4);
        last_after = app
            .find(Role::Button, "Queued task 39")
            .and_then(|el| el.bounds())
            .unwrap_or_else(|| panic!("last session missing after opposite wheel"));
        first_after = app
            .find(Role::Button, "Queued task 0")
            .and_then(|el| el.bounds())
            .unwrap_or_else(|| panic!("first session missing after opposite wheel"));
    }
    assert!(
        first_after.min.y < first.min.y - 20.0 || last_after.min.y < last.min.y - 20.0
            || first_after.min.y > first.min.y + 20.0
            || last_after.min.y > last.min.y + 20.0,
        "wheel should move the session list: first {first:?} -> {first_after:?}, last {last:?} -> {last_after:?}"
    );
}

#[test]
fn narrow_chat_falls_back_to_tabs() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_chat, 640.0, 700.0);
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Chat or actions")),
        "tab list: {names:?}"
    );
    assert!(
        app.find(Role::Tab, "Chat").is_some() && app.find(Role::Tab, "Actions").is_some(),
        "tabs: {names:?}"
    );
}

fn preview_window(build: fn(&remedy_ui::Ui) -> remedy_ui::El) -> TestApp {
    preview_window_size(build, 1100.0, 700.0)
}

fn preview_window_size(
    build: fn(&remedy_ui::Ui) -> remedy_ui::El,
    width: f32,
    height: f32,
) -> TestApp {
    TestApp::new(AppConfig::new("Remedy Desktop test"), move |app| {
        app.open(
            remedy_ui::WindowSpec::new("preview").size(width, height),
            move |ui, _| {
                ui.root().child(build(ui));
            },
        );
    })
}


fn horizontal_overflow(app: &TestApp, width: f32) -> Vec<String> {
    let Some(ui) = app.ui() else {
        return vec!["no ui".into()];
    };
    let mut out = Vec::new();
    for n in app.reader_view() {
        let Some(el) = ui.element_by_node_id(n.id) else { continue };
        let Some(b) = el.bounds() else { continue };
        let left = b.min.x < -1.5;
        let interactive = matches!(
            n.role,
            Role::Button
                | Role::TextInput
                | Role::MultilineTextInput
                | Role::ComboBox
                | Role::Link
                | Role::Search
                | Role::Tab
        );
        let right = b.max.x > width + 1.5 && b.min.x < width - 1.0;
        if (left || (right && interactive)) && b.width() > 1.0 && b.height() > 1.0 {
            out.push(format!(
                "{:?} {:?} x={:.1}..{:.1} y={:.1}..{:.1}",
                n.role, n.name, b.min.x, b.max.x, b.min.y, b.max.y
            ));
        }
    }
    out
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
    // Test profile first: the face seed must not come from this machine.
    no_spawn();
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
    // Test profile first: the face seed must not come from this machine.
    no_spawn();
    let preview = Preview::new(name, build)
        .sizes(&[SHELL])
        .themes(SETTINGS_THEMES);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn previews_match_their_golden_images() {
    no_spawn();
    assert_goldens("studio", THEMES, remedy_desktop::home);
    assert_goldens("studio-chat", THEMES, remedy_desktop::home_chat);
    let rails = &["forest", "deutan"][..];
    assert_goldens("rails-left", rails, remedy_desktop::home_rails_left);
    assert_goldens("rails-right", rails, remedy_desktop::home_rails_right);
    assert_goldens("rails-dual", rails, remedy_desktop::home_rails_dual);
}

#[test]
fn live_chat_preview_matches_its_golden_images() {
    // New mid-turn surface: a missing PNG is written and is not a failure.
    assert_settings_goldens("studio-live-react", remedy_desktop::home_chat_live);
}

#[test]
fn grove_preview_matches_its_golden_images() {
    assert_goldens("grove-home", THEMES, remedy_desktop::grove_home);
    // The left column once a conversation is going. New surface: a missing PNG
    // is written and is not a failure.
    assert_settings_goldens("grove-talking", remedy_desktop::grove_home_talking);
}

#[test]
fn settings_previews_match_their_golden_images() {
    assert_settings_goldens("settings-general", remedy_desktop::preview_general);
    assert_settings_goldens("settings-appearance", remedy_desktop::preview_appearance);
    assert_settings_goldens("settings-models", remedy_desktop::preview_models);
    assert_settings_goldens("settings-connect", remedy_desktop::preview_connect);
    assert_settings_goldens("settings-error", remedy_desktop::preview_error);
}

#[test]
fn help_preview_shows_the_manual() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::help_home);
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names
            .iter()
            .any(|n| n.contains("Owner's Manual") || n.contains("Help")),
        "help dialog: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Close help")),
        "close control: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Overview")),
        "overview chapter: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Search help")),
        "search field: {names:?}"
    );
}

/// Forest, light, and a colorblind palette. The manual is a new surface, so
/// these PNGs are the intended baseline. A missing file is written, not a failure.
#[test]
fn help_preview_matches_its_golden_image() {
    assert_settings_goldens("help", remedy_desktop::help_home);
}

/// Forest, a light theme, and a colorblind palette. A missing PNG is written and is not a failure.
#[test]
fn time_travel_preview_matches_its_golden_images() {
    // Test profile first: the face seed must not come from this machine.
    no_spawn();
    let themes = &["forest", "light", "deutan"][..];
    let preview = Preview::new("timetravel", remedy_desktop::time_travel_preview)
        .sizes(&[SHELL])
        .themes(themes);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn time_travel_preview_lists_steps_and_asks_before_restore() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::time_travel_preview);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Time Travel")),
        "the rail names itself: {names:?}"
    );
    assert!(
        names
            .iter()
            .any(|n| n.contains("Step 1") && n.contains("Restore")),
        "step 1 is a restore point: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains('3') && n.contains("tools")),
        "tool count sits on the step: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("The rail is up")),
        "the assistant line hangs under the user step: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Step 3")),
        "the latest step is on the rail: {names:?}"
    );
    assert!(
        app.find(Role::Button, "Refresh timeline").is_some(),
        "refresh stays in the footer: {names:?}"
    );
    let step = button_named_like(&app, "Step 1");
    assert!(
        app.press(Role::Button, step.as_deref().unwrap_or("")),
        "clicking a step asks before it restores: {names:?}"
    );
    let asked = accessible_names(&app);
    assert!(
        asked
            .iter()
            .any(|n| n.contains("Restore to") && n.contains("Later messages")),
        "confirm copy matches the current app: {asked:?}"
    );
    assert!(
        app.press(Role::Button, "Cancel"),
        "cancel the confirm: {asked:?}"
    );
    let closed = accessible_names(&app);
    assert!(
        !closed
            .iter()
            .any(|n| n.contains("changes made outside Remedy")),
        "cancel closes the confirm: {closed:?}"
    );
    assert!(
        app.press(Role::Button, step.as_deref().unwrap_or("")),
        "the step opens the confirm again"
    );
    assert!(
        app.press(Role::Button, "Restore here"),
        "restore is offered"
    );
    let refused = accessible_names(&app);
    assert!(
        refused.iter().any(|n| n.contains("not connected")),
        "a preview does not call the runtime: {refused:?}"
    );
}

fn button_named_like(app: &TestApp, needle: &str) -> Option<String> {
    app.reader_view().into_iter().find_map(|n| {
        let name = n.name?;
        (n.role == Role::Button && name.contains(needle)).then_some(name)
    })
}

/// Forest, a light theme, and a colorblind palette. A missing PNG is written and is not a failure.
#[test]
fn usage_previews_match_their_golden_images() {
    assert_settings_goldens("usage", remedy_desktop::preview_usage);
    assert_settings_goldens("usage-harness", remedy_desktop::preview_usage_harness);
}

const CHROME_THEMES: &[&str] = &["forest", "deutan"];

fn assert_chrome_goldens(name: &str, build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    // Test profile first: the face seed must not come from this machine.
    no_spawn();
    let preview = Preview::new(name, build)
        .sizes(&[SHELL])
        .themes(CHROME_THEMES);
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
    assert_chrome_goldens("grove-chrome", remedy_desktop::grove_home_chrome);
    assert_chrome_goldens("grove-offer", remedy_desktop::grove_home_offer);
}

#[test]
fn house_rules_banner_fits_a_1024_window() {
    // Test profile first: the face seed must not come from this machine.
    no_spawn();
    let preview = Preview::new("grove-offer-compact", remedy_desktop::grove_home_offer)
        .sizes(&[(1024.0, 642.0)])
        .themes(&["forest"]);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

fn has(names: &[String], needle: &str) -> bool {
    names.iter().any(|n| n.contains(needle))
}

#[test]
fn palette_opens_and_closes_from_the_keyboard() {
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(
        app.press(Role::Button, "Studio"),
        "leave Grove before the chord"
    );
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
    assert!(
        has(&accessible_names(&app), "New Session"),
        "Ctrl+P reopens"
    );
}

#[test]
fn opening_memory_and_resizing_does_not_panic() {
    // Opening the Memory overlay used to flush the sidebar tree bind after its
    // `rows` signal was disposed ("the signal was disposed" at sidebar.rs:460).
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    app.key(KeyEvent::press(Key::char('k')).with_modifiers(Modifiers::CTRL));
    app.frames(1);
    for ch in "Memory".chars() {
        app.key(KeyEvent::press(Key::char(ch)));
    }
    app.key(KeyEvent::press(Key::Enter));
    app.frames(3);
    let win = app.runner().app().windows().first().cloned();
    if let Some(win) = win {
        win.set_size(700.0, 520.0);
        app.frames(2);
        win.set_size(1180.0, 740.0);
        app.frames(2);
        win.set_size(1440.0, 900.0);
        app.frames(2);
    }
}

#[test]
fn squeezed_chat_split_stacks_instead_of_overlapping() {
    // Sessions + Settings at 1180×740 leave the chat well far under 720px.
    // The split used to keep both panes and paint them on top of each other.
    // Use a finished-turn preview so the well is the chat/actions split, not
    // the empty-session Home landing.
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_chat, 1180.0, 740.0);
    app.frames(4);
    // Settings is on the status dock in this preview; opening it narrows the well.
    let _ = app.press(Role::Button, "Settings");
    app.key(KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL));
    app.frames(4);
    let names = accessible_names(&app);
    assert!(
        has(&names, "Chat or actions") || (has(&names, "Actions") && has(&names, "Chat")),
        "squeezed well should stack into Actions/Chat tabs, not overlap panes: {names:?}"
    );
}

#[test]
fn settings_fields_stay_inside_a_narrow_window() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    app.key(KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL));
    app.frames(2);
    let win = app.runner().app().windows().first().cloned();
    if let Some(win) = win {
        win.set_size(700.0, 520.0);
        app.frames(3);
    }
    let names = accessible_names(&app);
    assert!(
        has(&names, "Search all settings")
            || has(&names, "Settings couldn’t load")
            || has(&names, "Settings"),
        "settings still open at 700px: {names:?}"
    );
    let window_w = 700.0;
    let search = app
        .find(Role::TextInput, "Search all settings")
        .expect("Search must stay laid out when the window is 700px wide");
    if let Some(bounds) = search.bounds() {
        assert!(
            bounds.max.x <= window_w + 1.0,
            "Search overflows the window: {bounds:?}"
        );
        assert!(bounds.min.x >= -1.0, "Search starts off-screen: {bounds:?}");
        assert!(
            bounds.max.x - bounds.min.x >= 80.0,
            "Search collapsed instead of shrinking to fit: {bounds:?}"
        );
    }
    for (role, name) in [(Role::TextInput, "Base URL"), (Role::TextInput, "Model")] {
        if let Some(el) = app.find(role, name) {
            if let Some(bounds) = el.bounds() {
                assert!(
                    bounds.max.x <= window_w + 1.0,
                    "{name} overflows the window: {bounds:?}"
                );
                assert!(bounds.min.x >= -1.0, "{name} starts off-screen: {bounds:?}");
                assert!(
                    bounds.max.x - bounds.min.x >= 80.0,
                    "{name} collapsed instead of shrinking to fit: {bounds:?}"
                );
            }
        }
    }
}

#[test]
fn settings_search_stays_visible_in_studio_at_700() {
    // #117 residual: at 700px (below NARROW_MAX) with Studio chrome and both
    // icon rails, crossing into the narrow band used to clear `narrow_side`,
    // collapse Settings, and hide "Search all settings".
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(
        app.press(Role::Button, "Studio"),
        "open the session workspace"
    );
    app.frames(2);
    app.key(KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL));
    app.frames(4);
    let win = app.runner().app().windows().first().cloned();
    if let Some(win) = win {
        win.set_size(1180.0, 740.0);
        app.frames(2);
        win.set_size(700.0, 520.0);
        app.frames(4);
    }
    let names = accessible_names(&app);
    let search = app.find(Role::TextInput, "Search all settings");
    assert!(
        search.is_some(),
        "Search must stay laid out in Studio at 700px: {names:?}"
    );
    let window_w = 700.0;
    if let Some(bounds) = search.and_then(|el| el.bounds()) {
        assert!(
            bounds.max.x <= window_w + 1.0,
            "Search overflows the window: {bounds:?}"
        );
        assert!(bounds.min.x >= -1.0, "Search starts off-screen: {bounds:?}");
        assert!(
            bounds.max.x - bounds.min.x >= 80.0,
            "Search collapsed instead of shrinking to fit: {bounds:?}"
        );
    }
}

#[test]
fn chat_plan_build_switcher_updates_composer_and_plan_empty() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(app.press(Role::Button, "Studio"), "open Studio");
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        has(&names, "Chat") && has(&names, "Plan") && has(&names, "Build"),
        "mode switcher shows all three modes: {names:?}"
    );
    // Default is Build: composer uses the build placeholder and hint.
    assert!(
        app.find(Role::TextInput, "Ask Remedy").is_some() || has(&names, "Ask Remedy"),
        "Build placeholder in the composer: {names:?}"
    );
    assert!(
        names.iter().any(|n| {
            n.contains("Enter to send")
                || n.contains("Shift+Tab to switch mode")
                || n.contains("Enter send")
                || n.contains("Shift+Tab Chat")
        }),
        "Build hint stays short: {names:?}"
    );

    assert!(app.press(Role::Button, "Plan"), "switch to Plan");
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        has(&names, "No plan yet") || names.iter().any(|n| n.contains("outline the work")),
        "Plan mode shows its empty state: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("research only")
            || n.contains("Shift+Tab Build")
            || n.contains("Outline the work")),
        "Plan placeholder/hint reflects the mode: {names:?}"
    );

    assert!(app.press(Role::Button, "Chat"), "switch to Chat");
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Just talking")
            || n.contains("Shift+Tab for Plan")
            || n.contains("no tools")),
        "Chat placeholder/hint reflects the mode: {names:?}"
    );

    // Shift+Tab cycles Chat → Plan.
    app.key(KeyEvent::press(Key::Tab).with_modifiers(Modifiers::SHIFT));
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        has(&names, "No plan yet")
            || names
                .iter()
                .any(|n| n.contains("outline the work") || n.contains("Shift+Tab for Build")),
        "Shift+Tab lands on Plan: {names:?}"
    );

    // Ctrl+B cycles Plan → Build.
    app.key(KeyEvent::press(Key::char('b')).with_modifiers(Modifiers::CTRL));
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::TextInput, "Ask Remedy").is_some()
            || has(&names, "Ask Remedy")
            || names.iter().any(|n| n.contains("Enter send") || n.contains("Enter to send")),
        "Ctrl+B lands on Build: {names:?}"
    );
}

#[test]
fn plan_list_pane_docks_collapses_and_toggles_from_the_keyboard() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(app.press(Role::Button, "Studio"), "open Studio");
    app.frames(2);
    assert!(app.press(Role::Button, "Plan"), "switch to Plan");
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        has(&names, "Plans") || names.iter().any(|n| n.contains("Plans")),
        "Plan mode shows the plan list pane: {names:?}"
    );
    assert!(
        has(&names, "No plan yet") || names.iter().any(|n| n.contains("outline the work")),
        "the pane shows the empty plan: {names:?}"
    );
    assert!(
        app.find(Role::Button, "Collapse Plans").is_some(),
        "a chevron collapses the pane: {names:?}"
    );
    let plans = app.find(Role::Region, "Plans").and_then(|el| el.bounds());
    let chat = app
        .find(Role::Region, "Chat")
        .and_then(|el| el.bounds())
        .or_else(|| {
            app.find_all(Role::Region).into_iter().find_map(|el| {
                el.bounds().filter(|b| {
                    b.width() > 80.0 && plans.as_ref().is_some_and(|p| b.min.x >= p.max.x - 1.0)
                })
            })
        });
    if let (Some(plans), Some(chat)) = (plans, chat) {
        assert!(
            plans.min.x < chat.min.x,
            "the plan list starts on the left: plans={plans:?} chat={chat:?}"
        );
        assert!(
            plans.width() >= 180.0 && plans.width() <= 560.0,
            "the plan list is a usable column: {plans:?}"
        );
    }

    assert!(app.press(Role::Button, "Collapse Plans"), "collapse");
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Button, "Expand Plans").is_some()
            || names
                .iter()
                .any(|n| n.contains("Expand Plans") || n.contains("Plans (collapsed)")),
        "collapsed strip names the expand action: {names:?}"
    );
    assert!(
        !has(&names, "No plan yet"),
        "the body is hidden while collapsed: {names:?}"
    );

    // Ctrl+Shift+[ expands it again, even while the composer is focused.
    app.key(KeyEvent::press(Key::char('[')).with_modifiers(Modifiers {
        ctrl: true,
        shift: true,
        ..Modifiers::NONE
    }));
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Button, "Collapse Plans").is_some() || has(&names, "No plan yet"),
        "Ctrl+Shift+[ expands the plan list: {names:?}"
    );
}

#[test]
fn help_about_diagnostics_and_quit_use_the_title_menu() {
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    app.key(KeyEvent::press(Key::F(1)));
    app.frames(3);
    assert!(
        has(&accessible_names(&app), "Overview"),
        "F1 opens the manual: {:?}",
        accessible_names(&app)
    );
    app.key(KeyEvent::press(Key::Escape));
    app.frames(1);
    assert!(app.press(Role::Button, "Open Remedy menu"));
    assert!(app.press(Role::Button, concat!("About Remedy (v", env!("CARGO_PKG_VERSION"), ")")));
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
    // The live app has no server under REMEDY_DESKTOP_NO_SPAWN. The landed
    // panel stays on its dialog, the hive group, and either the loading line
    // or the offline error. The sample golden is the Healthy snapshot.
    assert!(has(&names, "Diagnostics"), "diagnostics title: {names:?}");
    assert!(has(&names, "Health diagnostics"), "dialog: {names:?}");
    assert!(has(&names, "Hive"), "hive: {names:?}");
    assert!(
        has(&names, "Loading diagnostics")
            || has(&names, "Could not reach the runtime")
            || has(&names, "Cannot reach local API")
            // An installed Remedy running on this PC answers the port, and
            // refuses the test, which has no token for it.
            || has(&names, "Missing or invalid Bearer token"),
        "offline diagnostics: {names:?}"
    );
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
    let _layout = reset_saved_layout();
    fresh_profile();
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
    let composer = "Ask Remedy";
    // Chat-depth draws the composer as a text area.
    let field = app.find(Role::MultilineTextInput, composer);
    assert!(
        field.is_some(),
        "composer missing among text fields: {:?}",
        app.reader_view()
            .into_iter()
            .filter(|n| {
                matches!(
                    n.role,
                    Role::TextInput | Role::MultilineTextInput | Role::SearchInput
                )
            })
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
    let names = accessible_names(&app);
    assert!(
        has(&names, "— Chat")
            || names.iter().any(|n| {
                n.contains("Just talking")
                    || n.contains("no tools")
                    || n.contains("Shift+Tab Plan")
                    || n.contains("Shift+Tab for Plan")
            }),
        "Shift+Tab cycles Build to Chat: {names:?}"
    );
}

fn settings_rail_open(names: &[String]) -> bool {
    has(names, "Search all settings") || has(names, "Settings couldn’t load")
}

fn open_settings_rail(app: &mut TestApp) {
    app.key(KeyEvent::press(Key::char(',')).with_modifiers(Modifiers::CTRL));
    app.frames(2);
    assert!(
        settings_rail_open(&accessible_names(app)),
        "settings rail: {:?}",
        accessible_names(app)
    );
}

fn assert_settings_rail_closed(app: &TestApp, why: &str) {
    let names = accessible_names(app);
    assert!(
        !settings_rail_open(&names),
        "{why}: {names:?}"
    );
}

#[test]
fn escape_closes_settings_regardless_of_focus() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(2);
    assert!(app.press(Role::Button, "Studio"));

    // Ctrl+, leaves focus on the chrome frame, which is not under the shell
    // column's on_key handler. Escape must still close the rail.
    open_settings_rail(&mut app);
    app.key(KeyEvent::press(Key::Escape));
    app.frames(2);
    assert_settings_rail_closed(&app, "escape closes settings when the frame has focus");

    open_settings_rail(&mut app);
    let search = app
        .find(Role::TextInput, "Search all settings")
        .or_else(|| app.find(Role::SearchInput, "Search all settings"));
    if let Some(search) = search {
        search.focus();
        app.frames(1);
    }
    app.key(KeyEvent::press(Key::Escape));
    app.frames(2);
    assert_settings_rail_closed(&app, "escape closes settings when Search is focused");

    open_settings_rail(&mut app);
    if let Some(field) = app.find(Role::MultilineTextInput, "Ask Remedy") {
        field.focus();
        app.frames(1);
    }
    app.key(KeyEvent::press(Key::Escape));
    app.frames(2);
    assert_settings_rail_closed(&app, "escape closes settings when the composer is focused");
}

/// The diagnostics overlay is a new surface. Forest, light, and deutan lock it.
/// A missing PNG is written and is not a failure the first time.
#[test]
fn diagnostics_preview_matches_its_golden_images() {
    assert_settings_goldens("diagnostics", remedy_desktop::diagnostics_preview);
}

#[test]
fn diagnostics_preview_names_the_panel_and_the_hive() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::diagnostics_preview);
    app.frames(2);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Diagnostics")),
        "diagnostics title: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Hive")),
        "hive inspector: {names:?}"
    );
    assert!(
        names.iter().any(|n| n.contains("Health diagnostics")),
        "dialog name: {names:?}"
    );
}

fn press_nth(app: &mut TestApp, role: Role, name: &str, nth: usize) -> bool {
    let Some(ui) = app.ui() else { return false };
    let nodes: Vec<_> = app
        .reader_view()
        .into_iter()
        .filter(|n| n.role == role && n.name.as_deref() == Some(name))
        .collect();
    let Some(node) = nodes.get(nth) else {
        return false;
    };
    let Some(el) = ui.element_by_node_id(node.id) else {
        return false;
    };
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
    assert!(
        app.press(Role::Button, "Swap left and right panels"),
        "swap"
    );
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
    assert!(
        press_last(&mut app, Role::Button, "Settings"),
        "status Settings"
    );
    assert!(
        app.find(Role::Button, "Expand right rail").is_none(),
        "settings opened the right rail"
    );
    assert!(
        app.find(Role::Toolbar, "Right workspace rail").is_some(),
        "right rail is icons plus the panel"
    );
    assert!(
        press_last(&mut app, Role::Button, "Settings"),
        "status Settings again"
    );
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
    app.host()
        .user_resize(id, remedy_ui::core::Size::new(700.0, 700.0));
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
        names.iter().any(|n| n.contains("Clear terminal")),
        "the right panel opened for this width only: {names:?}"
    );
    app.host()
        .user_resize(id, remedy_ui::core::Size::new(1100.0, 700.0));
    app.frames(2);
    assert!(
        app.find(Role::Button, "Expand right rail").is_some(),
        "the saved thin right rail comes back"
    );
    assert!(app.find(Role::Button, "Expand left rail").is_none());
    let names = accessible_names(&app);
    assert!(
        !names.iter().any(|n| n.contains("Clear terminal")),
        "widening does not keep the temporary panel: {names:?}"
    );
}

#[test]
fn terminal_pops_out_of_the_right_rail() {
    no_spawn();
    let mut app = preview_window(remedy_desktop::home);
    app.frames(1);
    assert!(app.press(Role::Button, "Expand right rail"));
    assert!(
        press_nth(&mut app, Role::Button, "Terminal", 1),
        "right Terminal"
    );
    assert!(
        app.press(Role::Button, "Pop out to floating window"),
        "pop out"
    );
    assert!(
        app.find(Role::Dialog, "Terminal").is_some(),
        "floating terminal"
    );
    app.key(KeyEvent::press(Key::Escape));
    assert!(
        app.find(Role::Dialog, "Terminal").is_none(),
        "escape closes it"
    );
}

#[test]
fn resize_left_panel_from_the_keyboard() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(app.press(Role::Button, "Studio"), "open Studio");
    let handle = app
        .find(Role::Separator, "Resize left panel")
        .expect("left resize handle");
    handle.focus();
    app.frames(1);
    // Layout file is process-wide; hold the lock across the write and the read
    // so a parallel test cannot delete it mid-assert.
    let saved = {
        let _g = layout_lock();
        app.key(KeyEvent::press(Key::ArrowRight));
        saved_layout_text()
    };
    assert!(
        saved.contains("\"leftWidth\":290"),
        "arrow grows the left body by 10: {saved}"
    );
}

fn first_window(app: &mut TestApp) -> remedy_ui::host::WindowId {
    app.host().window_ids().first().copied().expect("a window")
}

fn region(app: &TestApp, name: &str) -> remedy_ui::El {
    app.find(Role::Region, name)
        .unwrap_or_else(|| panic!("missing scroll region {name}"))
}

fn mid_of(el: &remedy_ui::El) -> remedy_ui::core::Point {
    el.bounds().expect("laid out").center()
}

fn offset_y(app: &TestApp, el: &remedy_ui::El) -> f32 {
    app.ui().expect("ui").scroll_offset(el.id()).y
}

fn hover(app: &mut TestApp, at: remedy_ui::core::Point) {
    use remedy_ui::host::HostEvent;
    use remedy_ui::input::{PointerEvent, PointerPhase};
    let id = first_window(app);
    app.host().send(HostEvent::Pointer(
        id,
        PointerEvent::new(PointerPhase::Move, at),
    ));
    app.frames(1);
}

fn wheel_at(app: &mut TestApp, at: remedy_ui::core::Point, delta: remedy_ui::core::Vec2) {
    let id = first_window(app);
    app.host().wheel(id, at, delta);
    app.frames(8);
}

fn force_overflow(app: &mut TestApp, el: &remedy_ui::El) -> f32 {
    el.scroll_to(remedy_ui::core::Vec2::new(0.0, 10_000.0));
    app.frames(4);
    let max = offset_y(app, el);
    el.scroll_to_exact(remedy_ui::core::Vec2::ZERO);
    app.frames(2);
    max
}

fn scroll_with_wheel_and_keys(app: &mut TestApp, name: &str) {
    let el = region(app, name);
    let bounds = el.bounds().expect("laid out");
    let max = force_overflow(app, &el);
    assert!(
        max > 1.0,
        "{name} should overflow its viewport so it can scroll (bounds {bounds:?}, max offset {max})"
    );
    let at = mid_of(&el);
    hover(app, at);
    let before = offset_y(app, &el);
    wheel_at(app, at, remedy_ui::core::Vec2::new(0.0, 200.0));
    let mut after_wheel = offset_y(app, &el);
    if (after_wheel - before).abs() < 1.0 {
        wheel_at(app, at, remedy_ui::core::Vec2::new(0.0, -200.0));
        after_wheel = offset_y(app, &el);
    }
    el.scroll_to_exact(remedy_ui::core::Vec2::ZERO);
    app.frames(2);
    hover(app, at);
    app.key(KeyEvent::press(Key::PageDown));
    let after_page = offset_y(app, &el);
    app.key(KeyEvent::press(Key::End));
    let after_end = offset_y(app, &el);
    app.key(KeyEvent::press(Key::Home));
    let after_home = offset_y(app, &el);
    assert!(
        (after_wheel - before).abs() > 1.0,
        "{name} should move with the wheel: {before} → {after_wheel} (max {max}, bounds {bounds:?})"
    );
    assert!(
        after_page > 1.0 || after_end > 1.0,
        "{name} should move with Page Down or End: page {after_page} end {after_end} (max {max})"
    );
    assert!(
        after_home.abs() < 1.0,
        "{name} Home jumps back to the start: {after_home}"
    );
}

/// Studio's empty well and Grove's left column stay scroll containers, and the
/// starter cards sit in a pane that ends above the composer.
#[test]
fn overflowing_home_panes_scroll_with_the_wheel_and_keys() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home, 1180.0, 520.0);
    app.frames(2);
    let starters = region(&app, "Home");
    let bounds = starters.bounds().expect("starters laid out");
    assert!(
        bounds.height() > 40.0,
        "the empty well is a scroll pane: {bounds:?}"
    );
    let composer = app
        .find(Role::MultilineTextInput, "Ask Remedy")
        .expect("composer");
    let c = composer.bounds().expect("composer laid out");
    assert!(
        bounds.max.y <= c.min.y + 1.5,
        "starter cards must not run under the composer: well {bounds:?} composer {c:?}"
    );

    // A finished turn is taller than a short window, so the feed must scroll.
    let mut chat = preview_window_size(remedy_desktop::home_chat, 1180.0, 520.0);
    chat.frames(2);
    scroll_with_wheel_and_keys(&mut chat, "Chat messages");

    fresh_profile();
    let mut grove = preview_window_size(remedy_desktop::grove_home, 900.0, 420.0);
    grove.frames(2);
    let plots = region(&grove, "Home plots");
    assert!(plots.bounds().is_some(), "Home plots is on screen");
    if force_overflow(&mut grove, &plots) > 1.0 {
        scroll_with_wheel_and_keys(&mut grove, "Home plots");
    }
}

/// Middle-click autoscroll, as Windows browsers have it, moves an overflowing pane.
#[test]
fn middle_click_autoscrolls_an_overflowing_home_pane() {
    use remedy_ui::host::HostEvent;
    use remedy_ui::input::{Buttons, PointerEvent, PointerPhase};
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::home_chat, 1180.0, 520.0);
    app.frames(2);
    let feed = region(&app, "Chat messages");
    let max = force_overflow(&mut app, &feed);
    assert!(
        max > 1.0,
        "Chat messages should overflow so autoscroll has room: max {max}"
    );
    let feed = region(&app, "Chat messages");
    let at = mid_of(&feed);
    let before = offset_y(&app, &feed);
    let id = first_window(&mut app);
    app.host().send(HostEvent::Pointer(
        id,
        PointerEvent::new(PointerPhase::Down, at).with_buttons(Buttons::MIDDLE),
    ));
    app.frames(1);
    // Engine pans like a grab: move the pointer up to increase the offset.
    // Try both directions so a layout change cannot hide a real regression.
    let mut after = before;
    for delta in [90.0, -90.0] {
        let far = remedy_ui::core::Point::new(at.x, at.y + delta);
        app.host().send(HostEvent::Pointer(
            id,
            PointerEvent::new(PointerPhase::Move, far).with_buttons(Buttons::MIDDLE),
        ));
        app.frames(8);
        after = offset_y(&app, &feed);
        if (after - before).abs() > 1.0 {
            break;
        }
    }
    assert!(
        (after - before).abs() > 1.0,
        "middle-click autoscroll should move the chat feed: {before} → {after}"
    );
}

/// Settings, Help, diagnostics, and the files list are scroll containers.
#[test]
fn settings_help_and_files_panes_are_scroll_containers() {
    no_spawn();
    let mut settings = preview_window_size(remedy_desktop::preview_general, 700.0, 480.0);
    settings.frames(2);
    let names = accessible_names(&settings);
    assert!(
        names
            .iter()
            .any(|n| n.contains("General") || n.contains("Appearance") || n.contains("Settings")),
        "settings preview opened: {names:?}"
    );

    let mut help = preview_window_size(remedy_desktop::help_home, 900.0, 500.0);
    help.frames(2);
    help.key(KeyEvent::press(Key::PageDown));
    let names = accessible_names(&help);
    assert!(
        names.iter().any(|n| !n.is_empty()),
        "help is on screen: {names:?}"
    );
}

#[test]
fn status_dock_shows_the_0_70_5_rail_controls() {
    use remedy_ui::a11y::Role;
    let mut app = preview_window_size(remedy_desktop::home_chat, 1180.0, 740.0);
    app.frames(4);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Toolbar, "Status").is_some(),
        "status dock is on screen: {names:?}"
    );
    for label in ["Settings", "Help", "Simple", "Speak"] {
        assert!(
            app.find(Role::Button, label).is_some(),
            "{label} belongs on the 0.70.5 status rail: {names:?}"
        );
    }
    // Memory/Skills already live in the chat header in Simple; Usage, thinking,
    // and approval are Advanced-only dock controls.
    assert!(
        app.find(Role::Button, "Usage").is_none(),
        "Usage stays in Advanced: {names:?}"
    );
    assert!(app.press(Role::Button, "Simple"), "switch to Advanced");
    app.frames(4);
    let names = accessible_names(&app);
    for label in [
        "Usage",
        "Advanced",
        "Think med",
        "Ask",
        "Web UI",
        "Time travel",
    ] {
        assert!(
            app.find(Role::Button, label).is_some(),
            "{label} belongs on the Advanced status rail: {names:?}"
        );
    }
    assert!(app.press(Role::Button, "Help"), "open Help from the rail");
    app.frames(2);
}

#[test]
fn status_dock_shows_streaming_on_a_live_turn() {
    use remedy_ui::a11y::Role;
    let mut app = preview_window_size(remedy_desktop::home_chat_live, 1180.0, 740.0);
    app.frames(4);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Status, "Streaming").is_some() || names.iter().any(|n| n == "Streaming"),
        "a live turn shows Streaming on the status dock: {names:?}"
    );
}


#[test]
fn title_bar_switches_grove_and_studio() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(
        app.find(Role::Button, "Grove").is_some(),
        "Grove is in the title bar: {:?}",
        accessible_names(&app)
    );
    assert!(
        app.find(Role::Button, "Studio").is_some(),
        "Studio is in the title bar: {:?}",
        accessible_names(&app)
    );
    assert!(
        app.find(Role::Button, "Home").is_some(),
        "Grove starts on the home view"
    );
    assert!(app.press(Role::Button, "Studio"), "open Studio from the title bar");
    app.frames(4);
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "Studio workbench is open"
    );
    assert!(app.press(Role::Button, "Grove"), "return from the title bar");
    app.frames(4);
    assert!(
        app.find(Role::Button, "Home").is_some(),
        "Grove is back after the title-bar switch"
    );
}

#[test]
fn ctrl_1_and_ctrl_2_switch_grove_and_studio() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.key(KeyEvent::press(Key::char('2')).with_modifiers(Modifiers::CTRL));
    app.frames(4);
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "Ctrl+2 opens Studio: {:?}",
        accessible_names(&app)
    );
    app.key(KeyEvent::press(Key::char('1')).with_modifiers(Modifiers::CTRL));
    app.frames(4);
    assert!(
        app.find(Role::Button, "Home").is_some(),
        "Ctrl+1 returns to Grove: {:?}",
        accessible_names(&app)
    );
}

#[test]
fn title_bar_switcher_stays_visible_when_narrow() {
    let mut app = preview_window_size(remedy_desktop::grove_home_chrome, 700.0, 520.0);
    app.frames(4);
    let names = accessible_names(&app);
    assert!(
        app.find(Role::Button, "Grove").is_some(),
        "Grove stays in the title bar at 700px: {names:?}"
    );
    assert!(
        app.find(Role::Button, "Studio").is_some(),
        "Studio stays in the title bar at 700px: {names:?}"
    );
}

struct PresenceOverrideGuard;

impl Drop for PresenceOverrideGuard {
    fn drop(&mut self) {
        remedy_desktop::set_presence_enabled_override(None);
    }
}

fn presence_image_names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter(|n| n.role == Role::Image)
        .filter_map(|n| n.name)
        .filter(|n| n.starts_with("Remedy,"))
        .collect()
}

fn assert_fits_window(app: &TestApp, width: f32, why: &str) {
    // Session lists may overflow vertically so they can scroll. Horizontal
    // overflow would mean the hidden face left a clipped or empty frame.
    for node in app.reader_view() {
        let Some(bounds) = node.bounds else { continue };
        let name = node.name.as_deref().unwrap_or("(unnamed)");
        assert!(
            bounds.min.x >= -1.5,
            "{why}: {name} starts off-screen: {bounds:?}"
        );
        assert!(
            bounds.max.x <= width + 1.5 || bounds.min.x >= width - 1.0,
            "{why}: {name} overflows width {width}: {bounds:?}"
        );
    }
}

/// Turning off Remedy's face must collapse the slot (no empty frame) and
/// leave Grove and Studio home readable at narrow, normal, and wide widths.
#[test]
fn hiding_presence_reflows_home_without_a_frame() {
    no_spawn();
    let _guard = PresenceOverrideGuard;
    let sizes = [(700.0, 520.0), (1100.0, 700.0), (1400.0, 800.0)];

    // Default is on: the face is an Image named "Remedy, …".
    let mut on = preview_window_size(remedy_desktop::grove_home_chrome, 1100.0, 700.0);
    on.frames(4);
    let faces = presence_image_names(&on);
    assert!(
        !faces.is_empty(),
        "presence defaults to on at Grove: {:?}",
        accessible_names(&on)
    );

    remedy_desktop::set_presence_enabled_override(Some(false));
    for (w, h) in sizes {
        let mut grove = preview_window_size(remedy_desktop::grove_home_presence_off, w, h);
        grove.frames(4);
        let names = accessible_names(&grove);
        assert!(
            presence_image_names(&grove).is_empty(),
            "no face on Grove at {w}x{h}: {names:?}"
        );
        assert!(
            has(&names, "Good morning") || has(&names, "Good afternoon") || has(&names, "Good evening"),
            "Grove greeting remains at {w}x{h}: {names:?}"
        );
        assert_fits_window(&grove, w, &format!("Grove {w}x{h}"));

        let mut studio = preview_window_size(remedy_desktop::home_presence_off, w, h);
        studio.frames(4);
        let names = accessible_names(&studio);
        assert!(
            presence_image_names(&studio).is_empty(),
            "no face on Studio home at {w}x{h}: {names:?}"
        );
        assert!(
            has(&names, "Ready when you are"),
            "Studio greeting remains at {w}x{h}: {names:?}"
        );
        assert_fits_window(&studio, w, &format!("Studio {w}x{h}"));
    }
}

/// chrome::TRAY_QUIT. Tray menu ids are not re-exported from the lib.
const TRAY_QUIT: u32 = 2;

#[test]
fn tray_quit_shows_the_confirm_sheet_after_hide_to_tray() {
    // Title-bar close hides the window and leaves the process running
    // (close-to-tray). Tray "Quit Remedy" used to set the confirm overlay
    // on that hidden window, so nothing appeared and the app never exited.
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(3);
    let id = *app
        .host()
        .window_ids()
        .first()
        .expect("a window opened");
    app.host().send(HostEvent::CloseRequested(id));
    app.frames(2);
    assert!(
        app.host().window(id).is_some_and(|w| !w.options().visible),
        "title-bar close hides to the tray"
    );
    assert!(
        !app.host().has_exited(),
        "close-to-tray must keep the process running"
    );

    app.host().send(HostEvent::Tray(TrayEvent::Menu(TRAY_QUIT)));
    app.frames(3);
    assert!(
        app.host().window(id).is_some_and(|w| w.options().visible),
        "tray Quit must show the window so the confirm sheet is visible"
    );
    assert!(!app.host().has_exited(), "confirm sheet is shown before quit");
    let names = accessible_names(&app);
    assert!(
        has(&names, "Quit and stop server")
            || has(&names, "Quit Remedy and stop the local server"),
        "tray Quit must show the confirm sheet: {names:?}"
    );

    assert!(
        app.press(Role::Button, "Quit and stop server"),
        "confirm Quit and stop server"
    );
    app.frames(2);
    assert!(
        app.host().has_exited(),
        "confirming tray Quit must exit the app"
    );
}

#[test]
fn clicking_the_face_swaps_it_for_the_mark_and_back() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    app.frames(3);
    assert!(
        app.find(Role::Button, "Hide Remedy's face").is_some(),
        "the face is a button that hides it: {:?}",
        accessible_names(&app)
    );
    assert!(app.press(Role::Button, "Hide Remedy's face"), "click the face");
    app.frames(3);
    assert!(
        app.find(Role::Button, "Show Remedy's face").is_some()
            && app.find(Role::Button, "Hide Remedy's face").is_none(),
        "the R mark stands in and brings the face back: {:?}",
        accessible_names(&app)
    );
    assert!(app.press(Role::Button, "Show Remedy's face"), "click the mark");
    app.frames(3);
    assert!(
        app.find(Role::Button, "Hide Remedy's face").is_some(),
        "the face is back: {:?}",
        accessible_names(&app)
    );
}

#[test]
fn the_phone_grove_is_the_conversation_and_her_work_and_nothing_else() {
    no_spawn();
    let mut app = preview_window_size(remedy_desktop::grove_phone, 412.0, 892.0);
    app.frames(3);
    let names = accessible_names(&app);
    for want in ["Talk", "Work", "Send"] {
        assert!(names.iter().any(|n| n == want), "{want} on the phone's first screen: {names:?}");
    }
    for desktop_only in ["Alongside", "Attach", "Speak", "Studio", "Storyline"] {
        assert!(
            names.iter().all(|n| n != desktop_only),
            "{desktop_only} needs the PC's own screen and must not be on the phone: {names:?}"
        );
    }
    // Nothing is laid out past the right edge at phone width.
    let off = horizontal_overflow(&app, 412.0);
    assert!(off.is_empty(), "painted off the phone's screen: {off:?}");

    // The tab is a button around its label button; either opens it.
    assert!(
        press_nth(&mut app, Role::Button, "Work", 1) || press_nth(&mut app, Role::Button, "Work", 0),
        "open her work"
    );
    app.frames(3);
    let names = accessible_names(&app);
    assert!(
        names.iter().any(|n| n.contains("Give Remedy a side job")),
        "the Work tab shows what she is doing and takes a new job: {names:?}"
    );
    let off = horizontal_overflow(&app, 412.0);
    assert!(off.is_empty(), "the Work tab paints off the phone's screen: {off:?}");
}
#[test]
fn studio_rails_stay_beside_grove() {
    let _layout = reset_saved_layout();
    fresh_profile();
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), remedy_desktop::app);
    assert!(
        app.find(Role::Button, "New session").is_some(),
        "the Sessions rail is there on Grove, the home surface"
    );
    assert!(app.press(Role::Button, "Studio"), "to Studio");
    assert!(app.press(Role::Button, "New session"), "the same rail still works");
    assert!(
        app.find(Role::Button, "Close New chat 2").is_some(),
        "and its list takes the new row"
    );
}
