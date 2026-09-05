package httpapi

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

// ResolveListenModel picks an OpenAI-compatible live model when settings+secret
// (including xAI OAuth) are ready; otherwise prefers the local vision helper
// when it is up; last resort is a ScriptedModel that emits Hello/world.
func ResolveListenModel(home string) cognition.Model {
	return ResolveChatModel(home, "", "", "")
}

// ResolveChatModel resolves the chat muscle for a turn.
//
// Order:
//  1. Explicit provider/model/baseURL (session bind) when credentials are ready
//  2. Global config provider when the bind is missing credentials (or unset)
//  3. Local vision helper (SmolVLM) when installed and listening
//  4. Scripted Hello/world last resort
func ResolveChatModel(home, provider, model, baseURL string) cognition.Model {
	return resolveChatModel(home, provider, model, baseURL, "")
}

// resolveChatModel is ResolveChatModel with an optional provider to skip
// (used after a credentialed provider returns 401/402/403 at chat time).
func resolveChatModel(home, provider, model, baseURL, excludeProvider string) cognition.Model {
	home = ResolveHomeDir(home)
	cfg := LoadConfig(home)
	exclude := strings.ToLower(strings.TrimSpace(excludeProvider))

	explicitProv := strings.TrimSpace(provider)
	explicitModel := strings.TrimSpace(model)
	explicitURL := strings.TrimSpace(baseURL)

	if !providerExcluded(explicitProv, exclude) {
		if m := tryOpenAICompat(home, cfg, explicitProv, explicitModel, explicitURL); m != nil {
			return m
		}
	}

	// Session bind to a provider without credentials must not take down chat —
	// fall through to the owner's configured active provider (e.g. xAI OAuth).
	cfgProv := cfgString(cfg, "llm_provider", envOr("REMEDY_LLM_PROVIDER", "openai"))
	cfgModel := cfgString(cfg, "llm_model", envOr("REMEDY_LLM_MODEL", ""))
	cfgURL := cfgString(cfg, "llm_base_url", envOr("REMEDY_LLM_BASE_URL", ""))
	sameAsExplicit := strings.EqualFold(strings.TrimSpace(cfgProv), explicitProv) &&
		(explicitModel == "" || strings.EqualFold(explicitModel, strings.TrimSpace(cfgModel))) &&
		(explicitURL == "" || strings.EqualFold(explicitURL, strings.TrimSpace(cfgURL)))
	if !sameAsExplicit && !providerExcluded(cfgProv, exclude) {
		if m := tryOpenAICompat(home, cfg, cfgProv, cfgModel, cfgURL); m != nil {
			return m
		}
	}

	if m := resolveVisionHelperModel(home, cfg); m != nil {
		return m
	}

	return &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "Hello ", Done: false}, {Text: "world", Done: true}},
	}}
}

func providerExcluded(provider, excludeLower string) bool {
	if excludeLower == "" {
		return false
	}
	return strings.ToLower(strings.TrimSpace(provider)) == excludeLower
}

// isProviderUnusableError reports auth/billing failures where a key exists but
// chat cannot proceed (e.g. Poe 402 subscription required). Callers should
// fall back to another provider rather than hard-fail the turn.
func isProviderUnusableError(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	if strings.Contains(msg, "openai-compat http 401") ||
		strings.Contains(msg, "openai-compat http 402") ||
		strings.Contains(msg, "openai-compat http 403") {
		return true
	}
	// Some gateways wrap status differently but still surface subscription/billing.
	if strings.Contains(msg, "requires an active") && strings.Contains(msg, "subscription") {
		return true
	}
	if strings.Contains(msg, "insufficient credits") || strings.Contains(msg, "payment required") {
		return true
	}
	return false
}

func tryOpenAICompat(home string, cfg ConfigMap, provider, model, baseURL string) cognition.Model {
	rawProvider := strings.TrimSpace(provider)
	if rawProvider == "" {
		return nil
	}
	rawModel := strings.TrimSpace(model)
	rawURL := strings.TrimSpace(baseURL)
	prov, modelID, urlOut := normalizeLLMSettings(rawProvider, rawModel, rawURL)

	// Catalog base URL wins over normalize's custom localhost fallback when the
	// caller did not pass an explicit URL (Poe used to resolve to :5001).
	meta, ok := mergedProviderCatalog(cfg)[prov]
	if !ok {
		meta = providerCatalog[prov]
	}
	if rawURL != "" {
		urlOut = rawURL
	} else if catalogURL := strings.TrimSpace(meta.BaseURL); catalogURL != "" {
		if urlOut == "" || (isLocalURL(urlOut) && !isLocalURL(catalogURL)) {
			urlOut = catalogURL
		}
	}
	if rawModel != "" {
		modelID = rawModel
	} else if modelID == "" && len(meta.Models) > 0 {
		modelID = meta.Models[0].ID
	}

	key := resolveProviderAPIKey(cfg, prov, home)
	if !providerCredentialsReady(cfg, prov, key) {
		return nil
	}
	if strings.TrimSpace(urlOut) == "" {
		return nil
	}
	// Local providers are "credential-ready" without a key, but must actually
	// be listening — otherwise fall through to the active cloud provider.
	if prov == "ollama" || prov == "rmb" || prov == "llamacpp" || (prov == "custom" && isLocalURL(urlOut)) {
		if !openaiEndpointReachable(urlOut) {
			return nil
		}
	}
	if strings.TrimSpace(modelID) == "" {
		if prov == "ollama" || prov == "rmb" || prov == "llamacpp" || prov == "custom" {
			modelID = "default"
		} else {
			return nil
		}
	}
	return &providers.OpenAICompat{
		BaseURL: urlOut,
		APIKey:  firstNonEmpty(key, "unused"),
		Model:   modelID,
	}
}

func openaiEndpointReachable(rawURL string) bool {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || u.Host == "" {
		return false
	}
	host := u.Hostname()
	port := u.Port()
	if port == "" {
		if u.Scheme == "https" {
			port = "443"
		} else {
			port = "80"
		}
	}
	n, err := strconv.Atoi(port)
	if err != nil || n <= 0 {
		return false
	}
	return visionPortOpen(host, n)
}

// resolveVisionHelperModel returns an OpenAI-compat client for the local
// SmolVLM helper when vision is enabled and its llama-server port is open.
// Tools are left empty — this path is for basic chat when no cloud provider
// is ready, not full ReAct.
func resolveVisionHelperModel(home string, cfg ConfigMap) cognition.Model {
	if cfg == nil {
		cfg = LoadConfig(home)
	}
	vcfg := visionSectionFromConfig(cfg)
	if !anyBoolDef(vcfg["enabled"], true) {
		return nil
	}
	mid := strings.TrimSpace(anyString(vcfg["model_id"]))
	if mid == "" {
		mid = visionDefaultModelID
	}
	if !visionInstalled(home, mid) {
		return nil
	}
	host := strings.TrimSpace(anyString(vcfg["host"]))
	if host == "" {
		host = visionDefaultHost
	}
	port := anyInt(vcfg["port"], visionDefaultPort)
	if port <= 0 {
		port = visionDefaultPort
	}
	if !visionPortOpen(host, port) {
		return nil
	}
	base := strings.TrimSpace(anyString(vcfg["base_url"]))
	if base == "" {
		base = fmt.Sprintf("http://%s:%d/v1", host, port)
	}
	// llama-server ignores the model id when one GGUF is loaded; keep a stable
	// placeholder matching the Python vision decoder.
	return &providers.OpenAICompat{
		BaseURL: base,
		APIKey:  "unused",
		Model:   "vision-decoder",
	}
}
