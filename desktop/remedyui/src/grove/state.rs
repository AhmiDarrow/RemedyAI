//! Grove's state: the life board, the head, the open room, and the talk.
//!
//! Signals stay on the UI thread. Network work runs on a worker and comes
//! back through a waker. Studio keeps its own session list; Grove never
//! writes a `grove` session into it.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use remedy_ui::playback::{Microphone, MicrophoneOptions, MicrophoneState};
use remedy_ui::reactive::{waker, Signal};
use serde_json::{json, Value};

use crate::runtime::Client;
use crate::studio::{Studio, WorkMode};

use super::api::{self, Approval, Attachment, HeadOverview, LifeBoard, LifeGoal, LifeTask};
use super::logic::{self, BubblePos, StoryMessage, TodoRow, ToolCall};
use super::store::{self, Bookmark, Prefs};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Surface {
    Grove,
    Studio,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum HomeTab {
    Plots,
    Alongside,
    Storyline,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RoomTab {
    Alongside,
    Storyline,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Place {
    Home,
    Goal(String),
    Child(String),
}

#[derive(Clone, Debug)]
enum Gate {
    Idle,
    Busy(Arc<Mutex<Option<Result<String, String>>>>),
}

struct Slot {
    gate: Mutex<Gate>,
    cv: Condvar,
}

impl Slot {
    fn new() -> Self {
        Slot {
            gate: Mutex::new(Gate::Idle),
            cv: Condvar::new(),
        }
    }
}

/// In-flight session creates, so a double send cannot open two rooms.
struct Book {
    slots: Mutex<BTreeMap<String, Arc<Slot>>>,
}

impl Book {
    fn new() -> Self {
        Book {
            slots: Mutex::new(BTreeMap::new()),
        }
    }

    fn slot(&self, key: &str) -> Arc<Slot> {
        let mut map = self.slots.lock().unwrap_or_else(|e| e.into_inner());
        map.entry(key.to_string())
            .or_insert_with(|| Arc::new(Slot::new()))
            .clone()
    }
}

fn once(slot: &Slot, make: impl FnOnce() -> Result<String, String>) -> Result<String, String> {
    let mut gate = slot.gate.lock().unwrap_or_else(|e| e.into_inner());
    let (leader, shared) = match &*gate {
        Gate::Busy(shared) => (false, shared.clone()),
        Gate::Idle => {
            let shared = Arc::new(Mutex::new(None));
            *gate = Gate::Busy(shared.clone());
            (true, shared)
        }
    };
    drop(gate);
    if leader {
        let made = make();
        {
            let mut slot_gate = slot.gate.lock().unwrap_or_else(|e| e.into_inner());
            if let Ok(mut cell) = shared.lock() {
                *cell = Some(made.clone());
            }
            *slot_gate = Gate::Idle;
            slot.cv.notify_all();
        }
        made
    } else {
        let mut gate = slot.gate.lock().unwrap_or_else(|e| e.into_inner());
        loop {
            if let Ok(cell) = shared.lock() {
                if let Some(result) = cell.clone() {
                    return result;
                }
            }
            gate = slot.cv.wait(gate).unwrap_or_else(|e| e.into_inner());
        }
    }
}

#[derive(Clone)]
struct SendJob {
    text: String,
    attachments: Vec<Attachment>,
    steer: bool,
    home: bool,
    head_session: String,
    stored_home: String,
    goal_id: String,
    goal_title: String,
    goal_linked: String,
    goal_stored: String,
}

struct PollQuery {
    session: String,
    child_session: String,
    fast: bool,
}

/// A Studio session Remedy started from a Grove request.
#[derive(Clone, Debug)]
pub(crate) struct StudioHandoff {
    pub kind: logic::StudioKind,
    pub prompt: String,
    /// The Studio session doing the work. `None` until a new session exists.
    pub session: Option<String>,
}

/// The partner surface. Clone is a handle; the signals are shared.
#[derive(Clone)]
pub(crate) struct Grove {
    pub studio: Studio,
    pub surface: Signal<Surface>,
    pub place: Signal<Place>,
    pub home_tab: Signal<HomeTab>,
    pub room_tab: Signal<RoomTab>,
    pub board: Signal<Option<LifeBoard>>,
    pub goals: Signal<Vec<LifeGoal>>,
    pub head: Signal<Option<HeadOverview>>,
    pub children: Signal<Vec<api::HeadChild>>,
    pub routines: Signal<Vec<api::HeadRoutine>>,
    pub activity: Signal<Vec<api::Activity>>,
    pub approvals: Signal<Vec<Approval>>,
    pub life_task: Signal<Option<LifeTask>>,
    pub tucked: Signal<bool>,
    pub seen: Signal<Vec<String>>,
    pub explain_id: Signal<Option<String>>,
    pub flash: Signal<Option<(String, bool)>>,
    pub decision_busy: Signal<bool>,
    pub messages: Signal<Vec<StoryMessage>>,
    pub partial: Signal<String>,
    pub streaming: Signal<bool>,
    pub loading: Signal<bool>,
    pub attachments: Signal<Vec<Attachment>>,
    pub uploading: Signal<bool>,
    pub attach_error: Signal<String>,
    pub plant_error: Signal<String>,
    pub mic_error: Signal<String>,
    pub mic_on: Signal<bool>,
    pub composer_value: Signal<String>,
    pub session_id: Signal<String>,
    pub child_messages: Signal<Vec<StoryMessage>>,
    pub child_loading: Signal<bool>,
    pub home_split: Signal<f32>,
    pub room_split: Signal<f32>,
    pub clock_hour: Signal<Option<u32>>,
    pub note: Signal<String>,
    pub err: Signal<String>,
    pub settings_open: Signal<bool>,
    pub job_open: Signal<bool>,
    pub routine_open: Signal<bool>,
    pub show_all: Signal<bool>,
    pub bubble_from: Signal<Option<String>>,
    pub bubble_pos: Signal<BubblePos>,
    pub stage_url: Signal<String>,
    pub stage_draft: Signal<String>,
    pub stage_status: Signal<String>,
    pub bookmarks: Signal<Vec<Bookmark>>,
    pub bookmarks_open: Signal<bool>,
    pub shield: Signal<bool>,
    pub desktop_site: Signal<bool>,
    pub partner_name: Signal<String>,
    /// `female`, `male`, or `neutral`. Grove's talk-bar hints follow it.
    pub agent_gender: Signal<String>,
    pub speak_replies: Signal<bool>,
    pub todos: Signal<Vec<TodoRow>>,
    pub process: Signal<Vec<String>>,
    pub focus_nonce: Signal<u64>,
    pub prefill: Signal<Option<String>>,
    /// Last Studio session Remedy opened from a Grove request.
    pub handoff: Signal<Option<StudioHandoff>>,
    pub hide_window: Signal<u64>,
    pub busy_ui: Signal<bool>,
    pub draft_empty: Signal<bool>,
    pub local_only: bool,
    persist: bool,
    samples: Arc<Mutex<Vec<f32>>>,
    client: Signal<Option<Client>>,
    home_session: Signal<String>,
    goal_sessions: Signal<BTreeMap<String, String>>,
    live_tools: Signal<Vec<ToolCall>>,
    next_msg: Signal<u64>,
    book: Arc<Book>,
    poll_started: Arc<std::sync::atomic::AtomicBool>,
    busy: Arc<std::sync::atomic::AtomicBool>,
    migrated: Arc<std::sync::atomic::AtomicBool>,
    seen_armed: Arc<std::sync::atomic::AtomicBool>,
    poll_query: Arc<Mutex<PollQuery>>,
}


thread_local! {
    static MIC: RefCell<Option<Microphone>> = const { RefCell::new(None) };
}

fn take_mic() -> Option<Microphone> {
    MIC.with(|slot| slot.borrow_mut().take())
}

fn put_mic(mic: Microphone) {
    MIC.with(|slot| *slot.borrow_mut() = Some(mic));
}

impl Grove {
    pub(crate) fn new(studio: Studio, persist: bool) -> Self {
        let prefs = if persist { store::load() } else { Prefs::default() };
        let surface = if prefs.surface == "studio" {
            Surface::Studio
        } else {
            Surface::Grove
        };
        let bubble = prefs.bubble().unwrap_or(BubblePos { x: 760.0, y: 280.0 });
        Grove {
            studio,
            surface: Signal::new(surface),
            place: Signal::new(Place::Home),
            home_tab: Signal::new(HomeTab::Plots),
            room_tab: Signal::new(RoomTab::Alongside),
            board: Signal::new(None),
            goals: Signal::new(Vec::new()),
            head: Signal::new(None),
            children: Signal::new(Vec::new()),
            routines: Signal::new(Vec::new()),
            activity: Signal::new(Vec::new()),
            approvals: Signal::new(Vec::new()),
            life_task: Signal::new(None),
            tucked: Signal::new(false),
            seen: Signal::new(Vec::new()),
            explain_id: Signal::new(None),
            flash: Signal::new(None),
            decision_busy: Signal::new(false),
            messages: Signal::new(Vec::new()),
            partial: Signal::new(String::new()),
            streaming: Signal::new(false),
            loading: Signal::new(false),
            attachments: Signal::new(Vec::new()),
            uploading: Signal::new(false),
            attach_error: Signal::new(String::new()),
            plant_error: Signal::new(String::new()),
            mic_error: Signal::new(String::new()),
            mic_on: Signal::new(false),
            composer_value: Signal::new(String::new()),
            session_id: Signal::new(String::new()),
            child_messages: Signal::new(Vec::new()),
            child_loading: Signal::new(false),
            home_split: Signal::new(prefs.home_split),
            room_split: Signal::new(prefs.room_split),
            clock_hour: Signal::new(None),
            note: Signal::new(String::new()),
            err: Signal::new(String::new()),
            settings_open: Signal::new(false),
            job_open: Signal::new(false),
            routine_open: Signal::new(false),
            show_all: Signal::new(false),
            bubble_from: Signal::new(None),
            bubble_pos: Signal::new(bubble),
            stage_url: Signal::new(prefs.stage_url.clone()),
            stage_draft: Signal::new(prefs.stage_url),
            stage_status: Signal::new(String::new()),
            bookmarks: Signal::new(prefs.bookmarks),
            bookmarks_open: Signal::new(false),
            shield: Signal::new(prefs.shield),
            desktop_site: Signal::new(prefs.desktop_site),
            partner_name: Signal::new("Remedy".into()),
            agent_gender: Signal::new("female".into()),
            speak_replies: Signal::new(false),
            todos: Signal::new(Vec::new()),
            process: Signal::new(Vec::new()),
            focus_nonce: Signal::new(0),
            prefill: Signal::new(None),
            handoff: Signal::new(None),
            hide_window: Signal::new(0),
            busy_ui: Signal::new(false),
            draft_empty: Signal::new(true),
            local_only: false,
            persist,
            samples: Arc::new(Mutex::new(Vec::new())),
            client: Signal::new(None),
            home_session: Signal::new(prefs.home_session),
            goal_sessions: Signal::new(prefs.goal_sessions),
            live_tools: Signal::new(Vec::new()),
            next_msg: Signal::new(1),
            book: Arc::new(Book::new()),
            poll_started: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            busy: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            migrated: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            seen_armed: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            poll_query: Arc::new(Mutex::new(PollQuery {
                session: String::new(),
                child_session: String::new(),
                fast: false,
            })),
        }
    }

    /// Morning Grove with example plots, for the golden. It does not touch the network or disk.
    pub(crate) fn preview(studio: Studio) -> Self {
        let mut g = Self::new(studio, false);
        g.local_only = true;
        g.clock_hour.set(Some(9));
        g.partner_name.set("Remedy".into());
        g.install_head(HeadOverview {
            inner_activity: String::new(),
            inner_done: (0, 0, 0),
            session_id: String::new(),
            state: "idle".into(),
            capacity_note: "This PC is quiet.".into(),
            capacity: api::Capacity {
                slots: 2,
                busy: 0,
                paused: false,
                memory_known: true,
                free_mem_pct: 40,
            },
            children: Vec::new(),
            goals: Vec::new(),
            routines: Vec::new(),
            activity: Vec::new(),
            activity_unseen: 0,
            heartbeat: api::Heartbeat {
                enabled: true,
                every_minutes: 60,
                quiet_start: "22:00".into(),
                quiet_end: "07:00".into(),
                last_note: String::new(),
            },
            owner_channel: None,
            recent_channels: Vec::new(),
        });
        g.stage_url.set(logic::BROWSER_HOME.into());
        g.stage_draft.set(logic::BROWSER_HOME.into());
        g
    }

    pub(crate) fn attach_client(&self, client: Client) {
        self.client.set(Some(client.clone()));
        self.ensure_poll();
        let g = self.clone();
        self.spawn(move |c| api::settings_identity(&c), move |g, out| {
            if let Ok((name, gender)) = out {
                if !name.trim().is_empty() {
                    g.partner_name.set(name);
                }
                if !gender.trim().is_empty() {
                    g.agent_gender.set(gender);
                }
            }
        });
        let _ = g;
        self.spawn(move |c| api::speak_replies(&c), move |g, out| {
            if let Ok(on) = out {
                g.speak_replies.set(on);
            }
        });
    }

    pub(crate) fn ensure_poll(&self) {
        if self.local_only || self.client.get().is_none() {
            return;
        }
        if self
            .poll_started
            .swap(true, std::sync::atomic::Ordering::SeqCst)
        {
            return;
        }
        let Some(client) = self.client.get() else {
            self.poll_started
                .store(false, std::sync::atomic::Ordering::SeqCst);
            return;
        };
        let Ok(waker) = waker() else {
            self.poll_started
                .store(false, std::sync::atomic::Ordering::SeqCst);
            return;
        };
        let grove = self.clone();
        let _ = std::thread::Builder::new()
            .name("grove-poll".into())
            .spawn(move || poll_loop(client, grove, waker));
    }

    pub(crate) fn hour(&self) -> u32 {
        self.clock_hour.get().unwrap_or_else(local_hour)
    }

    pub(crate) fn partner(&self) -> String {
        let name = self.partner_name.get();
        if name.trim().is_empty() {
            "Remedy".into()
        } else {
            name
        }
    }

    pub(crate) fn on_studio(&self) -> bool {
        matches!(self.surface.get(), Surface::Studio)
    }

    pub(crate) fn show_studio(&self) {
        self.surface.set(Surface::Studio);
        self.save();
    }

    pub(crate) fn show_grove(&self) {
        self.surface.set(Surface::Grove);
        self.save();
    }

    /// Start a Studio session from a Grove request in the background: set the
    /// mode, start the prompt, and leave a status card in Grove that links to it.
    pub(crate) fn open_studio_session(&self, kind: logic::StudioKind, prompt: &str) {
        let prompt = prompt.trim().to_string();
        if prompt.is_empty() {
            return;
        }
        let mode = match kind {
            logic::StudioKind::Chat => WorkMode::Chat,
            logic::StudioKind::Plan => WorkMode::Plan,
            logic::StudioKind::Build => WorkMode::Build,
        };
        self.studio.set_mode(mode);
        // An empty open chat takes the work; anything else gets a session of its own.
        let fresh = !self.studio.messages.get().is_empty() || self.studio.streaming.get();
        if fresh {
            self.studio.add();
        }
        self.handoff.set(Some(StudioHandoff {
            kind,
            prompt: prompt.clone(),
            session: if fresh { None } else { self.studio.active.get() },
        }));
        self.studio.send(prompt);
    }

    /// Once the new session exists, the card follows it by id, so opening
    /// another Studio chat later does not change what the card reports.
    fn pin_handoff(&self) {
        let Some(item) = self.handoff.get() else { return };
        if item.session.is_some() || self.studio.depth.creating_session.get() {
            return;
        }
        if let Some(sid) = self.studio.active.get() {
            self.handoff.set(Some(StudioHandoff { session: Some(sid), ..item }));
        }
    }

    /// True while the handed-off session still has a turn running.
    pub(crate) fn handoff_working(&self) -> bool {
        let Some(sid) = self.handoff.get().and_then(|h| h.session) else {
            return self.handoff.get().is_some();
        };
        let open = self.studio.active.get().as_deref() == Some(sid.as_str());
        (open && self.studio.streaming.get())
            || self.studio.sessions.get().iter().any(|s| s.id == sid && s.claimed)
    }

    /// Show the handed-off session in Studio.
    pub(crate) fn open_handoff(&self) {
        self.pin_handoff();
        if let Some(sid) = self.handoff.get().and_then(|h| h.session) {
            self.studio.select(&sid);
        }
        self.show_studio();
    }

    pub(crate) fn dismiss_handoff(&self) {
        self.handoff.set(None);
    }

    pub(crate) fn go_home(&self) {
        self.place.set(Place::Home);
        self.home_tab.set(HomeTab::Plots);
        self.bubble_from.set(None);
        self.kick_home();
    }

    pub(crate) fn set_home_tab(&self, tab: HomeTab) {
        self.home_tab.set(tab);
        if tab != HomeTab::Plots {
            self.place.set(Place::Home);
        }
    }

    pub(crate) fn set_room_tab(&self, tab: RoomTab) {
        self.room_tab.set(tab);
    }

    pub(crate) fn open_goal(&self, id: &str) {
        self.place.set(Place::Goal(id.to_string()));
        self.room_tab.set(RoomTab::Alongside);
        self.bubble_from.set(None);
        self.kick_goal(id);
    }

    pub(crate) fn open_child(&self, id: &str) {
        self.place.set(Place::Child(id.to_string()));
        self.bubble_from.set(None);
        self.child_messages.set(Vec::new());
        self.child_loading.set(true);
        self.refresh_query();
    }

    /// Open a session the head named: a side job's room, or her own thread.
    pub(crate) fn open_linked(&self, session_id: &str) {
        if session_id.is_empty() {
            return;
        }
        if let Some(child) = self
            .children
            .get()
            .into_iter()
            .find(|c| c.session_id == session_id)
        {
            let id = child.child_id.clone();
            self.open_child(&id);
            return;
        }
        self.show_grove();
        self.place.set(Place::Home);
        self.home_tab.set(HomeTab::Plots);
        self.session_id.set(session_id.to_string());
        self.load_messages(session_id);
    }

    pub(crate) fn goal(&self, id: &str) -> Option<LifeGoal> {
        self.goals.get().into_iter().find(|g| g.id == id)
    }

    pub(crate) fn ordered_decisions(&self) -> Vec<Approval> {
        let items = self.approvals.get();
        let keys: Vec<logic::Decision> = items
            .iter()
            .map(|a| logic::Decision {
                id: a.id.clone(),
                tool_name: a.tool_name.clone(),
                created_at: a.created_at,
                status: a.status.clone(),
            })
            .collect();
        let order = logic::order_decisions(&keys);
        order
            .into_iter()
            .filter_map(|d| items.iter().find(|a| a.id == d.id).cloned())
            .collect()
    }

    pub(crate) fn current_decision(&self) -> Option<Approval> {
        self.ordered_decisions().into_iter().next()
    }

    pub(crate) fn waiting_on_you(&self) -> bool {
        let sid = self.session_id.get();
        let items = self.ordered_decisions();
        if sid.is_empty() {
            return !items.is_empty();
        }
        items
            .iter()
            .any(|a| a.session_id.is_empty() || a.session_id == sid)
    }

    pub(crate) fn focus_composer(&self) {
        let n = self.focus_nonce.get();
        self.focus_nonce.set(n.wrapping_add(1));
    }

    /// Accept a talk-bar send. The caller clears the field when this returns true.
    pub(crate) fn begin_send(&self, text: String) -> bool {
        let trimmed = text.trim().to_string();
        let attachments = self.attachments.get();
        if trimmed.is_empty() && attachments.is_empty() {
            return false;
        }
        if self
            .busy
            .compare_exchange(
                false,
                true,
                std::sync::atomic::Ordering::SeqCst,
                std::sync::atomic::Ordering::SeqCst,
            )
            .is_err()
        {
            return false;
        }
        self.busy_ui.set(true);
        self.attach_error.set(String::new());
        let on_home = matches!(self.place.get(), Place::Home);
        if logic::should_plant(on_home, &trimmed, attachments.len()) {
            let title = logic::planted_title(&trimmed).unwrap_or_default();
            self.release_busy();
            return self.plant(&title);
        }
        if on_home && attachments.is_empty() {
            if let Some(kind) = logic::studio_handoff(&trimmed) {
                self.release_busy();
                self.open_studio_session(kind, &trimmed);
                return true;
            }
        }
        if self.local_only {
            self.push_message("user", &trimmed, Vec::new());
            self.push_message("assistant", "Got it — I'll take that from here.", Vec::new());
            self.attachments.set(Vec::new());
            self.release_busy();
            return true;
        }
        self.push_message("user", &trimmed, Vec::new());
        let steer = logic::steer_first(self.streaming.get(), attachments.len());
        if !steer {
            self.streaming.set(true);
            self.partial.set(String::new());
            self.process.set(Vec::new());
            self.live_tools.set(Vec::new());
            self.todos.set(Vec::new());
        }
        let job = self.job(trimmed, attachments, steer);
        self.launch(job);
        true
    }

    pub(crate) fn send_about_child(&self, label: &str, text: &str) -> bool {
        let text = text.trim();
        if text.is_empty() {
            return false;
        }
        if self.bubble_from.get().is_none() {
            let anchor = self
                .messages
                .get()
                .last()
                .map(|m| m.id.clone())
                .unwrap_or_default();
            self.bubble_from.set(Some(anchor));
        }
        let place = self.place.get();
        self.place.set(Place::Home);
        let ok = self.begin_send(logic::about_child(label, text));
        self.place.set(place);
        ok
    }

    pub(crate) fn stop(&self) {
        if let (false, Some(client), sid) = (
            self.local_only,
            self.client.get(),
            self.session_id.get(),
        ) {
            if !sid.is_empty() {
                crate::runtime::abort_session(&client, &sid, |_| {});
            }
        }
        self.finish_turn();
    }

    pub(crate) fn plant(&self, title: &str) -> bool {
        let title = title.trim().to_string();
        if title.is_empty() {
            return false;
        }
        self.plant_error.set(String::new());
        if self.local_only {
            let id = format!("local-{}", self.next_msg.get());
            self.next_msg.set(self.next_msg.get() + 1);
            let goal = LifeGoal {
                id: id.clone(),
                title: title.clone(),
                horizon: "growing".into(),
                status: "growing".into(),
                next_action: String::new(),
                done_looks_like: String::new(),
            };
            self.goals.update(|g| g.push(goal));
            self.open_goal(&id);
            return true;
        }
        let title2 = title.clone();
        self.spawn(
            move |c| api::create_goal(&c, &title2),
            move |g, out| match out {
                Ok(goal) => {
                    g.open_goal(&goal.id);
                    g.refresh_board();
                }
                Err(_) => g
                    .plant_error
                    .set("I couldn't plant that goal just now — try again in a moment.".into()),
            },
        );
        true
    }

    pub(crate) fn remove_goal(&self, id: &str) {
        let id = id.to_string();
        if self.local_only {
            self.goals.update(|g| g.retain(|x| x.id != id));
            return;
        }
        self.spawn(
            move |c| api::delete_goal(&c, &id),
            |g, _| g.refresh_board(),
        );
    }

    pub(crate) fn rename_goal(&self, id: &str, title: &str) {
        let title = title.trim().to_string();
        let id = id.to_string();
        if title.is_empty() {
            return;
        }
        if self.local_only {
            self.goals.update(|g| {
                if let Some(goal) = g.iter_mut().find(|x| x.id == id) {
                    goal.title = title.clone();
                }
            });
            return;
        }
        self.spawn(
            move |c| api::patch_goal(&c, &id, &json!({ "title": title })),
            |g, _| g.refresh_board(),
        );
    }

    pub(crate) fn toggle_autopilot(&self, id: &str, on: bool) {
        let id = id.to_string();
        self.spawn(
            move |c| api::set_goal_link(&c, &id, &json!({ "autopilot": on })),
            |g, out| g.after_action(out),
        );
    }

    pub(crate) fn mark_complete(&self, id: &str) {
        let id = id.to_string();
        self.spawn(
            move |c| api::patch_goal(&c, &id, &json!({ "status": "done" })),
            |g, _| g.refresh_board(),
        );
    }

    pub(crate) fn step_now(&self, id: &str) {
        let id = id.to_string();
        self.spawn(move |c| api::step_goal(&c, &id), |g, out| g.after_action(out));
    }

    pub(crate) fn clear_activity(&self) {
        self.spawn(api::clear_activity, |g, _| g.refresh_board());
    }

    pub(crate) fn decide(&self, approve: bool, scope: &str) {
        let Some(item) = self.current_decision() else {
            return;
        };
        if self.decision_busy.get() {
            return;
        }
        self.decision_busy.set(true);
        let id = item.id.clone();
        let scope = if approve { scope.to_string() } else { "once".into() };
        self.spawn(
            move |c| api::resolve_approval(&c, &id, approve, &scope),
            move |g, out| {
                g.decision_busy.set(false);
                match out {
                    Ok(hint) => {
                        let text = if hint.is_empty() {
                            if approve {
                                "Okay — going ahead.".into()
                            } else {
                                "Okay — I won’t.".into()
                            }
                        } else {
                            hint
                        };
                        g.set_flash(text, false);
                        g.approvals.update(|list| list.retain(|a| a.id != item.id));
                        g.refresh_board();
                    }
                    Err(e) => g.set_flash(
                        if e.is_empty() {
                            "That didn’t go through — try again.".into()
                        } else {
                            e
                        },
                        true,
                    ),
                }
            },
        );
    }

    pub(crate) fn tuck(&self, on: bool) {
        self.tucked.set(on);
    }

    pub(crate) fn toggle_explain(&self) {
        let Some(item) = self.current_decision() else {
            return;
        };
        if self.explain_id.get().as_deref() == Some(item.id.as_str()) {
            self.explain_id.set(None);
        } else {
            self.explain_id.set(Some(item.id));
        }
    }

    pub(crate) fn act_life(&self, action: &str) {
        let task = self.life_task.get();
        let session = self.session_id.get();
        let action = action.to_string();
        let task_id = task.as_ref().map(|t| t.task_id.clone()).unwrap_or_default();
        let approval = task
            .as_ref()
            .map(|t| t.approval_id.clone())
            .unwrap_or_default();
        let session = if session.is_empty() { None } else { Some(session) };
        self.spawn(
            move |c| {
                api::act_life_task(
                    &c,
                    &action,
                    session.as_deref(),
                    &task_id,
                    &approval,
                )
            },
            |g, out| {
                if let Ok(spoken) = out {
                    if !spoken.is_empty() {
                        g.note.set(spoken);
                    }
                }
                g.refresh_life();
            },
        );
    }

    pub(crate) fn mic(&self) {
        self.mic_error.set(String::new());
        if self.mic_on.get() {
            self.mic_on.set(false);
            if let Some(mic) = take_mic() {
                mic.stop();
            }
            self.mic_on.set(false);
            if self.note.get() == "Hearing…" {
                self.note.set(String::new());
            }
            let samples = self
                .samples
                .lock()
                .map(|mut g| std::mem::take(&mut *g))
                .unwrap_or_default();
            self.transcribe(samples);
            return;
        }
        if let Ok(mut g) = self.samples.lock() {
            g.clear();
        }
        let buf = self.samples.clone();
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
            self.mic_error.set(msg);
            return;
        }
        self.mic_on.set(true);
        put_mic(mic);
    }

    fn transcribe(&self, samples: Vec<f32>) {
        self.mic_on.set(false);
        if samples.is_empty() {
            if self.note.get() == "Hearing…" {
                self.note.set(String::new());
            }
            self.mic_error.set("No speech captured.".into());
            return;
        }
        if self.local_only {
            if self.note.get() == "Hearing…" {
                self.note.set(String::new());
            }
            self.mic_error.set("Voice needs the local mind.".into());
            return;
        }
        self.note.set("Hearing…".into());
        let wav = api::wav_bytes(&samples, 48_000);
        self.spawn(
            move |c| api::transcribe(c, wav),
            |g, out| match out {
                Ok(heard) if heard.trim().is_empty() => {
                    if g.note.get() == "Hearing…" {
                        g.note.set(String::new());
                    }
                    g.mic_error.set("Nothing to transcribe.".into());
                }
                Ok(heard) => {
                    g.note.set(String::new());
                    g.mic_error.set(String::new());
                    let cur = g.composer_value.get();
                    let next = if cur.trim().is_empty() {
                        heard
                    } else {
                        format!("{cur} {heard}")
                    };
                    g.composer_value.set(next.clone());
                    g.draft_empty.set(next.trim().is_empty());
                    g.prefill.set(Some(next));
                }
                Err(e) => {
                    if g.note.get() == "Hearing…" {
                        g.note.set(String::new());
                    }
                    g.mic_error.set(e);
                }
            },
        );
    }

    pub(crate) fn pick_files(&self, app: &remedy_ui::App) {
        let mut dialog = remedy_ui::host::FileDialog::default();
        dialog.title = "Attach a file, receipt, or image".into();
        dialog.multiple = true;
        let g = self.clone();
        app.file_dialog(None, dialog, move |paths| g.upload_paths(paths));
    }

    pub(crate) fn upload_paths(&self, paths: Vec<PathBuf>) {
        if paths.is_empty() || self.local_only {
            return;
        }
        self.uploading.set(true);
        self.attach_error.set(String::new());
        let job = self.job(String::new(), Vec::new(), false);
        let g = self.clone();
        self.spawn(
            move |c| upload_all(&c, &g.book, &job, &paths),
            |g, out| {
                g.uploading.set(false);
                match out {
                    Ok((sid, files)) => {
                        g.remember_session(job_kind_home(&sid, &g), sid);
                        g.attachments.update(|list| list.extend(files));
                    }
                    Err(e) => g.attach_error.set(e),
                }
            },
        );
    }

    pub(crate) fn remove_attachment(&self, index: usize) {
        self.attachments.update(|list| {
            if index < list.len() {
                list.remove(index);
            }
        });
    }

    pub(crate) fn spawn_job(&self, task: &str, label: &str) {
        let task = task.trim().to_string();
        let label = label.trim().to_string();
        if task.is_empty() {
            return;
        }
        self.spawn(
            move |c| api::spawn_child(&c, &task, &label),
            |g, out| {
                // The form stays open with what was typed until the job is really started.
                match out {
                    Ok(note) => {
                        g.job_open.set(false);
                        if !note.is_empty() {
                            g.note.set(note);
                        }
                    }
                    Err(_) => g.note.set("That did not go through — try again.".into()),
                }
                g.refresh_head();
            },
        );
    }

    pub(crate) fn stop_child(&self, id: &str) {
        let id = id.to_string();
        self.spawn(move |c| api::stop_child(&c, &id), |g, out| {
            if out.is_ok() {
                g.note.set("Stopped.".into());
            }
            g.refresh_head();
        });
    }

    pub(crate) fn forget_child(&self, id: &str) {
        let id = id.to_string();
        self.spawn(move |c| api::forget_child(&c, &id), |g, out| g.after_action(out));
    }

    pub(crate) fn toggle_watch(&self, on: bool) {
        self.spawn(
            move |c| api::set_heartbeat(&c, &json!({ "enabled": on })),
            move |g, out| {
                g.note.set(
                    if out.is_err() {
                        "That did not go through — try again.".into()
                    } else if on {
                        "Remedy will keep watch.".into()
                    } else {
                        "Remedy stopped keeping watch.".into()
                    },
                );
                g.refresh_head();
            },
        );
    }

    pub(crate) fn save_heartbeat(&self, every: i64, quiet_start: String, quiet_end: String) {
        self.spawn(
            move |c| {
                api::set_heartbeat(
                    &c,
                    &json!({
                        "every_minutes": every,
                        "quiet_start": quiet_start,
                        "quiet_end": quiet_end,
                    }),
                )
            },
            |g, out| {
                // A failed save leaves the settings open with what was entered.
                if out.is_ok() {
                    g.note.set("Saved.".into());
                    g.settings_open.set(false);
                } else {
                    g.note.set("That did not go through — try again.".into());
                }
                g.refresh_head();
            },
        );
    }

    pub(crate) fn set_channel(&self, channel: Option<api::Channel>) {
        let noted = channel.clone();
        self.spawn(
            move |c| api::set_owner_channel(&c, channel.as_ref()),
            move |g, out| {
                g.note.set(if out.is_err() {
                    "That did not go through — try again.".into()
                } else if noted.is_some() {
                    "Updates will reach you there too.".into()
                } else {
                    "Updates stay in Grove.".into()
                });
                g.refresh_head();
            },
        );
    }

    pub(crate) fn check_now(&self) {
        self.spawn(api::run_heartbeat, |g, out| g.after_action(out));
    }

    pub(crate) fn toggle_routine(&self, id: &str, on: bool) {
        let id = id.to_string();
        self.spawn(
            move |c| api::patch_routine(&c, &id, &json!({ "enabled": on })),
            |g, out| g.after_action(out),
        );
    }

    pub(crate) fn run_routine(&self, id: &str, title: &str) {
        let id = id.to_string();
        let title = title.to_string();
        self.spawn(
            move |c| api::run_routine(&c, &id),
            move |g, out| {
                if out.is_ok() {
                    g.note.set(format!("{title} — on it."));
                }
                g.refresh_head();
            },
        );
    }

    pub(crate) fn retire_routine(&self, id: &str) {
        let id = id.to_string();
        self.spawn(
            move |c| api::delete_routine(&c, &id),
            |g, out| g.after_action(out),
        );
    }

    /// Save a routine: a new one, or an edit of `id`.
    /// `saved` turns true once the server has it, which is when the dialog
    /// closes; on failure `err` says why and the dialog keeps what was typed.
    pub(crate) fn save_routine(&self, id: Option<String>, body: Value, saved: Signal<bool>, err: Signal<String>) {
        self.spawn(
            move |c| match &id {
                Some(id) => api::patch_routine(&c, id, &body),
                None => api::create_routine(&c, &body),
            },
            move |g, out| {
                match out {
                    Ok(()) => {
                        g.routine_open.set(false);
                        g.note.set("Routine saved.".into());
                        // The dialog may already be gone (Cancel, Escape).
                        let _ = saved.try_set(true);
                    }
                    Err(why) => {
                        let text = if why.trim().is_empty() { "Could not save that routine.".to_string() } else { why };
                        let _ = err.try_set(text);
                    }
                }
                g.refresh_head();
            },
        );
    }

    pub(crate) fn mark_seen_later(&self) {
        let unseen = self
            .head
            .get()
            .map(|h| h.activity_unseen)
            .unwrap_or(0);
        if unseen <= 0
            || self
                .seen_armed
                .swap(true, std::sync::atomic::Ordering::SeqCst)
        {
            return;
        }
        let Ok(waker) = waker() else {
            self.seen_armed
                .store(false, std::sync::atomic::Ordering::SeqCst);
            return;
        };
        let g = self.clone();
        let _ = std::thread::Builder::new()
            .name("grove-seen".into())
            .spawn(move || {
                std::thread::sleep(Duration::from_secs(4));
                let _ = waker.post(move || {
                    g.spawn(api::mark_activity_seen, |g, _| {
                        g.seen_armed
                            .store(false, std::sync::atomic::Ordering::SeqCst);
                        g.refresh_head();
                    });
                });
            });
    }

    pub(crate) fn go(&self, raw: &str) {
        let url = logic::resolve_address(raw);
        if url.is_empty() {
            self.stage_status
                .set("That address cannot be opened.".into());
            return;
        }
        self.stage_url.set(url.clone());
        self.stage_draft.set(url.clone());
        if let Some(model) = crate::browser::live() {
            model.navigate_here(&url);
        }
        self.stage_status.set(
            "Asked the stage to open this address. The browser host owns the page.".into(),
        );
        self.save();
        let sid = self.session_id.get();
        self.spawn(
            move |c| api::browser_hello(&c, if sid.is_empty() { None } else { Some(&sid) }),
            |_, _| {},
        );
    }

    pub(crate) fn toggle_bookmark(&self) {
        let url = self.stage_url.get();
        if logic::resolve_address(&url).is_empty() {
            return;
        }
        let mut list = self.bookmarks.get();
        let starred = if let Some(i) = list.iter().position(|b| b.url == url) {
            list.remove(i);
            self.stage_status.set("Bookmark removed".into());
            false
        } else {
            list.push(Bookmark {
                title: url.clone(),
                url: url.clone(),
            });
            self.stage_status.set("Bookmarked".into());
            true
        };
        self.bookmarks.set(list);
        if let Some(model) = crate::browser::live()
            && model.bookmarked(&url) != starred
        {
            model.toggle_bookmark(&url);
        }
        self.save();
    }

    /// The browser rail starred or unstarred `url`. Match that state here.
    pub(crate) fn apply_browser_star(&self, url: &str, on: bool) {
        let mut list = self.bookmarks.get();
        if store::set_star(&mut list, url, on) {
            self.bookmarks.set(list);
            self.save();
        }
    }

    pub(crate) fn open_bookmark(&self, url: &str) {
        self.bookmarks_open.set(false);
        self.go(url);
    }

    pub(crate) fn remove_bookmark(&self, url: &str) {
        let url = url.to_string();
        self.bookmarks.update(|list| list.retain(|b| b.url != url));
        self.save();
    }

    pub(crate) fn save(&self) {
        if !self.persist {
            return;
        }
        let bubble = self.bubble_pos.get();
        store::save(&Prefs {
            surface: match self.surface.get() {
                Surface::Studio => "studio".into(),
                Surface::Grove => "grove".into(),
            },
            home_session: self.home_session.get(),
            goal_sessions: self.goal_sessions.get(),
            home_split: self.home_split.get(),
            room_split: self.room_split.get(),
            bubble_x: Some(bubble.x),
            bubble_y: Some(bubble.y),
            bookmarks: self.bookmarks.get(),
            shield: self.shield.get(),
            desktop_site: self.desktop_site.get(),
            stage_url: self.stage_url.get(),
        });
    }

    fn kick_home(&self) {
        if self.local_only {
            if self.session_id.get().is_empty() {
                self.session_id.set("grove-home".into());
            }
            return;
        }
        let job = self.job(String::new(), Vec::new(), false);
        let g = self.clone();
        self.spawn(
            move |c| ensure_session(&c, &g.book, &job),
            |g, out| {
                if let Ok(sid) = out {
                    g.remember_home(sid);
                }
            },
        );
    }

    fn kick_goal(&self, id: &str) {
        if self.local_only {
            self.session_id.set(format!("goal-{id}"));
            return;
        }
        let mut job = self.job(String::new(), Vec::new(), false);
        job.home = false;
        job.goal_id = id.to_string();
        if let Some(goal) = self.goal(id) {
            job.goal_title = goal.title;
        }
        if let Some(link) = self.head.get().and_then(|h| h.goal(id).cloned()) {
            job.goal_linked = link.session_id;
        }
        job.goal_stored = self
            .goal_sessions
            .get()
            .get(id)
            .cloned()
            .unwrap_or_default();
        self.loading.set(true);
        let goal_id = job.goal_id.clone();
        let book = self.book.clone();
        self.spawn(
            move |c| ensure_session(&c, &book, &job),
            move |g, out| {
                g.loading.set(false);
                if let Ok(sid) = out {
                    g.remember_goal(goal_id, sid);
                }
            },
        );
    }

    fn place_goal_id(&self) -> String {
        match self.place.get() {
            Place::Goal(id) => id,
            _ => String::new(),
        }
    }

    fn remember_home(&self, sid: String) {
        let changed = self.session_id.get() != sid;
        self.home_session.set(sid.clone());
        self.session_id.set(sid.clone());
        self.save();
        if changed && !self.streaming.get() {
            self.load_messages(&sid);
        }
        self.refresh_query();
    }

    fn remember_goal(&self, goal_id: String, sid: String) {
        if !goal_id.is_empty() {
            self.goal_sessions.update(|map| {
                map.insert(goal_id, sid.clone());
            });
        }
        self.session_id.set(sid.clone());
        self.save();
        if !self.streaming.get() {
            self.load_messages(&sid);
        }
        self.refresh_query();
    }

    fn remember_session(&self, home: bool, sid: String) {
        if home {
            self.remember_home(sid);
        } else {
            self.remember_goal(self.place_goal_id(), sid);
        }
    }

    fn load_messages(&self, id: &str) {
        if self.local_only || id.is_empty() {
            return;
        }
        let id = id.to_string();
        self.loading.set(true);
        self.spawn(
            move |c| api::messages(&c, &id, 80),
            |g, out| {
                g.loading.set(false);
                if let Ok(rows) = out {
                    if !g.streaming.get() {
                        g.messages.set(rows);
                    }
                }
            },
        );
    }

    fn refresh_board(&self) {
        self.spawn(api::life_board, |g, out| {
            if let Ok(board) = out {
                g.goals.set(board.goals.clone());
                g.board.set(Some(board));
            }
        });
        self.refresh_head();
    }

    fn refresh_head(&self) {
        self.pin_handoff();
        self.spawn(api::head, |g, out| {
            if let Ok(head) = out {
                g.note_links(&head);
                g.install_head(head);
            }
        });
    }

    fn install_head(&self, head: HeadOverview) {
        // Her own life reaches her face: what she is doing now, and what she has done.
        let (nights, words, runs) = head.inner_done;
        crate::presence::set_inner(&head.inner_activity, crate::face_skin::inner_points(nights, words, runs));
        self.children.set(head.children.clone());
        self.routines.set(head.routines.clone());
        self.activity.set(head.activity.clone());
        self.head.set(Some(head));
    }

    fn refresh_life(&self) {
        let sid = self.session_id.get();
        self.spawn(
            move |c| api::life_task(&c, if sid.is_empty() { None } else { Some(&sid) }),
            |g, out| {
                if let Ok(task) = out {
                    g.life_task.set(task);
                }
            },
        );
    }

    fn note_links(&self, head: &HeadOverview) {
        if self
            .migrated
            .swap(true, std::sync::atomic::Ordering::SeqCst)
        {
            return;
        }
        let local = self.goal_sessions.get();
        for (gid, sid) in local {
            let linked = head.goal(&gid).map(|l| l.session_id.clone()).unwrap_or_default();
            if linked.is_empty() && !sid.is_empty() {
                self.spawn(
                    move |c| api::set_goal_link(&c, &gid, &json!({ "session_id": sid })),
                    |_, _| {},
                );
            }
        }
    }

    fn job(&self, text: String, attachments: Vec<Attachment>, steer: bool) -> SendJob {
        let home = !matches!(self.place.get(), Place::Goal(_));
        let (goal_id, goal_title, goal_linked, goal_stored) = match self.place.get() {
            Place::Goal(id) => {
                let title = self
                    .goal(&id)
                    .map(|g| g.title)
                    .unwrap_or_default();
                let linked = self
                    .head
                    .get()
                    .and_then(|h| h.goal(&id).map(|l| l.session_id.clone()))
                    .unwrap_or_default();
                let stored = self
                    .goal_sessions
                    .get()
                    .get(&id)
                    .cloned()
                    .unwrap_or_default();
                (id, title, linked, stored)
            }
            _ => (String::new(), String::new(), String::new(), String::new()),
        };
        let head_session = self
            .head
            .get()
            .map(|h| h.session_id)
            .unwrap_or_default();
        SendJob {
            text,
            attachments,
            steer,
            home,
            head_session,
            stored_home: self.home_session.get(),
            goal_id,
            goal_title,
            goal_linked,
            goal_stored,
        }
    }

    fn launch(&self, job: SendJob) {
        let Some(client) = self.client.get() else {
            self.attach_error
                .set("The mind is not connected.".into());
            self.streaming.set(false);
            self.release_busy();
            return;
        };
        let Ok(wake) = waker() else {
            self.streaming.set(false);
            self.release_busy();
            return;
        };
        let book = self.book.clone();
        // Grove has no modes: Remedy decides what a request needs.
        let mode = WorkMode::Build;
        let model = self.studio.model.get();
        let provider = self.studio.provider.get();
        let grove = self.clone();
        let frames = wake.clone();
        let _ = std::thread::Builder::new().name("grove-send".into()).spawn(move || {
            let sid = match ensure_session(&client, &book, &job) {
                Ok(sid) => sid,
                Err(e) => {
                    let _ = wake.post(move || {
                        grove.attach_error.set(e);
                        if !job.steer {
                            grove.streaming.set(false);
                        }
                        grove.release_busy();
                    });
                    return;
                }
            };
            if job.steer && !job.text.is_empty() {
                match api::steer(&client, &sid, &job.text) {
                    Ok(true) => {
                        let home = job.home;
                        let _ = wake.post(move || {
                            grove.remember_session(home, sid);
                            grove.release_busy();
                        });
                        return;
                    }
                    Ok(false) => {
                        let g = grove.clone();
                        let _ = wake.post(move || {
                            g.partial.set(String::new());
                            g.process.set(Vec::new());
                            g.live_tools.set(Vec::new());
                            g.todos.set(Vec::new());
                            g.streaming.set(true);
                        });
                    }
                    Err(e) => {
                        let _ = wake.post(move || {
                            grove.attach_error.set(e);
                            grove.release_busy();
                        });
                        return;
                    }
                }
            }
            if job.text.is_empty() && job.attachments.is_empty() {
                let home = job.home;
                let _ = wake.post(move || {
                    grove.remember_session(home, sid);
                    grove.release_busy();
                });
                return;
            }
            let g2 = grove.clone();
            let wake_frames = frames.clone();
            api::stream_turn(
                &client,
                &sid,
                &job.text,
                &model,
                &provider,
                mode,
                &job.attachments,
                move |kind, data| {
                    let g = g2.clone();
                    let _ = wake_frames.post(move || g.on_frame(&kind, &data));
                },
            );
            let home = job.home;
            let _ = wake.post(move || {
                grove.streaming.set(true);
                grove.remember_session(home, sid);
                grove.release_busy();
            });
        });
    }

    fn push_message(&self, role: &str, text: &str, tools: Vec<ToolCall>) {
        let id = format!("m{}", self.next_msg.get());
        self.next_msg.set(self.next_msg.get() + 1);
        self.messages.update(|list| {
            list.push(StoryMessage {
                id,
                role: role.into(),
                content: text.into(),
                reverted: false,
                created_at: String::new(),
                tool_calls: tools,
            });
        });
    }

    fn on_frame(&self, kind: &str, data: &str) {
        match kind {
            "token" | "text" => {
                let piece = api::token_text(data);
                if !piece.is_empty() {
                    let mut partial = self.partial.get();
                    partial.push_str(&piece);
                    self.partial.set(partial);
                }
            }
            "tool_call" => {
                if let Some(call) = tool_from_frame(data) {
                    if let Some(line) = logic::describe_tool_call(&call) {
                        self.process.update(|rows| {
                            if rows.last().map(String::as_str) != Some(line.as_str()) {
                                rows.push(line);
                            }
                        });
                    }
                    self.live_tools.update(|tools| tools.push(call));
                }
            }
            "todos" => self.todos.set(logic::parse_todos(data)),
            "error" => {
                self.attach_error.set(api::error_text(data));
                self.finish_turn();
            }
            "done" => self.finish_turn(),
            _ => {}
        }
    }

    fn finish_turn(&self) {
        let partial = self.partial.get();
        let tools = self.live_tools.get();
        if !partial.is_empty() || !tools.is_empty() {
            self.push_message("assistant", &partial, tools);
        }
        let spoken = self
            .messages
            .get()
            .iter()
            .rev()
            .find(|m| m.role == "assistant" && !m.content.trim().is_empty() && !m.reverted)
            .map(|m| m.content.clone());
        self.partial.set(String::new());
        self.streaming.set(false);
        self.process.set(Vec::new());
        self.live_tools.set(Vec::new());
        self.release_busy();
        if self.speak_replies.get() {
            if let Some(text) = spoken {
                self.spawn(move |c| api::speak(&c, &text), |_, _| {});
            }
        }
    }

    fn release_busy(&self) {
        self.busy.store(false, std::sync::atomic::Ordering::SeqCst);
        self.busy_ui.set(false);
    }

    fn set_flash(&self, text: String, error: bool) {
        self.flash.set(Some((text.clone(), error)));
        let Ok(waker) = waker() else {
            return;
        };
        let g = self.clone();
        let wait = if error { 5 } else { 2 };
        let _ = std::thread::Builder::new()
            .name("grove-flash".into())
            .spawn(move || {
                std::thread::sleep(Duration::from_secs(wait));
                let _ = waker.post(move || {
                    if g.flash.get().as_ref().map(|(t, _)| t) == Some(&text) {
                        g.flash.set(None);
                    }
                });
            });
    }

    fn refresh_query(&self) {
        let child_session = match self.place.get() {
            Place::Child(id) => self
                .head
                .get()
                .and_then(|h| {
                    h.children
                        .iter()
                        .find(|c| c.child_id == id)
                        .map(|c| c.session_id.clone())
                })
                .unwrap_or_default(),
            _ => String::new(),
        };
        let fast = self.head.get().is_some_and(|h| {
            h.state == "working"
                || h.children
                    .iter()
                    .any(|c| c.state == "working" || c.state == "queued")
        });
        if let Ok(mut q) = self.poll_query.lock() {
            q.session = self.session_id.get();
            q.child_session = child_session;
            q.fast = fast;
        }
    }

    /// After an action the owner asked for: say so if it failed, then refresh.
    fn after_action<T>(&self, out: Result<T, String>) {
        if out.is_err() {
            self.note.set("That did not go through — try again.".into());
        }
        self.refresh_head();
    }

    fn spawn<T: Send + 'static>(
        &self,
        work: impl FnOnce(&Client) -> Result<T, String> + Send + 'static,
        done: impl FnOnce(Grove, Result<T, String>) + Send + 'static,
    ) {
        if self.local_only {
            return;
        }
        let Some(client) = self.client.get() else {
            return;
        };
        let Ok(waker) = waker() else {
            return;
        };
        let grove = self.clone();
        let _ = std::thread::Builder::new()
            .name("grove-io".into())
            .spawn(move || {
                let out = work(&client);
                let _ = waker.post(move || done(grove, out));
            });
    }

    fn apply_poll(&self, snap: PollSnap) {
        if let Ok(rows) = snap.approvals {
            let fresh = rows.iter().any(|a| !self.seen.get().iter().any(|s| s == &a.id));
            self.seen.update(|seen| {
                for row in &rows {
                    if !seen.iter().any(|s| s == &row.id) {
                        seen.push(row.id.clone());
                    }
                }
            });
            if fresh {
                self.tucked.set(false);
            }
            self.approvals.set(rows);
        }
        if let Ok(task) = snap.life {
            self.life_task.set(task);
        }
        if let Some(Ok(board)) = snap.board {
            self.goals.set(board.goals.clone());
            self.board.set(Some(board));
        }
        if let Some(Ok(head)) = snap.head {
            self.note_links(&head);
            self.install_head(head);
        }
        if let Some(Ok(rows)) = snap.child_messages {
            self.child_messages.set(rows);
            self.child_loading.set(false);
        }
        if let Some(true) = snap.speak {
            self.speak_replies.set(true);
        } else if let Some(false) = snap.speak {
            self.speak_replies.set(false);
        }
        if let Some(command) = snap.command {
            self.apply_command(&command);
        }
        if let Some(command) = snap.browser {
            self.apply_browser(&command);
        }
        self.refresh_query();
    }

    fn apply_command(&self, command: &Value) {
        let action = command
            .get("action")
            .and_then(|a| a.as_str())
            .unwrap_or("");
        let params = command.get("params").cloned().unwrap_or(Value::Null);
        let target = params
            .get("target")
            .and_then(|t| t.as_str())
            .unwrap_or("");
        match action {
            "switch_surface" => match target {
                "studio" => self.show_studio(),
                "alongside" => {
                    self.show_grove();
                    self.place.set(Place::Home);
                    self.home_tab.set(HomeTab::Alongside);
                    self.room_tab.set(RoomTab::Alongside);
                }
                "storyline" => {
                    self.show_grove();
                    self.place.set(Place::Home);
                    self.home_tab.set(HomeTab::Storyline);
                    self.room_tab.set(RoomTab::Storyline);
                }
                "home" | "grove" => self.go_home(),
                _ => self.show_grove(),
            },
            "open_goal" => {
                if let Some(id) = params.get("goal_id").and_then(|v| v.as_str()) {
                    self.show_grove();
                    self.open_goal(id);
                }
            }
            "focus_composer" => {
                self.show_grove();
                self.focus_composer();
            }
            "open_settings" => {
                self.show_grove();
                self.place.set(Place::Home);
                self.home_tab.set(HomeTab::Plots);
                self.settings_open.set(true);
            }
            "open_panel" => {
                let panel = params.get("panel").and_then(|v| v.as_str()).unwrap_or("");
                let article = params.get("article").and_then(|v| v.as_str());
                if !crate::chrome::handle_panel(panel, article, &self.studio) {
                    self.show_grove();
                }
            }
            "close_ui" => {
                let n = self.hide_window.get();
                self.hide_window.set(n.wrapping_add(1));
            }
            "new_session" => {
                if self.surface.get() == Surface::Studio {
                    self.studio.add();
                } else {
                    self.home_session.set(String::new());
                    self.session_id.set(String::new());
                    self.messages.set(Vec::new());
                    self.kick_home();
                    self.focus_composer();
                }
            }
            "open_session" => {
                if let Some(id) = params.get("session_id").and_then(|v| v.as_str()) {
                    self.show_grove();
                    self.place.set(Place::Home);
                    self.home_tab.set(HomeTab::Plots);
                    self.session_id.set(id.into());
                    self.load_messages(id);
                }
            }
            _ => {}
        }
    }

    fn apply_browser(&self, command: &Value) {
        let action = command
            .get("action")
            .and_then(|a| a.as_str())
            .unwrap_or("");
        if action != "open_browser" {
            return;
        }
        self.show_grove();
        match self.place.get() {
            Place::Home => self.home_tab.set(HomeTab::Alongside),
            Place::Goal(_) => self.room_tab.set(RoomTab::Alongside),
            Place::Child(_) => {
                self.place.set(Place::Home);
                self.home_tab.set(HomeTab::Alongside);
            }
        }
        if let Some(url) = command.get("url").and_then(|u| u.as_str()) {
            if !url.is_empty() {
                self.stage_url.set(url.into());
                self.stage_draft.set(url.into());
            }
        }
        let job_action = command
            .get("job_action")
            .and_then(|a| a.as_str())
            .unwrap_or("open");
        self.stage_status
            .set(format!("Alongside — the browser host is on “{job_action}”."));
        let sid = command
            .get("session_id")
            .and_then(|s| s.as_str())
            .unwrap_or("")
            .to_string();
        self.spawn(
            move |c| api::browser_hello(&c, if sid.is_empty() { None } else { Some(&sid) }),
            |_, _| {},
        );
    }
}

fn job_kind_home(sid: &str, grove: &Grove) -> bool {
    let _ = sid;
    !matches!(grove.place.get(), Place::Goal(_))
}

fn upload_all(
    client: &Client,
    book: &Book,
    job: &SendJob,
    paths: &[PathBuf],
) -> Result<(String, Vec<Attachment>), String> {
    let sid = ensure_session(client, book, job)?;
    let mut files = Vec::new();
    for path in paths {
        files.push(api::upload_file(client, &sid, path)?);
    }
    Ok((sid, files))
}

fn ensure_session(client: &Client, book: &Book, job: &SendJob) -> Result<String, String> {
    if job.home {
        let slot = book.slot("home");
        let head = job.head_session.clone();
        let stored = job.stored_home.clone();
        once(&slot, move || {
            if !head.is_empty() {
                if let Ok(None) = api::session_project(client, &head) {
                    return Ok(head);
                }
            }
            if !stored.is_empty() {
                if let Ok(None) = api::session_project(client, &stored) {
                    let _ = api::ensure_head_session(client, Some(&stored));
                    return Ok(stored);
                }
            }
            let id = api::create_grove_session(client, None)?;
            let _ = api::ensure_head_session(client, Some(&id));
            Ok(id)
        })
    } else {
        let slot = book.slot(&format!("goal:{}", job.goal_id));
        let linked = job.goal_linked.clone();
        let stored = job.goal_stored.clone();
        let title = job.goal_title.clone();
        let gid = job.goal_id.clone();
        once(&slot, move || {
            if !linked.is_empty() && api::session_project(client, &linked).is_ok() {
                return Ok(linked);
            }
            if !stored.is_empty() && api::session_project(client, &stored).is_ok() {
                return Ok(stored);
            }
            let titled = title.clone();
            let id = api::create_grove_session(client, Some(titled.as_str()))?;
            let _ = api::set_goal_link(client, &gid, &json!({ "session_id": id }));
            Ok(id)
        })
    }
}

struct PollSnap {
    approvals: Result<Vec<Approval>, String>,
    life: Result<Option<LifeTask>, String>,
    board: Option<Result<LifeBoard, String>>,
    head: Option<Result<HeadOverview, String>>,
    child_messages: Option<Result<Vec<StoryMessage>, String>>,
    speak: Option<bool>,
    command: Option<Value>,
    browser: Option<Value>,
}

fn poll_loop(client: Client, grove: Grove, waker: remedy_ui::reactive::Waker) {
    let mut tick = 0u32;
    loop {
        let query = grove
            .poll_query
            .lock()
            .map(|q| PollQuery {
                session: q.session.clone(),
                child_session: q.child_session.clone(),
                fast: q.fast,
            })
            .unwrap_or(PollQuery {
                session: String::new(),
                child_session: String::new(),
                fast: false,
            });
        let fetch_board = query.fast || tick % 3 == 0;
        let session = if query.session.is_empty() {
            None
        } else {
            Some(query.session.clone())
        };
        let snap = PollSnap {
            approvals: api::approvals(&client),
            life: api::life_task(&client, session.as_deref()),
            board: if fetch_board {
                Some(api::life_board(&client))
            } else {
                None
            },
            head: if fetch_board {
                Some(api::head(&client))
            } else {
                None
            },
            child_messages: if query.child_session.is_empty() {
                None
            } else {
                Some(api::messages(&client, &query.child_session, 80))
            },
            speak: api::speak_replies(&client).ok(),
            command: api::take_app_command(&client).ok().flatten(),
            browser: api::peek_browser_command(&client).ok().flatten(),
        };
        tick = tick.wrapping_add(1);
        let g = grove.clone();
        let wake = waker.clone();
        let _ = wake.post(move || g.apply_poll(snap));
        let pause = if query.fast { 2 } else { 4 };
        std::thread::sleep(Duration::from_secs(pause));
    }
}

fn tool_from_frame(data: &str) -> Option<ToolCall> {
    let v: Value = serde_json::from_str(data).ok()?;
    let name = v
        .get("name")
        .or_else(|| v.get("tool"))
        .and_then(|n| n.as_str())
        .unwrap_or("");
    if name.is_empty() {
        return None;
    }
    let args = v
        .get("args")
        .or_else(|| v.get("arguments"))
        .cloned()
        .unwrap_or(Value::Null);
    let args = if let Some(text) = args.as_str() {
        serde_json::from_str(text).unwrap_or(Value::String(text.into()))
    } else {
        args
    };
    Some(ToolCall {
        name: name.into(),
        args,
    })
}

fn local_hour() -> u32 {
    #[cfg(windows)]
    {
        #[repr(C)]
        struct SystemTime {
            _year: u16,
            _month: u16,
            _dow: u16,
            _day: u16,
            hour: u16,
            _minute: u16,
            _second: u16,
            _milli: u16,
        }
        unsafe extern "system" {
            fn GetLocalTime(out: *mut SystemTime);
        }
        unsafe {
            let mut st = std::mem::zeroed::<SystemTime>();
            GetLocalTime(&mut st);
            return u32::from(st.hour);
        }
    }
    #[cfg(not(windows))]
    {
        unsafe extern "C" {
            fn time(t: *mut i64) -> i64;
            fn localtime_r(t: *const i64, out: *mut u8) -> *mut u8;
        }
        unsafe {
            let mut secs = 0i64;
            time(&mut secs);
            let mut buf = [0u8; 64];
            if !localtime_r(&secs, buf.as_mut_ptr()).is_null() {
                let hour = i32::from_ne_bytes(buf[8..12].try_into().unwrap_or([0; 4]));
                if (0..24).contains(&hour) {
                    return hour as u32;
                }
            }
        }
        9
    }
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::studio::Studio;

    #[test]
    fn grove_mic_does_not_stub() {
        let g = Grove::preview(Studio::preview());
        g.mic();
        let err = g.mic_error.get();
        assert_ne!(
            err,
            "Microphone capture is not in this desktop build yet. Type instead."
        );
    }
}
