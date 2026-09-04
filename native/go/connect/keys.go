package connect

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	// HostKeyName is the on-disk host static key filename.
	HostKeyName = "host.key"
)

var hostKeyMu sync.Mutex

// HostKeyPath returns ~/.remedy/auth/connect/host.key for home (or REMEDY_HOME).
func HostKeyPath(home string) string {
	return filepath.Join(ResolveHome(home), "auth", "connect", HostKeyName)
}

// LoadOrCreateHostKeyPair returns the host static X25519 key, creating it once if missing.
func LoadOrCreateHostKeyPair(home string) (KeyPair, error) {
	path := HostKeyPath(home)
	hostKeyMu.Lock()
	defer hostKeyMu.Unlock()

	if st, err := os.Stat(path); err == nil && st.Size() > 0 {
		return loadHostKeyPair(path)
	}
	kp, err := GenerateKeyPair()
	if err != nil {
		return KeyPair{}, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return KeyPair{}, err
	}
	_ = os.Chmod(filepath.Dir(path), 0o700)
	raw, err := encodeHostKey(kp)
	if err != nil {
		return KeyPair{}, err
	}
	if err := writeBytesAtomic(path, raw); err != nil {
		return KeyPair{}, err
	}
	return loadHostKeyPair(path)
}

func loadHostKeyPair(path string) (KeyPair, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return KeyPair{}, err
	}
	sk, err := decodeHostKey(raw)
	if err != nil {
		return KeyPair{}, err
	}
	return KeyPairFromPrivate(sk)
}

func encodeHostKey(kp KeyPair) ([]byte, error) {
	if runtime.GOOS == "windows" {
		sealed, err := secret.Protect(kp.Private)
		if err == nil {
			envelope := map[string]any{
				"v":     2,
				"dpapi": true,
				"p":     base64.StdEncoding.EncodeToString(sealed),
			}
			return marshalEnvelope(envelope)
		}
	}
	envelope := map[string]any{
		"v":        2,
		"encoding": "plain",
		"p":        base64.StdEncoding.EncodeToString(kp.Private),
	}
	return marshalEnvelope(envelope)
}

func marshalEnvelope(envelope map[string]any) ([]byte, error) {
	b, err := json.MarshalIndent(envelope, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}

func decodeHostKey(raw []byte) ([]byte, error) {
	if len(raw) == DHLen {
		out := make([]byte, DHLen)
		copy(out, raw)
		return out, nil
	}
	var outer map[string]any
	if err := json.Unmarshal(raw, &outer); err != nil {
		return nil, fmt.Errorf("host key file is not valid JSON or raw key")
	}
	sk, err := skFromEnvelope(outer)
	if err != nil {
		return nil, err
	}
	if len(sk) != DHLen {
		return nil, fmt.Errorf("host key is not a 32-byte X25519 scalar")
	}
	return sk, nil
}

func skFromEnvelope(outer map[string]any) ([]byte, error) {
	if isEnvelopeV2(outer) && outer["dpapi"] == true {
		blob, _ := outer["p"].(string)
		if runtime.GOOS != "windows" {
			return nil, fmt.Errorf("DPAPI host key cannot be read on this platform")
		}
		cipher, err := base64.StdEncoding.DecodeString(blob)
		if err != nil {
			return nil, fmt.Errorf("host key DPAPI blob is not valid base64")
		}
		plain, err := secret.Unprotect(cipher)
		if err != nil {
			return nil, fmt.Errorf("host key DPAPI unprotect failed: %w", err)
		}
		return plain, nil
	}
	encoding, _ := outer["encoding"].(string)
	encoding = strings.TrimSpace(encoding)
	if encoding == "plain" || encoding == "raw" || encoding == "" {
		blob, _ := outer["p"].(string)
		if blob == "" {
			blob, _ = outer["sk"].(string)
		}
		if blob != "" {
			return base64.StdEncoding.DecodeString(blob)
		}
	}
	return nil, fmt.Errorf("unrecognized host key envelope")
}

func isEnvelopeV2(outer map[string]any) bool {
	switch v := outer["v"].(type) {
	case float64:
		return v == 2
	case json.Number:
		n, err := v.Int64()
		return err == nil && n == 2
	default:
		return false
	}
}
