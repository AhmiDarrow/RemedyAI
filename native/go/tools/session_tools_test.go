package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func sessionRegistry(t *testing.T) *Registry {
	t.Helper()
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	if err := RegisterSessionTools(registry); err != nil {
		t.Fatal(err)
	}
	return registry
}

func TestSessionToolDescriptorsDescribeEveryProperty(t *testing.T) {
	registry := sessionRegistry(t)
	want := map[string]Risk{"todo": RiskMutation, "screenshot": RiskReadOnly, "delegate": RiskMutation}
	for id, risk := range want {
		desc, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if desc.Risk != risk {
			t.Fatalf("%s risk=%v want %v", id, desc.Risk, risk)
		}
		var schema map[string]any
		if err := json.Unmarshal(desc.InputSchema, &schema); err != nil {
			t.Fatal(err)
		}
		props, _ := schema["properties"].(map[string]any)
		for name, raw := range props {
			p, _ := raw.(map[string]any)
			if d, _ := p["description"].(string); strings.TrimSpace(d) == "" {
				t.Fatalf("%s.%s has no description", id, name)
			}
		}
	}
}

func TestTodoRoundTripsThroughTheSessionFile(t *testing.T) {
	home := t.TempDir()
	registry := sessionRegistry(t)
	session := "sess-todo"

	out := mustCall(t, registry, "todo", map[string]any{
		"home_dir": home, "session_id": session,
		"items": []map[string]any{
			{"id": "a", "text": "read the spec", "status": "completed"},
			{"id": "b", "text": "write the tools", "status": "in_progress"},
			{"id": "c", "text": "run the tests"},
		},
	})
	if asInt(t, out["open"]) != 2 {
		t.Fatalf("open=%v", out["open"])
	}
	rows := toMaps(t, out["todos"])
	if len(rows) != 3 || rows[0]["id"] != "a" || rows[0]["content"] != "read the spec" {
		t.Fatalf("todos=%#v", rows)
	}
	if rows[2]["status"] != "pending" {
		t.Fatalf("status must default to pending: %#v", rows[2])
	}

	path := filepath.Join(home, "sessions", session, "todos.json")
	if out["path"] != filepath.ToSlash(path) {
		t.Fatalf("path=%v want %v", out["path"], filepath.ToSlash(path))
	}
	state, err := LoadTodoState(home, session)
	if err != nil {
		t.Fatal(err)
	}
	if len(state.Todos) != 3 || state.Open != 2 || state.Todos[1].Status != "in_progress" {
		t.Fatalf("persisted state=%#v", state)
	}

	// The list is replaced, not merged: what is not sent is gone.
	mustCall(t, registry, "todo", map[string]any{
		"home_dir": home, "session_id": session,
		"items": []map[string]any{{"id": "a", "text": "read the spec", "status": "completed"}},
	})
	state, err = LoadTodoState(home, session)
	if err != nil {
		t.Fatal(err)
	}
	if len(state.Todos) != 1 || state.Open != 0 {
		t.Fatalf("todo must replace the list: %#v", state)
	}

	// An empty list clears it.
	out = mustCall(t, registry, "todo", map[string]any{
		"home_dir": home, "session_id": session, "items": []map[string]any{},
	})
	if asInt(t, out["open"]) != 0 || len(toMaps(t, out["todos"])) != 0 {
		t.Fatalf("clear: %#v", out)
	}
}

func TestTodoRefusesBadListsLoudly(t *testing.T) {
	home := t.TempDir()
	registry := sessionRegistry(t)
	base := func(items []map[string]any) map[string]any {
		return map[string]any{"home_dir": home, "session_id": "s1", "items": items}
	}
	cases := []struct {
		name  string
		items []map[string]any
		want  string
	}{
		// The enum in the schema catches this one before the executor does; the
		// point is that the model is told the four statuses either way.
		{"bad status", []map[string]any{{"text": "x", "status": "doing"}}, "'pending', 'in_progress'"},
		{"two in progress", []map[string]any{
			{"id": "a", "text": "x", "status": "in_progress"},
			{"id": "b", "text": "y", "status": "in_progress"},
		}, "mark exactly one"},
		{"duplicate id", []map[string]any{{"id": "a", "text": "x"}, {"id": "a", "text": "y"}}, "repeats id"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, _, err := callTool(t, registry, "todo", base(c.items))
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("err=%v want %q", err, c.want)
			}
		})
	}
	if _, _, err := callTool(t, registry, "todo", map[string]any{
		"home_dir": home, "items": []map[string]any{{"text": "x"}},
	}); err == nil || !strings.Contains(err.Error(), "session") {
		t.Fatalf("todo without a session: %v", err)
	}
}

func TestLoadTodoStateIsEmptyBeforeAnyTodoCall(t *testing.T) {
	state, err := LoadTodoState(t.TempDir(), "never-used")
	if err != nil {
		t.Fatalf("an unused session must not be an error: %v", err)
	}
	if len(state.Todos) != 0 || state.Open != 0 {
		t.Fatalf("state=%#v", state)
	}
}

func TestScreenshotAttachesTheImageItself(t *testing.T) {
	home := requireHostSpawn(t)
	registry := sessionRegistry(t)
	out, res, err := callTool(t, registry, "screenshot", map[string]any{"label": "surface-test"})
	if err != nil {
		t.Skipf("no capturable screen in this environment: %v", err)
	}
	if len(res.Images) != 1 || res.Images[0].MediaType != "image/png" || len(res.Images[0].Data) == 0 {
		t.Fatalf("screenshot must return the pixels, not only a path: %#v", res.Images)
	}
	path, _ := out["path"].(string)
	if path == "" {
		t.Fatalf("screenshot output: %#v", out)
	}
	if !strings.HasPrefix(filepath.ToSlash(path), filepath.ToSlash(home)) {
		t.Fatalf("capture must land under the scratch home %s: %s", home, path)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("capture path unusable: %v", err)
	}
	if asInt(t, out["bytes"]) != len(res.Images[0].Data) {
		t.Fatalf("bytes=%v vs %d", out["bytes"], len(res.Images[0].Data))
	}
}

func TestDelegateDescriptorAdvertisesTheHandOff(t *testing.T) {
	registry := sessionRegistry(t)
	desc, err := registry.Latest("delegate")
	if err != nil {
		t.Fatalf("delegate must be registered: %v", err)
	}
	if desc.Risk != RiskMutation {
		t.Fatalf("delegate risk=%v", desc.Risk)
	}
	if len(desc.Capabilities) == 0 || desc.Capabilities[0] != "process.spawn" {
		t.Fatalf("delegate must declare the spawn capability: %v", desc.Capabilities)
	}
	var schema map[string]any
	if err := json.Unmarshal(desc.InputSchema, &schema); err != nil {
		t.Fatal(err)
	}
	props, _ := schema["properties"].(map[string]any)
	for _, field := range []string{"mission", "cwd", "agent", "allow_shell", "timeout_ms", "workspace_root", "write_roots"} {
		if _, ok := props[field]; !ok {
			t.Fatalf("delegate schema is missing %s", field)
		}
	}
	agent, _ := props["agent"].(map[string]any)
	enum, _ := agent["enum"].([]any)
	if len(enum) != 2 || enum[0] != "claude-code" || enum[1] != "codex" {
		t.Fatalf("agent enum=%v; codex stays listed so the shape is settled", enum)
	}
}
