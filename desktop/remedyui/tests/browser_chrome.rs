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
        app.find(Role::TextInput, "Browser address — URL or search").is_some(),
        "address field"
    );
}
