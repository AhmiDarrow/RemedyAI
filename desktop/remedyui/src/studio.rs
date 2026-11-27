//! Studio state: sessions, the open chat, composer, and connection.

use crate::runtime::{self, Client};
use crate::time;
use crate::work::{self, WorkStep};
use remedy_ui::reactive::{waker, Signal};

/// How the session list is filtered.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Filter {
    /// Every living (not archived) session.
    All,
    /// Pinned only.
    Pinned,
    /// Archived only.
    Archived,
}

/// Chat / Plan / Build, as the header chip and Shift+Tab cycle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WorkMode {
    /// Conversation, no tools.
    Chat,
    /// Outline first.
    Plan,
    /// Work when you ask (default).
    Build,
}

impl WorkMode {
    /// The chip label.
    pub fn label(self) -> &'static str {
        match self {
            WorkMode::Chat => "Chat",
            WorkMode::Plan => "Plan",
            WorkMode::Build => "Build",
        }
    }

    /// The next mode in the cycle.
    pub fn next(self) -> Self {
        match self {
            WorkMode::Chat => WorkMode::Plan,
            WorkMode::Plan => WorkMode::Build,
            WorkMode::Build => WorkMode::Chat,
        }
    }
}

/// Mind connection, as the status bar shows it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Conn {
    /// No runtime (or we have not tried yet).
    Disconnected,
    /// Spawned or probing `/api/ping`.
    Connecting,
    /// HTTP to 127.0.0.1:7400 is up.
    Ready,
    /// Last probe or stream failed.
    Error(String),
}

/// One Studio session row.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    /// Stable id (server string, or a local preview id).
    pub id: String,
    /// Title shown in the list.
    pub title: String,
    /// Project folder, if any.
    pub project_path: Option<String>,
    /// Pinned to the top of the list.
    pub pinned: bool,
    /// Hidden unless the Archive filter is on.
    pub archived: bool,
    /// Tags, at most a couple shown on the row.
    pub tags: Vec<String>,
    /// Message count from the server (or the open feed).
    pub message_count: u32,
    /// RFC3339 `updated_at`, for the age line.
    pub updated_at: Option<String>,
    /// The runtime still owns a turn on this session.
    pub claimed: bool,
    /// Server request id for a claimed turn, when one is attached.
    pub active_request_id: String,
}

impl Session {
    /// A living, unpinned chat with no project.
    pub(crate) fn fresh(id: impl Into<String>, title: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            title: title.into(),
            project_path: None,
            pinned: false,
            archived: false,
            tags: Vec::new(),
            message_count: 0,
            updated_at: None,
            claimed: false,
            active_request_id: String::new(),
        }
    }
}

/// One chat message in the well.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatMessage {
    /// Stable id.
    pub id: String,
    /// `user`, `assistant`, or `system`.
    pub role: String,
    /// Body text.
    pub content: String,
    /// RFC3339, when known.
    pub created_at: Option<String>,
    /// True while tokens are still arriving.
    pub streaming: bool,
    /// Tool steps behind the Worked line. Empty for a plain reply.
    pub(crate) steps: Vec<WorkStep>,
    /// Model thinking for this turn. Empty when the runtime sent none.
    pub(crate) thinking: String,
    /// The person's reaction on an assistant row. Empty when unset.
    pub user_reaction: String,
    /// The agent's reaction on a user row. Empty when unset.
    pub agent_reaction: String,
    /// Turn id used to fetch full tool evidence (`…/turns/{request_id}/tools/{call_id}`).
    pub(crate) request_id: String,
}

impl ChatMessage {
    /// A finished message with no tool steps and no thinking.
    pub(crate) fn plain(
        id: impl Into<String>,
        role: impl Into<String>,
        content: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            role: role.into(),
            content: content.into(),
            created_at: None,
            streaming: false,
            steps: Vec::new(),
            thinking: String::new(),
            user_reaction: String::new(),
            agent_reaction: String::new(),
            request_id: String::new(),
        }
    }
}

/// A known project folder, even if it has no sessions yet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Project {
    /// Filesystem path, or none for "No project".
    pub path: Option<String>,
    /// Collapsed in the tree.
    pub collapsed: bool,
}

/// Studio session list, the open chat, and the composer.
#[derive(Clone)]
pub struct Studio {
    /// Open sessions, top to bottom (server or preview order).
    pub sessions: Signal<Vec<Session>>,
    /// The selected session, if any.
    pub active: Signal<Option<String>>,
    /// Filter text for the session search box.
    pub query: Signal<String>,
    /// All / Pin / Archive.
    pub filter: Signal<Filter>,
    /// Session options disclosure.
    pub options_open: Signal<bool>,
    /// Sessions checked for a bulk move or delete.
    pub selected_ids: Signal<Vec<String>>,
    /// Last session clicked while selecting (Shift+click range).
    pub select_anchor: Signal<Option<String>>,
    /// When true, session rows show checkboxes.
    pub select_mode: Signal<bool>,
    /// Adding a project folder (path field visible).
    pub adding_project: Signal<bool>,
    /// Draft path for a new project folder.
    pub project_draft: Signal<String>,
    /// Known projects (plus implicit ones from sessions).
    pub projects: Signal<Vec<Project>>,
    /// Messages of the active session.
    pub messages: Signal<Vec<ChatMessage>>,
    /// In-flight assistant text, not yet a finished row (or the live tail).
    pub partial: Signal<String>,
    /// Tool steps for the turn that is streaming.
    pub live_steps: Signal<Vec<WorkStep>>,
    /// Thinking text for the turn that is streaming.
    pub live_thinking: Signal<String>,
    /// Byte offsets in [`Self::partial`] where a tool call started.
    pub live_breaks: Signal<Vec<usize>>,
    /// Real draft message id from the runtime's `event: draft`, when it has one.
    pub live_message_id: Signal<Option<String>>,
    /// Optimistic user reaction on the live draft, before the row is saved.
    pub live_user_reaction: Signal<String>,
    /// A turn is streaming.
    pub streaming: Signal<bool>,
    /// Chat / Plan / Build.
    pub mode: Signal<WorkMode>,
    /// Plan-mode plan list dock (side, width, collapsed).
    pub plan_dock: Signal<remedy_ui::widgets::DockState>,
    /// Connection to remedy-runtime.
    pub conn: Signal<Conn>,
    /// Active model id.
    pub model: Signal<String>,
    /// Active provider id.
    pub provider: Signal<String>,
    /// Models the picker can offer.
    pub models: Signal<Vec<String>>,
    /// Configured providers (and each one's models) for the status-bar picker.
    pub providers: Signal<Vec<crate::model_picker::ProviderChoice>>,
    /// Loading / ready / error for that picker.
    pub picker_status: Signal<crate::model_picker::PickerStatus>,
    /// Display name for "Ready when you are, {name}".
    pub user_name: Signal<String>,
    /// What the owner calls her (Settings, Partner name). One name for Grove and Studio.
    pub partner_name: Signal<String>,
    /// When true, this Studio does not talk to a runtime (goldens, tests).
    pub local_only: bool,
    /// Fixed "now" for relative times in previews (Unix seconds).
    pub clock: Signal<Option<i64>>,
    /// Time Travel rail. The shell mounts it; chrome calls [`crate::timetravel::TimeTravel::open`].
    pub time_travel: crate::timetravel::TimeTravel,
    next_id: Signal<u64>,
    next_msg: Signal<u64>,
    client: Signal<Option<Client>>,
    /// Session ids whose DELETE is in flight. List refreshes must not restore them.
    pending_deletes: Signal<Vec<String>>,
    /// Attachments, jobs, banners, order, and voice for this Studio.
    pub depth: crate::depth::Depth,
}

impl Studio {
    /// One empty chat, as a first launch looks.
    pub fn new() -> Self {
        Self::with(
            vec![Session::fresh("1", "New chat")],
            Some("1".into()),
            2,
            "",
            false,
        )
    }

    /// Three sessions and an empty well, matching the current-app capture.
    pub fn preview() -> Self {
        let s = Self::with(preview_sessions(), Some("s3".into()), 10, "Ahmi", true);
        s.clock.set(Some(preview_now()));
        s.conn.set(Conn::Connecting);
        s.model.set("remedy".into());
        s.provider.set("local".into());
        s.models.set(vec!["remedy".into()]);
        s
    }

    /// The same sessions with the recipe-importer turn open, for chat goldens.
    pub fn preview_chat() -> Self {
        let s = Self::with(preview_sessions(), Some("s1".into()), 10, "Ahmi", true);
        s.clock.set(Some(preview_now()));
        s.conn.set(Conn::Ready);
        s.model.set("remedy".into());
        s.provider.set("local".into());
        s.models.set(vec!["remedy".into()]);
        s.messages.set(preview_messages());
        s
    }

    /// Mid-turn chat: sealed speech already reacted to, only the last reply still live.
    pub fn preview_chat_live() -> Self {
        let s = Self::preview_chat();
        let user = preview_messages()
            .into_iter()
            .find(|m| m.role == "user")
            .expect("preview user turn");
        s.messages.set(vec![user]);
        let first = "I'll reach the folder through the shell.\n\n";
        let second = "Cmd is eating the search patterns...\n\n";
        let reply = "Patching the entry path and the";
        s.partial.set(format!("{first}{second}{reply}"));
        s.live_breaks
            .set(vec![first.len(), first.len() + second.len()]);
        let mut step = WorkStep::done("c1", "bash");
        step.status = crate::work::StepStatus::Running;
        step.narration = Some("I'll reach the folder through the shell.".into());
        step.detail = Some("Get-ChildItem".into());
        s.live_steps.set(vec![step]);
        s.live_thinking
            .set("Looking through the loader and the manifest.".into());
        s.live_message_id.set(Some("draft-1".into()));
        s.live_user_reaction.set("👍".into());
        s.streaming.set(true);
        s.depth.todos.set(vec![
            crate::depth::TodoItem {
                id: "t1".into(),
                content: "Patch the entry path".into(),
                status: "in_progress".into(),
            },
            crate::depth::TodoItem {
                id: "t2".into(),
                content: "Re-run the addon check".into(),
                status: "pending".into(),
            },
        ]);
        s
    }

    fn with(
        sessions: Vec<Session>,
        active: Option<String>,
        next_id: u64,
        user: &str,
        local_only: bool,
    ) -> Self {
        Studio {
            sessions: Signal::new(sessions),
            active: Signal::new(active),
            query: Signal::new(String::new()),
            filter: Signal::new(Filter::All),
            options_open: Signal::new(false),
            selected_ids: Signal::new(Vec::new()),
            select_anchor: Signal::new(None),
            select_mode: Signal::new(false),
            adding_project: Signal::new(false),
            project_draft: Signal::new(String::new()),
            projects: Signal::new(vec![Project {
                path: None,
                collapsed: false,
            }]),
            messages: Signal::new(Vec::new()),
            partial: Signal::new(String::new()),
            live_steps: Signal::new(Vec::new()),
            live_thinking: Signal::new(String::new()),
            live_breaks: Signal::new(Vec::new()),
            live_message_id: Signal::new(None),
            live_user_reaction: Signal::new(String::new()),
            streaming: Signal::new(false),
            mode: Signal::new(WorkMode::Build),
            plan_dock: Signal::new(crate::plan_dock::initial_state()),
            conn: Signal::new(Conn::Disconnected),
            model: Signal::new("remedy".into()),
            provider: Signal::new("local".into()),
            models: Signal::new(vec!["remedy".into()]),
            providers: Signal::new(Vec::new()),
            picker_status: Signal::new(crate::model_picker::PickerStatus::Ready),
            user_name: Signal::new(user.to_string()),
            partner_name: Signal::new("Remedy".into()),
            local_only,
            clock: Signal::new(None),
            time_travel: crate::timetravel::TimeTravel::new(),
            next_id: Signal::new(next_id),
            next_msg: Signal::new(1),
            client: Signal::new(None),
            pending_deletes: Signal::new(Vec::new()),
            depth: crate::depth::Depth::new(local_only),
        }
    }

    /// The next local message id.
    pub(crate) fn alloc_msg_id(&self) -> u64 {
        let id = self.next_msg.get();
        self.next_msg.set(id + 1);
        id
    }

    /// Unix seconds used for age lines.
    pub fn now(&self) -> i64 {
        self.clock.get().unwrap_or_else(time::now_secs)
    }

    /// First name for the empty-well greeting.
    pub fn greeting_name(&self) -> String {
        self.user_name
            .get()
            .split_whitespace()
            .next()
            .unwrap_or("there")
            .to_string()
    }

    /// Appends a new chat and selects it.
    pub fn add(&self) {
        if !self.local_only {
            if let Some(client) = self.client.get() {
                self.create_remote(client);
                return;
            }
        }
        let id = self.next_id.get();
        self.next_id.set(id + 1);
        let sid = id.to_string();
        let title = format!("New chat {id}");
        self.sessions.update(|list| {
            list.push(Session {
                id: sid.clone(),
                title,
                ..Session::fresh("", "")
            });
        });
        self.active.set(Some(sid.clone()));
        self.messages.set(Vec::new());
        crate::depth::focus_session(self, &sid);
    }

    fn create_remote(&self, client: Client) {
        if self.depth.creating_session.get() {
            return;
        }
        self.depth.creating_session.set(true);
        let studio = self.clone();
        let Ok(waker) = waker() else {
            self.depth.creating_session.set(false);
            return;
        };
        runtime::create_session(&client, move |out| {
            let _ = waker.post(move || match out {
                Ok(s) => {
                    studio.sessions.update(|list| {
                        if !list.iter().any(|x| x.id == s.id) {
                            list.insert(0, s.clone());
                        }
                    });
                    studio.active.set(Some(s.id.clone()));
                    studio.messages.set(Vec::new());
                    crate::depth::focus_session(&studio, &s.id);
                    studio.depth.creating_session.set(false);
                    crate::depth::on_session_created(&studio, &s.id);
                }
                Err(e) => {
                    studio.depth.creating_session.set(false);
                    studio.depth.held_for_session.set(None);
                    studio.depth.held_echo.set(true);
                    studio.conn.set(Conn::Error(e));
                }
            });
        });
    }

    /// Drops the session. If it is the open chat, another session is selected
    /// first (or a fresh one is created) and any in-flight turn is cancelled so
    /// the well is not torn down on the row being removed.
    pub fn close(&self, id: &str) {
        self.leave_then_drop(id, true);
    }

    /// Sidecar DELETE finished. A successful delete keeps the id in
    /// `pending_deletes` (a tombstone) so a stale in-flight GET /api/sessions
    /// cannot put the row back; a failure drops the tombstone and refreshes.
    pub(crate) fn on_delete_result(&self, id: &str, ok: bool) {
        if ok {
            return;
        }
        self.pending_deletes.update(|ids| ids.retain(|s| s != id));
        if let Some(c) = self.client.get() {
            self.refresh(&c);
        }
    }

    /// A `session_deleted` event from the sidecar (this surface or the Web UI).
    /// Tombstone the id immediately so a stale list refresh cannot restore it.
    pub fn forget_deleted(&self, id: &str) {
        if id.is_empty() {
            return;
        }
        self.leave_then_drop(id, false);
    }

    pub(crate) fn is_dropping(&self, id: &str) -> bool {
        self.pending_deletes.get().iter().any(|s| s == id)
    }

    fn neighbor_id(&self, except: &str) -> Option<String> {
        self.sessions
            .get()
            .into_iter()
            .find(|s| s.id != except)
            .map(|s| s.id)
    }

    /// Stops a live turn and drops jobs for `id` so later frames do not write
    /// into a session that is about to go away.
    fn abandon_session(&self, id: &str) {
        let running = self.depth.session_running(id)
            || (self.active.get().as_deref() == Some(id) && self.streaming.get());
        if running {
            if let (false, Some(client)) = (self.local_only, self.client.get()) {
                runtime::abort_session(&client, id, |_| {});
            }
            self.depth.finish_job(id, "aborted");
        }
        if self.active.get().as_deref() == Some(id) {
            self.streaming.set(false);
            self.clear_live();
            self.depth.todos.set(Vec::new());
            self.depth.progress.set(None);
            self.depth.remote_busy.set(false);
        }
    }

    fn switch_off(&self, except: &str) {
        if let Some(next) = self.neighbor_id(except) {
            self.select(&next);
            return;
        }
        self.add();
    }

    fn leave_then_drop(&self, id: &str, issue_delete: bool) {
        let id = id.to_string();
        if id.is_empty() {
            return;
        }
        let was_active = self.active.get().as_deref() == Some(id.as_str());
        if was_active {
            self.abandon_session(&id);
            self.switch_off(&id);
        }
        if !self.pending_deletes.get().iter().any(|s| s == &id) {
            self.pending_deletes.update(|ids| ids.push(id.clone()));
        }
        if issue_delete && !self.local_only {
            if let Some(client) = self.client.get() {
                let studio = self.clone();
                let dropped = id.clone();
                let wake = remedy_ui::reactive::waker().ok();
                runtime::delete_session(&client, &id, move |out| {
                    let Some(wake) = wake else { return };
                    let _ = wake.post(move || {
                        studio.on_delete_result(&dropped, out.is_ok());
                    });
                });
            }
        }
        self.sessions.update(|list| {
            if let Some(i) = list.iter().position(|s| s.id == id) {
                list.remove(i);
            }
        });
        self.selected_ids.update(|ids| ids.retain(|s| s != &id));
        if self.select_anchor.get().as_deref() == Some(id.as_str()) {
            self.select_anchor.set(None);
        }
        // Remote add() may still have this id selected until create returns.
        if self.active.get().as_deref() == Some(id.as_str()) {
            self.active.set(None);
            self.messages.set(Vec::new());
            self.clear_live();
            self.streaming.set(false);
        }
    }

    /// Selects `id` and loads its messages when connected.
    pub fn select(&self, id: &str) {
        if self.active.get().as_deref() == Some(id) {
            return;
        }
        self.active.set(Some(id.to_string()));
        self.messages.set(Vec::new());
        crate::depth::focus_session(self, id);
        self.load_messages(id);
    }

    pub(crate) fn load_messages(&self, id: &str) {
        if self.local_only {
            return;
        }
        let Some(client) = self.client.get() else {
            return;
        };
        let studio = self.clone();
        let sid = id.to_string();
        let Ok(waker) = waker() else {
            return;
        };
        let sid_ref = sid.clone();
        runtime::list_messages(&client, &sid_ref, move |out| {
            let _ = waker.post(move || {
                if studio.active.get().as_deref() == Some(sid.as_str()) {
                    if let Ok(msgs) = out {
                        let local = studio.messages.get();
                        studio.messages.set(merge_reloaded_messages(local, msgs));
                    }
                }
            });
        });
    }

    /// Pins or unpins `id`.
    pub fn toggle_pin(&self, id: &str) {
        let id = id.to_string();
        self.sessions.update(|list| {
            if let Some(s) = list.iter_mut().find(|s| s.id == id) {
                s.pinned = !s.pinned;
            }
        });
        self.push_filing(&id);
    }

    /// Archives or un-archives `id`.
    pub fn toggle_archive(&self, id: &str) {
        let id = id.to_string();
        self.sessions.update(|list| {
            if let Some(s) = list.iter_mut().find(|s| s.id == id) {
                s.archived = !s.archived;
            }
        });
        self.push_filing(&id);
    }

    /// Sends how `id` is filed (pinned, archived, tags) to the runtime, which
    /// keeps it for every window: the web page and a phone show the same.
    fn push_filing(&self, id: &str) {
        let Some(client) = self.runtime() else { return };
        let Some(s) = self.sessions.get().into_iter().find(|s| s.id == id) else {
            return;
        };
        let body = serde_json::json!({ "pinned": s.pinned, "archived": s.archived, "tags": s.tags });
        let (http, path) = (client.http(), format!("sessions/{id}"));
        let _ = std::thread::Builder::new().name("remedy-filing".into()).spawn(move || {
            let cancel = remedy_ui::reactive::Cancel::new();
            let _ = http.send_blocking(remedy_ui::net::Request::patch(&path).json(&body), &cancel);
        });
    }

    /// Records a project folder (creates the group if needed) and optionally assigns the
    /// active session to it.
    pub fn add_project(&self, path: String, assign_active: bool) {
        let path = path.trim().to_string();
        if path.is_empty() {
            return;
        }
        self.projects.update(|list| {
            if !list
                .iter()
                .any(|p| p.path.as_deref() == Some(path.as_str()))
            {
                list.push(Project {
                    path: Some(path.clone()),
                    collapsed: false,
                });
            }
        });
        if assign_active {
            if let Some(id) = self.active.get() {
                self.set_project(&id, Some(path));
            }
        }
        self.adding_project.set(false);
        self.project_draft.set(String::new());
    }

    /// Moves `id` to `project_path` (None = no project).
    pub fn set_project(&self, id: &str, project_path: Option<String>) {
        let id = id.to_string();
        self.sessions.update(|list| {
            if let Some(s) = list.iter_mut().find(|s| s.id == id) {
                s.project_path = project_path;
            }
        });
    }

    /// Replace the session list. How a session is filed comes from the runtime.
    /// Tags an older build kept only on this PC are sent up once, for sessions
    /// the runtime has no tags for, and the local file is then emptied.
    pub fn apply_sessions(&self, mut list: Vec<Session>) {
        let stored = if self.depth.save_enabled {
            crate::depth::load_session_tags()
        } else {
            Vec::new()
        };
        let mut adopted = Vec::new();
        for session in &mut list {
            if !session.tags.is_empty() {
                continue;
            }
            if let Some((_, tags)) = stored.iter().find(|(id, _)| id == &session.id) {
                session.tags = crate::depth::logic::normalize_tags(tags.iter());
                if !session.tags.is_empty() {
                    adopted.push(session.id.clone());
                }
            }
        }
        // Once: what the 0.70.x web shell kept in its own storage (pins, tags,
        // archive flags), for sessions the runtime has nothing filed for.
        let offer_old = self.depth.save_enabled
            && self.runtime().is_some()
            && !crate::depth::webview_filing_imported();
        if offer_old {
            for old in crate::theme_import::filing_from_webview() {
                let Some(session) = list.iter_mut().find(|s| s.id == old.id) else { continue };
                if session.pinned || session.archived || !session.tags.is_empty() {
                    continue;
                }
                session.pinned = old.pinned;
                session.archived = old.archived;
                session.tags = crate::depth::logic::normalize_tags(old.tags.iter());
                adopted.push(session.id.clone());
            }
        }
        self.sessions.set(list);
        if self.runtime().is_some() {
            for id in &adopted {
                self.push_filing(id);
            }
            if !stored.is_empty() {
                crate::depth::save_session_tags(&[]);
            }
            if offer_old {
                crate::depth::mark_webview_filing_imported();
            }
        }
    }

    /// Apply a sidecar session list and drop any active id the sidecar does not have.
    pub fn apply_live_sessions(&self, list: Vec<Session>) {
        let pending = self.pending_deletes.get();
        let list: Vec<Session> = list
            .into_iter()
            .filter(|s| !pending.iter().any(|id| id == &s.id))
            .collect();
        let ids: Vec<String> = list.iter().map(|s| s.id.clone()).collect();
        let decision = crate::depth::logic::adopt_active_session(self.active.get().as_deref(), &ids);
        self.apply_sessions(list);
        match decision {
            crate::depth::logic::SessionAdopt::Keep(id) => {
                self.active.set(Some(id.clone()));
                self.load_messages(&id);
            }
            crate::depth::logic::SessionAdopt::Select(id) => {
                self.active.set(Some(id.clone()));
                self.messages.set(Vec::new());
                self.clear_live();
                crate::depth::focus_session(self, &id);
                self.load_messages(&id);
            }
            crate::depth::logic::SessionAdopt::Create => {
                self.active.set(None);
                self.messages.set(Vec::new());
                self.clear_live();
                if self.runtime().is_some() && !self.depth.creating_session.get() {
                    self.add();
                }
            }
        }
    }

    /// Adds a tag to `id`, persists it, and closes the add-tag field.
    pub fn add_tag(&self, id: &str, tag: &str) {
        self.sessions.update(|list| {
            if let Some(session) = list.iter_mut().find(|s| s.id == id) {
                session.tags = crate::depth::logic::add_session_tag(&session.tags, tag);
            }
        });
        self.depth.tag_id.set(None);
        self.push_filing(id);
    }

    /// Collapses or expands a project group.
    pub fn toggle_collapsed(&self, path: Option<&str>) {
        let key = path.map(|s| s.to_string());
        self.projects.update(|list| {
            if let Some(p) = list.iter_mut().find(|p| p.path == key) {
                p.collapsed = !p.collapsed;
            } else {
                list.push(Project {
                    path: key,
                    collapsed: true,
                });
            }
        });
    }

    /// Sets Chat, Plan, or Build.
    pub fn set_mode(&self, mode: WorkMode) {
        self.mode.set(mode);
    }

    /// Cycles Chat → Plan → Build.
    pub fn cycle_mode(&self) {
        self.set_mode(self.mode.get().next());
    }

    /// Sends `text` from the composer or a starter. Slash commands, a busy turn,
    /// and attachments are handled by [`crate::depth::submit`].
    pub fn send(&self, text: String) {
        crate::depth::submit(self, text, Vec::new(), crate::depth::SubmitKind::Send, true);
    }

    /// Drops the in-flight tail so a new session does not keep the previous turn.
    pub(crate) fn clear_live(&self) {
        self.partial.set(String::new());
        self.live_steps.set(Vec::new());
        self.live_thinking.set(String::new());
        self.live_breaks.set(Vec::new());
        self.live_message_id.set(None);
        self.live_user_reaction.set(String::new());
    }

    /// Applies one SSE frame to the session it belongs to.
    pub(crate) fn on_frame_for(&self, sid: &str, kind: &str, data: &str) {
        crate::depth::apply_frame(self, sid, kind, data);
    }

    pub(crate) fn finish_assistant(&self, error: Option<String>) {
        let partial = self.partial.get();
        let breaks = self.live_breaks.get();
        let mut steps = self.live_steps.get();
        let thinking = self.live_thinking.get();
        let id = self
            .live_message_id
            .get()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("local-a-{}", self.alloc_msg_id()));
        let reaction = self.live_user_reaction.get();
        self.clear_live();
        self.streaming.set(false);
        let mut body = work::reply_after(&partial, &breaks);
        work::settle(&mut steps, error.is_some());
        if let Some(ref err) = error {
            if body.is_empty() {
                body = err.clone();
            } else {
                body.push_str("\n\n");
                body.push_str(err);
            }
        }
        if body.is_empty() && steps.is_empty() && thinking.trim().is_empty() {
            crate::depth::after_finish(self);
            return;
        }
        let role = if error.is_some() && body.starts_with("The mind") {
            "system"
        } else {
            "assistant"
        };
        let spoken = body.clone();
        let speak = role == "assistant";
        let request_id = self
            .active
            .get()
            .and_then(|sid| self.depth.job(&sid).map(|j| j.request_id))
            .unwrap_or_default();
        let message = ChatMessage {
            role: role.into(),
            steps,
            thinking,
            user_reaction: reaction,
            request_id,
            ..ChatMessage::plain(id.clone(), "assistant", body)
        };
        let existed = self.messages.get().iter().any(|row| row.id == id);
        self.messages.update(move |m| {
            if let Some(existing) = m.iter_mut().find(|row| row.id == id) {
                *existing = message.clone();
            } else {
                m.push(message);
            }
        });
        if !existed {
            if let Some(sid) = self.active.get() {
                self.sessions.update(|list| {
                    if let Some(s) = list.iter_mut().find(|s| s.id == sid) {
                        s.message_count = s.message_count.saturating_add(1);
                    }
                });
            }
        }
        if speak {
            crate::depth::speak_reply(self, &spoken);
        }
        crate::depth::after_finish(self);
    }

    /// Stops a live turn (local flag; server abort when connected).
    pub fn stop(&self) {
        if !self.streaming.get() {
            return;
        }
        if let (false, Some(client), Some(sid)) =
            (self.local_only, self.client.get(), self.active.get())
        {
            runtime::abort_session(&client, &sid, |_| {});
        }
        self.finish_assistant(None);
    }

    /// The runtime client, when this Studio is attached.
    pub fn client(&self) -> Option<Client> {
        self.client.get()
    }

    /// Reloads the session list and the open chat after a Time Travel restore.
    pub(crate) fn reload_open(&self) {
        if self.local_only {
            return;
        }
        let Some(client) = self.client.get() else {
            return;
        };
        self.refresh(&client);
    }

    /// The runtime client, when this Studio is allowed to talk to one.
    pub(crate) fn runtime(&self) -> Option<Client> {
        if self.local_only {
            None
        } else {
            self.client.get()
        }
    }

    /// Hooks a runtime client and loads sessions (no-op for local previews).
    pub fn attach_client(&self, client: Client) {
        self.client.set(Some(client.clone()));
        self.conn.set(Conn::Connecting);
        self.picker_status.set(crate::model_picker::PickerStatus::Loading);
        let studio = self.clone();
        let Ok(waker) = waker() else {
            return;
        };
        let client2 = client.clone();
        runtime::probe(&client, move |ok, err| {
            let studio = studio.clone();
            let client = client2.clone();
            let _ = waker.post(move || {
                if ok {
                    studio.conn.set(Conn::Ready);
                    studio.refresh(&client);
                    if !runtime::spawn_disabled() {
                        crate::depth::boot(&studio);
                    }
                } else {
                    studio
                        .conn
                        .set(Conn::Error(err.unwrap_or_else(|| "not connected".into())));
                }
            });
        });
    }

    fn refresh(&self, client: &Client) {
        let studio = self.clone();
        let Ok(wake) = waker() else {
            return;
        };
        let wake_sessions = wake.clone();
        runtime::list_sessions(client, move |out| {
            let studio = studio.clone();
            let _ = wake_sessions.post(move || {
                if let Ok(list) = out {
                    studio.apply_live_sessions(list);
                }
            });
        });
        crate::model_picker::refresh(self, client);
    }
}

/// Fixed "now" so preview age lines do not flicker across golden runs (2026-11-22T21:00:00Z).
pub fn preview_now() -> i64 {
    time::parse_rfc3339("2026-11-22T21:00:00Z").unwrap_or(1_795_381_200)
}

fn preview_sessions() -> Vec<Session> {
    vec![
        Session {
            updated_at: Some("2026-11-22T20:00:00Z".into()),
            ..Session::fresh("s3", "New chat")
        },
        Session {
            message_count: 2,
            updated_at: Some("2026-11-22T20:00:00Z".into()),
            ..Session::fresh("s1", "Garden planner")
        },
        Session {
            message_count: 2,
            updated_at: Some("2026-11-22T09:15:00Z".into()),
            ..Session::fresh("s2", "House rules")
        },
    ]
}

fn preview_messages() -> Vec<ChatMessage> {
    vec![
        ChatMessage {
            created_at: Some("2026-11-22T15:00:00Z".into()),
            ..ChatMessage::plain(
                "m1",
                "user",
                "The recipe importer stopped loading after the last update. Can you fix it?",
            )
        },
        ChatMessage {
            created_at: Some("2026-11-22T15:00:20Z".into()),
            thinking: "The manifest still named init.lua, but the last update renamed the entry. I'll patch the three files and run the addon check.".into(),
            steps: vec![
                {
                    let mut s = WorkStep::done("c1", "file_read");
                    s.detail = Some("manifest.json".into());
                    s
                },
                {
                    let mut s = WorkStep::failed("c2", "bash", "exit 1");
                    s.detail = Some("recipes.exe --check --all".into());
                    s
                },
                {
                    let mut s = WorkStep::done("c3", "file_write");
                    s.detail = Some("loader.lua".into());
                    s
                },
            ],
            ..ChatMessage::plain(
                "m2",
                "assistant",
                "The recipe importer loads again.\n\n\
What was wrong: the last update renamed `init.lua` to `init_main.lua`, but \
`manifest.json` and `loader.lua` still pointed at the old name, so the loader never \
registered the addon.\n\n\
What I changed:\n\
`manifest.json`: entry now `init_main.lua`; minimum version guard relaxed to 2.4.\n\
`loader.lua`: registers the entry before the version check.\n\
`config.json`: removed the stale `init.lua` override.\n\n\
`recipes.exe --check --all` reports all 6 addons OK. Restart the app to pick it up.",
            )
        },
    ]
}

/// Sessions grouped for the project tree (pinned first, then each project).
pub fn grouped(
    sessions: &[Session],
    projects: &[Project],
    filter: Filter,
    query: &str,
) -> Vec<(Project, Vec<Session>)> {
    let q = query.trim().to_ascii_lowercase();
    let visible: Vec<Session> = sessions
        .iter()
        .filter(|s| match filter {
            Filter::All => !s.archived,
            Filter::Pinned => s.pinned && !s.archived,
            Filter::Archived => s.archived,
        })
        .filter(|s| crate::depth::logic::session_matches_query(s, &q))
        .cloned()
        .collect();

    let mut paths: Vec<Option<String>> = projects.iter().map(|p| p.path.clone()).collect();
    for s in &visible {
        if !paths
            .iter()
            .any(|p| p.as_deref() == s.project_path.as_deref())
        {
            paths.push(s.project_path.clone());
        }
    }
    if !paths.iter().any(|p| p.is_none()) {
        paths.insert(0, None);
    }

    let mut out = Vec::new();
    for path in paths {
        let collapsed = projects
            .iter()
            .find(|p| p.path == path)
            .map(|p| p.collapsed)
            .unwrap_or(false);
        let members: Vec<Session> = visible
            .iter()
            .filter(|s| s.project_path == path)
            .cloned()
            .collect();
        if members.is_empty() && path.is_some() && !projects.iter().any(|p| p.path == path) {
            continue;
        }
        out.push((Project { path, collapsed }, members));
    }
    out
}

/// Count of visible sessions in a group, for the header.
pub fn project_label(path: Option<&str>) -> String {
    match path {
        Some(p) => {
            let name = std::path::Path::new(p)
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or(p);
            name.to_string()
        }
        None => String::new(), // filled from i18n at the call site
    }
}


/// Keep locally finished assistant text when a list refresh comes back empty
/// (the turn worked, but the bubble would otherwise show only the Remedy header).
fn merge_reloaded_messages(local: Vec<ChatMessage>, server: Vec<ChatMessage>) -> Vec<ChatMessage> {
    server
        .into_iter()
        .map(|mut m| {
            if let Some(old) = local.iter().find(|o| o.id == m.id) {
                if m.content.trim().is_empty() && !old.content.trim().is_empty() {
                    m.content = old.content.clone();
                }
                if m.thinking.trim().is_empty() && !old.thinking.trim().is_empty() {
                    m.thinking = old.thinking.clone();
                }
                if m.steps.is_empty() && !old.steps.is_empty() {
                    m.steps = old.steps.clone();
                }
            }
            m
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_tag_updates_the_session_and_closes_the_field() {
        let studio = Studio::preview();
        studio.depth.tag_id.set(Some("s3".into()));
        studio.add_tag("s3", "  recipes ");
        let tags = studio
            .sessions
            .get()
            .into_iter()
            .find(|s| s.id == "s3")
            .map(|s| s.tags)
            .unwrap_or_default();
        assert_eq!(tags, vec!["recipes".to_string()]);
        assert!(studio.depth.tag_id.get().is_none());
        studio.add_tag("s3", "recipes");
        let tags = studio
            .sessions
            .get()
            .into_iter()
            .find(|s| s.id == "s3")
            .map(|s| s.tags)
            .unwrap_or_default();
        assert_eq!(tags, vec!["recipes".to_string()]);
    }

    #[test]
    fn merge_reloaded_messages_keeps_local_assistant_text() {
        let local = vec![ChatMessage::plain("a1", "assistant", "The importer loads.")];
        let server = vec![ChatMessage::plain("a1", "assistant", "")];
        let out = merge_reloaded_messages(local, server);
        assert_eq!(out[0].content, "The importer loads.");
    }

    #[test]
    fn apply_live_sessions_does_not_restore_a_pending_delete() {
        let studio = Studio::preview();
        let id = studio.sessions.get()[0].id.clone();
        studio.pending_deletes.set(vec![id.clone()]);
        studio.apply_live_sessions(vec![
            Session::fresh(&id, "Gone?"),
            Session::fresh("keep", "Keep me"),
        ]);
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&id), "pending delete came back: {ids:?}");
        assert!(ids.contains(&"keep".to_string()));
    }

    #[test]
    fn close_drops_the_row_and_tombs_the_id() {
        let studio = Studio::preview();
        let id = studio.sessions.get()[0].id.clone();
        studio.close(&id);
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&id));
        assert!(
            studio.pending_deletes.get().iter().any(|s| s == &id),
            "X / Delete must tombstone so a stale list cannot restore the row"
        );
        studio.on_delete_result(&id, true);
        assert!(
            studio.pending_deletes.get().iter().any(|s| s == &id),
            "a successful DELETE must keep the tombstone"
        );
        studio.apply_live_sessions(vec![
            Session::fresh(&id, "Stale snapshot"),
            Session::fresh("keep", "Keep me"),
        ]);
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&id), "stale GET after DELETE restored the row");
    }

    #[test]
    fn failed_delete_drops_the_tombstone() {
        let studio = Studio::preview();
        let id = studio.sessions.get()[0].id.clone();
        studio.close(&id);
        studio.on_delete_result(&id, false);
        assert!(
            !studio.pending_deletes.get().iter().any(|s| s == &id),
            "a failed DELETE must allow the next refresh to restore the row"
        );
    }

    #[test]
    fn forget_deleted_tombs_a_row_removed_on_the_other_surface() {
        let studio = Studio::preview();
        let id = studio.sessions.get()[0].id.clone();
        studio.forget_deleted(&id);
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&id));
        studio.apply_live_sessions(vec![Session::fresh(&id, "Stale"), Session::fresh("keep", "Keep")]);
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&id));
    }

    #[test]
    fn close_active_session_switches_to_a_neighbor_first() {
        let studio = Studio::preview();
        assert_eq!(studio.active.get().as_deref(), Some("s3"));
        studio.messages.set(vec![ChatMessage::plain("m", "user", "about to go")]);
        studio.close("s3");
        assert_ne!(studio.active.get().as_deref(), Some("s3"));
        assert!(studio.active.get().is_some(), "a neighbor must be selected");
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        assert!(!ids.contains(&"s3".to_string()));
        assert!(ids.contains(&studio.active.get().unwrap()));
        assert!(
            !studio.messages.get().iter().any(|m| m.content == "about to go"),
            "the deleted session's messages must not stay in the well"
        );
    }

    #[test]
    fn close_last_session_opens_a_fresh_chat() {
        let studio = Studio::preview();
        let ids: Vec<String> = studio.sessions.get().into_iter().map(|s| s.id).collect();
        for id in ids {
            studio.close(&id);
        }
        let left = studio.sessions.get();
        assert!(
            !left.is_empty(),
            "deleting the last session must leave a fresh chat"
        );
        assert!(studio.active.get().is_some());
        assert!(!studio.is_dropping(&studio.active.get().unwrap()));
    }

    #[test]
    fn close_active_session_cancels_a_running_turn() {
        let studio = Studio::preview();
        studio.streaming.set(true);
        studio.partial.set("halfway".into());
        studio.depth.map_job("s3", |job| job.status = "running".into());
        studio.close("s3");
        assert!(!studio.streaming.get());
        assert!(studio.partial.get().is_empty());
        let job = studio.depth.job("s3");
        assert!(
            job.as_ref().is_none_or(|j| j.status != "running"),
            "the deleted session's job must not stay running: {job:?}"
        );
    }
}
