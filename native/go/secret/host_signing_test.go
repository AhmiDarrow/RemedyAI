package secret

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestEnsureHostSigningKeyPersistsAndReloads(t *testing.T) {
	home := t.TempDir()
	key1, err := EnsureHostSigningKey(home)
	if err != nil {
		t.Fatal(err)
	}
	if len(key1) != HostSigningKeyBytes {
		t.Fatalf("len=%d", len(key1))
	}
	key2, err := EnsureHostSigningKey(home)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(key1, key2) {
		t.Fatal("reload must return the same key bytes")
	}
	path := filepath.Join(home, "auth", hostSigningFile)
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("missing on-disk key: %v", err)
	}
}

func TestPersistHostSigningKeyRejectsShort(t *testing.T) {
	if err := PersistHostSigningKey(t.TempDir(), make([]byte, 8)); err != ErrShortSigningKey {
		t.Fatalf("got %v", err)
	}
}

func TestDecodeRejectsWrongKind(t *testing.T) {
	raw := []byte(`{"v":2,"kind":"other","dpapi":"QQ=="}`)
	if got := decodeSigningKeyBytes(raw); got != nil {
		t.Fatalf("expected nil, got %d bytes", len(got))
	}
}
