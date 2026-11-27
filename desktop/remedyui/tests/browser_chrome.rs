//! Browser chrome on the in-memory host. No sidecar, no window.

use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use std::sync::Once;

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") });
}

#[test]
fn browser_chrome_has_go_back_and_an_address_field() {
    no_spawn();
    let app = TestApp::window(|ui, _window| {
        ui.root().child(remedy_desktop::browser_preview(ui));
    });
    assert!(app.find(Role::Button, "Go").is_some(), "Go");
    assert!(app.find(Role::Button, "Back").is_some(), "Back");
    assert!(
        app.find(Role::TextInput, "Browser address — URL or search")
            .is_some(),
        "address field"
    );
}

fn preview_at(width: f32) -> TestApp {
    no_spawn();
    TestApp::new(remedy_ui::AppConfig::new("browser-rail"), move |app| {
        app.open(
            remedy_ui::WindowSpec::new("browser-rail").size(width, 400.0),
            move |ui, _| {
                ui.root()
                    .child(remedy_desktop::browser_preview_at(ui, width));
            },
        );
    })
}

fn button_names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter(|n| n.role == Role::Button)
        .filter_map(|n| n.name)
        .collect()
}

fn max_button_x(app: &TestApp) -> f32 {
    app.find_all(Role::Button)
        .into_iter()
        .filter_map(|el| el.bounds())
        .map(|b| b.max.x)
        .fold(0.0, f32::max)
}

#[test]
fn browser_toolbar_fits_a_narrow_rail() {
    for width in [200.0, 280.0, 300.0] {
        let mut app = preview_at(width);
        app.frames(4);
        let names = button_names(&app);
        assert!(
            app.find(Role::TextInput, "Browser address — URL or search")
                .is_some(),
            "{width} px: address field: {names:?}"
        );
        assert!(
            names.iter().any(|n| n == "Back"),
            "{width} px: Back stays in the bar: {names:?}"
        );
        assert!(
            names.iter().any(|n| n == "More"),
            "{width} px: trailing actions move into More: {names:?}"
        );
        // The shield is an icon with this accessible name; the wide pill is gone
        // so the bar stays inside the rail.
        assert!(
            max_button_x(&app) <= width + 1.0,
            "{width} px: toolbar overflowed, max_x={}",
            max_button_x(&app)
        );

        if width == 300.0 {
            assert!(app.press(Role::Button, "More"), "open More at {width}");
            app.frames(2);
            let after: Vec<String> = app
                .reader_view()
                .into_iter()
                .filter_map(|n| n.name)
                .collect();
            assert!(
                after.iter().any(|n| n == "Go"
                    || n == "Bookmark"
                    || n == "Bookmarks"
                    || n == "Desktop site"),
                "More menu lists overflowed browser actions: {after:?}"
            );
        }
    }
}

/// What is typed has to be readable: the address keeps most of the bar.
#[test]
fn the_address_field_keeps_room_to_read() {
    for (width, least) in [(640.0, 300.0), (420.0, 170.0)] {
        let mut app = preview_at(width);
        app.frames(4);
        let field = app
            .find(Role::TextInput, "Browser address — URL or search")
            .expect("address field");
        let b = field.bounds().expect("laid out");
        let w = b.max.x - b.min.x;
        assert!(w >= least, "{width} px: the address is {w} px wide");
        assert!(max_button_x(&app) <= width + 1.0, "{width} px: toolbar overflowed");
    }
}
