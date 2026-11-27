package tools

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"unicode"
)

// Host shell names the model is told about, and that platformShellArgvFor
// actually launches. On Windows the default is Git Bash when it is installed,
// then pwsh, then Windows PowerShell; the shell setting or REMEDY_SHELL names
// one outright.
const (
	hostShellPwsh       = "pwsh"
	hostShellPowerShell = "powershell"
	hostShellBash       = "bash"
	hostShellCmd        = "cmd"
	hostShellWSL        = "wsl"

	// RemedyShellEnv is the opt-in override for the Windows host shell:
	// "bash" / "git-bash" uses Git Bash, "cmd" uses cmd.exe, anything else
	// (or unset) uses PowerShell.
	RemedyShellEnv = "REMEDY_SHELL"
	// RemedyScratchEnv is set on every bash child so temporary scripts can
	// land in one session directory instead of the project root.
	RemedyScratchEnv = "REMEDY_SCRATCH"
)

// ShellPreference returns the owner's shell setting ("auto", "bash",
// "powershell", "cmd" or ""). The runtime sets it; the REMEDY_SHELL
// environment variable still comes first.
var ShellPreference func() string

// lookPathFunc and getenvFunc let tests inject PATH and env without
// depending on the builder's operating system.
type lookPathFunc func(string) (string, error)
type getenvFunc func(string) string

// hostShellSpec is the resolved wrapper used to run a command string.
type hostShellSpec struct {
	Kind  string
	Path  string
	Flags []string
}

// describeHostShell is the short phrase the bash tool description uses so
// the model knows which quoting rules apply.
func describeHostShell() string {
	spec, err := resolveHostShellFor(runtime.GOOS, os.Getenv, lookPath)
	if err != nil {
		if runtime.GOOS == "windows" {
			return "the host shell (PowerShell when available)"
		}
		return "bash"
	}
	switch spec.Kind {
	case hostShellPwsh:
		return "pwsh -NoProfile -Command; the command is one argv element, not interpolated through cmd.exe"
	case hostShellPowerShell:
		return "Windows PowerShell -NoProfile -Command; the command is one argv element, not interpolated through cmd.exe"
	case hostShellBash:
		if runtime.GOOS == "windows" {
			return "Git Bash (bash -c): POSIX commands, and paths written /c/Users/name or \"C:/Users/name\""
		}
		return "bash -lc"
	case hostShellCmd:
		return "cmd.exe /d /s /c (REMEDY_SHELL=cmd)"
	default:
		return spec.Kind
	}
}

func bashToolDescription() string {
	return "Run a shell command string in " + describeHostShell() + " and capture its output. " +
		"Foreground commands return exit_code; a failed command includes that exit code and the last 40 lines of stderr and stdout, never a bare failure. " +
		"Set background:true to return a job_id for a long-running command, then follow it with the jobs tool (list includes a short log tail). " +
		"On Windows, do not use findstr, dir /s, or type to search or read files — use grep, glob, or read; those tools accept drive-letter paths, backslashes and ~. " +
		"Set wsl:true to run the command in WSL bash; Windows paths (C:\\...) in the command and cwd are converted to /mnt/c/.... " +
		"Temporary scripts belong in the session scratch directory (scratch_dir in the result, also $REMEDY_SCRATCH), not the project root. " +
		"On Windows, pass shell:\"powershell\" for a Windows-native command (cmdlets, the registry, a .ps1 script) or shell:\"bash\" for Git Bash; without it the shell named above runs. " +
		"Cancellation and timeout kill the whole process tree."
}

func jobsToolDescription() string {
	return "List, tail, read or kill the background commands started by bash with background:true. " +
		"list returns a short log tail for each job so you can see progress without a separate tail call; tail reads more of one log; " +
		"read pages a full job log with offset/limit; kill terminates the whole process tree."
}

// resolveHostShellFor picks the wrapper binary and its flags. look and getenv
// are injected so Windows PowerShell selection can be tested on Linux.
func resolveHostShellFor(goos string, getenv getenvFunc, look lookPathFunc) (hostShellSpec, error) {
	if getenv == nil {
		getenv = os.Getenv
	}
	if look == nil {
		look = lookPath
	}
	if goos != "windows" {
		return resolvePosixShell(look)
	}
	pref := strings.ToLower(strings.TrimSpace(getenv(RemedyShellEnv)))
	if pref == "" && ShellPreference != nil {
		pref = strings.ToLower(strings.TrimSpace(ShellPreference()))
	}
	// Not a login shell: Git Bash sets its PATH either way, and a login
	// shell costs about 400 ms a command against 80.
	gitBash := func(path string) hostShellSpec {
		return hostShellSpec{Kind: hostShellBash, Path: path, Flags: []string{"-c"}}
	}
	switch pref {
	case "bash", "git-bash", "gitbash", "git_bash":
		path, err := lookGitBash(look, getenv)
		if err != nil {
			return hostShellSpec{}, fmt.Errorf("%w: the shell is set to %s but Git Bash was not found: %v", ErrInvalidInput, pref, err)
		}
		return gitBash(path), nil
	case "powershell", "pwsh":
		// Fall through to the PowerShell chain below.
	case "", "auto":
		// The tool is called bash and models write bash for it. Run bash when
		// the machine has one; PowerShell stays one argument away.
		if path, err := lookGitBash(look, getenv); err == nil {
			return gitBash(path), nil
		}
	case "cmd", "cmd.exe":
		path, err := lookCmd(look, getenv)
		if err != nil {
			return hostShellSpec{}, err
		}
		return hostShellSpec{Kind: hostShellCmd, Path: path, Flags: []string{"/d", "/s", "/c"}}, nil
	}
	if path, err := firstFound(look, "pwsh", "pwsh.exe"); err == nil {
		return hostShellSpec{Kind: hostShellPwsh, Path: path, Flags: []string{
			"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
		}}, nil
	}
	if path, err := firstFound(look, "powershell", "powershell.exe"); err == nil {
		return hostShellSpec{Kind: hostShellPowerShell, Path: path, Flags: []string{
			"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
		}}, nil
	}
	// Last resort: cmd.exe, and the description will say so. A host with
	// neither PowerShell is unusual, but the tool must still be able to run.
	path, err := lookCmd(look, getenv)
	if err != nil {
		return hostShellSpec{}, fmt.Errorf("%w: no command shell on this host (pwsh, powershell, or cmd): %v", ErrInvalidInput, err)
	}
	return hostShellSpec{Kind: hostShellCmd, Path: path, Flags: []string{"/d", "/s", "/c"}}, nil
}

func resolvePosixShell(look lookPathFunc) (hostShellSpec, error) {
	if path, err := look("bash"); err == nil {
		return hostShellSpec{Kind: hostShellBash, Path: path, Flags: []string{"-lc"}}, nil
	}
	path, err := look("sh")
	if err != nil {
		return hostShellSpec{}, fmt.Errorf("%w: neither bash nor sh is on PATH: %v", ErrInvalidInput, err)
	}
	return hostShellSpec{Kind: hostShellBash, Path: path, Flags: []string{"-c"}}, nil
}

func firstFound(look lookPathFunc, names ...string) (string, error) {
	var last error
	for _, name := range names {
		path, err := look(name)
		if err == nil {
			return path, nil
		}
		last = err
	}
	if last == nil {
		last = errLookPathNotFound(names[0])
	}
	return "", last
}

func lookCmd(look lookPathFunc, getenv getenvFunc) (string, error) {
	root := strings.TrimSpace(getenv("SystemRoot"))
	if root != "" {
		candidate := filepath.Join(root, "System32", "cmd.exe")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
		// Tests on Unix inject a lookPath and may not have the file.
	}
	return firstFound(look, "cmd.exe", "cmd")
}

func lookGitBash(look lookPathFunc, getenv getenvFunc) (string, error) {
	// A bash on PATH counts unless it is Windows' own bash.exe, which starts
	// WSL (a different machine as far as paths and tools go).
	if path, err := firstFound(look, "bash.exe", "bash"); err == nil {
		low := strings.ToLower(filepath.ToSlash(path))
		if !strings.Contains(low, "/windows/system32/") && !strings.Contains(low, "/windowsapps/") {
			return path, nil
		}
	}
	var bases []string
	for _, key := range []string{"ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"} {
		if v := strings.TrimSpace(getenv(key)); v != "" {
			if key == "LOCALAPPDATA" {
				bases = append(bases, filepath.Join(v, "Programs"))
			} else {
				bases = append(bases, v)
			}
		}
	}
	for _, base := range bases {
		for _, rel := range []string{
			filepath.Join("Git", "bin", "bash.exe"),
			filepath.Join("Git", "usr", "bin", "bash.exe"),
		} {
			candidate := filepath.Join(base, rel)
			if _, err := os.Stat(candidate); err == nil {
				return candidate, nil
			}
		}
	}
	return "", errLookPathNotFound("bash.exe")
}

func absShellPath(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", fmt.Errorf("%w: shell path %q is unusable: %v", ErrInvalidInput, path, err)
	}
	return abs, nil
}

func (s hostShellSpec) argv(command string) ([]string, error) {
	abs, err := absShellPath(s.Path)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, 2+len(s.Flags))
	out = append(out, abs)
	out = append(out, s.Flags...)
	out = append(out, command)
	return out, nil
}

// platformShellArgv wraps a command string in the host shell. The wrapper is
// the argv the classifier inspects: policy.isDangerousProcess recognises cmd,
// bash, sh and pwsh as wrappers and scans the payload that follows.
func platformShellArgv(command string) ([]string, error) {
	argv, _, err := platformShellArgvFor(runtime.GOOS, command, os.Getenv, lookPath)
	return argv, err
}

func platformShellArgvFor(goos, command string, getenv getenvFunc, look lookPathFunc) ([]string, string, error) {
	spec, err := resolveHostShellFor(goos, getenv, look)
	if err != nil {
		return nil, "", err
	}
	argv, err := spec.argv(command)
	if err != nil {
		return nil, "", err
	}
	return argv, spec.Kind, nil
}

// wslArgv builds `wsl.exe -e bash -lc <command>` with optional --cd. Paths
// in command and cwd must already have been rewritten to /mnt/<drive>.
func wslArgvFor(command, cwd string, look lookPathFunc) ([]string, error) {
	path, err := firstFound(look, "wsl.exe", "wsl")
	if err != nil {
		return nil, fmt.Errorf("%w: wsl.exe was not found on PATH, so wsl:true cannot run: %v", ErrInvalidInput, err)
	}
	abs, err := absShellPath(path)
	if err != nil {
		return nil, err
	}
	argv := []string{abs}
	if cwd != "" {
		argv = append(argv, "--cd", cwd)
	}
	argv = append(argv, "-e", "bash", "-lc", command)
	return argv, nil
}

// interceptWindowsFileIdiom returns a refusal message when command is a
// file-search or file-read idiom the model should send to grep, glob or
// read instead of the shell. An empty string means the command may run.
func interceptWindowsFileIdiom(command string) string {
	head := commandHead(unwrapShellWrapper(command))
	if head == "" {
		return ""
	}
	verb, rest := firstToken(head)
	switch strings.ToLower(verb) {
	case "findstr", "findstr.exe":
		return "This command is a file search (findstr). Use the grep tool instead of findstr. Grep accepts Windows drive-letter paths, backslashes and ~, and returns the matching lines with context."
	case "dir", "dir.exe":
		if hasDirRecursiveFlag(rest) {
			return "This command is a recursive file search (dir /s). Use the glob tool instead of dir /s. Glob accepts Windows drive-letter paths, backslashes and ~."
		}
		return ""
	case "type", "type.exe":
		if firstArg(rest) != "" {
			return "This command is a file read (type). Use the read tool instead of type. Read accepts Windows drive-letter paths, backslashes and ~."
		}
		return ""
	default:
		return ""
	}
}

// unwrapShellWrapper strips one outer cmd / powershell / pwsh invocation so
// `cmd /c dir /s /b foo` is still intercepted.
func unwrapShellWrapper(command string) string {
	s := strings.TrimSpace(command)
	if s == "" {
		return s
	}
	lower := strings.ToLower(s)
	prefixes := []string{
		"cmd.exe /d /s /c ",
		"cmd.exe /s /c ",
		"cmd.exe /c ",
		"cmd /d /s /c ",
		"cmd /s /c ",
		"cmd /c ",
		"powershell.exe -noprofile -noninteractive -executionpolicy bypass -command ",
		"powershell.exe -noprofile -command ",
		"powershell.exe -command ",
		"powershell -noprofile -noninteractive -executionpolicy bypass -command ",
		"powershell -noprofile -command ",
		"powershell -command ",
		"pwsh.exe -noprofile -noninteractive -executionpolicy bypass -command ",
		"pwsh.exe -noprofile -command ",
		"pwsh.exe -command ",
		"pwsh -noprofile -noninteractive -executionpolicy bypass -command ",
		"pwsh -noprofile -command ",
		"pwsh -command ",
	}
	for _, p := range prefixes {
		if strings.HasPrefix(lower, p) {
			rest := strings.TrimSpace(s[len(p):])
			if len(rest) >= 2 {
				if (rest[0] == '"' && rest[len(rest)-1] == '"') || (rest[0] == '\'' && rest[len(rest)-1] == '\'') {
					rest = rest[1 : len(rest)-1]
				}
			}
			return rest
		}
	}
	return s
}

// commandHead is the first pipeline or chain segment, so `type file | more`
// still intercepts as a file read.
func commandHead(s string) string {
	s = strings.TrimSpace(s)
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '"' || c == '\'' {
			q := c
			i++
			for i < len(s) && s[i] != q {
				i++
			}
			continue
		}
		if c == '|' {
			return strings.TrimSpace(s[:i])
		}
		if c == '&' {
			// Keep 2>&1 and 1>&2 and >&; treat && and a bare & as a chain.
			if i+1 < len(s) && s[i+1] == '&' {
				return strings.TrimSpace(s[:i])
			}
			if i > 0 && (s[i-1] == '>' || unicode.IsDigit(rune(s[i-1]))) {
				continue
			}
			return strings.TrimSpace(s[:i])
		}
		if c == '\n' || c == ';' {
			return strings.TrimSpace(s[:i])
		}
	}
	return s
}

func firstToken(s string) (verb, rest string) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", ""
	}
	i := 0
	for i < len(s) && !isShellSpace(s[i]) {
		i++
	}
	return s[:i], strings.TrimSpace(s[i:])
}

func firstArg(rest string) string {
	rest = strings.TrimSpace(rest)
	if rest == "" {
		return ""
	}
	if rest[0] == '"' || rest[0] == '\'' {
		q := rest[0]
		if end := strings.IndexByte(rest[1:], q); end >= 0 {
			return rest[1 : 1+end]
		}
	}
	i := 0
	for i < len(rest) && !isShellSpace(rest[i]) {
		i++
	}
	return rest[:i]
}

func isShellSpace(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\r'
}

func hasDirRecursiveFlag(rest string) bool {
	fields := shellFields(rest)
	for _, f := range fields {
		low := strings.ToLower(f)
		switch low {
		case "/s", "-s", "-recurse", "/recurse", "--recurse":
			return true
		}
		// dir /s /b — /s may be combined as /s/b in rare typo forms; also
		// accept /s as a prefix of a bundled switch like /sb if we see /s*.
		if strings.HasPrefix(low, "/s") && (len(low) == 2 || isDirSwitchLetter(low[2:])) {
			return true
		}
	}
	return false
}

func isDirSwitchLetter(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c < 'a' || c > 'z') && (c < 'A' || c > 'Z') {
			return false
		}
	}
	return len(s) > 0
}

func shellFields(s string) []string {
	var fields []string
	i := 0
	for i < len(s) {
		for i < len(s) && isShellSpace(s[i]) {
			i++
		}
		if i >= len(s) {
			break
		}
		if s[i] == '"' || s[i] == '\'' {
			q := s[i]
			i++
			start := i
			for i < len(s) && s[i] != q {
				i++
			}
			fields = append(fields, s[start:i])
			if i < len(s) {
				i++
			}
			continue
		}
		start := i
		for i < len(s) && !isShellSpace(s[i]) {
			i++
		}
		fields = append(fields, s[start:i])
	}
	return fields
}

// sessionScratchDir is the single directory under a session where temporary
// scripts should go: <home>/sessions/<sid>/tmp, or <home>/tmp when the call
// has no session.
func sessionScratchDir(home, sessionID string) (string, error) {
	home = strings.TrimSpace(home)
	if home == "" {
		home = resolveToolHome()
	}
	if home == "" {
		return "", fmt.Errorf("%w: no Remedy home is available, so a scratch directory cannot be created", ErrInvalidInput)
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		return filepath.Join(home, "tmp"), nil
	}
	if !sessionIDPattern.MatchString(sid) {
		return "", fmt.Errorf("%w: session id %q is not a usable directory name", ErrInvalidInput, sid)
	}
	return filepath.Join(home, "sessions", sid, "tmp"), nil
}

func ensureScratchDir(home, sessionID string) string {
	dir, err := sessionScratchDir(home, sessionID)
	if err != nil {
		return ""
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return ""
	}
	return dir
}

func withScratchEnv(spec shellSpawnSpec, home, sessionID string) (shellSpawnSpec, string) {
	scratch := ensureScratchDir(home, sessionID)
	if scratch == "" {
		return spec, ""
	}
	env := spec.env
	if env == nil {
		env = map[string]string{}
	} else {
		// Copy so the caller's map is not mutated.
		cp := make(map[string]string, len(env)+1)
		for k, v := range env {
			cp[k] = v
		}
		env = cp
	}
	if _, ok := env[RemedyScratchEnv]; !ok {
		env[RemedyScratchEnv] = scratch
	}
	spec.env = env
	// The write jail reads the script's own redirects and copies, so the
	// session's scratch folder has to be one of its roots for a temporary
	// script to land there. Empty roots stay empty: that is the unbound jail.
	if len(spec.writeRoots) > 0 {
		spec.writeRoots = append(append([]string(nil), spec.writeRoots...), scratch)
	}
	return spec, scratch
}

// lastLines returns the last n lines of s (empty input stays empty).
func lastLines(s string, n int) string {
	if n <= 0 {
		return ""
	}
	lines := splitLines(s)
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, "\n")
}

const failedCommandLogLines = 40

// FormatFailedCommandPreview is the body a failed bash-like result must
// carry: the exit code (or timeout) plus the last 40 lines of stderr and
// stdout. It is never the bare string "tool failed".
func FormatFailedCommandPreview(output []byte) string {
	var result struct {
		ExitCode *int   `json:"exit_code"`
		TimedOut bool   `json:"timed_out"`
		Stdout   string `json:"stdout"`
		Stderr   string `json:"stderr"`
	}
	if len(output) == 0 {
		return "command failed with no stdout or stderr"
	}
	if err := json.Unmarshal(output, &result); err != nil {
		text := lastLines(string(output), failedCommandLogLines)
		if strings.TrimSpace(text) == "" {
			return "command failed with no stdout or stderr"
		}
		return text
	}
	var b strings.Builder
	switch {
	case result.TimedOut:
		b.WriteString("timed out")
		if result.ExitCode != nil {
			fmt.Fprintf(&b, ", exit_code %d", *result.ExitCode)
		}
	case result.ExitCode != nil:
		fmt.Fprintf(&b, "exit_code %d", *result.ExitCode)
	default:
		b.WriteString("command failed")
	}
	if stderr := lastLines(result.Stderr, failedCommandLogLines); stderr != "" {
		b.WriteString("\nstderr:\n")
		b.WriteString(stderr)
	}
	if stdout := lastLines(result.Stdout, failedCommandLogLines); stdout != "" {
		b.WriteString("\nstdout:\n")
		b.WriteString(stdout)
	}
	if result.Stderr == "" && result.Stdout == "" && result.ExitCode == nil && !result.TimedOut {
		return "command failed with no stdout or stderr"
	}
	return b.String()
}
