package httpapi

import (
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// The rules the Python settings tests pinned (tests/test_settings_normalisers.py,
// test_settings_apply_llm_patch.py, test_generation_settings.py,
// test_settings_version.py), checked on the Go apply path the CLI now uses.

func cliTestHome(t *testing.T) string {
	t.Helper()
	home := filepath.Join(t.TempDir(), "home")
	if err := os.MkdirAll(home, 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	return home
}

func cliConfigText(t *testing.T, home string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(home, "config.toml"))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestCLINormalizeToolProcess(t *testing.T) {
	for _, raw := range []any{"off", "OFF", "", nil, "nonsense", 0, false} {
		if got := normalizeToolProcess(nil, raw); got != "off" {
			t.Errorf("%#v → %q, want off", raw, got)
		}
	}
	for _, raw := range []any{"medium", "MEDIUM", " med "} {
		if got := normalizeToolProcess(nil, raw); got != "medium" {
			t.Errorf("%#v → %q, want medium", raw, got)
		}
	}
	for _, raw := range []any{"full", "full+", "fullplus", "full_plus", "debug", "on", "true", "1", "yes", true} {
		if got := normalizeToolProcess(nil, raw); got != "full" {
			t.Errorf("%#v → %q, want full", raw, got)
		}
	}
	if normalizeToolProcess(ConfigMap{"tool_process": "medium"}, nil) != "medium" ||
		normalizeToolProcess(ConfigMap{"show_tool_calls": true}, nil) != "full" ||
		normalizeToolProcess(ConfigMap{"tool_process": "off", "show_tool_calls": true}, nil) != "off" ||
		normalizeToolProcess(ConfigMap{"show_tool_calls": false}, nil) != "off" {
		t.Fatal("config / legacy flag handling")
	}
}

func TestCLINormalizeBrowserHomeURL(t *testing.T) {
	for _, u := range []string{
		"javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<script>x</script>",
		"vbscript:msgbox(1)", "file:///C:/Users/me/.remedy/auth", "about:config", "", "   ",
	} {
		if got := normalizeBrowserHomeURL(u); got != defaultBrowserHomeURL {
			t.Errorf("%q → %q", u, got)
		}
	}
	for in, want := range map[string]string{
		"https://example.com/start": "https://example.com/start",
		"http://192.168.0.10:8080":  "http://192.168.0.10:8080",
		"example.com":               "https://example.com",
		"example.com/dash":          "https://example.com/dash",
		"  example.com  ":           "https://example.com",
	} {
		if got := normalizeBrowserHomeURL(in); got != want {
			t.Errorf("%q → %q, want %q", in, got, want)
		}
	}
	if !strings.HasPrefix(defaultBrowserHomeURL, "https://") {
		t.Fatal("default must be https")
	}
}

func TestCLICoerceBoolWords(t *testing.T) {
	for _, v := range []any{true, "1", "true", "TRUE", " yes ", "on", "enable", "enabled"} {
		if !coerceBool(v, false) {
			t.Errorf("%#v should be true", v)
		}
	}
	for _, v := range []any{false, "0", "false", "no", "off", "disable", "disabled"} {
		if coerceBool(v, true) {
			t.Errorf("%#v should be false", v)
		}
	}
}

// Through the apply path the CLI uses: words land as the values the rest of
// the app trusts.
func TestCLIApplySettingsNormalisesWords(t *testing.T) {
	home := cliTestHome(t)
	_, err := CLIApplySettings(home, map[string]any{
		"tool_process": "full+", "browser_home_url": "javascript:alert(1)",
		"privacy_mode": "on", "thinking_level": "LOW", "approval_mode": "confirm",
	})
	if err != nil {
		t.Fatal(err)
	}
	text := cliConfigText(t, home)
	for _, want := range []string{
		`tool_process = "full"`, `browser_home_url = "` + defaultBrowserHomeURL + `"`,
		"privacy_mode = true", `thinking_level = "low"`, `approval_mode = "ask"`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("config lacks %q:\n%s", want, text)
		}
	}
}

func TestCLIApplySettingsRefusals(t *testing.T) {
	home := cliTestHome(t)
	for _, patch := range []map[string]any{
		{},
		{"nonsense": 1},
		{"thinking_budget_tokens": 10},
		{"max_output_tokens": "lots"},
		{"context_window_tokens": 99_000_000},
		{"max_output_tokens": true},
		{"model_settings": "o3"},
		{"model_settings": map[string]any{"o3": map[string]any{"temperature": 1}}},
		{"model_settings": map[string]any{"o3": map[string]any{"reasoning_effort": "warp"}}},
		{"model_settings": map[string]any{"o3": map[string]any{"max_output_tokens": "x"}}},
		{"max_output_tokens": int64(3)},
	} {
		if _, err := CLIApplySettings(home, patch); !errors.Is(err, ErrSettingsRejected) {
			t.Fatalf("%v: want rejected, got %v", patch, err)
		}
	}
	if _, err := os.Stat(filepath.Join(home, "config.toml")); err == nil {
		t.Fatal("a refused patch wrote config.toml")
	}
}

func TestCLIValidateGenerationPatchRefusesNonNumbers(t *testing.T) {
	for _, bad := range []map[string]any{
		{"max_output_tokens": "lots"},
		{"max_output_tokens": true},
		{"thinking_budget_tokens": []any{1}},
		{"model_settings": map[string]any{"o3": map[string]any{"context_window_tokens": "big"}}},
	} {
		if err := validateGenerationPatch(bad); err == nil || !strings.Contains(err.Error(), "must be a number") {
			t.Fatalf("%v: %v", bad, err)
		}
	}
	ok := map[string]any{"max_output_tokens": "8000", "thinking_budget_tokens": "", "context_window_tokens": int64(64000)}
	if err := validateGenerationPatch(ok); err != nil || ok["max_output_tokens"] != 8000 {
		t.Fatalf("valid patch: %v %v", ok, err)
	}
}

func TestCLIApplySettingsWritesGenerationKeys(t *testing.T) {
	home := cliTestHome(t)
	_, err := CLIApplySettings(home, map[string]any{
		"reasoning_effort":      "low",
		"context_window_tokens": int64(64000),
		"model_settings": map[string]any{
			"anthropic/claude-sonnet-4-5": map[string]any{"thinking_budget_tokens": int64(6000)},
			"gemini-2.5-pro":              map[string]any{"reasoning_effort": "low"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	snap := CLISettingsSnapshot(home, "")
	if snap["reasoning_effort"] != "low" || snap["context_window_tokens"] != 64000 {
		t.Fatalf("snapshot: effort=%v ctx=%v", snap["reasoning_effort"], snap["context_window_tokens"])
	}
	ms, _ := snap["model_settings"].(map[string]any)
	if len(ms) != 2 || ms["gemini-2.5-pro"] == nil || ms["anthropic/claude-sonnet-4-5"] == nil {
		t.Fatalf("model_settings: %v", ms)
	}
}

func TestCLIApplySettingsLeavesTheLLMAloneOnAnUnrelatedPatch(t *testing.T) {
	home := cliTestHome(t)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(
		"llm_provider = \"deepseek\"\nllm_model = \"deepseek-v4-flash\"\nllm_base_url = \"https://api.deepseek.com/v1\"\nproject_path = \".\"\nharness_mode = \"auto\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	out, err := CLIApplySettings(home, map[string]any{"harness_mode": "manual", "sarcasm_mode": true})
	if err != nil || out["status"] != "saved" {
		t.Fatalf("%v %v", out, err)
	}
	text := cliConfigText(t, home)
	if !strings.Contains(text, "deepseek-v4-flash") || !strings.Contains(text, `llm_provider = "deepseek"`) {
		t.Fatalf("LLM rewritten:\n%s", text)
	}
	for _, ch := range out["changes"].([]string) {
		if ch == "llm_model" || ch == "llm_provider" {
			t.Fatalf("changes claim %s", ch)
		}
	}
}

// A Claude Code / Max login token is refused by the settings path before
// anything is written, and a console key is filed under the provider that
// issued it (Python config.set_provider_key).
func TestCLIApplySettingsRefusesSubscriptionTokensAndFilesKeysByOwner(t *testing.T) {
	home := cliTestHome(t)
	_, err := CLIApplySettings(home, map[string]any{"llm_provider": "anthropic", "llm_api_key": "sk-ant-oat01-secret", "sarcasm_mode": true})
	if !errors.Is(err, ErrSettingsRejected) || !strings.Contains(err.Error(), "Claude Code / Max login token") {
		t.Fatalf("want refusal, got %v", err)
	}
	if keys := secret.LoadProviderKeys(home); len(keys) != 0 {
		t.Fatalf("token stored: %v", keys)
	}
	if _, err := os.Stat(filepath.Join(home, "config.toml")); err == nil {
		t.Fatal("refused patch still wrote config.toml")
	}
	if _, err := CLIApplySettings(home, map[string]any{"llm_provider": "xai", "llm_api_key": "gsk_groq"}); err != nil {
		t.Fatal(err)
	}
	if keys := secret.LoadProviderKeys(home); keys["groq"] != "gsk_groq" || keys["xai"] != "" {
		t.Fatalf("filed wrong: %v", keys)
	}
	owner, err := CLIStoreProviderKey(home, "openai", "sk-ant-sid01-session")
	if !errors.Is(err, ErrSettingsRejected) || owner != "" {
		t.Fatalf("session token: %q %v", owner, err)
	}
}

func TestCLISettingsSnapshotVersionAndGender(t *testing.T) {
	home := cliTestHome(t)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(
		"setup_completed = true\nllm_provider = \"demo\"\nagent_gender = \"neutral\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	snap := CLISettingsSnapshot(home, "")
	if snap["agent_gender"] != "neutral" || snap["version"] != Version || Version == "0.9.0" {
		t.Fatalf("gender=%v version=%v", snap["agent_gender"], snap["version"])
	}
	if CLISettingsSnapshot(home, "9.9.9")["version"] != "9.9.9" {
		t.Fatal("launcher version not used")
	}
	for k := range snap {
		if strings.Contains(strings.ToLower(k), "api_key") && k != "llm_api_key_set" {
			t.Fatalf("snapshot exposes %s", k)
		}
	}
}

func TestCLIResolveConfig(t *testing.T) {
	home := cliTestHome(t)
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("name = \"Remi\"\n[log]\nformat = \"json\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	env := []string{"REMEDY_LOG__LEVEL=DEBUG", "REMEDY_MAX_ITER=12", "REMEDY_FLAG=yes", "REMEDY_RATIO=0.5",
		"REMEDY_CLI_VERSION=1.2.3", "OTHER=x"}
	cfg := CLIResolveConfig(home, env)
	if cfg["name"] != "Remi" || cfg["max_iter"] != int64(12) || cfg["flag"] != true || cfg["ratio"] != 0.5 || cfg["home_dir"] != home {
		t.Fatalf("overrides: %v", cfg)
	}
	if !reflect.DeepEqual(cfg["log"], map[string]any{"format": "json", "level": "DEBUG"}) {
		t.Fatalf("nested: %v", cfg["log"])
	}
	if _, ok := cfg["cli_version"]; ok {
		t.Fatal("launcher plumbing leaked into config")
	}
	if cfg["llm_provider"] != "demo" {
		t.Fatalf("clean config should bootstrap demo, got %v", cfg["llm_provider"])
	}
	if got := CLIResolveConfig(home, []string{"XAI_API_KEY=xai-k", "GROQ_API_KEY=g"}); got["llm_provider"] != "xai" {
		t.Fatalf("xai first: %v", got["llm_provider"])
	}
	if got := CLIResolveConfig(home, []string{"GROQ_API_KEY=g"}); got["llm_provider"] != "groq" {
		t.Fatalf("groq: %v", got["llm_provider"])
	}
	if got := CLIResolveConfig(home, []string{"REMEDY_DEMO_DISABLED=1"}); got["llm_provider"] != nil {
		t.Fatalf("demo disabled: %v", got["llm_provider"])
	}
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("llm_provider = \"deepseek\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	if got := CLIResolveConfig(home, []string{"XAI_API_KEY=xai-k"}); got["llm_provider"] != "deepseek" {
		t.Fatalf("a chosen provider must stay: %v", got["llm_provider"])
	}
}

func TestCLIXaiStatusNeverShowsTheKey(t *testing.T) {
	home := cliTestHome(t)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	if ok, err := CLIXaiSaveAPIKey(home, "xai-secret-value"); err != nil || !ok {
		t.Fatalf("%v %v", ok, err)
	}
	var names []string
	for _, f := range CLIXaiStatus(home) {
		names = append(names, f.Name)
		if s, _ := f.Value.(string); strings.Contains(s, "xai-secret") {
			t.Fatal("key in status")
		}
	}
	if !reflect.DeepEqual(names[:6], []string{"provider", "auth_method", "connected", "has_api_key", "has_oauth", "expires_at"}) {
		t.Fatalf("order: %v", names)
	}
	CLIXaiClear(home)
	for _, f := range CLIXaiStatus(home) {
		if f.Name == "has_api_key" && f.Value != false {
			t.Fatal("clear left the key")
		}
	}
}
