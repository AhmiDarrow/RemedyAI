package httpapi

import (
	"strings"
	"testing"
)

func TestSessionsSearchFindsATalkByItsWordsAndByItsTitle(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	mk := func(title string, lines ...string) string {
		t.Helper()
		sess, err := s.sessions.Create(createSessionRequest{Title: title})
		if err != nil {
			t.Fatal(err)
		}
		for i, line := range lines {
			role := "user"
			if i%2 == 1 {
				role = "assistant"
			}
			if _, err := s.sessions.AddMessage(sess.ID, role, line, nil, nil); err != nil {
				t.Fatal(err)
			}
		}
		return sess.ID
	}
	router := mk("Home network", "the router keeps dropping the forwarded port 7403", "Your router's lease ran out; I renewed it.")
	taxes := mk("Tax receipts 2026", "scan these", "Done, 14 receipts filed.")
	_ = mk("Garden", "when do I plant 100% of the beans_and_peas?", "After the last frost.")

	out, err := toolSessionsSearch(s, partnerToolInput{Query: "Router PORT"})
	if err != nil {
		t.Fatal(err)
	}
	rows := out.(map[string]any)["sessions"].([]map[string]any)
	if len(rows) != 1 || rows[0]["session_id"] != router {
		t.Fatalf("every word must match, in any case: %v", rows)
	}
	snips := rows[0]["snippets"].([]map[string]any)
	if len(snips) == 0 || !strings.Contains(strings.ToLower(snips[0]["text"].(string)), "router") {
		t.Fatalf("a result shows the talk that matched: %v", snips)
	}

	// A title is enough, with no message saying the words.
	out, err = toolSessionsSearch(s, partnerToolInput{Query: "tax receipts"})
	if err != nil {
		t.Fatal(err)
	}
	rows = out.(map[string]any)["sessions"].([]map[string]any)
	if len(rows) == 0 || rows[0]["session_id"] != taxes || rows[0]["title_matches"] != true {
		t.Fatalf("found by title: %v", rows)
	}

	// % and _ in the owner's words are letters, not wildcards.
	out, _ = toolSessionsSearch(s, partnerToolInput{Query: "100% beans_and_peas"})
	if rows = out.(map[string]any)["sessions"].([]map[string]any); len(rows) != 1 {
		t.Fatalf("literal %% and _: %v", rows)
	}
	out, _ = toolSessionsSearch(s, partnerToolInput{Query: "10_% xx"})
	if rows = out.(map[string]any)["sessions"].([]map[string]any); len(rows) != 0 {
		t.Fatalf("wildcards must not match everything: %v", rows)
	}
	if _, err := toolSessionsSearch(s, partnerToolInput{Query: " . "}); err == nil {
		t.Fatal("an empty question is refused")
	}
}

func TestSnippetShowsTheWordsInTheirSurroundings(t *testing.T) {
	text := strings.Repeat("filler words here. ", 40) + "Then the ROUTER was restarted.\n" + strings.Repeat(" more after.", 40)
	got := snippetAround(text, []string{"router"}, 120)
	if !strings.Contains(got, "ROUTER") || len([]rune(got)) > 124 || !strings.HasPrefix(got, "…") {
		t.Fatalf("snippet = %q", got)
	}
}
