//! Time Travel: a rail of restore points for the open chat.
//!
//! Port of `desktop/src/components/TimeTravelTimeline.tsx` (and the `Panel`
//! chrome it sits in). Click a step, confirm, and the runtime rolls chat
//! history, checkpoints, and best-effort workspace files back to that moment.
//!
//! Chrome opens it later from the palette, menus, and shortcuts:
//! `studio.time_travel.open()`. The shell calls [`TimeTravel::mount`] once.
//! This module does not register global shortcuts.

mod api;

use std::cell::RefCell;
use std::rc::Rc;

use crate::studio::Studio;
use crate::tool_button_sized;
use api::Step;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::{Color, Insets};
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::{Signal, batch, untrack, waker};
use remedy_ui::style::{
    BorderEdge, BorderSide, LetterSpacing, RADIUS_FULL, Shadow, StylePatch, TextAlign,
};
use remedy_ui::text::{RichText, TextStylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

/// Width of the Tauri rail (`min(380px, 100vw)`).
const PANEL_WIDTH: f32 = 380.0;
/// The status dock is at least 40 px, with 6 px of padding on each side.
/// The rail stops above it so the dock stays clickable.
const ABOVE_STATUS: f32 = 52.0;

/// Time Travel for one window. Clone shares the same signals.
#[derive(Clone)]
pub struct TimeTravel {
    opened: Signal<bool>,
    steps: Signal<Vec<Step>>,
    loading: Signal<bool>,
    /// Message id of the restore in flight, if any.
    busy: Signal<Option<String>>,
    error: Signal<Option<String>>,
    /// User message waiting for Restore / Cancel.
    confirm: Signal<Option<String>>,
    /// Bumped when the session or the open flag changes, so a late reply is dropped.
    epoch: Signal<u64>,
    /// Bumped per load, so an older refresh cannot paint over a newer one.
    req: Signal<u64>,
    restoring: Signal<bool>,
    /// `"{session}:{open}"` — the effect key from the Tauri component.
    watch_key: Signal<String>,
    /// Previews and a disconnected studio do not call the runtime.
    offline: Signal<bool>,
    mounted: Signal<bool>,
}

impl TimeTravel {
    /// A closed rail with nothing loaded.
    pub fn new() -> Self {
        Self {
            opened: Signal::new(false),
            steps: Signal::new(Vec::new()),
            loading: Signal::new(false),
            busy: Signal::new(None),
            error: Signal::new(None),
            confirm: Signal::new(None),
            epoch: Signal::new(0),
            req: Signal::new(0),
            restoring: Signal::new(false),
            watch_key: Signal::new(String::new()),
            offline: Signal::new(false),
            mounted: Signal::new(false),
        }
    }

    /// Opens the rail for the studio's active session.
    pub fn open(&self) {
        self.opened.set(true);
    }

    /// Closes the rail. An in-flight reply is ignored.
    pub fn close(&self) {
        self.opened.set(false);
    }

    /// Opens when closed, closes when open.
    pub fn toggle(&self) {
        self.opened.update(|open| *open = !*open);
    }

    /// Whether the rail is open.
    pub fn is_open(&self) -> bool {
        self.opened.get()
    }

    /// The rail. [`Self::mount`] shows it on the right while it is open.
    /// A host that places the rail itself can call this directly.
    pub fn view(&self, ui: &Ui, studio: &Studio) -> El {
        rail(ui, self, studio)
    }

    /// Installs a hidden keeper on the overlay layer. Call once from the shell.
    /// The keeper is not a child of the studio column, so a closed rail does not
    /// change the shell's layout. Parenting the keeper again would move it.
    pub fn mount(&self, ui: &Ui, studio: Studio) {
        if self.mounted.get() {
            return;
        }
        self.mounted.set(true);
        let holder = ui.el("timetravel-mount").hidden(true).layout(|layout| {
            layout.width = Length::Px(0.0);
            layout.height = Length::Px(0.0);
            layout.min_width = Length::Px(0.0);
            layout.min_height = Length::Px(0.0);
        });
        ui.overlay(holder.clone());
        // Own the handle. The bind closure is `'static`; a borrowed `&Ui` would not be.
        let ui = ui.clone();
        let tt = self.clone();
        let layer: Rc<RefCell<Option<El>>> = Rc::default();
        holder.bind(move || {
            let open = tt.opened.get();
            let session = live_session(&studio);
            let offline = studio.local_only;
            let tt = tt.clone();
            let studio = studio.clone();
            let ui = ui.clone();
            let layer = layer.clone();
            untrack(move || {
                tt.sync(open, session, offline);
                if open {
                    if layer.borrow().is_none() {
                        let panel = tt.view(&ui, &studio);
                        let host = overlay_host(&ui, panel.clone());
                        ui.overlay(host.clone());
                        panel.focus_first();
                        *layer.borrow_mut() = Some(host);
                    }
                } else if let Some(host) = layer.borrow_mut().take() {
                    host.remove();
                }
            });
        });
    }

    /// Clears the rail when the session or the open flag changes, then loads.
    fn sync(&self, open: bool, session: Option<String>, offline: bool) {
        let key = format!("{}:{open}", session.as_deref().unwrap_or(""));
        if self.watch_key.get() == key {
            return;
        }
        // One notification, so the rail does not flash "No steps yet" before "Loading…".
        batch(|| {
            self.watch_key.set(key);
            self.epoch.update(|epoch| *epoch = epoch.saturating_add(1));
            self.steps.set(Vec::new());
            self.confirm.set(None);
            self.error.set(None);
            self.busy.set(None);
            self.restoring.set(false);
            self.offline.set(offline);
            if open {
                self.loading.set(true);
                self.request_load(session, offline);
            } else {
                self.loading.set(false);
            }
        });
    }

    /// `GET /sessions/{id}/timeline`. A newer load or a closed rail drops the reply.
    fn request_load(&self, session: Option<String>, offline: bool) {
        if self.restoring.get() {
            return;
        }
        let Some(session) = session.filter(|id| !id.is_empty()) else {
            self.loading.set(false);
            self.steps.set(Vec::new());
            self.busy.set(None);
            return;
        };
        if offline {
            self.loading.set(false);
            self.busy.set(None);
            return;
        }
        let epoch = self.epoch.get();
        let req = self.req.get().saturating_add(1);
        self.req.set(req);
        self.loading.set(true);
        self.error.set(None);
        let tt = self.clone();
        let Ok(wake) = waker() else {
            self.loading.set(false);
            self.busy.set(None);
            self.error.set(Some("Failed to load timeline".into()));
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-timeline".into())
            .spawn(move || {
                let out = api::fetch_timeline(&session);
                let _ = wake.post(move || {
                    if tt.epoch.get() != epoch || tt.req.get() != req {
                        return;
                    }
                    match out {
                        Ok(steps) => tt.steps.set(steps),
                        Err(message) => {
                            tt.error.set(Some(message));
                            tt.steps.set(Vec::new());
                        }
                    }
                    tt.loading.set(false);
                    tt.busy.set(None);
                });
            });
        if spawned.is_err() {
            self.loading.set(false);
            self.busy.set(None);
            self.error.set(Some("Failed to load timeline".into()));
        }
    }

    /// Confirmed restore. Reloads the chat through [`Studio::reload_open`].
    fn restore_at(&self, studio: &Studio, message_id: &str) {
        if message_id.is_empty() || self.restoring.get() {
            return;
        }
        if self.offline.get() || studio.local_only {
            self.error
                .set(Some("The mind is not connected. Start this chat once it is.".into()));
            return;
        }
        let Some(session) = live_session(studio) else {
            return;
        };
        self.restoring.set(true);
        // Drop an in-flight load so it cannot clear `busy` or paint stale steps.
        self.req.update(|req| *req = req.saturating_add(1));
        let epoch = self.epoch.get();
        self.busy.set(Some(message_id.to_string()));
        self.error.set(None);
        let message_id = message_id.to_string();
        let tt = self.clone();
        let studio = studio.clone();
        let Ok(wake) = waker() else {
            self.restoring.set(false);
            self.busy.set(None);
            self.error.set(Some("Restore failed".into()));
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-time-travel".into())
            .spawn(move || {
                let out = api::restore(&session, &message_id);
                let _ = wake.post(move || {
                    // The restore happened on the server either way: the chat must show it
                    // even if the rail was closed meanwhile.
                    if out.is_ok() {
                        studio.reload_open();
                    }
                    if tt.epoch.get() != epoch {
                        return;
                    }
                    match out {
                        Ok(()) => {
                            tt.confirm.set(None);
                            tt.restoring.set(false);
                            tt.request_load(live_session(&studio), false);
                        }
                        Err(message) => {
                            tt.error.set(Some(message));
                            tt.restoring.set(false);
                            tt.busy.set(None);
                        }
                    }
                });
            });
        if spawned.is_err() {
            self.restoring.set(false);
            self.busy.set(None);
            self.error.set(Some("Restore failed".into()));
        }
    }
}

impl Default for TimeTravel {
    fn default() -> Self {
        Self::new()
    }
}

/// The rail on a full window, with sample steps and no network.
/// For a golden on the box. The live shell uses [`TimeTravel::mount`].
pub fn preview(ui: &Ui) -> El {
    let travel = TimeTravel::new();
    travel.offline.set(true);
    travel.loading.set(false);
    travel.steps.set(api::parse_steps(&api::sample_timeline()));
    let studio = Studio::preview();
    let panel = travel.view(ui, &studio);
    let bg = ui.theme().colors.bg;
    ui.el("timetravel-preview")
        .row()
        .justify(Justify::End)
        .align(Align::Start)
        .layout(|layout| {
            layout.width = Length::Percent(1.0);
            layout.height = Length::Percent(1.0);
        })
        .style(StylePatch::new().background(bg))
        .child(panel)
}

fn live_session(studio: &Studio) -> Option<String> {
    studio
        .active
        .get()
        .map(|id| id.trim().to_string())
        .filter(|id| !id.is_empty())
}

fn overlay_host(ui: &Ui, panel: El) -> El {
    w::fill_window(ui.el("timetravel-layer"))
        .row()
        .justify(Justify::End)
        .align(Align::Start)
        .pass_through()
        .child(panel)
}

fn rail(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let frame = ui.el("timetravel").column().style(
        StylePatch::new()
            .background(colors.panel)
            .foreground(colors.text)
            .border_side(BorderEdge::Start, BorderSide::new(1.0, line))
            .shadow(Shadow::new(-8.0, 0.0, 24.0, crate::tokens::depth().shadow.with_alpha(0.22))),
    );
    let sized = frame.clone();
    let u = ui.clone();
    let frame = frame.bind(move || {
        let size = u.window_size();
        let width = if size.width <= 1.0 {
            PANEL_WIDTH
        } else {
            size.width.min(PANEL_WIDTH)
        };
        let height = if size.height <= 1.0 {
            640.0
        } else {
            (size.height - ABOVE_STATUS).max(160.0)
        };
        let _ = sized.clone().layout(|layout| {
            layout.width = Length::Px(width);
            layout.height = Length::Px(height);
            layout.shrink = 0.0;
            layout.min_height = Length::Px(0.0);
        });
    });
    let close_tt = tt.clone();
    let frame = frame
        .focusable(true)
        .semantics(Semantics::new(Role::Group).label("Time Travel"))
        .on_key_capture(move |event| {
            if event.pressed && event.key == Key::Escape {
                close_tt.close();
                true
            } else {
                false
            }
        })
        .child(header(ui, tt))
        .child(intro(ui))
        .child(error_line(ui, tt))
        .child(scroll_body(ui, tt, studio))
        .child(footer(ui, tt, studio));
    frame
}

fn header(ui: &Ui, tt: &TimeTravel) -> El {
    let unit = ui.theme().spacing.unit;
    let line = hairline(&ui.theme().colors);
    let close = tt.clone();
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .padding(Insets {
            top: 10.0,
            end: unit * 3.0,
            bottom: 10.0,
            start: unit * 3.0,
        })
        .layout(|layout| {
            layout.width = Length::Percent(1.0);
            layout.shrink = 0.0;
        })
        .style(StylePatch::new().border_side(BorderEdge::Bottom, BorderSide::new(1.0, line)))
        .child(
            w::text(ui, "Time Travel")
                .variant("strong")
                .style(
                    StylePatch::new()
                        .text_size(crate::tokens::size(crate::tokens::META))
                        .weight(600.0)
                        .letter_spacing(LetterSpacing::Px(-0.2)),
                ),
        )
        .child(tool_button_sized(
            ui,
            w::Icon::Close,
            "Close Time Travel",
            move || close.close(),
            28.0,
            16.0,
        ))
}

fn intro(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    let muted = ui.theme().colors.muted;
    w::text(
        ui,
        "Click a step to roll back chat history, best-effort workspace file writes, and mid-task checkpoints to that moment.",
    )
    .variant("muted")
    .style(
        StylePatch::new()
            .foreground(muted)
            .text_size(crate::tokens::size(crate::tokens::META))
            .line_height(remedy_ui::style::LineHeight::Relative(1.35)),
    )
    .padding(Insets {
        top: unit * 2.0,
        end: unit * 3.0,
        bottom: unit * 2.0,
        start: unit * 3.0,
    })
    .layout(|layout| l_width(layout))
}

fn error_line(ui: &Ui, tt: &TimeTravel) -> El {
    let tt_show = tt.clone();
    let u = ui.clone();
    let tt_text = tt.clone();
    show(ui, move || tt_show.error.get().is_some(), move || {
        let danger = u.theme().colors.danger;
        let unit = u.theme().spacing.unit;
        let tt = tt_text.clone();
        w::text(&u, move || tt.error.get().unwrap_or_default())
            .style(StylePatch::new().foreground(danger).text_size(crate::tokens::size(crate::tokens::LABEL)))
            .padding(Insets {
                top: 0.0,
                end: unit * 3.0,
                bottom: unit * 2.0,
                start: unit * 3.0,
            })
            .layout(|layout| l_width(layout))
    })
    .el()
}

fn scroll_body(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let studio_missing = studio.clone();
    let u_missing = ui.clone();
    let u_rest = ui.clone();
    let tt_rest = tt.clone();
    let studio_rest = studio.clone();
    w::scroll(ui)
        .layout(|layout| {
            layout.width = Length::Percent(1.0);
            layout.grow = 1.0;
            layout.basis = Length::Px(0.0);
            layout.min_height = Length::Px(0.0);
        })
        .child(
            show(
                ui,
                move || live_session(&studio_missing).is_none(),
                move || empty(&u_missing, "No session", "Open a chat to browse its timeline."),
            )
            .fallback(move || loaded(&u_rest, &tt_rest, &studio_rest))
            .el(),
        )
}

fn loaded(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let tt_loading = tt.clone();
    let u_loading = ui.clone();
    let u_rest = ui.clone();
    let tt_rest = tt.clone();
    let studio_rest = studio.clone();
    show(ui, move || tt_loading.loading.get(), move || loading(&u_loading))
        .fallback(move || steps_or_empty(&u_rest, &tt_rest, &studio_rest))
        .el()
}

fn steps_or_empty(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let tt_empty = tt.clone();
    let u_empty = ui.clone();
    let u_list = ui.clone();
    let tt_list = tt.clone();
    let studio_list = studio.clone();
    show(
        ui,
        move || api::user_steps(&tt_empty.steps.get()).is_empty(),
        move || empty(&u_empty, "No steps yet", "Send a message to build the timeline."),
    )
    .fallback(move || step_list(&u_list, &tt_list, &studio_list))
    .el()
}

fn loading(ui: &Ui) -> El {
    let unit = ui.theme().spacing.unit;
    w::text(ui, "Loading…")
        .variant("muted")
        .style(StylePatch::new().text_size(crate::tokens::size(crate::tokens::META)))
        .padding(Insets {
            top: unit,
            end: unit * 3.0,
            bottom: unit,
            start: unit * 3.0,
        })
}

fn step_list(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let users: Signal<Vec<Step>> = Signal::new(Vec::new());
    let src = tt.steps.clone();
    let dst = users.clone();
    let col = w::column(ui)
        .padding(Insets {
            top: unit,
            end: unit * 3.0,
            bottom: unit * 3.0,
            start: unit * 3.0,
        })
        .layout(|layout| l_width(layout))
        .semantics(Semantics::new(Role::List).label("Timeline"))
        .bind(move || {
            dst.set(api::user_steps(&src.get()));
        });
    let u = ui.clone();
    let travel = tt.clone();
    let studio = studio.clone();
    w::for_each(
        &col,
        users,
        |step| step.message_id.clone(),
        move |step| step_row(&u, &travel, &studio, step.message_id.clone()),
    )
}

fn step_row(ui: &Ui, tt: &TimeTravel, studio: &Studio, message_id: String) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .align(Align::Start)
        .gap(unit * 2.0)
        .padding(Insets {
            top: 0.0,
            end: 0.0,
            bottom: unit * 3.0,
            start: 0.0,
        })
        .layout(|layout| l_width(layout))
        .semantics({
            let steps = tt.steps.clone();
            let id = message_id.clone();
            move || {
                let label = user_field(&steps.get(), &id, |step| step.label.clone())
                    .unwrap_or_else(|| "Timeline step".into());
                Semantics::new(Role::ListItem).label(label)
            }
        })
        .child(rail_spine(ui, tt, message_id.clone()))
        .child(step_main(ui, tt, studio, message_id))
}

fn rail_spine(ui: &Ui, tt: &TimeTravel, message_id: String) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let accent = colors.accent;
    let card = colors.card;
    let steps = tt.steps.clone();
    let id = message_id.clone();
    let dot = ui
        .el("timetravel-dot")
        .layout(|layout| {
            layout.width = Length::Px(10.0);
            layout.height = Length::Px(10.0);
            layout.min_width = Length::Px(10.0);
            layout.min_height = Length::Px(10.0);
            layout.shrink = 0.0;
        })
        .style(move || {
            let steps = steps.get();
            let last = api::is_last_user(&steps, &id);
            let (fill, edge) = if last {
                (accent, accent)
            } else {
                (card, line)
            };
            StylePatch::new()
                .background(fill)
                .border(edge)
                .border_width(2.0)
                .radius(RADIUS_FULL)
        });
    let steps_above = tt.steps.clone();
    let id_above = message_id.clone();
    let above = show(
        ui,
        move || {
            let steps = steps_above.get();
            !api::is_first_user(&steps, &id_above)
                && api::user_steps(&steps).iter().any(|step| step.message_id == id_above)
        },
        {
            let u = ui.clone();
            move || stem(&u, line, false)
        },
    )
    .el();
    let steps_below = tt.steps.clone();
    let id_below = message_id;
    let below = show(
        ui,
        move || {
            let steps = steps_below.get();
            !api::is_last_user(&steps, &id_below)
        },
        {
            let u = ui.clone();
            move || stem(&u, line, true)
        },
    )
    .el();
    w::column(ui)
        .align(Align::Center)
        .layout(|layout| {
            layout.width = Length::Px(16.0);
            layout.shrink = 0.0;
            layout.align_self = Some(Align::Stretch);
        })
        .padding(Insets {
            top: 8.0,
            end: 0.0,
            bottom: 0.0,
            start: 0.0,
        })
        .child(above)
        .child(dot)
        .child(below)
}

fn stem(ui: &Ui, color: Color, grow: bool) -> El {
    let stem = ui.el("timetravel-stem").style(StylePatch::new().background(color));
    if grow {
        stem.layout(|layout| {
            layout.width = Length::Px(1.0);
            layout.min_height = Length::Px(12.0);
            layout.grow = 1.0;
            layout.shrink = 0.0;
        })
    } else {
        stem.layout(|layout| {
            layout.width = Length::Px(1.0);
            layout.height = Length::Px(8.0);
            layout.shrink = 0.0;
        })
    }
}

fn step_main(ui: &Ui, tt: &TimeTravel, studio: &Studio, message_id: String) -> El {
    let unit = ui.theme().spacing.unit;
    let id_when = message_id.clone();
    let id_box = message_id.clone();
    let tt_confirm = tt.clone();
    let travel = tt.clone();
    let studio = studio.clone();
    let u = ui.clone();
    w::column(ui)
        .gap(unit)
        .layout(|layout| {
            layout.grow = 1.0;
            layout.shrink = 1.0;
            layout.min_width = Length::Px(0.0);
            layout.width = Length::Percent(1.0);
        })
        .child(step_card(ui, tt, message_id))
        .child(
            show(
                ui,
                move || tt_confirm.confirm.get().as_deref() == Some(id_when.as_str()),
                move || confirm_box(&u, &travel, &studio, id_box.clone()),
            )
            .el(),
        )
}

fn step_card(ui: &Ui, tt: &TimeTravel, message_id: String) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let radius = ui.theme().radii.small;
    let unit = ui.theme().spacing.unit;
    let card = colors.card;
    let text = colors.text;
    let accent = colors.accent;
    let muted = colors.muted;
    let steps = tt.steps.clone();
    let id_for_click = message_id.clone();
    let tt_click = tt.clone();
    let card_el = ui
        .el("timetravel-step")
        .column()
        .gap(crate::tokens::space() * 0.5)
        .padding(Insets::all(unit * 2.0))
        .layout(|layout| l_width(layout))
        .disabled({
            let tt = tt.clone();
            let steps = tt.steps.clone();
            let id = message_id.clone();
            move || tt.busy.get().is_some() || !step_can_restore(&steps.get(), &id)
        })
        .style({
            let tt = tt.clone();
            move || {
                StylePatch::new()
                    .background(card)
                    .foreground(text)
                    .border(line)
                    .border_width(1.0)
                    .radius(radius)
                    .opacity(if tt.busy.get().is_some() { 0.7 } else { 1.0 })
            }
        })
        .on_click(move || {
            if tt_click.busy.get().is_some() {
                return;
            }
            if !step_can_restore(&tt_click.steps.get(), &id_for_click) {
                return;
            }
            tt_click.confirm.set(Some(id_for_click.clone()));
        })
        .semantics({
            let steps = steps.clone();
            let id = message_id.clone();
            move || {
                let label = user_field(&steps.get(), &id, |step| step.label.clone())
                    .unwrap_or_else(|| "Step".into());
                Semantics::new(Role::Button)
                    .label(format!("{label}. Restore to this step"))
                    .action(Action::Click)
            }
        })
        .child(step_title(ui, &message_id, &steps, accent, muted))
        .child(step_preview(ui, &message_id, &steps, text))
        .child(step_arrow(ui, tt, &message_id, muted));
    w::tooltip(&card_el, "Restore to this step");
    card_el
}

fn step_title(
    ui: &Ui,
    message_id: &str,
    steps: &Signal<Vec<Step>>,
    accent: Color,
    muted: Color,
) -> El {
    let id = message_id.to_string();
    let steps_label = steps.clone();
    let steps_count = steps.clone();
    let id_count = id.clone();
    w::row(ui)
        .align(Align::Center)
        .layout(|layout| l_width(layout))
        .child(
            w::text(ui, move || {
                user_field(&steps_label.get(), &id, |step| step.label.clone()).unwrap_or_default()
            })
            .variant("strong")
            .style(
                StylePatch::new()
                    .foreground(accent)
                    .text_size(crate::tokens::size(crate::tokens::LABEL))
                    .weight(600.0),
            ),
        )
        .child(
            show(
                ui,
                move || {
                    user_field(&steps_count.get(), &id_count, |step| step.tool_count).unwrap_or(0)
                        > 0
                },
                {
                    let u = ui.clone();
                    let steps = steps.clone();
                    let id = message_id.to_string();
                    move || {
                        let steps = steps.clone();
                        let id = id.clone();
                        w::text(&u, move || {
                            let count =
                                user_field(&steps.get(), &id, |step| step.tool_count).unwrap_or(0);
                            format!(" · {count} tools")
                        })
                        .style(
                            StylePatch::new()
                                .foreground(muted)
                                .text_size(crate::tokens::size(crate::tokens::LABEL))
                                .weight(400.0),
                        )
                    }
                },
            )
            .el(),
        )
}

fn step_preview(ui: &Ui, message_id: &str, steps: &Signal<Vec<Step>>, text: Color) -> El {
    let id = message_id.to_string();
    let steps = steps.clone();
    w::text(ui, move || {
        user_field(&steps.get(), &id, |step| step.preview.clone()).unwrap_or_default()
    })
    .max_lines(3)
    .style(StylePatch::new().foreground(text).text_size(crate::tokens::size(crate::tokens::LABEL)))
    .layout(|layout| l_width(layout))
}

fn step_arrow(ui: &Ui, tt: &TimeTravel, message_id: &str, muted: Color) -> El {
    let steps = tt.steps.clone();
    let id = message_id.to_string();
    let steps_text = steps.clone();
    let id_text = id.clone();
    let u = ui.clone();
    show(
        ui,
        move || api::assistant_line(&steps.get(), &id).is_some(),
        move || {
            let steps_text = steps_text.clone();
            let id_text = id_text.clone();
            w::text(&u, move || {
                api::assistant_line(&steps_text.get(), &id_text).unwrap_or_default()
            })
            .max_lines(2)
            .style(StylePatch::new().foreground(muted).text_size(crate::tokens::size(crate::tokens::META)))
            .layout(|layout| l_width(layout))
        },
    )
    .el()
}

fn confirm_box(ui: &Ui, tt: &TimeTravel, studio: &Studio, message_id: String) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let unit = ui.theme().spacing.unit;
    let radius = ui.theme().radii.small;
    let secondary = colors.text.mix_oklab(colors.muted, 0.35);
    let steps = tt.steps.clone();
    let id = message_id.clone();
    w::column(ui)
        .gap(unit * 1.5)
        .padding(Insets::all(unit * 2.0))
        .layout(|layout| l_width(layout))
        .style(
            StylePatch::new()
                .background(colors.bg)
                .border(line)
                .border_width(1.0)
                .radius(radius),
        )
        .child(
            ui.el("timetravel-confirm-copy")
                .text(move || {
                    let label = user_field(&steps.get(), &id, |step| step.label.clone())
                        .unwrap_or_else(|| "this step".into());
                    confirm_copy(&label)
                })
                .style(StylePatch::new().foreground(secondary).text_size(crate::tokens::size(crate::tokens::META)))
                .layout(|layout| l_width(layout)),
        )
        .child(confirm_actions(ui, tt, studio, message_id))
}

fn confirm_copy(label: &str) -> RichText {
    let mut text = RichText::new();
    text.push_text("Restore to ");
    let mut strong = TextStylePatch::default();
    strong.weight = Some(600.0);
    text.push_styled(label, strong);
    text.push_text(
        "? Later messages, and checkpoints will be rolled back. Remedy will try to restore workspace files; changes made outside Remedy may remain.",
    );
    text
}

fn confirm_actions(ui: &Ui, tt: &TimeTravel, studio: &Studio, message_id: String) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let radius = ui.theme().radii.small;
    let accent = colors.accent;
    let on_accent = colors.on_accent;
    let text = colors.text;
    let bg = colors.bg;
    let busy = tt.busy.clone();
    let id_label = message_id.clone();
    let tt_go = tt.clone();
    let studio_go = studio.clone();
    let id_go = message_id;
    let restore = w::button(
        ui,
        move || {
            if busy.get().as_deref() == Some(id_label.as_str()) {
                "Restoring…".to_string()
            } else {
                "Restore here".to_string()
            }
        },
        move || tt_go.restore_at(&studio_go, &id_go),
    )
    .variant("primary")
    .disabled({
        let busy = tt.busy.clone();
        move || busy.get().is_some()
    })
    .layout(|layout| {
        layout.grow = 1.0;
        layout.basis = Length::Px(0.0);
        layout.min_width = Length::Px(0.0);
    })
    .style(
        StylePatch::new()
            .background(accent)
            .foreground(on_accent)
            .radius(radius)
            .border(accent)
            .border_width(1.0)
            .text_size(crate::tokens::size(crate::tokens::LABEL))
            .weight(600.0),
    );
    let tt_cancel = tt.clone();
    let cancel = w::button(ui, "Cancel", move || tt_cancel.confirm.set(None))
        .variant("quiet")
        .layout(|layout| {
            layout.min_width = Length::Px(72.0);
            layout.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(bg)
                .foreground(text)
                .border(line)
                .border_width(1.0)
                .radius(radius)
                .text_size(crate::tokens::size(crate::tokens::LABEL)),
        );
    w::row(ui)
        .gap(ui.theme().spacing.unit)
        .layout(|layout| l_width(layout))
        .child(restore)
        .child(cancel)
}

fn footer(ui: &Ui, tt: &TimeTravel, studio: &Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let radius = ui.theme().radii.small;
    let tt_load = tt.clone();
    let studio_load = studio.clone();
    let tt_disable = tt.clone();
    w::row(ui)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(|layout| {
            l_width(layout);
            layout.shrink = 0.0;
        })
        .style(StylePatch::new().border_side(BorderEdge::Top, BorderSide::new(1.0, line)))
        .child(
            w::button(
                ui,
                "Refresh timeline",
                move || tt_load.request_load(live_session(&studio_load), studio_load.local_only),
            )
            .variant("quiet")
            .disabled(move || tt_disable.loading.get() || tt_disable.busy.get().is_some())
            .fill_width()
            .style(
                StylePatch::new()
                    .foreground(colors.muted)
                    .border(line)
                    .border_width(1.0)
                    .radius(radius)
                    .text_size(crate::tokens::size(crate::tokens::META)),
            ),
        )
}

fn empty(ui: &Ui, title: &str, hint: &str) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let title = title.to_string();
    let hint = hint.to_string();
    let spoken = format!("{title}. {hint}");
    let mark = ui
        .el("timetravel-empty-mark")
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|layout| {
            layout.width = Length::Px(28.0);
            layout.height = Length::Px(28.0);
            layout.min_width = Length::Px(28.0);
            layout.min_height = Length::Px(28.0);
            layout.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.muted.with_alpha(0.12))
                .border(colors.muted.with_alpha(0.28))
                .border_width(1.0)
                .radius(RADIUS_FULL),
        )
        .child(
            w::text(ui, "·")
                .variant("muted")
                .style(StylePatch::new().foreground(colors.muted).text_size(crate::tokens::size(crate::tokens::TITLE))),
        );
    w::column(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .padding(Insets {
            top: unit * 6.0,
            end: unit * 3.0,
            bottom: unit * 6.0,
            start: unit * 3.0,
        })
        .layout(|layout| l_width(layout))
        .semantics(Semantics::new(Role::Status).label(spoken))
        .child(mark)
        .child(
            w::text(ui, title)
                .variant("strong")
                .text_align(TextAlign::Center)
                .style(StylePatch::new().text_size(crate::tokens::size(crate::tokens::LABEL)).weight(600.0)),
        )
        .child(
            w::text(ui, hint)
                .variant("muted")
                .text_align(TextAlign::Center)
                .style(StylePatch::new().foreground(colors.muted).text_size(crate::tokens::size(crate::tokens::META)))
                .layout(|layout| layout.max_width = Length::Px(288.0)),
        )
}

fn hairline(colors: &remedy_ui::style::ColorRoles) -> Color {
    colors
        .line
        .unwrap_or_else(|| colors.muted.mix_oklab(colors.panel, 0.45))
}

fn l_width(layout: &mut remedy_ui::layout::LayoutStyle) {
    layout.width = Length::Percent(1.0);
}

fn user_field<T>(steps: &[Step], id: &str, map: impl FnOnce(&Step) -> T) -> Option<T> {
    steps
        .iter()
        .find(|step| step.kind == "user" && step.message_id == id)
        .map(map)
}

fn step_can_restore(steps: &[Step], id: &str) -> bool {
    user_field(steps, id, |step| step.can_restore).unwrap_or(false)
}
