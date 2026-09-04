package httpapi

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSessionEventsSSEHelloAndCRUD(t *testing.T) {
	const token = "test-token-not-a-secret-16"
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	base, shutdown := startTestServer(t, Config{
		Token:   token,
		Version: "0.50.2",
		DBPath:  dbPath,
	})
	defer shutdown()

	// Unauthenticated SSE → 401
	resp, err := http.Get(base + "/api/events/sessions")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauth SSE status = %d", resp.StatusCode)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/api/events/sessions", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)

	// Dial with a deadline so ReadString can time out between events.
	transport := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			d := net.Dialer{Timeout: 2 * time.Second}
			conn, err := d.DialContext(ctx, network, addr)
			if err != nil {
				return nil, err
			}
			return &deadlineConn{Conn: conn}, nil
		},
	}
	client := &http.Client{Transport: transport}
	stream, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Body.Close()
	if stream.StatusCode != http.StatusOK {
		t.Fatalf("SSE status = %d", stream.StatusCode)
	}
	if ct := stream.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Fatalf("Content-Type = %q", ct)
	}
	for _, hdr := range []struct{ k, want string }{
		{"Cache-Control", "no-cache"},
		{"X-Accel-Buffering", "no"},
	} {
		if got := stream.Header.Get(hdr.k); got != hdr.want {
			t.Fatalf("%s = %q, want %q", hdr.k, got, hdr.want)
		}
	}

	reader := bufio.NewReader(stream.Body)
	event, data := readSSEEvent(t, reader, 2*time.Second)
	if event != "hello" {
		t.Fatalf("first event = %q, want hello", event)
	}
	var hello map[string]any
	if err := json.Unmarshal([]byte(data), &hello); err != nil {
		t.Fatal(err)
	}
	if hello["type"] != "hello" {
		t.Fatalf("hello payload = %#v", hello)
	}
	if _, ok := hello["ts"].(float64); !ok {
		t.Fatalf("hello ts missing: %#v", hello["ts"])
	}

	// Create a session on another request; expect session_created on the stream.
	go func() {
		time.Sleep(50 * time.Millisecond)
		body := bytes.NewBufferString(`{"title":"SSE Session"}`)
		creq, _ := http.NewRequest(http.MethodPost, base+"/api/sessions", body)
		creq.Header.Set("Authorization", "Bearer "+token)
		creq.Header.Set("Content-Type", "application/json")
		cresp, err := http.DefaultClient.Do(creq)
		if err != nil {
			t.Errorf("create: %v", err)
			return
		}
		io.Copy(io.Discard, cresp.Body)
		cresp.Body.Close()
	}()

	event, data = readSSEEvent(t, reader, 3*time.Second)
	if event != "session_created" {
		t.Fatalf("event = %q data=%s", event, data)
	}
	var created SessionEvent
	if err := json.Unmarshal([]byte(data), &created); err != nil {
		t.Fatal(err)
	}
	if created.Type != "session_created" || created.SessionID == "" {
		t.Fatalf("created = %#v", created)
	}
	if created.Title == nil || *created.Title != "SSE Session" {
		t.Fatalf("title = %#v", created.Title)
	}
	var raw map[string]any
	if err := json.Unmarshal([]byte(data), &raw); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"type", "session_id", "origin_channel", "message_id",
		"title", "message_count", "role", "ts",
	} {
		if _, ok := raw[key]; !ok {
			t.Fatalf("missing key %q in %#v", key, raw)
		}
	}
	cancel()
}

type deadlineConn struct {
	net.Conn
}

func (c *deadlineConn) Read(b []byte) (int, error) {
	_ = c.Conn.SetReadDeadline(time.Now().Add(150 * time.Millisecond))
	return c.Conn.Read(b)
}

func readSSEEvent(t *testing.T, r *bufio.Reader, timeout time.Duration) (event, data string) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	var eventName, dataLine string
	for time.Now().Before(deadline) {
		line, err := r.ReadString('\n')
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			if time.Now().Before(deadline) {
				continue
			}
			t.Fatalf("read SSE: %v (partial event=%q data=%q)", err, eventName, dataLine)
		}
		line = strings.TrimRight(line, "\r\n")
		if strings.HasPrefix(line, ":") {
			continue
		}
		if strings.HasPrefix(line, "event:") {
			eventName = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
			continue
		}
		if strings.HasPrefix(line, "data:") {
			dataLine = strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			continue
		}
		if line == "" && (eventName != "" || dataLine != "") {
			return eventName, dataLine
		}
	}
	t.Fatal("timed out waiting for SSE event")
	return "", ""
}
