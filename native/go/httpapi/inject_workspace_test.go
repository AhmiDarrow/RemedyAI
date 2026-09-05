package httpapi

import (
	"encoding/json"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestInjectShellCwdOnlyWhenMissing(t *testing.T) {
	with := injectShellCwd([]byte(`{"argv":["C:\\Windows\\System32\\cmd.exe","/c","cd"],"cwd":"D:\\keep"}`), `C:\proj`)
	var args map[string]any
	if err := json.Unmarshal(with, &args); err != nil {
		t.Fatal(err)
	}
	if args["cwd"] != `D:\keep` {
		t.Fatalf("cwd overwritten: %#v", args["cwd"])
	}
	filled := injectShellCwd([]byte(`{"argv":["C:\\Windows\\System32\\cmd.exe","/c","cd"]}`), `C:\proj`)
	if err := json.Unmarshal(filled, &args); err != nil {
		t.Fatal(err)
	}
	if args["cwd"] != `C:\proj` {
		t.Fatalf("cwd not injected: %#v", args["cwd"])
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
	// Schema validate through registry Latest for workspace.list
	desc, err := reg.Latest("workspace.list")
	if err != nil {
		t.Fatal(err)
	}
	req := tools.Request{ToolID: desc.ID, Version: desc.Version, Input: injected}
	// Execute against local mirror — must not fail schema validation
	if _, err := reg.Execute(t.Context(), req); err != nil {
		t.Fatalf("schema/execute after inject: %v input=%s", err, string(injected))
	}
}
