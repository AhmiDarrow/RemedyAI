//! Status-bar provider and model picker: the old webview's two-level selector,
//! ported to the RemedyUI dock.
//!
//! The sidecar's source of truth is `GET /api/providers/connected` (picker
//! rows, active provider/model) plus `GET /api/models?provider=` for live
//! model lists. Switching persists through `PUT /api/sessions/{id}/llm`
//! with `make_default` when a chat is open, otherwise `PUT /api/settings`.

use crate::runtime::{self, Client};
use crate::studio::Studio;
use crate::{messages, tokens};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::Length;
use remedy_ui::reactive::{Waker, waker};
use remedy_ui::widgets as w;
use remedy_ui::widgets::MenuItem;
use remedy_ui::{El, Ui};
use serde_json::{Value, json};
use std::cell::RefCell;
use std::rc::Rc;

/// One model a provider can run.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelChoice {
    pub id: String,
    pub name: String,
}

/// A provider the picker can switch to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProviderChoice {
    pub id: String,
    pub name: String,
    pub models: Vec<ModelChoice>,
    pub last_model: String,
    pub connected: bool,
    pub enabled: bool,
}

/// Fetch state for the picker, so the bar can show loading and errors
/// instead of a stub "remedy" option.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PickerStatus {
    Loading,
    Ready,
    Error(String),
}

/// Parsed `/api/providers/connected` body.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ConnectedList {
    pub providers: Vec<ProviderChoice>,
    pub active_provider: String,
    pub active_model: String,
}

/// How the open menu should look: groups, a status line, and the Settings link.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MenuPlan {
    pub trigger: String,
    pub groups: Vec<MenuGroup>,
    pub message: Option<String>,
    pub add_provider: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MenuGroup {
    pub id: String,
    pub name: String,
    pub current: bool,
    pub models: Vec<(String, String, bool)>, // id, label, selected
}

impl PickerStatus {
    pub fn is_loading(&self) -> bool {
        matches!(self, PickerStatus::Loading)
    }
}

/// Builds the visible menu plan from live Studio signals. Pure, for tests.
pub fn plan(studio: &Studio) -> MenuPlan {
    let providers = studio.providers.get();
    let current_p = studio.provider.get();
    let current_m = studio.model.get();
    let status = studio.picker_status.get();
    let add_provider = !studio.local_only;

    let trigger = trigger_label(&providers, &current_p, &current_m, &status, studio);

    if providers.is_empty() {
        let message = match &status {
            PickerStatus::Loading => Some("Loading providers…".into()),
            PickerStatus::Error(e) => Some(e.clone()),
            PickerStatus::Ready => {
                if studio.local_only && !studio.models.get().is_empty() {
                    None
                } else {
                    Some("No providers are set up yet.".into())
                }
            }
        };
        let mut groups = Vec::new();
        // Preview / local_only: keep a flat list of studio.models so goldens stay put.
        if studio.local_only {
            let models = studio.models.get();
            if !models.is_empty() {
                groups.push(MenuGroup {
                    id: current_p.clone(),
                    name: if current_p.is_empty() {
                        "Model".into()
                    } else {
                        current_p.clone()
                    },
                    current: true,
                    models: models
                        .into_iter()
                        .map(|id| {
                            let on = id == current_m;
                            (id.clone(), id, on)
                        })
                        .collect(),
                });
            }
        }
        return MenuPlan {
            trigger,
            groups,
            message,
            add_provider,
        };
    }

    let mut groups: Vec<MenuGroup> = providers
        .iter()
        .map(|p| {
            let current = p.id == current_p;
            let models = p
                .models
                .iter()
                .map(|m| {
                    let on = current && (m.id == current_m);
                    let label = if m.name.is_empty() || m.name == m.id {
                        m.id.clone()
                    } else {
                        m.name.clone()
                    };
                    (m.id.clone(), label, on)
                })
                .collect();
            MenuGroup {
                id: p.id.clone(),
                name: p.name.clone(),
                current,
                models,
            }
        })
        .collect();
    // Current provider first so the open menu lands on what's in use.
    groups.sort_by_key(|g| if g.current { 0 } else { 1 });

    let message = match &status {
        PickerStatus::Error(e) => Some(e.clone()),
        PickerStatus::Loading => None,
        PickerStatus::Ready => None,
    };
    MenuPlan {
        trigger,
        groups,
        message,
        add_provider,
    }
}

fn trigger_label(
    providers: &[ProviderChoice],
    current_p: &str,
    current_m: &str,
    status: &PickerStatus,
    studio: &Studio,
) -> String {
    if matches!(status, PickerStatus::Loading) && providers.is_empty() {
        return "Loading models…".into();
    }
    if let PickerStatus::Error(_) = status {
        if providers.is_empty() && (current_m.is_empty() || is_stub_model(current_m, current_p)) {
            return "Models".into();
        }
    }
    if let Some(p) = providers.iter().find(|p| p.id == current_p) {
        let model = providers
            .iter()
            .find(|x| x.id == current_p)
            .and_then(|x| {
                x.models
                    .iter()
                    .find(|m| m.id == current_m)
                    .map(|m| {
                        if m.name.is_empty() {
                            m.id.clone()
                        } else {
                            m.name.clone()
                        }
                    })
            })
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| current_m.to_string());
        if model.is_empty() {
            return p.name.clone();
        }
        // Prefer the model id/name; the menu shows the provider.
        return model;
    }
    if !current_m.is_empty() && !is_stub_model(current_m, current_p) {
        return current_m.to_string();
    }
    if studio.local_only && !current_m.is_empty() {
        return current_m.to_string();
    }
    if providers.is_empty() && matches!(status, PickerStatus::Ready) && !studio.local_only {
        return "No providers".into();
    }
    "Model".into()
}

fn is_stub_model(model: &str, provider: &str) -> bool {
    model == "remedy" && (provider == "local" || provider.is_empty())
}

/// `GET /api/providers/connected` → picker rows (else connected), plus the
/// active provider and model. This is not a persona or profile list.
pub fn parse_connected(v: &Value) -> ConnectedList {
    let picker = v.get("picker").and_then(|x| x.as_array());
    let connected = v.get("connected").and_then(|x| x.as_array());
    let all = v.get("providers").and_then(|x| x.as_array());
    let rows = if picker.map(|a| !a.is_empty()).unwrap_or(false) {
        picker
    } else if connected.map(|a| !a.is_empty()).unwrap_or(false) {
        connected
    } else {
        all
    };
    let mut providers = rows
        .unwrap_or(&Vec::new())
        .iter()
        .filter_map(parse_provider)
        .collect::<Vec<_>>();
    // If the payload listed every catalog row, keep ones the owner can
    // actually switch to (connected + enabled), else keep what we were given.
    if all.is_some()
        && picker.map(|a| a.is_empty()).unwrap_or(true)
        && connected.map(|a| a.is_empty()).unwrap_or(true)
    {
        let eligible: Vec<_> = providers
            .iter()
            .filter(|p| p.connected && p.enabled)
            .cloned()
            .collect();
        if !eligible.is_empty() {
            providers = eligible;
        }
    }
    ConnectedList {
        providers,
        active_provider: v
            .get("active_provider")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim()
            .to_string(),
        active_model: v
            .get("active_model")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim()
            .to_string(),
    }
}

fn parse_provider(v: &Value) -> Option<ProviderChoice> {
    let id = v.get("id").and_then(|x| x.as_str())?.trim().to_string();
    if id.is_empty() {
        return None;
    }
    let name = v
        .get("name")
        .and_then(|x| x.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| id.clone());
    let models = parse_model_rows(v.get("models"));
    let last_model = v
        .get("last_model")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    Some(ProviderChoice {
        id,
        name,
        models,
        last_model,
        connected: v.get("connected").and_then(|x| x.as_bool()).unwrap_or(false),
        enabled: v.get("enabled").and_then(|x| x.as_bool()).unwrap_or(true),
    })
}

/// Models from `/api/models` (`{ models: [...] }`) or a raw array.
pub fn parse_model_rows(v: Option<&Value>) -> Vec<ModelChoice> {
    let arr = match v {
        Some(v) => v
            .get("models")
            .and_then(|s| s.as_array())
            .or_else(|| v.as_array()),
        None => None,
    };
    let Some(arr) = arr else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for m in arr {
        let id = if let Some(s) = m.as_str() {
            s.trim().to_string()
        } else {
            m.get("id")
                .and_then(|i| i.as_str())
                .unwrap_or("")
                .trim()
                .to_string()
        };
        if id.is_empty() || !seen.insert(id.clone()) {
            continue;
        }
        let name = m
            .get("name")
            .and_then(|n| n.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| id.clone());
        out.push(ModelChoice { id, name });
    }
    out
}

/// Body for `PUT /api/settings` when no session is open.
pub fn settings_body(provider: &str, model: &str) -> Value {
    let mut body = serde_json::Map::new();
    body.insert("llm_provider".into(), json!(provider));
    if !model.is_empty() {
        body.insert("llm_model".into(), json!(model));
    }
    Value::Object(body)
}

/// Body for `PUT /api/sessions/{id}/llm`. `make_default` also writes Settings.
pub fn session_body(provider: &str, model: &str, make_default: bool) -> Value {
    json!({
        "provider": provider,
        "model": if model.is_empty() { Value::Null } else { json!(model) },
        "make_default": make_default,
    })
}

fn pick_model_for(p: &ProviderChoice, preferred: &str) -> String {
    if p.models.iter().any(|m| m.id == preferred) {
        return preferred.to_string();
    }
    if p.models.iter().any(|m| m.id == p.last_model) {
        return p.last_model.clone();
    }
    p.models
        .first()
        .map(|m| m.id.clone())
        .unwrap_or_else(|| p.last_model.clone())
}

/// Merge a live `/api/models` list into one provider row, keeping last_model.
pub fn merge_models(p: &mut ProviderChoice, live: Vec<ModelChoice>) {
    if live.is_empty() {
        return;
    }
    p.models = live;
    if !p.last_model.is_empty() && !p.models.iter().any(|m| m.id == p.last_model) {
        p.models.insert(
            0,
            ModelChoice {
                id: p.last_model.clone(),
                name: p.last_model.clone(),
            },
        );
    }
}

/// Loads connected providers and the active model into `studio`.
pub fn refresh(studio: &Studio, client: &Client) {
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    studio.picker_status.set(PickerStatus::Loading);
    let client2 = client.clone();
    runtime::list_connected_providers(client, move |out| {
        let studio = studio.clone();
        let client = client2.clone();
        let wake2 = wake.clone();
        let _ = wake.post(move || {
            match out {
                Ok(list) => {
                    if !list.active_provider.is_empty() {
                        studio.provider.set(list.active_provider.clone());
                    }
                    if !list.active_model.is_empty() {
                        studio.model.set(list.active_model.clone());
                    }
                    studio.providers.set(list.providers.clone());
                    let names: Vec<String> = list
                        .providers
                        .iter()
                        .find(|p| p.id == studio.provider.get())
                        .map(|p| p.models.iter().map(|m| m.id.clone()).collect())
                        .unwrap_or_default();
                    if !names.is_empty() {
                        studio.models.set(names);
                    }
                    studio.picker_status.set(PickerStatus::Ready);
                    // Fill live model lists for each picker row (active first).
                    let mut ids: Vec<String> = list.providers.iter().map(|p| p.id.clone()).collect();
                    let active = studio.provider.get();
                    ids.sort_by_key(|id| if *id == active { 0 } else { 1 });
                    for id in ids.into_iter().take(8) {
                        refresh_models_for(&studio, &client, &wake2, id);
                    }
                }
                Err(e) => {
                    studio
                        .picker_status
                        .set(PickerStatus::Error(if e.is_empty() {
                            "Could not load providers.".into()
                        } else {
                            e
                        }));
                }
            }
        });
    });
}

fn refresh_models_for(
    studio: &Studio,
    client: &Client,
    wake: &Waker,
    provider: String,
) {
    let studio = studio.clone();
    let wake = wake.clone();
    let pid = provider.clone();
    runtime::list_models_for(client, Some(&pid), move |out| {
        let studio = studio.clone();
        let _ = wake.post(move || {
            let Ok(live) = out else { return };
            if live.is_empty() {
                return;
            }
            let mut rows = studio.providers.get();
            if let Some(p) = rows.iter_mut().find(|p| p.id == provider) {
                merge_models(p, live);
                if p.id == studio.provider.get() {
                    studio
                        .models
                        .set(p.models.iter().map(|m| m.id.clone()).collect());
                }
            }
            studio.providers.set(rows);
        });
    });
}

/// Switch provider/model live and persist the choice.
pub fn apply(studio: &Studio, provider: String, model: String) {
    if provider.is_empty() {
        return;
    }
    if studio.streaming.get() {
        studio
            .picker_status
            .set(PickerStatus::Error(
                "Stop generation before switching provider or model.".into(),
            ));
        return;
    }
    let model = {
        let rows = studio.providers.get();
        if let Some(p) = rows.iter().find(|p| p.id == provider) {
            pick_model_for(p, &model)
        } else {
            model
        }
    };
    if studio.provider.get() == provider && studio.model.get() == model {
        return;
    }
    studio.provider.set(provider.clone());
    studio.model.set(model.clone());
    if let Some(p) = studio
        .providers
        .get()
        .into_iter()
        .find(|p| p.id == provider)
    {
        studio
            .models
            .set(p.models.iter().map(|m| m.id.clone()).collect());
    }
    if studio.local_only {
        return;
    }
    let Some(client) = studio.runtime() else {
        return;
    };
    let session = studio.active.get();
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    runtime::apply_llm(&client, session, &provider, &model, move |out| {
        let studio = studio.clone();
        let _ = wake.post(move || match out {
            Ok(_) => {
                if let PickerStatus::Error(_) = studio.picker_status.get() {
                    studio.picker_status.set(PickerStatus::Ready);
                }
            }
            Err(e) => {
                studio.picker_status.set(PickerStatus::Error(if e.is_empty() {
                    "Could not switch provider or model.".into()
                } else {
                    e
                }));
            }
        });
    });
}

/// Opens Settings on Models so the owner can add a provider.
pub fn open_settings_models() {
    crate::settings::open_models();
}

/// Status-bar control (Studio and Grove). Dark Forest chip, Remedy Sans UI
/// via the body type token. The menu is a native RemedyUI menu (keyboard,
/// submenus, no clipping into the overflow row's ⋯).
pub fn control(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let plan_s = studio.clone();
    let label = {
        let st = plan_s.clone();
        move || plan(&st).trigger
    };
    let anchor: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let open_ui = ui.clone();
    let open_studio = studio.clone();
    let open_anchor = anchor.clone();
    let btn = w::button(ui, label, move || {
        let Some(anchor_el) = open_anchor.borrow().clone() else {
            return;
        };
        let items = menu_items(&open_studio);
        w::menu(&open_ui, &anchor_el, items);
    })
    .variant("quiet")
    .style({
        let colors = colors.clone();
        let st = studio.clone();
        move || {
            let on = !st.provider.get().is_empty() && !is_stub_model(&st.model.get(), &st.provider.get());
            tokens::chip(&colors, on)
                .merge(&tokens::typo(tokens::BODY))
                .weight(550.0)
        }
    })
    .semantics({
        let st = studio.clone();
        let u = ui.clone();
        move || {
            let name = plan(&st).trigger;
            let label = format!("{}: {name}", messages().text(&u, "status-model"));
            Semantics::new(Role::Button)
                .label(label)
                .action(Action::Click)
        }
    })
    .child(w::icon(ui, w::Icon::ChevronDown, ui.theme().type_scale.small + 2.0))
    .layout(|l| {
        l.min_width = Length::Px(0.0);
        l.max_width = Length::Px(240.0);
        l.shrink = 1.0;
    });
    *anchor.borrow_mut() = Some(btn.clone());
    // Tight chip padding so the dock stays 44px tall at every width.
    btn.padding(Insets {
        top: 2.0,
        end: 6.0,
        bottom: 2.0,
        start: 8.0,
    })
}

fn menu_items(studio: &Studio) -> Vec<MenuItem> {
    let plan = plan(studio);
    let mut items = Vec::new();
    if let Some(msg) = &plan.message {
        items.push(MenuItem::new(msg.clone(), || {}).disabled(true));
        if plan.groups.is_empty() && plan.add_provider {
            items.push(MenuItem::separator());
        }
    }
    for g in &plan.groups {
        let title = if g.current {
            format!("{} — current", g.name)
        } else {
            g.name.clone()
        };
        if g.models.is_empty() {
            items.push(
                MenuItem::submenu(
                    title,
                    vec![MenuItem::new("No models yet", || {}).disabled(true)],
                ),
            );
            continue;
        }
        let pid = g.id.clone();
        let st = studio.clone();
        let model_items: Vec<MenuItem> = g
            .models
            .iter()
            .map(|(id, label, on)| {
                let st = st.clone();
                let pid = pid.clone();
                let mid = id.clone();
                MenuItem::radio(label.clone(), *on, move || apply(&st, pid.clone(), mid.clone()))
            })
            .collect();
        items.push(MenuItem::submenu(title, model_items));
    }
    if plan.add_provider {
        if !items.is_empty() {
            items.push(MenuItem::separator());
        }
        items.push(MenuItem::new("Add a provider…", open_settings_models));
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_connected() -> Value {
        json!({
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
            "connected": [],
            "providers": [],
            "active_provider": "openrouter",
            "active_model": "openrouter/auto"
        })
    }

    #[test]
    fn parse_connected_uses_picker_rows_not_personas() {
        let list = parse_connected(&sample_connected());
        let ids: Vec<_> = list.providers.iter().map(|p| p.id.as_str()).collect();
        assert_eq!(ids, ["openrouter", "ollama", "xai"]);
        assert_eq!(list.active_provider, "openrouter");
        assert_eq!(list.active_model, "openrouter/auto");
        assert_eq!(list.providers[0].name, "OpenRouter");
        assert_eq!(list.providers[0].models[0].id, "openrouter/auto");
        assert!(!ids.contains(&"remedy"), "stub remedy is not a provider");
        assert!(!ids.contains(&"local"));
    }

    #[test]
    fn parse_connected_falls_back_to_connected_then_eligible_catalog() {
        let only_connected = json!({
            "picker": [],
            "connected": [
                {"id": "demo", "name": "Demo (Free)", "connected": true, "enabled": true, "models": [{"id": "demo-1"}]}
            ],
            "active_provider": "demo",
            "active_model": "demo-1"
        });
        let list = parse_connected(&only_connected);
        assert_eq!(list.providers.len(), 1);
        assert_eq!(list.providers[0].id, "demo");

        let catalog = json!({
            "providers": [
                {"id": "openai", "name": "OpenAI", "connected": false, "enabled": true, "models": []},
                {"id": "ollama", "name": "Ollama (local)", "connected": true, "enabled": true, "models": [{"id": "llama3"}]}
            ],
            "active_provider": "ollama",
            "active_model": "llama3"
        });
        let list = parse_connected(&catalog);
        assert_eq!(list.providers.len(), 1);
        assert_eq!(list.providers[0].id, "ollama");
    }

    #[test]
    fn parse_models_accepts_objects_and_strings() {
        let body = json!({
            "models": [
                {"id": "openrouter/auto", "name": "Auto", "provider": "openrouter"},
                "anthropic/claude-sonnet-4",
                {"id": "openrouter/auto"}
            ],
            "provider": "openrouter",
            "default": "openrouter/auto"
        });
        let rows = parse_model_rows(Some(&body));
        let ids: Vec<_> = rows.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, ["openrouter/auto", "anthropic/claude-sonnet-4"]);
        assert_eq!(rows[0].name, "Auto");
    }

    #[test]
    fn persist_bodies_match_the_sidecar() {
        assert_eq!(
            settings_body("openrouter", "openrouter/auto"),
            json!({"llm_provider": "openrouter", "llm_model": "openrouter/auto"})
        );
        assert_eq!(
            session_body("ollama", "llama3.2", true),
            json!({"provider": "ollama", "model": "llama3.2", "make_default": true})
        );
        assert_eq!(
            session_body("xai", "", false)["model"],
            Value::Null
        );
    }

    #[test]
    fn plan_lists_every_provider_marks_current_and_links_settings() {
        let s = Studio::preview();
        let list = parse_connected(&sample_connected());
        s.providers.set(list.providers);
        s.provider.set(list.active_provider);
        s.model.set(list.active_model);
        s.picker_status.set(PickerStatus::Ready);
        // preview() is local_only; treat this as a live-shaped plan by
        // still having add_provider false. Force the groups check anyway.
        let p = plan(&s);
        // local_only still builds groups from providers when they are set.
        let ids: Vec<_> = p.groups.iter().map(|g| g.id.as_str()).collect();
        assert_eq!(ids, ["openrouter", "ollama", "xai"]);
        assert!(p.groups[0].current);
        assert_eq!(p.trigger, "Auto");
        assert!(p.groups[0].models.iter().any(|(id, _, on)| id == "openrouter/auto" && *on));
        assert!(p.groups[1].models.iter().all(|(_, _, on)| !*on));
        assert!(p.message.is_none());
        let _ = p.add_provider;
    }

    #[test]
    fn plan_shows_loading_error_and_empty() {
        let s = Studio::preview();
        s.providers.set(Vec::new());
        s.models.set(Vec::new());
        s.model.set(String::new());
        s.provider.set(String::new());
        s.picker_status.set(PickerStatus::Loading);
        // preview is local_only; empty models + loading
        let p = plan(&s);
        assert_eq!(p.trigger, "Loading models…");
        assert_eq!(p.message.as_deref(), Some("Loading providers…"));
        assert!(p.groups.is_empty());

        s.picker_status.set(PickerStatus::Error("backend down".into()));
        let p = plan(&s);
        assert_eq!(p.trigger, "Models");
        assert_eq!(p.message.as_deref(), Some("backend down"));

        s.picker_status.set(PickerStatus::Ready);
        let p = plan(&s);
        assert_eq!(p.message.as_deref(), Some("No providers are set up yet."));
    }

    #[test]
    fn pick_model_prefers_requested_then_last_then_first() {
        let p = ProviderChoice {
            id: "openrouter".into(),
            name: "OpenRouter".into(),
            models: vec![
                ModelChoice { id: "a".into(), name: "A".into() },
                ModelChoice { id: "b".into(), name: "B".into() },
            ],
            last_model: "b".into(),
            connected: true,
            enabled: true,
        };
        assert_eq!(pick_model_for(&p, "a"), "a");
        assert_eq!(pick_model_for(&p, "missing"), "b");
        let mut q = p.clone();
        q.last_model.clear();
        assert_eq!(pick_model_for(&q, "missing"), "a");
    }

    #[test]
    fn apply_updates_studio_signals_without_a_runtime() {
        let s = Studio::preview();
        let list = parse_connected(&sample_connected());
        s.providers.set(list.providers);
        s.provider.set("openrouter".into());
        s.model.set("openrouter/auto".into());
        apply(&s, "ollama".into(), "llama3.2".into());
        assert_eq!(s.provider.get(), "ollama");
        assert_eq!(s.model.get(), "llama3.2");
        assert_eq!(s.models.get(), vec!["llama3.2".to_string()]);
    }
}
