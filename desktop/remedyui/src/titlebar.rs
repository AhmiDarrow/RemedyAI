//! 36 px title bar: logo menu, drag, download chip, min / max / close.

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use crate::chrome::{Chrome, Overlay};
use crate::messages;
use crate::studio::Studio;
use crate::updates::VERSION;

const BAR: f32 = 36.0;

pub fn bar(ui: &Ui, _studio: &Studio, chrome: Chrome) -> El {
    let colors = ui.theme().colors.clone();
    let colors_logo = colors.clone();
    let unit = ui.theme().spacing.unit;
    let logo = ui.el("title-logo");
    let mark = logo.clone();
    let c = chrome;
    let u = ui.clone();
    let logo = logo
        .on_click(move || c.menu_open.update(|o| *o = !*o))
        .semantics(
            Semantics::new(Role::Button)
                .label(messages().text(&u, "menu-open"))
                .action(Action::Click),
        );
    let c = chrome;
    let u = ui.clone();
    let logo = logo.bind(move || {
        mark.clear();
        let open = c.menu_open.get();
        let _ = mark
            .clone()
            .style(StylePatch::new().background(if open {
                colors_logo.card
            } else {
                colors_logo.panel
            }))
            .row()
            .align(Align::Center)
            .gap(unit * 0.5)
            .padding(Insets {
                start: unit,
                end: unit,
                top: 0.0,
                bottom: 0.0,
            })
            .child(wordmark(&u))
            .child(w::text(&u, "▾").variant("muted"))
            .semantics(
                Semantics::new(Role::Button)
                    .label(messages().text(&u, "menu-open"))
                    .action(Action::Click),
            );
    });

    let drag = ui.el("title-drag").layout(|l| {
        l.grow = 1.0;
        l.min_width = Length::Px(0.0);
        l.height = Length::Percent(1.0);
    });
    let c = chrome;
    let ui_click = ui.clone();
    let drag = drag
        .on_click(move || {
            // Click count lives on Ui. A double-click on the drag spacer toggles maximize.
            if ui_click.click_count() >= 2 {
                c.toggle_max();
            }
        })
        .drag_region({
            let c = chrome;
            move || c.begin_move()
        });
    let chip_host = drag.clone();
    let c = chrome;
    let u = ui.clone();
    let drag = drag.bind(move || {
        // The drag element keeps its handlers; only the chip is a child.
        // Rebuilding children every caption change is enough.
        let caption = c.download_caption.get();
        chip_host.clear();
        if caption.is_empty() {
            return;
        }
        let pct = c.download_pct.get().unwrap_or(0.32);
        let pct = if pct <= 0.02 {
            0.32
        } else {
            pct.clamp(0.0, 1.0)
        };
        let _ = chip_host.clone().child(
            w::row(&u)
                .align(Align::Center)
                .gap(u.theme().spacing.unit)
                .child(w::text(&u, caption).variant("muted"))
                .child(
                    u.el("dl-track")
                        .style(
                            StylePatch::new()
                                .background(u.theme().colors.card)
                                .radius(999.0),
                        )
                        .layout(|l| {
                            l.width = Length::Px(72.0);
                            l.height = Length::Px(4.0);
                        })
                        .child(
                            u.el("dl-fill")
                                .style(
                                    StylePatch::new()
                                        .background(u.theme().colors.accent)
                                        .radius(999.0),
                                )
                                .layout(|l| {
                                    l.width = Length::Percent(pct);
                                    l.height = Length::Percent(1.0);
                                }),
                        ),
                ),
        );
    });

    let c = chrome;
    let u = ui.clone();
    w::row(ui)
        .align(Align::Center)
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text)
                .border_side(
                    remedy_ui::style::BorderEdge::Bottom,
                    remedy_ui::style::BorderSide::new(
                        1.0,
                        colors.line.unwrap_or(colors.muted.with_alpha(0.35)),
                    ),
                ),
        )
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Px(BAR);
            l.min_height = Length::Px(BAR);
            l.shrink = 0.0;
        })
        .child(logo.layout(|l| {
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        }))
        .child(surface_switch(ui, chrome))
        .child(drag)
        .child(
            w::text(ui, format!("v{VERSION}"))
                .variant("muted")
                .padding(Insets {
                    end: unit * 2.0,
                    start: unit,
                    top: 0.0,
                    bottom: 0.0,
                }),
        )
        .child(win_btn(
            ui,
            "win-minimize",
            false,
            w::Icon::Minus,
            move || c.minimize(),
        ))
        .child(win_btn_max(ui, chrome))
        .child(win_btn(&u, "win-close", true, w::Icon::Close, {
            let c = chrome;
            move || c.hide()
        }))
}

/// Icon button. The accessible name stays the translated window command.
fn win_btn(ui: &Ui, id: &'static str, close: bool, glyph: w::Icon, on: impl Fn() + 'static) -> El {
    let colors = ui.theme().colors.clone();
    let fg = if close { colors.danger } else { colors.text };
    let label = messages().text(ui, id);
    ui.el(id)
        .child(w::icon(ui, glyph, 12.0))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .style(StylePatch::new().foreground(fg))
        .layout(|l| {
            l.width = Length::Px(46.0);
            l.height = Length::Px(BAR);
            l.shrink = 0.0;
        })
        .on_click(on)
        .semantics(
            Semantics::new(Role::Button)
                .label(label)
                .action(Action::Click),
        )
}

fn win_btn_max(ui: &Ui, chrome: Chrome) -> El {
    let host = ui.el("win-max-host");
    let slot = host.clone();
    let u = ui.clone();
    let c = chrome;
    host.bind(move || {
        let maxed = c.maximized.get();
        let id = if maxed { "win-restore" } else { "win-maximize" };
        let glyph = if maxed {
            w::Icon::Restore
        } else {
            w::Icon::Maximize
        };
        slot.clear();
        let toggle = c;
        let _ = slot
            .clone()
            .child(win_btn(&u, id, false, glyph, move || toggle.toggle_max()));
    })
}

pub fn menu(ui: &Ui, studio: &Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let rows = w::column(ui)
        .padding(Insets {
            top: unit,
            bottom: unit,
            start: 0.0,
            end: 0.0,
        })
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-newSession"),
            "Ctrl+N",
            Kind::Normal,
            {
                let s = studio.clone();
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    s.add();
                }
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "surface-grove"),
            "Ctrl+1",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.show_grove();
                }
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "surface-studio"),
            "Ctrl+2",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.show_studio();
                }
            },
        ))
        .child(sep(ui))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-settings"),
            "Ctrl+,",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.open_settings();
                }
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-memory"),
            "",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.toggle(Overlay::Memory);
                }
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-skills"),
            "",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.toggle(Overlay::Skills);
                }
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-diagnostics"),
            "",
            Kind::Normal,
            {
                let c = chrome;
                let s = studio.clone();
                move || c.open_diagnostics(&s)
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-help"),
            "F1",
            Kind::Normal,
            {
                let c = chrome;
                let s = studio.clone();
                move || c.open_help(&s, crate::help_view::default_id())
            },
        ))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-openBrowser"),
            "",
            Kind::Normal,
            {
                let c = chrome;
                let u = ui.clone();
                move || {
                    c.menu_open.set(false);
                    c.open_web(&u);
                }
            },
        ))
        .child(sep(ui))
        .child(update_item(ui, chrome))
        .child(item(
            ui,
            chrome,
            format!("{} (v{VERSION})", messages().text(ui, "menu-about")),
            "",
            Kind::Normal,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.overlay.set(Overlay::About);
                }
            },
        ))
        .child(sep(ui))
        .child(item(
            ui,
            chrome,
            messages().text(ui, "menu-quit"),
            "",
            Kind::Danger,
            {
                let c = chrome;
                move || {
                    c.menu_open.set(false);
                    c.request_quit();
                }
            },
        ));
    rows.style(crate::surface_of(ui))
        .layout(|l| {
            l.min_width = Length::Px(220.0);
            l.max_width = Length::Px(320.0);
        })
        .semantics(Semantics::new(Role::Menu).label(messages().text(ui, "menu-open")))
}

enum Kind {
    Normal,
    Danger,
    Accent,
}

fn update_item(ui: &Ui, chrome: Chrome) -> El {
    let u_label = ui.clone();
    let u_go = ui.clone();
    let c = chrome;
    let label_c = chrome;
    w::button(
        ui,
        move || {
            let id = if label_c.update_available.get() {
                "menu-installUpdate"
            } else {
                "menu-checkUpdates"
            };
            messages().text(&u_label, id)
        },
        move || {
            c.menu_open.set(false);
            if c.update_available.get() {
                c.install_update(&u_go);
            } else {
                c.check_updates(&u_go);
            }
        },
    )
    .variant("quiet")
    .style(StylePatch::new().foreground({
        if chrome.update_available.get() {
            ui.theme().colors.accent
        } else {
            ui.theme().colors.text
        }
    }))
    .justify(Justify::Start)
    .layout(|l| l.width = Length::Percent(1.0))
}

fn item(
    ui: &Ui,
    _chrome: Chrome,
    label: String,
    shortcut: &str,
    kind: Kind,
    on: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let fg = match kind {
        Kind::Danger => colors.danger,
        Kind::Accent => colors.accent,
        Kind::Normal => colors.text,
    };
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit * 2.0)
        .padding(Insets {
            top: unit * 1.5,
            bottom: unit * 1.5,
            start: unit * 3.0,
            end: unit * 3.0,
        })
        .on_click(on)
        .semantics(
            Semantics::new(Role::Button)
                .label(label.clone())
                .action(Action::Click),
        )
        .child(w::text(ui, label).style(StylePatch::new().foreground(fg)))
        .child(w::text(ui, shortcut).variant("muted"))
}

fn sep(ui: &Ui) -> El {
    let colors = ui.theme().colors.clone();
    ui.el("menu-sep")
        .style(StylePatch::new().background(colors.line.unwrap_or(colors.muted.with_alpha(0.3))))
        .layout(|l| {
            l.height = Length::Px(1.0);
            l.width = Length::Percent(1.0);
        })
        .margin_y(ui.theme().spacing.unit)
}

pub(crate) fn wordmark(ui: &Ui) -> El {
    // The bundled logo.png is a 640×320 poster. Decoding it to 280×36, as this
    // bar used to, squashed it into a smear. A 36 px title bar needs the
    // circuit-R monogram and the word, not that poster.
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .layout(|l| l.shrink = 0.0)
        .child(crate::logo::mark(ui, 18.0))
        .child(w::text(ui, "Remedy").variant("strong").nowrap())
}

trait MarginY {
    fn margin_y(self, y: f32) -> Self;
}
impl MarginY for El {
    fn margin_y(self, y: f32) -> Self {
        self.layout(|l| {
            l.margin.top = y;
            l.margin.bottom = y;
        })
    }
}


/// Always-visible Grove / Studio switcher in the window title bar.
///
/// The old Studio control lived in Grove's content bar, after the brand and
/// tabs. At ordinary widths that row could push it off the right edge, and it
/// was never in the window top bar — so it also never appeared in Studio.
fn surface_switch(ui: &Ui, chrome: Chrome) -> El {
    let c = chrome;
    show(ui, move || c.grove.get().is_some(), {
        let ui = ui.clone();
        move || surface_switch_row(&ui, chrome)
    })
    .el()
    .layout(|l| {
        l.shrink = 0.0;
        l.height = Length::Percent(1.0);
    })
}

fn surface_switch_row(ui: &Ui, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .padding(Insets {
            start: unit * 2.0,
            end: unit * 2.0,
            top: 0.0,
            bottom: 0.0,
        })
        .layout(|l| {
            l.shrink = 0.0;
            l.height = Length::Percent(1.0);
        })
        .semantics(Semantics::new(Role::Group).label("Grove or Studio"))
        .child(surface_chip(ui, chrome, false))
        .child(surface_chip(ui, chrome, true))
}

fn surface_chip(ui: &Ui, chrome: Chrome, studio: bool) -> El {
    let colors = ui.theme().colors.clone();
    let id = if studio { "surface-studio" } else { "surface-grove" };
    let tip_id = if studio {
        "surface-studio-tip"
    } else {
        "surface-grove-tip"
    };
    let label = messages().text(ui, id);
    let tip = messages().text(ui, tip_id);
    let c = chrome;
    let btn = w::button(ui, label.clone(), move || {
        if studio {
            c.show_studio();
        } else {
            c.show_grove();
        }
    })
    .variant("quiet");
    let paint = btn.clone();
    let c = chrome;
    let btn = btn.bind(move || {
        let on = c
            .grove
            .get()
            .is_some_and(|g| g.on_studio() == studio);
        let _ = paint.clone().style(switch_style(&colors, on));
    });
    w::tooltip(&btn, tip);
    btn.layout(|l| l.shrink = 0.0).semantics({
        let c = chrome;
        move || {
            let on = c
                .grove
                .get()
                .is_some_and(|g| g.on_studio() == studio);
            Semantics::new(Role::Button)
                .label(label.clone())
                .selected(on)
                .action(Action::Click)
        }
    })
}

fn switch_style(colors: &remedy_ui::style::ColorRoles, on: bool) -> StylePatch {
    crate::tokens::chip(colors, on).weight(550.0)
}
