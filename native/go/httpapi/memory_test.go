package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

const memoryTestToken = "tok-memory-test-not-a-secret"

func newMemoryTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   memoryTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doMemoryJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+memoryTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload
}

func seedMemoryEntry(t *testing.T, s *Server, id, title, content, entryType, sessionID string, meta map[string]any) {
	t.Helper()
	s.sessions.mu.Lock()
	defer s.sessions.mu.Unlock()
	if err := s.sessions.ensurePartnerMemorySchema(); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	metaRaw := "{}"
	if meta != nil {
		b, _ := json.Marshal(meta)
		metaRaw = string(b)
	}
	_, err := s.sessions.db.Exec(
		`INSERT INTO memory_entries
		 (id, entry_type, title, content, tags, metadata, created_at, updated_at, session_id, importance)
		 VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, 0.7)`,
		id, entryType, title, content, metaRaw, now, now, sessionID,
	)
	if err != nil {
		t.Fatal(err)
	}
}

func seedProfileFacts(t *testing.T, s *Server, facts []map[string]any) {
	t.Helper()
	profile := map[string]any{
		"user_id": "default",
		"facts":   facts,
		"traits":  map[string]any{},
	}
	raw, _ := json.MarshalIndent(profile, "", "  ")
	s.sessions.mu.Lock()
	defer s.sessions.mu.Unlock()
	if err := s.sessions.ensurePartnerMemorySchema(); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.sessions.db.Exec(
		`INSERT OR REPLACE INTO user_profile (user_id, profile_json, updated_at) VALUES (?, ?, ?)`,
		"default", string(raw), now,
	)
	if err != nil {
		t.Fatal(err)
	}
}

func TestMemorySearchRecentAndQuery(t *testing.T) {
	s, _ := newMemoryTestServer(t)
	seedMemoryEntry(t, s, "e1", "Coffee", "Partner likes oat milk lattes", "note", "sess-1", nil)
	seedMemoryEntry(t, s, "e2", "Hive noise", "ignore me", "note", "hive_abc", nil)
	seedMemoryEntry(t, s, "e3", "System beat", "heartbeat", "system", "sess-1", nil)

	code, body := doMemoryJSON(t, s, http.MethodGet, "/api/memory/search?limit=10", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	results, _ := body["results"].([]any)
	if len(results) != 1 {
		t.Fatalf("recent results=%v", body["results"])
	}

	code, body = doMemoryJSON(t, s, http.MethodGet, "/api/memory/search?query=latte&limit=10", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	results, _ = body["results"].([]any)
	if len(results) != 1 {
		t.Fatalf("query results=%v", body["results"])
	}
	row, _ := results[0].(map[string]any)
	if row["title"] != "Coffee" {
		t.Fatalf("row=%v", row)
	}
}

func TestMemoryFactsFiltersHiveAndWorkResidue(t *testing.T) {
	s, _ := newMemoryTestServer(t)
	seedProfileFacts(t, s, []map[string]any{
		{"fact": "Name is Ahmi", "category": "personal", "authority": "owner", "source": "explicit", "inferred": false},
		{"fact": "Hive secret", "category": "general", "authority": "hive", "source": "agent", "inferred": true},
		{"fact": "Continue: C:\\Users\\Administrator\\Old-Remedy review", "category": "workflow", "authority": "agent", "source": "soul_dream", "inferred": true},
	})
	code, body := doMemoryJSON(t, s, http.MethodGet, "/api/memory/facts?limit=12", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	facts, _ := body["facts"].([]any)
	if len(facts) != 1 {
		t.Fatalf("facts=%v", body["facts"])
	}
	row, _ := facts[0].(map[string]any)
	if row["text"] != "Name is Ahmi" || row["authority"] != "owner" {
		t.Fatalf("row=%v", row)
	}
}

func TestMemoryPersonaWipeRequiresConfirm(t *testing.T) {
	s, home := newMemoryTestServer(t)
	seedProfileFacts(t, s, []map[string]any{
		{"fact": "Name is Ahmi", "category": "personal", "authority": "owner", "source": "explicit", "inferred": false},
	})
	seedMemoryEntry(t, s, "n1", "Note", "remember this", "note", "sess-1", nil)
	_ = writeJSONAtomic(filepath.Join(home, "life_goals.json"), map[string]any{"goals": []any{}})
	_ = writeJSONAtomic(filepath.Join(home, "soul", "field.json"), map[string]any{
		"identity_name": "Remedy", "identity_gender": "female",
		"pledges": []any{"Stay with: continue the build"},
	})

	code, body := doMemoryJSON(t, s, http.MethodPost, "/api/memory/persona-wipe", map[string]any{"confirm": "nope"})
	if code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%v", code, body)
	}

	code, body = doMemoryJSON(t, s, http.MethodPost, "/api/memory/persona-wipe", map[string]any{"confirm": "WIPE"})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%v", code, body)
	}
	if body["status"] != "wiped" || body["ok"] != true {
		t.Fatalf("body=%v", body)
	}
	if body["profile_reset"] != true {
		t.Fatalf("profile_reset=%v", body["profile_reset"])
	}
	if body["life_goals_removed"] != true {
		t.Fatalf("life_goals_removed=%v", body["life_goals_removed"])
	}

	code, factsBody := doMemoryJSON(t, s, http.MethodGet, "/api/memory/facts", nil)
	if code != http.StatusOK {
		t.Fatalf("facts status=%d", code)
	}
	facts, _ := factsBody["facts"].([]any)
	if len(facts) != 0 {
		t.Fatalf("facts after wipe=%v", facts)
	}
	code, searchBody := doMemoryJSON(t, s, http.MethodGet, "/api/memory/search", nil)
	if code != http.StatusOK {
		t.Fatalf("search status=%d", code)
	}
	results, _ := searchBody["results"].([]any)
	if len(results) != 0 {
		t.Fatalf("notes after wipe=%v", results)
	}
}
