//! Session list: New session, search, chips, project tree, row actions.

use crate::studio::{Filter, Project, Session, Studio, grouped};
use crate::time;
use crate::{SELECT_MARK, SIDEBAR_WIDTH, messages, tool_button, tool_button_sized};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{RADIUS_FULL, StylePatch};
use remedy_ui::view::IntoProp;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

/// The 270 px session column.
pub fn sidebar(ui: &Ui, studio: Studio) -> El {
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
            bottom: unit,
            start: unit * 2.0,
        })
        .gap(unit * 2.0)
        .child(chrome(ui, studio.clone()))
        .child(tree(ui, studio))
}

fn chrome(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(new_session_button(ui, studio.clone()))
        .child({
            let search = studio.clone();
            w::search(ui, messages().text(ui, "search-sessions"), move |q| {
                search.query.set(q);
            })
            .layout(|l| l.width = Length::Percent(1.0))
        })
        .child(filter_row(ui, studio.clone()))
        .child(session_options(ui, studio.clone()))
        .child(add_project(ui, studio))
}

fn new_session_button(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    let add = studio.clone();
    ui.el("button")
        .variant("primary")
        .row()
        .align(Align::Center)
        .justify(Justify::Start)
        .gap(ui.theme().spacing.unit)
        .padding(Insets {
            top: 8.0,
            end: 12.0,
            bottom: 8.0,
            start: 12.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(36.0);
            l.shrink = 0.0;
        })
        .child(w::icon(ui, w::Icon::Plus, 16.0))
        .child(
            w::text(ui, "")
                .text(move || messages().text(&u, "new-session"))
                .nowrap(),
        )
        .on_click(move || add.add())
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Button)
                    .label(messages().text(&u, "new-session"))
                    .action(Action::Click)
            }
        })
        .style(StylePatch::new().foreground(colors.on_accent))
}

fn filter_row(ui: &Ui, studio: Studio) -> El {
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
            {
                let s = studio.clone();
                move || s.filter.get() == Filter::All
            },
            {
                let s = studio.clone();
                move || s.filter.set(Filter::All)
            },
        ))
        .child(filter_chip(
            ui,
            move || messages().text(&u_pin, "filter-pin"),
            {
                let s = studio.clone();
                move || s.filter.get() == Filter::Pinned
            },
            {
                let s = studio.clone();
                move || s.filter.set(Filter::Pinned)
            },
        ))
        .child(filter_chip(
            ui,
            move || messages().text(&u_arch, "filter-archive"),
            {
                let s = studio.clone();
                move || s.filter.get() == Filter::Archived
            },
            {
                let s = studio.clone();
                move || s.filter.set(Filter::Archived)
            },
        ))
}

fn filter_chip(
    ui: &Ui,
    label: impl IntoProp<String>,
    active: impl Fn() -> bool + 'static,
    on_press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors
        .line
        .unwrap_or(colors.muted.mix_oklab(colors.panel, 0.5));
    w::button(ui, label, on_press)
        .variant("quiet")
        .style(move || {
            let style = StylePatch::new()
                .radius(RADIUS_FULL)
                .border(line)
                .border_width(1.0);
            if active() {
                style
                    .background(colors.accent.mix_oklab(colors.panel, 0.82))
                    .foreground(colors.accent)
                    .border(colors.accent.mix_oklab(line, 0.4))
            } else {
                style
                    .foreground(colors.muted)
                    .background(colors.panel.mix_oklab(colors.bg, 0.15))
            }
        })
}

fn session_options(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let toggle = studio.clone();
    let u = ui.clone();
    let header = w::button(
        ui,
        move || messages().text(&u, "session-options"),
        move || {
            toggle.options_open.update(|v| *v = !*v);
        },
    )
    .variant("quiet")
    .layout(|l| l.width = Length::Percent(1.0));
    let st = studio.clone();
    let u = ui.clone();
    let actions = show(
        ui,
        move || st.options_open.get(),
        move || {
            let u_in = u.clone();
            let u_ex = u.clone();
            w::row(&u)
                .gap(unit)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(
                    w::button(&u, move || messages().text(&u_in, "sidebar-import"), || {})
                        .variant("quiet")
                        .layout(|l| {
                            l.grow = 1.0;
                            l.basis = Length::Px(0.0);
                        }),
                )
                .child(
                    w::button(&u, move || messages().text(&u_ex, "sidebar-export"), || {})
                        .variant("quiet")
                        .layout(|l| {
                            l.grow = 1.0;
                            l.basis = Length::Px(0.0);
                        }),
                )
        },
    )
    .el();
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(header)
        .child(actions)
        .style(StylePatch::new().foreground(colors.muted))
}

fn add_project(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let st = studio.clone();
    let u = ui.clone();
    let studio_form = studio.clone();
    show(
        ui,
        move || st.adding_project.get(),
        move || add_project_form(&u, studio_form.clone(), unit),
    )
    .fallback({
        let open = studio.clone();
        let u = ui.clone();
        let radius = ui.theme().radii.small;
        move || {
            w::button(
                &u,
                {
                    let u = u.clone();
                    move || messages().text(&u, "add-project")
                },
                {
                    let open = open.clone();
                    move || {
                        open.adding_project.set(true);
                    }
                },
            )
            .variant("quiet")
            .layout(|l| l.width = Length::Percent(1.0))
            .style(
                StylePatch::new()
                    .border(line)
                    .border_width(1.0)
                    .border_style(remedy_ui::style::BorderStyle::Dashed)
                    .foreground(colors.muted)
                    .radius(radius),
            )
        }
    })
    .el()
    .layout(|l| l.width = Length::Percent(1.0))
}

fn add_project_form(ui: &Ui, studio: Studio, unit: f32) -> El {
    let field = w::text_input(ui, messages().text(ui, "project-path"));
    let f2 = field.clone();
    let s = studio.clone();
    let field = field.on_submit(move || {
        s.add_project(f2.value(), false);
    });
    let s2 = studio.clone();
    w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(field.el().layout(|l| l.width = Length::Percent(1.0)))
        .child(
            w::row(ui)
                .gap(unit)
                .child(
                    w::button(
                        ui,
                        {
                            let u = ui.clone();
                            move || messages().text(&u, "project-add")
                        },
                        {
                            let s = studio.clone();
                            let f = field.clone();
                            move || s.add_project(f.value(), false)
                        },
                    )
                    .variant("primary"),
                )
                .child(
                    w::button(
                        ui,
                        {
                            let u = ui.clone();
                            move || messages().text(&u, "cancel")
                        },
                        move || {
                            s2.adding_project.set(false);
                            s2.project_draft.set(String::new());
                        },
                    )
                    .variant("quiet"),
                ),
        )
}

fn tree(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let rows: Signal<Vec<TreeItem>> = Signal::new(Vec::new());
    let rows_out = rows.clone();
    let st = studio.clone();
    let col = w::column(ui).gap(unit * 1.5).layout(|l| {
        l.width = Length::Percent(1.0);
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
    });
    let _ = col.clone().bind(move || {
        rows_out.set(tree_items(
            &st.sessions.get(),
            &st.projects.get(),
            st.filter.get(),
            &st.query.get(),
        ));
    });
    let ui_rows = ui.clone();
    let studio_rows = studio.clone();
    let col = w::for_each(&col, rows, TreeItem::key, move |item| match item {
        TreeItem::Header(path) => project_header(&ui_rows, &studio_rows, path.clone()),
        TreeItem::Session(id) => {
            let session = studio_rows
                .sessions
                .get()
                .into_iter()
                .find(|s| s.id == *id)
                .unwrap_or(Session {
                    id: id.clone(),
                    title: String::new(),
                    project_path: None,
                    pinned: false,
                    archived: false,
                    tags: Vec::new(),
                    message_count: 0,
                    updated_at: None,
                });
            session_row(&ui_rows, &studio_rows, &session)
        }
        TreeItem::Hint => {
            let hint = ui_rows.clone();
            w::text(&ui_rows, "")
                .variant("muted")
                .text(move || messages().text(&hint, "row-hint"))
        }
    });
    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(col)
}

#[derive(Clone)]
enum TreeItem {
    Header(Option<String>),
    Session(String),
    Hint,
}

impl TreeItem {
    fn key(&self) -> String {
        match self {
            TreeItem::Header(path) => format!("h:{}", path.as_deref().unwrap_or("-")),
            TreeItem::Session(id) => format!("s:{id}"),
            TreeItem::Hint => "hint".into(),
        }
    }
}

fn tree_items(
    sessions: &[Session],
    projects: &[Project],
    filter: Filter,
    query: &str,
) -> Vec<TreeItem> {
    let mut out = Vec::new();
    for (project, members) in grouped(sessions, projects, filter, query) {
        out.push(TreeItem::Header(project.path.clone()));
        if !project.collapsed {
            for session in members {
                out.push(TreeItem::Session(session.id));
            }
        }
    }
    out.push(TreeItem::Hint);
    out
}

fn project_header(ui: &Ui, studio: &Studio, path: Option<String>) -> El {
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    let st = studio.clone();
    let path_for_label = path.clone();
    let label = move || {
        let groups = grouped(
            &st.sessions.get(),
            &st.projects.get(),
            st.filter.get(),
            &st.query.get(),
        );
        let count = groups
            .iter()
            .find(|(p, _)| p.path == path_for_label)
            .map(|(_, m)| m.len())
            .unwrap_or(0);
        let base = if path_for_label.is_none() {
            messages().text(&u, "no-project")
        } else {
            crate::studio::project_label(path_for_label.as_deref())
        };
        format!("{base}  {count}")
    };
    let s = studio.clone();
    w::button(ui, label, move || s.toggle_collapsed(path.as_deref()))
        .variant("quiet")
        .layout(|l| l.width = Length::Percent(1.0))
        .style(StylePatch::new().foreground(colors.muted))
}

fn session_row(ui: &Ui, studio: &Studio, session: &Session) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let id = session.id.clone();
    let title = session.title.clone();
    let radius = ui.theme().radii.small;
    let now = studio.now();
    let age = session
        .updated_at
        .as_deref()
        .map(|t| time::relative(t, now))
        .filter(|s| !s.is_empty());
    let count = session.message_count;
    let sub = match age {
        Some(a) => format!(
            "{a}  ·  {count} {}",
            if count == 1 { "msg" } else { "msgs" }
        ),
        None => format!("{count} {}", if count == 1 { "msg" } else { "msgs" }),
    };

    let close_label = {
        let title = title.clone();
        let u = ui.clone();
        move || format!("{} {title}", messages().text(&u, "close"))
    };
    let studio_sel = studio.clone();
    let sid = id.clone();
    let studio_sem = studio.clone();
    let sem_title = title.clone();
    let sem_id = id.clone();

    let text_col = w::column(ui)
        .gap(2.0)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .clip()
        .child(
            w::text(ui, &title)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .nowrap(),
        )
        .child(w::text(ui, &sub).variant("muted").nowrap());

    let pin = studio.clone();
    let pin_id = id.clone();
    let pin_lookup = studio.clone();
    let pin_btn = w::button(
        ui,
        {
            let st = studio.clone();
            let pin_id = id.clone();
            move || {
                let pinned = st
                    .sessions
                    .get()
                    .iter()
                    .find(|s| s.id == pin_id)
                    .map(|s| s.pinned)
                    .unwrap_or(false);
                if pinned { "★" } else { "☆" }.to_string()
            }
        },
        move || pin.toggle_pin(&pin_id),
    )
    .variant("quiet")
    .semantics({
        let u = ui.clone();
        let st = pin_lookup;
        let pin_id = id.clone();
        move || {
            let pinned = st
                .sessions
                .get()
                .iter()
                .find(|s| s.id == pin_id)
                .map(|s| s.pinned)
                .unwrap_or(false);
            Semantics::new(Role::Button)
                .label(messages().text(&u, if pinned { "unpin" } else { "pin" }))
                .action(Action::Click)
        }
    })
    .layout(|l| {
        l.shrink = 0.0;
        l.min_width = Length::Px(28.0);
        l.width = Length::Px(28.0);
    });

    let arch = studio.clone();
    let arch_id = id.clone();
    let arch_btn = {
        let u = ui.clone();
        let st = studio.clone();
        let aid = id.clone();
        tool_button_sized(
            ui,
            w::Icon::Download,
            move || {
                let archived = st
                    .sessions
                    .get()
                    .iter()
                    .find(|s| s.id == aid)
                    .map(|s| s.archived)
                    .unwrap_or(false);
                messages().text(&u, if archived { "unarchive" } else { "archive" })
            },
            move || arch.toggle_archive(&arch_id),
            28.0,
            16.0,
        )
    };

    let folder_btn = {
        let u = ui.clone();
        tool_button_sized(
            ui,
            w::Icon::Folder,
            move || messages().text(&u, "move-project"),
            || {},
            28.0,
            16.0,
        )
    };

    let studio_close = studio.clone();
    let close_id = id.clone();

    w::row(ui)
        .align(Align::Center)
        .gap(unit * 0.25)
        .padding(Insets {
            top: unit * 1.5,
            end: 0.0,
            bottom: unit * 1.5,
            start: 0.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(44.0);
            l.shrink = 0.0;
            l.wrap = false;
        })
        .style({
            let st = studio.clone();
            let sid = id.clone();
            move || {
                let selected = st.active.get().as_deref() == Some(sid.as_str());
                let fill = if selected {
                    colors.card.mix_oklab(colors.accent, 0.12)
                } else {
                    colors.panel.with_alpha(0.0)
                };
                StylePatch::new().background(fill).radius(radius)
            }
        })
        .on_click(move || studio_sel.select(&sid))
        .semantics(move || {
            Semantics::new(Role::Button)
                .label(sem_title.clone())
                .selected(studio_sem.active.get().as_deref() == Some(sem_id.as_str()))
                .action(Action::Click)
        })
        .child(
            ui.el("selection-mark")
                .style({
                    let st = studio.clone();
                    let sid = id.clone();
                    move || {
                        let selected = st.active.get().as_deref() == Some(sid.as_str());
                        let mark = if selected {
                            colors.accent
                        } else {
                            colors.accent.with_alpha(0.0)
                        };
                        StylePatch::new().background(mark)
                    }
                })
                .layout(|l| {
                    l.width = Length::Px(SELECT_MARK);
                    l.min_width = Length::Px(SELECT_MARK);
                    l.height = Length::Percent(1.0);
                    l.shrink = 0.0;
                }),
        )
        .child(pin_btn)
        .child(text_col)
        .child(arch_btn)
        .child(folder_btn)
        .child(tool_button(ui, w::Icon::Close, close_label, move || {
            studio_close.close(&close_id);
        }))
}
