//! Traditional setup wizard. The API key stays in a signal and never enters the draft.

use std::time::Duration;

use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use serde_json::json;

use super::chrome;
use super::messengers::{self, Messenger};
use super::model::{self, STEP_FOLDER, STEP_PROVIDER, STEP_READY, STEP_STYLE, STEP_VISION, STEP_WELCOME};
use crate::logo;
use crate::runtime::Client;

#[derive(Clone)]
pub struct Wizard {
    pub step: Signal<usize>,
    pub provider: Signal<String>,
    pub api_key: Signal<String>,
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
    pub done: Signal<bool>,
    pub talk: Signal<bool>,
    pub failure: Signal<String>,
    pub client: Option<Client>,
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
            done: Signal::new(false),
            talk: Signal::new(false),
            failure: Signal::new(String::new()),
            client,
            booted: Signal::new(false),
        }
    }

    pub fn ensure_booted(&self) {
        if self.booted.get() {
            return;
        }
        self.booted.set(true);
        let Some(client) = self.client.clone() else {
            return;
        };
        std::thread::spawn(move || {
            let _ = super::api::begin_setup(&client, "traditional");
        });
    }

    fn remember_draft(&self) {
        let Some(client) = self.client.clone() else {
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
        self.provider.set(id.into());
        self.error.set(String::new());
        if self.base_url.get().is_empty() || id == "ollama" || id == "custom" {
            let base = model::provider_base(id);
            if !base.is_empty() {
                self.base_url.set(base.into());
            }
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
                &self.api_key.get(),
                &self.base_url.get(),
                self.xai_connected.get(),
            )
        {
            self.error.set(model::key_error(&self.provider.get()).into());
            return;
        }
        self.error.set(String::new());
        if let Some(next) = model::next_step(step) {
            self.step.set(next);
            self.remember_draft();
        }
    }

    pub fn back(&self) {
        if let Some(prev) = model::prev_step(self.step.get()) {
            self.step.set(prev);
            self.error.set(String::new());
        }
    }

    pub fn skip(&self) {
        let Some(client) = self.client.clone() else {
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
                    Ok(_) => done.set(true),
                    Err(e) => error.set(e),
                }
            });
        });
    }

    pub fn finish(&self) {
        if self.provider.get() == "ollama" {
            let model = self.model_name.get();
            let pulled = self.ollama_models.get();
            if !pulled.iter().any(|m| m == &model) {
                self.error
                    .set("Pull that model in Ollama first, or pick one from the list.".into());
                return;
            }
        }
        let Some(client) = self.client.clone() else {
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
                    Ok(_) => done.set(true),
                    Err(e) => error.set(e),
                }
            });
        });
    }

    fn settings_body(&self) -> serde_json::Value {
        let mut messengers = Vec::new();
        for row in self.messengers.get() {
            if row.enabled.get() {
                let token = row.token.get();
                let mut item = json!({ "id": row.id, "enabled": true });
                if !token.trim().is_empty() {
                    item["bot_token"] = json!(token.trim());
                }
                messengers.push(item);
            }
        }
        json!({
            "llm_provider": self.provider.get(),
            "llm_model": self.model_name.get(),
            "llm_base_url": self.base_url.get(),
            "custom_llm_name": self.custom_name.get(),
            "llm_api_key": self.api_key.get(),
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
            "messengers": messengers,
        })
    }

    pub fn xai_sign_in(&self) {
        let Some(client) = self.client.clone() else {
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
            let _ = waker.post({
                let busy = busy.clone();
                let code = code.clone();
                let url = url.clone();
                let msg = msg.clone();
                move || match &started {
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
    let unit = ui.theme().spacing.unit;
    let pct = Signal::new(Some(model::progress_fraction(wiz.step.get())));
    let pct2 = pct.clone();
    let step = wiz.step.clone();
    let header = w::column(ui)
        .gap(unit * 0.6)
        .align(Align::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(logo::wordmark(ui, 40.0))
        .child(chrome::heading(ui, "Remedy"))
        .child(chrome::sub(ui, "Your local AI partner"))
        .child(chrome::quiet(ui, "Talk with Remedy instead", {
            let wiz = wiz.clone();
            move || wiz.talk.set(true)
        }));
    let banner = chrome::when(
        ui,
        {
            let f = wiz.failure.clone();
            move || !f.get().trim().is_empty()
        },
        {
            let f = wiz.failure.clone();
            move || {
                w::text(ui, "").variant("muted").text(move || {
                    format!(
                        "The mind couldn’t start. {} Traditional setup has the same settings. You can retry the mind from Talk with Remedy, or keep going here.",
                        f.get()
                    )
                })
            }
        },
    );
    let inner = chrome::stack(
        ui,
        vec![
            header,
            banner,
            chrome::progress_bar(ui, pct),
            chrome::step_chips(ui, wiz.step.clone()),
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
    let screen = chrome::center(ui, inner, 520.0);
    let screen2 = screen.clone();
    screen.bind(move || {
        pct2.set(Some(model::progress_fraction(step.get())));
        let _ = &screen2;
    });
    screen
}

fn on_step(wiz: &Wizard, step: usize) -> impl Fn() -> bool + 'static {
    let s = wiz.step.clone();
    move || s.get() == step
}

fn welcome(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_WELCOME), move || {
        let langs = wiz.languages.clone();
        chrome::stack(
            ui,
            vec![
                w::text(ui, "Set up a provider and start chatting. Takes about a minute."),
                chrome::when(
                    ui,
                    move || !langs.get().is_empty(),
                    {
                        let wiz = wiz.clone();
                        move || language_select(ui, wiz.clone())
                    },
                ),
                chrome::primary(ui, "Get Started", {
                    let wiz = wiz.clone();
                    move || wiz.advance()
                }),
                chrome::quiet(ui, "Talk with Remedy instead", {
                    let wiz = wiz.clone();
                    move || wiz.talk.set(true)
                }),
                chrome::quiet(ui, "Skip for now", {
                    let wiz = wiz.clone();
                    move || wiz.skip()
                }),
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
    row.bind(move || {
        let langs = wiz.languages.get();
        if let Some(code) = langs.get(index2.get()) {
            language.set(code.clone());
        }
        let _ = &row2;
    });
    row
}

fn provider_step(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_PROVIDER), move || {
        let unit = ui.theme().spacing.unit;
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
                move || p.get() == "ollama"
            },
            {
                let wiz = wiz.clone();
                move || wiz.select_provider("ollama")
            },
        );
        let paid_names: Vec<String> = std::iter::once("Choose provider…".to_string())
            .chain(model::paid_providers().into_iter().map(|p| p.name.to_string()))
            .collect();
        let paid = w::select(ui, "Or pick a paid / full provider", paid_names, wiz.paid_index.clone());
        let paid2 = paid.clone();
        let wiz_paid = wiz.clone();
        paid.bind(move || {
            let i = wiz_paid.paid_index.get();
            if i > 0 {
                if let Some(p) = model::paid_providers().get(i - 1) {
                    if wiz_paid.provider.get() != p.id {
                        wiz_paid.select_provider(p.id);
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
                w::text(ui, "Start free").variant("strong"),
                demo,
                ollama,
                w::text(ui, "Or pick a paid / full provider").variant("muted"),
                paid,
                chrome::when(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "demo"
                    },
                    || {
                        w::text(
                            ui,
                            "Demo is rate-limited and not private. Prefer Ollama or your own key for real work.",
                        )
                        .variant("muted")
                    },
                ),
                chrome::when(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "xai"
                    },
                    {
                        let wiz = wiz.clone();
                        move || xai_box(ui, wiz.clone())
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
                chrome::when(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || model::shows_base_url(&p.get())
                    },
                    {
                        let wiz = wiz.clone();
                        move || base_field(ui, wiz.clone())
                    },
                ),
                chrome::when(
                    ui,
                    {
                        let p = wiz.provider.clone();
                        move || p.get() == "custom"
                    },
                    {
                        let wiz = wiz.clone();
                        move || custom_name_field(ui, wiz.clone())
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
    let field = w::secret_input(ui, label);
    let f2 = field.clone();
    let key = wiz.api_key.clone();
    field.on_change(move || {
        let shown = f2.value();
        if !shown.is_empty() {
            key.set(shown);
        }
    });
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
    chrome::stack(
        ui,
        vec![
            chrome::when(
                ui,
                {
                    let c = wiz.xai_connected.clone();
                    move || c.get()
                },
                || w::text(ui, "Signed in with xAI").variant("strong"),
            ),
            chrome::when(
                ui,
                {
                    let c = wiz.xai_connected.clone();
                    move || !c.get()
                },
                {
                    let wiz = wiz.clone();
                    move || {
                        chrome::primary(ui, "Sign in with xAI", {
                            let wiz = wiz.clone();
                            move || wiz.xai_sign_in()
                        })
                    }
                },
            ),
            chrome::when(
                ui,
                {
                    let b = wiz.xai_busy.clone();
                    move || b.get()
                },
                || w::text(ui, "Waiting…").variant("muted"),
            ),
            chrome::when(
                ui,
                {
                    let c = wiz.xai_code.clone();
                    move || !c.get().is_empty()
                },
                {
                    let wiz = wiz.clone();
                    move || {
                        w::text(ui, "").variant("muted").text(move || {
                            let code = wiz.xai_code.get();
                            let url = wiz.xai_url.get();
                            if url.is_empty() {
                                format!("Enter code {code} at xAI.")
                            } else {
                                format!("Enter code {code} at xAI.\n{url}")
                            }
                        })
                    }
                },
            ),
            chrome::error_text(ui, wiz.xai_msg.clone()),
        ],
    )
    .gap(unit)
}

fn folder_step(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_FOLDER), move || {
        let field = w::text_input(ui, "Default project folder").placeholder(model::WORKSPACE_PLACEHOLDER);
        let f2 = field.clone();
        let path = wiz.project_path.clone();
        let field = field.on_change(move || path.set(f2.value()));
        chrome::stack(
            ui,
            vec![
                w::text(ui, "Default project folder for tools and shell (optional).").variant("muted"),
                field.el(),
            ],
        )
    })
}

fn style_step(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_STYLE), move || {
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
        chrome::stack(
            ui,
            vec![
                name,
                agent,
                w::text(ui, "Partner gender").variant("strong"),
                genders,
                w::text(ui, "Style").variant("strong"),
                styles,
            ],
        )
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
    chrome::when(ui, on_step(&wiz, model::STEP_CHAT), move || {
        messengers::view(ui, wiz.messengers.clone())
    })
}

fn vision_step(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_VISION), move || {
        let ready = wiz.vision_ready.clone();
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
                    move || !ready.get(),
                    || {},
                ),
            ],
        )
    })
}

fn ready_step(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(ui, on_step(&wiz, STEP_READY), move || {
        let launch = w::check(
            ui,
            "Start with Windows — Optional tray + warm server",
            wiz.launch.clone(),
        );
        chrome::stack(
            ui,
            vec![
                chrome::when(
                    ui,
                    {
                        let i = wiz.installing.clone();
                        move || i.get()
                    },
                    {
                        let wiz = wiz.clone();
                        move || installing_block(ui, wiz.clone())
                    },
                ),
                chrome::when(
                    ui,
                    {
                        let i = wiz.installing.clone();
                        move || !i.get()
                    },
                    {
                        let wiz = wiz.clone();
                        move || {
                            chrome::stack(
                                ui,
                                vec![
                                    chrome::heading(ui, "You're ready"),
                                    chrome::sub(ui, "Enter to send · F1 for help"),
                                    launch,
                                    chrome::primary(ui, "Start chatting", {
                                        let wiz = wiz.clone();
                                        move || wiz.finish()
                                    }),
                                ],
                            )
                        }
                    },
                ),
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
                move || wiz.done.set(true)
            }),
        ],
    )
}

fn nav(ui: &Ui, wiz: Wizard) -> El {
    chrome::when(
        ui,
        {
            let s = wiz.step.clone();
            move || {
                let step = s.get();
                step != STEP_WELCOME && step != STEP_READY
            }
        },
        move || {
            chrome::stack(
                ui,
                vec![
                    chrome::actions(
                        ui,
                        vec![
                            chrome::quiet(ui, "Back", {
                                let wiz = wiz.clone();
                                move || wiz.back()
                            }),
                            chrome::primary(ui, "Continue", {
                                let wiz = wiz.clone();
                                move || wiz.advance()
                            }),
                        ],
                    ),
                    chrome::quiet(ui, "Skip remaining", {
                        let wiz = wiz.clone();
                        move || wiz.skip()
                    }),
                ],
            )
        },
    )
}
