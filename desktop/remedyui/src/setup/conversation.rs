//! House-rules conversation. The kickoff line matches the Tauri app exactly.

use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;
use super::model::{self, Question, TopicStatus};
use crate::runtime::Client;
use crate::studio::WorkMode;

#[derive(Clone, Debug)]
struct Bubble {
    id: String,
    role: String,
    text: String,
}

#[derive(Clone)]
pub struct Conversation {
    pub bubbles: Signal<Vec<Bubble>>,
    pub input: Signal<String>,
    pub busy: Signal<bool>,
    pub error: Signal<String>,
    pub skipped: Signal<Vec<String>>,
    pub draft_keys: Signal<Vec<String>>,
    pub questions: Signal<Vec<Question>>,
    pub traditional: Signal<bool>,
    pub done: Signal<bool>,
    pub session_id: Signal<String>,
    pub failure: Signal<String>,
    pub client: Signal<Option<Client>>,
    started: Signal<bool>,
}

impl Conversation {
    pub fn preview() -> Self {
        Self::new(None, Signal::new(String::new()))
    }

    pub fn new(client: Option<Client>, failure: Signal<String>) -> Self {
        Self {
            bubbles: Signal::new(vec![Bubble {
                id: "kickoff".into(),
                role: "assistant".into(),
                text: model::KICKOFF.into(),
            }]),
            input: Signal::new(String::new()),
            busy: Signal::new(false),
            error: Signal::new(String::new()),
            skipped: Signal::new(Vec::new()),
            draft_keys: Signal::new(Vec::new()),
            questions: Signal::new(model::default_questions()),
            traditional: Signal::new(false),
            done: Signal::new(false),
            session_id: Signal::new(String::new()),
            failure,
            client: Signal::new(client),
            started: Signal::new(false),
        }
    }

    pub fn ensure_started(&self) {
        if self.started.get() {
            return;
        }
        self.started.set(true);
        let Some(client) = self.client.get() else {
            return;
        };
        let session_id = self.session_id.clone();
        let questions = self.questions.clone();
        let skipped_boot = self.skipped.clone();
        let draft_boot = self.draft_keys.clone();
        let bubbles = self.bubbles.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            return;
        };
        std::thread::Builder::new()
            .name("remedy-house-rules".into())
            .spawn(move || {
                let _ = super::api::begin_setup(&client, "conversation");
                let setup_first = super::api::get_json(&client, "setup").ok();
                let mut sid = setup_first
                    .as_ref()
                    .and_then(|v| v.get("setup_session_id"))
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                if sid.is_empty() {
                    if let Ok(created) = super::api::create_house_session(&client) {
                        sid = created;
                        let _ = super::api::put_setup(
                            &client,
                            &serde_json::json!({ "setup_session_id": sid }),
                        );
                    }
                }
                let prior = if sid.is_empty() {
                    Vec::new()
                } else {
                    super::api::list_messages(&client, &sid).unwrap_or_default()
                };
                let setup = super::api::get_json(&client, "setup").ok().or(setup_first);
                let _ = waker.post(move || {
                    if !sid.is_empty() {
                        session_id.set(sid);
                    }
                    if let Some(v) = setup {
                        apply_session(&questions, &skipped_boot, &draft_boot, &v);
                    }
                    if !prior.is_empty() {
                        bubbles.set(
                            prior
                                .into_iter()
                                .map(|(id, role, text)| Bubble { id, role, text })
                                .collect(),
                        );
                    }
                });
            })
            .ok();
    }

    pub fn send_text(&self, text: String) {
        let text = text.trim().to_string();
        if text.is_empty() || self.busy.get() {
            return;
        }
        self.input.set(String::new());
        self.error.set(String::new());
        if text.eq_ignore_ascii_case("skip all") {
            self.skip_all();
            return;
        }
        if text.eq_ignore_ascii_case("skip") {
            self.skip_one();
        }
        let id = format!("u-{}", self.bubbles.get().len());
        self.bubbles.update(|b| {
            b.push(Bubble {
                id,
                role: "user".into(),
                text: text.clone(),
            });
        });
        let Some(client) = self.client.get() else {
            self.error.set(model::MIND_ANSWER_FAILED.into());
            return;
        };
        self.busy.set(true);
        let aid = format!("a-{}", self.bubbles.get().len());
        self.bubbles.update(|b| {
            b.push(Bubble {
                id: aid,
                role: "assistant".into(),
                text: String::new(),
            });
        });
        let sid = self.session_id.get();
        if sid.is_empty() {
            self.busy.set(false);
            self.error.set(model::MIND_ANSWER_FAILED.into());
            return;
        }
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.busy.set(false);
            return;
        };
        let bubbles = self.bubbles.clone();
        let busy = self.busy.clone();
        let error = self.error.clone();
        let questions_live = self.questions.clone();
        let skipped_live = self.skipped.clone();
        let draft_live = self.draft_keys.clone();
        let client_live = client.clone();
        crate::runtime::stream_message(
            &client,
            &sid,
            &text,
            "",
            "",
            WorkMode::Chat,
            move |kind, data| {
                if kind == "done" {
                    let setup = super::api::get_json(&client_live, "setup").ok();
                    let busy = busy.clone();
                    let questions_live = questions_live.clone();
                    let skipped_live = skipped_live.clone();
                    let draft_live = draft_live.clone();
                    let _ = waker.post(move || {
                        busy.set(false);
                        if let Some(v) = setup.as_ref() {
                            apply_session(&questions_live, &skipped_live, &draft_live, v);
                        }
                    });
                    return;
                }
                let bubbles = bubbles.clone();
                let busy = busy.clone();
                let error = error.clone();
                let _ = waker.post(move || {
                    if kind == "error" {
                        busy.set(false);
                        error.set(model::MIND_ANSWER_FAILED.into());
                        return;
                    }
                    let bit = crate::sse::token_text(&data);
                    if bit.is_empty() {
                        return;
                    }
                    bubbles.update(|rows| {
                        if let Some(last) = rows.last_mut() {
                            if last.role == "assistant" {
                                last.text.push_str(&bit);
                            }
                        }
                    });
                });
            },
        );
    }

    pub fn skip_one(&self) {
        let questions = self.questions.get();
        let skipped = self.skipped.get();
        let draft = self.draft_keys.get();
        if let Some(q) = questions.iter().find(|q| {
            model::topic_status(&q.id, &skipped, &draft) == TopicStatus::Open
        }) {
            let id = q.id.clone();
            self.skipped.update(|s| {
                if !s.iter().any(|x| x == &id) {
                    s.push(id);
                }
            });
        }
    }

    pub fn skip_all(&self) {
        let ids: Vec<String> = self.questions.get().into_iter().map(|q| q.id).collect();
        self.skipped.set(ids);
        self.bubbles.update(|b| {
            b.push(Bubble {
                id: format!("skip-{}", b.len()),
                role: "user".into(),
                text: "skip all".into(),
            });
        });
        if let Some(client) = self.client.get() {
            std::thread::spawn(move || {
                let _ = super::api::put_setup(&client, &serde_json::json!({ "action": "skip_all" }));
            });
        }
        self.done.set(true);
    }

    pub fn confirm(&self) {
        let Some(client) = self.client.get() else {
            self.error.set(model::SERVER_UNREACHABLE.into());
            return;
        };
        let done = self.done.clone();
        let error = self.error.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            return;
        };
        std::thread::spawn(move || {
            let out = super::api::put_setup(&client, &serde_json::json!({ "action": "confirm" }));
            let _ = waker.post(move || match out {
                Ok(_) => done.set(true),
                Err(e) => error.set(e),
            });
        });
    }
}

fn apply_session(
    questions: &Signal<Vec<Question>>,
    skipped: &Signal<Vec<String>>,
    draft_keys: &Signal<Vec<String>>,
    v: &serde_json::Value,
) {
    if let Some(arr) = v.get("questions").and_then(|q| q.as_array()) {
        let parsed: Vec<Question> = arr
            .iter()
            .filter_map(|q| {
                Some(Question {
                    id: q.get("id")?.as_str()?.to_string(),
                    title: q.get("title").and_then(|t| t.as_str()).unwrap_or("").to_string(),
                    prompt: q.get("prompt").and_then(|t| t.as_str()).unwrap_or("").to_string(),
                })
            })
            .filter(|q| !q.id.is_empty())
            .collect();
        if !parsed.is_empty() {
            questions.set(parsed);
        }
    }
    if let Some(arr) = v.get("skipped").and_then(|s| s.as_array()) {
        skipped.set(
            arr.iter()
                .filter_map(|s| s.as_str().map(|x| x.to_string()))
                .collect(),
        );
    }
    if let Some(draft) = v.get("draft").and_then(|d| d.as_object()) {
        draft_keys.set(draft.keys().cloned().collect());
    }
}

pub fn conversation_view(ui: &Ui, talk: Conversation) -> El {
    talk.ensure_started();
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let header = w::row(ui)
        .align(Align::Start)
        .justify(Justify::SpaceBetween)
        .gap(unit * 2.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(
            w::column(ui)
                .gap(unit * 0.5)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                })
                .child(w::text(ui, "House rules with Remedy").variant("heading"))
                .child(chrome::sub(
                    ui,
                    "A short, skippable conversation. Traditional setup is always right here.",
                )),
        )
        .child(
            w::row(ui).gap(unit).align(Align::Center).child(chrome::quiet(
                ui,
                "Traditional setup",
                {
                    let talk = talk.clone();
                    move || talk.traditional.set(true)
                },
            )).child(chrome::quiet(ui, "Skip all, use defaults", {
                let talk = talk.clone();
                move || talk.skip_all()
            })),
        );
    let ui_fail = ui.clone();
    let failure = chrome::when(
        ui,
        {
            let f = talk.failure.clone();
            move || !f.get().trim().is_empty()
        },
        {
            let f = talk.failure.clone();
            move || {
                w::text(&ui_fail, "")
                    .variant("muted")
                    .text(move || {
                        format!(
                            "The mind couldn’t start. {} You can keep going in Traditional setup, and retry the mind anytime.",
                            f.get()
                        )
                    })
            }
        },
    );
    let topics = topic_rail(ui, talk.clone());
    let chat = chat_column(ui, talk.clone());
    let body = w::row(ui)
        .gap(unit * 2.0)
        .align(Align::Start)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(topics)
        .child(chat);
    let frame = w::column(ui)
        .gap(unit * 2.0)
        .style(StylePatch::new().background(colors.bg))
        .padding(remedy_ui::core::Insets::all(unit * 3.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(header)
        .child(failure)
        .child(body);
    frame
}

fn topic_rail(ui: &Ui, talk: Conversation) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let head = w::text(ui, "Topics").variant("strong");
    let list = w::column(ui).gap(unit * 0.6);
    let ui_rows = ui.clone();
    w::for_each(
        &list,
        talk.questions.clone(),
        |q| q.id.clone(),
        move |q| {
            let skipped = talk.skipped.clone();
            let draft = talk.draft_keys.clone();
            let id = q.id.clone();
            let title = q.title.clone();
            w::row(&ui_rows)
                .justify(Justify::SpaceBetween)
                .child(w::text(&ui_rows, title).variant("muted"))
                .child(w::text(&ui_rows, "").variant("muted").text(move || {
                    let status = model::topic_status(&id, &skipped.get(), &draft.get());
                    model::topic_status_label(status).to_string()
                }))
        },
    );
    w::column(ui)
        .gap(unit)
        .padding(remedy_ui::core::Insets::all(unit * 2.0))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .radius(ui.theme().radii.medium),
        )
        .layout(|l| l.width = Length::Px(220.0))
        .child(head)
        .child(list)
}

fn chat_column(ui: &Ui, talk: Conversation) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let log_parent = w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    let ui_bubbles = ui.clone();
    let radius = ui.theme().radii.medium;
    w::for_each(
        &log_parent,
        talk.bubbles.clone(),
        |b| b.id.clone(),
        move |b| {
            let mine = b.role == "user";
            let bg = if mine { colors.accent.mix_oklab(colors.bg, 0.8) } else { colors.card };
            w::text(&ui_bubbles, b.text)
                .padding(remedy_ui::core::Insets::all(unit * 1.5))
                .style(
                    StylePatch::new()
                        .background(bg)
                        .radius(radius)
                        .foreground(colors.text),
                )
        },
    );
    let log = w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(160.0);
        })
        .child(log_parent);
    let field = w::text_input(ui, "Reply to Remedy")
        .placeholder("Type a reply, or “skip” / “skip all”");
    let f2 = field.clone();
    let input = talk.input.clone();
    let field = field.on_change({
        let input = input.clone();
        move || input.set(f2.value())
    });
    let f3 = field.clone();
    let talk_submit = talk.clone();
    let field = field.on_submit(move || {
        let text = f3.value();
        f3.set_value("");
        talk_submit.send_text(text);
    });
    let send = chrome::primary(ui, "Send", {
        let talk = talk.clone();
        let field = field.clone();
        move || {
            let text = field.value();
            field.set_value("");
            talk.send_text(text);
        }
    });
    let send2 = send.clone();
    let input2 = input.clone();
    let busy = talk.busy.clone();
    send.bind(move || {
        let _ = send2.disabled(busy.get() || input2.get().trim().is_empty());
    });
    let form = w::row(ui)
        .gap(unit)
        .align(Align::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(field.el().layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
        }))
        .child(send);
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(log)
        .child(chrome::error_text(ui, talk.error.clone()))
        .child(form)
        .child(chrome::actions(
            ui,
            vec![
                chrome::quiet(ui, "Skip this one", {
                    let talk = talk.clone();
                    move || talk.send_text("skip".into())
                }),
                chrome::primary(ui, "Looks good — use these", {
                    let talk = talk.clone();
                    move || talk.confirm()
                }),
            ],
        ))
}
