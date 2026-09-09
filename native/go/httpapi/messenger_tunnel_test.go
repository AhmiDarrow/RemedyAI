package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

func runtimeGOOSWindows() bool { return runtime.GOOS == "windows" }

func TestMessengerTunnelStatusLoopbackOnly(t *testing.T) {
	s, _ := newConnectTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/messengers/tunnel", nil)
	req.Header.Set("Authorization", "Bearer "+connectTestToken)
	req.RemoteAddr = "10.9.8.7:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("non-loopback status %d body=%s", rr.Code, rr.Body.String())
	}

	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/messengers/tunnel", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	for _, k := range []string{"running", "binary_ready", "env_configured", "download_url"} {
		if _, ok := body[k]; !ok {
			t.Fatalf("missing %s in %s", k, text)
		}
	}
}

func TestMessengerTunnelStartStopWithInjectedStarter(t *testing.T) {
	t.Setenv("REMEDY_PUBLIC_BASE_URL", "")
	t.Setenv("REMEDY_WEBHOOK_PUBLIC_URL", "")
	t.Setenv("REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD", "1")

	s, home := newConnectTestServer(t)
	s.apiListenPort = 7400

	// Place a fake managed binary so Ensure short-circuits before download skip.
	binDir := filepath.Join(gateway.ManagedTunnelDir(home), "bin")
	if err := os.MkdirAll(binDir, 0o700); err != nil {
		t.Fatal(err)
	}
	binName := "cloudflared"
	if runtimeGOOSWindows() {
		binName = "cloudflared.exe"
	}
	binPath := filepath.Join(binDir, binName)
	if err := os.WriteFile(binPath, []byte("fake"), 0o755); err != nil {
		t.Fatal(err)
	}

	prevPoll := pollQuickTunnelHostname
	t.Cleanup(func() { pollQuickTunnelHostname = prevPoll })
	pollQuickTunnelHostname = func(metricsURL string, timeout time.Duration) (string, error) {
		return "https://demo.trycloudflare.com", nil
	}

	var startedArgv []string
	s.tunnelStarter = func(_ context.Context, argv []string, _ map[string]string, _ string) (*tunnelStartedProcess, error) {
		startedArgv = append([]string{}, argv...)
		return &tunnelStartedProcess{PID: 4242, Cleanup: func() error { return nil }}, nil
	}

	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/messengers/tunnel/start", map[string]any{
		"mode": "quick",
	}, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("start %d %s", code, text)
	}
	if body["running"] != true {
		t.Fatalf("running=%v body=%s", body["running"], text)
	}
	if pub, _ := body["public_url"].(string); pub != "https://demo.trycloudflare.com" {
		t.Fatalf("public_url=%v", body["public_url"])
	}
	if os.Getenv("REMEDY_PUBLIC_BASE_URL") != "https://demo.trycloudflare.com" {
		t.Fatalf("env not set: %q", os.Getenv("REMEDY_PUBLIC_BASE_URL"))
	}
	if len(startedArgv) < 2 || !strings.Contains(startedArgv[0], "cloudflared") {
		t.Fatalf("argv=%v", startedArgv)
	}
	joined := strings.Join(startedArgv, " ")
	// The tunnel points at the webhook-only origin, never at the API port.
	if !strings.Contains(joined, "--url") || !strings.Contains(joined, "http://127.0.0.1:") {
		t.Fatalf("expected quick tunnel url argv, got %v", startedArgv)
	}
	if strings.Contains(joined, "127.0.0.1:7400") {
		t.Fatalf("tunnel must not be pointed at the API port: %v", startedArgv)
	}
	if origin, _ := body["origin_url"].(string); origin == "" || !strings.Contains(joined, origin) {
		t.Fatalf("status origin_url=%q argv=%v", body["origin_url"], startedArgv)
	}

	// Health should treat webhook messengers as tunnel-ready.
	cfg := ConfigMap{
		"enabled_channels": []string{"cli", "whatsapp"},
		"whatsapp":         map[string]any{"phone_number_id": "pn1"},
	}
	keys := map[string]bool{"ch:whatsapp:access_token": true}
	got := publicMessengers(cfg, keys, home)
	var wa map[string]any
	for _, row := range got {
		if row["id"] == "whatsapp" {
			wa = row
			break
		}
	}
	if wa["status"] != "ready" {
		t.Fatalf("whatsapp status=%v reason=%v", wa["status"], wa["status_reason"])
	}

	code, body, text = doConnectJSON(t, s, http.MethodPost, "/api/messengers/tunnel/stop", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("stop %d %s", code, text)
	}
	if body["running"] != false {
		t.Fatalf("still running: %s", text)
	}
	if os.Getenv("REMEDY_PUBLIC_BASE_URL") != "" {
		t.Fatalf("env should clear, got %q", os.Getenv("REMEDY_PUBLIC_BASE_URL"))
	}
}

func TestMessengerTunnelNamedRequiresTokenAndURL(t *testing.T) {
	s, _ := newConnectTestServer(t)
	s.tunnelStarter = func(context.Context, []string, map[string]string, string) (*tunnelStartedProcess, error) {
		t.Fatal("should not spawn")
		return nil, nil
	}
	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/messengers/tunnel/start", map[string]any{
		"mode": "named",
	}, connectAuthHeader())
	if code != http.StatusInternalServerError {
		t.Fatalf("status %d %s", code, text)
	}
	detail, _ := body["detail"].(string)
	if !strings.Contains(detail, "token") {
		t.Fatalf("detail=%q", detail)
	}
}

func TestMessengerSignalEnsureSkip(t *testing.T) {
	t.Setenv("REMEDY_SKIP_MANAGED_SIGNAL_DOWNLOAD", "1")
	s, _ := newConnectTestServer(t)
	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/messengers/signal/ensure", nil, connectAuthHeader())
	if code != http.StatusInternalServerError {
		t.Fatalf("status %d %s", code, text)
	}
	if _, ok := body["download_url"]; !ok {
		raw, _ := json.Marshal(body)
		t.Fatalf("missing download_url: %s", raw)
	}
}

func TestPublicMessengersHealthWhenPublicURLPresent(t *testing.T) {
	t.Setenv("REMEDY_PUBLIC_BASE_URL", "https://hooks.example.com")
	t.Setenv("REMEDY_WEBHOOK_PUBLIC_URL", "")
	cfg := ConfigMap{
		"enabled_channels": []string{"cli", "whatsapp", "teams", "google_chat"},
		"whatsapp":         map[string]any{"phone_number_id": "pn"},
		"teams":            map[string]any{"app_id": "aid"},
		"google_chat":      map[string]any{},
	}
	keys := map[string]bool{
		"ch:whatsapp:access_token":           true,
		"ch:teams:app_password":              true,
		"ch:google_chat:access_token":        true,
		"ch:google_chat:refresh_token":       true,
		"ch:google_chat:oauth_client_id":     true,
		"ch:google_chat:oauth_client_secret": true,
	}
	got := publicMessengers(cfg, keys, t.TempDir())
	for _, row := range got {
		id, _ := row["id"].(string)
		switch id {
		case "whatsapp", "teams", "google_chat":
			if row["status"] != "ready" {
				t.Fatalf("%s status=%v reason=%v", id, row["status"], row["status_reason"])
			}
		}
	}
}

// A loopback-only server has no reverse proxy in front of it, so a request
// carrying Cf-Connecting-Ip / X-Forwarded-For came off the tunnel. Only the
// webhook routes are published there; everything else must be refused before
// the Host check is the last thing standing between the tunnel and the token.
func TestForwardedHeadersAreRefusedOffTheWebhookPath(t *testing.T) {
	s, _ := newConnectTestServer(t)
	for _, header := range []string{"Cf-Connecting-Ip", "X-Forwarded-For"} {
		hdr := connectAuthHeader()
		hdr.Set(header, "203.0.113.7")
		code, _, text := doConnectJSON(t, s, http.MethodGet, "/api/sessions", nil, hdr)
		if code != http.StatusForbidden {
			t.Fatalf("%s on /api/sessions: %d %s", header, code, text)
		}
		if !strings.Contains(text, "webhooks") {
			t.Fatalf("%s refusal should tell the owner what is exposed: %s", header, text)
		}
		// The webhook lane still works: that is the whole point of the tunnel.
		code, _, text = doConnectJSON(t, s, http.MethodPost, "/api/webhooks/whatsapp", map[string]any{}, hdr)
		if code == http.StatusForbidden && strings.Contains(text, "webhooks are exposed") {
			t.Fatalf("%s must not be refused on a webhook path: %d %s", header, code, text)
		}
	}
}

// Handing the API token to a browser while the machine is published over a
// tunnel turns one rewritten Host header into full API access.
func TestTokenBootstrapRefusedWhileTunnelRunning(t *testing.T) {
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "1")
	s, _ := newConnectTestServer(t)

	code, _, text := doConnectJSON(t, s, http.MethodGet, "/api/auth/local-bootstrap", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("bootstrap while idle: %d %s", code, text)
	}

	ts := s.tunnelState()
	ts.mu.Lock()
	ts.running = true
	ts.mu.Unlock()
	t.Cleanup(func() {
		ts.mu.Lock()
		ts.running = false
		ts.mu.Unlock()
	})

	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/auth/local-bootstrap", nil, connectAuthHeader())
	if code != http.StatusForbidden {
		t.Fatalf("bootstrap while tunnelled: %d %s", code, text)
	}
	if body["error"] != "tunnel_running" {
		t.Fatalf("owner needs to know why: %s", text)
	}
	if strings.Contains(text, connectTestToken) {
		t.Fatalf("token leaked in refusal: %s", text)
	}
}

// The quick tunnel used to be pointed at the whole local API. It must now
// reach a dedicated loopback origin that serves webhooks and nothing else.
func TestQuickTunnelOriginServesWebhooksOnly(t *testing.T) {
	t.Setenv("REMEDY_PUBLIC_BASE_URL", "")
	t.Setenv("REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD", "1")

	s, home := newConnectTestServer(t)
	s.apiListenPort = 7400

	binDir := filepath.Join(gateway.ManagedTunnelDir(home), "bin")
	if err := os.MkdirAll(binDir, 0o700); err != nil {
		t.Fatal(err)
	}
	binName := "cloudflared"
	if runtimeGOOSWindows() {
		binName = "cloudflared.exe"
	}
	if err := os.WriteFile(filepath.Join(binDir, binName), []byte("fake"), 0o755); err != nil {
		t.Fatal(err)
	}

	prevPoll := pollQuickTunnelHostname
	t.Cleanup(func() { pollQuickTunnelHostname = prevPoll })
	pollQuickTunnelHostname = func(string, time.Duration) (string, error) {
		return "https://demo.trycloudflare.com", nil
	}
	var argv []string
	s.tunnelStarter = func(_ context.Context, a []string, _ map[string]string, _ string) (*tunnelStartedProcess, error) {
		argv = append([]string{}, a...)
		return &tunnelStartedProcess{PID: 4242, Cleanup: func() error { return nil }}, nil
	}

	st, err := s.startMessengerTunnel(tunnelStartBody{Mode: "quick"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.stopMessengerTunnel() })

	if st.OriginURL == "" {
		t.Fatalf("status must name the origin so a named tunnel can be pointed at it: %+v", st)
	}
	if strings.Contains(st.OriginURL, ":7400") {
		t.Fatalf("origin must not be the API port: %s", st.OriginURL)
	}
	if !strings.Contains(strings.Join(argv, " "), st.OriginURL) {
		t.Fatalf("cloudflared argv %v should point at %s", argv, st.OriginURL)
	}

	client := &http.Client{Timeout: 5 * time.Second}
	get := func(path string) (int, string) {
		t.Helper()
		resp, err := client.Get(st.OriginURL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		defer resp.Body.Close()
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return resp.StatusCode, string(raw)
	}
	for _, path := range []string{"/", "/api/ping", "/api/sessions", "/api/auth/local-bootstrap", "/api/connect/me", "/connect/me"} {
		if code, body := get(path); code != http.StatusNotFound {
			t.Fatalf("origin exposed %s: %d %s", path, code, body)
		}
	}
	if code, body := get("/api/webhooks/whatsapp"); code == http.StatusNotFound {
		t.Fatalf("webhook route must be reachable on the origin: %d %s", code, body)
	}
	_ = fmt.Sprint(home)
}
