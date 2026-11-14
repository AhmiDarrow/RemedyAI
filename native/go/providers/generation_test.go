package providers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
)

func TestGenerationOptionsValidate(t *testing.T) {
	o, err := GenerationOptions{Effort: " High "}.Validate()
	if err != nil || o.Effort != "high" {
		t.Fatalf("effort normalize: %+v %v", o, err)
	}
	for raw, want := range map[string]string{"default": "", "off": "none", "max": "xhigh", "": ""} {
		if got, err := NormalizeEffort(raw); err != nil || got != want {
			t.Fatalf("NormalizeEffort(%q)=%q,%v want %q", raw, got, err, want)
		}
	}
	bad := []GenerationOptions{
		{Effort: "extreme"},
		{ThinkingBudget: 10},
		{ThinkingBudget: MaxThinkingBudget + 1},
		{MaxOutputTokens: 1},
		{ContextWindow: 100},
		{ContextWindow: -5},
	}
	for _, b := range bad {
		if _, err := b.Validate(); err == nil {
			t.Fatalf("expected error for %+v", b)
		}
	}
	if _, err := (GenerationOptions{}).Validate(); err != nil {
		t.Fatalf("zero options must validate: %v", err)
	}
}

func TestGenerationMerge(t *testing.T) {
	got := GenerationOptions{Effort: "low"}.Merge(GenerationOptions{Effort: "high", MaxOutputTokens: 4096})
	if got != (GenerationOptions{Effort: "low", MaxOutputTokens: 4096}) {
		t.Fatalf("merge=%+v", got)
	}
}

func applied(family, model string, o GenerationOptions) map[string]any {
	p := map[string]any{"model": model}
	ApplyOpenAICompatGeneration(p, family, model, o)
	delete(p, "model")
	return p
}

func TestOpenAICompatGenerationPayloads(t *testing.T) {
	full := GenerationOptions{Effort: "medium", ThinkingBudget: 4096, MaxOutputTokens: 8000}
	cases := []struct {
		name, family, model string
		o                   GenerationOptions
		want                map[string]any
	}{
		{"openai reasoning", FamilyOpenAI, "o3", full,
			map[string]any{"reasoning_effort": "medium", "max_completion_tokens": 8000}},
		{"openai gpt-5 minimal", FamilyOpenAI, "gpt-5", GenerationOptions{Effort: "none"},
			map[string]any{"reasoning_effort": "minimal"}},
		{"openai o4-mini xhigh caps at high", FamilyOpenAI, "o4-mini", GenerationOptions{Effort: "xhigh"},
			map[string]any{"reasoning_effort": "high"}},
		{"openai chat model omits effort", FamilyOpenAI, "gpt-4o", full,
			map[string]any{"max_tokens": 8000}},
		{"xai grok-3-mini", FamilyXAI, "grok-3-mini", GenerationOptions{Effort: "medium"},
			map[string]any{"reasoning_effort": "high"}},
		{"xai grok-3-mini low", FamilyXAI, "grok-3-mini-fast", GenerationOptions{Effort: "minimal"},
			map[string]any{"reasoning_effort": "low"}},
		{"xai grok-4 omits effort", FamilyXAI, "grok-4", full,
			map[string]any{"max_tokens": 8000}},
		{"gemini budget", FamilyGoogle, "gemini-2.5-pro", full,
			map[string]any{
				"extra_body": map[string]any{"google": map[string]any{
					"thinking_config": map[string]any{"thinking_budget": 4096}}},
				"max_tokens": 8000}},
		{"gemini effort", FamilyGoogle, "models/gemini-2.5-flash", GenerationOptions{Effort: "none"},
			map[string]any{"reasoning_effort": "none"}},
		{"gemini 2.0 omits thinking", FamilyGoogle, "gemini-2.0-flash", full,
			map[string]any{"max_tokens": 8000}},
		{"other provider only max tokens", "", "llama3.1", full,
			map[string]any{"max_tokens": 8000}},
		{"unset adds nothing (openai)", FamilyOpenAI, "o3", GenerationOptions{}, map[string]any{}},
		{"unset adds nothing (google)", FamilyGoogle, "gemini-2.5-pro", GenerationOptions{}, map[string]any{}},
	}
	for _, c := range cases {
		got := applied(c.family, c.model, c.o)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

// End to end: the request body OpenAICompat actually sends.
func capturePayload(t *testing.T, m *OpenAICompat) map[string]any {
	t.Helper()
	var body map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Errorf("bad body: %v", err)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n"))
	}))
	defer srv.Close()
	m.BaseURL = srv.URL + "/v1"
	m.APIKey = "sk-test-not-a-real-key-abcdef"
	m.HTTPClient = srv.Client()
	ch, err := m.Stream(context.Background(), userTurn("hi"))
	if err != nil {
		t.Fatal(err)
	}
	for range ch {
	}
	return body
}

func TestOpenAICompatSendsGenerationFields(t *testing.T) {
	body := capturePayload(t, &OpenAICompat{Model: "o3", Family: FamilyOpenAI,
		Gen: GenerationOptions{Effort: "low", MaxOutputTokens: 2048}})
	if body["reasoning_effort"] != "low" || body["max_completion_tokens"] != float64(2048) {
		t.Fatalf("payload=%v", body)
	}
}

func TestOpenAICompatUnsetKeepsPayload(t *testing.T) {
	body := capturePayload(t, &OpenAICompat{Model: "o3", Family: FamilyOpenAI})
	for _, k := range []string{"reasoning_effort", "max_completion_tokens", "max_tokens", "extra_body"} {
		if _, ok := body[k]; ok {
			t.Fatalf("unset options must not add %s: %v", k, body)
		}
	}
}

func TestOpenAICompatContextWindowOverride(t *testing.T) {
	if got := (&OpenAICompat{Model: "gpt-4o", Gen: GenerationOptions{ContextWindow: 50_000}}).ContextWindow(); got != 50_000 {
		t.Fatalf("override=%d", got)
	}
	// A local server's real n_ctx is a hard ceiling.
	if got := (&OpenAICompat{NCtx: 8192, Gen: GenerationOptions{ContextWindow: 50_000}}).ContextWindow(); got != 8192 {
		t.Fatalf("local cap=%d", got)
	}
	if got := (&OpenAICompat{NCtx: 8192, Gen: GenerationOptions{ContextWindow: 4096}}).ContextWindow(); got != 4096 {
		t.Fatalf("local shrink=%d", got)
	}
}

func anthropicPayload(t *testing.T, a *Anthropic) map[string]any {
	t.Helper()
	raw, err := json.Marshal(a.buildParams(userTurn("hi")))
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestAnthropicBudgetThinkingPayload(t *testing.T) {
	p := anthropicPayload(t, &Anthropic{Model: "claude-sonnet-4-5", Gen: GenerationOptions{ThinkingBudget: 6000}})
	th, _ := p["thinking"].(map[string]any)
	if th["type"] != "enabled" || th["budget_tokens"] != float64(6000) {
		t.Fatalf("thinking=%v", p["thinking"])
	}
	if p["max_tokens"].(float64) <= 6000 {
		t.Fatalf("max_tokens must exceed budget: %v", p["max_tokens"])
	}

	// Effort maps to a budget; a large budget grows the default ceiling.
	p = anthropicPayload(t, &Anthropic{Model: "claude-opus-4-1", Gen: GenerationOptions{Effort: "xhigh"}})
	th, _ = p["thinking"].(map[string]any)
	if th["budget_tokens"] != float64(32000) || p["max_tokens"].(float64) <= 32000 {
		t.Fatalf("xhigh payload thinking=%v max=%v", th, p["max_tokens"])
	}

	// An explicit max_output_tokens wins and the budget shrinks under it.
	p = anthropicPayload(t, &Anthropic{Model: "claude-opus-4-1",
		Gen: GenerationOptions{ThinkingBudget: 20000, MaxOutputTokens: 10000}})
	th, _ = p["thinking"].(map[string]any)
	if p["max_tokens"] != float64(10000) || th["budget_tokens"] != float64(9999) {
		t.Fatalf("capped payload thinking=%v max=%v", th, p["max_tokens"])
	}

	// effort none: no thinking block.
	p = anthropicPayload(t, &Anthropic{Model: "claude-sonnet-4-5", Gen: GenerationOptions{Effort: "none"}})
	if _, ok := p["thinking"]; ok {
		t.Fatalf("effort none must not think: %v", p["thinking"])
	}
}

func TestAnthropicUnsetKeepsPayload(t *testing.T) {
	for _, model := range []string{"claude-sonnet-4-5", "claude-3-5-haiku-latest", "claude-opus-4-6"} {
		before := anthropicPayload(t, &Anthropic{Model: model})
		after := anthropicPayload(t, &Anthropic{Model: model, Gen: GenerationOptions{}})
		if !reflect.DeepEqual(before, after) {
			t.Fatalf("%s: unset changed payload", model)
		}
		if model != "claude-opus-4-6" {
			if _, ok := before["thinking"]; ok {
				t.Fatalf("%s: unset must not add budget thinking: %v", model, before["thinking"])
			}
		}
	}
	// Old models never get thinking even when asked.
	p := anthropicPayload(t, &Anthropic{Model: "claude-3-5-haiku-latest", Gen: GenerationOptions{Effort: "high"}})
	if _, ok := p["thinking"]; ok {
		t.Fatalf("3.5 must not think: %v", p["thinking"])
	}
}

func TestAnthropicAdaptiveEffortOverride(t *testing.T) {
	model := "claude-opus-4-6"
	if !supportsAdaptiveThinking(model) {
		t.Skip("model naming changed")
	}
	p := anthropicPayload(t, &Anthropic{Model: model, Gen: GenerationOptions{Effort: "low", MaxOutputTokens: 4000}})
	oc, _ := p["output_config"].(map[string]any)
	if oc["effort"] != "low" || p["max_tokens"] != float64(4000) {
		t.Fatalf("adaptive payload output_config=%v max=%v", p["output_config"], p["max_tokens"])
	}
	if th, _ := p["thinking"].(map[string]any); th["type"] != "adaptive" {
		t.Fatalf("adaptive models keep adaptive thinking: %v", p["thinking"])
	}
	p = anthropicPayload(t, &Anthropic{Model: model, Gen: GenerationOptions{Effort: "none"}})
	if oc, _ := p["output_config"].(map[string]any); oc["effort"] != "low" {
		t.Fatalf("none on adaptive maps to low: %v", p["output_config"])
	}
}

func TestAnthropicContextWindowOverride(t *testing.T) {
	if got := (&Anthropic{Model: "claude-sonnet-4-5", Gen: GenerationOptions{ContextWindow: 100_000}}).ContextWindow(); got != 100_000 {
		t.Fatalf("override=%d", got)
	}
}
