package httpapi

import (
	"os"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// ResolveListenModel picks an OpenAI-compatible live model when settings+secret
// are ready; otherwise returns a ScriptedModel that emits Hello/world.
func ResolveListenModel(home string) cognition.Model {
	cfg := LoadConfig(home)
	rawProvider := cfgString(cfg, "llm_provider", envOr("REMEDY_LLM_PROVIDER", "openai"))
	rawModel := cfgString(cfg, "llm_model", envOr("REMEDY_LLM_MODEL", ""))
	rawURL := cfgString(cfg, "llm_base_url", envOr("REMEDY_LLM_BASE_URL", ""))
	provider, model, baseURL := normalizeLLMSettings(rawProvider, rawModel, rawURL)
	key := secret.GetProviderSecret(home, provider)
	if key == "" {
		key = strings.TrimSpace(os.Getenv("REMEDY_LLM_API_KEY"))
	}
	if !providerCredentialsReady(cfg, provider, key) {
		return &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
			{{Text: "Hello ", Done: false}, {Text: "world", Done: true}},
		}}
	}
	return &providers.OpenAICompat{
		BaseURL: baseURL,
		APIKey:  key,
		Model:   model,
	}
}
