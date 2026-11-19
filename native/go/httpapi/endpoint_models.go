package httpapi

import (
	"os"
	"strings"
)

// Models always come from the provider's own models endpoint, never from a
// list baked into Remedy: any baked-in list goes stale as providers ship new
// versions. These helpers pick and check models against what the endpoint
// reports right now (cached by discoverModelsCached).

// endpointChatModels lists the chat model ids the endpoint offers.
// ok is false when the endpoint could not be reached or listed.
func endpointChatModels(provider, baseURL, apiKey string) (ids []string, loaded []string, ok bool) {
	if strings.TrimSpace(baseURL) == "" {
		return nil, nil, false
	}
	switch strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_LIVE_MODELS"))) {
	case "0", "false", "no", "off":
		return nil, nil, false
	}
	disc := discoverModelsCached(baseURL, apiKey, provider)
	if !disc.OK {
		return nil, nil, false
	}
	for _, m := range disc.Models {
		id := strings.TrimSpace(fmtString(m["id"]))
		if id == "" || !boolOr(m["chat"], true) {
			continue
		}
		ids = append(ids, id)
	}
	return ids, disc.Loaded, true
}

// pickEndpointModel chooses a model when none is configured: the owner's last
// pick for this provider if the endpoint still offers it, else the first chat
// model the endpoint lists. "" when the endpoint cannot be listed.
func pickEndpointModel(cfg ConfigMap, provider, baseURL, apiKey string) string {
	ids, loaded, ok := endpointChatModels(provider, baseURL, apiKey)
	if !ok || len(ids) == 0 {
		return ""
	}
	rows := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		rows = append(rows, map[string]any{"id": id})
	}
	var preferred []string
	if lastBy, has := asStringMap(cfg["last_model_by_provider"]); has {
		preferred = append(preferred, strings.TrimSpace(fmtString(lastBy[provider])))
	}
	return chooseDefaultModel(rows, preferred, loaded)
}

// checkModelOffered rejects a model the provider's endpoint does not list.
// When the endpoint cannot be listed (offline, no key yet) any non-empty id
// is accepted: Remedy never second-guesses with a baked-in list.
func (s *Server) checkModelOffered(provider, model string) error {
	if err := validateProviderModel(provider, model); err != nil {
		return err
	}
	res := s.listModels(provider, "", "")
	disc, _ := res["discovery"].(map[string]any)
	if ok, _ := disc["ok"].(bool); !ok {
		return nil
	}
	rows, _ := res["models"].([]map[string]any)
	want := strings.TrimSpace(model)
	for _, r := range rows {
		if fmtString(r["id"]) == want {
			return nil
		}
	}
	return modelNotOfferedError(provider, want)
}
