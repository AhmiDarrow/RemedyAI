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
			tool: "prompt.slim_epoch",
			input: map[string]any{
				"system":      "sys",
				"goal":        "build the thing",
				"text":        "working",
				"session_id":  "s1",
				"epoch":       1,
				"total_steps": 64,
				"ledger":      []any{"- shell.exec [err] exit_code=1"},
				"home_dir":    "h",
				"provider":    "anthropic",
				"model":       "claude-opus-5",
			},
		},
		{
			tool: "prompt.should_continue",
			input: map[string]any{
				"goal":        "build the thing",
				"text":        "Done, tests pass",
				"session_id":  "s1",
				"tool_count":  3,
				"chat_mode":   false,
				"plan_mode":   false,
				"verify_seen": false,
				"last_results": []any{
					map[string]any{"name": "shell.exec", "ok": false, "tail": "exit_code=1"},
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
