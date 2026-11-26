package httpapi

import (
	"context"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
)

// Remedy's head: the always-on side of the partner.
//
// Remedy is one partner, not a fleet. She keeps her own conversation (the
// head session), and when a job is better done on the side she starts a
// child — a session with one job that reports back to her. Routines, the
// heartbeat and goal autopilot all run under her. Children never start
// children of their own.
//
// Everything that runs without the owner typing goes through one queue and
// one governor (partner_governor.go), so the PC stays responsive however
// many jobs are waiting.

// Child / run states, in the words Grove shows.
const (
	partnerStateQueued  = "queued"
	partnerStateWorking = "working"
	partnerStateWaiting = "waiting" // needs the owner
	partnerStateBlocked = "blocked"
	partnerStateDone    = "done"
	partnerStateIdle    = "idle"
	partnerStateStopped = "stopped"
	partnerStateError   = "error"
)

const (
	partnerActivityMax     = 200
	partnerQueueMax        = 24
	partnerLiveChildrenMax = 12
	partnerRoutinesMax     = 40
	partnerRunTimeout      = 45 * time.Minute
)

type partnerHeartbeat struct {
	Enabled      bool   `json:"enabled"`
	EveryMinutes int    `json:"every_minutes"`
	QuietStart   string `json:"quiet_start"`
	QuietEnd     string `json:"quiet_end"`
	LastAt       string `json:"last_at,omitempty"`
	LastNote     string `json:"last_note,omitempty"`
	// LastDigest fingerprints what the last sweep reported, so an unchanged
	// house produces no new note.
	LastDigest string `json:"last_digest,omitempty"`
}

type partnerGoalLink struct {
	GoalID       string `json:"goal_id"`
	SessionID    string `json:"session_id,omitempty"`
	Autopilot    bool   `json:"autopilot"`
	EveryMinutes int    `json:"every_minutes,omitempty"`
	State        string `json:"state,omitempty"`
	LastUpdate   string `json:"last_update,omitempty"`
	LastRunAt    string `json:"last_run_at,omitempty"`
	Runs         int    `json:"runs,omitempty"`
	// Stalls counts runs in a row that reported the same thing; each one
	// doubles the wait before the next run (up to 8x) so a stuck goal does
	// not burn model usage repeating itself.
	Stalls int `json:"stalls,omitempty"`
	// OwnerSeenCount is the session message count when the goal last went
	// waiting/blocked; autopilot resumes once the owner has said something.
	OwnerSeenCount int `json:"owner_seen_count,omitempty"`
}

type partnerChild struct {
	SessionID       string `json:"session_id"`
	Label           string `json:"label"`
	Task            string `json:"task"`
	ParentSessionID string `json:"parent_session_id,omitempty"`
	GoalID          string `json:"goal_id,omitempty"`
	State           string `json:"state"`
	LastUpdate      string `json:"last_update,omitempty"`
	CreatedAt       string `json:"created_at"`
	UpdatedAt       string `json:"updated_at"`
	Runs            int    `json:"runs,omitempty"`
	// Mode is plan or chat when the session runs that way; empty is build.
	Mode string `json:"mode,omitempty"`
	// Outside is set when someone other than the owner had Remedy start this
	// child, or sent it a message since: its runs are not the owner's.
	Outside bool `json:"outside,omitempty"`
}

type partnerActivity struct {
	ID        string `json:"id"`
	TS        string `json:"ts"`
	Kind      string `json:"kind"` // child, routine, heartbeat, autopilot, note, messenger, system
	Title     string `json:"title"`
	Detail    string `json:"detail,omitempty"`
	State     string `json:"state,omitempty"`
	SessionID string `json:"session_id,omitempty"`
	GoalID    string `json:"goal_id,omitempty"`
	RoutineID string `json:"routine_id,omitempty"`
	Channel   string `json:"channel,omitempty"`
}

type partnerChannel struct {
	Channel  string `json:"channel"`
	ChatID   string `json:"chat_id"`
	Username string `json:"username,omitempty"`
	LastAt   string `json:"last_at,omitempty"`
}

type partnerState struct {
	Version       int                         `json:"version"`
	HeadSessionID string                      `json:"head_session_id,omitempty"`
	Heartbeat     partnerHeartbeat            `json:"heartbeat"`
	Goals         map[string]*partnerGoalLink `json:"goals"`
	Children      map[string]*partnerChild    `json:"children"`
	Routines      []*partnerRoutine           `json:"routines"`
	Activity      []partnerActivity           `json:"activity"`
	ActivitySeen  string                      `json:"activity_seen,omitempty"`
	OwnerChannel  *partnerChannel             `json:"owner_channel,omitempty"`
	RecentChats   []partnerChannel            `json:"recent_chats,omitempty"`
}

func defaultPartnerState() partnerState {
	return partnerState{
		Version: 1,
		Heartbeat: partnerHeartbeat{
			EveryMinutes: 60,
			QuietStart:   "22:00",
			QuietEnd:     "07:00",
		},
		Goals:    map[string]*partnerGoalLink{},
		Children: map[string]*partnerChild{},
		Routines: []*partnerRoutine{},
		Activity: []partnerActivity{},
	}
}

// partnerRunRequest is one piece of background work waiting for room.
type partnerRunRequest struct {
	Key       string // dedupe key: one queued run per key
	Kind      string // child, routine, heartbeat, autopilot
	SessionID string
	Prompt    string // what the model is asked
	Shown     string // what the session feed shows as the prompt ("" = Prompt)
	Origin    string
	Label     string
	GoalID    string
	RoutineID string
	Deliver   string // grove | messenger | both
	Mode      string // plan | chat; empty is build
	QueuedAt  time.Time
}

type partnerRunning struct {
	Req       partnerRunRequest
	StartedAt time.Time
	Cancel    context.CancelFunc
}

type partnerHub struct {
	mu      sync.Mutex
	path    string
	st      partnerState
	queue   []partnerRunRequest
	running map[string]*partnerRunning // by session id
	wake    chan struct{}
	cancel  context.CancelFunc
	wg      sync.WaitGroup
	now     func() time.Time
	readRes func() sysres.Snapshot
	// lastTick is when the scheduler loop last looked for due work.
	lastTick time.Time
}

func newPartnerHub(home string) *partnerHub {
	h := &partnerHub{
		running: map[string]*partnerRunning{},
		wake:    make(chan struct{}, 1),
		now:     time.Now,
		readRes: sysres.Read,
		st:      defaultPartnerState(),
	}
	if home = strings.TrimSpace(home); home != "" {
		h.path = filepath.Join(home, "partner_head.json")
		h.load()
	}
	return h
}

func (h *partnerHub) load() {
	if h.path == "" {
		return
	}
	st := defaultPartnerState()
	if err := readJSONFile(h.path, &st); err != nil {
		return
	}
	if st.Goals == nil {
		st.Goals = map[string]*partnerGoalLink{}
	}
	if st.Children == nil {
		st.Children = map[string]*partnerChild{}
	}
	if st.Routines == nil {
		st.Routines = []*partnerRoutine{}
	}
	if st.Activity == nil {
		st.Activity = []partnerActivity{}
	}
	if st.Heartbeat.EveryMinutes <= 0 {
		st.Heartbeat.EveryMinutes = 60
	}
	// A crash mid-run leaves "working" / "queued" on disk; nothing is running
	// in a fresh process, so say so instead of showing a spinner forever.
	for _, c := range st.Children {
		if c.State == partnerStateWorking || c.State == partnerStateQueued {
			c.State = partnerStateIdle
		}
	}
	for _, g := range st.Goals {
		if g.State == partnerStateWorking || g.State == partnerStateQueued {
			g.State = partnerStateIdle
		}
	}
	h.st = st
}

// saveLocked persists state. Callers hold h.mu.
func (h *partnerHub) saveLocked() {
	if h.path == "" {
		return
	}
	_ = writeJSONAtomic(h.path, h.st)
}

func (h *partnerHub) nowISO() string {
	return h.now().UTC().Format(time.RFC3339)
}

// recordLocked appends one activity row, newest last. Callers hold h.mu.
func (h *partnerHub) recordLocked(a partnerActivity) partnerActivity {
	if a.ID == "" {
		a.ID = shortHexID(10)
	}
	if a.TS == "" {
		a.TS = h.nowISO()
	}
	a.Title = truncateRunes(strings.TrimSpace(a.Title), 160)
	a.Detail = truncateRunes(strings.TrimSpace(a.Detail), 600)
	h.st.Activity = append(h.st.Activity, a)
	if over := len(h.st.Activity) - partnerActivityMax; over > 0 {
		h.st.Activity = append([]partnerActivity(nil), h.st.Activity[over:]...)
	}
	return a
}

func (h *partnerHub) record(a partnerActivity) {
	h.mu.Lock()
	h.recordLocked(a)
	h.saveLocked()
	h.mu.Unlock()
}

// enqueue adds a run unless one with the same key is already queued or
// running. It reports whether the run was accepted and its queue position.
func (h *partnerHub) enqueue(req partnerRunRequest) (accepted bool, position int, reason string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if req.QueuedAt.IsZero() {
		req.QueuedAt = h.now()
	}
	if req.Key != "" {
		for i, q := range h.queue {
			if q.Key == req.Key {
				return true, i + 1, "already waiting"
			}
		}
		for _, r := range h.running {
			if r.Req.Key == req.Key {
				return true, 0, "already running"
			}
		}
	}
	if len(h.queue) >= partnerQueueMax {
		return false, 0, "the queue is full — let some work finish first"
	}
	h.queue = append(h.queue, req)
	h.poke()
	return true, len(h.queue), ""
}

func (h *partnerHub) poke() {
	select {
	case h.wake <- struct{}{}:
	default:
	}
}

// dropQueued removes queued runs for a session (child stopped / deleted).
func (h *partnerHub) dropQueuedLocked(sessionID string) int {
	kept := h.queue[:0]
	n := 0
	for _, q := range h.queue {
		if q.SessionID == sessionID {
			n++
			continue
		}
		kept = append(kept, q)
	}
	h.queue = kept
	return n
}

func (h *partnerHub) sortedChildrenLocked() []*partnerChild {
	out := make([]*partnerChild, 0, len(h.st.Children))
	for _, c := range h.st.Children {
		out = append(out, c)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt > out[j].CreatedAt })
	return out
}

func (h *partnerHub) liveChildrenLocked() int {
	n := 0
	for _, c := range h.st.Children {
		switch c.State {
		case partnerStateDone, partnerStateStopped, partnerStateError:
		default:
			n++
		}
	}
	return n
}

// isPartnerOrigin reports a turn Remedy started on her own: a child, a
// routine, the heartbeat or goal autopilot. These are minted only by the
// runtime (never taken from a request body) for work the owner authorized.
func isPartnerOrigin(origin string) bool {
	return strings.HasPrefix(strings.ToLower(strings.TrimSpace(origin)), "partner:")
}

// partnerOutsidePrefix marks background work Remedy does because someone other
// than the owner asked for it: a routine or child a messenger sender or another
// agent had her create. It is not an owner origin, so that work asks before every
// change and reads only what a turn from outside may read (guards.go), however
// much later it runs.
const partnerOutsidePrefix = "partner-outside:"

// partnerOrigin is the origin of a background run of `kind` (child, routine, goal).
func partnerOrigin(kind, id string, outside bool) string {
	if outside {
		return partnerOutsidePrefix + kind + ":" + id
	}
	return "partner:" + kind + ":" + id
}

// isPartnerRun reports any background run Remedy started, trusted or not.
func isPartnerRun(origin string) bool {
	o := strings.ToLower(strings.TrimSpace(origin))
	return strings.HasPrefix(o, "partner:") || strings.HasPrefix(o, partnerOutsidePrefix)
}

// isChildOrigin reports a turn running inside one of Remedy's children.
func isChildOrigin(origin string) bool {
	o := strings.ToLower(strings.TrimSpace(origin))
	return strings.HasPrefix(o, "partner:child:") || strings.HasPrefix(o, partnerOutsidePrefix+"child:")
}
