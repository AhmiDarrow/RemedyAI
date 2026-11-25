package httpapi

import (
	"encoding/json"
	"sort"
	"strings"
)

// How a session is filed (pinned, archived, tagged) is part of the session,
// not of the window looking at it: the desktop, the web page and a phone all
// read and write it here, so a pin made on one shows on the others.
//
// It lives in its own table so the chat_sessions queries stay as they are.

const sessionMetaSchema = `
CREATE TABLE IF NOT EXISTS session_meta (
    session_id TEXT PRIMARY KEY,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]'
);`

// sessionMetaTagLimit and sessionMetaTagLen bound what one session carries.
const (
	sessionMetaTagLimit = 12
	sessionMetaTagLen   = 40
)

type sessionMeta struct {
	Pinned   bool
	Archived bool
	Tags     []string
}

// sessionMetaPatch is the part of PATCH /api/sessions/{id} about filing. A
// field left out is left alone.
type sessionMetaPatch struct {
	Pinned   *bool     `json:"pinned"`
	Archived *bool     `json:"archived"`
	Tags     *[]string `json:"tags"`
}

func (p sessionMetaPatch) empty() bool {
	return p.Pinned == nil && p.Archived == nil && p.Tags == nil
}

// normalizeSessionTags trims, drops empties and repeats (case-insensitively),
// and keeps the first sessionMetaTagLimit.
func normalizeSessionTags(in []string) []string {
	out := make([]string, 0, len(in))
	seen := map[string]struct{}{}
	for _, t := range in {
		t = strings.TrimSpace(t)
		if r := []rune(t); len(r) > sessionMetaTagLen {
			t = strings.TrimSpace(string(r[:sessionMetaTagLen]))
		}
		if t == "" {
			continue
		}
		k := strings.ToLower(t)
		if _, dup := seen[k]; dup {
			continue
		}
		seen[k] = struct{}{}
		out = append(out, t)
		if len(out) == sessionMetaTagLimit {
			break
		}
	}
	return out
}

// AllMeta returns the filing of every session that has any.
func (s *sessionStore) AllMeta() (map[string]sessionMeta, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rows, err := s.db.Query(`SELECT session_id, pinned, archived, tags FROM session_meta`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]sessionMeta{}
	for rows.Next() {
		var (
			id               string
			pinned, archived int
			tags             string
		)
		if err := rows.Scan(&id, &pinned, &archived, &tags); err != nil {
			return nil, err
		}
		m := sessionMeta{Pinned: pinned != 0, Archived: archived != 0}
		_ = json.Unmarshal([]byte(tags), &m.Tags)
		out[id] = m
	}
	return out, rows.Err()
}

// Meta returns one session's filing (the zero value when it has none).
func (s *sessionStore) Meta(id string) (sessionMeta, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.metaLocked(id)
}

func (s *sessionStore) metaLocked(id string) (sessionMeta, error) {
	var (
		pinned, archived int
		tags             string
	)
	err := s.db.QueryRow(`SELECT pinned, archived, tags FROM session_meta WHERE session_id = ?`, id).
		Scan(&pinned, &archived, &tags)
	if err != nil {
		// No row is a session nobody has filed yet.
		if strings.Contains(err.Error(), "no rows") {
			return sessionMeta{}, nil
		}
		return sessionMeta{}, err
	}
	m := sessionMeta{Pinned: pinned != 0, Archived: archived != 0}
	_ = json.Unmarshal([]byte(tags), &m.Tags)
	return m, nil
}

// PatchMeta applies the fields that are present and returns the result. The
// caller has checked that the session exists.
func (s *sessionStore) PatchMeta(id string, p sessionMetaPatch) (sessionMeta, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	m, err := s.metaLocked(id)
	if err != nil {
		return sessionMeta{}, err
	}
	if p.Pinned != nil {
		m.Pinned = *p.Pinned
	}
	if p.Archived != nil {
		m.Archived = *p.Archived
	}
	if p.Tags != nil {
		m.Tags = normalizeSessionTags(*p.Tags)
	}
	tags := m.Tags
	if tags == nil {
		tags = []string{}
	}
	raw, err := json.Marshal(tags)
	if err != nil {
		return sessionMeta{}, err
	}
	b := func(v bool) int {
		if v {
			return 1
		}
		return 0
	}
	_, err = s.db.Exec(
		`INSERT INTO session_meta (session_id, pinned, archived, tags) VALUES (?, ?, ?, ?)
		 ON CONFLICT(session_id) DO UPDATE SET pinned = excluded.pinned,
		     archived = excluded.archived, tags = excluded.tags`,
		id, b(m.Pinned), b(m.Archived), string(raw),
	)
	return m, err
}

// applyMeta stamps a session's filing onto its row. Tags are never null in
// the reply, and are in a stable order for clients that compare lists.
func applyMeta(sess *ChatSession, m sessionMeta) {
	sess.Pinned = m.Pinned
	sess.Archived = m.Archived
	sess.Tags = append([]string{}, m.Tags...)
	sort.SliceStable(sess.Tags, func(i, j int) bool {
		return strings.ToLower(sess.Tags[i]) < strings.ToLower(sess.Tags[j])
	})
}
