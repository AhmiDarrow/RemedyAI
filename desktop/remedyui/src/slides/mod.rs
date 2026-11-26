//! Studio rail slides: Files, Terminal, Browser hook, Scratch, Automations.
//! Rail layout and pop-out chrome live in [`crate::rails`]. These bodies fill
//! that panel. Terminal, Browser, and Scratch leave the rail through its header.

mod api;
mod automations;
mod chrome;
mod files;
mod hostcmd;
mod nav;
mod paths;
mod pty;
mod scratch;
mod terminal;

pub use hostcmd::{bind_host, current_app, current_window, pick_folder};
pub use terminal::terminal_context_items;

use crate::runtime::Client;
use crate::studio::Studio;
use automations::AutomationsModel;
use files::FilesModel;
use remedy_ui::layout::Length;
use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use scratch::ScratchModel;
use std::cell::RefCell;
use terminal::TerminalModel;

/// Slides this port owns. Sessions and Settings stay where they already live.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SlideId {
    Files,
    Terminal,
    Browser,
    Scratch,
    Automations,
}

impl SlideId {
    pub const ALL: [SlideId; 5] = [
        SlideId::Files,
        SlideId::Terminal,
        SlideId::Browser,
        SlideId::Scratch,
        SlideId::Automations,
    ];
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SlideMeta {
    pub label: &'static str,
    pub short: &'static str,
    pub popout: bool,
}

pub fn slide_meta(id: SlideId) -> SlideMeta {
    match id {
        SlideId::Files => SlideMeta { label: "Files", short: "📁", popout: false },
        SlideId::Terminal => SlideMeta { label: "Terminal", short: ">_", popout: true },
        SlideId::Browser => SlideMeta { label: "Browser", short: "🌐", popout: true },
        SlideId::Scratch => SlideMeta { label: "Scratch", short: "✎", popout: true },
        SlideId::Automations => SlideMeta { label: "Automations", short: "⚡", popout: false },
    }
}

/// Open rail slide, pop-out, and the models those bodies keep.
#[derive(Clone)]
pub struct Workspace {
    pub open: Signal<Option<SlideId>>,
    pub popout: Signal<Option<SlideId>>,
    pub fullscreen: Signal<bool>,
    pub session_id: Signal<Option<String>>,
    pub project_path: Signal<String>,
    pub client: Signal<Option<Client>>,
    pub preview: bool,
    pub files: FilesModel,
    pub scratch: ScratchModel,
    pub automations: AutomationsModel,
    pub terminal: TerminalModel,
}

impl Workspace {
    /// Live shell. The rail chooses which body is on screen.
    pub fn live(client: Client) -> Self {
        Self::build(Signal::new(Some(client)), false, None)
    }

    /// Preview fixtures when the shell is local-only, otherwise the Studio client.
    pub fn for_studio(studio: &Studio) -> Self {
        if studio.local_only {
            let ws = Self::build(Signal::new(None), true, None);
            ws.project_path.set("/home/ahmi/RemedyAI".into());
            ws.session_id.set(Some("s3".into()));
            ws
        } else {
            Self::build(Signal::new(studio.runtime()), false, None)
        }
    }

    /// One slide, filled with the fixture the goldens draw.
    pub fn preview_slide(id: SlideId) -> Self {
        let ws = Self::build(Signal::new(None), true, Some(id));
        ws.project_path.set("/home/ahmi/RemedyAI".into());
        ws.session_id.set(Some("s3".into()));
        ws
    }

    fn build(client: Signal<Option<Client>>, preview: bool, open: Option<SlideId>) -> Self {
        let session_id = Signal::new(None);
        let project_path = Signal::new(String::new());
        Self {
            open: Signal::new(open),
            popout: Signal::new(None),
            fullscreen: Signal::new(false),
            session_id: session_id.clone(),
            project_path: project_path.clone(),
            client: client.clone(),
            preview,
            files: FilesModel::new(session_id.clone(), client.clone(), preview),
            scratch: ScratchModel::new(session_id.clone(), client.clone(), preview),
            automations: AutomationsModel::new(client.clone(), preview),
            terminal: TerminalModel::new(session_id, client, project_path, preview),
        }
    }

    /// Keep the slides on the Studio's active session and its project folder.
    pub fn follow(&self, host: &El, studio: &Studio) {
        let ws = self.clone();
        let studio = studio.clone();
        let _ = host.clone().bind(move || {
            let sid = studio.active.get();
            let path = studio
                .sessions
                .get()
                .into_iter()
                .find(|s| Some(&s.id) == sid.as_ref())
                .and_then(|s| s.project_path)
                .unwrap_or_default();
            if ws.session_id.get() != sid {
                ws.session_id.set(sid);
            }
            if ws.project_path.get() != path {
                ws.project_path.set(path);
            }
        });
    }

    pub fn pop_out(&self, id: SlideId) {
        if !slide_meta(id).popout {
            return;
        }
        if self.open.get() != Some(id) {
            self.open.set(Some(id));
        }
        self.fullscreen.set(false);
        if self.popout.get() != Some(id) {
            self.popout.set(Some(id));
        }
    }

    pub fn go_fullscreen(&self, id: SlideId) {
        if !slide_meta(id).popout {
            return;
        }
        if self.open.get() != Some(id) {
            self.open.set(Some(id));
        }
        if self.popout.get() != Some(id) {
            self.popout.set(Some(id));
        }
        if !self.fullscreen.get() {
            self.fullscreen.set(true);
        }
    }

    pub fn hide(&self) {
        if self.open.get().is_some() {
            self.open.set(None);
        }
    }

    pub fn close_popout(&self) {
        if self.popout.get().is_some() {
            self.popout.set(None);
        }
        if self.fullscreen.get() {
            self.fullscreen.set(false);
        }
    }

    pub fn toggle(&self, id: SlideId) {
        if self.open.get() == Some(id) {
            self.hide();
            if self.popout.get() == Some(id) {
                self.close_popout();
            }
        } else {
            self.open.set(Some(id));
        }
    }
}

thread_local! {
    static CURRENT: RefCell<Option<Workspace>> = const { RefCell::new(None) };
}

/// Remember the shell's slide models. [`crate::rails`] reads them while drawing.
pub fn install(ws: Workspace) {
    CURRENT.with(|slot| *slot.borrow_mut() = Some(ws));
}

/// Drop the models when the shell goes away.
pub fn uninstall() {
    CURRENT.with(|slot| *slot.borrow_mut() = None);
}

/// Files, Terminal, Browser, Scratch, or Automations, when a shell installed them.
/// Sessions and Settings stay with the rails. Browser without a registered host
/// still returns the follow-up note.
pub fn rail_body(ui: &Ui, slide: crate::layout::SlideId) -> Option<El> {
    let id = match slide {
        crate::layout::SlideId::Files => SlideId::Files,
        crate::layout::SlideId::Terminal => SlideId::Terminal,
        crate::layout::SlideId::Browser => SlideId::Browser,
        crate::layout::SlideId::Scratch => SlideId::Scratch,
        crate::layout::SlideId::Automations => SlideId::Automations,
        _ => return None,
    };
    let ws = CURRENT.with(|slot| slot.borrow().clone())?;
    Some(slide_body(ui, &ws, id))
}

/// The slide's body, with no frame. Rails and the pop-out both call this.
pub fn slide_body(ui: &Ui, ws: &Workspace, id: SlideId) -> El {
    match id {
        SlideId::Files => files::body(ui, &ws.files),
        SlideId::Terminal => terminal::body(ui, &ws.terminal),
        SlideId::Browser => browser_body(ui),
        SlideId::Scratch => scratch::body(ui, &ws.scratch),
        SlideId::Automations => automations::body(ui, &ws.automations),
    }
}

/// Frame for the open slide: title, pop-out / fullscreen / hide, then the body.
pub fn slide_frame(ui: &Ui, ws: &Workspace) -> El {
    chrome::frame(ui, ws)
}

/// Right-hand column while no rail layout is mounted. Hidden when nothing is
/// open, and while the open slide is the one popped out.
pub fn placeholder_column(ui: &Ui, ws: &Workspace) -> El {
    let gate = ws.clone();
    let ui_b = ui.clone();
    let ws_b = ws.clone();
    show(
        ui,
        move || match gate.open.get() {
            Some(id) => gate.popout.get() != Some(id),
            None => false,
        },
        move || {
            w::row(&ui_b)
                .layout(|l| {
                    l.height = Length::Percent(1.0);
                    l.shrink = 0.0;
                })
                .child(w::divider(&ui_b, true))
                .child(slide_frame(&ui_b, &ws_b).layout(|l| {
                    l.width = Length::Px(360.0);
                    l.height = Length::Percent(1.0);
                    l.shrink = 0.0;
                }))
        },
    )
    .el()
}

/// Overlay for the popped-out slide. Call once per window, not once per frame.
pub fn mount_popout(ui: &Ui, ws: &Workspace) {
    let gate = ws.clone();
    let ui_b = ui.clone();
    let ws_b = ws.clone();
    let layer = show(ui, move || gate.popout.get().is_some(), move || {
        chrome::popout_layer(&ui_b, &ws_b)
    })
    .el();
    ui.overlay(layer);
}

thread_local! {
    static BROWSER: RefCell<Option<BrowserBody>> = const { RefCell::new(None) };
}

type BrowserBody = Box<dyn Fn(&Ui) -> El>;

/// Registers the browser slide from the computer-use port. Until that lands,
/// the slide explains that its host is still that pull request.
pub fn set_browser_body(build: impl Fn(&Ui) -> El + 'static) {
    BROWSER.with(|slot| *slot.borrow_mut() = Some(Box::new(build)));
}

fn browser_body(ui: &Ui) -> El {
    BROWSER.with(|slot| {
        if let Some(build) = slot.borrow().as_ref() {
            return build(ui);
        }
        let colors = ui.theme().colors.clone();
        let unit = ui.theme().spacing.unit;
        w::column(ui)
            .gap(unit)
            .padding(remedy_ui::core::Insets {
                top: unit * 2.0,
                bottom: unit * 2.0,
                start: unit * 2.0,
                end: unit * 2.0,
            })
            .style(remedy_ui::style::StylePatch::new().background(colors.panel).foreground(colors.text))
            .child(w::text(ui, "Browser").style(remedy_ui::style::StylePatch::new().weight(600.0)))
            .child(
                w::text(
                    ui,
                    "The browser host lands with the computer-use port. This pop-out is ready for it.",
                )
                .variant("muted"),
            )
    })
}

/// Files slide, Forest-sized, for goldens and tests.
pub fn files_preview(ui: &Ui) -> El {
    preview(ui, SlideId::Files)
}

/// Terminal slide. The screen is a fixed prompt; the preview does not spawn a PTY.
pub fn terminal_preview(ui: &Ui) -> El {
    preview(ui, SlideId::Terminal)
}

/// Scratch pad with the fixture note.
pub fn scratch_preview(ui: &Ui) -> El {
    preview(ui, SlideId::Scratch)
}

/// Background tasks with the fixture roster.
pub fn automations_preview(ui: &Ui) -> El {
    preview(ui, SlideId::Automations)
}

/// Terminal already popped out, windowed, so tests can drive Esc and fullscreen.
pub fn terminal_popout_preview(ui: &Ui) -> El {
    let ws = Workspace::preview_slide(SlideId::Terminal);
    ws.pop_out(SlideId::Terminal);
    mount_popout(ui, &ws);
    slide_frame(ui, &ws)
}

fn preview(ui: &Ui, id: SlideId) -> El {
    let ws = Workspace::preview_slide(id);
    mount_popout(ui, &ws);
    slide_frame(ui, &ws)
}
