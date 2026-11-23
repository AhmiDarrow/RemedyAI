//! The Alongside stage. The computer-use session owns the page; this is its rail.
//!
//! Back, forward, and reload say so. Go, Home, the shield, bookmarks, and
//! desktop or mobile are remembered here and handed to that host.

use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::logic;
use super::state::Grove;
use super::store::Bookmark;

pub(super) fn stage(ui: &Ui, grove: Grove) -> El {
    let colors = ui.theme().colors.clone();
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
        })
        .style(StylePatch::new().background(colors.bg))
        .child(toolbar(ui, grove.clone()))
        .child(page(ui, grove))
}

fn toolbar(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let field = w::text_input(ui, "Browser address — URL or search")
        .placeholder("Search or enter URL");
    let f = field.clone();
    let g = grove.clone();
    let el = field.el().bind(move || {
        let url = g.stage_url.get();
        if f.value() != url {
            f.set_value(&url);
        }
    });
    let g = grove.clone();
    let f = field.clone();
    let go = w::button(ui, "Go", move || g.go(&f.value()));
    let g = grove.clone();
    let back = w::button(ui, "Back", move || {
        g.stage_status
            .set("Back is the browser host’s. This rail does not move the page.".into());
    });
    let g = grove.clone();
    let forward = w::button(ui, "Forward", move || {
        g.stage_status
            .set("Forward is the browser host’s. This rail does not move the page.".into());
    });
    let g = grove.clone();
    let home = w::button(ui, "Home", move || g.go(logic::BROWSER_HOME))
        .semantics(Semantics::new(Role::Button).label("Home").action(Action::Click));
    let g = grove.clone();
    let shield = w::button(
        ui,
        move || {
            if g.shield.get() {
                "Shield on".to_string()
            } else {
                "Shield off".to_string()
            }
        },
        {
            let g = grove.clone();
            move || {
                let next = !g.shield.get();
                g.shield.set(next);
                g.stage_status.set(
                    if next {
                        "Privacy Shield on — the browser host applies it.".into()
                    } else {
                        "Privacy Shield off — the browser host applies it.".into()
                    },
                );
                g.save();
            }
        },
    );
    let g = grove.clone();
    let reload = w::button(ui, "Reload", move || {
        g.stage_status
            .set("Reload is the browser host’s. The address stays put.".into());
    });
    let g = grove.clone();
    let star = w::button(
        ui,
        move || {
            let url = g.stage_url.get();
            if g.bookmarks.get().iter().any(|b| b.url == url) {
                "Unstar".to_string()
            } else {
                "Star".to_string()
            }
        },
        {
            let g = grove.clone();
            move || g.toggle_bookmark()
        },
    );
    let g = grove.clone();
    let list = w::button(ui, "Bookmarks", move || {
        g.bookmarks_open.update(|v| *v = !*v);
    });
    let g = grove.clone();
    let site = w::button(
        ui,
        move || {
            if g.desktop_site.get() {
                "Desktop site".to_string()
            } else {
                "Mobile view".to_string()
            }
        },
        {
            let g = grove.clone();
            move || {
                let next = !g.desktop_site.get();
                g.desktop_site.set(next);
                g.stage_status.set(
                    if next {
                        "Desktop site — the browser host applies it.".into()
                    } else {
                        "Mobile view — the browser host applies it.".into()
                    },
                );
                g.save();
            }
        },
    );
    let bar = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets::all(unit))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .border(line)
                .border_width(1.0),
        )
        .child(back)
        .child(forward)
        .child(home)
        .child(shield)
        .child(reload)
        .child(el.layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(go)
        .child(star)
        .child(list)
        .child(site);
    let books = bookmarks(ui, grove);
    w::column(ui).child(bar).child(books)
}

fn bookmarks(ui: &Ui, grove: Grove) -> El {
    let g = grove.clone();
    show(ui, move || g.bookmarks_open.get(), {
        let ui = ui.clone();
        let grove = grove.clone();
        move || {
            let col = w::column(&ui).gap(ui.theme().spacing.unit).padding(Insets::all(ui.theme().spacing.unit));
            let g = grove.clone();
            let empty = show(&ui, move || g.bookmarks.get().is_empty(), {
                let ui = ui.clone();
                move || w::text(&ui, "No bookmarks yet. Star a page to save it here.").variant("muted")
            })
            .el();
            w::for_each(&col, grove.bookmarks, |b: &Bookmark| b.url.clone(), {
                let ui = ui.clone();
                let g = grove.clone();
                move |b| {
                    let url = b.url.clone();
                    let title = if b.title.is_empty() { url.clone() } else { b.title.clone() };
                    let g_open = g.clone();
                    let url_open = url.clone();
                    let g_drop = g.clone();
                    let url_drop = url.clone();
                    w::row(&ui)
                        .align(Align::Center)
                        .gap(8.0)
                        .child(w::button(&ui, title, move || g_open.open_bookmark(&url_open)))
                        .child(w::button(&ui, "Remove", move || g_drop.remove_bookmark(&url_drop)))
                }
            });
            col.child(empty)
        }
    })
    .el()
}

fn page(ui: &Ui, grove: Grove) -> El {
    let unit = ui.theme().spacing.unit;
    let g = grove.clone();
    w::column(ui)
        .gap(unit)
        .padding(Insets::all(unit * 3.0))
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(w::text(ui, "Alongside").variant("strong"))
        .child(w::text(ui, "").text({
            let g = grove.clone();
            move || g.stage_url.get()
        }))
        .child(
            w::text(
                ui,
                "The page itself is the computer-use browser. This rail drives that host: address, home, shield, bookmarks, and desktop or mobile.",
            )
            .variant("muted"),
        )
        .child(w::text(ui, "").variant("muted").text(move || g.stage_status.get()))
}

