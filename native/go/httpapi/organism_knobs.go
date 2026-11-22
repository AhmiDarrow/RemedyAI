package httpapi

import (
	"encoding/json"
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
