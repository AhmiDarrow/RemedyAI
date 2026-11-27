package tools

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPlatformShellArgvForUsesPowerShellOnWindows(t *testing.T) {
	getenv := func(string) string { return "" }
	look := func(name string) (string, error) {
		switch name {
		case "pwsh", "pwsh.exe":
			return `/fake/pwsh.exe`, nil
		default:
			return "", errLookPathNotFound(name)
		}
	}
	command := `gh release view v0.1.5 --json assets -q ".assets[].name"`
	argv, kind, err := platformShellArgvFor("windows", command, getenv, look)
	if err != nil {
		t.Fatal(err)
	}
	if kind != hostShellPwsh {
		t.Fatalf("kind=%s want pwsh", kind)
	}
	if !strings.HasSuffix(argv[0], "pwsh.exe") {
		t.Fatalf("argv[0]=%q", argv[0])
	}
	if !containsAll(argv, "-NoProfile", "-Command") {
		t.Fatalf("PowerShell flags missing: %v", argv)
	}
	if argv[len(argv)-1] != command {
		t.Fatalf("the command must be the last argv element, not interpolated: %v", argv)
	}
}

func TestPlatformShellArgvForFallsBackToWindowsPowerShell(t *testing.T) {
	look := func(name string) (string, error) {
		switch name {
		case "powershell", "powershell.exe":
			return `/fake/powershell.exe`, nil
		default:
			return "", errLookPathNotFound(name)
		}
	}
	argv, kind, err := platformShellArgvFor("windows", "echo hi", func(string) string { return "" }, look)
	if err != nil {
		t.Fatal(err)
	}
	if kind != hostShellPowerShell {
		t.Fatalf("kind=%s", kind)
	}
	if !strings.HasSuffix(strings.ToLower(argv[0]), "powershell.exe") {
		t.Fatalf("argv[0]=%q", argv[0])
	}
}

func TestPlatformShellArgvForGitBashWhenConfigured(t *testing.T) {
	getenv := func(k string) string {
		if k == RemedyShellEnv {
			return "bash"
		}
		return ""
	}
	look := func(name string) (string, error) {
		if name == "bash.exe" || name == "bash" {
			return `/fake/bash.exe`, nil
		}
		return "", errLookPathNotFound(name)
	}
	argv, kind, err := platformShellArgvFor("windows", "echo hi && ls", getenv, look)
	if err != nil {
		t.Fatal(err)
	}
	if kind != hostShellBash {
		t.Fatalf("kind=%s want bash", kind)
	}
	if argv[len(argv)-2] != "-c" || argv[len(argv)-1] != "echo hi && ls" {
		t.Fatalf("git bash argv=%v", argv)
	}
}

func TestWSLArgvConvertsCwdAndKeepsCommandIntact(t *testing.T) {
	look := func(name string) (string, error) {
		if name == "wsl.exe" || name == "wsl" {
			return `/fake/wsl.exe`, nil
		}
		return "", errLookPathNotFound(name)
	}
	command := "uname -r && ls /mnt/c/work"
	argv, err := wslArgvFor(command, "/mnt/c/work", look)
	if err != nil {
		t.Fatal(err)
	}
	if !containsAll(argv, "--cd", "/mnt/c/work", "-e", "bash", "-lc", command) {
		t.Fatalf("wsl argv=%v", argv)
	}
}

func TestInterceptWindowsFileIdiom(t *testing.T) {
	cases := []struct {
		cmd  string
		want string // substring of the refusal, empty if allowed
	}{
		{`findstr /n /c:"name = \"tauri-plugin-updater\"" Cargo.lock`, "grep"},
		{`FINDSTR /S /I pattern *.go`, "grep"},
		{`dir /s /b C:\Users\Administrator\Projects\RemedyUI\target\release`, "glob"},
		{`dir /S C:\work`, "glob"},
		{`dir -Recurse C:\work`, "glob"},
		{`type C:\Users\Administrator\SecretFolder\.tmp-release\pkg-win.log`, "read"},
		{`type "C:\My Files\log.txt"`, "read"},
		{`cmd /c dir /s /b C:\work`, "glob"},
		{`cmd /d /s /c findstr /c:"foo" bar.txt`, "grep"},
		{`dir C:\work`, ""}, // listing one directory is allowed
		{`echo hello`, ""},
		{`git grep -E "foo"`, ""},
		{`type`, ""}, // no file argument
	}
	for _, tc := range cases {
		got := interceptWindowsFileIdiom(tc.cmd)
		if tc.want == "" {
			if got != "" {
				t.Errorf("%q should run, got refusal %q", tc.cmd, got)
			}
			continue
		}
		if !strings.Contains(strings.ToLower(got), tc.want) {
			t.Errorf("%q: refusal %q does not point at %s", tc.cmd, got, tc.want)
		}
	}
}

func TestFormatFailedCommandPreviewIncludesExitAndTails(t *testing.T) {
	if msg := FormatFailedCommandPreview(nil); !strings.Contains(msg, "no stdout or stderr") {
		t.Fatalf("empty: %q", msg)
	}
	if strings.Contains(FormatFailedCommandPreview(nil), "tool failed") {
		t.Fatal("must never return the bare string tool failed")
	}

	var stdout, stderr strings.Builder
	for i := 1; i <= 50; i++ {
		stdout.WriteString("out line ")
		stdout.WriteByte('0' + byte((i/10)%10))
		stdout.WriteByte('0' + byte(i%10))
		stdout.WriteByte('\n')
		stderr.WriteString("err line ")
		stderr.WriteByte('0' + byte((i/10)%10))
		stderr.WriteByte('0' + byte(i%10))
		stderr.WriteByte('\n')
	}
	code := 1
	raw, err := json.Marshal(map[string]any{
		"exit_code": code,
		"timed_out": false,
		"stdout":    stdout.String(),
		"stderr":    stderr.String(),
	})
	if err != nil {
		t.Fatal(err)
	}
	msg := FormatFailedCommandPreview(raw)
	if !strings.Contains(msg, "exit_code 1") {
		t.Fatalf("missing exit code: %q", msg)
	}
	if !strings.Contains(msg, "err line 50") || !strings.Contains(msg, "out line 50") {
		t.Fatalf("missing tails: %q", msg)
	}
	if strings.Contains(msg, "err line 01") || strings.Contains(msg, "out line 01") {
		t.Fatalf("head of a long stream must be dropped: %q", msg)
	}
	if strings.Contains(msg, "tool failed") {
		t.Fatalf("bare tool failed leaked: %q", msg)
	}

	timed, _ := json.Marshal(map[string]any{"exit_code": 1, "timed_out": true, "stdout": "", "stderr": "still running"})
	if msg := FormatFailedCommandPreview(timed); !strings.Contains(msg, "timed out") || !strings.Contains(msg, "still running") {
		t.Fatalf("timeout: %q", msg)
	}
}

func TestSessionScratchDirLivesUnderTheSession(t *testing.T) {
	home := t.TempDir()
	dir, err := sessionScratchDir(home, "sess-1")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(filepath.ToSlash(dir), "/sessions/sess-1/tmp") {
		t.Fatalf("scratch=%s", dir)
	}
	got := ensureScratchDir(home, "sess-1")
	if got != dir {
		t.Fatalf("ensure=%s want %s", got, dir)
	}
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		t.Fatalf("scratch dir was not created: %v", err)
	}

	spec, scratch := withScratchEnv(shellSpawnSpec{env: map[string]string{"FOO": "1"}}, home, "sess-1")
	if scratch != dir || spec.env[RemedyScratchEnv] != dir || spec.env["FOO"] != "1" {
		t.Fatalf("env=%v scratch=%s", spec.env, scratch)
	}
	if len(spec.writeRoots) != 0 {
		t.Fatalf("an unbound jail stays unbound: %v", spec.writeRoots)
	}
	bound, _ := withScratchEnv(shellSpawnSpec{writeRoots: []string{"/proj"}}, home, "sess-1")
	if len(bound.writeRoots) != 2 || bound.writeRoots[0] != "/proj" || bound.writeRoots[1] != dir {
		t.Fatalf("a bound jail lets the scratch folder be written: %v", bound.writeRoots)
	}
}

func containsAll(argv []string, want ...string) bool {
	have := map[string]bool{}
	for _, a := range argv {
		have[a] = true
	}
	for _, w := range want {
		if !have[w] {
			return false
		}
	}
	return true
}

// shellLook finds the named programs and nothing else.
func shellLook(found map[string]string) lookPathFunc {
	return func(name string) (string, error) {
		if p, ok := found[name]; ok {
			return p, nil
		}
		return "", errLookPathNotFound(name)
	}
}

func TestWindowsDefaultShellIsGitBashWhenInstalled(t *testing.T) {
	none := func(string) string { return "" }
	both := shellLook(map[string]string{"bash.exe": `/git/bin/bash.exe`, "pwsh": `/fake/pwsh.exe`})
	argv, kind, err := platformShellArgvFor("windows", "ls -la", none, both)
	if err != nil || kind != hostShellBash || argv[len(argv)-2] != "-c" {
		t.Fatalf("with Git Bash installed: kind=%s argv=%v err=%v", kind, argv, err)
	}
	// Windows' own bash.exe starts WSL; it is not Git Bash.
	wsl := shellLook(map[string]string{"bash.exe": `C:/Windows/System32/bash.exe`, "pwsh": `/fake/pwsh.exe`})
	if _, kind, err := platformShellArgvFor("windows", "ls", none, wsl); err != nil || kind != hostShellPwsh {
		t.Fatalf("the WSL launcher must not be taken for Git Bash: kind=%s err=%v", kind, err)
	}
	// The owner's word, from the setting or the environment, beats the default.
	prev := ShellPreference
	t.Cleanup(func() { ShellPreference = prev })
	ShellPreference = func() string { return "powershell" }
	if _, kind, _ := platformShellArgvFor("windows", "Get-Item .", none, both); kind != hostShellPwsh {
		t.Fatalf("shell = powershell: kind=%s", kind)
	}
	env := func(k string) string {
		if k == RemedyShellEnv {
			return "bash"
		}
		return ""
	}
	if _, kind, _ := platformShellArgvFor("windows", "ls", env, both); kind != hostShellBash {
		t.Fatalf("REMEDY_SHELL comes before the setting: kind=%s", kind)
	}
}
