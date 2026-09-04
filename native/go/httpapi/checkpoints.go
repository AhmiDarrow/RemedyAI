package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var checkpointIDSafe = regexp.MustCompile(`[^a-zA-Z0-9_-]`)

type turnCheckpoint struct {
	ID            string         `json:"id"`
	SessionID     *string        `json:"session_id"`
	Title         string         `json:"title"`
	Done          []string       `json:"done"`
	NextSteps     []string       `json:"next_steps"`
	ToolsUsed     []string       `json:"tools_used"`
	ToolStepCount int            `json:"tool_step_count"`
	Failures      []string       `json:"failures"`
	PlanID        *string        `json:"plan_id,omitempty"`
	Reason        string         `json:"reason"`
	CreatedAt     string         `json:"created_at"`
	Metadata      map[string]any `json:"metadata,omitempty"`
}

func (s *Server) checkpointsRoot() string {
	return filepath.Join(s.remedyHomeDir(), "checkpoints")
}

func checkpointPath(root, id string) string {
	safe := checkpointIDSafe.ReplaceAllString(id, "")
	if safe == "" {
		safe = "cp"
	}
	return filepath.Join(root, safe+".json")
}

func (c *turnCheckpoint) toDict() map[string]any {
	var sid any
	if c.SessionID != nil {
		sid = *c.SessionID
	}
	var pid any
	if c.PlanID != nil {
		pid = *c.PlanID
	}
	meta := c.Metadata
	if meta == nil {
		meta = map[string]any{}
	}
	return map[string]any{
		"id":              c.ID,
		"session_id":      sid,
		"title":           c.Title,
		"done":            nonNilStrings(c.Done),
		"next_steps":      nonNilStrings(c.NextSteps),
		"tools_used":      nonNilStrings(c.ToolsUsed),
		"tool_step_count": c.ToolStepCount,
		"failures":        nonNilStrings(c.Failures),
		"plan_id":         pid,
		"reason":          c.Reason,
		"created_at":      c.CreatedAt,
		"metadata":        meta,
	}
}

func nonNilStrings(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}

func (c *turnCheckpoint) summaryMarkdown() string {
	var b strings.Builder
	b.WriteString("# Checkpoint: ")
	b.WriteString(c.Title)
	b.WriteString("\n**When:** ")
	b.WriteString(c.CreatedAt)
	b.WriteString("\n**Reason:** ")
	b.WriteString(c.Reason)
	b.WriteString("\n**Tools so far:** ")
	b.WriteString(strconv.Itoa(c.ToolStepCount))
	b.WriteString("\n")
	if len(c.Done) > 0 {
		b.WriteString("\n## Done\n")
		for _, d := range c.Done {
			b.WriteString("- ")
			b.WriteString(d)
			b.WriteString("\n")
		}
	}
	if len(c.NextSteps) > 0 {
		b.WriteString("\n## Next\n")
		for _, n := range c.NextSteps {
			b.WriteString("- ")
			b.WriteString(n)
			b.WriteString("\n")
		}
	}
	return b.String()
}

func parseCheckpointFile(path string) (*turnCheckpoint, error) {
	var raw map[string]any
	if err := readJSONFile(path, &raw); err != nil {
		return nil, err
	}
	id := asString(raw["id"])
	if id == "" {
		id = shortHexID(12)
	}
	title := asString(raw["title"])
	if title == "" {
		title = "checkpoint"
	}
	var sid *string
	if v := asString(raw["session_id"]); v != "" {
		sid = &v
	}
	var pid *string
	if v := asString(raw["plan_id"]); v != "" {
		pid = &v
	}
	created := asString(raw["created_at"])
	if created == "" {
		created = utcNowISO()
	}
	reason := asString(raw["reason"])
	if reason == "" {
		reason = "manual"
	}
	meta, _ := raw["metadata"].(map[string]any)
	count := 0
	switch v := raw["tool_step_count"].(type) {
	case float64:
		count = int(v)
	case int:
		count = v
	}
	return &turnCheckpoint{
		ID:            id,
		SessionID:     sid,
		Title:         title,
		Done:          stringList(raw["done"]),
		NextSteps:     stringList(raw["next_steps"]),
		ToolsUsed:     stringList(raw["tools_used"]),
		ToolStepCount: count,
		Failures:      stringList(raw["failures"]),
		PlanID:        pid,
		Reason:        reason,
		CreatedAt:     created,
		Metadata:      meta,
	}, nil
}

func (s *Server) saveCheckpoint(c *turnCheckpoint) error {
	root := s.checkpointsRoot()
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	return writeJSONAtomic(checkpointPath(root, c.ID), c.toDict())
}

func (s *Server) listCheckpoints(sessionID string, limit int) []*turnCheckpoint {
	if limit < 1 {
		limit = 1
	}
	if limit > 50 {
		limit = 50
	}
	root := s.checkpointsRoot()
	ents, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	type dated struct {
		mtime time.Time
		path  string
	}
	files := make([]dated, 0, len(ents))
	for _, e := range ents {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, dated{mtime: info.ModTime(), path: filepath.Join(root, e.Name())})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime.After(files[j].mtime) })
	out := make([]*turnCheckpoint, 0, limit)
	for _, f := range files {
		cp, err := parseCheckpointFile(f.path)
		if err != nil {
			continue
		}
		if sessionID != "" && cp.SessionID != nil && *cp.SessionID != sessionID {
			continue
		}
		out = append(out, cp)
		if len(out) >= limit {
			break
		}
	}
	return out
}

func (s *Server) handleListCheckpoints(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			limit = n
		}
	}
	items := s.listCheckpoints(sid, limit)
	out := make([]map[string]any, 0, len(items))
	for _, c := range items {
		out = append(out, c.toDict())
	}
	writeJSON(w, http.StatusOK, map[string]any{"checkpoints": out})
}

func (s *Server) handleLatestCheckpoint(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	items := s.listCheckpoints(sid, 1)
	if len(items) == 0 {
		writeJSON(w, http.StatusOK, map[string]any{"checkpoint": nil})
		return
	}
	cp := items[0]
	writeJSON(w, http.StatusOK, map[string]any{
		"checkpoint": cp.toDict(),
		"markdown":   cp.summaryMarkdown(),
	})
}
