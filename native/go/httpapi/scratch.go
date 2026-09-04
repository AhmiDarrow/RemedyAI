package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const scratchMaxChars = 256_000

var scratchSafeID = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func scratchDir(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "scratch")
}

func scratchID(sessionID string) string {
	raw := strings.TrimSpace(sessionID)
	if raw == "" {
		raw = "_global"
	}
	cleaned := strings.Trim(scratchSafeID.ReplaceAllString(raw, "_"), ".-")
	if cleaned == "" {
		cleaned = "_global"
	}
	if len(cleaned) > 80 {
		cleaned = cleaned[:80]
	}
	return cleaned
}

func scratchPath(homeDir, sessionID string) string {
	return filepath.Join(scratchDir(homeDir), scratchID(sessionID)+".md")
}

func readScratch(homeDir, sessionID string) string {
	raw, err := os.ReadFile(scratchPath(homeDir, sessionID))
	if err != nil {
		return ""
	}
	return string(raw)
}

func writeScratch(homeDir, sessionID, text string, append bool) (string, error) {
	body := text
	if append {
		body = readScratch(homeDir, sessionID) + body
	}
	if len(body) > scratchMaxChars {
		body = body[:scratchMaxChars]
	}
	dir := scratchDir(homeDir)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	path := scratchPath(homeDir, sessionID)
	tmp, err := os.CreateTemp(dir, ".scratch-*.tmp")
	if err != nil {
		return "", err
	}
	tmpPath := tmp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmpPath)
		}
	}()
	if _, err := tmp.WriteString(body); err != nil {
		_ = tmp.Close()
		return "", err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return "", err
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	_ = os.Remove(path)
	if err := os.Rename(tmpPath, path); err != nil {
		return "", err
	}
	cleanup = false
	_ = os.Chmod(path, 0o600)
	return body, nil
}

func (s *Server) handleGetScratch(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	text := readScratch(s.homeDir, sid)
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": scratchID(sid),
		"text":       text,
		"chars":      len(text),
	})
}

func (s *Server) handlePutScratch(w http.ResponseWriter, r *http.Request) {
	defer r.Body.Close()
	var body struct {
		SessionID *string `json:"session_id"`
		Text      any     `json:"text"`
		Append    bool    `json:"append"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	sid := ""
	if body.SessionID != nil {
		sid = strings.TrimSpace(*body.SessionID)
	}
	text := ""
	switch t := body.Text.(type) {
	case nil:
		text = ""
	case string:
		text = t
	default:
		text = anyString(t)
	}
	stored, err := writeScratch(s.homeDir, sid, text, body.Append)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": scratchID(sid),
		"text":       stored,
		"chars":      len(stored),
	})
}
