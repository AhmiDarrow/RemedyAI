package httpapi

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

// The named tunnel's token is a credential: it reaches cloudflared through
// TUNNEL_TOKEN, never through argv, which the process list shows to anyone.
func TestNamedTunnelTokenStaysOffTheCommandLine(t *testing.T) {
	t.Setenv("REMEDY_PUBLIC_BASE_URL", "")
	t.Setenv("REMEDY_WEBHOOK_PUBLIC_URL", "")
	t.Setenv("REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD", "1")

	s, home := newConnectTestServer(t)
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

	const tok = "eyJhIjoiZmFrZS10dW5uZWwtdG9rZW4tZm9yLXRlc3RzIn0"
	var argv []string
	var env map[string]string
	s.tunnelStarter = func(_ context.Context, a []string, e map[string]string, _ string) (*tunnelStartedProcess, error) {
		argv, env = append([]string{}, a...), e
		return &tunnelStartedProcess{PID: 4243, Cleanup: func() error { return nil }}, nil
	}
	code, _, text := doConnectJSON(t, s, http.MethodPost, "/api/messengers/tunnel/start", map[string]any{
		"mode":            "named",
		"token":           tok,
		"public_base_url": "https://hooks.example.com",
	}, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("start %d %s", code, text)
	}
	t.Cleanup(func() {
		doConnectJSON(t, s, http.MethodPost, "/api/messengers/tunnel/stop", nil, connectAuthHeader())
	})
	joined := strings.Join(argv, " ")
	if strings.Contains(joined, tok) || strings.Contains(joined, "--token") {
		t.Fatalf("tunnel token on the command line: %v", argv)
	}
	if !strings.Contains(joined, "tunnel") || argv[len(argv)-1] != "run" {
		t.Fatalf("argv=%v", argv)
	}
	if env["TUNNEL_TOKEN"] != tok {
		t.Fatalf("TUNNEL_TOKEN not passed to cloudflared: env=%v", env)
	}
}
