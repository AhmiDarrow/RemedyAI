package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func startTestServer(t *testing.T, cfg Config) (baseURL string, shutdown func()) {
	t.Helper()
	if cfg.DBPath == "" {
		cfg.DBPath = filepath.Join(t.TempDir(), "memory.db")
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	s, err := New(cfg)
	if err != nil {
		_ = ln.Close()
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		_ = s.Serve(ctx, ln)
		close(done)
	}()
	return "http://" + ln.Addr().String(), func() {
		// Abort turns before cancel so SSE handlers drain within Shutdown.
		if s.claims != nil {
			s.claims.AbortAll()
		}
		cancel()
		_ = ln.Close()
		select {
		case <-done:
		case <-time.After(10 * time.Second):
			t.Error("server did not shut down")
		}
	}
}

func TestPublicAndAuthRoutes(t *testing.T) {
	const token = "test-token-not-a-secret-16"
	base, shutdown := startTestServer(t, Config{Token: token, Version: "0.50.2"})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}

	tests := []struct {
		name       string
		method     string
		path       string
		headers    map[string]string
		wantStatus int
		check      func(t *testing.T, body map[string]any, hdr http.Header)
	}{
		{
			name:       "ping public",
			method:     http.MethodGet,
			path:       "/api/ping",
			wantStatus: http.StatusOK,
			check: func(t *testing.T, body map[string]any, _ http.Header) {
				if body["status"] != "ok" || body["version"] != "0.50.2" {
					t.Fatalf("ping body = %#v", body)
				}
				if _, ok := body["ts"].(float64); !ok {
					t.Fatalf("ping ts missing: %#v", body["ts"])
				}
				nr, ok := body["native_runtime"].(map[string]any)
				if !ok || nr["ready"] != true {
					t.Fatalf("native_runtime = %#v", body["native_runtime"])
				}
			},
		},
		{
			name:       "status public zero counts",
			method:     http.MethodGet,
			path:       "/api/status",
			wantStatus: http.StatusOK,
			check: func(t *testing.T, body map[string]any, _ http.Header) {
				if body["status"] != "ok" || body["version"] != "0.50.2" {
					t.Fatalf("status body = %#v", body)
				}
				gw, _ := body["gateway"].(map[string]any)
				if gw["running"] != false {
					t.Fatalf("gateway = %#v", gw)
				}
				for _, key := range []string{"memory_entries", "sessions_count", "chat_sessions_count"} {
					if n, _ := body[key].(float64); n != 0 {
						t.Fatalf("%s = %v, want 0", key, body[key])
					}
				}
				// skills_count is discovered from bundled/dev roots when present.
				if _, ok := body["skills_count"].(float64); !ok {
					t.Fatalf("skills_count missing: %#v", body["skills_count"])
				}
			},
		},
		{
			name:       "turn-active public",
			method:     http.MethodGet,
			path:       "/api/turn-active",
			wantStatus: http.StatusOK,
			check: func(t *testing.T, body map[string]any, _ http.Header) {
				if body["status"] != "ok" || body["active"] != false {
					t.Fatalf("turn-active = %#v", body)
				}
			},
		},
		{
			name:       "protected 401 without token",
			method:     http.MethodGet,
			path:       "/api/skills",
			wantStatus: http.StatusUnauthorized,
			check: func(t *testing.T, body map[string]any, _ http.Header) {
				if body["error"] != "Unauthorized" {
					t.Fatalf("401 body = %#v", body)
				}
			},
		},
		{
			name:   "protected ok with Bearer",
			method: http.MethodGet,
			path:   "/api/skills",
			headers: map[string]string{
				"Authorization": "Bearer " + token,
			},
			wantStatus: http.StatusOK,
			check: func(t *testing.T, body map[string]any, _ http.Header) {
				// list endpoint returns a JSON array (decoded into nil map here).
				if body != nil && body["error"] != nil {
					t.Fatalf("skills list error body = %#v", body)
				}
			},
		},
		{
			name:   "protected ok with X-Remedy-Token",
			method: http.MethodGet,
			path:   "/api/skills",
			headers: map[string]string{
				"X-Remedy-Token": token,
			},
			wantStatus: http.StatusOK,
		},
		{
			name:   "protected 401 wrong token",
			method: http.MethodGet,
			path:   "/api/skills",
			headers: map[string]string{
				"Authorization": "Bearer wrong-token-value!!",
			},
			wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "cors tauri origin",
			method: http.MethodGet,
			path:   "/api/ping",
			headers: map[string]string{
				"Origin": "tauri://localhost",
			},
			wantStatus: http.StatusOK,
			check: func(t *testing.T, _ map[string]any, hdr http.Header) {
				if got := hdr.Get("Access-Control-Allow-Origin"); got != "tauri://localhost" {
					t.Fatalf("ACA-Origin = %q", got)
				}
			},
		},
		{
			name:   "cors vite origin",
			method: http.MethodGet,
			path:   "/api/ping",
			headers: map[string]string{
				"Origin": "http://localhost:1420",
			},
			wantStatus: http.StatusOK,
			check: func(t *testing.T, _ map[string]any, hdr http.Header) {
				if got := hdr.Get("Access-Control-Allow-Origin"); got != "http://localhost:1420" {
					t.Fatalf("ACA-Origin = %q", got)
				}
			},
		},
		{
			name:   "options preflight no auth",
			method: http.MethodOptions,
			path:   "/api/skills",
			headers: map[string]string{
				"Origin":                        "http://localhost:1420",
				"Access-Control-Request-Method": "GET",
			},
			wantStatus: http.StatusNoContent,
			check: func(t *testing.T, _ map[string]any, hdr http.Header) {
				if got := hdr.Get("Access-Control-Allow-Origin"); got != "http://localhost:1420" {
					t.Fatalf("preflight ACA-Origin = %q", got)
				}
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, base+tc.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			for k, v := range tc.headers {
				req.Header.Set(k, v)
			}
			resp, err := client.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			raw, _ := io.ReadAll(resp.Body)
			if resp.StatusCode != tc.wantStatus {
				t.Fatalf("status = %d, want %d; body=%s", resp.StatusCode, tc.wantStatus, raw)
			}
			var body map[string]any
			if len(raw) > 0 && resp.Header.Get("Content-Type") != "" {
				_ = json.Unmarshal(raw, &body)
			}
			if tc.check != nil {
				tc.check(t, body, resp.Header)
			}
		})
	}
}

func TestResolveTokenFromFileAndEnv(t *testing.T) {
	dir := t.TempDir()
	authDir := filepath.Join(dir, "auth")
	if err := os.MkdirAll(authDir, 0o700); err != nil {
		t.Fatal(err)
	}
	const fileTok = "file-token-not-secret16"
	if err := os.WriteFile(filepath.Join(authDir, "local_api_token"), []byte(fileTok+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	t.Setenv("REMEDY_API_AUTH", "1")
	t.Setenv("REMEDY_API_KEY", "")
	t.Setenv("REMEDY_HOME", dir)
	if got := ResolveToken(""); got != fileTok {
		t.Fatalf("file token = %q, want %q", got, fileTok)
	}

	t.Setenv("REMEDY_API_KEY", "env-token-not-a-secret!")
	if got := ResolveToken(""); got != "env-token-not-a-secret!" {
		t.Fatalf("env token = %q", got)
	}

	t.Setenv("REMEDY_API_AUTH", "0")
	if got := ResolveToken(""); got != "" {
		t.Fatalf("auth disabled still returned %q", got)
	}
}

func TestResolveTokenPosixFallback(t *testing.T) {
	dir := t.TempDir()
	authDir := filepath.Join(dir, "auth")
	if err := os.MkdirAll(authDir, 0o700); err != nil {
		t.Fatal(err)
	}
	// Fake DPAPI primary the process cannot unwrap.
	if err := os.WriteFile(filepath.Join(authDir, "local_api_token"), []byte(`{"v":2,"dpapi":"AAAA"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	const posixTok = "posix-token-not-secret16"
	if err := os.WriteFile(filepath.Join(authDir, "local_api_token.posix"), []byte(posixTok+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_API_AUTH", "1")
	t.Setenv("REMEDY_API_KEY", "")
	if got := ResolveToken(dir); got != posixTok {
		t.Fatalf("posix fallback = %q, want %q", got, posixTok)
	}
}

func TestStatusAuthenticatedChatSessionsCount(t *testing.T) {
	const token = "test-token-not-a-secret-16"
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	base, shutdown := startTestServer(t, Config{
		Token:   token,
		Version: "0.50.2",
		DBPath:  dbPath,
	})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}

	// Create one session.
	req, err := http.NewRequest(http.MethodPost, base+"/api/sessions", strings.NewReader(`{"title":"Counted"}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()

	// Unauth status stays at zero counts.
	resp, err = client.Get(base + "/api/status")
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var unauth map[string]any
	_ = json.Unmarshal(raw, &unauth)
	if n, _ := unauth["chat_sessions_count"].(float64); n != 0 {
		t.Fatalf("unauth count = %v", unauth["chat_sessions_count"])
	}

	req, err = http.NewRequest(http.MethodGet, base+"/api/status", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	var authed map[string]any
	_ = json.Unmarshal(raw, &authed)
	if n, _ := authed["chat_sessions_count"].(float64); n != 1 {
		t.Fatalf("authed count = %v body=%s", authed["chat_sessions_count"], raw)
	}
}

func TestValidateListenAddr(t *testing.T) {
	if err := ValidateListenAddr("127.0.0.1:0"); err != nil {
		t.Fatal(err)
	}
	if err := ValidateListenAddr("0.0.0.0:7400"); err == nil {
		t.Fatal("expected non-loopback rejection")
	}
	if err := ValidateListenAddr("example.com:80"); err == nil {
		t.Fatal("expected hostname rejection")
	}
}

func TestListenAndServeOptInPort(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	boundCh := make(chan string, 1)
	errCh := make(chan error, 1)
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	go func() {
		errCh <- ListenAndServe(ctx, "127.0.0.1:0", Config{
			Token:   "",
			Version: "0.50.2",
			DBPath:  dbPath,
		}, func(bound string) {
			boundCh <- bound
		})
	}()
	var base string
	select {
	case base = <-boundCh:
	case err := <-errCh:
		t.Fatalf("ListenAndServe exited early: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("ListenAndServe did not bind")
	}
	if base == "" || base == "127.0.0.1:7400" {
		t.Fatalf("unexpected bind %q", base)
	}
	resp, err := http.Get("http://" + base + "/api/ping")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("ping status = %d", resp.StatusCode)
	}
	cancel()
	select {
	case <-errCh:
	case <-time.After(2 * time.Second):
		t.Fatal("ListenAndServe did not exit")
	}
}
