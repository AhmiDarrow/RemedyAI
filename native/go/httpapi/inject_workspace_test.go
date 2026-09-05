package httpapi

import (
	"encoding/json"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestInjectShellCwdScopeMatrix(t *testing.T) {
	proj := `C:\proj`
	subdir := `C:\proj\src`
	outside := `D:\other`

	must := func(cwd, root, scope, want string) {
		t.Helper()
		var in []byte
		if cwd == "" {
			in, _ = json.Marshal(map[string]any{"argv": []string{"x"}})
		} else {
			in, _ = json.Marshal(map[string]any{"argv": []string{"x"}, "cwd": cwd})
		}
		out := injectShellCwd(in, root, scope)
		var args map[string]any
		if err := json.Unmarshal(out, &args); err != nil {
			t.Fatal(err)
		}
		got, _ := args["cwd"].(string)
		if got != want {
			t.Fatalf("scope=%s cwd=%q → %q want %q", scope, cwd, got, want)
		}
	}

	must(subdir, proj, "project", subdir)
	must(outside, proj, "project", proj)
	must(outside, proj, "full", outside)
	must("", proj, "project", proj)
}

func TestInjectWorkspaceRootOverwritesModelRoot(t *testing.T) {
	raw := []byte(`{"path":".","workspace_root":"C:\\Users\\evil\\.remedy\\auth"}`)
	out := injectWorkspaceRoot(raw, `C:\proj`)
	var args map[string]any
	if err := json.Unmarshal(out, &args); err != nil {
		t.Fatal(err)
	}
	if args["workspace_root"] != `C:\proj` || args["project_path"] != `C:\proj` {
		t.Fatalf("model root not forced: %#v", args)
	}
}

func TestInjectWorkspaceRootPassesSchema(t *testing.T) {
	reg := tools.NewRegistry()
	if err := tools.RegisterPythonWorkerLocalMirrors(reg); err != nil {
		t.Fatal(err)
	}
	raw := []byte(`{"path":"."}`)
	injected := injectWorkspaceRoot(raw, `C:\Users\Administrator\Old-Remedy`)
	var args map[string]any
	if err := json.Unmarshal(injected, &args); err != nil {
		t.Fatal(err)
	}
	if args["workspace_root"] == nil || args["project_path"] == nil {
		t.Fatalf("missing inject fields: %#v", args)
	}
	desc, err := reg.Latest("workspace.list")
	if err != nil {
		t.Fatal(err)
	}
	req := tools.Request{ToolID: desc.ID, Version: desc.Version, Input: injected}
	if _, err := reg.Execute(t.Context(), req); err != nil {
		t.Fatalf("schema/execute after inject: %v input=%s", err, string(injected))
	}
}

func TestRegistryPolicyAutoAllowsMutation(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("auto")
	p := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: "s1"}
	// shell.exec is Zig-registered RiskMutation (workspace.* needs RMDY attach).
	call := cognition.ToolCall{Name: "shell.exec", Input: []byte(`{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}`)}
	if d := p.Decide(t.Context(), call); d != cognition.Allow {
		t.Fatalf("auto mutation Decide=%v want Allow (mode=%q)", d, q.Mode())
	}
	_ = q.SetMode("ask")
	if d := p.Decide(t.Context(), call); d != cognition.Ask {
		t.Fatalf("ask mutation Decide=%v want Ask", d)
	}
}

func TestRegistryPolicyAskThenFingerprintAllows(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	sid := "s1"
	input := `{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}`
	item := q.Enqueue("shell.exec", input, "test", &sid, "run echo")
	_ = q.Resolve(item.ID, true, "session")
	p := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: sid}
	call := cognition.ToolCall{Name: "shell.exec", Input: []byte(input)}
	if d := p.Decide(t.Context(), call); d != cognition.Allow {
		t.Fatalf("after approve Decide=%v want Allow", d)
	}
}
