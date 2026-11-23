//! Optional messenger step. Tokens stay in the secret field and are never written into the setup draft.

use std::cell::RefCell;
use std::rc::Rc;

use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, TextField, Ui};

use super::chrome;

#[derive(Clone)]
pub struct Messenger {
    pub id: String,
    pub name: String,
    pub status: String,
    pub enabled: Signal<bool>,
    /// Secret field, created when the row is shown. `value()` is empty for secrets.
    pub token_field: Rc<RefCell<Option<TextField>>>,
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
            token_field: Rc::new(RefCell::new(None)),
        })
        .collect()
}

/// Takes the token out of the secret field. Empty when the row was never opened.
pub fn take_token(m: &Messenger) -> String {
    let Some(field) = m.token_field.borrow().clone() else {
        return String::new();
    };
    if field.is_empty() {
        return String::new();
    }
    field.take_secret().expose().trim().to_string()
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
    let ui_rows = ui.clone();
    w::for_each(&list, rows, |m| m.id.clone(), move |m| row(&ui_rows, m));
    chrome::stack(ui, vec![intro, list])
}

fn row(ui: &Ui, m: Messenger) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let enabled = m.enabled.clone();
    let check = w::check(ui, format!("{}  {}", m.name, status_text(&m.status, "")), enabled.clone());
    let slot = m.token_field.clone();
    let field = {
        let mut held = slot.borrow_mut();
        if held.is_none() {
            *held = Some(
                w::secret_input(ui, "Bot token (optional now)").placeholder("Bot token (optional now)"),
            );
        }
        held.clone().unwrap()
    };
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
