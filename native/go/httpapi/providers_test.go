package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const providersTestToken = "tok-providers-test-not-a-secret"

func newProvidersTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_LIVE_MODELS", "0") // catalog-only in unit tests
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   providersTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doProvidersJSON(t *testing.T, s *Server, method, path string) (int, map[string]any, string) {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	req.Header.Set("Authorization", "Bearer "+providersTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, string(raw)
}

func TestListProvidersShape(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	providers, ok := body["providers"].([]any)
	if !ok || len(providers) < 10 {
		t.Fatalf("expected providers list, got %s", text)
	}
	ids := map[string]bool{}
	for _, p := range providers {
		m, _ := p.(map[string]any)
		id, _ := m["id"].(string)
		ids[id] = true
		if m["name"] == nil || m["base_url"] == nil {
			t.Fatalf("provider missing name/base_url: %v", m)
		}
		if _, ok := m["models"].([]any); !ok {
			t.Fatalf("provider %s missing models: %v", id, m)
		}
		if _, ok := m["auth"].([]any); !ok {
			t.Fatalf("provider %s missing auth: %v", id, m)
		}
		if _, ok := m["oauth"].(bool); !ok {
			t.Fatalf("provider %s missing oauth bool: %v", id, m)
		}
	}
	for _, want := range []string{"demo", "openai", "anthropic", "xai", "ollama", "custom"} {
		if !ids[want] {
			t.Fatalf("missing provider %s in %v", want, ids)
		}
	}
	// xAI advertises oauth.
	for _, p := range providers {
		m, _ := p.(map[string]any)
		if m["id"] == "xai" {
			if m["oauth"] != true {
				t.Fatalf("xai oauth=%v", m["oauth"])
			}
			break
		}
	}
}

func TestListFreeProviders(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers/free")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	opts, ok := body["options"].([]any)
	if !ok || len(opts) < 3 {
		t.Fatalf("expected free options, got %s", text)
	}
	first, _ := opts[0].(map[string]any)
	if first["id"] != "demo" {
		t.Fatalf("first free option want demo, got %v", first["id"])
	}
}

func TestListFreeProvidersHidesDemoWhenDisabled(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	t.Setenv("REMEDY_DEMO_DISABLED", "1")
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers/free")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	for _, opt := range body["options"].([]any) {
		m, _ := opt.(map[string]any)
		if m["id"] == "demo" {
			t.Fatalf("demo should be hidden: %s", text)
		}
	}
}

func TestListConnectedProviders(t *testing.T) {
	s, home := newProvidersTestServer(t)
	if err := secret.SetProviderSecret(home, "openai", "sk-test-openai"); err != nil {
		t.Fatal(err)
	}

	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers/connected")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	for _, key := range []string{"providers", "connected", "picker", "active_provider", "enabled_providers"} {
		if _, ok := body[key]; !ok {
			t.Fatalf("missing %s in %s", key, text)
		}
	}
	connected, _ := body["connected"].([]any)
	foundDemo, foundOpenAI := false, false
	for _, p := range connected {
		m, _ := p.(map[string]any)
		switch m["id"] {
		case "demo":
			foundDemo = true
			if m["connect_reason"] != "demo" {
				t.Fatalf("demo reason=%v", m["connect_reason"])
			}
			if m["picker_eligible"] != true {
				t.Fatalf("demo should be picker_eligible")
			}
		case "openai":
			foundOpenAI = true
			if m["connected"] != true || m["connect_reason"] != "api_key" {
				t.Fatalf("openai connected=%v reason=%v", m["connected"], m["connect_reason"])
			}
		}
	}
	if !foundDemo || !foundOpenAI {
		t.Fatalf("expected demo+openai connected, got %s", text)
	}
}

func TestListModelsCatalogFallback(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/models?provider=openai")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["provider"] != "openai" {
		t.Fatalf("provider=%v", body["provider"])
	}
	models, ok := body["models"].([]any)
	if !ok || len(models) == 0 {
		t.Fatalf("expected catalog models: %s", text)
	}
	first, _ := models[0].(map[string]any)
	if first["id"] == "" || first["source"] != "catalog" {
		t.Fatalf("first model=%v", first)
	}
	if body["default"] == "" {
		t.Fatalf("missing default: %s", text)
	}
	disc, _ := body["discovery"].(map[string]any)
	if disc == nil {
		t.Fatalf("missing discovery: %s", text)
	}
	// Live discovery disabled in tests.
	if disc["ok"] == true {
		t.Fatalf("expected no live discovery when REMEDY_LIVE_MODELS=0: %v", disc)
	}
}

func TestListModelsDeepseekCatalog(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/models?provider=deepseek")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	ids := []string{}
	for _, m := range body["models"].([]any) {
		row, _ := m.(map[string]any)
		ids = append(ids, fmtString(row["id"]))
	}
	joined := strings.Join(ids, ",")
	if !strings.Contains(joined, "deepseek-v4-flash") {
		t.Fatalf("expected deepseek catalog ids, got %v", ids)
	}
}

func TestListModelsRefusesStoredKeyToForeignURL(t *testing.T) {
	s, home := newProvidersTestServer(t)
	if err := secret.SetProviderSecret(home, "openai", "sk-test"); err != nil {
		t.Fatal(err)
	}
	code, body, text := doProvidersJSON(t, s, http.MethodGet,
		"/api/models?provider=openai&base_url=https://evil.example/v1")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["error"] == nil || body["error"] == "" {
		t.Fatalf("expected refusal error, got %s", text)
	}
	models, _ := body["models"].([]any)
	if len(models) != 0 {
		t.Fatalf("expected empty models on refusal, got %s", text)
	}
}

func TestProvidersRequireAuth(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/providers", nil)
	req.Host = "127.0.0.1:7400"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d body=%s", rr.Code, rr.Body.String())
	}
}

func TestOllamaDetectShape(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers/ollama/detect")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if _, ok := body["available"].(bool); !ok {
		t.Fatalf("missing available: %s", text)
	}
	if body["base_url"] == nil || body["base_url"] == "" {
		t.Fatalf("missing base_url: %s", text)
	}
	if _, ok := body["models"].([]any); !ok {
		// json may decode []string as []any
		if _, ok2 := body["models"].([]string); !ok2 {
			t.Fatalf("missing models: %s", text)
		}
	}
}

func doProvidersBody(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *strings.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = strings.NewReader(string(raw))
	} else {
		rdr = strings.NewReader("")
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+providersTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, string(raw)
}

func TestSaveAndDeleteCustomProvider(t *testing.T) {
	s, home := newProvidersTestServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/models" && r.URL.Path != "/v1/models" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{
				{"id": "qwen2.5", "name": "qwen2.5"},
				{"id": "emb", "name": "emb"},
			},
		})
	}))
	t.Cleanup(upstream.Close)
	base := strings.TrimRight(upstream.URL, "/") + "/v1"

	code, body, text := doProvidersBody(t, s, http.MethodPost, "/api/providers/custom", map[string]any{
		"name": "LM Studio", "base_url": base + "/", "api_key": "lm-secret",
	})
	if code != http.StatusOK {
		t.Fatalf("save status=%d body=%s", code, text)
	}
	if body["id"] != "custom-lm-studio" {
		t.Fatalf("id=%v body=%s", body["id"], text)
	}
	prov, _ := body["provider"].(map[string]any)
	if prov["user_defined"] != true || prov["name"] != "LM Studio" {
		t.Fatalf("provider=%v", prov)
	}
	if secret.GetProviderSecret(home, "custom-lm-studio") != "lm-secret" {
		t.Fatalf("key not stored under custom id")
	}
	if secret.GetProviderSecret(home, "custom") != "" {
		t.Fatalf("key must not land on template custom")
	}

	code, listed, text := doProvidersJSON(t, s, http.MethodGet, "/api/providers")
	if code != http.StatusOK {
		t.Fatalf("list status=%d body=%s", code, text)
	}
	found := false
	for _, p := range listed["providers"].([]any) {
		m, _ := p.(map[string]any)
		if m["id"] == "custom-lm-studio" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("saved provider missing from catalog: %s", text)
	}

	code, delBody, text := doProvidersBody(t, s, http.MethodDelete, "/api/providers/custom/custom-lm-studio", nil)
	if code != http.StatusOK {
		t.Fatalf("delete status=%d body=%s", code, text)
	}
	if delBody["ok"] != true {
		t.Fatalf("delete body=%s", text)
	}
	if secret.GetProviderSecret(home, "custom-lm-studio") != "" {
		t.Fatalf("key should be cleared")
	}
}

func TestSaveCustomProviderKeylessWhenHostDown(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	// Closed port — discovery fails fast.
	code, body, text := doProvidersBody(t, s, http.MethodPost, "/api/providers/custom", map[string]any{
		"name": "Home server", "base_url": "http://127.0.0.1:1/v1",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	prov, _ := body["provider"].(map[string]any)
	auth, _ := prov["auth"].([]any)
	if len(auth) != 1 || auth[0] != "none" {
		t.Fatalf("expected keyless auth, got %v body=%s", auth, text)
	}
	disc, _ := body["discovery"].(map[string]any)
	if disc["ok"] != false {
		t.Fatalf("expected discovery failure: %s", text)
	}
	if body["note"] == nil || body["note"] == "" {
		t.Fatalf("expected note from discovery error: %s", text)
	}
}

func TestCustomProviderBadRequests(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	code, _, _ := doProvidersBody(t, s, http.MethodPost, "/api/providers/custom", map[string]any{
		"name": "x", "base_url": "nope",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("bad url want 400 got %d", code)
	}
	code, _, _ = doProvidersBody(t, s, http.MethodPost, "/api/providers/custom", map[string]any{
		"id": "openai", "name": "x", "base_url": "http://a/v1",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("builtin id want 400 got %d", code)
	}
	code, _, _ = doProvidersBody(t, s, http.MethodDelete, "/api/providers/custom/openai", nil)
	if code != http.StatusNotFound {
		t.Fatalf("delete builtin want 404 got %d", code)
	}
}

func TestProbeProviderRefusesStoredKeyToForeignURL(t *testing.T) {
	s, home := newProvidersTestServer(t)
	if err := secret.SetProviderSecret(home, "openai", "sk-test"); err != nil {
		t.Fatal(err)
	}
	code, body, text := doProvidersBody(t, s, http.MethodPost, "/api/providers/probe", map[string]any{
		"provider": "openai", "base_url": "https://evil.example/v1",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != false {
		t.Fatalf("expected refusal: %s", text)
	}
	errMsg, _ := body["error"].(string)
	if !strings.Contains(errMsg, "Refused") {
		t.Fatalf("error=%q body=%s", errMsg, text)
	}
}

func TestProbeProviderLiveSuccess(t *testing.T) {
	s, _ := newProvidersTestServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": []map[string]any{{"id": "m1", "name": "Model One"}},
		})
	}))
	t.Cleanup(upstream.Close)
	code, body, text := doProvidersBody(t, s, http.MethodPost, "/api/providers/probe", map[string]any{
		"provider": "custom",
		"base_url": upstream.URL,
		"api_key":  "unused",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != true {
		t.Fatalf("probe failed: %s", text)
	}
	if int(body["models"].(float64)) < 1 {
		t.Fatalf("models=%v body=%s", body["models"], text)
	}
	list, _ := body["model_list"].([]any)
	if len(list) < 1 {
		t.Fatalf("model_list empty: %s", text)
	}
}
