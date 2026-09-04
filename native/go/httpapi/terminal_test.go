package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

const terminalTestToken = "tok-terminal-test-not-a-secret"

type fakeTermProc struct {
	mu     sync.Mutex
	writes []string
	reads  [][]byte
	ri     int
	code   *int
	killed bool
}

func (p *fakeTermProc) Write(data []byte) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.writes = append(p.writes, string(data))
	return nil
}

func (p *fakeTermProc) ReadSync(n int) ([]byte, error) {
	for {
		p.mu.Lock()
		if p.ri < len(p.reads) {
			chunk := p.reads[p.ri]
			p.ri++
			if len(chunk) > n {
				rest := append([]byte(nil), chunk[n:]...)
				chunk = chunk[:n]
				p.reads = append([][]byte{rest}, p.reads[p.ri:]...)
				p.ri = 0
			}
			out := append([]byte(nil), chunk...)
			p.mu.Unlock()
			return out, nil
		}
		killed := p.killed
		p.mu.Unlock()
		if killed {
			return nil, nil
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func (p *fakeTermProc) Poll() *int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.code
}

func (p *fakeTermProc) Kill() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.killed = true
	c := 1
	p.code = &c
}

func (p *fakeTermProc) Resize(_, _ int) error { return nil }

func newTerminalTestServer(t *testing.T) (*Server, *fakeTermProc) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   terminalTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	fake := &fakeTermProc{reads: [][]byte{[]byte("hello\x1b[31mred\x1b[0m\n")}}
	s.terminals().setSpawnOverride(func(cwd string, cols, rows int) (TerminalProc, error) {
		return fake, nil
	})
	t.Cleanup(func() { _ = s.Close() })
	return s, fake
}

func doTerminalJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+terminalTestToken)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, string(raw)
}

func TestTerminalOpenInputResizeClose(t *testing.T) {
	s, fake := newTerminalTestServer(t)
	code, body, text := doTerminalJSON(t, s, http.MethodPost, "/api/terminal", map[string]any{
		"cols": 80, "rows": 24,
	})
	if code != http.StatusOK {
		t.Fatalf("open status=%d body=%s", code, text)
	}
	tid, _ := body["terminal_id"].(string)
	if !strings.HasPrefix(tid, "term-") {
		t.Fatalf("terminal_id=%v", body["terminal_id"])
	}

	code, _, text = doTerminalJSON(t, s, http.MethodPost, "/api/terminal/"+tid+"/input", map[string]any{
		"data": "echo hi\n",
	})
	if code != http.StatusOK {
		t.Fatalf("input status=%d body=%s", code, text)
	}
	fake.mu.Lock()
	if len(fake.writes) == 0 || fake.writes[0] != "echo hi\n" {
		fake.mu.Unlock()
		t.Fatalf("writes=%v", fake.writes)
	}
	fake.mu.Unlock()

	code, _, text = doTerminalJSON(t, s, http.MethodPost, "/api/terminal/"+tid+"/resize", map[string]any{
		"cols": 120, "rows": 40,
	})
	if code != http.StatusOK {
		t.Fatalf("resize status=%d body=%s", code, text)
	}

	code, _, text = doTerminalJSON(t, s, http.MethodDelete, "/api/terminal/"+tid, nil)
	if code != http.StatusOK {
		t.Fatalf("close status=%d body=%s", code, text)
	}

	code, _, text = doTerminalJSON(t, s, http.MethodPost, "/api/terminal/"+tid+"/input", map[string]any{
		"data": "x",
	})
	if code != http.StatusNotFound {
		t.Fatalf("input after close want 404 got %d body=%s", code, text)
	}
}

func TestTerminalStreamStripsANSI(t *testing.T) {
	s, fake := newTerminalTestServer(t)
	code, body, text := doTerminalJSON(t, s, http.MethodPost, "/api/terminal", map[string]any{})
	if code != http.StatusOK {
		t.Fatalf("open status=%d body=%s", code, text)
	}
	tid, _ := body["terminal_id"].(string)

	// Unblock reader after first chunk by marking killed on a timer.
	go func() {
		time.Sleep(100 * time.Millisecond)
		fake.Kill()
	}()

	req := httptest.NewRequest(http.MethodGet, "/api/terminal/"+tid+"/stream", nil)
	req.Header.Set("Authorization", "Bearer "+terminalTestToken)
	rr := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		s.Handler().ServeHTTP(rr, req)
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("stream did not finish")
	}
	out := rr.Body.String()
	if !strings.Contains(out, "event: output") {
		t.Fatalf("missing output event: %s", out)
	}
	if strings.Contains(out, "\x1b[") {
		t.Fatalf("ANSI not stripped: %s", out)
	}
	if !strings.Contains(out, "hellored") && !strings.Contains(out, "hello") {
		t.Fatalf("expected text in stream: %s", out)
	}
}

func TestStripANSI(t *testing.T) {
	got := stripANSI("a\x1b[31mb\x1b[0mc")
	if got != "abc" {
		t.Fatalf("got %q", got)
	}
}

func TestTerminalOpenFailsClosedWithoutOverrideOrConPTY(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	// Point at a missing library so ConPTY path fails closed.
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(home, "missing.dll"))
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   terminalTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	// Ensure no spawn override.
	s.terminals().setSpawnOverride(nil)
	code, body, text := doTerminalJSON(t, s, http.MethodPost, "/api/terminal", map[string]any{})
	if code != http.StatusInternalServerError {
		t.Fatalf("want 500 got %d body=%s", code, text)
	}
	detail, _ := body["detail"].(string)
	if !strings.Contains(strings.ToLower(detail), "terminal") && !strings.Contains(strings.ToLower(detail), "conpty") && !strings.Contains(strings.ToLower(detail), "unavailable") {
		t.Fatalf("detail=%q", detail)
	}
}
