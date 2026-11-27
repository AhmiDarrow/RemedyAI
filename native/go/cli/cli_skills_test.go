package cli

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// Ports of tests/test_cli_skill_commands.py, test_cli_skill_list_and_partner_status.py,
// test_migration_import.py and the CLI-visible parts of test_skill_loader.py,
// test_skill_lifecycle.py, test_skills_system_v2.py and test_bundled_skills.py.

// skRun runs the CLI with a fresh --home and returns (code, stdout, stderr).
func skRun(t *testing.T, home string, args ...string) (int, string, string) {
	t.Helper()
	t.Setenv("REMEDY_HOME", home) // ResolveCLIHome publishes it; restore after
	var out, errw bytes.Buffer
	code := Run(append([]string{"--home", home}, args...), &out, &errw, strings.NewReader(""), Hooks{}, "test")
	return code, out.String(), errw.String()
}

func skFake(name string, learned bool, status, desc string) *skSkill {
	if desc == "" {
		desc = name + " does a thing"
	}
	sk := &skSkill{Name: name, Description: desc, Version: "1.0.0", Kind: "native", Status: status, Tags: []string{"demo"}}
	if learned {
		sk.Meta.set("auto_generated", true)
	}
	return sk
}

func skListOut(reg *skRegistry, all, learned bool) string {
	var b bytes.Buffer
	skPrintList(&b, reg, all, learned)
	return b.String()
}

// --- listing ------------------------------------------------------------------

func TestCLISkillListEmptyRegistrySaysHowToPopulateIt(t *testing.T) {
	out := skListOut(&skRegistry{}, false, false)
	if !strings.Contains(out, "No skills registered") || !strings.Contains(out, "skill discover") {
		t.Fatalf("got %q", out)
	}
}

func TestCLISkillListHandWrittenSkillsInNameOrder(t *testing.T) {
	reg := &skRegistry{}
	reg.register(skFake("zebra", false, "active", ""))
	reg.register(skFake("alpha", false, "active", ""))
	out := skListOut(reg, false, false)
	if !strings.Contains(out, "2 skill(s):") || strings.Index(out, "alpha") > strings.Index(out, "zebra") {
		t.Fatalf("got %q", out)
	}
}

func TestCLISkillListHidesProbationAndSaysSo(t *testing.T) {
	reg := &skRegistry{}
	reg.register(skFake("project-etiquette", false, "active", "Ship gate chain"))
	reg.register(skFake("file_read-bash_exec-chain", true, "discovered", "tool chain noise"))
	reg.register(skFake("learned-winner", true, "active", "promoted"))

	out := skListOut(reg, false, false)
	if !strings.Contains(out, "project-etiquette") || !strings.Contains(out, "learned-winner") {
		t.Errorf("default list lost a skill: %q", out)
	}
	if strings.Contains(out, "file_read-bash_exec-chain") {
		t.Errorf("probation skill shown by default: %q", out)
	}
	if !strings.Contains(out, "2 skill(s) (of 3; 1 learned probation hidden — use --all):") {
		t.Errorf("count does not admit what it hides: %q", out)
	}
	if !strings.Contains(out, "learned-winner (learned/active) promoted") {
		t.Errorf("learned skill not badged: %q", out)
	}

	all := skListOut(reg, true, false)
	if !strings.Contains(all, "file_read-bash_exec-chain (learned/discovered)") || !strings.HasPrefix(all, "3 skill(s):") {
		t.Errorf("--all: %q", all)
	}
	learned := skListOut(reg, false, true)
	if !strings.Contains(learned, "file_read-bash_exec-chain") || !strings.Contains(learned, "learned-winner") ||
		strings.Contains(learned, "project-etiquette") || !strings.HasPrefix(learned, "2 skill(s) (of 3):") {
		t.Errorf("--learned: %q", learned)
	}
}

func TestCLISkillListTrimsLongDescriptions(t *testing.T) {
	reg := &skRegistry{}
	reg.register(skFake("verbose", false, "active", strings.Repeat("x", 500)))
	out := skListOut(reg, false, false)
	if strings.Contains(out, strings.Repeat("x", 61)) || !strings.Contains(out, strings.Repeat("x", 60)) {
		t.Fatalf("got %q", out)
	}
}

func TestCLISkillListFromDiskHidesLearnedProbation(t *testing.T) {
	home := t.TempDir()
	skWriteFixture(t, filepath.Join(home, "skills", "learned-foo"),
		"---\nname: learned-foo\ndescription: Auto skill\nstatus: discovered\nmetadata:\n  auto_generated: true\n  effort_weight: 0.8\n---\n\nBody\n")
	code, out, _ := skRun(t, home, "skill", "list")
	if code != 0 || strings.Contains(out, "learned-foo") || !strings.Contains(out, "learned probation hidden") {
		t.Fatalf("code %d out %q", code, out)
	}
	_, out, _ = skRun(t, home, "skill", "list", "--learned")
	if !strings.Contains(out, "learned-foo (learned/discovered) Auto skill") {
		t.Fatalf("--learned: %q", out)
	}
}

// test_skills_system_v2 / test_bundled_skills: curated skills seed into the
// home and become active; a learned one keeps its probation status.
func TestCLISkillDefaultsSeedBundledAndRespectLearnedStatus(t *testing.T) {
	home := t.TempDir()
	skWriteFixture(t, filepath.Join(home, "skills", "learned-foo"),
		"---\nname: learned-foo\ndescription: Auto skill\nstatus: discovered\nmetadata:\n  auto_generated: true\n---\n\nBody\n")
	reg := skDefaults(home)
	if sk := reg.get("learned-foo"); sk == nil || sk.Status != "discovered" {
		t.Fatalf("learned-foo: %+v", sk)
	}
	po := reg.get("project-overview")
	if po == nil {
		t.Skip("bundled skills not reachable from this checkout")
	}
	if po.Status != "active" {
		t.Errorf("project-overview status %q", po.Status)
	}
	for _, name := range []string{"project-overview", "comfyui", "project-etiquette", "change-safety"} {
		if !skIsFile(filepath.Join(home, "skills", name, "SKILL.md")) {
			t.Errorf("%s not seeded into the home", name)
		}
	}
	if len(reg.skills) < 24 {
		t.Errorf("only %d skills", len(reg.skills))
	}
}

func TestCLISkillSeededCopyRefreshesOnBundledVersionBump(t *testing.T) {
	root := t.TempDir()
	bundled, user := filepath.Join(root, "bundled", "comfyui"), filepath.Join(root, "user", "comfyui")
	skWriteFixture(t, bundled, "---\nname: comfyui\nversion: 1.1.0\n---\n# New bootstrap\n")
	skWriteFixture(t, user, "---\nname: comfyui\nversion: 1.0.0\n---\n# Old\n")
	if ok, err := skRefreshSeeded(bundled, user); !ok || err != nil {
		t.Fatalf("refresh %v %v", ok, err)
	}
	body, _ := os.ReadFile(filepath.Join(user, "SKILL.md"))
	if !strings.Contains(string(body), "1.1.0") || !strings.Contains(string(body), "New bootstrap") {
		t.Fatalf("not refreshed: %q", body)
	}
	skWriteFixture(t, user, "---\nname: comfyui\nversion: 1.0.0\n---\n# Locked\n")
	if err := os.WriteFile(filepath.Join(user, ".user_locked"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	skWriteFixture(t, bundled, "---\nname: comfyui\nversion: 9.9.9\n---\n# Even newer\n")
	if ok, _ := skRefreshSeeded(bundled, user); ok {
		t.Fatal("locked skill refreshed")
	}
	body, _ = os.ReadFile(filepath.Join(user, "SKILL.md"))
	if !strings.Contains(string(body), "Locked") {
		t.Fatalf("locked skill overwritten: %q", body)
	}
	if !skTupleGreater(skVersionTuple("1.10"), skVersionTuple("1.9")) || skTupleGreater(skVersionTuple("1.0"), skVersionTuple("1.0.0")) {
		t.Error("version tuple order")
	}
}

func TestCLISkillSeededCopyGainsBundledLocalBlock(t *testing.T) {
	root := t.TempDir()
	b, u := filepath.Join(root, "b", "SKILL.md"), filepath.Join(root, "u", "SKILL.md")
	skWriteFixture(t, filepath.Dir(b), "---\nname: comfyui\nlocal:\n  services:\n  - id: comfyui\ntools: [local_discover, comfyui]\n---\nB\n")
	skWriteFixture(t, filepath.Dir(u), "---\nname: comfyui\ndescription: mine\ntools: [comfyui]\n---\nMy body\n")
	if err := skMergeBundledLocal(b, u); err != nil {
		t.Fatal(err)
	}
	got, _ := skReadText(u)
	want := "---\nname: comfyui\ndescription: mine\ntools:\n- comfyui\n- local_discover\nlocal:\n  services:\n  - id: comfyui\n---\nMy body\n"
	if got != want {
		t.Fatalf("got\n%q\nwant\n%q", got, want)
	}
}

// --- info -----------------------------------------------------------------------

func TestCLISkillInfoDescribesASkill(t *testing.T) {
	home := t.TempDir()
	skWriteFixture(t, filepath.Join(home, "skills", "deploy"),
		"---\nname: deploy\ndescription: deploy does a thing\nversion: 1.0.0\ntags: [demo]\n---\nhow to use deploy\n")
	code, out, _ := skRun(t, home, "skill", "info", "deploy")
	want := "Skill Info\ndeploy v1.0.0\ndeploy does a thing\n\nKind: native | Status: active\nTags: demo\nPath: "
	if code != 0 || !strings.HasPrefix(out, want) || !strings.HasSuffix(out, "\n\nInstructions:\nhow to use deploy\n") {
		t.Fatalf("code %d out %q", code, out)
	}
}

func TestCLISkillInfoUnknownExitsOneAndSaysWhatToDo(t *testing.T) {
	code, _, errw := skRun(t, t.TempDir(), "skill", "info", "nope-not-a-skill")
	if code != 1 || !strings.Contains(errw, "Skill not found: nope-not-a-skill") || !strings.Contains(errw, "discover") {
		t.Fatalf("code %d err %q", code, errw)
	}
}

// --- discover and load ---------------------------------------------------------

func TestCLISkillDiscoverReportsAndRecursesUnlessTold(t *testing.T) {
	home, dir := t.TempDir(), t.TempDir()
	skWriteFixture(t, dir, "---\nname: top\ndescription: Top level\n---\n\nBody")
	skWriteFixture(t, filepath.Join(dir, "nested"), "---\nname: nested\ndescription: Nested\n---\n\nBody")
	skWriteFixture(t, filepath.Join(dir, "broken"), "---\ndescription: no name\n---\n")
	code, out, _ := skRun(t, home, "skill", "discover", dir)
	if code != 0 || !strings.Contains(out, "Discovered 2 skill(s) from "+dir) || !strings.Contains(out, "Registered Skills") {
		t.Fatalf("code %d out %q", code, out)
	}
	_, out, _ = skRun(t, home, "skill", "discover", dir, "--no-recurse")
	if !strings.Contains(out, "Discovered 1 skill(s)") || strings.Contains(out, "nested") {
		t.Fatalf("--no-recurse: %q", out)
	}
	_, out, _ = skRun(t, home, "skill", "discover", filepath.Join(dir, "missing"))
	if !strings.Contains(out, "Discovered 0 skill(s)") || !strings.Contains(out, "No skills registered.") {
		t.Fatalf("missing dir: %q", out)
	}
}

func TestCLISkillLoadNamesTheSkillAndAcceptsSkillMD(t *testing.T) {
	home, dir := t.TempDir(), filepath.Join(t.TempDir(), "one")
	skWriteFixture(t, dir, "---\nname: loaded-one\ndescription: A loaded one\nversion: 2.0\n---\nBody\n")
	for _, p := range []string{dir, filepath.Join(dir, "SKILL.md")} {
		code, out, _ := skRun(t, home, "skill", "load", p)
		if code != 0 || !strings.HasPrefix(out, "Loaded: loaded-one v2.0\nRegistered Skills\n") {
			t.Fatalf("%s: code %d out %q", p, code, out)
		}
	}
	code, _, errw := skRun(t, home, "skill", "load", filepath.Join(dir, "nope"))
	if code != 1 || !strings.Contains(errw, "Skill directory not found") {
		t.Fatalf("code %d err %q", code, errw)
	}
}

// --- loader (test_skill_loader.py) -------------------------------------------------

const skSampleMD = "---\nname: test-skill\ndescription: A skill for testing\nversion: 1.2.3\nauthor: Tester\ntags:\n  - test\n  - example\nrequires:\n  - requests\ntools:\n  - echo\n---\n\n# Test Skill\n\nThis is the instruction body.\n\n## Steps\n\n1. Do this.\n2. Do that.\n"

func TestCLISkillLoaderParsesFrontmatterAndBody(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "my-skill")
	skWriteFixture(t, dir, skSampleMD)
	sk, err := skLoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if sk.Name != "test-skill" || sk.Version != "1.2.3" || sk.Author == nil || *sk.Author != "Tester" ||
		strings.Join(sk.Tags, ",") != "test,example" || sk.Status != "discovered" || sk.Kind != "native" {
		t.Fatalf("%+v", sk)
	}
	if !strings.HasPrefix(sk.Instructions, "# Test Skill") || !strings.HasSuffix(sk.Instructions, "2. Do that.") {
		t.Fatalf("body %q", sk.Instructions)
	}
	if abs, _ := filepath.Abs(dir); sk.Dir != skResolvePath(abs) {
		t.Errorf("dir %q", sk.Dir)
	}
}

func TestCLISkillLoaderFrontmatterEdgeCases(t *testing.T) {
	dir := t.TempDir()
	md := filepath.Join(dir, "SKILL.md")
	write := func(s string) {
		if err := os.WriteFile(md, []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("# Just markdown\n\nNo frontmatter here.")
	if fm, body, err := skParseSkillMD(md); err != nil || len(fm) != 0 || body != "# Just markdown\n\nNo frontmatter here." {
		t.Errorf("no frontmatter: %v %v %q", fm, err, body)
	}
	write("---\n---\n\nMarkdown body")
	if fm, body, err := skParseSkillMD(md); err != nil || len(fm) != 0 || !strings.Contains(body, "Markdown body") {
		t.Errorf("empty frontmatter: %v %v %q", fm, err, body)
	}
	write("---\n\tbad: [yaml\n---\n\nBody")
	if _, _, err := skParseSkillMD(md); err == nil || !strings.Contains(err.Error(), "Invalid YAML frontmatter") {
		t.Errorf("invalid yaml: %v", err)
	}
	write("---\r\nname: crlf\r\ndescription: Windows line ends\r\n---\r\n\r\nBody\r\n")
	if fm, body, err := skParseSkillMD(md); err != nil || !fm.has("name") || body != "Body" {
		t.Errorf("CRLF: %v %v %q", fm, err, body)
	}
	write("---\ndescription: No name here\n---\n\nBody")
	if _, err := skLoadDir(dir); err == nil || !strings.Contains(err.Error(), "missing required 'name'") {
		t.Errorf("missing name: %v", err)
	}
	write("---\nname: lonely\n---\n\nBody")
	if _, err := skLoadDir(dir); err == nil || !strings.Contains(err.Error(), "missing required 'description'") {
		t.Errorf("missing description: %v", err)
	}
	write("---\nname: v\ndescription: float version\nversion: 1.10\nstatus: Validated\nmetadata: nope\neffort_weight: 0.7\n---\nB")
	sk, err := skLoadDir(dir)
	if err != nil || sk.Version != "1.1" || sk.Status != "validated" {
		t.Fatalf("%v %+v", err, sk)
	}
	if v, _ := sk.Meta.get("effort_weight"); v != 0.7 {
		t.Errorf("promoted meta %v", sk.Meta)
	}
	if _, err := skLoadDir(filepath.Join(dir, "missing")); err == nil || !strings.Contains(err.Error(), "not found") {
		t.Errorf("missing dir: %v", err)
	}
	if _, err := skLoadDir(t.TempDir()); err == nil || !strings.Contains(err.Error(), "No SKILL.md found") {
		t.Errorf("no SKILL.md: %v", err)
	}
}

func TestCLISkillLoaderListsScriptsAndReferences(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "rich-skill")
	skWriteFixture(t, dir, skSampleMD)
	for p, body := range map[string]string{"scripts/run.py": "print('hello')", "scripts/sub/helper.py": "pass", "references/api.md": "# API"} {
		full := filepath.Join(dir, filepath.FromSlash(p))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	sk, err := skLoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join("scripts", "run.py"), filepath.Join("scripts", "sub", "helper.py")}
	if strings.Join(sk.Scripts, "|") != strings.Join(want, "|") || len(sk.References) != 1 || !strings.Contains(sk.References[0], "api.md") {
		t.Fatalf("scripts %v refs %v", sk.Scripts, sk.References)
	}
}

// --- running --------------------------------------------------------------------

func TestCLISkillRunUnknownExitsOne(t *testing.T) {
	code, _, errw := skRun(t, t.TempDir(), "skill", "run", "nope-not-a-skill")
	if code != 1 || !strings.Contains(errw, "Skill not found") {
		t.Fatalf("code %d err %q", code, errw)
	}
}

func TestCLISkillRunRefusesAScriptOutsideTheSkill(t *testing.T) {
	home := t.TempDir()
	skWriteFixture(t, filepath.Join(home, "skills", "deploy"), "---\nname: deploy\ndescription: deploy does a thing\n---\nBody\n")
	for _, s := range []string{"../../etc/passwd", "/etc/passwd", `C:\Windows\x.py`, "scripts/../x.py", `\\server\share\x.py`} {
		code, _, errw := skRun(t, home, "skill", "run", "deploy", "--script", s)
		if code != 1 || !strings.Contains(errw, "Script path escapes skill scripts/: "+s) {
			t.Errorf("%s: code %d err %q", s, code, errw)
		}
	}
	code, _, errw := skRun(t, home, "skill", "run", "deploy", "--script", "missing.py")
	if code != 1 || !strings.Contains(errw, "Script not found: missing.py") {
		t.Errorf("missing: code %d err %q", code, errw)
	}
}

func TestCLISkillScriptJail(t *testing.T) {
	dir := t.TempDir()
	scripts := filepath.Join(dir, "scripts")
	if err := os.MkdirAll(scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := skJailScript(dir, "scripts/run.py")
	if err != nil || got != filepath.Join(skResolvePath(scripts), "run.py") {
		t.Fatalf("%q %v", got, err)
	}
	if got2, _ := skJailScript(dir, `sub\run.py`); got2 != filepath.Join(skResolvePath(scripts), "sub", "run.py") {
		t.Errorf("backslash: %q", got2)
	}
	for _, bad := range []string{"", "  ", "scripts", "..", "a/../../b", "/abs.py", "c:rel.py", "//x/y"} {
		if _, err := skJailScript(dir, bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	outside := filepath.Join(t.TempDir(), "evil.py")
	if err := os.WriteFile(outside, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(scripts, "link.py")); err == nil {
		if _, err := skJailScript(dir, "link.py"); err == nil {
			t.Error("a symlink out of scripts/ was accepted")
		}
	}
}

// fakeRun records the spawn instead of making it.
type skSpawnCall struct {
	argv []string
	cwd  string
	env  map[string]string
	to   time.Duration
}

func skFakeExecutor(code int, timedOut bool, stdout string) (*skExecutor, *[]skSpawnCall) {
	var calls []skSpawnCall
	x := &skExecutor{ctx: context.Background()}
	x.run = func(_ context.Context, argv []string, cwd string, env map[string]string, out, errw *bytes.Buffer, to time.Duration) (int, bool, error) {
		calls = append(calls, skSpawnCall{append([]string(nil), argv...), cwd, env, to})
		out.WriteString(stdout)
		return code, timedOut, nil
	}
	return x, &calls
}

func skFakePython(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(p, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_PYTHON", p)
	return p
}

func TestCLISkillRunScriptUsesTheInterpreterAndScriptFolder(t *testing.T) {
	py := skFakePython(t)
	dir := t.TempDir()
	script := filepath.Join(dir, "scripts", "go.py")
	if err := os.MkdirAll(filepath.Dir(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(script, []byte("print(1)"), 0o644); err != nil {
		t.Fatal(err)
	}
	x, calls := skFakeExecutor(0, false, "a\r\nb\n")
	res := x.runScript(script)
	if !res.success || res.exitCode != 0 || res.stdout != "a\nb\n" {
		t.Fatalf("%+v", res)
	}
	c := (*calls)[0]
	if len(c.argv) != 2 || c.argv[0] != py || c.argv[1] != skResolvePath(script) || c.cwd != filepath.Dir(skResolvePath(script)) || c.to != 300*time.Second {
		t.Fatalf("%+v", c)
	}
	x, _ = skFakeExecutor(1, true, "lost")
	if res := x.runScript(script); res.success || res.err != "Script timed out after 300.0s" || res.exitCode != -1 || res.stdout != "" {
		t.Fatalf("timeout: %+v", res)
	}
	t.Setenv("REMEDY_PYTHON", filepath.Join(t.TempDir(), "remedy"))
	t.Setenv("PATH", t.TempDir())
	if res := x.runScript(script); res.err != "No real Python interpreter for skill scripts (Desktop sidecar is not CPython). Set REMEDY_PYTHON." {
		t.Fatalf("no python: %+v", res)
	}
}

// Python ran skill scripts with an allowlisted environment
// (credentials.broker._SAFE_EXACT): provider keys never reached them.
func TestCLISkillScriptEnvironmentDropsSecrets(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "sk-secret")
	t.Setenv("REMEDY_LOCAL_API_TOKEN", "tok")
	t.Setenv("GH_TOKEN", "ghp")
	t.Setenv("LANG", "C.UTF-8")
	env := skChildEnv()
	for _, k := range []string{"OPENAI_API_KEY", "REMEDY_LOCAL_API_TOKEN", "GH_TOKEN"} {
		if v, ok := env[k]; !ok || v != "" {
			t.Errorf("%s reaches the child: %q (present %v)", k, v, ok)
		}
	}
	for _, k := range []string{"PATH", "LANG"} {
		if _, ok := env[k]; ok {
			t.Errorf("%s overridden", k)
		}
	}
	py := skFakePython(t)
	_ = py
	dir := t.TempDir()
	script := filepath.Join(dir, "s.py")
	if err := os.WriteFile(script, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	x, calls := skFakeExecutor(0, false, "")
	x.runScript(script)
	if v, ok := (*calls)[0].env["OPENAI_API_KEY"]; !ok || v != "" {
		t.Error("runScript passed the parent's API key through")
	}
}

func TestCLISkillRunAllScriptsAndInstructionBlocks(t *testing.T) {
	skFakePython(t)
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "scripts", "a.py"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	x, calls := skFakeExecutor(0, false, "ok")
	order, res := x.runAllScripts([]string{filepath.Join("scripts", "a.py"), filepath.Join("scripts", "gone.py"), filepath.Join("..", "x.py")}, dir)
	if len(order) != 3 || !res[order[0]].success || res[order[1]].err != "Script not found: "+order[1] ||
		res[order[2]].err != "Script path escapes skill directory: "+order[2] || len(*calls) != 1 {
		t.Fatalf("%v %+v", order, res)
	}

	x, calls = skFakeExecutor(0, false, "42")
	defer x.cleanup()
	got := x.runInstructions("intro\n```python\nprint(6*7)\n```\n\n```Bash  \necho hi\n```\n```ruby\nputs 1\n```\n")
	if len(got) != 3 || !got[0].success || !got[1].success || got[2].stdout != "Skipped unsupported language: ruby" || got[2].exitCode != -1 || !got[2].success {
		t.Fatalf("%+v", got)
	}
	sh := (*calls)[1]
	if runtime.GOOS != "windows" && (len(sh.argv) != 3 || sh.argv[1] != "-c" || sh.argv[2] != "echo hi" || sh.to != 120*time.Second || sh.cwd != x.sandbox) {
		t.Fatalf("shell block %+v", sh)
	}
	py := (*calls)[0]
	if filepath.Dir(py.argv[1]) != skResolvePath(x.sandbox) || !strings.HasPrefix(filepath.Base(py.argv[1]), "exec_") {
		t.Fatalf("python block %+v", py)
	}
	if _, err := os.Stat(py.argv[1]); err == nil {
		t.Error("python block temp file left behind")
	}
	x.cleanup()
	if _, err := os.Stat(x.sandbox); err == nil {
		t.Error("sandbox left behind")
	}
}

// A real spawn through tools.RunAttached when a CPython and the Zig core are here.
func TestCLISkillRunEndToEnd(t *testing.T) {
	if len(skPythonCmd()) == 0 {
		t.Skip("no python interpreter")
	}
	home := t.TempDir()
	dir := filepath.Join(home, "skills", "runner")
	skWriteFixture(t, dir, "---\nname: runner\ndescription: runs a script end to end\n---\nBody\n")
	if err := os.MkdirAll(filepath.Join(dir, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "scripts", "go.py"), []byte("import os\nprint('key=' + os.environ.get('OPENAI_API_KEY', ''))\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OPENAI_API_KEY", "sk-secret")
	code, out, errw := skRun(t, home, "skill", "run", "runner")
	if strings.Contains(out, "remedy_core") {
		t.Skipf("native core unavailable: %s", out)
	}
	if code != 0 || !strings.Contains(out, "Script: "+filepath.Join("scripts", "go.py")+"\n  Status: SUCCESS\n  Exit code: 0\n  stdout: key=\n") {
		t.Fatalf("code %d out %q err %q", code, out, errw)
	}
}

// --- test -----------------------------------------------------------------------

func TestCLISkillTestValidatesAndScores(t *testing.T) {
	home := t.TempDir()
	skWriteFixture(t, filepath.Join(home, "skills", "tiny"), "---\nname: tiny\ndescription: short\nversion: not a version\n---\nhi\n")
	code, out, _ := skRun(t, home, "skill", "test", "tiny")
	for _, want := range []string{
		"\nFAIL tiny:\n  Error: Description must be at least 10 characters\n  Warning: Version 'not a version' is not valid SemVer\n",
		"  Warning: Skill instructions are very short; consider expanding\n  Warning: No author specified; consider adding one\n  Warning: No tags specified; tags help with discoverability\n",
		"PASS tiny:\n  Warning: No test files found (looked for test_*.py, *_test.py, tests/)\n",
		"\nPASS tiny:\n\nPASS tiny:\n\nPASS tiny:\n", "\nCompliance Score: 65%\n", // Python prints the same for this skill
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in\n%s", want, out)
		}
	}
	if code != 1 {
		t.Errorf("code %d", code)
	}
	for v, ok := range map[string]bool{"1.0.0": true, "1.1": true, "2.0rc1": true, "1.0.post1.dev2+abc.1": true, "v1": true, " 1.0 ": true, "1.0-": false, "x": false, "": false, "None": false} {
		if skRePEP440.MatchString(v) != ok {
			t.Errorf("PEP 440 %q: want %v", v, ok)
		}
	}
}

// --- learn ------------------------------------------------------------------------

func TestCLILearnReflectRejectsBadJSON(t *testing.T) {
	code, _, errw := skRun(t, t.TempDir(), "learn", "reflect", "t", "--steps", "[1")
	if code != 2 || !strings.HasPrefix(errw, "Invalid --steps JSON: ") {
		t.Fatalf("code %d err %q", code, errw)
	}
	code, _, _ = skRun(t, t.TempDir(), "learn", "reflect", "t", "--steps", `{"a":1}`)
	if code != 2 {
		t.Fatalf("object: code %d", code)
	}
}

func TestCLILearnReflectShortTraceAndGeneratedSkill(t *testing.T) {
	home := t.TempDir()
	code, out, _ := skRun(t, home, "learn", "reflect", "tiny")
	if code != 0 || out != "Trace too short for meaningful reflection.\n" {
		t.Fatalf("code %d out %q", code, out)
	}
	steps := `[{"tool":"file_read","args":{"path":"a","z":1},"result":"ok"},{"tool":"bash_exec","args":{"cmd":"make"},"result":"built"},` +
		`{"tool":"bash_exec","success":false,"error":"boom"},{"tool":"bash_exec","args":{"cmd":"make -B"},"result":"built"},{"tool":"file_write","args":{"path":"b"},"result":"done"}]`
	code, out, errw := skRun(t, home, "learn", "reflect", "deploy the web app", "--steps", steps)
	want := "Generated skill: bash-exec-deploy-the-web-app\n  Version: 0.1.0\n  Tags: auto-generated, learned, medium-confidence, filesystem, probation\nInstructions (preview)\n# deploy the web app\n"
	if code != 0 || !strings.HasPrefix(out, want) {
		t.Fatalf("code %d out %q err %q", code, out, errw)
	}
	md, err := os.ReadFile(filepath.Join(home, "skills", "bash-exec-deploy-the-web-app", "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	for _, w := range []string{"1. **file_read** [SUCCESS]\n   - Argument keys: `path, z`\n   - Result sketch: ok\n", "3. **bash_exec** [FAILED — do not treat as required success]\n   - Error seen: boom\n"} {
		if !strings.Contains(string(md), w) {
			t.Errorf("SKILL.md lacks %q:\n%s", w, md)
		}
	}
	// A second identical trace merges into the skill and bumps its version.
	_, out, _ = skRun(t, home, "learn", "reflect", "deploy the web app", "--steps", steps)
	if !strings.Contains(out, "  Version: 0.1.1\n") {
		t.Fatalf("merge: %q", out)
	}
}

func TestCLILearnStatsHistoryChangelogSync(t *testing.T) {
	home := t.TempDir()
	stats := `{"skills":{"a":{"total_executions":4,"successes":3,"failures":1,"avg_duration_ms":12.6,` +
		`"last_executed":"2024-01-02T03:04:05.000100+00:00","common_errors":{"boom":2,"x":1}},"b":{}}}`
	if err := os.WriteFile(filepath.Join(home, "skill_stats.json"), []byte(stats), 0o644); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		args []string
		want string
	}{
		{[]string{"learn", "stats"}, "a: 3/4 (75%)\nb: 0/0 (50%)\n"},
		{[]string{"learn", "stats", "--skill", "a"}, "Skill Stats\na\nExecutions: 4\nSuccesses: 3\nFailures: 1\nSuccess rate: 75%\nAvg duration: 13ms\nLast executed: 2024-01-02 03:04:05.000100+00:00\n\nCommon Errors:\n  (2x) boom\n  (1x) x\n"},
		{[]string{"learn", "stats", "--skill", "zz"}, "Skill Stats\nzz\nExecutions: 0\nSuccesses: 0\nFailures: 0\nSuccess rate: 50%\nAvg duration: 0ms\nLast executed: None\n"},
		{[]string{"learn", "history"}, "No learning events recorded.\n"},
		{[]string{"learn", "changelog"}, "No refinements recorded.\n"},
		{[]string{"learn", "changelog", "a"}, "No refinements recorded for skill 'a'.\n"},
		{[]string{"learn", "sync"}, "Synced 0 learning events to memory.\n"},
	}
	for _, c := range cases {
		code, out, _ := skRun(t, home, c.args...)
		if code != 0 || out != c.want {
			t.Errorf("%v: code %d\n got %q\nwant %q", c.args, code, out, c.want)
		}
	}
	if err := os.Remove(filepath.Join(home, "skill_stats.json")); err != nil {
		t.Fatal(err)
	}
	if _, out, _ := skRun(t, home, "learn", "stats"); out != "No skill stats recorded.\n" {
		t.Errorf("no stats: %q", out)
	}
	for in, want := range map[string]string{
		"2024-01-02":                "2024-01-02 00:00:00",
		"2024-01-02T03:04:05Z":      "2024-01-02 03:04:05+00:00",
		"2024-01-02 03:04:05-05:30": "2024-01-02 03:04:05-05:30",
		"2024-01-02T03:04:05.5":     "2024-01-02 03:04:05.500000",
		"2024-02-30":                "",
		"yesterday":                 "",
	} {
		if got := skFromISOStr(in); got != want {
			t.Errorf("fromisoformat(%q) = %q, want %q", in, got, want)
		}
	}
}

// --- migrate (test_migration_import.py) ---------------------------------------------

func skMigrateSource(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "hermes", "skills")
	for _, n := range []string{"alpha", "beta"} {
		skWriteFixture(t, filepath.Join(root, n), "---\nname: "+n+"\ndescription: "+n+" skill\n---\n# "+n+"\n")
	}
	return root
}

func TestCLIMigrateMissingSourceIsAnErrorNamingTheInstall(t *testing.T) {
	for label, sub := range map[string]string{"Hermes": "hermes", "OpenClaw": "openclaw"} {
		home := t.TempDir()
		missing := filepath.Join(t.TempDir(), "not-here")
		code, out, errw := skRun(t, home, "migrate", sub, missing)
		if code != 1 || out != label+" migration: 0 imported, 0 skipped\n" || !strings.Contains(errw, label+" skills directory not found") {
			t.Errorf("%s: code %d out %q err %q", sub, code, out, errw)
		}
	}
	stray := filepath.Join(t.TempDir(), "skills")
	if err := os.WriteFile(stray, []byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	if code, _, _ := skRun(t, t.TempDir(), "migrate", "hermes", stray); code != 1 {
		t.Errorf("a file as the source: code %d", code)
	}
}

func TestCLIMigrateCopiesIntoTheHomeWithoutOverwriting(t *testing.T) {
	src, home := skMigrateSource(t), t.TempDir()
	dest := filepath.Join(home, "skills")
	skWriteFixture(t, filepath.Join(dest, "alpha"), "# my edited version\n")
	code, out, _ := skRun(t, home, "migrate", "hermes", src)
	if code != 0 || out != "Hermes migration: 2 imported, 0 skipped\n" {
		t.Fatalf("code %d out %q", code, out)
	}
	if b, _ := os.ReadFile(filepath.Join(dest, "alpha", "SKILL.md")); string(b) != "# my edited version\n" {
		t.Errorf("an existing skill was overwritten: %q", b)
	}
	if !skIsFile(filepath.Join(dest, "beta", "SKILL.md")) {
		t.Error("beta not copied")
	}
	if code, out, _ := skRun(t, home, "migrate", "openclaw", src); code != 0 || out != "OpenClaw migration: 2 imported, 0 skipped\n" {
		t.Errorf("openclaw: code %d out %q", code, out)
	}
}

func TestCLIMigrateNoCopyAndEmptySource(t *testing.T) {
	src, home := skMigrateSource(t), t.TempDir()
	if code, out, _ := skRun(t, home, "migrate", "hermes", src, "--no-copy"); code != 0 || out != "Hermes migration: 2 imported, 0 skipped\n" {
		t.Fatalf("code %d out %q", code, out)
	}
	if entries, _ := os.ReadDir(filepath.Join(home, "skills")); len(entries) != 0 {
		t.Errorf("--no-copy copied %v", entries)
	}
	empty := t.TempDir()
	if code, out, _ := skRun(t, home, "migrate", "hermes", empty); code != 0 || out != "Hermes migration: 0 imported, 0 skipped\n" {
		t.Errorf("empty: code %d out %q", code, out)
	}
}

func TestCLIMigrateAdaptersMapFieldNames(t *testing.T) {
	root := t.TempDir()
	skWriteFixture(t, filepath.Join(root, "h"), "---\nskill_name: hs\ndescription: hermes skill\nhermes_version: 3\n---\nH\n")
	if sk, err := skLoadHermes(filepath.Join(root, "h")); err != nil || sk.Name != "hs" || sk.Version != "3" || sk.Kind != "hermes" {
		t.Errorf("hermes: %v %+v", err, sk)
	}
	oc := filepath.Join(root, "oc")
	if err := os.MkdirAll(oc, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(oc, "skill.yaml"), []byte("title: oc-skill\ndescription: from yaml\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if sk, err := skLoadOpenClaw(oc); err != nil || sk.Name != "oc-skill" || sk.Instructions != "" || sk.Kind != "openclaw" {
		t.Errorf("openclaw yaml: %v %+v", err, sk)
	}
	if err := os.WriteFile(filepath.Join(oc, "skill.md"), []byte("---\ntitle: oc-md\ndescription: from md\n---\nMD body\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if sk, err := skLoadOpenClaw(oc); err != nil || sk.Name != "oc-md" || sk.Instructions != "MD body" {
		t.Errorf("openclaw md: %v %+v", err, sk)
	}
}

func TestCLIMigrateExpandsTheUserHome(t *testing.T) {
	uh := t.TempDir()
	t.Setenv("HOME", uh)
	t.Setenv("USERPROFILE", uh)
	skWriteFixture(t, filepath.Join(uh, "skills", "alpha"), "---\nname: alpha\ndescription: alpha skill\n---\nA\n")
	if code, out, _ := skRun(t, t.TempDir(), "migrate", "hermes", "~/skills", "--no-copy"); code != 0 || out != "Hermes migration: 1 imported, 0 skipped\n" {
		t.Fatalf("code %d out %q", code, out)
	}
}

// shutil.copytree followed symlinks, so importing a folder holding a link to
// the owner's SSH key copied the key into ~/.remedy/skills.
func TestCLIMigrateDoesNotCopyFilesLinkedFromOutsideTheSkill(t *testing.T) {
	root := t.TempDir()
	secret := filepath.Join(root, "id_rsa")
	if err := os.WriteFile(secret, []byte("PRIVATE KEY"), 0o600); err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(root, "hermes")
	skWriteFixture(t, filepath.Join(src, "alpha"), "---\nname: alpha\ndescription: alpha skill\n---\nA\n")
	if err := os.WriteFile(filepath.Join(src, "alpha", "notes.md"), []byte("mine"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secret, filepath.Join(src, "alpha", "key")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := os.Symlink(filepath.Join(src, "alpha", "notes.md"), filepath.Join(src, "alpha", "notes-link.md")); err != nil {
		t.Fatal(err)
	}
	home := t.TempDir()
	if code, out, errw := skRun(t, home, "migrate", "hermes", src); code != 0 {
		t.Fatalf("code %d out %q err %q", code, out, errw)
	}
	dst := filepath.Join(home, "skills", "alpha")
	if _, err := os.Lstat(filepath.Join(dst, "key")); err == nil {
		t.Error("the linked secret was copied into the home")
	}
	if b, err := os.ReadFile(filepath.Join(dst, "notes-link.md")); err != nil || string(b) != "mine" {
		t.Errorf("a link inside the skill was not copied as a file: %q %v", b, err)
	}
	if !skIsFile(filepath.Join(dst, "SKILL.md")) {
		t.Error("SKILL.md not copied")
	}
}

func TestCLISkillBareGroupsNeedASubcommand(t *testing.T) {
	for _, g := range []string{"skill", "learn", "migrate"} {
		if code, _, _ := skRun(t, t.TempDir(), g); code != 2 {
			t.Errorf("%s: code %d", g, code)
		}
	}
}

// A dangerous instruction block is refused before anything is spawned, the
// same guard `remedy exec` uses. The block is harmless if it did run.
func TestCLISkillRunBlocksDangerousShellBlock(t *testing.T) {
	x, calls := skFakeExecutor(0, false, "ok")
	defer x.cleanup()
	got := x.runInstructions("```bash\necho hi | sh\n```\n")
	if len(got) != 1 || got[0].success || !strings.Contains(got[0].err, "Blocked by security policy") {
		t.Fatalf("%+v", got)
	}
	if len(*calls) != 0 {
		t.Fatalf("spawned a blocked block: %+v", *calls)
	}
}
