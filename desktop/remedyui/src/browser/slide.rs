//! Browser rail chrome. The toolbar, address field, and status line match
//! `BrowserSlide.tsx`. The page itself is a RemedyUI web view placed in the
//! host under the toolbar, so overlays (the bookmarks menu) punch it out.

use std::cell::RefCell;
use std::rc::Rc;

use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::style::{Pad, StylePatch};
use remedy_ui::webview::{WebViewOptions, WebViewStatus, web_view};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, show};

use super::host::Model;
use super::layout::{browser_is_open, browser_width};
use super::url::{self, is_openable_browser_url};
use crate::messages;
use crate::studio::Studio;

thread_local! {
    static APP: RefCell<Option<App>> = const { RefCell::new(None) };
}

/// The live window, so ↗ can call [`App::open_url`] on the UI thread.
pub fn bind_app(app: App) {
    APP.with(|slot| *slot.borrow_mut() = Some(app));
}

/// Right-hand column. Hidden, and taking no room, until the layout opens Browser.
pub fn rail(ui: &Ui, studio: Studio) -> El {
    let model = super::host::ensure(studio);
    let gate = model.clone();
    let built = model;
    let ui_build = ui.clone();
    show(ui, move || browser_is_open(&gate.layout()), move || open_rail(&ui_build, &built)).el()
}

/// Browser slide body for the Studio rail and its pop-out. Fills the parent.
pub fn body(ui: &Ui) -> El {
    match super::host::live() {
        Some(model) => chrome(ui, &model, None),
        None => preview(ui),
    }
}

/// The page alone. Grove's Alongside toolbar stays; this is the web view under it.
pub fn page(ui: &Ui) -> El {
    let Some(model) = super::host::live() else {
        return w::column(ui);
    };
    if model.is_live() {
        model.autostart();
    }
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .child(host(ui, &model))
}

fn open_rail(ui: &Ui, model: &Model) -> El {
    let width = browser_width(&model.layout());
    w::row(ui)
        .layout(|l| {
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(w::divider(ui, true))
        .child(chrome(ui, model, Some(width)))
}

/// Toolbar and empty state with no web view and no poller. Screenshot and chrome tests use this.
pub fn preview(ui: &Ui) -> El {
    preview_at(ui, 480.0)
}

/// Same chrome as [`preview`], at `width` logical pixels (a narrow rail).
pub fn preview_at(ui: &Ui, width: f32) -> El {
    chrome(ui, &Model::preview(), Some(width))
}

fn chrome(ui: &Ui, model: &Model, width: Option<f32>) -> El {
    if model.is_live() {
        model.autostart();
    }
    let colors = ui.theme().colors.clone();
    let column = w::column(ui)
        .style(StylePatch::new().background(colors.bg))
        .layout(move |l| {
            l.width = match width {
                Some(px) => Length::Px(px),
                None => Length::Percent(1.0),
            };
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        });
    let field = address_field(ui, model);
    let column = column
        .child(toolbar(ui, model, &field))
        .child(host(ui, model))
        .child(status_line(ui, model));
    let watch = field.clone();
    let followed = model.clone();
    column.bind(move || {
        if followed.editing() {
            return;
        }
        let next = followed.live_url();
        if !next.is_empty() && watch.value() != next {
            watch.set_value(&next);
        }
    })
}

fn address_field(ui: &Ui, model: &Model) -> remedy_ui::view::TextField {
    let hint_ui = ui.clone();
    let submit_field_model = model.clone();
    let field = w::text_input(ui, messages().text(ui, "browser-address"))
        .placeholder(messages().text(&hint_ui, "browser-address-placeholder"));
    let typed = field.clone();
    let typing = model.clone();
    let field = field.on_change(move || {
        typing.set_editing(true);
        typing.set_address_text(typed.value());
    });
    let submitted = field.clone();
    let going = submit_field_model;
    let field = field.on_submit(move || {
        going.set_editing(false);
        let text = submitted.value();
        going.go_address(&text);
    });
    let escape = field.clone();
    let restoring = model.clone();
    let _ = field.el().on_key(move |event| {
        if event.pressed && event.key == Key::Escape {
            restoring.set_editing(false);
            let live = restoring.live_url();
            escape.set_value(&live);
            restoring.set_address_text(live);
            return true;
        }
        false
    });
    field
}

fn toolbar(ui: &Ui, model: &Model, field: &remedy_ui::view::TextField) -> El {
    let shown = model.clone();
    let ui_bar = ui.clone();
    let field = field.clone();
    let model = model.clone();
    show(ui, move || !shown.fullscreen(), move || {
        let colors = ui_bar.theme().colors.clone();
        let unit = ui_bar.theme().spacing.unit;
        let back = nav_button(&ui_bar, &model, w::Icon::ArrowLeft, "browser-back", |m| m.go_back(), true);
        let forward = nav_button(&ui_bar, &model, w::Icon::ArrowRight, "browser-forward", |m| m.go_forward(), true);
        let home = nav_button(&ui_bar, &model, w::Icon::Home, "browser-home", |m| m.go_home(), false);
        let reload = nav_button(&ui_bar, &model, w::Icon::Refresh, "browser-reload", |m| m.reload(), true);
        let address = field.el().grow(1.0).layout(|l| {
            l.min_width = Length::Px(0.0);
            l.shrink = 1.0;
            l.grow = 1.0;
        });
        let go_ui = ui_bar.clone();
        let star_ui = ui_bar.clone();
        let star_model = model.clone();
        let star_field = field.clone();
        let bookmarks_ui = ui_bar.clone();
        let bookmarks_model = model.clone();
        let site_ui = ui_bar.clone();
        let site_model = model.clone();
        let external_ui = ui_bar.clone();
        let close_ui = ui_bar.clone();
        w::overflow_row(&ui_bar)
            .gap(unit)
            .keep(back)
            .keep(forward)
            .child(messages().text(&ui_bar, "browser-home"), home)
            .keep(shield_button(&ui_bar, &model))
            .child(messages().text(&ui_bar, "browser-reload"), reload)
            .flex(address)
            .child_fn(move || messages().text(&go_ui, "browser-go"), go_button(&ui_bar, &model, &field))
            .child_fn(
                move || {
                    let url = current_target(&star_field, &star_model);
                    let id = if star_model.bookmarked(&url) { "browser-unbookmark" } else { "browser-bookmark" };
                    messages().text(&star_ui, id)
                },
                star_button(&ui_bar, &model, &field),
            )
            .child_submenu(
                messages().text(&bookmarks_ui, "browser-bookmarks"),
                bookmarks_button(&ui_bar, &model),
                {
                    let ui = bookmarks_ui.clone();
                    let model = bookmarks_model;
                    move || bookmark_menu_items(&ui, &model)
                },
            )
            .child_fn(
                move || {
                    let id = if site_model.desktop() { "browser-desktop" } else { "browser-mobile" };
                    messages().text(&site_ui, id)
                },
                site_button(&ui_bar, &model),
            )
            .child(messages().text(&external_ui, "browser-external"), external_button(&ui_bar, &model, &field))
            .child(messages().text(&close_ui, "browser-close"), close_button(&ui_bar, &model))
            .el()
            .padding(Pad::all(unit.max(4.0) * 0.5))
            .style(StylePatch::new().background(colors.card))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            })
    })
    .el()
    .layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    })
}

fn nav_button(
    ui: &Ui,
    model: &Model,
    icon: w::Icon,
    label_id: &'static str,
    act: fn(&Model),
    needs_page: bool,
) -> El {
    let label_ui = ui.clone();
    let press = model.clone();
    let gate = model.clone();
    let tip_ui = ui.clone();
    let btn = w::icon_button(ui, icon, move || messages().text(&label_ui, label_id), move || act(&press)).disabled(move || {
        needs_page && (!gate.loaded() || gate.busy())
    });
    w::tooltip(&btn, move || messages().text(&tip_ui, label_id));
    btn
}

fn go_button(ui: &Ui, model: &Model, field: &remedy_ui::view::TextField) -> El {
    let label_ui = ui.clone();
    let field = field.clone();
    let model = model.clone();
    w::button(ui, move || messages().text(&label_ui, "browser-go"), move || {
        model.set_editing(false);
        let text = field.value();
        model.go_address(&text);
    })
    .variant("primary")
}

fn shield_button(ui: &Ui, model: &Model) -> El {
    let on = model.clone();
    let ui_on = ui.clone();
    let model_on = model.clone();
    let ui_off = ui.clone();
    let model_off = model.clone();
    show(ui, move || on.shield_on(), move || shield_icon(&ui_on, &model_on, w::Icon::Lock, true))
        .fallback(move || shield_icon(&ui_off, &model_off, w::Icon::Unlock, false))
        .el()
}

fn shield_icon(ui: &Ui, model: &Model, icon: w::Icon, on: bool) -> El {
    let label_ui = ui.clone();
    let model_label = model.clone();
    let press = model.clone();
    let colors = ui.theme().colors.clone();
    let fg = if on { colors.accent } else { colors.muted };
    let btn = w::icon_button(
        ui,
        icon,
        move || {
            let id = if model_label.shield_on() { "browser-shield-on" } else { "browser-shield-off" };
            messages().text(&label_ui, id)
        },
        move || press.toggle_shield(),
    )
    .style(StylePatch::new().foreground(fg));
    let tip_ui = ui.clone();
    let tip_model = model.clone();
    w::tooltip(&btn, move || {
        let id = if tip_model.shield_on() { "browser-shield-on" } else { "browser-shield-off" };
        messages().text(&tip_ui, id)
    });
    btn
}

fn star_button(ui: &Ui, model: &Model, field: &remedy_ui::view::TextField) -> El {
    let label_ui = ui.clone();
    let model_label = model.clone();
    let field_label = field.clone();
    let press = model.clone();
    let field_press = field.clone();
    w::button(
        ui,
        move || {
            let url = current_target(&field_label, &model_label);
            let id = if model_label.bookmarked(&url) { "browser-unbookmark" } else { "browser-bookmark" };
            messages().text(&label_ui, id)
        },
        move || {
            let url = current_target(&field_press, &press);
            press.toggle_bookmark(&url);
        },
    )
}

fn site_button(ui: &Ui, model: &Model) -> El {
    let label_ui = ui.clone();
    let model_label = model.clone();
    let press = model.clone();
    w::button(
        ui,
        move || {
            let id = if model_label.desktop() { "browser-desktop" } else { "browser-mobile" };
            messages().text(&label_ui, id)
        },
        move || press.toggle_desktop(),
    )
}

fn external_button(ui: &Ui, model: &Model, field: &remedy_ui::view::TextField) -> El {
    let label_ui = ui.clone();
    let model = model.clone();
    let field = field.clone();
    let tip_ui = ui.clone();
    let btn = w::icon_button(ui, w::Icon::External, move || messages().text(&label_ui, "browser-external"), move || {
        open_external(&model, &current_target(&field, &model));
    });
    w::tooltip(&btn, move || messages().text(&tip_ui, "browser-external"));
    btn
}

fn close_button(ui: &Ui, model: &Model) -> El {
    let label_ui = ui.clone();
    let model = model.clone();
    let tip_ui = ui.clone();
    let btn = w::icon_button(ui, w::Icon::Close, move || messages().text(&label_ui, "browser-close"), move || model.close_embed());
    w::tooltip(&btn, move || messages().text(&tip_ui, "browser-close"));
    btn
}

fn bookmarks_button(ui: &Ui, model: &Model) -> El {
    let anchor: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let open_anchor = anchor.clone();
    let open_ui = ui.clone();
    let open_model = model.clone();
    let label_ui = ui.clone();
    let button = w::button(ui, move || messages().text(&label_ui, "browser-bookmarks"), move || {
        let Some(anchor) = open_anchor.borrow().clone() else { return };
        let list = open_model.bookmarks();
        let unit = open_ui.theme().spacing.unit;
        let mut column = w::column(&open_ui)
            .gap(unit)
            .padding(Pad::all(unit * 2.0))
            .layout(|l| l.min_width = Length::Px(220.0));
        if list.is_empty() {
            column = column.child(w::text(&open_ui, messages().text(&open_ui, "browser-bookmarks-empty")).variant("muted"));
        } else {
            for mark in list {
                let url = mark.url.clone();
                let going = open_model.clone();
                let title = if mark.title.is_empty() { url.clone() } else { mark.title.clone() };
                column = column.child(w::button(&open_ui, title, move || {
                    going.set_editing(false);
                    going.navigate_url(&url, None);
                }));
            }
        }
        w::popover(&open_ui, &anchor, column);
    });
    *anchor.borrow_mut() = Some(button.clone());
    button
}

fn bookmark_menu_items(ui: &Ui, model: &Model) -> Vec<w::MenuItem> {
    let list = model.bookmarks();
    if list.is_empty() {
        return vec![w::MenuItem::new(messages().text(ui, "browser-bookmarks-empty"), || {}).disabled(true)];
    }
    list.into_iter()
        .map(|mark| {
            let url = mark.url.clone();
            let going = model.clone();
            let title = if mark.title.is_empty() { url.clone() } else { mark.title.clone() };
            w::MenuItem::new(title, move || {
                going.set_editing(false);
                going.navigate_url(&url, None);
            })
        })
        .collect()
}

fn host(ui: &Ui, model: &Model) -> El {
    let placed = model.clone();
    let area = ui
        .el("browser_host")
        .column()
        .grow(1.0)
        .layout(|l| {
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(160.0);
            l.min_width = Length::Px(0.0);
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .on_placed(move |placement| {
            if let Some(place) = placement {
                let bounds = place.bounds;
                placed.note_bounds(bounds.min.x, bounds.min.y, bounds.width(), bounds.height(), place.scale);
            }
        });
    let gate = model.clone();
    let page_model = model.clone();
    let page_ui = ui.clone();
    let empty_model = model.clone();
    let empty_ui = ui.clone();
    area.child(
        show(ui, move || gate.loaded(), move || embed(&page_ui, &page_model))
            .fallback(move || empty_state(&empty_ui, &empty_model))
            .el()
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_width = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            }),
    )
}

fn embed(ui: &Ui, model: &Model) -> El {
    let dir = super::paths::webview_data_dir();
    let _ = std::fs::create_dir_all(&dir);
    let allow_model = model.clone();
    let options = WebViewOptions::new("Browser")
        .devtools(true)
        .data_dir(dir)
        .downloads(|req| {
            let url = req.url.to_ascii_lowercase();
            url.starts_with("http://") || url.starts_with("https://")
        })
        .allow(move |req| allow_model.allow_navigation(&req.url));
    let (element, view) = web_view(ui, options);
    model.attach_view(view.clone());
    let progress = model.clone();
    let watched = view.clone();
    let last_status = std::rc::Rc::new(std::cell::RefCell::new(None::<WebViewStatus>));
    element
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .bind(move || {
            let url = watched.url();
            let loading = watched.is_loading();
            progress.on_progress(&url, loading);
            let status = watched.status();
            let mut last = last_status.borrow_mut();
            if last.as_ref() != Some(&status) {
                *last = Some(status.clone());
                match &status {
                    WebViewStatus::Unsupported(reason) | WebViewStatus::Failed(reason) => {
                        progress.flash(&format!("Browser unavailable: {reason}"));
                    }
                    _ => {}
                }
            }
        })
}

fn empty_state(ui: &Ui, model: &Model) -> El {
    let title_ui = ui.clone();
    let body_ui = ui.clone();
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let column = w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(unit * 2.0)
        .padding(Pad::all(unit * 4.0))
        .grow(1.0)
        .child(
            w::text(ui, move || messages().text(&title_ui, "browser-empty-title")).style(StylePatch::new().foreground(colors.text)),
        )
        .child(
            w::text(ui, move || messages().text(&body_ui, "browser-empty-body"))
                .variant("muted")
                .layout(|l| l.max_width = Length::Px(360.0)),
        );
    let busy = model.clone();
    let busy_ui = ui.clone();
    column.child(
        show(ui, move || busy.busy(), move || w::text(&busy_ui, messages().text(&busy_ui, "browser-loading"))).el(),
    )
}

fn status_line(ui: &Ui, model: &Model) -> El {
    let shown = model.clone();
    let ui_line = ui.clone();
    let model = model.clone();
    show(ui, move || !shown.fullscreen(), move || {
        let colors = ui_line.theme().colors.clone();
        let unit = ui_line.theme().spacing.unit;
        let text_model = model.clone();
        let text_ui = ui_line.clone();
        let row = w::row(&ui_line)
            .align(Align::Center)
            .gap(unit)
            .padding(Pad::all(unit))
            .style(StylePatch::new().background(colors.card))
            .child(
                w::text(&ui_line, move || {
                    let status = text_model.status();
                    if !status.is_empty() {
                        return status;
                    }
                    if text_model.loaded() {
                        let url = text_model.live_url();
                        if !url.is_empty() {
                            return url;
                        }
                    }
                    messages().text(&text_ui, "browser-ready")
                })
                .variant("muted")
                .truncate()
                .grow(1.0)
                .layout(|l| {
                    l.min_width = Length::Px(0.0);
                }),
            );
        let busy = model.clone();
        let dot_ui = ui_line.clone();
        row.child(show(&ui_line, move || busy.busy(), move || w::text(&dot_ui, "…")).el())
    })
    .el()
}

fn current_target(field: &remedy_ui::view::TextField, model: &Model) -> String {
    let typed = field.value();
    if !typed.trim().is_empty() {
        return typed;
    }
    let live = model.live_url();
    if !live.is_empty() {
        live
    } else {
        model.address()
    }
}

fn open_external(model: &Model, raw: &str) {
    let normalized = url::normalize_browser_url(raw);
    let url = if normalized.is_empty() { raw.trim().to_string() } else { normalized };
    let lower = url.to_ascii_lowercase();
    let openable = is_openable_browser_url(&url)
        || lower.starts_with("http://")
        || lower.starts_with("https://")
        || lower.starts_with("mailto:");
    if url.is_empty() || !openable || !(lower.starts_with("http://") || lower.starts_with("https://") || lower.starts_with("mailto:")) {
        model.flash("Nothing to open — enter a URL first");
        return;
    }
    let Some(app) = APP.with(|slot| slot.borrow().clone()) else {
        model.flash("Open in system browser needs the desktop window");
        return;
    };
    model.flash("Opened in system browser");
    app.open_url(url, |_| {});
}
