//! Two-pane Studio chat: Coding/Actions beside Chat, as in the current app
//! (`remedy.chatSplit.v1`).
//!
//! The split is a layout over existing chat-depth data: thinking, tool steps,
//! and BUILD todos already live on the session. Nothing new is fetched here.
//! Side and width persist under `~/.remedy/ui/chat-split.json`. Tests that set
//! `REMEDY_DESKTOP_NO_SPAWN` keep the defaults and never touch disk.

use std::cell::Cell;
use std::fs;
use std::path::PathBuf;
use std::rc::Rc;

use serde::{Deserialize, Serialize};

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::input::{Buttons, Key, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use crate::depth::TodoItem;
use crate::runtime::{self, spawn_disabled};
use crate::studio::{ChatMessage, Studio};
use crate::tokens;
use crate::work::{StepStatus, WorkStep};
use crate::{messages, say};

/// Matches `--chat-bubble-radius: 0.85rem` in the current app (at 16px).
pub const PANE_RADIUS: f32 = 2.0;
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
    let unit = ui.theme().spacing.unit;

    let n_out = narrow.clone();
    let root = w::column(ui)
        .layout(|l| {
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

    let panes = panes_row(ui, studio, prefs.clone(), mobile, narrow.clone());

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
                let mut p = StylePatch::new().radius(8.0).text_size(12.0).weight(600.0);
                if active() {
                    p = p
                        .background(colors.accent.with_alpha(0.16))
                        .foreground(colors.text)
                        .border(colors.accent.with_alpha(0.4))
                        .border_width(1.0);
                } else {
                    p = p.foreground(colors.muted);
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
) -> El {
    let ui = ui.clone();
    let actions = pane_shell(
        &ui,
        studio.clone(),
        prefs.clone(),
        mobile.clone(),
        narrow.clone(),
        true,
    );
    let chat = pane_shell(&ui, studio, prefs.clone(), mobile, narrow.clone(), false);
    let handle = resize_handle(&ui, prefs.clone(), narrow);

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
    is_actions: bool,
) -> El {
    let colors = ui.theme().colors.clone();
    let visible = {
        let n = narrow.clone();
        let m = mobile;
        move || {
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
    let shell = show(ui, visible, move || {
        let pane = if is_actions {
            actions_column(&u, st.clone(), pr.clone(), n.get())
        } else {
            chat_column(&u, st.clone(), pr.clone(), n.get())
        };
        pane.style(
            StylePatch::new()
                .background(tokens::pane_fill(&colors))
                .border(tokens::hairline(&colors))
                .border_width(1.0)
                .radius(PANE_RADIUS),
        )
        .clip()
    })
    .el();
    let shell_b = shell.clone();
    let n_layout = narrow;
    shell.bind(move || {
        let stacked = n_layout.get();
        let _ = shell_b.clone().layout(move |l| {
            l.min_width = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            if stacked || !is_actions {
                // Chat (and both panes when stacked) fills leftover well width
                // when the side rails collapse.
                l.grow = 1.0;
                l.shrink = 1.0;
                l.basis = Length::Px(0.0);
                l.width = Length::Percent(1.0);
            } else {
                l.grow = 0.0;
                l.shrink = 0.0;
            }
        });
    })
}

fn actions_column(ui: &Ui, studio: Studio, prefs: Signal<ChatSplit>, stacked: bool) -> El {
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
                l.basis = Length::Px(width());
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
        ))
        .child(actions_feed(ui, studio).layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        }))
}

fn chat_column(ui: &Ui, studio: Studio, prefs: Signal<ChatSplit>, stacked: bool) -> El {
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
) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let titles = w::column(ui)
        .gap(1.0)
        .layout(|l| {
            l.grow = 1.0;
            l.shrink = 1.0;
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
            top: 10.0,
            end: tokens::GUTTER,
            bottom: 10.0,
            start: tokens::GUTTER,
        })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().border_side(
            BorderEdge::Bottom,
            BorderSide::new(1.0, line.with_alpha(0.4)),
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
    if !stacked {
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

fn resize_handle(ui: &Ui, prefs: Signal<ChatSplit>, narrow: Signal<bool>) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted).with_alpha(0.7);
    let u_show = ui.clone();
    let p_show = prefs;
    let n = narrow;
    show(
        ui,
        move || !n.get(),
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
                    l.width = Length::Px(1.0);
                    l.height = Length::Percent(1.0);
                })
                .style(StylePatch::new().background(line).radius(1.0)),
        )
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
    let unit = ui.theme().spacing.unit;
    let turns: Signal<Vec<ActionTurn>> = Signal::new(Vec::new());
    let out = turns.clone();
    let st = studio.clone();
    let col = w::column(ui)
        .gap(unit * 3.0)
        .padding(Insets {
            top: 12.0,
            end: tokens::GUTTER,
            bottom: tokens::GUTTER,
            start: tokens::GUTTER,
        })
        .layout(|l| l.width = Length::Percent(1.0));
    let _ = col.clone().bind(move || {
        let next = collect_turns(&st);
        if out.get() != next {
            out.set(next);
        }
    });
    let collapsed: Signal<Vec<String>> = Signal::new(Vec::new());
    let u = ui.clone();
    let st_rows = studio;
    let col_state = collapsed;
    let col = w::for_each(
        &col,
        turns,
        |t| t.id.clone(),
        move |t| action_turn(&u, st_rows.clone(), t.clone(), col_state.clone()),
    );
    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(col)
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ActionTurn {
    id: String,
    thinking: String,
    steps: Vec<WorkStep>,
    todos: Vec<TodoItem>,
    live: bool,
}

fn collect_turns(studio: &Studio) -> Vec<ActionTurn> {
    let streaming = studio.streaming.get();
    let live_id = studio.live_message_id.get();
    let mut out = Vec::new();
    for m in studio.messages.get() {
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
        });
    }
    out
}

fn skip_idle(m: &ChatMessage) -> bool {
    m.thinking.trim().is_empty() && m.steps.is_empty()
}

fn action_turn(ui: &Ui, studio: Studio, turn: ActionTurn, collapsed: Signal<Vec<String>>) -> El {
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
        col = col.child(thinking_block(ui, &turn.id, &thinking, collapsed));
    }
    if !steps.is_empty() {
        col = col.child(tools_block(ui, &steps));
    }
    if !todos.is_empty() {
        col = col.child(build_block(ui, &todos));
    }
    let _ = studio;
    col
}

fn thinking_block(ui: &Ui, id: &str, thinking: &str, collapsed: Signal<Vec<String>>) -> El {
    let colors = ui.theme().colors.clone();
    let chunks = thinking_chunks(thinking);
    let open = {
        let collapsed = collapsed.clone();
        let id = id.to_string();
        move || !collapsed.get().iter().any(|x| x == &id)
    };
    let toggle = {
        let collapsed = collapsed.clone();
        let id = id.to_string();
        move || {
            let mut next = collapsed.get();
            if let Some(i) = next.iter().position(|x| x == &id) {
                next.remove(i);
            } else {
                next.push(id.clone());
            }
            collapsed.set(next);
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

fn tools_block(ui: &Ui, steps: &[WorkStep]) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit * 0.6)
        .layout(|l| l.width = Length::Percent(1.0));
    for step in steps {
        col = col.child(tool_row(ui, step));
    }
    col
}

fn tool_row(ui: &Ui, step: &WorkStep) -> El {
    let colors = ui.theme().colors.clone();
    let (mark, mark_color) = match step.status {
        StepStatus::Running => ("◌", colors.accent),
        StepStatus::Done => ("✓", colors.ok),
        StepStatus::Error => ("✕", colors.danger),
    };
    let name = short_tool(&step.name);
    let detail = step.detail.clone();
    let mut label = format!("{mark} {name}");
    if let Some(d) = &detail {
        label.push(' ');
        label.push_str(d);
    }
    let mut row = w::row(ui)
        .align(Align::Center)
        .gap(8.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .semantics({
            let label = label.clone();
            let err = step.error.clone();
            move || {
                let mut s = label.clone();
                if let Some(e) = &err {
                    if !e.is_empty() {
                        s.push_str(": ");
                        s.push_str(e);
                    }
                }
                Semantics::new(Role::Label).label(s)
            }
        })
        .child(
            w::text(ui, mark).style(
                tokens::typo(tokens::MONO)
                    .foreground(mark_color)
                    .weight(650.0),
            ),
        )
        .child(
            w::text(ui, &name).style(
                tokens::typo(tokens::MONO)
                    .foreground(colors.text)
                    .weight(600.0),
            ),
        );
    if let Some(detail) = detail.as_deref().filter(|s| !s.is_empty()) {
        row = row.child(
            w::text(ui, detail)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::typo(tokens::MONO).foreground(colors.muted)),
        );
    }
    if let Some(err) = step.error.as_deref().filter(|s| !s.is_empty()) {
        row = row.child(
            w::text(ui, err)
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                })
                .style(tokens::typo(tokens::META).foreground(colors.danger)),
        );
    }
    row
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
            top: 10.0,
            end: 12.0,
            bottom: 10.0,
            start: 12.0,
        })
        .layout(|l| l.width = Length::Percent(1.0))
        .style(
            StylePatch::new()
                .background(tokens::surface_overlay(&colors).mix_oklab(colors.bg, 0.12))
                .border(tokens::hairline(&colors))
                .border_width(1.0)
                .radius(ui.theme().radii.medium),
        )
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
}
