package httpapi

import (
	"context"
	"encoding/json"
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
	if !strings.Contains(joined, "--url") || !strings.Contains(joined, "127.0.0.1:7400") {
		t.Fatalf("expected quick tunnel url argv, got %v", startedArgv)
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
		"ch:whatsapp:access_token":            true,
		"ch:teams:app_password":               true,
		"ch:google_chat:access_token":         true,
		"ch:google_chat:refresh_token":        true,
		"ch:google_chat:oauth_client_id":      true,
		"ch:google_chat:oauth_client_secret":  true,
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
