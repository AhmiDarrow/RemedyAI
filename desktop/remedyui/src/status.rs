//! Status dock: the 0.70.5 bottom rail — connection, actions, model, and modes.

use crate::studio::{Conn, Studio};
use crate::{messages, say};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{RADIUS_FULL, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use serde_json::json;

const THINKING: [&str; 4] = ["off", "low", "medium", "high"];
const APPROVAL: [&str; 3] = ["ask", "auto", "full"];

/// Full-width status bar under the shell.
pub fn status_bar(ui: &Ui, studio: Studio, on_settings: impl Fn() + 'static) -> El {
    if crate::chrome::compact_now() {
        return compact_bar(ui, studio);
    }

    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();

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
            l.min_height = Length::Px(44.0);
            l.shrink = 0.0;
        })
        .semantics(Semantics::new(Role::Toolbar).label(messages().text(ui, "status-bar")))
        .child(left_cluster(ui, studio.clone()))
        .child(action_cluster(ui, studio.clone(), on_settings))
        .child(right_cluster(ui, studio))
}

/// Phone portal: status, a live pill, and reconnect. Power controls stay on the desktop.
fn compact_bar(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.muted),
        )
        .padding(Insets {
            top: unit,
            end: unit * 2.0,
            bottom: unit,
            start: unit * 2.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_height = Length::Px(36.0);
            l.shrink = 0.0;
        })
        .semantics(Semantics::new(Role::Toolbar).label(messages().text(ui, "status-bar")))
        .child(connection_indicator(ui, studio.clone()))
        .child(streaming_slot(ui, studio.clone()))
        .child(ui.el("status-flex").layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(reconnect_slot(ui, studio))
}

fn left_cluster(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .layout(|l| l.shrink = 0.0)
        .child(connection_indicator(ui, studio.clone()))
        .child(streaming_slot(ui, studio.clone()))
        .child(time_travel_slot(ui, studio.clone()))
        .child(access_scope_slot(ui))
        .child(reconnect_slot(ui, studio.clone()))
        .child(update_button(ui))
        .child(update_note(ui))
}

fn action_cluster(ui: &Ui, studio: Studio, on_settings: impl Fn() + 'static) -> El {
    let unit = ui.theme().spacing.unit;
    let mem = studio.clone();
    let skills = studio.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(advanced_slot(ui, "bar-memory", move || {
            toggle_memory(&mem);
        }))
        .child(advanced_slot(ui, "bar-skills", move || {
            toggle_skills(&skills);
        }))
        .child(chip(ui, "status-settings", settings_open(), on_settings))
        .child(privacy_slot(ui))
        .child(chip(ui, "bar-help", false, || {
            crate::chrome::toggle_overlay(crate::chrome::Overlay::Help);
        }))
        .child(webui_slot(ui))
}

fn right_cluster(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let st = studio.clone();
    let picker_studio = studio.clone();
    let u = ui.clone();
    let picker = show(
        ui,
        move || !st.models.get().is_empty(),
        move || model_picker(&u, picker_studio.clone()),
    )
    .fallback({
        let u = ui.clone();
        move || say(&u, "partner-name", "muted")
    })
    .el();

    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .layout(|l| l.shrink = 0.0)
        .child(speak_chip(ui))
        .child(mode_chip(ui))
        .child(advanced_slot(ui, "bar-usage", {
            let st = studio;
            move || {
                crate::chrome::open_usage(&st);
            }
        }))
        .child(picker)
        .child(thinking_slot(ui))
        .child(approval_slot(ui))
}

fn connection_indicator(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let st_dot = studio.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(
            ui.el("conn-dot")
                .style({
                    let st = st_dot.clone();
                    let warn = colors.warn;
                    let ok = colors.ok;
                    let muted = colors.muted;
                    move || {
                        let dot = match st.conn.get() {
                            Conn::Connecting | Conn::Error(_) => warn,
                            Conn::Ready => ok,
                            Conn::Disconnected => muted,
                        };
                        StylePatch::new().background(dot).radius(RADIUS_FULL)
                    }
                })
                .layout(|l| {
                    l.width = Length::Px(8.0);
                    l.height = Length::Px(8.0);
                    l.min_width = Length::Px(8.0);
                    l.shrink = 0.0;
                }),
        )
        .child(w::text(ui, "").variant("muted").text({
            let st = studio.clone();
            let u = ui.clone();
            move || match st.conn.get() {
                Conn::Connecting => messages().text(&u, "status-connecting"),
                Conn::Ready => messages().text(&u, "status-connected"),
                Conn::Error(e) => e,
                Conn::Disconnected => messages().text(&u, "status-disconnected"),
            }
        }))
        .on_click(|| crate::chrome::toggle_server_menu())
        .semantics(
            Semantics::new(Role::Button)
                .label(messages().text(ui, "server-menu"))
                .action(Action::Click),
        )
}

fn streaming_slot(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || st.streaming.get(),
        move || streaming_pill(&u),
    )
    .el()
}

fn streaming_pill(ui: &Ui) -> El {
    let colors = ui.theme().colors.clone();
    let accent = colors.accent;
    w::row(ui)
        .align(Align::Center)
        .gap(6.0)
        .padding(Insets {
            top: 4.0,
            end: 8.0,
            bottom: 4.0,
            start: 8.0,
        })
        .style(
            StylePatch::new()
                .background(accent.with_alpha(0.16))
                .foreground(accent)
                .radius(999.0),
        )
        .semantics(Semantics::new(Role::Status).label(messages().text(ui, "bar-streaming")))
        .child(
            ui.el("stream-dot")
                .style(StylePatch::new().background(accent).radius(999.0))
                .layout(|l| {
                    l.width = Length::Px(6.0);
                    l.height = Length::Px(6.0);
                    l.min_width = Length::Px(6.0);
                    l.shrink = 0.0;
                }),
        )
        .child(
            w::text(ui, "")
                .text({
                    let u = ui.clone();
                    move || messages().text(&u, "bar-streaming")
                })
                .nowrap(),
        )
}

fn time_travel_slot(ui: &Ui, studio: Studio) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || {
            let st = studio.clone();
            chip(&u, "bar-time-travel", false, move || {
                crate::chrome::handle_panel("time_travel", None, &st);
            })
        },
    )
    .el()
}

fn access_scope_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || {
            let ready = crate::settings::current()
                .and_then(|s| s.phase.try_get().ok())
                .as_deref()
                == Some("ready");
            let scope = access_scope();
            ready && (scope == "full" || scope == "untrusted")
        },
        move || {
            let id = if access_scope() == "untrusted" {
                "bar-scope-untrusted"
            } else {
                "bar-scope-full"
            };
            chip(&u, id, true, crate::settings::toggle)
        },
    )
    .el()
}

fn reconnect_slot(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || matches!(st.conn.get(), Conn::Disconnected | Conn::Error(_)),
        move || chip(&u, "bar-reconnect", true, || crate::chrome::toggle_server_menu()),
    )
    .el()
}

fn privacy_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now() || privacy_on(),
        move || {
            let on = privacy_on();
            let id = if on { "bar-privacy-on" } else { "bar-privacy" };
            chip(&u, id, on, toggle_privacy)
        },
    )
    .el()
}

fn webui_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || chip(&u, "bar-webui", false, crate::chrome::open_web_now),
    )
    .el()
}

fn thinking_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || live_chip(
            &u,
            || match thinking_level().as_str() {
                "off" => "bar-thinking-off",
                "low" => "bar-thinking-low",
                "high" => "bar-thinking-high",
                _ => "bar-thinking-med",
            },
            || thinking_level() != "off",
            cycle_thinking,
        ),
    )
    .el()
}

fn approval_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || live_chip(
            &u,
            || match approval_mode().as_str() {
                "auto" => "bar-approval-auto",
                "full" => "bar-approval-full",
                _ => "bar-approval-ask",
            },
            || approval_mode() != "ask",
            cycle_approval,
        ),
    )
    .el()
}

fn live_chip(
    ui: &Ui,
    id: impl Fn() -> &'static str + 'static,
    active: impl Fn() -> bool + 'static,
    on_click: impl Fn() + 'static,
) -> El {
    let colors = ui.theme().colors.clone();
    let id = std::rc::Rc::new(id);
    let active = std::rc::Rc::new(active);
    w::button(
        ui,
        {
            let u = ui.clone();
            let id = id.clone();
            move || messages().text(&u, id())
        },
        on_click,
    )
    .variant("quiet")
    .semantics({
        let u = ui.clone();
        let id = id.clone();
        move || Semantics::new(Role::Button).label(messages().text(&u, id())).action(Action::Click)
    })
    .style(move || {
        if active() {
            StylePatch::new()
                .background(colors.accent.with_alpha(0.14))
                .foreground(colors.accent)
        } else {
            StylePatch::new()
        }
    })
}

fn advanced_slot(ui: &Ui, id: &'static str, on_click: impl Fn() + 'static) -> El {
    let u = ui.clone();
    let click = std::rc::Rc::new(on_click);
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || {
            let click = click.clone();
            chip(&u, id, false, move || click())
        },
    )
    .el()
}

fn speak_chip(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        speak_on,
        {
            let u = u.clone();
            move || chip(&u, "bar-quiet", true, toggle_speak)
        },
    )
    .fallback(move || chip(&u, "bar-speak", false, toggle_speak))
    .el()
}

fn mode_chip(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        {
            let u = u.clone();
            move || chip(&u, "bar-advanced", true, || crate::chrome::set_advanced(false))
        },
    )
    .fallback(move || chip(&u, "bar-simple", false, || crate::chrome::set_advanced(true)))
    .el()
}

fn chip(ui: &Ui, id: &'static str, active: bool, on_click: impl Fn() + 'static) -> El {
    let colors = ui.theme().colors.clone();
    let u = ui.clone();
    let label = messages().text(ui, id);
    let mut btn = w::button(
        ui,
        {
            let u = ui.clone();
            move || messages().text(&u, id)
        },
        on_click,
    )
    .variant("quiet")
    .semantics({
        let label = label.clone();
        move || Semantics::new(Role::Button).label(label.clone()).action(Action::Click)
    });
    if active {
        btn = btn.style(
            StylePatch::new()
                .background(colors.accent.with_alpha(0.14))
                .foreground(colors.accent),
        );
    }
    let _ = u;
    btn
}

fn settings_open() -> bool {
    crate::settings::current()
        .and_then(|s| s.open.try_get().ok())
        .unwrap_or(false)
}

fn access_scope() -> String {
    crate::settings::current()
        .and_then(|s| s.access_scope.try_get().ok())
        .unwrap_or_default()
}

fn privacy_on() -> bool {
    crate::settings::current()
        .and_then(|s| s.privacy_mode.try_get().ok())
        .unwrap_or(false)
}

fn thinking_level() -> String {
    crate::settings::current()
        .and_then(|s| s.thinking_level.try_get().ok())
        .unwrap_or_else(|| "medium".into())
}

fn approval_mode() -> String {
    crate::settings::current()
        .and_then(|s| s.approval_mode.try_get().ok())
        .unwrap_or_else(|| "ask".into())
}

fn speak_on() -> bool {
    crate::settings::speak_replies_on()
}

fn toggle_speak() {
    crate::settings::set_speak_replies(!speak_on());
}

fn toggle_privacy() {
    if let Some(s) = crate::settings::current() {
        let on = !s.privacy_mode.get();
        s.privacy_mode.set(on);
        s.put_patch(json!({ "privacy_mode": on }));
    }
}

fn cycle_thinking() {
    if let Some(s) = crate::settings::current() {
        let cur = s.thinking_level.get();
        let i = THINKING.iter().position(|x| *x == cur).unwrap_or(2);
        let next = THINKING[(i + 1) % THINKING.len()];
        s.thinking_level.set(next.to_string());
        s.put_patch(json!({ "thinking_level": next }));
    }
}

fn cycle_approval() {
    if let Some(s) = crate::settings::current() {
        let cur = s.approval_mode.get();
        let i = APPROVAL.iter().position(|x| *x == cur).unwrap_or(0);
        let next = APPROVAL[(i + 1) % APPROVAL.len()];
        s.approval_mode.set(next.to_string());
        s.put_patch(json!({ "approval_mode": next }));
    }
}

fn toggle_memory(studio: &Studio) {
    crate::chrome::toggle_overlay(crate::chrome::Overlay::Memory);
    let open = studio.depth.panel.get() == crate::depth::SidePanel::Memory;
    if open {
        studio.depth.panel.set(crate::depth::SidePanel::None);
    } else {
        studio.depth.panel.set(crate::depth::SidePanel::Memory);
        studio.depth.panel_tab.set("memory".into());
        crate::depth::load_memory(studio);
    }
}

fn toggle_skills(studio: &Studio) {
    crate::chrome::toggle_overlay(crate::chrome::Overlay::Skills);
    let open = studio.depth.panel.get() == crate::depth::SidePanel::Skills;
    if open {
        studio.depth.panel.set(crate::depth::SidePanel::None);
    } else {
        studio.depth.panel.set(crate::depth::SidePanel::Skills);
        studio.depth.panel_tab.set("installed".into());
        crate::depth::load_skills(studio);
    }
}

/// Quiet control. Absent from layout while no newer release is offered, so idle
/// previews keep a stable status row when no update is pending.
fn update_button(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || {
            !crate::update::offer_signal()
                .try_get()
                .unwrap_or_default()
                .is_empty()
        },
        move || {
            let u = u.clone();
            w::button(
                &u,
                {
                    let u = u.clone();
                    move || {
                        let ver = crate::update::offer_signal().try_get().unwrap_or_default();
                        format!("{} {ver}", messages().text(&u, "status-update"))
                    }
                },
                crate::update::press_offer,
            )
            .variant("quiet")
        },
    )
    .el()
}

/// Download percent or a check failure. Absent while the note is empty, so idle
/// previews keep a stable status row.
fn update_note(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || {
            !crate::update::note_signal()
                .try_get()
                .unwrap_or_default()
                .is_empty()
        },
        move || {
            let u = u.clone();
            w::text(&u, "")
                .variant("muted")
                .text(move || crate::update::note_signal().try_get().unwrap_or_default())
        },
    )
    .el()
}

fn model_picker(ui: &Ui, studio: Studio) -> El {
    let models = studio.models.get();
    let current = studio.model.get();
    let selected = models.iter().position(|m| *m == current).unwrap_or(0);
    let sel = Signal::new(selected);
    let label = messages().text(ui, "status-model");
    let control = w::select(ui, label, models.clone(), sel);
    let sel2 = sel;
    let names = models;
    let st = studio.clone();
    control.clone().bind(move || {
        let i = sel2.get();
        if let Some(name) = names.get(i) {
            if st.model.get() != *name {
                st.model.set(name.clone());
            }
        }
    });
    control
}
