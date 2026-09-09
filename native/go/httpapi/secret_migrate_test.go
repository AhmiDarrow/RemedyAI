package httpapi

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const legacyConfigTOML = `enabled_channels = ["telegram", "matrix"]
llm_provider = "openai"
llm_api_key = "legacy-llm-key"

[telegram]
bot_token = "legacy-telegram"
allow_chat_ids = "555"

[matrix]
access_token = "legacy-matrix"
homeserver = "https://matrix.example.org"

[provider_keys]
anthropic = "legacy-anthropic"
`

func writeLegacyConfig(t *testing.T, home string) string {
	t.Helper()
	path := filepath.Join(home, "config.toml")
	if err := os.WriteFile(path, []byte(legacyConfigTOML), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()
	return path
}

// A plaintext token sitting in config.toml stays authoritative forever, since
// the resolver still reads it. Move it into the secret store once and take it
// out of the file.
func TestLegacyConfigCredentialsMoveIntoTheSecretStore(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	path := writeLegacyConfig(t, home)

	moved, err := migrateConfigSecrets(home)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"anthropic", "ch:matrix:access_token", "ch:telegram:bot_token", "openai"}
	if strings.Join(moved, ",") != strings.Join(want, ",") {
		t.Fatalf("moved=%v want %v", moved, want)
	}

	for storeKey, value := range map[string]string{
		"ch:telegram:bot_token":  "legacy-telegram",
		"ch:matrix:access_token": "legacy-matrix",
		"anthropic":              "legacy-anthropic",
		"openai":                 "legacy-llm-key",
	} {
		if got := secret.GetProviderSecret(home, storeKey); got != value {
			t.Fatalf("store[%s]=%q want %q", storeKey, got, value)
		}
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	for _, leaked := range []string{"legacy-telegram", "legacy-matrix", "legacy-anthropic", "legacy-llm-key"} {
		if strings.Contains(text, leaked) {
			t.Fatalf("config still holds %q:\n%s", leaked, text)
		}
	}
	for _, key := range []string{"bot_token", "access_token", "llm_api_key", "provider_keys"} {
		if strings.Contains(text, key) {
			t.Fatalf("config still names %q:\n%s", key, text)
		}
	}

	// Non-secret settings survive: this is a credential move, not a reset.
	InvalidateConfigCache()
	cfg := LoadConfig(home)
	tg, _ := asStringMap(cfg["telegram"])
	if tg == nil || strings.TrimSpace(cfgString(ConfigMap(tg), "allow_chat_ids", "")) != "555" {
		t.Fatalf("allowlist lost: %+v", cfg["telegram"])
	}
	mx, _ := asStringMap(cfg["matrix"])
	if mx == nil || cfgString(ConfigMap(mx), "homeserver", "") != "https://matrix.example.org" {
		t.Fatalf("homeserver lost: %+v", cfg["matrix"])
	}

	// The migrated token resolves exactly as the config one used to.
	if got := resolveMessengerSecret(map[string]any(cfg), home, "telegram", "bot_token"); got != "legacy-telegram" {
		t.Fatalf("resolver after migration=%q", got)
	}

	// One shot: a second run has nothing to move and leaves the file alone.
	before, _ := os.ReadFile(path)
	again, err := migrateConfigSecrets(home)
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != 0 {
		t.Fatalf("second run moved %v", again)
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("second run rewrote the config")
	}
}

// A key the owner already moved into the store wins; the stale config copy is
// removed rather than overwriting the good one.
func TestMigrationNeverOverwritesAStoredKey(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	writeLegacyConfig(t, home)
	if err := secret.SetProviderSecret(home, "ch:telegram:bot_token", "current"); err != nil {
		t.Fatal(err)
	}
	if _, err := migrateConfigSecrets(home); err != nil {
		t.Fatal(err)
	}
	if got := secret.GetProviderSecret(home, "ch:telegram:bot_token"); got != "current" {
		t.Fatalf("stored key was clobbered: %q", got)
	}
}

// Plaintext at rest is a fact the owner has to be able to see, not a silent
// fallback buried in the store format.
func TestPlaintextAtRestIsSurfaced(t *testing.T) {
	home := t.TempDir()
	if err := secret.SetProviderSecret(home, "openai", "k"); err != nil {
		t.Fatal(err)
	}
	status := secret.PublicSecretStatus(home)
	encoding, _ := status["encoding"].(string)
	plaintext, _ := status["plaintext_at_rest"].(bool)
	if plaintext != (encoding == "plain") {
		t.Fatalf("encoding=%q plaintext_at_rest=%v", encoding, plaintext)
	}
	if plaintext {
		warning, _ := status["encoding_warning"].(string)
		if warning == "" {
			t.Fatal("a plaintext store must carry a warning the owner can read")
		}
		if !strings.Contains(warning, "plaintext") {
			t.Fatalf("warning=%q", warning)
		}
	}
	if secret.PlaintextAtRest(home) != plaintext {
		t.Fatal("PlaintextAtRest disagrees with the reported status")
	}
	// Never a raw secret in the owner-facing blob.
	for _, v := range status {
		if s, ok := v.(string); ok && strings.Contains(s, "k") && s == "k" {
			t.Fatalf("secret leaked into status: %v", status)
		}
	}
}

// The server runs the migration before it reads any messenger credential.
func TestServerMigratesLegacyCredentialsOnStartup(t *testing.T) {
	s, home := newConnectTestServer(t)
	path := writeLegacyConfig(t, home)

	secretMigrateMu.Lock()
	delete(secretMigratedHome, ResolveHomeDir(home))
	secretMigrateMu.Unlock()

	s.migrateSecretsOnce()

	if got := secret.GetProviderSecret(home, "ch:telegram:bot_token"); got != "legacy-telegram" {
		t.Fatalf("store=%q", got)
	}
	raw, _ := os.ReadFile(path)
	if strings.Contains(string(raw), "legacy-telegram") {
		t.Fatalf("config still holds the token:\n%s", raw)
	}
}
