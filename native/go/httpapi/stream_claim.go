package httpapi

import (
	"context"
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

// streamClaims mirrors remedy.core.turn_context stream claim + abort epoch.
type streamClaims struct {
	mu     sync.Mutex
	bySID  map[string]*claimEntry
	epochs map[string]int
}

func newStreamClaims() *streamClaims {
	return &streamClaims{
		bySID:  make(map[string]*claimEntry),
		epochs: make(map[string]int),
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

// TryClaim atomically claims sessionID for a new turn. False → 409.
func (c *streamClaims) TryClaim(sessionID string) bool {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || c == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, busy := c.bySID[sid]; busy {
		return false
	}
	n := c.epochs[sid] + 1
	c.epochs[sid] = n
	ctx, cancel := context.WithCancel(context.Background())
	c.bySID[sid] = &claimEntry{epoch: n, cancel: cancel, ctx: ctx}
	return true
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
