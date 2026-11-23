//! Status dock: connection, Settings, model picker.

use crate::studio::{Conn, Studio};
use crate::{messages, say};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{RADIUS_FULL, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

/// Full-width status bar under the shell.
pub fn status_bar(ui: &Ui, studio: Studio, on_settings: impl Fn() + 'static) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let u = ui.clone();

    let st_dot = studio.clone();
    let indicator = w::row(ui)
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
        );

    let settings = ui
        .el("button")
        .row()
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 1.5,
            bottom: unit,
            start: unit * 1.5,
        })
        .style(
            StylePatch::new()
                .foreground(colors.text)
                .radius(ui.theme().radii.small),
        )
        .child(w::icon(ui, w::Icon::Settings, 16.0))
        .child(
            w::text(ui, "")
                .text(move || messages().text(&u, "status-settings"))
                .nowrap(),
        )
        .on_click(on_settings)
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Button)
                    .label(messages().text(&u, "status-settings"))
                    .action(Action::Click)
            }
        });

    let st = studio.clone();
    let u = ui.clone();
    let studio_picker = studio.clone();
    let picker = show(
        ui,
        move || !st.models.get().is_empty(),
        move || model_picker(&u, studio_picker.clone()),
    )
    .fallback({
        let u = ui.clone();
        move || say(&u, "partner-name", "muted")
    })
    .el();

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
        .child({
            let mut cluster = w::row(ui)
                .align(Align::Center)
                .gap(unit * 2.0)
                .child(indicator)
                .child(settings);
            // Built once with the shell. Advanced mode at that moment shows Usage.
            // A later toggle waits until Grove and Studio swap, so this row stays
            // pixel-identical to the studio goldens while the mode is simple.
            if crate::chrome::advanced_now() {
                let u = ui.clone();
                let usage_studio = studio.clone();
                cluster = cluster.child(
                    w::button(
                        ui,
                        move || messages().text(&u, "bar-usage"),
                        move || {
                            crate::chrome::open_usage(&usage_studio);
                        },
                    )
                    .variant("quiet"),
                );
            }
            cluster
        })
        .child(picker)
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
