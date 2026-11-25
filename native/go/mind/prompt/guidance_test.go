package prompt

import (
	"os"
	"regexp"
	"strings"
	"testing"
	"unicode/utf8"
)

// pySourceText reads a triple-quoted constant's source from the Python tree.
func pySourceText(t *testing.T, rel, name string) string {
	t.Helper()
	b, err := os.ReadFile("../../../../src/remedy/core/" + rel)
	if err != nil {
		t.Skip("python source absent: " + err.Error())
	}
	re := regexp.MustCompile(`(?s)` + name + ` = """(.*?)"""`)
	m := re.FindSubmatch(b)
	if m == nil {
		t.Fatalf("%s not found in %s", name, rel)
	}
	return strings.TrimSpace(string(m[1]))
}

func within2pct(t *testing.T, got, want string) {
	t.Helper()
	g, w := utf8.RuneCountInString(got), utf8.RuneCountInString(want)
	d := g - w
	if d < 0 {
		d = -d
	}
	if d*100 > w*2 {
		t.Fatalf("length %d not within 2%% of python %d", g, w)
	}
}

func TestComputerUsePlaybookText(t *testing.T) {
	p := computerUsePlaybook()
	if !strings.HasPrefix(p, "## Computer use (full PC + Browser rail) — FAST & ACCURATE\n") {
		t.Fatalf("heading: %.80q", p)
	}
	if strings.HasPrefix(p, "\n") || strings.HasSuffix(p, "\n") {
		t.Fatal("must be stripped")
	}
	within2pct(t, p, pySourceText(t, "computer/guidance.py", "COMPUTER_USE_SYSTEM_ADDENDUM"))
	if computerUseGuidance("take a screenshot") != p {
		t.Fatal("gated text differs from playbook")
	}
	t.Logf("playbook chars: %d", utf8.RuneCountInString(p))
}

func TestAwayText(t *testing.T) {
	a := awayGuidance("work alone please")
	if !strings.HasPrefix(a, "## Away mode (owner stepped out)\n") {
		t.Fatalf("heading: %.60q", a)
	}
	for _, s := range []string{
		"You are finishing this request without check-ins.",
		"Do not ask clarifying questions. Pick a reasonable default and continue.",
		"Escalate ONLY for: secrets, paid APIs, irreversible destroy, APPROVAL_REQUIRED.",
		"If you are blocked on approval, say what is waiting — once — then idle.",
		`they can say "I'm back" for a digest of what you already did.`,
	} {
		if !strings.Contains(a, s) {
			t.Errorf("missing %q", s)
		}
	}
	within2pct(t, a, pySourceText(t, "away_mode.py", "AWAY_ADDENDUM"))
}

func TestComputerUseDistinctive(t *testing.T) {
	p := computerUsePlaybook()
	for _, s := range []string{
		"You have the full capability of a person at the keyboard:",
		"The loop when something is unfamiliar or an action fails:",
		"A form is one job, not a pile of unfocused keystrokes:",
		"These pages lie to click-by-text. Do this, in order:",
		"Known retailers have direct search URLs",
	} {
		if !strings.Contains(p, s) {
			t.Errorf("missing %q", s)
		}
	}
}

func TestComputerUseGate(t *testing.T) {
	yes := []string{
		"take a screenshot of this", "open the browser", "what is on my screen",
		"what's on the screen right now", "play the game for me", "play it",
		"please fill out the form", "fill in form", "go to checkout", "add to cart",
		"see https://example.com", "goto page", "go to gmail", "go to www.x.org",
		"open the desktop app", "click the OK button", "type into the box",
		"type in the field", "use computer_click", "on screen", "computer.use now", "COMPUTER_USE", `["open the browser"]`,
	}
	no := []string{
		"", "implement a parser", "fix the bug in foo.go", "hello there",
		"computerized systems", "browsers are fine", "screenshots only", "play music",
		"fill the form", "checkouts", "gotofail", "go to bed", "type the answer",
		"click here", "desktop applications", "play the song",
	}
	for _, m := range yes {
		if computerUseGuidance(m) == "" {
			t.Errorf("want playbook for %q", m)
		}
	}
	for _, m := range no {
		if computerUseGuidance(m) != "" {
			t.Errorf("want none for %q", m)
		}
	}
}

func TestAwayGate(t *testing.T) {
	yes := []string{
		"work alone", "do it on your own", "handle this on your own", "I need to go now",
		"I'm going to be with my kids", "stepping away", "step away for a bit", "don't wait for me",
		"do not wait for me", "run it unattended", "fully autonomous please", "finish without me",
		"take it from here", "you got this", "I'm out", "im heading out", "i'm stepping out",
		"away mode on", "keep going without me", "do it while I'm gone",
	}
	no := []string{
		"", "build the thing", "I am out of coffee today", "outside", "workalone",
		"on your owner", "going away", "autonomy", "wait for me", "i'm outside",
		"unattended2 ok x", "you got thisx",
	}
	for _, m := range yes {
		if awayGuidance(m) == "" {
			t.Errorf("want away for %q", m)
		}
	}
	for _, m := range no {
		if awayGuidance(m) != "" {
			t.Errorf("want none for %q", m)
		}
	}
}
