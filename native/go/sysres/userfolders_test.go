package sysres

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A home that is not the signed-in user's has no system record: the folders
// under it are found by their default names, and only when they exist.
func TestUserFoldersOfAnotherHome(t *testing.T) {
	home := t.TempDir()
	if got := UserFolders(home); len(got) != 0 {
		t.Fatalf("no folders exist, got %v", got)
	}
	for _, name := range []string{"Desktop", "Downloads"} {
		if err := os.Mkdir(filepath.Join(home, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	got := UserFolders(home)
	if len(got) != 2 || filepath.Base(got[0]) != "Desktop" || filepath.Base(got[1]) != "Downloads" {
		t.Fatalf("got %v", got)
	}
}

// The signed-in user's folders come from the system and are real folders.
func TestUserFoldersOfOwnHomeExist(t *testing.T) {
	for _, p := range UserFolders("") {
		if st, err := os.Stat(p); err != nil || !st.IsDir() || !filepath.IsAbs(p) {
			t.Fatalf("%q is not a folder", p)
		}
	}
}

func TestOwnHomeIsRecognised(t *testing.T) {
	own, err := os.UserHomeDir()
	if err != nil {
		t.Skip("no user home")
	}
	if !isOwnHome(strings.ToUpper(own[:1])+own[1:]) || isOwnHome(t.TempDir()) {
		t.Fatal("own home check is wrong")
	}
}
