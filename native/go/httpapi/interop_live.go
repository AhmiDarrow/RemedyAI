package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

type interopLiveCtl struct {
	mu          sync.Mutex
	active      int
	wait        map[string][]chan struct{}
	cancels     map[string]context.CancelFunc
	ttlOverride time.Duration // tests only
}

func newInteropLiveCtl() *interopLiveCtl {
	return &interopLiveCtl{
		wait:    map[string][]chan struct{}{},
		cancels: map[string]context.CancelFunc{},
	}
}

func (s *Server) liveCtl() *interopLiveCtl {
	if s == nil {
		return nil
	}
	if s.interopLive == nil {
		s.interopLive = newInteropLiveCtl()
	}
	return s.interopLive
}

func (s *Server) interopAutoReplyOn() bool {
	if s == nil || !s.interopInboundOn() {
		return false
	}
	return coerceBool(resolveAdvanced(advancedByKey()["interop_auto_reply"], s.interopCfg()), true)
}

func (s *Server) interopMaxConcurrent() int {
	n := resolveAdvancedInt(s.interopCfg(), "interop_max_concurrent")
	if n <= 0 {
		return 2
	}
	return n
}

func (s *Server) interopTurnTimeout() time.Duration {
	if ctl := s.liveCtl(); ctl != nil && ctl.ttlOverride > 0 {
		return ctl.ttlOverride
	}
	d := resolveAdvancedSeconds(s.interopCfg(), "interop_task_ttl_s")
	if d <= 0 {
		d = 24 * time.Hour
	}
	return d
}

func (s *Server) interopShouldLive(task map[string]any) bool {
	if task == nil || !s.interopAutoReplyOn() || s.runner == nil || s.sessions == nil {
		return false
	}
	meta, _ := task["metadata"].(map[string]any)
	action := interopNormalizeAction(stringVal(meta, "action"))
	if action != "ask" && action != "delegate" {
		return false
	}
	switch statusState(task) {
	case "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING":
		return true
	}
	return false
}

func (s *Server) acquireInteropSlot(ctx context.Context) bool {
	ctl := s.liveCtl()
	for {
		ctl.mu.Lock()
		if ctl.active < s.interopMaxConcurrent() {
			ctl.active++
			ctl.mu.Unlock()
			return true
		}
		ctl.mu.Unlock()
		select {
		case <-ctx.Done():
			return false
		case <-time.After(25 * time.Millisecond):
		}
	}
}

func (s *Server) releaseInteropSlot() {
	ctl := s.liveCtl()
	ctl.mu.Lock()
	if ctl.active > 0 {
		ctl.active--
	}
	ctl.mu.Unlock()
}

func (s *Server) interopRegisterCancel(id string, cancel context.CancelFunc) {
	ctl := s.liveCtl()
	ctl.mu.Lock()
	ctl.cancels[id] = cancel
	ctl.mu.Unlock()
}

func (s *Server) interopUnregisterCancel(id string) {
	ctl := s.liveCtl()
	ctl.mu.Lock()
	delete(ctl.cancels, id)
	ctl.mu.Unlock()
}

func (s *Server) interopCancelLive(id string) {
	ctl := s.liveCtl()
	ctl.mu.Lock()
	cancel := ctl.cancels[id]
	ctl.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func (s *Server) interopNotify(id string) {
	ctl := s.liveCtl()
	ctl.mu.Lock()
	waiters := ctl.wait[id]
	delete(ctl.wait, id)
	ctl.mu.Unlock()
	for _, ch := range waiters {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

func (s *Server) interopSubscribe(id string) chan struct{} {
	ctl := s.liveCtl()
	ch := make(chan struct{}, 1)
	ctl.mu.Lock()
	ctl.wait[id] = append(ctl.wait[id], ch)
	ctl.mu.Unlock()
	return ch
}

func a2aAgentMessage(text string) map[string]any {
	return map[string]any{
		"messageId": uuid.NewString(),
		"role":      "agent",
		"parts":     []any{map[string]any{"kind": "text", "text": text}},
	}
}

func (s *Server) interopPatchTask(id string, fn func(task map[string]any)) map[string]any {
	if id == "" {
		return nil
	}
	interopMu.Lock()
	store := s.loadInteropTasks()
	task, ok := store[id]
	if !ok {
		interopMu.Unlock()
		return nil
	}
	fn(task)
	store[id] = task
	s.saveInteropTasks(store)
	// copy so callers can use the map after the unlock
	out := task
	interopMu.Unlock()
	s.interopNotify(id)
	return out
}

func (s *Server) interopSetState(id, state, text string, artifact map[string]any, history bool) map[string]any {
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	return s.interopPatchTask(id, func(task map[string]any) {
		status := map[string]any{"state": state, "timestamp": now}
		if text != "" {
			status["message"] = a2aAgentMessage(text)
		}
		task["status"] = status
		if artifact != nil {
			arts, _ := task["artifacts"].([]any)
			task["artifacts"] = append(arts, artifact)
		}
		if history && text != "" {
			hist, _ := task["history"].([]any)
			task["history"] = append(hist, a2aAgentMessage(text))
		}
	})
}

func interopUserText(task map[string]any) string {
	if task == nil {
		return ""
	}
	if hist, ok := task["history"].([]any); ok {
		for i := len(hist) - 1; i >= 0; i-- {
			msg, _ := hist[i].(map[string]any)
			if msg == nil {
				continue
			}
			role, _ := msg["role"].(string)
			if role != "user" && role != "" {
				continue
			}
			if t := extractA2AText(msg); t != "" {
				return t
			}
		}
	}
	if st, ok := task["status"].(map[string]any); ok {
		if msg, ok := st["message"].(map[string]any); ok {
			return extractA2AText(msg)
		}
	}
	return ""
}

// interopProtocol reports a channel name peer turns may use.
func interopProtocol(ch string) bool {
	return ch == "a2a" || ch == "mcp"
}

func (s *Server) interopSession(protocol, peerID, contextID string) (ChatSession, error) {
	if s.sessions == nil {
		return ChatSession{}, fmt.Errorf("no session store")
	}
	ch := strings.ToLower(strings.TrimSpace(protocol))
	if ch == "" {
		ch = "a2a"
	}
	// Peer turns live only in interop sessions. The protocol can come from a
	// request body, and any other channel name ("telegram", "desktop") would
	// have joined that channel's conversation and its history.
	if !interopProtocol(ch) {
		return ChatSession{}, fmt.Errorf("unknown interop protocol %q", protocol)
	}
	ext := strings.TrimSpace(peerID)
	if contextID = strings.TrimSpace(contextID); contextID != "" {
		if ext != "" {
			ext = ext + ":" + contextID
		} else {
			ext = contextID
		}
	}
	if ext == "" {
		ext = "unknown"
	}
	if found, ok, err := s.sessions.FindByOrigin(ch, ext); err != nil {
		return ChatSession{}, err
	} else if ok {
		return found, nil
	}
	title := "Peer"
	if peerID != "" {
		title = "Peer · " + peerID
	}
	var user *string
	if peerID != "" {
		p := peerID
		user = &p
	}
	return s.sessions.CreateMessenger("", title, ch, ext, user)
}

func (s *Server) waitInteropClaim(ctx context.Context, sid string) (int, context.Context, bool) {
	for {
		epoch, claimCtx, claimed := s.claims.TryClaim(sid)
		if claimed {
			return epoch, claimCtx, true
		}
		select {
		case <-ctx.Done():
			return 0, nil, false
		case <-time.After(25 * time.Millisecond):
		}
	}
}

func interopStatusEvent(taskID, contextID, state, text string, final bool) map[string]any {
	status := map[string]any{"state": state, "timestamp": time.Now().UTC().Format("2006-01-02T15:04:05.000Z")}
	if text != "" {
		status["message"] = a2aAgentMessage(text)
	}
	return map[string]any{
		"kind":      "status-update",
		"taskId":    taskID,
		"contextId": contextID,
		"status":    status,
		"final":     final,
	}
}

func interopArtifactEvent(taskID, contextID string, artifact map[string]any) map[string]any {
	return map[string]any{
		"kind":      "artifact-update",
		"taskId":    taskID,
		"contextId": contextID,
		"artifact":  artifact,
		"lastChunk": true,
	}
}

func interopTaskEvent(task map[string]any) map[string]any {
	return map[string]any{"kind": "task", "task": task}
}

// interopRunLive starts a real untrusted session turn for an allowed inbound
// task. Timeouts follow interop_task_ttl_s (or a test override). Concurrency
// is capped by interop_max_concurrent. Money, send, and delete never reach
// here — those stay INPUT_REQUIRED from consent.
func (s *Server) interopRunLive(ctx context.Context, protocol string, task map[string]any, emit func(map[string]any)) map[string]any {
	if task == nil {
		return nil
	}
	id, _ := task["id"].(string)
	contextID, _ := task["contextId"].(string)
	meta, _ := task["metadata"].(map[string]any)
	peerID := stringVal(meta, "peer_id")
	action := interopNormalizeAction(stringVal(meta, "action"))
	text := interopUserText(task)
	if text == "" {
		return s.interopSetState(id, "TASK_STATE_FAILED", "empty inbound prompt", nil, true)
	}
	if ctx == nil {
		ctx = context.Background()
	}
	ttl := s.interopTurnTimeout()
	runCtx, cancel := context.WithTimeout(ctx, ttl)
	defer cancel()
	s.interopRegisterCancel(id, cancel)
	defer s.interopUnregisterCancel(id)

	task = s.interopSetState(id, "TASK_STATE_WORKING", "", nil, false)
	s.interopPatchTask(id, func(t map[string]any) {
		md, _ := t["metadata"].(map[string]any)
		if md == nil {
			md = map[string]any{}
		}
		md["live"] = true
		t["metadata"] = md
	})
	if emit != nil {
		emit(interopTaskEvent(task))
		emit(interopStatusEvent(id, contextID, "TASK_STATE_WORKING", "", false))
	}

	fail := func(state, msg string, extra map[string]any) map[string]any {
		out := s.interopSetState(id, state, msg, nil, true)
		auditExtra := map[string]any{"task_id": id, "failed": state != "TASK_STATE_CANCELED", "state": state}
		for k, v := range extra {
			auditExtra[k] = v
		}
		s.appendInteropAuditExtra(protocol, "out", peerID, action, msg, auditExtra)
		if emit != nil {
			emit(interopStatusEvent(id, contextID, state, msg, true))
		}
		return out
	}

	if !s.acquireInteropSlot(runCtx) {
		state := "TASK_STATE_FAILED"
		msg := "live reply timed out waiting for a free slot"
		if errors.Is(runCtx.Err(), context.Canceled) && !errors.Is(runCtx.Err(), context.DeadlineExceeded) {
			// distinguish: WithTimeout wraps cancel as DeadlineExceeded or Canceled
		}
		if runCtx.Err() == context.Canceled {
			state = "TASK_STATE_CANCELED"
			msg = "canceled"
		} else if runCtx.Err() == context.DeadlineExceeded {
			msg = "live reply timed out waiting for a free slot"
		}
		return fail(state, msg, nil)
	}
	defer s.releaseInteropSlot()

	sess, err := s.interopSession(protocol, peerID, contextID)
	if err != nil {
		return fail("TASK_STATE_FAILED", "could not open a session: "+err.Error(), nil)
	}
	s.interopPatchTask(id, func(t map[string]any) {
		md, _ := t["metadata"].(map[string]any)
		if md == nil {
			md = map[string]any{}
		}
		md["session_id"] = sess.ID
		t["metadata"] = md
	})

	var history []map[string]any
	if s.sessions != nil {
		history = s.turnHistory(sess.ID)
		if _, err := s.sessions.AddMessage(sess.ID, "user", text, nil, nil); err != nil {
			return fail("TASK_STATE_FAILED", "could not record the inbound message", nil)
		}
	}

	epoch, claimCtx, claimed := s.waitInteropClaim(runCtx, sess.ID)
	if !claimed {
		if runCtx.Err() == context.Canceled {
			return fail("TASK_STATE_CANCELED", "canceled", nil)
		}
		return fail("TASK_STATE_FAILED", "live reply timed out waiting for the session", nil)
	}
	defer s.claims.Release(sess.ID, &epoch)
	if claimCtx != nil {
		var stop context.CancelFunc
		runCtx, stop = mergeCancel(runCtx, claimCtx)
		defer stop()
	}

	origin := "interop:" + protocol
	if peerID != "" {
		origin += ":" + peerID
	}

	projectPath := ""
	if sess.ProjectPath != nil {
		projectPath = effectiveTurnProjectPath(*sess.ProjectPath)
	}

	var reply strings.Builder
	var lastEmit time.Time
	turnErr := s.runner.RunTurn(runCtx, TurnRequest{
		SessionID:   sess.ID,
		Prompt:      text,
		Model:       sess.Model,
		Provider:    sess.LLMProvider,
		ProjectPath: projectPath,
		Origin:      origin,
		History:     history,
	}, func(token string) error {
		if plain, ok := modelTextToken(token); ok {
			token = plain
		} else if strings.HasPrefix(token, "@@") {
			return nil
		}
		reply.WriteString(token)
		if emit != nil && time.Since(lastEmit) >= 200*time.Millisecond {
			lastEmit = time.Now()
			emit(interopStatusEvent(id, contextID, "TASK_STATE_WORKING", reply.String(), false))
		}
		return nil
	})

	outText := strings.TrimSpace(reply.String())
	pending := false
	if s.approvals != nil && sess.ID != "" {
		if items := s.approvals.ListPending(sess.ID); len(items) > 0 {
			pending = true
		}
	}

	state := "TASK_STATE_COMPLETED"
	if pending {
		state = "TASK_STATE_INPUT_REQUIRED"
		if outText == "" {
			outText = "Waiting for the owner to approve a money, send, or delete action."
		}
	} else if turnErr != nil && outText == "" {
		if errors.Is(turnErr, context.DeadlineExceeded) || runCtx.Err() == context.DeadlineExceeded {
			state = "TASK_STATE_FAILED"
			outText = "live reply timed out"
		} else if errors.Is(turnErr, context.Canceled) || runCtx.Err() == context.Canceled {
			state = "TASK_STATE_CANCELED"
			outText = "canceled"
		} else {
			state = "TASK_STATE_FAILED"
			outText = "live reply failed"
		}
	} else if turnErr != nil && !pending {
		// Partial text + error: still surface the text as a failed-or-complete?
		// Prefer COMPLETED if we have a usable reply; otherwise FAILED.
		if outText == "" {
			state = "TASK_STATE_FAILED"
			outText = "live reply failed"
		}
	}
	if outText == "" && state == "TASK_STATE_COMPLETED" {
		outText = "I finished that step without a reply I can show here."
	}

	if s.sessions != nil && outText != "" {
		_, _ = s.sessions.AddMessage(sess.ID, "assistant", outText, sess.Model, nil)
	}

	artifact := map[string]any{
		"artifactId": uuid.NewString(),
		"name":       "reply",
		"parts":      []any{map[string]any{"kind": "text", "text": outText}},
	}
	final := s.interopSetState(id, state, outText, artifact, true)
	s.appendInteropAuditExtra(protocol, "out", peerID, action, outText, map[string]any{
		"task_id":    id,
		"live":       true,
		"state":      state,
		"session_id": sess.ID,
		"origin":     origin,
	})
	if emit != nil {
		emit(interopArtifactEvent(id, contextID, artifact))
		emit(interopStatusEvent(id, contextID, state, outText, true))
	}
	return final
}

func mergeCancel(parent, other context.Context) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(parent)
	go func() {
		select {
		case <-other.Done():
			cancel()
		case <-ctx.Done():
		}
	}()
	return ctx, cancel
}

func interopBlocking(params map[string]any) bool {
	cfg, _ := params["configuration"].(map[string]any)
	if cfg == nil {
		return true
	}
	if v, ok := cfg["blocking"].(bool); ok {
		return v
	}
	if v, ok := cfg["return_immediately"].(bool); ok {
		return !v
	}
	return true
}

func (s *Server) writeA2ASSE(w http.ResponseWriter, id any, result any) error {
	payload, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(w, "data: %s\n\n", payload)
	if flusher, ok := w.(http.Flusher); ok {
		flusher.Flush()
	}
	return err
}

func (s *Server) interopStreamLive(w http.ResponseWriter, r *http.Request, rpcID any, protocol string, task map[string]any) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	if flusher, ok := w.(http.Flusher); ok {
		flusher.Flush()
	}
	emit := func(ev map[string]any) {
		_ = s.writeA2ASSE(w, rpcID, ev)
	}
	if s.interopShouldLive(task) {
		s.interopRunLive(r.Context(), protocol, task, emit)
		return
	}
	emit(interopTaskEvent(task))
	emit(interopStatusEvent(stringVal(task, "id"), stringVal(task, "contextId"), statusState(task), "", true))
}

func (s *Server) interopSubscribeStream(w http.ResponseWriter, r *http.Request, rpcID any, task map[string]any) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	id := stringVal(task, "id")
	ctxID := stringVal(task, "contextId")
	_ = s.writeA2ASSE(w, rpcID, interopTaskEvent(task))
	if isInteropTerminal(statusState(task)) {
		_ = s.writeA2ASSE(w, rpcID, interopStatusEvent(id, ctxID, statusState(task), "", true))
		return
	}
	ch := s.interopSubscribe(id)
	defer func() {
		// leave the channel; notify is non-blocking
	}()
	timer := time.NewTimer(s.interopTurnTimeout())
	defer timer.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-timer.C:
			fresh := s.interopGetTask(id)
			if fresh != nil {
				_ = s.writeA2ASSE(w, rpcID, interopStatusEvent(id, ctxID, statusState(fresh), "", isInteropTerminal(statusState(fresh))))
			}
			return
		case <-ch:
			fresh := s.interopGetTask(id)
			if fresh == nil {
				return
			}
			st := statusState(fresh)
			_ = s.writeA2ASSE(w, rpcID, interopStatusEvent(id, ctxID, st, "", isInteropTerminal(st)))
			if isInteropTerminal(st) {
				return
			}
			ch = s.interopSubscribe(id)
		}
	}
}

func isInteropTerminal(state string) bool {
	switch state {
	case "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED":
		return true
	}
	return false
}

func interopMCPToolResult(task map[string]any) string {
	if task == nil {
		return interopMustJSON(map[string]any{"ok": false, "error": "no task"})
	}
	state := statusState(task)
	queued := state == "TASK_STATE_SUBMITTED" || state == "TASK_STATE_INPUT_REQUIRED" || state == "TASK_STATE_WORKING"
	text := ""
	if st, ok := task["status"].(map[string]any); ok {
		if msg, ok := st["message"].(map[string]any); ok {
			text = extractA2AText(msg)
		}
	}
	out := map[string]any{
		"ok":             state != "TASK_STATE_REJECTED" && state != "TASK_STATE_FAILED",
		"queued":         queued && state != "TASK_STATE_WORKING",
		"needs_approval": state == "TASK_STATE_INPUT_REQUIRED",
		"task_id":        task["id"],
		"state":          state,
	}
	if text != "" {
		out["text"] = text
	}
	if state == "TASK_STATE_WORKING" {
		out["queued"] = false
	}
	if state == "TASK_STATE_COMPLETED" {
		out["queued"] = false
	}
	return interopMustJSON(out)
}

func (s *Server) handleInteropTurn(w http.ResponseWriter, r *http.Request) {
	if !s.interopGate(w, r, "a2a", false) {
		return
	}
	if !loopbackAddr(r) {
		http.Error(w, `{"error":{"message":"internal turn is loopback-only"}}`, http.StatusForbidden)
		return
	}
	if s.runner == nil || s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "no live runtime"})
		return
	}
	if !s.interopAutoReplyOn() {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "auto-reply is off or inbound is disabled", "queued": true})
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "parse error"})
		return
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "parse error"})
		return
	}
	protocol := strings.ToLower(strings.TrimSpace(stringVal(body, "protocol")))
	if protocol == "" {
		protocol = "mcp"
	}
	if !interopProtocol(protocol) {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "protocol must be a2a or mcp"})
		return
	}
	text := stringVal(body, "text", "question", "prompt")
	peerID := stringVal(body, "peer_id")
	contextID := stringVal(body, "context_id", "contextId")
	action := interopNormalizeAction(stringVal(body, "action"))
	if action == "" {
		action = "ask"
	}
	if text == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": "text is required"})
		return
	}
	// The caller (Python stdio / MCP HTTP) already applied consent. This
	// loopback helper only runs the untrusted session turn and returns text.
	task := s.interopNewWorkingTask(protocol, action, text, peerID, contextID)
	task = s.interopRunLive(r.Context(), protocol, task, nil)
	textOut := ""
	if st, ok := task["status"].(map[string]any); ok {
		if msg, ok := st["message"].(map[string]any); ok {
			textOut = extractA2AText(msg)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      statusState(task) == "TASK_STATE_COMPLETED" || statusState(task) == "TASK_STATE_INPUT_REQUIRED",
		"queued":  false,
		"state":   statusState(task),
		"task_id": task["id"],
		"text":    textOut,
	})
}

func (s *Server) interopNewWorkingTask(protocol, action, text, peerID, contextID string) map[string]any {
	interopMu.Lock()
	defer interopMu.Unlock()
	store := s.loadInteropTasks()
	id := uuid.NewString()
	ctx := contextID
	if ctx == "" {
		ctx = uuid.NewString()
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	msg := a2aUserMessage(text)
	task := map[string]any{
		"id":        id,
		"contextId": ctx,
		"status":    map[string]any{"state": "TASK_STATE_SUBMITTED", "timestamp": now, "message": msg},
		"history":   []any{msg},
		"artifacts": []any{},
		"metadata": map[string]any{
			"peer_id":    peerID,
			"action":     action,
			"created_ts": float64(time.Now().Unix()),
			"reason":     "internal live turn",
			"internal":   true,
		},
	}
	store[id] = task
	s.saveInteropTasks(store)
	s.appendInteropAudit(protocol, "in", peerID, action, text, id)
	return task
}
