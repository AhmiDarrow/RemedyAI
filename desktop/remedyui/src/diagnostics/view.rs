//! Diagnostics dialog: server, host, hardware, providers, vision, computer, self-inject.
//!
//! The hive card is a sibling of the snapshot body so its fields survive a refresh.

use std::cell::Cell;
use std::rc::Rc;

use remedy_ui::a11y::{Live, Role, Semantics};
use remedy_ui::core::{Color, Insets};
use remedy_ui::input::{Key, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::{
    BorderEdge, BorderSide, RADIUS_FULL, StylePatch, TextAlign, TextTransform, ensure_contrast,
};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::DiagnosticsPanel;
use super::model::{
    self, Badge, DiagnosticsView, Fact, IssueLine, Meter, Providers, RoundLine, Tone,
};

/// The modal layer. `overlay` pins it to the window; a preview fills its parent instead.
pub(crate) fn scrim(ui: &Ui, panel: &DiagnosticsPanel, overlay: bool) -> El {
    let unit = ui.theme().spacing.unit;
    let dialog = dialog(ui, panel);
    let mut layer = ui
        .el("scrim")
        .column()
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(Insets::all(unit * 4.0))
        .child(dialog);
    if overlay {
        layer = w::fill_window(layer);
    } else {
        layer = layer.layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        });
    }
    let panel = panel.clone();
    layer.on_pointer(move |e| {
        if e.phase == PointerPhase::Down {
            panel.close();
            true
        } else {
            false
        }
    })
}

fn dialog(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let theme = ui.theme();
    let derived = theme.derive();
    let unit = theme.spacing.unit;
    let dialog = ui
        .el("diagnostics")
        .column()
        .style(
            StylePatch::new()
                .background(theme.colors.bg)
                .foreground(derived.text)
                .border(derived.line)
                .border_width(theme.stroke)
                .radius(theme.radii.large),
        )
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.max_width = Length::Px(768.0);
            l.min_width = Length::Px(320.0);
            l.height = Length::Percent(0.9);
            l.max_height = Length::Percent(0.9);
            l.min_height = Length::Px(0.0);
        })
        .semantics(
            Semantics::new(Role::Dialog)
                .label("Health diagnostics")
                .modal(true),
        )
        .focus_scope()
        .on_pointer(|e| e.phase == PointerPhase::Down)
        .on_key_capture({
            let panel = panel.clone();
            move |e| {
                if e.pressed && e.key == Key::Escape {
                    panel.close();
                    true
                } else {
                    false
                }
            }
        });
    let dialog = probe_bind(dialog, panel);
    let gap = unit * 3.0;
    let body = w::scroll(ui)
        .gap(gap)
        .padding(Insets::all(unit * 4.0))
        .child(error_banner(ui, panel))
        .child(loading_line(ui, panel))
        .child(snapshot_slot(ui, panel, gap))
        .child(super::hive::section(ui, panel));
    dialog.child(header(ui, panel)).child(body.grow(1.0))
}

/// First run only records the checkbox. A later change drops the in-flight fetch and loads again.
fn probe_bind(dialog: El, panel: &DiagnosticsPanel) -> El {
    let seen = Rc::new(Cell::new(None::<bool>));
    let panel_b = panel.clone();
    dialog.bind(move || {
        let now = panel_b.probe.get();
        let prev = seen.replace(Some(now));
        if prev.is_some() && prev != Some(now) {
            let panel = panel_b.clone();
            untrack(|| {
                panel.in_flight.set(None);
                panel.load_diagnostics();
            });
        }
    })
}

fn untrack(f: impl FnOnce()) {
    remedy_ui::reactive::untrack(f);
}

fn header(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let theme = ui.theme();
    let unit = theme.spacing.unit;
    let line = theme.derive().line;
    let titles = w::column(ui)
        .gap(unit * 0.5)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(title_row(ui, panel))
        .child(subtitle(ui, panel));
    let controls = w::wrap(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .child(check_tip(ui, "Auto", "Refresh every 8s", panel.auto))
        .child(check_tip(
            ui,
            "Probe",
            "Measure local provider HTTP latency",
            panel.probe,
        ))
        .child(refresh_button(ui, panel))
        .child(copy_button(ui, panel))
        .child(close_button(ui, panel));
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 3.0)
        .padding(Insets::symmetric(unit * 3.0, unit * 4.0))
        .layout(|l| l.shrink = 0.0)
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(theme.stroke, line.with_alpha(0.8)),
        ))
        .layout(|l| l.wrap = true)
        .child(titles)
        .child(controls)
}

fn title_row(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let snap = panel.snapshot;
    w::row(ui)
        .align(Align::Center)
        .gap(ui.theme().spacing.unit * 2.0)
        .child(w::text(ui, "Diagnostics").variant("strong"))
        .child(
            show(ui, move || snap.get().is_some(), {
                let ui = ui.clone();
                move || overall_badge(&ui, snap)
            })
            .el(),
        )
}

fn subtitle(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let snap = panel.snapshot;
    w::text(ui, move || {
        let mut s = "Remedy · RMB · hardware · hive".to_string();
        if let Some(view) = snap.get() {
            if let Some(ms) = &view.collect_ms {
                s.push_str(&format!(" · {ms} ms"));
            }
            if let Some(at) = &view.checked_at {
                s.push_str(" · ");
                s.push_str(at);
            }
        }
        s
    })
    .variant("small")
}

fn check_tip(ui: &Ui, label: &str, tip: &str, value: remedy_ui::reactive::Signal<bool>) -> El {
    let el = w::check(ui, label, value);
    w::tooltip(&el, tip);
    el
}

fn refresh_button(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let loading = panel.loading;
    let panel = panel.clone();
    w::button(
        ui,
        move || -> String {
            if loading.get() {
                "…".into()
            } else {
                "Refresh".into()
            }
        },
        move || panel.load_diagnostics(),
    )
    .variant("quiet")
    .disabled(move || loading.get())
}

fn copy_button(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let snap = panel.snapshot;
    let ui_c = ui.clone();
    let button = w::button(ui, "Copy JSON", move || {
        if let Some(view) = snap.get() {
            ui_c.copy_text(view.json);
        }
    })
    .variant("quiet")
    .disabled(move || snap.get().is_none());
    w::tooltip(&button, "Copy full snapshot JSON");
    button
}

fn close_button(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let panel = panel.clone();
    w::button(ui, "Close", move || panel.close()).variant("quiet")
}

fn error_banner(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let err = panel.error;
    show(ui, move || err.get().is_some(), {
        let ui = ui.clone();
        move || {
            let err_t = err;
            let ui_s = ui.clone();
            let text = w::text(&ui, move || err_t.get().unwrap_or_default());
            ui.el("diagnostics-error")
                .padding(Insets::symmetric(
                    ui.theme().spacing.unit * 2.0,
                    ui.theme().spacing.unit * 3.0,
                ))
                .style(move || {
                    let colors = ui_s.theme().colors;
                    let fg = ensure_contrast(colors.text, colors.danger, 4.5);
                    StylePatch::new()
                        .background(colors.danger)
                        .foreground(fg)
                        .radius(ui_s.theme().radii.small)
                })
                .semantics(move || {
                    Semantics::new(Role::Alert)
                        .live(Live::Assertive)
                        .label(err_t.get().unwrap_or_default())
                })
                .child(text)
        }
    })
    .el()
}

fn loading_line(ui: &Ui, panel: &DiagnosticsPanel) -> El {
    let snap = panel.snapshot;
    let err = panel.error;
    show(ui, move || snap.get().is_none() && err.get().is_none(), {
        let ui = ui.clone();
        move || {
            ui.el("loading")
                .column()
                .align(Align::Center)
                .fill_width()
                .padding(Insets::symmetric(ui.theme().spacing.unit * 8.0, 0.0))
                .child(
                    w::text(&ui, "Loading diagnostics…")
                        .variant("small")
                        .text_align(TextAlign::Center),
                )
        }
    })
    .el()
}

fn snapshot_slot(ui: &Ui, panel: &DiagnosticsPanel, gap: f32) -> El {
    let slot = ui.el("diagnostics-snapshot").column().gap(gap);
    let slot_b = slot.clone();
    let snap = panel.snapshot;
    let rounds = panel.rounds;
    let ui_s = ui.clone();
    slot.bind(move || {
        let view = snap.get();
        let ledger = rounds.get();
        let ui = ui_s.clone();
        untrack(|| {
            slot_b.clear();
            if let Some(view) = view {
                let _ = slot_b
                    .clone()
                    .child(snapshot_body(&ui, &view, &ledger, gap));
            }
        });
    })
}

fn snapshot_body(ui: &Ui, view: &DiagnosticsView, rounds: &[RoundLine], gap: f32) -> El {
    let mut body = w::column(ui).gap(gap).child(issues_card(ui, view));
    let grid = w::wrap(ui)
        .gap(gap)
        .child(fact_card(ui, "Remedy server", &view.server, false))
        .child(fact_card(ui, "RMB host", &view.rmb, false))
        .child(hardware_card(ui, &view.hardware))
        .child(providers_card(ui, &view.providers))
        .child(fact_card(ui, "Vision (SmolVLM)", &view.vision, false))
        .child(fact_card(ui, "Computer host", &view.computer, false));
    body = body.child(grid);
    if !rounds.is_empty() {
        body = body.child(rounds_card(ui, rounds));
    }
    body
}

fn issues_card(ui: &Ui, view: &DiagnosticsView) -> El {
    let unit = ui.theme().spacing.unit;
    let mut list = w::column(ui).gap(unit * 1.5);
    if view.issues.is_empty() {
        list = list.child(w::text(ui, "No issues detected.").variant("small"));
    } else {
        for issue in &view.issues {
            list = list.child(issue_row(ui, issue));
        }
    }
    wide_card(ui, "Issues", Some(view.issue_subtitle.as_str()), None, list)
}

fn issue_row(ui: &Ui, issue: &IssueLine) -> El {
    let colors = ui.theme().colors.clone();
    let tone = match issue.severity.as_str() {
        "error" => colors.danger,
        "warn" => colors.warn,
        _ => colors.muted,
    };
    let card = colors.card;
    let line = ui.theme().derive().line;
    let unit = ui.theme().spacing.unit;
    let mut head = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .child(
            w::text(ui, issue.severity.clone()).style(
                StylePatch::new()
                    .foreground(tone)
                    .text_transform(TextTransform::Upper)
                    .weight(700.0)
                    .text_size(ui.theme().type_scale.small),
            ),
        )
        .child(w::text(ui, "·").variant("small"));
    if !issue.area.is_empty() {
        head = head.child(w::text(ui, issue.area.clone()).variant("small"));
    }
    let mut row = ui
        .el("issue")
        .column()
        .gap(unit * 0.5)
        .padding(Insets::symmetric(unit * 2.0, unit * 2.5))
        .style(
            StylePatch::new()
                .background(tone.mix_oklab(card, 0.92))
                .border(tone.mix_oklab(line, 0.65))
                .border_width(ui.theme().stroke)
                .radius(ui.theme().radii.medium),
        )
        .child(head)
        .child(w::text(ui, issue.message.clone()));
    if let Some(hint) = &issue.hint {
        row = row.child(w::text(ui, hint.clone()).variant("small"));
    }
    row
}

fn fact_card(ui: &Ui, title: &str, fact: &Fact, wide: bool) -> El {
    let unit = ui.theme().spacing.unit;
    let mut body = w::column(ui).gap(unit * 1.5).child(kv(ui, &fact.rows));
    if let Some(meter) = &fact.meter {
        body = body.child(meter_bar(ui, meter));
    }
    if let Some(hint) = &fact.hint {
        body = body.child(w::text(ui, hint.clone()).variant("small"));
    }
    if let Some(error) = &fact.error {
        body = body.child(w::text(ui, error.clone()).variant("error"));
    }
    let sub = if fact.subtitle.is_empty() {
        None
    } else {
        Some(fact.subtitle.as_str())
    };
    let card = wide_card(ui, title, sub, Some(badge(ui, &fact.badge)), body);
    if wide { card } else { grid_card(card) }
}

fn hardware_card(ui: &Ui, hw: &model::Hardware) -> El {
    let unit = ui.theme().spacing.unit;
    let mut body = w::column(ui).gap(unit * 1.5).child(kv(ui, &hw.fact.rows));
    if let Some(meter) = &hw.fact.meter {
        body = body.child(meter_bar(ui, meter));
    }
    for gpu in &hw.gpus {
        let mut block = w::column(ui).gap(unit * 0.5);
        if !gpu.title.is_empty() {
            let title = w::text(ui, gpu.title.clone())
                .variant("small")
                .style(StylePatch::new().weight(600.0));
            let shown = trunc(title);
            w::full_text_tooltip(&shown);
            block = block.child(shown);
        }
        if let Some(meter) = &gpu.meter {
            block = block.child(meter_bar(ui, meter));
        }
        body = body.child(block);
    }
    if let Some(error) = &hw.gpu_error {
        body = body.child(w::text(ui, error.clone()).variant("small"));
    }
    let sub = if hw.fact.subtitle.is_empty() {
        None
    } else {
        Some(hw.fact.subtitle.as_str())
    };
    grid_card(wide_card(
        ui,
        "Hardware",
        sub,
        Some(badge(ui, &hw.fact.badge)),
        body,
    ))
}

fn providers_card(ui: &Ui, providers: &Providers) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let mut body = w::column(ui).gap(unit * 1.5).child(
        w::row(ui)
            .gap(unit)
            .child(w::text(ui, "Active:").variant("small"))
            .child(
                w::text(ui, providers.active.clone()).style(
                    StylePatch::new()
                        .weight(600.0)
                        .text_size(ui.theme().type_scale.small),
                ),
            ),
    );
    let mut remote = ui.el("providers").column().scroll().layout(|l| {
        l.max_height = Length::Px(160.0);
        l.grow = 0.0;
        l.min_height = Length::Px(0.0);
    });
    if providers.remote.is_empty() {
        remote = remote.child(w::text(ui, "No cloud providers connected.").variant("small"));
    } else {
        for line in &providers.remote {
            remote = remote.child(provider_row(ui, line, &colors));
        }
    }
    body = body.child(remote);
    if !providers.local.is_empty() {
        let mut local = w::column(ui)
            .gap(unit * 0.5)
            .child(section_label(ui, "Local"));
        for line in &providers.local {
            let detail = if line.detail.is_empty() {
                "—"
            } else {
                line.detail.as_str()
            };
            local = local.child(
                w::row(ui)
                    .justify(Justify::SpaceBetween)
                    .gap(unit)
                    .child(
                        w::text(ui, line.name.clone())
                            .style(StylePatch::new().text_size(ui.theme().type_scale.small)),
                    )
                    .child(
                        w::text(ui, detail.to_string())
                            .variant("small")
                            .style(StylePatch::new().mono(true)),
                    ),
            );
        }
        body = body.child(local);
    }
    if !providers.usage.is_empty() {
        body = body.child(usage_table(ui, &providers.usage));
    }
    if let Some(probes) = &providers.probes {
        body = body.child(w::text(ui, probes.clone()).variant("small"));
    }
    grid_card(wide_card(
        ui,
        "Cloud & local providers",
        providers.subtitle.as_deref(),
        Some(badge(ui, &providers.badge)),
        body,
    ))
}

fn provider_row(ui: &Ui, line: &model::ProviderLine, colors: &remedy_ui::style::ColorRoles) -> El {
    let unit = ui.theme().spacing.unit;
    let dot_color = if line.connected {
        colors.ok
    } else {
        colors.muted
    };
    let name = trunc(
        w::text(ui, line.name.clone()).style(
            StylePatch::new()
                .weight(600.0)
                .text_size(ui.theme().type_scale.small),
        ),
    );
    let detail = w::text(ui, line.detail.clone())
        .variant("small")
        .style(StylePatch::new().mono(true))
        .layout(|l| l.shrink = 0.0);
    let tip = if line.reason.is_empty() {
        line.detail.clone()
    } else {
        line.reason.clone()
    };
    w::tooltip(&detail, tip);
    w::row(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .padding(Insets::symmetric(unit * 0.5, 0.0))
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(ui.theme().stroke, ui.theme().derive().line.with_alpha(0.5)),
        ))
        .child(dot(ui, dot_color))
        .child(name.layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        }))
        .child(detail)
}

fn usage_table(ui: &Ui, rows: &[model::UsageLine]) -> El {
    let unit = ui.theme().spacing.unit;
    let small = ui.theme().type_scale.small;
    let mut table = w::column(ui)
        .gap(unit * 0.5)
        .child(section_label(ui, "Usage (7d)"))
        .child(
            w::row(ui)
                .child(
                    w::text(ui, "Provider")
                        .variant("small")
                        .layout(|l| l.grow = 1.0),
                )
                .child(end_label(ui, "Tokens"))
                .child(end_label(ui, "Cost")),
        );
    for row in rows {
        table = table.child(
            w::row(ui)
                .align(Align::Center)
                .padding(Insets::symmetric(unit, 0.0))
                .style(StylePatch::new().border_side(
                    BorderEdge::Top,
                    BorderSide::new(ui.theme().stroke, ui.theme().derive().line.with_alpha(0.45)),
                ))
                .child(
                    w::text(ui, row.provider.clone())
                        .style(StylePatch::new().text_size(small))
                        .layout(|l| {
                            l.grow = 1.0;
                            l.min_width = Length::Px(0.0);
                        }),
                )
                .child(end_figure(ui, row.tokens.clone()))
                .child(end_figure(ui, row.cost.clone())),
        );
    }
    table
}

fn rounds_card(ui: &Ui, rounds: &[RoundLine]) -> El {
    let unit = ui.theme().spacing.unit;
    let mut list = w::column(ui).gap(unit * 1.5);
    for round in rounds {
        list = list.child(round_row(ui, round));
    }
    wide_card(
        ui,
        "Self-inject rounds",
        Some("Test-gated self-edits — what changed, and whether serve runs it"),
        None,
        list,
    )
}

fn round_row(ui: &Ui, round: &RoundLine) -> El {
    let unit = ui.theme().spacing.unit;
    let summary = trunc(
        w::text(ui, round.summary.clone())
            .style(StylePatch::new().text_size(ui.theme().type_scale.small)),
    );
    w::full_text_tooltip(&summary);
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(unit * 1.5)
        .child(badge(ui, &round.status));
    if let Some(live) = &round.live {
        row = row.child(badge(ui, live));
    }
    row.child(summary.layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
    }))
    .child(
        w::text(ui, round.meta.clone())
            .variant("small")
            .style(StylePatch::new().mono(true))
            .layout(|l| l.shrink = 0.0),
    )
}

fn wide_card(ui: &Ui, title: &str, subtitle: Option<&str>, badge_el: Option<El>, body: El) -> El {
    let unit = ui.theme().spacing.unit;
    let mut titles = w::column(ui).gap(unit * 0.5).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
    });
    titles = titles.child(w::text(ui, title.to_string()).variant("strong"));
    if let Some(sub) = subtitle {
        titles = titles.child(w::text(ui, sub.to_string()).variant("small"));
    }
    let mut head = w::row(ui)
        .align(Align::Start)
        .justify(Justify::SpaceBetween)
        .gap(unit * 2.0)
        .child(titles);
    if let Some(badge_el) = badge_el {
        head = head.child(badge_el.layout(|l| l.shrink = 0.0));
    }
    w::card(ui)
        .gap(unit * 2.0)
        .semantics(Semantics::new(Role::Group).label(title))
        .child(head)
        .child(body)
}

fn grid_card(card: El) -> El {
    card.layout(|l| {
        l.basis = Length::Px(300.0);
        l.grow = 1.0;
        l.min_width = Length::Px(260.0);
    })
}

fn kv(ui: &Ui, rows: &[model::Pair]) -> El {
    let unit = ui.theme().spacing.unit;
    let mut list = w::column(ui).gap(unit);
    for row in rows {
        let value = trunc(
            w::text(ui, row.value.clone()).style(
                StylePatch::new()
                    .weight(600.0)
                    .mono(true)
                    .text_size(ui.theme().type_scale.small),
            ),
        );
        if row.value != "—" {
            w::full_text_tooltip(&value);
        }
        list = list.child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 2.0)
                .child(w::text(ui, row.label).variant("small").layout(|l| {
                    l.basis = Length::Percent(0.38);
                    l.shrink = 0.0;
                }))
                .child(value.layout(|l| {
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                    l.min_width = Length::Px(0.0);
                })),
        );
    }
    list
}

fn meter_bar(ui: &Ui, meter: &Meter) -> El {
    let pct = meter.pct.clamp(0.0, 100.0);
    let colors = ui.theme().colors.clone();
    let line = ui.theme().derive().line;
    let fill = if pct >= 90.0 {
        colors.danger
    } else if pct >= 75.0 {
        colors.warn
    } else {
        colors.accent
    };
    let unit = ui.theme().spacing.unit;
    let label = meter.label.clone();
    let track = ui
        .el("meter")
        .row()
        .layout(|l| {
            l.height = Length::Px(6.0);
            l.width = Length::Percent(1.0);
        })
        .style(
            StylePatch::new()
                .background(line.with_alpha(0.7))
                .radius(RADIUS_FULL),
        )
        .child(
            ui.el("meter-fill")
                .layout(move |l| {
                    l.height = Length::Px(6.0);
                    l.width = Length::Percent(pct / 100.0);
                })
                .style(StylePatch::new().background(fill).radius(RADIUS_FULL)),
        );
    w::column(ui)
        .gap(unit * 0.5)
        .semantics(
            Semantics::new(Role::Progress)
                .label(label.clone())
                .numeric(pct, 0.0, 100.0),
        )
        .child(
            w::row(ui)
                .justify(Justify::SpaceBetween)
                .child(w::text(ui, label).variant("small"))
                .child(
                    w::text(ui, format!("{pct:.0}%"))
                        .variant("small")
                        .style(StylePatch::new().mono(true)),
                ),
        )
        .child(track)
}

fn badge(ui: &Ui, badge: &Badge) -> El {
    let color = tone_color(&ui.theme().colors, model::badge_tone(badge.ok, badge.warn));
    pill(
        ui,
        color,
        0.14,
        0.35,
        w::text(ui, badge.label.clone()).style(badge_text(ui)),
    )
}

fn overall_badge(ui: &Ui, snapshot: remedy_ui::reactive::Signal<Option<DiagnosticsView>>) -> El {
    let snap = snapshot;
    let ui_c = ui.clone();
    let label = w::text(ui, move || {
        snap.get().map(|v| v.overall_label).unwrap_or_default()
    })
    .style(badge_text(ui));
    let dot_el = dot_live(ui, snapshot);
    let ui_s = ui_c.clone();
    ui.el("overall")
        .row()
        .align(Align::Center)
        .gap(ui.theme().spacing.unit)
        .child(dot_el)
        .child(label)
        .style(move || {
            let colors = ui_s.theme().colors;
            let overall = snapshot.get().map(|v| v.overall).unwrap_or_default();
            let color = tone_color(&colors, model::overall_tone(&overall));
            StylePatch::new()
                .foreground(color)
                .background(color.with_alpha(0.12))
                .border(color.with_alpha(0.30))
                .border_width(1.0)
                .radius(ui_s.theme().radii.small)
                .padding(Insets::symmetric(
                    ui_s.theme().spacing.unit * 0.5,
                    ui_s.theme().spacing.unit * 1.5,
                ))
        })
}

fn pill(ui: &Ui, color: Color, fill: f32, edge: f32, label: El) -> El {
    let unit = ui.theme().spacing.unit;
    ui.el("badge")
        .row()
        .align(Align::Center)
        .gap(unit)
        .padding(Insets::symmetric(unit * 0.5, unit * 1.5))
        .style(
            StylePatch::new()
                .foreground(color)
                .background(color.with_alpha(fill))
                .border(color.with_alpha(edge))
                .border_width(1.0)
                .radius(ui.theme().radii.small),
        )
        .child(dot(ui, color))
        .child(label)
}

fn badge_text(ui: &Ui) -> StylePatch {
    StylePatch::new()
        .text_transform(TextTransform::Upper)
        .weight(700.0)
        .text_size(ui.theme().type_scale.small)
}

fn dot(ui: &Ui, color: Color) -> El {
    ui.el("status-dot")
        .layout(|l| {
            l.width = Length::Px(6.0);
            l.height = Length::Px(6.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().background(color).radius(RADIUS_FULL))
}

fn dot_live(ui: &Ui, snapshot: remedy_ui::reactive::Signal<Option<DiagnosticsView>>) -> El {
    let ui_s = ui.clone();
    ui.el("status-dot")
        .layout(|l| {
            l.width = Length::Px(6.0);
            l.height = Length::Px(6.0);
            l.shrink = 0.0;
        })
        .style(move || {
            let colors = ui_s.theme().colors;
            let overall = snapshot.get().map(|v| v.overall).unwrap_or_default();
            StylePatch::new()
                .background(tone_color(&colors, model::overall_tone(&overall)))
                .radius(RADIUS_FULL)
        })
}

fn tone_color(colors: &remedy_ui::style::ColorRoles, tone: Tone) -> Color {
    match tone {
        Tone::Ok => colors.ok,
        Tone::Warn => colors.warn,
        Tone::Danger => colors.danger,
        Tone::Muted => colors.muted,
    }
}

fn section_label(ui: &Ui, text: &str) -> El {
    w::text(ui, text).variant("small").style(
        StylePatch::new()
            .weight(700.0)
            .text_transform(TextTransform::Upper),
    )
}

fn end_label(ui: &Ui, text: &str) -> El {
    ui.el("cell")
        .row()
        .justify(Justify::End)
        .layout(|l| l.width = Length::Px(72.0))
        .child(w::text(ui, text).variant("small"))
}

fn end_figure(ui: &Ui, text: String) -> El {
    ui.el("cell")
        .row()
        .justify(Justify::End)
        .layout(|l| l.width = Length::Px(72.0))
        .child(
            w::text(ui, text)
                .variant("small")
                .style(StylePatch::new().mono(true)),
        )
}

fn trunc(el: El) -> El {
    el.truncate().layout(|l| l.min_width = Length::Px(0.0))
}
