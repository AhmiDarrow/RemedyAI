//! Session scratch pad. Local cache plus PUT `/scratch`, same timing as
//! `ScratchSlide.tsx`: 250 ms debounce, flush on blur and on session switch.
//! Clear asks in the window, not in a second OS dialog.

use super::api;
use super::files::flash_status;
use super::hostcmd;
use super::nav;
use super::paths::encode_query;
use crate::runtime::{self, Client};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::input::Key;
use remedy_ui::layout::{Align, Length};
use remedy_ui::net::Request;
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets::{self as w, DialogButton};
use remedy_ui::{El, Ui, show};
use serde_json::json;
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

pub const PREVIEW_TEXT: &str = "# Notes\n- port the rail slides\n- keep the chrome sharp";

#[derive(Clone)]
pub struct ScratchModel {
    pub session_id: Signal<Option<String>>,
    pub client: Signal<Option<Client>>,
    pub preview: bool,
    pub text: Signal<String>,
    pub markdown: Signal<bool>,
    pub status: Signal<String>,
    pub dirty: Signal<bool>,
    pub loading: Signal<bool>,
    revision: Rc<Cell<u64>>,
    timer: Rc<Cell<u64>>,
    status_timer: Rc<Cell<u64>>,
    host: Rc<RefCell<Option<El>>>,
}

impl ScratchModel {
    pub fn new(session_id: Signal<Option<String>>, client: Signal<Option<Client>>, preview: bool) -> Self {
        Self {
            session_id,
            client,
            preview,
            text: Signal::new(if preview { PREVIEW_TEXT.into() } else { String::new() }),
            markdown: Signal::new(false),
            status: Signal::new(String::new()),
            dirty: Signal::new(false),
            loading: Signal::new(false),
            revision: Rc::new(Cell::new(0)),
            timer: Rc::new(Cell::new(0)),
            status_timer: Rc::new(Cell::new(0)),
            host: Rc::new(RefCell::new(None)),
        }
    }

    fn flash(&self, msg: String, ms: u64) {
        if let Some(host) = self.host.borrow().clone() {
            flash_status(&host, &self.status, &self.status_timer, msg, ms);
        } else {
            self.status.set(msg);
        }
    }

    fn save(&self, value: String, flush: bool) {
        self.revision.set(self.revision.get().wrapping_add(1));
        if self.text.get() != value {
            self.text.set(value.clone());
        }
        self.dirty.set(true);
        self.arm_persist(value, flush);
    }

    fn arm_persist(&self, value: String, flush: bool) {
        if self.preview {
            self.dirty.set(false);
            return;
        }
        if let Some(host) = self.host.borrow().clone() {
            let prev = self.timer.get();
            if prev != 0 {
                host.ui().cancel_timer(prev);
                self.timer.set(0);
            }
            if flush {
                self.persist_now(value);
                return;
            }
            let model = self.clone();
            let id = host.ui().after(Duration::from_millis(250), move || {
                model.timer.set(0);
                let v = model.text.get();
                model.persist_now(v);
            });
            self.timer.set(id);
            let _ = value;
        } else {
            self.persist_now(value);
        }
    }

    fn persist_now(&self, value: String) {
        if self.preview {
            self.dirty.set(false);
            return;
        }
        let session = self.session_id.get();
        if write_cache(&session, &value).is_err() {
            self.flash("Could not persist (storage full?)".into(), 4000);
            return;
        }
        self.dirty.set(false);
        self.persist_server(session, value);
    }

    fn persist_server(&self, session: Option<String>, value: String) {
        let Some(client) = self.client.get() else {
            return;
        };
        let (_file, pending) = cache_paths(&session);
        let _ = std::fs::write(&pending, "1");
        let body = json!({ "session_id": session.clone(), "text": value });
        let model = self.clone();
        api::request(&client, Request::put("scratch").json(&body), move |res| {
            if res.is_ok() {
                let (file, pending) = cache_paths(&model.session_id.get());
                if std::fs::read_to_string(&file).ok().as_deref() == Some(model.text.get().as_str()) {
                    let _ = std::fs::remove_file(pending);
                }
            } else if model.session_id.get() == session {
                model.flash(
                    "Saved on this device; server sync will retry when you reopen these notes.".into(),
                    6000,
                );
            }
        });
    }

    fn load(&self, session: Option<String>) {
        if self.preview {
            self.loading.set(false);
            self.dirty.set(false);
            self.markdown.set(false);
            if self.text.get() != PREVIEW_TEXT {
                self.text.set(PREVIEW_TEXT.into());
            }
            return;
        }
        self.loading.set(true);
        self.markdown.set(false);
        self.dirty.set(false);
        let (file, pending) = cache_paths(&session);
        if pending.is_file() {
            if let Ok(cached) = std::fs::read_to_string(&file) {
                self.loading.set(false);
                self.text.set(cached.clone());
                self.persist_server(session, cached);
                return;
            }
        }
        let Some(client) = self.client.get() else {
            self.loading.set(false);
            self.text.set(std::fs::read_to_string(&file).unwrap_or_default());
            return;
        };
        let mut q = "scratch".to_string();
        if let Some(sid) = session.clone().filter(|s| !s.is_empty()) {
            q.push_str("?session_id=");
            q.push_str(&encode_query(&sid));
        }
        let model = self.clone();
        let revision = self.revision.get();
        api::request(&client, Request::get(&q), move |res| {
            if model.revision.get() != revision {
                model.loading.set(false);
                return;
            }
            model.loading.set(false);
            match res {
                Ok(v) => {
                    if let Some(t) = v.get("text").and_then(|t| t.as_str()) {
                        model.text.set(t.to_string());
                    } else {
                        let cached = std::fs::read_to_string(&file).unwrap_or_default();
                        model.text.set(cached.clone());
                        if !cached.is_empty() {
                            model.persist_server(session, cached);
                        }
                    }
                }
                Err(_) => {
                    let cached = std::fs::read_to_string(&file).unwrap_or_default();
                    model.text.set(cached);
                }
            }
        });
    }

    fn export(&self) {
        if let Some(host) = self.host.borrow().clone() {
            let prev = self.timer.get();
            if prev != 0 {
                host.ui().cancel_timer(prev);
                self.timer.set(0);
            }
        }
        let text = self.text.get();
        self.persist_now(text.clone());
        let name = export_name(&self.session_id.get());
        let model = self.clone();
        hostcmd::save_text_file(name.clone(), text, move |res| match res {
            Ok(Some(path)) => model.flash(format!("Saved {path}"), 4000),
            Ok(None) => model.flash("Save cancelled".into(), 2000),
            Err(e) => model.flash(e, 4000),
        });
    }
}

pub fn count_stats(text: &str) -> (usize, usize, usize) {
    let chars = text.encode_utf16().count();
    let lines = if text.is_empty() { 0 } else { count_breaks(text) };
    let trimmed = text.trim();
    let words = if trimmed.is_empty() {
        0
    } else {
        trimmed.split_whitespace().count()
    };
    (lines, words, chars)
}

fn count_breaks(text: &str) -> usize {
    let b = text.as_bytes();
    let mut n = 1usize;
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\r' {
            n += 1;
            if b.get(i + 1) == Some(&b'\n') {
                i += 1;
            }
        } else if b[i] == b'\n' {
            n += 1;
        }
        i += 1;
    }
    n
}

fn export_name(session: &Option<String>) -> String {
    let raw = session.as_deref().filter(|s| !s.is_empty()).unwrap_or("global");
    let short: String = raw.chars().take(8).collect();
    format!("scratch-{short}.md")
}

fn cache_key(session: &Option<String>) -> String {
    let raw = session.as_deref().filter(|s| !s.is_empty()).unwrap_or("global");
    raw.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

fn cache_paths(session: &Option<String>) -> (PathBuf, PathBuf) {
    let dir = runtime::remedy_home().join("scratch-ui");
    let key = cache_key(session);
    (dir.join(format!("{key}.md")), dir.join(format!("{key}.md.pending")))
}

fn write_cache(session: &Option<String>, text: &str) -> Result<(), ()> {
    let (file, _) = cache_paths(session);
    if let Some(parent) = file.parent() {
        std::fs::create_dir_all(parent).map_err(|_| ())?;
    }
    std::fs::write(file, text).map_err(|_| ())
}

pub fn body(ui: &Ui, model: &ScratchModel) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let root = w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(StylePatch::new().background(colors.panel).foreground(colors.text));
    *model.host.borrow_mut() = Some(root.clone());

    let seen: Rc<RefCell<Option<Option<String>>>> = Rc::new(RefCell::new(None));
    let m = model.clone();
    let _ = root.clone().bind(move || {
        let sid = m.session_id.get();
        if seen.borrow().as_ref() == Some(&sid) {
            return;
        }
        if let Some(prev) = seen.borrow().clone() {
            if m.timer.get() != 0 {
                if let Some(host) = m.host.borrow().clone() {
                    host.ui().cancel_timer(m.timer.get());
                    m.timer.set(0);
                }
                let text = m.text.get();
                let _ = write_cache(&prev, &text);
                m.persist_server(prev, text);
            }
        }
        *seen.borrow_mut() = Some(sid.clone());
        m.text.set(String::new());
        m.load(sid);
    });

    let m = model.clone();
    let guard = nav::on_scratch_reload(move || {
        if m.timer.get() != 0 {
            return;
        }
        let revision = m.revision.get();
        m.load(m.session_id.get());
        let _ = revision;
    });
    let _ = root.clone().on_removed(move || drop(guard));

    let m = model.clone();
    let _ = root.clone().on_key_capture(move |e| {
        if !e.pressed || e.composing {
            return false;
        }
        if e.key == Key::Escape {
            return false;
        }
        if (e.modifiers.ctrl || e.modifiers.meta)
            && !e.modifiers.shift
            && matches!(&e.key, Key::Character(ch) if ch.eq_ignore_ascii_case("s"))
        {
            m.export();
            return true;
        }
        false
    });

    root.child(toolbar(ui, model, unit, &colors))
        .child(status_line(ui, model))
        .child(editor(ui, model, &colors))
        .child(w::divider(ui, false))
        .child(stats_line(ui, model, unit, &colors))
}

fn toolbar(ui: &Ui, model: &ScratchModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let m = model.clone();
    let title = w::text(ui, "").variant("muted").text(move || {
        let who = if m.session_id.get().as_deref().is_some_and(|s| !s.is_empty()) {
            "session"
        } else {
            "global"
        };
        let tail = if m.loading.get() {
            "loading…"
        } else if m.dirty.get() {
            "saving…"
        } else {
            "auto-saves"
        };
        format!("Scratch · {who} · {tail}")
    });

    let preview = w::button(
        ui,
        {
            let m = model.clone();
            move || {
                if m.markdown.get() {
                    "Edit".to_string()
                } else {
                    "Preview".to_string()
                }
            }
        },
        {
            let m = model.clone();
            move || m.markdown.set(!m.markdown.get())
        },
    )
    .variant({
        let m = model.clone();
        move || if m.markdown.get() { "primary" } else { "quiet" }
    });
    let _ = preview.clone().semantics({
        let m = model.clone();
        move || {
            Semantics::new(Role::Button)
                .label(if m.markdown.get() {
                    "Edit".to_string()
                } else {
                    "Preview".to_string()
                })
                .checked(m.markdown.get())
                .action(Action::Click)
        }
    });

    let copy = w::button(ui, "Copy", {
        let m = model.clone();
        let ui = ui.clone();
        move || {
            ui.copy_text(m.text.get());
            m.flash("Copied all".into(), 2800);
        }
    })
    .variant("quiet")
    .disabled({
        let m = model.clone();
        move || m.text.get().is_empty()
    });

    let save = w::button(ui, "Save as…", {
        let m = model.clone();
        move || m.export()
    })
    .variant("quiet");
    w::tooltip(&save, "Export as Markdown file (Ctrl+S)");

    let clear = w::button(ui, "Clear", {
        let ui = ui.clone();
        let m = model.clone();
        move || confirm_clear(&ui, &m)
    })
    .variant("quiet")
    .disabled({
        let m = model.clone();
        move || m.text.get().is_empty()
    })
    .style(StylePatch::new().foreground(colors.danger));

    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(pad(unit, unit * 2.0))
        .child(title.layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(preview)
        .child(copy)
        .child(save)
        .child(clear)
}

fn confirm_clear(ui: &Ui, model: &ScratchModel) {
    let m = model.clone();
    let m2 = model.clone();
    w::dialog(
        ui,
        "Clear this scratch pad?",
        "Everything written here is erased. This can’t be undone — use “Save as…” first if you want to keep it.",
        vec![
            DialogButton::new("Cancel", "quiet", || {}),
            DialogButton::new("Clear pad", "danger_solid", move || {
                m.save(String::new(), true);
                m.flash("Cleared".into(), 2800);
            }),
        ],
    );
    let _ = m2;
}

fn status_line(ui: &Ui, model: &ScratchModel) -> El {
    let m = model.clone();
    show(
        ui,
        move || !m.status.get().is_empty(),
        {
            let ui = ui.clone();
            let m = model.clone();
            move || {
                w::text(&ui, "")
                    .variant("muted")
                    .text({
                        let m = m.clone();
                        move || m.status.get()
                    })
                    .semantics(Semantics::new(Role::Status).label("Scratch status"))
                    .padding(pad(2.0, 8.0))
            }
        },
    )
    .el()
}

fn editor(ui: &Ui, model: &ScratchModel, colors: &remedy_ui::style::ColorRoles) -> El {
    let edit = show(
        ui,
        {
            let m = model.clone();
            move || !m.markdown.get()
        },
        {
            let ui = ui.clone();
            let model = model.clone();
            let bg = colors.bg;
            let fg = colors.text;
            move || scratch_field(&ui, &model, bg, fg)
        },
    )
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    let preview = show(
        ui,
        {
            let m = model.clone();
            move || m.markdown.get()
        },
        {
            let ui = ui.clone();
            let m = model.clone();
            let bg = colors.bg;
            let fg = colors.text;
            move || {
                w::scroll(&ui)
                    .style(StylePatch::new().background(bg).foreground(fg))
                    .padding(pad(8.0, 8.0))
                    .child(w::text(&ui, "").text({
                        let m = m.clone();
                        move || {
                            let t = m.text.get();
                            if t.is_empty() {
                                "— empty —".into()
                            } else {
                                t
                            }
                        }
                    }).layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                    }))
                    .layout(|l| {
                        l.grow = 1.0;
                        l.width = Length::Percent(1.0);
                        l.min_width = Length::Px(0.0);
                        l.min_height = Length::Px(0.0);
                    })
            }
        },
    )
    .el()
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    w::column(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(edit)
        .child(preview)
}

fn scratch_field(ui: &Ui, model: &ScratchModel, bg: remedy_ui::core::Color, fg: remedy_ui::core::Color) -> El {
    let field = w::text_area(ui, "Scratch pad notes").placeholder(
        "Notes, TODOs, paste dumps… (Markdown ok) · Ctrl+S export · Esc exits fullscreen",
    );
    let writing = Rc::new(Cell::new(false));
    let f = field.clone();
    let m = model.clone();
    let field = field.on_change({
        let writing = writing.clone();
        move || {
            if writing.get() {
                return;
            }
            m.save(f.value(), false);
        }
    });
    let f = field.clone();
    let m = model.clone();
    let writing2 = writing.clone();
    let el = field.el();
    let _ = el.clone().bind(move || {
        let v = m.text.get();
        if f.value() != v {
            writing2.set(true);
            f.set_value(&v);
            writing2.set(false);
        }
    });
    let f = field.clone();
    let m = model.clone();
    let _ = el.clone().on_focus_change(move |focused| {
        if focused {
            return;
        }
        if m.timer.get() != 0 {
            if let Some(host) = m.host.borrow().clone() {
                host.ui().cancel_timer(m.timer.get());
                m.timer.set(0);
            }
            m.persist_now(f.value());
        }
    });
    // The current pad is a borderless flex-1 textarea. The field fills the
    // column; stats stay on the bottom edge.
    el.style(
        StylePatch::new()
            .background(bg)
            .foreground(fg)
            .border(remedy_ui::core::Color::TRANSPARENT)
            .border_width(0.0),
    )
    .layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
        l.height = Length::Percent(1.0);
        l.width = Length::Percent(1.0);
        l.max_width = Length::Percent(1.0);
    })
}

fn stats_line(ui: &Ui, model: &ScratchModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let m = model.clone();
    w::row(ui)
        .padding(pad(unit * 0.5, unit * 2.0))
        .style(StylePatch::new().foreground(colors.muted))
        .child(w::text(ui, "").variant("muted").text(move || {
            let (lines, words, chars) = count_stats(&m.text.get());
            format!(
                "{lines} line{} · {words} word{} · {chars} char{}",
                if lines == 1 { "" } else { "s" },
                if words == 1 { "" } else { "s" },
                if chars == 1 { "" } else { "s" },
            )
        }))
}

fn pad(y: f32, x: f32) -> remedy_ui::core::Insets {
    remedy_ui::core::Insets {
        top: y,
        bottom: y,
        start: x,
        end: x,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stats_match_the_scratch_pad_rules() {
        assert_eq!(count_stats(""), (0, 0, 0));
        assert_eq!(count_stats("one"), (1, 1, 3));
        assert_eq!(count_stats("a\nb"), (2, 2, 3));
        assert_eq!(count_stats(PREVIEW_TEXT).0, 3);
        assert!(count_stats(PREVIEW_TEXT).1 > 3);
    }
}
