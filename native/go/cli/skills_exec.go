package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// Port of src/remedy/skills/executor.py (SkillExecutor), script_path.py
// (resolve_jailed_skill_script) and the interpreter lookup of
// core/build_python.py. Python ran every skill script as a child process of
// a real CPython; so does this, through tools.RunAttached (the authorized
// spawn: Zig deny classifier, capability token, kill-tree).

// skExecResult is ExecutionResult.
type skExecResult struct {
	success        bool
	stdout, stderr string
	exitCode       int
	err            string
}

func skFailed(msg string) skExecResult { return skExecResult{exitCode: -1, err: msg} }

var skRePyStem = regexp.MustCompile(`(?i)^python(?:w)?\d*(?:\.\d+)*$`)

func skBaseName(p string) string {
	p = strings.ReplaceAll(strings.Trim(strings.TrimSpace(p), "\"'"), `\`, "/")
	if i := strings.LastIndex(p, "/"); i >= 0 {
		p = p[i+1:]
	}
	return p
}

// skStemIsPython is build_python._stem_is_python.
func skStemIsPython(p string) bool {
	name := skBaseName(p)
	if strings.HasSuffix(strings.ToLower(name), ".exe") {
		name = name[:len(name)-4]
	}
	return skRePyStem.MatchString(name)
}

// skLooksLikeSidecar is build_python._looks_like_sidecar.
func skLooksLikeSidecar(p string) bool {
	name := strings.ToLower(skBaseName(p))
	name = strings.TrimSuffix(name, ".exe")
	return strings.Contains(name, "remedy") && !skStemIsPython(p)
}

// skWhich is shutil.which: an absolute path to an executable on PATH, or "".
func skWhich(name string) string {
	p, err := tools.ResolveArgv0(name, "")
	if err != nil {
		return ""
	}
	return p
}

var skReWinPyVer = regexp.MustCompile(`(?i)Python(\d)(\d+)`)

// skResolvePython is resolve_python_interpreter without its sys.executable
// steps: a Go binary has no interpreter of its own to fall back on.
func skResolvePython() []string {
	if o := strings.Trim(strings.TrimSpace(os.Getenv("REMEDY_PYTHON")), "\"'"); o != "" {
		if skIsFile(o) {
			return []string{o}
		}
		if f := skWhich(o); f != "" {
			return []string{f}
		}
	}
	usable := func(p string) bool {
		return p != "" && skStemIsPython(p) && !strings.Contains(strings.ToLower(p), "windowsapps")
	}
	if f := skWhich("python"); usable(f) {
		return []string{f}
	}
	if f := skWhich("py"); f != "" {
		return []string{f, "-3"}
	}
	if f := skWhich("python3"); usable(f) {
		return []string{f}
	}
	if runtime.GOOS == "windows" {
		var patterns []string
		if local := os.Getenv("LOCALAPPDATA"); local != "" {
			patterns = append(patterns, filepath.Join(local, "Programs", "Python", "Python3*", "python.exe"))
		}
		for _, kv := range [][2]string{{"PROGRAMFILES", `C:\Program Files`}, {"PROGRAMFILES(X86)", `C:\Program Files (x86)`}} {
			base := os.Getenv(kv[0])
			if base == "" {
				base = kv[1]
			}
			patterns = append(patterns, filepath.Join(base, "Python3*", "python.exe"))
		}
		patterns = append(patterns, `C:\Python3*\python.exe`)
		var hits []string
		for _, pat := range patterns {
			m, _ := filepath.Glob(pat)
			for _, p := range m {
				if skIsFile(p) {
					hits = append(hits, p)
				}
			}
		}
		if len(hits) > 0 {
			ver := func(p string) [2]int {
				m := skReWinPyVer.FindStringSubmatch(p)
				if m == nil {
					return [2]int{}
				}
				var a, b int
				fmt.Sscan(m[1], &a)
				fmt.Sscan(m[2], &b)
				return [2]int{a, b}
			}
			sort.SliceStable(hits, func(i, j int) bool {
				vi, vj := ver(hits[i]), ver(hits[j])
				return vi[0] > vj[0] || (vi[0] == vj[0] && vi[1] > vj[1])
			})
			return []string{hits[0]}
		}
	}
	return nil
}

// skPythonCmd is python_cmd_for_subprocess(): never the Remedy sidecar.
func skPythonCmd() []string {
	if found := skResolvePython(); found != nil && !(len(found) == 1 && skLooksLikeSidecar(found[0])) {
		return found
	}
	for _, name := range []string{"python", "python3"} {
		w := skWhich(name)
		if w == "" || strings.Contains(strings.ToLower(w), "windowsapps") || skLooksLikeSidecar(w) {
			continue
		}
		if skStemIsPython(w) {
			return []string{w}
		}
	}
	return nil
}

// skSafeEnvKeys is credentials.broker._SAFE_EXACT: the only variables a
// skill's child process may see.
var skSafeEnvKeys = map[string]bool{
	"PATH": true, "PATHEXT": true, "SYSTEMROOT": true, "WINDIR": true, "USERPROFILE": true,
	"HOME": true, "HOMEDRIVE": true, "HOMEPATH": true, "APPDATA": true, "LOCALAPPDATA": true,
	"USERNAME": true, "USERDOMAIN": true, "COMPUTERNAME": true, "NUMBER_OF_PROCESSORS": true,
	"PROCESSOR_ARCHITECTURE": true, "COMSPEC": true, "OS": true, "TERM": true, "TEMP": true,
	"TMP": true, "TMPDIR": true, "LANG": true, "LC_ALL": true, "PYTHONIOENCODING": true,
	"PYTHONUTF8": true, "SYSTEMDRIVE": true, "PROGRAMFILES": true, "PROGRAMDATA": true,
}

// skSpawnDeniedEnv are the loader / hook variables the Zig spawn policy
// (native/zig/src/policy.zig env_denied_exact, DYLD_*) refuses to see in an
// override at all, even an empty one; the child inherits the parent's.
var skSpawnDeniedEnv = map[string]bool{
	"LD_PRELOAD": true, "LD_LIBRARY_PATH": true, "LD_AUDIT": true, "GIT_SSH_COMMAND": true,
	"GIT_EXEC_PATH": true, "BROWSER": true, "PYTHONSTARTUP": true, "PERL5OPT": true,
	"NODE_OPTIONS": true, "RUBYOPT": true, "BASH_ENV": true, "ENV": true, "PROMPT_COMMAND": true,
}

// skChildEnv is scrub_subprocess_env(): provider keys, tokens and REMEDY_*
// never reach a skill script. RunAttached merges onto the parent's
// environment, so every other variable is overridden with an empty value
// (CPython ignores an empty PYTHON* variable). The spawn policy's loader
// variables cannot be overridden and stay as the parent has them.
func skChildEnv() map[string]string {
	env := map[string]string{}
	for _, kv := range os.Environ() {
		k, _, ok := strings.Cut(kv, "=")
		if !ok || k == "" {
			continue // Windows' "=C:=C:\..." drive entries
		}
		up := strings.ToUpper(k)
		if skSafeEnvKeys[up] || skSpawnDeniedEnv[up] || strings.HasPrefix(up, "DYLD_") {
			continue
		}
		env[k] = ""
	}
	return env
}

// skOutText is subprocess text=True: universal newlines.
func skOutText(b []byte) string {
	s := strings.ToValidUTF8(string(b), "\uFFFD")
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n")
}

// skExecutor is SkillExecutor; sandbox is made on first use and removed by
// cleanup (Python's mkdtemp sandbox was never removed).
type skExecutor struct {
	sandbox string
	ctx     context.Context
	// run is the spawn; tests replace it.
	run func(ctx context.Context, argv []string, cwd string, env map[string]string, out, errw *bytes.Buffer, timeout time.Duration) (int, bool, error)
}

func newSkExecutor(ctx context.Context) *skExecutor {
	return &skExecutor{ctx: ctx, run: func(ctx context.Context, argv []string, cwd string, env map[string]string, out, errw *bytes.Buffer, timeout time.Duration) (int, bool, error) {
		return tools.RunAttached(ctx, argv, cwd, env, nil, out, errw, timeout)
	}}
}

func (x *skExecutor) sandboxDir() (string, error) {
	if x.sandbox == "" {
		d, err := os.MkdirTemp("", "remedy_exec_")
		if err != nil {
			return "", err
		}
		x.sandbox = d
	}
	return x.sandbox, nil
}

func (x *skExecutor) cleanup() {
	if x.sandbox != "" {
		_ = os.RemoveAll(x.sandbox)
	}
}

func (x *skExecutor) spawn(argv []string, cwd string, timeout time.Duration, timeoutMsg, notFoundMsg string) skExecResult {
	var out, errb bytes.Buffer
	code, timedOut, err := x.run(x.ctx, argv, cwd, skChildEnv(), &out, &errb, timeout)
	switch {
	case timedOut:
		return skFailed(timeoutMsg)
	case errors.Is(err, tools.ErrSpawnRefused):
		return skFailed("Blocked by security policy: " + err.Error())
	case err != nil && code == 127 && notFoundMsg != "":
		return skFailed(notFoundMsg)
	case err != nil:
		return skFailed(err.Error())
	}
	return skExecResult{success: code == 0, stdout: skOutText(out.Bytes()), stderr: skOutText(errb.Bytes()), exitCode: code}
}

// runScript is SkillExecutor.run_script (timeout 300s, cwd the script's folder).
func (x *skExecutor) runScript(script string) skExecResult {
	script = skResolvePath(script)
	if !skIsFile(script) {
		return skFailed("Script not found: " + script)
	}
	py := skPythonCmd()
	if len(py) == 0 {
		return skFailed("No real Python interpreter for skill scripts (Desktop sidecar is not CPython). Set REMEDY_PYTHON.")
	}
	command := append(append([]string(nil), py...), script)
	if danger := DangerousCommand(command); danger != "" {
		return skFailed("Blocked by security policy: " + danger)
	}
	return x.spawn(command, filepath.Dir(script), 300*time.Second,
		"Script timed out after 300.0s", "Python executable not found when running "+script)
}

// skShellPrefix is process_argv.win_shell_prefix.
func skShellPrefix() []string {
	if runtime.GOOS != "windows" {
		sh := skWhich("bash")
		if sh == "" {
			sh = skWhich("sh")
		}
		if sh == "" {
			sh = "/bin/sh"
		}
		return []string{sh, "-c"}
	}
	cmd := skWhich("cmd")
	if cmd == "" {
		cmd = "cmd.exe"
	}
	return []string{cmd, "/c"}
}

func skHex8() string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return fmt.Sprintf("%x", b)
}

func (x *skExecutor) runPythonBlock(code string) skExecResult {
	dir, err := x.sandboxDir()
	if err != nil {
		return skFailed(err.Error())
	}
	tmp := filepath.Join(dir, "exec_"+skHex8()+".py")
	if err := skWriteText(tmp, code); err != nil {
		return skFailed(err.Error())
	}
	defer os.Remove(tmp)
	return x.runScript(tmp)
}

func (x *skExecutor) runBashBlock(code string) skExecResult {
	command := append(skShellPrefix(), code)
	if danger := DangerousCommand(command); danger != "" {
		return skFailed("Blocked by security policy: " + danger)
	}
	dir, err := x.sandboxDir()
	if err != nil {
		return skFailed(err.Error())
	}
	return x.spawn(command, dir, 120*time.Second, "Shell block timed out after 120s", "")
}

var skReCodeBlock = regexp.MustCompile("(?s)```([\\p{L}\\p{N}_]+)" + skPyWS + "*\n(.*?)```")

// runInstructions is SkillExecutor.run_instructions: python/py, bash/sh/shell
// fenced blocks run; any other language is skipped as a success.
func (x *skExecutor) runInstructions(text string) []skExecResult {
	var out []skExecResult
	for _, m := range skReCodeBlock.FindAllStringSubmatch(text, -1) {
		lang, code := strings.ToLower(skPyStrip(m[1])), skPyStrip(m[2])
		switch lang {
		case "python", "py":
			out = append(out, x.runPythonBlock(code))
		case "bash", "sh", "shell":
			out = append(out, x.runBashBlock(code))
		default:
			out = append(out, skExecResult{success: true, exitCode: -1, stdout: "Skipped unsupported language: " + lang})
		}
	}
	return out
}

func skUnder(base, p string) bool {
	r, err := filepath.Rel(base, p)
	return err == nil && r != ".." && !strings.HasPrefix(r, ".."+string(filepath.Separator)) && !filepath.IsAbs(r)
}

// runAllScripts is SkillExecutor.run_all_scripts: every listed script, in
// order, run with Python whatever its extension (as Python did).
func (x *skExecutor) runAllScripts(scripts []string, baseDir string) ([]string, map[string]skExecResult) {
	base := skResolvePath(baseDir)
	res := map[string]skExecResult{}
	var order []string
	for _, rel := range scripts {
		if _, seen := res[rel]; !seen {
			order = append(order, rel)
		}
		p := skResolvePath(filepath.Join(base, rel))
		switch {
		case !skUnder(base, p):
			res[rel] = skExecResult{exitCode: -1, err: "Script path escapes skill directory: " + rel}
		case skIsFile(p):
			res[rel] = x.runScript(p)
		default:
			res[rel] = skExecResult{exitCode: -1, err: "Script not found: " + rel}
		}
	}
	return order, res
}

// skJailError is SkillScriptJailError.
var skJailError = errors.New("script path escapes scripts/")

func skIsAbsoluteScriptName(name string) bool {
	s := strings.TrimSpace(name)
	if s == "" {
		return false
	}
	if filepath.IsAbs(s) || filepath.VolumeName(s) != "" {
		return true
	}
	if strings.HasPrefix(s, "/") || strings.HasPrefix(s, `\`) {
		return true
	}
	if utf8.RuneCountInString(s) >= 2 && []rune(s)[1] == ':' {
		return true
	}
	return strings.HasPrefix(s, `\\`) || strings.HasPrefix(s, "//")
}

// skJailScript is resolve_jailed_skill_script: the script must resolve under
// <skill>/scripts; absolute names and ".." are refused.
func skJailScript(skillDir, scriptName string) (string, error) {
	raw := strings.TrimSpace(scriptName)
	if raw == "" || skIsAbsoluteScriptName(raw) {
		return "", skJailError
	}
	var parts []string
	for _, p := range strings.Split(strings.ReplaceAll(raw, `\`, "/"), "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." {
			return "", skJailError
		}
	}
	if len(parts) > 0 && parts[0] == "scripts" {
		parts = parts[1:]
	}
	if len(parts) == 0 {
		return "", skJailError
	}
	rel := strings.Join(parts, "/")
	if skIsAbsoluteScriptName(rel) {
		return "", skJailError
	}
	scriptsRoot := skResolvePath(filepath.Join(skResolvePath(skillDir), "scripts"))
	cand := skResolvePath(filepath.Join(scriptsRoot, filepath.FromSlash(rel)))
	if !skUnder(scriptsRoot, cand) {
		return "", skJailError
	}
	return cand, nil
}
