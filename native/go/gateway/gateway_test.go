package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
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
	bin := filepath.Join(home, "signal-cli")
	if err := os.WriteFile(bin, []byte(""), 0o755); err != nil {
		t.Fatal(err)
	}
	g := New(Config{HomeDir: home})
	cfg := map[string]any{
		"enabled_channels": []string{"telegram", "signal"},
		"telegram": map[string]any{
			"allow_chat_ids": []string{"1"},
			"allow_all":      false,
		},
		"signal": map[string]any{
			"cli_path": bin,
			"account":  "+15550100",
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

func TestStringCursorRoundTrip(t *testing.T) {
	home := t.TempDir()
	if LoadStringCursor(home, "matrix_since") != "" {
		t.Fatal("want empty")
	}
	SaveStringCursor(home, "matrix_since", "s123_abc")
	if got := LoadStringCursor(home, "matrix_since"); got != "s123_abc" {
		t.Fatalf("got %q", got)
	}
}

func TestRegisterFromConfigSlackMatrixMattermost(t *testing.T) {
	home := t.TempDir()
	g := New(Config{HomeDir: home})
	cfg := map[string]any{
		"enabled_channels": []string{"slack", "matrix", "mattermost"},
		"slack":            map[string]any{"channel_id": "C1", "allow_all": true},
		"matrix":           map[string]any{"homeserver": "https://matrix.example", "room_id": "!r:ex", "allow_all": true},
		"mattermost":       map[string]any{"base_url": "https://mm.example", "channel_id": "ch1", "allow_all": true},
	}
	got := RegisterFromConfig(g, cfg, home, func(channel, field string) string {
		switch channel + ":" + field {
		case "slack:bot_token":
			return "xoxb-test"
		case "slack:app_token":
			return "xapp-test"
		case "matrix:access_token":
			return "mat-token"
		case "mattermost:bot_token":
			return "mm-token"
		default:
			return ""
		}
	})
	if len(got) != 3 {
		t.Fatalf("registered = %v", got)
	}
	if _, ok := g.GetChannel(ChannelSlack).(*SlackChannel); !ok {
		t.Fatalf("slack type = %T", g.GetChannel(ChannelSlack))
	}
	if _, ok := g.GetChannel(ChannelMatrix).(*MatrixChannel); !ok {
		t.Fatalf("matrix type = %T", g.GetChannel(ChannelMatrix))
	}
	if _, ok := g.GetChannel(ChannelMattermost).(*MattermostChannel); !ok {
		t.Fatalf("mattermost type = %T", g.GetChannel(ChannelMattermost))
	}
}

func TestSlackHandleEventFilters(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewSlack(g, SlackConfig{BotToken: "xoxb", AllowAll: true})
	ctx := context.Background()
	ch.handleEvent(ctx, map[string]any{"type": "message", "subtype": "bot_message", "text": "x", "channel": "C1", "user": "U1"})
	ch.handleEvent(ctx, map[string]any{"type": "message", "bot_id": "B1", "text": "x", "channel": "C1", "user": "U1"})
	ch.handleEvent(ctx, map[string]any{"type": "message", "text": "hello", "channel": "C1", "user": "U1", "ts": "1.0"})
	ch.handleEvent(ctx, map[string]any{"type": "message", "text": "hello", "channel": "C1", "user": "U1", "ts": "1.0"}) // dedupe
	if n.Load() != 1 {
		t.Fatalf("handler calls = %d, want 1", n.Load())
	}
}

func TestMatrixHandleTimelineFilters(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewMatrix(g, MatrixConfig{
		AccessToken: "t",
		Homeserver:  "https://matrix.example",
		UserID:      "@bot:ex",
		AllowAll:    true,
	})
	ch.client = &http.Client{Timeout: time.Millisecond}
	ctx := context.Background()
	ch.handleTimelineEvent(ctx, "!r:ex", map[string]any{
		"type": "m.room.message", "sender": "@bot:ex",
		"content": map[string]any{"msgtype": "m.text", "body": "self"},
	})
	ch.handleTimelineEvent(ctx, "!r:ex", map[string]any{
		"type": "m.room.message", "sender": "@alice:ex",
		"content": map[string]any{"msgtype": "m.image", "body": "pic"},
	})
	ch.handleTimelineEvent(ctx, "!r:ex", map[string]any{
		"type": "m.room.message", "sender": "@alice:ex",
		"content": map[string]any{"msgtype": "m.text", "body": "hi"},
	})
	if n.Load() != 1 {
		t.Fatalf("handler calls = %d, want 1", n.Load())
	}
}

func TestMattermostOnEventFilters(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewMattermost(g, MattermostConfig{
		BotToken: "t",
		BaseURL:  "https://mm.example",
		AllowAll: true,
	})
	ctx := context.Background()
	ch.onEvent(ctx, map[string]any{"event": "typing"})
	botPost, _ := json.Marshal(map[string]any{
		"message": "bot", "channel_id": "c1", "user_id": "u1",
		"props": map[string]any{"from_bot": true},
	})
	ch.onEvent(ctx, map[string]any{"event": "posted", "data": map[string]any{"post": string(botPost)}})
	okPost, _ := json.Marshal(map[string]any{
		"message": "hello", "channel_id": "c1", "user_id": "u1",
	})
	ch.onEvent(ctx, map[string]any{"event": "posted", "data": map[string]any{"post": string(okPost)}})
	if n.Load() != 1 {
		t.Fatalf("handler calls = %d, want 1", n.Load())
	}
}

func TestWhatsAppWebhookVerifyAndPayload(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewWhatsApp(g, WhatsAppConfig{
		VerifyToken: "secret",
		AppSecret:   "app-secret",
		AllowFrom:   []string{"15551234567"},
	})
	if got, ok := ch.VerifyWebhookChallenge("subscribe", "secret", "42"); !ok || got != "42" {
		t.Fatalf("verify ok got=%q ok=%v", got, ok)
	}
	if _, ok := ch.VerifyWebhookChallenge("subscribe", "wrong", "42"); ok {
		t.Fatal("want verify fail")
	}
	if _, ok := ch.VerifyWebhookChallenge("subscribe", "secre", "42"); ok {
		t.Fatal("length mismatch must fail closed")
	}
	body := []byte(`{"x":1}`)
	if ch.VerifySignature(body, "sha256=nope") {
		t.Fatal("want bad signature")
	}
	handled := ch.HandleWebhookPayload(context.Background(), map[string]any{
		"entry": []any{
			map[string]any{
				"changes": []any{
					map[string]any{
						"value": map[string]any{
							"messages": []any{
								map[string]any{
									"from": "15551234567",
									"type": "text",
									"text": map[string]any{"body": "hello"},
								},
								map[string]any{
									"from": "999",
									"type": "text",
									"text": map[string]any{"body": "blocked"},
								},
							},
						},
					},
				},
			},
		},
	})
	if handled != 1 || n.Load() != 1 {
		t.Fatalf("handled=%d events=%d", handled, n.Load())
	}
}

func TestTeamsActivityAndJWTClaims(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewTeams(g, TeamsConfig{AppID: "app-id", AppPassword: "pw", AllowAll: true})
	amerClaims := map[string]any{"serviceurl": "https://smba.trafficmanager.net/amer/"}
	ok := ch.HandleActivity(context.Background(), map[string]any{
		"type":       "message",
		"text":       "hi teams",
		"serviceUrl": "https://smba.trafficmanager.net/amer/",
		"conversation": map[string]any{
			"id": "conv1",
		},
		"from": map[string]any{"id": "u1", "name": "User"},
	}, amerClaims)
	if !ok || n.Load() != 1 {
		t.Fatalf("ok=%v events=%d", ok, n.Load())
	}
	ch.mu.Lock()
	conv := ch.lastConversationID
	svc := ch.serviceURLs["conv1"]
	ch.mu.Unlock()
	if conv != "conv1" {
		t.Fatalf("lastConversationID=%q", conv)
	}
	if svc != "https://smba.trafficmanager.net/amer" {
		t.Fatalf("serviceURL for conv1=%q", svc)
	}
	if isAllowedBotFrameworkServiceURL("http://evil.example/") {
		t.Fatal("http serviceUrl must be rejected")
	}
	now := time.Now()
	if JWTClaimsStructurallyValid(map[string]any{
		"aud": "app-id",
		"exp": float64(now.Add(time.Hour).Unix()),
		"iss": "https://api.botframework.com",
	}, "app-id", now, false) != true {
		t.Fatal("want valid claims")
	}
	if JWTClaimsStructurallyValid(map[string]any{
		"exp": float64(now.Add(time.Hour).Unix()),
	}, "app-id", now, false) {
		t.Fatal("missing aud must fail")
	}
}

func TestTeamsPerConversationServiceURL(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	ch := NewTeams(g, TeamsConfig{AppID: "app-id", AppPassword: "pw", AllowAll: true})
	amer := "https://smba.trafficmanager.net/amer/"
	emea := "https://smba.trafficmanager.net/emea/"
	if !ch.HandleActivity(context.Background(), map[string]any{
		"type": "message", "text": "a", "serviceUrl": amer,
		"conversation": map[string]any{"id": "conv-amer"},
		"from":         map[string]any{"id": "u1"},
	}, map[string]any{"serviceurl": amer}) {
		t.Fatal("amer activity rejected")
	}
	if !ch.HandleActivity(context.Background(), map[string]any{
		"type": "message", "text": "b", "serviceUrl": emea,
		"conversation": map[string]any{"id": "conv-emea"},
		"from":         map[string]any{"id": "u2"},
	}, map[string]any{"serviceurl": emea}) {
		t.Fatal("emea activity rejected")
	}
	convAmer, svcAmer := ch.conversationRef("conv-amer")
	convEmea, svcEmea := ch.conversationRef("conv-emea")
	if convAmer != "conv-amer" || svcAmer != "https://smba.trafficmanager.net/amer" {
		t.Fatalf("amer ref=%q %q", convAmer, svcAmer)
	}
	if convEmea != "conv-emea" || svcEmea != "https://smba.trafficmanager.net/emea" {
		t.Fatalf("emea ref=%q %q", convEmea, svcEmea)
	}
	// Empty target falls back to last conversation (emea), not amer's host.
	_, lastSvc := ch.conversationRef("")
	if lastSvc != "https://smba.trafficmanager.net/emea" {
		t.Fatalf("last fallback service=%q", lastSvc)
	}
}

func TestGoogleChatEventFilters(t *testing.T) {
	g := New(Config{RateLimitPerMin: 100, HeartbeatInterval: time.Hour})
	var n atomic.Int32
	g.RegisterHandler(func(ctx context.Context, ev Event) error {
		if ev.Kind == EventMessage {
			n.Add(1)
		}
		return nil
	})
	ch := NewGoogleChat(g, GoogleChatConfig{AccessToken: "tok", AllowAll: true})
	// The app's own outbound access token is never inbound proof, and without a
	// project number there is no audience to verify against: reject both.
	if ch.VerifyInboundAuth("Bearer tok") {
		t.Fatal("outbound access token must not authenticate inbound webhooks")
	}
	if ch.VerifyInboundAuth("Bearer wrong") {
		t.Fatal("want auth fail")
	}
	ok := ch.HandleEvent(context.Background(), map[string]any{
		"type": "MESSAGE",
		"message": map[string]any{
			"text":   "hi",
			"sender": map[string]any{"name": "users/1", "displayName": "A", "type": "HUMAN"},
		},
		"space": map[string]any{"name": "spaces/s1"},
	})
	if !ok || n.Load() != 1 {
		t.Fatalf("ok=%v events=%d", ok, n.Load())
	}
	botOK := ch.HandleEvent(context.Background(), map[string]any{
		"type": "MESSAGE",
		"message": map[string]any{
			"text":   "bot",
			"sender": map[string]any{"name": "bots/1", "type": "BOT"},
		},
		"space": map[string]any{"name": "spaces/s1"},
	})
	if botOK || n.Load() != 1 {
		t.Fatalf("bot should be ignored ok=%v events=%d", botOK, n.Load())
	}
}

func TestRegisterFromConfigWebhookMessengers(t *testing.T) {
	home := t.TempDir()
	g := New(Config{HomeDir: home})
	cfg := map[string]any{
		"enabled_channels": []string{"whatsapp", "teams", "google_chat"},
		"whatsapp":         map[string]any{"phone_number_id": "pn", "allow_all": true},
		"teams":            map[string]any{"app_id": "aid", "allow_all": true},
		"google_chat":      map[string]any{"space_id": "spaces/s1", "allow_all": true},
	}
	got := RegisterFromConfig(g, cfg, home, func(channel, field string) string {
		switch channel + ":" + field {
		case "whatsapp:access_token":
			return "wa-tok"
		case "whatsapp:verify_token":
			return "vtok"
		case "whatsapp:app_secret":
			return "asec"
		case "teams:app_password":
			return "tpw"
		case "google_chat:access_token":
			return "gc-tok"
		default:
			return ""
		}
	})
	if len(got) != 3 {
		t.Fatalf("registered=%v", got)
	}
	if _, ok := g.GetChannel(ChannelWhatsApp).(*WhatsAppChannel); !ok {
		t.Fatalf("whatsapp type=%T", g.GetChannel(ChannelWhatsApp))
	}
	if _, ok := g.GetChannel(ChannelTeams).(*TeamsChannel); !ok {
		t.Fatalf("teams type=%T", g.GetChannel(ChannelTeams))
	}
	if _, ok := g.GetChannel(ChannelGoogleChat).(*GoogleChatChannel); !ok {
		t.Fatalf("google_chat type=%T", g.GetChannel(ChannelGoogleChat))
	}
}
