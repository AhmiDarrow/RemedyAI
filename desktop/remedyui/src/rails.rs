//! Left and right workspace rails around the chat well.
//!
//! Each side is a thin strip, an icon rail, or an icon rail plus a panel.
//! The icon rail stays on the outer edge. Width, slide, and mode persist
//! through [`crate::layout`]. A narrow window (≤ 760 px) shows one panel at
//! a time and leaves the saved layout alone.

use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;

use remedy_ui::a11y::{Action, Current, Role, Semantics};
use remedy_ui::core::{Color, Insets, Size};
use remedy_ui::input::{Buttons, Key, KeyEvent, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length, Position, AUTO};
use remedy_ui::reactive::{Signal, untrack};
use remedy_ui::style::{BorderEdge, BorderSide, Shadow, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::layout::{
    self, LayoutPatch, NARROW_MAX, RAIL_WIDTH_DEFAULT, RAIL_WIDTH_MAX, RAIL_WIDTH_MIN, RailMode, Side,
    SlideId, WorkspaceLayout, clamp_rail_width, layout_open_browser, layout_open_browser_beside_settings,
    layout_open_settings, layout_open_slide, layout_patch, layout_swap, layout_toggle_settings, normalize,
};
use crate::{messages, sidebar, studio::Studio};

const THIN_W: f32 = 12.0;
const RAIL_W: f32 = 36.0;
const HANDLE_W: f32 = 5.0;
const ICON_BTN: f32 = 32.0;
const HEADER_H: f32 = 36.0;

/// A slide pulled out of its rail. Not persisted: it is window state.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Popout {
    slide: SlideId,
    fullscreen: bool,
}

struct Built {
    key: (RailMode, SlideId, bool, bool),
    /// The panel body, so a width drag can resize it without remounting.
    body: Option<El>,
}

/// Live rails: layout signal, the temporary narrow side, and popout.
#[derive(Clone)]
pub struct WorkspaceChrome {
    layout: Signal<WorkspaceLayout>,
    /// Which side is temporarily open while the window is narrow.
    narrow_side: Signal<Option<Side>>,
    popout: Signal<Option<Popout>>,
    was_narrow: Rc<Cell<bool>>,
    previous: Rc<RefCell<WorkspaceLayout>>,
    /// `None` for previews, so goldens never write `~/.remedy`.
    persist: Option<PathBuf>,
}

impl WorkspaceChrome {
    /// Load the saved layout and write later changes back.
    pub fn load() -> Self {
        let dir = layout::layout_dir();
        let layout = layout::load_workspace_layout_from(&dir);
        Self::new(layout, Some(dir))
    }

    /// A fixed layout that is not written to disk.
    pub fn fixed(layout: WorkspaceLayout) -> Self {
        Self::new(normalize(layout), None)
    }

    fn new(layout: WorkspaceLayout, persist: Option<PathBuf>) -> Self {
        WorkspaceChrome {
            layout: Signal::new(layout),
            narrow_side: Signal::new(None),
            popout: Signal::new(None),
            was_narrow: Rc::new(Cell::new(false)),
            previous: Rc::new(RefCell::new(layout)),
            persist,
        }
    }

    /// The layout as saved, including while a narrow window is overriding it.
    pub fn layout(&self) -> WorkspaceLayout {
        self.layout.get()
    }

    fn commit(&self, layout: WorkspaceLayout) {
        let layout = normalize(layout);
        if self.layout.get() == layout {
            return;
        }
        self.layout.set(layout);
        if let Some(dir) = &self.persist {
            layout::save_workspace_layout_to(dir, &layout);
        }
    }

    fn patch(&self, patch: LayoutPatch) {
        let prev = self.layout.get();
        self.commit(layout_patch(&prev, &patch));
    }

    /// Swap slides, widths, and rail modes.
    pub fn swap(&self) {
        let prev = self.layout.get();
        self.commit(layout_swap(&prev));
    }

    /// Icon click. The open active tab collapses to icons. Otherwise the
    /// slide is stored on this side and the panel opens.
    pub fn select(&self, side: Side, id: SlideId) {
        let mode = self.display_mode(side);
        let layout = self.layout.get();
        if mode == RailMode::Open && layout.slide(side) == id {
            self.set_mode(side, RailMode::Icons);
            return;
        }
        let patch = match side {
            Side::Left => LayoutPatch { left: Some(id), ..LayoutPatch::default() },
            Side::Right => LayoutPatch { right: Some(id), ..LayoutPatch::default() },
        };
        self.commit(layout_patch(&layout, &patch));
        if mode != RailMode::Open {
            self.set_mode(side, RailMode::Open);
        }
    }

    /// Chevron: open becomes icons, icons becomes open.
    pub fn toggle_open(&self, side: Side) {
        let next = if self.display_mode(side) == RailMode::Open {
            RailMode::Icons
        } else {
            RailMode::Open
        };
        self.set_mode(side, next);
    }

    /// Header ✕ and the rail ✕. While narrow, thin is shown as icons.
    pub fn collapse_thin(&self, side: Side) {
        self.set_mode(side, RailMode::Thin);
    }

    /// What this side shows. Clicks use this; drawing uses the live window width.
    pub fn display_mode(&self, side: Side) -> RailMode {
        if self.was_narrow.get() {
            if self.narrow_side.get() == Some(side) {
                RailMode::Open
            } else {
                RailMode::Icons
            }
        } else {
            self.layout.get().rail(side)
        }
    }

    fn set_mode(&self, side: Side, mode: RailMode) {
        if self.was_narrow.get() {
            let next = if mode == RailMode::Open { Some(side) } else { None };
            self.narrow_side.set_if_changed(next);
            return;
        }
        let open = mode == RailMode::Open;
        let patch = match side {
            Side::Left => LayoutPatch {
                left_rail: Some(mode),
                left_open: Some(open),
                ..LayoutPatch::default()
            },
            Side::Right => LayoutPatch {
                right_rail: Some(mode),
                right_open: Some(open),
                ..LayoutPatch::default()
            },
        };
        self.patch(patch);
    }

    /// Drag, arrow keys, or a double-click reset.
    pub fn set_width(&self, side: Side, width: u32) {
        let width = clamp_rail_width(f64::from(width), width);
        if self.layout.get().width(side) == width {
            return;
        }
        let patch = match side {
            Side::Left => LayoutPatch { left_width: Some(width), ..LayoutPatch::default() },
            Side::Right => LayoutPatch { right_width: Some(width), ..LayoutPatch::default() },
        };
        self.patch(patch);
    }

    /// Status-bar Settings: open on the right, or collapse when it already is.
    pub fn toggle_settings(&self) {
        let prev = self.layout.get();
        self.commit(layout_toggle_settings(&prev));
    }

    /// Collapse every rail that is showing Settings. Used by Escape.
    pub fn collapse_settings(&self) {
        for side in [Side::Left, Side::Right] {
            if self.display_mode(side) == RailMode::Open && self.layout.get().slide(side) == SlideId::Settings {
                self.collapse_thin(side);
            }
        }
    }

    /// Command palette: Settings on the right, and a thin left rail becomes icons.
    pub fn open_settings(&self) {
        let prev = self.layout.get();
        self.commit(layout_open_settings(&prev));
    }

    /// Sessions on the left. Anything else on the right. Browser uses [`Self::open_browser`].
    pub fn open_slide(&self, id: SlideId) {
        let prev = self.layout.get();
        self.commit(layout_open_slide(&prev, id));
    }

    /// Browser on the right, never on both sides.
    pub fn open_browser(&self) {
        let prev = self.layout.get();
        self.commit(layout_open_browser(&prev));
    }

    /// OAuth: Browser beside Settings so Settings stays mounted.
    pub fn open_browser_beside_settings(&self) {
        let prev = self.layout.get();
        self.commit(layout_open_browser_beside_settings(&prev));
    }

    /// Pop Terminal, Browser, or Scratch out of the rail.
    pub fn pop_out(&self, side: Side, fullscreen: bool) {
        let slide = self.layout.get().slide(side);
        if !slide.popout() {
            return;
        }
        self.popout.set_if_changed(Some(Popout { slide, fullscreen }));
    }

    /// Put the slide back in its rail.
    pub fn close_popout(&self) {
        self.popout.set_if_changed(None);
    }

    /// Full window, or the floating frame.
    pub fn toggle_popout_fullscreen(&self) {
        let Some(pop) = self.popout.get() else { return };
        self.popout.set_if_changed(Some(Popout { fullscreen: !pop.fullscreen, ..pop }));
    }

    /// Viewport bookkeeping. `width` and `layout` are read by the caller so a
    /// bind can subscribe before this writes [`Self::narrow_side`].
    pub fn apply_narrow(&self, width: f32, layout: WorkspaceLayout) {
        let narrow = width > 0.0 && width <= NARROW_MAX;
        let crossed = narrow != self.was_narrow.get();
        if crossed {
            self.was_narrow.set(narrow);
            self.narrow_side.set_if_changed(None);
        }
        let old = *self.previous.borrow();
        if !crossed && narrow && old != layout {
            if layout.right_rail == RailMode::Open
                && (old.right_rail != RailMode::Open || old.right != layout.right)
            {
                self.narrow_side.set_if_changed(Some(Side::Right));
            } else if layout.left_rail == RailMode::Open
                && (old.left_rail != RailMode::Open || old.left != layout.left)
            {
                self.narrow_side.set_if_changed(Some(Side::Left));
            }
        }
        *self.previous.borrow_mut() = layout;
    }
}

/// One side of the shell. The structure is rebuilt when the mode, the slide,
/// or popout changes. A width drag only updates the pixel widths.
pub(crate) fn side(ui: &Ui, chrome: WorkspaceChrome, which: Side, studio: Studio) -> El {
    let root = ui.el("workspace_side").row().layout(|l| {
        l.height = Length::Percent(1.0);
        l.shrink = 0.0;
        l.grow = 0.0;
    });
    let built: Rc<RefCell<Option<Built>>> = Rc::new(RefCell::new(None));
    let ui_b = ui.clone();
    let chrome_b = chrome.clone();
    let root_b = root.clone();
    let studio_b = studio.clone();
    root.clone().bind(move || {
        let window_w = ui_b.window_size().width;
        let layout = chrome_b.layout.get();
        let narrow_side = chrome_b.narrow_side.get();
        let popped = chrome_b.popout.get();
        let mode = shown_mode(window_w, narrow_side, layout, which);
        let slide = layout.slide(which);
        let hide_body = popped.is_some_and(|p| p.slide == slide);
        let compact = which == Side::Left && crate::chrome::compact_now();
        let key = (mode, slide, hide_body, compact);
        let same = built.borrow().as_ref().is_some_and(|b| b.key == key);
        if !same {
            let built = built.clone();
            let ui = ui_b.clone();
            let root = root_b.clone();
            let chrome = chrome_b.clone();
            let studio = studio_b.clone();
            untrack(move || {
                if compact {
                    root.clear();
                    *built.borrow_mut() = Some(Built { key, body: None });
                } else {
                    let body = mount(&ui, &root, &chrome, which, mode, slide, hide_body, &studio);
                    *built.borrow_mut() = Some(Built { key, body });
                }
            });
        }
        let total = if compact { 0.0 } else { side_width(mode, layout.width(which)) };
        root_b.clone().layout(|l| {
            l.width = Length::Px(total);
            l.min_width = Length::Px(total);
            l.max_width = Length::Px(total);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        });
        if mode == RailMode::Open
            && let Some(body) = built.borrow().as_ref().and_then(|b| b.body.clone())
        {
            let w = layout.width(which) as f32;
            body.layout(|l| {
                l.width = Length::Px(w);
                l.min_width = Length::Px(w);
                l.max_width = Length::Px(w);
                l.height = Length::Percent(1.0);
                l.shrink = 0.0;
            });
        }
    });
    root
}

/// Narrow-viewport sync and the popout layer. Zero size, out of the flow.
pub(crate) fn hosts(ui: &Ui, chrome: WorkspaceChrome) -> El {
    let host = ui
        .el("rail_hosts")
        .layout(|l| {
            l.position = Position::Absolute;
            l.width = Length::Px(0.0);
            l.height = Length::Px(0.0);
        })
        .semantics(Semantics::new(Role::Group).hidden(true));
    narrow_watch(ui, &chrome, &host);
    popout_host(ui, &chrome, &host);
    host
}

fn narrow_watch(ui: &Ui, chrome: &WorkspaceChrome, host: &El) {
    let ui = ui.clone();
    let chrome = chrome.clone();
    host.clone().bind(move || {
        let width = ui.window_size().width;
        let layout = chrome.layout.get();
        untrack(|| chrome.apply_narrow(width, layout));
    });
}

fn popout_host(ui: &Ui, chrome: &WorkspaceChrome, host: &El) {
    let slot: Rc<RefCell<Option<El>>> = Rc::new(RefCell::new(None));
    let ui = ui.clone();
    let chrome = chrome.clone();
    let slot_gone = slot.clone();
    host.clone().on_removed(move || {
        if let Some(el) = slot_gone.borrow_mut().take() {
            el.remove();
        }
    });
    host.clone().bind(move || {
        let pop = chrome.popout.get();
        let size = ui.window_size();
        let slot = slot.clone();
        let ui = ui.clone();
        let chrome = chrome.clone();
        untrack(move || {
            if let Some(old) = slot.borrow_mut().take() {
                old.remove();
            }
            let Some(pop) = pop else { return };
            let el = popout_layer(&ui, &chrome, pop, size);
            ui.overlay(el.clone());
            el.focus();
            *slot.borrow_mut() = Some(el);
        });
    });
}

fn shown_mode(window_w: f32, narrow_side: Option<Side>, layout: WorkspaceLayout, side: Side) -> RailMode {
    let narrow = window_w > 0.0 && window_w <= NARROW_MAX;
    if narrow {
        if narrow_side == Some(side) { RailMode::Open } else { RailMode::Icons }
    } else {
        layout.rail(side)
    }
}

fn side_width(mode: RailMode, body: u32) -> f32 {
    match mode {
        RailMode::Thin => THIN_W,
        RailMode::Icons => RAIL_W,
        RailMode::Open => body as f32 + RAIL_W + HANDLE_W,
    }
}

fn mount(
    ui: &Ui,
    root: &El,
    chrome: &WorkspaceChrome,
    side: Side,
    mode: RailMode,
    slide: SlideId,
    hide_body: bool,
    studio: &Studio,
) -> Option<El> {
    root.clear();
    match mode {
        RailMode::Thin => {
            let _ = root.clone().child(thin_strip(ui, chrome, side));
            None
        }
        RailMode::Icons => {
            let _ = root.clone().child(icon_rail(ui, chrome, side, false));
            None
        }
        RailMode::Open => {
            let body = panel(ui, chrome, side, slide, hide_body, studio);
            let handle = resize_handle(ui, chrome, side);
            let rail = icon_rail(ui, chrome, side, true);
            if side == Side::Left {
                let _ = root.clone().child(rail).child(body.clone()).child(handle);
            } else {
                let _ = root.clone().child(handle).child(body.clone()).child(rail);
            }
            Some(body)
        }
    }
}

fn thin_strip(ui: &Ui, chrome: &WorkspaceChrome, side: Side) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let label_id = if side == Side::Left { "rail-expand-left" } else { "rail-expand-right" };
    let edge = if side == Side::Left { BorderEdge::End } else { BorderEdge::Start };
    let glyph = if side == Side::Left { w::Icon::ChevronRight } else { w::Icon::ChevronLeft };
    let chrome = chrome.clone();
    let u = ui.clone();
    let btn = ui
        .el("thin_rail")
        .column()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.width = Length::Px(THIN_W);
            l.min_width = Length::Px(THIN_W);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.card)
                .foreground(colors.muted.with_alpha(0.55))
                .border_side(edge, BorderSide::new(1.0, line)),
        )
        .child(w::icon(ui, glyph, 10.0))
        .on_click(move || chrome.set_mode(side, RailMode::Icons))
        .semantics({
            let u = u.clone();
            move || Semantics::new(Role::Button).label(messages().text(&u, label_id)).action(Action::Click)
        });
    w::tooltip(&btn, {
        let u = ui.clone();
        move || messages().text(&u, label_id)
    });
    btn
}

fn icon_rail(ui: &Ui, chrome: &WorkspaceChrome, side: Side, open: bool) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let edge = if side == Side::Left { BorderEdge::End } else { BorderEdge::Start };
    let label_id = if side == Side::Left { "rail-left" } else { "rail-right" };
    let rail_bg = colors.card.mix_oklab(colors.bg, 0.08);
    let mut rail = ui
        .el("slide_rail")
        .column()
        .align(Align::Center)
        .gap(3.0)
        .padding(Insets { top: 6.0, end: 0.0, bottom: 6.0, start: 0.0 })
        .layout(|l| {
            l.width = Length::Px(RAIL_W);
            l.min_width = Length::Px(RAIL_W);
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(rail_bg)
                .border_side(edge, BorderSide::new(1.0, line.with_alpha(0.85))),
        )
        .semantics({
            let u = ui.clone();
            move || Semantics::new(Role::Toolbar).label(messages().text(&u, label_id))
        })
        .child(chevron_button(ui, chrome, side, open));

    let active = chrome.layout.get().slide(side);
    for id in layout::ALL_SLIDES {
        rail = rail.child(slide_button(ui, chrome, side, id, id == active, open));
        if id == SlideId::Settings {
            rail = rail.child(rail_divider(ui, &colors));
        }
    }
    rail.child(w::spacer(ui)).child(collapse_button(ui, chrome, side))
}

fn chevron_button(ui: &Ui, chrome: &WorkspaceChrome, side: Side, open: bool) -> El {
    let colors = ui.theme().colors.clone();
    let glyph = match (side, open) {
        (Side::Left, true) | (Side::Right, false) => w::Icon::ChevronLeft,
        (Side::Left, false) | (Side::Right, true) => w::Icon::ChevronRight,
    };
    let label_id = if open { "rail-collapse-panel" } else { "rail-open-panel" };
    let title_id = if open { "rail-collapse-panel-title" } else { "rail-open-panel" };
    let chrome = chrome.clone();
    icon_btn(
        ui,
        glyph,
        12.0,
        ICON_BTN,
        msg(ui, label_id),
        msg(ui, title_id),
        quiet_style(&colors),
        false,
        move || chrome.toggle_open(side),
    )
}

fn slide_button(ui: &Ui, chrome: &WorkspaceChrome, side: Side, id: SlideId, on: bool, open: bool) -> El {
    let colors = ui.theme().colors.clone();
    let name = msg(ui, id.label_id());
    let chrome = chrome.clone();
    icon_btn(
        ui,
        slide_icon(id),
        15.0,
        ICON_BTN,
        name.clone(),
        name,
        mark_style(&colors, on, open),
        on && open,
        move || chrome.select(side, id),
    )
}

fn collapse_button(ui: &Ui, chrome: &WorkspaceChrome, side: Side) -> El {
    let colors = ui.theme().colors.clone();
    let name = msg(ui, "rail-collapse");
    let chrome = chrome.clone();
    icon_btn(
        ui,
        w::Icon::Close,
        12.0,
        ICON_BTN,
        name.clone(),
        name,
        quiet_style(&colors),
        false,
        move || chrome.collapse_thin(side),
    )
}

fn rail_divider(ui: &Ui, colors: &remedy_ui::style::ColorRoles) -> El {
    let line = hairline(colors).with_alpha(0.8);
    ui.el("rail_rule")
        .layout(|l| {
            l.width = Length::Px(20.0);
            l.height = Length::Px(1.0);
            l.margin = Insets { top: 4.0, end: 0.0, bottom: 4.0, start: 0.0 };
            l.shrink = 0.0;
        })
        .style(StylePatch::new().background(line))
}

fn panel(
    ui: &Ui,
    chrome: &WorkspaceChrome,
    side: Side,
    slide: SlideId,
    hide_body: bool,
    studio: &Studio,
) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors).with_alpha(0.8);
    let mut body = ui
        .el("panel_body")
        .column()
        .layout(|l| {
            l.height = Length::Percent(1.0);
            l.shrink = 0.0;
        })
        .style(StylePatch::new().background(colors.panel))
        .clip()
        .child(swap_header(ui, chrome, side, slide, &colors, line));
    if !hide_body {
        body = body.child(slide_body(ui, studio, slide));
    }
    body
}

fn swap_header(
    ui: &Ui,
    chrome: &WorkspaceChrome,
    side: Side,
    slide: SlideId,
    colors: &remedy_ui::style::ColorRoles,
    line: Color,
) -> El {
    let title = msg(ui, slide.label_id());
    let mut header = w::row(ui)
        .align(Align::Center)
        .gap(2.0)
        .padding(Insets { top: 0.0, end: 8.0, bottom: 0.0, start: 8.0 })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Px(HEADER_H);
            l.min_height = Length::Px(HEADER_H);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel.mix_oklab(colors.bg, 0.10))
                .border_side(BorderEdge::Bottom, BorderSide::new(1.0, line)),
        )
        .child(
            w::text(ui, title)
                .variant("strong")
                .nowrap()
                .truncate()
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child(swap_button(ui, chrome, colors));
    if slide.popout() {
        let pop_name = msg(ui, "rail-popout");
        let full_name = msg(ui, "rail-fullscreen");
        let pop = chrome.clone();
        let full = chrome.clone();
        header = header
            .child(icon_btn(
                ui,
                w::Icon::External,
                13.0,
                24.0,
                pop_name.clone(),
                pop_name,
                quiet_style(colors),
                false,
                move || pop.pop_out(side, false),
            ))
            .child(icon_btn(
                ui,
                w::Icon::Fullscreen,
                13.0,
                24.0,
                full_name.clone(),
                full_name,
                quiet_style(colors),
                false,
                move || full.pop_out(side, true),
            ));
    }
    let close_name = msg(ui, "rail-collapse");
    let close = chrome.clone();
    header.child(icon_btn(
        ui,
        w::Icon::Close,
        12.0,
        24.0,
        close_name.clone(),
        close_name,
        quiet_style(colors),
        false,
        move || close.collapse_thin(side),
    ))
}

fn swap_button(ui: &Ui, chrome: &WorkspaceChrome, colors: &remedy_ui::style::ColorRoles) -> El {
    let name = msg(ui, "rail-swap");
    let chrome = chrome.clone();
    let btn = ui
        .el("icon_button")
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Px(24.0);
            l.height = Length::Px(24.0);
            l.min_width = Length::Px(24.0);
            l.min_height = Length::Px(24.0);
            l.shrink = 0.0;
        })
        .style(quiet_style(colors))
        .child(w::icon(ui, w::Icon::ChevronLeft, 10.0))
        .child(w::icon(ui, w::Icon::ChevronRight, 10.0))
        .on_click(move || chrome.swap())
        .semantics(Semantics::new(Role::Button).label(name.clone()).action(Action::Click));
    w::tooltip(&btn, name);
    btn
}

/// Escape while Studio is showing. A search query clears first. Otherwise an
/// open Settings rail collapses to thin. False when Settings is not up, so a
/// popout dialog can take Escape.
pub(crate) fn dismiss_settings(chrome: &WorkspaceChrome) -> bool {
    let open = [Side::Left, Side::Right].into_iter().any(|side| {
        chrome.display_mode(side) == RailMode::Open && chrome.layout().slide(side) == SlideId::Settings
    });
    if !open {
        return false;
    }
    if let Some(s) = crate::settings::current()
        && crate::settings::searching(&s)
    {
        crate::settings::clear_search(s);
        return true;
    }
    chrome.collapse_settings();
    true
}

fn slide_body(ui: &Ui, studio: &Studio, slide: SlideId) -> El {
    let inner = if slide == SlideId::Sessions {
        sidebar::sidebar(ui, studio.clone())
    } else if slide == SlideId::Settings && !studio.local_only {
        settings_slide(ui)
    } else if let Some(body) = crate::slides::rail_body(ui, slide) {
        body
    } else {
        blurb(ui, slide)
    };
    // The header keeps its 36 px. This takes the rest, so the session list
    // does not stack on top of the header.
    ui.el("slide_fill")
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
        })
        .child(inner)
}

fn blurb(ui: &Ui, slide: SlideId) -> El {
    let u = ui.clone();
    let id = slide.blurb_id();
    w::column(ui)
        .padding(Insets { top: 16.0, end: 16.0, bottom: 16.0, start: 16.0 })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
        })
        .child(w::text(ui, move || messages().text(&u, id)).variant("muted"))
}

/// Live Settings. Previews keep [`blurb`] so goldens stay about the chrome
/// and do not read or write config.
fn settings_slide(ui: &Ui) -> El {
    let Some(s) = crate::settings::current() else {
        return blurb(ui, SlideId::Settings);
    };
    crate::settings::open_for_rail(s);
    crate::settings::embedded(ui, s).on_removed(move || crate::settings::close_for_rail(s))
}

fn resize_handle(ui: &Ui, chrome: &WorkspaceChrome, side: Side) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors).with_alpha(0.7);
    let drag: Rc<Cell<Option<(f32, u32)>>> = Rc::new(Cell::new(None));
    let start = drag.clone();
    let move_drag = drag.clone();
    let end_drag = drag.clone();
    let ui_ptr = ui.clone();
    let chrome_down = chrome.clone();
    let chrome_move = chrome.clone();
    let chrome_key = chrome.clone();
    let label_id = if side == Side::Left { "rail-resize-left" } else { "rail-resize-right" };
    let handle = ui
        .el("resize_handle")
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
            ui.el("resize_hairline")
                .layout(|l| {
                    l.width = Length::Px(1.0);
                    l.height = Length::Percent(1.0);
                })
                .style(StylePatch::new().background(line)),
        )
        .focusable(true)
        .on_pointer(move |e| match e.phase {
            PointerPhase::Down if e.buttons.contains(Buttons::PRIMARY) => {
                if ui_ptr.click_count() == 2 {
                    start.set(None);
                    chrome_down.set_width(side, RAIL_WIDTH_DEFAULT);
                } else {
                    let width = chrome_down.layout().width(side);
                    start.set(Some((e.position.x, width)));
                }
                true
            }
            PointerPhase::Move => match move_drag.get() {
                Some((x0, w0)) => {
                    let dx = if side == Side::Left { e.position.x - x0 } else { x0 - e.position.x };
                    let next = f64::from(w0) + f64::from(dx);
                    chrome_move.set_width(side, clamp_rail_width(next, w0));
                    true
                }
                None => false,
            },
            PointerPhase::Up | PointerPhase::Cancel => end_drag.take().is_some(),
            _ => false,
        })
        .on_key(move |e| resize_key(e, &chrome_key, side))
        .semantics({
            let chrome = chrome.clone();
            let ui = ui.clone();
            move || {
                let w = f64::from(chrome.layout().width(side));
                Semantics::new(Role::Separator)
                    .label(messages().text(&ui, label_id))
                    .description(messages().text(&ui, "rail-resize-hint"))
                    .numeric(w, f64::from(RAIL_WIDTH_MIN), f64::from(RAIL_WIDTH_MAX))
            }
        });
    w::tooltip(&handle, {
        let ui = ui.clone();
        move || messages().text(&ui, "rail-resize-hint")
    });
    handle
}

fn resize_key(e: &KeyEvent, chrome: &WorkspaceChrome, side: Side) -> bool {
    if e.composing || !e.pressed {
        return false;
    }
    let current = chrome.layout().width(side) as i32;
    let step: i32 = if e.modifiers.shift { 40 } else { 10 };
    let next = match e.key {
        Key::Home => RAIL_WIDTH_MIN as i32,
        Key::End => RAIL_WIDTH_MAX as i32,
        Key::ArrowLeft => current + if side == Side::Left { -step } else { step },
        Key::ArrowRight => current + if side == Side::Left { step } else { -step },
        _ => return false,
    };
    chrome.set_width(side, clamp_rail_width(f64::from(next), current as u32));
    true
}

fn popout_layer(ui: &Ui, chrome: &WorkspaceChrome, pop: Popout, size: Size) -> El {
    let colors = ui.theme().colors.clone();
    let line = hairline(&colors);
    let title = msg(ui, pop.slide.label_id());
    let mut layer = ui
        .el("popout")
        .column()
        .clip()
        .focusable(true)
        .layout(|l| {
            l.position = Position::Absolute;
            if pop.fullscreen || size.width <= 0.0 || size.height <= 0.0 {
                l.inset = Insets::ZERO;
                l.width = Length::Percent(1.0);
                l.height = Length::Percent(1.0);
            } else {
                l.inset = Insets {
                    top: size.height * 0.08,
                    start: size.width * 0.12,
                    end: AUTO,
                    bottom: AUTO,
                };
                l.width = Length::Px(size.width * 0.76);
                l.height = Length::Px(size.height * 0.80);
            }
        })
        .style({
            let mut s = StylePatch::new().background(colors.bg).foreground(colors.text);
            if pop.fullscreen {
                s = s.radius(0.0);
            } else {
                s = s
                    .radius(12.0)
                    .border(line)
                    .border_width(1.0)
                    .shadow(Shadow::new(0.0, 24.0, 64.0, Color::rgba8(0, 0, 0, 115)));
            }
            s
        })
        .semantics(Semantics::new(Role::Dialog).label(title.clone()).modal(true))
        .on_key({
            let chrome = chrome.clone();
            move |e| {
                if e.composing || !e.pressed || e.key != Key::Escape {
                    return false;
                }
                if chrome.popout.get().is_some_and(|p| p.fullscreen) {
                    chrome.toggle_popout_fullscreen();
                } else {
                    chrome.close_popout();
                }
                true
            }
        });
    layer = layer.child(popout_chrome(ui, chrome, pop, &title, &colors, line));
    layer.child(slide_body(ui, &Studio::preview(), pop.slide))
}

fn popout_chrome(
    ui: &Ui,
    chrome: &WorkspaceChrome,
    pop: Popout,
    title: &str,
    colors: &remedy_ui::style::ColorRoles,
    line: Color,
) -> El {
    let hint = msg(ui, if pop.fullscreen { "popout-esc-exit" } else { "popout-esc-close" });
    let full_label = msg(ui, if pop.fullscreen { "popout-exit-fullscreen" } else { "popout-fullscreen" });
    let close_title = msg(ui, "popout-close-title");
    let close_label = msg(ui, "popout-close");
    let toggle = chrome.clone();
    let close = chrome.clone();
    w::row(ui)
        .align(Align::Center)
        .gap(8.0)
        .padding(Insets { top: 0.0, end: 12.0, bottom: 0.0, start: 12.0 })
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Px(44.0);
            l.min_height = Length::Px(44.0);
            l.shrink = 0.0;
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .border_side(BorderEdge::Bottom, BorderSide::new(1.0, line)),
        )
        .child(
            w::text(ui, title.to_string())
                .variant("strong")
                .nowrap()
                .truncate()
                .layout(|l| {
                    l.grow = 1.0;
                    l.shrink = 1.0;
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child(w::text(ui, hint).variant("muted"))
        .child(text_btn(ui, &full_label, &full_label, colors.text, colors.card, colors.line, move || {
            toggle.toggle_popout_fullscreen();
        }))
        .child(text_btn(ui, &close_label, &close_title, Color::WHITE, colors.danger, None, move || {
            close.close_popout();
        }))
}

fn text_btn(
    ui: &Ui,
    label: &str,
    title: &str,
    fg: Color,
    bg: Color,
    border: Option<Color>,
    on_press: impl Fn() + 'static,
) -> El {
    let mut style = StylePatch::new().background(bg).foreground(fg).radius(6.0);
    if let Some(c) = border {
        style = style.border(c).border_width(1.0);
    }
    let btn = ui
        .el("button")
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(Insets { top: 6.0, end: 12.0, bottom: 6.0, start: 12.0 })
        .style(style)
        .child(w::text(ui, label.to_string()))
        .on_click(on_press)
        .semantics(Semantics::new(Role::Button).label(title.to_string()).action(Action::Click));
    w::tooltip(&btn, title.to_string());
    btn
}

fn slide_icon(id: SlideId) -> w::Icon {
    match id {
        SlideId::Sessions => w::Icon::Menu,
        SlideId::Settings => w::Icon::Settings,
        SlideId::Files => w::Icon::Folder,
        SlideId::Terminal => w::Icon::Code,
        SlideId::Browser => w::Icon::Monitor,
        SlideId::Scratch => w::Icon::Note,
        SlideId::Automations => w::Icon::ListTask,
    }
}

fn icon_btn(
    ui: &Ui,
    glyph: w::Icon,
    glyph_size: f32,
    box_size: f32,
    label: String,
    title: String,
    style: StylePatch,
    current: bool,
    on_press: impl Fn() + 'static,
) -> El {
    let mut sem = Semantics::new(Role::Button).label(label).action(Action::Click);
    if current {
        sem = sem.selected(true).current(Current::Page);
    }
    let btn = ui
        .el("icon_button")
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .layout(|l| {
            l.width = Length::Px(box_size);
            l.height = Length::Px(box_size);
            l.min_width = Length::Px(box_size);
            l.min_height = Length::Px(box_size);
            l.shrink = 0.0;
        })
        .style(style)
        .child(w::icon(ui, glyph, glyph_size))
        .on_click(on_press)
        .semantics(sem);
    w::tooltip(&btn, title);
    btn
}

fn quiet_style(colors: &remedy_ui::style::ColorRoles) -> StylePatch {
    StylePatch::new()
        .background(Color::TRANSPARENT)
        .foreground(colors.muted)
        .border(Color::TRANSPARENT)
        .border_width(1.0)
        .radius(8.0)
}

fn mark_style(colors: &remedy_ui::style::ColorRoles, current: bool, open: bool) -> StylePatch {
    if current && open {
        StylePatch::new()
            .background(colors.accent.mix_oklab(colors.panel, 0.78))
            .foreground(colors.accent)
            .border(colors.accent.with_alpha(0.40))
            .border_width(1.0)
            .radius(8.0)
    } else if current {
        StylePatch::new()
            .background(colors.accent.with_alpha(0.10))
            .foreground(colors.accent)
            .border(colors.accent.with_alpha(0.22))
            .border_width(1.0)
            .radius(8.0)
    } else {
        quiet_style(colors)
    }
}

fn hairline(colors: &remedy_ui::style::ColorRoles) -> Color {
    colors.line.unwrap_or(colors.muted)
}

fn msg(ui: &Ui, id: &'static str) -> String {
    messages().text(ui, id)
}
