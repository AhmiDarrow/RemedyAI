//! Pure settings rules, ported from the React desktop so they can be tested
//! without a window.
//!
//! Search, categories, the save body, fonts, Sleev, Connect addresses, and
//! messenger drafts all live here. The widgets only read and write these.

use remedy_ui::themes::{PALETTES, Palette};
use serde_json::{Map, Value, json};

pub const CUSTOM_PROVIDER: &str = "custom";
pub const DEFAULT_BROWSER_HOME: &str = "https://github.com/AhmiDarrow/RemedyAI";
pub const CONNECT_LOOPBACK_WARNING: &str =
    "127.0.0.1 is only reachable on this computer. Pick a LAN address so the phone can connect.";

pub const PANE_KEYS: &[&str] = &[
    "live_ui",
    "chat",
    "approvals",
    "sessions",
    "rails",
    "computer_preview",
    "settings_write",
];

pub const CATEGORIES: &[(&str, &str, &str)] = &[
    ("general", "General", "Make Remedy comfortable to use."),
    ("models", "Models", "Choose how Remedy runs and which model it uses."),
    ("connections", "Connections", "Connect your devices, accounts, and messengers."),
    ("privacy", "Privacy", "Control access, approvals, and what you share."),
    ("system", "System", "Updates, help, and app preferences."),
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Section {
    pub id: &'static str,
    pub title: &'static str,
    pub summary: &'static str,
    pub keywords: &'static str,
    pub category: &'static str,
    pub advanced_only: bool,
    /// Shown in the header when it differs from the search title.
    pub display_title: Option<&'static str>,
    pub display_summary: Option<&'static str>,
}

pub const SECTIONS: &[Section] = &[
    Section { id: "you-agent", title: "You & Agent", summary: "Names, language & persona", keywords: "user name agent persona identity whoami soul language locale i18n", category: "general", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "voice", title: "Voice", summary: "Speak, hear, turn-taking", keywords: "voice speak hear mic kokoro whisper tts stt smart-turn turn-taking aloud quiet grove speech hq high-quality chatterbox human robot", category: "general", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "workspace", title: "Project workspace", summary: "Folder for tools", keywords: "project path folder directory cwd browser home homepage github url", category: "general", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "theme", title: "Appearance", summary: "Theme, text size, motion", keywords: "theme density accent dark light color appearance font size text large accessibility contrast motion reduce a11y presence face grove", category: "general", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "provider", title: "Provider", summary: "Model & API key", keywords: "llm openai anthropic xai ollama key model sleev gateway compression tokens", category: "models", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "provider-catalog", title: "Provider catalog", summary: "Which providers appear", keywords: "enable disable models budget skills catalog", category: "models", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "rmb", title: "RMB", summary: "Local agent · coding + tools", keywords: "rmb remedy muscle bridge local agent llama coding tools gguf qwen coder offline private huggingface hugging face pull download", category: "models", advanced_only: false, display_title: Some("Local models"), display_summary: Some("Run a model on this computer") },
    Section { id: "vision", title: "Local vision", summary: "SmolVLM2 · image decode", keywords: "vision local model smolvlm llama screenshot ocr image dependency", category: "models", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "memory-harness", title: "Memory harness", summary: "Chat compression", keywords: "harness compact prune context percent budget", category: "models", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "phone", title: "Phone", summary: "A voice on the line", keywords: "phone call sip telephony baresip line dial talk hq chatterbox", category: "connections", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "connect", title: "Connect", summary: "Use this PC from a phone on the same network", keywords: "connect grove phone remote pair qr lan wifi live ui approvals sessions rails pause relay", category: "connections", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "channels", title: "Messengers", summary: "Telegram, Discord, WhatsApp…", keywords: "telegram discord slack mattermost whatsapp teams matrix signal messenger channel bot gateway continuity", category: "connections", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "assistant", title: "Personal assistant", summary: "Calendar, mail, budget (local)", keywords: "assistant personal calendar gmail mail outlook hotmail yahoo budget debt bills brief oauth accounts money disclaimer", category: "connections", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "mcp", title: "MCP host", summary: "Expose skills to other apps", keywords: "mcp host server external client", category: "connections", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "access", title: "Access & permissions", summary: "Workspace and machine access", keywords: "scope untrusted home full project jail workspace default folder machine", category: "privacy", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "security-power", title: "Security & power", summary: "Approvals, trust, web, bootstrap", keywords: "approval auto ask shell web_fetch bootstrap token ssrf thinking privacy trust conservative balanced autonomous profile", category: "privacy", advanced_only: false, display_title: Some("Permissions"), display_summary: Some("When Remedy asks before acting") },
    Section { id: "privacy", title: "Privacy", summary: "What leaves this PC to your model", keywords: "privacy mode pii email phone scrub redact tool results llm egress cloud mail calendar page wipe memory forget persona soul claimidx", category: "privacy", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "always-ready", title: "Always ready", summary: "Startup & tray", keywords: "login tray startup quit windows", category: "system", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "tool-process", title: "Tool process", summary: "Visibility of tool steps", keywords: "process trail full medium off diagnostics", category: "system", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "advanced", title: "Advanced", summary: "Learning, logs, tone", keywords: "skill creation log level sarcasm threshold learning", category: "system", advanced_only: true, display_title: None, display_summary: None },
    Section { id: "help", title: "Help & shortcuts", summary: "Manual & keys", keywords: "f1 wiki hotkeys keyboard", category: "system", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "about", title: "About", summary: "Version & WebUI", keywords: "version update webui about", category: "system", advanced_only: false, display_title: None, display_summary: None },
    Section { id: "license", title: "License", summary: "Free tier & commercial", keywords: "license commercial free indie enterprise copyright", category: "system", advanced_only: false, display_title: None, display_summary: None },
];

pub fn section(id: &str) -> Option<&'static Section> {
    SECTIONS.iter().find(|s| s.id == id)
}

pub fn shown_title(sec: &Section) -> &'static str {
    sec.display_title.unwrap_or(sec.title)
}

pub fn shown_summary(sec: &Section) -> &'static str {
    sec.display_summary.unwrap_or(sec.summary)
}

/// Empty query matches every section. Otherwise every whitespace token must
/// appear in the title, the displayed title, the summary, or the keywords.
pub fn section_matches(sec: &Section, query: &str) -> bool {
    let tokens: Vec<String> = query
        .split_whitespace()
        .map(|t| t.to_lowercase())
        .filter(|t| !t.is_empty())
        .collect();
    if tokens.is_empty() {
        return true;
    }
    let hay = format!(
        "{} {} {} {} {} {}",
        sec.title,
        sec.summary,
        sec.keywords,
        sec.display_title.unwrap_or(""),
        sec.display_summary.unwrap_or(""),
        sec.id
    )
    .to_lowercase();
    tokens.iter().all(|t| hay.contains(t))
}

pub fn searching(query: &str) -> bool {
    !query.trim().is_empty()
}

/// Search crosses categories and shows advanced sections. Otherwise the
/// section must live in `category` and be allowed in `mode` (`simple` or
/// `advanced`).
pub fn section_visible(sec: &Section, category: &str, mode: &str, query: &str) -> bool {
    if searching(query) {
        section_matches(sec, query)
    } else {
        sec.category == category && (mode == "advanced" || !sec.advanced_only)
    }
}

pub fn category_label(id: &str) -> &str {
    CATEGORIES.iter().find(|(k, _, _)| *k == id).map(|(_, n, _)| *n).unwrap_or(id)
}

pub fn category_hint(id: &str) -> &'static str {
    CATEGORIES.iter().find(|(k, _, _)| *k == id).map(|(_, _, h)| *h).unwrap_or("")
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ThemeItem {
    pub id: String,
    pub name: String,
    pub bg: String,
    pub accent: String,
    /// Second half of a split swatch (System follows dark | light).
    pub split_bg: Option<String>,
}

pub fn palette_menu_name(id: &str, engine_name: &str) -> String {
    match id {
        "deutan" => "Deutan".into(),
        "protan" => "Protan".into(),
        "tritan" => "Tritan".into(),
        _ => engine_name.to_string(),
    }
}

/// Menu order from today's `buildThemeList`: System, Light, names that do not
/// start with "Dark", then Dark* with Neutral Dark directly under Dark.
pub fn theme_menu() -> Vec<ThemeItem> {
    let name_of = |p: &Palette| palette_menu_name(p.id, p.name);
    let light: Vec<&Palette> = PALETTES.iter().filter(|p| p.id == "light").collect();
    let rest: Vec<&Palette> = PALETTES.iter().filter(|p| p.id != "light" && p.id != "neutral").collect();
    let mut not_dark: Vec<&Palette> = rest.iter().copied().filter(|p| !name_of(p).starts_with("Dark")).collect();
    let mut dark: Vec<&Palette> = rest.iter().copied().filter(|p| name_of(p).starts_with("Dark")).collect();
    not_dark.sort_by_key(|p| name_of(p));
    dark.sort_by_key(|p| name_of(p));
    let neutral = PALETTES.iter().find(|p| p.id == "neutral");
    let dark_p = PALETTES.iter().find(|p| p.id == "dark");
    let light_p = PALETTES.iter().find(|p| p.id == "light");
    let mut out = Vec::new();
    out.push(ThemeItem {
        id: "system".into(),
        name: "System".into(),
        bg: dark_p.map(|p| p.bg.to_string()).unwrap_or_else(|| "#0c0c14".into()),
        accent: dark_p.map(|p| p.accent.to_string()).unwrap_or_else(|| "#8b5cf6".into()),
        split_bg: light_p.map(|p| p.bg.to_string()),
    });
    for p in light.into_iter().chain(not_dark) {
        out.push(item_from(p));
    }
    for p in dark {
        out.push(item_from(p));
        if p.id == "dark" {
            if let Some(n) = neutral {
                out.push(item_from(n));
            }
        }
    }
    out
}

fn item_from(p: &Palette) -> ThemeItem {
    ThemeItem {
        id: p.id.to_string(),
        name: palette_menu_name(p.id, p.name),
        bg: p.bg.to_string(),
        accent: p.accent.to_string(),
        split_bg: None,
    }
}

pub const EFFORTS: &[&str] = &["", "none", "minimal", "low", "medium", "high", "xhigh"];

pub fn token_limits(key: &str) -> Option<(i64, i64)> {
    match key {
        "thinking_budget_tokens" => Some((1024, 128_000)),
        "max_output_tokens" => Some((256, 256_000)),
        "context_window_tokens" => Some((4096, 2_000_000)),
        _ => None,
    }
}

/// Empty is valid (clears the limit). Otherwise a whole number inside the range.
pub fn token_error(key: &str, raw: &str) -> Option<String> {
    let (lo, hi) = token_limits(key)?;
    let s = raw.trim().replace(['_', ','], "");
    if s.is_empty() {
        return None;
    }
    match s.parse::<i64>() {
        Ok(n) if n >= lo && n <= hi => None,
        _ => Some(format!("Use a whole number from {lo} to {hi}, or leave empty.")),
    }
}

fn token_number(raw: &str) -> i64 {
    let s = raw.trim().replace(['_', ','], "");
    if s.is_empty() {
        0
    } else {
        s.parse::<i64>().unwrap_or(0)
    }
}

pub fn model_key(provider: &str, model: &str) -> String {
    let p = provider.trim();
    let m = model.trim();
    if !p.is_empty() && !m.is_empty() {
        format!("{p}/{m}")
    } else {
        m.to_string()
    }
}

pub fn form_effort(raw: &str) -> String {
    let e = raw.trim().to_lowercase();
    if EFFORTS.contains(&e.as_str()) { e } else { String::new() }
}

pub fn positive_token_text(n: Option<i64>) -> String {
    match n {
        Some(n) if n > 0 => n.to_string(),
        _ => String::new(),
    }
}

/// Settings patch for reasoning. Empty token fields send 0, which clears them.
pub fn generation_patch(scope: &str, provider: &str, model: &str, effort: &str, think: &str, out: &str, ctx: &str) -> Value {
    let values = json!({
        "reasoning_effort": form_effort(effort),
        "thinking_budget_tokens": token_number(think),
        "max_output_tokens": token_number(out),
        "context_window_tokens": token_number(ctx),
    });
    if scope == "model" {
        let key = model_key(provider, model);
        if key.is_empty() {
            return json!({});
        }
        return json!({ "model_settings": { key: values } });
    }
    values
}

pub fn couple_harness_min(min: f64, max: f64) -> Option<f64> {
    if min >= max {
        // Percent slider steps are whole points. Stay in cents so 0.95 + 0.02 is 0.97.
        let cents = ((min * 100.0).round() as i32 + 2).min(99);
        Some(cents as f64 / 100.0)
    } else {
        None
    }
}

pub fn couple_harness_max(min: f64, max: f64) -> Option<f64> {
    if max <= min {
        let cents = ((max * 100.0).round() as i32 - 2).max(5);
        Some(cents as f64 / 100.0)
    } else {
        None
    }
}

/// Host of a Sleev URL. A missing scheme is treated as `http://`.
pub fn sleev_host(url: &str) -> String {
    let t = url.trim();
    if t.is_empty() {
        return String::new();
    }
    let with = if t.contains("://") { t.to_string() } else { format!("http://{t}") };
    let rest = with.split_once("://").map(|(_, r)| r).unwrap_or(with.as_str());
    let rest = rest.split(['/', '?', '#']).next().unwrap_or(rest);
    let rest = rest.rsplit('@').next().unwrap_or(rest);
    if let Some(inner) = rest.strip_prefix('[') {
        return inner.split(']').next().unwrap_or("").to_string();
    }
    if rest.matches(':').count() == 1 {
        return rest.split(':').next().unwrap_or("").to_string();
    }
    rest.to_string()
}

pub fn sleev_loopback(host: &str) -> bool {
    let h = host.trim().trim_matches(['[', ']']).to_ascii_lowercase();
    matches!(h.as_str(), "localhost" | "127.0.0.1" | "0.0.0.0" | "::1") || h.starts_with("127.")
}

/// True when Save must stop: a non-loopback gateway without the remote opt-in.
pub fn sleev_remote_blocked(url: &str, allow_remote: bool) -> bool {
    if allow_remote {
        return false;
    }
    let host = sleev_host(url);
    if host.is_empty() {
        return false;
    }
    !sleev_loopback(&host)
}

pub fn is_connect_loopback(host: &str) -> bool {
    let h = host.trim();
    h == "127.0.0.1" || h.starts_with("127.")
}

fn ipv4_candidate(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() || s == "*" || s.contains('*') {
        return None;
    }
    let no_cidr = s.split('/').next().unwrap_or("");
    let host = if no_cidr.contains(':') { no_cidr.split(':').next().unwrap_or("") } else { no_cidr };
    if host.is_empty() || host == "0.0.0.0" || !is_ipv4(host) {
        return None;
    }
    Some(host.to_string())
}

fn is_ipv4(host: &str) -> bool {
    let parts: Vec<&str> = host.split('.').collect();
    if parts.len() != 4 {
        return false;
    }
    parts.iter().all(|p| !p.is_empty() && p.len() <= 3 && p.chars().all(|c| c.is_ascii_digit()) && p.parse::<u16>().ok().is_some_and(|n| n <= 255))
}

/// Drop wildcards, `0.0.0.0`, and non-IPv4. LAN first, loopback last, deduped.
pub fn filter_connect_addresses(addrs: &[String]) -> Vec<String> {
    let mut lan = Vec::new();
    let mut loopback = Vec::new();
    for item in addrs {
        let Some(host) = ipv4_candidate(item) else { continue };
        if lan.iter().chain(loopback.iter()).any(|h| h == &host) {
            continue;
        }
        if is_connect_loopback(&host) {
            loopback.push(host);
        } else {
            lan.push(host);
        }
    }
    lan.extend(loopback);
    lan
}

/// Keep an explicit current host (even loopback). Otherwise the first LAN
/// address, else the first remaining address.
pub fn preferred_connect_bind(addrs: &[String], current: &str) -> String {
    let cur = current.trim();
    if !cur.is_empty() {
        return cur.to_string();
    }
    let rows: Vec<String> = addrs.iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    rows.iter().find(|ip| !is_connect_loopback(ip)).cloned().or_else(|| rows.first().cloned()).unwrap_or_default()
}

/// Approvals stay on. Missing keys take the pane defaults.
pub fn merge_connect_panes(raw: &Value) -> Map<String, Value> {
    let mut next = Map::new();
    for key in PANE_KEYS {
        let default = !matches!(*key, "computer_preview" | "settings_write");
        let v = raw.get(*key).and_then(|v| v.as_bool()).unwrap_or(default);
        next.insert((*key).to_string(), Value::Bool(v));
    }
    next.insert("approvals".into(), Value::Bool(true));
    next
}

pub fn pane_label(key: &str) -> &str {
    match key {
        "live_ui" => "Live host UI",
        "chat" => "Chat/stream",
        "approvals" => "Approvals/Stop",
        "sessions" => "Sessions",
        "rails" => "Files/Terminal/Browser rails",
        "computer_preview" => "Computer preview",
        "settings_write" => "Settings write",
        _ => key,
    }
}

pub fn messenger_badge(origin: &str) -> String {
    match origin {
        "telegram" => "Telegram".into(),
        "discord" => "Discord".into(),
        "slack" => "Slack".into(),
        "mattermost" => "Mattermost".into(),
        "whatsapp" => "WhatsApp".into(),
        "teams" => "Teams".into(),
        "matrix" => "Matrix".into(),
        "google_chat" => "GChat".into(),
        "signal" => "Signal".into(),
        other => other.to_string(),
    }
}

/// Drafts from a messengers array. Arrays join with ", ". Null becomes "".
pub fn drafts_from_messengers(list: &Value) -> Value {
    let mut drafts = Map::new();
    let Some(arr) = list.as_array() else { return Value::Object(drafts) };
    for m in arr {
        let Some(id) = m.get("id").and_then(|v| v.as_str()) else { continue };
        let mut d = Map::new();
        d.insert("enabled".into(), Value::Bool(m.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false)));
        if let Some(fields) = m.get("fields").and_then(|v| v.as_object()) {
            for (k, v) in fields {
                let stored = if let Some(items) = v.as_array() {
                    Value::String(items.iter().filter_map(|i| i.as_str()).collect::<Vec<_>>().join(", "))
                } else if let Some(b) = v.as_bool() {
                    Value::Bool(b)
                } else if v.is_null() {
                    Value::String(String::new())
                } else if let Some(s) = v.as_str() {
                    Value::String(s.to_string())
                } else {
                    Value::String(v.to_string())
                };
                d.insert(k.clone(), stored);
            }
        }
        drafts.insert(id.to_string(), Value::Object(d));
    }
    Value::Object(drafts)
}

fn secret_keys(list: &Value, id: &str) -> Vec<String> {
    list.as_array()
        .and_then(|arr| arr.iter().find(|m| m.get("id").and_then(|v| v.as_str()) == Some(id)))
        .and_then(|m| m.get("field_schema").and_then(|v| v.as_array()))
        .map(|schema| {
            schema
                .iter()
                .filter(|f| f.get("kind").and_then(|v| v.as_str()) == Some("secret"))
                .filter_map(|f| f.get("key").and_then(|v| v.as_str()).map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

/// `None` when both the server list and the drafts are empty. Secret fields
/// are included only when the draft text is non-empty, so a blank box keeps
/// the stored credential.
pub fn messengers_update(list: &Value, drafts: &Value) -> Option<Value> {
    let empty_list = list.as_array().is_none_or(|a| a.is_empty());
    let empty_drafts = drafts.as_object().is_none_or(|o| o.is_empty());
    if empty_list && empty_drafts {
        return None;
    }
    let mut ids: Vec<String> = Vec::new();
    if let Some(arr) = list.as_array() {
        for m in arr {
            if let Some(id) = m.get("id").and_then(|v| v.as_str()) {
                if !ids.iter().any(|x| x == id) {
                    ids.push(id.to_string());
                }
            }
        }
    }
    if let Some(obj) = drafts.as_object() {
        for id in obj.keys() {
            if !ids.iter().any(|x| x == id) {
                ids.push(id.clone());
            }
        }
    }
    let mut body = Map::new();
    for id in ids {
        let draft = drafts.get(&id).cloned().unwrap_or_else(|| json!({}));
        let prev_enabled = list
            .as_array()
            .and_then(|arr| arr.iter().find(|m| m.get("id").and_then(|v| v.as_str()) == Some(id.as_str())))
            .and_then(|m| m.get("enabled").and_then(|v| v.as_bool()))
            .unwrap_or(false);
        let mut entry = Map::new();
        let enabled = draft.get("enabled").and_then(|v| v.as_bool()).unwrap_or(prev_enabled);
        entry.insert("enabled".into(), Value::Bool(enabled));
        let secrets = secret_keys(list, &id);
        if let Some(obj) = draft.as_object() {
            for (k, v) in obj {
                if k == "enabled" {
                    continue;
                }
                if secrets.iter().any(|s| s == k) {
                    if let Some(text) = v.as_str() {
                        if !text.trim().is_empty() {
                            entry.insert(k.clone(), Value::String(text.trim().to_string()));
                        }
                    }
                    continue;
                }
                entry.insert(k.clone(), v.clone());
            }
        }
        body.insert(id, Value::Object(entry));
    }
    Some(Value::Object(body))
}

/// Face id, family name the theme uses, and whether it is monospace.
pub const FACES: &[(&str, &str, bool)] = &[
    ("remedy-sans-ui", "Remedy Sans UI", false),
    ("remedy-sans", "Remedy Sans", false),
    ("ahmi-print", "AhmiPrint", false),
    ("ahmi-pixel", "Ahmi Pixel", false),
    ("inter", "Inter", false),
    ("cascadia-code", "Cascadia Code", true),
    ("jetbrains-mono", "JetBrains Mono", true),
    ("fira-code", "Fira Code", true),
    ("system", "system-ui", false),
    ("system-mono", "ui-monospace", true),
];

pub const FONT_ROLES: &[(&str, &str, &str, &str)] = &[
    ("ui", "Text", "Chat, menus and settings — the primary font.", "remedy-sans-ui"),
    ("heading", "Display", "Titles and section labels — the display font.", "ahmi-pixel"),
    ("reading", "Reading", "Long-form pages such as Help articles.", "ahmi-print"),
    ("accent", "Accent", "Small touches: the signature and shortcut keys.", "ahmi-pixel"),
    ("code", "Code", "Code blocks, diffs and logs.", "ahmi-print"),
];

pub fn family_for_face(id: &str) -> String {
    FACES.iter().find(|(k, _, _)| *k == id).map(|(_, fam, _)| (*fam).to_string()).unwrap_or_else(|| id.to_string())
}

pub fn face_for_family(family: &str) -> String {
    FACES
        .iter()
        .find(|(_, fam, _)| *fam == family)
        .map(|(id, _, _)| (*id).to_string())
        .unwrap_or_else(|| family.to_string())
}

pub fn face_label(id: &str) -> String {
    FACES.iter().find(|(k, _, _)| *k == id).map(|(_, fam, _)| (*fam).to_string()).unwrap_or_else(|| id.to_string())
}

/// Code leads with AhmiPrint, then monospace faces, then Ahmi Pixel.
pub fn faces_for_role(role: &str) -> Vec<&'static str> {
    let mono: Vec<&str> = FACES.iter().filter(|(_, _, m)| *m).map(|(id, _, _)| *id).collect();
    let text: Vec<&str> = FACES.iter().filter(|(_, _, m)| !*m).map(|(id, _, _)| *id).collect();
    if role == "code" {
        let mut out = vec!["ahmi-print"];
        out.extend(mono);
        out.push("ahmi-pixel");
        return out;
    }
    if role == "heading" || role == "display" {
        let mut out = vec!["ahmi-pixel"];
        out.extend(text.into_iter().filter(|id| *id != "ahmi-pixel"));
        out.extend(mono);
        return out;
    }
    let mut out = text;
    out.extend(mono);
    out
}

pub fn font_scale_factor(id: &str) -> f32 {
    match id {
        "sm" => 0.9,
        "lg" => 1.15,
        "xl" => 1.3,
        _ => 1.0,
    }
}

pub fn step_font_scale(current: &str, dir: i32) -> &'static str {
    const ORDER: [&str; 4] = ["sm", "md", "lg", "xl"];
    let i = ORDER.iter().position(|s| *s == current).unwrap_or(1) as i32;
    ORDER[(i + dir).clamp(0, 3) as usize]
}

pub const HOTKEYS: &[(&str, &str, &str)] = &[
    ("Enter", "composer", "Send message"),
    ("Shift+Enter", "composer", "Insert a new line"),
    ("↑", "composer", "Previous prompt (empty / start of single line)"),
    ("↓", "composer", "Next prompt (while browsing history)"),
    ("Ctrl+N", "global", "New chat session"),
    ("Ctrl+P", "global", "Open command palette"),
    ("Ctrl+K", "global", "Open command palette"),
    ("Ctrl+B", "global", "Toggle plan mode"),
    ("Shift+Tab", "global", "Toggle plan mode"),
    ("Ctrl+,", "global", "Open settings"),
    ("Ctrl+=", "global", "Larger text"),
    ("Ctrl++", "global", "Larger text"),
    ("Ctrl+-", "global", "Smaller text"),
    ("Ctrl+0", "global", "Reset text size"),
    ("Ctrl+/", "global", "Open Help wiki (owner's manual)"),
    ("F1", "global", "Open Help wiki (owner's manual)"),
    ("Escape", "global", "Close panels and command palette"),
];

pub fn first_lines(text: &str, n: usize) -> String {
    text.lines().take(n).collect::<Vec<_>>().join("\n")
}

pub fn phone_terms() -> String {
    const RAW: &str = include_str!("../../../../docs/TELEPHONY_TERMS.md");
    RAW.replace("](../LICENSE)", "](https://github.com/AhmiDarrow/RemedyAI/blob/master/LICENSE)")
        .replace("](./THIRD_PARTY.md)", "](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/THIRD_PARTY.md)")
        .replace("(../LICENSE)", "(https://github.com/AhmiDarrow/RemedyAI/blob/master/LICENSE)")
        .replace("(./THIRD_PARTY.md)", "(https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/THIRD_PARTY.md)")
}

pub fn license_text() -> &'static str {
    include_str!("../../../public/LICENSE.txt")
}

pub fn third_party_text() -> &'static str {
    include_str!("../../../public/THIRD_PARTY_NOTICES.txt")
}

/// What Save sends. Mirrors `handleSave` in SettingsPanel.tsx.
#[derive(Clone, Debug)]
pub struct SaveDraft {
    pub provider: String,
    pub model: String,
    pub base_url: String,
    pub loaded_provider: String,
    pub loaded_model: String,
    pub loaded_base_url: String,
    pub custom_name: String,
    pub api_key: String,
    pub project_path: String,
    pub default_workspace: String,
    pub browser_home_url: String,
    pub persona: String,
    pub user_name: String,
    pub name: String,
    pub agent_gender: String,
    pub ui_language: String,
    pub access_scope: String,
    pub launch_at_login: bool,
    pub start_in_tray: bool,
    pub harness_mode: String,
    pub harness_min: f64,
    pub harness_max: f64,
    pub thinking_level: String,
    pub tool_process: String,
    pub skills_budget: f64,
    pub web_tools: bool,
    pub http_bootstrap: bool,
    pub privacy_mode: bool,
    pub sleev_enabled: bool,
    pub sleev_url: String,
    pub sleev_allow_remote: bool,
    pub soul_field: bool,
    pub approval_mode: String,
    pub trust_profile: String,
    pub allow_skills: bool,
    pub auto_approve: f64,
    pub log_level: String,
    pub sarcasm: bool,
    pub claimidx_enabled: bool,
    pub claimidx: bool,
    pub advanced: Value,
    pub providers_touched: bool,
    pub enabled_providers: Vec<String>,
    pub catalog_ids: Vec<String>,
    pub enabled_models: Value,
    pub messengers_dirty: bool,
    pub messengers: Option<Value>,
    pub assistant: Value,
}

impl Default for SaveDraft {
    fn default() -> Self {
        SaveDraft {
            provider: "demo".into(),
            model: String::new(),
            base_url: String::new(),
            loaded_provider: "demo".into(),
            loaded_model: String::new(),
            loaded_base_url: String::new(),
            custom_name: String::new(),
            api_key: String::new(),
            project_path: String::new(),
            default_workspace: String::new(),
            browser_home_url: String::new(),
            persona: "balanced".into(),
            user_name: String::new(),
            name: "Remedy".into(),
            agent_gender: "female".into(),
            ui_language: "auto".into(),
            access_scope: "workspace".into(),
            launch_at_login: false,
            start_in_tray: false,
            harness_mode: "auto".into(),
            harness_min: 0.75,
            harness_max: 0.92,
            thinking_level: "medium".into(),
            tool_process: "off".into(),
            skills_budget: 80.0,
            web_tools: false,
            http_bootstrap: true,
            privacy_mode: false,
            sleev_enabled: false,
            sleev_url: String::new(),
            sleev_allow_remote: false,
            soul_field: true,
            approval_mode: "ask".into(),
            trust_profile: "balanced".into(),
            allow_skills: true,
            auto_approve: 0.8,
            log_level: "INFO".into(),
            sarcasm: false,
            claimidx_enabled: true,
            claimidx: true,
            advanced: json!({}),
            providers_touched: false,
            enabled_providers: Vec::new(),
            catalog_ids: Vec::new(),
            enabled_models: json!({}),
            messengers_dirty: false,
            messengers: None,
            assistant: json!({}),
        }
    }
}

pub fn project_forces_full(_path: &str) -> bool {
    // Kept so older call sites compile; an unset project no longer implies Full.
    false
}

/// Profile home (`USERPROFILE` on Windows, `HOME` elsewhere). Never a hardcoded name.
pub fn user_profile_dir() -> std::path::PathBuf {
    for key in ["USERPROFILE", "HOME"] {
        if let Ok(p) = std::env::var(key) {
            let t = p.trim();
            if !t.is_empty() {
                return std::path::PathBuf::from(t);
            }
        }
    }
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| std::path::PathBuf::from("."))
}

/// Display path for the default workspace. Empty config means `{profile}/Remedy`.
pub fn default_workspace_display(configured: &str) -> String {
    let t = configured.trim();
    if !t.is_empty() && t != "." {
        return t.to_string();
    }
    user_profile_dir()
        .join("Remedy")
        .display()
        .to_string()
}

pub fn normalize_access_scope(raw: &str) -> String {
    match raw.trim().to_ascii_lowercase().as_str() {
        "home" | "user" | "project+home" | "project_home" => "home".into(),
        "full" | "machine" | "all" | "unrestricted" => "full".into(),
        "untrusted" | "sandbox" | "strict" | "download" => "untrusted".into(),
        "workspace" | "default" | "default_workspace" | "owner_workspace" => "workspace".into(),
        _ => "project".into(),
    }
}

pub fn is_legacy_access_scope(raw: &str) -> bool {
    matches!(normalize_access_scope(raw).as_str(), "home" | "untrusted")
}

pub fn legacy_access_note(raw: &str) -> String {
    match normalize_access_scope(raw).as_str() {
        "home" => "Current scope is Home (legacy). Pick one of the options above to change it.".into(),
        "untrusted" => "Current scope is Untrusted (legacy). Pick one of the options above to change it.".into(),
        _ => String::new(),
    }
}

pub fn access_scope_choices() -> &'static [(&'static str, &'static str)] {
    &[
        ("project", "Project only"),
        ("workspace", "Default workspace"),
        ("full", "Full machine access"),
    ]
}

pub fn advanced_category_titles() -> &'static [&'static str] {
    &["Learning", "Logs", "Tone", "Ledger"]
}

pub fn save_body(d: &SaveDraft) -> Result<Value, String> {
    if sleev_remote_blocked(&d.sleev_url, d.sleev_allow_remote) {
        return Err("Sleev gateway is not on this computer. Turn on “Allow a remote gateway” or use a loopback address.".into());
    }
    let mut body = Map::new();
    let persisted_url = crate::setup::model::persist_base_url(&d.provider, &d.base_url);
    // Snap a leftover hidden-provider URL (e.g. OpenRouter still stored under
    // Anthropic) without injecting llm_* into an otherwise unrelated save
    // when no URL was loaded.
    let hidden_url_dirty = !crate::setup::model::shows_base_url(&d.provider)
        && !d.loaded_base_url.is_empty()
        && persisted_url != d.loaded_base_url;
    let llm_edited = d.provider != d.loaded_provider
        || d.model != d.loaded_model
        || d.base_url != d.loaded_base_url
        || hidden_url_dirty;
    if llm_edited {
        body.insert("llm_provider".into(), json!(d.provider));
        body.insert("llm_model".into(), json!(d.model));
        body.insert("llm_base_url".into(), json!(persisted_url));
    }
    if d.provider == CUSTOM_PROVIDER {
        body.insert("custom_llm_name".into(), json!(d.custom_name.trim()));
    }
    body.insert("project_path".into(), json!(d.project_path));
    body.insert("default_workspace".into(), json!(d.default_workspace.trim()));
    let home = {
        let t = d.browser_home_url.trim();
        if t.is_empty() { DEFAULT_BROWSER_HOME.to_string() } else { t.to_string() }
    };
    body.insert("browser_home_url".into(), json!(home));
    body.insert("persona".into(), json!(d.persona));
    body.insert("user_name".into(), json!(d.user_name.trim()));
    let name = {
        let t = d.name.trim();
        if t.is_empty() { "Remedy".to_string() } else { t.to_string() }
    };
    body.insert("name".into(), json!(name));
    let gender = if d.agent_gender.trim().is_empty() { "female" } else { d.agent_gender.trim() };
    body.insert("agent_gender".into(), json!(gender));
    let lang = if d.ui_language.trim().is_empty() { "auto" } else { d.ui_language.trim() };
    body.insert("ui_language".into(), json!(lang));
    body.insert("access_scope".into(), json!(normalize_access_scope(&d.access_scope)));
    body.insert("launch_at_login".into(), json!(d.launch_at_login));
    body.insert("start_in_tray".into(), json!(d.start_in_tray));
    body.insert("close_to_tray".into(), json!(true));
    body.insert("harness_mode".into(), json!(d.harness_mode));
    body.insert("harness_min_context_pct".into(), json!(d.harness_min));
    let max = d.harness_max.max(d.harness_min + 0.01);
    body.insert("harness_max_context_pct".into(), json!(max));
    body.insert("thinking_level".into(), json!(d.thinking_level));
    body.insert("tool_process".into(), json!(d.tool_process));
    body.insert("skills_active_budget".into(), json!(d.skills_budget));
    body.insert("web_tools_enabled".into(), json!(d.web_tools));
    body.insert("http_bootstrap".into(), json!(d.http_bootstrap));
    body.insert("privacy_mode".into(), json!(d.privacy_mode));
    body.insert("sleev_enabled".into(), json!(d.sleev_enabled));
    body.insert("sleev_gateway_url".into(), json!(d.sleev_url.trim()));
    body.insert("sleev_allow_remote_gateway".into(), json!(d.sleev_allow_remote));
    body.insert("soul_field_enabled".into(), json!(d.soul_field));
    body.insert("approval_mode".into(), json!(d.approval_mode));
    body.insert("trust_profile".into(), json!(d.trust_profile));
    body.insert("allow_skill_creation".into(), json!(d.allow_skills));
    body.insert("auto_approve_threshold".into(), json!(d.auto_approve));
    body.insert("log_level".into(), json!(d.log_level));
    body.insert("sarcasm_mode".into(), json!(d.sarcasm));
    body.insert("claimidx_enabled".into(), json!(d.claimidx_enabled));
    body.insert("claimidx_public_ledger".into(), json!(d.claimidx));
    body.insert("advanced".into(), if d.advanced.is_object() { d.advanced.clone() } else { json!({}) });
    if d.providers_touched {
        let ep = d.enabled_providers.clone();
        let covers = !ep.is_empty() && d.catalog_ids.iter().all(|id| ep.iter().any(|e| e == id));
        if covers {
            body.insert("enabled_providers".into(), Value::Null);
        } else {
            body.insert("enabled_providers".into(), json!(ep));
        }
    }
    let models = if d.enabled_models.is_object() { d.enabled_models.clone() } else { json!({}) };
    body.insert("enabled_models".into(), models);
    if !d.api_key.trim().is_empty() {
        body.insert("llm_api_key".into(), json!(d.api_key));
    }
    if d.messengers_dirty {
        if let Some(msg) = &d.messengers {
            body.insert("messengers".into(), msg.clone());
        }
    }
    if d.assistant.as_object().is_some_and(|o| !o.is_empty()) {
        body.insert("assistant".into(), d.assistant.clone());
    }
    Ok(Value::Object(body))
}

/// Quote a TOML string the simple reader in `fonts.rs` can unquote.
pub fn toml_quote(s: &str) -> String {
    let clean: String = s.chars().filter(|c| *c != '"' && *c != '\n' && *c != '\r').collect();
    format!("\"{clean}\"")
}

/// Replace keys inside `[table]`, or append the table. Other tables stay.
pub fn upsert_keys(text: &str, table: &str, updates: &[(&str, String)]) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut in_table = false;
    let mut seen = vec![false; updates.len()];
    let mut found = false;
    let flush_missing = |out: &mut Vec<String>, seen: &[bool]| {
        for (i, (k, v)) in updates.iter().enumerate() {
            if !seen[i] {
                out.push(format!("{k} = {v}"));
            }
        }
    };
    for line in text.lines() {
        let trim = line.trim();
        if trim.starts_with('[') && trim.ends_with(']') && !trim.starts_with("[=") {
            if in_table {
                flush_missing(&mut out, &seen);
                seen.fill(false);
            }
            let name = trim.trim_start_matches('[').trim_end_matches(']').trim();
            in_table = name == table;
            if in_table {
                found = true;
            }
            out.push(line.to_string());
            continue;
        }
        if in_table {
            if let Some((k, _)) = trim.split_once('=') {
                let key = k.trim();
                if let Some(i) = updates.iter().position(|(uk, _)| *uk == key) {
                    out.push(format!("{} = {}", updates[i].0, updates[i].1));
                    seen[i] = true;
                    continue;
                }
            }
        }
        out.push(line.to_string());
    }
    if in_table {
        flush_missing(&mut out, &seen);
    } else if !found {
        if out.last().is_some_and(|l| !l.is_empty()) {
            out.push(String::new());
        }
        out.push(format!("[{table}]"));
        for (k, v) in updates {
            out.push(format!("{k} = {v}"));
        }
    }
    let mut s = out.join("\n");
    if !s.ends_with('\n') {
        s.push('\n');
    }
    s
}

pub fn table_string(text: &str, table: &str, key: &str) -> Option<String> {
    let mut in_table = false;
    for line in text.lines() {
        let trim = line.trim();
        if trim.starts_with('[') && trim.ends_with(']') {
            let name = trim.trim_start_matches('[').trim_end_matches(']').trim();
            in_table = name == table;
            continue;
        }
        if !in_table {
            continue;
        }
        let Some((k, v)) = trim.split_once('=') else { continue };
        if k.trim() == key {
            return Some(unquote(v.trim()));
        }
    }
    None
}

pub fn unquote(v: &str) -> String {
    let v = v.trim();
    v.strip_prefix('"')
        .and_then(|s| s.strip_suffix('"'))
        .or_else(|| v.strip_prefix('\'').and_then(|s| s.strip_suffix('\'')))
        .unwrap_or(v)
        .trim()
        .to_string()
}

pub fn table_bool(text: &str, table: &str, key: &str) -> Option<bool> {
    match table_string(text, table, key).as_deref() {
        Some("true") => Some(true),
        Some("false") => Some(false),
        _ => None,
    }
}

/// Engine knobs rendered from data. Bools use a switch; the rest are numbers
/// or free text applied with the row's Apply control.
#[derive(Clone, Copy, Debug)]
pub struct Knob {
    pub key: &'static str,
    pub label: &'static str,
    pub kind: &'static str,
    pub min: f64,
    pub max: f64,
    pub step: f64,
}

pub const RMB_KNOBS: &[Knob] = &[
    Knob { key: "enable_mtp", label: "MTP speculative", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "n_cpu_moe", label: "MoE experts on CPU (0 = auto, −1 = all on GPU)", kind: "int", min: -1.0, max: 256.0, step: 1.0 },
    Knob { key: "reasoning_budget", label: "Reasoning budget (−1 = no cap)", kind: "int", min: -1.0, max: 100_000.0, step: 1.0 },
    Knob { key: "spec_draft_n_max", label: "MTP draft tokens (0 = auto)", kind: "int", min: 0.0, max: 8.0, step: 1.0 },
    Knob { key: "n_gpu_layers_draft", label: "Draft GPU layers (0 = auto)", kind: "int", min: 0.0, max: 99.0, step: 1.0 },
    Knob { key: "cache_reuse", label: "Prefix cache reuse (0 = off)", kind: "int", min: 0.0, max: 1_000_000.0, step: 16.0 },
    Knob { key: "threads", label: "Threads (0 = auto)", kind: "int", min: 0.0, max: 256.0, step: 1.0 },
    Knob { key: "parallel", label: "Parallel slots", kind: "int", min: 1.0, max: 64.0, step: 1.0 },
    Knob { key: "batch_size", label: "Batch size (0 = auto)", kind: "int", min: 0.0, max: 8192.0, step: 1.0 },
    Knob { key: "ubatch_size", label: "Ubatch size (0 = auto)", kind: "int", min: 0.0, max: 8192.0, step: 1.0 },
    Knob { key: "temperature", label: "Temperature", kind: "float", min: 0.0, max: 2.0, step: 0.05 },
    Knob { key: "top_p", label: "Top-P", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "top_k", label: "Top-K (0 = off)", kind: "int", min: 0.0, max: 1000.0, step: 1.0 },
    Knob { key: "min_p", label: "Min-P", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "repeat_penalty", label: "Repeat penalty", kind: "float", min: 0.0, max: 2.0, step: 0.05 },
    Knob { key: "repeat_last_n", label: "Repeat last N (0 = off)", kind: "int", min: 0.0, max: 4096.0, step: 1.0 },
    Knob { key: "seed", label: "Seed (−1 = random)", kind: "int", min: -1.0, max: 1_000_000_000.0, step: 1.0 },
    Knob { key: "rope_freq_scale", label: "RoPE freq scale (0 = off)", kind: "float", min: 0.0, max: 4.0, step: 0.05 },
    Knob { key: "rope_freq_base", label: "RoPE freq base (0 = off)", kind: "float", min: 0.0, max: 1_000_000.0, step: 1.0 },
    Knob { key: "typical_p", label: "Typical-P (0 = off)", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "tfs_z", label: "TFS-Z (0 = off)", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "mirostat", label: "Mirostat (0/1/2)", kind: "int", min: 0.0, max: 2.0, step: 1.0 },
    Knob { key: "mirostat_tau", label: "Mirostat tau", kind: "float", min: 0.0, max: 10.0, step: 0.05 },
    Knob { key: "mirostat_eta", label: "Mirostat eta", kind: "float", min: 0.0, max: 1.0, step: 0.01 },
    Knob { key: "presence_penalty", label: "Presence penalty", kind: "float", min: -2.0, max: 2.0, step: 0.05 },
    Knob { key: "frequency_penalty", label: "Frequency penalty", kind: "float", min: -2.0, max: 2.0, step: 0.05 },
    Knob { key: "main_gpu", label: "Main GPU (0 = first)", kind: "int", min: 0.0, max: 16.0, step: 1.0 },
    Knob { key: "threads_batch", label: "Threads batch (0 = auto)", kind: "int", min: 0.0, max: 256.0, step: 1.0 },
    Knob { key: "dry_multiplier", label: "Dry multiplier (0 = off)", kind: "float", min: 0.0, max: 10.0, step: 0.05 },
    Knob { key: "dry_base", label: "Dry base", kind: "float", min: 0.0, max: 10.0, step: 0.05 },
    Knob { key: "dry_allowed_length", label: "Dry allowed length", kind: "int", min: 0.0, max: 100.0, step: 1.0 },
    Knob { key: "dry_penalty_last_n", label: "Dry penalty last N (−1 = all)", kind: "int", min: -1.0, max: 4096.0, step: 1.0 },
    Knob { key: "xtc_probability", label: "XTC probability (0 = off)", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "xtc_threshold", label: "XTC threshold", kind: "float", min: 0.0, max: 1.0, step: 0.05 },
    Knob { key: "flash_attn", label: "Flash attention", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "use_jinja", label: "Jinja chat template", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "mlock", label: "mlock", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "no_mmap", label: "no-mmap", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "no_kv_offload", label: "no-kv-offload", kind: "bool", min: 0.0, max: 1.0, step: 1.0 },
    Knob { key: "yarn_orig_ctx", label: "YaRN orig ctx", kind: "int", min: 0.0, max: 1_000_000.0, step: 1.0 },
    Knob { key: "yarn_factor", label: "YaRN factor", kind: "float", min: 0.0, max: 128.0, step: 0.1 },
    Knob { key: "yarn_beta_fast", label: "YaRN beta fast", kind: "float", min: 0.0, max: 128.0, step: 0.1 },
    Knob { key: "yarn_beta_slow", label: "YaRN beta slow", kind: "float", min: 0.0, max: 128.0, step: 0.1 },
    Knob { key: "samplers", label: "Sampler order (--samplers)", kind: "text", min: 0.0, max: 0.0, step: 0.0 },
    Knob { key: "rope_scaling", label: "RoPE scaling", kind: "text", min: 0.0, max: 0.0, step: 0.0 },
    Knob { key: "tensor_split", label: "Tensor split", kind: "text", min: 0.0, max: 0.0, step: 0.0 },
    Knob { key: "mmproj", label: "MMProj path", kind: "text", min: 0.0, max: 0.0, step: 0.0 },
    Knob { key: "chat_template", label: "Chat template", kind: "text", min: 0.0, max: 0.0, step: 0.0 },
];


pub fn clamp_knob(knob: &Knob, n: f64) -> f64 {
    if knob.kind == "text" || knob.kind == "bool" {
        return n;
    }
    let n = n.clamp(knob.min, knob.max);
    if knob.kind == "int" { n.round() } else { n }
}

fn field_str(v: &Value, key: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("").to_string()
}

fn models_array(v: &Value) -> Option<&Vec<Value>> {
    v.get("models").and_then(|m| m.as_array()).filter(|rows| !rows.is_empty())
}

/// Catalog models from `GET /rmb/catalog`, falling back to the status payload.
pub fn rmb_catalog_models(catalog: &Value, status: &Value) -> Vec<Value> {
    if let Some(rows) = models_array(catalog) {
        return rows.clone();
    }
    status
        .get("catalog")
        .and_then(models_array)
        .cloned()
        .unwrap_or_default()
}

pub fn rmb_catalog_label(model: &Value) -> String {
    let name = field_str(model, "name");
    let id = field_str(model, "id");
    let label = if name.is_empty() { id } else { name };
    match model.get("approx_gb") {
        Some(gb) if !gb.is_null() => format!("{label} (~{gb} GB)"),
        _ => label,
    }
}

pub fn rmb_catalog_patch(model_id: &str) -> Value {
    json!({
        "model_id": model_id,
        "model_path": "",
        "enabled": true,
        "use_as_chat_provider": true,
    })
}

pub fn hf_repo_label(row: &Value) -> String {
    let id = field_str(row, "id");
    let n = row.get("downloads").and_then(|x| x.as_f64()).unwrap_or(0.0);
    if n <= 0.0 {
        return id;
    }
    let suffix = if n >= 1_000_000.0 {
        format!("{:.1}M dl", n / 1_000_000.0)
    } else if n >= 1_000.0 {
        format!("{:.1}k dl", n / 1_000.0)
    } else {
        format!("{n:.0} dl")
    };
    format!("{id} · {suffix}")
}

pub fn hf_file_path(row: &Value) -> String {
    let path = field_str(row, "path");
    if path.is_empty() { field_str(row, "name") } else { path }
}

pub fn hf_file_label(row: &Value) -> String {
    let name = {
        let n = field_str(row, "name");
        if n.is_empty() { hf_file_path(row) } else { n }
    };
    let star = if row.get("recommended").and_then(|x| x.as_bool()).unwrap_or(false) {
        "★ "
    } else {
        ""
    };
    let size = match row.get("size_gb") {
        Some(gb) if !gb.is_null() => format!(" ({gb} GB)"),
        _ => String::new(),
    };
    let role = if field_str(row, "role") == "mmproj" { " · mmproj" } else { "" };
    format!("{star}{name}{size}{role}")
}

pub fn recommended_hf_file(files: &Value) -> Option<Value> {
    let rows = files.as_array()?;
    rows.iter()
        .find(|f| f.get("recommended").and_then(|x| x.as_bool()) == Some(true))
        .cloned()
        .or_else(|| rows.first().cloned())
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_is_and_of_tokens_and_includes_display_titles() {
        let theme = section("theme").unwrap();
        assert!(section_matches(theme, "presence"));
        assert!(section_matches(theme, "face"));
        let rmb = section("rmb").unwrap();
        assert!(section_matches(rmb, "local models"));
        assert!(section_matches(rmb, "gguf"));
        assert!(!section_matches(rmb, "local calendar"));
        assert!(section_matches(rmb, ""));
        let perms = section("security-power").unwrap();
        assert!(section_matches(perms, "permissions"));
        assert!(section_matches(perms, "security"));
    }

    #[test]
    fn simple_hides_advanced_until_search() {
        let vision = section("vision").unwrap();
        assert!(!section_visible(vision, "models", "simple", ""));
        assert!(section_visible(vision, "models", "advanced", ""));
        assert!(section_visible(vision, "general", "simple", "smolvlm"));
        let you = section("you-agent").unwrap();
        assert!(section_visible(you, "general", "simple", ""));
        assert!(!section_visible(you, "models", "advanced", ""));
        let license = section("license").unwrap();
        assert!(section_visible(license, "system", "simple", ""));
        assert!(section_visible(license, "system", "advanced", ""));
    }

    #[test]
    fn theme_menu_matches_todays_order() {
        let names: Vec<String> = theme_menu().into_iter().map(|t| t.name).collect();
        assert_eq!(
            names,
            vec![
                "System",
                "Light",
                "Amber",
                "Amethyst",
                "Deutan",
                "Emerald",
                "Ocean",
                "Protan",
                "Tritan",
                "Dark",
                "Neutral Dark",
                "Dark Forest",
                "Dark Purple",
            ]
        );
    }

    #[test]
    fn save_omits_untouched_llm_blank_key_and_forces_tray() {
        let mut d = SaveDraft::default();
        d.api_key = "  ".into();
        d.project_path = "C:/work".into();
        d.access_scope = "project".into();
        d.harness_min = 0.80;
        d.harness_max = 0.70;
        let body = save_body(&d).unwrap();
        assert!(body.get("llm_provider").is_none());
        assert!(body.get("llm_api_key").is_none());
        assert_eq!(body["close_to_tray"], json!(true));
        assert_eq!(body["access_scope"], json!("project"));
        assert_eq!(body["harness_max_context_pct"], json!(0.81));
        assert_eq!(body["browser_home_url"], json!(DEFAULT_BROWSER_HOME));
        assert_eq!(body["enabled_models"], json!({}));
        assert!(body.get("enabled_providers").is_none());
        assert!(body.get("custom_llm_name").is_none());
    }

    #[test]
    fn save_sends_llm_when_edited_and_custom_name_only_for_custom() {
        let mut d = SaveDraft::default();
        d.provider = "custom".into();
        d.custom_name = " Home ".into();
        d.model = "llama".into();
        d.api_key = "sk-test".into();
        d.providers_touched = true;
        d.enabled_providers = vec!["xai".into()];
        // Ollama and Demo stay off, so the save is a list rather than null (null means all catalog ids).
        d.catalog_ids = vec!["demo".into(), "xai".into(), "ollama".into()];
        d.messengers_dirty = true;
        d.messengers = Some(json!({"telegram": {"enabled": true}}));
        d.assistant = json!({"enabled": true});
        let body = save_body(&d).unwrap();
        assert_eq!(body["llm_provider"], json!("custom"));
        assert_eq!(body["custom_llm_name"], json!("Home"));
        assert_eq!(body["llm_api_key"], json!("sk-test"));
        assert_eq!(body["enabled_providers"], json!(["xai"]));
        assert!(body.get("messengers").is_some());
        assert_eq!(body["assistant"]["enabled"], json!(true));
        d.catalog_ids = vec!["demo".into(), "xai".into()];
        d.enabled_providers = vec!["demo".into(), "xai".into()];
        let body = save_body(&d).unwrap();
        assert!(body["enabled_providers"].is_null());
    }

    #[test]
    fn save_allows_turning_demo_off() {
        let mut d = SaveDraft::default();
        d.providers_touched = true;
        d.enabled_providers = vec!["anthropic".into()];
        d.catalog_ids = vec!["demo".into(), "anthropic".into(), "xai".into()];
        let body = save_body(&d).unwrap();
        assert_eq!(body["enabled_providers"], json!(["anthropic"]));
        assert!(!body["enabled_providers"].as_array().unwrap().iter().any(|v| v == "demo"));
    }

    #[test]
    fn save_snaps_hidden_provider_url_even_when_form_still_has_a_leftover() {
        let mut d = SaveDraft::default();
        d.provider = "anthropic".into();
        d.loaded_provider = "anthropic".into();
        d.model = "claude-haiku-4-5-20251001".into();
        d.loaded_model = "claude-haiku-4-5-20251001".into();
        d.base_url = "https://openrouter.ai/api/v1".into();
        d.loaded_base_url = "https://openrouter.ai/api/v1".into();
        let body = save_body(&d).unwrap();
        assert_eq!(body["llm_provider"], json!("anthropic"));
        assert_eq!(body["llm_model"], json!("claude-haiku-4-5-20251001"));
        assert_eq!(body["llm_base_url"], json!("https://api.anthropic.com/v1"));
    }

    #[test]
    fn empty_project_keeps_chosen_scope_and_blocks_remote_sleev() {
        let mut d = SaveDraft::default();
        d.project_path = " . ".into();
        d.access_scope = "project".into();
        let body = save_body(&d).unwrap();
        assert_eq!(body["access_scope"], json!("project"));
        assert_eq!(body["default_workspace"], json!(""));
        d.access_scope = "workspace".into();
        d.default_workspace = "C:/Users/Example/Remedy".into();
        let body = save_body(&d).unwrap();
        assert_eq!(body["access_scope"], json!("workspace"));
        assert_eq!(body["default_workspace"], json!("C:/Users/Example/Remedy"));
        d.access_scope = "full".into();
        assert_eq!(save_body(&d).unwrap()["access_scope"], json!("full"));
        d.sleev_url = "http://192.168.1.9:17321".into();
        d.sleev_allow_remote = false;
        assert!(save_body(&d).is_err());
        d.sleev_allow_remote = true;
        assert!(save_body(&d).is_ok());
        assert!(sleev_loopback(&sleev_host("sleev.local:17321")) == false);
        assert!(sleev_loopback(&sleev_host("http://user:pw@[::1]:17321/x")));
        assert!(!sleev_remote_blocked("", false));
    }

    #[test]
    fn token_bounds_and_generation_scope() {
        assert!(token_error("thinking_budget_tokens", "").is_none());
        assert!(token_error("thinking_budget_tokens", "1_024").is_none());
        assert!(token_error("max_output_tokens", "10").is_some());
        assert!(token_error("context_window_tokens", "4096").is_none());
        let all = generation_patch("all", "xai", "grok", "HIGH", "", "256", "");
        assert_eq!(all["reasoning_effort"], json!("high"));
        assert_eq!(all["thinking_budget_tokens"], json!(0));
        assert_eq!(all["max_output_tokens"], json!(256));
        let one = generation_patch("model", "xai", "grok", "", "", "", "");
        assert!(one.get("model_settings").unwrap().get("xai/grok").is_some());
        assert_eq!(generation_patch("model", "", "", "", "", "", ""), json!({}));
        assert_eq!(model_key("xai", "grok"), "xai/grok");
    }

    #[test]
    fn connect_addresses_and_panes() {
        let addrs = filter_connect_addresses(&[
            "*".into(),
            "0.0.0.0".into(),
            "10.0.0.8/24".into(),
            "127.0.0.1:7400".into(),
            "not-an-ip".into(),
            "10.0.0.8".into(),
            "192.168.0.2".into(),
        ]);
        assert_eq!(addrs, vec!["10.0.0.8".to_string(), "192.168.0.2".to_string(), "127.0.0.1".to_string()]);
        assert_eq!(preferred_connect_bind(&addrs, ""), "10.0.0.8");
        assert_eq!(preferred_connect_bind(&addrs, "127.0.0.1"), "127.0.0.1");
        let panes = merge_connect_panes(&json!({"approvals": false, "settings_write": true}));
        assert_eq!(panes["approvals"], json!(true));
        assert_eq!(panes["settings_write"], json!(true));
        assert_eq!(panes["live_ui"], json!(true));
        assert_eq!(panes["computer_preview"], json!(false));
    }

    #[test]
    fn messenger_drafts_skip_blank_secrets() {
        let list = json!([{
            "id": "telegram",
            "enabled": false,
            "fields": {"chat": ["a", "b"], "token": null, "quiet": true},
            "field_schema": [
                {"key": "token", "kind": "secret"},
                {"key": "chat", "kind": "list"}
            ]
        }]);
        let drafts = drafts_from_messengers(&list);
        assert_eq!(drafts["telegram"]["chat"], json!("a, b"));
        assert_eq!(drafts["telegram"]["token"], json!(""));
        assert_eq!(drafts["telegram"]["quiet"], json!(true));
        let mut edited = drafts.clone();
        edited["telegram"]["enabled"] = json!(true);
        edited["telegram"]["token"] = json!("  ");
        edited["telegram"]["chat"] = json!("a, c");
        let body = messengers_update(&list, &edited).unwrap();
        assert_eq!(body["telegram"]["enabled"], json!(true));
        assert!(body["telegram"].get("token").is_none());
        assert_eq!(body["telegram"]["chat"], json!("a, c"));
        assert!(messengers_update(&json!([]), &json!({})).is_none());
        assert_eq!(messenger_badge("google_chat"), "GChat");
    }

    #[test]
    fn font_roles_and_toml_round_trip() {
        assert_eq!(faces_for_role("code"), vec!["ahmi-print", "cascadia-code", "jetbrains-mono", "fira-code", "system-mono", "ahmi-pixel"]);
        assert_eq!(family_for_face("remedy-sans-ui"), "Remedy Sans UI");
        assert_eq!(face_for_family("AhmiPrint"), "ahmi-print");
        assert_eq!(faces_for_role("ui").first().copied(), Some("remedy-sans-ui"));
        let src = "[advanced]\nsomething = true\n\n[fonts]\nui = \"Inter\"\ncode = \"Cascadia Code\"\n";
        let next = upsert_keys(src, "fonts", &[("ui", toml_quote("Remedy Sans UI")), ("heading", toml_quote("Remedy Sans"))]);
        assert!(next.contains("something = true"));
        assert!(next.contains("code = \"Cascadia Code\""));
        assert!(next.contains("ui = \"Remedy Sans UI\""));
        assert!(next.contains("heading = \"Remedy Sans\""));
        assert_eq!(table_string(&next, "fonts", "ui").as_deref(), Some("Remedy Sans UI"));
        let appearance = upsert_keys("", "appearance", &[("theme", toml_quote("forest")), ("reduce_motion", "true".into()), ("show_presence", "true".into())]);
        assert_eq!(table_string(&appearance, "appearance", "theme").as_deref(), Some("forest"));
        assert_eq!(table_bool(&appearance, "appearance", "reduce_motion"), Some(true));
        assert_eq!(table_bool(&appearance, "appearance", "show_presence"), Some(true));
        let hidden = upsert_keys(&appearance, "appearance", &[("show_presence", "false".into())]);
        assert_eq!(table_bool(&hidden, "appearance", "show_presence"), Some(false));
        assert!(appearance.contains("[fonts]") == false);
    }

    #[test]
    fn harness_coupling_and_font_steps() {
        assert_eq!(couple_harness_min(0.95, 0.92), Some(0.97));
        assert_eq!(couple_harness_max(0.75, 0.70), Some(0.68));
        assert_eq!(couple_harness_min(0.75, 0.92), None);
        assert_eq!(step_font_scale("md", 1), "lg");
        assert_eq!(step_font_scale("sm", -1), "sm");
        assert_eq!(step_font_scale("xl", 1), "xl");
        assert!((font_scale_factor("lg") - 1.15).abs() < 0.001);
    }

    #[test]
    fn every_section_has_one_category_and_rmb_knobs_cover_the_engine() {
        for sec in SECTIONS {
            assert!(CATEGORIES.iter().any(|(id, _, _)| *id == sec.category), "{}", sec.id);
        }
        for key in ["enable_mtp", "n_cpu_moe", "flash_attn", "use_jinja", "samplers", "rope_scaling", "tensor_split", "mmproj", "chat_template", "yarn_factor", "no_kv_offload"] {
            assert!(RMB_KNOBS.iter().any(|k| k.key == key), "{key}");
        }
    }

    #[test]
    fn rmb_catalog_and_hf_files_match_the_old_shell() {
        let catalog = json!({
            "default_model_id": "qwen35-9b",
            "models": [
                {"id": "qwen35-9b", "name": "Qwen3.5 9B (Q6_K)", "approx_gb": 8.5},
                {"id": "qwen25-coder-7b", "name": "Qwen2.5 Coder 7B (Q4_K_M)", "approx_gb": 5}
            ]
        });
        let status = json!({"catalog": {"models": [{"id": "fallback", "name": "From status"}]}});
        let models = rmb_catalog_models(&catalog, &status);
        assert_eq!(models.len(), 2);
        assert_eq!(rmb_catalog_label(&models[0]), "Qwen3.5 9B (Q6_K) (~8.5 GB)");
        assert_eq!(rmb_catalog_label(&models[1]), "Qwen2.5 Coder 7B (Q4_K_M) (~5 GB)");
        assert_eq!(
            rmb_catalog_models(&json!({}), &status)[0]["id"],
            "fallback"
        );
        let patch = rmb_catalog_patch("qwen35-9b");
        assert_eq!(patch["model_id"], "qwen35-9b");
        assert_eq!(patch["model_path"], "");
        assert_eq!(patch["enabled"], true);
        assert_eq!(patch["use_as_chat_provider"], true);

        assert_eq!(hf_repo_label(&json!({"id": "Qwen/Qwen"})), "Qwen/Qwen");
        assert_eq!(hf_repo_label(&json!({"id": "Qwen/Qwen", "downloads": 2500})), "Qwen/Qwen · 2.5k dl");
        assert_eq!(hf_repo_label(&json!({"id": "big", "downloads": 2_000_000})), "big · 2.0M dl");

        let files = json!([
            {"path": "a.gguf", "name": "a.gguf", "size_gb": 4.2},
            {"path": "b.gguf", "name": "b.gguf", "recommended": true, "size_gb": 5, "role": "weights"},
            {"path": "mm.gguf", "name": "mm.gguf", "role": "mmproj"}
        ]);
        let rec = recommended_hf_file(&files).unwrap();
        assert_eq!(hf_file_path(&rec), "b.gguf");
        assert_eq!(hf_file_label(&rec), "★ b.gguf (5 GB)");
        assert_eq!(hf_file_label(&files[2]), "mm.gguf · mmproj");
        assert_eq!(hf_file_path(&json!({"name": "only-name.gguf"})), "only-name.gguf");
        assert!(recommended_hf_file(&json!([])).is_none());
        assert_eq!(
            hf_file_path(&recommended_hf_file(&json!([{"path": "first.gguf"}])).unwrap()),
            "first.gguf"
        );
    }
    #[test]
    fn default_workspace_display_uses_profile_not_a_hardcoded_name() {
        let profile = user_profile_dir();
        let shown = default_workspace_display("");
        assert_eq!(shown, profile.join("Remedy").display().to_string());
        assert!(!shown.to_lowercase().contains("\\joe\\") || profile.display().to_string().to_lowercase().contains("joe"));
        assert_eq!(default_workspace_display(" /data/ws "), "/data/ws");
        assert_eq!(normalize_access_scope("default_workspace"), "workspace");
        assert_eq!(normalize_access_scope("FULL"), "full");
        assert!(is_legacy_access_scope("home"));
        assert!(!is_legacy_access_scope("workspace"));
        assert_eq!(access_scope_choices().len(), 3);
        assert_eq!(advanced_category_titles(), &["Learning", "Logs", "Tone", "Ledger"]);
        // Never force Full just because the project path is empty.
        assert!(!project_forces_full(""));
        assert!(!project_forces_full("."));
    }

}
