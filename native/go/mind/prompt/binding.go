package prompt

import (
	"net/url"
	"regexp"
	"strings"
)

// binding is the provider/model/base URL one turn runs on
// (prompt_assemble._binding_of after config_to_agent_config).
type binding struct {
	Provider, Model, BaseURL string
}

// resolveBinding follows _agent_config + config_to_agent_config: the
// request wins, then config.toml, then the defaults. The REMEDY_LLM_*
// environment fallbacks are not read; the Go runtime has no such knobs.
func resolveBinding(cfg map[string]any, in assembleInput) binding {
	provider := in.Provider
	if provider == "" {
		provider = cfgStr(cfg, "llm_provider")
	}
	if provider == "" {
		provider = "openai"
	}
	model := in.Model
	if model == "" {
		model = cfgStr(cfg, "llm_model")
	}
	// default_model_for_provider only knows models a live /models call
	// listed in this process; the worker never makes that call.
	base := in.BaseURL
	if base == "" {
		base = cfgStr(cfg, "llm_base_url")
	}
	if base == "" {
		base = "https://api.openai.com/v1"
	}
	return binding{Provider: provider, Model: model, BaseURL: base}
}

// --- token_nanobot.is_local_model and rmb.mode.is_rmb_provider -------------

var localProviders = map[string]bool{"ollama": true, "local": true, "llamacpp": true, "rmb": true, "llama": true}

var cloudProviders = map[string]bool{
	"openai": true, "anthropic": true, "google": true, "deepseek": true, "xai": true, "groq": true,
	"mistral": true, "openrouter": true, "poe": true, "demo": true,
}

var providerFamily = map[string]string{
	"openai": "openai-compat", "xai": "openai-compat", "groq": "openai-compat",
	"openrouter": "openai-compat", "mistral": "openai-compat", "google": "gemini-like",
	"anthropic": "anthropic-like", "deepseek": "deepseek-like", "ollama": "local",
	"demo": "openai-compat", "custom": "openai-compat",
}

// localSizeWindows are _LOCAL_SIZE_WINDOWS and _LOCAL_SIZE_WINDOWS_EXTRA.
// RE2 has no lookahead: "1(?!\d)" is always followed by \s*b there, so the
// lookahead can never fail and is dropped; "b(?!\w)" becomes "b(?:\W|$)",
// which matches exactly when the lookahead would (search, not match).
var localSizeWindows = []*regexp.Regexp{
	regexp.MustCompile(`(?i):\s*(?:0?\.5|1)\s*b(?:\W|$)`),
	regexp.MustCompile(`(?i):\s*(?:2|3|2\.5)\s*b(?:\W|$)`),
	regexp.MustCompile(`(?i):\s*[4-9]\s*b(?:\W|$)`),
	regexp.MustCompile(`(?i):\s*1[0-9]\s*b(?:\W|$)`),
	regexp.MustCompile(`(?i)(?:^|[^0-9])(?:0?\.5|1)\s*b(?:\b|[_-]|\.)`),
	regexp.MustCompile(`(?i)(?:^|[^0-9])(?:2|2\.5|3)\s*b(?:\b|[_-]|\.)`),
	regexp.MustCompile(`(?i)(?:^|[^0-9])[4-9]\s*b(?:\b|[_-]|\.)`),
	regexp.MustCompile(`(?i)(?:^|[^0-9])1[0-9]\s*b(?:\b|[_-]|\.)`),
}

func hasModelSizeWindow(model string) bool {
	ml := strings.ToLower(model)
	for _, re := range localSizeWindows {
		if re.MatchString(ml) {
			return true
		}
	}
	return false
}

func urlHost(raw string) (host string, port string) {
	u, err := url.Parse(raw)
	if err != nil {
		return "", ""
	}
	return strings.ToLower(u.Hostname()), u.Port()
}

func isLoopbackURL(raw string) bool {
	if raw == "" {
		return false
	}
	host, _ := urlHost(raw)
	return host == "localhost" || host == "0.0.0.0" || host == "::1" || strings.HasPrefix(host, "127.")
}

func encodingFamily(provider, model string) string {
	p := strings.ToLower(strings.TrimSpace(provider))
	m := strings.ToLower(strings.TrimSpace(model))
	fam, ok := providerFamily[p]
	if !ok {
		fam = "openai-compat"
	}
	switch {
	case strings.Contains(m, "claude"):
		fam = "anthropic-like"
	case strings.Contains(m, "deepseek"):
		fam = "deepseek-like"
	case strings.Contains(m, "gemini"):
		fam = "gemini-like"
	default:
		for _, x := range []string{"llama", "mistral", "codestral", "ollama", "phi-", "qwen"} {
			if strings.Contains(m, x) {
				fam = "local"
				break
			}
		}
	}
	return fam
}

func isLocalModel(provider, model, baseURL string) bool {
	p := strings.ToLower(strings.TrimSpace(provider))
	m := strings.ToLower(strings.TrimSpace(model))
	u := strings.ToLower(strings.TrimSpace(baseURL))
	if localProviders[p] {
		return true
	}
	if strings.HasSuffix(m, ".rmb4") || strings.HasSuffix(m, ".mwi") || strings.Contains(m, "rmb") {
		return true
	}
	if strings.Contains(u, ":8787") || strings.HasSuffix(strings.TrimRight(u, "/"), ":8787") || strings.Contains(u, "/rmb") {
		return true
	}
	if isLoopbackURL(u) {
		return true
	}
	switch p {
	case "custom", "ollama", "llamacpp", "rmb", "local", "":
		if hasModelSizeWindow(m) {
			return true
		}
	}
	return encodingFamily(p, m) == "local" && !cloudProviders[p]
}

func isRMBBaseURL(baseURL string) bool {
	u := strings.TrimSpace(baseURL)
	if u == "" {
		return false
	}
	host, port := urlHost(u)
	if port == "8787" {
		return true
	}
	switch host {
	case "rmb", "rmb.local", "muscle-bridge", "musclebridge":
		return true
	}
	low := strings.ToLower(u)
	return strings.Contains(low, ":8787/") || strings.HasSuffix(strings.TrimRight(low, "/"), ":8787")
}

func isRMBProvider(provider, baseURL string) bool {
	if strings.ToLower(strings.TrimSpace(provider)) == "rmb" {
		return true
	}
	return isRMBBaseURL(baseURL)
}

// isLocalBinding is local_agent_optimize.is_local_binding.
func isLocalBinding(b binding) bool {
	return isRMBProvider(b.Provider, b.BaseURL) || isLocalModel(b.Provider, b.Model, b.BaseURL)
}

// --- muscle_profile.classify_muscle -----------------------------------------

type muscle struct {
	tier                          int
	label, provider, model        string
	preferSpread, builderContract bool
	denseMemory                   bool
}

var strongProviders = map[string]bool{
	"xai": true, "anthropic": true, "openai": true, "google": true, "gemini": true,
	"deepseek": true, "mistral": true, "openrouter": true, "groq": true,
}

func classifyMuscle(b binding) muscle {
	p := strings.ToLower(strings.TrimSpace(b.Provider))
	m := strings.TrimSpace(b.Model)
	ml := strings.ToLower(m)
	u := strings.ToLower(strings.TrimSpace(b.BaseURL))
	local := p == "ollama" || p == "llamacpp" || p == "rmb" || p == "local" || strings.Contains(u, "11434")
	var tier int
	switch {
	case tinyModelRE.MatchString(ml):
		if local {
			tier = 0
		} else {
			tier = 1
		}
	case frontierModelRE.MatchString(ml):
		tier = 3
	case midModelRE.MatchString(ml):
		tier = 2
	case p == "xai" || p == "anthropic" || p == "openai":
		tier = 3
	case p == "google" || p == "gemini" || p == "deepseek" || p == "mistral":
		if ml != "" {
			tier = 3
		} else {
			tier = 2
		}
	case p == "openrouter":
		tier = 2
	case local:
		if ml != "" {
			tier = 1
		}
	case strongProviders[p]:
		tier = 2
	default:
		if ml != "" {
			tier = 1
		}
	}
	labels := map[int]string{0: "tiny", 1: "small", 2: "mid", 3: "frontier"}
	return muscle{
		tier: tier, label: labels[tier], provider: p, model: m,
		preferSpread: tier >= 2, builderContract: tier >= 2, denseMemory: tier >= 2,
	}
}

// builderAddendum is muscle_profile.builder_system_addendum.
func builderAddendum(m muscle) string {
	research := "If a check fails on a missing module or unknown command, " +
		"search once with " + toolNameTable["web_search"] + " before editing."
	if !m.builderContract {
		return "[Harness · " + m.label + "]\n" +
			"Run until the request is finished. " +
			"Done means a check you ran succeeded.\n" +
			research
	}
	spread := ""
	if m.preferSpread {
		spread = " Survey independent modules in one parallel batch."
	}
	prov := m.provider
	if prov == "" {
		prov = "provider"
	}
	model := ""
	if m.model != "" {
		model = " / " + m.model
	}
	return "[Builder · " + m.label + " · " + prov + model + "]\n" +
		"You can design and ship full systems with tools. After a runnable " +
		"with a window: run it, " + toolNameTable["computer"] + " screenshot the desktop, play it, " +
		toolNameTable["edit"] + " what you see." +
		spread + " " +
		"Run until the request is finished. " +
		"Done means a check you ran succeeded. " +
		research
}
