//! Two-pane Studio chat: Coding/Actions beside Chat, as in the current app
//! (`remedy.chatSplit.v1`).
//!
//! The split is a layout over existing chat-depth data: thinking, tool steps,
//! and BUILD todos already live on the session. Nothing new is fetched here.
//! Side and width persist under `~/.remedy/ui/chat-split.json`. Tests that set
//! `REMEDY_DESKTOP_NO_SPAWN` keep the defaults and never touch disk.

use std::cell::{Cell, RefCell};
use std::fs;
use std::path::PathBuf;
use std::rc::Rc;

use serde::{Deserialize, Serialize};

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::{Insets, Size};
use remedy_ui::input::{Buttons, Key, PointerPhase};
use remedy_ui::layout::{AUTO, Align, Justify, Length, Position};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{App, El, Ui, Window, WindowSpec, show};

use crate::depth::TodoItem;
use crate::runtime::{self, spawn_disabled};
use crate::studio::{ChatMessage, Studio};
use crate::tokens;
use crate::work::{self, StepStatus, WorkStep};
use crate::{messages, say};

/// Matches `--chat-bubble-radius: 0.85rem` in the current app (at 16px).
pub const PANE_RADIUS: f32 = crate::theme_pack::MECHA.layout.pane_radius;
pub const ACTIONS_WIDTH_DEFAULT: f32 = 380.0;
pub const ACTIONS_WIDTH_MIN: f32 = 240.0;
pub const ACTIONS_WIDTH_MAX: f32 = 720.0;
pub const CHAT_PANE_MIN: f32 = 280.0;
pub const HANDLE_W: f32 = 8.0;
/// Stack into tabs when the well is narrower than this, same as the current app.
pub const STACK_BELOW: f32 = 720.0;
/// Slack for the rails that sit outside the well, so the 720px threshold
/// tracks the well rather than the outer window.
const RAIL_SLACK: f32 = 96.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ActionsSide {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MobilePane {
    Actions,
    Chat,
}

/// In-app float for a Code or Chat pane (not persisted).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FloatKind {
    PopOut,
    Fullscreen,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FloatStage {
    pub pane: MobilePane,
    pub kind: FloatKind,
    pub resume: Option<FloatKind>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FloatFrame {
    pub top: f32,
    pub start: f32,
    pub width: f32,
    pub height: f32,
    pub fills: bool,
}

pub fn open_float(_current: Option<FloatStage>, pane: MobilePane, kind: FloatKind) -> Option<FloatStage> {
    Some(FloatStage {
        pane,
        kind,
        resume: None,
    })
}

pub fn float_to_fullscreen(current: Option<FloatStage>, pane: MobilePane) -> Option<FloatStage> {
    let resume = current.and_then(|s| {
        if s.pane == pane && s.kind != FloatKind::Fullscreen {
            Some(s.kind)
        } else if s.pane == pane {
            s.resume
        } else {
            None
        }
    });
    Some(FloatStage {
        pane,
        kind: FloatKind::Fullscreen,
        resume,
    })
}

pub fn escape_float(current: Option<FloatStage>) -> Option<FloatStage> {
    match current {
        Some(s) if s.kind == FloatKind::Fullscreen => {
            s.resume.map(|kind| FloatStage {
                pane: s.pane,
                kind,
                resume: None,
            })
        }
        Some(_) | None => None,
    }
}

pub fn dock_float(_current: Option<FloatStage>) -> Option<FloatStage> {
    None
}

/// Whether this pane should stay in the split well.
/// A popped-out pane leaves the well (it lives in its own window). A
/// fullscreen pane stays and fills the well; its sibling is hidden.
pub fn pane_stays_in_dock(stage: Option<FloatStage>, is_actions: bool) -> bool {
    match stage {
        None => true,
        Some(s) => {
            let this = (s.pane == MobilePane::Actions) == is_actions;
            if this {
                s.kind == FloatKind::Fullscreen
            } else {
                s.kind != FloatKind::Fullscreen
            }
        }
    }
}

#[derive(Clone)]
struct FloatCtl {
    stage: Signal<Option<FloatStage>>,
    pop: Rc<RefCell<Option<Window>>>,
    on_top: Signal<bool>,
    studio: Studio,
    prefs: Signal<ChatSplit>,
}

thread_local! {
    static FLOAT_CTL: RefCell<Option<FloatCtl>> = const { RefCell::new(None) };
}

fn bind_float_ctl(ctl: FloatCtl) {
    FLOAT_CTL.with(|c| *c.borrow_mut() = Some(ctl));
}

fn float_ctl() -> Option<FloatCtl> {
    FLOAT_CTL.with(|c| c.borrow().clone())
}

fn studio_app() -> Option<App> {
    crate::slides::current_app().or_else(crate::settings::host)
}

fn main_window() -> Option<Window> {
    crate::slides::current_window()
}

impl FloatCtl {
    fn set_stage(&self, next: Option<FloatStage>) {
        self.stage.set(next);
    }

    fn close_pop(&self) {
        if let Some(win) = self.pop.borrow_mut().take() {
            win.close();
        }
    }

    fn exit_main_fullscreen(&self) {
        if let Some(win) = main_window() {
            win.set_fullscreen(false);
        }
    }

    fn dock(&self) {
        self.close_pop();
        self.exit_main_fullscreen();
        self.on_top.set(false);
        self.set_stage(None);
    }

    fn escape(&self) -> bool {
        let Some(cur) = self.stage.get() else {
            return false;
        };
        match escape_float(Some(cur)) {
            Some(next) => {
                if cur.kind == FloatKind::Fullscreen {
                    self.exit_main_fullscreen();
                }
                self.set_stage(Some(next));
                if next.kind == FloatKind::PopOut {
                    self.open_pop(next.pane);
                }
                true
            }
            None => {
                self.dock();
                true
            }
        }
    }

    fn enter_fullscreen(&self, pane: MobilePane) {
        let current = self.stage.get();
        // Pop-out window: fill *that* window, do not close it.
        if current.is_some_and(|s| s.pane == pane && s.kind == FloatKind::PopOut) {
            if let Some(win) = self.pop.borrow().clone() {
                win.set_fullscreen(true);
                win.focus();
                return;
            }
        }
        self.close_pop();
        self.set_stage(float_to_fullscreen(current, pane));
        if let Some(win) = main_window() {
            win.set_fullscreen(true);
        }
    }

    fn open_pop(&self, pane: MobilePane) {
        if let Some(win) = self.pop.borrow().clone() {
            if self.stage.get().is_some_and(|s| s.pane == pane && s.kind == FloatKind::PopOut) {
                win.set_fullscreen(false);
                win.focus();
                return;
            }
        }
        let Some(app) = studio_app() else {
            // Headless previews have no host: keep the pane in the well.
            return;
        };
        self.exit_main_fullscreen();
        self.close_pop();
        self.set_stage(open_float(self.stage.get(), pane, FloatKind::PopOut));
        let title = if pane == MobilePane::Actions {
            "Coding / Actions"
        } else {
            "Chat"
        };
        let ctl = self.clone();
        let studio = self.studio.clone();
        let prefs = self.prefs.clone();
        let stage = self.stage.clone();
        let on_top = self.on_top.get();
        let win = app.open(
            WindowSpec::new(title)
                .size(720.0, 640.0)
                .min_size(360.0, 280.0)
                .always_on_top(on_top),
            move |ui, window| {
                crate::settings::bind_window(ui);
                let colors = ui.theme().colors.clone();
                let dock = ctl.clone();
                let top = ctl.on_top.clone();
                let win_top = window.clone();
                let chrome = w::row(ui)
                    .align(Align::Center)
                    .gap(8.0)
                    .padding(Insets::all(8.0))
                    .layout(|l| l.width = Length::Percent(1.0))
                    .child(
                        w::text(ui, title)
                            .style(StylePatch::new().weight(600.0).text_size(13.0)),
                    )
                    .child(w::row(ui).layout(|l| {
                        l.grow = 1.0;
                        l.min_width = Length::Px(0.0);
                    }))
                    .child(
                        w::button(ui, {
                            let u = ui.clone();
                            let top = top.clone();
                            move || {
                                let label = messages().text(&u, "chat-split-always-on-top");
                                if top.get() {
                                    format!("{label} · on")
                                } else {
                                    label
                                }
                            }
                        }, {
                            let top = top.clone();
                            let win_top = win_top.clone();
                            move || {
                                let next = !top.get();
                                top.set(next);
                                win_top.set_always_on_top(next);
                            }
                        })
                        .variant("quiet"),
                    )
                    .child(
                        w::button(ui, {
                            let u = ui.clone();
                            move || messages().text(&u, "chat-split-dock-back")
                        }, move || dock.dock())
                        .variant("quiet"),
                    );
                let float = stage.clone();
                let body = if pane == MobilePane::Actions {
                    actions_column(ui, studio.clone(), prefs.clone(), float, true, true)
                } else {
                    chat_column(ui, studio.clone(), prefs.clone(), float, true, true)
                };
                let esc = ctl.clone();
                let root = ui
                    .root()
                    .column()
                    .style(StylePatch::new().background(colors.bg))
                    .on_key(move |e| {
                        if e.composing || !e.pressed || e.key != Key::Escape {
                            return false;
                        }
                        esc.escape();
                        true
                    })
                    .child(chrome)
                    .child(body.layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.height = Length::Percent(1.0);
                        l.grow = 1.0;
                        l.min_height = Length::Px(0.0);
                        l.min_width = Length::Px(0.0);
                    }));
                let _ = root;
            },
        );
        let dock = self.clone();
        win.on_closed(move || {
            dock.pop.borrow_mut().take();
            if dock.stage.get().is_some_and(|s| s.kind == FloatKind::PopOut) {
                dock.set_stage(None);
            }
        });
        *self.pop.borrow_mut() = Some(win);
    }
}

/// Geometry for Pop out / PiP / Fullscreen inside a well. Must fit 400 / 700 / 1180.
pub fn float_frame(kind: FloatKind, well: Size) -> FloatFrame {
    let w = well.width.max(0.0);
    let h = well.height.max(0.0);
    match kind {
        FloatKind::Fullscreen => FloatFrame {
            top: 0.0,
            start: 0.0,
            width: w,
            height: h,
            fills: true,
        },
        FloatKind::PopOut => {
            let width = (w * 0.76).clamp(0.0, w);
            let height = (h * 0.80).clamp(0.0, h);
            FloatFrame {
                top: h * 0.08,
                start: w * 0.12,
                width,
                height,
                fills: false,
            }
        }
    }
}

pub fn float_frame_fits(kind: FloatKind, well: Size) -> bool {
    let f = float_frame(kind, well);
    f.width <= w_plus(well.width)
        && f.height <= w_plus(well.height)
        && f.start >= -0.01
        && f.top >= -0.01
        && f.start + f.width <= w_plus(well.width)
        && f.top + f.height <= w_plus(well.height)
}

fn w_plus(v: f32) -> f32 {
    v + 0.51
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChatSplit {
    #[serde(default = "default_side")]
    pub actions_side: ActionsSide,
    #[serde(default = "default_width")]
    pub actions_width: f32,
}

fn default_side() -> ActionsSide {
    ActionsSide::Left
}

fn default_width() -> f32 {
    ACTIONS_WIDTH_DEFAULT
}

impl Default for ChatSplit {
    fn default() -> Self {
        Self {
            actions_side: default_side(),
            actions_width: default_width(),
        }
    }
}

/// Clamp the actions width so both panes keep a usable column.
pub fn clamp_actions_width(width: f32, preferred: f32, container: Option<f32>) -> f32 {
    let _ = preferred;
    let mut max = ACTIONS_WIDTH_MAX;
    if let Some(total) = container {
        let room = (total - HANDLE_W - CHAT_PANE_MIN).max(ACTIONS_WIDTH_MIN);
        max = max.min(room);
    }
    width.clamp(ACTIONS_WIDTH_MIN, max)
}

/// True when the well should fall back to tabs.
pub fn should_stack(container: f32) -> bool {
    container < STACK_BELOW || container < ACTIONS_WIDTH_MIN + CHAT_PANE_MIN + HANDLE_W
}

fn prefs_path() -> PathBuf {
    runtime::remedy_home().join("ui").join("chat-split.json")
}

pub fn load() -> ChatSplit {
    if spawn_disabled() {
        return ChatSplit::default();
    }
    let Ok(raw) = fs::read_to_string(prefs_path()) else {
        return ChatSplit::default();
    };
    let mut prefs: ChatSplit = serde_json::from_str(&raw).unwrap_or_default();
    prefs.actions_width = clamp_actions_width(prefs.actions_width, ACTIONS_WIDTH_DEFAULT, None);
    prefs
}

pub fn save(prefs: ChatSplit) {
    if spawn_disabled() {
        return;
    }
    let path = prefs_path();
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if let Ok(raw) = serde_json::to_string_pretty(&prefs) {
        let _ = fs::write(path, raw);
    }
}

/// The split well: Actions and Chat side by side, or tabs when the window is narrow.
pub fn workspace(ui: &Ui, studio: Studio) -> El {
    let prefs = Signal::new(load());
    let mobile = Signal::new(MobilePane::Chat);
    let narrow = Signal::new(false);
    let float: Signal<Option<FloatStage>> = Signal::new(None);
    let well = Signal::new(Size {
        width: 0.0,
        height: 0.0,
    });
    let unit = ui.theme().spacing.unit;

    let n_out = narrow.clone();
    let well_out = well.clone();
    let root = w::column(ui)
        .layout(|l| {
            l.position = Position::Relative;
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        })
        // Use the well's laid-out width, not the window minus a rail guess: a
        // Sessions sidebar or Settings rail makes the well much narrower, and
        // the two panes must stack instead of painting over each other.
        .on_resize(move |size| {
            n_out.set_if_changed(should_stack(size.width));
            well_out.set_if_changed(size);
        });

    let tabs = {
        let u = ui.clone();
        let n = narrow.clone();
        let mob = mobile.clone();
        let pr = prefs.clone();
        show(
            ui,
            move || n.get(),
            move || tabs_row(&u, mob.clone(), pr.clone()),
        )
        .el()
    };

    let panes = panes_row(
        ui,
        studio.clone(),
        prefs.clone(),
        mobile,
        narrow.clone(),
        float.clone(),
    );
    bind_float_ctl(FloatCtl {
        stage: float.clone(),
        pop: Rc::new(RefCell::new(None)),
        on_top: Signal::new(false),
        studio,
        prefs: prefs.clone(),
    });
    let esc = float.clone();
    let root = root.on_key(move |e| {
        if e.composing || !e.pressed || e.key != Key::Escape {
            return false;
        }
        float_ctl().is_some_and(|c| c.escape()) || {
            let _ = esc;
            false
        }
    });

    root.padding(Insets::all(unit * 1.5))
        .gap(unit)
        .semantics({
            let prefs = prefs.clone();
            let narrow = narrow;
            let u = ui.clone();
            move || {
                let side = match prefs.get().actions_side {
                    ActionsSide::Left => messages().text(&u, "chat-split-side-left"),
                    ActionsSide::Right => messages().text(&u, "chat-split-side-right"),
                };
                let label = if narrow.get() {
                    messages().text(&u, "chat-split-tabs")
                } else {
                    side
                };
                Semantics::new(Role::Region).label(label)
            }
        })
        .child(tabs)
        .child(panes)
}

fn tabs_row(ui: &Ui, mobile: Signal<MobilePane>, prefs: Signal<ChatSplit>) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let actions = tab_btn(
        ui,
        mobile.clone(),
        MobilePane::Actions,
        "chat-split-tab-actions",
    );
    let chat = tab_btn(ui, mobile, MobilePane::Chat, "chat-split-tab-chat");
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: 0.0,
            end: unit,
            bottom: 0.0,
            start: unit,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics({
            let u = ui.clone();
            move || Semantics::new(Role::TabList).label(messages().text(&u, "chat-split-tabs"))
        })
        .child(actions)
        .child(chat)
        .child(w::row(ui).layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(swap_btn(ui, prefs, true))
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(1.0, line.with_alpha(0.45)),
        ))
}

fn tab_btn(ui: &Ui, mobile: Signal<MobilePane>, which: MobilePane, key: &'static str) -> El {
    let colors = ui.theme().colors.clone();
    let active = {
        let m = mobile.clone();
        move || m.get() == which
    };
    let u = ui.clone();
    let set = mobile;
    w::button(ui, move || messages().text(&u, key), move || set.set(which))
        .variant("quiet")
        .style({
            let colors = colors.clone();
            let active = active.clone();
            move || {
                let mut p = StylePatch::new().radius(2.0).text_size(12.0).weight(600.0);
                if active() {
                    p = tokens::selected_surface(&colors)
                        .text_size(12.0)
                        .weight(600.0)
                        .foreground(colors.text);
                } else {
                    p = p.foreground(colors.muted).radius(2.0);
                }
                p
            }
        })
        .semantics({
            let active = active;
            let u = ui.clone();
            move || {
                Semantics::new(Role::Tab)
                    .label(messages().text(&u, key))
                    .selected(active())
            }
        })
}

fn panes_row(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    mobile: Signal<MobilePane>,
    narrow: Signal<bool>,
    float: Signal<Option<FloatStage>>,
) -> El {
    let ui = ui.clone();
    let actions = pane_shell(
        &ui,
        studio.clone(),
        prefs.clone(),
        mobile.clone(),
        narrow.clone(),
        float.clone(),
        true,
    );
    let chat = pane_shell(
        &ui,
        studio,
        prefs.clone(),
        mobile,
        narrow.clone(),
        float.clone(),
        false,
    );
    let handle = resize_handle(&ui, prefs.clone(), narrow, float);

    // One mounted row. Remounting the same pane elements through `show()`
    // left both panes with empty bounds after a swap. Row-reverse keeps them
    // alive and keeps their sizes; the handle stays between them.
    let row = pane_pair(&ui, actions, handle, chat);
    let row_b = row.clone();
    let p = prefs;
    row.bind(move || {
        let reverse = p.get().actions_side == ActionsSide::Right;
        let _ = row_b.clone().layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.reverse = reverse;
        });
    })
}

fn pane_pair(ui: &Ui, first: El, handle: El, second: El) -> El {
    w::row(ui)
        .align(Align::Stretch)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.height = Length::Percent(1.0);
        })
        .clip()
        .child(first)
        .child(handle)
        .child(second)
}

fn pane_shell(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    mobile: Signal<MobilePane>,
    narrow: Signal<bool>,
    float: Signal<Option<FloatStage>>,
    is_actions: bool,
) -> El {
    let colors = ui.theme().colors.clone();
    let visible = {
        let n = narrow.clone();
        let m = mobile;
        let f = float.clone();
        move || {
            if !pane_stays_in_dock(f.get(), is_actions) {
                return false;
            }
            if !n.get() {
                return true;
            }
            match m.get() {
                MobilePane::Actions => is_actions,
                MobilePane::Chat => !is_actions,
            }
        }
    };
    let u = ui.clone();
    let st = studio;
    let pr = prefs;
    let n = narrow.clone();
    let fl = float.clone();
    let shell = show(ui, visible, move || {
        let pane = if is_actions {
            actions_column(&u, st.clone(), pr.clone(), fl.clone(), n.get(), false)
        } else {
            chat_column(&u, st.clone(), pr.clone(), fl.clone(), n.get(), false)
        };
        pane.style(tokens::panel_surface(&colors).radius(PANE_RADIUS))
        .clip()
    })
    .el();
    let shell_b = shell.clone();
    let n_layout = narrow;
    let f_layout = float;
    shell.bind(move || {
        let stacked = n_layout.get();
        let other_floating = f_layout
            .get()
            .is_some_and(|s| (s.pane == MobilePane::Actions) != is_actions);
        let _ = shell_b.clone().layout(move |l| {
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            if stacked || !is_actions || other_floating {
                // Chat (and both panes when stacked, or when the other pane
                // is floating) fills leftover well width.
                l.grow = 1.0;
                l.shrink = 1.0;
                l.basis = Length::Px(0.0);
                l.width = Length::Percent(1.0);
            } else {
                // Also undo the fill it had while the other pane was out, or it
                // keeps the whole well and the returning pane has no room.
                l.grow = 0.0;
                l.shrink = 0.0;
                l.basis = Length::Auto;
                l.width = Length::Auto;
            }
        });
    })
}

fn actions_column(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    float: Signal<Option<FloatStage>>,
    stacked: bool,
    overlay: bool,
) -> El {
    let width = {
        let p = prefs.clone();
        move || p.get().actions_width
    };
    w::column(ui)
        .layout(move |l| {
            if stacked {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
                l.grow = 1.0;
                l.shrink = 1.0;
                l.basis = Length::Px(0.0);
            } else {
                l.width = Length::Px(width());
                l.min_width = Length::Px(ACTIONS_WIDTH_MIN);
                l.max_width = Length::Px(ACTIONS_WIDTH_MAX);
                l.shrink = 0.0;
                l.grow = 0.0;
                // No basis: the shell stacks vertically, so a basis of the
                // width made the pane a square instead of full height.
            }
            l.height = Length::Percent(1.0);
        })
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Region).label(messages().text(&u, "chat-split-actions-kicker"))
            }
        })
        .child(pane_head(
            ui,
            prefs,
            "chat-split-actions-kicker",
            "chat-split-actions-sub",
            false,
            studio.clone(),
            stacked,
            float,
            overlay,
        ))
        .child(actions_feed(ui, studio).layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        }))
}

fn chat_column(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    float: Signal<Option<FloatStage>>,
    stacked: bool,
    overlay: bool,
) -> El {
    let thread = w::column(ui)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .child(crate::depth::banners::lead(ui, studio.clone()))
        .child(crate::chat::feed(ui, studio.clone()))
        .child(crate::depth::banners::status(ui, studio.clone()));
    w::column(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(CHAT_PANE_MIN);
            l.height = Length::Percent(1.0);
            l.grow = 1.0;
            l.shrink = 1.0;
            l.basis = Length::Px(0.0);
        })
        .semantics({
            let u = ui.clone();
            move || {
                Semantics::new(Role::Region).label(messages().text(&u, "chat-split-chat-kicker"))
            }
        })
        .child(pane_head(
            ui,
            prefs,
            "chat-split-chat-kicker",
            "chat-split-chat-sub",
            true,
            studio,
            stacked,
            float,
            overlay,
        ))
        .child(thread)
}

fn pane_head(
    ui: &Ui,
    prefs: Signal<ChatSplit>,
    kicker: &'static str,
    sub: &'static str,
    is_chat: bool,
    studio: Studio,
    stacked: bool,
    float: Signal<Option<FloatStage>>,
    overlay: bool,
) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let titles = w::column(ui)
        .gap(1.0)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
            // Truncate down to 120 px before the pane tools wrap to a second line.
            l.basis = Length::Px(120.0);
            l.min_width = Length::Px(0.0);
        })
        .child(
            say(ui, kicker, "strong")
                .truncate()
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::section_kicker(&colors)),
        )
        .child(
            say(ui, sub, "muted")
                .truncate()
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::typo(tokens::META).foreground(colors.muted)),
        );
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(Insets {
            top: 8.0,
            end: tokens::GUTTER,
            bottom: 8.0,
            start: tokens::GUTTER,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
            l.shrink = 0.0;
        })
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(1.0, crate::tokens::hairline(&colors)),
        ))
        .child(titles);
    if is_chat {
        let st = studio;
        let u = ui.clone();
        row = row.child(
            show(
                ui,
                move || st.streaming.get(),
                move || {
                    w::text(&u, "")
                        .text({
                            let u = u.clone();
                            move || messages().text(&u, "chat-split-working")
                        })
                        .nowrap()
                        .style(
                            tokens::section_kicker(&u.theme().colors)
                                .foreground(u.theme().colors.accent),
                        )
                },
            )
            .el(),
        );
    }
    let pane = if is_chat { MobilePane::Chat } else { MobilePane::Actions };
    row = row.child(pane_float_tools(ui, float, pane, overlay));
    if !stacked && !overlay {
        row = row.child(swap_btn(ui, prefs, false));
    }
    row
}

fn swap_btn(ui: &Ui, prefs: Signal<ChatSplit>, in_tabs: bool) -> El {
    let u = ui.clone();
    let p = prefs;
    w::button(
        ui,
        move || "⇄".to_string(),
        move || {
            let mut next = p.get();
            next.actions_side = match next.actions_side {
                ActionsSide::Left => ActionsSide::Right,
                ActionsSide::Right => ActionsSide::Left,
            };
            p.set(next);
            save(next);
        },
    )
    .variant("quiet")
    .style(
        StylePatch::new()
            .radius(8.0)
            .text_size(if in_tabs { 16.0 } else { 15.0 }),
    )
    .semantics({
        let u = u;
        move || Semantics::new(Role::Button).label(messages().text(&u, "chat-split-swap"))
    })
}


/// The float buttons of one pane, rebuilt when the pane's float state changes
/// so a fullscreen pane swaps Pop out / Fullscreen for Dock back.
fn pane_float_tools(
    ui: &Ui,
    float: Signal<Option<FloatStage>>,
    pane: MobilePane,
    overlay: bool,
) -> El {
    let key = move || {
        let kind = float.try_get().ok().flatten().filter(|s| s.pane == pane).map(|s| s.kind);
        vec![match kind {
            Some(FloatKind::Fullscreen) => "full",
            Some(_) => "float",
            None => "dock",
        }]
    };
    let state: Signal<Vec<&'static str>> = Signal::new(key());
    let host = w::row(ui).layout(|l| {
        l.min_width = Length::Px(0.0);
        l.shrink = 0.0;
    });
    let _ = host.clone().bind(move || {
        let next = key();
        if state.try_get().is_ok_and(|cur| cur != next) {
            let _ = state.try_set(next);
        }
    });
    let u = ui.clone();
    w::for_each(
        &host,
        state,
        |k| k.to_string(),
        move |_| float_tools_row(&u, float, pane, overlay),
    )
}

fn float_tools_row(
    ui: &Ui,
    float: Signal<Option<FloatStage>>,
    pane: MobilePane,
    overlay: bool,
) -> El {
    let colors = ui.theme().colors.clone();
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(ui.theme().spacing.unit)
        .layout(|l| {
            l.min_width = Length::Px(0.0);
            l.wrap = true;
            l.shrink = 0.0;
        });
    let kind = float.get().filter(|s| s.pane == pane).map(|s| s.kind);
    // A fullscreen pane stays in the dock, so it needs Dock back there too.
    let in_this = kind == Some(FloatKind::Fullscreen) || (overlay && kind.is_some());
    if overlay && !in_this {
        return row;
    }
    let btn = |ui: &Ui, key: &'static str, on: Box<dyn Fn() + 'static>| {
        let u = ui.clone();
        w::button(ui, move || messages().text(&u, key), on)
            .variant("quiet")
            .style(
                StylePatch::new()
                    .radius(2.0)
                    .border(tokens::hairline(&colors))
                    .border_width(1.0)
                    .shadow(tokens::elevation(&colors))
                    .text_size(12.0)
                    .weight(550.0),
            )
    };
    if in_this {
        if kind != Some(FloatKind::Fullscreen) {
            row = row.child(btn(
                ui,
                "chat-split-fullscreen",
                Box::new(move || {
                    if let Some(ctl) = float_ctl() {
                        ctl.enter_fullscreen(pane);
                    }
                }),
            ));
        }
        row = row.child(btn(
            ui,
            "chat-split-dock-back",
            Box::new(move || {
                if let Some(ctl) = float_ctl() {
                    ctl.dock();
                } else {
                    float.set(dock_float(float.get()));
                }
            }),
        ));
        return row;
    }
    row = row.child(btn(
        ui,
        "chat-split-pop-out",
        Box::new(move || {
            if let Some(ctl) = float_ctl() {
                ctl.open_pop(pane);
            }
        }),
    ));
    row = row.child(btn(
        ui,
        "chat-split-fullscreen",
        Box::new(move || {
            if let Some(ctl) = float_ctl() {
                ctl.enter_fullscreen(pane);
            } else {
                float.set(float_to_fullscreen(float.get(), pane));
            }
        }),
    ));
    row
}

fn float_host(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    float: Signal<Option<FloatStage>>,
    well: Signal<Size>,
    narrow: Signal<bool>,
) -> El {
    let u = ui.clone();
    let st = studio;
    let pr = prefs;
    let fl = float.clone();
    let n = narrow;
    show(
        ui,
        move || fl.get().is_some(),
        move || {
            let stage = fl.get().expect("float host visible");
            float_layer(&u, st.clone(), pr.clone(), fl.clone(), well.clone(), n.get(), stage)
        },
    )
    .el()
}

fn float_layer(
    ui: &Ui,
    studio: Studio,
    prefs: Signal<ChatSplit>,
    float: Signal<Option<FloatStage>>,
    well: Signal<Size>,
    stacked: bool,
    stage: FloatStage,
) -> El {
    let colors = ui.theme().colors.clone();
    let frame = float_frame(stage.kind, well.get());
    let is_actions = stage.pane == MobilePane::Actions;
    let body = if is_actions {
        actions_column(ui, studio, prefs, float.clone(), stacked, true)
    } else {
        chat_column(ui, studio, prefs, float.clone(), stacked, true)
    };
    let mut skin = tokens::overlay_surface(&colors);
    if stage.kind == FloatKind::Fullscreen {
        skin = skin.radius(0.0);
    }
    let title_key = if is_actions {
        "chat-split-actions-kicker"
    } else {
        "chat-split-chat-kicker"
    };
    let layer = ui
        .el("pane-float")
        .column()
        .clip()
        .focusable(true)
        .layout(move |l| {
            l.position = Position::Absolute;
            let size = well.get();
            let frame = float_frame(stage.kind, size);
            if frame.fills || size.width <= 0.0 || size.height <= 0.0 {
                l.inset = Insets::ZERO;
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
            } else {
                l.inset = Insets {
                    top: frame.top,
                    start: frame.start,
                    end: AUTO,
                    bottom: AUTO,
                };
                l.width = Length::Px(frame.width);
                l.height = Length::Px(frame.height);
            }
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .style(skin)
        .semantics({
            let u = ui.clone();
            move || Semantics::new(Role::Dialog).label(messages().text(&u, title_key)).modal(true)
        })
        .on_key({
            let float = float.clone();
            move |e| {
                if e.composing || !e.pressed || e.key != Key::Escape {
                    return false;
                }
                float.set(escape_float(float.get()));
                true
            }
        })
        .child(body);
    let layer_b = layer.clone();
    let well_b = well;
    let layer = layer.bind(move || {
        let size = well_b.get();
        let frame = float_frame(stage.kind, size);
        let _ = layer_b.clone().layout(move |l| {
            l.position = Position::Absolute;
            if frame.fills || size.width <= 0.0 || size.height <= 0.0 {
                l.inset = Insets::ZERO;
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
            } else {
                l.inset = Insets {
                    top: frame.top,
                    start: frame.start,
                    end: AUTO,
                    bottom: AUTO,
                };
                l.width = Length::Px(frame.width);
                l.height = Length::Px(frame.height);
            }
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        });
    });
    let _ = frame;
    layer
}

fn resize_handle(
    ui: &Ui,
    prefs: Signal<ChatSplit>,
    narrow: Signal<bool>,
    float: Signal<Option<FloatStage>>,
) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted).with_alpha(0.7);
    let u_show = ui.clone();
    let p_show = prefs;
    let n = narrow;
    let f = float;
    show(
        ui,
        move || !n.get() && f.get().is_none(),
        move || handle_el(&u_show, p_show.clone(), line),
    )
    .el()
    .layout(|l| {
        l.shrink = 0.0;
        l.height = Length::Percent(1.0);
    })
}

fn handle_el(ui: &Ui, prefs: Signal<ChatSplit>, line: remedy_ui::core::Color) -> El {
    let drag: Rc<Cell<Option<(f32, f32)>>> = Rc::new(Cell::new(None));
    let start = drag.clone();
    let move_drag = drag.clone();
    let end_drag = drag;
    let ui_ptr = ui.clone();
    let prefs_down = prefs.clone();
    let prefs_move = prefs.clone();
    let prefs_key = prefs.clone();
    let hot = Signal::new(false);
    let grip = ui.theme().colors.accent;
    let handle = ui
        .el("chat_split_handle")
        .row()
        .align(Align::Stretch)
        .justify(Justify::Center)
        .layout(|l| {
            l.width = Length::Px(HANDLE_W);
            l.min_width = Length::Px(HANDLE_W);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .child(
            ui.el("chat_split_hairline")
                .layout(|l| {
                    l.width = Length::Px(2.0);
                    l.height = Length::Percent(1.0);
                })
                // Lights up under the pointer: the pane edge can be dragged.
                .style(move || {
                    StylePatch::new()
                        .background(if hot.get() { grip } else { line })
                        .radius(1.0)
                }),
        )
        .cursor(remedy_ui::input::Cursor::ColResize)
        .on_hover(move |on| hot.set(on))
        .focusable(true)
        .on_pointer(move |e| match e.phase {
            PointerPhase::Down if e.buttons.contains(Buttons::PRIMARY) => {
                if ui_ptr.click_count() == 2 {
                    start.set(None);
                    let mut next = prefs_down.get();
                    let well = (ui_ptr.window_size().width - RAIL_SLACK).max(0.0);
                    next.actions_width = clamp_actions_width(
                        ACTIONS_WIDTH_DEFAULT,
                        ACTIONS_WIDTH_DEFAULT,
                        Some(well),
                    );
                    prefs_down.set(next);
                    save(next);
                } else {
                    start.set(Some((e.position.x, prefs_down.get().actions_width)));
                }
                true
            }
            PointerPhase::Move => match move_drag.get() {
                Some((x0, w0)) => {
                    let side = prefs_move.get().actions_side;
                    let dx = match side {
                        ActionsSide::Left => e.position.x - x0,
                        ActionsSide::Right => x0 - e.position.x,
                    };
                    let well = (ui_ptr.window_size().width - RAIL_SLACK).max(0.0);
                    let mut next = prefs_move.get();
                    next.actions_width = clamp_actions_width(w0 + dx, w0, Some(well));
                    prefs_move.set(next);
                    true
                }
                None => false,
            },
            PointerPhase::Up | PointerPhase::Cancel => {
                if end_drag.take().is_some() {
                    save(prefs.get());
                    true
                } else {
                    false
                }
            }
            _ => false,
        })
        .on_key({
            let ui = ui.clone();
            move |e| {
                if e.composing || !e.pressed {
                    return false;
                }
                let step = match e.key {
                    Key::ArrowLeft => -16.0,
                    Key::ArrowRight => 16.0,
                    _ => return false,
                };
                let side = prefs_key.get().actions_side;
                let signed = match side {
                    ActionsSide::Left => step,
                    ActionsSide::Right => -step,
                };
                let well = (ui.window_size().width - RAIL_SLACK).max(0.0);
                let mut next = prefs_key.get();
                next.actions_width = clamp_actions_width(
                    next.actions_width + signed,
                    next.actions_width,
                    Some(well),
                );
                prefs_key.set(next);
                save(next);
                true
            }
        })
        .semantics({
            let prefs = prefs.clone();
            let ui = ui.clone();
            move || {
                Semantics::new(Role::Separator)
                    .label(messages().text(&ui, "chat-split-resize"))
                    .description(messages().text(&ui, "chat-split-resize-hint"))
                    .numeric(
                        f64::from(prefs.get().actions_width),
                        f64::from(ACTIONS_WIDTH_MIN),
                        f64::from(ACTIONS_WIDTH_MAX),
                    )
            }
        });
    w::tooltip(&handle, {
        let ui = ui.clone();
        move || messages().text(&ui, "chat-split-resize-hint")
    });
    handle
}

fn actions_feed(ui: &Ui, studio: Studio) -> El {
    // The feed's state lives as long as the feed. The list that shows it is built inside the
    // branch, fresh each time the branch mounts: an element built once out here and mounted
    // again after its branch was torn down still ran its list over state that teardown had
    // dropped, and the app closed (changing the text size rebuilds this pane).
    let turns: Signal<Vec<ActionTurn>> = Signal::new(Vec::new());
    let expanded: Signal<Vec<String>> = Signal::new(Vec::new());
    let process_open: Signal<Vec<String>> = Signal::new(Vec::new());
    let empty_st = studio.clone();
    let u_empty = ui.clone();
    let u_feed = ui.clone();
    show(
        ui,
        move || collect_turns(&empty_st).is_empty(),
        move || actions_standby(&u_empty),
    )
    .fallback(move || {
        let ui = &u_feed;
        let unit = ui.theme().spacing.unit;
        let col = w::column(ui)
            .gap(unit * 3.0)
            .padding(Insets {
                top: 12.0,
                end: tokens::GUTTER,
                bottom: tokens::GUTTER,
                start: tokens::GUTTER,
            })
            .layout(|l| l.width = Length::Percent(1.0));
        let st = studio.clone();
        let _ = col.clone().bind(move || {
            let next = collect_turns(&st);
            if turns.try_with(|cur| *cur != next).unwrap_or(false) {
                let _ = turns.try_set(next);
            }
        });
        let (u, st_rows) = (ui.clone(), studio.clone());
        let col = w::for_each(&col, turns, |t| t.id.clone(), move |t| {
            action_turn(&u, st_rows.clone(), t.clone(), expanded, process_open)
        });
        w::scroll(ui)
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.grow = 1.0;
                l.basis = Length::Px(0.0);
                l.min_height = Length::Px(0.0);
            })
            .child(col)
    })
    .el()
}

/// Secondary info panel when there is no tool work yet. Not a toolbox.
fn actions_standby(ui: &Ui) -> El {
    let colors = ui.theme().colors.clone();
    let body = w::column(ui)
        .gap(tokens::SPACE * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .child(
            say(ui, "actions-empty-body", "muted")
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::typo(tokens::BODY).foreground(colors.text)),
        )
        .child(
            say(ui, "actions-empty-hint", "muted")
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::meta_caps(&colors)),
        );
    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(
            w::column(ui)
                .padding(Insets::all(tokens::GUTTER))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                })
                .child(
                    tokens::info_panel(ui, messages().text(ui, "actions-empty-title"), body).layout(
                        |l| {
                            l.width = Length::Percent(1.0);
                            l.min_width = Length::Px(0.0);
                        },
                    ),
                ),
        )
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ActionTurn {
    id: String,
    thinking: String,
    steps: Vec<WorkStep>,
    todos: Vec<TodoItem>,
    live: bool,
    session_id: String,
    request_id: String,
}

fn collect_turns(studio: &Studio) -> Vec<ActionTurn> {
    let streaming = studio.streaming.get();
    let live_id = studio.live_message_id.get();
    let session_id = studio.active.get().unwrap_or_default();
    // Read without subscribing: the job record changes with every streamed
    // word, and the request id only when a turn starts (streaming changes too).
    let live_request_id = remedy_ui::reactive::untrack(|| {
        studio.depth.job(&session_id).map(|j| j.request_id).filter(|s| !s.is_empty()).unwrap_or_default()
    });
    let mut out = Vec::new();
    // The same recent part of the conversation the chat builds.
    for m in studio.recent_messages().0 {
        if m.role != "assistant" {
            continue;
        }
        if streaming && live_id.as_deref() == Some(m.id.as_str()) {
            continue;
        }
        if skip_idle(&m) {
            continue;
        }
        out.push(ActionTurn {
            id: m.id.clone(),
            thinking: m.thinking.clone(),
            steps: m.steps.clone(),
            todos: Vec::new(),
            live: false,
            session_id: session_id.clone(),
            request_id: m.request_id.clone(),
        });
    }
    let thinking = studio.live_thinking.get();
    let steps = studio.live_steps.get();
    let todos = studio.depth.todos.get();
    if streaming || !thinking.trim().is_empty() || !steps.is_empty() || !todos.is_empty() {
        out.push(ActionTurn {
            id: live_id.unwrap_or_else(|| "live".into()),
            thinking,
            steps,
            todos,
            live: true,
            session_id: session_id.clone(),
            request_id: live_request_id,
        });
    }
    out
}

fn skip_idle(m: &ChatMessage) -> bool {
    m.thinking.trim().is_empty() && m.steps.is_empty()
}

fn action_turn(
    ui: &Ui,
    studio: Studio,
    turn: ActionTurn,
    expanded: Signal<Vec<String>>,
    process_open: Signal<Vec<String>>,
) -> El {
    let unit = ui.theme().spacing.unit;
    let thinking = turn.thinking.clone();
    let steps = turn.steps.clone();
    let todos = turn.todos.clone();
    let live = turn.live;
    let mut col = w::column(ui)
        .gap(unit * 2.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(crate::chat::work_meta(
            ui,
            {
                let steps = steps.clone();
                move || steps.clone()
            },
            {
                let thinking = thinking.clone();
                move || thinking.clone()
            },
            live,
        ));
    if !thinking.trim().is_empty() {
        col = col.child(thinking_block(ui, &turn.id, &thinking, expanded));
    }
    if !steps.is_empty() {
        col = col.child(tools_block(ui, studio.clone(), &turn, &steps, process_open));
    }
    if !todos.is_empty() {
        col = col.child(build_block(ui, &todos));
    }
    col
}

fn thinking_block(ui: &Ui, id: &str, thinking: &str, expanded: Signal<Vec<String>>) -> El {
    let colors = ui.theme().colors.clone();
    let chunks = thinking_chunks(thinking);
    let open = {
        let expanded = expanded.clone();
        let id = id.to_string();
        move || thinking_is_open(&expanded.get(), &id)
    };
    let toggle = {
        let expanded = expanded.clone();
        let id = id.to_string();
        move || {
            let mut next = expanded.get();
            if let Some(i) = next.iter().position(|x| x == &id) {
                next.remove(i);
            } else {
                next.push(id.clone());
            }
            expanded.set(next);
        }
    };
    let chevron = {
        let open = open.clone();
        move || {
            if open() {
                "▾".to_string()
            } else {
                "▸".to_string()
            }
        }
    };
    let u = ui.clone();
    let header = w::row(ui)
        .align(Align::Center)
        .gap(8.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .on_click(toggle)
        .semantics({
            let u = u.clone();
            move || {
                Semantics::new(Role::Button)
                    .label(messages().text(&u, "chat-split-thinking"))
                    .action(remedy_ui::a11y::Action::Click)
            }
        })
        .child(
            w::text(ui, "")
                .text(chevron)
                .style(tokens::typo(tokens::META).foreground(colors.muted)),
        )
        .child(say(ui, "chat-split-thinking", "muted").style(tokens::section_kicker(&colors)));
    let u_body = ui.clone();
    let body = show(ui, open, move || {
        let mut paras = w::column(&u_body)
            .gap(tokens::SPACE)
            .layout(|l| l.width = Length::Percent(1.0));
        for para in &chunks {
            paras = paras.child(thinking_para(&u_body, para));
        }
        paras
    })
    .el();
    w::column(ui)
        .gap(tokens::SPACE)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(header)
        .child(body)
}

/// Thinking in the Coding / Actions pane starts collapsed. The owner opens
/// a turn by clicking its THINKING header; `expanded` then holds that id.
fn thinking_is_open(expanded: &[String], id: &str) -> bool {
    expanded.iter().any(|x| x == id)
}

/// Thinking arrives as one buffer; blank lines become readable paragraphs.
fn thinking_chunks(text: &str) -> Vec<String> {
    text.split("\n\n")
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

fn thinking_para(ui: &Ui, text: &str) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let ink = colors.text.mix_oklab(colors.muted, 0.22);
    let label = text.to_string();
    w::row(ui)
        .align(Align::Start)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0))
        .style(StylePatch::new().border_side(
            BorderEdge::Start,
            BorderSide::new(2.0, colors.accent.with_alpha(0.28)),
        ))
        .padding(Insets {
            top: 2.0,
            end: 4.0,
            bottom: 2.0,
            start: 10.0,
        })
        .semantics(move || Semantics::new(Role::Label).label(label.clone()))
        .child(
            w::text(ui, text)
                .layout(|l| l.width = Length::Percent(1.0))
                .style(tokens::typo(tokens::THINKING).foreground(ink)),
        )
}

fn process_mode() -> String {
    crate::settings::current()
        .map(|s| work::normalize_tool_process(&s.tool_process.get()).to_string())
        .unwrap_or_else(|| "off".into())
}

fn tools_block(
    ui: &Ui,
    studio: Studio,
    turn: &ActionTurn,
    steps: &[WorkStep],
    process_open: Signal<Vec<String>>,
) -> El {
    let u = ui.clone();
    let steps = steps.to_vec();
    let process_open = process_open.clone();
    let session_id = turn.session_id.clone();
    let request_id = turn.request_id.clone();
    let min = show(ui, || process_mode() == "off", {
        let u = u.clone();
        let steps = steps.clone();
        move || tools_min(&u, &steps)
    })
    .el();
    let med = show(ui, || process_mode() == "medium", {
        let u = u.clone();
        let steps = steps.clone();
        let process_open = process_open.clone();
        let studio = studio.clone();
        let session_id = session_id.clone();
        let request_id = request_id.clone();
        move || tools_medium(&u, studio.clone(), &session_id, &request_id, &steps, process_open.clone())
    })
    .el();
    let full = show(ui, || process_mode() == "full", {
        let u = u.clone();
        let steps = steps.clone();
        let process_open = process_open.clone();
        let studio = studio.clone();
        let session_id = session_id.clone();
        let request_id = request_id.clone();
        move || tools_full(&u, studio.clone(), &session_id, &request_id, &steps, process_open.clone())
    })
    .el();
    w::column(ui)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(min)
        .child(med)
        .child(full)
}

/// The name a run of steps with one tool is shown under, and whether to paint
/// it as failed. One failure in eight is "Bash ×8 · 1 failed" in the ordinary
/// colour: painting the whole group red read as eight failures.
fn group_label(group: &[WorkStep]) -> (String, bool) {
    let name = short_tool(&group[0].name);
    let failed = group.iter().filter(|s| s.status == StepStatus::Error).count();
    if group.len() == 1 {
        return (name, failed == 1);
    }
    let label = match failed {
        0 => format!("{name} ×{}", group.len()),
        n if n == group.len() => format!("{name} ×{} · all failed", group.len()),
        n => format!("{name} ×{} · {n} failed", group.len()),
    };
    (label, failed == group.len())
}

/// Min: compact name chips and counts, no command lines.
fn tools_min(ui: &Ui, steps: &[WorkStep]) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let mut row = w::wrap(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    for group in work::group_process_steps(steps) {
        let (label, failed) = group_label(group);
        let running = group.iter().any(|s| s.status == StepStatus::Running);
        let fg = if failed {
            colors.danger
        } else if running {
            colors.accent
        } else {
            colors.text
        };
        row = row.child(
            w::text(ui, label)
                .nowrap()
                .style(
                    tokens::typo(tokens::MONO)
                        .foreground(fg)
                        .weight(600.0),
                )
                .padding(Insets {
                    top: 2.0,
                    end: 6.0,
                    bottom: 2.0,
                    start: 6.0,
                }),
        );
    }
    row
}

fn tools_medium(
    ui: &Ui,
    studio: Studio,
    session_id: &str,
    request_id: &str,
    steps: &[WorkStep],
    process_open: Signal<Vec<String>>,
) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit * 0.8)
        .layout(|l| l.width = Length::Percent(1.0));
    for group in work::group_process_steps(steps) {
        col = col.child(process_group(
            ui,
            studio.clone(),
            session_id,
            request_id,
            group,
            "medium",
            process_open.clone(),
        ));
    }
    col
}

fn tools_full(
    ui: &Ui,
    studio: Studio,
    session_id: &str,
    request_id: &str,
    steps: &[WorkStep],
    process_open: Signal<Vec<String>>,
) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit)
        .layout(|l| l.width = Length::Percent(1.0));
    for step in steps {
        col = col.child(process_group(
            ui,
            studio.clone(),
            session_id,
            request_id,
            std::slice::from_ref(step),
            "full",
            process_open.clone(),
        ));
    }
    col
}

fn process_group(
    ui: &Ui,
    studio: Studio,
    session_id: &str,
    request_id: &str,
    group: &[WorkStep],
    mode: &'static str,
    process_open: Signal<Vec<String>>,
) -> El {
    let step = &group[0];
    let id = group
        .iter()
        .map(|s| s.id.clone())
        .collect::<Vec<_>>()
        .join("+");
    let colors = ui.theme().colors.clone();
    let (title, failed) = group_label(group);
    let running = group.iter().any(|s| s.status == StepStatus::Running);
    let mark = if failed {
        "✕"
    } else if running {
        "◌"
    } else {
        "✓"
    };
    let mark_color = if failed {
        colors.danger
    } else if running {
        colors.accent
    } else {
        colors.ok
    };
    let summary = group
        .iter()
        .find_map(|s| {
            work::step_inline_summary(
                &s.name,
                s.args_text.as_deref(),
                s.result_text.as_deref(),
                s.error.as_deref(),
            )
        })
        .or_else(|| group.iter().find_map(|s| s.detail.clone()));
    let body = process_group_body(group, mode);
    let can_open = !body.is_empty();
    let open = {
        let process_open = process_open.clone();
        let id = id.clone();
        move || can_open && process_body_open(mode, &process_open.get(), &id)
    };
    let toggle = {
        let process_open = process_open.clone();
        let id = id.clone();
        move || {
            if !can_open {
                return;
            }
            let mut next = process_open.get();
            if let Some(i) = next.iter().position(|x| x == &id) {
                next.remove(i);
            } else {
                next.push(id.clone());
            }
            process_open.set(next);
        }
    };
    let chevron = {
        let open = open.clone();
        move || {
            if !can_open {
                return " ".to_string();
            }
            if open() {
                "▾".to_string()
            } else {
                "▸".to_string()
            }
        }
    };
    let mut head = w::row(ui)
        .align(Align::Center)
        .gap(8.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .on_click(toggle)
        .semantics({
            let title = title.clone();
            let summary = summary.clone().unwrap_or_default();
            move || {
                let mut s = title.clone();
                if !summary.is_empty() {
                    s.push(' ');
                    s.push_str(&summary);
                }
                Semantics::new(Role::Button).label(s).action(remedy_ui::a11y::Action::Click)
            }
        })
        .child(
            w::text(ui, "")
                .text(chevron)
                .nowrap()
                .layout(|l| l.shrink = 0.0)
                .style(tokens::typo(tokens::META).foreground(colors.muted)),
        )
        .child(
            w::text(ui, mark)
                .nowrap()
                .layout(|l| l.shrink = 0.0)
                .style(
                    tokens::typo(tokens::MONO)
                        .foreground(mark_color)
                        .weight(650.0),
                ),
        )
        .child(
            w::text(ui, &title)
                .nowrap()
                .layout(|l| l.shrink = 0.0)
                .style(
                    tokens::typo(tokens::MONO)
                        .foreground(colors.text)
                        .weight(600.0),
                ),
        );
    if let Some(summary) = summary.clone() {
        head = head.child(
            w::text(ui, summary)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::typo(tokens::MONO).foreground(colors.muted)),
        );
    }
    let evidence: Signal<Option<String>> = Signal::new(None);
    let fetching: Signal<bool> = Signal::new(false);
    let want_evidence = work::is_full_process_mode(mode) && group.len() == 1;
    let call_id = step.id.clone();
    let session_id = session_id.to_string();
    let request_id = request_id.to_string();
    let u_body = ui.clone();
    let body_view = show(ui, open, {
        let evidence = evidence.clone();
        let fetching = fetching.clone();
        let body = body.clone();
        move || {
            if want_evidence {
                request_tool_evidence(
                    studio.clone(),
                    &session_id,
                    &request_id,
                    &call_id,
                    evidence.clone(),
                    fetching.clone(),
                );
            }
            process_dump(
                &u_body,
                {
                    let evidence = evidence.clone();
                    let body = body.clone();
                    move || merge_evidence_body(&body, evidence.get().as_deref())
                },
                failed,
            )
        }
    })
    .el();
    w::column(ui)
        .gap(tokens::SPACE)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(head)
        .child(body_view)
}

fn encode_path_seg(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn request_tool_evidence(
    studio: Studio,
    session_id: &str,
    request_id: &str,
    call_id: &str,
    evidence: Signal<Option<String>>,
    fetching: Signal<bool>,
) {
    if fetching.get() || evidence.get().is_some() {
        return;
    }
    if studio.local_only || session_id.is_empty() || request_id.is_empty() || call_id.is_empty() {
        return;
    }
    let Some(client) = studio.client() else {
        return;
    };
    fetching.set(true);
    let path = format!(
        "sessions/{}/turns/{}/tools/{}",
        encode_path_seg(session_id),
        encode_path_seg(request_id),
        encode_path_seg(call_id),
    );
    let Ok(wake) = remedy_ui::reactive::waker() else {
        fetching.set(false);
        return;
    };
    runtime::get_json(&client, &path, move |res| {
        // get_json completes on a worker thread; Signal::set must run on the UI waker.
        // Setting a signal here used to PANIC ("belongs to another thread's runtime")
        // when Settings opened and remounted the Actions trail.
        let _ = wake.post(move || apply_tool_evidence_result(res, &evidence, &fetching));
    });
}

/// Apply a tool-evidence payload on the UI thread.
fn apply_tool_evidence_result(
    res: Result<serde_json::Value, String>,
    evidence: &Signal<Option<String>>,
    fetching: &Signal<bool>,
) {
    if let Ok(v) = res {
        if let Some(out) = v
            .get("output")
            .and_then(|s| s.as_str())
            .filter(|s| !s.is_empty())
        {
            // The row may be gone (its session was deleted) by the time this lands.
            let _ = evidence.try_set(Some(out.to_string()));
            return;
        }
    }
    let _ = fetching.try_set(false);
}

fn merge_evidence_body(body: &str, evidence: Option<&str>) -> String {
    let Some(output) = evidence.map(str::trim).filter(|s| !s.is_empty()) else {
        return body.to_string();
    };
    if body.contains("// --- result ---") {
        let prefix = body.split("// --- result ---").next().unwrap_or("").trim_end();
        if prefix.is_empty() {
            return format!("// --- result ---\n{output}");
        }
        return format!("{prefix}\n\n// --- result ---\n{output}");
    }
    if body.is_empty() {
        return output.to_string();
    }
    format!("{body}\n\n{output}")
}

fn process_body_open(_mode: &str, toggled: &[String], id: &str) -> bool {
    // Min, medium, and full all start collapsed. The owner expands a
    // step; switching the visibility mode must not uncollapse every action.
    toggled.iter().any(|x| x == id)
}

fn process_group_body(group: &[WorkStep], mode: &str) -> String {
    let full = work::is_full_process_mode(mode);
    let mut parts = Vec::new();
    for (i, step) in group.iter().enumerate() {
        if full {
            if let Some(args) = step.args_text.as_deref().filter(|s| !s.is_empty()) {
                let tag = if group.len() > 1 {
                    format!("// --- args [{}] ---\n", i + 1)
                } else {
                    "// --- args ---\n".into()
                };
                parts.push(format!("{tag}{args}"));
            }
            if let Some(err) = step.error.as_deref().filter(|s| !s.is_empty()) {
                parts.push(format!("// --- error ---\n{err}"));
            } else if let Some(res) = step.result_text.as_deref().filter(|s| !s.is_empty()) {
                parts.push(format!("// --- result ---\n{res}"));
            }
        } else {
            let preview = work::step_medium_preview(step.result_text.as_deref(), step.error.as_deref());
            if !preview.is_empty() {
                parts.push(preview);
            }
        }
    }
    parts.join("\n\n")
}

fn process_dump(ui: &Ui, text: impl Fn() -> String + 'static, failed: bool) -> El {
    let colors = ui.theme().colors.clone();
    let fg = if failed { colors.danger } else { colors.muted };
    w::text(ui, "")
        .text(text)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(tokens::typo(tokens::MONO).foreground(fg).text_size(12.0))
}

fn short_tool(name: &str) -> String {
    let trimmed = name.rsplit([':', '/']).next().unwrap_or(name);
    let mut chars = trimmed.chars();
    match chars.next() {
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str().replace('_', " ").as_str(),
        None => trimmed.to_string(),
    }
}

fn build_block(ui: &Ui, todos: &[TodoItem]) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let mut col = w::column(ui)
        .gap(unit)
        .padding(Insets {
            top: 8.0,
            end: 12.0,
            bottom: 8.0,
            start: 12.0,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .style(tokens::raised_surface(&colors))
        .child(say(ui, "chat-split-build", "strong").style(tokens::section_kicker(&colors)));
    for t in todos {
        col = col.child(todo_row(ui, t));
    }
    col
}

fn todo_row(ui: &Ui, item: &TodoItem) -> El {
    let colors = ui.theme().colors.clone();
    let status = item.status.to_ascii_lowercase();
    let (mark, color) = match status.as_str() {
        "completed" | "done" => ("✓", colors.accent),
        "in_progress" | "running" => ("›", colors.accent),
        "cancelled" | "canceled" => ("–", colors.muted),
        _ => ("○", colors.muted),
    };
    w::row(ui)
        .align(Align::Start)
        .gap(8.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics({
            let label = format!("{mark} {}", item.content);
            move || Semantics::new(Role::Label).label(label.clone())
        })
        .child(w::text(ui, mark).style(tokens::typo(tokens::MONO).foreground(color).weight(650.0)))
        .child(
            w::text(ui, &item.content)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                })
                .style(
                    tokens::typo(tokens::BODY)
                        .foreground(colors.text)
                        .text_size(13.5),
                ),
        )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_group_is_failed_only_when_every_step_failed() {
        let ok = |id: &str| WorkStep::done(id, "bash");
        let bad = |id: &str| WorkStep::failed(id, "bash", "exit 1");
        assert_eq!(group_label(&[ok("1"), ok("2")]), ("Bash ×2".to_string(), false));
        assert_eq!(group_label(&[ok("1"), bad("2"), ok("3")]), ("Bash ×3 · 1 failed".to_string(), false));
        assert_eq!(group_label(&[bad("1"), bad("2")]), ("Bash ×2 · all failed".to_string(), true));
        assert_eq!(group_label(&[bad("1")]), ("Bash".to_string(), true));
    }

    #[test]
    fn default_width_stays_put_in_a_wide_well() {
        assert_eq!(
            clamp_actions_width(ACTIONS_WIDTH_DEFAULT, ACTIONS_WIDTH_DEFAULT, Some(1100.0)),
            ACTIONS_WIDTH_DEFAULT
        );
    }

    #[test]
    fn clamp_keeps_a_minimum_chat_column() {
        let w = clamp_actions_width(700.0, 700.0, Some(600.0));
        assert!(w <= 600.0 - HANDLE_W - CHAT_PANE_MIN + 0.01);
        assert!(w >= ACTIONS_WIDTH_MIN);
    }

    #[test]
    fn stack_below_the_current_app_threshold() {
        assert!(should_stack(719.0));
        assert!(!should_stack(720.0));
        assert!(should_stack(
            ACTIONS_WIDTH_MIN + CHAT_PANE_MIN + HANDLE_W - 1.0
        ));
    }

    #[test]
    fn thinking_splits_on_blank_lines() {
        let chunks = thinking_chunks("First thought.\n\nSecond, after a beat.\n\n  \n\nThird.");
        assert_eq!(
            chunks,
            vec!["First thought.", "Second, after a beat.", "Third."]
        );
    }

    #[test]
    fn thinking_starts_collapsed() {
        assert!(!thinking_is_open(&[], "t1"));
        assert!(thinking_is_open(&["t1".into()], "t1"));
        assert!(!thinking_is_open(&["t2".into()], "t1"));
    }

    #[test]
    fn process_detail_stays_collapsed_until_toggled() {
        assert!(!process_body_open("full", &[], "s1"));
        assert!(process_body_open("full", &["s1".into()], "s1"));
        assert!(!process_body_open("medium", &[], "s1"));
        assert!(process_body_open("medium", &["s1".into()], "s1"));
        assert!(!process_body_open("off", &[], "s1"));
        assert!(process_body_open("off", &["s1".into()], "s1"));
        let body = "// --- args ---\n{\"command\":\"ls\"}\n\n// --- result ---\nshort…";
        let merged = merge_evidence_body(body, Some("one\ntwo\nthree"));
        assert!(merged.contains("one"), "{merged}");
        assert!(merged.contains("// --- args ---"), "{merged}");
        assert!(!merged.contains("short…"), "{merged}");
    }

    #[test]
    fn tool_evidence_apply_stores_full_output() {
        let evidence: Signal<Option<String>> = Signal::new(None);
        let fetching: Signal<bool> = Signal::new(true);
        apply_tool_evidence_result(
            Ok(serde_json::json!({"preview": "short…", "output": "line one\nline two"})),
            &evidence,
            &fetching,
        );
        assert_eq!(evidence.get().as_deref(), Some("line one\nline two"));
        assert!(fetching.get(), "a successful body should leave fetching set");
        apply_tool_evidence_result(Ok(serde_json::json!({})), &evidence, &fetching);
        // already has output; a later empty payload only flips fetching
        assert_eq!(evidence.get().as_deref(), Some("line one\nline two"));
        assert!(!fetching.get());
    }

    #[test]
    fn tool_evidence_fetch_posts_signal_updates_to_the_ui_waker() {
        // Regression for 8b715d5b: opening Settings remounted the Actions trail,
        // get_json completed on the remedy-get worker, and evidence.set panicked
        // with "the signal belongs to another thread's runtime".
        let src = include_str!("chat_split.rs");
        let start = src.find("fn request_tool_evidence").expect("request_tool_evidence");
        let rest = &src[start..];
        let end = rest[1..].find("\nfn ").map(|i| i + 1).unwrap_or(rest.len());
        let body = &rest[..end];
        assert!(body.contains("remedy_ui::reactive::waker()"), "must take the UI waker");
        assert!(body.contains("wake.post"), "must post Signal updates onto the UI thread: {body}");
        let worker = body.split("runtime::get_json").nth(1).expect("get_json");
        let before_post = worker.split("wake.post").next().unwrap();
        assert!(
            !before_post.contains("evidence.set") && !before_post.contains("fetching.set"),
            "the worker callback must not touch Signals before posting: {before_post}"
        );
    }

    #[test]
    fn pane_float_stages_and_escape() {
        let pop = open_float(None, MobilePane::Actions, FloatKind::PopOut).unwrap();
        assert_eq!(pop.kind, FloatKind::PopOut);
        let full = float_to_fullscreen(Some(pop), MobilePane::Actions).unwrap();
        assert_eq!(full.kind, FloatKind::Fullscreen);
        assert_eq!(full.resume, Some(FloatKind::PopOut));
        let back = escape_float(Some(full)).unwrap();
        assert_eq!(back.kind, FloatKind::PopOut);
        assert!(escape_float(Some(back)).is_none());
        assert!(dock_float(Some(pop)).is_none());
        let pop = open_float(None, MobilePane::Chat, FloatKind::PopOut).unwrap();
        assert!(escape_float(Some(pop)).is_none());
        let full_direct = float_to_fullscreen(None, MobilePane::Chat).unwrap();
        assert!(escape_float(Some(full_direct)).is_none());
    }

    #[test]
    fn pane_float_frames_fit_narrow_and_wide_wells() {
        for (w, h) in [(400.0, 1000.0), (700.0, 1000.0), (1180.0, 1000.0)] {
            let well = Size { width: w, height: h };
            assert!(
                float_frame_fits(FloatKind::Fullscreen, well),
                "fullscreen must fit a {w}x{h} well, got {:?}",
                float_frame(FloatKind::Fullscreen, well)
            );
        }
    }

    #[test]
    fn popped_out_pane_leaves_the_dock_and_fullscreen_stays() {
        let pop = open_float(None, MobilePane::Actions, FloatKind::PopOut);
        assert!(!pane_stays_in_dock(pop, true), "popped Actions leaves the well");
        assert!(pane_stays_in_dock(pop, false), "Chat stays while Actions is popped out");
        let full = float_to_fullscreen(None, MobilePane::Actions);
        assert!(pane_stays_in_dock(full, true), "fullscreen Actions stays in the well");
        assert!(
            !pane_stays_in_dock(full, false),
            "Chat hides while Actions is fullscreen"
        );
    }

    #[test]
    fn action_turns_bind_survives_a_disposed_signal() {
        let out: Signal<Vec<ActionTurn>> = Signal::new(Vec::new());
        let next = vec![ActionTurn {
            id: "a1".into(),
            thinking: String::new(),
            steps: Vec::new(),
            todos: Vec::new(),
            live: false,
            session_id: "s1".into(),
            request_id: String::new(),
        }];
        match out.try_get() {
            Ok(cur) if cur != next => {
                let _ = out.try_set(next.clone());
            }
            _ => panic!("live signal should accept a write"),
        }
        assert_eq!(out.get(), next);
        out.dispose();
        let panicked = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            match out.try_get() {
                Ok(cur) if cur != next => {
                    let _ = out.try_set(next.clone());
                }
                _ => {}
            }
        }));
        assert!(panicked.is_ok(), "a disposed turns signal must not panic");
    }
}
