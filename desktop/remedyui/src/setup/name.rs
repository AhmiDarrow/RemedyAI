//! "What should Remedy call you?" Skip is a new file under the Remedy home.
//! It does not erase anything else in that folder.

use std::path::PathBuf;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::chrome;
use super::model::reject_empty_name;
use crate::logo;
use crate::runtime::{self, Client};

pub fn skip_path() -> PathBuf {
    runtime::remedy_home()
        .join("desktop-ui")
        .join("user-name-skipped")
}

pub fn is_skipped() -> bool {
    skip_path().is_file()
}

pub fn write_skip() {
    let path = skip_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, "1\n");
}

pub fn clear_skip() {
    let _ = std::fs::remove_file(skip_path());
}

#[derive(Clone)]
pub struct NamePrompt {
    pub open: Signal<bool>,
    pub name: Signal<String>,
    pub error: Signal<String>,
    pub saved: Signal<bool>,
    pub client: Signal<Option<Client>>,
}

impl NamePrompt {
    pub fn new(client: Option<Client>) -> Self {
        Self {
            open: Signal::new(false),
            name: Signal::new(String::new()),
            error: Signal::new(String::new()),
            saved: Signal::new(false),
            client: Signal::new(client),
        }
    }

    /// Opens the prompt. The gate calls this only when the name is still empty.
    pub fn ask(&self) {
        self.error.set(String::new());
        self.saved.set(false);
        self.open.set(true);
    }

    pub fn save(&self) {
        let name = self.name.get();
        if reject_empty_name(&name) {
            return;
        }
        let Some(client) = self.client.get() else {
            self.error
                .set("Could not reach the local server to save your name.".into());
            return;
        };
        let saved = self.saved.clone();
        let open = self.open.clone();
        let error = self.error.clone();
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.error
                .set("Could not reach the local server to save your name.".into());
            return;
        };
        let trimmed = name.trim().to_string();
        std::thread::Builder::new()
            .name("remedy-name".into())
            .spawn(move || {
                let body = serde_json::json!({ "user_name": trimmed });
                let out = super::api::put_settings(&client, &body);
                let _ = waker.post(move || match out {
                    Ok(_) => {
                        clear_skip();
                        saved.set(true);
                        open.set(false);
                    }
                    Err(e) => error.set(e),
                });
            })
            .ok();
    }

    pub fn later(&self) {
        write_skip();
        self.open.set(false);
    }
}

pub fn name_layer(ui: &Ui, prompt: NamePrompt) -> El {
    let open = prompt.open.clone();
    let ui_body = ui.clone();
    chrome::when(ui, move || open.get(), move || card(&ui_body, prompt.clone()))
}

/// Overlay with no fallback, so a closed prompt does not cover the studio.
pub fn name_overlay(ui: &Ui, prompt: NamePrompt) -> El {
    let open = prompt.open.clone();
    let ui_body = ui.clone();
    show(ui, move || open.get(), move || card(&ui_body, prompt.clone())).el()
}

fn card(ui: &Ui, prompt: NamePrompt) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let name = prompt.name.clone();
    let field = w::text_input(ui, "What should Remedy call you?").placeholder("Your name");
    let f2 = field.clone();
    let name2 = name.clone();
    let field = field.on_change(move || name2.set(f2.value()));
    let prompt_enter = prompt.clone();
    let field = field.on_submit(move || prompt_enter.save());
    let save = chrome::primary(ui, "Save", {
        let prompt = prompt.clone();
        move || prompt.save()
    });
    let name3 = name.clone();
    let save = save.disabled(move || reject_empty_name(&name3.get()));
    let body = w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| l.width = Length::Px(360.0))
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 1.5)
                .child(logo::mark(ui, 36.0))
                .child(
                    w::column(ui)
                        .gap(unit * 0.4)
                        .child(w::text(ui, "What should Remedy call you?").variant("strong"))
                        .child(
                            w::text(ui, "Used in chat and memory — change anytime in Settings.")
                                .variant("muted"),
                        ),
                ),
        )
        .child(field.el())
        .child(chrome::error_text(ui, prompt.error.clone()))
        .child(chrome::actions(
            ui,
            vec![
                chrome::quiet(ui, "Later", {
                    let prompt = prompt.clone();
                    move || prompt.later()
                }),
                save,
            ],
        ));
    w::column(ui)
        .align(Align::Center)
        .justify(Justify::Center)
        .style(StylePatch::new().background(colors.bg.mix_oklab(colors.text, 0.35)))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .semantics(|| Semantics::new(Role::Dialog).label("What should Remedy call you?").modal(true))
        .child(
            w::column(ui)
                .padding(Insets::all(unit * 3.0))
                .style(
                    StylePatch::new()
                        .background(colors.card)
                        .radius(ui.theme().radii.large)
                        .border(line)
                        .border_width(1.0),
                )
                .child(body),
        )
}
