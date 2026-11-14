package providers

import (
	"fmt"
	"strings"
)

// GenerationOptions are the owner's reasoning and context knobs for one model.
// The zero value means "unset" everywhere: providers keep their built-in
// behavior (Anthropic's shape-based adaptive effort, no reasoning fields on
// OpenAI-compatible requests, default context windows and output ceilings).
type GenerationOptions struct {
	// Effort is the reasoning effort: none, minimal, low, medium, high, xhigh.
	// Mapped per provider; omitted for models that have no such control.
	Effort string
	// ThinkingBudget is an explicit thinking-token budget for providers that
	// take one (Anthropic extended thinking budget_tokens, Gemini
	// thinking_budget). 0 = unset.
	ThinkingBudget int
	// MaxOutputTokens caps one model round's output. 0 = provider default.
	MaxOutputTokens int
	// ContextWindow overrides the prompt window used for history truncation
	// and compaction. 0 = provider default.
	ContextWindow int
}

// Limits for validation. Wide enough for every current frontier model; a
// value outside them is a typo, not a preference.
const (
	MinThinkingBudget  = 1024
	MaxThinkingBudget  = 128_000
	MinMaxOutputTokens = 256
	MaxMaxOutputTokens = 256_000
	MinContextWindow   = 4096
	MaxContextWindow   = 2_000_000
)

// Efforts lists the accepted reasoning effort values, lowest first.
var Efforts = []string{"none", "minimal", "low", "medium", "high", "xhigh"}

// NormalizeEffort lowercases and validates an effort. "" and "default" mean
// unset.
func NormalizeEffort(raw string) (string, error) {
	v := strings.ToLower(strings.TrimSpace(raw))
	switch v {
	case "", "default", "auto":
		return "", nil
	case "off":
		return "none", nil
	case "max", "maximum":
		return "xhigh", nil
	}
	for _, e := range Efforts {
		if v == e {
			return v, nil
		}
	}
	return "", fmt.Errorf("reasoning effort %q is not one of %s", raw, strings.Join(Efforts, ", "))
}

// Validate normalizes the effort and checks every number against its range.
func (o GenerationOptions) Validate() (GenerationOptions, error) {
	eff, err := NormalizeEffort(o.Effort)
	if err != nil {
		return GenerationOptions{}, err
	}
	o.Effort = eff
	check := func(name string, v, lo, hi int) error {
		if v == 0 {
			return nil
		}
		if v < lo || v > hi {
			return fmt.Errorf("%s must be 0 (default) or between %d and %d, got %d", name, lo, hi, v)
		}
		return nil
	}
	if err := check("thinking_budget_tokens", o.ThinkingBudget, MinThinkingBudget, MaxThinkingBudget); err != nil {
		return GenerationOptions{}, err
	}
	if err := check("max_output_tokens", o.MaxOutputTokens, MinMaxOutputTokens, MaxMaxOutputTokens); err != nil {
		return GenerationOptions{}, err
	}
	if err := check("context_window_tokens", o.ContextWindow, MinContextWindow, MaxContextWindow); err != nil {
		return GenerationOptions{}, err
	}
	return o, nil
}

// IsZero reports whether no option is set.
func (o GenerationOptions) IsZero() bool { return o == GenerationOptions{} }

// Merge returns o with every unset field taken from fallback.
func (o GenerationOptions) Merge(fallback GenerationOptions) GenerationOptions {
	if o.Effort == "" {
		o.Effort = fallback.Effort
	}
	if o.ThinkingBudget == 0 {
		o.ThinkingBudget = fallback.ThinkingBudget
	}
	if o.MaxOutputTokens == 0 {
		o.MaxOutputTokens = fallback.MaxOutputTokens
	}
	if o.ContextWindow == 0 {
		o.ContextWindow = fallback.ContextWindow
	}
	return o
}

// Provider families for OpenAI-compatible request shaping.
const (
	FamilyOpenAI = "openai"
	FamilyXAI    = "xai"
	FamilyGoogle = "google"
)

// FamilyFor maps a Remedy provider id to the request dialect it speaks.
func FamilyFor(providerID string) string {
	switch strings.ToLower(strings.TrimSpace(providerID)) {
	case "openai":
		return FamilyOpenAI
	case "xai", "grok":
		return FamilyXAI
	case "google", "gemini":
		return FamilyGoogle
	}
	return ""
}

func modelBase(model string) string {
	m := strings.ToLower(strings.TrimSpace(model))
	if i := strings.LastIndex(m, "/"); i >= 0 {
		m = m[i+1:]
	}
	return m
}

// openAIReasoningModel: o-series and GPT-5 take reasoning_effort and
// max_completion_tokens; chat models (gpt-4o, gpt-4.1) reject reasoning_effort.
func openAIReasoningModel(model string) bool {
	m := modelBase(model)
	if strings.HasPrefix(m, "gpt-5") {
		return !strings.Contains(m, "chat")
	}
	for _, p := range []string{"o1", "o3", "o4"} {
		if m == p || strings.HasPrefix(m, p+"-") {
			return true
		}
	}
	return false
}

func openAIEffort(effort, model string) string {
	gpt5 := strings.HasPrefix(modelBase(model), "gpt-5")
	switch effort {
	case "none", "minimal":
		if gpt5 {
			return "minimal"
		}
		return "low"
	case "xhigh":
		return "high"
	}
	return effort
}

// xaiEffortModel: of the Grok line only grok-3-mini takes reasoning_effort
// (low | high); Grok 4 reasons always and rejects the field.
func xaiEffortModel(model string) bool {
	return strings.HasPrefix(modelBase(model), "grok-3-mini")
}

func xaiEffort(effort string) string {
	switch effort {
	case "none", "minimal", "low":
		return "low"
	}
	return "high"
}

// geminiThinkingModel: 2.5 and later think and accept a budget or an effort.
func geminiThinkingModel(model string) bool {
	m := modelBase(model)
	if !strings.HasPrefix(m, "gemini-") {
		return false
	}
	return !(strings.HasPrefix(m, "gemini-1") || strings.HasPrefix(m, "gemini-2.0"))
}

func geminiEffort(effort string) string {
	switch effort {
	case "minimal":
		return "low"
	case "xhigh":
		return "high"
	}
	return effort // none | low | medium | high
}

// ApplyOpenAICompatGeneration adds the reasoning and output fields this
// provider family and model understand. Nothing is added for unset options or
// for models that do not take a field, so unset keeps the old payload.
func ApplyOpenAICompatGeneration(payload map[string]any, family, model string, o GenerationOptions) {
	switch family {
	case FamilyOpenAI:
		if openAIReasoningModel(model) {
			if o.Effort != "" {
				payload["reasoning_effort"] = openAIEffort(o.Effort, model)
			}
			if o.MaxOutputTokens > 0 {
				payload["max_completion_tokens"] = o.MaxOutputTokens
			}
			return
		}
	case FamilyXAI:
		if o.Effort != "" && xaiEffortModel(model) {
			payload["reasoning_effort"] = xaiEffort(o.Effort)
		}
	case FamilyGoogle:
		if geminiThinkingModel(model) {
			// The OpenAI-compatible Gemini endpoint takes either an effort or
			// an explicit thinkingConfig budget, never both.
			if o.ThinkingBudget > 0 {
				payload["extra_body"] = map[string]any{
					"google": map[string]any{
						"thinking_config": map[string]any{"thinking_budget": o.ThinkingBudget},
					},
				}
			} else if o.Effort != "" {
				payload["reasoning_effort"] = geminiEffort(o.Effort)
			}
		}
	}
	if o.MaxOutputTokens > 0 {
		payload["max_tokens"] = o.MaxOutputTokens
	}
}

// anthropicEffortBudget maps an effort onto budget_tokens for Claude models
// that think with a fixed budget instead of adaptive effort.
func anthropicEffortBudget(effort string) int {
	switch effort {
	case "minimal", "low":
		return 2048
	case "medium":
		return 8192
	case "high":
		return 16_384
	case "xhigh":
		return 32_000
	}
	return 0
}

// supportsBudgetThinking reports Claude models with extended thinking that
// predate adaptive thinking (3.7, the 4 / 4.1 / 4.5 line, Haiku 4.5).
func supportsBudgetThinking(model string) bool {
	m := strings.ToLower(strings.TrimSpace(model))
	if m == "" || strings.Contains(m, "-3-5") || strings.Contains(m, "-2-") || strings.Contains(m, "claude-3-opus") ||
		strings.Contains(m, "claude-3-sonnet") || strings.Contains(m, "claude-3-haiku") {
		return false
	}
	return !supportsAdaptiveThinking(model)
}
