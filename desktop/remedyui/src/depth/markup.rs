//! Image markup editor: pen, highlight, arrow, box, and text on the lightbox.

use super::actions::{add_paths, save_bytes_as};
use crate::messages;
use crate::studio::Studio;
use crate::tokens::{self, typo};
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::{Color, Insets, Point, Vec2};
use remedy_ui::image::decode;
use remedy_ui::input::{Key, KeyEvent, Modifiers, PointerEvent, PointerPhase};
use remedy_ui::layout::{Align, Justify, Length, Position};
use remedy_ui::paint::{Brush, Image, Item, Path, Stroke, StrokeCap, StrokeJoin};
use remedy_ui::reactive::Signal;
use remedy_ui::style::{ColorRoles, LineHeight, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::view::TextPaint;
use remedy_ui::{show, El, PaintCx, Ui};
use std::cell::RefCell;
use std::rc::Rc;

/// Default brush colors, matching the approved markup mockups.
pub const COLORS: &[(&str, &str)] = &[
    ("Red", "#ef4444"),
    ("Orange", "#f97316"),
    ("Yellow", "#eab308"),
    ("Green", "#22c55e"),
    ("Blue", "#3b82f6"),
    ("White", "#f8fafc"),
    ("Black", "#0f172a"),
];

/// Small, medium, and large stroke widths, in image pixels.
pub const WIDTHS: &[(&str, f32)] = &[("S", 2.0), ("M", 4.0), ("L", 8.0)];

const ZOOM_MIN: f32 = 0.25;
const ZOOM_MAX: f32 = 4.0;
const ZOOM_STEP: f32 = 0.15;
const HIGHLIGHT_ALPHA: f32 = 0.4;
const HIGHLIGHT_WIDTH: f32 = 2.2;

/// A drawing tool.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tool {
    Pen,
    Highlight,
    Arrow,
    Box,
    Text,
}

impl Tool {
    pub fn from_key(c: &str) -> Option<Self> {
        match c {
            "p" => Some(Tool::Pen),
            "h" => Some(Tool::Highlight),
            "a" => Some(Tool::Arrow),
            "r" => Some(Tool::Box),
            "t" => Some(Tool::Text),
            _ => None,
        }
    }

    pub fn label_key(self) -> &'static str {
        match self {
            Tool::Pen => "lightbox-pen",
            Tool::Highlight => "lightbox-highlight",
            Tool::Arrow => "lightbox-arrow",
            Tool::Box => "lightbox-box",
            Tool::Text => "lightbox-text",
        }
    }
}

/// One committed (or in-progress) mark, in un-zoomed image pixels.
#[derive(Clone, Debug, PartialEq)]
pub enum Mark {
    Pen {
        color: String,
        width: f32,
        points: Vec<(f32, f32)>,
    },
    Highlight {
        color: String,
        width: f32,
        points: Vec<(f32, f32)>,
    },
    Arrow {
        color: String,
        width: f32,
        from: (f32, f32),
        to: (f32, f32),
    },
    Box {
        color: String,
        width: f32,
        from: (f32, f32),
        to: (f32, f32),
    },
    Text {
        color: String,
        at: (f32, f32),
        text: String,
    },
}

/// Keyboard commands the markup editor understands.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Command {
    Tool(Tool),
    Undo,
    Clear,
    ZoomIn,
    ZoomOut,
    ZoomReset,
    Close,
    CloseText,
}

/// Session state for the open lightbox.
#[derive(Clone)]
pub struct Markup {
    pub tool: Signal<Tool>,
    pub color: Signal<String>,
    pub width: Signal<f32>,
    pub strokes: Signal<Vec<Mark>>,
    pub draft: Signal<Option<Mark>>,
    pub zoom: Signal<f32>,
    pub text_open: Signal<bool>,
    pub text_draft: Signal<String>,
    pub text_at: Signal<Option<(f32, f32)>>,
    pub src_name: Signal<String>,
    pub tick: Signal<u64>,
}

impl Markup {
    pub fn new() -> Self {
        Self {
            tool: Signal::new(Tool::Pen),
            color: Signal::new(COLORS[0].1.to_string()),
            width: Signal::new(4.0),
            strokes: Signal::new(Vec::new()),
            draft: Signal::new(None),
            zoom: Signal::new(1.0),
            text_open: Signal::new(false),
            text_draft: Signal::new(String::new()),
            text_at: Signal::new(None),
            src_name: Signal::new("image.png".into()),
            tick: Signal::new(0),
        }
    }

    pub fn reset(&self) {
        self.tool.set(Tool::Pen);
        self.color.set(COLORS[0].1.to_string());
        self.width.set(4.0);
        self.strokes.set(Vec::new());
        self.draft.set(None);
        self.zoom.set(1.0);
        self.text_open.set(false);
        self.text_draft.set(String::new());
        self.text_at.set(None);
        self.src_name.set("image.png".into());
        self.bump();
    }

    pub fn bump(&self) {
        self.tick.update(|n| *n += 1);
    }

    pub fn undo(&self) {
        self.strokes.update(|s| {
            s.pop();
        });
        self.draft.set(None);
        self.bump();
    }

    pub fn clear(&self) {
        self.strokes.set(Vec::new());
        self.draft.set(None);
        self.bump();
    }

    pub fn set_zoom(&self, zoom: f32) {
        self.zoom.set(clamp_zoom(zoom));
        self.bump();
    }

    pub fn apply_command(&self, cmd: Command) -> bool {
        match cmd {
            Command::Tool(tool) => {
                self.tool.set(tool);
                self.bump();
                true
            }
            Command::Undo => {
                self.undo();
                true
            }
            Command::Clear => {
                self.clear();
                true
            }
            Command::ZoomIn => {
                self.set_zoom(self.zoom.get() + ZOOM_STEP);
                true
            }
            Command::ZoomOut => {
                self.set_zoom(self.zoom.get() - ZOOM_STEP);
                true
            }
            Command::ZoomReset => {
                self.set_zoom(1.0);
                true
            }
            Command::Close | Command::CloseText => false,
        }
    }
}

/// Clamp zoom to the 25%–400% window used by the old lightbox.
pub fn clamp_zoom(zoom: f32) -> f32 {
    if !zoom.is_finite() {
        return 1.0;
    }
    zoom.clamp(ZOOM_MIN, ZOOM_MAX)
}

/// Map a window point on the ink overlay to un-zoomed image pixels.
pub fn pointer_to_image(bounds_min: Point, zoom: f32, pos: Point) -> (f32, f32) {
    let z = zoom.max(0.01);
    ((pos.x - bounds_min.x) / z, (pos.y - bounds_min.y) / z)
}

/// Map a key press to an editor command.
pub fn command_for(key: &Key, modifiers: Modifiers, text_open: bool) -> Option<Command> {
    if modifiers.ctrl || modifiers.meta {
        return match key {
            Key::Character(c) if c == "z" => Some(Command::Undo),
            _ => None,
        };
    }
    if text_open {
        return match key {
            Key::Escape => Some(Command::CloseText),
            _ => None,
        };
    }
    match key {
        Key::Escape => Some(Command::Close),
        Key::Character(c) => {
            let c = c.as_str();
            if let Some(tool) = Tool::from_key(c) {
                return Some(Command::Tool(tool));
            }
            match c {
                "0" => Some(Command::ZoomReset),
                "-" | "_" | "−" => Some(Command::ZoomOut),
                "=" | "+" => Some(Command::ZoomIn),
                _ => None,
            }
        }
        _ => None,
    }
}

/// File name shown in the editor chrome.
pub fn src_name(src: &str) -> String {
    if src.starts_with("data:") {
        return "image.png".into();
    }
    std::path::Path::new(src)
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "image.png".into())
}

/// Decode a data URL or file path for the lightbox, capped like other chat images.
pub fn decode_src(src: &str) -> Option<Image> {
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
    let decoded = decode(&bytes, Some((1600, 1600))).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}

/// Open the markup editor over `src`.
pub fn open(ui: &Ui, studio: &Studio, src: String) {
    let slot: Rc<RefCell<Option<w::Modal>>> = Rc::new(RefCell::new(None));
    let content = editor(ui, studio, &src, {
        let slot = slot.clone();
        let st = studio.clone();
        move || {
            st.depth.lightbox.set(None);
            st.depth.markup.reset();
            if let Some(m) = slot.borrow().as_ref() {
                m.close();
            }
        }
    });
    let modal = w::modal_fitted(ui, messages().text(ui, "lightbox-title"), content, false);
    *slot.borrow_mut() = Some(modal);
}

/// Seed a preview lightbox so tests and shipped screenshots can open the editor
/// with no sidecar.
pub fn seed_preview(studio: &Studio, text_dialog: bool) {
    let url = format!(
        "data:image/png;base64,{}",
        super::api::b64_encode(&preview_settings_png())
    );
    studio.depth.show_image(url);
    studio
        .depth
        .markup
        .src_name
        .set("settings-general.png".into());
    studio.depth.markup.strokes.set(preview_marks());
    if text_dialog {
        studio.depth.markup.tool.set(Tool::Text);
        studio.depth.markup.text_at.set(Some((300.0, 210.0)));
        studio.depth.markup.text_draft.set(String::new());
        studio.depth.markup.text_open.set(true);
    }
    studio.depth.markup.bump();
}

fn preview_marks() -> Vec<Mark> {
    vec![
        Mark::Highlight {
            color: "#eab308".into(),
            width: 4.0 * HIGHLIGHT_WIDTH,
            points: vec![(36.0, 86.0), (250.0, 86.0)],
        },
        Mark::Box {
            color: "#3b82f6".into(),
            width: 3.0,
            from: (24.0, 150.0),
            to: (360.0, 250.0),
        },
        Mark::Arrow {
            color: "#ef4444".into(),
            width: 3.0,
            from: (390.0, 70.0),
            to: (300.0, 200.0),
        },
        Mark::Text {
            color: "#22c55e".into(),
            at: (40.0, 260.0),
            text: "Keep this?".into(),
        },
    ]
}

fn editor(ui: &Ui, studio: &Studio, src: &str, on_close: impl Fn() + Clone + 'static) -> El {
    let colors = ui.theme().colors.clone();
    let depth = tokens::depth_for(&colors);
    let unit = ui.theme().spacing.unit;
    let markup = studio.depth.markup.clone();
    let image = decode_src(src);
    let line = colors.line.unwrap_or(colors.muted);

    let st_keys = studio.clone();
    let close_keys = on_close.clone();
    w::column(ui)
        .gap(unit * 1.5)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.bg)
                .foreground(colors.text)
                .padding(Insets::symmetric(unit * 2.0, unit * 2.0)),
        )
        .semantics(
            Semantics::new(Role::Dialog)
                .label(messages().text(ui, "lightbox-title"))
                .modal(true),
        )
        .on_key(move |e: &KeyEvent| handle_key(&st_keys, e, &close_keys))
        .child(header_row(ui, &markup, &colors))
        .child(tool_row(ui, &markup))
        .child(color_size_row(ui, &markup))
        .child(action_row(ui, studio, image.as_ref(), on_close))
        .child(stage(ui, &markup, image, depth.well, line, unit))
        .child(hint_line(ui, &colors))
        .child(show(
            ui,
            {
                let m = markup.clone();
                move || m.text_open.get()
            },
            {
                let ui = ui.clone();
                let markup = markup.clone();
                move || text_label_card(&ui, &markup)
            },
        ))
}

fn handle_key(studio: &Studio, e: &KeyEvent, on_close: &impl Fn()) -> bool {
    if !e.pressed || e.composing {
        return false;
    }
    if e.repeat && matches!(e.key, Key::Escape) {
        return false;
    }
    let open = studio.depth.markup.text_open.get();
    let Some(cmd) = command_for(&e.key, e.modifiers, open) else {
        return false;
    };
    match cmd {
        Command::Close => {
            on_close();
            true
        }
        Command::CloseText => {
            studio.depth.markup.text_open.set(false);
            studio.depth.markup.text_draft.set(String::new());
            studio.depth.markup.bump();
            true
        }
        other => studio.depth.markup.apply_command(other),
    }
}

fn hint_line(ui: &Ui, colors: &ColorRoles) -> El {
    w::text(ui, messages().text(ui, "lightbox-hint"))
        .style(
            typo(tokens::META)
                .foreground(colors.muted)
                .line_height(LineHeight::Relative(1.4)),
        )
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.wrap = true;
        })
}

fn wrap_row(ui: &Ui) -> El {
    w::row(ui).gap(ui.theme().spacing.unit).layout(|l| {
        l.wrap = true;
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
        l.align_items = Align::Center;
    })
}

fn header_row(ui: &Ui, markup: &Markup, colors: &ColorRoles) -> El {
    let u = ui.clone();
    let m = markup.clone();
    let name = w::text(ui, {
        let m = m.clone();
        let u = u.clone();
        move || {
            let name = m.src_name.get();
            if name.is_empty() {
                messages().text(&u, "lightbox-title")
            } else {
                name
            }
        }
    })
    .style(typo(tokens::BODY).weight(600.0))
    .layout(|l| {
        l.min_width = Length::Px(0.0);
        l.wrap = true;
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
    });
    let status = w::text(ui, {
        let m = m.clone();
        let u = u.clone();
        move || {
            if m.strokes.get().is_empty() {
                messages().text(&u, "lightbox-empty")
            } else {
                messages().text(&u, "lightbox-ready")
            }
        }
    })
    .style(typo(tokens::META).foreground(colors.muted))
    .layout(|l| {
        l.min_width = Length::Px(0.0);
        l.wrap = true;
    });
    wrap_row(ui)
        .justify(Justify::SpaceBetween)
        .child(name)
        .child(status)
}

fn tool_row(ui: &Ui, markup: &Markup) -> El {
    let mut row = wrap_row(ui);
    for tool in [Tool::Pen, Tool::Highlight, Tool::Arrow, Tool::Box, Tool::Text] {
        row = row.child(tool_button(ui, markup, tool));
    }
    row
}

fn tool_button(ui: &Ui, markup: &Markup, tool: Tool) -> El {
    let label = messages().text(ui, tool.label_key());
    let colors = ui.theme().colors.clone();
    let m_click = markup.clone();
    let m_style = markup.clone();
    let m_sem = markup.clone();
    let label_sem = label.clone();
    w::button(ui, label, move || {
        m_click.tool.set(tool);
        m_click.bump();
    })
    .variant("quiet")
    .style(move || chip_style(&colors, m_style.tool.get() == tool))
    .semantics(move || {
        Semantics::new(Role::Button)
            .label(label_sem.clone())
            .selected(m_sem.tool.get() == tool)
            .action(Action::Click)
    })
}

fn chip_style(colors: &ColorRoles, on: bool) -> StylePatch {
    if on {
        StylePatch::new()
            .background(colors.accent)
            .foreground(colors.on_accent)
    } else {
        StylePatch::new()
            .background(colors.card)
            .foreground(colors.muted)
            .border(colors.line.unwrap_or(colors.muted))
            .border_width(1.0)
    }
}

fn color_size_row(ui: &Ui, markup: &Markup) -> El {
    let mut row = wrap_row(ui);
    for (name, hex) in COLORS {
        row = row.child(color_button(ui, markup, name, hex));
    }
    for (name, width) in WIDTHS {
        row = row.child(size_button(ui, markup, name, *width));
    }
    row
}

fn color_button(ui: &Ui, markup: &Markup, name: &str, hex: &str) -> El {
    let fill = Color::from_hex(hex).unwrap_or(Color::WHITE);
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    let accent = colors.accent;
    let m_style = markup.clone();
    let m_click = markup.clone();
    let m_sem = markup.clone();
    let hex_s = hex.to_string();
    let hex_click = hex_s.clone();
    let hex_sem = hex_s.clone();
    let name_s = name.to_string();
    ui.el("markup-color")
        .style(move || {
            let selected = m_style.color.get() == hex_s;
            StylePatch::new()
                .background(fill)
                .border(if selected { accent } else { line })
                .border_width(if selected { 2.0 } else { 1.0 })
                .radius(4.0)
        })
        .layout(|l| {
            l.width = Length::Px(22.0);
            l.height = Length::Px(22.0);
            l.min_width = Length::Px(22.0);
            l.min_height = Length::Px(22.0);
            l.shrink = 0.0;
        })
        .focusable(true)
        .semantics(move || {
            Semantics::new(Role::Button)
                .label(name_s.clone())
                .selected(m_sem.color.get() == hex_sem)
                .action(Action::Click)
        })
        .on_click(move || {
            m_click.color.set(hex_click.clone());
            m_click.bump();
        })
}

fn size_button(ui: &Ui, markup: &Markup, name: &str, width: f32) -> El {
    let colors = ui.theme().colors.clone();
    let m_click = markup.clone();
    let m_style = markup.clone();
    let m_sem = markup.clone();
    let name_s = name.to_string();
    w::button(ui, name.to_string(), move || {
        m_click.width.set(width);
        m_click.bump();
    })
    .variant("quiet")
    .style(move || chip_style(&colors, (m_style.width.get() - width).abs() < f32::EPSILON))
    .semantics(move || {
        Semantics::new(Role::Button)
            .label(name_s.clone())
            .selected((m_sem.width.get() - width).abs() < f32::EPSILON)
            .action(Action::Click)
    })
}

fn action_row(
    ui: &Ui,
    studio: &Studio,
    image: Option<&Image>,
    on_close: impl Fn() + Clone + 'static,
) -> El {
    let st_undo = studio.clone();
    let st_clear = studio.clone();
    let st_out = studio.clone();
    let st_in = studio.clone();
    let st_reset = studio.clone();
    let st_dl = studio.clone();
    let st_at = studio.clone();
    let img_dl = image.cloned();
    let img_at = image.cloned();
    let close = on_close.clone();
    let close_after = on_close;
    let m_zoom = studio.depth.markup.clone();

    wrap_row(ui)
        .child(
            w::button(ui, messages().text(ui, "lightbox-undo"), move || {
                st_undo.depth.markup.undo();
            })
            .variant("quiet"),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-clear"), move || {
                st_clear.depth.markup.clear();
            })
            .variant("quiet"),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-zoom-out"), move || {
                let z = st_out.depth.markup.zoom.get();
                st_out.depth.markup.set_zoom(z - ZOOM_STEP);
            })
            .variant("quiet"),
        )
        .child(
            w::button(
                ui,
                move || format!("{}%", zoom_percent(m_zoom.zoom.get())),
                move || {
                    st_reset.depth.markup.set_zoom(1.0);
                },
            )
            .variant("quiet"),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-zoom-in"), move || {
                let z = st_in.depth.markup.zoom.get();
                st_in.depth.markup.set_zoom(z + ZOOM_STEP);
            })
            .variant("quiet"),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-download"), move || {
                download(&st_dl, img_dl.as_ref());
            })
            .variant("quiet"),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-attach"), move || {
                if attach(&st_at, img_at.as_ref()) {
                    close_after();
                }
            })
            .variant("primary")
            .layout(|l| {
                l.min_width = Length::Px(0.0);
                l.wrap = true;
            }),
        )
        .child(
            w::button(ui, messages().text(ui, "lightbox-close"), move || {
                close();
            })
            .variant("quiet"),
        )
}

fn stage(
    ui: &Ui,
    markup: &Markup,
    image: Option<Image>,
    well: Color,
    line: Color,
    unit: f32,
) -> El {
    let inner = match image {
        Some(image) => canvas(ui, markup, image),
        None => w::text(ui, messages().text(ui, "lightbox-missing")),
    };
    w::scroll(ui)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(120.0);
        })
        .style(
            StylePatch::new()
                .background(well)
                .border(line)
                .border_width(1.0)
                .radius(6.0)
                .padding(Insets::symmetric(unit, unit)),
        )
        .child(
            ui.el("markup-stage-inner")
                .column()
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                    l.align_items = Align::Start;
                    l.justify = Justify::Start;
                })
                .child(inner),
        )
}

fn canvas(ui: &Ui, markup: &Markup, image: Image) -> El {
    let host = ui.el("markup-canvas").layout(|l| {
        l.position = Position::Relative;
        l.shrink = 0.0;
    });
    let w0 = image.width as f32;
    let h0 = image.height as f32;
    let img_el = w::image(ui, image, markup.src_name.get()).layout(|l| {
        l.width = Length::Px(w0);
        l.height = Length::Px(h0);
        l.min_width = Length::Px(0.0);
        l.shrink = 0.0;
    });
    let m = markup.clone();
    img_el.clone().bind({
        let img_el = img_el.clone();
        move || {
            let z = m.zoom.get();
            img_el.clone().layout(|l| {
                l.width = Length::Px((w0 * z).max(1.0));
                l.height = Length::Px((h0 * z).max(1.0));
                l.min_width = Length::Px(0.0);
                l.shrink = 0.0;
            });
        }
    });
    let ink = ui.el("markup-ink").layout(|l| {
        l.position = Position::Absolute;
        l.inset.start = 0.0;
        l.inset.top = 0.0;
        l.width = Length::Px(w0);
        l.height = Length::Px(h0);
    });
    let m = markup.clone();
    ink.clone().bind({
        let ink = ink.clone();
        move || {
            let z = m.zoom.get();
            ink.clone().layout(|l| {
                l.position = Position::Absolute;
                l.inset.start = 0.0;
                l.inset.top = 0.0;
                l.width = Length::Px((w0 * z).max(1.0));
                l.height = Length::Px((h0 * z).max(1.0));
            });
        }
    });
    let m = markup.clone();
    ink.clone().bind({
        let ink = ink.clone();
        let m = m.clone();
        move || {
            let _ = m.tick.get();
            let m = m.clone();
            ink.clone().paint(move |cx| {
                let z = m.zoom.get();
                let mut marks = m.strokes.get();
                if let Some(draft) = m.draft.get() {
                    marks.push(draft);
                }
                paint_marks(cx, &marks, z);
            });
        }
    });
    let m = markup.clone();
    let ink_hit = ink.clone();
    let ink = ink.on_pointer(move |e: &PointerEvent| handle_pointer(&m, &ink_hit, e));
    host.child(img_el).child(ink)
}

fn handle_pointer(markup: &Markup, ink: &El, e: &PointerEvent) -> bool {
    let Some(bounds) = ink.bounds() else {
        return false;
    };
    let to_img = |p: Point| pointer_to_image(bounds.min, markup.zoom.get(), p);
    match e.phase {
        PointerPhase::Down => start_mark(markup, to_img(e.position)),
        PointerPhase::Move => {
            let mut pts: Vec<(f32, f32)> = e.coalesced.iter().map(|(p, _)| to_img(*p)).collect();
            pts.push(to_img(e.position));
            extend_mark(markup, &pts)
        }
        PointerPhase::Up | PointerPhase::Cancel => finish_mark(markup),
    }
}

/// Begin a mark or open the text label card, in image pixels.
pub fn start_mark(markup: &Markup, pt: (f32, f32)) -> bool {
    if markup.text_open.get() {
        return false;
    }
    match markup.tool.get() {
        Tool::Text => {
            markup.text_at.set(Some(pt));
            markup.text_draft.set(String::new());
            markup.text_open.set(true);
            markup.bump();
            true
        }
        Tool::Pen => {
            markup.draft.set(Some(Mark::Pen {
                color: markup.color.get(),
                width: markup.width.get(),
                points: vec![pt],
            }));
            markup.bump();
            true
        }
        Tool::Highlight => {
            markup.draft.set(Some(Mark::Highlight {
                color: markup.color.get(),
                width: markup.width.get() * HIGHLIGHT_WIDTH,
                points: vec![pt],
            }));
            markup.bump();
            true
        }
        Tool::Arrow => {
            markup.draft.set(Some(Mark::Arrow {
                color: markup.color.get(),
                width: markup.width.get(),
                from: pt,
                to: pt,
            }));
            markup.bump();
            true
        }
        Tool::Box => {
            markup.draft.set(Some(Mark::Box {
                color: markup.color.get(),
                width: markup.width.get(),
                from: pt,
                to: pt,
            }));
            markup.bump();
            true
        }
    }
}

/// Extend the in-progress mark through `pts` (image pixels).
pub fn extend_mark(markup: &Markup, pts: &[(f32, f32)]) -> bool {
    let Some(mut draft) = markup.draft.get() else {
        return false;
    };
    match &mut draft {
        Mark::Pen { points, .. } | Mark::Highlight { points, .. } => {
            points.extend(pts.iter().copied());
        }
        Mark::Arrow { to, .. } | Mark::Box { to, .. } => {
            if let Some(last) = pts.last() {
                *to = *last;
            }
        }
        Mark::Text { .. } => {}
    }
    markup.draft.set(Some(draft));
    markup.bump();
    true
}

/// Commit the in-progress mark, dropping a click that never moved.
pub fn finish_mark(markup: &Markup) -> bool {
    if let Some(draft) = markup.draft.get() {
        if mark_is_useful(&draft) {
            markup.strokes.update(|s| s.push(draft));
        }
        markup.draft.set(None);
        markup.bump();
        true
    } else {
        false
    }
}

fn mark_is_useful(mark: &Mark) -> bool {
    match mark {
        Mark::Pen { points, .. } | Mark::Highlight { points, .. } => points.len() >= 2,
        Mark::Arrow { from, to, .. } | Mark::Box { from, to, .. } => {
            (from.0 - to.0).hypot(from.1 - to.1) >= 2.0
        }
        Mark::Text { text, .. } => !text.trim().is_empty(),
    }
}

fn text_label_card(ui: &Ui, markup: &Markup) -> El {
    let colors = ui.theme().colors.clone();
    let depth = tokens::depth_for(&colors);
    let unit = ui.theme().spacing.unit;
    let line = colors.line.unwrap_or(colors.muted);
    let field = w::text_input(ui, messages().text(ui, "lightbox-text-title"));
    field.set_value(&markup.text_draft.get());
    let draft = field.clone();
    let m = markup.clone();
    let field = field.on_change(move || m.text_draft.set(draft.value()));
    let submitted = markup.clone();
    let field = field.on_submit(move || commit_text(&submitted));
    let input = field.el().layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    });
    let cancel_m = markup.clone();
    let add_m = markup.clone();
    w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
            l.max_width = Length::Px(360.0);
        })
        .style(
            StylePatch::new()
                .background(depth.raised)
                .border(line)
                .border_width(1.0)
                .radius(8.0)
                .padding(Insets::symmetric(unit * 2.0, unit * 2.0)),
        )
        .child(
            w::text(ui, messages().text(ui, "lightbox-text-title"))
                .style(typo(tokens::BODY).weight(600.0)),
        )
        .child(input)
        .child(
            wrap_row(ui)
                .justify(Justify::End)
                .child(
                    w::button(ui, messages().text(ui, "lightbox-text-cancel"), move || {
                        cancel_m.text_open.set(false);
                        cancel_m.text_draft.set(String::new());
                        cancel_m.bump();
                    })
                    .variant("quiet"),
                )
                .child(
                    w::button(ui, messages().text(ui, "lightbox-text-add"), move || {
                        commit_text(&add_m);
                    })
                    .variant("primary"),
                ),
        )
}

/// Commit the text-label card into a mark.
pub fn commit_text(markup: &Markup) {
    let text = markup.text_draft.get();
    let Some(at) = markup.text_at.get() else {
        markup.text_open.set(false);
        return;
    };
    if !text.trim().is_empty() {
        markup.strokes.update(|s| {
            s.push(Mark::Text {
                color: markup.color.get(),
                at,
                text,
            });
        });
    }
    markup.text_open.set(false);
    markup.text_draft.set(String::new());
    markup.text_at.set(None);
    markup.bump();
}

fn download(studio: &Studio, image: Option<&Image>) {
    match composite_png(image, &studio.depth.markup.strokes.get()) {
        Some(bytes) => {
            let name = annotated_name(&studio.depth.markup.src_name.get());
            save_bytes_as(studio, "Download markup", &name, bytes);
        }
        None => studio
            .depth
            .notice
            .set("The image could not be written.".into()),
    }
}

fn attach(studio: &Studio, image: Option<&Image>) -> bool {
    let Some(bytes) = composite_png(image, &studio.depth.markup.strokes.get()) else {
        studio
            .depth
            .notice
            .set("The image could not be written.".into());
        return false;
    };
    let name = annotated_name(&studio.depth.markup.src_name.get());
    let path = std::env::temp_dir().join(&name);
    match std::fs::write(&path, &bytes) {
        Ok(()) => {
            add_paths(studio, &[path]);
            studio
                .depth
                .notice
                .set("Attached to the next prompt.".into());
            true
        }
        Err(e) => {
            studio.depth.notice.set(e.to_string());
            false
        }
    }
}

/// `{stem}-markup.png` for a download or an attachment.
pub fn annotated_name(src_name: &str) -> String {
    let path = std::path::Path::new(src_name);
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("image");
    format!("{stem}-markup.png")
}

fn zoom_percent(zoom: f32) -> i32 {
    (clamp_zoom(zoom) * 100.0).round() as i32
}

/// Flatten the base image and marks to a PNG (straight RGBA).
pub fn composite_png(image: Option<&Image>, marks: &[Mark]) -> Option<Vec<u8>> {
    let (w, h, mut pixels) = if let Some(image) = image {
        (
            image.width,
            image.height,
            unpremultiply(&image.pixels, image.width, image.height),
        )
    } else {
        return None;
    };
    for mark in marks {
        raster_mark(&mut pixels, w, h, mark);
    }
    Some(encode_png(w, h, &pixels))
}

fn paint_marks(cx: &mut PaintCx<'_>, marks: &[Mark], zoom: f32) {
    for mark in marks {
        match mark {
            Mark::Pen {
                color,
                width,
                points,
            } => stroke_poly(cx, &scale_pts(points, zoom), color, *width * zoom, 1.0),
            Mark::Highlight {
                color,
                width,
                points,
            } => stroke_poly(
                cx,
                &scale_pts(points, zoom),
                color,
                *width * zoom,
                HIGHLIGHT_ALPHA,
            ),
            Mark::Arrow {
                color,
                width,
                from,
                to,
            } => paint_arrow(cx, *from, *to, color, *width * zoom, zoom),
            Mark::Box {
                color,
                width,
                from,
                to,
            } => paint_box(cx, *from, *to, color, *width * zoom, zoom),
            Mark::Text { color, at, text } => paint_label(cx, *at, text, color, zoom),
        }
    }
}

fn scale_pts(points: &[(f32, f32)], zoom: f32) -> Vec<(f32, f32)> {
    points
        .iter()
        .map(|(x, y)| (*x * zoom, *y * zoom))
        .collect()
}

fn stroke_poly(cx: &mut PaintCx<'_>, points: &[(f32, f32)], color: &str, width: f32, alpha: f32) {
    if points.len() < 2 {
        return;
    }
    let mut path = Path::new();
    path.move_to(Point::new(points[0].0, points[0].1));
    for p in points.iter().skip(1) {
        path.line_to(Point::new(p.0, p.1));
    }
    cx.list.push(Item::StrokePath {
        path,
        stroke: Stroke {
            width: width.max(1.0),
            cap: StrokeCap::Round,
            join: StrokeJoin::Round,
            dashes: Vec::new(),
        },
        brush: Brush::Solid(color_alpha(color, alpha)),
    });
}

fn paint_arrow(
    cx: &mut PaintCx<'_>,
    from: (f32, f32),
    to: (f32, f32),
    color: &str,
    width: f32,
    zoom: f32,
) {
    let from = (from.0 * zoom, from.1 * zoom);
    let to = (to.0 * zoom, to.1 * zoom);
    stroke_poly(cx, &[from, to], color, width, 1.0);
    let (hx, hy) = arrow_heads(from, to, (10.0 + width * 2.0).max(12.0));
    stroke_poly(cx, &[hx, to, hy], color, width, 1.0);
}

fn paint_box(
    cx: &mut PaintCx<'_>,
    from: (f32, f32),
    to: (f32, f32),
    color: &str,
    width: f32,
    zoom: f32,
) {
    let x0 = from.0.min(to.0) * zoom;
    let y0 = from.1.min(to.1) * zoom;
    let x1 = from.0.max(to.0) * zoom;
    let y1 = from.1.max(to.1) * zoom;
    let pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)];
    stroke_poly(cx, &pts, color, width, 1.0);
}

fn paint_label(cx: &mut PaintCx<'_>, at: (f32, f32), text: &str, color: &str, zoom: f32) {
    let fill = color_alpha(color, 1.0);
    cx.text(
        text,
        Point::new(at.0 * zoom, at.1 * zoom),
        Vec2::new(0.0, 0.0),
        TextPaint {
            size: (16.0 * zoom).max(10.0),
            weight: 600.0,
            color: fill,
            mono: false,
        },
    );
}

fn color_alpha(hex: &str, alpha: f32) -> Color {
    Color::from_hex(hex)
        .unwrap_or(Color::rgb8(239, 68, 68))
        .with_alpha(alpha.clamp(0.0, 1.0))
}

fn arrow_heads(from: (f32, f32), to: (f32, f32), size: f32) -> ((f32, f32), (f32, f32)) {
    let dx = to.0 - from.0;
    let dy = to.1 - from.1;
    let len = dx.hypot(dy).max(0.001);
    let (ux, uy) = (dx / len, dy / len);
    let (px, py) = (-uy, ux);
    let back_x = to.0 - ux * size;
    let back_y = to.1 - uy * size;
    let spread = size * 0.55;
    (
        (back_x + px * spread, back_y + py * spread),
        (back_x - px * spread, back_y - py * spread),
    )
}

fn raster_mark(px: &mut [u8], w: u32, h: u32, mark: &Mark) {
    match mark {
        Mark::Pen {
            color,
            width,
            points,
        } => raster_poly(px, w, h, points, color, *width, 1.0),
        Mark::Highlight {
            color,
            width,
            points,
        } => raster_poly(px, w, h, points, color, *width, HIGHLIGHT_ALPHA),
        Mark::Arrow {
            color,
            width,
            from,
            to,
        } => {
            raster_poly(px, w, h, &[*from, *to], color, *width, 1.0);
            let (hx, hy) = arrow_heads(*from, *to, (10.0 + *width * 2.0).max(12.0));
            raster_poly(px, w, h, &[hx, *to, hy], color, *width, 1.0);
        }
        Mark::Box {
            color,
            width,
            from,
            to,
        } => {
            let x0 = from.0.min(to.0);
            let y0 = from.1.min(to.1);
            let x1 = from.0.max(to.0);
            let y1 = from.1.max(to.1);
            let pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)];
            raster_poly(px, w, h, &pts, color, *width, 1.0);
        }
        Mark::Text { color, at, text } => blit_text(px, w, h, at.0, at.1, text, color, 2),
    }
}

fn raster_poly(px: &mut [u8], w: u32, h: u32, points: &[(f32, f32)], color: &str, width: f32, a: f32) {
    if points.len() < 2 {
        return;
    }
    let color = color_alpha(color, a);
    let radius = (width / 2.0).max(0.6);
    for pair in points.windows(2) {
        raster_capsule(px, w, h, pair[0], pair[1], radius, color);
    }
}

fn raster_capsule(
    px: &mut [u8],
    w: u32,
    h: u32,
    a: (f32, f32),
    b: (f32, f32),
    radius: f32,
    color: Color,
) {
    let pad = radius.ceil() + 1.0;
    let min_x = a.0.min(b.0) - pad;
    let min_y = a.1.min(b.1) - pad;
    let max_x = a.0.max(b.0) + pad;
    let max_y = a.1.max(b.1) + pad;
    let x0 = min_x.floor().max(0.0) as i32;
    let y0 = min_y.floor().max(0.0) as i32;
    let x1 = max_x.ceil().min(w as f32) as i32;
    let y1 = max_y.ceil().min(h as f32) as i32;
    let dx = b.0 - a.0;
    let dy = b.1 - a.1;
    let len2 = dx * dx + dy * dy;
    for y in y0..y1 {
        for x in x0..x1 {
            let px_x = x as f32 + 0.5;
            let px_y = y as f32 + 0.5;
            let t = if len2 <= f32::EPSILON {
                0.0
            } else {
                ((px_x - a.0) * dx + (px_y - a.1) * dy) / len2
            }
            .clamp(0.0, 1.0);
            let cx = a.0 + dx * t;
            let cy = a.1 + dy * t;
            if (px_x - cx).hypot(px_y - cy) <= radius {
                blend_pixel(px, w, x as u32, y as u32, color);
            }
        }
    }
}

fn blend_pixel(px: &mut [u8], w: u32, x: u32, y: u32, color: Color) {
    let i = ((y * w + x) * 4) as usize;
    if i + 3 >= px.len() {
        return;
    }
    let [sr, sg, sb, sa] = color.to_rgba8();
    let src_a = sa as f32 / 255.0;
    if src_a <= 0.0 {
        return;
    }
    let inv = 1.0 - src_a;
    px[i] = (sr as f32 * src_a + px[i] as f32 * inv).round() as u8;
    px[i + 1] = (sg as f32 * src_a + px[i + 1] as f32 * inv).round() as u8;
    px[i + 2] = (sb as f32 * src_a + px[i + 2] as f32 * inv).round() as u8;
    let da = px[i + 3] as f32 / 255.0;
    px[i + 3] = ((src_a + da * inv) * 255.0).round() as u8;
}

fn blit_text(
    px: &mut [u8],
    w: u32,
    h: u32,
    x: f32,
    y: f32,
    text: &str,
    color: &str,
    scale: i32,
) {
    let color = color_alpha(color, 1.0);
    let mut cx = x.round() as i32;
    let cy = y.round() as i32;
    let scale = scale.max(1);
    for ch in text.chars() {
        if ch == '\n' {
            continue;
        }
        let bits = glyph(ch);
        if bits != 0 || ch == ' ' {
            for row in 0..7 {
                for col in 0..5 {
                    if bits & (1 << (row * 5 + col)) != 0 {
                        for oy in 0..scale {
                            for ox in 0..scale {
                                let px_x = cx + col * scale + ox;
                                let px_y = cy + row * scale + oy;
                                if px_x >= 0 && px_y >= 0 && (px_x as u32) < w && (px_y as u32) < h
                                {
                                    blend_pixel(px, w, px_x as u32, px_y as u32, color);
                                }
                            }
                        }
                    }
                }
            }
        }
        cx += 6 * scale;
    }
}

fn glyph(c: char) -> u64 {
    match c {
        ' ' => 0x00000000u64,
        '!' => 0x100421084u64,
        '"' => 0x0000014au64,
        '#' => 0x15f52beau64,
        '%' => 0x674222173u64,
        '&' => 0x592932526u64,
        '\'' => 0x00001084u64,
        '(' => 0x208421088u64,
        ')' => 0x88421082u64,
        '*' => 0x144f9140u64,
        '+' => 0x084f9080u64,
        ',' => 0x88400000u64,
        '-' => 0x000f0000u64,
        '.' => 0x108000000u64,
        '/' => 0x42222210u64,
        '0' => 0x3a33ae62eu64,
        '1' => 0x3884210c4u64,
        '2' => 0x7c444422eu64,
        '3' => 0x3a306422eu64,
        '4' => 0x211f4a988u64,
        '5' => 0x3a3083c3fu64,
        '6' => 0x3a317842eu64,
        '7' => 0x10842221fu64,
        '8' => 0x3a317462eu64,
        '9' => 0x3a10f462eu64,
        ':' => 0x08401080u64,
        ';' => 0x88401080u64,
        '=' => 0x01e07800u64,
        '?' => 0x10044422eu64,
        'A' => 0x4631fc62eu64,
        'B' => 0x3e317c62fu64,
        'C' => 0x3a210862eu64,
        'D' => 0x3e318c62fu64,
        'E' => 0x7c217843fu64,
        'F' => 0x4217843fu64,
        'G' => 0x3a31e862eu64,
        'H' => 0x4631fc631u64,
        'I' => 0x38842108eu64,
        'J' => 0x19284211cu64,
        'K' => 0x452519531u64,
        'L' => 0x7c2108421u64,
        'M' => 0x4631ad771u64,
        'N' => 0x4631cd671u64,
        'O' => 0x3a318c62eu64,
        'P' => 0x4217c62fu64,
        'Q' => 0x59358c62eu64,
        'R' => 0x45257c62fu64,
        'S' => 0x3a307062eu64,
        'T' => 0x10842109fu64,
        'U' => 0x3a318c631u64,
        'V' => 0x11518c631u64,
        'W' => 0x4775ac631u64,
        'X' => 0x462a22a31u64,
        'Y' => 0x108422a31u64,
        'Z' => 0x7c222221fu64,
        '\\' => 0x420820821u64,
        '_' => 0x7c0000000u64,
        'a' => 0x4631fc62eu64,
        'b' => 0x3e317c62fu64,
        'c' => 0x3a210862eu64,
        'd' => 0x3e318c62fu64,
        'e' => 0x7c217843fu64,
        'f' => 0x4217843fu64,
        'g' => 0x3a31e862eu64,
        'h' => 0x4631fc631u64,
        'i' => 0x38842108eu64,
        'j' => 0x19284211cu64,
        'k' => 0x452519531u64,
        'l' => 0x7c2108421u64,
        'm' => 0x4631ad771u64,
        'n' => 0x4631cd671u64,
        'o' => 0x3a318c62eu64,
        'p' => 0x4217c62fu64,
        'q' => 0x59358c62eu64,
        'r' => 0x45257c62fu64,
        's' => 0x3a307062eu64,
        't' => 0x10842109fu64,
        'u' => 0x3a318c631u64,
        'v' => 0x11518c631u64,
        'w' => 0x4775ac631u64,
        'x' => 0x462a22a31u64,
        'y' => 0x108422a31u64,
        'z' => 0x7c222221fu64,
        '\u{00b7}' => 0x00020000u64,
        '\u{2212}' => 0x000f0000u64,
        _ => 0,
    }
}

fn unpremultiply(pixels: &[u8], w: u32, h: u32) -> Vec<u8> {
    let want = w as usize * h as usize * 4;
    let mut out = vec![0u8; want];
    let n = want.min(pixels.len());
    out[..n].copy_from_slice(&pixels[..n]);
    for c in out.chunks_exact_mut(4) {
        let a = c[3];
        if a == 0 {
            c[0] = 0;
            c[1] = 0;
            c[2] = 0;
        } else if a < 255 {
            let s = 255.0 / a as f32;
            c[0] = (c[0] as f32 * s).round().min(255.0) as u8;
            c[1] = (c[1] as f32 * s).round().min(255.0) as u8;
            c[2] = (c[2] as f32 * s).round().min(255.0) as u8;
        }
    }
    out
}

/// Encode straight RGBA8 `pixels` as a PNG.
pub fn encode_png(width: u32, height: u32, pixels: &[u8]) -> Vec<u8> {
    let mut raw = Vec::with_capacity((width as usize + 1) * height as usize * 4);
    let row = width as usize * 4;
    for y in 0..height as usize {
        raw.push(0);
        let start = y * row;
        let end = (start + row).min(pixels.len());
        raw.extend_from_slice(&pixels[start..end]);
        if end - start < row {
            raw.resize(raw.len() + (row - (end - start)), 0);
        }
    }
    let deflated = zlib_store(&raw);
    let mut out = Vec::new();
    out.extend_from_slice(&[137, 80, 78, 71, 13, 10, 26, 10]);
    write_chunk(&mut out, *b"IHDR", &{
        let mut d = Vec::new();
        d.extend_from_slice(&width.to_be_bytes());
        d.extend_from_slice(&height.to_be_bytes());
        d.extend_from_slice(&[8, 6, 0, 0, 0]);
        d
    });
    write_chunk(&mut out, *b"IDAT", &deflated);
    write_chunk(&mut out, *b"IEND", &[]);
    out
}

fn write_chunk(out: &mut Vec<u8>, kind: [u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(&kind);
    out.extend_from_slice(data);
    let mut crc_src = Vec::with_capacity(4 + data.len());
    crc_src.extend_from_slice(&kind);
    crc_src.extend_from_slice(data);
    out.extend_from_slice(&crc32(&crc_src).to_be_bytes());
}

fn zlib_store(data: &[u8]) -> Vec<u8> {
    let mut out = vec![0x78, 0x01];
    let mut i = 0;
    while i < data.len() {
        let end = (i + 65535).min(data.len());
        let chunk = &data[i..end];
        let last = end == data.len();
        out.push(if last { 0x01 } else { 0x00 });
        let n = chunk.len() as u16;
        out.extend_from_slice(&n.to_le_bytes());
        out.extend_from_slice((!n).to_le_bytes().as_ref());
        out.extend_from_slice(chunk);
        i = end;
    }
    if data.is_empty() {
        out.push(0x01);
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice((!0u16).to_le_bytes().as_ref());
    }
    out.extend_from_slice(&adler32(data).to_be_bytes());
    out
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xffff_ffffu32;
    for &b in data {
        crc ^= u32::from(b);
        for _ in 0..8 {
            crc = if crc & 1 == 1 {
                (crc >> 1) ^ 0xedb8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

fn adler32(data: &[u8]) -> u32 {
    let mut a = 1u32;
    let mut b = 0u32;
    for &x in data {
        a = (a + u32::from(x)) % 65521;
        b = (b + a) % 65521;
    }
    (b << 16) | a
}

fn preview_settings_png() -> Vec<u8> {
    let w = 480u32;
    let h = 300u32;
    let mut px = vec![0u8; (w * h * 4) as usize];
    fill_rect(&mut px, w, h, 0, 0, w, h, Color::rgb8(11, 26, 23));
    fill_rect(&mut px, w, h, 0, 0, w, 44, Color::rgb8(7, 20, 18));
    blit_text(&mut px, w, h, 16.0, 14.0, "Settings · General", "#e2f3ee", 2);
    blit_text(&mut px, w, h, 16.0, 64.0, "Your name", "#86a39c", 2);
    blit_text(&mut px, w, h, 200.0, 64.0, "Joe", "#e2f3ee", 2);
    blit_text(&mut px, w, h, 16.0, 100.0, "Launch at login", "#86a39c", 2);
    blit_text(&mut px, w, h, 200.0, 100.0, "Off", "#e2f3ee", 2);
    blit_text(&mut px, w, h, 16.0, 136.0, "Provider", "#86a39c", 2);
    blit_text(&mut px, w, h, 200.0, 136.0, "local / remedy", "#e2f3ee", 2);
    fill_rect(&mut px, w, h, 16, 184, 180, 36, Color::rgb8(111, 220, 134));
    blit_text(&mut px, w, h, 28.0, 194.0, "Keep this default", "#040a09", 2);
    encode_png(w, h, &px)
}

fn fill_rect(px: &mut [u8], w: u32, h: u32, x: u32, y: u32, rw: u32, rh: u32, color: Color) {
    let x1 = (x + rw).min(w);
    let y1 = (y + rh).min(h);
    for yy in y..y1 {
        for xx in x..x1 {
            blend_pixel(px, w, xx, yy, color);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use remedy_ui::input::KeyEvent;

    #[test]
    fn commands_cover_tools_undo_zoom_and_esc() {
        assert_eq!(
            command_for(&Key::char('p'), Modifiers::NONE, false),
            Some(Command::Tool(Tool::Pen))
        );
        assert_eq!(
            command_for(&Key::char('h'), Modifiers::NONE, false),
            Some(Command::Tool(Tool::Highlight))
        );
        assert_eq!(
            command_for(&Key::char('a'), Modifiers::NONE, false),
            Some(Command::Tool(Tool::Arrow))
        );
        assert_eq!(
            command_for(&Key::char('r'), Modifiers::NONE, false),
            Some(Command::Tool(Tool::Box))
        );
        assert_eq!(
            command_for(&Key::char('t'), Modifiers::NONE, false),
            Some(Command::Tool(Tool::Text))
        );
        assert_eq!(
            command_for(&Key::char('z'), Modifiers::CTRL, false),
            Some(Command::Undo)
        );
        assert_eq!(
            command_for(&Key::char('0'), Modifiers::NONE, false),
            Some(Command::ZoomReset)
        );
        assert_eq!(
            command_for(&Key::char('-'), Modifiers::NONE, false),
            Some(Command::ZoomOut)
        );
        assert_eq!(
            command_for(&Key::char('='), Modifiers::NONE, false),
            Some(Command::ZoomIn)
        );
        assert_eq!(
            command_for(&Key::Escape, Modifiers::NONE, false),
            Some(Command::Close)
        );
        assert_eq!(
            command_for(&Key::Escape, Modifiers::NONE, true),
            Some(Command::CloseText)
        );
        assert_eq!(
            command_for(&Key::char('p'), Modifiers::NONE, true),
            None
        );
        let _ = KeyEvent::press(Key::char('p'));
    }

    #[test]
    fn zoom_stays_in_range_and_names_annotate() {
        assert_eq!(clamp_zoom(0.1), 0.25);
        assert_eq!(clamp_zoom(8.0), 4.0);
        assert_eq!(clamp_zoom(f32::NAN), 1.0);
        assert_eq!(zoom_percent(1.0), 100);
        assert_eq!(annotated_name("settings-general.png"), "settings-general-markup.png");
        assert_eq!(annotated_name(""), "image-markup.png");
        assert_eq!(src_name("data:image/png;base64,aa"), "image.png");
        assert_eq!(src_name("/tmp/shot.PNG"), "shot.PNG");
    }

    #[test]
    fn pointer_maps_through_zoom_and_marks_undo() {
        let pt = pointer_to_image(Point::new(10.0, 20.0), 2.0, Point::new(30.0, 60.0));
        assert_eq!(pt, (10.0, 20.0));
        let m = Markup::new();
        assert!(start_mark(&m, (8.0, 8.0)));
        assert!(extend_mark(&m, &[(40.0, 12.0)]));
        assert!(finish_mark(&m));
        assert_eq!(m.strokes.get().len(), 1);
        m.undo();
        assert!(m.strokes.get().is_empty());
        m.tool.set(Tool::Text);
        assert!(start_mark(&m, (16.0, 16.0)));
        assert!(m.text_open.get());
        m.text_draft.set("Note".into());
        commit_text(&m);
        assert!(!m.text_open.get());
        assert_eq!(m.strokes.get().len(), 1);
        m.clear();
        assert!(m.strokes.get().is_empty());
    }

    #[test]
    fn encode_png_round_trips_and_composite_keeps_size() {
        let mut px = vec![0u8; 8 * 8 * 4];
        for c in px.chunks_exact_mut(4) {
            c[0] = 239;
            c[1] = 68;
            c[2] = 68;
            c[3] = 255;
        }
        let png = encode_png(8, 8, &px);
        assert_eq!(&png[..8], &[137, 80, 78, 71, 13, 10, 26, 10]);
        let decoded = decode(&png, None).expect("png decodes");
        assert_eq!(decoded.image.width, 8);
        assert_eq!(decoded.image.height, 8);
        let image = Image::new(
            decoded.image.width,
            decoded.image.height,
            decoded.image.pixels,
        );
        let marks = vec![Mark::Box {
            color: "#22c55e".into(),
            width: 2.0,
            from: (1.0, 1.0),
            to: (6.0, 6.0),
        }];
        let out = composite_png(Some(&image), &marks).expect("composite");
        let again = decode(&out, None).expect("composite decodes");
        assert_eq!(again.image.width, 8);
        assert_eq!(again.image.height, 8);
    }

    #[test]
    fn preview_png_is_a_real_image() {
        let png = preview_settings_png();
        let decoded = decode(&png, None).expect("preview");
        assert_eq!(decoded.image.width, 480);
        assert_eq!(decoded.image.height, 300);
    }
}
