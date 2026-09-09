package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// handleStreamAttach serves
// GET /api/sessions/{id}/stream/attach?request_id=…&after=<seq>
//
// It replays the turn log from `after` and then tails the live turn until the
// done record. Every frame carries the seq of the record that produced it, so
// a client that reloads mid-build resumes exactly once: frames at or below
// `after` are never re-sent, and nothing between `after` and the live tail is
// skipped because the log — not the in-memory queue — is the source.
func (s *Server) handleStreamAttach(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	requestID := strings.TrimSpace(r.URL.Query().Get("request_id"))
	if requestID == "" && s.claims != nil {
		requestID = s.claims.ActiveRequest(sid)
	}
	if safeTurnID(requestID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "request_id required"})
		return
	}
	after := uint64(0)
	if raw := strings.TrimSpace(r.URL.Query().Get("after")); raw != "" {
		n, err := strconv.ParseUint(raw, 10, 64)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid after"})
			return
		}
		after = n
	}
	path := turnLogPath(s.homeDir, sid, requestID)
	if path == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Turn log not available"})
		return
	}
	reader, err := openTurnLogReader(path)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Turn not found"})
		return
	}
	defer reader.Close()

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "streaming unsupported"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	keepalive := time.NewTicker(sseKeepaliveInterval)
	defer keepalive.Stop()

	lastSent := after
	for {
		progressed := false
		for {
			rec, ok := reader.next()
			if !ok {
				break
			}
			if rec.Seq <= lastSent {
				continue
			}
			lastSent = rec.Seq
			event, payload, renders := rec.frame()
			if !renders {
				continue
			}
			if _, err := io.WriteString(w, sseFrame(event, payload)); err != nil {
				return
			}
			flusher.Flush()
			keepalive.Reset(sseKeepaliveInterval)
			progressed = true
			if rec.T == "done" {
				return
			}
		}
		live := s.turns.get(sid, requestID)
		if live == nil {
			if progressed {
				// The writer may have finished between the last read and the
				// registry check — drain once more before deciding.
				continue
			}
			// No live writer and no done record: the process died mid-turn.
			_, _ = io.WriteString(w, sseFrame("done", map[string]any{
				"type":       "done",
				"request_id": requestID,
				"status":     "interrupted",
				"seq":        lastSent,
			}))
			flusher.Flush()
			return
		}
		select {
		case <-r.Context().Done():
			return
		case <-live.wait():
		case <-keepalive.C:
			if _, err := io.WriteString(w, sseKeepaliveComment); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// handleTurnToolEvidence serves
// GET /api/sessions/{id}/turns/{request_id}/tools/{call_id}[?image=<sha256>]
//
// The SSE stream carries a short preview of every tool result; this route
// returns what Remedy actually saw — the full recorded output, and the image
// bytes stored beside the log.
func (s *Server) handleTurnToolEvidence(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.PathValue("id"))
	requestID := strings.TrimSpace(r.PathValue("request_id"))
	callID := strings.TrimSpace(r.PathValue("call_id"))
	if safeTurnID(requestID) == "" || callID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "request_id and call_id required"})
		return
	}
	path := turnLogPath(s.homeDir, sid, requestID)
	if path == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Turn log not available"})
		return
	}
	if _, err := os.Stat(path); err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Turn not found"})
		return
	}
	ev, err := findToolEvidence(path, callID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
		return
	}
	if !ev.Found {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Tool call not found in this turn"})
		return
	}

	if sha := strings.TrimSpace(r.URL.Query().Get("image")); sha != "" {
		for _, img := range ev.Images {
			if img.SHA256 != sha || img.Path == "" {
				continue
			}
			root := turnsDir(s.homeDir, sid)
			full := filepath.Join(root, filepath.FromSlash(img.Path))
			if !pathUnder(full, root) {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Image not found"})
				return
			}
			data, readErr := os.ReadFile(full)
			if readErr != nil {
				writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Image not found"})
				return
			}
			w.Header().Set("Content-Type", mediaForImageExt(full))
			w.Header().Set("Cache-Control", "private, max-age=86400")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(data)
			return
		}
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Image not found"})
		return
	}

	base := "/api/sessions/" + sid + "/turns/" + requestID + "/tools/" + callID
	images := make([]map[string]any, 0, len(ev.Images))
	for _, img := range ev.Images {
		images = append(images, map[string]any{
			"media_type": img.MediaType,
			"sha256":     img.SHA256,
			"bytes":      img.Bytes,
			"path":       img.Path,
			"url":        base + "?image=" + img.SHA256,
		})
	}
	var input any
	if len(ev.Input) > 0 {
		_ = json.Unmarshal(ev.Input, &input)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": sid,
		"request_id": requestID,
		"call_id":    callID,
		"name":       ev.Name,
		"input":      input,
		"ok":         !ev.IsError,
		"is_error":   ev.IsError,
		"preview":    ev.Preview,
		"output":     ev.Output,
		"bytes":      len(ev.Output),
		"images":     images,
	})
}
