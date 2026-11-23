//! Snapshot, ledger, and hive shapes the panel draws.
//!
//! The runtime sends loose JSON (`map[string]any` on the Go side). Missing
//! fields follow the Tauri panel: an em dash, or the same badge fallback.

use serde_json::{Number, Value};

/// Green, amber, red, or quiet. Order matches the Tauri `Badge`:
/// `ok === true`, then `warn`, then `ok === false`, then muted.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Tone {
    Ok,
    Warn,
    Danger,
    Muted,
}

/// A pill the Tauri `Badge` would draw.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Badge {
    pub ok: Option<bool>,
    pub warn: bool,
    pub label: String,
}

/// One label and the figure next to it. Empty values are already an em dash.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Pair {
    pub label: &'static str,
    pub value: String,
}

/// A labeled 0–100 meter. `pct` is unclamped; the view clamps it.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Meter {
    pub label: String,
    pub pct: f64,
}

/// A titled fact card: server, RMB, vision, computer.
#[derive(Clone, Debug)]
pub(crate) struct Fact {
    pub subtitle: String,
    pub badge: Badge,
    pub rows: Vec<Pair>,
    pub meter: Option<Meter>,
    pub hint: Option<String>,
    pub error: Option<String>,
}

/// One GPU under the hardware card.
#[derive(Clone, Debug)]
pub(crate) struct GpuBlock {
    pub title: String,
    pub meter: Option<Meter>,
}

/// Hardware card plus GPU blocks the fact card does not carry.
#[derive(Clone, Debug)]
pub(crate) struct Hardware {
    pub fact: Fact,
    pub gpus: Vec<GpuBlock>,
    pub gpu_error: Option<String>,
}

/// One cloud or local provider row.
#[derive(Clone, Debug)]
pub(crate) struct ProviderLine {
    pub id: String,
    pub name: String,
    pub connected: bool,
    pub detail: String,
    pub reason: String,
}

/// One usage row, tokens and cost already formatted.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct UsageLine {
    pub provider: String,
    pub tokens: String,
    pub cost: String,
}

/// Provider card. `probes` is the joined line, present only when the payload has some.
#[derive(Clone, Debug)]
pub(crate) struct Providers {
    pub subtitle: Option<String>,
    pub badge: Badge,
    pub active: String,
    pub remote: Vec<ProviderLine>,
    pub local: Vec<ProviderLine>,
    pub usage: Vec<UsageLine>,
    pub probes: Option<String>,
}

/// One issue row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct IssueLine {
    pub severity: String,
    pub area: String,
    pub message: String,
    pub hint: Option<String>,
}

/// One self-inject round, in server order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct RoundLine {
    pub key: String,
    pub status: Badge,
    pub live: Option<Badge>,
    pub summary: String,
    pub meta: String,
}

/// Everything the diagnostics body draws, plus the JSON Copy uses.
#[derive(Clone, Debug)]
pub(crate) struct DiagnosticsView {
    pub overall: String,
    pub overall_label: String,
    pub collect_ms: Option<String>,
    pub checked_at: Option<String>,
    pub issues: Vec<IssueLine>,
    pub issue_subtitle: String,
    pub server: Fact,
    pub rmb: Fact,
    pub hardware: Hardware,
    pub providers: Providers,
    pub vision: Fact,
    pub computer: Fact,
    /// `JSON.stringify(snapshot, null, 2)`. Key order follows `serde_json` (sorted).
    pub json: String,
}

/// One daughter. Fields the inspector and the spawn form actually show.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HiveRow {
    pub id: String,
    pub cadence: String,
    pub status: String,
    pub goal: String,
    pub outcome: String,
    pub blockers: Vec<String>,
}

/// `GET /hive/roster`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HiveRoster {
    pub rows: Vec<HiveRow>,
    pub live_posts: i64,
    pub live_foragers: i64,
}

/// `POST /hive/spawn`, `/assign`, `/retire` — HTTP 200, `ok` says whether it took.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct HiveOp {
    pub ok: bool,
    pub error: Option<String>,
}

/// Pronoun line the inspector hard-codes when i18n is not mounted here.
pub(crate) const HIVE_INTRO: &str = "Daughters Remedy hired. They report to them, not you.";

/// Tone of a Tauri `Badge`, including the harsh cases (Off, Starting, Idle are red).
pub(crate) fn badge_tone(ok: Option<bool>, warn: bool) -> Tone {
    if ok == Some(true) {
        Tone::Ok
    } else if warn {
        Tone::Warn
    } else if ok == Some(false) {
        Tone::Danger
    } else {
        Tone::Muted
    }
}

/// Header pill for `overall`.
pub(crate) fn overall_tone(overall: &str) -> Tone {
    match overall {
        "ok" => Tone::Ok,
        "degraded" => Tone::Warn,
        "error" => Tone::Danger,
        _ => Tone::Muted,
    }
}

pub(crate) fn overall_label(overall: &str) -> &'static str {
    match overall {
        "ok" => "Healthy",
        "degraded" => "Degraded",
        "error" => "Issues",
        _ => "Unknown",
    }
}

/// `formatTokens` from `desktop/src/utils/tokenCost.ts`.
pub(crate) fn format_tokens(n: f64) -> String {
    let v = if n.is_finite() {
        n.round().max(0.0)
    } else {
        0.0
    };
    let v = v as u64;
    if v < 1000 {
        return v.to_string();
    }
    if v < 10_000 {
        return format!("{:.1}k", v as f64 / 1000.0);
    }
    format!("{}k", ((v as f64) / 1000.0).round() as u64)
}

/// `formatCost` from `desktop/src/utils/tokenCost.ts`.
pub(crate) fn format_cost(usd: f64) -> String {
    if !usd.is_finite() || usd <= 0.0 {
        return "$0.00".into();
    }
    if usd < 0.01 {
        format!("${usd:.4}")
    } else if usd < 1.0 {
        format!("${usd:.3}")
    } else {
        format!("${usd:.2}")
    }
}

/// `hiveRowLabel`: cadence, status, and the goal clipped at 72 characters.
pub(crate) fn hive_row_label(row: &HiveRow) -> String {
    let trimmed = row.goal.trim();
    let goal = if trimmed.is_empty() {
        "untitled job"
    } else {
        trimmed
    };
    let short = if goal.chars().count() > 72 {
        let mut s: String = goal.chars().take(71).collect();
        s.push('…');
        s
    } else {
        goal.to_string()
    };
    format!("{} · {} · {short}", row.cadence, row.status)
}

/// Not retired and not cancelled.
pub(crate) fn hive_is_live(row: &HiveRow) -> bool {
    row.status != "retired" && row.status != "cancelled"
}

/// File name after the last `/` or `\`, as the GGUF row does.
pub(crate) fn file_name(path: &str) -> String {
    match path.rfind(['/', '\\']) {
        Some(i) => path[i + 1..].to_string(),
        None => path.to_string(),
    }
}

/// `checked_at.replace('T', ' ').replace('Z', ' UTC')` — first match only.
pub(crate) fn stamp(s: &str) -> String {
    replace_first(&replace_first(s, 'T', " "), 'Z', " UTC")
}

/// `finished_utc.replace('T', ' ').slice(0, 16)`.
pub(crate) fn short_stamp(s: &str) -> String {
    replace_first(s, 'T', " ").chars().take(16).collect()
}

/// Parse `GET /diagnostics` into the cards. Never fails: missing objects become dashes.
pub(crate) fn view_from(v: &Value) -> DiagnosticsView {
    let overall = string_field(v, "overall").unwrap_or_default();
    let remedy = obj(v, "remedy");
    let rmb = obj(v, "rmb");
    let hw = obj(v, "hardware");
    let vision = obj(v, "vision");
    let computer = obj(v, "computer");
    let providers = obj(v, "providers");
    let issues = issues_from(v);
    DiagnosticsView {
        overall_label: overall_label(&overall).to_string(),
        collect_ms: val(v, "collect_ms").and_then(as_f64).map(fmt_num),
        checked_at: string_field(v, "checked_at").map(|s| stamp(&s)),
        issue_subtitle: format!("{} findings", issues.len()),
        issues,
        server: server_fact(remedy),
        rmb: rmb_fact(rmb),
        hardware: hardware_from(hw),
        providers: providers_from(providers),
        vision: vision_fact(vision),
        computer: computer_fact(computer),
        json: serde_json::to_string_pretty(v).unwrap_or_else(|_| "{}".into()),
        overall,
    }
}

/// Parse `GET /self-inject/rounds`. Keeps server order. A bad payload is no rounds.
pub(crate) fn rounds_from(v: &Value) -> Vec<RoundLine> {
    let Some(arr) = v.get("rounds").and_then(Value::as_array) else {
        return Vec::new();
    };
    arr.iter()
        .enumerate()
        .map(|(i, round)| round_line(round, i))
        .collect()
}

/// Parse `GET /hive/roster`. An `{ok:false, error}` body is an error.
pub(crate) fn roster_from(v: &Value) -> Result<HiveRoster, String> {
    if v.get("ok").and_then(Value::as_bool) == Some(false) {
        return Err(string_field(v, "error").unwrap_or_else(|| "roster failed".into()));
    }
    let rows = v
        .get("daughters")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .enumerate()
                .filter(|(_, row)| row.is_object())
                .map(|(i, row)| HiveRow {
                    id: string_field(row, "id").unwrap_or_else(|| format!("hive-{i}")),
                    cadence: string_field(row, "cadence").unwrap_or_default(),
                    status: string_field(row, "status").unwrap_or_default(),
                    goal: row
                        .get("goal")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    outcome: row
                        .get("outcome")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                    blockers: blockers_of(row),
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(HiveRoster {
        rows,
        live_posts: v.get("live_posts").and_then(as_i64).unwrap_or(0),
        live_foragers: v.get("live_foragers").and_then(as_i64).unwrap_or(0),
    })
}

pub(crate) fn hive_op(v: &Value) -> HiveOp {
    HiveOp {
        ok: v.get("ok").and_then(Value::as_bool).unwrap_or(false),
        error: string_field(v, "error"),
    }
}

pub(crate) fn sample_snapshot() -> Value {
    serde_json::json!({
        "ok": false,
        "overall": "degraded",
        "checked_at": "2026-11-22T21:04:05Z",
        "collect_ms": 128,
        "issues": [{
            "severity": "warn",
            "area": "rmb",
            "message": "RMB is still starting.",
            "hint": "Give the weights a moment."
        }],
        "remedy": {
            "version": "1.0.0",
            "uptime": "2h 14m",
            "api": {"host": "127.0.0.1", "port": 7400, "base_url": "http://127.0.0.1:7400", "listening": true},
            "process": {"pid": 4242, "rss_mb": 180.5, "cpu_pct": 3.2, "threads": 12},
            "memory_entries": 40,
            "chat_sessions_count": 3,
            "skills_count": 7,
            "active_provider": "grok",
            "active_model": "grok-4",
            "home_dir": "C:\\Users\\Ahmi\\.remedy",
            "home_disk": {"free_gb": 120.5, "used_pct": 62}
        },
        "rmb": {
            "enabled": true,
            "running": true,
            "ready": false,
            "model_name": "Qwen2.5",
            "model_path": "C:\\models\\qwen2.5-7b.gguf",
            "model_present": true,
            "runtime_present": true,
            "ctx_size": 8192,
            "n_gpu_layers": 20,
            "profile": "balanced",
            "base_url": "http://127.0.0.1:8787",
            "nvidia": true,
            "latency_ms": 40,
            "not_ready_hint": "Loading weights.",
            "vision_suspended": true
        },
        "hardware": {
            "platform": "Windows-11",
            "system": "Windows",
            "machine": "AMD64",
            "python": "3.12.6",
            "hostname": "hub",
            "cpu": {"count_logical": 16, "percent": 22, "brand": "Ryzen"},
            "memory": {"total_gb": 64, "available_gb": 12, "used_pct": 82},
            "gpu": {
                "nvidia": true,
                "gpus": [{
                    "name": "RTX 4090",
                    "memory_total_mb": 24576,
                    "memory_used_mb": 23000,
                    "util_pct": 12,
                    "temp_c": 48
                }]
            }
        },
        "providers": {
            "active": {"provider": "grok", "model": "grok-4"},
            "connected_count": 2,
            "remote_connected_count": 1,
            "local_connected_count": 1,
            "providers": [
                {"id": "grok", "label": "Grok", "connected": true, "local": false, "last_model": "grok-4"},
                {"id": "ollama", "label": "Ollama", "connected": true, "local": true, "last_model": "qwen2.5"},
                {"id": "openai", "label": "OpenAI", "connected": false, "local": false, "reason": "no key"}
            ],
            "usage_7d": [{"provider": "grok", "total_tokens": 12345, "estimated_cost_usd": 1.25}],
            "probes": [
                {"id": "ollama", "ok": true, "latency_ms": 18},
                {"id": "lmstudio", "ok": false, "latency_ms": null}
            ]
        },
        "vision": {
            "enabled": true,
            "running": false,
            "ready": false,
            "model_id": "SmolVLM",
            "port": 8790,
            "base_url": "http://127.0.0.1:8790",
            "suspended_for_rmb": true,
            "not_ready_hint": "Paused while RMB loads."
        },
        "computer": {
            "host_connected": true,
            "pending_jobs": 1,
            "jobs_root": "C:\\Users\\Ahmi\\.remedy\\jobs"
        }
    })
}

pub(crate) fn sample_rounds() -> Value {
    serde_json::json!({
        "serve_started_utc": "2026-11-22T20:00:00Z",
        "rounds": [
            {
                "round_id": "r-live",
                "status": "applied",
                "tree": "desktop",
                "summary": "Diagnostics panel wired to the runtime.",
                "finished_utc": "2026-11-22T21:01:00Z",
                "live_state": "live"
            },
            {
                "round_id": "r-back",
                "status": "rolled_back",
                "tree": "python",
                "summary": "Gate failed, serve kept the previous tree.",
                "finished_utc": "2026-11-22T18:12:00Z",
                "live_state": ""
            },
            {
                "round_id": "r-red",
                "status": "red",
                "tree": "both",
                "summary": "Typecheck failed.",
                "finished_utc": "2026-11-22T16:00:00Z"
            }
        ]
    })
}

pub(crate) fn sample_roster() -> HiveRoster {
    roster_from(&serde_json::json!({
        "daughters": [
            {
                "id": "post-1",
                "cadence": "post",
                "status": "running",
                "goal": "Watch the build queue and report when a job stalls overnight",
                "outcome": "Last check was clean.",
                "blockers": []
            },
            {
                "id": "forage-1",
                "cadence": "forager",
                "status": "pending",
                "goal": "Collect overnight logs",
                "outcome": "",
                "blockers": ["disk full", "retry later", "ignored third"]
            },
            {
                "id": "old-1",
                "cadence": "forager",
                "status": "retired",
                "goal": "Old sweep",
                "outcome": "finished",
                "blockers": []
            }
        ],
        "live_posts": 1,
        "live_foragers": 1,
        "count": 3
    }))
    .unwrap_or(HiveRoster {
        rows: Vec::new(),
        live_posts: 0,
        live_foragers: 0,
    })
}

fn server_fact(remedy: Option<&Value>) -> Fact {
    let api = remedy.and_then(|r| obj(r, "api"));
    let process = remedy.and_then(|r| obj(r, "process"));
    let disk = remedy.and_then(|r| obj(r, "home_disk"));
    let listening_down = api
        .and_then(|a| val(a, "listening"))
        .and_then(Value::as_bool)
        == Some(false);
    let free = disk.and_then(|d| val(d, "free_gb")).and_then(as_f64);
    let home_disk = match free {
        Some(free) => {
            let used = disk
                .and_then(|d| val(d, "used_pct"))
                .and_then(as_f64)
                .map(fmt_num)
                .unwrap_or_else(|| "—".into());
            format!("{} GB ({used}% used)", fmt_num(free))
        }
        None => "—".into(),
    };
    Fact {
        subtitle: api
            .and_then(|a| string_field(a, "base_url"))
            .unwrap_or_else(|| "local API".into()),
        badge: Badge {
            ok: Some(!listening_down),
            warn: false,
            label: if listening_down { "Down" } else { "Up" }.into(),
        },
        rows: vec![
            pair("Version", remedy.and_then(|r| string_field(r, "version"))),
            pair("Uptime", remedy.and_then(|r| string_field(r, "uptime"))),
            pair("PID", process.and_then(|p| num_field(p, "pid"))),
            pair("RSS", process.and_then(|p| suffixed(p, "rss_mb", " MB"))),
            pair("CPU", process.and_then(|p| suffixed(p, "cpu_pct", "%"))),
            pair(
                "Memory entries",
                remedy.and_then(|r| num_field(r, "memory_entries")),
            ),
            pair(
                "Chat sessions",
                remedy.and_then(|r| num_field(r, "chat_sessions_count")),
            ),
            pair("Skills", remedy.and_then(|r| num_field(r, "skills_count"))),
            pair("Active", Some(active_line(remedy))),
            pair("Home", remedy.and_then(|r| string_field(r, "home_dir"))),
            pair("Home disk free", Some(home_disk)),
        ],
        meter: meter_of(
            disk.and_then(|d| val(d, "used_pct")).and_then(as_f64),
            "Remedy disk",
        ),
        hint: None,
        error: None,
    }
}

fn rmb_fact(rmb: Option<&Value>) -> Fact {
    let running = flag(rmb, "running");
    let ready = flag(rmb, "ready");
    let enabled = flag(rmb, "enabled");
    let label = if running {
        if ready { "Running" } else { "Starting" }
    } else if enabled {
        "Stopped"
    } else {
        "Off"
    };
    let gguf = match rmb.and_then(|r| string_field(r, "model_path")) {
        Some(path) => file_name(&path),
        None => {
            if flag(rmb, "model_present") {
                "present".into()
            } else {
                "missing".into()
            }
        }
    };
    Fact {
        subtitle: rmb
            .and_then(|r| string_field(r, "base_url"))
            .unwrap_or_else(|| "llama-server :8787".into()),
        badge: Badge {
            ok: Some(running && ready),
            warn: enabled && !running,
            label: label.into(),
        },
        rows: vec![
            pair(
                "Model",
                rmb.and_then(|r| {
                    string_field(r, "model_name").or_else(|| string_field(r, "model_id"))
                }),
            ),
            pair("GGUF", Some(gguf)),
            pair("Context", rmb.and_then(|r| num_field(r, "ctx_size"))),
            pair("Profile", rmb.and_then(|r| string_field(r, "profile"))),
            pair("GPU layers", rmb.and_then(|r| num_field(r, "n_gpu_layers"))),
            pair(
                "Latency",
                rmb.and_then(|r| suffixed(r, "latency_ms", " ms")),
            ),
            pair("NVIDIA", Some(yes_no(flag(rmb, "nvidia")))),
            pair(
                "Runtime",
                Some(
                    if flag(rmb, "runtime_present") {
                        "ok"
                    } else {
                        "missing"
                    }
                    .into(),
                ),
            ),
            pair(
                "Vision suspended",
                Some(yes_no(flag(rmb, "vision_suspended"))),
            ),
        ],
        meter: None,
        hint: rmb.and_then(|r| string_field(r, "not_ready_hint")),
        error: rmb.and_then(|r| string_field(r, "error")),
    }
}

fn hardware_from(hw: Option<&Value>) -> Hardware {
    let cpu = hw.and_then(|h| obj(h, "cpu"));
    let memory = hw.and_then(|h| obj(h, "memory"));
    let gpu = hw.and_then(|h| obj(h, "gpu"));
    let nvidia = flag(gpu, "nvidia");
    let cpu_label = cpu
        .and_then(|c| string_field(c, "brand"))
        .unwrap_or_else(|| {
            format!(
                "{} cores",
                cpu.and_then(|c| num_field(c, "count_logical"))
                    .unwrap_or_else(|| "—".into())
            )
        });
    let ram = memory
        .and_then(|m| val(m, "total_gb"))
        .and_then(as_f64)
        .map(|total| {
            let free = memory
                .and_then(|m| val(m, "available_gb"))
                .and_then(as_f64)
                .map(fmt_num)
                .unwrap_or_else(|| "—".into());
            format!("{free} / {} GB free", fmt_num(total))
        });
    let gpus = gpu
        .and_then(|g| g.get("gpus"))
        .and_then(Value::as_array)
        .map(|arr| arr.iter().map(gpu_block).collect())
        .unwrap_or_default();
    Hardware {
        fact: Fact {
            subtitle: hw
                .and_then(|h| string_field(h, "hostname").or_else(|| string_field(h, "platform")))
                .unwrap_or_default(),
            badge: Badge {
                ok: if nvidia { Some(true) } else { None },
                warn: false,
                label: if nvidia {
                    "NVIDIA".into()
                } else {
                    hw.and_then(|h| string_field(h, "system"))
                        .unwrap_or_else(|| "CPU".into())
                },
            },
            rows: vec![
                pair("CPU", Some(cpu_label)),
                pair("CPU load", cpu.and_then(|c| suffixed(c, "percent", "%"))),
                pair("RAM", ram),
                pair("Python", hw.and_then(|h| string_field(h, "python"))),
                pair("Machine", hw.and_then(|h| string_field(h, "machine"))),
            ],
            meter: meter_of(
                memory.and_then(|m| val(m, "used_pct")).and_then(as_f64),
                "System RAM",
            ),
            hint: None,
            error: None,
        },
        gpu_error: gpu
            .and_then(|g| string_field(g, "error"))
            .map(|e| format!("GPU: {e}")),
        gpus,
    }
}

fn providers_from(providers: Option<&Value>) -> Providers {
    let list = providers
        .and_then(|p| p.get("providers"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let mut remote = Vec::new();
    let mut local = Vec::new();
    for (i, p) in list.iter().enumerate() {
        let local_flag = p.get("local").and_then(Value::as_bool).unwrap_or(false);
        let connected = p.get("connected").and_then(Value::as_bool).unwrap_or(false);
        let id = string_field(p, "id").unwrap_or_else(|| format!("provider-{i}"));
        let name = string_field(p, "label").unwrap_or_else(|| id.clone());
        let reason = string_field(p, "reason").unwrap_or_default();
        let last = string_field(p, "last_model");
        let detail = if connected {
            last.or(if reason.is_empty() {
                None
            } else {
                Some(reason.clone())
            })
            .unwrap_or_else(|| "ok".into())
        } else if reason.is_empty() {
            "off".into()
        } else {
            reason.clone()
        };
        let line = ProviderLine {
            id,
            name,
            connected,
            detail,
            reason,
        };
        if !local_flag {
            remote.push(line);
        } else if connected {
            local.push(line);
        }
    }
    let n = providers
        .and_then(|p| p.get("connected_count"))
        .and_then(as_i64)
        .unwrap_or(0);
    let usage = providers
        .and_then(|p| p.get("usage_7d"))
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .map(|u| UsageLine {
                    provider: string_field(u, "provider").unwrap_or_else(|| "—".into()),
                    tokens: format_tokens(u.get("total_tokens").and_then(as_f64).unwrap_or(0.0)),
                    cost: format_cost(u.get("estimated_cost_usd").and_then(as_f64).unwrap_or(0.0)),
                })
                .collect()
        })
        .unwrap_or_default();
    let active = providers.and_then(|p| obj(p, "active"));
    Providers {
        subtitle: providers.map(|p| {
            let remote_n = p
                .get("remote_connected_count")
                .and_then(as_i64)
                .unwrap_or(0);
            let local_n = p.get("local_connected_count").and_then(as_i64).unwrap_or(0);
            format!("{remote_n} remote · {local_n} local connected")
        }),
        badge: Badge {
            ok: Some(n > 0),
            warn: false,
            label: format!("{n} ready"),
        },
        active: format!(
            "{} / {}",
            active
                .and_then(|a| string_field(a, "provider"))
                .unwrap_or_else(|| "—".into()),
            active
                .and_then(|a| string_field(a, "model"))
                .unwrap_or_else(|| "—".into())
        ),
        probes: providers
            .and_then(|p| p.get("probes"))
            .and_then(Value::as_array)
            .and_then(|probes| probe_line(probes)),
        remote,
        local,
        usage,
    }
}

fn vision_fact(vision: Option<&Value>) -> Fact {
    let running = flag(vision, "running");
    let suspended = flag(vision, "suspended_for_rmb");
    let label = if suspended {
        "Suspended"
    } else if running {
        "Running"
    } else {
        "Idle"
    };
    Fact {
        subtitle: vision
            .and_then(|v| string_field(v, "base_url"))
            .unwrap_or_else(|| "local vision".into()),
        badge: Badge {
            ok: Some(running),
            warn: suspended,
            label: label.into(),
        },
        rows: vec![
            pair("Model", vision.and_then(|v| string_field(v, "model_id"))),
            pair("Enabled", Some(yes_no(flag(vision, "enabled")))),
            pair("Ready", Some(yes_no(flag(vision, "ready")))),
            pair("Port", vision.and_then(|v| num_field(v, "port"))),
        ],
        meter: None,
        hint: vision.and_then(|v| string_field(v, "not_ready_hint")),
        error: None,
    }
}

fn computer_fact(computer: Option<&Value>) -> Fact {
    let connected = flag(computer, "host_connected");
    Fact {
        subtitle: "Desktop poller / browser rail".into(),
        badge: Badge {
            ok: Some(connected),
            warn: false,
            label: if connected { "Connected" } else { "Offline" }.into(),
        },
        rows: vec![
            pair(
                "Pending jobs",
                computer.and_then(|c| num_field(c, "pending_jobs")),
            ),
            pair(
                "Jobs root",
                computer.and_then(|c| string_field(c, "jobs_root")),
            ),
        ],
        meter: None,
        hint: None,
        error: computer.and_then(|c| string_field(c, "error")),
    }
}

fn issues_from(v: &Value) -> Vec<IssueLine> {
    v.get("issues")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter(|i| i.is_object())
                .map(|i| IssueLine {
                    severity: string_field(i, "severity").unwrap_or_else(|| "info".into()),
                    area: string_field(i, "area").unwrap_or_default(),
                    message: string_field(i, "message").unwrap_or_default(),
                    hint: string_field(i, "hint"),
                })
                .collect()
        })
        .unwrap_or_default()
}

fn round_line(round: &Value, index: usize) -> RoundLine {
    let status = string_field(round, "status").unwrap_or_else(|| "draft".into());
    let applied = status == "applied";
    let live_state = round
        .get("live_state")
        .and_then(Value::as_str)
        .unwrap_or("");
    let tree = string_field(round, "tree").unwrap_or_else(|| "—".into());
    let meta = match string_field(round, "finished_utc") {
        Some(fin) => format!("{tree} · {}", short_stamp(&fin)),
        None => tree,
    };
    RoundLine {
        key: string_field(round, "round_id")
            .or_else(|| string_field(round, "started_utc"))
            .unwrap_or_else(|| format!("round-{index}")),
        status: status_badge(&status),
        live: if applied {
            Some(live_badge(live_state))
        } else {
            None
        },
        summary: string_field(round, "summary").unwrap_or_else(|| "(no summary)".into()),
        meta,
    }
}

fn status_badge(status: &str) -> Badge {
    let applied = status == "applied";
    let rolled = status == "rolled_back";
    let red = status == "red";
    Badge {
        ok: if applied {
            Some(true)
        } else if rolled || red {
            Some(false)
        } else {
            None
        },
        warn: rolled,
        label: status.to_string(),
    }
}

fn live_badge(live: &str) -> Badge {
    let label = match live {
        "live" => "Live",
        "awaiting_restart" => "Awaiting restart",
        "not_loaded" => "Not loaded",
        _ => "Applied",
    };
    Badge {
        ok: if live == "live" { Some(true) } else { None },
        warn: live != "live",
        label: label.into(),
    }
}

fn gpu_block(g: &Value) -> GpuBlock {
    let tot = g.get("memory_total_mb").and_then(as_f64).unwrap_or(0.0);
    let used = g.get("memory_used_mb").and_then(as_f64).unwrap_or(0.0);
    let mut title = string_field(g, "name").unwrap_or_default();
    if let Some(temp) = val(g, "temp_c").and_then(as_f64) {
        title.push_str(&format!(" · {}°C", fmt_num(temp)));
    }
    if let Some(util) = val(g, "util_pct").and_then(as_f64) {
        title.push_str(&format!(" · {}% util", fmt_num(util)));
    }
    let meter = if tot > 0.0 {
        Some(Meter {
            label: format!("VRAM {}/{} MB", fixed0(used), fixed0(tot)),
            pct: (100.0 * used) / tot,
        })
    } else {
        None
    };
    GpuBlock { title, meter }
}

fn probe_line(probes: &[Value]) -> Option<String> {
    if probes.is_empty() {
        return None;
    }
    let parts: Vec<String> = probes
        .iter()
        .map(|p| {
            let id = string_field(p, "id").unwrap_or_else(|| "undefined".into());
            let ok = p.get("ok").and_then(Value::as_bool).unwrap_or(false);
            if ok {
                let ms = match val(p, "latency_ms") {
                    Some(Value::Number(n)) => num_string(n),
                    Some(Value::String(s)) => s.clone(),
                    _ => "null".into(),
                };
                format!("{id}: {ms} ms")
            } else {
                format!("{id}: fail")
            }
        })
        .collect();
    Some(format!("Probes: {}", parts.join(" · ")))
}

fn blockers_of(row: &Value) -> Vec<String> {
    row.get("blockers")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn active_line(remedy: Option<&Value>) -> String {
    let provider = remedy
        .and_then(|r| string_field(r, "active_provider"))
        .unwrap_or_else(|| "—".into());
    let model = remedy
        .and_then(|r| string_field(r, "active_model"))
        .unwrap_or_else(|| "—".into());
    format!("{provider}/{model}")
}

fn pair(label: &'static str, value: Option<String>) -> Pair {
    Pair {
        label,
        value: value
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "—".into()),
    }
}

fn meter_of(pct: Option<f64>, label: &str) -> Option<Meter> {
    pct.filter(|n| n.is_finite()).map(|pct| Meter {
        label: label.into(),
        pct,
    })
}

fn yes_no(on: bool) -> String {
    if on { "yes" } else { "no" }.into()
}

fn flag(v: Option<&Value>, key: &str) -> bool {
    v.and_then(|o| o.get(key))
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

fn obj<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.get(key).filter(|x| x.is_object())
}

fn val<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.get(key).filter(|x| !x.is_null())
}

fn string_field(v: &Value, key: &str) -> Option<String> {
    val(v, key).and_then(as_text)
}

fn num_field(v: &Value, key: &str) -> Option<String> {
    val(v, key).and_then(as_f64).map(fmt_num)
}

fn suffixed(v: &Value, key: &str, suffix: &str) -> Option<String> {
    num_field(v, key).map(|n| format!("{n}{suffix}"))
}

fn as_text(v: &Value) -> Option<String> {
    match v {
        Value::String(s) => {
            let t = s.trim();
            if t.is_empty() {
                None
            } else {
                Some(t.to_string())
            }
        }
        Value::Number(n) => Some(num_string(n)),
        Value::Bool(b) => Some(if *b { "true" } else { "false" }.into()),
        _ => None,
    }
}

fn as_f64(v: &Value) -> Option<f64> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .or_else(|| v.as_i64().map(|i| i as f64))
        .or_else(|| v.as_u64().map(|u| u as f64))
}

fn as_i64(v: &Value) -> Option<i64> {
    v.as_i64()
        .or_else(|| v.as_u64().and_then(|u| i64::try_from(u).ok()))
        .or_else(|| v.as_f64().filter(|n| n.is_finite()).map(|n| n as i64))
}

fn num_string(n: &Number) -> String {
    if let Some(i) = n.as_i64() {
        i.to_string()
    } else if let Some(u) = n.as_u64() {
        u.to_string()
    } else if let Some(f) = n.as_f64() {
        fmt_num(f)
    } else {
        n.to_string()
    }
}

/// JavaScript `String(number)` for finite values: `12` stays `12`, `180.5` stays `180.5`.
fn fmt_num(n: f64) -> String {
    if !n.is_finite() {
        return "—".into();
    }
    if n.fract() == 0.0 && n.abs() < 1e15 {
        format!("{}", n as i64)
    } else {
        format!("{n}")
    }
}

fn fixed0(n: f64) -> String {
    format!("{}", n.round() as i64)
}

fn replace_first(s: &str, from: char, to: &str) -> String {
    match s.find(from) {
        Some(i) => {
            let mut out = String::with_capacity(s.len() + to.len());
            out.push_str(&s[..i]);
            out.push_str(to);
            out.push_str(&s[i + from.len_utf8()..]);
            out
        }
        None => s.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_follow_the_ts_thresholds() {
        assert_eq!(format_tokens(0.0), "0");
        assert_eq!(format_tokens(-4.0), "0");
        assert_eq!(format_tokens(f64::NAN), "0");
        assert_eq!(format_tokens(999.4), "999");
        assert_eq!(format_tokens(999.6), "1.0k");
        assert_eq!(format_tokens(1500.0), "1.5k");
        assert_eq!(format_tokens(9999.0), "10.0k");
        assert_eq!(format_tokens(10_000.0), "10k");
        assert_eq!(format_tokens(12_345.0), "12k");
    }

    #[test]
    fn cost_follows_the_ts_thresholds() {
        assert_eq!(format_cost(f64::NAN), "$0.00");
        assert_eq!(format_cost(0.0), "$0.00");
        assert_eq!(format_cost(-1.0), "$0.00");
        assert_eq!(format_cost(0.004), "$0.0040");
        assert_eq!(format_cost(0.01), "$0.010");
        assert_eq!(format_cost(0.5), "$0.500");
        assert_eq!(format_cost(1.2), "$1.20");
    }

    #[test]
    fn hive_label_clips_on_characters_and_untitled() {
        let row = HiveRow {
            id: "a".into(),
            cadence: "post".into(),
            status: "running".into(),
            goal: "  ".into(),
            outcome: String::new(),
            blockers: Vec::new(),
        };
        assert_eq!(hive_row_label(&row), "post · running · untitled job");
        let long = "x".repeat(73);
        let row = HiveRow { goal: long, ..row };
        let label = hive_row_label(&row);
        assert!(label.ends_with('…'));
        assert_eq!(label.chars().filter(|c| *c == 'x').count(), 71);
        let exact = "y".repeat(72);
        let row = HiveRow {
            goal: exact.clone(),
            ..row
        };
        assert!(hive_row_label(&row).ends_with(&exact));
    }

    #[test]
    fn live_rows_exclude_retired_and_cancelled() {
        let row = |status: &str| HiveRow {
            id: "a".into(),
            cadence: "forager".into(),
            status: status.into(),
            goal: "g".into(),
            outcome: String::new(),
            blockers: Vec::new(),
        };
        assert!(hive_is_live(&row("running")));
        assert!(hive_is_live(&row("pending")));
        assert!(!hive_is_live(&row("retired")));
        assert!(!hive_is_live(&row("cancelled")));
    }

    #[test]
    fn names_and_stamps() {
        assert_eq!(file_name(r"C:\models\qwen.gguf"), "qwen.gguf");
        assert_eq!(file_name("/var/models/qwen.gguf"), "qwen.gguf");
        assert_eq!(file_name("qwen.gguf"), "qwen.gguf");
        assert_eq!(stamp("2026-11-22T21:04:05Z"), "2026-11-22 21:04:05 UTC");
        assert_eq!(short_stamp("2026-11-22T21:04:05Z"), "2026-11-22 21:04");
    }

    #[test]
    fn badge_order_matches_the_tauri_pill() {
        assert_eq!(badge_tone(Some(true), true), Tone::Ok);
        assert_eq!(badge_tone(Some(false), true), Tone::Warn);
        assert_eq!(badge_tone(Some(false), false), Tone::Danger);
        assert_eq!(badge_tone(None, false), Tone::Muted);
    }

    #[test]
    fn sample_snapshot_uses_the_tauri_rules() {
        let view = view_from(&sample_snapshot());
        assert_eq!(view.overall_label, "Degraded");
        assert_eq!(view.checked_at.as_deref(), Some("2026-11-22 21:04:05 UTC"));
        assert_eq!(view.collect_ms.as_deref(), Some("128"));
        assert_eq!(view.server.badge.label, "Up");
        assert_eq!(
            view.server
                .rows
                .iter()
                .find(|r| r.label == "Active")
                .map(|r| r.value.as_str()),
            Some("grok/grok-4")
        );
        assert_eq!(
            view.server
                .rows
                .iter()
                .find(|r| r.label == "RSS")
                .map(|r| r.value.as_str()),
            Some("180.5 MB")
        );
        assert_eq!(view.rmb.badge.label, "Starting");
        assert_eq!(
            badge_tone(view.rmb.badge.ok, view.rmb.badge.warn),
            Tone::Danger
        );
        assert_eq!(
            view.rmb
                .rows
                .iter()
                .find(|r| r.label == "GGUF")
                .map(|r| r.value.as_str()),
            Some("qwen2.5-7b.gguf")
        );
        assert_eq!(view.hardware.fact.badge.label, "NVIDIA");
        assert!(view.hardware.gpus[0].meter.is_some());
        assert_eq!(view.providers.badge.label, "2 ready");
        assert_eq!(view.providers.remote.len(), 2);
        assert_eq!(view.providers.local.len(), 1);
        assert_eq!(view.providers.usage[0].tokens, "12k");
        assert_eq!(view.providers.usage[0].cost, "$1.25");
        assert_eq!(
            view.providers.probes.as_deref(),
            Some("Probes: ollama: 18 ms · lmstudio: fail")
        );
        assert_eq!(view.vision.badge.label, "Suspended");
        assert_eq!(
            badge_tone(view.vision.badge.ok, view.vision.badge.warn),
            Tone::Warn
        );
        assert_eq!(view.computer.badge.label, "Connected");
        let down = view_from(
            &serde_json::json!({"remedy": {"api": {"listening": false}}, "rmb": {"enabled": false}}),
        );
        assert_eq!(down.server.badge.label, "Down");
        assert_eq!(down.rmb.badge.label, "Off");
        assert_eq!(
            badge_tone(down.rmb.badge.ok, down.rmb.badge.warn),
            Tone::Danger
        );
        let stopped = view_from(&serde_json::json!({"rmb": {"enabled": true, "running": false}}));
        assert_eq!(stopped.rmb.badge.label, "Stopped");
        assert_eq!(
            badge_tone(stopped.rmb.badge.ok, stopped.rmb.badge.warn),
            Tone::Warn
        );
    }

    #[test]
    fn rounds_keep_server_order_and_badge_rules() {
        let rounds = rounds_from(&sample_rounds());
        assert_eq!(rounds.len(), 3);
        assert_eq!(rounds[0].status.label, "applied");
        assert_eq!(
            badge_tone(rounds[0].status.ok, rounds[0].status.warn),
            Tone::Ok
        );
        assert_eq!(
            rounds[0].live.as_ref().map(|b| b.label.as_str()),
            Some("Live")
        );
        assert_eq!(rounds[0].meta, "desktop · 2026-11-22 21:01");
        assert_eq!(rounds[1].status.label, "rolled_back");
        assert_eq!(
            badge_tone(rounds[1].status.ok, rounds[1].status.warn),
            Tone::Warn
        );
        assert!(rounds[1].live.is_none());
        assert_eq!(
            badge_tone(rounds[2].status.ok, rounds[2].status.warn),
            Tone::Danger
        );
    }

    #[test]
    fn roster_parses_counts_and_a_logical_error() {
        let roster = sample_roster();
        assert_eq!(roster.rows.len(), 3);
        assert_eq!(roster.live_posts, 1);
        assert_eq!(roster.live_foragers, 1);
        assert_eq!(roster.rows[1].blockers.len(), 3);
        let err = roster_from(&serde_json::json!({"ok": false, "error": "hive down"})).unwrap_err();
        assert_eq!(err, "hive down");
        let op = hive_op(&serde_json::json!({"ok": false, "error": "retire failed"}));
        assert!(!op.ok);
        assert_eq!(op.error.as_deref(), Some("retire failed"));
    }
}
