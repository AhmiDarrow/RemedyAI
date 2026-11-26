package httpapi

import (
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/houserules"
)

// /house-rules is in the owner's manual, so the runtime has to answer it.
func TestSlashHouseRulesShowsAndRevisits(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Rules"})
	if err != nil {
		t.Fatal(err)
	}
	show, _ := s.executeSlashCommand(sess.ID, "/house-rules")["text"].(string)
	if !strings.HasPrefix(show, "**House rules**") {
		t.Fatalf("/house-rules: %q", show)
	}
	if alias, _ := s.executeSlashCommand(sess.ID, "/houserules")["text"].(string); alias != show {
		t.Fatalf("/houserules differs: %q", alias)
	}
	again, _ := s.executeSlashCommand(sess.ID, "/house-rules revisit")["text"].(string)
	if strings.TrimSpace(again) == "" || strings.Contains(again, "Could not") {
		t.Fatalf("/house-rules revisit: %q", again)
	}
	if !houseRulesReopened(t, s) {
		t.Fatal("revisit did not reopen the house-rules conversation")
	}
	bad, _ := s.executeSlashCommand(sess.ID, "/house-rules nonsense")["text"].(string)
	if !strings.Contains(bad, "takes revisit") {
		t.Fatalf("bad arg: %q", bad)
	}
}

// A paired phone can read the rules but not reopen them.
func TestSlashHouseRulesRevisitRefusedFromPhone(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Rules"})
	if err != nil {
		t.Fatal(err)
	}
	text, _ := s.executeSlashCommandFrom(sess.ID, "/house-rules revisit", true)["text"].(string)
	if !strings.Contains(text, "only be reopened on the PC") {
		t.Fatalf("phone revisit: %q", text)
	}
	if houseRulesReopened(t, s) {
		t.Fatal("phone reopened house rules")
	}
}

func houseRulesReopened(t *testing.T, s *Server) bool {
	t.Helper()
	out, err := houserules.Dispatch(s.houseRulesEnv(), map[string]any{"action": "status"})
	if err != nil {
		t.Fatal(err)
	}
	pending, _ := out["onboarding_pending"].(bool)
	return pending
}
