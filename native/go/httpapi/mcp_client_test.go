package httpapi

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

type mcpEchoIn struct {
	Text string `json:"text" jsonschema:"what to say back"`
}

// startTestConnector runs a real MCP server in memory with two tools and
// returns a manager connected to it under the name "demo".
func startTestConnector(t *testing.T) (*mcpManager, *tools.Registry, *atomic.Int32) {
	t.Helper()
	server := mcp.NewServer(&mcp.Implementation{Name: "demo-server", Version: "1"}, nil)
	mcp.AddTool(server, &mcp.Tool{Name: "echo", Description: "Say the text back."},
		func(_ context.Context, _ *mcp.CallToolRequest, in mcpEchoIn) (*mcp.CallToolResult, any, error) {
			return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: "you said: " + in.Text}}}, nil, nil
		})
	mcp.AddTool(server, &mcp.Tool{Name: "Break It", Description: "Always fails."},
		func(_ context.Context, _ *mcp.CallToolRequest, _ mcpEchoIn) (*mcp.CallToolResult, any, error) {
			return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "nope"}}}, nil, nil
		})
	serverSide, clientSide := mcp.NewInMemoryTransports()
	session, err := server.Connect(context.Background(), serverSide, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = session.Close() })

	registry := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	// The manager calls this from its connect goroutine.
	resyncs := &atomic.Int32{}
	m := newMCPManager(t.TempDir(), registry, func() { resyncs.Add(1) })
	m.dial = func(mcpServerConfig, string) (mcp.Transport, error) { return clientSide, nil }
	t.Cleanup(m.Stop)
	m.set(mcpServerConfig{Name: "demo", Command: "unused", Enabled: true, Approval: "ask"})
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if st := m.status(); len(st) == 1 && st[0]["state"] == "connected" {
			return m, registry, resyncs
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("the connector did not connect: %v", m.status())
	return nil, nil, nil
}

func TestConnectorToolsAreListedCalledAndRemoved(t *testing.T) {
	m, registry, resyncs := startTestConnector(t)
	st := m.status()[0]
	listed, _ := st["tools"].([]mcpToolInfo)
	if len(listed) != 2 || listed[0].ID != "mcp.demo.break_it" || listed[1].ID != "mcp.demo.echo" {
		t.Fatalf("tools: %+v", listed)
	}
	if resyncs.Load() == 0 {
		t.Fatal("the model's tool list was not refreshed after the tools arrived")
	}
	desc, err := registry.Latest("mcp.demo.echo")
	if err != nil || !strings.HasPrefix(desc.Description, "[demo connector] ") || desc.Risk != tools.RiskMutation {
		t.Fatalf("descriptor: %+v err=%v", desc, err)
	}
	// A call reaches the server and comes back labelled as outside content.
	res, err := registry.Execute(context.Background(), tools.Request{
		ToolID: "mcp.demo.echo", Version: 1, Input: []byte(`{"text":"hello"}`), CapabilityToken: []byte("runtime-local"),
	})
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	_ = json.Unmarshal(res.Output, &out)
	if out["content"] != "you said: hello" || !strings.Contains(out["source"].(string), "outside service") {
		t.Fatalf("result: %v", out)
	}
	// The server's own failure is an error she can read, not an empty success.
	if _, err := registry.Execute(context.Background(), tools.Request{
		ToolID: "mcp.demo.break_it", Version: 1, Input: []byte(`{"text":"x"}`), CapabilityToken: []byte("runtime-local"),
	}); err == nil || !strings.Contains(err.Error(), "nope") {
		t.Fatalf("a failed tool must be an error naming why: %v", err)
	}
	// Removing the connector removes its tools.
	if !m.remove("demo") {
		t.Fatal("remove")
	}
	if _, err := registry.Latest("mcp.demo.echo"); err == nil {
		t.Fatal("the tool is still registered after its connector was removed")
	}
}

func TestConnectorCallsFollowTheApprovalRules(t *testing.T) {
	_, registry, _ := startTestConnector(t)
	call := cognition.ToolCall{Name: "mcp.demo.echo", Input: []byte(`{"text":"hi"}`)}
	decide := func(mode string, auto, outside bool) cognition.Decision {
		q := newApprovalQueue()
		q.SetMode(mode)
		p := &RegistryPolicy{Registry: registry, Approvals: q, SessionID: "s", ForceAsk: outside}
		if auto {
			p.Guards.MCPAuto = map[string]bool{"demo": true}
		}
		return p.Decide(context.Background(), call)
	}
	for _, c := range []struct {
		name          string
		mode          string
		auto, outside bool
		want          cognition.Decision
	}{
		{"ask mode asks", "ask", false, false, cognition.Ask},
		{"auto mode asks for a connector", "auto", false, false, cognition.Ask},
		{"auto mode runs a connector the owner marked auto", "auto", true, false, cognition.Allow},
		{"full mode runs it", "full", false, false, cognition.Allow},
		{"a turn from outside cannot use a connector, in any mode", "full", true, true, cognition.Deny},
	} {
		if got := decide(c.mode, c.auto, c.outside); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}

func TestConnectorConfigIsReadAndWritten(t *testing.T) {
	cfg := ConfigMap{"mcp_servers": []any{
		map[string]any{"name": "GitHub", "url": "https://example.test/mcp", "approval": "auto"},
		map[string]any{"name": "files", "command": "npx", "args": []any{"-y", "some-server"}, "token_env": "TOKEN", "enabled": false},
		map[string]any{"name": "bad name!", "command": "x"},
		map[string]any{"name": "empty"},
		map[string]any{"name": "github", "command": "duplicate"},
	}}
	got := loadMCPServers(cfg)
	if len(got) != 2 || got[0].Name != "github" || got[0].Approval != "auto" || !got[0].Enabled ||
		got[1].Name != "files" || got[1].Enabled || got[1].Approval != "ask" || len(got[1].Args) != 2 {
		t.Fatalf("servers: %+v", got)
	}
	if auto := mcpAutoServers(cfg); len(auto) != 1 || !auto["github"] {
		t.Fatalf("auto servers: %v", auto)
	}
	// What is written back reads the same, and holds no token.
	back := loadMCPServers(ConfigMap{"mcp_servers": []any{got[0].table(), got[1].table()}})
	if len(back) != 2 || back[1].TokenEnv != "TOKEN" || back[1].Command != "npx" {
		t.Fatalf("round trip: %+v", back)
	}
	for _, c := range got {
		if _, has := c.table()["token"]; has {
			t.Fatal("a token must never be written to config")
		}
	}
	if mcpServerOf("mcp.github.create_issue") != "github" || mcpServerOf("bash") != "" {
		t.Fatal("server name of a tool id")
	}
}
