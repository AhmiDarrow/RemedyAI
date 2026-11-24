//! Traditional setup wizard. The API key stays in the secret field and never enters the draft.

use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::{waker, Signal};
use crate::presence::{self, Mood};
use remedy_ui::widgets as w;
use remedy_ui::{show, El, TextField, Ui};
use serde_json::{Map, Value, json};

use super::chrome;
use super::messengers::{self, Messenger};
use super::model::{self, STEP_FOLDER, STEP_PROVIDER, STEP_READY, STEP_STYLE, STEP_VISION, STEP_WELCOME};
use crate::runtime::Client;
use crate::tokens::{typo, BODY, HERO, META, SECTION, SPACE, TITLE};

#[derive(Clone)]
pub struct Wizard {
    pub step: Signal<usize>,
    pub provider: Signal<String>,
    /// Legacy copy. Live keys are read from `key_field` (`value()` is empty for secrets).
    pub api_key: Signal<String>,
    pub key_field: Rc<RefCell<Option<TextField>>>,
    pub model_name: Signal<String>,
    pub base_url: Signal<String>,
    pub project_path: Signal<String>,
    pub persona: Signal<String>,
    pub user_name: Signal<String>,
    pub agent_name: Signal<String>,
    pub agent_gender: Signal<String>,
    pub language: Signal<String>,
    pub launch: Signal<bool>,
    pub xai_connected: Signal<bool>,
    pub xai_busy: Signal<bool>,
    pub xai_code: Signal<String>,
    pub xai_url: Signal<String>,
    pub xai_msg: Signal<String>,
    pub error: Signal<String>,
    pub saving: Signal<bool>,
    pub custom_name: Signal<String>,
    pub installing: Signal<bool>,
    pub install_pct: Signal<Option<f32>>,
    pub install_msg: Signal<String>,
    pub vision_ready: Signal<bool>,
    pub messengers: Signal<Vec<Messenger>>,
    pub ollama_models: Signal<Vec<String>>,
    pub languages: Signal<Vec<String>>,
    pub paid_index: Signal<usize>,
    pub free_options: Signal<Vec<model::FreeProviderOpt>>,
    pub free_index: Signal<usize>,
    /// Recommended path: hello → folder → name → ready (still writes full defaults).
    pub short: Signal<bool>,
    /// Provider step: "On this PC" (local default) vs explicit Ollama.
    pub on_this_pc: Signal<bool>,
    pub done: Signal<bool>,
    pub talk: Signal<bool>,
    pub failure: Signal<String>,
    /// Shared with the gate so a token loaded after splash is the one we save with.
    pub client: Signal<Option<Client>>,
    booted: Signal<bool>,
}

impl Wizard {
    pub fn preview() -> Self {
        Self::new(None)
    }

    pub fn new(client: Option<Client>) -> Self {
        Self {
            step: Signal::new(STEP_WELCOME),
            provider: Signal::new("demo".into()),
            api_key: Signal::new(String::new()),
            key_field: Rc::new(RefCell::new(None)),
            model_name: Signal::new(String::new()),
            base_url: Signal::new(String::new()),
            project_path: Signal::new(String::new()),
            persona: Signal::new("balanced".into()),
            user_name: Signal::new(String::new()),
            agent_name: Signal::new("Remedy".into()),
            agent_gender: Signal::new("female".into()),
            language: Signal::new("auto".into()),
            launch: Signal::new(false),
            xai_connected: Signal::new(false),
            xai_busy: Signal::new(false),
            xai_code: Signal::new(String::new()),
            xai_url: Signal::new(String::new()),
            xai_msg: Signal::new(String::new()),
            error: Signal::new(String::new()),
            saving: Signal::new(false),
            custom_name: Signal::new(String::new()),
            installing: Signal::new(false),
            install_pct: Signal::new(None),
            install_msg: Signal::new(String::new()),
            vision_ready: Signal::new(false),
            messengers: Signal::new(messengers::fallback_list()),
            ollama_models: Signal::new(Vec::new()),
            languages: Signal::new(Vec::new()),
            paid_index: Signal::new(0),
            free_options: Signal::new(model::offline_free_options()),
            free_index: Signal::new(0),
            short: Signal::new(false),
            on_this_pc: Signal::new(false),
            done: Signal::new(false),
            talk: Signal::new(false),
            failure: Signal::new(String::new()),
            client: Signal::new(client),
            booted: Signal::new(false),
        }
    }

    pub fn ensure_booted(&self) {
        if self.booted.get() {
            return;
        }
        self.booted.set(true);
        let Some(client) = self.client.get() else {
            return;
        };
        let wiz = self.clone();
        let wake = waker().ok();
        std::thread::spawn(move || {
            let _ = super::api::begin_setup(&client, "traditional");
            if let Ok(v) = super::api::get_json(&client, "providers/free") {
                let opts = model::free_providers_or_fallback(&v);
                if let Some(wake) = wake {
                    let _ = wake.post(move || wiz.free_options.set(opts));
                }
            }
        });
    }

    fn remember_draft(&self) {
        let Some(client) = self.client.get() else {
            return;
        };
        let body = json!({
            "mode": "traditional",
            "draft": {
                "llm_provider": self.provider.get(),
                "llm_model": self.model_name.get(),
                "llm_base_url": self.base_url.get(),
                "custom_llm_name": self.custom_name.get(),
                "project_path": self.project_path.get(),
                "persona": model::persona_wire(&self.persona.get()),
                "user_name": self.user_name.get(),
                "name": self.agent_name.get(),
                "agent_gender": self.agent_gender.get(),
                "ui_language": self.language.get(),
                "launch_at_login": self.launch.get(),
            }
        });
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(400));
            let _ = super::api::put_setup(&client, &body);
        });
    }

    pub fn select_provider(&self, id: &str) {
        self.on_this_pc.set(false);
        let current = self.base_url.get();
        self.provider.set(id.into());
        self.error.set(String::new());
        if let Some(base) = model::adopt_base_url(id, &current, &[]) {
            self.base_url.set(base);
        }
        self.remember_draft();
    }

    pub fn advance(&self) {
        if self.saving.get() {
            return;
        }
        let step = self.step.get();
        if step == STEP_PROVIDER
            && model::provider_blocks_next(
                &self.provider.get(),
                &self.api_key_text(),
                &self.base_url.get(),
                self.xai_connected.get(),
            )
        {
            self.error.set(model::key_error(&self.provider.get()).into());
            return;
        }
        self.error.set(String::new());
        if let Some(next) = model::next_step(step, self.short.get()) {
            self.step.set(next);
            self.remember_draft();
        }
    }

    pub fn back(&self) {
        if let Some(prev) = model::prev_step(self.step.get(), self.short.get()) {
            self.step.set(prev);
            self.error.set(String::new());
        }
    }

    /// Hello CTA: local default, then folder and name only.
    pub fn take_recommended(&self) {
        self.short.set(true);
        self.select_provider("ollama");
        self.on_this_pc.set(true);
        self.advance();
    }

    /// Hello quiet link: full provider list, then the rest of the form.
    pub fn take_more(&self) {
        self.short.set(false);
        self.advance();
    }

    pub fn skip(&self) {
        let Some(client) = self.client.get() else {
            self.error.set(model::SERVER_UNREACHABLE.into());
            return;
        };
        self.saving.set(true);
        let done = self.done.clone();
        let error = self.error.clone();
        let saving = self.saving.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.saving.set(false);
            self.error.set(model::SERVER_UNREACHABLE.into());
            return;
        };
        std::thread::spawn(move || {
            let settings = super::api::put_settings(
                &client,
                &json!({
                    "llm_provider": "demo",
                    "llm_base_url": model::DEMO_BASE,
                    "setup_completed": true,
                }),
            );
            let setup = settings.and_then(|_| {
                super::api::put_setup(&client, &json!({ "action": "skip_all" }))
            });
            let _ = waker.post(move || {
                saving.set(false);
                match setup {
                    Ok(_) => { presence::set_skin_override(None); done.set(true); },
                    Err(e) => error.set(e),
                }
            });
        });
    }

    pub fn finish(&self) {
        if self.provider.get() == "ollama" {
            let model = self.model_name.get();
            let pulled = self.ollama_models.get();
            // Recommended / first-run can finish without a pulled model so
            // "On this PC" just works; Ollama can be set up after.
            if !model.is_empty() && !pulled.is_empty() && !pulled.iter().any(|m| m == &model) {
                self.error
                    .set("Pull that model in Ollama first, or pick one from the list.".into());
                return;
            }
        }
        let Some(client) = self.client.get() else {
            self.error.set(model::SERVER_UNREACHABLE.into());
            return;
        };
        self.saving.set(true);
        let body = self.settings_body();
        let done = self.done.clone();
        let error = self.error.clone();
        let saving = self.saving.clone();
        let installing = self.installing.clone();
        let msg = self.install_msg.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.saving.set(false);
            self.error.set(model::SERVER_UNREACHABLE.into());
            return;
        };
        std::thread::spawn(move || {
            if let Ok(status) = super::api::vision_status(&client) {
                let installed = status.get("installed").and_then(|b| b.as_bool()).unwrap_or(false);
                let mode = status.get("mode").and_then(|s| s.as_str()).unwrap_or("");
                let ready = status
                    .pointer("/progress/phase")
                    .and_then(|p| p.as_str())
                    .unwrap_or("")
                    == "ready";
                if !installed && mode != "already_installed" && mode != "local_files" && !ready {
                    let _ = waker.post({
                        let installing = installing.clone();
                        let msg = msg.clone();
                        move || {
                            installing.set(true);
                            msg.set("Downloading local vision model (SmolVLM2 2.2B)…".into());
                        }
                    });
                    let _ = super::api::vision_install(&client);
                }
            }
            let saved = super::api::put_settings(&client, &body).and_then(|_| {
                super::api::put_setup(&client, &json!({ "action": "confirm" }))
            });
            let _ = waker.post(move || {
                saving.set(false);
                installing.set(false);
                match saved {
                    Ok(_) => { presence::set_skin_override(None); done.set(true); },
                    Err(e) => error.set(e),
                }
            });
        });
    }

    /// The key currently in the secret field, without removing it.
    ///
    /// `TextField::value` is always empty for a secret. `take_secret` would clear the
    /// field, so Continue only checks length here and the save copies it out once.
    fn api_key_text(&self) -> String {
        if let Some(field) = self.key_field.borrow().clone() {
            if !field.is_empty() {
                return "present".into();
            }
        }
        self.api_key.get()
    }

    fn take_api_key(&self) -> String {
        if let Some(field) = self.key_field.borrow().clone() {
            if !field.is_empty() {
                return field.take_secret().expose().trim().to_string();
            }
        }
        self.api_key.get()
    }

    fn settings_body(&self) -> Value {
        let mut messengers = Map::new();
        for row in self.messengers.get() {
            if row.enabled.get() {
                let mut item = Map::new();
                item.insert("enabled".into(), json!(true));
                let token = messengers::take_token(&row);
                if !token.is_empty() {
                    item.insert("bot_token".into(), json!(token));
                }
                messengers.insert(row.id, Value::Object(item));
            }
        }
        let mut body = json!({
            "llm_provider": self.provider.get(),
            "llm_model": self.model_name.get(),
            "llm_base_url": self.base_url.get(),
            "custom_llm_name": self.custom_name.get(),
            "llm_api_key": self.take_api_key(),
            "project_path": self.project_path.get(),
            "persona": model::persona_wire(&self.persona.get()),
            "user_name": self.user_name.get(),
            "name": self.agent_name.get(),
            "agent_gender": self.agent_gender.get(),
            "ui_language": self.language.get(),
            "setup_completed": true,
            "launch_at_login": self.launch.get(),
            "start_in_tray": false,
            "close_to_tray": true,
            "vision_enabled": true,
            "vision_model_id": "smolvlm2-2.2b",
        });
        if !messengers.is_empty() {
            body["messengers"] = Value::Object(messengers);
        }
        body
    }

    pub fn xai_sign_in(&self) {
        let Some(client) = self.client.get() else {
            self.xai_msg
                .set("Enter code at xAI when the local server is running.".into());
            return;
        };
        self.xai_busy.set(true);
        self.xai_msg.set(String::new());
        let busy = self.xai_busy.clone();
        let code = self.xai_code.clone();
        let url = self.xai_url.clone();
        let msg = self.xai_msg.clone();
        let connected = self.xai_connected.clone();
        let error = self.error.clone();
        let provider = self.provider.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.xai_busy.set(false);
            return;
        };
        std::thread::spawn(move || {
            let started = super::api::xai_login(&client);
            let for_ui = started.clone();
            let _ = waker.post({
                let busy = busy.clone();
                let code = code.clone();
                let url = url.clone();
                let msg = msg.clone();
                move || match &for_ui {
                    Ok(v) => {
                        let user_code = v.get("user_code").and_then(|s| s.as_str()).unwrap_or("");
                        let verify = v
                            .get("verification_uri")
                            .or_else(|| v.get("verification_url"))
                            .and_then(|s| s.as_str())
                            .unwrap_or("");
                        code.set(user_code.into());
                        url.set(verify.into());
                        if !user_code.is_empty() {
                            msg.set(format!("Enter code {user_code} at xAI."));
                        }
                        busy.set(user_code.is_empty());
                    }
                    Err(e) => {
                        busy.set(false);
                        msg.set(e.clone());
                    }
                }
            });
            let Ok(v) = started else {
                return;
            };
            let sid = v.get("session_id").and_then(|s| s.as_str()).unwrap_or("").to_string();
            if sid.is_empty() {
                return;
            }
            for _ in 0..40 {
                std::thread::sleep(Duration::from_secs(2));
                let Ok(st) = super::api::xai_status(&client, &sid) else {
                    continue;
                };
                let state = st.get("status").and_then(|s| s.as_str()).unwrap_or("");
                if state == "connected" || state == "success" || st.get("connected").and_then(|b| b.as_bool()) == Some(true)
                {
                    let saved = super::api::put_settings(
                        &client,
                        &json!({ "llm_provider": "xai", "setup_completed": false }),
                    );
                    let _ = waker.post(move || {
                        busy.set(false);
                        match saved {
                            Ok(_) => {
                                connected.set(true);
                                provider.set("xai".into());
                                msg.set("Signed in with xAI".into());
                            }
                            Err(_) => error.set(model::XAI_SAVE_FAILED.into()),
                        }
                    });
                    return;
                }
            }
            let _ = waker.post(move || {
                busy.set(false);
            });
        });
    }
}

pub fn wizard_screen(ui: &Ui, wiz: Wizard) -> El {
    wiz.ensure_booted();
    presence::set_skin_override(Some("square"));
    let unit = ui.theme().spacing.unit;
    let pct = Signal::new(Some(model::progress_fraction(wiz.step.get(), wiz.short.get())));
    let pct2 = pct.clone();
    let step = wiz.step.clone();
    let short = wiz.short.clone();
    let ui_banner = ui.clone();
    let banner = chrome::when(
        ui,
        {
            let f = wiz.failure.clone();
            move || !f.get().trim().is_empty()
        },
        {
            let f = wiz.failure.clone();
            move || {
                w::text(&ui_banner, "").variant("muted").text(move || {
                    format!(
                        "The mind couldn’t start. {} Traditional setup has the same settings. You can retry the mind from Talk with Remedy, or keep going here.",
                        f.get()
                    )
                })
            }
        },
    );
    let after_hello = {
        let step = wiz.step.clone();
        move || step.get() != STEP_WELCOME
    };
    let inner = chrome::stack(
        ui,
        vec![
            banner,
            chrome::when_ui(ui, after_hello, move |ui| chrome::progress_bar(ui, pct.clone())),
            welcome(ui, wiz.clone()),
            provider_step(ui, wiz.clone()),
            folder_step(ui, wiz.clone()),
            style_step(ui, wiz.clone()),
            chat_step(ui, wiz.clone()),
            vision_step(ui, wiz.clone()),
            ready_step(ui, wiz.clone()),
            chrome::error_text(ui, wiz.error.clone()),
            nav(ui, wiz.clone()),
        ],
    );
    // The seven steps are taller than the card. Scroll the stack instead of clipping it.
    let inner = w::scroll(ui).child(inner).layout(|l| {
        l.width = Length::Percent(1.0);
        l.max_height = Length::Px(560.0);
        l.grow = 0.0;
        l.min_height = Length::Px(0.0);
    });
    let screen = chrome::center(ui, inner, 480.0);
    let screen2 = screen.clone();
    let screen = screen.bind(move || {
        pct2.set(Some(model::progress_fraction(step.get(), short.get())));
        let _ = &screen2;
        let _ = unit;
    });
    screen
}

fn on_step(wiz: &Wizard, step: usize) -> impl Fn() -> bool + 'static {
    let s = wiz.step.clone();
    move || s.get() == step
}

fn welcome(ui: &Ui, wiz: Wizard) -> El {
    let wiz_body = wiz.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_WELCOME), move |ui| {
        let langs = wiz_body.languages.clone();
        let wiz_lang = wiz_body.clone();
        let wiz_go = wiz_body.clone();
        let wiz_more = wiz_body.clone();
        let wiz_talk = wiz_body.clone();
        let wiz_skip = wiz_body.clone();
        let face_ui = ui.clone();
        let face_mark = show(ui, move || presence::enabled(), move || {
            presence::mark(&face_ui, Mood::Happy, 72.0)
        })
        .el();
        let face = w::column(ui)
            .gap(SPACE)
            .align(Align::Center)
            .layout(|l| l.width = Length::Percent(1.0))
            .child(face_mark)
            .child(w::text(ui, "I'm Remedy.").style(typo(HERO)))
            .child(
                w::text(ui, "On this PC. No account, no key.")
                    .style(typo(META))
                    .variant("muted"),
            );
        chrome::stack(
            ui,
            vec![
                face,
                chrome::primary(ui, "Use the recommended setup", move || wiz_go.take_recommended()),
                chrome::quiet(ui, "More options", move || wiz_more.take_more()),
                chrome::when_ui(ui, move || !langs.get().is_empty(), move |ui| {
                    language_select(ui, wiz_lang.clone())
                }),
                chrome::quiet(ui, "Talk with Remedy instead", move || wiz_talk.talk.set(true)),
                chrome::quiet(ui, "Skip for now", move || wiz_skip.skip()),
            ],
        )
    })
}

fn language_select(ui: &Ui, wiz: Wizard) -> El {
    let options = wiz.languages.get();
    let index = Signal::new(0usize);
    let index2 = index.clone();
    let language = wiz.language.clone();
    let row = w::select(ui, "Language", options, index);
    let row2 = row.clone();
    let row = row.bind(move || {
        let langs = wiz.languages.get();
        if let Some(code) = langs.get(index2.get()) {
            language.set(code.clone());
        }
        let _ = &row2;
    });
    row
}

fn provider_step(ui: &Ui, wiz: Wizard) -> El {
    let wiz_body = wiz.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_PROVIDER), move |ui| {
        let wiz = wiz_body.clone();
        let unit = ui.theme().spacing.unit;
        let on_pc = chrome::choice(
            ui,
            "On this PC",
            "No account, no key. Local models when they’re here; you can add more later.",
            {
                let flag = wiz.on_this_pc.clone();
                move || flag.get()
            },
            {
                let wiz = wiz.clone();
                move || {
                    wiz.select_provider("ollama");
                    wiz.on_this_pc.set(true);
                }
            },
        );
        let demo = chrome::choice(
            ui,
            "Demo · no signup",
            "Chat immediately on a rate-limited free gateway. Switch later in Settings.",
            {
                let p = wiz.provider.clone();
                move || p.get() == "demo"
            },
            {
                let wiz = wiz.clone();
                move || wiz.select_provider("demo")
            },
        );
        let ollama = chrome::choice(
            ui,
            "Ollama · free & private on this PC",
            model::OLLAMA_HINT,
            {
                let p = wiz.provider.clone();
                let flag = wiz.on_this_pc.clone();
                move || p.get() == "ollama" && !flag.get()
            },
            {
                let wiz = wiz.clone();
                move || wiz.select_provider("ollama")
            },
        );
        let free_keys = model::free_key_options(&wiz.free_options.get());
        let free_ids: Vec<String> = free_keys.iter().map(|o| o.id.clone()).collect();
        let free_names: Vec<String> = std::iter::once("Choose provider…".to_string())
            .chain(free_keys.iter().map(|o| o.title.clone()))
            .collect();
        let free = w::select(ui, "Or a free key", free_names, wiz.free_index.clone());
        let free2 = free.clone();
        let wiz_free = wiz.clone();
        let free_keys_bind = free_keys.clone();
        let free = free.bind(move || {
            let i = wiz_free.free_index.get();
            if i > 0 {
                if let Some(p) = free_keys_bind.get(i - 1) {
                    if wiz_free.provider.get() != p.id {
                        wiz_free.select_provider(&p.id);
                        wiz_free.paid_index.set(0);
                    }
                }
            }
            let _ = &free2;
        });
        let paid_list = model::paid_providers_except(&free_ids);
        let paid_names: Vec<String> = std::iter::once("Choose provider…".to_string())
            .chain(paid_list.iter().map(|p| p.name.to_string()))
            .collect();
        let paid = w::select(ui, "Or pick a paid / full provider", paid_names, wiz.paid_index.clone());
        let paid2 = paid.clone();
        let wiz_paid = wiz.clone();
        let paid = paid.bind(move || {
            let i = wiz_paid.paid_index.get();
            if i > 0 {
                if let Some(p) = paid_list.get(i - 1) {
                    if wiz_paid.provider.get() != p.id {
                        wiz_paid.select_provider(p.id);
                        wiz_paid.free_index.set(0);
                    }
                }
            }
            let _ = &paid2;
        });
        let key = api_key_field(ui, wiz.clone());
        let model_field = model_field(ui, wiz.clone());
        chrome::stack(
            ui,
            vec![
                w::text(ui, "Recommended").style(typo(SECTION)),
                on_pc,
                w::text(ui, "Free").style(typo(SECTION)),
                demo,
                ollama,
                w::text(ui, "Or a free key").variant("muted"),
                free,
                w::text(ui, "Other models").style(typo(SECTION)),
                w::text(ui, "Or pick a paid / full provider").variant("muted"),
                paid,
                chrome::when_ui(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "demo"
                    },
                    |ui| {
                        w::text(
                            ui,
                            "Demo is rate-limited and not private. Prefer Ollama or your own key for real work.",
                        )
                        .variant("muted")
                    },
                ),
                chrome::when_ui(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "xai"
                    },
                    {
                        let wiz = wiz.clone();
                        move |ui| xai_box(ui, wiz.clone())
                    },
                ),
                chrome::when(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || {
                            let id = p.get();
                            id != "demo" && id != "ollama"
                        }
                    },
                    move || key.clone(),
                ),
                model_field,
                chrome::when_ui(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || model::shows_base_url(&p.get())
                    },
                    {
                        let wiz = wiz.clone();
                        move |ui| base_field(ui, wiz.clone())
                    },
                ),
                chrome::when_ui(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "custom"
                    },
                    {
                        let wiz = wiz.clone();
                        move |ui| custom_name_field(ui, wiz.clone())
                    },
                ),
            ],
        )
        .gap(unit)
    })
}

fn api_key_field(ui: &Ui, wiz: Wizard) -> El {
    let label = if wiz.provider.get() == "xai" {
        "API key (optional if signed in)"
    } else {
        "API key"
    };
    let slot = wiz.key_field.clone();
    let field = {
        let mut held = slot.borrow_mut();
        if held.is_none() {
            *held = Some(w::secret_input(ui, label));
        }
        held.clone().unwrap()
    };
    field.el()
}

fn model_field(ui: &Ui, wiz: Wizard) -> El {
    let field = w::text_input(ui, "Model").placeholder("Or type a model name");
    let f2 = field.clone();
    let model_name = wiz.model_name.clone();
    let field = field.on_change(move || model_name.set(f2.value()));
    field.el()
}

fn base_field(ui: &Ui, wiz: Wizard) -> El {
    let field = w::text_input(ui, "Base URL");
    let f2 = field.clone();
    let url = wiz.base_url.clone();
    field.set_value(&wiz.base_url.get());
    let field = field.on_change(move || url.set(f2.value()));
    field.el()
}

fn custom_name_field(ui: &Ui, wiz: Wizard) -> El {
    let field = w::text_input(ui, "Name (shown in the status bar)");
    let f2 = field.clone();
    let name = wiz.custom_name.clone();
    let field = field.on_change(move || name.set(f2.value()));
    field.el()
}

fn xai_box(ui: &Ui, wiz: Wizard) -> El {
    let unit = ui.theme().spacing.unit;
    let connected = wiz.xai_connected.clone();
    let signed_out = wiz.xai_connected.clone();
    let busy = wiz.xai_busy.clone();
    let code_on = wiz.xai_code.clone();
    let wiz_sign = wiz.clone();
    let wiz_code = wiz.clone();
    chrome::stack(
        ui,
        vec![
            chrome::when_ui(ui, move || connected.get(), |ui| {
                w::text(ui, "Signed in with xAI").variant("strong")
            }),
            chrome::when_ui(ui, move || !signed_out.get(), move |ui| {
                let wiz_sign = wiz_sign.clone();
                chrome::primary(ui, "Sign in with xAI", move || wiz_sign.xai_sign_in())
            }),
            chrome::when_ui(ui, move || busy.get(), |ui| w::text(ui, "Waiting…").variant("muted")),
            chrome::when_ui(ui, move || !code_on.get().is_empty(), move |ui| {
                let wiz = wiz_code.clone();
                w::text(ui, "").variant("muted").text(move || {
                    let code = wiz.xai_code.get();
                    let url = wiz.xai_url.get();
                    if url.is_empty() {
                        format!("Enter code {code} at xAI.")
                    } else {
                        format!("Enter code {code} at xAI.\n{url}")
                    }
                })
            }),
            chrome::error_text(ui, wiz.xai_msg.clone()),
        ],
    )
    .gap(unit)
}

fn folder_step(ui: &Ui, wiz: Wizard) -> El {
    let path_sig = wiz.project_path.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_FOLDER), move |ui| {
        let field = w::text_input(ui, "Default project folder").placeholder(model::WORKSPACE_PLACEHOLDER);
        let f2 = field.clone();
        let path = path_sig.clone();
        let field = field.on_change(move || path.set(f2.value()));
        chrome::stack(
            ui,
            vec![
                w::text(ui, "Where should we work?").style(typo(TITLE)),
                w::text(ui, "Default project folder for tools and shell (optional).")
                    .style(typo(META))
                    .variant("muted"),
                field.el(),
            ],
        )
    })
}

fn style_step(ui: &Ui, wiz: Wizard) -> El {
    let wiz_body = wiz.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_STYLE), move |ui| {
        let wiz = wiz_body.clone();
        let name = labeled_input(ui, "Your name", "e.g. Alex", wiz.user_name.clone());
        let agent = labeled_input(ui, "Partner name", "Remedy", wiz.agent_name.clone());
        let genders = chrome::stack(
            ui,
            vec![
                gender(ui, wiz.clone(), "female", "Female", "she/her"),
                gender(ui, wiz.clone(), "male", "Male", "he/him"),
                gender(ui, wiz.clone(), "neutral", "Neither / AI", "they/them"),
            ],
        );
        let styles = chrome::stack(
            ui,
            vec![
                style_card(ui, wiz.clone(), "balanced", "Balanced", "Helpful and adaptable to the task"),
                style_card(ui, wiz.clone(), "efficient", "Efficient", "Concise, code-first, minimal explanation"),
                style_card(ui, wiz.clone(), "detailed", "Detailed", "Thorough explanations with context"),
                style_card(ui, wiz.clone(), "playful", "Playful", "Casual tone with light humor"),
            ],
        );
        if wiz.short.get() {
            chrome::stack(
                ui,
                vec![
                    w::text(ui, "What should I call you?").style(typo(TITLE)),
                    name,
                ],
            )
        } else {
            chrome::stack(
                ui,
                vec![
                    w::text(ui, "What should I call you?").style(typo(TITLE)),
                    name,
                    agent,
                    w::text(ui, "Partner gender").variant("strong"),
                    genders,
                    w::text(ui, "Style").variant("strong"),
                    styles,
                ],
            )
        }
    })
}

fn labeled_input(ui: &Ui, label: &'static str, placeholder: &'static str, value: Signal<String>) -> El {
    let field = w::text_input(ui, label).placeholder(placeholder);
    field.set_value(&value.get());
    let f2 = field.clone();
    let field = field.on_change(move || value.set(f2.value()));
    field.el()
}

fn gender(ui: &Ui, wiz: Wizard, id: &'static str, title: &'static str, hint: &'static str) -> El {
    chrome::choice(
        ui,
        title,
        hint,
        {
            let g = wiz.agent_gender.clone();
            move || g.get() == id
        },
        move || {
            wiz.agent_gender.set(id.into());
            wiz.remember_draft();
        },
    )
}

fn style_card(ui: &Ui, wiz: Wizard, id: &'static str, title: &'static str, hint: &'static str) -> El {
    chrome::choice(
        ui,
        title,
        hint,
        {
            let p = wiz.persona.clone();
            move || p.get() == id
        },
        move || {
            wiz.persona.set(id.into());
            wiz.remember_draft();
        },
    )
}

fn chat_step(ui: &Ui, wiz: Wizard) -> El {
    let rows = wiz.messengers.clone();
    chrome::when_ui(ui, on_step(&wiz, model::STEP_CHAT), move |ui| {
        messengers::view(ui, rows.clone())
    })
}

fn vision_step(ui: &Ui, wiz: Wizard) -> El {
    let ready = wiz.vision_ready.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_VISION), move |ui| {
        chrome::stack(
            ui,
            vec![
                w::text(ui, "What Remedy needs").variant("strong"),
                w::text(
                    ui,
                    "Vision, voice, and local helpers download automatically on this computer. Nothing to opt into — Remedy starts the downloads.",
                )
                .variant("muted"),
                chrome::choice(
                    ui,
                    "Downloading local vision, voice, and helpers",
                    "Pinned SmolVLM2 2.2B plus Remedy's voice. You can keep going — this continues in the background.",
                    {
                        let ready = ready.clone();
                        move || !ready.get()
                    },
                    || {},
                ),
            ],
        )
    })
}

fn ready_step(ui: &Ui, wiz: Wizard) -> El {
    let wiz_body = wiz.clone();
    chrome::when_ui(ui, on_step(&wiz, STEP_READY), move |ui| {
        let wiz = wiz_body.clone();
        let launch = w::check(
            ui,
            "Start with Windows — Optional tray + warm server",
            wiz.launch.clone(),
        );
        let installing = wiz.installing.clone();
        let idle = wiz.installing.clone();
        let wiz_install = wiz.clone();
        let wiz_idle = wiz.clone();
        chrome::stack(
            ui,
            vec![
                chrome::when_ui(ui, move || installing.get(), move |ui| {
                    installing_block(ui, wiz_install.clone())
                }),
                chrome::when_ui(ui, move || !idle.get(), move |ui| {
                    let wiz = wiz_idle.clone();
                    chrome::stack(
                        ui,
                        vec![
                            w::text(ui, "We're ready.").style(typo(TITLE)),
                            w::text(ui, "I'll meet you in the Grove.")
                                .style(typo(BODY))
                                .variant("muted"),
                            launch.clone(),
                            chrome::primary(ui, "Open Grove", move || wiz.finish()),
                        ],
                    )
                }),
            ],
        )
    })
}

fn installing_block(ui: &Ui, wiz: Wizard) -> El {
    chrome::stack(
        ui,
        vec![
            w::text(ui, "Installing local model").variant("strong"),
            w::progress(ui, "Vision install", wiz.install_pct.clone()),
            w::text(ui, "").variant("muted").text({
                let m = wiz.install_msg.clone();
                move || m.get()
            }),
            chrome::quiet(ui, "Use app while downloading", {
                let wiz = wiz.clone();
                move || { presence::set_skin_override(None); wiz.done.set(true); }
            }),
        ],
    )
}

fn nav(ui: &Ui, wiz: Wizard) -> El {
    let step = wiz.step.clone();
    let wiz_body = wiz.clone();
    chrome::when_ui(
        ui,
        move || {
            let step = step.get();
            step != STEP_WELCOME && step != STEP_READY
        },
        move |ui| {
            let wiz_back = wiz_body.clone();
            let wiz_next = wiz_body.clone();
            let wiz_skip = wiz_body.clone();
            chrome::stack(
                ui,
                vec![
                    chrome::actions(
                        ui,
                        vec![
                            chrome::quiet(ui, "Back", move || wiz_back.back()),
                            chrome::primary(ui, "Continue", move || wiz_next.advance()),
                        ],
                    ),
                    chrome::quiet(ui, "Skip remaining", move || wiz_skip.skip()),
                ],
            )
        },
    )
}
