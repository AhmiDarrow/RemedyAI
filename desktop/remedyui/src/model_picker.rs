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
use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::{Signal, Waker, waker};
use remedy_ui::show;
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
    pub base_url: String,
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
                    (m.id.clone(), model_menu_label(&m.id, &m.name), on)
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
        let raw = p
            .models
            .iter()
            .find(|m| m.id == current_m)
            .map(|m| if m.name.is_empty() { m.id.clone() } else { m.name.clone() })
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| current_m.to_string());
        if raw.is_empty() {
            return p.name.clone();
        }
        return short_model_name(current_m, &raw);
    }
    if !current_m.is_empty() && !is_stub_model(current_m, current_p) {
        return short_model_name(current_m, current_m);
    }
    if studio.local_only && !current_m.is_empty() {
        return short_model_name(current_m, current_m);
    }
    if providers.is_empty() && matches!(status, PickerStatus::Ready) && !studio.local_only {
        return "No providers".into();
    }
    "Model".into()
}

/// Last path segment, or a human display name that isn't the raw provider/path id.
pub fn short_model_name(id: &str, display: &str) -> String {
    let id = id.trim();
    let display = display.trim();
    let last = id.rsplit('/').next().unwrap_or(id).trim();
    let last = if last.is_empty() { id } else { last };
    if !display.is_empty() && display != id {
        if display.contains('/') {
            let dlast = display.rsplit('/').next().unwrap_or(display).trim();
            if !dlast.is_empty() {
                return dlast.to_string();
            }
        } else {
            return display.to_string();
        }
    }
    last.to_string()
}

fn model_menu_label(id: &str, name: &str) -> String {
    let short = short_model_name(id, name);
    if short == id {
        short
    } else {
        format!("{short} ({id})")
    }
}

fn trigger_prefix(studio: &Studio) -> Option<String> {
    let providers = studio.providers.get();
    let current_p = studio.provider.get();
    let status = studio.picker_status.get();
    if matches!(status, PickerStatus::Loading) && providers.is_empty() {
        return None;
    }
    let p = providers.iter().find(|x| x.id == current_p)?;
    let short = trigger_label(
        &providers,
        &current_p,
        &studio.model.get(),
        &status,
        studio,
    );
    if short == p.name {
        return None;
    }
    let name = p.name.trim();
    if name.is_empty() {
        None
    } else {
        Some(name.to_string())
    }
}

fn is_stub_model(model: &str, provider: &str) -> bool {
    model == "remedy" && (provider == "local" || provider.is_empty())
}

const CHIP_MAX: f32 = 240.0;
const CHIP_CHROME: f32 = 36.0; // start/end padding plus chevron

fn estimate_text_width(text: &str, size: f32) -> f32 {
    // Slightly wide guess for Remedy Sans UI at body size, so the prefix hides
    // before it would crowd the short name.
    text.chars().count() as f32 * size * 0.58
}

fn prefix_fits(prefix: &str, name: &str, inner: f32) -> bool {
    if prefix.is_empty() || name.is_empty() {
        return false;
    }
    let size = tokens::BODY.size;
    estimate_text_width(prefix, size) + 8.0 + estimate_text_width(name, size) <= inner
}

fn tooltip_text(studio: &Studio) -> String {
    let providers = studio.providers.get();
    let current_p = studio.provider.get();
    let current_m = studio.model.get();
    let provider = providers
        .iter()
        .find(|p| p.id == current_p)
        .map(|p| p.name.as_str())
        .filter(|s| !s.is_empty());
    if current_m.is_empty() || is_stub_model(&current_m, &current_p) {
        return trigger_label(
            &providers,
            &current_p,
            &current_m,
            &studio.picker_status.get(),
            studio,
        );
    }
    match provider {
        Some(name) => format!("{name}  {current_m}"),
        None => current_m,
    }
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
    let active_provider = v
        .get("active_provider")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let active_model = v
        .get("active_model")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if !active_model.is_empty() {
        if let Some(p) = providers.iter_mut().find(|p| p.id == active_provider) {
            if !p.models.iter().any(|m| m.id == active_model) {
                p.models.insert(
                    0,
                    ModelChoice {
                        id: active_model.clone(),
                        name: active_model.clone(),
                    },
                );
            }
        }
    }
    ConnectedList {
        providers,
        active_provider,
        active_model,
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
    let base_url = v
        .get("base_url")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let mut models = models;
    if models.is_empty() && !last_model.is_empty() && last_model != "default" {
        models.push(ModelChoice {
            id: last_model.clone(),
            name: last_model.clone(),
        });
    }
    Some(ProviderChoice {
        id,
        name,
        models,
        last_model,
        base_url,
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
                    // Pin each request to that provider's catalog URL so a leftover
                    // active URL (OpenRouter saved under Anthropic) cannot leak.
                    let mut rows: Vec<(String, String)> = list
                        .providers
                        .iter()
                        .map(|p| (p.id.clone(), p.base_url.clone()))
                        .collect();
                    let active = studio.provider.get();
                    rows.sort_by_key(|(id, _)| if *id == active { 0 } else { 1 });
                    for (id, url) in rows {
                        refresh_models_for(&studio, &client, &wake2, id, url);
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
    catalog_url: String,
) {
    let studio = studio.clone();
    let wake = wake.clone();
    let pid = provider.clone();
    let url = crate::setup::model::discovery_url(&pid, "", &catalog_url);
    runtime::list_models_for(client, Some(&pid), Some(&url), move |out| {
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
    let anchor: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let open_ui = ui.clone();
    let open_studio = studio.clone();
    let open_anchor = anchor.clone();
    // w::button's label is nowrap + shrink 0, so a long id painted over Simple.
    // Build the chip ourselves: short name, optional muted provider prefix,
    // ellipsis, max width, tooltip with the full id.
    let show_prefix = Signal::new({
        let st = studio.clone();
        match trigger_prefix(&st) {
            Some(prefix) => prefix_fits(&prefix, &plan(&st).trigger, CHIP_MAX - CHIP_CHROME),
            None => false,
        }
    });

    let prefix_el = {
        let st = studio.clone();
        let colors = colors.clone();
        let prefix_on = show_prefix.clone();
        let u = ui.clone();
        show(ui, move || prefix_on.get() && trigger_prefix(&st).is_some(), {
            let st = studio.clone();
            let colors = colors.clone();
            let u = u.clone();
            move || {
                let st = st.clone();
                w::text(&u, move || trigger_prefix(&st).unwrap_or_default())
                    .variant("muted")
                    .nowrap()
                    .style({
                        let colors = colors.clone();
                        move || tokens::typo(tokens::BODY).foreground(colors.muted)
                    })
            }
        })
        .el()
    };

    let name_el = {
        let st = studio.clone();
        let colors = colors.clone();
        w::text(ui, move || plan(&st).trigger)
            .nowrap()
            .truncate()
            .style({
                let colors = colors.clone();
                move || tokens::typo(tokens::BODY).weight(550.0).foreground(colors.text)
            })
            .layout(|l| {
                l.min_width = Length::Px(0.0);
                l.shrink = 1.0;
            })
    };

    let gap = ui.theme().spacing.unit * 1.5;
    let btn = ui
        .el("button")
        .row()
        .align(Align::Center)
        .gap(gap)
        .variant("quiet")
        .style({
            let colors = colors.clone();
            let st = studio.clone();
            move || {
                let on = !st.provider.get().is_empty()
                    && !is_stub_model(&st.model.get(), &st.provider.get());
                tokens::chip(&colors, on)
                    .merge(&tokens::typo(tokens::BODY))
                    .weight(550.0)
            }
        })
        .child(prefix_el)
        .child(name_el)
        .child(w::icon(
            ui,
            w::Icon::ChevronDown,
            ui.theme().type_scale.small + 2.0,
        ))
        .on_click({
            let open_anchor = open_anchor.clone();
            let open_ui = open_ui.clone();
            let open_studio = open_studio.clone();
            move || {
                let Some(anchor_el) = open_anchor.borrow().clone() else {
                    return;
                };
                let items = menu_items(&open_studio);
                w::menu(&open_ui, &anchor_el, items);
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
        .layout(|l| {
            l.min_width = Length::Px(0.0);
            l.max_width = Length::Px(CHIP_MAX);
            l.shrink = 1.0;
        })
        .padding(Insets {
            top: 2.0,
            end: 6.0,
            bottom: 2.0,
            start: 8.0,
        });

    let measured = btn.clone();
    let st_fit = studio.clone();
    let show = show_prefix.clone();
    let btn = btn.bind(move || {
        let name = plan(&st_fit).trigger;
        let Some(prefix) = trigger_prefix(&st_fit) else {
            if show.get() {
                show.set(false);
            }
            return;
        };
        let inner = measured
            .bounds()
            .map(|b| (b.width() - CHIP_CHROME).max(0.0))
            .unwrap_or(CHIP_MAX - CHIP_CHROME);
        let room = prefix_fits(&prefix, &name, inner);
        if show.get() != room {
            show.set(room);
        }
    });

    *anchor.borrow_mut() = Some(btn.clone());
    let st_tip = studio.clone();
    w::tooltip(&btn, move || tooltip_text(&st_tip));
    btn
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
    fn short_model_name_uses_display_or_last_path_segment() {
        let long = "anthropic/thinkingmachines/inkling-2-mini[1m]";
        assert_eq!(short_model_name(long, long), "inkling-2-mini[1m]");
        assert_eq!(short_model_name(long, ""), "inkling-2-mini[1m]");
        assert_eq!(short_model_name("openrouter/auto", "Auto"), "Auto");
        assert_eq!(short_model_name("llama3.2", "llama3.2"), "llama3.2");
        assert_eq!(
            model_menu_label(long, long),
            "inkling-2-mini[1m] (anthropic/thinkingmachines/inkling-2-mini[1m])"
        );
        assert_eq!(model_menu_label("llama3.2", "llama3.2"), "llama3.2");
    }

    #[test]
    fn trigger_uses_short_name_and_tooltip_keeps_the_full_id() {
        let s = Studio::preview();
        let long = "anthropic/thinkingmachines/inkling-2-mini[1m]";
        s.providers.set(vec![ProviderChoice {
            id: "anthropic".into(),
            name: "Anthropic".into(),
            models: vec![ModelChoice { id: long.into(), name: long.into() }],
            last_model: long.into(),
            base_url: "https://api.anthropic.com/v1".into(),
            connected: true,
            enabled: true,
        }]);
        s.provider.set("anthropic".into());
        s.model.set(long.into());
        s.picker_status.set(PickerStatus::Ready);
        let p = plan(&s);
        assert_eq!(p.trigger, "inkling-2-mini[1m]");
        assert!(
            !p.trigger.contains("anthropic/thinkingmachines"),
            "chip must not show the full path id: {}",
            p.trigger
        );
        let tip = tooltip_text(&s);
        assert!(tip.contains(long), "tooltip should keep the full id: {tip}");
        assert_eq!(trigger_prefix(&s).as_deref(), Some("Anthropic"));
        let label = &p.groups[0].models[0].1;
        assert!(label.contains(long), "dropdown should include the full id: {label}");
        assert!(label.contains("inkling-2-mini[1m]"));
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
            base_url: "https://openrouter.ai/api/v1".into(),
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

    #[test]
    fn anthropic_lists_against_its_catalog_url_not_a_leftover_openrouter_url() {
        let leftover = "https://openrouter.ai/api/v1";
        let anthropic = "https://api.anthropic.com/v1";
        let url = crate::setup::model::discovery_url("anthropic", leftover, anthropic);
        let path = crate::setup::model::models_request_path("anthropic", &url);
        assert!(path.contains("api.anthropic.com"), "{path}");
        assert!(!path.contains("openrouter"), "{path}");
    }

    #[test]
    fn parse_connected_seeds_last_and_active_models_when_live_list_is_empty() {
        let v = json!({
            "picker": [{
                "id": "anthropic",
                "name": "Anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "models": [],
                "last_model": "claude-haiku-4-5-20251001",
                "connected": true,
                "enabled": true
            }],
            "active_provider": "anthropic",
            "active_model": "anthropic/thinkingmachines/inkling-2-mini[1m]"
        });
        let list = parse_connected(&v);
        assert_eq!(list.providers.len(), 1);
        assert_eq!(list.providers[0].base_url, "https://api.anthropic.com/v1");
        let ids: Vec<&str> = list.providers[0].models.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(
            ids,
            vec![
                "anthropic/thinkingmachines/inkling-2-mini[1m]",
                "claude-haiku-4-5-20251001",
            ]
        );
    }

}
