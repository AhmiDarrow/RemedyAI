//! Memory and Skills, opened from the chat header.

use super::logic;
use super::{
    ask_archive_unused, ask_delete_skill, export_skills_pack, import_skills_pack, install_named,
    load_memory, load_more_memory, load_skills, open_skill, save_skill, search_library,
    search_memory, trust_skill, MemoryRow, SidePanel, SkillRow,
};
use crate::messages;
use crate::studio::Studio;
use crate::tokens::{self, section_kicker};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

/// The right-hand column. Empty until Memory or Skills is opened.
pub fn side(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || st.depth.panel.get() != SidePanel::None,
        move || {
            let studio = st.clone();
            match studio.depth.panel.get() {
                SidePanel::Memory => memory(&u, studio.clone()),
                SidePanel::Skills => skills(&u, studio),
                SidePanel::None => w::column(&u),
            }
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
    if !studio.depth.memory_ready.get() && !studio.depth.memory_loading.get() {
        load_memory(&studio);
    }
    frame(ui, &studio, "memory", memory_sheet(ui, studio.clone()))
}

/// Instant Memory list: a loading state, paginated rows, and search (`query=`).
pub(crate) fn memory_sheet(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let field = w::text_input(ui, messages().text(ui, "memory-search"));
    let f = field.clone();
    let st = studio.clone();
    let field = field.on_submit(move || search_memory(&st, &f.value()));
    let list = w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
    let u = ui.clone();
    let list = w::for_each(
        &list,
        studio.depth.memory_rows.clone(),
        |row: &MemoryRow| {
            if row.id.is_empty() {
                format!("{}\0{}", row.title, row.body)
            } else {
                row.id.clone()
            }
        },
        {
            let u = u.clone();
            move |row| memory_row(&u, row.clone())
        },
    );
    let st_status = studio.clone();
    let u_status = ui.clone();
    let status = w::text(ui, "")
        .variant("muted")
        .text(move || {
            if st_status.depth.memory_loading.get() && st_status.depth.memory_rows.get().is_empty() {
                return messages().text(&u_status, "memory-loading");
            }
            let err = st_status.depth.memory_error.get();
            if !err.trim().is_empty() && st_status.depth.memory_rows.get().is_empty() {
                return err;
            }
            if st_status.depth.memory_rows.get().is_empty() && st_status.depth.memory_ready.get() {
                return messages().text(&u_status, "memory-empty");
            }
            st_status.depth.memory_body.get()
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
    let st_more = studio.clone();
    let u_more = ui.clone();
    let u_btn = ui.clone();
    let st_btn = studio.clone();
    let more = show(
        ui,
        move || st_more.depth.memory_has_more.get() && !st_more.depth.memory_loading.get(),
        move || {
            let st = st_btn.clone();
            let u = u_btn.clone();
            let label_ui = u_more.clone();
            w::button(
                &u,
                move || messages().text(&label_ui, "memory-load-more"),
                move || load_more_memory(&st),
            )
            .variant("quiet")
        },
    )
    .el();
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(field.el().layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        }))
        .child(status)
        .child(
            w::scroll(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                })
                .child(list),
        )
        .child(more)
}

fn memory_row(ui: &Ui, row: MemoryRow) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let title = if row.title.trim().is_empty() {
        row.kind.clone()
    } else {
        row.title.clone()
    };
    let body = row.body.clone();
    w::column(ui)
        .gap(unit * 0.5)
        .padding(Insets::all(unit * 1.5))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.card)
                .border(colors.line.unwrap_or(colors.muted))
                .border_width(1.0)
                .radius(6.0),
        )
        .child(
            w::text(ui, title)
                .variant("strong")
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child(
            w::text(ui, body)
                .variant("muted")
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        )
}

pub(crate) fn skills_sheet(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let tab = studio.depth.panel_tab.clone();
    let st_in = studio.clone();
    let st_lib = studio.clone();
    let tabs = w::row(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        })
        .child(skill_mode_tab(
            ui,
            &studio,
            "installed",
            "skills-installed",
            {
                let tab = tab.clone();
                let st = st_in.clone();
                move || {
                    tab.set("installed".into());
                    load_skills(&st);
                }
            },
        ))
        .child(skill_mode_tab(
            ui,
            &studio,
            "library",
            "skills-library",
            {
                let tab = tab.clone();
                let st = st_lib;
                move || {
                    tab.set("library".into());
                    search_library(&st, "");
                }
            },
        ));
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
        move || library(&u_lib, st.clone())
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
    col
}

fn skills(ui: &Ui, studio: Studio) -> El {
    frame(ui, &studio, "skills", skills_sheet(ui, studio.clone()))
}

fn installed(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let visible: Signal<Vec<SkillRow>> = Signal::new(Vec::new());
    let vis_out = visible.clone();
    let st_filter = studio.clone();
    let hook = ui.el("skills-visible").layout(|l| {
        l.width = Length::Px(0.0);
        l.height = Length::Px(0.0);
    });
    let _ = hook.clone().bind(move || {
        let next = logic::filter_skills(
            &st_filter.depth.skills.get(),
            &st_filter.depth.skill_filter.get(),
        );
        let _ = vis_out.try_set(next);
    });
    let list = w::column(ui).gap(2.0).layout(|l| l.width = Length::Percent(1.0));
    let u = ui.clone();
    let list = w::for_each(&list, visible, |row: &SkillRow| row.name.clone(), {
        let st = studio.clone();
        let u = u.clone();
        move |row| skill_button(&u, st.clone(), row.clone())
    });
    let notice_st = studio.clone();
    let editor = skill_editor(ui, studio.clone());
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(hook)
        .child(skill_filters(ui, &studio))
        .child(pack_toolbar(ui, &studio))
        .child(w::text(ui, "").text(move || notice_st.depth.notice.get()).variant("muted"))
        .child(skill_budget_block(ui, &studio))
        .child(
            w::scroll(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                    l.max_height = Length::Px(220.0);
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                })
                .child(list),
        )
        .child(editor)
}

fn skill_mode_tab(
    ui: &Ui,
    studio: &Studio,
    id: &'static str,
    label: &'static str,
    on_click: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let st = studio.clone();
    let u = ui.clone();
    w::button(ui, move || messages().text(&u, label), on_click)
        .variant("quiet")
        .style(move || {
            let on = if id == "library" {
                st.depth.panel_tab.get() == "library"
            } else {
                st.depth.panel_tab.get() != "library"
            };
            if on {
                StylePatch::new()
                    .background(colors.accent)
                    .foreground(colors.on_accent)
            } else {
                StylePatch::new()
                    .background(colors.card)
                    .foreground(colors.text)
                    .border(colors.line.unwrap_or(colors.muted))
                    .border_width(1.0)
            }
        })
}

fn skill_filters(ui: &Ui, studio: &Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        })
        .child(skill_filter_chip(ui, studio, logic::SKILL_FILTER_ALL, "skills-filter-all"))
        .child(skill_filter_chip(ui, studio, logic::SKILL_FILTER_ACTIVE, "skills-filter-active"))
        .child(skill_filter_chip(
            ui,
            studio,
            logic::SKILL_FILTER_QUARANTINE,
            "skills-filter-quarantine",
        ))
        .child(skill_filter_chip(ui, studio, logic::SKILL_FILTER_LEARNED, "skills-filter-learned"))
        .child(skill_filter_chip(
            ui,
            studio,
            logic::SKILL_FILTER_ARCHIVED,
            "skills-filter-archived",
        ))
}

fn skill_filter_chip(ui: &Ui, studio: &Studio, id: &'static str, label: &'static str) -> El {
    let colors = ui.theme().colors.clone();
    let st = studio.clone();
    let st_click = studio.clone();
    let u = ui.clone();
    w::button(
        ui,
        move || messages().text(&u, label),
        move || st_click.depth.skill_filter.set(id.to_string()),
    )
    .variant("quiet")
    .style(move || {
        if st.depth.skill_filter.get() == id {
            StylePatch::new()
                .background(colors.accent)
                .foreground(colors.on_accent)
        } else {
            StylePatch::new()
                .background(colors.card)
                .foreground(colors.muted)
                .border(colors.line.unwrap_or(colors.muted))
                .border_width(1.0)
        }
    })
}

fn skill_budget_block(ui: &Ui, studio: &Studio) -> El {
    let colors = ui.theme().colors.clone();
    let depth = tokens::depth_for(&colors);
    let unit = ui.theme().spacing.unit;
    let line = colors.line.unwrap_or(colors.muted);
    let st_show = studio.clone();
    let st_banner = studio.clone();
    let st_line = studio.clone();
    let st_learn = studio.clone();
    let st_ratio = studio.clone();
    let u = ui.clone();
    let earth = depth.earth;
    let well = depth.well;
    let banner_colors = colors.clone();
    let body = w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .child(show(ui, {
            let st = st_banner.clone();
            move || !st.depth.skill_budget.get().banner.is_empty()
        }, {
            let st = st_banner;
            let earth = earth;
            let colors = banner_colors;
            move || {
                w::text(&u, "")
                    .text(move || st.depth.skill_budget.get().banner)
                    .style(
                        StylePatch::new()
                            .foreground(earth)
                            .background(colors.card)
                            .border(earth)
                            .border_width(1.0),
                    )
                    .padding(Insets::all(unit * 2.0))
                    .layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                    })
            }
        }))
        .child(
            w::column(ui)
                .gap(unit)
                .padding(Insets::all(unit * 2.5))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                    l.wrap = true;
                })
                .style(
                    StylePatch::new()
                        .background(colors.card)
                        .border(line)
                        .border_width(1.0),
                )
                .child(
                    w::text(ui, messages().text(ui, "skills-active-budget")).style(section_kicker(&colors)),
                )
                .child(w::text(ui, "").text(move || {
                    logic::skill_budget_line(&st_line.depth.skill_budget.get())
                }))
                .child({
                    let fill = ui
                        .el("budget-fill")
                        .style(StylePatch::new().background(earth))
                        .layout({
                            let st = st_ratio;
                            move |l| {
                                l.width = Length::Percent(logic::skill_budget_ratio(
                                    &st.depth.skill_budget.get(),
                                ));
                                l.height = Length::Px(8.0);
                                l.min_width = Length::Px(0.0);
                            }
                        });
                    w::row(ui)
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                            l.height = Length::Px(8.0);
                        })
                        .style(StylePatch::new().background(well).border(line).border_width(1.0))
                        .child(fill)
                })
                .child(
                    w::text(ui, "")
                        .variant("muted")
                        .text(move || logic::skill_learning_line(&st_learn.depth.skill_learning.get()))
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        }),
                ),
        );
    show(
        ui,
        move || {
            let budget = st_show.depth.skill_budget.get();
            let learn = st_show.depth.skill_learning.get();
            logic::show_skill_budget(&budget, &learn)
        },
        move || body.clone(),
    )
    .into()
}

fn pack_toolbar(ui: &Ui, studio: &Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let st_ex = studio.clone();
    let st_im = studio.clone();
    let st_ar = studio.clone();
    let u = ui.clone();
    w::row(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        })
        .child(w::button(ui, {
            let u = u.clone();
            move || messages().text(&u, "skills-export")
        }, move || export_skills_pack(&st_ex)).variant("quiet"))
        .child(w::button(ui, {
            let u = u.clone();
            move || messages().text(&u, "skills-import")
        }, move || import_skills_pack(&st_im)).variant("quiet"))
        .child(w::button(ui, {
            let u = u.clone();
            move || messages().text(&u, "skills-archive-unused")
        }, move || ask_archive_unused(&st_ar)).variant("quiet"))
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

fn skill_button(ui: &Ui, studio: Studio, row: SkillRow) -> El {
    let colors = ui.theme().colors.clone();
    let depth = tokens::depth_for(&colors);
    let tag = logic::skill_tag(&row);
    let tag_color = match tag {
        "archived" => colors.muted,
        "quarantine" | "learned · probation" => depth.earth,
        _ => colors.accent,
    };
    let line = colors.line.unwrap_or(colors.muted);
    let unit = ui.theme().spacing.unit;
    let st = studio;
    let open = row.name.clone();
    let title = row.name.clone();
    let detail = row.description;
    w::row(ui)
        .align(Align::Start)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        })
        .style(StylePatch::new().border(line).border_width(1.0))
        .padding(Insets::all(unit * 2.0))
        .child(
            w::column(ui)
                .gap(unit)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                    l.basis = Length::Px(0.0);
                })
                .child(
                    w::button(ui, title, move || open_skill(&st, &open))
                        .variant("quiet")
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        }),
                )
                .child(
                    w::text(ui, detail)
                        .variant("muted")
                        .layout(|l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        }),
                ),
        )
        .child(
            w::text(ui, tag)
                .style(StylePatch::new().foreground(tag_color).border(line).border_width(1.0))
                .padding(Insets::symmetric(unit, unit * 1.5)),
        )
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
