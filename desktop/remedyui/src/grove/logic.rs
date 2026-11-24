//! Pure Grove rules, translated from `desktop/src/grove`.
//!
//! Storyline moments, the head's plain words, the floating bubble, and the
//! decision card's order and explain text. No widgets and no network, so the
//! same cases the TypeScript tests cover can run here.

use serde_json::Value;

/// `you-said`, `remedy-said`, or `remedy-did`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MomentKind {
    /// The owner spoke.
    YouSaid,
    /// Remedy spoke.
    RemedySaid,
    /// Remedy did something, in plain words.
    RemedyDid,
}

/// One block on the storyline.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Moment {
    /// Stable id.
    pub id: String,
    /// Who this block belongs to.
    pub kind: MomentKind,
    /// Plain-language body (never raw tool JSON).
    pub text: String,
    /// Timestamp from the source message.
    pub ts: String,
}

/// A tool call as the storyline sees it.
#[derive(Clone, Debug, PartialEq)]
pub struct ToolCall {
    /// Tool name (`computer.navigate` or `computer_navigate`).
    pub name: String,
    /// Arguments object.
    pub args: Value,
}

/// One chat row the storyline and the bubble read.
#[derive(Clone, Debug, PartialEq)]
pub struct StoryMessage {
    /// Stable id.
    pub id: String,
    /// `user`, `assistant`, `system`, or `tool`.
    pub role: String,
    /// Body text.
    pub content: String,
    /// True when the row was undone.
    pub reverted: bool,
    /// ISO timestamp.
    pub created_at: String,
    /// Tools this reply ran.
    pub tool_calls: Vec<ToolCall>,
}

/// Newest owner line and newest Remedy line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exchange {
    /// Newest user text, if any.
    pub you: Option<String>,
    /// Newest assistant text, if any.
    pub remedy: Option<String>,
}

/// One labeled line of "exactly what Remedy would do".
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FieldRow {
    /// Human label.
    pub label: String,
    /// Value as the owner should read it.
    pub value: String,
}

/// Either the raw command or labeled fields.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CommandExplain {
    /// Not a JSON object, or nothing worth listing.
    Raw(String),
    /// Label / value rows, runtime fields dropped.
    Fields(Vec<FieldRow>),
}

/// A decision waiting on the owner.
#[derive(Clone, Debug, PartialEq)]
pub struct Decision {
    /// Stable id.
    pub id: String,
    /// Tool name, when the runtime sent one.
    pub tool_name: String,
    /// Unix seconds or milliseconds, oldest first.
    pub created_at: i64,
    /// `pending` surfaces; anything else does not. Empty means pending.
    pub status: String,
}

/// Text inside the floating bubble.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BubbleMessage {
    /// Source message id.
    pub id: String,
    /// `user` or `assistant`.
    pub role: String,
    /// Visible text (the side-job prefix stripped on the owner's lines).
    pub text: String,
}

/// Bubble position in logical pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BubblePos {
    /// Distance from the left.
    pub x: f32,
    /// Distance from the top.
    pub y: f32,
}

/// A routine schedule the runtime accepts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Schedule {
    /// `every`, `daily`, `weekdays`, `weekly`, or `once`.
    pub kind: String,
    /// Minutes between runs, for `every`.
    pub every_minutes: Option<i64>,
    /// Clock time `HH:MM`.
    pub at: Option<String>,
    /// Weekday codes.
    pub days: Vec<String>,
    /// One-shot instant, UTC, no milliseconds.
    pub once_at: Option<String>,
}

/// How a routine dialog choice becomes a schedule.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScheduleChoice {
    /// Every hour.
    Hourly,
    /// Every few hours.
    Every,
    /// Every day at a clock time.
    Daily,
    /// Monday to Friday.
    Weekdays,
    /// Chosen weekdays.
    Weekly,
    /// A single instant.
    Once,
}

const BUBBLE_W: f32 = 340.0;

const RUNTIME_FIELDS: &[&str] = &[
    "caller_origin",
    "session_id",
    "home_dir",
    "workspace_root",
    "project_path",
    "write_roots",
    "owner_confirmed",
    "page_context",
    "_go_bound",
];

const QUIET_TOOLS: &[&str] = &[
    "computer.screenshot",
    "computer.snapshot",
    "computer.page.text",
    "computer.page_text",
    "computer.find",
    "computer.wait",
    "computer.monitors",
    "computer.uia.focused",
    "computer.uia.read.text",
    "computer.uia.read_text",
    "help.list",
    "help_list",
    "file.read",
    "read",
];

fn abi_name(name: &str) -> String {
    name.to_lowercase().replace('_', ".")
}

fn arg_str(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn host_of(url: &str) -> String {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    let host = rest.split(['/', '?', '#']).next().unwrap_or(rest);
    let host = host.split('@').next_back().unwrap_or(host);
    let host = host.split(':').next().unwrap_or(host);
    host.strip_prefix("www.").unwrap_or(host).to_string()
}

fn cap_first(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// Tool name to a plain verb phrase. `None` means the storyline folds it away.
pub fn describe_tool_call(call: &ToolCall) -> Option<String> {
    let name = abi_name(&call.name);
    let args = &call.args;
    let text = match name.as_str() {
        "computer.navigate" => {
            let url = arg_str(args, "url");
            if url.is_empty() {
                "Opened a page".into()
            } else {
                format!("Opened {}", host_of(&url))
            }
        }
        "computer.act" => {
            let mut bits = Vec::new();
            let url = arg_str(args, "url");
            if !url.is_empty() {
                bits.push(format!("went to {}", host_of(&url)));
            }
            let click = arg_str(args, "click");
            if !click.is_empty() {
                bits.push(format!("pressed “{click}”"));
            }
            if !arg_str(args, "type").is_empty() {
                bits.push("typed into the page".into());
            }
            let key = arg_str(args, "key");
            if !key.is_empty() {
                bits.push(format!("pressed {key}"));
            }
            if bits.is_empty() {
                "Worked the page".into()
            } else {
                cap_first(&bits.join(", "))
            }
        }
        "computer.click" => {
            let text = arg_str(args, "text");
            if text.is_empty() {
                "Clicked on the page".into()
            } else {
                format!("Pressed “{text}”")
            }
        }
        "computer.type" => "Typed into a field".into(),
        "computer.select" => {
            let value = arg_str(args, "value");
            let text = if value.is_empty() {
                arg_str(args, "text")
            } else {
                value
            };
            if text.is_empty() {
                "Chose a dropdown option".into()
            } else {
                format!("Chose “{text}”")
            }
        }
        "computer.fill" => "Filled in a form".into(),
        "computer.key" => {
            let key = arg_str(args, "key");
            if key.is_empty() {
                "Pressed a key".into()
            } else {
                format!("Pressed {key}")
            }
        }
        "computer.app" | "computer.window" => {
            let app = arg_str(args, "app");
            let title = if app.is_empty() {
                arg_str(args, "title")
            } else {
                app
            };
            if title.is_empty() {
                "Opened an app".into()
            } else {
                format!("Opened {title} on this PC")
            }
        }
        "computer.screenshot"
        | "computer.snapshot"
        | "computer.page.text"
        | "computer.page_text"
        | "computer.find"
        | "computer.uia.focused"
        | "computer.uia.read.text"
        | "computer.uia.read_text" => "Looked at the screen".into(),
        "computer.uia.action" => {
            let name = arg_str(args, "name");
            if name.is_empty() {
                "Used a control on this PC".into()
            } else {
                format!("Used “{name}” on this PC")
            }
        }
        "vault.list" => "Checked which stored secrets exist (never their values)".into(),
        "file.write" | "file.edit" | "edit" => {
            let path = arg_str(args, "path");
            if path.is_empty() {
                "Worked on a file".into()
            } else {
                format!("Worked on {path}")
            }
        }
        "bash.exec" | "shell.exec" | "host.run" => "Ran a command on this PC".into(),
        "mail.send" => "Sent an email (with your go-ahead)".into(),
        "web.search" => {
            let query = arg_str(args, "query");
            if query.is_empty() {
                "Searched the web".into()
            } else {
                format!("Searched the web for “{query}”")
            }
        }
        _ => return None,
    };
    Some(text)
}

/// Fold a chat into storyline moments: words, then what Remedy did, in order.
pub fn messages_to_moments(messages: &[StoryMessage]) -> Vec<Moment> {
    let mut out = Vec::new();
    for m in messages {
        if m.reverted {
            continue;
        }
        if m.role == "user" {
            let text = m.content.trim();
            if !text.is_empty() {
                out.push(Moment {
                    id: format!("{}:u", m.id),
                    kind: MomentKind::YouSaid,
                    text: text.to_string(),
                    ts: m.created_at.clone(),
                });
            }
            continue;
        }
        if m.role != "assistant" {
            continue;
        }
        let mut seen = Vec::new();
        for (i, call) in m.tool_calls.iter().enumerate() {
            if QUIET_TOOLS.iter().any(|q| *q == abi_name(&call.name)) {
                continue;
            }
            let Some(text) = describe_tool_call(call) else {
                continue;
            };
            if seen.iter().any(|s: &String| s == &text) {
                continue;
            }
            seen.push(text.clone());
            out.push(Moment {
                id: format!("{}:t{i}", m.id),
                kind: MomentKind::RemedyDid,
                text,
                ts: m.created_at.clone(),
            });
        }
        let text = m.content.trim();
        if !text.is_empty() {
            out.push(Moment {
                id: format!("{}:a", m.id),
                kind: MomentKind::RemedySaid,
                text: text.to_string(),
                ts: m.created_at.clone(),
            });
        }
    }
    out
}

/// The newest user line and the newest Remedy line, walking backward.
pub fn latest_exchange(messages: &[StoryMessage]) -> Exchange {
    let mut you = None;
    let mut remedy = None;
    for m in messages.iter().rev() {
        if m.reverted {
            continue;
        }
        let text = m.content.trim();
        if text.is_empty() {
            continue;
        }
        if remedy.is_none() && m.role == "assistant" {
            remedy = Some(text.to_string());
        } else if you.is_none() && m.role == "user" {
            you = Some(text.to_string());
        }
        if you.is_some() && remedy.is_some() {
            break;
        }
    }
    Exchange { you, remedy }
}

/// Plain words for a head state. Never the raw token.
pub fn state_words(state: &str) -> &'static str {
    match state {
        "working" => "Working",
        "queued" => "Waiting for room",
        "waiting" | "blocked" => "Needs you",
        "done" => "Done",
        "stopped" => "Stopped",
        "error" => "Hit a problem",
        _ => "Resting",
    }
}

/// A state that wants the owner's eyes.
pub fn needs_owner(state: &str) -> bool {
    matches!(state, "waiting" | "blocked" | "error")
}

/// "in 3h", "in 12m", "now" — `iso` against `now_ms`.
pub fn relative_when(iso: Option<&str>, now_ms: i64) -> String {
    let Some(iso) = iso.filter(|s| !s.is_empty()) else {
        return String::new();
    };
    let Some(t) = parse_rfc3339_ms(iso) else {
        return String::new();
    };
    let diff = t - now_ms;
    let abs = diff.unsigned_abs();
    let mins = ((abs as f64) / 60_000.0).round() as u64;
    if mins < 1 {
        return if diff >= 0 {
            "now".into()
        } else {
            "just now".into()
        };
    }
    let span = if mins < 60 {
        format!("{mins}m")
    } else if mins < 60 * 24 {
        format!("{}h", ((mins as f64) / 60.0).round() as u64)
    } else {
        format!("{}d", ((mins as f64) / (60.0 * 24.0)).round() as u64)
    };
    if diff >= 0 {
        format!("in {span}")
    } else {
        format!("{span} ago")
    }
}

/// Parse `YYYY-MM-DDTHH:MM:SSZ` (optional fraction) to Unix milliseconds.
pub fn parse_rfc3339_ms(s: &str) -> Option<i64> {
    let s = s.trim();
    if s.len() < 19 {
        return None;
    }
    let b = s.as_bytes();
    if b.get(10).copied() != Some(b'T') {
        return None;
    }
    let year: i32 = s.get(0..4)?.parse().ok()?;
    let month: u32 = s.get(5..7)?.parse().ok()?;
    let day: u32 = s.get(8..10)?.parse().ok()?;
    let hour: u32 = s.get(11..13)?.parse().ok()?;
    let min: u32 = s.get(14..16)?.parse().ok()?;
    let sec: u32 = s.get(17..19)?.parse().ok()?;
    if !(1..=12).contains(&month) || day == 0 || day > 31 || hour > 23 || min > 59 || sec > 60 {
        return None;
    }
    let mut rest = &s[19..];
    if rest.starts_with('.') {
        rest = rest.trim_start_matches('.');
        let digits = rest.chars().take_while(|c| c.is_ascii_digit()).count();
        rest = &rest[digits..];
    }
    let mut offset_min: i32 = 0;
    if rest == "Z" || rest.is_empty() {
        offset_min = 0;
    } else if let Some(sign) = rest.chars().next() {
        if sign == '+' || sign == '-' {
            let hh: i32 = rest.get(1..3)?.parse().ok()?;
            let mm: i32 = if rest.len() >= 6 && rest.as_bytes().get(3) == Some(&b':') {
                rest.get(4..6)?.parse().ok()?
            } else if rest.len() >= 5 {
                rest.get(3..5)?.parse().ok()?
            } else {
                0
            };
            offset_min = hh * 60 + mm;
            if sign == '-' {
                offset_min = -offset_min;
            }
        } else {
            return None;
        }
    }
    Some(ymd_hms_to_unix_ms(year, month, day, hour, min, sec)? - i64::from(offset_min) * 60_000)
}

fn ymd_hms_to_unix_ms(year: i32, month: u32, day: u32, hour: u32, min: u32, sec: u32) -> Option<i64> {
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = (y - era * 400) as u32;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = i64::from(era) * 146097 + i64::from(doe) - 719468;
    let secs = days * 86_400 + i64::from(hour) * 3600 + i64::from(min) * 60 + i64::from(sec);
    Some(secs * 1000)
}

/// Format a Unix millisecond instant as `YYYY-MM-DDTHH:MM:SSZ`.
pub fn format_unix_ms_z(ms: i64) -> String {
    let secs = ms.div_euclid(1000);
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400) as u32;
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { y + 1 } else { y };
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}Z")
}

/// Build a schedule from the routine dialog's simple choices.
pub fn schedule_from_choice(
    choice: ScheduleChoice,
    at: Option<&str>,
    every_hours: Option<f64>,
    days: &[String],
    once_local: Option<&str>,
    now_ms: i64,
) -> Schedule {
    match choice {
        ScheduleChoice::Hourly => Schedule {
            kind: "every".into(),
            every_minutes: Some(60),
            at: None,
            days: Vec::new(),
            once_at: None,
        },
        ScheduleChoice::Every => {
            let hours = every_hours.unwrap_or(1.0).round() as i64;
            Schedule {
                kind: "every".into(),
                every_minutes: Some(hours.max(1) * 60),
                at: None,
                days: Vec::new(),
                once_at: None,
            }
        }
        ScheduleChoice::Daily => Schedule {
            kind: "daily".into(),
            every_minutes: None,
            at: Some(at.filter(|s| !s.is_empty()).unwrap_or("08:00").into()),
            days: Vec::new(),
            once_at: None,
        },
        ScheduleChoice::Weekdays => Schedule {
            kind: "weekdays".into(),
            every_minutes: None,
            at: Some(at.filter(|s| !s.is_empty()).unwrap_or("08:00").into()),
            days: Vec::new(),
            once_at: None,
        },
        ScheduleChoice::Weekly => Schedule {
            kind: "weekly".into(),
            every_minutes: None,
            at: Some(at.filter(|s| !s.is_empty()).unwrap_or("08:00").into()),
            days: if days.is_empty() {
                vec!["mon".into()]
            } else {
                days.to_vec()
            },
            once_at: None,
        },
        ScheduleChoice::Once => {
            let ms = once_local
                .and_then(parse_local_minute)
                .unwrap_or(now_ms + 3_600_000);
            Schedule {
                kind: "once".into(),
                every_minutes: None,
                at: None,
                days: Vec::new(),
                once_at: Some(format_unix_ms_z(ms)),
            }
        }
    }
}

/// `YYYY-MM-DDTHH:MM` with no zone, read as UTC minutes (the dialog's local field).
fn parse_local_minute(s: &str) -> Option<i64> {
    let s = s.trim();
    if s.len() < 16 {
        return None;
    }
    let with_sec = if s.len() == 16 {
        format!("{s}:00Z")
    } else if s.ends_with('Z') {
        s.to_string()
    } else {
        format!("{s}Z")
    };
    parse_rfc3339_ms(&with_sec)
}

/// Poll fast while the child works, slow otherwise.
pub fn child_poll_ms(state: &str) -> u64 {
    if state == "working" || state == "queued" {
        2_000
    } else {
        10_000
    }
}

/// The words that reach Remedy when the owner types in a child's room.
pub fn about_child(label: &str, text: &str) -> String {
    format!("About the side job “{label}”: {}", text.trim())
}

/// Drop the "About the side job …:" prefix that names the job for Remedy.
pub fn strip_about_child(text: &str) -> String {
    const PREFIX: &str = "About the side job “";
    let Some(rest) = text.strip_prefix(PREFIX) else {
        return text.to_string();
    };
    let Some(end) = rest.find('”') else {
        return text.to_string();
    };
    let after = &rest[end + '”'.len_utf8()..];
    let Some(after) = after.strip_prefix(':') else {
        return text.to_string();
    };
    after.trim_start().to_string()
}

/// Remedy's messages after `anchor_id` (`""` = all), user and assistant text only.
pub fn bubble_slice(messages: &[StoryMessage], anchor_id: &str) -> Vec<BubbleMessage> {
    let idx = if anchor_id.is_empty() {
        None
    } else {
        messages.iter().position(|m| m.id == anchor_id)
    };
    let start = idx.map(|i| i + 1).unwrap_or(0);
    messages
        .iter()
        .skip(start)
        .filter(|m| {
            (m.role == "user" || m.role == "assistant") && !m.content.trim().is_empty()
        })
        .map(|m| BubbleMessage {
            id: m.id.clone(),
            role: m.role.clone(),
            text: if m.role == "user" {
                strip_about_child(&m.content)
            } else {
                m.content.clone()
            },
        })
        .collect()
}

/// Keep the bubble fully on screen for a viewport of `(vw, vh)`.
pub fn clamp_bubble(pos: BubblePos, vw: f32, vh: f32) -> BubblePos {
    let pad = 8.0;
    BubblePos {
        x: pos.x.min((vw - BUBBLE_W - pad).max(pad)).max(pad),
        y: pos.y.min((vh - 60.0).max(pad)).max(pad),
    }
}

/// Oldest pending decision first. Internal life-drive rows never surface.
pub fn order_decisions(items: &[Decision]) -> Vec<Decision> {
    let mut out: Vec<Decision> = items
        .iter()
        .filter(|i| {
            i.tool_name != "life_drive" && (i.status.is_empty() || i.status == "pending")
        })
        .cloned()
        .collect();
    out.sort_by_key(|i| i.created_at);
    out
}

/// Reasons that only restate that a card exists.
pub fn is_generic_reason(reason: &str) -> bool {
    let r = reason.trim().to_lowercase();
    r.is_empty() || r == "tool requires your approval" || r == "approval required"
}

/// What exactly would run. JSON objects become labeled rows; runtime fields drop.
pub fn explain_command(command: &str) -> CommandExplain {
    let raw = command.trim();
    if !raw.starts_with('{') {
        return CommandExplain::Raw(raw.to_string());
    }
    let Some(entries) = ordered_object(raw) else {
        return CommandExplain::Raw(raw.to_string());
    };
    let mut rows = Vec::new();
    for (k, v) in entries {
        if RUNTIME_FIELDS.contains(&k.as_str()) {
            continue;
        }
        if v.is_null() || v.as_str() == Some("") {
            continue;
        }
        let label = {
            let spaced = k.replace('_', " ");
            cap_first(&spaced)
        };
        let value = if let Some(s) = v.as_str() {
            s.to_string()
        } else {
            v.to_string()
        };
        rows.push(FieldRow { label, value });
    }
    if rows.is_empty() {
        CommandExplain::Raw(raw.to_string())
    } else {
        CommandExplain::Fields(rows)
    }
}

/// Top-level JSON object entries in source order (not map-key order).
fn ordered_object(raw: &str) -> Option<Vec<(String, Value)>> {
    let s = raw.trim();
    let bytes = s.as_bytes();
    if bytes.first() != Some(&b'{') {
        return None;
    }
    let mut i = 1;
    let mut out = Vec::new();
    loop {
        i = skip_ws(bytes, i);
        if i >= bytes.len() {
            return None;
        }
        if bytes[i] == b'}' {
            return Some(out);
        }
        if bytes[i] != b'"' {
            return None;
        }
        let (key, next) = read_string(s, i)?;
        i = skip_ws(bytes, next);
        if bytes.get(i) != Some(&b':') {
            return None;
        }
        i = skip_ws(bytes, i + 1);
        let end = skip_value(bytes, i)?;
        let slice = s.get(i..end)?;
        let value: Value = serde_json::from_str(slice).ok()?;
        out.push((key, value));
        i = skip_ws(bytes, end);
        if bytes.get(i) == Some(&b',') {
            i += 1;
            continue;
        }
        if bytes.get(i) == Some(&b'}') {
            return Some(out);
        }
        return None;
    }
}

fn skip_ws(bytes: &[u8], mut i: usize) -> usize {
    while i < bytes.len() && bytes[i].is_ascii_whitespace() {
        i += 1;
    }
    i
}

fn read_string(s: &str, start: usize) -> Option<(String, usize)> {
    let bytes = s.as_bytes();
    if bytes.get(start) != Some(&b'"') {
        return None;
    }
    let mut i = start + 1;
    let mut out = String::new();
    while i < bytes.len() {
        match bytes[i] {
            b'"' => return Some((out, i + 1)),
            b'\\' => {
                let n = *bytes.get(i + 1)?;
                match n {
                    b'"' | b'\\' | b'/' => out.push(n as char),
                    b'n' => out.push('\n'),
                    b't' => out.push('\t'),
                    b'r' => out.push('\r'),
                    _ => out.push(n as char),
                }
                i += 2;
            }
            c => {
                out.push(c as char);
                i += 1;
            }
        }
    }
    None
}

fn skip_value(bytes: &[u8], i: usize) -> Option<usize> {
    let b = *bytes.get(i)?;
    match b {
        b'"' => skip_string_bytes(bytes, i),
        b'{' | b'[' => skip_container(bytes, i),
        b't' => skip_lit(bytes, i, b"true"),
        b'f' => skip_lit(bytes, i, b"false"),
        b'n' => skip_lit(bytes, i, b"null"),
        b'-' | b'0'..=b'9' => skip_number(bytes, i),
        _ => None,
    }
}

fn skip_lit(bytes: &[u8], i: usize, lit: &[u8]) -> Option<usize> {
    if bytes.get(i..i + lit.len()) == Some(lit) {
        Some(i + lit.len())
    } else {
        None
    }
}

fn skip_number(bytes: &[u8], mut i: usize) -> Option<usize> {
    if bytes.get(i) == Some(&b'-') {
        i += 1;
    }
    if i >= bytes.len() || !bytes[i].is_ascii_digit() {
        return None;
    }
    while i < bytes.len() && matches!(bytes[i], b'0'..=b'9' | b'.' | b'e' | b'E' | b'+' | b'-') {
        i += 1;
    }
    Some(i)
}

fn skip_string_bytes(bytes: &[u8], start: usize) -> Option<usize> {
    let mut i = start + 1;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => return Some(i + 1),
            b'\\' => i += 2,
            _ => i += 1,
        }
    }
    None
}

fn skip_container(bytes: &[u8], start: usize) -> Option<usize> {
    let open = bytes[start];
    let close = if open == b'{' { b'}' } else { b']' };
    let mut depth = 1;
    let mut i = start + 1;
    while i < bytes.len() {
        match bytes[i] {
            b'"' => i = skip_string_bytes(bytes, i)?,
            b'{' | b'[' => {
                depth += 1;
                i += 1;
            }
            c if c == close => {
                depth -= 1;
                i += 1;
                if depth == 0 {
                    return Some(i);
                }
            }
            _ => i += 1,
        }
    }
    None
}

/// Headline for a decision card, including "via …" when it did not start here.
pub fn approval_headline(summary: &str, reason: &str, origin: &str, channel: &str) -> String {
    let base = if summary.trim().is_empty() {
        reason.trim()
    } else {
        summary.trim()
    };
    let Some(origin) = approval_origin_label(origin, channel) else {
        return base.to_string();
    };
    if base.is_empty() {
        format!("Request via {origin}")
    } else {
        format!("{base} · via {origin}")
    }
}

fn approval_origin_label(origin: &str, channel: &str) -> Option<String> {
    let raw = if origin.trim().is_empty() {
        channel.trim()
    } else {
        origin.trim()
    };
    if raw.is_empty() {
        return None;
    }
    let key = raw.to_lowercase();
    let known = match key.as_str() {
        "telegram" => "Telegram",
        "discord" => "Discord",
        "whatsapp" => "WhatsApp",
        "signal" => "Signal",
        "slack" => "Slack",
        "sms" => "SMS",
        "phone" => "a phone call",
        "voice" => "voice",
        "email" => "email",
        "desktop" => "this desktop",
        "webui" => "the web UI",
        "api" => "the API",
        "automation" => "an automation",
        "cron" => "a scheduled task",
        _ => "",
    };
    if !known.is_empty() {
        return Some(known.into());
    }
    if key.starts_with("hive:") {
        return Some("a hive task".into());
    }
    if key.starts_with("partner:child:") {
        return Some("a side job".into());
    }
    if key.starts_with("partner:routine:") {
        return Some("a routine".into());
    }
    if key.starts_with("partner:goal:") {
        return Some("goal autopilot".into());
    }
    if key.starts_with("partner:") {
        return Some("background work".into());
    }
    Some(title_token(&raw))
}

fn title_token(raw: &str) -> String {
    let spaced = raw.replace(['_', '-'], " ");
    spaced
        .split_whitespace()
        .map(cap_first)
        .collect::<Vec<_>>()
        .join(" ")
}

/// "I want to …" plants a goal. Returns the title, or nothing.
pub fn planted_title(text: &str) -> Option<String> {
    let t = text.trim();
    let prefix = "i want to ";
    if t.len() >= prefix.len() && t[..prefix.len()].eq_ignore_ascii_case(prefix) {
        let title = t[prefix.len()..].trim();
        if !title.is_empty() {
            return Some(title.to_string());
        }
    }
    None
}

/// A starter that ends in space or an ellipsis prefills instead of sending.
pub fn starter_prefills(text: &str) -> bool {
    text.ends_with([' ', '\n', '\t', '…'])
}

/// Greeting key for an hour in 0–23.
pub fn hello_key(hour: u32) -> &'static str {
    if hour < 5 {
        "grove-hello-late"
    } else if hour < 12 {
        "grove-hello-morning"
    } else if hour < 18 {
        "grove-hello-afternoon"
    } else {
        "grove-hello-evening"
    }
}

/// Grove home starters. A trailing space means "prefill, don't send".
pub fn home_starters() -> &'static [(&'static str, &'static str)] {
    &[
        (
            "What can you help with?",
            "What kinds of things can you help me with, day to day?",
        ),
        ("Plant a goal", "I want to "),
        (
            "Plan my day",
            "Help me plan my day — ask me what’s on my plate.",
        ),
        (
            "Check on my goals",
            "How are my goals doing? Anything that needs me today?",
        ),
    ]
}

/// Goal-room starters.
pub fn goal_starters() -> &'static [(&'static str, &'static str)] {
    &[
        (
            "Where are we?",
            "Catch me up on this goal — where are we, and what’s next?",
        ),
        (
            "Do the next step",
            "Take the next step on this goal now — I’ll watch.",
        ),
        ("Add a note", "A note for this goal: "),
    ]
}

/// One build-checklist row, as the stream's `todos` frame describes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TodoRow {
    /// Stable id, or the text when the frame omitted one.
    pub id: String,
    /// Words on the row.
    pub content: String,
    /// `pending`, `in_progress`, `completed`, or `cancelled`.
    pub status: String,
}

/// Normalize a todo status. Anything else counts as pending.
pub fn todo_status(raw: &str) -> &'static str {
    match raw {
        "in_progress" | "completed" | "cancelled" => {
            if raw == "in_progress" {
                "in_progress"
            } else if raw == "completed" {
                "completed"
            } else {
                "cancelled"
            }
        }
        _ => "pending",
    }
}

/// Parse a `todos` SSE payload. Rows without text are dropped.
pub fn parse_todos(data: &str) -> Vec<TodoRow> {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return Vec::new();
    };
    let Some(raw) = v.get("todos").and_then(|t| t.as_array()) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for row in raw {
        let content = row
            .get("content")
            .and_then(|c| c.as_str())
            .filter(|s| !s.trim().is_empty())
            .or_else(|| row.get("title").and_then(|c| c.as_str()))
            .unwrap_or("")
            .trim();
        if content.is_empty() {
            continue;
        }
        let content: String = content.chars().take(240).collect();
        let id = row
            .get("id")
            .and_then(|i| i.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or(content.as_str());
        let id: String = id.chars().take(48).collect();
        let status = todo_status(
            row.get("status")
                .and_then(|s| s.as_str())
                .unwrap_or("pending"),
        );
        out.push(TodoRow {
            id,
            content,
            status: status.into(),
        });
    }
    out
}

/// The checklist stays up while any row is still pending or in progress.
pub fn todos_open(items: &[TodoRow]) -> bool {
    items.iter().any(|t| {
        let s = todo_status(&t.status);
        s == "pending" || s == "in_progress"
    })
}

/// The mark beside a checklist row.
pub fn todo_mark(status: &str) -> &'static str {
    match todo_status(status) {
        "completed" => "✓",
        "cancelled" => "–",
        "in_progress" => "›",
        _ => "",
    }
}

/// Kind of Studio session Remedy should open from a Grove request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StudioKind {
    Chat,
    Plan,
    Build,
}

impl StudioKind {
    pub fn label(self) -> &'static str {
        match self {
            Self::Chat => "Talk",
            Self::Plan => "Plan",
            Self::Build => "Build",
        }
    }
}

/// If this Grove request is work for Studio, the session kind to open.
/// Life, goals, and everyday talk stay in Grove. Conservative: when unsure, stay.
pub fn studio_handoff(text: &str) -> Option<StudioKind> {
    let t = text.trim();
    if t.is_empty() || planted_title(t).is_some() {
        return None;
    }
    if !looks_like_studio_work(t) {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    if has_phrase(&lower, &["help me plan", "outline", "design the steps", "plan the", "plan a refactor", "plan this"])
        && !has_phrase(&lower, &["plan my day", "plan the day"])
    {
        return Some(StudioKind::Plan);
    }
    if has_phrase(&lower, &["explain", "what does", "walk me through", "what is this"])
        && !has_phrase(&lower, &["fix", "implement", "patch", "debug", "compile", "refactor", "write a"])
    {
        return Some(StudioKind::Chat);
    }
    Some(StudioKind::Build)
}

fn looks_like_studio_work(text: &str) -> bool {
    let lower = format!(" {} ", text.to_ascii_lowercase());
    const PHRASES: &[&str] = &[
        "stack trace",
        "pull request",
        "unit test",
        "type error",
        "syntax error",
        "write a function",
        "open a pr",
        "manifest.json",
        "init_main",
    ];
    if PHRASES.iter().any(|p| lower.contains(p)) {
        return true;
    }
    const WORDS: &[&str] = &[
        "rust", "rustc", "lua", "python", "javascript", "typescript", "cargo",
        "clippy", "npm", "crate", "addon", "repo", "github", "git", "refactor",
        "implement", "compile", "compiler", "debug", "bugfix", "lint", "patch",
        "loader", "typo", "endpoint", "segfault", "sql", "css",
    ];
    has_word(&lower, WORDS)
}

fn has_phrase(lower: &str, phrases: &[&str]) -> bool {
    phrases.iter().any(|p| lower.contains(p))
}

fn has_word(lower: &str, words: &[&str]) -> bool {
    // `lower` may already be padded; split on non-identifier characters.
    lower
        .split(|c: char| !c.is_ascii_alphanumeric() && c != '_')
        .any(|w| words.iter().any(|cue| *cue == w))
}

/// Home, with no attachments, and an "I want to …" line plants a goal.
pub fn should_plant(on_home: bool, text: &str, attachments: usize) -> bool {
    on_home && attachments == 0 && planted_title(text).is_some()
}

/// A live turn with no attachment takes a steer instead of a new send.
pub fn steer_first(streaming: bool, attachments: usize) -> bool {
    streaming && attachments == 0
}

/// Grove's browser stage home, the same page the current app opens.
pub const BROWSER_HOME: &str = "https://github.com/AhmiDarrow/RemedyAI";

/// Address bar: a real http(s) URL, or a DuckDuckGo search. Dangerous schemes become empty.
pub fn resolve_address(raw: &str) -> String {
    let raw = raw.trim();
    if raw.is_empty() {
        return String::new();
    }
    let lower = raw.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("data:")
        || lower.starts_with("vbscript:")
        || lower.starts_with("file:")
    {
        return String::new();
    }
    if let Some(url) = bare_https(raw) {
        return url;
    }
    let mut enc = String::new();
    for b in raw.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                enc.push(b as char);
            }
            b' ' => enc.push('+'),
            _ => enc.push_str(&format!("%{b:02X}")),
        }
    }
    format!("https://duckduckgo.com/?q={enc}")
}

fn bare_https(raw: &str) -> Option<String> {
    if raw.contains(' ') || raw.contains('@') && !raw.to_ascii_lowercase().starts_with("http") {
        return None;
    }
    let mut u = raw.to_string();
    let lower = u.to_ascii_lowercase();
    if lower.starts_with("about:") {
        return Some(u);
    }
    if !lower.starts_with("http://") && !lower.starts_with("https://") {
        let host = u.split(['?', '#']).next().unwrap_or(u.as_str());
        let ok = host == "localhost"
            || host.split(':').next() == Some("localhost")
            || looks_like_host(host);
        if !ok {
            return None;
        }
        u = format!("https://{u}");
    }
    if u.split("://").nth(1)?.split(['/', '?', '#']).next()?.contains('@') {
        return None;
    }
    Some(u)
}

fn looks_like_host(host: &str) -> bool {
    let host = host.split('/').next().unwrap_or(host);
    let Some((name, tld)) = host.rsplit_once('.') else {
        return false;
    };
    !name.is_empty()
        && tld.len() >= 2
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
}

/// Example plots shown only while the board is empty.
pub fn example_plots() -> &'static [(super::marks::PlotMark, &'static str, &'static str)] {
    use super::marks::PlotMark;
    &[
        (
            PlotMark::Receipts,
            "Sort out this month’s receipts",
            "Drop a photo — I’ll total and organize them",
        ),
        (
            PlotMark::Trip,
            "Plan a weekend trip",
            "Tell me where and when; I’ll research and draft it",
        ),
        (
            PlotMark::Resume,
            "Polish my resume",
            "Share it and the role — we’ll tailor it together",
        ),
        (
            PlotMark::Usuals,
            "Reorder the usuals",
            "I can shop with you, you approve the checkout",
        ),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn call(name: &str, args: Value) -> ToolCall {
        ToolCall {
            name: name.into(),
            args,
        }
    }

    fn msg(id: &str, role: &str, content: &str) -> StoryMessage {
        StoryMessage {
            id: id.into(),
            role: role.into(),
            content: content.into(),
            reverted: false,
            created_at: "2026-08-16T09:00:00Z".into(),
            tool_calls: Vec::new(),
        }
    }

    #[test]
    fn tool_calls_become_plain_language() {
        assert_eq!(
            describe_tool_call(&call("computer_navigate", json!({"url": "https://www.kroger.com/cart"}))).as_deref(),
            Some("Opened kroger.com")
        );
        assert_eq!(
            describe_tool_call(&call("computer.navigate", json!({"url": "https://www.kroger.com/cart"}))).as_deref(),
            Some("Opened kroger.com")
        );
        assert_eq!(
            describe_tool_call(&call(
                "computer_act",
                json!({"url": "https://www.kroger.com", "click": "Add to Cart"})
            ))
            .as_deref(),
            Some("Went to kroger.com, pressed “Add to Cart”")
        );
        assert_eq!(
            describe_tool_call(&call("computer.click", json!({"text": "Sign in"}))).as_deref(),
            Some("Pressed “Sign in”")
        );
        assert_eq!(
            describe_tool_call(&call("computer_click", json!({"text": "Sign in"}))).as_deref(),
            Some("Pressed “Sign in”")
        );
        assert_eq!(
            describe_tool_call(&call("computer_select", json!({"value": "CA"}))).as_deref(),
            Some("Chose “CA”")
        );
        assert_eq!(
            describe_tool_call(&call("computer_fill", json!({"fields": []}))).as_deref(),
            Some("Filled in a form")
        );
        assert!(
            describe_tool_call(&call("mail_send", json!({})))
                .unwrap()
                .contains("go-ahead")
        );
        assert_eq!(
            describe_tool_call(&call("host_run", json!({"command": "exit /b 7"}))).as_deref(),
            Some("Ran a command on this PC")
        );
    }

    #[test]
    fn typed_secrets_stay_off_the_storyline() {
        let d = describe_tool_call(&call("computer_type", json!({"text": "hunter2-secret"}))).unwrap();
        assert_eq!(d, "Typed into a field");
        assert!(!d.contains("hunter2"));
    }

    #[test]
    fn unknown_tools_fold_away() {
        assert!(describe_tool_call(&call("weird_tool", json!({}))).is_none());
    }

    #[test]
    fn moments_follow_you_did_said() {
        let mut reply = msg("m2", "assistant", "Cart is ready — I stopped before paying.");
        reply.tool_calls = vec![
            call("computer_navigate", json!({"url": "https://www.kroger.com"})),
            call("computer_snapshot", json!({})),
            call("computer_act", json!({"click": "Add to Cart"})),
        ];
        let moments = messages_to_moments(&[
            msg("m1", "user", "Do the groceries, peppers not mushrooms"),
            reply,
        ]);
        assert_eq!(
            moments.iter().map(|m| m.kind).collect::<Vec<_>>(),
            vec![
                MomentKind::YouSaid,
                MomentKind::RemedyDid,
                MomentKind::RemedyDid,
                MomentKind::RemedySaid
            ]
        );
        assert!(moments[0].text.contains("groceries"));
        assert_eq!(moments[1].text, "Opened kroger.com");
        assert!(moments[3].text.contains("stopped before paying"));
    }

    #[test]
    fn moments_skip_reverted_system_and_dupes() {
        let mut reply = msg("a", "assistant", "");
        reply.tool_calls = vec![
            call("computer_click", json!({"text": "Next"})),
            call("computer_click", json!({"text": "Next"})),
        ];
        let mut reverted = msg("b", "user", "hello");
        reverted.reverted = true;
        let moments = messages_to_moments(&[msg("s", "system", "internal"), reverted, reply]);
        assert_eq!(moments.len(), 1);
        assert_eq!(moments[0].kind, MomentKind::RemedyDid);
    }

    #[test]
    fn latest_exchange_is_the_newest_pair() {
        let ex = latest_exchange(&[
            msg("1", "user", "old question"),
            msg("2", "assistant", "old answer"),
            msg("3", "user", "swap mushrooms for peppers"),
            msg("4", "assistant", "Done — total dropped 80 cents."),
        ]);
        assert_eq!(ex.you.as_deref(), Some("swap mushrooms for peppers"));
        assert!(ex.remedy.unwrap().contains("80 cents"));
        assert_eq!(latest_exchange(&[]), Exchange { you: None, remedy: None });
    }

    #[test]
    fn head_states_are_plain_words() {
        for s in ["idle", "queued", "working", "waiting", "blocked", "done", "stopped", "error", ""] {
            let w = state_words(s);
            assert!(!w.is_empty());
            assert!(!matches!(w, "queued" | "blocked" | "error" | "idle"));
        }
        assert_eq!(state_words("waiting"), state_words("blocked"));
        assert!(needs_owner("waiting") && needs_owner("blocked") && needs_owner("error"));
        for s in ["working", "queued", "done", "stopped", "idle", ""] {
            assert!(!needs_owner(s));
        }
    }

    #[test]
    fn relative_when_covers_future_past_and_junk() {
        let now = parse_rfc3339_ms("2026-11-19T12:00:00Z").unwrap();
        assert_eq!(relative_when(Some("2026-11-19T12:00:20Z"), now), "now");
        assert_eq!(relative_when(Some("2026-11-19T11:59:40Z"), now), "just now");
        assert_eq!(relative_when(Some("2026-11-19T12:12:00Z"), now), "in 12m");
        assert_eq!(relative_when(Some("2026-11-19T15:00:00Z"), now), "in 3h");
        assert_eq!(relative_when(Some("2026-11-17T12:00:00Z"), now), "2d ago");
        assert_eq!(relative_when(Some("not a date"), now), "");
        assert_eq!(relative_when(None, now), "");
    }

    #[test]
    fn schedules_match_the_dialog() {
        assert_eq!(
            schedule_from_choice(ScheduleChoice::Hourly, None, None, &[], None, 0).every_minutes,
            Some(60)
        );
        assert_eq!(
            schedule_from_choice(ScheduleChoice::Every, None, Some(3.0), &[], None, 0).every_minutes,
            Some(180)
        );
        assert_eq!(
            schedule_from_choice(ScheduleChoice::Every, None, Some(0.0), &[], None, 0).every_minutes,
            Some(60)
        );
        let weekdays = schedule_from_choice(ScheduleChoice::Weekdays, Some("07:15"), None, &[], None, 0);
        assert_eq!(weekdays.kind, "weekdays");
        assert_eq!(weekdays.at.as_deref(), Some("07:15"));
        let daily = schedule_from_choice(ScheduleChoice::Daily, None, None, &[], None, 0);
        assert_eq!(daily.at.as_deref(), Some("08:00"));
        let weekly = schedule_from_choice(ScheduleChoice::Weekly, Some("09:00"), None, &[], None, 0);
        assert_eq!(weekly.days, vec!["mon".to_string()]);
        let once = schedule_from_choice(
            ScheduleChoice::Once,
            None,
            None,
            &[],
            Some("2026-12-01T10:30"),
            0,
        );
        assert_eq!(once.kind, "once");
        let stamp = once.once_at.unwrap();
        assert!(stamp.ends_with('Z') && stamp.len() == 20, "{stamp}");
    }

    #[test]
    fn child_room_words_and_poll() {
        assert_eq!(
            about_child("Flights", "  cheaper please "),
            "About the side job “Flights”: cheaper please"
        );
        assert!(child_poll_ms("working") < child_poll_ms("done"));
        assert_eq!(child_poll_ms("queued"), child_poll_ms("working"));
        assert_eq!(child_poll_ms("waiting"), child_poll_ms("done"));
        let thread = vec![
            msg("a", "user", "old"),
            msg("b", "assistant", "old reply"),
            msg("c", "user", "About the side job “F”: hi"),
            msg("d", "tool", "ignored"),
            msg("e", "assistant", "   "),
            msg("f", "assistant", "Sure — on it"),
        ];
        let slice = bubble_slice(&thread, "b");
        assert_eq!(
            slice.iter().map(|m| m.id.as_str()).collect::<Vec<_>>(),
            vec!["c", "f"]
        );
        assert_eq!(slice[0].text, "hi");
        assert_eq!(
            bubble_slice(&thread, "")
                .iter()
                .map(|m| m.id.as_str())
                .collect::<Vec<_>>(),
            vec!["a", "b", "c", "f"]
        );
        assert_eq!(
            bubble_slice(&thread, "gone")
                .iter()
                .map(|m| m.id.as_str())
                .collect::<Vec<_>>(),
            vec!["a", "b", "c", "f"]
        );
    }

    #[test]
    fn bubble_stays_on_screen() {
        assert_eq!(
            clamp_bubble(BubblePos { x: -50.0, y: -50.0 }, 1200.0, 800.0),
            BubblePos { x: 8.0, y: 8.0 }
        );
        let far = clamp_bubble(BubblePos { x: 5000.0, y: 5000.0 }, 1200.0, 800.0);
        assert!(far.x <= 1200.0 - 340.0 - 8.0);
        assert!(far.y <= 800.0 - 60.0);
        assert_eq!(
            clamp_bubble(BubblePos { x: 100.0, y: 100.0 }, 200.0, 800.0).x,
            8.0
        );
    }

    #[test]
    fn decisions_are_oldest_pending_first() {
        let got = order_decisions(&[
            Decision { id: "b".into(), tool_name: String::new(), created_at: 20, status: "pending".into() },
            Decision { id: "drive".into(), tool_name: "life_drive".into(), created_at: 1, status: "pending".into() },
            Decision { id: "a".into(), tool_name: String::new(), created_at: 10, status: "pending".into() },
            Decision { id: "old".into(), tool_name: String::new(), created_at: 5, status: "approved".into() },
        ]);
        assert_eq!(
            got.iter().map(|d| d.id.as_str()).collect::<Vec<_>>(),
            vec!["a", "b"]
        );
    }

    #[test]
    fn explain_drops_runtime_fields_in_source_order() {
        match explain_command(
            r#"{"caller_origin":"","session_id":"s1","label":"Phone plans","task":"Compare plans","schedule":{"kind":"daily"}}"#,
        ) {
            CommandExplain::Fields(rows) => {
                assert_eq!(
                    rows,
                    vec![
                        FieldRow { label: "Label".into(), value: "Phone plans".into() },
                        FieldRow { label: "Task".into(), value: "Compare plans".into() },
                        FieldRow { label: "Schedule".into(), value: r#"{"kind":"daily"}"#.into() },
                    ]
                );
            }
            other => panic!("{other:?}"),
        }
        assert_eq!(
            explain_command("git push origin main"),
            CommandExplain::Raw("git push origin main".into())
        );
        assert_eq!(
            explain_command("{not json"),
            CommandExplain::Raw("{not json".into())
        );
        assert_eq!(
            explain_command(r#"{"session_id":"x"}"#),
            CommandExplain::Raw(r#"{"session_id":"x"}"#.into())
        );
        assert!(is_generic_reason("Tool requires your approval"));
        assert!(is_generic_reason(""));
        assert!(!is_generic_reason("Payment step — card ending 4242"));
    }

    #[test]
    fn headlines_name_where_a_request_came_from() {
        assert_eq!(
            approval_headline("Remedy wants to run a shell command", "x", "telegram", ""),
            "Remedy wants to run a shell command · via Telegram"
        );
        assert_eq!(approval_headline("ok", "r", "", ""), "ok");
        assert_eq!(approval_origin_label("matrix_bridge", "").as_deref(), Some("Matrix Bridge"));
        assert_eq!(approval_origin_label("hive:parent", "").as_deref(), Some("a hive task"));
        assert_eq!(
            approval_origin_label("partner:child:5f2e-41a8", "").as_deref(),
            Some("a side job")
        );
        assert_eq!(
            approval_origin_label("PARTNER:routine:rt-1", "").as_deref(),
            Some("a routine")
        );
        assert_eq!(
            approval_origin_label("partner:goal:abc", "").as_deref(),
            Some("goal autopilot")
        );
        assert_eq!(
            approval_origin_label("partner:other", "").as_deref(),
            Some("background work")
        );
    }

    #[test]
    fn planting_and_starters() {
        assert_eq!(planted_title("I want to sort the receipts").as_deref(), Some("sort the receipts"));
        assert!(planted_title("hello").is_none());
        assert!(starter_prefills("I want to "));
        assert!(starter_prefills("A note for this goal: "));
        assert!(!starter_prefills("Take the next step on this goal now — I’ll watch."));
        assert_eq!(hello_key(3), "grove-hello-late");
        assert_eq!(hello_key(9), "grove-hello-morning");
        assert_eq!(hello_key(14), "grove-hello-afternoon");
        assert_eq!(hello_key(20), "grove-hello-evening");
        assert!(should_plant(true, "I want to file the receipts", 0));
        assert!(!should_plant(true, "I want to file the receipts", 1));
        assert!(!should_plant(false, "I want to file the receipts", 0));
        assert!(steer_first(true, 0));
        assert!(!steer_first(true, 2));
        assert!(!steer_first(false, 0));
    }

    #[test]
    fn coding_requests_open_studio_and_life_stays_in_grove() {
        assert_eq!(
            studio_handoff("Get the portal addon loading again after the entry rename."),
            Some(StudioKind::Build)
        );
        assert_eq!(
            studio_handoff("Help me plan the addon loader rewrite."),
            Some(StudioKind::Plan)
        );
        assert_eq!(
            studio_handoff("Explain what init_main.lua should do."),
            Some(StudioKind::Chat)
        );
        assert!(studio_handoff("Help me plan my day — ask me what’s on my plate.").is_none());
        assert!(studio_handoff("How are my goals doing? Anything that needs me today?").is_none());
        assert!(studio_handoff("I want to get the garden going").is_none());
        assert!(studio_handoff("What's for dinner?").is_none());
        assert!(studio_handoff("What kinds of things can you help me with, day to day?").is_none());
    }

    #[test]
    fn checklist_shows_only_while_a_row_is_open() {
        let rows = parse_todos(
            r#"{"todos":[{"id":"a","content":"Write the note","status":"in_progress"},{"title":"Send it","status":"pending"},{"content":"","status":"pending"},{"content":"Old","status":"completed"}]}"#,
        );
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[1].id, "Send it");
        assert_eq!(rows[1].status, "pending");
        assert!(todos_open(&rows));
        assert_eq!(todo_mark("in_progress"), "›");
        assert_eq!(todo_mark("completed"), "✓");
        assert_eq!(todo_mark("cancelled"), "–");
        assert!(!todos_open(&[TodoRow {
            id: "a".into(),
            content: "Old".into(),
            status: "completed".into(),
        }]));
    }

    #[test]
    fn address_bar_rejects_scripts_and_searches_prose() {
        assert_eq!(resolve_address("javascript:alert(1)"), "");
        assert_eq!(
            resolve_address("github.com/AhmiDarrow/RemedyAI"),
            "https://github.com/AhmiDarrow/RemedyAI"
        );
        assert!(resolve_address("weekend trip ideas").starts_with("https://duckduckgo.com/?q="));
    }
}
