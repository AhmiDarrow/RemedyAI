package cli

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// `remedy uninstall`: a port of src/remedy/interfaces/uninstaller.py. Every
// deletion goes through sysWipeRemove, which refuses any path that is not
// the checked Remedy home itself (--purge) or one of the fixed names below
// directly inside it.

// sysConfigNames are the files _wipe_config removes (plus auth/).
var sysConfigNames = []string{"config.toml", "config.yaml", "config.yml", "desktop.json", "comfyui.json"}

// sysWipeNames is every entry under home that any flag may remove.
var sysWipeNames = map[string]bool{
	"config.toml": true, "config.yaml": true, "config.yml": true, "desktop.json": true, "comfyui.json": true,
	"auth":   true,
	"vision": true, "llama-server": true, "llama.cpp": true,
	"skills": true, "skill_stats.json": true,
}

// sysPipDists is _pip_dists: only remedy-ai. The PyPI name "remedy" is an
// unrelated package and must never be removed.
var sysPipDists = []string{"remedy-ai"}

// sysUserHome is replaceable in tests.
var sysUserHome = os.UserHomeDir

// sysSafeWipeRoot is _assert_safe_wipe_root: the resolved home must be named
// .remedy, not sit at a filesystem root, not be the user's home, and not lie
// under a Windows system folder.
func sysSafeWipeRoot(home string) (string, error) {
	target := expandUser(home)
	abs, err := filepath.Abs(target)
	if err != nil {
		return "", err
	}
	resolved := abs
	if r, err := filepath.EvalSymlinks(abs); err == nil {
		resolved = r
	}
	resolved = filepath.Clean(resolved)
	if strings.ToLower(filepath.Base(resolved)) != ".remedy" {
		return "", fmt.Errorf("Refuse wipe: path must be a .remedy directory, got %s", resolved)
	}
	parent := filepath.Dir(resolved)
	if parent == resolved || filepath.Dir(parent) == parent {
		return "", fmt.Errorf("Refuse wipe of .remedy at drive/FS root: %s", resolved)
	}
	if uh, err := sysUserHome(); err == nil && uh != "" {
		u, _ := filepath.Abs(uh)
		if r, err := filepath.EvalSymlinks(u); err == nil {
			u = r
		}
		if sysSamePath(filepath.Clean(u), resolved) {
			return "", fmt.Errorf("Refuse wipe of user home: %s", resolved)
		}
	}
	blocked := map[string]bool{"windows": true, "system32": true, "program files": true, "program files (x86)": true, "programdata": true}
	for p := parent; ; {
		if blocked[strings.ToLower(filepath.Base(p))] {
			return "", fmt.Errorf("Refuse wipe under system path: %s", resolved)
		}
		next := filepath.Dir(p)
		if next == p {
			break
		}
		p = next
	}
	return resolved, nil
}

func sysSamePath(a, b string) bool {
	if runtime.GOOS == "windows" || runtime.GOOS == "darwin" {
		return strings.EqualFold(a, b)
	}
	return a == b
}

// sysRemoveAll does the actual removal; tests wrap it to record targets.
var sysRemoveAll = os.RemoveAll

// sysWipeRemove removes path only when it is root itself (allowWhole) or a
// fixed wipe name directly inside root. A symlink is removed as a link and
// never followed.
func sysWipeRemove(root, path string, allowWhole bool) error {
	root = filepath.Clean(root)
	p := filepath.Clean(path)
	ok := false
	if allowWhole && p == root {
		ok = true
	} else if filepath.Dir(p) == root && sysWipeNames[filepath.Base(p)] {
		ok = true
	}
	if !ok {
		return fmt.Errorf("refuse to remove %s: outside the Remedy data paths under %s", p, root)
	}
	return sysRemoveAll(p)
}

func sysExists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}

// sysWipeConfig is _wipe_config.
func sysWipeConfig(c *Ctx, home string) error {
	root, err := sysSafeWipeRoot(home)
	if err != nil {
		return err
	}
	for _, n := range sysConfigNames {
		p := filepath.Join(root, n)
		if sysExists(p) {
			if err := sysWipeRemove(root, p, false); err != nil {
				return err
			}
			c.Printf("  removed %s\n", p)
		}
	}
	auth := filepath.Join(root, "auth")
	if sysExists(auth) {
		_ = sysWipeRemove(root, auth, false)
		c.Printf("  removed %s\n", auth)
	}
	return nil
}

// sysMatchingProcesses is execution.process.matching_processes (fail
// closed to none); replaceable in tests.
var sysMatchingProcesses = func(pattern string) [][2]string {
	if runtime.GOOS == "windows" {
		ps := "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | " +
			"Where-Object { $_.CommandLine -and ($_.CommandLine -match '" + pattern + "') } | " +
			"ForEach-Object { \"{0}`t{1}\" -f $_.ProcessId, $_.CommandLine }"
		var out, errb bytes.Buffer
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if _, _, err := tools.RunAttached(ctx, []string{"powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps}, "", nil, nil, &out, &errb, 5*time.Second); err != nil {
			return nil
		}
		var res [][2]string
		for _, line := range strings.Split(out.String(), "\n") {
			pid, cmd, ok := strings.Cut(strings.TrimRight(line, "\r"), "\t")
			cmd = strings.TrimSpace(cmd)
			// The PowerShell query's own command line names the pattern too.
			if !ok || cmd == "" || strings.Contains(cmd, "Get-CimInstance") {
				continue
			}
			if n, err := strconv.Atoi(strings.TrimSpace(pid)); err == nil && n > 0 {
				res = append(res, [2]string{strconv.Itoa(n), cmd})
			}
		}
		return res
	}
	re, err := regexp.Compile("(?i)" + pattern)
	if err != nil {
		return nil
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil
	}
	self := os.Getpid()
	var res [][2]string
	for _, e := range entries {
		n, err := strconv.Atoi(e.Name())
		if err != nil || n == self {
			continue
		}
		raw, err := os.ReadFile(filepath.Join("/proc", e.Name(), "cmdline"))
		if err != nil {
			continue
		}
		text := strings.TrimSpace(strings.ReplaceAll(string(raw), "\x00", " "))
		if text != "" && re.MatchString(text) {
			res = append(res, [2]string{e.Name(), text})
		}
	}
	return res
}

// sysKillTree is the Zig kill_tree; replaceable in tests.
var sysKillTree = func(pid int) error { return core.ProcessKillTree(uint32(pid)) }

// sysStopLlamaServers is _stop_llama_server_processes.
func sysStopLlamaServers() {
	for _, m := range sysMatchingProcesses("llama-server") {
		if pid, err := strconv.Atoi(m[0]); err == nil && pid > 0 {
			_ = sysKillTree(pid)
		}
	}
}

// sysWipeVisionData is vision.install.wipe_vision_data; replaceable in tests.
var sysWipeVisionData = func(root string) (map[string]any, error) { return httpapi.UninstallWipeVision(root) }

// sysWipeVision is _wipe_vision.
func sysWipeVision(c *Ctx, home string) error {
	root, err := sysSafeWipeRoot(home)
	if err != nil {
		return err
	}
	sysStopLlamaServers()
	vision := filepath.Join(root, "vision")
	res, verr := sysWipeVisionData(root)
	if sysExists(vision) {
		_ = sysWipeRemove(root, vision, false)
		if verr != nil {
			c.Printf("  removed %s\n", vision)
		}
	}
	if verr == nil {
		extra := ""
		if len(res) > 0 {
			extra = fmt.Sprintf(" — %v", res)
		}
		c.Printf("  removed %s (llama.cpp + visual models)%s\n", vision, extra)
	}
	for _, stray := range []string{"llama-server", "llama.cpp"} {
		p := filepath.Join(root, stray)
		if sysExists(p) {
			_ = sysWipeRemove(root, p, false)
		}
	}
	return nil
}

// sysWipeSkills is _wipe_skills: skills/ and skill_stats.json only.
func sysWipeSkills(c *Ctx, home string) error {
	root, err := sysSafeWipeRoot(home)
	if err != nil {
		return err
	}
	for _, n := range []string{"skills", "skill_stats.json"} {
		p := filepath.Join(root, n)
		if sysExists(p) {
			if err := sysWipeRemove(root, p, false); err != nil {
				return err
			}
			c.Printf("  removed %s\n", p)
		}
	}
	return nil
}

// sysFrozen reports a binary not launched from a pip install (Desktop
// sidecar or standalone): the launcher passes REMEDY_CLI_PYTHON otherwise.
func sysFrozen() bool { return strings.TrimSpace(os.Getenv("REMEDY_CLI_PYTHON")) == "" }

// sysPackagePath is _get_package_path (from the launcher).
func sysPackagePath() string { return strings.TrimSpace(os.Getenv("REMEDY_CLI_PACKAGE_DIR")) }

// sysPipRun runs `python -m pip ...` and captures its output; replaceable.
var sysPipRun = func(args []string, timeout time.Duration) (stdout, stderr string, code int, err error) {
	py := strings.TrimSpace(os.Getenv("REMEDY_CLI_PYTHON"))
	if py == "" {
		return "", "", 1, errors.New("no Python interpreter (REMEDY_CLI_PYTHON unset)")
	}
	var out, errb bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), timeout+5*time.Second)
	defer cancel()
	code, timedOut, err := tools.RunAttached(ctx, append([]string{py, "-m", "pip"}, args...), "", nil, nil, &out, &errb, timeout)
	if timedOut && err == nil {
		err = fmt.Errorf("timed out after %s", timeout)
	}
	return out.String(), errb.String(), code, err
}

// sysPipUninstall is _pip_uninstall.
func sysPipUninstall(c *Ctx) bool {
	ok := true
	for _, dist := range sysPipDists {
		out, errs, code, err := sysPipRun([]string{"uninstall", "-y", dist}, 60*time.Second)
		if err != nil {
			c.Printf("pip uninstall failed (%s): %v\n", dist, err)
			ok = false
			continue
		}
		if o := strings.TrimSpace(out); o != "" {
			c.Println(o)
		}
		e := strings.TrimSpace(errs)
		if code != 0 && !strings.Contains(strings.ToLower(e), "not installed") && !strings.Contains(e, "Skipping") {
			c.Printf("pip uninstall warning (%s):\n%s\n", dist, e)
			ok = false
		}
	}
	return ok
}

// sysDataFiles lists every file under home (_get_data_files).
func sysDataFiles(home string) [][2]string {
	var out [][2]string
	_ = filepath.WalkDir(home, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		if st, err := os.Stat(p); err != nil || !st.Mode().IsRegular() {
			return nil
		}
		rel, _ := filepath.Rel(home, p)
		out = append(out, [2]string{"data file - " + rel, p})
		return nil
	})
	return out
}

func cmdUninstall(c *Ctx) int {
	purge := c.P.Bool("purge")
	config := c.P.Bool("config")
	skills := c.P.Bool("skills")
	dry := c.P.Bool("dry-run")
	home := c.Home
	if abs, err := filepath.Abs(expandUser(home)); err == nil {
		home = abs
		if r, err := filepath.EvalSymlinks(abs); err == nil {
			home = r
		}
	}
	if purge {
		config, skills = true, true
	}

	sysPanel(c, "Uninstaller", "Preparing to uninstall Remedy...")
	if pkg := sysPackagePath(); pkg != "" {
		c.Printf("\nPackage:  %s\n", pkg)
	} else {
		c.Println("\nPackage:  not found (may already be removed)")
	}
	if sysExists(home) {
		for _, f := range sysDataFiles(home) {
			c.Printf("  %s: %s\n", f[0], f[1])
		}
	}
	state := "(kept)"
	if purge {
		state = "(full wipe)"
	} else if config || skills {
		state = fmt.Sprintf("(partial: config=%s skills=%s)", sysPyBool(config), sysPyBool(skills))
	}
	c.Printf("\nHome dir: %s %s\n", home, state)

	if dry {
		c.Println("\nDry run complete. No changes made.")
		c.Println("Run without --dry-run to proceed.")
		return 0
	}

	c.Println()
	var action string
	switch {
	case purge:
		action = "Uninstall package AND full-wipe all Remedy data?\nThis cannot be undone!"
	case config || skills:
		var bits []string
		if config {
			bits = append(bits, "config")
		}
		if skills {
			bits = append(bits, "skills")
		}
		action = "Uninstall package and remove " + strings.Join(bits, ", ") + "?"
	default:
		action = "Uninstall the remedy package (keep user data)?"
	}
	if !sysConfirmOrNo(c, action) {
		c.Println("Uninstall cancelled.")
		return 0
	}

	ok := true
	if sysFrozen() {
		c.Println("\nRemedy Desktop is removed from Windows Apps / your package manager; this clears data only.")
	} else {
		c.Println("\nUninstalling package...")
		ok = sysPipUninstall(c)
	}

	if purge && sysExists(home) {
		root, err := sysSafeWipeRoot(home)
		if err != nil {
			c.Errorf("Wipe aborted: %v\n", err)
			return 1
		}
		c.Printf("\nFull wipe %s...\n", root)
		_ = sysWipeVision(c, root)
		if err := sysWipeRemove(root, root, true); err != nil {
			c.Printf("Failed to remove data: %v\n", err)
			ok = false
		} else {
			c.Println("Remedy data removed.")
		}
	} else {
		if config || skills {
			c.Println("\nRemoving selected user data...")
		}
		var err error
		if config {
			if err = sysWipeConfig(c, home); err == nil {
				err = sysWipeVision(c, home)
			}
		}
		if err == nil && skills {
			err = sysWipeSkills(c, home)
		}
		if err != nil {
			c.Printf("Failed to remove data: %v\n", err)
			ok = false
		}
	}

	if !ok {
		c.Errorf("\nUninstall finished with errors.\n")
		return 1
	}
	c.Println()
	c.Println("Uninstall complete.")
	c.Println("\nThanks for trying Remedy!")
	c.Println("To reinstall: pip install remedy-ai or pip install -e . from the repo")
	return 0
}

// sysConfirmOrNo asks a yes/no question defaulting to no; ended input is no.
func sysConfirmOrNo(c *Ctx, prompt string) (yes bool) {
	defer func() {
		if r := recover(); r != nil {
			if err, ok := r.(error); ok && errors.Is(err, errSysNoInput) {
				c.Println()
				yes = false
				return
			}
			panic(r)
		}
	}()
	return newSysPrompter(c).Confirm(prompt, false)
}
