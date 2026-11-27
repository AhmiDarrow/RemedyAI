package httpapi

import (
	"errors"
	"io"
	"log"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

// This file is what `remedy config|settings|auth` reuse from the runtime, so
// the CLI reads and writes settings exactly as Settings / PUT /api/settings do.

// cliSettingsServer is a Server with only what the settings path reads. Its
// messenger gateway exists but is never started, so a settings change from the
// CLI re-registers channels without a short-lived process polling them.
func cliSettingsServer(home, version string) *Server {
	return &Server{
		homeDir: home,
		version: version,
		messengerGW: gateway.New(gateway.Config{
			HomeDir:           ResolveHomeDir(home),
			HeartbeatInterval: 60 * time.Second,
			RateLimitPerMin:   60,
		}),
	}
}

// quietLog drops the runtime's log lines while fn runs: they are written for
// the runtime's log file, not for a terminal.
func quietLog(fn func()) {
	prev := log.Writer()
	log.SetOutput(io.Discard)
	defer log.SetOutput(prev)
	fn()
}

// CLISettingsSnapshot is GET /api/settings for home (no secrets). An empty
// version reports the runtime's own.
func CLISettingsSnapshot(home, version string) map[string]any {
	if strings.TrimSpace(version) == "" {
		version = Version
	}
	var out map[string]any
	quietLog(func() { out = cliSettingsServer(home, version).settingsPayload() })
	return out
}

// ErrSettingsRejected marks a patch the settings path refused (bad value,
// unknown keys). Anything else from CLIApplySettings is a write failure.
var ErrSettingsRejected = errors.New("settings rejected")

type cliRejected struct{ msg string }

func (e cliRejected) Error() string        { return e.msg }
func (e cliRejected) Is(target error) bool { return target == ErrSettingsRejected }

// CLIApplySettings runs PUT /api/settings's apply path for home: same key
// filter, normalisers, validation, secret store and config.toml writer.
func CLIApplySettings(home string, patch map[string]any) (map[string]any, error) {
	if len(patch) == 0 {
		return nil, cliRejected{"updates must be a non-empty object"}
	}
	var (
		out map[string]any
		err error
	)
	quietLog(func() { out, err = cliSettingsServer(home, Version).applySettingsUpdate(patch) })
	var ve settingsValueError
	if errors.As(err, &ve) {
		return nil, cliRejected{ve.msg}
	}
	return out, err
}

// SettableSettingsKeys is every key PUT /api/settings accepts, sorted.
func SettableSettingsKeys() []string {
	out := make([]string, 0, len(settableKeys))
	for k := range settableKeys {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// IsSettableSettingsKey reports whether PUT /api/settings accepts key.
func IsSettableSettingsKey(key string) bool {
	_, ok := settableKeys[key]
	return ok
}

// CLIProvider is one provider as `remedy auth` needs it.
type CLIProvider struct {
	Label   string
	Auth    []string
	EnvKeys []string
}

// CLIProviderCatalog is the provider catalog (built-ins plus saved custom
// endpoints) keyed by id.
func CLIProviderCatalog(home string) map[string]CLIProvider {
	merged := mergedProviderCatalog(LoadConfig(home))
	out := make(map[string]CLIProvider, len(merged))
	for id, m := range merged {
		out[id] = CLIProvider{
			Label:   m.Label,
			Auth:    append([]string(nil), m.Auth...),
			EnvKeys: append([]string(nil), m.EnvKeys...),
		}
	}
	return out
}

// CLIStoreProviderKey files an API key the way Settings does: under the
// provider its prefix names, refusing Claude subscription tokens. It returns
// the provider the key was filed under.
func CLIStoreProviderKey(home, provider, key string) (string, error) {
	target, err := storeProviderAPIKey(home, provider, strings.TrimSpace(key))
	var ve settingsValueError
	if errors.As(err, &ve) {
		return "", cliRejected{ve.msg}
	}
	return target, err
}

// XaiStatusField is one row of `remedy auth status xai`.
type XaiStatusField struct {
	Name  string
	Value any
}

// CLIXaiStatus is the public xAI credential status (never a token), in the
// order Python's to_public_dict listed it.
func CLIXaiStatus(home string) []XaiStatusField {
	pub := loadXaiCredentials(home).toPublic(home)
	order := []string{
		"provider", "auth_method", "connected", "has_api_key", "has_oauth",
		"expires_at", "credentials_encoding", "credentials_encoding_warning",
	}
	out := make([]XaiStatusField, 0, len(order))
	for _, k := range order {
		if v, ok := pub[k]; ok {
			out = append(out, XaiStatusField{k, v})
		}
	}
	return out
}

// CLIXaiClear removes auth/xai.json.
func CLIXaiClear(home string) { clearXaiCredentials(home) }

// CLIXaiSaveAPIKey stores a console key in auth/xai.json and reports whether
// xAI now counts as connected.
func CLIXaiSaveAPIKey(home, key string) (bool, error) {
	creds, err := saveXaiAPIKey(home, key)
	if err != nil {
		return false, err
	}
	return creds.connected(), nil
}

// CLIXaiStartLogin starts the device-code OAuth flow; a goroutine polls the
// token endpoint and saves the credentials when the owner approves.
func CLIXaiStartLogin(home string) (map[string]any, error) { return startXaiDeviceLogin(home) }

// CLIXaiLoginStatus is the state of a device-code session started here.
func CLIXaiLoginStatus(home, sessionID string) map[string]any {
	return xaiLoginStatus(home, sessionID)
}

// CLIResolveConfig is Python resolve_config(home_dir=home): config.toml, then
// REMEDY_* environment overrides, then the env-key provider bootstrap, then
// home_dir. Nothing is written.
func CLIResolveConfig(home string, environ []string) map[string]any {
	cfg := map[string]any(LoadConfig(home))
	if cfg == nil {
		cfg = map[string]any{}
	}
	cliEnvOverrides(cfg, environ)
	cliEnvProviderBootstrap(cfg, environ)
	cfg["home_dir"] = home
	return cfg
}

func cliEnviron(environ []string) map[string]string {
	out := make(map[string]string, len(environ))
	for _, kv := range environ {
		if k, v, ok := strings.Cut(kv, "="); ok {
			out[k] = v
		}
	}
	return out
}

// cliEnvOverrides is Python load_env_overrides: REMEDY_NAME=x → name = x,
// REMEDY_A__B=x → [a] b = x. REMEDY_CLI_VERSION is launcher plumbing, not
// configuration, and is left out.
func cliEnvOverrides(cfg map[string]any, environ []string) {
	keys := make([]string, 0, len(environ))
	env := cliEnviron(environ)
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if !strings.HasPrefix(key, "REMEDY_") || key == "REMEDY_CLI_VERSION" {
			continue
		}
		parts := strings.Split(strings.ToLower(key[len("REMEDY_"):]), "__")
		cur := cfg
		for _, part := range parts[:len(parts)-1] {
			next, ok := cur[part].(map[string]any)
			if !ok {
				next = map[string]any{}
				cur[part] = next
			}
			cur = next
		}
		cur[parts[len(parts)-1]] = cliCoerceEnv(env[key])
	}
}

// cliCoerceEnv is Python config._coerce.
func cliCoerceEnv(v string) any {
	switch strings.ToLower(v) {
	case "true", "yes", "1":
		return true
	case "false", "no", "0":
		return false
	}
	if n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64); err == nil {
		return n
	}
	if f, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil && !math.IsInf(f, 0) && !math.IsNaN(f) {
		return f
	}
	return v
}

// cliEnvProviderBootstrap is Python apply_env_provider_bootstrap: on a config
// with no key that is still on the factory default, an env key picks the
// provider (xAI first), else the free demo.
func cliEnvProviderBootstrap(cfg map[string]any, environ []string) {
	env := cliEnviron(environ)
	get := func(k string) string { return strings.TrimSpace(env[k]) }
	if strings.TrimSpace(cfgString(ConfigMap(cfg), "llm_api_key", "")) != "" {
		return
	}
	provider := strings.ToLower(strings.TrimSpace(cfgString(ConfigMap(cfg), "llm_provider", "")))
	if provider != "" && provider != "openai" {
		return
	}
	set := func(pid, model string) {
		p, m, u := normalizeLLMSettings(pid, model, "")
		cfg["llm_provider"], cfg["llm_model"], cfg["llm_base_url"] = p, m, u
	}
	if get("XAI_API_KEY") != "" || get("REMEDY_XAI_API_KEY") != "" {
		set("xai", "")
		return
	}
	if get("REMEDY_LLM_API_KEY") == "" {
		switch strings.ToLower(get("REMEDY_PREFER_OLLAMA")) {
		case "1", "true", "yes":
			probe := detectOllama("", 1500*time.Millisecond)
			if probe["available"] == true {
				model := ""
				if names, ok := probe["models"].([]string); ok && len(names) > 0 {
					model = names[0]
				}
				cfg["llm_provider"] = "ollama"
				cfg["llm_model"] = model
				cfg["llm_base_url"] = firstNonEmpty(fmtString(probe["base_url"]), providerCatalog["ollama"].BaseURL)
				return
			}
		}
	}
	for _, pair := range [][2]string{
		{"GROQ_API_KEY", "groq"}, {"MISTRAL_API_KEY", "mistral"}, {"DEEPSEEK_API_KEY", "deepseek"},
		{"OPENROUTER_API_KEY", "openrouter"}, {"POE_API_KEY", "poe"}, {"REMEDY_POE_API_KEY", "poe"},
		{"ANTHROPIC_API_KEY", "anthropic"}, {"OPENAI_API_KEY", "openai"},
		{"GOOGLE_API_KEY", "google"}, {"GEMINI_API_KEY", "google"},
	} {
		if get(pair[0]) != "" {
			set(pair[1], "")
			return
		}
	}
	switch strings.ToLower(get("REMEDY_DEMO_DISABLED")) {
	case "1", "true", "yes":
		return
	}
	prefer := get("REMEDY_PREFER_DEMO")
	if prefer == "" {
		prefer = "1"
	}
	switch strings.ToLower(prefer) {
	case "0", "false", "no":
		return
	}
	model := ""
	if cfgString(ConfigMap(cfg), "llm_provider", "") == "demo" {
		model = cfgString(ConfigMap(cfg), "llm_model", "")
	}
	set("demo", model)
}
