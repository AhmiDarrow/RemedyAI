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


fn long_id_studio() -> Studio {
    let s = Studio::preview();
    let long = "anthropic/thinkingmachines/inkling-2-mini[1m]";
    let list = remedy_desktop::parse_connected(&json!({
        "picker": [{
            "id": "anthropic",
            "name": "Anthropic",
            "connected": true,
            "enabled": true,
            "last_model": long,
            "models": [{"id": long, "name": long}]
        }],
        "active_provider": "anthropic",
        "active_model": long
    }));
    s.providers.set(list.providers);
    s.provider.set(list.active_provider);
    s.model.set(list.active_model);
    s.picker_status.set(remedy_desktop::PickerStatus::Ready);
    s
}

fn open_long_bar(width: f32, compact: bool) -> TestApp {
    no_spawn();
    let studio = long_id_studio();
    TestApp::new(AppConfig::new("model-picker-long"), move |app: &App| {
        let studio = studio.clone();
        let compact = compact;
        app.open(
            WindowSpec::new("picker").size(width, 120.0),
            move |ui: &Ui, _| {
                let bar = if compact {
                    remedy_desktop::compact_bar(ui, studio.clone())
                } else {
                    remedy_desktop::status_bar(ui, studio.clone(), || {})
                };
                ui.root().child(bar);
            },
        );
    })
}

fn named_bounds(app: &TestApp) -> Vec<(String, f32, f32, f32, f32)> {
    let ui = app.ui().expect("ui");
    let mut out = Vec::new();
    for n in app.reader_view() {
        let Some(name) = n.name.clone() else { continue };
        let Some(el) = ui.element_by_node_id(n.id) else { continue };
        let Some(b) = el.bounds() else { continue };
        out.push((name, b.min.x, b.min.y, b.max.x, b.max.y));
    }
    out
}

fn overlaps(a: (f32, f32, f32, f32), b: (f32, f32, f32, f32)) -> bool {
    let (ax0, ay0, ax1, ay1) = a;
    let (bx0, by0, bx1, by1) = b;
    ax0 < bx1 - 0.5 && ax1 > bx0 + 0.5 && ay0 < by1 - 0.5 && ay1 > by0 + 0.5
}

#[test]
fn long_model_chip_uses_short_name_and_does_not_overlap_neighbors() {
    let long = "anthropic/thinkingmachines/inkling-2-mini[1m]";
    for (width, compact) in [
        (700.0, false),
        (1180.0, false),
        (1600.0, false),
        (700.0, true),
    ] {
        let mut app = open_long_bar(width, compact);
        let labels = names(&app);
        let joined = labels.join(" | ");
        assert!(
            labels.iter().any(|n| n.contains("inkling-2-mini")),
            "width {width} compact={compact}: expected short name on the chip; got {labels:?}"
        );
        assert!(
            labels.iter().all(|n| !n.contains("anthropic/thinkingmachines")),
            "width {width} compact={compact}: full path id must not appear on the chip; got {labels:?}"
        );

        // Overlap is checked on the closed chip, before the menu opens.
        let bounds = named_bounds(&app);
        let chips: Vec<_> = bounds
            .iter()
            .filter(|(n, _, _, _, _)| n.starts_with("Model:"))
            .cloned()
            .collect();
        let simples: Vec<_> = bounds
            .iter()
            .filter(|(n, _, _, _, _)| n == "Simple" || n.contains("Simple"))
            .cloned()
            .collect();
        for (cn, cx0, cy0, cx1, cy1) in &chips {
            assert!(
                *cx0 >= -1.5 && *cx1 <= width + 1.5,
                "width {width} compact={compact}: chip '{cn}' clipped x={cx0}..{cx1}"
            );
            assert!(
                *cx1 - *cx0 <= 248.0,
                "width {width} compact={compact}: chip '{cn}' wider than 240px: {:.1}",
                *cx1 - *cx0
            );
            for (sn, sx0, sy0, sx1, sy1) in &simples {
                assert!(
                    !overlaps((*cx0, *cy0, *cx1, *cy1), (*sx0, *sy0, *sx1, *sy1)),
                    "width {width} compact={compact}: chip '{cn}' {:.1}..{:.1} overlaps '{sn}' {:.1}..{:.1}; all={bounds:?}",
                    cx0, cx1, sx0, sx1
                );
            }
        }

        let chip = labels
            .iter()
            .find(|n| n.starts_with("Model:"))
            .cloned()
            .expect("model chip");
        assert!(
            app.press(Role::Button, &chip),
            "width {width}: should open the chip; labels {labels:?}"
        );
        let open = names(&app);
        if let Some(prov) = open.iter().find(|n| n.contains("Anthropic")).cloned() {
            let _ = app.press(Role::MenuItem, &prov)
                || app.press(Role::Button, &prov)
                || app.press(Role::Menu, &prov);
        }
        let open = names(&app);
        let open_joined = open.join(" | ");
        assert!(
            open_joined.contains(long),
            "width {width}: dropdown should include the full id; got {open:?}"
        );
        let _ = joined;
    }
}
