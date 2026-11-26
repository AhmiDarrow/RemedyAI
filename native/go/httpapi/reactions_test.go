package httpapi

import (
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

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func startReactionServer(t *testing.T, runner TurnRunner) (*Server, string, string, func()) {
	t.Helper()
	token := "test-token-not-a-secret-16"
	cfg := Config{
		Token: token, Version: "0.50.2",
		DBPath: filepath.Join(t.TempDir(), "memory.db"), HomeDir: t.TempDir(), TurnRunner: runner,
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Serve(ctx, ln); close(done) }()
	return s, "http://" + ln.Addr().String(), token, func() {
		if s.claims != nil {
			s.claims.AbortAll()
		}
		cancel()
		_ = ln.Close()
		select {
		case <-done:
		case <-time.After(10 * time.Second):
			t.Error("server did not shut down")
		}
	}
}

func putReaction(t *testing.T, base, token, sid, mid, emoji string) (int, map[string]any) {
	t.Helper()
	body, _ := json.Marshal(map[string]string{"emoji": emoji})
	req := authReq(t, http.MethodPut, base+"/api/sessions/"+sid+"/messages/"+mid+"/reaction", token, bytes.NewReader(body))
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	var out map[string]any
	_ = json.Unmarshal(raw, &out)
	return resp.StatusCode, out
}

func listedReactions(t *testing.T, s *Server, sid, mid string) *MessageReactions {
	t.Helper()
	msgs, err := s.sessions.ListMessages(sid, 50, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range msgs {
		if m.ID == mid {
			return m.Reactions
		}
	}
	t.Fatalf("message %s not listed", mid)
	return nil
}

func TestNormalizeReaction(t *testing.T) {
	cases := map[string]string{
		"👍": "👍", "👍🏽": "👍", "+1": "👍", "thumbs_down": "👎", "❤": "❤️", "❤️": "❤️",
		"heart": "❤️", "💔": "💔", "broken_heart": "💔", "": "",
	}
	for in, want := range cases {
		got, err := normalizeReaction(in)
		if err != nil || got != want {
			t.Fatalf("normalize(%q) = %q, %v; want %q", in, got, err, want)
		}
	}
	for _, bad := range []string{"😂", "🎉", "ok", "👍👍"} {
		if _, err := normalizeReaction(bad); err == nil {
			t.Fatalf("normalize(%q) should fail", bad)
		}
	}
}

func TestReactionPersistsTogglesAndRecordsFeedback(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	user, _ := s.sessions.AddMessage(sid, "user", "Can you fix the addon?", nil, nil)
	reply, _ := s.sessions.AddMessage(sid, "assistant", "Fixed: the loader points at init_main.lua now.", nil, nil)

	code, out := putReaction(t, base, token, sid, reply.ID, "❤️")
	if code != http.StatusOK || out["signal"] != "recorded" {
		t.Fatalf("set: %d %v", code, out)
	}
	if r := listedReactions(t, s, sid, reply.ID); r == nil || r.User != "❤️" || r.Agent != "" {
		t.Fatalf("persisted = %#v", r)
	}
	// Feedback memory entry the agent can consult.
	var title, meta string
	if err := s.sessions.db.QueryRow(
		`SELECT title, metadata FROM memory_entries WHERE id = ? AND entry_type = 'reaction_feedback'`,
		"reaction:"+reply.ID,
	).Scan(&title, &meta); err != nil {
		t.Fatalf("feedback entry: %v", err)
	}
	if !strings.Contains(title, "❤️") || !strings.Contains(meta, reply.ID) {
		t.Fatalf("feedback = %q %q", title, meta)
	}

	// Switching replaces (one per side); clearing removes the pill and the entry.
	if code, _ := putReaction(t, base, token, sid, reply.ID, "💔"); code != http.StatusOK {
		t.Fatal(code)
	}
	if r := listedReactions(t, s, sid, reply.ID); r == nil || r.User != "💔" {
		t.Fatalf("switched = %#v", r)
	}
	if code, out := putReaction(t, base, token, sid, reply.ID, ""); code != http.StatusOK || out["signal"] != "cleared" {
		t.Fatalf("clear: %d %v", code, out)
	}
	if r := listedReactions(t, s, sid, reply.ID); r != nil {
		t.Fatalf("cleared = %#v", r)
	}
	var n int
	_ = s.sessions.db.QueryRow(`SELECT COUNT(*) FROM memory_entries WHERE id = ?`, "reaction:"+reply.ID).Scan(&n)
	if n != 0 {
		t.Fatalf("feedback entry should be gone, have %d", n)
	}

	// The owner reacts to Remedy's messages, not their own.
	if code, _ := putReaction(t, base, token, sid, user.ID, "👍"); code != http.StatusBadRequest {
		t.Fatalf("react to own message: %d", code)
	}
	if code, _ := putReaction(t, base, token, sid, reply.ID, "🎉"); code != http.StatusBadRequest {
		t.Fatalf("invalid emoji: %d", code)
	}
	other := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	if code, _ := putReaction(t, base, token, other, reply.ID, "👍"); code != http.StatusNotFound {
		t.Fatalf("foreign session: %d", code)
	}
}

func TestReactionNeverStartsATurn(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	_, _ = s.sessions.AddMessage(sid, "user", "Plan the fix first.", nil, nil)
	reply, _ := s.sessions.AddMessage(sid, "assistant", "Plan: patch the manifest, then the loader. Go ahead?", nil, nil)
	before, _ := s.sessions.ListMessages(sid, 50, 0)
	if code, out := putReaction(t, base, token, sid, reply.ID, "👍"); code != http.StatusOK || out["signal"] != "recorded" {
		t.Fatalf("%d %v", code, out)
	}
	after, _ := s.sessions.ListMessages(sid, 50, 0)
	if len(after) != len(before) {
		t.Fatalf("a reaction added messages: %d -> %d", len(before), len(after))
	}
	if s.claims.IsClaimed(sid) {
		t.Fatal("a reaction claimed a turn")
	}
}

func TestReactionReachesRunningTurnAsQuietSignal(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	reply, _ := s.sessions.AddMessage(sid, "assistant", "Working on the loader next.", nil, nil)
	epoch, _, ok := s.claims.TryClaim(sid)
	if !ok {
		t.Fatal("claim")
	}
	defer s.claims.Release(sid, &epoch)
	code, out := putReaction(t, base, token, sid, reply.ID, "👎")
	if code != http.StatusOK || out["signal"] != "delivered" {
		t.Fatalf("%d %v", code, out)
	}
	nudges := s.claims.DrainNudges(sid)
	if len(nudges) != 1 || !strings.Contains(nudges[0], "not a new request") || !strings.Contains(nudges[0], "👎") {
		t.Fatalf("nudges = %q", nudges)
	}
}

func TestThumbsAnswerAPendingConfirmation(t *testing.T) {
	for _, tc := range []struct {
		emoji, signal, status string
		approved              bool
	}{
		{"👍", "confirmation_approved", "approved", true},
		{"👎", "confirmation_denied", "denied", false},
	} {
		t.Run(tc.emoji, func(t *testing.T) {
			s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
			defer shutdown()
			sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
			// The ask belongs to the turn running now; the owner reacts to
			// that turn's own reply row.
			release := startLiveTurn(t, s, sid, "req-confirm")
			defer release()
			reply, err := s.sessions.UpsertDraftMessage(sid, "req-confirm", "assistant", "I need to run the installer — OK?", nil, nil, nil, nil)
			if err != nil {
				t.Fatal(err)
			}
			sidCopy := sid
			item := s.approvals.Enqueue("bash", "installer.exe /quiet", "needs approval", &sidCopy, "")
			got := make(chan bool, 1)
			go func() {
				ok, _ := s.approvals.WaitAll(context.Background(), []string{item.ID})
				got <- ok
			}()
			deadline := time.Now().Add(2 * time.Second)
			for !s.approvals.HasWaiter(item.ID) && time.Now().Before(deadline) {
				time.Sleep(5 * time.Millisecond)
			}
			code, out := putReaction(t, base, token, sid, reply.ID, tc.emoji)
			if code != http.StatusOK || out["signal"] != tc.signal {
				t.Fatalf("%d %v", code, out)
			}
			select {
			case ok := <-got:
				if ok != tc.approved {
					t.Fatalf("waiter got %v", ok)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("waiter not released")
			}
			// "once": a thumbs-up is not standing consent for the next call.
			// The approved call itself runs once; the one after it asks again.
			if tc.approved {
				_ = s.approvals.IsApproved("bash", "installer.exe /quiet", sid, "", true)
			}
			if tc.approved && s.approvals.IsApproved("bash", "installer.exe /quiet", sid, "", true) {
				t.Fatal("thumbs-up must not grant the session")
			}
		})
	}
}

func TestReactionDoesNotAnswerOlderRepliesOrForeignAsks(t *testing.T) {
	s, base, token, shutdown := startReactionServer(t, &stubRunner{tokens: []string{"ok"}})
	defer shutdown()
	sid := createSessionID(t, &http.Client{Timeout: 5 * time.Second}, base, token)
	old, _ := s.sessions.AddMessage(sid, "assistant", "Earlier reply.", nil, nil)
	_, _ = s.sessions.AddMessage(sid, "assistant", "Need approval to run it.", nil, nil)
	sidCopy := sid
	item := s.approvals.EnqueueOrigin("bash", "x", "r", &sidCopy, "", "telegram:42")
	go func() { _, _ = s.approvals.WaitAll(context.Background(), []string{item.ID}) }()
	deadline := time.Now().Add(2 * time.Second)
	for !s.approvals.HasWaiter(item.ID) && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	// 👍 on an older reply only records.
	if _, out := putReaction(t, base, token, sid, old.ID, "👍"); out["signal"] != "recorded" {
		t.Fatalf("old reply: %v", out)
	}
	latest := s.sessions.LatestMessageID(sid, "assistant")
	// A messenger-originated ask is never answered by a reaction.
	if _, out := putReaction(t, base, token, sid, latest, "👍"); out["signal"] != "recorded" {
		t.Fatalf("foreign ask: %v", out)
	}
	s.approvals.Resolve(item.ID, false, "once")
}

func TestHistoryCarriesOwnerReactionsAsNotes(t *testing.T) {
	history := []map[string]any{
		{"role": "user", "content": "Plan it."},
		{"role": "assistant", "content": "Plan A.", "owner_reaction": "👎"},
		{"role": "user", "content": "Try again."},
		{"role": "assistant", "content": "Plan B.", "owner_reaction": "👍"},
	}
	msgs, pending := historyMessagesWithReactions(history)
	if len(msgs) != 4 {
		t.Fatalf("msgs = %d", len(msgs))
	}
	second := msgs[2].Blocks[0].Text
	if !strings.HasPrefix(second, "(Owner reacted 👎") || !strings.HasSuffix(second, "Try again.") {
		t.Fatalf("note on next user message = %q", second)
	}
	if !strings.Contains(pending, "👍") {
		t.Fatalf("pending = %q", pending)
	}
	seed := seedTranscript(TurnRequest{History: history}, "Ship it", t.TempDir())
	last := seed[len(seed)-1].Blocks[0].Text
	if !strings.HasPrefix(last, "(Owner reacted 👍") || !strings.HasSuffix(last, "Ship it") {
		t.Fatalf("current prompt = %q", last)
	}
	// Assistant turns are never rewritten.
	if msgs[1].Blocks[0].Text != "Plan A." {
		t.Fatalf("assistant text changed: %q", msgs[1].Blocks[0].Text)
	}
}

func TestReactionTokenFromReactTool(t *testing.T) {
	ok := cognition.ToolResult{Name: "react", Output: json.RawMessage(`{"emoji":"👍","message_id":""}`)}
	if tok := reactionToken(ok); !strings.HasPrefix(tok, "@@reaction:") || !strings.Contains(tok, "👍") {
		t.Fatalf("token = %q", tok)
	}
	if reactionToken(cognition.ToolResult{Name: "react", IsError: true, Output: ok.Output}) != "" {
		t.Fatal("failed call must not emit")
	}
	if reactionToken(cognition.ToolResult{Name: "todo", Output: ok.Output}) != "" {
		t.Fatal("other tools must not emit")
	}
}

// The agent's react tool lands on the owner message that started the turn.
func TestStreamAgentReactionPersistsOnUserMessage(t *testing.T) {
	runner := &stubRunner{tokens: []string{
		`@@reaction:{"emoji":"👍","message_id":""}`,
		"On it.",
	}}
	s, base, token, shutdown := startReactionServer(t, runner)
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"Use tabs from now on."}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body := readSSE(t, resp)
	resp.Body.Close()
	if !strings.Contains(body, "event: reaction") || !strings.Contains(body, `"side":"agent"`) {
		t.Fatalf("no reaction frame: %s", body)
	}
	userID := s.sessions.LatestMessageID(sid, "user")
	if r := listedReactions(t, s, sid, userID); r == nil || r.Agent != "👍" || r.User != "" {
		t.Fatalf("agent reaction = %#v", r)
	}
}
