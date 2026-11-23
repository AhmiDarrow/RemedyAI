//! Studio state: sessions, the open chat, composer, and connection.

use crate::runtime::{self, Client};
use crate::time;
use crate::work::{self, WorkStep};
use remedy_ui::reactive::{Signal, waker};

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
            WorkMode::Chat => "CHAT",
            WorkMode::Plan => "PLAN",
            WorkMode::Build => "BUILD",
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
    /// A turn is streaming.
    pub streaming: Signal<bool>,
    /// Chat / Plan / Build.
    pub mode: Signal<WorkMode>,
    /// Connection to remedy-runtime.
    pub conn: Signal<Conn>,
    /// Active model id.
    pub model: Signal<String>,
    /// Active provider id.
    pub provider: Signal<String>,
    /// Models the picker can offer.
    pub models: Signal<Vec<String>>,
    /// Display name for "Ready when you are, {name}".
    pub user_name: Signal<String>,
    /// When true, this Studio does not talk to a runtime (goldens, tests).
    pub local_only: bool,
    /// Fixed "now" for relative times in previews (Unix seconds).
    pub clock: Signal<Option<i64>>,
    /// Time Travel rail. The shell mounts it; chrome calls [`crate::timetravel::TimeTravel::open`].
    pub time_travel: crate::timetravel::TimeTravel,
    next_id: Signal<u64>,
    next_msg: Signal<u64>,
    client: Signal<Option<Client>>,
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
            "Joe",
            false,
        )
    }

    /// Three sessions and an empty well, matching the current-app capture.
    pub fn preview() -> Self {
        let s = Self::with(preview_sessions(), Some("s3".into()), 10, "Joe", true);
        s.clock.set(Some(preview_now()));
        s.conn.set(Conn::Connecting);
        s.model.set("remedy".into());
        s.provider.set("local".into());
        s.models.set(vec!["remedy".into()]);
        s
    }

    /// The same sessions with the portal-addon turn open, for chat goldens.
    pub fn preview_chat() -> Self {
        let s = Self::with(preview_sessions(), Some("s1".into()), 10, "Joe", true);
        s.clock.set(Some(preview_now()));
        s.conn.set(Conn::Ready);
        s.model.set("remedy".into());
        s.provider.set("local".into());
        s.models.set(vec!["remedy".into()]);
        s.messages.set(preview_messages());
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
            streaming: Signal::new(false),
            mode: Signal::new(WorkMode::Build),
            conn: Signal::new(Conn::Disconnected),
            model: Signal::new("remedy".into()),
            provider: Signal::new("local".into()),
            models: Signal::new(vec!["remedy".into()]),
            user_name: Signal::new(user.to_string()),
            local_only,
            clock: Signal::new(None),
            time_travel: crate::timetravel::TimeTravel::new(),
            next_id: Signal::new(next_id),
            next_msg: Signal::new(1),
            client: Signal::new(None),
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
        let studio = self.clone();
        let Ok(waker) = waker() else {
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
                }
                Err(e) => studio.conn.set(Conn::Error(e)),
            });
        });
    }

    /// Drops the session; if it was selected, a neighbour is selected.
    pub fn close(&self, id: &str) {
        let id = id.to_string();
        if !self.local_only {
            if let Some(client) = self.client.get() {
                runtime::delete_session(&client, &id, |_| {});
            }
        }
        self.sessions.update(|list| {
            if let Some(i) = list.iter().position(|s| s.id == id) {
                list.remove(i);
            }
        });
        if self.active.get().as_deref() == Some(id.as_str()) {
            let list = self.sessions.get();
            self.active.set(list.first().map(|s| s.id.clone()));
            self.messages.set(Vec::new());
            self.clear_live();
            if let Some(next) = self.active.get() {
                self.load_messages(&next);
            }
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
                        studio.messages.set(msgs);
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
    }

    /// Archives or un-archives `id`.
    pub fn toggle_archive(&self, id: &str) {
        let id = id.to_string();
        self.sessions.update(|list| {
            if let Some(s) = list.iter_mut().find(|s| s.id == id) {
                s.archived = !s.archived;
            }
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

    /// Cycles Chat → Plan → Build.
    pub fn cycle_mode(&self) {
        self.mode.update(|m| *m = m.next());
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
        let mid = self.alloc_msg_id();
        let role = if error.is_some() && body.starts_with("The mind") {
            "system"
        } else {
            "assistant"
        };
        let spoken = body.clone();
        let speak = role == "assistant";
        let message = ChatMessage {
            role: role.into(),
            steps,
            thinking,
            ..ChatMessage::plain(format!("local-a-{mid}"), "assistant", body)
        };
        self.messages.update(move |m| m.push(message));
        if let Some(sid) = self.active.get() {
            self.sessions.update(|list| {
                if let Some(s) = list.iter_mut().find(|s| s.id == sid) {
                    s.message_count = s.message_count.saturating_add(1);
                }
            });
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
        if self.local_only { None } else { self.client.get() }
    }

    /// Hooks a runtime client and loads sessions (no-op for local previews).
    pub fn attach_client(&self, client: Client) {
        self.client.set(Some(client.clone()));
        self.conn.set(Conn::Connecting);
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
                    let keep = if studio.active.get().is_some() {
                        studio.active.get()
                    } else {
                        list.first().map(|s| s.id.clone())
                    };
                    studio.sessions.set(list);
                    if let Some(id) = keep {
                        if studio.sessions.get().iter().any(|s| s.id == id) {
                            studio.active.set(Some(id.clone()));
                            studio.load_messages(&id);
                        }
                    }
                }
            });
        });
        let studio = self.clone();
        runtime::list_models(client, move |out| {
            let _ = wake.post(move || {
                if let Ok(models) = out {
                    if !models.is_empty() {
                        studio.models.set(models);
                    }
                }
            });
        });
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
            ..Session::fresh("s1", "Port to RemedyUI")
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
                "The portal addon stopped loading after the last update. Can you fix it?",
            )
        },
        ChatMessage {
            created_at: Some("2026-11-22T15:00:20Z".into()),
            steps: vec![
                WorkStep::done("c1", "file_read"),
                WorkStep::failed("c2", "bash", "exit 1"),
                WorkStep::done("c3", "file_write"),
            ],
            ..ChatMessage::plain(
                "m2",
                "assistant",
                "The portal addon loads again.\n\n\
What was wrong: the last update renamed `init.lua` to `init_main.lua`, but \
`manifest.json` and `loader.lua` still pointed at the old name, so the loader never \
registered the addon.\n\n\
What I changed:\n\
`manifest.json`: entry now `init_main.lua`; minimum version guard relaxed to 2.4.\n\
`loader.lua`: registers the entry before the version check.\n\
`config.json`: removed the stale `init.lua` override.\n\n\
`portal.exe --addon-check --all` reports all 6 addons OK. Restart the portal to pick it up.",
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
        .filter(|s| {
            q.is_empty()
                || s.title.to_ascii_lowercase().contains(&q)
                || s.project_path
                    .as_deref()
                    .map(|p| p.to_ascii_lowercase().contains(&q))
                    .unwrap_or(false)
        })
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
