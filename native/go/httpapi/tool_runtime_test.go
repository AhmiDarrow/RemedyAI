package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func slowToolRegistry(t *testing.T, deadline time.Duration) *tools.Registry {
	t.Helper()
	reg := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	err := reg.Register(tools.Descriptor{
		ID:           "test.slow",
		Version:      1,
		Description:  "blocks until its context ends",
		Runtime:      tools.RuntimeGo,
		Risk:         tools.RiskReadOnly,
		Deadline:     deadline,
		InputSchema:  json.RawMessage(`{"type":"object"}`),
		OutputSchema: json.RawMessage(`{"type":"object"}`),
	}, tools.ExecutorFunc(func(ctx context.Context, _ tools.Request) (tools.Result, error) {
		<-ctx.Done()
		return tools.Result{}, ctx.Err()
	}))
	if err != nil {
		t.Fatal(err)
	}
	return reg
}

func TestRegistryToolExecutorAppliesDescriptorDeadline(t *testing.T) {
	exec := &RegistryToolExecutor{Registry: slowToolRegistry(t, time.Second)}
	start := time.Now()
	res := exec.Execute(context.Background(), cognition.ToolCall{ID: "c1", Name: "test.slow", Input: []byte(`{}`)})
	if res.Err != "tool timed out after 1s" {
		t.Fatalf("Err=%q", res.Err)
	}
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Fatalf("deadline not enforced promptly: %s", elapsed)
	}
}

func TestRegistryToolExecutorReportsParentCancel(t *testing.T) {
	exec := &RegistryToolExecutor{Registry: slowToolRegistry(t, time.Minute)}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()
	res := exec.Execute(ctx, cognition.ToolCall{ID: "c2", Name: "test.slow", Input: []byte(`{}`)})
	if res.Err != "cancelled" {
		t.Fatalf("Err=%q want cancelled", res.Err)
	}
}

func TestDefaultRegistryDeadlines(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	shell, err := reg.Latest("shell.exec")
	if err != nil {
		t.Fatal(err)
	}
	if shell.Deadline != 0 {
		t.Fatalf("shell.exec deadline=%s want none", shell.Deadline)
	}
	probe, err := reg.Latest("runtime.probe")
	if err != nil {
		t.Fatal(err)
	}
	if probe.Deadline != defaultToolDeadline {
		t.Fatalf("runtime.probe deadline=%s want %s", probe.Deadline, defaultToolDeadline)
	}
	if deadlineForTool("prompt.assemble") != promptToolDeadline || deadlineForTool("web.fetch") != webToolDeadline {
		t.Fatal("prompt./web. deadline rule mismatch")
	}
}

func TestToolCommandPreviewIsRuneSafe(t *testing.T) {
	// 3-byte runes: the byte limit falls inside a rune unless clipping backs off.
	input := []byte(`{"text":"` + strings.Repeat("€", toolCommandPreviewLimit) + `"}`)
	preview := toolCommandPreview(cognition.ToolCall{Name: "computer.type", Input: input})
	if len(preview) > toolCommandPreviewLimit {
		t.Fatalf("preview len=%d over limit", len(preview))
	}
	for _, r := range preview {
		if r == '\uFFFD' {
			t.Fatal("preview split a rune")
		}
	}
	if !strings.HasSuffix(preview, "€") {
		t.Fatalf("preview should end on a whole rune: %q", preview[len(preview)-6:])
	}
}

func TestBindToolInputStripsServerOwnedFields(t *testing.T) {
	root := t.TempDir()
	evil := filepath.Join(t.TempDir(), ".remedy", "auth")

	decode := func(raw []byte) map[string]any {
		t.Helper()
		out := map[string]any{}
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatalf("bound input not JSON: %v %s", err, raw)
		}
		return out
	}

	ws := bindToolInput(cognition.ToolCall{Name: "workspace.read", Input: []byte(`{"path":"a.txt","workspace_root":"` + jsonEscape(evil) + `","home_dir":"` + jsonEscape(evil) + `"}`)}, toolBinding{Root: root, Scope: "project"})
	args := decode(ws.Input)
	if args["workspace_root"] != root || args["project_path"] != root {
		t.Fatalf("workspace root not rebound: %#v", args)
	}
	if _, ok := args["home_dir"]; ok {
		t.Fatalf("home_dir leaked: %#v", args)
	}

	sh := bindToolInput(cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["x"],"cwd":"` + jsonEscape(evil) + `","write_roots":["C:\\"],"home_dir":"h","owner_confirmed":true}`)}, toolBinding{Root: root, Scope: "project"})
	args = decode(sh.Input)
	if args["cwd"] != root {
		t.Fatalf("shell cwd not clamped: %#v", args)
	}
	roots, _ := args["write_roots"].([]any)
	if len(roots) != 1 || roots[0] != root {
		t.Fatalf("write_roots not rebound: %#v", args)
	}
	if _, ok := args["home_dir"]; ok {
		t.Fatalf("home_dir leaked on shell: %#v", args)
	}
	if _, ok := args["owner_confirmed"]; ok {
		t.Fatalf("owner_confirmed leaked: %#v", args)
	}

	mem := bindToolInput(cognition.ToolCall{Name: "memory.search", Input: []byte(`{"query":"q","home_dir":"h","project_path":"p","workspace_root":"w"}`)}, toolBinding{Root: root, Scope: "project"})
	args = decode(mem.Input)
	if args["project_path"] != root {
		t.Fatalf("memory project_path not rebound: %#v", args)
	}
	if _, ok := args["home_dir"]; ok {
		t.Fatalf("home_dir leaked on memory: %#v", args)
	}
	if _, ok := args["workspace_root"]; ok {
		t.Fatalf("workspace_root leaked on memory: %#v", args)
	}

	// No root: fields are still stripped, nothing injected.
	other := bindToolInput(cognition.ToolCall{Name: "prompt.assemble", Input: []byte(`{"home_dir":"h","workspace_root":"w","x":1}`)}, toolBinding{Scope: "project"})
	args = decode(other.Input)
	if len(args) != 1 || args["x"] != float64(1) {
		t.Fatalf("unexpected bound input: %#v", args)
	}
}

func jsonEscape(s string) string {
	b, _ := json.Marshal(s)
	return strings.Trim(string(b), `"`)
}

func TestInvokeToolBindsInputLikeEngine(t *testing.T) {
	s := newToolsAPIServer(t)
	_ = s.approvals.SetMode("ask")
	// A session with a project folder is the engine-parity case: scope is
	// "project", so the shell is clamped to the project and write_roots are set.
	project := t.TempDir()
	sess, err := s.sessions.Create(createSessionRequest{Title: "bind", ProjectPath: &project})
	if err != nil {
		t.Fatal(err)
	}
	body := `{"id":"shell.exec","session_id":"` + sess.ID + `","input":{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"],"cwd":"C:\\Windows\\System32","home_dir":"C:\\evil-home","workspace_root":"C:\\evil-root"}}`
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", body)
	if code != http.StatusForbidden {
		t.Fatalf("status=%d body=%s want 403 (Ask)", code, raw)
	}
	pending := s.approvals.ListPending(sess.ID)
	if len(pending) != 1 {
		t.Fatalf("pending=%d", len(pending))
	}
	cmd := pending[0].Command
	if strings.Contains(cmd, "evil-home") || strings.Contains(cmd, "evil-root") {
		t.Fatalf("caller-supplied home_dir/workspace_root reached the tool input: %s", cmd)
	}
	if strings.Contains(cmd, `System32`) && !strings.Contains(cmd, `cmd.exe`) {
		t.Fatalf("packaged-install cwd not rebound: %s", cmd)
	}
	var args map[string]any
	if err := json.Unmarshal([]byte(cmd), &args); err != nil {
		t.Fatalf("command preview not JSON: %v", err)
	}
	if cwd, _ := args["cwd"].(string); cwd == "" || strings.EqualFold(cwd, `C:\Windows\System32`) {
		t.Fatalf("cwd=%q should be the session root", cwd)
	}
	if _, ok := args["write_roots"]; !ok {
		t.Fatalf("write_roots not injected: %s", cmd)
	}
}

func TestIsSensitiveComputerAction(t *testing.T) {
	cases := []struct {
		name  string
		call  cognition.ToolCall
		wants bool
	}{
		{"place order label", cognition.ToolCall{Name: "computer.click", Input: []byte(`{"x":1,"y":2,"label":"Place order"}`)}, true},
		{"plain click", cognition.ToolCall{Name: "computer.click", Input: []byte(`{"x":1,"y":2,"label":"Open settings"}`)}, false},
		{"coordinate click on checkout", cognition.ToolCall{Name: "computer.click", Input: []byte(`{"x":1,"y":2,"page_context":"https://shop.example/checkout"}`)}, true},
		{"coordinate click elsewhere", cognition.ToolCall{Name: "computer.click", Input: []byte(`{"x":1,"y":2,"page_context":"https://docs.example/guide"}`)}, false},
		{"uia continue on billing", cognition.ToolCall{Name: "computer.uia.action", Input: []byte(`{"hwnd":1,"name":"Continue","action":"invoke","page_context":"Billing address"}`)}, true},
		{"uia toggle normal", cognition.ToolCall{Name: "computer.uia.action", Input: []byte(`{"hwnd":1,"name":"Dark mode","action":"toggle"}`)}, false},
		{"enter on payment page", cognition.ToolCall{Name: "computer.key", Input: []byte(`{"key":"enter","page_context":"Payment details"}`)}, true},
		{"tab on payment page", cognition.ToolCall{Name: "computer.key", Input: []byte(`{"key":"tab","page_context":"Payment details"}`)}, false},
		{"raw card typed", cognition.ToolCall{Name: "computer.type", Input: []byte(`{"text":"4111 1111 1111 1111"}`)}, true},
		{"vault handle typed", cognition.ToolCall{Name: "computer.type", Input: []byte(`{"text":"{{vault:visa}}"}`)}, true},
		{"ordinary typing", cognition.ToolCall{Name: "computer.type", Input: []byte(`{"text":"please send the report tomorrow"}`)}, false},
		{"captcha click", cognition.ToolCall{Name: "computer_click", Input: []byte(`{"x":1,"y":2,"page_context":"Verify you are human"}`)}, true},
		{"shell password", cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["C:\\x\\mysql.exe","--password=hunter2"]}`)}, true},
		{"shell ssh key", cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["C:\\x\\type.exe","C:\\Users\\me\\.ssh\\id_rsa"]}`)}, true},
		{"shell stripe charge", cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["stripe","charges","create","--amount","5000"]}`)}, true},
		{"shell build", cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["C:\\Go\\bin\\go.exe","test","./..."]}`)}, false},
		{"other tool", cognition.ToolCall{Name: "workspace.write", Input: []byte(`{"path":"pay now.txt","label":"Place order"}`)}, false},
	}
	for _, tc := range cases {
		if got := isSensitiveComputerAction(tc.call); got != tc.wants {
			t.Errorf("%s: got %v want %v", tc.name, got, tc.wants)
		}
	}
}

func TestSensitiveMutationAsksInAutoAndIsExcludedFromModeSweep(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	sid := "s-sensitive"
	p := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: sid}
	call := cognition.ToolCall{ID: "c-order", Name: "computer.click", Input: []byte(`{"x":10,"y":20,"label":"Place order"}`)}
	item := enqueueToolApproval(q, reg, sid, call)
	if !item.Sensitive || !approvalIsSensitive(call) {
		t.Fatal("Place order click must enqueue as sensitive")
	}
	_ = q.SetMode("auto")
	if got := q.Get(item.ID).Status; got != "pending" {
		t.Fatalf("SetMode(auto) swept a sensitive item: %q", got)
	}
	if d := p.Decide(context.Background(), call); d != cognition.Ask {
		t.Fatalf("auto mode Decide=%v want Ask for a payment click", d)
	}
	plain := cognition.ToolCall{ID: "c-plain", Name: "computer.click", Input: []byte(`{"x":10,"y":20,"label":"Open settings"}`)}
	if d := p.Decide(context.Background(), plain); d != cognition.Allow {
		t.Fatalf("auto mode Decide=%v want Allow for an ordinary click", d)
	}
}

func TestShellExecCancelledThroughExecutor(t *testing.T) {
	if runtime.GOOS != "windows" && runtime.GOOS != "linux" {
		t.Skip("authorized spawn is Windows/Linux")
	}
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	if core.FindLibraryPath() == "" {
		t.Skip("remedy_core not built")
	}
	t.Setenv("REMEDY_HOME", t.TempDir())
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	exec := &RegistryToolExecutor{Registry: reg, TokenFor: RuntimeCapabilityToken}
	var argv []string
	if runtime.GOOS == "windows" {
		argv = []string{`C:\Windows\System32\cmd.exe`, "/c", "ping", "-n", "30", "127.0.0.1"}
	} else {
		argv = []string{"/bin/sleep", "30"}
	}
	input, _ := json.Marshal(map[string]any{"argv": argv})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(200 * time.Millisecond)
		cancel()
	}()
	start := time.Now()
	res := exec.Execute(ctx, cognition.ToolCall{ID: "c-cancel", Name: "shell.exec", Input: input})
	if res.Err != "cancelled" {
		t.Fatalf("Err=%q output=%s", res.Err, res.Output)
	}
	if elapsed := time.Since(start); elapsed > 6*time.Second {
		t.Fatalf("cancellation took %s", elapsed)
	}
}
