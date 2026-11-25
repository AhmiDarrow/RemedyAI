package prompt

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
)

// Parity with the Python worker: every fixture in testdata/ was recorded by
// scripts/record_prompt_fixtures.py. For each ported section the Go text
// must equal the recorded text byte for byte after the temp-path
// placeholders are filled in. Sections the port does not build must be
// reported in Result.Missing, never emitted.

type fixtureSection struct {
	Name string `json:"name"`
	Sep  string `json:"sep"`
	Text string `json:"text"`
}

type fixtureStep struct {
	Op         string             `json:"op"`
	Input      map[string]any     `json:"input"`
	Output     map[string]any     `json:"output"`
	Sections   []fixtureSection   `json:"sections"`
	FilesAfter map[string]*string `json:"files_after"`
}

type fixture struct {
	Case    string            `json:"case"`
	Clock   float64           `json:"clock"`
	Config  map[string]any    `json:"config"`
	Files   map[string]string `json:"files"`
	Readers map[string]any    `json:"readers"`
	Steps   []fixtureStep     `json:"steps"`
}

// portedSections are the section names this package builds.
var portedSections = map[string]bool{
	"system_prompt": true, "runtime_info": true, "turn_marker": true, "isolation": true,
	"soul": true, "alive": true, "builder": true, "workspace": true, "access_reminder": true,
	"approvals": true, "messenger": true, "rmb": true, "self_config": true,
	"durable_memory": true, "manual": true, "skills": true, "plan_mode_addendum": true,
	"frontier_build_addendum": true, "build_mode_addendum": true, "history": true,
}

// missingKey maps a recorded section that is not ported to the Missing
// entry that must announce it.
func missingKey(name string) string {
	switch {
	case strings.HasPrefix(name, "metabolism_"):
		return "metabolism"
	case name == "orientation" || name == "fingerprint":
		return "work_roots"
	case name == "build_engine" || name == "auto_resume":
		return "build_resume"
	}
	return name
}

// sideEffectOwner is the section whose producer writes a file.
var sideEffectOwner = map[string]string{
	"{HOME}/inner_language.jsonl": "alive",
	"{HOME}/soul/vigil.json":      "soul",
}

type placeholders struct {
	pairs []string
}

func newPlaceholders(t *testing.T, root string) (placeholders, map[string]string) {
	dirs := map[string]string{
		"{USER}":    filepath.Join(root, "user"),
		"{HOME}":    filepath.Join(root, "user", ".remedy"),
		"{PROJECT}": filepath.Join(root, "work", "project"),
		"{REPO}":    filepath.Join(root, "repo"),
	}
	var pairs []string
	for tag, dir := range dirs {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		real := resolvePath(dir, dir)
		dirs[tag] = real
		pairs = append(pairs, tag[:len(tag)-1]+":esc}", strings.ReplaceAll(real, `\`, `\\`), tag, real)
	}
	return placeholders{pairs: pairs}, dirs
}

func (p placeholders) fill(s string) string { return strings.NewReplacer(p.pairs...).Replace(s) }

func (p placeholders) fillAny(v any) any {
	switch t := v.(type) {
	case string:
		return p.fill(t)
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = p.fillAny(x)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			out[p.fill(k)] = p.fillAny(x)
		}
		return out
	}
	return v
}

// sepNorm makes Windows-recorded paths comparable on other systems.
func sepNorm(s string) string {
	if runtime.GOOS == "windows" {
		return s
	}
	return strings.ReplaceAll(s, `\`, "/")
}

type rankedSkills struct {
	count int
	rows  []SkillRow
}

func (r rankedSkills) Count() int { return r.count }
func (r rankedSkills) Rank(string, int, string) []SkillRow {
	return r.rows
}

func loadFixtures(t *testing.T) []fixture {
	paths, err := filepath.Glob(filepath.Join("testdata", "*.json"))
	if err != nil || len(paths) == 0 {
		t.Fatalf("no fixtures: %v", err)
	}
	var out []fixture
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		var f fixture
		if err := json.Unmarshal(data, &f); err != nil {
			t.Fatalf("%s: %v", p, err)
		}
		out = append(out, f)
	}
	return out
}

type caseEnv struct {
	env     Env
	ph      placeholders
	dirs    map[string]string
	defects map[string]fixtureSection
}

// knownPythonDefects are recorded spans the port deliberately does not
// copy: case -> section -> the fixture whose recording holds the correct
// text for that section.
var knownPythonDefects = map[string]map[string]string{
	// The worker builds its runtime without a skill registry, so its prompt
	// always says "Skills loaded: (none yet …)" although skill.search and
	// skill.activate serve the installed library natively. The port renders
	// the catalog from the library; the expected text is the catalog branch
	// recorded in skills_registry (same message, project and ranking).
	"skills": {"skills": "skills_registry"},
}

func defectFixes(t *testing.T, f fixture, all map[string]fixture) map[string]fixtureSection {
	out := map[string]fixtureSection{}
	for section, source := range knownPythonDefects[f.Case] {
		src, ok := all[source]
		if !ok {
			t.Fatalf("defect fix source %q missing", source)
		}
		found := false
		for _, st := range src.Steps {
			for _, s := range st.Sections {
				if s.Name == section {
					out[section], found = s, true
				}
			}
		}
		if !found {
			t.Fatalf("%s has no %q section", source, section)
		}
	}
	return out
}

func setupCase(t *testing.T, f fixture) caseEnv {
	root := t.TempDir()
	ph, dirs := newPlaceholders(t, root)
	for name, body := range f.Files {
		p := filepath.FromSlash(ph.fill(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(ph.fill(body)), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	cfg, _ := ph.fillAny(f.Config).(map[string]any)
	clock := time.Unix(0, int64(f.Clock*1e9)).UTC()
	env := Env{
		Home:     dirs["{HOME}"],
		UserHome: dirs["{USER}"],
		HelpRoots: []string{
			filepath.Join(dirs["{REPO}"], "docs", "manual"),
			filepath.Join(dirs["{REPO}"], "desktop", "src", "help", "articles"),
		},
		Clock:  func() time.Time { return clock },
		Config: cfg,
		State:  &State{},
	}
	if rows, ok := f.Readers["skills_ranked"].([]any); ok {
		rs := rankedSkills{}
		if n, ok := f.Readers["skills_count"].(float64); ok {
			rs.count = int(n)
		}
		for _, r := range rows {
			m := r.(map[string]any)
			rs.rows = append(rs.rows, SkillRow{Name: pyStr(m, "name"), Status: pyStr(m, "status"), Description: pyStr(m, "description")})
		}
		env.Skills = rs
	}
	return caseEnv{env: env, ph: ph, dirs: dirs}
}

func readText(path string) (string, bool) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	return strings.ReplaceAll(string(data), "\r\n", "\n"), true
}

// sameFile compares a side-effect file: JSON files by value (the Go and
// Python writers lay JSON out differently), anything else as text.
func sameFile(name, want, got string) bool {
	if strings.HasSuffix(name, ".json") {
		var a, b any
		if json.Unmarshal([]byte(want), &a) != nil || json.Unmarshal([]byte(got), &b) != nil {
			return false
		}
		return reflect.DeepEqual(a, b)
	}
	return want == got
}

type stepReport struct {
	matched, notPorted []string
	matchedChars       int
	totalChars         int
}

// checkAssemble runs one recorded assemble step and compares it.
func checkAssemble(t *testing.T, ce caseEnv, step fixtureStep) stepReport {
	t.Helper()
	in, _ := ce.ph.fillAny(step.Input).(map[string]any)
	before := snapshotFiles(ce)
	readiness := Missing(OpAssemble, ce.env, in)
	res, err := Assemble(ce.env, in)
	if err != nil {
		t.Fatalf("Assemble: %v", err)
	}
	if !reflect.DeepEqual(readiness, res.Missing) {
		t.Errorf("Missing() = %v, Assemble reported %v", readiness, res.Missing)
	}
	missing := map[string]bool{}
	for _, m := range res.Missing {
		missing[m] = true
		if k, _, ok := strings.Cut(m, ":"); ok {
			missing[k] = true
		}
	}
	var rep stepReport
	var want []fixtureSection
	for _, s := range step.Sections {
		size := len([]rune(ce.ph.fill(s.Sep + s.Text)))
		rep.totalChars += size
		switch {
		case !portedSections[s.Name]:
			if !missing[missingKey(s.Name)] {
				t.Errorf("section %q is not ported but Missing does not say so (%v)", s.Name, res.Missing)
			}
			rep.notPorted = append(rep.notPorted, s.Name)
		case missing[s.Name]:
			// Ported, but this call needs a piece the port does not build.
			rep.notPorted = append(rep.notPorted, s.Name)
		default:
			if fix, ok := ce.defects[s.Name]; ok {
				// Explicit exception: the recorded span is a Python defect
				// (see knownPythonDefects); compare against the fix instead.
				s = fix
			}
			want = append(want, s)
		}
	}
	var got []fixtureSection
	produced := map[string]bool{}
	for _, s := range res.Sections {
		got = append(got, fixtureSection{s.Name, s.Sep, s.Text})
		produced[s.Name] = true
	}
	for i := 0; i < len(want) || i < len(got); i++ {
		if i >= len(want) {
			t.Errorf("extra Go section %q:\n%s", got[i].Name, got[i].Text)
			continue
		}
		if i >= len(got) {
			t.Errorf("Go did not build section %q", want[i].Name)
			continue
		}
		w := fixtureSection{want[i].Name, ce.ph.fill(want[i].Sep), ce.ph.fill(want[i].Text)}
		g := got[i]
		if w.Name != g.Name {
			t.Errorf("section %d: want %q, got %q", i, w.Name, g.Name)
			continue
		}
		if sepNorm(w.Sep+w.Text) != sepNorm(g.Sep+g.Text) {
			t.Errorf("section %q differs:\n--- python\n%q\n--- go\n%q", w.Name, w.Sep+w.Text, g.Sep+g.Text)
			continue
		}
		rep.matched = append(rep.matched, w.Name)
		rep.matchedChars += len([]rune(w.Sep + w.Text))
	}
	if res.Complete() && len(ce.defects) == 0 {
		if g, w := sepNorm(pyStr(res.Output, "system")), sepNorm(ce.ph.fill(pyStr(step.Output, "system"))); g != w {
			t.Errorf("complete result differs from the recorded system block")
		}
	}
	// Side effects of the ported producers.
	after := snapshotFiles(ce)
	for name, owner := range sideEffectOwner {
		path := filepath.FromSlash(ce.ph.fill(name))
		wantBody, pyChanged := step.FilesAfter[name]
		gotBody, ok := after[path]
		goChanged := ok && gotBody != before[path]
		if !produced[owner] {
			if goChanged {
				t.Errorf("%s changed although %s was not built", name, owner)
			}
			continue
		}
		if pyChanged != goChanged {
			t.Errorf("%s: python changed=%v, go changed=%v", name, pyChanged, goChanged)
			continue
		}
		if pyChanged && wantBody != nil && !sameFile(name, ce.ph.fill(*wantBody), gotBody) {
			t.Errorf("%s differs:\n--- python\n%s\n--- go\n%s", name, ce.ph.fill(*wantBody), gotBody)
		}
	}
	for path, body := range after {
		if before[path] == body {
			continue
		}
		owned := false
		for name := range sideEffectOwner {
			if filepath.FromSlash(ce.ph.fill(name)) == path {
				owned = true
			}
		}
		if !owned {
			t.Errorf("Go wrote an unexpected file %s", path)
		}
	}
	return rep
}

func snapshotFiles(ce caseEnv) map[string]string {
	out := map[string]string{}
	for _, root := range []string{ce.dirs["{HOME}"], ce.dirs["{PROJECT}"]} {
		_ = filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() {
				if body, ok := readText(p); ok {
					out[p] = body
				}
			}
			return nil
		})
	}
	return out
}

func checkOther(t *testing.T, ce caseEnv, step fixtureStep) stepReport {
	in, _ := ce.ph.fillAny(step.Input).(map[string]any)
	var res Result
	switch step.Op {
	case OpShouldContinue:
		res = ShouldContinue(ce.env, in)
	case OpSlimEpoch:
		res = SlimEpoch(ce.env, in)
	case OpAfterToolBatch:
		res = AfterToolBatch(ce.env, in)
	case OpTurnEnd:
		res = TurnEnd(ce.env, in)
	default:
		t.Fatalf("unknown op %s", step.Op)
	}
	if !reflect.DeepEqual(res.Missing, Missing(step.Op, ce.env, in)) {
		t.Errorf("Missing() disagrees with the operation: %v", res.Missing)
	}
	body, _ := json.Marshal(step.Output)
	rep := stepReport{totalChars: len([]rune(string(body)))}
	if !res.Complete() {
		if res.Output != nil {
			t.Errorf("incomplete %s returned output %v", step.Op, res.Output)
		}
		rep.notPorted = []string{step.Op}
		return rep
	}
	want, _ := ce.ph.fillAny(step.Output).(map[string]any)
	gotJSON, _ := json.Marshal(res.Output)
	var got map[string]any
	_ = json.Unmarshal(gotJSON, &got)
	if !reflect.DeepEqual(want, got) {
		t.Errorf("%s output:\n python %v\n go     %v", step.Op, want, got)
		return rep
	}
	rep.matched = []string{step.Op}
	rep.matchedChars = rep.totalChars
	return rep
}

func byCase(fs []fixture) map[string]fixture {
	out := map[string]fixture{}
	for _, f := range fs {
		out[f.Case] = f
	}
	return out
}

func runFixture(t *testing.T, f fixture, all map[string]fixture) []stepReport {
	ce := setupCase(t, f)
	ce.defects = defectFixes(t, f, all)
	var reps []stepReport
	for i, step := range f.Steps {
		t.Run(fmt.Sprintf("%d_%s", i, strings.TrimPrefix(step.Op, "prompt.")), func(t *testing.T) {
			if step.Op == OpAssemble {
				reps = append(reps, checkAssemble(t, ce, step))
			} else {
				reps = append(reps, checkOther(t, ce, step))
			}
		})
	}
	return reps
}

func TestParity(t *testing.T) {
	fs := loadFixtures(t)
	all := byCase(fs)
	for _, f := range fs {
		t.Run(f.Case, func(t *testing.T) { runFixture(t, f, all) })
	}
}

// TestParitySummary prints, per case, what matched, what is not ported,
// and the coverage in characters.
func TestParitySummary(t *testing.T) {
	var lines []string
	totalM, totalT := 0, 0
	fs := loadFixtures(t)
	all := byCase(fs)
	for _, f := range fs {
		var reps []stepReport
		t.Run(f.Case, func(t *testing.T) { reps = runFixture(t, f, all) })
		for i, r := range reps {
			totalM += r.matchedChars
			totalT += r.totalChars
			pct := 0.0
			if r.totalChars > 0 {
				pct = 100 * float64(r.matchedChars) / float64(r.totalChars)
			}
			np := uniq(r.notPorted)
			lines = append(lines, fmt.Sprintf("%-20s step %d %-24s %5.1f%% (%d/%d chars)\n    matched: %s\n    not ported: %s",
				f.Case, i, f.Steps[i].Op, pct, r.matchedChars, r.totalChars,
				strings.Join(uniq(r.matched), ", "), strings.Join(np, ", ")))
		}
	}
	t.Logf("parity summary\n%s\nTOTAL %.1f%% (%d/%d chars)", strings.Join(lines, "\n"),
		100*float64(totalM)/float64(max(1, totalT)), totalM, totalT)
}

func uniq(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	if len(out) == 0 {
		return []string{"-"}
	}
	return out
}
