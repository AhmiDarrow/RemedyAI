//! Grove's loopback calls: goals, the head, approvals, sessions, and the stage.
//!
//! Same runtime as Studio, HTTP on 127.0.0.1:7400. Grove sessions are created
//! with `origin_channel: "grove"` so the Studio list (which already drops that
//! channel) never shows them.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::Cancel;
use serde_json::{Map, Value};

use crate::runtime::{self, Client};
use crate::sse::{self, Parser};
use crate::studio::WorkMode;

use super::logic::{StoryMessage, ToolCall};

/// One goal on the life board.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LifeGoal {
    /// Stable id.
    pub id: String,
    /// Title the owner sees.
    pub title: String,
    /// Horizon or status word under the title.
    pub horizon: String,
    /// Status, when the board sent one.
    pub status: String,
    /// Next step, if Remedy has one.
    pub next_action: String,
    /// What done looks like.
    pub done_looks_like: String,
}

/// The last thing Remedy did while the owner was away.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LastStep {
    /// Plain sentence.
    pub did: String,
    /// Goal it belonged to, if any.
    pub goal: String,
}

/// The life board.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LifeBoard {
    /// Open goals.
    pub goals: Vec<LifeGoal>,
    /// Last drive step, if one is still showing.
    pub last_step: Option<LastStep>,
}

/// One side job.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HeadChild {
    /// Child id.
    pub child_id: String,
    /// The child's own session.
    pub session_id: String,
    /// Short name.
    pub label: String,
    /// The task she was given.
    pub task: String,
    /// `working`, `queued`, `done`, and the rest.
    pub state: String,
    /// Latest report line.
    pub last_report: String,
}

/// A standing routine.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HeadRoutine {
    /// Routine id.
    pub routine_id: String,
    /// Name.
    pub title: String,
    /// What she does or says.
    pub what: String,
    /// `remind` or `task`.
    pub mode: String,
    /// When, in the owner's words.
    pub when: String,
    /// On or paused.
    pub enabled: bool,
    /// Next run, ISO.
    pub next_run: String,
    /// Last state token.
    pub last_state: String,
    /// `head` opens her conversation.
    pub target: String,
}

/// A goal's link to a conversation and autopilot.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct GoalLink {
    /// Goal id.
    pub goal_id: String,
    /// Bound session, if any.
    pub session_id: String,
    /// Keep going on its own.
    pub autopilot: bool,
    /// State token.
    pub state: String,
    /// Latest note.
    pub last_update: String,
}

/// One line of "while you were away".
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Activity {
    /// Stable id.
    pub id: String,
    /// When it happened.
    pub ts: String,
    /// `child`, `routine`, `heartbeat`, and the rest.
    pub kind: String,
    /// Title.
    pub title: String,
    /// Detail line.
    pub detail: String,
    /// State token, if any.
    pub state: String,
    /// Session to open, if the row is a door.
    pub session_id: String,
}

/// A messenger the owner can receive updates on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Channel {
    /// Channel name.
    pub channel: String,
    /// Chat id.
    pub chat_id: String,
    /// Display name.
    pub username: String,
}

/// Heartbeat (keep watch).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Heartbeat {
    /// On.
    pub enabled: bool,
    /// Minutes between looks.
    pub every_minutes: i64,
    /// Quiet from `HH:MM`.
    pub quiet_start: String,
    /// Quiet until `HH:MM`.
    pub quiet_end: String,
    /// Last note.
    pub last_note: String,
}

/// How busy this PC is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Capacity {
    /// Slots.
    pub slots: i64,
    /// Busy slots.
    pub busy: i64,
    /// Jobs are waiting because the PC is busy.
    pub paused: bool,
    /// Memory figure is known.
    pub memory_known: bool,
    /// Free memory percent.
    pub free_mem_pct: i64,
}

/// Remedy at the head of the house.
#[derive(Clone, Debug, PartialEq)]
pub struct HeadOverview {
    /// Her own session.
    pub session_id: String,
    /// Her state.
    pub state: String,
    /// Capacity sentence.
    pub capacity_note: String,
    /// Slots.
    pub capacity: Capacity,
    /// Side jobs.
    pub children: Vec<HeadChild>,
    /// Goal links.
    pub goals: Vec<GoalLink>,
    /// Routines.
    pub routines: Vec<HeadRoutine>,
    /// Timeline.
    pub activity: Vec<Activity>,
    /// Unseen timeline rows.
    pub activity_unseen: i64,
    /// Keep watch.
    pub heartbeat: Heartbeat,
    /// Where updates can also go.
    pub owner_channel: Option<Channel>,
    /// Recent messenger chats.
    pub recent_channels: Vec<Channel>,
}

impl HeadOverview {
    /// Link for one goal.
    pub fn goal(&self, id: &str) -> Option<&GoalLink> {
        self.goals.iter().find(|g| g.goal_id == id)
    }
}

/// A decision the owner has not answered.
#[derive(Clone, Debug, PartialEq)]
pub struct Approval {
    /// Id.
    pub id: String,
    /// Tool.
    pub tool_name: String,
    /// Command text or JSON.
    pub command: String,
    /// Reason.
    pub reason: String,
    /// Plain headline.
    pub summary: String,
    /// Money, password, or send.
    pub sensitive: bool,
    /// Origin channel.
    pub origin: String,
    /// Legacy origin.
    pub channel: String,
    /// Session, if the decision belongs to one.
    pub session_id: String,
    /// A live turn is paused on this.
    pub blocking: bool,
    /// Status. Empty means pending.
    pub status: String,
    /// When she asked.
    pub created_at: i64,
}

/// A file sitting on the talk bar.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Attachment {
    /// Server path.
    pub path: String,
    /// File name.
    pub name: String,
    /// MIME type.
    pub mime: String,
    /// Size in bytes.
    pub size: u64,
    /// Image.
    pub is_image: bool,
    /// Text.
    pub is_text: bool,
}

/// One build-checklist row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Todo {
    /// Id.
    pub id: String,
    /// Words.
    pub content: String,
    /// `pending`, `in_progress`, `completed`, or `cancelled`.
    pub status: String,
}

/// The life-task card Grove docks above the board.
#[derive(Clone, Debug, PartialEq, Eq, Default)]
pub struct LifeTask {
    /// Task id.
    pub task_id: String,
    /// Goal title.
    pub goal: String,
    /// Status token.
    pub status: String,
    /// Spoken line.
    pub spoken: String,
    /// Step number.
    pub step: i64,
    /// Step count.
    pub total: i64,
    /// Step title.
    pub title: String,
    /// Yes / no / explain, when she is asking.
    pub choices: Vec<String>,
    /// Approval id, if this card is also a checkpoint.
    pub approval_id: String,
}

/// Borrow the runtime HTTP client. Grove stays on the same bearer and base.
pub(crate) fn http(client: &Client) -> &Http {
    client.http()
}

fn send(client: &Client, req: Request) -> Result<Value, String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(req, &cancel)
        .map_err(|e| e.to_string())
        .and_then(|r| r.json::<Value>().map_err(|e| e.to_string()))
}

fn str_of(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| match x {
            Value::String(s) => Some(s.clone()),
            Value::Number(n) => Some(n.to_string()),
            Value::Bool(b) => Some(b.to_string()),
            _ => None,
        })
        .unwrap_or_default()
}

fn bool_of(v: &Value, key: &str) -> bool {
    v.get(key).and_then(|x| x.as_bool()).unwrap_or(false)
}

fn i64_of(v: &Value, key: &str) -> i64 {
    v.get(key)
        .and_then(|x| x.as_i64().or_else(|| x.as_f64().map(|n| n as i64)))
        .unwrap_or(0)
}

/// GET `/api/goals`.
pub fn life_board(client: &Client) -> Result<LifeBoard, String> {
    let v = send(client, Request::get("goals"))?;
    let goals = v
        .get("goals")
        .and_then(|g| g.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|g| {
                    let id = str_of(g, "id");
                    if id.is_empty() {
                        return None;
                    }
                    Some(LifeGoal {
                        id,
                        title: str_of(g, "title"),
                        horizon: str_of(g, "horizon"),
                        status: str_of(g, "status"),
                        next_action: str_of(g, "next_action"),
                        done_looks_like: str_of(g, "done_looks_like"),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let last_step = v.get("last_step").and_then(|s| {
        if s.is_null() {
            return None;
        }
        let did = str_of(s, "did");
        if did.is_empty() {
            None
        } else {
            Some(LastStep {
                did,
                goal: str_of(s, "goal"),
            })
        }
    });
    Ok(LifeBoard { goals, last_step })
}

/// POST `/api/goals`.
pub fn create_goal(client: &Client, title: &str) -> Result<LifeGoal, String> {
    let v = send(
        client,
        Request::post("goals").json(&serde_json::json!({ "title": title })),
    )?;
    let id = str_of(&v, "id");
    if id.is_empty() {
        return Err("the runtime did not return a goal".into());
    }
    Ok(LifeGoal {
        id,
        title: {
            let t = str_of(&v, "title");
            if t.is_empty() {
                title.to_string()
            } else {
                t
            }
        },
        horizon: str_of(&v, "horizon"),
        status: str_of(&v, "status"),
        next_action: str_of(&v, "next_action"),
        done_looks_like: str_of(&v, "done_looks_like"),
    })
}

/// DELETE `/api/goals/{id}`.
pub fn delete_goal(client: &Client, id: &str) -> Result<(), String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(Request::delete(&format!("goals/{id}")), &cancel)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// POST `/api/goals/activity/clear`.
pub fn clear_activity(client: &Client) -> Result<(), String> {
    send(client, Request::post("goals/activity/clear").json(&serde_json::json!({})))?;
    Ok(())
}

/// PATCH `/api/goals/{id}` — title, status, and the other board fields.
pub fn patch_goal(client: &Client, id: &str, body: &Value) -> Result<(), String> {
    patch(client, &format!("goals/{id}"), body).map(|_| ())
}

/// GET `/api/partner/head`.
pub fn head(client: &Client) -> Result<HeadOverview, String> {
    let v = send(client, Request::get("partner/head"))?;
    parse_head(&v)
}

fn parse_head(v: &Value) -> Result<HeadOverview, String> {
    let head = v.get("head").cloned().unwrap_or(Value::Null);
    let cap = v.get("capacity").cloned().unwrap_or(Value::Null);
    let hb = v.get("heartbeat").cloned().unwrap_or(Value::Null);
    let channels = v.get("channels").cloned().unwrap_or(Value::Null);
    let children = arr(&v, "children")
        .into_iter()
        .filter_map(|c| {
            let child_id = str_of(&c, "child_id");
            if child_id.is_empty() {
                return None;
            }
            Some(HeadChild {
                child_id,
                session_id: str_of(&c, "session_id"),
                label: {
                    let l = str_of(&c, "label");
                    if l.is_empty() { "Side job".into() } else { l }
                },
                task: str_of(&c, "task"),
                state: {
                    let s = str_of(&c, "state");
                    if s.is_empty() { "idle".into() } else { s }
                },
                last_report: str_of(&c, "last_report"),
            })
        })
        .collect();
    let goals = v
        .get("goals")
        .and_then(|g| g.as_object())
        .map(|map| {
            map.iter()
                .map(|(id, link)| GoalLink {
                    goal_id: str_of(link, "goal_id").is_empty().then(|| id.clone()).unwrap_or_else(|| str_of(link, "goal_id")),
                    session_id: str_of(link, "session_id"),
                    autopilot: bool_of(link, "autopilot"),
                    state: str_of(link, "state"),
                    last_update: str_of(link, "last_update"),
                })
                .collect()
        })
        .unwrap_or_default();
    let routines = arr(v, "routines")
        .into_iter()
        .filter_map(|r| {
            let routine_id = str_of(&r, "routine_id");
            if routine_id.is_empty() {
                return None;
            }
            Some(HeadRoutine {
                routine_id,
                title: str_of(&r, "title"),
                what: str_of(&r, "what"),
                mode: str_of(&r, "mode"),
                when: str_of(&r, "when"),
                enabled: bool_of(&r, "enabled"),
                next_run: str_of(&r, "next_run"),
                last_state: str_of(&r, "last_state"),
                target: str_of(&r, "target"),
            })
        })
        .collect();
    let activity = arr(v, "activity")
        .into_iter()
        .enumerate()
        .map(|(i, a)| Activity {
            id: {
                let id = str_of(&a, "id");
                if id.is_empty() { format!("a{i}") } else { id }
            },
            ts: str_of(&a, "ts"),
            kind: str_of(&a, "kind"),
            title: str_of(&a, "title"),
            detail: str_of(&a, "detail"),
            state: str_of(&a, "state"),
            session_id: str_of(&a, "session_id"),
        })
        .collect();
    let recent = channels
        .get("recent")
        .and_then(|r| r.as_array())
        .map(|arr| arr.iter().map(parse_channel).collect())
        .unwrap_or_default();
    let owner = channels.get("owner").and_then(|o| {
        if o.is_null() {
            None
        } else {
            let c = parse_channel(o);
            if c.channel.is_empty() { None } else { Some(c) }
        }
    });
    Ok(HeadOverview {
        session_id: str_of(&head, "session_id"),
        state: {
            let s = str_of(&head, "state");
            if s.is_empty() { "idle".into() } else { s }
        },
        capacity_note: str_of(v, "capacity_note"),
        capacity: Capacity {
            slots: i64_of(&cap, "slots"),
            busy: i64_of(&cap, "busy"),
            paused: bool_of(&cap, "paused"),
            memory_known: bool_of(&cap, "memory_known"),
            free_mem_pct: i64_of(&cap, "free_mem_pct"),
        },
        children,
        goals,
        routines,
        activity,
        activity_unseen: i64_of(v, "activity_unseen"),
        heartbeat: Heartbeat {
            enabled: bool_of(&hb, "enabled"),
            every_minutes: i64_of(&hb, "every_minutes"),
            quiet_start: str_of(&hb, "quiet_start"),
            quiet_end: str_of(&hb, "quiet_end"),
            last_note: str_of(&hb, "last_note"),
        },
        owner_channel: owner,
        recent_channels: recent,
    })
}

fn parse_channel(v: &Value) -> Channel {
    Channel {
        channel: str_of(v, "channel"),
        chat_id: str_of(v, "chat_id"),
        username: str_of(v, "username"),
    }
}

fn arr(v: &Value, key: &str) -> Vec<Value> {
    v.get(key)
        .and_then(|a| a.as_array())
        .cloned()
        .unwrap_or_default()
}

/// POST `/api/partner/head/session`.
pub fn ensure_head_session(client: &Client, session_id: Option<&str>) -> Result<String, String> {
    let body = match session_id {
        Some(id) if !id.is_empty() => serde_json::json!({ "session_id": id }),
        _ => serde_json::json!({}),
    };
    let v = send(client, Request::post("partner/head/session").json(&body))?;
    let id = str_of(&v, "session_id");
    if id.is_empty() {
        Err("the runtime did not return a head session".into())
    } else {
        Ok(id)
    }
}

/// PUT `/api/partner/goals/{id}`.
pub fn set_goal_link(client: &Client, goal_id: &str, body: &Value) -> Result<(), String> {
    send(
        client,
        Request::put(&format!("partner/goals/{goal_id}")).json(body),
    )?;
    Ok(())
}

/// POST `/api/partner/goals/{id}/step`.
pub fn step_goal(client: &Client, goal_id: &str) -> Result<(), String> {
    send(
        client,
        Request::post(&format!("partner/goals/{goal_id}/step")).json(&serde_json::json!({})),
    )?;
    Ok(())
}

/// PUT `/api/partner/heartbeat`.
pub fn set_heartbeat(client: &Client, body: &Value) -> Result<(), String> {
    send(client, Request::put("partner/heartbeat").json(body))?;
    Ok(())
}

/// POST `/api/partner/heartbeat/run`.
pub fn run_heartbeat(client: &Client) -> Result<(), String> {
    send(client, Request::post("partner/heartbeat/run").json(&serde_json::json!({})))?;
    Ok(())
}

/// POST `/api/partner/children`.
pub fn spawn_child(client: &Client, task: &str, label: &str) -> Result<String, String> {
    let mut body = Map::new();
    body.insert("task".into(), Value::String(task.into()));
    if !label.is_empty() {
        body.insert("label".into(), Value::String(label.into()));
    }
    let v = send(client, Request::post("partner/children").json(&Value::Object(body)))?;
    Ok(str_of(&v, "capacity_note"))
}

/// POST `/api/partner/children/{id}/stop`.
pub fn stop_child(client: &Client, id: &str) -> Result<(), String> {
    send(
        client,
        Request::post(&format!("partner/children/{id}/stop")).json(&serde_json::json!({})),
    )?;
    Ok(())
}

/// DELETE `/api/partner/children/{id}`.
pub fn forget_child(client: &Client, id: &str) -> Result<(), String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(
            Request::delete(&format!("partner/children/{id}")),
            &cancel,
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// PATCH `/api/partner/routines/{id}`.
pub fn patch_routine(client: &Client, id: &str, body: &Value) -> Result<(), String> {
    patch(client, &format!("partner/routines/{id}"), body).map(|_| ())
}

/// DELETE `/api/partner/routines/{id}`.
pub fn delete_routine(client: &Client, id: &str) -> Result<(), String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(
            Request::delete(&format!("partner/routines/{id}")),
            &cancel,
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// POST `/api/partner/routines/{id}/run`.
pub fn run_routine(client: &Client, id: &str) -> Result<(), String> {
    send(
        client,
        Request::post(&format!("partner/routines/{id}/run")).json(&serde_json::json!({})),
    )?;
    Ok(())
}

/// POST `/api/partner/routines`.
pub fn create_routine(client: &Client, body: &Value) -> Result<(), String> {
    send(client, Request::post("partner/routines").json(body))?;
    Ok(())
}

/// PUT `/api/partner/channel`.
pub fn set_owner_channel(client: &Client, channel: Option<&Channel>) -> Result<(), String> {
    let body = match channel {
        Some(c) => serde_json::json!({ "channel": c.channel, "chat_id": c.chat_id }),
        None => serde_json::json!({}),
    };
    send(client, Request::put("partner/channel").json(&body))?;
    Ok(())
}

/// POST `/api/partner/activity/seen`.
pub fn mark_activity_seen(client: &Client) -> Result<(), String> {
    send(
        client,
        Request::post("partner/activity/seen").json(&serde_json::json!({})),
    )?;
    Ok(())
}

/// GET `/api/approvals`.
pub fn approvals(client: &Client) -> Result<Vec<Approval>, String> {
    let v = send(client, Request::get("approvals"))?;
    Ok(arr(&v, "approvals")
        .into_iter()
        .filter_map(|a| {
            let id = str_of(&a, "id");
            if id.is_empty() {
                return None;
            }
            Some(Approval {
                id,
                tool_name: str_of(&a, "tool_name"),
                command: str_of(&a, "command"),
                reason: str_of(&a, "reason"),
                summary: str_of(&a, "summary"),
                sensitive: bool_of(&a, "sensitive"),
                origin: str_of(&a, "origin"),
                channel: str_of(&a, "channel"),
                session_id: str_of(&a, "session_id"),
                blocking: bool_of(&a, "blocking"),
                status: str_of(&a, "status"),
                created_at: i64_of(&a, "created_at"),
            })
        })
        .collect())
}

/// POST `/api/approvals/{id}/resolve`.
pub fn resolve_approval(
    client: &Client,
    id: &str,
    approve: bool,
    scope: &str,
) -> Result<String, String> {
    let v = send(
        client,
        Request::post(&format!("approvals/{id}/resolve")).json(&serde_json::json!({
            "approve": approve,
            "scope": scope,
        })),
    )?;
    Ok(str_of(&v, "hint"))
}

/// GET `/api/life-tasks/current`.
pub fn life_task(client: &Client, session_id: Option<&str>) -> Result<Option<LifeTask>, String> {
    let path = match session_id.filter(|s| !s.is_empty()) {
        Some(id) => format!("life-tasks/current?session_id={id}"),
        None => "life-tasks/current".into(),
    };
    let v = send(client, Request::get(&path))?;
    let Some(task) = v.get("task").filter(|t| !t.is_null()) else {
        return Ok(None);
    };
    if task.as_object().is_none() {
        return Ok(None);
    }
    let choices = task
        .get("choices")
        .and_then(|c| c.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|c| c.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    Ok(Some(LifeTask {
        task_id: str_of(task, "task_id"),
        goal: str_of(task, "goal"),
        status: str_of(task, "status"),
        spoken: str_of(task, "spoken"),
        step: i64_of(task, "step"),
        total: i64_of(task, "total"),
        title: str_of(task, "title"),
        choices,
        approval_id: str_of(task, "approval_id"),
    }))
}

/// True when the life-task strip should show.
pub fn life_task_visible(task: &LifeTask) -> bool {
    let st = task.status.to_lowercase();
    if st == "cancelled" {
        return false;
    }
    !task.spoken.is_empty()
        || st == "running"
        || st == "need_you"
        || st == "done"
        || st == "blocked"
        || !task.approval_id.is_empty()
}

/// POST `/api/life-tasks/act`.
pub fn act_life_task(
    client: &Client,
    action: &str,
    session_id: Option<&str>,
    task_id: &str,
    approval_id: &str,
) -> Result<String, String> {
    let mut body = Map::new();
    body.insert("action".into(), Value::String(action.into()));
    if let Some(id) = session_id.filter(|s| !s.is_empty()) {
        body.insert("session_id".into(), Value::String(id.into()));
    }
    if !task_id.is_empty() {
        body.insert("task_id".into(), Value::String(task_id.into()));
    }
    if !approval_id.is_empty() {
        body.insert("approval_id".into(), Value::String(approval_id.into()));
    }
    let v = send(client, Request::post("life-tasks/act").json(&Value::Object(body)))?;
    Ok(str_of(&v, "spoken"))
}

/// GET `/api/sessions/{id}` — project path, if the session still exists.
pub fn session_project(client: &Client, id: &str) -> Result<Option<String>, String> {
    let v = send(client, Request::get(&format!("sessions/{id}")))?;
    if str_of(&v, "id").is_empty() && v.get("id").is_none() {
        return Err("missing session".into());
    }
    let path = str_of(&v, "project_path");
    Ok(if path.is_empty() { None } else { Some(path) })
}

/// POST `/api/sessions` with `origin_channel: grove` and no project folder.
pub fn create_grove_session(client: &Client, title: Option<&str>) -> Result<String, String> {
    let mut body = Map::new();
    body.insert("project_path".into(), Value::String(String::new()));
    body.insert("origin_channel".into(), Value::String("grove".into()));
    if let Some(title) = title.filter(|t| !t.is_empty()) {
        body.insert("title".into(), Value::String(title.into()));
    }
    let v = send(client, Request::post("sessions").json(&Value::Object(body)))?;
    let id = str_of(&v, "id");
    if id.is_empty() {
        Err("the runtime did not return a session".into())
    } else {
        Ok(id)
    }
}

/// GET `/api/sessions/{id}/messages`.
pub fn messages(client: &Client, id: &str, limit: u32) -> Result<Vec<StoryMessage>, String> {
    let v = send(
        client,
        Request::get(&format!("sessions/{id}/messages?limit={limit}")),
    )?;
    Ok(parse_messages(&v))
}

fn parse_messages(v: &Value) -> Vec<StoryMessage> {
    arr(v, "messages")
        .into_iter()
        .filter_map(|row| {
            let id = str_of(&row, "id");
            let role = str_of(&row, "role");
            if id.is_empty() || role.is_empty() || role == "tool" {
                return None;
            }
            let content = match row.get("content") {
                Some(Value::String(s)) => s.clone(),
                Some(other) if !other.is_null() => other.to_string(),
                _ => String::new(),
            };
            let tool_calls = row
                .get("tool_calls")
                .and_then(|t| t.as_array())
                .map(|calls| {
                    calls
                        .iter()
                        .filter_map(|c| {
                            let name = c
                                .get("name")
                                .and_then(|n| n.as_str())
                                .unwrap_or("")
                                .to_string();
                            if name.is_empty() {
                                return None;
                            }
                            let args = c
                                .get("args")
                                .or_else(|| c.get("arguments"))
                                .cloned()
                                .unwrap_or(Value::Object(Map::new()));
                            let args = if let Some(s) = args.as_str() {
                                serde_json::from_str(s).unwrap_or(Value::String(s.to_string()))
                            } else {
                                args
                            };
                            Some(ToolCall { name, args })
                        })
                        .collect()
                })
                .unwrap_or_default();
            Some(StoryMessage {
                id,
                role,
                content,
                reverted: bool_of(&row, "reverted"),
                created_at: str_of(&row, "created_at"),
                tool_calls,
            })
        })
        .collect()
}

/// POST `/api/sessions/{id}/attachments` with the file bytes as base64 JSON.
pub fn upload_file(client: &Client, session_id: &str, path: &std::path::Path) -> Result<Attachment, String> {
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("file")
        .to_string();
    let mime = mime_of(&name);
    let body = serde_json::json!({
        "filename": name,
        "content_type": mime,
        "data_base64": base64(&bytes),
    });
    let v = send(
        client,
        Request::post(&format!("sessions/{session_id}/attachments")).json(&body),
    )?;
    Ok(Attachment {
        path: {
            let p = str_of(&v, "path");
            if p.is_empty() { path.display().to_string() } else { p }
        },
        name: {
            let n = str_of(&v, "name");
            if n.is_empty() { name } else { n }
        },
        mime: {
            let m = str_of(&v, "mime");
            if m.is_empty() { mime.clone() } else { m }
        },
        size: i64_of(&v, "size").max(0) as u64,
        is_image: bool_of(&v, "is_image") || mime.starts_with("image/"),
        is_text: bool_of(&v, "is_text") || mime.starts_with("text/"),
    })
}

fn mime_of(name: &str) -> String {
    let ext = name.rsplit('.').next().unwrap_or("").to_lowercase();
    match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "txt" | "md" | "log" => "text/plain",
        "json" => "application/json",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
    .into()
}

fn base64(data: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    let mut i = 0;
    while i + 3 <= data.len() {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8) | data[i + 2] as u32;
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(T[((n >> 6) & 63) as usize] as char);
        out.push(T[(n & 63) as usize] as char);
        i += 3;
    }
    let rest = data.len() - i;
    if rest == 1 {
        let n = (data[i] as u32) << 16;
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push('=');
        out.push('=');
    } else if rest == 2 {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8);
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(T[((n >> 6) & 63) as usize] as char);
        out.push('=');
    }
    out
}

/// POST `/api/sessions/{id}/steer`. `Ok(true)` means the live turn took the words.
pub fn steer(client: &Client, session_id: &str, message: &str) -> Result<bool, String> {
    let v = send(
        client,
        Request::post(&format!("sessions/{session_id}/steer")).json(&serde_json::json!({
            "message": message,
        })),
    )?;
    Ok(bool_of(&v, "steered"))
}

/// Stream one turn. `on_frame` runs on the worker; hop to the UI before signals.
pub fn stream_turn(
    client: &Client,
    session_id: &str,
    text: &str,
    model: &str,
    provider: &str,
    mode: WorkMode,
    attachments: &[Attachment],
    on_frame: impl Fn(String, String) + Send + Sync + 'static,
) {
    let path = format!("sessions/{session_id}/messages/stream");
    let attach: Vec<Value> = attachments
        .iter()
        .map(|a| {
            serde_json::json!({
                "path": a.path,
                "name": a.name,
                "mime": a.mime,
                "size": a.size,
                "is_image": a.is_image,
                "is_text": a.is_text,
            })
        })
        .collect();
    let mut body = serde_json::json!({
        "message": text,
        "model": model,
        "provider": provider,
        "plan_mode": matches!(mode, WorkMode::Plan),
        "chat_mode": matches!(mode, WorkMode::Chat),
    });
    if !attach.is_empty() {
        body["attachments"] = Value::Array(attach);
    }
    let parser = std::sync::Arc::new(std::sync::Mutex::new(Parser::new()));
    let on_frame = std::sync::Arc::new(on_frame);
    let on_chunk = {
        let parser = parser.clone();
        let on_frame = on_frame.clone();
        move |_res: &remedy_ui::net::Response, chunk: &[u8]| {
            let text = String::from_utf8_lossy(chunk);
            let frames = parser
                .lock()
                .ok()
                .map(|mut p| p.push(&text))
                .unwrap_or_default();
            for f in frames {
                on_frame(f.kind, f.data);
            }
        }
    };
    let on_done = {
        let on_frame = on_frame.clone();
        move |out: Result<remedy_ui::net::Response, remedy_ui::net::NetError>| {
            if let Err(e) = out {
                let data = serde_json::json!({ "type": "error", "message": e.to_string() }).to_string();
                on_frame("error".into(), data);
            } else {
                on_frame("done".into(), "{\"type\":\"done\"}".into());
            }
        }
    };
    client.http().stream(Request::post(&path).json(&body), &Cancel::new(), on_chunk, on_done);
}

/// Text from a token frame. Reuses the Studio parser's shape.
pub fn token_text(data: &str) -> String {
    sse::token_text(data)
}

/// Error text from an error frame.
pub fn error_text(data: &str) -> String {
    sse::error_text(data)
}

/// GET `/api/app/command?take=1`.
pub fn take_app_command(client: &Client) -> Result<Option<Value>, String> {
    let v = send(client, Request::get("app/command?take=1"))?;
    Ok(v.get("command").filter(|c| !c.is_null()).cloned())
}

/// Peek the browser-rail command without taking it (the host still consumes it).
pub fn peek_browser_command(client: &Client) -> Result<Option<Value>, String> {
    let v = send(client, Request::get("computer/ui/command"))?;
    Ok(v.get("command").filter(|c| !c.is_null()).cloned())
}

/// POST `/api/computer/host/hello` so the runtime knows Grove's stage is up.
pub fn browser_hello(client: &Client, session_id: Option<&str>) -> Result<bool, String> {
    let mut body = serde_json::json!({ "client": "remedyui-grove" });
    if let Some(id) = session_id.filter(|s| !s.is_empty()) {
        body["session_id"] = Value::String(id.into());
    }
    let v = send(client, Request::post("computer/host/hello").json(&body))?;
    Ok(bool_of(&v, "host_connected"))
}

/// GET `/api/settings` for the partner's name and gender. Missing keys stay empty.
pub fn settings_identity(client: &Client) -> Result<(String, String), String> {
    let v = send(client, Request::get("settings"))?;
    Ok((str_of(&v, "name"), str_of(&v, "agent_gender")))
}

/// GET `/api/voice/status` — whether speak-back is on.
pub fn speak_replies(client: &Client) -> Result<bool, String> {
    let v = send(client, Request::get("voice/status"))?;
    Ok(v.get("settings")
        .and_then(|s| s.get("speak_replies"))
        .and_then(|b| b.as_bool())
        .unwrap_or(false))
}

/// POST `/api/voice/speak`. Playback of the bytes needs the engine playback
/// feature, which this shell does not turn on; the call still reaches Kokoro.
pub fn speak(client: &Client, text: &str) -> Result<(), String> {
    let cancel = Cancel::new();
    client
        .http()
        .send_blocking(
            Request::post("voice/speak").json(&serde_json::json!({ "text": text })),
            &cancel,
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// PATCH through a short loopback request.
///
/// `remedy_ui::net::Request` has GET, POST, PUT, and DELETE. Goal rename and
/// routine edits are PATCH on the runtime, so Grove speaks that verb itself
/// on the same 127.0.0.1 origin and bearer.
pub fn patch(client: &Client, path: &str, body: &Value) -> Result<Value, String> {
    let _ = client;
    let origin = runtime::origin();
    let (host, port) = loopback_host(&origin)?;
    let payload = serde_json::to_vec(body).map_err(|e| e.to_string())?;
    let token = runtime::load_token().unwrap_or_default();
    let mut req = format!(
        "PATCH /api/{path} HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
        payload.len()
    );
    if token.len() >= 8 {
        req.push_str(&format!("Authorization: Bearer {token}\r\n"));
    }
    req.push_str("\r\n");
    let mut stream = TcpStream::connect((host.as_str(), port)).map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(20)))
        .ok();
    stream
        .set_write_timeout(Some(Duration::from_secs(20)))
        .ok();
    stream.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    stream.write_all(&payload).map_err(|e| e.to_string())?;
    let mut buf = Vec::new();
    stream.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&buf);
    let status = text
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .unwrap_or(0);
    let body = text.split("\r\n\r\n").nth(1).unwrap_or("").trim();
    if !(200..300).contains(&status) {
        return Err(if body.is_empty() {
            format!("PATCH {path} returned {status}")
        } else {
            body.chars().take(240).collect()
        });
    }
    if body.is_empty() {
        Ok(Value::Null)
    } else {
        serde_json::from_str(body).map_err(|e| e.to_string())
    }
}

fn loopback_host(origin: &str) -> Result<(String, u16), String> {
    let rest = origin
        .trim()
        .trim_end_matches('/')
        .trim_start_matches("http://");
    if origin.trim().starts_with("https://") {
        return Err("Grove PATCH expects the loopback HTTP runtime".into());
    }
    let (host, port) = if let Some((h, p)) = rest.rsplit_once(':') {
        (h.to_string(), p.parse::<u16>().map_err(|e| e.to_string())?)
    } else {
        (rest.to_string(), 80)
    };
    if host != "127.0.0.1" && host != "localhost" && host != "[::1]" {
        return Err("Grove PATCH stays on loopback".into());
    }
    Ok((host, port))
}
