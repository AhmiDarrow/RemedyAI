package httpapi

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

const memoryPersonaWipeConfirm = "WIPE"

// Partner-memory tables shared with Python MemoryStore (same memory.db).
const partnerMemorySchema = `
CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT,
    importance REAL NOT NULL DEFAULT 0.5
);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_entries(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);
CREATE TABLE IF NOT EXISTS user_profile (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    confidence REAL NOT NULL DEFAULT 0.7,
    source TEXT NOT NULL DEFAULT 'inferred',
    created_at TEXT NOT NULL,
    last_referenced TEXT NOT NULL,
    reference_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id);
`

func (s *sessionStore) ensurePartnerMemorySchema() error {
	if s == nil || s.db == nil {
		return sql.ErrConnDone
	}
	_, err := s.db.Exec(partnerMemorySchema)
	return err
}

// looksLikeWorkResidue is the one residue rule, shared with the native soul.
func looksLikeWorkResidue(text string) bool { return soul.LooksLikeWorkResidue(text) }

func isHiveSessionID(sessionID string) bool {
	return strings.HasPrefix(strings.TrimSpace(sessionID), "hive_")
}

func isHiveMemoryHit(authority, sessionID string) bool {
	if isHiveSessionID(sessionID) {
		return true
	}
	return strings.EqualFold(strings.TrimSpace(authority), "hive")
}

func factIsWorkResidue(text, authority, source string, inferred bool) bool {
	if !looksLikeWorkResidue(text) {
		return false
	}
	if strings.EqualFold(strings.TrimSpace(authority), "owner") {
		return false
	}
	src := strings.ToLower(strings.TrimSpace(source))
	if src == "explicit" || src == "user" || src == "owner" {
		return false
	}
	switch src {
	case "inferred", "soul_dream", "agent", "tool", "web", "browser", "http", "":
		return true
	}
	return inferred
}

type memoryEntryRow struct {
	ID         string
	Title      string
	Content    string
	EntryType  string
	Importance float64
	CreatedAt  string
	SessionID  string
	Metadata   map[string]any
}

func (s *sessionStore) listRecentMemory(limit int) ([]memoryEntryRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return nil, err
	}
	rows, err := s.db.Query(
		`SELECT id, title, content, entry_type, importance, created_at,
		        COALESCE(session_id, ''), COALESCE(metadata, '{}')
		 FROM memory_entries
		 ORDER BY created_at DESC LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanMemoryRows(rows)
}

func (s *sessionStore) searchMemoryLIKE(query string, limit int) ([]memoryEntryRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return nil, err
	}
	q := strings.TrimSpace(query)
	if q == "" {
		return nil, nil
	}
	safe := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(q)
	like := "%" + safe + "%"
	rows, err := s.db.Query(
		`SELECT id, title, content, entry_type, importance, created_at,
		        COALESCE(session_id, ''), COALESCE(metadata, '{}')
		 FROM memory_entries
		 WHERE title LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\'
		 ORDER BY created_at DESC LIMIT ?`,
		like, like, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanMemoryRows(rows)
}

// searchMemoryWords finds notes that contain any word of the query and puts
// the ones matching the most words first. A question such as "what did we
// decide about the garden fence" then finds a note that says "garden fence"
// without containing the whole question as one phrase.
func (s *sessionStore) searchMemoryWords(query string, limit int) ([]memoryEntryRow, error) {
	words := memoryQueryWords(query)
	if len(words) == 0 {
		return nil, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return nil, err
	}
	esc := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
	const match = `(title LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\')`
	var score, where []string
	var likes []any
	for _, w := range words {
		like := "%" + esc.Replace(w) + "%"
		score = append(score, `(CASE WHEN `+match+` THEN 1 ELSE 0 END)`)
		where = append(where, match)
		likes = append(likes, like, like)
	}
	// The word patterns are bound twice: once for the score, once for the filter.
	args := append(append([]any{}, likes...), likes...)
	args = append(args, limit)
	rows, err := s.db.Query(
		`SELECT id, title, content, entry_type, importance, created_at,
		        COALESCE(session_id, ''), COALESCE(metadata, '{}')
		 FROM (SELECT *, `+strings.Join(score, " + ")+` AS hits FROM memory_entries WHERE `+strings.Join(where, " OR ")+`)
		 ORDER BY hits DESC, created_at DESC LIMIT ?`,
		args...,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanMemoryRows(rows)
}

// memoryQueryWords are the distinct words of a query worth matching: two or
// more characters, not a filler word, at most eight.
func memoryQueryWords(query string) []string {
	filler := map[string]bool{"the": true, "and": true, "for": true, "what": true, "did": true, "was": true,
		"are": true, "you": true, "about": true, "with": true, "that": true, "this": true, "have": true,
		"how": true, "our": true, "is": true, "of": true, "to": true, "in": true, "on": true, "we": true,
		"it": true, "do": true, "my": true, "me": true, "an": true}
	seen := map[string]bool{}
	var out []string
	for _, w := range strings.FieldsFunc(strings.ToLower(query), func(r rune) bool {
		return !unicode.IsLetter(r) && !unicode.IsDigit(r)
	}) {
		if len([]rune(w)) < 2 || filler[w] || seen[w] {
			continue
		}
		seen[w] = true
		out = append(out, w)
		if len(out) == 8 {
			break
		}
	}
	return out
}

func scanMemoryRows(rows *sql.Rows) ([]memoryEntryRow, error) {
	out := make([]memoryEntryRow, 0, 16)
	for rows.Next() {
		var (
			row     memoryEntryRow
			metaRaw string
		)
		if err := rows.Scan(
			&row.ID, &row.Title, &row.Content, &row.EntryType,
			&row.Importance, &row.CreatedAt, &row.SessionID, &metaRaw,
		); err != nil {
			return nil, err
		}
		meta := map[string]any{}
		_ = json.Unmarshal([]byte(metaRaw), &meta)
		row.Metadata = meta
		out = append(out, row)
	}
	return out, rows.Err()
}

func (s *sessionStore) loadUserProfileJSON(userID string) (map[string]any, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return nil, err
	}
	var raw string
	err := s.db.QueryRow(
		`SELECT profile_json FROM user_profile WHERE user_id = ?`, userID,
	).Scan(&raw)
	if err == sql.ErrNoRows {
		return map[string]any{"user_id": userID, "facts": []any{}}, nil
	}
	if err != nil {
		return nil, err
	}
	var data map[string]any
	if err := json.Unmarshal([]byte(raw), &data); err != nil || data == nil {
		return map[string]any{"user_id": userID, "facts": []any{}}, nil
	}
	return data, nil
}

func (s *sessionStore) saveUserProfileJSON(userID string, profile map[string]any) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return err
	}
	if profile == nil {
		profile = map[string]any{}
	}
	profile["user_id"] = userID
	profile["facts"] = []any{}
	profile["traits"] = map[string]any{}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	raw, err := json.MarshalIndent(profile, "", "  ")
	if err != nil {
		return err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.Exec(
		`INSERT OR REPLACE INTO user_profile (user_id, profile_json, updated_at) VALUES (?, ?, ?)`,
		userID, string(raw), now,
	); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM user_facts WHERE user_id = ?`, userID); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *sessionStore) deleteMemoryByType(entryType string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return 0, err
	}
	res, err := s.db.Exec(`DELETE FROM memory_entries WHERE entry_type = ?`, entryType)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

func keepMemoryEntry(row memoryEntryRow) bool {
	if isHiveSessionID(row.SessionID) {
		return false
	}
	auth, _ := row.Metadata["authority"].(string)
	return !isHiveMemoryHit(auth, row.SessionID)
}

func (s *Server) handleMemorySearch(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"detail": "Memory store not available",
		})
		return
	}
	q := strings.TrimSpace(r.URL.Query().Get("query"))
	limit := 10
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 50 {
		limit = 50
	}
	offset := 0
	if raw := strings.TrimSpace(r.URL.Query().Get("offset")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 {
			offset = n
		}
	}

	var (
		rows    []memoryEntryRow
		err     error
		hasMore bool
	)
	if q == "" {
		fetchN := (offset + limit + 1) * 3
		if fetchN < 30 {
			fetchN = 30
		}
		raw, e := s.sessions.listRecentMemory(fetchN)
		err = e
		kept := make([]memoryEntryRow, 0, limit)
		skipped := 0
		for _, row := range raw {
			if strings.EqualFold(row.EntryType, "system") {
				continue
			}
			if !keepMemoryEntry(row) {
				continue
			}
			if skipped < offset {
				skipped++
				continue
			}
			if len(kept) >= limit {
				hasMore = true
				break
			}
			kept = append(kept, row)
		}
		rows = kept
	} else {
		raw, e := s.sessions.searchMemoryLIKE(q, offset+limit+1)
		err = e
		kept := make([]memoryEntryRow, 0, limit)
		skipped := 0
		for _, row := range raw {
			if !keepMemoryEntry(row) {
				continue
			}
			if skipped < offset {
				skipped++
				continue
			}
			if len(kept) >= limit {
				hasMore = true
				break
			}
			kept = append(kept, row)
		}
		rows = kept
	}
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "memory search failed",
		})
		return
	}
	results := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		content := row.Content
		if len(content) > 300 {
			content = content[:300]
		}
		var created any
		if strings.TrimSpace(row.CreatedAt) != "" {
			created = row.CreatedAt
		}
		results = append(results, map[string]any{
			"id":         row.ID,
			"title":      row.Title,
			"content":    content,
			"type":       row.EntryType,
			"importance": row.Importance,
			"created_at": created,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"query":    q,
		"results":  results,
		"has_more": hasMore,
		"offset":   offset,
		"limit":    limit,
	})
}

func (s *Server) handleMemoryFacts(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"detail": "Memory store not available",
		})
		return
	}
	limit := 20
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 50 {
		limit = 50
	}
	offset := 0
	if raw := strings.TrimSpace(r.URL.Query().Get("offset")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n > 0 {
			offset = n
		}
	}
	profile, err := s.sessions.loadUserProfileJSON("default")
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "memory facts failed",
		})
		return
	}
	factsOut := make([]map[string]any, 0, limit)
	rawFacts, _ := profile["facts"].([]any)
	skipped := 0
	hasMore := false
	for _, item := range rawFacts {
		f, ok := item.(map[string]any)
		if !ok {
			continue
		}
		auth := asString(f["authority"])
		if strings.EqualFold(auth, "hive") {
			continue
		}
		text := strings.TrimSpace(asString(f["fact"]))
		if text == "" {
			continue
		}
		inferred := true
		switch v := f["inferred"].(type) {
		case bool:
			inferred = v
		}
		if factIsWorkResidue(text, auth, asString(f["source"]), inferred) {
			continue
		}
		if skipped < offset {
			skipped++
			continue
		}
		if len(factsOut) >= limit {
			hasMore = true
			break
		}
		if len(text) > 300 {
			text = text[:300]
		}
		cat := strings.TrimSpace(asString(f["category"]))
		if cat == "" {
			cat = "general"
		}
		if auth == "" {
			auth = "agent"
		}
		id := strings.TrimSpace(asString(f["id"]))
		if id == "" {
			id = strings.TrimSpace(asString(f["key"]))
		}
		factsOut = append(factsOut, map[string]any{
			"id":        id,
			"title":     cat,
			"text":      text,
			"category":  cat,
			"authority": auth,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"facts":    factsOut,
		"has_more": hasMore,
		"offset":   offset,
		"limit":    limit,
	})
}

func (s *Server) handleMemoryPersonaWipe(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Confirm string `json:"confirm"`
	}
	dec := json.NewDecoder(r.Body)
	_ = dec.Decode(&body)
	if strings.ToUpper(strings.TrimSpace(body.Confirm)) != memoryPersonaWipeConfirm {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Type WIPE to confirm persona wipe",
		})
		return
	}
	home := s.remedyHomeDir()
	stats := map[string]any{
		"status":              "wiped",
		"ok":                  true,
		"profile_reset":       false,
		"user_fact_entries":   0,
		"soul_reset":          false,
		"partner_state_files": 0,
		"life_goals_removed":  false,
		"note_entries":        0,
		"session_entries":     0,
		"cas_tombstoned":      0,
		"persona_files":       0,
	}
	if s.sessions != nil {
		if err := s.sessions.saveUserProfileJSON("default", map[string]any{
			"user_id": "default",
		}); err == nil {
			stats["profile_reset"] = true
		}
		if n, err := s.sessions.deleteMemoryByType("user_fact"); err == nil {
			stats["user_fact_entries"] = n
		}
		if n, err := s.sessions.deleteMemoryByType("note"); err == nil {
			stats["note_entries"] = n
		}
		if n, err := s.sessions.deleteMemoryByType("session"); err == nil {
			stats["session_entries"] = n
		}
	}
	stats["soul_reset"] = resetSoulPersonaFiles(home)
	stats["partner_state_files"] = wipePartnerStateFiles(home)
	stats["life_goals_removed"] = wipeLifeGoalsFile(home)
	stats["cas_tombstoned"] = wipeCASPersonhood(home)
	stats["persona_files"] = wipePersonaFiles(home)
	writeJSON(w, http.StatusOK, stats)
}

// resetSoulPersonaFiles keeps the partner's name, presentation and vow and
// forgets the rest, under the field's lock (no cache can re-save the old one).
func resetSoulPersonaFiles(home string) bool {
	env := mindEnv(home)
	env.Home = home
	return soul.Reset(env) == nil
}

func wipePartnerStateFiles(home string) int {
	root := filepath.Join(home, "partner_state")
	entries, err := os.ReadDir(root)
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
			continue
		}
		if err := os.Remove(filepath.Join(root, e.Name())); err == nil {
			n++
		}
	}
	return n
}

func wipeLifeGoalsFile(home string) bool {
	path := filepath.Join(home, "life_goals.json")
	if _, err := os.Stat(path); err != nil {
		return false
	}
	return os.Remove(path) == nil
}

func wipeCASPersonhood(home string) int {
	// Best-effort: tombstone fact/life rows in eternal CAS sqlite if present.
	dbPath := filepath.Join(home, "cas", "eternal.db")
	if _, err := os.Stat(dbPath); err != nil {
		return 0
	}
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return 0
	}
	defer db.Close()
	res, err := db.Exec(
		`UPDATE objects SET tombstone = 1
		 WHERE kind IN ('fact','life') AND COALESCE(tombstone, 0) = 0`,
	)
	if err != nil {
		return 0
	}
	n, _ := res.RowsAffected()
	return int(n)
}

func wipePersonaFiles(home string) int {
	n := 0
	paths := []string{
		filepath.Join(home, "soul", "embeddings.json"),
		filepath.Join(home, "soul", "proprioception.json"),
		filepath.Join(home, "soul", "vigil.json"),
		filepath.Join(home, "soul", "vigil_journal.jsonl"),
		filepath.Join(home, "soul", "hearth.json"),
		filepath.Join(home, "soul", "dream.json"),
		filepath.Join(home, "alive.json"),
		filepath.Join(home, "facts.jsonl"),
		filepath.Join(home, "myelin", "ledger.json"),
	}
	// Under each file's lock, so a running tick cannot re-save what was wiped.
	for _, p := range paths {
		if removed, _ := soul.RemoveState(p); removed {
			n++
		}
	}
	sheaths := filepath.Join(home, "myelin", "sheaths")
	if entries, err := os.ReadDir(sheaths); err == nil {
		for _, e := range entries {
			if e.IsDir() {
				continue
			}
			if os.Remove(filepath.Join(sheaths, e.Name())) == nil {
				n++
			}
		}
	}
	crystal := filepath.Join(home, "time_crystal")
	if entries, err := os.ReadDir(crystal); err == nil {
		for _, e := range entries {
			if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
				continue
			}
			if os.Remove(filepath.Join(crystal, e.Name())) == nil {
				n++
			}
		}
	}
	lifeDir := filepath.Join(home, "life")
	_ = filepath.WalkDir(lifeDir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d == nil || d.IsDir() {
			return nil
		}
		if os.Remove(path) == nil {
			n++
		}
		return nil
	})
	return n
}
