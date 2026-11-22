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

pub fn next_step(step: usize) -> Option<usize> {
    if step + 1 < STEP_LABELS.len() {
        Some(step + 1)
    } else {
        None
    }
}

pub fn prev_step(step: usize) -> Option<usize> {
    step.checked_sub(1)
}

pub fn progress_fraction(step: usize) -> f32 {
    let denom = (STEP_LABELS.len() - 1) as f32;
    (step.min(STEP_LABELS.len() - 1) as f32) / denom
}

pub const KEY_ERROR: &str =
    "Enter an API key, or choose Demo (no signup) / Ollama for free use.";
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

/// Balanced is stored as `default`, matching the wizard draft.
pub fn persona_wire(persona: &str) -> &str {
    if persona == "balanced" { "default" } else { persona }
}

#[derive(Clone, Debug)]
pub struct ProviderOpt {
    pub id: &'static str,
    pub name: &'static str,
    pub base_url: &'static str,
}

/// Offline catalog. No `advanced` flag, so the paid list is everything except Demo and Ollama.
pub const OFFLINE_PROVIDERS: &[ProviderOpt] = &[
    ProviderOpt { id: "demo", name: "Demo (Free)", base_url: "https://api.llm7.io/v1" },
    ProviderOpt { id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1" },
    ProviderOpt { id: "anthropic", name: "Anthropic", base_url: "https://api.anthropic.com/v1" },
    ProviderOpt {
        id: "google",
        name: "Google AI (Gemini)",
        base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    ProviderOpt { id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1" },
    ProviderOpt { id: "xai", name: "xAI (Grok)", base_url: "https://api.x.ai/v1" },
    ProviderOpt { id: "groq", name: "Groq", base_url: "https://api.groq.com/openai/v1" },
    ProviderOpt { id: "mistral", name: "Mistral", base_url: "https://api.mistral.ai/v1" },
    ProviderOpt { id: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1" },
    ProviderOpt { id: "poe", name: "Poe", base_url: "https://api.poe.com/v1" },
    ProviderOpt { id: "ollama", name: "Ollama (local)", base_url: "http://127.0.0.1:11434/v1" },
    ProviderOpt { id: "llamacpp", name: "llama.cpp (local)", base_url: "http://127.0.0.1:8080/v1" },
    ProviderOpt { id: "rmb", name: "RMB (local agent)", base_url: "http://127.0.0.1:8787/v1" },
    ProviderOpt { id: "custom", name: "Custom / OpenAI-compatible", base_url: "http://127.0.0.1:5001/v1" },
];

pub fn paid_providers() -> Vec<&'static ProviderOpt> {
    OFFLINE_PROVIDERS
        .iter()
        .filter(|p| p.id != "demo" && p.id != "ollama")
        .collect()
}

pub fn provider_base(id: &str) -> &'static str {
    OFFLINE_PROVIDERS
        .iter()
        .find(|p| p.id == id)
        .map(|p| p.base_url)
        .unwrap_or("")
}

pub const OFFER_ID: &str = "major-changes-2026-11";
pub const OFFER_TITLE: &str = "House rules";
pub const OFFER_BODY: &str = "Remedy has changed in some big ways: a new built-in mind, smarter token saving, local muscle sharing, and talking with your other bots. It's worth a few minutes to go over the house rules together.";
pub const OFFER_PRIMARY: &str = "Let's go";
pub const OFFER_SECONDARY: &str = "Not now";

pub const KICKOFF: &str = "Hi — I'm Remedy. Let's set a few house rules so I can live with you the way you like. Every question is skippable, and you can skip all of them and keep the defaults. You can also switch to Traditional setup anytime; I won't lose what you've already said. What should I call you, and what should you call me?";

pub const TOPIC_KEYS: &[(&str, &[&str])] = &[
    ("names", &["user_name", "name", "agent_gender"]),
    ("language", &["ui_language"]),
    ("tone", &["persona", "sarcasm_mode"]),
    ("provider", &["llm_provider", "llm_model", "llm_base_url", "custom_llm_name"]),
    ("workspace", &["project_path"]),
    ("proactive", &["proactive_reach_out", "drives_enabled"]),
    ("privacy", &["privacy_mode", "inner_audit"]),
    ("tokens", &["always_send_images", "vision_force_decode", "muscle_route"]),
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
        ("names", "Names", "What should I call you, and what should you call me? You can keep Remedy, or pick another name."),
        ("language", "Language", "Which language should I use? The default is this computer's language, and I can follow what you type."),
        ("tone", "Tone", "How should I talk — balanced, concise, detailed, or playful?"),
        ("provider", "Provider", "If you want a cloud model as muscle, which provider should I use? You can skip this and add a key later in Settings. I will not write API keys into logs or the house-rules file."),
        ("workspace", "Workspace", "Is there a default project folder I should use for tools and files? You can leave this empty."),
        ("proactive", "How proactive", "Should I reach out between visits, and keep my own drives going? Reaching out stays off unless you say yes."),
        ("privacy", "Privacy and audit", "Should I keep a visible audit of inner work, and turn on privacy mode that strips personal details from provider traffic?"),
        ("tokens", "Token saving", "I can handle easy chats and pictures here on this PC, and send the muscle a short description instead of the raw image. That saves your tokens. Want that?"),
        ("gpu", "Local muscle and the GPU", "If a local muscle (a bigger model on this PC) is running, how should we share the GPU? Auto is fine for most machines. On a 12 GB card, yielding the mind to the CPU lets a 9B muscle run at full speed."),
        ("consent", "Consent", "I will always ask before spending money, sending messages, or deleting things — that's recommended and already the rule. Should I also ask before other tools run, or use the usual in-project auto mode?"),
        ("interop", "Other bots", "Should other bots be able to talk to me (A2A or MCP), and should I answer them automatically? Both stay off until you turn them on. Inbound connections stay on this computer and need a token."),
        ("updates", "Updates", "Updates happen in the app. Should I check for them automatically and let you know, or only when you ask? Installs always wait for you."),
        ("launch", "Start with the computer", "Should I start with this computer so I'm ready when you are?"),
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
                "The mind could not start. You can retry, or continue with Traditional setup.".into()
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
pub const SPLASH_TIMEOUT: &str = "Server failed to start after ~90s. On a fresh install, wait a moment and Retry.";

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
        .find_map(|s| s.get("message").and_then(|m| m.as_str()).filter(|m| !m.is_empty()))
        .unwrap_or("Remedy's voice")
        .to_string();
    let percent = active.iter().filter_map(|s| s.get("percent").and_then(|n| n.as_f64())).fold(
        None,
        |best, n| {
            let n = clamp_pct(n);
            Some(best.map(|b: u32| b.max(n)).unwrap_or(n))
        },
    );
    Some(DownloadJob { id: "voice".into(), label, percent })
}

fn vision_job(v: &Value) -> Option<DownloadJob> {
    let p = v.get("progress")?;
    let phase = p.get("phase").and_then(|s| s.as_str()).unwrap_or("").to_ascii_lowercase();
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
    Some(DownloadJob { id: "vision".into(), label, percent })
}

fn hf_job(v: &Value) -> Option<DownloadJob> {
    let phase = v.get("phase").and_then(|s| s.as_str()).unwrap_or("").to_ascii_lowercase();
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
    Some(DownloadJob { id: "hf".into(), label, percent })
}

fn clamp_pct(n: f64) -> u32 {
    n.round().clamp(0.0, 100.0) as u32
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReleaseInfo {
    pub version: String,
    pub notes: String,
    pub download_url: String,
    pub signed: bool,
}

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
    let version = v.get("version").and_then(|s| s.as_str()).unwrap_or("").trim().to_string();
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
        let signature = row.get("signature").and_then(|s| s.as_str()).unwrap_or("").trim();
        if !url.is_empty() && !signature.is_empty() {
            return Some(ReleaseInfo {
                version,
                notes,
                download_url: url,
                signed: true,
            });
        }
    }
    None
}

pub const DEMO_BASE: &str = "https://api.llm7.io/v1";
pub const OLLAMA_HINT: &str = "Requires Ollama installed locally. No cloud API key.";
pub const WORKSPACE_PLACEHOLDER: &str = "e.g. C:\\Users\\You\\Projects\\MyApp";
pub const SERVER_UNREACHABLE: &str = "Could not reach the local server to save setup.";
pub const XAI_SAVE_FAILED: &str = "Signed in with xAI, but could not save the provider. You can enter an API key instead.";
pub const MIND_ANSWER_FAILED: &str = "The mind could not answer. You can retry, or switch to Traditional setup.";
