package httpapi

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	contentCap = 32_000
	toolCap    = 8_000
)

// ChatMessage matches the list_messages wire shape (Python messages.py).
type ChatMessage struct {
	ID          string `json:"id"`
	Role        string `json:"role"`
	Content     any    `json:"content"`
	Thinking    any    `json:"thinking"`
	ToolCalls   any    `json:"tool_calls"`
	ToolResults any    `json:"tool_results"`
	Model       any    `json:"model"`
	Agent       any    `json:"agent"`
	Tokens      any    `json:"tokens"`
	CreatedAt   any    `json:"created_at"`
	Reverted    bool   `json:"reverted"`
}

type sendMessageRequest struct {
	Message     string           `json:"message"`
	Model       *string          `json:"model"`
	Provider    *string          `json:"provider"`
	Agent       *string          `json:"agent"`
	Attachments []map[string]any `json:"attachments"`
	PlanMode    bool             `json:"plan_mode"`
	ChatMode    bool             `json:"chat_mode"`
}

// TurnRequest is what a TurnRunner receives for POST .../messages.
type TurnRequest struct {
	SessionID   string
	Prompt      string
	Model       *string
	Provider    *string
	PlanMode    bool
	ChatMode    bool
	Attachments []map[string]any
}

// TurnRunner generates assistant tokens for the sync send path.
// @@-prefixed tokens are filtered out of the saved reply (tool lifecycle).
type TurnRunner interface {
	RunTurn(ctx context.Context, req TurnRequest, emit func(token string) error) error
}

func truncStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + fmt.Sprintf("\n…[truncated %d chars]", len(s)-n)
}

func truncAny(v any, n int) any {
	s, ok := v.(string)
	if !ok || len(s) <= n {
		return v
	}
	return truncStr(s, n)
}

func truncToolResults(trs any) any {
	list, ok := trs.([]any)
	if !ok {
		return trs
	}
	out := make([]any, 0, len(list))
	for _, tr := range list {
		if m, ok := tr.(map[string]any); ok {
			item := make(map[string]any, len(m))
			for k, v := range m {
				item[k] = v
			}
			if _, has := item["output"]; has {
				item["output"] = truncAny(item["output"], toolCap)
			}
			out = append(out, item)
		} else {
			out = append(out, truncAny(tr, toolCap))
		}
	}
	return out
}

func (s *sessionStore) ListMessages(sessionID string, limit, offset int) ([]ChatMessage, error) {
	if limit < 0 {
		limit = 0
	}
	if limit > 500 {
		limit = 500
	}
	if offset < 0 {
		offset = 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rows, err := s.db.Query(
		`SELECT id, role, content, thinking, tool_calls, tool_results,
		        model, agent, tokens, created_at, reverted
		 FROM (
		   SELECT id, role, content, thinking, tool_calls, tool_results,
		          model, agent, tokens, created_at, reverted, rowid AS _rid
		   FROM chat_messages
		   WHERE session_id = ? AND reverted = 0
		   ORDER BY created_at DESC, rowid DESC
		   LIMIT ? OFFSET ?
		 ) AS recent
		 ORDER BY created_at ASC, _rid ASC`,
		sessionID, limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]ChatMessage, 0)
	for rows.Next() {
		msg, err := scanMessage(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, msg)
	}
	return out, rows.Err()
}

func scanMessage(row scannable) (ChatMessage, error) {
	var (
		id, role, content, created     string
		thinking, model, agent         sql.NullString
		toolCallsJSON, toolResultsJSON string
		tokens                         sql.NullInt64
		reverted                       int
	)
	err := row.Scan(
		&id, &role, &content, &thinking, &toolCallsJSON, &toolResultsJSON,
		&model, &agent, &tokens, &created, &reverted,
	)
	if err != nil {
		return ChatMessage{}, err
	}
	var toolCalls any = []any{}
	var toolResults any = []any{}
	if toolCallsJSON != "" {
		_ = json.Unmarshal([]byte(toolCallsJSON), &toolCalls)
	}
	if toolResultsJSON != "" {
		_ = json.Unmarshal([]byte(toolResultsJSON), &toolResults)
	}
	msg := ChatMessage{
		ID:          id,
		Role:        role,
		Content:     content,
		ToolCalls:   toolCalls,
		ToolResults: toolResults,
		CreatedAt:   created,
		Reverted:    reverted != 0,
	}
	if thinking.Valid {
		msg.Thinking = thinking.String
	} else {
		msg.Thinking = nil
	}
	if model.Valid {
		msg.Model = model.String
	} else {
		msg.Model = nil
	}
	if agent.Valid {
		msg.Agent = agent.String
	} else {
		msg.Agent = nil
	}
	if tokens.Valid {
		msg.Tokens = tokens.Int64
	} else {
		msg.Tokens = nil
	}
	return msg, nil
}

func (s *sessionStore) AddMessage(sessionID, role, content string, model, agent *string) (ChatMessage, error) {
	return s.AddMessageFull(sessionID, role, content, nil, nil, nil, model, agent, nil)
}

// AddMessageFull inserts a chat row including thinking / tool payloads (stream path).
func (s *sessionStore) AddMessageFull(
	sessionID, role, content string,
	thinking *string,
	toolCalls, toolResults any,
	model, agent *string,
	tokens *int64,
) (ChatMessage, error) {
	now := nowISO()
	id := newSessionID()
	tcJSON := "[]"
	trJSON := "[]"
	if toolCalls != nil {
		if b, err := json.Marshal(toolCalls); err == nil {
			tcJSON = string(b)
		}
	}
	if toolResults != nil {
		if b, err := json.Marshal(toolResults); err == nil {
			trJSON = string(b)
		}
	}
	var tok any
	if tokens != nil {
		tok = *tokens
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := s.db.Exec(
		`INSERT INTO chat_messages (
			id, session_id, role, content, thinking, tool_calls, tool_results,
			model, agent, tokens, created_at, reverted
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)`,
		id, sessionID, role, content, nullStr(thinking), tcJSON, trJSON,
		nullStr(model), nullStr(agent), tok, now,
	)
	if err != nil {
		return ChatMessage{}, err
	}
	_, err = s.db.Exec(
		`UPDATE chat_sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?`,
		now, sessionID,
	)
	if err != nil {
		return ChatMessage{}, err
	}
	var thinkAny any
	if thinking != nil {
		thinkAny = *thinking
	}
	tcAny := any([]any{})
	trAny := any([]any{})
	_ = json.Unmarshal([]byte(tcJSON), &tcAny)
	_ = json.Unmarshal([]byte(trJSON), &trAny)
	var tokAny any
	if tokens != nil {
		tokAny = *tokens
	}
	return ChatMessage{
		ID:          id,
		Role:        role,
		Content:     content,
		Thinking:    thinkAny,
		ToolCalls:   tcAny,
		ToolResults: trAny,
		Model:       nullToAny(model),
		Agent:       nullToAny(agent),
		Tokens:      tokAny,
		CreatedAt:   now,
		Reverted:    false,
	}, nil
}

func (s *sessionStore) SetTitle(sessionID, title string) error {
	title = strings.TrimSpace(title)
	if title == "" {
		title = "New Session"
	}
	now := nowISO()
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := s.db.Exec(
		`UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?`,
		title, now, sessionID,
	)
	return err
}

func (s *sessionStore) UpdateLLMBind(sessionID string, provider, model *string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	sets := make([]string, 0, 3)
	args := make([]any, 0, 3)
	if provider != nil {
		sets = append(sets, "llm_provider = ?")
		args = append(args, *provider)
	}
	if model != nil {
		sets = append(sets, "model = ?")
		args = append(args, *model)
	}
	if len(sets) == 0 {
		return nil
	}
	now := nowISO()
	sets = append(sets, "updated_at = ?")
	args = append(args, now, sessionID)
	_, err := s.db.Exec(
		`UPDATE chat_sessions SET `+strings.Join(sets, ", ")+` WHERE id = ?`,
		args...,
	)
	return err
}

func nullToAny(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func wireMessage(m ChatMessage) ChatMessage {
	out := m
	out.Content = truncAny(m.Content, contentCap)
	if m.Thinking != nil {
		out.Thinking = truncAny(m.Thinking, contentCap/2)
	}
	out.ToolResults = truncToolResults(m.ToolResults)
	return out
}

func (s *Server) handleListMessages(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	limit := 100
	offset := 0
	if v := r.URL.Query().Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n > 500 {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"detail": "invalid limit"})
			return
		}
		limit = n
	}
	if v := r.URL.Query().Get("offset"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"detail": "invalid offset"})
			return
		}
		offset = n
	}
	if _, ok, err := s.sessions.Get(sid); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	} else if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	msgs, err := s.sessions.ListMessages(sid, limit, offset)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	wired := make([]ChatMessage, len(msgs))
	for i, m := range msgs {
		wired[i] = wireMessage(m)
	}
	writeJSON(w, http.StatusOK, map[string]any{"messages": wired})
}

func (s *Server) handleSendMessage(w http.ResponseWriter, r *http.Request) {
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
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Message is empty"})
		return
	}

	if !s.claims.TryClaim(sid) {
		writeJSON(w, http.StatusConflict, map[string]string{"detail": sessionBusyDetail})
		return
	}
	epoch := s.claims.Epoch(sid)
	defer s.claims.Release(sid, &epoch)

	requestID := newSessionID()
	start := time.Now()

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
	}

	ctx := s.claims.Context(sid)
	if ctx == nil {
		ctx = r.Context()
	}
	var responseText strings.Builder
	turnErr := s.runner.RunTurn(ctx, TurnRequest{
		SessionID:   sid,
		Prompt:      prompt,
		Model:       sessModel,
		Provider:    sessProvider,
		PlanMode:    req.PlanMode,
		ChatMode:    req.ChatMode,
		Attachments: attDicts,
	}, func(token string) error {
		if strings.HasPrefix(token, "@@") {
			return nil
		}
		responseText.WriteString(token)
		if s.sessions != nil && responseText.Len() > 0 && responseText.Len()%64 == 0 {
			if _, ok, _ := s.sessions.Get(sid); !ok {
				return errSessionGone
			}
		}
		return nil
	})

	if errors.Is(turnErr, errSessionGone) {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	if turnErr != nil {
		if s.sessions != nil {
			if _, ok, _ := s.sessions.Get(sid); !ok {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
				return
			}
		}
		var httpErr *turnHTTPError
		if errors.As(turnErr, &httpErr) {
			writeJSON(w, httpErr.Status, map[string]string{"detail": httpErr.Detail})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
		return
	}

	reply := responseText.String()
	if s.sessions != nil && reply != "" {
		if _, ok, _ := s.sessions.Get(sid); !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
			return
		}
		if _, err := s.sessions.AddMessage(sid, "assistant", reply, sessModel, nil); err != nil {
			if _, ok, _ := s.sessions.Get(sid); !ok {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
				return
			}
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
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
		}
	}

	out := reply
	if out == "" {
		out = "Processed."
	}
	elapsedMs := float64(int(time.Since(start).Seconds()*10000+0.5)) / 10
	writeJSON(w, http.StatusOK, map[string]any{
		"request_id":         requestID,
		"session_id":         sid,
		"response":           out,
		"processing_time_ms": elapsedMs,
	})
}

var errSessionGone = errors.New("session gone")

type turnHTTPError struct {
	Status int
	Detail string
}

func (e *turnHTTPError) Error() string { return e.Detail }

// TurnHTTPError builds a runner error that preserves status (Python HTTPException).
func TurnHTTPError(status int, detail string) error {
	return &turnHTTPError{Status: status, Detail: detail}
}

func (s *Server) handleAbortSession(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	q := r.URL.Query()
	var reasonPtr *string
	if q.Has("reason") {
		raw := q.Get("reason")
		reasonPtr = &raw
	}
	var epochPtr *int
	if v := q.Get("epoch"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid epoch"})
			return
		}
		epochPtr = &n
	}
	n := 0
	if s.claims != nil {
		n = s.claims.Abort(sid, epochPtr, reasonPtr)
	}
	reasonN := abortReasonStop
	if reasonPtr != nil {
		reasonN = normalizeAbortReason(*reasonPtr)
	}
	ignored := epochPtr != nil && n == 0
	status := "aborted"
	if ignored {
		status = "ignored"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     status,
		"session_id": sid,
		"notified":   n,
		"reason":     reasonN,
		"epoch":      epochPtr,
	})
}
