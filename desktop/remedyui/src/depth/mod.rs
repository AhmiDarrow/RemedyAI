//! Chat-depth state: attachments, jobs, banners, sidebar order, voice.
//!
//! Widget code reads these signals. The decisions live in [`logic`].

mod actions;
mod api;
pub(crate) mod banners;
pub(crate) mod composer;
pub(crate) mod dialogs;
mod markdown;
pub(crate) mod markup;
pub(crate) mod panels;

pub(crate) mod logic;

pub(crate) use actions::{
    add_paths, after_finish, apply_frame, approve_plan, ask_delete_skill, ask_remove_project,
    assign_project, background_title, boot, bulk_assign_project, continue_pending, delete_skill, dismiss_library, edit_user_message,
    export_active, focus_session, focused_steps, import_session, install_library, install_named,
    load_memory, load_skills, move_project_order, move_session_order, open_skill, pick_attachments,
    pick_folder, place_session, project_confirm_body, react, regenerate, refresh_banners, remove_project,
    ask_archive_unused, export_skills_pack, import_skills_pack, archive_unused_skills,
    rename_session, request_plan_changes, resolve_approval, save_skill, search_library,
    search_memory, send_life, show_plan, speak_reply, stall_seconds, submit, toggle_mic,
    trust_skill, visible_approval,
};
pub(crate) use markdown::{blocks, inline_rich};

use crate::runtime;
use crate::work::WorkStep;
use remedy_ui::reactive::Signal;
use remedy_ui::{App, Ui, Window};
use std::cell::RefCell;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};

pub(crate) use logic::SlashCommand;

/// How a busy composer submits.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SubmitKind {
    /// A new turn when the session is idle. While it is busy this steers.
    Send,
    /// Talk to the live turn.
    Steer,
    /// Stop the live turn, then send this next.
    Interrupt,
    /// Wait until the live turn ends.
    After,
}

/// One file on the composer rail.
#[derive(Clone, Debug)]
pub struct Attachment {
    pub name: String,
    pub path: String,
    pub mime: String,
    pub size: u64,
    pub is_image: bool,
    pub is_text: bool,
    pub pending: bool,
}

/// A prompt waiting for the current turn to end.
#[derive(Clone, Debug)]
pub struct Queued {
    pub id: u64,
    pub text: String,
    pub mode: String,
    pub attachments: Vec<Attachment>,
}

/// A send held for the concurrent-turn confirm.
#[derive(Clone, Debug)]
pub struct PendingSend {
    pub text: String,
    pub attachments: Vec<Attachment>,
    pub kind: SubmitKind,
}

/// One session's live turn, painted when that session is focused.
#[derive(Clone, Debug)]
pub struct StreamJob {
    pub session_id: String,
    pub status: String,
    pub partial: String,
    pub breaks: Vec<usize>,
    pub message_id: String,
    pub thinking: String,
    pub steps: Vec<WorkStep>,
    pub request_id: String,
    pub last_event_ms: i64,
    pub outgoing: String,
    pub outgoing_files: bool,
    pub held: Vec<Attachment>,
    pub todos: Vec<TodoItem>,
    pub progress: Option<f32>,
}

impl StreamJob {
    fn running(session_id: String) -> Self {
        Self {
            session_id,
            status: "running".into(),
            partial: String::new(),
            breaks: Vec::new(),
            message_id: String::new(),
            thinking: String::new(),
            steps: Vec::new(),
            request_id: String::new(),
            last_event_ms: now_ms(),
            outgoing: String::new(),
            outgoing_files: false,
            held: Vec::new(),
            todos: Vec::new(),
            progress: None,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TodoItem {
    pub id: String,
    pub content: String,
    pub status: String,
}

#[derive(Clone, Debug, Default)]
pub struct Usage {
    pub input: u64,
    pub output: u64,
    /// Dollars, when the runtime sent a cost.
    pub cost: Option<f64>,
}

#[derive(Clone, Debug)]
pub struct Approval {
    pub id: String,
    pub session_id: String,
    pub tool: String,
    pub summary: String,
    pub blocking: bool,
    pub origin: String,
}

#[derive(Clone, Debug)]
pub struct PlanCard {
    pub status: String,
    pub goal: String,
    pub steps: Vec<(String, String)>,
    pub done: u32,
    pub total: u32,
}

#[derive(Clone, Debug)]
pub struct LifeCard {
    pub status: String,
    pub spoken: String,
    pub goal: String,
    pub title: String,
    pub step: Option<u64>,
    pub total: Option<u64>,
    pub steps: Vec<String>,
    pub approval: bool,
}

#[derive(Clone, Debug)]
pub struct LibraryChip {
    pub id: String,
    pub name: String,
    pub reason: String,
}

#[derive(Clone, Debug, Default)]
pub struct VoicePrefs {
    pub speak_replies: bool,
    pub stt: bool,
    pub tts: bool,
    pub hint: String,
}

#[derive(Clone, Debug)]
pub struct SkillRow {
    pub name: String,
    pub description: String,
    pub status: String,
    pub quarantine: bool,
    pub auto_generated: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SidePanel {
    None,
    Memory,
    Skills,
}

#[derive(Clone, Debug)]
pub enum Confirm {
    RemoveProject(String),
    DeleteSkill(String),
    ArchiveUnusedSkills,
}

/// Signals the chat column, sidebar, and dialogs share.
#[derive(Clone)]
pub struct Depth {
    pub save_enabled: bool,
    poll_started: Arc<AtomicBool>,
    session_events_started: Arc<AtomicBool>,
    coordination_started: Arc<AtomicBool>,
    /// Other live sessions from `GET /api/coordination/presence`.
    pub siblings: Signal<Vec<logic::SiblingBeacon>>,
    tick_started: Arc<AtomicBool>,
    pub samples: Arc<Mutex<Vec<f32>>>,
    pub draft_nonce: Signal<u64>,
    pub draft_text: Signal<String>,
    /// Latest composer text, so a transcript can append without fighting the caret.
    pub composer_value: Signal<String>,
    pub attachments: Signal<Vec<Attachment>>,
    pub drag_over: Signal<bool>,
    pub notice: Signal<String>,
    pub commands: Signal<Vec<SlashCommand>>,
    pub agents: Signal<Vec<String>>,
    pub prompt_history: Signal<Vec<String>>,
    pub project_order: Signal<Vec<String>>,
    pub session_order: Signal<Vec<(String, Vec<String>)>>,
    pub approvals: Signal<Vec<Approval>>,
    pub plan: Signal<Option<PlanCard>>,
    pub life: Signal<Option<LifeCard>>,
    pub library: Signal<Option<LibraryChip>>,
    pub usage_run: Signal<Usage>,
    pub usage_session: Signal<Usage>,
    pub hide_ticker: Signal<bool>,
    pub hide_cost: Signal<bool>,
    pub todos: Signal<Vec<TodoItem>>,
    pub progress: Signal<Option<f32>>,
    pub stuck: Signal<bool>,
    pub lightbox: Signal<Option<String>>,
    pub lightbox_gen: Signal<u64>,
    pub markup: markup::Markup,
    pub panel: Signal<SidePanel>,
    pub panel_tab: Signal<String>,
    pub confirm: Signal<Option<Confirm>>,
    pub confirm_gen: Signal<u64>,
    pub concurrent_open: Signal<bool>,
    pub concurrent_gen: Signal<u64>,
    pub pending: Signal<Option<PendingSend>>,
    pub jobs: Signal<Vec<StreamJob>>,
    pub queue: Signal<Vec<Queued>>,
    next_queue: Signal<u64>,
    pub stall_hidden: Signal<bool>,
    pub tick: Signal<u64>,
    pub voice: Signal<VoicePrefs>,
    pub mic_on: Signal<bool>,
    pub rename_id: Signal<Option<String>>,
    /// Session whose add-tag field is open.
    pub tag_id: Signal<Option<String>>,
    /// Draft in the add-tag field.
    pub tag_draft: Signal<String>,
    pub remote_busy: Signal<bool>,
    pub memory_body: Signal<String>,
    pub skills: Signal<Vec<SkillRow>>,
    /// Status filter on the installed skills list.
    pub skill_filter: Signal<String>,
    pub skill_budget: Signal<logic::SkillBudget>,
    pub skill_learning: Signal<logic::SkillLearning>,
    pub skill_name: Signal<String>,
    pub skill_body: Signal<String>,
    pub library_rows: Signal<Vec<SkillRow>>,
    pub trace_open: Signal<bool>,
    pub suggest_gen: Signal<u64>,
}

impl Depth {
    pub fn new(local_only: bool) -> Self {
        let save_enabled = !local_only && !runtime::spawn_disabled();
        let (history, projects, sessions) = if save_enabled {
            load_disk()
        } else {
            (Vec::new(), Vec::new(), Vec::new())
        };
        Self {
            save_enabled,
            poll_started: Arc::new(AtomicBool::new(false)),
            session_events_started: Arc::new(AtomicBool::new(false)),
            coordination_started: Arc::new(AtomicBool::new(false)),
            siblings: Signal::new(Vec::new()),
            tick_started: Arc::new(AtomicBool::new(false)),
            samples: Arc::new(Mutex::new(Vec::new())),
            draft_nonce: Signal::new(0),
            draft_text: Signal::new(String::new()),
            composer_value: Signal::new(String::new()),
            attachments: Signal::new(Vec::new()),
            drag_over: Signal::new(false),
            notice: Signal::new(String::new()),
            commands: Signal::new(logic::fallback_commands()),
            agents: Signal::new(Vec::new()),
            prompt_history: Signal::new(history),
            project_order: Signal::new(projects),
            session_order: Signal::new(sessions),
            approvals: Signal::new(Vec::new()),
            plan: Signal::new(None),
            life: Signal::new(None),
            library: Signal::new(None),
            usage_run: Signal::new(Usage::default()),
            usage_session: Signal::new(Usage::default()),
            hide_ticker: Signal::new(false),
            hide_cost: Signal::new(false),
            todos: Signal::new(Vec::new()),
            progress: Signal::new(None),
            stuck: Signal::new(true),
            lightbox: Signal::new(None),
            lightbox_gen: Signal::new(0),
            markup: markup::Markup::new(),
            panel: Signal::new(SidePanel::None),
            panel_tab: Signal::new("memory".into()),
            confirm: Signal::new(None),
            confirm_gen: Signal::new(0),
            concurrent_open: Signal::new(false),
            concurrent_gen: Signal::new(0),
            pending: Signal::new(None),
            jobs: Signal::new(Vec::new()),
            queue: Signal::new(Vec::new()),
            next_queue: Signal::new(1),
            stall_hidden: Signal::new(false),
            tick: Signal::new(0),
            voice: Signal::new(VoicePrefs::default()),
            mic_on: Signal::new(false),
            rename_id: Signal::new(None),
            tag_id: Signal::new(None),
            tag_draft: Signal::new(String::new()),
            remote_busy: Signal::new(false),
            memory_body: Signal::new(String::new()),
            skills: Signal::new(Vec::new()),
            skill_filter: Signal::new(logic::SKILL_FILTER_ALL.into()),
            skill_budget: Signal::new(logic::SkillBudget::default()),
            skill_learning: Signal::new(logic::SkillLearning::default()),
            skill_name: Signal::new(String::new()),
            skill_body: Signal::new(String::new()),
            library_rows: Signal::new(Vec::new()),
            trace_open: Signal::new(false),
            suggest_gen: Signal::new(0),
        }
    }

    pub fn running_count(&self) -> usize {
        self.jobs
            .get()
            .iter()
            .filter(|j| j.status == "running")
            .count()
    }

    pub fn session_running(&self, sid: &str) -> bool {
        self.jobs
            .get()
            .iter()
            .any(|j| j.session_id == sid && j.status == "running")
    }

    pub fn job(&self, sid: &str) -> Option<StreamJob> {
        self.jobs.get().into_iter().find(|j| j.session_id == sid)
    }

    pub fn map_job(&self, sid: &str, f: impl FnOnce(&mut StreamJob)) {
        let sid = sid.to_string();
        self.jobs.update(|jobs| {
            if let Some(job) = jobs.iter_mut().find(|j| j.session_id == sid) {
                f(job);
            } else {
                let mut job = StreamJob::running(sid);
                f(&mut job);
                jobs.push(job);
            }
        });
    }

    pub fn finish_job(&self, sid: &str, status: &str) {
        let sid = sid.to_string();
        let status = status.to_string();
        self.jobs.update(|jobs| {
            if let Some(job) = jobs.iter_mut().find(|j| j.session_id == sid) {
                job.status = status;
            }
        });
    }

    pub fn push_queued(&self, text: String, mode: &str, attachments: Vec<Attachment>) -> Queued {
        let id = self.next_queue.get();
        self.next_queue.set(id + 1);
        let item = Queued {
            id,
            text,
            mode: mode.to_string(),
            attachments,
        };
        let saved = item.clone();
        self.queue.update(|q| q.push(item));
        saved
    }

    /// Inserts at the front of the queue and returns the row (a real id, not zero).
    pub fn insert_front(&self, text: String, mode: &str, attachments: Vec<Attachment>) -> Queued {
        let id = self.next_queue.get();
        self.next_queue.set(id + 1);
        let item = Queued {
            id,
            text,
            mode: mode.to_string(),
            attachments,
        };
        let saved = item.clone();
        self.queue.update(|q| q.insert(0, item));
        saved
    }

    pub fn remember_prompt(&self, text: &str) {
        let next = logic::push_history(&self.prompt_history.get(), text);
        self.prompt_history.set(next.clone());
        if self.save_enabled {
            let _ = std::fs::create_dir_all(desktop_dir());
            if let Ok(raw) = serde_json::to_string_pretty(&next) {
                let _ = std::fs::write(desktop_dir().join("composer-history.json"), raw);
            }
        }
    }

    pub fn save_order(&self) {
        if !self.save_enabled {
            return;
        }
        let body = serde_json::json!({
            "projects": self.project_order.get(),
            "sessions": self.session_order.get(),
        });
        let _ = std::fs::create_dir_all(desktop_dir());
        if let Ok(raw) = serde_json::to_string_pretty(&body) {
            let _ = std::fs::write(desktop_dir().join("sidebar-order.json"), raw);
        }
    }

    pub fn ask_confirm(&self, confirm: Confirm) {
        self.confirm.set(Some(confirm));
        self.confirm_gen.update(|n| *n += 1);
    }

    pub fn ask_concurrent(&self, pending: PendingSend) {
        self.pending.set(Some(pending));
        self.concurrent_open.set(true);
        self.concurrent_gen.update(|n| *n += 1);
    }

    pub fn show_image(&self, src: String) {
        self.markup.reset();
        self.markup.src_name.set(markup::src_name(&src));
        self.lightbox.set(Some(src));
        self.lightbox_gen.update(|n| *n += 1);
    }

    pub fn poll_flag(&self) -> Arc<AtomicBool> {
        self.poll_started.clone()
    }

    pub fn session_events_flag(&self) -> Arc<AtomicBool> {
        self.session_events_started.clone()
    }

    pub fn coordination_flag(&self) -> Arc<AtomicBool> {
        self.coordination_started.clone()
    }

    pub fn tick_flag(&self) -> Arc<AtomicBool> {
        self.tick_started.clone()
    }
}

pub(crate) fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn desktop_dir() -> PathBuf {
    runtime::remedy_home().join("desktop")
}

pub(crate) fn load_session_tags() -> Vec<(String, Vec<String>)> {
    std::fs::read_to_string(desktop_dir().join("session-tags.json"))
        .ok()
        .map(|raw| logic::parse_session_tags_file(&raw))
        .unwrap_or_default()
}

pub(crate) fn save_session_tags(sessions: &[crate::studio::Session]) {
    let _ = std::fs::create_dir_all(desktop_dir());
    let _ = std::fs::write(
        desktop_dir().join("session-tags.json"),
        logic::session_tags_file_body(sessions),
    );
}

fn load_disk() -> (Vec<String>, Vec<String>, Vec<(String, Vec<String>)>) {
    let dir = desktop_dir();
    let history = std::fs::read_to_string(dir.join("composer-history.json"))
        .ok()
        .and_then(|raw| serde_json::from_str::<Vec<String>>(&raw).ok())
        .unwrap_or_default();
    let order = std::fs::read_to_string(dir.join("sidebar-order.json"))
        .ok()
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok());
    let projects = order
        .as_ref()
        .and_then(|v| v.get("projects"))
        .and_then(|v| serde_json::from_value::<Vec<String>>(v.clone()).ok())
        .unwrap_or_default();
    let sessions = order
        .as_ref()
        .and_then(|v| v.get("sessions"))
        .and_then(|v| serde_json::from_value::<Vec<(String, Vec<String>)>>(v.clone()).ok())
        .unwrap_or_default();
    (history, projects, sessions)
}

thread_local! {
    static SPEAK: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
    static UI: RefCell<Option<Ui>> = const { RefCell::new(None) };
    static HOST: RefCell<Option<(App, Window)>> = const { RefCell::new(None) };
    static MIC: RefCell<Option<remedy_ui::playback::Microphone>> = const { RefCell::new(None) };
    static PLAYER: RefCell<Option<remedy_ui::playback::Player>> = const { RefCell::new(None) };
}

/// The Settings toggle for spoken replies, when the person has changed it this window.
pub(crate) fn note_speak_replies(on: bool) {
    SPEAK.with(|c| c.set(Some(on)));
}

pub(crate) fn speak_override() -> Option<bool> {
    SPEAK.with(|c| c.get())
}

/// The shell's `Ui`, so a worker can hop back and play audio on the UI thread.
pub fn set_ui(ui: Ui) {
    UI.with(|slot| *slot.borrow_mut() = Some(ui));
}

pub fn with_ui(f: impl FnOnce(&Ui)) {
    UI.with(|slot| {
        if let Some(ui) = slot.borrow().as_ref() {
            f(ui);
        }
    });
}

/// The real window, for native file dialogs. Previews leave this empty.
pub fn set_host(app: App, window: Window) {
    HOST.with(|slot| *slot.borrow_mut() = Some((app, window)));
}

pub fn with_host(f: impl FnOnce(&App, &Window)) -> bool {
    HOST.with(|slot| {
        if let Some((app, window)) = slot.borrow().as_ref() {
            f(app, window);
            true
        } else {
            false
        }
    })
}

pub(crate) fn take_mic() -> Option<remedy_ui::playback::Microphone> {
    MIC.with(|slot| slot.borrow_mut().take())
}

pub(crate) fn put_mic(mic: remedy_ui::playback::Microphone) {
    MIC.with(|slot| *slot.borrow_mut() = Some(mic));
}

pub(crate) fn put_player(player: remedy_ui::playback::Player) {
    PLAYER.with(|slot| *slot.borrow_mut() = Some(player));
}
