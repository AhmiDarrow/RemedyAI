package httpapi

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Advanced organism knobs. Defaults match the Python SSOT in
// remedy.core.organism_knobs and the values already in production.
// Overrides live under config.toml [advanced]; mapped env vars still win.

type advancedKnob struct {
	Key       string
	Default   any
	Kind      string // int, float, bool
	Min, Max  float64
	Unit      string
	Group     string
	Help      string
	Env       string
	EnvInvert bool
}

var advancedKnobList = []advancedKnob{
	{"improve_tick_s", 60, "int", 20, 3600, "s", "Improve tick", "Default unattended learn interval.", "", false},
	{"improve_tick_min_s", 20, "int", 5, 600, "s", "Improve tick", "Fastest improve tick after heavy use.", "", false},
	{"improve_tick_max_s", 900, "int", 60, 7200, "s", "Improve tick", "Slowest improve tick when nothing changed or the machine is busy.", "", false},
	{"improve_heavy_window_s", 300, "int", 30, 3600, "s", "Improve tick", "How long a turn_end / after_tool_batch counts as heavy use.", "", false},
	{"turn_end_timeout_s", 30, "int", 5, 120, "s", "Learn timeouts", "Cap for background prompt.turn_end after a turn.", "", false},
	{"after_tool_batch_timeout_s", 10, "int", 2, 60, "s", "Learn timeouts", "Cap for mid-turn prompt.after_tool_batch.", "", false},
	{"pulse_learn_timeout_s", 45, "int", 10, 180, "s", "Learn timeouts", "Cap for one hearth partner.improve pulse.", "", false},
	{"soul_inject_chars", 1400, "int", 200, 8000, "chars", "Token budgets", "Max characters of soul injected after the cache-stable marker.", "", false},
	{"max_result_chars", 24000, "int", 2000, 256000, "chars", "Token budgets", "Max characters of each tool result kept for the model.", "REMEDY_REACT_MAX_RESULT_CHARS", false},
	{"soul_max_bytes", 64 * 1024, "int", 8 * 1024, 1024 * 1024, "bytes", "Caps", "Hard cap on the on-disk soul field.", "", false},
	{"residue_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps", "Hard cap on residue dumps during compact.", "", false},
	{"journal_max_bytes", 64 * 1024, "int", 4 * 1024, 1024 * 1024, "bytes", "Caps", "Hard cap on the vigil journal tail.", "", false},
	{"learn_log_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps", "Learn-log file size cap.", "", false},
	{"learn_log_max_lines", 400, "int", 50, 10000, "lines", "Caps", "Learn-log line cap.", "", false},
	{"max_episodes", 12, "int", 4, 128, "count", "Caps", "Soul episodes kept after salience retention.", "", false},
	{"max_future_dreams", 8, "int", 1, 64, "count", "Caps", "Queued future-dream notes kept.", "", false},
	{"max_phenotypes", 128, "int", 16, 1024, "count", "Caps", "Skill-genome phenotypes kept.", "", false},
	{"episode_recent_protect", 4, "int", 1, 32, "count", "Caps", "Newest soul episodes always kept.", "", false},
	{"episode_halflife_days", 30.0, "float", 1, 365, "days", "Caps", "Forgetting-curve half-life since last recall.", "", false},
	{"max_tensions", 16, "int", 2, 128, "count", "Caps", "Open relational tensions kept.", "", false},
	{"max_self_lessons", 24, "int", 2, 128, "count", "Caps", "Self-lessons kept on the soul field.", "", false},
	{"max_voice_markers", 12, "int", 2, 64, "count", "Caps", "Voice markers kept on the soul field.", "", false},
	{"max_open_threads", 10, "int", 2, 64, "count", "Caps", "Open threads kept on the soul field.", "", false},
	{"vigil_offer_turns", 12, "int", 1, 200, "turns", "Cadence", "Turns together before the one-time vigil offer.", "", false},
	{"dream_cooldown_s", 120.0, "float", 10, 86400, "s", "Cadence", "Minimum seconds between dream passes.", "", false},
	{"organism_compact_every_s", 86400.0, "float", 300, 30 * 86400, "s", "Cadence", "How often the organism cycle may compact residue.", "", false},
	{"organism_stall_after_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Cadence", "Idle time before the organism treats a skill path as stalled.", "", false},
	{"voice_evolution", true, "bool", 0, 0, "", "Voice & self-inject", "Daily voice drift at worker start. Off via REMEDY_NO_VOICE_EVOLUTION=1.", "REMEDY_NO_VOICE_EVOLUTION", true},
	{"self_inject_idle_s", 300, "int", 10, 7200, "s", "Voice & self-inject", "User-idle window before unattended code self-inject may run.", "", false},
	{"drives_enabled", true, "bool", 0, 0, "", "Alive", "Internal drives (curiosity, social, competence, rest) drift on the hearth tick.", "", false},
	{"drive_drift_rate", 0.04, "float", 0.005, 0.2, "", "Alive", "How far drives move toward a new value on each unattended tick.", "", false},
	{"proactive_reach_out", false, "bool", 0, 0, "", "Alive", "Opt-in: when a drive crosses the threshold, arm one gentle check-in. Off by default.", "", false},
	{"reach_out_threshold", 0.85, "float", 0.5, 1.0, "", "Alive", "Drive level that may arm a single reach-out offer.", "", false},
	{"reach_out_min_interval_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Alive", "Minimum time between reach-out offers. Never nagging.", "", false},
	{"autobiography_chars", 400, "int", 0, 2000, "chars", "Alive", "Max characters of the self-maintained autobiography injected after the cache marker.", "", false},
	{"affect_enabled", true, "bool", 0, 0, "", "Alive", "Affect state, shaped by outcomes and rapport, may lightly color tone.", "", false},
	{"affect_tone_weight", 0.15, "float", 0, 1, "", "Alive", "How strongly affect may influence tone. 0 turns the tone hint off.", "", false},
	{"voice_drift_from_affect", true, "bool", 0, 0, "", "Alive", "Daily voice drift follows affect and rapport instead of a random daily step.", "", false},
	{"outcome_learning", true, "bool", 0, 0, "", "Alive", "Track whether suggestions worked (accepted, fix held, unhappy) and feed the skill genome.", "", false},
	{"weekly_reflection", true, "bool", 0, 0, "", "Alive", "Weekly self-reflection may write proposed self-changes. They never apply without approval.", "", false},
	{"weekly_reflection_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Alive", "Minimum seconds between weekly reflection proposals.", "", false},
	{"inner_language", true, "bool", 0, 0, "", "Inner language", "Use Remedy's self-evolving inner language on inner and worker channels.", "", false},
	{"inner_language_verbosity", "dense", "str", 0, 0, "", "Inner language", "off, natural, or dense. Dense is the default for inner and worker channels.", "", false},
	{"inner_coinage", true, "bool", 0, 0, "", "Inner language", "Allow dreams and reflection to coin new terms (always with a gloss).", "", false},
	{"inner_coinage_rate", 0.3, "float", 0, 1, "", "Inner language", "How freely she may coin terms on a given dream or reflection pass.", "", false},
	{"inner_lexicon_cap", 128, "int", 8, 1024, "count", "Inner language", "Maximum coined terms. The seed core is not counted and cannot be pruned.", "", false},
	{"inner_audit", true, "bool", 0, 0, "", "Inner language", "Keep an audit log of dense + decoded forms. The log cannot hide entries while the language is in use.", "", false},
	{"mind_slots", 2, "int", 1, 8, "slots", "Local mind", "Parallel llama-server slots for concurrent inner tasks.", "", false},
	{"mind_ctx", 8192, "int", 2048, 32768, "tokens", "Local mind", "Context tokens per slot for the unified 4B mind host.", "", false},
	{"mind_flash_attn", true, "bool", 0, 0, "", "Local mind", "Enable flash attention on the local mind host.", "", false},
	{"mind_kv_cache", "q8_0", "str", 0, 0, "", "Local mind", "KV cache type: q8_0 (default), f16, bf16, or f8_e4m3.", "", false},
	{"mind_cache_prompt", true, "bool", 0, 0, "", "Local mind", "Reuse the prompt / prefix KV cache across calls (align with the Turn context marker).", "", false},
	{"mind_cache_ram_mib", 4096, "int", 0, 32768, "MiB", "Local mind", "Prompt-cache RAM cap for llama-server. 0 disables the cache-ram cap.", "", false},
	{"mind_mmap", true, "bool", 0, 0, "", "Local mind", "Memory-map the GGUF so idle-stop wake is a process start, not a full disk re-read.", "", false},
	{"mind_spec", "auto", "str", 0, 0, "", "Local mind", "Speculative decoding: auto (MTP if present, else ngram-mod), draft-mtp, ngram-mod, or off.", "", false},
	{"mind_spec_draft_n", 3, "int", 1, 8, "tokens", "Local mind", "Max draft tokens for speculative decoding.", "", false},
	{"mind_image_max_tokens", 1024, "int", 64, 4096, "tokens", "Local mind", "Cap on vision tokens per image (--image-max-tokens).", "", false},
	{"mind_thinking", false, "bool", 0, 0, "", "Local mind", "Allow Qwen thinking / <think> blocks on the local mind. Off by default.", "", false},
	{"mind_json_schema", true, "bool", 0, 0, "", "Local mind", "Constrain tool-call arguments and media digests with JSON schema / grammar.", "", false},
	{"mind_idle_stop_s", 600, "int", 0, 86400, "s", "Local mind", "Stop the mind host after this many idle seconds. 0 disables. Fast wake via mmap.", "", false},
	{"mind_quant", "auto", "str", 0, 0, "", "Local mind", "Weight quant: auto (Q6_K, or Q4_K_M on weak machines), q6_k, or q4_k_m.", "", false},
	{"mind_keep_gpu", false, "bool", 0, 0, "", "Local mind", "Keep the mind on the GPU even when RMB muscle is running. Off: mind yields to CPU.", "", false},
	{"always_send_images", false, "bool", 0, 0, "", "Local mind", "Always attach original image pixels to frontier / RMB muscle. Off (default) sends a digest only.", "", false},
}

func advancedByKey() map[string]advancedKnob {
	out := make(map[string]advancedKnob, len(advancedKnobList))
	for _, k := range advancedKnobList {
		out[k.Key] = k
	}
	return out
}

func coerceAdvanced(k advancedKnob, raw any) (any, bool) {
	if k.Kind == "bool" {
		return coerceBool(raw, false), true
	}
	if k.Kind == "str" {
		s := strings.ToLower(strings.TrimSpace(fmt.Sprint(raw)))
		allowed := map[string][]string{
			"inner_language_verbosity": {"off", "natural", "dense"},
			"mind_kv_cache":            {"q8_0", "f16", "bf16", "f8_e4m3"},
			"mind_spec":                {"auto", "draft-mtp", "mtp", "ngram-mod", "ngram", "off", "none"},
			"mind_quant":               {"auto", "q6_k", "q6", "q4_k_m", "q4"},
		}
		if opts, ok := allowed[k.Key]; ok {
			okVal := false
			for _, o := range opts {
				if s == o {
					okVal = true
					break
				}
			}
			if !okVal {
				s = strings.ToLower(fmt.Sprint(k.Default))
			}
		}
		return s, true
	}
	n, ok := anyToFloat(raw)
	if !ok {
		return nil, false
	}
	if n < k.Min {
		n = k.Min
	}
	if n > k.Max {
		n = k.Max
	}
	if k.Kind == "int" {
		return int(n), true
	}
	return n, true
}

func anyToFloat(raw any) (float64, bool) {
	switch t := raw.(type) {
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case float64:
		return t, true
	case json.Number:
		n, err := t.Float64()
		return n, err == nil
	case string:
		n, err := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return n, err == nil
	default:
		return 0, false
	}
}

func envAdvanced(k advancedKnob) (any, bool) {
	if k.Env == "" {
		return nil, false
	}
	raw := strings.TrimSpace(os.Getenv(k.Env))
	if raw == "" {
		return nil, false
	}
	if k.EnvInvert && k.Kind == "bool" {
		on := raw == "1" || strings.EqualFold(raw, "true") || strings.EqualFold(raw, "yes") || strings.EqualFold(raw, "on")
		return !on, true
	}
	return coerceAdvanced(k, raw)
}

func resolveAdvanced(k advancedKnob, cfg ConfigMap) any {
	if v, ok := envAdvanced(k); ok {
		return v
	}
	if table, ok := asStringMap(cfg["advanced"]); ok {
		if raw, exists := table[k.Key]; exists && raw != nil {
			if v, ok := coerceAdvanced(k, raw); ok {
				return v
			}
		}
	}
	if k.Key == "self_inject_idle_s" {
		if si, ok := asStringMap(cfg["self_inject"]); ok {
			if raw, exists := si["idle_seconds"]; exists && raw != nil {
				if v, ok := coerceAdvanced(k, raw); ok {
					return v
				}
			}
		}
	}
	return k.Default
}

func resolveAdvancedInt(cfg ConfigMap, key string) int {
	k, ok := advancedByKey()[key]
	if !ok {
		return 0
	}
	v, ok := coerceAdvanced(k, resolveAdvanced(k, cfg))
	if !ok {
		n, _ := anyToFloat(k.Default)
		return int(n)
	}
	n, _ := anyToFloat(v)
	return int(n)
}

func resolveAdvancedSeconds(cfg ConfigMap, key string) time.Duration {
	k, ok := advancedByKey()[key]
	if !ok {
		return 0
	}
	raw := resolveAdvanced(k, cfg)
	n, ok := anyToFloat(raw)
	if !ok {
		n, _ = anyToFloat(k.Default)
	}
	return time.Duration(n * float64(time.Second))
}

func advancedTable(cfg ConfigMap) map[string]any {
	out := make(map[string]any, len(advancedKnobList))
	for _, k := range advancedKnobList {
		out[k.Key] = resolveAdvanced(k, cfg)
	}
	return out
}

func advancedSchema() []map[string]any {
	out := make([]map[string]any, 0, len(advancedKnobList))
	for _, k := range advancedKnobList {
		out = append(out, map[string]any{
			"key":     k.Key,
			"default": k.Default,
			"kind":    k.Kind,
			"min":     k.Min,
			"max":     k.Max,
			"unit":    k.Unit,
			"group":   k.Group,
			"help":    k.Help,
			"env":     k.Env,
		})
	}
	return out
}

func normalizeAdvancedTable(raw any) map[string]any {
	src, ok := asStringMap(raw)
	if !ok {
		return map[string]any{}
	}
	index := advancedByKey()
	out := map[string]any{}
	for key, val := range src {
		k, exists := index[key]
		if !exists || val == nil {
			continue
		}
		v, ok := coerceAdvanced(k, val)
		if !ok {
			continue
		}
		if !advancedEqual(v, k.Default) {
			out[k.Key] = v
		}
	}
	return out
}

func advancedEqual(a, b any) bool {
	af, aOK := anyToFloat(a)
	bf, bOK := anyToFloat(b)
	if aOK && bOK {
		return af == bf
	}
	return a == b
}

func loadHomeConfig(home string) ConfigMap {
	if strings.TrimSpace(home) == "" {
		return ConfigMap{}
	}
	return LoadConfig(home)
}

func learnClockFrom(cfg ConfigMap) (def, min, max, heavy time.Duration) {
	def = resolveAdvancedSeconds(cfg, "improve_tick_s")
	min = resolveAdvancedSeconds(cfg, "improve_tick_min_s")
	max = resolveAdvancedSeconds(cfg, "improve_tick_max_s")
	heavy = resolveAdvancedSeconds(cfg, "improve_heavy_window_s")
	if def <= 0 {
		def = hearthLearnDefault
	}
	if min <= 0 {
		min = hearthLearnMin
	}
	if max <= 0 {
		max = hearthLearnMax
	}
	if heavy <= 0 {
		heavy = hearthLearnHeavy
	}
	if min > max {
		min, max = max, min
	}
	if def < min {
		def = min
	}
	if def > max {
		def = max
	}
	return def, min, max, heavy
}
