package tools

import (
	"strings"
	"testing"
)

func TestNormalizeInputPathWindowsDriveBackslashAndHome(t *testing.T) {
	home := `C:\Users\Administrator`
	cases := []struct {
		in, want string
	}{
		{`C:\Users\Administrator\Projects\app\src\main.go`, `C:\Users\Administrator\Projects\app\src\main.go`},
		{`C:/Users/Administrator/Projects/app/src/main.go`, `C:\Users\Administrator\Projects\app\src\main.go`},
		{`~\Projects\app\file.go`, `C:\Users\Administrator\Projects\app\file.go`},
		{`~/Projects/app/file.go`, `C:\Users\Administrator\Projects\app\file.go`},
		{`~`, home},
		{`src\main.go`, `src\main.go`},
		{`C:\Users\Administrator\Projects\app\..\other\x.go`, `C:\Users\Administrator\Projects\other\x.go`},
	}
	for _, tc := range cases {
		got := normalizeInputPath(tc.in, home, PathOSWindows)
		if got != tc.want {
			t.Errorf("normalizeInputPath(%q)=%q want %q", tc.in, got, tc.want)
		}
	}
}

func TestIsAbsPathWindowsAndUnix(t *testing.T) {
	if !isAbsPath(`C:\Users\Administrator\Projects`, PathOSWindows) {
		t.Fatal("drive-letter path must be absolute on Windows")
	}
	if !isAbsPath(`C:/Users/Administrator/Projects`, PathOSWindows) {
		t.Fatal("forward-slash drive path must be absolute on Windows")
	}
	if !isAbsPath(`\\server\share\dir`, PathOSWindows) {
		t.Fatal("UNC path must be absolute on Windows")
	}
	if isAbsPath(`src\main.go`, PathOSWindows) {
		t.Fatal("relative Windows path is not absolute")
	}
	if isAbsPath(`C:\Users\Administrator\Projects`, PathOSUnix) {
		t.Fatal("a Windows drive path is not absolute under Unix rules")
	}
	if !isAbsPath(`/home/user/src`, PathOSUnix) {
		t.Fatal("POSIX path must be absolute under Unix rules")
	}
	if isAbsPath(`~/src`, PathOSUnix) {
		t.Fatal("~ is not absolute until it is expanded")
	}
}

func TestResolveBoundPathOSAcceptsWindowsPaths(t *testing.T) {
	root := `C:\Users\Administrator\Projects\app`
	home := `C:\Users\Administrator`
	cases := []struct {
		in, want string
	}{
		{`C:\Users\Administrator\Projects\app\src\main.go`, "src/main.go"},
		{`C:/Users/Administrator/Projects/app/src/main.go`, "src/main.go"},
		{`~\Projects\app\src\main.go`, "src/main.go"},
		{`src\pkg\lib.go`, "src/pkg/lib.go"},
		{`C:\Users\Administrator\Projects\app`, "."},
	}
	for _, tc := range cases {
		got, err := resolveBoundPathOS(root, tc.in, home, PathOSWindows)
		if err != nil {
			t.Fatalf("resolveBoundPathOS(%q): %v", tc.in, err)
		}
		if got != tc.want {
			t.Errorf("resolveBoundPathOS(%q)=%q want %q", tc.in, got, tc.want)
		}
	}

	_, err := resolveBoundPathOS(root, `C:\Users\Administrator\SecretFolder\x.go`, home, PathOSWindows)
	if err == nil || !strings.Contains(err.Error(), "outside the workspace root") {
		t.Fatalf("a path outside the root must be refused clearly, not as a bare schema error: %v", err)
	}
}

func TestWindowsPathToWSLAndRewrite(t *testing.T) {
	if got := windowsPathToWSL(`C:\Users\Administrator\Projects\app`); got != `/mnt/c/Users/Administrator/Projects/app` {
		t.Fatalf("to WSL: %q", got)
	}
	if got := windowsPathToWSL(`D:/work/src`); got != `/mnt/d/work/src` {
		t.Fatalf("to WSL forward slash: %q", got)
	}
	if got := windowsPathToWSL(`/already/posix`); got != `/already/posix` {
		t.Fatalf("posix passthrough: %q", got)
	}

	cmd := `git -C C:\Users\Administrator\Projects\app grep -n -E "foo" src\main.go`
	got := rewriteWindowsPathsForWSL(cmd)
	if !strings.Contains(got, `/mnt/c/Users/Administrator/Projects/app`) {
		t.Fatalf("command rewrite missed the path: %q", got)
	}
	if strings.Contains(got, `C:\`) {
		t.Fatalf("drive-letter path left in place: %q", got)
	}

	quoted := `bash /mnt/c/work/script.sh`
	if rewriteWindowsPathsForWSL(quoted) != quoted {
		t.Fatalf("already-converted path was rewritten: %q", rewriteWindowsPathsForWSL(quoted))
	}
}

func TestExpandHomeUnix(t *testing.T) {
	if got := expandHome("~/src/app", "/home/ahmi", PathOSUnix); got != "/home/ahmi/src/app" {
		t.Fatalf("unix home: %q", got)
	}
	if got := expandHome("~other/x", "/home/ahmi", PathOSUnix); got != "~other/x" {
		t.Fatalf("~other must stay literal: %q", got)
	}
}
