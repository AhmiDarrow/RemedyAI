package httpapi

import (
	"os"
	"testing"
	"time"
)

func TestAdvancedDefaultsMatchProduction(t *testing.T) {
	tbl := advancedTable(ConfigMap{})
	if tbl["improve_tick_s"] != 60 {
		t.Fatalf("improve_tick_s=%v", tbl["improve_tick_s"])
	}
	if tbl["soul_inject_chars"] != 1400 {
		t.Fatalf("soul_inject_chars=%v", tbl["soul_inject_chars"])
	}
	if tbl["max_result_chars"] != 24000 {
		t.Fatalf("max_result_chars=%v", tbl["max_result_chars"])
	}
	if tbl["voice_evolution"] != true {
		t.Fatalf("voice_evolution=%v", tbl["voice_evolution"])
	}
	if tbl["self_inject_idle_s"] != 300 {
		t.Fatalf("self_inject_idle_s=%v", tbl["self_inject_idle_s"])
	}
	if tbl["onboarding"] != true {
		t.Fatalf("onboarding=%v", tbl["onboarding"])
	}
	if tbl["onboarding_reask"] != false {
		t.Fatalf("onboarding_reask=%v", tbl["onboarding_reask"])
	}
	if tbl["house_rules_permission"] != "confirm_sensitive" {
		t.Fatalf("house_rules_permission=%v", tbl["house_rules_permission"])
	}
	if len(advancedSchema()) != len(advancedKnobList) {
		t.Fatalf("schema=%d list=%d", len(advancedSchema()), len(advancedKnobList))
	}
}

func TestAdvancedOverridesAndClamp(t *testing.T) {
	cfg := ConfigMap{"advanced": map[string]any{"soul_inject_chars": 800, "improve_tick_s": 120}}
	if got := resolveAdvancedInt(cfg, "soul_inject_chars"); got != 800 {
		t.Fatalf("override=%d", got)
	}
	if got := resolveAdvancedInt(cfg, "max_episodes"); got != 12 {
		t.Fatalf("default leaked=%d", got)
	}
	out := normalizeAdvancedTable(map[string]any{
		"soul_inject_chars": 50,
		"improve_tick_s":    99999,
		"not_a_knob":        1,
		"vigil_offer_turns": 12,
	})
	if _, ok := out["not_a_knob"]; ok {
		t.Fatal("unknown key kept")
	}
	if out["soul_inject_chars"] != 200 {
		t.Fatalf("min clamp=%v", out["soul_inject_chars"])
	}
	if out["improve_tick_s"] != 3600 {
		t.Fatalf("max clamp=%v", out["improve_tick_s"])
	}
	if _, ok := out["vigil_offer_turns"]; ok {
		t.Fatal("default should be dropped")
	}
}

func TestReactMaxResultEnvWins(t *testing.T) {
	t.Setenv("REMEDY_REACT_MAX_RESULT_CHARS", "8000")
	cfg := ConfigMap{"advanced": map[string]any{"max_result_chars": 12000}}
	if got := resolveAdvancedInt(cfg, "max_result_chars"); got != 8000 {
		t.Fatalf("env should win: %d", got)
	}
	if err := os.Unsetenv("REMEDY_REACT_MAX_RESULT_CHARS"); err != nil {
		t.Fatal(err)
	}
	if got := resolveAdvancedInt(cfg, "max_result_chars"); got != 12000 {
		t.Fatalf("config after unset=%d", got)
	}
}

func TestNoVoiceEvolutionEnvStillDisables(t *testing.T) {
	t.Setenv("REMEDY_NO_VOICE_EVOLUTION", "1")
	cfg := ConfigMap{"advanced": map[string]any{"voice_evolution": true}}
	tbl := advancedTable(cfg)
	if tbl["voice_evolution"] != false {
		t.Fatalf("env should disable: %v", tbl["voice_evolution"])
	}
}

func TestLearnClockFromAdvanced(t *testing.T) {
	cfg := ConfigMap{"advanced": map[string]any{
		"improve_tick_s":          90,
		"improve_tick_min_s":      15,
		"improve_tick_max_s":      600,
		"improve_heavy_window_s":  120,
	}}
	def, min, max, heavy := learnClockFrom(cfg)
	if def != 90*time.Second || min != 15*time.Second || max != 600*time.Second || heavy != 120*time.Second {
		t.Fatalf("clock def=%s min=%s max=%s heavy=%s", def, min, max, heavy)
	}
	got := nextLearnIntervalBounds(90*time.Second, false, false, false, def, min, max)
	if got != 180*time.Second {
		t.Fatalf("backoff=%s", got)
	}
}

func TestLegacySelfInjectIdleMaps(t *testing.T) {
	cfg := ConfigMap{"self_inject": map[string]any{"idle_seconds": 600}}
	if got := resolveAdvancedInt(cfg, "self_inject_idle_s"); got != 600 {
		t.Fatalf("legacy=%d", got)
	}
}
