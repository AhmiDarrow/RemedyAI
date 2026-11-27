//! Last install step: get the local mind ready, or switch to Traditional setup.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

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
    pub bytes: Signal<String>,
    pub error: Signal<String>,
    pub traditional: Signal<bool>,
    pub ready: Signal<bool>,
    pub client: Signal<Option<Client>>,
    /// The owner said yes to the download. Nothing is fetched before that.
    pub asked: Signal<bool>,
    /// The download's size as the runtime reports it, for the question.
    pub size: Signal<String>,
    allow: Arc<std::sync::atomic::AtomicBool>,
    generation: Arc<AtomicU64>,
}

impl Mind {
    pub fn preview() -> Self {
        Self::new(None)
    }

    pub fn new(client: Option<Client>) -> Self {
        Self {
            phase: Signal::new("preparing".into()),
            percent: Signal::new(None),
            bytes: Signal::new(String::new()),
            error: Signal::new(String::new()),
            traditional: Signal::new(false),
            ready: Signal::new(false),
            client: Signal::new(client),
            asked: Signal::new(false),
            size: Signal::new(String::new()),
            allow: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Poll only when a client exists. Previews stay on "preparing".
    /// The owner's yes: the download may start.
    pub fn allow_download(&self) {
        self.allow.store(true, Ordering::SeqCst);
        self.asked.set(true);
    }

    pub fn kick(&self) {
        let Some(client) = self.client.get() else {
            return;
        };
        let ticket = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let generation = self.generation.clone();
        let phase = self.phase.clone();
        let percent = self.percent.clone();
        let bytes = self.bytes.clone();
        let error = self.error.clone();
        let ready = self.ready.clone();
        let allow = self.allow.clone();
        let size = self.size.clone();
        let asked = self.asked.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            return;
        };
        std::thread::Builder::new()
            .name("remedy-mind".into())
            .spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(45 * 60);
                loop {
                    if generation.load(Ordering::SeqCst) != ticket || std::time::Instant::now() > deadline {
                        break;
                    }
                    let status = super::api::get_json(&client, "vision/status?full=1").ok();
                    let setup = super::api::get_json(&client, "setup").ok();
                    let phase_s = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/phase"))
                        .and_then(|p| p.as_str())
                        .unwrap_or("preparing")
                        .to_string();
                    let downloading = matches!(
                        phase_s.as_str(),
                        "downloading" | "extracting" | "verifying" | "starting"
                    );
                    let done = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/bytes_done"))
                        .and_then(|n| n.as_f64())
                        .unwrap_or(0.0);
                    let total = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/bytes_total"))
                        .and_then(|n| n.as_f64())
                        .unwrap_or(0.0);
                    let pct = if total > 0.0 {
                        Some((done / total).clamp(0.0, 1.0) as f32)
                    } else {
                        None
                    };
                    let bytes_label = model::gb_pair(done, total);
                    let installed = status
                        .as_ref()
                        .and_then(|v| v.get("installed"))
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false);
                    let vis_ready = status
                        .as_ref()
                        .and_then(|v| v.get("ready"))
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false);
                    let running = status
                        .as_ref()
                        .and_then(|v| v.get("running"))
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false);
                    let progress_error = status
                        .as_ref()
                        .and_then(|v| v.pointer("/progress/error"))
                        .and_then(|e| e.as_str())
                        .unwrap_or("")
                        .to_string();
                    let failure = setup.as_ref().and_then(|v| v.get("mind_failure")).cloned();
                    let mind_ready = setup
                        .as_ref()
                        .and_then(|v| v.get("mind_ready"))
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false);
                    let hands_off = installed && (vis_ready || running) && progress_error.is_empty() && !downloading;
                    if hands_off && !running && !vis_ready {
                        let _ = super::api::vision_start(&client);
                    }
                    let finished = hands_off || mind_ready;
                    let phase2 = phase.clone();
                    let percent2 = percent.clone();
                    let bytes2 = bytes.clone();
                    let error2 = error.clone();
                    let ready2 = ready.clone();
                    let phase_for_ui = if finished { "ready".into() } else { phase_s.clone() };
                    let fail_msg = failure.as_ref().map(|f| {
                        let reason = f.get("reason").and_then(|r| r.as_str()).unwrap_or("");
                        let detail = f.get("detail").and_then(|r| r.as_str()).unwrap_or("");
                        model::mind_failure_message(reason, detail)
                    });
                    let progress_error2 = progress_error.clone();
                    let need = status
                        .as_ref()
                        .and_then(|v| {
                            v.pointer("/model/approx_download_bytes")
                                .or_else(|| v.pointer("/approx_download_bytes"))
                        })
                        .and_then(|n| n.as_f64())
                        .filter(|n| *n > 0.0)
                        .map(|n| format!("{:.1} GB", n / 1_073_741_824.0))
                        .unwrap_or_default();
                    let size2 = size.clone();
                    let asked2 = asked.clone();
                    let _ = waker.post(move || {
                        size2.set_if_changed(need);
                        // A download already under way, or a model already here, needs no question.
                        if downloading || installed {
                            asked2.set_if_changed(true);
                        }
                        phase2.set(phase_for_ui);
                        percent2.set(pct);
                        bytes2.set(bytes_label);
                        if finished {
                            ready2.set(true);
                        } else if !downloading {
                            if let Some(msg) = fail_msg.filter(|m| !m.is_empty()) {
                                error2.set(msg);
                            } else if !progress_error2.is_empty() {
                                error2.set(progress_error2);
                            }
                        }
                    });
                    if finished {
                        break;
                    }
                    if !installed && !downloading && progress_error.is_empty() && allow.load(Ordering::SeqCst) {
                        let _ = super::api::vision_install(&client);
                    }
                    std::thread::sleep(Duration::from_millis(1500));
                }
            })
            .ok();
    }

    pub fn retry(&self) {
        self.error.set(String::new());
        if let Some(client) = self.client.get() {
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
    let label = Signal::new(model::mind_status_line(&phase.get(), &mind.bytes.get(), mind.percent.get()));
    let label2 = label.clone();
    let phase2 = phase.clone();
    let bytes2 = mind.bytes.clone();
    let pct2 = mind.percent.clone();
    let head = w::column(ui)
        .gap(unit)
        .align(remedy_ui::layout::Align::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(logo::wordmark(ui, 40.0))
        .child(chrome::heading(ui, MIND_TITLE))
        .child(chrome::sub(ui, MIND_SUBTITLE));
    let bar = w::progress(ui, "Mind download progress", mind.percent.clone());
    let status = w::text(ui, "").variant("muted").text(move || label2.get());
    let progress = w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(bar)
        .child(status);
    let root = chrome::stack(
        ui,
        vec![
            head,
            chrome::when(
                ui,
                {
                    let asked = mind.asked.clone();
                    move || !asked.get()
                },
                {
                    let mind = mind.clone();
                    let ui_ask = ui.clone();
                    move || ask_block(&ui_ask, mind.clone())
                },
            ),
            crate::settings::visible_when(progress, {
                let asked = mind.asked.clone();
                move || asked.get()
            }),
            chrome::when(
                ui,
                {
                    let err = mind.error.clone();
                    move || !err.get().is_empty()
                },
                {
                    let mind = mind.clone();
                    let ui_err = ui.clone();
                    move || error_block(&ui_err, mind.clone())
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
                    let ui_quiet = ui.clone();
                    move || {
                        chrome::quiet(&ui_quiet, "Traditional setup", move || mind.traditional.set(true))
                    }
                },
            ),
        ],
    );
    let root2 = root.clone();
    let root = root.bind(move || {
        label.set(model::mind_status_line(&phase2.get(), &bytes2.get(), pct2.get()));
        let _ = &root2;
    });
    chrome::center(ui, root, 520.0)
}

/// Nothing is downloaded until the owner says so here.
fn ask_block(ui: &Ui, mind: Mind) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .child(w::text(ui, "").variant("muted").text({
            let size = mind.size.clone();
            move || {
                let size = size.get();
                let amount = if size.is_empty() { "a large download".to_string() } else { format!("a {size} download") };
                format!(
                    "Her mind is a model that runs on this computer. It is {amount} from Hugging Face and GitHub. Nothing is fetched until you say so."
                )
            }
        }))
        .child(chrome::actions(
            ui,
            vec![chrome::primary(ui, "Download", {
                let mind = mind.clone();
                move || mind.allow_download()
            })],
        ))
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
