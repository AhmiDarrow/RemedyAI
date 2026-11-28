//! Slide frame header and the pop-out / fullscreen layer.
//! Rail placement (side, width, icon strip) belongs to the rails port.
//! This frame is what those rails mount, or a placeholder column until then.

use super::{SlideId, SlideMeta, Workspace, slide_meta};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::{Color, Insets};
use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::{Shadow, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use std::cell::Cell;
use std::rc::Rc;

/// Header plus the open slide. The body unmounts while that slide is popped out.
pub fn frame(ui: &Ui, ws: &Workspace) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(StylePatch::new().background(colors.panel).foreground(colors.text))
        .child(header(ui, ws, unit, &colors))
        .child(w::divider(ui, false))
        .child(bodies(ui, ws, false).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        }))
}

/// `popped` is the overlay's copy of a slide. The rail copy uses the opposite
/// condition, so only one body (and one PTY) is mounted. Fullscreen is style.
pub fn bodies(ui: &Ui, ws: &Workspace, popped: bool) -> El {
    let mut host = w::column(ui).gap(0.0).layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
        l.min_height = Length::Px(0.0);
    });
    for id in SlideId::ALL {
        let gate = ws.clone();
        let ui_b = ui.clone();
        let ws_b = ws.clone();
        host = host.child(
            show(
                ui,
                move || {
                    if popped {
                        gate.popout.get() == Some(id)
                    } else {
                        gate.open.get() == Some(id) && gate.popout.get() != Some(id)
                    }
                },
                move || super::slide_body(&ui_b, &ws_b, id),
            )
            .el()
            // A hidden show collapses. The open one has to fill the frame, or a
            // percent-height body (and its footer) stays content-sized.
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            }),
        );
    }
    host
}

fn header(ui: &Ui, ws: &Workspace, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let title = w::text(ui, "")
        .text({
            let ws = ws.clone();
            move || open_meta(&ws).label.to_string()
        })
        .truncate();
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(crate::tokens::space() * 0.5)
        .padding(pad(unit * 0.5, unit))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text)
                .text_size(crate::tokens::size(crate::tokens::META))
                .weight(600.0),
        )
        .child(title.layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        }));

    let ui_pop = ui.clone();
    let ws_pop = ws.clone();
    row = row.child(
        show(
            ui,
            {
                let ws = ws.clone();
                move || open_meta(&ws).popout
            },
            move || {
                let ws = ws_pop.clone();
                glyph(&ui_pop, "↗", move || format!("Pop out {}", open_meta(&ws).label), {
                    let ws = ws_pop.clone();
                    move || {
                        if let Some(id) = ws.open.get() {
                            ws.pop_out(id);
                        }
                    }
                })
            },
        )
        .el(),
    );

    let ui_full = ui.clone();
    let ws_full = ws.clone();
    row = row.child(
        show(
            ui,
            {
                let ws = ws.clone();
                move || open_meta(&ws).popout
            },
            move || {
                let ws = ws_full.clone();
                glyph(&ui_full, "⛶", move || format!("Fullscreen {}", open_meta(&ws).label), {
                    let ws = ws_full.clone();
                    move || {
                        if let Some(id) = ws.open.get() {
                            ws.go_fullscreen(id);
                        }
                    }
                })
            },
        )
        .el(),
    );

    let ui_hide = ui.clone();
    let ws_hide = ws.clone();
    let ws_hide_press = ws_hide.clone();
    row.child(glyph(
        &ui_hide,
        "×",
        move || format!("Hide {}", open_meta(&ws_hide).label),
        move || ws_hide_press.hide(),
    ))
}

/// Floating panel for Terminal, Browser, and Scratch. Esc exits fullscreen, then closes.
pub fn popout_layer(ui: &Ui, ws: &Workspace) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or_else(|| colors.muted.with_alpha(0.45));
    let ws_style = ws.clone();
    let ui_style = ui.clone();
    let scrim = w::fill_window(ui.el("popout"))
        .column()
        .style(move || {
            let full = ws_style.fullscreen.get();
            let size = ui_style.window_size();
            let pad = if full {
                Insets::ZERO
            } else {
                Insets {
                    top: size.height * 0.08,
                    bottom: size.height * 0.12,
                    start: size.width * 0.12,
                    end: size.width * 0.12,
                }
            };
            StylePatch::new().padding(pad).background(if full {
                Color::TRANSPARENT
            } else {
                Color::rgba8(0, 0, 0, 120)
            })
        });
    let ws_key = ws.clone();
    let scrim = scrim.on_key_capture(move |e| on_escape(&ws_key, e));

    let ws_panel = ws.clone();
    let ui_panel = ui.clone();
    let colors_panel = colors.clone();
    let panel = w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(move || {
            let full = ws_panel.fullscreen.get();
            let mut patch = StylePatch::new()
                .background(colors_panel.bg)
                .foreground(colors_panel.text)
                .radius(if full { 0.0 } else { 12.0 });
            if !full {
                patch = patch
                    .border(line)
                    .border_width(1.0)
                    .shadow(Shadow::new(0.0, 24.0, 64.0, Color::rgba8(0, 0, 0, 115)));
            }
            patch
        })
        .semantics({
            let ws = ws.clone();
            move || Semantics::new(Role::Dialog).label(pop_meta(&ws).label)
        })
        .child(popout_chrome(ui, ws, &colors, line))
        .child(bodies(ui, ws, true).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        }));

    let _ = ui_panel;
    let scrim = scrim.child(panel).focusable(true);
    let focused = Rc::new(Cell::new(false));
    let scrim_focus = scrim.clone();
    let _ = scrim.clone().bind(move || {
        if !focused.get() {
            focused.set(true);
            scrim_focus.focus();
        }
    });
    scrim
}

fn popout_chrome(ui: &Ui, ws: &Workspace, colors: &remedy_ui::style::ColorRoles, line: Color) -> El {
    let unit = ui.theme().spacing.unit;
    let title = w::text(ui, "").text({
        let ws = ws.clone();
        move || pop_meta(&ws).label.to_string()
    });
    let hint = w::text(ui, "").variant("muted").text({
        let ws = ws.clone();
        move || {
            if ws.fullscreen.get() {
                "Esc · exit fullscreen".to_string()
            } else {
                "Esc · close".to_string()
            }
        }
    });
    let full = w::button(
        ui,
        {
            let ws = ws.clone();
            move || {
                if ws.fullscreen.get() {
                    "↘ Exit fullscreen".to_string()
                } else {
                    "⛶ Fullscreen".to_string()
                }
            }
        },
        {
            let ws = ws.clone();
            move || ws.fullscreen.set(!ws.fullscreen.get())
        },
    )
    .variant("quiet");
    let _ = full.clone().semantics({
        let ws = ws.clone();
        move || {
            let label = if ws.fullscreen.get() {
                "Exit fullscreen"
            } else {
                "Fullscreen"
            };
            Semantics::new(Role::Button).label(label).action(Action::Click)
        }
    });
    let close = w::button(ui, "✕ Close", {
        let ws = ws.clone();
        move || ws.close_popout()
    })
    .variant("danger_solid");
    let _ = close
        .clone()
        .semantics(Semantics::new(Role::Button).label("Close").action(Action::Click));

    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(pad(0.0, unit * 1.5))
        .layout(|l| {
            l.height = Length::Px(44.0);
            l.min_height = Length::Px(44.0);
            l.shrink = 0.0;
            l.width = Length::Percent(1.0);
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text)
                .border_side(
                    remedy_ui::style::BorderEdge::Bottom,
                    remedy_ui::style::BorderSide::new(1.0, line),
                )
                .weight(600.0),
        )
        .child(title.layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(hint)
        .child(full)
        .child(close)
}

fn on_escape(ws: &Workspace, e: &KeyEvent) -> bool {
    if !e.pressed || e.composing || e.key != Key::Escape {
        return false;
    }
    if ws.fullscreen.get() {
        ws.fullscreen.set(false);
    } else {
        ws.close_popout();
    }
    true
}

fn open_meta(ws: &Workspace) -> SlideMeta {
    ws.open.get().map(slide_meta).unwrap_or(SlideMeta {
        label: "Workspace",
        short: "·",
        popout: false,
    })
}

fn pop_meta(ws: &Workspace) -> SlideMeta {
    ws.popout.get().or_else(|| ws.open.get()).map(slide_meta).unwrap_or(SlideMeta {
        label: "Workspace",
        short: "·",
        popout: true,
    })
}

fn glyph(
    ui: &Ui,
    mark: &'static str,
    label: impl Fn() -> String + 'static,
    on_press: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let label = Rc::new(label);
    let read = label.clone();
    let tip = label;
    let b = ui
        .el("button")
        .child(w::text(ui, mark).style(StylePatch::new().foreground(colors.text)))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.min_width = Length::Px(28.0);
            l.min_height = Length::Px(28.0);
        })
        .style(
            StylePatch::new()
                .background(Color::TRANSPARENT)
                .radius(ui.theme().radii.small)
                .foreground(colors.text),
        )
        .on_click(on_press)
        .semantics(move || Semantics::new(Role::Button).label((*read)()).action(Action::Click));
    w::tooltip(&b, move || (*tip)());
    b
}

fn pad(y: f32, x: f32) -> Insets {
    Insets {
        top: y,
        bottom: y,
        start: x,
        end: x,
    }
}
