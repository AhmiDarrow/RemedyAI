package httpapi

import (
	"errors"
	"sort"
	"strings"
)

// Searching the owner's sessions by what was said in them, so Remedy can find
// "the one where we talked about the router" and carry it on.

// sessionHit is one message that matched a search.
type sessionHit struct {
	SessionID string
	Role      string
	Content   string
	CreatedAt string
}

// likePattern is `text` as a LIKE pattern that matches it anywhere, with the
// wildcard characters in it taken literally.
func likePattern(text string) string {
	r := strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)
	return "%" + r.Replace(text) + "%"
}

// SearchMessages returns the newest messages of the owner's and Remedy's that
// contain every word of `words` (case-insensitive), across all sessions.
func (s *sessionStore) SearchMessages(words []string, limit int) ([]sessionHit, error) {
	if len(words) == 0 {
		return nil, nil
	}
	if limit <= 0 || limit > 400 {
		limit = 400
	}
	where := make([]string, 0, len(words))
	args := make([]any, 0, len(words)+1)
	for _, w := range words {
		where = append(where, `content LIKE ? ESCAPE '\'`)
		args = append(args, likePattern(w))
	}
	args = append(args, limit)
	s.mu.Lock()
	defer s.mu.Unlock()
	rows, err := s.db.Query(
		`SELECT session_id, role, content, created_at FROM chat_messages
		 WHERE role IN ('user','assistant') AND reverted = 0 AND draft = 0 AND `+strings.Join(where, " AND ")+`
		 ORDER BY created_at DESC LIMIT ?`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []sessionHit
	for rows.Next() {
		var h sessionHit
		if err := rows.Scan(&h.SessionID, &h.Role, &h.Content, &h.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// searchWords splits a query into its words, at most eight, each at least two
// characters, lower-cased and without repeats.
func searchWords(query string) []string {
	seen := map[string]bool{}
	var out []string
	for _, w := range strings.Fields(strings.ToLower(query)) {
		w = strings.Trim(w, `"'.,;:!?()[]{}`)
		if len([]rune(w)) < 2 || seen[w] {
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

// snippetAround is a short piece of `text` around the first of `words` found
// in it, on one line.
func snippetAround(text string, words []string, width int) string {
	flat := strings.Join(strings.Fields(text), " ")
	lower := strings.ToLower(flat)
	at := -1
	for _, w := range words {
		if i := strings.Index(lower, w); i >= 0 && (at < 0 || i < at) {
			at = i
		}
	}
	if at < 0 {
		return truncateRunes(flat, width)
	}
	// Lower-casing can change byte lengths; work in runes of the flat text.
	runes := []rune(flat)
	pos := len([]rune(lower[:at]))
	if pos > len(runes) {
		pos = len(runes)
	}
	start := pos - width/3
	if start < 0 {
		start = 0
	}
	end := start + width
	if end > len(runes) {
		end = len(runes)
	}
	out := string(runes[start:end])
	if start > 0 {
		out = "…" + out
	}
	if end < len(runes) {
		out += "…"
	}
	return out
}

func containsAll(text string, words []string) bool {
	lower := strings.ToLower(text)
	for _, w := range words {
		if !strings.Contains(lower, w) {
			return false
		}
	}
	return true
}

// toolSessionsSearch finds sessions by their title and by what was said in them.
func toolSessionsSearch(s *Server, in partnerToolInput) (any, error) {
	if s.sessions == nil {
		return nil, errors.New("sessions are not available")
	}
	words := searchWords(in.Query)
	if len(words) == 0 {
		return nil, errors.New("say what to look for: query needs at least one word of two letters or more")
	}
	n := in.Last
	if n <= 0 || n > 20 {
		n = 8
	}
	hits, err := s.sessions.SearchMessages(words, 400)
	if err != nil {
		return nil, err
	}
	type found struct {
		sess     ChatSession
		snippets []map[string]any
		matches  int
		latest   string
		inTitle  bool
	}
	byID := map[string]*found{}
	var order []string
	get := func(id string) *found {
		if f := byID[id]; f != nil {
			return f
		}
		sess, ok, err := s.sessions.Get(id)
		if err != nil || !ok {
			return nil
		}
		sess.Claimed = s.claims != nil && s.claims.ActiveRequest(sess.ID) != ""
		f := &found{sess: sess}
		byID[id] = f
		order = append(order, id)
		return f
	}
	for _, h := range hits {
		f := get(h.SessionID)
		if f == nil {
			continue
		}
		f.matches++
		if h.CreatedAt > f.latest {
			f.latest = h.CreatedAt
		}
		if len(f.snippets) < 2 {
			f.snippets = append(f.snippets, map[string]any{
				"who":  h.Role,
				"when": h.CreatedAt,
				"text": snippetAround(h.Content, words, 220),
			})
		}
	}
	// A session can be about the thing without the words being in a message.
	if list, err := s.sessions.List(500, 0); err == nil {
		for _, sess := range list {
			if !containsAll(sess.Title, words) {
				continue
			}
			if f := get(sess.ID); f != nil {
				f.inTitle = true
				if sess.UpdatedAt != nil && *sess.UpdatedAt > f.latest {
					f.latest = *sess.UpdatedAt
				}
			}
		}
	}
	// A title match first, then the most recent talk about it.
	sort.SliceStable(order, func(i, j int) bool {
		a, b := byID[order[i]], byID[order[j]]
		if a.inTitle != b.inTitle {
			return a.inTitle
		}
		return a.latest > b.latest
	})
	if len(order) > n {
		order = order[:n]
	}
	rows := make([]map[string]any, 0, len(order))
	for _, id := range order {
		f := byID[id]
		row := sessionPublic(s, f.sess)
		row["matching_messages"] = f.matches
		row["title_matches"] = f.inTitle
		if len(f.snippets) > 0 {
			row["snippets"] = f.snippets
		}
		rows = append(rows, row)
	}
	return map[string]any{
		"query":    strings.Join(words, " "),
		"sessions": rows,
		"total":    len(rows),
		"next":     "sessions.read shows more of one; sessions.continue carries it on; grove.open with target shows it to the owner.",
	}, nil
}
