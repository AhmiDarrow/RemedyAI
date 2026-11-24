//! Confirms, the concurrent-turn dialog, and the image markup lightbox.
//!
//! Each opens once per generation so a binding does not stack a new modal every frame.

use super::logic::concurrent_turn_message;
use super::markup;
use super::{archive_unused_skills, continue_pending, delete_skill, project_confirm_body, remove_project, Confirm};
use crate::messages;
use crate::studio::Studio;
use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

/// A zero-size hook the shell keeps alive.
pub fn mount(ui: &Ui, studio: Studio) -> El {
    let seen_confirm = Signal::new(0u64);
    let seen_turns = Signal::new(0u64);
    let seen_image = Signal::new(0u64);
    let u = ui.clone();
    let st = studio.clone();
    ui.el("depth-hooks")
        .layout(|l| {
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
        })
        .bind(move || {
            let confirm_ticket = st.depth.confirm_gen.get();
            if confirm_ticket != seen_confirm.get() {
                seen_confirm.set(confirm_ticket);
                if let Some(confirm) = st.depth.confirm.get() {
                    open_confirm(&u, &st, confirm);
                }
            }
            let turn_ticket = st.depth.concurrent_gen.get();
            if turn_ticket != seen_turns.get() {
                seen_turns.set(turn_ticket);
                if st.depth.concurrent_open.get() {
                    open_turns(&u, &st);
                }
            }
            let image_ticket = st.depth.lightbox_gen.get();
            if image_ticket != seen_image.get() {
                seen_image.set(image_ticket);
                if let Some(src) = st.depth.lightbox.get() {
                    open_lightbox(&u, &st, src);
                }
            }
        })
}

fn open_confirm(ui: &Ui, studio: &Studio, confirm: Confirm) {
    let (title, body, danger) = match &confirm {
        Confirm::RemoveProject(path) => (
            messages().text(ui, "remove-project"),
            project_confirm_body(studio, path),
            messages().text(ui, "remove-project"),
        ),
        Confirm::DeleteSkill(name) => (
            messages().text(ui, "confirm-delete-skill"),
            format!("{} {name}", messages().text(ui, "confirm-delete-skill")),
            messages().text(ui, "skill-delete"),
        ),
        Confirm::ArchiveUnusedSkills => (
            messages().text(ui, "skills-archive-unused"),
            messages().text(ui, "skills-archive-unused-body"),
            messages().text(ui, "skills-archive-unused"),
        ),
    };
    let st_yes = studio.clone();
    let st_no = studio.clone();
    let confirm_yes = confirm.clone();
    w::dialog(
        ui,
        title,
        body,
        vec![
            w::DialogButton::new(messages().text(ui, "cancel"), "quiet", move || {
                st_no.depth.confirm.set(None);
            }),
            w::DialogButton::new(danger, "danger_solid", move || match &confirm_yes {
                Confirm::RemoveProject(path) => remove_project(&st_yes, path),
                Confirm::DeleteSkill(name) => delete_skill(&st_yes, name),
                Confirm::ArchiveUnusedSkills => archive_unused_skills(&st_yes),
            }),
        ],
    );
}

fn open_turns(ui: &Ui, studio: &Studio) {
    let running = studio.depth.running_count();
    let message = concurrent_turn_message(running);
    let st_yes = studio.clone();
    let st_no = studio.clone();
    w::dialog(
        ui,
        messages().text(ui, "concurrent-title"),
        message,
        vec![
            w::DialogButton::new(messages().text(ui, "cancel"), "quiet", move || {
                st_no.depth.pending.set(None);
                st_no.depth.concurrent_open.set(false);
            }),
            w::DialogButton::new(messages().text(ui, "concurrent-continue"), "primary", move || {
                continue_pending(&st_yes);
            }),
        ],
    );
}

fn open_lightbox(ui: &Ui, studio: &Studio, src: String) {
    markup::open(ui, studio, src);
}
