package httpapi

import "strings"

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
