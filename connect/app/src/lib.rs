//! RemedyConnect: Grove on a phone paired with the owner's PC.
//!
//! The app is three things and nothing else: pair once with a code from the
//! PC, keep an encrypted tunnel to it, and show Grove through that tunnel so
//! the owner can talk with Remedy, see what she is doing, and answer when she
//! asks before acting. Remedy and everything she does stay on the PC.
//!
//! `cargo remedy package --target android` builds the APK (see remedy.toml).
//! `cargo run` opens the same app in a desktop window for working on it.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use remedy_connect_core::{
    pair, ClientConfig, ConnState, ConnectClient, FileKeyStore, KeyStore, LoopbackShim, ShimConfig,
    Via,
};
use remedy_desktop::phone::PhoneGrove;
use remedy_ui::core::Insets;
use remedy_ui::i18n::Messages;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{show, App, El, Ui, WindowSpec};

/// Which screen the app is on.
#[derive(Clone, Debug, PartialEq)]
enum Stage {
    /// Not paired: ask for a pairing code.
    Pair,
    /// Pairing or unpairing is in flight.
    Busy,
    /// Paired. Grove is open on the tunnel shim at this address.
    Live(String),
}

/// What the tunnel is doing, for the line above Grove.
#[derive(Clone, Copy, Debug, PartialEq)]
enum LinkStatus {
    Connecting,
    Connected(Via),
    Retrying,
}

fn status_of(state: &ConnState) -> LinkStatus {
    match state {
        ConnState::Connected { via } => LinkStatus::Connected(*via),
        ConnState::Failed { .. } => LinkStatus::Retrying,
        ConnState::Connecting { .. } | ConnState::Stopped => LinkStatus::Connecting,
    }
}

/// The address Grove's API client uses: the shim's port and its secret path.
/// (`web_url` is for a web page, and carries a query string.)
pub fn shim_origin(shim: &LoopbackShim) -> String {
    format!("http://127.0.0.1:{}/{}", shim.port(), shim.token())
}

/// The live tunnel and the shim Grove talks to.
struct Live {
    client: ConnectClient,
    shim: LoopbackShim,
}

/// Pairing and the connection. Cheap to clone; every clone is the same link.
#[derive(Clone)]
struct Link {
    store: Arc<FileKeyStore>,
    live: Arc<Mutex<Option<Live>>>,
    stage: Signal<Stage>,
    status: Signal<LinkStatus>,
    error: Signal<String>,
}

impl Link {
    fn new(file: PathBuf) -> Self {
        Link {
            store: Arc::new(FileKeyStore::new(file)),
            live: Arc::new(Mutex::new(None)),
            stage: Signal::new(Stage::Pair),
            status: Signal::new(LinkStatus::Connecting),
            error: Signal::new(String::new()),
        }
    }

    /// Opens the tunnel if this phone is paired. Call on the UI thread.
    fn start(&self) {
        match self.store.load_pairing() {
            Ok(Some(_)) => {}
            Ok(None) => {
                self.stage.set(Stage::Pair);
                return;
            }
            Err(e) => {
                self.error.set(e.to_string());
                self.stage.set(Stage::Pair);
                return;
            }
        }
        // Keep the PC's current away-from-home address as it reports it.
        let saver = self.store.clone();
        let config = ClientConfig {
            on_record: Some(Arc::new(move |record| {
                let _ = saver.save_pairing(record);
            })),
            ..ClientConfig::default()
        };
        let client = match ConnectClient::start_from_store(&*self.store, config) {
            Ok(c) => c,
            Err(e) => {
                self.error.set(e.to_string());
                self.stage.set(Stage::Pair);
                return;
            }
        };
        let shim = match LoopbackShim::start(client.shim_pipe(), ShimConfig::default()) {
            Ok(s) => s,
            Err(e) => {
                client.stop();
                self.error.set(e.to_string());
                self.stage.set(Stage::Pair);
                return;
            }
        };
        let origin = shim_origin(&shim);
        self.status.set(status_of(&client.state()));
        if let Ok(wake) = remedy_ui::reactive::waker() {
            let updates = client.subscribe();
            let status = self.status;
            std::thread::spawn(move || {
                for state in updates {
                    let next = status_of(&state);
                    if wake.post(move || status.set(next)).is_err() {
                        break;
                    }
                }
            });
        }
        if let Ok(mut slot) = self.live.lock() {
            *slot = Some(Live { client, shim });
        }
        self.error.set(String::new());
        self.stage.set(Stage::Live(origin));
    }

    /// Pairs with the PC that showed `code`, then opens the tunnel.
    fn pair(&self, code: String, name: String) {
        let Ok(wake) = remedy_ui::reactive::waker() else {
            return;
        };
        self.error.set(String::new());
        self.stage.set(Stage::Busy);
        let me = self.clone();
        std::thread::spawn(move || {
            let result = pair(&*me.store, &code, &name, &ClientConfig::default())
                .map(|_| ())
                .map_err(|e| e.to_string());
            let _ = wake.post(move || match result {
                Ok(()) => me.start(),
                Err(e) => {
                    me.error.set(e);
                    me.stage.set(Stage::Pair);
                }
            });
        });
    }

    /// Forgets the PC here, and asks the PC to forget this phone.
    fn unpair(&self) {
        let Ok(wake) = remedy_ui::reactive::waker() else {
            return;
        };
        self.stage.set(Stage::Busy);
        let live = self.live.lock().ok().and_then(|mut slot| slot.take());
        let me = self.clone();
        std::thread::spawn(move || {
            if let Some(live) = live {
                // Best effort: if the PC cannot be reached the owner can still
                // remove this phone from the PC's device list.
                if let Ok(Some(keys)) = me.store.load_device_keys() {
                    let target = format!("/api/connect/devices/{}/revoke", keys.device_id());
                    if live.client.wait_connected(Duration::from_secs(3)).is_ok() {
                        let _ = live.client.http("POST", &target, "", b"");
                    }
                }
                live.shim.stop();
                live.client.stop();
            }
            let cleared = me.store.clear_pairing().map_err(|e| e.to_string());
            let _ = wake.post(move || {
                if let Err(e) = cleared {
                    me.error.set(e);
                }
                me.stage.set(Stage::Pair);
            });
        });
    }
}

/// The app's own text, from `i18n/<locale>.ftl`, carried inside the app.
fn messages() -> Messages {
    // Detached: made inside a screen, the catalog would die with that screen (the pairing
    // screen goes away once paired) and the next lookup would panic.
    thread_local!(static MESSAGES: Messages = remedy_ui::reactive::Scope::detached(|| {
        Messages::new("en-US").embedded("en-US", include_str!("../i18n/en-US.ftl"))
    }).0);
    MESSAGES.with(Clone::clone)
}

fn tr(ui: &Ui, id: &'static str) -> String {
    messages().text(ui, id)
}

/// Opens the app's window (on Android, the activity's).
pub fn app(app: &App) {
    let data = app.data_dir().unwrap_or_else(std::env::temp_dir);
    let _ = std::fs::create_dir_all(&data);
    // Grove keeps its small prefs file under REMEDY_HOME, and must never try
    // to start a runtime of its own: Remedy lives on the PC.
    std::env::set_var("REMEDY_HOME", &data);
    std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1");
    let link = Link::new(data.join("connect.json"));
    let host = app.clone();
    app.open(WindowSpec::new("Remedy").size(412.0, 892.0), move |ui, _| {
        link.start();
        ui.root().child(shell(ui, link.clone(), Some(host.clone())));
    });
}

/// The whole app: the pairing screen, or the status line over Grove.
fn shell(ui: &Ui, link: Link, app: Option<App>) -> El {
    let colors = ui.theme().colors.clone();
    let page = w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(StylePatch::new().background(colors.bg).foreground(colors.text));
    // The engine's window root already keeps this clear of the status bar, the gesture
    // bar, a display cutout and the on-screen keyboard.

    let stage = link.stage;
    let (pair_ui, pair_link) = (ui.clone(), link.clone());
    let pairing = show(
        ui,
        move || !matches!(stage.get(), Stage::Live(_)),
        move || pair_screen(&pair_ui, pair_link.clone()),
    )
    .el()
    .layout(grow);

    // Grove is opened once per connection and kept while the app is live.
    let origins = Signal::new(Vec::<String>::new());
    let watch = ui.el("connect-origin").bind(move || {
        let next = match stage.get() {
            Stage::Live(origin) => vec![origin],
            _ => Vec::new(),
        };
        origins.set_if_changed(next);
    });
    // Only the region in use takes the screen: two growing regions would each get half,
    // and the pairing form would be cut off above the keyboard.
    let slot = w::column(ui);
    let slot_layout = slot.clone();
    let slot = slot.bind(move || {
        let live = matches!(stage.get(), Stage::Live(_));
        let _ = slot_layout.clone().layout(move |l| {
            grow(l);
            if !live {
                l.grow = 0.0;
            }
        });
    });
    let (live_ui, live_link) = (ui.clone(), link);
    w::for_each(&slot, origins, |origin: &String| origin.clone(), move |origin| {
        let grove = PhoneGrove::open(origin, true);
        w::column(&live_ui)
            .layout(grow)
            .child(link_bar(&live_ui, live_link.clone()))
            .child(grove.view(&live_ui, app.clone()).layout(grow))
    });

    page.child(watch.layout(|l| {
        l.width = Length::Px(0.0);
        l.height = Length::Px(0.0);
    }))
    .child(pairing)
    .child(slot)
}

fn grow(l: &mut remedy_ui::layout::LayoutStyle) {
    l.width = Length::Percent(1.0);
    l.grow = 1.0;
    l.shrink = 1.0;
    l.basis = Length::Px(0.0);
    l.min_height = Length::Px(0.0);
    l.min_width = Length::Px(0.0);
}

/// How the phone reaches the PC right now, and the way to unpair.
fn link_bar(ui: &Ui, link: Link) -> El {
    let unit = ui.theme().spacing.unit;
    let (status, text_ui) = (link.status, ui.clone());
    let line = w::text(ui, "").variant("muted").text(move || {
        let id = match status.get() {
            LinkStatus::Connecting => "link-connecting",
            LinkStatus::Retrying => "link-retrying",
            LinkStatus::Connected(Via::Tailscale) => "link-connected-tailscale",
            LinkStatus::Connected(Via::Lan) => "link-connected-lan",
            LinkStatus::Connected(Via::Direct) => "link-connected-direct",
            LinkStatus::Connected(_) => "link-connected",
        };
        tr(&text_ui, id)
    });
    let ask_ui = ui.clone();
    let unpair = w::button(ui, tr(ui, "link-unpair"), move || {
        let yes = link.clone();
        w::dialog(
            &ask_ui,
            tr(&ask_ui, "unpair-title"),
            tr(&ask_ui, "unpair-body"),
            vec![
                w::DialogButton::new(tr(&ask_ui, "unpair-cancel"), "quiet", || {}),
                w::DialogButton::new(tr(&ask_ui, "unpair-yes"), "danger_solid", move || yes.unpair()),
            ],
        );
    })
    .variant("quiet");
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit * 2.0)
        .padding(Insets { top: unit, end: unit * 3.0, bottom: unit, start: unit * 3.0 })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(line.layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(unpair)
}

/// The first screen: paste the code the PC shows.
fn pair_screen(ui: &Ui, link: Link) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let code = w::text_area(ui, tr(ui, "pair-code")).placeholder(tr(ui, "pair-code-hint"));
    let name = w::text_input(ui, tr(ui, "pair-name"));
    name.set_value(&tr(ui, "pair-name-default"));

    let (stage, error) = (link.stage, link.error);
    let (go_code, go_name, go_ui, go_link) = (code.clone(), name.clone(), ui.clone(), link);
    let go = w::button(ui, tr(ui, "pair-go"), move || {
        let text = go_code.value().trim().to_string();
        if text.is_empty() {
            error.set(tr(&go_ui, "pair-empty"));
            return;
        }
        let mut device = go_name.value().trim().to_string();
        if device.is_empty() {
            device = tr(&go_ui, "pair-name-default");
        }
        go_link.pair(text, device);
    })
    .variant("primary")
    .disabled(move || stage.get() == Stage::Busy);

    let busy_ui = ui.clone();
    let working = show(ui, move || stage.get() == Stage::Busy, move || {
        w::text(&busy_ui, tr(&busy_ui, "pair-working")).variant("muted")
    })
    .el();
    let (err_ui, danger) = (ui.clone(), colors.danger);
    let problem = show(ui, move || !error.get().is_empty(), move || {
        w::text(&err_ui, "")
            .text(move || error.get())
            .style(StylePatch::new().foreground(danger))
    })
    .el();

    let card = w::column(ui)
        .gap(unit * 3.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.max_width = Length::Px(520.0);
            l.min_width = Length::Px(0.0);
        })
        .child(w::heading(ui, 1, tr(ui, "pair-title")))
        .child(w::text(ui, tr(ui, "pair-steps")).variant("muted"))
        .child(code.el().layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(96.0);
        }))
        .child(name.el().layout(|l| l.width = Length::Percent(1.0)))
        .child(problem)
        .child(working)
        .child(go)
        .child(w::text(ui, tr(ui, "pair-note")).variant("muted"));

    w::scroll(ui)
        .layout(grow)
        .child(
            w::column(ui)
                .align(Align::Center)
                .padding(Insets::all(unit * 5.0))
                .layout(|l| l.width = Length::Percent(1.0))
                .child(card),
        )
}

/// The pairing screen on its own, for screenshots and tests.
pub fn preview_pair(ui: &Ui) -> El {
    let link = Link::new(std::env::temp_dir().join("remedy-connect-preview.json"));
    shell(ui, link, None)
}

/// Android starts the app here, in the activity's thread.
// Written by hand: at this engine pin `main!` is exported from the platform crate
// root, which an app cannot reach through `remedy_ui`.
#[cfg(target_os = "android")]
#[unsafe(no_mangle)]
fn android_main(activity: remedy_ui::android::AndroidApp) {
    remedy_ui::android::attach(activity);
    let config = remedy_ui::AppConfig::new("Remedy").edition(remedy_ui::Edition::E2026);
    if let Err(e) = remedy_ui::run(config, app) {
        remedy_ui::android::log(&format!("RemedyConnect: {e}"));
    }
}
