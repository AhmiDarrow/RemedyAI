package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

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
			Token:   "tok-connect-test-not-a-secret",
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
	token := "tok-connect-test-not-a-secret"
	for time.Now().Before(deadline) {
		req, err := http.NewRequest(http.MethodGet, "http://"+base+"/api/connect", nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			time.Sleep(20 * time.Millisecond)
			continue
		}
		_ = json.NewDecoder(resp.Body).Decode(&payload)
		resp.Body.Close()
		if payload["listening"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if payload["listening"] != true {
		t.Fatalf("connect not listening: %+v", payload)
	}
	if payload["serving"] != true {
		t.Fatalf("connect not serving: %+v", payload)
	}

	cancel()
	select {
	case <-errCh:
	case <-time.After(3 * time.Second):
		t.Fatal("ListenAndServe did not exit")
	}
}
