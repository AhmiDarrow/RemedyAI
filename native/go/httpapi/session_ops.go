package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const (
	importMaxChars    = 2_000_000
	importMaxMessages = 2_000
)

var (
	roleHeaderRE  = regexp.MustCompile(`(?i)^=====\s*(USER|ASSISTANT|SYSTEM|TOOL)\s*=====\s*$`)
	legacyRoleRE  = regexp.MustCompile(`(?i)^\*\*(User|Assistant|System|Tool)\*\*`)
	metaLineRE    = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$`)
)

type parsedMessage struct {
	Role    string
	Content string
	Model   string
	Agent   string
}

type parsedSession struct {
	Title    string
	Model    string
	Agent    string
	Messages []parsedMessage
}

func (s *Server) handleEditFromMessage(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	msgID := strings.TrimSpace(r.PathValue("msg_id"))
	msg, msgSID, ok, err := s.sessions.GetMessage(msgID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok || msgSID != sid {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Message not found"})
		return
	}
	if !strings.EqualFold(msg.Role, "user") {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Only user messages can be edited. Use Edit on your message to revise and resend.",
		})
		return
	}
	if msg.Reverted {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Message already reverted"})
		return
	}
	original := contentAsString(msg.Content)
	count, err := s.sessions.RevertFrom(sid, msgID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "ready_to_edit",
		"msg_id":         msgID,
		"content":        original,
		"text":           original,
		"reverted_count": count,
	})
}

func (s *Server) handleBulkSetSessionProject(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	defer r.Body.Close()
	var req struct {
		SessionIDs  []string `json:"session_ids"`
		ProjectPath *string  `json:"project_path"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	ids := make([]string, 0, len(req.SessionIDs))
	for _, id := range req.SessionIDs {
		if t := strings.TrimSpace(id); t != "" {
			ids = append(ids, t)
		}
	}
	if len(ids) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "session_ids required"})
		return
	}
	if len(ids) > 200 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "At most 200 sessions per bulk move"})
		return
	}
	projectPath, err := s.resolveSessionProject(req.ProjectPath)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	updated := make([]string, 0, len(ids))
	missing := make([]string, 0)
	for _, sid := range ids {
		upd := updateSessionRequest{ProjectPath: optionalString{Set: true, Value: projectPath}}
		sess, ok, err := s.sessions.Update(sid, upd)
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
		if !ok {
			missing = append(missing, sid)
			continue
		}
		updated = append(updated, sid)
		title := sess.Title
		count := sess.MessageCount
		s.publishSessionEvent(SessionEvent{
			Type:          "session_updated",
			SessionID:     sess.ID,
			OriginChannel: sess.OriginChannel,
			Title:         &title,
			MessageCount:  &count,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":       "ok",
		"project_path": projectPath,
		"updated":      updated,
		"missing":      missing,
		"count":        len(updated),
	})
}

func (s *Server) resolveSessionProject(raw *string) (*string, error) {
	if raw == nil {
		return nil, nil
	}
	trimmed := strings.TrimSpace(*raw)
	if isUnsetProjectPath(trimmed) {
		return nil, nil
	}
	abs, err := resolveAbsPath(trimmed)
	if err != nil {
		return nil, err
	}
	if isProtectedSecretPath(abs, s.remedyHomeDir()) || hasBlockedSearchPart(abs) {
		return nil, fmt.Errorf(
			"Project path is not allowed: %s. Pick a user folder, not an OS or program directory.",
			abs,
		)
	}
	if st, err := os.Stat(abs); err == nil && !st.IsDir() {
		abs = filepath.Dir(abs)
	}
	if err := os.MkdirAll(abs, 0o700); err != nil {
		// Still bind to the resolved path even if mkdir fails (Python parity).
		return &abs, nil
	}
	return &abs, nil
}

func (s *Server) handleImportSession(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	defer r.Body.Close()
	var req struct {
		Text        string  `json:"text"`
		Path        string  `json:"path"`
		Title       string  `json:"title"`
		Model       *string `json:"model"`
		Agent       *string `json:"agent"`
		ProjectPath *string `json:"project_path"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, importMaxChars+64_000)).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	text := strings.TrimSpace(req.Text)
	if text == "" && strings.TrimSpace(req.Path) != "" {
		loaded, err := s.readImportPath(strings.TrimSpace(req.Path))
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
			return
		}
		text = loaded
	}
	if strings.TrimSpace(text) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Provide text or path to a session .txt / .md export",
		})
		return
	}
	if len(text) > importMaxChars {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Import text too large",
		})
		return
	}
	parsed, err := parseSessionText(text)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	if len(parsed.Messages) > importMaxMessages {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Import has too many messages",
		})
		return
	}
	title := strings.TrimSpace(req.Title)
	if title == "" {
		title = parsed.Title
	}
	if title == "" {
		title = "Imported Session"
	}
	if len(title) > 200 {
		title = title[:200]
	}
	model := req.Model
	if model == nil && parsed.Model != "" {
		m := parsed.Model
		model = &m
	}
	agent := req.Agent
	if agent == nil && parsed.Agent != "" {
		a := parsed.Agent
		agent = &a
	}
	projectPath := req.ProjectPath
	if projectPath == nil {
		raw := s.configProjectRaw()
		if raw != "" {
			projectPath = &raw
		}
	}
	pp, err := s.resolveSessionProject(projectPath)
	if err != nil {
		// Soft: leave nil rather than fail import.
		pp = nil
	}
	sess, err := s.sessions.Create(createSessionRequest{
		Title:       title,
		Model:       model,
		Agent:       agent,
		ProjectPath: pp,
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	imported := 0
	for _, pm := range parsed.Messages {
		role := strings.ToLower(strings.TrimSpace(pm.Role))
		if role == "" {
			role = "user"
		}
		if role == "system" && strings.TrimSpace(pm.Content) == "" {
			continue
		}
		var mPtr, aPtr *string
		if pm.Model != "" {
			m := pm.Model
			mPtr = &m
		} else {
			mPtr = model
		}
		if pm.Agent != "" {
			a := pm.Agent
			aPtr = &a
		} else {
			aPtr = agent
		}
		if _, err := s.sessions.AddMessage(sess.ID, role, pm.Content, mPtr, aPtr); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
		imported++
	}
	refreshed, ok, _ := s.sessions.Get(sess.ID)
	if ok {
		sess = refreshed
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"id":                sess.ID,
		"title":             sess.Title,
		"model":             sess.Model,
		"agent":             sess.Agent,
		"project_path":      sess.ProjectPath,
		"message_count":     sess.MessageCount,
		"imported_messages": imported,
		"created_at":        sess.CreatedAt,
		"updated_at":        sess.UpdatedAt,
	})
}

func (s *Server) readImportPath(raw string) (string, error) {
	scope := effectiveAccessScope(s.configAccessScope(), s.configProjectRaw())
	proj := s.configProjectRaw()
	if isUnsetProjectPath(proj) {
		if owner := defaultOwnerFilesBase(); owner != "" {
			proj = owner
		} else if uh := s.userHomeForWorkspace(); uh != "" {
			proj = uh
		} else {
			cwd, _ := os.Getwd()
			if !isPackagedInstallDir(cwd) {
				proj = cwd
			}
		}
	}
	roots := allowedReadRoots(scope, proj, s.userHomeForWorkspace())
	path, err := resolveUnderRoots(raw, roots, scope)
	if err != nil {
		return "", err
	}
	if isProtectedSecretPath(path, s.remedyHomeDir()) {
		return "", errPathOutside
	}
	st, err := os.Stat(path)
	if err != nil || !st.Mode().IsRegular() {
		return "", os.ErrNotExist
	}
	if st.Size() > int64(importMaxChars)*4 {
		return "", errPathOutside
	}
	rawBytes, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(rawBytes), nil
}

func parseSessionText(text string) (parsedSession, error) {
	raw := strings.ReplaceAll(text, "\r\n", "\n")
	raw = strings.ReplaceAll(raw, "\r", "\n")
	if strings.TrimSpace(raw) == "" {
		return parsedSession{}, errEmptySessionText
	}
	lines := strings.Split(raw, "\n")
	hasRole := false
	for _, ln := range lines {
		if roleHeaderRE.MatchString(strings.TrimSpace(ln)) {
			hasRole = true
			break
		}
	}
	if hasRole || (len(lines) > 0 && strings.HasPrefix(strings.ToLower(strings.TrimSpace(lines[0])), "# remedy session")) {
		return parseNativeSession(lines), nil
	}
	for _, ln := range lines {
		if legacyRoleRE.MatchString(strings.TrimSpace(ln)) {
			return parseLegacyMarkdown(lines), nil
		}
	}
	title := "Imported Session"
	bodyStart := 0
	first := strings.TrimSpace(lines[0])
	if strings.HasPrefix(first, "#") {
		title = strings.TrimSpace(strings.TrimLeft(first, "#"))
		if title == "" {
			title = "Imported Session"
		}
		bodyStart = 1
		for bodyStart < len(lines) && strings.TrimSpace(lines[bodyStart]) == "" {
			bodyStart++
		}
	}
	content := strings.TrimSpace(strings.Join(lines[bodyStart:], "\n"))
	if content == "" {
		return parsedSession{}, errNoMessageContent
	}
	return parsedSession{
		Title:    title,
		Messages: []parsedMessage{{Role: "user", Content: content}},
	}, nil
}

var (
	errEmptySessionText = errImport("Empty session text")
	errNoMessageContent = errImport("No message content found in file")
)

type importError string

func (e importError) Error() string { return string(e) }
func errImport(s string) error      { return importError(s) }

func parseNativeSession(lines []string) parsedSession {
	session := parsedSession{Title: "Imported Session"}
	i := 0
	for i < len(lines) {
		ln := lines[i]
		if roleHeaderRE.MatchString(strings.TrimSpace(ln)) {
			break
		}
		stripped := strings.TrimSpace(ln)
		if strings.HasPrefix(stripped, "#") && strings.Contains(strings.ToLower(stripped), "remedy session") {
			i++
			continue
		}
		if strings.HasPrefix(stripped, "#") {
			if session.Title == "Imported Session" {
				t := strings.TrimSpace(strings.TrimLeft(stripped, "#"))
				if t != "" && !strings.HasPrefix(strings.ToLower(t), "remedy") {
					session.Title = t
				}
			}
			i++
			continue
		}
		if m := metaLineRE.FindStringSubmatch(stripped); m != nil {
			key, val := strings.ToLower(m[1]), strings.TrimSpace(m[2])
			switch key {
			case "title":
				if val != "" {
					session.Title = val
				}
			case "model":
				session.Model = val
			case "agent":
				session.Agent = val
			}
		}
		i++
	}
	var currentRole string
	var currentMeta map[string]string
	var buf []string
	flush := func() {
		if currentRole == "" {
			buf = nil
			return
		}
		content := strings.Trim(strings.Join(buf, "\n"), "\n")
		if strings.TrimSpace(content) != "" || currentRole == "user" || currentRole == "assistant" {
			pm := parsedMessage{Role: currentRole, Content: strings.TrimSpace(content)}
			if currentMeta != nil {
				pm.Model = currentMeta["model"]
				pm.Agent = currentMeta["agent"]
			}
			if pm.Model == "" {
				pm.Model = session.Model
			}
			if pm.Agent == "" {
				pm.Agent = session.Agent
			}
			session.Messages = append(session.Messages, pm)
		}
		currentRole = ""
		currentMeta = nil
		buf = nil
	}
	for i < len(lines) {
		ln := lines[i]
		if rm := roleHeaderRE.FindStringSubmatch(strings.TrimSpace(ln)); rm != nil {
			flush()
			currentRole = strings.ToLower(rm[1])
			currentMeta = map[string]string{}
			if i+1 < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i+1]), "#") {
				peek := strings.TrimSpace(lines[i+1])
				if strings.Contains(peek, "=") {
					body := strings.TrimSpace(strings.TrimPrefix(peek, "#"))
					for _, part := range strings.Split(body, "|") {
						part = strings.TrimSpace(part)
						if k, v, ok := strings.Cut(part, "="); ok {
							currentMeta[strings.ToLower(strings.TrimSpace(k))] = strings.TrimSpace(v)
						}
					}
					i += 2
					continue
				}
			}
			i++
			continue
		}
		buf = append(buf, ln)
		i++
	}
	flush()
	return session
}

func parseLegacyMarkdown(lines []string) parsedSession {
	session := parsedSession{Title: "Imported Session"}
	if len(lines) > 0 {
		first := strings.TrimSpace(lines[0])
		if strings.HasPrefix(first, "#") {
			t := strings.TrimSpace(strings.TrimLeft(first, "#"))
			if t != "" {
				session.Title = t
			}
		}
	}
	var currentRole string
	var buf []string
	flush := func() {
		if currentRole == "" {
			buf = nil
			return
		}
		content := strings.TrimSpace(strings.Join(buf, "\n"))
		if content != "" {
			session.Messages = append(session.Messages, parsedMessage{
				Role: currentRole, Content: content,
			})
		}
		currentRole = ""
		buf = nil
	}
	for _, ln := range lines {
		if m := legacyRoleRE.FindStringSubmatch(strings.TrimSpace(ln)); m != nil {
			flush()
			currentRole = strings.ToLower(m[1])
			continue
		}
		if currentRole != "" {
			buf = append(buf, ln)
		}
	}
	flush()
	return session
}
