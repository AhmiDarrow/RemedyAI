//! Chat markdown: paragraphs, filled inline-code chips, fenced code, diffs, images.
//!
//! The engine's markdown widget does not paint chip backgrounds, so the feed
//! opts in itself with [`SpanBackground::chip`]. A plain paragraph stays one
//! text run so a streamed reply is still in the reader view.

use crate::depth::api;
use crate::depth::logic::{
    chat_markdown_url, classify_diff_line, is_diff_language, looks_like_unified_diff,
    remote_image_host, sanitize_assistant_text, DiffLineKind,
};
use remedy_ui::core::Insets;
use remedy_ui::layout::Length;
use remedy_ui::paint::Image;
use remedy_ui::reactive::{waker, Signal};
use remedy_ui::style::StylePatch;
use remedy_ui::text::{RichText, SpanBackground, TextStylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};

const CHIP: f32 = 14.0;

/// Inline markdown as one rich run. Used for the live bubble, which has to
/// update as tokens arrive.
pub fn inline_rich(content: &str, chip: remedy_ui::core::Color) -> RichText {
    let mut rich = RichText::new();
    let code = code_patch(chip);
    for (i, line) in content.split('\n').enumerate() {
        if i > 0 {
            rich.push_text("\n");
        }
        push_inline(&mut rich, line, &code);
    }
    rich
}

/// A finished message: blocks, diffs, and images under the bubble text.
pub fn blocks(ui: &Ui, content: &str, assistant: bool, on_image: impl Fn(String) + Clone + 'static) -> El {
    let text = if assistant {
        sanitize_assistant_text(content)
    } else {
        content.to_string()
    };
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui)
        .gap(unit)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
    if text.trim().is_empty() {
        return col.child(w::text(ui, "…").variant("muted"));
    }
    for block in split_blocks(&text) {
        col = col.child(render_block(ui, &block, on_image.clone()));
    }
    col
}

fn code_patch(chip: remedy_ui::core::Color) -> TextStylePatch {
    let mut code = TextStylePatch::background(SpanBackground::chip(chip, CHIP));
    code.families = Some(vec!["AhmiPrint".into()]);
    code.weight = Some(600.0);
    code
}

fn push_inline(rich: &mut RichText, line: &str, code: &TextStylePatch) {
    let mut rest = line;
    while !rest.is_empty() {
        if let Some(start) = rest.find('`') {
            let (head, tail) = rest.split_at(start);
            if !head.is_empty() {
                rich.push_text(head);
            }
            let after = &tail[1..];
            if let Some(end) = after.find('`') {
                let body = &after[..end];
                if !body.is_empty() && !body.contains('\n') {
                    rich.push_styled(body, code.clone());
                }
                rest = &after[end + 1..];
            } else {
                rich.push_text("`");
                rest = after;
            }
        } else {
            rich.push_text(rest);
            break;
        }
    }
}

enum Block {
    Paragraph(String),
    Code { lang: String, text: String },
    Image { alt: String, url: String },
}

fn split_blocks(text: &str) -> Vec<Block> {
    scan_blocks(text)
}

fn scan_blocks(text: &str) -> Vec<Block> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find("```") {
        push_paragraphs(&mut out, &rest[..start]);
        let after = &rest[start + 3..];
        let fence = after.find("```");
        let (lang, body, next) = if let Some(nl) = after.find('\n') {
            let lang = after[..nl].trim().to_string();
            let body_at = &after[nl + 1..];
            if let Some(end) = body_at.find("```") {
                (lang, body_at[..end].trim_end_matches('\n').to_string(), &body_at[end + 3..])
            } else {
                (lang, body_at.to_string(), "")
            }
        } else if let Some(end) = fence {
            (String::new(), after[..end].to_string(), &after[end + 3..])
        } else {
            (String::new(), after.to_string(), "")
        };
        out.push(Block::Code { lang, text: body });
        if next.is_empty() && !rest[start + 3..].contains("```") && fence.is_none() {
            break;
        }
        rest = next;
        if rest.is_empty() {
            break;
        }
    }
    push_paragraphs(&mut out, rest);
    out
}

fn push_paragraphs(out: &mut Vec<Block>, text: &str) {
    let mut para = String::new();
    for line in text.split('\n') {
        if let Some((alt, url)) = image_line(line.trim()) {
            if !para.trim().is_empty() {
                out.push(Block::Paragraph(para.trim().to_string()));
                para.clear();
            }
            out.push(Block::Image { alt, url });
            continue;
        }
        if line.trim().is_empty() {
            if !para.trim().is_empty() {
                out.push(Block::Paragraph(para.trim().to_string()));
                para.clear();
            }
        } else {
            if !para.is_empty() {
                para.push('\n');
            }
            para.push_str(line);
        }
    }
    if !para.trim().is_empty() {
        out.push(Block::Paragraph(para.trim().to_string()));
    }
}

fn image_line(line: &str) -> Option<(String, String)> {
    if !line.starts_with("![") {
        return None;
    }
    let alt_end = line.find("](")?;
    let alt = line[2..alt_end].to_string();
    let rest = &line[alt_end + 2..];
    let close = rest.find(')')?;
    if close + 1 != rest.len() && rest[close + 1..].trim().is_empty() {
        return Some((alt, rest[..close].to_string()));
    }
    if close + 1 == rest.len() {
        return Some((alt, rest[..close].to_string()));
    }
    None
}

fn render_block(ui: &Ui, block: &Block, on_image: impl Fn(String) + Clone + 'static) -> El {
    let chip = ui.theme().colors.bg.mix_oklab(ui.theme().colors.accent, 0.22);
    match block {
        Block::Paragraph(text) => ui
            .el("bubble-body")
            .text(inline_rich(text, chip))
            .layout(|l| {
                l.width = Length::Percent(1.0);
                l.min_width = Length::Px(0.0);
            }),
        Block::Code { lang, text } => {
            if is_diff_language(lang) || looks_like_unified_diff(text) || crate::depth::logic::should_render_as_diff(text, lang)
            {
                diff_block(ui, text)
            } else {
                code_block(ui, lang, text)
            }
        }
        Block::Image { alt, url } => image_block(ui, alt, url, on_image),
    }
}

fn code_block(ui: &Ui, lang: &str, text: &str) -> El {
    use crate::tokens;
    use remedy_ui::layout::Align;
    let colors = ui.theme().colors.clone();
    let copy = text.to_string();
    let lang_label = if lang.is_empty() { "code".to_string() } else { lang.to_string() };
    let chip = w::text(ui, lang_label)
        .nowrap()
        .style(
            tokens::chip(&colors, false)
                .mono(true)
                .text_size(11.0)
                .padding(Insets {
                    top: 2.0,
                    end: 6.0,
                    bottom: 2.0,
                    start: 6.0,
                }),
        );
    let u = ui.clone();
    let copy_btn = w::icon_button(ui, w::Icon::Copy, "Copy", move || {
        u.copy_text(copy.clone());
    })
    .style(StylePatch::new().foreground(colors.muted));
    let head = w::row(ui)
        .align(Align::Center)
        .gap(tokens::SPACE * 2.0)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(chip)
        .child(ui.el("code-head-flex").layout(|l| {
            l.grow = 1.0;
            l.min_width = Length::Px(0.0);
        }))
        .child(copy_btn);

    let mut body = w::column(ui).gap(0.0).layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    });
    for line in text.lines() {
        let trimmed = line.trim_start();
        let comment = trimmed.starts_with("//")
            || trimmed.starts_with("--")
            || trimmed.starts_with("#")
            || trimmed.starts_with("/*")
            || trimmed.starts_with("*")
            || trimmed.starts_with("*/");
        let fg = if comment { colors.muted } else { colors.text };
        body = body.child(
            w::text(ui, line)
                .style(StylePatch::new().mono(true).text_size(12.5).foreground(fg))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        );
    }

    w::column(ui)
        .gap(tokens::SPACE * 2.0)
        .padding(Insets::all(tokens::SPACE * 2.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.bg)
                .radius(0.0)
                .border(tokens::hairline(&colors))
                .border_width(1.0),
        )
        .child(head)
        .child(body)
}

fn diff_block(ui: &Ui, text: &str) -> El {
    let colors = ui.theme().colors.clone();
    let mut col = w::column(ui)
        .gap(0.0)
        .padding(Insets::all(6.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.bg)
                .radius(ui.theme().radii.small)
                .mono(true),
        );
    for line in text.lines() {
        let kind = classify_diff_line(line);
        let color = match kind {
            DiffLineKind::Add => colors.accent,
            DiffLineKind::Del => colors.danger,
            DiffLineKind::Hunk => colors.muted,
            DiffLineKind::Meta => colors.muted,
            DiffLineKind::Ctx => colors.text,
        };
        col = col.child(
            w::text(ui, line)
                .style(StylePatch::new().foreground(color).mono(true).text_size(12.0))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        );
    }
    col
}

fn image_block(ui: &Ui, alt: &str, url: &str, on_image: impl Fn(String) + Clone + 'static) -> El {
    let safe = chat_markdown_url(url);
    if safe.is_empty() {
        return w::text(ui, alt).variant("muted");
    }
    if remote_image_host(&safe).is_some() {
        let shown = Signal::new(false);
        let src = safe.clone();
        let alt = alt.to_string();
        let open = on_image.clone();
        let u = ui.clone();
        return show(ui, {
            let shown = shown.clone();
            move || shown.get()
        }, {
            let src = src.clone();
            let alt = alt.clone();
            let open = open.clone();
            let u = u.clone();
            move || loaded_image(&u, &src, &alt, open.clone())
        })
        .fallback({
            let u = ui.clone();
            let label = if alt.is_empty() {
                "Load image".to_string()
            } else {
                format!("Load image · {alt}")
            };
            move || {
                let shown = shown.clone();
                let label = label.clone();
                w::button(&u, label, move || shown.set(true)).variant("quiet")
            }
        })
        .el();
    }
    loaded_image(ui, &safe, alt, on_image)
}

fn loaded_image(ui: &Ui, src: &str, alt: &str, on_image: impl Fn(String) + Clone + 'static) -> El {
    let alt = if alt.is_empty() { "Image".to_string() } else { alt.to_string() };
    let src_owned = src.to_string();
    if let Some(image) = decode_local(src) {
        let open = src_owned.clone();
        return w::image(ui, image, alt)
            .layout(|l| {
                l.max_width = Length::Px(360.0);
                l.max_height = Length::Px(280.0);
                l.shrink = 1.0;
            })
            .on_click(move || on_image(open.clone()));
    }
    let slot: Signal<Option<Image>> = Signal::new(None);
    let err: Signal<String> = Signal::new(String::new());
    let slot_set = slot.clone();
    let err_set = err.clone();
    let url = src.to_string();
    if let Ok(wake) = waker() {
        if url.starts_with("http://") || url.starts_with("https://") {
            api::fetch_bytes(&url, move |out| {
                let _ = wake.post(move || match out {
                    Ok(bytes) if bytes.len() <= api::IMAGE_CAP => {
                        if let Some(image) = decode_bytes(&bytes) {
                            slot_set.set(Some(image));
                        } else {
                            err_set.set("Could not read that image.".into());
                        }
                    }
                    Ok(_) => err_set.set("That image is too large to show.".into()),
                    Err(e) => err_set.set(e),
                });
            });
        } else if let Some(bytes) = data_bytes(&url) {
            if let Some(image) = decode_bytes(&bytes) {
                slot.set(Some(image));
            }
        }
    }
    let open = src_owned;
    let u = ui.clone();
    let alt2 = alt.clone();
    let on_show = std::rc::Rc::new(on_image);
    let slot_when = slot.clone();
    show(ui, move || slot_when.get().is_some(), move || {
        let image = slot.get().unwrap_or_else(|| {
            Image::new(1, 1, std::sync::Arc::<[u8]>::from(vec![0, 0, 0, 0]))
        });
        let open = open.clone();
        let on_image = on_show.clone();
        w::image(&u, image, alt2.clone())
            .layout(|l| {
                l.max_width = Length::Px(360.0);
                l.max_height = Length::Px(280.0);
            })
            .on_click(move || on_image(open.clone()))
    })
    .fallback({
        let u = ui.clone();
        let err = err.clone();
        let alt = alt.clone();
        move || {
            let alt = alt.clone();
            w::text(&u, "").text(move || {
                let e = err.get();
                if e.is_empty() { alt.clone() } else { e }
            }).variant("muted")
        }
    })
    .el()
}

fn decode_local(src: &str) -> Option<Image> {
    if src.starts_with("data:image/") {
        return decode_bytes(&data_bytes(src)?);
    }
    let path = file_path(src)?;
    let meta = std::fs::metadata(&path).ok()?;
    if meta.len() > api::IMAGE_CAP as u64 {
        return None;
    }
    let bytes = std::fs::read(&path).ok()?;
    decode_bytes(&bytes)
}

pub(crate) fn file_path(src: &str) -> Option<std::path::PathBuf> {
    if src.len() >= 5 && src[..5].eq_ignore_ascii_case("file:") {
        let rest = src[5..].trim_start_matches('/');
        // file:///C:/... keeps the drive letter after stripping one extra slash on Windows.
        let rest = if rest.len() >= 2 && rest.as_bytes()[1] == b':' {
            rest
        } else if src[5..].starts_with("///") {
            &src[7..]
        } else {
            rest
        };
        return Some(std::path::PathBuf::from(rest));
    }
    let b = src.as_bytes();
    if b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/') {
        return Some(std::path::PathBuf::from(src));
    }
    None
}

fn data_bytes(src: &str) -> Option<Vec<u8>> {
    let comma = src.find(',')?;
    let meta = &src[..comma];
    let data = &src[comma + 1..];
    if meta.to_ascii_lowercase().contains(";base64") {
        api::b64_decode(data)
    } else {
        Some(data.as_bytes().to_vec())
    }
}

fn decode_bytes(bytes: &[u8]) -> Option<Image> {
    if bytes.len() > api::IMAGE_CAP {
        return None;
    }
    let decoded = remedy_ui::image::decode(bytes, Some((960, 960))).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}
