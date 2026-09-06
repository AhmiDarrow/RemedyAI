package httpapi

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func newSettingsToolsServer(t *testing.T) (*Server, *tools.Registry) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	cfgPath := filepath.Join(home, "config.toml")
	cfg := "" +
		"approval_mode = \"ask\"\n" +
		"web_tools_enabled = true\n" +
		"user_name = \"Owner\"\n" +
		"enabled_channels = [\"cli\", \"telegram\"]\n" +
		"llm_api_key = \"sk-should-never-echo-this-value\"\n"
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	if err := secret.SetProviderSecret(home, "openai", "sk-live-provider-secret-not-for-tools"); err != nil {
		t.Fatal(err)
	}
	runner := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	s, err := New(Config{
		HomeDir:    home,
		Token:      "settings-tools-token-not-sec",
		DBPath:     filepath.Join(home, "memory.db"),
		Version:    "0.60.0-test",
		TurnRunner: runner,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	reg := runner.Registry
	if _, err := reg.Latest("settings.get"); err != nil {
		t.Fatalf("settings.get not attached: %v", err)
	}
	if _, err := reg.Latest("settings.patch"); err != nil {
		t.Fatalf("settings.patch not attached: %v", err)
	}
	return s, reg
}

func execSettingsTool(t *testing.T, reg *tools.Registry, id string, input string) map[string]any {
	t.Helper()
	desc, err := reg.Latest(id)
	if err != nil {
		t.Fatal(err)
	}
	out, err := reg.Execute(context.Background(), tools.Request{
		ToolID:          id,
		Version:         1,
		Input:           json.RawMessage(input),
		CapabilityToken: RuntimeCapabilityToken(desc),
	})
	if err != nil {
		t.Fatalf("%s execute: %v", id, err)
	}
	var body map[string]any
	if err := json.Unmarshal(out.Output, &body); err != nil {
		t.Fatalf("%s decode: %v raw=%s", id, err, out.Output)
	}
	raw := string(out.Output)
	for _, leak := range []string{
		"sk-should-never-echo-this-value",
		"sk-live-provider-secret-not-for-tools",
	} {
		if strings.Contains(raw, leak) {
			t.Fatalf("%s leaked secret material %q in %s", id, leak, raw)
		}
	}
	if strings.Contains(raw, "sk-") {
		t.Fatalf("%s leaked sk- shaped secret in %s", id, raw)
	}
	return body
}

func TestSettingsGetScrubbedNoSecrets(t *testing.T) {
	_, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.get", `{}`)
	if body["ok"] != true {
		t.Fatalf("get=%v", body)
	}
	settings, _ := body["settings"].(map[string]any)
	if settings["approval_mode"] != "ask" {
		t.Fatalf("approval_mode=%v", settings["approval_mode"])
	}
	if settings["user_name"] != "Owner" {
		t.Fatalf("user_name=%v", settings["user_name"])
	}
	if _, ok := settings["llm_api_key"]; ok {
		t.Fatalf("llm_api_key must not appear: %v", settings)
	}
	if settings["llm_api_key_set"] != true && settings["llm_api_key_set"] != false {
		// May be true because we seeded a provider secret; either way boolean only.
		t.Fatalf("llm_api_key_set want bool got %T %v", settings["llm_api_key_set"], settings["llm_api_key_set"])
	}
	keys, _ := body["allowed_keys"].([]any)
	if len(keys) < 5 {
		t.Fatalf("allowed_keys=%v", keys)
	}
}

func TestSettingsGetKeysFilter(t *testing.T) {
	_, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.get", `{"keys":["approval_mode","llm_api_key"]}`)
	settings, _ := body["settings"].(map[string]any)
	if len(settings) != 1 || settings["approval_mode"] != "ask" {
		t.Fatalf("filtered=%v", settings)
	}
}

func TestSettingsPatchSafeFields(t *testing.T) {
	s, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.patch", `{"patch":{"approval_mode":"auto","web_tools_enabled":false}}`)
	if body["ok"] != true {
		t.Fatalf("patch=%v", body)
	}
	changes, _ := body["changes"].([]any)
	if len(changes) == 0 {
		t.Fatalf("expected changes: %v", body)
	}
	got := s.settingsPayload()
	if got["approval_mode"] != "auto" {
		t.Fatalf("persisted approval_mode=%v", got["approval_mode"])
	}
	if got["web_tools_enabled"] != false {
		t.Fatalf("persisted web_tools_enabled=%v", got["web_tools_enabled"])
	}
	settings, _ := body["settings"].(map[string]any)
	if _, ok := settings["llm_api_key"]; ok {
		t.Fatalf("patch response leaked llm_api_key: %v", settings)
	}
}

func TestSettingsPatchRefusesSecrets(t *testing.T) {
	_, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.patch", `{"patch":{"llm_api_key":"sk-injected-should-refuse","bot_token":"123:ABC"}}`)
	if body["ok"] != false {
		t.Fatalf("expected refuse: %v", body)
	}
	refused, _ := body["refused"].([]any)
	if len(refused) < 1 {
		t.Fatalf("refused=%v", body)
	}
}

func TestSettingsPatchPartialRefuseKeepsSafe(t *testing.T) {
	s, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.patch",
		`{"patch":{"approval_mode":"full","llm_api_key":"sk-nope","messengers":{"telegram":{"bot_token":"t"}}}}`)
	if body["ok"] != true {
		t.Fatalf("partial patch=%v", body)
	}
	refused, _ := body["refused"].([]any)
	if len(refused) < 2 {
		t.Fatalf("expected refused secret keys: %v", body)
	}
	if s.settingsPayload()["approval_mode"] != "full" {
		t.Fatalf("safe field not applied: %v", s.settingsPayload()["approval_mode"])
	}
}

func TestSettingsPatchEnabledChannels(t *testing.T) {
	s, reg := newSettingsToolsServer(t)
	body := execSettingsTool(t, reg, "settings.patch",
		`{"patch":{"enabled_channels":["cli","discord"]}}`)
	if body["ok"] != true {
		t.Fatalf("channels patch=%v", body)
	}
	chs := normalizeEnabledChannels(s.settingsPayload()["enabled_channels"])
	hasDiscord, hasCLI := false, false
	for _, ch := range chs {
		if ch == "discord" {
			hasDiscord = true
		}
		if ch == "cli" {
			hasCLI = true
		}
	}
	if !hasDiscord || !hasCLI {
		t.Fatalf("enabled_channels=%v", chs)
	}
}

func TestAttachSettingsToolsIdempotent(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	home := t.TempDir()
	s, err := New(Config{
		HomeDir: home,
		Token:   "attach-settings-token-not-sec",
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if err := r.AttachSettingsTools(func() *Server { return s }); err != nil {
		t.Fatal(err)
	}
	if err := r.AttachSettingsTools(func() *Server { return s }); err != nil {
		t.Fatal(err)
	}
	desc, err := r.Registry.Latest("settings.patch")
	if err != nil {
		t.Fatal(err)
	}
	if desc.Risk != tools.RiskMutation {
		t.Fatalf("risk=%v", desc.Risk)
	}
}

