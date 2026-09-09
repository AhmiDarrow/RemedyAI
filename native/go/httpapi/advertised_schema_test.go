package httpapi

import (
	"encoding/json"
	"strings"
	"testing"
)

// The runtime binds workspace_root, home_dir and session_id itself and
// overwrites whatever the model sends. Advertising them invites the model to
// set one, get no effect, and spend a round working out why.
func TestAdvertisedSchemasHideRuntimeBoundFields(t *testing.T) {
	runner := NewCognitionTurnRunner(nil)
	surface := runner.registryToolSurface(false)
	if len(surface) == 0 {
		t.Fatal("no tools advertised")
	}

	hidden := []string{"workspace_root", "home_dir", "session_id", "project_path"}
	sawSomeProperties := false
	for _, tool := range surface {
		if len(tool.InputSchema) == 0 {
			continue
		}
		var doc map[string]any
		if err := json.Unmarshal(tool.InputSchema, &doc); err != nil {
			t.Fatalf("%s: advertised schema is not JSON: %v", tool.ID, err)
		}
		props, _ := doc["properties"].(map[string]any)
		if len(props) > 0 {
			sawSomeProperties = true
		}
		for _, key := range hidden {
			if _, ok := props[key]; ok {
				t.Fatalf("%s advertises runtime-bound field %q", tool.ID, key)
			}
		}
		if req, ok := doc["required"].([]any); ok {
			for _, r := range req {
				name, _ := r.(string)
				for _, key := range hidden {
					if strings.EqualFold(name, key) {
						t.Fatalf("%s requires runtime-bound field %q", tool.ID, key)
					}
				}
			}
		}
	}
	if !sawSomeProperties {
		t.Fatal("every advertised schema was empty; the stripper is too aggressive")
	}
}
