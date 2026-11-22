//! Optional messenger step. Tokens stay in signals and are never written into the setup draft.

use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;

#[derive(Clone)]
pub struct Messenger {
    pub id: String,
    pub name: String,
    pub status: String,
    pub enabled: Signal<bool>,
    pub token: Signal<String>,
}

pub fn fallback_list() -> Vec<Messenger> {
    ["telegram", "discord", "slack", "mattermost"]
        .into_iter()
        .map(|id| Messenger {
            id: id.into(),
            name: match id {
                "telegram" => "Telegram",
                "discord" => "Discord",
                "slack" => "Slack",
                _ => "Mattermost",
            }
            .into(),
            status: "ready".into(),
            enabled: Signal::new(false),
            token: Signal::new(String::new()),
        })
        .collect()
}

pub fn status_text(status: &str, reason: &str) -> String {
    match status {
        "needs_setup" => {
            if reason.trim().is_empty() {
                "needs setup".into()
            } else {
                reason.trim().into()
            }
        }
        "planned" => "planned".into(),
        _ => "ready".into(),
    }
}

pub fn view(ui: &Ui, rows: Signal<Vec<Messenger>>) -> El {
    let unit = ui.theme().spacing.unit;
    let intro = w::column(ui)
        .gap(unit * 0.5)
        .child(w::text(ui, "Messengers (optional)").variant("strong"))
        .child(
            w::text(
                ui,
                "Talk to Remedy from Telegram, Discord, and more. Chats show up in the session list so you can continue in the desktop app. Skip if you only want the desktop for now.",
            )
            .variant("muted"),
        );
    let list = w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    w::for_each(&list, rows, |m| m.id.clone(), move |m| row(ui, m));
    chrome::stack(ui, vec![intro, list])
}

fn row(ui: &Ui, m: Messenger) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let enabled = m.enabled.clone();
    let token = m.token.clone();
    let check = w::check(ui, format!("{}  {}", m.name, m.status), enabled.clone());
    let field = w::secret_input(ui, "Bot token (optional now)")
        .placeholder("Bot token (optional now)");
    let f2 = field.clone();
    let token2 = token.clone();
    let field = field.on_change(move || {
        let shown = f2.value();
        if !shown.is_empty() {
            token2.set(shown);
        }
    });
    w::column(ui)
        .gap(unit)
        .padding(remedy_ui::core::Insets::all(unit * 1.5))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .border(line)
                .border_width(1.0)
                .radius(ui.theme().radii.medium),
        )
        .layout(|l| l.width = Length::Percent(1.0))
        .child(check)
        .child(chrome::when(
            ui,
            move || enabled.get(),
            move || field.el(),
        ))
}
