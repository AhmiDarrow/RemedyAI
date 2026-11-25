//! Privacy, permissions, startup, help, about, and license.
//!
//! Generation limits save on their own. Everything else waits for Settings Save.
//! Launch-at-login is omitted on Linux. Close-to-tray stays on and cannot be turned off.

use remedy_ui::layout::Length;
use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use serde_json::{Number, Value, json};

use super::bits;
use super::form;
use super::logic;
use super::state::Settings;
use crate::runtime;

pub fn sections(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(privacy(ui, s))
        .child(access(ui, s))
        .child(permissions(ui, s))
        .child(always_ready(ui, s))
        .child(tool_process(ui, s))
        .child(advanced(ui, s))
        .child(license(ui, s))
        .child(help(ui, s))
        .child(about(ui, s))
}

fn privacy(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "privacy", move |ui, s| {
        let busy = Signal::new(false);
        let msg = Signal::new(String::new());
        form::stack(ui)
            .child(form::hint(
                ui,
                "Cloud models receive your chat and tool results. Choose how much personal information to include.",
            ))
            .child(form::toggle(
                ui,
                "Redact personal information",
                "Remove email addresses, phone numbers, and similar identifiers from tool results before sending them to your model. Results may contain less detail.",
                s.privacy_mode,
            ))
            .child(form::hint(ui, "Stored API keys are kept out of model input in both modes."))
            .child(form::toggle(
                ui,
                "Remember our working relationship",
                "Keep personal context and continuity when you switch models. Turning this off stops updates; it does not erase existing memories.",
                s.soul_field,
            ))
            .child(form::toggle(
                ui,
                "ClaimIDX",
                "On by default. Turn off to stop the local ClaimIDX host completely, including public ledger submits.",
                s.claimidx_enabled,
            ))
            .child(wipe_block(ui, s, busy, msg))
    })
}

fn wipe_block(ui: &Ui, s: Settings, busy: Signal<bool>, msg: Signal<String>) -> El {
    let confirm = bits::visible_when(
        form::stack(ui)
            .child(form::notice(
                ui,
                "error",
                "This deletes your saved profile, personal memories, relationship context, and life goals. Type WIPE to confirm.",
            ))
            .child(form::bound_line(ui, "Type WIPE to confirm persona wipe", s.wipe_text, "Type WIPE"))
            .child(
                w::button(ui, move || -> String { if busy.get() { "Wiping…".into() } else { "Wipe what Remedy knows about me".into() } }, move || {
                    run_wipe(s, busy, msg);
                })
                .variant("danger")
                .disabled(move || busy.get() || !s.wipe_text.get().trim().eq_ignore_ascii_case("WIPE")),
            )
            .child(form::action(ui, "Cancel", "quiet", move || {
                s.wipe_open.set(false);
                s.wipe_text.set(String::new());
            })),
        move || s.wipe_open.get(),
    );
    let start = bits::visible_when(
        form::action(ui, "Wipe persona…", "danger", move || {
            s.wipe_open.set(true);
            msg.set(String::new());
        }),
        move || !s.wipe_open.get(),
    );
    let note = bits::visible_when(w::text(ui, move || msg.get()).variant("small"), move || !msg.get().is_empty());
    form::stack(ui)
        .child(form::label(ui, "Delete personal memories"))
        .child(form::hint(
            ui,
            "Delete remembered facts about you, relationship context, and life goals. Chats, API keys, skills, and this app stay. Cannot be undone.",
        ))
        .child(start)
        .child(confirm)
        .child(note)
}

fn run_wipe(s: Settings, busy: Signal<bool>, msg: Signal<String>) {
    if busy.get() || !s.wipe_text.get().trim().eq_ignore_ascii_case("WIPE") {
        return;
    }
    if s.preview {
        msg.set("Not available in this preview.".into());
        return;
    }
    busy.set(true);
    msg.set(String::new());
    super::http::request(s, "POST", "memory/persona-wipe".into(), Some(json!({ "confirm": "WIPE" })), move |out| {
        busy.set(false);
        match out {
            Ok(_) => {
                s.wipe_text.set(String::new());
                s.wipe_open.set(false);
                let done = "Persona wiped. Remedy no longer remembers facts about you.";
                msg.set(done.into());
                s.banner.set(done.into());
            }
            Err(e) => {
                let text = if e.is_empty() { "Wipe failed".into() } else { e };
                msg.set(text.clone());
                s.banner.set(text);
            }
        }
    });
}

fn access(ui: &Ui, s: Settings) -> El {
    Effect::new(move || {
        if logic::project_forces_full(&s.project_path.get()) {
            s.access_scope.set_if_changed("full".into());
        }
    });
    form::block(ui, s, "access", move |ui, s| {
        let options = vec![
            ("untrusted".into(), "Untrusted project (strict)".into()),
            ("project".into(), "Project + Desktop/Docs/Downloads".into()),
            ("home".into(), "Project + full home folder".into()),
            ("full".into(), "Full user machine (you grant)".into()),
        ];
        let picker = form::pick(ui, "Filesystem scope", &options, s.access_scope)
            .disabled(move || logic::project_forces_full(&s.project_path.get()));
        form::stack(ui)
            .child(form::label(ui, "Filesystem scope"))
            .child(picker)
            .child(w::text(ui, move || access_hint(&s.project_path.get()).to_string()).variant("small"))
    })
}

fn access_hint(path: &str) -> &'static str {
    if logic::project_forces_full(path) {
        "Scope is forced to full while no project folder is set. Choose a workspace in General to limit file access."
    } else {
        "Untrusted = project root only, and shell or write still asks. Full still runs with your user account permissions."
    }
}

fn permissions(ui: &Ui, s: Settings) -> El {
    watch_generation(s);
    form::block(ui, s, "security-power", move |ui, s| {
        form::stack(ui)
            .child(form::hint(
                ui,
                "Choose when Remedy asks before acting. Payment and message-sending checkpoints remain required.",
            ))
            .child(bits::choice(
                ui,
                "Approvals",
                &[("ask", "Ask"), ("auto", "Auto"), ("full", "Full")],
                s.approval_mode,
            ))
            .child(w::text(ui, move || approval_hint(&s.approval_mode.get()).to_string()).variant("small"))
            .child(bits::choice(
                ui,
                "Thinking level",
                &[("off", "Off"), ("low", "Low"), ("medium", "Medium"), ("high", "High")],
                s.thinking_level,
            ))
            .child(form::hint(ui, "Also on the status bar. High = more deliberation when the model supports it."))
            .child(form::flow(
                ui,
                "Reasoning and context",
                "Reasoning effort, thinking budget, output and context limits",
                false,
                move |ui| generation(ui, s),
            ))
            .child(form::toggle(
                ui,
                "Search and read the web",
                "Allow web search and public-page fetching. This does not control cloud models or connected accounts.",
                s.web_tools,
            ))
            .child(form::flow(
                ui,
                "Advanced permissions",
                "Trust behavior and browser access",
                false,
                move |ui| advanced_permissions(ui, s),
            ))
    })
}

fn approval_hint(mode: &str) -> &'static str {
    match mode {
        "full" => "Full allows file and command actions beyond your project, with warnings. Protected credentials remain restricted.",
        "auto" => "Auto allows routine work within your project without repeated prompts. Access outside it stays restricted.",
        _ => "Ask lets you review higher-impact actions before they run.",
    }
}

fn advanced_permissions(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(bits::choice(
            ui,
            "Trust",
            &[("conservative", "Conservative"), ("balanced", "Balanced"), ("autonomous", "Autonomous")],
            s.trust_profile,
        ))
        .child(w::text(ui, move || trust_hint(&s.trust_profile.get()).to_string()).variant("small"))
        .child(form::toggle(
            ui,
            "Allow browser sign-in on this computer",
            "Lets the local WebUI obtain access to Remedy. The desktop works with this off. Only enable it if other users on this computer may access Remedy.",
            s.http_bootstrap,
        ))
}

fn trust_hint(profile: &str) -> &'static str {
    match profile {
        "conservative" => "Even Auto still asks before shell, files, and skills. Mail and payment always stop.",
        "autonomous" => "In-project high-impact work runs without extra prompts, like Auto. Mail and payment always stop.",
        _ => "Follows Approvals (Ask / Auto / Full). Mail and payment always stop.",
    }
}

/// Keeps the reasoning form on the selected scope. The all-models draft is
/// snapshotted when you switch to one model, and restored when you switch back.
fn watch_generation(s: Settings) {
    let prev = Signal::new(String::new());
    let snap = Signal::new(Value::Null);
    Effect::new(move || {
        let scope = s.gen_scope.get();
        let provider = s.provider.get();
        let model = s.model.get();
        let stored = s.model_settings.get();
        let prev_scope = prev.get_untracked().unwrap_or_default();
        if prev_scope.is_empty() {
            prev.set(scope);
            return;
        }
        if prev_scope != "model" && scope == "model" {
            snap.set(json!({
                "reasoning_effort": s.gen_effort.get_untracked().unwrap_or_default(),
                "thinking_budget_tokens": s.gen_think.get_untracked().unwrap_or_default(),
                "max_output_tokens": s.gen_out.get_untracked().unwrap_or_default(),
                "context_window_tokens": s.gen_ctx.get_untracked().unwrap_or_default(),
            }));
        }
        if scope == "model" {
            let key = logic::model_key(&provider, &model);
            let src = stored
                .get(key.as_str())
                .or_else(|| stored.get(model.trim()))
                .cloned()
                .unwrap_or_else(|| json!({}));
            apply_gen(s, &src);
        } else if prev_scope == "model" {
            let saved = snap.get_untracked().unwrap_or(Value::Null);
            if saved.is_object() {
                apply_gen(s, &saved);
            }
        }
        if prev_scope != scope {
            prev.set(scope);
        }
    });
}

fn apply_gen(s: Settings, src: &Value) {
    s.gen_effort.set_if_changed(logic::form_effort(&as_text(src, "reasoning_effort")));
    s.gen_think.set_if_changed(as_token(src, "thinking_budget_tokens"));
    s.gen_out.set_if_changed(as_token(src, "max_output_tokens"));
    s.gen_ctx.set_if_changed(as_token(src, "context_window_tokens"));
}

fn as_text(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn as_token(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => logic::positive_token_text(number_i64(n)),
        _ => String::new(),
    }
}

fn number_i64(n: &Number) -> Option<i64> {
    n.as_i64().or_else(|| n.as_f64().map(|f| f as i64))
}

fn generation(ui: &Ui, s: Settings) -> El {
    let efforts = vec![
        ("".into(), "Provider default".into()),
        ("none".into(), "None".into()),
        ("minimal".into(), "Minimal".into()),
        ("low".into(), "Low".into()),
        ("medium".into(), "Medium".into()),
        ("high".into(), "High".into()),
        ("xhigh".into(), "Extra high".into()),
    ];
    let invalid = move || generation_invalid(s);
    form::stack(ui)
        .child(bits::choice(ui, "Scope", &[("all", "All models"), ("model", "This model")], s.gen_scope))
        .child(w::text(ui, move || {
            let model = s.model.get();
            if model.trim().is_empty() {
                "This model: none selected".into()
            } else {
                format!("This model: {model}")
            }
        }).variant("small"))
        .child(bits::picker(ui, "Reasoning effort", &efforts, s.gen_effort))
        .child(form::hint(
            ui,
            "Sent as reasoning effort (OpenAI reasoning models, Grok 3 mini, Gemini), adaptive effort or a thinking budget (Claude). Models without the control ignore it.",
        ))
        .child(bits::field(ui, "Thinking budget (tokens)", s.gen_think, "Provider default"))
        .child(token_hint(ui, "thinking_budget_tokens", s.gen_think, "Claude models with budget thinking and Gemini 2.5+. Overrides the effort for those models."))
        .child(bits::field(ui, "Max output tokens", s.gen_out, "Provider default"))
        .child(token_hint(ui, "max_output_tokens", s.gen_out, "Ceiling for one model reply."))
        .child(bits::field(ui, "Context window (tokens)", s.gen_ctx, "Provider default"))
        .child(token_hint(
            ui,
            "context_window_tokens",
            s.gen_ctx,
            "How much history is kept before older turns are trimmed. Local servers stay capped at their real window.",
        ))
        .child(form::hint(ui, "This button saves reasoning and context only. The main Settings Save does not send these limits."))
        .child(
            w::button(ui, move || -> String { if s.saving.get() { "Saving…".into() } else { "Save reasoning and context".into() } }, move || {
                save_generation(s);
            })
            .variant("primary")
            .disabled(move || s.saving.get() || invalid()),
        )
}

fn token_hint(ui: &Ui, key: &'static str, value: Signal<String>, fallback: &'static str) -> El {
    w::text(ui, move || logic::token_error(key, &value.get()).unwrap_or_else(|| fallback.to_string())).variant("small")
}

fn generation_invalid(s: Settings) -> bool {
    logic::token_error("thinking_budget_tokens", &s.gen_think.get()).is_some()
        || logic::token_error("max_output_tokens", &s.gen_out.get()).is_some()
        || logic::token_error("context_window_tokens", &s.gen_ctx.get()).is_some()
        || (s.gen_scope.get() == "model" && logic::model_key(&s.provider.get(), &s.model.get()).is_empty())
}

fn save_generation(s: Settings) {
    if s.saving.get() || generation_invalid(s) {
        return;
    }
    if s.preview {
        s.banner.set("Not available in this preview.".into());
        return;
    }
    let scope = s.gen_scope.get();
    let provider = s.provider.get();
    let model = s.model.get();
    let patch = logic::generation_patch(&scope, &provider, &model, &s.gen_effort.get(), &s.gen_think.get(), &s.gen_out.get(), &s.gen_ctx.get());
    if patch.as_object().is_none_or(|m| m.is_empty()) {
        s.banner.set("Choose a model before saving limits for this model only.".into());
        return;
    }
    if scope == "model" {
        remember_model_gen(s, &patch);
    }
    s.put_patch(patch);
}

fn remember_model_gen(s: Settings, patch: &Value) {
    let Some(values) = patch.get("model_settings").and_then(|m| m.as_object()).and_then(|m| m.values().next()).cloned() else {
        return;
    };
    let key = logic::model_key(&s.provider.get(), &s.model.get());
    if key.is_empty() {
        return;
    }
    let mut stored = s.model_settings.get();
    if !stored.is_object() {
        stored = json!({});
    }
    stored[key.as_str()] = values;
    s.model_settings.set(stored);
}

fn always_ready(ui: &Ui, s: Settings) -> El {
    let copy = tray_copy();
    form::block(ui, s, "always-ready", move |ui, s| {
        let col = form::stack(ui);
        #[cfg(not(target_os = "linux"))]
        let col = col.child(form::toggle(ui, copy.launch_label, copy.launch_hint, s.launch_at_login));
        col.child(form::toggle(ui, copy.start_label, copy.start_hint, s.start_in_tray))
            .child(locked_close(ui, copy.close_label, copy.close_hint))
            .child(form::toggle(
                ui,
                "Don't warn when quitting (server stops)",
                "Opt-in only. Quit fully stops the local API; use Switch to WebUI or hide-to-tray to keep the server running.",
                s.skip_quit_warn,
            ))
    })
}

#[allow(dead_code)]
struct TrayCopy {
    launch_label: &'static str,
    launch_hint: &'static str,
    start_label: &'static str,
    start_hint: &'static str,
    close_label: &'static str,
    close_hint: &'static str,
}

fn tray_copy() -> TrayCopy {
    #[cfg(target_os = "linux")]
    {
        return TrayCopy {
            launch_label: "",
            launch_hint: "",
            start_label: "Start minimized",
            start_hint: "Off = window opens normally (recommended). On = start minimized to the taskbar (WSLg has no tray).",
            close_label: "Close window (✕) minimizes to the taskbar",
            close_hint: "Always on: ✕ keeps Remedy running and leaves a taskbar button (WSLg has no tray). Fully stop from the app menu → Quit.",
        };
    }
    #[cfg(target_os = "macos")]
    {
        return TrayCopy {
            launch_label: "Start at login",
            launch_hint: "Writes a Launch Agent in your account (~/Library/LaunchAgents) so Remedy opens when you log in. Drag the app to Applications first if you opened it from a disk image.",
            start_label: "Start hidden in the menu bar",
            start_hint: "Off = window opens normally (recommended). On = only a menu bar extra until you click it. Independent of \"Start at login\".",
            close_label: "Close window (✕) always hides to the menu bar",
            close_hint: "Always on for the always-ready partner: the OS ✕ hides Remedy to the menu bar extra and keeps the local API running. Fully stop only from Quit in the menu bar.",
        };
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        TrayCopy {
            launch_label: "Start with Windows",
            launch_hint: "Uses the Windows Startup folder (Settings → Apps → Startup) — not the registry Run key.",
            start_label: "Start hidden in tray",
            start_hint: "Off = window opens normally (recommended). On = only a tray icon until you click it. Independent of \"Start with Windows\".",
            close_label: "Close window (✕) always hides to tray",
            close_hint: "Always on for the always-ready partner: the OS ✕ / Alt+F4 hides Remedy to the system tray and keeps the local API running. Fully stop only from tray Quit.",
        }
    }
}

fn locked_close(ui: &Ui, title: &'static str, hint: &'static str) -> El {
    let on = Signal::new(true);
    Effect::new(move || {
        if !on.get() {
            on.set(true);
        }
    });
    form::stack(ui)
        .child(w::switch(ui, title.to_string(), on).disabled(true))
        .child(form::hint(ui, hint.to_string()))
}

fn tool_process(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "tool-process", move |ui, s| {
        form::stack(ui)
            .child(form::hint(
                ui,
                "How much Process detail to show under replies — same list, more depth. The chat answer is always complete.",
            ))
            .child(bits::choice(ui, "Process detail", &[("off", "Min"), ("medium", "Med"), ("full", "Full")], s.tool_process))
            .child(w::text(ui, move || tool_hint(&s.tool_process.get()).to_string()).variant("small"))
    })
}

fn tool_hint(id: &str) -> &'static str {
    match id {
        "medium" => "Grouped runs, path/command line, expand for short result.",
        "full" => "Every step with full args and results (live auto-scroll).",
        _ => "Compact chips and counts — no duplicate progress chips.",
    }
}


fn updates_panel(ui: &Ui, _s: Settings) -> El {
    let notes = crate::update::last_notes_signal();
    let channel = crate::update::channel_signal();
    let picker = bits::picker(
        ui,
        "Update channel",
        &[
            ("stable".into(), "Stable".into()),
            ("preview".into(), "Preview".into()),
        ],
        channel,
    );
    let last = Signal::new(crate::update::channel());
    let picker2 = picker.clone();
    let picker = picker.bind(move || {
        let v = crate::update::channel_signal().get();
        if last.get() != v {
            last.set(v.clone());
            crate::update::set_channel(&v);
        }
        let _ = &picker2;
    });
    let previous = crate::update::previous_version();
    let rollback = if let Some(ver) = previous.clone() {
        form::action(ui, &format!("Restore {ver}"), "quiet", || {
            crate::update::press_rollback();
        })
    } else {
        form::hint(ui, "No previous build is kept on this PC.")
    };
    let ready = crate::update::ready_signal().try_get().unwrap_or_default();
    let restart = if ready.is_empty() {
        form::hint(ui, "Updates download quietly. Restart appears here and in the top notice when a build is ready.")
    } else {
        form::action(ui, &format!("Restart to install {ready}"), "primary", || {
            crate::update::press_apply();
        })
    };
    form::stack(ui)
        .child(form::label(ui, "Updates"))
        .child(w::text(ui, move || {
            let n = notes.get();
            if n.trim().is_empty() {
                "No update notes yet.".into()
            } else {
                n
            }
        }).variant("small"))
        .child(picker)
        .child(form::hint(
            ui,
            "Preview uses the same signed feed until a preview channel is published.",
        ))
        .child(rollback)
        .child(restart)
}

fn advanced(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "advanced", move |ui, s| {
        let knobs = bits::rebuild_on(
            ui,
            move || {
                let _ = s.advanced_schema.get();
            },
            move |ui| knob_groups(ui, s),
        );
        form::stack(ui)
            .child(updates_panel(ui, s))
            .child(form::toggle(ui, "Allow skill creation (learning loop)", "", s.allow_skills))
            .child(w::text(ui, move || format!("Learning auto-approve threshold ({:.2})", s.auto_approve.get())).variant("strong"))
            .child(w::slider(ui, "Learning auto-approve threshold", s.auto_approve, 0.0, 1.0, 0.05))
            .child(bits::picker(
                ui,
                "Log level",
                &[
                    ("DEBUG".into(), "DEBUG".into()),
                    ("INFO".into(), "INFO".into()),
                    ("WARNING".into(), "WARNING".into()),
                    ("ERROR".into(), "ERROR".into()),
                ],
                s.log_level,
            ))
            .child(form::toggle(ui, "Sarcasm mode (tone flag)", "", s.sarcasm))
            .child(form::toggle(ui, "Claimidx public ledger (submit claims)", "", s.claimidx))
            .child(form::hint(
                ui,
                "On by default when ClaimIDX is on (Claimidx 0.7+ submits by default). Turn off only if you want a local Claimidx home with no public submits. The Privacy ClaimIDX switch turns the whole host off.",
            ))
            .child(form::hint(
                ui,
                "Advanced knobs only — defaults keep full owner power. Skill creation stays on so Remedy can improve.",
            ))
            .child(knobs)
    })
}

fn knob_groups(ui: &Ui, s: Settings) -> El {
    let specs = parse_schema(&s.advanced_schema.get());
    if specs.is_empty() {
        return form::hint(
            ui,
            "Organism and token-budget knobs also live in config.toml under [advanced], or via remedy settings set.",
        );
    }
    let mut col = form::stack(ui);
    let mut last = String::new();
    for spec in specs {
        if spec.group != last {
            last = spec.group.clone();
            col = col.child(form::label(ui, &spec.group));
        }
        col = col.child(knob_row(ui, s, spec));
    }
    col
}

struct Spec {
    key: String,
    kind: String,
    default_bool: bool,
    default_num: f64,
    min: Option<f64>,
    max: Option<f64>,
    unit: String,
    group: String,
    help: String,
}

fn parse_schema(v: &Value) -> Vec<Spec> {
    let Some(rows) = v.as_array() else { return Vec::new() };
    rows.iter().filter_map(parse_knob).collect()
}

fn parse_knob(row: &Value) -> Option<Spec> {
    let key = row.get("key").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    if key.is_empty() {
        return None;
    }
    let raw_kind = row.get("kind").and_then(|v| v.as_str()).unwrap_or("float");
    let kind = match raw_kind {
        "bool" | "boolean" => "bool",
        "int" | "integer" => "int",
        _ => "float",
    };
    let default = row.get("default");
    let default_bool = default.and_then(|v| v.as_bool()).unwrap_or(false);
    let default_num = default.and_then(|v| v.as_f64()).unwrap_or(0.0);
    let group = row.get("group").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    Some(Spec {
        key,
        kind: kind.to_string(),
        default_bool,
        default_num,
        min: row.get("min").and_then(|v| v.as_f64()),
        max: row.get("max").and_then(|v| v.as_f64()),
        unit: row.get("unit").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        group: if group.is_empty() { "Other".into() } else { group },
        help: row.get("help").and_then(|v| v.as_str()).unwrap_or("").to_string(),
    })
}

fn knob_row(ui: &Ui, s: Settings, spec: Spec) -> El {
    let title = spec.key.replace('_', " ");
    let title = if spec.unit.is_empty() { title } else { format!("{title} ({})", spec.unit) };
    if spec.kind == "bool" {
        let on = Signal::new(s.advanced.get().get(&spec.key).and_then(|v| v.as_bool()).unwrap_or(spec.default_bool));
        bind_bool(s, spec.key.clone(), on);
        let mut col = form::stack(ui).child(form::toggle(ui, &title, "", on));
        if !spec.help.is_empty() {
            col = col.child(form::hint(ui, spec.help));
        }
        return col;
    }
    let current = s.advanced.get().get(&spec.key).and_then(|v| v.as_f64()).unwrap_or(spec.default_num);
    if let (Some(min), Some(max)) = (spec.min, spec.max) {
        if max > min {
            let n = Signal::new(current.clamp(min, max));
            let step = if spec.kind == "int" { 1.0 } else { ((max - min) / 100.0).max(0.01) };
            bind_num(s, spec.key.clone(), spec.kind.clone(), n);
            let mut col = form::stack(ui)
                .child(form::label(ui, &title))
                .child(w::slider(ui, title.clone(), n, min, max, step));
            if !spec.help.is_empty() {
                col = col.child(form::hint(ui, spec.help));
            }
            return col;
        }
    }
    let text = Signal::new(format_num(current, &spec.kind));
    bind_text_num(s, spec.key.clone(), spec.kind.clone(), text);
    let mut col = form::stack(ui).child(bits::field(ui, &title, text, ""));
    if !spec.help.is_empty() {
        col = col.child(form::hint(ui, spec.help));
    }
    col
}

fn format_num(n: f64, kind: &str) -> String {
    if kind == "int" { format!("{}", n.round() as i64) } else { format!("{n}") }
}

fn bind_bool(s: Settings, key: String, on: Signal<bool>) {
    let key_write = key.clone();
    Effect::new(move || {
        let want = on.get();
        let adv = s.advanced.get_untracked().unwrap_or_else(|_| json!({}));
        if adv.get(&key_write).and_then(|v| v.as_bool()) == Some(want) {
            return;
        }
        write_advanced(s, &key_write, json!(want));
    });
    Effect::new(move || {
        let adv = s.advanced.get();
        if let Some(v) = adv.get(&key).and_then(|v| v.as_bool()) {
            on.set_if_changed(v);
        }
    });
}

fn bind_num(s: Settings, key: String, kind: String, n: Signal<f64>) {
    let key_write = key.clone();
    Effect::new(move || {
        let want = n.get();
        let adv = s.advanced.get_untracked().unwrap_or_else(|_| json!({}));
        if adv.get(&key_write).and_then(|v| v.as_f64()).is_some_and(|cur| (cur - want).abs() < 1e-6) {
            return;
        }
        write_advanced(s, &key_write, num_value(&kind, want));
    });
    Effect::new(move || {
        let adv = s.advanced.get();
        if let Some(v) = adv.get(&key).and_then(|v| v.as_f64()) {
            let cur = n.get_untracked().unwrap_or(v);
            if (cur - v).abs() >= 1e-6 {
                n.set(v);
            }
        }
    });
}

fn bind_text_num(s: Settings, key: String, kind: String, text: Signal<String>) {
    let mut armed = false;
    Effect::new(move || {
        let raw = text.get();
        if !armed {
            armed = true;
            return;
        }
        let Ok(n) = raw.trim().parse::<f64>() else { return };
        let adv = s.advanced.get_untracked().unwrap_or_else(|_| json!({}));
        if adv.get(&key).and_then(|v| v.as_f64()).is_some_and(|cur| (cur - n).abs() < 1e-6) {
            return;
        }
        write_advanced(s, &key, num_value(&kind, n));
    });
}

fn num_value(kind: &str, n: f64) -> Value {
    if kind == "int" { json!(n.round() as i64) } else { json!((n * 1000.0).round() / 1000.0) }
}

fn write_advanced(s: Settings, key: &str, val: Value) {
    let mut adv = s.advanced.get_untracked().unwrap_or_else(|_| json!({}));
    if !adv.is_object() {
        adv = json!({});
    }
    adv[key] = val;
    s.advanced.set(adv);
}

fn license(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "license", move |ui, s| {
        form::stack(ui)
            .child(w::text(ui, "Apache License 2.0 — free to use, modify, and redistribute, including commercially.").variant("strong"))
            .child(form::hint(
                ui,
                "Copyright (c) 2025–2026 Ahmi Darrow. Full terms are below and in the repo LICENSE. You are responsible for how you use Remedy, including sites and accounts you point it at.",
            ))
            .child(form::label(ui, "License"))
            .child(legal(ui, s.license_all, logic::license_text()))
            .child(w::link(ui, "Open Security & data (includes bootstrap / power notes)", || {
                bits::open_http("https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/04-security-and-data.md");
            }))
            .child(form::label(ui, "Third-party notices"))
            .child(legal(ui, s.notices_all, logic::third_party_text()))
    })
}

fn legal(ui: &Ui, all: Signal<bool>, full: &'static str) -> El {
    let total = full.lines().count();
    let head = logic::first_lines(full, 400);
    let body = show(ui, move || all.get(), {
        let ui = ui.clone();
        move || legal_pre(&ui, full)
    })
    .fallback({
        let ui = ui.clone();
        let head = head;
        move || legal_pre(&ui, &head)
    })
    .el();
    let more = show(ui, move || !all.get() && total > 400, {
        let ui = ui.clone();
        move || w::button(&ui, format!("Show all {total} lines"), move || all.set(true)).variant("quiet")
    })
    .el();
    form::stack(ui).child(body).child(more)
}

fn legal_pre(ui: &Ui, text: &str) -> El {
    w::scroll(ui)
        .child(w::text(ui, text.to_string()).variant("code"))
        .layout(|l| {
            l.max_height = Length::Px(280.0);
            l.grow = 0.0;
            l.width = Length::Percent(1.0);
        })
}

fn help(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "help", move |ui, _s| {
        let mut keys = form::stack(ui);
        for (chord, _scope, action) in logic::HOTKEYS {
            keys = keys.child(
                w::row(ui)
                    .justify(remedy_ui::layout::Justify::SpaceBetween)
                    .gap(form::gap(ui))
                    .fill_width()
                    .child(w::text(ui, *chord).variant("code"))
                    .child(w::text(ui, *action).variant("small")),
            );
        }
        form::stack(ui)
            .child(form::hint(ui, "Enter sends · Shift+Enter new line · F1 opens Help"))
            .child(form::action(ui, "Open Help", "primary", || bits::open_http(HELP_HOME)))
            .child(help_links(ui))
            .child(keys)
            .child(form::hint(
                ui,
                "Connect a provider above to chat. Plan mode explores without editing files; Build mode can change your project. Data stays in ~/.remedy on this machine.",
            ))
    })
}

pub(crate) const HELP_HOME: &str = "https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md";

fn help_links(ui: &Ui) -> El {
    let pages = [
        ("02-first-run", "Setup"),
        ("03-providers-and-auth", "Providers"),
        ("14-visual-decoder", "Vision"),
        ("09-troubleshooting", "Troubleshoot"),
        ("13-whats-new", "What's new"),
    ];
    let mut row = w::row(ui).gap(form::gap(ui)).fill_width();
    for (id, label) in pages {
        let url = format!("https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/{id}.md");
        row = row.child(form::action(ui, label, "quiet", move || bits::open_http(&url)));
    }
    row
}

fn about(ui: &Ui, s: Settings) -> El {
    let checking = Signal::new(false);
    let info = Signal::new(Value::Null);
    let status = Signal::new(String::new());
    form::block(ui, s, "about", move |ui, s| {
        let update = show(
            ui,
            move || info.get().get("update_available").and_then(|v| v.as_bool()).unwrap_or(false),
            {
                let ui = ui.clone();
                move || {
                    form::stack(&ui)
                        .child(form::action(&ui, "Update & Relaunch", "primary", move || open_installer(s, &info.get())))
                        .child(form::hint(&ui, "Opens the installer page. This shell does not download or relaunch by itself."))
                }
            },
        )
        .el();
        form::stack(ui)
            .child(crate::logo::empty_mark(ui))
            .child(form::hint(
                ui,
                "Personal AI partner on this machine — chat, files, terminal, browser rail, computer use. Continuity under ~/.remedy; your model keys stay yours. Not a medical product.",
            ))
            .child(form::notice(ui, "ok", "My name is Ahmi, I hope you enjoy my Remedy."))
            .child(w::text(ui, move || format!("Version  {}", s.version.get())).variant("small"))
            .child(w::text(ui, "Config  ~/.remedy/config.toml").variant("code"))
            .child(form::action(ui, "Switch to WebUI…", "quiet", move || {
                let url = format!("{}/", runtime::origin().trim_end_matches('/'));
                bits::open_http(&url);
                s.banner.set("Opened the local WebUI. Show Remedy from the tray to return.".into());
            }))
            .child(form::hint(ui, "Opens the local WebUI in your browser. Tray → Show Remedy returns to the desktop."))
            .child(
                w::button(ui, move || -> String { if checking.get() { "Checking for updates…".into() } else { "Check for Updates".into() } }, move || {
                    check_updates(s, checking, info, status);
                })
                .variant("quiet")
                .disabled(move || checking.get()),
            )
            .child(w::text(ui, move || status.get()).variant("small"))
            .child(update)
            .child(form::action(ui, "Report an issue on GitHub…", "quiet", move || bits::open_http(&issue_url(&s.version.get()))))
            .child(w::link(ui, "github.com/AhmiDarrow/RemedyAI/issues", || {
                bits::open_http("https://github.com/AhmiDarrow/RemedyAI/issues");
            }))
    })
}

fn check_updates(s: Settings, checking: Signal<bool>, info: Signal<Value>, status: Signal<String>) {
    if checking.get() {
        return;
    }
    if s.preview {
        status.set("Not available in this preview.".into());
        return;
    }
    checking.set(true);
    status.set("Contacting GitHub releases…".into());
    let ver = s.version.get();
    super::http::request(s, "GET", format!("updates/check?current={ver}"), None, move |out| {
        checking.set(false);
        match out {
            Ok(v) => {
                let latest = v.get("latest_desktop").and_then(|x| x.as_str()).unwrap_or("");
                let err = v.get("error").and_then(|x| x.as_str()).unwrap_or("");
                let available = v.get("update_available").and_then(|x| x.as_bool()).unwrap_or(false);
                if !err.is_empty() && latest.is_empty() {
                    status.set(err.to_string());
                } else if available {
                    status.set(format!("Update available: v{ver} → v{latest}"));
                } else {
                    status.set("You're up to date.".into());
                }
                info.set(v);
            }
            Err(e) => status.set(if e.is_empty() { "Could not check for updates.".into() } else { e }),
        }
    });
}

fn open_installer(s: Settings, info: &Value) {
    let url = info.get("installer_url").and_then(|x| x.as_str()).filter(|u| !u.is_empty())
        .or_else(|| info.get("release_url").and_then(|x| x.as_str()).filter(|u| !u.is_empty()))
        .unwrap_or("");
    if url.is_empty() {
        s.banner.set("No installer link in the update response.".into());
        return;
    }
    bits::open_http(url);
    s.banner.set("Opened the installer page.".into());
}

fn issue_url(version: &str) -> String {
    let ver = version.trim().trim_start_matches('v');
    let title = query_encode(&format!("[Desktop v{ver}] "));
    let body = query_encode(&format!(
        "## What happened\n\n(Describe the issue…)\n\n## Steps to reproduce\n\n1. \n\n## Expected vs actual\n\n\n## Environment\n\n- Remedy Desktop: v{ver}\n"
    ));
    format!("https://github.com/AhmiDarrow/RemedyAI/issues/new?title={title}&body={body}&labels=bug")
}

fn query_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}
