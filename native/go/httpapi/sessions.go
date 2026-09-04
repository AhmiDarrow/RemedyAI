package httpapi

import (
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// ChatSession matches memory/store.py chat_sessions + session_to_public JSON.
type ChatSession struct {
	ID             string  `json:"id"`
	Title          string  `json:"title"`
	Model          *string `json:"model"`
	Agent          *string `json:"agent"`
	ProjectPath    *string `json:"project_path"`
	LLMProvider    *string `json:"llm_provider"`
	MessageCount   int     `json:"message_count"`
	OriginChannel  *string `json:"origin_channel"`
	ExternalChatID *string `json:"external_chat_id"`
	ExternalUser   *string `json:"external_user"`
	CreatedAt      *string `json:"created_at"`
	UpdatedAt      *string `json:"updated_at"`
}

type createSessionRequest struct {
	Title         string  `json:"title"`
	Model         *string `json:"model"`
	Agent         *string `json:"agent"`
	ProjectPath   *string `json:"project_path"`
	LLMProvider   *string `json:"llm_provider"`
	OriginChannel *string `json:"origin_channel"`
}

// optionalString distinguishes JSON null / omitted / string for PATCH.
type optionalString struct {
	Set   bool
	Value *string
}

func (o *optionalString) UnmarshalJSON(b []byte) error {
	o.Set = true
	if string(b) == "null" {
		o.Value = nil
		return nil
	}
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return err
	}
	o.Value = &s
	return nil
}

type updateSessionRequest struct {
	Title       optionalString `json:"title"`
	Model       optionalString `json:"model"`
	Agent       optionalString `json:"agent"`
	ProjectPath optionalString `json:"project_path"`
}

const chatSessionsSchema = `
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Session',
    model TEXT,
    agent TEXT,
    project_path TEXT,
    llm_provider TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    origin_channel TEXT,
    external_chat_id TEXT,
    external_user TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_origin
    ON chat_sessions(origin_channel, external_chat_id);
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    thinking TEXT,
    tool_calls TEXT NOT NULL DEFAULT '[]',
    tool_results TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    agent TEXT,
    tokens INTEGER,
    created_at TEXT NOT NULL,
    reverted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at);
`

type sessionStore struct {
	mu sync.Mutex
	db *sql.DB
}

func openSessionStore(path string) (*sessionStore, error) {
	if path == "" {
		path = ":memory:"
	} else if path != ":memory:" {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return nil, fmt.Errorf("session db dir: %w", err)
		}
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// modernc: single connection avoids concurrent-write races on one file.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(0)
	if _, err := db.Exec(`PRAGMA journal_mode=WAL`); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec(`PRAGMA synchronous=NORMAL`); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec(`PRAGMA busy_timeout=5000`); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec(`PRAGMA foreign_keys=ON`); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec(chatSessionsSchema); err != nil {
		_ = db.Close()
		return nil, err
	}
	return &sessionStore{db: db}, nil
}

func resolveDBPath(cfg Config) string {
	if strings.TrimSpace(cfg.DBPath) != "" {
		return cfg.DBPath
	}
	home := strings.TrimSpace(cfg.HomeDir)
	if home == "" {
		home = strings.TrimSpace(os.Getenv("REMEDY_HOME"))
	}
	if home == "" {
		userHome, err := os.UserHomeDir()
		if err != nil || userHome == "" {
			return ":memory:"
		}
		home = filepath.Join(userHome, ".remedy")
	}
	return filepath.Join(home, "memory.db")
}

func (s *sessionStore) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.db == nil {
		return nil
	}
	err := s.db.Close()
	s.db = nil
	return err
}

func nowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.999999999Z07:00")
}

func newSessionID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func normalizeProjectPath(raw *string) *string {
	if raw == nil {
		return nil
	}
	trimmed := strings.TrimSpace(*raw)
	if trimmed == "" || trimmed == "." || trimmed == "./" {
		return nil
	}
	return &trimmed
}

func nullableTrim(raw *string) *string {
	if raw == nil {
		return nil
	}
	trimmed := strings.TrimSpace(*raw)
	if trimmed == "" {
		return nil
	}
	return &trimmed
}

func (s *sessionStore) Create(req createSessionRequest) (ChatSession, error) {
	title := strings.TrimSpace(req.Title)
	if title == "" {
		title = "New Session"
	}
	now := nowISO()
	sess := ChatSession{
		ID:            newSessionID(),
		Title:         title,
		Model:         nullableTrim(req.Model),
		Agent:         nullableTrim(req.Agent),
		ProjectPath:   normalizeProjectPath(req.ProjectPath),
		LLMProvider:   nullableTrim(req.LLMProvider),
		MessageCount:  0,
		OriginChannel: nullableTrim(req.OriginChannel),
		CreatedAt:     &now,
		UpdatedAt:     &now,
	}
	if sess.LLMProvider != nil {
		lower := strings.ToLower(*sess.LLMProvider)
		sess.LLMProvider = &lower
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := s.db.Exec(
		`INSERT INTO chat_sessions (
			id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		sess.ID, sess.Title, nullStr(sess.Model), nullStr(sess.Agent),
		nullStr(sess.ProjectPath), nullStr(sess.LLMProvider), sess.MessageCount,
		nullStr(sess.OriginChannel), nullStr(sess.ExternalChatID), nullStr(sess.ExternalUser),
		*sess.CreatedAt, *sess.UpdatedAt,
	)
	if err != nil {
		return ChatSession{}, err
	}
	return sess, nil
}

func (s *sessionStore) Get(id string) (ChatSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	row := s.db.QueryRow(
		`SELECT id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		 FROM chat_sessions WHERE id = ?`, id,
	)
	sess, err := scanSession(row)
	if errors.Is(err, sql.ErrNoRows) {
		return ChatSession{}, false, nil
	}
	if err != nil {
		return ChatSession{}, false, err
	}
	return sess, true, nil
}

func (s *sessionStore) List(limit, offset int) ([]ChatSession, error) {
	if limit <= 0 {
		limit = 100
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
		`SELECT id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		 FROM chat_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?`,
		limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]ChatSession, 0)
	for rows.Next() {
		sess, err := scanSession(rows)
		if err != nil {
			return nil, err
		}
		// Hive sessions are mother-private — never the owner sidebar.
		if strings.HasPrefix(sess.ID, "hive_") {
			continue
		}
		out = append(out, sess)
	}
	return out, rows.Err()
}

func (s *sessionStore) Update(id string, req updateSessionRequest) (ChatSession, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	row := s.db.QueryRow(
		`SELECT id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		 FROM chat_sessions WHERE id = ?`, id,
	)
	sess, err := scanSession(row)
	if errors.Is(err, sql.ErrNoRows) {
		return ChatSession{}, false, nil
	}
	if err != nil {
		return ChatSession{}, false, err
	}

	sets := make([]string, 0, 5)
	args := make([]any, 0, 5)
	if req.Title.Set && req.Title.Value != nil {
		title := strings.TrimSpace(*req.Title.Value)
		if title == "" {
			title = "New Session"
		}
		sets = append(sets, "title = ?")
		args = append(args, title)
		sess.Title = title
	}
	if req.Model.Set && req.Model.Value != nil {
		sets = append(sets, "model = ?")
		args = append(args, *req.Model.Value)
		v := *req.Model.Value
		sess.Model = &v
	}
	if req.Agent.Set && req.Agent.Value != nil {
		sets = append(sets, "agent = ?")
		args = append(args, *req.Agent.Value)
		v := *req.Agent.Value
		sess.Agent = &v
	}
	if req.ProjectPath.Set {
		// project_path may be cleared to NULL (exclude_unset + null in Python).
		pp := normalizeProjectPath(req.ProjectPath.Value)
		sets = append(sets, "project_path = ?")
		args = append(args, nullStr(pp))
		sess.ProjectPath = pp
	}
	if len(sets) == 0 {
		return sess, true, nil
	}
	now := nowISO()
	sets = append(sets, "updated_at = ?")
	args = append(args, now)
	args = append(args, id)
	_, err = s.db.Exec(
		`UPDATE chat_sessions SET `+strings.Join(sets, ", ")+` WHERE id = ?`,
		args...,
	)
	if err != nil {
		return ChatSession{}, false, err
	}
	sess.UpdatedAt = &now
	return sess, true, nil
}

func (s *sessionStore) Delete(id string) (bool, error) {
	sid := strings.TrimSpace(id)
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.db.Exec(`DELETE FROM chat_messages WHERE session_id = ?`, sid); err != nil {
		return false, err
	}
	res, err := s.db.Exec(`DELETE FROM chat_sessions WHERE id = ?`, sid)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return false, err
	}
	return n > 0, nil
}

func (s *sessionStore) Count() (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var n int
	err := s.db.QueryRow(`SELECT COUNT(*) FROM chat_sessions`).Scan(&n)
	return n, err
}

type scannable interface {
	Scan(dest ...any) error
}

func scanSession(row scannable) (ChatSession, error) {
	var (
		sess                                   ChatSession
		model, agent, projectPath, llmProvider sql.NullString
		origin, extChat, extUser               sql.NullString
		created, updated                       string
	)
	err := row.Scan(
		&sess.ID, &sess.Title, &model, &agent, &projectPath, &llmProvider,
		&sess.MessageCount, &origin, &extChat, &extUser, &created, &updated,
	)
	if err != nil {
		return ChatSession{}, err
	}
	sess.Model = nullToPtr(model)
	sess.Agent = nullToPtr(agent)
	sess.ProjectPath = nullToPtr(projectPath)
	sess.LLMProvider = nullToPtr(llmProvider)
	sess.OriginChannel = nullToPtr(origin)
	sess.ExternalChatID = nullToPtr(extChat)
	sess.ExternalUser = nullToPtr(extUser)
	sess.CreatedAt = &created
	sess.UpdatedAt = &updated
	return sess, nil
}

func nullToPtr(ns sql.NullString) *string {
	if !ns.Valid {
		return nil
	}
	s := ns.String
	return &s
}

func nullStr(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func (s *Server) handleListSessions(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	limit := 100
	offset := 0
	if v := r.URL.Query().Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid limit"})
			return
		}
		limit = n
	}
	if v := r.URL.Query().Get("offset"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid offset"})
			return
		}
		offset = n
	}
	sessions, err := s.sessions.List(limit, offset)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"sessions": sessions,
		"offset":   offset,
		"limit":    limit,
		"has_more": len(sessions) >= limit,
	})
}

func (s *Server) handleCreateSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	var req createSessionRequest
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil && !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	sess, err := s.sessions.Create(req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	title := sess.Title
	count := sess.MessageCount
	s.publishSessionEvent(SessionEvent{
		Type:          "session_created",
		SessionID:     sess.ID,
		OriginChannel: sess.OriginChannel,
		Title:         &title,
		MessageCount:  &count,
	})
	writeJSON(w, http.StatusOK, sess)
}

func (s *Server) handleGetSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	id := r.PathValue("id")
	sess, ok, err := s.sessions.Get(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	writeJSON(w, http.StatusOK, sess)
}

func (s *Server) handleUpdateSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	id := r.PathValue("id")
	var req updateSessionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	sess, ok, err := s.sessions.Update(id, req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	title := sess.Title
	count := sess.MessageCount
	s.publishSessionEvent(SessionEvent{
		Type:          "session_updated",
		SessionID:     sess.ID,
		OriginChannel: sess.OriginChannel,
		Title:         &title,
		MessageCount:  &count,
	})
	// PATCH returns a subset (matches Python crud.update_chat_session).
	writeJSON(w, http.StatusOK, map[string]any{
		"id":           sess.ID,
		"title":        sess.Title,
		"model":        sess.Model,
		"agent":        sess.Agent,
		"project_path": sess.ProjectPath,
		"llm_provider": sess.LLMProvider,
		"updated_at":   sess.UpdatedAt,
	})
}

func (s *Server) handleDeleteSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	id := strings.TrimSpace(r.PathValue("id"))
	// Stop in-flight turn + drop claim before removing the row (Python crud parity).
	if s.claims != nil {
		s.claims.Abort(id, nil, nil)
		s.claims.Release(id, nil)
		// Wait for the detached turn to finish DB writes before DELETE.
		s.claims.WaitSessionTurn(id)
	}
	ok, err := s.sessions.Delete(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	s.publishSessionEvent(SessionEvent{
		Type:      "session_deleted",
		SessionID: id,
	})
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     "deleted",
		"session_id": id,
		"cascade":    map[string]any{},
	})
}
