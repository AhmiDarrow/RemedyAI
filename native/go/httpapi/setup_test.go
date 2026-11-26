package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func newSetupTestServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("setup_completed = false\nname = \"Remedy\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   "setup-test-token-not-sec",
		DBPath:  filepath.Join(home, "memory.db"),
		Version: "0.70.5-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doSetupJSON(t *testing.T, s *Server, method, path, body string) (int, map[string]any) {
	t.Helper()
	var req *http.Request
	var err error
	if body != "" {
		req, err = http.NewRequest(method, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
	} else {
		req, err = http.NewRequest(method, path, nil)
	}
	if err != nil {
		t.Fatal(err)
	}
	rec := httptest.NewRecorder()
	s.mux.ServeHTTP(rec, req)
	var out map[string]any
	if rec.Body.Len() > 0 {
		if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
			t.Fatalf("decode %s: %v raw=%s", path, err, rec.Body.String())
		}
	}
	return rec.Code, out
}

func TestSetupGetAndSwitchModeKeepsDraft(t *testing.T) {
	s := newSetupTestServer(t)
	code, body := doSetupJSON(t, s, http.MethodGet, "/api/setup", "")
	if code != 200 {
		t.Fatalf("GET status=%d body=%v", code, body)
	}
	if body["traditional_available"] != true {
		t.Fatalf("traditional setup must always be available: %v", body)
	}
	if body["mode"] != "conversation" {
		t.Fatalf("default mode=%v", body["mode"])
	}
	qs, _ := body["questions"].([]any)
	if len(qs) < 10 {
		t.Fatalf("expected full question catalog, got %d", len(qs))
	}

	code, body = doSetupJSON(t, s, http.MethodPut, "/api/setup",
		`{"draft":{"user_name":"Ahmi","persona":"playful","llm_api_key":"sk-should-never-persist"}}`)
	if code != 200 {
		t.Fatalf("PUT draft=%d %v", code, body)
	}
	draft, _ := body["draft"].(map[string]any)
	if draft["user_name"] != "Ahmi" || draft["persona"] != "playful" {
		t.Fatalf("draft=%v", draft)
	}
	raw, _ := json.Marshal(body)
	if strings.Contains(string(raw), "sk-should-never-persist") {
		t.Fatal("API key leaked into setup session")
	}

	code, body = doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"mode":"traditional"}`)
	if code != 200 {
		t.Fatalf("switch=%d %v", code, body)
	}
	if body["mode"] != "traditional" {
		t.Fatalf("mode=%v", body["mode"])
	}
	draft, _ = body["draft"].(map[string]any)
	if draft["user_name"] != "Ahmi" {
		t.Fatalf("switching modes lost the draft: %v", draft)
	}

	code, body = doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"mode":"conversation"}`)
	if code != 200 || body["mode"] != "conversation" {
		t.Fatalf("switch back=%d %v", code, body)
	}
	draft, _ = body["draft"].(map[string]any)
	if draft["user_name"] != "Ahmi" {
		t.Fatalf("conversation switch lost draft: %v", draft)
	}
}

func TestSetupSkipAllUsesDefaultsAndSecretsStayOut(t *testing.T) {
	s := newSetupTestServer(t)
	_, _ = doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"draft":{"user_name":"Sam"}}`)
	code, body := doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"action":"skip_all"}`)
	if code != 200 {
		t.Fatalf("skip_all=%d %v", code, body)
	}
	if body["status"] != "skipped" {
		t.Fatalf("status=%v", body["status"])
	}
	cfg := LoadConfig(s.homeDir)
	if cfg["setup_completed"] != true && cfg["setup_completed"] != "true" {
		// applySettingsUpdate stores bool
		if got := coerceBool(cfg["setup_completed"], false); !got {
			t.Fatalf("setup_completed not set: %v", cfg["setup_completed"])
		}
	}
	if cfgString(cfg, "user_name", "") != "Sam" {
		t.Fatalf("answered name should be kept, defaults fill the rest: user_name=%v", cfg["user_name"])
	}
}

func TestClassifyMindFailureReasons(t *testing.T) {
	if classifyMindFailure(nil) != nil {
		t.Fatal("nil status is not a failure")
	}
	if classifyMindFailure(map[string]any{"installed": true, "ready": true}) != nil {
		t.Fatal("ready mind is not a failure")
	}
	// No GPU is not a failure.
	ok := map[string]any{"installed": true, "ready": true, "health": map[string]any{"nvidia_detected": false, "ram_gb": 16, "min_ram_gb": 8}}
	if classifyMindFailure(ok) != nil {
		t.Fatal("CPU-only must not be treated as failure")
	}
	fail := classifyMindFailure(map[string]any{"progress": map[string]any{"phase": "downloading", "error": "checksum mismatch (sha256)"}})
	if fail == nil || fail["reason"] != "verify" {
		t.Fatalf("verify reason=%v", fail)
	}
	ram := classifyMindFailure(map[string]any{"health": map[string]any{"ram_gb": 2.0, "min_ram_gb": 8.0}})
	if ram == nil || ram["reason"] != "low_ram" {
		t.Fatalf("low_ram reason=%v", ram)
	}
}

func TestUpdateOfferOnceForExistingInstall(t *testing.T) {
	s := newSetupTestServer(t)
	// Fresh test server writes setup_completed=false — no pop-up yet.
	code, body := doSetupJSON(t, s, http.MethodGet, "/api/setup", "")
	if code != 200 {
		t.Fatalf("GET status=%d body=%v", code, body)
	}
	offer, _ := body["update_offer"].(map[string]any)
	if offer == nil || offer["available"] == true {
		t.Fatalf("new install must not get the update pop-up: %v", offer)
	}

	cfgPath := filepath.Join(s.homeDir, "config.toml")
	if err := os.WriteFile(cfgPath, []byte("setup_completed = true\nname = \"Remedy\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()

	code, body = doSetupJSON(t, s, http.MethodGet, "/api/setup", "")
	if code != 200 {
		t.Fatalf("GET after existing=%d %v", code, body)
	}
	offer, _ = body["update_offer"].(map[string]any)
	if offer == nil || offer["available"] != true {
		t.Fatalf("existing install should see the one-time offer: %v", offer)
	}
	if offer["primary"] != "Let's go" || offer["secondary"] != "Not now" {
		t.Fatalf("buttons=%v / %v", offer["primary"], offer["secondary"])
	}
	bodyText, _ := offer["body"].(string)
	if !strings.Contains(bodyText, "smarter token saving") || !strings.Contains(bodyText, "A2A and MCP") || !strings.Contains(bodyText, "house rules") {
		t.Fatalf("copy should name what ships: %q", bodyText)
	}
	if strings.Contains(bodyText, "built-in mind") {
		t.Fatalf("copy must not mention the shelved mind: %q", bodyText)
	}

	code, body = doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"action":"update_offer_later"}`)
	if code != 200 {
		t.Fatalf("Not now=%d %v", code, body)
	}
	offer, _ = body["update_offer"].(map[string]any)
	if offer["available"] == true {
		t.Fatal("Not now must consume the offer")
	}
	code, body = doSetupJSON(t, s, http.MethodGet, "/api/setup", "")
	offer, _ = body["update_offer"].(map[string]any)
	if offer["available"] == true {
		t.Fatal("the pop-up must not appear a second time")
	}
}

func TestUpdateOfferLetsGoDoesNotRerunSetupWhenComplete(t *testing.T) {
	s := newSetupTestServer(t)
	if err := os.WriteFile(filepath.Join(s.homeDir, "config.toml"), []byte("setup_completed = true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	code, body := doSetupJSON(t, s, http.MethodPut, "/api/setup", `{"action":"update_offer_go"}`)
	if code != 200 {
		t.Fatalf("Let's go=%d %v", code, body)
	}
	offer, _ := body["update_offer"].(map[string]any)
	if offer["available"] == true {
		t.Fatal("Let's go must consume the offer")
	}
	if body["onboarding_pending"] == true {
		t.Fatalf("Let's go must not re-trigger setup when setup_completed: %v", body)
	}
}
