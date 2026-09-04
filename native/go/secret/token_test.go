package secret

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestDecodePlaintext(t *testing.T) {
	const tok = "plain-token-not-secret16"
	if got := DecodeTokenBytes([]byte(tok + "\n")); got != tok {
		t.Fatalf("got %q", got)
	}
	if got := DecodeTokenBytes([]byte("short")); got != "" {
		t.Fatalf("short token should be rejected, got %q", got)
	}
}

func TestDecodeUnknownJSON(t *testing.T) {
	if got := DecodeTokenBytes([]byte(`{"v":1,"x":"y"}`)); got != "" {
		t.Fatalf("unknown JSON = %q", got)
	}
}

func TestDecodeLegacyAndV2DPAPI(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("DPAPI round-trip is Windows-only")
	}
	const tok = "dpapi-token-not-a-secret!"
	sealed, err := Protect([]byte(tok))
	if err != nil {
		t.Fatal(err)
	}
	b64 := base64.StdEncoding.EncodeToString(sealed)

	v2, _ := json.Marshal(map[string]any{"v": 2, "dpapi": b64, "updated_at": 1.0})
	if got := DecodeTokenBytes(v2); got != tok {
		t.Fatalf("v2 = %q, want %q", got, tok)
	}

	legacy, _ := json.Marshal(map[string]any{"encoding": "dpapi", "payload": b64})
	if got := DecodeTokenBytes(legacy); got != tok {
		t.Fatalf("legacy payload = %q", got)
	}

	legacyTok, _ := json.Marshal(map[string]any{"encoding": "dpapi", "token": b64})
	if got := DecodeTokenBytes(legacyTok); got != tok {
		t.Fatalf("legacy token = %q", got)
	}
}

func TestReadLocalAPITokenPosixFallback(t *testing.T) {
	dir := t.TempDir()
	auth := filepath.Join(dir, "auth")
	if err := os.MkdirAll(auth, 0o700); err != nil {
		t.Fatal(err)
	}
	// Unreadable primary (fake DPAPI JSON this process cannot unwrap).
	primary := []byte("{\n  \"v\": 2,\n  \"dpapi\": \"AAAA\"\n}\n")
	if err := os.WriteFile(filepath.Join(auth, "local_api_token"), primary, 0o600); err != nil {
		t.Fatal(err)
	}
	const posixTok = "posix-token-not-secret16"
	if err := os.WriteFile(filepath.Join(auth, "local_api_token.posix"), []byte(posixTok+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := ReadLocalAPIToken(dir); got != posixTok {
		t.Fatalf("posix fallback = %q, want %q", got, posixTok)
	}
}

func TestReadLocalAPITokenPlainPrimary(t *testing.T) {
	dir := t.TempDir()
	auth := filepath.Join(dir, "auth")
	if err := os.MkdirAll(auth, 0o700); err != nil {
		t.Fatal(err)
	}
	const tok = "file-token-not-secret16"
	if err := os.WriteFile(filepath.Join(auth, "local_api_token"), []byte(tok+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := ReadLocalAPIToken(dir); got != tok {
		t.Fatalf("got %q", got)
	}
}
