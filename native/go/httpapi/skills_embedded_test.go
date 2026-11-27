package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

// The shipped skills are inside the runtime, so a fresh home on any install
// route gets them without a skills folder beside the program.
func TestEmbeddedSkillsAreWrittenOnceAndFound(t *testing.T) {
	home := t.TempDir()
	root := embeddedSkillsRoot(home)
	if root == "" {
		t.Fatal("the shipped skills were not written out")
	}
	for _, name := range []string{"project-overview", "change-safety", "code-review"} {
		if !fileExists(filepath.Join(root, name, "SKILL.md")) {
			t.Fatalf("%s is not among the shipped skills in %s", name, root)
		}
	}
	entries, err := os.ReadDir(root)
	if err != nil || len(entries) < 40 {
		t.Fatalf("%d entries in %s: %v", len(entries), root, err)
	}
	if again := embeddedSkillsRoot(home); again != root {
		t.Fatalf("a second call moved the folder: %s then %s", root, again)
	}
	// The folder is a seed root: discovery copies the skills into the home.
	found := false
	for _, r := range skillSeedRoots(home) {
		found = found || r == root
	}
	if !found {
		t.Fatalf("the shipped skills are not a seed root: %v", skillSeedRoots(home))
	}
}
