package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestPhoneEndToEndHost is the PC half of the phone end-to-end check. It is
// skipped unless REMEDY_PHONE_E2E_DIR names a folder. With it set, the test
// serves a real runtime with the Connect gateway on, writes a fresh pairing
// code to <dir>/qr.txt, and keeps serving until <dir>/done appears, so the
// Rust phone app (connect/app/tests/live.rs) can pair and talk through the
// tunnel to the same code a real PC runs.
func TestPhoneEndToEndHost(t *testing.T) {
	dir := strings.TrimSpace(os.Getenv("REMEDY_PHONE_E2E_DIR"))
	if dir == "" {
		t.Skip("set REMEDY_PHONE_E2E_DIR to run the phone end-to-end host")
	}
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	cfg := "connect_enabled = true\nconnect_bind_host = \"127.0.0.1\"\nconnect_bind_port = 17481\nconnect_rdv_enabled = false\n"
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	boundCh := make(chan string, 1)
	errCh := make(chan error, 1)
	go func() {
		errCh <- ListenAndServe(ctx, "127.0.0.1:0", Config{
			HomeDir: home, Token: connectTestToken, DBPath: filepath.Join(home, "memory.db"),
		}, func(bound string) { boundCh <- bound })
	}()
	var base string
	select {
	case base = <-boundCh:
	case err := <-errCh:
		t.Fatalf("ListenAndServe exited early: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("ListenAndServe did not bind")
	}
	call := func(method, path string) (int, map[string]any) {
		req, err := http.NewRequest(method, "http://"+base+path, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+connectTestToken)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, nil
		}
		defer resp.Body.Close()
		var body map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&body)
		return resp.StatusCode, body
	}
	deadline := time.Now().Add(5 * time.Second)
	var state map[string]any
	for time.Now().Before(deadline) {
		if _, state = call(http.MethodGet, "/api/connect"); listeningReady(state) {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !listeningReady(state) {
		t.Fatalf("the Connect gateway is not listening: %v", state)
	}
	t.Logf("gateway listening: %v", state["listening"])
	code, body := call(http.MethodPost, "/api/connect/pair/start")
	qr, _ := body["qr"].(string)
	if code != http.StatusOK || !strings.Contains(qr, "remedy-connect/1") {
		t.Fatalf("pair/start: %d %v", code, body)
	}
	if err := os.WriteFile(filepath.Join(dir, "qr.txt"), []byte(qr), 0o600); err != nil {
		t.Fatal(err)
	}
	done := filepath.Join(dir, "done")
	wait := time.Now().Add(150 * time.Second)
	for time.Now().Before(wait) {
		if _, err := os.Stat(done); err == nil {
			_, devices := call(http.MethodGet, "/api/connect")
			t.Logf("phone finished; gateway state: %v", devices["devices"])
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatal("the phone side never reported done")
}
