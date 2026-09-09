package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestConnectMeIdleNullSession(t *testing.T) {
	s, _ := newConnectTestServer(t)
	s.SetFocusedSession("")
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if sid, ok := body["session_id"]; ok && sid != nil && sid != "" {
		t.Fatalf("session_id=%v", sid)
	}
	if body["turn_active"] != false {
		t.Fatalf("turn_active=%v", body["turn_active"])
	}
	if body["device_id"] != nil {
		t.Fatalf("device_id=%v", body["device_id"])
	}
	dev, _ := body["device"].(map[string]any)
	if dev == nil || dev["id"] != nil || dev["name"] != nil {
		t.Fatalf("device=%v", body["device"])
	}
	if _, ok := body["panes"].(map[string]any); !ok {
		t.Fatalf("panes=%T", body["panes"])
	}
	low := strings.ToLower(text)
	for _, bad := range []string{"local_api_token", "bearer", connectTestToken, "ps="} {
		if strings.Contains(low, strings.ToLower(bad)) {
			t.Fatalf("leak %q in %s", bad, text)
		}
	}
}

func TestConnectMeStreamingSessionAndAlias(t *testing.T) {
	s, _ := newConnectTestServer(t)
	_, _, ok := s.claims.TryClaim("sid-connect-me-stream")
	if !ok {
		t.Fatal("claim")
	}
	t.Cleanup(func() { s.claims.Release("sid-connect-me-stream", nil) })

	code, body, text := doConnectJSON(t, s, http.MethodGet, "/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if body["session_id"] != "sid-connect-me-stream" {
		t.Fatalf("session_id=%v", body["session_id"])
	}
	if body["turn_active"] != true {
		t.Fatalf("turn_active=%v", body["turn_active"])
	}
	code, alias, text := doConnectJSON(t, s, http.MethodGet, "/api/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("alias %d %s", code, text)
	}
	if alias["session_id"] != "sid-connect-me-stream" {
		t.Fatalf("alias session_id=%v", alias["session_id"])
	}
}

func TestConnectMePrefersFocusedWhenStreaming(t *testing.T) {
	s, _ := newConnectTestServer(t)
	if _, _, ok := s.claims.TryClaim("sid-a"); !ok {
		t.Fatal("claim a")
	}
	if _, _, ok := s.claims.TryClaim("sid-b"); !ok {
		t.Fatal("claim b")
	}
	t.Cleanup(func() {
		s.claims.Release("sid-a", nil)
		s.claims.Release("sid-b", nil)
	})
	s.SetFocusedSession("sid-b")
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if body["session_id"] != "sid-b" {
		t.Fatalf("session_id=%v", body["session_id"])
	}
}

func TestConnectMeFallsBackToFocusedWhenIdle(t *testing.T) {
	s, _ := newConnectTestServer(t)
	s.SetFocusedSession("sid-focused-idle")
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if body["session_id"] != "sid-focused-idle" {
		t.Fatalf("session_id=%v", body["session_id"])
	}
	if body["turn_active"] != false {
		t.Fatalf("turn_active=%v", body["turn_active"])
	}
}

func TestConnectStopAbortsConnectMeSessionNotListRow(t *testing.T) {
	s, _ := newConnectTestServer(t)
	s.SetFocusedSession("")

	code, idle, text := doConnectJSON(t, s, http.MethodPost, "/api/stop", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("idle status %d %s", code, text)
	}
	if idle["status"] != "idle" {
		t.Fatalf("idle=%v", idle)
	}
	if sid, ok := idle["session_id"]; ok && sid != nil && sid != "" {
		t.Fatalf("idle session_id=%v", sid)
	}
	if idle["notified"] != float64(0) {
		t.Fatalf("idle notified=%v", idle["notified"])
	}

	_, _, ok := s.claims.TryClaim("sid-connect-stop")
	if !ok {
		t.Fatal("claim")
	}
	t.Cleanup(func() { s.claims.Release("sid-connect-stop", nil) })

	code, live, text := doConnectJSON(t, s, http.MethodPost, "/api/stop", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("live status %d %s", code, text)
	}
	if live["session_id"] != "sid-connect-stop" {
		t.Fatalf("live session_id=%v", live["session_id"])
	}
	if live["status"] != "aborted" {
		t.Fatalf("live status=%v", live["status"])
	}
	if live["reason"] != "stop" {
		t.Fatalf("live reason=%v", live["reason"])
	}
	if live["notified"] != float64(1) {
		t.Fatalf("live notified=%v", live["notified"])
	}
}

func TestConnectMePausedReachable(t *testing.T) {
	s, home := newConnectTestServer(t)
	if err := connect.SetPaused(true, home); err != nil {
		t.Fatal(err)
	}
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/connect/me", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if body["paused"] != true {
		t.Fatalf("paused=%v", body["paused"])
	}
	if body["reachable"] != "paused" {
		t.Fatalf("reachable=%v", body["reachable"])
	}
}

// connectMeRequest issues one /connect/me call with an explicit peer and Host
// so the rebinding guard can be exercised.
func connectMeRequest(t *testing.T, s *Server, path, remoteAddr, host string, auth bool) int {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.RemoteAddr = remoteAddr
	req.Host = host
	if auth {
		req.Header.Set("Authorization", "Bearer "+connectTestToken)
	}
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	return rr.Code
}

// /connect/me is registered outside /api/, so the Bearer middleware never sees
// it. Its payload names the focused session and the open panes, so it must be
// no easier to reach than token bootstrap.
func TestConnectMeUnauthenticatedAliasIsLoopbackAndHostGuarded(t *testing.T) {
	s, _ := newConnectTestServer(t)

	if code := connectMeRequest(t, s, "/connect/me", "127.0.0.1:5555", "127.0.0.1:7400", false); code != http.StatusOK {
		t.Fatalf("loopback peer + loopback Host must pass: %d", code)
	}
	if code := connectMeRequest(t, s, "/connect/me", "10.9.8.7:5555", "127.0.0.1:7400", false); code != http.StatusForbidden {
		t.Fatalf("off-box peer must be refused: %d", code)
	}
	// DNS rebinding: the socket is loopback but the browser addressed a name
	// the attacker controls.
	if code := connectMeRequest(t, s, "/connect/me", "127.0.0.1:5555", "rebind.example.com", false); code != http.StatusForbidden {
		t.Fatalf("rebound Host must be refused: %d", code)
	}
	if code := connectMeRequest(t, s, "/api/connect/me", "127.0.0.1:5555", "rebind.example.com", false); code != http.StatusUnauthorized {
		t.Fatalf("/api alias without a token must be 401: %d", code)
	}
}

// The token holder is the owner; remote-but-authenticated callers (Connect
// proxy, desktop over the inner hop) keep working.
func TestConnectMeAuthenticatedCallerBypassesLoopbackGuard(t *testing.T) {
	s, _ := newConnectTestServer(t)
	if code := connectMeRequest(t, s, "/api/connect/me", "10.9.8.7:5555", "remedy.local", true); code != http.StatusOK {
		t.Fatalf("authenticated /api/connect/me: %d", code)
	}
	if code := connectMeRequest(t, s, "/connect/me", "10.9.8.7:5555", "remedy.local", true); code != http.StatusOK {
		t.Fatalf("authenticated /connect/me: %d", code)
	}
}
