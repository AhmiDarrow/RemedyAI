package httpapi

import (
	"context"
	"net/http"
	"testing"
	"time"
)

// waitForWaiter enqueues an owner ask and blocks a goroutine on it, the way a
// turn blocked on a confirmation does.
func waitForWaiter(t *testing.T, s *Server, sid, tool, command, reason string) (*pendingApproval, chan bool) {
	t.Helper()
	sidCopy := sid
	item := s.approvals.Enqueue(tool, command, reason, &sidCopy, "")
	got := make(chan bool, 1)
	go func() {
		ok, _ := s.approvals.WaitAll(context.Background(), []string{item.ID})
		got <- ok
	}()
	deadline := time.Now().Add(2 * time.Second)
	for !s.approvals.HasWaiter(item.ID) && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if !s.approvals.HasWaiter(item.ID) {
		t.Fatal("waiter never registered")
	}
	return item, got
}

// startLiveTurn claims the session and labels the claim with requestID, the
// way the stream handler does before the engine runs.
func startLiveTurn(t *testing.T, s *Server, sid, requestID string) func() {
	t.Helper()
	epoch, _, ok := s.claims.TryClaim(sid)
	if !ok {
		t.Fatal("claim")
	}
	s.claims.SetActiveRequest(sid, epoch, requestID)
	return func() { s.claims.Release(sid, &epoch) }
}

// Regression (red-team): a 👍 the owner leaves on the PREVIOUS turn's reply
// ("nice answer") must never approve a dangerous call the next turn is
// blocked on. The live reply is not even reactable in the desktop feed, so
// before the fix the newest persisted reply was always an unrelated one.
func TestReactionOnPreviousTurnReplyNeverApproves(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	_, _ = s.sessions.AddMessage(sid, "user", "summarize the build log", nil, nil)
	prev, _ := s.sessions.AddMessage(sid, "assistant", "Here is the summary you asked for.", nil, nil)
	_, _ = s.sessions.AddMessage(sid, "user", "now clean up the repo", nil, nil)
	release := startLiveTurn(t, s, sid, "req-live-1")
	defer release()
	item, got := waitForWaiter(t, s, sid, "bash", "rm -rf ./build ./dist", "Tool requires your approval")

	_, out := putReaction(t, base, token, sid, prev.ID, "👍")
	if sig, _ := out["signal"].(string); sig == "confirmation_approved" || sig == "confirmation_denied" {
		t.Fatalf("a reaction on an unrelated earlier reply answered the live ask: %v", out)
	}
	if cur := s.approvals.Get(item.ID); cur == nil || cur.Status != "pending" {
		t.Fatalf("ask must stay pending, got %+v", cur)
	}
	select {
	case ok := <-got:
		t.Fatalf("waiter released (%v) by an unrelated reaction", ok)
	case <-time.After(150 * time.Millisecond):
	}
	s.approvals.Resolve(item.ID, false, "once")
}

// A reaction on a reply that is not part of any running turn never answers
// an ask either (no claim ⇒ nothing the owner could be confirming).
func TestReactionWithoutLiveTurnNeverApproves(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	reply, _ := s.sessions.AddMessage(sid, "assistant", "Done.", nil, nil)
	item, _ := waitForWaiter(t, s, sid, "bash", "installer.exe /quiet", "Tool requires your approval")
	if _, out := putReaction(t, base, token, sid, reply.ID, "👍"); out["signal"] == "confirmation_approved" {
		t.Fatalf("approved with no live turn: %v", out)
	}
	if cur := s.approvals.Get(item.ID); cur == nil || cur.Status != "pending" {
		t.Fatalf("ask must stay pending, got %+v", cur)
	}
	s.approvals.Resolve(item.ID, false, "once")
}

// Regression (red-team): an Owner checkpoint (payments, credentials, other
// sensitive computer actions) needs the explicit banner. 👍 never approves
// one; 👎 may still deny it (the safe direction).
func TestThumbsUpNeverApprovesOwnerCheckpoint(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	release := startLiveTurn(t, s, sid, "req-live-2")
	defer release()
	draft, err := s.sessions.UpsertDraftMessage(sid, "req-live-2", "assistant", "I'll pay the invoice now — OK?", nil, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	item, got := waitForWaiter(t, s, sid, "pay_invoice", `{"amount":"4200"}`, sensitivePrefix+" — pay $4,200")
	if !item.Sensitive {
		t.Fatal("fixture: ask should be sensitive")
	}
	if _, out := putReaction(t, base, token, sid, draft.ID, "👍"); out["signal"] == "confirmation_approved" {
		t.Fatalf("👍 approved an owner checkpoint: %v", out)
	}
	if cur := s.approvals.Get(item.ID); cur == nil || cur.Status != "pending" {
		t.Fatalf("checkpoint must stay pending, got %+v", cur)
	}
	if _, out := putReaction(t, base, token, sid, draft.ID, "👎"); out["signal"] != "confirmation_denied" {
		t.Fatalf("👎 should deny the checkpoint: %v", out)
	}
	select {
	case ok := <-got:
		if ok {
			t.Fatal("checkpoint waiter approved")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waiter not released by 👎")
	}
}

// Two asks in flight make a single 👍 ambiguous, including when only one of
// them is an Owner checkpoint: nothing is approved.
func TestThumbsUpAmbiguousWithCheckpointApprovesNothing(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	release := startLiveTurn(t, s, sid, "req-live-3")
	defer release()
	draft, _ := s.sessions.UpsertDraftMessage(sid, "req-live-3", "assistant", "Two steps need you.", nil, nil, nil, nil)
	a, _ := waitForWaiter(t, s, sid, "bash", "npm install", "Tool requires your approval")
	b, _ := waitForWaiter(t, s, sid, "pay_invoice", `{"amount":"1"}`, sensitivePrefix+" — pay $1")
	if _, out := putReaction(t, base, token, sid, draft.ID, "👍"); out["signal"] == "confirmation_approved" {
		t.Fatalf("ambiguous 👍 approved something: %v", out)
	}
	for _, id := range []string{a.ID, b.ID} {
		if cur := s.approvals.Get(id); cur == nil || cur.Status != "pending" {
			t.Fatalf("%s must stay pending, got %+v", id, cur)
		}
		s.approvals.Resolve(id, false, "once")
	}
}

// A 👍 left before an ask exists is not consent for an ask that appears
// later, and a cleared reaction is gone: neither pre-approves anything.
func TestStaleOrClearedReactionNeverPreApproves(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	release := startLiveTurn(t, s, sid, "req-live-4")
	defer release()
	draft, _ := s.sessions.UpsertDraftMessage(sid, "req-live-4", "assistant", "Looking at the repo.", nil, nil, nil, nil)
	if _, out := putReaction(t, base, token, sid, draft.ID, "👍"); out["signal"] == "confirmation_approved" {
		t.Fatalf("approved with no ask: %v", out)
	}
	item, got := waitForWaiter(t, s, sid, "bash", "git clean -fdx", "Tool requires your approval")
	// Re-sending the same reaction is "unchanged" and answers nothing.
	if _, out := putReaction(t, base, token, sid, draft.ID, "👍"); out["signal"] != "unchanged" {
		t.Fatalf("repeat 👍 should be unchanged: %v", out)
	}
	// Clearing is a revocation of the reaction, never an answer.
	if _, out := putReaction(t, base, token, sid, draft.ID, ""); out["signal"] != "cleared" {
		t.Fatalf("clear: %v", out)
	}
	select {
	case ok := <-got:
		t.Fatalf("waiter released (%v) by a stale reaction", ok)
	case <-time.After(150 * time.Millisecond):
	}
	if cur := s.approvals.Get(item.ID); cur == nil || cur.Status != "pending" {
		t.Fatalf("ask must stay pending, got %+v", cur)
	}
	s.approvals.Resolve(item.ID, false, "once")
}
