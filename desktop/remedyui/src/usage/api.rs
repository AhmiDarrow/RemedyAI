//! Usage, continuity, and NanoToken calls.
//!
//! Paths match `desktop/src/api/usage.ts`: the runtime client base is
//! `{origin}/api`, and these paths are joined onto it the same way
//! `runtime::Client` joins `sessions` and `models`.

use remedy_ui::net::{Http, NetError, Request};
use remedy_ui::reactive::Cancel;
use serde_json::Value;

/// One row of `/usage/summary` totals, or one provider / model bucket.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct UsageTotals {
    pub prompt_tokens: f64,
    pub completion_tokens: f64,
    pub total_tokens: f64,
    pub estimated_cost_usd: f64,
    pub events: f64,
}

impl Default for UsageTotals {
    fn default() -> Self {
        Self {
            prompt_tokens: 0.0,
            completion_tokens: 0.0,
            total_tokens: 0.0,
            estimated_cost_usd: 0.0,
            events: 0.0,
        }
    }
}

/// A provider or model bucket. `provider` is empty when the server omitted it.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct UsageByGroup {
    pub provider: String,
    pub model: String,
    pub totals: UsageTotals,
}

/// `GET /usage/summary`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct UsageSummary {
    pub range_days: f64,
    pub totals: UsageTotals,
    pub by_provider: Vec<UsageByGroup>,
    pub by_model: Vec<UsageByGroup>,
}

/// One `GET /usage/series` point.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct UsagePoint {
    pub day: String,
    pub group: String,
    pub total_tokens: f64,
    pub estimated_cost_usd: f64,
    pub events: f64,
}

/// A quality field that may be a number, a string, or absent.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Loose {
    /// JSON null or a missing key. The dashboard draws an em dash.
    Empty,
    /// A finite number.
    Num(f64),
    /// A non-numeric string the server sent.
    Text(String),
}

impl Loose {
    pub(crate) fn count(&self) -> String {
        match self {
            Loose::Empty => "—".into(),
            Loose::Num(n) => format_number(*n),
            Loose::Text(s) => s.clone(),
        }
    }

    fn number(&self) -> Option<f64> {
        match self {
            Loose::Num(n) if n.is_finite() => Some(*n),
            _ => None,
        }
    }
}

/// `context_snapshot` fields the dashboard prints.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Snapshot {
    pub intent: String,
    pub policy_id: String,
    pub nudge: Option<String>,
}

/// `GET /continuity/dashboard`, reduced to what the panel shows.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Continuity {
    pub harness_mode: String,
    pub token_method: String,
    pub token_estimate: f64,
    /// `token.last_remeasure.fill_pct`, when it is a number.
    pub token_fill: Option<f64>,
    /// `context_snapshot.fill_pct`, the fallback the Tauri panel uses.
    pub snapshot_fill: Option<f64>,
    pub turns: Loose,
    pub tokens_saved: Loose,
    pub soft_nudge: Loose,
    pub strong_nudge: Loose,
    pub compress: Loose,
    pub stuck_rate: Option<f64>,
    pub re_explain_rate: Option<f64>,
    pub tool_fail_max: Loose,
    pub step_count: f64,
    pub success_rate: Option<f64>,
    pub recent: Vec<String>,
    pub snapshot: Option<Snapshot>,
    pub goals: Vec<String>,
    pub goals_stale: bool,
    pub samples: f64,
    pub error_rate: Option<f64>,
    pub avg_latency_ms: Option<f64>,
    pub flaky: bool,
    pub rate_limit_hits: f64,
}

/// `GET /nanoswarm/token/status`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct TokenStatus {
    pub label: String,
    pub last_method: String,
    pub last_estimate: f64,
    pub active_provider: Option<String>,
    pub active_model: Option<String>,
    pub worker: Option<String>,
    pub note: Option<String>,
}

/// Summary, series, and continuity from one refresh.
#[derive(Clone, Debug)]
pub(crate) struct DashboardData {
    pub summary: UsageSummary,
    pub points: Vec<UsagePoint>,
    pub continuity: Continuity,
}

/// One bar in the tokens-over-time chart, after points are summed by day.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct DayBar {
    pub day: String,
    pub label: String,
    pub tokens: f64,
    /// Pixel height. Matches the Tauri chart: 0 when empty, else at least 3 and at most 88.
    pub height: f32,
    pub tip: String,
}

/// Date ranges the Tauri range menu offers, in menu order. Default is 7 days.
pub(crate) const RANGE_DAYS: [u32; 4] = [1, 7, 30, 90];
pub(crate) const RANGE_LABELS: [&str; 4] = ["1 day", "7 days", "30 days", "90 days"];

pub(crate) fn range_days(index: usize) -> u32 {
    RANGE_DAYS.get(index).copied().unwrap_or(7)
}

/// Same base and bearer as [`crate::runtime::Client::new`] with [`crate::runtime::origin`]
/// and [`crate::runtime::load_token`]. The client's HTTP handle stays private to the
/// runtime module, so the panel builds the loopback client the same way.
pub(crate) fn runtime_http() -> Http {
    let base = format!("{}/api", crate::runtime::origin().trim_end_matches('/'));
    let mut http = Http::new(base);
    if let Some(t) = crate::runtime::load_token().filter(|t| !t.is_empty()) {
        http = http.header("Authorization", format!("Bearer {t}"));
    }
    http
}

pub(crate) fn summary_path(range_days: u32, session_id: Option<&str>) -> String {
    let mut path = format!("usage/summary?range_days={range_days}");
    if let Some(id) = session_id.map(str::trim).filter(|id| !id.is_empty()) {
        path.push_str("&session_id=");
        path.push_str(&query_escape(id));
    }
    path
}

pub(crate) fn series_path(range_days: u32, group: &str) -> String {
    let group = if group == "model" { "model" } else { "provider" };
    format!("usage/series?range_days={range_days}&group={group}")
}

pub(crate) fn continuity_path(session_id: Option<&str>) -> String {
    match session_id.map(str::trim).filter(|id| !id.is_empty()) {
        Some(id) => format!("continuity/dashboard?session_id={}", query_escape(id)),
        None => "continuity/dashboard".into(),
    }
}

pub(crate) fn token_status_path() -> &'static str {
    "nanoswarm/token/status"
}

pub(crate) fn export_path(range_days: u32) -> String {
    format!("usage/export?range_days={range_days}&format=csv")
}

/// Summary, series, and continuity. A failure of any of the three fails the refresh,
/// matching `Promise.all` in the Tauri dashboard. Token status is separate so a missing
/// nanoswarm route does not blank the ledger.
pub(crate) fn fetch_dashboard(
    http: &Http,
    range_days: u32,
    session_id: Option<&str>,
) -> Result<DashboardData, String> {
    let cancel = Cancel::new();
    let summary = get_json(http, &summary_path(range_days, None), &cancel).and_then(|v| {
        parse_summary(&v).ok_or_else(|| "the usage summary was incomplete".into())
    })?;
    let series = get_json(http, &series_path(range_days, "provider"), &cancel).and_then(|v| {
        parse_series(&v).ok_or_else(|| "the usage series was incomplete".into())
    })?;
    let continuity = get_json(http, &continuity_path(session_id), &cancel).and_then(|v| {
        parse_continuity(&v).ok_or_else(|| "the continuity dashboard was incomplete".into())
    })?;
    Ok(DashboardData {
        summary,
        points: series,
        continuity,
    })
}

/// Best-effort `GET /nanoswarm/token/status`. `Err` leaves the previous card in place.
pub(crate) fn fetch_token_status(http: &Http) -> Result<TokenStatus, String> {
    let cancel = Cancel::new();
    let v = get_json(http, token_status_path(), &cancel)?;
    parse_token_status(&v).ok_or_else(|| "the token status was incomplete".into())
}

/// `GET /usage/export?format=csv`. Returns the file bytes and a download name.
pub(crate) fn fetch_export(http: &Http, range_days: u32) -> Result<(Vec<u8>, String), String> {
    let cancel = Cancel::new();
    let response = http
        .send_blocking(
            Request::get(export_path(range_days)).header("Accept", "text/csv"),
            &cancel,
        )
        .map_err(|e| explain(&e))?;
    let name = attachment_name(response.header("content-disposition"), range_days);
    Ok((response.body, name))
}

/// Writes the CSV into the person's Downloads folder, under the server's file name.
pub(crate) fn save_csv(filename: &str, bytes: &[u8]) -> Result<std::path::PathBuf, String> {
    let dir = downloads_dir()?;
    let name = sanitize_filename(filename);
    let path = dir.join(name);
    std::fs::write(&path, bytes).map_err(|e| format!("could not save the CSV ({e})"))?;
    Ok(path)
}

fn downloads_dir() -> Result<std::path::PathBuf, String> {
    if let Some(dir) = std::env::var_os("USERPROFILE") {
        let dir = std::path::PathBuf::from(dir).join("Downloads");
        if std::fs::create_dir_all(&dir).is_ok() {
            return Ok(dir);
        }
    }
    if let Some(dir) = std::env::var_os("HOME") {
        let dir = std::path::PathBuf::from(dir).join("Downloads");
        if std::fs::create_dir_all(&dir).is_ok() {
            return Ok(dir);
        }
    }
    let dir = std::env::temp_dir().join("remedy-usage");
    std::fs::create_dir_all(&dir).map_err(|e| format!("could not choose a folder for the CSV ({e})"))?;
    Ok(dir)
}

fn get_json(http: &Http, path: &str, cancel: &Cancel) -> Result<Value, String> {
    let response = http
        .send_blocking(Request::get(path), cancel)
        .map_err(|e| explain(&e))?;
    response
        .json::<Value>()
        .map_err(|e| explain(&e))
}

/// User-facing text for a failed call. JSON `detail` is flattened the way
/// `formatApiErrorBody` flattens FastAPI errors; other bodies are clipped.
pub(crate) fn explain(err: &NetError) -> String {
    match err {
        NetError::Http(h) => {
            let fallback = format!("HTTP {}", h.status);
            explain_body(&h.body, &fallback)
        }
        NetError::Timeout => "The server did not answer in time".into(),
        NetError::Network(m) => format!("Cannot reach the local API ({m})"),
        NetError::Cancelled => "The request was cancelled".into(),
        NetError::Decode(m) => format!("The answer was not readable ({m})"),
        NetError::TooLarge(n) => format!("The answer was longer than {n} bytes"),
        NetError::Refused(m) => m.clone(),
        other => other.to_string(),
    }
}

pub(crate) fn explain_body(body: &str, fallback: &str) -> String {
    let trimmed = body.trim();
    if trimmed.is_empty() {
        return fallback.to_string();
    }
    if let Ok(v) = serde_json::from_str::<Value>(trimmed) {
        return format_api_error(&v, fallback);
    }
    clip(trimmed, 240)
}

fn format_api_error(body: &Value, fallback: &str) -> String {
    if body.is_null() {
        return fallback.to_string();
    }
    if let Some(s) = body.as_str() {
        return if s.is_empty() { fallback.into() } else { clip(s, 240) };
    }
    let Some(obj) = body.as_object() else {
        return clip(&body.to_string(), 240);
    };
    let detail = obj.get("detail").or_else(|| obj.get("error")).or_else(|| obj.get("message"));
    if let Some(text) = detail_text(detail) {
        return clip(&text, 240);
    }
    if let Some(s) = obj.get("error").and_then(|v| v.as_str()).filter(|s| !s.trim().is_empty()) {
        return clip(s, 240);
    }
    let rendered = body.to_string();
    if rendered == "{}" || rendered == "[]" {
        fallback.to_string()
    } else {
        clip(&rendered, 240)
    }
}

fn detail_text(detail: Option<&Value>) -> Option<String> {
    let detail = detail?;
    if let Some(s) = detail.as_str() {
        let s = s.trim();
        return if s.is_empty() { None } else { Some(s.to_string()) };
    }
    if let Some(items) = detail.as_array() {
        let parts: Vec<String> = items
            .iter()
            .map(|item| {
                if let Some(s) = item.as_str() {
                    return s.to_string();
                }
                let Some(row) = item.as_object() else {
                    return item.to_string();
                };
                let loc = row.get("loc").and_then(|v| v.as_array()).map(|loc| {
                    loc.iter()
                        .filter_map(|p| p.as_str().map(str::to_string).or_else(|| p.as_i64().map(|n| n.to_string())))
                        .collect::<Vec<_>>()
                        .join(".")
                });
                let msg = row
                    .get("msg")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(|| Value::Object(row.clone()).to_string());
                match loc {
                    Some(loc) if !loc.is_empty() => format!("{loc}: {msg}"),
                    _ => msg,
                }
            })
            .filter(|s| !s.is_empty())
            .collect();
        if parts.is_empty() {
            return None;
        }
        return Some(parts.join("; "));
    }
    if detail.is_object() {
        return Some(detail.to_string());
    }
    None
}

pub(crate) fn parse_summary(v: &Value) -> Option<UsageSummary> {
    let obj = v.as_object()?;
    Some(UsageSummary {
        range_days: num(obj.get("range_days")).unwrap_or(0.0),
        totals: totals(obj.get("totals")).unwrap_or_default(),
        by_provider: groups(obj.get("by_provider")),
        by_model: groups(obj.get("by_model")),
    })
}

pub(crate) fn parse_series(v: &Value) -> Option<Vec<UsagePoint>> {
    let obj = v.as_object()?;
    let Some(points) = obj.get("points") else {
        return Some(Vec::new());
    };
    let points = points.as_array()?;
    Some(
        points
            .iter()
            .filter_map(|row| {
                let obj = row.as_object()?;
                Some(UsagePoint {
                    day: text(obj.get("day")),
                    group: text(obj.get("group")),
                    total_tokens: num(obj.get("total_tokens")).unwrap_or(0.0),
                    estimated_cost_usd: num(obj.get("estimated_cost_usd")).unwrap_or(0.0),
                    events: num(obj.get("events")).unwrap_or(0.0),
                })
            })
            .collect(),
    )
}

pub(crate) fn parse_continuity(v: &Value) -> Option<Continuity> {
    let obj = v.as_object()?;
    let token = obj.get("token").and_then(|t| t.as_object());
    let quality = obj.get("session_quality").and_then(|q| q.as_object());
    let pattern = obj.get("pattern").and_then(|p| p.as_object());
    let goal = obj.get("goal").and_then(|g| g.as_object());
    let health = obj.get("health").and_then(|h| h.as_object());
    let snapshot_obj = obj.get("context_snapshot").filter(|s| s.is_object());
    let remeasure = token.and_then(|t| t.get("last_remeasure")).filter(|r| r.is_object());
    Some(Continuity {
        harness_mode: token_str(obj.get("harness_mode"), "auto"),
        token_method: token.and_then(|t| nonempty(t.get("last_method"))).unwrap_or_else(|| "—".into()),
        token_estimate: token.and_then(|t| num(t.get("last_estimate"))).unwrap_or(0.0),
        token_fill: remeasure.and_then(|r| num(r.get("fill_pct"))),
        snapshot_fill: snapshot_obj.and_then(|s| num(s.get("fill_pct"))),
        turns: loose(quality.and_then(|q| q.get("turns"))),
        tokens_saved: loose(quality.and_then(|q| q.get("tokens_saved_by_compress"))),
        soft_nudge: loose(quality.and_then(|q| q.get("soft_nudge_count"))),
        strong_nudge: loose(quality.and_then(|q| q.get("strong_nudge_count"))),
        compress: loose(quality.and_then(|q| q.get("compress_count"))),
        stuck_rate: quality.and_then(|q| num(q.get("stuck_rate"))),
        re_explain_rate: quality.and_then(|q| num(q.get("re_explain_rate"))),
        tool_fail_max: loose(quality.and_then(|q| q.get("max_tool_fail_streak"))),
        step_count: pattern.and_then(|p| num(p.get("step_count"))).unwrap_or(0.0),
        success_rate: pattern.and_then(|p| num(p.get("success_rate"))),
        recent: pattern
            .and_then(|p| p.get("recent"))
            .and_then(|r| r.as_array())
            .map(|items| items.iter().filter_map(|s| s.as_str().map(str::to_string)).collect())
            .unwrap_or_default(),
        snapshot: snapshot_obj.map(|s| Snapshot {
            intent: text(s.get("intent")),
            policy_id: text(s.get("policy_id")),
            nudge: nonempty(s.get("nudge")),
        }),
        goals: goal
            .and_then(|g| g.get("open"))
            .and_then(|o| o.as_array())
            .map(|items| {
                items
                    .iter()
                    .filter_map(|s| s.as_str().map(str::to_string))
                    .take(4)
                    .collect()
            })
            .unwrap_or_default(),
        goals_stale: goal.and_then(|g| g.get("stale")).and_then(|s| s.as_bool()).unwrap_or(false),
        samples: health.and_then(|h| num(h.get("samples"))).unwrap_or(0.0),
        error_rate: health.and_then(|h| num(h.get("error_rate"))),
        avg_latency_ms: health.and_then(|h| num(h.get("avg_latency_ms"))),
        flaky: health.and_then(|h| h.get("flaky")).and_then(|f| f.as_bool()).unwrap_or(false),
        rate_limit_hits: health.and_then(|h| num(h.get("rate_limit_hits"))).unwrap_or(0.0),
    })
}

pub(crate) fn parse_token_status(v: &Value) -> Option<TokenStatus> {
    let obj = v.as_object()?;
    Some(TokenStatus {
        label: nonempty(obj.get("label")).unwrap_or_else(|| "NanoToken".into()),
        last_method: nonempty(obj.get("last_method")).unwrap_or_else(|| "—".into()),
        last_estimate: num(obj.get("last_estimate")).unwrap_or(0.0),
        active_provider: nonempty(obj.get("active_provider")),
        active_model: nonempty(obj.get("active_model")),
        worker: nonempty(obj.get("worker")),
        note: nonempty(obj.get("ip_note")),
    })
}

/// Sums series points by day, oldest first, and sizes bars the way the Tauri chart does.
pub(crate) fn day_bars(points: &[UsagePoint]) -> Vec<DayBar> {
    let mut days: Vec<(String, f64)> = Vec::new();
    for point in points {
        if point.day.is_empty() {
            continue;
        }
        if let Some((_, total)) = days.iter_mut().find(|(day, _)| day == &point.day) {
            *total += point.total_tokens.max(0.0);
        } else {
            days.push((point.day.clone(), point.total_tokens.max(0.0)));
        }
    }
    days.sort_by(|a, b| a.0.cmp(&b.0));
    let max = days.iter().map(|(_, v)| *v).fold(1.0_f64, f64::max).max(1.0);
    days.into_iter()
        .map(|(day, tokens)| {
            let height = if tokens > 0.0 {
                (((tokens / max) * 88.0).round() as f32).max(3.0)
            } else {
                0.0
            };
            DayBar {
                label: day.chars().skip(5).collect(),
                tip: format!("{day}: {}", format_tokens(tokens)),
                day,
                tokens,
                height,
            }
        })
        .collect()
}

/// `formatTokens` from `desktop/src/utils/tokenCost.ts`.
pub(crate) fn format_tokens(n: f64) -> String {
    if !n.is_finite() {
        return "0".into();
    }
    let v = n.max(0.0).round();
    if v < 1000.0 {
        return format!("{}", v as u64);
    }
    if v < 10_000.0 {
        return format!("{}k", format_fixed(v / 1000.0, 1));
    }
    format!("{}k", (v / 1000.0).round() as u64)
}

/// `formatCost` from `desktop/src/utils/tokenCost.ts`.
pub(crate) fn format_cost(usd: f64) -> String {
    if !usd.is_finite() || usd <= 0.0 {
        return "$0.00".into();
    }
    if usd < 0.01 {
        return format!("${}", format_fixed(usd, 4));
    }
    if usd < 1.0 {
        return format!("${}", format_fixed(usd, 3));
    }
    format!("${}", format_fixed(usd, 2))
}

pub(crate) fn format_number(n: f64) -> String {
    if !n.is_finite() {
        return "0".into();
    }
    if n.fract() == 0.0 && n.abs() < 1e15 {
        return format!("{}", n as i64);
    }
    format!("{n}")
}

/// CSS `text-transform: capitalize`: the first letter of each word, the rest unchanged.
pub(crate) fn capitalize_words(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut cap = true;
    for ch in s.chars() {
        if ch.is_whitespace() {
            cap = true;
            out.push(ch);
        } else if cap {
            out.extend(ch.to_uppercase());
            cap = false;
        } else {
            out.push(ch);
        }
    }
    out
}

pub(crate) fn fill_label(fill: Option<f64>) -> String {
    match fill.filter(|n| n.is_finite()) {
        Some(n) => format!("{}%", (n * 100.0).round() as i64),
        None => "—".into(),
    }
}

pub(crate) fn percent_fixed(rate: f64, digits: usize) -> String {
    format!("{}%", format_fixed(rate * 100.0, digits))
}

pub(crate) fn provider_label(provider: &str) -> String {
    if provider.is_empty() { "unknown".into() } else { provider.to_string() }
}

pub(crate) fn continuity_fill(c: &Continuity) -> Option<f64> {
    c.token_fill.or(c.snapshot_fill)
}

pub(crate) fn saved_tokens(c: &Continuity) -> String {
    format_tokens(c.tokens_saved.number().unwrap_or(0.0))
}

fn totals(v: Option<&Value>) -> Option<UsageTotals> {
    let obj = v?.as_object()?;
    Some(UsageTotals {
        prompt_tokens: num(obj.get("prompt_tokens")).unwrap_or(0.0),
        completion_tokens: num(obj.get("completion_tokens")).unwrap_or(0.0),
        total_tokens: num(obj.get("total_tokens")).unwrap_or(0.0),
        estimated_cost_usd: num(obj.get("estimated_cost_usd")).unwrap_or(0.0),
        events: num(obj.get("events")).unwrap_or(0.0),
    })
}

fn groups(v: Option<&Value>) -> Vec<UsageByGroup> {
    let Some(arr) = v.and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|row| {
            let obj = row.as_object()?;
            Some(UsageByGroup {
                provider: text(obj.get("provider")),
                model: text(obj.get("model")),
                totals: totals(Some(row)).unwrap_or_default(),
            })
        })
        .collect()
}

fn loose(v: Option<&Value>) -> Loose {
    let Some(v) = v else {
        return Loose::Empty;
    };
    if v.is_null() {
        return Loose::Empty;
    }
    if let Some(n) = num(Some(v)) {
        return Loose::Num(n);
    }
    if let Some(s) = v.as_str() {
        if s.is_empty() {
            return Loose::Empty;
        }
        if let Ok(n) = s.parse::<f64>() {
            if n.is_finite() {
                return Loose::Num(n);
            }
        }
        return Loose::Text(s.to_string());
    }
    Loose::Empty
}

fn num(v: Option<&Value>) -> Option<f64> {
    let v = v?;
    let n = if let Some(n) = v.as_f64() {
        n
    } else if let Some(n) = v.as_i64() {
        n as f64
    } else if let Some(n) = v.as_u64() {
        n as f64
    } else if let Some(s) = v.as_str() {
        s.parse().ok()?
    } else {
        return None;
    };
    n.is_finite().then_some(n)
}

fn text(v: Option<&Value>) -> String {
    v.and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn nonempty(v: Option<&Value>) -> Option<String> {
    let s = v?.as_str()?.trim();
    if s.is_empty() { None } else { Some(s.to_string()) }
}

fn token_str(v: Option<&Value>, fallback: &str) -> String {
    nonempty(v).unwrap_or_else(|| fallback.to_string())
}

/// `Number#toFixed` for a finite value: half away from zero, padded with zeros.
pub(crate) fn format_fixed(value: f64, digits: usize) -> String {
    if !value.is_finite() {
        return "0".into();
    }
    let digits = digits.min(6);
    let scale = 10f64.powi(digits as i32);
    let rounded = (value * scale).round();
    if !rounded.is_finite() {
        return "0".into();
    }
    let neg = rounded < 0.0;
    let whole = rounded.abs() as u64;
    let sign = if neg { "-" } else { "" };
    if digits == 0 {
        return format!("{sign}{whole}");
    }
    let scale_i = 10u64.pow(digits as u32);
    let ip = whole / scale_i;
    let frac = whole % scale_i;
    format!("{sign}{ip}.{frac:0digits$}")
}

fn clip(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        return s.to_string();
    }
    s.chars().take(max).collect()
}

fn query_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn attachment_name(header: Option<&str>, range_days: u32) -> String {
    if let Some(name) = header.and_then(disposition_filename) {
        return sanitize_filename(&name);
    }
    format!("remedy-usage-{range_days}d.csv")
}

fn disposition_filename(header: &str) -> Option<String> {
    for part in header.split(';') {
        let part = part.trim();
        let (name, value) = part.split_once('=')?;
        let name = name.trim();
        if name.eq_ignore_ascii_case("filename*") {
            let value = value.trim().trim_matches('"');
            let encoded = value.split_once("''").map(|(_, rest)| rest).unwrap_or(value);
            let decoded = percent_decode(encoded);
            if !decoded.is_empty() {
                return Some(decoded);
            }
        }
    }
    for part in header.split(';') {
        let part = part.trim();
        let (name, value) = part.split_once('=')?;
        if name.trim().eq_ignore_ascii_case("filename") {
            let value = value.trim().trim_matches('"').to_string();
            if !value.is_empty() {
                return Some(value);
            }
        }
    }
    None
}

fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let (Some(h), Some(l)) = (hex_val(bytes[i + 1]), hex_val(bytes[i + 2])) {
                out.push((h << 4) | l);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}

fn sanitize_filename(name: &str) -> String {
    let base = name.rsplit(['/', '\\']).next().unwrap_or(name).trim();
    let cleaned: String = base
        .chars()
        .filter(|c| *c != '\0' && *c != ':' && *c != '"' && *c != '*' && *c != '?' && *c != '<' && *c != '>' && *c != '|')
        .collect();
    let cleaned = cleaned.trim().trim_matches('.');
    if cleaned.is_empty() || cleaned == "." || cleaned == ".." {
        "remedy-usage.csv".into()
    } else {
        cleaned.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn token_and_cost_match_the_tauri_formatters() {
        assert_eq!(format_tokens(0.0), "0");
        assert_eq!(format_tokens(999.4), "999");
        assert_eq!(format_tokens(1500.0), "1.5k");
        assert_eq!(format_tokens(9999.0), "10.0k");
        assert_eq!(format_tokens(10_000.0), "10k");
        assert_eq!(format_tokens(18_420.0), "18k");
        assert_eq!(format_tokens(f64::NAN), "0");
        assert_eq!(format_cost(0.0), "$0.00");
        assert_eq!(format_cost(-1.0), "$0.00");
        assert_eq!(format_cost(f64::NAN), "$0.00");
        assert_eq!(format_cost(0.004), "$0.0040");
        assert_eq!(format_cost(0.5), "$0.500");
        assert_eq!(format_cost(1.2), "$1.20");
        assert_eq!(format_cost(1.28), "$1.28");
    }

    #[test]
    fn paths_match_usage_ts() {
        assert_eq!(summary_path(7, None), "usage/summary?range_days=7");
        assert_eq!(
            summary_path(7, Some("a b")),
            "usage/summary?range_days=7&session_id=a%20b"
        );
        assert_eq!(series_path(30, "provider"), "usage/series?range_days=30&group=provider");
        assert_eq!(series_path(30, "nope"), "usage/series?range_days=30&group=provider");
        assert_eq!(series_path(30, "model"), "usage/series?range_days=30&group=model");
        assert_eq!(continuity_path(None), "continuity/dashboard");
        assert_eq!(
            continuity_path(Some("sess/1")),
            "continuity/dashboard?session_id=sess%2F1"
        );
        assert_eq!(token_status_path(), "nanoswarm/token/status");
        assert_eq!(export_path(90), "usage/export?range_days=90&format=csv");
    }

    #[test]
    fn summary_series_and_continuity_parse() {
        let summary = parse_summary(&json!({
            "range_days": 7,
            "totals": {"total_tokens": 42, "estimated_cost_usd": 0.5, "events": 3,
                       "prompt_tokens": 20, "completion_tokens": 22},
            "by_provider": [{"provider": "openai", "total_tokens": 40, "estimated_cost_usd": 0.4, "events": 2}],
            "by_model": [{"provider": "openai", "model": "gpt-4o", "total_tokens": 40, "estimated_cost_usd": 0.4, "events": 2}]
        }))
        .unwrap();
        assert_eq!(summary.totals.total_tokens, 42.0);
        assert_eq!(summary.by_provider[0].provider, "openai");
        assert_eq!(summary.by_model[0].model, "gpt-4o");

        let points = parse_series(&json!({
            "points": [
                {"day": "2026-11-22", "group": "openai", "total_tokens": 10, "estimated_cost_usd": 0.1, "events": 1},
                {"day": "2026-11-22", "group": "anthropic", "total_tokens": 5, "estimated_cost_usd": 0.2, "events": 1}
            ]
        }))
        .unwrap();
        let bars = day_bars(&points);
        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].label, "11-22");
        assert_eq!(bars[0].tokens, 15.0);
        assert_eq!(bars[0].height, 88.0);
        assert_eq!(bars[0].tip, "2026-11-22: 15");

        let continuity = parse_continuity(&json!({
            "harness_mode": "auto",
            "session_quality": {"turns": 4, "tokens_saved_by_compress": 1500, "stuck_rate": 0.1, "soft_nudge_count": 2},
            "pattern": {"step_count": 3, "success_rate": 0.5, "recent": ["read", "edit"]},
            "goal": {"open": ["ship the port"], "stale": false},
            "health": {"samples": 8, "error_rate": 0.0, "avg_latency_ms": 120.4, "flaky": false, "rate_limit_hits": 0},
            "token": {"last_method": "bpe", "last_estimate": 1000, "last_remeasure": {"fill_pct": 0.42}},
            "context_snapshot": {"intent": "port", "policy_id": "house", "fill_pct": 0.1},
            "swarm": {"event_count": 0}
        }))
        .unwrap();
        assert_eq!(continuity.harness_mode, "auto");
        assert_eq!(continuity_fill(&continuity), Some(0.42));
        assert_eq!(fill_label(continuity_fill(&continuity)), "42%");
        assert_eq!(continuity.recent, vec!["read".to_string(), "edit".to_string()]);
        assert_eq!(saved_tokens(&continuity), "1.5k");
        assert!(continuity.snapshot.is_some());
    }

    #[test]
    fn empty_days_and_short_bars() {
        assert!(day_bars(&[]).is_empty());
        let bars = day_bars(&[
            UsagePoint { day: "2026-11-21".into(), group: "a".into(), total_tokens: 1.0, estimated_cost_usd: 0.0, events: 1.0 },
            UsagePoint { day: "2026-11-20".into(), group: "a".into(), total_tokens: 0.0, estimated_cost_usd: 0.0, events: 0.0 },
            UsagePoint { day: "2026-11-21".into(), group: "b".into(), total_tokens: 1.0, estimated_cost_usd: 0.0, events: 1.0 },
        ]);
        assert_eq!(bars[0].day, "2026-11-20");
        assert_eq!(bars[0].height, 0.0);
        assert_eq!(bars[1].tokens, 2.0);
        assert!(bars[1].height >= 3.0);
    }

    #[test]
    fn api_errors_flatten_fastapi_detail() {
        let body = r#"{"detail":[{"loc":["query","range_days"],"msg":"value is not a valid integer"}]}"#;
        assert_eq!(
            explain_body(body, "Export failed"),
            "query.range_days: value is not a valid integer"
        );
        assert_eq!(explain_body("plain failure", "Export failed"), "plain failure");
        assert_eq!(explain_body("{}", "Export failed"), "Export failed");
    }

    #[test]
    fn export_name_comes_from_content_disposition() {
        assert_eq!(
            attachment_name(Some("attachment; filename=\"remedy-usage-7d.csv\""), 7),
            "remedy-usage-7d.csv"
        );
        assert_eq!(attachment_name(None, 30), "remedy-usage-30d.csv");
        assert_eq!(sanitize_filename("../secret.csv"), "secret.csv");
        assert_eq!(sanitize_filename("a/b\\c.csv"), "c.csv");
    }

    #[test]
    fn token_status_reads_the_nanoswarm_payload() {
        let status = parse_token_status(&json!({
            "bot": "token",
            "label": "NanoToken",
            "last_method": "unavailable",
            "last_estimate": 0,
            "active_provider": "local",
            "active_model": null,
            "worker": "not_attached",
            "ip_note": "Remedy-owned BBPE packs only"
        }))
        .unwrap();
        assert_eq!(status.label, "NanoToken");
        assert_eq!(status.worker.as_deref(), Some("not_attached"));
        assert_eq!(status.active_provider.as_deref(), Some("local"));
        assert_eq!(status.active_model, None);
        assert_eq!(status.note.as_deref(), Some("Remedy-owned BBPE packs only"));
    }
}
