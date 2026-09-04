package httpapi

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
)

type attachmentUploadRequest struct {
	Filename    string  `json:"filename"`
	ContentType *string `json:"content_type"`
	DataBase64  string  `json:"data_base64"`
}

func (s *Server) attachmentHomeDir() string {
	cfg := LoadConfig(s.homeDir)
	if h := strings.TrimSpace(cfgString(cfg, "home_dir", "")); h != "" {
		return h
	}
	if home := ResolveHomeDir(s.homeDir); home != "" {
		return home
	}
	return s.homeDir
}

func (s *Server) handleUploadAttachment(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	if sid == "" {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}
	_, ok, err := s.sessions.Get(sid)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}

	var req attachmentUploadRequest
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil && !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(req.DataBase64))
	if err != nil {
		// Padding-tolerant fallback (some clients strip '=').
		raw, err = base64.RawStdEncoding.DecodeString(strings.TrimSpace(req.DataBase64))
	}
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": fmt.Sprintf("Invalid base64 payload: %v", err),
		})
		return
	}
	if len(raw) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Empty file"})
		return
	}
	if len(raw) > maxAttachmentBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{
			"detail": fmt.Sprintf("File too large (max %d MB)", maxAttachmentBytes/(1024*1024)),
		})
		return
	}

	filename := strings.TrimSpace(req.Filename)
	if filename == "" {
		filename = "upload.bin"
	}
	var contentType string
	if req.ContentType != nil {
		contentType = strings.TrimSpace(*req.ContentType)
	}

	meta, err := saveUpload(sid, filename, raw, contentType, s.attachmentHomeDir())
	if err != nil {
		msg := err.Error()
		if strings.Contains(strings.ToLower(msg), "too large") {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"detail": msg})
			return
		}
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": msg})
		return
	}
	writeJSON(w, http.StatusOK, meta)
}

func (s *Server) handleGetAttachment(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	filename := r.PathValue("filename")
	if sid == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid path"})
		return
	}
	path, notFound, err := resolveAttachmentFile(sid, filename, s.attachmentHomeDir())
	if err != nil {
		status := http.StatusBadRequest
		detail := err.Error()
		if notFound {
			status = http.StatusNotFound
			detail = "Attachment not found"
		}
		writeJSON(w, status, map[string]string{"detail": detail})
		return
	}
	safe := filepath.Base(path)
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, safe))
	http.ServeFile(w, r, path)
}
