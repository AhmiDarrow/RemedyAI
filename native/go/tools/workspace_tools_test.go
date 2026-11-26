package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func workspaceReg(t *testing.T) *Registry {
	t.Helper()
	reg := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	if err := RegisterWorkspaceTools(reg); err != nil {
		t.Fatal(err)
	}
	return reg
}

func runWS(t *testing.T, reg *Registry, id string, in map[string]any) (map[string]any, error) {
	t.Helper()
	raw, _ := json.Marshal(in)
	res, err := reg.Execute(context.Background(), Request{ToolID: id, Version: 1, Input: raw, CapabilityToken: []byte("t")})
	if err != nil {
		return nil, err
	}
	out := map[string]any{}
	if err := json.Unmarshal(res.Output, &out); err != nil {
		t.Fatal(err)
	}
	return out, nil
}

func mustWS(t *testing.T, reg *Registry, id string, in map[string]any) map[string]any {
	t.Helper()
	out, err := runWS(t, reg, id, in)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	return out
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestWorkspaceToolsAreGoAndKeepTheirIDs(t *testing.T) {
	reg := workspaceReg(t)
	for id := range goWorkspaceToolIDs {
		d, err := reg.Latest(id)
		if err != nil || d.Runtime != RuntimeGo {
			t.Fatalf("%s: %+v err=%v", id, d, err)
		}
		if !IsModelHiddenTool(id) {
			t.Fatalf("%s must stay hidden from the model", id)
		}
	}
	if got := mustWS(t, reg, "text.slugify", map[string]any{"text": "Hello, Remedy AI!"})["slug"]; got != "hello-remedy-ai" {
		t.Fatalf("slug=%v", got)
	}
	if got := mustWS(t, reg, "text.word_count", map[string]any{"text": "one two  three"})["words"]; got != float64(3) {
		t.Fatalf("words=%v", got)
	}
}

func TestWorkspaceReadPagesLikeThePythonTool(t *testing.T) {
	ws := t.TempDir()
	writeFile(t, filepath.Join(ws, "a.txt"), "one\ntwo\r\nthree\nfour\n")
	reg := workspaceReg(t)
	out := mustWS(t, reg, "workspace.read", map[string]any{"path": "a.txt", "offset": 2, "limit": 2, "workspace_root": ws})
	if out["content"] != "two\r\nthree\n" || out["line_start"] != float64(2) || out["line_end"] != float64(3) ||
		out["next_offset"] != float64(4) || out["total_lines"] != float64(4) || out["truncated"] != true || out["path"] != "a.txt" {
		t.Fatalf("read=%v", out)
	}
	out = mustWS(t, reg, "workspace.read", map[string]any{"path": "a.txt", "offset": 4, "line_numbers": true, "workspace_root": ws})
	if out["content"] != "4\tfour\n" {
		t.Fatalf("numbered=%q", out["content"])
	}
	if _, ok := out["next_offset"]; ok {
		t.Fatalf("last page has next_offset: %v", out)
	}
}

func TestWorkspaceRefusesCredentialsSecretsAndEscapes(t *testing.T) {
	ws := t.TempDir()
	writeFile(t, filepath.Join(ws, ".env"), "KEY=1")
	writeFile(t, filepath.Join(ws, "keys", "id_rsa"), "x")
	writeFile(t, filepath.Join(ws, ".env.example"), "KEY=")
	writeFile(t, filepath.Join(ws, "ok.txt"), "fine")
	reg := workspaceReg(t)
	for _, p := range []string{".env", "keys/id_rsa", "../outside.txt", "/etc/hostname"} {
		if _, err := runWS(t, reg, "workspace.read", map[string]any{"path": p, "workspace_root": ws}); err == nil {
			t.Fatalf("read %q was allowed", p)
		}
	}
	if _, err := runWS(t, reg, "workspace.read", map[string]any{"path": ".env.example", "workspace_root": ws}); err != nil {
		t.Fatalf("a template is not a credential: %v", err)
	}
	if _, err := runWS(t, reg, "workspace.write", map[string]any{"path": "server.pem", "content": "x", "workspace_root": ws}); err == nil {
		t.Fatal("wrote a credential-looking file")
	}
	listed := mustWS(t, reg, "workspace.list", map[string]any{"workspace_root": ws})
	for _, e := range listed["entries"].([]any) {
		if name := e.(map[string]any)["name"]; name == ".env" {
			t.Fatalf(".env listed: %v", listed)
		}
	}
	// The Remedy auth folder is refused even when it sits inside the root.
	writeFile(t, filepath.Join(ws, ".remedy", "auth", "notes.txt"), "secret")
	if _, err := runWS(t, reg, "workspace.read", map[string]any{"path": ".remedy/auth/notes.txt", "workspace_root": ws}); err == nil || !strings.Contains(err.Error(), "auth secret") {
		t.Fatal("read under .remedy/auth was allowed")
	}
}

func TestWorkspaceWriteGuards(t *testing.T) {
	ws := t.TempDir()
	reg := workspaceReg(t)
	for _, p := range []string{"nul", "src/CON.txt", "_ref_copy.py", "src/{a,b}/x.py", `a\_patch_fix.py`} {
		if _, err := runWS(t, reg, "workspace.write", map[string]any{"path": p, "content": "x", "workspace_root": ws}); err == nil {
			t.Fatalf("write %q was allowed", p)
		}
	}
	if _, err := runWS(t, reg, "workspace.write", map[string]any{
		"path": "main.py", "content": "[file_write content omitted from provider history]", "workspace_root": ws,
	}); err == nil {
		t.Fatal("wrote a history stub")
	}
	out := mustWS(t, reg, "workspace.write", map[string]any{"path": "out/note.txt", "content": "hello abi", "workspace_root": ws})
	if out["path"] != "out/note.txt" || out["bytes_written"] != float64(9) || out["created"] != true {
		t.Fatalf("write=%v", out)
	}
	out = mustWS(t, reg, "workspace.write", map[string]any{"path": "out/note.txt", "content": "again", "workspace_root": ws})
	if out["created"] != false {
		t.Fatalf("overwrite reported created: %v", out)
	}
}

func TestWorkspaceEditKeepsBOMAndCRLFAndIsAllOrNothing(t *testing.T) {
	ws := t.TempDir()
	target := filepath.Join(ws, "w.txt")
	writeFile(t, target, "\xEF\xBB\xBFalpha\r\nbeta\r\ngamma\r\n")
	reg := workspaceReg(t)
	out := mustWS(t, reg, "workspace.edit", map[string]any{"path": "w.txt", "old_string": "beta\n", "new_string": "BETA\n", "workspace_root": ws})
	if out["changed"] != true || out["occurrences"] != float64(1) {
		t.Fatalf("edit=%v", out)
	}
	got, _ := os.ReadFile(target)
	if string(got) != "\xEF\xBB\xBFalpha\r\nBETA\r\ngamma\r\n" {
		t.Fatalf("file=%q", got)
	}
	// A failing second hunk leaves the file as it was.
	if _, err := runWS(t, reg, "workspace.edit", map[string]any{"path": "w.txt", "workspace_root": ws, "edits": []any{
		map[string]any{"old_string": "alpha", "new_string": "ALPHA"},
		map[string]any{"old_string": "missing", "new_string": "x"},
	}}); err == nil {
		t.Fatal("bad hunk accepted")
	}
	if again, _ := os.ReadFile(target); string(again) != string(got) {
		t.Fatalf("partial edit written: %q", again)
	}
	out = mustWS(t, reg, "workspace.edit", map[string]any{"path": "w.txt", "workspace_root": ws, "edits": []any{
		map[string]any{"old_string": "alpha", "new_string": "ALPHA"},
		map[string]any{"old_string": "gamma", "new_string": "GAMMA"},
	}})
	if out["hunks_applied"] != float64(2) || out["occurrences"] != float64(2) {
		t.Fatalf("multi=%v", out)
	}
	if _, err := runWS(t, reg, "workspace.edit", map[string]any{"path": "w.txt", "old_string": "\r\n", "new_string": "\n", "workspace_root": ws}); err == nil ||
		!strings.Contains(err.Error(), "matches") {
		t.Fatalf("ambiguous edit: %v", err)
	}
}

func TestWorkspaceListSortsDirsFirstAndSkipsBuildTrees(t *testing.T) {
	ws := t.TempDir()
	writeFile(t, filepath.Join(ws, "b.txt"), "x")
	writeFile(t, filepath.Join(ws, "A.txt"), "x")
	writeFile(t, filepath.Join(ws, "src", "m.go"), "x")
	writeFile(t, filepath.Join(ws, "node_modules", "p", "i.js"), "x")
	reg := workspaceReg(t)
	out := mustWS(t, reg, "workspace.list", map[string]any{"path": ".", "workspace_root": ws})
	var names []string
	for _, e := range out["entries"].([]any) {
		m := e.(map[string]any)
		names = append(names, m["name"].(string)+":"+m["kind"].(string))
	}
	if strings.Join(names, ",") != "src:dir,A.txt:file,b.txt:file" || out["total"] != float64(3) {
		t.Fatalf("list=%v", names)
	}
	sub := mustWS(t, reg, "workspace.list", map[string]any{"path": "src", "workspace_root": ws})
	if first := sub["entries"].([]any)[0].(map[string]any)["name"]; first != "src/m.go" {
		t.Fatalf("sub names are root-relative: %v", first)
	}
	page := mustWS(t, reg, "workspace.list", map[string]any{"limit": 1, "offset": 1, "workspace_root": ws})
	if len(page["entries"].([]any)) != 1 || page["truncated"] != true {
		t.Fatalf("page=%v", page)
	}
}

func TestWorkspaceSearchCapsAndIsRelative(t *testing.T) {
	ws := t.TempDir()
	writeFile(t, filepath.Join(ws, "a.go"), "foo\nbar\nfoo\n")
	writeFile(t, filepath.Join(ws, "b.txt"), "FOO\n")
	reg := workspaceReg(t)
	out := mustWS(t, reg, "workspace.search", map[string]any{"pattern": "foo", "workspace_root": ws})
	if out["total"] != float64(2) || out["capped"] != false {
		t.Fatalf("search=%v", out)
	}
	if p := out["matches"].([]any)[0].(map[string]any)["path"]; p != "a.go" {
		t.Fatalf("path not relative: %v", p)
	}
	out = mustWS(t, reg, "workspace.search", map[string]any{"pattern": "foo", "case_insensitive": true, "max_matches": 2, "workspace_root": ws})
	if out["total"] != float64(2) || out["capped"] != true || out["truncated"] != true {
		t.Fatalf("capped=%v", out)
	}
	out = mustWS(t, reg, "workspace.search", map[string]any{"pattern": "bar", "context": 1, "workspace_root": ws})
	if txt := out["matches"].([]any)[0].(map[string]any)["text"]; txt != "foo\nbar\nfoo" {
		t.Fatalf("context=%q", txt)
	}
	if _, err := runWS(t, reg, "workspace.search", map[string]any{"pattern": "(", "workspace_root": ws}); err == nil ||
		!strings.Contains(err.Error(), "invalid regex") {
		t.Fatalf("bad regex: %v", err)
	}
}

func TestWorkspaceRootNeverHomeOrFilesystemRoot(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("REMEDY_HOME", filepath.Join(home, ".remedy"))
	for _, k := range []string{"REMEDY_WORKSPACE", "REMEDY_PROJECT_PATH", "REMEDY_PROJECT", "REMEDY_FILES_ROOT"} {
		t.Setenv(k, "")
	}
	wd, _ := os.Getwd()
	t.Chdir(home) // no pyproject.toml above the home folder
	defer func() { _ = os.Chdir(wd) }()
	for _, bad := range []string{home, string(filepath.Separator)} {
		got := workspaceRootFor(workspaceInput{WorkspaceRoot: bad})
		if want, _ := filepath.EvalSymlinks(filepath.Join(home, "Remedy")); got != want {
			t.Fatalf("root %q -> %q, want the owner folder %q", bad, got, want)
		}
	}
	proj := filepath.Join(home, "proj")
	writeFile(t, filepath.Join(home, ".remedy", "config.toml"), "project_path = \""+strings.ReplaceAll(proj, `\`, `\\`)+"\"\n")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	want, _ := filepath.EvalSymlinks(proj)
	if got := workspaceRootFor(workspaceInput{}); got != want {
		t.Fatalf("config project_path: got %q want %q", got, want)
	}
}
