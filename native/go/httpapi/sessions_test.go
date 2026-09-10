package httpapi

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"path/filepath"
	"testing"
	"time"
)

func TestSessionsCRUD(t *testing.T) {
	const token = "test-token-not-a-secret-16"
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	base, shutdown := startTestServer(t, Config{
		Token:   token,
		Version: "0.50.2",
		DBPath:  dbPath,
	})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	auth := func(req *http.Request) {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	// Unauthenticated list → 401
	resp, err := client.Get(base + "/api/sessions")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauth list status = %d", resp.StatusCode)
	}

	// Create
	body := bytes.NewBufferString(`{"title":"Hello","model":"grok-4","llm_provider":"XAI","agent":"default"}`)
	req, err := http.NewRequest(http.MethodPost, base+"/api/sessions", body)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	req.Header.Set("Content-Type", "application/json")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create status = %d body=%s", resp.StatusCode, raw)
	}
	var created ChatSession
	if err := json.Unmarshal(raw, &created); err != nil {
		t.Fatal(err)
	}
	if created.ID == "" || created.Title != "Hello" {
		t.Fatalf("created = %#v", created)
	}
	if created.Model == nil || *created.Model != "grok-4" {
		t.Fatalf("model = %#v", created.Model)
	}
	if created.LLMProvider == nil || *created.LLMProvider != "xai" {
		t.Fatalf("llm_provider = %#v (want lowercased xai)", created.LLMProvider)
	}
	if created.MessageCount != 0 || created.CreatedAt == nil || created.UpdatedAt == nil {
		t.Fatalf("created timestamps/count = %#v", created)
	}

	// List
	req, err = http.NewRequest(http.MethodGet, base+"/api/sessions?limit=50&offset=0", nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list status = %d body=%s", resp.StatusCode, raw)
	}
	var listed struct {
		Sessions []ChatSession `json:"sessions"`
		Offset   int           `json:"offset"`
		Limit    int           `json:"limit"`
		HasMore  bool          `json:"has_more"`
	}
	if err := json.Unmarshal(raw, &listed); err != nil {
		t.Fatal(err)
	}
	if listed.Offset != 0 || listed.Limit != 50 || listed.HasMore {
		t.Fatalf("list meta = %#v", listed)
	}
	if len(listed.Sessions) != 1 || listed.Sessions[0].ID != created.ID {
		t.Fatalf("list sessions = %#v", listed.Sessions)
	}

	// Get
	req, err = http.NewRequest(http.MethodGet, base+"/api/sessions/"+created.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get status = %d body=%s", resp.StatusCode, raw)
	}
	var got ChatSession
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got.ID != created.ID || got.Title != "Hello" {
		t.Fatalf("get = %#v", got)
	}

	// PATCH title + clear project_path
	patchBody := bytes.NewBufferString(`{"title":"Renamed","project_path":null}`)
	req, err = http.NewRequest(http.MethodPatch, base+"/api/sessions/"+created.ID, patchBody)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	req.Header.Set("Content-Type", "application/json")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("patch status = %d body=%s", resp.StatusCode, raw)
	}
	var patched map[string]any
	if err := json.Unmarshal(raw, &patched); err != nil {
		t.Fatal(err)
	}
	if patched["title"] != "Renamed" {
		t.Fatalf("patched title = %#v", patched["title"])
	}
	if patched["project_path"] != nil {
		t.Fatalf("project_path should be null, got %#v", patched["project_path"])
	}
	for _, key := range []string{"id", "model", "agent", "llm_provider", "updated_at"} {
		if _, ok := patched[key]; !ok {
			t.Fatalf("PATCH missing %s in %#v", key, patched)
		}
	}
	if _, ok := patched["message_count"]; ok {
		t.Fatalf("PATCH should omit message_count: %#v", patched)
	}

	// 404 get
	req, err = http.NewRequest(http.MethodGet, base+"/api/sessions/missing-id", nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("missing get status = %d body=%s", resp.StatusCode, raw)
	}

	// Delete
	req, err = http.NewRequest(http.MethodDelete, base+"/api/sessions/"+created.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("delete status = %d body=%s", resp.StatusCode, raw)
	}
	var deleted map[string]any
	if err := json.Unmarshal(raw, &deleted); err != nil {
		t.Fatal(err)
	}
	if deleted["status"] != "deleted" || deleted["session_id"] != created.ID {
		t.Fatalf("delete body = %#v", deleted)
	}

	// Delete again → 404
	req, err = http.NewRequest(http.MethodDelete, base+"/api/sessions/"+created.ID, nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("second delete status = %d", resp.StatusCode)
	}

	// Empty list
	req, err = http.NewRequest(http.MethodGet, base+"/api/sessions", nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if err := json.Unmarshal(raw, &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed.Sessions) != 0 {
		t.Fatalf("expected empty list, got %#v", listed.Sessions)
	}
}

func TestSessionsHiveFilteredAndXRemedyToken(t *testing.T) {
	const token = "test-token-not-a-secret-16"
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	store, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	now := nowISO()
	_, err = store.db.Exec(
		`INSERT INTO chat_sessions (
			id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		) VALUES (?, ?, NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL, ?, ?)`,
		"hive_secret", "Hive", now, now,
	)
	if err != nil {
		t.Fatal(err)
	}
	_, err = store.db.Exec(
		`INSERT INTO chat_sessions (
			id, title, model, agent, project_path, llm_provider, message_count,
			origin_channel, external_chat_id, external_user, created_at, updated_at
		) VALUES (?, ?, NULL, NULL, NULL, NULL, 0, NULL, NULL, NULL, ?, ?)`,
		"owner-session", "Mine", now, now,
	)
	if err != nil {
		t.Fatal(err)
	}
	_ = store.Close()

	base, shutdown := startTestServer(t, Config{
		Token:   token,
		Version: "0.50.2",
		DBPath:  dbPath,
	})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}

	req, err := http.NewRequest(http.MethodGet, base+"/api/sessions", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("X-Remedy-Token", token)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list status = %d body=%s", resp.StatusCode, raw)
	}
	var listed struct {
		Sessions []ChatSession `json:"sessions"`
	}
	if err := json.Unmarshal(raw, &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed.Sessions) != 1 || listed.Sessions[0].ID != "owner-session" {
		t.Fatalf("hive filter failed: %#v", listed.Sessions)
	}
}

func TestSessionStorePersistsAcrossOpen(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	store, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	model := "m1"
	created, err := store.Create(createSessionRequest{Title: "Persist", Model: &model})
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	store2, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer store2.Close()
	got, ok, err := store2.Get(created.ID)
	if err != nil || !ok {
		t.Fatalf("get after reopen: ok=%v err=%v", ok, err)
	}
	if got.Title != "Persist" || got.Model == nil || *got.Model != "m1" {
		t.Fatalf("persisted = %#v", got)
	}
}

// Homes created before request_id existed must still open. CREATE INDEX on
// that column used to run in the same Exec as CREATE TABLE IF NOT EXISTS, so
// an old chat_messages table made serve exit before the ALTER ran — Desktop
// stayed on "Connecting to local server".
func TestSessionStoreOpensPreRequestIDDatabase(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`
CREATE TABLE chat_sessions (
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
CREATE TABLE chat_messages (
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
CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);
`)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}

	store, err := openSessionStore(dbPath)
	if err != nil {
		t.Fatalf("open pre-request_id database: %v", err)
	}
	defer store.Close()
	sess, err := store.Create(createSessionRequest{Title: "after migrate"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AddMessage(sess.ID, "user", "hello", nil, nil); err != nil {
		t.Fatal(err)
	}
}
