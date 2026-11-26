package tools

import (
	"context"
	"io/fs"
	"os"
	"path/filepath"
	"testing"
)

func TestSecretAuthPathInEverySpellingThatOpensIt(t *testing.T) {
	for _, p := range []string{
		"C:/Users/me/.remedy/auth/local_api_token",
		"C:/Users/me/.REMEDY/Auth",
		"C:/Users/me/REMEDY~1/auth/x",
		"C:/Users/me/.remedy/auth./x",
		"C:/Users/me/.remedy/auth::$INDEX_ALLOCATION",
		"C:/Users/me/.remedy./auth /x",
	} {
		if !isSecretAuthPath(filepath.FromSlash(p)) {
			t.Errorf("%s reaches the secrets folder", p)
		}
	}
	for _, p := range []string{"C:/Users/me/.remedy/memory.db", "C:/work/auth/login.go", "C:/work/remedy/auth.go"} {
		if isSecretAuthPath(filepath.FromSlash(p)) {
			t.Errorf("%s is not the secrets folder", p)
		}
	}
}

func TestSecretsStayClosedWhenTheBoundFolderContainsThem(t *testing.T) {
	home := t.TempDir()
	auth := filepath.Join(home, ".remedy", "auth")
	if err := os.MkdirAll(auth, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(auth, "local_api_token"), []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "notes.txt"), []byte("hi"), 0o600); err != nil {
		t.Fatal(err)
	}
	// The project is the home folder, so the secrets are inside the jail.
	b, err := openBoundFS(home, "project")
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if _, err := b.locate(".remedy/auth/local_api_token"); err == nil {
		t.Fatal("a path inside the bound folder must still not open a secret")
	}
	if _, err := b.locate("notes.txt"); err != nil {
		t.Fatalf("ordinary files still open: %v", err)
	}
	var seen []string
	if err := walkBound(context.Background(), b.root, ".", "", func(p string, _ fs.DirEntry) error {
		seen = append(seen, p)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	for _, p := range seen {
		if filepath.Base(p) == "local_api_token" {
			t.Fatalf("a search walked into the secrets folder: %v", seen)
		}
	}
	seen = nil
	if err := walkHost(context.Background(), home, "", func(rel, _ string, _ fs.DirEntry) error {
		seen = append(seen, rel)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	for _, p := range seen {
		if filepath.Base(p) == "local_api_token" {
			t.Fatalf("a full-scope search walked into the secrets folder: %v", seen)
		}
	}
}

func TestSecretsStayClosedThroughSymlinksAndACustomHome(t *testing.T) {
	base := t.TempDir()
	home := filepath.Join(base, "custom-home") // REMEDY_HOME need not be .remedy
	auth := filepath.Join(home, "auth")
	if err := os.MkdirAll(auth, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(auth, "local_api_token"), []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_HOME", home)
	proj := filepath.Join(base, "proj")
	if err := os.MkdirAll(proj, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(auth, filepath.Join(proj, "link")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.WriteFile(filepath.Join(proj, "notes.txt"), []byte("hi"), 0o600); err != nil {
		t.Fatal(err)
	}

	b, err := openBoundFS(base, "project")
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	for _, p := range []string{
		"custom-home/auth/local_api_token",
		"proj/link/local_api_token",
		"proj/link/new_file",
	} {
		if _, err := b.locate(p); err == nil {
			t.Errorf("%s opened a secret", p)
		}
	}
	if _, err := b.locate("proj/notes.txt"); err != nil {
		t.Fatalf("ordinary files still open: %v", err)
	}

	full, err := openBoundFS(t.TempDir(), "full")
	if err != nil {
		t.Fatal(err)
	}
	defer full.Close()
	for _, p := range []string{
		filepath.Join(auth, "local_api_token"),
		filepath.Join(proj, "link", "local_api_token"),
	} {
		if _, err := full.locate(p); err == nil {
			t.Errorf("full scope opened %s", p)
		}
	}
	if _, err := full.locate(filepath.Join(proj, "notes.txt")); err != nil {
		t.Fatalf("full scope ordinary file: %v", err)
	}
}
