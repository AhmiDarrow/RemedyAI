package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// `remedy update [--check]`: a port of src/remedy/interfaces/updater.py. The
// Python CLI updater checks PyPI (remedy-ai) and git, then applies with
// `pip install --upgrade remedy-ai` or `git pull` + `pip install -e .`, and
// only after the owner says yes at a terminal. The Desktop app updates
// itself (latest.json + signatures) and is only told about here.

// sysPyPIURL is PYPI_URL. Never pypi.org/pypi/remedy: an unrelated package.
var sysPyPIURL = "https://pypi.org/pypi/remedy-ai/json"

const sysManualClone = "  git clone https://github.com/AhmiDarrow/RemedyAI.git && cd RemedyAI"

// sysRunResult is one captured child run.
type sysRunResult struct {
	stdout, stderr string
	code           int
	err            error
}

// sysUpdater holds every outside effect of `remedy update`, so tests drive
// it without a network, git or pip.
type sysUpdater struct {
	installed     func() string
	latest        func() string // "" when unreachable or malformed
	frozen        func() bool
	installSource func() string
	projectRoot   func() string
	git           func() string // "" when git is not on PATH
	isTTY         func() bool
	run           func(argv []string, cwd string, timeout time.Duration) sysRunResult
	python        func() string
	configExists  func() bool
}

var sysNewUpdater = func(c *Ctx) *sysUpdater {
	u := &sysUpdater{
		installed:    func() string { return Version(httpapi.Version) },
		latest:       func() string { return sysFetchLatest(sysPyPIURL) },
		frozen:       sysFrozen,
		python:       func() string { return strings.TrimSpace(os.Getenv("REMEDY_CLI_PYTHON")) },
		projectRoot:  func() string { return sysFindProjectRoot(sysPackageStart()) },
		git:          func() string { p, _ := tools.ResolveArgv0("git", ""); return p },
		isTTY:        func() bool { return sysIsTerminal(c.In) },
		run:          sysRunCapture,
		configExists: sysDefaultConfigExists,
	}
	u.installSource = func() string { return sysProbeInstallSource(u.python(), u.run) }
	return u
}

func cmdUpdate(c *Ctx) int { return sysNewUpdater(c).runUpdate(c, c.P.Bool("check")) }

func sysIsTerminal(r io.Reader) bool {
	f, ok := r.(*os.File)
	return ok && secret.IsTerminal(f.Fd())
}

// sysRunCapture runs argv through the authorized spawn path and captures it.
func sysRunCapture(argv []string, cwd string, timeout time.Duration) sysRunResult {
	var out, errb bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), timeout+5*time.Second)
	defer cancel()
	code, timedOut, err := tools.RunAttached(ctx, argv, cwd, nil, nil, &out, &errb, timeout)
	if timedOut && err == nil {
		err = fmt.Errorf("timed out after %s", timeout)
	}
	return sysRunResult{stdout: out.String(), stderr: errb.String(), code: code, err: err}
}

// sysFetchLatest is _get_latest_version: info.version from the PyPI JSON,
// "" on any failure or a malformed answer.
func sysFetchLatest(url string) string {
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return ""
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Remedy-Updater")
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return ""
	}
	var data struct {
		Info struct {
			Version any `json:"version"`
		} `json:"info"`
	}
	if json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&data) != nil {
		return ""
	}
	v, _ := data.Info.Version.(string)
	return strings.TrimSpace(v)
}

// sysParseVersion is _parse_version: strip v, drop -/+ suffixes, leading
// digits of each dot segment (missing = 0), padded to three parts.
func sysParseVersion(v string) []int {
	s := strings.TrimLeft(strings.TrimSpace(v), "vV")
	for _, sep := range []string{"-", "+"} {
		if i := strings.Index(s, sep); i >= 0 {
			s = s[:i]
		}
	}
	var parts []int
	for _, p := range strings.Split(s, ".") {
		n := 0
		for _, ch := range p {
			if ch < '0' || ch > '9' {
				break
			}
			n = n*10 + int(ch-'0')
		}
		parts = append(parts, n)
	}
	for len(parts) < 3 {
		parts = append(parts, 0)
	}
	return parts
}

// sysIsNewer is _is_newer: Python tuple ordering (a longer tuple with an
// equal prefix sorts higher).
func sysIsNewer(latest, installed string) bool {
	a, b := sysParseVersion(latest), sysParseVersion(installed)
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return len(a) > len(b)
}

// sysProbeScript prints the remedy-ai (or legacy remedy) distribution's
// direct_url.json, as _get_distribution().read_text does.
const sysProbeScript = `import sys
import importlib.metadata as m
for n in ("remedy-ai", "remedy"):
    try:
        d = m.distribution(n)
    except Exception:
        continue
    sys.stdout.write("DIST\n" + (d.read_text("direct_url.json") or ""))
    break
else:
    sys.stdout.write("NODIST")
`

func sysProbeInstallSource(py string, run func([]string, string, time.Duration) sysRunResult) string {
	if py == "" {
		return "unknown"
	}
	r := run([]string{py, "-c", sysProbeScript}, "", 30*time.Second)
	if r.err != nil || r.code != 0 {
		return "unknown"
	}
	out := r.stdout
	if !strings.HasPrefix(out, "DIST\n") {
		return "unknown"
	}
	return sysClassifyDirectURL(strings.TrimPrefix(out, "DIST\n"))
}

// sysClassifyDirectURL is the direct_url.json half of _detect_install_source.
func sysClassifyDirectURL(text string) string {
	if strings.TrimSpace(text) == "" {
		return "pip"
	}
	var raw any
	if json.Unmarshal([]byte(text), &raw) != nil {
		return "unknown"
	}
	info, ok := raw.(map[string]any)
	if !ok {
		return "unknown"
	}
	if di, ok := info["dir_info"].(map[string]any); ok {
		if b, _ := di["editable"].(bool); b {
			return "git-editable"
		}
	}
	url := ""
	if s, ok := info["url"].(string); ok {
		url = strings.ToLower(s)
	}
	vcs := ""
	if vi, ok := info["vcs_info"].(map[string]any); ok {
		if s, ok := vi["vcs"].(string); ok {
			vcs = strings.ToLower(s)
		}
	}
	if vcs == "git" || strings.HasSuffix(url, ".git") || strings.HasPrefix(url, "git+") ||
		strings.HasPrefix(url, "git://") || strings.HasPrefix(url, "ssh://") {
		return "git-folder"
	}
	return "pip"
}

// sysIsRemedyPyproject is _is_remedy_pyproject.
func sysIsRemedyPyproject(text string) bool {
	if strings.Contains(text, `name = "remedy-ai"`) || strings.Contains(text, `name = 'remedy-ai'`) {
		return true
	}
	legacy := strings.Contains(text, `name = "remedy"`) || strings.Contains(text, `name = 'remedy'`)
	return legacy && (strings.Contains(strings.ToLower(text), "coding agent") || strings.Contains(text, "remedy-ai") || strings.Contains(text, "Ahmi"))
}

// sysPackageStart is where _find_project_root starts: the Python package
// folder the launcher names, else this binary's folder.
func sysPackageStart() string {
	if p := sysPackagePath(); p != "" {
		return p
	}
	if exe, err := os.Executable(); err == nil {
		return filepath.Dir(exe)
	}
	return ""
}

func sysIsFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// sysFindProjectRoot is _find_project_root: the first ancestor whose
// pyproject is this product's, or a git tree with our package layout. A git
// tree holding this Go module also counts, so the root is still found once
// the Python package leaves the tree.
func sysFindProjectRoot(start string) string {
	if start == "" {
		return ""
	}
	abs, err := filepath.Abs(start)
	if err != nil {
		return ""
	}
	if r, err := filepath.EvalSymlinks(abs); err == nil {
		abs = r
	}
	for root := abs; ; {
		hasGit := sysExists(filepath.Join(root, ".git"))
		pp := filepath.Join(root, "pyproject.toml")
		if sysIsFile(pp) {
			if raw, err := os.ReadFile(pp); err == nil && sysIsRemedyPyproject(string(raw)) {
				return root
			}
		}
		if hasGit {
			if sysIsFile(filepath.Join(root, "src", "remedy", "interfaces", "updater.py")) {
				return root
			}
			if raw, err := os.ReadFile(filepath.Join(root, "native", "go", "go.mod")); err == nil &&
				strings.Contains(string(raw), "module github.com/AhmiDarrow/RemedyAI/native/go") {
				return root
			}
		}
		parent := filepath.Dir(root)
		if parent == root {
			return ""
		}
		root = parent
	}
}

func sysDefaultConfigExists() bool {
	uh, err := os.UserHomeDir()
	if err != nil {
		return false
	}
	return sysIsFile(filepath.Join(uh, ".remedy", "config.toml"))
}

var sysRemedyAIName = regexp.MustCompile(`(?m)^name\s*=\s*["']remedy-ai["']`)

// sysIsSourceCheckout is self_inject_draft.is_source_checkout.
func sysIsSourceCheckout(root string) bool {
	if root == "" || !sysExists(filepath.Join(root, ".git")) {
		return false
	}
	raw, err := os.ReadFile(filepath.Join(root, "pyproject.toml"))
	return err == nil && sysRemedyAIName.Match(raw)
}

// dirtyAbort is origin_wins_if_dirty's abort_dirty: a source checkout with
// uncommitted changes. A failed status check does not block (Python's
// suppress around the policy).
func (u *sysUpdater) dirtyAbort(root string) bool {
	if !sysIsSourceCheckout(root) {
		return false
	}
	git := u.git()
	if git == "" {
		return false
	}
	r := u.run([]string{git, "-C", root, "status", "--porcelain"}, "", 15*time.Second)
	return r.err == nil && r.code == 0 && strings.TrimSpace(r.stdout) != ""
}

func (u *sysUpdater) gitPullAndReinstall(c *Ctx, root string) bool {
	git := u.git()
	if git == "" {
		c.Println("git not found in PATH")
		return false
	}
	r := u.run([]string{git, "pull"}, root, 60*time.Second)
	if r.err != nil {
		c.Printf("git pull error: %v\n", r.err)
		return false
	}
	if r.code != 0 {
		c.Printf("git pull failed:\n%s\n", r.stderr)
		return false
	}
	c.Println(strings.TrimSpace(r.stdout))
	py := u.python()
	if py == "" {
		c.Println("pip install error: no Python interpreter (REMEDY_CLI_PYTHON unset)")
		return false
	}
	r = u.run([]string{py, "-m", "pip", "install", "-e", "."}, root, 120*time.Second)
	if r.err != nil {
		c.Printf("pip install error: %v\n", r.err)
		return false
	}
	if r.code != 0 {
		c.Printf("pip install failed:\n%s\n", r.stderr)
		return false
	}
	return true
}

func (u *sysUpdater) pipUpgrade(c *Ctx) bool {
	py := u.python()
	if py == "" {
		c.Println("pip upgrade error: no Python interpreter (REMEDY_CLI_PYTHON unset)")
		return false
	}
	r := u.run([]string{py, "-m", "pip", "install", "--upgrade", "remedy-ai"}, "", 120*time.Second)
	if r.err != nil {
		c.Printf("pip upgrade error: %v\n", r.err)
		return false
	}
	if r.code != 0 {
		c.Printf("pip upgrade failed:\n%s\n", r.stderr)
		return false
	}
	c.Println(strings.TrimSpace(r.stdout))
	return true
}

func (u *sysUpdater) postUpdateChecks(c *Ctx) {
	c.Println("\nPost-update verification:")
	status := func(ok bool) string {
		if ok {
			return "OK"
		}
		return "WARN"
	}
	c.Printf("  %s  Version check\n", status(u.installed() != ""))
	c.Printf("  %s  Config valid\n", status(u.configExists()))
	c.Printf("  %s  Memory store\n", status(true))
}

// gitBehind is the fetch + rev-list probe; "" when unknown.
func (u *sysUpdater) gitBehind(root string) string {
	git := u.git()
	if git == "" {
		return ""
	}
	u.run([]string{git, "fetch", "origin"}, root, 30*time.Second)
	for _, ref := range []string{"@{upstream}", "origin/master", "origin/main"} {
		r := u.run([]string{git, "rev-list", "--count", "HEAD.." + ref, "--"}, root, 30*time.Second)
		s := strings.TrimSpace(r.stdout)
		if r.err == nil && r.code == 0 && s != "" && sysAllDigits(s) {
			return s
		}
	}
	return ""
}

func sysAllDigits(s string) bool {
	_, err := strconv.ParseUint(s, 10, 64)
	return err == nil
}

func (u *sysUpdater) runUpdate(c *Ctx, checkOnly bool) int {
	sysPanel(c, "Remedy Updater", "Checking for updates...")
	installed := u.installed()
	if installed == "" {
		installed = "unknown"
	}
	c.Printf("  Installed: %s\n", installed)

	if u.frozen() {
		latest := u.latest()
		if latest != "" && sysIsNewer(latest, installed) {
			c.Printf("  A newer Remedy Desktop (%s) is out. Update from the app: About Remedy -> Check for updates.\n", latest)
		} else {
			c.Println("  Remedy Desktop is up to date.")
		}
		return 0
	}

	c.Printf("  Checking PyPI... ")
	latest := u.latest()
	if latest == "" {
		c.Println("unreachable (offline?)")
	} else {
		c.Println(latest)
	}

	src := u.installSource()
	root := u.projectRoot()
	gitSource := src == "git-editable" || src == "git-folder"
	behind := false
	if gitSource && root != "" {
		c.Printf("  Install source: %s\n", src)
		c.Printf("  Project root:   %s\n", root)
		if u.git() != "" {
			if n := u.gitBehind(root); n != "" && n != "0" {
				behind = true
				c.Printf("  Git behind by: %s commits\n", n)
			} else {
				c.Println("  Git status:    up to date")
			}
		}
	}

	updatable := false
	switch {
	case latest != "" && behind:
		updatable = true
	case latest != "" && sysIsNewer(latest, installed):
		updatable = true
		c.Printf("\nUpdate available: %s -> %s\n", installed, latest)
	case latest != "" && latest != installed:
		c.Printf("\nPyPI has %s (installed: %s is newer or different)\n", latest, installed)
	case behind:
		updatable = true
		c.Printf("\nGit update available (%s)\n", installed)
	case latest == "":
		c.Errorf("\nCould not reach PyPI; update status unknown (offline?).\n")
		return 1
	default:
		c.Println("\nRemedy is up to date.")
	}
	if !updatable {
		return 0
	}
	if checkOnly {
		c.Println("\nUse 'remedy update' (without --check) to apply updates.")
		return 0
	}
	c.Println()
	if !u.isTTY() {
		c.Println("Non-interactive terminal; use --check first, then re-run to apply.")
		return 0
	}
	if !sysConfirmRequired(c, "Apply update now?") {
		c.Println("Update cancelled.")
		return 0
	}

	var ok bool
	if gitSource && root != "" {
		if u.dirtyAbort(root) {
			c.Println("\nLocal self-improve or WIP is dirty. Official update does not merge per-client patches.")
			c.Println("Post the draft to the GitHub inbox issue (`self_improve_submit_issue`) or discard it (`git reset --hard` + clean) then retry.")
			return 1
		}
		c.Println("\nPulling from git + reinstalling...")
		ok = u.gitPullAndReinstall(c, root)
	} else {
		c.Println("\nUpgrading via pip...")
		ok = u.pipUpgrade(c)
	}
	if !ok {
		c.Println("\nUpdate failed. Try manually:")
		c.Println(sysManualClone)
		c.Println("  pip install -e .")
		c.Println("  # or: pip install --upgrade remedy-ai")
		return 1
	}
	c.Println("\nUpdate complete!")
	c.Printf("  New version: %s\n", u.installed())
	u.postUpdateChecks(c)
	return 0
}

// sysConfirmRequired is Confirm.ask with no default: an empty answer re-asks;
// ended input is no.
func sysConfirmRequired(c *Ctx, prompt string) (yes bool) {
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
	p := newSysPrompter(c)
	for {
		fmt.Fprintf(c.Out, "%s [y/n]: ", prompt)
		switch strings.ToLower(strings.TrimSpace(p.line())) {
		case "y":
			return true
		case "n":
			return false
		}
		fmt.Fprintln(c.Out, "Please enter Y or N")
	}
}
