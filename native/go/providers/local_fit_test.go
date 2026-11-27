package providers

import (
	"strings"
	"testing"
)

func fitTool(name string) map[string]any {
	return map[string]any{"type": "function", "function": map[string]any{
		"name": name, "description": strings.Repeat("d", 400),
		"parameters": map[string]any{"type": "object"},
	}}
}

// A local model whose context is full keeps the tools it codes with. The
// allowlist once named tools the registry no longer has, so read, edit and
// bash were all dropped.
func TestFitLocalRequestKeepsTheCodingTools(t *testing.T) {
	var tools []map[string]any
	for _, name := range []string{
		"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo",
		"memory_search", "web_fetch", "calendar_list_events", "mail_send", "computer_click", "sessions_list",
	} {
		tools = append(tools, fitTool(name))
	}
	msgs := []map[string]any{
		{"role": "system", "content": strings.Repeat("s", 20000)},
		{"role": "user", "content": "fix the bug"},
	}
	_, kept, meta := FitLocalRequest(msgs, tools, 4096)
	names := map[string]bool{}
	for _, tl := range kept {
		fn, _ := tl["function"].(map[string]any)
		name, _ := fn["name"].(string)
		names[name] = true
	}
	for _, want := range []string{"read", "edit", "write", "glob", "grep", "bash"} {
		if !names[want] {
			t.Errorf("%s was dropped (levels=%v kept=%v)", want, meta["levels"], names)
		}
	}
	for _, gone := range []string{"calendar_list_events", "mail_send", "computer_click"} {
		if names[gone] {
			t.Errorf("%s should be dropped under pressure", gone)
		}
	}
}
