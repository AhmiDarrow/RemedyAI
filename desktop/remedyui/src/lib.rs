//! Remedy Desktop shell: Studio sessions, close-to-tray, one instance.
//!
//! Visual language follows the current Tauri/React app: Remedy Sans UI at 16 px
//! for interface text, AhmiPrint for code, a full-width New session control,
//! dense session rows, a typographic well and a composer. The mind is not
//! spawned here yet; the status bar says so. Chat streaming, settings, setup
//! and the rest of the workspace follow on this branch.

mod fonts;

pub use fonts::{DEFAULT_CODE_FONT, DEFAULT_UI_FONT, Fonts};

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::host::{TrayEvent, TrayIcon, TrayImage, TrayItem};
use remedy_ui::i18n::Messages;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{RADIUS_FULL, StylePatch};
use remedy_ui::view::IntoProp;
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, WindowSpec};

const SHOW: u32 = 1;
const QUIT: u32 = 2;
/// Matches the current app's 270 px session column.
const SIDEBAR_WIDTH: f32 = 270.0;
const TOOL: f32 = 32.0;
const GLYPH: f32 = 20.0;
/// Inset accent bar on the selected row; reserved on every row so the close
/// control stays lined up (the first shell prepended this only when selected).
const SELECT_MARK: f32 = 3.0;

/// A Studio session row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    /// Stable id for this run.
    pub id: u64,
    /// Title shown in the list.
    pub title: String,
    /// Quiet second line (time, message count); empty if we do not have one yet.
    pub subtitle: String,
}

/// Studio session list and the selected id.
#[derive(Clone)]
pub struct Studio {
    /// Open sessions, top to bottom.
    pub sessions: Signal<Vec<Session>>,
    /// The selected session, if any.
    pub active: Signal<Option<u64>>,
    /// Filter text for the session search box.
    pub query: Signal<String>,
    next_id: Signal<u64>,
}

impl Studio {
    /// One empty chat, as a first launch looks.
    pub fn new() -> Self {
        Self::with(
            vec![Session {
                id: 1,
                title: "New chat".into(),
                subtitle: String::new(),
            }],
            Some(1),
            2,
        )
    }

    /// Two named sessions, for previews and visual review.
    pub fn preview() -> Self {
        Self::with(
            vec![
                Session {
                    id: 1,
                    title: "Plan the week".into(),
                    subtitle: "2 msgs".into(),
                },
                Session {
                    id: 2,
                    title: "House rules".into(),
                    subtitle: "2 msgs".into(),
                },
            ],
            Some(1),
            3,
        )
    }

    fn with(sessions: Vec<Session>, active: Option<u64>, next_id: u64) -> Self {
        Studio {
            sessions: Signal::new(sessions),
            active: Signal::new(active),
            query: Signal::new(String::new()),
            next_id: Signal::new(next_id),
        }
    }

    /// Appends a new chat and selects it.
    pub fn add(&self) {
        let id = self.next_id.get();
        self.next_id.set(id + 1);
        let title = format!("New chat {id}");
        self.sessions.update(|list| {
            list.push(Session {
                id,
                title,
                subtitle: String::new(),
            })
        });
        self.active.set(Some(id));
    }

    /// Drops the session; if it was selected, the neighbour above (or the first left) is selected.
    pub fn close(&self, id: u64) {
        self.sessions.update(|list| {
            if let Some(i) = list.iter().position(|s| s.id == id) {
                list.remove(i);
            }
        });
        if self.active.get() == Some(id) {
            let list = self.sessions.get();
            self.active.set(list.last().map(|s| s.id));
        }
    }
}

/// Opens the window and puts the app in the tray.
pub fn app(app: &App) {
    app.keep_running(true);
    let studio = Studio::new();
    let a = app.clone();
    let win = app.open(
        WindowSpec::new("Remedy Desktop")
            .size(1180.0, 740.0)
            .hide_on_close()
            .persist_geometry("main"),
        move |ui, window| {
            let (u, win, a) = (ui.clone(), window.clone(), a.clone());
            ui.root().child(shell(ui, studio.clone())).bind(move || {
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

/// The window's content with two preview sessions (golden images and visual review).
pub fn home(ui: &Ui) -> El {
    shell(ui, Studio::preview())
}

/// The Studio shell: session list, well, composer, status.
pub fn shell(ui: &Ui, studio: Studio) -> El {
    fonts::prepare_theme(ui, &Fonts::load());
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
                .child(sidebar(ui, studio.clone()))
                .child(w::divider(ui, true))
                .child(well(ui, studio)),
        )
        .child(w::divider(ui, false))
        .child(status_bar(ui))
}

fn sidebar(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    w::column(ui)
        .style(StylePatch::new().background(colors.panel))
        .layout(|l| {
            l.width = Length::Px(SIDEBAR_WIDTH);
            l.min_width = Length::Px(SIDEBAR_WIDTH);
            l.max_width = Length::Px(SIDEBAR_WIDTH);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .padding(Insets {
            top: unit * 3.0,
            end: unit * 2.0,
            bottom: unit * 3.0,
            start: unit * 2.0,
        })
        .gap(unit * 2.0)
        .child(sidebar_chrome(ui, studio.clone()))
        .child(session_list(ui, studio))
}

fn sidebar_chrome(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let add = studio.clone();
    let search = studio.clone();
    let u = ui.clone();
    w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(
            w::button(
                ui,
                move || messages().text(&u, "new-session"),
                move || add.add(),
            )
            .variant("primary")
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_height = Length::Px(36.0);
                l.shrink = 0.0;
            }),
        )
        .child({
            w::search(ui, messages().text(ui, "search-sessions"), move |q| {
                search.query.set(q);
            })
            .layout(|l| l.width = Length::Percent(1.0))
        })
        .child(filter_row(ui))
}

fn filter_row(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let u_all = ui.clone();
    let u_pin = ui.clone();
    let u_arch = ui.clone();
    w::row(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(filter_chip(
            ui,
            move || messages().text(&u_all, "filter-all"),
            true,
            || {},
        ))
        .child(filter_chip(
            ui,
            move || messages().text(&u_pin, "filter-pin"),
            false,
            || {},
        ))
        .child(filter_chip(
            ui,
            move || messages().text(&u_arch, "filter-archive"),
            false,
            || {},
        ))
}

fn filter_chip(
    ui: &Ui,
    label: impl IntoProp<String>,
    active: bool,
    on_press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let mut style = StylePatch::new().radius(RADIUS_FULL);
    if active {
        style = style
            .background(colors.accent.mix_oklab(colors.panel, 0.82))
            .foreground(colors.accent);
    } else {
        style = style.foreground(colors.muted);
    }
    w::button(ui, label, on_press).variant("quiet").style(style)
}

fn session_list(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let u_label = ui.clone();
    let group = w::text(ui, "")
        .variant("muted")
        .text(move || messages().text(&u_label, "no-project"));
    let col = w::column(ui).gap(unit * 0.5).layout(|l| {
        l.width = Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
    });
    let scroll = w::scroll(ui).layout(|l| {
        l.width = Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
    });
    let ui_rows = ui.clone();
    let studio_rows = studio.clone();
    let studio_src = studio.clone();
    w::for_each(
        &col,
        studio.sessions,
        |s| s.id,
        move |s| {
            let q = studio_src.query.get();
            let q = q.trim().to_ascii_lowercase();
            if !q.is_empty()
                && !s.title.to_ascii_lowercase().contains(&q)
                && !s.subtitle.to_ascii_lowercase().contains(&q)
            {
                return ui_rows.el("session-hidden").layout(|l| {
                    l.width = Length::Px(0.0);
                    l.height = Length::Px(0.0);
                    l.shrink = 0.0;
                });
            }
            session_row(&ui_rows, &studio_rows, s)
        },
    );
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(group)
        .child(scroll.child(col))
}

fn session_row(ui: &Ui, studio: &Studio, session: &Session) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let id = session.id;
    let title = session.title.clone();
    let subtitle = session.subtitle.clone();
    let studio_sel = studio.clone();
    let studio_close = studio.clone();
    let selected = studio.active.get() == Some(id);
    let close_label = {
        let title = title.clone();
        let u = ui.clone();
        move || format!("{} {title}", messages().text(&u, "close"))
    };
    let studio_sem = studio.clone();

    let fill = if selected {
        colors.card.mix_oklab(colors.accent, 0.12)
    } else {
        colors.panel.with_alpha(0.0)
    };
    let mark = if selected {
        colors.accent
    } else {
        colors.accent.with_alpha(0.0)
    };

    let text_col = w::column(ui)
        .gap(unit * 0.25)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, &title)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .nowrap(),
        );
    let text_col = if subtitle.is_empty() {
        text_col
    } else {
        text_col.child(w::text(ui, &subtitle).variant("muted").nowrap())
    };

    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit * 2.0,
            end: unit,
            bottom: unit * 2.0,
            start: 0.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(TOOL);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(fill)
                .radius(ui.theme().radii.small),
        )
        .on_click(move || studio_sel.active.set(Some(id)))
        .semantics({
            let title = title.clone();
            move || {
                Semantics::new(Role::Button)
                    .label(title.clone())
                    .selected(studio_sem.active.get() == Some(id))
                    .action(Action::Click)
            }
        })
        .child(
            ui.el("selection-mark")
                .style(StylePatch::new().background(mark))
                .layout(|l| {
                    l.width = Length::Px(SELECT_MARK);
                    l.min_width = Length::Px(SELECT_MARK);
                    l.height = Length::Percent(1.0);
                    l.shrink = 0.0;
                }),
        )
        .child(text_col)
        .child(tool_button(ui, w::Icon::Close, close_label, move || {
            studio_close.close(id)
        }))
}

fn well(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    w::column(ui)
        .style(StylePatch::new().background(colors.bg))
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        })
        .child(chat_header(ui, studio.clone()))
        .child(well_body(ui, studio))
        .child(composer(ui))
}

fn chat_header(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    let u2 = ui.clone();
    w::column(ui)
        .gap(unit * 0.5)
        .padding(Insets {
            top: unit * 3.0,
            end: unit * 4.0,
            bottom: unit * 2.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().foreground(colors.text))
        .child(w::text(ui, "").variant("strong").text(move || {
            let active = studio.active.get();
            let list = studio.sessions.get();
            match active.and_then(|id| list.iter().find(|s| s.id == id).map(|s| s.title.clone())) {
                Some(title) => title,
                None => messages().text(&u, "no-session"),
            }
        }))
        .child(
            w::text(ui, "")
                .variant("muted")
                .text(move || messages().text(&u2, "partner-name")),
        )
}

fn well_body(ui: &Ui, _studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 8.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(say(ui, "ready-title", "heading"))
        .child(say(ui, "ready-hint", "muted"))
        .child(say(ui, "ready-keys", "muted"))
}

fn composer(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors
        .line
        .unwrap_or(colors.accent.mix_oklab(colors.bg, 0.65));
    let u = ui.clone();
    let shell = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit * 1.5,
            end: unit * 1.5,
            bottom: unit * 1.5,
            start: unit * 1.5,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(48.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .radius(ui.theme().radii.large)
                .border(line)
                .border_width(1.0),
        )
        .child(tool_button(
            ui,
            w::Icon::File,
            move || messages().text(&u, "composer-attach"),
            || {},
        ))
        .child(
            w::text_input(ui, messages().text(ui, "composer-placeholder"))
                .placeholder(messages().text(ui, "composer-placeholder"))
                .el()
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child({
            let u = ui.clone();
            tool_button(
                ui,
                w::Icon::Send,
                move || messages().text(&u, "composer-send"),
                || {},
            )
        });
    w::column(ui)
        .padding(Insets {
            top: unit,
            end: unit * 4.0,
            bottom: unit * 3.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(shell)
}

fn status_bar(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit * 2.0)
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.muted),
        )
        .padding(Insets {
            top: unit * 1.5,
            end: unit * 3.0,
            bottom: unit * 1.5,
            start: unit * 3.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(36.0);
            l.shrink = 0.0;
        })
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 2.0)
                .child(
                    w::button(ui, move || messages().text(&u, "status-settings"), || {})
                        .variant("quiet"),
                )
                .child(say(ui, "status-disconnected", "muted")),
        )
        .child(say(ui, "partner-name", "muted"))
}

/// A square tool control: 32 px hit target, 20 px glyph, theme hover and focus, always visible.
fn tool_button(
    ui: &Ui,
    glyph: w::Icon,
    label: impl IntoProp<String>,
    on_press: impl Fn() + 'static,
) -> El {
    let label = label.into_prop();
    let colors = ui.theme().colors.clone();
    let b = ui
        .el("icon_button")
        .child(w::icon(ui, glyph, GLYPH))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.min_width = Length::Px(TOOL);
            l.min_height = Length::Px(TOOL);
            l.width = Length::Px(TOOL);
            l.height = Length::Px(TOOL);
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
    // The `icon_button` sheet supplies hover (flat fill) and focus (accent line, no glow).
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
pub fn messages() -> Messages {
    thread_local!(static MESSAGES: Messages = {
        let m = Messages::new("en-US").embedded("en-US", include_str!("../i18n/en-US.ftl"));
        if cfg!(debug_assertions) {
            let m = m.folder(concat!(env!("CARGO_MANIFEST_DIR"), "/i18n"));
            m.watch();
            m
        } else {
            m
        }
    });
    MESSAGES.with(Clone::clone)
}

fn say(ui: &Ui, id: &'static str, variant: &'static str) -> El {
    let u = ui.clone();
    w::text(ui, "")
        .variant(variant)
        .text(move || messages().text(&u, id))
}
