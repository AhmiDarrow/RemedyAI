package httpapi

import (
	"strings"
	"testing"
)

func TestSettingsCommandsShowAndChangeSettings(t *testing.T) {
	s, home := newPartnerTestServer(t)
	run := func(cmd string) string {
		t.Helper()
		text, _ := s.executeSlashCommand("", cmd)["text"].(string)
		return text
	}
	if text := run("/settings"); !strings.Contains(text, "approval_mode: ask") || !strings.Contains(text, "theme") {
		t.Fatalf("/settings lists names and values: %.300s", text)
	}
	// Nothing that looks like a secret, and nothing that is only a fact about the install.
	for _, hidden := range []string{"api_key", "config_path", "provider_keys"} {
		if strings.Contains(run("/settings"), hidden) {
			t.Fatalf("/settings shows %s", hidden)
		}
	}
	if text := run("/settings interop"); !strings.Contains(text, "advanced.interop_inbound") {
		t.Fatalf("a word finds advanced settings: %.300s", text)
	}
	if text := run("/get approval_mode"); !strings.Contains(text, "ask") {
		t.Fatalf("/get: %s", text)
	}

	if text := run("/web off"); !strings.Contains(text, "off") {
		t.Fatalf("/web off: %s", text)
	}
	InvalidateConfigCache()
	if cfgBool(LoadConfig(home), "web_tools_enabled", true) {
		t.Fatal("/web off did not save")
	}
	if text := run("/web"); !strings.Contains(text, "off") {
		t.Fatalf("/web alone shows it: %s", text)
	}
	if text := run("/web maybe"); !strings.Contains(text, "on or off") {
		t.Fatalf("a wrong value is explained: %s", text)
	}
	if text := run("/set log_level debug"); !strings.Contains(text, "DEBUG") {
		t.Fatalf("/set reads the saved value back: %s", text)
	}
	if text := run("/set retention_log_days 12"); !strings.Contains(text, "12") {
		t.Fatalf("/set a number: %s", text)
	}
	if text := run("/set retention_log_days soon"); !strings.Contains(text, "number") {
		t.Fatalf("a number setting refuses a word: %s", text)
	}
	if text := run("/set llm_api_key sk-test"); !strings.Contains(text, "not changed by a command") {
		t.Fatalf("a key is never set by a command: %s", text)
	}
	if text := run("/set nothing_like_this 1"); !strings.Contains(text, "no setting called") {
		t.Fatalf("an unknown name: %s", text)
	}
	if text := run("/safeguards"); !strings.Contains(text, "shell_in_auto: guard") {
		t.Fatalf("/safeguards: %s", text)
	}
}

func TestAPhoneCannotChangeLockedSettingsByCommand(t *testing.T) {
	s, home := newPartnerTestServer(t)
	// Give the phone the settings pane, so only the lock list stands in the way.
	if err := WriteConfig(FindConfigPath(home), ConfigMap{
		"approval_mode": "ask", "access_scope": "project",
		"connect_panes": map[string]any{"chat": true, "sessions": true, "settings_write": true},
	}); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	for _, cmd := range []string{"/approvals full", "/scope full", "/set shell_in_auto allow", "/set messenger_tools all"} {
		text, _ := s.executeSlashCommandFrom("", cmd, true)["text"].(string)
		if !strings.Contains(text, "at the PC") && !strings.Contains(text, "turned off for the phone") {
			t.Fatalf("%s from a phone must be refused: %s", cmd, text)
		}
	}
	InvalidateConfigCache()
	cfg := LoadConfig(home)
	if cfgString(cfg, "approval_mode", "") != "ask" || cfgString(cfg, "access_scope", "") != "project" {
		t.Fatalf("a phone changed a locked setting: %v / %v", cfg["approval_mode"], cfg["access_scope"])
	}
	// A harmless one goes through with the pane on.
	if out := s.executeSlashCommandFrom("", "/theme light", true); out["action"] != "settings_changed" {
		t.Fatalf("/theme from a phone with the settings pane on: %v", out)
	}
}

func TestInformationCommandsAnswerWithFacts(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Trip"})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := s.sessions.AddMessage(sess.ID, "user", strings.Repeat("word ", 40), nil, nil); err != nil {
			t.Fatal(err)
		}
	}
	run := func(cmd string) string {
		t.Helper()
		text, _ := s.executeSlashCommand(sess.ID, cmd)["text"].(string)
		return text
	}
	if text := run("/context"); !strings.Contains(text, "3 messages") {
		t.Fatalf("/context: %s", text)
	}
	if text := run("/usage 30"); !strings.Contains(text, "last 30 days") || !strings.Contains(text, "tokens") {
		t.Fatalf("/usage: %s", text)
	}
	if text := run("/tools read"); !strings.Contains(text, "read") {
		t.Fatalf("/tools with a filter: %s", text)
	}
	if text := run("/version"); !strings.Contains(text, "0.50.2-test") {
		t.Fatalf("/version: %s", text)
	}
	if text := run("/help model"); !strings.Contains(text, "/model") || !strings.Contains(text, "Also") {
		t.Fatalf("/help <command>: %s", text)
	}
	if text := run("/commands"); !strings.Contains(text, "**Settings**") || !strings.Contains(text, "/usage") {
		t.Fatalf("/commands groups them: %.200s", text)
	}

	if text := run("/remember the cat is called Miso"); !strings.Contains(text, "Miso") {
		t.Fatal(text)
	}
	if text := run("/forget Miso"); !strings.Contains(text, "Removed 2") { // the note and the fact
		t.Fatalf("/forget: %s", text)
	}
	if text := run("/memory Miso"); strings.Contains(text, "cat is called") {
		t.Fatalf("forgotten means gone: %s", text)
	}
	if text := run("/forget nothing-like-this"); !strings.Contains(text, "Nothing in memory") {
		t.Fatalf("/forget with no match: %s", text)
	}
}

func TestThePaletteOnlyOffersCommandsThatWork(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	_, out := s.selfCall("GET", "/api/commands", nil)
	list, _ := out["commands"].([]any)
	if len(list) < 50 {
		t.Fatalf("only %d commands offered", len(list))
	}
	seen := map[string]bool{}
	for _, c := range list {
		name, _ := c.(map[string]any)["name"].(string)
		if seen[name] {
			t.Errorf("%s is listed twice", name)
		}
		seen[name] = true
		text, _ := s.executeSlashCommand("", name)["text"].(string)
		if strings.Contains(text, "Unknown command") || strings.TrimSpace(text) == "" {
			t.Errorf("%s is offered but does nothing: %q", name, text)
		}
	}
	if got := groupDigits(1234567); got != "1,234,567" {
		t.Fatalf("groupDigits = %s", got)
	}
}
