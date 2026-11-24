//! What the chat column does: send, steer, queue, slash commands, files, voice.

use super::api;
use super::logic::{
    self, MAX_ATTACHMENTS, name_size_key, project_key, rehome_order, should_confirm_new_turn,
    strip_markdown_for_speech,
};
use super::{
    Approval, Attachment, Confirm, Depth, LibraryChip, LifeCard, PendingSend, PlanCard, SkillRow,
    StreamJob, SubmitKind, TodoItem, Usage, VoicePrefs, now_ms, put_mic, take_mic, with_host,
    with_ui,
};
use crate::runtime::{self, Client};
use crate::sse;
use crate::studio::{ChatMessage, Studio, WorkMode};
use crate::work::{self, WorkStep};
use remedy_ui::host::FileDialog;
use remedy_ui::net::{Multipart, Request};
use remedy_ui::playback::{
    MediaSource, Microphone, MicrophoneOptions, MicrophoneState, Player, PlayerOptions,
};
use remedy_ui::reactive::{Cancel, waker};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};
use std::time::Duration;

pub fn submit(
    studio: &Studio,
    text: String,
    attachments: Vec<Attachment>,
    kind: SubmitKind,
    check_confirm: bool,
) {
    let text = text.trim().to_string();
    if text.is_empty() && attachments.is_empty() {
        return;
    }
    if text.starts_with('/') && attachments.is_empty() {
        if studio.streaming.get() {
            studio
                .depth
                .notice
                .set("Commands wait until this turn finishes.".into());
            return;
        }
        run_slash(studio, &text);
        return;
    }
    let sid = ensure_session(studio);
    let Some(sid) = sid else {
        return;
    };
    let busy = studio.depth.session_running(&sid)
        || (studio.streaming.get() && studio.active.get().as_deref() == Some(sid.as_str()));
    if busy {
        match kind {
            SubmitKind::After => {
                studio.depth.push_queued(text, "after", attachments);
                studio
                    .depth
                    .notice
                    .set("Queued until this turn ends.".into());
            }
            SubmitKind::Interrupt => interrupt(studio, &sid, text, attachments),
            SubmitKind::Send | SubmitKind::Steer => {
                if attachments.is_empty() {
                    steer(studio, &sid, &text);
                } else {
                    studio.depth.push_queued(text, "after", attachments);
                    studio
                        .depth
                        .notice
                        .set("Files need their own turn. Queued until this one ends.".into());
                }
            }
        }
        return;
    }
    if check_confirm && should_confirm_new_turn(studio.depth.running_count()) {
        studio.depth.ask_concurrent(PendingSend {
            text,
            attachments,
            kind,
        });
        return;
    }
    begin_turn(studio, &sid, text, attachments);
}

pub fn continue_pending(studio: &Studio) {
    let pending = studio.depth.pending.get();
    studio.depth.pending.set(None);
    studio.depth.concurrent_open.set(false);
    if let Some(p) = pending {
        submit(studio, p.text, p.attachments, p.kind, false);
    }
}

pub fn after_finish(studio: &Studio) {
    if studio.depth.queue.get().is_empty() {
        return;
    }
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    let _ = std::thread::Builder::new()
        .name("remedy-queue".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(40));
            let _ = wake.post(move || pump(&studio));
        });
}

fn pump(studio: &Studio) {
    if studio.streaming.get() {
        return;
    }
    let next = {
        let q = studio.depth.queue.get();
        q.first().cloned()
    };
    let Some(next) = next else {
        return;
    };
    studio.depth.queue.update(|q| {
        if !q.is_empty() {
            q.remove(0);
        }
    });
    let sid = studio.active.get();
    if sid.is_none() {
        return;
    }
    begin_turn(
        studio,
        sid.as_deref().unwrap_or(""),
        next.text,
        next.attachments,
    );
}

fn interrupt(studio: &Studio, sid: &str, text: String, attachments: Vec<Attachment>) {
    if let Some(client) = studio.runtime() {
        runtime::abort_with(&client, sid, "supersede", |_| {});
    }
    studio.depth.insert_front(text, "interrupt", attachments);
    studio.finish_assistant(None);
}

fn steer(studio: &Studio, sid: &str, text: &str) {
    studio.depth.remember_prompt(text);
    push_user(studio, sid, text);
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = format!("sessions/{sid}/steer");
    let body = json!({ "message": text });
    api::spawn_json(&client, Request::post(&path).json(&body), |_| {});
}

fn begin_turn(studio: &Studio, sid: &str, text: String, attachments: Vec<Attachment>) {
    if sid.is_empty() {
        return;
    }
    if !text.is_empty() {
        studio.depth.remember_prompt(&text);
    }
    if !text.is_empty() || !attachments.is_empty() {
        let line = if text.is_empty() {
            attachments
                .iter()
                .map(|a| a.name.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        } else {
            text.clone()
        };
        push_user(studio, sid, &line);
        retitle(studio, sid, &line);
    }
    studio.depth.attachments.set(Vec::new());
    if studio.local_only || studio.runtime().is_none() {
        push_system(
            studio,
            "The mind is not connected. Start this chat once it is.",
        );
        return;
    }
    studio.clear_live();
    studio.streaming.set(true);
    studio.depth.remote_busy.set(false);
    studio.depth.stall_hidden.set(false);
    studio.depth.usage_run.set(Usage::default());
    let files = attachments.clone();
    studio.depth.map_job(sid, |job| {
        *job = StreamJob::running(sid.to_string());
        job.outgoing = text.clone();
        job.outgoing_files = !files.is_empty();
        job.held = files.clone();
    });
    let client = studio.runtime().unwrap();
    if attachments.is_empty() {
        stream(studio, &client, sid, &text, Vec::new());
        return;
    }
    let studio = studio.clone();
    let sid = sid.to_string();
    let text2 = text.clone();
    let Ok(wake) = waker() else {
        studio.streaming.set(false);
        return;
    };
    let _ = std::thread::Builder::new()
        .name("remedy-attach".into())
        .spawn(move || {
            let mut metas = Vec::new();
            for file in &attachments {
                if file.pending {
                    continue;
                }
                match read_capped(&file.path) {
                    Ok(bytes) => {
                        let body = json!({
                            "filename": file.name,
                            "content_type": file.mime,
                            "data_base64": api::b64_encode(&bytes),
                        });
                        let path = format!("sessions/{sid}/attachments");
                        let cancel = Cancel::new();
                        match client
                            .http()
                            .send_blocking(Request::post(&path).json(&body), &cancel)
                        {
                            Ok(r) => {
                                if let Ok(v) = r.json::<Value>() {
                                    metas.push(attachment_meta(&v, file));
                                } else {
                                    metas.push(attachment_meta(&Value::Null, file));
                                }
                            }
                            Err(e) => {
                                let msg = e.to_string();
                                let _ = wake.post({
                                    let studio = studio.clone();
                                    move || {
                                        studio.streaming.set(false);
                                        studio.depth.finish_job(&sid, "error");
                                        studio.depth.notice.set(msg);
                                        studio
                                            .depth
                                            .attachments
                                            .set(attachments_back(&studio, &sid));
                                    }
                                });
                                return;
                            }
                        }
                    }
                    Err(e) => {
                        let _ = wake.post({
                            let studio = studio.clone();
                            let sid = sid.clone();
                            move || {
                                studio.streaming.set(false);
                                studio.depth.finish_job(&sid, "error");
                                studio.depth.notice.set(e);
                            }
                        });
                        return;
                    }
                }
            }
            let _ = wake.post(move || stream(&studio, &client, &sid, &text2, metas));
        });
}

fn attachments_back(studio: &Studio, sid: &str) -> Vec<Attachment> {
    studio.depth.job(sid).map(|j| j.held).unwrap_or_default()
}

fn attachment_meta(v: &Value, file: &Attachment) -> Value {
    if v.get("name").is_some() || v.get("path").is_some() {
        return v.clone();
    }
    json!({
        "name": file.name,
        "path": file.path,
        "mime": file.mime,
        "size": file.size,
        "is_image": file.is_image,
        "is_text": file.is_text,
    })
}

fn stream(studio: &Studio, client: &Client, sid: &str, text: &str, attachments: Vec<Value>) {
    let studio_cb = studio.clone();
    let sid_cb = sid.to_string();
    let Ok(wake) = waker() else {
        studio.streaming.set(false);
        return;
    };
    runtime::stream_message(
        client,
        sid,
        text,
        &studio.model.get(),
        &studio.provider.get(),
        studio.mode.get(),
        &attachments,
        move |kind, data| {
            let studio = studio_cb.clone();
            let sid = sid_cb.clone();
            let _ = wake.post(move || studio.on_frame_for(&sid, &kind, &data));
        },
    );
}

fn run_slash(studio: &Studio, text: &str) {
    let stripped = text.trim().to_ascii_lowercase();
    studio.depth.remember_prompt(text);
    if stripped == "/export" || stripped == "/export-session" {
        export_active(studio);
        return;
    }
    if stripped == "/import-session"
        || stripped == "/session-import"
        || stripped.starts_with("/import-session ")
    {
        import_session(studio);
        return;
    }
    if stripped == "/new" && studio.runtime().is_none() {
        studio.add();
        push_system(studio, "New session.");
        return;
    }
    let Some(client) = studio.runtime() else {
        push_user(studio, &ensure_session(studio).unwrap_or_default(), text);
        push_system(
            studio,
            "The mind is not connected. Start this chat once it is.",
        );
        return;
    };
    let sid = ensure_session(studio).unwrap_or_default();
    if sid.is_empty() {
        return;
    }
    push_user(studio, &sid, text);
    let path = format!("sessions/{sid}/command");
    let body = json!({ "command": text });
    let studio = studio.clone();
    let command = text.to_string();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::post(&path).json(&body), move |out| {
        let _ = wake.post(move || match out {
            Ok(v) => {
                let reply = api::str_field(&v, "text");
                if !reply.is_empty() {
                    push_system(&studio, &reply);
                }
                let action = api::str_field(&v, "action");
                if action == "new_session" || command.trim() == "/new" {
                    studio.add();
                }
                if action == "reset_session" {
                    studio.messages.set(Vec::new());
                    if !reply.is_empty() {
                        push_system(&studio, &reply);
                    }
                }
                let sid = api::str_field(&v, "session_id");
                if !sid.is_empty() {
                    studio.select(&sid);
                }
            }
            Err(e) => push_system(&studio, &e),
        });
    });
}

fn ensure_session(studio: &Studio) -> Option<String> {
    if let Some(id) = studio.active.get() {
        return Some(id);
    }
    studio.add();
    studio.active.get()
}

fn push_user(studio: &Studio, sid: &str, text: &str) {
    let id = studio.alloc_msg_id();
    studio.messages.update(|m| {
        m.push(ChatMessage::plain(format!("local-u-{id}"), "user", text));
    });
    let sid = sid.to_string();
    studio.sessions.update(|list| {
        if let Some(s) = list.iter_mut().find(|s| s.id == sid) {
            s.message_count = s.message_count.saturating_add(1);
        }
    });
}

fn push_system(studio: &Studio, text: &str) {
    let id = studio.alloc_msg_id();
    studio.messages.update(|m| {
        m.push(ChatMessage::plain(
            format!("local-sys-{id}"),
            "system",
            text,
        ));
    });
}

fn retitle(studio: &Studio, sid: &str, text: &str) {
    let sid = sid.to_string();
    let text = text.to_string();
    studio.sessions.update(|list| {
        if let Some(s) = list.iter_mut().find(|s| s.id == sid) {
            if s.title == "New chat" || s.title.starts_with("New chat ") {
                s.title = if text.chars().count() > 42 {
                    text.chars().take(40).collect::<String>() + "…"
                } else {
                    text.clone()
                };
            }
        }
    });
}

pub fn apply_frame(studio: &Studio, sid: &str, kind: &str, data: &str) {
    let active = studio.active.get().as_deref() == Some(sid);
    studio.depth.map_job(sid, |job| {
        job.last_event_ms = now_ms();
        if job.status != "running" && !matches!(kind, "done" | "end" | "aborted" | "error") {
            job.status = "running".into();
        }
    });
    if active {
        studio.depth.stall_hidden.set(false);
    }
    match kind {
        "token" | "text" => {
            let bit = sse::token_text(data);
            if bit.is_empty() {
                return;
            }
            studio.depth.map_job(sid, |job| job.partial.push_str(&bit));
            if active {
                studio.partial.update(|p| p.push_str(&bit));
                studio.streaming.set(true);
            }
        }
        "thinking" => {
            studio
                .depth
                .map_job(sid, |job| work::on_thinking(&mut job.thinking, data));
            if active {
                studio
                    .live_thinking
                    .update(|buf| work::on_thinking(buf, data));
            }
        }
        "tool_call" => {
            let partial = studio.depth.job(sid).map(|j| j.partial).unwrap_or_default();
            studio.depth.map_job(sid, |job| {
                work::on_tool_call(&mut job.steps, &mut job.breaks, &partial, data);
            });
            if active {
                let partial = studio.partial.get();
                let mut steps = studio.live_steps.get();
                let mut breaks = studio.live_breaks.get();
                work::on_tool_call(&mut steps, &mut breaks, &partial, data);
                studio.live_steps.set(steps);
                studio.live_breaks.set(breaks);
            }
        }
        "tool_result" => {
            studio
                .depth
                .map_job(sid, |job| work::on_tool_result(&mut job.steps, data));
            if active {
                studio
                    .live_steps
                    .update(|steps| work::on_tool_result(steps, data));
            }
        }
        "step" => {
            studio
                .depth
                .map_job(sid, |job| work::on_step(&mut job.steps, data));
            if active {
                studio.live_steps.update(|steps| work::on_step(steps, data));
            }
        }
        "start" => {
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                let id = api::str_field(&v, "request_id");
                if !id.is_empty() {
                    studio.depth.map_job(sid, |job| job.request_id = id);
                }
            }
        }
        "draft" => {
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                let mut id = api::str_field(&v, "message_id");
                if id.is_empty() {
                    id = api::str_field(&v, "id");
                }
                if !id.is_empty() {
                    studio.depth.map_job(sid, |job| job.message_id = id.clone());
                    if active {
                        studio.live_message_id.set(Some(id));
                    }
                }
            }
        }
        "usage" => {
            if active {
                if let Ok(v) = serde_json::from_str::<Value>(data) {
                    let usage = Usage {
                        input: api::u64_field(&v, "input_tokens")
                            .max(api::u64_field(&v, "prompt_tokens")),
                        output: api::u64_field(&v, "output_tokens")
                            .max(api::u64_field(&v, "completion_tokens")),
                        cost: v
                            .get("cost")
                            .and_then(|c| c.as_f64())
                            .or_else(|| v.get("cost_usd").and_then(|c| c.as_f64())),
                    };
                    let mut session = studio.depth.usage_session.get();
                    session.input = session.input.saturating_add(usage.input);
                    session.output = session.output.saturating_add(usage.output);
                    if let Some(c) = usage.cost {
                        session.cost = Some(session.cost.unwrap_or(0.0) + c);
                    }
                    studio.depth.usage_run.set(usage);
                    studio.depth.usage_session.set(session);
                }
            }
        }
        "todos" => {
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                let todos = parse_todos(&v);
                studio.depth.map_job(sid, |job| job.todos = todos.clone());
                if active {
                    studio.depth.todos.set(todos);
                }
            }
        }
        "progress" => {
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                let raw = v
                    .get("percent")
                    .or_else(|| v.get("value"))
                    .or_else(|| v.get("progress"))
                    .and_then(|n| n.as_f64())
                    .unwrap_or(0.0) as f32;
                let p = if raw > 1.0 { raw / 100.0 } else { raw };
                let p = p.clamp(0.0, 1.0);
                studio.depth.map_job(sid, |job| job.progress = Some(p));
                if active {
                    studio.depth.progress.set(Some(p));
                }
            }
        }
        "library_suggest" => {
            if active {
                if let Ok(v) = serde_json::from_str::<Value>(data) {
                    let chip = parse_library(&v);
                    if chip.id.is_empty() && chip.name.is_empty() {
                        return;
                    }
                    studio.depth.library.set(Some(chip));
                }
            }
        }
        "reaction" => {
            if let Ok(v) = serde_json::from_str::<Value>(data) {
                let id = api::str_field(&v, "message_id");
                let reactions = v.get("reactions");
                let user = {
                    let top = api::str_field(&v, "user");
                    if !top.is_empty() {
                        top
                    } else {
                        reactions
                            .and_then(|r| r.get("user"))
                            .and_then(|t| t.as_str())
                            .unwrap_or("")
                            .to_string()
                    }
                };
                let agent = {
                    let top = api::str_field(&v, "agent");
                    if !top.is_empty() {
                        top
                    } else {
                        reactions
                            .and_then(|r| r.get("agent"))
                            .and_then(|t| t.as_str())
                            .unwrap_or("")
                            .to_string()
                    }
                };
                let has_user =
                    v.get("user").is_some() || reactions.and_then(|r| r.get("user")).is_some();
                let has_agent =
                    v.get("agent").is_some() || reactions.and_then(|r| r.get("agent")).is_some();
                if active && !id.is_empty() {
                    if has_user && studio.live_message_id.get().as_deref() == Some(id.as_str()) {
                        studio.live_user_reaction.set(user.clone());
                    }
                    studio.messages.update(|list| {
                        if let Some(m) = list.iter_mut().find(|m| m.id == id) {
                            if has_user {
                                m.user_reaction = user;
                            }
                            if has_agent {
                                m.agent_reaction = agent;
                            }
                        }
                    });
                }
            }
        }
        "life_task" => {
            if active {
                if let Ok(v) = serde_json::from_str::<Value>(data) {
                    studio.depth.life.set(Some(parse_life(&v)));
                }
            }
        }
        "steered" => {
            if active {
                studio.streaming.set(true);
                studio.depth.remote_busy.set(true);
                studio.depth.notice.set("Sent to the live turn.".into());
            }
        }
        "queued" => {
            if active {
                let held = studio.depth.job(sid).map(|j| j.held).unwrap_or_default();
                if !held.is_empty() {
                    studio.depth.attachments.set(held);
                }
                studio
                    .depth
                    .notice
                    .set("This session is still working. The files stayed on the composer.".into());
                studio.streaming.set(true);
                studio.depth.remote_busy.set(true);
            }
        }
        "error" => {
            if work::is_step_error(data) {
                studio
                    .depth
                    .map_job(sid, |job| work::on_step_error(&mut job.steps, data));
                if active {
                    studio
                        .live_steps
                        .update(|steps| work::on_step_error(steps, data));
                }
            } else if active {
                studio.finish_assistant(Some(sse::error_text(data)));
            } else {
                studio.depth.finish_job(sid, "error");
            }
        }
        "done" | "end" | "aborted" => {
            let status = if kind == "aborted" { "aborted" } else { "done" };
            studio.depth.finish_job(sid, status);
            studio.depth.map_job(sid, |job| job.progress = None);
            if active {
                studio.depth.progress.set(None);
                studio.finish_assistant(None);
            }
        }
        _ => {}
    }
}

fn parse_todos(v: &Value) -> Vec<TodoItem> {
    api::rows(v, "todos")
        .into_iter()
        .map(|row| TodoItem {
            id: api::str_field(row, "id"),
            content: {
                let c = api::str_field(row, "content");
                if c.is_empty() {
                    api::str_field(row, "title")
                } else {
                    c
                }
            },
            status: {
                let s = api::str_field(row, "status");
                if s.is_empty() { "pending".into() } else { s }
            },
        })
        .filter(|t| !t.content.is_empty())
        .collect()
}

fn parse_library(v: &Value) -> LibraryChip {
    let src = v.get("suggestion").unwrap_or(v);
    LibraryChip {
        id: {
            let id = api::str_field(src, "skill_id");
            if id.is_empty() {
                api::str_field(src, "id")
            } else {
                id
            }
        },
        name: api::str_field(src, "name"),
        reason: {
            let r = api::str_field(src, "reason");
            if r.is_empty() {
                api::str_field(src, "description")
            } else {
                r
            }
        },
    }
}

fn parse_life(v: &Value) -> LifeCard {
    let steps = v
        .get("steps")
        .and_then(|s| s.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|step| {
                    if let Some(s) = step.as_str() {
                        return Some(s.to_string());
                    }
                    let title = api::str_field(step, "title");
                    if title.is_empty() { None } else { Some(title) }
                })
                .collect()
        })
        .unwrap_or_default();
    LifeCard {
        status: api::str_field(v, "status"),
        spoken: api::str_field(v, "spoken"),
        goal: api::str_field(v, "goal"),
        title: api::str_field(v, "title"),
        step: v.get("step").and_then(|n| n.as_u64()),
        total: v.get("total").and_then(|n| n.as_u64()),
        steps,
        approval: v
            .get("approval_id")
            .and_then(|s| s.as_str())
            .is_some_and(|s| !s.is_empty()),
    }
}

pub fn focus_session(studio: &Studio, id: &str) {
    studio.depth.notice.set(String::new());
    studio.depth.stall_hidden.set(false);
    if let Some(job) = studio.depth.job(id) {
        if job.status == "running" {
            studio.partial.set(job.partial);
            studio.live_breaks.set(job.breaks);
            studio.live_thinking.set(job.thinking);
            studio.live_steps.set(job.steps);
            studio.live_message_id.set(if job.message_id.is_empty() {
                None
            } else {
                Some(job.message_id)
            });
            studio.streaming.set(true);
            studio.depth.todos.set(job.todos);
            studio.depth.progress.set(job.progress);
            studio.depth.remote_busy.set(false);
            return;
        }
    }
    studio.clear_live();
    studio.streaming.set(false);
    studio.depth.todos.set(Vec::new());
    studio.depth.progress.set(None);
    reattach(studio, id);
}

fn reattach(studio: &Studio, id: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    if runtime::spawn_disabled() {
        return;
    }
    let path = format!("sessions/{id}");
    let studio = studio.clone();
    let sid = id.to_string();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            let Ok(v) = out else {
                return;
            };
            if studio.active.get().as_deref() != Some(sid.as_str()) {
                return;
            }
            let claimed = api::bool_field(&v, "claimed");
            let request = api::str_field(&v, "active_request_id");
            let local = studio.depth.job(&sid).filter(|j| j.status == "running");
            let plan = logic::reattach_plan(
                claimed,
                if request.is_empty() {
                    None
                } else {
                    Some(request.as_str())
                },
                local.is_some(),
                local.as_ref().map(|j| j.request_id.as_str()),
            );
            match plan {
                logic::ReattachPlan::Attach { request_id } => {
                    studio.streaming.set(true);
                    studio.depth.remote_busy.set(true);
                    studio.depth.map_job(&sid, |job| {
                        job.status = "running".into();
                        job.request_id = request_id.clone();
                    });
                    attach_stream(&studio, &sid, &request_id);
                }
                logic::ReattachPlan::Working => {
                    studio.streaming.set(true);
                    studio.depth.remote_busy.set(true);
                }
                logic::ReattachPlan::Idle | logic::ReattachPlan::FollowLocal => {
                    studio.depth.remote_busy.set(false);
                }
            }
        });
    });
}

fn attach_stream(studio: &Studio, sid: &str, request_id: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = format!(
        "sessions/{sid}/stream/attach?request_id={}&after=0",
        api::query(request_id)
    );
    let studio_cb = studio.clone();
    let sid_cb = sid.to_string();
    let Ok(wake) = waker() else {
        return;
    };
    runtime::drive_stream(client.http(), Request::get(&path), move |kind, data| {
        let studio = studio_cb.clone();
        let sid = sid_cb.clone();
        let _ = wake.post(move || studio.on_frame_for(&sid, &kind, &data));
    });
}

pub fn boot(studio: &Studio) {
    if studio.local_only || runtime::spawn_disabled() {
        return;
    }
    let Some(client) = studio.runtime() else {
        return;
    };
    refresh_lists(&client, studio);
    refresh_banners(studio);
    if let Some(id) = studio.active.get() {
        reattach(studio, &id);
    }
    start_poll(studio);
    start_session_events(studio);
    start_coordination(studio);
    start_tick(studio);
}

fn refresh_lists(client: &Client, studio: &Studio) {
    let studio_c = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    let wake2 = wake.clone();
    api::spawn_json(client, Request::get("commands"), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                let cmds = parse_commands(&v);
                if !cmds.is_empty() {
                    studio_c.depth.commands.set(cmds);
                }
            }
        });
    });
    let studio_a = studio.clone();
    api::spawn_json(client, Request::get("agents"), move |out| {
        let _ = wake2.post(move || {
            if let Ok(v) = out {
                let names = api::rows(&v, "agents")
                    .into_iter()
                    .filter_map(|row| {
                        if let Some(s) = row.as_str() {
                            return Some(s.to_string());
                        }
                        let name = api::str_field(row, "name");
                        if name.is_empty() { None } else { Some(name) }
                    })
                    .collect::<Vec<_>>();
                studio_a.depth.agents.set(names);
            }
        });
    });
    let studio_v = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(client, Request::get("voice/status"), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                studio_v.depth.voice.set(parse_voice(&v));
            }
        });
    });
}

fn parse_commands(v: &Value) -> Vec<logic::SlashCommand> {
    api::rows(v, "commands")
        .into_iter()
        .filter_map(|row| {
            let name = if let Some(s) = row.as_str() {
                s.to_string()
            } else {
                let n = api::str_field(row, "name");
                if n.is_empty() {
                    api::str_field(row, "command")
                } else {
                    n
                }
            };
            if name.is_empty() {
                return None;
            }
            let description = api::str_field(row, "description");
            Some(logic::SlashCommand::new(name, description))
        })
        .collect()
}

fn parse_voice(v: &Value) -> VoicePrefs {
    let settings = v.get("settings").cloned().unwrap_or(Value::Null);
    let stt = v.get("stt").cloned().unwrap_or(Value::Null);
    let tts = v.get("tts").cloned().unwrap_or(Value::Null);
    VoicePrefs {
        speak_replies: api::bool_field(&settings, "speak_replies"),
        stt: api::bool_field(&stt, "available") && api::bool_field(&settings, "stt_enabled"),
        tts: api::bool_field(&tts, "available") && api::bool_field(&settings, "tts_enabled"),
        hint: api::str_field(&stt, "hint"),
    }
}

pub fn refresh_banners(studio: &Studio) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let sid = studio.active.get().unwrap_or_default();
    let studio_a = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get("approvals"), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                studio_a.depth.approvals.set(parse_approvals(&v));
            }
        });
    });
    if sid.is_empty() {
        return;
    }
    let studio_p = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    let path = format!("plans/latest?session_id={}", api::query(&sid));
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            studio_p
                .depth
                .plan
                .set(out.ok().as_ref().and_then(parse_plan));
        });
    });
    let studio_l = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get("goals"), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                if let Some(card) = v
                    .get("life")
                    .or_else(|| v.get("task"))
                    .or_else(|| v.get("current"))
                {
                    studio_l.depth.life.set(Some(parse_life(card)));
                }
            }
        });
    });
}

fn parse_approvals(v: &Value) -> Vec<Approval> {
    api::rows(v, "approvals")
        .into_iter()
        .filter_map(|row| {
            let id = api::str_field(row, "id");
            if id.is_empty() {
                return None;
            }
            let tool = {
                let t = api::str_field(row, "tool");
                if t.is_empty() {
                    api::str_field(row, "tool_name")
                } else {
                    t
                }
            };
            let summary = {
                let s = api::str_field(row, "summary");
                if !s.is_empty() {
                    s
                } else if !api::str_field(row, "description").is_empty() {
                    api::str_field(row, "description")
                } else {
                    api::str_field(row, "command")
                }
            };
            Some(Approval {
                id,
                session_id: api::str_field(row, "session_id"),
                tool,
                summary,
                blocking: row.get("blocking").is_none() || api::bool_field(row, "blocking"),
                origin: {
                    let o = api::str_field(row, "origin");
                    if o.is_empty() {
                        api::str_field(row, "origin_channel")
                    } else {
                        o
                    }
                },
            })
        })
        .collect()
}

pub fn visible_approval(depth: &Depth, sid: &str) -> Option<Approval> {
    depth.approvals.get().into_iter().find(|a| {
        let tool = a.tool.to_ascii_lowercase();
        if tool.contains("life") && tool.contains("drive") {
            return false;
        }
        let session_ok = a.session_id.is_empty() || a.session_id == sid;
        let origin_ok = a.origin.is_empty() || a.origin == "hive" || a.origin == "studio";
        session_ok && a.blocking && origin_ok
    })
}

fn parse_plan(v: &Value) -> Option<PlanCard> {
    if v.is_null() || (v.as_object().is_some_and(|o| o.is_empty())) {
        return None;
    }
    let src = v.get("plan").unwrap_or(v);
    let status = api::str_field(src, "status");
    let goal = {
        let g = api::str_field(src, "goal");
        if g.is_empty() {
            api::str_field(src, "title")
        } else {
            g
        }
    };
    let steps: Vec<(String, String)> = src
        .get("steps")
        .and_then(|s| s.as_array())
        .map(|arr| {
            arr.iter()
                .map(|step| {
                    if let Some(s) = step.as_str() {
                        return (s.to_string(), String::new());
                    }
                    (
                        api::str_field(step, "title"),
                        api::str_field(step, "block_reason"),
                    )
                })
                .filter(|(t, _)| !t.is_empty())
                .collect()
        })
        .unwrap_or_default();
    if status.is_empty() && goal.is_empty() && steps.is_empty() {
        return None;
    }
    let done = api::u64_field(src, "done") as u32;
    let total = {
        let t = api::u64_field(src, "total") as u32;
        if t == 0 { steps.len() as u32 } else { t }
    };
    Some(PlanCard {
        status,
        goal,
        steps,
        done,
        total,
    })
}

pub fn show_plan(card: &PlanCard, mode: WorkMode) -> bool {
    let status = card.status.to_ascii_lowercase();
    if mode == WorkMode::Build && matches!(status.as_str(), "approved" | "active") {
        return false;
    }
    mode == WorkMode::Plan || matches!(status.as_str(), "draft" | "ready" | "proposed" | "")
}

fn start_poll(studio: &Studio) {
    start_poll_every(studio, Duration::from_secs(4));
}

fn start_coordination(studio: &Studio) {
    if studio
        .depth
        .coordination_flag()
        .swap(true, std::sync::atomic::Ordering::Relaxed)
    {
        return;
    }
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .coordination_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let Ok(wake) = waker() else {
        studio
            .depth
            .coordination_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let studio = studio.clone();
    let _ = std::thread::Builder::new()
        .name("remedy-coordination".into())
        .spawn(move || coordination_loop(studio, client, wake));
}

fn coordination_loop(studio: Studio, client: Client, wake: remedy_ui::reactive::Waker) {
    loop {
        if studio.local_only {
            return;
        }
        let (tx, rx) = std::sync::mpsc::channel();
        let sid = studio.active.get().unwrap_or_default();
        let path = if sid.is_empty() {
            "coordination/presence".to_string()
        } else {
            format!("coordination/presence?session_id={sid}")
        };
        let studio_cb = studio.clone();
        let wake_cb = wake.clone();
        api::spawn_json(&client, Request::get(&path), move |out| {
            let (others, error) = match out {
                Ok(v) => (logic::other_beacons(&logic::parse_presence_beacons(&v)), false),
                Err(_) => (Vec::new(), true),
            };
            let wait = logic::next_presence_poll_ms(others.len(), error);
            let _ = wake_cb.post(move || studio_cb.depth.siblings.set(others));
            let _ = tx.send(wait);
        });
        let wait = rx
            .recv_timeout(std::time::Duration::from_secs(20))
            .unwrap_or(logic::PRESENCE_POLL_ERROR_MS);
        std::thread::sleep(std::time::Duration::from_millis(wait));
    }
}

fn start_session_events(studio: &Studio) {
    if studio
        .depth
        .session_events_flag()
        .swap(true, std::sync::atomic::Ordering::Relaxed)
    {
        return;
    }
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .session_events_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let Ok(wake) = waker() else {
        studio
            .depth
            .session_events_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let studio = studio.clone();
    let _ = std::thread::Builder::new()
        .name("remedy-session-events".into())
        .spawn(move || session_events_loop(studio, client, wake));
}

fn session_events_loop(studio: Studio, client: Client, wake: remedy_ui::reactive::Waker) {
    let mut backoff = logic::SESSION_SYNC_BACKOFF_START_MS;
    loop {
        if studio.local_only {
            return;
        }
        let (tx, rx) = std::sync::mpsc::channel::<()>();
        let connected = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let flag = connected.clone();
        let wake_ev = wake.clone();
        let studio_ev = studio.clone();
        let cancel = Cancel::new();
        runtime::stream_session_events(
            &client,
            cancel,
            move |kind, data| {
                if kind == "hello" {
                    flag.store(true, std::sync::atomic::Ordering::Relaxed);
                    return;
                }
                let studio = studio_ev.clone();
                let _ = wake_ev.post(move || on_session_sync_event(&studio, &kind, &data));
            },
            move || {
                let _ = tx.send(());
            },
        );
        if rx.recv().is_err() {
            return;
        }
        if connected.load(std::sync::atomic::Ordering::Relaxed) {
            backoff = logic::SESSION_SYNC_BACKOFF_START_MS;
        } else {
            backoff = logic::next_session_sync_backoff_ms(backoff);
        }
        std::thread::sleep(Duration::from_millis(backoff));
    }
}

fn on_session_sync_event(studio: &Studio, kind: &str, data: &str) {
    if !logic::is_session_sync_event(kind) {
        return;
    }
    let sid = logic::event_session_id(data);
    let reload = logic::should_reload_open_thread(
        kind,
        &sid,
        studio.active.get().as_deref(),
        studio.streaming.get(),
    );
    let seq = pending_sync_push(if reload { Some(sid) } else { None });
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        apply_session_sync(&studio, pending_sync_take());
        return;
    };
    let _ = std::thread::Builder::new()
        .name("remedy-session-sync-wait".into())
        .spawn(move || {
            std::thread::sleep(Duration::from_millis(logic::SESSION_SYNC_DEBOUNCE_MS));
            let _ = wake.post(move || {
                if pending_sync_gen() != seq {
                    return;
                }
                let sid = pending_sync_take();
                apply_session_sync(&studio, sid);
            });
        });
}

fn apply_session_sync(studio: &Studio, reload_sid: Option<String>) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    runtime::list_sessions(&client, move |out| {
        let _ = wake.post(move || {
            let Ok(list) = out else {
                return;
            };
            let active = studio.active.get();
            studio.apply_sessions(list);
            if let Some(id) = active {
                if !studio.sessions.get().iter().any(|s| s.id == id) {
                    if let Some(next) = studio.sessions.get().first().map(|s| s.id.clone()) {
                        studio.active.set(Some(next.clone()));
                        studio.load_messages(&next);
                    } else {
                        studio.active.set(None);
                        studio.messages.set(Vec::new());
                    }
                    return;
                }
            }
            if let Some(sid) = reload_sid {
                if !studio.streaming.get() && studio.active.get().as_deref() == Some(sid.as_str()) {
                    studio.load_messages(&sid);
                }
            }
        });
    });
}

fn pending_sync_push(reload: Option<String>) -> u64 {
    if let Some(sid) = reload {
        *pending_sync_reload() = Some(sid);
    }
    pending_sync_counter().fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1
}

fn pending_sync_take() -> Option<String> {
    pending_sync_reload().take()
}

fn pending_sync_gen() -> u64 {
    pending_sync_counter().load(std::sync::atomic::Ordering::SeqCst)
}

fn pending_sync_counter() -> &'static std::sync::atomic::AtomicU64 {
    static GEN: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    &GEN
}

fn pending_sync_reload() -> std::sync::MutexGuard<'static, Option<String>> {
    static RELOAD: std::sync::OnceLock<std::sync::Mutex<Option<String>>> = std::sync::OnceLock::new();
    RELOAD
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}


/// Approvals polling. `waker()` is bound to this thread's runtime, so it
/// must be captured on the UI thread and moved into the worker. The 1.0.0
/// build called `studio.runtime()` on `remedy-approvals` itself, which
/// panics (`the signal belongs to another thread's runtime`) about four
/// seconds after launch.
fn start_poll_every(studio: &Studio, every: Duration) {
    if studio
        .depth
        .poll_flag()
        .swap(true, std::sync::atomic::Ordering::Relaxed)
    {
        return;
    }
    let Ok(wake) = waker() else {
        studio
            .depth
            .poll_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let studio = studio.clone();
    let _ = std::thread::Builder::new()
        .name("remedy-approvals".into())
        .spawn(move || {
            loop {
                std::thread::sleep(every);
                let studio = studio.clone();
                let _ = wake.post(move || poll_approvals(&studio));
            }
        });
}

fn poll_approvals(studio: &Studio) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get("approvals"), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                studio.depth.approvals.set(parse_approvals(&v));
            }
        });
    });
}

fn start_tick(studio: &Studio) {
    if studio
        .depth
        .tick_flag()
        .swap(true, std::sync::atomic::Ordering::Relaxed)
    {
        return;
    }
    let Ok(wake) = waker() else {
        studio
            .depth
            .tick_flag()
            .store(false, std::sync::atomic::Ordering::Relaxed);
        return;
    };
    let studio = studio.clone();
    let _ = std::thread::Builder::new()
        .name("remedy-tick".into())
        .spawn(move || {
            loop {
                std::thread::sleep(Duration::from_secs(5));
                let studio = studio.clone();
                let _ = wake.post(move || studio.depth.tick.update(|n| *n = n.wrapping_add(1)));
            }
        });
}

pub fn resolve_approval(studio: &Studio, id: &str, approve: bool, scope: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = format!("approvals/{id}/resolve");
    let body = json!({ "approve": approve, "scope": scope });
    let id = id.to_string();
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::post(&path).json(&body), move |_| {
        let _ = wake.post(move || {
            studio
                .depth
                .approvals
                .update(|list| list.retain(|a| a.id != id));
        });
    });
}

pub fn approve_plan(studio: &Studio) {
    studio.mode.set(WorkMode::Build);
    submit(
        studio,
        "Implement the approved plan.".into(),
        Vec::new(),
        SubmitKind::Send,
        true,
    );
}

pub fn request_plan_changes(studio: &Studio) {
    studio.mode.set(WorkMode::Plan);
    studio.depth.draft_text.set(String::new());
    studio.depth.draft_nonce.update(|n| *n += 1);
    studio
        .depth
        .notice
        .set("Plan mode stays on. Say what should change.".into());
}

pub fn send_life(studio: &Studio, word: &str) {
    submit(
        studio,
        word.to_string(),
        Vec::new(),
        SubmitKind::Send,
        false,
    );
}

pub fn install_library(studio: &Studio) {
    let Some(chip) = studio.depth.library.get() else {
        return;
    };
    let Some(client) = studio.runtime() else {
        return;
    };
    let body = json!({ "skill_id": chip.id, "force": false, "version": null });
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(
        &client,
        Request::post("skills/library/install").json(&body),
        move |out| {
            let _ = wake.post(move || match out {
                Ok(v) => {
                    let name = v
                        .get("names")
                        .and_then(|n| n.as_array())
                        .and_then(|a| a.first())
                        .and_then(|s| s.as_str())
                        .unwrap_or("")
                        .to_string();
                    if !name.is_empty() {
                        if let Some(client) = studio.runtime() {
                            let path = format!("skills/{}/status", api::query(&name));
                            api::spawn_json(
                                &client,
                                Request::post(&path).json(&json!({
                                    "status": "active",
                                    "force_promote": true,
                                    "quarantine": false,
                                })),
                                |_| {},
                            );
                            let qpath = format!("skills/{}/quarantine", api::query(&name));
                            api::spawn_json(
                                &client,
                                Request::post(&qpath).json(&json!({ "quarantine": false })),
                                |_| {},
                            );
                        }
                    }
                    dismiss_library(&studio);
                    studio.depth.notice.set("Installed. It is active.".into());
                }
                Err(e) => studio.depth.notice.set(e),
            });
        },
    );
}

pub fn dismiss_library(studio: &Studio) {
    let chip = studio.depth.library.get();
    studio.depth.library.set(None);
    let Some(chip) = chip else {
        return;
    };
    let Some(client) = studio.runtime() else {
        return;
    };
    let body = json!({
        "skill_id": chip.id,
        "session_id": studio.active.get().unwrap_or_default(),
    });
    api::spawn_json(
        &client,
        Request::post("skills/library/suggest/dismiss").json(&body),
        |_| {},
    );
}

pub fn react(studio: &Studio, message_id: &str, emoji: &str) {
    if message_id.is_empty() {
        return;
    }
    let current = studio
        .messages
        .get()
        .iter()
        .find(|m| m.id == message_id)
        .map(|m| m.user_reaction.clone())
        .unwrap_or_else(|| {
            if studio.live_message_id.get().as_deref() == Some(message_id) {
                studio.live_user_reaction.get()
            } else {
                String::new()
            }
        });
    let next = logic::next_reaction(&current, emoji);
    let message_id = message_id.to_string();
    if studio.live_message_id.get().as_deref() == Some(message_id.as_str()) {
        studio.live_user_reaction.set(next.clone());
    }
    studio.messages.update(|list| {
        if let Some(m) = list.iter_mut().find(|m| m.id == message_id) {
            m.user_reaction = next.clone();
        }
    });
    let Some(client) = studio.runtime() else {
        return;
    };
    let Some(sid) = studio.active.get() else {
        return;
    };
    let path = format!("sessions/{sid}/messages/{message_id}/reaction");
    api::spawn_json(
        &client,
        Request::put(&path).json(&json!({ "emoji": next })),
        |_| {},
    );
}

fn set_composer_draft(studio: &Studio, text: &str) {
    studio.depth.draft_text.set(text.to_string());
    studio.depth.composer_value.set(text.to_string());
    studio.depth.draft_nonce.update(|n| *n += 1);
}

/// Soft-delete this user message and later ones, and put the text in the composer.
pub fn edit_user_message(studio: &Studio, msg_id: &str, content: &str) {
    if msg_id.is_empty() || studio.streaming.get() {
        return;
    }
    let Some(sid) = studio.active.get() else {
        return;
    };
    set_composer_draft(studio, content);
    let Some(client) = studio.runtime() else {
        return;
    };
    let Ok(wake) = waker() else {
        return;
    };
    let studio = studio.clone();
    let path = logic::edit_message_path(&sid, msg_id);
    api::spawn_json(&client, Request::post(&path), move |out| {
        let _ = wake.post(move || {
            if studio.active.get().as_deref() != Some(sid.as_str()) {
                return;
            }
            match out {
                Ok(v) => {
                    let server = api::str_field(&v, "content");
                    if !server.is_empty() && server != studio.depth.draft_text.get() {
                        set_composer_draft(&studio, &server);
                    }
                    studio.load_messages(&sid);
                }
                Err(e) => studio
                    .depth
                    .notice
                    .set(format!("Could not edit message. {e}")),
            }
        });
    });
}

/// Roll the thread back to the user turn that produced this reply, then resend it.
pub fn regenerate(studio: &Studio, assistant_msg_id: &str) {
    if assistant_msg_id.is_empty() || studio.streaming.get() {
        return;
    }
    let Some(sid) = studio.active.get() else {
        return;
    };
    let Some((user_id, prompt)) =
        logic::preceding_user_for_regen(&studio.messages.get(), assistant_msg_id)
    else {
        return;
    };
    let clean = logic::clean_resend_text(&prompt);
    let to_send = if !clean.is_empty() {
        clean
    } else if !prompt.trim().is_empty() {
        prompt.trim().to_string()
    } else {
        "(see attached files)".into()
    };
    let Some(client) = studio.runtime() else {
        return;
    };
    let Ok(wake) = waker() else {
        return;
    };
    let studio = studio.clone();
    let path = logic::edit_message_path(&sid, &user_id);
    api::spawn_json(&client, Request::post(&path), move |out| {
        let _ = wake.post(move || {
            if studio.active.get().as_deref() != Some(sid.as_str()) {
                return;
            }
            match out {
                Ok(_) => reload_then_send(&studio, &sid, to_send),
                Err(e) => studio
                    .depth
                    .notice
                    .set(format!("Could not regenerate reply. {e}")),
            }
        });
    });
}

fn reload_then_send(studio: &Studio, sid: &str, text: String) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let Ok(wake) = waker() else {
        submit(studio, text, Vec::new(), SubmitKind::Send, false);
        return;
    };
    let studio = studio.clone();
    let sid = sid.to_string();
    let sid_ref = sid.clone();
    runtime::list_messages(&client, &sid_ref, move |out| {
        let _ = wake.post(move || {
            if studio.active.get().as_deref() == Some(sid.as_str()) {
                if let Ok(msgs) = out {
                    studio.messages.set(msgs);
                }
            }
            submit(&studio, text, Vec::new(), SubmitKind::Send, false);
        });
    });
}

pub fn rename_session(studio: &Studio, id: &str, title: &str) {
    let title = title.trim().to_string();
    if title.is_empty() {
        return;
    }
    let id = id.to_string();
    studio.sessions.update(|list| {
        if let Some(s) = list.iter_mut().find(|s| s.id == id) {
            s.title = title.clone();
        }
    });
    studio.depth.rename_id.set(None);
    if let Some(client) = studio.runtime() {
        let path = format!("sessions/{id}");
        api::spawn_json(
            &client,
            Request::patch(&path).json(&json!({ "title": title })),
            |_| {},
        );
    }
}

pub fn assign_project(studio: &Studio, id: &str, project: Option<String>) {
    let old = studio
        .sessions
        .get()
        .iter()
        .find(|s| s.id == id)
        .and_then(|s| s.project_path.clone());
    studio.set_project(id, project.clone());
    let mut map = studio.depth.session_order.get();
    rehome_order(&mut map, id, old.as_deref(), project.as_deref());
    studio.depth.session_order.set(map);
    studio.depth.save_order();
    if let Some(client) = studio.runtime() {
        let path = format!("sessions/{id}");
        let body = json!({ "project_path": project });
        api::spawn_json(&client, Request::patch(&path).json(&body), |_| {});
    }
}

/// Move many sessions to one project (or no project) with a single request.
pub fn bulk_assign_project(studio: &Studio, ids: &[String], project: Option<String>) {
    let ids: Vec<String> = ids.iter().take(200).cloned().collect();
    if ids.is_empty() {
        return;
    }
    let mut map = studio.depth.session_order.get();
    for id in &ids {
        let old = studio
            .sessions
            .get()
            .iter()
            .find(|s| s.id == *id)
            .and_then(|s| s.project_path.clone());
        studio.set_project(id, project.clone());
        rehome_order(&mut map, id, old.as_deref(), project.as_deref());
    }
    studio.depth.session_order.set(map);
    studio.depth.save_order();
    if let Some(client) = studio.runtime() {
        let body = logic::bulk_project_body(&ids, project.as_deref());
        api::spawn_json(
            &client,
            Request::post("sessions/bulk-project").json(&body),
            |_| {},
        );
    }
    studio.selected_ids.set(Vec::new());
    studio.select_anchor.set(None);
}

pub fn move_project_order(studio: &Studio, path: &str, dir: i32) {
    let keys = project_keys(studio);
    if let Some(next) = logic::move_in_list(&keys, path, dir) {
        studio.depth.project_order.set(next);
        studio.depth.save_order();
    }
}

pub fn move_session_order(studio: &Studio, id: &str, dir: i32) {
    let key = session_project_key(studio, id);
    let mut map = studio.depth.session_order.get();
    let current = map
        .iter()
        .find(|(k, _)| k == &key)
        .map(|(_, ids)| ids.clone())
        .unwrap_or_else(|| member_ids(studio, &key));
    let Some(next) = logic::move_in_list(&current, id, dir) else {
        return;
    };
    if let Some((_, ids)) = map.iter_mut().find(|(k, _)| k == &key) {
        *ids = next;
    } else {
        map.push((key, next));
    }
    studio.depth.session_order.set(map);
    studio.depth.save_order();
}

/// Drops `id` just before `before` in that session's project. A different project
/// moves the chat there first.
pub fn place_session(studio: &Studio, id: &str, before: Option<&str>) {
    if id.is_empty() {
        return;
    }
    if let Some(before_id) = before.filter(|b| *b != id) {
        let dest = studio
            .sessions
            .get()
            .iter()
            .find(|s| s.id == before_id)
            .and_then(|s| s.project_path.clone());
        let cur = studio
            .sessions
            .get()
            .iter()
            .find(|s| s.id == id)
            .and_then(|s| s.project_path.clone());
        if dest != cur {
            assign_project(studio, id, dest);
        }
    }
    let key = session_project_key(studio, id);
    let mut map = studio.depth.session_order.get();
    let mut ids = map
        .iter()
        .find(|(k, _)| k == &key)
        .map(|(_, ids)| ids.clone())
        .unwrap_or_else(|| member_ids(studio, &key));
    ids.retain(|s| s != id);
    match before {
        Some(b) => {
            if let Some(i) = ids.iter().position(|s| s == b) {
                ids.insert(i, id.to_string());
            } else {
                ids.push(id.to_string());
            }
        }
        None => ids.push(id.to_string()),
    }
    if let Some((_, slot)) = map.iter_mut().find(|(k, _)| k == &key) {
        *slot = ids;
    } else {
        map.push((key, ids));
    }
    studio.depth.session_order.set(map);
    studio.depth.save_order();
}

fn project_keys(studio: &Studio) -> Vec<String> {
    let mut keys = Vec::new();
    for (project, _) in logic::ordered_groups(
        &studio.sessions.get(),
        &studio.projects.get(),
        studio.filter.get(),
        &studio.query.get(),
        &studio.depth.project_order.get(),
        &studio.depth.session_order.get(),
    ) {
        if let Some(path) = project.path.as_deref() {
            if path != logic::PINNED_GROUP_KEY {
                keys.push(project_key(Some(path)));
            }
        }
    }
    keys
}

fn session_project_key(studio: &Studio, id: &str) -> String {
    let path = studio
        .sessions
        .get()
        .iter()
        .find(|s| s.id == id)
        .and_then(|s| s.project_path.clone());
    project_key(path.as_deref())
}

fn member_ids(studio: &Studio, key: &str) -> Vec<String> {
    studio
        .sessions
        .get()
        .into_iter()
        .filter(|s| project_key(s.project_path.as_deref()) == key)
        .map(|s| s.id)
        .collect()
}

pub fn remove_project(studio: &Studio, path: &str) {
    let path = path.to_string();
    let ids: Vec<String> = studio
        .sessions
        .get()
        .iter()
        .filter(|s| s.project_path.as_deref() == Some(path.as_str()))
        .map(|s| s.id.clone())
        .collect();
    for id in &ids {
        assign_project(studio, id, None);
    }
    studio
        .projects
        .update(|list| list.retain(|p| p.path.as_deref() != Some(path.as_str())));
    studio.depth.project_order.update(|keys| {
        keys.retain(|k| k != &project_key(Some(&path)));
    });
    studio.depth.save_order();
    studio.depth.confirm.set(None);
}

pub fn trust_skill(studio: &Studio, name: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let name = name.to_string();
    let path = format!("skills/{}/status", api::query(&name));
    api::spawn_json(
        &client,
        Request::post(&path).json(&json!({
            "status": "active",
            "force_promote": true,
            "quarantine": false,
        })),
        |_| {},
    );
    let qpath = format!("skills/{}/quarantine", api::query(&name));
    api::spawn_json(
        &client,
        Request::post(&qpath).json(&json!({ "quarantine": false })),
        |_| {},
    );
    studio.depth.notice.set("Trusted. It can run.".into());
    load_skills(studio);
}

pub fn delete_skill(studio: &Studio, name: &str) {
    let Some(client) = studio.runtime() else {
        studio.depth.confirm.set(None);
        return;
    };
    let path = format!("skills/{}", api::query(name));
    let name = name.to_string();
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::delete(&path), move |out| {
        let _ = wake.post(move || {
            studio.depth.confirm.set(None);
            match out {
                Ok(_) => {
                    studio
                        .depth
                        .skills
                        .update(|list| list.retain(|s| s.name != name));
                    if studio.depth.skill_name.get() == name {
                        studio.depth.skill_name.set(String::new());
                        studio.depth.skill_body.set(String::new());
                    }
                }
                Err(e) => studio.depth.notice.set(e),
            }
        });
    });
}

pub fn export_active(studio: &Studio) {
    let sid = studio.active.get();
    let local = transcript(studio);
    let Some(sid) = sid else {
        save_text(studio, "session.txt", local);
        return;
    };
    let Some(client) = studio.runtime() else {
        save_text(studio, "session.txt", local);
        return;
    };
    let path = format!("sessions/{sid}/export?format=txt");
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            let text = match out {
                Ok(v) => {
                    let t = api::str_field(&v, "text");
                    if t.is_empty() {
                        api::str_field(&v, "markdown")
                    } else {
                        t
                    }
                }
                Err(_) => transcript(&studio),
            };
            let name = "session.txt".to_string();
            save_text(&studio, &name, text);
        });
    });
}

fn transcript(studio: &Studio) -> String {
    let mut out = String::new();
    for msg in studio.messages.get() {
        out.push_str(&msg.role);
        out.push_str(":\n");
        out.push_str(&msg.content);
        out.push_str("\n\n");
    }
    out
}

fn save_text(studio: &Studio, name: &str, text: String) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let name = name.to_string();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = "Export session".into();
        dialog.save = true;
        dialog.file_name = Some(name);
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            if let Some(path) = paths.into_iter().next() {
                match std::fs::write(&path, &text) {
                    Ok(()) => studio
                        .depth
                        .notice
                        .set(format!("Exported {}", path.display())),
                    Err(e) => studio.depth.notice.set(e.to_string()),
                }
            }
        });
    });
    if !ok {
        studio_notice_window(&studio);
    }
}

pub fn import_session(studio: &Studio) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = "Import session".into();
        dialog.filters = vec![("Text".into(), vec!["txt".into(), "md".into()])];
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            let Some(path) = paths.into_iter().next() else {
                return;
            };
            let Ok(text) = std::fs::read_to_string(&path) else {
                studio.depth.notice.set("Could not read that file.".into());
                return;
            };
            let Some(client) = studio.runtime() else {
                studio.add();
                push_system(&studio, &text);
                return;
            };
            let body = json!({ "text": text, "title": path.file_stem().and_then(|s| s.to_str()).unwrap_or("Imported") });
            let Ok(wake) = waker() else {
                return;
            };
            api::spawn_json(&client, Request::post("sessions/import").json(&body), move |out| {
                let _ = wake.post(move || match out {
                    Ok(v) => {
                        let id = api::str_field(&v, "id");
                        studio.depth.notice.set("Imported.".into());
                        if !id.is_empty() {
                            if let Some(client) = studio.runtime() {
                                let studio = studio.clone();
                                let Ok(wake) = waker() else {
                                    return;
                                };
                                runtime::list_sessions(&client, move |res| {
                                    let _ = wake.post(move || {
                                        if let Ok(list) = res {
                                            studio.apply_sessions(list);
                                            studio.select(&id);
                                        }
                                    });
                                });
                            }
                        }
                    }
                    Err(e) => studio.depth.notice.set(e),
                });
            });
        });
    });
    if !ok {
        studio_notice_window(&studio);
    }
}

pub fn pick_attachments(studio: &Studio) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = "Attach files".into();
        dialog.multiple = true;
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            add_paths(&studio, &paths)
        });
    });
    if !ok {
        studio_notice_window(&studio);
    }
}

pub fn pick_folder(studio: &Studio) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = "Project folder".into();
        dialog.folders = true;
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            if let Some(path) = paths.into_iter().next() {
                studio.add_project(path.display().to_string(), false);
            }
        });
    });
    if !ok {
        studio.adding_project.set(true);
        studio
            .depth
            .notice
            .set("File picking needs a window. Type the folder path.".into());
    }
}

fn studio_notice_window(studio: &Studio) {
    studio
        .depth
        .notice
        .set("File picking needs a window.".into());
}

pub fn add_paths(studio: &Studio, paths: &[PathBuf]) {
    let mut list = studio.depth.attachments.get();
    for path in paths {
        if list.len() >= MAX_ATTACHMENTS {
            studio
                .depth
                .notice
                .set("Twelve attachments is the limit.".into());
            break;
        }
        let name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("file")
            .to_string();
        let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
        if size > api::ATTACH_CAP {
            studio.depth.notice.set(format!("{name} is over 20 MB."));
            continue;
        }
        let key = name_size_key(&name, size);
        if list.iter().any(|a| name_size_key(&a.name, a.size) == key) {
            studio
                .depth
                .notice
                .set(format!("{name} is already attached."));
            continue;
        }
        let mime = mime_for(&name);
        list.push(Attachment {
            is_image: mime.starts_with("image/"),
            is_text: logic::looks_like_text_name(&name, &mime),
            name,
            path: path.display().to_string(),
            mime,
            size,
            pending: false,
        });
    }
    studio.depth.attachments.set(list);
}

fn mime_for(name: &str) -> String {
    let ext = Path::new(name)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "txt" | "md" | "rs" | "json" | "toml" => "text/plain",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
    .to_string()
}

fn read_capped(path: &str) -> Result<Vec<u8>, String> {
    let meta = std::fs::metadata(path).map_err(|e| e.to_string())?;
    if meta.len() > api::ATTACH_CAP {
        return Err("That file is over 20 MB.".into());
    }
    std::fs::read(path).map_err(|e| e.to_string())
}

pub fn toggle_mic(studio: &Studio) {
    if studio.depth.mic_on.get() {
        studio.depth.mic_on.set(false);
        if let Some(mic) = take_mic() {
            mic.stop();
        }
        let samples = studio
            .depth
            .samples
            .lock()
            .map(|mut g| std::mem::take(&mut *g))
            .unwrap_or_default();
        transcribe(studio, samples);
        return;
    }
    let buf = studio.depth.samples.clone();
    if let Ok(mut g) = buf.lock() {
        g.clear();
    }
    let mic = Microphone::open_platform(MicrophoneOptions::default(), move |frames| {
        if let Ok(mut g) = buf.lock() {
            if g.len() < 48_000 * 60 {
                g.extend_from_slice(frames);
            }
        }
    });
    if mic.state() == MicrophoneState::Failed {
        let msg = mic
            .error()
            .map(|e| e.to_string())
            .unwrap_or_else(|| "The microphone did not open.".into());
        studio.depth.notice.set(msg);
        return;
    }
    studio.depth.mic_on.set(true);
    put_mic(mic);
}

fn transcribe(studio: &Studio, samples: Vec<f32>) {
    if samples.is_empty() {
        studio.depth.notice.set("No speech captured.".into());
        return;
    }
    studio.depth.notice.set("Hearing…".into());
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .notice
            .set("Voice needs the local mind.".into());
        return;
    };
    let wav = api::wav_bytes(&samples, 48_000);
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(
        &client,
        Request::post("voice/transcribe").body(wav, "audio/wav"),
        move |out| {
            let _ = wake.post(move || match out {
                Ok(v) => {
                    let heard = api::str_field(&v, "text");
                    if heard.trim().is_empty() {
                        studio.depth.notice.set("Nothing to transcribe.".into());
                        return;
                    }
                    let cur = studio.depth.composer_value.get();
                    let next = if cur.trim().is_empty() {
                        heard
                    } else {
                        format!("{cur} {heard}")
                    };
                    studio.depth.draft_text.set(next.clone());
                    studio.depth.composer_value.set(next);
                    studio.depth.draft_nonce.update(|n| *n += 1);
                }
                Err(e) => studio.depth.notice.set(e),
            });
        },
    );
}

pub fn speak_reply(studio: &Studio, text: &str) {
    let prefs = studio.depth.voice.get();
    let speak = super::speak_override().unwrap_or(prefs.speak_replies);
    if !speak {
        return;
    }
    let spoken = strip_markdown_for_speech(text);
    if spoken.trim().is_empty() {
        return;
    }
    let chunk = logic::split_for_speech(&spoken)
        .into_iter()
        .next()
        .unwrap_or(spoken);
    let Some(client) = studio.runtime() else {
        return;
    };
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_bytes(
        &client,
        Request::post("voice/speak").json(&json!({ "text": chunk })),
        move |out| {
            let _ = wake.post(move || {
                let Ok(bytes) = out else {
                    return;
                };
                if bytes.len() < 16 || &bytes[..4] != b"RIFF" {
                    return;
                }
                let path = std::env::temp_dir().join(format!("remedy-speak-{}.wav", now_ms()));
                if std::fs::write(&path, &bytes).is_err() {
                    return;
                }
                with_ui(|ui| {
                    let player = Player::open_platform(
                        ui,
                        MediaSource::file(&path),
                        PlayerOptions::default().autoplay(true),
                    );
                    player.play();
                    super::put_player(player);
                });
            });
        },
    );
}

pub fn load_memory(studio: &Studio) {
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .memory_body
            .set("The mind is not connected.".into());
        return;
    };
    let sid = studio.active.get().unwrap_or_default();
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    let facts = format!("memory/facts");
    api::spawn_json(&client, Request::get(&facts), move |facts_out| {
        let client = studio.runtime();
        let _ = wake.post(move || {
            let mut body = String::new();
            if let Ok(v) = facts_out {
                body.push_str(&brief_json("Facts", &v));
            }
            studio.depth.memory_body.set(body);
            let Some(client) = client else {
                return;
            };
            let studio = studio.clone();
            let Ok(wake) = waker() else {
                return;
            };
            let path = format!("checkpoints/latest?session_id={}", api::query(&sid));
            api::spawn_json(&client, Request::get(&path), move |out| {
                let _ = wake.post(move || {
                    if let Ok(v) = out {
                        let mut body = studio.depth.memory_body.get();
                        body.push_str(&brief_json("Checkpoint", &v));
                        studio.depth.memory_body.set(body);
                    }
                });
            });
        });
    });
}

fn brief_json(title: &str, v: &Value) -> String {
    let text = serde_json::to_string_pretty(v).unwrap_or_default();
    if text.trim().is_empty() || text == "null" {
        return String::new();
    }
    format!("{title}\n{text}\n\n")
}

pub fn search_memory(studio: &Studio, q: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = format!("memory/search?q={}", api::query(q));
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                studio.depth.memory_body.set(brief_json("Memory", &v));
            }
        });
    });
}

pub fn load_skills(studio: &Studio) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    let st_list = studio.clone();
    let wake_list = wake.clone();
    api::spawn_json(&client, Request::get("skills"), move |out| {
        let _ = wake_list.post(move || {
            if let Ok(v) = out {
                st_list.depth.skills.set(parse_skills(&v));
            }
        });
    });
    load_skill_insights(&client, &studio, &wake);
}

fn load_skill_insights(client: &Client, studio: &Studio, wake: &remedy_ui::reactive::Waker) {
    let st = studio.clone();
    let wake_b = wake.clone();
    api::spawn_json(client, Request::get("skills/packs"), move |out| {
        let _ = wake_b.post(move || {
            if let Ok(v) = out {
                st.depth.skill_budget.set(logic::parse_skill_budget(&v));
            }
        });
    });
    let st = studio.clone();
    let wake_l = wake.clone();
    api::spawn_json(client, Request::get("skills/learning/summary"), move |out| {
        let _ = wake_l.post(move || {
            if let Ok(v) = out {
                let parsed = logic::parse_skill_learning(&v);
                st.depth.skill_learning.update(|learn| {
                    learn.learned_count = parsed.learned_count;
                    learn.probation_count = parsed.probation_count;
                });
            }
        });
    });
    let st = studio.clone();
    let wake_r = wake.clone();
    api::spawn_json(client, Request::get("skills/metrics/reuse"), move |out| {
        let _ = wake_r.post(move || {
            if let Ok(v) = out {
                let (uses, multi) = logic::parse_skill_reuse(&v);
                st.depth.skill_learning.update(|learn| {
                    learn.total_activations = uses;
                    learn.multi_session = multi;
                });
            }
        });
    });
}

fn parse_skills(v: &Value) -> Vec<SkillRow> {
    api::rows(v, "skills")
        .into_iter()
        .filter_map(|row| {
            let name = api::str_field(row, "name");
            if name.is_empty() {
                return None;
            }
            Some(SkillRow {
                name,
                description: api::str_field(row, "description"),
                status: api::str_field(row, "status"),
                quarantine: api::bool_field(row, "quarantine"),
                auto_generated: api::bool_field(row, "auto_generated"),
            })
        })
        .collect()
}

pub fn open_skill(studio: &Studio, name: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    studio.depth.skill_name.set(name.to_string());
    let path = format!("skills/{}", api::query(name));
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                let body = {
                    let b = api::str_field(&v, "body");
                    if b.is_empty() {
                        api::str_field(&v, "instructions")
                    } else {
                        b
                    }
                };
                studio.depth.skill_body.set(body);
            }
        });
    });
}

pub fn save_skill(studio: &Studio, body: &str) {
    let name = studio.depth.skill_name.get();
    if name.is_empty() {
        return;
    }
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = format!("skills/{}/body", api::query(&name));
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(
        &client,
        Request::put(&path).json(&json!({ "body": body })),
        move |out| {
            let _ = wake.post(move || match out {
                Ok(_) => studio.depth.notice.set("Skill saved.".into()),
                Err(e) => studio.depth.notice.set(e),
            });
        },
    );
}

pub fn search_library(studio: &Studio, q: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let path = if q.trim().is_empty() {
        "skills/library/catalog".to_string()
    } else {
        format!("skills/library/search?q={}", api::query(q))
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(&client, Request::get(&path), move |out| {
        let _ = wake.post(move || {
            if let Ok(v) = out {
                let rows = parse_skills(&v);
                let rows = if rows.is_empty() {
                    api::rows(&v, "results")
                        .into_iter()
                        .filter_map(|row| {
                            let name = api::str_field(row, "name");
                            if name.is_empty() {
                                return None;
                            }
                            Some(SkillRow {
                                name,
                                description: api::str_field(row, "description"),
                                status: api::str_field(row, "id"),
                                quarantine: false,
                                auto_generated: false,
                            })
                        })
                        .collect()
                } else {
                    rows
                };
                studio.depth.library_rows.set(rows);
            }
        });
    });
}

pub fn install_named(studio: &Studio, skill_id: &str) {
    let Some(client) = studio.runtime() else {
        return;
    };
    let body = json!({ "skill_id": skill_id, "force": false, "version": null });
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(
        &client,
        Request::post("skills/library/install").json(&body),
        move |out| {
            let _ = wake.post(move || match out {
                Ok(_) => {
                    studio
                        .depth
                        .notice
                        .set("Installed. Trust it before it runs.".into());
                    load_skills(&studio);
                }
                Err(e) => studio.depth.notice.set(e),
            });
        },
    );
}

/// The confirm copy for a project, including how many chats move.
pub fn project_confirm_body(studio: &Studio, path: &str) -> String {
    let n = studio
        .sessions
        .get()
        .iter()
        .filter(|s| s.project_path.as_deref() == Some(path))
        .count();
    logic::project_remove_body(path, n)
}

pub fn ask_archive_unused(studio: &Studio) {
    studio.depth.ask_confirm(Confirm::ArchiveUnusedSkills);
}

pub fn export_skills_pack(studio: &Studio) {
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .notice
            .set("Connect Remedy to export skills.".into());
        return;
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    std::thread::spawn(move || {
        let cancel = Cancel::new();
        let req = Request::post("skills/export")
            .json(&json!({ "names": [] }))
            .max_body(None);
        let out = client.http().send_blocking(req, &cancel);
        let _ = wake.post(move || match out {
            Ok(res) => {
                let name = logic::attachment_filename(
                    res.header("content-disposition"),
                    "remedy-skills-pack.zip",
                );
                save_bytes(&studio, &name, res.body);
            }
            Err(e) => studio.depth.notice.set(e.to_string()),
        });
    });
}

pub fn import_skills_pack(studio: &Studio) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = "Import skills pack".into();
        dialog.filters = vec![("ZIP".into(), vec!["zip".into()])];
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            let Some(path) = paths.into_iter().next() else {
                return;
            };
            let Ok(bytes) = std::fs::read(&path) else {
                studio
                    .depth
                    .notice
                    .set("Could not read that file.".into());
                return;
            };
            let Some(client) = studio.runtime() else {
                studio
                    .depth
                    .notice
                    .set("Connect Remedy to import skills.".into());
                return;
            };
            let Ok(wake) = waker() else {
                return;
            };
            let file_name = path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("skills.zip")
                .to_string();
            std::thread::spawn(move || {
                let cancel = Cancel::new();
                let form = Multipart::new().file(
                    "file",
                    file_name,
                    "application/zip",
                    bytes,
                );
                let req = Request::post("skills/import").multipart(&form);
                let out = match client.http().send_blocking(req, &cancel) {
                    Ok(res) => res.json::<Value>().map_err(|e| e.to_string()),
                    Err(e) => Err(e.to_string()),
                };
                let _ = wake.post(move || match out {
                    Ok(v) => {
                        let (n, names) = logic::parse_import_pack(&v);
                        studio
                            .depth
                            .notice
                            .set(logic::import_pack_notice(n, &names));
                        load_skills(&studio);
                    }
                    Err(e) => studio.depth.notice.set(e),
                });
            });
        });
    });
    if !ok {
        studio_notice_window(&studio);
    }
}

fn save_bytes(studio: &Studio, name: &str, bytes: Vec<u8>) {
    save_bytes_as(studio, "Export skills pack", name, bytes);
}

pub(crate) fn save_bytes_as(studio: &Studio, title: &str, name: &str, bytes: Vec<u8>) {
    let studio = studio.clone();
    let dialog_studio = studio.clone();
    let name = name.to_string();
    let title = title.to_string();
    let ok = with_host(move |app, window| {
        let mut dialog = FileDialog::default();
        dialog.title = title;
        dialog.save = true;
        dialog.file_name = Some(name);
        let studio = dialog_studio.clone();
        app.file_dialog(Some(window), dialog, move |paths| {
            if let Some(path) = paths.into_iter().next() {
                match std::fs::write(&path, &bytes) {
                    Ok(()) => studio
                        .depth
                        .notice
                        .set(format!("Exported {}", path.display())),
                    Err(e) => studio.depth.notice.set(e.to_string()),
                }
            }
        });
    });
    if !ok {
        studio
            .depth
            .notice
            .set("File picking needs a window.".into());
    }
}

pub fn archive_unused_skills(studio: &Studio) {
    studio.depth.confirm.set(None);
    let Some(client) = studio.runtime() else {
        studio
            .depth
            .notice
            .set("Connect Remedy to archive unused skills.".into());
        return;
    };
    let studio = studio.clone();
    let Ok(wake) = waker() else {
        return;
    };
    api::spawn_json(
        &client,
        Request::post("skills/archive-unused").json(&json!({
            "days": 90,
            "dry_run": false,
            "include_quarantine": false,
        })),
        move |out| {
            let _ = wake.post(move || match out {
                Ok(v) => {
                    let count = logic::parse_archive_count(&v);
                    studio
                        .depth
                        .notice
                        .set(logic::archive_unused_notice(count, None));
                    load_skills(&studio);
                }
                Err(e) => studio
                    .depth
                    .notice
                    .set(logic::archive_unused_notice(0, Some(&e))),
            });
        },
    );
}

pub fn ask_remove_project(studio: &Studio, path: &str) {
    studio
        .depth
        .ask_confirm(Confirm::RemoveProject(path.to_string()));
}

pub fn ask_delete_skill(studio: &Studio, name: &str) {
    studio
        .depth
        .ask_confirm(Confirm::DeleteSkill(name.to_string()));
}

/// Quiet time for the stall banner, in whole seconds. None when it should stay hidden.
pub fn stall_seconds(studio: &Studio) -> Option<u64> {
    let _ = studio.depth.tick.get();
    if studio.depth.stall_hidden.get() || !studio.streaming.get() {
        return None;
    }
    let sid = studio.active.get()?;
    let job = studio.depth.job(&sid)?;
    if job.status != "running" {
        return None;
    }
    let quiet = now_ms().saturating_sub(job.last_event_ms).max(0) as u64;
    if quiet < logic::STALL_WARN_MS {
        return None;
    }
    Some(quiet / 1000)
}

pub fn background_title(studio: &Studio) -> Option<String> {
    let active = studio.active.get();
    let jobs = studio.depth.jobs.get();
    let other = jobs
        .iter()
        .find(|j| j.status == "running" && active.as_deref() != Some(j.session_id.as_str()))?;
    let title = studio
        .sessions
        .get()
        .iter()
        .find(|s| s.id == other.session_id)
        .map(|s| s.title.clone())
        .unwrap_or_else(|| other.session_id.clone());
    Some(title)
}

/// Steps copied out so the trace can render without holding the job.
pub fn focused_steps(studio: &Studio) -> Vec<WorkStep> {
    if studio.streaming.get() {
        return studio.live_steps.get();
    }
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::studio::{ChatMessage, Studio};
    use crate::work::{StepStatus, WorkStep};
    use crate::shell;
    use remedy_ui::testing::TestApp;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;
    use std::time::Instant;

    #[test]
    fn draft_event_stores_the_real_message_id() {
        let studio = Studio::new();
        studio.active.set(Some("s1".into()));
        apply_frame(&studio, "s1", "draft", r#"{"message_id":"msg-42"}"#);
        assert_eq!(studio.live_message_id.get().as_deref(), Some("msg-42"));
        let job = studio.depth.job("s1").expect("job recorded");
        assert_eq!(job.message_id, "msg-42");
    }

    #[test]
    fn mid_turn_react_uses_the_draft_id() {
        let studio = Studio::new();
        studio.active.set(Some("s1".into()));
        studio.live_message_id.set(Some("msg-42".into()));
        react(&studio, "msg-42", "👍");
        assert_eq!(studio.live_user_reaction.get(), "👍");
        react(&studio, "msg-42", "👍");
        assert_eq!(studio.live_user_reaction.get(), "", "toggle off");
        react(&studio, "msg-42", "❤️");
        assert_eq!(studio.live_user_reaction.get(), "❤️");
    }

    #[test]
    fn finish_assistant_keeps_draft_id_and_mid_turn_reaction() {
        let studio = Studio::new();
        studio.active.set(Some("1".into()));
        let first = "I'll look.\n\n";
        studio.partial.set(format!("{first}The portal loads."));
        studio.live_breaks.set(vec![first.len()]);
        let mut step = WorkStep::done("c1", "bash");
        step.status = StepStatus::Done;
        step.narration = Some("I'll look.".into());
        studio.live_steps.set(vec![step]);
        studio.live_message_id.set(Some("draft-9".into()));
        studio.live_user_reaction.set("👍".into());
        studio.finish_assistant(None);
        let msgs = studio.messages.get();
        let a = msgs
            .iter()
            .find(|m| m.role == "assistant")
            .expect("assistant row");
        assert_eq!(a.id, "draft-9");
        assert_eq!(a.user_reaction, "👍");
        assert_eq!(a.content, "The portal loads.");
        assert_eq!(a.steps[0].narration.as_deref(), Some("I'll look."));
        assert!(studio.live_message_id.get().is_none());
        assert!(studio.live_user_reaction.get().is_empty());
    }

    #[test]
    fn approvals_poll_reads_the_client_on_the_ui_thread() {
        // The live app starts this poller after the sidecar is up. Reading
        // `studio.runtime()` on the worker used to abort the process.
        unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") };
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind mock");
        let addr = listener.local_addr().expect("addr");
        thread::Builder::new()
            .name("mock-approvals".into())
            .spawn(move || {
                for stream in listener.incoming() {
                    let Ok(mut s) = stream else { continue };
                    let mut buf = vec![0u8; 4096];
                    let n = match s.read(&mut buf) {
                        Ok(n) => n,
                        Err(_) => continue,
                    };
                    let req = String::from_utf8_lossy(&buf[..n]);
                    let path = req
                        .lines()
                        .next()
                        .unwrap_or("")
                        .split_whitespace()
                        .nth(1)
                        .unwrap_or("/");
                    let body = if path.contains("approvals") {
                        r#"{"approvals":[{"id":"ap-1","tool":"bash","summary":"list files"}]}"#
                    } else {
                        r#"{"ok":true}"#
                    };
                    let _ = write!(
                        s,
                        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        body.len(),
                        body
                    );
                }
            })
            .expect("spawn mock");

        let studio = Studio::new();
        studio.attach_client(Client::new(&format!("http://{addr}"), Some("test-token-1")));
        let mut app = TestApp::window({
            let studio = studio.clone();
            move |ui, _| {
                ui.root().child(shell(ui, studio.clone()));
            }
        });
        // The poller thread can only obtain a Waker after the window is up.
        app.frames(2);
        start_poll_every(&studio, Duration::from_millis(10));
        let start = Instant::now();
        let mut saw = false;
        while start.elapsed() < Duration::from_secs(3) {
            app.frames(2);
            thread::sleep(Duration::from_millis(20));
            if studio.depth.approvals.get().iter().any(|a| a.id == "ap-1") {
                saw = true;
                break;
            }
        }
        assert!(
            saw,
            "the approvals poller must post the list onto the UI thread without panicking"
        );
    }

    #[test]
    fn edit_prefills_the_composer_when_idle() {
        let studio = Studio::new();
        studio.active.set(Some("s1".into()));
        edit_user_message(&studio, "u1", "please review this");
        assert_eq!(studio.depth.draft_text.get(), "please review this");
        assert_eq!(studio.depth.composer_value.get(), "please review this");
        assert!(studio.depth.draft_nonce.get() >= 1);
    }

    #[test]
    fn edit_is_a_no_op_while_a_turn_is_streaming() {
        let studio = Studio::new();
        studio.active.set(Some("s1".into()));
        studio.streaming.set(true);
        edit_user_message(&studio, "u1", "please review this");
        assert!(studio.depth.draft_text.get().is_empty());
        assert_eq!(studio.depth.draft_nonce.get(), 0);
    }

    #[test]
    fn regenerate_is_a_no_op_without_a_preceding_user_turn() {
        let studio = Studio::new();
        studio.active.set(Some("s1".into()));
        studio.messages.set(vec![ChatMessage::plain("a1", "assistant", "hello")]);
        regenerate(&studio, "a1");
        assert!(studio.depth.draft_text.get().is_empty());
        assert!(studio.depth.notice.get().is_empty());
        assert!(!studio.streaming.get());
    }

    #[test]
    fn session_sync_pending_batches_a_reload() {
        let first = pending_sync_push(None);
        let second = pending_sync_push(Some("s1".into()));
        assert!(second > first);
        assert_eq!(pending_sync_take().as_deref(), Some("s1"));
        assert!(pending_sync_take().is_none());
    }
}
