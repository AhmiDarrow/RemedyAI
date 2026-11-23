//! Remedy Desktop: Grove is home, and Studio is one tap away.
//!
//! Visual language follows the current Tauri/React app, then goes a little
//! further (Remedy Sans UI in the chrome, AhmiPrint in code). The window talks
//! to remedy-runtime over HTTP+SSE on 127.0.0.1:7400.

mod chat;
mod chrome;
mod depth;
mod diagnostics;
mod fonts;
mod grove;
mod help;
mod help_data;
mod help_view;
mod icons;
mod layout;
mod logo;
mod palette;
mod panels;
mod prefs;
mod rails;
mod runtime;
mod server_menu;
mod settings;
mod sidebar;
mod slides;
mod sse;
mod status;
mod studio;
mod time;
mod timetravel;
mod titlebar;
mod tokens;
mod tray_copy;
mod updates;
mod usage;
mod work;

pub use diagnostics::DiagnosticsPanel;
pub use fonts::{DEFAULT_CODE_FONT, DEFAULT_UI_FONT, Fonts};
pub use help::HelpPanel;
pub use runtime::Client;
pub use settings::{
    preview_appearance, preview_connect, preview_error, preview_general, preview_models,
};
pub use slides::{
    automations_preview, bind_host, files_preview, scratch_preview, set_browser_body,
    terminal_context_items, terminal_preview,
};
pub use studio::Studio;
pub use timetravel::{TimeTravel, preview as time_travel_preview};
pub use usage::{UsageContext, UsageDashboard, UsagePanel, UsageWindow};

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem};
use remedy_ui::i18n::Messages;
use remedy_ui::layout::{Align, Justify, LayoutStyle, Length, Position};
use remedy_ui::style::StylePatch;
use remedy_ui::view::IntoProp;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, WindowSpec};

use chrome::{Chrome, TRAY_ABOUT, TRAY_QUIT, TRAY_SETTINGS, TRAY_SHOW, TRAY_UPDATES};
pub(crate) use icons::{Glyph, glyph};
pub(crate) use tokens::{ROW_ACTIONS, ROW_GLYPH, ROW_TOOL, TOOL, TOOL_GLYPH};
/// Inset accent bar on the selected row; reserved on every row so close × stays lined up.
pub(crate) const SELECT_MARK: f32 = 3.0;

/// Opens the window and puts the app in the tray.
pub fn app(app: &App) {
    settings::bind_host(app);
    app.keep_running(true);
    let studio = Studio::new();
    let grove = grove::Grove::new(studio.clone(), true);
    let chrome = Chrome::load();
    if !runtime::spawn_disabled() {
        runtime::ensure_running();
        let client = runtime::Client::new(&runtime::origin(), runtime::load_token().as_deref());
        studio.attach_client(client.clone());
        grove.attach_client(client);
        chrome.watch_runtime(&studio);
    }
    let start_hidden = chrome.start_in_tray.get() && !runtime::spawn_disabled();
    let mut spec = WindowSpec::new("Remedy Desktop")
        .size(1180.0, 740.0)
        .frameless()
        .hide_on_close()
        .persist_geometry("main");
    if start_hidden {
        spec = spec.hidden();
    }
    let a = app.clone();
    let win = app.open(spec, move |ui, window| {
        chrome.attach(&a, window, ui);
        depth::set_host(a.clone(), window.clone());
        settings::install(ui, studio.clone());
        let tag = tray_copy::fluent_tag(&chrome.locale.get());
        ui.set_locale(&tag);
        a.set_locale(Some(tag));
        let body_grove = grove.clone();
        let body_app = a.clone();
        let (u, win, a) = (ui.clone(), window.clone(), a.clone());
        let grove_ui = grove.clone();
        let hidden = std::rc::Rc::new(std::cell::Cell::new(0u64));
        ui.root()
            .child(chrome::hosted(ui, studio.clone(), chrome, move |ui| {
                grove::root(ui, body_grove.clone(), Some(body_app.clone()))
            }))
            .bind(move || {
                let copy = tray_copy::labels(&chrome.locale.get(), &tray_copy::os_locale_name());
                let custom = chrome.tooltip.get();
                let tip = if custom.is_empty() {
                    copy.tooltip.to_string()
                } else {
                    custom
                };
                win.set_title(messages().text(&u, "app-title"));
                let item = |id, label: String| TrayItem::Item {
                    id,
                    label,
                    enabled: true,
                    default: id == TRAY_SHOW,
                };
                a.set_tray(Some(TrayIcon::new(
                    tip,
                    tray_icon(),
                    vec![
                        item(TRAY_SHOW, copy.show.to_string()),
                        item(TRAY_SETTINGS, copy.settings.to_string()),
                        item(TRAY_UPDATES, copy.updates.to_string()),
                        item(TRAY_ABOUT, copy.about.to_string()),
                        TrayItem::Separator,
                        item(TRAY_QUIT, copy.quit.to_string()),
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
    });
    let (w2, c2) = (win.clone(), chrome);
    app.on_tray(move |e| match e {
        TrayEvent::Activate | TrayEvent::Menu(TRAY_SHOW) => {
            w2.set_visible(true);
            w2.focus();
        }
        TrayEvent::Menu(TRAY_SETTINGS) => {
            w2.set_visible(true);
            w2.focus();
            c2.open_settings();
        }
        TrayEvent::Menu(TRAY_UPDATES) => {
            w2.set_visible(true);
            w2.focus();
            c2.begin_check();
        }
        TrayEvent::Menu(TRAY_ABOUT) => {
            w2.set_visible(true);
            w2.focus();
            c2.open(chrome::Overlay::About);
        }
        TrayEvent::Menu(TRAY_QUIT) => c2.request_quit(),
        _ => {}
    });
    app.on_second_instance(move |_| {
        win.set_visible(true);
        win.focus();
    });
}

/// Empty-well preview (goldens and visual review). Left rail open, right thin.
pub fn home(ui: &Ui) -> El {
    home_rails_left(ui)
}

/// Chat-feed preview with a finished turn, for side-by-side review.
pub fn home_chat(ui: &Ui) -> El {
    shell_with(
        ui,
        Studio::preview_chat(),
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Mid-turn chat: sealed bubbles already reacted to, only the last reply still live.
pub fn home_chat_live(ui: &Ui) -> El {
    shell_with(
        ui,
        Studio::preview_chat_live(),
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Diagnostics golden: server, host, hardware, and the hive inspector. No network.
pub fn diagnostics_preview(ui: &Ui) -> El {
    diagnostics::preview(ui)
}

/// Morning Grove with example plots, for goldens. It does not touch disk or the network.
pub fn grove_home(ui: &Ui) -> El {
    grove::preview_home(ui)
}

/// Command palette over the Studio preview.
pub fn home_palette(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::Palette, false)
}

/// Help wiki open on Overview, inside the chrome frame.
pub fn home_help(ui: &Ui) -> El {
    chrome::install_help(HelpPanel::preview());
    chrome::preview_frame(ui, chrome::Overlay::None, false)
}

/// Owner's manual, open on the overview. The panel on its own, for its golden.
pub fn help_home(ui: &Ui) -> El {
    HelpPanel::preview().view(ui)
}

/// About Remedy.
pub fn home_about(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::About, false)
}

/// Diagnostics over the chrome frame. The landed panel's sample, not the thin sheet.
pub fn home_diagnostics(ui: &Ui) -> El {
    chrome::install_diag(diagnostics::DiagnosticsPanel::sample());
    chrome::preview_frame(ui, chrome::Overlay::None, false)
}

/// Title-bar logo menu open.
pub fn home_menu(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::None, true)
}

/// Filled usage tab, for goldens. No runtime calls.
pub fn preview_usage(ui: &Ui) -> El {
    usage::UsagePanel::preview(ui)
}

/// Filled harness tab, for goldens. No runtime calls.
pub fn preview_usage_harness(ui: &Ui) -> El {
    usage::UsagePanel::preview_harness(ui)
}

/// Left rail open on Sessions, right rail a thin strip. Same shell as [`home`].
pub fn home_rails_left(ui: &Ui) -> El {
    shell_with(
        ui,
        Studio::preview(),
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Right rail open on Settings, left rail a thin strip.
pub fn home_rails_right(ui: &Ui) -> El {
    let layout = layout::WorkspaceLayout {
        left_rail: layout::RailMode::Thin,
        left_open: false,
        right: layout::SlideId::Settings,
        right_width: 300,
        right_rail: layout::RailMode::Open,
        right_open: true,
        ..layout::WorkspaceLayout::default()
    };
    shell_with(ui, Studio::preview(), rails::WorkspaceChrome::fixed(layout))
}

/// Both rails open: Sessions on the left, Settings on the right.
pub fn home_rails_dual(ui: &Ui) -> El {
    let layout = layout::WorkspaceLayout {
        right: layout::SlideId::Settings,
        right_width: 300,
        right_rail: layout::RailMode::Open,
        right_open: true,
        ..layout::WorkspaceLayout::default()
    };
    shell_with(ui, Studio::preview(), rails::WorkspaceChrome::fixed(layout))
}

/// The Studio shell: left rail, chat well, right rail, status.
pub fn shell(ui: &Ui, studio: Studio) -> El {
    shell_with(ui, studio, rails::WorkspaceChrome::load())
}

/// Studio shell for chrome goldens. A fixed layout, so a saved rail file cannot move the picture.
pub(crate) fn shell_fixed(ui: &Ui, studio: Studio) -> El {
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

fn shell_with(ui: &Ui, studio: Studio, chrome: rails::WorkspaceChrome) -> El {
    // Chat-depth workers hop back onto this Ui (audio, dialogs). Every shell
    // path, including rail previews, goes through here.
    depth::set_ui(ui.clone());
    fonts::prepare_theme(ui, &Fonts::load());
    // Hidden overlay keeper. A closed rail is not a child of this column.
    studio.time_travel.mount(ui, studio.clone());
    let workspace = slides::Workspace::for_studio(&studio);
    slides::install(workspace.clone());
    let follow_ws = workspace;
    let follow_studio = studio.clone();
    let panel = settings::install(ui, studio.clone());
    let toggle = chrome.clone();
    let dismiss = chrome.clone();
    let status_toggle = chrome.clone();
    let hooks = settings::bind_studio_hooks(
        move || toggle.toggle_settings(),
        move || rails::dismiss_settings(&dismiss),
    );
    let shell = w::column(ui)
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
                    l.min_height = Length::Px(0.0);
                })
                .child(rails::side(
                    ui,
                    chrome.clone(),
                    layout::Side::Left,
                    studio.clone(),
                ))
                .child(chat::well(ui, studio.clone()))
                .child(rails::side(
                    ui,
                    chrome.clone(),
                    layout::Side::Right,
                    studio.clone(),
                )),
        )
        .child(w::divider(ui, false))
        .child(status::status_bar(ui, studio, move || {
            status_toggle.toggle_settings()
        }))
        .child(rails::hosts(ui, chrome))
        .on_key_capture(move |ev| settings::shell_chord(panel, ev))
        .on_key(move |ev| settings::shell_escape(panel, ev))
        .on_removed(move || {
            settings::clear_studio_hooks(hooks);
            slides::uninstall();
        });
    follow_ws.follow(&shell, &follow_studio);
    shell
}

/// A square tool control: 32 px hit target, 20 px glyph, always visible.
pub(crate) fn tool_button(
    ui: &Ui,
    glyph: impl Into<Glyph>,
    label: impl IntoProp<String>,
    on_press: impl Fn() + 'static,
) -> El {
    tool_button_sized(ui, glyph, label, on_press, TOOL, TOOL_GLYPH)
}

/// A tool control at a chosen size (row actions use 28 px so titles still fit).
pub(crate) fn tool_button_sized(
    ui: &Ui,
    glyph: impl Into<Glyph>,
    label: impl IntoProp<String>,
    on_press: impl Fn() + 'static,
    size: f32,
    glyph_size: f32,
) -> El {
    let label = label.into_prop();
    let mark = glyph.into();
    let colors = ui.theme().colors.clone();
    let b = ui
        .el("icon_button")
        .child(crate::glyph(ui, mark, glyph_size))
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
                .foreground(crate::tokens::icon_fg(&colors))
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
            let m = Messages::new("en-US")
                .embedded("en-US", include_str!("../i18n/en-US.ftl"))
                .embedded("de", include_str!("../i18n/de.ftl"))
                .embedded("es", include_str!("../i18n/es.ftl"))
                .embedded("fr", include_str!("../i18n/fr.ftl"))
                .embedded("it", include_str!("../i18n/it.ftl"))
                .embedded("ja", include_str!("../i18n/ja.ftl"))
                .embedded("ko", include_str!("../i18n/ko.ftl"))
                .embedded("pl", include_str!("../i18n/pl.ftl"))
                .embedded("pt", include_str!("../i18n/pt.ftl"))
                .embedded("ru", include_str!("../i18n/ru.ftl"))
                .embedded("zh-Hans", include_str!("../i18n/zh-Hans.ftl"));
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

/// Card surface used by menus and dialogs.
pub(crate) fn surface_of(ui: &Ui) -> StylePatch {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or_else(|| colors.muted.with_alpha(0.35));
    StylePatch::new()
        .background(colors.panel.mix_oklab(colors.bg, 0.04))
        .foreground(colors.text)
        .border(line)
        .border_width(1.0)
        .radius(ui.theme().radii.medium)
}

/// Stretch an absolute child over its parent.
pub(crate) fn abs_fill(l: &mut LayoutStyle) {
    l.position = Position::Absolute;
    l.inset = remedy_ui::core::Insets::all(0.0);
    l.width = Length::Percent(1.0);
    l.height = Length::Percent(1.0);
}

pub(crate) fn say(ui: &Ui, id: &'static str, variant: &'static str) -> El {
    let u = ui.clone();
    w::text(ui, "")
        .variant(variant)
        .text(move || messages().text(&u, id))
}
