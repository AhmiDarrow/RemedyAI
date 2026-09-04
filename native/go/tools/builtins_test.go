package tools

import (
	"context"
	"encoding/json"
	"runtime"
	"testing"
)

func TestGoBuiltinsExecute(t *testing.T) {
	registry := NewRegistry()
	if err := RegisterGoBuiltins(registry); err != nil {
		t.Fatal(err)
	}

	probe, err := registry.Execute(context.Background(), Request{
		ToolID: "runtime.probe", Version: 1, Input: json.RawMessage(`{}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var probeOut struct {
		Status   string `json:"status"`
		Protocol int    `json:"protocol"`
		ToolABI  int    `json:"tool_abi"`
		OS       string `json:"os"`
		Arch     string `json:"arch"`
	}
	if err := json.Unmarshal(probe.Output, &probeOut); err != nil {
		t.Fatal(err)
	}
	if probeOut.Status != "ready" || probeOut.Protocol != 1 || probeOut.ToolABI != 1 {
		t.Fatalf("probe=%#v", probeOut)
	}
	if probeOut.OS != runtime.GOOS || probeOut.Arch != runtime.GOARCH {
		t.Fatalf("probe os/arch=%s/%s", probeOut.OS, probeOut.Arch)
	}

	canon, err := registry.Execute(context.Background(), Request{
		ToolID: "json.canonical", Version: 1,
		Input: json.RawMessage(`{"value":{"b":1,"a":2}}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var canonOut struct {
		Canonical string `json:"canonical"`
	}
	if err := json.Unmarshal(canon.Output, &canonOut); err != nil {
		t.Fatal(err)
	}
	if canonOut.Canonical != `{"a":2,"b":1}` {
		t.Fatalf("canonical=%q", canonOut.Canonical)
	}

	digest, err := registry.Execute(context.Background(), Request{
		ToolID: "text.sha256", Version: 1,
		Input: json.RawMessage(`{"text":"remedy"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var digestOut struct {
		SHA256 string `json:"sha256"`
	}
	if err := json.Unmarshal(digest.Output, &digestOut); err != nil {
		t.Fatal(err)
	}
	if len(digestOut.SHA256) != 64 {
		t.Fatalf("sha256=%q", digestOut.SHA256)
	}
}
