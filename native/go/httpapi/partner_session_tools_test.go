package httpapi

import (
	"context"
	"strings"
	"testing"
)

func TestRemedyStartsSeesAndContinuesStudioSessions(t *testing.T) {
	runner := &stubRunner{tokens: []string{"@@text:Built it and the tests pass.\n", "@@text:STATUS: done — all green"}}
	s := newHeadTestServer(t, runner)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.startPartnerDispatcher(ctx)
	head, err := s.partnerEnsureHeadSession()
	if err != nil {
		t.Fatal(err)
	}
	proj := t.TempDir()

	// She starts a build session for a job; the owner sees it in Studio.
	out, err := toolSessionsStart(s, partnerToolInput{
		SessionID: head, CallerOrigin: "desktop",
		Title: "Fix the parser", Task: "Fix the failing parser test", ProjectPath: proj, Mode: "build",
	})
	if err != nil {
		t.Fatal(err)
	}
	started := out.(map[string]any)["session"].(map[string]any)
	sid, _ := started["session_id"].(string)
	sess, ok, err := s.sessions.Get(sid)
	if err != nil || !ok {
		t.Fatalf("started session must exist: %v", err)
	}
	if sess.OriginChannel != nil && *sess.OriginChannel != "" {
		t.Fatalf("a session she starts for Studio must not be a Grove-only one (origin %q)", *sess.OriginChannel)
	}
	if sess.Title != "Fix the parser" || sess.ProjectPath == nil || *sess.ProjectPath != proj {
		t.Fatalf("title/project: %+v", sess)
	}
	waitFor(t, "the session to report", func() bool {
		s.partner.mu.Lock()
		defer s.partner.mu.Unlock()
		c := s.partner.st.Children[sid]
		return c != nil && c.State == partnerStateDone
	})

	// The owner has a session of their own in Studio.
	mine, err := s.sessions.Create(createSessionRequest{Title: "My refactor"})
	if err != nil {
		t.Fatal(err)
	}

	listed, err := toolSessionsList(s, partnerToolInput{})
	if err != nil {
		t.Fatal(err)
	}
	where := map[string]string{}
	for _, row := range listed.(map[string]any)["sessions"].([]map[string]any) {
		where[row["session_id"].(string)], _ = row["where"].(string)
	}
	if !strings.HasPrefix(where[sid], "studio") || where[mine.ID] != "studio" || !strings.HasPrefix(where[head], "grove") {
		t.Fatalf("she must see her session, the owner's and her own conversation: %v", where)
	}

	read, err := toolSessionsRead(s, partnerToolInput{Target: sid})
	if err != nil {
		t.Fatal(err)
	}
	if msgs := read.(map[string]any)["messages"].([]map[string]any); len(msgs) == 0 {
		t.Fatal("reading a session returns its messages")
	}

	// She carries on the owner's own session, which then reports to her.
	if _, err := toolSessionsContinue(s, partnerToolInput{SessionID: head, CallerOrigin: "desktop", Target: mine.ID, Text: "keep going"}); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "the owner's session to report", func() bool {
		s.partner.mu.Lock()
		defer s.partner.mu.Unlock()
		c := s.partner.st.Children[mine.ID]
		return c != nil && c.State == partnerStateDone
	})

	// Refusals: her own conversation, an unknown id, and a helper trying it.
	if _, err := toolSessionsContinue(s, partnerToolInput{SessionID: head, Target: head, Text: "x"}); err == nil {
		t.Fatal("continuing her own conversation must be refused")
	}
	if _, err := toolSessionsRead(s, partnerToolInput{Target: "nope"}); err == nil {
		t.Fatal("an unknown session id must be an error")
	}
	if _, err := toolSessionsContinue(s, partnerToolInput{CallerOrigin: "partner:child:" + head, Target: mine.ID, Text: "x"}); err == nil {
		t.Fatal("a helper must not direct other sessions")
	}
	if _, err := toolSessionsStop(s, partnerToolInput{CallerOrigin: "partner:child:" + head, Target: mine.ID}); err == nil {
		t.Fatal("a helper must not stop other sessions")
	}
	if _, err := toolSessionsStart(s, partnerToolInput{CallerOrigin: "partner:child:" + head, Task: "x"}); err == nil {
		t.Fatal("a helper must not start sessions")
	}
}

func TestPartnerRunModeFollowsTheRequest(t *testing.T) {
	for in, want := range map[string]string{"": "", "build": "", "Plan": "plan", "chat": "chat", "talk": "chat", "nonsense": ""} {
		if got := partnerRunMode(in); got != want {
			t.Errorf("partnerRunMode(%q) = %q, want %q", in, got, want)
		}
	}
}
