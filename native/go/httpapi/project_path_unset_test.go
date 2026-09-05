package httpapi

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsUnsetProjectPathTreatsUserHomeAsUnset(t *testing.T) {
	uh, err := os.UserHomeDir()
	if err != nil || uh == "" {
		t.Skip("no home")
	}
	if !isUnsetProjectPath(uh) {
		t.Fatalf("user home %q should be unset", uh)
	}
	if !isUnsetProjectPath(uh + string(filepath.Separator)) {
		t.Fatalf("user home with sep should be unset")
	}
	proj := filepath.Join(uh, "Documents", "Old-Remedy")
	if isUnsetProjectPath(proj) {
		t.Fatalf("real project %q must not be unset", proj)
	}
	if isUnsetProjectPath("") != true {
		t.Fatal("empty should be unset")
	}
}

func TestEffectiveTurnProjectPathDropsHome(t *testing.T) {
	uh, err := os.UserHomeDir()
	if err != nil || uh == "" {
		t.Skip("no home")
	}
	if got := effectiveTurnProjectPath(uh); got != "" {
		t.Fatalf("home → %q want empty", got)
	}
	proj := filepath.Join(uh, "code", "app")
	if got := effectiveTurnProjectPath(proj); got != filepath.Clean(proj) {
		t.Fatalf("proj → %q want %q", got, filepath.Clean(proj))
	}
}

func TestIsJunkListingName(t *testing.T) {
	if !isJunkListingName("C\uf03a") {
		t.Fatal("private-use junk should be filtered")
	}
	if isJunkListingName("src") {
		t.Fatal("normal name must pass")
	}
}

func TestNormalizeProjectPathClearsHome(t *testing.T) {
	uh, err := os.UserHomeDir()
	if err != nil || uh == "" {
		t.Skip("no home")
	}
	home := uh
	if got := normalizeProjectPath(&home); got != nil {
		t.Fatalf("home normalize → %v want nil", *got)
	}
	proj := filepath.Join(uh, "code", "app")
	got := normalizeProjectPath(&proj)
	if got == nil || *got != filepath.Clean(proj) {
		t.Fatalf("proj normalize → %v", got)
	}
}
