package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"path/filepath"
	"testing"
	"time"
)

func TestHiveRosterSpawnAssignRetire(t *testing.T) {
	home := t.TempDir()
	base, shutdown := startTestServer(t, Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	defer shutdown()

	client := &http.Client{Timeout: 5 * time.Second}
	auth := func(req *http.Request) {
		req.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := client.Get(base + "/api/hive/roster")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("roster unauth status=%d", resp.StatusCode)
	}

	req, _ := http.NewRequest(http.MethodGet, base+"/api/hive/roster", nil)
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var roster map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&roster); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if roster["count"] != float64(0) {
		t.Fatalf("empty roster = %#v", roster)
	}

	spawnBody, _ := json.Marshal(map[string]any{
		"goal":    "review auth.py",
		"cadence": "forager",
	})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/hive/spawn", bytes.NewReader(spawnBody))
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var spawned map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&spawned); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if spawned["ok"] != true || spawned["hive_id"] == nil || spawned["started"] != true {
		t.Fatalf("spawn = %#v", spawned)
	}
	hiveID, _ := spawned["hive_id"].(string)

	postBody, _ := json.Marshal(map[string]any{
		"goal":    "watch logs",
		"cadence": "post",
		"pulse_s": 60,
	})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/hive/spawn", bytes.NewReader(postBody))
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var post map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&post); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if post["ok"] != true || post["cadence"] != "post" {
		t.Fatalf("post spawn = %#v", post)
	}
	postID, _ := post["hive_id"].(string)

	assignBody, _ := json.Marshal(map[string]any{"hive_id": postID, "goal": "watch deploy"})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/hive/assign", bytes.NewReader(assignBody))
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var assigned map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&assigned); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if assigned["ok"] != true || assigned["goal"] != "watch deploy" {
		t.Fatalf("assign = %#v", assigned)
	}

	req, _ = http.NewRequest(http.MethodGet, base+"/api/hive/roster", nil)
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.NewDecoder(resp.Body).Decode(&roster); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if roster["count"] != float64(2) || roster["live_foragers"] != float64(1) || roster["live_posts"] != float64(1) {
		t.Fatalf("roster after spawn = %#v", roster)
	}

	retireBody, _ := json.Marshal(map[string]any{"hive_id": hiveID})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/hive/retire", bytes.NewReader(retireBody))
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var retired map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&retired); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if retired["ok"] != true || retired["status"] != "retired" {
		t.Fatalf("retire = %#v", retired)
	}
}

func TestHiveAssignRejectsForager(t *testing.T) {
	home := t.TempDir()
	base, shutdown := startTestServer(t, Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	authHdr := "Bearer test-token-not-a-secret-16"

	spawnBody, _ := json.Marshal(map[string]any{"goal": "one shot", "cadence": "forager"})
	req, _ := http.NewRequest(http.MethodPost, base+"/api/hive/spawn", bytes.NewReader(spawnBody))
	req.Header.Set("Authorization", authHdr)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var spawned map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&spawned)
	resp.Body.Close()
	id, _ := spawned["hive_id"].(string)

	assignBody, _ := json.Marshal(map[string]any{"hive_id": id, "goal": "nope"})
	req, _ = http.NewRequest(http.MethodPost, base+"/api/hive/assign", bytes.NewReader(assignBody))
	req.Header.Set("Authorization", authHdr)
	req.Header.Set("Content-Type", "application/json")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var assigned map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&assigned)
	resp.Body.Close()
	if assigned["ok"] != false {
		t.Fatalf("forager assign should fail: %#v", assigned)
	}
}
