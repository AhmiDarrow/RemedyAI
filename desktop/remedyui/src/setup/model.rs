//! Pure setup and update decisions. No widgets and no network, so tests can
//! lock the Tauri flow without opening a window.

use serde_json::Value;

/// First-run wizard, the house-rules conversation, or the mind download.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    /// Vision / mind install before the conversation.
    Mind,
    /// Skippable house-rules conversation.
    Conversation,
    /// The seven-step wizard.
    Traditional,
}

/// `mode == "traditional"` wins on a first open. A revisit ignores that and
/// follows the mind: ready → conversation, failure or fallback → traditional,
/// otherwise the mind screen.
pub fn stage_for(mode: &str, mind_ready: bool, failed_or_fallback: bool, revisit: bool) -> Stage {
    if !revisit && mode == "traditional" {
        return Stage::Traditional;
    }
    if mind_ready {
        return Stage::Conversation;
    }
    if failed_or_fallback {
        return Stage::Traditional;
    }
    Stage::Mind
}

/// Settings that decide whether the wizard opens. A failed load is `None`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SettingsFlags {
    /// The settings document loaded.
    pub loaded: bool,
    /// Server asked for setup.
    pub needs_setup: Option<bool>,
    /// Missing is not the same as false.
    pub setup_completed: Option<bool>,
    /// `Some(false)` means there is no config file yet.
    pub config_exists: Option<bool>,
}

/// Wizard only when settings loaded and one of the three flags says so.
pub fn needs_wizard(settings: Option<&SettingsFlags>) -> bool {
    let Some(s) = settings else {
        return false;
    };
    if !s.loaded {
        return false;
    }
    s.needs_setup == Some(true)
        || s.setup_completed == Some(false)
        || s.config_exists == Some(false)
}

/// Name prompt: not during setup, no update offer, no revisit, empty name, not skipped.
pub fn ask_user_name(
    in_wizard: bool,
    offer: bool,
    revisit: bool,
    user_name: &str,
    skipped: bool,
) -> bool {
    !in_wizard && !offer && !revisit && user_name.trim().is_empty() && !skipped
}

/// True when Save must stay disabled.
pub fn reject_empty_name(name: &str) -> bool {
    name.trim().is_empty()
}

pub const STEP_WELCOME: usize = 0;
pub const STEP_PROVIDER: usize = 1;
pub const STEP_FOLDER: usize = 2;
pub const STEP_STYLE: usize = 3;
pub const STEP_CHAT: usize = 4;
pub const STEP_VISION: usize = 5;
pub const STEP_READY: usize = 6;

/// Chip labels, in wizard order.
pub const STEP_LABELS: [&str; 7] = [
    "Welcome",
    "Provider",
    "Folder",
    "Style",
    "Chat apps",
    "On this PC",
    "Ready",
];

/// Steps the recommended path skips (full provider/style extras stay on More options).
pub fn skip_on_short(step: usize) -> bool {
    matches!(step, STEP_PROVIDER | STEP_CHAT | STEP_VISION)
}

pub fn next_step(step: usize, short: bool) -> Option<usize> {
    let mut s = step + 1;
    while s < STEP_LABELS.len() {
        if short && skip_on_short(s) {
            s += 1;
            continue;
        }
        return Some(s);
    }
    None
}

pub fn prev_step(step: usize, short: bool) -> Option<usize> {
    let mut s = step;
    while s > 0 {
        s -= 1;
        if short && skip_on_short(s) {
            continue;
        }
        return Some(s);
    }
    None
}

pub fn progress_fraction(step: usize, short: bool) -> f32 {
    let steps: Vec<usize> = (0..STEP_LABELS.len())
        .filter(|s| !(short && skip_on_short(*s)))
        .collect();
    let i = steps.iter().position(|s| *s == step).unwrap_or(0);
    let denom = (steps.len().saturating_sub(1)).max(1) as f32;
    i as f32 / denom
}

pub const KEY_ERROR: &str = "Enter an API key, or choose Demo (no signup) / Ollama for free use.";
pub const XAI_KEY_ERROR: &str =
    "Sign in with xAI or enter an API key. Or pick Demo / Ollama for free use.";

pub fn key_error(provider: &str) -> &'static str {
    if provider == "xai" {
        XAI_KEY_ERROR
    } else {
        KEY_ERROR
    }
}

/// `custom` or an id that starts with `custom-`.
pub fn is_custom_like(provider: &str) -> bool {
    provider == "custom" || provider.starts_with("custom-")
}

/// Prefix match, case-insensitive. `127.0.0.1.evil` matches, same as the Tauri check.
pub fn is_loopback_prefix(url: &str) -> bool {
    let u = url.trim().to_ascii_lowercase();
    let rest = u
        .strip_prefix("https://")
        .or_else(|| u.strip_prefix("http://"))
        .unwrap_or(u.as_str());
    rest.starts_with("127.0.0.1") || rest.starts_with("localhost") || rest.starts_with("[::1]")
}

/// True when Continue must stop on the provider step.
pub fn provider_blocks_next(
    provider: &str,
    api_key: &str,
    base_url: &str,
    xai_connected: bool,
) -> bool {
    if !api_key.trim().is_empty() {
        return false;
    }
    if provider == "ollama" || provider == "demo" {
        return false;
    }
    if is_custom_like(provider) {
        return false;
    }
    if is_loopback_prefix(base_url) {
        return false;
    }
    if provider == "xai" && xai_connected {
        return false;
    }
    true
}

pub fn shows_base_url(provider: &str) -> bool {
    matches!(provider, "ollama" | "llamacpp" | "rmb" | "custom") || is_custom_like(provider)
}

/// URL Settings should persist for this provider. Hidden-URL providers
/// always get their catalog default so a leftover `llm_base_url` (OpenRouter
/// after a switch to Anthropic, for example) cannot hijack later turns.
pub fn persist_base_url(provider: &str, form_or_saved: &str) -> String {
    if shows_base_url(provider) {
        return form_or_saved.trim().to_string();
    }
    let cat = provider_base(provider);
    if cat.is_empty() {
        form_or_saved.trim().to_string()
    } else {
        cat.to_string()
    }
}

/// Balanced is stored as `default`, matching the wizard draft.
pub fn persona_wire(persona: &str) -> &str {
    if persona == "balanced" {
        "default"
    } else {
        persona
    }
}

#[derive(Clone, Debug)]
pub struct ProviderOpt {
    pub id: &'static str,
    pub name: &'static str,
    pub base_url: &'static str,
}

/// Offline catalog. No `advanced` flag, so the paid list is everything except Demo and Ollama.
pub const OFFLINE_PROVIDERS: &[ProviderOpt] = &[
    ProviderOpt {
        id: "demo",
        name: "Demo (Free)",
        base_url: "https://api.llm7.io/v1",
    },
    ProviderOpt {
        id: "openai",
        name: "OpenAI",
        base_url: "https://api.openai.com/v1",
    },
    ProviderOpt {
        id: "anthropic",
        name: "Anthropic",
        base_url: "https://api.anthropic.com/v1",
    },
    ProviderOpt {
        id: "google",
        name: "Google AI (Gemini)",
        base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    ProviderOpt {
        id: "deepseek",
        name: "DeepSeek",
        base_url: "https://api.deepseek.com/v1",
    },
    ProviderOpt {
        id: "xai",
        name: "xAI (Grok)",
        base_url: "https://api.x.ai/v1",
    },
    ProviderOpt {
        id: "groq",
        name: "Groq",
        base_url: "https://api.groq.com/openai/v1",
    },
    ProviderOpt {
        id: "mistral",
        name: "Mistral",
        base_url: "https://api.mistral.ai/v1",
    },
    ProviderOpt {
        id: "openrouter",
        name: "OpenRouter",
        base_url: "https://openrouter.ai/api/v1",
    },
    ProviderOpt {
        id: "poe",
        name: "Poe",
        base_url: "https://api.poe.com/v1",
    },
    ProviderOpt {
        id: "ollama",
        name: "Ollama (local)",
        base_url: "http://127.0.0.1:11434/v1",
    },
    ProviderOpt {
        id: "llamacpp",
        name: "llama.cpp (local)",
        base_url: "http://127.0.0.1:8080/v1",
    },
    ProviderOpt {
        id: "rmb",
        name: "RMB (local agent)",
        base_url: "http://127.0.0.1:8787/v1",
    },
    ProviderOpt {
        id: "custom",
        name: "Custom / OpenAI-compatible",
        base_url: "http://127.0.0.1:5001/v1",
    },
];

pub fn paid_providers() -> Vec<&'static ProviderOpt> {
    paid_providers_except(&[])
}

pub fn paid_providers_except(free_ids: &[String]) -> Vec<&'static ProviderOpt> {
    OFFLINE_PROVIDERS
        .iter()
        .filter(|p| p.id != "demo" && p.id != "ollama" && !free_ids.iter().any(|id| id == p.id))
        .collect()
}

/// A row from `GET /api/providers/free`, or the bundled fallback.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FreeProviderOpt {
    pub id: String,
    pub tier: String,
    pub title: String,
    pub name: String,
    pub blurb: String,
}

/// Bundled free-key rows used when the sidecar is offline.
pub fn offline_free_options() -> Vec<FreeProviderOpt> {
    [
        ("google", "free_key", "Google Gemini", "Google AI (Gemini)", "Free AI Studio key — strong free multimodal models."),
        ("groq", "free_key", "Groq", "Groq", "Free key — very fast open models."),
        ("openrouter", "free_key", "OpenRouter free models", "OpenRouter", "Free key — pick models with :free suffix."),
        ("mistral", "free_key", "Mistral", "Mistral", "Free Experiment plan key."),
    ]
    .into_iter()
    .map(|(id, tier, title, name, blurb)| FreeProviderOpt {
        id: id.into(),
        tier: tier.into(),
        title: title.into(),
        name: name.into(),
        blurb: blurb.into(),
    })
    .collect()
}

pub fn parse_free_providers(v: &Value) -> Vec<FreeProviderOpt> {
    let Some(arr) = v.get("options").and_then(|o| o.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|row| {
            let id = row.get("id")?.as_str()?.trim();
            if id.is_empty() {
                return None;
            }
            let name = row
                .get("name")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .trim();
            let title = row
                .get("title")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .trim();
            let label = if !title.is_empty() {
                title
            } else if !name.is_empty() {
                name
            } else {
                id
            };
            let tier = row
                .get("tier")
                .and_then(|s| s.as_str())
                .or_else(|| row.get("free_tier").and_then(|s| s.as_str()))
                .unwrap_or("")
                .to_string();
            Some(FreeProviderOpt {
                id: id.to_string(),
                tier,
                title: label.to_string(),
                name: if name.is_empty() { label.to_string() } else { name.to_string() },
                blurb: row
                    .get("blurb")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string(),
            })
        })
        .collect()
}

/// Live list when the sidecar returned options, otherwise the bundled catalog.
pub fn free_providers_or_fallback(v: &Value) -> Vec<FreeProviderOpt> {
    let parsed = parse_free_providers(v);
    if parsed.is_empty() {
        offline_free_options()
    } else {
        parsed
    }
}

pub fn free_key_options(options: &[FreeProviderOpt]) -> Vec<FreeProviderOpt> {
    options
        .iter()
        .filter(|o| {
            o.id != "demo"
                && o.id != "ollama"
                && (o.tier == "free_key")
        })
        .cloned()
        .collect()
}

pub fn provider_base(id: &str) -> &'static str {
    OFFLINE_PROVIDERS
        .iter()
        .find(|p| p.id == id)
        .map(|p| p.base_url)
        .unwrap_or("")
}

/// Auth modes the sidecar catalog advertises for a built-in provider.
pub fn provider_auth(id: &str) -> &'static [&'static str] {
    match id {
        "demo" | "ollama" | "rmb" | "llamacpp" => &["none"],
        "xai" => &["oauth", "api_key"],
        _ => &["api_key"],
    }
}

/// True when the provider offers a sign-in (device-code OAuth) path.
pub fn has_oauth(id: &str) -> bool {
    provider_auth(id).iter().any(|mode| *mode == "oauth")
}

/// Catalog default for `id`, preferring a live catalog row when one is supplied.
pub fn catalog_base_url(id: &str, catalog: &[(&str, &str)]) -> String {
    catalog
        .iter()
        .find(|(pid, url)| *pid == id && !url.trim().is_empty())
        .map(|(_, url)| (*url).to_string())
        .filter(|url| !url.is_empty())
        .unwrap_or_else(|| provider_base(id).to_string())
}

fn is_known_base_url(url: &str, catalog: &[(&str, &str)]) -> bool {
    let url = url.trim();
    if url.is_empty() {
        return true;
    }
    catalog.iter().any(|(_, known)| known.trim() == url)
        || OFFLINE_PROVIDERS.iter().any(|p| p.base_url == url)
}

/// Next base URL after a provider switch, matching the old Settings rule:
/// adopt the new catalog default when the field is empty or still a known
/// preset. Hidden-URL providers always adopt their own default so a leftover
/// OpenRouter (or other) URL cannot appear as this provider's endpoint.
/// Percent-encode a query parameter value.
pub fn query_param(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Base URL used to list models for `provider`.
///
/// Hidden-URL providers always use the catalog default so a leftover saved
/// URL (OpenRouter left on Anthropic) cannot replace that provider's list.
/// Typable-URL providers (Ollama, custom, llama.cpp, RMB) use the form or
/// saved URL when the owner has typed one.
pub fn discovery_url(provider: &str, form_or_saved_url: &str, catalog_url: &str) -> String {
    let form = form_or_saved_url.trim();
    let catalog = catalog_url.trim();
    let catalog = if catalog.is_empty() {
        provider_base(provider).to_string()
    } else {
        catalog.to_string()
    };
    if shows_base_url(provider) {
        if !form.is_empty() {
            form.to_string()
        } else {
            catalog
        }
    } else if !catalog.is_empty() {
        catalog
    } else {
        form.to_string()
    }
}

/// Path for `GET /api/models`, including a base_url override when we have one.
pub fn models_request_path(provider: &str, discovery_url: &str) -> String {
    let pid = provider.trim();
    if pid.is_empty() {
        return "models".into();
    }
    let mut path = format!("models?provider={}", query_param(pid));
    let url = discovery_url.trim();
    if !url.is_empty() {
        path.push_str("&base_url=");
        path.push_str(&query_param(url));
    }
    path
}

pub fn adopt_base_url(new_id: &str, current_url: &str, catalog: &[(&str, &str)]) -> Option<String> {
    let next = catalog_base_url(new_id, catalog);
    if next.is_empty() {
        return None;
    }
    let current = current_url.trim();
    if current == next {
        return None;
    }
    if current.is_empty() || is_known_base_url(current, catalog) || !shows_base_url(new_id) {
        Some(next)
    } else {
        None
    }
}

pub const OFFER_ID: &str = "major-changes-2026-11";
pub const OFFER_TITLE: &str = "House rules";
pub const OFFER_BODY: &str = "Remedy now has smarter token saving, local muscle sharing, and talking with other bots (A2A and MCP). It's worth a few minutes to go over the house rules together.";
pub const OFFER_PRIMARY: &str = "Let's go";
pub const OFFER_SECONDARY: &str = "Not now";

pub const KICKOFF: &str = "Hi — I'm Remedy. Let's set a few house rules so I can live with you the way you like. Every question is skippable, and you can skip all of them and keep the defaults. You can also switch to Traditional setup anytime; I won't lose what you've already said. What should I call you, and what should you call me?";

pub const TOPIC_KEYS: &[(&str, &[&str])] = &[
    ("names", &["user_name", "name", "agent_gender"]),
    ("language", &["ui_language"]),
    ("tone", &["persona", "sarcasm_mode"]),
    (
        "provider",
        &[
            "llm_provider",
            "llm_model",
            "llm_base_url",
            "custom_llm_name",
        ],
    ),
    ("workspace", &["project_path"]),
    ("proactive", &["proactive_reach_out", "drives_enabled"]),
    ("privacy", &["privacy_mode", "inner_audit"]),
    (
        "tokens",
        &["always_send_images", "vision_force_decode", "muscle_route"],
    ),
    ("gpu", &["muscle_gpu_policy"]),
    ("consent", &["approval_mode", "trust_profile"]),
    ("interop", &["interop_inbound", "interop_auto_reply"]),
    ("updates", &["update_check"]),
    ("launch", &["launch_at_login"]),
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Question {
    pub id: String,
    pub title: String,
    pub prompt: String,
}

pub fn default_questions() -> Vec<Question> {
    const RAW: &[(&str, &str, &str)] = &[
        (
            "names",
            "Names",
            "What should I call you, and what should you call me? You can keep Remedy, or pick another name.",
        ),
        (
            "language",
            "Language",
            "Which language should I use? The default is this computer's language, and I can follow what you type.",
        ),
        (
            "tone",
            "Tone",
            "How should I talk — balanced, concise, detailed, or playful?",
        ),
        (
            "provider",
            "Provider",
            "If you want a cloud model as muscle, which provider should I use? You can skip this and add a key later in Settings. I will not write API keys into logs or the house-rules file.",
        ),
        (
            "workspace",
            "Workspace",
            "Is there a default project folder I should use for tools and files? You can leave this empty.",
        ),
        (
            "proactive",
            "How proactive",
            "Should I reach out between visits, and keep my own drives going? Reaching out stays off unless you say yes.",
        ),
        (
            "privacy",
            "Privacy and audit",
            "Should I keep a visible audit of inner work, and turn on privacy mode that strips personal details from provider traffic?",
        ),
        (
            "tokens",
            "Token saving",
            "I can handle easy chats and pictures here on this PC, and send the muscle a short description instead of the raw image. That saves your tokens. Want that?",
        ),
        (
            "gpu",
            "Local muscle and the GPU",
            "If a local muscle (a bigger model on this PC) is running, how should we share the GPU? Auto is fine for most machines. On a 12 GB card, yielding the mind to the CPU lets a 9B muscle run at full speed.",
        ),
        (
            "consent",
            "Consent",
            "I will always ask before spending money, sending messages, or deleting things — that's recommended and already the rule. Should I also ask before other tools run, or use the usual in-project auto mode?",
        ),
        (
            "interop",
            "Other bots",
            "Should other bots be able to talk to me (A2A or MCP), and should I answer them automatically? Both stay off until you turn them on. Inbound connections stay on this computer and need a token.",
        ),
        (
            "updates",
            "Updates",
            "Updates happen in the app. Should I check for them automatically and let you know, or only when you ask? Installs always wait for you.",
        ),
        (
            "launch",
            "Start with the computer",
            "Should I start with this computer so I'm ready when you are?",
        ),
    ];
    RAW.iter()
        .map(|(id, title, prompt)| Question {
            id: (*id).into(),
            title: (*title).into(),
            prompt: (*prompt).into(),
        })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TopicStatus {
    Skipped,
    Set,
    Open,
}

pub fn topic_status(id: &str, skipped: &[String], draft_keys: &[String]) -> TopicStatus {
    if skipped.iter().any(|s| s == id) {
        return TopicStatus::Skipped;
    }
    let keys = TOPIC_KEYS
        .iter()
        .find(|(k, _)| *k == id)
        .map(|(_, v)| *v)
        .unwrap_or(&[]);
    if keys.iter().any(|k| draft_keys.iter().any(|d| d == k)) {
        TopicStatus::Set
    } else {
        TopicStatus::Open
    }
}

pub fn topic_status_label(status: TopicStatus) -> &'static str {
    match status {
        TopicStatus::Skipped => "skipped",
        TopicStatus::Set => "set",
        TopicStatus::Open => "open",
    }
}

pub fn mind_failure_message(reason: &str, detail: &str) -> String {
    let detail = detail.trim();
    if !detail.is_empty() && reason != "low_ram" && reason != "verify" && reason != "download" {
        return detail.to_string();
    }
    match reason {
        "low_ram" => {
            if detail.is_empty() {
                "This computer does not have enough memory to start the mind comfortably.".into()
            } else {
                detail.into()
            }
        }
        "verify" => {
            if detail.is_empty() {
                "The mind download could not be verified. You can retry, or continue with Traditional setup.".into()
            } else {
                detail.into()
            }
        }
        "download" => {
            if detail.is_empty() {
                "The mind could not be downloaded. You can retry, or continue with Traditional setup.".into()
            } else {
                detail.into()
            }
        }
        _ => {
            if detail.is_empty() {
                "The mind could not start. You can retry, or continue with Traditional setup."
                    .into()
            } else {
                detail.into()
            }
        }
    }
}

pub fn mind_phase_label(phase: &str) -> &'static str {
    match phase {
        "preparing" => "Getting everything ready…",
        "downloading" => "Downloading Remedy’s mind…",
        "extracting" => "Unpacking the mind…",
        "verifying" => "Verifying the download…",
        "starting" => "Starting the mind…",
        "ready" => "The mind is ready.",
        _ => "Working…",
    }
}

pub const MIND_TITLE: &str = "Getting Remedy’s mind ready";
pub const MIND_SUBTITLE: &str = "This is the last step of install. When her mind is ready, she’ll take it from here — a short conversation to set house rules. You can switch to Traditional setup at any time.";

pub const SPLASH_STARTING: &str = "Starting Remedy";
pub const SPLASH_CONNECTING: &str = "Connecting to local server";
pub const SPLASH_READY: &str = "Ready";
pub const SPLASH_FAILED: &str = "Server connection failed. Is Remedy installed?";
pub const SPLASH_TIMEOUT: &str =
    "Server failed to start after ~90s. On a fresh install, wait a moment and Retry.";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpdatePhase {
    Ready,
    Downloading,
    Finishing,
    StartingApp,
    Failed,
}

pub fn update_subtitle(phase: UpdatePhase) -> &'static str {
    match phase {
        UpdatePhase::Ready => "A new version is ready",
        UpdatePhase::Downloading => "Downloading update…",
        UpdatePhase::Finishing => "Finishing — Remedy will restart shortly",
        UpdatePhase::StartingApp => "Starting Remedy…",
        UpdatePhase::Failed => "Update failed",
    }
}

/// Map known installer lines. Unknown text is returned unchanged.
pub fn localize_update_message(raw: &str) -> String {
    let msg = raw.trim();
    if msg.is_empty() {
        return String::new();
    }
    if let Some(mb) = downloading_mb(msg) {
        return format!("Downloading update… {mb} MB");
    }
    if msg.starts_with("Download complete") {
        return "Download complete. Restarting to finish install…".into();
    }
    raw.to_string()
}

fn downloading_mb(msg: &str) -> Option<&str> {
    let lower = msg.to_ascii_lowercase();
    let rest = lower.strip_prefix("downloading update...")?;
    let rest = rest.trim();
    let mut parts = rest.split_whitespace();
    let mb = parts.next()?;
    let unit = parts.next()?;
    if unit != "mb" || parts.next().is_some() {
        return None;
    }
    if !mb_token(mb) {
        return None;
    }
    // Return the original slice so "12.5" keeps its spelling.
    let start = msg.len() - rest.len();
    let original = msg.get(start..)?;
    let token = original.split_whitespace().next()?;
    Some(token)
}

fn mb_token(mb: &str) -> bool {
    let mut seen_dot = false;
    let mut digits = 0u32;
    for c in mb.chars() {
        if c.is_ascii_digit() {
            digits += 1;
        } else if c == '.' && !seen_dot {
            seen_dot = true;
        } else {
            return false;
        }
    }
    digits > 0
}

pub fn notes_slice(notes: &str) -> String {
    notes.chars().take(800).collect()
}

/// `candidate` is newer than `current`. `1.0.0` is not newer than itself; `0.70.5` is older.
pub fn is_newer(candidate: &str, current: &str) -> bool {
    let a = semver_parts(candidate);
    let b = semver_parts(current);
    let n = a.len().max(b.len());
    for i in 0..n {
        let av = a.get(i).copied().unwrap_or(0);
        let bv = b.get(i).copied().unwrap_or(0);
        if av != bv {
            return av > bv;
        }
    }
    false
}

fn semver_parts(s: &str) -> Vec<u64> {
    s.trim()
        .trim_start_matches('v')
        .split(|c: char| !c.is_ascii_digit())
        .filter(|p| !p.is_empty())
        .map(|p| p.parse().unwrap_or(0))
        .collect()
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DownloadJob {
    pub id: String,
    pub label: String,
    pub percent: Option<u32>,
}

pub fn download_caption(jobs: &[DownloadJob]) -> String {
    jobs.iter()
        .map(|j| match j.percent {
            Some(p) => format!("{} {p}%", j.label),
            None => j.label.clone(),
        })
        .collect::<Vec<_>>()
        .join(" · ")
}

/// Title-bar fill is known only at 3% and above. `None` and 0–2 stay indeterminate.
pub fn titlebar_known(percent: Option<u32>) -> bool {
    percent.is_some_and(|p| p >= 3)
}

pub fn collect_download_jobs(
    voice: Option<&Value>,
    vision: Option<&Value>,
    hf: Option<&Value>,
) -> Vec<DownloadJob> {
    let mut jobs = Vec::new();
    if let Some(v) = voice {
        if let Some(job) = voice_job(v) {
            jobs.push(job);
        }
    }
    if let Some(v) = vision {
        if let Some(job) = vision_job(v) {
            jobs.push(job);
        }
    }
    if let Some(v) = hf {
        if let Some(job) = hf_job(v) {
            jobs.push(job);
        }
    }
    jobs
}

fn voice_job(v: &Value) -> Option<DownloadJob> {
    let sides = ["pack", "tts", "stt", "smart_turn", "hq"];
    let mut active: Vec<&Value> = Vec::new();
    for name in sides {
        if let Some(install) = v.get(name).and_then(|s| s.get("install")) {
            let st = install.get("status").and_then(|s| s.as_str()).unwrap_or("");
            if st.eq_ignore_ascii_case("downloading") || st.eq_ignore_ascii_case("loading") {
                active.push(install);
            }
        }
    }
    if active.is_empty() {
        return None;
    }
    let label = active
        .iter()
        .find_map(|s| {
            s.get("message")
                .and_then(|m| m.as_str())
                .filter(|m| !m.is_empty())
        })
        .unwrap_or("Remedy's voice")
        .to_string();
    let percent = active
        .iter()
        .filter_map(|s| s.get("percent").and_then(|n| n.as_f64()))
        .fold(None, |best, n| {
            let n = clamp_pct(n);
            Some(best.map(|b: u32| b.max(n)).unwrap_or(n))
        });
    Some(DownloadJob {
        id: "voice".into(),
        label,
        percent,
    })
}

fn vision_job(v: &Value) -> Option<DownloadJob> {
    let p = v.get("progress")?;
    let phase = p
        .get("phase")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    const BUSY: &[&str] = &[
        "downloading",
        "download",
        "extracting",
        "extract",
        "installing",
        "install",
        "verifying",
        "unpacking",
        "preparing",
        "starting",
        "loading",
    ];
    let done = p.get("bytes_done").and_then(|n| n.as_f64()).unwrap_or(0.0);
    let total = p.get("bytes_total").and_then(|n| n.as_f64()).unwrap_or(0.0);
    let busy = BUSY.contains(&phase.as_str())
        || (total > 0.0 && done < total && phase != "ready" && phase != "idle" && phase != "error");
    if !busy {
        return None;
    }
    let percent = if total > 0.0 {
        Some(clamp_pct(100.0 * done / total))
    } else {
        None
    };
    let label = p
        .get("message")
        .and_then(|m| m.as_str())
        .filter(|m| !m.is_empty())
        .or_else(|| p.get("current_file").and_then(|m| m.as_str()))
        .unwrap_or("Local model")
        .trim()
        .to_string();
    Some(DownloadJob {
        id: "vision".into(),
        label,
        percent,
    })
}

fn hf_job(v: &Value) -> Option<DownloadJob> {
    let phase = v
        .get("phase")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if phase != "downloading" && phase != "loading" {
        return None;
    }
    let percent = if let Some(n) = v.get("pct").and_then(|n| n.as_f64()) {
        Some(clamp_pct(n))
    } else {
        let total = v.get("bytes_total").and_then(|n| n.as_f64()).unwrap_or(0.0);
        if total > 0.0 {
            let done = v.get("bytes_done").and_then(|n| n.as_f64()).unwrap_or(0.0);
            Some(clamp_pct(100.0 * done / total))
        } else {
            None
        }
    };
    let label = v
        .get("message")
        .and_then(|m| m.as_str())
        .filter(|m| !m.is_empty())
        .or_else(|| v.get("filename").and_then(|m| m.as_str()))
        .unwrap_or("Language model")
        .trim()
        .to_string();
    Some(DownloadJob {
        id: "hf".into(),
        label,
        percent,
    })
}

fn clamp_pct(n: f64) -> u32 {
    n.round().clamp(0.0, 100.0) as u32
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReleaseInfo {
    pub version: String,
    pub notes: String,
    pub download_url: String,
    pub signature: String,
}

/// This desktop's version. Newer than the last Tauri build (0.70.5).
pub fn shell_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Only this repository's release assets, plus the GitHub release CDNs.
pub fn is_trusted_download_url(url: &str) -> bool {
    if url.starts_with("https://github.com/AhmiDarrow/RemedyAI/releases/") {
        return true;
    }
    if url.starts_with("https://objects.githubusercontent.com/")
        || url.starts_with("https://release-assets.githubusercontent.com/")
    {
        return !url.contains("..") && url.len() < 2048;
    }
    false
}

pub fn normalize_installer_url(url: &str) -> String {
    url.replace("Remedy_Desktop_", "Remedy.Desktop_")
        .replace("Remedy Desktop_", "Remedy.Desktop_")
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OfferCopy {
    pub title: String,
    pub body: String,
    pub primary: String,
    pub secondary: String,
}

impl Default for OfferCopy {
    fn default() -> Self {
        Self {
            title: OFFER_TITLE.into(),
            body: OFFER_BODY.into(),
            primary: OFFER_PRIMARY.into(),
            secondary: OFFER_SECONDARY.into(),
        }
    }
}

fn offer_text(v: &Value, key: &str, fallback: &str) -> String {
    v.get(key)
        .and_then(|s| s.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| fallback.to_string())
}

/// The pop-up copy when the server says an update offer is waiting.
pub fn offer_from_session(session: &Value) -> Option<OfferCopy> {
    let offer = session.get("update_offer")?;
    if offer.get("available").and_then(|b| b.as_bool()) != Some(true) {
        return None;
    }
    Some(OfferCopy {
        title: offer_text(offer, "title", OFFER_TITLE),
        body: offer_text(offer, "body", OFFER_BODY),
        primary: offer_text(offer, "primary", OFFER_PRIMARY),
        secondary: offer_text(offer, "secondary", OFFER_SECONDARY),
    })
}

pub fn flags_from_settings(v: &Value) -> SettingsFlags {
    SettingsFlags {
        loaded: true,
        needs_setup: v.get("needs_setup").and_then(|b| b.as_bool()),
        setup_completed: v.get("setup_completed").and_then(|b| b.as_bool()),
        config_exists: v.get("config_exists").and_then(|b| b.as_bool()),
    }
}

pub fn user_name_from_settings(v: &Value) -> String {
    v.get("user_name")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

fn failure_present(v: &Value) -> bool {
    match v {
        Value::Null | Value::Bool(false) => false,
        Value::String(s) => !s.trim().is_empty(),
        Value::Bool(true) | Value::Number(_) => true,
        Value::Array(a) => !a.is_empty(),
        Value::Object(m) => !m.is_empty(),
    }
}

fn failure_text(v: &Value) -> String {
    if let Some(s) = v.as_str() {
        return mind_failure_message(s, "");
    }
    let reason = v.get("reason").and_then(|r| r.as_str()).unwrap_or("");
    let detail = v.get("detail").and_then(|r| r.as_str()).unwrap_or("");
    mind_failure_message(reason, detail)
}

/// Stage plus the banner text when the mind failed and we land on Traditional setup.
pub fn stage_from_session(session: &Value, revisit: bool) -> (Stage, String) {
    let mode = session.get("mode").and_then(|s| s.as_str()).unwrap_or("");
    let mind_ready = session
        .get("mind_ready")
        .and_then(|b| b.as_bool())
        .unwrap_or(false);
    let failure = session.get("mind_failure").filter(|v| failure_present(v));
    let fallback = session
        .get("fallback_suggested")
        .and_then(|b| b.as_bool())
        .unwrap_or(false);
    let stage = stage_for(mode, mind_ready, failure.is_some() || fallback, revisit);
    let msg = if stage == Stage::Traditional {
        failure.map(failure_text).unwrap_or_default()
    } else {
        String::new()
    };
    (stage, msg)
}

/// `1.2 GB / 4.0 GB`, or empty when the size is unknown.
pub fn gb_pair(done: f64, total: f64) -> String {
    if total <= 0.0 {
        return String::new();
    }
    let gb = 1024f64 * 1024.0 * 1024.0;
    format!("{:.1} GB / {:.1} GB", done / gb, total / gb)
}

pub fn mind_status_line(phase: &str, bytes: &str, fraction: Option<f32>) -> String {
    let mut line = mind_phase_label(phase).to_string();
    if !bytes.is_empty() {
        line.push_str(" · ");
        line.push_str(bytes);
    }
    if let Some(f) = fraction {
        let pct = (f.clamp(0.0, 1.0) * 100.0).round() as u32;
        line.push_str(&format!(" · {pct}%"));
    }
    line
}

pub const SETTINGS_UNREACHABLE: &str = "Could not reach your Remedy settings (the local server is not responding or this browser is not signed in to it). Your setup is untouched. Use Retry, or reopen the Web UI from the desktop app.";
pub const UPDATE_TITLE: &str = "Remedy Update";
pub const UPDATE_LATER: &str = "Later";
pub const UPDATE_RELAUNCH: &str = "Update & Relaunch";
pub const UPDATE_RETRY: &str = "Retry";
pub const UPDATE_CLOSE: &str = "Close";
pub const UPDATE_PLEASE_WAIT: &str = "Please wait…";
pub const UPDATE_HANG_TIGHT: &str = "App restarts automatically — hang tight";
pub const UPDATE_STARTING: &str = "Starting download…";
pub const UPDATE_MANUAL: &str = "You can install manually from:";
pub const UPDATE_GITHUB: &str = "Download the latest installer from GitHub Releases.";
pub const UPDATE_NO_URL: &str = "No installer URL for this release.";
pub const UPDATE_WRONG: &str = "Something went wrong.";
pub const UPDATE_VERSION: &str = "Version";

pub fn platform_keys() -> &'static [&'static str] {
    if cfg!(windows) {
        &["windows-x86_64", "windows-x86-64"]
    } else if cfg!(target_os = "macos") {
        &["darwin-aarch64", "darwin-x86_64", "darwin-x86-64"]
    } else {
        &["linux-x86_64", "linux-x86-64"]
    }
}

/// A release is showable when it has a version, a download URL, and a signature.
pub fn parse_latest(body: &str, keys: &[&str]) -> Option<ReleaseInfo> {
    let v: Value = serde_json::from_str(body).ok()?;
    let version = v
        .get("version")
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if version.is_empty() {
        return None;
    }
    let notes = v
        .get("notes")
        .or_else(|| v.get("body"))
        .and_then(|s| s.as_str())
        .unwrap_or("")
        .to_string();
    let platforms = v.get("platforms")?;
    for key in keys {
        let Some(row) = platforms.get(*key) else {
            continue;
        };
        let url = row
            .get("url")
            .and_then(|s| s.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let signature = row
            .get("signature")
            .and_then(|s| s.as_str())
            .unwrap_or("")
            .trim();
        if !url.is_empty() && !signature.is_empty() && is_trusted_download_url(&url) {
            return Some(ReleaseInfo {
                version,
                notes,
                download_url: normalize_installer_url(&url),
                signature: signature.to_string(),
            });
        }
    }
    None
}

pub const DEMO_BASE: &str = "https://api.llm7.io/v1";
pub const OLLAMA_HINT: &str = "Requires Ollama installed locally. No cloud API key.";
pub const WORKSPACE_PLACEHOLDER: &str = "e.g. C:\\Users\\You\\Projects\\MyApp";
pub const SERVER_UNREACHABLE: &str = "Could not reach the local server to save setup.";
pub const XAI_SAVE_FAILED: &str =
    "Signed in with xAI, but could not save the provider. You can enter an API key instead.";
pub const MIND_ANSWER_FAILED: &str =
    "The mind could not answer. You can retry, or switch to Traditional setup.";

/// Tauri's message when this build cannot hand the installer off itself.
pub const UPDATE_PLATFORM: &str = "In-app updates are available on Windows, Linux AppImage, and macOS. Install from GitHub Releases.";

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn traditional_wins_until_a_revisit() {
        assert_eq!(
            stage_for("traditional", true, false, false),
            Stage::Traditional
        );
        assert_eq!(
            stage_for("traditional", true, false, true),
            Stage::Conversation
        );
        assert_eq!(
            stage_for("conversation", false, true, true),
            Stage::Traditional
        );
        assert_eq!(stage_for("conversation", false, false, true), Stage::Mind);
        assert_eq!(
            stage_for("conversation", true, true, false),
            Stage::Conversation
        );
    }

    #[test]
    fn wizard_stays_closed_when_settings_did_not_load() {
        assert!(!needs_wizard(None));
        assert!(!needs_wizard(Some(&SettingsFlags {
            loaded: false,
            ..SettingsFlags::default()
        })));
        assert!(needs_wizard(Some(&SettingsFlags {
            loaded: true,
            needs_setup: Some(true),
            ..SettingsFlags::default()
        })));
        assert!(needs_wizard(Some(&SettingsFlags {
            loaded: true,
            setup_completed: Some(false),
            ..SettingsFlags::default()
        })));
        assert!(needs_wizard(Some(&SettingsFlags {
            loaded: true,
            config_exists: Some(false),
            ..SettingsFlags::default()
        })));
        assert!(!needs_wizard(Some(&SettingsFlags {
            loaded: true,
            needs_setup: Some(false),
            setup_completed: Some(true),
            config_exists: Some(true),
        })));
    }

    #[test]
    fn name_prompt_waits_for_a_quiet_studio() {
        assert!(ask_user_name(false, false, false, "", false));
        assert!(ask_user_name(false, false, false, "  ", false));
        assert!(!ask_user_name(true, false, false, "", false));
        assert!(!ask_user_name(false, true, false, "", false));
        assert!(!ask_user_name(false, false, true, "", false));
        assert!(!ask_user_name(false, false, false, "Ahmi", false));
        assert!(!ask_user_name(false, false, false, "", true));
        assert!(reject_empty_name("  "));
        assert!(!reject_empty_name("Ada"));
    }

    #[test]
    fn this_desktop_is_newer_than_the_last_tauri_build() {
        assert_eq!(shell_version(), "2.1.1");
        assert!(is_newer("1.0.0", "0.70.5"));
        assert!(!is_newer("0.70.5", "1.0.0"));
        assert!(!is_newer("1.0.0", "1.0.0"));
        assert!(is_newer("v1.0.1", "1.0.0"));
    }

    #[test]
    fn installer_urls_stay_on_this_repo() {
        assert!(is_trusted_download_url(
            "https://github.com/AhmiDarrow/RemedyAI/releases/download/v1.2.0/Remedy.Desktop_1.2.0_x64-setup.exe"
        ));
        assert!(is_trusted_download_url(
            "https://objects.githubusercontent.com/github-production-release-asset-abc"
        ));
        assert!(!is_trusted_download_url(
            "https://objects.githubusercontent.com/a/../b"
        ));
        assert!(!is_trusted_download_url(
            "https://evil.example/Remedy.Desktop.exe"
        ));
        assert!(!is_trusted_download_url(
            "http://github.com/AhmiDarrow/RemedyAI/releases/a"
        ));
        assert_eq!(
            normalize_installer_url("https://example/Remedy_Desktop_1.exe"),
            "https://example/Remedy.Desktop_1.exe"
        );
        assert_eq!(
            normalize_installer_url("https://example/Remedy Desktop_1.exe"),
            "https://example/Remedy.Desktop_1.exe"
        );
    }

    #[test]
    fn latest_json_needs_a_signature_and_a_trusted_url() {
        let ok = r#"{
            "version": "1.2.0",
            "notes": "house rules",
            "platforms": {
                "windows-x86_64": {
                    "url": "https://github.com/AhmiDarrow/RemedyAI/releases/download/v1.2.0/Remedy_Desktop_1.2.0.exe",
                    "signature": "untrusted comment: signature"
                }
            }
        }"#;
        let info = parse_latest(ok, &["windows-x86_64"]).expect("release");
        assert_eq!(info.version, "1.2.0");
        assert!(info.download_url.contains("Remedy.Desktop_"));
        assert!(!info.signature.is_empty());
        let unsigned = ok.replace("untrusted comment: signature", "");
        assert!(parse_latest(&unsigned, &["windows-x86_64"]).is_none());
        let evil = ok.replace(
            "https://github.com/AhmiDarrow/RemedyAI/releases/download/v1.2.0/Remedy_Desktop_1.2.0.exe",
            "https://evil.example/setup.exe",
        );
        assert!(parse_latest(&evil, &["windows-x86_64"]).is_none());
    }

    #[test]
    fn offer_falls_back_when_the_server_leaves_a_blank() {
        assert!(offer_from_session(&json!({"update_offer": {"available": false}})).is_none());
        let copy = offer_from_session(&json!({
            "update_offer": {"available": true, "title": "", "body": "Custom body"}
        }))
        .expect("offer");
        assert_eq!(copy.title, OFFER_TITLE);
        assert_eq!(copy.body, "Custom body");
        assert_eq!(copy.primary, OFFER_PRIMARY);
        assert!(OFFER_BODY.contains("smarter token saving"));
        assert!(OFFER_BODY.contains("A2A and MCP"));
        assert!(OFFER_BODY.contains("house rules"));
        assert!(!OFFER_BODY.contains("built-in mind"));
    }

    #[test]
    fn title_bar_hides_a_bar_under_three_percent() {
        assert!(!titlebar_known(None));
        assert!(!titlebar_known(Some(0)));
        assert!(!titlebar_known(Some(2)));
        assert!(titlebar_known(Some(3)));
        let jobs = [
            DownloadJob {
                id: "voice".into(),
                label: "Remedy's voice".into(),
                percent: Some(40),
            },
            DownloadJob {
                id: "hf".into(),
                label: "Language model".into(),
                percent: None,
            },
        ];
        assert_eq!(
            download_caption(&jobs),
            "Remedy's voice 40% · Language model"
        );
    }

    #[test]
    fn session_json_picks_the_same_stage_as_the_react_flow() {
        let (stage, msg) =
            stage_from_session(&json!({"mode": "traditional", "mind_ready": true}), false);
        assert_eq!(stage, Stage::Traditional);
        assert!(msg.is_empty());
        let (stage, msg) =
            stage_from_session(&json!({"mode": "traditional", "mind_ready": true}), true);
        assert_eq!(stage, Stage::Conversation);
        assert!(msg.is_empty());
        let (stage, msg) = stage_from_session(
            &json!({"mind_failure": {"reason": "download", "detail": ""}}),
            true,
        );
        assert_eq!(stage, Stage::Traditional);
        assert!(msg.contains("could not be downloaded"));
    }

    #[test]
    fn short_path_skips_provider_chat_and_vision() {
        assert_eq!(next_step(STEP_WELCOME, true), Some(STEP_FOLDER));
        assert_eq!(next_step(STEP_FOLDER, true), Some(STEP_STYLE));
        assert_eq!(next_step(STEP_STYLE, true), Some(STEP_READY));
        assert_eq!(next_step(STEP_READY, true), None);
        assert_eq!(prev_step(STEP_READY, true), Some(STEP_STYLE));
        assert_eq!(prev_step(STEP_FOLDER, true), Some(STEP_WELCOME));
        assert_eq!(next_step(STEP_WELCOME, false), Some(STEP_PROVIDER));
        assert_eq!(next_step(STEP_PROVIDER, false), Some(STEP_FOLDER));
    }

    #[test]
    fn free_providers_use_the_live_list_and_fall_back_offline() {
        let live = serde_json::json!({
            "options": [
                {"id": "demo", "tier": "instant", "title": "Start instantly (Demo)", "name": "Demo"},
                {"id": "groq", "tier": "free_key", "title": "Groq", "name": "Groq", "blurb": "fast"},
                {"id": "newfree", "tier": "free_key", "title": "New Cloud", "name": "New Cloud"}
            ]
        });
        let keys = free_key_options(&parse_free_providers(&live));
        assert_eq!(keys.iter().map(|o| o.id.as_str()).collect::<Vec<_>>(), vec!["groq", "newfree"]);
        assert_eq!(free_providers_or_fallback(&serde_json::json!({})), offline_free_options());
        let offline = free_key_options(&offline_free_options());
        assert!(offline.iter().any(|o| o.id == "google" && o.title.contains("Gemini")));
        assert!(offline.iter().any(|o| o.id == "groq"));
        let paid = paid_providers_except(&offline.iter().map(|o| o.id.clone()).collect::<Vec<_>>());
        assert!(paid.iter().all(|p| p.id != "google" && p.id != "groq"));
        assert!(paid.iter().any(|p| p.id == "openai"));
    }

    /// Sidecar / old-Remedy defaults this port must keep per provider.
    const EXPECTED: &[(&str, &str, &str, &[&str], bool)] = &[
        ("demo", "Demo (Free)", "https://api.llm7.io/v1", &["none"], false),
        ("openai", "OpenAI", "https://api.openai.com/v1", &["api_key"], false),
        ("anthropic", "Anthropic", "https://api.anthropic.com/v1", &["api_key"], false),
        (
            "google",
            "Google AI (Gemini)",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            &["api_key"],
            false,
        ),
        ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", &["api_key"], false),
        ("xai", "xAI (Grok)", "https://api.x.ai/v1", &["oauth", "api_key"], false),
        ("groq", "Groq", "https://api.groq.com/openai/v1", &["api_key"], false),
        ("mistral", "Mistral", "https://api.mistral.ai/v1", &["api_key"], false),
        ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", &["api_key"], false),
        ("poe", "Poe", "https://api.poe.com/v1", &["api_key"], false),
        ("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", &["none"], true),
        ("llamacpp", "llama.cpp (local)", "http://127.0.0.1:8080/v1", &["none"], true),
        ("rmb", "RMB (local agent)", "http://127.0.0.1:8787/v1", &["none"], true),
        (
            "custom",
            "Custom / OpenAI-compatible",
            "http://127.0.0.1:5001/v1",
            &["api_key"],
            true,
        ),
    ];

    #[test]
    fn each_provider_row_keeps_its_own_label_url_and_auth() {
        assert_eq!(OFFLINE_PROVIDERS.len(), EXPECTED.len());
        for (i, p) in OFFLINE_PROVIDERS.iter().enumerate() {
            let (id, name, url, auth, show) = EXPECTED[i];
            assert_eq!(p.id, id, "catalog order drifted at {i}");
            assert_eq!(p.name, name, "{id} label");
            assert_eq!(p.base_url, url, "{id} default base URL");
            assert_eq!(provider_auth(p.id), auth, "{id} auth");
            assert_eq!(has_oauth(p.id), p.id == "xai", "{id} oauth");
            assert_eq!(shows_base_url(p.id), show, "{id} show_base_url");
            if p.id != "openrouter" {
                assert!(
                    !p.name.to_ascii_lowercase().contains("openrouter"),
                    "{id} must not reuse the OpenRouter label"
                );
                assert!(
                    !p.base_url.contains("openrouter.ai"),
                    "{id} must not reuse the OpenRouter URL"
                );
            }
        }
        assert_eq!(provider_base("anthropic"), "https://api.anthropic.com/v1");
        assert_eq!(provider_base("xai"), "https://api.x.ai/v1");
        assert_eq!(provider_base("openrouter"), "https://openrouter.ai/api/v1");
        assert!(has_oauth("xai"));
        assert!(!has_oauth("anthropic"));
        assert!(!has_oauth("openrouter"));
    }

    #[test]
    fn switching_providers_adopts_each_catalog_url_and_keeps_a_custom_one() {
        let openrouter = "https://openrouter.ai/api/v1";
        for p in OFFLINE_PROVIDERS {
            let next = adopt_base_url(p.id, openrouter, &[]);
            if p.id == "openrouter" {
                assert!(next.is_none(), "OpenRouter should keep its own URL");
            } else {
                assert_eq!(
                    next.as_deref(),
                    Some(p.base_url),
                    "switching from OpenRouter onto {} should adopt its own URL",
                    p.id
                );
            }
        }
        assert_eq!(
            adopt_base_url("xai", "https://api.anthropic.com/v1", &[]).as_deref(),
            Some("https://api.x.ai/v1")
        );
        // A typable custom URL is kept when the new provider shows the field.
        assert_eq!(
            adopt_base_url("custom", "http://127.0.0.1:1234/v1", &[]),
            None
        );
        // Hidden-URL providers still drop a leftover custom URL so it cannot
        // appear as that provider's endpoint.
        assert_eq!(
            adopt_base_url("anthropic", "http://127.0.0.1:1234/v1", &[]).as_deref(),
            Some("https://api.anthropic.com/v1")
        );
    }

    #[test]
    fn hidden_url_providers_list_models_against_their_catalog_url() {
        let leftover = "https://openrouter.ai/api/v1";
        let anthropic = "https://api.anthropic.com/v1";
        assert_eq!(
            discovery_url("anthropic", leftover, anthropic),
            anthropic,
            "Anthropic must not list models from a leftover OpenRouter URL"
        );
        assert_eq!(
            discovery_url("xai", leftover, "https://api.x.ai/v1"),
            "https://api.x.ai/v1"
        );
        assert_eq!(
            discovery_url("openrouter", leftover, leftover),
            leftover
        );
        // Typable providers keep the owner's URL.
        assert_eq!(
            discovery_url("custom", "http://127.0.0.1:1234/v1", "http://127.0.0.1:5001/v1"),
            "http://127.0.0.1:1234/v1"
        );
        assert_eq!(
            discovery_url("ollama", "", "http://127.0.0.1:11434/v1"),
            "http://127.0.0.1:11434/v1"
        );
        let path = models_request_path("anthropic", anthropic);
        assert!(
            path.starts_with("models?provider=anthropic&base_url="),
            "{path}"
        );
        assert!(
            path.contains("api.anthropic.com"),
            "path should pin Anthropic's catalog host, got {path}"
        );
        assert!(
            !path.contains("openrouter"),
            "path must not keep a leftover OpenRouter URL, got {path}"
        );
    }

    #[test]
    fn persist_base_url_snaps_hidden_providers_and_keeps_typable() {
        assert_eq!(
            persist_base_url("anthropic", "https://openrouter.ai/api/v1"),
            "https://api.anthropic.com/v1"
        );
        assert_eq!(
            persist_base_url("xai", "https://openrouter.ai/api/v1"),
            "https://api.x.ai/v1"
        );
        assert_eq!(
            persist_base_url("openai", ""),
            "https://api.openai.com/v1"
        );
        assert_eq!(
            persist_base_url("ollama", "http://192.168.1.9:11434/v1"),
            "http://192.168.1.9:11434/v1"
        );
        assert_eq!(
            persist_base_url("custom", "http://127.0.0.1:1234/v1"),
            "http://127.0.0.1:1234/v1"
        );
    }

    fn offline_catalog_is_the_full_old_provider_set() {
        let ids: Vec<&str> = OFFLINE_PROVIDERS.iter().map(|p| p.id).collect();
        assert_eq!(
            ids,
            vec![
                "demo", "openai", "anthropic", "google", "deepseek", "xai", "groq",
                "mistral", "openrouter", "poe", "ollama", "llamacpp", "rmb", "custom",
            ]
        );
    }

}
