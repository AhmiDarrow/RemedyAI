//! Design-8 first-run and auto-update surfaces.
//!
//! Preview only. These screens stay on the local `design/setup-update` branch
//! until the owner approves them. They follow the Dark Forest tokens and the three
//! config-driven font roles: no glow, blur, gradients, or glass.

use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::core::Color;
use remedy_ui::{El, Ui};

use crate::chrome::{self, Chrome};
use crate::fonts::Fonts;
use crate::grove;
use crate::presence::{self, Mood};
use crate::studio::Studio;
use crate::titlebar;
use crate::tokens::{self, typo, BODY, HERO, META, SECTION, space, TITLE};

fn pin_face() {
    presence::set_skin_override(Some("square"));
    presence::set_stage_override(Some(2));
    presence::set_hour_override(Some(10));
}

fn colors(ui: &Ui) -> remedy_ui::style::ColorRoles {
    ui.theme().colors.clone()
}

fn line(colors: &remedy_ui::style::ColorRoles) -> Color {
    colors.line.unwrap_or(colors.muted)
}

fn label(ui: &Ui, text: &str, typ: tokens::Type, color: Color) -> El {
    w::text(ui, text.to_string())
        .style(typo(typ).foreground(color))
}

/// First-run chrome: real title bar, no Grove/Studio switcher, no status dock.
fn setup_frame(ui: &Ui, body: El) -> El {
    crate::fonts::prepare_theme(ui, &Fonts::load());
    pin_face();
    let studio = Studio::preview();
    let chrome = Chrome::preview(chrome::Overlay::None);
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .style(StylePatch::new().background(colors(ui).bg))
        .child(titlebar::bar(ui, &studio, chrome))
        .child(body.layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        }))
}

fn setup_card(ui: &Ui, children: Vec<El>) -> El {
    let c = colors(ui);
    let mut col = w::column(ui)
        .gap(space() * 3.0)
        .align(Align::Start)
        .padding(Insets::all(space() * 5.0))
        .layout(|l| {
            l.width = Length::Px(480.0);
            l.max_width = Length::Px(480.0);
        })
        .style(crate::tokens::raised_surface(&c));
    for child in children {
        col = col.child(child);
    }
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(col)
}

fn primary(ui: &Ui, text: &str) -> El {
    w::button(ui, text.to_string(), || {}).variant("primary")
}

fn quiet(ui: &Ui, text: &str) -> El {
    w::button(ui, text.to_string(), || {}).variant("quiet")
}

fn field(ui: &Ui, value: &str) -> El {
    let c = colors(ui);
    w::row(ui)
        .align(Align::Center)
        .padding(Insets {
            start: space() * 2.0,
            end: space() * 2.0,
            top: space() * 1.5,
            bottom: space() * 1.5,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .style(crate::tokens::well_surface(&c))
        .child(label(ui, value, BODY, c.text))
}

fn actions_end(ui: &Ui, children: Vec<El>) -> El {
    let mut row = w::row(ui)
        .gap(space() * 2.0)
        .align(Align::Center)
        .justify(Justify::End)
        .layout(|l| l.width = Length::Percent(1.0));
    for child in children {
        row = row.child(child);
    }
    row
}

fn option_row(ui: &Ui, title: &str, body: &str, recommended: bool, selected: bool) -> El {
    let c = colors(ui);
    let border = if selected { c.accent } else { line(&c) };
    let bg = if selected { c.panel } else { c.card };
    let col = w::column(ui)
        .gap(space())
        .align(Align::Start)
        .padding(Insets::all(space() * 2.5))
        .layout(|l| l.width = Length::Percent(1.0))
        .style(
            StylePatch::new()
                .background(bg)
                .border(border)
                .border_width(if selected { 1.5 } else { 1.0 })
                .radius(ui.theme().radii.medium),
        )
        .child({
            let mut row = w::row(ui)
                .gap(space() * 2.0)
                .align(Align::Center)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(label(ui, title, TITLE, c.text).layout(|l| l.grow = 1.0));
            if recommended {
                row = row.child(
                    w::row(ui)
                        .padding(Insets {
                            start: space(),
                            end: space(),
                            top: space() * 0.4,
                            bottom: space() * 0.4,
                        })
                        .style(
                            StylePatch::new()
                                .background(c.accent)
                                .radius(ui.theme().radii.small),
                        )
                        .child(label(ui, "Recommended", META, c.on_accent)),
                );
            }
            row
        })
        .child(label(ui, body, META, c.muted));
    col
}

fn group_label(ui: &Ui, text: &str) -> El {
    let c = colors(ui);
    label(ui, text, SECTION, c.muted)
}

/// Greeting: Square face, short copy, one recommended choice that just works.
pub fn hello(ui: &Ui) -> El {
    let c = colors(ui);
    setup_frame(
        ui,
        setup_card(
            ui,
            vec![
                presence::preview(ui, Mood::Curious, 72.0),
                label(ui, "I'm Remedy.", HERO, c.text),
                label(
                    ui,
                    "I'll set a few things so we can get to work.",
                    BODY,
                    c.muted,
                ),
                w::column(ui)
                    .gap(space() * 1.5)
                    .align(Align::Start)
                    .layout(|l| l.width = Length::Percent(1.0))
                    .child(primary(ui, "Use the recommended setup"))
                    .child(label(
                        ui,
                        "On this PC. No account, no key.",
                        META,
                        c.muted,
                    )),
                quiet(ui, "More options"),
            ],
        ),
    )
}

/// One plain step: a folder for shared work.
pub fn folder(ui: &Ui) -> El {
    let c = colors(ui);
    setup_frame(
        ui,
        setup_card(
            ui,
            vec![
                presence::preview(ui, Mood::Idle, 72.0),
                label(ui, "A folder for the work we do together.", TITLE, c.text),
                label(
                    ui,
                    "I'll keep projects here. You can change this later.",
                    BODY,
                    c.muted,
                ),
                field(ui, r"C:\Users\Ahmi\Remedy"),
                actions_end(ui, vec![quiet(ui, "Skip"), primary(ui, "Continue")]),
            ],
        ),
    )
}

/// One plain step: what to call the person.
pub fn name(ui: &Ui) -> El {
    let c = colors(ui);
    setup_frame(
        ui,
        setup_card(
            ui,
            vec![
                presence::preview(ui, Mood::Listening, 72.0),
                label(ui, "What should I call you?", TITLE, c.text),
                field(ui, "Ahmi"),
                actions_end(ui, vec![primary(ui, "Continue")]),
            ],
        ),
    )
}

/// Last step: ready to open the Grove.
pub fn ready(ui: &Ui) -> El {
    let c = colors(ui);
    setup_frame(
        ui,
        setup_card(
            ui,
            vec![
                presence::preview(ui, Mood::Happy, 72.0),
                label(ui, "We're ready.", HERO, c.text),
                label(ui, "I'll meet you in the Grove.", BODY, c.muted),
                primary(ui, "Open Grove"),
            ],
        ),
    )
}

/// More options: recommended first, then free and other models.
pub fn more(ui: &Ui) -> El {
    let c = colors(ui);
    setup_frame(
        ui,
        setup_card(
            ui,
            vec![
                presence::preview(ui, Mood::Idle, 72.0),
                label(ui, "Other ways to start.", TITLE, c.text),
                option_row(
                    ui,
                    "On this PC",
                    "The recommended setup. No account, no key.",
                    true,
                    true,
                ),
                group_label(ui, "Free"),
                option_row(
                    ui,
                    "Demo",
                    "Talk now, no install. Limited.",
                    false,
                    false,
                ),
                option_row(
                    ui,
                    "Ollama",
                    "Your own models, on this machine.",
                    false,
                    false,
                ),
                group_label(ui, "Other models"),
                option_row(
                    ui,
                    "xAI, OpenAI, Anthropic, or custom",
                    "You'll need an API key.",
                    false,
                    false,
                ),
                actions_end(ui, vec![quiet(ui, "Back"), primary(ui, "Continue")]),
            ],
        ),
    )
}

fn grove_window(ui: &Ui, extra: Option<El>) -> El {
    crate::fonts::prepare_theme(ui, &Fonts::load());
    pin_face();
    let chrome = Chrome::preview(chrome::Overlay::None);
    let studio = Studio::preview();
    let grove = grove::Grove::preview(studio.clone());
    chrome.grove.set(Some(grove.clone()));
    chrome::hosted(ui, studio, chrome, move |ui| {
        let home = grove::root(ui, grove.clone(), None, None).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        });
        if let Some(bar) = extra.clone() {
            w::column(ui)
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.height = Length::Percent(1.0);
                })
                .child(home)
                .child(bar)
        } else {
            home
        }
    })
}

/// Quiet background download: Grove as usual, no banner, no chip.
pub fn update_quiet(ui: &Ui) -> El {
    grove_window(ui, None)
}

fn ready_notice(ui: &Ui) -> El {
    let c = colors(ui);
    w::row(ui)
        .gap(space() * 2.0)
        .align(Align::Center)
        .padding(Insets {
            start: space() * 3.0,
            end: space() * 3.0,
            top: space() * 1.5,
            bottom: space() * 1.5,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .style(
            StylePatch::new()
                .background(c.panel)
                .border(line(&c))
                .border_width(1.0),
        )
        .child(
            label(
                ui,
                "1.0.1 is ready  ·  A shorter first-run, and a Square face that can smile.",
                META,
                c.text,
            )
            .layout(|l| {
                l.grow = 1.0;
                l.shrink = 1.0;
                l.min_width = Length::Px(0.0);
            }),
        )
        .child(primary(ui, "Restart"))
}

/// One-line ready notice with a short what's-new and a single Restart button.
pub fn update_ready(ui: &Ui) -> El {
    let bar = ready_notice(ui);
    grove_window(ui, Some(bar))
}

/// Advanced settings: update notes, channel, and rollback.
pub fn update_advanced(ui: &Ui) -> El {
    crate::fonts::prepare_theme(ui, &Fonts::load());
    pin_face();
    let chrome = Chrome::preview(chrome::Overlay::None);
    chrome.advanced.set(true);
    let studio = Studio::preview();
    let grove = grove::Grove::preview(studio.clone());
    chrome.grove.set(Some(grove));
    let body = advanced_body(ui);
    chrome::hosted(ui, studio, chrome, move |_| body.clone())
}

fn nav_item(ui: &Ui, text: &str, on: bool) -> El {
    let c = colors(ui);
    let fg = if on { c.text } else { c.muted };
    w::row(ui)
        .padding(Insets {
            start: space() * 2.0,
            end: space() * 2.0,
            top: space() * 1.2,
            bottom: space() * 1.2,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .style(if on {
            StylePatch::new()
                .background(c.card)
                .border(line(&c))
                .border_width(1.0)
                .radius(ui.theme().radii.small)
        } else {
            StylePatch::new()
        })
        .child(label(ui, text, BODY, fg))
}

fn channel_row(ui: &Ui, text: &str, on: bool) -> El {
    let c = colors(ui);
    let border = if on { c.accent } else { line(&c) };
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(Insets {
            start: space() * 2.5,
            end: space() * 2.5,
            top: space() * 1.2,
            bottom: space() * 1.2,
        })
        .layout(|l| l.grow = 1.0)
        .style(
            StylePatch::new()
                .background(if on { c.panel } else { c.card })
                .border(border)
                .border_width(if on { 1.5 } else { 1.0 })
                .radius(ui.theme().radii.small),
        )
        .child(label(ui, text, META, if on { c.text } else { c.muted }))
}

fn advanced_body(ui: &Ui) -> El {
    let c = colors(ui);
    let nav = w::column(ui)
        .gap(space())
        .padding(Insets::all(space() * 3.0))
        .layout(|l| {
            l.width = Length::Px(220.0);
            l.height = Length::Percent(1.0);
        })
        .style(
            StylePatch::new()
                .background(c.panel)
                .border(line(&c))
                .border_width(1.0),
        )
        .child(label(ui, "Settings", TITLE, c.text))
        .child(nav_item(ui, "General", false))
        .child(nav_item(ui, "Appearance", false))
        .child(nav_item(ui, "Advanced", true));

    let notes = w::column(ui)
        .gap(space())
        .padding(Insets::all(space() * 2.5))
        .layout(|l| l.width = Length::Percent(1.0))
        .style(
            StylePatch::new()
                .background(c.card)
                .border(line(&c))
                .border_width(1.0)
                .radius(ui.theme().radii.medium),
        )
        .child(label(ui, "1.0.1", TITLE, c.text))
        .child(label(
            ui,
            "A shorter first-run, and a Square face that can smile. Updates download in the background and wait for you to restart.",
            BODY,
            c.muted,
        ));

    let channel = w::column(ui)
        .gap(space() * 1.5)
        .align(Align::Start)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(group_label(ui, "Channel"))
        .child(
            w::row(ui)
                .gap(space() * 1.5)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(channel_row(ui, "Stable", true))
                .child(channel_row(ui, "Preview", false)),
        )
        .child(label(
            ui,
            "Stable is the default. Preview is for early builds.",
            META,
            c.muted,
        ));

    let rollback = w::column(ui)
        .gap(space() * 1.5)
        .align(Align::Start)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(group_label(ui, "Rollback"))
        .child(label(
            ui,
            "The previous build, 1.0.0, is still on this PC.",
            BODY,
            c.muted,
        ))
        .child(quiet(ui, "Restore 1.0.0"));

    let pane = w::column(ui)
        .gap(space() * 4.0)
        .align(Align::Start)
        .padding(Insets::all(space() * 5.0))
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        })
        .child(label(ui, "Updates", HERO, c.text))
        .child(label(
            ui,
            "Downloads happen in the background. When one is ready, a one-line notice offers Restart.",
            BODY,
            c.muted,
        ))
        .child({
            let mut col = w::column(ui)
                .gap(space())
                .align(Align::Start)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(group_label(ui, "Notes"));
            col = col.child(notes);
            col
        })
        .child(channel)
        .child(rollback);

    w::row(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(nav)
        .child(pane)
}
