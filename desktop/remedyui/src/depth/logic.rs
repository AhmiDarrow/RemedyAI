//! Pure chat-depth rules, translated from the Tauri helpers.
//!
//! These stay free of widgets so the decisions (what a drop is, when a turn
//! needs a confirm, when the feed follows the floor) can be read on their own.

use crate::studio::{ChatMessage, Filter, Project, Session};
use crate::work::WorkStep;

pub const MAX_ATTACHMENTS: usize = 12;
pub const PROMPT_HISTORY_MAX: usize = 80;
pub const NEAR_PX: f32 = 48.0;
pub const STALL_WARN_MS: u64 = 180_000;
pub const SOFT_BADGE_AT: usize = 2;
pub const CONFIRM_AT: usize = 3;
pub const PINNED_GROUP_KEY: &str = "__pinned__";

/// One slash command the composer can offer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlashCommand {
    pub name: String,
    pub description: String,
}

impl SlashCommand {
    pub fn new(name: impl Into<String>, description: impl Into<String>) -> Self {
        let mut name = name.into();
        if !name.starts_with('/') {
            name.insert(0, '/');
        }
        Self { name, description: description.into() }
    }
}

/// Commands shown before `/api/commands` answers, and if it never does.
pub fn fallback_commands() -> Vec<SlashCommand> {
    vec![
        SlashCommand::new("/help", "List commands"),
        SlashCommand::new("/new", "New session"),
        SlashCommand::new("/reset", "Full reset this session (stay here — like new chat)"),
        SlashCommand::new("/compact", "Compact conversation"),
        SlashCommand::new("/remember", "Save a memory"),
        SlashCommand::new("/thinking", "Toggle thinking visibility"),
        SlashCommand::new("/export", "Export session as .txt"),
        SlashCommand::new("/import-session", "Import session from .txt / .md"),
    ]
}

/// The `@query` being typed, if the cursor sits in one.
pub fn at_query(text: &str, cursor: usize) -> Option<String> {
    let cursor = cursor.min(text.len());
    let before = &text[..cursor];
    let at = before.rfind('@')?;
    let q = &before[at + 1..];
    if q.chars().any(char::is_whitespace) {
        return None;
    }
    Some(q.to_string())
}

/// The slash draft at the start of the field, without the leading `/`.
///
/// `None` once the line has arguments or a newline: the menu hides.
pub fn slash_query(text: &str, cursor: usize) -> Option<String> {
    let cursor = cursor.min(text.len());
    let before = &text[..cursor];
    if !before.starts_with('/') || before.contains('\n') {
        return None;
    }
    if before.contains(' ') && !before.ends_with(' ') {
        return None;
    }
    Some(before[1..].to_string())
}

pub fn filter_commands(commands: &[SlashCommand], query: &str) -> Vec<SlashCommand> {
    let ql = query.to_lowercase();
    commands
        .iter()
        .filter(|c| {
            if ql.is_empty() {
                return true;
            }
            let name = c.name.trim_start_matches('/').to_lowercase();
            let full = c.name.to_lowercase();
            name.starts_with(&ql) || full.contains(&ql) || c.description.to_lowercase().contains(&ql)
        })
        .take(10)
        .cloned()
        .collect()
}

/// Newest first. A repeat moves to the front. Empty text is ignored.
pub fn push_history(history: &[String], text: &str) -> Vec<String> {
    let t = text.trim();
    if t.is_empty() {
        return history.to_vec();
    }
    let mut next = vec![t.to_string()];
    next.extend(history.iter().filter(|x| x.as_str() != t).cloned());
    next.truncate(PROMPT_HISTORY_MAX);
    next
}

pub fn format_bytes(n: u64) -> String {
    if n < 1024 {
        format!("{n} B")
    } else if n < 1024 * 1024 {
        format!("{:.1} KB", n as f64 / 1024.0)
    } else {
        format!("{:.1} MB", n as f64 / (1024.0 * 1024.0))
    }
}

pub fn name_size_key(name: &str, size: u64) -> String {
    format!("{}|{size}", name.to_lowercase())
}

pub fn looks_like_text_name(name: &str, mime: &str) -> bool {
    mime.starts_with("text/")
        || matches!(
            std::path::Path::new(name)
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.to_ascii_lowercase())
                .as_deref(),
            Some("txt" | "md" | "py" | "ts" | "js" | "json" | "csv" | "log" | "toml" | "yaml" | "yml")
        )
}

// --- sanitize (sanitizeChat.ts) ---

pub fn looks_like_tool_markup(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    let lower = text.to_ascii_lowercase();
    lower.contains("dsml")
        || lower.contains("tool_calls")
        || lower.contains("tool calls")
        || lower.contains("tool-calls")
        || lower.contains("function_calls")
        || lower.contains("function calls")
        || lower.contains("<invoke")
        || lower.contains("</invoke")
        || lower.contains("<parameter")
        || lower.contains("invoke_parameter")
}

pub fn sanitize_assistant_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut t = String::new();
    for line in text.split('\n') {
        let trimmed = line.trim_start();
        if trimmed.len() >= 9 && trimmed[..9].eq_ignore_ascii_case("@@status:") {
            continue;
        }
        if !t.is_empty() {
            t.push('\n');
        }
        t.push_str(line);
    }
    if looks_like_tool_markup(&t) {
        t = strip_tool_markup(&t);
    }
    collapse_blank_lines(t.trim())
}

fn strip_tool_markup(text: &str) -> String {
    let mut out = String::new();
    let lower = text.to_ascii_lowercase();
    // Drop invoke / parameter tags and DSML markers by scanning bytes, but
    // copy whole chars so a non-ASCII reply is not split into garbage.
    let mut i = 0;
    let b = text.as_bytes();
    while i < b.len() {
        if b[i] == b'<' {
            let rest = &lower[i..];
            if rest.starts_with("<invoke")
                || rest.starts_with("</invoke")
                || rest.starts_with("<parameter")
                || rest.starts_with("</parameter")
                || rest.starts_with("<invoke_parameter")
                || rest.starts_with("</invoke_parameter")
                || rest.starts_with("<invoke_step")
                || rest.starts_with("</invoke_step")
            {
                if let Some(end) = text[i..].find('>') {
                    i += end + 1;
                    out.push(' ');
                    continue;
                }
            }
        }
        let ch = text[i..].chars().next().unwrap_or('\u{FFFD}');
        out.push(ch);
        i += ch.len_utf8();
    }
    let mut cleaned = String::new();
    for line in out.split('\n') {
        let l = line.to_ascii_lowercase();
        if l.contains("dsml") || l.contains("tool_calls") || l.contains("function_calls") {
            continue;
        }
        if !cleaned.is_empty() {
            cleaned.push('\n');
        }
        cleaned.push_str(line);
    }
    collapse_blank_lines(cleaned.trim())
}

fn collapse_blank_lines(text: &str) -> String {
    let mut out = String::new();
    let mut blanks = 0;
    for line in text.lines() {
        if line.trim().is_empty() {
            blanks += 1;
            if blanks <= 2 && !out.is_empty() {
                out.push('\n');
            }
        } else {
            blanks = 0;
            out.push_str(line);
            out.push('\n');
        }
    }
    out.trim().to_string()
}

// --- urls (chatMarkdownUrl.ts) ---

pub fn remote_image_host(src: &str) -> Option<String> {
    let v = trim_angles(src);
    let rest = strip_prefix_ignore_ascii_case(&v, "https://")
        .or_else(|| strip_prefix_ignore_ascii_case(&v, "http://"))?;
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host = host.trim_matches(|c| c == '[' || c == ']').to_ascii_lowercase();
    if host.is_empty()
        || host == "localhost"
        || host == "127.0.0.1"
        || host == "::1"
        || host.ends_with(".localhost")
    {
        return None;
    }
    Some(host)
}

pub fn is_network_path(url: &str) -> bool {
    let mut v = trim_angles(url);
    if v.len() >= 5 && v[..5].eq_ignore_ascii_case("file:") {
        let rest = &v[5..];
        let lead = rest.chars().take_while(|c| *c == '\\' || *c == '/').count();
        let body: String = rest.chars().skip(lead).collect();
        if (lead == 2 || lead >= 4) && !is_drive_prefix(&body) {
            return true;
        }
        v = body;
    }
    v.starts_with("\\\\") || v.starts_with("//")
}

pub fn chat_markdown_url(url: &str) -> String {
    let v = trim_angles(url);
    if v.is_empty() {
        return String::new();
    }
    if has_scheme(&v, "javascript:") || has_scheme(&v, "vbscript:") {
        return String::new();
    }
    if v.len() >= 5 && v[..5].eq_ignore_ascii_case("data:") && !has_scheme(&v, "data:image/") {
        return String::new();
    }
    if is_network_path(&v) {
        return String::new();
    }
    if has_scheme(&v, "https:")
        || has_scheme(&v, "http:")
        || has_scheme(&v, "data:image/")
        || has_scheme(&v, "blob:")
        || has_scheme(&v, "file:")
    {
        return v;
    }
    if is_drive_prefix(&v) {
        return v;
    }
    let colon = v.find(':');
    let Some(colon) = colon else {
        return v;
    };
    let slash = v.find('/');
    let back = v.find('\\');
    let first_sep = match (slash, back) {
        (None, None) => None,
        (Some(s), None) => Some(s),
        (None, Some(b)) => Some(b),
        (Some(s), Some(b)) => Some(s.min(b)),
    };
    if first_sep.is_none_or(|sep| colon < sep) {
        return String::new();
    }
    v
}

fn trim_angles(url: &str) -> String {
    url.trim().trim_matches(|c| c == '<' || c == '>').to_string()
}

fn has_scheme(v: &str, scheme: &str) -> bool {
    v.len() >= scheme.len() && v[..scheme.len()].eq_ignore_ascii_case(scheme)
}

fn strip_prefix_ignore_ascii_case<'a>(v: &'a str, prefix: &str) -> Option<&'a str> {
    if v.len() >= prefix.len() && v[..prefix.len()].eq_ignore_ascii_case(prefix) {
        Some(&v[prefix.len()..])
    } else {
        None
    }
}

fn is_drive_prefix(v: &str) -> bool {
    let b = v.as_bytes();
    b.len() >= 3 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/')
}

// --- diffs (diffHighlight.ts) ---

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DiffLineKind {
    Add,
    Del,
    Hunk,
    Meta,
    Ctx,
}

pub fn looks_like_unified_diff(text: &str) -> bool {
    let mut plus = 0;
    let mut minus = 0;
    let mut hunk = 0;
    let mut headers = 0;
    let mut lines = 0;
    for line in text.replace("\r\n", "\n").lines() {
        lines += 1;
        if line.starts_with("@@") {
            hunk += 1;
        } else if line.starts_with("diff --git")
            || line.starts_with("index ")
            || line.starts_with("+++ ")
            || line.starts_with("--- ")
        {
            headers += 1;
        } else if line.starts_with('+') && !line.starts_with("+++") {
            plus += 1;
        } else if line.starts_with('-') && !line.starts_with("---") {
            minus += 1;
        }
    }
    if lines < 2 {
        return false;
    }
    if hunk > 0 || headers >= 2 {
        return true;
    }
    plus >= 1 && minus >= 1 && plus + minus >= 3
}

pub fn is_diff_language(class_name: &str) -> bool {
    let l = class_name.to_ascii_lowercase();
    l.contains("language-diff") || l.contains("language-patch") || l.contains("language-udiff")
}

pub fn classify_diff_line(line: &str) -> DiffLineKind {
    if line.starts_with("diff --git")
        || line.starts_with("index ")
        || line.starts_with("+++ ")
        || line.starts_with("--- ")
        || line.starts_with("new file")
        || line.starts_with("deleted file")
        || line.starts_with("similarity index")
        || line.starts_with("rename from")
        || line.starts_with("rename to")
        || line.starts_with("Binary files")
    {
        DiffLineKind::Meta
    } else if line.starts_with("@@") {
        DiffLineKind::Hunk
    } else if line.starts_with('+') {
        DiffLineKind::Add
    } else if line.starts_with('-') {
        DiffLineKind::Del
    } else {
        DiffLineKind::Ctx
    }
}

pub fn should_render_as_diff(text: &str, lang: &str) -> bool {
    is_diff_language(lang) || looks_like_unified_diff(text)
}

// --- reactions ---

pub const REACTIONS: &[(&str, &str)] = &[
    ("👍", "Thumbs up"),
    ("👎", "Thumbs down"),
    ("❤️", "Heart"),
    ("💔", "Broken heart"),
];

pub fn is_reaction(emoji: &str) -> bool {
    REACTIONS.iter().any(|(e, _)| *e == emoji)
}

pub fn reaction_label(emoji: &str) -> &str {
    REACTIONS.iter().find(|(e, _)| *e == emoji).map(|(_, l)| *l).unwrap_or(emoji)
}

pub fn next_reaction(current: &str, picked: &str) -> String {
    if current == picked { String::new() } else { picked.to_string() }
}

// --- follow (followController.ts) ---

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ScrollMetrics {
    pub scroll_top: f32,
    pub scroll_height: f32,
    pub client_height: f32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FollowState {
    pub stuck: bool,
    pub expected_top: Option<f32>,
    pub lock_until: u64,
    pub instant_until: u64,
    pub anchoring: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub enum FollowEvent {
    Scroll { metrics: ScrollMetrics, now: u64 },
    Wheel { delta_y: f32, can_scroll: bool },
    Key { key: String, can_scroll: bool },
    Pinned { top: f32, now: u64, instant: bool, smooth_ms: u64 },
    Attach,
    Detach,
    AnchorStart,
    AnchorEnd { metrics: ScrollMetrics, top: f32 },
}

pub const INSTANT_SETTLE_MS: u64 = 280;

pub fn initial_follow(start_at_bottom: bool) -> FollowState {
    FollowState {
        stuck: start_at_bottom,
        expected_top: None,
        lock_until: 0,
        instant_until: 0,
        anchoring: false,
    }
}

pub fn distance_from_bottom(m: ScrollMetrics) -> f32 {
    m.scroll_height - m.scroll_top - m.client_height
}

pub fn is_near_bottom(m: ScrollMetrics) -> bool {
    distance_from_bottom(m) <= NEAR_PX
}

pub fn can_scroll(m: ScrollMetrics) -> bool {
    m.scroll_height - m.client_height > 1.0
}

pub fn reduce_follow(s: FollowState, e: FollowEvent) -> FollowState {
    match e {
        FollowEvent::Scroll { metrics, now } => {
            if s.anchoring || now < s.lock_until {
                return s;
            }
            if s.expected_top.is_some_and(|t| (metrics.scroll_top - t).abs() <= 1.0) {
                return s;
            }
            let near = is_near_bottom(metrics);
            if now < s.instant_until && near {
                return s;
            }
            FollowState { expected_top: None, stuck: near, ..s }
        }
        FollowEvent::Wheel { delta_y, can_scroll } => {
            if delta_y < 0.0 && can_scroll && s.stuck {
                FollowState { stuck: false, ..s }
            } else {
                s
            }
        }
        FollowEvent::Key { key, can_scroll } => {
            if !can_scroll {
                return s;
            }
            if matches!(key.as_str(), "ArrowUp" | "PageUp" | "Home") && s.stuck {
                FollowState { stuck: false, ..s }
            } else {
                s
            }
        }
        FollowEvent::Pinned { top, now, instant, smooth_ms } => FollowState {
            expected_top: Some(top),
            lock_until: if smooth_ms > 0 { now + smooth_ms } else { s.lock_until },
            instant_until: if instant { now + INSTANT_SETTLE_MS } else { s.instant_until },
            ..s
        },
        FollowEvent::Attach => {
            if s.stuck { s } else { FollowState { stuck: true, ..s } }
        }
        FollowEvent::Detach => {
            if s.stuck { FollowState { stuck: false, ..s } } else { s }
        }
        FollowEvent::AnchorStart => FollowState { anchoring: true, ..s },
        FollowEvent::AnchorEnd { metrics, top } => FollowState {
            anchoring: false,
            expected_top: Some(top),
            stuck: is_near_bottom(metrics),
            ..s
        },
    }
}

pub fn should_pin(s: FollowState) -> bool {
    s.stuck && !s.anchoring
}

// --- reattach ---

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReattachPlan {
    Idle,
    FollowLocal,
    Working,
    Attach { request_id: String },
}

pub fn reattach_plan(
    claimed: bool,
    active_request_id: Option<&str>,
    local_job_running: bool,
    local_request_id: Option<&str>,
) -> ReattachPlan {
    let request_id = active_request_id.unwrap_or("").trim();
    if !claimed {
        return if local_job_running { ReattachPlan::FollowLocal } else { ReattachPlan::Idle };
    }
    if local_job_running {
        let local = local_request_id.unwrap_or("").trim();
        if local.is_empty() || request_id.is_empty() || local == request_id {
            return ReattachPlan::FollowLocal;
        }
    }
    if request_id.is_empty() {
        ReattachPlan::Working
    } else {
        ReattachPlan::Attach { request_id: request_id.to_string() }
    }
}

// --- concurrent turns ---

pub fn should_show_busy_badge(running: usize) -> bool {
    running >= SOFT_BADGE_AT
}

pub fn should_confirm_new_turn(running: usize) -> bool {
    running >= CONFIRM_AT
}

pub fn concurrent_turn_message(running: usize) -> String {
    format!(
        "{running} sessions already have a live turn. Starting another can burn rate limits and contend for tools. Continue?"
    )
}

// --- sidebar order ---

pub fn project_key(path: Option<&str>) -> String {
    path.unwrap_or("").trim().replace('\\', "/").trim_end_matches('/').to_string()
}

pub fn merge_project_order(saved: &[String], active_keys: &[String]) -> Vec<String> {
    let mut active = Vec::new();
    for k in active_keys {
        let k = project_key(Some(k));
        if !k.is_empty() && !active.contains(&k) {
            active.push(k);
        }
    }
    let mut ordered: Vec<String> = saved.iter().filter(|k| active.iter().any(|a| a == *k)).cloned().collect();
    let mut rest: Vec<String> = active.into_iter().filter(|k| !ordered.contains(k)).collect();
    rest.sort_by(|a, b| a.to_lowercase().cmp(&b.to_lowercase()));
    ordered.append(&mut rest);
    ordered
}

pub fn move_in_list(list: &[String], key: &str, dir: i32) -> Option<Vec<String>> {
    let i = list.iter().position(|k| k == key)?;
    let j = i as i32 + dir;
    if j < 0 || j as usize >= list.len() {
        return Some(list.to_vec());
    }
    let mut next = list.to_vec();
    next.swap(i, j as usize);
    Some(next)
}

pub fn merge_session_order(saved: &[String], current: &[String]) -> Vec<String> {
    let mut cur = Vec::new();
    for id in current {
        if !id.is_empty() && !cur.contains(id) {
            cur.push(id.clone());
        }
    }
    let mut ordered: Vec<String> = saved.iter().filter(|id| cur.iter().any(|c| c == *id)).cloned().collect();
    for id in cur {
        if !ordered.contains(&id) {
            ordered.push(id);
        }
    }
    ordered
}

pub fn rehome_order(
    map: &mut Vec<(String, Vec<String>)>,
    session_id: &str,
    from_project: Option<&str>,
    to_project: Option<&str>,
) {
    let from_k = project_key(from_project);
    let to_k = project_key(to_project);
    if from_k == to_k || session_id.is_empty() {
        return;
    }
    if let Some((_, ids)) = map.iter_mut().find(|(k, _)| k == &from_k) {
        ids.retain(|id| id != session_id);
    }
    if let Some((_, ids)) = map.iter_mut().find(|(k, _)| k == &to_k) {
        if !ids.iter().any(|id| id == session_id) {
            ids.push(session_id.to_string());
        }
    } else {
        map.push((to_k, vec![session_id.to_string()]));
    }
}

pub fn order_for<'a>(map: &'a [(String, Vec<String>)], key: &str) -> &'a [String] {
    map.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_slice()).unwrap_or(&[])
}

/// Pinned strip, then projects in the saved order, sessions in the saved order.
pub fn ordered_groups(
    sessions: &[Session],
    projects: &[Project],
    filter: Filter,
    query: &str,
    project_order: &[String],
    session_order: &[(String, Vec<String>)],
) -> Vec<(Project, Vec<Session>)> {
    let groups = crate::studio::grouped(sessions, projects, filter, query);
    let pin_strip = filter != Filter::Pinned;
    let pinned: Vec<Session> = if pin_strip {
        let mut p: Vec<Session> = groups
            .iter()
            .flat_map(|(_, members)| members.iter().filter(|s| s.pinned).cloned())
            .collect();
        p.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        p
    } else {
        Vec::new()
    };
    let keys: Vec<String> = groups
        .iter()
        .filter_map(|(p, _)| p.path.as_ref().map(|s| project_key(Some(s))))
        .filter(|k| !k.is_empty())
        .collect();
    let ordered_keys = merge_project_order(project_order, &keys);
    let mut out = Vec::new();
    if !pinned.is_empty() {
        out.push((
            Project { path: Some(PINNED_GROUP_KEY.into()), collapsed: false },
            pinned,
        ));
    }
    let none = groups.iter().find(|(p, _)| p.path.is_none()).cloned();
    let mut projects_out = Vec::new();
    for key in &ordered_keys {
        if let Some((project, members)) = groups.iter().find(|(p, _)| {
            p.path.as_ref().is_some_and(|path| project_key(Some(path)) == *key)
        }) {
            let ids: Vec<String> = members.iter().filter(|s| !(pin_strip && s.pinned)).map(|s| s.id.clone()).collect();
            let order = merge_session_order(order_for(session_order, key), &ids);
            let mut sorted = Vec::new();
            for id in order {
                if let Some(s) = members.iter().find(|s| s.id == id) {
                    sorted.push(s.clone());
                }
            }
            projects_out.push((project.clone(), sorted));
        }
    }
    if let Some((project, members)) = none {
        let ids: Vec<String> = members.iter().filter(|s| !(pin_strip && s.pinned)).map(|s| s.id.clone()).collect();
        let order = merge_session_order(order_for(session_order, ""), &ids);
        let mut sorted = Vec::new();
        for id in order {
            if let Some(s) = members.iter().find(|s| s.id == id) {
                sorted.push(s.clone());
            }
        }
        // No-project sits between nothing special and the unlocked tail: after locked
        // keys we don't track here, so it leads the project list, matching the Tauri tree
        // when nothing is locked.
        out.push((project, sorted));
    }
    out.extend(projects_out);
    if out.is_empty() {
        groups
    } else {
        out
    }
}

pub fn session_delete_body() -> &'static str {
    "This removes the transcript plus session notes, attachments, plans, and undo history.\n\nWhat I remember about you (Partner Memory) is kept — clear that in Settings → You & Agent → Wipe persona."
}

pub fn project_remove_body(path: &str, session_count: usize) -> String {
    let name = std::path::Path::new(path)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or(path);
    let chat = if session_count > 0 {
        format!(
            "Its {session_count} chat{} stay — they move to No project.",
            if session_count == 1 { "" } else { "s" }
        )
    } else {
        "It has no chats.".into()
    };
    format!("{chat}\n\nNothing on disk is touched — the folder and its files stay exactly where they are. You can add it back any time. The name was {name}.")
}

pub fn short_model(model: &str) -> String {
    let m = model.trim();
    if m.chars().count() <= 28 {
        return m.to_string();
    }
    let head: String = m.chars().take(14).collect();
    let tail: String = m.chars().rev().take(10).collect::<String>().chars().rev().collect();
    format!("{head}…{tail}")
}

pub fn life_headline(spoken: &str, goal: &str, title: &str, step: Option<u64>, total: Option<u64>) -> String {
    let spoken = spoken.trim();
    if !spoken.is_empty() {
        return spoken.to_string();
    }
    if let (Some(step), Some(total)) = (step.filter(|n| *n > 0), total.filter(|n| *n > 0)) {
        let title = if title.trim().is_empty() { "the next step" } else { title.trim() };
        return format!("Step {step} of {total} — {title}.");
    }
    let goal = goal.trim();
    if goal.is_empty() { String::new() } else { format!("Toward {goal}.") }
}

pub fn show_life_banner(status: &str, spoken: &str, steps: usize, approval: bool) -> bool {
    let st = status.to_ascii_lowercase();
    if st == "cancelled" {
        return false;
    }
    !spoken.trim().is_empty()
        || steps > 0
        || approval
        || matches!(st.as_str(), "running" | "need_you" | "done" | "blocked")
}

/// Speech chunks: first sentence starts sound, later ones pair when short.
pub fn split_for_speech(text: &str) -> Vec<String> {
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.is_empty() {
        return Vec::new();
    }
    let mut sentences = Vec::new();
    let mut cur = String::new();
    for word in flat.split(' ') {
        if !cur.is_empty() {
            cur.push(' ');
        }
        cur.push_str(word);
        if cur.ends_with(['.', '!', '?', '…']) {
            sentences.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        sentences.push(cur);
    }
    let mut out = Vec::new();
    let mut buf = String::new();
    for s in sentences {
        if buf.is_empty() {
            buf = s;
            continue;
        }
        let limit_ok = if out.is_empty() { buf.len() < 40 } else { buf.len() < 90 };
        if limit_ok && buf.len() + 1 + s.len() <= 220 {
            buf.push(' ');
            buf.push_str(&s);
        } else {
            out.push(std::mem::take(&mut buf));
            buf = s;
        }
    }
    if !buf.is_empty() {
        out.push(buf);
    }
    out
}

pub fn strip_markdown_for_speech(md: &str) -> String {
    let mut out = String::new();
    let mut chars = md.chars().peekable();
    let mut fence = false;
    while let Some(c) = chars.next() {
        if c == '`' {
            let mut ticks = 1;
            while chars.peek() == Some(&'`') {
                ticks += 1;
                chars.next();
            }
            if ticks >= 3 {
                fence = !fence;
                if fence {
                    out.push_str(" code shown on screen. ");
                }
                continue;
            }
            continue;
        }
        if fence {
            continue;
        }
        out.push(c);
    }
    let flat = out.split_whitespace().collect::<Vec<_>>().join(" ");
    flat.chars().take(1400).collect()
}

/// Trace rows the work strip can open. Tool steps already on the message, named.
pub fn trace_lines(steps: &[WorkStep]) -> Vec<(String, String)> {
    steps
        .iter()
        .map(|s| {
            let state = format!("{:?}", s.status).to_lowercase();
            (s.name.clone(), state)
        })
        .collect()
}


/// Path for `POST /api/sessions/{sid}/messages/{msg_id}/edit`.
pub fn edit_message_path(session_id: &str, msg_id: &str) -> String {
    format!("sessions/{session_id}/messages/{msg_id}/edit")
}

/// Drop the display-only attachment block the feed appends under a prompt.
pub fn clean_resend_text(content: &str) -> String {
    const MARK: &str = "\n\n📎 Attachments:\n";
    let cut = content.find(MARK).unwrap_or(content.len());
    content[..cut].trim().to_string()
}

/// The last non-empty user turn before `assistant_id`, used to regenerate.
pub fn preceding_user_for_regen(
    messages: &[ChatMessage],
    assistant_id: &str,
) -> Option<(String, String)> {
    let idx = messages.iter().position(|m| m.id == assistant_id)?;
    messages[..idx]
        .iter()
        .rev()
        .find(|m| m.role == "user")
        .map(|m| (m.id.clone(), m.content.clone()))
}

/// Text the Copy message control puts on the clipboard.
pub fn message_copy_text(content: &str, beats: &[String]) -> String {
    if !content.trim().is_empty() {
        return content.to_string();
    }
    beats
        .iter()
        .map(|b| b.trim())
        .filter(|b| !b.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
}


/// Toggle `id` in a bulk-select set, preserving order.
pub fn toggle_selected_id(selected: &[String], id: &str) -> Vec<String> {
    if selected.iter().any(|s| s == id) {
        selected.iter().filter(|s| s.as_str() != id).cloned().collect()
    } else {
        let mut next = selected.to_vec();
        next.push(id.to_string());
        next
    }
}

/// Inclusive range of visible session ids between `anchor` and `id` (Shift+click).
pub fn selected_id_range(visible: &[String], anchor: Option<&str>, id: &str) -> Vec<String> {
    let Some(a) = anchor.and_then(|a| visible.iter().position(|x| x == a)) else {
        return vec![id.to_string()];
    };
    let Some(b) = visible.iter().position(|x| x == id) else {
        return vec![id.to_string()];
    };
    let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
    visible[lo..=hi].to_vec()
}

/// Body for `POST /api/sessions/bulk-project`. Empty path means no project.
/// The sidecar rejects more than 200 ids.
pub fn bulk_project_body(ids: &[String], project_path: Option<&str>) -> serde_json::Value {
    let ids = if ids.len() > 200 { &ids[..200] } else { ids };
    serde_json::json!({
        "session_ids": ids,
        "project_path": project_path.unwrap_or(""),
    })
}

/// Visible session ids in tree order, for Shift+click ranges and Select visible.
pub fn visible_session_ids(
    sessions: &[crate::studio::Session],
    projects: &[crate::studio::Project],
    filter: crate::studio::Filter,
    query: &str,
    project_order: &[String],
    session_order: &[(String, Vec<String>)],
) -> Vec<String> {
    ordered_groups(
        sessions,
        projects,
        filter,
        query,
        project_order,
        session_order,
    )
    .into_iter()
    .flat_map(|(_, members)| members.into_iter().map(|s| s.id))
    .collect()
}

pub fn delete_selected_title(count: usize) -> String {
    if count == 1 {
        "Delete 1 chat?".into()
    } else {
        format!("Delete {count} chats?")
    }
}

pub fn delete_selected_confirm_label(count: usize) -> String {
    if count == 1 {
        "Delete chat".into()
    } else {
        format!("Delete {count} chats")
    }
}

/// Event names from `GET /api/events/sessions` that should refresh the sidebar.
pub fn is_session_sync_event(kind: &str) -> bool {
    matches!(
        kind,
        "session_created" | "session_updated" | "message_added" | "session_deleted"
    )
}

/// Reload the open thread after a sync event, but not mid-stream.
pub fn should_reload_open_thread(
    kind: &str,
    event_sid: &str,
    active: Option<&str>,
    streaming: bool,
) -> bool {
    if streaming || event_sid.is_empty() {
        return false;
    }
    matches!(kind, "message_added" | "session_updated") && active == Some(event_sid)
}

/// `session_id` from a session-sync JSON payload.
pub fn event_session_id(data: &str) -> String {
    serde_json::from_str::<serde_json::Value>(data)
        .ok()
        .and_then(|v| {
            v.get("session_id")
                .and_then(|s| s.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_default()
}

pub const SESSION_SYNC_BACKOFF_START_MS: u64 = 1500;
pub const SESSION_SYNC_BACKOFF_MAX_MS: u64 = 30_000;
pub const SESSION_SYNC_DEBOUNCE_MS: u64 = 400;

/// Next reconnect delay after a dropped session-events stream, matching the Tauri shell.
pub fn next_session_sync_backoff_ms(current: u64) -> u64 {
    let next = current.saturating_mul(16) / 10;
    next.clamp(SESSION_SYNC_BACKOFF_START_MS, SESSION_SYNC_BACKOFF_MAX_MS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slash_hides_once_arguments_start() {
        assert_eq!(slash_query("/he", 3).as_deref(), Some("he"));
        assert_eq!(slash_query("/help ", 6).as_deref(), Some("help "));
        assert!(slash_query("/help me", 8).is_none());
        assert!(slash_query("hello /help", 11).is_none());
    }

    #[test]
    fn at_query_stops_at_whitespace() {
        assert_eq!(at_query("see @jo", 7).as_deref(), Some("jo"));
        assert!(at_query("see @jo e", 9).is_none());
    }

    #[test]
    fn history_dedupes_to_the_front() {
        let h = push_history(&["a".into(), "b".into()], "b");
        assert_eq!(h, vec!["b".to_string(), "a".to_string()]);
    }

    #[test]
    fn urls_block_active_content_and_unc() {
        assert_eq!(chat_markdown_url("javascript:alert(1)"), "");
        assert_eq!(chat_markdown_url("data:text/html,hi"), "");
        assert_eq!(chat_markdown_url(r"\\server\share\a.png"), "");
        assert_eq!(chat_markdown_url("https://example.com/a.png"), "https://example.com/a.png");
        assert_eq!(remote_image_host("http://127.0.0.1/a.png"), None);
        assert_eq!(remote_image_host("https://cdn.example/a.png").as_deref(), Some("cdn.example"));
        assert_eq!(chat_markdown_url(r"C:\pics\a.png"), r"C:\pics\a.png");
    }

    #[test]
    fn diffs_classify() {
        assert!(looks_like_unified_diff("@@ -1 +1 @@\n-a\n+b\n"));
        assert_eq!(classify_diff_line("+added"), DiffLineKind::Add);
        assert_eq!(classify_diff_line("-gone"), DiffLineKind::Del);
        assert!(should_render_as_diff("x", "language-diff"));
    }

    #[test]
    fn follow_pauses_when_the_reader_leaves_the_floor() {
        let s = initial_follow(true);
        let up = reduce_follow(s, FollowEvent::Wheel { delta_y: -1.0, can_scroll: true });
        assert!(!up.stuck);
        let back = reduce_follow(
            up,
            FollowEvent::Scroll {
                metrics: ScrollMetrics { scroll_top: 100.0, scroll_height: 140.0, client_height: 40.0 },
                now: 10,
            },
        );
        assert!(back.stuck);
    }

    #[test]
    fn reattach_matches_the_tauri_rule() {
        assert_eq!(reattach_plan(false, None, false, None), ReattachPlan::Idle);
        assert_eq!(reattach_plan(true, None, false, None), ReattachPlan::Working);
        assert_eq!(
            reattach_plan(true, Some("r1"), false, None),
            ReattachPlan::Attach { request_id: "r1".into() }
        );
        assert_eq!(reattach_plan(true, Some("r1"), true, Some("r1")), ReattachPlan::FollowLocal);
    }

    #[test]
    fn confirms_at_three_live_turns() {
        assert!(!should_confirm_new_turn(2));
        assert!(should_confirm_new_turn(3));
        assert!(should_show_busy_badge(2));
    }

    #[test]
    fn sanitize_drops_status_tokens() {
        let t = sanitize_assistant_text("Hello\n@@status:vision\nThere");
        assert!(t.contains("Hello"));
        assert!(!t.to_ascii_lowercase().contains("@@status"));
    }

    #[test]
    fn edit_path_matches_the_sidecar() {
        assert_eq!(
            edit_message_path("s1", "m9"),
            "sessions/s1/messages/m9/edit"
        );
    }

    #[test]
    fn bulk_select_toggles_and_shift_range_matches_the_old_shell() {
        assert_eq!(toggle_selected_id(&[], "a"), vec!["a"]);
        assert_eq!(toggle_selected_id(&["a".into()], "a"), Vec::<String>::new());
        assert_eq!(toggle_selected_id(&["a".into()], "b"), vec!["a", "b"]);
        let vis = vec!["a".into(), "b".into(), "c".into(), "d".into()];
        assert_eq!(selected_id_range(&vis, Some("b"), "d"), vec!["b", "c", "d"]);
        assert_eq!(selected_id_range(&vis, Some("d"), "b"), vec!["b", "c", "d"]);
        assert_eq!(selected_id_range(&vis, None, "c"), vec!["c"]);
        assert_eq!(selected_id_range(&vis, Some("missing"), "c"), vec!["c"]);
        let body = bulk_project_body(&["s1".into(), "s2".into()], Some("/tmp/p"));
        assert_eq!(body["session_ids"], serde_json::json!(["s1", "s2"]));
        assert_eq!(body["project_path"], "/tmp/p");
        assert_eq!(bulk_project_body(&["s1".into()], None)["project_path"], "");
        let many: Vec<String> = (0..250).map(|i| format!("s{i}")).collect();
        assert_eq!(bulk_project_body(&many, None)["session_ids"].as_array().unwrap().len(), 200);
        assert_eq!(delete_selected_title(1), "Delete 1 chat?");
        assert_eq!(delete_selected_title(4), "Delete 4 chats?");
        assert_eq!(delete_selected_confirm_label(1), "Delete chat");
        assert_eq!(delete_selected_confirm_label(4), "Delete 4 chats");
    }

    fn message_copy_prefers_the_body_then_falls_back_to_beats() {
        assert_eq!(message_copy_text("hello\n", &["beat".into()]), "hello\n");
        assert_eq!(message_copy_text("   ", &["one".into(), "".into(), "two".into()]), "one\n\ntwo");
        assert_eq!(message_copy_text("", &[]), "");
        assert!(message_copy_text("  ", &["\n".into()]).is_empty());
    }

    fn clean_resend_strips_the_attachment_block() {
        let raw = "please review\n\n📎 Attachments:\n- notes.txt (12 B)";
        assert_eq!(clean_resend_text(raw), "please review");
        assert_eq!(clean_resend_text("  just text  "), "just text");
        assert_eq!(clean_resend_text("\n\n📎 Attachments:\n- a.png"), "");
    }

    #[test]
    fn regen_finds_the_preceding_user_turn() {
        let msgs = vec![
            ChatMessage::plain("u1", "user", "first"),
            ChatMessage::plain("a1", "assistant", "reply one"),
            ChatMessage::plain("u2", "user", "second"),
            ChatMessage::plain("a2", "assistant", "reply two"),
        ];
        assert_eq!(
            preceding_user_for_regen(&msgs, "a2"),
            Some(("u2".into(), "second".into()))
        );
        assert_eq!(
            preceding_user_for_regen(&msgs, "a1"),
            Some(("u1".into(), "first".into()))
        );
        assert_eq!(preceding_user_for_regen(&msgs, "missing"), None);
        assert_eq!(preceding_user_for_regen(&msgs, "u1"), None);
    }

    #[test]
    fn session_sync_events_refresh_the_sidebar() {
        assert!(is_session_sync_event("session_created"));
        assert!(is_session_sync_event("session_updated"));
        assert!(is_session_sync_event("message_added"));
        assert!(is_session_sync_event("session_deleted"));
        assert!(!is_session_sync_event("hello"));
        assert!(!is_session_sync_event("token"));
    }

    #[test]
    fn session_sync_reloads_the_open_thread_only_when_idle() {
        assert!(should_reload_open_thread(
            "message_added",
            "s1",
            Some("s1"),
            false
        ));
        assert!(should_reload_open_thread(
            "session_updated",
            "s1",
            Some("s1"),
            false
        ));
        assert!(!should_reload_open_thread(
            "message_added",
            "s1",
            Some("s1"),
            true
        ));
        assert!(!should_reload_open_thread(
            "message_added",
            "s2",
            Some("s1"),
            false
        ));
        assert!(!should_reload_open_thread("session_created", "s1", Some("s1"), false));
        assert!(!should_reload_open_thread("session_deleted", "s1", Some("s1"), false));
    }

    #[test]
    fn session_sync_reads_the_session_id() {
        let data = r#"{"type":"session_created","session_id":"s9","title":"From messenger"}"#;
        assert_eq!(event_session_id(data), "s9");
        assert_eq!(event_session_id("not-json"), "");
    }

    #[test]
    fn session_sync_backoff_matches_the_tauri_shell() {
        assert_eq!(next_session_sync_backoff_ms(1500), 2400);
        assert_eq!(next_session_sync_backoff_ms(2400), 3840);
        assert_eq!(next_session_sync_backoff_ms(30_000), 30_000);
        assert_eq!(SESSION_SYNC_DEBOUNCE_MS, 400);
    }
}
