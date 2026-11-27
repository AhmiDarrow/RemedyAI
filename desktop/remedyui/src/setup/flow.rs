//! First-run gate: splash, setup, the house-rules pop-up, the name prompt, and the update screen.
//!
//! When the gate is done, Grove is home and Studio is one tap away. The offer
//! and the name prompt sit on that window. `REMEDY_DESKTOP_NO_SPAWN` skips the
//! gate and opens Grove so the session tests still see today's shell.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length, Position};
use remedy_ui::reactive::{Signal, Waker};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};
use serde_json::{Value, json};

use super::api;
use super::chrome;
use super::conversation::{Conversation, conversation_view};
use super::downloads::{self, Downloads};
use super::mind::{Mind, mind_view};
use super::model::{self, Stage};
use super::name::{self, NamePrompt, name_overlay};
use super::offer::Offer;
use super::splash;
use super::update::{Updater, update_screen};
use super::wizard::{Wizard, wizard_screen};
use crate::fonts::{self, Fonts};
use crate::grove::Grove;
use crate::runtime::{self, Client};
use crate::studio::Studio;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Surface {
    Splash,
    Error,
    Setup,
    Update,
    Studio,
}

#[derive(Clone)]
struct Gate {
    surface: Signal<Surface>,
    splash: Signal<String>,
    error: Signal<String>,
    stage: Signal<Stage>,
    loaded: Signal<bool>,
    loading: Signal<bool>,
    revisit: Signal<bool>,
    failure: Signal<String>,
    user_name: Signal<String>,
    offer_checked: Signal<bool>,
    mind_armed: Signal<bool>,
    enter_studio: Signal<bool>,
    studio_attached: Signal<bool>,
    boot_epoch: Arc<AtomicU64>,
    slot: Signal<Option<Client>>,
    wizard: Wizard,
    talk: Conversation,
    mind: Mind,
    offer: Offer,
    name: NamePrompt,
    updater: Updater,
    downloads: Downloads,
    studio: Studio,
    grove: Grove,
    app: App,
}

impl Gate {
    fn new(studio: Studio, grove: Grove, app: App, client: Client) -> Self {
        let slot = Signal::new(Some(client));
        let failure = Signal::new(String::new());
        let mut wizard = Wizard::new(None);
        wizard.client = slot.clone();
        wizard.failure = failure.clone();
        let mut talk = Conversation::new(None, failure.clone());
        talk.client = slot.clone();
        let mut mind = Mind::new(None);
        mind.client = slot.clone();
        let mut name = NamePrompt::new(None);
        name.client = slot.clone();
        let downloads = Downloads::new(slot.clone());
        Self {
            surface: Signal::new(Surface::Splash),
            splash: Signal::new("starting".into()),
            error: Signal::new(String::new()),
            stage: Signal::new(Stage::Mind),
            loaded: Signal::new(false),
            loading: Signal::new(false),
            revisit: Signal::new(false),
            failure,
            user_name: Signal::new(String::new()),
            offer_checked: Signal::new(false),
            mind_armed: Signal::new(false),
            enter_studio: Signal::new(false),
            studio_attached: Signal::new(false),
            boot_epoch: Arc::new(AtomicU64::new(0)),
            slot,
            wizard,
            talk,
            mind,
            offer: Offer::closed(),
            name,
            updater: Updater::new(),
            downloads,
            studio,
            grove,
            app,
        }
    }

    fn start_boot(&self) {
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.error.set(model::SPLASH_TIMEOUT.into());
            self.surface.set(Surface::Error);
            return;
        };
        let ticket = self.boot_epoch.fetch_add(1, Ordering::SeqCst) + 1;
        let epoch = Arc::clone(&self.boot_epoch);
        let splash = self.splash.clone();
        let error = self.error.clone();
        let surface = self.surface.clone();
        let slot = self.slot.clone();
        let user_name = self.user_name.clone();
        let offer = self.offer.clone();
        let name = self.name.clone();
        let updater = self.updater.clone();
        let update_ready = self.downloads.update_ready.clone();
        let enter_studio = self.enter_studio.clone();
        let offer_checked = self.offer_checked.clone();
        let spawned = thread::Builder::new()
            .name("remedy-boot".into())
            .spawn(move || {
                let outcome = runtime::ensure_running();
                if matches!(
                    outcome,
                    runtime::EnsureOutcome::MissingBinary | runtime::EnsureOutcome::SpawnFailed
                ) {
                    let epoch_now = Arc::clone(&epoch);
                    let _ = waker.post(move || {
                        if epoch_now.load(Ordering::SeqCst) != ticket {
                            return;
                        }
                        splash.set("error".into());
                        error.set(model::SPLASH_FAILED.into());
                        surface.set(Surface::Error);
                    });
                    return;
                }
                let started_at = Instant::now();
                let mut attempts = 0u32;
                let mut up = false;
                while attempts < 45 {
                    if epoch.load(Ordering::SeqCst) != ticket {
                        return;
                    }
                    attempts += 1;
                    let label = if attempts <= 1 {
                        "starting"
                    } else {
                        "connecting"
                    };
                    let splash_now = splash.clone();
                    let epoch_now = Arc::clone(&epoch);
                    let _ = waker.post(move || {
                        if epoch_now.load(Ordering::SeqCst) == ticket {
                            splash_now.set(label.into());
                        }
                    });
                    if runtime::wait_until_up(
                        &runtime::origin(),
                        runtime::load_token().as_deref(),
                        1,
                    ) {
                        up = true;
                        break;
                    }
                    let shift = (attempts - 1).min(3);
                    let wait = 250u64.saturating_mul(1u64 << shift).min(2000);
                    thread::sleep(Duration::from_millis(wait));
                }
                if epoch.load(Ordering::SeqCst) != ticket {
                    return;
                }
                publish_update(&waker, &updater, &update_ready);
                if !up {
                    let epoch_now = Arc::clone(&epoch);
                    let _ = waker.post(move || {
                        if epoch_now.load(Ordering::SeqCst) != ticket {
                            return;
                        }
                        splash.set("error".into());
                        error.set(model::SPLASH_TIMEOUT.into());
                        surface.set(Surface::Error);
                    });
                    return;
                }
                let elapsed = started_at.elapsed();
                if elapsed < Duration::from_millis(350) {
                    thread::sleep(Duration::from_millis(350) - elapsed);
                }
                thread::sleep(Duration::from_millis(180));
                if epoch.load(Ordering::SeqCst) != ticket {
                    return;
                }
                let fresh = Client::new(&runtime::origin(), runtime::load_token().as_deref());
                let settings = api::get_json(&fresh, "settings");
                let setup_doc = api::get_json(&fresh, "setup").ok();
                let epoch_now = Arc::clone(&epoch);
                let _ = waker.post(move || {
                    if epoch_now.load(Ordering::SeqCst) != ticket {
                        return;
                    }
                    slot.set(Some(fresh));
                    splash.set("ready".into());
                    match settings {
                        Err(_) => {
                            error.set(model::SETTINGS_UNREACHABLE.into());
                            surface.set(Surface::Error);
                        }
                        Ok(v) => {
                            let flags = model::flags_from_settings(&v);
                            let name_s = model::user_name_from_settings(&v);
                            user_name.set(name_s.clone());
                            if model::needs_wizard(Some(&flags)) {
                                surface.set(Surface::Setup);
                            } else {
                                offer_checked.set(true);
                                reveal_offer_or_name(&offer, &name, &setup_doc, &name_s);
                                enter_studio.set(true);
                            }
                        }
                    }
                });
            });
        if spawned.is_err() {
            self.error.set(model::SPLASH_TIMEOUT.into());
            self.surface.set(Surface::Error);
        }
    }

    fn watch(&self) {
        if self.updater.quit.get() {
            self.updater.quit.set(false);
            self.app.quit();
            return;
        }
        if self.updater.close.get() {
            self.updater.close.set(false);
            self.updater.auto_start.set(false);
            if self.surface.get() == Surface::Update {
                self.show_studio();
            }
        }
        if self.enter_studio.get() {
            self.enter_studio.set(false);
            self.show_studio();
        }
        if self.offer.go.get() {
            self.offer.go.set(false);
            self.revisit.set(true);
            self.loaded.set(false);
            self.loading.set(true);
            self.failure.set(String::new());
            spawn_load(
                self.slot.clone(),
                self.stage.clone(),
                self.failure.clone(),
                self.loaded.clone(),
                self.loading.clone(),
                true,
                Some("update_offer_go".into()),
            );
        }
        if self.offer.later.get() {
            self.offer.later.set(false);
            spawn_put(self.slot.clone(), json!({ "action": "update_offer_later" }));
            if model::ask_user_name(
                false,
                false,
                self.revisit.get(),
                &self.user_name.get(),
                name::is_skipped(),
            ) {
                self.name.ask();
            }
        }
        if self.wizard.done.get() || self.talk.done.get() {
            self.wizard.done.set(false);
            self.talk.done.set(false);
            self.revisit.set(false);
            self.show_studio();
            self.spawn_after();
        }
        if self.mind.traditional.get() {
            self.mind.traditional.set(false);
            let reason = self.mind.error.get();
            if !reason.is_empty() {
                self.failure.set(reason);
            }
            self.stage.set(Stage::Traditional);
            spawn_put(self.slot.clone(), json!({ "mode": "traditional" }));
        }
        if self.talk.traditional.get() {
            self.talk.traditional.set(false);
            self.stage.set(Stage::Traditional);
            spawn_put(self.slot.clone(), json!({ "mode": "traditional" }));
        }
        if self.wizard.talk.get() {
            self.wizard.talk.set(false);
            let next = if self.mind.ready.get() {
                Stage::Conversation
            } else {
                Stage::Mind
            };
            self.stage.set(next);
            spawn_put(self.slot.clone(), json!({ "mode": "conversation" }));
        }
        if self.loaded.get() && self.stage.get() == Stage::Mind && self.mind.ready.get() {
            self.stage.set(Stage::Conversation);
        }
        if self.loaded.get() && self.stage.get() == Stage::Mind {
            if !self.mind_armed.get() {
                self.mind_armed.set(true);
                self.mind.kick();
            }
        } else if self.mind_armed.get() {
            self.mind_armed.set(false);
        }
        let want_setup = self.surface.get() == Surface::Setup || self.revisit.get();
        if want_setup && !self.loaded.get() && !self.loading.get() {
            self.loading.set(true);
            spawn_load(
                self.slot.clone(),
                self.stage.clone(),
                self.failure.clone(),
                self.loaded.clone(),
                self.loading.clone(),
                self.revisit.get(),
                None,
            );
        }
        if self.surface.get() == Surface::Studio {
            crate::chrome::show_home_switcher(Some(self.grove.clone()));
            if let Ok(waker) = remedy_ui::reactive::waker() {
                self.downloads.start(waker);
            }
        } else {
            crate::chrome::show_home_switcher(None);
        }
    }

    fn show_studio(&self) {
        if !self.studio_attached.get() {
            if let Some(client) = self.slot.get() {
                self.studio.attach_client(client.clone());
                self.grove.attach_client(client);
                self.studio_attached.set(true);
            }
        }
        if self.surface.get() != Surface::Studio {
            self.surface.set(Surface::Studio);
        }
    }

    fn open_update(&self) {
        // Do not block first-run or Grove with a modal. Download quietly;
        // the chrome ready notice offers Restart when the build is staged.
        crate::update::press_offer();
        self.show_studio();
    }

    fn retry(&self) {
        self.error.set(String::new());
        self.splash.set("starting".into());
        self.loaded.set(false);
        self.loading.set(false);
        self.surface.set(Surface::Splash);
        self.start_boot();
    }

    fn open_setup(&self) {
        self.slot.set(Some(Client::new(
            &runtime::origin(),
            runtime::load_token().as_deref(),
        )));
        self.error.set(String::new());
        self.loaded.set(false);
        self.loading.set(false);
        self.revisit.set(false);
        self.surface.set(Surface::Setup);
    }

    fn spawn_after(&self) {
        if self.offer_checked.get() {
            return;
        }
        self.offer_checked.set(true);
        let Some(client) = self.slot.get() else {
            return;
        };
        let Ok(waker) = remedy_ui::reactive::waker() else {
            return;
        };
        let user_name = self.user_name.clone();
        let offer = self.offer.clone();
        let name = self.name.clone();
        let offer_checked = self.offer_checked.clone();
        thread::Builder::new()
            .name("remedy-after-setup".into())
            .spawn(move || {
                let settings = api::get_json(&client, "settings").ok();
                let setup_doc = api::get_json(&client, "setup").ok();
                let _ = waker.post(move || {
                    let name_s = settings
                        .as_ref()
                        .map(model::user_name_from_settings)
                        .unwrap_or_default();
                    user_name.set(name_s.clone());
                    offer_checked.set(true);
                    reveal_offer_or_name(&offer, &name, &setup_doc, &name_s);
                });
            })
            .ok();
    }
}

fn reveal_offer_or_name(offer: &Offer, name: &NamePrompt, setup_doc: &Option<Value>, name_s: &str) {
    if let Some(doc) = setup_doc.as_ref().and_then(model::offer_from_session) {
        offer.show(doc);
    } else if model::ask_user_name(false, false, false, name_s, name::is_skipped()) {
        name.ask();
    }
}

fn publish_update(waker: &Waker, updater: &Updater, ready: &Signal<bool>) {
    if !crate::update::launch_check_allowed() {
        return;
    }
    let Ok(body) = api::fetch_latest_json() else {
        return;
    };
    let Some(info) = model::parse_latest(&body, model::platform_keys()) else {
        return;
    };
    if !model::is_newer(&info.version, model::shell_version()) {
        return;
    }
    let updater = updater.clone();
    let ready = ready.clone();
    let _ = waker.post(move || {
        updater.apply_release(info);
        ready.set(true);
    });
}

fn spawn_load(
    slot: Signal<Option<Client>>,
    stage: Signal<Stage>,
    failure: Signal<String>,
    loaded: Signal<bool>,
    loading: Signal<bool>,
    revisit: bool,
    action: Option<String>,
) {
    let Some(client) = slot.get() else {
        loaded.set(true);
        loading.set(false);
        stage.set(Stage::Mind);
        return;
    };
    let Ok(waker) = remedy_ui::reactive::waker() else {
        loaded.set(true);
        loading.set(false);
        stage.set(Stage::Mind);
        return;
    };
    let spawned = thread::Builder::new()
        .name("remedy-setup-load".into())
        .spawn(move || {
            if let Some(action) = action {
                let _ = api::put_setup(&client, &json!({ "action": action }));
            }
            let session = if revisit {
                api::get_json(&client, "setup").ok()
            } else {
                let current = api::get_json(&client, "setup").ok();
                let begun = api::put_setup(
                    &client,
                    &json!({ "action": "begin", "onboarding_pending": true }),
                )
                .ok();
                begun.or(current)
            };
            let (next, msg) = session
                .as_ref()
                .map(|v| model::stage_from_session(v, revisit))
                .unwrap_or((Stage::Mind, String::new()));
            let _ = waker.post(move || {
                if !msg.is_empty() {
                    failure.set(msg);
                }
                stage.set(next);
                loaded.set(true);
                loading.set(false);
            });
        });
    if spawned.is_err() {
        loaded.set(true);
        loading.set(false);
        stage.set(Stage::Mind);
    }
}

fn spawn_put(slot: Signal<Option<Client>>, body: Value) {
    let Some(client) = slot.get() else {
        return;
    };
    thread::Builder::new()
        .name("remedy-setup-put".into())
        .spawn(move || {
            let _ = api::put_setup(&client, &body);
        })
        .ok();
}

fn open_data_folder() {
    let path = runtime::remedy_home();
    let _ = std::fs::create_dir_all(&path);
    open_path(&path);
}

fn open_troubleshooting(error: Signal<String>) {
    if let Some(path) = find_manual() {
        open_path(&path);
        return;
    }
    let note = "See docs/manual/09-troubleshooting.md";
    let current = error.get();
    if !current.contains(note) {
        if current.trim().is_empty() {
            error.set(note.into());
        } else {
            error.set(format!("{current}\n\n{note}"));
        }
    }
}

fn find_manual() -> Option<PathBuf> {
    let rel = Path::new("docs")
        .join("manual")
        .join("09-troubleshooting.md");
    let mut starts = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }
    for start in starts {
        let mut dir = start;
        for _ in 0..8 {
            let candidate = dir.join(&rel);
            if candidate.is_file() {
                return Some(candidate);
            }
            if !dir.pop() {
                break;
            }
        }
    }
    None
}

fn open_path(path: &Path) {
    #[cfg(windows)]
    let mut cmd = Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = Command::new("xdg-open");
    cmd.arg(path);
    let _ = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

/// Live window. Tests call [`crate::app`] with spawn disabled and land on Grove.
pub fn host(ui: &Ui, studio: Studio, grove: Grove, app: App, client: Client) -> El {
    fonts::prepare_theme(ui, &Fonts::load());
    if runtime::spawn_disabled() {
        return crate::grove::root(ui, grove, Some(app), None);
    }
    let gate = Gate::new(studio, grove, app, client);
    gate.start_boot();
    let watching = gate.clone();
    let root = w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(surface(ui, gate));
    let root2 = root.clone();
    let root = root.bind(move || {
        watching.watch();
        let _ = &root2;
    });
    root
}

fn surface(ui: &Ui, gate: Gate) -> El {
    let splash_on = gate.surface.clone();
    let error_on = gate.surface.clone();
    let setup_on = gate.surface.clone();
    let update_on = gate.surface.clone();
    let studio_on = gate.surface.clone();
    let ui_splash = ui.clone();
    let ui_error = ui.clone();
    let ui_setup = ui.clone();
    let ui_update = ui.clone();
    let ui_studio = ui.clone();
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
        })
        .child(fill(show(
            ui,
            move || splash_on.get() == Surface::Splash,
            {
                let status = gate.splash.clone();
                move || splash::splash_view(&ui_splash, status.clone())
            },
        )))
        .child(fill(show(ui, move || error_on.get() == Surface::Error, {
            let gate = gate.clone();
            move || error_view(&ui_error, gate.clone())
        })))
        .child(fill(show(ui, move || setup_on.get() == Surface::Setup, {
            let gate = gate.clone();
            move || setup_screen(&ui_setup, gate.clone())
        })))
        .child(fill(show(
            ui,
            move || update_on.get() == Surface::Update,
            {
                let updater = gate.updater.clone();
                move || update_screen(&ui_update, updater.clone())
            },
        )))
        .child(fill(show(
            ui,
            move || studio_on.get() == Surface::Studio,
            {
                let gate = gate.clone();
                // Built again when the theme changes: its surfaces copy the colours.
                move || {
                    let gate = gate.clone();
                    crate::settings::with_look(&ui_studio, true, move |ui| studio_layer(ui, gate.clone()))
                }
            },
        )))
}

fn fill(layer: remedy_ui::view::Show) -> El {
    layer.el().layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
        l.grow = 1.0;
    })
}

fn setup_screen(ui: &Ui, gate: Gate) -> El {
    let loaded = gate.loaded.clone();
    let loaded_mind = gate.loaded.clone();
    let loaded_talk = gate.loaded.clone();
    let loaded_wiz = gate.loaded.clone();
    let stage_mind = gate.stage.clone();
    let stage_talk = gate.stage.clone();
    let stage_wiz = gate.stage.clone();
    let ui_load = ui.clone();
    let ui_mind = ui.clone();
    let ui_talk = ui.clone();
    let ui_wiz = ui.clone();
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(
            show(
                ui,
                move || !loaded.get(),
                move || {
                    chrome::center(
                        &ui_load,
                        w::text(&ui_load, "Loading setup…").variant("muted"),
                        420.0,
                    )
                },
            )
            .el(),
        )
        .child(
            show(
                ui,
                move || loaded_mind.get() && stage_mind.get() == Stage::Mind,
                {
                    let mind = gate.mind.clone();
                    move || mind_view(&ui_mind, mind.clone())
                },
            )
            .el(),
        )
        .child(
            show(
                ui,
                move || loaded_talk.get() && stage_talk.get() == Stage::Conversation,
                {
                    let talk = gate.talk.clone();
                    move || conversation_view(&ui_talk, talk.clone())
                },
            )
            .el(),
        )
        .child(
            show(
                ui,
                move || loaded_wiz.get() && stage_wiz.get() == Stage::Traditional,
                {
                    let wizard = gate.wizard.clone();
                    move || wizard_screen(&ui_wiz, wizard.clone())
                },
            )
            .el(),
        )
}

fn studio_layer(ui: &Ui, gate: Gate) -> El {
    let on_update = {
        let gate = gate.clone();
        move || gate.open_update()
    };
    let revisit_on = gate.revisit.clone();
    let gate_setup = gate.clone();
    let ui_revisit = ui.clone();
    // Grove stays in the document flow so the page, composer, and status bar
    // get a real height. The old `w::stack` placed every child absolutely, and
    // the house-rules `show` overlay then collapsed the page to zero height:
    // the banner clipped off the top of the window, the main area was empty,
    // and only a sliver of the composer remained.
    let grove = crate::grove::root(
        ui,
        gate.grove.clone(),
        Some(gate.app.clone()),
        Some(gate.offer.clone()),
    )
    .layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
    });
    let name = name_overlay(ui, gate.name.clone()).layout(|l| {
        l.position = Position::Absolute;
        l.inset = Insets::ZERO;
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    });
    let revisit = show(
        ui,
        move || revisit_on.get(),
        move || setup_screen(&ui_revisit, gate_setup.clone()),
    )
    .el()
    .layout(|l| {
        l.position = Position::Absolute;
        l.inset = Insets::ZERO;
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    });
    let body = w::column(ui)
        .layout(|l| {
            l.position = Position::Relative;
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(grove)
        .child(name)
        .child(revisit);
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(downloads::top_strip(ui, gate.downloads.clone(), on_update))
        .child(body)
}

fn error_view(ui: &Ui, gate: Gate) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let message = gate.error.clone();
    let retry = chrome::primary(ui, "Retry", {
        let gate = gate.clone();
        move || gate.retry()
    });
    let setup = chrome::quiet(ui, "Open setup", {
        let gate = gate.clone();
        move || gate.open_setup()
    });
    let folder = chrome::quiet(ui, "Open data folder", move || open_data_folder());
    let help = chrome::quiet(ui, "Troubleshooting help", {
        let error = gate.error.clone();
        move || open_troubleshooting(error.clone())
    });
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit * 2.0)
        .padding(remedy_ui::core::Insets::all(unit * 3.0))
        .style(StylePatch_bg(ui))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(
            w::text(ui, "")
                .variant("strong")
                .text(move || {
                    let msg = message.get();
                    if msg.trim().is_empty() {
                        "Server connection failed".into()
                    } else {
                        msg
                    }
                })
                .style(StylePatch_danger(colors.danger))
                .layout(|l| l.max_width = Length::Px(520.0)),
        )
        .child(
            w::text(
                ui,
                "The Remedy server could not start or respond. On a fresh install, try Retry (starts the local API), then complete setup.",
            )
            .variant("muted")
            .layout(|l| l.max_width = Length::Px(440.0)),
        )
        .child(
            w::wrap(ui)
                .gap(unit * 1.5)
                .justify(Justify::Center)
                .child(retry)
                .child(setup)
                .child(folder)
                .child(help),
        )
}

fn StylePatch_bg(ui: &Ui) -> remedy_ui::style::StylePatch {
    remedy_ui::style::StylePatch::new().background(ui.theme().colors.bg)
}

fn StylePatch_danger(color: remedy_ui::core::Color) -> remedy_ui::style::StylePatch {
    remedy_ui::style::StylePatch::new().foreground(color)
}
