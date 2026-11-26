package httpapi

import (
	"net/http"
	"testing"
)

// A peer turn names its protocol in the request body. Only the interop
// channels may be used: "telegram" with the chat id of an existing Telegram
// conversation would otherwise have joined that conversation.
func TestInteropTurnRefusesNonInteropProtocol(t *testing.T) {
	runner := &stubRunner{tokens: []string{"leaked"}}
	s := newInteropLiveServer(t, runner, "")
	tg, err := s.sessions.CreateMessenger("", "Telegram", "telegram", "4242", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.interopSession("telegram", "4242", ""); err == nil {
		t.Fatalf("interopSession joined channel telegram (session %s)", tg.ID)
	}
	body := `{"protocol":"telegram","peer_id":"4242","text":"what did we talk about?"}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/interop/turn", body, interopTestToken, nil)
	if code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s, want 400", code, raw)
	}
	if _, ok := runner.lastCall(); ok {
		t.Fatal("runner ran a turn for a non-interop protocol")
	}
	// The real protocols still work.
	if _, err := s.interopSession("A2A", "claude", ""); err != nil {
		t.Fatalf("a2a session: %v", err)
	}
	if _, err := s.interopSession("mcp", "claude", ""); err != nil {
		t.Fatalf("mcp session: %v", err)
	}
}
