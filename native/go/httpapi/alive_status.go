package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

func readAlivePublic(home string) map[string]any {
	home = strings.TrimSpace(home)
	if home == "" {
		return map[string]any{}
	}
	raw, err := os.ReadFile(filepath.Join(home, "alive.json"))
	if err != nil || len(raw) == 0 {
		return map[string]any{}
	}
	var state map[string]any
	if err := json.Unmarshal(raw, &state); err != nil {
		return map[string]any{}
	}
	out := map[string]any{}
	if d, ok := asStringMap(state["drives"]); ok {
		out["drives"] = d
	}
	if a, ok := asStringMap(state["affect"]); ok {
		out["affect"] = a
	}
	if s, ok := state["autobiography"].(string); ok {
		out["autobiography"] = s
	}
	if b, ok := state["beliefs"].([]any); ok {
		out["beliefs"] = b
	}
	if s, ok := state["last_idle"].(string); ok {
		out["last_idle"] = s
	}
	reach := map[string]any{"pending": false, "reason": "", "gloss": ""}
	if r, ok := asStringMap(state["reach_out"]); ok {
		// Honor the opt-in knob: never report a pending offer when off.
		cfg := loadHomeConfig(home)
		on := false
		if k, ok := advancedByKey()["proactive_reach_out"]; ok {
			if v, ok := resolveAdvanced(k, cfg).(bool); ok {
				on = v
			}
		}
		pend, _ := r["pending"].(bool)
		reach["pending"] = on && pend
		if on {
			if s, ok := r["reason"].(string); ok {
				reach["reason"] = s
			}
			if s, ok := r["gloss"].(string); ok {
				reach["gloss"] = s
			}
		}
	}
	out["reach_out"] = reach
	var pending []map[string]any
	if rows, ok := state["pending_changes"].([]any); ok {
		for _, row := range rows {
			item, ok := asStringMap(row)
			if !ok {
				continue
			}
			status, _ := item["status"].(string)
			if status != "" && status != "pending" {
				continue
			}
			pending = append(pending, map[string]any{
				"id":       item["id"],
				"proposal": item["proposal"],
				"status":   "pending",
			})
		}
	}
	if pending == nil {
		pending = []map[string]any{}
	}
	out["pending_changes"] = pending
	return out
}
