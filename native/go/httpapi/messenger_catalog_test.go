package httpapi

import "testing"

func TestMessengerCatalogEmbeddedJSON(t *testing.T) {
	if len(messengerCatalog) < 9 {
		t.Fatalf("catalog len=%d", len(messengerCatalog))
	}
	seen := map[string]bool{}
	for _, e := range messengerCatalog {
		if e.ID == "" || len(e.Fields) == 0 {
			t.Fatalf("bad entry %#v", e)
		}
		seen[e.ID] = true
	}
	for _, need := range []string{
		"telegram", "discord", "slack", "mattermost", "whatsapp",
		"teams", "matrix", "google_chat", "signal",
	} {
		if !seen[need] {
			t.Fatalf("missing %s", need)
		}
	}
}
