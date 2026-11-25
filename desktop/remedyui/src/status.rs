//! Status dock: the 0.70.5 bottom rail — connection, actions, model, and modes.

use crate::studio::{Conn, Studio};
use crate::messages;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use serde_json::json;

const THINKING: [&str; 4] = ["off", "low", "medium", "high"];
const APPROVAL: [&str; 3] = ["ask", "auto", "full"];
const PROCESS: [&str; 3] = ["off", "medium", "full"];

/// Full-width status bar under the shell.
///
/// One [`w::overflow_row`]: fixed gaps, priority items stay (Connected, the
/// model picker, Simple/Advanced), and everything else moves into a "…" menu
/// instead of overlapping or clipping as the window narrows.
pub fn status_bar(ui: &Ui, studio: Studio, on_settings: impl Fn() + 'static) -> El {
    if crate::chrome::compact_now() {
        return compact_bar(ui, studio);
    }

    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();

    let picker = crate::model_picker::control(ui, studio.clone());

    let mem = studio.clone();
    let skills = studio.clone();
    let usage = studio.clone();

    let bar = w::overflow_row(ui)
        .gap(unit * 2.0)
        .keep(connection_indicator(ui, studio.clone()))
        .keep(streaming_slot(ui, studio.clone()))
        .child_maybe_fn(
            {
                let st = studio.clone();
                move || crate::depth::logic::sibling_chip_label(&st.depth.siblings.get())
            },
            siblings_slot(ui, studio.clone()),
        )
        .child_maybe_fn(
            bar_label(ui, "bar-reconnect"),
            reconnect_slot(ui, studio.clone()),
        )
        .child_maybe_fn(
            bar_label(ui, "bar-time-travel"),
            time_travel_slot(ui, studio.clone()),
        )
        .child_maybe_fn(
            {
                let u = ui.clone();
                move || {
                    let id = if access_scope() == "untrusted" {
                        "bar-scope-untrusted"
                    } else {
                        "bar-scope-full"
                    };
                    messages().text(&u, id)
                }
            },
            access_scope_slot(ui),
        )
        .child_maybe_fn(
            {
                let u = ui.clone();
                move || {
                    let ver = crate::update::offer_signal().try_get().unwrap_or_default();
                    format!("{} {ver}", messages().text(&u, "status-update"))
                }
            },
            update_button(ui),
        )
        .keep(update_note(ui))
        .child_maybe_fn(
            bar_label(ui, "bar-memory"),
            advanced_slot(ui, "bar-memory", move || {
                toggle_memory(&mem);
            }),
        )
        .child_maybe_fn(
            bar_label(ui, "bar-skills"),
            advanced_slot(ui, "bar-skills", move || {
                toggle_skills(&skills);
            }),
        )
        .child(
            messages().text(ui, "status-settings"),
            chip(ui, "status-settings", settings_open(), on_settings),
        )
        .child_maybe_fn(
            {
                let u = ui.clone();
                move || {
                    messages().text(&u, if privacy_on() { "bar-privacy-on" } else { "bar-privacy" })
                }
            },
            privacy_slot(ui),
        )
        .keep(chip(ui, "bar-help", false, || {
            crate::chrome::toggle_overlay(crate::chrome::Overlay::Help);
        }))
        .keep(webui_slot(ui))
        .child_fn(
            {
                let u = ui.clone();
                let st = studio.clone();
                move || {
                    if st.depth.speaking.get() {
                        messages().text(&u, "bar-speaking")
                    } else if speak_on() {
                        messages().text(&u, "bar-quiet")
                    } else {
                        messages().text(&u, "bar-speak")
                    }
                }
            },
            speak_chip(ui, studio.clone()),
        )
        // Advanced-only controls stay on the rail (0.70.5). They must not be
        // the first chips pushed into the overflow menu at a typical width.
        .keep(advanced_slot(ui, "bar-usage", move || {
            crate::chrome::open_usage(&usage);
        }))
        .keep(thinking_slot(ui))
        .keep(approval_slot(ui))
        .keep(process_slot(ui))
        .flex(ui.el("status-flex").layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            l.min_width = Length::Px(0.0);
            l.basis = Length::Px(0.0);
        }))
        .keep(rail_rule(ui))
        .keep(mode_chip(ui))
        .keep(picker);

    w::row(ui)
        .align(Align::Center)
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
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(44.0);
            l.shrink = 0.0;
        })
        .semantics(Semantics::new(Role::Toolbar).label(messages().text(ui, "status-bar")))
        .child(bar.el().layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.grow = 1.0;
            l.shrink = 1.0;
        }))
}

/// Phone portal: status, a live pill, and reconnect. Power controls stay on the desktop.
pub fn compact_bar(ui: &Ui, studio: Studio) -> El {
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
        .child(siblings_slot(ui, studio.clone()))
        .child(ui.el("status-flex").layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(crate::model_picker::control(ui, studio.clone()))
        .child(reconnect_slot(ui, studio))
}


fn bar_label(ui: &Ui, id: &'static str) -> impl Fn() -> String + 'static {
    let u = ui.clone();
    move || messages().text(&u, id)
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
                        StylePatch::new().background(dot).radius(0.0)
                    }
                })
                .layout(|l| {
                    let m = crate::theme_pack::layout().connected_mark;
                    l.width = Length::Px(m);
                    l.height = Length::Px(m);
                    l.min_width = Length::Px(m);
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
        .style(crate::tokens::chip(&colors, true))
        .semantics(Semantics::new(Role::Status).label(messages().text(ui, "bar-streaming")))
        .child(
            ui.el("stream-dot")
                .style(StylePatch::new().background(accent).radius(0.0))
                .layout(|l| {
                    let m = crate::theme_pack::layout().connected_mark;
                    l.width = Length::Px(m);
                    l.height = Length::Px(m);
                    l.min_width = Length::Px(m);
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
    let on = privacy_on();
    let id = if on { "bar-privacy-on" } else { "bar-privacy" };
    chip(ui, id, on, toggle_privacy)
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

fn process_slot(ui: &Ui) -> El {
    let u = ui.clone();
    show(
        ui,
        || crate::chrome::advanced_now(),
        move || live_chip(
            &u,
            || match process_mode().as_str() {
                "medium" => "bar-process-med",
                "full" => "bar-process-full",
                _ => "bar-process-min",
            },
            || process_mode() != "off",
            cycle_process,
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

fn speak_chip(ui: &Ui, studio: Studio) -> El {
    let u = ui.clone();
    let st_speak = studio.clone();
    show(
        ui,
        {
            let st = studio.clone();
            move || st.depth.speaking.get()
        },
        {
            let u = u.clone();
            let st = studio.clone();
            move || chip(&u, "bar-speaking", true, {
                let st = st.clone();
                move || toggle_speak(&st)
            })
        },
    )
    .fallback({
        let u = u.clone();
        let st = st_speak;
        move || {
            let u = u.clone();
            let st = st.clone();
            show(
                &u,
                speak_on,
                {
                    let u = u.clone();
                    let st = st.clone();
                    move || chip(&u, "bar-quiet", true, {
                        let st = st.clone();
                        move || toggle_speak(&st)
                    })
                },
            )
            .fallback({
                let u = u.clone();
                let st = st.clone();
                move || chip(&u, "bar-speak", false, {
                    let st = st.clone();
                    move || toggle_speak(&st)
                })
            })
            .el()
        }
    })
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

fn rail_rule(ui: &Ui) -> El {
    let line = crate::tokens::hairline(&ui.theme().colors);
    ui.el("status-rule")
        .style(StylePatch::new().background(line))
        .layout(|l| {
            l.width = Length::Px(1.0);
            l.min_width = Length::Px(1.0);
            l.height = Length::Px(16.0);
            l.shrink = 0.0;
        })
}

fn siblings_slot(ui: &Ui, studio: Studio) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let show_st = studio.clone();
    let body_ui = ui.clone();
    let body_st = studio;
    show(
        ui,
        move || !show_st.depth.siblings.get().is_empty(),
        move || {
            let label_st = body_st.clone();
            let tip_st = body_st.clone();
            w::text(&body_ui, move || {
                crate::depth::logic::sibling_chip_label(&label_st.depth.siblings.get())
            })
            .style(
                StylePatch::new()
                    .foreground(colors.accent)
                    .background(colors.accent.with_alpha(0.12))
                    .radius(3.0),
            )
            .padding(Insets::symmetric(unit * 0.5, unit))
            .semantics(move || {
                let others = tip_st.depth.siblings.get();
                Semantics::new(Role::Status)
                    .label(crate::depth::logic::sibling_chip_label(&others))
                    .description(crate::depth::logic::sibling_chip_tip(&others))
            })
        },
    )
    .into()
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
    btn = btn.style(crate::tokens::chip(&colors, active).weight(550.0));
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

fn process_mode() -> String {
    crate::settings::current()
        .map(|s| crate::work::normalize_tool_process(&s.tool_process.get()).to_string())
        .unwrap_or_else(|| "off".into())
}

fn speak_on() -> bool {
    crate::settings::speak_replies_on()
}

fn toggle_speak(studio: &Studio) {
    let on = !speak_on();
    crate::settings::set_speak_replies(on);
    let mut v = studio.depth.voice.get();
    v.speak_replies = on;
    studio.depth.voice.set(v);
    if !on {
        crate::depth::stop_player();
        studio.depth.speaking.set(false);
        studio.depth.notice.set("I'll stay quiet.".into());
    } else {
        studio.depth.notice.set("I'll speak replies.".into());
    }
}

fn toggle_privacy() {
    crate::settings::open_privacy();
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

fn cycle_process() {
    if let Some(s) = crate::settings::current() {
        let cur = crate::work::normalize_tool_process(&s.tool_process.get());
        let i = PROCESS.iter().position(|x| *x == cur).unwrap_or(0);
        let next = PROCESS[(i + 1) % PROCESS.len()];
        s.tool_process.set(next.to_string());
        s.put_patch(json!({ "tool_process": next }));
    }
}

fn toggle_memory(_studio: &Studio) {
    // One Memory surface: the overlay, which opens instantly with a loading state.
    crate::chrome::toggle_overlay(crate::chrome::Overlay::Memory);
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
            // Ready builds use the chrome notice, not a status-bar chip.
            false && !crate::update::offer_signal()
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
            {
                let note = crate::update::note_signal()
                    .try_get()
                    .unwrap_or_default();
                !note.is_empty() && !note.starts_with("Downloading")
            }
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
