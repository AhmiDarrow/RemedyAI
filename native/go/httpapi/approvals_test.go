package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestApprovalPruneKeepsPendingAndWaiters(t *testing.T) {
	q := newApprovalQueue()
	clock := time.Unix(1_000_000, 0)
	q.now = func() time.Time { return clock }
	sid := "s-prune"

	pending := q.Enqueue("shell.exec", `{"argv":["a"]}`, "test", &sid, "")
	resolved := q.Enqueue("shell.exec", `{"argv":["b"]}`, "test", &sid, "")
	waited := q.Enqueue("shell.exec", `{"argv":["c"]}`, "test", &sid, "")
	_ = q.Resolve(resolved.ID, true, "session")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	waitDone := make(chan struct{})
	go func() {
		defer close(waitDone)
		_, _ = q.WaitAll(ctx, []string{waited.ID})
	}()
	deadline := time.Now().Add(2 * time.Second)
	for !q.HasWaiter(waited.ID) && time.Now().Before(deadline) {
		time.Sleep(2 * time.Millisecond)
	}
	if !q.HasWaiter(waited.ID) {
		t.Fatal("waiter never registered")
	}

	// Two hours later a new enqueue triggers pruning.
	clock = clock.Add(2 * time.Hour)
	_ = q.Enqueue("shell.exec", `{"argv":["d"]}`, "test", &sid, "")

	if q.Get(pending.ID) == nil {
		t.Fatal("pending item was pruned")
	}
	if q.Get(waited.ID) == nil {
		t.Fatal("item with a live waiter was pruned")
	}
	if q.Get(resolved.ID) != nil {
		t.Fatal("resolved item older than retention should be pruned")
	}
	cancel()
	<-waitDone
}

func TestApprovalSetModeWakesWaitersAndSkipsSensitive(t *testing.T) {
	q := newApprovalQueue()
	sid := "s-mode"
	plain := q.Enqueue("shell.exec", `{"argv":["C:\\x\\build.exe"]}`, "Tool requires your approval", &sid, "")
	sensitive := q.Enqueue("mail.send", `{"to":"a@b"}`, sensitivePrefix+" — send", &sid, "")

	type outcome struct {
		ok  bool
		err error
	}
	got := make(chan outcome, 1)
	go func() {
		ok, err := q.WaitAll(context.Background(), []string{plain.ID})
		got <- outcome{ok, err}
	}()
	deadline := time.Now().Add(2 * time.Second)
	for !q.HasWaiter(plain.ID) && time.Now().Before(deadline) {
		time.Sleep(2 * time.Millisecond)
	}
	if !q.HasWaiter(plain.ID) {
		t.Fatal("waiter never registered")
	}

	q.SetMode("auto")

	select {
	case res := <-got:
		if res.err != nil || !res.ok {
			t.Fatalf("WaitAll after SetMode(auto): ok=%v err=%v", res.ok, res.err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("SetMode(auto) did not wake the blocked waiter")
	}
	if q.Get(plain.ID).Status != "approved" {
		t.Fatalf("plain status=%q", q.Get(plain.ID).Status)
	}
	if q.Get(sensitive.ID).Status != "pending" {
		t.Fatalf("sensitive item must survive a mode sweep: %q", q.Get(sensitive.ID).Status)
	}
}

func TestResolveApprovalRequiresExplicitBoolean(t *testing.T) {
	s := newToolsAPIServer(t)
	sid := "s-resolve"
	item := s.approvals.Enqueue("shell.exec", `{"argv":["x"]}`, "test", &sid, "")

	for _, body := range []string{"", "{}", `{"approve":"yes"}`, `{"scope":"session"}`, "not json"} {
		code, raw := doTools(t, s, http.MethodPost, "/api/approvals/"+item.ID+"/resolve", body)
		if code != http.StatusBadRequest {
			t.Fatalf("body=%q status=%d body=%s want 400", body, code, raw)
		}
		if s.approvals.Get(item.ID).Status != "pending" {
			t.Fatalf("body=%q resolved the item without an explicit decision", body)
		}
	}

	code, raw := doTools(t, s, http.MethodPost, "/api/approvals/"+item.ID+"/resolve", `{"approve":false}`)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "denied" {
		t.Fatalf("status=%v want denied", out["status"])
	}
}

func TestOneShotGrantCoversIdenticalBatchCalls(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	clock := time.Unix(2_000_000, 0)
	q.now = func() time.Time { return clock }
	sid := "s-batch"
	_ = q.SetMode("full") // no mode waives an owner moment

	input := []byte(`{"x":10,"y":20,"label":"Place order","page_context":"https://shop.example/checkout"}`)
	callA := cognition.ToolCall{ID: "call-a", Name: "computer.click", Input: input}
	callB := cognition.ToolCall{ID: "call-b", Name: "computer.click", Input: input}
	p := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: sid}

	if d := p.Decide(context.Background(), callA); d != cognition.Ask {
		t.Fatalf("sensitive click in full mode Decide=%v want Ask", d)
	}
	itemA := enqueueToolApproval(q, reg, sid, callA)
	itemB := enqueueToolApproval(q, reg, sid, callB)
	if itemA.ID != itemB.ID {
		t.Fatalf("identical batch calls should share one banner item: %s vs %s", itemA.ID, itemB.ID)
	}
	if !itemA.Sensitive {
		t.Fatal("Place order click must be a sensitive item")
	}
	if len(q.ListPending(sid)) != 1 {
		t.Fatalf("pending=%d want 1", len(q.ListPending(sid)))
	}

	if q.Get(itemA.ID).Status != "pending" {
		t.Fatalf("status=%q want pending", q.Get(itemA.ID).Status)
	}
	_ = q.Resolve(itemA.ID, true, "always") // "always" never becomes standing consent for sensitive

	if d := p.Decide(context.Background(), callA); d != cognition.Allow {
		t.Fatalf("call A after approval Decide=%v want Allow", d)
	}
	if d := p.Decide(context.Background(), callA); d != cognition.Ask {
		t.Fatalf("call A must not consume twice: Decide=%v want Ask", d)
	}
	if d := p.Decide(context.Background(), callB); d != cognition.Allow {
		t.Fatalf("call B after approval Decide=%v want Allow", d)
	}
	callC := cognition.ToolCall{ID: "call-c", Name: "computer.click", Input: input}
	if d := p.Decide(context.Background(), callC); d != cognition.Ask {
		t.Fatalf("third identical call must ask again: Decide=%v", d)
	}
	if q.IsApproved("computer.click", toolCommandPreview(callC), sid) {
		t.Fatal("sensitive approval leaked into standing session/always consent")
	}

	// Unused grants expire.
	itemD := enqueueToolApproval(q, reg, sid, callC)
	_ = q.Resolve(itemD.ID, true, "session")
	clock = clock.Add(oneShotGrantTTL + time.Second)
	if d := p.Decide(context.Background(), callC); d != cognition.Ask {
		t.Fatalf("expired one-shot grant still allowed: Decide=%v", d)
	}
}

func TestWaitAllDeniedDoesNotHangOnDuplicateIDs(t *testing.T) {
	q := newApprovalQueue()
	sid := "s-dup"
	item := q.Enqueue("shell.exec", `{"argv":["x"]}`, "test", &sid, "")
	done := make(chan bool, 1)
	go func() {
		ok, _ := q.WaitAll(context.Background(), []string{item.ID, item.ID})
		done <- ok
	}()
	deadline := time.Now().Add(2 * time.Second)
	for !q.HasWaiter(item.ID) && time.Now().Before(deadline) {
		time.Sleep(2 * time.Millisecond)
	}
	_ = q.Resolve(item.ID, false, "session")
	select {
	case ok := <-done:
		if ok {
			t.Fatal("denied item reported approved")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("WaitAll hung on duplicate ids")
	}
}

func TestApprovalPublicCommandClipIsRuneSafe(t *testing.T) {
	q := newApprovalQueue()
	cmd := strings.Repeat("€", 300) // 900 bytes; a 500-byte clip lands mid-rune
	item := q.Enqueue("shell.exec", cmd, "test", nil, "")
	pub := q.ToPublic(item)
	got, _ := pub["command"].(string)
	if len(got) != 498 || !strings.HasSuffix(got, "€") {
		t.Fatalf("clip len=%d want 498 ending on a whole rune", len(got))
	}
	for _, r := range got {
		if r == '\uFFFD' {
			t.Fatal("clip split a rune")
		}
	}
}
