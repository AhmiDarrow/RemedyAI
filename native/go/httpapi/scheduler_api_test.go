package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSchedulerJobsAddListCancelPersist(t *testing.T) {
	home := t.TempDir()
	base, shutdown := startTestServer(t, Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	defer shutdown()

	client := &http.Client{Timeout: 5 * time.Second}
	auth := "Bearer test-token-not-a-secret-16"

	body, _ := json.Marshal(map[string]any{
		"id":          "daily-check",
		"trigger":     "recurring",
		"interval_ms": 60000,
		"priority":    3,
	})
	req, _ := http.NewRequest(http.MethodPost, base+"/api/scheduler/jobs", bytes.NewReader(body))
	req.Header.Set("Authorization", auth)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var added map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&added); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || added["ok"] != true {
		t.Fatalf("add = %d %#v", resp.StatusCode, added)
	}

	req, _ = http.NewRequest(http.MethodGet, base+"/api/scheduler/jobs", nil)
	req.Header.Set("Authorization", auth)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var listed map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&listed); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if listed["count"] != float64(1) {
		t.Fatalf("list = %#v", listed)
	}

	req, _ = http.NewRequest(http.MethodPost, base+"/api/scheduler/jobs/daily-check/cancel", nil)
	req.Header.Set("Authorization", auth)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var canceled map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&canceled); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if canceled["ok"] != true || canceled["status"] != "canceled" {
		t.Fatalf("cancel = %#v", canceled)
	}

	raw, err := os.ReadFile(filepath.Join(home, "scheduler.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !json.Valid(raw) || !bytes.Contains(raw, []byte("daily-check")) {
		t.Fatalf("persist snapshot = %s", raw)
	}
}

func TestSchedulerGoalReady(t *testing.T) {
	home := t.TempDir()
	base, shutdown := startTestServer(t, Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	auth := "Bearer test-token-not-a-secret-16"

	body, _ := json.Marshal(map[string]any{
		"id":      "goal-job",
		"trigger": "on_goal",
		"goal_id": "g1",
	})
	req, _ := http.NewRequest(http.MethodPost, base+"/api/scheduler/jobs", bytes.NewReader(body))
	req.Header.Set("Authorization", auth)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	ready, _ := json.Marshal(map[string]any{"goal_id": "g1"})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/scheduler/goals/ready", bytes.NewReader(ready))
	req.Header.Set("Authorization", auth)
	req.Header.Set("Content-Type", "application/json")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&out)
	resp.Body.Close()
	if out["ok"] != true {
		t.Fatalf("goal ready = %#v", out)
	}
}
