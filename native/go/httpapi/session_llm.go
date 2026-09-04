package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// Exact 409 body from Python sessions/llm.py (desktop may match on it).
const sessionLLMBusyDetail = "Stop generation in this session before switching provider/model"

type sessionLLMRequest struct {
	Provider    string  `json:"provider"`
	Model       *string `json:"model"`
	MakeDefault bool    `json:"make_default"`
}

// resolveSessionLLMBind mirrors core/session_llm.resolve_session_llm_bind
// without live RMB stem probing (Phase-4 slice).
func resolveSessionLLMBind(
	sessProvider, sessModel, reqProvider, reqModel *string,
) (provider *string, model *string) {
	reqP := lowerPtr(reqProvider)
	reqM := trimPtr(reqModel)
	sessP := lowerPtr(sessProvider)
	sessM := trimPtr(sessModel)

	if reqP != nil && reqM != nil {
		return reqP, reqM
	}

	if sessP != nil && sessM != nil {
		if reqP != nil && *reqP != *sessP {
			mid := reqM
			if mid == nil {
				mid = sessM
			}
			return reqP, mid
		}
		if reqM != nil && reqP == nil {
			owner := inferProviderFromModel(*reqM)
			if owner == nil || *owner == *sessP {
				return sessP, reqM
			}
			return sessP, sessM
		}
		return sessP, sessM
	}

	if sessP != nil {
		mid := reqM
		if mid == nil {
			mid = sessM
		}
		return sessP, mid
	}

	mid := reqM
	if mid == nil {
		mid = sessM
	}
	if mid != nil {
		prov := reqP
		if prov == nil {
			prov = inferProviderFromModel(*mid)
		}
		return prov, mid
	}
	return reqP, nil
}

func sessionLLMUpdateFields(provider, model *string) (llmProvider *string, outModel *string, ok bool) {
	p := lowerPtr(provider)
	m := trimPtr(model)
	if m != nil && p == nil {
		p = inferProviderFromModel(*m)
	}
	if p == nil && m == nil {
		return nil, nil, false
	}
	return p, m, true
}

func inferProviderFromModel(model string) *string {
	m := strings.ToLower(strings.TrimSpace(model))
	if m == "" {
		return nil
	}
	var id string
	switch {
	case strings.HasPrefix(m, "grok"):
		id = "xai"
	case strings.HasPrefix(m, "deepseek"):
		id = "deepseek"
	case strings.HasPrefix(m, "claude"):
		id = "anthropic"
	case strings.HasPrefix(m, "gpt"), strings.HasPrefix(m, "o1"), strings.HasPrefix(m, "o3"):
		id = "openai"
	case strings.HasPrefix(m, "gemini"):
		id = "google"
	default:
		return nil
	}
	return &id
}

// validateProviderModel mirrors interfaces/config.validate_provider_model for the
// Phase-4 closed-catalog subset (native family prefixes + known defaults).
// Flexible providers accept any non-empty id.
func validateProviderModel(provider, model string) error {
	prov := strings.ToLower(strings.TrimSpace(provider))
	if prov == "" {
		prov = "openai"
	}
	mid := strings.TrimSpace(model)
	if mid == "" {
		return fmt.Errorf("Model id is required")
	}
	flexible := map[string]struct{}{
		"openrouter": {}, "custom": {}, "ollama": {}, "poe": {}, "rmb": {}, "llamacpp": {},
	}
	if _, ok := flexible[prov]; ok {
		return nil
	}
	if _, known := providerDefaults[prov]; !known {
		// Unknown / user-defined provider — accept any non-empty id.
		return nil
	}
	def := providerDefaults[prov]
	if def.model != "" && mid == def.model {
		return nil
	}
	if nativeModelIDForProvider(prov, mid) {
		return nil
	}
	sample := def.model
	if sample == "" {
		sample = "(none)"
	}
	return fmt.Errorf(
		"Unknown model %q for provider %q. Pick a listed model (e.g. %s).",
		mid, prov, sample,
	)
}

func nativeModelIDForProvider(provider, modelID string) bool {
	mid := strings.ToLower(strings.TrimSpace(modelID))
	prov := strings.ToLower(strings.TrimSpace(provider))
	if mid == "" || prov == "" {
		return false
	}
	switch prov {
	case "deepseek":
		return strings.HasPrefix(mid, "deepseek")
	case "xai":
		return strings.HasPrefix(mid, "grok") || strings.HasPrefix(mid, "xai/")
	case "openai":
		return strings.HasPrefix(mid, "gpt-") ||
			strings.HasPrefix(mid, "o1") ||
			strings.HasPrefix(mid, "o3") ||
			strings.HasPrefix(mid, "o4") ||
			strings.HasPrefix(mid, "chatgpt-")
	case "anthropic":
		return strings.HasPrefix(mid, "claude")
	case "google":
		return strings.HasPrefix(mid, "gemini") || strings.HasPrefix(mid, "models/gemini")
	case "mistral":
		return strings.HasPrefix(mid, "mistral") ||
			strings.HasPrefix(mid, "codestral") ||
			strings.HasPrefix(mid, "open-mistral")
	case "groq":
		return strings.HasPrefix(mid, "groq/")
	case "demo":
		return mid == "codestral-latest" || strings.HasPrefix(mid, "codestral")
	default:
		return false
	}
}

func (s *Server) handleSetSessionLLM(w http.ResponseWriter, r *http.Request) {
	if s.sessions == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"detail": "Memory store not available"})
		return
	}
	sid := strings.TrimSpace(r.PathValue("id"))
	if sid == "" {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}

	// Only block if *this* session is streaming — other tabs may run freely.
	if s.claims != nil && s.claims.IsClaimed(sid) {
		writeJSON(w, http.StatusConflict, map[string]string{"detail": sessionLLMBusyDetail})
		return
	}

	var req sessionLLMRequest
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil && !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	if strings.TrimSpace(req.Provider) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "provider is required"})
		return
	}

	_, ok, err := s.sessions.Get(sid)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Session not found"})
		return
	}

	cfg := LoadConfig(s.homeDir)
	rawProvider := strings.TrimSpace(req.Provider)
	if rawProvider == "" {
		rawProvider = cfgString(cfg, "llm_provider", "openai")
	}
	rawModel := ""
	if req.Model != nil {
		rawModel = strings.TrimSpace(*req.Model)
	}
	if rawModel == "" {
		rawModel = strings.TrimSpace(cfgString(cfg, "llm_model", ""))
	}
	// Fail closed on garbage ids before toast/persist.
	if rawModel != "" {
		if verr := validateProviderModel(rawProvider, rawModel); verr != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": verr.Error()})
			return
		}
	}

	baseHint := ""
	cfgProv := strings.ToLower(cfgString(cfg, "llm_provider", ""))
	if strings.ToLower(rawProvider) == cfgProv {
		baseHint = cfgString(cfg, "llm_base_url", "")
	}
	provider, model, baseURL := normalizeLLMSettings(rawProvider, rawModel, baseHint)

	if p, m, has := sessionLLMUpdateFields(&provider, &model); has {
		if err := s.sessions.UpdateLLMBind(sid, p, m); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "Internal Server Error"})
			return
		}
		if p != nil {
			provider = *p
		}
		if m != nil {
			model = *m
		}
	}

	// Always remember last model for this provider; global default only when asked.
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	if path != "" {
		if cfg == nil {
			cfg = ConfigMap{}
		}
		lastBy, _ := asStringMap(cfg["last_model_by_provider"])
		if lastBy == nil {
			lastBy = map[string]any{}
		}
		lastBy[provider] = model
		cfg["last_model_by_provider"] = lastBy
		if req.MakeDefault {
			cfg["llm_provider"] = provider
			cfg["llm_model"] = model
			cfg["llm_base_url"] = baseURL
		}
		_ = WriteConfig(path, cfg)
	}

	toast := fmt.Sprintf("Now using %s · %s", provider, model)
	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "ok",
		"session_id":     sid,
		"provider":       provider,
		"model":          model,
		"base_url":       baseURL,
		"make_default":   req.MakeDefault,
		"remeasure":      nil,
		"context_window": nil,
		"toast":          toast,
		"rmb_live":       nil,
	})
}

func lowerPtr(p *string) *string {
	if p == nil {
		return nil
	}
	s := strings.ToLower(strings.TrimSpace(*p))
	if s == "" {
		return nil
	}
	return &s
}

func trimPtr(p *string) *string {
	if p == nil {
		return nil
	}
	s := strings.TrimSpace(*p)
	if s == "" {
		return nil
	}
	return &s
}
