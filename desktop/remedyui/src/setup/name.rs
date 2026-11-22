//! "What should Remedy call you?" Skip is a new file under the Remedy home.
//! It does not erase anything else in that folder.

use std::path::PathBuf;

use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use super::chrome;
use super::model::reject_empty_name;
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
    pub client: Option<Client>,
}

impl NamePrompt {
    pub fn new(client: Option<Client>) -> Self {
        Self {
            open: Signal::new(true),
            name: Signal::new(String::new()),
            error: Signal::new(String::new()),
            saved: Signal::new(false),
            client,
        }
    }

    pub fn save(&self) {
        let name = self.name.get();
        if reject_empty_name(&name) {
            return;
        }
        let Some(client) = self.client.clone() else {
            self.error
                .set("Could not reach the local server to save your name.".into());
            return;
        };
        let body = serde_json::json!({ "user_name": name.trim() });
        match super::api::put_settings(&client, &body) {
            Ok(_) => {
                clear_skip();
                self.saved.set(true);
                self.open.set(false);
            }
            Err(e) => self.error.set(e),
        }
    }

    pub fn later(&self) {
        write_skip();
        self.open.set(false);
    }
}

pub fn name_layer(ui: &Ui, prompt: NamePrompt) -> El {
    let open = prompt.open.clone();
    chrome::when(
        ui,
        move || open.get(),
        move || card(ui, prompt.clone()),
    )
}

fn card(ui: &Ui, prompt: NamePrompt) -> El {
    let unit = ui.theme().spacing.unit;
    let name = prompt.name.clone();
    let field = w::text_input(ui, "Your name").placeholder("Your name");
    let f2 = field.clone();
    let name2 = name.clone();
    let field = field.on_change(move || name2.set(f2.value()));
    let save = chrome::primary(ui, "Save", {
        let prompt = prompt.clone();
        move || prompt.save()
    });
    let save2 = save.clone();
    let name3 = name.clone();
    save.bind(move || {
        let _ = save2.disabled(reject_empty_name(&name3.get()));
    });
    let inner = w::column(ui)
        .gap(unit * 1.5)
        .layout(|l| l.width = Length::Px(420.0))
        .child(w::text(ui, "What should Remedy call you?").variant("heading"))
        .child(w::text(ui, "Used in chat and memory — change anytime in Settings.").variant("muted"))
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
    chrome::center(ui, inner, 460.0)
}
