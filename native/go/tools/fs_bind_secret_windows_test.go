//go:build windows

package tools

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// shortPath returns the 8.3 spelling of p, or "" when the volume keeps none.
func shortPath(t *testing.T, p string) string {
	t.Helper()
	from, err := syscall.UTF16PtrFromString(p)
	if err != nil {
		t.Fatal(err)
	}
	buf := make([]uint16, 1024)
	n, err := syscall.GetShortPathName(from, &buf[0], uint32(len(buf)))
	if err != nil || n == 0 || int(n) > len(buf) {
		return ""
	}
	short := syscall.UTF16ToString(buf[:n])
	if strings.EqualFold(short, p) {
		return ""
	}
	return short
}

func TestSecretsStayClosedThroughShortNamesAndJunctions(t *testing.T) {
	base := t.TempDir()
	home := filepath.Join(base, "a-long-custom-home") // short name is not REMEDY~1
	auth := filepath.Join(home, "auth")
	if err := os.MkdirAll(auth, 0o700); err != nil {
		t.Fatal(err)
	}
	token := filepath.Join(auth, "local_api_token")
	if err := os.WriteFile(token, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_HOME", home)

	full, err := openBoundFS(t.TempDir(), "full")
	if err != nil {
		t.Fatal(err)
	}
	defer full.Close()

	if short := shortPath(t, home); short == "" {
		t.Log("no 8.3 names on this volume; short-name case not run")
	} else {
		for _, p := range []string{
			filepath.Join(short, "auth", "local_api_token"),
			filepath.Join(short, "auth", "new_file"),
		} {
			if !isSecretAuthPath(p) {
				t.Errorf("%s reaches the secrets folder", p)
			}
			if _, err := full.locate(p); err == nil {
				t.Errorf("full scope opened %s", p)
			}
		}
	}

	// A junction needs no privilege, unlike a symlink.
	link := filepath.Join(base, "jump")
	if out, err := exec.Command("cmd", "/c", "mklink", "/J", link, auth).CombinedOutput(); err != nil {
		t.Skipf("junctions unavailable: %v: %s", err, out)
	}
	// A shell command that names the file through the junction is refused too.
	for _, argv := range [][]string{
		{"findstr", ".", `jump\local_api_token`},
		{"pwsh.exe", "-Command", `Get-Content "jump\local_api_token"`},
		{"cmd.exe", "/c", "type " + filepath.Join(link, "local_api_token")},
	} {
		if !spawnNamesSecret(argv, base) {
			t.Errorf("%q reaches the secrets folder", argv)
		}
	}
	// Run from the home folder, the secret needs no home in its spelling, and
	// a file that is not there yet is judged by where it would land.
	for _, argv := range [][]string{
		{"findstr", ".", `auth\local_api_token`},
		{"cmd.exe", "/c", "copy notes.txt auth/new_file"},
	} {
		if !spawnNamesSecret(argv, home) {
			t.Errorf("%q from the home folder reaches the secrets folder", argv)
		}
	}
	if spawnNamesSecret([]string{"findstr", ".", "notes.txt"}, base) || spawnNamesSecret([]string{"git", "status"}, base) ||
		spawnNamesSecret([]string{"git", "log", "--author=x"}, home) {
		t.Error("ordinary commands still run")
	}
	for _, p := range []string{
		filepath.Join(link, "local_api_token"),
		filepath.Join(link, "new_file"),
	} {
		if !isSecretAuthPath(p) {
			t.Errorf("%s reaches the secrets folder", p)
		}
		if _, err := full.locate(p); err == nil {
			t.Errorf("full scope opened %s", p)
		}
	}
}
