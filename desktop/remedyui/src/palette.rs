//! Command palette. The command list matches `paletteCommands` in App.tsx.

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Length};
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::chrome::{Chrome, Overlay};
use crate::help_view;
use crate::messages;
use crate::studio::Studio;

#[derive(Clone, Debug)]
pub struct Command {
    pub id: String,
    pub label: String,
    pub description: String,
    pub category: String,
}

pub fn commands(studio: &Studio, chrome: &Chrome) -> Vec<Command> {
    let mut items = vec![
        cmd("new", "New Session", "Start a new chat session", "session"),
        cmd("time-travel", "Time Travel", "Timeline: restore chat & files to an earlier step", "session"),
        cmd("export", "Export Session", "Download active session as .txt", "session"),
        cmd("import", "Import Session", "Import a session from .txt / .md", "session"),
        cmd("palette", "Command palette", "Open this palette", "general"),
        cmd("plan", "Cycle Chat / Plan / Build", "Pin conversation, planning, or building", "general"),
        cmd("memory", "Memory", "Toggle memory panel", "panel"),
        cmd("skills", "Skills", "Toggle skills panel", "panel"),
        cmd("settings", "Settings", "Open Settings", "panel"),
        cmd("help", "Help", "Searchable offline wiki (F1 / Ctrl+/)", "general"),
        cmd("diagnostics", "Diagnostics", "Server, local host, hardware, hive", "panel"),
        cmd("help-troubleshoot", "Troubleshooting", "Open Help → Troubleshooting", "general"),
        cmd("help-commands", "Slash commands reference", "Open Help → Commands", "general"),
    ];
    for s in studio.sessions.get() {
        items.push(Command {
            id: format!("session-{}", s.id),
            label: if s.title.is_empty() { "Untitled".into() } else { s.title },
            description: format!("{} messages", s.message_count),
            category: "session".into(),
        });
    }
    for a in chrome.agents.get() {
        items.push(Command {
            id: format!("agent-{}", a.name),
            label: format!("@{}", a.name),
            description: a.description.clone(),
            category: "agent".into(),
        });
    }
    let provider = studio.provider.get();
    for m in studio.models.get() {
        items.push(Command {
            id: format!("model-{m}"),
            label: m.clone(),
            description: provider.clone(),
            category: "model".into(),
        });
    }
    items
}

fn cmd(id: &str, label: &str, description: &str, category: &str) -> Command {
    Command {
        id: id.into(),
        label: label.into(),
        description: description.into(),
        category: category.into(),
    }
}

pub fn filter(items: &[Command], query: &str) -> Vec<Command> {
    let q = query.trim().to_lowercase();
    let matched: Vec<Command> = if q.is_empty() {
        items.to_vec()
    } else {
        items
            .iter()
            .filter(|c| {
                c.label.to_lowercase().contains(&q)
                    || c.description.to_lowercase().contains(&q)
                    || c.category.to_lowercase().contains(&q)
                    || c.id.to_lowercase().contains(&q)
            })
            .cloned()
            .collect()
    };
    matched.into_iter().take(40).collect()
}

pub fn run(id: &str, studio: &Studio, chrome: Chrome, ui: &Ui) {
    let open_help = |article: &str| {
        chrome.open_help(studio, article);
    };
    if let Some(rest) = id.strip_prefix("session-") {
        studio.select(rest);
        chrome.close_overlay();
        return;
    }
    if let Some(rest) = id.strip_prefix("model-") {
        studio.model.set(rest.to_string());
        chrome.close_overlay();
        chrome.toast(ui, rest.to_string());
        return;
    }
    if id.starts_with("agent-") {
        chrome.close_overlay();
        return;
    }
    match id {
        "new" => {
            studio.add();
            chrome.close_overlay();
        }
        "time-travel" => chrome.open_time_travel(studio),
        "export" => {
            chrome.toast(ui, messages().text(ui, "toast-export"));
            chrome.close_overlay();
        }
        "import" => {
            chrome.toast(ui, messages().text(ui, "toast-import"));
            chrome.close_overlay();
        }
        "palette" => {
            chrome.palette_query.set(String::new());
            chrome.palette_idx.set(0);
            chrome.overlay.set(Overlay::Palette);
        }
        "plan" => {
            studio.cycle_mode();
            chrome.close_overlay();
        }
        "memory" => chrome.toggle(Overlay::Memory),
        "skills" => chrome.toggle(Overlay::Skills),
        "settings" => chrome.open_settings(),
        "help" => open_help(help_view::default_id()),
        "diagnostics" => chrome.open_diagnostics(studio),
        "help-troubleshoot" => open_help("09-troubleshooting"),
        "help-commands" => open_help("11-reference-commands"),
        _ => chrome.close_overlay(),
    }
}

pub fn panel(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let mut field = w::text_input(ui, messages().text(ui, "palette-search"));
    field = field.placeholder(messages().text(ui, "palette-placeholder"));
    let qf = field.clone();
    let c = chrome;
    field = field.on_change(move || {
        c.palette_query.set(qf.value());
        c.palette_idx.set(0);
    });

    let list = ui.el("palette-list").column();
    let host = list.clone();
    let c = chrome;
    let s = studio.clone();
    let u = ui.clone();
    let list = list.bind(move || {
        let items = filter(&commands(&s, &c), &c.palette_query.get());
        let mut idx = c.palette_idx.get();
        if items.is_empty() {
            idx = 0;
        } else if idx >= items.len() {
            idx = items.len() - 1;
            c.palette_idx.set_if_changed(idx);
        }
        host.clear();
        if items.is_empty() {
            let _ = host.clone().child(w::text(&u, messages().text(&u, "palette-empty")).variant("muted"));
            return;
        }
        for (i, item) in items.iter().enumerate() {
            let on = i == idx;
            let id = item.id.clone();
            let cc = c;
            let ss = s.clone();
            let uu = u.clone();
            let _ = host.clone().child(
                w::row(&u)
                    .align(Align::Center)
                    .gap(unit * 2.0)
                    .padding(Insets {
                        top: unit * 1.5,
                        bottom: unit * 1.5,
                        start: unit * 3.0,
                        end: unit * 3.0,
                    })
                    .style(
                        StylePatch::new()
                            .background(if on { colors.accent.with_alpha(0.14) } else { colors.panel })
                            .border_side(
                                remedy_ui::style::BorderEdge::Start,
                                remedy_ui::style::BorderSide {
                                    width: if on { 3.0 } else { 0.0 },
                                    color: colors.accent,
                                    style: remedy_ui::style::BorderStyle::Solid,
                                },
                            ),
                    )
                    .on_click(move || run(&id, &ss, cc, &uu))
                    .semantics(
                        Semantics::new(Role::Button)
                            .label(item.label.clone())
                            .action(remedy_ui::a11y::Action::Click),
                    )
                    .child(ui_chip(&u, &item.category, colors.muted))
                    .child(
                        w::column(&u)
                            .layout(|l| l.grow = 1.0)
                            .child(w::text(&u, item.label.clone()))
                            .child(w::text(&u, item.description.clone()).variant("muted")),
                    ),
            );
        }
    });

    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.max_width = Length::Px(560.0);
            l.max_height = Length::Percent(0.62);
        })
        .style(crate::surface_of(ui))
        .semantics(Semantics::new(Role::Dialog).label(messages().text(ui, "palette-title")).modal(true))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 2.0)
                .padding(Insets::all(unit * 3.0))
                .child(w::text(ui, "⌘").style(StylePatch::new().foreground(colors.accent)))
                .child({
                    let input = field.el();
                    input.focus();
                    input.layout(|l| l.grow = 1.0)
                })
                .child(
                    w::text(ui, "Esc")
                        .variant("muted")
                        .nowrap()
                        .layout(|l| l.shrink = 0.0),
                ),
        )
        .child(w::divider(ui, false))
        .child(w::scroll(ui).child(list).layout(|l| {
            l.grow = 1.0;
            l.min_height = Length::Px(0.0);
            l.max_height = Length::Px(360.0);
        }))
        .child(w::divider(ui, false))
        .child(
            w::text(ui, "")
                .variant("muted")
                .text({
                    let u = ui.clone();
                    move || messages().text(&u, "palette-footer")
                })
                .padding(Insets::all(unit * 2.0)),
        )
}

fn ui_chip(ui: &Ui, category: &str, mark: remedy_ui::core::Color) -> El {
    w::text(ui, category).style(
        StylePatch::new()
            .foreground(mark)
            .padding(Insets::all(ui.theme().spacing.unit)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chrome::Chrome;
    use crate::studio::Studio;

    #[test]
    fn palette_lists_every_current_command() {
        let studio = Studio::preview();
        let chrome = Chrome::preview(Overlay::Palette);
        let items = commands(&studio, &chrome);
        let ids: Vec<&str> = items.iter().map(|c| c.id.as_str()).collect();
        for id in [
            "new",
            "time-travel",
            "export",
            "import",
            "palette",
            "plan",
            "memory",
            "skills",
            "settings",
            "help",
            "diagnostics",
            "help-troubleshoot",
            "help-commands",
        ] {
            assert!(ids.contains(&id), "missing {id}");
        }
        assert!(ids.iter().any(|id| id.starts_with("session-")));
        assert!(ids.iter().any(|id| *id == "agent-remedy"));
        assert!(ids.iter().any(|id| *id == "model-remedy"));
        let found = filter(&items, "slash");
        assert!(found.iter().any(|c| c.id == "help-commands"));
        assert!(filter(&items, "zzzz-no-such").is_empty());
    }
}
