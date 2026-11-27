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
	"unicode/utf8"
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
	// Draft marks a partial assistant row written while the turn is still
	// running. The final write replaces it in place.
	Draft bool `json:"draft"`
	// RequestID names the turn that produced this row. It is what addresses
	// the recorded evidence (…/turns/{request_id}/tools/{call_id}), so without
	// it the process trail can only show the full output of a turn this client
	// happened to watch live. Empty for rows written before turn logs.
	RequestID string `json:"request_id,omitempty"`
	// Reactions: the owner's and Remedy's, one each (see reactions.go).
	Reactions *MessageReactions `json:"reactions,omitempty"`
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
	SessionID string
	Prompt    string
	Model     *string
	Provider  *string
	// ProjectPath is the session's focused project folder (workspace root).
	ProjectPath string
	PlanMode    bool
	ChatMode    bool
	Attachments []map[string]any
	// MaxIterations caps cognition.Engine loops when > 0 (hive forager budget).
	MaxIterations int
	// DrainNudges returns queued owner mid-turn guidance (steer). Optional.
	DrainNudges func() []string
	// History is the last few session messages before Prompt, oldest first,
	// as {"role","content"}. They become transcript messages (not a system
	// block) so prior turns are not rendered twice.
	History []map[string]any
	// UserMessageID is the persisted owner message that started this turn
	// (the react tool's default target).
	UserMessageID string
	// Origin names where the prompt came from: "desktop" (default when empty),
	// "webui", "cli", "hive:<parent-session>", "partner:…" (work Remedy started
	// herself), or a messenger channel such as "telegram:<user-id>". Non-owner
	// origins run untrusted: the prompt is wrapped in an untrusted envelope and
	// every mutation asks regardless of approval_mode (see OriginIsOwner).
	//
	// The owner's paired phone (Remedy Connect) is the owner: its requests
	// reach the local API through the Noise-authenticated tunnel and arrive
	// with an empty origin, exactly like the desktop — an owner decision
	// (2026-11). Payment, credential and send checkpoints still always ask.
	// A literal "phone:<device>" origin, if anything ever sets one, stays
	// untrusted.
	Origin string
}

// OriginIsOwner reports whether a TurnRequest.Origin is one of the owner's own
// surfaces. Empty means desktop — including the owner's paired phone, whose
// requests arrive through the Connect tunnel without an origin. Messenger,
// "phone:" and hive origins are untrusted.
func OriginIsOwner(origin string) bool {
	o := strings.ToLower(strings.TrimSpace(origin))
	switch {
	case o == "", o == "desktop", o == "webui", o == "cli", o == "fixture":
		return true
	case isPartnerOrigin(o):
		// Minted only by the runtime for work the owner authorized: a child
		// Remedy started from an owner turn (or the owner approved), a
		// routine, goal autopilot. Checkpoints still always ask.
		return true
	default:
		return false
	}
}

// TurnRunner generates assistant tokens for the sync send path.
// @@-prefixed tokens are filtered out of the saved reply (tool lifecycle).
type TurnRunner interface {
	RunTurn(ctx context.Context, req TurnRequest, emit func(token string) error) error
}

// truncStr keeps the first n bytes of s, cutting on a rune boundary.
func truncStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n] + fmt.Sprintf("\n…[truncated %d chars]", len(s)-n)
}

// A turn carries the conversation itself, not a short tail of it: the engine
// trims and compacts to the model's window, which it knows and this does not.
// These only bound what is loaded.
const (
	// turnHistoryMessages is the least a long session carries; the most is
	// that plus one block, less one.
	turnHistoryMessages = 200
	// turnHistoryBlock is how many messages the window's start moves by.
	turnHistoryBlock = 20
	// turnHistoryContentChars cuts one earlier message, with a marker.
	turnHistoryContentChars = 16_000
	// turnHistoryChars is the most earlier text a turn loads (about 60k
	// tokens); past it whole blocks leave from the front.
	turnHistoryChars = 240_000
)

// turnHistoryWindow is how many of a session's total messages a turn carries.
// The window's first message moves a block at a time, not on every turn: a
// provider caches a request by prefix, so a window that slid by two each turn
// made it re-read the whole history every time.
func turnHistoryWindow(total int) int {
	if total <= turnHistoryMessages {
		return turnHistoryMessages
	}
	start := (total - turnHistoryMessages) / turnHistoryBlock * turnHistoryBlock
	return total - start
}

// countMessages is how many rows a session has; 0 when it cannot be read.
func (s *sessionStore) countMessages(sessionID string) int {
	if s == nil {
		return 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	_ = s.db.QueryRow(`SELECT COUNT(*) FROM chat_messages WHERE session_id = ?`, sessionID).Scan(&n)
	return n
}

// turnHistory returns the most recent session messages, oldest first, as
// prompt.assemble history rows. Call it before persisting the current prompt.
func (s *Server) turnHistory(sid string) []map[string]any {
	if s == nil || s.sessions == nil {
		return nil
	}
	total := s.sessions.countMessages(sid)
	msgs, err := s.sessions.ListMessages(sid, turnHistoryWindow(total), 0)
	if err != nil {
		return nil
	}
	// Over the text budget, whole blocks leave from the front (so the start
	// still moves rarely); the newest block always stays.
	size := 0
	for _, m := range msgs {
		c, _ := m.Content.(string)
		size += min(len(c), turnHistoryContentChars)
	}
	for size > turnHistoryChars && len(msgs) > turnHistoryBlock {
		for _, m := range msgs[:turnHistoryBlock] {
			c, _ := m.Content.(string)
			size -= min(len(c), turnHistoryContentChars)
		}
		msgs = msgs[turnHistoryBlock:]
	}
	out := make([]map[string]any, 0, len(msgs)+1)
	if hidden := total - len(msgs); hidden > 0 && len(msgs) > 0 {
		// Said, not silent: she should know the conversation is longer than
		// what she can see.
		out = append(out, map[string]any{
			"role":    "user",
			"content": fmt.Sprintf("[%d earlier messages of this conversation are not shown here.]", hidden),
		})
	}
	for _, m := range msgs {
		content, _ := m.Content.(string)
		content = truncStr(content, turnHistoryContentChars)
		// What she did, not only what she said: without it the next turn has
		// no idea a file was already read or a command already run.
		if ledger := toolLedgerLine(m.ToolCalls, m.ToolResults); ledger != "" {
			content = strings.TrimSpace(content + "\n" + ledger)
		}
		row := map[string]any{
			"role":    m.Role,
			"content": content,
		}
		if m.Reactions != nil && m.Reactions.User != "" {
			row["owner_reaction"] = m.Reactions.User
		}
		out = append(out, row)
	}
	return out
}

const (
	toolLedgerCalls = 20
	toolLedgerChars = 700
)

// toolLedgerLine is one line naming the tools a saved message ran and the main
// thing each acted on, with failures marked. Empty when it ran none.
func toolLedgerLine(calls, results any) string {
	list, _ := calls.([]any)
	if len(list) == 0 {
		return ""
	}
	failed := map[string]bool{}
	if rs, ok := results.([]any); ok {
		for _, r := range rs {
			row, _ := r.(map[string]any)
			if id, _ := row["id"].(string); id != "" && row["error"] != nil {
				failed[id] = true
			}
		}
	}
	parts := make([]string, 0, len(list))
	for i, c := range list {
		if i == toolLedgerCalls {
			parts = append(parts, fmt.Sprintf("and %d more", len(list)-i))
			break
		}
		call, _ := c.(map[string]any)
		name, _ := call["name"].(string)
		if name == "" {
			continue
		}
		part := name
		if args, ok := call["args"].(map[string]any); ok {
			for _, key := range []string{"path", "command", "query", "url", "pattern"} {
				if v, _ := args[key].(string); strings.TrimSpace(v) != "" {
					v = strings.Join(strings.Fields(v), " ")
					if len(v) > 80 {
						v = truncStr(v, 80)
						v = v[:strings.Index(v, "\n")] + "…"
					}
					part += " " + v
					break
				}
			}
		}
		if id, _ := call["id"].(string); failed[id] {
			part += " (failed)"
		}
		parts = append(parts, part)
	}
	if len(parts) == 0 {
		return ""
	}
	line := "[Tools used in this message: " + strings.Join(parts, " · ") + "]"
	if len(line) > toolLedgerChars {
		line = truncStr(line, toolLedgerChars)
		line = line[:strings.Index(line, "\n")] + "…]"
	}
	return line
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
		        model, agent, tokens, created_at, reverted, draft, request_id, reactions
		 FROM (
		   SELECT id, role, content, thinking, tool_calls, tool_results,
		          model, agent, tokens, created_at, reverted, draft, request_id, COALESCE(reactions, '{}') AS reactions, rowid AS _rid
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

// ListMessagesExport returns up to limit non-reverted messages in chrono order
// (export / timeline; higher cap than the list UI).
func (s *sessionStore) ListMessagesExport(sessionID string, limit int) ([]ChatMessage, error) {
	if limit <= 0 {
		limit = 2000
	}
	if limit > 2000 {
		limit = 2000
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	rows, err := s.db.Query(
		`SELECT id, role, content, thinking, tool_calls, tool_results,
		        model, agent, tokens, created_at, reverted, draft, request_id, reactions
		 FROM (
		   SELECT id, role, content, thinking, tool_calls, tool_results,
		          model, agent, tokens, created_at, reverted, draft, request_id, COALESCE(reactions, '{}') AS reactions, rowid AS _rid
		   FROM chat_messages
		   WHERE session_id = ? AND reverted = 0
		   ORDER BY created_at DESC, rowid DESC
		   LIMIT ?
		 ) AS recent
		 ORDER BY created_at ASC, _rid ASC`,
		sessionID, limit,
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

// GetMessage returns a chat row and its session_id.
func (s *sessionStore) GetMessage(msgID string) (msg ChatMessage, sessionID string, ok bool, err error) {
	msgID = strings.TrimSpace(msgID)
	if msgID == "" {
		return ChatMessage{}, "", false, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	row := s.db.QueryRow(
		`SELECT id, session_id, role, content, thinking, tool_calls, tool_results,
		        model, agent, tokens, created_at, reverted, draft
		 FROM chat_messages WHERE id = ?`,
		msgID,
	)
	var (
		id, sid, role, content, created string
		thinking, model, agent          sql.NullString
		toolCallsJSON, toolResultsJSON  string
		tokens                          sql.NullInt64
		reverted, draft                 int
	)
	err = row.Scan(
		&id, &sid, &role, &content, &thinking, &toolCallsJSON, &toolResultsJSON,
		&model, &agent, &tokens, &created, &reverted, &draft,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return ChatMessage{}, "", false, nil
	}
	if err != nil {
		return ChatMessage{}, "", false, err
	}
	var toolCalls any = []any{}
	var toolResults any = []any{}
	if toolCallsJSON != "" {
		_ = json.Unmarshal([]byte(toolCallsJSON), &toolCalls)
	}
	if toolResultsJSON != "" {
		_ = json.Unmarshal([]byte(toolResultsJSON), &toolResults)
	}
	msg = ChatMessage{
		ID:          id,
		Role:        role,
		Content:     content,
		ToolCalls:   toolCalls,
		ToolResults: toolResults,
		CreatedAt:   created,
		Reverted:    reverted != 0,
		Draft:       draft != 0,
	}
	if thinking.Valid {
		msg.Thinking = thinking.String
	}
	if model.Valid {
		msg.Model = model.String
	}
	if agent.Valid {
		msg.Agent = agent.String
	}
	if tokens.Valid {
		msg.Tokens = tokens.Int64
	}
	return msg, sid, true, nil
}

// RevertFrom soft-deletes msgID and all later messages; resyncs message_count.
func (s *sessionStore) RevertFrom(sessionID, msgID string) (int, error) {
	sessionID = strings.TrimSpace(sessionID)
	msgID = strings.TrimSpace(msgID)
	if sessionID == "" || msgID == "" {
		return 0, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var cutAt string
	var cutRid int64
	err := s.db.QueryRow(
		`SELECT created_at, rowid FROM chat_messages WHERE id = ? AND session_id = ?`,
		msgID, sessionID,
	).Scan(&cutAt, &cutRid)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	res, err := s.db.Exec(
		`UPDATE chat_messages SET reverted = 1
		 WHERE session_id = ? AND reverted = 0 AND (
		   created_at > ? OR (created_at = ? AND rowid >= ?)
		 )`,
		sessionID, cutAt, cutAt, cutRid,
	)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	if n > 0 {
		var live int
		_ = s.db.QueryRow(
			`SELECT COUNT(*) FROM chat_messages WHERE session_id = ? AND reverted = 0`,
			sessionID,
		).Scan(&live)
		_, _ = s.db.Exec(
			`UPDATE chat_sessions SET message_count = ?, updated_at = ? WHERE id = ?`,
			live, nowISO(), sessionID,
		)
	}
	return int(n), nil
}

// ClearMessages hard-deletes all messages for a session (slash /reset).
func (s *sessionStore) ClearMessages(sessionID string) (int, error) {
	sessionID = strings.TrimSpace(sessionID)
	if sessionID == "" {
		return 0, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	res, err := s.db.Exec(`DELETE FROM chat_messages WHERE session_id = ?`, sessionID)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	_, err = s.db.Exec(
		`UPDATE chat_sessions SET message_count = 0, updated_at = ? WHERE id = ?`,
		nowISO(), sessionID,
	)
	return int(n), err
}

func scanMessage(row scannable) (ChatMessage, error) {
	var (
		id, role, content, created     string
		thinking, model, agent         sql.NullString
		toolCallsJSON, toolResultsJSON string
		tokens                         sql.NullInt64
		reverted, draft                int
		requestID                      sql.NullString
		reactionsJSON                  string
	)
	err := row.Scan(
		&id, &role, &content, &thinking, &toolCallsJSON, &toolResultsJSON,
		&model, &agent, &tokens, &created, &reverted, &draft, &requestID, &reactionsJSON,
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
		Reactions:   parseReactions(reactionsJSON),
		RequestID:   requestID.String,
		ID:          id,
		Role:        role,
		Content:     content,
		ToolCalls:   toolCalls,
		ToolResults: toolResults,
		CreatedAt:   created,
		Reverted:    reverted != 0,
		Draft:       draft != 0,
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
// The INSERT and the message_count UPDATE run in one transaction: a chat row
// is never written without its counter, and a failure is returned, never
// swallowed.
func (s *sessionStore) AddMessageFull(
	sessionID, role, content string,
	thinking *string,
	toolCalls, toolResults any,
	model, agent *string,
	tokens *int64,
) (ChatMessage, error) {
	return s.insertMessage(sessionID, "", role, content, thinking, toolCalls, toolResults, model, agent, tokens, false)
}

// marshalToolPayload renders a tool_calls / tool_results column value.
func marshalToolPayload(v any) string {
	if v == nil {
		return "[]"
	}
	b, err := json.Marshal(v)
	if err != nil {
		return "[]"
	}
	return string(b)
}

func (s *sessionStore) insertMessage(
	sessionID, requestID, role, content string,
	thinking *string,
	toolCalls, toolResults any,
	model, agent *string,
	tokens *int64,
	draft bool,
) (ChatMessage, error) {
	now := nowISO()
	id := newSessionID()
	tcJSON := marshalToolPayload(toolCalls)
	trJSON := marshalToolPayload(toolResults)
	var tok any
	if tokens != nil {
		tok = *tokens
	}
	draftFlag := 0
	if draft {
		draftFlag = 1
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.Begin()
	if err != nil {
		return ChatMessage{}, err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.Exec(
		`INSERT INTO chat_messages (
			id, session_id, role, content, thinking, tool_calls, tool_results,
			model, agent, tokens, created_at, reverted, request_id, draft
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
		id, sessionID, role, content, nullStr(thinking), tcJSON, trJSON,
		nullStr(model), nullStr(agent), tok, now, nullStr(nullableTrim(&requestID)), draftFlag,
	); err != nil {
		return ChatMessage{}, err
	}
	if _, err := tx.Exec(
		`UPDATE chat_sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?`,
		now, sessionID,
	); err != nil {
		return ChatMessage{}, err
	}
	if err := tx.Commit(); err != nil {
		return ChatMessage{}, err
	}
	return buildChatMessage(id, role, content, thinking, tcJSON, trJSON, model, agent, tokens, now, draft), nil
}

func buildChatMessage(
	id, role, content string,
	thinking *string,
	tcJSON, trJSON string,
	model, agent *string,
	tokens *int64,
	created string,
	draft bool,
) ChatMessage {
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
		CreatedAt:   created,
		Reverted:    false,
		Draft:       draft,
	}
}

// findTurnRow returns the id of the row already written for (sessionID,
// requestID), draft or final.
func (s *sessionStore) findTurnRowLocked(sessionID, requestID, role string) (string, bool, error) {
	var id string
	err := s.db.QueryRow(
		`SELECT id FROM chat_messages
		 WHERE session_id = ? AND request_id = ? AND role = ? AND reverted = 0
		 ORDER BY rowid DESC LIMIT 1`,
		sessionID, requestID, role,
	).Scan(&id)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	return id, true, nil
}

// UpsertDraftMessage writes the partial answer for a running turn. The first
// call inserts the row (and counts it); later calls update it in place, so a
// crash mid-build leaves the partial text and the tool ledger on disk instead
// of an empty assistant turn.
func (s *sessionStore) UpsertDraftMessage(
	sessionID, requestID, role, content string,
	thinking *string,
	toolCalls, toolResults any,
	model *string,
) (ChatMessage, error) {
	requestID = strings.TrimSpace(requestID)
	if requestID == "" {
		return ChatMessage{}, errors.New("request_id required for a draft row")
	}
	s.mu.Lock()
	id, found, err := s.findTurnRowLocked(sessionID, requestID, role)
	s.mu.Unlock()
	if err != nil {
		return ChatMessage{}, err
	}
	if !found {
		return s.insertMessage(
			sessionID, requestID, role, content, thinking,
			toolCalls, toolResults, model, nil, nil, true,
		)
	}
	tcJSON := marshalToolPayload(toolCalls)
	trJSON := marshalToolPayload(toolResults)
	now := nowISO()
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.db.Exec(
		`UPDATE chat_messages
		 SET content = ?, thinking = ?, tool_calls = ?, tool_results = ?, draft = 1
		 WHERE id = ?`,
		content, nullStr(thinking), tcJSON, trJSON, id,
	); err != nil {
		return ChatMessage{}, err
	}
	if _, err := s.db.Exec(
		`UPDATE chat_sessions SET updated_at = ? WHERE id = ?`, now, sessionID,
	); err != nil {
		return ChatMessage{}, err
	}
	msg := buildChatMessage(id, role, content, thinking, tcJSON, trJSON, model, nil, nil, now, true)
	return msg, nil
}

// FinalizeMessage replaces the draft row for a turn with its final content
// (draft = 0), or inserts the row when no draft was ever written. It is the
// single assistant-write path for a streamed turn, so an interrupted, errored
// and completed turn all leave exactly one row.
func (s *sessionStore) FinalizeMessage(
	sessionID, requestID, role, content string,
	thinking *string,
	toolCalls, toolResults any,
	model, agent *string,
	tokens *int64,
) (ChatMessage, error) {
	requestID = strings.TrimSpace(requestID)
	if requestID != "" {
		s.mu.Lock()
		id, found, err := s.findTurnRowLocked(sessionID, requestID, role)
		s.mu.Unlock()
		if err != nil {
			return ChatMessage{}, err
		}
		if found {
			tcJSON := marshalToolPayload(toolCalls)
			trJSON := marshalToolPayload(toolResults)
			now := nowISO()
			var tok any
			if tokens != nil {
				tok = *tokens
			}
			s.mu.Lock()
			defer s.mu.Unlock()
			tx, err := s.db.Begin()
			if err != nil {
				return ChatMessage{}, err
			}
			defer func() { _ = tx.Rollback() }()
			if _, err := tx.Exec(
				`UPDATE chat_messages
				 SET content = ?, thinking = ?, tool_calls = ?, tool_results = ?,
				     model = ?, agent = ?, tokens = ?, draft = 0
				 WHERE id = ?`,
				content, nullStr(thinking), tcJSON, trJSON,
				nullStr(model), nullStr(agent), tok, id,
			); err != nil {
				return ChatMessage{}, err
			}
			if _, err := tx.Exec(
				`UPDATE chat_sessions SET updated_at = ? WHERE id = ?`, now, sessionID,
			); err != nil {
				return ChatMessage{}, err
			}
			if err := tx.Commit(); err != nil {
				return ChatMessage{}, err
			}
			return buildChatMessage(id, role, content, thinking, tcJSON, trJSON, model, agent, tokens, now, false), nil
		}
	}
	return s.insertMessage(
		sessionID, requestID, role, content, thinking,
		toolCalls, toolResults, model, agent, tokens, false,
	)
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
	s.NoteUserActivity()
	s.hearthTouch("desktop", time.Now())

	epoch, claimCtx, claimed := s.claims.TryClaim(sid)
	if !claimed {
		writeJSON(w, http.StatusConflict, map[string]string{"detail": sessionBusyDetail})
		return
	}
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
	var history []map[string]any
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
		ex, _, _ := s.sessions.Get(sid)
		if ex.ID != "" {
			sessProvider, sessModel = resolveSessionLLMBind(ex.LLMProvider, ex.Model, req.Provider, req.Model)
		} else {
			sessProvider, sessModel = sp, sm
		}
		history = s.turnHistory(sid)
		if _, err := s.sessions.AddMessage(sid, "user", displayContent, nil, nil); err != nil {
			if _, ok, _ := s.sessions.Get(sid); !ok {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
				return
			}
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
	}

	ctx := claimCtx
	if ctx == nil {
		ctx = r.Context()
	}
	var responseText strings.Builder
	var collectedToolCalls []map[string]any
	var collectedToolResults []map[string]any
	var answeredModel *string
	turnErr := s.runner.RunTurn(ctx, TurnRequest{
		SessionID:   sid,
		Prompt:      prompt,
		Model:       sessModel,
		Provider:    sessProvider,
		ProjectPath: projectPath,
		PlanMode:    req.PlanMode,
		ChatMode:    req.ChatMode,
		Attachments: attDicts,
		History:     history,
		DrainNudges: func() []string {
			if s.claims == nil {
				return nil
			}
			return s.claims.DrainNudges(sid)
		},
	}, func(token string) error {
		if text, ok := modelTextToken(token); ok {
			token = text
		} else if strings.HasPrefix(token, "@@tool_call:") {
			collectedToolCalls = append(collectedToolCalls, parseToolCallToken(token))
			return nil
		} else if strings.HasPrefix(token, "@@tool_result:") {
			collectedToolResults = append(collectedToolResults, parseToolResultToken(token).record())
			return nil
		} else if name := parseAnsweringModelToken(token); name != nil {
			answeredModel = name
			return nil
		} else if strings.HasPrefix(token, "@@") {
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
	if turnErr == nil {
		s.hearthAck(time.Now())
	}

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
		// Surface a redacted provider/runtime error — opaque 500s hide DeepSeek
		// tool-name rejects and similar recoverable chat failures.
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": redactStreamError(turnErr),
		})
		return
	}

	reply := responseText.String()
	hasTools := len(collectedToolCalls) > 0 || len(collectedToolResults) > 0
	if reply == "" && hasTools {
		reply = "*(Used tools — see process.)*"
	}
	if s.sessions != nil && (reply != "" || hasTools) {
		if _, ok, _ := s.sessions.Get(sid); !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
			return
		}
		var calls any = []any{}
		var results any = []any{}
		if len(collectedToolCalls) > 0 {
			calls = collectedToolCalls
		}
		if len(collectedToolResults) > 0 {
			results = collectedToolResults
		}
		if _, err := s.sessions.AddMessageFull(sid, "assistant", reply, nil, calls, results, firstNonEmptyPtr(answeredModel, sessModel), nil, nil); err != nil {
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
			s.mirrorDesktopReply(sess, reply)
		}
	}

	out := reply
	if out == "" {
		out = "I finished that step without a reply I can show here. Send continue if you want me to keep going."
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
