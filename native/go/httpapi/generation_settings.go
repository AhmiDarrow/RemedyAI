package httpapi

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

// Config keys for reasoning and context. Top-level keys are the default for
// every model; [model_settings."<model id>"] (or "<provider>/<model id>")
// overrides them per model. Unset keeps each provider's built-in behavior.
const (
	cfgReasoningEffort  = "reasoning_effort"
	cfgThinkingBudget   = "thinking_budget_tokens"
	cfgMaxOutputTokens  = "max_output_tokens"
	cfgContextWindow    = "context_window_tokens"
	cfgModelSettingsKey = "model_settings"
)

var generationKeys = []string{cfgReasoningEffort, cfgThinkingBudget, cfgMaxOutputTokens, cfgContextWindow}

func generationFromMap(m map[string]any) (providers.GenerationOptions, error) {
	if m == nil {
		return providers.GenerationOptions{}, nil
	}
	o := providers.GenerationOptions{
		Effort:          strings.TrimSpace(anyString(m[cfgReasoningEffort])),
		ThinkingBudget:  anyInt(m[cfgThinkingBudget], 0),
		MaxOutputTokens: anyInt(m[cfgMaxOutputTokens], 0),
		ContextWindow:   anyInt(m[cfgContextWindow], 0),
	}
	return o.Validate()
}

// generationOptionsFor resolves the options for one provider/model binding:
// the per-model table first, then the top-level defaults. An invalid value is
// logged and ignored so a typo never breaks a turn.
func generationOptionsFor(cfg ConfigMap, provider, model string) providers.GenerationOptions {
	global, err := generationFromMap(cfg)
	if err != nil {
		log.Printf("settings: ignoring reasoning/context defaults: %v", err)
		global = providers.GenerationOptions{}
	}
	table := asMap(cfg[cfgModelSettingsKey])
	if table == nil {
		return global
	}
	model = strings.TrimSpace(model)
	for _, key := range []string{strings.TrimSpace(provider) + "/" + model, model} {
		entry := asMap(table[key])
		if entry == nil {
			continue
		}
		per, err := generationFromMap(entry)
		if err != nil {
			log.Printf("settings: ignoring model_settings[%q]: %v", key, err)
			continue
		}
		return per.Merge(global)
	}
	return global
}

// validateGenerationPatch normalizes the reasoning/context keys of a settings
// patch in place. 0 or "" clears a key (back to the provider default).
func validateGenerationPatch(patch map[string]any) error {
	if err := checkTokenCounts(patch, ""); err != nil {
		return err
	}
	single := map[string]any{}
	for _, k := range generationKeys {
		if v, ok := patch[k]; ok && v != nil {
			single[k] = v
		}
	}
	if len(single) > 0 {
		o, err := generationFromMap(single)
		if err != nil {
			return err
		}
		writeGeneration(patch, single, o)
	}
	raw, ok := patch[cfgModelSettingsKey]
	if !ok || raw == nil {
		return nil
	}
	table, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("model_settings must be a table of model id → settings")
	}
	for model, v := range table {
		entry, ok := v.(map[string]any)
		if !ok || strings.TrimSpace(model) == "" {
			return fmt.Errorf("model_settings[%q] must be a table", model)
		}
		for k := range entry {
			if !containsString(generationKeys, k) {
				return fmt.Errorf("model_settings[%q]: unknown key %q", model, k)
			}
		}
		if err := checkTokenCounts(entry, fmt.Sprintf("model_settings[%q]: ", model)); err != nil {
			return err
		}
		o, err := generationFromMap(entry)
		if err != nil {
			return fmt.Errorf("model_settings[%q]: %w", model, err)
		}
		clean := map[string]any{}
		writeGeneration(clean, entry, o)
		table[model] = clean
	}
	return nil
}

// checkTokenCounts refuses a token count that is not a number, so "lots" or
// true is an error rather than silently clearing the setting (Python
// generation_settings._token_count). Unset (nil or "") is allowed.
func checkTokenCounts(m map[string]any, prefix string) error {
	for _, k := range []string{cfgThinkingBudget, cfgMaxOutputTokens, cfgContextWindow} {
		v, ok := m[k]
		if !ok || v == nil {
			continue
		}
		bad := false
		switch t := v.(type) {
		case bool:
			bad = true
		case int, int64, int32:
		case float64:
			bad = math.IsNaN(t) || math.IsInf(t, 0)
		case json.Number:
			f, err := t.Float64()
			bad = err != nil || math.IsNaN(f) || math.IsInf(f, 0)
		case string:
			s := strings.TrimSpace(t)
			if s == "" {
				continue
			}
			f, err := strconv.ParseFloat(s, 64)
			bad = err != nil || math.IsNaN(f) || math.IsInf(f, 0)
			if !bad {
				// anyInt only reads whole numbers from text; keep "8000.0" usable.
				m[k] = int(f)
			}
		default:
			bad = true
		}
		if bad {
			return fmt.Errorf("%s%s must be a number, got %v", prefix, k, v)
		}
	}
	return nil
}

func writeGeneration(dst, present map[string]any, o providers.GenerationOptions) {
	if _, ok := present[cfgReasoningEffort]; ok {
		dst[cfgReasoningEffort] = o.Effort
	}
	if _, ok := present[cfgThinkingBudget]; ok {
		dst[cfgThinkingBudget] = o.ThinkingBudget
	}
	if _, ok := present[cfgMaxOutputTokens]; ok {
		dst[cfgMaxOutputTokens] = o.MaxOutputTokens
	}
	if _, ok := present[cfgContextWindow]; ok {
		dst[cfgContextWindow] = o.ContextWindow
	}
}

// generationSettingsView is what GET /api/settings reports.
func generationSettingsView(cfg ConfigMap) map[string]any {
	o, _ := generationFromMap(cfg)
	out := map[string]any{
		cfgReasoningEffort: o.Effort,
		cfgThinkingBudget:  o.ThinkingBudget,
		cfgMaxOutputTokens: o.MaxOutputTokens,
		cfgContextWindow:   o.ContextWindow,
	}
	if t := asMap(cfg[cfgModelSettingsKey]); t != nil {
		out[cfgModelSettingsKey] = t
	} else {
		out[cfgModelSettingsKey] = map[string]any{}
	}
	return out
}

// mergeModelSettings folds a model_settings patch into the stored table, so
// changing one model leaves the others alone. An entry whose values are all
// unset removes that model's overrides.
func mergeModelSettings(patch, cfg ConfigMap) {
	incoming, ok := patch[cfgModelSettingsKey].(map[string]any)
	if !ok {
		return
	}
	merged := map[string]any{}
	for k, v := range asMap(cfg[cfgModelSettingsKey]) {
		merged[k] = v
	}
	for model, v := range incoming {
		entry := asMap(v)
		o, _ := generationFromMap(entry)
		if o.IsZero() {
			delete(merged, model)
			continue
		}
		merged[model] = entry
	}
	patch[cfgModelSettingsKey] = merged
}
