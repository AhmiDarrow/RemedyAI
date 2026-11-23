//! Confirms, the concurrent-turn dialog, and the image lightbox.
//!
//! Each opens once per generation so a binding does not stack a new modal every frame.

use super::logic::concurrent_turn_message;
use super::{continue_pending, delete_skill, project_confirm_body, remove_project, Confirm};
use crate::messages;
use crate::studio::Studio;
use remedy_ui::image::decode;
use remedy_ui::layout::Length;
use remedy_ui::paint::Image;
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use std::cell::RefCell;
use std::rc::Rc;

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
    let slot: Rc<RefCell<Option<w::Modal>>> = Rc::new(RefCell::new(None));
    let close_slot = slot.clone();
    let st = studio.clone();
    let u = ui.clone();
    let content = w::column(ui)
        .gap(ui.theme().spacing.unit * 2.0)
        .child(lightbox_body(ui, &src))
        .child(w::button(
            ui,
            move || messages().text(&u, "lightbox-close"),
            move || {
                st.depth.lightbox.set(None);
                if let Some(m) = close_slot.borrow().as_ref() {
                    m.close();
                }
            },
        ).variant("primary"));
    let modal = w::modal(ui, messages().text(ui, "lightbox-close"), content, true);
    *slot.borrow_mut() = Some(modal);
}

fn lightbox_body(ui: &Ui, src: &str) -> El {
    if let Some(image) = decode_src(src) {
        return w::image(ui, image, src).layout(|l| {
            l.max_width = Length::Px(960.0);
            l.max_height = Length::Px(640.0);
        });
    }
    w::text(ui, src)
}

fn decode_src(src: &str) -> Option<Image> {
    let bytes = if let Some(rest) = src.strip_prefix("data:") {
        let comma = rest.find(',')?;
        let meta = &rest[..comma];
        let data = &rest[comma + 1..];
        if meta.to_ascii_lowercase().contains(";base64") {
            super::api::b64_decode(data)?
        } else {
            data.as_bytes().to_vec()
        }
    } else if let Some(path) = super::markdown::file_path(src) {
        let meta = std::fs::metadata(&path).ok()?;
        if meta.len() as usize > super::api::IMAGE_CAP {
            return None;
        }
        std::fs::read(&path).ok()?
    } else {
        return None;
    };
    if bytes.len() > super::api::IMAGE_CAP {
        return None;
    }
    let decoded = decode(&bytes, Some((960, 960))).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}
