//! Help overlay. RA-chrome opens it; this module does not bind F1 or Ctrl+/.

use std::cell::{Cell, RefCell};
#[cfg(not(windows))]
use std::process::Command;
use std::rc::Rc;
use std::time::Duration;

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::{Color, Insets, Vec2};
use remedy_ui::input::{Key, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::style::{BorderEdge, BorderSide, States, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, TextField, Ui, show};

use super::article::{self, percent_decode};
use super::catalog::{self, articles, articles_by_category, get_article, DEFAULT_ID};
use super::search::search_articles;
use crate::{tool_button, SELECT_MARK};

const NAV_WIDTH: f32 = 240.0;
const NARROW_AT: f32 = 640.0;

/// Offline owner's manual.
///
/// [`HelpPanel::view`] is an empty anchor while closed. [`HelpPanel::open`]
/// shows a chapter. Mount the anchor in the shell; the panel draws itself in
/// the overlay layer.
#[derive(Clone)]
pub struct HelpPanel {
    open: Signal<bool>,
    article: Signal<String>,
    query: Signal<String>,
    history: Signal<Vec<String>>,
    version: Signal<String>,
    scroll_tick: Signal<u64>,
    app: Rc<RefCell<Option<App>>>,
    field: Rc<RefCell<Option<TextField>>>,
    headings: Rc<RefCell<Vec<(String, El)>>>,
    restore: Rc<RefCell<Option<El>>>,
    focused_search: Rc<Cell<bool>>,
}

impl HelpPanel {
    /// Closed on the overview, version from this crate (`1.0.0` until the shell sets it).
    pub fn new() -> Self {
        let id = DEFAULT_ID.to_string();
        Self {
            open: Signal::new(false),
            article: Signal::new(id.clone()),
            query: Signal::new(String::new()),
            history: Signal::new(vec![id]),
            version: Signal::new(env!("CARGO_PKG_VERSION").to_string()),
            scroll_tick: Signal::new(0),
            app: Rc::new(RefCell::new(None)),
            field: Rc::new(RefCell::new(None)),
            headings: Rc::new(RefCell::new(Vec::new())),
            restore: Rc::new(RefCell::new(None)),
            focused_search: Rc::new(Cell::new(false)),
        }
    }

    /// Open on the overview, for a golden of the manual alone.
    pub fn preview() -> Self {
        let panel = Self::new();
        panel.open.set(true);
        panel
    }

    /// Shows `id`, or the overview when `id` is missing. Clears the search.
    pub fn open(&self, id: Option<&str>) {
        let resolved = id
            .and_then(get_article)
            .map(|article| article.id)
            .unwrap_or(DEFAULT_ID)
            .to_string();
        let was_open = self.open.get();
        self.article.set(resolved.clone());
        self.history.set(vec![resolved]);
        self.query.set(String::new());
        self.bump_scroll();
        if was_open {
            if let Some(field) = self.field.borrow().clone() {
                field.set_value("");
                field.el().focus();
            }
        } else {
            self.focused_search.set(false);
            self.open.set(true);
        }
    }

    /// Hides the manual and returns focus to whatever had it.
    pub fn close(&self) {
        if !self.open.try_get().unwrap_or(false) {
            return;
        }
        let restore = self.restore.borrow_mut().take();
        self.focused_search.set(false);
        self.open.set(false);
        if let Some(restore) = restore {
            restore.focus();
        }
    }

    /// Whether the overlay is up.
    pub fn is_open(&self) -> bool {
        self.open.try_get().unwrap_or(false)
    }

    /// False after the runtime that created the panel has been disposed.
    pub(crate) fn is_alive(&self) -> bool {
        self.open.is_alive()
    }

    /// Desktop version written into Report an issue. A leading `v` is stripped later.
    pub fn set_version(&self, version: impl Into<String>) {
        self.version.set(version.into());
    }

    /// Browser opener. Without it, http(s) and mailto fall back to the OS.
    pub fn attach_app(&self, app: App) {
        *self.app.borrow_mut() = Some(app);
    }

    /// Anchor plus overlay. Takes no space while closed.
    pub fn view(&self, ui: &Ui) -> El {
        crate::fonts::prepare_theme(ui, &crate::fonts::Fonts::load());
        let panel = self.clone();
        let ui_for = ui.clone();
        show(
            ui,
            {
                let panel = panel.clone();
                move || panel.open.try_get().unwrap_or(false)
            },
            move || panel.mount(&ui_for),
        )
        .el()
    }

    fn mount(&self, ui: &Ui) -> El {
        let layer = self.layer(ui);
        let overlay = ui.overlay(layer);
        let anchor = ui.el("help_anchor");
        let panel = self.clone();
        anchor.on_removed(move || {
            overlay.remove();
            *panel.field.borrow_mut() = None;
        })
    }

    fn layer(&self, ui: &Ui) -> El {
        if self.restore.borrow().is_none() {
            *self.restore.borrow_mut() = ui.tree().focused().map(|id| ui.element(id));
        }
        let host = ui.el("help_frame").column().align(Align::Center).justify(Justify::Center).layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        });
        let host_key = host.clone();
        let panel = self.clone();
        let ui_size = ui.clone();
        let narrow_at = Rc::new(Cell::new(None::<bool>));
        let host = host.bind(move || {
            let size = ui_size.window_size();
            if size.width <= 0.0 || size.height <= 0.0 {
                return;
            }
            let narrow = size.width < NARROW_AT;
            if narrow_at.get() == Some(narrow) {
                return;
            }
            narrow_at.set(Some(narrow));
            let panel = panel.clone();
            let ui_size = ui_size.clone();
            let host_key = host_key.clone();
            untrack(move || {
                host_key.clear();
                let _ = host_key.child(panel.frame(&ui_size, narrow));
            });
        });
        let panel_key = self.clone();
        let panel_ptr = self.clone();
        let host_hit = host.clone();
        w::fill_window(ui.el("scrim"))
            .column()
            .focus_scope()
            .style(StylePatch::new().background(crate::tokens::depth().shadow.with_alpha(0.48)))
            .padding(Insets {
                top: 24.0,
                end: 24.0,
                bottom: 24.0,
                start: 24.0,
            })
            .child(host)
            .on_key(move |event| {
                if event.pressed && event.key == Key::Escape {
                    panel_key.close();
                    return true;
                }
                false
            })
            .on_pointer(move |event| {
                if event.phase != PointerPhase::Down {
                    return false;
                }
                let inside = host_hit.child_elements().iter().any(|child| {
                    child.bounds().is_some_and(|bounds| bounds.contains(event.position))
                        || child.child_elements().iter().any(|dialog| {
                            dialog.bounds().is_some_and(|bounds| bounds.contains(event.position))
                        })
                });
                if !inside {
                    panel_ptr.close();
                    return true;
                }
                false
            })
    }

    fn frame(&self, ui: &Ui, narrow: bool) -> El {
        let colors = ui.theme().colors.clone();
        let line = colors.line.unwrap_or(colors.muted);
        let dialog = (if narrow { ui.el("dialog").column() } else { ui.el("dialog").row() })
            .clip()
            .fill_width()
            .layout(|l| {
                l.max_width = Length::Px(1024.0);
                l.max_height = Length::Px(920.0);
                l.min_width = Length::Px(280.0);
                l.min_height = Length::Px(240.0);
                l.height = Length::Percent(1.0);
            })
            .style(
                StylePatch::new()
                    .background(colors.bg)
                    .radius(ui.theme().radii.large)
                    .border(line)
                    .border_width(1.0),
            )
            .semantics(
                Semantics::new(Role::Dialog)
                    .label("Help \u{2014} Owner's Manual")
                    .modal(true),
            )
            .child(self.nav(ui, narrow))
            .child(w::divider(ui, !narrow))
            .child(self.article_pane(ui));
        self.schedule_search_focus(ui);
        dialog
    }

    fn schedule_search_focus(&self, ui: &Ui) {
        if self.focused_search.get() {
            return;
        }
        self.focused_search.set(true);
        let field = self.field.borrow().clone();
        // `after` borrows `ui` for the call. The closure has to own its own
        // handle, or edition 2024 rejects the move (E0505).
        let later = ui.clone();
        let _ = ui.after(Duration::from_millis(50), move || {
            if let Some(field) = field {
                let el = field.el();
                if later.tree().contains(el.id()) {
                    el.focus();
                }
            }
        });
    }

    fn nav(&self, ui: &Ui, narrow: bool) -> El {
        let colors = ui.theme().colors.clone();
        let line = colors.line.unwrap_or(colors.muted);
        let edge = if narrow { BorderEdge::Bottom } else { BorderEdge::End };
        ui.el("help_nav")
            .column()
            .style(
                StylePatch::new()
                    .background(colors.panel)
                    .border_side(edge, BorderSide::new(1.0, line)),
            )
            .layout(|l| {
                l.shrink = 0.0;
                if narrow {
                    l.width = Length::Percent(1.0);
                    l.max_height = Length::Percent(0.34);
                } else {
                    l.width = Length::Px(NAV_WIDTH);
                    l.height = Length::Percent(1.0);
                }
            })
            .child(self.nav_header(ui))
            .child(self.toc(ui))
            .child(self.nav_actions(ui))
    }

    fn nav_header(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let count = articles().len();
        ui.el("help_nav_header")
            .column()
            .gap(unit)
            .fill_width()
            .padding(Insets {
                top: unit * 3.0,
                end: unit * 2.0,
                bottom: unit * 2.0,
                start: unit * 2.0,
            })
            .style(StylePatch::new().border_side(
                BorderEdge::Bottom,
                BorderSide::new(1.0, ui.theme().colors.line.unwrap_or(ui.theme().colors.muted)),
            ))
            .child(w::text(ui, "Owner's Manual"))
            .child(w::text(ui, format!("Offline wiki \u{00B7} {count} chapters")).variant("muted"))
            .child(self.search_row(ui))
    }

    fn search_row(&self, ui: &Ui) -> El {
        let field = w::text_input(ui, "Search help articles").placeholder("Search help\u{2026}");
        let _ = field.el().variant("bare");
        field.set_value(&self.query.get());
        let watch = field.clone();
        let panel = self.clone();
        let field = field.on_change(move || panel.query.set(watch.value()));
        *self.field.borrow_mut() = Some(field.clone());
        let gap = ui.theme().spacing.unit * 1.5;
        ui.el("search")
            .row()
            .align(Align::Center)
            .gap(gap)
            .fill_width()
            .child(w::icon(ui, w::Icon::Search, ui.theme().type_scale.body + 2.0))
            .child(field.el().grow(1.0))
    }

    fn toc(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let list = ui.el("help_toc").column().fill_width().gap(crate::tokens::space() * 0.5);
        let list_key = list.clone();
        let panel = self.clone();
        let ui_toc = ui.clone();
        let list = list.bind(move || {
            let query = panel.query.get();
            let current = panel.article.get();
            let panel = panel.clone();
            let ui_toc = ui_toc.clone();
            let list_key = list_key.clone();
            untrack(move || {
                list_key.clear();
                if query.trim().is_empty() {
                    for group in articles_by_category() {
                        let _ = list_key.clone().child(
                            w::text(&ui_toc, group.category.to_uppercase())
                                .variant("muted")
                                .padding(Insets {
                                    top: 8.0,
                                    end: 8.0,
                                    bottom: 2.0,
                                    start: 8.0,
                                }),
                        );
                        for article in group.articles {
                            let id = article.id.to_string();
                            let panel = panel.clone();
                            let _ = list_key.clone().child(toc_button(
                                &ui_toc,
                                article.title,
                                None,
                                article.id == current,
                                move || panel.navigate(&id),
                            ));
                        }
                    }
                } else {
                    let hits = search_articles(&query);
                    let noun = if hits.len() == 1 { "result" } else { "results" };
                    let _ = list_key.clone().child(
                        w::text(&ui_toc, format!("{} {noun}", hits.len()))
                            .variant("muted")
                            .padding(Insets {
                                top: 6.0,
                                end: 8.0,
                                bottom: 2.0,
                                start: 8.0,
                            }),
                    );
                    if hits.is_empty() {
                        let _ = list_key.clone().child(
                            w::text(
                                &ui_toc,
                                "No matches. Try \u{201C}oauth\u{201D}, \u{201C}compact\u{201D}, or \u{201C}uninstall\u{201D}.",
                            )
                            .variant("muted")
                            .padding(Insets {
                                top: 8.0,
                                end: 8.0,
                                bottom: 8.0,
                                start: 8.0,
                            }),
                        );
                    }
                    for article in hits {
                        let id = article.id.to_string();
                        let panel = panel.clone();
                        let _ = list_key.clone().child(toc_button(
                            &ui_toc,
                            article.title,
                            Some(article.summary),
                            article.id == current,
                            move || panel.navigate(&id),
                        ));
                    }
                }
            });
        });
        w::scroll(ui)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            })
            .padding(Insets {
                top: unit,
                end: unit,
                bottom: unit,
                start: unit,
            })
            .child(list)
    }

    fn nav_actions(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let row = ui
            .el("help_nav_actions")
            .row()
            .gap(unit)
            .fill_width()
            .padding(Insets {
                top: unit * 2.0,
                end: unit * 2.0,
                bottom: unit * 2.0,
                start: unit * 2.0,
            })
            .style(StylePatch::new().border_side(
                BorderEdge::Top,
                BorderSide::new(1.0, ui.theme().colors.line.unwrap_or(ui.theme().colors.muted)),
            ));
        let row_key = row.clone();
        let panel = self.clone();
        let ui_row = ui.clone();
        let row = row.bind(move || {
            let can_back = panel.history.get().len() > 1;
            let panel = panel.clone();
            let ui_row = ui_row.clone();
            let row_key = row_key.clone();
            untrack(move || {
                row_key.clear();
                let back = w::button(&ui_row, "\u{2190} Back", {
                    let panel = panel.clone();
                    move || panel.go_back()
                })
                .variant("quiet")
                .disabled(!can_back)
                .grow(1.0);
                let home = w::button(&ui_row, "Home", {
                    let panel = panel.clone();
                    move || panel.navigate(DEFAULT_ID)
                })
                .variant("quiet")
                .grow(1.0);
                let _ = row_key.child(back).child(home);
            });
        });
        row
    }

    fn article_pane(&self, ui: &Ui) -> El {
        let colors = ui.theme().colors.clone();
        ui.el("help_article")
            .column()
            .style(StylePatch::new().background(colors.bg))
            .layout(|l| {
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_width = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
            })
            .child(self.article_header(ui))
            .child(self.article_body(ui))
            .child(self.footer(ui))
    }

    fn article_header(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let colors = ui.theme().colors.clone();
        let line = colors.line.unwrap_or(colors.muted);
        let head = ui
            .el("help_header")
            .row()
            .align(Align::Center)
            .gap(unit * 2.0)
            .fill_width()
            .padding(Insets {
                top: unit * 3.0,
                end: unit * 3.0,
                bottom: unit * 3.0,
                start: unit * 4.0,
            })
            .style(StylePatch::new().background(colors.panel).border_side(BorderEdge::Bottom, BorderSide::new(1.0, line)));
        let titles = ui.el("help_titles").column().grow(1.0).gap(crate::tokens::space() * 0.5);
        let titles_key = titles.clone();
        let panel = self.clone();
        let ui_head = ui.clone();
        let titles = titles.bind(move || {
            let id = panel.article.get();
            let ui_head = ui_head.clone();
            let titles_key = titles_key.clone();
            untrack(move || {
                titles_key.clear();
                let article = get_article(&id).or_else(|| articles().first());
                let category = article.map(|article| article.category).unwrap_or("");
                let title = article.map(|article| article.title).unwrap_or("Help");
                let summary = article.map(|article| article.summary).unwrap_or("");
                let _ = titles_key
                    .child(w::text(&ui_head, category.to_uppercase()).variant("muted"))
                    .child(w::text(&ui_head, title).nowrap().truncate())
                    .child(w::text(&ui_head, summary).variant("muted").max_lines(2));
            });
        });
        let panel = self.clone();
        head.child(titles).child(tool_button(ui, w::Icon::Close, "Close help", move || panel.close()))
    }

    fn article_body(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let scroller = w::scroll(ui).layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        });
        let body = ui
            .el("help_body")
            .column()
            .fill_width()
            .padding(Insets {
                top: unit * 3.0,
                end: unit * 4.0,
                bottom: unit * 4.0,
                start: unit * 4.0,
            });
        let body_key = body.clone();
        let scroller_key = scroller.clone();
        let panel = self.clone();
        let ui_body = ui.clone();
        let body = body.bind(move || {
            let id = panel.article.get();
            let _tick = panel.scroll_tick.get();
            let panel = panel.clone();
            let ui_body = ui_body.clone();
            let body_key = body_key.clone();
            let scroller_key = scroller_key.clone();
            untrack(move || {
                body_key.clear();
                let Some(article) = get_article(&id).or_else(|| articles().first()) else {
                    return;
                };
                let on_href = panel.follow();
                let rendered = article::render(&ui_body, &article.body, &panel.headings, on_href);
                let _ = body_key.child(rendered);
                scroller_key.scroll_to(Vec2 { x: 0.0, y: 0.0 });
            });
        });
        scroller.child(body)
    }

    fn footer(&self, ui: &Ui) -> El {
        let unit = ui.theme().spacing.unit;
        let colors = ui.theme().colors.clone();
        let line = colors.line.unwrap_or(colors.muted);
        let links = ui
            .el("help_footer_links")
            .row()
            .gap(unit * 3.0)
            .align(Align::Center)
            .layout(|l| {
                // Whole links stay intact. The tip takes the leftover width and wraps.
                l.shrink = 0.0;
            })
            .child(self.footer_nav(ui, "Commands", "11-reference-commands"))
            .child(self.footer_nav(ui, "Shortcuts", "12-reference-shortcuts"))
            .child(self.footer_nav(ui, "Fix a problem", "09-troubleshooting"))
            .child(self.footer_report(ui));
        ui.el("help_footer")
            .row()
            .align(Align::Center)
            .gap(unit * 2.0)
            .fill_width()
            .padding(Insets {
                top: unit * 2.0,
                end: unit * 3.0,
                bottom: unit * 2.0,
                start: unit * 4.0,
            })
            .style(
                StylePatch::new()
                    .background(colors.panel)
                    .foreground(colors.muted)
                    .border_side(BorderEdge::Top, BorderSide::new(1.0, line)),
            )
            .child(
                w::text(
                    ui,
                    "Tip: wiki links navigate in-app \u{00B7} external links open in browser \u{00B7} Esc closes",
                )
                .variant("muted")
                .grow(1.0),
            )
            .child(links)
    }

    fn footer_nav(&self, ui: &Ui, label: &str, id: &'static str) -> El {
        let panel = self.clone();
        footer_link(w::link(ui, label, move || panel.navigate(id)))
    }

    fn footer_report(&self, ui: &Ui) -> El {
        let panel = self.clone();
        footer_link(w::link(ui, "Report an issue", move || {
            let url = report_issue_url(&panel.version.get());
            panel.open_external(&url);
        }))
    }

    fn follow(&self) -> Rc<dyn Fn(&str)> {
        let panel = self.clone();
        Rc::new(move |href: &str| {
            if let Some(anchor) = href.strip_prefix('#') {
                let anchor = percent_decode(anchor);
                let target = panel
                    .headings
                    .borrow()
                    .iter()
                    .find(|(id, _)| id == &anchor)
                    .map(|(_, el)| el.clone());
                if let Some(target) = target {
                    target.scroll_into_view();
                    target.focus();
                }
                return;
            }
            if let Some(id) = catalog::resolve_wiki_href(href) {
                panel.navigate(id);
                return;
            }
            if let Some(url) = catalog::resolve_external_help_href(Some(href)) {
                panel.open_external(&url);
            }
        })
    }

    fn navigate(&self, id: &str) {
        let Some(article) = get_article(id) else {
            return;
        };
        if self.article.get() != article.id {
            let mut history = self.history.get();
            if history.last().map(String::as_str) != Some(article.id) {
                history.push(article.id.to_string());
                self.history.set(history);
            }
            self.article.set(article.id.to_string());
        }
        self.bump_scroll();
    }

    fn go_back(&self) {
        let mut history = self.history.get();
        if history.len() <= 1 {
            return;
        }
        history.pop();
        if let Some(id) = history.last().cloned() {
            self.history.set(history);
            self.article.set(id);
            self.bump_scroll();
        }
    }

    fn bump_scroll(&self) {
        self.scroll_tick.set(self.scroll_tick.get().wrapping_add(1));
    }

    fn open_external(&self, url: &str) {
        let lower = url.to_ascii_lowercase();
        if !(lower.starts_with("http://") || lower.starts_with("https://") || lower.starts_with("mailto:")) {
            return;
        }
        if let Some(app) = self.app.borrow().clone() {
            app.open_url(url, |_| {});
            return;
        }
        open_with_os(url);
    }
}

impl Default for HelpPanel {
    fn default() -> Self {
        Self::new()
    }
}

/// A footer link stays one line. Squeezing the row must not break "Commands" in half.
fn footer_link(link: El) -> El {
    link.nowrap().layout(|l| l.shrink = 0.0)
}

fn toc_button(ui: &Ui, title: &str, subtitle: Option<&str>, active: bool, on_press: impl Fn() + 'static) -> El {
    let colors = ui.theme().colors.clone();
    let mark = if active { colors.accent } else { colors.panel.with_alpha(0.0) };
    let bg = if active {
        colors.accent.mix_oklab(colors.panel, 0.86)
    } else {
        colors.panel.with_alpha(0.0)
    };
    let fg = if active { colors.accent } else { colors.text };
    // Titles wrap inside the 240 px nav, the same way the current wiki does.
    // Truncating them hides "continuity" and "nano swarm".
    let mut column = ui
        .el("help_toc_label")
        .column()
        .grow(1.0)
        .gap(crate::tokens::space() * 0.25)
        .layout(|l| {
            l.min_width = Length::Px(0.0);
            l.shrink = 1.0;
        })
        .child(
            w::text(ui, title.to_string())
                .layout(|l| {
                    l.min_width = Length::Px(0.0);
                    l.shrink = 1.0;
                    l.width = Length::Percent(1.0);
                })
                .style(StylePatch::new().foreground(fg)),
        );
    if let Some(subtitle) = subtitle {
        column = column.child(
            w::text(ui, subtitle.to_string())
                .variant("muted")
                .max_lines(2),
        );
    }
    let button = ui
        .el("button")
        .variant("quiet")
        .row()
        .align(Align::Center)
        .justify(Justify::Start)
        .gap(crate::tokens::space() * 2.0)
        .fill_width()
        .padding(Insets {
            top: 6.0,
            end: 8.0,
            bottom: 6.0,
            start: 8.0,
        })
        .style(
            StylePatch::new()
                .background(bg)
                .foreground(fg)
                .radius(ui.theme().radii.small)
                .border_side(BorderEdge::Start, BorderSide::new(SELECT_MARK, mark)),
        )
        .child(column)
        .on_click(on_press)
        .semantics(Semantics::new(Role::Button).label(title.to_string()).action(Action::Click));
    button.state(States::SELECTED, active);
    button
}

fn open_with_os(url: &str) {
    #[cfg(windows)]
    {
        let _ = crate::slides::hostcmd::shell_open(std::ffi::OsStr::new(url));
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(url).spawn();
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = Command::new("xdg-open").arg(url).spawn();
    }
}

/// GitHub issue form from `desktop/src/utils/reportIssue.ts`.
pub(crate) fn report_issue_url(version: &str) -> String {
    let ver = if version.is_empty() { "unknown" } else { version };
    let ver = ver.strip_prefix('v').or_else(|| ver.strip_prefix('V')).unwrap_or(ver);
    let body = encode_uri_component(&format!(
        "## What happened\n\n(Describe the issue\u{2026})\n\n## Steps to reproduce\n\n1. \n\n## Expected vs actual\n\n\n## Environment\n\n- Remedy Desktop: v{ver}\n- OS: Windows\n"
    ));
    let title = encode_uri_component(&format!("[Desktop v{ver}] "));
    format!(
        "https://github.com/AhmiDarrow/RemedyAI/issues/new?title={title}&body={body}&labels=bug"
    )
}

fn encode_uri_component(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'!' | b'~' | b'*' | b'\'' | b'('
            | b')' => out.push(byte as char),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::report_issue_url;

    #[test]
    fn report_url_matches_the_desktop_prefill() {
        let url = report_issue_url("v1.2.3");
        assert!(url.starts_with("https://github.com/AhmiDarrow/RemedyAI/issues/new?"));
        assert!(url.contains("title=%5BDesktop%20v1.2.3%5D%20"));
        assert!(url.contains("Remedy%20Desktop%3A%20v1.2.3"));
        assert!(url.contains("%E2%80%A6"));
        assert!(url.contains("labels=bug"));
        assert!(report_issue_url("").contains("vunknown"));
        assert!(report_issue_url("V9").contains("title=%5BDesktop%20v9%5D%20"));
    }
}
