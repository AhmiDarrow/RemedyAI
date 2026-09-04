package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

func (s *Server) bridge() *HostBridge {
	if s == nil {
		return nil
	}
	s.bridgeOnce.Do(func() {
		s.hostBridge = newHostBridge(s.homeDir)
		s.hostBridge.setSessionStreaming(func(sid string) bool {
			return s.claims != nil && s.claims.IsClaimed(sid)
		})
	})
	return s.hostBridge
}

func (s *Server) handleComputerHostHello(w http.ResponseWriter, r *http.Request) {
	b := s.bridge()
	b.mu.Lock()
	b.markHostAlive(false, "")
	b.mu.Unlock()
	var req struct {
		Client    string         `json:"client"`
		Bounds    map[string]any `json:"bounds"`
		Scale     *float64       `json:"scale"`
		SessionID string         `json:"session_id"`
	}
	client := "desktop"
	if r.Body != nil {
		defer r.Body.Close()
		_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req)
		if strings.TrimSpace(req.Client) != "" {
			client = req.Client
		}
	}
	b.mu.Lock()
	if strings.TrimSpace(req.SessionID) != "" {
		b.setFocusedSession(req.SessionID)
		s.SetFocusedSession(req.SessionID)
	}
	if req.Bounds != nil {
		b.setBrowserBounds(req.Bounds, req.Scale)
	}
	connected := b.hostConnected()
	b.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":             true,
		"client":         client,
		"host_connected": connected,
		"poller_required": true,
	})
}

func (s *Server) handleComputerHostStatus(w http.ResponseWriter, _ *http.Request) {
	b := s.bridge()
	b.mu.Lock()
	defer b.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"host_connected":      b.hostConnected(),
		"focused_session_id":  b.focusedSessionID(),
		"browser_bounds":      b.getBrowserBounds(),
		"pending_jobs":        b.pendingCount(),
		"ui_command":          b.peekUICommandLocked(),
		"jobs_root":           b.root,
		"pending_hint":        "Rust computer-host claims GET /api/computer/jobs/next",
		"host_driver":         b.hostDriver(),
	})
}

func (s *Server) handleComputerUICommand(w http.ResponseWriter, r *http.Request) {
	b := s.bridge()
	q := r.URL.Query()
	take := q.Get("take") == "1" || strings.EqualFold(q.Get("take"), "true")
	sessionID := q.Get("session_id")
	driver := q.Get("driver")
	b.mu.Lock()
	if take {
		d := driver
		if d == "" {
			d = "rust"
		}
		b.markHostAlive(true, d)
	} else {
		b.markHostAlive(false, "")
	}
	if strings.TrimSpace(sessionID) != "" {
		b.setFocusedSession(sessionID)
		s.SetFocusedSession(sessionID)
	}
	b.mu.Unlock()
	var cmd map[string]any
	if take {
		cmd = b.takeUICommand(sessionID)
	} else {
		cmd = b.peekUICommand()
	}
	writeJSON(w, http.StatusOK, map[string]any{"command": cmd})
}

func (s *Server) handleComputerUICommandAck(w http.ResponseWriter, r *http.Request) {
	b := s.bridge()
	var req struct {
		JobID *string `json:"job_id"`
	}
	if r.Body != nil {
		defer r.Body.Close()
		_ = json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req)
	}
	if jid := r.URL.Query().Get("job_id"); jid != "" && req.JobID == nil {
		req.JobID = &jid
	}
	b.clearUICommand(req.JobID)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleComputerCapture(w http.ResponseWriter, r *http.Request) {
	var req struct {
		X      *int    `json:"x"`
		Y      *int    `json:"y"`
		Width  *int    `json:"width"`
		Height *int    `json:"height"`
		Scale  float64 `json:"scale"`
		Label  string  `json:"label"`
	}
	req.Scale = 1
	req.Label = "capture"
	if r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
			return
		}
	}
	labelL := strings.ToLower(req.Label)
	browserish := strings.Contains(labelL, "browser") || strings.Contains(labelL, "rail")
	hasBounds := req.X != nil && req.Y != nil && req.Width != nil && req.Height != nil
	if browserish && !hasBounds {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false,
			"error": "Browser rail bounds missing — open the Browser rail and " +
				"wait for bounds. Not capturing the full desktop as a rail shot.",
			"target": "desktop",
		})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	var info map[string]any
	var err error
	if hasBounds {
		info, err = core.ScreenshotRegionPNG(home, req.Label, *req.X, *req.Y, *req.Width, *req.Height, req.Scale)
	} else {
		info, err = core.ScreenshotPNG(home, req.Label)
	}
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": err.Error()})
		return
	}
	out := map[string]any{"ok": true, "capture": info}
	if !hasBounds {
		out["target"] = "desktop"
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleComputerJobsNext(w http.ResponseWriter, r *http.Request) {
	b := s.bridge()
	q := r.URL.Query()
	driver := q.Get("driver")
	if driver == "" {
		driver = "rust"
	}
	b.mu.Lock()
	b.markHostAlive(true, driver)
	if sid := strings.TrimSpace(q.Get("session_id")); sid != "" {
		b.setFocusedSession(sid)
		s.SetFocusedSession(sid)
	}
	b.mu.Unlock()

	var exclude map[string]struct{}
	if ex := strings.TrimSpace(q.Get("exclude")); ex != "" {
		exclude = map[string]struct{}{}
		for _, p := range strings.Split(ex, ",") {
			p = strings.ToLower(strings.TrimSpace(p))
			if p != "" {
				exclude[p] = struct{}{}
			}
		}
	}
	var only map[string]struct{}
	if o := strings.TrimSpace(q.Get("only")); o != "" {
		only = map[string]struct{}{}
		for _, p := range strings.Split(o, ",") {
			p = strings.ToLower(strings.TrimSpace(p))
			if p != "" {
				only[p] = struct{}{}
			}
		}
	}
	waitMS, _ := strconv.Atoi(q.Get("wait_ms"))
	if waitMS < 0 {
		waitMS = 0
	}
	if waitMS > 5000 {
		waitMS = 5000
	}
	job := b.claimNext(exclude, only, q.Get("session_id"), float64(waitMS)/1000.0)
	if job == nil {
		writeJSON(w, http.StatusOK, map[string]any{"job": nil})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"job": job.toDict()})
}

func (s *Server) handleComputerJobComplete(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("job_id")
	var req struct {
		OK    bool           `json:"ok"`
		Result map[string]any `json:"result"`
		Error *string        `json:"error"`
	}
	req.OK = true
	if r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<22)).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
			return
		}
	}
	b := s.bridge()
	b.mu.Lock()
	b.markHostAlive(false, "")
	b.mu.Unlock()
	job := b.complete(jobID, req.OK, req.Result, req.Error)
	if job == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "job not found"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"job": job.toDict()})
}

func (s *Server) handleComputerJobCancel(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("job_id")
	job := s.bridge().cancel(jobID)
	if job == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "job not found"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"job": job.toDict()})
}

func a11yCORS(w http.ResponseWriter, r *http.Request) {
	origin := strings.TrimSpace(r.Header.Get("Origin"))
	switch origin {
	case "http://127.0.0.1:7400", "http://localhost:7400",
		"http://127.0.0.1:5173", "http://localhost:5173", "null":
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Vary", "Origin")
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
	}
}

func (s *Server) handleComputerA11yPushOptions(w http.ResponseWriter, r *http.Request) {
	a11yCORS(w, r)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleComputerA11yPush(w http.ResponseWriter, r *http.Request) {
	var req struct {
		JobID    string           `json:"job_id"`
		Elements []map[string]any `json:"elements"`
	}
	if r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<22)).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
			return
		}
	}
	jid := strings.TrimSpace(req.JobID)
	if jid == "" || len(jid) < 32 || !isAlnum(jid) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid job_id"})
		return
	}
	els := make([]map[string]any, 0, len(req.Elements))
	for _, e := range req.Elements {
		if e != nil {
			els = append(els, e)
		}
		if len(els) >= 120 {
			break
		}
	}
	job := s.bridge().completeA11yPush(jid, els)
	if job == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "job not found or not a snapshot"})
		return
	}
	a11yCORS(w, r)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok":true}`))
}

func isAlnum(s string) bool {
	for _, r := range s {
		if (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') {
			return false
		}
	}
	return true
}
