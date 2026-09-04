package connect_test

import (
	"bytes"
	"context"
	"errors"
	"net"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestGatewayMaybeStartStopLoopback(t *testing.T) {
	var got atomic.Int32
	g := connect.NewGateway(func(ctx context.Context, conn net.Conn) {
		got.Add(1)
		<-ctx.Done()
		_ = conn.Close()
	})
	defer func() { _ = g.Stop() }()

	err := g.MaybeStart(connect.GatewaySettings{
		Enabled: true,
		Host:    "127.0.0.1",
		Port:    0,
	})
	if err != nil {
		t.Fatal(err)
	}
	host, port, ok := g.ListeningAddr()
	if !ok || host != "127.0.0.1" || port == 0 {
		t.Fatalf("addr=%s:%d ok=%v", host, port, ok)
	}
	h := g.Health()
	if !h.Serving || !h.Listening {
		t.Fatalf("health=%+v", h)
	}

	// Same bind is a no-op.
	if err := g.MaybeStart(connect.GatewaySettings{Enabled: true, Host: "127.0.0.1", Port: port}); err != nil {
		t.Fatal(err)
	}

	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()

	deadline := time.Now().Add(2 * time.Second)
	for got.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if got.Load() == 0 {
		t.Fatal("handler never ran")
	}

	if err := g.Stop(); err != nil {
		t.Fatal(err)
	}
	if _, _, ok := g.ListeningAddr(); ok {
		t.Fatal("still listening after stop")
	}
}

func TestGatewayDisabledIsNoop(t *testing.T) {
	g := connect.NewGateway(nil)
	if err := g.MaybeStart(connect.GatewaySettings{Enabled: false, Host: "127.0.0.1"}); err != nil {
		t.Fatal(err)
	}
	if _, _, ok := g.ListeningAddr(); ok {
		t.Fatal("must not listen when disabled")
	}
}

func TestGatewayWildcardRefused(t *testing.T) {
	g := connect.NewGateway(nil)
	if err := g.MaybeStart(connect.GatewaySettings{Enabled: true, Host: "0.0.0.0"}); err != nil {
		t.Fatal(err)
	}
	if _, _, ok := g.ListeningAddr(); ok {
		t.Fatal("wildcard must not listen")
	}
}

func TestGatewayApplySettingsPauseAndDisable(t *testing.T) {
	home := t.TempDir()
	g := connect.NewGateway(func(ctx context.Context, conn net.Conn) {
		<-ctx.Done()
		_ = conn.Close()
	})
	defer func() { _ = g.Stop() }()

	if err := g.MaybeStart(connect.GatewaySettings{Enabled: true, Host: "127.0.0.1", Port: 0, Home: home}); err != nil {
		t.Fatal(err)
	}
	host, port, ok := g.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	time.Sleep(50 * time.Millisecond)

	if err := g.ApplySettings(connect.GatewaySettings{
		Enabled: true,
		Paused:  true,
		Host:    "127.0.0.1",
		Port:    port,
		Home:    home,
	}); err != nil {
		t.Fatal(err)
	}
	if !connect.IsPaused(home) {
		t.Fatal("expected paused")
	}

	if err := g.ApplySettings(connect.GatewaySettings{
		Enabled: false,
		Host:    "127.0.0.1",
		Home:    home,
	}); err != nil {
		t.Fatal(err)
	}
	if _, _, ok := g.ListeningAddr(); ok {
		t.Fatal("still listening after disable")
	}
}

func TestGatewayNoteCrashSchedulesHeal(t *testing.T) {
	g := connect.NewGateway(nil)
	healed := make(chan struct{}, 1)
	scheduled := g.NoteCrash(errors.New("boom"), func() {
		select {
		case healed <- struct{}{}:
		default:
		}
	})
	if !scheduled {
		t.Fatal("heal not scheduled")
	}
	h := g.Health()
	if h.Crashes != 1 || h.LastCrash == "" {
		t.Fatalf("health=%+v", h)
	}
	select {
	case <-healed:
	case <-time.After(3 * time.Second):
		t.Fatal("heal did not run")
	}
}

func TestSettingsFromMap(t *testing.T) {
	cfg := connect.SettingsFromMap(map[string]any{
		"connect_enabled":     true,
		"connect_bind_host":   "10.0.0.8",
		"connect_bind_port":   7401.0,
		"connect_relay_url":   "10.0.0.8:7402",
		"connect_rdv_enabled": false,
		"connect_paused":      "yes",
		"connect_home":        "/tmp/remedy-home",
		"api_key":             "tok-from-map",
		"sidecar_port":        7411.0,
	})
	if !cfg.Enabled || !cfg.Paused || cfg.Host != "10.0.0.8" || cfg.Port != 7401 {
		t.Fatalf("%+v", cfg)
	}
	if cfg.RDV || cfg.RelayURL != "10.0.0.8:7402" {
		t.Fatalf("%+v", cfg)
	}
	if cfg.Home != "/tmp/remedy-home" || cfg.APIKey != "tok-from-map" || cfg.Sidecar != 7411 {
		t.Fatalf("%+v", cfg)
	}
}

func TestGatewayNilHandlerServesNoiseConnectMe(t *testing.T) {
	// Production path: NewGateway(nil) → Noise IK → allowlist → /connect/me.
	home := pipeHome(t)
	hostKP := mustHostKP(t, home)

	g := connect.NewGateway(nil)
	defer func() { _ = g.Stop() }()

	if err := g.MaybeStart(connect.GatewaySettings{
		Enabled: true,
		Host:    "127.0.0.1",
		Port:    0,
		Home:    home,
		APIKey:  "tok-gateway-noise-test",
		Sidecar: 7400,
	}); err != nil {
		t.Fatal(err)
	}
	host, port, ok := g.ListeningAddr()
	if !ok {
		t.Fatal("not listening")
	}
	_, secret := mustStartPair(t, home, port)

	deviceKP, err := connect.GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	_ = c.SetDeadline(time.Now().Add(8 * time.Second))

	client, err := connect.HandshakeInitiator(c, deviceKP, hostKP.Public, connect.PairPayload(secret, "phone"))
	if err != nil {
		t.Fatal(err)
	}
	payload, err := connect.EncodeHTTPRequest(connect.HTTPRequest{
		Method: "GET",
		Target: "/connect/me",
	})
	if err != nil {
		t.Fatal(err)
	}
	frame, err := connect.EncodeInner(connect.TypeHTTPReq, 7, payload, true)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := client.Encrypt(frame)
	if err != nil {
		t.Fatal(err)
	}
	if err := connect.WriteRecord(c, blob); err != nil {
		t.Fatal(err)
	}

	var joined []byte
	for {
		nonce, ct, err := connect.ReadRecord(c)
		if err != nil {
			t.Fatal(err)
		}
		packed, _ := connect.PackRecord(nonce, ct)
		out, err := client.Decrypt(packed)
		if err != nil {
			t.Fatal(err)
		}
		inner, err := connect.DecodeInner(out)
		if err != nil {
			t.Fatal(err)
		}
		if inner.Type != connect.TypeHTTPRes || inner.ID != 7 {
			t.Fatalf("unexpected %+v", inner)
		}
		joined = append(joined, inner.Payload...)
		if inner.Fin() {
			break
		}
	}
	if !bytes.Contains(joined, []byte("HTTP/1.1 200")) {
		t.Fatalf("joined=%q", joined)
	}
	for _, want := range []string{`"device_id"`, `"panes"`, `"reachable"`} {
		if !bytes.Contains(joined, []byte(want)) {
			t.Fatalf("missing %s in %q", want, joined)
		}
	}
	if bytes.Contains(joined, []byte("tok-gateway-noise-test")) || bytes.Contains(joined, []byte("Bearer")) {
		t.Fatalf("api key leaked in /connect/me: %q", joined)
	}
}
