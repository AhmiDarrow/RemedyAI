//! Offline manual body: the markdown the Tauri Help panel renders, turned into
//! RemedyUI elements.
//!
//! The subset is the one the chapters actually use (ATX headings, fenced code,
//! GFM tables, task lists, quotes, images, inline emphasis). Heading slugs match
//! `desktop/src/help/headingAnchor.ts`, including non-English text and hyphens
//! that are not collapsed. A separator row needs one dash, same as remark-gfm
//! (`|--|--|` in the overview).

use std::rc::Rc;
use std::sync::OnceLock;

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::image::decode;
use remedy_ui::layout::{Display, Length, Track};
use remedy_ui::paint::Image;
use remedy_ui::style::{ensure_contrast, BorderEdge, BorderSide, StylePatch};
use remedy_ui::text::{RichText, TextStylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

const HERO: &[u8] = include_bytes!("../../help/assets/hero_logo_color_on_dark.png");
const HERO_PREVIEW: &[u8] = include_bytes!("../../help/assets/previews/hero_logo_color_on_dark.png");
const ARCH: &[u8] = include_bytes!("../../help/assets/remedy-partner-architecture.jpg");
const ARCH_PREVIEW: &[u8] = include_bytes!("../../help/assets/previews/remedy-partner-architecture.jpg");

/// GitHub-style slug. Letters, numbers, marks, `_`, and `-` stay; every
/// whitespace character becomes its own `-`.
pub(crate) fn heading_anchor(text: &str) -> String {
    let mut out = String::new();
    for ch in text.to_lowercase().chars() {
        if ch == '_' || ch == '-' || ch.is_whitespace() || ch.is_alphanumeric() || is_mark(ch) {
            if ch.is_whitespace() {
                out.push('-');
            } else {
                out.push(ch);
            }
        }
    }
    out
}

/// Unicode general category M, the marks `\p{M}` keeps in a slug.
fn is_mark(ch: char) -> bool {
    matches!(
        ch as u32,
        0x0300..=0x036F
            | 0x0483..=0x0489
            | 0x0591..=0x05BD
            | 0x05BF
            | 0x05C1..=0x05C2
            | 0x05C4..=0x05C5
            | 0x05C7
            | 0x0610..=0x061A
            | 0x064B..=0x065F
            | 0x0670
            | 0x06D6..=0x06DC
            | 0x06DF..=0x06E4
            | 0x06E7..=0x06E8
            | 0x06EA..=0x06ED
            | 0x0711
            | 0x0730..=0x074A
            | 0x07A6..=0x07B0
            | 0x07EB..=0x07F3
            | 0x07FD
            | 0x0816..=0x0819
            | 0x081B..=0x0823
            | 0x0825..=0x0827
            | 0x0829..=0x082D
            | 0x0859..=0x085B
            | 0x0898..=0x089F
            | 0x08CA..=0x08E1
            | 0x08E3..=0x0902
            | 0x093A
            | 0x093C
            | 0x0941..=0x0948
            | 0x094D
            | 0x0951..=0x0957
            | 0x0962..=0x0963
            | 0x09BC
            | 0x09C1..=0x09C4
            | 0x09CD
            | 0x09E2..=0x09E3
            | 0x09FE
            | 0x0A3C
            | 0x0A41..=0x0A42
            | 0x0A47..=0x0A48
            | 0x0A4B..=0x0A4D
            | 0x0A51
            | 0x0A70..=0x0A71
            | 0x0A75
            | 0x0ABC
            | 0x0AC1..=0x0AC8
            | 0x0ACD
            | 0x0AE2..=0x0AE3
            | 0x0AFA..=0x0AFF
            | 0x0B3C
            | 0x0B3F
            | 0x0B41..=0x0B44
            | 0x0B4D
            | 0x0B55..=0x0B56
            | 0x0B62..=0x0B63
            | 0x0B82
            | 0x0BC0
            | 0x0BCD
            | 0x0C3E..=0x0C40
            | 0x0C46..=0x0C48
            | 0x0C4A..=0x0C4D
            | 0x0C55..=0x0C56
            | 0x0C62..=0x0C63
            | 0x0CBC
            | 0x0CBF
            | 0x0CC6
            | 0x0CCC..=0x0CCD
            | 0x0CE2..=0x0CE3
            | 0x0D3B..=0x0D3C
            | 0x0D41..=0x0D44
            | 0x0D4D
            | 0x0D62..=0x0D63
            | 0x0DCA
            | 0x0DD2..=0x0DD4
            | 0x0DD6
            | 0x0E31
            | 0x0E34..=0x0E3A
            | 0x0E47..=0x0E4E
            | 0x0EB1
            | 0x0EB4..=0x0EBC
            | 0x0EC8..=0x0ECE
            | 0x0F18..=0x0F19
            | 0x0F35
            | 0x0F37
            | 0x0F39
            | 0x0F71..=0x0F7E
            | 0x0F80..=0x0F84
            | 0x0F86..=0x0F87
            | 0x0F8D..=0x0F97
            | 0x0F99..=0x0FBC
            | 0x0FC6
            | 0x102D..=0x1030
            | 0x1032..=0x1037
            | 0x1039..=0x103A
            | 0x103D..=0x103E
            | 0x1058..=0x1059
            | 0x105E..=0x1060
            | 0x1071..=0x1074
            | 0x1082
            | 0x1085..=0x1086
            | 0x108D
            | 0x109D
            | 0x135D..=0x135F
            | 0x1712..=0x1714
            | 0x1732..=0x1734
            | 0x1752..=0x1753
            | 0x1772..=0x1773
            | 0x17B4..=0x17B5
            | 0x17B7..=0x17BD
            | 0x17C6
            | 0x17C9..=0x17D3
            | 0x17DD
            | 0x180B..=0x180F
            | 0x1885..=0x1886
            | 0x18A9
            | 0x1920..=0x1922
            | 0x1927..=0x1928
            | 0x1932
            | 0x1939..=0x193B
            | 0x1A17..=0x1A18
            | 0x1A1B
            | 0x1A56
            | 0x1A58..=0x1A5E
            | 0x1A60
            | 0x1A62
            | 0x1A65..=0x1A6C
            | 0x1A73..=0x1A7C
            | 0x1A7F
            | 0x1AB0..=0x1ACE
            | 0x1B00..=0x1B04
            | 0x1B34..=0x1B3C
            | 0x1B42
            | 0x1B6B..=0x1B73
            | 0x1B80..=0x1B81
            | 0x1BA2..=0x1BA5
            | 0x1BA8..=0x1BA9
            | 0x1BAB..=0x1BAD
            | 0x1BE6..=0x1BF3
            | 0x1CD0..=0x1CD2
            | 0x1CD4..=0x1CE0
            | 0x1CE2..=0x1CE8
            | 0x1CED
            | 0x1CF4
            | 0x1CF8..=0x1CF9
            | 0x1DC0..=0x1DFF
            | 0x20D0..=0x20F0
            | 0x2CEF..=0x2CF1
            | 0x2D7F
            | 0x2DE0..=0x2DFF
            | 0x302A..=0x302F
            | 0x3099..=0x309A
            | 0xA66F
            | 0xA674..=0xA67D
            | 0xA69E..=0xA69F
            | 0xA6F0..=0xA6F1
            | 0xA802
            | 0xA806
            | 0xA80B
            | 0xA825..=0xA826
            | 0xA82C
            | 0xA8C4..=0xA8C5
            | 0xA8E0..=0xA8F1
            | 0xA8FF
            | 0xA926..=0xA92D
            | 0xA947..=0xA951
            | 0xA980..=0xA982
            | 0xA9B3
            | 0xA9B6..=0xA9B9
            | 0xA9BC..=0xA9BD
            | 0xA9E5
            | 0xAA29..=0xAA2E
            | 0xAA31..=0xAA32
            | 0xAA35..=0xAA36
            | 0xAA43
            | 0xAA4C
            | 0xAA7C
            | 0xAAB0
            | 0xAAB2..=0xAAB4
            | 0xAAB7..=0xAAB8
            | 0xAABE..=0xAABF
            | 0xAAC1
            | 0xAAEC..=0xAAED
            | 0xAAF6
            | 0xABE5
            | 0xABE8
            | 0xABED
            | 0xFB1E
            | 0xFE00..=0xFE0F
            | 0xFE20..=0xFE2F
            | 0x101FD
            | 0x102E0
            | 0x10376..=0x1037A
            | 0x10A01..=0x10A03
            | 0x10A05..=0x10A06
            | 0x10A0C..=0x10A0F
            | 0x10A38..=0x10A3A
            | 0x10A3F
            | 0x10AE5..=0x10AE6
            | 0x10D24..=0x10D27
            | 0x10EAB..=0x10EAC
            | 0x10F46..=0x10F50
            | 0x10F82..=0x10F85
            | 0x11001
            | 0x11038..=0x11046
            | 0x1107F..=0x11081
            | 0x110B3..=0x110B6
            | 0x110B9..=0x110BA
            | 0x11100..=0x11102
            | 0x11127..=0x1112B
            | 0x1112D..=0x11134
            | 0x11173
            | 0x11180..=0x11181
            | 0x111B6..=0x111BE
            | 0x111C9..=0x111CC
            | 0x1122F..=0x11231
            | 0x11234
            | 0x11236..=0x11237
            | 0x112E3..=0x112EA
            | 0x11300..=0x11301
            | 0x1133B..=0x1133C
            | 0x11340
            | 0x11366..=0x1136C
            | 0x11370..=0x11374
            | 0x11438..=0x1143F
            | 0x11442..=0x11444
            | 0x11446
            | 0x1145E
            | 0x114B3..=0x114B8
            | 0x114BA
            | 0x114BF..=0x114C0
            | 0x114C2..=0x114C3
            | 0x115B2..=0x115B5
            | 0x115BC..=0x115BD
            | 0x115BF..=0x115C0
            | 0x115DC..=0x115DD
            | 0x11633..=0x1163A
            | 0x1163D
            | 0x1163F..=0x11640
            | 0x116AB
            | 0x116AD
            | 0x116B0..=0x116B5
            | 0x116B7
            | 0x1171D..=0x1171F
            | 0x11722..=0x11725
            | 0x11727..=0x1172B
            | 0x1182F..=0x11837
            | 0x11930..=0x11935
            | 0x11937..=0x11938
            | 0x1193B..=0x1193C
            | 0x1193E
            | 0x11943
            | 0x119D4..=0x119D7
            | 0x119DA..=0x119DB
            | 0x11A01..=0x11A0A
            | 0x11A33..=0x11A38
            | 0x11A3B..=0x11A3E
            | 0x11A47
            | 0x11A51..=0x11A56
            | 0x11A59..=0x11A5B
            | 0x11A8A..=0x11A96
            | 0x11A98..=0x11A99
            | 0x11C30..=0x11C36
            | 0x11C38..=0x11C3D
            | 0x11C3F
            | 0x11C92..=0x11CA7
            | 0x11CAA..=0x11CB0
            | 0x11CB2..=0x11CB3
            | 0x11CB5..=0x11CB6
            | 0x11D31..=0x11D36
            | 0x11D3A
            | 0x11D3C..=0x11D3D
            | 0x11D3F..=0x11D45
            | 0x11D47
            | 0x11D90..=0x11D91
            | 0x11D95
            | 0x11D97
            | 0x11EF3..=0x11EF4
            | 0x16AF0..=0x16AF4
            | 0x16B30..=0x16B36
            | 0x16F4F
            | 0x16F8F..=0x16F92
            | 0x1BC9D..=0x1BC9E
            | 0x1CF00..=0x1CF2D
            | 0x1CF30..=0x1CF46
            | 0x1D167..=0x1D169
            | 0x1D17B..=0x1D182
            | 0x1D185..=0x1D18B
            | 0x1D1AA..=0x1D1AD
            | 0x1D242..=0x1D244
            | 0x1DA00..=0x1DA36
            | 0x1DA3B..=0x1DA6C
            | 0x1DA75
            | 0x1DA84
            | 0x1DA9B..=0x1DA9F
            | 0x1DAA1..=0x1DAAF
            | 0x1E000..=0x1E006
            | 0x1E008..=0x1E018
            | 0x1E01B..=0x1E021
            | 0x1E023..=0x1E024
            | 0x1E026..=0x1E02A
            | 0x1E130..=0x1E136
            | 0x1E2EC..=0x1E2EF
            | 0x1E8D0..=0x1E8D6
            | 0x1E944..=0x1E94A
            | 0xE0100..=0xE01EF
    )
}

#[derive(Clone, Debug)]
enum Inline {
    Text(String),
    Code(String),
    Strong(Vec<Inline>),
    Em(Vec<Inline>),
    Strike(Vec<Inline>),
    Link { dest: String, children: Vec<Inline> },
    Image { alt: String, src: String },
}

#[derive(Clone, Debug)]
enum Block {
    Heading {
        level: u8,
        anchor: String,
        inlines: Vec<Inline>,
    },
    Paragraph(Vec<Inline>),
    Code {
        lang: String,
        text: String,
    },
    Quote(Vec<Block>),
    List(Vec<Item>),
    Table {
        header: Vec<String>,
        rows: Vec<Vec<String>>,
    },
    Rule,
}

#[derive(Clone, Debug)]
struct Item {
    marker: String,
    task: Option<bool>,
    blocks: Vec<Block>,
}

struct Marker {
    indent: usize,
    marker: String,
    rest: String,
    task: Option<bool>,
    /// Character column where the item body starts, after the marker and its space.
    content_col: usize,
}

pub(crate) fn render(
    ui: &Ui,
    markdown: &str,
    headings: &Rc<std::cell::RefCell<Vec<(String, El)>>>,
    on_href: Rc<dyn Fn(&str)>,
) -> El {
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or(colors.muted);
    headings.borrow_mut().clear();
    let view = View {
        ui,
        headings,
        on_href: &on_href,
        text: colors.text,
        muted: colors.muted,
        accent: colors.accent,
        link: ensure_contrast(colors.accent, colors.bg, 4.5),
        card: colors.card,
        panel: colors.panel,
        bg: colors.bg,
        line,
        mono: ui.theme().mono_families.clone(),
        unit: ui.theme().spacing.unit,
        radius: ui.theme().radii.medium,
    };
    let root = ui
        .el("help_md")
        .column()
        .fill_width()
        .gap(view.unit * 3.0);
    view.blocks(&root, &parse(markdown), colors.text);
    root
}

struct View<'a> {
    ui: &'a Ui,
    headings: &'a Rc<std::cell::RefCell<Vec<(String, El)>>>,
    on_href: &'a Rc<dyn Fn(&str)>,
    text: remedy_ui::core::Color,
    muted: remedy_ui::core::Color,
    accent: remedy_ui::core::Color,
    link: remedy_ui::core::Color,
    card: remedy_ui::core::Color,
    panel: remedy_ui::core::Color,
    bg: remedy_ui::core::Color,
    line: remedy_ui::core::Color,
    mono: Vec<String>,
    unit: f32,
    radius: f32,
}

impl View<'_> {
    fn blocks(&self, parent: &El, blocks: &[Block], color: remedy_ui::core::Color) {
        for block in blocks {
            let _ = parent.clone().child(self.block(block, color));
        }
    }

    fn block(&self, block: &Block, color: remedy_ui::core::Color) -> El {
        match block {
            Block::Heading { level, anchor, inlines } => self.heading(*level, anchor, inlines),
            Block::Paragraph(inlines) => self.paragraph(inlines, color),
            Block::Code { lang, text } => self.code_block(lang, text),
            Block::Quote(inner) => self.quote(inner),
            Block::List(items) => self.list(items, color),
            Block::Table { header, rows } => self.table(header, rows, color),
            Block::Rule => w::divider(self.ui, false).fill_width(),
        }
    }

    fn heading(&self, level: u8, anchor: &str, inlines: &[Inline]) -> El {
        let variant = match level {
            1 => "1",
            2 => "2",
            _ => "3",
        };
        let (rich, links) = self.write_inlines(inlines, false);
        let plain = inline_text(inlines);
        let el = self
            .ui
            .el("heading")
            .variant(variant)
            .fill_width()
            .text(rich)
            .data_text()
            .selectable(true)
            .focus_target()
            .on_link(self.follow(links))
            .semantics(Semantics::new(Role::Heading(level.clamp(1, 6))).label(plain));
        if level == 2 {
            let _ = el
                .clone()
                .style(StylePatch::new().border_side(BorderEdge::Bottom, BorderSide::new(1.0, self.line)));
        }
        if level > 1 {
            let top = if level == 2 { self.unit * 2.0 } else { self.unit };
            let _ = el.clone().padding(Insets {
                top,
                end: 0.0,
                bottom: 0.0,
                start: 0.0,
            });
        }
        {
            let mut map = self.headings.borrow_mut();
            if !map.iter().any(|(id, _)| id == anchor) {
                map.push((anchor.to_string(), el.clone()));
            }
        }
        el
    }

    fn paragraph(&self, inlines: &[Inline], color: remedy_ui::core::Color) -> El {
        let segments = segments(inlines);
        if segments.len() == 1 {
            return self.segment(&segments[0], color);
        }
        let col = self.ui.el("help_p").column().fill_width().gap(self.unit * 2.0);
        for segment in &segments {
            let _ = col.clone().child(self.segment(segment, color));
        }
        col
    }

    fn segment(&self, segment: &Seg, color: remedy_ui::core::Color) -> El {
        match segment {
            Seg::Text(inlines) => {
                let (rich, links) = self.write_inlines(inlines, false);
                self.ui
                    .el("label")
                    .fill_width()
                    .text(rich)
                    .data_text()
                    .selectable(true)
                    .on_link(self.follow(links))
                    .style(StylePatch::new().foreground(color))
            }
            Seg::Image { alt, src } => self.image(alt, src),
        }
    }

    fn image(&self, alt: &str, src: &str) -> El {
        let Some(image) = bundled_image(src) else {
            let label = if alt.is_empty() { src.to_string() } else { alt.to_string() };
            return w::text(self.ui, label)
                .data_text()
                .style(StylePatch::new().foreground(self.muted));
        };
        let natural_w = image.width as f32;
        let natural_h = image.height as f32;
        let capped_w = natural_w.min(840.0);
        let capped_h = if natural_w > 0.0 {
            natural_h * (capped_w / natural_w)
        } else {
            natural_h
        };
        w::image(self.ui, image, alt.to_string())
            .layout(|l| {
                l.width = Length::Px(capped_w);
                l.height = Length::Px(capped_h);
                l.max_width = Length::Percent(1.0);
                l.shrink = 1.0;
            })
            .style(
                StylePatch::new()
                    .background(self.card)
                    .radius(self.radius)
                    .border(self.line)
                    .border_width(1.0),
            )
    }

    fn code_block(&self, lang: &str, text: &str) -> El {
        let mut patch = TextStylePatch::default();
        patch.families = Some(self.mono.clone());
        patch.size = Some(13.0);
        let mut rich = RichText::new();
        rich.push_styled(text, patch);
        let label = if lang.is_empty() {
            "Code".to_string()
        } else {
            format!("Code, {lang}")
        };
        let body = self
            .ui
            .el("md_code")
            .text(rich)
            .data_text()
            .selectable(true)
            .nowrap()
            .style(StylePatch::new().foreground(self.text));
        self.ui
            .el("md_code_scroll")
            .fill_width()
            .scroll_x()
            .clip()
            .padding(Insets {
                top: self.unit * 2.0,
                end: self.unit * 3.0,
                bottom: self.unit * 2.0,
                start: self.unit * 3.0,
            })
            .style(
                StylePatch::new()
                    .background(self.card)
                    .radius(self.radius)
                    .border(self.line)
                    .border_width(1.0),
            )
            .child(body)
            .semantics(Semantics::new(Role::Group).label(label))
    }

    fn quote(&self, inner: &[Block]) -> El {
        let body = self.ui.el("md_quote_body").column().grow(1.0).gap(self.unit * 2.0);
        self.blocks(&body, inner, self.muted);
        self.ui
            .el("md_quote")
            .fill_width()
            .padding(Insets {
                top: self.unit * 2.0,
                end: self.unit * 3.0,
                bottom: self.unit * 2.0,
                start: self.unit * 3.0,
            })
            .style(
                StylePatch::new()
                    .background(self.panel)
                    .foreground(self.muted)
                    .border_side(BorderEdge::Start, BorderSide::new(3.0, self.accent)),
            )
            .child(body)
            .semantics(Semantics::new(Role::Group).label("Quote"))
    }

    fn list(&self, items: &[Item], color: remedy_ui::core::Color) -> El {
        let list = self
            .ui
            .el("md_list")
            .column()
            .fill_width()
            .gap(self.unit)
            .semantics(Semantics::new(Role::List));
        for (index, item) in items.iter().enumerate() {
            let marker = item_marker(item, index);
            let body = self.ui.el("md_item_body").column().grow(1.0).gap(self.unit);
            self.blocks(&body, &item.blocks, color);
            let mark = self
                .ui
                .el("md_marker")
                .text(RichText::plain(marker))
                .data_text()
                .nowrap()
                .style(StylePatch::new().foreground(color));
            let row = self
                .ui
                .el("md_item")
                .row()
                .fill_width()
                .gap(self.unit * 2.0)
                .child(mark)
                .child(body)
                .semantics(Semantics::new(Role::ListItem));
            let _ = list.clone().child(row);
        }
        list
    }

    fn table(&self, header: &[String], rows: &[Vec<String>], color: remedy_ui::core::Color) -> El {
        let n = header
            .len()
            .max(rows.iter().map(Vec::len).max().unwrap_or(0))
            .max(1);
        let table = self
            .ui
            .el("md_table")
            .fill_width()
            .layout(|l| {
                l.display = Display::Grid;
                l.columns = vec![Track::MinMax(Box::new(Track::MinContent), Box::new(Track::Fr(1.0))); n];
            })
            .semantics(Semantics::new(Role::Table));
        for (index, cell) in pad(header, n).iter().enumerate() {
            let _ = table.clone().child(self.cell(cell, true, color, index, n));
        }
        for row in rows {
            for (index, cell) in pad(row, n).iter().enumerate() {
                let _ = table.clone().child(self.cell(cell, false, color, index, n));
            }
        }
        self.ui
            .el("md_table_scroll")
            .fill_width()
            .scroll_x()
            .clip()
            .style(
                StylePatch::new()
                    .background(self.bg)
                    .radius(self.radius)
                    .border(self.line)
                    .border_width(1.0),
            )
            .child(table)
    }

    fn cell(&self, text: &str, head: bool, color: remedy_ui::core::Color, index: usize, n: usize) -> El {
        let (rich, links) = self.write_inlines(&parse_inlines(text), false);
        let plain = inline_text(&parse_inlines(text));
        let role = if head { Role::ColumnHeader } else { Role::Cell };
        let mut patch = StylePatch::new().border_side(BorderEdge::Bottom, BorderSide::new(1.0, self.line));
        if index + 1 < n {
            patch = patch.border_side(BorderEdge::End, BorderSide::new(1.0, self.line));
        }
        if head {
            patch = patch.background(self.card).foreground(self.text);
        } else {
            patch = patch.foreground(color);
        }
        self.ui
            .el("md_cell")
            .variant(if head { "head" } else { "" })
            .text(rich)
            .data_text()
            .selectable(true)
            .on_link(self.follow(links))
            .padding(Insets {
                top: self.unit * 1.5,
                end: self.unit * 2.0,
                bottom: self.unit * 1.5,
                start: self.unit * 2.0,
            })
            .style(patch)
            .semantics(Semantics::new(role).label(plain))
    }

    fn write_inlines(&self, nodes: &[Inline], in_link: bool) -> (RichText, Vec<String>) {
        let mut writer = Writer {
            text: RichText::new(),
            links: Vec::new(),
            mono: self.mono.clone(),
            accent: self.accent,
            link_color: self.link,
        };
        writer.write(nodes, in_link);
        (writer.text, writer.links)
    }

    fn follow(&self, links: Vec<String>) -> impl Fn(u64) + 'static {
        let links = Rc::new(links);
        let on_href = Rc::clone(self.on_href);
        move |id: u64| {
            if let Some(dest) = usize::try_from(id).ok().and_then(|i| links.get(i)) {
                on_href(dest);
            }
        }
    }
}

struct Writer {
    text: RichText,
    links: Vec<String>,
    mono: Vec<String>,
    accent: remedy_ui::core::Color,
    link_color: remedy_ui::core::Color,
}

impl Writer {
    fn write(&mut self, nodes: &[Inline], in_link: bool) {
        for node in nodes {
            match node {
                Inline::Text(s) => self.text.push_text(s),
                Inline::Code(s) => {
                    let start = self.text.len();
                    self.text.push_text(s);
                    let end = self.text.len();
                    if end > start {
                        let mut patch = TextStylePatch::default();
                        patch.families = Some(self.mono.clone());
                        patch.weight = Some(600.0);
                        patch.size = Some(13.0);
                        if !in_link {
                            patch.color = Some(self.accent);
                        }
                        self.text.add_span(start..end, patch);
                    }
                }
                Inline::Strong(inner) => self.wrap(inner, in_link, TextStylePatch::bold()),
                Inline::Em(inner) => self.wrap(inner, in_link, TextStylePatch::italic()),
                Inline::Strike(inner) => self.wrap(inner, in_link, TextStylePatch::strikethrough()),
                Inline::Link { dest, children } => {
                    let start = self.text.len();
                    self.write(children, true);
                    let end = self.text.len();
                    if end > start {
                        let id = self.links.len() as u64;
                        self.links.push(dest.clone());
                        let mut patch = TextStylePatch::link(id);
                        patch.color = Some(self.link_color);
                        self.text.add_span(start..end, patch);
                    }
                }
                Inline::Image { alt, .. } => {
                    if !alt.is_empty() {
                        self.text.push_text(alt);
                    }
                }
            }
        }
    }

    fn wrap(&mut self, inner: &[Inline], in_link: bool, patch: TextStylePatch) {
        let start = self.text.len();
        self.write(inner, in_link);
        let end = self.text.len();
        if end > start {
            self.text.add_span(start..end, patch);
        }
    }
}

enum Seg {
    Text(Vec<Inline>),
    Image { alt: String, src: String },
}

fn segments(inlines: &[Inline]) -> Vec<Seg> {
    let mut out = Vec::new();
    let mut buf = Vec::new();
    for inline in inlines {
        if let Inline::Image { alt, src } = inline {
            if buf.iter().any(|node| !is_blank(node)) {
                out.push(Seg::Text(std::mem::take(&mut buf)));
            } else {
                buf.clear();
            }
            out.push(Seg::Image {
                alt: alt.clone(),
                src: src.clone(),
            });
        } else {
            buf.push(inline.clone());
        }
    }
    if buf.iter().any(|node| !is_blank(node)) {
        out.push(Seg::Text(buf));
    }
    if out.is_empty() {
        out.push(Seg::Text(Vec::new()));
    }
    out
}

fn is_blank(inline: &Inline) -> bool {
    match inline {
        Inline::Text(s) => s.trim().is_empty(),
        _ => false,
    }
}

fn item_marker(item: &Item, index: usize) -> String {
    if let Some(done) = item.task {
        return if done { "\u{2611}".to_string() } else { "\u{2610}".to_string() };
    }
    let ordered = item.marker.chars().next().is_some_and(|c| c.is_ascii_digit());
    if ordered {
        item.marker.clone()
    } else if item.marker.is_empty() {
        format!("{}.", index + 1)
    } else {
        "\u{2022}".to_string()
    }
}

fn pad(cells: &[String], n: usize) -> Vec<String> {
    let mut out: Vec<String> = cells.iter().take(n).cloned().collect();
    while out.len() < n {
        out.push(String::new());
    }
    out
}

fn bundled_image(src: &str) -> Option<Image> {
    let key = normalize_asset(src);
    let (slot, bytes) = match key.as_str() {
        "assets/hero_logo_color_on_dark.png" | "hero_logo_color_on_dark.png" => (&HERO_IMG, HERO),
        "assets/previews/hero_logo_color_on_dark.png" | "previews/hero_logo_color_on_dark.png" => {
            (&HERO_PREVIEW_IMG, HERO_PREVIEW)
        }
        "assets/remedy-partner-architecture.jpg" | "remedy-partner-architecture.jpg" => (&ARCH_IMG, ARCH),
        "assets/previews/remedy-partner-architecture.jpg" | "previews/remedy-partner-architecture.jpg" => {
            (&ARCH_PREVIEW_IMG, ARCH_PREVIEW)
        }
        _ => return None,
    };
    slot.get_or_init(|| decode_image(bytes)).clone()
}

static HERO_IMG: OnceLock<Option<Image>> = OnceLock::new();
static HERO_PREVIEW_IMG: OnceLock<Option<Image>> = OnceLock::new();
static ARCH_IMG: OnceLock<Option<Image>> = OnceLock::new();
static ARCH_PREVIEW_IMG: OnceLock<Option<Image>> = OnceLock::new();

fn decode_image(bytes: &'static [u8]) -> Option<Image> {
    let decoded = decode(bytes, None).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}

fn normalize_asset(src: &str) -> String {
    let mut s = src.trim();
    while let Some(rest) = s.strip_prefix("./") {
        s = rest;
    }
    s.trim_start_matches('/').to_string()
}

fn parse(text: &str) -> Vec<Block> {
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let lines: Vec<&str> = normalized.split('\n').collect();
    parse_lines(&lines)
}

fn parse_lines(lines: &[&str]) -> Vec<Block> {
    let n = lines.len();
    let mut i = 0;
    let mut blocks = Vec::new();
    while i < n {
        let line = lines[i];
        if line.trim().is_empty() {
            i += 1;
            continue;
        }
        if let Some((flen, lang)) = fence_open(line) {
            i += 1;
            let mut body = Vec::new();
            while i < n && !fence_close(lines[i], flen) {
                body.push(lines[i]);
                i += 1;
            }
            if i < n {
                i += 1;
            }
            blocks.push(Block::Code {
                lang,
                text: body.join("\n"),
            });
            continue;
        }
        if atx_prefix(line) {
            if let Some((level, content)) = parse_atx(line) {
                let nodes = parse_inlines(&content);
                blocks.push(Block::Heading {
                    level,
                    anchor: heading_anchor(&inline_text(&nodes)),
                    inlines: nodes,
                });
            }
            i += 1;
            continue;
        }
        if is_hr(line) {
            blocks.push(Block::Rule);
            i += 1;
            continue;
        }
        if line.contains('|') && i + 1 < n && is_sep(lines[i + 1]) {
            let header = split_row(line);
            i += 2;
            let mut rows = Vec::new();
            while i < n && lines[i].contains('|') && !lines[i].trim().is_empty() {
                rows.push(split_row(lines[i]));
                i += 1;
            }
            blocks.push(Block::Table { header, rows });
            continue;
        }
        if line.trim_start().starts_with('>') {
            let mut quoted = Vec::new();
            while i < n && lines[i].trim_start().starts_with('>') {
                let rest = &lines[i].trim_start()[1..];
                let rest = rest.strip_prefix(' ').unwrap_or(rest);
                quoted.push(rest.to_string());
                i += 1;
            }
            blocks.push(Block::Quote(parse(&quoted.join("\n"))));
            continue;
        }
        if let Some(marker) = list_marker(line) {
            if marker.indent == 0 {
                blocks.push(Block::List(parse_list(lines, &mut i, 0)));
                continue;
            }
        }
        let mut para = vec![line];
        i += 1;
        while i < n && !lines[i].trim().is_empty() {
            if paragraph_break(lines, i) {
                break;
            }
            para.push(lines[i]);
            i += 1;
        }
        let text = para
            .iter()
            .map(|line| line.trim())
            .collect::<Vec<_>>()
            .join(" ");
        blocks.push(Block::Paragraph(parse_inlines(&text)));
    }
    blocks
}

fn parse_list(lines: &[&str], i: &mut usize, marker_col: usize) -> Vec<Item> {
    let n = lines.len();
    let mut items = Vec::new();
    while *i < n {
        let marker = if lines[*i].trim().is_empty() {
            None
        } else {
            list_marker(lines[*i])
        };
        if !marker.as_ref().is_some_and(|m| m.indent == marker_col) {
            if lines[*i].trim().is_empty() {
                let mut j = *i + 1;
                while j < n && lines[j].trim().is_empty() {
                    j += 1;
                }
                let next = if j < n { list_marker(lines[j]) } else { None };
                if next.is_some_and(|m| m.indent == marker_col) {
                    *i = j;
                    continue;
                }
            }
            break;
        }
        let marker = marker.expect("marker");
        let content_col = marker.content_col;
        let mut item_lines = vec![marker.rest];
        let task = marker.task;
        let token = marker.marker;
        *i += 1;
        while *i < n {
            if lines[*i].trim().is_empty() {
                let mut j = *i + 1;
                while j < n && lines[j].trim().is_empty() {
                    j += 1;
                }
                if j >= n {
                    break;
                }
                let next = list_marker(lines[j]);
                if next.as_ref().is_some_and(|m| m.indent <= marker_col) {
                    break;
                }
                let ind = leading_ws(lines[j]);
                if ind < content_col && !next.as_ref().is_some_and(|m| m.indent > marker_col) {
                    break;
                }
                item_lines.push(String::new());
                *i += 1;
                continue;
            }
            let ind = leading_ws(lines[*i]);
            let next = list_marker(lines[*i]);
            if next.as_ref().is_some_and(|m| m.indent == marker_col) {
                break;
            }
            if ind < content_col && !next.as_ref().is_some_and(|m| m.indent > marker_col) {
                break;
            }
            let stripped = if char_len(lines[*i]) >= content_col {
                slice_chars(lines[*i], content_col).to_string()
            } else {
                lines[*i].trim_start().to_string()
            };
            item_lines.push(stripped);
            *i += 1;
        }
        items.push(Item {
            marker: token,
            task,
            blocks: parse(&item_lines.join("\n")),
        });
    }
    items
}

fn paragraph_break(lines: &[&str], i: usize) -> bool {
    let line = lines[i];
    atx_prefix(line)
        || fence_open(line).is_some()
        || is_hr(line)
        || line.trim_start().starts_with('>')
        || list_marker(line).is_some_and(|m| m.indent == 0)
        || (line.contains('|') && i + 1 < lines.len() && is_sep(lines[i + 1]))
}

fn fence_open(line: &str) -> Option<(usize, String)> {
    let trimmed = line.trim_start();
    let ticks = trimmed.bytes().take_while(|b| *b == b'`').count();
    if ticks < 3 {
        return None;
    }
    let info = trimmed[ticks..].trim();
    let lang = info.split_whitespace().next().unwrap_or("").to_string();
    Some((ticks, lang))
}

fn fence_close(line: &str, flen: usize) -> bool {
    let st = line.trim();
    !st.is_empty() && st.bytes().all(|b| b == b'`') && st.len() >= flen
}

fn atx_prefix(line: &str) -> bool {
    let bytes = line.as_bytes();
    let mut i = 0;
    while i < bytes.len() && bytes[i] == b'#' {
        i += 1;
    }
    (1..=6).contains(&i) && matches!(bytes.get(i), Some(b' ' | b'\t'))
}

fn parse_atx(line: &str) -> Option<(u8, String)> {
    if !atx_prefix(line) {
        return None;
    }
    let hashes = line.bytes().take_while(|b| *b == b'#').count();
    let rest = line[hashes..].trim();
    let content = strip_closing_hashes(rest).trim();
    if content.is_empty() {
        None
    } else {
        Some((hashes as u8, content.to_string()))
    }
}

fn strip_closing_hashes(s: &str) -> &str {
    let t = s.trim_end();
    let hashless = t.trim_end_matches('#');
    if hashless.len() < t.len() {
        hashless.trim_end()
    } else {
        t
    }
}

fn is_hr(line: &str) -> bool {
    let t = line.trim();
    let mut chars = t.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if first != '-' && first != '*' && first != '_' {
        return false;
    }
    let mut count = 1;
    for ch in chars {
        if ch == first {
            count += 1;
        } else if !ch.is_whitespace() {
            return false;
        }
    }
    count >= 3
}

fn split_row(line: &str) -> Vec<String> {
    let mut t = line.trim();
    if let Some(rest) = t.strip_prefix('|') {
        t = rest;
    }
    if let Some(rest) = t.strip_suffix('|') {
        t = rest;
    }
    let mut cells = Vec::new();
    let mut buf = String::new();
    let mut code = false;
    for ch in t.chars() {
        if ch == '`' {
            code = !code;
            buf.push(ch);
        } else if ch == '|' && !code {
            cells.push(buf.trim().to_string());
            buf.clear();
        } else {
            buf.push(ch);
        }
    }
    cells.push(buf.trim().to_string());
    cells
}

fn is_sep(line: &str) -> bool {
    if !line.contains('|') {
        return false;
    }
    let cells = split_row(line);
    if cells.is_empty() {
        return false;
    }
    let mut ok = false;
    for cell in cells {
        let t = cell.trim();
        if t.is_empty() {
            return false;
        }
        let dashes = t.trim_matches(':');
        if dashes.is_empty() || dashes.chars().any(|ch| ch != '-') {
            return false;
        }
        ok = true;
    }
    ok
}

fn list_marker(line: &str) -> Option<Marker> {
    let indent = line.chars().take_while(|c| *c == ' ' || *c == '\t').count();
    let after_indent = slice_chars(line, indent);
    let (marker, after_marker) = take_marker(after_indent)?;
    let ws = after_marker.chars().take_while(|c| *c == ' ' || *c == '\t').count();
    if ws == 0 {
        return None;
    }
    let content_col = indent + char_len(&marker) + ws;
    let mut rest = slice_chars(line, content_col).to_string();
    let mut task = None;
    if marker.chars().all(|c| matches!(c, '*' | '+' | '-')) {
        if let Some((done, remain)) = task_prefix(&rest) {
            task = Some(done);
            rest = remain;
        }
    }
    Some(Marker {
        indent,
        marker,
        rest,
        task,
        content_col,
    })
}

fn take_marker(after_indent: &str) -> Option<(String, &str)> {
    let bytes = after_indent.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    if matches!(bytes[0], b'*' | b'+' | b'-') {
        return Some((after_indent[..1].to_string(), &after_indent[1..]));
    }
    let digits = after_indent.chars().take_while(|c| c.is_ascii_digit()).count();
    if digits == 0 {
        return None;
    }
    let rest = slice_chars(after_indent, digits);
    if rest.starts_with('.') || rest.starts_with(')') {
        let marker_bytes = digits + 1;
        Some((after_indent[..marker_bytes].to_string(), &after_indent[marker_bytes..]))
    } else {
        None
    }
}

fn task_prefix(rest: &str) -> Option<(bool, String)> {
    let bytes = rest.as_bytes();
    if bytes.len() < 4 || bytes[0] != b'[' || bytes[2] != b']' {
        return None;
    }
    let mark = bytes[1];
    if mark != b' ' && mark != b'x' && mark != b'X' {
        return None;
    }
    let after = &rest[3..];
    let ws = after.chars().take_while(|c| *c == ' ' || *c == '\t').count();
    if ws == 0 {
        return None;
    }
    Some((mark == b'x' || mark == b'X', slice_chars(after, ws).to_string()))
}

fn leading_ws(line: &str) -> usize {
    char_len(line) - char_len(line.trim_start())
}

fn char_len(s: &str) -> usize {
    s.chars().count()
}

fn slice_chars(line: &str, n: usize) -> &str {
    match line.char_indices().nth(n) {
        Some((idx, _)) => &line[idx..],
        None => "",
    }
}

fn parse_inlines(s: &str) -> Vec<Inline> {
    let mut out = Vec::new();
    let mut buf = String::new();
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'`' {
            if let Some(rel) = s[i + 1..].find('`') {
                let j = i + 1 + rel;
                flush(&mut buf, &mut out);
                out.push(Inline::Code(s[i + 1..j].to_string()));
                i = j + 1;
                continue;
            }
        }
        if s[i..].starts_with("![") {
            if let Some((end, alt, src)) = scan_bracket(s, i, true) {
                flush(&mut buf, &mut out);
                out.push(Inline::Image { alt, src });
                i = end;
                continue;
            }
        }
        if bytes[i] == b'[' {
            if let Some((end, text, dest)) = scan_bracket(s, i, false) {
                flush(&mut buf, &mut out);
                out.push(Inline::Link {
                    dest,
                    children: parse_inlines(&text),
                });
                i = end;
                continue;
            }
        }
        if s[i..].starts_with("**") || s[i..].starts_with("__") {
            let delim = &s[i..i + 2];
            if let Some(rel) = s[i + 2..].find(delim) {
                let j = i + 2 + rel;
                flush(&mut buf, &mut out);
                out.push(Inline::Strong(parse_inlines(&s[i + 2..j])));
                i = j + 2;
                continue;
            }
        }
        if s[i..].starts_with("~~") {
            if let Some(rel) = s[i + 2..].find("~~") {
                let j = i + 2 + rel;
                flush(&mut buf, &mut out);
                out.push(Inline::Strike(parse_inlines(&s[i + 2..j])));
                i = j + 2;
                continue;
            }
        }
        if bytes[i] == b'*' && !s[i..].starts_with("**") {
            if let Some(rel) = s[i + 1..].find('*') {
                let j = i + 1 + rel;
                if !s[j..].starts_with("**") {
                    flush(&mut buf, &mut out);
                    out.push(Inline::Em(parse_inlines(&s[i + 1..j])));
                    i = j + 1;
                    continue;
                }
            }
        }
        let ch = s[i..].chars().next().unwrap_or('\u{FFFD}');
        buf.push(ch);
        i += ch.len_utf8();
    }
    flush(&mut buf, &mut out);
    out
}

fn flush(buf: &mut String, out: &mut Vec<Inline>) {
    if !buf.is_empty() {
        out.push(Inline::Text(std::mem::take(buf)));
    }
}

/// `![label](dest)` or `[label](dest)`, optional `"title"`. Returns the index past the `)`.
fn scan_bracket(s: &str, i: usize, bang: bool) -> Option<(usize, String, String)> {
    let rest = if bang {
        s.get(i..)?.strip_prefix("![")?
    } else {
        s.get(i..)?.strip_prefix('[')?
    };
    let close = rest.find(']')?;
    if rest.as_bytes().get(close + 1) != Some(&b'(') {
        return None;
    }
    let label = rest[..close].to_string();
    let after = &rest[close + 2..];
    let dest_len = after.find(|c: char| c == ')' || c.is_whitespace())?;
    if dest_len == 0 {
        return None;
    }
    let dest = after[..dest_len].to_string();
    let mut cursor = close + 2 + dest_len;
    let tail = rest.get(cursor..)?;
    if tail.starts_with(|c: char| c.is_whitespace()) {
        let ws = tail.chars().take_while(|c| c.is_whitespace()).map(char::len_utf8).sum::<usize>();
        let titled = &tail[ws..];
        if let Some(body) = titled.strip_prefix('"') {
            if let Some(end_q) = body.find('"') {
                if body.as_bytes().get(end_q + 1) == Some(&b')') {
                    cursor += ws + 1 + end_q + 1;
                }
            }
        }
    }
    if rest.as_bytes().get(cursor) != Some(&b')') {
        return None;
    }
    let base = if bang { i + 2 } else { i + 1 };
    Some((base + cursor + 1, label, dest))
}

fn inline_text(nodes: &[Inline]) -> String {
    let mut out = String::new();
    for node in nodes {
        match node {
            Inline::Text(s) | Inline::Code(s) => out.push_str(s),
            Inline::Image { alt, .. } => out.push_str(alt),
            Inline::Link { children, .. } => out.push_str(&inline_text(children)),
            Inline::Strong(inner) | Inline::Em(inner) | Inline::Strike(inner) => {
                out.push_str(&inline_text(inner));
            }
        }
    }
    out
}

/// `%HH` bytes, as `decodeURIComponent` does for the anchors in this manual.
pub(crate) fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(v) = u8::from_str_radix(std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or(""), 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

struct Walk {
    headings: Vec<String>,
    links: Vec<String>,
    images: Vec<String>,
    tables: usize,
    tasks: usize,
}

fn walk(blocks: &[Block], acc: &mut Walk) {
    for block in blocks {
        match block {
            Block::Heading { anchor, inlines, .. } => {
                acc.headings.push(anchor.clone());
                walk_inlines(inlines, acc);
            }
            Block::Paragraph(inlines) => walk_inlines(inlines, acc),
            Block::Quote(inner) => walk(inner, acc),
            Block::List(items) => {
                for item in items {
                    if item.task.is_some() {
                        acc.tasks += 1;
                    }
                    walk(&item.blocks, acc);
                }
            }
            Block::Table { header, rows, .. } => {
                acc.tables += 1;
                for cell in header.iter().chain(rows.iter().flatten()) {
                    walk_inlines(&parse_inlines(cell), acc);
                }
            }
            Block::Code { .. } | Block::Rule => {}
        }
    }
}

fn walk_inlines(nodes: &[Inline], acc: &mut Walk) {
    for node in nodes {
        match node {
            Inline::Link { dest, children } => {
                acc.links.push(dest.clone());
                walk_inlines(children, acc);
            }
            Inline::Image { src, .. } => acc.images.push(src.clone()),
            Inline::Strong(inner) | Inline::Em(inner) | Inline::Strike(inner) => walk_inlines(inner, acc),
            Inline::Text(_) | Inline::Code(_) => {}
        }
    }
}

fn nested_lists(blocks: &[Block], depth: usize) -> usize {
    let mut n = 0;
    for block in blocks {
        match block {
            Block::List(items) => {
                if depth > 0 {
                    n += 1;
                }
                for item in items {
                    n += nested_lists(&item.blocks, depth + 1);
                }
            }
            Block::Quote(inner) => n += nested_lists(inner, depth),
            _ => {}
        }
    }
    n
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn anchors_match_the_tauri_manual() {
        assert_eq!(heading_anchor("What’s new (start here)"), "whats-new-start-here");
        assert_eq!(
            heading_anchor("What\u{2019}s new (start here)"),
            "whats-new-start-here"
        );
        assert_eq!(heading_anchor("Always-ready desktop"), "always-ready-desktop");
        assert_eq!(heading_anchor("Install Linux"), "install-linux");
        assert_eq!(heading_anchor("日本語 設定"), "日本語-設定");
        assert_eq!(heading_anchor("License (Apache-2.0)"), "license-apache-20");
        assert_eq!(
            heading_anchor("0.70.5 - Claude works on every model"),
            "0705---claude-works-on-every-model"
        );
        assert_eq!(heading_anchor("Quick start (60 seconds)"), "quick-start-60-seconds");
        assert_eq!(
            heading_anchor("Remedy \u{2014} Owner\u{2019}s Manual Overview"),
            "remedy--owners-manual-overview"
        );
    }

    #[test]
    fn every_chapter_anchor_resolves() {
        for article in super::super::catalog::articles() {
            let mut acc = Walk {
                headings: Vec::new(),
                links: Vec::new(),
                images: Vec::new(),
                tables: 0,
                tasks: 0,
            };
            let blocks = parse(&article.body);
            assert!(
                matches!(blocks.first(), Some(Block::Heading { level: 1, .. })),
                "{}",
                article.id
            );
            walk(&blocks, &mut acc);
            assert!(!acc.headings.is_empty(), "{}", article.id);
            for link in &acc.links {
                if let Some(anchor) = link.strip_prefix('#') {
                    let anchor = percent_decode(anchor);
                    assert!(
                        acc.headings.iter().any(|h| h == &anchor),
                        "{} missing #{anchor}",
                        article.id
                    );
                }
            }
        }
    }

    #[test]
    fn overview_and_release_notes_keep_their_shape() {
        let overview = super::super::catalog::get_article("00-overview").unwrap();
        let mut acc = Walk {
            headings: Vec::new(),
            links: Vec::new(),
            images: Vec::new(),
            tables: 0,
            tasks: 0,
        };
        walk(&parse(&overview.body), &mut acc);
        assert_eq!(
            acc.headings.first().map(String::as_str),
            Some("remedy--owners-manual-overview")
        );
        assert_eq!(
            acc.images,
            vec![
                "assets/hero_logo_color_on_dark.png".to_string(),
                "assets/remedy-partner-architecture.jpg".to_string(),
            ]
        );
        assert!(acc.tables >= 5, "tables {}", acc.tables);

        let notes = super::super::catalog::get_article("13-whats-new").unwrap();
        let mut notes_acc = Walk {
            headings: Vec::new(),
            links: Vec::new(),
            images: Vec::new(),
            tables: 0,
            tasks: 0,
        };
        walk(&parse(&notes.body), &mut notes_acc);
        assert!(notes_acc.headings.iter().any(|h| h == "0705---claude-works-on-every-model"));

        let soak = super::super::catalog::get_article("computer-use-soak").unwrap();
        let mut soak_acc = Walk {
            headings: Vec::new(),
            links: Vec::new(),
            images: Vec::new(),
            tables: 0,
            tasks: 0,
        };
        walk(&parse(&soak.body), &mut soak_acc);
        assert_eq!(soak_acc.tasks, 27);

        let security = super::super::catalog::get_article("04-security-and-data").unwrap();
        assert!(nested_lists(&parse(&security.body), 0) >= 1);
    }
}
