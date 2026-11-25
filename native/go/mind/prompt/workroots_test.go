package prompt

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The recorded Python work_roots section of the project fixtures.
func TestWorkRootsFixtures(t *testing.T) {
	all := byCase(loadFixtures(t))
	for _, name := range []string{"project_node", "project_rust"} {
		f, ok := all[name]
		if !ok {
			t.Fatalf("fixture %s missing", name)
		}
		ce := setupCase(t, f)
		want, found := "", false
		for _, st := range f.Steps {
			for _, s := range st.Sections {
				if s.Name == "work_roots" {
					want, found = ce.ph.fill(s.Text), true
				}
			}
		}
		if !found {
			t.Fatalf("%s: no work_roots section", name)
		}
		if got := workRootsBlock(ce.dirs["{PROJECT}"]); got != want {
			t.Errorf("%s:\n--- python\n%q\n--- go\n%q", name, want, got)
		}
	}
}

func TestWorkRootsTable(t *testing.T) {
	tests := []struct {
		name  string
		files map[string]string
		want  func(root string) string // "" means unrecognised
	}{
		{"python", map[string]string{"pyproject.toml": "[project]\n"}, func(r string) string {
			return "## Active work tree\n" + r + "\nStack fingerprint (" + r + "):\n  stacks: python\n  suggested verify: pytest -q\n  - Python: uv run pytest / pytest -q when tests exist"
		}},
		{"go", map[string]string{"go.mod": "module x\n", "AGENTS.md": "Be   kind.\n"}, func(r string) string {
			return "## Active work tree\n" + r + "\nOrientation (read these first if relevant):\n- AGENTS.md: Be kind.\n" +
				"Stack fingerprint (" + r + "):\n  stacks: go\n  suggested verify: go test ./...\n  - Go: go test ./…"
		}},
		{"empty", nil, func(string) string { return "" }},
		{"godot only", map[string]string{"project.godot": "config_version=5\n"}, func(string) string { return "" }},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := resolvePath(t.TempDir(), "")
			for n, body := range tc.files {
				if err := os.WriteFile(filepath.Join(root, n), []byte(body), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			if got, want := workRootsBlock(root), tc.want(root); got != want {
				t.Errorf("got %q want %q", got, want)
			}
		})
	}
	if got := workRootsBlock(filepath.Join(t.TempDir(), "missing")); got != "" {
		t.Errorf("missing folder: %q", got)
	}
	if got := workRootsBlock(""); got != "" {
		t.Errorf("no folder: %q", got)
	}
}

func TestWorkRootsCap(t *testing.T) {
	root := resolvePath(t.TempDir(), "")
	if err := os.WriteFile(filepath.Join(root, "README.md"), []byte(strings.Repeat("word ", 100)), 0o644); err != nil {
		t.Fatal(err)
	}
	got := workRootsBlock(root)
	if !strings.Contains(got, "…\n") && !strings.HasSuffix(got, "…") {
		t.Errorf("long excerpt not cut: %q", got)
	}
}
