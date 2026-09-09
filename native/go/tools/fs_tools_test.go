package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fileRegistry is the file tool surface with a permissive authorizer: the
// capability token is the registry's gate and is supplied by the runtime, so
// these tests exercise the tools, not the token plumbing.
func fileRegistry(t *testing.T) *Registry {
	t.Helper()
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	if err := RegisterFileTools(registry); err != nil {
		t.Fatal(err)
	}
	return registry
}

func callTool(t *testing.T, registry *Registry, id string, input map[string]any) (map[string]any, Result, error) {
	t.Helper()
	raw, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	res, err := registry.Execute(context.Background(), Request{
		ToolID: id, Version: 1, Input: raw, CapabilityToken: []byte("test-token"),
	})
	if err != nil {
		return nil, res, err
	}
	out := map[string]any{}
	if len(res.Output) > 0 {
		if err := json.Unmarshal(res.Output, &out); err != nil {
			t.Fatalf("%s output not an object: %v %s", id, err, res.Output)
		}
	}
	return out, res, nil
}

func mustCall(t *testing.T, registry *Registry, id string, input map[string]any) map[string]any {
	t.Helper()
	out, _, err := callTool(t, registry, id, input)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	return out
}

func writeFileForTest(t *testing.T, dir, name, body string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func asInt(t *testing.T, v any) int {
	t.Helper()
	f, ok := v.(float64)
	if !ok {
		t.Fatalf("not a number: %#v", v)
	}
	return int(f)
}

func TestFileToolDescriptorsDescribeEveryProperty(t *testing.T) {
	registry := fileRegistry(t)
	want := map[string]Risk{
		"read": RiskReadOnly, "glob": RiskReadOnly, "grep": RiskReadOnly,
		"edit": RiskMutation, "write": RiskMutation,
	}
	for id, risk := range want {
		desc, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if desc.Risk != risk {
			t.Fatalf("%s risk=%v want %v", id, desc.Risk, risk)
		}
		if desc.Runtime != RuntimeGo {
			t.Fatalf("%s runtime=%s", id, desc.Runtime)
		}
		var schema map[string]any
		if err := json.Unmarshal(desc.InputSchema, &schema); err != nil {
			t.Fatalf("%s input schema: %v", id, err)
		}
		props, _ := schema["properties"].(map[string]any)
		if len(props) == 0 {
			t.Fatalf("%s advertises no properties", id)
		}
		for name, raw := range props {
			p, _ := raw.(map[string]any)
			if desc, _ := p["description"].(string); strings.TrimSpace(desc) == "" {
				t.Fatalf("%s.%s has no description — a model guesses at undocumented fields", id, name)
			}
		}
		if schema["additionalProperties"] != false {
			t.Fatalf("%s must refuse unknown properties", id)
		}
	}
}

func TestReadPagesALongFileAndReportsTotals(t *testing.T) {
	root := t.TempDir()
	var b strings.Builder
	for i := 1; i <= 5000; i++ {
		fmt.Fprintf(&b, "line %d\n", i)
	}
	writeFileForTest(t, root, "big.txt", b.String())
	registry := fileRegistry(t)

	first := mustCall(t, registry, "read", map[string]any{"path": "big.txt", "workspace_root": root})
	if got := asInt(t, first["total_lines"]); got != 5000 {
		t.Fatalf("total_lines=%d", got)
	}
	if first["truncated"] != true {
		t.Fatalf("a 5000-line file must be truncated at the 2000-line cap: %#v", first)
	}
	next := asInt(t, first["next_offset"])
	if next != 2001 {
		t.Fatalf("next_offset=%d want 2001", next)
	}
	content, _ := first["content"].(string)
	if !strings.HasPrefix(content, "1\tline 1\n") {
		t.Fatalf("read must number lines from 1: %q", content[:40])
	}
	if !strings.Contains(content, "\n2000\tline 2000\n") {
		t.Fatalf("first page must end at line 2000")
	}

	// next_offset is usable: paging with it continues exactly where we stopped.
	second := mustCall(t, registry, "read", map[string]any{
		"path": "big.txt", "offset": next, "workspace_root": root,
	})
	body, _ := second["content"].(string)
	if !strings.HasPrefix(body, "2001\tline 2001\n") {
		t.Fatalf("second page did not continue at %d: %q", next, body[:40])
	}

	last := mustCall(t, registry, "read", map[string]any{
		"path": "big.txt", "offset": 4999, "workspace_root": root,
	})
	if last["truncated"] != false || asInt(t, last["next_offset"]) != 0 {
		t.Fatalf("the final page must report no more lines: %#v", last)
	}

	if _, _, err := callTool(t, registry, "read", map[string]any{
		"path": "big.txt", "offset": 9000, "workspace_root": root,
	}); err == nil || !strings.Contains(err.Error(), "total_lines 5000") {
		t.Fatalf("an offset past the end must say how long the file is: %v", err)
	}
}

func TestReadReturnsAnImageBlockForAnImageFile(t *testing.T) {
	root := t.TempDir()
	// A one-pixel PNG: the bytes matter only as bytes.
	png := []byte{
		0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A,
		0x00, 0x00, 0x00, 0x0D, 'I', 'H', 'D', 'R',
	}
	if err := os.WriteFile(filepath.Join(root, "shot.png"), png, 0o644); err != nil {
		t.Fatal(err)
	}
	registry := fileRegistry(t)
	out, res, err := callTool(t, registry, "read", map[string]any{"path": "shot.png", "workspace_root": root})
	if err != nil {
		t.Fatal(err)
	}
	if out["is_image"] != true || out["media_type"] != "image/png" {
		t.Fatalf("image read output: %#v", out)
	}
	if len(res.Images) != 1 || string(res.Images[0].Data) != string(png) {
		t.Fatalf("read must attach the image itself: %#v", res.Images)
	}
	if body, _ := out["content"].(string); body != "" {
		t.Fatalf("an image must not also be sent as text: %q", body)
	}
}

func TestReadRefusesBinaryAndDirectories(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "blob.bin"), []byte{0x00, 0x01, 0x02}, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	registry := fileRegistry(t)
	if _, _, err := callTool(t, registry, "read", map[string]any{"path": "blob.bin", "workspace_root": root}); err == nil ||
		!strings.Contains(err.Error(), "not UTF-8") {
		t.Fatalf("binary read: %v", err)
	}
	if _, _, err := callTool(t, registry, "read", map[string]any{"path": "sub", "workspace_root": root}); err == nil ||
		!strings.Contains(err.Error(), "glob") {
		t.Fatalf("directory read must point at glob: %v", err)
	}
	if _, _, err := callTool(t, registry, "read", map[string]any{"path": "gone.txt", "workspace_root": root}); err == nil ||
		!strings.Contains(err.Error(), "does not exist") {
		t.Fatalf("missing file: %v", err)
	}
}

func TestFileToolsStayInsideTheBoundRoot(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	secret := filepath.Join(outside, "secret.txt")
	if err := os.WriteFile(secret, []byte("no"), 0o644); err != nil {
		t.Fatal(err)
	}
	registry := fileRegistry(t)

	for _, path := range []string{secret, "../" + filepath.Base(outside) + "/secret.txt"} {
		_, _, err := callTool(t, registry, "read", map[string]any{"path": path, "workspace_root": root})
		if err == nil {
			t.Fatalf("%q escaped the jail", path)
		}
		if !strings.Contains(err.Error(), root) {
			t.Fatalf("the jail error must name the root so the model can correct itself: %v", err)
		}
	}
	if _, _, err := callTool(t, registry, "read", map[string]any{"path": "a.txt"}); err == nil ||
		!strings.Contains(err.Error(), "no workspace root") {
		t.Fatalf("an unbound root must be an explicit error: %v", err)
	}
}

func TestEditRefusesAmbiguousMatchesWithTheCount(t *testing.T) {
	root := t.TempDir()
	writeFileForTest(t, root, "app.go", "x := 1\ny := 2\nx := 1\n")
	registry := fileRegistry(t)

	_, _, err := callTool(t, registry, "edit", map[string]any{
		"path": "app.go", "old_string": "x := 1", "new_string": "x := 3", "workspace_root": root,
	})
	if err == nil {
		t.Fatal("an edit that matches twice must fail")
	}
	if !strings.Contains(err.Error(), "matches 2 times") || !strings.Contains(err.Error(), "replace_all") {
		t.Fatalf("the error must report the count and the way out: %v", err)
	}
	if body, rerr := os.ReadFile(filepath.Join(root, "app.go")); rerr != nil || strings.Contains(string(body), "x := 3") {
		t.Fatalf("a refused edit must not touch the file: %q %v", body, rerr)
	}

	out := mustCall(t, registry, "edit", map[string]any{
		"path": "app.go", "old_string": "x := 1", "new_string": "x := 3",
		"replace_all": true, "workspace_root": root,
	})
	if asInt(t, out["replacements"]) != 2 {
		t.Fatalf("replace_all=%#v", out)
	}

	_, _, err = callTool(t, registry, "edit", map[string]any{
		"path": "app.go", "old_string": "nowhere", "new_string": "z", "workspace_root": root,
	})
	if err == nil || !strings.Contains(err.Error(), "0 matches") {
		t.Fatalf("a miss must report zero matches: %v", err)
	}
}

func TestEditPreservesCRLFAndBOM(t *testing.T) {
	root := t.TempDir()
	original := "\ufeffalpha\r\nbeta\r\ngamma\r\n"
	writeFileForTest(t, root, "win.txt", original)
	registry := fileRegistry(t)

	// The model writes \n; the file is \r\n. One line changes, not all three.
	out := mustCall(t, registry, "edit", map[string]any{
		"path": "win.txt", "old_string": "beta", "new_string": "delta", "workspace_root": root,
	})
	if out["line_ending"] != "crlf" || out["bom"] != true {
		t.Fatalf("edit output: %#v", out)
	}
	body, err := os.ReadFile(filepath.Join(root, "win.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "\ufeffalpha\r\ndelta\r\ngamma\r\n" {
		t.Fatalf("CRLF/BOM not preserved: %q", body)
	}

	// A multi-line replacement written with \n also comes back CRLF.
	mustCall(t, registry, "edit", map[string]any{
		"path": "win.txt", "old_string": "delta", "new_string": "one\ntwo", "workspace_root": root,
	})
	body, err = os.ReadFile(filepath.Join(root, "win.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ReplaceAll(string(body), "\r\n", ""), "\n") {
		t.Fatalf("a bare LF leaked into a CRLF file: %q", body)
	}
	if !strings.Contains(string(body), "one\r\ntwo") {
		t.Fatalf("replacement not normalized: %q", body)
	}
}

func TestEditRejectsNoOpAndEmptyOldString(t *testing.T) {
	root := t.TempDir()
	writeFileForTest(t, root, "a.txt", "hello\n")
	registry := fileRegistry(t)
	if _, _, err := callTool(t, registry, "edit", map[string]any{
		"path": "a.txt", "old_string": "hello", "new_string": "hello", "workspace_root": root,
	}); err == nil || !strings.Contains(err.Error(), "identical") {
		t.Fatalf("no-op edit: %v", err)
	}
	if _, _, err := callTool(t, registry, "edit", map[string]any{
		"path": "a.txt", "old_string": "", "new_string": "x", "workspace_root": root,
	}); err == nil || !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("empty old_string: %v", err)
	}
}

func TestWriteCreatesParentsAtomically(t *testing.T) {
	root := t.TempDir()
	registry := fileRegistry(t)
	out := mustCall(t, registry, "write", map[string]any{
		"path": "pkg/sub/new.txt", "content": "hi\n", "workspace_root": root,
	})
	if out["created"] != true || asInt(t, out["bytes"]) != 3 {
		t.Fatalf("write output: %#v", out)
	}
	body, err := os.ReadFile(filepath.Join(root, "pkg", "sub", "new.txt"))
	if err != nil || string(body) != "hi\n" {
		t.Fatalf("write: %q %v", body, err)
	}
	out = mustCall(t, registry, "write", map[string]any{
		"path": "pkg/sub/new.txt", "content": "bye\n", "workspace_root": root,
	})
	if out["created"] != false {
		t.Fatalf("overwrite must not claim creation: %#v", out)
	}
	// No temp file is left behind.
	entries, err := os.ReadDir(filepath.Join(root, "pkg", "sub"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("atomic write left residue: %#v", entries)
	}
}

func TestGlobIsRecursiveSortedAndCapped(t *testing.T) {
	root := t.TempDir()
	writeFileForTest(t, root, "top.go", "package a\n")
	writeFileForTest(t, root, "src/one.go", "package a\n")
	writeFileForTest(t, root, "src/deep/two.go", "package a\n")
	writeFileForTest(t, root, "src/deep/notes.md", "x\n")
	writeFileForTest(t, root, "node_modules/skip.go", "package a\n")
	registry := fileRegistry(t)

	out := mustCall(t, registry, "glob", map[string]any{"pattern": "**/*.go", "workspace_root": root})
	files := toStrings(t, out["files"])
	joined := strings.Join(files, "|")
	for _, want := range []string{"top.go", "src/one.go", "src/deep/two.go"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("glob missed %s: %v", want, files)
		}
	}
	if strings.Contains(joined, "node_modules") {
		t.Fatalf("glob must skip node_modules unless asked: %v", files)
	}
	if strings.Contains(joined, "notes.md") {
		t.Fatalf("glob matched the wrong extension: %v", files)
	}

	// A pattern that names a skipped tree opts back into it.
	out = mustCall(t, registry, "glob", map[string]any{"pattern": "node_modules/**/*.go", "workspace_root": root})
	if len(toStrings(t, out["files"])) != 1 {
		t.Fatalf("an explicit node_modules pattern must search it: %#v", out)
	}

	// One level only, without **.
	out = mustCall(t, registry, "glob", map[string]any{"pattern": "*.go", "workspace_root": root})
	if files := toStrings(t, out["files"]); len(files) != 1 || !strings.HasSuffix(files[0], "top.go") {
		t.Fatalf("a pattern without ** must stay at one level: %v", files)
	}

	// Scoped to a subdirectory, the pattern is matched from there.
	out = mustCall(t, registry, "glob", map[string]any{"pattern": "deep/*.go", "path": "src", "workspace_root": root})
	if files := toStrings(t, out["files"]); len(files) != 1 || !strings.HasSuffix(files[0], "two.go") {
		t.Fatalf("scoped glob: %v", files)
	}
}

func TestGlobCapsResults(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < globMaxResults+10; i++ {
		writeFileForTest(t, root, fmt.Sprintf("f%03d.txt", i), "x")
	}
	registry := fileRegistry(t)
	out := mustCall(t, registry, "glob", map[string]any{"pattern": "*.txt", "workspace_root": root})
	if out["truncated"] != true || asInt(t, out["count"]) != globMaxResults {
		t.Fatalf("glob cap: count=%v truncated=%v", out["count"], out["truncated"])
	}
}

func TestGrepReturnsContextAndReportsCapping(t *testing.T) {
	root := t.TempDir()
	writeFileForTest(t, root, "src/a.go", "package a\n\nfunc Target() {}\n\nvar x = 1\n")
	writeFileForTest(t, root, "src/b.md", "Target is documented here\n")
	registry := fileRegistry(t)

	out := mustCall(t, registry, "grep", map[string]any{
		"pattern": `func Tar\w+`, "context": 1, "workspace_root": root,
	})
	matches := toMaps(t, out["matches"])
	if len(matches) != 1 {
		t.Fatalf("matches=%#v", matches)
	}
	m := matches[0]
	if asInt(t, m["line"]) != 3 {
		t.Fatalf("line=%v", m["line"])
	}
	before := toStrings(t, m["before"])
	after := toStrings(t, m["after"])
	if len(before) != 1 || before[0] != "" {
		t.Fatalf("before=%#v", before)
	}
	if len(after) != 1 || after[0] != "" {
		t.Fatalf("after=%#v", after)
	}
	if out["truncated"] != false {
		t.Fatalf("nothing was capped: %#v", out)
	}

	// The glob filter narrows which files are searched.
	out = mustCall(t, registry, "grep", map[string]any{
		"pattern": "Target", "glob": "**/*.md", "workspace_root": root,
	})
	if got := asInt(t, out["count"]); got != 1 {
		t.Fatalf("glob-filtered grep count=%d", got)
	}
	if matches := toMaps(t, out["matches"]); !strings.HasSuffix(matches[0]["path"].(string), "b.md") {
		t.Fatalf("glob filter ignored: %#v", matches)
	}

	// An invalid regex is a loud error, never a silent literal search.
	_, _, err := callTool(t, registry, "grep", map[string]any{"pattern": "func(", "workspace_root": root})
	if err == nil || !strings.Contains(err.Error(), "literal:true") {
		t.Fatalf("invalid regex must say how to search literally: %v", err)
	}
	out = mustCall(t, registry, "grep", map[string]any{
		"pattern": "func(", "literal": true, "workspace_root": root,
	})
	if asInt(t, out["count"]) != 0 {
		t.Fatalf("literal search should not match: %#v", out)
	}
}

func TestGrepCapsPerFileAndGlobally(t *testing.T) {
	root := t.TempDir()
	var b strings.Builder
	for i := 0; i < grepMaxPerFile+25; i++ {
		b.WriteString("needle\n")
	}
	writeFileForTest(t, root, "one.txt", b.String())
	writeFileForTest(t, root, "two.txt", b.String())
	registry := fileRegistry(t)

	// Per-file cap: each file stops at grepMaxPerFile, and the file is named.
	out := mustCall(t, registry, "grep", map[string]any{"pattern": "needle", "workspace_root": root})
	if out["truncated"] != true {
		t.Fatalf("per-file cap not reported: %#v", out)
	}
	if got := asInt(t, out["count"]); got != 2*grepMaxPerFile {
		t.Fatalf("count=%d want %d", got, 2*grepMaxPerFile)
	}
	if len(toStrings(t, out["truncated_files"])) != 2 {
		t.Fatalf("truncated_files=%#v", out["truncated_files"])
	}

	// Global cap: distinct from the per-file one.
	out = mustCall(t, registry, "grep", map[string]any{
		"pattern": "needle", "max_results": 10, "workspace_root": root,
	})
	if asInt(t, out["count"]) != 10 || out["truncated"] != true {
		t.Fatalf("global cap: %#v", out)
	}
}

func TestMatchGlobSemantics(t *testing.T) {
	cases := []struct {
		pattern, name string
		want          bool
	}{
		{"**/*.go", "a/b/c.go", true},
		{"**/*.go", "c.go", true},
		{"*.go", "a/c.go", false},
		{"src/**/*.ts", "src/x/y/z.ts", true},
		{"src/**/*.ts", "lib/x.ts", false},
		{"?.txt", "a.txt", true},
		{"[ab].txt", "b.txt", true},
		{"[ab].txt", "c.txt", false},
		{"**", "any/depth/file", true},
	}
	for _, c := range cases {
		if got := matchGlob(c.pattern, c.name); got != c.want {
			t.Fatalf("matchGlob(%q, %q)=%v want %v", c.pattern, c.name, got, c.want)
		}
	}
}

func toStrings(t *testing.T, v any) []string {
	t.Helper()
	raw, ok := v.([]any)
	if !ok {
		if v == nil {
			return nil
		}
		t.Fatalf("not an array: %#v", v)
	}
	out := make([]string, 0, len(raw))
	for _, item := range raw {
		s, ok := item.(string)
		if !ok {
			t.Fatalf("not a string: %#v", item)
		}
		out = append(out, s)
	}
	return out
}

func toMaps(t *testing.T, v any) []map[string]any {
	t.Helper()
	raw, ok := v.([]any)
	if !ok {
		t.Fatalf("not an array: %#v", v)
	}
	out := make([]map[string]any, 0, len(raw))
	for _, item := range raw {
		m, ok := item.(map[string]any)
		if !ok {
			t.Fatalf("not an object: %#v", item)
		}
		out = append(out, m)
	}
	return out
}
