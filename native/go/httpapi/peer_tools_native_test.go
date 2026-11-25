package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const peerFakeSecret = "tok-fake-peer-secret-0000"

type peerSeen struct {
	method, path string
	header       http.Header
	body         map[string]any
}

type peerFake struct {
	mu   sync.Mutex
	seen []peerSeen
}

func (f *peerFake) last(t *testing.T) peerSeen {
	t.Helper()
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.seen) == 0 {
		t.Fatal("fake peer received no request")
	}
	return f.seen[len(f.seen)-1]
}

func (f *peerFake) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.seen)
}

func newPeerFake(t *testing.T, status int, reply string) (*peerFake, *httptest.Server) {
	t.Helper()
	f := &peerFake{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		f.mu.Lock()
		f.seen = append(f.seen, peerSeen{r.Method, r.URL.Path, r.Header.Clone(), body})
		f.mu.Unlock()
		w.WriteHeader(status)
		_, _ = io.WriteString(w, reply)
	}))
	t.Cleanup(srv.Close)
	return f, srv
}

func peerWriteStore(t *testing.T, s *Server, peers map[string]any) {
	t.Helper()
	dir := s.interopDir()
	raw, _ := json.Marshal(map[string]any{"version": 1, "peers": peers})
	if err := os.WriteFile(filepath.Join(dir, "peers.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
}

func peerAuditLines(t *testing.T, s *Server) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(s.interopDir(), "audit.jsonl"))
	if err != nil {
		return nil
	}
	var out []map[string]any
	for _, l := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		if l == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			t.Fatalf("bad audit line %q: %v", l, err)
		}
		out = append(out, m)
	}
	return out
}

func peerEntry(endpoint, protocol string, enabled bool, skills ...any) map[string]any {
	return map[string]any{
		"id": "x", "name": "Fake Bot", "protocol": protocol, "endpoint": endpoint,
		"skills": skills, "enabled": enabled, "permissions": []any{"ask"},
		"auth_ref": "FAKE_ENV", "token": peerFakeSecret, "api_key": peerFakeSecret,
	}
}

const peerTaskReply = `{"jsonrpc":"2.0","id":"1","result":{"task":{"id":"t-1","status":{"state":"TASK_STATE_WORKING","message":{"parts":[{"text":"hello from fake"}]}}}}}`

func TestPeerListRedactsAndIncludesDisabled(t *testing.T) {
	s := newInteropServer(t, false)
	b := peerEntry("http://x.example.com/a2a", "a2a", false, "code")
	b["id"], b["name"] = "b", "Beta"
	a := peerEntry("http://a.example.com/a2a", "mcp", true)
	a["id"], a["name"] = "a", "Alpha"
	peerWriteStore(t, s, map[string]any{"b": b, "a": a, "junk": "not a dict"})
	out, err := toolPeerList(context.Background(), s, map[string]any{})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(out)
	if strings.Contains(string(raw), peerFakeSecret) || strings.Contains(string(raw), "endpoint") || strings.Contains(string(raw), "FAKE_ENV") {
		t.Fatalf("list leaked: %s", raw)
	}
	want := `{"ok":true,"peers":[{"enabled":true,"id":"a","name":"Alpha","protocol":"mcp","skills":[]},{"enabled":false,"id":"b","name":"Beta","protocol":"a2a","skills":["code"]}],"total":2}`
	if string(raw) != want {
		t.Fatalf("got %s\nwant %s", raw, want)
	}
}

func TestPeerListEmptyStore(t *testing.T) {
	s := newInteropServer(t, false)
	out, _ := toolPeerList(context.Background(), s, map[string]any{})
	if out["ok"] != true || out["total"] != 0 {
		t.Fatalf("%v", out)
	}
}

func TestPeerAskA2AExactRequest(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL+"/a2a", "a2a", true)})
	out, err := toolPeerAsk(context.Background(), s, map[string]any{"question": "  what is up?  ", "peer_id": "x"})
	if err != nil {
		t.Fatal(err)
	}
	got := f.last(t)
	if got.method != "POST" || got.path != "/a2a" {
		t.Fatalf("%s %s", got.method, got.path)
	}
	if got.header.Get("Content-Type") != "application/json" || got.header.Get("Accept") != "application/json" || got.header.Get("A2A-Version") != "1.0" {
		t.Fatalf("headers %v", got.header)
	}
	if got.header.Get("Authorization") != "" || strings.Contains(strings.Join(got.header.Values("Authorization"), ""), peerFakeSecret) {
		t.Fatal("token was sent")
	}
	if got.body["jsonrpc"] != "2.0" || got.body["method"] != "SendMessage" || got.body["id"] == "" {
		t.Fatalf("body %v", got.body)
	}
	params := got.body["params"].(map[string]any)
	if cfg := params["configuration"].(map[string]any); cfg["return_immediately"] != true || len(cfg) != 1 {
		t.Fatalf("cfg %v", cfg)
	}
	msg := params["message"].(map[string]any)
	parts := msg["parts"].([]any)
	if msg["role"] != "user" || msg["messageId"] == "" || len(parts) != 1 || parts[0].(map[string]any)["text"] != "what is up?" {
		t.Fatalf("msg %v", msg)
	}
	if md := msg["metadata"].(map[string]any); md["hop"] != float64(1) || md["from"] != "remedy" || len(md) != 2 {
		t.Fatalf("metadata %v", md)
	}
	raw, _ := json.Marshal(out)
	want := `{"ok":true,"results":[{"ok":true,"peer_id":"x","task_id":"t-1","text":"hello from fake"}],"summary":"x: hello from fake"}`
	if string(raw) != want {
		t.Fatalf("got %s\nwant %s", raw, want)
	}
	if len(peerAuditLines(t, s)) != 0 {
		t.Fatal("named-peer path must not audit (Python parity)")
	}
}

func TestPeerAskMCPPeerUsesA2AWire(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, `{"result":{"message":{"parts":[{"text":"mcp peer says hi"}]}}}`)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL+"/mcp", "mcp", true)})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "ping", "peer_id": "x"})
	if got := f.last(t); got.path != "/mcp" || got.body["method"] != "SendMessage" {
		t.Fatalf("%v", got)
	}
	if out["ok"] != true || out["summary"] != "x: mcp peer says hi" {
		t.Fatalf("%v", out)
	}
}

func TestPeerAskJSONQuestionSendsDataPart(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, `{"result":{"text":"plain"}}`)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
	_, _ = toolPeerAsk(context.Background(), s, map[string]any{"question": `{"a":1}`, "peer_id": "x"})
	parts := f.last(t).body["params"].(map[string]any)["message"].(map[string]any)["parts"].([]any)
	if len(parts) != 2 || parts[1].(map[string]any)["data"].(map[string]any)["a"] != float64(1) {
		t.Fatalf("%v", parts)
	}
}

func TestPeerAskRefusals(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{
		"x":  peerEntry(srv.URL, "a2a", true),
		"ne": peerEntry("  ", "a2a", true),
	})
	ctx := context.Background()
	out, _ := toolPeerAsk(ctx, s, map[string]any{"question": "  ", "peer_id": "x"})
	if out["ok"] != false || out["error"] != "question is required" {
		t.Fatalf("%v", out)
	}
	out, _ = toolPeerDelegate(ctx, s, map[string]any{"task": ""})
	if out["error"] != "task is required" {
		t.Fatalf("%v", out)
	}
	out, _ = toolPeerAsk(ctx, s, map[string]any{"question": "hi", "peer_id": "nobody"})
	if out["ok"] != false || out["error"] != "unknown peer" || out["summary"] != "" {
		t.Fatalf("%v", out)
	}
	out, _ = toolPeerAsk(ctx, s, map[string]any{"question": "hi", "peer_id": "ne"})
	if out["ok"] != false || !strings.Contains(out["summary"].(string), "peer has no endpoint") {
		t.Fatalf("%v", out)
	}
	if f.count() != 0 {
		t.Fatal("refused calls reached the peer")
	}
}

// The Python never checks `enabled` for a peer named by id; this pins that.
func TestPeerAskDisabledNamedPeerParity(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", false)})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
	if out["ok"] != true || f.count() != 1 {
		t.Fatalf("%v count=%d", out, f.count())
	}
}

func TestPeerAskPickBySkillSkipsDisabledAndAudits(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, peerTaskReply)
	on := peerEntry(srv.URL+"/a2a?key="+peerFakeSecret, "a2a", true, "Weather")
	on["id"] = "on"
	off := peerEntry(srv.URL+"/off", "a2a", false, "weather")
	off["id"] = "off"
	other := peerEntry(srv.URL+"/other", "a2a", true, "cooking")
	other["id"] = "other"
	peerWriteStore(t, s, map[string]any{"on": on, "off": off, "other": other})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "weather in Oslo? Bearer abc.def"})
	if out["ok"] != true || f.count() != 1 || f.last(t).path != "/a2a" {
		t.Fatalf("%v count=%d", out, f.count())
	}
	lines := peerAuditLines(t, s)
	if len(lines) != 1 {
		t.Fatalf("audit lines: %v", lines)
	}
	l := lines[0]
	if l["direction"] != "out" || l["protocol"] != "a2a" || l["peer_id"] != "on" || l["action"] != "delegate" {
		t.Fatalf("%v", l)
	}
	if l["summary"] != "weather in Oslo? Bearer ***" {
		t.Fatalf("summary %v", l["summary"])
	}
	raw, _ := json.Marshal(l)
	if strings.Contains(string(raw), peerFakeSecret) || strings.Contains(string(raw), "key=") {
		t.Fatalf("audit leaked endpoint secret: %s", raw)
	}
	if _, ok := l["ts"].(float64); !ok {
		t.Fatal("no ts")
	}
	out, _ = toolPeerAsk(context.Background(), s, map[string]any{"question": "quantum"})
	if out["ok"] != false || out["error"] != "no matching peer" {
		t.Fatalf("%v", out)
	}
}

func TestPeerAuditOffKnob(t *testing.T) {
	s := newInteropServer(t, false)
	_ = os.WriteFile(filepath.Join(s.homeDir, "config.toml"), []byte("[advanced]\ninterop_audit = false\n"), 0o600)
	_, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true, "code")})
	_, _ = toolPeerDelegate(context.Background(), s, map[string]any{"task": "code review"})
	if len(peerAuditLines(t, s)) != 0 {
		t.Fatal("audit written with interop_audit=false")
	}
}

func TestPeerDelegateThenStatus(t *testing.T) {
	s := newInteropServer(t, false)
	_, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
	out, _ := toolPeerDelegate(context.Background(), s, map[string]any{"task": "do it", "peer_id": "x"})
	tid := out["results"].([]any)[0].(map[string]any)["task_id"].(string)
	// Python never records a delegated task locally: status says unknown.
	st, _ := toolPeerStatus(context.Background(), s, map[string]any{"task_id": tid})
	if st["ok"] != false || st["error"] != "unknown task" {
		t.Fatalf("%v", st)
	}
	// Local tasks written by the inbound side are readable, then reach completion and failure.
	for _, state := range []string{"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"} {
		s.saveInteropTasks(map[string]map[string]any{
			"t-9": {"id": "t-9", "status": map[string]any{"state": state}},
		})
		st, _ = toolPeerStatus(context.Background(), s, map[string]any{"task_id": " t-9 "})
		task, _ := st["task"].(map[string]any)
		if st["ok"] != true || task["status"].(map[string]any)["state"] != state {
			t.Fatalf("%v", st)
		}
	}
	lines := peerAuditLines(t, s)
	if len(lines) != 2 {
		t.Fatalf("audit %v", lines)
	}
	l := lines[0]
	if l["direction"] != "in" || l["protocol"] != "mcp" || l["peer_id"] != nil || l["action"] != "task_status" || l["summary"] != "t-9" {
		t.Fatalf("%v", l)
	}
	st, _ = toolPeerStatus(context.Background(), s, map[string]any{"task_id": ""})
	if st["error"] != "task_id is required" {
		t.Fatalf("%v", st)
	}
}

func TestPeerAskHTTPErrors(t *testing.T) {
	for _, tc := range []struct {
		status int
		want   string
	}{{400, "x: error — A2A HTTP 400"}, {404, "x: error — A2A HTTP 404"}, {500, "x: error — A2A HTTP 500"}, {307, "x: error — A2A HTTP 307"}} {
		s := newInteropServer(t, false)
		_, srv := newPeerFake(t, tc.status, `{"error":"nope"}`)
		peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
		out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
		if out["ok"] != false || out["summary"] != tc.want {
			t.Fatalf("%d: %v", tc.status, out)
		}
		r := out["results"].([]any)[0].(map[string]any)
		if r["ok"] != false || r["error"] != strings.TrimPrefix(tc.want, "x: error — ") {
			t.Fatalf("%v", r)
		}
	}
}

func TestPeerAskRPCErrorAndBadJSON(t *testing.T) {
	s := newInteropServer(t, false)
	_, srv := newPeerFake(t, 200, `{"jsonrpc":"2.0","error":{"code":-32000,"message":"permission denied"}}`)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
	if out["ok"] != false || out["summary"] != "x: error — permission denied" {
		t.Fatalf("%v", out)
	}
	_, srv2 := newPeerFake(t, 200, `not json`)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv2.URL, "a2a", true)})
	out, _ = toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
	if out["ok"] != false {
		t.Fatalf("%v", out)
	}
}

func TestPeerAskTimeout(t *testing.T) {
	old := peerTimeout
	peerTimeout = 150 * time.Millisecond
	t.Cleanup(func() { peerTimeout = old })
	s := newInteropServer(t, false)
	done := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
		}
	}))
	t.Cleanup(func() { close(done); srv.Close() })
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
	if out["ok"] != false || !strings.Contains(out["summary"].(string), "timed out") {
		t.Fatalf("%v", out)
	}
}

func TestPeerAskUnreachable(t *testing.T) {
	s := newInteropServer(t, false)
	srv := httptest.NewServer(http.NotFoundHandler())
	url := srv.URL
	srv.Close()
	peerWriteStore(t, s, map[string]any{"x": peerEntry(url, "a2a", true)})
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
	if out["ok"] != false || !strings.HasPrefix(out["summary"].(string), "x: error — A2A request failed: ") {
		t.Fatalf("%v", out)
	}
}

// The Python has no size limit on the outbound text; an oversized message is
// sent whole and the full body arrives.
func TestPeerAskOversizedMessageParity(t *testing.T) {
	s := newInteropServer(t, false)
	f, srv := newPeerFake(t, 200, peerTaskReply)
	peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
	big := strings.Repeat("a", 200_000)
	out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": big, "peer_id": "x"})
	got := f.last(t).body["params"].(map[string]any)["message"].(map[string]any)["parts"].([]any)[0].(map[string]any)["text"].(string)
	if out["ok"] != true || got != big {
		t.Fatal("oversized message was altered or refused")
	}
}

func TestPeerReplyShapes(t *testing.T) {
	cases := map[string]string{
		`{"result":{"id":"t2","status":{"message":{"parts":[{"kind":"text","text":"flat task"}]}}}}`: "x: flat task",
		`{"result":{"task":{"id":"t3","status":{}}}}`:                                                "x: (empty)",
		`{"result":{"message":{"parts":[{"text":"ril1 abcdense"}]}}}`:                        "x: [inner note]",
	}
	for reply, want := range cases {
		s := newInteropServer(t, false)
		_, srv := newPeerFake(t, 200, reply)
		peerWriteStore(t, s, map[string]any{"x": peerEntry(srv.URL, "a2a", true)})
		out, _ := toolPeerAsk(context.Background(), s, map[string]any{"question": "hi", "peer_id": "x"})
		if out["summary"] != want {
			t.Fatalf("%s -> %v", reply, out["summary"])
		}
	}
}

func TestPeerRedactAndAuditRotate(t *testing.T) {
	m := peerRedact(map[string]any{"Token": "abc", "x_token": "d", "empty_token": "", "n": []any{"Bearer q sk-abcdefghijklmnop"}}).(map[string]any)
	if m["Token"] != "***" || m["x_token"] != "***" || m["empty_token"] != "" || m["n"].([]any)[0] != "Bearer *** ***" {
		t.Fatalf("%v", m)
	}
	s := newInteropServer(t, false)
	pad := strings.Repeat("p", 1000)
	for i := 0; i < 700; i++ {
		peerAudit(s, nil, "out", "a2a", "x", "delegate", pad, nil)
	}
	n := len(peerAuditLines(t, s))
	if n == 0 || n > peerAuditMaxLines {
		t.Fatalf("lines %d", n)
	}
	st, _ := os.Stat(filepath.Join(s.interopDir(), "audit.jsonl"))
	if st.Size() > peerAuditMaxBytes+2000 {
		t.Fatalf("size %d", st.Size())
	}
}
