//! Composer: slash commands, @-mentions, attachments, history, voice, and the send menu.

use super::api;
use super::logic::{self, at_query, filter_commands, slash_query};
use super::{add_paths, pick_attachments, submit, toggle_mic, Attachment, SubmitKind};
use crate::messages;
use crate::studio::Studio;
use crate::tool_button;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::host::DropEffect;
use remedy_ui::input::{Buttons, Key, PointerPhase};
use remedy_ui::layout::{Align, Length};
use remedy_ui::net::Request;
use remedy_ui::reactive::{waker, Signal};
use remedy_ui::style::StylePatch;
use remedy_ui::view::Drag;
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

#[derive(Clone)]
struct Offer {
    label: String,
    detail: String,
    insert: String,
    /// Byte index where the token starts. `None` replaces from the start (a slash command).
    at: Option<usize>,
}

/// The composer under the feed: field, files, suggestions, queue, and send.
pub fn composer(ui: &Ui, studio: Studio) -> El {
    let unit = ui.theme().spacing.unit;
    let placeholder = messages().text(ui, "composer-placeholder");
    let field = w::text_area(ui, placeholder.clone())
        .placeholder(placeholder)
        .submit_on_enter(true);
    let files: Signal<Vec<String>> = Signal::new(Vec::new());
    let caret_at = Signal::new(0usize);
    let picked = Signal::new(0usize);
    let hist_at = Signal::new(-1i32);
    let hist_saved = Signal::new(String::new());
    let editing = Signal::new(false);
    let file_q = Signal::new(String::new());
    let menu_on = Signal::new(true);
    let offers: Signal<Vec<Offer>> = Signal::new(Vec::new());

    let f_sub = field.clone();
    let menu_sub = menu_on.clone();
    let hist_sub = hist_at.clone();
    let st_sub = studio.clone();
    let files_sub = files.clone();
    let caret_sub = caret_at.clone();
    let picked_sub = picked.clone();
    let edit_sub = editing.clone();
    let field = field.on_submit_with(move |mods| {
        let text = f_sub.value();
        let offers = current_offers(&st_sub, caret_sub.get(), &files_sub.get(), menu_sub.get());
        if !offers.is_empty() {
            let i = picked_sub.get().min(offers.len() - 1);
            apply_offer(&f_sub, &st_sub, &offers[i]);
            picked_sub.set(0);
            return;
        }
        if edit_sub.get() {
            edit_sub.set(false);
            let attachments = st_sub.depth.attachments.get();
            f_sub.set_value("");
            st_sub.depth.composer_value.set(String::new());
            st_sub.depth.attachments.set(Vec::new());
            if !text.trim().is_empty() || !attachments.is_empty() {
                st_sub.depth.push_queued(text, "after", attachments);
            }
            return;
        }
        let attachments = st_sub.depth.attachments.get();
        let kind = if mods.ctrl || mods.meta {
            SubmitKind::Interrupt
        } else if mods.alt {
            SubmitKind::After
        } else if st_sub.streaming.get() {
            SubmitKind::Steer
        } else {
            SubmitKind::Send
        };
        f_sub.set_value("");
        st_sub.depth.composer_value.set(String::new());
        hist_sub.set(-1);
        menu_sub.set(true);
        submit(&st_sub, text, attachments, kind, true);
    });

    let f_ch = field.clone();
    let st_ch = studio.clone();
    let files_ch = files.clone();
    let caret_ch = caret_at.clone();
    let picked_ch = picked.clone();
    let q_ch = file_q.clone();
    let menu_ch = menu_on.clone();
    let hist_ch = hist_at.clone();
    let field = field.on_change(move || {
        let text = f_ch.value();
        caret_ch.set(f_ch.caret());
        st_ch.depth.composer_value.set(text.clone());
        refresh_files(&st_ch, &text, f_ch.caret(), &files_ch, &q_ch);
        picked_ch.set(0);
        hist_ch.set(-1);
        menu_ch.set(true);
    });

    let f_key = field.clone();
    let st_key = studio.clone();
    let files_key = files.clone();
    let caret_key = caret_at.clone();
    let picked_key = picked.clone();
    let hist_key = hist_at.clone();
    let saved_key = hist_saved.clone();
    let menu_key = menu_on.clone();
    field.intercept_keys(move |e| {
        if !e.pressed {
            return false;
        }
        caret_key.set(f_key.caret());
        if e.key == Key::Tab
            && e.modifiers.shift
            && !e.modifiers.ctrl
            && !e.modifiers.alt
            && !e.modifiers.meta
        {
            st_key.cycle_mode();
            return true;
        }
        let offers = current_offers(&st_key, f_key.caret(), &files_key.get(), menu_key.get());
        if e.key == Key::Escape && !offers.is_empty() {
            menu_key.set(false);
            return true;
        }
        if !offers.is_empty() && matches!(e.key, Key::ArrowUp | Key::ArrowDown) {
            let n = offers.len();
            let i = picked_key.get();
            let next = if e.key == Key::ArrowDown {
                (i + 1) % n
            } else if i == 0 {
                n - 1
            } else {
                i - 1
            };
            picked_key.set(next);
            return true;
        }
        let text = f_key.value();
        let one_line = !text.contains('\n');
        if one_line && f_key.caret() == 0 && matches!(e.key, Key::ArrowUp | Key::ArrowDown) {
            return walk_history(&f_key, &st_key, &hist_key, &saved_key, e.key == Key::ArrowUp);
        }
        false
    });

    let f_draft = field.clone();
    let st_draft = studio.clone();
    let seen = Signal::new(0u64);
    let _ = field.el().bind(move || {
        let n = st_draft.depth.draft_nonce.get();
        if n != seen.get() {
            seen.set(n);
            let text = st_draft.depth.draft_text.get();
            f_draft.set_value(&text);
            st_draft.depth.composer_value.set(text);
        }
    });

    let shell = field_row(ui, &studio, &field, unit);
    let st_over = studio.clone();
    let shell = shell
        .on_drop(
            |drag, _| {
                if drag.data.files.is_empty() {
                    DropEffect::None
                } else {
                    DropEffect::Copy
                }
            },
            {
                let st = studio.clone();
                move |drag, _, _| add_paths(&st, &drag.data.files)
            },
        )
        .on_drag_hover(move |drag: Option<&Drag>| {
            let over = drag.is_some_and(|d| !d.data.files.is_empty());
            st_over.depth.drag_over.set(over);
        });

    let offer_sync = {
        let st = studio.clone();
        let files = files.clone();
        let caret = caret_at.clone();
        let menu = menu_on.clone();
        let out = offers.clone();
        ui.el("offer-sync")
            .layout(|l| {
                l.width = Length::Px(0.0);
                l.height = Length::Px(0.0);
            })
            .bind(move || {
                out.set(current_offers(
                    &st,
                    caret.get(),
                    &files.get(),
                    menu.get(),
                ));
            })
    };
    let suggests = suggestion_list(ui, studio.clone(), offers, picked, field.clone());
    let queue = queue_list(ui, studio.clone(), field.clone(), editing.clone());
    let attached = attachment_row(ui, studio.clone());
    let vision = vision_line(ui, studio.clone());
    let notice = notice_line(ui, studio.clone());
    let drop_hint = {
        let st = studio.clone();
        let u = ui.clone();
        show(ui, move || st.depth.drag_over.get(), move || {
            let label = u.clone();
            w::text(&u, "").variant("muted").text(move || {
                messages().text(&label, "composer-drop")
            })
        })
        .el()
    };

    w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: unit,
            end: unit * 4.0,
            bottom: unit * 2.0,
            start: unit * 4.0,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(offer_sync)
        .child(notice)
        .child(queue)
        .child(suggests)
        .child(attached)
        .child(vision)
        .child(drop_hint)
        .child(shell)
        .child(crate::say(ui, "composer-hint", "muted"))
}

fn field_row(ui: &Ui, studio: &Studio, field: &remedy_ui::TextField, unit: f32) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors
        .line
        .unwrap_or(colors.accent.mix_oklab(colors.bg, 0.65));
    let attach = {
        let u = ui.clone();
        let st = studio.clone();
        tool_button(
            ui,
            w::Icon::File,
            move || messages().text(&u, "composer-attach"),
            move || pick_attachments(&st),
        )
        .style(
            StylePatch::new()
                .background(colors.card)
                .radius(18.0)
                .foreground(colors.text),
        )
    };
    let mic = mic_button(ui, studio);
    let send = send_button(ui, studio, field);
    w::context_menu(
        &w::row(ui)
            .align(Align::Center)
            .gap(unit)
            .padding(Insets {
                top: unit * 1.5,
                end: unit * 1.5,
                bottom: unit * 1.5,
                start: unit * 1.5,
            })
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_height = Length::Px(52.0);
                l.shrink = 0.0;
            })
            .style(
                StylePatch::new()
                    .background(colors.panel)
                    .radius(ui.theme().radii.large)
                    .border(line)
                    .border_width(1.0),
            )
            .child(attach)
            .child(mic)
            .child(field.el().layout(|l| {
                l.grow = 1.0;
                l.shrink = 1.0;
                l.min_width = Length::Px(0.0);
                l.min_height = Length::Px(36.0);
            }))
            .child(send),
        {
            let st = studio.clone();
            let f = field.clone();
            let u = ui.clone();
            move || composer_menu(&u, &st, &f)
        },
    )
}

fn mic_button(ui: &Ui, studio: &Studio) -> El {
    let u = ui.clone();
    let st = studio.clone();
    let label = {
        let u = u.clone();
        let st = st.clone();
        move || {
            let id = if st.depth.mic_on.get() {
                "composer-mic-stop"
            } else {
                "composer-mic"
            };
            messages().text(&u, id)
        }
    };
    w::button(ui, label, move || toggle_mic(&st))
        .variant("quiet")
        .semantics({
            let u = u.clone();
            let st = studio.clone();
            move || {
                let id = if st.depth.mic_on.get() {
                    "composer-mic-stop"
                } else {
                    "composer-mic"
                };
                Semantics::new(Role::Button)
                    .label(messages().text(&u, id))
                    .action(Action::Click)
            }
        })
}

fn send_button(ui: &Ui, studio: &Studio, field: &remedy_ui::TextField) -> El {
    let streaming = {
        let s = studio.clone();
        move || s.streaming.get()
    };
    let stop = {
        let s = studio.clone();
        let u = ui.clone();
        move || {
            let s = s.clone();
            tool_button(
                &u,
                w::Icon::Close,
                {
                    let u = u.clone();
                    move || messages().text(&u, "composer-stop")
                },
                move || s.stop(),
            )
        }
    };
    let send = {
        let s = studio.clone();
        let f = field.clone();
        let u = ui.clone();
        move || {
            tool_button(
                &u,
                w::Icon::Send,
                {
                    let u = u.clone();
                    move || messages().text(&u, "composer-send")
                },
                {
                    let s = s.clone();
                    let f = f.clone();
                    move || {
                        let text = f.value();
                        let files = s.depth.attachments.get();
                        if text.trim().is_empty() && files.is_empty() {
                            return;
                        }
                        f.set_value("");
                        s.depth.composer_value.set(String::new());
                        let kind = if s.streaming.get() {
                            SubmitKind::Steer
                        } else {
                            SubmitKind::Send
                        };
                        submit(&s, text, files, kind, true);
                    }
                },
            )
        }
    };
    let btn = show(ui, streaming, stop).fallback(send).el();
    let st = studio.clone();
    let f = field.clone();
    btn.on_pointer(move |e| {
        if e.phase == PointerPhase::Down && e.buttons.contains(Buttons::SECONDARY) && st.streaming.get()
        {
            let text = f.value();
            let files = st.depth.attachments.get();
            f.set_value("");
            st.depth.composer_value.set(String::new());
            submit(&st, text, files, SubmitKind::Interrupt, false);
            return true;
        }
        false
    })
}

fn composer_menu(ui: &Ui, studio: &Studio, field: &remedy_ui::TextField) -> Vec<w::MenuItem> {
    let text = field.value();
    let files = studio.depth.attachments.get();
    let st = studio.clone();
    let f = field.clone();
    let send_as = move |kind: SubmitKind| {
        let st = st.clone();
        let f = f.clone();
        move || {
            let text = f.value();
            let files = st.depth.attachments.get();
            f.set_value("");
            st.depth.composer_value.set(String::new());
            submit(&st, text, files, kind, true);
        }
    };
    let _ = (text, files);
    vec![
        w::MenuItem::new(messages().text(ui, "composer-steer"), send_as(SubmitKind::Steer)),
        w::MenuItem::new(
            messages().text(ui, "composer-interrupt"),
            send_as(SubmitKind::Interrupt),
        ),
        w::MenuItem::new(messages().text(ui, "composer-queue"), send_as(SubmitKind::After)),
        w::MenuItem::new(messages().text(ui, "composer-clear-files"), {
            let st = studio.clone();
            move || st.depth.attachments.set(Vec::new())
        }),
    ]
}

fn suggestion_list(
    ui: &Ui,
    studio: Studio,
    offers: Signal<Vec<Offer>>,
    picked: Signal<usize>,
    field: remedy_ui::TextField,
) -> El {
    let when = offers.clone();
    let u = ui.clone();
    show(
        ui,
        move || !when.get().is_empty(),
        move || {
            let host = w::column(&u)
                .gap(2.0)
                .layout(|l| l.width = Length::Percent(1.0));
            let rows = offers.clone();
            let st = studio.clone();
            let f = field.clone();
            let picked_btn = picked.clone();
            w::for_each(&host, rows, |offer: &Offer| offer.insert.clone(), {
                let u = u.clone();
                let st = st.clone();
                let f = f.clone();
                let picked = picked_btn.clone();
                let rows = offers.clone();
                move |offer| {
                    let label = if offer.detail.is_empty() {
                        offer.label.clone()
                    } else {
                        format!("{}  {}", offer.label, offer.detail)
                    };
                    let insert = offer.insert.clone();
                    let chosen = offer.clone();
                    let mark = picked.clone();
                    let rows_mark = rows.clone();
                    w::button(&u, label, {
                        let st = st.clone();
                        let f = f.clone();
                        let picked = picked.clone();
                        move || {
                            apply_offer(&f, &st, &chosen);
                            picked.set(0);
                        }
                    })
                    .variant(move || {
                        let on = rows_mark
                            .get()
                            .get(mark.get())
                            .is_some_and(|o| o.insert == insert);
                        if on { "primary" } else { "quiet" }
                    })
                    .layout(|l| l.width = Length::Percent(1.0))
                }
            })
        },
    )
    .el()
}

fn current_offers(studio: &Studio, caret: usize, files: &[String], menu_on: bool) -> Vec<Offer> {
    if !menu_on {
        return Vec::new();
    }
    let text = studio.depth.composer_value.get();
    let caret = boundary(&text, caret);
    if let Some(q) = slash_query(&text, caret) {
        return filter_commands(&studio.depth.commands.get(), q.trim())
            .into_iter()
            .map(|c| Offer {
                insert: format!("{} ", c.name),
                label: c.name,
                detail: c.description,
                at: None,
            })
            .collect();
    }
    let Some(q) = at_query(&text, caret) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if !q.is_empty() {
        let ql = q.to_lowercase();
        for name in studio.depth.agents.get() {
            if out.len() >= 4 {
                break;
            }
            if name.to_lowercase().contains(&ql) {
                out.push(Offer {
                    insert: format!("@{name} "),
                    label: format!("@{name}"),
                    detail: String::new(),
                    at: Some(token_at(&text, caret)),
                });
            }
        }
    }
    if q.len() >= 2 {
        for name in files.iter().take(6) {
            out.push(Offer {
                insert: format!("@{name} "),
                label: format!("@{name}"),
                detail: String::new(),
                at: Some(token_at(&text, caret)),
            });
        }
    }
    out
}

fn refresh_files(
    studio: &Studio,
    text: &str,
    caret: usize,
    files: &Signal<Vec<String>>,
    last_q: &Signal<String>,
) {
    let caret = boundary(text, caret);
    let Some(q) = at_query(text, caret).filter(|q| q.len() >= 2) else {
        if !last_q.get().is_empty() {
            last_q.set(String::new());
            files.set(Vec::new());
        }
        return;
    };
    if q == last_q.get() {
        return;
    }
    last_q.set(q.clone());
    studio.depth.suggest_gen.update(|n| *n += 1);
    let gen = studio.depth.suggest_gen.get();
    let Some(client) = studio.runtime() else {
        files.set(Vec::new());
        return;
    };
    let path = format!("files/search?query={}", api::query(&q));
    let files = files.clone();
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            if studio.depth.suggest_gen.get() != gen {
                return;
            }
            let names = match out {
                Ok(v) => api::rows(&v, "files")
                    .into_iter()
                    .filter_map(|row| {
                        if let Some(s) = row.as_str() {
                            return Some(s.to_string());
                        }
                        let path = api::str_field(row, "path");
                        if !path.is_empty() {
                            return Some(path);
                        }
                        let name = api::str_field(row, "name");
                        if name.is_empty() { None } else { Some(name) }
                    })
                    .take(6)
                    .collect(),
                Err(_) => Vec::new(),
            };
            files.set(names);
        });
    });
}

fn apply_offer(field: &remedy_ui::TextField, studio: &Studio, offer: &Offer) {
    let text = field.value();
    let caret = boundary(&text, field.caret());
    let start = offer.at.unwrap_or(0).min(caret);
    let mut next = String::new();
    next.push_str(&text[..start]);
    next.push_str(&offer.insert);
    next.push_str(&text[caret..]);
    field.set_value(&next);
    studio.depth.composer_value.set(next);
}

fn walk_history(
    field: &remedy_ui::TextField,
    studio: &Studio,
    index: &Signal<i32>,
    saved: &Signal<String>,
    up: bool,
) -> bool {
    let history = studio.depth.prompt_history.get();
    if history.is_empty() {
        return false;
    }
    let cur = index.get();
    if up {
        if cur < 0 {
            saved.set(field.value());
        }
        let next = (cur + 1).min(history.len() as i32 - 1);
        index.set(next);
        if let Some(text) = history.get(next as usize) {
            field.set_value(text);
            studio.depth.composer_value.set(text.clone());
        }
        return true;
    }
    if cur < 0 {
        return false;
    }
    let next = cur - 1;
    index.set(next);
    let text = if next < 0 {
        saved.get()
    } else {
        history.get(next as usize).cloned().unwrap_or_default()
    };
    field.set_value(&text);
    studio.depth.composer_value.set(text);
    true
}

fn queue_list(ui: &Ui, studio: Studio, field: remedy_ui::TextField, editing: Signal<bool>) -> El {
    let st_when = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || !st_when.depth.queue.get().is_empty(),
        move || {
            let head = {
                let label = u.clone();
                w::text(&u, "")
                    .variant("muted")
                    .text(move || messages().text(&label, "composer-queue-label"))
            };
            let col = w::column(&u)
                .gap(2.0)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(head);
            let rows = studio.depth.queue.clone();
            let st = studio.clone();
            let f = field.clone();
            let editing = editing.clone();
            w::for_each(&col, rows, |item: &super::Queued| item.id.to_string(), {
                let u = u.clone();
                let st = st.clone();
                let f = f.clone();
                let editing = editing.clone();
                move |item| {
                    let label = if item.text.trim().is_empty() {
                        item.attachments
                            .iter()
                            .map(|a| a.name.as_str())
                            .collect::<Vec<_>>()
                            .join(", ")
                    } else {
                        item.text.chars().take(80).collect()
                    };
                    let id = item.id;
                    w::button(&u, label, move || {
                        let found = st.depth.queue.get().into_iter().find(|q| q.id == id);
                        let Some(found) = found else {
                            return;
                        };
                        st.depth.queue.update(|q| q.retain(|item| item.id != id));
                        f.set_value(&found.text);
                        st.depth.composer_value.set(found.text);
                        st.depth.attachments.set(found.attachments);
                        editing.set(true);
                    })
                    .variant("quiet")
                    .layout(|l| l.width = Length::Percent(1.0))
                }
            })
        },
    )
    .el()
}

fn attachment_row(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || !st.depth.attachments.get().is_empty(),
        move || {
            let row = w::row(&u)
                .gap(u.theme().spacing.unit)
                .align(Align::Center)
                .layout(|l| l.width = Length::Percent(1.0));
            let files = studio.depth.attachments.clone();
            let st = studio.clone();
            w::for_each(
                &row,
                files,
                |file: &super::Attachment| format!("{}|{}", file.path, file.size),
                {
                    let u = u.clone();
                    let st = st.clone();
                    move |file| {
                        let label = format!("{}  {}", file.name, logic::format_bytes(file.size));
                        let path = file.path.clone();
                        let size = file.size;
                        w::button(&u, label, move || {
                            st.depth.attachments.update(|list| {
                                if let Some(i) = list
                                    .iter()
                                    .position(|a| a.path == path && a.size == size)
                                {
                                    list.remove(i);
                                }
                            });
                        })
                        .variant("quiet")
                    }
                },
            )
        },
    )
    .el()
}

fn vision_line(ui: &Ui, studio: Studio) -> El {
    let st = studio.clone();
    let u = ui.clone();
    show(
        ui,
        move || needs_vision(&st.model.get(), &st.depth.attachments.get()),
        move || {
            let label = u.clone();
            w::text(&u, "")
                .variant("muted")
                .text(move || messages().text(&label, "composer-vision"))
        },
    )
    .el()
}

fn needs_vision(model: &str, files: &[Attachment]) -> bool {
    if !files.iter().any(|f| f.is_image) {
        return false;
    }
    let m = model.to_lowercase();
    let known = ["vision", "vl", "gpt-4o", "gemini", "claude-3", "llava", "pixtral"];
    !known.iter().any(|k| m.contains(k))
}

fn notice_line(ui: &Ui, studio: Studio) -> El {
    let st_when = studio.clone();
    let st_body = studio;
    let u = ui.clone();
    show(
        ui,
        move || !st_when.depth.notice.get().trim().is_empty(),
        move || {
            let st = st_body.clone();
            w::text(&u, "")
                .variant("muted")
                .text(move || st.depth.notice.get())
        },
    )
    .el()
}

fn boundary(text: &str, caret: usize) -> usize {
    let mut n = caret.min(text.len());
    while n > 0 && !text.is_char_boundary(n) {
        n -= 1;
    }
    n
}

fn token_at(text: &str, caret: usize) -> usize {
    text[..caret].rfind('@').unwrap_or(caret)
}
