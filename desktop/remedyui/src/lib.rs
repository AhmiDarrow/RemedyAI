//! Remedy Desktop: Grove is home, and Studio is one tap away.
//!
//! Visual language follows the current Tauri/React app, then goes a little
//! further (always-visible row actions, a 16 px Remedy Sans UI face). The
//! window talks to remedy-runtime over HTTP+SSE on 127.0.0.1:7400.

mod chat;
mod fonts;
mod grove;
mod logo;
mod runtime;
mod settings;
mod sidebar;
mod sse;
mod status;
mod studio;
mod time;
mod usage;
mod work;

pub use fonts::{DEFAULT_CODE_FONT, DEFAULT_UI_FONT, Fonts};
pub use runtime::Client;
pub use settings::{preview_appearance, preview_connect, preview_error, preview_general, preview_models};
pub use studio::Studio;
pub use usage::{UsageContext, UsageDashboard, UsagePanel, UsageWindow};

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem};
use remedy_ui::i18n::Messages;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::view::IntoProp;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, WindowSpec};

const SHOW: u32 = 1;
const QUIT: u32 = 2;
/// Matches the current app's 270 px session column.
pub(crate) const SIDEBAR_WIDTH: f32 = 270.0;
/// Visible 32 px hit target for tool buttons (New-session plus, close ×).
pub(crate) const TOOL: f32 = 32.0;
pub(crate) const GLYPH: f32 = 20.0;
/// Inset accent bar on the selected row; reserved on every row so close × stays lined up.
pub(crate) const SELECT_MARK: f32 = 3.0;

/// Opens the window and puts the app in the tray.
pub fn app(app: &App) {
    settings::bind_host(app);
    app.keep_running(true);
    let studio = Studio::new();
    let grove = grove::Grove::new(studio.clone(), true);
    if !runtime::spawn_disabled() {
        runtime::ensure_running();
        let client = runtime::Client::new(&runtime::origin(), runtime::load_token().as_deref());
        studio.attach_client(client.clone());
        grove.attach_client(client);
    }
    let a = app.clone();
    let win = app.open(
        WindowSpec::new("Remedy Desktop")
            .size(1180.0, 740.0)
            .hide_on_close()
            .persist_geometry("main"),
        move |ui, window| {
            let (u, win, a) = (ui.clone(), window.clone(), a.clone());
            let grove_ui = grove.clone();
            let hidden = std::rc::Rc::new(std::cell::Cell::new(0u64));
            // Window scope, not a Grove/Studio `show` branch. Those branches
            // are disposed on the way between surfaces; Settings has to outlive both.
            settings::install(ui, studio.clone());
            ui.root()
                .child(grove::root(ui, grove.clone(), Some(a.clone())))
                .bind(move || {
                    let m = messages();
                    win.set_title(m.text(&u, "app-title"));
                    let item = |id, label: String| TrayItem::Item {
                        id,
                        label,
                        enabled: true,
                        default: id == SHOW,
                    };
                    a.set_tray(Some(TrayIcon::new(
                        m.text(&u, "app-title"),
                        tray_icon(),
                        vec![
                            item(SHOW, m.text(&u, "tray-show")),
                            TrayItem::Separator,
                            item(QUIT, m.text(&u, "tray-quit")),
                        ],
                    )));
                    let n = grove_ui.hide_window.get();
                    if n != hidden.get() {
                        hidden.set(n);
                        if n != 0 {
                            win.set_visible(false);
                        }
                    }
                });
        },
    );
    let (w2, a2) = (win.clone(), app.clone());
    app.on_tray(move |e| match e {
        TrayEvent::Activate | TrayEvent::Menu(SHOW) => {
            w2.set_visible(true);
            w2.focus();
        }
        TrayEvent::Menu(QUIT) => a2.quit(),
        _ => {}
    });
    app.on_second_instance(move |_| {
        win.set_visible(true);
        win.focus();
    });
}

/// Empty-well preview (goldens and visual review).
pub fn home(ui: &Ui) -> El {
    shell(ui, Studio::preview())
}

/// Chat-feed preview with a finished turn, for side-by-side review.
pub fn home_chat(ui: &Ui) -> El {
    shell(ui, Studio::preview_chat())
}

/// Morning Grove with example plots, for goldens. It does not touch disk or the network.
pub fn grove_home(ui: &Ui) -> El {
    grove::preview_home(ui)
}

/// The Studio shell: session list, well, composer, status.
pub fn shell(ui: &Ui, studio: Studio) -> El {
    fonts::prepare_theme(ui, &Fonts::load());
    let panel = settings::install(ui, studio.clone());
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(
            w::row(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                })
                .child(sidebar::sidebar(ui, studio.clone()))
                .child(w::divider(ui, true))
                .child(chat::well(ui, studio.clone()))
                .child(settings::rail(ui, panel)),
        )
        .child(w::divider(ui, false))
        .child(status::status_bar(ui, studio))
        .on_key_capture(move |ev| settings::shell_chord(panel, ev))
        .on_key(move |ev| settings::shell_escape(panel, ev))
}

/// A square tool control: 32 px hit target, 20 px glyph, always visible.
pub(crate) fn tool_button(
    ui: &Ui,
    glyph: w::Icon,
    label: impl IntoProp<String>,
    on_press: impl Fn() + 'static,
) -> El {
    tool_button_sized(ui, glyph, label, on_press, TOOL, GLYPH)
}

/// A tool control at a chosen size (row actions use 28 px so titles still fit).
pub(crate) fn tool_button_sized(
    ui: &Ui,
    glyph: w::Icon,
    label: impl IntoProp<String>,
    on_press: impl Fn() + 'static,
    size: f32,
    glyph_size: f32,
) -> El {
    let label = label.into_prop();
    let colors = ui.theme().colors.clone();
    let b = ui
        .el("icon_button")
        .child(w::icon(ui, glyph, glyph_size))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.min_width = Length::Px(size);
            l.min_height = Length::Px(size);
            l.width = Length::Px(size);
            l.height = Length::Px(size);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .foreground(colors.text)
                .radius(ui.theme().radii.small),
        )
        .on_click(on_press)
        .semantics(
            label
                .clone()
                .map(|l| Semantics::new(Role::Button).label(l).action(Action::Click)),
        );
    w::tooltip(&b, label);
    b
}

/// A 32 px tray mark: a solid forest square, not a soft blob.
pub fn tray_icon() -> TrayImage {
    let size = 32u32;
    let mut rgba = Vec::with_capacity((size * size * 4) as usize);
    for y in 0..size {
        for x in 0..size {
            let inset = 6u32;
            let on = x >= inset && x < size - inset && y >= inset && y < size - inset;
            if on {
                rgba.extend([61, 139, 95, 255]);
            } else {
                rgba.extend([0, 0, 0, 0]);
            }
        }
    }
    TrayImage { size, rgba }
}

/// The app's strings, from `i18n/<locale>.ftl`.
///
/// The catalog's revision signal lives in a detached scope. The first caller is
/// often a `show` branch (Grove's status line). That branch is disposed when the
/// owner switches to Studio, and a signal owned by it would die with the branch.
pub fn messages() -> Messages {
    thread_local!(static MESSAGES: Messages = {
        let (m, _scope) = remedy_ui::reactive::Scope::detached(|| {
            let m = Messages::new("en-US").embedded("en-US", include_str!("../i18n/en-US.ftl"));
            if cfg!(debug_assertions) {
                let m = m.folder(concat!(env!("CARGO_MANIFEST_DIR"), "/i18n"));
                m.watch();
                m
            } else {
                m
            }
        });
        m
    });
    MESSAGES.with(Clone::clone)
}

pub(crate) fn say(ui: &Ui, id: &'static str, variant: &'static str) -> El {
    let u = ui.clone();
    w::text(ui, "")
        .variant(variant)
        .text(move || messages().text(&u, id))
}
