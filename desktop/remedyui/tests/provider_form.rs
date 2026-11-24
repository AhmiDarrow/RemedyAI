//! Settings Models form: each provider keeps its own label, URL, and auth.
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use remedy_ui::AppConfig;

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

fn names_for(build: fn(&remedy_ui::Ui) -> remedy_ui::El) -> Vec<String> {
    let mut app = preview_window(build);
    app.frames(3);
    app.reader_view()
        .into_iter()
        .filter_map(|n| n.name)
        .collect()
}

fn preview_anthropic(ui: &remedy_ui::Ui) -> remedy_ui::El {
    remedy_desktop::preview_models_provider(ui, "anthropic")
}

fn preview_xai(ui: &remedy_ui::Ui) -> remedy_ui::El {
    remedy_desktop::preview_models_provider(ui, "xai")
}

fn preview_openrouter(ui: &remedy_ui::Ui) -> remedy_ui::El {
    remedy_desktop::preview_models_provider(ui, "openrouter")
}

fn preview_ollama(ui: &remedy_ui::Ui) -> remedy_ui::El {
    remedy_desktop::preview_models_provider(ui, "ollama")
}

fn has(names: &[String], needle: &str) -> bool {
    names.iter().any(|n| n.contains(needle))
}

#[test]
fn settings_provider_rows_show_their_own_label_url_and_auth() {
    let anthropic = names_for(preview_anthropic);
    assert!(
        has(&anthropic, "Anthropic"),
        "Anthropic label missing: {anthropic:?}"
    );
    assert!(
        !has(&anthropic, "openrouter.ai") && !has(&anthropic, "OpenRouter API"),
        "Anthropic must not show an OpenRouter URL or label: {anthropic:?}"
    );
    assert!(
        !has(&anthropic, "Sign in with xAI"),
        "Anthropic must not show xAI sign-in: {anthropic:?}"
    );

    let xai = names_for(preview_xai);
    assert!(
        has(&xai, "xAI (Grok)") || has(&xai, "xAI"),
        "xAI label missing: {xai:?}"
    );
    assert!(
        has(&xai, "Sign in with xAI"),
        "xAI must offer Sign in with xAI: {xai:?}"
    );
    assert!(
        !has(&xai, "openrouter.ai") && !has(&xai, "OpenRouter API"),
        "xAI must not show an OpenRouter URL or label: {xai:?}"
    );

    let openrouter = names_for(preview_openrouter);
    assert!(
        has(&openrouter, "OpenRouter"),
        "OpenRouter label missing: {openrouter:?}"
    );
    assert!(
        !has(&openrouter, "Sign in with xAI"),
        "OpenRouter must not show xAI sign-in: {openrouter:?}"
    );
    assert!(
        !has(&openrouter, "api.anthropic.com") && !has(&openrouter, "api.x.ai"),
        "OpenRouter must not show another provider's URL: {openrouter:?}"
    );

    let ollama = names_for(preview_ollama);
    assert!(
        has(&ollama, "Ollama"),
        "Ollama label missing: {ollama:?}"
    );
    assert!(
        has(&ollama, "Base URL"),
        "Ollama should show its own Base URL field: {ollama:?}"
    );
    assert!(
        !has(&ollama, "Sign in with xAI"),
        "Ollama must not show xAI sign-in: {ollama:?}"
    );
    assert!(
        !has(&ollama, "openrouter.ai"),
        "Ollama must not show an OpenRouter URL: {ollama:?}"
    );

    // Hidden-URL providers (Anthropic / xAI / OpenRouter) must not leave
    // a typable Base URL on screen; that was how a leftover OpenRouter
    // URL showed up on the Anthropic and Grok rows.
    let mut app = preview_window(preview_anthropic);
    app.frames(3);
    assert!(
        app.find(Role::TextInput, "Base URL").is_none(),
        "Anthropic should hide the Base URL field"
    );
    let mut app = preview_window(preview_xai);
    app.frames(3);
    assert!(
        app.find(Role::TextInput, "Base URL").is_none(),
        "xAI should hide the Base URL field"
    );
    assert!(
        app.find(Role::Button, "Sign in with xAI").is_some(),
        "xAI Sign in button must be present"
    );
    let mut app = preview_window(preview_openrouter);
    app.frames(3);
    assert!(
        app.find(Role::TextInput, "Base URL").is_none(),
        "OpenRouter should hide the Base URL field"
    );
    let mut app = preview_window(preview_ollama);
    app.frames(3);
    assert!(
        app.find(Role::TextInput, "Base URL").is_some(),
        "Ollama should show the Base URL field"
    );
}
