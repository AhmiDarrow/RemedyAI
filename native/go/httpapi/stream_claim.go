package httpapi

import (
	"context"
	"sort"
	"strings"
	"sync"
)

// Exact 409 body from Python messages.py / stream.py (desktop may match on it).
const sessionBusyDetail = "Session already has a generation in progress. " +
	"Stop the current turn first, then send again."

const (
	abortReasonStop      = "stop"
	abortReasonSupersede = "supersede"
)

type claimEntry struct {
	epoch  int
	reason string
	cancel context.CancelFunc
	ctx    context.Context
}

const nudgeMax = 24

// streamClaims mirrors remedy.core.turn_context stream claim + abort epoch.
type streamClaims struct {
	mu       sync.Mutex
	bySID    map[string]*claimEntry
	epochs   map[string]int
	turns    sync.WaitGroup
	sidTurns map[string]*sync.WaitGroup
	nudges   map[string][]string
}

func newStreamClaims() *streamClaims {
	return &streamClaims{
		bySID:    make(map[string]*claimEntry),
		epochs:   make(map[string]int),
		sidTurns: make(map[string]*sync.WaitGroup),
		nudges:   make(map[string][]string),
	}
}

func normalizeAbortReason(reason string) string {
	r := strings.ToLower(strings.TrimSpace(reason))
	switch r {
	case "supersede", "superseded", "resend", "next_message":
		return abortReasonSupersede
	default:
		return abortReasonStop
	}
}

// TryClaim atomically claims sessionID for a new turn.
// ok=false → 409. On success, epoch and ctx are the live claim (never Background).
func (c *streamClaims) TryClaim(sessionID string) (epoch int, ctx context.Context, ok bool) {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return 0, nil, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, busy := c.bySID[sid]; busy {
		return 0, nil, false
	}
	n := c.epochs[sid] + 1
	c.epochs[sid] = n
	ctx, cancel := context.WithCancel(context.Background())
	c.bySID[sid] = &claimEntry{epoch: n, cancel: cancel, ctx: ctx}
	return n, ctx, true
}

// BeginTurn marks a detached turn goroutine for sid; EndTurn must be deferred.
func (c *streamClaims) BeginTurn(sessionID string) {
	if c == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	c.turns.Add(1)
	if sid == "" {
		return
	}
	c.mu.Lock()
	wg := c.sidTurns[sid]
	if wg == nil {
		wg = &sync.WaitGroup{}
		c.sidTurns[sid] = wg
	}
	wg.Add(1)
	c.mu.Unlock()
}

// EndTurn pairs with BeginTurn.
func (c *streamClaims) EndTurn(sessionID string) {
	if c == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	if sid != "" {
		c.mu.Lock()
		wg := c.sidTurns[sid]
		c.mu.Unlock()
		if wg != nil {
			wg.Done()
		}
	}
	c.turns.Done()
}

// WaitSessionTurn blocks until detached turns for sid have finished.
func (c *streamClaims) WaitSessionTurn(sessionID string) {
	if c == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		return
	}
	c.mu.Lock()
	wg := c.sidTurns[sid]
	c.mu.Unlock()
	if wg != nil {
		wg.Wait()
	}
}

// AbortAll cancels every live claim (server shutdown / Close).
func (c *streamClaims) AbortAll() {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, ent := range c.bySID {
		if ent.cancel != nil {
			ent.cancel()
		}
	}
}

// WaitTurns blocks until every BeginTurn has EndTurn'd.
func (c *streamClaims) WaitTurns() {
	if c == nil {
		return
	}
	c.turns.Wait()
}

// Epoch returns the current claim generation (0 if never claimed).
func (c *streamClaims) Epoch(sessionID string) int {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.epochs[sid]
}

// Context returns the claim context for cooperative cancel (nil if no claim).
func (c *streamClaims) Context(sessionID string) context.Context {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	ent, ok := c.bySID[sid]
	if !ok {
		return nil
	}
	return ent.ctx
}

// Release drops the claim when epoch matches (or epoch is nil).
func (c *streamClaims) Release(sessionID string, epoch *int) {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	ent, ok := c.bySID[sid]
	if !ok {
		return
	}
	if epoch != nil && ent.epoch != *epoch {
		return
	}
	if ent.cancel != nil {
		ent.cancel()
	}
	delete(c.bySID, sid)
	// A nudge nobody drained belongs to a turn that is over.
	delete(c.nudges, sid)
}

// Abort signals the live claim. Returns notified count (1 when a claim was
// cancelled). Keeps the claim until Release — matches Python abort_session.
// Stale epoch → 0 (caller reports status "ignored").
func (c *streamClaims) Abort(sessionID string, epoch *int, reason *string) int {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	cur := c.epochs[sid]
	if epoch != nil && cur != *epoch {
		return 0
	}
	ent, ok := c.bySID[sid]
	if reason != nil {
		r := normalizeAbortReason(*reason)
		if ok {
			ent.reason = r
		}
	}
	if !ok {
		return 0
	}
	if ent.cancel != nil {
		ent.cancel()
	}
	return 1
}

// PeekAbortReason returns the reason recorded by the last Abort, if any.
func (c *streamClaims) PeekAbortReason(sessionID string) string {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return ""
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	ent, ok := c.bySID[sid]
	if !ok {
		return ""
	}
	return ent.reason
}

// AnyActive reports whether any session currently holds a stream claim.
func (c *streamClaims) AnyActive() bool {
	if c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.bySID) > 0
}

// ActiveSessionIDs returns live claim session keys (excludes blank / _anon).
// Sorted for stable /connect/me selection when no focused sid is streaming.
func (c *streamClaims) ActiveSessionIDs() []string {
	if c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	out := make([]string, 0, len(c.bySID))
	for sid := range c.bySID {
		sid = strings.TrimSpace(sid)
		if sid == "" || sid == "_anon" {
			continue
		}
		out = append(out, sid)
	}
	sort.Strings(out)
	return out
}

// IsClaimed reports whether sessionID holds a live claim.
func (c *streamClaims) IsClaimed(sessionID string) bool {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.bySID[sid]
	return ok
}

// TryPushNudge queues owner mid-turn text. reason is ok | empty | no_turn | nudge_full.
func (c *streamClaims) TryPushNudge(sessionID, text string) (ok bool, reason string) {
	sid := strings.TrimSpace(sessionID)
	body := strings.TrimSpace(text)
	if sid == "" || body == "" || c == nil {
		return false, "empty"
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, busy := c.bySID[sid]; !busy {
		return false, "no_turn"
	}
	lst := c.nudges[sid]
	if len(lst) >= nudgeMax {
		return false, "nudge_full"
	}
	c.nudges[sid] = append(lst, body)
	return true, "ok"
}

// DrainNudges takes every queued nudge for sessionID (empty when none).
func (c *streamClaims) DrainNudges(sessionID string) []string {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	out := c.nudges[sid]
	delete(c.nudges, sid)
	if len(out) == 0 {
		return nil
	}
	return append([]string(nil), out...)
}

// ClearNudges drops queued mid-turn text for sessionID.
func (c *streamClaims) ClearNudges(sessionID string) {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.nudges, sid)
}
