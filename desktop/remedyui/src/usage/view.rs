//! Usage and continuity dashboard.
//!
//! The panel presents what remedy-runtime already serves. It does not keep a
//! ledger of its own. `GET /usage/summary`, `GET /usage/series`, and
//! `GET /continuity/dashboard` refresh together; a failure of any one of them
//! leaves the previous figures up. `GET /nanoswarm/token/status` is separate,
//! so a missing nanoswarm route does not blank the ledger. CSV export is
//! `GET /usage/export?format=csv`, saved into Downloads.

use std::cell::RefCell;
use std::rc::Rc;

use remedy_ui::a11y::{Live, Role, Semantics};
use remedy_ui::core::{Color, CornerRadii, Insets};
use remedy_ui::input::{Key, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::{Signal, batch, untrack, waker};
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch, TextAlign};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::api::{self, Continuity, Loose, TokenStatus, UsageByGroup, UsageSummary, UsageTotals};

/// What the shell knows when it opens the dashboard.
///
/// `session_id` is sent only to continuity. The usage ledger stays the
/// account total, the same way `getUsageSummary` is called from the Tauri panel.
#[derive(Clone, Debug)]
pub struct UsageContext {
    /// Open session, when there is one.
    pub session_id: Option<String>,
    /// Active provider. Empty hides the "active" subtitle.
    pub provider: String,
    /// Active model. Empty shows an ellipsis next to the provider.
    pub model: String,
}

impl UsageContext {
    /// A context for `session_id`, `provider`, and `model`.
    pub fn new(
        session_id: Option<String>,
        provider: impl Into<String>,
        model: impl Into<String>,
    ) -> Self {
        Self {
            session_id,
            provider: provider.into(),
            model: model.into(),
        }
    }
}

/// Dashboard state. Clone is a handle: the signals stay on the UI thread.
///
/// Chrome keeps one of these and passes it to [`UsagePanel::view`]. Changing
/// the session reloads continuity. Changing the provider or model only updates
/// the subtitle.
#[derive(Clone, Copy)]
pub struct UsageDashboard {
    pub(crate) session_id: Signal<Option<String>>,
    pub(crate) provider: Signal<String>,
    pub(crate) model: Signal<String>,
    pub(crate) range_index: Signal<usize>,
    pub(crate) tab: Signal<usize>,
    pub(crate) loading: Signal<bool>,
    pub(crate) error: Signal<Option<String>>,
    pub(crate) summary: Signal<Option<UsageSummary>>,
    pub(crate) series: Signal<Vec<api::UsagePoint>>,
    pub(crate) continuity: Signal<Option<Continuity>>,
    pub(crate) token: Signal<Option<TokenStatus>>,
    pub(crate) export_note: Signal<Option<String>>,
    pub(crate) exporting: Signal<bool>,
    pub(crate) load_gen: Signal<u64>,
    pub(crate) export_gen: Signal<u64>,
    /// False for goldens: no HTTP, and the range watcher does not fetch.
    live: bool,
}

impl UsageDashboard {
    /// A live dashboard. Nothing is fetched until the panel is shown.
    pub fn new(ctx: UsageContext) -> Self {
        Self {
            session_id: Signal::new(blank_to_none(ctx.session_id)),
            provider: Signal::new(ctx.provider),
            model: Signal::new(ctx.model),
            range_index: Signal::new(1),
            tab: Signal::new(0),
            loading: Signal::new(false),
            error: Signal::new(None),
            summary: Signal::new(None),
            series: Signal::new(Vec::new()),
            continuity: Signal::new(None),
            token: Signal::new(None),
            export_note: Signal::new(None),
            exporting: Signal::new(false),
            load_gen: Signal::new(0),
            export_gen: Signal::new(0),
            live: true,
        }
    }

    /// Fetches summary, series, and continuity. A newer call or [`Self::cancel`]
    /// drops the answer. Token status rides along and never fails the refresh.
    pub fn load(&self) {
        if !self.live {
            return;
        }
        let generation = self.load_gen.get().wrapping_add(1);
        self.load_gen.set(generation);
        self.loading.set(true);
        self.error.set(None);
        self.export_note.set(None);
        let range = api::range_days(self.range_index.get());
        let session = self.session_id.get();
        let dash = *self;
        let Ok(waker) = waker() else {
            self.loading.set(false);
            self.error
                .set(Some("Usage could not refresh from this screen.".into()));
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-usage".into())
            .spawn(move || {
                let http = api::runtime_http();
                let result = api::fetch_dashboard(&http, range, session.as_deref());
                let token = api::fetch_token_status(&http);
                let _ = waker.post(move || apply_load(dash, generation, result, token));
            });
        if spawned.is_err() {
            self.loading.set(false);
            self.error
                .set(Some("Usage could not refresh from this screen.".into()));
        }
    }

    /// Downloads the CSV for the current range and writes it under Downloads.
    pub fn export_csv(&self) {
        if !self.live || self.exporting.get() {
            return;
        }
        let generation = self.export_gen.get().wrapping_add(1);
        self.export_gen.set(generation);
        self.exporting.set(true);
        self.export_note.set(None);
        let range = api::range_days(self.range_index.get());
        let dash = *self;
        let Ok(waker) = waker() else {
            self.exporting.set(false);
            self.error
                .set(Some("The CSV could not be saved from this screen.".into()));
            return;
        };
        let spawned = std::thread::Builder::new()
            .name("remedy-usage".into())
            .spawn(move || {
                let http = api::runtime_http();
                let result = api::fetch_export(&http, range).and_then(|(bytes, name)| {
                    api::save_csv(&name, &bytes).map(|path| path.display().to_string())
                });
                let _ = waker.post(move || apply_export(dash, generation, result));
            });
        if spawned.is_err() {
            self.exporting.set(false);
            self.error
                .set(Some("The CSV could not be saved from this screen.".into()));
        }
    }

    /// Drops answers still in flight. The open panel calls this when it closes.
    pub fn cancel(&self) {
        self.load_gen.update(|g| *g = g.wrapping_add(1));
        self.export_gen.update(|g| *g = g.wrapping_add(1));
        self.loading.set(false);
        self.exporting.set(false);
    }

    /// Points continuity at another session and reloads when the id changes.
    pub fn set_session(&self, session_id: Option<String>) {
        self.session_id.set_if_changed(blank_to_none(session_id));
    }

    /// Subtitle only. The ledger is not filtered by the active model.
    pub fn set_active(&self, provider: impl Into<String>, model: impl Into<String>) {
        self.provider.set(provider.into());
        self.model.set(model.into());
    }
}

fn apply_load(
    dash: UsageDashboard,
    generation: u64,
    result: Result<api::DashboardData, String>,
    token: Result<TokenStatus, String>,
) {
    if dash.load_gen.get() != generation {
        return;
    }
    batch(|| {
        match result {
            Ok(data) => {
                dash.summary.set(Some(data.summary));
                dash.series.set(data.points);
                dash.continuity.set(Some(data.continuity));
                dash.error.set(None);
            }
            Err(err) => dash.error.set(Some(err)),
        }
        if let Ok(token) = token {
            dash.token.set(Some(token));
        }
        dash.loading.set(false);
    });
}

fn apply_export(dash: UsageDashboard, generation: u64, result: Result<String, String>) {
    if dash.export_gen.get() != generation {
        return;
    }
    batch(|| {
        match result {
            Ok(path) => {
                dash.error.set(None);
                dash.export_note.set(Some(format!("Saved to {path}")));
            }
            Err(err) => dash.error.set(Some(err)),
        }
        dash.exporting.set(false);
    });
}

fn blank_to_none(id: Option<String>) -> Option<String> {
    id.filter(|s| !s.trim().is_empty())
}

/// The panel chrome opens from the palette, menus, and shortcuts.
pub struct UsagePanel;

impl UsagePanel {
    /// The dashboard, without a dialog around it, so chrome can embed it.
    ///
    /// `on_close` runs from the Close button. A live dashboard loads once here,
    /// and again when the range or the session changes.
    pub fn view(ui: &Ui, dash: UsageDashboard, on_close: impl Fn() + 'static) -> El {
        content(ui, dash, on_close)
    }

    /// Opens the dashboard as a modal. [`UsageWindow::close`] removes it.
    pub fn open(ui: &Ui, ctx: UsageContext) -> UsageWindow {
        UsageWindow::open(ui, ctx)
    }

    /// Filled usage tab, for a golden. No runtime calls.
    pub fn preview(ui: &Ui) -> El {
        crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
        framed(ui, dialog_card(ui, content(ui, sample(false), || {})))
    }

    /// Filled harness tab, including the tokenizer card. No runtime calls.
    pub fn preview_harness(ui: &Ui) -> El {
        crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
        framed(ui, dialog_card(ui, content(ui, sample(true), || {})))
    }
}

fn framed(ui: &Ui, card: El) -> El {
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .style(StylePatch::new().background(ui.theme().colors.bg))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(card)
}

/// The open modal. Dropping this does not close it; call [`UsageWindow::close`].
#[derive(Clone)]
pub struct UsageWindow {
    /// The dashboard inside the modal.
    pub dashboard: UsageDashboard,
    layer: Rc<RefCell<Option<El>>>,
    restore: Option<El>,
}

impl UsageWindow {
    fn open(ui: &Ui, ctx: UsageContext) -> Self {
        let dashboard = UsageDashboard::new(ctx);
        let layer: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
        let restore = ui.tree().focused().map(|id| ui.element(id));
        let close = {
            let layer = layer.clone();
            let restore = restore.clone();
            let dashboard = dashboard;
            Rc::new(move || {
                dashboard.cancel();
                let Some(layer) = layer.borrow_mut().take() else {
                    return;
                };
                layer.remove();
                if let Some(previous) = restore.as_ref().filter(|el| el.ui().tree().contains(el.id()))
                {
                    previous.focus();
                }
            }) as Rc<dyn Fn()>
        };
        let on_close = {
            let close = close.clone();
            move || close()
        };
        let card = dialog_card(ui, content(ui, dashboard, on_close));
        let inside = card.clone();
        let scrim = w::fill_window(ui.el("scrim"))
            .column()
            .align(Align::Center)
            .justify(Justify::Center)
            .focus_scope()
            .child(card.clone());
        let on_pointer = close.clone();
        let on_key = close;
        let scrim = scrim
            .on_pointer(move |e| {
                let within = inside.bounds().is_some_and(|b| b.contains(e.position));
                if e.phase == PointerPhase::Down && !within {
                    on_pointer();
                    return true;
                }
                false
            })
            .on_key(move |e| {
                if e.pressed && e.key == Key::Escape {
                    on_key();
                    return true;
                }
                false
            });
        ui.overlay(scrim.clone());
        card.focus_first();
        *layer.borrow_mut() = Some(scrim);
        Self {
            dashboard,
            layer,
            restore,
        }
    }

    /// Closes the modal and returns focus to what had it.
    pub fn close(&self) {
        self.dashboard.cancel();
        let Some(layer) = self.layer.borrow_mut().take() else {
            return;
        };
        layer.remove();
        if let Some(previous) = self.restore.as_ref().filter(|el| el.ui().tree().contains(el.id()))
        {
            previous.focus();
        }
    }

    /// True while the scrim is still in the window.
    pub fn is_open(&self) -> bool {
        self.layer
            .borrow()
            .as_ref()
            .is_some_and(|el| el.ui().tree().contains(el.id()))
    }
}

pub(crate) fn dialog_card(ui: &Ui, body: El) -> El {
    ui.el("dialog")
        .column()
        .gap(0.0)
        .scroll()
        .padding(Insets::all(0.0))
        .layout(|l| {
            l.width = Length::Px(672.0);
            l.max_width = Length::Percent(0.92);
            l.min_width = Length::Px(280.0);
            l.max_height = Length::Percent(0.85);
        })
        .semantics(
            Semantics::new(Role::Dialog)
                .label("Usage and continuity dashboard")
                .modal(true),
        )
        .child(body)
}

fn content(ui: &Ui, dash: UsageDashboard, on_close: impl Fn() + 'static) -> El {
    let seen = Rc::new(RefCell::new((dash.range_index.get(), dash.session_id.get())));
    let watch = dash;
    let root = w::column(ui)
        .gap(0.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(header(ui, dash, on_close))
        .child(toolbar(ui, dash))
        .child(body(ui, dash))
        .bind(move || {
            let key = (watch.range_index.get(), watch.session_id.get());
            if *seen.borrow() == key {
                return;
            }
            *seen.borrow_mut() = key;
            untrack(|| watch.load());
        });
    if dash.live {
        untrack(|| dash.load());
    }
    root
}

fn header(ui: &Ui, dash: UsageDashboard, on_close: impl Fn() + 'static) -> El {
    let unit = ui.theme().spacing.unit;
    let line = ui.theme().derive().line;
    let title = w::column(ui)
        .gap(unit * 0.5)
        .layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, "Usage & Continuity").style(
                StylePatch::new()
                    .text_size(ui.theme().type_scale.small)
                    .weight(600.0),
            ),
        )
        .child(
            w::text(ui, move || subtitle(&dash.provider.get(), &dash.model.get()))
                .variant("muted")
                .style(StylePatch::new().text_size(11.0)),
        );
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit * 3.0)
        .padding(Insets {
            top: unit * 3.0,
            end: unit * 4.0,
            bottom: unit * 3.0,
            start: unit * 4.0,
        })
        .style(StylePatch::new().border_side(BorderEdge::Bottom, BorderSide::new(1.0, line)))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(title)
        .child(w::button(ui, "Close", on_close).variant("quiet"))
}

fn toolbar(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let tabs = dash;
    let range = dash;
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 4.0,
            bottom: 0.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
            l.wrap = true;
        })
        .child(w::segmented(
            ui,
            "Usage and continuity",
            &["Usage", "Harness"],
            tabs.tab,
        ))
        .child(w::spacer(ui))
        .child(
            show(ui, move || range.tab.get() == 0, {
                let u = ui.clone();
                let dash = range;
                move || range_tools(&u, dash)
            })
            .el(),
        )
}

fn range_tools(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let labels: Vec<String> = api::RANGE_LABELS.iter().map(|label| (*label).to_string()).collect();
    let exporting = dash;
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .child(
            w::select(ui, "Usage date range", labels, dash.range_index).style(
                StylePatch::new().text_size(ui.theme().type_scale.small),
            ),
        )
        .child(
            w::button(ui, "Export CSV", move || dash.export_csv())
                .variant("quiet")
                .disabled(move || exporting.exporting.get())
                .style(
                    StylePatch::new()
                        .text_size(ui.theme().type_scale.small)
                        .border(ui.theme().derive().line)
                        .border_width(1.0)
                        .radius(ui.theme().radii.small),
                ),
        )
}

fn body(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let loading = dash;
    let error = dash;
    let note = dash;
    let usage = dash;
    let harness = dash;
    w::column(ui)
        .gap(unit * 4.0)
        .padding(Insets::all(unit * 4.0))
        .layout(|l| l.width = Length::Percent(1.0))
        .child(
            show(ui, move || loading.loading.get(), {
                let u = ui.clone();
                move || {
                    w::text(&u, "Loading usage…").variant("muted").semantics(
                        Semantics::new(Role::Status)
                            .label("Loading usage…")
                            .live(Live::Polite),
                    )
                }
            })
            .el(),
        )
        .child(
            show(ui, move || error.error.get().is_some(), {
                let u = ui.clone();
                let dash = error;
                move || error_banner(&u, dash)
            })
            .el(),
        )
        .child(
            show(ui, move || note.export_note.get().is_some(), {
                let u = ui.clone();
                let dash = note;
                move || {
                    w::text(&u, move || dash.export_note.get().unwrap_or_default())
                        .variant("small")
                        .semantics(move || {
                            Semantics::new(Role::Status)
                                .label(dash.export_note.get().unwrap_or_default())
                                .live(Live::Polite)
                        })
                }
            })
            .el(),
        )
        .child(
            show(
                ui,
                move || {
                    usage.tab.get() == 0
                        && usage.summary.get().is_some()
                        && !usage.loading.get()
                        && usage.error.get().is_none()
                },
                {
                    let u = ui.clone();
                    let dash = usage;
                    move || match dash.summary.get() {
                        Some(summary) => usage_body(&u, &summary, &dash.series.get()),
                        None => w::column(&u),
                    }
                },
            )
            .el(),
        )
        .child(
            show(ui, move || harness.tab.get() == 1, {
                let u = ui.clone();
                let dash = harness;
                move || harness_body(&u, dash)
            })
            .el(),
        )
}

fn error_banner(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let retry = dash;
    w::wrap(ui)
        .align(Align::Center)
        .gap(unit * 2.0)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .style(
            StylePatch::new()
                .background(colors.card.mix_oklab(colors.danger, 0.18))
                .radius(ui.theme().radii.small)
                .border_side(BorderEdge::Start, BorderSide::new(3.0, colors.danger)),
        )
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics(move || {
            Semantics::new(Role::Alert)
                .label(dash.error.get().unwrap_or_default())
                .live(Live::Assertive)
        })
        .child(
            w::text(ui, move || dash.error.get().unwrap_or_default()).layout(|l| {
                l.grow = 1.0;
                l.min_width = Length::Px(0.0);
            }),
        )
        .child(
            w::button(ui, "Try again", move || retry.load())
                .variant("quiet")
                .disabled(move || retry.loading.get()),
        )
}

fn usage_body(ui: &Ui, summary: &UsageSummary, points: &[api::UsagePoint]) -> El {
    let unit = ui.theme().spacing.unit;
    let totals = &summary.totals;
    let mut column = w::column(ui)
        .gap(unit * 4.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(stat_row(
            ui,
            [
                ("Tokens", api::format_tokens(totals.total_tokens)),
                ("Events", api::format_number(totals.events)),
                // Share of input the provider served from its prompt cache, and how much.
                ("From cache", api::cache_label(totals)),
            ],
        ))
        .child(group(ui, "Tokens over time", chart_card(ui, points)))
        .child(group(ui, "By provider", provider_table(ui, &summary.by_provider)));
    if !summary.by_model.is_empty() {
        column = column.child(group(ui, "By model", model_table(ui, &summary.by_model)));
    }
    column
}

fn stat_row(ui: &Ui, stats: [(&str, String); 3]) -> El {
    let unit = ui.theme().spacing.unit;
    let mut row = w::row(ui)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.wrap = true;
        });
    for (label, value) in stats {
        row = row.child(stat_card(ui, label, value.as_str()));
    }
    row
}

fn stat_card(ui: &Ui, label: &str, value: &str) -> El {
    let unit = ui.theme().spacing.unit;
    w::card(ui)
        .gap(unit * 0.5)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, label)
                .variant("muted")
                .style(StylePatch::new().text_size(10.0)),
        )
        .child(w::text(ui, value).style(StylePatch::new().weight(600.0)))
}

fn group(ui: &Ui, title: &str, body: El) -> El {
    let unit = ui.theme().spacing.unit;
    w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics(Semantics::new(Role::Group).label(title))
        .child(caption(ui, title))
        .child(body)
}

fn caption(ui: &Ui, title: &str) -> El {
    w::text(ui, title).style(
        StylePatch::new()
            .text_size(ui.theme().type_scale.small)
            .weight(600.0),
    )
}

fn chart_card(ui: &Ui, points: &[api::UsagePoint]) -> El {
    let unit = ui.theme().spacing.unit;
    let bars = api::day_bars(points);
    let body = if bars.is_empty() {
        w::empty_state(
            ui,
            "No usage data yet",
            "Send a few messages and costs will appear here.",
        )
    } else {
        chart(ui, &bars)
    };
    w::card(ui)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| l.width = Length::Percent(1.0))
        .child(body)
}

fn chart(ui: &Ui, bars: &[api::DayBar]) -> El {
    let accent = ui.theme().colors.accent.with_alpha(0.85);
    let radius = ui.theme().radii.small;
    let mut row = w::row(ui)
        .align(Align::End)
        .gap(4.0)
        .padding(Insets {
            top: 0.0,
            end: 4.0,
            bottom: 0.0,
            start: 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Px(112.0);
        });
    for bar in bars {
        let column = bar_column(ui, bar, accent, radius);
        w::tooltip(&column, bar.tip.clone());
        row = row.child(column);
    }
    row
}

fn bar_column(ui: &Ui, bar: &api::DayBar, accent: Color, radius: f32) -> El {
    let mut column = w::column(ui)
        .align(Align::Center)
        .justify(Justify::End)
        .gap(4.0)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        });
    if bar.height > 0.0 {
        let height = bar.height;
        column = column.child(
            ui.el("usage_bar")
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.height = Length::Px(height);
                    l.shrink = 0.0;
                })
                .style(StylePatch::new().background(accent).corners(CornerRadii {
                    top_start: radius,
                    top_end: radius,
                    bottom_end: 0.0,
                    bottom_start: 0.0,
                })),
        );
    }
    column.child(
        w::text(ui, bar.label.clone())
            .variant("muted")
            .nowrap()
            .truncate()
            .style(
                StylePatch::new()
                    .text_size(10.0)
                    .text_align(TextAlign::Center),
            )
            .layout(|l| l.width = Length::Percent(1.0)),
    )
}

fn provider_table(ui: &Ui, rows: &[UsageByGroup]) -> El {
    let mut column = w::column(ui).layout(|l| l.width = Length::Percent(1.0)).child(
        table_row(
            ui,
            vec![
                head_name(ui, "Provider"),
                end_text(ui, "Tokens", COL_TOKENS, true),
                end_text(ui, "Events", COL_EVENTS, true),
            ],
            false,
        ),
    );
    if rows.is_empty() {
        return column.child(
            w::text(ui, "No provider breakdown yet")
                .variant("muted")
                .style(
                    StylePatch::new()
                        .text_size(ui.theme().type_scale.small)
                        .text_align(TextAlign::Center),
                )
                .padding(Insets {
                    top: ui.theme().spacing.unit * 3.0,
                    end: 0.0,
                    bottom: ui.theme().spacing.unit * 3.0,
                    start: 0.0,
                })
                .layout(|l| l.width = Length::Percent(1.0)),
        );
    }
    for row in rows {
        column = column.child(table_row(
            ui,
            vec![
                name_text(ui, api::provider_label(&row.provider)),
                end_text(ui, api::format_tokens(row.totals.total_tokens), COL_TOKENS, false),
                end_text(ui, api::format_number(row.totals.events), COL_EVENTS, false),
            ],
            true,
        ));
    }
    column
}

fn model_table(ui: &Ui, rows: &[UsageByGroup]) -> El {
    let mut column = w::column(ui).layout(|l| l.width = Length::Percent(1.0)).child(
        table_row(
            ui,
            vec![
                head_name(ui, "Model"),
                end_text(ui, "Tokens", COL_TOKENS, true),
            ],
            false,
        ),
    );
    for row in rows.iter().take(12) {
        column = column.child(table_row(
            ui,
            vec![
                model_name(ui, row),
                end_text(ui, api::format_tokens(row.totals.total_tokens), COL_TOKENS, false),
            ],
            true,
        ));
    }
    column
}

const COL_TOKENS: f32 = 72.0;
const COL_EVENTS: f32 = 64.0;

fn table_row(ui: &Ui, cells: Vec<El>, hairline: bool) -> El {
    let unit = ui.theme().spacing.unit;
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: unit * 1.5,
            end: 0.0,
            bottom: unit * 1.5,
            start: 0.0,
        })
        .layout(|l| l.width = Length::Percent(1.0));
    if hairline {
        let line = ui.theme().derive().line;
        row = row.style(StylePatch::new().border_side(BorderEdge::Top, BorderSide::new(1.0, line)));
    }
    for cell in cells {
        row = row.child(cell);
    }
    row
}

fn head_name(ui: &Ui, label: &str) -> El {
    w::text(ui, label)
        .variant("muted")
        .style(StylePatch::new().text_size(ui.theme().type_scale.small))
        .layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        })
}

fn name_text(ui: &Ui, label: String) -> El {
    w::text(ui, label)
        .nowrap()
        .truncate()
        .style(StylePatch::new().text_size(ui.theme().type_scale.small))
        .layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        })
}

fn model_name(ui: &Ui, row: &UsageByGroup) -> El {
    let size = ui.theme().type_scale.small;
    w::row(ui)
        .align(Align::Center)
        .layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        })
        .child(
            w::text(ui, format!("{}/", row.provider))
                .variant("muted")
                .nowrap()
                .style(StylePatch::new().text_size(size)),
        )
        .child(
            w::text(ui, row.model.clone())
                .nowrap()
                .truncate()
                .style(StylePatch::new().text_size(size)),
        )
}

fn end_text(ui: &Ui, value: impl Into<String>, width: f32, muted: bool) -> El {
    let mut text = w::text(ui, value.into())
        .nowrap()
        .style(
            StylePatch::new()
                .text_size(ui.theme().type_scale.small)
                .text_align(TextAlign::End),
        )
        .layout(|l| {
            l.width = Length::Px(width);
            l.shrink = 0.0;
        });
    if muted {
        text = text.variant("muted");
    }
    text
}

fn harness_body(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let continuity = dash;
    let token = dash;
    w::column(ui)
        .gap(unit * 4.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(
            show(ui, move || continuity.continuity.get().is_some(), {
                let u = ui.clone();
                let dash = continuity;
                move || continuity_body(&u, dash)
            })
            .el(),
        )
        .child(
            show(ui, move || token.token.get().is_some(), {
                let u = ui.clone();
                let dash = token;
                move || token_card(&u, dash)
            })
            .el(),
        )
}

fn continuity_body(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let read = dash;
    w::column(ui)
        .gap(unit * 4.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(pair(
            ui,
            hero_card(ui, "Context fill", move || {
                read.continuity
                    .get()
                    .map(|c| api::fill_label(api::continuity_fill(&c)))
                    .unwrap_or_else(|| "—".into())
            }, move || {
                read.continuity
                    .get()
                    .map(|c| method_line(&c))
                    .unwrap_or_default()
            }),
            hero_card(ui, "Context budget", move || {
                read.continuity
                    .get()
                    .map(|c| budget_label(&c))
                    .unwrap_or_else(|| "—".into())
            }, move || {
                read.continuity
                    .get()
                    .map(|c| harness_sub(&c))
                    .unwrap_or_default()
            }),
        ))
        .child(quality_grid(ui, dash))
        .child(note_card(ui, "Pattern window (this session)", dash, pattern_block, true))
        .child(note_card(ui, "Provider health", dash, health_block, true))
}

fn pair(ui: &Ui, left: El, right: El) -> El {
    let unit = ui.theme().spacing.unit;
    w::row(ui)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.wrap = true;
        })
        .child(left)
        .child(right)
}

fn hero_card(
    ui: &Ui,
    label: &str,
    value: impl Fn() -> String + 'static,
    detail: impl Fn() -> String + 'static,
) -> El {
    let unit = ui.theme().spacing.unit;
    w::card(ui)
        .gap(unit * 0.5)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(200.0);
            l.min_width = Length::Px(180.0);
        })
        .child(
            w::text(ui, label)
                .variant("muted")
                .style(StylePatch::new().text_size(10.0)),
        )
        .child(
            w::text(ui, value).style(
                StylePatch::new()
                    .text_size(ui.theme().type_scale.body + 2.0)
                    .weight(600.0),
            ),
        )
        .child(
            w::text(ui, detail)
                .variant("muted")
                .style(StylePatch::new().text_size(10.0)),
        )
}

fn quality_grid(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let mut wrap = w::wrap(ui).gap(unit * 2.0).layout(|l| l.width = Length::Percent(1.0));
    for index in 0..QUALITY.len() {
        let label = QUALITY[index];
        let read = dash;
        wrap = wrap.child(tile(ui, label, move || {
            read.continuity
                .get()
                .map(|c| quality_value(&c, index))
                .unwrap_or_else(|| "—".into())
        }));
    }
    wrap
}

const QUALITY: [&str; 3] = ["Compresses", "Tokens saved", "Tool fail max"];

fn tile(ui: &Ui, label: &str, value: impl Fn() -> String + 'static) -> El {
    let unit = ui.theme().spacing.unit;
    w::card(ui)
        .gap(unit * 0.5)
        .padding(Insets::all(unit * 2.0))
        .layout(|l| {
            // Three across in the dialog, same as the Tauri `grid-cols-3` row.
            l.grow = 1.0;
            l.basis = Length::Px(180.0);
            l.min_width = Length::Px(160.0);
        })
        .child(
            w::text(ui, label)
                .variant("muted")
                .style(StylePatch::new().text_size(ui.theme().type_scale.small)),
        )
        .child(
            w::text(ui, value).style(
                StylePatch::new()
                    .text_size(ui.theme().type_scale.small)
                    .weight(600.0),
            ),
        )
}

fn note_card(
    ui: &Ui,
    title: &str,
    dash: UsageDashboard,
    block: fn(&Ui, UsageDashboard) -> El,
    stacked: bool,
) -> El {
    let unit = ui.theme().spacing.unit;
    w::card(ui)
        .gap(unit)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(move |l| {
            // A stacked card hugs its text. Sharing a row, it takes half the width.
            // Grow on a column child would stretch the card down the dialog.
            if stacked {
                l.width = Length::Percent(1.0);
                l.grow = 0.0;
            } else {
                l.grow = 1.0;
                l.basis = Length::Px(200.0);
                l.min_width = Length::Px(180.0);
            }
        })
        .child(caption(ui, title))
        .child(block(ui, dash))
}

fn pattern_block(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let recent = dash;
    w::column(ui).gap(unit).child(
        w::text(ui, move || {
            dash.continuity
                .get()
                .map(|c| pattern_line(&c))
                .unwrap_or_default()
        })
        .variant("muted")
        .style(StylePatch::new().text_size(ui.theme().type_scale.small)),
    )
    .child(
        show(ui, move || recent.continuity.get().is_some_and(|c| !c.recent.is_empty()), {
            let u = ui.clone();
            move || {
                w::text(&u, move || {
                    recent
                        .continuity
                        .get()
                        .map(|c| c.recent.join(" → "))
                        .unwrap_or_default()
                })
                .style(
                    StylePatch::new()
                        .mono(true)
                        .text_size(10.0),
                )
            }
        })
        .el(),
    )
}

fn health_block(ui: &Ui, dash: UsageDashboard) -> El {
    w::text(ui, move || {
        dash.continuity
            .get()
            .map(|c| health_line(&c))
            .unwrap_or_else(|| "samples 0".into())
    })
    .variant("muted")
    .style(StylePatch::new().text_size(ui.theme().type_scale.small))
}

fn token_card(ui: &Ui, dash: UsageDashboard) -> El {
    let unit = ui.theme().spacing.unit;
    let active = dash;
    let note = dash;
    w::card(ui)
        .gap(unit)
        .padding(Insets {
            top: unit * 2.0,
            end: unit * 3.0,
            bottom: unit * 2.0,
            start: unit * 3.0,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics(Semantics::new(Role::Group).label("Tokenizer"))
        .child(
            w::text(ui, move || {
                dash.token
                    .get()
                    .map(|t| t.label)
                    .unwrap_or_else(|| "Tokenizer".into())
            })
            .style(
                StylePatch::new()
                    .text_size(ui.theme().type_scale.small)
                    .weight(600.0),
            ),
        )
        .child(
            w::text(ui, move || {
                dash.token
                    .get()
                    .map(|t| method_bits(&t.last_method, t.last_estimate))
                    .unwrap_or_default()
            })
            .variant("muted")
            .style(StylePatch::new().text_size(10.0)),
        )
        .child(
            show(ui, move || active.token.get().is_some_and(|t| token_active(&t).is_some()), {
                let u = ui.clone();
                move || {
                    w::text(&u, move || {
                        active
                            .token
                            .get()
                            .and_then(|t| token_active(&t))
                            .unwrap_or_default()
                    })
                    .variant("muted")
                    .style(StylePatch::new().text_size(10.0))
                }
            })
            .el(),
        )
        .child(
            show(ui, move || note.token.get().is_some_and(|t| t.note.is_some()), {
                let u = ui.clone();
                move || {
                    w::text(&u, move || {
                        note.token.get().and_then(|t| t.note).unwrap_or_default()
                    })
                    .variant("muted")
                    .style(StylePatch::new().text_size(10.0))
                }
            })
            .el(),
        )
}

fn subtitle(provider: &str, model: &str) -> String {
    let mut line = "Token use and conversation health".to_string();
    if !provider.is_empty() {
        line.push_str(" · active ");
        line.push_str(provider);
        line.push('/');
        line.push_str(if model.is_empty() { "…" } else { model });
    }
    line
}

fn method_line(c: &Continuity) -> String {
    method_bits(&c.token_method, c.token_estimate)
}

fn method_bits(method: &str, estimate: f64) -> String {
    format!("method {method} · estimate {}", api::format_tokens(estimate))
}

fn harness_sub(c: &Continuity) -> String {
    format!("turns {} · saved {}", c.turns.count(), api::saved_tokens(c))
}

fn quality_value(c: &Continuity, index: usize) -> String {
    match index {
        0 => c.compress.count(),
        1 => api::saved_tokens(c),
        _ => c.tool_fail_max.count(),
    }
}

/// The tokens a transcript may reach before the engine compacts it. Known after a turn.
fn budget_label(c: &Continuity) -> String {
    c.budget
        .filter(|n| n.is_finite() && *n > 0.0)
        .map(api::format_tokens)
        .unwrap_or_else(|| "—".into())
}

fn pattern_line(c: &Continuity) -> String {
    let mut line = format!("{} steps", api::format_number(c.step_count));
    if let Some(rate) = c.success_rate.filter(|n| n.is_finite()) {
        line.push_str(" · ");
        line.push_str(&api::percent_fixed(rate, 0));
        line.push_str(" success");
    }
    line
}

fn health_line(c: &Continuity) -> String {
    let mut line = format!("samples {}", api::format_number(c.samples));
    if let Some(rate) = c.error_rate.filter(|n| n.is_finite()) {
        line.push_str(" · err ");
        line.push_str(&api::percent_fixed(rate, 0));
    }
    if c.rate_limit_hits.is_finite() && c.rate_limit_hits != 0.0 {
        line.push_str(&format!(" · 429×{}", api::format_number(c.rate_limit_hits)));
    }
    line
}

fn token_active(token: &TokenStatus) -> Option<String> {
    match (&token.active_provider, &token.active_model) {
        (Some(provider), Some(model)) => Some(format!("active {provider}/{model}")),
        (Some(provider), None) => Some(format!("active {provider}")),
        (None, Some(model)) => Some(format!("active {model}")),
        (None, None) => None,
    }
}

fn sample(harness: bool) -> UsageDashboard {
    let mut dash = UsageDashboard::new(UsageContext::new(Some("preview".into()), "local", "remedy"));
    dash.live = false;
    dash.tab.set(if harness { 1 } else { 0 });
    dash.summary.set(Some(sample_summary()));
    dash.series.set(sample_series());
    dash.continuity.set(Some(sample_continuity()));
    dash.token.set(Some(sample_token()));
    dash
}

fn sample_summary() -> UsageSummary {
    UsageSummary {
        range_days: 7.0,
        totals: totals(8420.0, 1.28, 37.0),
        by_provider: vec![
            group_row("local", "", 5200.0, 1.28, 22.0),
            group_row("openai", "", 3220.0, 2.40, 15.0),
        ],
        by_model: vec![
            group_row("local", "remedy", 5200.0, 1.28, 22.0),
            group_row("openai", "gpt", 3220.0, 2.40, 15.0),
        ],
    }
}

fn sample_series() -> Vec<api::UsagePoint> {
    let days = [
        ("2026-11-16", 1200.0),
        ("2026-11-17", 800.0),
        ("2026-11-18", 2400.0),
        ("2026-11-19", 400.0),
        ("2026-11-20", 6400.0),
        ("2026-11-21", 1600.0),
        ("2026-11-22", 2100.0),
    ];
    days.into_iter()
        .map(|(day, tokens)| api::UsagePoint {
            day: day.into(),
            group: "local".into(),
            total_tokens: tokens,
            estimated_cost_usd: 0.0,
            events: 1.0,
        })
        .collect()
}

fn sample_continuity() -> Continuity {
    Continuity {
        token_method: "o200k_base".into(),
        token_estimate: 18420.0,
        token_fill: Some(0.62),
        budget: Some(160_000.0),
        turns: Loose::Num(14.0),
        tokens_saved: Loose::Num(3200.0),
        compress: Loose::Num(3.0),
        tool_fail_max: Loose::Num(2.0),
        step_count: 8.0,
        success_rate: Some(0.75),
        recent: vec!["read".into(), "edit".into(), "test".into()],
        samples: 20.0,
        error_rate: Some(0.05),
        rate_limit_hits: 0.0,
    }
}

fn sample_token() -> TokenStatus {
    TokenStatus {
        label: "Tokenizer".into(),
        last_method: "o200k_base".into(),
        last_estimate: 4096.0,
        active_provider: Some("local".into()),
        active_model: Some("remedy".into()),
        note: Some("Counted with the o200k_base encoding (github.com/tiktoken-go/tokenizer, MIT).".into()),
    }
}

fn totals(total: f64, cost: f64, events: f64) -> UsageTotals {
    UsageTotals {
        prompt_tokens: total * 0.7,
        completion_tokens: total * 0.3,
        total_tokens: total,
        estimated_cost_usd: cost,
        events,
        cache_read_tokens: total * 0.35,
        cache_hit_rate: Some(0.5),
    }
}

fn group_row(provider: &str, model: &str, total: f64, cost: f64, events: f64) -> UsageByGroup {
    UsageByGroup {
        provider: provider.into(),
        model: model.into(),
        totals: totals(total, cost, events),
    }
}

#[cfg(test)]
mod tests {
    use super::api;
    use super::{
        budget_label, harness_sub, health_line, method_line, pattern_line, quality_value,
        sample_continuity, sample_token, subtitle, token_active,
    };

    #[test]
    fn subtitle_names_the_active_model() {
        assert_eq!(
            subtitle("local", "remedy"),
            "Token use and conversation health · active local/remedy"
        );
        assert_eq!(
            subtitle("local", ""),
            "Token use and conversation health · active local/…"
        );
        assert_eq!(
            subtitle("", "remedy"),
            "Token use and conversation health"
        );
    }

    #[test]
    fn harness_lines_show_what_the_runtime_measured() {
        let c = sample_continuity();
        assert_eq!(api::fill_label(api::continuity_fill(&c)), "62%");
        assert_eq!(method_line(&c), "method o200k_base · estimate 18k");
        assert_eq!(budget_label(&c), "160k");
        assert_eq!(harness_sub(&c), "turns 14 · saved 3.2k");
        assert_eq!(quality_value(&c, 0), "3");
        assert_eq!(quality_value(&c, 1), "3.2k");
        assert_eq!(quality_value(&c, 2), "2");
        assert_eq!(pattern_line(&c), "8 steps · 75% success");
        assert_eq!(c.recent.join(" → "), "read → edit → test");
        assert_eq!(health_line(&c), "samples 20 · err 5%");
        assert_eq!(
            token_active(&sample_token()).as_deref(),
            Some("active local/remedy")
        );
    }

    #[test]
    fn lines_before_any_turn_claim_nothing() {
        let mut c = sample_continuity();
        c.success_rate = None;
        c.error_rate = None;
        c.budget = None;
        c.token_fill = None;
        c.rate_limit_hits = 2.0;
        assert_eq!(pattern_line(&c), "8 steps");
        assert_eq!(health_line(&c), "samples 20 · 429×2");
        assert_eq!(budget_label(&c), "—");
        assert_eq!(api::fill_label(api::continuity_fill(&c)), "—");
    }
}
