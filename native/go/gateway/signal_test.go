package gateway

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestResolveSignalCLIFileAndMissing(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	if err := os.WriteFile(bin, []byte(""), 0o755); err != nil {
		t.Fatal(err)
	}
	got := resolveSignalCLI(bin)
	if got == "" || !strings.Contains(got, "signal-cli") {
		t.Fatalf("resolved=%q", got)
	}
	if resolveSignalCLI(filepath.Join(dir, "missing-cli")) != "" {
		t.Fatal("missing path should not resolve")
	}
	if resolveSignalCLI(dir) != "" {
		t.Fatal("directory must not resolve as binary")
	}
}

func TestResolveSignalCLICachedOnce(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	_ = os.WriteFile(bin, []byte(""), 0o755)
	ch := NewSignal(nil, SignalConfig{CLIPath: bin, Account: "+1"})
	a := ch.resolveBin()
	b := ch.resolveBin()
	if a == "" || a != b {
		t.Fatalf("cache broken: %q vs %q", a, b)
	}
}

func TestSignalSendArgvAndGuards(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	_ = os.WriteFile(bin, []byte(""), 0o755)

	var mu sync.Mutex
	var recorded [][]string
	var timeouts []time.Duration
	runner := func(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
		_ = ctx
		mu.Lock()
		recorded = append(recorded, append([]string(nil), argv...))
		timeouts = append(timeouts, timeout)
		mu.Unlock()
		return 0, "", "", nil
	}

	ch := NewSignal(nil, SignalConfig{
		CLIPath: bin,
		Account: "+15550100",
		Runner:  runner,
	})
	ok, err := ch.Send(context.Background(), "hello there", " +15550111 ")
	if err != nil || !ok {
		t.Fatalf("send ok=%v err=%v", ok, err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(recorded) != 1 {
		t.Fatalf("recorded=%v", recorded)
	}
	want := []string{bin, "-a", "+15550100", "send", "-m", "hello there", "+15550111"}
	// resolveBin may absolutize
	if recorded[0][0] == "" || recorded[0][2] != "+15550100" || recorded[0][5] != "hello there" || recorded[0][6] != "+15550111" {
		t.Fatalf("argv=%v want prefix %#v", recorded[0], want)
	}
	if timeouts[0] != 90*time.Second {
		t.Fatalf("send timeout=%v", timeouts[0])
	}

	chNoBin := NewSignal(nil, SignalConfig{CLIPath: filepath.Join(dir, "nope"), Account: "+1", Runner: runner})
	if ok, _ := chNoBin.Send(context.Background(), "hi", "+2"); ok {
		t.Fatal("missing binary must fail send")
	}
	chNoAcct := NewSignal(nil, SignalConfig{CLIPath: bin, Account: "", Runner: runner})
	if ok, _ := chNoAcct.Send(context.Background(), "hi", "+2"); ok {
		t.Fatal("missing account must fail send")
	}
	for _, target := range []string{"", "   "} {
		if ok, _ := ch.Send(context.Background(), "hi", target); ok {
			t.Fatalf("empty target %q must fail", target)
		}
	}
}

func TestSignalSendNonzeroExit(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	_ = os.WriteFile(bin, []byte(""), 0o755)
	ch := NewSignal(nil, SignalConfig{
		CLIPath: bin,
		Account: "+1",
		Runner: func(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
			return 1, "", "Unregistered user", nil
		},
	})
	ok, err := ch.Send(context.Background(), "hi", "+2")
	if err != nil || ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
}

func TestSignalEnvelopeAllowlistAndParse(t *testing.T) {
	gw := New(Config{})
	var got []Event
	gw.RegisterHandler(func(ctx context.Context, ev Event) error {
		got = append(got, ev)
		return nil
	})

	ch := NewSignal(gw, SignalConfig{AllowFrom: []string{"+15550111"}, Runner: nopRunner})
	ch.onEnvelope(context.Background(), envelope("+15550111", "hello", "source"))
	if len(got) != 1 {
		t.Fatalf("listed source should emit, got %d", len(got))
	}
	ev := got[0]
	if ev.Channel != ChannelSignal || ev.SessionID != "+15550111" || ev.Payload["message"] != "hello" {
		t.Fatalf("event=%+v", ev)
	}
	if ev.Payload["chat_id"] != "+15550111" || ev.Payload["user_id"] != "+15550111" {
		t.Fatalf("ids=%v", ev.Payload)
	}

	got = nil
	ch2 := NewSignal(gw, SignalConfig{AllowFrom: []string{"+15550999"}, Runner: nopRunner})
	ch2.onEnvelope(context.Background(), envelope("+15550111", "hello", "source"))
	if len(got) != 0 {
		t.Fatal("unlisted refused")
	}

	got = nil
	ch3 := NewSignal(gw, SignalConfig{AllowAll: true, Runner: nopRunner})
	ch3.onEnvelope(context.Background(), envelope("+19998887777", "x", "source"))
	if len(got) != 1 {
		t.Fatal("allow_all")
	}

	got = nil
	ch4 := NewSignal(gw, SignalConfig{AllowFrom: []string{"+15550111"}, Runner: nopRunner})
	ch4.onEnvelope(context.Background(), envelope("+15550111", "hello", "sourceNumber"))
	if len(got) != 1 {
		t.Fatal("sourceNumber")
	}

	got = nil
	ch5 := NewSignal(gw, SignalConfig{AllowAll: true, Runner: nopRunner})
	ch5.onEnvelope(context.Background(), map[string]any{
		"source":      "+1",
		"dataMessage": map[string]any{"message": "flat"},
	})
	if len(got) != 1 || got[0].Payload["message"] != "flat" {
		t.Fatalf("flat envelope: %+v", got)
	}

	got = nil
	ch6 := NewSignal(gw, SignalConfig{AllowAll: true, Runner: nopRunner})
	for _, env := range []map[string]any{
		{"envelope": map[string]any{"source": "+1"}},
		{"envelope": map[string]any{"source": "+1", "dataMessage": map[string]any{}}},
		{"envelope": map[string]any{"source": "+1", "dataMessage": map[string]any{"message": ""}}},
		{"envelope": map[string]any{"source": "+1", "dataMessage": map[string]any{"message": "   "}}},
		{"envelope": map[string]any{"source": "+1", "receiptMessage": map[string]any{"isDelivery": true}}},
	} {
		ch6.onEnvelope(context.Background(), env)
	}
	if len(got) != 0 {
		t.Fatalf("empty/receipt should not emit: %d", len(got))
	}

	got = nil
	ch7 := NewSignal(gw, SignalConfig{AllowAll: true, Runner: nopRunner})
	ch7.onEnvelope(context.Background(), map[string]any{
		"envelope": map[string]any{"dataMessage": map[string]any{"message": "who am i"}},
	})
	if len(got) != 1 || got[0].Payload["chat_id"] != "signal" {
		t.Fatalf("anonymous session: %+v", got)
	}
}

func TestSignalReceiveLoopJSONLines(t *testing.T) {
	gw := New(Config{})
	var got []string
	gw.RegisterHandler(func(ctx context.Context, ev Event) error {
		got = append(got, ev.Payload["message"].(string))
		return nil
	})

	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	_ = os.WriteFile(bin, []byte(""), 0o755)

	lines := strings.Join([]string{
		"",
		"not json",
		mustJSON(envelope("+1", "survivor", "source")),
		"   ",
		"{broken",
		mustJSON(envelope("+1", "second", "source")),
	}, "\n")

	var calls int
	ctx, cancel := context.WithCancel(context.Background())
	ch := NewSignal(gw, SignalConfig{
		CLIPath:  bin,
		Account:  "+15550100",
		AllowAll: true,
		HomeDir:  dir,
		Runner: func(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
			calls++
			if calls == 1 {
				if timeout != 30*time.Second {
					t.Errorf("receive timeout=%v", timeout)
				}
				if len(argv) < 6 || argv[3] != "receive" || argv[6] != "--json" {
					t.Errorf("receive argv=%v", argv)
				}
				cancel() // stop loop after first poll
				return 1, lines, "warning: rate limited", nil
			}
			<-ctx.Done()
			return 0, "", "", ctx.Err()
		},
	})
	ch.mu.Lock()
	ch.running = true
	ch.mu.Unlock()
	ch.wg.Add(1)
	ch.receiveLoop(ctx)
	if got[0] != "survivor" || got[1] != "second" {
		t.Fatalf("messages=%v", got)
	}
}

func TestSignalStartWithoutAccountNoPoll(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "signal-cli")
	_ = os.WriteFile(bin, []byte(""), 0o755)
	var calls int
	ch := NewSignal(nil, SignalConfig{
		CLIPath: bin,
		Account: "",
		HomeDir: dir,
		Runner: func(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
			calls++
			return 0, "", "", nil
		},
	})
	if err := ch.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !ch.Running() {
		t.Fatal("should be running stub")
	}
	time.Sleep(50 * time.Millisecond)
	if calls != 0 {
		t.Fatalf("spawned %d times", calls)
	}
	_ = ch.Stop(context.Background())
}

func TestRegisterFromConfigSignalCLIPath(t *testing.T) {
	home := t.TempDir()
	g := New(Config{HomeDir: home})
	cfg := map[string]any{
		"enabled_channels": []string{"signal"},
		"signal": map[string]any{
			"cli_path":   "/opt/signal-cli",
			"account":    "+15550100",
			"allow_from": []string{"+1"},
			"allow_all":  true,
		},
	}
	got := RegisterFromConfig(g, cfg, home, nil)
	if len(got) != 1 || got[0] != "signal" {
		t.Fatalf("registered=%v", got)
	}
	ch, ok := g.GetChannel(ChannelSignal).(*SignalChannel)
	if !ok || ch == nil {
		t.Fatal("expected *SignalChannel")
	}
	if ch.cliPath != "/opt/signal-cli" || ch.account != "+15550100" || !ch.allowAll {
		t.Fatalf("cfg=%+v", ch)
	}
}

func TestSignalTypingNoOp(t *testing.T) {
	ch := NewSignal(nil, SignalConfig{})
	if err := ch.SendTyping(context.Background(), "+1"); err != nil {
		t.Fatal(err)
	}
}

func nopRunner(ctx context.Context, argv []string, timeout time.Duration) (int, string, string, error) {
	_ = ctx
	_ = argv
	_ = timeout
	return 0, "", "", nil
}

func envelope(source, text, key string) map[string]any {
	return map[string]any{
		"envelope": map[string]any{
			key:           source,
			"dataMessage": map[string]any{"message": text},
		},
	}
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}
