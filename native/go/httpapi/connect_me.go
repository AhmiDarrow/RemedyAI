package httpapi

import (
	"log"
	"net/http"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// SetFocusedSession records the desktop-focused chat id (host_bridge parity).
func (s *Server) SetFocusedSession(sessionID string) {
	if s == nil {
		return
	}
	s.focusedMu.Lock()
	s.focusedSessionID = strings.TrimSpace(sessionID)
	s.focusedMu.Unlock()
}

// FocusedSessionID returns the desktop-focused chat id, if any.
func (s *Server) FocusedSessionID() string {
	if s == nil {
		return ""
	}
	s.focusedMu.Lock()
	defer s.focusedMu.Unlock()
	return s.focusedSessionID
}

// connectMeSessionID prefers a focused sid that is streaming, else the first
// active stream claim, else the focused sid when idle (Python connect.py).
func (s *Server) connectMeSessionID() string {
	var active []string
	if s.claims != nil {
		active = s.claims.ActiveSessionIDs()
	}
	focused := s.FocusedSessionID()
	if focused != "" {
		for _, sid := range active {
			if sid == focused {
				return focused
			}
		}
	}
	if len(active) > 0 {
		return active[0]
	}
	return focused
}

func (s *Server) connectMePayload() map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	paused := cfgBool(cfg, "connect_paused", false) || connect.IsPaused(home)
	reachable := "lan"
	if paused {
		reachable = "paused"
	}
	turnActive := s.claims != nil && s.claims.AnyActive()
	var sessionID any
	if sid := s.connectMeSessionID(); sid != "" {
		sessionID = sid
	}
	return map[string]any{
		"panes":       connect.PanesFromConfig(map[string]any(cfg)),
		"paused":      paused,
		"reachable":   reachable,
		"device_id":   nil,
		"session_id":  sessionID,
		"turn_active": turnActive,
		"device":      map[string]any{"id": nil, "name": nil},
	}
}

// connectMeAuthorized gates the unauthenticated /connect/me alias.
//
// /connect/me is registered outside /api/, so the Bearer middleware never sees
// it, yet the payload names the focused session, whether a turn is running and
// which panes are open — enough to watch the owner. Authenticated callers
// (/api/connect/me with the API token) pass straight through; everyone else
// must be on loopback AND have addressed a loopback Host, which is the same
// pair of checks token bootstrap uses to survive DNS rebinding.
func (s *Server) connectMeAuthorized(r *http.Request) bool {
	if s != nil && s.token != "" && requestAuthorized(r, s.token) {
		return true
	}
	return clientIsLoopback(r) && hostHeaderIsLoopback(r)
}

// handleConnectMe serves GET /connect/me and GET /api/connect/me.
func (s *Server) handleConnectMe(w http.ResponseWriter, r *http.Request) {
	if !s.connectMeAuthorized(r) {
		log.Printf("connect/me: refused (not loopback, or Host is not loopback — rebinding blocked)")
		writeJSON(w, http.StatusForbidden, map[string]string{
			"detail": "loopback only (send the API token for /api/connect/me)",
		})
		return
	}
	writeJSON(w, http.StatusOK, s.connectMePayload())
}

// handleConnectStop aborts the /connect/me session (never the first sessions row).
func (s *Server) handleConnectStop(w http.ResponseWriter, _ *http.Request) {
	sid := s.connectMeSessionID()
	if sid == "" {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":     "idle",
			"session_id": nil,
			"notified":   0,
		})
		return
	}
	reason := abortReasonStop
	reasonPtr := &reason
	n := 0
	if s.claims != nil {
		n = s.claims.Abort(sid, nil, reasonPtr)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":     "aborted",
		"session_id": sid,
		"notified":   n,
		"reason":     reason,
	})
}
