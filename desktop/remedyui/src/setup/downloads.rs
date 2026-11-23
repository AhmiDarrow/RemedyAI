//! Title-bar download strip, and the "a new version is ready" chip when nothing is downloading.

use std::rc::Rc;
use std::thread;
use std::time::Duration;

use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::{Signal, Waker};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::model::{self, DownloadJob};
use crate::runtime::Client;

#[derive(Clone)]
pub struct Downloads {
    pub jobs: Signal<Vec<DownloadJob>>,
    pub update_ready: Signal<bool>,
    client: Signal<Option<Client>>,
    started: Signal<bool>,
}

impl Downloads {
    pub fn new(client: Signal<Option<Client>>) -> Self {
        Self {
            jobs: Signal::new(Vec::new()),
            update_ready: Signal::new(false),
            client,
            started: Signal::new(false),
        }
    }

    /// Poll voice, vision, and Hugging Face only after the studio is up.
    pub fn start(&self, waker: Waker) {
        if self.started.get() {
            return;
        }
        // Signals are UI-thread only. Snapshot the client here; the poll itself stays off the UI.
        let Some(client) = self.client.get() else {
            return;
        };
        self.started.set(true);
        let jobs = self.jobs.clone();
        thread::Builder::new()
            .name("remedy-downloads".into())
            .spawn(move || {
                loop {
                    let voice = super::api::get_json(&client, "voice/status").ok();
                    let vision = super::api::get_json(&client, "vision/status").ok();
                    let hf = super::api::get_json(&client, "rmb/hf/progress").ok();
                    let next = model::collect_download_jobs(voice.as_ref(), vision.as_ref(), hf.as_ref());
                    let jobs = jobs.clone();
                    if waker.post(move || jobs.set(next)).is_err() {
                        break;
                    }
                    thread::sleep(Duration::from_secs(2));
                }
            })
            .ok();
    }
}

/// Sits above the studio. A running download hides the update chip.
pub fn top_strip(ui: &Ui, downloads: Downloads, on_update: impl Fn() + 'static) -> El {
    let jobs = downloads.jobs.clone();
    let ready = downloads.update_ready.clone();
    let on_update = Rc::new(on_update);
    let show_jobs = jobs.clone();
    let show_ready = ready.clone();
    let ui_strip = ui.clone();
    show(
        ui,
        move || !show_jobs.get().is_empty() || show_ready.get(),
        move || {
            let jobs_bar = jobs.clone();
            let jobs_chip = jobs.clone();
            let ready_chip = ready.clone();
            let on_update = on_update.clone();
            let ui_bar = ui_strip.clone();
            let ui_chip = ui_strip.clone();
            w::column(&ui_strip)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(show(&ui_strip, move || !jobs_bar.get().is_empty(), {
                    let jobs = jobs.clone();
                    move || bar(&ui_bar, jobs.clone())
                }).el())
                .child(show(
                    &ui_strip,
                    move || jobs_chip.get().is_empty() && ready_chip.get(),
                    move || {
                        let on_update = on_update.clone();
                        w::button(&ui_chip, "A new version is ready", move || on_update())
                            .variant("quiet")
                            .layout(|l| l.width = Length::Percent(1.0))
                    },
                ).el())
        },
    )
    .el()
}

fn bar(ui: &Ui, jobs: Signal<Vec<DownloadJob>>) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let fraction = Signal::new(known_fraction(&jobs.get()));
    let fraction_live = fraction.clone();
    let jobs_live = jobs.clone();
    let caption = jobs.clone();
    let root = w::column(ui)
        .gap(unit * 0.35)
        .align(Align::Center)
        .padding(remedy_ui::core::Insets::symmetric(unit * 0.5, unit * 1.5))
        .style(StylePatch::new().background(colors.panel))
        .layout(|l| l.width = Length::Percent(1.0))
        .child(
            w::progress(ui, "Download", fraction)
                .style(StylePatch::new().foreground(colors.accent))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.height = Length::Px(7.0);
                }),
        )
        .child(
            w::text(ui, "")
                .variant("muted")
                .text(move || model::download_caption(&caption.get())),
        );
    let root2 = root.clone();
    root.bind(move || {
        fraction_live.set(known_fraction(&jobs_live.get()));
        let _ = &root2;
    });
    root
}

fn known_fraction(jobs: &[DownloadJob]) -> Option<f32> {
    let primary = jobs.first()?;
    if model::titlebar_known(primary.percent) {
        primary.percent.map(|p| p as f32 / 100.0)
    } else {
        None
    }
}
