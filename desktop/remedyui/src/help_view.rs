//! Offline Help wiki: the owner's manual, search, and heading anchors.

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::image::decode;
use remedy_ui::layout::{Justify, Length};
use remedy_ui::paint::Image;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::chrome::Chrome;
use crate::help_data::{self, Article};
use crate::messages;

pub fn articles() -> &'static [Article] {
    help_data::ARTICLES
}

pub fn article(id: &str) -> Option<&'static Article> {
    articles().iter().find(|a| a.id == id)
}

pub fn default_id() -> &'static str {
    "00-overview"
}

/// Search scoring matches `searchArticles` in the current app.
pub fn search(query: &str) -> Vec<&'static Article> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        let mut rows: Vec<&Article> = articles().iter().collect();
        rows.sort_by(|a, b| a.order.total_cmp(&b.order));
        return rows;
    }
    let terms: Vec<&str> = q.split_whitespace().collect();
    let mut scored: Vec<(&Article, i32)> = articles()
        .iter()
        .filter_map(|a| {
            let hay = format!(
                "{}\n{}\n{}\n{}\n{}",
                a.title,
                a.summary,
                a.category,
                a.tags.join(" "),
                a.body.chars().take(4000).collect::<String>()
            )
            .to_lowercase();
            let mut score = 0;
            for t in &terms {
                if a.title.to_lowercase().contains(t) {
                    score += 8;
                }
                if a.tags.iter().any(|tag| tag.to_lowercase().contains(t)) {
                    score += 5;
                }
                if a.summary.to_lowercase().contains(t) {
                    score += 3;
                }
                if hay.contains(t) {
                    score += 1;
                }
            }
            (score > 0).then_some((a, score))
        })
        .collect();
    scored.sort_by(|x, y| y.1.cmp(&x.1).then_with(|| x.0.order.total_cmp(&y.0.order)));
    scored.into_iter().map(|(a, _)| a).collect()
}

pub fn heading_anchor(text: &str) -> String {
    let mut raw = String::new();
    for ch in text.chars() {
        if ch.is_alphanumeric() || ch == '_' || ch == '-' || ch.is_whitespace() {
            raw.push(ch);
        }
    }
    raw.to_lowercase()
        .chars()
        .map(|c| if c.is_whitespace() { '-' } else { c })
        .collect()
}

/// The wiki body (search, contents, article). The caller supplies the dialog chrome.
pub fn body(ui: &Ui, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let mut field = w::text_input(ui, messages().text(ui, "help-search"));
    field = field.placeholder(messages().text(ui, "help-placeholder"));
    let qf = field.clone();
    let c = chrome;
    field = field.on_change(move || c.help_query.set(qf.value()));

    let list_host = ui.el("help-list").column().gap(unit);
    let host = list_host.clone();
    let c = chrome;
    let u = ui.clone();
    let list_host = list_host.bind(move || {
        let q = c.help_query.get();
        let current = c.help_id.get();
        host.clear();
        let rows = search(&q);
        if rows.is_empty() {
            let _ = host.clone().child(w::text(&u, messages().text(&u, "help-empty")).variant("muted"));
            return;
        }
        let mut cat = String::new();
        for a in rows {
            if q.trim().is_empty() && a.category != cat {
                cat = a.category.to_string();
                let _ = host.clone().child(w::text(&u, a.category).variant("muted"));
            }
            let id = a.id;
            let cc = c;
            let title = a.title;
            let colors = u.theme().colors.clone();
            let fg = if current.as_str() == id { colors.accent } else { colors.text };
            let _ = host.clone().child(
                w::button(&u, title, move || cc.help_id.set(id.into()))
                    .variant("quiet")
                    .style(StylePatch::new().foreground(fg))
                    .justify(Justify::Start)
                    .layout(|l| l.width = Length::Percent(1.0)),
            );
        }
    });

    let body_host = ui.el("help-body").column().gap(unit);
    let bhost = body_host.clone();
    let c = chrome;
    let u = ui.clone();
    let body_host = body_host.bind(move || {
        let id = c.help_id.get();
        bhost.clear();
        let Some(a) = article(&id).or_else(|| article(default_id())) else {
            return;
        };
        let _ = bhost.clone().child(w::heading(&u, 2, a.title));
        let _ = bhost.clone().child(w::text(&u, a.summary).variant("muted"));
        for block in blocks(&u, a.body) {
            let _ = bhost.clone().child(block);
        }
    });

    w::row(ui)
        .gap(unit * 2.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.grow = 1.0;
            l.min_height = Length::Px(0.0);
        })
        .child(
            w::scroll(ui)
                .child(list_host.padding(Insets::all(unit)))
                .layout(|l| {
                    l.width = Length::Px(230.0);
                    l.shrink = 0.0;
                    l.height = Length::Percent(1.0);
                }),
        )
        .child(w::divider(ui, true))
        .child(
            w::column(ui)
                .gap(unit * 2.0)
                .layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                    l.height = Length::Percent(1.0);
                })
                .child({
                    let input = field.el();
                    input.focus();
                    input
                })
                .child(w::scroll(ui).child(body_host).layout(|l| {
                    l.grow = 1.0;
                    l.min_height = Length::Px(0.0);
                })),
        )
}

fn blocks(ui: &Ui, body: &str) -> Vec<El> {
    let mut out = Vec::new();
    let mut para = String::new();
    let mut fence = false;
    let mut code = String::new();
    for line in body.lines() {
        let trim = line.trim();
        if trim.starts_with("```") {
            if fence {
                out.push(code_block(ui, &code));
                code.clear();
                fence = false;
            } else {
                push_para(ui, &mut out, &mut para);
                fence = true;
            }
            continue;
        }
        if fence {
            if !code.is_empty() {
                code.push('\n');
            }
            code.push_str(line);
            continue;
        }
        if trim.is_empty() {
            push_para(ui, &mut out, &mut para);
            continue;
        }
        if trim.starts_with('|') {
            push_para(ui, &mut out, &mut para);
            if !trim.contains("---") {
                out.push(w::text(ui, trim.trim_matches('|').replace('|', " · ")));
            }
            continue;
        }
        if trim.starts_with('#') {
            push_para(ui, &mut out, &mut para);
            let level = trim.chars().take_while(|c| *c == '#').count() as u8;
            let text = trim.trim_start_matches('#').trim();
            let anchor = heading_anchor(text);
            let shown = inline(text);
            out.push(
                w::heading(ui, level.clamp(1, 3), shown.clone())
                    .semantics(Semantics::new(Role::Heading(level.clamp(1, 6))).label(format!("{shown} #{anchor}"))),
            );
            continue;
        }
        if let Some(rest) = trim.strip_prefix("- ").or_else(|| trim.strip_prefix("* ")) {
            push_para(ui, &mut out, &mut para);
            out.push(w::text(ui, format!("· {}", inline(rest))));
            continue;
        }
        if let Some((alt, src)) = image_line(trim) {
            push_para(ui, &mut out, &mut para);
            out.push(image_or_caption(ui, alt, src));
            continue;
        }
        if !para.is_empty() {
            para.push(' ');
        }
        para.push_str(trim);
    }
    if fence && !code.is_empty() {
        out.push(code_block(ui, &code));
    }
    push_para(ui, &mut out, &mut para);
    out
}

fn push_para(ui: &Ui, out: &mut Vec<El>, para: &mut String) {
    let t = para.trim();
    if !t.is_empty() {
        out.push(w::text(ui, inline(t)));
    }
    para.clear();
}

fn image_line(line: &str) -> Option<(&str, &str)> {
    let rest = line.strip_prefix("![")?;
    let (alt, after) = rest.split_once("](")?;
    let src = after.strip_suffix(')')?;
    Some((alt, src))
}

fn image_or_caption(ui: &Ui, alt: &str, src: &str) -> El {
    if let Some(image) = bundled_image(src) {
        return w::image(ui, image, alt).layout(|l| {
            l.max_width = Length::Px(420.0);
            l.max_height = Length::Px(96.0);
        });
    }
    w::text(ui, format!("[{alt}]")).variant("muted")
}

fn bundled_image(src: &str) -> Option<Image> {
    let clean = src.trim_start_matches("./").trim_start_matches('/');
    let bytes: &[u8] = match clean {
        "assets/hero_logo_color_on_dark.png" => {
            include_bytes!("../help/assets/hero_logo_color_on_dark.png")
        }
        "assets/remedy-partner-architecture.jpg" => {
            include_bytes!("../help/assets/remedy-partner-architecture.jpg")
        }
        _ => return None,
    };
    let decoded = decode(bytes, Some((840, 280))).ok()?;
    Some(Image::new(
        decoded.image.width,
        decoded.image.height,
        decoded.image.pixels,
    ))
}

fn code_block(ui: &Ui, code: &str) -> El {
    let colors = ui.theme().colors.clone();
    w::text(ui, code.trim_end()).style(
        StylePatch::new()
            .background(colors.bg)
            .foreground(colors.text)
            .radius(ui.theme().radii.small)
            .padding(Insets::all(ui.theme().spacing.unit * 2.0)),
    )
}

fn inline(text: &str) -> String {
    let mut s = text.replace("**", "").replace('`', "");
    while let Some(start) = s.find('[') {
        let Some(mid) = s[start..].find("](") else { break };
        let Some(end) = s[start + mid..].find(')') else { break };
        let label = s[start + 1..start + mid].to_string();
        let abs_end = start + mid + end;
        s.replace_range(start..=abs_end, &label);
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_has_the_owners_manual() {
        assert!(articles().len() >= 32);
        assert!(article("00-overview").is_some());
        assert!(article("09-troubleshooting").is_some());
        assert!(article("11-reference-commands").is_some());
        assert!(article("13-whats-new").is_some());
    }

    #[test]
    fn search_ranks_troubleshooting() {
        let hits = search("troubleshooting");
        assert!(hits.first().is_some_and(|a| a.id == "09-troubleshooting"));
    }

    #[test]
    fn anchors_keep_letters_and_drop_punctuation() {
        assert_eq!(heading_anchor("What's new?"), "whats-new");
        assert_eq!(heading_anchor("Install (Windows)"), "install-windows");
    }
}
