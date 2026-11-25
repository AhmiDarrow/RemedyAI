//! Grove's chrome: the top bar, the three home views, a goal room, and a side job.
//!
//! The page body is one keyed child. Switching Home, Alongside, or Storyline
//! replaces that child. The talk field lives inside the page, so a new message
//! does not rebuild it. Grove / Studio switching lives in the window title bar
//! so it stays on screen on both surfaces, including at narrow widths.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Axis, Justify, Length, Position};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{States, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};


use super::api::{self, GoalLink, LifeGoal, LifeTask};
use super::logic::{self, Moment, MomentKind};
use super::state::{Grove, HomeTab, Place, RoomTab, Surface};
use super::{chat, child, decision, head, stage};

/// Grove, or a way back plus the Studio shell.
pub(super) fn root(
    ui: &Ui,
    grove: Grove,
    app: Option<App>,
    offer: Option<crate::setup::Offer>,
) -> El {
    // Flex percentages under the 36 px title bar do not cap this page, so it
    // would lay out at its content height and the composer would fall off the
    // bottom. Cap Grove to the remaining window height (`window_size` is tracked).
    // Studio stays in the document flow (not an absolute overlay) so its rails
    // and resize handles keep a real size.
    const TITLE_BAR: f32 = 36.0;
    let grove_flag = grove.clone();
    let frame_grove = grove.clone();
    let frame_app = app.clone();
    let frame_offer = offer.clone();
    let ui_frame = ui.clone();
    let grove_on = show(
        ui,
        move || matches!(grove_flag.surface.get(), Surface::Grove),
        move || {
            let el = frame(
                &ui_frame,
                frame_grove.clone(),
                frame_app.clone(),
                frame_offer.clone(),
            );
            let cap = el.clone();
            let cap_ui = ui_frame.clone();
            el.bind(move || {
                // Hosted chrome already reserves the 36px title bar and the
                // ~44px status dock. Cap to that remaining column so the
                // composer and Routines are not painted under the dock.
                let dock = if crate::chrome::dock_in_host() { 44.0 } else { 0.0 };
                let h = (cap_ui.window_size().height - TITLE_BAR - dock).max(160.0);
                let _ = cap.clone().layout(|l| {
                    l.height = Length::Px(h);
                    l.max_height = Length::Px(h);
                    l.min_height = Length::Px(0.0);
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.basis = Length::Px(0.0);
                    l.width = Length::Percent(1.0);
                });
            })
        },
    )
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.shrink = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.max_height = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
        l.width = Length::Percent(1.0);
    });
    let studio_flag = grove.clone();
    let studio_grove = grove.clone();
    let on_grove = grove.clone();
    let ui_studio = ui.clone();
    let studio_on = show(
        ui,
        move || matches!(studio_flag.surface.get(), Surface::Studio),
        move || studio_face(&ui_studio, studio_grove.clone()),
    )
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    });
    let body = w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(grove_on)
        .child(studio_on);
    w::row(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(body)
        .child(crate::settings::grove_rail(ui, move || {
            matches!(on_grove.surface.get(), Surface::Grove)
        }))
        .on_key_capture(crate::settings::shell_chord_current)
        .on_key(crate::settings::shell_escape_current)
}

/// The Grove window: bars, the open page, the shared status line, and the
/// decision card. Morning goldens call this directly.
pub(super) fn frame(
    ui: &Ui,
    grove: Grove,
    app: Option<App>,
    offer: Option<crate::setup::Offer>,
) -> El {
    crate::fonts::prepare_theme(ui, &crate::Fonts::load());
    let colors = ui.theme().colors.clone();
    let pages = Signal::new(vec![page_key(&grove)]);
    let last = Rc::new(RefCell::new(page_key(&grove)));
    let watch = ui.el("grove-pages").bind({
        let g = grove.clone();
        let pages = pages;
        move || {
            let next = page_key(&g);
            if *last.borrow() != next {
                *last.borrow_mut() = next.clone();
                pages.set(vec![next]);
            }
        }
    });
    let slot = w::column(ui).layout(|l| {
        l.grow = 1.0;
        l.shrink = 1.0;
        l.basis = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    let page_grove = grove.clone();
    let page_app = app.clone();
    w::for_each(&slot, pages, |k: &String| k.clone(), {
        let ui = ui.clone();
        move |key| render_page(&ui, page_grove.clone(), page_app.clone(), key)
    });
    w::column(ui)
        .layout(|l| {
            l.position = Position::Relative;
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
            l.max_height = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.bg)
                .foreground(colors.text),
        )
        .child(home_bar(ui, grove.clone()))
        .child(goal_bar(ui, grove.clone()))
        .child(child_bar(ui, grove.clone()))
        .child(zero(watch))
        .child(offer_slot(ui, offer))
        .child(slot)
        .child(if crate::chrome::dock_in_host() {
            ui.el("status-hosted").layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Px(0.0);
            })
        } else {
            w::column(ui)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(w::divider(ui, false))
                .child(crate::status::status_bar(ui, grove.studio.clone(), || {
                    crate::settings::toggle()
                }))
        })
        .child(decision::layer(ui, grove.clone()))
        .child(child::bubble_layer(ui, grove.clone()))
        .child(zero(split_saver(ui, grove)))
}

fn studio_face(ui: &Ui, grove: Grove) -> El {
    // The title-bar Grove/Studio switcher is the way back; don't add a second strip.
    crate::shell(ui, grove.studio.clone()).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.height = Length::Percent(1.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    })
}

fn page_key(grove: &Grove) -> String {
    match grove.place.get() {
        Place::Home => {
            let tab = match grove.home_tab.get() {
                HomeTab::Plots => "plots",
                HomeTab::Alongside => "alongside",
                HomeTab::Storyline => "story",
            };
            format!("home\u{1f}{tab}")
        }
        Place::Goal(id) => {
            let tab = match grove.room_tab.get() {
                RoomTab::Alongside => "alongside",
                RoomTab::Storyline => "story",
            };
            format!("goal\u{1f}{id}\u{1f}{tab}")
        }
        Place::Child(id) => format!("child\u{1f}{id}"),
    }
}

fn render_page(ui: &Ui, grove: Grove, app: Option<App>, key: &String) -> El {
    let mut parts = key.split('\u{1f}');
    match parts.next() {
        Some("goal") => {
            let id = parts.next().unwrap_or("").to_string();
            if parts.next() == Some("story") {
                storyline(ui, grove, false)
            } else {
                goal_alongside(ui, grove, app, id)
            }
        }
        Some("child") => {
            let id = parts.next().unwrap_or("").to_string();
            child::room(ui, grove, id)
        }
        _ => match parts.next() {
            Some("alongside") => home_alongside(ui, grove, app),
            Some("story") => storyline(ui, grove, true),
            _ => home_plots(ui, grove, app),
        },
    }
}

fn home_bar(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || matches!(g.place.get(), Place::Home), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let unit = ui.theme().spacing.unit;
            w::column(&ui)
                .child(
                    w::row(&ui)
                        .align(Align::Center)
                        .gap(unit * 3.0)
                        .padding(bar_insets(unit))
                        .layout(|l| l.width = Length::Percent(1.0))
                        .child(home_tabs(&ui, grove.clone()))
                        .child(spacer(&ui)),
                )
                .child(w::divider(&ui, false))
        }
    })
    .el()
}

fn goal_bar(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || matches!(g.place.get(), Place::Goal(_)), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let unit = ui.theme().spacing.unit;
            let title = w::text(&ui, "").variant("strong").text({
                let g = grove.clone();
                move || match g.place.get() {
                    Place::Goal(id) => g
                        .goal(&id)
                        .map(|goal| goal.title)
                        .filter(|t| !t.is_empty())
                        .unwrap_or_else(|| "Goal".into()),
                    _ => String::new(),
                }
            });
            let g = grove.clone();
            let dot = show(&ui, move || g.streaming.get(), {
                let ui = ui.clone();
                move || {
                    let colors = ui.theme().colors.clone();
                    ui.el("grove-live")
                        .layout(|l| {
                            l.width = Length::Px(8.0);
                            l.height = Length::Px(8.0);
                        })
                        .style(StylePatch::new().background(colors.accent).radius(8.0))
                        .semantics(Semantics::new(Role::Status).label("Remedy is working"))
                }
            })
            .el();
            let crumb = w::row(&ui)
                .align(Align::Center)
                .gap(unit)
                .child(crumb_back(&ui, grove.clone(), "Grove"))
                .child(w::text(&ui, "▸").variant("muted"))
                .child(title)
                .child(dot);
            let tabs = w::row(&ui)
                .align(Align::Center)
                .gap(unit * 0.5)
                .semantics(Semantics::new(Role::Group).label("This goal"))
                .child(room_tab(
                    &ui,
                    grove.clone(),
                    RoomTab::Alongside,
                    tr(&ui, "grove-alongside"),
                    false,
                ))
                .child(room_tab(
                    &ui,
                    grove.clone(),
                    RoomTab::Storyline,
                    tr(&ui, "grove-storyline"),
                    true,
                ));
            w::column(&ui)
                .child(
                    w::row(&ui)
                        .align(Align::Center)
                        .gap(unit * 2.0)
                        .padding(bar_insets(unit))
                        .layout(|l| l.width = Length::Percent(1.0))
                        .child(crumb)
                        .child(tabs)
                        .child(spacer(&ui)),
                )
                .child(w::divider(&ui, false))
        }
    })
    .el()
}

fn child_bar(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || matches!(g.place.get(), Place::Child(_)), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let unit = ui.theme().spacing.unit;
            let here = w::text(&ui, "").variant("strong").text({
                let g = grove.clone();
                move || match g.place.get() {
                    Place::Child(id) => g
                        .children
                        .get()
                        .into_iter()
                        .find(|c| c.child_id == id)
                        .map(|c| c.label)
                        .unwrap_or_else(|| "Side job".into()),
                    _ => String::new(),
                }
            });
            let crumb = w::row(&ui)
                .align(Align::Center)
                .gap(unit)
                .child(crumb_back(&ui, grove.clone(), "Grove"))
                .child(w::text(&ui, "▸").variant("muted"))
                .child(crumb_named(&ui, grove.clone()))
                .child(w::text(&ui, "▸").variant("muted"))
                .child(here);
            w::column(&ui)
                .child(
                    w::row(&ui)
                        .align(Align::Center)
                        .gap(unit * 2.0)
                        .padding(bar_insets(unit))
                        .layout(|l| l.width = Length::Percent(1.0))
                        .child(crumb)
                        .child(spacer(&ui)),
                )
                .child(w::divider(&ui, false))
        }
    })
    .el()
}

fn crumb_back(ui: &Ui, grove: Grove, label: &str) -> El {
    let g = grove;
    w::button(ui, label.to_string(), move || g.go_home()).variant("quiet")
}

fn crumb_named(ui: &Ui, grove: Grove) -> El {
    let press = grove.clone();
    w::button(ui, move || grove.partner(), move || press.go_home()).variant("quiet")
}

fn offer_slot(ui: &Ui, offer: Option<crate::setup::Offer>) -> El {
    match offer {
        Some(offer) => crate::setup::offer_banner(ui, offer),
        None => ui.el("grove-no-offer"),
    }
}

fn brand(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    // Do not shrink the name at ordinary window widths; ellipsize only when
    // the bar itself is narrower than the word plus the monogram.
    let name = w::text(ui, "")
        .variant("strong")
        .nowrap()
        .text(move || grove.partner())
        .layout(|l| l.shrink = 0.0);
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .layout(|l| l.shrink = 0.0)
        .child(crate::logo::mark(ui, 18.0))
        .child(name)
}

fn home_tabs(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 0.5)
        .semantics(Semantics::new(Role::Group).label("Grove views"))
        .child(home_tab(
            ui,
            grove.clone(),
            HomeTab::Plots,
            "Home".into(),
            "Your goals",
        ))
        .child(home_tab(
            ui,
            grove.clone(),
            HomeTab::Alongside,
            tr(ui, "grove-alongside"),
            "Live stage — I drive the browser, you watch (shop, forms, email)",
        ))
        .child(home_tab(
            ui,
            grove,
            HomeTab::Storyline,
            tr(ui, "grove-storyline"),
            "Everything we say and do, in order, in plain words",
        ))
}

fn home_tab(ui: &Ui, grove: Grove, tab: HomeTab, label: String, tip: &str) -> El {
    let press = grove.clone();
    let watch = grove.clone();
    let on = Rc::new(move || watch.home_tab.get() == tab);
    let b = place_tab(ui, label.clone(), on, move || press.set_home_tab(tab));
    w::tooltip(&b, tip);
    let seen = grove;
    b.semantics(move || {
        Semantics::new(Role::Button)
            .label(label.clone())
            .checked(seen.home_tab.get() == tab)
            .action(Action::Click)
    })
}

fn room_tab(ui: &Ui, grove: Grove, tab: RoomTab, label: String, _story: bool) -> El {
    let g = grove.clone();
    let watch = grove.clone();
    let on = Rc::new(move || watch.room_tab.get() == tab);
    let b = place_tab(ui, label.clone(), on, move || g.set_room_tab(tab));
    let seen = grove;
    b.semantics(move || {
        Semantics::new(Role::Button)
            .label(label.clone())
            .checked(seen.room_tab.get() == tab)
            .action(Action::Click)
    })
}

/// Second-level tabs: typographic, with a 1px underline when selected.
/// Distinct from the boxed Grove/Studio switcher in the title bar.
fn place_tab(
    ui: &Ui,
    label: String,
    on: Rc<dyn Fn() -> bool>,
    press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let b = w::button(ui, label, press).variant("quiet");
    let paint = b.clone();
    let on_btn = on.clone();
    let colors_btn = colors.clone();
    let b = b.bind(move || {
        let selected = on_btn();
        paint.state(States::SELECTED, selected);
        let _ = paint.clone().style(
            StylePatch::new()
                .background(colors_btn.bg.with_alpha(0.0))
                .border_width(0.0)
                .foreground(if selected {
                    colors_btn.accent
                } else {
                    colors_btn.muted
                })
                .weight(if selected { 600.0 } else { 500.0 }),
        );
    });
    let rule = ui.el("tab-underline").layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Px(1.0);
        l.min_height = Length::Px(1.0);
    });
    let paint_rule = rule.clone();
    let colors_rule = colors;
    let rule = rule.bind(move || {
        let selected = on();
        let _ = paint_rule.clone().style(StylePatch::new().background(if selected {
            colors_rule.accent
        } else {
            colors_rule.bg.with_alpha(0.0)
        }));
    });
    w::column(ui).align(Align::Stretch).child(b).child(rule)
}

fn chip_style(colors: &remedy_ui::style::ColorRoles, on: bool) -> StylePatch {
    if on {
        StylePatch::new()
            .foreground(colors.accent)
            .background(colors.accent.mix_oklab(colors.card, 0.88))
            .border(colors.accent)
            .border_width(1.0)
    } else {
        StylePatch::new()
            .foreground(colors.text)
            .border(colors.line.unwrap_or(colors.muted))
            .border_width(1.0)
    }
}


fn studio_handoff_card(ui: &Ui, grove: Grove) -> El {
    let watch = grove.clone();
    show(ui, move || watch.handoff.get().is_some(), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let colors = ui.theme().colors.clone();
            let unit = ui.theme().spacing.unit;
            let Some(item) = grove.handoff.get() else {
                return ui.el("grove-handoff-empty");
            };
            let title_key = match item.kind {
                logic::StudioKind::Build => "grove-handoff-build",
                logic::StudioKind::Plan => "grove-handoff-plan",
                logic::StudioKind::Chat => "grove-handoff-chat",
            };
            let title = crate::messages().text(&ui, title_key);
            let mut prompt = item.prompt.clone();
            if prompt.chars().count() > 140 {
                prompt = prompt.chars().take(137).collect::<String>() + "…";
            }
            let quote = w::text(&ui, prompt)
                .style(crate::tokens::typo(crate::tokens::BODY).foreground(colors.text))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                });
            // Live status of the background session: working until its turn ends.
            let note = w::text(&ui, "")
                .text({
                    let (g, ui) = (grove.clone(), ui.clone());
                    move || {
                        let key = if g.handoff_working() {
                            "grove-handoff-working"
                        } else {
                            "grove-handoff-ready"
                        };
                        crate::messages().text(&ui, key)
                    }
                })
                .style(crate::tokens::typo(crate::tokens::META).foreground(colors.muted))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                });
            let g = grove.clone();
            let open = w::button(&ui, crate::messages().text(&ui, "grove-handoff-open"), move || {
                g.open_handoff();
            })
            .variant("quiet")
            .semantics(
                Semantics::new(Role::Button)
                    .label(crate::messages().text(&ui, "grove-handoff-open"))
                    .action(Action::Click),
            );
            let body = w::column(&ui)
                .gap(unit * 2.0)
                .align(Align::Start)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .child(quote)
                .child(note)
                .child(w::row(&ui).gap(unit).child(open).child({
                    let g = grove.clone();
                    w::button(&ui, crate::messages().text(&ui, "grove-handoff-dismiss"), move || g.dismiss_handoff())
                        .variant("quiet")
                }));
            crate::tokens::info_panel(&ui, title, body).layout(|l| {
                l.margin = Insets {
                    top: unit,
                    end: unit * 3.0,
                    bottom: unit * 2.0,
                    start: unit * 3.0,
                };
                // No fixed width: 100% plus the margins ran past the column.
                l.min_width = Length::Px(0.0);
            })
        }
    })
    .el()
    .layout(|l| l.width = Length::Percent(1.0))
}

fn home_plots(ui: &Ui, grove: Grove, app: Option<App>) -> El {
    let unit = ui.theme().spacing.unit;
    let scroll = w::scroll(ui)
        .child(space_kicker(ui))
        .child(away(ui, grove.clone()))
        .child(studio_handoff_card(ui, grove.clone()));
    let scroll = scroll
        .child(life_banner(ui, grove.clone()))
        .child(head::head_block(ui, grove.clone()))
        .child(section_label(ui, grove.clone()))
        .child(plots(ui, grove.clone()))
        .child(head::house_block(ui, grove.clone()));
    // The split sizes this pane. Without a height, the scroll grows with its
    // content and paints over the status bar. 16 px matches the desktop split.
    let scroll = scroll
        .padding(Insets {
            top: 0.0,
            end: 0.0,
            bottom: unit * 6.0,
            start: 0.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.align_self = Some(Align::Stretch);
        })
        .semantics(Semantics::new(Role::Region).label("Home plots"));
    // The widget clamps a drag to 0.05–0.95. React's home divider stops at
    // 0.28–0.72. 0.44, the saved start, is inside both.
    let chat = chat::pane(ui, grove.clone(), app, chat::RoomKind::Home);
    w::split(ui, Axis::Row, scroll, chat, grove.home_split).layout(|l| {
        l.grow = 1.0;
        l.shrink = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    })
}

fn home_alongside(ui: &Ui, grove: Grove, app: Option<App>) -> El {
    // Room divider: the widget allows 0.05–0.95. React stops at 0.30–0.80.
    // 0.6, the saved start, is inside both.
    let top = stage::stage(ui, grove.clone());
    let talk = chat::pane(ui, grove.clone(), app, chat::RoomKind::Alongside);
    w::split(ui, Axis::Column, top, talk, grove.room_split).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    })
}

fn goal_alongside(ui: &Ui, grove: Grove, app: Option<App>, id: String) -> El {
    let top = stage::stage(ui, grove.clone());
    let talk = w::column(ui)
        .layout(|l| {
            l.min_height = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(room_hint(ui, grove.clone()))
        .child(chat::pane(ui, grove.clone(), app, chat::RoomKind::Goal(id)));
    let split = w::split(ui, Axis::Column, top, talk, grove.room_split).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    w::column(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(life_banner(ui, grove))
        .child(split)
}

fn space_kicker(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    w::text(ui, "Your space")
        .style(crate::tokens::typo(crate::tokens::SECTION).foreground(colors.muted))
        .layout(|l| {
            l.margin = Insets {
                top: unit * 3.0,
                end: unit * 3.0,
                bottom: unit,
                start: unit * 3.0,
            };
        })
}

fn intro_line(grove: &Grove) -> String {
    let n = grove.goals.get().len();
    if n == 0 {
        return "Bring a goal, a question, or something you’d like a hand with.".into();
    }
    let word = if n == 1 { "goal" } else { "goals" };
    let needs = grove.ordered_decisions().len();
    if needs > 0 {
        format!("{n} {word} in progress · {needs} waiting for you")
    } else {
        format!("{n} {word} in progress")
    }
}

fn away(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let watch_last = grove.clone();
    let last_body = grove.clone();
    let last = show(ui, move || last_step_line(&watch_last).is_some(), {
        let ui = ui.clone();
        move || {
            let clear_g = last_body.clone();
            let clear = w::button(&ui, "×", move || clear_g.clear_activity())
                .variant("quiet")
                .semantics(
                    Semantics::new(Role::Button)
                        .label("Clear last activity")
                        .action(Action::Click),
                );
            let line_g = last_body.clone();
            w::row(&ui)
                .align(Align::Center)
                .gap(ui.theme().spacing.unit)
                .child(w::text(&ui, "").text(move || last_step_line(&line_g).unwrap_or_default()))
                .child(clear)
        }
    })
    .el();
    let needs_watch = grove.clone();
    let needs_body = grove;
    let needs = show(ui, move || !needs_watch.ordered_decisions().is_empty(), {
        let ui = ui.clone();
        move || {
            let label_g = needs_body.clone();
            let press = needs_body.clone();
            w::button(
                &ui,
                move || {
                    let n = label_g.ordered_decisions().len();
                    let s = if n == 1 { "" } else { "s" };
                    format!("Needs you: {n} decision{s} waiting")
                },
                move || press.tuck(false),
            )
        }
    })
    .el();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 3.0,
            bottom: unit,
            start: unit * 3.0,
        })
        .child(last)
        .child(needs)
}

fn last_step_line(grove: &Grove) -> Option<String> {
    if grove.goals.get().is_empty() {
        return None;
    }
    let step = grove.board.get().and_then(|b| b.last_step)?;
    if step.did.is_empty() {
        return None;
    }
    if step.goal.is_empty() {
        Some(format!("Last: {}", step.did))
    } else {
        Some(format!("Last: {} — {}", step.did, step.goal))
    }
}

fn life_banner(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || {
            g.life_task
                .get()
                .as_ref()
                .is_some_and(api::life_task_visible)
        },
        {
            let ui = ui.clone();
            let grove = grove.clone();
            move || life_body(&ui, grove.clone())
        },
    )
    .el()
}

fn life_body(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let g = grove.clone();
    let status = w::text(ui, "").variant("strong").text({
        let ui = ui.clone();
        move || {
            g.life_task
                .get()
                .map(|t| life_status(&ui, &t))
                .unwrap_or_default()
        }
    });
    let g = grove.clone();
    let goal = w::text(ui, "").text(move || g.life_task.get().map(|t| t.goal).unwrap_or_default());
    let g = grove.clone();
    let step = w::text(ui, "").variant("muted").text({
        let ui = ui.clone();
        move || {
            g.life_task
                .get()
                .map(|t| life_step(&ui, &t))
                .unwrap_or_default()
        }
    });
    let g = grove.clone();
    let headline = w::text(ui, "").text(move || {
        g.life_task
            .get()
            .map(|t| life_headline(&t))
            .unwrap_or_default()
    });
    let ask_watch = grove.clone();
    let ask_body = grove.clone();
    let ask = show(
        ui,
        move || ask_watch.life_task.get().as_ref().is_some_and(life_asks),
        {
            let ui = ui.clone();
            move || {
                let g_yes = ask_body.clone();
                let g_no = ask_body.clone();
                w::row(&ui)
                    .gap(ui.theme().spacing.unit)
                    .child(w::button(&ui, tr(&ui, "life-task-yes"), move || {
                        g_yes.act_life("yes")
                    }))
                    .child(
                        w::button(&ui, tr(&ui, "life-task-no"), move || g_no.act_life("no"))
                            .variant("quiet"),
                    )
            }
        },
    )
    .el();
    let g = grove;
    let explain = w::button(ui, tr(ui, "life-task-explain"), move || {
        g.act_life("explain")
    })
    .variant("quiet");
    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 3.0,
            bottom: unit,
            start: unit * 3.0,
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .radius(ui.theme().radii.medium),
        )
        .semantics(Semantics::new(Role::Group).label(tr(ui, "life-task-region")))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                .child(status)
                .child(goal)
                .child(spacer(ui))
                .child(step),
        )
        .child(headline)
        .child(w::row(ui).gap(unit).child(ask).child(explain))
}

fn life_status(ui: &Ui, task: &LifeTask) -> String {
    match task.status.to_ascii_lowercase().as_str() {
        "need_you" => tr(ui, "life-task-needs-you"),
        "done" => tr(ui, "life-task-done"),
        "blocked" => tr(ui, "life-task-blocked"),
        _ => tr(ui, "life-task-working"),
    }
}

fn life_step(ui: &Ui, task: &LifeTask) -> String {
    if task.step > 0 && task.total > 0 {
        format!("{} {}/{}", tr(ui, "life-task-step"), task.step, task.total)
    } else {
        String::new()
    }
}

fn life_headline(task: &LifeTask) -> String {
    let spoken = task.spoken.trim();
    if !spoken.is_empty() {
        return spoken.to_string();
    }
    if task.step > 0 && task.total > 0 {
        let title = if task.title.trim().is_empty() {
            "the next step"
        } else {
            task.title.trim()
        };
        return format!("Step {} of {} — {title}.", task.step, task.total);
    }
    if !task.goal.trim().is_empty() {
        format!("Toward {}.", task.goal.trim())
    } else {
        String::new()
    }
}

fn life_asks(task: &LifeTask) -> bool {
    task.choices.iter().any(|c| c.eq_ignore_ascii_case("yes"))
        || task.status.eq_ignore_ascii_case("need_you")
}

fn section_label(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    w::text(ui, "")
        .text(move || {
            if grove.goals.get().is_empty() {
                "A few places to start".to_string()
            } else {
                "Your goals".to_string()
            }
        })
        .style(crate::tokens::typo(crate::tokens::SECTION).foreground(colors.text))
        .layout(|l| {
            l.margin = Insets {
                top: unit * 2.0,
                end: unit * 3.0,
                bottom: unit * 2.0,
                start: unit * 3.0,
            };
        })
}

fn plots(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let grid = w::wrap(ui).gap(unit * 1.5).padding(Insets {
        top: 0.0,
        end: unit * 3.0,
        bottom: unit * 2.0,
        start: unit * 3.0,
    });
    let rows = grove.clone();
    w::for_each(&grid, rows.goals, |g: &LifeGoal| g.id.clone(), {
        let ui = ui.clone();
        move |goal| plot_card(&ui, &rows, &goal.id)
    });
    let watch = grove.clone();
    let sample = grove.clone();
    let examples = show(ui, move || watch.goals.get().is_empty(), {
        let ui = ui.clone();
        move || example_row(&ui, &sample)
    })
    .el();
    w::column(ui)
        .child(grid)
        .child(examples)
        .child(seed(ui, grove))
}

fn example_row(ui: &Ui, grove: &Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let mut row = w::wrap(ui).gap(unit * 1.5).padding(Insets {
        top: 0.0,
        end: unit * 3.0,
        bottom: 0.0,
        start: unit * 3.0,
    });
    for (icon, title, hint) in logic::example_plots() {
        let g = grove.clone();
        let title = (*title).to_string();
        let open = title.clone();
        let colors = ui.theme().colors.clone();
        let b = plot_shell(
            ui,
            w::column(ui)
                .gap(unit * 2.0)
                .align(Align::Start)
                .child(super::marks::mark(ui, *icon, 32.0))
                .child(
                    w::text(ui, title.clone())
                        .style(crate::tokens::typo(crate::tokens::BODY).weight(600.0).foreground(colors.text)),
                )
                .child(
                    w::text(ui, *hint)
                        .style(crate::tokens::typo(crate::tokens::META).foreground(colors.muted)),
                )
                .child(
                    w::text(ui, "Start here →")
                        .style(crate::tokens::typo(crate::tokens::META).foreground(colors.accent)),
                ),
        )
        .on_click(move || {
            let _ = g.plant(&open);
        })
        .semantics(
            Semantics::new(Role::Button)
                .label(format!("Start {title}"))
                .action(Action::Click),
        );
        row = row.child(b);
    }
    row
}

fn seed(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove;
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .child(w::text(ui, "Something else in mind?").variant("muted"))
        .child(
            w::button(ui, "Ask Remedy →", move || g.focus_composer())
                .variant("quiet"),
        )
}

fn plot_card(ui: &Ui, grove: &Grove, id: &str) -> El {
    let unit = ui.theme().spacing.unit;
    let id = id.to_string();
    let g = grove.clone();
    let id_open = id.clone();
    let open = w::column(ui)
        .gap(unit * 0.5)
        .align(Align::Start)
        .child(w::text(ui, "").variant("muted").text({
            let g = grove.clone();
            let id = id.clone();
            move || {
                goal_of(&g, &id)
                    .map(|goal| {
                        let kind = if goal.horizon.is_empty() {
                            if goal.status.is_empty() {
                                "growing".into()
                            } else {
                                goal.status
                            }
                        } else {
                            goal.horizon
                        };
                        kind
                    })
                    .unwrap_or_default()
            }
        }))
        .child(w::text(ui, "").variant("strong").text({
            let g = grove.clone();
            let id = id.clone();
            move || goal_of(&g, &id).map(|goal| goal.title).unwrap_or_default()
        }))
        .child(plot_next(ui, grove.clone(), id.clone()))
        .child(plot_update(ui, grove.clone(), id.clone()))
        .child(
            w::text(ui, "Continue →").style(StylePatch::new().foreground(ui.theme().colors.accent)),
        )
        .on_click(move || g.open_goal(&id_open))
        .semantics({
            let g = grove.clone();
            let id = id.clone();
            move || {
                let title = goal_of(&g, &id).map(|goal| goal.title).unwrap_or_default();
                Semantics::new(Role::Button)
                    .label(format!("Open goal: {title}"))
                    .action(Action::Click)
            }
        });
    let ui_r = ui.clone();
    let g = grove.clone();
    let id_r = id.clone();
    let rename = w::button(ui, "Rename", move || {
        let title = goal_of(&g, &id_r)
            .map(|goal| goal.title)
            .unwrap_or_default();
        open_rename(&ui_r, &g, &id_r, &title);
    })
    .variant("quiet");
    let g = grove.clone();
    let id_x = id.clone();
    let remove = w::button(ui, "Remove", move || g.remove_goal(&id_x)).variant("quiet");
    let actions = w::row(ui)
        .align(Align::Center)
        .justify(Justify::End)
        .gap(unit)
        .child(rename)
        .child(remove);
    plot_shell(
        ui,
        w::column(ui)
            .gap(unit)
            .child(actions)
            .child(open)
            .child(plot_foot(ui, grove.clone(), id)),
    )
}

fn plot_next(ui: &Ui, grove: Grove, id: String) -> El {
    let g = grove.clone();
    let id_w = id.clone();
    show(
        ui,
        move || {
            goal_of(&g, &id_w).is_some_and(|goal| {
                !goal.next_action.is_empty() || !goal.done_looks_like.is_empty()
            })
        },
        {
            let ui = ui.clone();
            move || {
                w::text(&ui, "").variant("muted").text({
                    let g = grove.clone();
                    let id = id.clone();
                    move || {
                        let Some(goal) = goal_of(&g, &id) else {
                            return String::new();
                        };
                        if !goal.next_action.is_empty() {
                            format!("Next: {}", goal.next_action)
                        } else {
                            format!("Done looks like: {}", goal.done_looks_like)
                        }
                    }
                })
            }
        },
    )
    .el()
}

fn plot_update(ui: &Ui, grove: Grove, id: String) -> El {
    let g = grove.clone();
    let id_w = id.clone();
    show(
        ui,
        move || link_of(&g, &id_w).is_some_and(|l| !l.last_update.is_empty()),
        {
            let ui = ui.clone();
            move || {
                let g = grove.clone();
                let id = id.clone();
                w::text(&ui, "").variant("muted").text(move || {
                    link_of(&g, &id)
                        .map(|l| format!("{} · {}", logic::state_words(&l.state), l.last_update))
                        .unwrap_or_default()
                })
            }
        },
    )
    .el()
}

fn plot_foot(ui: &Ui, grove: Grove, id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    let id_a = id.clone();
    let auto = w::button(ui, "On its own", move || {
        let on = link_of(&g, &id_a).is_some_and(|l| l.autopilot);
        g.toggle_autopilot(&id_a, !on);
    });
    w::tooltip(
        &auto,
        "Keep going on its own — Remedy takes a step now and then, and stops when she needs you",
    );
    let paint = auto.clone();
    let g = grove.clone();
    let id_p = id.clone();
    let auto = auto.bind(move || {
        let on = link_of(&g, &id_p).is_some_and(|l| l.autopilot);
        paint.state(States::CHECKED, on);
        paint.state(States::SELECTED, on);
    });
    let g = grove.clone();
    let id_s = id.clone();
    let auto = auto.semantics(move || {
        let on = link_of(&g, &id_s).is_some_and(|l| l.autopilot);
        let title = goal_of(&g, &id_s)
            .map(|goal| goal.title)
            .unwrap_or_default();
        Semantics::new(Role::Switch)
            .label(format!("Keep going on {title} on its own"))
            .checked(on)
            .action(Action::Click)
    });
    let g = grove.clone();
    let id_st = id.clone();
    let state = show(
        ui,
        move || link_of(&g, &id_st).is_some_and(|l| !l.state.is_empty() && l.state != "idle"),
        {
            let ui = ui.clone();
            let g = grove.clone();
            let id = id.clone();
            move || {
                let g = g.clone();
                let id = id.clone();
                w::text(&ui, "").variant("muted").text(move || {
                    link_of(&g, &id)
                        .map(|l| logic::state_words(&l.state).to_string())
                        .unwrap_or_default()
                })
            }
        },
    )
    .el();
    let g = grove.clone();
    let id_d = id.clone();
    let step = show(
        ui,
        move || {
            let done = link_of(&g, &id_d).is_some_and(|l| l.state == "done");
            let open = goal_of(&g, &id_d).is_none_or(|goal| goal.status != "done");
            done && open
        },
        {
            let ui = ui.clone();
            let g = grove.clone();
            let id = id.clone();
            move || {
                let g = g.clone();
                let id = id.clone();
                let b =
                    w::button(&ui, "Mark complete", move || g.mark_complete(&id)).variant("quiet");
                w::tooltip(&b, "Remedy thinks this is finished — confirm it");
                b
            }
        },
    )
    .fallback({
        let ui = ui.clone();
        let g = grove;
        let id = id.clone();
        move || {
            let g = g.clone();
            let id = id.clone();
            let b = w::button(&ui, "Step now", move || g.step_now(&id)).variant("quiet");
            w::tooltip(&b, "Remedy takes one step on this now, in the background");
            b
        }
    })
    .el();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(auto)
        .child(state)
        .child(step)
}

fn plot_shell(ui: &Ui, body: El) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    body.padding(Insets::all(unit * 2.0))
        .layout(|l| {
            l.min_width = Length::Px(210.0);
            l.grow = 1.0;
            l.basis = Length::Px(210.0);
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text)
                .border(crate::tokens::hairline(&colors))
                .border_width(1.0)
                .radius(0.0),
        )
}

fn open_rename(ui: &Ui, grove: &Grove, id: &str, title: &str) {
    let id = id.to_string();
    let field = w::text_input(ui, format!("Rename {title}"));
    field.set_value(title);
    field.select_all();
    let slot: Rc<RefCell<Option<w::Modal>>> = Rc::new(RefCell::new(None));
    let field_s = field.clone();
    let g = grove.clone();
    let slot_s = slot.clone();
    let save = w::button(ui, "Save", move || {
        let next = field_s.value();
        let next = next.trim();
        if !next.is_empty() {
            g.rename_goal(&id, next);
        }
        if let Some(m) = slot_s.borrow().clone() {
            m.close();
        }
    });
    let slot_c = slot.clone();
    let cancel = w::button(ui, "Cancel", move || {
        if let Some(m) = slot_c.borrow().clone() {
            m.close();
        }
    })
    .variant("quiet");
    let form = w::column(ui)
        .gap(ui.theme().spacing.unit)
        .child(w::heading(ui, 2, "Rename this plot"))
        .child(field.el())
        .child(
            w::row(ui)
                .gap(ui.theme().spacing.unit)
                .child(cancel)
                .child(save),
        );
    let modal = w::modal(ui, "Rename this plot", form, true);
    *slot.borrow_mut() = Some(modal);
}

fn room_hint(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(
        ui,
        move || {
            let ex = logic::latest_exchange(&g.messages.get());
            ex.you.is_none() && ex.remedy.is_none() && g.partial.get().is_empty()
        },
        {
            let ui = ui.clone();
            let g = grove;
            move || {
                let g = g.clone();
                let unit = ui.theme().spacing.unit;
                w::text(&ui, "")
                    .variant("muted")
                    .text(move || {
                        if g.loading.get() {
                            "Opening this goal’s room…".to_string()
                        } else {
                            "Say what you’d like to do — I’ll drive, you watch.".to_string()
                        }
                    })
                    .layout(|l| {
                        l.margin = Insets {
                            top: unit * 2.0,
                            end: unit * 2.0,
                            bottom: 0.0,
                            start: unit * 2.0,
                        };
                    })
            }
        },
    )
    .el()
}

fn storyline(ui: &Ui, grove: Grove, home: bool) -> El {
    let moments: Signal<Vec<Moment>> = Signal::new(Vec::new());
    let watch = ui.el("grove-moments").bind({
        let g = grove.clone();
        let moments = moments;
        move || {
            moments.set(logic::messages_to_moments(&g.messages.get()));
        }
    });
    let unit = ui.theme().spacing.unit;
    let list = w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 3.0));
    let rows = grove.clone();
    w::for_each(&list, moments, |m: &Moment| m.id.clone(), {
        let ui = ui.clone();
        move |m| moment_row(&ui, &rows, m)
    });
    let g = grove.clone();
    let quiet = moments;
    let empty = show(
        ui,
        move || quiet.get().is_empty() && !g.loading.get() && !g.streaming.get(),
        {
            let ui = ui.clone();
            move || {
                let copy = if home {
                    "Everything we say and everything I do lands here, in order, in plain words."
                } else {
                    "The story starts when you do — everything we say and everything I do in this goal lands here, in order, in plain words."
                };
                w::text(&ui, copy).variant("muted")
            }
        },
    )
    .el();
    let live_flag = grove.clone();
    let partial_src = grove;
    let now = show(ui, move || live_flag.streaming.get(), {
        let ui = ui.clone();
        let partial = partial_src;
        move || {
            let who = tr(&ui, "grove-happening-now");
            w::column(&ui)
                .gap(ui.theme().spacing.unit * 0.4)
                .child(w::text(&ui, who).variant("muted"))
                .child(w::text(&ui, "").text(move || {
                    let t = partial.partial.get();
                    if t.is_empty() { "…".into() } else { t }
                }))
        }
    })
    .el();
    w::scroll(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(zero(watch))
        .child(empty)
        .child(list)
        .child(now)
}

fn moment_row(ui: &Ui, grove: &Grove, moment: &Moment) -> El {
    let unit = ui.theme().spacing.unit;
    let who = match moment.kind {
        MomentKind::YouSaid => "You said".to_string(),
        MomentKind::RemedySaid => format!("{} said", grove.partner()),
        MomentKind::RemedyDid => format!("{} did", grove.partner()),
    };
    w::column(ui)
        .gap(unit * 0.4)
        .child(w::text(ui, who).variant("muted"))
        .child(w::text(ui, moment.text.clone()))
}

/// Saves the split when the rounded percent changes, not on every drag frame.
fn split_saver(ui: &Ui, grove: Grove) -> El {
    let home = Rc::new(Cell::new((grove.home_split.get() * 100.0).round() as i32));
    let room = Rc::new(Cell::new((grove.room_split.get() * 100.0).round() as i32));
    ui.el("grove-splits").bind(move || {
        let h = (grove.home_split.get() * 100.0).round() as i32;
        let r = (grove.room_split.get() * 100.0).round() as i32;
        if h != home.get() || r != room.get() {
            home.set(h);
            room.set(r);
            grove.save();
        }
    })
}

fn goal_of(grove: &Grove, id: &str) -> Option<LifeGoal> {
    grove.goals.get().into_iter().find(|g| g.id == id)
}

fn link_of(grove: &Grove, id: &str) -> Option<GoalLink> {
    grove.head.get().and_then(|h| h.goal(id).cloned())
}

fn tr(ui: &Ui, id: &'static str) -> String {
    crate::messages().text(ui, id)
}

fn bar_insets(unit: f32) -> Insets {
    Insets {
        top: unit * 2.0,
        end: unit * 3.0,
        bottom: unit * 2.0,
        start: unit * 3.0,
    }
}

fn spacer(ui: &Ui) -> El {
    ui.el("grove-spacer").layout(|l| l.grow = 1.0)
}

fn zero(el: El) -> El {
    el.layout(|l| {
        l.width = Length::Px(0.0);
        l.height = Length::Px(0.0);
    })
}
