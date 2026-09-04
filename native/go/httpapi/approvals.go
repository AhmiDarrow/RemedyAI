package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
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

type approvalQueue struct {
	mu           sync.Mutex
	items        map[string]*pendingApproval
	approvedFPs  map[string]struct{}
	sessionFPs   map[string]map[string]struct{}
	sessionOrder []string
	oneShot      map[string]map[string]string // sid -> fp -> origin
	mode         string
}

func newApprovalQueue() *approvalQueue {
	return &approvalQueue{
		items:       map[string]*pendingApproval{},
		approvedFPs: map[string]struct{}{},
		sessionFPs:  map[string]map[string]struct{}{},
		oneShot:     map[string]map[string]string{},
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
	if scope != "session" && scope != "always" {
		scope = "session"
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	item := q.items[id]
	if item == nil {
		return nil
	}
	if item.Status != "pending" {
		cp := *item
		return &cp
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
	cp := *item
	return &cp
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
	if tool == "bash_exec" {
		if len(cmd) > 120 {
			cmd = cmd[:120]
		}
		return "Remedy wants to run a command: " + cmd
	}
	if tool == "file_write" || tool == "file_edit" {
		if len(cmd) > 120 {
			cmd = cmd[:120]
		}
		return "Remedy wants to change a file: " + cmd
	}
	if tool == "mail_send" {
		return "Remedy wants to send an email."
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
	item := s.approvals.Resolve(id, req.Approve, req.Scope)
	if item == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Approval not found"})
		return
	}
	hint := "Denied — do not run the command."
	if item.Status == "approved" {
		hint = "Approved — ask Remedy to retry the same command."
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":   item.Status,
		"approval": s.approvals.ToPublic(item),
		"hint":     hint,
	})
}
