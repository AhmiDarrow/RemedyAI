//! Last install step: get the local mind ready, or switch to Traditional setup.

use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;
use super::model::{self, MIND_SUBTITLE, MIND_TITLE};
use crate::logo;
use crate::runtime::Client;

#[derive(Clone)]
pub struct Mind {
    pub phase: Signal<String>,
    pub percent: Signal<Option<f32>>,
    pub error: Signal<String>,
    pub traditional: Signal<bool>,
    pub ready: Signal<bool>,
    pub client: Option<Client>,
}

impl Mind {
    pub fn preview() -> Self {
        Self {
            phase: Signal::new("preparing".into()),
            percent: Signal::new(None),
            error: Signal::new(String::new()),
            traditional: Signal::new(false),
            ready: Signal::new(false),
            client: None,
        }
    }

    /// Poll only when a client exists. Previews stay on "preparing".
    pub fn kick(&self) {
        let Some(client) = self.client.clone() else {
            return;
        };
        let phase = self.phase.clone();
        let percent = self.percent.clone();
        let error = self.error.clone();
        let ready = self.ready.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            return;
        };
        std::thread::Builder::new()
            .name("remedy-mind".into())
            .spawn(move || {
                let _ = super::api::vision_install(&client);
                for _ in 0..40 {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                    let status = super::api::vision_status(&client).ok();
                    let setup = super::api::get_json(&client, "setup").ok();
                    let phase_s = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/phase"))
                        .and_then(|p| p.as_str())
                        .unwrap_or("preparing")
                        .to_string();
                    let pct = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/bytes_total"))
                        .and_then(|n| n.as_f64())
                        .filter(|t| *t > 0.0)
                        .and_then(|total| {
                            let done = status
                                .as_ref()
                                .and_then(|v| v.pointer("/progress/bytes_done"))
                                .and_then(|n| n.as_f64())
                                .unwrap_or(0.0);
                            Some((done / total).clamp(0.0, 1.0) as f32)
                        });
                    let mind_ready = setup
                        .as_ref()
                        .and_then(|v| v.get("mind_ready"))
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false);
                    let failed = setup
                        .as_ref()
                        .and_then(|v| v.get("mind_failure"))
                        .cloned();
                    let phase2 = phase.clone();
                    let percent2 = percent.clone();
                    let error2 = error.clone();
                    let ready2 = ready.clone();
                    let waker2 = waker;
                    let _ = waker.post(move || {
                        phase2.set(phase_s);
                        percent2.set(pct);
                        if mind_ready || phase2.get() == "ready" {
                            ready2.set(true);
                        }
                        if let Some(f) = failed {
                            let reason = f.get("reason").and_then(|r| r.as_str()).unwrap_or("");
                            let detail = f.get("detail").and_then(|r| r.as_str()).unwrap_or("");
                            if !reason.is_empty() || !detail.is_empty() {
                                error2.set(model::mind_failure_message(reason, detail));
                            }
                        }
                    });
                    if mind_ready {
                        break;
                    }
                    let _ = waker2;
                }
            })
            .ok();
    }

    pub fn retry(&self) {
        self.error.set(String::new());
        if let Some(client) = self.client.clone() {
            let c = client.clone();
            std::thread::spawn(move || {
                let _ = super::api::mind_retry(&c);
                let _ = super::api::vision_install(&c);
            });
        }
        self.kick();
    }
}

pub fn mind_view(ui: &Ui, mind: Mind) -> El {
    let unit = ui.theme().spacing.unit;
    let phase = mind.phase.clone();
    let label = Signal::new(model::mind_phase_label(&phase.get()).to_string());
    let label2 = label.clone();
    let phase2 = phase.clone();
    let head = w::column(ui)
        .gap(unit)
        .align(remedy_ui::layout::Align::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(logo::wordmark(ui, 40.0))
        .child(chrome::heading(ui, MIND_TITLE))
        .child(chrome::sub(ui, MIND_SUBTITLE));
    let bar = w::progress(ui, "Mind download progress", mind.percent.clone());
    let status = w::text(ui, "").variant("muted").text(move || label2.get());
    let root = chrome::stack(
        ui,
        vec![
            head,
            bar,
            status,
            chrome::when(
                ui,
                {
                    let err = mind.error.clone();
                    move || !err.get().is_empty()
                },
                {
                    let mind = mind.clone();
                    move || error_block(ui, mind.clone())
                },
            ),
            chrome::when(
                ui,
                {
                    let err = mind.error.clone();
                    move || err.get().is_empty()
                },
                {
                    let mind = mind.clone();
                    move || {
                        chrome::quiet(ui, "Traditional setup", move || mind.traditional.set(true))
                    }
                },
            ),
        ],
    );
    let root2 = root.clone();
    root.bind(move || {
        label.set(model::mind_phase_label(&phase2.get()).into());
        let _ = &root2;
    });
    chrome::center(ui, root, 520.0)
}

fn error_block(ui: &Ui, mind: Mind) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .child(w::text(ui, "The mind couldn’t start").variant("strong"))
        .child(w::text(ui, "").variant("muted").text({
            let err = mind.error.clone();
            move || format!("{} We’ll open Traditional setup so you can keep going. You can retry the mind anytime.", err.get())
        }))
        .child(chrome::actions(
            ui,
            vec![
                chrome::primary(ui, "Retry", {
                    let mind = mind.clone();
                    move || mind.retry()
                }),
                chrome::quiet(ui, "Traditional setup", move || mind.traditional.set(true)),
            ],
        ))
}
