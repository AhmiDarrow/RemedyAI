package httpapi

import (
	"strings"
	"testing"
)

func TestSlashModelArgTakesTheCommandInItsSpellings(t *testing.T) {
	for in, want := range map[string]string{
		"/model":                "",
		"/models":               "",
		"/m":                    "",
		"/MODEL  fable 5.2 ":    "fable 5.2",
		"/model anthropic/opus": "anthropic/opus",
		"  /models   sonnet":    "sonnet",
	} {
		got, ok := slashModelArg(strings.TrimSpace(in))
		if !ok || got != want {
			t.Errorf("%q -> %q, %v; want %q", in, got, ok, want)
		}
	}
	for _, other := range []string{"/modeling", "/memory x", "model", ""} {
		if _, ok := slashModelArg(other); ok {
			t.Errorf("%q is not the model command", other)
		}
	}
}

func TestMatchModelsFindsANameTheWayAPersonTypesIt(t *testing.T) {
	ids := []string{"claude-fable-5-2", "claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5"}
	one := func(want string) string {
		t.Helper()
		got := matchModels(ids, want)
		if len(got) != 1 {
			t.Fatalf("%q matched %v", want, got)
		}
		return got[0]
	}
	if one("fable") != "claude-fable-5-2" || one("Fable 5.2") != "claude-fable-5-2" || one("claude-opus-5-5") != "claude-opus-5-5" {
		t.Fatal("part of a name, in any case and with any separator, finds the model")
	}
	// An exact id wins over the longer ids that contain it.
	if one("claude-sonnet-5") != "claude-sonnet-5" {
		t.Fatal("an exact id is that model")
	}
	if got := matchModels(ids, "sonnet"); len(got) != 2 {
		t.Fatalf("two sonnets: %v", got)
	}
	if got := matchModels(ids, "gpt"); len(got) != 0 {
		t.Fatalf("no such model: %v", got)
	}
	if got := matchModels(ids, " "); got != nil {
		t.Fatalf("nothing asked: %v", got)
	}
}

func TestSlashModelIsARealCommandNow(t *testing.T) {
	s, _ := newPartnerTestServer(t)
	out := s.executeSlashCommand("", "/model")
	text, _ := out["text"].(string)
	if text == "" || strings.Contains(text, "GET /api/models") {
		t.Fatalf("/model must answer in plain words, not point at an API: %q", text)
	}
	help, _ := s.executeSlashCommand("", "/help")["text"].(string)
	if !strings.Contains(help, "/model") {
		t.Fatalf("help lists /model: %q", help)
	}
}
