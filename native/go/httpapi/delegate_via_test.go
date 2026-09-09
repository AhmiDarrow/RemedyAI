package httpapi

import (
	"encoding/json"
	"testing"
)

// A delegated mission's tool activity belongs to the sub-agent, not to Remedy.
// The provenance has to survive the whole path — token, SSE frame, turn log,
// and the replay that rebuilds frames from that log — or the trail credits
// Remedy with work another agent did.
func TestDelegatedWorkKeepsItsProvenance(t *testing.T) {
	call := parseToolCallToken(`@@tool_call:{"name":"claude-code:Edit","args":{"path":"a.go"},"id":"c1","via":"claude-code"}`)
	if call["via"] != "claude-code" {
		t.Fatalf("tool_call dropped via: %#v", call)
	}

	res := parseToolResultToken(`@@tool_result:{"name":"claude-code:Edit","preview":"ok","ok":true,"id":"c1","via":"claude-code"}`)
	if res.Via != "claude-code" {
		t.Fatalf("tool_result dropped via: %#v", res)
	}
	if row := res.record(); row["via"] != "claude-code" {
		t.Fatalf("persisted row dropped via: %#v", row)
	}

	// The replay path rebuilds frames from log records, so it must agree.
	for _, tc := range []struct {
		name  string
		block logBlock
	}{
		{"tool_use", logBlock{Type: "tool_use", ID: "c1", Name: "claude-code:Edit", Via: "claude-code", Input: json.RawMessage(`{}`)}},
		{"tool_result", logBlock{Type: "tool_result", ToolUseID: "c1", Name: "claude-code:Edit", Via: "claude-code"}},
	} {
		rec := turnRecord{Seq: 7, T: "message", Role: "assistant", Blocks: []logBlock{tc.block}}
		_, frame, ok := rec.messageFrame()
		if !ok {
			t.Fatalf("%s produced no frame", tc.name)
		}
		if frame["via"] != "claude-code" {
			t.Fatalf("%s replay frame dropped via: %#v", tc.name, frame)
		}
	}

	// Remedy's own work carries no provenance field at all.
	own := parseToolCallToken(`@@tool_call:{"name":"read","args":{},"id":"c2"}`)
	if _, present := own["via"]; present {
		t.Fatalf("Remedy's own call should carry no via: %#v", own)
	}
}
