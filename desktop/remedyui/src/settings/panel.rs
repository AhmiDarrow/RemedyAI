//! The settings panel: mode, search, categories, and the section list.
//!
//! Closed, the shell shows this with `show`, so it takes no space. While it is
//! open, sections stay mounted and hide, so a typed API key is not thrown away.
//! Theme changes apply from here only when this is a real window (`preview` is
//! false). Studio goldens never build this panel.

use remedy_ui::core::Insets;
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Effect;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::bits;
use super::form;
use super::logic;
use super::prefs;
use super::sections;
use super::state::{self, Settings};
use crate::tool_button;

pub fn view(ui: &Ui, s: Settings, rail: bool) -> El {
    watch_appearance(s);
    watch_search(s);
    watch_force(s);
    let colors = ui.theme().colors.clone();
    let root = w::column(ui)
        .gap(form::gap(ui))
        .padding(Insets::all(form::gap(ui) * 2.0))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text),
        )
        .child(header(ui, s))
        .child(search_row(ui, s))
        .child(categories(ui, s))
        .child(w::text(ui, move || hint_line(s)).variant("small"))
        .child(banner(ui, s))
        .child(body(ui, s))
        .child(footer(ui, s));
    if rail {
        root.layout(|l| {
            l.width = Length::Percent(1.0);
            // Shrink to fit a tight window, but never collapse to an empty strip.
            l.min_width = Length::Px(220.0);
            l.max_width = Length::Px(320.0);
            l.height = Length::Percent(1.0);
            l.shrink = 1.0;
            l.grow = 0.0;
            l.basis = Length::Px(320.0);
        })
        .clip()
    } else {
        bits::fill(root)
    }
}

pub fn open_panel(s: Settings) {
    if crate::settings::take_open_models() {
        s.last_section.set("provider".into());
        s.category.set("models".into());
    }
    if crate::settings::take_open_privacy() {
        s.last_section.set("privacy".into());
        s.category.set("privacy".into());
    }
    let id = s.last_section.get();
    if let Some(sec) = logic::section(&id) {
        s.category.set(sec.category.to_string());
        if sec.advanced_only {
            s.mode.set("advanced".into());
        }
        s.force.set(Some(id.clone()));
        let mut open = s.open_ids.get();
        if !open.iter().any(|x| x == &id) {
            open.push(id);
            s.open_ids.set(open);
        }
    }
    s.open.set(true);
    s.ensure_loaded();
}

pub fn close_panel(s: Settings) {
    s.open.set(false);
    state::clear_search(s);
    let open: Vec<String> = s
        .open_ids
        .get()
        .into_iter()
        .filter(|id| id != "vision" && id != "rmb")
        .collect();
    s.open_ids.set(open);
    s.vision_loaded.set(false);
    s.rmb_loaded.set(false);
}

fn watch_appearance(s: Settings) {
    Effect::new(move || {
        let _ = (
            s.theme_id.get(),
            s.density.get(),
            s.font_scale.get(),
            s.reduce_motion.get(),
            s.show_presence.get(),
            s.high_contrast.get(),
            s.custom_accent.get(),
            s.font_ui.get(),
            s.font_heading.get(),
            s.font_reading.get(),
            s.font_accent.get(),
            s.font_code.get(),
            s.mode.get(),
            s.privacy_shield.get(),
            s.skip_quit_warn.get(),
            s.last_section.get(),
        );
        if s.preview {
            return;
        }
        prefs::apply(s);
        prefs::persist(s);
    });
}

/// Expands sections that match the current query. A collapse stays collapsed
/// until the query itself changes.
fn watch_search(s: Settings) {
    Effect::new(move || {
        let q = s.search.get();
        if !logic::searching(&q) {
            return;
        }
        let mut open = s.open_ids.get_untracked().unwrap_or_default();
        let mut changed = false;
        for sec in logic::SECTIONS {
            if logic::section_matches(sec, &q) && !open.iter().any(|id| id == sec.id) {
                open.push(sec.id.to_string());
                changed = true;
            }
        }
        if changed {
            s.open_ids.set(open);
        }
    });
}

fn watch_force(s: Settings) {
    Effect::new(move || {
        let Some(id) = s.force.get() else { return };
        let mut open = s.open_ids.get_untracked().unwrap_or_default();
        if !open.iter().any(|x| x == &id) {
            open.push(id);
            s.open_ids.set(open);
        }
    });
}

fn header(ui: &Ui, s: Settings) -> El {
    let row = w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(form::gap(ui))
        .fill_width()
        .child(settings_mode_chips(ui, s));
    // The workspace rail already has a header and a collapse button.
    if super::studio_rail_owns_close() {
        row
    } else {
        row.child(tool_button(ui, w::Icon::Close, "Close", move || {
            close_panel(s)
        }))
    }
}

fn search_row(ui: &Ui, s: Settings) -> El {
    let field = w::text_input(ui, "Search all settings").placeholder("Search all settings…");
    state::store_search(field.clone());
    let watch = field.clone();
    field.observe_text(move || {
        s.search.set_if_changed(watch.value());
    });
    let sync = field.clone();
    let input = field.el();
    input.clone().bind(move || {
        let next = s.search.get();
        if sync.value() != next {
            sync.set_value(&next);
        }
    });
    w::row(ui)
        .align(Align::Center)
        .gap(form::gap(ui))
        .fill_width()
        .child(w::icon(ui, w::Icon::Search, 16.0))
        .child(input.grow(1.0).layout(|l| {
            l.min_width = Length::Px(0.0);
            l.shrink = 1.0;
        }))
        .on_key(move |ev| {
            if ev.pressed && ev.key == Key::Escape && logic::searching(&s.search.get()) {
                state::clear_search(s);
                return true;
            }
            false
        })
}

fn categories(ui: &Ui, s: Settings) -> El {
    let mut first = w::row(ui).gap(form::gap(ui)).fill_width();
    let mut second = w::row(ui).gap(form::gap(ui)).fill_width();
    for (i, cat) in logic::CATEGORIES.iter().enumerate() {
        let (id, label, _) = *cat;
        let button = category_button(ui, s, id, label);
        if i < 3 {
            first = first.child(button);
        } else {
            second = second.child(button);
        }
    }
    form::stack(ui).child(first).child(second)
}

fn settings_mode_chips(ui: &Ui, s: Settings) -> El {
    w::row(ui)
        .align(Align::Center)
        .gap(crate::tokens::SPACE * 2.0)
        .child(mode_chip(ui, s, "simple", "Simple"))
        .child(mode_chip(ui, s, "advanced", "Advanced"))
}

fn mode_chip(ui: &Ui, s: Settings, id: &'static str, label: &'static str) -> El {
    let colors = ui.theme().colors.clone();
    w::button(ui, label, move || s.mode.set(id.to_string()))
        .variant("quiet")
        .style(move || crate::tokens::chip(&colors, s.mode.get() == id).weight(550.0))
}

fn category_button(ui: &Ui, s: Settings, id: &'static str, label: &'static str) -> El {
    let colors = ui.theme().colors.clone();
    w::button(ui, label, move || {
        s.category.set(id.to_string());
        state::clear_search(s);
    })
    .variant("quiet")
    .style(move || crate::tokens::chip(&colors, s.category.get() == id).weight(550.0))
}

fn hint_line(s: Settings) -> String {
    let q = s.search.get();
    if logic::searching(&q) {
        "Showing matches across all settings.".into()
    } else {
        logic::category_hint(&s.category.get()).to_string()
    }
}

fn banner(ui: &Ui, s: Settings) -> El {
    let line = form::notice(ui, "warn", move || s.banner.get());
    bits::visible_when(line, move || !s.banner.get().trim().is_empty())
}

fn body(ui: &Ui, s: Settings) -> El {
    show(ui, move || s.phase.get() == "ready", {
        let ui = ui.clone();
        move || {
            w::scroll(&ui).child(sections::all(&ui, s)).layout(|l| {
                l.width = Length::Percent(1.0);
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            })
        }
    })
    .fallback({
        let ui = ui.clone();
        move || {
            show(&ui, move || s.phase.get() == "error", {
                let ui = ui.clone();
                move || {
                    form::stack(&ui)
                        .child(w::empty_state(&ui, "Settings couldn’t load", move || {
                            let err = s.error.get();
                            if err.is_empty() {
                                "Settings couldn’t load".into()
                            } else {
                                err
                            }
                        }))
                        .child(form::action(&ui, "Try again", "primary", move || s.retry()))
                }
            })
            .fallback({
                let ui = ui.clone();
                move || w::empty_state(&ui, "Loading settings…", "Reading your preferences.")
            })
            .el()
        }
    })
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    })
}

fn footer(ui: &Ui, s: Settings) -> El {
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::End)
        .fill_width()
        .child(
            w::button(
                ui,
                move || -> String {
                    if s.saving.get() {
                        "Saving…".into()
                    } else {
                        "Save".into()
                    }
                },
                move || s.save(),
            )
            .variant("quiet")
            .style(crate::tokens::primary_button(&ui.theme().colors))
            .disabled(move || s.saving.get() || s.phase.get() != "ready"),
        )
}
