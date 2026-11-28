//! Background work for one assistant turn.
//!
//! Matches the current app's collapsed WorkStrip and `liveSegments`: tool
//! calls, tool results, step frames, and named error frames become steps.
//! The reply is the text after the last tool-call break. Text before each
//! break is sealed into its own settled bubble, with reactions once the
//! runtime sends a draft id.

use serde_json::Value;

/// `running`, `done`, or `error`, as a process step reports it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepStatus {
    /// The tool is still going.
    Running,
    /// The tool finished cleanly.
    Done,
    /// The tool failed.
    Error,
}

/// One tool or step in a turn.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkStep {
    /// Call id, when the runtime sent one.
    pub id: String,
    /// Tool name (`file_read`, `bash`, …).
    pub name: String,
    /// Running, done, or error.
    pub status: StepStatus,
    /// Short failure text, when `status` is [`StepStatus::Error`].
    pub error: Option<String>,
    /// What the model wrote just before this call. Shown as a settled mid-turn bubble.
    pub narration: Option<String>,
    /// A short argument preview (`command`, `path`, `argv`, …), when the call carried one.
    pub detail: Option<String>,
    /// Pretty-printed args for Med/Full process detail (the full command, path, pattern, …).
    pub args_text: Option<String>,
    /// Tool output or preview, persisted on the step so the trail can show it.
    pub result_text: Option<String>,
    /// When the call was made and when its result came back (Unix seconds),
    /// where known. What she said before the call was said by `at`.
    pub at: Option<i64>,
    pub done_at: Option<i64>,
}

impl WorkStep {
    fn new(id: impl Into<String>, name: impl Into<String>, status: StepStatus) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            status,
            error: None,
            narration: None,
            detail: None,
            args_text: None,
            result_text: None,
            at: None,
            done_at: None,
        }
    }

    /// A finished step, for previews.
    pub fn done(id: &str, name: &str) -> Self {
        Self::new(id, name, StepStatus::Done)
    }

    /// A failed step, for previews.
    pub fn failed(id: &str, name: &str, error: &str) -> Self {
        let mut step = Self::new(id, name, StepStatus::Error);
        step.error = Some(error.to_string());
        step
    }
}

/// Counts behind "N steps / N errors".
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct WorkSummary {
    /// Every step.
    pub total: usize,
    /// Still running.
    pub running: usize,
    /// Finished cleanly.
    pub done: usize,
    /// Failed.
    pub errors: usize,
}

/// Counts each status. Unknown values are not produced.
pub fn summarize(steps: &[WorkStep]) -> WorkSummary {
    let mut sum = WorkSummary {
        total: steps.len(),
        ..WorkSummary::default()
    };
    for step in steps {
        match step.status {
            StepStatus::Running => sum.running += 1,
            StepStatus::Done => sum.done += 1,
            StepStatus::Error => sum.errors += 1,
        }
    }
    sum
}

/// Fluent id for the strip title. `Worked` when there is any step.
pub fn title_key(sum: &WorkSummary, thought: bool, live: bool) -> &'static str {
    if sum.total > 0 || !thought {
        if live { "work-working" } else { "work-worked" }
    } else if live {
        "work-thinking"
    } else {
        "work-thought"
    }
}

/// The strip is absent for a plain reply (no steps and no thinking).
pub fn visible(sum: &WorkSummary, thought: bool) -> bool {
    sum.total > 0 || thought
}

/// Reply text: everything after the last tool-call break, leading space removed.
pub fn reply_after(text: &str, breaks: &[usize]) -> String {
    let from = breaks
        .iter()
        .copied()
        .filter(|n| *n > 0 && *n <= text.len())
        .max()
        .unwrap_or(0);
    let from = char_floor(text, from);
    text[from..].trim_start().to_string()
}

/// Completed mid-turn speech: everything before the last tool-call break.
pub fn sealed_before(text: &str, breaks: &[usize]) -> String {
    let Some(at) = breaks
        .iter()
        .copied()
        .filter(|n| *n > 0 && *n <= text.len())
        .max()
    else {
        return String::new();
    };
    let at = char_floor(text, at);
    text[..at].to_string()
}

/// Sealed mid-turn beats, in order, split on each tool-call break.
pub fn narration_beats(text: &str, breaks: &[usize]) -> Vec<String> {
    let sealed = sealed_before(text, breaks);
    if sealed.trim().is_empty() {
        return Vec::new();
    }
    let mut cuts: Vec<usize> = breaks
        .iter()
        .copied()
        .filter(|n| *n > 0 && *n <= sealed.len())
        .map(|n| char_floor(&sealed, n))
        .collect();
    cuts.sort_unstable();
    cuts.dedup();
    let mut beats = Vec::new();
    let mut from = 0usize;
    for at in cuts {
        if at <= from {
            continue;
        }
        let seg = sealed[from..at].trim();
        if !seg.is_empty() {
            beats.push(seg.to_string());
        }
        from = at;
    }
    beats
}

/// Removes saved narration from a stored reply, in order. No narration leaves the text alone.
pub fn strip_narration(content: &str, narrations: &[String]) -> String {
    let mut out = String::new();
    let mut cursor = 0usize;
    let mut any = false;
    for raw in narrations {
        let n = raw.trim();
        if n.is_empty() || cursor > content.len() || !content.is_char_boundary(cursor) {
            continue;
        }
        let Some(rel) = content[cursor..].find(n) else {
            continue;
        };
        let at = cursor + rel;
        out.push_str(&content[cursor..at]);
        cursor = at + n.len();
        any = true;
    }
    if cursor <= content.len() && content.is_char_boundary(cursor) {
        out.push_str(&content[cursor..]);
    }
    if !any {
        return content.to_string();
    }
    collapse_blank(&out).trim().to_string()
}

/// Marks still-running steps done, or error when the turn itself failed.
pub fn settle(steps: &mut [WorkStep], failed: bool) {
    for step in steps {
        if step.status == StepStatus::Running {
            step.status = if failed {
                StepStatus::Error
            } else {
                StepStatus::Done
            };
        }
    }
}

/// Appends or replaces thinking text from a `thinking` frame.
pub fn on_thinking(buf: &mut String, data: &str) {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        if !data.is_empty() {
            buf.push_str(data);
        }
        return;
    };
    let text = v.get("text").and_then(|t| t.as_str()).unwrap_or("");
    if v.get("replace").and_then(|r| r.as_bool()) == Some(true) {
        buf.clear();
        buf.push_str(text);
    } else if !text.is_empty() {
        buf.push_str(text);
    }
}

/// Records a `tool_call`. A repeated call id does not add a second step or break.
pub fn on_tool_call(steps: &mut Vec<WorkStep>, breaks: &mut Vec<usize>, partial: &str, data: &str) {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return;
    };
    let name = first_str(&v, &["name", "tool"]);
    if name.is_empty() {
        return;
    }
    let given = first_str(&v, &["call_id", "id"]);
    if !given.is_empty() && steps.iter().any(|s| s.id == given) {
        return;
    }
    let id = if given.is_empty() {
        format!("{name}-{}", steps.len())
    } else {
        given
    };
    let at = partial.len();
    let start = breaks.last().copied().unwrap_or(0).min(at);
    let narration = partial.get(start..at).unwrap_or("").trim();
    if at > 0 && breaks.last().copied() != Some(at) {
        breaks.push(at);
    }
    let mut step = WorkStep::new(id, name, StepStatus::Running);
    if !narration.is_empty() {
        step.narration = Some(narration.to_string());
    }
    step.args_text = args_text(&v);
    step.detail = short_args(&v);
    step.at = Some(crate::time::now_secs());
    steps.push(step);
}

/// Resolves a `tool_result` onto its step. `ok: false` is an error. A lost call still counts.
pub fn on_tool_result(steps: &mut Vec<WorkStep>, data: &str) {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return;
    };
    let name = first_str(&v, &["name", "tool"]);
    if name.is_empty() {
        return;
    }
    let id = first_str(&v, &["call_id", "id"]);
    let ok = v.get("ok").and_then(|x| x.as_bool()).unwrap_or(true);
    let output = first_str(&v, &["output", "preview", "error", "message"]);
    let preview = first_str(&v, &["preview", "output", "error", "message"]);
    let error = if ok {
        None
    } else if preview.is_empty() && output.is_empty() {
        Some("tool failed".to_string())
    } else if !preview.is_empty() {
        Some(preview)
    } else {
        Some(output.clone())
    };
    let status = if ok {
        StepStatus::Done
    } else {
        StepStatus::Error
    };
    let result = if output.is_empty() { None } else { Some(output) };
    mark_or_push(steps, &id, &name, status, error, result);
    let now = crate::time::now_secs();
    if let Some(step) = steps.iter_mut().rev().find(|s| (!id.is_empty() && s.id == id) || (id.is_empty() && s.name == name)) {
        step.done_at.get_or_insert(now);
    }
}

/// Creates or updates a step from a `step` frame.
pub fn on_step(steps: &mut Vec<WorkStep>, data: &str) {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return;
    };
    let name = first_str(&v, &["name", "tool"]);
    let id = first_str(&v, &["call_id", "id"]);
    if name.is_empty() && id.is_empty() {
        return;
    }
    let status = parse_status(&v).unwrap_or(StepStatus::Running);
    let err = optional_str(&v, &["error", "message"]);
    let error = if status == StepStatus::Error {
        Some(err.unwrap_or_else(|| "tool failed".to_string()))
    } else {
        None
    };
    mark_or_push(steps, &id, &name, status, error, None);
}

/// True when an `error` frame names a tool, so the turn keeps going.
pub fn is_step_error(data: &str) -> bool {
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return false;
    };
    nonempty(&v, "name")
        || nonempty(&v, "tool")
        || nonempty(&v, "call_id")
        || (nonempty(&v, "id") && (v.get("status").is_some() || v.get("ok").is_some()))
}

/// Marks the named step failed. Does not end the turn.
pub fn on_step_error(steps: &mut Vec<WorkStep>, data: &str) {
    if !is_step_error(data) {
        return;
    }
    let Ok(v) = serde_json::from_str::<Value>(data) else {
        return;
    };
    let name = first_str(&v, &["name", "tool"]);
    let id = first_str(&v, &["call_id", "id"]);
    let err = optional_str(&v, &["error", "message", "detail"])
        .unwrap_or_else(|| "tool failed".to_string());
    mark_or_push(steps, &id, &name, StepStatus::Error, Some(err), None);
}

/// A JSON array, or a string that contains one. Missing stays empty.
pub fn json_array(v: Option<&Value>) -> Vec<Value> {
    let Some(v) = v else {
        return Vec::new();
    };
    if let Some(items) = v.as_array() {
        return items.clone();
    }
    v.as_str()
        .and_then(|s| serde_json::from_str::<Vec<Value>>(s).ok())
        .unwrap_or_default()
}

/// Pairs saved `tool_calls` with `tool_results` the way the current app does.
pub fn steps_from_history(calls: &[Value], results: &[Value]) -> Vec<WorkStep> {
    let calls = read_calls(calls);
    let results = read_results(results);
    if calls.is_empty() {
        return results
            .into_iter()
            .enumerate()
            .map(|(i, r)| {
                let mut step = WorkStep::new(
                    if r.id.is_empty() {
                        format!("hist-r-{i}-{}", r.name)
                    } else {
                        r.id
                    },
                    r.name,
                    if r.error.is_some() {
                        StepStatus::Error
                    } else {
                        StepStatus::Done
                    },
                );
                step.error = r.error.clone();
                step.result_text = r.output.clone();
                step
            })
            .collect();
    }
    let paired = pair_results(&calls, &results);
    calls
        .into_iter()
        .enumerate()
        .map(|(i, call)| {
            let res = paired[i].map(|j| &results[j]);
            let mut step = WorkStep::new(
                if call.id.is_empty() {
                    format!("hist-{i}-{}", call.name)
                } else {
                    call.id
                },
                call.name,
                if res.and_then(|r| r.error.as_ref()).is_some() {
                    StepStatus::Error
                } else {
                    StepStatus::Done
                },
            );
            step.error = res.and_then(|r| r.error.clone());
            step.narration = call.narration;
            step.args_text = call.args_text;
            step.detail = call.detail;
            step.result_text = res.and_then(|r| r.output.clone());
            step.at = call.at;
            step.done_at = res.and_then(|r| r.at);
            step
        })
        .collect()
}

fn mark_or_push(
    steps: &mut Vec<WorkStep>,
    id: &str,
    name: &str,
    status: StepStatus,
    error: Option<String>,
    result: Option<String>,
) {
    let idx = if !id.is_empty() {
        steps.iter().position(|s| s.id == id)
    } else {
        None
    };
    let idx = idx.or_else(|| {
        steps
            .iter()
            .position(|s| s.status == StepStatus::Running && s.name == name)
    });
    if let Some(i) = idx {
        steps[i].status = status;
        if !id.is_empty() && steps[i].id.is_empty() {
            steps[i].id = id.to_string();
        }
        steps[i].error = error;
        if let Some(r) = result.filter(|s| !s.is_empty()) {
            steps[i].result_text = Some(r);
        }
        return;
    }
    let mut step = WorkStep::new(
        if id.is_empty() {
            format!("{name}-{}", steps.len())
        } else {
            id.to_string()
        },
        if name.is_empty() { "tool" } else { name },
        status,
    );
    step.error = error;
    if let Some(r) = result.filter(|s| !s.is_empty()) {
        step.result_text = Some(r);
    }
    steps.push(step);
}


/// The args object (or string) from a tool-call or history payload.
fn call_args(v: &Value) -> Option<&Value> {
    v.get("args")
        .or_else(|| v.get("input"))
        .or_else(|| v.get("arguments"))
        .filter(|a| !a.is_null())
}

/// Pretty-printed args for the process trail (Full / expand).
pub fn args_text(v: &Value) -> Option<String> {
    let args = call_args(v)?;
    if let Some(s) = args.as_str().map(str::trim).filter(|s| !s.is_empty()) {
        return Some(s.to_string());
    }
    if args.as_object().is_some_and(|o| o.is_empty()) || args.as_array().is_some_and(|a| a.is_empty()) {
        return None;
    }
    serde_json::to_string_pretty(args).ok()
}

/// A one-line argument preview from a tool-call payload.
///
/// Reads `command`/`path`/`pattern`/`query` and also `argv` arrays (shell.exec),
/// so a bash step shows the actual command line, not just the tool name.
pub fn short_args(v: &Value) -> Option<String> {
    step_inline_summary("", args_text(v).as_deref(), None, None)
}

/// Settings `tool_process`: off (Min), medium (Med), full (Full).
pub fn normalize_tool_process(raw: &str) -> &'static str {
    let s = raw.trim().to_ascii_lowercase();
    match s.as_str() {
        "medium" | "med" => "medium",
        "full" | "full+" | "fullplus" | "full_plus" | "debug" | "on" | "true" | "1" | "yes" => {
            "full"
        }
        _ => "off",
    }
}

pub fn is_full_process_mode(mode: &str) -> bool {
    normalize_tool_process(mode) == "full"
}

/// Always-visible one-liner under a process step (Med depth).
pub fn step_inline_summary(
    _tool_name: &str,
    args_text: Option<&str>,
    result_text: Option<&str>,
    error: Option<&str>,
) -> Option<String> {
    if let Some(err) = error.map(str::trim).filter(|s| !s.is_empty()) {
        return Some(one_line(&format!("Error: {err}"), 160));
    }
    if let Some(raw) = args_text.map(str::trim).filter(|s| !s.is_empty()) {
        if let Ok(obj) = serde_json::from_str::<Value>(raw) {
            if let Some(s) = summary_from_args(&obj) {
                return Some(s);
            }
        } else if !raw.starts_with('{') {
            return Some(one_line(raw, 160));
        }
    }
    if let Some(result) = result_text.map(str::trim).filter(|s| !s.is_empty()) {
        let first = result
            .lines()
            .map(str::trim)
            .find(|l| !l.is_empty())
            .unwrap_or(result);
        return Some(one_line(first, 160));
    }
    if let Some(raw) = args_text {
        if let Ok(Value::Object(obj)) = serde_json::from_str::<Value>(raw) {
            let keys: Vec<&str> = obj.keys().take(4).map(|s| s.as_str()).collect();
            if !keys.is_empty() {
                return Some(one_line(&format!("{{{}}}", keys.join(", ")), 120));
            }
        }
    }
    None
}

fn summary_from_args(args: &Value) -> Option<String> {
    if let Some(s) = args.as_str().map(str::trim).filter(|s| !s.is_empty()) {
        return Some(one_line(s, 160));
    }
    let obj = args.as_object()?;
    if let Some(list) = obj.get("todos").and_then(Value::as_array) {
        return Some(checklist_line(list));
    }
    const PATH_KEYS: &[&str] = &["path", "file", "filepath", "file_path", "directory", "dir"];
    const CMD_KEYS: &[&str] = &["command", "cmd", "shell", "code"];
    const QUERY_KEYS: &[&str] = &[
        "query", "q", "pattern", "search", "url", "prompt", "title", "mission",
    ];
    for key in PATH_KEYS.iter().chain(CMD_KEYS).chain(QUERY_KEYS) {
        if let Some(s) = stringish(obj.get(*key)) {
            return Some(one_line(&s, 160));
        }
    }
    if let Some(s) = argv_line(obj.get("argv")).or_else(|| argv_line(obj.get("args"))) {
        return Some(one_line(&s, 160));
    }
    for val in obj.values() {
        if let Some(s) = stringish(Some(val)) {
            return Some(one_line(&s, 160));
        }
        if let Some(s) = argv_line(Some(val)) {
            return Some(one_line(&s, 160));
        }
    }
    None
}

fn stringish(v: Option<&Value>) -> Option<String> {
    let v = v?;
    if let Some(s) = v.as_str().map(str::trim).filter(|s| !s.is_empty()) {
        return Some(s.to_string());
    }
    if v.is_number() || v.is_boolean() {
        return Some(v.to_string());
    }
    argv_line(Some(v))
}

/// A checklist update, said the way a person would: how far along, and what is next.
fn checklist_line(items: &[Value]) -> String {
    let text = |t: &Value| ["content", "text", "title"].iter().find_map(|k| stringish(t.get(*k)));
    let status = |t: &Value| t.get("status").and_then(Value::as_str).unwrap_or("").to_ascii_lowercase();
    let done = items.iter().filter(|t| matches!(status(t).as_str(), "done" | "completed" | "complete")).count();
    let mut line = format!("{done} of {} done", items.len());
    if let Some(now) = items
        .iter()
        .find(|t| matches!(status(t).as_str(), "in_progress" | "doing" | "active"))
        .and_then(text)
    {
        line.push_str(&format!(" · now: {now}"));
    }
    one_line(&line, 160)
}

fn argv_line(v: Option<&Value>) -> Option<String> {
    let arr = v?.as_array()?;
    // Words and numbers only: a list of objects is not a command line.
    let parts: Vec<String> = arr
        .iter()
        .filter_map(|x| match x {
            Value::String(s) => Some(s.clone()),
            Value::Number(n) => Some(n.to_string()),
            Value::Bool(b) => Some(b.to_string()),
            _ => None,
        })
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join(" "))
    }
}

fn one_line(text: &str, max: usize) -> String {
    let s = text.split_whitespace().collect::<Vec<_>>().join(" ");
    truncate_arg_at(&s, max)
}

/// Short multi-line body for Med (not a full dump).
pub fn step_medium_preview(result: Option<&str>, error: Option<&str>) -> String {
    let raw = error.or(result).unwrap_or("").trim();
    if raw.is_empty() {
        return String::new();
    }
    let lines: Vec<&str> = raw.lines().collect();
    let mut out = lines.iter().take(4).copied().collect::<Vec<_>>().join("\n");
    if lines.len() > 4 {
        out.push('\n');
        out.push('…');
    }
    if out.chars().count() > 480 {
        out = truncate_arg_at(&out, 480);
    }
    out
}

/// Consecutive same-name steps, the way Med groups repeated tool runs.
pub fn group_process_steps(steps: &[WorkStep]) -> Vec<&[WorkStep]> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < steps.len() {
        let name = steps[i].name.as_str();
        let mut j = i + 1;
        while j < steps.len() && steps[j].name == name {
            j += 1;
        }
        out.push(&steps[i..j]);
        i = j;
    }
    out
}

fn truncate_arg(s: &str) -> String {
    truncate_arg_at(s, 56)
}

fn truncate_arg_at(s: &str, max: usize) -> String {
    let mut chars = s.chars();
    let taken: String = chars.by_ref().take(max).collect();
    if chars.next().is_some() {
        let mut cut = taken;
        if max > 1 {
            cut = cut.chars().take(max.saturating_sub(1)).collect();
        }
        format!("{cut}…")
    } else {
        taken
    }
}

fn parse_status(v: &Value) -> Option<StepStatus> {
    if v.get("ok").and_then(|x| x.as_bool()) == Some(false) {
        return Some(StepStatus::Error);
    }
    let raw = v
        .get("status")
        .or_else(|| v.get("state"))
        .and_then(|x| x.as_str())?;
    match raw.trim().to_ascii_lowercase().as_str() {
        "running" | "start" | "started" => Some(StepStatus::Running),
        "done" | "ok" | "success" | "finished" => Some(StepStatus::Done),
        "error" | "failed" | "fail" => Some(StepStatus::Error),
        _ => None,
    }
}

struct HistCall {
    name: String,
    id: String,
    narration: Option<String>,
    args_text: Option<String>,
    detail: Option<String>,
    at: Option<i64>,
}

struct HistRes {
    name: String,
    id: String,
    error: Option<String>,
    output: Option<String>,
    at: Option<i64>,
}

fn read_calls(values: &[Value]) -> Vec<HistCall> {
    values
        .iter()
        .filter_map(|v| {
            let name = first_str(v, &["name", "tool"]);
            if name.is_empty() {
                return None;
            }
            let narration = optional_str(v, &["narration"]).filter(|s| !s.trim().is_empty());
            Some(HistCall {
                name,
                id: first_str(v, &["id", "call_id"]),
                narration,
                args_text: args_text(v),
                detail: short_args(v),
                at: v.get("at").and_then(Value::as_i64),
            })
        })
        .collect()
}

fn read_results(values: &[Value]) -> Vec<HistRes> {
    values
        .iter()
        .filter_map(|v| {
            let name = first_str(v, &["name", "tool"]);
            if name.is_empty() {
                return None;
            }
            let err = optional_str(v, &["error"]).filter(|s| !s.is_empty());
            let err = if err.is_none() && v.get("ok").and_then(|x| x.as_bool()) == Some(false) {
                Some(
                    optional_str(v, &["preview", "output", "message"])
                        .unwrap_or_else(|| "tool failed".to_string()),
                )
            } else {
                err
            };
            let output = optional_str(v, &["output", "preview", "result", "content"])
                .filter(|s| !s.is_empty());
            Some(HistRes {
                name,
                id: first_str(v, &["id", "call_id"]),
                error: err,
                output,
                at: v.get("at").and_then(Value::as_i64),
            })
        })
        .collect()
}

fn pair_results(calls: &[HistCall], results: &[HistRes]) -> Vec<Option<usize>> {
    let mut used = vec![false; results.len()];
    let mut out = vec![None; calls.len()];
    let call_ids: Vec<&str> = calls
        .iter()
        .filter(|c| !c.id.is_empty())
        .map(|c| c.id.as_str())
        .collect();
    for (i, call) in calls.iter().enumerate() {
        if call.id.is_empty() {
            continue;
        }
        if let Some(j) = results.iter().position(|r| r.id == call.id) {
            if !used[j] {
                used[j] = true;
                out[i] = Some(j);
            }
        }
    }
    for (i, call) in calls.iter().enumerate() {
        if out[i].is_some() {
            continue;
        }
        let claimable = |j: usize| {
            !used[j]
                && (results[j].id.is_empty() || !call_ids.iter().any(|id| *id == results[j].id))
        };
        let mut pick = None;
        if i < results.len() && claimable(i) && results[i].name == call.name {
            pick = Some(i);
        } else {
            for (j, res) in results.iter().enumerate() {
                if claimable(j) && res.name == call.name {
                    pick = Some(j);
                    break;
                }
            }
        }
        if let Some(j) = pick {
            used[j] = true;
            out[i] = Some(j);
        }
    }
    out
}

fn first_str(v: &Value, keys: &[&str]) -> String {
    optional_str(v, keys).unwrap_or_default()
}

fn optional_str(v: &Value, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|k| {
        v.get(*k)
            .and_then(|x| x.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
    })
}

fn nonempty(v: &Value, key: &str) -> bool {
    v.get(key)
        .and_then(|x| x.as_str())
        .is_some_and(|s| !s.trim().is_empty())
}

fn char_floor(text: &str, mut i: usize) -> usize {
    if i > text.len() {
        i = text.len();
    }
    while i > 0 && !text.is_char_boundary(i) {
        i -= 1;
    }
    i
}

fn collapse_blank(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut newlines = 0u32;
    for ch in s.chars() {
        if ch == '\n' {
            newlines += 1;
            if newlines <= 2 {
                out.push('\n');
            }
        } else {
            newlines = 0;
            out.push(ch);
        }
    }
    out
}

/// A tool's name as people read it: `web.fetch` is "Fetch", `read_file` is "Read file".
pub fn short_tool(name: &str) -> String {
    let trimmed = name.rsplit([':', '/', '.']).next().unwrap_or(name);
    let mut chars = trimmed.chars();
    match chars.next() {
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str().replace('_', " ").as_str(),
        None => trimmed.to_string(),
    }
}

/// Her reply in the order it happened: what she said before a run of steps, and the steps.
/// A run starts at a step she narrated; steps before her first words have no words.
pub fn flow(steps: &[WorkStep]) -> Vec<(Option<String>, Vec<WorkStep>)> {
    let mut out: Vec<(Option<String>, Vec<WorkStep>)> = Vec::new();
    for step in steps {
        let said = step.narration.as_deref().map(str::trim).filter(|n| !n.is_empty());
        match (said, out.last_mut()) {
            (Some(n), _) => out.push((Some(n.to_string()), vec![step.clone()])),
            (None, Some(last)) => last.1.push(step.clone()),
            (None, None) => out.push((None, vec![step.clone()])),
        }
    }
    out
}

/// One quiet line for a run of her steps, as the chat shows it between what she says:
/// what is running now, or what she ran ("Bash ×3 · Read · 1 failed").
pub fn steps_line(steps: &[WorkStep]) -> Option<String> {
    if steps.is_empty() {
        return None;
    }
    if let Some(now) = steps.iter().rev().find(|s| s.status == StepStatus::Running) {
        let what = step_inline_summary(&now.name, now.args_text.as_deref(), None, None).unwrap_or_default();
        return Some(if what.is_empty() {
            format!("Running {}…", short_tool(&now.name))
        } else {
            one_line(&format!("Running {}: {what}", short_tool(&now.name)), 140)
        });
    }
    let mut order: Vec<(String, usize)> = Vec::new();
    for step in steps {
        let name = short_tool(&step.name);
        match order.iter_mut().find(|(n, _)| *n == name) {
            Some((_, count)) => *count += 1,
            None => order.push((name, 1)),
        }
    }
    let mut line = order
        .iter()
        .map(|(n, c)| if *c > 1 { format!("{n} ×{c}") } else { n.clone() })
        .collect::<Vec<_>>()
        .join(" · ");
    let failed = steps.iter().filter(|s| s.status == StepStatus::Error).count();
    if failed > 0 {
        line.push_str(&format!(" · {failed} failed"));
    }
    Some(line)
}

/// The thought she is on: the last paragraph of her thinking, on one line.
pub fn latest_thought(thinking: &str) -> Option<String> {
    let last = thinking.trim().rsplit("\n\n").map(str::trim).find(|p| !p.is_empty())?;
    Some(one_line(last, 220))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Her reply reads in order: words, then the steps they led to.
    #[test]
    fn a_reply_flows_words_then_steps() {
        let mut a = WorkStep::done("1", "bash");
        a.narration = Some("Building it first.".into());
        let b = WorkStep::done("2", "bash");
        let mut c = WorkStep::failed("3", "read_file", "missing");
        c.narration = Some("Now the config.".into());
        let lead = WorkStep::done("0", "grep");
        let flowed = flow(&[lead, a, b, c]);
        let shape: Vec<(Option<&str>, usize)> = flowed.iter().map(|(w, s)| (w.as_deref(), s.len())).collect();
        assert_eq!(shape, vec![(None, 1), (Some("Building it first."), 2), (Some("Now the config."), 1)]);
        assert_eq!(steps_line(&flowed[1].1).as_deref(), Some("Bash ×2"));
        assert_eq!(steps_line(&flowed[2].1).as_deref(), Some("Read file · 1 failed"));
        let mut running = WorkStep::done("4", "bash");
        running.status = StepStatus::Running;
        running.args_text = Some(r#"{"command":"cargo build"}"#.into());
        assert_eq!(steps_line(&[running]).as_deref(), Some("Running Bash: cargo build"));
        assert_eq!(latest_thought("First idea.\n\nChecking the frame size now.").as_deref(), Some("Checking the frame size now."));
    }

    /// A checklist update reads as progress, never as the raw list.
    #[test]
    fn a_checklist_step_says_how_far_along() {
        let args = r#"{"todos":[{"id":"b1","status":"completed","text":"Fix the stack test"},
            {"id":"m4","status":"in_progress","text":"Merge PR #4"},{"id":"x","status":"pending","text":"Ship"}]}"#;
        assert_eq!(
            step_inline_summary("todo", Some(args), None, None).as_deref(),
            Some("1 of 3 done · now: Merge PR #4")
        );
        let objects = r#"{"items":[{"a":1},{"b":2}]}"#;
        let line = step_inline_summary("x", Some(objects), None, None).unwrap_or_default();
        assert!(!line.contains("\"a\""), "objects are not printed as JSON: {line}");
    }

    #[test]
    fn short_args_reads_command() {
        let v: Value = serde_json::from_str(r#"{"args":{"command":"npm test"}}"#).unwrap();
        assert_eq!(short_args(&v).as_deref(), Some("npm test"));
    }

    #[test]
    fn short_args_reads_argv_and_grep_pattern() {
        let bash: Value = serde_json::from_str(
            r#"{"name":"shell.exec","args":{"argv":["git","-C","C:\\proj","status"]}}"#,
        )
        .unwrap();
        let line = short_args(&bash).expect("argv");
        assert!(line.contains("git"), "{line}");
        assert!(line.contains("status"), "{line}");
        let grep: Value =
            serde_json::from_str(r#"{"args":{"pattern":"fn main","path":"src/lib.rs"}}"#).unwrap();
        assert_eq!(short_args(&grep).as_deref(), Some("src/lib.rs"));
        assert_eq!(normalize_tool_process("Med"), "medium");
        assert_eq!(normalize_tool_process("full+"), "full");
        assert_eq!(normalize_tool_process("min"), "off");
    }

    #[test]
    fn tool_call_and_failed_result_count_one_error() {
        let mut steps = Vec::new();
        let mut breaks = Vec::new();
        let call = r#"{"name":"bash","id":"c1","args":{"command":"npm test"}}"#;
        on_tool_call(&mut steps, &mut breaks, "I'll run the tests.", call);
        assert_eq!(steps[0].detail.as_deref(), Some("npm test"));
        on_tool_call(&mut steps, &mut breaks, "I'll run the tests.", call);
        assert_eq!(steps.len(), 1, "a repeated call id is one step");
        assert_eq!(breaks, vec!["I'll run the tests.".len()]);
        on_tool_result(
            &mut steps,
            r#"{"name":"bash","id":"c1","ok":false,"preview":"exit 1"}"#,
        );
        let sum = summarize(&steps);
        assert_eq!(sum.total, 1);
        assert_eq!(sum.errors, 1);
        assert_eq!(steps[0].error.as_deref(), Some("exit 1"));
        assert_eq!(steps[0].result_text.as_deref(), Some("exit 1"));
        assert_eq!(steps[0].args_text.as_deref().unwrap().contains("npm test"), true);
        assert_eq!(title_key(&sum, false, false), "work-worked");
        assert_eq!(title_key(&sum, false, true), "work-working");
    }

    #[test]
    fn tool_result_prefers_full_output_over_preview() {
        let mut steps = Vec::new();
        let mut breaks = Vec::new();
        on_tool_call(
            &mut steps,
            &mut breaks,
            "",
            r#"{"name":"bash","id":"c2","args":{"command":"ls"}}"#,
        );
        on_tool_result(
            &mut steps,
            r#"{"name":"bash","id":"c2","ok":true,"preview":"a.txt…","output":"a.txt\nb.txt\nc.txt"}"#,
        );
        assert_eq!(steps[0].result_text.as_deref(), Some("a.txt\nb.txt\nc.txt"));
        assert_eq!(steps[0].detail.as_deref(), Some("ls"));
    }

    #[test]
    fn step_frame_and_named_error_do_not_end_like_a_turn_error() {
        let mut steps = Vec::new();
        on_step(
            &mut steps,
            r#"{"name":"file_read","id":"s1","status":"running"}"#,
        );
        on_step(
            &mut steps,
            r#"{"name":"file_read","id":"s1","status":"done"}"#,
        );
        assert_eq!(steps[0].status, StepStatus::Done);
        assert!(!is_step_error(r#"{"message":"the mind hit an error"}"#));
        assert!(is_step_error(r#"{"name":"bash","message":"exit 2"}"#));
        on_step_error(
            &mut steps,
            r#"{"name":"file_write","id":"s2","message":"denied"}"#,
        );
        let sum = summarize(&steps);
        assert_eq!(sum.total, 2);
        assert_eq!(sum.errors, 1);
        assert_eq!(sum.done, 1);
    }

    #[test]
    fn reply_keeps_only_text_after_the_last_break() {
        let text = "I'll reach the folder.\n\nThe importer loads again.";
        let at = "I'll reach the folder.\n\n".len();
        assert_eq!(reply_after(text, &[at]), "The importer loads again.");
        assert_eq!(reply_after("Hello", &[]), "Hello");
    }

    #[test]
    fn mid_turn_beats_seal_at_each_break() {
        let first = "I'll reach the folder through the shell.\n\n";
        let second = "Cmd is eating the search patterns...\n\n";
        let reply = "Patching the entry path.";
        let text = format!("{first}{second}{reply}");
        let breaks = [first.len(), first.len() + second.len()];
        assert_eq!(sealed_before(&text, &breaks), format!("{first}{second}"));
        assert_eq!(
            narration_beats(&text, &breaks),
            vec![
                "I'll reach the folder through the shell.".to_string(),
                "Cmd is eating the search patterns...".to_string(),
            ]
        );
        assert_eq!(reply_after(&text, &breaks), reply);
        assert!(narration_beats("Hello", &[]).is_empty());
        let only = first.to_string();
        assert_eq!(
            narration_beats(&only, &[first.len()]),
            vec!["I'll reach the folder through the shell.".to_string()]
        );
        assert_eq!(reply_after(&only, &[first.len()]), "");
    }

    #[test]
    fn settle_and_saved_pairing() {
        let mut steps = vec![WorkStep::new("a", "file_read", StepStatus::Running)];
        settle(&mut steps, false);
        assert_eq!(steps[0].status, StepStatus::Done);
        settle(&mut steps, true);
        assert_eq!(steps[0].status, StepStatus::Done, "already done stays done");

        let mut running = vec![WorkStep::new("b", "bash", StepStatus::Running)];
        settle(&mut running, true);
        assert_eq!(running[0].status, StepStatus::Error);

        let calls = vec![
            serde_json::json!({"name": "file_read", "id": "c1", "narration": "Reading.", "args": {"path": "C:\\Users\\Ada\\Projects\\app\\readme.md"}}),
            serde_json::json!({"name": "bash", "id": "c2", "args": {"command": "dir src"}}),
        ];
        let results = vec![
            serde_json::json!({"name": "bash", "id": "c2", "error": "exit 1", "output": "dir failed"}),
            serde_json::json!({"name": "file_read", "id": "c1", "output": "ok from file"}),
        ];
        let saved = steps_from_history(&calls, &results);
        assert_eq!(saved.len(), 2);
        assert_eq!(saved[0].status, StepStatus::Done);
        assert_eq!(saved[1].status, StepStatus::Error);
        assert!(saved[0].detail.as_deref().unwrap().contains("readme.md"));
        assert_eq!(saved[1].detail.as_deref(), Some("dir src"));
        assert_eq!(saved[0].result_text.as_deref(), Some("ok from file"));
        assert_eq!(saved[1].result_text.as_deref(), Some("dir failed"));
        let groups = group_process_steps(&saved);
        assert_eq!(groups.len(), 2);
        let reply = strip_narration(
            "Reading.\n\nIt loads.",
            &[saved[0].narration.clone().unwrap()],
        );
        assert_eq!(reply, "It loads.");
        assert!(!visible(&WorkSummary::default(), false));
        assert!(visible(&summarize(&saved), false));
        let one = WorkSummary {
            total: 1,
            done: 1,
            ..WorkSummary::default()
        };
        assert_eq!(title_key(&one, true, false), "work-worked");
        let thought_only = WorkSummary::default();
        assert_eq!(title_key(&thought_only, true, true), "work-thinking");
        assert_eq!(title_key(&thought_only, true, false), "work-thought");
    }
}
