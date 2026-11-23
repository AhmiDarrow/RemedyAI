//! Memory and Skills, opened from the chat header.

use super::{
    ask_delete_skill, install_named, load_memory, load_skills, open_skill, save_skill, search_library,
    search_memory, trust_skill, SidePanel, SkillRow,
};
use crate::messages;
use crate::studio::Studio;
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

/// The right-hand column. Empty until Memory or Skills is opened.
pub fn side(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    show(
        ui,
        move || st.depth.panel.get() != SidePanel::None,
        move || match studio.depth.panel.get() {
            SidePanel::Memory => memory(ui, studio.clone()),
            SidePanel::Skills => skills(ui, studio.clone()),
            SidePanel::None => w::column(ui),
        },
    )
    .el()
}

fn frame(ui: &Ui, studio: &Studio, title_id: &'static str, body: El) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let st = studio.clone();
    let u = ui.clone();
    w::column(ui)
        .gap(unit)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| {
            l.width = Length::Px(320.0);
            l.min_width = Length::Px(280.0);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .border(line)
                .border_width(1.0),
        )
        .child(
            w::row(ui)
                .align(Align::Center)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(
                    w::text(ui, "")
                        .variant("strong")
                        .text(move || messages().text(&u, title_id))
                        .layout(|l| l.grow = 1.0),
                )
                .child(w::button(
                    ui,
                    {
                        let u = ui.clone();
                        move || messages().text(&u, "panel-close")
                    },
                    move || st.depth.panel.set(SidePanel::None),
                ).variant("quiet")),
        )
        .child(body)
}

fn memory(ui: &Ui, studio: Studio) -> El {
    let field = w::text_input(ui, messages().text(ui, "memory"));
    let f = field.clone();
    let st = studio.clone();
    let field = field.on_submit(move || search_memory(&st, &f.value()));
    let st_body = studio.clone();
    let body = w::column(ui)
        .gap(ui.theme().spacing.unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(field.el().layout(|l| l.width = Length::Percent(1.0)))
        .child(
            w::scroll(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                })
                .child(
                    w::text(ui, "")
                        .text(move || {
                            let body = st_body.depth.memory_body.get();
                            if body.trim().is_empty() {
                                "…".into()
                            } else {
                                body
                            }
                        })
                        .layout(|l| l.width = Length::Percent(1.0)),
                ),
        );
    frame(ui, &studio, "memory", body)
}

fn skills(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let tab = studio.depth.panel_tab.clone();
    let st_in = studio.clone();
    let st_lib = studio.clone();
    let u = ui.clone();
    let tabs = w::row(ui)
        .gap(unit)
        .child(w::button(
            ui,
            {
                let u = u.clone();
                move || messages().text(&u, "skills-installed")
            },
            {
                let tab = tab.clone();
                let st = st_in.clone();
                move || {
                    tab.set("installed".into());
                    load_skills(&st);
                }
            },
        ).variant("quiet"))
        .child(w::button(
            ui,
            move || messages().text(&u, "skills-library"),
            {
                let tab = tab.clone();
                move || {
                    tab.set("library".into());
                    search_library(&st_lib, "");
                }
            },
        ).variant("quiet"));
    let u_in = ui.clone();
    let u_lib = ui.clone();
    let body = show(
        ui,
        {
            let tab = tab.clone();
            move || tab.get() != "library"
        },
        {
            let st = studio.clone();
            move || installed(&u_in, st.clone())
        },
    )
    .fallback({
        let st = studio.clone();
        move || library(&u_lib, st)
    })
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    let col = w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(tabs)
        .child(body);
    frame(ui, &studio, "skills", col)
}

fn installed(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let rows = studio.depth.skills.clone();
    let list = w::column(ui).gap(2.0).layout(|l| l.width = Length::Percent(1.0));
    let u = ui.clone();
    let list = w::for_each(&list, rows, |row: &SkillRow| row.name.clone(), {
        let st = studio.clone();
        let u = u.clone();
        move |row| skill_button(&u, st.clone(), row.name, row.description.clone(), false)
    });
    let editor = skill_editor(ui, studio);
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(
            w::scroll(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.max_height = Length::Px(180.0);
                })
                .child(list),
        )
        .child(editor)
}

fn library(ui: &Ui, studio: Studio) -> El {
    let field = w::text_input(ui, messages().text(ui, "skills-library"));
    let f = field.clone();
    let st = studio.clone();
    let field = field.on_submit(move || search_library(&st, &f.value()));
    let rows = studio.depth.library_rows.clone();
    let list = w::column(ui).gap(2.0);
    let u = ui.clone();
    let list = w::for_each(&list, rows, |row: &SkillRow| row.name.clone(), {
        let st = studio.clone();
        let u = u.clone();
        move |row| {
            let id = if row.status.is_empty() {
                row.name.clone()
            } else {
                row.status.clone()
            };
            let st = st.clone();
            let label = row.name.clone();
            w::button(&u, label, move || install_named(&st, &id))
                .variant("quiet")
                .layout(|l| l.width = Length::Percent(1.0))
        }
    });
    w::column(ui)
        .gap(ui.theme().spacing.unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
        })
        .child(field.el().layout(|l| l.width = Length::Percent(1.0)))
        .child(w::scroll(ui).layout(|l| l.grow = 1.0).child(list))
}

fn skill_button(ui: &Ui, studio: Studio, name: String, detail: String, _library: bool) -> El {
    let label = if detail.is_empty() {
        name.clone()
    } else {
        format!("{name}  {detail}")
    };
    let st = studio.clone();
    let open = name.clone();
    w::button(ui, label, move || open_skill(&st, &open))
        .variant("quiet")
        .layout(|l| l.width = Length::Percent(1.0))
}

fn skill_editor(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let shown = studio.clone();
    let body = studio;
    let u_ed = ui.clone();
    show(
        ui,
        move || !shown.depth.skill_name.get().is_empty(),
        move || skill_editor_open(&u_ed, body.clone(), unit),
    )
    .el()
}

/// A fresh field each time a skill opens. Hiding the editor disposes its element,
/// so the field cannot be built once and re-parented.
fn skill_editor_open(ui: &Ui, studio: Studio, unit: f32) -> El {
    let field = w::text_area(ui, messages().text(ui, "skill-save"));
    let _ = field.el().style(StylePatch::new().mono(true)).layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_height = Length::Px(160.0);
        l.grow = 1.0;
    });
    let f = field.clone();
    let st = studio.clone();
    let seen_name = Signal::new(String::new());
    let seen_body = Signal::new(String::new());
    let _ = field.el().bind(move || {
        let name = st.depth.skill_name.get();
        let body = st.depth.skill_body.get();
        if name != seen_name.get() {
            seen_name.set(name);
            seen_body.set(String::new());
        }
        if body != seen_body.get() {
            seen_body.set(body.clone());
            f.set_value(&body);
        }
    });
    let st_name = studio.clone();
    let st_save = studio.clone();
    let f_save = field.clone();
    let st_trust = studio.clone();
    let st_del = studio.clone();
    let u_save = ui.clone();
    let u_trust = ui.clone();
    let u_del = ui.clone();
    w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(w::text(ui, "").text(move || st_name.depth.skill_name.get()))
        .child(field.el())
        .child(
            w::row(ui)
                .gap(unit)
                .child(
                    w::button(ui, move || messages().text(&u_save, "skill-save"), move || {
                        save_skill(&st_save, &f_save.value())
                    })
                    .variant("primary"),
                )
                .child(
                    w::button(
                        ui,
                        move || messages().text(&u_trust, "skill-trust"),
                        move || {
                            let name = st_trust.depth.skill_name.get();
                            trust_skill(&st_trust, &name);
                        },
                    )
                    .variant("quiet"),
                )
                .child(
                    w::button(
                        ui,
                        move || messages().text(&u_del, "skill-delete"),
                        move || {
                            let name = st_del.depth.skill_name.get();
                            ask_delete_skill(&st_del, &name);
                        },
                    )
                    .variant("quiet"),
                ),
        )
}
