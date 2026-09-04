package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	toml "github.com/pelletier/go-toml/v2"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// ConfigMap is the parsed config.toml root (scalars + one-level tables).
type ConfigMap map[string]any

type configCacheEntry struct {
	path  string
	mtime int64
	size  int64
	data  ConfigMap
}

type configKV struct {
	k string
	v any
}

var (
	configMu    sync.Mutex
	configCache configCacheEntry
)

// ResolveHomeDir returns cfg home, REMEDY_HOME, or ~/.remedy.
func ResolveHomeDir(homeDir string) string {
	homeDir = strings.TrimSpace(homeDir)
	if homeDir != "" {
		return homeDir
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

// DefaultConfigPath is $home/config.toml.
func DefaultConfigPath(homeDir string) string {
	home := ResolveHomeDir(homeDir)
	if home == "" {
		return ""
	}
	return filepath.Join(home, "config.toml")
}

// FindConfigPath returns an existing config.toml path, or "" when absent.
// When REMEDY_HOME / HomeDir is set, never falls through to another home.
func FindConfigPath(homeDir string) string {
	primary := DefaultConfigPath(homeDir)
	if primary == "" {
		return ""
	}
	if st, err := os.Stat(primary); err == nil && !st.IsDir() {
		return primary
	}
	return ""
}

// LoadConfig loads config.toml for home (mtime-cached). Returns a shallow copy.
func LoadConfig(homeDir string) ConfigMap {
	path := FindConfigPath(homeDir)
	if path == "" {
		return ConfigMap{}
	}
	st, err := os.Stat(path)
	if err != nil {
		return ConfigMap{}
	}
	mtime := st.ModTime().UnixNano()
	size := st.Size()
	configMu.Lock()
	defer configMu.Unlock()
	if configCache.path == path && configCache.mtime == mtime && configCache.size == size && configCache.data != nil {
		return cloneConfig(configCache.data)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return ConfigMap{}
	}
	data, err := parseTOML(raw)
	if err != nil {
		return ConfigMap{}
	}
	configCache = configCacheEntry{path: path, mtime: mtime, size: size, data: data}
	return cloneConfig(data)
}

// InvalidateConfigCache drops the process-local mtime cache.
func InvalidateConfigCache() {
	configMu.Lock()
	configCache = configCacheEntry{}
	configMu.Unlock()
}

// WriteConfig persists non-secret settings. API keys never land in config.toml.
// Scalars are emitted before tables (Python parity — avoids TOML overwrite bugs).
func WriteConfig(path string, cfg ConfigMap) error {
	if path == "" {
		return fmt.Errorf("config path required")
	}
	safe := scrubConfigSecrets(cfg)
	var b bytes.Buffer
	b.WriteString("# Remedy AI Configuration\n\n")
	b.WriteString("# API keys are stored in ~/.remedy/auth/ (DPAPI-encrypted on Windows),\n")
	b.WriteString("# not in this file.\n\n")

	var scalars []configKV
	var tables []configKV
	for k, v := range safe {
		if k == "provider_keys" || k == "llm_api_key" {
			continue
		}
		if v == nil {
			continue
		}
		if m, ok := asStringMap(v); ok {
			tables = append(tables, configKV{k, m})
		} else {
			scalars = append(scalars, configKV{k, v})
		}
	}
	sort.Slice(scalars, func(i, j int) bool { return scalars[i].k < scalars[j].k })
	sort.Slice(tables, func(i, j int) bool { return tables[i].k < tables[j].k })
	for _, item := range scalars {
		line, err := serializeTOMLValue(item.v)
		if err != nil {
			return err
		}
		b.WriteString(item.k)
		b.WriteString(" = ")
		b.WriteString(line)
		b.WriteByte('\n')
	}
	if len(scalars) > 0 && len(tables) > 0 {
		b.WriteByte('\n')
	}
	for _, item := range tables {
		m := item.v.(map[string]any)
		b.WriteByte('[')
		b.WriteString(item.k)
		b.WriteString("]\n")
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			v := m[k]
			if v == nil {
				continue
			}
			line, err := serializeTOMLValue(v)
			if err != nil {
				return err
			}
			b.WriteString(k)
			b.WriteString(" = ")
			b.WriteString(line)
			b.WriteByte('\n')
		}
		b.WriteByte('\n')
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	if err := secret.WriteFileAtomic(path, b.Bytes(), 0o600); err != nil {
		return err
	}
	InvalidateConfigCache()
	if st, err := os.Stat(path); err == nil {
		configMu.Lock()
		configCache = configCacheEntry{
			path:  path,
			mtime: st.ModTime().UnixNano(),
			size:  st.Size(),
			data:  cloneConfig(safe),
		}
		configMu.Unlock()
	}
	return nil
}

func parseTOML(raw []byte) (ConfigMap, error) {
	var data map[string]any
	if err := toml.Unmarshal(raw, &data); err != nil {
		return nil, err
	}
	if data == nil {
		return ConfigMap{}, nil
	}
	return ConfigMap(data), nil
}

func scrubConfigSecrets(cfg ConfigMap) ConfigMap {
	out := cloneConfig(cfg)
	delete(out, "provider_keys")
	if _, ok := out["llm_api_key"]; ok {
		out["llm_api_key"] = ""
	}
	secretKeys := map[string]struct{}{
		"bot_token": {}, "access_token": {}, "app_token": {}, "app_password": {},
		"app_secret": {}, "verify_token": {}, "signing_secret": {},
	}
	for k, v := range out {
		m, ok := asStringMap(v)
		if !ok {
			continue
		}
		cleaned := cloneConfig(ConfigMap(m))
		for fk := range cleaned {
			fl := strings.ToLower(fk)
			_, hit := secretKeys[fl]
			if hit || strings.HasSuffix(fl, "_token") || strings.HasSuffix(fl, "_password") || strings.HasSuffix(fl, "_secret") {
				cleaned[fk] = ""
			}
		}
		out[k] = map[string]any(cleaned)
	}
	return out
}

func serializeTOMLValue(v any) (string, error) {
	switch t := v.(type) {
	case bool:
		if t {
			return "true", nil
		}
		return "false", nil
	case int:
		return fmt.Sprintf("%d", t), nil
	case int64:
		return fmt.Sprintf("%d", t), nil
	case float32:
		return serializeTOMLValue(float64(t))
	case float64:
		if t == float64(int64(t)) {
			return fmt.Sprintf("%d", int64(t)), nil
		}
		return fmt.Sprintf("%v", t), nil
	case string:
		b, err := json.Marshal(t)
		if err != nil {
			return "", err
		}
		return string(b), nil
	case []any:
		parts := make([]string, 0, len(t))
		for _, item := range t {
			s, err := serializeTOMLValue(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, s)
		}
		return "[" + strings.Join(parts, ", ") + "]", nil
	case []string:
		parts := make([]string, 0, len(t))
		for _, item := range t {
			s, err := serializeTOMLValue(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, s)
		}
		return "[" + strings.Join(parts, ", ") + "]", nil
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, k := range keys {
			if t[k] == nil {
				continue
			}
			s, err := serializeTOMLValue(t[k])
			if err != nil {
				return "", err
			}
			parts = append(parts, k+" = "+s)
		}
		return "{" + strings.Join(parts, ", ") + "}", nil
	default:
		b, err := json.Marshal(fmt.Sprint(t))
		if err != nil {
			return "", err
		}
		return string(b), nil
	}
}

func cloneConfig(in ConfigMap) ConfigMap {
	out := make(ConfigMap, len(in))
	for k, v := range in {
		switch t := v.(type) {
		case map[string]any:
			out[k] = map[string]any(cloneConfig(ConfigMap(t)))
		case []any:
			cp := make([]any, len(t))
			copy(cp, t)
			out[k] = cp
		case []string:
			cp := make([]string, len(t))
			copy(cp, t)
			out[k] = cp
		default:
			out[k] = v
		}
	}
	return out
}

func asStringMap(v any) (map[string]any, bool) {
	switch t := v.(type) {
	case map[string]any:
		return t, true
	case ConfigMap:
		return map[string]any(t), true
	default:
		return nil, false
	}
}
