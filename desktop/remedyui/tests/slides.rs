//! Rail slide bodies: Files, Terminal, Scratch, Automations, and pop-out.

use remedy_ui::AppConfig;
use remedy_ui::a11y::{Action, Role};
use remedy_ui::devtools::{GoldenError, Preview, check_golden};
use remedy_ui::input::{Key, KeyEvent, Modifiers};
use remedy_ui::testing::TestApp;

fn window(build: fn(&remedy_ui::Ui) -> remedy_ui::El) -> TestApp {
    TestApp::new(AppConfig::new("Remedy Desktop slides"), move |app| {
        let app_for_host = app.clone();
        app.open(
            remedy_ui::WindowSpec::new("slides").size(480.0, 700.0),
            move |ui, window| {
                remedy_desktop::bind_host(&app_for_host, window);
                ui.root().child(build(ui));
            },
        );
    })
}

fn names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect()
}

/// Clicks the control named `name` that follows a row labeled `after`.
/// `press` always hits the first match, and a roster has one Stop per task.
fn press_after(app: &mut TestApp, after: &str, role: Role, name: &str) -> bool {
    let view = app.reader_view();
    let mut seen = false;
    let mut id = None;
    for n in &view {
        let label = n.name.as_deref().unwrap_or("");
        if label.contains(after) {
            seen = true;
        }
        if seen && n.role == role && label == name {
            id = Some(n.id);
            break;
        }
    }
    let Some(id) = id else { return false };
    let Some(ui) = app.ui() else { return false };
    let Some(el) = ui.element_by_node_id(id) else {
        return false;
    };
    let done = ui.accessibility_action(el.node_id(), Action::Click);
    app.frames(1);
    done
}

#[test]
fn files_open_a_folder_and_filter() {
    let mut app = window(remedy_desktop::files_preview);
    app.frames(2);
    assert!(
        app.find(Role::Button, "components").is_some(),
        "fixture folder"
    );
    assert!(app.find(Role::Button, "App.tsx").is_some(), "fixture file");
    assert!(app.find(Role::Button, "Parent folder").is_some());
    assert!(app.press(Role::Button, "components"), "open the folder");
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "slides"),
        "entered components: {listed:?}"
    );
    assert!(
        !listed.iter().any(|n| n == "App.tsx"),
        "left the parent listing: {listed:?}"
    );

    assert!(app.press(Role::Button, "Parent folder"), "back up");
    app.frames(1);
    let filter = app.find(Role::TextInput, "Filter files").expect("filter");
    filter.focus();
    app.type_text("theme");
    app.frames(1);
    let listed = names(&app);
    assert!(listed.iter().any(|n| n == "themes.ts"), "{listed:?}");
    assert!(!listed.iter().any(|n| n == "App.tsx"), "{listed:?}");
    assert!(!listed.iter().any(|n| n == "components"), "{listed:?}");
}

#[test]
fn files_navigate_with_breadcrumbs_history_and_a_tree() {
    let mut app = window(remedy_desktop::files_preview);
    app.frames(2);
    let listed = names(&app);
    assert!(
        listed
            .iter()
            .any(|n| n == "Open desktop" || n == "Open RemedyAI"),
        "breadcrumbs: {listed:?}"
    );
    assert!(
        app.find(Role::Button, "Current folder src").is_some(),
        "current crumb: {listed:?}"
    );
    assert!(app.find(Role::Button, "Back").is_some());
    assert!(app.find(Role::Button, "Forward").is_some());
    assert!(app.find(Role::Button, "Sort: Folders").is_some());
    assert!(app.find(Role::Button, "Expand components").is_some());
    assert!(app.find(Role::Button, "Reveal App.tsx").is_some());

    assert!(
        app.press(Role::Button, "Expand components"),
        "expand in place"
    );
    app.frames(2);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "App.tsx"),
        "parent listing stays: {listed:?}"
    );
    assert!(
        listed.iter().any(|n| n == "slides"),
        "child appears under the folder: {listed:?}"
    );
    assert!(app.find(Role::Button, "Collapse components").is_some());

    assert!(app.press(Role::Button, "components"), "enter the folder");
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "slides"),
        "entered components: {listed:?}"
    );
    assert!(
        !listed.iter().any(|n| n == "App.tsx"),
        "left the parent listing: {listed:?}"
    );
    assert!(
        app.find(Role::Button, "Current folder components")
            .is_some(),
        "{listed:?}"
    );

    assert!(app.press(Role::Button, "Back"), "history back");
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "App.tsx"),
        "back to src: {listed:?}"
    );
    assert!(app.press(Role::Button, "Forward"));
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "slides"),
        "forward to components: {listed:?}"
    );

    assert!(app.press(Role::Button, "Open src"), "breadcrumb up");
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n == "App.tsx"),
        "crumb returns to src: {listed:?}"
    );

    assert!(app.press(Role::Button, "Sort: Folders"));
    app.frames(1);
    assert!(
        app.find(Role::Button, "Sort: A–Z").is_some(),
        "{:?}",
        names(&app)
    );
}

#[test]
fn scratch_wraps_long_notes_inside_the_pane() {
    let mut app = window(remedy_desktop::scratch_preview);
    app.frames(2);
    let notes = app
        .find(Role::MultilineTextInput, "Scratch pad notes")
        .expect("scratch field");
    notes.focus();
    app.type_text(" ");
    app.type_text(&"word ".repeat(24));
    app.frames(2);
    let bounds = notes.bounds().expect("scratch field laid out");
    assert!(
        bounds.width() <= 481.0 && bounds.max.x <= 481.0 && bounds.min.x >= -1.0,
        "scratch must wrap inside the 480px preview, not grow sideways: {bounds:?}"
    );
}

#[test]
fn scratch_previews_confirms_clear_and_exports() {
    let mut app = window(remedy_desktop::scratch_preview);
    app.frames(2);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n.contains("3 lines")),
        "stats: {listed:?}"
    );
    assert!(app.press(Role::Button, "Preview"), "markdown preview");
    app.frames(1);
    assert!(
        app.find(Role::Button, "Edit").is_some(),
        "preview toggles to Edit"
    );
    assert!(app.press(Role::Button, "Edit"));
    app.frames(1);
    assert!(app.find(Role::Button, "Preview").is_some());

    assert!(app.press(Role::Button, "Clear"));
    app.frames(1);
    assert!(
        app.find(Role::Button, "Clear pad").is_some(),
        "confirm is in the window: {:?}",
        names(&app)
    );
    assert!(app.press(Role::Button, "Cancel"));
    app.frames(1);
    assert!(
        app.find(Role::Button, "Clear pad").is_none(),
        "confirm closed"
    );
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n.contains("3 lines")),
        "text kept: {listed:?}"
    );

    let dest =
        std::env::temp_dir().join(format!("remedy-scratch-{}-export.md", std::process::id()));
    let _ = std::fs::remove_file(&dest);
    app.host().answer_dialog(vec![dest.clone()]);
    let notes = app
        .find(Role::MultilineTextInput, "Scratch pad notes")
        .expect("scratch field");
    notes.focus();
    app.key(KeyEvent::press(Key::char('s')).with_modifiers(Modifiers::CTRL));
    app.frames(2);
    assert!(dest.is_file(), "export wrote {}", dest.display());
    let body = std::fs::read_to_string(&dest).unwrap_or_default();
    assert!(body.contains("port the rail slides"), "{body}");
    let _ = std::fs::remove_file(&dest);
}

#[test]
fn automations_spawn_and_stop_are_two_steps() {
    let mut app = window(remedy_desktop::automations_preview);
    app.frames(2);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n.contains("Watch the build")),
        "{listed:?}"
    );
    assert!(
        listed.iter().any(|n| n.contains("1 one-time")),
        "{listed:?}"
    );
    assert!(
        listed.iter().any(|n| n.contains("1 repeating")),
        "{listed:?}"
    );
    let start = app.node(Role::Button, "Start task").expect("start");
    assert!(start.disabled, "empty goal cannot start");

    let goal = app
        .find(Role::MultilineTextInput, "Task instructions")
        .expect("goal");
    goal.focus();
    app.type_text("Ship the notes");
    app.frames(1);
    assert!(app.press(Role::Button, "Start task"), "spawn");
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n.contains("Ship the notes")),
        "{listed:?}"
    );
    assert!(
        listed.iter().any(|n| n.contains("2 one-time")),
        "{listed:?}"
    );

    assert!(
        press_after(&mut app, "Watch the build", Role::Button, "Stop"),
        "arm the forager, not the task just started"
    );
    app.frames(1);
    assert!(
        app.find(Role::Button, "Confirm stop").is_some(),
        "{:?}",
        names(&app)
    );
    assert!(app.press(Role::Button, "Confirm stop"));
    app.frames(1);
    let listed = names(&app);
    assert!(
        !listed.iter().any(|n| n.contains("Watch the build")),
        "stopped task leaves the live roster: {listed:?}"
    );
    assert!(app.press(Role::Check, "Include stopped"));
    app.frames(1);
    let listed = names(&app);
    assert!(
        listed.iter().any(|n| n.contains("Watch the build")),
        "{listed:?}"
    );
}

#[test]
fn terminal_copies_a_selection_and_pops_out() {
    let mut app = window(remedy_desktop::terminal_preview);
    app.frames(2);
    assert!(app.find(Role::Button, "Clear terminal").is_some());
    assert!(app.find(Role::Button, "Restart").is_some());
    assert!(app.find(Role::Button, "Copy working directory").is_some());
    assert!(app.find(Role::Button, "Pop out Terminal").is_some());
    assert!(app.find(Role::Button, "Fullscreen Terminal").is_some());
    let term = app
        .find(Role::MultilineTextInput, "Terminal")
        // The screen is named after the shell, which is PowerShell on Windows.
        .or_else(|| app.find(Role::MultilineTextInput, "PowerShell"))
        .expect("terminal");
    term.focus();
    app.frames(1);
    app.key(KeyEvent::press(Key::char('a')).with_modifiers(Modifiers::CTRL));
    app.key(KeyEvent::press(Key::char('c')).with_modifiers(Modifiers::CTRL));
    let copied = app
        .host()
        .clipboard()
        .map(|(text, _)| text.to_string())
        .unwrap_or_default();
    assert!(
        copied.contains("ahmi@remedy"),
        "Ctrl+A then Ctrl+C copied the screen: {copied:?}"
    );

    assert!(app.press(Role::Button, "Pop out Terminal"));
    app.frames(2);
    assert!(
        app.find(Role::Button, "Close").is_some(),
        "pop-out chrome: {:?}",
        names(&app)
    );
    assert!(app.find(Role::Button, "Fullscreen").is_some());
    assert!(app.press(Role::Button, "Fullscreen"));
    app.frames(1);
    assert!(
        app.find(Role::Button, "Exit fullscreen").is_some(),
        "{:?}",
        names(&app)
    );
    app.key(KeyEvent::press(Key::Escape));
    assert!(
        app.find(Role::Button, "Fullscreen").is_some(),
        "Esc leaves fullscreen: {:?}",
        names(&app)
    );
    app.key(KeyEvent::press(Key::Escape));
    assert!(
        app.find(Role::Button, "Close").is_none(),
        "Esc closes the pop-out"
    );
    assert!(
        app.find(Role::Button, "Pop out Terminal").is_some(),
        "rail returns"
    );
}

#[test]
fn studio_shell_preview_does_not_mount_a_slide() {
    let mut app = TestApp::new(AppConfig::new("Remedy Desktop test"), |app| {
        app.open(
            remedy_ui::WindowSpec::new("preview").size(1100.0, 700.0),
            |ui, _| {
                ui.root().child(remedy_desktop::home(ui));
            },
        );
    });
    app.frames(1);
    let listed = names(&app);
    assert!(
        !listed
            .iter()
            .any(|n| n.contains("Pop out") || n == "Workspace" || n.contains("BACKGROUND TASKS")),
        "home() stays the studio shell: {listed:?}"
    );
}

fn assert_slide_goldens(name: &str, build: fn(&remedy_ui::Ui) -> remedy_ui::El) {
    let preview = Preview::new(name, build)
        .sizes(&[(480.0, 700.0)])
        .themes(&["forest", "deutan"]);
    match check_golden(&preview, std::path::Path::new("tests/golden")) {
        Ok(()) | Err(GoldenError::Missing(_)) => {}
        Err(e) => panic!("{e}"),
    }
}

#[test]
fn slide_previews_match_their_golden_images() {
    assert_slide_goldens("workspace-files", remedy_desktop::files_preview);
    assert_slide_goldens("workspace-terminal", remedy_desktop::terminal_preview);
    assert_slide_goldens("workspace-scratch", remedy_desktop::scratch_preview);
    assert_slide_goldens("workspace-automations", remedy_desktop::automations_preview);
}

#[test]
fn context_menu_decision_matches_the_terminal_slide() {
    assert_eq!(remedy_desktop::terminal_context_items(true), vec!["Copy"]);
    assert_eq!(remedy_desktop::terminal_context_items(false), vec!["Paste"]);
}
