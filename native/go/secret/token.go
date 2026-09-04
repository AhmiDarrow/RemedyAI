// Package secret decodes Remedy on-disk secrets (local API token envelopes).
package secret

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// MinTokenLen matches Python local_auth.MIN_TOKEN_LEN.
const MinTokenLen = 16

// DecodeTokenBytes mirrors Python local_auth._decode_token_bytes and Rust
// decode_local_api_token_bytes: plaintext, v2 DPAPI envelope, or legacy
// {"encoding":"dpapi","payload"|"token"}. Unknown JSON yields "".
func DecodeTokenBytes(raw []byte) string {
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return ""
	}
	if strings.HasPrefix(text, "{") {
		var outer map[string]any
		if err := json.Unmarshal([]byte(text), &outer); err != nil {
			return ""
		}
		if isV2(outer) {
			b64, _ := outer["dpapi"].(string)
			return unprotectB64(b64)
		}
		if enc, _ := outer["encoding"].(string); strings.EqualFold(enc, "dpapi") {
			b64, _ := outer["payload"].(string)
			if b64 == "" {
				b64, _ = outer["token"].(string)
			}
			return unprotectB64(b64)
		}
		return ""
	}
	if len(text) < MinTokenLen {
		return ""
	}
	return text
}

func isV2(outer map[string]any) bool {
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

func unprotectB64(b64 string) string {
	b64 = strings.TrimSpace(b64)
	if b64 == "" {
		return ""
	}
	cipher, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return ""
	}
	plain, err := Unprotect(cipher)
	if err != nil || len(plain) == 0 {
		return ""
	}
	tok := strings.TrimSpace(string(plain))
	if len(tok) < MinTokenLen {
		return ""
	}
	return tok
}

// ReadLocalAPIToken reads $home/auth/local_api_token, falling back to
// local_api_token.posix when the primary file is a DPAPI envelope this
// process cannot unwrap (WSL / cross-user share of ~/.remedy).
func ReadLocalAPIToken(home string) string {
	home = strings.TrimSpace(home)
	if home == "" {
		return ""
	}
	primary := joinAuth(home, "local_api_token")
	if raw, err := os.ReadFile(primary); err == nil {
		if tok := DecodeTokenBytes(raw); tok != "" {
			return tok
		}
		// Unreadable DPAPI (or empty) — try the posix sidecar.
		if alt, err := os.ReadFile(joinAuth(home, "local_api_token.posix")); err == nil {
			return DecodeTokenBytes(alt)
		}
		return ""
	}
	if alt, err := os.ReadFile(joinAuth(home, "local_api_token.posix")); err == nil {
		return DecodeTokenBytes(alt)
	}
	return ""
}

func joinAuth(home, name string) string {
	return filepath.Join(home, "auth", name)
}
