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
	serverReg := NewRegistry()
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

	clientReg := NewRegistry()
	if err := RegisterPythonWorkerTools(clientReg, client); err != nil {
		t.Fatal(err)
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
