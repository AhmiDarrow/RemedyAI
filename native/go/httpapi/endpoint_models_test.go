package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// fakeModelsEndpoint serves an OpenAI-style /models listing.
func fakeModelsEndpoint(t *testing.T, ids ...string) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/models") {
			http.NotFound(w, r)
			return
		}
		rows := make([]string, 0, len(ids))
		for _, id := range ids {
			rows = append(rows, `{"id":"`+id+`","object":"model"}`)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"object":"list","data":[` + strings.Join(rows, ",") + `]}`))
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/v1"
}

func useEndpointForProvider(t *testing.T, home, provider, base string) {
	t.Helper()
	t.Setenv("REMEDY_LIVE_MODELS", "1")
	if err := WriteConfig(DefaultConfigPath(home), ConfigMap{
		"llm_provider": provider,
		"llm_base_url": base,
	}); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
}

func TestBuiltinCatalogBakesInNoModels(t *testing.T) {
	for _, pid := range providerCatalogOrder {
		if n := len(providerCatalog[pid].Models); n != 0 {
			t.Fatalf("%s bakes in %d models; models must come from the provider's endpoint", pid, n)
		}
		if m := defaultModelForProvider(pid); m != "" {
			t.Fatalf("%s bakes in default model %q", pid, m)
		}
	}
}

func TestListModelsNeverFallsBackToABakedInList(t *testing.T) {
	s, _ := newProvidersTestServer(t) // live listing off: endpoint unreachable
	for _, pid := range []string{"openai", "deepseek", "xai", "anthropic", "demo"} {
		_, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/models?provider="+pid)
		for _, m := range body["models"].([]any) {
			row, _ := m.(map[string]any)
			if row["source"] == "catalog" {
				t.Fatalf("%s listed a baked-in model: %s", pid, text)
			}
		}
	}
}

func TestListModelsComeFromTheEndpoint(t *testing.T) {
	s, home := newProvidersTestServer(t)
	base := fakeModelsEndpoint(t, "deepseek-v9", "deepseek-v9-pro")
	useEndpointForProvider(t, home, "deepseek", base)

	_, body, text := doProvidersJSON(t, s, http.MethodGet, "/api/models?provider=deepseek")
	var ids []string
	for _, m := range body["models"].([]any) {
		row, _ := m.(map[string]any)
		if row["source"] != "endpoint" {
			t.Fatalf("non-endpoint row: %s", text)
		}
		ids = append(ids, fmtString(row["id"]))
	}
	if strings.Join(ids, ",") != "deepseek-v9,deepseek-v9-pro" {
		t.Fatalf("ids=%v body=%s", ids, text)
	}
	if body["default"] != "deepseek-v9" {
		t.Fatalf("default=%v (first endpoint model)", body["default"])
	}
}

func TestPickEndpointModelPrefersLastPickStillOffered(t *testing.T) {
	t.Setenv("REMEDY_LIVE_MODELS", "1")
	base := fakeModelsEndpoint(t, "m-new", "m-old")
	cfg := ConfigMap{"last_model_by_provider": map[string]any{"custom": "m-old"}}
	if got := pickEndpointModel(cfg, "custom", base, ""); got != "m-old" {
		t.Fatalf("got %q, want the last pick", got)
	}
	cfg = ConfigMap{"last_model_by_provider": map[string]any{"custom": "m-retired"}}
	if got := pickEndpointModel(cfg, "custom", base+"/", ""); got != "m-new" {
		t.Fatalf("got %q, want the endpoint's first model when the last pick is gone", got)
	}
	t.Setenv("REMEDY_LIVE_MODELS", "0")
	if got := pickEndpointModel(cfg, "custom", base+"//", ""); got != "" {
		t.Fatalf("listing off must not invent a model, got %q", got)
	}
}

func TestSetSessionLLMChecksTheEndpointList(t *testing.T) {
	s, home := newSessionLLMTestServer(t)
	base := fakeModelsEndpoint(t, "deepseek-v9")
	useEndpointForProvider(t, home, "deepseek", base)
	sid := createTestSession(t, s)

	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "deepseek", "model": "not-offered-zzz",
	})
	if code != http.StatusBadRequest || !strings.Contains(fmtString(body["detail"]), "not offered") {
		t.Fatalf("status=%d body=%s", code, text)
	}
	code, _, text = doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "deepseek", "model": "deepseek-v9",
	})
	if code != http.StatusOK {
		t.Fatalf("offered model refused: status=%d body=%s", code, text)
	}
}

func TestSetSessionLLMAcceptsAnyModelWhenEndpointUnlisted(t *testing.T) {
	s, _ := newSessionLLMTestServer(t)
	t.Setenv("REMEDY_LIVE_MODELS", "0")
	sid := createTestSession(t, s)
	code, _, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "openai", "model": "gpt-from-the-future",
	})
	if code != http.StatusOK {
		t.Fatalf("a brand-new model id must not be refused by a baked-in list: %d %s", code, text)
	}
}
