package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

const sensitivePrefix = "Owner checkpoint"

const (
	// approvalRetention is how long a resolved item stays queryable by id.
	approvalRetention = time.Hour
	// oneShotGrantTTL bounds a payment/credential go-ahead that was never
	// consumed (turn aborted, model changed its mind).
	oneShotGrantTTL = 10 * time.Minute
)

type pendingApproval struct {
	ID              string  `json:"id"`
	ToolName        string  `json:"tool_name"`
	Command         string  `json:"command"`
	Reason          string  `json:"reason"`
	SessionID       *string `json:"session_id"`
	CreatedAt       float64 `json:"created_at"`
	Status          string  `json:"status"`
	Fingerprint     string  `json:"-"`
	Sensitive       bool    `json:"sensitive"`
	Origin          string  `json:"-"`
	SummaryOverride string  `json:"-"`
	// Slots counts the tool calls this item covers. Identical calls in one
	// batch share one banner item; a sensitive approval then grants exactly
	// that many one-shot executions.
	Slots      int       `json:"-"`
	resolvedAt time.Time // zero while pending
}

type approvalResolveResult struct {
	approved bool
}

// oneShotGrant is a consumed-per-call go-ahead for a sensitive item. It is
// keyed by the approval id that produced it and expires after
// oneShotGrantTTL.
type oneShotGrant struct {
	fingerprint string
	origin      string
	slots       int
	consumed    map[string]struct{}
	expires     time.Time
}

type approvalQueue struct {
	mu           sync.Mutex
	items        map[string]*pendingApproval
	approvedFPs  map[string]struct{}
	sessionFPs   map[string]map[string]struct{}
	sessionOrder []string
	oneShot      map[string]map[string]*oneShotGrant // sid -> approval id -> grant
	waiters      map[string]chan approvalResolveResult
	mode         string
	now          func() time.Time

	// observer records approval activity into the session's live turn log, so
	// the owner can see afterwards what they were asked and what they decided.
	// Nil in tests and before the server wires it.
	observer func(sessionID, id, tool, summary, decision string)
}

// SetObserver installs the approval-activity sink (see observer).
func (q *approvalQueue) SetObserver(fn func(sessionID, id, tool, summary, decision string)) {
	if q == nil {
		return
	}
	q.mu.Lock()
	q.observer = fn
	q.mu.Unlock()
}

// notify calls the observer outside the queue lock — the sink writes to a file.
func (q *approvalQueue) notify(item *pendingApproval, decision string) {
	if q == nil || item == nil {
		return
	}
	q.mu.Lock()
	fn := q.observer
	q.mu.Unlock()
	if fn == nil {
		return
	}
	sid := ""
	if item.SessionID != nil {
		sid = *item.SessionID
	}
	fn(sid, item.ID, item.ToolName, plainApprovalSummary(item), decision)
}

func newApprovalQueue() *approvalQueue {
	return &approvalQueue{
		items:       map[string]*pendingApproval{},
		approvedFPs: map[string]struct{}{},
		sessionFPs:  map[string]map[string]struct{}{},
		oneShot:     map[string]map[string]*oneShotGrant{},
		waiters:     map[string]chan approvalResolveResult{},
		mode:        "ask",
		now:         time.Now,
	}
}

func normalizeApprovalMode(raw string) string {
	m := strings.ToLower(strings.TrimSpace(raw))
	switch m {
	case "full", "warn", "owner", "yolo", "unrestricted":
		return "full"
	case "auto", "auto-approve", "thumbs-up", "trust":
		return "auto"
	case "ask", "confirm", "manual", "safe":
		return "ask"
	case "":
		return "ask"
	default:
		if m == "ask" || m == "auto" || m == "full" {
			return m
		}
		return "ask"
	}
}

func approvalFingerprint(toolName, command string) string {
	return toolName + "::" + strings.TrimSpace(command)
}

func sessionKey(sessionID *string) string {
	if sessionID != nil && strings.TrimSpace(*sessionID) != "" {
		return *sessionID
	}
	return "default"
}

func shortHexID(n int) string {
	b := make([]byte, (n+1)/2)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)[:n]
}

// clipUTF8 truncates s to at most max bytes without splitting a rune.
func clipUTF8(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	cut := max
	for cut > 0 && !utf8.RuneStart(s[cut]) {
		cut--
	}
	return s[:cut]
}

func (q *approvalQueue) Mode() string {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.mode
}

// SetMode switches ask/auto/full. Entering auto or full approves every
// pending non-sensitive item and wakes the turns blocked on them; sensitive
// (payment / credential) items stay pending — no mode waives an owner moment.
func (q *approvalQueue) SetMode(mode string) string {
	m := normalizeApprovalMode(mode)
	q.mu.Lock()
	defer q.mu.Unlock()
	prev := q.mode
	q.mode = m
	if (m == "auto" || m == "full") && prev != "auto" && prev != "full" {
		for _, item := range q.items {
			if item.Status != "pending" || item.Sensitive {
				continue
			}
			item.Status = "approved"
			item.resolvedAt = q.now()
			q.addSessionFPLocked(sessionKey(item.SessionID), item.Fingerprint)
			q.signalWaiterLocked(item.ID, true)
		}
	}
	return q.mode
}

func (q *approvalQueue) SyncFromConfig(cfg ConfigMap) string {
	if cfg == nil {
		q.mu.Lock()
		defer q.mu.Unlock()
		return q.mode
	}
	if _, ok := cfg["approval_mode"]; !ok {
		q.mu.Lock()
		defer q.mu.Unlock()
		return q.mode
	}
	return q.SetMode(cfgString(cfg, "approval_mode", "ask"))
}

func (q *approvalQueue) addSessionFPLocked(sessionID, fp string) {
	set, ok := q.sessionFPs[sessionID]
	if !ok {
		set = map[string]struct{}{}
		q.sessionFPs[sessionID] = set
		q.sessionOrder = append(q.sessionOrder, sessionID)
	}
	set[fp] = struct{}{}
	for len(q.sessionFPs) > 48 {
		old := q.sessionOrder[0]
		q.sessionOrder = q.sessionOrder[1:]
		delete(q.sessionFPs, old)
	}
}

// Enqueue adds a pending approval (tool gate / tests). An identical pending
// call in the same session (same fingerprint) joins the existing item instead
// of raising a second banner; the item then covers one more slot.
//
// Sensitivity comes from the reason prefix (checkpoint-risk tools) or from
// the call shape itself (payment / credential predicates), so a mutation
// tool aimed at "Place order" is an owner moment even when the caller only
// knows it as a mutation.
func (q *approvalQueue) Enqueue(toolName, command, reason string, sessionID *string, summary string) *pendingApproval {
	item := q.enqueueItem(toolName, command, reason, sessionID, summary)
	// Outside the lock: the observer writes to the turn log.
	q.notify(item, "asked")
	return item
}

func (q *approvalQueue) enqueueItem(toolName, command, reason string, sessionID *string, summary string) *pendingApproval {
	fp := approvalFingerprint(toolName, command)
	sensitive := strings.HasPrefix(reason, sensitivePrefix) ||
		approvalIsSensitive(cognition.ToolCall{Name: toolName, Input: []byte(command)})
	q.mu.Lock()
	defer q.mu.Unlock()
	now := q.now()
	q.pruneLocked(now)
	sid := sessionKey(sessionID)
	for _, existing := range q.items {
		if existing.Status == "pending" && existing.Fingerprint == fp && sessionKey(existing.SessionID) == sid {
			existing.Slots++
			return existing
		}
	}
	item := &pendingApproval{
		ID:              shortHexID(12),
		ToolName:        toolName,
		Command:         command,
		Reason:          reason,
		SessionID:       sessionID,
		CreatedAt:       float64(now.UnixNano()) / 1e9,
		Status:          "pending",
		Fingerprint:     fp,
		Sensitive:       sensitive,
		SummaryOverride: strings.TrimSpace(summary),
		Slots:           1,
	}
	q.items[item.ID] = item
	return item
}

// pruneLocked drops resolved items past retention and expired one-shot
// grants. Pending items and items with a registered waiter are never
// pruned — a blocked turn must always be able to find its approval.
func (q *approvalQueue) pruneLocked(now time.Time) {
	cutoff := now.Add(-approvalRetention)
	for id, item := range q.items {
		if item.Status == "pending" {
			continue
		}
		if _, waiting := q.waiters[id]; waiting {
			continue
		}
		if !item.resolvedAt.IsZero() && item.resolvedAt.Before(cutoff) {
			delete(q.items, id)
		}
	}
	for sid, grants := range q.oneShot {
		for id, grant := range grants {
			if !grant.expires.After(now) || grant.slots <= 0 {
				delete(grants, id)
			}
		}
		if len(grants) == 0 {
			delete(q.oneShot, sid)
		}
	}
}

func (q *approvalQueue) ListPending(sessionID string) []*pendingApproval {
	q.mu.Lock()
	defer q.mu.Unlock()
	out := make([]*pendingApproval, 0)
	for _, v := range q.items {
		if v.Status != "pending" {
			continue
		}
		if sessionID != "" {
			if v.SessionID != nil && *v.SessionID != "" && *v.SessionID != sessionID {
				continue
			}
		}
		cp := *v
		out = append(out, &cp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt > out[j].CreatedAt })
	return out
}

func (q *approvalQueue) Resolve(id string, approve bool, scope string) *pendingApproval {
	item, _ := q.resolve(id, approve, scope)
	decision := "denied"
	if approve {
		decision = "approved"
	}
	q.notify(item, decision)
	return item
}

// resolve applies approve/deny and reports whether a blocked turn was waiting.
func (q *approvalQueue) resolve(id string, approve bool, scope string) (*pendingApproval, bool) {
	if scope != "session" && scope != "always" {
		scope = "session"
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	item := q.items[id]
	if item == nil {
		return nil, false
	}
	if item.Status != "pending" {
		cp := *item
		return &cp, false
	}
	resumed := false
	if _, ok := q.waiters[id]; ok {
		resumed = approve
	}
	now := q.now()
	item.resolvedAt = now
	if approve {
		item.Status = "approved"
		sid := sessionKey(item.SessionID)
		switch {
		case item.Sensitive:
			// One go-ahead is not standing consent: grant exactly the covered
			// calls, keyed by this approval, and let it lapse if unused.
			if q.oneShot[sid] == nil {
				q.oneShot[sid] = map[string]*oneShotGrant{}
			}
			slots := item.Slots
			if slots < 1 {
				slots = 1
			}
			q.oneShot[sid][item.ID] = &oneShotGrant{
				fingerprint: item.Fingerprint,
				origin:      item.Origin,
				slots:       slots,
				consumed:    map[string]struct{}{},
				expires:     now.Add(oneShotGrantTTL),
			}
		case scope == "always":
			q.approvedFPs[item.Fingerprint] = struct{}{}
			for len(q.approvedFPs) > 1000 {
				for k := range q.approvedFPs {
					delete(q.approvedFPs, k)
					break
				}
			}
		default:
			q.addSessionFPLocked(sid, item.Fingerprint)
		}
	} else {
		item.Status = "denied"
	}
	q.signalWaiterLocked(id, approve)
	cp := *item
	return &cp, resumed
}

func (q *approvalQueue) signalWaiterLocked(id string, approve bool) {
	ch := q.waiters[id]
	if ch == nil {
		return
	}
	delete(q.waiters, id)
	select {
	case ch <- approvalResolveResult{approved: approve}:
	default:
	}
}

// HasWaiter reports whether a live turn is blocked on this approval id.
func (q *approvalQueue) HasWaiter(id string) bool {
	if q == nil {
		return false
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	_, ok := q.waiters[id]
	return ok
}

// WaitAll blocks until every id is approved, or returns false if any is denied.
// Already-resolved ids are checked without waiting. Duplicate ids (identical
// calls sharing one item) wait once. Context cancel unregisters waiters and
// returns ctx.Err().
func (q *approvalQueue) WaitAll(ctx context.Context, ids []string) (bool, error) {
	if q == nil {
		return false, errors.New("approval queue is nil")
	}
	ids = uniqueApprovalIDs(ids)
	if len(ids) == 0 {
		return true, nil
	}
	type waitSlot struct {
		id string
		ch <-chan approvalResolveResult
	}
	var waiting []waitSlot
	q.mu.Lock()
	for _, id := range ids {
		item := q.items[id]
		if item == nil {
			q.mu.Unlock()
			q.clearWaiters(ids)
			return false, fmt.Errorf("approval %s not found", id)
		}
		switch item.Status {
		case "approved":
			continue
		case "denied":
			q.mu.Unlock()
			q.clearWaiters(ids)
			return false, nil
		default:
			ch := make(chan approvalResolveResult, 1)
			q.waiters[id] = ch
			waiting = append(waiting, waitSlot{id: id, ch: ch})
		}
	}
	q.mu.Unlock()

	for _, slot := range waiting {
		select {
		case <-ctx.Done():
			q.clearWaiters(ids)
			return false, ctx.Err()
		case res := <-slot.ch:
			if !res.approved {
				q.denyRemaining(ids, slot.id)
				q.clearWaiters(ids)
				return false, nil
			}
		}
	}
	return true, nil
}

func uniqueApprovalIDs(ids []string) []string {
	out := make([]string, 0, len(ids))
	seen := map[string]struct{}{}
	for _, id := range ids {
		id = strings.TrimSpace(id)
		if id == "" {
			continue
		}
		if _, dup := seen[id]; dup {
			continue
		}
		seen[id] = struct{}{}
		out = append(out, id)
	}
	return out
}

func (q *approvalQueue) clearWaiters(ids []string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for _, id := range ids {
		delete(q.waiters, id)
	}
}

// denyRemaining marks other still-pending ids in the batch as denied so the
// banner clears when the owner rejects one tool in a multi-tool Ask batch.
func (q *approvalQueue) denyRemaining(ids []string, except string) {
	q.mu.Lock()
	defer q.mu.Unlock()
	for _, id := range ids {
		if id == except {
			continue
		}
		item := q.items[id]
		if item == nil || item.Status != "pending" {
			continue
		}
		item.Status = "denied"
		item.resolvedAt = q.now()
		q.signalWaiterLocked(id, false)
	}
}

func (q *approvalQueue) Get(id string) *pendingApproval {
	q.mu.Lock()
	defer q.mu.Unlock()
	item := q.items[id]
	if item == nil {
		return nil
	}
	cp := *item
	return &cp
}

// IsApproved reports whether this tool+command fingerprint was already
// approved for the session (or always). Used so Ask → Approve → retry works.
// A one-shot grant also satisfies it (consumed, unbound to a call id).
func (q *approvalQueue) IsApproved(toolName, command, sessionID string) bool {
	if q == nil {
		return false
	}
	fp := approvalFingerprint(toolName, command)
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = "default"
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if _, ok := q.approvedFPs[fp]; ok {
		return true
	}
	if set, ok := q.sessionFPs[sid]; ok {
		if _, ok := set[fp]; ok {
			return true
		}
	}
	return q.consumeOneShotLocked(sid, fp, "")
}

// IsOneShotApproved consumes one slot of a sensitive go-ahead for this call.
// Standing session / always approvals never satisfy it — payment and
// credential steps ask every time. callID keeps two identical calls in one
// batch from sharing a slot and stops a single call from consuming twice.
func (q *approvalQueue) IsOneShotApproved(toolName, command, sessionID, callID string) bool {
	if q == nil {
		return false
	}
	fp := approvalFingerprint(toolName, command)
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = "default"
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.consumeOneShotLocked(sid, fp, callID)
}

func (q *approvalQueue) consumeOneShotLocked(sid, fp, callID string) bool {
	grants := q.oneShot[sid]
	if len(grants) == 0 {
		return false
	}
	now := q.now()
	// Deterministic order so repeated calls drain grants oldest-first.
	ids := make([]string, 0, len(grants))
	for id := range grants {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return grants[ids[i]].expires.Before(grants[ids[j]].expires) })
	for _, id := range ids {
		grant := grants[id]
		if !grant.expires.After(now) || grant.slots <= 0 {
			delete(grants, id)
			continue
		}
		if grant.fingerprint != fp {
			continue
		}
		if callID != "" {
			if _, done := grant.consumed[callID]; done {
				continue
			}
			grant.consumed[callID] = struct{}{}
		}
		grant.slots--
		if grant.slots <= 0 {
			delete(grants, id)
		}
		if len(grants) == 0 {
			delete(q.oneShot, sid)
		}
		return true
	}
	return false
}

func plainApprovalSummary(item *pendingApproval) string {
	if s := strings.TrimSpace(item.SummaryOverride); s != "" {
		return s
	}
	tool := strings.TrimSpace(item.ToolName)
	cmd := clipUTF8(strings.TrimSpace(item.Command), 120)
	if tool == "life_drive" {
		first := strings.TrimSpace(strings.Split(item.Command, "\n")[0])
		if strings.HasPrefix(strings.ToLower(first), "remedy will") {
			if strings.HasSuffix(first, "?") {
				return first
			}
			return strings.TrimRight(first, ".") + ". Yes, No, or Explain?"
		}
		return "Remedy wants to drive this computer through a plan. Yes, No, or Explain?"
	}
	if tool == "bash_exec" || tool == "shell.exec" {
		return "Remedy wants to run a command: " + cmd
	}
	if tool == "file_write" || tool == "file_edit" || tool == "workspace.write" || tool == "workspace.edit" {
		return "Remedy wants to change a file: " + cmd
	}
	if tool == "mail_send" || tool == "mail.send" {
		return "Remedy wants to send an email."
	}
	if tool == "computer.navigate" {
		return "Remedy wants to open a page in the Browser rail: " + cmd
	}
	return "Remedy wants to run " + tool + ": " + cmd
}

func (q *approvalQueue) ToPublic(item *pendingApproval) map[string]any {
	cmd := clipUTF8(item.Command, 500)
	var soft any
	low := strings.ToLower(item.Reason)
	if strings.Contains(low, "soft-risk:") || strings.Contains(item.Reason, "Soft-risk") {
		soft = item.Reason
	}
	hint := "Approve for this session, or set Approvals → Auto (in-project) " +
		"to finish work without prompts. Full (warn) turns the write jail " +
		"into a warning only — auth secrets stay closed."
	if item.Sensitive {
		hint = "One-time go-ahead for payment steps — these ask in every mode. "
	}
	choices := []string{"yes", "no"}
	if item.ToolName == "life_drive" {
		choices = []string{"yes", "no", "explain"}
	}
	var sid any
	if item.SessionID != nil {
		sid = *item.SessionID
	}
	return map[string]any{
		"id":                 item.ID,
		"tool_name":          item.ToolName,
		"command":            cmd,
		"reason":             item.Reason,
		"summary":            plainApprovalSummary(item),
		"sensitive":          item.Sensitive,
		"soft_risk":          soft,
		"session_id":         sid,
		"status":             item.Status,
		"created_at":         item.CreatedAt,
		"approval_mode_hint": hint,
		"choices":            choices,
	}
}

func (s *Server) handleListApprovals(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	items := s.approvals.ListPending(sid)
	out := make([]map[string]any, 0, len(items))
	for _, it := range items {
		out = append(out, s.approvals.ToPublic(it))
	}
	writeJSON(w, http.StatusOK, map[string]any{"approvals": out})
}

// handleResolveApproval requires an explicit JSON decision: {"approve": true|false}.
// A missing or malformed body is a 400, never an implicit approve.
func (s *Server) handleResolveApproval(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("approval_id"))
	var req struct {
		Approve *bool  `json:"approve"`
		Scope   string `json:"scope"`
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 64<<10))
	if err != nil || len(strings.TrimSpace(string(raw))) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body with boolean approve is required"})
		return
	}
	if err := json.Unmarshal(raw, &req); err != nil || req.Approve == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body with boolean approve is required"})
		return
	}
	scope := strings.TrimSpace(req.Scope)
	if scope == "" {
		scope = "session"
	}
	item, resumed := s.approvals.resolve(id, *req.Approve, scope)
	if item == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Approval not found"})
		return
	}
	hint := "Denied — stopped."
	if item.Status == "approved" {
		if resumed {
			hint = "Approved — Remedy is continuing with the approved action."
		} else {
			hint = "Approved — the same action can run now without asking again this session."
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":   item.Status,
		"approval": s.approvals.ToPublic(item),
		"hint":     hint,
		"resumed":  resumed,
	})
}
