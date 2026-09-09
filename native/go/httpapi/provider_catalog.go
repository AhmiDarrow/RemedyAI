package httpapi

import (
	"fmt"
	"os"
	"sort"
	"strings"
)

// Catalog model row (curated fallback for pickers).
type catalogModel struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Vision *bool  `json:"vision,omitempty"`
}

type providerMeta struct {
	Label       string
	BaseURL     string
	Auth        []string
	EnvKeys     []string
	ShowBaseURL bool
	Advanced    bool
	FreeTier    string
	Badge       string
	LimitsBlurb string
	PrivacyNote string
	KeyDocsURL  string
	Models      []catalogModel
	UserDefined bool
	Flavour     string
}

// Builtin provider order matches Python PROVIDER_CATALOG insertion order.
var providerCatalogOrder = []string{
	"demo", "openai", "anthropic", "google", "deepseek", "xai", "groq",
	"mistral", "openrouter", "poe", "ollama", "rmb", "llamacpp", "custom",
}

var providerCatalog = map[string]providerMeta{
	"demo": {
		Label: "Demo (Free)", BaseURL: "https://api.llm7.io/v1",
		Auth: []string{"none"}, FreeTier: "instant", Badge: "No signup",
		LimitsBlurb: "Rate-limited guest chat only (third-party). Curated stable models — " +
			"not the full gateway catalog. Add a real provider for agents / vision.",
		PrivacyNote: "Chat is sent to a free third-party API (not Remedy cloud). Prefer Ollama for private local use.",
		KeyDocsURL:  "https://llm7.io/",
		Models: []catalogModel{
			{ID: "codestral-latest", Name: "Codestral demo", Vision: boolPtr(false)},
			{ID: "gemini-3.1-flash-lite", Name: "Gemini Flash Lite demo", Vision: boolPtr(false)},
			{ID: "gpt-oss:20b", Name: "GPT-OSS 20B demo", Vision: boolPtr(false)},
		},
	},
	"openai": {
		Label: "OpenAI", BaseURL: "https://api.openai.com/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"OPENAI_API_KEY", "REMEDY_LLM_API_KEY"},
		FreeTier: "none", KeyDocsURL: "https://platform.openai.com/api-keys",
		Models: []catalogModel{
			{ID: "gpt-4o-mini", Name: "GPT-4o Mini", Vision: boolPtr(true)},
			{ID: "gpt-4o", Name: "GPT-4o", Vision: boolPtr(true)},
			{ID: "gpt-4.1-mini", Name: "GPT-4.1 Mini", Vision: boolPtr(true)},
			{ID: "o4-mini", Name: "o4-mini", Vision: boolPtr(false)},
		},
	},
	"anthropic": {
		Label: "Anthropic", BaseURL: "https://api.anthropic.com/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"ANTHROPIC_API_KEY"},
		FreeTier: "none", KeyDocsURL: "https://console.anthropic.com/settings/keys",
		LimitsBlurb: "Uses prepaid Anthropic API credits (console.anthropic.com). " +
			"A Claude Pro / Max / Claude Code weekly limit does not pay for " +
			"this. Do not paste sk-ant-oat login tokens here.",
		Models: []catalogModel{
			{ID: "claude-opus-5", Name: "Claude Opus 5", Vision: boolPtr(true)},
			{ID: "claude-sonnet-5", Name: "Claude Sonnet 5", Vision: boolPtr(true)},
			{ID: "claude-haiku-4-5", Name: "Claude Haiku 4.5", Vision: boolPtr(true)},
			{ID: "claude-opus-4-8", Name: "Claude Opus 4.8", Vision: boolPtr(true)},
			{ID: "claude-sonnet-4-6", Name: "Claude Sonnet 4.6", Vision: boolPtr(true)},
			{ID: "claude-fable-5-1", Name: "Claude Fable 5.1", Vision: boolPtr(true)},
		},
	},
	"google": {
		Label:   "Google AI (Gemini)",
		BaseURL: "https://generativelanguage.googleapis.com/v1beta/openai",
		Auth:    []string{"api_key"}, EnvKeys: []string{"GOOGLE_API_KEY", "GEMINI_API_KEY"},
		FreeTier: "free_key", Badge: "Free key",
		LimitsBlurb: "Generous free tier via AI Studio (region limits may apply).",
		KeyDocsURL:  "https://aistudio.google.com/app/apikey",
		Models: []catalogModel{
			{ID: "gemini-2.5-flash", Name: "Gemini 2.5 Flash", Vision: boolPtr(true)},
			{ID: "gemini-2.0-flash", Name: "Gemini 2.0 Flash", Vision: boolPtr(true)},
			{ID: "gemini-1.5-pro", Name: "Gemini 1.5 Pro", Vision: boolPtr(true)},
		},
	},
	"deepseek": {
		Label: "DeepSeek", BaseURL: "https://api.deepseek.com/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"DEEPSEEK_API_KEY"},
		FreeTier: "none", KeyDocsURL: "https://platform.deepseek.com/api_keys",
		Models: []catalogModel{
			{ID: "deepseek-v4-flash", Name: "DeepSeek V4 Flash", Vision: boolPtr(false)},
			{ID: "deepseek-v4-pro", Name: "DeepSeek V4 Pro", Vision: boolPtr(false)},
		},
	},
	"xai": {
		Label: "xAI (Grok)", BaseURL: "https://api.x.ai/v1",
		Auth: []string{"oauth", "api_key"}, EnvKeys: []string{"XAI_API_KEY", "REMEDY_XAI_API_KEY"},
		FreeTier: "none", KeyDocsURL: "https://console.x.ai/team/default/api-keys",
		Models: []catalogModel{
			{ID: "grok-4.5", Name: "Grok 4.5", Vision: boolPtr(true)},
			{ID: "grok-4.3", Name: "Grok 4.3", Vision: boolPtr(true)},
			{ID: "grok-4", Name: "Grok 4", Vision: boolPtr(true)},
		},
	},
	"groq": {
		Label: "Groq", BaseURL: "https://api.groq.com/openai/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"GROQ_API_KEY"},
		FreeTier: "free_key", Badge: "Free key",
		LimitsBlurb: "Very fast free tier — great for trying agent tools.",
		KeyDocsURL:  "https://console.groq.com/keys",
		Models: []catalogModel{
			{ID: "llama-3.3-70b-versatile", Name: "Llama 3.3 70B", Vision: boolPtr(false)},
			{ID: "llama-3.1-8b-instant", Name: "Llama 3.1 8B Instant", Vision: boolPtr(false)},
			{ID: "mixtral-8x7b-32768", Name: "Mixtral 8x7B", Vision: boolPtr(false)},
		},
	},
	"mistral": {
		Label: "Mistral", BaseURL: "https://api.mistral.ai/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"MISTRAL_API_KEY"},
		FreeTier: "free_key", Badge: "Free key",
		LimitsBlurb: "Free Experiment plan (no card typical).",
		KeyDocsURL:  "https://console.mistral.ai/api-keys",
		Models: []catalogModel{
			{ID: "mistral-small-latest", Name: "Mistral Small", Vision: boolPtr(false)},
			{ID: "mistral-large-latest", Name: "Mistral Large", Vision: boolPtr(false)},
			{ID: "codestral-latest", Name: "Codestral", Vision: boolPtr(false)},
		},
	},
	"openrouter": {
		Label: "OpenRouter", BaseURL: "https://openrouter.ai/api/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"OPENROUTER_API_KEY"},
		FreeTier: "free_key", Badge: "Free key",
		LimitsBlurb: "Free-tier models (ids ending in :free) come and go; live /models " +
			"lists what your key can use. Curated fallbacks below are long-running free chat.",
		KeyDocsURL: "https://openrouter.ai/keys",
		Models: []catalogModel{
			{ID: "openrouter/auto", Name: "OpenRouter Auto"},
			{ID: "openai/gpt-oss-20b:free", Name: "GPT-OSS 20B (free)"},
			{ID: "meta-llama/llama-3.3-70b-instruct:free", Name: "Llama 3.3 70B (free)"},
		},
	},
	"poe": {
		Label: "Poe", BaseURL: "https://api.poe.com/v1",
		Auth: []string{"api_key"}, EnvKeys: []string{"POE_API_KEY", "REMEDY_POE_API_KEY"},
		FreeTier: "none", Badge: "Multi-model",
		LimitsBlurb: "One Poe API key → many frontier bots (Claude, GPT, Gemini, Grok, …). " +
			"Uses your Poe subscription points / add-on balance. " +
			"Live GET /models lists bots your account can call.",
		PrivacyNote: "Chat is sent to Poe (Quora) and routed to the selected bot/provider.",
		KeyDocsURL:  "https://poe.com/api_key",
		Models: []catalogModel{
			{ID: "Claude-Sonnet-4.6", Name: "Claude Sonnet 4.6", Vision: boolPtr(true)},
			{ID: "Claude-Opus-4.7", Name: "Claude Opus 4.7", Vision: boolPtr(true)},
			{ID: "GPT-5.4", Name: "GPT-5.4", Vision: boolPtr(true)},
			{ID: "Gemini-3.1-Pro", Name: "Gemini 3.1 Pro", Vision: boolPtr(true)},
			{ID: "Grok-4", Name: "Grok 4", Vision: boolPtr(true)},
		},
	},
	"ollama": {
		Label: "Ollama (local)", BaseURL: "http://127.0.0.1:11434/v1",
		Auth: []string{"none"}, FreeTier: "local", Badge: "Local",
		LimitsBlurb: "Fully free on your machine. Install Ollama and pull a model.",
		KeyDocsURL:  "https://ollama.com/download",
		Models:      nil,
	},
	"rmb": {
		Label: "RMB (local agent)", BaseURL: "http://127.0.0.1:8787/v1",
		Auth: []string{"none"}, ShowBaseURL: true, FreeTier: "local", Badge: "Local · Agent",
		LimitsBlurb: "Remedy Muscle Bridge — built-in local chat host (llama.cpp) optimized " +
			"for coding and tool use. Manage under Settings → RMB. Fully private on this PC.",
		PrivacyNote: "Chat stays on this PC. Powered by llama.cpp under the RMB brand.",
		Models: []catalogModel{
			{ID: "Qwen2.5-Coder-7B-Instruct-Q4_K_M", Name: "Qwen2.5 Coder 7B"},
			{ID: "Qwen2.5-Coder-14B-Instruct-Q4_K_M", Name: "Qwen2.5 Coder 14B"},
			{ID: "default", Name: "Default (loaded model)"},
		},
	},
	"llamacpp": {
		Label: "llama.cpp (manual URL)", BaseURL: "http://127.0.0.1:8080/v1",
		Auth: []string{"none"}, ShowBaseURL: true, Advanced: true,
		FreeTier: "local", Badge: "Local",
		LimitsBlurb: "Point at any OpenAI-compatible llama-server. Prefer Settings → RMB for managed local agent.",
		Models:      []catalogModel{{ID: "default", Name: "Default"}},
	},
	"custom": {
		Label: "Custom / OpenAI-compatible", BaseURL: "http://127.0.0.1:5001/v1",
		Auth: []string{"api_key"}, ShowBaseURL: true, FreeTier: "none",
		Models: []catalogModel{{ID: "default", Name: "Default (custom endpoint)"}},
	},
}

type freeOptionSeed struct {
	ID    string
	Tier  string
	Title string
	Blurb string
}

var freeProviderOptions = []freeOptionSeed{
	{ID: "demo", Tier: "instant", Title: "Start instantly (Demo)",
		Blurb: "No account. Limited rate/quality. Chat leaves your PC to a free third-party gateway."},
	{ID: "google", Tier: "free_key", Title: "Google Gemini",
		Blurb: "Free AI Studio key — strong free multimodal models."},
	{ID: "groq", Tier: "free_key", Title: "Groq",
		Blurb: "Free key — very fast open models."},
	{ID: "openrouter", Tier: "free_key", Title: "OpenRouter free models",
		Blurb: "Free key — pick models with :free suffix."},
	{ID: "mistral", Tier: "free_key", Title: "Mistral",
		Blurb: "Free Experiment plan key."},
	{ID: "rmb", Tier: "local", Title: "RMB (local agent)",
		Blurb: "Built-in llama.cpp host for coding + tools — private, no API key."},
	{ID: "ollama", Tier: "local", Title: "Ollama (on this PC)",
		Blurb: "Install locally for private, unlimited free use."},
}

func boolPtr(v bool) *bool { return &v }

func demoDisabled() bool {
	flag := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_DEMO_DISABLED")))
	return flag == "1" || flag == "true" || flag == "yes"
}

func catalogModelsMaps(models []catalogModel) []map[string]any {
	out := make([]map[string]any, 0, len(models))
	for _, m := range models {
		row := map[string]any{"id": m.ID, "name": m.Name}
		if m.Vision != nil {
			row["vision"] = *m.Vision
		}
		out = append(out, row)
	}
	return out
}

func publicProviderRow(pid string, meta providerMeta, cfg ConfigMap) map[string]any {
	auth := append([]string{}, meta.Auth...)
	if pid == "ollama" || pid == "demo" || pid == "rmb" || pid == "llamacpp" {
		auth = []string{"none"}
	}
	models := catalogModelsMaps(meta.Models)
	defaultModel := "default"
	if len(meta.Models) > 0 {
		defaultModel = meta.Models[0].ID
	}
	name := meta.Label
	baseURL := meta.BaseURL
	if pid == "custom" {
		savedURL := strings.TrimSpace(cfgString(cfg, "llm_base_url", ""))
		provNow := strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", "")))
		if savedURL != "" && provNow == "custom" {
			baseURL = savedURL
		}
		if customName := strings.TrimSpace(cfgString(cfg, "custom_llm_name", "")); customName != "" {
			name = customName
		}
	}
	out := map[string]any{
		"id":            pid,
		"name":          name,
		"base_url":      baseURL,
		"models":        models,
		"default_model": defaultModel,
		"auth":          auth,
		"oauth":         containsString(auth, "oauth"),
		"env_keys":      append([]string{}, meta.EnvKeys...),
		"show_base_url": meta.ShowBaseURL,
		"advanced":      meta.Advanced,
		"key_docs_url":  nilIfEmpty(meta.KeyDocsURL),
		"free_tier":     firstNonEmpty(meta.FreeTier, "none"),
		"badge":         nilIfEmpty(meta.Badge),
		"limits_blurb":  nilIfEmpty(meta.LimitsBlurb),
		"privacy_note":  nilIfEmpty(meta.PrivacyNote),
		"user_defined":  meta.UserDefined,
		"flavour":       nilIfEmpty(meta.Flavour),
	}
	return out
}

func nilIfEmpty(s string) any {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return s
}

func containsString(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

// Merge built-in catalog with config custom_providers (user-defined endpoints).
func mergedProviderCatalog(cfg ConfigMap) map[string]providerMeta {
	out := make(map[string]providerMeta, len(providerCatalog)+4)
	for k, v := range providerCatalog {
		out[k] = v
	}
	raw, ok := asStringMap(cfg["custom_providers"])
	if !ok || raw == nil {
		return out
	}
	for pid, specAny := range raw {
		p := strings.ToLower(strings.TrimSpace(pid))
		if !strings.HasPrefix(p, "custom-") {
			continue
		}
		spec, ok := asStringMap(specAny)
		if !ok {
			continue
		}
		base := strings.TrimRight(strings.TrimSpace(fmtString(spec["base_url"])), "/")
		if base == "" {
			continue
		}
		label := strings.TrimSpace(fmtString(spec["label"]))
		if label == "" {
			label = strings.TrimPrefix(p, "custom-")
		}
		flavour := strings.ToLower(strings.TrimSpace(fmtString(spec["flavour"])))
		switch flavour {
		case "openai", "anthropic", "ollama", "gemini":
		default:
			flavour = "openai"
		}
		auth := strings.ToLower(strings.TrimSpace(fmtString(spec["auth"])))
		if auth != "none" {
			auth = "api_key"
		}
		out[p] = providerMeta{
			Label: label, BaseURL: base, Auth: []string{auth},
			ShowBaseURL: true, UserDefined: true, Flavour: flavour, FreeTier: "none",
			LimitsBlurb: "Saved custom endpoint. Models come from the host itself.",
			Models:      nil,
		}
	}
	return out
}

func fmtString(v any) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return strings.TrimSpace(fmt.Sprint(v))
}

func publicProviderCatalog(cfg ConfigMap) []map[string]any {
	merged := mergedProviderCatalog(cfg)
	items := make([]map[string]any, 0, len(merged))
	seen := map[string]struct{}{}
	for _, pid := range providerCatalogOrder {
		meta, ok := merged[pid]
		if !ok {
			continue
		}
		items = append(items, publicProviderRow(pid, meta, cfg))
		seen[pid] = struct{}{}
	}
	// User-defined endpoints after builtins (stable by id).
	extra := make([]string, 0)
	for pid := range merged {
		if _, ok := seen[pid]; ok {
			continue
		}
		extra = append(extra, pid)
	}
	sortStrings(extra)
	for _, pid := range extra {
		items = append(items, publicProviderRow(pid, merged[pid], cfg))
	}
	return items
}

func freeOptionsPublic() []map[string]any {
	out := make([]map[string]any, 0, len(freeProviderOptions))
	for _, item := range freeProviderOptions {
		if item.ID == "demo" && demoDisabled() {
			continue
		}
		meta := providerCatalog[item.ID]
		models := catalogModelsMaps(meta.Models)
		defaultModel := "default"
		if len(meta.Models) > 0 {
			defaultModel = meta.Models[0].ID
		}
		out = append(out, map[string]any{
			"id":            item.ID,
			"tier":          item.Tier,
			"title":         firstNonEmpty(item.Title, meta.Label, item.ID),
			"blurb":         firstNonEmpty(item.Blurb, meta.LimitsBlurb),
			"badge":         nilIfEmpty(meta.Badge),
			"name":          firstNonEmpty(meta.Label, item.ID),
			"base_url":      meta.BaseURL,
			"auth":          append([]string{}, meta.Auth...),
			"key_docs_url":  nilIfEmpty(meta.KeyDocsURL),
			"limits_blurb":  nilIfEmpty(meta.LimitsBlurb),
			"privacy_note":  nilIfEmpty(meta.PrivacyNote),
			"default_model": defaultModel,
			"models":        models,
			"free_tier":     firstNonEmpty(meta.FreeTier, item.Tier),
		})
	}
	return out
}

func sortStrings(ss []string) {
	sort.Strings(ss)
}

func catalogModelsForProvider(provider string) []map[string]any {
	prov := strings.ToLower(strings.TrimSpace(provider))
	if prov == "" {
		prov = "openai"
	}
	meta, ok := providerCatalog[prov]
	if !ok {
		meta = providerCatalog["custom"]
	}
	out := make([]map[string]any, 0, len(meta.Models))
	for _, m := range meta.Models {
		out = append(out, map[string]any{
			"id":       m.ID,
			"name":     firstNonEmpty(m.Name, m.ID),
			"provider": prov,
			"default":  false,
		})
	}
	return out
}
