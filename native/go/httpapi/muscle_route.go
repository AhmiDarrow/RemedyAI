package httpapi

import (
	"fmt"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

// Muscle routing (keep in sync with remedy.runtime.rmb.coresidence).
// Auto never steals a session or global chat provider that is already a
// credentialed cloud model. Forced muscle_route knobs always win.
const (
	muscleFrontierMin = 0.85
	muscleEasyMax     = 0.4
)

var muscleLocalProviders = map[string]struct{}{
	"rmb": {}, "ollama": {}, "llamacpp": {}, "lmstudio": {}, "vllm": {},
}

var muscleHardHints = []string{
	"implement", "refactor", "debug", "stack trace", "traceback",
	"write a patch", "multi-file", "architecture", "prove", "formally",
	"optimize the algorithm",
}

var muscleCodeHints = []string{
	"```", "def ", "class ", "fn ", "func ", "#!/", "error:", "failed tests",
}

func isLocalChatProvider(prov, baseURL string) bool {
	p := strings.ToLower(strings.TrimSpace(prov))
	if _, ok := muscleLocalProviders[p]; ok {
		return true
	}
	if p == "custom" || p == "" {
		return isLocalURL(baseURL)
	}
	return isLocalURL(baseURL)
}

func estimateDifficulty(prompt, taskKind string) float64 {
	text := strings.TrimSpace(prompt)
	kind := strings.ToLower(strings.TrimSpace(taskKind))
	switch kind {
	case "continuity_core", "nano_classify", "brief_update", "dream", "digest", "media_digest":
		return 0.15
	case "vision_decode":
		return 0.35
	}
	n := len(text)
	score := 0.2
	if n > 4000 {
		score += 0.25
	} else if n > 1200 {
		score += 0.15
	} else if n < 240 {
		score -= 0.05
	}
	low := strings.ToLower(text)
	hits := 0
	for _, h := range muscleHardHints {
		if strings.Contains(low, h) {
			hits++
		}
	}
	if hits > 0 {
		extra := hits - 1
		if extra > 3 {
			extra = 3
		}
		score += 0.25 + 0.08*float64(extra)
	}
	for _, h := range muscleCodeHints {
		if strings.Contains(low, h) {
			score += 0.15
			break
		}
	}
	switch kind {
	case "agent", "coding", "tool_loop", "react":
		score += 0.2
	}
	if score < 0 {
		return 0
	}
	if score > 1 {
		return 1
	}
	return score
}

func muscleRoutePolicy(cfg ConfigMap) string {
	k, ok := advancedByKey()["muscle_route"]
	if !ok {
		return "auto"
	}
	s, _ := resolveAdvanced(k, cfg).(string)
	s = strings.ToLower(strings.TrimSpace(s))
	switch s {
	case "auto", "mind", "rmb", "local", "frontier":
		return s
	default:
		return "auto"
	}
}

// muscleRouteTarget is the pure decision used by applyMuscleRoute and tests.
// target is mind | rmb | local | frontier | "" (keep the already-resolved model).
func muscleRouteTarget(policy, prompt, taskKind, sessionProv, cfgProv string, hasMind, hasRMB, hasLocal, hasFrontier, activeCloud bool) string {
	policy = strings.ToLower(strings.TrimSpace(policy))
	if policy == "" {
		policy = "auto"
	}
	switch policy {
	case "mind":
		if hasMind {
			return "mind"
		}
		return ""
	case "rmb":
		if hasRMB {
			return "rmb"
		}
		return ""
	case "local":
		if hasLocal {
			return "local"
		}
		if hasRMB {
			return "rmb"
		}
		if hasMind {
			return "mind"
		}
		return ""
	case "frontier":
		if hasFrontier {
			return "frontier"
		}
		if hasRMB {
			return "rmb"
		}
		if hasMind {
			return "mind"
		}
		return ""
	}

	// auto: do not steal a cloud session or a configured cloud chat provider.
	if activeCloud {
		return ""
	}
	diff := estimateDifficulty(prompt, taskKind)
	if diff < muscleEasyMax {
		if hasMind {
			return "mind"
		}
		return ""
	}
	var local string
	if hasRMB {
		local = "rmb"
	} else if hasLocal {
		local = "local"
	}
	if local != "" && (diff < muscleFrontierMin || !hasFrontier) {
		return local
	}
	if hasFrontier && diff >= muscleFrontierMin {
		return "frontier"
	}
	if local != "" {
		return local
	}
	if hasMind {
		return "mind"
	}
	return ""
}

func mindChatModel(home string, cfg ConfigMap) cognition.Model {
	host := "127.0.0.1"
	port := visionDefaultPort
	if cfg != nil {
		vcfg := visionSectionFromConfig(cfg)
		if h := strings.TrimSpace(anyString(vcfg["host"])); h != "" {
			host = h
		}
		if p := anyInt(vcfg["port"], visionDefaultPort); p > 0 {
			port = p
		}
	}
	if !visionPortOpen(host, port) {
		return nil
	}
	return &providers.OpenAICompat{
		BaseURL: fmt.Sprintf("http://%s:%d/v1", host, port),
		APIKey:  "unused",
		Model:   "qwen35-4b-mind",
	}
}

func rmbChatModel(home string, cfg ConfigMap) cognition.Model {
	return tryLiveModel(home, cfg, "rmb", "", fmt.Sprintf("http://127.0.0.1:%d/v1", rmbDefaultPort))
}

func discoveredLocalModel(home string, cfg ConfigMap) cognition.Model {
	if k, ok := advancedByKey()["muscle_discover"]; ok {
		if v, ok := resolveAdvanced(k, cfg).(bool); ok && !v {
			return nil
		}
	}
	if last := lastCoresidenceReuseURL(home); last != "" && openaiEndpointReachable(last) {
		return &providers.OpenAICompat{BaseURL: last, APIKey: "unused", Model: "default"}
	}
	for _, probe := range []struct {
		kind string
		port int
	}{
		{"ollama", 11434},
		{"lmstudio", 1234},
		{"vllm", 8000},
		{"llamacpp", 8080},
		{"openai-compat", 5000},
		{"openai-compat", 8001},
	} {
		if probe.port == visionDefaultPort || probe.port == rmbDefaultPort {
			continue
		}
		if visionPortOpen("127.0.0.1", probe.port) {
			return &providers.OpenAICompat{
				BaseURL: fmt.Sprintf("http://127.0.0.1:%d/v1", probe.port),
				APIKey:  "unused",
				Model:   "default",
			}
		}
	}
	return nil
}

func frontierChatModel(home string, cfg ConfigMap) cognition.Model {
	seen := map[string]struct{}{}
	for _, prov := range []string{
		cfgString(cfg, "llm_provider", ""),
		cfgString(cfg, "last_llm_provider", ""),
	} {
		prov = strings.TrimSpace(prov)
		if prov == "" {
			continue
		}
		if _, dup := seen[strings.ToLower(prov)]; dup {
			continue
		}
		seen[strings.ToLower(prov)] = struct{}{}
		base := ""
		if strings.EqualFold(prov, cfgString(cfg, "llm_provider", "")) {
			base = cfgString(cfg, "llm_base_url", "")
		}
		if isLocalChatProvider(prov, base) {
			continue
		}
		if m := tryLiveModel(home, cfg, prov, "", base); m != nil {
			return m
		}
	}
	return nil
}

func applyMuscleRoute(home string, req TurnRequest, live cognition.Model) cognition.Model {
	cfg := LoadConfig(home)
	policy := muscleRoutePolicy(cfg)
	sessionProv := ""
	if req.Provider != nil {
		sessionProv = strings.TrimSpace(*req.Provider)
	}
	cfgProv := cfgString(cfg, "llm_provider", "")
	cfgURL := cfgString(cfg, "llm_base_url", "")
	activeCloud := false
	if sessionProv != "" && !isLocalChatProvider(sessionProv, "") {
		activeCloud = true
	} else if sessionProv == "" && cfgProv != "" && !isLocalChatProvider(cfgProv, cfgURL) {
		activeCloud = true
	}
	kind := ""
	if req.PlanMode {
		kind = "coding"
	}
	hasMind := mindChatModel(home, cfg) != nil
	hasRMB := rmbChatModel(home, cfg) != nil
	hasLocal := discoveredLocalModel(home, cfg) != nil
	hasFrontier := frontierChatModel(home, cfg) != nil
	target := muscleRouteTarget(policy, req.Prompt, kind, sessionProv, cfgProv, hasMind, hasRMB, hasLocal, hasFrontier, activeCloud)
	switch target {
	case "mind":
		if m := mindChatModel(home, cfg); m != nil {
			return m
		}
	case "rmb":
		if m := rmbChatModel(home, cfg); m != nil {
			return m
		}
	case "local":
		if m := discoveredLocalModel(home, cfg); m != nil {
			return m
		}
	case "frontier":
		if m := frontierChatModel(home, cfg); m != nil {
			return m
		}
	}
	return live
}
