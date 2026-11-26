package httpapi

import (
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
)

// Provider base URLs for settings normalize. No model ids: the model always
// comes from the provider's models endpoint (see endpoint_models.go).
var providerDefaults = map[string]struct {
	baseURL string
	model   string
	noAuth  bool
}{
	"demo":       {baseURL: "https://api.llm7.io/v1", model: "", noAuth: true},
	"openai":     {baseURL: "https://api.openai.com/v1", model: ""},
	"anthropic":  {baseURL: "https://api.anthropic.com/v1", model: ""},
	"google":     {baseURL: "https://generativelanguage.googleapis.com/v1beta/openai", model: ""},
	"deepseek":   {baseURL: "https://api.deepseek.com/v1", model: ""},
	"xai":        {baseURL: "https://api.x.ai/v1", model: ""},
	"groq":       {baseURL: "https://api.groq.com/openai/v1", model: ""},
	"mistral":    {baseURL: "https://api.mistral.ai/v1", model: ""},
	"openrouter": {baseURL: "https://openrouter.ai/api/v1", model: ""},
	"poe":        {baseURL: "https://api.poe.com/v1", model: ""},
	"ollama":     {baseURL: "http://127.0.0.1:11434/v1", model: "", noAuth: true},
	"custom":     {baseURL: "http://127.0.0.1:5001/v1", model: ""},
	"rmb":        {baseURL: "http://127.0.0.1:8787/v1", model: "", noAuth: true},
	"llamacpp":   {baseURL: "http://127.0.0.1:8080/v1", model: "", noAuth: true},
}

// providerShowsBaseURL reports whether the owner may type a custom endpoint
// for this provider (Ollama, llama.cpp, RMB, custom, or a user-defined id).
// Hidden-URL providers (Anthropic, OpenAI, xAI, …) always use the catalog URL.
func providerShowsBaseURL(provider string) bool {
	p := strings.ToLower(strings.TrimSpace(provider))
	switch p {
	case "ollama", "llamacpp", "rmb", "custom":
		return true
	}
	if meta, ok := providerCatalog[p]; ok {
		return meta.ShowBaseURL || meta.UserDefined
	}
	// Unknown / user-defined endpoint ids stay typable.
	return p != ""
}

// catalogBaseURL is the built-in default endpoint for a known provider.
func catalogBaseURL(provider string) string {
	p := strings.ToLower(strings.TrimSpace(provider))
	if meta, ok := providerCatalog[p]; ok {
		if u := strings.TrimSpace(meta.BaseURL); u != "" {
			return u
		}
	}
	if def, ok := providerDefaults[p]; ok {
		return def.baseURL
	}
	return ""
}

// endpointKey is host:port for a base URL. Port is required so a loopback
// mock (127.0.0.1:9) is not mistaken for Ollama or RMB on another port.
func endpointKey(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Hostname() == "" {
		return ""
	}
	host := strings.ToLower(u.Hostname())
	port := u.Port()
	if port == "" {
		if strings.EqualFold(u.Scheme, "https") {
			port = "443"
		} else {
			port = "80"
		}
	}
	return host + ":" + port
}

// foreignCatalogURL reports whether rawURL is a built-in catalog endpoint
// for a *different* provider (a leftover llm_base_url after a switch).
func foreignCatalogURL(provider, rawURL string) bool {
	key := endpointKey(rawURL)
	if key == "" {
		return false
	}
	p := strings.ToLower(strings.TrimSpace(provider))
	if mine := endpointKey(catalogBaseURL(p)); mine != "" && key == mine {
		return false
	}
	for id, meta := range providerCatalog {
		if id == p {
			continue
		}
		if h := endpointKey(meta.BaseURL); h != "" && key == h {
			return true
		}
	}
	for id, def := range providerDefaults {
		if id == p {
			continue
		}
		if h := endpointKey(def.baseURL); h != "" && key == h {
			return true
		}
	}
	return false
}

// effectiveBaseURL is the endpoint a client should call. Hidden-URL
// providers ignore a leftover llm_base_url that belongs to another
// provider (OpenRouter still stored under Anthropic) and otherwise
// use the catalog URL when none is set. Typable providers keep a
// custom URL. A mock or private gateway that is not another provider's
// catalog host is left alone so live listing can hit that endpoint.
func effectiveBaseURL(provider, rawURL string) string {
	raw := strings.TrimSpace(rawURL)
	if providerShowsBaseURL(provider) {
		if raw != "" {
			return raw
		}
		return catalogBaseURL(provider)
	}
	cat := catalogBaseURL(provider)
	if raw == "" || foreignCatalogURL(provider, raw) {
		if cat != "" {
			return cat
		}
	}
	if raw != "" {
		return raw
	}
	return cat
}

func normalizeLLMSettings(provider, model, baseURL string) (string, string, string) {
	prov := strings.ToLower(strings.TrimSpace(provider))
	if prov == "" {
		prov = "openai"
	}
	if prov == "claude_code" {
		prov = "anthropic"
		if strings.HasPrefix(strings.ToLower(strings.TrimSpace(baseURL)), "claude-code:") {
			baseURL = ""
		}
	}
	def, ok := providerDefaults[prov]
	if !ok {
		def = providerDefaults["custom"]
	}
	urlOut := effectiveBaseURL(prov, baseURL)
	modelOut := strings.TrimSpace(model)
	if modelOut == "" {
		modelOut = def.model
	}
	return prov, modelOut, urlOut
}

func defaultModelForProvider(provider string) string {
	def, ok := providerDefaults[strings.ToLower(strings.TrimSpace(provider))]
	if !ok {
		return ""
	}
	return def.model
}

func isLocalURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" {
		return false
	}
	host := u.Hostname()
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func providerCredentialsReady(cfg ConfigMap, provider, key string) bool {
	prov := strings.ToLower(strings.TrimSpace(provider))
	base := strings.TrimSpace(cfgString(cfg, "llm_base_url", ""))
	if base == "" {
		base = strings.TrimSpace(os.Getenv("REMEDY_LLM_BASE_URL"))
	}
	if base != "" && isLocalURL(base) {
		if prov == "" || prov == "ollama" || prov == "rmb" || prov == "llamacpp" || prov == "custom" {
			return true
		}
	}
	if prov == "ollama" || prov == "rmb" || prov == "llamacpp" {
		return true
	}
	if prov == "demo" {
		flag := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_DEMO_DISABLED")))
		return flag != "1" && flag != "true" && flag != "yes"
	}
	k := strings.TrimSpace(key)
	if k != "" && !isPlaceholderKey(k) {
		return true
	}
	def, ok := providerDefaults[prov]
	return ok && def.noAuth
}

func isPlaceholderKey(k string) bool {
	switch strings.ToLower(strings.TrimSpace(k)) {
	case "local", "rmb", "unused":
		return true
	default:
		return false
	}
}

func cfgString(cfg ConfigMap, key, def string) string {
	if cfg == nil {
		return def
	}
	v, ok := cfg[key]
	if !ok || v == nil {
		return def
	}
	switch t := v.(type) {
	case string:
		if strings.TrimSpace(t) == "" {
			return def
		}
		return t
	default:
		s := strings.TrimSpace(fmt.Sprint(t))
		if s == "" {
			return def
		}
		return s
	}
}

func cfgBool(cfg ConfigMap, key string, def bool) bool {
	if cfg == nil {
		return def
	}
	v, ok := cfg[key]
	if !ok || v == nil {
		return def
	}
	return coerceBool(v, def)
}

func cfgFloat(cfg ConfigMap, key string, def float64) float64 {
	if cfg == nil {
		return def
	}
	v, ok := cfg[key]
	if !ok || v == nil {
		return def
	}
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		// A settings save decodes numbers this way; without this case a number
		// typed in Settings was dropped and the default came back.
		f, err := t.Float64()
		if err != nil {
			return def
		}
		return f
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(t), 64)
		if err != nil {
			return def
		}
		return f
	default:
		return def
	}
}

func cfgInt(cfg ConfigMap, key string, def int) int {
	if cfg == nil {
		return def
	}
	v, ok := cfg[key]
	if !ok || v == nil {
		return def
	}
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		if n, err := t.Int64(); err == nil {
			return int(n)
		}
		if f, err := t.Float64(); err == nil {
			return int(f)
		}
		return def
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(t))
		if err != nil {
			return def
		}
		return n
	default:
		return def
	}
}

func coerceBool(v any, def bool) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "1", "true", "yes", "on", "enable", "enabled":
			return true
		case "0", "false", "no", "off", "disable", "disabled":
			return false
		default:
			return def
		}
	case int:
		return t != 0
	case int64:
		return t != 0
	case float64:
		return t != 0
	default:
		return def
	}
}
