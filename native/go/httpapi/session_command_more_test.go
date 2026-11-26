package httpapi

import (
	"strings"
	"testing"
)

// Every command answers in words a person on a phone can use: none sends them
// to a panel or an API, and none is unknown.
func TestEverySlashCommandDoesSomething(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Trip"})
	if err != nil {
		t.Fatal(err)
	}
	all := append(append([]map[string]any{}, builtinSlashCommands...), moreSlashCommands...)
	if len(all) < 20 {
		t.Fatalf("only %d commands", len(all))
	}
	for _, c := range all {
		name, _ := c["name"].(string)
		if name == "/new" || name == "/reset" || name == "/export" {
			continue // these hand the screen an action; covered elsewhere
		}
		out := s.executeSlashCommand(sess.ID, name)
		text, _ := out["text"].(string)
		if strings.TrimSpace(text) == "" {
			t.Errorf("%s says nothing", name)
		}
		if name == "/help" {
			continue // its keyboard list mentions panels by name
		}
		for _, bad := range []string{"GET /api", "Unknown command", " panel"} {
			if strings.Contains(text, bad) {
				t.Errorf("%s answers with %q: %s", name, bad, text)
			}
		}
	}
	help, _ := s.executeSlashCommand(sess.ID, "/help")["text"].(string)
	for _, want := range []string{"/model", "/stop", "/status", "/think", "/search", "/approve", "/plan", "/remember"} {
		if !strings.Contains(help, want) {
			t.Errorf("help does not list %s", want)
		}
	}
}

func TestSlashCommandsThatChangeThings(t *testing.T) {
	s, home := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Trip"})
	if err != nil {
		t.Fatal(err)
	}
	run := func(cmd string) (string, string) {
		t.Helper()
		out := s.executeSlashCommand(sess.ID, cmd)
		text, _ := out["text"].(string)
		action, _ := out["action"].(string)
		return text, action
	}

	if text, _ := run("/stop"); !strings.Contains(text, "Nothing is being written") {
		t.Fatalf("/stop with nothing running: %s", text)
	}

	if text, action := run("/think low"); action != "settings_changed" || !strings.Contains(text, "low") {
		t.Fatalf("/think low: %s (%s)", text, action)
	}
	InvalidateConfigCache()
	if got := cfgString(LoadConfig(home), "thinking_level", ""); got != "low" {
		t.Fatalf("thinking_level saved as %q", got)
	}
	if text, action := run("/think loud"); action != "" || !strings.Contains(text, "off, low, medium or high") {
		t.Fatalf("/think with a wrong word: %s", text)
	}

	if _, action := run("/rename Lisbon weekend"); action != "renamed" {
		t.Fatal("/rename")
	}
	if got, _, _ := s.sessions.Get(sess.ID); got.Title != "Lisbon weekend" {
		t.Fatalf("title = %q", got.Title)
	}

	if text, _ := run("/remember the cat is called Miso"); !strings.Contains(text, "Miso") {
		t.Fatalf("/remember: %s", text)
	}
	if text, _ := run("/memory Miso"); !strings.Contains(text, "Miso") {
		t.Fatalf("/memory finds what /remember saved: %s", text)
	}

	if _, action := run("/goal Learn to bake bread"); action != "goal_added" {
		t.Fatal("/goal")
	}
	if text, _ := run("/goals"); !strings.Contains(text, "Learn to bake bread") {
		t.Fatalf("/goals lists it: %s", text)
	}

	if _, err := s.sessions.AddMessage(sess.ID, "user", "the router keeps dropping the port", nil, nil); err != nil {
		t.Fatal(err)
	}
	if text, _ := run("/search router port"); !strings.Contains(text, "Lisbon weekend") {
		t.Fatalf("/search finds the conversation: %s", text)
	}

	if text, _ := run("/approve"); !strings.Contains(text, "Nothing is waiting") {
		t.Fatalf("/approve with nothing waiting: %s", text)
	}
	sid := sess.ID
	item := s.approvals.Enqueue("bash", `{"command":"echo hi"}`, "test", &sid, "Run echo hi")
	if text, action := run("/approve"); action != "approval_resolved" || !strings.Contains(text, "Approved") {
		t.Fatalf("/approve the one waiting: %s", text)
	}
	if got := s.approvals.Get(item.ID); got == nil || got.Status != "approved" {
		t.Fatalf("approval status: %+v", got)
	}
	other := s.approvals.Enqueue("bash", `{"command":"echo no"}`, "test", &sid, "Run echo no")
	if _, action := run("/deny " + other.ID[:6]); action != "approval_resolved" {
		t.Fatal("/deny by the start of the id")
	}
	if got := s.approvals.Get(other.ID); got == nil || got.Status != "denied" {
		t.Fatalf("denied status: %+v", got)
	}

	if text, _ := run("/status"); !strings.Contains(text, "Thinking: low") || !strings.Contains(text, "not working") {
		t.Fatalf("/status: %s", text)
	}
	if text, _ := run("/plan"); !strings.Contains(text, "no plan") {
		t.Fatalf("/plan with none: %s", text)
	}
}

// A phone gets through a command only what its panes allow: with the settings
// pane off, /think cannot change a setting, and with approvals off /approve
// cannot answer for the owner.
func TestCommandsFromAPhoneObeyItsPanes(t *testing.T) {
	s, home := newPartnerTestServer(t)
	sess, err := s.sessions.Create(createSessionRequest{Title: "Trip"})
	if err != nil {
		t.Fatal(err)
	}
	out := s.executeSlashCommandFrom(sess.ID, "/think low", true)
	if text, _ := out["text"].(string); !strings.Contains(text, "turned off for the phone") {
		t.Fatalf("settings pane is off by default: %s", text)
	}
	InvalidateConfigCache()
	if got := cfgString(LoadConfig(home), "thinking_level", "unset"); got == "low" {
		t.Fatal("the phone changed a setting through a command")
	}
	// The same command from the PC works.
	if out := s.executeSlashCommandFrom(sess.ID, "/think low", false); out["action"] != "settings_changed" {
		t.Fatalf("from the PC: %v", out)
	}
	// Reading commands work from the phone.
	if text, _ := s.executeSlashCommandFrom(sess.ID, "/status", true)["text"].(string); !strings.Contains(text, "Thinking") {
		t.Fatalf("/status from the phone: %s", text)
	}
}
