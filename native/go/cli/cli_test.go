package cli

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
)

// runCLI runs the CLI with a throwaway home and captures both streams.
func runCLI(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	t.Setenv("REMEDY_HOME", "")
	var out, errb strings.Builder
	code := Run(args, &out, &errb, strings.NewReader(""), Hooks{}, "9.9.9")
	return code, out.String(), errb.String()
}

func TestCLINoCommandExits2(t *testing.T) {
	code, out, _ := runCLI(t, "--home", t.TempDir())
	if code != 2 || !strings.Contains(out, "usage: remedy") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIHelpAndVersion(t *testing.T) {
	if code, out, _ := runCLI(t, "--help"); code != 0 || !strings.Contains(out, "remedy serve") {
		t.Fatalf("--help: %d %q", code, out)
	}
	if code, out, _ := runCLI(t, "--version"); code != 0 || strings.TrimSpace(out) != "remedy 9.9.9" {
		t.Fatalf("--version: %d %q", code, out)
	}
}

func TestCLIIsCLI(t *testing.T) {
	for _, a := range [][]string{{"memory", "list"}, {"--home", "x", "tool", "list"}, {"--version"}, {"help"}, {"-h"}} {
		if !IsCLI(a) {
			t.Errorf("IsCLI(%q) = false", a)
		}
	}
	for _, a := range [][]string{{}, {"--probe"}, {"--serve"}, {"--listen", "127.0.0.1:0"}, {"--smoke-fixture", "--serve"}} {
		if IsCLI(a) {
			t.Errorf("IsCLI(%q) = true; runtime flags must reach the runtime", a)
		}
	}
}

func TestCLIParserErrors(t *testing.T) {
	cases := []struct {
		args []string
		want string
	}{
		{[]string{"nope"}, "invalid choice"},
		{[]string{"mcp"}, "the following arguments are required: mcp_cmd"},
		{[]string{"tool", "run"}, "required"},
		{[]string{"memory", "list", "--limit", "abc"}, "invalid int value"},
		{[]string{"desktop", "bogus"}, "invalid choice"},
	}
	for _, tc := range cases {
		code, _, errs := runCLI(t, append([]string{"--home", t.TempDir()}, tc.args...)...)
		if code != 2 || !strings.Contains(errs, tc.want) || !strings.Contains(errs, "usage: remedy") {
			t.Errorf("%q: code=%d stderr=%q", tc.args, code, errs)
		}
	}
}

func TestCLIParserFlagsAndRemainder(t *testing.T) {
	p, err := Parse(Root(), []string{"exec", "--timeout", "3", "--", "python", "-c", "print(1)"})
	if err != nil {
		t.Fatal(err)
	}
	if got := p.List("cmdline"); strings.Join(got, " ") != "-- python -c print(1)" {
		t.Fatalf("cmdline %q", got)
	}
	if p.Float("timeout") != 3 {
		t.Fatalf("timeout %v", p.Float("timeout"))
	}
	p, err = Parse(Root(), []string{"memory", "add", "T", "C", "--tags=a,b", "--importance", "0.9"})
	if err != nil {
		t.Fatal(err)
	}
	if p.Str("tags") != "a,b" || p.Float("importance") != 0.9 || p.Str("type") != "note" {
		t.Fatalf("flags: tags=%q imp=%v type=%q", p.Str("tags"), p.Float("importance"), p.Str("type"))
	}
	p, err = Parse(Root(), []string{"--home", "/tmp/h", "tool", "list"})
	if err != nil || p.Str("home") != "/tmp/h" {
		t.Fatalf("root flag: %v %q", err, p.Str("home"))
	}
}

func TestCLIHomeJail(t *testing.T) {
	t.Setenv("REMEDY_HOME", "")
	ok := filepath.Join(t.TempDir(), ".remedy")
	got, err := ResolveCLIHome(ok, true)
	if err != nil {
		t.Fatal(err)
	}
	if st, err := os.Stat(got); err != nil || !st.IsDir() || filepath.Base(got) != ".remedy" {
		t.Fatalf("home not created: %v", err)
	}
	if os.Getenv("REMEDY_HOME") != got {
		t.Fatalf("REMEDY_HOME not published: %q", os.Getenv("REMEDY_HOME"))
	}
	f := filepath.Join(t.TempDir(), "not-a-dir")
	if err := os.WriteFile(f, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	refuse := []string{`C:\`, `C:\Windows\Temp\remedy-home`, `C:\Program Files\remedy`, f}
	if runtime.GOOS != "windows" {
		refuse = append(refuse, "/", "/etc", "/usr/local/remedy", "/proc/self")
	}
	for _, raw := range refuse {
		_, err := ResolveCLIHome(raw, false)
		var uh *UnsafeHomeError
		if !errors.As(err, &uh) {
			t.Errorf("accepted unsafe home %q (err=%v)", raw, err)
		}
	}
	code, _, errs := runCLI(t, "--home", `C:\Windows`, "config", "path")
	if code != 2 || !strings.Contains(errs, "refuses system path") {
		t.Fatalf("unsafe home via Run: %d %q", code, errs)
	}
}

func TestCLIServeRefusesNonLoopback(t *testing.T) {
	served := false
	var out, errb strings.Builder
	t.Setenv("REMEDY_HOME", "")
	for _, host := range []string{"0.0.0.0", "::", "*", "192.168.1.10", "10.0.0.5"} {
		code := Run([]string{"--home", t.TempDir(), "serve", "--host", host}, &out, &errb, nil,
			Hooks{Serve: func(string, string, func(string)) int { served = true; return 0 }}, "x")
		if code != 2 || served {
			t.Fatalf("host %s: code=%d served=%v", host, code, served)
		}
	}
	for _, host := range []string{"127.0.0.1", "localhost", "::1", "[::1]"} {
		var gotAddr string
		code := Run([]string{"--home", t.TempDir(), "serve", "--host", host, "--port", "7411"}, &out, &errb, nil,
			Hooks{Serve: func(addr, _ string, _ func(string)) int { gotAddr = addr; return 0 }}, "x")
		if code != 0 || !strings.HasSuffix(gotAddr, ":7411") {
			t.Fatalf("host %s: code=%d addr=%q", host, code, gotAddr)
		}
	}
}

func TestCLIToolCommandsTalkToRuntime(t *testing.T) {
	var calls []string
	var body map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/tools":
			_, _ = w.Write([]byte(`{"count":1,"tools":[{"id":"runtime.probe","runtime":"go","risk":"read_only","description":"Emit the native runtime probe record"}]}`))
		case "/api/tools/invoke":
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body["id"] == "fail.tool" {
				w.WriteHeader(503)
				_, _ = w.Write([]byte(`{"detail":"down"}`))
				return
			}
			_, _ = w.Write([]byte(`{"ok":true,"id":"runtime.probe","output":{"status":"ready"}}`))
		}
	}))
	defer srv.Close()
	t.Setenv("REMEDY_API", srv.URL)
	t.Setenv("REMEDY_API_AUTH", "0")

	code, out, _ := runCLI(t, "--home", t.TempDir(), "tool", "list")
	if code != 0 || !strings.Contains(out, "runtime.probe") || calls[0] != "GET /api/tools" {
		t.Fatalf("list: %d %q %v", code, out, calls)
	}
	code, out, _ = runCLI(t, "--home", t.TempDir(), "tool", "run", "runtime.probe")
	if code != 0 || !strings.Contains(out, "Success") || !strings.Contains(out, "ready") {
		t.Fatalf("run: %d %q", code, out)
	}
	if body["id"] != "runtime.probe" || body["input"] == nil {
		t.Fatalf("invoke body %v", body)
	}
	if code, _, _ = runCLI(t, "--home", t.TempDir(), "tool", "run", "fail.tool"); code != 1 {
		t.Fatalf("failed tool exit %d, want 1", code)
	}
	if code, _, _ = runCLI(t, "--home", t.TempDir(), "tool", "run", "x", "--args", "[1]"); code != 2 {
		t.Fatalf("non-object --args exit %d, want 2", code)
	}

	t.Setenv("REMEDY_API", "http://127.0.0.1:9")
	code, out, _ = runCLI(t, "--home", t.TempDir(), "tool", "list")
	if code != 2 || !strings.Contains(out, "remedy serve") {
		t.Fatalf("unreachable: %d %q", code, out)
	}
}

func TestCLIExecMissingCommandExits2(t *testing.T) {
	if code, _, errs := runCLI(t, "--home", t.TempDir(), "exec"); code != 2 || !strings.Contains(errs, "No command") {
		t.Fatalf("%d %q", code, errs)
	}
	if code, _, _ := runCLI(t, "--home", t.TempDir(), "exec", "--"); code != 2 {
		t.Fatalf("bare -- exit %d", code)
	}
}

func TestCLIChatRetired(t *testing.T) {
	if code, _, errs := runCLI(t, "--home", t.TempDir(), "chat"); code != 2 || !strings.Contains(errs, "remedy serve") {
		t.Fatalf("%d %q", code, errs)
	}
}

func TestCLIGatewayChannelsAndStatus(t *testing.T) {
	code, out, _ := runCLI(t, "--home", t.TempDir(), "gateway", "channels")
	if code != 0 || !strings.Contains(out, "telegram") || !strings.Contains(out, "Internal channels:") {
		t.Fatalf("channels: %d %q", code, out)
	}
	home := t.TempDir()
	code, out, _ = runCLI(t, "--home", home, "gateway", "status")
	if code != 0 || !strings.Contains(out, "db_exists") || !strings.Contains(out, "False") {
		t.Fatalf("status: %d %q", code, out)
	}
	if _, err := os.Stat(filepath.Join(home, "memory.db")); err == nil {
		t.Fatal("gateway status created memory.db")
	}
	code, _, errs := runCLI(t, "--home", t.TempDir(), "gateway", "start", "--telegram-token", "x")
	if code != 1 || !strings.Contains(errs, "process lists") {
		t.Fatalf("start: %d %q", code, errs)
	}
}

func TestCLIDesktop(t *testing.T) {
	goos = "linux"
	t.Cleanup(func() { goos = runtime.GOOS })
	if code, out, _ := runCLI(t, "--home", t.TempDir(), "desktop", "launch"); code != 1 || !strings.Contains(out, "Windows-only") {
		t.Fatalf("launch: %d %q", code, out)
	}
	dialRuntime = func() error { return nil }
	t.Cleanup(func() { dialRuntime = func() error { return errors.New("x") } })
	if code, out, _ := runCLI(t, "--home", t.TempDir(), "desktop", "status"); code != 0 || !strings.Contains(out, "Online") {
		t.Fatalf("status: %d %q", code, out)
	}

	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "desktop"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "desktop", "package.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_DEV_ROOT", root)
	var ran [][]string
	oldRun, oldNpm := runChild, lookNpm
	t.Cleanup(func() { runChild, lookNpm = oldRun, oldNpm })
	lookNpm = func() string { return "/usr/bin/npm" }
	runChild = func(argv []string, cwd string, _ *Ctx) int {
		if cwd != filepath.Join(root, "desktop") {
			t.Errorf("cwd %q", cwd)
		}
		ran = append(ran, argv)
		if argv[len(argv)-1] == "build" {
			return 3
		}
		return 0
	}
	if code, _, _ := runCLI(t, "--home", t.TempDir(), "desktop", "dev", "--open"); code != 0 {
		t.Fatalf("dev exit %d", code)
	}
	if code, _, errs := runCLI(t, "--home", t.TempDir(), "desktop", "build"); code != 3 || !strings.Contains(errs, "Build failed.") {
		t.Fatalf("build: %d %q", code, errs)
	}
	if strings.Join(ran[0], " ") != "/usr/bin/npm run dev -- --open" {
		t.Fatalf("dev argv %q", ran[0])
	}
	lookNpm = func() string { return "" }
	if code, _, errs := runCLI(t, "--home", t.TempDir(), "desktop", "install"); code != 1 || !strings.Contains(errs, "nodejs.org") {
		t.Fatalf("no npm: %d %q", code, errs)
	}
}

// Every `remedy …` line in a fenced block of the docs must parse; a renamed
// subcommand or an invented flag shows up here as the line that broke.
func TestCLIDocumentedCommandsParse(t *testing.T) {
	repo := filepath.Join("..", "..", "..")
	sources := []string{
		"README.md", "docs/USAGE.md", "docs/manual/10-cli-and-api.md",
		"docs/manual/11-reference-commands.md", "docs/manual/03-providers-and-auth.md",
		"docs/manual/21-personal-assistant.md", "docs/manual/24-telephony.md",
	}
	fence := regexp.MustCompile("(?ms)^```[a-z]*$(.*?)^```")
	found := map[string]bool{}
	for _, rel := range sources {
		raw, err := os.ReadFile(filepath.Join(repo, rel))
		if err != nil {
			continue
		}
		for _, m := range fence.FindAllStringSubmatch(string(raw), -1) {
			for _, line := range strings.Split(m[1], "\n") {
				line = strings.TrimSpace(strings.TrimSpace(strings.TrimSpace(line)))
				line = strings.TrimSpace(strings.TrimSuffix(line, `\`))
				if !strings.HasPrefix(line, "remedy ") {
					continue
				}
				line = strings.TrimSpace(strings.SplitN(line[len("remedy "):], "#", 2)[0])
				if line == "" || strings.ContainsAny(line, "<>|") || strings.Contains(line, "…") || strings.Contains(line, "...") {
					continue
				}
				found[line] = true
			}
		}
	}
	if len(found) <= 30 {
		t.Fatalf("found only %d documented command lines; extraction broke", len(found))
	}
	var refused []string
	for line := range found {
		argv, ok := shlexSplit(line)
		if !ok || len(argv) == 0 || argv[0] == "--help" || argv[0] == "-h" || argv[0] == "--version" {
			continue
		}
		if _, err := Parse(Root(), argv); err != nil && !errors.Is(err, errHelp) {
			refused = append(refused, line+"   ("+err.Error()+")")
		}
	}
	if len(refused) > 0 {
		t.Fatalf("the docs tell owners to type commands the CLI refuses:\n  remedy %s", strings.Join(refused, "\n  remedy "))
	}
	for _, cmd := range []string{"auth status", "auth apikey anthropic", "chat", "serve", "memory list", "skill list", "config show", "setup"} {
		argv, _ := shlexSplit(cmd)
		if _, err := Parse(Root(), argv); err != nil {
			t.Errorf("%q: %v", cmd, err)
		}
	}
}

// shlexSplit is POSIX shell word splitting (quotes and backslashes).
func shlexSplit(s string) ([]string, bool) {
	var out []string
	var cur strings.Builder
	inWord, quote := false, rune(0)
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch {
		case quote == '\'':
			if r == '\'' {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case quote == '"':
			if r == '"' {
				quote = 0
			} else if r == '\\' && i+1 < len(rs) && strings.ContainsRune(`"\$`+"`", rs[i+1]) {
				i++
				cur.WriteRune(rs[i])
			} else {
				cur.WriteRune(r)
			}
		case r == '\'' || r == '"':
			quote, inWord = r, true
		case r == '\\' && i+1 < len(rs):
			i++
			cur.WriteRune(rs[i])
			inWord = true
		case r == ' ' || r == '\t':
			if inWord {
				out = append(out, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}
	if quote != 0 {
		return nil, false
	}
	if inWord {
		out = append(out, cur.String())
	}
	return out, true
}
