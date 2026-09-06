package httpapi

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

const sensitivePrefix = "Owner checkpoint"

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
}

type approvalResolveResult struct {
	approved bool
}

type approvalQueue struct {
	mu           sync.Mutex
	items        map[string]*pendingApproval
	approvedFPs  map[string]struct{}
	sessionFPs   map[string]map[string]struct{}
	sessionOrder []string
	oneShot      map[string]map[string]string // sid -> fp -> origin
	waiters      map[string]chan approvalResolveResult
	mode         string
}

func newApprovalQueue() *approvalQueue {
	return &approvalQueue{
		items:       map[string]*pendingApproval{},
		approvedFPs: map[string]struct{}{},
		sessionFPs:  map[string]map[string]struct{}{},
		oneShot:     map[string]map[string]string{},
		waiters:     map[string]chan approvalResolveResult{},
		mode:        "ask",
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

func shortHexID(n int) string {
	b := make([]byte, (n+1)/2)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)[:n]
}

func (q *approvalQueue) Mode() string {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.mode
}

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
			sid := "default"
			if item.SessionID != nil && *item.SessionID != "" {
				sid = *item.SessionID
			}
			q.addSessionFPLocked(sid, item.Fingerprint)
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

// Enqueue adds a pending approval (tool gate / tests).
func (q *approvalQueue) Enqueue(toolName, command, reason string, sessionID *string, summary string) *pendingApproval {
	item := &pendingApproval{
		ID:              shortHexID(12),
		ToolName:        toolName,
		Command:         command,
		Reason:          reason,
		SessionID:       sessionID,
		CreatedAt:       float64(time.Now().UnixNano()) / 1e9,
		Status:          "pending",
		Fingerprint:     approvalFingerprint(toolName, command),
		Sensitive:       strings.HasPrefix(reason, sensitivePrefix),
		SummaryOverride: strings.TrimSpace(summary),
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	q.items[item.ID] = item
	cutoff := float64(time.Now().Unix()) - 3600
	for k, v := range q.items {
		if v.CreatedAt < cutoff {
			delete(q.items, k)
		}
	}
	return item
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
	if approve {
		item.Status = "approved"
		sid := "default"
		if item.SessionID != nil && *item.SessionID != "" {
			sid = *item.SessionID
		}
		if item.Sensitive {
			if q.oneShot[sid] == nil {
				q.oneShot[sid] = map[string]string{}
			}
			q.oneShot[sid][item.Fingerprint] = item.Origin
		} else if scope == "always" {
			q.approvedFPs[item.Fingerprint] = struct{}{}
			for len(q.approvedFPs) > 1000 {
				for k := range q.approvedFPs {
					delete(q.approvedFPs, k)
					break
				}
			}
		} else {
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
// Already-resolved ids are checked without waiting. Context cancel unregisters
// waiters and returns ctx.Err().
func (q *approvalQueue) WaitAll(ctx context.Context, ids []string) (bool, error) {
	if q == nil {
		return false, errors.New("approval queue is nil")
	}
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
		id = strings.TrimSpace(id)
		if id == "" {
			continue
		}
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
	if ones, ok := q.oneShot[sid]; ok {
		if _, ok := ones[fp]; ok {
			delete(ones, fp) // one-shot consume
			return true
		}
	}
	return false
}

func plainApprovalSummary(item *pendingApproval) string {
	if s := strings.TrimSpace(item.SummaryOverride); s != "" {
		return s
	}
	tool := strings.TrimSpace(item.ToolName)
	cmd := strings.TrimSpace(item.Command)
	if tool == "life_drive" {
		first := strings.TrimSpace(strings.Split(cmd, "\n")[0])
		if strings.HasPrefix(strings.ToLower(first), "remedy will") {
			if strings.HasSuffix(first, "?") {
				return first
			}
			return strings.TrimRight(first, ".") + ". Yes, No, or Explain?"
		}
		return "Remedy wants to drive this computer through a plan. Yes, No, or Explain?"
	}
	if tool == "bash_exec" || tool == "shell.exec" {
		if len(cmd) > 120 {
			cmd = cmd[:120]
		}
		return "Remedy wants to run a command: " + cmd
	}
	if tool == "file_write" || tool == "file_edit" || tool == "workspace.write" || tool == "workspace.edit" {
		if len(cmd) > 120 {
			cmd = cmd[:120]
		}
		return "Remedy wants to change a file: " + cmd
	}
	if tool == "mail_send" || tool == "mail.send" {
		return "Remedy wants to send an email."
	}
	if tool == "computer.navigate" {
		if len(cmd) > 120 {
			cmd = cmd[:120]
		}
		return "Remedy wants to open a page in the Browser rail: " + cmd
	}
	if len(cmd) > 120 {
		cmd = cmd[:120]
	}
	return "Remedy wants to run " + tool + ": " + cmd
}

func (q *approvalQueue) ToPublic(item *pendingApproval) map[string]any {
	cmd := item.Command
	if len(cmd) > 500 {
		cmd = cmd[:500]
	}
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

func (s *Server) handleResolveApproval(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("approval_id"))
	var req struct {
		Approve bool   `json:"approve"`
		Scope   string `json:"scope"`
	}
	req.Approve = true
	req.Scope = "session"
	dec := json.NewDecoder(r.Body)
	_ = dec.Decode(&req)
	item, resumed := s.approvals.resolve(id, req.Approve, req.Scope)
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
