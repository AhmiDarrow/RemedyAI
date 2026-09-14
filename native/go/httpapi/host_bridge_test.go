package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestHostBridgeEnqueueClaimComplete(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	job := b.Enqueue("navigate", map[string]any{"url": "https://example.com"}, "sess-1")
	if job == nil || job.ID == "" || job.Status != "pending" {
		t.Fatalf("enqueue job=%v", job)
	}
	cmd := b.peekUICommand()
	if cmd["action"] != "open_browser" || cmd["job_id"] != job.ID {
		t.Fatalf("ui_command=%v", cmd)
	}
	raw, err := os.ReadFile(filepath.Join(home, "computer", "ui_command.json"))
	if err != nil {
		t.Fatal(err)
	}
	var disk map[string]any
	if json.Unmarshal(raw, &disk) != nil || disk["action"] != "open_browser" {
		t.Fatalf("disk ui=%s", raw)
	}

	claimed := b.claimNext(nil, nil, "sess-1", 0)
	if claimed == nil || claimed.ID != job.ID || claimed.Status != "running" {
		t.Fatalf("claim=%v", claimed)
	}
	done := b.complete(job.ID, true, map[string]any{"ok": true, "url": "https://example.com/"}, nil)
	if done == nil || done.Status != "done" {
		t.Fatalf("complete=%v", done)
	}
	if got := b.lastObservedURLFor("sess-1"); got != "https://example.com/" {
		t.Fatalf("observed=%q", got)
	}
}

func TestHostBridgeCompleteWithoutURLDoesNotClaimNavigation(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	job := b.Enqueue("navigate", map[string]any{"url": "https://shop.example/checkout"}, "sess-1")
	b.claimNext(nil, nil, "sess-1", 0)
	done := b.complete(job.ID, true, map[string]any{"ok": true}, nil)
	if done == nil || done.Status != "done" {
		t.Fatalf("complete=%v", done)
	}
	if got := b.lastObservedURLFor("sess-1"); got != "" {
		t.Fatalf("missing observed url must not fall back to the requested url, got %q", got)
	}
}

func TestHostBridgeWaitTimeoutUnclaimed(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	job := b.Enqueue("snapshot", map[string]any{"ui": map[string]any{"open_browser": true}}, "")
	// Clear UI so unclaimed timeout can fire.
	b.clearUICommand(nil)
	limit := 0.6
	done := b.Wait(job.ID, WaitOptions{TimeoutS: 2, UnclaimedTimeoutS: &limit, PollS: 0.05})
	if done == nil || done.Status != "error" || done.Error == nil {
		t.Fatalf("wait=%v", done)
	}
	if !containsFold(*done.Error, "did not claim") {
		t.Fatalf("error=%q", *done.Error)
	}
}

func TestHostBridgeWaitCompletes(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	job := b.Enqueue("navigate", map[string]any{"url": "https://example.com"}, "s")
	go func() {
		time.Sleep(80 * time.Millisecond)
		_ = b.claimNext(nil, nil, "s", 0)
		b.complete(job.ID, true, map[string]any{"ok": true, "url": "https://example.com"}, nil)
	}()
	done := b.Wait(job.ID, WaitOptions{TimeoutS: 3, UnclaimedTimeoutS: nil, PollS: 0.02})
	if done == nil || done.Status != "done" {
		t.Fatalf("wait=%v", done)
	}
}

func TestHostBridgeCancel(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	job := b.Enqueue("page_text", map[string]any{}, "s")
	got := b.cancel(job.ID)
	if got == nil || got.Status != "cancelled" {
		t.Fatalf("cancel=%v", got)
	}
}

func TestLivePageContextDoesNotBleedLabelsAcrossSessions(t *testing.T) {
	home := t.TempDir()
	b := newHostBridge(home)
	a := b.Enqueue("snapshot", map[string]any{}, "sess-a")
	if b.completeA11yPush(a.ID, []map[string]any{{"name": "Place order"}}) == nil {
		t.Fatal("a11y a")
	}
	c := b.Enqueue("snapshot", map[string]any{}, "sess-b")
	if b.completeA11yPush(c.ID, []map[string]any{{"name": "Search"}}) == nil {
		t.Fatal("a11y b")
	}
	gotA := b.livePageContext("sess-a")
	gotB := b.livePageContext("sess-b")
	if !strings.Contains(gotA, "Place order") || strings.Contains(gotA, "Search") {
		t.Fatalf("sess-a context=%q", gotA)
	}
	if !strings.Contains(gotB, "Search") || strings.Contains(gotB, "Place order") {
		t.Fatalf("sess-b context=%q", gotB)
	}
}

func containsFold(s, sub string) bool {
	return strings.Contains(strings.ToLower(s), strings.ToLower(sub))
}
