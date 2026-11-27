package cli

import (
	"context"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/learn"
)

// `remedy skill …`, `remedy learn …`, `remedy migrate …`: port of
// src/remedy/interfaces/cli/cmd_skills.py (_cmd_skill, _cmd_learn),
// cmd_store.py _cmd_migrate and migrate/from_hermes.py.

func skPct(f float64) string { return fmt.Sprintf("%.0f%%", f*100) }

func skPrintTable(c *Ctx, reg *skRegistry) {
	if len(reg.skills) == 0 {
		c.Printf("No skills registered.\n")
		return
	}
	t := newTable("Registered Skills", "Name", "Version", "Kind", "Status", "Description")
	for _, sk := range reg.skills {
		desc := clip(sk.Description, 60)
		if len([]rune(sk.Description)) > 60 {
			desc += "..."
		}
		t.add(sk.Name, sk.Version, sk.Kind, sk.Status, desc)
	}
	t.print(c.Out)
}

func skPrintExec(c *Ctx, r skExecResult) {
	if r.success {
		c.Printf("  Status: SUCCESS\n")
	} else {
		c.Printf("  Status: FAILED\n")
	}
	c.Printf("  Exit code: %d\n", r.exitCode)
	if r.stdout != "" {
		c.Printf("  stdout: %s\n", clip(r.stdout, 200))
	}
	if r.stderr != "" {
		c.Printf("  stderr: %s\n", clip(r.stderr, 200))
	}
	if r.err != "" {
		c.Printf("  Error: %s\n", r.err)
	}
}

func cmdSkill(c *Ctx) int {
	switch c.P.Sub(1) {
	case "list":
		return skCmdList(c)
	case "discover":
		reg := &skRegistry{}
		n := reg.discover(c.P.Str("path"), !c.P.Bool("no-recurse"))
		c.Printf("Discovered %d skill(s) from %s\n", n, c.P.Str("path"))
		skPrintTable(c, reg)
		return 0
	case "load":
		path := c.P.Str("path")
		// The help says a SKILL.md may be given; Python wanted the folder.
		if skIsFile(path) && strings.EqualFold(filepath.Base(path), "SKILL.md") {
			path = filepath.Dir(path)
		}
		sk, err := skLoadDir(path)
		if err != nil {
			c.Errorf("%v\n", err)
			return 1
		}
		reg := &skRegistry{}
		reg.register(sk)
		c.Printf("Loaded: %s v%s\n", sk.Name, sk.Version)
		skPrintTable(c, reg)
		return 0
	}
	reg := skDefaults(c.Home)
	name := c.P.Str("name")
	sk := reg.get(name)
	if sk == nil {
		c.Errorf("Skill not found: %s\n", name)
		if c.P.Sub(1) == "info" {
			c.Errorf("Run 'remedy skill discover <path>' first.\n")
		}
		return 1
	}
	switch c.P.Sub(1) {
	case "info":
		tags := "none"
		if len(sk.Tags) > 0 {
			tags = strings.Join(sk.Tags, ", ")
		}
		c.Printf("Skill Info\n%s v%s\n%s\n\nKind: %s | Status: %s\nTags: %s\nPath: %s\n",
			sk.Name, sk.Version, sk.Description, sk.Kind, sk.Status, tags, sk.Path)
		if sk.Instructions != "" {
			c.Printf("\nInstructions:\n%s\n", clip(sk.Instructions, 500))
		}
		return 0
	case "run":
		return skCmdRun(c, sk)
	case "test":
		return skCmdTest(c, sk)
	case "export":
		return skCmdExport(c, sk)
	}
	c.usage()
	return 2
}

func skCmdList(c *Ctx) int {
	skPrintList(c.Out, skDefaults(c.Home), c.P.Bool("all"), c.P.Bool("learned"))
	return 0
}

// skPrintList is the `skill list` body: learned probation skills are hidden
// unless --all, --learned shows only learned ones, and the count says what
// it hides.
func skPrintList(w io.Writer, reg *skRegistry, showAll, learnedOnly bool) {
	if len(reg.skills) == 0 {
		fmt.Fprintf(w, "No skills registered. Use 'remedy skill discover <path>'\n")
		return
	}
	var visible []*skSkill
	hidden := 0
	for _, sk := range reg.skills {
		av, _ := sk.Meta.get("auto_generated")
		auto := skTruthy(av)
		if learnedOnly {
			if auto {
				visible = append(visible, sk)
			}
			continue
		}
		// Default: hide auto-learned probation so curated skills stay usable.
		if auto && !showAll && sk.Status != "active" {
			hidden++
			continue
		}
		visible = append(visible, sk)
	}
	total := len(reg.skills)
	head := fmt.Sprintf("%d skill(s)", len(visible))
	switch {
	case hidden > 0:
		head += fmt.Sprintf(" (of %d; %d learned probation hidden — use --all)", total, hidden)
	case len(visible) != total:
		head += fmt.Sprintf(" (of %d)", total)
	}
	fmt.Fprintf(w, "%s:\n", head)
	sort.SliceStable(visible, func(i, j int) bool { return visible[i].Name < visible[j].Name })
	for _, sk := range visible {
		badge := ""
		if av, _ := sk.Meta.get("auto_generated"); skTruthy(av) {
			badge = " (learned/" + sk.Status + ")"
		}
		fmt.Fprintf(w, "  %s%s %s\n", sk.Name, badge, clip(sk.Description, 60))
	}
}

func skInterruptCtx() (context.Context, context.CancelFunc) {
	return signal.NotifyContext(context.Background(), os.Interrupt)
}

func skCmdRun(c *Ctx, sk *skSkill) int {
	ctx, stop := skInterruptCtx()
	defer stop()
	x := newSkExecutor(ctx)
	defer x.cleanup()
	failed := false
	if script := c.P.Str("script"); script != "" && sk.Dir != "" {
		p, err := skJailScript(sk.Dir, script)
		if err != nil {
			c.Errorf("Script path escapes skill scripts/: %s\n", script)
			return 1
		}
		if !skIsFile(p) {
			c.Errorf("Script not found: %s\n", script)
			return 1
		}
		res := x.runScript(p)
		skPrintExec(c, res)
		failed = !res.success
	} else if len(sk.Scripts) > 0 && sk.Dir != "" {
		order, results := x.runAllScripts(sk.Scripts, sk.Dir)
		for _, rel := range order {
			c.Printf("\nScript: %s\n", rel)
			skPrintExec(c, results[rel])
			if !results[rel].success {
				failed = true
			}
		}
	} else {
		c.Printf("No scripts to run. Running instruction code blocks...\n")
		for i, res := range x.runInstructions(sk.Instructions) {
			c.Printf("\nBlock %d\n", i+1)
			skPrintExec(c, res)
			if !res.success {
				failed = true
			}
		}
	}
	if failed {
		return 1
	}
	return 0
}

func skCmdTest(c *Ctx, sk *skSkill) int {
	ctx, stop := skInterruptCtx()
	defer stop()
	x := newSkExecutor(ctx)
	defer x.cleanup()
	checks := []*skValidation{
		skValidateMetadata(sk),
		skValidateDependencies(x, sk),
		skValidateScripts(sk),
		skRunTests(x, sk),
	}
	bad := false
	for _, r := range checks {
		st := "PASS"
		if !r.valid() {
			st, bad = "FAIL", true
		}
		c.Printf("\n%s %s:\n", st, r.name)
		for _, e := range r.errors {
			c.Printf("  Error: %s\n", e)
		}
		for _, w := range r.warns {
			c.Printf("  Warning: %s\n", w)
		}
		for _, tr := range r.tests {
			mark := "PASS"
			if !tr.success {
				mark = "FAIL"
			}
			c.Printf("  Test %s: %s\n", tr.file, mark)
		}
	}
	c.Printf("\nCompliance Score: %s\n", skPct(skComputeScore(checks)))
	if bad {
		return 1
	}
	return 0
}

func skCmdExport(c *Ctx, sk *skSkill) int {
	e := &skExporter{out: c.P.Str("output"), now: time.Now}
	var dest string
	var err error
	switch c.P.Str("format") {
	case "hermes":
		dest, err = e.hermes(sk)
	case "openclaw":
		dest, err = e.openclaw(sk)
	case "zip":
		dest, err = e.zip(sk)
	default:
		dest, err = e.native(sk)
	}
	if err != nil {
		c.Errorf("Export failed: %v\n", err)
		return 1
	}
	c.Printf("Exported to: %s\n", dest)
	return 0
}

// -- learn ----------------------------------------------------------------------

func cmdLearn(c *Ctx) int {
	if err := os.MkdirAll(filepath.Join(c.Home, "skills"), 0o700); err != nil {
		c.Errorf("%v\n", err)
		return 1
	}
	switch c.P.Sub(1) {
	case "reflect":
		return skCmdReflect(c)
	case "history":
		// LearningHistory lived in the Python process; a fresh CLI run has none.
		c.Printf("No learning events recorded.\n")
		return 0
	case "changelog":
		// SkillRefiner's refinement history was in-memory too.
		if name := c.P.Str("skill_name"); name != "" {
			c.Printf("No refinements recorded for skill '%s'.\n", name)
		} else {
			c.Printf("No refinements recorded.\n")
		}
		return 0
	case "stats":
		return skCmdStats(c)
	case "sync":
		c.Printf("Synced 0 learning events to memory.\n")
		return 0
	}
	c.usage()
	return 2
}

// skArgKeys is sorted(str(k) for k in step.arguments) before sorting.
func skArgKeys(v any) []string {
	var out []string
	switch t := v.(type) {
	case skMap:
		for _, e := range t {
			out = append(out, e.K)
		}
	case []any:
		for _, x := range t {
			out = append(out, skPyStr(x))
		}
	case string:
		for _, r := range t {
			out = append(out, string(r))
		}
	}
	return out
}

func skCmdReflect(c *Ctx) int {
	raw, err := skJSONDecode([]byte(c.P.Str("steps")))
	if err != nil {
		c.Errorf("Invalid --steps JSON: %v\n", err)
		return 2
	}
	var steps []learn.CLIStep
	if skTruthy(raw) {
		list, ok := raw.([]any)
		if !ok {
			c.Errorf("Invalid --steps JSON: expected a list of step objects\n")
			return 2
		}
		for i, it := range list {
			s, ok := it.(skMap)
			if !ok {
				c.Errorf("Invalid --steps JSON: step %d is not an object\n", i)
				return 2
			}
			st := learn.CLIStep{Tool: fmt.Sprintf("step_%d", i), Success: true}
			if v, ok := s.get("tool"); ok {
				st.Tool = skPyStr(v)
			}
			if v, ok := s.get("args"); ok && skTruthy(v) {
				st.ArgKeys = skArgKeys(v)
			}
			if v, ok := s.get("result"); ok {
				st.Result = skPyStr(v)
			}
			if v, ok := s.get("success"); ok {
				st.Success = skTruthy(v)
			}
			if v, _ := s.get("error"); skTruthy(v) {
				st.Error = skPyStr(v)
			}
			steps = append(steps, st)
		}
	}
	sk, _, err := learn.ReflectCLI(c.Home, c.P.Str("task_title"), steps, time.Now())
	if err != nil {
		c.Errorf("%v\n", err)
		return 1
	}
	if sk == nil {
		c.Printf("Trace too short for meaningful reflection.\n")
		return 0
	}
	c.Printf("Generated skill: %s\n", sk.Name)
	c.Printf("  Version: %s\n", sk.Version)
	c.Printf("  Tags: %s\n", strings.Join(sk.Tags, ", "))
	c.Printf("Instructions (preview)\n%s\n", clip(sk.Instructions, 400))
	return 0
}

// skStats is SkillStats as SkillRefiner.load_stats reads it.
type skStats struct {
	name                       string
	total, successes, failures int
	avgMS                      float64
	lastExecuted               string // str(datetime), or "None"
	commonErrors               []skKV // count in V (int)
}

func (s skStats) rate() float64 {
	if s.total == 0 {
		return 0.5
	}
	return float64(s.successes) / float64(s.total)
}

// skPyIntOr0 is int(v or 0) for JSON values; what Python could not convert
// counts as 0 instead of a traceback.
func skPyIntOr0(v any) int {
	switch t := v.(type) {
	case skInt:
		n, _ := strconv.Atoi(t.s)
		return n
	case float64:
		return int(t)
	case bool:
		if t {
			return 1
		}
	case string:
		n, _ := strconv.Atoi(strings.ReplaceAll(skPyStrip(t), "_", ""))
		return n
	}
	return 0
}

func skPyFloatOr0(v any) float64 {
	switch t := v.(type) {
	case skInt:
		f, _ := strconv.ParseFloat(t.s, 64)
		return f
	case float64:
		return t
	case bool:
		if t {
			return 1
		}
	case string:
		f, _ := strconv.ParseFloat(skPyStrip(t), 64)
		return f
	}
	return 0
}

var skReISO = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})(?:.(\d{2})(?::?(\d{2})(?::?(\d{2})(?:[.,](\d{1,9}))?)?)?(Z|[+-]\d{2}(?::?\d{2}(?::?\d{2}(?:\.\d{1,6})?)?)?)?)?$`)

// skFromISOStr is str(datetime.fromisoformat(s)), or "" when Python would
// raise ValueError (load_stats then leaves last_executed None).
func skFromISOStr(s string) string {
	m := skReISO.FindStringSubmatch(s)
	if m == nil {
		return ""
	}
	n := func(x string) int { v, _ := strconv.Atoi(x); return v }
	y, mo, d := n(m[1]), n(m[2]), n(m[3])
	if t := time.Date(y, time.Month(mo), d, 0, 0, 0, 0, time.UTC); y < 1 || mo < 1 || mo > 12 || t.Day() != d {
		return ""
	}
	hh, mm, ss := n(m[4]), n(m[5]), n(m[6])
	if hh > 23 || mm > 59 || ss > 59 {
		return ""
	}
	out := fmt.Sprintf("%04d-%02d-%02d %02d:%02d:%02d", y, mo, d, hh, mm, ss)
	if f := m[7]; f != "" {
		f = (f + "000000")[:6]
		if n(f) != 0 {
			out += "." + f
		}
	}
	if tz := m[8]; tz != "" {
		if tz == "Z" {
			return out + "+00:00"
		}
		digits := strings.NewReplacer(":", "").Replace(tz[1:])
		frac := ""
		if i := strings.Index(digits, "."); i >= 0 {
			digits, frac = digits[:i], digits[i+1:]
		}
		oh, om, os := n(digits[0:2]), 0, 0
		if len(digits) >= 4 {
			om = n(digits[2:4])
		}
		if len(digits) >= 6 {
			os = n(digits[4:6])
		}
		if om > 59 || os > 59 || oh > 23 {
			return ""
		}
		sign := tz[:1]
		if oh == 0 && om == 0 && os == 0 && (frac == "" || n(frac) == 0) {
			sign = "+"
		}
		out += fmt.Sprintf("%s%02d:%02d", sign, oh, om)
		if os != 0 || (frac != "" && n(frac) != 0) {
			out += fmt.Sprintf(":%02d", os)
			if frac != "" && n(frac) != 0 {
				out += "." + (frac + "000000")[:6]
			}
		}
	}
	return out
}

// skLoadStats is SkillRefiner.load_stats(<home>/skill_stats.json).
func skLoadStats(home string) []skStats {
	raw, err := os.ReadFile(filepath.Join(home, "skill_stats.json"))
	if err != nil {
		return nil
	}
	v, err := skJSONDecode(raw)
	if err != nil {
		return nil
	}
	top, _ := v.(skMap)
	sv, _ := top.get("skills")
	skills, ok := sv.(skMap)
	if !ok {
		return nil
	}
	var out []skStats
	for _, e := range skills {
		r, ok := e.V.(skMap)
		if !ok {
			continue
		}
		get := func(k string) any { x, _ := r.get(k); return x }
		st := skStats{
			name: e.K, total: skPyIntOr0(get("total_executions")), successes: skPyIntOr0(get("successes")),
			failures: skPyIntOr0(get("failures")), avgMS: skPyFloatOr0(get("avg_duration_ms")), lastExecuted: "None",
		}
		if ce, ok := get("common_errors").(skMap); ok {
			for _, kv := range ce {
				st.commonErrors = append(st.commonErrors, skKV{kv.K, skPyIntOr0(kv.V)})
			}
		}
		if le := get("last_executed"); skTruthy(le) {
			if s := skFromISOStr(skPyStr(le)); s != "" {
				st.lastExecuted = s
			}
		}
		replaced := false
		for i := range out {
			if out[i].name == st.name {
				out[i], replaced = st, true
			}
		}
		if !replaced {
			out = append(out, st)
		}
	}
	return out
}

func skCmdStats(c *Ctx) int {
	all := skLoadStats(c.Home)
	if name := c.P.Str("skill"); name != "" {
		st := skStats{name: name, lastExecuted: "None"}
		for _, s := range all {
			if s.name == name {
				st = s
			}
		}
		c.Printf("Skill Stats\n%s\nExecutions: %d\nSuccesses: %d\nFailures: %d\nSuccess rate: %s\nAvg duration: %.0fms\nLast executed: %s\n",
			st.name, st.total, st.successes, st.failures, skPct(st.rate()), st.avgMS, st.lastExecuted)
		if len(st.commonErrors) > 0 {
			c.Printf("\nCommon Errors:\n")
			for _, kv := range st.commonErrors {
				c.Printf("  (%dx) %s\n", kv.V, kv.K)
			}
		}
		return 0
	}
	if len(all) == 0 {
		c.Printf("No skill stats recorded.\n")
		return 0
	}
	for _, s := range all {
		c.Printf("%s: %d/%d (%s)\n", s.name, s.successes, s.total, skPct(s.rate()))
	}
	return 0
}

// -- migrate --------------------------------------------------------------------

// skCopyTree is shutil.copytree(src, dst) without its one hazard: Python
// followed symlinks, so a folder being imported could carry in a copy of any
// file the link pointed at (an SSH key). A link whose target is inside the
// skill folder is still copied as a file; others are skipped.
func skCopyTree(src, dst string) error {
	base := skResolvePath(src)
	if _, err := os.Lstat(dst); err == nil {
		return fmt.Errorf("[Errno 17] File exists: '%s'", dst)
	}
	return filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, p)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if d.Type()&fs.ModeSymlink != 0 {
			real := skResolvePath(p)
			if !skUnder(base, real) || !skIsFile(real) {
				return nil
			}
			return skCopy2(real, target)
		}
		if d.IsDir() {
			info, err := d.Info()
			if err != nil {
				return err
			}
			return os.MkdirAll(target, info.Mode().Perm()|0o700)
		}
		if !d.Type().IsRegular() {
			return nil
		}
		return skCopy2(p, target)
	})
}

func cmdMigrate(c *Ctx) int {
	label, load := "Hermes", skLoadHermes
	if c.P.Sub(1) == "openclaw" {
		label, load = "OpenClaw", skLoadOpenClaw
	}
	skillsDir := filepath.Join(c.Home, "skills")
	if err := os.MkdirAll(skillsDir, 0o700); err != nil {
		c.Errorf("%v\n", err)
		return 1
	}
	imported, skipped := 0, 0
	var errs []string
	src := skResolvePath(skExpandUser(c.P.Str("path")))
	if !skIsDir(src) {
		errs = append(errs, fmt.Sprintf("%s skills directory not found: %s", label, src))
	} else {
		dest := ""
		if !c.P.Bool("no-copy") {
			dest = skResolvePath(skillsDir)
		}
		for _, sk := range skDiscoverFlat(src, load) {
			if dest != "" && sk.Dir != "" {
				dst := filepath.Join(dest, filepath.Base(sk.Dir))
				if _, err := os.Stat(dst); err != nil {
					if err := skCopyTree(sk.Dir, dst); err != nil {
						errs = append(errs, fmt.Sprintf("Failed to import %s: %v", sk.Name, err))
						skipped++
						continue
					}
					sk.Dir = dst
				}
			}
			imported++
		}
	}
	c.Printf("%s migration: %d imported, %d skipped\n", label, imported, skipped)
	if len(errs) > 0 {
		for _, e := range errs {
			c.Errorf("  Error: %s\n", e)
		}
		return 1
	}
	return 0
}
