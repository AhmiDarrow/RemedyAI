package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const updatesTestToken = "tok-updates-test-not-a-secret"

func newUpdatesTestServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   updatesTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
		Version: "0.41.5",
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func TestUpdatesCheckCachesSecondPoll(t *testing.T) {
	s := newUpdatesTestServer(t)
	var n atomic.Int64
	prev := updatesHTTPGet
	t.Cleanup(func() { updatesHTTPGet = prev })
	updatesHTTPGet = func(url string, timeout time.Duration) (map[string]any, error) {
		n.Add(1)
		switch {
		case strings.Contains(url, "pypi.org"):
			return map[string]any{"info": map[string]any{"version": "0.41.6"}}, nil
		case strings.Contains(url, "latest.json"):
			return map[string]any{
				"version": "0.41.6",
				"platforms": map[string]any{
					"windows-x86_64": map[string]any{
						"url": "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.41.6/setup.exe",
					},
				},
			}, nil
		default:
			return nil, fmt.Errorf("unexpected url %s", url)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/api/updates/check?current=0.41.5", nil)
	req.Header.Set("Authorization", "Bearer "+updatesTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
	var first map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &first)
	afterFirst := n.Load()
	if afterFirst < 1 {
		t.Fatal("expected at least one fetch")
	}
	if first["update_available"] != true {
		t.Fatalf("expected update available: %v", first)
	}
	if first["current_version"] != "0.41.5" {
		t.Fatalf("current=%v", first["current_version"])
	}
	if first["latest_desktop"] != "0.41.6" {
		t.Fatalf("latest_desktop=%v", first["latest_desktop"])
	}

	rr2 := httptest.NewRecorder()
	req2 := httptest.NewRequest(http.MethodGet, "/api/updates/check?current=0.41.5", nil)
	req2.Header.Set("Authorization", "Bearer "+updatesTestToken)
	req2.Host = "127.0.0.1:7400"
	req2.RemoteAddr = "127.0.0.1:12345"
	s.Handler().ServeHTTP(rr2, req2)
	if rr2.Code != http.StatusOK {
		t.Fatalf("second status=%d", rr2.Code)
	}
	if n.Load() != afterFirst {
		t.Fatalf("cache miss: fetches %d -> %d", afterFirst, n.Load())
	}
	var second map[string]any
	_ = json.Unmarshal(rr2.Body.Bytes(), &second)
	if second["latest_desktop"] != first["latest_desktop"] {
		t.Fatalf("cached body diverged: %v vs %v", second, first)
	}
}
