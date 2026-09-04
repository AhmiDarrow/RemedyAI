package httpapi

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
)

// Minimal provider defaults for settings normalize (catalog subset).
var providerDefaults = map[string]struct {
	baseURL string
	model   string
	noAuth  bool
}{
	"demo":       {baseURL: "https://api.llm7.io/v1", model: "codestral-latest", noAuth: true},
	"openai":     {baseURL: "https://api.openai.com/v1", model: "gpt-4o-mini"},
	"anthropic":  {baseURL: "https://api.anthropic.com/v1", model: "claude-sonnet-4-6"},
	"google":     {baseURL: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.5-flash"},
	"deepseek":   {baseURL: "https://api.deepseek.com/v1", model: "deepseek-v4-flash"},
	"xai":        {baseURL: "https://api.x.ai/v1", model: "grok-4"},
	"groq":       {baseURL: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile"},
	"mistral":    {baseURL: "https://api.mistral.ai/v1", model: "mistral-small-latest"},
	"openrouter": {baseURL: "https://openrouter.ai/api/v1", model: "openrouter/auto"},
	"ollama":     {baseURL: "http://127.0.0.1:11434/v1", model: "", noAuth: true},
	"custom":     {baseURL: "http://127.0.0.1:5001/v1", model: ""},
	"rmb":        {baseURL: "http://127.0.0.1:8741/v1", model: "", noAuth: true},
	"llamacpp":   {baseURL: "http://127.0.0.1:8080/v1", model: "", noAuth: true},
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
	urlOut := strings.TrimSpace(baseURL)
	if urlOut == "" {
		urlOut = def.baseURL
	}
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
