package connect

import (
	"strings"
	"testing"
	"time"
)

// A phone that finished pairing a minute ago should not already have a shell.
// rails maps to /api/terminal, /api/files and /api/browser, so it is the one
// pane whose default has to be off; the owner turns it on deliberately.
func TestRailsAreOffForANewlyPairedDevice(t *testing.T) {
	if DefaultPanes()["rails"] {
		t.Fatal("rails must default off for a newly paired device")
	}
	// The capability is unchanged, only the default.
	for _, path := range []string{"/api/terminal", "/api/files", "/api/browser"} {
		if reason := ConnectForbidden("POST", path, "", nil); reason != "pane:rails" {
			t.Fatalf("%s with default panes: reason=%q want pane:rails", path, reason)
		}
		on := DefaultPanes()
		on["rails"] = true
		if reason := ConnectForbidden("POST", path, "", on); reason != "" {
			t.Fatalf("%s with rails on: reason=%q want allowed", path, reason)
		}
	}
	// Panes the owner already chose are untouched by the new default.
	explicit := NormalizePanes(map[string]any{"rails": true})
	if !explicit["rails"] {
		t.Fatal("an explicit rails=true must survive normalization")
	}
	// Everything else a phone needs day to day still works out of the box.
	for _, pane := range []string{"chat", "sessions", "approvals", "live_ui"} {
		if !DefaultPanes()[pane] {
			t.Fatalf("pane %q should still be on by default", pane)
		}
	}
}

// emitAll collects the response pieces one inner request produced.
func emitAll(pieces *[]byte) func([]byte) error {
	return func(piece []byte) error {
		*pieces = append(*pieces, piece...)
		return nil
	}
}

// An authenticated socket that has been silent past the idle timeout is a
// phone somebody may have picked up since. Keepalive PINGs do not refresh it:
// rails need a live handshake, not a live socket.
func TestRailsRequireAFreshHandshakeAfterTheIdleTimeout(t *testing.T) {
	home := t.TempDir()
	device := Device{ID: "abcdef0123456789", Name: "phone"}
	panes := DefaultPanes()
	panes["rails"] = true

	newState := func(last time.Time) *InnerMuxState {
		return &InnerMuxState{
			fragments:   make(map[uint32]*fragmentBuf),
			Panes:       panes,
			SidecarPort: 1, // never dialled on the refusal path
			idleTimeout: IdleTimeout,
			lastRequest: last,
		}
	}

	var stale []byte
	state := newState(time.Now().Add(-2 * IdleTimeout))
	if err := IterRequestHTTP(HTTPRequest{Method: "POST", Target: "/api/terminal"},
		device, state, home, emitAll(&stale)); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(stale), "rails:stale-session") {
		t.Fatalf("idle session kept rails open: %s", stale)
	}

	// The refusal names the pane family, so the phone can say what to do, and
	// the session is not torn down for it.
	if !strings.Contains(string(stale), "403") {
		t.Fatalf("stale rails must be a 403: %s", stale)
	}

	// A session that has been in use keeps its rails.
	var fresh []byte
	state = newState(time.Now())
	if err := IterRequestHTTP(HTTPRequest{Method: "POST", Target: "/api/terminal"},
		device, state, home, emitAll(&fresh)); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(fresh), "rails:stale-session") {
		t.Fatalf("an active session must keep rails: %s", fresh)
	}

	// Non-rails panes are unaffected by the idle gap: the phone can still read
	// its own chat after a long quiet spell.
	var chat []byte
	state = newState(time.Now().Add(-2 * IdleTimeout))
	if err := IterRequestHTTP(HTTPRequest{Method: "GET", Target: "/api/connect/me"},
		device, state, home, emitAll(&chat)); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(chat), "rails:stale-session") {
		t.Fatalf("/api/connect/me must not be gated by rails freshness: %s", chat)
	}
}

// A brand new session starts fresh: the handshake itself is the activity.
func TestNewSessionStartsWithLiveRails(t *testing.T) {
	state := newInnerMuxState(SessionConfig{Panes: map[string]bool{"rails": true}})
	if state.railsStale(time.Now()) {
		t.Fatal("a session that just handshaked must not be stale")
	}
	if !state.railsStale(time.Now().Add(2 * IdleTimeout)) {
		t.Fatal("a session idle past the timeout must be stale")
	}
}
