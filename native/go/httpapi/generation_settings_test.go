package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

func TestGenerationOptionsForUnset(t *testing.T) {
	if got := generationOptionsFor(ConfigMap{"llm_model": "o3"}, "openai", "o3"); !got.IsZero() {
		t.Fatalf("unset config must give zero options, got %+v", got)
	}
}

func TestGenerationOptionsForPerModelOverridesGlobal(t *testing.T) {
	cfg := ConfigMap{
		"reasoning_effort":      "medium",
		"max_output_tokens":     int64(8000),
		"context_window_tokens": float64(64000),
		"model_settings": map[string]any{
			"o3":                      map[string]any{"reasoning_effort": "high"},
			"anthropic/claude-opus-4": map[string]any{"thinking_budget_tokens": "12000"},
		},
	}
	got := generationOptionsFor(cfg, "openai", "o3")
	want := providers.GenerationOptions{Effort: "high", MaxOutputTokens: 8000, ContextWindow: 64000}
	if got != want {
		t.Fatalf("o3 got %+v want %+v", got, want)
	}
	got = generationOptionsFor(cfg, "anthropic", "claude-opus-4")
	if got.ThinkingBudget != 12000 || got.Effort != "medium" {
		t.Fatalf("provider/model key: %+v", got)
	}
	got = generationOptionsFor(cfg, "xai", "grok-4")
	if got.Effort != "medium" || got.ThinkingBudget != 0 {
		t.Fatalf("fallback to global: %+v", got)
	}
}

func TestGenerationOptionsForIgnoresInvalid(t *testing.T) {
	cfg := ConfigMap{
		"reasoning_effort": "ludicrous",
		"model_settings":   map[string]any{"o3": map[string]any{"max_output_tokens": 5}},
	}
	if got := generationOptionsFor(cfg, "openai", "o3"); !got.IsZero() {
		t.Fatalf("invalid values must be ignored, got %+v", got)
	}
}

func TestValidateGenerationPatch(t *testing.T) {
	patch := map[string]any{"reasoning_effort": "MAX", "max_output_tokens": "4096"}
	if err := validateGenerationPatch(patch); err != nil {
		t.Fatal(err)
	}
	if patch["reasoning_effort"] != "xhigh" || patch["max_output_tokens"] != 4096 {
		t.Fatalf("normalized patch=%v", patch)
	}
	for _, bad := range []map[string]any{
		{"reasoning_effort": "warp"},
		{"thinking_budget_tokens": 3},
		{"context_window_tokens": 99_000_000},
		{"model_settings": "o3"},
		{"model_settings": map[string]any{"o3": map[string]any{"temperature": 1}}},
		{"model_settings": map[string]any{"o3": map[string]any{"max_output_tokens": 1}}},
	} {
		if err := validateGenerationPatch(bad); err == nil {
			t.Fatalf("expected error for %v", bad)
		}
	}
	clear := map[string]any{"reasoning_effort": "", "context_window_tokens": 0}
	if err := validateGenerationPatch(clear); err != nil || clear["reasoning_effort"] != "" {
		t.Fatalf("clearing must be allowed: %v %v", clear, err)
	}
}

func TestMergeModelSettingsKeepsOtherModels(t *testing.T) {
	cfg := ConfigMap{"model_settings": map[string]any{
		"o3":     map[string]any{"reasoning_effort": "high"},
		"grok-4": map[string]any{"max_output_tokens": 4096},
	}}
	patch := ConfigMap{"model_settings": map[string]any{
		"o3":             map[string]any{"reasoning_effort": "", "max_output_tokens": 0},
		"gemini-2.5-pro": map[string]any{"thinking_budget_tokens": 2048},
	}}
	if err := validateGenerationPatch(patch); err != nil {
		t.Fatal(err)
	}
	mergeModelSettings(patch, cfg)
	table := patch["model_settings"].(map[string]any)
	if _, ok := table["o3"]; ok {
		t.Fatalf("all-unset entry must be removed: %v", table)
	}
	if table["grok-4"] == nil || table["gemini-2.5-pro"] == nil {
		t.Fatalf("other models must survive: %v", table)
	}
}

func TestTryLiveModelCarriesGeneration(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-abcdef")
	t.Setenv("REMEDY_LLM_BASE_URL", "")
	cfg := ConfigMap{
		"llm_provider":     "openai",
		"llm_model":        "o3",
		"reasoning_effort": "low",
	}
	m := tryLiveModel(t.TempDir(), cfg, "openai", "o3", "")
	oc, ok := m.(*providers.OpenAICompat)
	if !ok {
		t.Fatalf("model type %T", m)
	}
	if oc.Family != providers.FamilyOpenAI || oc.Gen.Effort != "low" {
		t.Fatalf("family=%q gen=%+v", oc.Family, oc.Gen)
	}
}

func TestSettingsPutRoundTripsGenerationKeys(t *testing.T) {
	home := t.TempDir()
	const token = "settings-token-not-sec!"
	t.Setenv("REMEDY_HOME", home)
	base, shutdown := startTestServer(t, Config{Token: token, HomeDir: home})
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	do := func(method, body string) (int, map[string]any) {
		req, err := http.NewRequest(method, base+"/api/settings", strings.NewReader(body))
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
		var out map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&out)
		return resp.StatusCode, out
	}
	code, first := do(http.MethodPut, `{"reasoning_effort":"high","context_window_tokens":64000,
		"model_settings":{"anthropic/claude-sonnet-4-5":{"thinking_budget_tokens":6000}}}`)
	if code != http.StatusOK {
		t.Fatalf("put status %d %v", code, first)
	}
	code, _ = do(http.MethodPut, `{"model_settings":{"o3":{"max_output_tokens":8000}}}`)
	if code != http.StatusOK {
		t.Fatalf("second put status %d", code)
	}
	if code, _ := do(http.MethodPut, `{"max_output_tokens":3}`); code == http.StatusOK {
		t.Fatalf("out-of-range value must be refused")
	}
	InvalidateConfigCache()
	_, got := do(http.MethodGet, "")
	if got["reasoning_effort"] != "high" || got["context_window_tokens"] != float64(64000) {
		t.Fatalf("settings view: effort=%v ctx=%v first=%v", got["reasoning_effort"], got["context_window_tokens"], first)
	}
	table, _ := got["model_settings"].(map[string]any)
	if len(table) != 2 || table["o3"] == nil || table["anthropic/claude-sonnet-4-5"] == nil {
		t.Fatalf("model_settings=%v", got["model_settings"])
	}
	opts := generationOptionsFor(LoadConfig(home), "anthropic", "claude-sonnet-4-5")
	if opts.ThinkingBudget != 6000 || opts.Effort != "high" || opts.ContextWindow != 64000 {
		t.Fatalf("resolved options from saved config: %+v", opts)
	}
}
