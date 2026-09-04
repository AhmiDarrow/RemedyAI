package connect

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestHostKeyPersistsAndReloads(t *testing.T) {
	home := t.TempDir()
	first, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	second, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	if string(first.Public) != string(second.Public) || string(first.Private) != string(second.Private) {
		t.Fatal("reload must return the same keypair")
	}
	if len(first.Public) != DHLen || len(first.Private) != DHLen {
		t.Fatal("X25519 keys must be 32 bytes")
	}
	path := HostKeyPath(home)
	if path != filepath.Join(home, "auth", "connect", HostKeyName) {
		t.Fatalf("unexpected path %s", path)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
}

func TestDistinctHomesGetDistinctKeys(t *testing.T) {
	root := t.TempDir()
	a, err := LoadOrCreateHostKeyPair(filepath.Join(root, "a"))
	if err != nil {
		t.Fatal(err)
	}
	b, err := LoadOrCreateHostKeyPair(filepath.Join(root, "b"))
	if err != nil {
		t.Fatal(err)
	}
	if string(a.Public) == string(b.Public) {
		t.Fatal("distinct homes must not share a host key")
	}
}

func TestHostKeyHonoursRemedyHome(t *testing.T) {
	home := filepath.Join(t.TempDir(), "env-home")
	t.Setenv("REMEDY_HOME", home)
	kp, err := LoadOrCreateHostKeyPair("")
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, "auth", "connect", HostKeyName)
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
	again, err := LoadOrCreateHostKeyPair("")
	if err != nil {
		t.Fatal(err)
	}
	if string(again.Public) != string(kp.Public) {
		t.Fatal("REMEDY_HOME reload mismatch")
	}
}

func TestHostKeyEnvelopeShape(t *testing.T) {
	home := t.TempDir()
	kp, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(HostKeyPath(home))
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	if strings.Contains(text, hex.EncodeToString(kp.Private)) {
		t.Fatal("private key must not appear in envelope plaintext")
	}
	var outer map[string]any
	if err := json.Unmarshal(raw, &outer); err != nil {
		t.Fatal(err)
	}
	if v, _ := outer["v"].(float64); v != 2 {
		t.Fatalf("v=%v", outer["v"])
	}
	if runtime.GOOS == "windows" {
		if outer["dpapi"] != true {
			t.Fatalf("windows envelope should set dpapi=true, got %#v", outer["dpapi"])
		}
		p, _ := outer["p"].(string)
		if p == "" {
			t.Fatal("missing p")
		}
	} else {
		enc, _ := outer["encoding"].(string)
		if enc != "plain" && enc != "raw" {
			t.Fatalf("encoding=%q", enc)
		}
	}
}

func TestReloadFromRaw32ByteFile(t *testing.T) {
	home := t.TempDir()
	kp, err := GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	path := HostKeyPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, kp.Private, 0o600); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	if string(loaded.Public) != string(kp.Public) {
		t.Fatal("raw 32-byte host key reload failed")
	}
}

func TestCorruptHostKeyFailsClosed(t *testing.T) {
	home := t.TempDir()
	path := HostKeyPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadOrCreateHostKeyPair(home); err == nil {
		t.Fatal("corrupt host key must fail")
	}
}

func TestUnrecognizedEnvelopeFailsClosed(t *testing.T) {
	home := t.TempDir()
	path := HostKeyPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(map[string]any{"v": 2, "encoding": "mystery"})
	if err := os.WriteFile(path, body, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadOrCreateHostKeyPair(home); err == nil {
		t.Fatal("unrecognized envelope must fail")
	}
}

func TestPlainEnvelopeRoundTrip(t *testing.T) {
	home := t.TempDir()
	kp, err := GenerateKeyPair()
	if err != nil {
		t.Fatal(err)
	}
	path := HostKeyPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	envelope := map[string]any{
		"v":        2,
		"encoding": "plain",
		"p":        base64.StdEncoding.EncodeToString(kp.Private),
	}
	raw, _ := json.MarshalIndent(envelope, "", "  ")
	if err := os.WriteFile(path, append(raw, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		t.Fatal(err)
	}
	if string(loaded.Private) != string(kp.Private) {
		t.Fatal("plain envelope private mismatch")
	}
}
