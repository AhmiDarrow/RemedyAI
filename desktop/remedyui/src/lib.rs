//! Remedy Desktop: Grove is home, and Studio is one tap away.
//!
//! Visual language follows the owner's own craft: flat dark forest panels, hairline
//! borders, and three config-driven font roles (UI, display, code). The window talks
//! to remedy-runtime over HTTP+SSE on 127.0.0.1:7400.

mod browser;
mod chat;
mod chat_split;
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
mod model_picker;
mod palette;
pub mod phone;
mod panels;
mod plan_dock;
mod prefs;
mod presence;
mod presence_draw;
mod face_skin;
mod face_draw;
mod face_proc;
mod rails;
mod runtime;
mod server_menu;
mod settings;
mod setup;
mod design_setup;
mod sidebar;
mod slides;
mod sse;
mod status;
mod studio;
mod time;
mod timetravel;
mod titlebar;
mod theme_pack;
mod theme_import;
mod theme_custom;
mod tokens;
mod tray_copy;
mod update;
mod updates;
mod usage;
mod work;

pub use diagnostics::DiagnosticsPanel;
pub use fonts::{DEFAULT_CODE_FONT, DEFAULT_DISPLAY_FONT, DEFAULT_UI_FONT, Fonts};
pub use help::HelpPanel;
pub use runtime::{Client, install_panic_hook};
pub use settings::{
    preview_appearance, preview_connect, preview_error, preview_general, preview_models,
    preview_models_provider,
};
pub use setup::{
    preview_conversation, preview_mind, preview_offer, preview_update, preview_wizard,
};
pub use slides::{
    automations_preview, bind_host, files_preview, scratch_preview, set_browser_body,
    terminal_context_items, terminal_preview,
};
pub use studio::Studio;
pub use model_picker::{parse_connected, PickerStatus, ProviderChoice};
pub use status::{compact_bar, status_bar};
pub use timetravel::{TimeTravel, preview as time_travel_preview};
pub use usage::{UsageContext, UsageDashboard, UsagePanel, UsageWindow};

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem, WindowIcon};
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
pub(crate) const SELECT_MARK: f32 = theme_pack::MECHA.layout.select_mark;

/// Opens the window and puts the app in the tray.
pub fn app(app: &App) {
    install_panic_hook();
    settings::bind_host(app);
    app.keep_running(true);
    update::spawn_startup_check(app);
    let studio = Studio::new();
    // Grove is home. On a real launch the setup gate sits in front of it
    // (splash, mind, house rules, wizard, update), inside the Studio chrome.
    // REMEDY_DESKTOP_NO_SPAWN keeps Grove, with Studio one tap away, and does
    // not start a sidecar.
    let grove = grove::Grove::new(studio.clone(), true);
    grove::bind_live(grove.clone());
    let chrome = Chrome::load();
    chrome.grove.set(Some(grove.clone()));
    let live = if runtime::spawn_disabled() {
        None
    } else {
        let _ = runtime::ensure_running();
        let client = runtime::Client::new(&runtime::origin(), runtime::load_token().as_deref());
        studio.attach_client(client.clone());
        grove.attach_client(client.clone());
        chrome.watch_runtime(&studio);
        Some(client)
    };
    let start_hidden = chrome.start_in_tray.get() && !runtime::spawn_disabled();
    let mut spec = WindowSpec::new("Remedy Desktop")
        .size(1180.0, 740.0)
        // The window draws its own frame, so below a certain size there is nothing
        // left to grab. A saved size smaller than this (a minimized window's 160 x 28
        // was once saved and restored) can then never come back.
        .min_size(MAIN_MIN_W, MAIN_MIN_H)
        .frameless()
        .hide_on_close()
        .persist_geometry("main")
        .icon(window_icon());
    if start_hidden {
        spec = spec.hidden();
    }
    let a = app.clone();
    let win = app.open(spec, move |ui, window| {
        // Belt and braces for a saved size the platform restored anyway.
        let (fix_ui, fix_win) = (ui.clone(), window.clone());
        let _ = ui.after(std::time::Duration::from_millis(400), move || {
            let size = fix_ui.window_size();
            if size.width > 0.0 && window_too_small(size.width, size.height) {
                fix_win.set_size(1180.0, 740.0);
                fix_win.center();
            }
        });
        chrome.attach(&a, window, ui);
        depth::set_host(a.clone(), window.clone());
        // The slides' save, open and folder dialogs need the window too.
        slides::bind_host(&a, window);
        // Window scope, not a Grove/Studio/setup `show` branch. Those
        // branches are disposed on the way between surfaces. Settings
        // has to outlive all of them. The computer-use poller starts
        // here too: Grove is the default surface, and the Studio rails
        // (where the Browser slide lives) are not mounted yet.
        let boot_studio = studio.clone();
        browser::bind_app(a.clone());
        settings::install(ui, studio.clone());
        theme_custom::attach(ui, &a);
        let tag = tray_copy::fluent_tag(&chrome.locale.get());
        ui.set_locale(&tag);
        a.set_locale(Some(tag));
        let body_grove = grove.clone();
        let body_app = a.clone();
        let body_studio = studio.clone();
        let body_live = live.clone();
        let (u, win, a) = (ui.clone(), window.clone(), a.clone());
        let grove_ui = grove.clone();
        let hidden = std::rc::Rc::new(std::cell::Cell::new(0u64));
        ui.root()
            .child(chrome::hosted(ui, studio.clone(), chrome, move |ui| {
                if let Some(client) = body_live.clone() {
                    setup::host(
                        ui,
                        body_studio.clone(),
                        body_grove.clone(),
                        body_app.clone(),
                        client,
                    )
                } else {
                    grove::root(ui, body_grove.clone(), Some(body_app.clone()), None)
                }
            }))
            .bind(move || {
                browser::boot(boot_studio.clone());
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
                // Download progress wins, then an offered version, then the
                // shell's check label. The signed install lives behind that item.
                let note = update::note_signal().try_get().unwrap_or_default();
                let offer = update::offer_signal().try_get().unwrap_or_default();
                let updates_label = if !note.is_empty() {
                    note
                } else if !offer.is_empty() {
                    format!("{} {offer}", copy.updates)
                } else {
                    copy.updates.to_string()
                };
                a.set_tray(Some(TrayIcon::new(
                    tip,
                    tray_icon(),
                    vec![
                        item(TRAY_SHOW, copy.show.to_string()),
                        item(TRAY_SETTINGS, copy.settings.to_string()),
                        item(TRAY_UPDATES, updates_label),
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
    win.set_icon(Some(window_icon()));
    let (w2, c2) = (win.clone(), chrome);
    let a_up = app.clone();
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
            update::on_update_menu(&a_up);
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


/// Window chrome around a design-review surface, with the Grove/Studio
/// switcher in the title bar. Does not replace golden `home()` previews.
fn close_design_settings() {
    if let Some(s) = settings::current() {
        settings::close_for_rail(s);
    }
}

fn design_chrome(ui: &Ui, studio: Studio, grove: grove::Grove, body: impl Fn(&Ui) -> El + 'static) -> El {
    fonts::prepare_theme(ui, &Fonts::load());
    let chrome = Chrome::preview(chrome::Overlay::None);
    chrome.grove.set(Some(grove));
    chrome::hosted(ui, studio, chrome, body)
}

/// Studio design shots: attach a preview Grove so the title-bar switcher
/// appears, and select Studio so the switcher reads correctly.
fn design_hosted(ui: &Ui, studio: Studio, body: impl Fn(&Ui) -> El + 'static) -> El {
    let grove = grove::Grove::preview(studio.clone());
    grove.show_studio();
    design_chrome(ui, studio, grove, body)
}

fn design_grove_body(ui: &Ui, grove: grove::Grove) -> El {
    grove::root(ui, grove, None, None).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.height = Length::Percent(1.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    })
}

/// Grove is the default home. Title-bar switcher with Grove selected.
pub fn design_home(ui: &Ui) -> El {
    design_home_kind(ui, presence::Kind::Square)
}

pub fn design_home_wolf(ui: &Ui) -> El {
    design_home_kind(ui, presence::Kind::Wolf)
}

pub fn design_home_bear(ui: &Ui) -> El {
    design_home_kind(ui, presence::Kind::Bear)
}

fn design_home_kind(ui: &Ui, kind: presence::Kind) -> El {
    presence::set_kind_override(Some(kind));
    presence::set_stage_override(Some(2));
    presence::set_hour_override(Some(10));
    close_design_settings();
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    studio.set_mode(studio::WorkMode::Chat);
    let grove = grove::Grove::preview(studio.clone());
    design_chrome(ui, studio, grove.clone(), move |ui| design_grove_body(ui, grove.clone()))
}

/// Grove after Remedy opened a Build session from a coding request.
pub fn design_grove_handoff(ui: &Ui) -> El {
    presence::set_kind_override(Some(presence::Kind::MorningStar));
    close_design_settings();
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    studio.set_mode(studio::WorkMode::Build);
    let grove = grove::Grove::preview(studio.clone());
    grove.handoff.set(Some(grove::StudioHandoff {
        kind: grove::StudioKind::Build,
        prompt: "Get the recipe addon loading again after the entry rename.".into(),
        session: None,
    }));
    design_chrome(ui, studio, grove.clone(), move |ui| design_grove_body(ui, grove.clone()))
}

/// Build-mode chat inside the window chrome (status-rail review).
pub fn design_status_rail(ui: &Ui) -> El {
    let studio = Studio::preview_chat();
    let st = studio.clone();
    design_hosted(ui, studio, move |ui| {
        shell_with(
            ui,
            st.clone(),
            rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
        )
    })
}

/// Empty-well preview (goldens and visual review). Left rail open, right thin.
pub fn home(ui: &Ui) -> El {
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Studio empty well with Remedy's presence turned off. Appearance defaults to on;
/// this is the hidden state for layout tests.
pub fn home_presence_off(ui: &Ui) -> El {
    presence::set_enabled_override(Some(false));
    home(ui)
}

/// Studio empty well with a tagged session, for sidebar tag tests (not a golden).
pub fn home_tagged(ui: &Ui) -> El {
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    studio.sessions.update(|list| {
        if let Some(session) = list.iter_mut().find(|s| s.id == "s1") {
            session.tags = vec!["recipes".into(), "ui".into()];
        }
    });
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Status bar showing another live session (not a golden).
pub fn home_siblings(ui: &Ui) -> El {
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    studio.depth.siblings.set(vec![depth::logic::SiblingBeacon {
        session_id: "s-other".into(),
        you: false,
        muscle: "xai/grok-4.5".into(),
        project: "recipes".into(),
        goal: "load the addon".into(),
        phase: "build".into(),
        held_files: vec!["main.rs".into()],
        held_count: 1,
    }]);
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
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

/// Design-review Build chat: a finished turn with a fenced code block.
pub fn design_build_chat(ui: &Ui) -> El {
    presence::set_kind_override(Some(presence::Kind::Square));
    presence::set_stage_override(Some(2));
    presence::set_hour_override(Some(10));
    close_design_settings();
    let studio = Studio::preview_chat();
    studio.mode.set(studio::WorkMode::Build);
    studio.messages.set(design_preview_messages());
    let st = studio.clone();
    design_hosted(ui, studio, move |ui| {
        shell_with(
            ui,
            st.clone(),
            rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
        )
    })
}

/// Design-review Plan mode with the dock open and a sample outline.
pub fn design_plan(ui: &Ui) -> El {
    let studio = Studio::preview_chat();
    studio.mode.set(studio::WorkMode::Plan);
    studio.depth.plan.set(Some(depth::PlanCard {
        status: "ready".into(),
        goal: "Get the recipe addon loading again after the entry rename.".into(),
        steps: vec![
            (
                "Patch manifest.json to init_main.lua".into(),
                "the last update renamed the entry".into(),
            ),
            (
                "Register the loader before the version check".into(),
                String::new(),
            ),
            ("Run recipes.exe --check --all".into(), String::new()),
        ],
        done: 1,
        total: 3,
    }));
    let st = studio.clone();
    design_hosted(ui, studio, move |ui| {
        shell_with(
            ui,
            st.clone(),
            rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
        )
    })
}

/// Design-review Settings rail open on the Studio shell.
pub fn design_settings(ui: &Ui) -> El {
    let studio = Studio::preview_chat();
    let layout = layout::layout_open_settings(&layout::WorkspaceLayout::default());
    let st = studio.clone();
    design_hosted(ui, studio, move |ui| {
        settings::install_preview(ui);
        shell_with(
            ui,
            st.clone(),
            rails::WorkspaceChrome::fixed(layout),
        )
    })
}

/// Design-review card for one presence state (static under reduced_motion).
pub fn design_presence(ui: &Ui, state: presence::State) -> El {
    presence::set_kind_override(Some(presence::Kind::MorningStar));
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    design_hosted(ui, studio, move |ui| {
        fonts::prepare_theme(ui, &Fonts::load());
        let colors = ui.theme().colors.clone();
        let size = theme_pack::layout().presence_review;
        let title = w::text(ui, state.label())
            .variant("heading")
            .style(tokens::typo(tokens::TITLE).foreground(colors.text));
        let body = w::text(ui, state.caption())
            .style(tokens::typo(tokens::BODY).foreground(colors.text))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        let panel = tokens::info_panel(ui, "Status", body).layout(|l| {
            l.max_width = Length::Px(320.0);
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
        w::column(ui)
            .align(Align::Center)
            .justify(Justify::Center)
            .gap(tokens::GUTTER)
            .padding(Insets::all(tokens::GUTTER * 2.0))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
                l.grow = 1.0;
                l.min_height = Length::Px(0.0);
            })
            .style(StylePatch::new().background(colors.bg).foreground(colors.text))
            .child(presence::preview(ui, state, size))
            .child(title)
            .child(panel)
    })
}

pub fn design_presence_idle(ui: &Ui) -> El {
    design_presence(ui, presence::State::Idle)
}
pub fn design_presence_listening(ui: &Ui) -> El {
    design_presence(ui, presence::State::Listening)
}
pub fn design_presence_thinking(ui: &Ui) -> El {
    design_presence(ui, presence::State::Thinking)
}
pub fn design_presence_working(ui: &Ui) -> El {
    design_presence(ui, presence::State::Working)
}
pub fn design_presence_done(ui: &Ui) -> El {
    design_presence(ui, presence::State::Done)
}
pub fn design_presence_error(ui: &Ui) -> El {
    design_presence(ui, presence::State::Error)
}

pub fn design_concept_star(ui: &Ui) -> El {
    design_presence_board(ui, presence::Kind::MorningStar)
}

pub fn design_concept_wolf(ui: &Ui) -> El {
    design_presence_board(ui, presence::Kind::Wolf)
}

pub fn design_concept_bear(ui: &Ui) -> El {
    design_presence_board(ui, presence::Kind::Bear)
}

/// All twelve moods of the default Square skin, at `size` px.
pub fn design_moods(ui: &Ui, size: f32) -> El {
    presence::set_kind_override(Some(presence::Kind::Square));
    presence::set_stage_override(Some(2));
    presence::set_hour_override(Some(10));
    close_design_settings();
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    design_hosted(ui, studio, move |ui| {
        fonts::prepare_theme(ui, &Fonts::load());
        let colors = ui.theme().colors.clone();
        let title = w::text(ui, format!("Square — moods at {}", size as i32))
            .style(tokens::typo(tokens::HERO).foreground(colors.text))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        let blurb = w::text(
            ui,
            "The default face. Neutral, living, and skinnable. Companion stage.",
        )
        .style(tokens::typo(tokens::BODY).foreground(colors.muted))
        .layout(|l| {
            l.max_width = Length::Px(720.0);
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
        let moods = presence::Mood::all();
        let mut col = w::column(ui)
            .align(Align::Start)
            .gap(tokens::GUTTER * 2.0)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        for chunk in moods.chunks(6) {
            let mut row = w::row(ui)
                .align(Align::Start)
                .justify(Justify::SpaceBetween)
                .gap(tokens::GUTTER)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                });
            for mood in chunk {
                let label = w::text(ui, mood.label())
                    .style(tokens::typo(tokens::META).foreground(colors.muted));
                let cell = w::column(ui)
                    .align(Align::Center)
                    .gap(tokens::SPACE * 2.0)
                    .child(presence::preview_stage(ui, *mood, size, 2))
                    .child(label);
                row = row.child(cell);
            }
            col = col.child(row);
        }
        w::column(ui)
            .align(Align::Start)
            .justify(Justify::Center)
            .gap(tokens::GUTTER)
            .padding(Insets::all(tokens::GUTTER * 2.0))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
                l.grow = 1.0;
                l.min_height = Length::Px(0.0);
            })
            .style(StylePatch::new().background(colors.bg).foreground(colors.text))
            .child(title)
            .child(blurb)
            .child(col)
    })
}

pub fn design_moods_72(ui: &Ui) -> El {
    design_moods(ui, 72.0)
}

pub fn design_moods_144(ui: &Ui) -> El {
    design_moods(ui, 144.0)
}

/// Grown faces: four seeds across three amounts of use, shown idle.
pub fn design_grown(ui: &Ui) -> El {
    presence::set_kind_override(Some(presence::Kind::Square));
    presence::set_hour_override(Some(10));
    close_design_settings();
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    design_hosted(ui, studio, move |ui| {
        fonts::prepare_theme(ui, &Fonts::load());
        let colors = ui.theme().colors.clone();
        let uses = [
            ("First week", face_skin::Facts { sessions: 3, messages: 20, days: 5, goals: 0 }),
            ("A few months", face_skin::Facts { sessions: 30, messages: 600, days: 90, goals: 2 }),
            ("Years", face_skin::Facts { sessions: 400, messages: 20000, days: 900, goals: 6 }),
        ];
        let mut col = w::column(ui)
            .gap(tokens::GUTTER)
            .padding(Insets::all(tokens::GUTTER * 2.0))
            .style(StylePatch::new().background(colors.bg).foreground(colors.text));
        for (label, facts) in uses {
            let mut row = w::row(ui).align(Align::Center).gap(tokens::GUTTER * 2.0).child(
                w::text(ui, label)
                    .style(tokens::typo(tokens::TITLE).foreground(colors.text))
                    .layout(|l| l.width = Length::Px(160.0)),
            );
            for seed in [1u64, 2, 3, 4, 5] {
                row = row.child(presence::preview_grown(ui, presence::Mood::Idle, 120.0, seed, facts));
            }
            col = col.child(row);
        }
        col
    })
}

/// Growth stages of the default Square skin, shown idle at 144.
pub fn design_evolution(ui: &Ui) -> El {
    presence::set_kind_override(Some(presence::Kind::Square));
    presence::set_hour_override(Some(10));
    close_design_settings();
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    design_hosted(ui, studio, move |ui| {
        fonts::prepare_theme(ui, &Fonts::load());
        let colors = ui.theme().colors.clone();
        let title = w::text(ui, "Square — growth stages")
            .style(tokens::typo(tokens::HERO).foreground(colors.text))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        let blurb = w::text(
            ui,
            "Details refine over time from data Remedy already has — sessions, messages, days, and planted goals. The face is complete from the first day.",
        )
        .style(tokens::typo(tokens::BODY).foreground(colors.muted))
        .layout(|l| {
            l.max_width = Length::Px(860.0);
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
        let stages: [(&str, &str, u8); 4] = [
            ("New", "The first days. A complete face, hairline frame only.", 0),
            ("Familiar", "A few conversations. A quiet inner ring.", 1),
            ("Companion", "Settled use. Tiny corner studs.", 2),
            ("Seasoned", "A long companionship. Side ticks and a chin mark.", 3),
        ];
        let mut row = w::row(ui)
            .align(Align::Start)
            .justify(Justify::SpaceBetween)
            .gap(tokens::GUTTER)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        for (name, when, stage) in stages {
            let label = w::text(ui, name)
                .style(tokens::typo(tokens::TITLE).foreground(colors.text));
            let cap = w::text(ui, when)
                .style(tokens::typo(tokens::META).foreground(colors.muted))
                .layout(|l| {
                    l.max_width = Length::Px(220.0);
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                });
            let cell = w::column(ui)
                .align(Align::Start)
                .gap(tokens::SPACE * 2.0)
                .child(presence::preview_stage(ui, presence::Mood::Idle, 144.0, stage))
                .child(label)
                .child(cap);
            row = row.child(cell);
        }
        w::column(ui)
            .align(Align::Start)
            .justify(Justify::Center)
            .gap(tokens::GUTTER)
            .padding(Insets::all(tokens::GUTTER * 2.0))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
                l.grow = 1.0;
                l.min_height = Length::Px(0.0);
            })
            .style(StylePatch::new().background(colors.bg).foreground(colors.text))
            .child(title)
            .child(blurb)
            .child(row)
    })
}

/// All six states of one presence form, for the owner to compare.
pub fn design_presence_board(ui: &Ui, kind: presence::Kind) -> El {
    presence::set_kind_override(Some(kind));
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    design_hosted(ui, studio, move |ui| {
        fonts::prepare_theme(ui, &Fonts::load());
        let colors = ui.theme().colors.clone();
        let size = theme_pack::layout().presence_review;
        let title = w::text(ui, kind.label())
            .style(tokens::typo(tokens::HERO).foreground(colors.text))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        let blurb = w::text(ui, kind.blurb())
            .style(tokens::typo(tokens::BODY).foreground(colors.muted))
            .layout(|l| {
                l.max_width = Length::Px(720.0);
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        let states = [
            presence::State::Idle,
            presence::State::Listening,
            presence::State::Thinking,
            presence::State::Working,
            presence::State::Done,
            presence::State::Error,
        ];
        let mut row = w::row(ui)
            .align(Align::Start)
            .justify(Justify::SpaceBetween)
            .gap(tokens::GUTTER)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            });
        for state in states {
            let label = w::text(ui, state.label())
                .style(tokens::typo(tokens::META).foreground(colors.muted));
            let cell = w::column(ui)
                .align(Align::Center)
                .gap(tokens::SPACE * 2.0)
                .child(presence::preview(ui, state, size))
                .child(label);
            row = row.child(cell);
        }
        w::column(ui)
            .align(Align::Start)
            .justify(Justify::Center)
            .gap(tokens::GUTTER)
            .padding(Insets::all(tokens::GUTTER * 2.0))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
                l.grow = 1.0;
                l.min_height = Length::Px(0.0);
            })
            .style(StylePatch::new().background(colors.bg).foreground(colors.text))
            .child(title)
            .child(blurb)
            .child(row)
    })
}


/// Design-8 first-run greeting. No Grove/Studio switcher.
pub fn design_setup_hello(ui: &Ui) -> El {
    design_setup::hello(ui)
}

/// Design-8 folder step.
pub fn design_setup_folder(ui: &Ui) -> El {
    design_setup::folder(ui)
}

/// Design-8 name step.
pub fn design_setup_name(ui: &Ui) -> El {
    design_setup::name(ui)
}

/// Design-8 ready step.
pub fn design_setup_ready(ui: &Ui) -> El {
    design_setup::ready(ui)
}

/// Design-8 more options: recommended, free, and other models.
pub fn design_setup_more(ui: &Ui) -> El {
    design_setup::more(ui)
}

/// Design-8 quiet background download: Grove as usual, no banner.
pub fn design_update_quiet(ui: &Ui) -> El {
    design_setup::update_quiet(ui)
}

/// Design-8 one-line update-ready notice with a single Restart button.
pub fn design_update_ready(ui: &Ui) -> El {
    design_setup::update_ready(ui)
}

/// Design-8 Advanced settings: notes, channel, and rollback.
pub fn design_update_advanced(ui: &Ui) -> El {
    design_setup::update_advanced(ui)
}

fn design_preview_messages() -> Vec<studio::ChatMessage> {
    use studio::ChatMessage;
    use work::WorkStep;
    let body = "The recipe importer loads again.\n\nThe loader now points at the new entry:\n\n```lua\n-- loader.lua\naddon.entry = \"init_main.lua\"\nassert(addon:register(), \"register before version check\")\n```\n\n`recipes.exe --check --all` reports all 6 addons OK. Restart the app to pick it up.";
    vec![
        ChatMessage {
            created_at: Some("2026-11-22T15:00:00Z".into()),
            ..ChatMessage::plain(
                "m1",
                "user",
                "The recipe importer stopped loading after the last update. Can you fix it?",
            )
        },
        ChatMessage {
            created_at: Some("2026-11-22T15:00:20Z".into()),
            thinking: "The manifest still named init.lua, but the last update renamed the entry. I'll patch the three files and run the addon check.".into(),
            steps: vec![
                {
                    let mut s = WorkStep::done("c1", "file_read");
                    s.detail = Some("manifest.json".into());
                    s
                },
                {
                    let mut s = WorkStep::done("c3", "file_write");
                    s.detail = Some("loader.lua".into());
                    s
                },
            ],
            ..ChatMessage::plain("m2", "assistant", body)
        },
    ]
}

/// Diagnostics golden: server, host, hardware, and the hive inspector. No network.
pub fn diagnostics_preview(ui: &Ui) -> El {
    diagnostics::preview(ui)
}

/// Grove as the paired phone shows it: the conversation first, one column.
pub fn grove_phone(ui: &Ui) -> El {
    grove::preview_phone(ui)
}

/// The phone's second tab: her goals and what she is working on.
pub fn grove_phone_work(ui: &Ui) -> El {
    let grove = grove::Grove::preview_phone(Studio::preview());
    grove.set_home_tab(grove::HomeTab::Plots);
    grove::phone(ui, grove, None)
}

/// Morning Grove with example plots, for goldens. It does not touch disk or the network.
pub fn grove_home(ui: &Ui) -> El {
    grove::preview_home(ui)
}

/// Grove home with a conversation under way, for goldens. It does not touch disk or the network.
pub fn grove_home_talking(ui: &Ui) -> El {
    grove::preview_talking(ui)
}

/// Grove home inside the real window chrome, with the post-update house-rules banner open.
pub fn grove_home_offer(ui: &Ui) -> El {
    let chrome = Chrome::preview(chrome::Overlay::None);
    let studio = Studio::preview();
    let grove = grove::Grove::preview(studio.clone());
    chrome.grove.set(Some(grove.clone()));
    let offer = setup::Offer::preview();
    chrome::hosted(ui, studio, chrome, move |ui| {
        grove::root(ui, grove.clone(), None, Some(offer.clone())).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
    })
}

/// Grove home inside the real window chrome, no banner. Used to check the
/// composer and the partner name at the same size as a typical install.
pub fn grove_home_chrome(ui: &Ui) -> El {
    let chrome = Chrome::preview(chrome::Overlay::None);
    let studio = Studio::preview();
    let grove = grove::Grove::preview(studio.clone());
    chrome.grove.set(Some(grove.clone()));
    chrome::hosted(ui, studio, chrome, move |ui| {
        grove::root(ui, grove.clone(), None, None).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
    })
}

/// Grove home in chrome with Remedy's presence turned off. Appearance defaults
/// to on; this is the hidden state for layout tests.
pub fn grove_home_presence_off(ui: &Ui) -> El {
    presence::set_enabled_override(Some(false));
    grove_home_chrome(ui)
}

/// Test hook: force presence on or off, or `None` to follow the Appearance pref.
pub fn set_presence_enabled_override(on: Option<bool>) {
    presence::set_enabled_override(on);
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

/// Settings overlay inside the chrome frame.
pub fn home_settings(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::Settings, false)
}

/// Memory overlay inside the chrome frame.
pub fn home_memory(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::Memory, false)
}

/// Skills overlay inside the chrome frame.
pub fn home_skills(ui: &Ui) -> El {
    chrome::preview_frame(ui, chrome::Overlay::Skills, false)
}

/// Image markup editor over a seeded settings screenshot (not a golden).
pub fn home_markup(ui: &Ui) -> El {
    markup_preview(ui, false)
}

/// Image markup editor with the text-label card open (not a golden).
pub fn home_markup_text(ui: &Ui) -> El {
    markup_preview(ui, true)
}

fn markup_preview(ui: &Ui, text_dialog: bool) -> El {
    let studio = Studio::preview();
    studio.conn.set(studio::Conn::Ready);
    depth::markup::seed_preview(&studio, text_dialog);
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// Filled usage tab, for goldens. No runtime calls.
pub fn preview_usage(ui: &Ui) -> El {
    usage::UsagePanel::preview(ui)
}

/// Filled harness tab, for goldens. No runtime calls.
pub fn preview_usage_harness(ui: &Ui) -> El {
    usage::UsagePanel::preview_harness(ui)
}

/// Browser chrome alone, for the computer-use side-by-side and the chrome test.
pub fn browser_preview(ui: &Ui) -> El {
    browser::preview(ui)
}

/// Browser chrome at `width` logical pixels (a narrow rail).
pub fn browser_preview_at(ui: &Ui, width: f32) -> El {
    browser::preview_at(ui, width)
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

/// Sessions rail filled past its viewport, for scroll tests.
pub fn home_sessions_long(ui: &Ui) -> El {
    let studio = Studio::preview_chat();
    let mut sessions = studio.sessions.get();
    for i in 0..40 {
        sessions.push(crate::studio::Session::fresh(
            format!("extra-{i}"),
            format!("Queued task {i}"),
        ));
    }
    studio.sessions.set(sessions);
    shell_with(
        ui,
        studio,
        rails::WorkspaceChrome::fixed(layout::WorkspaceLayout::default()),
    )
}

/// The smallest the main window may be: the narrow layout still works here.
const MAIN_MIN_W: f32 = 360.0;
const MAIN_MIN_H: f32 = 280.0;

/// Whether a window of this size is too small to use or to resize by its own frame.
fn window_too_small(width: f32, height: f32) -> bool {
    width < MAIN_MIN_W || height < MAIN_MIN_H
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
    let chat = studio.clone();
    shell_around(ui, studio, chrome, move |ui| chat::well(ui, chat))
}

/// Studio's rails and status around `center`, for the live window: Grove sits between
/// the same rails as the chat, so they stay put when the surface changes.
pub(crate) fn shell_around_live(ui: &Ui, studio: Studio, center: El) -> El {
    shell_around(ui, studio, rails::WorkspaceChrome::load(), move |_| center)
}

/// `center` is built after the shell has set up its theme and settings, as the chat always was.
fn shell_around(
    ui: &Ui,
    studio: Studio,
    chrome: rails::WorkspaceChrome,
    center: impl FnOnce(&Ui) -> El,
) -> El {
    // Chat-depth workers hop back onto this Ui (audio, dialogs). Every shell
    // path, including rail previews, goes through here.
    depth::set_ui(ui.clone());
    fonts::prepare_theme(ui, &Fonts::load());
    if !studio.local_only {
        rails::bind_chrome(chrome.clone());
        browser::boot(studio.clone());
    }
    // Hidden overlay keeper. A closed rail is not a child of this column.
    studio.time_travel.mount(ui, studio.clone());
    let workspace = slides::Workspace::for_studio(&studio);
    slides::install(workspace.clone());
    let follow_ws = workspace;
    let follow_studio = studio.clone();
    if studio.local_only {
        settings::forget_current();
    }
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
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
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
                .child(center(ui))
                .child(rails::side(
                    ui,
                    chrome.clone(),
                    layout::Side::Right,
                    studio.clone(),
                )),
        )
        .child(if crate::chrome::dock_in_host() {
            ui.el("status-hosted").layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Px(0.0);
            })
        } else {
            w::column(ui)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(w::divider(ui, false))
                .child(status::status_bar(ui, studio, move || {
                    status_toggle.toggle_settings()
                }))
        })
        .child(rails::hosts(ui, chrome))
        .on_key_capture(move |ev| settings::shell_chord(panel, ev))
        .on_key(move |ev| settings::shell_escape(panel, ev))
        .on_removed(move || {
            settings::clear_studio_hooks(hooks);
            slides::uninstall();
            rails::unbind_chrome();
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

/// The same 32 px Remedy tray mark 0.70.5 shipped.
pub fn tray_icon() -> TrayImage {
    const TRAY_PNG: &[u8] = include_bytes!("../assets/icons/tray-icon.png");
    let decoded = remedy_ui::image::decode(TRAY_PNG, Some((32, 32)))
        .or_else(|_| remedy_ui::image::decode(TRAY_PNG, None))
        .expect("0.70.5 tray-icon.png must decode");
    let img = decoded.image;
    TrayImage {
        size: img.width.max(1),
        rgba: img.pixels.to_vec(),
    }
}

/// Taskbar / Alt+Tab / title-bar mark from the same 0.70.5 artwork as the exe icon.
/// Drawings at 32 and 128 px so Windows can pick the size it needs.
pub fn window_icon() -> WindowIcon {
    fn from_png(bytes: &[u8], size: u32) -> WindowIcon {
        let decoded = remedy_ui::image::decode(bytes, Some((size, size)))
            .or_else(|_| remedy_ui::image::decode(bytes, None))
            .expect("Remedy icon PNG must decode");
        let img = decoded.image;
        WindowIcon::new(img.width.max(1), img.pixels.to_vec())
            .expect("Remedy icon PNG must be square RGBA")
    }
    const ICON_32: &[u8] = include_bytes!("../assets/icons/32x32.png");
    const ICON_128: &[u8] = include_bytes!("../assets/icons/128x128.png");
    from_png(ICON_32, 32).with_size(from_png(ICON_128, 128))
}

#[cfg(test)]
mod window_size_tests {
    use super::window_too_small;

    #[test]
    fn a_minimized_windows_saved_size_is_too_small() {
        assert!(window_too_small(160.0, 28.0), "what Windows reports for a minimized window");
        assert!(window_too_small(1180.0, 20.0));
        assert!(!window_too_small(400.0, 700.0), "the narrow layout");
        assert!(!window_too_small(1180.0, 740.0));
    }
}

#[cfg(test)]
mod tray_icon_tests {
    use super::{tray_icon, window_icon};

    #[test]
    fn tray_uses_the_remedy_mark_not_a_green_square() {
        let icon = tray_icon();
        assert_eq!(icon.size, 32, "Windows tray mark is 32 px, same as 0.70.5");
        assert_eq!(icon.rgba.len(), 32 * 32 * 4);
        let mut forest = 0u32;
        let mut opaque = 0u32;
        let mut colors = std::collections::BTreeSet::new();
        for px in icon.rgba.chunks_exact(4) {
            colors.insert((px[0], px[1], px[2], px[3]));
            if px[3] == 0 {
                continue;
            }
            opaque += 1;
            if px == [61, 139, 95, 255] {
                forest += 1;
            }
        }
        assert!(opaque > 0, "tray mark must have visible pixels");
        assert!(
            colors.len() > 2,
            "the 0.70.5 mark is a multi-color circuit R, not a flat square"
        );
        assert!(
            forest * 2 < opaque,
            "tray must not be the solid forest square 1.0.0 shipped"
        );
    }

    #[test]
    fn window_icon_is_the_remedy_mark_at_taskbar_sizes() {
        let icon = window_icon();
        let sizes = icon.drawn_sizes();
        assert!(sizes.contains(&32), "taskbar needs 32 px, got {sizes:?}");
        assert!(sizes.contains(&128), "Alt+Tab / jumbo needs 128 px, got {sizes:?}");
        assert_eq!(icon.rgba().len(), (icon.size() * icon.size() * 4) as usize);
        let mut opaque = 0u32;
        let mut colors = std::collections::BTreeSet::new();
        for px in icon.rgba().chunks_exact(4) {
            colors.insert((px[0], px[1], px[2], px[3]));
            if px[3] > 0 {
                opaque += 1;
            }
        }
        assert!(opaque > 0, "window icon must have visible pixels");
        assert!(
            colors.len() > 2,
            "window icon must be the Remedy mark, not a blank document"
        );
    }
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
