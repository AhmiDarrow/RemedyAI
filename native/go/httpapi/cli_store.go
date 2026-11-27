package httpapi

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

// CLIStore is memory.db opened the way Python's MemoryStore opens it, for the
// `remedy memory|user|session|handoff` commands. Every table, trigger and
// JSON column is written exactly as MemoryStore writes it, so Python, the
// running runtime and the CLI keep reading each other's rows.
type CLIStore struct {
	db   *sql.DB
	path string
}

// cliStorePythonSchema is MemoryStore._SCHEMA, verbatim.
const cliStorePythonSchema = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

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

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    title, content, tags,
    content=memory_entries,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS handoff_notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    from_session TEXT,
    to_session TEXT,
    context_summary TEXT,
    action_items TEXT NOT NULL DEFAULT '[]',
    decisions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    skills_created INTEGER NOT NULL DEFAULT 0,
    skills_refined INTEGER NOT NULL DEFAULT 0,
    key_decisions TEXT NOT NULL DEFAULT '[]',
    open_items TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_entries(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_entries(importance);
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
    reference_count INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES user_profile(user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_facts_user_cat ON user_facts(user_id, category);

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
-- idx_chat_sessions_origin is created in _migrate_schema after columns exist
-- (CREATE INDEX here breaks older DBs that lack origin_channel until ALTER).

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
    reactions TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS user_traits (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'inferred',
    first_observed TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, key),
    FOREIGN KEY (user_id) REFERENCES user_profile(user_id)
);
`

// CLIMemoryEntryTypes are MemoryEntryType's values, in declaration order.
var CLIMemoryEntryTypes = []string{"note", "handoff", "session", "user_fact", "skill_learned", "task_result", "conversation", "system"}

// CLIValidEntryType reports whether t is a MemoryEntryType value.
func CLIValidEntryType(t string) bool {
	for _, v := range CLIMemoryEntryTypes {
		if v == t {
			return true
		}
	}
	return false
}

// OpenCLIStore opens (creating if needed) memory.db as MemoryStore.initialize
// does: same pragmas, the same schema script, the same additive migrations.
func OpenCLIStore(dbPath string) (*CLIStore, error) {
	path, err := filepath.Abs(dbPath)
	if err != nil {
		return nil, err
	}
	if real, rerr := filepath.EvalSymlinks(path); rerr == nil {
		path = real
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	// One connection: pragmas such as foreign_keys are per connection.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(0)
	for _, p := range []string{
		"PRAGMA busy_timeout=5000",
		"PRAGMA journal_mode=WAL",
		"PRAGMA synchronous=NORMAL",
		"PRAGMA temp_store=MEMORY",
		"PRAGMA cache_size=-65536",
		"PRAGMA mmap_size=268435456",
	} {
		if _, err := db.Exec(p); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("open %s: %w", path, err)
		}
	}
	if _, err := db.Exec(cliStorePythonSchema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("memory schema: %w", err)
	}
	s := &CLIStore{db: db, path: path}
	if err := s.migrate(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("memory migrate: %w", err)
	}
	return s, nil
}

// Close closes the database.
func (s *CLIStore) Close() error { return s.db.Close() }

// Path is the resolved memory.db path.
func (s *CLIStore) Path() string { return s.path }

func (s *CLIStore) columns(table string) (map[string]bool, error) {
	rows, err := s.db.Query(`PRAGMA table_info(` + table + `)`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var (
			cid         int
			name, ctype string
			notnull, pk int
			dflt        sql.NullString
		)
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return nil, err
		}
		out[name] = true
	}
	return out, rows.Err()
}

// migrate is MemoryStore._migrate_schema.
func (s *CLIStore) migrate() error {
	cols, err := s.columns("chat_sessions")
	if err != nil {
		return err
	}
	if !cols["llm_provider"] {
		if _, err := s.db.Exec(`ALTER TABLE chat_sessions ADD COLUMN llm_provider TEXT`); err != nil {
			return err
		}
	}
	for _, c := range []string{"origin_channel", "external_chat_id", "external_user"} {
		if !cols[c] {
			if _, err := s.db.Exec(`ALTER TABLE chat_sessions ADD COLUMN ` + c + ` TEXT`); err != nil {
				return err
			}
		}
	}
	mcols, err := s.columns("chat_messages")
	if err != nil {
		return err
	}
	if !mcols["reactions"] {
		if _, err := s.db.Exec(`ALTER TABLE chat_messages ADD COLUMN reactions TEXT NOT NULL DEFAULT '{}'`); err != nil {
			return err
		}
	}
	_, err = s.db.Exec(`CREATE INDEX IF NOT EXISTS idx_chat_sessions_origin ON chat_sessions(origin_channel, external_chat_id)`)
	return err
}

// ── memory entries ─────────────────────────────────────────────────────────

// CLIMemoryEntry is one memory_entries row as Python's MemoryEntry reads it.
type CLIMemoryEntry struct {
	ID         string
	EntryType  string
	Title      string
	Content    string
	Tags       []string
	SessionID  *string
	Importance float64
	created    cliStoreTime
	updated    cliStoreTime
	meta       *cliStoreObj
}

// NewCLIMemoryEntry is MemoryEntry(...) with a fresh uuid4 id and now stamps.
func NewCLIMemoryEntry(entryType, title, content string, tags []string, importance float64) *CLIMemoryEntry {
	now := cliStoreNow()
	if tags == nil {
		tags = []string{}
	}
	return &CLIMemoryEntry{
		ID: uuid.NewString(), EntryType: entryType, Title: title, Content: content, Tags: tags,
		Importance: importance, created: cliStoreTime{T: now}, updated: cliStoreTime{T: now}, meta: newCLIStoreObj(),
	}
}

// CreatedISO is created_at.isoformat().
func (e *CLIMemoryEntry) CreatedISO() string { return e.created.iso(false) }

// Validate is the pydantic check MemoryEntry runs on construction.
func (e *CLIMemoryEntry) Validate() error {
	if !CLIValidEntryType(e.EntryType) {
		return fmt.Errorf("'%s' is not a valid MemoryEntryType", e.EntryType)
	}
	if e.Importance < 0 || e.Importance > 1 {
		return fmt.Errorf("1 validation error for MemoryEntry\nimportance\n  Input should be between 0 and 1 [input_value=%s]", cliStorePyFloat(e.Importance))
	}
	return nil
}

const cliStoreEntryCols = `id, entry_type, title, content, tags, metadata, created_at, updated_at, session_id, importance`

func cliStoreScanEntries(rows *sql.Rows) ([]*CLIMemoryEntry, error) {
	defer rows.Close()
	var out []*CLIMemoryEntry
	for rows.Next() {
		var (
			id, et, title, content, tags, meta, created, updated, sid sql.NullString
			imp                                                       sql.NullFloat64
		)
		if err := rows.Scan(&id, &et, &title, &content, &tags, &meta, &created, &updated, &sid, &imp); err != nil {
			return nil, err
		}
		// Rows this model cannot describe (Go's reaction:<id> feedback rows,
		// unknown entry types) are skipped, as _rows_to_entries does.
		if e, ok := cliStoreRowToEntry(id, et, title, content, tags, meta, created, updated, sid, imp); ok {
			out = append(out, e)
		}
	}
	return out, rows.Err()
}

func cliStoreRowToEntry(id, et, title, content, tags, meta, created, updated, sid sql.NullString, imp sql.NullFloat64) (*CLIMemoryEntry, bool) {
	if !id.Valid || !et.Valid || !title.Valid || !content.Valid || !tags.Valid || !meta.Valid || !created.Valid || !updated.Valid || !imp.Valid {
		return nil, false
	}
	u, err := uuid.Parse(id.String)
	if err != nil || !CLIValidEntryType(et.String) {
		return nil, false
	}
	tagList, ok := cliStoreStringList(tags.String)
	if !ok {
		return nil, false
	}
	mv, err := cliStorePyLoads(meta.String)
	mo, isObj := mv.(*cliStoreObj)
	if err != nil || !isObj {
		return nil, false
	}
	ct, err := cliStoreParseISO(created.String)
	if err != nil {
		return nil, false
	}
	ut, err := cliStoreParseISO(updated.String)
	if err != nil {
		return nil, false
	}
	if imp.Float64 < 0 || imp.Float64 > 1 {
		return nil, false
	}
	e := &CLIMemoryEntry{
		ID: u.String(), EntryType: et.String, Title: title.String, Content: content.String, Tags: tagList,
		Importance: imp.Float64, created: ct, updated: ut, meta: mo,
	}
	if sid.Valid {
		v := sid.String
		e.SessionID = &v
	}
	return e, true
}

// cliStoreStringList decodes a JSON list[str] column.
func cliStoreStringList(raw string) ([]string, bool) {
	v, err := cliStorePyLoads(raw)
	if err != nil {
		return nil, false
	}
	arr, ok := v.([]any)
	if !ok {
		return nil, false
	}
	out := make([]string, 0, len(arr))
	for _, x := range arr {
		s, ok := x.(string)
		if !ok {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

// Upsert is MemoryStore.upsert: updated_at becomes now, created_at is kept on
// conflict, tags/metadata are json.dumps'd as Python does.
func (s *CLIStore) Upsert(e *CLIMemoryEntry) error {
	e.updated = cliStoreTime{T: cliStoreNow()}
	if e.meta == nil {
		e.meta = newCLIStoreObj()
	}
	if e.Tags == nil {
		e.Tags = []string{}
	}
	var sid any
	if e.SessionID != nil {
		sid = *e.SessionID
	}
	_, err := s.db.Exec(`
            INSERT INTO memory_entries (id, entry_type, title, content, tags, metadata,
                                        created_at, updated_at, session_id, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entry_type = excluded.entry_type,
                title = excluded.title,
                content = excluded.content,
                tags = excluded.tags,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                session_id = excluded.session_id,
                importance = excluded.importance`,
		e.ID, e.EntryType, e.Title, e.Content, cliStorePyDumps(e.Tags, 0, true), cliStorePyDumps(e.meta, 0, true),
		e.created.iso(false), e.updated.iso(false), sid, e.Importance)
	return err
}

func (s *CLIStore) queryEntries(q string, args ...any) ([]*CLIMemoryEntry, error) {
	rows, err := s.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	return cliStoreScanEntries(rows)
}

// ListRecent is MemoryStore.list_recent.
func (s *CLIStore) ListRecent(limit int) ([]*CLIMemoryEntry, error) {
	return s.queryEntries(`SELECT `+cliStoreEntryCols+` FROM memory_entries ORDER BY created_at DESC LIMIT ?`, limit)
}

// ListByType is MemoryStore.list_by_type.
func (s *CLIStore) ListByType(entryType string, limit int) ([]*CLIMemoryEntry, error) {
	if !CLIValidEntryType(entryType) {
		return nil, fmt.Errorf("'%s' is not a valid MemoryEntryType", entryType)
	}
	return s.queryEntries(`SELECT `+cliStoreEntryCols+` FROM memory_entries WHERE entry_type = ? ORDER BY created_at DESC LIMIT ? OFFSET ?`, entryType, limit, 0)
}

func (s *CLIStore) listBySession(sessionID string, limit int) ([]*CLIMemoryEntry, error) {
	return s.queryEntries(`SELECT `+cliStoreEntryCols+` FROM memory_entries WHERE session_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?`, sessionID, limit, 0)
}

func (s *CLIStore) listImportant(threshold float64, limit int) ([]*CLIMemoryEntry, error) {
	return s.queryEntries(`SELECT `+cliStoreEntryCols+` FROM memory_entries WHERE importance >= ? ORDER BY importance DESC LIMIT ?`, threshold, limit)
}

// CLISearchQueryError is SecurityError from sanitize_search_query.
type CLISearchQueryError struct{ Msg string }

func (e *CLISearchQueryError) Error() string { return e.Msg }

var cliStoreFTSStrip = regexp.MustCompile(`["*]`)

// cliStoreSanitizeQuery is remedy.core.security.sanitize_search_query.
func cliStoreSanitizeQuery(q string, maxLen int) (string, error) {
	q = strings.TrimSpace(q)
	if q == "" {
		return "", &CLISearchQueryError{"Empty search query"}
	}
	if n := utf8.RuneCountInString(q); n > maxLen {
		return "", &CLISearchQueryError{fmt.Sprintf("Search query too long (%d > %d)", n, maxLen)}
	}
	cleaned := cliStoreFTSStrip.ReplaceAllString(q, "")
	if strings.TrimSpace(cleaned) == "" {
		return "", &CLISearchQueryError{"Search query contains only invalid characters"}
	}
	return cleaned, nil
}

var cliStoreFTSToken = regexp.MustCompile(`[\p{L}\p{N}_]+`)

// cliStoreFTS5MatchQuery is remedy.memory.fts_query.fts5_match_query.
func cliStoreFTS5MatchQuery(q string) string {
	var out []string
	for _, tok := range cliStoreFTSToken.FindAllString(q, -1) {
		t := strings.Trim(tok, "_")
		switch strings.ToLower(t) {
		case "", "and", "or", "not", "near":
			continue
		}
		out = append(out, `"`+t+`"`)
		if len(out) >= 32 {
			break
		}
	}
	return strings.Join(out, " ")
}

// Search is MemoryStore.search: FTS5 MATCH ranked, the quoted-token form when
// MATCH rejects the text, and the escaped LIKE scan when FTS cannot answer.
// An empty or unusable query is a *CLISearchQueryError, as Python raises.
func (s *CLIStore) Search(query string, limit int) ([]*CLIMemoryEntry, error) {
	q, err := cliStoreSanitizeQuery(query, 500)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(q) == "" {
		return nil, nil
	}
	const ftsSQL = `SELECT memory_entries.id, memory_entries.entry_type, memory_entries.title, memory_entries.content,
	        memory_entries.tags, memory_entries.metadata, memory_entries.created_at, memory_entries.updated_at,
	        memory_entries.session_id, memory_entries.importance
	    FROM memory_entries
	    JOIN memory_fts ON memory_entries.rowid = memory_fts.rowid
	    WHERE memory_fts MATCH ?
	    ORDER BY rank
	    LIMIT ?`
	hits, err := s.queryEntries(ftsSQL, q, limit)
	if err == nil {
		return hits, nil
	}
	msg := err.Error()
	if strings.Contains(msg, "fts5: syntax error") || !strings.Contains(msg, "no such") {
		match := cliStoreFTS5MatchQuery(q)
		if match == "" {
			return nil, nil
		}
		if hits, err2 := s.queryEntries(ftsSQL, match, limit); err2 == nil {
			return hits, nil
		}
	}
	return s.searchLike(q, limit)
}

var cliStoreLikeEscaper = strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)

func (s *CLIStore) searchLike(query string, limit int) ([]*CLIMemoryEntry, error) {
	q := strings.TrimSpace(query)
	if q == "" {
		return nil, nil
	}
	like := "%" + cliStoreLikeEscaper.Replace(q) + "%"
	return s.queryEntries(`SELECT `+cliStoreEntryCols+` FROM memory_entries WHERE title LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\' ORDER BY created_at DESC LIMIT ?`, like, like, limit)
}

// ── handoff notes ──────────────────────────────────────────────────────────

// CLIHandoffNote is a handoff_notes row as Python's HandoffNote reads it.
type CLIHandoffNote struct {
	ID             string
	Title          string
	Content        string
	Tags           []string
	FromSession    *string
	ToSession      *string
	ContextSummary *string
	ActionItems    []string
	Decisions      []string
	Acknowledged   bool
	created        cliStoreTime
}

// NewCLIHandoffNote is HandoffNote(title=..., content=..., tags=...).
func NewCLIHandoffNote(title, content string, tags []string) *CLIHandoffNote {
	if tags == nil {
		tags = []string{}
	}
	return &CLIHandoffNote{ID: uuid.NewString(), Title: title, Content: content, Tags: tags,
		ActionItems: []string{}, Decisions: []string{}, created: cliStoreTime{T: cliStoreNow()}}
}

// CreatedISO is created_at.isoformat().
func (h *CLIHandoffNote) CreatedISO() string { return h.created.iso(false) }

// JSON is json.dumps(note.model_dump(mode="json")) pretty-printed with two
// spaces, which `remedy handoff show` prints.
func (h *CLIHandoffNote) JSON() string {
	o := newCLIStoreObj()
	opt := func(p *string) any {
		if p == nil {
			return nil
		}
		return *p
	}
	o.set("id", h.ID)
	o.set("title", h.Title)
	o.set("content", h.Content)
	o.set("tags", h.Tags)
	o.set("from_session", opt(h.FromSession))
	o.set("to_session", opt(h.ToSession))
	o.set("context_summary", opt(h.ContextSummary))
	o.set("action_items", h.ActionItems)
	o.set("decisions", h.Decisions)
	o.set("created_at", h.created.iso(true))
	o.set("acknowledged", h.Acknowledged)
	return cliStorePyDumps(o, 2, false)
}

const cliStoreHandoffCols = `id, title, content, tags, from_session, to_session, context_summary, action_items, decisions, created_at, acknowledged`

func (s *CLIStore) queryHandoffs(q string, args ...any) ([]*CLIHandoffNote, error) {
	rows, err := s.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*CLIHandoffNote
	for rows.Next() {
		var (
			id, title, content, tags, from, to, ctx, items, decisions, created sql.NullString
			ack                                                                sql.NullInt64
		)
		if err := rows.Scan(&id, &title, &content, &tags, &from, &to, &ctx, &items, &decisions, &created, &ack); err != nil {
			return nil, err
		}
		h, err := cliStoreRowToHandoff(id, title, content, tags, from, to, ctx, items, decisions, created, ack)
		if err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

func cliStoreRowToHandoff(id, title, content, tags, from, to, ctx, items, decisions, created sql.NullString, ack sql.NullInt64) (*CLIHandoffNote, error) {
	bad := func(what string) error { return fmt.Errorf("handoff %q: unreadable %s", id.String, what) }
	u, err := uuid.Parse(id.String)
	if err != nil {
		return nil, bad("id")
	}
	tl, ok := cliStoreStringList(tags.String)
	if !ok {
		return nil, bad("tags")
	}
	il, ok := cliStoreStringList(items.String)
	if !ok {
		return nil, bad("action_items")
	}
	dl, ok := cliStoreStringList(decisions.String)
	if !ok {
		return nil, bad("decisions")
	}
	ct, err := cliStoreParseISO(created.String)
	if err != nil {
		return nil, bad("created_at")
	}
	opt := func(n sql.NullString) *string {
		if !n.Valid {
			return nil
		}
		v := n.String
		return &v
	}
	return &CLIHandoffNote{
		ID: u.String(), Title: title.String, Content: content.String, Tags: tl,
		FromSession: opt(from), ToSession: opt(to), ContextSummary: opt(ctx),
		ActionItems: il, Decisions: dl, Acknowledged: ack.Int64 != 0, created: ct,
	}, nil
}

// CreateHandoff is MemoryStore.create_handoff: the note row plus a handoff
// memory entry whose id is the note id (re-saving does not duplicate it).
func (s *CLIStore) CreateHandoff(h *CLIHandoffNote) error {
	opt := func(p *string) any {
		if p == nil {
			return nil
		}
		return *p
	}
	ack := 0
	if h.Acknowledged {
		ack = 1
	}
	if _, err := s.db.Exec(`
                    INSERT OR REPLACE INTO handoff_notes
                        (id, title, content, tags, from_session, to_session,
                         context_summary, action_items, decisions, created_at, acknowledged)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		h.ID, h.Title, h.Content, cliStorePyDumps(h.Tags, 0, true), opt(h.FromSession), opt(h.ToSession),
		opt(h.ContextSummary), cliStorePyDumps(h.ActionItems, 0, true), cliStorePyDumps(h.Decisions, 0, true),
		h.created.iso(false), ack); err != nil {
		return err
	}
	meta := newCLIStoreObj()
	meta.set("handoff_id", h.ID)
	meta.set("from_session", opt(h.FromSession))
	meta.set("action_items", h.ActionItems)
	meta.set("decisions", h.Decisions)
	e := NewCLIMemoryEntry("handoff", "Handoff: "+h.Title, h.Content, h.Tags, 0.9)
	e.ID = h.ID
	e.meta = meta
	e.SessionID = h.FromSession
	return s.Upsert(e)
}

// GetHandoff is MemoryStore.get_handoff (nil, nil when absent).
func (s *CLIStore) GetHandoff(id string) (*CLIHandoffNote, error) {
	hs, err := s.queryHandoffs(`SELECT `+cliStoreHandoffCols+` FROM handoff_notes WHERE id = ?`, id)
	if err != nil || len(hs) == 0 {
		return nil, err
	}
	return hs[0], nil
}

// ListHandoffs is MemoryStore.list_handoffs.
func (s *CLIStore) ListHandoffs(limit int) ([]*CLIHandoffNote, error) {
	return s.queryHandoffs(`SELECT `+cliStoreHandoffCols+` FROM handoff_notes ORDER BY created_at DESC LIMIT ?`, limit)
}

// RelevantHandoffs is MemoryStore.get_relevant_handoffs (escaped LIKE).
func (s *CLIStore) RelevantHandoffs(query string, limit int) ([]*CLIHandoffNote, error) {
	like := "%" + cliStoreLikeEscaper.Replace(strings.TrimSpace(query)) + "%"
	return s.queryHandoffs(`SELECT `+cliStoreHandoffCols+` FROM handoff_notes WHERE title LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\' ORDER BY created_at DESC LIMIT ?`, like, like, limit)
}

// PendingHandoffs is AutoHandoffManager.get_pending_handoffs.
func (s *CLIStore) PendingHandoffs() ([]*CLIHandoffNote, error) {
	all, err := s.ListHandoffs(50)
	if err != nil {
		return nil, err
	}
	var out []*CLIHandoffNote
	for _, h := range all {
		if !h.Acknowledged {
			out = append(out, h)
		}
	}
	return out, nil
}

// ── repair / backup ────────────────────────────────────────────────────────

// CLIIntegrityItem is one key of MemoryRepair.check_integrity, in order.
type CLIIntegrityItem struct {
	Key   string
	Value any // string, int64, or *cliStoreObj for entry_counts
}

// Text is the value as Python prints it (`{k}: {v}`).
func (i CLIIntegrityItem) Text() string { return cliStorePyStr(i.Value) }

// CheckIntegrity is MemoryRepair.check_integrity.
func (s *CLIStore) CheckIntegrity() ([]CLIIntegrityItem, error) {
	one := func(q string) (any, error) {
		var v any
		err := s.db.QueryRow(q).Scan(&v)
		if errors.Is(err, sql.ErrNoRows) {
			return "unknown", nil
		}
		if b, ok := v.([]byte); ok {
			v = string(b)
		}
		return v, err
	}
	integrity, err := one(`PRAGMA integrity_check`)
	if err != nil {
		return nil, err
	}
	quick, err := one(`PRAGMA quick_check`)
	if err != nil {
		return nil, err
	}
	if _, err := s.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`); err != nil {
		return nil, err
	}
	count := func(q string) (int64, error) {
		var n int64
		err := s.db.QueryRow(q).Scan(&n)
		return n, err
	}
	pages, err := count(`PRAGMA page_count`)
	if err != nil {
		return nil, err
	}
	free, err := count(`PRAGMA freelist_count`)
	if err != nil {
		return nil, err
	}
	rows, err := s.db.Query(`SELECT entry_type, COUNT(*) as cnt FROM memory_entries GROUP BY entry_type`)
	if err != nil {
		return nil, err
	}
	counts := newCLIStoreObj()
	for rows.Next() {
		var t string
		var n int64
		if err := rows.Scan(&t, &n); err != nil {
			rows.Close()
			return nil, err
		}
		counts.set(t, n)
	}
	rows.Close()
	fts, err := count(`SELECT COUNT(*) FROM memory_fts`)
	if err != nil {
		return nil, err
	}
	handoffs, err := count(`SELECT COUNT(*) FROM handoff_notes`)
	if err != nil {
		return nil, err
	}
	sessions, err := count(`SELECT COUNT(*) FROM session_summaries`)
	if err != nil {
		return nil, err
	}
	return []CLIIntegrityItem{
		{"integrity", integrity}, {"quick_check", quick}, {"page_count", pages},
		{"freelist_pages", free}, {"estimated_size_bytes", pages * 4096}, {"entry_counts", counts},
		{"fte_count", fts}, {"handoff_count", handoffs}, {"session_count", sessions},
	}, nil
}

func cliStoreIntegrityObj(items []CLIIntegrityItem) *cliStoreObj {
	o := newCLIStoreObj()
	for _, it := range items {
		o.set(it.Key, it.Value)
	}
	return o
}

func cliStoreFileSize(p string) int64 {
	if st, err := os.Stat(p); err == nil {
		return st.Size()
	}
	return 0
}

// Vacuum is MemoryRepair.vacuum; it returns the bytes reclaimed.
func (s *CLIStore) Vacuum() (before, after, reclaimed int64, err error) {
	before = cliStoreFileSize(s.path)
	if _, err = s.db.Exec(`PRAGMA optimize`); err != nil {
		return
	}
	if _, err = s.db.Exec(`VACUUM`); err != nil {
		return
	}
	after = cliStoreFileSize(s.path)
	reclaimed = before - after
	if reclaimed < 0 {
		reclaimed = 0
	}
	return
}

// Backup is MemoryRepair.backup: <db dir>/backups/memory_backup_<local
// %Y%m%d_%H%M%S>.db plus a .json manifest beside it. It returns the copy's path.
func (s *CLIStore) Backup() (string, error) {
	st, err := os.Stat(s.path)
	if err != nil {
		return "", fmt.Errorf("Memory database not found: %s", s.path)
	}
	dir := filepath.Join(filepath.Dir(s.path), "backups")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	stamp := time.Now().Format("20060102_150405")
	dst := filepath.Join(dir, "memory_backup_"+stamp+".db")
	// Fold the WAL into the main file first so the copy is complete.
	if _, err := s.db.Exec(`PRAGMA wal_checkpoint(TRUNCATE)`); err != nil {
		return "", err
	}
	if err := cliStoreCopyFile(s.path, dst, st); err != nil {
		return "", err
	}
	info, err := s.CheckIntegrity()
	if err != nil {
		return "", err
	}
	manifest := newCLIStoreObj()
	manifest.set("backup_path", dst)
	manifest.set("original_path", s.path)
	manifest.set("timestamp", stamp)
	manifest.set("stats", cliStoreIntegrityObj(info))
	if err := cliStoreWriteAtomic(filepath.Join(dir, "memory_backup_"+stamp+".json"), cliStorePyDumps(manifest, 2, true)); err != nil {
		return "", err
	}
	return dst, nil
}

// cliStoreCopyFile is shutil.copy2: bytes, permission bits and mtime.
func cliStoreCopyFile(src, dst string, st os.FileInfo) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, st.Mode().Perm())
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	_ = os.Chmod(dst, st.Mode().Perm())
	_ = os.Chtimes(dst, st.ModTime(), st.ModTime())
	return nil
}

func cliStoreWriteAtomic(path, text string) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".remedy-json-*.tmp")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	if _, err := tmp.WriteString(text); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		_ = os.Remove(tmpPath)
		return err
	}
	return nil
}

// ── user profile ───────────────────────────────────────────────────────────

// CLIUserFact is one user_facts row (search_user_facts returns dict(row)).
type CLIUserFact struct {
	Category       string
	Fact           string
	ReferenceCount int64
}

// SearchUserFacts is MemoryStore.search_user_facts (user "default"). Like
// Python, the query is not LIKE-escaped.
func (s *CLIStore) SearchUserFacts(query string, limit int) ([]CLIUserFact, error) {
	like := "%" + query + "%"
	rows, err := s.db.Query(`SELECT category, fact, reference_count FROM user_facts WHERE user_id = ? AND (fact LIKE ? OR category LIKE ?) ORDER BY reference_count DESC LIMIT ?`,
		"default", like, like, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []CLIUserFact
	for rows.Next() {
		var f CLIUserFact
		if err := rows.Scan(&f.Category, &f.Fact, &f.ReferenceCount); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// CLIUserTrait is one profile trait as `remedy user show` prints it.
type CLIUserTrait struct {
	Key        string
	Value      string // str(value)
	Confidence float64
}

// CLIUserProfile is what `remedy user show` prints.
type CLIUserProfile struct {
	UserID       string
	DisplayName  string
	SessionsText string
	CreatedISO   string
	LastISO      string
	Traits       []CLIUserTrait
	Facts        []CLIUserFact
}

// Profile is MemoryStore.get_or_create_profile("default") for display.
func (s *CLIStore) Profile() (*CLIUserProfile, error) {
	p, err := s.loadOrCreateProfile("default")
	if err != nil {
		return nil, err
	}
	out := &CLIUserProfile{UserID: cliStoreStr(p.vals["user_id"], "default")}
	if dn, ok := p.vals["display_name"].(string); ok {
		out.DisplayName = dn
	}
	if st, ok := p.vals["stats"].(*cliStoreObj); ok {
		if v, ok := st.get("sessions_count"); ok {
			out.SessionsText = cliStorePyStr(v)
		}
	}
	if out.SessionsText == "" {
		out.SessionsText = "0"
	}
	disp := func(v any) string {
		if t, err := cliStoreParseISO(cliStoreStr(v, "")); err == nil {
			return t.iso(false)
		}
		return cliStoreStr(v, "")
	}
	out.CreatedISO = disp(p.vals["created_at"])
	out.LastISO = disp(p.vals["last_active"])
	if tr, ok := p.vals["traits"].(*cliStoreObj); ok {
		for _, k := range tr.keys {
			t, _ := tr.vals[k].(*cliStoreObj)
			if t == nil {
				continue
			}
			out.Traits = append(out.Traits, CLIUserTrait{Key: k, Value: cliStorePyStr(t.vals["value"]), Confidence: cliStoreFloat(t.vals["confidence"], 0.5)})
		}
	}
	for _, f := range cliStoreFactList(p) {
		out.Facts = append(out.Facts, CLIUserFact{Category: cliStoreStr(f["category"], "general"), Fact: cliStoreStr(f["fact"], ""),
			ReferenceCount: int64(cliStoreInt(f["reference_count"], 1))})
	}
	return out, nil
}

func cliStoreStr(v any, def string) string {
	if s, ok := v.(string); ok {
		return s
	}
	return def
}

func cliStoreFloat(v any, def float64) float64 {
	switch x := v.(type) {
	case json.Number:
		if f, err := x.Float64(); err == nil {
			return f
		}
	case float64:
		return x
	case int:
		return float64(x)
	case int64:
		return float64(x)
	}
	return def
}

func cliStoreInt(v any, def int) int {
	switch x := v.(type) {
	case json.Number:
		if n, err := x.Int64(); err == nil {
			return int(n)
		}
		if f, err := x.Float64(); err == nil && f == float64(int(f)) {
			return int(f)
		}
	case float64:
		return int(x)
	case int:
		return x
	case int64:
		return int(x)
	}
	return def
}

// cliStoreFactList returns the profile's facts as maps, the shape
// applyProfileFactUpsert works on (it writes them back into the profile).
func cliStoreFactList(p *cliStoreObj) []map[string]any {
	arr, _ := p.vals["facts"].([]any)
	out := make([]map[string]any, 0, len(arr))
	for i, item := range arr {
		switch f := item.(type) {
		case map[string]any:
			out = append(out, f)
		case *cliStoreObj:
			m := make(map[string]any, len(f.keys))
			for _, k := range f.keys {
				m[k] = f.vals[k]
			}
			arr[i] = m
			out = append(out, m)
		}
	}
	return out
}

// cliStorePydanticTime renders a datetime field as pydantic's JSON does,
// defaulting to now like the Field(default_factory=...) Python declares.
func cliStorePydanticTime(v any, now time.Time) string {
	if t, err := cliStoreParseISO(cliStoreStr(v, "")); err == nil {
		return t.iso(true)
	}
	return cliStoreTime{T: now}.iso(true)
}

func cliStorePythonTime(v any, now time.Time) string {
	if t, err := cliStoreParseISO(cliStoreStr(v, "")); err == nil {
		return t.iso(false)
	}
	return cliStoreISO(now)
}

func cliStoreLookup(v any, k string) (any, bool) {
	switch x := v.(type) {
	case *cliStoreObj:
		return x.get(k)
	case map[string]any:
		val, ok := x[k]
		return val, ok
	}
	return nil, false
}

func cliStoreDefaultStats() *cliStoreObj {
	st := newCLIStoreObj()
	st.set("sessions_count", 0)
	st.set("total_interactions", 0)
	st.set("skills_used", newCLIStoreObj())
	st.set("preferred_channels", []any{})
	st.set("avg_session_duration_minutes", 0.0)
	return st
}

// cliStoreNormalizeProfile is UserProfile(**data).model_dump(): model field
// order, defaults filled, unknown keys dropped, numbers typed per field.
func cliStoreNormalizeProfile(p *cliStoreObj, userID string, now time.Time) *cliStoreObj {
	out := newCLIStoreObj()
	out.set("user_id", cliStoreStr(p.vals["user_id"], userID))
	if dn, ok := p.vals["display_name"].(string); ok {
		out.set("display_name", dn)
	} else {
		out.set("display_name", nil)
	}
	traits := newCLIStoreObj()
	if tr, ok := p.vals["traits"].(*cliStoreObj); ok {
		for _, k := range tr.keys {
			t := tr.vals[k]
			get := func(f string) any { v, _ := cliStoreLookup(t, f); return v }
			n := newCLIStoreObj()
			n.set("key", cliStoreStr(get("key"), k))
			n.set("value", get("value"))
			n.set("confidence", cliStoreFloat(get("confidence"), 0.5))
			n.set("source", cliStoreStr(get("source"), "inferred"))
			n.set("first_observed", cliStorePydanticTime(get("first_observed"), now))
			n.set("last_updated", cliStorePydanticTime(get("last_updated"), now))
			n.set("observation_count", cliStoreInt(get("observation_count"), 1))
			traits.set(k, n)
		}
	}
	out.set("traits", traits)
	facts := []any{}
	for _, f := range cliStoreFactList(p) {
		n := newCLIStoreObj()
		id := cliStoreStr(f["id"], "")
		if u, err := uuid.Parse(id); err == nil {
			id = u.String()
		} else {
			id = uuid.NewString()
		}
		n.set("id", id)
		n.set("fact", cliStoreStr(f["fact"], ""))
		n.set("category", cliStoreStr(f["category"], "general"))
		n.set("confidence", cliStoreFloat(f["confidence"], 0.7))
		n.set("source", cliStoreStr(f["source"], "inferred"))
		n.set("created_at", cliStorePydanticTime(f["created_at"], now))
		n.set("last_referenced", cliStorePydanticTime(f["last_referenced"], now))
		n.set("reference_count", cliStoreInt(f["reference_count"], 1))
		if pp, ok := f["project_path"].(string); ok {
			n.set("project_path", pp)
		} else {
			n.set("project_path", nil)
		}
		pinned, _ := f["pinned"].(bool)
		n.set("pinned", pinned)
		inferred, ok := f["inferred"].(bool)
		if !ok {
			inferred = true
		}
		n.set("inferred", inferred)
		n.set("authority", cliStoreStr(f["authority"], "agent"))
		n.set("why", cliStoreStr(f["why"], ""))
		facts = append(facts, n)
	}
	out.set("facts", facts)
	if st, ok := p.vals["stats"].(*cliStoreObj); ok {
		out.set("stats", st)
	} else {
		out.set("stats", cliStoreDefaultStats())
	}
	out.set("created_at", cliStorePydanticTime(p.vals["created_at"], now))
	out.set("last_active", cliStorePydanticTime(p.vals["last_active"], now))
	return out
}

// loadOrCreateProfile is MemoryStore.get_or_create_profile: the stored
// profile, or a fresh default one saved on the spot.
func (s *CLIStore) loadOrCreateProfile(userID string) (*cliStoreObj, error) {
	var raw string
	err := s.db.QueryRow(`SELECT profile_json FROM user_profile WHERE user_id = ?`, userID).Scan(&raw)
	now := cliStoreNow()
	if errors.Is(err, sql.ErrNoRows) {
		p := cliStoreNormalizeProfile(newCLIStoreObj(), userID, now)
		if err := s.saveProfile(p, userID); err != nil {
			return nil, err
		}
		return p, nil
	}
	if err != nil {
		return nil, err
	}
	v, err := cliStorePyLoads(raw)
	if err != nil {
		return nil, fmt.Errorf("user_profile %q: unreadable profile_json: %w", userID, err)
	}
	obj, ok := v.(*cliStoreObj)
	if !ok {
		return nil, fmt.Errorf("user_profile %q: profile_json is not an object", userID)
	}
	return cliStoreNormalizeProfile(obj, userID, now), nil
}

// saveProfile is MemoryStore.save_user_profile: profile_json
// (model_dump_json(indent=2)), then user_facts and user_traits rewritten,
// all in one transaction.
func (s *CLIStore) saveProfile(p *cliStoreObj, userID string) error {
	now := cliStoreNow()
	p = cliStoreNormalizeProfile(p, userID, now)
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.Exec(`INSERT OR REPLACE INTO user_profile (user_id, profile_json, updated_at) VALUES (?, ?, ?)`,
		userID, cliStorePyDumps(p, 2, false), cliStoreISO(now)); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM user_facts WHERE user_id = ?`, userID); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM user_traits WHERE user_id = ?`, userID); err != nil {
		return err
	}
	facts, _ := p.vals["facts"].([]any)
	for _, item := range facts {
		f := item.(*cliStoreObj)
		if _, err := tx.Exec(`INSERT OR REPLACE INTO user_facts (id, user_id, fact, category, confidence, source, created_at, last_referenced, reference_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			f.vals["id"], userID, f.vals["fact"], f.vals["category"], f.vals["confidence"], f.vals["source"],
			cliStorePythonTime(f.vals["created_at"], now), cliStorePythonTime(f.vals["last_referenced"], now), f.vals["reference_count"]); err != nil {
			return err
		}
	}
	traits, _ := p.vals["traits"].(*cliStoreObj)
	for _, k := range traits.keys {
		t := traits.vals[k].(*cliStoreObj)
		if _, err := tx.Exec(`INSERT OR REPLACE INTO user_traits (user_id, key, value_json, confidence, source, first_observed, last_updated, observation_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
			userID, k, cliStorePyDumps(t.vals["value"], 0, true), t.vals["confidence"], t.vals["source"],
			cliStorePythonTime(t.vals["first_observed"], now), cliStorePythonTime(t.vals["last_updated"], now), t.vals["observation_count"]); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// ── sessions ───────────────────────────────────────────────────────────────

// saveSessionSummary is MemoryStore.save_session_summary for the CLI's
// session end (no tasks, no skills).
func (s *CLIStore) saveSessionSummary(sessionID string, started, ended time.Time, summary string) error {
	_, err := s.db.Exec(`
                    INSERT OR REPLACE INTO session_summaries
                        (session_id, started_at, ended_at, tasks_completed, skills_created,
                         skills_refined, key_decisions, open_items, summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		sessionID, cliStoreISO(started), cliStoreISO(ended), 0, 0, 0, "[]", "[]", summary)
	return err
}

// StartSession is `remedy session start` against the store: the "Session
// started" note and the pending handoffs to show. The caller writes the marker.
func (s *CLIStore) StartSession() (string, []*CLIHandoffNote, error) {
	sid := uuid.NewString()
	e := NewCLIMemoryEntry("note", "Session started", "Session "+sid+" started.", nil, 0.3)
	if err := s.Upsert(e); err != nil {
		return "", nil, err
	}
	pending, err := s.PendingHandoffs()
	return sid, pending, err
}

// EndSession is `remedy session end` against the store:
// AutoHandoffManager.generate_handoff(session_id, [], []) then
// generate_session_summary(...). It returns the created handoff.
func (s *CLIStore) EndSession(sessionID string) (*CLIHandoffNote, error) {
	important, err := s.listImportant(0.7, 10)
	if err != nil {
		return nil, err
	}
	var highlights []string
	for _, e := range important {
		if e.SessionID != nil && *e.SessionID == sessionID {
			highlights = append(highlights, "- "+e.Title)
		}
	}
	content := "Session ended."
	if len(highlights) > 0 {
		if len(highlights) > 5 {
			highlights = highlights[:5]
		}
		content = "**Key memories**:\n" + strings.Join(highlights, "\n")
	}
	h := NewCLIHandoffNote("Handoff from session "+sessionID, content, []string{"auto-handoff", "session-" + sessionID})
	from := sessionID
	h.FromSession = &from
	if err := s.CreateHandoff(h); err != nil {
		return nil, err
	}
	started := cliStoreNow()
	if err := s.saveSessionSummary(sessionID, started, cliStoreNow(),
		fmt.Sprintf("Session %s: %d tasks, %d skills created.", sessionID, 0, 0)); err != nil {
		return nil, err
	}
	return h, nil
}
