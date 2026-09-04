package secret

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// EnsureLocalAPIToken mirrors Python local_auth.ensure_local_api_token:
// explicit > REMEDY_API_KEY > on-disk > generate+persist. Empty when auth off.
func EnsureLocalAPIToken(home, explicit string) string {
	if !authEnabledEnv() {
		return ""
	}
	if tok := strings.TrimSpace(explicit); tok != "" {
		_ = PersistLocalAPIToken(home, tok, "")
		return tok
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_API_KEY")); env != "" {
		_ = PersistLocalAPIToken(home, env, "")
		return env
	}
	home = resolveHome(home)
	if home == "" {
		return ""
	}
	primary := joinAuth(home, "local_api_token")
	if raw, err := os.ReadFile(primary); err == nil {
		if tok := DecodeTokenBytes(raw); len(tok) >= MinTokenLen {
			if tokenEncoding(raw) == "plain" && runtime.GOOS == "windows" {
				_ = PersistLocalAPIToken(home, tok, "")
			}
			return tok
		}
		if tokenEncoding(raw) == "dpapi" {
			alt := joinAuth(home, "local_api_token.posix")
			if altRaw, err := os.ReadFile(alt); err == nil {
				if tok := DecodeTokenBytes(altRaw); len(tok) >= MinTokenLen {
					return tok
				}
			}
			tok := generateTokenURLSafe(32)
			_ = PersistLocalAPIToken(home, tok, alt)
			return tok
		}
	}
	if altRaw, err := os.ReadFile(joinAuth(home, "local_api_token.posix")); err == nil {
		if tok := DecodeTokenBytes(altRaw); len(tok) >= MinTokenLen {
			return tok
		}
	}
	tok := generateTokenURLSafe(32)
	_ = PersistLocalAPIToken(home, tok, "")
	return tok
}

// PersistLocalAPIToken writes the bearer to disk (DPAPI envelope on Windows).
// dest overrides the primary path (used for local_api_token.posix).
func PersistLocalAPIToken(home, token, dest string) error {
	token = strings.TrimSpace(token)
	if len(token) < MinTokenLen {
		return nil
	}
	home = resolveHome(home)
	if home == "" {
		return errHomeRequired
	}
	path := dest
	if path == "" {
		path = joinAuth(home, "local_api_token")
	}
	data, err := encodeTokenBytes(token)
	if err != nil {
		return err
	}
	return WriteFileAtomic(path, data, 0o600)
}

func authEnabledEnv() bool {
	flag := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_API_AUTH")))
	switch flag {
	case "0", "false", "no", "off", "disable", "disabled":
		return false
	default:
		return true
	}
}

func resolveHome(home string) string {
	home = strings.TrimSpace(home)
	if home != "" {
		return home
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_HOME")); env != "" {
		return env
	}
	userHome, err := os.UserHomeDir()
	if err != nil || userHome == "" {
		return ""
	}
	return filepath.Join(userHome, ".remedy")
}

func tokenEncoding(raw []byte) string {
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return "missing"
	}
	if strings.HasPrefix(text, "{") {
		var outer map[string]any
		if err := json.Unmarshal([]byte(text), &outer); err != nil {
			return "plain"
		}
		if isV2(outer) {
			return "dpapi"
		}
		if enc, _ := outer["encoding"].(string); strings.EqualFold(enc, "dpapi") {
			return "dpapi"
		}
	}
	return "plain"
}

func encodeTokenBytes(token string) ([]byte, error) {
	plain := []byte(strings.TrimSpace(token))
	if runtime.GOOS == "windows" {
		if sealed, err := Protect(plain); err == nil && len(sealed) > 0 {
			envelope := map[string]any{
				"v":          2,
				"dpapi":      base64.StdEncoding.EncodeToString(sealed),
				"updated_at": float64(time.Now().UnixNano()) / 1e9,
			}
			b, err := json.MarshalIndent(envelope, "", "  ")
			if err != nil {
				return nil, err
			}
			return append(b, '\n'), nil
		}
	}
	return append(plain, '\n'), nil
}

func generateTokenURLSafe(nbytes int) string {
	if nbytes < 16 {
		nbytes = 16
	}
	buf := make([]byte, nbytes)
	if _, err := rand.Read(buf); err != nil {
		now := time.Now().UnixNano()
		for i := range buf {
			buf[i] = byte(now >> (uint(i%8) * 8))
		}
	}
	return base64.RawURLEncoding.EncodeToString(buf)
}
