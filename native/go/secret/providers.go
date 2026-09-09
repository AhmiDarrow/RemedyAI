package secret

import (
	"encoding/base64"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

// Provider store filename under $home/auth/.
const ProviderKeysFilename = "provider_keys.json"

const providerStoreVersion = 2

var (
	providerKeysMu    sync.Mutex
	providerKeysCache struct {
		path  string
		mtime int64
		size  int64
		data  map[string]string
	}
)

// ProviderKeysPath returns $home/auth/provider_keys.json.
func ProviderKeysPath(home string) string {
	return filepath.Join(strings.TrimSpace(home), "auth", ProviderKeysFilename)
}

// LoadProviderKeys returns {provider: api_key} from the secure store.
// Never returns secret values to callers that only need presence — use
// PublicSecretStatus for API responses.
func LoadProviderKeys(home string) map[string]string {
	home = strings.TrimSpace(home)
	if home == "" {
		return map[string]string{}
	}
	path := ProviderKeysPath(home)
	st, err := os.Stat(path)
	if err != nil {
		if providerKeysCache.path == path {
			invalidateProviderKeysCache()
		}
		return map[string]string{}
	}
	mtime := st.ModTime().UnixNano()
	size := st.Size()
	providerKeysMu.Lock()
	defer providerKeysMu.Unlock()
	if providerKeysCache.path == path &&
		providerKeysCache.mtime == mtime &&
		providerKeysCache.size == size &&
		providerKeysCache.data != nil {
		return cloneStringMap(providerKeysCache.data)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return map[string]string{}
	}
	data := decodeProviderStore(raw)
	providerKeysCache.path = path
	providerKeysCache.mtime = mtime
	providerKeysCache.size = size
	providerKeysCache.data = data
	return cloneStringMap(data)
}

// SetProviderSecret sets or clears one provider key. Empty apiKey clears.
func SetProviderSecret(home, provider, apiKey string) error {
	provider = strings.ToLower(strings.TrimSpace(provider))
	if provider == "" {
		return errProviderRequired
	}
	home = strings.TrimSpace(home)
	if home == "" {
		return errHomeRequired
	}
	providerKeysMu.Lock()
	defer providerKeysMu.Unlock()
	keys := LoadProviderKeysUnlocked(home)
	val := strings.TrimSpace(apiKey)
	if val != "" {
		keys[provider] = val
	} else {
		delete(keys, provider)
	}
	return saveProviderKeysUnlocked(home, keys)
}

// GetProviderSecret returns one provider key (empty if missing).
func GetProviderSecret(home, provider string) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	if provider == "" {
		return ""
	}
	return LoadProviderKeys(home)[provider]
}

// StoreEncoding reports how the provider store on disk is protected:
// "dpapi", "plain", "missing", or "unknown".
func StoreEncoding(home string) string {
	raw, err := os.ReadFile(ProviderKeysPath(home))
	if err != nil || len(raw) == 0 {
		return "missing"
	}
	var outer map[string]any
	if json.Unmarshal(raw, &outer) != nil {
		return "unknown"
	}
	if enc, _ := outer["encoding"].(string); enc != "" {
		return enc
	}
	return "unknown"
}

// PlaintextAtRest reports whether secrets are readable by anyone who can read
// the profile. Callers surface this; it is never a silent fallback.
func PlaintextAtRest(home string) bool {
	return strings.EqualFold(StoreEncoding(home), "plain")
}

// PlaintextWarning is the owner-facing sentence for a plaintext store.
const PlaintextWarning = "Provider and messenger credentials are stored as plaintext on this " +
	"machine: the OS credential seal (DPAPI) is unavailable here, and Remedy will not " +
	"invent a weaker one. Anyone who can read your user profile can read them. " +
	"Keep this home directory off shared storage and out of backups you do not control."

// warnPlaintextOnce keeps the startup log to one line per process.
var warnPlaintextOnce sync.Once

// WarnPlaintextAtRest logs PlaintextWarning once when the store is plaintext
// and actually holds something.
func WarnPlaintextAtRest(home string) {
	if !PlaintextAtRest(home) || len(LoadProviderKeys(home)) == 0 {
		return
	}
	warnPlaintextOnce.Do(func() {
		log.Printf("secret store: %s", PlaintextWarning)
	})
}

// PublicSecretStatus is the safe blob for GET /api/settings (no raw secrets).
func PublicSecretStatus(home string) map[string]any {
	keys := LoadProviderKeys(home)
	path := ProviderKeysPath(home)
	encoding := "missing"
	if raw, err := os.ReadFile(path); err == nil && len(raw) > 0 {
		var outer map[string]any
		if json.Unmarshal(raw, &outer) == nil {
			if enc, _ := outer["encoding"].(string); enc != "" {
				encoding = enc
			} else {
				encoding = "unknown"
			}
		} else {
			encoding = "unknown"
		}
	}
	set := make(map[string]bool, len(keys))
	providers := make([]string, 0, len(keys))
	for k := range keys {
		set[k] = true
		providers = append(providers, k)
	}
	plaintext := strings.EqualFold(encoding, "plain")
	out := map[string]any{
		"providers_with_keys": providers,
		"provider_keys_set":   set,
		"store_path":          path,
		"encoding":            encoding,
		"plaintext_at_rest":   plaintext,
	}
	if plaintext && len(keys) > 0 {
		out["encoding_warning"] = PlaintextWarning
	}
	return out
}

// LoadProviderKeysUnlocked assumes the caller holds providerKeysMu or is
// single-threaded (tests). Prefer LoadProviderKeys.
func LoadProviderKeysUnlocked(home string) map[string]string {
	path := ProviderKeysPath(home)
	raw, err := os.ReadFile(path)
	if err != nil {
		return map[string]string{}
	}
	return decodeProviderStore(raw)
}

func saveProviderKeysUnlocked(home string, keys map[string]string) error {
	cleaned := normalizeProviderKeys(keys)
	data, err := encodeProviderStore(cleaned)
	if err != nil {
		return err
	}
	path := ProviderKeysPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	if err := WriteFileAtomic(path, data, 0o600); err != nil {
		return err
	}
	invalidateProviderKeysCache()
	if st, err := os.Stat(path); err == nil {
		providerKeysCache.path = path
		providerKeysCache.mtime = st.ModTime().UnixNano()
		providerKeysCache.size = st.Size()
		providerKeysCache.data = cleaned
	}
	return nil
}

func invalidateProviderKeysCache() {
	providerKeysCache.path = ""
	providerKeysCache.mtime = 0
	providerKeysCache.size = 0
	providerKeysCache.data = nil
}

func decodeProviderStore(raw []byte) map[string]string {
	if len(raw) == 0 {
		return map[string]string{}
	}
	var outer map[string]any
	if err := json.Unmarshal(raw, &outer); err != nil {
		return map[string]string{}
	}
	enc, _ := outer["encoding"].(string)
	switch strings.ToLower(strings.TrimSpace(enc)) {
	case "dpapi":
		b64, _ := outer["payload"].(string)
		cipher, err := base64.StdEncoding.DecodeString(strings.TrimSpace(b64))
		if err != nil {
			return map[string]string{}
		}
		plain, err := Unprotect(cipher)
		if err != nil || len(plain) == 0 {
			return map[string]string{}
		}
		var inner map[string]any
		if err := json.Unmarshal(plain, &inner); err != nil {
			return map[string]string{}
		}
		if keys, ok := inner["keys"].(map[string]any); ok {
			return normalizeProviderKeysAny(keys)
		}
		return normalizeProviderKeysAny(inner)
	case "plain", "":
		if keys, ok := outer["keys"].(map[string]any); ok {
			return normalizeProviderKeysAny(keys)
		}
		skip := map[string]struct{}{
			"version": {}, "encoding": {}, "updated_at": {}, "payload": {}, "encryption": {},
		}
		flat := make(map[string]any)
		for k, v := range outer {
			if _, ok := skip[k]; ok {
				continue
			}
			flat[k] = v
		}
		return normalizeProviderKeysAny(flat)
	default:
		return map[string]string{}
	}
}

func encodeProviderStore(keys map[string]string) ([]byte, error) {
	inner := map[string]any{
		"version":    providerStoreVersion,
		"keys":       keys,
		"updated_at": float64(time.Now().UnixNano()) / 1e9,
	}
	plain, err := json.Marshal(inner)
	if err != nil {
		return nil, err
	}
	if runtime.GOOS == "windows" {
		sealed, perr := Protect(plain)
		if perr != nil {
			// On Windows DPAPI is expected to work; if it does not, the owner
			// needs to know their credentials just landed in the clear.
			log.Printf("secret store: DPAPI seal failed (%v) — writing plaintext instead", perr)
		}
		if perr == nil && len(sealed) > 0 {
			outer := map[string]any{
				"version":    providerStoreVersion,
				"encoding":   "dpapi",
				"payload":    base64.StdEncoding.EncodeToString(sealed),
				"updated_at": float64(time.Now().UnixNano()) / 1e9,
			}
			b, err := json.MarshalIndent(outer, "", "  ")
			if err != nil {
				return nil, err
			}
			return append(b, '\n'), nil
		}
	}
	// No OS credential seal on this platform. A keyring (Secret Service /
	// macOS Keychain) would need a new third-party dependency, so the store
	// stays plaintext and says so loudly rather than pretending otherwise:
	// see PlaintextWarning, PublicSecretStatus and WarnPlaintextAtRest.
	if len(keys) > 0 {
		warnPlaintextOnce.Do(func() {
			log.Printf("secret store: %s", PlaintextWarning)
		})
	}
	outer := map[string]any{
		"version":    providerStoreVersion,
		"encoding":   "plain",
		"keys":       keys,
		"updated_at": float64(time.Now().UnixNano()) / 1e9,
	}
	b, err := json.MarshalIndent(outer, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}

func normalizeProviderKeys(raw map[string]string) map[string]string {
	out := make(map[string]string, len(raw))
	for k, v := range raw {
		pk := strings.ToLower(strings.TrimSpace(k))
		val := strings.TrimSpace(v)
		if pk != "" && val != "" && !strings.HasPrefix(pk, "_") {
			out[pk] = val
		}
	}
	return out
}

func normalizeProviderKeysAny(raw map[string]any) map[string]string {
	out := make(map[string]string)
	for k, v := range raw {
		pk := strings.ToLower(strings.TrimSpace(k))
		val := strings.TrimSpace(asString(v))
		if pk != "" && val != "" && !strings.HasPrefix(pk, "_") {
			out[pk] = val
		}
	}
	return out
}

func asString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	default:
		return ""
	}
}

func cloneStringMap(in map[string]string) map[string]string {
	out := make(map[string]string, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

type secretError string

func (e secretError) Error() string { return string(e) }

const (
	errProviderRequired secretError = "provider is required"
	errHomeRequired     secretError = "home is required"
)
