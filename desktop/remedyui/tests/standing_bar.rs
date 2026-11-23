//! Standing bar: every port surface stays inside its box at narrow, normal, and wide sizes.

use remedy_ui::AppConfig;
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use remedy_ui::{App, WindowSpec};
use std::sync::Once;

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") });
}

fn open(name: &'static str, width: f32, height: f32, build: fn(&remedy_ui::Ui) -> remedy_ui::El) -> TestApp {
    no_spawn();
    TestApp::new(AppConfig::new("standing-bar"), move |app: &App| {
        app.open(WindowSpec::new(name).size(width, height), move |ui, _| {
            ui.root().child(build(ui));
        });
    })
}

fn overflow(app: &TestApp, width: f32, _height: f32) -> Vec<String> {
    let Some(ui) = app.ui() else {
        return vec!["no ui".into()];
    };
    let mut out = Vec::new();
    for n in app.reader_view() {
        let Some(el) = ui.element_by_node_id(n.id) else { continue };
        let Some(b) = el.bounds() else { continue };
        // Vertical overflow of a scrolling list is expected. Horizontal
        // overflow of interactive chrome (a toolbar button past the rail) is not.
        // Labels in a clipped scroll (help code, breadcrumbs) may extend in
        // layout without painting off-panel.
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
        // Breadcrumb segments live in a horizontal scroller; their layout
        // width can extend past the clip without painting off the rail.
        if let Some(name) = &n.name {
            if name.starts_with("Open ") || name.starts_with("Current folder ") {
                continue;
            }
        }
        if (left || (right && interactive)) && b.width() > 1.0 && b.height() > 1.0 {
            out.push(format!(
                "{:?} {:?} x={:.1}..{:.1} y={:.1}..{:.1}",
                n.role,
                n.name,
                b.min.x,
                b.max.x,
                b.min.y,
                b.max.y
            ));
        }
    }
    out
}

const SHELL: &[(f32, f32)] = &[(700.0, 520.0), (1180.0, 740.0), (1440.0, 900.0)];
const RAIL: &[(f32, f32)] = &[(200.0, 500.0), (300.0, 500.0), (640.0, 700.0)];

#[test]
fn shell_surfaces_stay_in_their_box() {
    let surfaces: &[(&str, fn(&remedy_ui::Ui) -> remedy_ui::El)] = &[
        ("home", remedy_desktop::home),
        ("chat", remedy_desktop::home_chat),
        ("chat-live", remedy_desktop::home_chat_live),
        ("grove", remedy_desktop::grove_home_chrome),
        ("settings", remedy_desktop::home_settings),
        ("memory", remedy_desktop::home_memory),
        ("skills", remedy_desktop::home_skills),
        ("usage", remedy_desktop::preview_usage),
        ("help", remedy_desktop::help_home),
        ("about", remedy_desktop::home_about),
        ("palette", remedy_desktop::home_palette),
        ("time-travel", remedy_desktop::time_travel_preview),
        ("settings-general", remedy_desktop::preview_general),
    ];
    let mut failures = Vec::new();
    for (name, build) in surfaces {
        for &(w, h) in SHELL {
            let mut app = open(name, w, h, *build);
            app.frames(3);
            let bad = overflow(&app, w, h);
            if !bad.is_empty() {
                failures.push(format!(
                    "{name} at {w}x{h}: {} off-box\n  {}",
                    bad.len(),
                    bad.join("\n  ")
                ));
            }
        }
    }
    assert!(failures.is_empty(), "shell standing-bar failures:\n{}", failures.join("\n\n"));
}

#[test]
fn rail_surfaces_stay_in_their_box() {
    let surfaces: &[(&str, fn(&remedy_ui::Ui) -> remedy_ui::El)] = &[
        ("files", remedy_desktop::files_preview),
        ("scratch", remedy_desktop::scratch_preview),
        ("terminal", remedy_desktop::terminal_preview),
    ];
    let mut failures = Vec::new();
    for (name, build) in surfaces {
        for &(w, h) in RAIL {
            let mut app = open(name, w, h, *build);
            app.frames(3);
            let bad = overflow(&app, w, h);
            if !bad.is_empty() {
                failures.push(format!(
                    "{name} at {w}x{h}: {} off-box\n  {}",
                    bad.len(),
                    bad.join("\n  ")
                ));
            }
        }
    }
    no_spawn();
    for &w in &[200.0, 300.0] {
        let mut app = TestApp::new(AppConfig::new("standing-bar"), move |app: &App| {
            app.open(WindowSpec::new("browser").size(w, 500.0), move |ui, _| {
                ui.root().child(remedy_desktop::browser_preview_at(ui, w));
            });
        });
        app.frames(4);
        let bad = overflow(&app, w, 500.0);
        if !bad.is_empty() {
            failures.push(format!(
                "browser at {w}x500: {} off-box\n  {}",
                bad.len(),
                bad.join("\n  ")
            ));
        }
    }
    assert!(failures.is_empty(), "rail standing-bar failures:\n{}", failures.join("\n\n"));
}
