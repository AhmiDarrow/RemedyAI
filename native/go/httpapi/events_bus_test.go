package httpapi

import (
	"bufio"
	"context"
	"encoding/json"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
	"github.com/AhmiDarrow/RemedyAI/native/go/scheduler"
)

func TestEventsReplayAndStream(t *testing.T) {
	home := t.TempDir()
	base, shutdown := startTestServer(t, Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	defer shutdown()

	client := &http.Client{Timeout: 5 * time.Second}
	req, _ := http.NewRequest(http.MethodPost, base+"/api/sessions", strings.NewReader(`{"title":"bus"}`))
	req.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		t.Fatalf("create session status=%d", resp.StatusCode)
	}

	req, _ = http.NewRequest(http.MethodGet, base+"/api/events?from=1&limit=10", nil)
	req.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var replay map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&replay); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if replay["ok"] != true {
		t.Fatalf("replay = %#v", replay)
	}
	count, _ := replay["count"].(float64)
	if count < 1 {
		t.Fatalf("expected durable session event, got %#v", replay)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	streamReq, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/api/events/stream", nil)
	if err != nil {
		t.Fatal(err)
	}
	streamReq.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
	stream, err := http.DefaultClient.Do(streamReq)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Body.Close()
	if ct := stream.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Fatalf("content-type=%q", ct)
	}
	reader := bufio.NewReader(stream.Body)
	event, _ := readSSEEvent(t, reader, 2*time.Second)
	if event != "hello" {
		t.Fatalf("first stream event=%q", event)
	}
}

func TestPublishBusEventWakesScheduler(t *testing.T) {
	home := t.TempDir()
	s, err := New(Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()

	if err := s.sched.Add(scheduler.Job{
		ID:        "wake-me",
		Trigger:   scheduler.OnEvent,
		EventType: "Wake",
		Status:    scheduler.Pending,
	}); err != nil {
		t.Fatal(err)
	}
	s.publishBusEvent(events.Event{Type: "Wake", Source: "test"})
	done := s.sched.Tick(context.Background(), time.Now())
	if len(done) != 1 || done[0].ID != "wake-me" {
		t.Fatalf("scheduler tick = %#v", done)
	}
}
