package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

func readInnerLanguagePublic(home string) map[string]any {
	home = strings.TrimSpace(home)
	out := map[string]any{
		"lexicon": map[string]any{"version": 1, "terms": []any{}, "core": []any{}, "count": 0},
		"audit":   []any{},
	}
	if home == "" {
		return out
	}
	if raw, err := os.ReadFile(filepath.Join(home, "inner_lexicon.json")); err == nil && len(raw) > 0 {
		var state map[string]any
		if json.Unmarshal(raw, &state) == nil {
			pub := map[string]any{"version": state["version"], "core": state["core"], "count": 0}
			var terms []map[string]any
			if m, ok := asStringMap(state["terms"]); ok {
				for key, val := range m {
					item, ok := asStringMap(val)
					if !ok {
						continue
					}
					gloss, _ := item["gloss"].(string)
					if strings.TrimSpace(gloss) == "" {
						continue
					}
					terms = append(terms, map[string]any{
						"term":        key,
						"gloss":       gloss,
						"source":      item["source"],
						"from":        item["from"],
						"uses":        item["uses"],
						"decode_ok":   item["decode_ok"],
						"decode_fail": item["decode_fail"],
						"coined_ts":   item["coined_ts"],
					})
				}
			}
			if terms == nil {
				terms = []map[string]any{}
			}
			pub["terms"] = terms
			pub["count"] = len(terms)
			out["lexicon"] = pub
		}
	}
	if raw, err := os.ReadFile(filepath.Join(home, "inner_language.jsonl")); err == nil && len(raw) > 0 {
		lines := strings.Split(string(raw), "\n")
		var rows []map[string]any
		for i := len(lines) - 1; i >= 0 && len(rows) < 20; i-- {
			line := strings.TrimSpace(lines[i])
			if line == "" {
				continue
			}
			var item map[string]any
			if json.Unmarshal([]byte(line), &item) != nil {
				continue
			}
			rows = append([]map[string]any{{
				"ts":      item["ts"],
				"kind":    item["kind"],
				"dense":   item["dense"],
				"decoded": item["decoded"],
			}}, rows...)
		}
		if rows == nil {
			rows = []map[string]any{}
		}
		out["audit"] = rows
	}
	return out
}
