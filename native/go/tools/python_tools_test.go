package tools

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

func TestIsModelHiddenTool(t *testing.T) {
	// Diagnostics, and the internal ABI the frontier surface replaced. The
	// workspace.* / shell.exec ids stay registered — approvals are
	// fingerprinted on the tool identity and the CLI still calls them — but the
	// model is shown read / edit / write / glob / grep / bash instead.
	for _, id := range []string{
		"text.slugify", "text.word_count", "runtime.probe", "json.canonical", "text.sha256",
		"workspace.read", "workspace.list", "workspace.write", "workspace.edit", "workspace.search",
		"shell.exec",
	} {
		if !IsModelHiddenTool(id) {
			t.Fatalf("%s must be hidden from the model", id)
		}
	}
	for _, id := range []string{
		"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo", "screenshot", "delegate",
		"web.fetch", "memory.search",
	} {
		if IsModelHiddenTool(id) {
			t.Fatalf("%s must stay model-visible", id)
		}
	}
}

func TestModelInputSchemaStripsInternalKeys(t *testing.T) {
	in := json.RawMessage(`{"type":"object","required":["query","home_dir"],"properties":{"query":{"type":"string"},"home_dir":{"type":"string"},"_go_bound":{"type":"boolean"}},"additionalProperties":false}`)
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

func TestPythonWorkerSchemasParseAndDescribeProperties(t *testing.T) {
	reg := NewRegistry()
	if err := RegisterPythonWorkerTools(reg, noopFrameCaller{}); err != nil {
		t.Fatal(err)
	}
	for _, d := range reg.List() {
		var doc map[string]any
		if err := json.Unmarshal(d.InputSchema, &doc); err != nil {
			t.Fatalf("%s input schema: %v", d.ID, err)
		}
		props, _ := doc["properties"].(map[string]any)
		switch {
		case hasPrefix(d.ID, "workspace."), hasPrefix(d.ID, "web."), hasPrefix(d.ID, "memory."), hasPrefix(d.ID, "skill."):
			for name, raw := range props {
				p, _ := raw.(map[string]any)
				if desc, _ := p["description"].(string); desc == "" {
					t.Fatalf("%s.%s lacks a description", d.ID, name)
				}
			}
		}
		if _, ok := props["home_dir"]; ok {
			if _, bound := props[GoBoundField]; !bound {
				t.Fatalf("%s accepts home_dir but not %s", d.ID, GoBoundField)
			}
		}
		if hasPrefix(d.ID, "workspace.") {
			if _, ok := props["home_dir"]; ok {
				t.Fatalf("%s must not advertise home_dir", d.ID)
			}
		}
	}
}

func hasPrefix(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }

// noopFrameCaller satisfies FrameCaller for schema-only registration tests.
type noopFrameCaller struct{}

func (noopFrameCaller) Call(context.Context, protocol.Frame) (protocol.Frame, error) {
	return protocol.Frame{}, errors.New("noop frame caller")
}
