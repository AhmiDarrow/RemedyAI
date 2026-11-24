//! Status-bar provider/model picker: lists configured providers and switches live.

use remedy_desktop::Studio;
use remedy_ui::AppConfig;
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use remedy_ui::{App, Ui, WindowSpec};
use serde_json::json;
use std::sync::Once;

fn no_spawn() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| unsafe {
        std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1");
        std::env::set_var("REMEDY_DESKTOP_NO_UPDATE", "1");
    });
}

fn seeded_studio() -> Studio {
    let s = Studio::preview();
    let list = remedy_desktop::parse_connected(&json!({
        "picker": [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "connected": true,
                "enabled": true,
                "last_model": "openrouter/auto",
                "models": [
                    {"id": "openrouter/auto", "name": "Auto"},
                    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4"}
                ]
            },
            {
                "id": "ollama",
                "name": "Ollama (local)",
                "connected": true,
                "enabled": true,
                "last_model": "llama3.2",
                "models": [{"id": "llama3.2", "name": "llama3.2"}]
            },
            {
                "id": "xai",
                "name": "xAI (Grok)",
                "connected": true,
                "enabled": true,
                "last_model": "grok-4.5",
                "models": [{"id": "grok-4.5", "name": "Grok 4.5"}]
            }
        ],
        "active_provider": "openrouter",
        "active_model": "openrouter/auto"
    }));
    s.providers.set(list.providers);
    s.provider.set(list.active_provider);
    s.model.set(list.active_model);
    s.picker_status
        .set(remedy_desktop::PickerStatus::Ready);
    s
}

fn names(app: &TestApp) -> Vec<String> {
    app.reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect()
}

fn open_bar(width: f32) -> (TestApp, Studio) {
    no_spawn();
    let studio = seeded_studio();
    let shown = studio.clone();
    let app = TestApp::new(AppConfig::new("model-picker"), move |app: &App| {
        let studio = shown.clone();
        app.open(
            WindowSpec::new("picker").size(width, 120.0),
            move |ui: &Ui, _| {
                ui.root()
                    .child(remedy_desktop::status_bar(ui, studio.clone(), || {}));
            },
        );
    });
    (app, studio)
}

#[test]
fn picker_lists_providers_and_models_and_switches() {
    let (mut app, studio) = open_bar(1180.0);
    let start = names(&app);
    assert!(
        start.iter().any(|n| n.contains("Auto") || n.contains("openrouter/auto")),
        "trigger should show the current model, not a stub remedy; got {start:?}"
    );
    assert!(
        start.iter().all(|n| !n.eq_ignore_ascii_case("remedy") || n.contains("Model")),
        "stub remedy must not be the only option; got {start:?}"
    );

    let opened = app.press(Role::Button, "Model: Auto")
        || start.iter().any(|n| {
            n.contains("Auto") && app.press(Role::Button, n)
        });
    assert!(opened, "should open the picker; labels were {start:?}");

    let after = names(&app);
    let joined = after.join(" | ");
    assert!(
        joined.contains("OpenRouter"),
        "OpenRouter missing from open picker: {after:?}"
    );
    assert!(
        joined.contains("Ollama"),
        "Ollama missing from open picker: {after:?}"
    );
    assert!(
        joined.contains("xAI") || joined.contains("Grok"),
        "xAI missing from open picker: {after:?}"
    );

    // Open the Ollama submenu, then switch to llama3.2.
    let ollama = after
        .iter()
        .find(|n| n.contains("Ollama"))
        .cloned()
        .expect("Ollama row");
    let opened_sub = app.press(Role::MenuItem, &ollama)
        || app.press(Role::Button, &ollama)
        || app.press(Role::Menu, &ollama);
    assert!(opened_sub, "should open the Ollama submenu; labels were {after:?}");
    let sub: Vec<(String, String)> = app
        .reader_view()
        .into_iter()
        .filter_map(|n| n.name.map(|name| (format!("{:?}", n.role), name)))
        .collect();
    let switched = ["llama3.2"].iter().any(|label| {
        app.press(Role::MenuItemRadio, label)
            || app.press(Role::MenuItem, label)
            || app.press(Role::Radio, label)
            || app.press(Role::Button, label)
    });
    assert!(
        switched || studio.model.get() == "llama3.2",
        "should be able to switch to Ollama llama3.2; opened={opened_sub} nodes were {sub:?}"
    );
    assert_eq!(studio.provider.get(), "ollama");
    assert_eq!(studio.model.get(), "llama3.2");
}

#[test]
fn picker_does_not_clip_at_narrow_normal_or_wide() {
    for width in [700.0, 1180.0, 1600.0] {
        let (app, _) = open_bar(width);
        let ui = app.ui().expect("ui");
        for n in app.reader_view() {
            let Some(el) = ui.element_by_node_id(n.id) else { continue };
            let Some(b) = el.bounds() else { continue };
            let interactive = matches!(
                n.role,
                Role::Button | Role::MenuItem | Role::ComboBox | Role::Link
            );
            if interactive && b.min.x < -1.5 || (interactive && b.max.x > width + 1.5 && b.min.x < width - 1.0)
            {
                panic!(
                    "picker chrome clipped at width {width}: {:?} {:?} x={:.1}..{:.1}",
                    n.role, n.name, b.min.x, b.max.x
                );
            }
        }
    }
}
