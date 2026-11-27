package cli

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// sysRun runs one CLI invocation against home with stdin.
func sysRun(t *testing.T, home, stdin string, args ...string) (int, string, string) {
	t.Helper()
	t.Setenv("REMEDY_HOME", "")
	var out, errb bytes.Buffer
	code := Run(append([]string{"--home", home}, args...), &out, &errb, strings.NewReader(stdin), Hooks{}, "0.0.1")
	return code, out.String(), errb.String()
}

func sysReadConfig(t *testing.T, home string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(home, "config.toml"))
	if err != nil {
		t.Fatalf("config not written: %v", err)
	}
	return string(raw)
}

// ---- setup -------------------------------------------------------------------

func TestCLISystemSetupQuickAsksNothing(t *testing.T) {
	home := t.TempDir()
	code, out, _ := sysRun(t, home, "", "setup", "--quick", "--skip-providers")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if strings.Contains(out, "[y/n]") || strings.Contains(out, "(1):") {
		t.Fatalf("quick mode asked a question:\n%s", out)
	}
	cfg := sysReadConfig(t, home)
	for _, want := range []string{`llm_provider = "openai"`, `llm_base_url = "https://api.openai.com/v1"`, `name = "Remedy"`,
		`enabled_channels = ["cli"]`, "skills_dir = []", "setup_completed = true", "[gateway]", "heartbeat_interval = 60", "[execution]", "max_retries = 3"} {
		if !strings.Contains(cfg, want) {
			t.Fatalf("quick config misses %q:\n%s", want, cfg)
		}
	}
	// Quick wins over --skip-providers, and the skip line still prints.
	if !strings.Contains(out, "Quick mode: OpenAI") || !strings.Contains(out, "Skipping provider setup") {
		t.Fatalf("quick+skip lines:\n%s", out)
	}
	if !strings.Contains(out, "Setup Complete") || !strings.Contains(out, filepath.Join(home, "memory.db")) {
		t.Fatalf("final panel:\n%s", out)
	}
}

// sysWizardAnswers walks every section with providers and messaging on.
var sysWizardAnswers = strings.Join([]string{
	"My_Agent", // agent name
	"3",        // persona: verbose
	"y",        // configure provider
	"5",        // xai
	"  xai-PLAINTEXTKEY-0123456789abcdef  ",
	"",                     // model: provider default
	"https://api.x.ai/v1/", // base url with trailing slash
	"y",                    // messaging
	"3, 5,3,9,x",           // telegram, slack (dupes and junk dropped)
	"y", "tg-token-SECRET", // telegram token
	"n",             // no slack token
	"WARNING",       // log level by name
	"0.6",           // threshold
	"n",             // no skill creation
	"y",             // skills
	"", "extra", "", // default "skills", then "extra", then stop
	"y", // save
}, "\n") + "\n"

func TestCLISystemSetupAsksTheSameQuestionsInOrder(t *testing.T) {
	home := t.TempDir()
	code, out, errs := sysRun(t, home, sysWizardAnswers, "setup")
	if code != 0 {
		t.Fatalf("exit %d\n%s\n%s", code, out, errs)
	}
	order := []string{
		"Step 1: Agent Identity", "Agent name (Remedy): ", "Available Personas", "Select persona (1-5) (1): ",
		"Step 2: LLM Provider", "Configure an LLM provider? [y/n] (y): ", "Available LLM Providers", "Select provider (1-11) (1): ",
		"Selected: xAI (Grok)", "API key (or press Enter to skip): ", "  Model name (blank = provider default) (): ",
		"  API base URL (https://api.x.ai/v1): ",
		"Step 3: Messaging Apps", "Configure messaging apps (Telegram, Discord, Slack)? [y/n] (n): ", "Channel Setup", "Channels (1): ",
		"Telegram Bot Token (from @BotFather)", "Do you have a Telegram token? [y/n] (n): ", "  Telegram token: ",
		"Slack Bot Token (from Slack API)", "Do you have a Slack token? [y/n] (n): ",
		"Step 4: Runtime Settings", "  2. INFO [default]", "Log level (INFO): ", "Auto-approve threshold for skill creation (0.8): ",
		"Allow automatic skill creation from traces? [y/n] (y): ",
		"Step 5: Skill Discovery", "Configure skill directories? [y/n] (y): ",
		"Skills directory path (Enter empty to finish) (skills): ", "Skills directory path (Enter empty to finish) (): ",
		"Will scan: skills, extra",
		"Review", "Configuration Summary", "Save this configuration? [y/n] (y): ", "Config written to", "Setup Complete",
	}
	pos := 0
	for _, s := range order {
		i := strings.Index(out[pos:], s)
		if i < 0 {
			t.Fatalf("%q missing or out of order after offset %d:\n%s", s, pos, out)
		}
		pos += i + len(s)
	}
	if strings.Contains(out, "Discord") && strings.Contains(out, "Do you have a Discord token") {
		t.Fatal("asked for an unselected channel's token")
	}
	if strings.Contains(out, "PLAINTEXTKEY") || strings.Contains(out, "tg-token-SECRET") {
		t.Fatal("a secret was echoed back")
	}
	cfg := sysReadConfig(t, home)
	if strings.Contains(cfg, "PLAINTEXTKEY") || strings.Contains(cfg, "tg-token-SECRET") {
		t.Fatalf("config.toml carries a plaintext secret:\n%s", cfg)
	}
	if got := secret.GetProviderSecret(home, "xai"); got != "xai-PLAINTEXTKEY-0123456789abcdef" {
		t.Fatalf("api key not trimmed into the secret store: %q", got)
	}
	if got := secret.GetProviderSecret(home, "ch:telegram:bot_token"); got != "tg-token-SECRET" {
		t.Fatalf("telegram token: %q", got)
	}
	for _, want := range []string{`name = "My_Agent"`, `persona = "verbose"`, `llm_provider = "xai"`, `llm_base_url = "https://api.x.ai/v1"`,
		`enabled_channels = ["telegram", "slack"]`, `log_level = "WARNING"`, "auto_approve_threshold = 0.6", "allow_skill_creation = false",
		`skills_dir = ["skills", "extra"]`, "[slack]", "[telegram]"} {
		if !strings.Contains(cfg, want) {
			t.Fatalf("config misses %q:\n%s", want, cfg)
		}
	}
	if strings.Contains(cfg, "[discord]") {
		t.Fatalf("unselected channel got a table:\n%s", cfg)
	}
}

func TestCLISystemSetupSkippedSectionsLeaveNoProvider(t *testing.T) {
	home := t.TempDir()
	in := "bad name!\n99\n\n\n\n\n" // name, persona (clamped), log, threshold, allow, save
	code, out, _ := sysRun(t, home, in, "setup", "--skip-providers", "--skip-messaging", "--skip-skills")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if !strings.Contains(out, "Name contains special chars; using 'remedy'") {
		t.Fatalf("bad name not replaced:\n%s", out)
	}
	cfg := sysReadConfig(t, home)
	if strings.Contains(cfg, "llm_provider") || strings.Contains(cfg, "llm_api_key =") {
		t.Fatalf("skipped provider written:\n%s", cfg)
	}
	for _, want := range []string{`name = "remedy"`, `persona = "minimal"`, `enabled_channels = ["cli"]`, "skills_dir = []", `log_level = "INFO"`} {
		if !strings.Contains(cfg, want) {
			t.Fatalf("config misses %q:\n%s", want, cfg)
		}
	}
}

func TestCLISystemSetupRefusingToSaveWritesNothing(t *testing.T) {
	home := t.TempDir()
	in := "\n\nn\nn\n\n\n\nn\nn\n" // name, persona, no provider, no messaging, log, thr, allow, no skills, don't save
	code, out, _ := sysRun(t, home, in, "setup")
	if code != 0 || !strings.Contains(out, "Setup cancelled. No changes made.") {
		t.Fatalf("exit %d\n%s", code, out)
	}
	if _, err := os.Stat(filepath.Join(home, "config.toml")); !os.IsNotExist(err) {
		t.Fatal("config written after refusing to save")
	}
}

func TestCLISystemSetupEndedInputWritesNothing(t *testing.T) {
	home := t.TempDir()
	code, _, errs := sysRun(t, home, "Remedy\n", "setup")
	if code != 1 || !strings.Contains(errs, "No changes made") {
		t.Fatalf("exit %d: %s", code, errs)
	}
	if _, err := os.Stat(filepath.Join(home, "config.toml")); !os.IsNotExist(err) {
		t.Fatal("config written when input ended mid-wizard")
	}
}

func TestCLISystemSetupChannelAndOptionParsing(t *testing.T) {
	cases := map[string][]string{
		"":          {"cli"},
		"x,y":       {"cli"},
		"0,6,-1":    {"cli"},
		"2":         {"web"},
		"4, 1,4":    {"discord", "cli"},
		"1,2,3,4,5": {"cli", "web", "telegram", "discord", "slack"},
	}
	for raw, want := range cases {
		if got := sysParseChannels(raw); !reflect.DeepEqual(got, want) {
			t.Fatalf("%q: %v want %v", raw, got, want)
		}
	}
	for in, want := range map[string]string{"1": "DEBUG", "4": "ERROR", "ERROR": "ERROR", "error": "INFO", "9": "INFO", "": "INFO"} {
		if got := sysResolveOption(in, sysLogLevels, "INFO"); got != want {
			t.Fatalf("option %q: %q want %q", in, got, want)
		}
	}
	if sysPyFloat(1) != "1.0" || sysPyFloat(0.8) != "0.8" {
		t.Fatal("python float formatting")
	}
}

// ---- update --------------------------------------------------------------------

func TestCLISystemUpdateVersionOrdering(t *testing.T) {
	parse := map[string][]int{
		"1.2.3": {1, 2, 3}, "v1.2.3": {1, 2, 3}, "1.2": {1, 2, 0}, "2": {2, 0, 0}, "1.2.3.4": {1, 2, 3, 4},
		"1.2.3-rc1": {1, 2, 3}, "1.2.3+b.7": {1, 2, 3}, "  1.2.3 ": {1, 2, 3}, "0.19.0rc1": {0, 19, 0},
		"": {0, 0, 0}, "abc": {0, 0, 0}, "...": {0, 0, 0, 0},
	}
	for in, want := range parse {
		if got := sysParseVersion(in); !reflect.DeepEqual(got, want) {
			t.Fatalf("parse %q: %v want %v", in, got, want)
		}
	}
	for _, c := range []struct {
		a, b string
		want bool
	}{{"1.0.1", "1.0.0", true}, {"1.0.0", "1.0.0", false}, {"0.9.9", "1.0.0", false}, {"1.0.0", "unknown", true},
		{"unknown", "1.0.0", false}, {"0.10.0", "0.9.9", true}, {"0.19.0rc1", "0.19.0", false}, {"...", "abc", true}} {
		if got := sysIsNewer(c.a, c.b); got != c.want {
			t.Fatalf("isNewer(%q,%q)=%v", c.a, c.b, got)
		}
	}
}

func TestCLISystemUpdateReadsPyPIForRemedyAIOnly(t *testing.T) {
	if !strings.HasSuffix(sysPyPIURL, "/pypi/remedy-ai/json") {
		t.Fatalf("wrong PyPI dist: %s", sysPyPIURL)
	}
	var ua string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ua = r.Header.Get("User-Agent")
		switch r.URL.Path {
		case "/ok":
			_, _ = w.Write([]byte(`{"info":{"version":"2.3.4"}}`))
		case "/bad":
			_, _ = w.Write([]byte(`{"info":{"version":7}}`))
		default:
			http.Error(w, "x", 500)
		}
	}))
	defer srv.Close()
	if v := sysFetchLatest(srv.URL + "/ok"); v != "2.3.4" || ua != "Remedy-Updater" {
		t.Fatalf("latest %q ua %q", v, ua)
	}
	if v := sysFetchLatest(srv.URL + "/bad"); v != "" {
		t.Fatalf("malformed answer read as %q", v)
	}
	if v := sysFetchLatest(srv.URL + "/err"); v != "" {
		t.Fatalf("server error read as %q", v)
	}
}

func TestCLISystemUpdateInstallSourceFromDirectURL(t *testing.T) {
	for text, want := range map[string]string{
		"": "pip",
		`{"url":"https://files.pythonhosted.org/x.whl"}`:                                    "pip",
		`{"url":"https://github.com/AhmiDarrow/RemedyAI.git","dir_info":{"editable":true}}`: "git-editable",
		`{"url":"https://github.com/AhmiDarrow/RemedyAI.git"}`:                              "git-folder",
		`{"url":"file:///x","vcs_info":{"vcs":"git"}}`:                                      "git-folder",
		`{"url":"https://x/y.whl","dir_info":"malformed","vcs_info":["git"]}`:               "pip",
		`{ this is not json`: "unknown",
		`[1,2]`:              "unknown",
	} {
		if got := sysClassifyDirectURL(text); got != want {
			t.Fatalf("%q: %s want %s", text, got, want)
		}
	}
	if sysProbeInstallSource("", nil) != "unknown" {
		t.Fatal("no python must read as unknown")
	}
}

func TestCLISystemUpdateFindsOnlyThisProductsRoot(t *testing.T) {
	ours := t.TempDir()
	_ = os.WriteFile(filepath.Join(ours, "pyproject.toml"), []byte("[project]\nname = \"remedy-ai\"\n"), 0o600)
	deep := filepath.Join(ours, "src", "remedy")
	_ = os.MkdirAll(deep, 0o755)
	if got := sysFindProjectRoot(deep); got != ours {
		t.Fatalf("root %q want %q", got, ours)
	}
	stranger := t.TempDir()
	_ = os.WriteFile(filepath.Join(stranger, "pyproject.toml"), []byte("[project]\nname = \"remedy\"\n"), 0o600)
	if got := sysFindProjectRoot(stranger); got == stranger {
		t.Fatal("a stranger's remedy project was accepted")
	}
	gitTree := t.TempDir()
	_ = os.MkdirAll(filepath.Join(gitTree, ".git"), 0o755)
	_ = os.MkdirAll(filepath.Join(gitTree, "src", "remedy", "interfaces"), 0o755)
	if got := sysFindProjectRoot(gitTree); got == gitTree {
		t.Fatal("git tree without updater.py accepted")
	}
	_ = os.WriteFile(filepath.Join(gitTree, "src", "remedy", "interfaces", "updater.py"), []byte(""), 0o600)
	if got := sysFindProjectRoot(gitTree); got != gitTree {
		t.Fatalf("git tree with our layout refused: %q", got)
	}
	if !sysIsRemedyPyproject(`name = 'remedy'`+"\n# Ahmi") || sysIsRemedyPyproject(`name = "remedy"`) {
		t.Fatal("legacy pyproject recognition")
	}
}

type sysFakeUpdate struct {
	u      *sysUpdater
	argvs  [][]string
	result map[string]sysRunResult // keyed by argv[1]
}

func newSysFakeUpdate(installed, latest, source, root string, tty bool) *sysFakeUpdate {
	f := &sysFakeUpdate{result: map[string]sysRunResult{}}
	f.u = &sysUpdater{
		installed:     func() string { return installed },
		latest:        func() string { return latest },
		frozen:        func() bool { return false },
		installSource: func() string { return source },
		projectRoot:   func() string { return root },
		git:           func() string { return "git" },
		isTTY:         func() bool { return tty },
		python:        func() string { return "python" },
		configExists:  func() bool { return true },
	}
	f.u.run = func(argv []string, cwd string, _ time.Duration) sysRunResult {
		f.argvs = append(f.argvs, argv)
		key := strings.Join(argv[1:], " ")
		if r, ok := f.result[key]; ok {
			return r
		}
		return sysRunResult{}
	}
	return f
}

func (f *sysFakeUpdate) installs() []string {
	var out []string
	for _, a := range f.argvs {
		j := strings.Join(a, " ")
		if strings.Contains(j, "pip install") || strings.HasSuffix(j, " pull") {
			out = append(out, j)
		}
	}
	return out
}

func sysUpdateCtx(stdin string) (*Ctx, *bytes.Buffer, *bytes.Buffer) {
	var out, errb bytes.Buffer
	return &Ctx{Out: &out, Err: &errb, In: strings.NewReader(stdin)}, &out, &errb
}

func TestCLISystemUpdateCheckOnlyNeverInstalls(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "2.0.0", "pip", "", true)
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, true); code != 0 || len(f.installs()) != 0 {
		t.Fatalf("exit %d installs %v", code, f.installs())
	}
	if !strings.Contains(out.String(), "Update available: 1.0.0 -> 2.0.0") || !strings.Contains(out.String(), "without --check") {
		t.Fatal(out.String())
	}
}

func TestCLISystemUpdateOfflineExitsOne(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "", "pip", "", true)
	c, out, errs := sysUpdateCtx("")
	if code := f.u.runUpdate(c, false); code != 1 {
		t.Fatalf("exit %d", code)
	}
	if strings.Contains(strings.ToLower(out.String()), "up to date") || !strings.Contains(errs.String(), "Could not reach PyPI") {
		t.Fatalf("offline claimed current:\n%s\n%s", out, errs)
	}
}

func TestCLISystemUpdateUpToDateAndNewerLocalBuild(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "1.0.0", "pip", "", true)
	c, out, _ := sysUpdateCtx("")
	if code := f.u.runUpdate(c, false); code != 0 || !strings.Contains(out.String(), "Remedy is up to date.") {
		t.Fatalf("%d %s", code, out)
	}
	f = newSysFakeUpdate("2.0.0", "1.0.0", "pip", "", true)
	c, out, _ = sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 || len(f.installs()) != 0 || !strings.Contains(out.String(), "is newer or different") {
		t.Fatalf("downgraded: %d %v %s", code, f.installs(), out)
	}
}

func TestCLISystemUpdateWithoutATTYDeclines(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "2.0.0", "pip", "", false)
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 || len(f.installs()) != 0 || !strings.Contains(out.String(), "Non-interactive") {
		t.Fatalf("%d %v %s", code, f.installs(), out)
	}
	// Answering no, or giving no answer, cancels too.
	for _, in := range []string{"n\n", "", "maybe\n"} {
		f = newSysFakeUpdate("1.0.0", "2.0.0", "pip", "", true)
		c, out, _ = sysUpdateCtx(in)
		if code := f.u.runUpdate(c, false); code != 0 || len(f.installs()) != 0 || !strings.Contains(out.String(), "Update cancelled.") {
			t.Fatalf("%q: %d %v %s", in, code, f.installs(), out)
		}
	}
}

func TestCLISystemUpdatePipUpgradeTargetsRemedyAIAndFailureExitsOne(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "2.0.0", "pip", "", true)
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 {
		t.Fatalf("exit %d %s", code, out)
	}
	if got := f.installs(); len(got) != 1 || got[0] != "python -m pip install --upgrade remedy-ai" {
		t.Fatalf("installs %v", got)
	}
	if !strings.Contains(out.String(), "Update complete!") || !strings.Contains(out.String(), "OK  Config valid") {
		t.Fatal(out.String())
	}
	f = newSysFakeUpdate("1.0.0", "2.0.0", "unknown", "", true)
	f.result["-m pip install --upgrade remedy-ai"] = sysRunResult{code: 1, stderr: "boom"}
	c, out, _ = sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 1 {
		t.Fatalf("failed apply exit %d", code)
	}
	if !strings.Contains(out.String(), "Update failed") || !strings.Contains(out.String(), "AhmiDarrow/RemedyAI") {
		t.Fatal(out.String())
	}
}

func TestCLISystemUpdateGitInstallPullsAndFallsBackOnRefs(t *testing.T) {
	root := t.TempDir()
	f := newSysFakeUpdate("1.0.0", "", "git-editable", root, true)
	f.result["rev-list --count HEAD..@{upstream} --"] = sysRunResult{code: 128}
	f.result["rev-list --count HEAD..origin/master --"] = sysRunResult{code: 0, stdout: "fatal"}
	f.result["rev-list --count HEAD..origin/main --"] = sysRunResult{code: 0, stdout: "3\n"}
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 {
		t.Fatalf("exit %d %s", code, out)
	}
	if !strings.Contains(out.String(), "Git behind by: 3 commits") {
		t.Fatal(out.String())
	}
	want := []string{"git pull", "python -m pip install -e ."}
	if got := f.installs(); !reflect.DeepEqual(got, want) {
		t.Fatalf("installs %v", got)
	}
	// A git source without a located root upgrades via pip.
	f = newSysFakeUpdate("1.0.0", "2.0.0", "git-folder", "", true)
	c, _, _ = sysUpdateCtx("y\n")
	f.u.runUpdate(c, false)
	if got := f.installs(); len(got) != 1 || !strings.Contains(got[0], "--upgrade remedy-ai") {
		t.Fatalf("installs %v", got)
	}
}

func TestCLISystemUpdateDirtyCheckoutAborts(t *testing.T) {
	root := t.TempDir()
	_ = os.MkdirAll(filepath.Join(root, ".git"), 0o755)
	_ = os.WriteFile(filepath.Join(root, "pyproject.toml"), []byte("name = \"remedy-ai\"\n"), 0o600)
	f := newSysFakeUpdate("1.0.0", "2.0.0", "git-editable", root, true)
	f.result["-C "+root+" status --porcelain"] = sysRunResult{stdout: " M src/x.py\n"}
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 1 || len(f.installs()) != 0 || !strings.Contains(out.String(), "dirty") {
		t.Fatalf("%d %v %s", code, f.installs(), out)
	}
	// A failing status check does not block the pull.
	f = newSysFakeUpdate("1.0.0", "2.0.0", "git-editable", root, true)
	f.result["-C "+root+" status --porcelain"] = sysRunResult{code: 128}
	c, _, _ = sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 || len(f.installs()) != 2 {
		t.Fatalf("%d %v", code, f.installs())
	}
}

func TestCLISystemUpdateFrozenBuildOnlyReports(t *testing.T) {
	f := newSysFakeUpdate("1.0.0", "2.0.0", "pip", "", true)
	f.u.frozen = func() bool { return true }
	c, out, _ := sysUpdateCtx("y\n")
	if code := f.u.runUpdate(c, false); code != 0 || len(f.argvs) != 0 || !strings.Contains(out.String(), "About Remedy -> Check for updates") {
		t.Fatalf("%d %v %s", code, f.argvs, out)
	}
}

// ---- uninstall ----------------------------------------------------------------

// sysUninstallFakes replaces every outside effect and records removals.
type sysUninstallFakes struct {
	removed []string
	pipArgs [][]string
	killed  []int
	visions []string
	pipFail bool
}

func installSysUninstallFakes(t *testing.T, python string) *sysUninstallFakes {
	t.Helper()
	f := &sysUninstallFakes{}
	oldRemove, oldPip, oldMatch, oldKill, oldVision, oldUH := sysRemoveAll, sysPipRun, sysMatchingProcesses, sysKillTree, sysWipeVisionData, sysUserHome
	t.Cleanup(func() {
		sysRemoveAll, sysPipRun, sysMatchingProcesses, sysKillTree, sysWipeVisionData, sysUserHome = oldRemove, oldPip, oldMatch, oldKill, oldVision, oldUH
	})
	t.Setenv("REMEDY_CLI_PYTHON", python)
	sysRemoveAll = func(p string) error {
		f.removed = append(f.removed, p)
		return os.RemoveAll(p)
	}
	sysPipRun = func(args []string, _ time.Duration) (string, string, int, error) {
		f.pipArgs = append(f.pipArgs, args)
		if f.pipFail {
			return "", "ERROR: permission denied", 1, nil
		}
		return "Successfully uninstalled remedy-ai", "", 0, nil
	}
	sysMatchingProcesses = func(string) [][2]string { return [][2]string{{"4242", "/opt/llama-server -m x.gguf"}} }
	sysKillTree = func(pid int) error { f.killed = append(f.killed, pid); return nil }
	sysWipeVisionData = func(root string) (map[string]any, error) {
		f.visions = append(f.visions, root)
		return nil, nil
	}
	sysUserHome = func() (string, error) { return "/nonexistent-user-home", nil }
	return f
}

// sysPopulatedHome makes <tmp>/.remedy with every wipe target plus data that
// must survive, and a sentinel outside the home.
func sysPopulatedHome(t *testing.T) (home, outside string) {
	t.Helper()
	base := t.TempDir()
	home = filepath.Join(base, ".remedy")
	for _, d := range []string{"auth", "vision/models", "llama.cpp", "skills/x", "sessions", "plans"} {
		_ = os.MkdirAll(filepath.Join(home, d), 0o700)
	}
	for _, f := range []string{"config.toml", "config.yaml", "config.yml", "desktop.json", "comfyui.json", "auth/provider_keys.json",
		"vision/models/m.gguf", "llama-server", "skills/x/SKILL.md", "skill_stats.json", "memory.db", "sessions/s.json", "plans/p.json"} {
		_ = os.WriteFile(filepath.Join(home, f), []byte("x"), 0o600)
	}
	outside = filepath.Join(base, "outside.txt")
	_ = os.WriteFile(outside, []byte("keep"), 0o600)
	return home, outside
}

func TestCLISystemUninstallNeverTargetsAPathOutsideTheAllowedSet(t *testing.T) {
	allowed := map[string]bool{}
	for n := range sysWipeNames {
		allowed[n] = true
	}
	for _, flags := range [][]string{{"--config"}, {"--skills"}, {"--config", "--skills"}, {"--purge"}, {}} {
		t.Run(strings.Join(flags, "+"), func(t *testing.T) {
			f := installSysUninstallFakes(t, "python")
			home, outside := sysPopulatedHome(t)
			code, out, errs := sysRun(t, home, "y\n", append([]string{"uninstall"}, flags...)...)
			if code != 0 {
				t.Fatalf("exit %d\n%s\n%s", code, out, errs)
			}
			purge := len(flags) == 1 && flags[0] == "--purge"
			for _, p := range f.removed {
				if purge && p == home {
					continue
				}
				if filepath.Dir(p) != home || !allowed[filepath.Base(p)] {
					t.Fatalf("removal targeted %s, outside the allowed set under %s", p, home)
				}
			}
			for _, v := range f.visions {
				if v != home {
					t.Fatalf("vision wipe pointed at %s", v)
				}
			}
			if _, err := os.Stat(outside); err != nil {
				t.Fatal("a file outside the home was removed")
			}
			if !purge {
				for _, keep := range []string{"memory.db", "sessions/s.json", "plans/p.json"} {
					if _, err := os.Stat(filepath.Join(home, keep)); err != nil {
						t.Fatalf("%s removed without --purge", keep)
					}
				}
			}
			sort.Strings(f.removed)
			switch {
			case purge:
				if _, err := os.Stat(home); !os.IsNotExist(err) {
					t.Fatal("purge left the home")
				}
			case len(flags) == 0:
				if len(f.removed) != 0 {
					t.Fatalf("plain uninstall removed data: %v", f.removed)
				}
			}
		})
	}
}

func TestCLISystemUninstallSkillsWipeIsSelective(t *testing.T) {
	installSysUninstallFakes(t, "python")
	home, _ := sysPopulatedHome(t)
	if code, out, _ := sysRun(t, home, "y\n", "uninstall", "--skills"); code != 0 {
		t.Fatal(out)
	}
	for _, gone := range []string{"skills", "skill_stats.json"} {
		if _, err := os.Stat(filepath.Join(home, gone)); !os.IsNotExist(err) {
			t.Fatalf("%s survived", gone)
		}
	}
	for _, kept := range []string{"memory.db", "config.toml", "vision"} {
		if _, err := os.Stat(filepath.Join(home, kept)); err != nil {
			t.Fatalf("%s removed by a skills wipe", kept)
		}
	}
}

func TestCLISystemUninstallConfigWipeAlsoStopsAndRemovesVision(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	home, _ := sysPopulatedHome(t)
	if code, out, _ := sysRun(t, home, "y\n", "uninstall", "--config"); code != 0 {
		t.Fatal(out)
	}
	for _, gone := range []string{"config.toml", "desktop.json", "auth", "vision", "llama-server", "llama.cpp"} {
		if _, err := os.Stat(filepath.Join(home, gone)); !os.IsNotExist(err) {
			t.Fatalf("%s survived a config wipe", gone)
		}
	}
	if _, err := os.Stat(filepath.Join(home, "memory.db")); err != nil {
		t.Fatal("memory removed by a config wipe")
	}
	if !reflect.DeepEqual(f.killed, []int{4242}) {
		t.Fatalf("llama-server not stopped through kill-tree: %v", f.killed)
	}
}

func TestCLISystemUninstallSymlinkInsideHomeIsNotFollowed(t *testing.T) {
	installSysUninstallFakes(t, "python")
	base := t.TempDir()
	home := filepath.Join(base, ".remedy")
	_ = os.MkdirAll(home, 0o700)
	victim := filepath.Join(base, "victim")
	_ = os.MkdirAll(victim, 0o700)
	_ = os.WriteFile(filepath.Join(victim, "precious.txt"), []byte("x"), 0o600)
	if err := os.Symlink(victim, filepath.Join(home, "skills")); err != nil {
		t.Skip("symlinks unavailable")
	}
	if code, out, _ := sysRun(t, home, "y\n", "uninstall", "--skills"); code != 0 {
		t.Fatal(out)
	}
	if _, err := os.Stat(filepath.Join(victim, "precious.txt")); err != nil {
		t.Fatal("the skills symlink was followed out of the home")
	}
	if _, err := os.Lstat(filepath.Join(home, "skills")); !os.IsNotExist(err) {
		t.Fatal("the link itself should be removed")
	}
}

func TestCLISystemUninstallRefusesAHomeThatIsNotARemedyDir(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	base := t.TempDir()
	victim := filepath.Join(base, "Documents")
	_ = os.MkdirAll(filepath.Join(victim, "skills"), 0o700)
	// A .remedy symlink to another tree resolves to that tree, and is refused.
	link := filepath.Join(base, ".remedy")
	if err := os.Symlink(victim, link); err != nil {
		t.Skip("symlinks unavailable")
	}
	for _, home := range []string{victim, link} {
		for _, flag := range []string{"--skills", "--purge"} {
			code, _, _ := sysRun(t, home, "y\n", "uninstall", flag)
			if code != 1 {
				t.Fatalf("%s %s: exit %d", home, flag, code)
			}
		}
	}
	if len(f.removed) != 0 {
		t.Fatalf("removed under an unsafe home: %v", f.removed)
	}
	if _, err := os.Stat(filepath.Join(victim, "skills")); err != nil {
		t.Fatal("victim tree damaged")
	}
}

func TestCLISystemUninstallSafeRootRules(t *testing.T) {
	old := sysUserHome
	t.Cleanup(func() { sysUserHome = old })
	base := t.TempDir()
	good := filepath.Join(base, ".remedy")
	_ = os.MkdirAll(good, 0o700)
	sysUserHome = func() (string, error) { return "/nonexistent", nil }
	if r, err := sysSafeWipeRoot(good); err != nil || r != good {
		t.Fatalf("good root refused: %v %q", err, r)
	}
	for _, bad := range []string{filepath.Join(base, "not-remedy"), "/.remedy", filepath.Join(base, "Program Files", ".remedy"), filepath.Join(base, "Windows", "x", ".remedy")} {
		if _, err := sysSafeWipeRoot(bad); err == nil {
			t.Fatalf("%s accepted", bad)
		}
	}
	sysUserHome = func() (string, error) { return good, nil }
	if _, err := sysSafeWipeRoot(good); err == nil || !strings.Contains(err.Error(), "user home") {
		t.Fatalf("user home accepted: %v", err)
	}
	// The guard itself: nothing but root (on purge) or a fixed name in root.
	for _, p := range []string{filepath.Join(good, "memory.db"), filepath.Join(good, "skills", "x"), filepath.Join(good, "..", "x"), base, good} {
		if err := sysWipeRemove(good, p, false); err == nil {
			t.Fatalf("guard let %s through", p)
		}
	}
}

func TestCLISystemUninstallDryRunTouchesNothing(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	home, _ := sysPopulatedHome(t)
	code, out, _ := sysRun(t, home, "y\n", "uninstall", "--purge", "--dry-run")
	if code != 0 || len(f.removed) != 0 || len(f.pipArgs) != 0 || len(f.killed) != 0 {
		t.Fatalf("dry run acted: %d %v %v %v", code, f.removed, f.pipArgs, f.killed)
	}
	if !strings.Contains(out, "Dry run complete. No changes made.") || !strings.Contains(out, "data file - memory.db") || !strings.Contains(out, "(full wipe)") {
		t.Fatal(out)
	}
	if _, err := os.Stat(filepath.Join(home, "memory.db")); err != nil {
		t.Fatal("dry run removed data")
	}
}

func TestCLISystemUninstallPipTargetsOnlyRemedyAI(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	home, _ := sysPopulatedHome(t)
	if code, out, _ := sysRun(t, home, "y\n", "uninstall"); code != 0 || !strings.Contains(out, "Uninstall complete.") {
		t.Fatalf("%d %s", code, out)
	}
	if !reflect.DeepEqual(f.pipArgs, [][]string{{"uninstall", "-y", "remedy-ai"}}) {
		t.Fatalf("pip args %v", f.pipArgs)
	}
}

func TestCLISystemUninstallPipFailureExitsOne(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	f.pipFail = true
	home, _ := sysPopulatedHome(t)
	code, out, errs := sysRun(t, home, "y\n", "uninstall")
	if code != 1 || strings.Contains(strings.ToLower(out), "complete") || !strings.Contains(errs, "finished with errors") {
		t.Fatalf("%d\n%s\n%s", code, out, errs)
	}
}

func TestCLISystemUninstallDefaultsToNoAndFrozenSkipsPip(t *testing.T) {
	f := installSysUninstallFakes(t, "python")
	home, _ := sysPopulatedHome(t)
	for _, in := range []string{"\n", ""} {
		code, out, _ := sysRun(t, home, in, "uninstall", "--purge")
		if code != 0 || !strings.Contains(out, "Uninstall cancelled.") || len(f.removed) != 0 || len(f.pipArgs) != 0 {
			t.Fatalf("%q: %d %v %s", in, code, f.removed, out)
		}
	}
	t.Setenv("REMEDY_CLI_PYTHON", "")
	code, out, _ := sysRun(t, home, "y\n", "uninstall", "--skills")
	if code != 0 || len(f.pipArgs) != 0 || !strings.Contains(out, "this clears data only") {
		t.Fatalf("frozen: %d %v %s", code, f.pipArgs, out)
	}
}

// ---- mcp / computer wiring -----------------------------------------------------

func TestCLISystemMCPCommandWiring(t *testing.T) {
	home := t.TempDir()
	in := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}` + "\n"
	code, out, errs := sysRun(t, home, in, "mcp", "serve")
	if code != 0 || !strings.Contains(out, `"protocolVersion":"2024-11-05"`) || errs != "" {
		t.Fatalf("%d %q %q", code, out, errs)
	}
	if code, _, errs := sysRun(t, home, "", "mcp", "serve", "--http", "--port", "7400"); code != 1 || !strings.Contains(errs, "7400 is reserved") {
		t.Fatalf("7400: %d %s", code, errs)
	}
}

type sysNoActions struct{}

func (sysNoActions) OpenURL(string) (map[string]any, error)                { return nil, nil }
func (sysNoActions) Screenshot(string) (map[string]any, error)             { return nil, nil }
func (sysNoActions) DesktopSnapshot(int, string) ([]map[string]any, error) { return nil, nil }
func (sysNoActions) Click(int, int, string, int) error                     { return nil }
func (sysNoActions) TypeText(string) error                                 { return nil }
func (sysNoActions) PressKey(string) error                                 { return nil }

func TestCLISystemComputerStatusAndHost(t *testing.T) {
	oldNew, oldHost, oldWait := sysNewComputerActions, sysComputerHost, sysWaitInterrupt
	t.Cleanup(func() { sysNewComputerActions, sysComputerHost, sysWaitInterrupt = oldNew, oldHost, oldWait })
	sysNewComputerActions = func() httpapi.ComputerActions { return sysNoActions{} }
	sysComputerHost = nil
	home := t.TempDir()
	code, out, _ := sysRun(t, home, "", "computer", "status")
	if code != 0 || !strings.Contains(out, "host_connected:  False") || !strings.Contains(out, "CLI host:        stopped") {
		t.Fatalf("%d %s", code, out)
	}
	sysComputerHost = nil
	sysWaitInterrupt = func(*httpapi.CLIComputerHost) {}
	code, out, _ = sysRun(t, home, "", "computer", "host", "run")
	if code != 0 || !strings.Contains(out, "CLI computer host started. host_connected=True") ||
		!strings.Contains(out, "Foreground host running") {
		t.Fatalf("%d %s", code, out)
	}
	if sysComputerHost.Running() {
		t.Fatal("run left the host running")
	}
	code, out, _ = sysRun(t, home, "", "computer", "host", "stop")
	if code != 0 || !strings.Contains(out, "CLI computer host stopped.") {
		t.Fatalf("%d %s", code, out)
	}
}
