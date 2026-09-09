package gateway

import (
	"context"
	"testing"
	"time"
)

// A refused message is silent to its sender, so the owner must still be able
// to see that somebody tried and why it was ignored.
func TestDeniedInboundIsVisibleToOwner(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	ch := NewTelegram(g, TelegramConfig{BotToken: "t", AllowChatIDs: "555"})

	stranger := map[string]any{
		"text": "hello",
		"chat": map[string]any{"id": "999"},
		"from": map[string]any{"id": "42"},
	}
	if _, ok := ch.eventFromMessage(stranger, nil); ok {
		t.Fatal("a sender outside the allowlist must be refused")
	}

	denied := g.DeniedInbounds()
	if len(denied) != 1 {
		t.Fatalf("denied=%d want 1", len(denied))
	}
	if denied[0].UserID != "42" || denied[0].ScopeID != "999" {
		t.Fatalf("denied row=%+v", denied[0])
	}
	if denied[0].Reason == "" {
		t.Fatal("a refusal must carry a reason the owner can act on")
	}
	if denied[0].Count != 1 {
		t.Fatalf("count=%d want 1", denied[0].Count)
	}

	// A repeat from the same sender collapses into the existing row so one
	// chatty stranger cannot push the owner's own refusal out of the ring.
	if _, ok := ch.eventFromMessage(stranger, nil); ok {
		t.Fatal("still refused")
	}
	denied = g.DeniedInbounds()
	if len(denied) != 1 || denied[0].Count != 2 {
		t.Fatalf("after repeat: rows=%d count=%d", len(denied), denied[0].Count)
	}

	// The owner's own id is admitted and never recorded as a refusal.
	owner := map[string]any{
		"text": "hi",
		"chat": map[string]any{"id": "555"},
		"from": map[string]any{"id": "555"},
	}
	if _, ok := ch.eventFromMessage(owner, nil); !ok {
		t.Fatal("the allowlisted owner must be admitted")
	}
	if got := len(g.DeniedInbounds()); got != 1 {
		t.Fatalf("admitting the owner must not record a refusal: rows=%d", got)
	}

	stats := g.StatsMap()
	if _, ok := stats["denied_inbound"]; !ok {
		t.Fatal("status must expose denied_inbound so Settings can show it")
	}
	_ = context.Background()
}
