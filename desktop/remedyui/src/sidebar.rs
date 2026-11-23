//! Session list: New session, search, chips, project tree, row actions.

use crate::depth::logic::{
    ordered_groups, session_delete_body, should_show_busy_badge, PINNED_GROUP_KEY,
};
use crate::depth::{
    ask_remove_project, assign_project, export_active, import_session, move_project_order,
    move_session_order, place_session, rename_session,
};
use crate::studio::{Filter, Project, Session, Studio};
use crate::time;
use crate::{SELECT_MARK, SIDEBAR_WIDTH, messages, tool_button, tool_button_sized};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::host::{DragData, DropEffect};
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{RADIUS_FULL, StylePatch};
use remedy_ui::view::{Drag, IntoProp};
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

/// Left-aligned disclosure row: chevron, label, optional trailing count.
///
/// Project headers stay keyed by path, so the arrow and count are closures.
fn disclosure_button(
    ui: &Ui,
    label: impl Fn() -> String + Clone + 'static,
    expanded: impl Fn() -> bool + Clone + 'static,
    on_press: impl Fn() + 'static,
    count: impl Fn() -> String + Clone + 'static,
    show_count: bool,
    emphasize: bool,
) -> El {
    let colors = ui.theme().colors.clone();
    let label_color = if emphasize { colors.text } else { colors.muted };
    let weight = if emphasize { 600.0 } else { 500.0 };
    let open = expanded.clone();
    let u_open = ui.clone();
    let u_shut = ui.clone();
    let chevron = show(
        ui,
        move || open(),
        move || {
            w::icon(&u_open, w::Icon::ChevronDown, 12.0)
                .style(StylePatch::new().foreground(colors.muted))
        },
    )
    .fallback(move || {
        w::icon(&u_shut, w::Icon::ChevronRight, 12.0)
            .style(StylePatch::new().foreground(colors.muted))
    })
    .el()
    .layout(|l| {
        l.width = Length::Px(12.0);
        l.height = Length::Px(12.0);
        l.shrink = 0.0;
    });
    let label_text = w::text(ui, "")
        .text({
            let label = label.clone();
            move || label()
        })
        .nowrap()
        .style(
            StylePatch::new()
                .foreground(label_color)
                .text_size(12.0)
                .weight(weight),
        )
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        });
    let mut button = ui
        .el("button")
        .variant("quiet")
        .row()
        .align(Align::Center)
        .justify(Justify::Start)
        .gap(6.0)
        .padding(Insets {
            top: 4.0,
            end: 8.0,
            bottom: 4.0,
            start: 2.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(28.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().foreground(colors.muted))
        .child(chevron)
        .child(label_text);
    if show_count {
        let count_text = count.clone();
        button = button.child(
            w::text(ui, "")
                .text(move || count_text())
                .nowrap()
                .style(
                    StylePatch::new()
                        .foreground(colors.muted)
                        .text_size(10.0)
                        .weight(500.0),
                ),
        );
    }
    let label_sem = label.clone();
    let count_sem = count;
    let exp_sem = expanded;
    button
        .on_click(on_press)
        .semantics(move || {
            let name = if show_count {
                format!("{}, {}", label_sem(), count_sem())
            } else {
                label_sem()
            };
            Semantics::new(Role::Button)
                .label(name)
                .expanded(exp_sem())
                .action(Action::Click)
        })
}

fn session_options(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let toggle = studio.clone();
    let u = ui.clone();
    let open = studio.clone();
    let header = disclosure_button(
        ui,
        move || messages().text(&u, "session-options"),
        move || open.options_open.get(),
        move || {
            toggle.options_open.update(|v| *v = !*v);
        },
        || String::new(),
        false,
        false,
    );
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
                    w::button(&u, move || messages().text(&u_in, "sidebar-import"), {
                        let st = st.clone();
                        move || import_session(&st)
                    })
                        .variant("quiet")
                        .layout(|l| {
                            l.grow = 1.0;
                            l.basis = Length::Px(0.0);
                        }),
                )
                .child(
                    w::button(&u, move || messages().text(&u_ex, "sidebar-export"), {
                        let st = st.clone();
                        move || export_active(&st)
                    })
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
            &st.depth.project_order.get(),
            &st.depth.session_order.get(),
        ));
    });
    let ui_rows = ui.clone();
    let studio_rows = studio.clone();
    let col = w::for_each(&col, rows, TreeItem::key, move |item| match item {
        TreeItem::Header(path) => project_header(&ui_rows, &studio_rows, path.clone()),
        TreeItem::Session { id, title } => {
            let session = studio_rows
                .sessions
                .get()
                .into_iter()
                .find(|s| s.id == *id)
                .unwrap_or_else(|| Session {
                    id: id.clone(),
                    title: title.clone(),
                    ..Session::fresh("", "")
                });
            session_row(&ui_rows, &studio_rows, &session)
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
    Session { id: String, title: String },
}

impl TreeItem {
    fn key(&self) -> String {
        match self {
            TreeItem::Header(path) => format!("h:{}", path.as_deref().unwrap_or("-")),
            TreeItem::Session { id, title } => format!("s:{id}:{title}"),
        }
    }
}

fn tree_items(
    sessions: &[Session],
    projects: &[Project],
    filter: Filter,
    query: &str,
    project_order: &[String],
    session_order: &[(String, Vec<String>)],
) -> Vec<TreeItem> {
    let mut out = Vec::new();
    for (project, members) in ordered_groups(
        sessions,
        projects,
        filter,
        query,
        project_order,
        session_order,
    ) {
        out.push(TreeItem::Header(project.path.clone()));
        if !project.collapsed {
            for session in members {
                out.push(TreeItem::Session {
                    id: session.id,
                    title: session.title,
                });
            }
        }
    }
    out
}

fn project_header(ui: &Ui, studio: &Studio, path: Option<String>) -> El {
    let base_ui = ui.clone();
    let base_path = path.clone();
    let exp_st = studio.clone();
    let exp_path = path.clone();
    let count_st = studio.clone();
    let count_path = path.clone();
    let press = studio.clone();
    let press_path = path.clone();
    let button = disclosure_button(
        ui,
        move || project_base(&base_ui, &base_path),
        move || project_stats(&exp_st, &exp_path).1,
        move || press.toggle_collapsed(press_path.as_deref()),
        move || project_stats(&count_st, &count_path).0.to_string(),
        true,
        true,
    );
    let u = ui.clone();
    let st_drop = studio.clone();
    let st_menu = studio.clone();
    let drop_path = path.clone();
    let menu_path = path.clone();
    w::context_menu(
        &button
            .on_drop(
                |drag, _| {
                    if drag.local::<String>().is_some() {
                        DropEffect::Move
                    } else {
                        DropEffect::None
                    }
                },
                move |drag, _, _| {
                    let Some(id) = drag.local::<String>().cloned() else {
                        return;
                    };
                    match drop_path.as_deref() {
                        Some(PINNED_GROUP_KEY) => {}
                        Some(path) => assign_project(&st_drop, &id, Some(path.to_string())),
                        None => assign_project(&st_drop, &id, None),
                    }
                },
            ),
        move || project_menu(&u, &st_menu, menu_path.clone()),
    )
}

fn project_base(ui: &Ui, path: &Option<String>) -> String {
    match path.as_deref() {
        None => messages().text(ui, "no-project"),
        Some(PINNED_GROUP_KEY) => messages().text(ui, "pinned-strip"),
        Some(_) => crate::studio::project_label(path.as_deref()),
    }
}

fn project_stats(studio: &Studio, path: &Option<String>) -> (usize, bool) {
    let groups = ordered_groups(
        &studio.sessions.get(),
        &studio.projects.get(),
        studio.filter.get(),
        &studio.query.get(),
        &studio.depth.project_order.get(),
        &studio.depth.session_order.get(),
    );
    let found = groups.iter().find(|(p, _)| &p.path == path);
    (
        found.map(|(_, members)| members.len()).unwrap_or(0),
        found.map(|(project, _)| !project.collapsed).unwrap_or(true),
    )
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
    let project_path = session.project_path.clone();

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
        .child(session_title(ui, studio, &id, &title))
        .child(session_sub(ui, studio, &id, count, age));

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

    let folder_slot: std::rc::Rc<std::cell::RefCell<Option<El>>> =
        std::rc::Rc::new(std::cell::RefCell::new(None));
    let folder_btn = {
        let u = ui.clone();
        let st = studio.clone();
        let sid = id.clone();
        let slot = folder_slot.clone();
        let ui_menu = ui.clone();
        tool_button_sized(
            ui,
            w::Icon::Folder,
            move || messages().text(&u, "move-project"),
            move || {
                let Some(anchor) = slot.borrow().clone() else {
                    return;
                };
                w::menu(&ui_menu, &anchor, project_move_menu(&ui_menu, &st, &sid));
            },
            28.0,
            16.0,
        )
    };
    *folder_slot.borrow_mut() = Some(folder_btn.clone());

    let studio_close = studio.clone();
    let close_id = id.clone();

    let row = w::row(ui)
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
        }));

    let drag_id = id.clone();
    let row = row.draggable(
        move || {
            Some(
                Drag::new(DragData::default())
                    .with_local(drag_id.clone())
                    .allow(&[DropEffect::Move]),
            )
        },
        |_| {},
    );
    let drop_id = id.clone();
    let st_place = studio.clone();
    let row = row.on_drop(
        |drag, _| {
            if drag.local::<String>().is_some() {
                DropEffect::Move
            } else {
                DropEffect::None
            }
        },
        move |drag, _, _| {
            let Some(from) = drag.local::<String>().cloned() else {
                return;
            };
            place_session(&st_place, &from, Some(&drop_id));
        },
    );
    let u_menu = ui.clone();
    let st_menu = studio.clone();
    let menu_id = id.clone();
    let menu_project = project_path;
    let u_keys = ui.clone();
    w::context_menu(&row, move || {
        session_menu(&u_menu, &st_menu, &menu_id, menu_project.clone())
    })
    .keyboard_alternative(move || messages().text(&u_keys, "keyboard-reorder"))
}

fn session_title(ui: &Ui, studio: &Studio, id: &str, title: &str) -> El {
    let st_when = studio.clone();
    let id_when = id.to_string();
    let u_edit = ui.clone();
    let st_edit = studio.clone();
    let id_edit = id.to_string();
    let seed = title.to_string();
    let u_show = ui.clone();
    let shown = title.to_string();
    show(
        ui,
        move || st_when.depth.rename_id.get().as_deref() == Some(id_when.as_str()),
        move || rename_field(&u_edit, &st_edit, &id_edit, &seed),
    )
    .fallback(move || {
        w::text(&u_show, shown.clone())
            .nowrap()
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            })
    })
    .el()
}

fn rename_field(ui: &Ui, studio: &Studio, id: &str, seed: &str) -> El {
    let field = w::text_input(ui, messages().text(ui, "rename-session"));
    field.set_value(seed);
    let submitted = field.clone();
    let st_sub = studio.clone();
    let id_sub = id.to_string();
    let field = field.on_submit(move || {
        rename_session(&st_sub, &id_sub, &submitted.value());
    });
    let st_esc = studio.clone();
    field
        .el()
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .on_key(move |e| {
            if e.pressed && e.key == Key::Escape {
                st_esc.depth.rename_id.set(None);
                true
            } else {
                false
            }
        })
}

fn session_sub(ui: &Ui, studio: &Studio, id: &str, count: u32, age: Option<String>) -> El {
    let line = match age {
        Some(a) => format!(
            "{a}  ·  {count} {}",
            if count == 1 { "msg" } else { "msgs" }
        ),
        None => format!("{count} {}", if count == 1 { "msg" } else { "msgs" }),
    };
    let st = studio.clone();
    let sid = id.to_string();
    let u = ui.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(6.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, line).variant("muted").nowrap().layout(|l| {
                l.shrink = 1.0;
                l.min_width = Length::Px(0.0);
            }),
        )
        .child(
            show(
                ui,
                move || {
                    should_show_busy_badge(st.depth.running_count()) && st.depth.session_running(&sid)
                },
                move || w::text(&u, messages().text(&u, "busy-badge")).variant("small").nowrap(),
            )
            .el(),
        )
}

fn project_menu(ui: &Ui, studio: &Studio, path: Option<String>) -> Vec<w::MenuItem> {
    let Some(path) = path else {
        return Vec::new();
    };
    if path == PINNED_GROUP_KEY {
        return Vec::new();
    }
    let st_up = studio.clone();
    let path_up = path.clone();
    let st_down = studio.clone();
    let path_down = path.clone();
    let st_rm = studio.clone();
    vec![
        w::MenuItem::new(messages().text(ui, "move-up"), move || {
            move_project_order(&st_up, &path_up, -1);
        }),
        w::MenuItem::new(messages().text(ui, "move-down"), move || {
            move_project_order(&st_down, &path_down, 1);
        }),
        w::MenuItem::separator(),
        w::MenuItem::new(messages().text(ui, "remove-project"), move || {
            ask_remove_project(&st_rm, &path);
        }),
    ]
}

fn project_move_menu(ui: &Ui, studio: &Studio, id: &str) -> Vec<w::MenuItem> {
    let st_none = studio.clone();
    let id_none = id.to_string();
    let mut items = vec![w::MenuItem::new(messages().text(ui, "no-project"), move || {
        assign_project(&st_none, &id_none, None);
    })];
    for project in studio.projects.get() {
        let Some(path) = project.path else {
            continue;
        };
        if path == PINNED_GROUP_KEY {
            continue;
        }
        let label = crate::studio::project_label(Some(&path));
        let st = studio.clone();
        let sid = id.to_string();
        items.push(w::MenuItem::new(label, move || {
            assign_project(&st, &sid, Some(path));
        }));
    }
    items
}

fn session_menu(ui: &Ui, studio: &Studio, id: &str, project: Option<String>) -> Vec<w::MenuItem> {
    let st_ren = studio.clone();
    let id_ren = id.to_string();
    let st_up = studio.clone();
    let id_up = id.to_string();
    let st_down = studio.clone();
    let id_down = id.to_string();
    let mut items = vec![
        w::MenuItem::new(messages().text(ui, "rename"), move || {
            st_ren.depth.rename_id.set(Some(id_ren));
        }),
        w::MenuItem::new(messages().text(ui, "move-up"), move || {
            move_session_order(&st_up, &id_up, -1);
        }),
        w::MenuItem::new(messages().text(ui, "move-down"), move || {
            move_session_order(&st_down, &id_down, 1);
        }),
    ];
    if project.is_some() {
        let st = studio.clone();
        let sid = id.to_string();
        items.push(w::MenuItem::new(
            messages().text(ui, "remove-from-project"),
            move || assign_project(&st, &sid, None),
        ));
    }
    items.push(w::MenuItem::separator());
    let st_del = studio.clone();
    let id_del = id.to_string();
    let u = ui.clone();
    items.push(w::MenuItem::new(messages().text(ui, "delete-session"), move || {
        let title = messages().text(&u, "delete-session");
        let cancel = messages().text(&u, "cancel");
        let st_yes = st_del.clone();
        let sid = id_del.clone();
        w::dialog(
            &u,
            title.clone(),
            session_delete_body(),
            vec![
                w::DialogButton::new(cancel, "quiet", || {}),
                w::DialogButton::new(title, "danger_solid", move || st_yes.close(&sid)),
            ],
        );
    }));
    items
}
