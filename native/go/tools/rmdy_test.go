package tools

import (
	"context"
	"encoding/json"
	"net"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

func TestRMDYPythonToolsRoundTrip(t *testing.T) {
	allow := AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil })
	serverReg := NewRegistry(allow)
	if err := RegisterPythonWorkerLocalMirrors(serverReg); err != nil {
		t.Fatal(err)
	}

	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ipc.ServeConn(ctx, serverConn, WorkerHandler{Registry: serverReg})

	client := ipc.NewClient(clientConn)
	defer client.Close()

	// workspace.* and text.* are Go tools now; route them over RMDY here only
	// to exercise the wire with tools whose results are easy to check.
	clientReg := NewRegistry(allow)
	exec := NewRMDYExecutor(client)
	for _, r := range pythonWorkerToolSpecs() {
		if _, ok := goWorkspaceToolIDs[r.id]; !ok {
			continue
		}
		if err := clientReg.Register(Descriptor{
			ID: r.id, Version: 1, Description: r.desc, Runtime: RuntimePython, Risk: r.risk,
			InputSchema: json.RawMessage(r.in), OutputSchema: json.RawMessage(r.out),
		}, exec); err != nil {
			t.Fatal(err)
		}
	}

	slug, err := clientReg.Execute(context.Background(), Request{
		ToolID: "text.slugify", Version: 1,
		Input: json.RawMessage(`{"text":"Hello, Remedy AI!"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var slugOut struct {
		Slug string `json:"slug"`
	}
	if err := json.Unmarshal(slug.Output, &slugOut); err != nil {
		t.Fatal(err)
	}
	if slugOut.Slug != "hello-remedy-ai" {
		t.Fatalf("slug=%q", slugOut.Slug)
	}

	words, err := clientReg.Execute(context.Background(), Request{
		ToolID: "text.word_count", Version: 1,
		Input: json.RawMessage(`{"text":"one two  three"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var wordOut struct {
		Words int `json:"words"`
	}
	if err := json.Unmarshal(words.Output, &wordOut); err != nil {
		t.Fatal(err)
	}
	if wordOut.Words != 3 {
		t.Fatalf("words=%d", wordOut.Words)
	}

	listed, err := clientReg.Execute(context.Background(), Request{
		ToolID: "workspace.list", Version: 1,
		Input: json.RawMessage(`{"path":"."}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var listOut struct {
		Total int `json:"total"`
	}
	if err := json.Unmarshal(listed.Output, &listOut); err != nil {
		t.Fatal(err)
	}
	if listOut.Total < 1 {
		t.Fatalf("workspace.list total=%d", listOut.Total)
	}

	written, err := clientReg.Execute(context.Background(), Request{
		ToolID: "workspace.write", Version: 1,
		Input:           json.RawMessage(`{"path":"out/note.txt","content":"hello abi"}`),
		CapabilityToken: []byte("test"),
	})
	if err != nil {
		t.Fatal(err)
	}
	var writeOut struct {
		Path         string `json:"path"`
		BytesWritten int    `json:"bytes_written"`
		Created      bool   `json:"created"`
	}
	if err := json.Unmarshal(written.Output, &writeOut); err != nil {
		t.Fatal(err)
	}
	if writeOut.Path != "out/note.txt" || writeOut.BytesWritten != len("hello abi") || !writeOut.Created {
		t.Fatalf("workspace.write=%+v", writeOut)
	}
}

func TestWirePayloadRoundTrip(t *testing.T) {
	raw, err := MarshalWireRequest(Request{
		ToolID: "text.slugify", Version: 1, Input: json.RawMessage(`{"text":"x"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	req, err := UnmarshalWireRequest(raw)
	if err != nil || req.ToolID != "text.slugify" || req.Version != 1 {
		t.Fatalf("req=%#v err=%v", req, err)
	}
	frame := protocol.Frame{Kind: protocol.KindToolRequest, Payload: raw}
	encoded, err := frame.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := protocol.Parse(encoded)
	if err != nil || parsed.Kind != protocol.KindToolRequest {
		t.Fatalf("frame=%#v err=%v", parsed, err)
	}
	out, err := MarshalWireResult(Result{Output: json.RawMessage(`{"slug":"x"}`)}, nil)
	if err != nil {
		t.Fatal(err)
	}
	result, err := UnmarshalWireResult(out)
	if err != nil || string(result.Output) != `{"slug":"x"}` {
		t.Fatalf("result=%#v err=%v", result, err)
	}
}
