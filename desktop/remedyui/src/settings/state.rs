//! Settings state. Signals live on the UI thread. HTTP callbacks only touch
//! them after a waker hop. The API-key field is not `Send`, so it stays in a
//! thread-local and is read on Save.

use std::cell::RefCell;

use remedy_ui::reactive::Signal;
use remedy_ui::view::TextField;
use serde_json::{Value, json};

use crate::runtime::{self, Client};

use super::http;
use super::logic::{self, SaveDraft};
use super::prefs;

#[derive(Clone, Debug)]
pub struct Prov {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub default_model: String,
    pub show_base_url: bool,
    pub auth: Vec<String>,
    pub oauth: bool,
}

impl Prov {
    pub fn new(
        id: impl Into<String>,
        name: impl Into<String>,
        base_url: impl Into<String>,
        default_model: impl Into<String>,
    ) -> Self {
        let id = id.into();
        let auth: Vec<String> = crate::setup::model::provider_auth(&id)
            .iter()
            .map(|mode| (*mode).to_string())
            .collect();
        let oauth = auth.iter().any(|mode| mode == "oauth");
        let show_base_url = crate::setup::model::shows_base_url(&id);
        Self {
            id,
            name: name.into(),
            base_url: base_url.into(),
            default_model: default_model.into(),
            show_base_url,
            auth,
            oauth,
        }
    }

    pub fn from_row(row: &Value) -> Option<Self> {
        let id = row.get("id").and_then(|v| v.as_str()).unwrap_or("").trim();
        if id.is_empty() {
            return None;
        }
        let mut prov = Self::new(
            id,
            jstr(row, "name", id),
            jstr(row, "base_url", ""),
            jstr(row, "default_model", ""),
        );
        if let Some(arr) = row.get("auth").and_then(|v| v.as_array()) {
            let parsed: Vec<String> = arr
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect();
            if !parsed.is_empty() {
                prov.auth = parsed;
            }
        }
        prov.oauth = row
            .get("oauth")
            .and_then(|v| v.as_bool())
            .unwrap_or_else(|| prov.auth.iter().any(|mode| mode == "oauth"));
        if let Some(flag) = row.get("show_base_url").and_then(|v| v.as_bool()) {
            prov.show_base_url = flag || crate::setup::model::shows_base_url(&prov.id);
        }
        Some(prov)
    }

    pub fn has_oauth(&self) -> bool {
        self.oauth || self.auth.iter().any(|mode| mode == "oauth")
    }
}

/// One Settings surface. Copy so widgets and callbacks share it.
#[derive(Clone, Copy)]
pub struct Settings {
    pub preview: bool,
    pub open: Signal<bool>,
    pub phase: Signal<String>,
    pub error: Signal<String>,
    pub banner: Signal<String>,
    pub saving: Signal<bool>,
    pub mode: Signal<String>,
    pub category: Signal<String>,
    pub search: Signal<String>,
    pub open_ids: Signal<Vec<String>>,
    pub force: Signal<Option<String>>,
    pub last_section: Signal<String>,
    pub client: Signal<Option<Client>>,
    pub loaded_once: Signal<bool>,

    pub user_name: Signal<String>,
    pub agent_name: Signal<String>,
    pub agent_gender: Signal<String>,
    pub ui_language: Signal<String>,
    pub ui_languages: Signal<Vec<(String, String)>>,
    pub persona: Signal<String>,
    pub soul_field: Signal<bool>,

    pub provider: Signal<String>,
    pub model: Signal<String>,
    pub base_url: Signal<String>,
    pub custom_name: Signal<String>,
    pub api_key_set: Signal<bool>,
    pub loaded_provider: Signal<String>,
    pub loaded_model: Signal<String>,
    pub loaded_base_url: Signal<String>,
    pub loaded_project: Signal<String>,
    pub catalog: Signal<Vec<Prov>>,
    pub models: Signal<Vec<String>>,
    pub discovery: Signal<String>,
    pub providers_touched: Signal<bool>,
    pub enabled_providers: Signal<Vec<String>>,
    pub enabled_models: Signal<Value>,
    pub skills_budget: Signal<f64>,
    pub xai: Signal<Value>,

    pub project_path: Signal<String>,
    pub browser_home: Signal<String>,
    pub privacy_shield: Signal<bool>,

    pub access_scope: Signal<String>,
    pub approval_mode: Signal<String>,
    pub trust_profile: Signal<String>,
    pub thinking_level: Signal<String>,
    pub web_tools: Signal<bool>,
    pub http_bootstrap: Signal<bool>,
    pub tool_process: Signal<String>,
    pub privacy_mode: Signal<bool>,

    pub sleev_enabled: Signal<bool>,
    pub sleev_url: Signal<String>,
    pub sleev_allow_remote: Signal<bool>,

    pub harness_mode: Signal<String>,
    pub harness_min: Signal<f64>,
    pub harness_max: Signal<f64>,

    pub allow_skills: Signal<bool>,
    pub auto_approve: Signal<f64>,
    pub log_level: Signal<String>,
    pub sarcasm: Signal<bool>,
    pub claimidx: Signal<bool>,
    pub advanced: Signal<Value>,
    pub advanced_schema: Signal<Value>,

    pub launch_at_login: Signal<bool>,
    pub start_in_tray: Signal<bool>,
    pub skip_quit_warn: Signal<bool>,

    pub vision_enabled: Signal<bool>,
    pub vision_force: Signal<bool>,
    pub vision: Signal<Value>,
    pub rmb: Signal<Value>,
    pub voice: Signal<Value>,
    pub phone: Signal<Value>,
    pub connect: Signal<Value>,
    pub tunnel: Signal<Value>,
    pub denied: Signal<Value>,
    pub pair_qr: Signal<String>,

    pub messengers: Signal<Value>,
    pub messenger_drafts: Signal<Value>,
    pub messengers_dirty: Signal<bool>,
    pub assistant: Signal<Value>,
    pub assistant_draft: Signal<Value>,

    pub gen_scope: Signal<String>,
    pub gen_effort: Signal<String>,
    pub gen_think: Signal<String>,
    pub gen_out: Signal<String>,
    pub gen_ctx: Signal<String>,
    pub model_settings: Signal<Value>,

    pub theme_id: Signal<String>,
    pub density: Signal<String>,
    pub font_scale: Signal<String>,
    pub reduce_motion: Signal<bool>,
    pub show_presence: Signal<bool>,
    pub high_contrast: Signal<bool>,
    pub custom_accent: Signal<String>,
    pub font_ui: Signal<String>,
    pub font_heading: Signal<String>,
    pub font_reading: Signal<String>,
    pub font_accent: Signal<String>,
    pub font_code: Signal<String>,
    pub system_dark: Signal<Option<bool>>,
    pub system_hc: Signal<bool>,

    pub license_all: Signal<bool>,
    pub notices_all: Signal<bool>,
    pub version: Signal<String>,
    pub wipe_open: Signal<bool>,
    pub wipe_text: Signal<String>,
    pub voice_loaded: Signal<bool>,
    pub phone_loaded: Signal<bool>,
    pub connect_loaded: Signal<bool>,
    pub rmb_loaded: Signal<bool>,
    pub vision_loaded: Signal<bool>,
    pub rmb_path: Signal<String>,
    pub hf_query: Signal<String>,
    pub hf_hits: Signal<Value>,
    pub rmb_catalog: Signal<Value>,
    pub hf_files: Signal<Value>,
    pub hf_repo: Signal<String>,
    pub hf_file: Signal<String>,
}

thread_local! {
    static API_KEY: RefCell<Option<TextField>> = RefCell::new(None);
    static SEARCH: RefCell<Option<TextField>> = RefCell::new(None);
}

impl Settings {
    fn blank(preview: bool) -> Self {
        Settings {
            preview,
            open: Signal::new(false),
            phase: Signal::new("loading".into()),
            error: Signal::new(String::new()),
            banner: Signal::new(String::new()),
            saving: Signal::new(false),
            mode: Signal::new("simple".into()),
            category: Signal::new("general".into()),
            search: Signal::new(String::new()),
            open_ids: Signal::new(Vec::new()),
            force: Signal::new(None),
            last_section: Signal::new("you-agent".into()),
            client: Signal::new(None),
            loaded_once: Signal::new(false),
            user_name: Signal::new(String::new()),
            agent_name: Signal::new("Remedy".into()),
            agent_gender: Signal::new("female".into()),
            ui_language: Signal::new("auto".into()),
            ui_languages: Signal::new(vec![("auto".into(), "Auto".into())]),
            persona: Signal::new("balanced".into()),
            soul_field: Signal::new(true),
            provider: Signal::new("demo".into()),
            model: Signal::new(String::new()),
            base_url: Signal::new(String::new()),
            custom_name: Signal::new(String::new()),
            api_key_set: Signal::new(false),
            loaded_provider: Signal::new("demo".into()),
            loaded_model: Signal::new(String::new()),
            loaded_base_url: Signal::new(String::new()),
            loaded_project: Signal::new(String::new()),
            catalog: Signal::new(Vec::new()),
            models: Signal::new(Vec::new()),
            discovery: Signal::new(String::new()),
            providers_touched: Signal::new(false),
            enabled_providers: Signal::new(Vec::new()),
            enabled_models: Signal::new(json!({})),
            skills_budget: Signal::new(80.0),
            xai: Signal::new(Value::Null),
            project_path: Signal::new(String::new()),
            browser_home: Signal::new(String::new()),
            privacy_shield: Signal::new(false),
            access_scope: Signal::new("full".into()),
            approval_mode: Signal::new("ask".into()),
            trust_profile: Signal::new("balanced".into()),
            thinking_level: Signal::new("medium".into()),
            web_tools: Signal::new(false),
            http_bootstrap: Signal::new(true),
            tool_process: Signal::new("off".into()),
            privacy_mode: Signal::new(false),
            sleev_enabled: Signal::new(false),
            sleev_url: Signal::new(String::new()),
            sleev_allow_remote: Signal::new(false),
            harness_mode: Signal::new("auto".into()),
            harness_min: Signal::new(0.75),
            harness_max: Signal::new(0.92),
            allow_skills: Signal::new(true),
            auto_approve: Signal::new(0.8),
            log_level: Signal::new("INFO".into()),
            sarcasm: Signal::new(false),
            claimidx: Signal::new(true),
            advanced: Signal::new(json!({})),
            advanced_schema: Signal::new(json!([])),
            launch_at_login: Signal::new(false),
            start_in_tray: Signal::new(false),
            skip_quit_warn: Signal::new(false),
            vision_enabled: Signal::new(false),
            vision_force: Signal::new(false),
            vision: Signal::new(Value::Null),
            rmb: Signal::new(Value::Null),
            voice: Signal::new(Value::Null),
            phone: Signal::new(Value::Null),
            connect: Signal::new(Value::Null),
            tunnel: Signal::new(Value::Null),
            denied: Signal::new(json!([])),
            pair_qr: Signal::new(String::new()),
            messengers: Signal::new(json!([])),
            messenger_drafts: Signal::new(json!({})),
            messengers_dirty: Signal::new(false),
            assistant: Signal::new(Value::Null),
            assistant_draft: Signal::new(json!({})),
            gen_scope: Signal::new("all".into()),
            gen_effort: Signal::new(String::new()),
            gen_think: Signal::new(String::new()),
            gen_out: Signal::new(String::new()),
            gen_ctx: Signal::new(String::new()),
            model_settings: Signal::new(json!({})),
            theme_id: Signal::new("forest".into()),
            density: Signal::new("cozy".into()),
            font_scale: Signal::new("md".into()),
            reduce_motion: Signal::new(false),
            show_presence: Signal::new(true),
            high_contrast: Signal::new(false),
            custom_accent: Signal::new(String::new()),
            font_ui: Signal::new("remedy-sans-ui".into()),
            font_heading: Signal::new("ahmi-pixel".into()),
            font_reading: Signal::new("ahmi-print".into()),
            font_accent: Signal::new("ahmi-pixel".into()),
            font_code: Signal::new("ahmi-print".into()),
            system_dark: Signal::new(None),
            system_hc: Signal::new(false),
            license_all: Signal::new(false),
            notices_all: Signal::new(false),
            version: Signal::new(env!("CARGO_PKG_VERSION").into()),
            wipe_open: Signal::new(false),
            wipe_text: Signal::new(String::new()),
            voice_loaded: Signal::new(false),
            phone_loaded: Signal::new(false),
            connect_loaded: Signal::new(false),
            rmb_loaded: Signal::new(false),
            vision_loaded: Signal::new(false),
            rmb_path: Signal::new(String::new()),
            hf_query: Signal::new(String::new()),
            hf_hits: Signal::new(json!([])),
            rmb_catalog: Signal::new(Value::Null),
            hf_files: Signal::new(json!([])),
            hf_repo: Signal::new(String::new()),
            hf_file: Signal::new(String::new()),
        }
    }

    /// Live settings. Call on the UI thread inside the window.
    pub fn live() -> Self {
        let s = Self::blank(false);
        if !runtime::spawn_disabled() {
            let client = Client::new(&runtime::origin(), runtime::load_token().as_deref());
            s.client.set(Some(client));
        }
        s
    }

    /// Fixture for previews and goldens. Does not read or write config.
    pub fn sample() -> Self {
        let s = Self::blank(true);
        s.open.set(true);
        s.phase.set("ready".into());
        s.user_name.set("Ada".into());
        s.agent_name.set("Remedy".into());
        s.persona.set("balanced".into());
        s.provider.set("xai".into());
        s.loaded_provider.set("xai".into());
        s.model.set("grok-4".into());
        s.loaded_model.set("grok-4".into());
        s.base_url.set("https://api.x.ai/v1".into());
        s.loaded_base_url.set("https://api.x.ai/v1".into());
        s.api_key_set.set(true);
        s.project_path.set("C:/Users/Ada/Projects/Remedy".into());
        s.loaded_project.set("C:/Users/Ada/Projects/Remedy".into());
        s.browser_home.set(logic::DEFAULT_BROWSER_HOME.into());
        s.access_scope.set("project".into());
        s.catalog.set(vec![
            Prov::new("demo", "Demo", "", "demo"),
            Prov::new("xai", "xAI (Grok)", "https://api.x.ai/v1", "grok-4"),
            Prov::new("ollama", "Ollama", "http://127.0.0.1:11434", "llama3.1"),
            Prov::new("custom", "Custom endpoint", "", ""),
        ]);
        s.models.set(vec!["grok-4".into(), "grok-4-fast".into()]);
        s.enabled_providers.set(vec!["demo".into(), "xai".into(), "ollama".into(), "custom".into()]);
        s.xai.set(json!({"connected": true, "auth_method": "api_key", "has_api_key": true}));
        s.messengers.set(json!([
            {"id": "telegram", "name": "Telegram", "enabled": false, "status": "needs_setup", "description": "A bot token from BotFather.", "fields": {"token": null, "allowed": ""}, "field_schema": [{"key": "token", "label": "Bot token", "kind": "secret"}, {"key": "allowed", "label": "Allowed chats", "kind": "list"}]}
        ]));
        s.messenger_drafts.set(logic::drafts_from_messengers(&s.messengers.get()));
        s.assistant.set(json!({
            "enabled": false,
            "privacy": {
                "privacy_ai_short": "Calendar and mail text can be sent to your model when you ask.",
                "privacy_ai_checkbox": "I understand Remedy may send account text to my model.",
                "account_connect_checkbox": "I want Remedy to connect this account on this computer."
            },
            "providers_planned": [{"id": "google", "name": "Google", "status": "ready"}]
        }));
        s.voice.set(json!({"settings": {"speak_replies": true, "stt_enabled": true, "stt_model": "small"}, "tts": {"installed": true, "enabled": true}, "stt": {"installed": true, "enabled": true, "models": ["small", "medium"]}, "turn": {"available": false}}));
        s.phone.set(json!({"message": "No phone line chosen yet.", "terms": {"agreed": false, "stale": false, "ask": "Read the terms, then agree on this computer."}, "lines": [{"name": "Local softphone", "ready": false, "achievable": true, "missing": "Agree to the terms first."}]}));
        s.connect.set(json!({"enabled": false, "paused": false, "bind_host": "192.168.1.20", "bind_port": 7400, "addresses": ["192.168.1.20", "127.0.0.1"], "devices": [], "panes": {"live_ui": true, "chat": true, "approvals": true, "sessions": true, "rails": true, "computer_preview": false, "settings_write": false}, "mobile": {"enabled": false, "punch": false, "port": 4433, "relay_mode": "off", "status": {"verdict": "off", "message": "Off. A phone on this Wi-Fi can still connect.", "steps": []}}}));
        s.rmb.set(json!({"running": false, "model": "", "profile": "autofit", "ctx_size": 8192, "discovered_ggufs": ["C:/models/remedy.gguf"], "engine": {"enable_mtp": true, "thinking": "on"}}));
        s.vision.set(json!({"model_name": "SmolVLM2 2.2B", "installed": true, "running": false, "ready": true, "runtime_version": "llama.cpp", "device": "CPU", "enabled": false}));
        s.vision_enabled.set(false);
        s.advanced_schema.set(json!([{"key": "example_knob", "name": "Example knob", "kind": "bool", "default": false, "help": "Organism knobs from the server appear here."}]));
        s.version.set("1.0.0".into());
        s.open_ids.set(vec!["you-agent".into()]);
        s
    }

    pub fn error_sample() -> Self {
        let s = Self::blank(true);
        s.open.set(true);
        s.phase.set("error".into());
        s.error.set("Settings couldn’t load".into());
        s
    }

    pub fn ensure_loaded(self) {
        if self.preview || self.loaded_once.get() {
            return;
        }
        if self.client.get().is_none() {
            // Headless preview tests set REMEDY_DESKTOP_NO_SPAWN and have no
            // runtime. Stay on the loading placeholder so an extra frame
            // (leftover waker work from an earlier test in the same process)
            // cannot flip rails-right goldens to "Settings couldn't load".
            if crate::runtime::spawn_disabled() {
                return;
            }
            self.phase.set("error".into());
            self.error.set("Settings couldn’t load".into());
            return;
        }
        self.loaded_once.set(true);
        if self.phase.get() != "ready" {
            self.phase.set("loading".into());
        }
        http::get_settings(self, move |out| match out {
            Ok(v) => {
                self.apply_doc(&v);
                self.phase.set("ready".into());
                self.load_catalog();
            }
            Err(e) => {
                self.loaded_once.set(false);
                self.phase.set("error".into());
                self.error.set(if e.is_empty() { "Settings couldn’t load".into() } else { e });
            }
        });
    }

    pub fn retry(self) {
        self.loaded_once.set(false);
        self.ensure_loaded();
    }

    pub fn apply_doc(self, v: &Value) {
        self.user_name.set(jstr(v, "user_name", ""));
        self.agent_name.set(jstr(v, "name", "Remedy"));
        self.agent_gender.set(jstr(v, "agent_gender", "female"));
        self.ui_language.set_if_changed(jstr(v, "ui_language", "auto"));
        if let Some(langs) = v.get("ui_languages").and_then(|x| x.as_array()) {
            let mut rows = vec![("auto".into(), "Auto".into())];
            for row in langs {
                let id = jstr(row, "id", "");
                if id.is_empty() || id == "auto" {
                    continue;
                }
                let label = jstr(row, "name_native", &jstr(row, "name_en", &id));
                rows.push((id, label));
            }
            self.ui_languages.set(rows);
        }
        self.persona.set(jstr(v, "persona", "balanced"));
        self.soul_field.set(jbool(v, "soul_field_enabled", true));
        let provider = jstr(v, "llm_provider", "demo");
        let model = jstr(v, "llm_model", "");
        let base = jstr(v, "llm_base_url", "");
        self.provider.set(provider.clone());
        self.model.set(model.clone());
        self.base_url.set(base.clone());
        self.loaded_provider.set(provider);
        self.loaded_model.set(model);
        self.loaded_base_url.set(base);
        self.custom_name.set(jstr(v, "custom_llm_name", ""));
        self.api_key_set.set(jbool(v, "llm_api_key_set", false));
        let project = jstr(v, "project_path", "");
        self.project_path.set(project.clone());
        self.loaded_project.set(project);
        self.browser_home.set(jstr(v, "browser_home_url", ""));
        let scope = jstr(v, "access_scope", "project");
        self.access_scope.set(if logic::project_forces_full(&self.project_path.get()) { "full".into() } else { scope });
        self.launch_at_login.set(jbool(v, "launch_at_login", false));
        self.start_in_tray.set(jbool(v, "start_in_tray", false));
        self.harness_mode.set(jstr(v, "harness_mode", "auto"));
        self.harness_min.set(jnum(v, "harness_min_context_pct", 0.75));
        self.harness_max.set(jnum(v, "harness_max_context_pct", 0.92));
        self.thinking_level.set(jstr(v, "thinking_level", "medium"));
        self.tool_process.set(jstr(v, "tool_process", "off"));
        self.skills_budget.set(jnum(v, "skills_active_budget", 80.0));
        self.web_tools.set(jbool(v, "web_tools_enabled", false));
        self.http_bootstrap.set(jbool(v, "http_bootstrap", true));
        self.privacy_mode.set(jbool(v, "privacy_mode", false));
        self.sleev_enabled.set(jbool(v, "sleev_enabled", false));
        self.sleev_url.set(jstr(v, "sleev_gateway_url", ""));
        self.sleev_allow_remote.set(jbool(v, "sleev_allow_remote_gateway", false));
        self.approval_mode.set(jstr(v, "approval_mode", "ask"));
        self.trust_profile.set(jstr(v, "trust_profile", "balanced"));
        self.allow_skills.set(jbool(v, "allow_skill_creation", true));
        self.auto_approve.set(jnum(v, "auto_approve_threshold", 0.8));
        self.log_level.set(jstr(v, "log_level", "INFO"));
        self.sarcasm.set(jbool(v, "sarcasm_mode", false));
        self.claimidx.set(jbool(v, "claimidx_public_ledger", true));
        self.advanced.set(v.get("advanced").cloned().unwrap_or_else(|| json!({})));
        self.advanced_schema.set(v.get("advanced_schema").cloned().unwrap_or_else(|| json!([])));
        self.vision_enabled.set_if_changed(jbool(v, "vision_enabled", false));
        self.vision_force.set_if_changed(jbool(v, "vision_force_decode", false));
        if let Some(vis) = v.get("vision") {
            self.vision.set(vis.clone());
        }
        if let Some(list) = v.get("enabled_providers").and_then(|x| x.as_array()) {
            self.enabled_providers.set(list.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect());
        }
        self.enabled_models.set(v.get("enabled_models").cloned().unwrap_or_else(|| json!({})));
        self.providers_touched.set(false);
        let messengers = v.get("messengers").cloned().unwrap_or_else(|| json!([]));
        self.messengers.set(messengers.clone());
        self.messenger_drafts.set(logic::drafts_from_messengers(&messengers));
        self.messengers_dirty.set(false);
        if let Some(a) = v.get("assistant") {
            self.assistant.set(a.clone());
        }
        self.assistant_draft.set(json!({}));
        self.gen_effort.set(logic::form_effort(&jstr(v, "reasoning_effort", "")));
        self.gen_think.set(logic::positive_token_text(v.get("thinking_budget_tokens").and_then(|n| n.as_i64())));
        self.gen_out.set(logic::positive_token_text(v.get("max_output_tokens").and_then(|n| n.as_i64())));
        self.gen_ctx.set(logic::positive_token_text(v.get("context_window_tokens").and_then(|n| n.as_i64())));
        self.model_settings.set(v.get("model_settings").cloned().unwrap_or_else(|| json!({})));
        if let Some(x) = v.get("xai_auth") {
            self.xai.set(x.clone());
        }
        let ver = jstr(v, "version", "");
        if !ver.is_empty() {
            self.version.set(ver);
        }
    }

    pub fn load_catalog(self) {
        http::request(self, "GET", "providers".into(), None, move |out| {
            if let Ok(v) = out {
                let rows = v.get("providers").and_then(|x| x.as_array()).or_else(|| v.as_array());
                if let Some(rows) = rows {
                    let provs = rows.iter().filter_map(Prov::from_row).collect::<Vec<_>>();
                    if self.enabled_providers.get().is_empty() {
                        self.enabled_providers.set(provs.iter().map(|p| p.id.clone()).collect());
                    }
                    self.catalog.set(provs);
                }
            }
        });
    }

    pub fn discover_models(self) {
        if self.preview {
            return;
        }
        let pid = self.provider.get();
        if pid.is_empty() {
            return;
        }
        let form_url = self.base_url.get();
        let catalog_url = self
            .catalog
            .get()
            .iter()
            .find(|p| p.id == pid)
            .map(|p| p.base_url.clone())
            .unwrap_or_default();
        let url = crate::setup::model::discovery_url(&pid, &form_url, &catalog_url);
        let q = crate::setup::model::models_request_path(&pid, &url);
        http::request(self, "GET", q, None, move |out| {
            match out {
                Ok(v) => {
                    let mut names: Vec<String> = crate::model_picker::parse_model_rows(Some(&v))
                        .into_iter()
                        .map(|m| m.id)
                        .collect();
                    let current = self.model.get();
                    if !current.is_empty() && !names.iter().any(|m| m == &current) {
                        names.insert(0, current);
                    }
                    self.models.set(names);
                    let hint = v.get("discovery").and_then(|d| d.get("message").or_else(|| d.get("error"))).and_then(|x| x.as_str()).unwrap_or("").to_string();
                    self.discovery.set(hint);
                }
                Err(e) => self.discovery.set(e),
            }
        });
    }

    pub fn load_voice(self) {
        if self.preview || self.voice_loaded.get() {
            return;
        }
        self.voice_loaded.set(true);
        http::request(self, "GET", "voice/status".into(), None, move |out| {
            match out {
                Ok(v) => self.voice.set(v),
                Err(e) => self.banner.set(e),
            }
        });
    }

    pub fn load_phone(self) {
        if self.preview || self.phone_loaded.get() {
            return;
        }
        self.phone_loaded.set(true);
        http::request(self, "GET", "telephony/status".into(), None, move |out| {
            match out {
                Ok(v) => self.phone.set(v),
                Err(e) => self.banner.set(e),
            }
        });
    }

    pub fn load_connect(self) {
        if self.preview || self.connect_loaded.get() {
            return;
        }
        self.connect_loaded.set(true);
        http::request(self, "GET", "connect".into(), None, move |out| {
            if let Ok(v) = out {
                self.connect.set(v);
            }
        });
        http::request(self, "GET", "messengers/tunnel".into(), None, move |out| {
            if let Ok(v) = out {
                self.tunnel.set(v);
            }
        });
        http::request(self, "GET", "status".into(), None, move |out| {
            if let Ok(v) = out {
                if let Some(d) = v.get("gateway").and_then(|g| g.get("denied_inbound")) {
                    self.denied.set(d.clone());
                }
            }
        });
    }

    pub fn load_rmb(self) {
        if self.preview || self.rmb_loaded.get() {
            return;
        }
        self.rmb_loaded.set(true);
        http::request(self, "GET", "rmb/status".into(), None, move |out| {
            if let Ok(v) = out {
                self.rmb.set(v);
            }
        });
        http::request(self, "GET", "rmb/catalog".into(), None, move |out| {
            if let Ok(v) = out {
                self.rmb_catalog.set(v);
            }
        });
    }

    pub fn refresh_rmb(self) {
        self.rmb_loaded.set(false);
        self.load_rmb();
    }

    pub fn load_vision(self) {
        if self.preview || self.vision_loaded.get() {
            return;
        }
        self.vision_loaded.set(true);
        http::request(self, "GET", "vision/status".into(), None, move |out| {
            if let Ok(v) = out {
                self.vision.set(v);
            }
        });
    }

    pub fn reload_section(self, id: &str) {
        match id {
            "voice" => {
                self.voice_loaded.set(false);
                self.load_voice();
            }
            "phone" => {
                self.phone_loaded.set(false);
                self.load_phone();
            }
            "connect" | "channels" => {
                self.connect_loaded.set(false);
                self.load_connect();
            }
            "rmb" => {
                self.rmb_loaded.set(false);
                self.load_rmb();
            }
            "vision" => {
                self.vision_loaded.set(false);
                self.load_vision();
            }
            _ => {}
        }
    }

    pub fn note_opened(self, id: &str) {
        self.force.set(Some(id.to_string()));
        self.last_section.set(id.to_string());
        if !self.preview {
            prefs::persist(self);
        }
        let mut open = self.open_ids.get();
        if !open.iter().any(|x| x == id) {
            open.push(id.to_string());
            self.open_ids.set(open);
        }
    }

    pub fn note_closed(self, id: &str) {
        if self.force.get().as_deref() == Some(id) {
            self.force.set(None);
        }
        let open: Vec<String> = self.open_ids.get().into_iter().filter(|x| x != id).collect();
        self.open_ids.set(open);
    }

    pub fn is_open(self, id: &str) -> bool {
        self.open_ids.get().iter().any(|x| x == id)
    }

    pub fn report(self, out: Result<Value, String>, ok: &str) {
        self.banner.set(match out {
            Ok(_) => ok.to_string(),
            Err(e) if e.is_empty() => "Request failed.".into(),
            Err(e) => e,
        });
    }

    pub fn put_patch(self, body: Value) {
        self.saving.set(true);
        http::put_settings(self, body, move |out| {
            self.saving.set(false);
            match out {
                Ok(_) => self.banner.set("Saved. Applies from the next message.".into()),
                Err(e) => self.banner.set(if e.is_empty() { "Could not save.".into() } else { e }),
            }
        });
    }

    pub fn save(self) {
        if self.saving.get() || self.phase.get() != "ready" {
            return;
        }
        let mut draft = self.gather();
        draft.api_key = take_api_key();
        let body = match logic::save_body(&draft) {
            Ok(v) => v,
            Err(e) => {
                self.banner.set(e);
                return;
            }
        };
        self.saving.set(true);
        self.banner.set(String::new());
        sync_login(self.launch_at_login.get());
        if !self.preview {
            prefs::persist(self);
        }
        let prev_project = self.loaded_project.get();
        let next_project = self.project_path.get();
        let sent_key = !draft.api_key.is_empty();
        http::put_settings(self, body, move |out| {
            self.saving.set(false);
            match out {
                Ok(v) => {
                    if let Some(p) = v.get("llm_provider").and_then(|x| x.as_str()) {
                        self.provider.set(p.to_string());
                        self.loaded_provider.set(p.to_string());
                    }
                    if let Some(p) = v.get("llm_model").and_then(|x| x.as_str()) {
                        self.model.set(p.to_string());
                        self.loaded_model.set(p.to_string());
                    }
                    if let Some(p) = v.get("llm_base_url").and_then(|x| x.as_str()) {
                        self.base_url.set(p.to_string());
                        self.loaded_base_url.set(p.to_string());
                    }
                    if v.get("project_path").is_some() {
                        let path = jstr(&v, "project_path", &self.project_path.get());
                        self.project_path.set(path.clone());
                        self.loaded_project.set(path);
                    }
                    if let Some(p) = v.get("persona").and_then(|x| x.as_str()) {
                        self.persona.set(p.to_string());
                    }
                    if sent_key {
                        self.api_key_set.set(true);
                    }
                    self.messengers_dirty.set(false);
                    self.assistant_draft.set(json!({}));
                    let changed = next_project.trim() != prev_project.trim() && !next_project.trim().is_empty();
                    self.banner.set(if changed {
                        "Settings saved · Workspace updated".into()
                    } else {
                        "Settings saved".into()
                    });
                    self.loaded_once.set(false);
                    self.ensure_loaded();
                }
                Err(e) => {
                    let msg = if e.is_empty() { "Failed to save settings".into() } else { e };
                    if sent_key {
                        self.banner.set(format!("{msg} Enter the API key again."));
                    } else {
                        self.banner.set(msg);
                    }
                }
            }
        });
    }

    pub fn gather(self) -> SaveDraft {
        let mut d = SaveDraft::default();
        d.provider = self.provider.get();
        d.model = self.model.get();
        d.base_url = self.base_url.get();
        d.loaded_provider = self.loaded_provider.get();
        d.loaded_model = self.loaded_model.get();
        d.loaded_base_url = self.loaded_base_url.get();
        d.custom_name = self.custom_name.get();
        d.project_path = self.project_path.get();
        d.browser_home_url = self.browser_home.get();
        d.persona = self.persona.get();
        d.user_name = self.user_name.get();
        d.name = self.agent_name.get();
        d.agent_gender = self.agent_gender.get();
        d.ui_language = self.ui_language.get();
        d.access_scope = self.access_scope.get();
        d.launch_at_login = self.launch_at_login.get();
        d.start_in_tray = self.start_in_tray.get();
        d.harness_mode = self.harness_mode.get();
        d.harness_min = self.harness_min.get();
        d.harness_max = self.harness_max.get();
        d.thinking_level = self.thinking_level.get();
        d.tool_process = self.tool_process.get();
        d.skills_budget = self.skills_budget.get();
        d.web_tools = self.web_tools.get();
        d.http_bootstrap = self.http_bootstrap.get();
        d.privacy_mode = self.privacy_mode.get();
        d.sleev_enabled = self.sleev_enabled.get();
        d.sleev_url = self.sleev_url.get();
        d.sleev_allow_remote = self.sleev_allow_remote.get();
        d.soul_field = self.soul_field.get();
        d.approval_mode = self.approval_mode.get();
        d.trust_profile = self.trust_profile.get();
        d.allow_skills = self.allow_skills.get();
        d.auto_approve = self.auto_approve.get();
        d.log_level = self.log_level.get();
        d.sarcasm = self.sarcasm.get();
        d.claimidx = self.claimidx.get();
        d.advanced = self.advanced.get();
        d.providers_touched = self.providers_touched.get();
        d.enabled_providers = self.enabled_providers.get();
        d.catalog_ids = self.catalog.get().into_iter().map(|p| p.id).collect();
        d.enabled_models = self.enabled_models.get();
        d.messengers_dirty = self.messengers_dirty.get();
        d.messengers = logic::messengers_update(&self.messengers.get(), &self.messenger_drafts.get());
        d.assistant = self.assistant_draft.get();
        d
    }
}

pub fn store_api_key(field: TextField) {
    API_KEY.with(|c| *c.borrow_mut() = Some(field));
}

pub fn store_search(field: TextField) {
    SEARCH.with(|c| *c.borrow_mut() = Some(field));
}

pub fn clear_search(s: Settings) {
    s.search.set(String::new());
    s.force.set(None);
    SEARCH.with(|c| {
        if let Some(field) = c.borrow().clone() {
            if !field.value().is_empty() {
                field.set_value("");
            }
        }
    });
}

pub(crate) fn take_api_key() -> String {
    API_KEY.with(|c| {
        let Some(field) = c.borrow().clone() else { return String::new() };
        let el = field.el();
        if !el.ui().tree().contains(el.id()) {
            return String::new();
        }
        let secret = field.take_secret();
        secret.expose().trim().to_string()
    })
}

fn sync_login(on: bool) {
    if cfg!(target_os = "linux") {
        return;
    }
    let Ok(entry) = remedy_ui::desktop::autostart::Entry::current("Remedy", "com.remedy.desktop", &[]) else {
        return;
    };
    let _ = remedy_ui::desktop::autostart::set(&entry, on);
}

pub fn jstr(v: &Value, key: &str, default: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or(default).to_string()
}

pub fn jbool(v: &Value, key: &str, default: bool) -> bool {
    v.get(key).and_then(|x| x.as_bool()).unwrap_or(default)
}

pub fn jnum(v: &Value, key: &str, default: f64) -> f64 {
    v.get(key).and_then(|x| x.as_f64()).unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn catalog_rows_keep_their_own_url_and_auth() {
        let body = json!([
            {
                "id": "anthropic",
                "name": "Anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "auth": ["api_key"],
                "show_base_url": false
            },
            {
                "id": "xai",
                "name": "xAI (Grok)",
                "base_url": "https://api.x.ai/v1",
                "auth": ["oauth", "api_key"],
                "oauth": true,
                "show_base_url": false
            },
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "auth": ["api_key"],
                "show_base_url": false
            },
            {
                "id": "custom",
                "name": "Custom / OpenAI-compatible",
                "base_url": "http://127.0.0.1:5001/v1",
                "auth": ["api_key"],
                "show_base_url": true
            }
        ]);
        let rows: Vec<Prov> = body.as_array().unwrap().iter().filter_map(Prov::from_row).collect();
        assert_eq!(rows[0].name, "Anthropic");
        assert_eq!(rows[0].base_url, "https://api.anthropic.com/v1");
        assert!(!rows[0].has_oauth());
        assert!(!rows[0].show_base_url);
        assert_eq!(rows[1].name, "xAI (Grok)");
        assert_eq!(rows[1].base_url, "https://api.x.ai/v1");
        assert!(rows[1].has_oauth());
        assert!(!rows[1].show_base_url);
        assert_eq!(rows[2].name, "OpenRouter");
        assert_eq!(rows[2].base_url, "https://openrouter.ai/api/v1");
        assert!(!rows[2].has_oauth());
        assert!(rows[3].show_base_url);
        for row in &rows {
            if row.id != "openrouter" {
                assert!(!row.base_url.contains("openrouter.ai"), "{} reused OpenRouter URL", row.id);
                assert!(!row.name.to_ascii_lowercase().contains("openrouter"), "{} reused OpenRouter label", row.id);
            }
        }
    }
}
