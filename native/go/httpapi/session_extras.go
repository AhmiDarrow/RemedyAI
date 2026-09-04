package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode"
)

const (
	exportContentCap  = 48_000
	exportToolCap     = 2_000
	exportMaxMessages = 2_000
)

var (
	exportRedactRE = regexp.MustCompile(`(?i)\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+)`)
	inlineImageRE  = regexp.MustCompile(`data:image/[^;]+;base64,[A-Za-z0-9+/=]+`)
)

func (s *Server) handleExportSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	sess, ok, err := s.sessions.Get(sid)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	msgs, err := s.sessions.ListMessagesExport(sid, exportMaxMessages)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	title := sess.Title
	if strings.TrimSpace(title) == "" {
		title = "Session"
	}
	fmtName := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("format")))
	var body, ext string
	if fmtName == "md" || fmtName == "markdown" {
		body = formatSessionMarkdown(title, sid, msgs)
		ext = "md"
	} else {
		var model, agent *string
		model = sess.Model
		agent = sess.Agent
		body = formatSessionTxt(title, sid, msgs, model, agent)
		ext = "txt"
	}
	filename := fmt.Sprintf("remedy-export-%s.%s", safeFilenameStem(title), ext)
	writeJSON(w, http.StatusOK, map[string]any{
		"text":     body,
		"markdown": body,
		"filename": filename,
		"format":   ext,
	})
}

func (s *Server) handleSteerSession(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	var req struct {
		Message string `json:"message"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err.Error() != "EOF" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	text := strings.TrimSpace(req.Message)
	if text == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Message is empty"})
		return
	}
	if s.claims == nil {
		writeJSON(w, http.StatusOK, map[string]any{"steered": false, "reason": "no_turn"})
		return
	}
	ok, reason := s.claims.TryPushNudge(sid, text)
	if !ok {
		writeJSON(w, http.StatusOK, map[string]any{"steered": false, "reason": reason})
		return
	}
	if s.sessions != nil {
		if _, found, err := s.sessions.Get(sid); err == nil && found {
			_, _ = s.sessions.AddMessage(sid, "user", text, nil, nil)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"steered": true, "reason": "ok"})
}

func (s *Server) handleSessionTodos(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	sess, ok, err := s.sessions.Get(sid)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	var root string
	if sess.ProjectPath != nil {
		raw := strings.TrimSpace(*sess.ProjectPath)
		if !isUnsetProjectPath(raw) && !isVolumeRootPath(raw) {
			p := raw
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				p = filepath.Dir(p)
			}
			if !isVolumeRootPath(p) {
				root = p
			}
		}
	}
	items := loadBuildTodos(root)
	if openTodoCount(items) == 0 {
		writeJSON(w, http.StatusOK, map[string]any{"todos": []any{}})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"todos": todosPublic(items)})
}

func (s *Server) handleSessionTimeline(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	if _, ok, err := s.sessions.Get(sid); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	} else if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	msgs, err := s.sessions.ListMessagesExport(sid, 500)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	steps := buildTimeline(msgs)
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": sid,
		"steps":      steps,
		"count":      len(steps),
	})
}

func (s *Server) handleSessionTimeTravel(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	var payload struct {
		MessageID string `json:"message_id"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)
	msgID := strings.TrimSpace(payload.MessageID)
	if msgID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "message_id required"})
		return
	}
	msg, msgSID, ok, err := s.sessions.GetMessage(msgID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok || msgSID != sid {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Message not found"})
		return
	}
	if msg.Reverted {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Message already reverted"})
		return
	}
	targetID := msgID
	original := contentAsString(msg.Content)
	if strings.EqualFold(msg.Role, "assistant") {
		all, err := s.sessions.ListMessagesExport(sid, 500)
		if err == nil {
			var prevUser *ChatMessage
			for i := range all {
				m := &all[i]
				if m.ID == msgID {
					break
				}
				if strings.EqualFold(m.Role, "user") && !m.Reverted {
					prevUser = m
				}
			}
			if prevUser != nil {
				targetID = prevUser.ID
				original = contentAsString(prevUser.Content)
			}
		}
	}
	count, err := s.sessions.RevertFrom(sid, targetID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":              "restored",
		"message_id":          targetID,
		"content":             original,
		"reverted_count":      count,
		"files":               map[string]any{"restored": 0, "deleted": 0, "skipped": 0, "blocked": 0, "paths": []any{}},
		"checkpoints_dropped": 0,
	})
}

func (s *Server) handleSessionCommand(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	var req struct {
		Command string `json:"command"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	cmd := strings.TrimSpace(req.Command)
	if cmd == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "command required"})
		return
	}
	result := s.executeSlashCommand(sid, cmd)
	out := map[string]any{
		"session_id": sid,
		"command":    cmd,
	}
	for k, v := range result {
		out[k] = v
	}
	writeJSON(w, http.StatusOK, out)
}

// ---- export helpers -------------------------------------------------------

func safeFilenameStem(title string) string {
	var b strings.Builder
	for _, r := range title {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '.' || r == '_' || r == '-' || r == ' ' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	safe := strings.TrimSpace(b.String())
	if safe == "" {
		safe = "Session"
	}
	if len(safe) > 60 {
		safe = safe[:60]
	}
	return safe
}

func contentAsString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprint(v)
}

func redactExport(content string) string {
	return exportRedactRE.ReplaceAllString(content, "[redacted]")
}

func exportContent(content, role string) string {
	if content == "" {
		return ""
	}
	content = redactExport(content)
	capN := exportContentCap
	if strings.EqualFold(role, "tool") {
		capN = exportToolCap
		one := strings.Join(strings.Fields(content), " ")
		if len(one) > capN {
			return one[:capN] + fmt.Sprintf("…[tool output truncated %d chars]", len(one)-capN)
		}
		return one
	}
	cleaned := inlineImageRE.ReplaceAllString(content, "[image omitted in export]")
	if len(cleaned) <= capN {
		return strings.TrimRight(cleaned, "\n")
	}
	return strings.TrimRight(cleaned[:capN], "\n") +
		fmt.Sprintf("\n\n…[export truncated %d chars]", len(cleaned)-capN)
}

func formatSessionTxt(title, sessionID string, messages []ChatMessage, model, agent *string) string {
	lines := []string{"# Remedy Session", "Title: " + title}
	if model != nil && strings.TrimSpace(*model) != "" {
		lines = append(lines, "Model: "+strings.TrimSpace(*model))
	}
	if agent != nil && strings.TrimSpace(*agent) != "" {
		lines = append(lines, "Agent: "+strings.TrimSpace(*agent))
	}
	lines = append(lines,
		"Source-Session-ID: "+sessionID,
		"Exported: "+time.Now().UTC().Format(time.RFC3339),
	)
	total := len(messages)
	if total > exportMaxMessages {
		lines = append(lines, fmt.Sprintf(
			"Messages: %d (exporting last %d; older omitted for size)", total, exportMaxMessages))
		messages = messages[total-exportMaxMessages:]
	} else {
		lines = append(lines, fmt.Sprintf("Messages: %d", total))
	}
	lines = append(lines, "")
	for _, m := range messages {
		role := strings.ToLower(strings.TrimSpace(m.Role))
		if role == "" {
			role = "user"
		}
		lines = append(lines, "===== "+strings.ToUpper(role)+" =====")
		var meta []string
		if a, ok := m.Agent.(string); ok && a != "" {
			meta = append(meta, "agent="+a)
		}
		if md, ok := m.Model.(string); ok && md != "" {
			meta = append(meta, "model="+md)
		}
		if c, ok := m.CreatedAt.(string); ok && c != "" {
			at := c
			if len(at) > 19 {
				at = at[:19]
			}
			meta = append(meta, "at="+at)
		}
		if len(meta) > 0 {
			lines = append(lines, "# "+strings.Join(meta, " | "))
		}
		body := exportContent(contentAsString(m.Content), role)
		if body != "" {
			lines = append(lines, body)
		}
		lines = append(lines, "")
	}
	return strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n"
}

func formatSessionMarkdown(title, sessionID string, messages []ChatMessage) string {
	lines := []string{
		"# " + title,
		"",
		"**Session ID:** `" + sessionID + "`",
		fmt.Sprintf("**Messages:** %d", len(messages)),
		"",
	}
	for _, m := range messages {
		role := strings.ToLower(strings.TrimSpace(m.Role))
		if role == "" {
			role = "user"
		}
		header := "**" + strings.ToUpper(role[:1]) + role[1:] + "**"
		if a, ok := m.Agent.(string); ok && a != "" {
			header += " (" + a + ")"
		}
		if md, ok := m.Model.(string); ok && md != "" {
			header += " — " + md
		}
		if c, ok := m.CreatedAt.(string); ok && c != "" {
			at := c
			if len(at) > 19 {
				at = at[:19]
			}
			header += " `" + at + "`"
		}
		lines = append(lines, header, "")
		body := exportContent(contentAsString(m.Content), role)
		if body != "" {
			lines = append(lines, body, "")
		}
		lines = append(lines, "---", "")
	}
	return strings.Join(lines, "\n")
}

// ---- todos ----------------------------------------------------------------

type buildTodoItem struct {
	ID      string `json:"id"`
	Content string `json:"content"`
	Status  string `json:"status"`
}

var validTodoStatus = map[string]struct{}{
	"pending": {}, "in_progress": {}, "completed": {}, "cancelled": {},
}

func loadBuildTodos(root string) []buildTodoItem {
	root = strings.TrimSpace(root)
	if root == "" || isVolumeRootPath(root) {
		return nil
	}
	fp := filepath.Join(root, ".remedy-build", "todos.json")
	raw, err := os.ReadFile(fp)
	if err != nil {
		return nil
	}
	var parsed any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil
	}
	var rows []any
	switch t := parsed.(type) {
	case []any:
		rows = t
	case map[string]any:
		if items, ok := t["items"].([]any); ok {
			rows = items
		}
	}
	out := make([]buildTodoItem, 0, len(rows))
	for _, row := range rows {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		content := strings.TrimSpace(fmt.Sprint(m["content"]))
		if content == "" || content == "<nil>" {
			continue
		}
		if len(content) > 240 {
			content = content[:240]
		}
		status := strings.ToLower(strings.TrimSpace(fmt.Sprint(m["status"])))
		if _, ok := validTodoStatus[status]; !ok {
			status = "pending"
		}
		id := strings.TrimSpace(fmt.Sprint(m["id"]))
		if id == "" || id == "<nil>" {
			id = newSessionID()[:8]
		}
		out = append(out, buildTodoItem{ID: id, Content: content, Status: status})
	}
	return out
}

func openTodoCount(items []buildTodoItem) int {
	n := 0
	for _, it := range items {
		if it.Status == "pending" || it.Status == "in_progress" {
			n++
		}
	}
	return n
}

func todosPublic(items []buildTodoItem) []map[string]string {
	out := make([]map[string]string, 0, len(items))
	for _, it := range items {
		out = append(out, map[string]string{
			"id":      it.ID,
			"content": it.Content,
			"status":  it.Status,
		})
	}
	return out
}

// ---- timeline -------------------------------------------------------------

func buildTimeline(messages []ChatMessage) []map[string]any {
	steps := make([]map[string]any, 0)
	stepN := 0
	var current map[string]any
	for _, msg := range messages {
		if msg.Reverted {
			continue
		}
		role := strings.ToLower(strings.TrimSpace(msg.Role))
		preview := strings.TrimSpace(contentAsString(msg.Content))
		if len(preview) > 160 {
			preview = preview[:157] + "…"
		}
		created := ""
		if c, ok := msg.CreatedAt.(string); ok {
			created = c
		}
		toolNames := toolNamesFromCalls(msg.ToolCalls)
		switch role {
		case "user":
			stepN++
			current = map[string]any{
				"step":          stepN,
				"id":            msg.ID,
				"kind":          "user",
				"label":         fmt.Sprintf("Step %d", stepN),
				"preview":       nonempty(preview, "(empty prompt)"),
				"created_at":    created,
				"message_id":    msg.ID,
				"tool_count":    0,
				"tools":         []string{},
				"assistant_ids": []string{},
				"can_restore":   true,
			}
			steps = append(steps, current)
		case "assistant":
			if current != nil {
				ids, _ := current["assistant_ids"].([]string)
				current["assistant_ids"] = append(ids, msg.ID)
				tc := 0
				if n, ok := current["tool_count"].(int); ok {
					tc = n
				}
				current["tool_count"] = tc + len(toolNames)
				existing, _ := current["tools"].([]string)
				current["tools"] = uniqueStringsCap(append(existing, toolNames...), 12)
				if preview != "" {
					if _, has := current["assistant_preview"]; !has {
						current["assistant_preview"] = preview
					}
				}
				parent, _ := current["message_id"].(string)
				steps = append(steps, map[string]any{
					"step":           stepN,
					"id":             msg.ID,
					"kind":           "assistant",
					"label":          fmt.Sprintf("Step %d · reply", stepN),
					"preview":        nonempty(preview, "(assistant)"),
					"created_at":     created,
					"message_id":     msg.ID,
					"parent_user_id": parent,
					"tool_count":     len(toolNames),
					"tools":          toolNamesCap(toolNames, 12),
					"can_restore":    true,
				})
			}
		case "system":
			steps = append(steps, map[string]any{
				"step":        stepN,
				"id":          msg.ID,
				"kind":        "system",
				"label":       "System",
				"preview":     preview,
				"created_at":  created,
				"message_id":  msg.ID,
				"can_restore": false,
			})
		}
	}
	return steps
}

func toolNamesFromCalls(v any) []string {
	list, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, t := range list {
		if m, ok := t.(map[string]any); ok {
			name := strings.TrimSpace(fmt.Sprint(m["name"]))
			if name == "" || name == "<nil>" {
				name = "?"
			}
			out = append(out, name)
		} else {
			out = append(out, "?")
		}
	}
	return out
}

func uniqueStringsCap(in []string, capN int) []string {
	seen := map[string]struct{}{}
	out := make([]string, 0, len(in))
	for _, s := range in {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
		if len(out) >= capN {
			break
		}
	}
	return out
}

func toolNamesCap(in []string, capN int) []string {
	if len(in) <= capN {
		return in
	}
	return in[:capN]
}

func nonempty(s, fallback string) string {
	if strings.TrimSpace(s) == "" {
		return fallback
	}
	return s
}
