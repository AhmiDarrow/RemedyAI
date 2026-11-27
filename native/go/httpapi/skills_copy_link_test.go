package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCopySkillDirSkipsLinks(t *testing.T) {
	root := t.TempDir()
	secret := filepath.Join(root, "id_rsa")
	if err := os.WriteFile(secret, []byte("private"), 0o600); err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(root, "skill")
	if err := os.MkdirAll(src, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "SKILL.md"), []byte("---\nname: s\n---\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secret, filepath.Join(src, "key")); err != nil {
		t.Skipf("no symlinks here: %v", err)
	}
	dst := filepath.Join(root, "out")
	if err := copySkillDir(src, dst); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dst, "SKILL.md")); err != nil {
		t.Fatalf("SKILL.md not copied: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(dst, "key")); err == nil {
		t.Fatal("followed a link out of the skill folder")
	}
}
