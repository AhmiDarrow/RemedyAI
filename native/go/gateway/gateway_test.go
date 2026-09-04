package gateway

import (
	"context"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func TestGatewayStartStopStats(t *testing.T) {
	g := New(Config{HeartbeatInterval: time.Hour, RateLimitPerMin: 120})
	if g.Running() {
		t.Fatal("expected idle")
	}
	st := g.Stats()
	if st.Running || st.Uptime != "0s" {
		t.Fatalf("idle stats = %+v", st)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := g.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if !g.Running() {
		t.Fatal("expected running")
	}
	m := g.StatsMap()
	if m["running"] != true {
		t.Fatalf("stats map = %#v", m)
	}
	if err := g.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	if g.Running() {
		t.Fatal("expected stopped")
	}
}

func TestGatewayEmitHandlerAndRateLimit(t *testing.T) {
	g := New(Config{RateLimitPerMin: 2, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ctx := context.Background()
	_ = g.Emit(ctx, Event{Kind: EventMessage, Channel: ChannelTelegram, SourceID: "1", Payload: map[string]any{"message": "a"}})
	_ = g.Emit(ctx, Event{Kind: EventMessage, Channel: ChannelTelegram, SourceID: "1", Payload: map[string]any{"message": "b"}})
	_ = g.Emit(ctx, Event{Kind: EventMessage, Channel: ChannelTelegram, SourceID: "1", Payload: map[string]any{"message": "c"}})
	if n.Load() != 2 {
		t.Fatalf("handler calls = %d, want 2 (third rate-limited)", n.Load())
	}
}

type stubChannel struct {
	kind    ChannelKind
	running bool
	sent    []string
}

func (s *stubChannel) Kind() ChannelKind { return s.kind }
func (s *stubChannel) Running() bool     { return s.running }
func (s *stubChannel) Start(ctx context.Context) error {
	s.running = true
	return nil
}
func (s *stubChannel) Stop(ctx context.Context) error {
	s.running = false
	return nil
}
func (s *stubChannel) Send(ctx context.Context, message, target string) (bool, error) {
	s.sent = append(s.sent, target+":"+message)
	return true, nil
}

func TestMirrorOutbound(t *testing.T) {
	g := New(Config{})
	ch := &stubChannel{kind: ChannelTelegram}
	g.RegisterChannel(ch)
	ok := MirrorOutbound(context.Background(), g, "telegram", "123", "hello")
	if !ok || len(ch.sent) != 1 || ch.sent[0] != "123:hello" {
		t.Fatalf("mirror = %v sent=%v", ok, ch.sent)
	}
}

func TestPollLockExclusive(t *testing.T) {
	home := t.TempDir()
	a := NewPollLock(home, "telegram")
	b := NewPollLock(home, "telegram")
	if !a.TryAcquire() {
		t.Fatal("a should acquire")
	}
	defer a.Release()
	if b.TryAcquire() {
		t.Fatal("b must not dual-acquire")
	}
	a.Heartbeat()
	a.Release()
	if !b.TryAcquire() {
		t.Fatal("b should acquire after release")
	}
	b.Release()
}

func TestOffsetRoundTrip(t *testing.T) {
	home := t.TempDir()
	if LoadUpdateOffset(home, "telegram") != 0 {
		t.Fatal("want 0")
	}
	SaveUpdateOffset(home, "telegram", 42)
	if LoadUpdateOffset(home, "telegram") != 42 {
		t.Fatal("want 42")
	}
	if _, err := filepath.Rel(home, offsetPath(home, "telegram")); err != nil {
		t.Fatal(err)
	}
}

func TestRegisterFromConfigTelegram(t *testing.T) {
	home := t.TempDir()
	g := New(Config{HomeDir: home})
	cfg := map[string]any{
		"enabled_channels": []string{"telegram", "signal"},
		"telegram": map[string]any{
			"allow_chat_ids": []string{"1"},
			"allow_all":      false,
		},
	}
	got := RegisterFromConfig(g, cfg, home, func(channel, field string) string {
		if channel == "telegram" && field == "bot_token" {
			return "123456:ABCDEF"
		}
		return ""
	})
	if len(got) != 2 {
		t.Fatalf("registered = %v", got)
	}
	if g.GetChannel(ChannelTelegram) == nil || g.GetChannel(ChannelSignal) == nil {
		t.Fatal("missing channels")
	}
}

func TestSplitAndSessionID(t *testing.T) {
	parts := SplitMessage(string(make([]byte, 5000)), ChannelTelegram)
	if len(parts) < 2 {
		t.Fatalf("expected split, got %d", len(parts))
	}
	id := ExternalSessionID("telegram", "42", "")
	if id != "msg:telegram:42" {
		t.Fatalf("id = %q", id)
	}
	title := HeuristicSessionTitle(ChannelTelegram, "bob", "", "hi")
	if title != "Telegram · @bob" {
		t.Fatalf("title = %q", title)
	}
}

func TestRedactSecrets(t *testing.T) {
	in := "https://api.telegram.org/bot123456:ABCDEFGHIJKLMNOPQRST/getUpdates"
	out := RedactSecrets(in)
	if out == in || !contains(out, "[redacted]") {
		t.Fatalf("redact failed: %q", out)
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		(func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		})())
}

func TestAllowlist(t *testing.T) {
	set := ParseIDs("1, 2;3")
	if !IsAllowed(set, false, "2") {
		t.Fatal("want allow")
	}
	if IsAllowed(set, false, "9") {
		t.Fatal("want deny")
	}
	if !IsAllowed(nil, true, "9") {
		t.Fatal("allow_all")
	}
	if IsAllowed(nil, false, "9") {
		t.Fatal("empty deny")
	}
}

func TestTelegram409Backoff(t *testing.T) {
	now := time.Now()
	wait, insist := telegram409Backoff(now, now.Add(-10*time.Second))
	if !insist || wait < 2*time.Second {
		t.Fatalf("takeover backoff = %v insist=%v", wait, insist)
	}
	wait, insist = telegram409Backoff(now, now.Add(-2*time.Minute))
	if insist || wait != 25*time.Second {
		t.Fatalf("steady backoff = %v insist=%v", wait, insist)
	}
}
