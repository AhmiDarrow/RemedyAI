package tools

import (
	"encoding/json"
	"testing"
)

// The runner reaches prompt.* through the RMDY executor, which does not run
// registry validation — so a field the runner sends but the schema does not
// declare fails only on a path that does validate. These tests pin the two
// payload shapes the turn runner actually produces.
func TestPromptSchemasAcceptRunnerPayloads(t *testing.T) {
	reg := NewRegistry()
	if err := RegisterPythonWorkerTools(reg, noopFrameCaller{}); err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		tool  string
		input map[string]any
	}{
		{
			tool: "prompt.turn_end",
			input: map[string]any{
				"message":        "I want to always use TypeScript",
				"assistant_text": "From now on we will use TypeScript.",
				"session_id":     "s1",
				"home_dir":       "h",
				"tool_steps": []any{
					map[string]any{"tool": "read", "ok": true, "result": "ok"},
					map[string]any{"tool": "edit", "ok": true, "result": "ok"},
					map[string]any{"tool": "bash", "ok": true, "result": "exit 0"},
				},
			},
		},
	}

	for _, tc := range cases {
		desc, err := reg.Latest(tc.tool)
		if err != nil {
			t.Fatalf("%s: %v", tc.tool, err)
		}
		schema, err := compileSchema(desc.ID+"-input", desc.InputSchema)
		if err != nil {
			t.Fatalf("%s schema: %v", tc.tool, err)
		}
		raw, err := json.Marshal(tc.input)
		if err != nil {
			t.Fatal(err)
		}
		var decoded any
		if err := json.Unmarshal(raw, &decoded); err != nil {
			t.Fatal(err)
		}
		if err := schema.Validate(decoded); err != nil {
			t.Fatalf("%s rejects the payload the runner sends: %v", tc.tool, err)
		}
	}
}
