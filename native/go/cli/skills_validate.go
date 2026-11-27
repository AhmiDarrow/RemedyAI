package cli

import (
	"bytes"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// Port of src/remedy/skills/validator.py (SkillValidator).

// skValidation is ValidationResult.
type skValidation struct {
	name          string
	errors, warns []string
	tests         []skTestRun
}

type skTestRun struct {
	file     string
	success  bool
	exitCode int
	err      string
}

func (v *skValidation) valid() bool { return len(v.errors) == 0 }

// skRePEP440 is packaging.version.VERSION_PATTERN, anchored as Version() does.
var skRePEP440 = regexp.MustCompile(`(?i)^` + skPyWS + `*v?(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*` +
	`(?:[-_.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_.]?[0-9]*)?` +
	`(?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?` +
	`(?:[-_.]?dev[-_.]?[0-9]*)?` +
	`(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?` + skPyWS + `*$`)

// validateMetadata is SkillValidator.validate_metadata.
func skValidateMetadata(sk *skSkill) *skValidation {
	r := &skValidation{name: sk.Name}
	if skPyStrip(sk.Name) == "" {
		r.errors = append(r.errors, "Skill name is missing or empty")
	}
	if utf8.RuneCountInString(sk.Description) < 10 {
		r.errors = append(r.errors, "Description must be at least 10 characters")
	}
	if sk.Version == "" {
		r.errors = append(r.errors, "Version is required")
	}
	if !skRePEP440.MatchString(sk.Version) {
		r.warns = append(r.warns, fmt.Sprintf("Version '%s' is not valid SemVer", sk.Version))
	}
	if sk.Status == "discovered" {
		r.warns = append(r.warns, "Skill is only in 'discovered' state; activate to use")
	}
	if utf8.RuneCountInString(sk.Instructions) < 20 {
		r.warns = append(r.warns, "Skill instructions are very short; consider expanding")
	}
	if sk.Author == nil {
		r.warns = append(r.warns, "No author specified; consider adding one")
	}
	if len(sk.Tags) == 0 {
		r.warns = append(r.warns, "No tags specified; tags help with discoverability")
	}
	return r
}

// skImportProbe is run by the interpreter skill scripts use: argv names the
// packages; it prints the index of each one that does not import.
const skImportProbe = "import sys\n" +
	"for i, p in enumerate(sys.argv[1:]):\n" +
	"    try:\n" +
	"        __import__(p)\n" +
	"    except BaseException:\n" +
	"        print(i)\n"

// skValidateDependencies is SkillValidator.validate_dependencies. Python
// tried __import__ in its own process; here the interpreter skill scripts run
// with (skPythonCmd) is asked, once, for all of them.
func skValidateDependencies(x *skExecutor, sk *skSkill) *skValidation {
	r := &skValidation{name: sk.Name}
	var deps, pkgs []string
	for _, dep := range sk.Requires {
		if skPyStrip(dep) == "" {
			continue
		}
		pkg := strings.Split(dep, ">=")[0]
		pkg = strings.Split(pkg, "==")[0]
		pkg = skPyStrip(strings.Split(pkg, "<")[0])
		deps, pkgs = append(deps, dep), append(pkgs, pkg)
	}
	if len(deps) == 0 {
		return r
	}
	py := skPythonCmd()
	if len(py) == 0 {
		for _, dep := range deps {
			r.warns = append(r.warns, fmt.Sprintf("Dependency '%s' not checked: no Python interpreter (set REMEDY_PYTHON)", dep))
		}
		return r
	}
	argv := append(append(append([]string(nil), py...), "-c", skImportProbe), pkgs...)
	var out, errb bytes.Buffer
	cwd, _ := os.Getwd()
	code, timedOut, err := x.run(x.ctx, argv, cwd, skChildEnv(), &out, &errb, 120*time.Second)
	if err != nil || timedOut || code != 0 {
		for _, dep := range deps {
			r.warns = append(r.warns, fmt.Sprintf("Dependency '%s' not checked: the Python import check did not run", dep))
		}
		return r
	}
	missing := map[int]bool{}
	for _, line := range strings.Fields(out.String()) {
		if i, err := strconv.Atoi(line); err == nil {
			missing[i] = true
		}
	}
	for i, dep := range deps {
		if missing[i] {
			r.warns = append(r.warns, fmt.Sprintf("Dependency '%s' not installed in current environment", dep))
		}
	}
	return r
}

// skValidateScripts is SkillValidator.validate_scripts.
func skValidateScripts(sk *skSkill) *skValidation {
	r := &skValidation{name: sk.Name}
	if sk.Dir == "" {
		return r
	}
	for _, rel := range sk.Scripts {
		if !skIsFile(filepath.Join(sk.Dir, rel)) {
			r.errors = append(r.errors, "Referenced script does not exist: "+rel)
		}
	}
	for _, rel := range sk.References {
		if !skIsFile(filepath.Join(sk.Dir, rel)) {
			r.warns = append(r.warns, "Referenced file does not exist: "+rel)
		}
	}
	return r
}

// skGlobNoDot is Path.glob(pattern) in one folder: "*" never matches a
// leading dot.
func skGlobNoDot(dir, pattern string) []string {
	m, _ := filepath.Glob(filepath.Join(dir, pattern))
	var out []string
	for _, p := range m {
		if !strings.HasPrefix(filepath.Base(p), ".") {
			out = append(out, p)
		}
	}
	sort.Strings(out)
	return out
}

// skRunTests is SkillValidator.run_tests: test_*.py and *_test.py in the
// skill folder, then tests/__init__.py and a file literally named
// tests/test_*.py (what Python's list actually holds), each run as a script.
func skRunTests(x *skExecutor, sk *skSkill) *skValidation {
	r := &skValidation{name: sk.Name}
	if sk.Dir == "" {
		r.warns = append(r.warns, "No source directory; skipping tests")
		return r
	}
	base := sk.Dir
	files := append(skGlobNoDot(base, "test_*.py"), skGlobNoDot(base, "*_test.py")...)
	if skIsDir(filepath.Join(base, "tests")) {
		files = append(files, filepath.Join(base, "tests", "__init__.py"), filepath.Join(base, "tests", "test_*.py"))
	}
	for _, tf := range files {
		if !skIsFile(tf) {
			continue
		}
		res := x.runScript(tf)
		rel, _ := filepath.Rel(base, tf)
		r.tests = append(r.tests, skTestRun{file: rel, success: res.success, exitCode: res.exitCode, err: res.err})
		if !res.success {
			r.errors = append(r.errors, fmt.Sprintf("Test failed: %s (exit code %d)", filepath.Base(tf), res.exitCode))
		}
	}
	if len(r.tests) == 0 {
		r.warns = append(r.warns, "No test files found (looked for test_*.py, *_test.py, tests/)")
	}
	return r
}

// skComputeScore is SkillValidator.compute_score.
func skComputeScore(results []*skValidation) float64 {
	if len(results) == 0 {
		return 1.0
	}
	passing := 0
	penalty := 0.0
	for _, r := range results {
		if r.valid() {
			passing++
		}
		penalty += math.Min(float64(len(r.warns))*0.02, 0.3)
	}
	return math.Max(0, math.Min(1, float64(passing)/float64(len(results))-penalty))
}
