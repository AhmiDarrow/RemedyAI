package httpapi

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const (
	// Plain text with one bold word: bold inside italics drew stray asterisks.
	stopNote      = "(Generation stopped. History is intact. Send **continue** to resume.)"
	supersedeNote = "(Interrupted by your next message. The partial work above is saved. " +
		"Say **continue** to pick it up.)"
	sseKeepaliveInterval = 12 * time.Second
	sseKeepaliveComment  = ": keepalive\n\n"
	streamQueueSize      = 512

	// A draft assistant row is upserted at least this often while a turn is
	// producing text, so a crash mid-build leaves the partial answer in place.
	draftInterval = 3 * time.Second
	draftMinBytes = 2_048
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

// parseToolCallToken decodes a @@tool_call token into {name, args[, id]}.
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
			out := map[string]any{"name": name, "args": args}
			if id, _ := obj["id"].(string); id != "" {
				out["id"] = id
			}
			// via names the sub-agent a delegated call came from, so the trail
			// can show whose work it was rather than attributing it to Remedy.
			if via, _ := obj["via"].(string); via != "" {
				out["via"] = via
			}
			return out
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

// toolResultImage is an image block carried back by a tool result.
type toolResultImage struct {
	MediaType string
	Data      []byte
}

// toolResultToken is a decoded @@tool_result token. Preview is what the SSE
// frame carries; Output is the full body the turn log records.
type toolResultToken struct {
	ID      string
	Name    string
	Preview string
	Output  string
	Error   string
	OK      bool
	Images  []toolResultImage
	// Via names the sub-agent this result came from (a delegated mission), so
	// the trail can attribute the work rather than showing it as Remedy's own.
	Via string
}

// record is the persisted tool_results row shape. Output is the full body
// (not the short SSE preview) so a reloaded turn stays fully transparent.
func (t toolResultToken) record() map[string]any {
	body := firstNonEmpty(t.Output, t.Preview)
	item := map[string]any{"name": t.Name, "output": body, "error": nil}
	if t.Preview != "" && t.Preview != body {
		item["preview"] = t.Preview
	}
	if t.Via != "" {
		item["via"] = t.Via
	}
	if t.ID != "" {
		item["id"] = t.ID
	}
	if !t.OK {
		errMsg := firstNonEmpty(t.Error, t.Preview, t.Output)
		if strings.TrimSpace(errMsg) == "" {
			errMsg = "command failed with no stdout or stderr"
		}
		item["error"] = errMsg
	}
	return item
}

func parseToolResultToken(token string) toolResultToken {
	raw := token[len("@@tool_result:"):]
	res := toolResultToken{Name: "tool", OK: true}
	trimmed := strings.TrimSpace(raw)
	if strings.HasPrefix(trimmed, "{") {
		var obj map[string]any
		if err := json.Unmarshal([]byte(raw), &obj); err == nil {
			if n, _ := obj["name"].(string); n != "" {
				res.Name = n
			}
			if p, _ := obj["preview"].(string); p != "" {
				res.Preview = p
			}
			if v, has := obj["ok"]; has {
				res.OK = asBool(v)
			}
			res.Output, _ = obj["output"].(string)
			res.Error, _ = obj["error"].(string)
			res.ID, _ = obj["id"].(string)
			res.Images = parseToolResultImages(obj["blocks"])
			res.Via, _ = obj["via"].(string)
			return res
		}
	}
	if i := strings.Index(raw, "|"); i >= 0 {
		res.Name = strings.TrimSpace(raw[:i])
	} else {
		res.Name = strings.TrimSpace(raw)
	}
	if res.Name == "" {
		res.Name = "tool"
	}
	return res
}

// parseToolResultImages decodes the base64 image blocks of a tool result.
func parseToolResultImages(raw any) []toolResultImage {
	list, ok := raw.([]any)
	if !ok {
		return nil
	}
	out := make([]toolResultImage, 0, len(list))
	for _, entry := range list {
		obj, ok := entry.(map[string]any)
		if !ok {
			continue
		}
		if t, _ := obj["type"].(string); t != "" && t != "image" {
			continue
		}
		mediaType, _ := obj["media_type"].(string)
		encoded, _ := obj["data"].(string)
		if encoded == "" {
			continue
		}
		data, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil || len(data) == 0 {
			continue
		}
		if strings.TrimSpace(mediaType) == "" {
			mediaType = "image/png"
		}
		out = append(out, toolResultImage{MediaType: mediaType, Data: data})
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// modelTextToken unwraps the @@text: escape the turn runner applies to model
// output that begins with "@@". ok is false for every other token.
func modelTextToken(token string) (string, bool) {
	if strings.HasPrefix(token, "@@text:") {
		return token[len("@@text:"):], true
	}
	return "", false
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
	low := strings.ToLower(msg)
	switch {
	case strings.Contains(low, "budget exhausted") || strings.Contains(low, "step budget"):
		return "Step budget used up — send continue or spawn another pulse. History is intact."
	case strings.Contains(low, "safety stop") || strings.Contains(low, "safety ceiling") ||
		strings.Contains(low, "tool ceiling") || strings.Contains(low, "step ceiling") ||
		strings.Contains(low, "tool-call"):
		return "Safety stop after a stuck loop. History is intact — send continue to keep going."
	case strings.Contains(low, "no progress") || strings.Contains(low, "without progress") ||
		strings.Contains(low, "stuck repeating"):
		return "Stuck repeating the same steps — change approach, then send continue."
	case strings.Contains(low, "mid-reply") || strings.Contains(low, "incomplete model"):
		return "The model stopped mid-reply — send continue to resume."
	case strings.Contains(low, "context canceled") || strings.Contains(low, "context cancelled"):
		return "Stopped."
	case strings.Contains(low, "deadline exceeded"):
		return "That step took too long — send continue to retry from where you left off."
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
	s.NoteUserActivity()
	s.hearthTouch("desktop", time.Now())

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
	var history []map[string]any
	var userMsgID string
	projectPath := ""
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
		if sess.ProjectPath != nil {
			projectPath = effectiveTurnProjectPath(*sess.ProjectPath)
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
		history = s.turnHistory(sid)
		um, err := s.sessions.AddMessage(sid, "user", displayContent, nil, nil)
		userMsgID = um.ID
		if err != nil {
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

	sink := newFrameSink(requestID, streamQueueSize)
	turnReq := TurnRequest{
		SessionID:     sid,
		Prompt:        prompt,
		Model:         sessModel,
		Provider:      sessProvider,
		ProjectPath:   projectPath,
		PlanMode:      req.PlanMode,
		ChatMode:      req.ChatMode,
		Attachments:   attDicts,
		History:       history,
		UserMessageID: userMsgID,
		DrainNudges: func() []string {
			if s.claims == nil {
				return nil
			}
			return s.claims.DrainNudges(sid)
		},
	}
	go s.runDetachedStream(sid, claimEpoch, claimCtx, requestID, turnReq, sink)
	handedOff = true
	s.publishTurnStarted(sid, requestID, userMsgID)

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	keepalive := time.NewTicker(sseKeepaliveInterval)
	defer keepalive.Stop()

	// Client disconnect must NOT cancel the turn — only POST /abort does. The
	// turn keeps writing its log, and the client re-attaches with
	// GET /stream/attach?request_id=…&after=<last seq>.
	for {
		select {
		case <-r.Context().Done():
			return
		case <-keepalive.C:
			if _, err := io.WriteString(w, sseKeepaliveComment); err != nil {
				return
			}
			flusher.Flush()
		case frame, open := <-sink.ch:
			if !open {
				return
			}
			if _, err := io.WriteString(w, frame.Data); err != nil {
				return
			}
			flusher.Flush()
			keepalive.Reset(sseKeepaliveInterval)
		}
	}
}

// turnOrigin names where a turn came from for the start record.
func turnOrigin(req TurnRequest) string {
	if o := strings.TrimSpace(req.Origin); o != "" {
		return o
	}
	return "desktop"
}

func derefStr(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}

func (s *Server) runDetachedStream(
	sid string,
	claimEpoch int,
	ctx context.Context,
	requestID string,
	req TurnRequest,
	sink *frameSink,
) {
	s.claims.BeginTurn(sid)
	defer s.claims.EndTurn(sid)
	// Her mood follows the turn: it starts here and its outcome is kept when
	// it ends (after the claim is released, so the turn no longer counts).
	firstMessage := s.sessions != nil && s.sessions.countMessages(sid) <= 1
	s.moodNote(sid, func(p *moodPhase) { p.first = firstMessage })
	s.moodTyping(false) // what was being typed has been sent
	defer s.moodEnd(sid)
	defer sink.close()
	defer s.claims.Release(sid, &claimEpoch)

	s.claims.SetActiveRequest(sid, claimEpoch, requestID)

	turnLog, logErr := openTurnLog(s.homeDir, sid, requestID)
	defer turnLog.Close()
	// Turn logs carry every tool result and image, so they are pruned once per
	// turn rather than left to grow without limit under the owner's home.
	defer pruneTurnLogs(s.homeDir, sid, turnLogKeepPerSession, turnLogMaxAge, time.Now())
	live := s.turns.begin(sid, requestID)
	defer s.turns.end(live)

	ts := &turnStream{log: turnLog, live: live, sink: sink}
	// Approvals are resolved from the UI, outside this goroutine; register the
	// writer so those decisions land in this turn's evidence.
	s.turns.attachStream(sid, ts)
	defer s.turns.detachStream(sid, ts)

	ts.record(turnRecord{
		T:          "start",
		RequestID:  requestID,
		SessionID:  sid,
		ClaimEpoch: claimEpoch,
		Origin:     turnOrigin(req),
		Model:      derefStr(req.Model),
		Provider:   derefStr(req.Provider),
	})
	if logErr != nil {
		ts.signal("error", map[string]any{
			"message": "Turn evidence is not being recorded: " + logErr.Error(),
		})
	}

	if ctx == nil {
		// Should never happen after TryClaim — fail closed instead of Background.
		cancelled, cancel := context.WithCancel(context.Background())
		cancel()
		ctx = cancelled
	}

	var (
		fullResponse        strings.Builder
		fullThinking        strings.Builder
		thinkingReplaceNext bool
		collectedToolCalls  []map[string]any
		// fullResponse length at the last narration cut (see @@tool_call).
		narrationMark        int
		collectedToolResults []map[string]any
		// draftToolResults is the same list with each output cut to what a
		// reader is ever shown (toolCap). A draft is rewritten on every tool
		// event, so writing full outputs each time grew with the square of
		// the turn's length; the final row and the turn log keep them whole.
		draftToolResults []map[string]any
		aborted              bool
		persistDone          bool
		answeredModel        *string
		draftAt              = time.Now()
		draftLen             int
		writeFailed          bool
		draftAnnounced       bool
		// rowKey names the saved row the reply is being written to. It is the
		// request id until the owner's mid-turn message is taken in; from
		// there the reply continues in a new row (see splitAtSteer).
		rowKey   = requestID
		rowCount = 1
	)

	// noteWriteFailure surfaces a lost persist as an SSE error frame rather
	// than dropping the turn on the floor.
	noteWriteFailure := func(what string, err error) {
		if err == nil || writeFailed {
			return
		}
		writeFailed = true
		ts.signal("error", map[string]any{
			"message": "Could not save this turn (" + what + "): " + redactStreamError(err),
		})
	}

	thinkingPtr := func() *string {
		if t := strings.TrimSpace(fullThinking.String()); t != "" {
			return &t
		}
		return nil
	}

	toolLedger := func() (any, any) {
		var calls any = []any{}
		var results any = []any{}
		if len(collectedToolCalls) > 0 {
			calls = collectedToolCalls
		}
		if len(collectedToolResults) > 0 {
			results = collectedToolResults
		}
		return calls, results
	}

	// upsertDraft writes the partial assistant row. force skips the rate gate
	// (used when the tool ledger changed, which must never be lost).
	upsertDraft := func(force bool) {
		if persistDone || s.sessions == nil {
			return
		}
		n := fullResponse.Len()
		if !force {
			if n == draftLen {
				return
			}
			if n-draftLen < draftMinBytes && time.Since(draftAt) < draftInterval {
				return
			}
		}
		draftLen = n
		draftAt = time.Now()
		calls, _ := toolLedger()
		var results any = []any{}
		if len(draftToolResults) > 0 {
			results = draftToolResults
		}
		body := fullResponse.String()
		if strings.TrimSpace(body) == "" && len(collectedToolCalls) == 0 {
			return
		}
		msg, err := s.sessions.UpsertDraftMessage(
			sid, rowKey, "assistant", body, thinkingPtr(), calls, results, firstNonEmptyPtr(answeredModel, req.Model),
		)
		if err != nil {
			noteWriteFailure("draft", err)
			return
		}
		if !draftAnnounced && strings.TrimSpace(msg.ID) != "" {
			draftAnnounced = true
			ts.signal("draft", map[string]any{"message_id": msg.ID})
		}
	}

	finalize := func(content string) {
		if persistDone || s.sessions == nil {
			return
		}
		if _, ok, _ := s.sessions.Get(sid); !ok {
			persistDone = true
			return
		}
		persistDone = true
		calls, results := toolLedger()
		if _, err := s.sessions.FinalizeMessage(
			sid, rowKey, "assistant", content, thinkingPtr(), calls, results, firstNonEmptyPtr(answeredModel, req.Model), nil, nil,
		); err != nil {
			noteWriteFailure("assistant message", err)
			return
		}
		s.publishMessageAdded(sid, content)
	}

	// splitAtSteer closes the reply so far as a message of its own when the
	// owner's mid-turn message is taken in. That message was saved when it was
	// sent, so the conversation reads in the order it happened: what she had
	// said, what the owner said, then the rest of her reply. With nothing said
	// or done yet there is nothing to close.
	splitAtSteer := func() {
		if persistDone || s.sessions == nil {
			return
		}
		body := strings.TrimSpace(fullResponse.String())
		if body == "" && len(collectedToolCalls) == 0 {
			return
		}
		if body == "" {
			body = "*(Used tools — see process.)*"
		}
		calls, results := toolLedger()
		if _, err := s.sessions.FinalizeMessage(
			sid, rowKey, "assistant", body, thinkingPtr(), calls, results, firstNonEmptyPtr(answeredModel, req.Model), nil, nil,
		); err != nil {
			noteWriteFailure("assistant message", err)
			return
		}
		s.publishMessageAdded(sid, body)
		rowCount++
		rowKey = fmt.Sprintf("%s~%d", requestID, rowCount)
		fullResponse.Reset()
		fullThinking.Reset()
		collectedToolCalls, collectedToolResults, draftToolResults = nil, nil, nil
		narrationMark, draftLen, draftAnnounced = 0, 0, false
		// Clients close their live bubble here and start the next one.
		ts.signal("steered", map[string]any{"request_id": requestID})
	}

	persistInterrupted := func() {
		if persistDone || s.sessions == nil {
			return
		}
		reason := s.claims.PeekAbortReason(sid)
		hasTools := len(collectedToolCalls) > 0 || len(collectedToolResults) > 0
		finalize(interruptedTurnContent(fullResponse.String(), reason, hasTools))
	}

	emitAborted := func() {
		ts.signal("aborted", map[string]any{
			"message":    "Generation stopped",
			"request_id": requestID,
		})
	}

	emit := func(token string) error {
		if token == "" {
			return nil
		}
		if text, ok := modelTextToken(token); ok {
			s.moodNote(sid, func(p *moodPhase) { p.thinking, p.spoke = false, true })
			fullResponse.WriteString(text)
			ts.assistantText(text)
			upsertDraft(false)
			return nil
		}
		if strings.HasPrefix(token, "@@aborted") {
			aborted = true
			persistInterrupted()
			emitAborted()
			return errStreamAborted
		}
		switch {
		case strings.HasPrefix(token, "@@tool_call:"):
			parsed := parseToolCallToken(token)
			// Text since the previous call is narration about this step; keep it
			// on the call so the saved bubble can show only the reply.
			if body := fullResponse.String(); len(body) > narrationMark {
				if seg := strings.TrimSpace(body[narrationMark:]); seg != "" {
					parsed["narration"] = seg
					narrationMark = len(body)
				}
			}
			// When the call was made: what she said before it was said by now.
			parsed["at"] = time.Now().Unix()
			collectedToolCalls = append(collectedToolCalls, parsed)
			s.moodNote(sid, func(p *moodPhase) { p.tools++; p.steps++; p.thinking = false })
			name, _ := parsed["name"].(string)
			id, _ := parsed["id"].(string)
			args, _ := parsed["args"].(map[string]any)
			via, _ := parsed["via"].(string)
			upsertDraft(true)
			ts.toolUse(id, name, via, args)
		case strings.HasPrefix(token, "@@tool_result:"):
			res := parseToolResultToken(token)
			s.moodNote(sid, func(p *moodPhase) {
				p.tools = max(p.tools-1, 0)
				if res.OK {
					p.ok++
				} else {
					p.err++
				}
			})
			rec := res.record()
			rec["at"] = time.Now().Unix()
			collectedToolResults = append(collectedToolResults, rec)
			draftToolResults = append(draftToolResults, draftToolResult(rec))
			// Write the ledger before telling anyone: a client that saw the
			// result must find it on disk if the process dies next.
			upsertDraft(true)
			ts.toolResult(res)
		case strings.HasPrefix(token, "@@progress:"):
			raw := token[len("@@progress:"):]
			payload := map[string]any{"label": raw}
			if strings.TrimSpace(raw) != "" && strings.HasPrefix(strings.TrimSpace(raw), "{") {
				var obj map[string]any
				if err := json.Unmarshal([]byte(raw), &obj); err == nil {
					payload = obj
					delete(payload, "type")
				}
			}
			ts.signal("progress", payload)
		case strings.HasPrefix(token, "@@model:"):
			if name := parseAnsweringModelToken(token); name != nil {
				answeredModel = name
			}
		case strings.HasPrefix(token, "@@status:"):
			ts.status(strings.TrimSpace(token[len("@@status:"):]))
		case strings.HasPrefix(token, "@@steered"):
			splitAtSteer()
			ts.status("Taking that in…")
		case strings.HasPrefix(token, "@@thinking_round"):
			thinkingReplaceNext = true
		case strings.HasPrefix(token, "@@thinking:"):
			s.moodNote(sid, func(p *moodPhase) { p.thinking = true })
			thought := token[len("@@thinking:"):]
			if thought == "" {
				return nil
			}
			replace := thinkingReplaceNext
			thinkingReplaceNext = false
			if replace {
				fullThinking.Reset()
			}
			fullThinking.WriteString(thought)
			ts.assistantThinking(thought, replace)
		case strings.HasPrefix(token, "@@usage:"):
			raw := token[len("@@usage:"):]
			var part map[string]any
			if err := json.Unmarshal([]byte(raw), &part); err == nil && part != nil {
				delete(part, "type")
				ts.usage(part)
			}
		case strings.HasPrefix(token, "@@library_suggest:"):
			raw := strings.TrimSpace(token[len("@@library_suggest:"):])
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload["id"] != nil {
				delete(payload, "type")
				ts.signal("library_suggest", payload)
			}
		case strings.HasPrefix(token, "@@life_task:"):
			raw := token[len("@@life_task:"):]
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload != nil {
				delete(payload, "type")
				ts.signal("life_task", payload)
			}
		case strings.HasPrefix(token, "@@reaction:"):
			var rx struct {
				Emoji     string `json:"emoji"`
				MessageID string `json:"message_id"`
			}
			if err := json.Unmarshal([]byte(strings.TrimSpace(token[len("@@reaction:"):])), &rx); err == nil {
				if target, reactions, err := s.applyAgentReaction(sid, req.UserMessageID, rx.MessageID, rx.Emoji); err == nil {
					ts.signal("reaction", map[string]any{
						"message_id": target, "side": "agent", "emoji": reactions.Agent, "reactions": reactions,
					})
				}
			}
		case strings.HasPrefix(token, "@@todos:"):
			raw := token[len("@@todos:"):]
			var payload map[string]any
			if err := json.Unmarshal([]byte(raw), &payload); err == nil && payload != nil {
				delete(payload, "type")
				ts.signal("todos", payload)
			}
		case token == "@@tool_calls":
			// no-op marker
		case strings.HasPrefix(token, "@@"):
			// Unknown control token — never leak into the bubble.
		default:
			fullResponse.WriteString(token)
			ts.assistantText(token)
			upsertDraft(false)
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
	if turnErr == nil && !aborted {
		s.hearthAck(time.Now())
	}
	if aborted && !persistDone {
		persistInterrupted()
		emitAborted()
	}

	if turnErr != nil {
		safe := redactStreamError(turnErr)
		ts.signal("error", map[string]any{"message": safe})
		if !persistDone && s.sessions != nil {
			if _, ok, _ := s.sessions.Get(sid); ok {
				note := ""
				if body := strings.TrimSpace(fullResponse.String()); body != "" {
					note = body + "\n\n"
				}
				note += "*(Turn ended with an error: " + strings.TrimRight(safe, ".!? ") + ". History is intact — send **continue** to resume.)*"
				finalize(note)
			} else {
				persistDone = true
			}
		}
		ts.done(requestID, "error", safe)
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
	if persistText != "" && !persistDone {
		finalize(persistText)
	}

	status := "ok"
	if aborted {
		status = "aborted"
	}
	ts.done(requestID, status, "")
}


// publishTurnStarted tells every other surface (desktop, Web UI) that a live
// turn has just begun on this session, with the request id they need to attach
// to /stream/attach for tokens and tool activity. Also announces the user
// message so the thread list and open chat catch up before the first token.
func (s *Server) publishTurnStarted(sid, requestID, userMsgID string) {
	if s == nil || sid == "" {
		return
	}
	var title *string
	var count *int
	var origin *string
	if s.sessions != nil {
		if sess, ok, _ := s.sessions.Get(sid); ok {
			t := sess.Title
			title = &t
			c := sess.MessageCount
			count = &c
			origin = sess.OriginChannel
		}
	}
	if userMsgID != "" {
		role := "user"
		mid := userMsgID
		s.publishSessionEvent(SessionEvent{
			Type:          "message_added",
			SessionID:     sid,
			OriginChannel: origin,
			MessageID:     &mid,
			Title:         title,
			MessageCount:  count,
			Role:          &role,
		})
	}
	if requestID != "" {
		rid := requestID
		s.publishSessionEvent(SessionEvent{
			Type:          "turn_started",
			SessionID:     sid,
			OriginChannel: origin,
			RequestID:     &rid,
			Title:         title,
			MessageCount:  count,
		})
	}
}

// publishMessageAdded announces a persisted assistant reply for every session,
// not only messenger-origin ones — the desktop sidebar and any attached client
// need the same signal. Only the messenger mirror stays origin-gated.
func (s *Server) publishMessageAdded(sid, reply string) {
	if s == nil || s.sessions == nil {
		return
	}
	sess, ok, _ := s.sessions.Get(sid)
	if !ok {
		return
	}
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
	if sess.OriginChannel != nil && strings.TrimSpace(*sess.OriginChannel) != "" {
		s.mirrorDesktopReply(sess, reply)
	}
}

var errStreamAborted = errors.New("stream aborted")

// draftToolResult is rec with its output cut to toolCap, the most a reader of
// the session ever sees. rec itself is left whole.
func draftToolResult(rec map[string]any) map[string]any {
	out := make(map[string]any, len(rec))
	for k, v := range rec {
		out[k] = v
	}
	if _, has := out["output"]; has {
		out["output"] = truncAny(out["output"], toolCap)
	}
	return out
}
