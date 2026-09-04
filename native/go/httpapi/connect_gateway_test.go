package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const connectTestToken = "tok-connect-test-not-a-secret"

func newConnectTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   connectTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func connectAuthHeader() http.Header {
	h := make(http.Header)
	h.Set("Authorization", "Bearer "+connectTestToken)
	return h
}

func doConnectJSON(t *testing.T, s *Server, method, path string, body any, hdr http.Header) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	if hdr != nil {
		req.Header = hdr.Clone()
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if req.Header.Get("Authorization") == "" {
		req.Header.Set("Authorization", "Bearer "+connectTestToken)
	}
	// httptest defaults Host=example.com and RemoteAddr=192.0.2.1:1234.
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	text := rr.Body.String()
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, text
}

func TestGetConnectDefaultOff(t *testing.T) {
	s, _ := newConnectTestServer(t)
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/connect", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	if body["enabled"] != false {
		t.Fatalf("enabled=%v", body["enabled"])
	}
	if paused, ok := body["paused"]; ok && paused != false && paused != nil {
		t.Fatalf("paused=%v", paused)
	}
	if _, ok := body["devices"]; !ok {
		t.Fatal("missing devices")
	}
	low := strings.ToLower(text)
	for _, bad := range []string{"local_api_token", "bearer", "public_hex", "ps="} {
		if strings.Contains(low, bad) {
			t.Fatalf("secret leak %q in %s", bad, text)
		}
	}
	if _, ok := body["panes"].(map[string]any); !ok {
		t.Fatalf("panes=%T", body["panes"])
	}
	if _, ok := body["gateway"].(map[string]any); !ok {
		t.Fatalf("gateway=%T", body["gateway"])
	}
	if body["listening"] != nil {
		t.Fatalf("listening=%v", body["listening"])
	}
}

func TestPairStartConnectHopHeader403(t *testing.T) {
	s, _ := newConnectTestServer(t)
	hdr := connectAuthHeader()
	hdr.Set("X-Remedy-Connect-Hop", "1")
	code, _, text := doConnectJSON(t, s, http.MethodPost, "/api/connect/pair/start", nil, hdr)
	if code != http.StatusForbidden {
		t.Fatalf("status %d", code)
	}
	if strings.Contains(text, "ps=") {
		t.Fatal("pair secret leaked")
	}
}

func TestPairStartNonLoopback403(t *testing.T) {
	s, _ := newConnectTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/connect/pair/start", nil)
	req.Header.Set("Authorization", "Bearer "+connectTestToken)
	req.Header.Set("Host", "10.9.8.7:7400")
	req.RemoteAddr = "10.9.8.7:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("status %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestPairStartLoopbackAfterBind(t *testing.T) {
	s, _ := newConnectTestServer(t)
	code, _, text := doConnectJSON(t, s, http.MethodPut, "/api/connect", map[string]any{
		"enabled":   false,
		"bind_host": "127.0.0.1",
		"bind_port": 7401,
	}, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("put %d %s", code, text)
	}
	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/connect/pair/start", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("pair %d %s", code, text)
	}
	qr, _ := body["qr"].(string)
	if !strings.Contains(qr, "remedy-connect/1") {
		t.Fatalf("qr=%q", qr)
	}
	if strings.Contains(qr, "local_api_token") || strings.Contains(qr, "Bearer") || strings.Contains(qr, connectTestToken) {
		t.Fatalf("token leak in qr=%q", qr)
	}
}

func TestWildcardEnableRejected(t *testing.T) {
	s, _ := newConnectTestServer(t)
	code, _, _ := doConnectJSON(t, s, http.MethodPut, "/api/connect", map[string]any{
		"enabled":   true,
		"bind_host": "0.0.0.0",
		"bind_port": 7401,
	}, connectAuthHeader())
	if code != http.StatusBadRequest && code != http.StatusForbidden {
		t.Fatalf("status %d", code)
	}
	_, body, _ := doConnectJSON(t, s, http.MethodGet, "/api/connect", nil, connectAuthHeader())
	if body["enabled"] != false {
		t.Fatalf("enabled still %v", body["enabled"])
	}
}

func TestPauseResumeAndRevokeRoutes(t *testing.T) {
	s, _ := newConnectTestServer(t)
	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/connect/pause", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("pause %d %s", code, text)
	}
	if body["paused"] != true {
		t.Fatalf("%v", body)
	}
	code, body, text = doConnectJSON(t, s, http.MethodPost, "/api/connect/resume", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("resume %d %s", code, text)
	}
	if body["paused"] != false {
		t.Fatalf("%v", body)
	}
	code, _, _ = doConnectJSON(t, s, http.MethodPost, "/api/connect/devices/not-a-real-id/revoke", nil, connectAuthHeader())
	if code != http.StatusNotFound && code != http.StatusBadRequest {
		t.Fatalf("revoke status %d", code)
	}
}

func TestAddressesEndpoint(t *testing.T) {
	s, _ := newConnectTestServer(t)
	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/connect/addresses", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("%d %s", code, text)
	}
	addrs, ok := body["addresses"].([]any)
	if !ok {
		t.Fatalf("addresses=%T %v", body["addresses"], body)
	}
	_ = addrs
	if _, ok := body["defaults"].(map[string]any); !ok {
		t.Fatalf("defaults missing: %v", body)
	}
}

func TestConnectMeNotOnManagementSurfaceAsSidecarSecret(t *testing.T) {
	s, _ := newConnectTestServer(t)
	_, _, text := doConnectJSON(t, s, http.MethodGet, "/api/connect", nil, connectAuthHeader())
	low := strings.ToLower(text)
	if strings.Contains(low, "ps=") || strings.Contains(low, "private") {
		t.Fatalf("leak: %s", text)
	}
}

func TestServeStartsConnectGatewayFromSettings(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	cfgPath := filepath.Join(home, "config.toml")
	body := "" +
		"connect_enabled = true\n" +
		"connect_bind_host = \"127.0.0.1\"\n" +
		"connect_bind_port = 0\n" +
		"connect_rdv_enabled = false\n"
	if err := os.WriteFile(cfgPath, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	boundCh := make(chan string, 1)
	errCh := make(chan error, 1)
	go func() {
		errCh <- ListenAndServe(ctx, "127.0.0.1:0", Config{
			HomeDir: home,
			Token:   connectTestToken,
			DBPath:  filepath.Join(home, "memory.db"),
		}, func(bound string) {
			boundCh <- bound
		})
	}()
	var base string
	select {
	case base = <-boundCh:
	case err := <-errCh:
		t.Fatalf("ListenAndServe exited early: %v", err)
	case <-time.After(3 * time.Second):
		t.Fatal("ListenAndServe did not bind")
	}

	deadline := time.Now().Add(3 * time.Second)
	var payload map[string]any
	for time.Now().Before(deadline) {
		req, err := http.NewRequest(http.MethodGet, "http://"+base+"/api/connect", nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+connectTestToken)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			time.Sleep(20 * time.Millisecond)
			continue
		}
		_ = json.NewDecoder(resp.Body).Decode(&payload)
		resp.Body.Close()
		if listeningReady(payload) {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !listeningReady(payload) {
		t.Fatalf("connect not listening: %+v", payload)
	}
	if payload["enabled"] != true {
		t.Fatalf("enabled=%v", payload["enabled"])
	}
	gw, _ := payload["gateway"].(map[string]any)
	if gw == nil || gw["serving"] != true {
		t.Fatalf("gateway not serving: %+v", payload)
	}
	if _, ok := payload["devices"].([]any); !ok && payload["devices"] == nil {
		t.Fatalf("devices missing: %+v", payload)
	}
	if _, ok := payload["panes"].(map[string]any); !ok {
		t.Fatalf("panes missing: %+v", payload)
	}

	cancel()
	select {
	case <-errCh:
	case <-time.After(3 * time.Second):
		t.Fatal("ListenAndServe did not exit")
	}
}

func listeningReady(payload map[string]any) bool {
	switch v := payload["listening"].(type) {
	case []any:
		return len(v) == 2
	case bool:
		return v
	default:
		return false
	}
}
