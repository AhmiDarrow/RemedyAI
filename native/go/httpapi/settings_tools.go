package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// SettingsServerProvider supplies the live Server for settings Tool ABI tools.
type SettingsServerProvider func() *Server

// toolSafeSettingsKeys is the jail for settings.get / settings.patch.
// Secrets, provider keys, project retarget, and raw TOML are excluded.
var toolSafeSettingsKeys = map[string]struct{}{
	"approval_mode":        {},
	"trust_profile":        {},
	"thinking_level":       {},
	"name":                 {},
	"user_name":            {},
	"agent_gender":         {},
	"ui_language":          {},
	"persona":              {},
	"show_tool_calls":      {},
	"sarcasm_mode":         {},
	"web_tools_enabled":    {},
	"vision_enabled":       {},
	"privacy_mode":         {},
	"launch_at_login":      {},
	"start_in_tray":        {},
	"close_to_tray":        {},
	"browser_home_url":     {},
	"access_scope":         {},
	"allow_skill_creation": {},
	"soul_field_enabled":   {},
	"rmb_enabled":          {},
	"enabled_channels":     {},
}

// toolForbiddenSettingsKeys are explicitly refused (even if present in a patch).
var toolForbiddenSettingsKeys = map[string]struct{}{
	"llm_api_key":                {},
	"provider_keys":              {},
	"messengers":                 {},
	"assistant":                  {},
	"project_path":               {},
	"sleev_gateway_url":          {},
	"sleev_allow_remote_gateway": {},
	"connect_relay_url":          {},
	"llm_base_url":               {},
	"llm_provider":               {},
	"llm_model":                  {},
}

// RegisterSettingsTools installs settings.get (read-only, scrubbed) and
// settings.patch (mutation) for jailed safe fields only.
func RegisterSettingsTools(registry *tools.Registry, server SettingsServerProvider) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", tools.ErrInvalidDescriptor)
	}
	if server == nil {
		return errors.New("settings server provider is required")
	}
	if _, err := registry.Latest("settings.get"); err == nil {
		return nil
	}

	if err := registry.Register(tools.Descriptor{
		ID:          "settings.get",
		Version:     1,
		Description: "Read scrubbed Remedy settings (approval mode, UI prefs, messenger enable flags). Never returns secrets.",
		Runtime:     tools.RuntimeGo,
		Risk:        tools.RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"properties":{
				"keys":{"type":"array","items":{"type":"string"},"description":"Optional subset of safe keys to return"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok","settings"],
			"properties":{
				"ok":{"type":"boolean"},
				"settings":{"type":"object"},
				"allowed_keys":{"type":"array","items":{"type":"string"}},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, tools.ExecutorFunc(func(_ context.Context, req tools.Request) (tools.Result, error) {
		return executeSettingsGet(server, req)
	})); err != nil {
		return err
	}

	return registry.Register(tools.Descriptor{
		ID:          "settings.patch",
		Version:     1,
		Description: "Patch jailed safe settings (approval_mode, UI prefs, enabled_channels). Secrets and provider keys are refused. Requires Ask in ask mode.",
		Runtime:     tools.RuntimeGo,
		Risk:        tools.RiskMutation,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["patch"],
			"properties":{
				"patch":{"type":"object","description":"Partial settings object of safe keys only"}
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["ok"],
			"properties":{
				"ok":{"type":"boolean"},
				"status":{"type":"string"},
				"changes":{"type":"array","items":{"type":"string"}},
				"settings":{"type":"object"},
				"refused":{"type":"array","items":{"type":"string"}},
				"error":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, tools.ExecutorFunc(func(_ context.Context, req tools.Request) (tools.Result, error) {
		return executeSettingsPatch(server, req)
	}))
}

func executeSettingsGet(server SettingsServerProvider, req tools.Request) (tools.Result, error) {
	s := server()
	if s == nil {
		out, _ := json.Marshal(map[string]any{"ok": false, "settings": map[string]any{}, "error": "settings server unavailable"})
		return tools.Result{Output: out}, nil
	}
	var body struct {
		Keys []string `json:"keys"`
	}
	if len(req.Input) > 0 {
		if err := json.Unmarshal(req.Input, &body); err != nil {
			return tools.Result{}, tools.ErrInvalidInput
		}
	}
	scrubbed := scrubbedToolSettings(s)
	if len(body.Keys) > 0 {
		filtered := map[string]any{}
		for _, k := range body.Keys {
			k = strings.TrimSpace(k)
			if _, ok := toolSafeSettingsKeys[k]; !ok {
				continue
			}
			if v, ok := scrubbed[k]; ok {
				filtered[k] = v
			}
		}
		scrubbed = filtered
	}
	out, err := json.Marshal(map[string]any{
		"ok":           true,
		"settings":     scrubbed,
		"allowed_keys": toolSafeKeyList(),
	})
	return tools.Result{Output: out}, err
}

func executeSettingsPatch(server SettingsServerProvider, req tools.Request) (tools.Result, error) {
	s := server()
	if s == nil {
		out, _ := json.Marshal(map[string]any{"ok": false, "error": "settings server unavailable"})
		return tools.Result{Output: out}, nil
	}
	var body struct {
		Patch map[string]any `json:"patch"`
	}
	if err := json.Unmarshal(req.Input, &body); err != nil || body.Patch == nil {
		return tools.Result{}, tools.ErrInvalidInput
	}
	if len(body.Patch) == 0 {
		out, _ := json.Marshal(map[string]any{"ok": false, "error": "empty patch"})
		return tools.Result{Output: out}, nil
	}

	clean := map[string]any{}
	refused := make([]string, 0)
	for k, v := range body.Patch {
		k = strings.TrimSpace(k)
		if k == "" {
			continue
		}
		if _, bad := toolForbiddenSettingsKeys[k]; bad {
			refused = append(refused, k)
			continue
		}
		if looksLikeSecretSettingsKey(k) {
			refused = append(refused, k)
			continue
		}
		if _, ok := toolSafeSettingsKeys[k]; !ok {
			refused = append(refused, k)
			continue
		}
		clean[k] = v
	}
	if len(refused) > 0 && len(clean) == 0 {
		out, _ := json.Marshal(map[string]any{
			"ok":      false,
			"refused": refused,
			"error":   "refused settings keys (secrets and non-jailed fields cannot be set via settings.patch)",
		})
		return tools.Result{Output: out}, nil
	}
	if len(clean) == 0 {
		out, _ := json.Marshal(map[string]any{"ok": false, "error": "no recognized safe settings keys in patch"})
		return tools.Result{Output: out}, nil
	}

	result, err := s.applySettingsUpdate(clean)
	if err != nil {
		out, _ := json.Marshal(map[string]any{"ok": false, "refused": refused, "error": err.Error()})
		return tools.Result{Output: out}, nil
	}
	resp := map[string]any{
		"ok":       true,
		"status":   result["status"],
		"changes":  result["changes"],
		"settings": scrubbedToolSettings(s),
	}
	if len(refused) > 0 {
		resp["refused"] = refused
	}
	out, mErr := json.Marshal(resp)
	return tools.Result{Output: out}, mErr
}

func scrubbedToolSettings(s *Server) map[string]any {
	full := s.settingsPayload()
	out := make(map[string]any, len(toolSafeSettingsKeys)+4)
	for k := range toolSafeSettingsKeys {
		if v, ok := full[k]; ok {
			out[k] = v
		}
	}
	// Derived non-secret status only — never raw tokens/keys.
	if v, ok := full["llm_api_key_set"]; ok {
		out["llm_api_key_set"] = v
	}
	if v, ok := full["provider_keys_set"]; ok {
		out["provider_keys_set"] = v
	}
	if raw, ok := full["messengers"].([]map[string]any); ok {
		out["messengers"] = scrubMessengerEnableFlags(raw)
	} else if rawAny, ok := full["messengers"].([]any); ok {
		rows := make([]map[string]any, 0, len(rawAny))
		for _, item := range rawAny {
			if m, ok := item.(map[string]any); ok {
				rows = append(rows, m)
			}
		}
		out["messengers"] = scrubMessengerEnableFlags(rows)
	}
	return out
}

func scrubMessengerEnableFlags(rows []map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		out = append(out, map[string]any{
			"id":        row["id"],
			"name":      row["name"],
			"enabled":   row["enabled"],
			"token_set": row["token_set"],
			"status":    row["status"],
		})
	}
	return out
}

func looksLikeSecretSettingsKey(k string) bool {
	kl := strings.ToLower(strings.TrimSpace(k))
	if _, ok := messengerSecretFields[kl]; ok {
		return true
	}
	return strings.HasSuffix(kl, "_token") ||
		strings.HasSuffix(kl, "_password") ||
		strings.HasSuffix(kl, "_secret") ||
		strings.HasSuffix(kl, "_key") ||
		kl == "toml" || kl == "raw_toml" || kl == "config_toml"
}

func toolSafeKeyList() []string {
	out := make([]string, 0, len(toolSafeSettingsKeys))
	for k := range toolSafeSettingsKeys {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// AttachSettingsTools registers settings.get / settings.patch on the cognition runner.
func (r *CognitionTurnRunner) AttachSettingsTools(server SettingsServerProvider) error {
	if r == nil || r.Registry == nil {
		return errors.New("cognition turn runner has no tool registry")
	}
	if err := RegisterSettingsTools(r.Registry, server); err != nil {
		return err
	}
	r.Tools = &RegistryToolExecutor{Registry: r.Registry, TokenFor: RuntimeCapabilityToken}
	r.Policy = &RegistryPolicy{Registry: r.Registry, Approvals: r.Approvals}
	r.syncModelToolSchemas()
	return nil
}
