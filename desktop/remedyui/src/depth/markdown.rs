//! Chat markdown: paragraphs, filled inline-code chips, fenced code, diffs, images, charts,
//! and tables, lists, headings and quotes through the engine's CommonMark.
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
                push_markup(rich, head);
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
            push_markup(rich, rest);
            break;
        }
    }
}

fn push_markup(rich: &mut RichText, mut rest: &str) {
    while !rest.is_empty() {
        if let Some((mark, len, patch)) = next_mark(rest) {
            if let Some(end) = rest[len..].find(mark) {
                let inner = &rest[len..len + end];
                if !inner.is_empty() && !inner.contains('\n') {
                    if !mark_is_plain_prefix(rest, len) {
                        rich.push_styled(inner, patch);
                        rest = &rest[len + end + len..];
                        continue;
                    }
                }
            }
        }
        let ch = rest.chars().next().unwrap();
        rich.push_text(&rest[..ch.len_utf8()]);
        rest = &rest[ch.len_utf8()..];
    }
}

fn next_mark(s: &str) -> Option<(&'static str, usize, TextStylePatch)> {
    if s.starts_with("**") {
        Some(("**", 2, TextStylePatch::bold()))
    } else if s.starts_with("~~") {
        Some(("~~", 2, TextStylePatch::strikethrough()))
    } else if s.starts_with('*') {
        Some(("*", 1, TextStylePatch::italic()))
    } else {
        None
    }
}

/// A list marker like "1. * item" or "* item" should stay literal when the
/// asterisk is followed by a space and there is no closing mark later.
fn mark_is_plain_prefix(s: &str, len: usize) -> bool {
    len == 1 && s[len..].starts_with(' ')
}

enum Block {
    Paragraph(String),
    Code { lang: String, text: String },
    Image { alt: String, url: String },
    Chart(Chart),
}

/// A ```chart block: `{"type":"bar"|"line","title":"…","unit":"…","labels":[…],
/// "series":[{"name":"…","values":[…]}]}`. Anything else stays a code block.
#[derive(Clone, Debug, PartialEq)]
struct Chart {
    kind: String,
    title: String,
    unit: String,
    labels: Vec<String>,
    series: Vec<(String, Vec<f32>)>,
}

fn parse_chart(text: &str) -> Option<Chart> {
    let v: serde_json::Value = serde_json::from_str(text.trim()).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").trim().to_string();
    let kind = match s("type").to_ascii_lowercase().as_str() {
        "line" => "line",
        _ => "bar",
    }
    .to_string();
    let labels: Vec<String> = v
        .get("labels")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().map(|l| l.as_str().map(str::to_string).unwrap_or_else(|| l.to_string())).collect())
        .unwrap_or_default();
    let numbers = |x: &serde_json::Value| -> Vec<f32> {
        x.as_array().map(|a| a.iter().filter_map(|n| n.as_f64()).map(|n| n as f32).collect()).unwrap_or_default()
    };
    let mut series: Vec<(String, Vec<f32>)> = v
        .get("series")
        .and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .map(|e| (e.get("name").and_then(|n| n.as_str()).unwrap_or("").to_string(), e.get("values").map(numbers).unwrap_or_default()))
                .filter(|(_, vals)| !vals.is_empty())
                .collect()
        })
        .unwrap_or_default();
    if series.is_empty() {
        if let Some(vals) = v.get("values").map(numbers).filter(|v| !v.is_empty()) {
            series.push((String::new(), vals));
        }
    }
    if series.is_empty() || series.iter().any(|(_, vals)| vals.iter().any(|n| !n.is_finite())) {
        return None;
    }
    Some(Chart { kind, title: s("title"), unit: s("unit"), labels, series })
}

/// A paragraph with block structure the chat's own run cannot draw: a table, a heading,
/// a list or a quote. It goes to the engine's CommonMark.
fn is_structured(text: &str) -> bool {
    let lines: Vec<&str> = text.lines().map(str::trim_start).collect();
    let table = lines.iter().filter(|l| l.starts_with('|')).count() >= 2;
    table
        || lines.iter().any(|l| {
            l.starts_with("# ") || l.starts_with("## ") || l.starts_with("### ") || l.starts_with("> ")
                || l.starts_with("- ") || l.starts_with("+ ")
                || (l.starts_with("* ") && !l[2..].contains('*'))
                || l.split_once(". ").is_some_and(|(n, _)| !n.is_empty() && n.len() <= 3 && n.chars().all(|c| c.is_ascii_digit()))
        })
}

/// A link in a reply opens in Remedy's browser (the system browser when that cannot take
/// it). Only web links: a reply cannot open a file or run anything.
fn open_link(url: &str) {
    let url = url.trim();
    if url.starts_with("https://") || url.starts_with("http://") {
        crate::settings::open_in_browser_rail(url);
    }
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
        match (lang.eq_ignore_ascii_case("chart"), parse_chart(&body)) {
            (true, Some(chart)) => out.push(Block::Chart(chart)),
            _ => out.push(Block::Code { lang, text: body }),
        }
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
        Block::Paragraph(text) if is_structured(text) => remedy_ui::rich::markdown(ui, text.clone(), open_link).layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        }),
        Block::Chart(chart) => chart_block(ui, chart),
        Block::Paragraph(text) => ui
            .el("bubble-body")
            .text(inline_rich(text, chip))
            .selectable(true)
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

/// A chart she drew in a reply. Bars are labelled rows (label, bar, value); a line is a
/// sparkline per series with its range under it.
fn chart_block(ui: &Ui, chart: &Chart) -> El {
    use crate::tokens;
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui).gap(unit).padding(Insets::all(unit * 2.0)).layout(|l| {
        l.width = Length::Percent(1.0);
        l.min_width = Length::Px(0.0);
    })
    .style(tokens::well_surface(&colors));
    if !chart.title.is_empty() {
        col = col.child(w::text(ui, chart.title.clone()).variant("strong"));
    }
    let fmt = |n: f32| {
        let v = if n.fract() == 0.0 { format!("{n:.0}") } else { format!("{n:.2}") };
        if chart.unit.is_empty() { v } else { format!("{v} {}", chart.unit) }
    };
    if chart.kind == "line" {
        for (name, values) in &chart.series {
            let lo = values.iter().cloned().fold(f32::INFINITY, f32::min);
            let hi = values.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
            let label = if name.is_empty() { chart.title.clone() } else { name.clone() };
            if !name.is_empty() {
                col = col.child(w::text(ui, name.clone()).variant("muted"));
            }
            let line = remedy_ui::viz::sparkline(
                ui,
                label,
                Signal::new(values.clone()),
                remedy_ui::viz::SparklineOptions::default().fill(true).dot(true).height(unit * 14.0),
            );
            let span = match (chart.labels.first(), chart.labels.last()) {
                (Some(a), Some(b)) if chart.labels.len() > 1 => format!("{a} – {b} · low {} · high {}", fmt(lo), fmt(hi)),
                _ => format!("low {} · high {}", fmt(lo), fmt(hi)),
            };
            col = col.child(line).child(w::text(ui, span).variant("muted"));
        }
        return col;
    }
    let (name, values) = &chart.series[0];
    if !name.is_empty() && chart.series.len() == 1 {
        col = col.child(w::text(ui, name.clone()).variant("muted"));
    }
    let top = values.iter().cloned().fold(0.0f32, f32::max).max(f32::EPSILON);
    for (i, value) in values.iter().enumerate() {
        let label = chart.labels.get(i).cloned().unwrap_or_else(|| format!("{}", i + 1));
        let share = (value.max(0.0) / top).clamp(0.0, 1.0);
        let bar = ui.el("chart-bar").layout(move |l| {
            l.width = Length::Percent(share.max(0.005));
            l.height = Length::Px(unit * 2.5);
        })
        .style(StylePatch::new().background(colors.accent).radius(crate::tokens::corner(crate::tokens::Corner::Small)));
        let track = w::row(ui).layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(bar);
        col = col.child(
            w::row(ui)
                .gap(unit)
                .align(remedy_ui::layout::Align::Center)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(w::text(ui, label).nowrap().layout(|l| {
                    l.width = Length::Px(120.0);
                    l.shrink = 0.0;
                }))
                .child(track)
                .child(w::text(ui, fmt(*value)).nowrap().variant("muted")),
        );
    }
    col
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
                .text_size(crate::tokens::size(crate::tokens::CAPTION))
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
        .gap(tokens::space() * 2.0)
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
                .selectable(true)
                .style(StylePatch::new().mono(true).text_size(crate::tokens::size(crate::tokens::META)).foreground(fg))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        );
    }

    w::column(ui)
        .gap(tokens::space() * 2.0)
        .padding(Insets::all(tokens::space() * 2.0))
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
        .padding(Insets::all(crate::tokens::space() * 1.5))
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
                .selectable(true)
                .style(StylePatch::new().foreground(color).mono(true).text_size(crate::tokens::size(crate::tokens::META)))
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
                        // The message may be gone by the time the image arrives.
                        if let Some(image) = decode_bytes(&bytes) {
                            let _ = slot_set.try_set(Some(image));
                        } else {
                            let _ = err_set.try_set("Could not read that image.".into());
                        }
                    }
                    Ok(_) => drop(err_set.try_set("That image is too large to show.".into())),
                    Err(e) => drop(err_set.try_set(e)),
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

#[cfg(test)]
mod chart_tests {
    use super::*;

    /// A chart block becomes a chart; a broken one stays readable as code.
    #[test]
    fn a_chart_block_is_drawn_and_a_broken_one_stays_code() {
        let good = "Here:\n\n```chart\n{\"type\":\"line\",\"title\":\"Builds\",\"labels\":[\"Mon\",\"Tue\"],\"series\":[{\"name\":\"s\",\"values\":[3,5]}]}\n```\n";
        let blocks = split_blocks(good);
        assert!(matches!(&blocks[1], Block::Chart(c) if c.kind == "line" && c.series[0].1 == vec![3.0, 5.0]));
        let short = "```chart\n{\"values\":[1,2,3]}\n```";
        assert!(matches!(&split_blocks(short)[0], Block::Chart(c) if c.kind == "bar" && c.series.len() == 1));
        let broken = "```chart\nnot json\n```";
        assert!(matches!(&split_blocks(broken)[0], Block::Code { lang, .. } if lang == "chart"));
    }

    /// Tables, headings, lists and quotes go to CommonMark; prose keeps the chat's run.
    #[test]
    fn structure_is_recognised() {
        for yes in ["| a | b |\n|---|---|", "# Plan", "- one\n- two", "1. first\n2. second", "> quoted"] {
            assert!(is_structured(yes), "{yes}");
        }
        for no in ["Plain words.", "*emphasis* only", "Costs 3. Then more", "a | b"] {
            assert!(!is_structured(no), "{no}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::inline_rich;
    use remedy_ui::core::Color;
    use remedy_ui::text::TextStylePatch;

    #[test]
    fn bold_inside_a_numbered_list_item_is_rendered() {
        let rich = inline_rich("1. **Speed:** the main suite", Color::BLACK);
        assert_eq!(rich.as_str(), "1. Speed: the main suite");
        assert!(
            rich.spans().iter().any(|span| {
                let text = &rich.as_str()[span.range.clone()];
                text == "Speed:" && span.patch.weight == TextStylePatch::bold().weight
            }),
            "expected a bold span for Speed:, spans={:?}",
            rich.spans()
        );
        assert!(!rich.as_str().contains("**"));
    }

    #[test]
    fn italic_and_code_still_parse_on_the_same_line() {
        let rich = inline_rich("see *note* and `code`", Color::BLACK);
        assert_eq!(rich.as_str(), "see note and code");
        assert!(rich.spans().iter().any(|s| s.patch.italic == TextStylePatch::italic().italic));
    }
}

