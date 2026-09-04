package secret

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

const (
	// HostSigningKeyBytes is the Zig HMAC-SHA256 key length.
	HostSigningKeyBytes = 32
	hostSigningFile     = "host_signing_key"
	hostSigningPosix    = "host_signing_key.posix"
)

var (
	ErrShortSigningKey = errors.New("host signing key must be at least 32 bytes")
)

// EnsureHostSigningKey loads or creates the Zig capability-token HMAC key under
// $home/auth/. Never logs or returns key material to callers beyond the bytes
// needed for remedy_core_security_set_signing_key.
func EnsureHostSigningKey(home string) ([]byte, error) {
	home = resolveHome(home)
	if home == "" {
		return nil, errHomeRequired
	}
	primary := joinAuth(home, hostSigningFile)
	if key, ok := readSigningKeyFile(primary); ok {
		return key, nil
	}
	if key, ok := readSigningKeyFile(joinAuth(home, hostSigningPosix)); ok {
		return key, nil
	}
	key := make([]byte, HostSigningKeyBytes)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	if err := PersistHostSigningKey(home, key); err != nil {
		return nil, err
	}
	return key, nil
}

// PersistHostSigningKey writes the key (DPAPI envelope on Windows).
func PersistHostSigningKey(home string, key []byte) error {
	if len(key) < HostSigningKeyBytes {
		return ErrShortSigningKey
	}
	home = resolveHome(home)
	if home == "" {
		return errHomeRequired
	}
	data, err := encodeSigningKeyBytes(key[:HostSigningKeyBytes])
	if err != nil {
		return err
	}
	path := joinAuth(home, hostSigningFile)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	if err := WriteFileAtomic(path, data, 0o600); err != nil {
		return err
	}
	if runtime.GOOS == "windows" {
		// Posix sidecar for WSL / cross-read: base64 plaintext of the raw key.
		plain := []byte(base64.StdEncoding.EncodeToString(key[:HostSigningKeyBytes]))
		_ = WriteFileAtomic(joinAuth(home, hostSigningPosix), plain, 0o600)
	}
	return nil
}

func readSigningKeyFile(path string) ([]byte, bool) {
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) == 0 {
		return nil, false
	}
	key := decodeSigningKeyBytes(raw)
	if len(key) < HostSigningKeyBytes {
		return nil, false
	}
	return key[:HostSigningKeyBytes], true
}

func encodeSigningKeyBytes(key []byte) ([]byte, error) {
	if runtime.GOOS == "windows" {
		sealed, err := Protect(key)
		if err != nil {
			return nil, err
		}
		env := map[string]any{
			"v":     2,
			"kind":  "host_signing_key",
			"dpapi": base64.StdEncoding.EncodeToString(sealed),
		}
		return json.Marshal(env)
	}
	return []byte(base64.StdEncoding.EncodeToString(key)), nil
}

func decodeSigningKeyBytes(raw []byte) []byte {
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return nil
	}
	if strings.HasPrefix(text, "{") {
		var outer map[string]any
		if err := json.Unmarshal([]byte(text), &outer); err != nil {
			return nil
		}
		if kind, _ := outer["kind"].(string); kind != "" && kind != "host_signing_key" {
			return nil
		}
		b64, _ := outer["dpapi"].(string)
		if b64 == "" {
			return nil
		}
		cipher, err := base64.StdEncoding.DecodeString(strings.TrimSpace(b64))
		if err != nil {
			return nil
		}
		plain, err := Unprotect(cipher)
		if err != nil || len(plain) < HostSigningKeyBytes {
			return nil
		}
		return plain
	}
	decoded, err := base64.StdEncoding.DecodeString(text)
	if err != nil || len(decoded) < HostSigningKeyBytes {
		return nil
	}
	return decoded
}
