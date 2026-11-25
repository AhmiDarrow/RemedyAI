package prompt

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// The recorded Python run on a local binding (plain-chat variant).
func TestSlimSystemRecordedFixture(t *testing.T) {
	raw, err := os.ReadFile("testdata/local_binding.json")
	if err != nil {
		t.Fatal(err)
	}
	var fx struct {
		Steps []struct {
			Input  map[string]any `json:"input"`
			Output struct {
				System string `json:"system"`
			} `json:"output"`
		} `json:"steps"`
	}
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	if len(fx.Steps) == 0 {
		t.Fatal("no steps")
	}
	for i, s := range fx.Steps {
		in := s.Input
		got := slimSystemForLocal("full system", "ctx", pyStr(in, "provider"), pyStr(in, "model"),
			maxReactSteps, pyStr(in, "message"), 0)
		if got != s.Output.System {
			t.Errorf("step %d:\n got %q\nwant %q", i, got, s.Output.System)
		}
	}
}

func TestSlimSystemWorkTurn(t *testing.T) {
	// Python: window 0 -> 8192 (the except fallback), head kept whole, parts
	// joined by "\n\n", context stripped, create nudge last.
	got := slimSystemForLocal("  I am Remedy.  ", " WORKSPACE ", "ollama", "m1", 256, "build a calculator", 0)
	want := "I am Remedy.\n\n" +
		"Provider: ollama · model: m1\n" +
		"Mode: local fixed-window agent · n_ctx=8192 · run until done (safety ceiling 256 steps).\n" +
		"Answer provider/model questions from this line — no tools needed.\n\n" +
		localContract + "\n\nWORKSPACE\n\n" + localCreateNudge
	if got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
	// No context, no implement intent: only head, runtime, contract.
	got = slimSystemForLocal("H", "", "p", "m", 5, "what time zone is London in?", 4096)
	if !strings.HasSuffix(got, localContract) || strings.Contains(got, "Local create") || !strings.Contains(got, "n_ctx=4096") {
		t.Errorf("unexpected %q", got)
	}
}

func TestSlimSystemCaps(t *testing.T) {
	long := strings.Repeat("x", 900) + "\n\n" + strings.Repeat("y", 50)
	// win 32768: headCap 1400; 952 <= 1600 so the head stays whole.
	got := slimSystemForLocal(long, "", "p", "m", 1, "fix it", 32768)
	if !strings.HasPrefix(got, long+"\n\nProvider:") {
		t.Error("head under cap+200 must stay whole")
	}
	// win 8192: headCap 700; 952 > 900 -> first paragraph [:700] + style line.
	got = slimSystemForLocal(long, "", "p", "m", 1, "fix it", 8192)
	if !strings.HasPrefix(got, strings.Repeat("x", 700)+"\nStyle: concise, tool-first. Prefer action over narration.\n\nProvider:") {
		t.Error("head not trimmed")
	}
	// ctx over cap: win 8192 -> ctxCap 6000, keep max(2000, 5500)=5500.
	ctx := strings.Repeat("é", 6001)
	got = slimSystemForLocal("H", ctx, "p", "m", 1, "fix it", 8192)
	if !strings.Contains(got, "\n\n"+strings.Repeat("é", 5500)+"\n…[context trimmed for local window]") {
		t.Error("context not trimmed to 5500 code points")
	}
	// 12288: cap 8000 keep 7500; 16384: 12000/11500; 24576: 18000/17500.
	for win, c := range map[int][2]int{12288: {8000, 7500}, 16384: {12000, 11500}, 24576: {18000, 17500}} {
		got = slimSystemForLocal("H", strings.Repeat("c", c[0]+1), "p", "m", 1, "fix it", win)
		if !strings.Contains(got, strings.Repeat("c", c[1])+"\n…[") || strings.Contains(got, strings.Repeat("c", c[1]+1)) {
			t.Errorf("win %d: wrong keep", win)
		}
	}
	if !strings.Contains(slimSystemForLocal("H", "", "p", "m", 1, "fix it", 1000), "n_ctx=8192") {
		t.Error("window <2048 must be 8192")
	}
}

func TestSlimSystemShortCircuits(t *testing.T) {
	// is_pure_trivia_message branch; empty model shows "local".
	if got := slimSystemForLocal("", "", "", "", 1, "2+2", 0); got !=
		"You are a local assistant on this PC (model: local).\nAnswer in one short sentence. No tools. No essays. No chain-of-thought." {
		t.Errorf("trivia: %q", got)
	}
	if got := slimSystemForLocal("", "", "", "m", 1, "how are you feeling", 0); !strings.Contains(got, "Answer in plain chat.") {
		t.Errorf("feeling: %q", got)
	}
	// Empty message is neither trivia nor chat: work variant.
	if got := slimSystemForLocal("H", "", "p", "m", 1, "", 0); !strings.Contains(got, localContract) {
		t.Error("empty message must be a work turn")
	}
}

func TestPureTrivia(t *testing.T) {
	for msg, want := range map[string]bool{
		"1 + 1":                       true,
		"what is 12*3 =?":             true,
		"What's 2.5 x 4":              true,
		"2 ÷ 2":                       true,
		"reply only STILLALIVE":       true,
		"turn 3: say just ok":         true,
		"please say ok":               false, // _REQUEST_WORK_RE matches please
		"say hello":                   true,
		"say hi to the file":          false, // tool hint: "file"
		"print the build log":         false,
		"reply with\nline":            false,
		"<invoke name=\"file_read\">": true,
		"call file_read(x)":           true,
		"| DSML | tool_calls":         true,
		"fix the bug":                 false,
		"":                            false,
		"hello":                       false,
		"1 + 1 is two":                false,
	} {
		if got := isPureTrivia(msg); got != want {
			t.Errorf("isPureTrivia(%q)=%v want %v", msg, got, want)
		}
	}
}

func TestMessageWantsImplement(t *testing.T) {
	long := strings.Repeat("z", 230)
	for msg, want := range map[string]bool{
		"build a calculator":    true,
		"keep going":            true,
		"Continue":              true,
		"resume the task":       true,
		"ship it":               true,
		"x":                     false,
		"":                      false,
		"tell me a joke":        false,
		"what is a codec":       false, // "code" needs a word boundary
		"please add a button":   true,
		"do it":                 true,
		long + " then fix":      true,
		"I will continue later": false, // _CONTINUE_RE is ^-anchored
	} {
		if got := messageWantsImplement(msg); got != want {
			t.Errorf("messageWantsImplement(%q)=%v want %v", msg, got, want)
		}
	}
}
