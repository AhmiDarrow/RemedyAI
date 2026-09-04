package secret

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestProviderKeysRoundTrip(t *testing.T) {
	home := t.TempDir()
	if err := SetProviderSecret(home, "openai", "sk-test-not-a-real-key-12"); err != nil {
		t.Fatal(err)
	}
	got := GetProviderSecret(home, "openai")
	if got != "sk-test-not-a-real-key-12" {
		t.Fatalf("got %q", got)
	}
	status := PublicSecretStatus(home)
	set, _ := status["provider_keys_set"].(map[string]bool)
	if !set["openai"] {
		t.Fatalf("provider_keys_set = %#v", status["provider_keys_set"])
	}
	enc, _ := status["encoding"].(string)
	if runtime.GOOS == "windows" && enc != "dpapi" {
		t.Fatalf("windows encoding = %q, want dpapi", enc)
	}
	if runtime.GOOS != "windows" && enc != "plain" {
		t.Fatalf("non-windows encoding = %q, want plain", enc)
	}
	// Clear
	if err := SetProviderSecret(home, "openai", ""); err != nil {
		t.Fatal(err)
	}
	if GetProviderSecret(home, "openai") != "" {
		t.Fatal("expected cleared key")
	}
}

func TestProviderKeysNeverPlainJSONSecretsOnWindows(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("DPAPI Windows-only")
	}
	home := t.TempDir()
	if err := SetProviderSecret(home, "xai", "xai-test-not-a-real-key!!"); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(ProviderKeysPath(home))
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Ext(ProviderKeysPath(home)) != ".json" {
		t.Fatal("unexpected path")
	}
	text := string(raw)
	if strings.Contains(text, "xai-test-not-a-real-key!!") {
		t.Fatal("raw secret leaked into on-disk store")
	}
}

func TestEnsureLocalAPITokenGeneratesAndReads(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_API_AUTH", "1")
	t.Setenv("REMEDY_API_KEY", "")
	tok := EnsureLocalAPIToken(home, "")
	if len(tok) < MinTokenLen {
		t.Fatalf("generated token too short: %q", tok)
	}
	again := EnsureLocalAPIToken(home, "")
	if again != tok {
		t.Fatalf("second ensure = %q, want %q", again, tok)
	}
	if ReadLocalAPIToken(home) != tok {
		t.Fatalf("ReadLocalAPIToken mismatch")
	}
}

func TestEnsureLocalAPITokenDisabled(t *testing.T) {
	t.Setenv("REMEDY_API_AUTH", "0")
	if got := EnsureLocalAPIToken(t.TempDir(), ""); got != "" {
		t.Fatalf("got %q", got)
	}
}
