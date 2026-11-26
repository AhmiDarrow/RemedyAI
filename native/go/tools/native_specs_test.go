package tools

import (
	"encoding/json"
	"testing"
)

func TestIsModelHiddenTool(t *testing.T) {
	// Diagnostics, and the internal ABI the frontier surface replaced. The
	// workspace.* / shell.exec ids stay registered — approvals are
	// fingerprinted on the tool identity and the CLI still calls them — but the
	// model is shown read / edit / write / glob / grep / bash instead.
	for _, id := range []string{
		"text.slugify", "text.word_count", "runtime.probe", "json.canonical", "text.sha256",
		"workspace.read", "workspace.list", "workspace.write", "workspace.edit", "workspace.search",
		"shell.exec", "setup.session",
	} {
		if !IsModelHiddenTool(id) {
			t.Fatalf("%s must be hidden from the model", id)
		}
	}
	for _, id := range []string{
		"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo", "screenshot", "delegate",
		"web.fetch", "memory.search", "partner.vigil", "house_rules",
	} {
		if IsModelHiddenTool(id) {
			t.Fatalf("%s must stay model-visible", id)
		}
	}
}

func TestModelInputSchemaStripsInternalKeys(t *testing.T) {
	in := json.RawMessage(`{"type":"object","required":["query","home_dir"],"properties":{"query":{"type":"string"},"home_dir":{"type":"string"},"page_context":{"type":"string"},"_go_bound":{"type":"boolean"}},"additionalProperties":false}`)
	out := ModelInputSchema(in)
	var doc map[string]any
	if err := json.Unmarshal(out, &doc); err != nil {
		t.Fatal(err)
	}
	props := doc["properties"].(map[string]any)
	if _, ok := props["home_dir"]; ok {
		t.Fatal("home_dir must be stripped from the model schema")
	}
	if _, ok := props[GoBoundField]; ok {
		t.Fatal("_go_bound must be stripped from the model schema")
	}
	if _, ok := props["page_context"]; ok {
		t.Fatal("page_context must be stripped from the model schema")
	}
	if _, ok := props["query"]; !ok {
		t.Fatal("query must survive")
	}
	req := doc["required"].([]any)
	if len(req) != 1 || req[0] != "query" {
		t.Fatalf("required = %v", req)
	}
	// Registry schema untouched.
	var orig map[string]any
	if err := json.Unmarshal(in, &orig); err != nil {
		t.Fatal(err)
	}
	if _, ok := orig["properties"].(map[string]any)["home_dir"]; !ok {
		t.Fatal("input schema must not be mutated")
	}
}

func TestNativeSpecSchemasParseAndBindTheHome(t *testing.T) {
	reg := NewRegistry()
	if err := RegisterMailCalendarTools(reg); err != nil {
		t.Fatal(err)
	}
	if len(reg.List()) == 0 {
		t.Fatal("no tools registered")
	}
	for _, d := range reg.List() {
		var doc map[string]any
		if err := json.Unmarshal(d.InputSchema, &doc); err != nil {
			t.Fatalf("%s input schema: %v", d.ID, err)
		}
		props, _ := doc["properties"].(map[string]any)
		if _, ok := props["home_dir"]; ok {
			if _, bound := props[GoBoundField]; !bound {
				t.Fatalf("%s accepts home_dir but not %s", d.ID, GoBoundField)
			}
		}
	}
}
