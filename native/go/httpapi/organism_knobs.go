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
	Key      string
	Default  any
	Kind     string // int, float, bool
	Min, Max float64
	Unit     string
	Group    string
	Help     string
	Env      string
}

var advancedKnobList = []advancedKnob{
	{"improve_tick_s", 60, "int", 20, 3600, "s", "Improve tick", "Default unattended learn interval.", ""},
	{"improve_tick_min_s", 20, "int", 5, 600, "s", "Improve tick", "Fastest improve tick after heavy use.", ""},
	{"improve_tick_max_s", 900, "int", 60, 7200, "s", "Improve tick", "Slowest improve tick when nothing changed or the machine is busy.", ""},
	{"improve_heavy_window_s", 300, "int", 30, 3600, "s", "Improve tick", "How long a turn_end / after_tool_batch counts as heavy use.", ""},
	{"pulse_learn_timeout_s", 45, "int", 10, 180, "s", "Learn timeouts", "Cap for one hearth partner.improve pulse.", ""},
	{"soul_inject_chars", 1400, "int", 200, 8000, "chars", "Token budgets", "Max characters of soul injected after the cache-stable marker.", ""},
	{"max_result_chars", 30000, "int", 2000, 256000, "chars", "Token budgets", "Max characters of each tool result kept for the model. Auto-tightens to 8,000 on a small local context window unless this knob or REMEDY_REACT_MAX_RESULT_CHARS is set.", "REMEDY_REACT_MAX_RESULT_CHARS"},
	{"soul_max_bytes", 64 * 1024, "int", 8 * 1024, 1024 * 1024, "bytes", "Caps", "Hard cap on the on-disk soul field.", ""},
	{"residue_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps", "Hard cap on residue dumps during compact.", ""},
	{"journal_max_bytes", 64 * 1024, "int", 4 * 1024, 1024 * 1024, "bytes", "Caps", "Hard cap on the vigil journal tail.", ""},
	{"learn_log_max_bytes", 256 * 1024, "int", 16 * 1024, 4 * 1024 * 1024, "bytes", "Caps", "Learn-log file size cap.", ""},
	{"learn_log_max_lines", 400, "int", 50, 10000, "lines", "Caps", "Learn-log line cap.", ""},
	{"max_episodes", 12, "int", 4, 128, "count", "Caps", "Soul episodes kept after salience retention.", ""},
	{"max_future_dreams", 8, "int", 1, 64, "count", "Caps", "Queued future-dream notes kept.", ""},
	{"max_phenotypes", 128, "int", 16, 1024, "count", "Caps", "Skill-genome phenotypes kept.", ""},
	{"episode_recent_protect", 4, "int", 1, 32, "count", "Caps", "Newest soul episodes always kept.", ""},
	{"episode_halflife_days", 30.0, "float", 1, 365, "days", "Caps", "Forgetting-curve half-life since last recall.", ""},
	{"max_tensions", 16, "int", 2, 128, "count", "Caps", "Open relational tensions kept.", ""},
	{"max_self_lessons", 24, "int", 2, 128, "count", "Caps", "Self-lessons kept on the soul field.", ""},
	{"max_voice_markers", 12, "int", 2, 64, "count", "Caps", "Voice markers kept on the soul field.", ""},
	{"max_open_threads", 10, "int", 2, 64, "count", "Caps", "Open threads kept on the soul field.", ""},
	{"vigil_offer_turns", 12, "int", 1, 200, "turns", "Cadence", "Turns together before the one-time vigil offer.", ""},
	{"dream_cooldown_s", 120.0, "float", 10, 86400, "s", "Cadence", "Minimum seconds between dream passes.", ""},
	{"organism_compact_every_s", 86400.0, "float", 300, 30 * 86400, "s", "Cadence", "How often the organism cycle may compact residue.", ""},
	{"organism_stall_after_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Cadence", "Idle time before the organism treats a skill path as stalled.", ""},
	{"self_inject_idle_s", 300, "int", 10, 7200, "s", "Voice & self-inject", "User-idle window before unattended code self-inject may run.", ""},
	{"drives_enabled", true, "bool", 0, 0, "", "Alive", "Internal drives (curiosity, social, competence, rest) drift on the hearth tick.", ""},
	{"drive_drift_rate", 0.04, "float", 0.005, 0.2, "", "Alive", "How far drives move toward a new value on each unattended tick.", ""},
	{"proactive_reach_out", false, "bool", 0, 0, "", "Alive", "Opt-in: when a drive crosses the threshold, arm one gentle check-in. Off by default.", ""},
	{"reach_out_threshold", 0.85, "float", 0.5, 1.0, "", "Alive", "Drive level that may arm a single reach-out offer.", ""},
	{"reach_out_min_interval_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Alive", "Minimum time between reach-out offers. Never nagging.", ""},
	{"autobiography_chars", 400, "int", 0, 2000, "chars", "Alive", "Max characters of the self-maintained autobiography injected after the cache marker.", ""},
	{"affect_enabled", true, "bool", 0, 0, "", "Alive", "Affect state, shaped by outcomes and rapport, may lightly color tone.", ""},
	{"affect_tone_weight", 0.15, "float", 0, 1, "", "Alive", "How strongly affect may influence tone. 0 turns the tone hint off.", ""},
	{"voice_drift_from_affect", true, "bool", 0, 0, "", "Alive", "Daily voice drift follows affect and rapport instead of a random daily step.", ""},
	{"outcome_learning", true, "bool", 0, 0, "", "Alive", "Track whether suggestions worked (accepted, fix held, unhappy) and feed the skill genome.", ""},
	{"weekly_reflection", true, "bool", 0, 0, "", "Alive", "Weekly self-reflection may write proposed self-changes. They never apply without approval.", ""},
	{"weekly_reflection_s", 7 * 86400.0, "float", 3600, 90 * 86400, "s", "Alive", "Minimum seconds between weekly reflection proposals.", ""},
	{"inner_language", true, "bool", 0, 0, "", "Inner language", "Use Remedy's self-evolving inner language on inner and worker channels.", ""},
	{"inner_language_verbosity", "dense", "str", 0, 0, "", "Inner language", "off, natural, or dense. Dense is the default for inner and worker channels.", ""},
	{"inner_coinage", true, "bool", 0, 0, "", "Inner language", "Allow dreams and reflection to coin new terms (always with a gloss).", ""},
	{"inner_coinage_rate", 0.3, "float", 0, 1, "", "Inner language", "How freely she may coin terms on a given dream or reflection pass.", ""},
	{"inner_lexicon_cap", 128, "int", 8, 1024, "count", "Inner language", "Maximum coined terms. The seed core is not counted and cannot be pruned.", ""},
	{"inner_audit", true, "bool", 0, 0, "", "Inner language", "Keep an audit log of dense + decoded forms. The log cannot hide entries while the language is in use.", ""},
	{"mind_slots", 2, "int", 1, 8, "slots", "Local mind", "Parallel llama-server slots for concurrent inner tasks.", ""},
	{"mind_ctx", 8192, "int", 2048, 32768, "tokens", "Local mind", "Context tokens per slot for the unified 4B mind host.", ""},
	{"mind_flash_attn", true, "bool", 0, 0, "", "Local mind", "Enable flash attention on the local mind host.", ""},
	{"mind_kv_cache", "q8_0", "str", 0, 0, "", "Local mind", "KV cache type: q8_0 (default), f16, bf16, or f8_e4m3.", ""},
	{"mind_cache_prompt", true, "bool", 0, 0, "", "Local mind", "Reuse the prompt / prefix KV cache across calls (align with the Turn context marker).", ""},
	{"mind_cache_ram_mib", 4096, "int", 0, 32768, "MiB", "Local mind", "Prompt-cache RAM cap for llama-server. 0 disables the cache-ram cap.", ""},
	{"mind_mmap", true, "bool", 0, 0, "", "Local mind", "Memory-map the GGUF so idle-stop wake is a process start, not a full disk re-read.", ""},
	{"mind_spec", "auto", "str", 0, 0, "", "Local mind", "Speculative decoding: auto (MTP if present, else ngram-mod), draft-mtp, ngram-mod, or off.", ""},
	{"mind_spec_draft_n", 3, "int", 1, 8, "tokens", "Local mind", "Max draft tokens for speculative decoding.", ""},
	{"mind_image_max_tokens", 1024, "int", 64, 4096, "tokens", "Local mind", "Cap on vision tokens per image (--image-max-tokens).", ""},
	{"mind_thinking", false, "bool", 0, 0, "", "Local mind", "Allow Qwen thinking / <think> blocks on the local mind. Off by default.", ""},
	{"mind_json_schema", true, "bool", 0, 0, "", "Local mind", "Constrain tool-call arguments and media digests with JSON schema / grammar.", ""},
	{"mind_idle_stop_s", 600, "int", 0, 86400, "s", "Local mind", "Stop the mind host after this many idle seconds. 0 disables. Fast wake via mmap.", ""},
	{"mind_quant", "auto", "str", 0, 0, "", "Local mind", "Weight quant: auto (Q6_K, or Q4_K_M on weak machines), q6_k, or q4_k_m.", ""},
	{"mind_keep_gpu", false, "bool", 0, 0, "", "Local mind", "Force the mind to keep its GPU layers even when local muscle is running. Off: the VRAM planner (muscle_gpu_policy) decides.", ""},
	{"mind_handles_easy_chats", true, "bool", 0, 0, "", "Local mind", "When on, auto routes clearly easy user chats to the local 4B mind even if a cloud provider is selected, to save tokens. Hard or complex chats stay on the selected provider. The classifier is conservative so quality does not drop.", ""},
	{"always_send_images", false, "bool", 0, 0, "", "Local mind", "Always attach original image pixels to frontier / RMB muscle. Off (default) sends a digest only.", ""},
	{"muscle_gpu_policy", "auto", "str", 0, 0, "", "Local muscle", "How the always-on mind and local muscle share one GPU: auto (share when both fully fit; if the muscle would fit alone, yield the mind to CPU + mmap so it can have the card), share, yield_mind, or keep_mind.", ""},
	{"muscle_schedule", "auto", "str", 0, 0, "", "Local muscle", "auto (overlap when there is headroom, serialize when the split is tight), overlap, or serialize. Keeps the mind responsive.", ""},
	{"muscle_route", "auto", "str", 0, 0, "", "Local muscle", "auto (clearly easy chats on the 4B mind, even when a cloud provider is selected; coding and harder work stay on the selected or local muscle, or escalate to a frontier provider), or force mind / rmb / local / frontier.", ""},
	{"muscle_discover", true, "bool", 0, 0, "", "Local muscle", "Discover already-running local OpenAI-compatible servers (Ollama, LM Studio, vLLM, llama-server). Measure only; never start or stop them.", ""},
	{"muscle_vram_headroom_mib", 768, "int", 128, 8192, "MiB", "Local muscle", "VRAM to leave free when planning mind + muscle layers, after subtracting any discovered external server.", ""},
	{"muscle_kv_cache", "auto", "str", 0, 0, "", "Local muscle", "KV cache type for RMB: auto (q8_0), q8_0, f16, bf16, or f8_e4m3.", ""},
	{"muscle_flash_attn", true, "bool", 0, 0, "", "Local muscle", "Enable flash attention on the RMB llama-server host.", ""},
	{"muscle_spec", "auto", "str", 0, 0, "", "Local muscle", "Speculative decoding for RMB: auto (MTP if the GGUF has a head, else ngram-mod), draft-mtp, ngram-mod, or off.", ""},
	{"messenger_join_desktop", true, "bool", 0, 0, "", "Messengers", "A message from an allowed messenger sender joins the conversation open on the desktop, and that conversation's replies are sent to their chat from then on. Turn off if you allow anyone but yourself: each sender then gets a conversation of their own.", ""},
	{"interop_inbound", false, "bool", 0, 0, "", "Agent interop", "Accept inbound A2A and MCP from other bots. Off by default. Localhost-only unless interop_bind is changed.", "REMEDY_INTEROP_INBOUND"},
	{"interop_bind", "127.0.0.1", "str", 0, 0, "", "Agent interop", "Where inbound A2A/MCP is accepted: 127.0.0.1 (default), lan, or 0.0.0.0. lan and 0.0.0.0 still require a token.", ""},
	{"interop_a2a", true, "bool", 0, 0, "", "Agent interop", "Serve the A2A Agent Card and JSON-RPC, and allow outbound A2A to peers.", ""},
	{"interop_mcp", true, "bool", 0, 0, "", "Agent interop", "Expose talk-to-Remedy MCP tools (ask, delegate, task status, share context).", ""},
	{"interop_claimidx", false, "bool", 0, 0, "", "Agent interop", "Optionally verify peer DIDs with ClaimIDX. Off: identity is pluggable and not required.", ""},
	{"interop_trust_policy", "gated", "str", 0, 0, "", "Agent interop", "open (known or unknown peers), gated (unverified peers limited), or verified (only DID-verified peers). Money, send, and delete always need the owner's approval.", ""},
	{"interop_auto_reply", true, "bool", 0, 0, "", "Agent interop", "When inbound is on, automatically answer allowed asks with a live session turn. Off: queue them for the owner. Does nothing while inbound itself is off.", ""},
	{"interop_audit", true, "bool", 0, 0, "", "Agent interop", "Write every inbound and outbound peer exchange to the interop audit log.", ""},
	{"interop_rate_per_min", 30, "int", 1, 600, "count", "Agent interop", "Max inbound requests per peer per minute.", ""},
	{"interop_hop_limit", 3, "int", 1, 8, "hops", "Agent interop", "Max forwarding hops. Stops ping-pong loops between bots.", ""},
	{"interop_max_peers", 64, "int", 1, 512, "count", "Agent interop", "Cap on saved peer contacts.", ""},
	{"interop_max_concurrent", 2, "int", 1, 16, "count", "Agent interop", "Max live inbound session turns running at once.", ""},
	{"interop_task_ttl_s", 86400, "int", 60, 7 * 86400, "s", "Agent interop", "How long peer tasks are kept, and the timeout for a live inbound turn.", ""},
	{"onboarding", true, "bool", 0, 0, "", "House rules", "Offer a first-run house-rules conversation after the mind is ready. Off: skip straight to defaults (or Traditional setup if the user opens it).", ""},
	{"onboarding_reask", false, "bool", 0, 0, "", "House rules", "If the first-run offer was ignored (not completed or skipped), offer it once more. Off (default): never auto-prompt again. The owner can still say let's go over house rules.", ""},
	{"house_rules_permission", "confirm_sensitive", "str", 0, 0, "", "House rules", "How talk-to-change settings is allowed: confirm_sensitive (default — ordinary prefs apply on request, consent/privacy/interop need an explicit yes), confirm_all, or plain (ordinary prefs apply on request; sensitive keys still need a yes).", ""},
	{"house_rules_audit", true, "bool", 0, 0, "", "House rules", "Write house-rules changes to the local audit log (secrets are never written).", ""},
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
			"muscle_gpu_policy":        {"auto", "share", "yield_mind", "keep_mind"},
			"muscle_schedule":          {"auto", "overlap", "serialize"},
			"muscle_route":             {"auto", "mind", "rmb", "local", "frontier"},
			"muscle_kv_cache":          {"auto", "q8_0", "f16", "bf16", "f8_e4m3"},
			"muscle_spec":              {"auto", "draft-mtp", "mtp", "ngram-mod", "ngram", "off", "none"},
			"interop_bind":             {"127.0.0.1", "localhost", "loopback", "lan", "0.0.0.0", "off"},
			"interop_trust_policy":     {"open", "gated", "verified"},
			"house_rules_permission":   {"confirm_sensitive", "confirm_all", "plain"},
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

// advancedExplicit reports whether the owner (or an env var) overrode this
// knob, so callers can leave zero-value Config fields as "auto".
func advancedExplicit(cfg ConfigMap, key string) bool {
	k, ok := advancedByKey()[key]
	if !ok {
		return false
	}
	if _, ok := envAdvanced(k); ok {
		return true
	}
	if table, ok := asStringMap(cfg["advanced"]); ok {
		if raw, exists := table[key]; exists && raw != nil {
			return true
		}
	}
	return false
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
