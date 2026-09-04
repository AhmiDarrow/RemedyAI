package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

func TestLocalBootstrapLoopback(t *testing.T) {
	home := t.TempDir()
	const token = "bootstrap-token-not-sec"
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "1")
	t.Setenv("REMEDY_API_AUTH", "1")
	base, shutdown := startTestServer(t, Config{
		Token:   token,
		HomeDir: home,
		Version: "0.50.2",
	})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}

	resp, err := client.Get(base + "/api/auth/local-bootstrap")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status=%d body=%s", resp.StatusCode, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["token"] != token {
		t.Fatalf("token=%v", body["token"])
	}
	if body["auth_required"] != true {
		t.Fatalf("auth_required=%v", body["auth_required"])
	}
}

func TestLocalBootstrapDisabled(t *testing.T) {
	home := t.TempDir()
	const token = "bootstrap-token-not-sec"
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "0")
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(base + "/api/auth/local-bootstrap")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status=%d", resp.StatusCode)
	}
	raw, _ := io.ReadAll(resp.Body)
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	if body["error"] != "http_bootstrap_disabled" {
		t.Fatalf("body=%s", raw)
	}
}

func TestLocalBootstrapHostRebindingBlocked(t *testing.T) {
	home := t.TempDir()
	const token = "bootstrap-token-not-sec"
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "1")
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	req, err := http.NewRequest(http.MethodGet, base+"/api/auth/local-bootstrap", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Host = "evil.example"
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status=%d", resp.StatusCode)
	}
}

func TestSettingsFirstRunRoundTrip(t *testing.T) {
	home := t.TempDir()
	const token = "settings-token-not-sec!"
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "1")
	base, shutdown := startTestServer(t, Config{
		Token:   token,
		HomeDir: home,
		Version: "0.50.2",
	})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	auth := func(req *http.Request) {
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
	}

	// GET — no config yet → needs_setup.
	req, err := http.NewRequest(http.MethodGet, base+"/api/settings", nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET status=%d body=%s", resp.StatusCode, raw)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["version"] != "0.50.2" {
		t.Fatalf("version=%v", got["version"])
	}
	if got["config_exists"] != false {
		t.Fatalf("config_exists=%v", got["config_exists"])
	}
	if got["needs_setup"] != true || got["setup_completed"] != false {
		t.Fatalf("setup flags=%v / %v", got["needs_setup"], got["setup_completed"])
	}
	if _, ok := got["agent_gender"]; !ok {
		t.Fatal("agent_gender missing")
	}
	if got["llm_api_key_set"] != false {
		t.Fatalf("llm_api_key_set=%v", got["llm_api_key_set"])
	}

	// PUT — first-run wizard save.
	body := `{
		"llm_provider":"openai",
		"llm_model":"gpt-4o-mini",
		"llm_base_url":"https://api.openai.com/v1",
		"llm_api_key":"sk-test-not-a-real-key-abcdef",
		"name":"Remedy",
		"user_name":"Ahmi",
		"agent_gender":"neutral",
		"setup_completed":true,
		"http_bootstrap":true,
		"close_to_tray":true
	}`
	req, err = http.NewRequest(http.MethodPut, base+"/api/settings", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("PUT status=%d body=%s", resp.StatusCode, raw)
	}
	var saved map[string]any
	if err := json.Unmarshal(raw, &saved); err != nil {
		t.Fatal(err)
	}
	if saved["status"] != "saved" {
		t.Fatalf("saved=%s", raw)
	}
	if saved["llm_provider"] != "openai" {
		t.Fatalf("provider=%v", saved["llm_provider"])
	}

	// Secrets must not appear in config.toml.
	cfgPath := filepath.Join(home, "config.toml")
	cfgRaw, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(cfgRaw), "sk-test-not-a-real-key-abcdef") {
		t.Fatal("api key leaked into config.toml")
	}
	if !strings.Contains(string(cfgRaw), "setup_completed = true") {
		t.Fatalf("setup_completed missing: %s", cfgRaw)
	}

	// Key landed in secure store.
	if secret.GetProviderSecret(home, "openai") != "sk-test-not-a-real-key-abcdef" {
		t.Fatal("provider key not stored")
	}

	// GET after save — setup done, key set, no raw secret.
	req, err = http.NewRequest(http.MethodGet, base+"/api/settings", nil)
	if err != nil {
		t.Fatal(err)
	}
	auth(req)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["needs_setup"] != false || got["setup_completed"] != true {
		t.Fatalf("after save setup flags=%v / %v", got["needs_setup"], got["setup_completed"])
	}
	if got["llm_api_key_set"] != true {
		t.Fatalf("llm_api_key_set=%v", got["llm_api_key_set"])
	}
	if got["user_name"] != "Ahmi" {
		t.Fatalf("user_name=%v", got["user_name"])
	}
	if got["agent_gender"] != "neutral" {
		t.Fatalf("agent_gender=%v", got["agent_gender"])
	}
	blob, _ := json.Marshal(got)
	if strings.Contains(string(blob), "sk-test-not-a-real-key-abcdef") {
		t.Fatal("raw key leaked in GET /api/settings")
	}
}

func TestSettingsEmptyPutIsNoop(t *testing.T) {
	home := t.TempDir()
	const token = "settings-token-not-sec!"
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	req, err := http.NewRequest(http.MethodPut, base+"/api/settings", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		t.Fatalf("status=%d body=%s", resp.StatusCode, raw)
	}
}

func TestSettingsRequiresAuth(t *testing.T) {
	home := t.TempDir()
	const token = "settings-token-not-sec!"
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(base + "/api/settings")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status=%d", resp.StatusCode)
	}
}

func TestConfigStoreRoundTrip(t *testing.T) {
	home := t.TempDir()
	path := filepath.Join(home, "config.toml")
	cfg := ConfigMap{
		"llm_provider":    "demo",
		"setup_completed": true,
		"name":            "Remedy",
		"enabled_channels": []string{"cli", "telegram"},
		"vision": map[string]any{
			"enabled":  true,
			"model_id": "smolvlm2-2.2b",
		},
	}
	if err := WriteConfig(path, cfg); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	got := LoadConfig(home)
	if cfgString(got, "llm_provider", "") != "demo" {
		t.Fatalf("got=%#v", got)
	}
	if !cfgBool(got, "setup_completed", false) {
		t.Fatal("setup_completed")
	}
	vision, ok := asStringMap(got["vision"])
	if !ok || coerceBool(vision["enabled"], false) != true {
		t.Fatalf("vision=%#v", got["vision"])
	}
}
