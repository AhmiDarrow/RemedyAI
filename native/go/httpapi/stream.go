package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const (
	stopNote = "*(Generation stopped. History is intact — send **continue** to resume.)*"
	supersedeNote = "*(Interrupted by your next message — partial work above is saved. " +
		"Say **continue** to pick it up.)*"
	sseKeepaliveInterval = 12 * time.Second
	sseKeepaliveComment  = ": keepalive\n\n"
	streamQueueSize      = 512
)

func interruptedTurnNote(reason string) string {
	if normalizeAbortReason(reason) == abortReasonSupersede {
		return supersedeNote
	}
	return stopNote
}

func interruptedTurnContent(partial, reason string, hasTools bool) string {
	body := strings.TrimSpace(partial)
	note := interruptedTurnNote(reason)
	if body == "" {
		if hasTools {
			return "*(Used tools — see process.)*\n\n" + note
		}
		return note
	}
	return body + "\n\n" + note
}

func sseFrame(event string, payload any) string {
	b, err := json.Marshal(payload)
	if err != nil {
		b = []byte(`{"type":"error","message":"encode failed"}`)
		event = "error"
	}
	return fmt.Sprintf("event: %s\ndata: %s\n\n", event, b)
}

func sseTextFrame(event, text string) string {
	return sseFrame(event, map[string]any{"type": event, "text": text})
}

func parseToolCallToken(token string) map[string]any {
	raw := token[len("@@tool_call:"):]
	name := raw
	args := map[string]any{}
	trimmed := strings.TrimSpace(raw)
	if strings.HasPrefix(trimmed, "{") {
		var obj map[string]any
		if err := json.Unmarshal([]byte(raw), &obj); err == nil {
			if n, ok := obj["name"].(string); ok && n != "" {
				name = n
			} else {
				name = "tool"
			}
			if a, ok := obj["args"].(map[string]any); ok {
				args = a
			}
			return map[string]any{"name": name, "args": args}
		}
	}
	if i := strings.Index(raw, "|"); i >= 0 {
		name = strings.TrimSpace(raw[:i])
	} else {
		name = strings.TrimSpace(raw)
	}
	if name == "" {
		name = "tool"
	}
	return map[string]any{"name": name, "args": args}
}

func parseToolResultToken(token string) (name, preview string, ok bool) {
	raw := token[len("@@tool_result:"):]
	name = "tool"
	preview = ""
	ok = true
	trimmed := strings.TrimSpace(raw)
	if strings.HasPrefix(trimmed, "{") {
		var obj map[string]any
		if err := json.Unmarshal([]byte(raw), &obj); err == nil {
			if n, _ := obj["name"].(string); n != "" {
				name = n
			}
			if p, _ := obj["preview"].(string); p != "" {
				preview = p
			}
			if v, has := obj["ok"]; has {
				ok = asBool(v)
			}
			return name, preview, ok
		}
	}
	if i := strings.Index(raw, "|"); i >= 0 {
		name = strings.TrimSpace(raw[:i])
	} else {
		name = strings.TrimSpace(raw)
	}
	if name == "" {
		name = "tool"
	}
	return name, preview, ok
}

func asBool(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case float64:
		return t != 0
	case string:
		low := strings.ToLower(strings.TrimSpace(t))
		return low == "1" || low == "true" || low == "yes" || low == "on"
	default:
		return true
	}
}

func redactStreamError(err error) string {
	if err == nil {
		return "Stream error"
	}
	msg := strings.TrimSpace(err.Error())
	if msg == "" {
		return "Stream error"
	}
	if len(msg) > 800 {
		msg = msg[:800]
	}
	return msg
}

func (s *Server) handleStreamMessage(w http.ResponseWriter, r *http.Request) {
	if s.runner == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Runtime not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	var req sendMessageRequest
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil && !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	userText := strings.TrimSpace(req.Message)
	hasAtts := len(req.Attachments) > 0
	if userText == "" && !hasAtts {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Message or attachment required"})
		return
	}

	// Claim *before* persisting the user message (Python stream.py).
	claimEpoch, claimCtx, claimed := s.claims.TryClaim(sid)
	if !claimed {
		writeJSON(w, http.StatusConflict, map[string]string{"detail": sessionBusyDetail})
		return
	}
	handedOff := false
	defer func() {
		if !handedOff {
			s.claims.Release(sid, &claimEpoch)
		}
	}()

	requestID := newSessionID()
	home := s.homeDir
	attDicts := filterJailedAttachments(req.Attachments, home, sid)
	displayContent := userText
	if len(attDicts) > 0 {
		displayContent = strings.TrimSpace(userText + buildAttachmentPromptBlock(attDicts))
		displayContent += injectTextFileSnippets(attDicts, home, sid)
	}
	if displayContent == "" {
		displayContent = "(see attached files)"
	}
	prompt := userText
	if prompt == "" {
		prompt = "(see attached files)"
	}

	var sessProvider, sessModel *string
	if s.sessions != nil {
		sess, ok, err := s.sessions.Get(sid)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
			return
		}
		sp, sm := resolveSessionLLMBind(sess.LLMProvider, sess.Model, req.Provider, req.Model)
		if p, m, has := sessionLLMUpdateFields(sp, sm); has {
			if err := s.sessions.UpdateLLMBind(sid, p, m); err != nil {
				writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
				return
			}
		}
		if title, refresh := maybeAutoTitle(sess, userText, attDicts); refresh {
			_ = s.sessions.SetTitle(sid, title)
		}
		ex, _, _ := s.sessions.Get(sid)
		if ex.ID != "" {
			sessProvider, sessModel = resolveSessionLLMBind(ex.LLMProvider, ex.Model, req.Provider, req.Model)
		} else {
			sessProvider, sessModel = sp, sm
		}
		if _, err := s.sessions.AddMessage(sid, "user", displayContent, nil, nil); err != nil {
			if _, ok, _ := s.sessions.Get(sid); !ok {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
				return
			}
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
	} else {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "streaming unsupported"})
		return
	}

	frames := make(chan string, streamQueueSize)
	turnReq := TurnRequest{
		SessionID:   sid,
		Prompt:      prompt,
		Model:       sessModel,
		Provider:    sessProvider,
		PlanMode:    req.PlanMode,
		ChatMode:    req.ChatMode,
		Attachments: attDicts,
		DrainNudges: func() []string {
			if s.claims == nil {
				return nil
			}
			return s.claims.DrainNudges(sid)
		},
	}
	go s.runDetachedStream(sid, claimEpoch, claimCtx, requestID, turnReq, frames)
	handedOff = true

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	keepalive := time.NewTicker(sseKeepaliveInterval)
	defer keepalive.Stop()

	// Client disconnect must NOT cancel the turn — only POST /abort does.
	for {
		select {
		case <-r.Context().Done():
			return
		case <-keepalive.C:
			if _, err := io.WriteString(w, sseKeepaliveComment); err != nil {
				return
			}
			flusher.Flush()
		case frame, open := <-frames:
			if !open {
				return
			}
			if _, err := io.WriteString(w, frame); err != nil {
				return
			}
			flusher.Flush()
			keepalive.Reset(sseKeepaliveInterval)
		}
	}
}

func (s *Server) enqueueFrame(ch chan string, frame string) {
	select {
	case ch <- frame:
	default:
		select {
		case <-ch:
		default:
		}
		select {
		case ch <- frame:
		default:
		}
	}
}

func (s *Server) runDetachedStream(
	sid string,
	claimEpoch int,
	ctx context.Context,
	requestID string,
	req TurnRequest,
	frames chan string,
) {
	s.claims.BeginTurn(sid)
	defer s.claims.EndTurn(sid)
	defer close(frames)
	defer s.claims.Release(sid, &claimEpoch)

	s.enqueueFrame(frames, sseFrame("start", map[string]any{
		"type":         "start",
		"request_id":   requestID,
		"session_id":   sid,
		"claim_epoch":  claimEpoch,
	}))

	if ctx == nil {
		// Should never happen after TryClaim — fail closed instead of Background.
		cancelled, cancel := context.WithCancel(context.Background())
		cancel()
		ctx = cancelled
	}

	var (
		fullResponse          strings.Builder
		fullThinking          strings.Builder
		thinkingReplaceNext   bool
		collectedToolCalls    []map[string]any
		collectedToolResults  []map[string]any
		aborted               bool
		persistDone           bool
		bodyFinished          bool
	)

	persistInterrupted := func(kind string) {
		_ = kind
		if persistDone || s.sessions == nil {
			return
		}
		persistDone = true
		reason := s.claims.PeekAbortReason(sid)
		calls := append([]map[string]any(nil), collectedToolCalls...)
		results := append([]map[string]any(nil), collectedToolResults...)
		content := interruptedTurnContent(fullResponse.String(), reason, len(calls) > 0 || len(results) > 0)
		var thinking *string
		if t := strings.TrimSpace(fullThinking.String()); t != "" {
			thinking = &t
		}
		if _, ok, _ := s.sessions.Get(sid); !ok {
			return
		}
		_, _ = s.sessions.AddMessageFull(
			sid, "assistant", content, thinking, calls, results, req.Model, nil, nil,
		)
	}

	emit := func(token string) error {
		if token == "" {
			return nil
		}
		if strings.HasPrefix(token, "@@aborted") {
			aborted = true
			persistInterrupted("aborted")
			s.enqueueFrame(frames, sseFrame("aborted", map[string]any{
				"type":       "aborted",
				"message":    "Generation stopped",
				"request_id": requestID,
			}))
			return errStreamAborted
		}
		switch {
		case strings.HasPrefix(token, "@@tool_call:"):
			parsed := parseToolCallToken(token)
			collectedToolCalls = append(collectedToolCalls, parsed)
			s.enqueueFrame(frames, sseFrame("tool_call", map[string]any{
				"type": "tool_call",
				"name": parsed["name"],
				"args": parsed["args"],
			}))
		case strings.HasPrefix(token, "@@tool_result:"):
			name, preview, ok := parseToolResultToken(token)
			item := map[string]any{
				"name":   name,
				"output": preview,
				"error":  nil,
			}
			if !ok {
				errMsg := preview
				if errMsg == "" {
					errMsg = "tool failed"
				}
				item["error"] = errMsg
			}
			collectedToolResults = append(collectedToolResults, item)
			s.enqueueFrame(frames, sseFrame("tool_result", map[string]any{
				"type":    "tool_result",
				"name":    name,
				"preview": preview,
				"ok":      ok,
			}))
		case strings.HasPrefix(token, "@@progress:"):
			raw := token[len("@@progress:"):]
			payload := map[string]any{"label": raw}
			if strings.TrimSpace(raw) != "" && strings.HasPrefix(strings.TrimSpace(raw), "{") {
				var obj map[string]any
				if err := json.Unmarshal([]byte(raw), &obj); err == nil {
					payload = obj
				}
			}
			payload["type"] = "progress"
			s.enqueueFrame(frames, sseFrame("progress", payload))
		case strings.HasPrefix(token, "@@status:"):
			label := strings.TrimSpace(token[len("@@status:"):])
			if label != "" {
				s.enqueueFrame(frames, sseFrame("progress", map[string]any{
					"type":  "progress",
					"label": label,
				}))
			}
		case strings.HasPrefix(token, "@@steered"):
			s.enqueueFrame(frames, sseFrame("progress", map[string]any{
				"type":  "progress",
				"label": "Taking that in…",
			}))
		case strings.HasPrefix(token, "@@thinking_round"):
			thinkingReplaceNext = true
		case strings.HasPrefix(token, "@@thinking:"):
			thought := token[len("@@thinking:"):]
			if thought == "" {
				return nil
			}
			if thinkingReplaceNext {
				thinkingReplaceNext = false
				fullThinking.Reset()
				fullThinking.WriteString(thought)
				s.enqueueFrame(frames, sseFrame("thinking", map[string]any{
					"type":    "thinking",
					"text":    thought,
					"replace": true,
				}))
			} else {
				fullThinking.WriteString(thought)
				s.enqueueFrame(frames, sseTextFrame("thinking", thought))
			}
		case strings.HasPrefix(token, "@@usage:"):
			raw := token[len("@@usage:"):]
			var part map[string]any
			if err := json.Unmarshal([]byte(raw), &part); err == nil && part != nil {
				part["type"] = "usage"
				s.enqueueFrame(frames, sseFrame("usage", part))
			}
		case strings.HasPrefix(token, "@@library_suggest:"):
			raw := strings.TrimSpace(token[len("@@library_suggest:"):])
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload["id"] != nil {
				payload["type"] = "library_suggest"
				s.enqueueFrame(frames, sseFrame("library_suggest", payload))
			}
		case strings.HasPrefix(token, "@@life_task:"):
			raw := token[len("@@life_task:"):]
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload != nil {
				payload["type"] = "life_task"
				s.enqueueFrame(frames, sseFrame("life_task", payload))
			}
		case strings.HasPrefix(token, "@@todos:"):
			raw := token[len("@@todos:"):]
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload != nil {
				payload["type"] = "todos"
				s.enqueueFrame(frames, sseFrame("todos", payload))
			}
		case token == "@@tool_calls":
			// no-op marker
		case strings.HasPrefix(token, "@@"):
			// Unknown control token — never leak into the bubble.
		default:
			fullResponse.WriteString(token)
			s.enqueueFrame(frames, sseTextFrame("token", token))
		}
		return nil
	}

	turnErr := s.runner.RunTurn(ctx, req, emit)
	if errors.Is(turnErr, errStreamAborted) {
		turnErr = nil
		aborted = true
	}
	if turnErr != nil && (errors.Is(turnErr, context.Canceled) || errors.Is(turnErr, context.DeadlineExceeded)) {
		aborted = true
		turnErr = nil
	}
	if aborted && !persistDone {
		persistInterrupted("aborted-fallback")
		s.enqueueFrame(frames, sseFrame("aborted", map[string]any{
			"type":       "aborted",
			"message":    "Generation stopped",
			"request_id": requestID,
		}))
	}

	if turnErr != nil {
		bodyFinished = true
		persistDone = true
		safe := redactStreamError(turnErr)
		s.enqueueFrame(frames, sseFrame("error", map[string]any{
			"type":    "error",
			"message": safe,
		}))
		if s.sessions != nil {
			if _, ok, _ := s.sessions.Get(sid); ok {
				note := ""
				if body := strings.TrimSpace(fullResponse.String()); body != "" {
					note = body + "\n\n"
				}
				note += "*(Turn ended with an error: " + safe + ". History is intact — send **continue** to resume.)*"
				_, _ = s.sessions.AddMessage(sid, "assistant", note, req.Model, nil)
			}
		}
		return
	}

	persistText := strings.TrimSpace(fullResponse.String())
	hasTools := len(collectedToolCalls) > 0 || len(collectedToolResults) > 0
	if aborted && persistText == "" && !persistDone {
		persistText = stopNote
		if normalizeAbortReason(s.claims.PeekAbortReason(sid)) == abortReasonSupersede {
			persistText = supersedeNote
		}
	}
	if persistText == "" && hasTools {
		persistText = "*(Used tools — see process.)*"
	}
	if persistText != "" && s.sessions != nil && !persistDone {
		if _, ok, _ := s.sessions.Get(sid); ok {
			var thinking *string
			if t := strings.TrimSpace(fullThinking.String()); t != "" {
				thinking = &t
			}
			var calls any
			var results any
			if len(collectedToolCalls) > 0 {
				calls = collectedToolCalls
			} else {
				calls = []any{}
			}
			if len(collectedToolResults) > 0 {
				results = collectedToolResults
			} else {
				results = []any{}
			}
			_, _ = s.sessions.AddMessageFull(
				sid, "assistant", persistText, thinking, calls, results, req.Model, nil, nil,
			)
			persistDone = true
			if sess, ok, _ := s.sessions.Get(sid); ok && sess.OriginChannel != nil && *sess.OriginChannel != "" {
				title := sess.Title
				count := sess.MessageCount
				role := "assistant"
				s.publishSessionEvent(SessionEvent{
					Type:          "message_added",
					SessionID:     sid,
					OriginChannel: sess.OriginChannel,
					Title:         &title,
					MessageCount:  &count,
					Role:          &role,
				})
				s.mirrorDesktopReply(sess, persistText)
			}
		} else {
			persistDone = true
		}
	}

	status := "ok"
	if aborted {
		status = "aborted"
	}
	bodyFinished = true
	_ = bodyFinished
	s.enqueueFrame(frames, sseFrame("done", map[string]any{
		"type":       "done",
		"request_id": requestID,
		"status":     status,
	}))
}

var errStreamAborted = errors.New("stream aborted")
