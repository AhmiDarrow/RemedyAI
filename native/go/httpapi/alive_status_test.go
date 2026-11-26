package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestReadAlivePublicDecodesAndHidesOfferWhenOff(t *testing.T) {
	home := t.TempDir()
	raw := map[string]any{
		"schema":          1,
		"drives":          map[string]any{"curiosity": 0.9, "social": 0.2, "competence": 0.4, "rest": 0.3},
		"affect":          map[string]any{"valence": 0.6, "arousal": 0.4, "label": "warm"},
		"autobiography":   "I am Remedy, Ahmi's partner on this machine.",
		"beliefs":         []any{"Ahmi likes concise answers."},
		"last_idle":       "dream",
		"reach_out":       map[string]any{"pending": true, "reason": "curiosity", "gloss": "I wanted to look into something."},
		"pending_changes": []any{map[string]any{"id": "ref-1", "proposal": "Ease the tick.", "status": "pending"}},
	}
	b, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "alive.json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
	got := readAlivePublic(home)
	if got["autobiography"] != raw["autobiography"] {
		t.Fatalf("autobiography=%v", got["autobiography"])
	}
	// Knob default is off, so a stale pending offer must not surface.
	reach, _ := got["reach_out"].(map[string]any)
	if reach["pending"] != false {
		t.Fatalf("reach_out should be hidden when opt-in is off: %#v", reach)
	}
	pending, _ := got["pending_changes"].([]map[string]any)
	if len(pending) != 1 || pending[0]["id"] != "ref-1" {
		t.Fatalf("pending=%#v", got["pending_changes"])
	}
}
