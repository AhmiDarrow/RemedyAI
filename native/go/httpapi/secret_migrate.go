package httpapi

import (
	"fmt"
	"log"
	"sort"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// Legacy layouts kept messenger and provider credentials in config.toml in the
// clear. WriteConfig has scrubbed them on save for a while, but a config that
// was never re-saved still holds them, and resolveMessengerSecret still reads
// them — so the plaintext copy stays authoritative forever. This moves them
// into the secret store once and takes them out of the config file.

// configSecretField reports whether a channel-table key holds a credential.
// Same rule scrubConfigSecrets uses on save, so the two cannot drift apart.
func configSecretField(key string) bool {
	k := strings.ToLower(strings.TrimSpace(key))
	switch k {
	case "bot_token", "access_token", "app_token", "app_password",
		"app_secret", "verify_token", "signing_secret":
		return true
	}
	return strings.HasSuffix(k, "_token") ||
		strings.HasSuffix(k, "_password") ||
		strings.HasSuffix(k, "_secret")
}

// configSecretValue returns a trimmed non-empty string value, else "".
func configSecretValue(v any) string {
	if v == nil {
		return ""
	}
	s, ok := v.(string)
	if !ok {
		s = fmt.Sprint(v)
	}
	s = strings.TrimSpace(s)
	if s == "" || s == "<nil>" {
		return ""
	}
	return s
}

// migrateConfigSecrets moves plaintext credentials out of config.toml into the
// secret store and rewrites the config without them. It returns the store keys
// it moved (never the values). Nothing to move means the config is not touched.
func migrateConfigSecrets(homeDir string) ([]string, error) {
	path := FindConfigPath(homeDir)
	if path == "" {
		return nil, nil
	}
	home := ResolveHomeDir(homeDir)
	if home == "" {
		return nil, nil
	}
	cfg := LoadConfig(homeDir)
	if len(cfg) == 0 {
		return nil, nil
	}

	pending := map[string]string{} // store key -> value
	strip := func(table map[string]any, field string) { delete(table, field) }

	for name, raw := range cfg {
		table, ok := asStringMap(raw)
		if !ok {
			continue
		}
		channel := strings.ToLower(strings.TrimSpace(name))
		for field, v := range table {
			if !configSecretField(field) {
				continue
			}
			value := configSecretValue(v)
			if value == "" {
				// Already scrubbed to "" on an earlier save; drop the husk.
				strip(table, field)
				continue
			}
			pending["ch:"+channel+":"+strings.ToLower(strings.TrimSpace(field))] = value
			strip(table, field)
		}
		cfg[name] = map[string]any(table)
	}

	// Legacy provider key holders that predate the store.
	if keys, ok := asStringMap(cfg["provider_keys"]); ok {
		for provider, v := range keys {
			if value := configSecretValue(v); value != "" {
				pending[strings.ToLower(strings.TrimSpace(provider))] = value
			}
		}
		delete(cfg, "provider_keys")
	}
	if value := configSecretValue(cfg["llm_api_key"]); value != "" {
		provider := strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", "")))
		if provider == "" {
			provider = "default"
		}
		pending[provider] = value
		delete(cfg, "llm_api_key")
	}

	if len(pending) == 0 {
		return nil, nil
	}

	moved := make([]string, 0, len(pending))
	for storeKey, value := range pending {
		// Never overwrite a key the owner already moved into the store.
		if existing := strings.TrimSpace(secret.GetProviderSecret(home, storeKey)); existing != "" {
			moved = append(moved, storeKey)
			continue
		}
		if err := secret.SetProviderSecret(home, storeKey, value); err != nil {
			return nil, fmt.Errorf("move %s into the secret store: %w", storeKey, err)
		}
		moved = append(moved, storeKey)
	}
	sort.Strings(moved)

	if err := WriteConfig(path, cfg); err != nil {
		return moved, fmt.Errorf("rewrite config without credentials: %w", err)
	}
	return moved, nil
}

// Migration runs once per home per process. Keyed by home rather than a plain
// sync.Once so a second Server (tests, a re-bound sidecar) still migrates its
// own home.
var (
	secretMigrateMu    sync.Mutex
	secretMigratedHome = map[string]struct{}{}
)

// migrateSecretsOnce runs migrateConfigSecrets at most once per home and tells
// the owner what moved, so a credential that stops working after an upgrade
// has a visible cause.
func (s *Server) migrateSecretsOnce() {
	if s == nil {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	secretMigrateMu.Lock()
	_, done := secretMigratedHome[home]
	if !done {
		secretMigratedHome[home] = struct{}{}
	}
	secretMigrateMu.Unlock()
	if done {
		return
	}

	moved, err := migrateConfigSecrets(s.homeDir)
	if err != nil {
		log.Printf("secret store: could not move credentials out of config.toml: %v", err)
		return
	}
	if len(moved) > 0 {
		log.Printf("secret store: moved %d credential(s) out of config.toml into %s (%s). "+
			"The config file no longer holds them.",
			len(moved), secret.ProviderKeysPath(home), strings.Join(moved, ", "))
	}
	secret.WarnPlaintextAtRest(home)
}
