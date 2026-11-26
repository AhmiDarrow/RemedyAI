//! In-app terminal. The widget draws the screen; [`super::pty::PtyHost`] owns the
//! shell, the same split as `TerminalSlide.tsx` plus `pty_host.rs`. Previews feed
//! a fixed screen and never spawn a process.

use super::api;
use super::nav;
use super::paths::encode_query;
use super::pty::PtyHost;
use crate::fonts::Fonts;
use crate::runtime::Client;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::net::Request;
use remedy_ui::reactive::{waker, Signal, Waker};
use remedy_ui::style::StylePatch;
use remedy_ui::term::{self, Terminal, TerminalOptions};
use remedy_ui::widgets::{self as w, MenuItem};
use remedy_ui::{El, Ui};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Static screen for goldens. No cursor blink, no live shell.
const PREVIEW_SCREEN: &[u8] = b"\r\nRemedy Desktop\r\nahmi@remedy:~/RemedyAI/desktop/src$ ";

/// What the right-click menu offers: Copy when text is selected, otherwise Paste.
/// No extra commands — Clear and Restart stay on the chrome, as in TerminalSlide.
pub fn terminal_context_items(has_selection: bool) -> Vec<&'static str> {
    if has_selection {
        vec!["Copy"]
    } else {
        vec!["Paste"]
    }
}

/// "Terminal" on Linux and macOS, "PowerShell" on Windows.
/// With a shell picked in the toolbar, that shell's name.
pub fn shell_label() -> String {
    if !crate::runtime::spawn_disabled() {
        let prefs = crate::prefs::DesktopPrefs::load();
        if !prefs.terminal_shell.trim().is_empty() {
            return super::pty::choose(&prefs.terminal_shell).label;
        }
    }
    if cfg!(windows) { "PowerShell" } else { "Terminal" }.into()
}

/// Saves the shell for every terminal from now on. Empty id is the default.
fn save_shell(id: &str) {
    let mut prefs = crate::prefs::DesktopPrefs::load();
    prefs.terminal_shell = id.to_string();
    prefs.save();
}

/// "Shell" select for the toolbar: the machine's shells, default first, plus a
/// custom path from desktop.json when one is set. Picking one restarts the shell.
fn shell_picker(ui: &Ui, model: &TerminalModel) -> El {
    let saved = crate::prefs::DesktopPrefs::load().terminal_shell;
    let mut list = super::pty::shells();
    let current = super::pty::choose(&saved);
    if !list.contains(&current) {
        list.push(current.clone());
    }
    let index = list.iter().position(|s| *s == current).unwrap_or(0);
    let labels: Vec<String> = list.iter().map(|s| s.label.clone()).collect();
    let picked: Signal<usize> = Signal::new(index);
    let seen = Rc::new(Cell::new(index));
    let m = model.clone();
    w::select(ui, "Shell", labels, picked).bind(move || {
        let i = picked.get();
        if i == seen.get() {
            return;
        }
        seen.set(i);
        if let Some(s) = list.get(i) {
            // The first entry is the default: store nothing, so it follows the machine.
            save_shell(if i == 0 { "" } else { &s.id });
            m.restart();
        }
    })
}

/// Monospace fallbacks, then the configured code face (AhmiPrint by default).
pub fn code_families() -> Vec<String> {
    let code = Fonts::load().code;
    if cfg!(windows) {
        vec![
            "Consolas".into(),
            "Cascadia Mono".into(),
            "Courier New".into(),
            code,
            "monospace".into(),
        ]
    } else {
        vec![
            "DejaVu Sans Mono".into(),
            "Liberation Mono".into(),
            "Noto Sans Mono".into(),
            code,
            "monospace".into(),
        ]
    }
}

#[derive(Clone)]
pub struct TerminalModel {
    pub session_id: Signal<Option<String>>,
    pub client: Signal<Option<Client>>,
    pub project_path: Signal<String>,
    pub preview: bool,
    pub status: Signal<String>,
    pub cwd: Signal<String>,
    cwd_lock: Rc<RefCell<Option<String>>>,
    last_cwd: Rc<RefCell<Option<String>>>,
    pty: PtyHost,
    pty_id: Arc<Mutex<Option<String>>>,
    pending_size: Arc<Mutex<Option<(u16, u16)>>>,
    generation: Arc<AtomicU64>,
    started: Rc<Cell<bool>>,
    mount_token: Rc<Cell<u64>>,
    term: Rc<RefCell<Option<Terminal>>>,
    waker: Rc<RefCell<Option<Waker>>>,
    host: Rc<RefCell<Option<El>>>,
    status_timer: Rc<Cell<u64>>,
    launch_seq: Signal<u64>,
}

impl TerminalModel {
    pub fn new(
        session_id: Signal<Option<String>>,
        client: Signal<Option<Client>>,
        project_path: Signal<String>,
        preview: bool,
    ) -> Self {
        let cwd = if preview {
            let p = project_path.get();
            if p.is_empty() {
                "/home/ahmi/RemedyAI".into()
            } else {
                p
            }
        } else {
            String::new()
        };
        let label = shell_label();
        let status = if cwd.is_empty() {
            format!("Starting {label}…")
        } else {
            format!("{label} · {cwd}")
        };
        Self {
            session_id,
            client,
            project_path,
            preview,
            status: Signal::new(status),
            cwd: Signal::new(cwd),
            cwd_lock: Rc::new(RefCell::new(None)),
            last_cwd: Rc::new(RefCell::new(None)),
            pty: PtyHost::new(),
            pty_id: Arc::new(Mutex::new(None)),
            pending_size: Arc::new(Mutex::new(None)),
            generation: Arc::new(AtomicU64::new(0)),
            started: Rc::new(Cell::new(false)),
            mount_token: Rc::new(Cell::new(0)),
            term: Rc::new(RefCell::new(None)),
            waker: Rc::new(RefCell::new(None)),
            host: Rc::new(RefCell::new(None)),
            status_timer: Rc::new(Cell::new(0)),
            launch_seq: Signal::new(0),
        }
    }

    fn begin_mount(&self) -> u64 {
        let token = self.mount_token.get().wrapping_add(1);
        self.mount_token.set(token);
        self.started.set(false);
        token
    }

    fn end_mount(&self, token: u64) {
        if self.mount_token.get() != token {
            return;
        }
        self.shutdown();
    }

    fn shutdown(&self) {
        self.generation.fetch_add(1, Ordering::Relaxed);
        self.started.set(false);
        let id = self.pty_id.lock().ok().and_then(|mut slot| slot.take());
        if let Some(id) = id {
            let _ = self.pty.close(&id);
        }
    }

    fn set_cwd(&self, next: String) {
        if self.cwd.get() != next {
            self.cwd.set(next);
        }
    }

    fn resolve_cwd(&self) {
        if let Some(pending) = nav::take_pending_terminal_cwd() {
            *self.cwd_lock.borrow_mut() = Some(pending.clone());
            self.set_cwd(pending);
            return;
        }
        *self.cwd_lock.borrow_mut() = None;
        if self.preview {
            let path = self.project_path.get();
            let path = if path.is_empty() {
                "/home/ahmi/RemedyAI".into()
            } else {
                path
            };
            self.set_cwd(path);
            return;
        }
        let Some(client) = self.client.get() else {
            self.set_cwd(self.project_path.get());
            return;
        };
        let mut q = "workspace".to_string();
        if let Some(sid) = self.session_id.get().filter(|s| !s.is_empty()) {
            q.push_str("?session_id=");
            q.push_str(&encode_query(&sid));
        }
        let model = self.clone();
        api::request(&client, Request::get(&q), move |res| {
            if model.cwd_lock.borrow().is_some() {
                return;
            }
            let next = res
                .ok()
                .and_then(|v| {
                    v.get("project_path")
                        .and_then(|p| p.as_str())
                        .map(|s| s.trim().to_string())
                })
                .unwrap_or_default();
            model.set_cwd(next);
        });
    }

    fn adopt_project_path(&self) {
        if self.cwd_lock.borrow().is_some() || self.preview {
            return;
        }
        let path = self.project_path.get();
        if !path.is_empty() {
            self.set_cwd(path);
        }
    }

    fn note_cwd(&self) {
        let cwd = self.cwd.get();
        if !self.started.get() {
            *self.last_cwd.borrow_mut() = Some(cwd);
            return;
        }
        if self.last_cwd.borrow().as_deref() == Some(cwd.as_str()) {
            return;
        }
        *self.last_cwd.borrow_mut() = Some(cwd.clone());
        let Some(term) = self.term.borrow().clone() else {
            return;
        };
        let msg = if cwd.is_empty() {
            "\r\n[session project cleared]\r\nRestarting shell…\r\n".to_string()
        } else {
            format!("\r\n[session project → {cwd}]\r\nRestarting shell…\r\n")
        };
        term.feeder().feed(msg.as_bytes());
        term.sync();
        if !self.preview {
            self.launch(&term, &cwd);
        }
    }

    fn launch(&self, term: &Terminal, workdir: &str) {
        if self.preview {
            return;
        }
        let ticket = self.generation.fetch_add(1, Ordering::Relaxed) + 1;
        if let Some(prev) = self.pty_id.lock().ok().and_then(|mut slot| slot.take()) {
            let _ = self.pty.close(&prev);
        }
        let size = term.size();
        let mut cols = u16::try_from(size.cols).unwrap_or(u16::MAX).max(20);
        let mut rows = u16::try_from(size.rows).unwrap_or(u16::MAX).max(5);
        if size.cols < 20 || size.rows < 5 {
            cols = cols.max(80);
            rows = rows.max(24);
        }
        let label = shell_label();
        self.status.set(format!("Launching {label}…"));
        let Some(wake) = self.waker.borrow().clone() else {
            let msg = format!("\r\nFailed to start {label}: ui is not running\r\n");
            term.feeder().feed(msg.as_bytes());
            term.sync();
            self.status
                .set("Could not connect to the terminal. Reopen this panel to retry.".into());
            return;
        };
        let feeder = term.feeder();
        let feeder_exit = feeder.clone();
        let feeder_err = feeder.clone();
        let pty_id = Arc::clone(&self.pty_id);
        let pty_id_done = Arc::clone(&self.pty_id);
        let generation = Arc::clone(&self.generation);
        let generation_done = Arc::clone(&self.generation);
        let status = self.status.clone();
        let status_done = self.status.clone();
        let pty = self.pty.clone();
        let pending_size = Arc::clone(&self.pending_size);
        let launch_seq = self.launch_seq.clone();
        let workdir_owned = workdir.to_string();
        // Never block the UI thread on ConPTY open/spawn. The posted
        // callback is Send, so it cannot capture Terminal/El; a bind on
        // `launch_seq` focuses and syncs on the UI thread.
        let wake_done = wake.clone();
        self.pty.open_async(
            if workdir.trim().is_empty() { None } else { Some(workdir) },
            cols,
            rows,
            move |bytes| feeder.feed(bytes),
            move || {
                let feeder_exit = feeder_exit.clone();
                let status = status.clone();
                let pty_id = Arc::clone(&pty_id);
                let generation = Arc::clone(&generation);
                let wake = wake.clone();
                let _ = wake.post(move || {
                    if generation.load(Ordering::Relaxed) != ticket {
                        return;
                    }
                    if let Ok(mut slot) = pty_id.lock() {
                        *slot = None;
                    }
                    feeder_exit.feed(b"\r\n[shell exited]\r\n");
                    status.set("Shell exited — Restart to open again".into());
                });
            },
            {
                let wake = wake_done;
                move |result| {
                    let _ = wake.post(move || {
                        match result {
                            Ok(id) => {
                                if generation_done.load(Ordering::Relaxed) != ticket {
                                    let _ = pty.close(&id);
                                    return;
                                }
                                if let Ok(mut pending) = pending_size.lock() {
                                    if let Some((c, r)) = pending.take() {
                                        let _ = pty.resize(&id, c, r);
                                    }
                                }
                                if let Ok(mut slot) = pty_id_done.lock() {
                                    *slot = Some(id);
                                }
                                let shown = if workdir_owned.trim().is_empty() {
                                    label.to_string()
                                } else {
                                    format!("{label} · {workdir_owned}")
                                };
                                status_done.set(shown);
                                launch_seq.update(|n| *n = n.wrapping_add(1));
                            }
                            Err(e) => {
                                if generation_done.load(Ordering::Relaxed) != ticket {
                                    return;
                                }
                                let msg = format!("\r\nFailed to start {label}: {e}\r\n");
                                feeder_err.feed(msg.as_bytes());
                                status_done.set(format!("Error: {e}"));
                                launch_seq.update(|n| *n = n.wrapping_add(1));
                            }
                        }
                    });
                }
            },
        );
    }

    fn consider_start(&self, host: &El, term: &Terminal, token: u64, attempt: u32) {
        if self.preview || self.mount_token.get() != token || self.started.get() {
            return;
        }
        let size = term.size();
        let px = size
            .cell_width
            .saturating_mul(u32::try_from(size.cols).unwrap_or(u32::MAX));
        let py = size
            .cell_height
            .saturating_mul(u32::try_from(size.rows).unwrap_or(u32::MAX));
        let ready = px >= 40 && py >= 40;
        if ready || attempt >= 20 {
            self.started.set(true);
            self.launch(term, &self.cwd.get());
            return;
        }
        let host = host.clone();
        let model = self.clone();
        let term = term.clone();
        let _ = host
            .clone()
            .ui()
            .after(Duration::from_millis(100), move || {
                model.consider_start(&host, &term, token, attempt + 1);
            });
    }

    fn clear_screen(&self) {
        let Some(term) = self.term.borrow().clone() else {
            return;
        };
        term.feeder().feed(b"\x1b[2J\x1b[H");
        if self.preview {
            term.feeder().feed(PREVIEW_SCREEN);
        }
        term.sync();
        term.input_element().focus();
    }

    fn restart(&self) {
        let Some(term) = self.term.borrow().clone() else {
            return;
        };
        if self.preview {
            term.feeder().feed(b"\x1b[2J\x1b[H");
            term.feeder().feed(PREVIEW_SCREEN);
            term.sync();
            return;
        }
        let label = shell_label();
        let msg = format!("\x1b[2J\x1b[HRestarting {label}…\r\n");
        term.feeder().feed(msg.as_bytes());
        term.sync();
        self.started.set(true);
        self.launch(&term, &self.cwd.get());
    }

    fn copy_cwd(&self, ui: &Ui) {
        let path = self.cwd.get();
        if path.is_empty() {
            return;
        }
        ui.copy_text(path.clone());
        let label = shell_label();
        let restore = if path.is_empty() {
            label.to_string()
        } else {
            format!("{label} · {path}")
        };
        self.status.set(format!("Copied · {path}"));
        let Some(host) = self.host.borrow().clone() else {
            return;
        };
        let prev = self.status_timer.get();
        if prev != 0 {
            host.ui().cancel_timer(prev);
        }
        let status = self.status.clone();
        let timer = self.status_timer.clone();
        let id = host.ui().after(Duration::from_millis(1800), move || {
            timer.set(0);
            status.set(restore);
        });
        self.status_timer.set(id);
    }

    fn on_output(&self, bytes: &[u8]) {
        let Ok(slot) = self.pty_id.lock() else {
            return;
        };
        let Some(id) = slot.as_deref() else {
            return;
        };
        let _ = self.pty.write_bytes(id, bytes);
    }
}

pub fn body(ui: &Ui, model: &TerminalModel) -> El {
    let token = model.begin_mount();
    *model.waker.borrow_mut() = waker().ok();
    let term = term::terminal(
        ui,
        TerminalOptions::default()
            .label(shell_label().into())
            .families(code_families()),
    );
    *model.term.borrow_mut() = Some(term.clone());
    if model.preview {
        term.feeder().feed(PREVIEW_SCREEN);
        term.sync();
    }

    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let root = w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.bg)
                .foreground(colors.text),
        );
    *model.host.borrow_mut() = Some(root.clone());

    let launch_model = model.clone();
    let _ = root.clone().bind(move || {
        if launch_model.launch_seq.get() == 0 {
            return;
        }
        if let Some(term) = launch_model.term.borrow().clone() {
            term.sync();
            term.input_element().focus();
        }
    });

    let end_model = model.clone();
    let _ = root.clone().on_removed(move || end_model.end_mount(token));

    let seen: Rc<RefCell<Option<Option<String>>>> = Rc::new(RefCell::new(None));
    let m = model.clone();
    let _ = root.clone().bind(move || {
        let sid = m.session_id.get();
        if seen.borrow().as_ref() == Some(&sid) {
            return;
        }
        *seen.borrow_mut() = Some(sid);
        m.resolve_cwd();
    });
    let m = model.clone();
    let _ = root.clone().bind(move || {
        let _ = m.project_path.get();
        m.adopt_project_path();
    });
    let m = model.clone();
    let _ = root.clone().bind(move || {
        let _ = m.cwd.get();
        m.note_cwd();
    });

    let m = model.clone();
    let guard = nav::on_terminal_cwd(move |folder| {
        let _ = nav::take_pending_terminal_cwd();
        *m.cwd_lock.borrow_mut() = Some(folder.to_string());
        m.set_cwd(folder.to_string());
    });
    let _ = root.clone().on_removed(move || drop(guard));

    let screen = term.element();
    let term_keys = term.clone();
    let ui_keys = ui.clone();
    let screen = screen.on_key_capture(move |e| on_term_key(&term_keys, &ui_keys, e));
    let term_menu = term.clone();
    let ui_menu = ui.clone();
    let screen = w::context_menu(&screen, move || menu_items(&term_menu, &ui_menu));
    let term_focus = term.clone();
    let screen = screen.on_click(move || {
        term_focus.input_element().focus();
    });
    w::tooltip(
        &screen,
        "Click to focus · Ctrl+Shift+C/V copy/paste · right-click paste · Esc exits fullscreen",
    );

    if !model.preview {
        let m = model.clone();
        let term_r = term.clone();
        let host = root.clone();
        let _ = term.on_resize(move |size| {
            if m.mount_token.get() != token {
                return;
            }
            let cols = u16::try_from(size.cols).unwrap_or(u16::MAX).max(20);
            let rows = u16::try_from(size.rows).unwrap_or(u16::MAX).max(5);
            if let Some(id) = m.pty_id.lock().ok().and_then(|slot| slot.clone()) {
                let _ = m.pty.resize(&id, cols, rows);
            } else {
                if let Ok(mut pending) = m.pending_size.lock() {
                    *pending = Some((cols, rows));
                }
                if !m.started.get() {
                    m.consider_start(&host, &term_r, token, 0);
                }
            }
        });
        let m = model.clone();
        let _ = term.on_output(move |bytes| m.on_output(bytes));
        let m = model.clone();
        let term_s = term.clone();
        let host = root.clone();
        let _ = root.ui().after(Duration::from_millis(100), move || {
            m.consider_start(&host, &term_s, token, 0);
        });
    }

    root.child(chrome(ui, model, unit, &colors))
        .child(w::divider(ui, false))
        .child(screen.layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        }))
}

fn chrome(ui: &Ui, model: &TerminalModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let press = model.clone();
    let press_ui = ui.clone();
    let status_label = model.clone();
    let status_text = w::text(ui, move || status_label.status.get())
        .variant("muted")
        .truncate()
        .layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        });
    let status = w::row(ui)
        .align(Align::Center)
        .child(status_text)
        .on_click({
            let m = press;
            let ui = press_ui;
            move || m.copy_cwd(&ui)
        })
        .semantics({
            let m = model.clone();
            move || {
                let label = if m.cwd.get().is_empty() {
                    m.status.get()
                } else {
                    "Copy working directory".into()
                };
                Semantics::new(Role::Button)
                    .label(label)
                    .action(Action::Click)
            }
        })
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .clip();
    w::tooltip(&status, {
        let m = model.clone();
        move || {
            let cwd = m.cwd.get();
            if cwd.is_empty() {
                m.status.get()
            } else {
                format!("{cwd} — click to copy cwd")
            }
        }
    });

    let clear = glyph(ui, "⌫", "Clear terminal", {
        let m = model.clone();
        move || m.clear_screen()
    });
    w::tooltip(&clear, "Clear terminal display");

    let restart = w::button(ui, "Restart", {
        let m = model.clone();
        move || m.restart()
    })
    .variant("quiet");
    let label = shell_label();
    w::tooltip(
        &restart,
        format!("Restart {label} in session project folder"),
    );

    let mut bar = w::overflow_row(ui).gap(unit).flex(status);
    // Previews show a fixed screen with no shell to choose.
    if !model.preview {
        bar = bar.child("Shell", shell_picker(ui, model));
    }
    bar
        .child("Clear terminal", clear)
        .child("Restart", restart)
        .el()
        .padding(pad(unit * 0.5, unit))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.muted),
        )
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
}

fn glyph(ui: &Ui, mark: &str, label: &str, on_press: impl Fn() + 'static) -> El {
    let colors = ui.theme().colors.clone();
    ui.el("button")
        .child(w::text(ui, mark).style(StylePatch::new().foreground(colors.muted)))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(pad(2.0, 6.0))
        .style(
            StylePatch::new()
                .background(colors.bg)
                .radius(ui.theme().radii.small)
                .foreground(colors.muted),
        )
        .on_click(on_press)
        .semantics(
            Semantics::new(Role::Button)
                .label(label)
                .action(Action::Click),
        )
}

fn on_term_key(term: &Terminal, ui: &Ui, e: &KeyEvent) -> bool {
    if !e.pressed || e.composing || e.key == Key::Escape {
        return false;
    }
    if !(e.modifiers.ctrl || e.modifiers.meta) {
        return false;
    }
    let Key::Character(ch) = &e.key else {
        return false;
    };
    match ch.as_str() {
        "c" if !e.modifiers.shift => {
            if term.selected_text().is_some() {
                term.copy_selection();
                true
            } else {
                false
            }
        }
        "v" => {
            term.input_element().focus();
            ui.request_paste();
            true
        }
        "a" if !e.modifiers.shift => {
            term.select_all();
            true
        }
        _ => false,
    }
}

fn menu_items(term: &Terminal, ui: &Ui) -> Vec<MenuItem> {
    let has = term.selected_text().is_some();
    terminal_context_items(has)
        .into_iter()
        .map(|name| {
            let term = term.clone();
            let ui = ui.clone();
            MenuItem::new(name, move || {
                if name == "Copy" {
                    term.copy_selection();
                } else {
                    term.input_element().focus();
                    ui.request_paste();
                }
            })
        })
        .collect()
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
    fn context_menu_is_copy_or_paste_only() {
        assert_eq!(terminal_context_items(true), vec!["Copy"]);
        assert_eq!(terminal_context_items(false), vec!["Paste"]);
    }

    #[test]
    fn shell_label_matches_the_desktop() {
        let label = shell_label();
        if cfg!(windows) {
            assert_eq!(label, "PowerShell");
        } else {
            assert_eq!(label, "Terminal");
        }
    }
}
