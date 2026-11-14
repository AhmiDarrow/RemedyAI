package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWriteConfigQuotesNonBareKeys(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.toml")
	cfg := ConfigMap{
		"llm_model": "gemini-2.5-pro",
		"model_settings": map[string]any{
			"gemini-2.5-pro":              map[string]any{"thinking_budget_tokens": 2048},
			"anthropic/claude-sonnet-4-5": map[string]any{"reasoning_effort": "high"},
		},
		"last_model_by_provider": map[string]any{"openai": "gpt-4.1"},
	}
	if err := WriteConfig(path, cfg); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	got, err := parseTOML(raw)
	if err != nil {
		t.Fatalf("written config must parse: %v", err)
	}
	table := asMap(got["model_settings"])
	if asMap(table["gemini-2.5-pro"]) == nil || asMap(table["anthropic/claude-sonnet-4-5"]) == nil {
		t.Fatalf("model ids must survive as single keys: %v", table)
	}
	if tomlKey("plain_key-1") != "plain_key-1" || tomlKey("a.b") != `"a.b"` {
		t.Fatal("tomlKey quoting")
	}
}
